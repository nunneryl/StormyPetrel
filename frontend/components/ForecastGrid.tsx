import type { Forecast } from '@/lib/types';
import { tierFromStars } from '@/lib/ratings';
import {
  dayKey,
  fmtDay,
  fmtFt,
  fmtMph,
  fmtSec,
  fmtShortTime,
  pickSwell,
} from '@/lib/formatting';
import { CompassArrow } from './CompassArrow';

// 3-hour buckets. interpret writes hourly rows; we sample every 3rd to
// match the MSW layout (8 rows/day) without overwhelming the page.
function bucket3hr(rows: Forecast[]): Forecast[] {
  const out: Forecast[] = [];
  for (const r of rows) {
    const h = new Date(r.valid_time).getUTCHours();
    if (h % 3 === 0) out.push(r);
  }
  return out;
}

function groupByDay(rows: Forecast[]): Array<{ day: string; rows: Forecast[] }> {
  const groups = new Map<string, Forecast[]>();
  for (const r of rows) {
    const k = dayKey(r.valid_time);
    if (!groups.has(k)) groups.set(k, []);
    groups.get(k)!.push(r);
  }
  return Array.from(groups.entries()).map(([day, rows]) => ({ day, rows }));
}

export function ForecastGrid({ forecasts }: { forecasts: Forecast[] }) {
  const sampled = bucket3hr(forecasts);
  const days = groupByDay(sampled).slice(0, 7);

  if (days.length === 0) {
    return (
      <div className="rounded border border-ink-700 bg-ink-900 p-6 text-slate-400">
        No forecast data in the next 7 days.
      </div>
    );
  }

  return (
    <div className="rounded border border-ink-700 bg-ink-900 overflow-hidden">
      <div className="hidden md:grid grid-cols-[110px_140px_70px_60px_70px_110px_70px] gap-2 px-3 py-2 text-[11px] uppercase tracking-wider text-slate-400 border-b border-ink-700 bg-ink-800/60">
        <div>Time</div>
        <div>Rating</div>
        <div className="text-right">Face</div>
        <div className="text-right">Period</div>
        <div>Swell</div>
        <div>Wind</div>
        <div className="text-right">Tide</div>
      </div>

      {days.map(({ day, rows }) => (
        <div key={day}>
          <div className="px-3 py-2 text-xs font-bold uppercase tracking-widest text-slate-300 bg-ink-800 border-b border-ink-700">
            {fmtDay(rows[0].valid_time)}
          </div>
          {rows.map((r) => {
            const tier = tierFromStars(r.stars);
            const dp = pickSwell(r.swell_dp, r.dp);
            const tp = pickSwell(r.swell_tp, r.tp);
            return (
              <div
                key={r.valid_time}
                className="grid grid-cols-2 md:grid-cols-[110px_140px_70px_60px_70px_110px_70px] gap-2 px-3 py-2 border-b border-ink-700/50 text-sm hover:bg-ink-800/40"
              >
                <div className="text-slate-300 font-mono">
                  {fmtShortTime(r.valid_time)}
                </div>
                <div>
                  <span
                    className={`inline-block px-2 py-1 rounded text-[10px] font-bold uppercase tracking-wider ${tier.bg} ${tier.fg}`}
                  >
                    {tier.label}
                  </span>
                </div>
                <div className="md:text-right font-bold tabular-nums">
                  {fmtFt(r.face_ft)}
                </div>
                <div className="md:text-right text-slate-300 tabular-nums">
                  {fmtSec(tp)}
                </div>
                <div>
                  <CompassArrow deg={dp} size={14} color="#3da9d7" />
                </div>
                <div className="flex items-center gap-2">
                  <CompassArrow deg={r.wind_dir} size={14} color="#9bbf3e" showLabel={false} />
                  <span className="text-slate-300 tabular-nums">{fmtMph(r.wind_speed)}</span>
                </div>
                <div className="md:text-right text-slate-400 tabular-nums">
                  {r.tide_level_ft !== null && r.tide_level_ft !== undefined
                    ? `${r.tide_level_ft.toFixed(1)}ft`
                    : '—'}
                </div>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}
