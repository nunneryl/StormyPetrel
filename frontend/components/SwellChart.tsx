'use client';

import {
  Area,
  AreaChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts';
import type { Forecast } from '@/lib/types';
import { fmtDay, fmtShortTime, metersToFeet } from '@/lib/formatting';

type Pt = {
  t: number;
  iso: string;
  p1: number | null;
  p2: number | null;
  p3: number | null;
  ws: number | null;
  face: number | null;
};

const COLOR = {
  p1:   '#0EA5E9',
  p2:   '#38BDF8',
  p3:   '#7DD3FC',
  ws:   '#94A3B8',
  face: '#00B4D8',
};

function buildSeries(rows: Forecast[]): Pt[] {
  return rows.map((r) => ({
    t: new Date(r.valid_time).getTime(),
    iso: r.valid_time,
    p1:   metersToFeet(r.swell_1_hs),
    p2:   metersToFeet(r.swell_2_hs),
    p3:   metersToFeet(r.swell_3_hs),
    ws:   metersToFeet(r.wind_wave_hs),
    face: r.face_ft,
  }));
}

const tickLabel = (ms: number) => fmtShortTime(new Date(ms).toISOString());

export function SwellChart({ forecasts }: { forecasts: Forecast[] }) {
  const data = buildSeries(forecasts);
  const hasPartitions = data.some((d) => d.p1 || d.p2 || d.p3);

  return (
    <div className="h-48 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
          <defs>
            <linearGradient id="ws-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={COLOR.ws} stopOpacity={0.45} />
              <stop offset="100%" stopColor={COLOR.ws} stopOpacity={0.05} />
            </linearGradient>
            <linearGradient id="face-fill" x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={COLOR.face} stopOpacity={0.55} />
              <stop offset="100%" stopColor={COLOR.face} stopOpacity={0.03} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="2 4" stroke="#1E3048" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={['dataMin', 'dataMax']}
            tickFormatter={tickLabel}
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
            labelStyle={{ color: '#94A3B8', fontWeight: 600 }}
            labelFormatter={(v) =>
              `${fmtDay(new Date(v as number).toISOString())} ${tickLabel(v as number)}`
            }
            formatter={(value, key) => {
              if (typeof value !== 'number') return [value, key];
              const v = `${value.toFixed(1)} ft`;
              const map: Record<string, string> = {
                face: 'Face',
                p1: 'P1', p2: 'P2', p3: 'P3', ws: 'Wind sea',
              };
              return [v, map[key as string] ?? key];
            }}
          />
          {hasPartitions && (
            <>
              <Area
                type="monotone"
                dataKey="ws"
                stackId="comp"
                stroke="none"
                fill="url(#ws-fill)"
                isAnimationActive={false}
              />
              <Area
                type="monotone"
                dataKey="p3"
                stackId="comp"
                stroke="none"
                fill={COLOR.p3}
                fillOpacity={0.35}
                isAnimationActive={false}
              />
              <Area
                type="monotone"
                dataKey="p2"
                stackId="comp"
                stroke="none"
                fill={COLOR.p2}
                fillOpacity={0.55}
                isAnimationActive={false}
              />
              <Area
                type="monotone"
                dataKey="p1"
                stackId="comp"
                stroke="none"
                fill={COLOR.p1}
                fillOpacity={0.75}
                isAnimationActive={false}
              />
            </>
          )}
          <Area
            type="monotone"
            dataKey="face"
            stroke={COLOR.face}
            strokeWidth={2}
            fill="url(#face-fill)"
            isAnimationActive={false}
          />
          <Legend
            verticalAlign="top"
            height={20}
            iconType="rect"
            iconSize={10}
            wrapperStyle={{ fontSize: 11, color: '#94A3B8' }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
