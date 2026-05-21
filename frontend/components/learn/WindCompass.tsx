'use client';

import { useMemo, useState } from 'react';

// Same six preset spots SwellDirectionMap uses on /learn/swell-
// direction. Orientation values are the seaward-facing bearings
// curated in that article — kept in sync here by hand because this
// component doesn't pull the full Leaflet preset shape.
const PRESETS = [
  { slug: 'banzai-pipeline',           label: 'Pipeline, HI',         orientationDeg: 315 },
  { slug: 'huntington-beach-pier',     label: 'Huntington Beach, CA', orientationDeg: 220 },
  { slug: 'narragansett-beach',        label: 'Narragansett, RI',     orientationDeg: 170 },
  { slug: 'sebastian-inlet',           label: 'Sebastian Inlet, FL',  orientationDeg:  80 },
  { slug: 'rincon',                    label: 'Rincon, CA',           orientationDeg: 210 },
  { slug: 'cape-hatteras-lighthouse',  label: 'Cape Hatteras, NC',    orientationDeg: 110 },
] as const;

type Tone = 'good' | 'workable' | 'marginal' | 'blown';

const TONE_STYLES: Record<Tone, { bg: string; fg: string; label: string }> = {
  good:     { bg: 'bg-emerald-100', fg: 'text-emerald-800', label: 'Good' },
  workable: { bg: 'bg-sky-100',     fg: 'text-sky-800',     label: 'Workable' },
  marginal: { bg: 'bg-amber-100',   fg: 'text-amber-800',   label: 'Marginal' },
  blown:    { bg: 'bg-red-100',     fg: 'text-red-800',     label: 'Blown out' },
};

function bearingToXY(bearing: number, radius: number, cx = 130, cy = 130) {
  const rad = (bearing * Math.PI) / 180;
  return { x: cx + radius * Math.sin(rad), y: cy - radius * Math.cos(rad) };
}

function cardinal16(deg: number): string {
  const C = ['N', 'NNE', 'NE', 'ENE', 'E', 'ESE', 'SE', 'SSE',
             'S', 'SSW', 'SW', 'WSW', 'W', 'WNW', 'NW', 'NNW'];
  const n = ((deg % 360) + 360) % 360;
  return C[Math.round(n / 22.5) % 16];
}

function computeVerdict(orient: number, windDir: number, windSpeed: number) {
  // wind direction is FROM bearing → flip to "blow toward" bearing.
  const windBlowDir = (windDir + 180) % 360;
  let angle = windBlowDir - orient;
  while (angle > 180) angle -= 360;
  while (angle < -180) angle += 360;
  const absAngle = Math.abs(angle);
  const crossShore = Math.round(windSpeed * Math.cos((angle * Math.PI) / 180));
  const absCS = Math.abs(crossShore);

  let verdict: string;
  let quality: string;
  let tone: Tone;

  if (absAngle < 30) {
    verdict = `Offshore at ${absCS} kt`;
    if (windSpeed < 5) {
      quality = 'Light offshore — glassy faces.';
      tone = 'workable';
    } else if (windSpeed < 15) {
      quality = 'Clean groomed conditions.';
      tone = 'good';
    } else if (windSpeed < 22) {
      quality = 'Strong offshore — drops go vertical, lip blows back.';
      tone = 'workable';
    } else {
      quality = 'Too strong — paddling difficult, waves may not break.';
      tone = 'marginal';
    }
  } else if (absAngle < 60) {
    verdict = `Offshore-cross at ${absCS} kt cross-shore`;
    quality = 'Mostly offshore with some side texture.';
    tone = 'workable';
  } else if (absAngle < 120) {
    verdict = `Cross-shore at ${windSpeed} kt`;
    if (windSpeed < 8) {
      quality = 'Some texture, manageable.';
      tone = 'workable';
    } else {
      quality = 'Choppy, hard to read.';
      tone = 'marginal';
    }
  } else if (absAngle < 150) {
    verdict = `Onshore-cross at ${absCS} kt cross-shore`;
    quality = 'Mostly onshore with some side texture.';
    tone = 'marginal';
  } else {
    verdict = `Onshore at ${absCS} kt`;
    if (windSpeed < 5) {
      quality = 'Light onshore — mushy but rideable.';
      tone = 'workable';
    } else if (windSpeed < 12) {
      quality = 'Junky, closeouts on beach breaks.';
      tone = 'marginal';
    } else {
      quality = 'Blown out at most spots.';
      tone = 'blown';
    }
  }

  return { verdict, quality, tone, absAngle, absCS };
}

export function WindCompass() {
  const [presetIdx, setPresetIdx] = useState(0);
  const [windDir, setWindDir] = useState(270);
  const [windSpeed, setWindSpeed] = useState(10);

  const preset = PRESETS[presetIdx];
  const orient = preset.orientationDeg;

  const { verdict, quality, tone } = useMemo(
    () => computeVerdict(orient, windDir, windSpeed),
    [orient, windDir, windSpeed],
  );

  // Compass geometry — orientation arrow (where the spot faces) and
  // wind arrow (where the wind comes FROM, so the head points into
  // the center).
  const nEnd = bearingToXY(orient, 95);
  const s1 = bearingToXY(orient + 90, 95);
  const s2 = bearingToXY(orient - 90, 95);
  const wStart = bearingToXY(windDir, 95);
  const wEnd = bearingToXY(windDir, 24);

  const toneStyle = TONE_STYLES[tone];

  return (
    <div className="my-6 rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5">
      {/* Spot picker — same pill style as SwellDirectionMap. */}
      <div className="flex flex-wrap items-center gap-2 mb-4">
        <span className="text-[11px] uppercase tracking-widest2 font-bold text-text-secondary mr-2">
          Spot
        </span>
        {PRESETS.map((p, i) => {
          const active = i === presetIdx;
          return (
            <button
              key={p.slug}
              type="button"
              onClick={() => setPresetIdx(i)}
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

      <div className="grid grid-cols-1 md:grid-cols-[260px_minmax(0,1fr)] gap-4 items-start">
        <div>
          <svg
            viewBox="0 0 260 260"
            className="block w-full h-auto"
            role="img"
            aria-label={`Compass showing the wind at ${windDir}° and ${preset.label} facing ${orient}°`}
          >
            <defs>
              <marker
                id="wc-arrow"
                viewBox="0 0 10 10"
                refX="8"
                refY="5"
                markerWidth="7"
                markerHeight="7"
                orient="auto-start-reverse"
              >
                <path
                  d="M2 1L8 5L2 9"
                  fill="none"
                  stroke="context-stroke"
                  strokeWidth="1.5"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </marker>
            </defs>
            <circle cx="130" cy="130" r="100" fill="none" stroke="#CBD5E1" strokeWidth="0.5" />
            <text x="130" y="20" textAnchor="middle" fontSize="12" fill="#94A3B8">N</text>
            <text x="244" y="135" textAnchor="middle" fontSize="12" fill="#94A3B8">E</text>
            <text x="130" y="250" textAnchor="middle" fontSize="12" fill="#94A3B8">S</text>
            <text x="16" y="135" textAnchor="middle" fontSize="12" fill="#94A3B8">W</text>

            {/* Shoreline — perpendicular to the orientation arrow */}
            <line
              x1={s1.x}
              y1={s1.y}
              x2={s2.x}
              y2={s2.y}
              stroke="#94A3B8"
              strokeWidth="2"
              opacity="0.3"
            />
            {/* Orientation arrow (seaward) — green/teal */}
            <line
              x1="130"
              y1="130"
              x2={nEnd.x}
              y2={nEnd.y}
              stroke="#1D9E75"
              strokeWidth="2.5"
              markerEnd="url(#wc-arrow)"
            />
            {/* Wind arrow — orange, points INTO center because wind
                is described by its from-bearing */}
            <line
              x1={wStart.x}
              y1={wStart.y}
              x2={wEnd.x}
              y2={wEnd.y}
              stroke="#D85A30"
              strokeWidth="2.5"
              markerEnd="url(#wc-arrow)"
            />
            <circle cx="130" cy="130" r="4" fill="#0F172A" />
          </svg>

          <div className="mt-2 flex flex-wrap items-center gap-x-4 gap-y-1 text-[11px] text-text-secondary">
            <LegendDot color="#1D9E75" label="Spot faces seaward" />
            <LegendDot color="#D85A30" label="Wind direction" />
          </div>
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
              {preset.label} faces {orient}° ({cardinal16(orient)}).
            </div>
          </div>
        </div>
      </div>

      <div className="mt-4 grid grid-cols-1 sm:grid-cols-2 gap-4">
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

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span className="inline-block w-5 h-1.5 rounded-sm" style={{ background: color }} />
      {label}
    </span>
  );
}
