'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import { LEARN_SPOT_PRESETS, type LearnSpotCore } from '@/lib/learn-spots';

// Same six teaching presets and the same Leaflet/CartoDB chrome as
// SwellDirectionMap, but visualizing wind direction + speed against
// the spot's seaward bearing instead of a swell window. No window
// polygon — wind is a directional thing relative to one axis, not a
// "does it fit through this gap" question.

const LEAFLET_CSS_ID = 'leaflet-css';
const RADIUS_KM = 8;
const D2R = Math.PI / 180;

// --- geodesy -----------------------------------------------------------

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

function cardinal16(deg: number): string {
  const C = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
             'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  const n = ((deg % 360) + 360) % 360;
  return C[Math.round(n / 22.5) % 16];
}

// --- verdict math ------------------------------------------------------

type Tone = 'good' | 'workable' | 'marginal' | 'blown';

const TONE_STYLES: Record<Tone, { bg: string; fg: string; label: string }> = {
  good:     { bg: 'bg-emerald-100', fg: 'text-emerald-800', label: 'Good' },
  workable: { bg: 'bg-sky-100',     fg: 'text-sky-800',     label: 'Workable' },
  marginal: { bg: 'bg-amber-100',   fg: 'text-amber-800',   label: 'Marginal' },
  blown:    { bg: 'bg-red-100',     fg: 'text-red-800',     label: 'Blown out' },
};

function computeVerdict(orient: number, windDir: number, windSpeed: number) {
  // Wind direction is FROM-bearing; flip to the toward-bearing then
  // compare against the spot's seaward orientation (also a toward-
  // bearing). 0° = wind blowing straight offshore.
  const windBlowDir = (windDir + 180) % 360;
  let angle = windBlowDir - orient;
  while (angle > 180) angle -= 360;
  while (angle < -180) angle += 360;
  const absAngle = Math.abs(angle);
  const crossShore = Math.round(windSpeed * Math.cos((angle * Math.PI) / 180));
  const absCS = Math.abs(crossShore);

  let verdict: string;
  let quality: string;
  let tone: Tone = 'workable';

  if (absAngle < 30) {
    verdict = `Offshore at ${absCS} kt`;
    if (windSpeed < 5) {
      quality = 'Light offshore — glassy faces';
      tone = 'good';
    } else if (windSpeed < 15) {
      quality = 'Clean groomed conditions';
      tone = 'good';
    } else if (windSpeed < 22) {
      quality = 'Strong offshore — drops vertical, lip blows back';
      tone = 'workable';
    } else {
      quality = 'Too strong — paddling difficult, waves may not break';
      tone = 'marginal';
    }
  } else if (absAngle < 60) {
    verdict = `Offshore-cross at ${absCS} kt cross-shore`;
    quality = 'Mostly offshore with some side texture';
    tone = 'workable';
  } else if (absAngle < 120) {
    verdict = `Cross-shore at ${windSpeed} kt`;
    if (windSpeed < 8) {
      quality = 'Some texture, manageable';
      tone = 'workable';
    } else {
      quality = 'Choppy, hard to read';
      tone = 'marginal';
    }
  } else if (absAngle < 150) {
    verdict = `Onshore-cross at ${absCS} kt cross-shore`;
    quality = 'Mostly onshore with some side texture';
    tone = 'marginal';
  } else {
    verdict = `Onshore at ${absCS} kt`;
    if (windSpeed < 5) {
      quality = 'Light onshore — mushy but rideable';
      tone = 'workable';
    } else if (windSpeed < 12) {
      quality = 'Junky, closeouts on beach breaks';
      tone = 'marginal';
    } else {
      quality = 'Blown out at most spots';
      tone = 'blown';
    }
  }

  return { verdict, quality, tone };
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

const SPOT_FACES_COLOR = '#1B5E91';
const WIND_COLOR = '#D85A30';

export function WindDirectionMap() {
  const presets = LEARN_SPOT_PRESETS;
  const [preset, setPreset] = useState<LearnSpotCore>(presets[0]);
  const [windDir, setWindDir] = useState<number>(270);
  const [windSpeed, setWindSpeed] = useState<number>(10);

  const { verdict, quality, tone } = useMemo(
    () => computeVerdict(preset.orientationDeg, windDir, windSpeed),
    [preset.orientationDeg, windDir, windSpeed],
  );
  const toneStyle = TONE_STYLES[tone];

  // --- Leaflet plumbing -------------------------------------------------
  const containerRef = useRef<HTMLDivElement | null>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const mapRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const markerRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const orientationRef = useRef<any>(null);
  // eslint-disable-next-line @typescript-eslint/no-explicit-any
  const windRef = useRef<any>(null);

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

      // Spot-faces polyline (solid blue, no arrow head — the line
      // itself reads as "the way the spot points").
      orientationRef.current = L.polyline(
        [
          [preset.lat, preset.lng],
          destination(preset.lat, preset.lng, preset.orientationDeg, RADIUS_KM * 0.8),
        ],
        { color: SPOT_FACES_COLOR, weight: 3 },
      ).addTo(map);

      // Wind polyline — drawn FROM the offshore "wind origin" point
      // TOWARD the spot, so the visual reads as wind arriving at the
      // break. Solid orange, no dash.
      const windOrigin = destination(preset.lat, preset.lng, windDir, RADIUS_KM * 0.8);
      windRef.current = L.polyline(
        [windOrigin, [preset.lat, preset.lng]],
        { color: WIND_COLOR, weight: 3 },
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
      windRef.current = null;
    };
    // Init runs ONCE — subsequent preset/wind changes go through the
    // two effects below.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Recenter + redraw orientation line on preset change.
  useEffect(() => {
    if (!mapRef.current) return;
    mapRef.current.setView([preset.lat, preset.lng], 11, { animate: true });
    markerRef.current?.setLatLng([preset.lat, preset.lng]);
    orientationRef.current?.setLatLngs([
      [preset.lat, preset.lng],
      destination(preset.lat, preset.lng, preset.orientationDeg, RADIUS_KM * 0.8),
    ]);
  }, [preset]);

  // Redraw the wind polyline whenever the slider moves or the
  // preset changes (the line originates from a point offset against
  // the spot, so it has to follow the spot when the preset does).
  useEffect(() => {
    if (!windRef.current) return;
    const windOrigin = destination(preset.lat, preset.lng, windDir, RADIUS_KM * 0.8);
    windRef.current.setLatLngs([windOrigin, [preset.lat, preset.lng]]);
  }, [windDir, preset]);

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

          <div className="mt-3 grid grid-cols-1 sm:grid-cols-2 gap-3">
            <SliderRow
              label="Wind from"
              value={windDir}
              min={0}
              max={359}
              step={1}
              unit="°"
              extra={` (${cardinal16(windDir)})`}
              onChange={setWindDir}
              accent="orange"
            />
            <SliderRow
              label="Wind speed"
              value={windSpeed}
              min={0}
              max={30}
              step={1}
              unit=" kt"
              onChange={setWindSpeed}
              accent="cyan"
            />
          </div>

          <Legend />
        </div>

        <div className="space-y-3">
          <div className={`rounded-lg border border-ink-600 p-3 ${toneStyle.bg}`}>
            <div className="text-[10px] uppercase tracking-widest2 font-bold text-text-secondary">
              Verdict
            </div>
            <div className={`mt-1 text-2xl font-bold ${toneStyle.fg}`}>
              {toneStyle.label}
            </div>
            <div className={`mt-1 text-sm font-medium ${toneStyle.fg}`}>
              {verdict}
            </div>
          </div>

          <div className="rounded-lg border border-ink-600 bg-ink-900/60 p-3">
            <div className="text-[10px] uppercase tracking-widest2 text-text-secondary">
              What that looks like
            </div>
            <div className="mt-1 text-sm text-text-primary leading-snug">
              {quality}
            </div>
            <div className="mt-2 text-xs text-text-secondary tabular-nums">
              {preset.label} faces {preset.orientationDeg}° ({cardinal16(preset.orientationDeg)}).
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function SliderRow({
  label, value, min, max, step, unit, extra, onChange, accent,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  extra?: string;
  onChange: (v: number) => void;
  accent: 'cyan' | 'orange';
}) {
  return (
    <div>
      <div className="flex items-center justify-between text-[11px] text-text-secondary mb-1">
        <span>{label}</span>
        <span className="font-mono tabular-nums text-text-primary font-bold">
          {value}{unit}{extra ?? ''}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className={accent === 'orange' ? 'w-full accent-orange-500' : 'w-full accent-cyan-600'}
        aria-label={label}
      />
    </div>
  );
}

function Legend() {
  return (
    <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-text-secondary">
      <LegendDot color={SPOT_FACES_COLOR} label="Spot faces seaward" />
      <LegendDot color={WIND_COLOR} label="Wind direction" />
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block w-5 h-1.5 rounded-sm" style={{ background: color }} />
      {label}
    </span>
  );
}
