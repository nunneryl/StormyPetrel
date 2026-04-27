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
import { fmtDay, fmtShortTime } from '@/lib/formatting';

type Pt = { t: number; iso: string; face: number | null; tp: number | null };

function buildSeries(rows: Forecast[]): Pt[] {
  return rows.map((r) => ({
    t: new Date(r.valid_time).getTime(),
    iso: r.valid_time,
    face: r.face_ft,
    tp: r.tp,
  }));
}

function tickLabel(ms: number): string {
  return fmtShortTime(new Date(ms).toISOString());
}

export function SwellChart({ forecasts }: { forecasts: Forecast[] }) {
  const data = buildSeries(forecasts);
  return (
    <div className="h-44 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="swellFill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor="#3da9d7" stopOpacity={0.7} />
              <stop offset="100%" stopColor="#3da9d7" stopOpacity={0.05} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="2 4" stroke="#1f3151" />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={['dataMin', 'dataMax']}
            tickFormatter={tickLabel}
            stroke="#5a6a7a"
            tick={{ fill: '#8aa3c0', fontSize: 11 }}
          />
          <YAxis
            stroke="#5a6a7a"
            tick={{ fill: '#8aa3c0', fontSize: 11 }}
            tickFormatter={(v) => `${v}ft`}
            width={42}
          />
          <Tooltip
            contentStyle={{
              background: '#0a1220',
              border: '1px solid #1f3151',
              borderRadius: 6,
              fontSize: 12,
            }}
            labelFormatter={(v) =>
              `${fmtDay(new Date(v as number).toISOString())} ${tickLabel(v as number)}`
            }
            formatter={(value, key) => {
              if (key === 'face') return [`${(value as number).toFixed(1)} ft`, 'Face'];
              if (key === 'tp') return [`${(value as number).toFixed(0)} s`, 'Period'];
              return [value, key];
            }}
          />
          <Area
            type="monotone"
            dataKey="face"
            stroke="#3da9d7"
            strokeWidth={2}
            fill="url(#swellFill)"
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
