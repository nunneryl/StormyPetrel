'use client';

import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { Forecast } from '@/lib/types';
import { degToCardinal, fmtShortTime, msToMph } from '@/lib/formatting';

type Pt = { t: number; iso: string; mph: number; dir: number | null };

function buildSeries(rows: Forecast[]): Pt[] {
  return rows.map((r) => ({
    t: new Date(r.valid_time).getTime(),
    iso: r.valid_time,
    mph: msToMph(r.wind_speed) ?? 0,
    dir: r.wind_dir,
  }));
}

function colorFor(mph: number): string {
  if (mph < 8) return '#3aa55c';
  if (mph < 14) return '#9bbf3e';
  if (mph < 20) return '#d8b13a';
  if (mph < 28) return '#d97a2b';
  return '#c2362f';
}

export function WindChart({ forecasts }: { forecasts: Forecast[] }) {
  const data = buildSeries(forecasts);
  return (
    <div className="h-44 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <BarChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <CartesianGrid strokeDasharray="2 4" stroke="#1f3151" />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={['dataMin', 'dataMax']}
            tickFormatter={(v) => fmtShortTime(new Date(v as number).toISOString())}
            stroke="#5a6a7a"
            tick={{ fill: '#8aa3c0', fontSize: 11 }}
          />
          <YAxis
            stroke="#5a6a7a"
            tick={{ fill: '#8aa3c0', fontSize: 11 }}
            tickFormatter={(v) => `${v}mph`}
            width={48}
          />
          <Tooltip
            contentStyle={{
              background: '#0a1220',
              border: '1px solid #1f3151',
              borderRadius: 6,
              fontSize: 12,
            }}
            labelFormatter={(v) => fmtShortTime(new Date(v as number).toISOString())}
            formatter={(value, _key, item) => {
              const dir = (item?.payload as Pt | undefined)?.dir;
              return [`${(value as number).toFixed(0)} mph ${degToCardinal(dir)}`, 'Wind'];
            }}
          />
          <Bar dataKey="mph" isAnimationActive={false} radius={[2, 2, 0, 0]}>
            {data.map((d) => (
              <Cell key={d.iso} fill={colorFor(d.mph)} />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
    </div>
  );
}
