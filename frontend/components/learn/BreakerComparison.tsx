'use client';

import { useState } from 'react';
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';

// Komar & Gaughan (1972) breaker-height calculator. Two side-by-side
// swell setups let the reader compare how a tall short-period swell
// stacks up against a smaller long-period one at the breaking point.
//
//   Hb = 0.39 * g^(1/5) * (T * H0^2)^(2/5)
//
// H0 = deep-water (offshore) wave height, T = period in seconds,
// g = 9.81 m/s^2. Heights are exposed to the reader in feet.

const FT_TO_M = 0.3048;
const G = 9.81;

function breakerHeightFt(h0Ft: number, periodS: number): number {
  const h0m = h0Ft * FT_TO_M;
  const hbm =
    0.39 * Math.pow(G, 1 / 5) * Math.pow(periodS * h0m * h0m, 2 / 5);
  return hbm / FT_TO_M;
}

type SwellState = { height: number; period: number };

const DEFAULTS = {
  A: { height: 3, period: 16 } as SwellState,
  B: { height: 5, period: 8 } as SwellState,
};

export function BreakerComparison() {
  const [a, setA] = useState<SwellState>(DEFAULTS.A);
  const [b, setB] = useState<SwellState>(DEFAULTS.B);

  const hbA = breakerHeightFt(a.height, a.period);
  const hbB = breakerHeightFt(b.height, b.period);

  const data = [
    {
      label: `Swell A · ${a.height}ft @ ${a.period}s`,
      offshore: a.height,
      breaking: hbA,
    },
    {
      label: `Swell B · ${b.height}ft @ ${b.period}s`,
      offshore: b.height,
      breaking: hbB,
    },
  ];

  return (
    <div className="rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5 my-6">
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-5">
        <SwellPanel
          label="Swell A"
          color="#0369A1"
          swell={a}
          onChange={setA}
          breaking={hbA}
        />
        <SwellPanel
          label="Swell B"
          color="#15803D"
          swell={b}
          onChange={setB}
          breaking={hbB}
        />
      </div>

      <div className="h-56 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart
            data={data}
            margin={{ top: 10, right: 12, bottom: 0, left: 0 }}
          >
            <CartesianGrid strokeDasharray="2 4" stroke="#E2E8F0" vertical={false} />
            <XAxis
              dataKey="label"
              stroke="#94A3B8"
              tick={{ fill: '#475569', fontSize: 11 }}
              axisLine={false}
              tickLine={false}
            />
            <YAxis
              stroke="#94A3B8"
              tick={{ fill: '#475569', fontSize: 10 }}
              tickFormatter={(v) => `${v}ft`}
              width={42}
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
              formatter={(value: number, key) => [
                `${value.toFixed(1)} ft`,
                key === 'offshore' ? 'Offshore (deep water)' : 'At breaking',
              ]}
            />
            <Bar dataKey="offshore" radius={[4, 4, 0, 0]}>
              {data.map((_, i) => (
                <Cell key={i} fill={i === 0 ? '#7DD3FC' : '#86EFAC'} />
              ))}
            </Bar>
            <Bar dataKey="breaking" radius={[4, 4, 0, 0]}>
              {data.map((_, i) => (
                <Cell key={i} fill={i === 0 ? '#0369A1' : '#15803D'} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>

      <div className="mt-1 flex items-center justify-center gap-4 text-[11px] text-text-secondary">
        <LegendDot color="#7DD3FC" label="Offshore (deep water)" />
        <LegendDot color="#0369A1" label="Breaking height" />
      </div>
    </div>
  );
}

function SwellPanel({
  label,
  color,
  swell,
  onChange,
  breaking,
}: {
  label: string;
  color: string;
  swell: SwellState;
  onChange: (next: SwellState) => void;
  breaking: number;
}) {
  const amp = swell.height > 0 ? breaking / swell.height : 1;
  return (
    <div className="rounded-lg border border-ink-600 bg-ink-900/60 p-3.5">
      <div
        className="text-[11px] uppercase tracking-widest2 font-bold mb-3"
        style={{ color }}
      >
        {label}
      </div>

      <SliderRow
        label="Height (offshore)"
        value={swell.height}
        min={1}
        max={12}
        step={0.5}
        unit="ft"
        onChange={(v) => onChange({ ...swell, height: v })}
      />
      <SliderRow
        label="Period"
        value={swell.period}
        min={5}
        max={22}
        step={1}
        unit="s"
        onChange={(v) => onChange({ ...swell, period: v })}
      />

      <div className="mt-3 pt-3 border-t border-ink-600 flex items-baseline justify-between gap-3">
        <div>
          <div className="text-[10px] uppercase tracking-widest2 text-text-secondary">
            Breaks at
          </div>
          <div
            className="text-2xl font-bold tabular-nums"
            style={{ color }}
          >
            {breaking.toFixed(1)}ft
          </div>
        </div>
        <div className="text-right">
          <div className="text-[10px] uppercase tracking-widest2 text-text-secondary">
            Amplification
          </div>
          <div className="text-sm font-bold tabular-nums text-text-primary">
            ×{amp.toFixed(2)}
          </div>
        </div>
      </div>
    </div>
  );
}

function SliderRow({
  label,
  value,
  min,
  max,
  step,
  unit,
  onChange,
}: {
  label: string;
  value: number;
  min: number;
  max: number;
  step: number;
  unit: string;
  onChange: (next: number) => void;
}) {
  return (
    <div className="mb-2.5">
      <div className="flex items-center justify-between text-[11px] text-text-secondary mb-1">
        <span>{label}</span>
        <span className="font-mono tabular-nums text-text-primary font-bold">
          {Number.isInteger(value) ? value : value.toFixed(1)}
          {unit}
        </span>
      </div>
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        onChange={(e) => onChange(Number(e.target.value))}
        className="w-full accent-cyan-600"
        aria-label={label}
      />
    </div>
  );
}

function LegendDot({ color, label }: { color: string; label: string }) {
  return (
    <span className="inline-flex items-center gap-1.5">
      <span
        className="inline-block w-2.5 h-2.5 rounded-sm"
        style={{ background: color }}
      />
      {label}
    </span>
  );
}
