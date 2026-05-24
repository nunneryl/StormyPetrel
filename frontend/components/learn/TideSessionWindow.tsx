'use client';

import { useState } from 'react';

// Idealized 24h semidiurnal tide curve (two highs, two lows). Preset
// buttons select a break type's preferred tide range; the curve
// segments inside the range highlight, and the total in-band time
// shows as the optimal session window.

const PRESETS: Record<string, [number, number]> = {
  reef: [2.75, 3.75],
  point: [1.0, 5.0],
  beach: [2.0, 4.0],
  shorebreak: [0, 1.0],
};

const PRESET_LABELS: Record<string, string> = {
  reef: 'Reef',
  point: 'Point',
  beach: 'Beach',
  shorebreak: 'Shorebreak',
};

export function TideSessionWindow() {
  const [preset, setPreset] = useState<keyof typeof PRESETS>('reef');
  const [mn, mx] = PRESETS[preset];

  const x0 = 80;
  const x1 = 640;
  const yTop = 42;
  const yBot = 220;
  const TIDE_MAX = 6;
  const HOURS_MAX = 24;

  const tx = (t: number) => x0 + ((x1 - x0) * t) / HOURS_MAX;
  const ty = (tide: number) => yBot - ((yBot - yTop) * tide) / TIDE_MAX;
  const tideAt = (t: number) => 3 - 3 * Math.cos((Math.PI * t) / 6);

  const buildPath = (start: number, end: number) => {
    let d = '';
    let first = true;
    for (let t = start; t < end; t += 0.1) {
      d += (first ? 'M' : 'L') + tx(t).toFixed(2) + ' ' + ty(tideAt(t)).toFixed(2) + ' ';
      first = false;
    }
    d += 'L' + tx(end).toFixed(2) + ' ' + ty(tideAt(end)).toFixed(2);
    return d;
  };

  const basePath = buildPath(0, HOURS_MAX);

  const a = Math.max(-1, Math.min(1, (3 - mx) / 3));
  const b = Math.max(-1, Math.min(1, (3 - mn) / 3));
  const segments: [number, number][] = [];
  if (a <= b) {
    const t1 = (6 / Math.PI) * Math.acos(b);
    const t2 = (6 / Math.PI) * Math.acos(a);
    if (t2 - t1 > 0.001) {
      segments.push([t1, t2]);
      segments.push([t1 + 12, t2 + 12]);
    }
    if (12 - t1 - (12 - t2) > 0.001) {
      segments.push([12 - t2, 12 - t1]);
      segments.push([24 - t2, 24 - t1]);
    }
  }
  const totalHours = segments.reduce((sum, [s, e]) => sum + (e - s), 0);

  const ticks: [number, string][] = [
    [80, '0'],
    [220, '6'],
    [360, '12'],
    [500, '18'],
    [640, '24'],
  ];

  return (
    <div className="my-6 rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5">
      <svg viewBox="0 0 680 280" className="block w-full h-auto" role="img">
        <title>Tide curve and optimal session window by break type</title>
        <desc>
          A 24-hour semidiurnal tide cycle with two highs and two lows.
          Selecting a break type highlights its preferred tide range and the
          in-band portions of the curve.
        </desc>
        <text x="76" y="32" fontSize="12" textAnchor="end" fill="#94A3B8">
          Tide (ft)
        </text>
        <line x1="80" y1="42" x2="80" y2="220" stroke="#E2E8F0" strokeWidth="0.5" />
        <text x="74" y="46" fontSize="12" textAnchor="end" fill="#94A3B8">
          6
        </text>
        <line x1="76" y1="42" x2="80" y2="42" stroke="#E2E8F0" strokeWidth="0.5" />
        <text x="74" y="135" fontSize="12" textAnchor="end" fill="#94A3B8">
          3
        </text>
        <line x1="76" y1="131" x2="80" y2="131" stroke="#E2E8F0" strokeWidth="0.5" />
        <text x="74" y="224" fontSize="12" textAnchor="end" fill="#94A3B8">
          0
        </text>
        <line x1="76" y1="220" x2="80" y2="220" stroke="#E2E8F0" strokeWidth="0.5" />

        <rect
          x="80"
          y={ty(mx)}
          width="560"
          height={ty(mn) - ty(mx)}
          fill="#1D9E75"
          fillOpacity="0.18"
        />
        <path d={basePath} stroke="#888780" strokeWidth="2" fill="none" opacity="0.45" />
        {segments.map(([s, e], i) => (
          <path
            key={i}
            d={buildPath(s, e)}
            stroke="#0F6E56"
            strokeWidth="3"
            fill="none"
            strokeLinecap="round"
          />
        ))}

        <line x1="80" y1="220" x2="640" y2="220" stroke="#CBD5E1" strokeWidth="0.5" />
        {ticks.map(([xp, label]) => (
          <g key={label}>
            <line x1={xp} y1="220" x2={xp} y2="225" stroke="#94A3B8" strokeWidth="0.5" />
            <text x={xp} y="244" fontSize="12" textAnchor="middle" fill="#94A3B8">
              {label}
            </text>
          </g>
        ))}
        <text x="360" y="266" fontSize="12" textAnchor="middle" fill="#94A3B8">
          Time (hours)
        </text>
      </svg>

      <div className="grid grid-cols-4 gap-2 mt-4">
        {(Object.keys(PRESETS) as Array<keyof typeof PRESETS>).map((p) => (
          <button
            key={p}
            onClick={() => setPreset(p)}
            className={`py-2 px-3 border rounded-md text-sm transition-colors ${
              preset === p
                ? 'bg-emerald-50 border-emerald-600 text-emerald-700 font-medium'
                : 'border-ink-600 text-text-secondary hover:bg-slate-50'
            }`}
          >
            {PRESET_LABELS[p]}
          </button>
        ))}
      </div>

      <div className="flex items-center gap-2 mt-4 text-xs text-text-muted">
        <span className="inline-block w-4 h-1.5 rounded-sm bg-emerald-700" />
        Best session time — tide in preferred range
      </div>

      <div className="bg-slate-50 rounded-lg px-4 py-3 mt-3 flex items-baseline justify-center gap-2">
        <span className="text-sm text-text-secondary">Optimal session window</span>
        <span className="text-2xl font-medium text-text-primary">
          {totalHours.toFixed(1)} hours
        </span>
      </div>
    </div>
  );
}
