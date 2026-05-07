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
import { degToCardinal, fmtDayTimeTick, msToMph } from '@/lib/formatting';
import { classifyWind } from '@/lib/ratings';

type Pt = {
  t: number;
  iso: string;
  mph: number;
  dir: number | null;
  /** quality: 'offshore'|'cross'|'onshore' — drives fill color. */
  q: 'offshore' | 'cross' | 'onshore' | 'unknown';
};

function buildSeries(rows: Forecast[], offshoreDeg: number | null): Pt[] {
  return rows.map((r) => {
    const w = classifyWind(r.wind_dir, offshoreDeg);
    const q: Pt['q'] =
      w === 'offshore' || w === 'cross-offshore'
        ? 'offshore'
        : w === 'onshore' || w === 'cross-onshore'
          ? 'onshore'
          : w === 'cross'
            ? 'cross'
            : 'unknown';
    return {
      t: new Date(r.valid_time).getTime(),
      iso: r.valid_time,
      mph: msToMph(r.wind_speed) ?? 0,
      dir: r.wind_dir,
      q,
    };
  });
}

const FILL = {
  offshore: '#22C55E',
  cross:    '#EAB308',
  onshore:  '#EF4444',
  unknown:  '#94A3B8',
};

export function WindChart({
  forecasts,
  offshoreDeg,
}: {
  forecasts: Forecast[];
  offshoreDeg: number | null | undefined;
}) {
  const data = buildSeries(forecasts, offshoreDeg ?? null);

  return (
    <div className="h-48 w-full relative">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 16, right: 12, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="wind-fill-off" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={FILL.offshore} stopOpacity={0.4} />
              <stop offset="100%" stopColor={FILL.offshore} stopOpacity={0.04} />
            </linearGradient>
            <linearGradient id="wind-fill-cross" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={FILL.cross} stopOpacity={0.4} />
              <stop offset="100%" stopColor={FILL.cross} stopOpacity={0.04} />
            </linearGradient>
            <linearGradient id="wind-fill-on" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={FILL.onshore} stopOpacity={0.4} />
              <stop offset="100%" stopColor={FILL.onshore} stopOpacity={0.04} />
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
            tickFormatter={(v) => `${v}`}
            width={32}
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
            labelFormatter={(v) => fmtDayTimeTick(new Date(v as number).toISOString())}
            formatter={(value, _key, item) => {
              const dir = (item?.payload as Pt | undefined)?.dir;
              return [`${(value as number).toFixed(0)} mph ${degToCardinal(dir)}`, 'Wind'];
            }}
          />
          <Area
            type="monotone"
            dataKey="mph"
            stroke="#15803D"
            strokeWidth={1.5}
            fillOpacity={1}
            fill={(() => {
              // pick a representative gradient based on the *median*
              // wind quality across the visible series. (Recharts can't
              // do per-point gradient fills without much more code.)
              const qs = data.map((d) => d.q);
              const counts = qs.reduce<Record<string, number>>((acc, q) => {
                acc[q] = (acc[q] ?? 0) + 1;
                return acc;
              }, {});
              const winner = Object.entries(counts).sort((a, b) => b[1] - a[1])[0]?.[0];
              if (winner === 'offshore') return 'url(#wind-fill-off)';
              if (winner === 'onshore')  return 'url(#wind-fill-on)';
              return 'url(#wind-fill-cross)';
            })()}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
