'use client';

import {
  CartesianGrid,
  Line,
  LineChart,
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
      <div className="h-44 w-full flex items-center justify-center text-slate-500 text-sm">
        No tide data for this spot.
      </div>
    );
  }
  // Window the hilo events to the chart's time range so reference dots
  // don't try to render off-screen.
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
    <div className="h-44 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 16, bottom: 0, left: 0 }}>
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
              `${fmtDay(new Date(v as number).toISOString())} ${fmtShortTime(new Date(v as number).toISOString())}`
            }
            formatter={(value) => [`${(value as number).toFixed(1)} ft`, 'Tide']}
          />
          <Line
            type="monotone"
            dataKey="level"
            stroke="#1ea098"
            strokeWidth={2}
            dot={false}
            isAnimationActive={false}
          />
          {events.map((e) => (
            <ReferenceDot
              key={`${e.t}-${e.type}`}
              x={e.t}
              y={e.level}
              r={4}
              fill={e.type === 'H' ? '#9bbf3e' : '#d97a2b'}
              stroke="#04080f"
              label={{
                value: e.type ?? '',
                fill: '#cbd5e1',
                fontSize: 10,
                position: 'top',
              }}
            />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </div>
  );
}
