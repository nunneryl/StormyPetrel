'use client';

import {
  Area,
  AreaChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { Forecast } from '@/lib/types';
import { fmtDayTimeTick, fmtDay, fmtShortTime } from '@/lib/formatting';

type Pt = { t: number; iso: string; level: number | null };

/** Build the tide curve from hourly forecast samples only. The
 *  tide_predictions H/L events used to be spliced in too, but they
 *  occur off-hour with their own interpolated levels that didn't
 *  match the forecast hourly values — so the merged series had near-
 *  duplicate timestamps with conflicting levels, which the monotone
 *  AreaChart rendered as sharp drop-and-recover spikes. Forecast
 *  hourlies are smooth on their own. */
function buildSeries(rows: Forecast[]): Pt[] {
  const out: Pt[] = [];
  const seen = new Set<number>();
  for (const r of rows) {
    if (r.tide_level_ft === null || r.tide_level_ft === undefined) continue;
    const t = new Date(r.valid_time).getTime();
    // Belt-and-suspenders dedupe: if two forecast rows ever land on
    // the same minute, keep the first and drop the second so we never
    // end up with two y values at one x.
    if (seen.has(t)) continue;
    seen.add(t);
    out.push({ t, iso: r.valid_time, level: r.tide_level_ft });
  }
  out.sort((a, b) => a.t - b.t);
  return out;
}

export function TideChart({ forecasts }: { forecasts: Forecast[] }) {
  const data = buildSeries(forecasts);
  if (data.length === 0) {
    return (
      <div className="h-48 w-full flex items-center justify-center text-text-muted text-sm">
        No tide data for this spot.
      </div>
    );
  }

  return (
    <div className="h-48 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 16, right: 12, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="tide-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#14B8A6" stopOpacity={0.5} />
              <stop offset="100%" stopColor="#14B8A6" stopOpacity={0.04} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="2 4" stroke="#E2E8F0" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={['dataMin', 'dataMax']}
            tickFormatter={(v) => fmtDayTimeTick(new Date(v as number).toISOString())}
            stroke="#94A3B8"
            tick={{ fill: '#475569', fontSize: 10 }}
            axisLine={false}
            tickLine={false}
            minTickGap={48}
          />
          <YAxis
            stroke="#94A3B8"
            tick={{ fill: '#475569', fontSize: 10 }}
            tickFormatter={(v) => `${v}ft`}
            width={42}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            contentStyle={{
              background: '#FFFFFF',
              border: '1px solid #E2E8F0',
              borderRadius: 8,
              fontSize: 12,
              color: '#0F172A',
              boxShadow: '0 8px 24px -8px rgba(15,23,42,0.18)',
            }}
            labelFormatter={(v) =>
              `${fmtDay(new Date(v as number).toISOString())} ${fmtShortTime(new Date(v as number).toISOString())}`
            }
            formatter={(value) => [`${(value as number).toFixed(1)} ft`, 'Tide']}
          />
          <Area
            type="monotone"
            dataKey="level"
            stroke="#14B8A6"
            strokeWidth={2}
            fill="url(#tide-fill)"
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
