'use client';

import {
  Area,
  AreaChart,
  CartesianGrid,
  ReferenceDot,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { Forecast, TidePrediction } from '@/lib/types';
import { fmtDay, fmtShortTime } from '@/lib/formatting';

type Pt = { t: number; iso: string; level: number | null };

function buildSeries(rows: Forecast[]): Pt[] {
  return rows
    .filter((r) => r.tide_level_ft !== null && r.tide_level_ft !== undefined)
    .map((r) => ({
      t: new Date(r.valid_time).getTime(),
      iso: r.valid_time,
      level: r.tide_level_ft,
    }));
}

export function TideChart({
  forecasts,
  hilo,
}: {
  forecasts: Forecast[];
  hilo: TidePrediction[];
}) {
  const data = buildSeries(forecasts);
  if (data.length === 0) {
    return (
      <div className="h-48 w-full flex items-center justify-center text-text-muted text-sm">
        No tide data for this spot.
      </div>
    );
  }
  const tMin = data[0].t;
  const tMax = data[data.length - 1].t;
  const events = hilo
    .filter((h) => {
      const t = new Date(h.predicted_at).getTime();
      return t >= tMin && t <= tMax;
    })
    .map((h) => ({
      t: new Date(h.predicted_at).getTime(),
      level: h.level_ft,
      type: h.type,
    }));

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
          <CartesianGrid strokeDasharray="2 4" stroke="#1E3048" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={['dataMin', 'dataMax']}
            tickFormatter={(v) => fmtShortTime(new Date(v as number).toISOString())}
            stroke="#64748B"
            tick={{ fill: '#94A3B8', fontSize: 10 }}
            axisLine={false}
            tickLine={false}
          />
          <YAxis
            stroke="#64748B"
            tick={{ fill: '#94A3B8', fontSize: 10 }}
            tickFormatter={(v) => `${v}ft`}
            width={42}
            axisLine={false}
            tickLine={false}
          />
          <Tooltip
            contentStyle={{
              background: '#0B1426',
              border: '1px solid #1E3048',
              borderRadius: 8,
              fontSize: 12,
              color: '#F1F5F9',
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
          {events.map((e) => (
            <ReferenceDot
              key={`${e.t}-${e.type}`}
              x={e.t}
              y={e.level}
              r={3.5}
              fill={e.type === 'H' ? '#84CC16' : '#F97316'}
              stroke="#0B1426"
              strokeWidth={1.5}
              label={{
                value: e.type ?? '',
                fill: '#94A3B8',
                fontSize: 10,
                position: 'top',
              }}
            />
          ))}
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
