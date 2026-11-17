'use client';

import { useCurrentHour } from './CurrentHour';
import { degToCardinal, fmtMph, metersToFeet } from '@/lib/formatting';
import { breakdownRows } from '@/lib/swellBreakdown';
import {
  NO_WINDOW_NOTE,
  compassArrows,
  compassPoint,
  wedgePath,
} from '@/lib/swellWindowCompass';
import type { Spot } from '@/lib/types';

// The spot's swell window, its optimal swell direction, the hour's swells and its wind, on one
// compass. Display only. The arrows are the Swell breakdown's own rows for the hour the Conditions
// tiles show, so the two cannot disagree (lib/swellWindowCompass.ts).

const SIZE = 240;
const C = SIZE / 2;
const R = 88;            // the ring
const HEAD_R = 12;       // arrows stop this far from the centre
const SWELL = '#0369A1'; // as the breakdown: dark blue swell
const WIND_SEA = '#94A3B8'; // as the breakdown: grey wind sea
const WIND = '#B45309';
const OPTIMAL = '#15803D';
const WEDGE_FILL = 'rgba(3, 105, 161, 0.12)';
const WEDGE_STROKE = 'rgba(3, 105, 161, 0.35)';

function Arrow({ dp, len, color, opacity, dashed, label, testId }: {
  dp: number; len: number; color: string; opacity: number; dashed?: boolean; label: string;
  testId: string;
}) {
  const tail = compassPoint(dp, len, C, C);
  const head = compassPoint(dp, HEAD_R, C, C);
  // The head: a small triangle at the inner end, pointing at the centre (the way the waves travel).
  const left = compassPoint(dp - 18, HEAD_R + 9, C, C);
  const right = compassPoint(dp + 18, HEAD_R + 9, C, C);
  const text = compassPoint(dp, len + 9, C, C);
  return (
    <g opacity={opacity} data-compass={testId}>
      <line x1={tail.x} y1={tail.y} x2={head.x} y2={head.y} stroke={color} strokeWidth={3}
        strokeLinecap="round" strokeDasharray={dashed ? '5 4' : undefined} />
      <polygon points={`${head.x},${head.y} ${left.x},${left.y} ${right.x},${right.y}`} fill={color} />
      <text x={text.x} y={text.y} fill={color} fontSize={10} fontWeight={700} textAnchor="middle"
        dominantBaseline="middle">{label}</text>
    </g>
  );
}

export function SwellWindowCompass({
  windowArcs,
  facingDeg,
  optimalDeg,
}: {
  windowArcs: Spot['swell_window_arcs'];
  facingDeg: Spot['orientation_deg'];
  optimalDeg: Spot['optimal_swell_dir'];
}) {
  // The hour the Conditions tiles show: one clock, one row (components/CurrentHour.tsx).
  const forecast = useCurrentHour().selection.row;
  if (!forecast) return null;
  // Exactly the Swell breakdown's rows (lib/swellBreakdown.ts).
  const rows = breakdownRows(forecast, windowArcs, facingDeg);
  const { arrows, noDirection } = compassArrows(rows);
  const arcs = windowArcs ?? [];
  const windDir = forecast.wind_dir;
  const span = R - HEAD_R - 14;

  const said = [
    arcs.length ? `${arcs.length === 1 ? 'Swell window' : 'Swell windows'} ${arcs
      .map((a) => `${Math.round(a.min)}° to ${Math.round(a.max)}°`).join(' and ')}` : NO_WINDOW_NOTE,
    optimalDeg !== null && optimalDeg !== undefined
      ? `best swell from ${degToCardinal(optimalDeg)} ${Math.round(optimalDeg)}°` : null,
    ...arrows.map((a) => `${a.label} ${metersToFeet(a.hs)!.toFixed(1)} ft from ${degToCardinal(a.dp)}`),
    windDir !== null && windDir !== undefined
      ? `wind ${fmtMph(forecast.wind_speed)} from ${degToCardinal(windDir)}` : null,
  ].filter(Boolean).join('; ');

  return (
    <section className="rounded-xl border border-ink-600 bg-white shadow-card">
      <div className="px-4 pt-3 pb-1">
        <h3 className="text-[11px] uppercase tracking-widest2 text-text-secondary">Swell compass</h3>
      </div>
      <div className="flex justify-center px-2">
        <svg width={SIZE} height={SIZE} viewBox={`0 0 ${SIZE} ${SIZE}`} role="img" aria-label={said}
          className="max-w-full h-auto">
          {arcs.map((a, i) => (
            <path key={i} d={wedgePath(a, R, C, C)} fill={WEDGE_FILL} stroke={WEDGE_STROKE}
              strokeWidth={1} data-compass="wedge" />
          ))}
          <circle cx={C} cy={C} r={R} fill="none" stroke="#D1D5DB" strokeWidth={1} />
          {(['N', 'E', 'S', 'W'] as const).map((n, i) => {
            const p = compassPoint(i * 90, R + 14, C, C);
            return (
              <text key={n} x={p.x} y={p.y} fontSize={11} fill="#64748B" textAnchor="middle"
                dominantBaseline="middle">{n}</text>
            );
          })}
          {optimalDeg !== null && optimalDeg !== undefined && (() => {
            const tip = compassPoint(optimalDeg, R - 2, C, C);
            const l = compassPoint(optimalDeg - 5, R + 8, C, C);
            const r = compassPoint(optimalDeg + 5, R + 8, C, C);
            return (
              <polygon points={`${tip.x},${tip.y} ${l.x},${l.y} ${r.x},${r.y}`} fill={OPTIMAL}
                data-compass="optimal" />
            );
          })()}
          {arrows.map((a) => (
            <Arrow key={a.label} dp={a.dp} len={HEAD_R + 14 + a.lengthFrac * span}
              color={a.windSea ? WIND_SEA : SWELL} opacity={a.faded ? 0.4 : a.muted ? 0.6 : 1}
              label={a.short} testId={a.faded ? 'row-faded' : 'row'} />
          ))}
          {windDir !== null && windDir !== undefined && (
            <Arrow dp={windDir} len={R - 22} color={WIND} opacity={1} dashed label="Wind"
              testId="wind" />
          )}
        </svg>
      </div>
      <ul className="flex flex-wrap gap-x-4 gap-y-1 px-4 pb-1 text-[11px] text-text-muted">
        <li><span style={{ color: SWELL }}>&#9632;</span> Swell</li>
        <li><span style={{ color: WIND_SEA }}>&#9632;</span> Wind sea</li>
        <li><span style={{ color: WIND }}>&#9632;</span> Wind</li>
        {optimalDeg !== null && optimalDeg !== undefined && (
          <li><span style={{ color: OPTIMAL }}>&#9650;</span> Best swell direction</li>
        )}
        {arcs.length > 0 && <li><span style={{ color: WEDGE_STROKE }}>&#9632;</span> Swell window</li>}
      </ul>
      <p className="px-4 pb-3 text-[11px] leading-snug text-text-muted">
        {arcs.length === 0 && <span data-compass="no-window">{NO_WINDOW_NOTE}. </span>}
        Arrows point the way the waves travel; longer is bigger. Faint arrows are much smaller
        than the biggest.
        {noDirection.length > 0 && (
          <span data-compass="no-direction">
            {' '}No direction for {noDirection.map((r) => r.label).join(', ')}.
          </span>
        )}
      </p>
    </section>
  );
}
