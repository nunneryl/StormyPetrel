'use client';

import { useMemo, useState } from 'react';
import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

// Stylised buoy energy spectrum — toggle between a clean groundswell
// (single narrow peak) and a mixed sea (groundswell peak + a wind-sea
// shoulder around 7s). DPD / APD / verdict values are tuned to the
// shapes generated below so the chart and the metric cards agree.

type Mode = 'clean' | 'messy';

function gaussian(x: number, mean: number, std: number): number {
  return Math.exp(-((x - mean) ** 2) / (2 * std * std));
}

function buildSpectrum(mode: Mode): Array<{ T: number; E: number }> {
  const pts: Array<{ T: number; E: number }> = [];
  for (let t = 4; t <= 22; t += 0.25) {
    let e: number;
    if (mode === 'clean') {
      e = gaussian(t, 14, 1.4);
    } else {
      // Bigger ground-swell peak at 14s but a wide, heavy wind-sea
      // shoulder around 7s that drags the energy-weighted APD down.
      e = 0.7 * gaussian(t, 14, 1.5) + 1.0 * gaussian(t, 7, 1.6);
    }
    pts.push({ T: t, E: Number(e.toFixed(4)) });
  }
  return pts;
}

function peakPeriod(spectrum: Array<{ T: number; E: number }>): number {
  return spectrum.reduce((best, p) => (p.E > best.E ? p : best), spectrum[0]).T;
}

function averagePeriod(spectrum: Array<{ T: number; E: number }>): number {
  const sumE = spectrum.reduce((s, p) => s + p.E, 0);
  const sumTE = spectrum.reduce((s, p) => s + p.T * p.E, 0);
  return sumE === 0 ? 0 : sumTE / sumE;
}

const COPY: Record<Mode, { verdict: string; explainer: string }> = {
  clean: {
    verdict: 'Clean, organized',
    explainer:
      'Almost all the energy sits in one narrow band around 14 seconds. DPD and APD are close together, so the dominant swell is doing essentially all the work. Expect well-spaced sets and clean faces.',
  },
  messy: {
    verdict: 'Bumpy, mixed',
    explainer:
      'A 14-second groundswell is still the loudest peak, but a fat shoulder of wind-sea energy around 7 seconds is pulling the energy-weighted APD way down. The sets will be there but the faces will be choppy.',
  },
};

export function SpectrumViewer() {
  const [mode, setMode] = useState<Mode>('clean');
  const spectrum = useMemo(() => buildSpectrum(mode), [mode]);
  const dpd = useMemo(() => peakPeriod(spectrum), [spectrum]);
  const apd = useMemo(() => averagePeriod(spectrum), [spectrum]);
  const { verdict, explainer } = COPY[mode];

  return (
    <div className="rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5 my-6">
      <div className="flex flex-wrap items-center justify-between gap-3 mb-4">
        <div className="text-[11px] uppercase tracking-widest2 font-bold text-text-secondary">
          Toggle conditions
        </div>
        <div className="inline-flex rounded-full border border-ink-600 p-0.5 text-[11px] font-bold">
          <ToggleBtn active={mode === 'clean'} onClick={() => setMode('clean')}>
            Clean groundswell
          </ToggleBtn>
          <ToggleBtn active={mode === 'messy'} onClick={() => setMode('messy')}>
            Mixed / messy
          </ToggleBtn>
        </div>
      </div>

      <div className="grid grid-cols-1 sm:grid-cols-3 gap-2 sm:gap-3 mb-4">
        <MetricCard label="DPD" value={`${dpd.toFixed(0)}s`} />
        <MetricCard label="APD" value={`${apd.toFixed(0)}s`} />
        <MetricCard label="Verdict" value={verdict} variant="text" />
      </div>

      <div className="h-56 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart
            data={spectrum}
            margin={{ top: 8, right: 12, bottom: 0, left: 0 }}
          >
            <defs>
              <linearGradient id="spectrum-fill" x1="0" y1="0" x2="0" y2="1">
                <stop offset="0%" stopColor="#0369A1" stopOpacity={0.55} />
                <stop offset="100%" stopColor="#0369A1" stopOpacity={0.04} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="2 4" stroke="#E2E8F0" vertical={false} />
            <XAxis
              dataKey="T"
              type="number"
              domain={[4, 22]}
              ticks={[5, 8, 11, 14, 17, 20]}
              tickFormatter={(v) => `${v}s`}
              stroke="#94A3B8"
              tick={{ fill: '#475569', fontSize: 10 }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              stroke="#94A3B8"
              tick={{ fill: '#475569', fontSize: 10 }}
              tickFormatter={() => ''}
              width={28}
              axisLine={false}
              tickLine={false}
            />
            <Tooltip
              contentStyle={{
                background: '#FFFFFF',
                border: '1px solid #E2E8F0',
                borderRadius: 8,
                fontSize: 12,
                color: '#0F172A',
                boxShadow: '0 8px 24px -8px rgba(15,23,42,0.18)',
              }}
              labelFormatter={(v) => `${(v as number).toFixed(1)}s period`}
              formatter={(v: number) => [v.toFixed(2), 'Energy density']}
            />
            <Area
              type="monotone"
              dataKey="E"
              stroke="#0369A1"
              strokeWidth={2}
              fill="url(#spectrum-fill)"
              isAnimationActive={false}
            />
            <ReferenceLine
              x={dpd}
              stroke="#0F172A"
              strokeDasharray="3 3"
              label={{ value: 'DPD', fill: '#0F172A', fontSize: 10, position: 'top' }}
            />
          </AreaChart>
        </ResponsiveContainer>
      </div>

      <p className="mt-3 text-sm text-text-secondary leading-relaxed">
        {explainer}
      </p>
    </div>
  );
}

function MetricCard({
  label,
  value,
  variant = 'number',
}: {
  label: string;
  value: string;
  /** 'number' = big tabular metric; 'text' = wraps for prose values
   *  like "Bumpy, mixed" so the verdict card doesn't truncate. */
  variant?: 'number' | 'text';
}) {
  return (
    <div className="rounded-lg border border-ink-600 bg-ink-900/60 p-3 min-w-0">
      <div className="text-[10px] uppercase tracking-widest2 text-text-secondary">
        {label}
      </div>
      <div
        className={
          variant === 'text'
            ? 'mt-1 text-sm font-bold text-text-primary leading-snug break-words'
            : 'mt-1 text-2xl font-bold tabular-nums text-text-primary'
        }
      >
        {value}
      </div>
    </div>
  );
}

function ToggleBtn({
  active,
  onClick,
  children,
}: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`rounded-full px-3 py-1 transition ${
        active
          ? 'bg-cyan-500 text-white'
          : 'text-text-secondary hover:text-text-primary'
      }`}
    >
      {children}
    </button>
  );
}
