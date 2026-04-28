import { CompassArrow } from './CompassArrow';
import { degToCardinal, fmtSec, metersToFeet } from '@/lib/formatting';
import type { Forecast } from '@/lib/types';

type Component = {
  label: string;
  hs: number | null;
  tp: number | null;
  dp: number | null;
};

function gather(f: Forecast): Component[] {
  const all: Component[] = [
    { label: 'P1', hs: f.swell_1_hs, tp: f.swell_1_tp, dp: f.swell_1_dp },
    { label: 'P2', hs: f.swell_2_hs, tp: f.swell_2_tp, dp: f.swell_2_dp },
    { label: 'P3', hs: f.swell_3_hs, tp: f.swell_3_tp, dp: f.swell_3_dp },
    { label: 'Wind sea', hs: f.wind_wave_hs, tp: f.wind_wave_tp, dp: f.wind_wave_dp },
  ];
  return all
    .filter((c) => c.hs !== null && c.hs !== undefined && c.hs > 0.05)
    .sort((a, b) => (b.hs ?? 0) - (a.hs ?? 0))
    .slice(0, 4);
}

export function SwellPartitions({ forecast }: { forecast: Forecast }) {
  const components = gather(forecast);
  if (components.length === 0) {
    return null;
  }
  return (
    <div className="rounded border border-ink-700 bg-ink-900 p-3">
      <div className="text-[10px] uppercase tracking-widest text-slate-400 mb-2">
        Swell components
      </div>
      <div className="space-y-1.5">
        {components.map((c) => {
          const ft = metersToFeet(c.hs);
          return (
            <div key={c.label} className="flex items-center gap-3 text-sm">
              <span className="w-16 text-xs text-slate-400">{c.label}</span>
              <span className="font-bold text-slate-100 tabular-nums w-12">
                {ft !== null ? `${ft.toFixed(1)}ft` : '—'}
              </span>
              <span className="text-slate-300 tabular-nums w-10">{fmtSec(c.tp)}</span>
              <CompassArrow deg={c.dp} size={14} color="#3da9d7" />
              <span className="text-xs text-slate-500 ml-auto tabular-nums">
                {c.dp !== null && c.dp !== undefined ? `${c.dp.toFixed(0)}°` : ''}
              </span>
            </div>
          );
        })}
      </div>
      {forecast.swell_source && (
        <div className="mt-2 text-[10px] uppercase tracking-widest text-slate-500">
          Source: {sourceLabel(forecast.swell_source)}
        </div>
      )}
    </div>
  );
}

function sourceLabel(source: string): string {
  switch (source) {
    case 'ww3': return 'WAVEWATCH III partitions';
    case 'nwps_swell': return 'NWPS swell channel';
    case 'buoy': return 'NDBC buoy';
    case 'nwps_total': return 'NWPS total (no swell decomposition)';
    default: return source;
  }
}
