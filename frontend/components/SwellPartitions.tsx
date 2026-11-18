'use client';

import { useCurrentHour } from './CurrentHour';
import { SwellCompass } from './SwellCompass';
import { SwellWindowCompass } from './SwellWindowCompass';
import { degToCardinal, fmtSec, metersToFeet } from '@/lib/formatting';
import { breakdownNote, breakdownRows, sharePercent } from '@/lib/swellBreakdown';
import { NO_WINDOW_NOTE, rowStyles } from '@/lib/swellWindowCompass';
import type { Spot } from '@/lib/types';

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

// The Swell breakdown card: the swell compass on the left (on top on phones) and the rows on the
// right. Both are drawn from the same rows (breakdownRows) for the hour the Conditions tiles show,
// and each row's chip is its arrow's colour (lib/swellWindowCompass.ts rowStyles).
export function SwellPartitions({
  windowArcs,
  facingDeg,
  optimalDeg,
  offshoreDeg,
  coastPath,
}: {
  windowArcs: Spot['swell_window_arcs'];
  facingDeg: Spot['orientation_deg'];
  optimalDeg: Spot['optimal_swell_dir'];
  offshoreDeg: Spot['offshore_wind_deg'];
  /** The land around the spot (lib/coastline.ts), read by the server page. */
  coastPath: string | null;
}) {
  // The hour the Conditions tiles show: one clock, one row (components/CurrentHour.tsx).
  const forecast = useCurrentHour().selection.row;
  if (!forecast) return null;
  // Every row's share of the total wave height, the measure the Conditions tile's "% wind sea"
  // uses (lib/swellBreakdown.ts).
  const all = breakdownRows(forecast, windowArcs, facingDeg);
  const styles = rowStyles(all, windowArcs);
  const noWindow = !windowArcs || windowArcs.length === 0;

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
      <div className="grid grid-cols-1 md:grid-cols-[minmax(0,500px)_minmax(0,1fr)] md:items-center gap-x-4 px-2 sm:px-4 pb-3">
        <div className="flex flex-col items-center">
          <SwellWindowCompass
            rows={all}
            windowArcs={windowArcs}
            coastPath={coastPath}
            optimalDeg={optimalDeg}
            offshoreDeg={offshoreDeg}
            windDir={forecast.wind_dir}
            windSpeed={forecast.wind_speed}
          />
          {noWindow && (
            <p className="text-[11px] text-text-muted" data-compass="no-window-note">{NO_WINDOW_NOTE}</p>
          )}
        </div>
        <div className="px-2 sm:px-0">
          {all.length === 0 ? (
            <p className="py-2 text-xs text-text-muted">No swell partitions for this hour.</p>
          ) : (
            <div className="divide-y divide-ink-600">
              {all.map((c, i) => {
                const ft = metersToFeet(c.hs);
                const chip = styles[i].colour;
                return (
                  <div
                    key={c.label}
                    className={`grid grid-cols-[minmax(0,1fr)_auto_auto_auto_auto] sm:grid-cols-[150px_84px_64px_40px_minmax(0,1fr)_44px] items-center gap-2 sm:gap-3 py-2 ${c.muted ? 'opacity-60' : ''}`}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span
                        className="inline-block h-3 w-3 shrink-0 rounded-sm"
                        style={chip ? { background: chip } : { border: '1.5px solid #94A3B8' }}
                        data-chip={chip ?? 'none'}
                      />
                      <span className="text-xs text-text-secondary truncate">{c.label}</span>
                    </div>
                    <div className="flex items-center gap-1.5">
                      <SwellCompass deg={c.dp} size={20} color={chip ?? '#94A3B8'} />
                      <span className="text-xs text-text-secondary tabular-nums">
                        {c.dp !== null && c.dp !== undefined ? degToCardinal(c.dp) : '—'}
                      </span>
                    </div>
                    <div className="text-right font-bold tabular-nums text-text-primary text-sm">
                      {ft !== null ? `${ft.toFixed(1)}ft` : '—'}
                    </div>
                    <div className="text-right text-xs text-text-secondary tabular-nums">{fmtSec(c.tp)}</div>
                    {/* Share bar, in the row's colour: desktop only, so a phone row keeps five
                        columns and fits. */}
                    <div className="hidden sm:block">
                      <div className="relative h-2 rounded-full bg-ink-800 overflow-hidden">
                        <div
                          className="absolute inset-y-0 left-0 rounded-full"
                          style={{ width: `${Math.max(4, c.share * 100)}%`, background: chip ?? '#94A3B8' }}
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
          )}
          {/* What the two columns measure: they come from two models (lib/swellBreakdown.ts). */}
          <p className="pt-2 text-[11px] leading-snug text-text-muted">{breakdownNote(forecast)}</p>
        </div>
      </div>
    </section>
  );
}
