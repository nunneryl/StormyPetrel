'use client';

import { useState } from 'react';
import type { Forecast } from '@/lib/types';
import { tierFromStars, classifyWind, windQualityClass, windQualityLabel } from '@/lib/ratings';
import {
  dayKey,
  degToCardinal,
  fmtDay,
  fmtSec,
  fmtShortTime,
  msToMph,
  pickSwell,
} from '@/lib/formatting';
import { CompassArrow } from './CompassArrow';
import { StarRating, ratingCellBg } from './StarRating';
import { SwellCompass } from './SwellCompass';

// 3-hour buckets matching MSW's row density. interpret writes hourly
// rows; sample every 3rd so the grid is scannable, not overwhelming.
function bucket3hr(rows: Forecast[]): Forecast[] {
  return rows.filter((r) => new Date(r.valid_time).getUTCHours() % 3 === 0);
}

function groupByDay(rows: Forecast[]): Array<{ day: string; rows: Forecast[] }> {
  const groups = new Map<string, Forecast[]>();
  for (const r of rows) {
    const k = dayKey(r.valid_time);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k)!.push(r);
  }
  return Array.from(groups.entries()).map(([day, rs]) => ({ day, rows: rs }));
}

/**
 * "Best window" detector — flags any 3-hour block whose rating is
 * 2+ stars higher than the average of the surrounding two blocks
 * (one before, one after). Used to draw a subtle left-border accent
 * on the row so the eye lands on the best surf time of each day.
 */
function isBestWindow(rows: Forecast[], idx: number): boolean {
  const cur = rows[idx]?.stars ?? 0;
  if (cur < 2) return false;
  const prev = rows[idx - 1]?.stars;
  const next = rows[idx + 1]?.stars;
  const neighbors: number[] = [];
  if (typeof prev === 'number') neighbors.push(prev);
  if (typeof next === 'number') neighbors.push(next);
  if (neighbors.length === 0) return false;
  const avg = neighbors.reduce((a, b) => a + b, 0) / neighbors.length;
  return cur - avg >= 2;
}

// Default forecast window when collapsed — about 48h of 3-hour blocks.
// Show-full reveals the rest out to ~7 days.
const COLLAPSED_HOURS = 48;

export function ForecastGrid({
  forecasts,
  offshoreDeg,
}: {
  forecasts: Forecast[];
  offshoreDeg: number | null | undefined;
}) {
  const [expanded, setExpanded] = useState(false);

  const sampled = bucket3hr(forecasts);
  const fullDays = groupByDay(sampled).slice(0, 7);
  const visibleSampled = expanded
    ? sampled
    : sampled.filter((r) => {
        const dt = new Date(r.valid_time).getTime() - Date.now();
        return dt <= COLLAPSED_HOURS * 3600_000;
      });
  const days = expanded ? fullDays : groupByDay(visibleSampled);

  if (days.length === 0) {
    return (
      <div className="rounded-xl border border-ink-600 bg-ink-900 p-6 text-text-muted">
        No forecast data in the next 7 days.
      </div>
    );
  }

  return (
    <div className="rounded-xl border border-ink-600 bg-white overflow-hidden shadow-card">
      <div className="overflow-x-auto scrollbar-hidden">
        <div className="min-w-[760px]">
          {/* Header row */}
          <div className="grid grid-cols-[80px_140px_64px_64px_120px_140px_72px] gap-2 px-3 py-2 text-[10px] uppercase tracking-widest2 text-text-secondary border-b border-ink-600 bg-ink-900">
            <div className="sticky left-0 z-10 bg-ink-900 -ml-3 pl-3">Time</div>
            <div className="sticky left-[80px] z-10 bg-ink-900">Rating</div>
            <div className="text-right">Face</div>
            <div className="text-right">Period</div>
            <div>Swell</div>
            <div>Wind</div>
            <div className="text-right">Tide</div>
          </div>

          {days.map(({ day, rows }) => (
            <div key={day}>
              <div className="px-3 py-1.5 text-[11px] font-bold uppercase tracking-widest2 text-text-secondary bg-ink-800 border-b border-ink-600 sticky top-0 z-[5]">
                {fmtDay(rows[0].valid_time)}
              </div>
              {rows.map((r, idx) => {
                const tier = tierFromStars(r.stars);
                const dp = pickSwell(r.swell_dp, r.dp);
                const tp = pickSwell(r.swell_tp, r.tp);
                const wMph = msToMph(r.wind_speed);
                const wQ = classifyWind(r.wind_dir, offshoreDeg ?? null);
                const tideUp = idx + 1 < rows.length
                  ? (rows[idx + 1].tide_level_ft ?? null)
                  : null;
                const tideTrend =
                  r.tide_level_ft !== null && r.tide_level_ft !== undefined &&
                  tideUp !== null && tideUp !== undefined
                    ? tideUp > r.tide_level_ft + 0.05
                      ? 'up'
                      : tideUp < r.tide_level_ft - 0.05
                        ? 'down'
                        : 'flat'
                    : null;

                const best = isBestWindow(rows, idx);

                // Row stripe — alternating zebra, MSW-style. White rows
                // and #F8FAFC (ink-900 in the new palette) every other row.
                const rowBg = idx % 2 === 0 ? 'bg-white' : 'bg-ink-900';

                return (
                  <div
                    key={r.valid_time}
                    className={`relative grid grid-cols-[80px_140px_64px_64px_120px_140px_72px] gap-2 px-3 py-1.5 border-b border-ink-600 text-sm ${rowBg} hover:bg-ink-800 transition-colors`}
                    style={
                      best
                        ? {
                            boxShadow: `inset 3px 0 0 ${tier.hex}`,
                          }
                        : undefined
                    }
                  >
                    <div className={`sticky left-0 z-10 ${rowBg} -ml-3 pl-3 text-text-secondary font-mono`}>
                      {fmtShortTime(r.valid_time)}
                    </div>
                    <div className={`sticky left-[80px] z-10 ${rowBg}`}>
                      <span
                        className="flex items-center justify-center w-full h-7 rounded"
                        style={{ background: ratingCellBg(r.stars) }}
                      >
                        <StarRating score={r.stars} size="md" />
                      </span>
                    </div>
                    <div className="text-right font-bold tabular-nums text-text-primary">
                      {r.face_ft !== null && r.face_ft !== undefined
                        ? `${r.face_ft.toFixed(1)}`
                        : '—'}
                    </div>
                    <div className="text-right text-text-secondary tabular-nums">
                      {fmtSec(tp)}
                    </div>
                    <div className="flex items-center gap-1.5">
                      <SwellCompass deg={dp} size={24} />
                      <span className="text-xs text-text-primary tabular-nums">
                        {degToCardinal(dp)}
                      </span>
                      <span className="text-[11px] text-text-muted tabular-nums">
                        {dp !== null && dp !== undefined
                          ? `${dp.toFixed(0)}°`
                          : ''}
                      </span>
                    </div>
                    <div className="flex items-center gap-1.5 text-xs">
                      <CompassArrow deg={r.wind_dir} size={14} variant="wind" showLabel={false} />
                      <span className="font-bold tabular-nums text-text-primary">
                        {wMph !== null ? `${wMph.toFixed(0)}` : '—'}
                      </span>
                      <span className="text-text-muted">mph</span>
                      {wQ !== 'unknown' && (
                        <span className={`px-1 py-0.5 rounded text-[9px] font-medium uppercase tracking-wider ${windQualityClass(wQ)}`}>
                          {windQualityLabel(wQ)}
                        </span>
                      )}
                    </div>
                    <div className="text-right text-text-secondary tabular-nums flex items-center justify-end gap-1">
                      {r.tide_level_ft !== null && r.tide_level_ft !== undefined
                        ? `${r.tide_level_ft.toFixed(1)}ft`
                        : '—'}
                      {tideTrend === 'up' && <span className="text-cyan-500">↑</span>}
                      {tideTrend === 'down' && <span className="text-cyan-500">↓</span>}
                    </div>
                  </div>
                );
              })}
            </div>
          ))}
        </div>
      </div>

      {/* Show-full toggle. Hidden when there's no extra data beyond the
          collapsed window (e.g. data feed shorter than 48h). */}
      {fullDays.length > days.length && !expanded && (
        <button
          type="button"
          onClick={() => setExpanded(true)}
          className="w-full px-4 py-3 text-sm font-bold text-cyan-600 hover:bg-ink-800 border-t border-ink-600 transition"
        >
          Show full 7-day forecast →
        </button>
      )}
      {expanded && (
        <button
          type="button"
          onClick={() => setExpanded(false)}
          className="w-full px-4 py-3 text-sm font-bold text-text-secondary hover:bg-ink-800 hover:text-cyan-600 border-t border-ink-600 transition"
        >
          Show only next 48 hours
        </button>
      )}
    </div>
  );
}
