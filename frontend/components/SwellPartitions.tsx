import { SwellCompass } from './SwellCompass';
import { degToCardinal, fmtSec, metersToFeet } from '@/lib/formatting';
import { breakdownRows, sharePercent } from '@/lib/swellBreakdown';
import type { Forecast, Spot } from '@/lib/types';

const SOURCE_LABEL: Record<string, string> = {
  ww3:                 'WAVEWATCH III',
  // NWPS nearshore height + WW3 partition direction/period. A hybrid of two feeds, so it
  // gets its own value rather than reusing 'nwps' — the plain value now means the override
  // found no WW3 partition for that hour and fell back to whole-spectrum DIRPW.
  nwps_height_ww3_dir: 'NWPS height + WW3',
  nwps:                'NWPS (DIRPW)',
  nwps_swell:          'NWPS swell',
  buoy:                'NDBC buoy',
  nwps_total:          'NWPS total',
};

export function SwellPartitions({
  forecast,
  windowArcs,
}: {
  forecast: Forecast;
  windowArcs: Spot['swell_window_arcs'];
}) {
  // Every row's share of the total wave height, the measure the Conditions tile's "% wind sea"
  // uses (lib/swellBreakdown.ts).
  const all = breakdownRows(forecast, windowArcs);
  if (all.length === 0) return null;

  // If we only have 1 swell partition AND no wind sea worth showing,
  // skip the whole card — the hero "Swell" tile already covers it.
  const swells = all.filter((c) => !c.windSea);
  if (swells.length <= 1 && all.length <= 1) return null;

  return (
    <section className="rounded-xl border border-ink-600 bg-white shadow-card">
      <div className="flex items-center justify-between px-4 pt-3 pb-2">
        <h3 className="text-[11px] uppercase tracking-widest2 text-text-secondary">
          Swell breakdown
        </h3>
        <span className="text-[10px] uppercase tracking-widest text-text-muted">
          {SOURCE_LABEL[forecast.swell_source ?? ''] ?? forecast.swell_source ?? '—'}
        </span>
      </div>
      <div className="px-4 pb-3 divide-y divide-ink-600">
        {all.map((c) => {
          const ft = metersToFeet(c.hs);
          const barColor = c.windSea ? '#94A3B8' : '#0369A1'; // gray wind sea, dark blue swell
          return (
            <div
              key={c.label}
              className={`grid grid-cols-[minmax(0,1fr)_auto_auto_auto_auto] sm:grid-cols-[140px_84px_72px_104px_minmax(0,1fr)_64px] items-center gap-2 sm:gap-3 py-2 ${c.muted ? 'opacity-60' : ''}`}
            >
              <div className="text-xs text-text-secondary truncate">{c.label}</div>
              <div className="flex items-center gap-1.5">
                <SwellCompass
                  deg={c.dp}
                  size={20}
                  color={c.windSea ? '#94A3B8' : '#0369A1'}
                />
                <span className="text-xs text-text-secondary tabular-nums">
                  {c.dp !== null && c.dp !== undefined ? degToCardinal(c.dp) : '—'}
                </span>
              </div>
              <div className="text-right font-bold tabular-nums text-text-primary text-sm">
                {ft !== null ? `${ft.toFixed(1)}ft` : '—'}
              </div>
              <div className="text-right text-xs text-text-secondary tabular-nums">{fmtSec(c.tp)}</div>
              {/* Share bar — desktop only. On mobile this cell is hidden so the grid collapses
                  to 5 columns and the row fits a phone width. */}
              <div className="hidden sm:block">
                <div className="relative h-2 rounded-full bg-ink-800 overflow-hidden">
                  <div
                    className="absolute inset-y-0 left-0 rounded-full"
                    style={{
                      width: `${Math.max(4, c.share * 100)}%`,
                      background: barColor,
                    }}
                  />
                </div>
              </div>
              <div className="text-right text-xs text-text-muted tabular-nums whitespace-nowrap">
                {sharePercent(c.share)}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}
