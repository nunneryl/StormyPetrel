import type { Spot } from '@/lib/types';
import { degToCardinal } from '@/lib/formatting';

// Static reference card. No comparison logic, no current-state hookup —
// surfers glance at this to learn what the spot wants, then judge the
// real-time data above for themselves.

function fmtCardinalDeg(deg: number | null | undefined): string {
  if (deg === null || deg === undefined) return '—';
  return `${degToCardinal(deg)} ${Math.round(deg)}°`;
}

export function OptimalConditions({ spot }: { spot: Spot }) {
  const rows: { label: string; value: string }[] = [
    {
      label: 'Swell',
      value:
        spot.optimal_swell_dir !== null
          ? fmtCardinalDeg(spot.optimal_swell_dir)
          : '—',
    },
    {
      label: 'Wind',
      value:
        spot.offshore_wind_deg !== null
          ? `${degToCardinal(spot.offshore_wind_deg)} offshore`
          : 'offshore',
    },
    { label: 'Tide',   value: spot.tide_preference ?? 'any' },
    { label: 'Period', value: '10s+' },
    { label: 'Break',  value: spot.break_type ?? '—' },
  ];

  return (
    <div className="rounded-xl border border-ink-600 bg-ink-800/60 p-4">
      <div className="text-[10px] uppercase tracking-widest2 text-text-secondary mb-3">
        Optimal conditions
      </div>
      <div className="space-y-2 text-sm">
        {rows.map((r) => (
          <div key={r.label} className="flex items-center justify-between gap-3">
            <span className="text-text-secondary">{r.label}</span>
            <span className="text-text-primary text-right truncate tabular-nums">
              {r.value}
            </span>
          </div>
        ))}
      </div>
    </div>
  );
}
