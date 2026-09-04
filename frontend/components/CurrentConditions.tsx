'use client';

// CLIENT, SO THE HOUR IS THE VIEWER'S. These tiles used to receive a `current` row chosen
// with the SERVER's clock, and the spot routes are statically generated with
// `revalidate = 3600` — so the row could have been picked up to an hour before anyone read
// it. `'use client'` does NOT opt the route out of prerendering: the initial HTML is still
// server-rendered from `serverNowMs` (identical bytes to before, so crawlers and first
// paint are unchanged), and the effect below re-picks with the reader's own clock on mount
// and once a minute after.
import { useEffect, useState } from 'react';

import { CompassArrow } from './CompassArrow';
import { SwellCompass } from './SwellCompass';
import { selectCurrentHour } from '@/lib/currentHour';
import type { Forecast } from '@/lib/types';
import {
  classifySurface, surfaceTextClass, chopLabel,
  classifyWind, windQualityClass, windQualityLabel,
} from '@/lib/ratings';
import {
  degToCardinal,
  fmtFtRange,
  fmtMph,
  fmtSec,
  pickSwell,
} from '@/lib/formatting';

type Tile = {
  label: string;
  value: string;
  hint?: string | null;
  icon?: React.ReactNode;
  rightSpark?: React.ReactNode;
  /** Interactive affordance for this tile, rendered at the right of the hint row. Used by
   *  the Face tile for the surf-report trigger, so the control sits against the number it
   *  is reporting on rather than at the bottom of the page. */
  action?: React.ReactNode;
};

export function CurrentConditions({
  rows,
  serverNowMs,
  offshoreDeg,
  faceAction,
}: {
  /** The spot's forecast rows, INCLUDING the recent past. The current hour's row is the one
   *  whose bucket contains now, so a window starting at `now` would exclude it the moment
   *  the server clock crossed the hour — see selectCurrentHour. */
  rows: Forecast[];
  /** The server's clock at render time. Used for the first paint on BOTH sides so the
   *  markup matches, then replaced by the viewer's clock in the effect below. */
  serverNowMs: number;
  offshoreDeg: number | null | undefined;
  faceAction?: React.ReactNode;
}) {
  // Server render and first client render both use serverNowMs — no hydration mismatch.
  // The effect then corrects to the reader's clock, and the interval keeps a page that is
  // left open from drifting into a stale hour the way the static HTML did.
  const [nowMs, setNowMs] = useState(serverNowMs);
  useEffect(() => {
    setNowMs(Date.now());
    const id = setInterval(() => setNowMs(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);

  const selection = selectCurrentHour(rows, nowMs);
  const current = selection.row;
  // The NEXT hour, for the tide trend arrow — relative to the selected hour, not to
  // whatever happened to be second in the array.
  const forecasts =
    selection.state === 'current' ? rows.slice(selection.index) : [];
  const tp = pickSwell(current?.swell_tp ?? null, current?.tp ?? null);
  const dp = pickSwell(current?.swell_dp ?? null, current?.dp ?? null);

  const wQ = classifyWind(current?.wind_dir ?? null, offshoreDeg ?? null);
  // Conditions is WIND-DERIVED. It reads the same wind_dir/offshore_wind_deg the Wind
  // tile does, plus wind_speed — deliberately, so the two tiles can no longer contradict
  // each other the way chop_ratio-driven labels did (offshore wind + "Blown out" in the
  // same strip). They stay separate functions: Wind reports the wind, Conditions reports
  // what that wind does to the surface. See classifySurface.
  const cQ = classifySurface(
    current?.wind_dir ?? null,
    current?.wind_speed ?? null,
    offshoreDeg ?? null,
  );

  // Tide trend over the next 1 hour for the inline arrow indicator.
  const tideTrend = (() => {
    const a = current?.tide_level_ft;
    const b = forecasts[1]?.tide_level_ft;
    if (a === null || a === undefined || b === null || b === undefined) return null;
    if (b > a + 0.05) return 'rising';
    if (b < a - 0.05) return 'falling';
    return 'slack';
  })();

  return (
    <>
      {/* ABSENT IS NOT THE NEXT HOUR. When no row covers the current hour the tiles render
          empty and say so, rather than quietly showing a neighbouring hour's numbers under
          a heading that means now. That fall-through is how an expired hour came to be
          displayed as current; a gap in the data is a fact about the data and the reader is
          told it. Distinct from a null FIELD — the Tide tile's em-dash means "this hour has
          no tide reading", this banner means "there is no row for this hour at all". */}
      {selection.state === 'absent' && (
        <p className="mb-2 text-xs text-text-muted">
          No forecast published for the current hour. The chart and the 7-day grid below are
          unaffected.
        </p>
      )}
      <section className="grid grid-cols-2 lg:grid-cols-5 gap-3">
      <BigTile
        label="Swell height"
        value={fmtFtRange(current?.face_lo_ft, current?.face_hi_ft, current?.face_ft)}
        hint={tp ? `${fmtSec(tp)} period` : null}
        action={faceAction}
      />
      <BigTile
        label="Swell"
        value={degToCardinal(dp)}
        hint={dp !== null && dp !== undefined ? `${dp.toFixed(0)}°` : null}
        icon={<SwellCompass deg={dp} size={40} />}
      />
      <BigTile
        label="Wind"
        value={fmtMph(current?.wind_speed)}
        hint={
          current?.wind_dir !== null && current?.wind_dir !== undefined
            ? `${degToCardinal(current.wind_dir)}`
            : null
        }
        icon={<CompassArrow deg={current?.wind_dir ?? null} size={22} variant="wind" showLabel={false} />}
        badge={
          wQ !== 'unknown' && (
            <span className={`px-1.5 py-0.5 rounded text-[10px] font-bold uppercase tracking-widest2 ${windQualityClass(wQ)}`}>
              {windQualityLabel(wQ)}
            </span>
          )
        }
      />
      <BigTile
        label="Tide"
        value={
          current?.tide_level_ft !== null && current?.tide_level_ft !== undefined
            ? `${current.tide_level_ft.toFixed(1)} ft`
            : '—'
        }
        hint={tideTrend ?? null}
        icon={
          tideTrend === 'rising' ? (
            <span className="text-cyan-400 text-xl leading-none">↑</span>
          ) : tideTrend === 'falling' ? (
            <span className="text-cyan-400 text-xl leading-none">↓</span>
          ) : null
        }
      />
      {/* The word appears ONCE, as the value — it is this tile's primary datum, the way
          Face/Swell/Wind/Tide each put theirs there. The badge that used to repeat it is
          gone; its colour moved onto the value via surfaceTextClass, so no information is
          lost. 'unknown' yields an empty class, leaving the em-dash exactly as before.

          The hint is a DIFFERENT measurement and is labelled as one. chop_ratio is the
          wind-sea fraction of total Hs — a swell-composition statistic, not the surface
          state above it — and this tile is the only place that number surfaces at all. */}
      <BigTile
        label="Conditions"
        value={chopLabel(cQ)}
        valueClass={surfaceTextClass(cQ)}
        hint={
          current?.chop_ratio !== null && current?.chop_ratio !== undefined
            ? `swell mix · ${(current.chop_ratio * 100).toFixed(0)}% wind sea`
            : null
        }
      />
      </section>
    </>
  );
}

function BigTile({
  label,
  value,
  valueClass,
  hint,
  icon,
  badge,
  rightSpark,
  action,
}: Tile & { badge?: React.ReactNode; valueClass?: string }) {
  return (
    <div className="rounded-xl border border-ink-600 bg-ink-800/60 p-3.5">
      <div className="flex items-start justify-between mb-2">
        <span className="text-[10px] uppercase tracking-widest2 text-text-secondary">
          {label}
        </span>
        {badge}
      </div>
      <div className="flex items-center gap-2">
        {icon}
        <span className={`text-2xl font-bold tabular-nums tracking-tightish ${valueClass || 'text-text-primary'}`}>
          {value}
        </span>
      </div>
      {(hint || rightSpark || action) && (
        <div className="mt-1 flex items-center justify-between gap-2 text-xs text-text-muted">
          <span>{hint}</span>
          {rightSpark}
          {action}
        </div>
      )}
    </div>
  );
}
