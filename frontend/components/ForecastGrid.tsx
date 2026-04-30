import type { Forecast } from '@/lib/types';
import { tierFromStars, classifyWind, windQualityClass, windQualityLabel } from '@/lib/ratings';
import {
  dayKey,
  fmtDay,
  fmtSec,
  fmtShortTime,
  msToMph,
  pickSwell,
} from '@/lib/formatting';
import { CompassArrow } from './CompassArrow';

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

export function ForecastGrid({
  forecasts,
  offshoreDeg,
}: {
  forecasts: Forecast[];
  offshoreDeg: number | null | undefined;
}) {
  const sampled = bucket3hr(forecasts);
  const days = groupByDay(sampled).slice(0, 7);

  if (days.length === 0) {
    return (
      <div className="rounded-xl border border-ink-600 bg-ink-900 p-6 text-text-muted">
        No forecast data in the next 7 days.
      </div>
    );
  }

  // Largest face_ft across the whole window — used to scale wave-bar widths.
  const maxFace = Math.max(...sampled.map((r) => r.face_ft ?? 0), 1);

  return (
    <div className="rounded-xl border border-ink-600 bg-white overflow-hidden shadow-card">
      <div className="overflow-x-auto scrollbar-hidden">
        <div className="min-w-[820px]">
          {/* Header row */}
          <div className="grid grid-cols-[80px_140px_140px_64px_120px_140px_72px] gap-2 px-3 py-2 text-[10px] uppercase tracking-widest2 text-text-secondary border-b border-ink-600 bg-ink-900">
            <div className="sticky left-0 z-10 bg-ink-900 -ml-3 pl-3">Time</div>
            <div className="sticky left-[80px] z-10 bg-ink-900">Rating</div>
            <div>Face</div>
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
                const faceFraction = (r.face_ft ?? 0) / maxFace;
                const tierFg =
                  tier.label === 'FAIR' || tier.label === 'FAIR TO GOOD'
                    ? 'text-ink-950 [color:#0F172A]'
                    : 'text-white';

                const best = isBestWindow(rows, idx);

                // Row stripe — alternating zebra, MSW-style. White rows
                // and #F8FAFC (ink-900 in the new palette) every other row.
                const rowBg = idx % 2 === 0 ? 'bg-white' : 'bg-ink-900';

                return (
                  <div
                    key={r.valid_time}
                    className={`relative grid grid-cols-[80px_140px_140px_64px_120px_140px_72px] gap-2 px-3 py-1.5 border-b border-ink-600 text-sm ${rowBg} hover:bg-ink-800 transition-colors`}
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
                        className={`flex items-center justify-center w-full h-7 rounded text-[10px] font-bold uppercase tracking-widest2 ${tierFg}`}
                        style={{ background: tier.hex }}
                      >
                        {tier.label}
                      </span>
                    </div>
                    <div className="flex items-center gap-2">
                      <div className="relative flex-1 h-3 rounded bg-ink-800 overflow-hidden">
                        <div
                          className="absolute inset-y-0 left-0 rounded"
                          style={{
                            width: `${Math.max(4, faceFraction * 100)}%`,
                            background: tier.hex,
                            opacity: 0.85,
                          }}
                        />
                      </div>
                      <span className="font-bold tabular-nums text-text-primary w-11 text-right">
                        {r.face_ft !== null && r.face_ft !== undefined
                          ? `${r.face_ft.toFixed(1)}`
                          : '—'}
                      </span>
                    </div>
                    <div className="text-right text-text-secondary tabular-nums">
                      {fmtSec(tp)}
                    </div>
                    <div className="flex items-center gap-1.5">
                      <CompassArrow deg={dp} size={14} variant="swell" showLabel={false} />
                      <span className="text-xs text-text-secondary tabular-nums">
                        {dp !== null && dp !== undefined
                          ? `${dp.toFixed(0)}°`
                          : '—'}
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
    </div>
  );
}
