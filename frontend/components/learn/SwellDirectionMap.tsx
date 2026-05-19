'use client';

import { useEffect, useMemo, useRef, useState } from 'react';

// Interactive "what's the angle?" map. Pick a preset spot, then drag
// the swell-direction slider and watch the energy delivery + verdict
// update. Map = Leaflet (same dynamic-import pattern as the main
// /map page so this doesn't drag the bundle into SSR).

type Preset = {
  key: string;
  label: string;
  lat: number;
  lng: number;
  /** Compass bearing the spot faces — direction of open ocean. */
  orientationDeg: number;
  /** Swell-window arc (degrees the spot can see). Wraps when min > max. */
  windowMin: number;
  windowMax: number;
};

const PRESETS: Preset[] = [
  { key: 'pipeline',  label: 'Pipeline, HI',        lat: 21.6651, lng: -158.0539, orientationDeg: 340, windowMin: 280, windowMax: 20 },
  { key: 'huntington', label: 'Huntington Beach, CA', lat: 33.6553, lng: -117.9988, orientationDeg: 210, windowMin: 180, windowMax: 280 },
  { key: 'narragansett', label: 'Narragansett, RI',  lat: 41.4490, lng:  -71.4545, orientationDeg: 180, windowMin: 100, windowMax: 220 },
  { key: 'sebastian',  label: 'Sebastian Inlet, FL', lat: 27.8576, lng:  -80.4487, orientationDeg:  80, windowMin:  20, windowMax: 160 },
  { key: 'rincon',     label: 'Rincon, CA',          lat: 34.3731, lng: -119.4782, orientationDeg: 200, windowMin: 180, windowMax: 280 },
  { key: 'hatteras',   label: 'Cape Hatteras, NC',   lat: 35.2228, lng:  -75.5356, orientationDeg: 120, windowMin:  20, windowMax: 200 },
];

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

function wedgeRing(p: Preset): Array<[number, number]> {
  // Polygon ring traced from the spot, around the arc from windowMin
  // to windowMax going clockwise (handles wrap-around), and back.
  const span = (p.windowMax - p.windowMin + 360) % 360 || 360;
  const stepCount = Math.max(8, Math.round(span / 5));
  const pts: Array<[number, number]> = [[p.lat, p.lng]];
  for (let i = 0; i <= stepCount; i += 1) {
    const bearing = (p.windowMin + (span * i) / stepCount) % 360;
    pts.push(destination(p.lat, p.lng, bearing, RADIUS_KM));
  }
  pts.push([p.lat, p.lng]);
  return pts;
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

function isInsideWindow(swellDir: number, p: Preset): boolean {
  const dir = ((swellDir % 360) + 360) % 360;
  if (p.windowMin <= p.windowMax) {
    return dir >= p.windowMin && dir <= p.windowMax;
  }
  return dir >= p.windowMin || dir <= p.windowMax;
}

type Verdict = {
  label: string;
  bg: string;
  fg: string;
};

function verdictFor(off: number, inside: boolean): Verdict {
  if (!inside) return { label: 'Outside window', bg: 'bg-slate-200', fg: 'text-slate-800' };
  if (off >= 90) return { label: 'Parallel',      bg: 'bg-red-100',   fg: 'text-red-800' };
  if (off >= 70) return { label: 'Scraps',         bg: 'bg-red-100',   fg: 'text-red-800' };
  if (off >= 45) return { label: 'Reduced',        bg: 'bg-amber-100', fg: 'text-amber-800' };
  if (off >= 20) return { label: 'Good',           bg: 'bg-emerald-100', fg: 'text-emerald-800' };
  return            { label: 'Prime',           bg: 'bg-sky-100',   fg: 'text-sky-800' };
}

function cardinal16(deg: number): string {
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

export function SwellDirectionMap() {
  const [preset, setPreset] = useState<Preset>(PRESETS[0]);
  const [swellDir, setSwellDir] = useState<number>(preset.orientationDeg);

  // Reset swell direction to the spot's orientation whenever the user
  // picks a new preset, so the visualization starts in a "prime"
  // state rather than a stale angle that may now be off-window.
  useEffect(() => {
    setSwellDir(preset.orientationDeg);
  }, [preset]);

  const off = useMemo(() => offAxisDeg(swellDir, preset.orientationDeg), [swellDir, preset.orientationDeg]);
  const inside = useMemo(() => isInsideWindow(swellDir, preset), [swellDir, preset]);
  const energy = inside ? energyFromOffAxis(off) : 0;
  const verdict = verdictFor(off, inside);

  // --- Leaflet plumbing -------------------------------------------------
  const containerRef = useRef<HTMLDivElement | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mapRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const layersRef = useRef<{ marker: any; window: any; orientation: any; swell: any } | null>(null);

  useEffect(() => {
    if (!containerRef.current || mapRef.current) return;
    let cancelled = false;
    (async () => {
      const leafletNs = await import('leaflet');
      // eslint-disable-next-line @typescript-eslint/no-explicit-any
      const L: any = (leafletNs as any).default ?? leafletNs;
      ensureCss(LEAFLET_CSS_ID, 'https://unpkg.com/leaflet@1.9.4/dist/leaflet.css');
      if (cancelled || !containerRef.current) return;

      const map = L.map(containerRef.current, {
        center: [preset.lat, preset.lng],
        zoom: 11,
        zoomControl: false,
        attributionControl: false,
        scrollWheelZoom: false, // keeps article scroll usable
      });
      L.control.zoom({ position: 'bottomright' }).addTo(map);
      L.tileLayer('https://basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png', {
        maxZoom: 19,
        subdomains: 'abcd',
      }).addTo(map);
      mapRef.current = map;

      const marker = L.circleMarker([preset.lat, preset.lng], {
        radius: 7,
        color: '#0F172A',
        weight: 2,
        fillColor: '#FFFFFF',
        fillOpacity: 1,
      }).addTo(map);

      const wedge = L.polygon(wedgeRing(preset), {
        color: '#0369A1',
        weight: 1,
        fillColor: '#0369A1',
        fillOpacity: 0.15,
      }).addTo(map);

      const orientation = L.polyline(
        [[preset.lat, preset.lng], destination(preset.lat, preset.lng, preset.orientationDeg, RADIUS_KM * 0.8)],
        { color: '#0369A1', weight: 3 },
      ).addTo(map);

      const swell = L.polyline(
        [[preset.lat, preset.lng], destination(preset.lat, preset.lng, swellDir, RADIUS_KM * 0.8)],
        { color: '#F97316', weight: 3, dashArray: '6 4' },
      ).addTo(map);

      layersRef.current = { marker, window: wedge, orientation, swell };
    })();
    return () => {
      cancelled = true;
      if (mapRef.current) {
        mapRef.current.remove();
        mapRef.current = null;
      }
      layersRef.current = null;
    };
    // The init effect runs ONCE — preset/swellDir changes update layers
    // via the next two effects without rebuilding the whole map.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Update layers when the preset changes.
  useEffect(() => {
    if (!mapRef.current || !layersRef.current) return;
    const m = mapRef.current;
    const L = layersRef.current;
    m.setView([preset.lat, preset.lng], 11, { animate: true });
    L.marker.setLatLng([preset.lat, preset.lng]);
    L.window.setLatLngs(wedgeRing(preset));
    L.orientation.setLatLngs([
      [preset.lat, preset.lng],
      destination(preset.lat, preset.lng, preset.orientationDeg, RADIUS_KM * 0.8),
    ]);
  }, [preset]);

  // Update the swell-direction line whenever the slider moves.
  useEffect(() => {
    if (!layersRef.current) return;
    layersRef.current.swell.setLatLngs([
      [preset.lat, preset.lng],
      destination(preset.lat, preset.lng, swellDir, RADIUS_KM * 0.8),
    ]);
  }, [swellDir, preset]);

  // --- render -----------------------------------------------------------
  return (
    <div className="rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5 my-6">
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <span className="text-[11px] uppercase tracking-widest2 font-bold text-text-secondary mr-2">
          Spot
        </span>
        {PRESETS.map((p) => {
          const active = p.key === preset.key;
          return (
            <button
              key={p.key}
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
            sub={`Spot faces ${preset.orientationDeg}° (${cardinal16(preset.orientationDeg)})`}
          />
          <StatCard
            label="Energy delivered"
            value={`${(energy * 100).toFixed(0)}%`}
            sub={
              <EnergyBar value={energy} />
            }
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
  // Color the bar by the same buckets as the verdict for visual
  // continuity.
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
