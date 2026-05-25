'use client';

import { useState } from 'react';

// Sample NDBC bulk-parameter table. Tapping a field swaps an
// explanation panel below it so readers can decode WVHT / DPD /
// APD / MWD in plain English without leaving the article.

type FieldKey = 'wvht' | 'dpd' | 'apd' | 'mwd';

const EXPLANATIONS: Record<FieldKey, { title: string; text: string }> = {
  wvht: {
    title: 'WVHT — Significant wave height',
    text: 'How big the waves are. It\'s the average height of the biggest third of waves passing under the buoy — roughly what someone on the buoy would describe as the wave size. The headline number for how much energy is in the water.',
  },
  dpd: {
    title: 'DPD — Dominant period',
    text: 'The number of seconds between the most energetic waves. The buoy picks out the loudest rhythm in the water. Long groundswell shows up as 12–20+ seconds. Choppy wind waves show up as 4–7 seconds.',
  },
  apd: {
    title: 'APD — Average period',
    text: 'Same idea as DPD but averaged across everything in the water — the dominant rhythm AND all the smaller rhythms. The gap between DPD and APD tells you whether the swell is organized (close together) or mixed up with wind chop (far apart).',
  },
  mwd: {
    title: 'MWD — Mean wave direction',
    text: 'The compass direction the dominant waves are coming from. 270° is straight west, 180° is south, 90° is east. 290° in this example is WNW — classic North Pacific groundswell pointing at the SoCal coast.',
  },
};

const FIELDS: { key: FieldKey; label: string; value: string }[] = [
  { key: 'wvht', label: 'WVHT', value: '8.2 ft' },
  { key: 'dpd', label: 'DPD', value: '14 s' },
  { key: 'apd', label: 'APD', value: '9 s' },
  { key: 'mwd', label: 'MWD', value: '290°' },
];

export function BuoyReportDecoder() {
  const [active, setActive] = useState<FieldKey | null>(null);
  const exp = active ? EXPLANATIONS[active] : null;

  return (
    <div className="my-6 rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5">
      <div className="font-mono text-xs text-text-muted px-1 pb-2">
        Station 46086 — San Clemente Basin — 2026-05-25 12:00 UTC
      </div>
      <div className="grid grid-cols-4 gap-2">
        {FIELDS.map((f) => (
          <button
            key={f.key}
            type="button"
            onClick={() => setActive(f.key)}
            className={`text-center py-3.5 px-3 rounded-md border transition-colors ${
              active === f.key
                ? 'bg-blue-50 border-blue-600 text-blue-700'
                : 'bg-slate-50 border-slate-200 hover:bg-white hover:border-slate-300'
            }`}
          >
            <div className="text-[11px] font-mono tracking-wider text-text-muted">
              {f.label}
            </div>
            <div className="font-mono text-lg sm:text-xl font-medium mt-1 text-text-primary">
              {f.value}
            </div>
          </button>
        ))}
      </div>
      <div className="mt-3 p-4 bg-slate-50 rounded-md min-h-[96px]">
        <div className="text-sm font-medium text-text-primary">
          {exp ? exp.title : 'Tap any field above'}
        </div>
        <div className="text-sm text-text-secondary mt-1.5 leading-relaxed">
          {exp
            ? exp.text
            : 'Each parameter on a buoy report tells you something different. Tap one to see what it means in plain English.'}
        </div>
      </div>
    </div>
  );
}
