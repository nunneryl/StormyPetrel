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
import { fmtDay, fmtDayTimeTick, fmtShortTime, metersToFeet } from '@/lib/formatting';

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
  p1:   '#0369A1', // dark ocean blue (primary swell)
  p2:   '#0284C7', // mid blue
  p3:   '#38BDF8', // lighter blue
  ws:   '#94A3B8', // gray (wind sea)
  face: '#0284C7', // brand accent line on top
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

const tickLabel = (ms: number) => fmtDayTimeTick(new Date(ms).toISOString());

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
          <CartesianGrid strokeDasharray="2 4" stroke="#E2E8F0" vertical={false} />
          <XAxis
            dataKey="t"
            type="number"
            scale="time"
            domain={['dataMin', 'dataMax']}
            tickFormatter={tickLabel}
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
            labelStyle={{ color: '#475569', fontWeight: 600 }}
            labelFormatter={(v) => tickLabel(v as number)}
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
            wrapperStyle={{ fontSize: 11, color: '#475569' }}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
