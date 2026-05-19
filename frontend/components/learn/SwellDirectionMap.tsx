'use client';

import { useEffect, useMemo, useRef, useState } from 'react';

// Interactive "what's the angle?" map. The preset spots + their
// geometry come in as a prop from the server component on the
// article page, sourced from Supabase. The client component just
// renders Leaflet layers and the slider/stats math.

export type SwellPreset = {
  slug: string;
  label: string;
  lat: number;
  lng: number;
  /** Compass bearing the spot faces — direction of open ocean. */
  orientationDeg: number | null;
  /** Optimal swell-FROM bearing. */
  optimalSwellDir: number | null;
  /** Inclusive bearing arcs (degrees, 0..360). Multi-arc spots get
   *  multiple shaded polygons. Arcs wrap when arc.min > arc.max. */
  windowArcs: Array<{ min: number; max: number }>;
};

const LEAFLET_CSS_ID = 'leaflet-css';
const RADIUS_KM = 8;

// --- pure math ---------------------------------------------------------

const D2R = Math.PI / 180;

function destination(lat: number, lng: number, bearingDeg: number, distKm: number): [number, number] {
  const R = 6371;
  const d = distKm / R;
  const φ1 = lat * D2R;
  const λ1 = lng * D2R;
  const θ = bearingDeg * D2R;
  const φ2 = Math.asin(Math.sin(φ1) * Math.cos(d) + Math.cos(φ1) * Math.sin(d) * Math.cos(θ));
  const λ2 = λ1 + Math.atan2(
    Math.sin(θ) * Math.sin(d) * Math.cos(φ1),
    Math.cos(d) - Math.sin(φ1) * Math.sin(φ2),
  );
  return [φ2 / D2R, λ2 / D2R];
}

function arcRing(
  lat: number,
  lng: number,
  minDeg: number,
  maxDeg: number,
): Array<[number, number]> {
  // Polygon ring traced from the spot, around the arc from min to
  // max going clockwise (handles wrap), and back. Used per-arc so a
  // spot with two windows renders as two distinct polygons.
  const span = (maxDeg - minDeg + 360) % 360 || 360;
  const stepCount = Math.max(8, Math.round(span / 5));
  const pts: Array<[number, number]> = [[lat, lng]];
  for (let i = 0; i <= stepCount; i += 1) {
    const bearing = (minDeg + (span * i) / stepCount) % 360;
    pts.push(destination(lat, lng, bearing, RADIUS_KM));
  }
  pts.push([lat, lng]);
  return pts;
}

/**
 * Join arcs that are stored as two halves split at 0°/360°. The
 * enrichment pipeline splits a wrapping window into [{233,359},
 * {0,33}] so each entry stays within JSON math; without merging
 * them back here the map renders two polygons with a visible
 * straight-line seam at north. Detect the touching pair and emit a
 * single arc whose `min > max` — arcRing already handles that
 * direction reversal as one continuous clockwise sweep.
 */
function mergeWrappingArcs(
  arcs: SwellPreset['windowArcs'],
): SwellPreset['windowArcs'] {
  if (arcs.length < 2) return arcs;
  // Allow a 1° tolerance on the seam in case a future entry uses
  // 358/2 instead of 359/0.
  const endsAtNorth = arcs.find((a) => a.max >= 358);
  const startsAtNorth = arcs.find((a) => a.min <= 1 && a !== endsAtNorth);
  if (!endsAtNorth || !startsAtNorth) return arcs;
  const merged = { min: endsAtNorth.min, max: startsAtNorth.max };
  const others = arcs.filter((a) => a !== endsAtNorth && a !== startsAtNorth);
  return [merged, ...others];
}

function offAxisDeg(swellDir: number, orientationDeg: number): number {
  const diff = Math.abs(((swellDir - orientationDeg) % 360) + 360) % 360;
  return Math.min(diff, 360 - diff);
}

function energyFromOffAxis(off: number): number {
  if (off >= 90) return 0;
  const c = Math.cos(off * D2R);
  return c * c;
}

function isInsideAnyArc(
  swellDir: number,
  arcs: SwellPreset['windowArcs'],
): boolean {
  if (arcs.length === 0) return true; // unknown window — don't penalise
  const dir = ((swellDir % 360) + 360) % 360;
  // Merge wrapping halves first so a dir of 359 isn't incorrectly
  // counted as inside a [0,33] arc when it actually sits on the
  // continuous [233,33] window's 0° seam.
  return mergeWrappingArcs(arcs).some((arc) =>
    arc.min <= arc.max
      ? dir >= arc.min && dir <= arc.max
      : dir >= arc.min || dir <= arc.max,
  );
}

type Verdict = { label: string; bg: string; fg: string };

function verdictFor(off: number, inside: boolean): Verdict {
  if (!inside) return { label: 'Outside window', bg: 'bg-slate-200', fg: 'text-slate-800' };
  if (off >= 90) return { label: 'Parallel', bg: 'bg-red-100', fg: 'text-red-800' };
  if (off >= 70) return { label: 'Scraps', bg: 'bg-red-100', fg: 'text-red-800' };
  if (off >= 45) return { label: 'Reduced', bg: 'bg-amber-100', fg: 'text-amber-800' };
  if (off >= 20) return { label: 'Good', bg: 'bg-emerald-100', fg: 'text-emerald-800' };
  return { label: 'Prime', bg: 'bg-sky-100', fg: 'text-sky-800' };
}

function cardinal16(deg: number | null | undefined): string {
  if (deg === null || deg === undefined || Number.isNaN(deg)) return '—';
  const CARDINALS = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
                     'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  const n = ((deg % 360) + 360) % 360;
  return CARDINALS[Math.round(n / 22.5) % 16];
}

// --- component ---------------------------------------------------------

function ensureCss(id: string, href: string) {
  if (typeof document === 'undefined' || document.getElementById(id)) return;
  const link = document.createElement('link');
  link.id = id;
  link.rel = 'stylesheet';
  link.href = href;
  link.crossOrigin = '';
  document.head.appendChild(link);
}

function defaultSwellDir(p: SwellPreset): number {
  // optimal_swell_dir from the spots table is the primary signal —
  // it's the recorded best-case angle for the break. When it isn't
  // set, fall back to the midpoint of the spot's swell window so
  // the slider still lands somewhere realistic (inside the arc).
  // Last resort: spot orientation, then due north.
  if (p.optimalSwellDir !== null) return Math.round(p.optimalSwellDir);
  const merged = mergeWrappingArcs(p.windowArcs);
  if (merged.length > 0) {
    const arc = merged[0];
    const span = (arc.max - arc.min + 360) % 360 || 360;
    return Math.round((arc.min + span / 2) % 360);
  }
  if (p.orientationDeg !== null) return Math.round(p.orientationDeg);
  return 0;
}

export function SwellDirectionMap({ presets }: { presets: SwellPreset[] }) {
  // Guard against an empty payload (shouldn't happen — the server
  // wrapper looks up six known slugs — but the type leaves room).
  const [preset, setPreset] = useState<SwellPreset | null>(presets[0] ?? null);
  const [swellDir, setSwellDir] = useState<number>(
    preset ? defaultSwellDir(preset) : 0,
  );

  // When the user picks a different preset, snap the slider back to
  // the new spot's optimal direction so the visualization opens at
  // a "prime" angle instead of carrying over the previous one.
  useEffect(() => {
    if (preset) setSwellDir(defaultSwellDir(preset));
  }, [preset]);

  const orientation = preset?.orientationDeg ?? 0;
  const off = useMemo(
    () => (preset?.orientationDeg !== null && preset?.orientationDeg !== undefined
      ? offAxisDeg(swellDir, preset.orientationDeg)
      : 0),
    [swellDir, preset?.orientationDeg],
  );
  const inside = useMemo(
    () => (preset ? isInsideAnyArc(swellDir, preset.windowArcs) : false),
    [swellDir, preset],
  );
  const energy = inside ? energyFromOffAxis(off) : 0;
  const verdict = verdictFor(off, inside);

  // --- Leaflet plumbing -------------------------------------------------
  const containerRef = useRef<HTMLDivElement | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mapRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const markerRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const orientationRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const swellRef = useRef<any>(null);
  // Multiple arcs per spot → an array of polygon layers we rebuild
  // from scratch on preset change.
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const arcLayersRef = useRef<any[]>([]);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const LRef = useRef<any>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current || !preset) return;
    let cancelled = false;
    (async () => {
      const leafletNs = await import('leaflet');
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const L: any = (leafletNs as any).default ?? leafletNs;
      LRef.current = L;
      ensureCss(LEAFLET_CSS_ID, 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css');
      if (cancelled || !containerRef.current) return;

      const map = L.map(containerRef.current, {
        center: [preset.lat, preset.lng],
        zoom: 11,
        zoomControl: false,
        attributionControl: false,
        scrollWheelZoom: false,
      });
      L.control.zoom({ position: 'bottomright' }).addTo(map);
      L.tileLayer('https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 19,
        subdomains: 'abcd',
      }).addTo(map);
      mapRef.current = map;

      markerRef.current = L.circleMarker([preset.lat, preset.lng], {
        radius: 7,
        color: '#0F172A',
        weight: 2,
        fillColor: '#FFFFFF',
        fillOpacity: 1,
      }).addTo(map);

      arcLayersRef.current = mergeWrappingArcs(preset.windowArcs).map((arc) =>
        L.polygon(arcRing(preset.lat, preset.lng, arc.min, arc.max), {
          color: '#0369A1',
          weight: 1,
          fillColor: '#0369A1',
          fillOpacity: 0.15,
        }).addTo(map),
      );

      orientationRef.current = L.polyline(
        preset.orientationDeg !== null
          ? [
              [preset.lat, preset.lng],
              destination(preset.lat, preset.lng, preset.orientationDeg, RADIUS_KM * 0.8),
            ]
          : [[preset.lat, preset.lng], [preset.lat, preset.lng]],
        { color: '#0369A1', weight: 3 },
      ).addTo(map);

      swellRef.current = L.polyline(
        [
          [preset.lat, preset.lng],
          destination(preset.lat, preset.lng, swellDir, RADIUS_KM * 0.8),
        ],
        { color: '#F97316', weight: 3, dashArray: '6 4' },
      ).addTo(map);
    })();
    return () => {
      cancelled = true;
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
      markerRef.current = null;
      orientationRef.current = null;
      swellRef.current = null;
      arcLayersRef.current = [];
    };
    // Init runs ONCE — subsequent preset / slider changes go through
    // the two effects below and just touch the existing layers.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Rebind layers when the preset changes.
  useEffect(() => {
    if (!mapRef.current || !preset || !LRef.current) return;
    const map = mapRef.current;
    const L = LRef.current;
    map.setView([preset.lat, preset.lng], 11, { animate: true });
    markerRef.current?.setLatLng([preset.lat, preset.lng]);

    // Tear down old arcs and rebuild from the new preset's merged
    // arc list (count can differ — Pipeline collapses two halves
    // into one, Hatteras stays as one, etc.).
    for (const layer of arcLayersRef.current) layer.remove();
    arcLayersRef.current = mergeWrappingArcs(preset.windowArcs).map((arc) =>
      L.polygon(arcRing(preset.lat, preset.lng, arc.min, arc.max), {
        color: '#0369A1',
        weight: 1,
        fillColor: '#0369A1',
        fillOpacity: 0.15,
      }).addTo(map),
    );

    if (preset.orientationDeg !== null) {
      orientationRef.current?.setLatLngs([
        [preset.lat, preset.lng],
        destination(preset.lat, preset.lng, preset.orientationDeg, RADIUS_KM * 0.8),
      ]);
    } else {
      orientationRef.current?.setLatLngs([[preset.lat, preset.lng], [preset.lat, preset.lng]]);
    }
  }, [preset]);

  // Slider movement updates only the swell-direction polyline.
  useEffect(() => {
    if (!swellRef.current || !preset) return;
    swellRef.current.setLatLngs([
      [preset.lat, preset.lng],
      destination(preset.lat, preset.lng, swellDir, RADIUS_KM * 0.8),
    ]);
  }, [swellDir, preset]);

  if (!preset) {
    return (
      <div className="rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5 my-6 text-sm text-text-muted">
        Preset data unavailable.
      </div>
    );
  }

  // --- render -----------------------------------------------------------
  return (
    <div className="rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5 my-6">
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <span className="text-[11px] uppercase tracking-widest2 font-bold text-text-secondary mr-2">
          Spot
        </span>
        {presets.map((p) => {
          const active = p.slug === preset.slug;
          return (
            <button
              key={p.slug}
              type="button"
              onClick={() => setPreset(p)}
              className={`px-2.5 py-1 rounded-full text-[11px] font-bold transition ${
                active
                  ? 'bg-cyan-500 text-white'
                  : 'bg-ink-800 text-text-secondary hover:text-text-primary hover:bg-ink-700'
              }`}
            >
              {p.label}
            </button>
          );
        })}
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-[3fr_2fr] gap-4">
        <div>
          <div
            ref={containerRef}
            className="w-full rounded-lg border border-ink-600 overflow-hidden"
            style={{ height: 360 }}
          />
          <div className="mt-3">
            <div className="flex items-center justify-between text-[11px] text-text-secondary mb-1">
              <span>Incoming swell direction (from)</span>
              <span className="font-mono tabular-nums text-text-primary font-bold">
                {swellDir}° {cardinal16(swellDir)}
              </span>
            </div>
            <input
              type="range"
              min={0}
              max={359}
              step={1}
              value={swellDir}
              onChange={(e) => setSwellDir(Number(e.target.value))}
              className="w-full accent-orange-500"
              aria-label="Swell direction in degrees"
            />
            <div className="mt-1 flex justify-between text-[10px] text-text-muted tabular-nums">
              <span>N · 0°</span>
              <span>E · 90°</span>
              <span>S · 180°</span>
              <span>W · 270°</span>
              <span>N · 360°</span>
            </div>
          </div>
          <Legend />
        </div>

        <div className="space-y-3">
          <StatCard
            label="Angle off-axis"
            value={`${off.toFixed(0)}°`}
            sub={
              preset.orientationDeg !== null
                ? `Spot faces ${Math.round(orientation)}° (${cardinal16(orientation)})`
                : 'Spot orientation not recorded'
            }
          />
          <StatCard
            label="Energy delivered"
            value={`${(energy * 100).toFixed(0)}%`}
            sub={<EnergyBar value={energy} />}
          />
          <div className={`rounded-lg border border-ink-600 p-3 ${verdict.bg}`}>
            <div className="text-[10px] uppercase tracking-widest2 font-bold text-text-secondary">
              Verdict
            </div>
            <div className={`mt-1 text-2xl font-bold ${verdict.fg}`}>
              {verdict.label}
            </div>
            <div className="text-xs text-text-secondary mt-1">
              {inside ? 'Inside the spot’s swell window.' : 'Outside the spot’s swell window — blocked by land.'}
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function StatCard({
  label, value, sub,
}: { label: string; value: string; sub?: React.ReactNode }) {
  return (
    <div className="rounded-lg border border-ink-600 bg-ink-900/60 p-3">
      <div className="text-[10px] uppercase tracking-widest2 text-text-secondary">{label}</div>
      <div className="mt-1 text-2xl font-bold tabular-nums text-text-primary">{value}</div>
      {sub && <div className="mt-1 text-xs text-text-secondary">{sub}</div>}
    </div>
  );
}

function EnergyBar({ value }: { value: number }) {
  const pct = Math.max(0, Math.min(1, value)) * 100;
  const color =
    value >= 0.85 ? '#0EA5E9' :
    value >= 0.5  ? '#22C55E' :
    value >= 0.12 ? '#EAB308' :
                    '#EF4444';
  return (
    <div className="h-2 mt-1 rounded-full bg-ink-700 overflow-hidden">
      <div
        className="h-full rounded-full transition-all"
        style={{ width: `${pct}%`, background: color }}
      />
    </div>
  );
}

function Legend() {
  return (
    <div className="mt-3 grid grid-cols-2 sm:grid-cols-3 gap-x-3 gap-y-1 text-[11px] text-text-secondary">
      <LegendDot color="#0369A1" label="Spot faces" />
      <LegendDot color="#0369A1" label="Swell window" muted />
      <LegendDot color="#F97316" label="Incoming swell" dashed />
    </div>
  );
}

function LegendDot({ color, label, muted, dashed }: { color: string; label: string; muted?: boolean; dashed?: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="inline-block w-5 h-1.5 rounded-sm"
        style={{
          background: muted ? `${color}33` : color,
          borderTop: dashed ? `2px dashed ${color}` : undefined,
          backgroundClip: dashed ? 'border-box' : undefined,
        }}
      />
      {label}
    </span>
  );
}
