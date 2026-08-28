import type { Spot } from '@/lib/types';
import { optimalConditionsRows } from '@/lib/spotInfo';

// Static reference card. No comparison logic, no current-state hookup —
// surfers glance at this to learn what the spot wants, then judge the
// real-time data above for themselves.
//
// Rows come from lib/spotInfo so WHICH ROWS EXIST, and how a null renders, are
// testable — the same seam the Spot info panel beside it uses, and the reason
// both cards can be held to one rendering of tide_preference. There is no
// Period row: it was a hardcoded '10s+' on all 648 spots and nothing in the
// schema can source a per-spot period. See optimalConditionsRows.

export function OptimalConditions({ spot }: { spot: Spot }) {
  return (
    <div className="rounded-xl border border-ink-600 bg-ink-800/60 p-4">
      <div className="text-[10px] uppercase tracking-widest2 text-text-secondary mb-3">
        Optimal conditions
      </div>
      <div className="space-y-2 text-sm">
        {optimalConditionsRows(spot).map((r) => (
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
