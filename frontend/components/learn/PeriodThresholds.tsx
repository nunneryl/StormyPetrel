'use client';

import { useState } from 'react';

// Four-bucket period reference. A small slider above lets the reader
// scrub a period value and watch which bucket lights up — turns the
// otherwise-static reference card into a learn-by-touch widget.

type Bucket = {
  min: number;
  max: number;
  label: string;
  title: string;
  description: string;
  bg: string;
  text: string;
  accent: string;
};

const BUCKETS: Bucket[] = [
  {
    min: 5, max: 8,
    label: '5–8s',
    title: 'Wind swell',
    description:
      'Local wind-generated waves. Short, disorganized, breaks close to its offshore height. Usually choppy and gutless.',
    bg: 'bg-red-50',
    text: 'text-red-900',
    accent: 'border-red-300',
  },
  {
    min: 9, max: 11,
    label: '9–11s',
    title: 'Mid-range swell',
    description:
      'Generated 500–1000 miles away. Starting to organize into lines. Decent surf if the direction is right and wind cooperates.',
    bg: 'bg-amber-50',
    text: 'text-amber-900',
    accent: 'border-amber-300',
  },
  {
    min: 12, max: 15,
    label: '12–15s',
    title: 'Groundswell',
    description:
      'Distant storm energy. Clean, organized lines with significant shoaling amplification. Most spots fire in this range.',
    bg: 'bg-emerald-50',
    text: 'text-emerald-900',
    accent: 'border-emerald-300',
  },
  {
    min: 16, max: 22,
    label: '16s+',
    title: 'Long-range groundswell',
    description:
      'Powerful energy from a major storm thousands of miles away. Major amplification on shallow reefs and points. These are the days you call in sick.',
    bg: 'bg-sky-50',
    text: 'text-sky-900',
    accent: 'border-sky-400',
  },
];

function activeIndex(period: number): number {
  for (let i = 0; i < BUCKETS.length; i += 1) {
    if (period >= BUCKETS[i].min && period <= BUCKETS[i].max) return i;
  }
  return period < BUCKETS[0].min ? 0 : BUCKETS.length - 1;
}

export function PeriodThresholds() {
  const [period, setPeriod] = useState(14);
  const active = activeIndex(period);

  return (
    <div className="rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5 my-6">
      <div className="flex items-center justify-between gap-3 mb-3">
        <div className="text-[11px] uppercase tracking-widest2 font-bold text-text-secondary">
          Drag to set period
        </div>
        <div className="font-mono tabular-nums text-text-primary font-bold">
          {period}s
        </div>
      </div>

      <input
        type="range"
        min={5}
        max={22}
        step={1}
        value={period}
        onChange={(e) => setPeriod(Number(e.target.value))}
        className="w-full accent-cyan-600"
        aria-label="Swell period in seconds"
      />
      <div className="mt-1 flex justify-between text-[10px] text-text-muted tabular-nums">
        <span>5s</span>
        <span>10s</span>
        <span>15s</span>
        <span>20s</span>
      </div>

      <div className="mt-4 space-y-2">
        {BUCKETS.map((b, i) => {
          const isActive = i === active;
          return (
            <div
              key={b.label}
              className={`rounded-lg border-2 transition p-3 sm:p-3.5 ${b.bg} ${
                isActive ? b.accent : 'border-transparent opacity-60'
              }`}
            >
              <div className={`flex items-baseline gap-2 ${b.text}`}>
                <span className="font-mono font-bold tabular-nums text-sm">
                  {b.label}
                </span>
                <span className="font-bold text-sm">{b.title}</span>
              </div>
              <p className={`mt-1 text-xs sm:text-sm leading-relaxed ${b.text}`}>
                {b.description}
              </p>
            </div>
          );
        })}
      </div>
    </div>
  );
}
