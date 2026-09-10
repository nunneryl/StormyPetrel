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

/** THE USER-FACING NAME OF EVERY SERIES, and the only place they are written.
 *
 *  THIS CHART PLOTS ONLY `face`. The other four names are kept deliberately.
 *  The partition series were removed because they are UNCORRECTED heights and
 *  `face` is corrected - divided by a per-spot factor, median 1.538 and up to
 *  3.893 across the 130 measured spots - so the two cannot share a y-axis
 *  without the published number being the smallest thing on the chart. The
 *  Swell breakdown panel above the charts already lists the four components
 *  with their own directions, periods and energy shares, as numbers rather than
 *  on a shared scale, which is where a comparison is safe.
 *  The names stay so that if the components ever get their own chart - the
 *  Magicseaweed arrangement, separate panels rather than one combined axis -
 *  nobody has to rediscover that an unnamed recharts series labels itself with
 *  its dataKey.
 *
 *  Each one is passed as the Area's `name`, which is what both <Legend> and
 *  <Tooltip> print. Recharts falls back to the series `dataKey` when `name` is
 *  absent (util/getLegendProps.js and util/ChartUtils getTooltipItem both read
 *  `name || dataKey`), so a series added here without a name shows the reader
 *  an internal column name - that is how the legend came to read "face".
 *
 *  Kept identical to the labels in SwellPartitions.tsx, which names the same
 *  four components in the Swell breakdown panel on the same page. The two must
 *  agree: a reader seeing "P2 (secondary)" in the panel and "p2" in the chart
 *  has no way to know they are the same thing. */
const LABEL = {
  face: 'Swell height',
  p1:   'P1 (primary)',
  p2:   'P2 (secondary)',
  p3:   'P3 (tertiary)',
  ws:   'Wind sea',
} as const;

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

  // THE NOTE BELOW IS CONDITIONAL BECAUSE THE BAND IS. Only spots with a
  // measured face-ratio spread get face_lo_ft/face_hi_ft written
  // (pipeline/forecast/face_correction.py, guarded by has_spread); the rest
  // publish a point and the tile renders one number via fmtFt. Explaining a
  // range on a page that shows no range would be worse than saying nothing, so
  // the note appears only where this window actually carries one.
  const hasBand = forecasts.some(
    (r) => r.face_lo_ft !== null && r.face_lo_ft !== undefined
        && r.face_hi_ft !== null && r.face_hi_ft !== undefined,
  );

  return (
    <>
      <div className="h-48 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <AreaChart data={data} margin={{ top: 8, right: 12, bottom: 0, left: 0 }}>
            <defs>
              {/* ws-fill went with the partition stack - nothing references a
                  wind-sea gradient now. */}
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
              formatter={(value, name) => [
                // `name` is the series' name= prop, i.e. LABEL - recharts passes
                // it here as the second argument. It used to be the dataKey,
                // because no series set a name, so this formatter carried its own
                // copy of the labels to translate it; one list is enough, and
                // keeping two let the legend drift from the tooltip. Returning
                // `name` untouched also keeps a null hour labelled: recharts skips
                // the formatter when the value is null (DefaultTooltipContent
                // line 65), falling back to the same name= this reuses.
                typeof value === 'number' ? `${value.toFixed(1)} ft` : value,
                name,
              ]}
            />
            {/* ONE SERIES, AND IF A SECOND IS EVER ADDED BACK, ADD IT AS AN ARRAY
                ITEM AND NOT INSIDE A FRAGMENT. Recharts collects its series by
                walking these children through util/ReactUtils.toArray, which
                unwraps a Fragment only when react-is isFragment() agrees it is
                one. React 19 renamed element $$typeof to
                Symbol(react.transitional.element); recharts 2.15 pins react-is
                ^18.3.1, which still tests for Symbol(react.element) and so
                answers false for every React 19 element. A Fragment here is
                handed to recharts as one opaque child whose type is not Area and
                everything inside it vanishes from the plot and the legend with no
                error - which is what hid the partition stack from ec17f7b until
                it was found. The same trap catches <Cell> inside <Bar> (see
                BreakerComparison, safe only because it uses .map()). Recording it
                here because this variant is the one with nothing left to break.

                name=, never a renamed dataKey: dataKey is the key into the Pt
                rows buildSeries produces, so renaming it would break the binding
                rather than relabel the series. With the legend gone it is the
                tooltip that prints it. */}
            <Area
              type="monotone"
              dataKey="face"
              name={LABEL.face}
              stroke={COLOR.face}
              strokeWidth={2}
              fill="url(#face-fill)"
              isAnimationActive={false}
            />
            {/* No <Legend>. A one-entry legend reading "Swell height" under a card
                already titled "Swell height (ft)" is the same word twice. */}
          </AreaChart>
        </ResponsiveContainer>
      </div>
      {/* The old copy said "The range shown above it spans the uncertainty in that
          estimate". With the stack drawn there WAS something above the line and a
          reader took it for an uncertainty band; the range has always lived in the
          Swell height tile at the top of the page, never in this plot. Nothing is
          above the line in this variant, so the caption says where to look. */}
      {hasBand && (
        <p className="mt-1.5 text-[10px] leading-snug text-text-muted">
          Swell height is a single best estimate; the tile at the top of the page
          gives its range.
        </p>
      )}
    </>
  );
}
