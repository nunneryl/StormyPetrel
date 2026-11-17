// The swell compass drawn in the Swell breakdown card (components/SwellPartitions.tsx). It takes
// the card's own rows and draws nothing else for the swells, so the two cannot disagree
// (lib/swellWindowCompass.ts). Display only.

import { classifyWind, windQualityClass } from '@/lib/ratings';
import { degToCardinal, fmtMph } from '@/lib/formatting';
import type { BreakdownRow } from '@/lib/swellBreakdown';
import {
  BEACH_R, CX, CY, FONT_PX, HEAD_R, MARK_R, NO_WINDOW_NOTE, RING_R, RING_W, TAIL_R, VIEW_H, VIEW_W,
  compassArrows, compassPoint, layoutLabels, ringArcPath, type LabelIn,
} from '@/lib/swellWindowCompass';
import type { Spot } from '@/lib/types';

const OPEN = '#0EA5E9';
const BLOCKED = '#E2E8F0';
const WATER = '#BAE6FD';
const SAND = '#FCD34D';
const LAND = '#D6CFB8';
const INK = '#475569';

/** The Wind tile's text colour for this wind (lib/ratings.ts windQualityClass). */
function windTextClass(dir: number, offshore: number | null | undefined): string {
  return windQualityClass(classifyWind(dir, offshore)).split(' ').find((c) => c.startsWith('text-'))
    ?? 'text-text-muted';
}

export function SwellWindowCompass({
  rows,
  windowArcs,
  facingDeg,
  optimalDeg,
  offshoreDeg,
  windDir,
  windSpeed,
}: {
  rows: BreakdownRow[];
  windowArcs: Spot['swell_window_arcs'];
  facingDeg: Spot['orientation_deg'];
  optimalDeg: Spot['optimal_swell_dir'];
  offshoreDeg: Spot['offshore_wind_deg'];
  windDir: number | null | undefined;
  windSpeed: number | null | undefined;
}) {
  const arcs = windowArcs ?? [];
  const arrows = compassArrows(rows, arcs);
  const hasWind = windDir !== null && windDir !== undefined;
  const hasBest = optimalDeg !== null && optimalDeg !== undefined;
  const windText = hasWind ? `${degToCardinal(windDir)} ${fmtMph(windSpeed)}` : '';

  const labels: LabelIn[] = [
    ...arrows.map((a, i) => ({ id: `swell-${i}`, bearing: a.dp, text: a.text })),
    ...(hasWind ? [{ id: 'wind', bearing: windDir, text: windText }] : []),
    ...(hasBest ? [{ id: 'best', bearing: optimalDeg, text: 'best' }] : []),
  ];
  const placed = new Map(layoutLabels(labels).map((l) => [l.id, l]));
  const windClass = hasWind ? windTextClass(windDir, offshoreDeg) : '';

  const said = [
    arcs.length
      ? `Swell window ${arcs.map((a) => `${Math.round(a.min)}° to ${Math.round(a.max)}°`).join(' and ')}`
      : NO_WINDOW_NOTE,
    hasBest ? `best swell from ${degToCardinal(optimalDeg)}` : null,
    ...arrows.map((a) => `${a.label}: ${a.text} from ${degToCardinal(a.dp)}${a.inside ? '' : ', outside the window'}`),
    hasWind ? `wind ${windText}` : null,
  ].filter(Boolean).join('; ');

  function Label({ id, className, fill }: { id: string; className?: string; fill?: string }) {
    const l = placed.get(id);
    if (!l) return null;
    const from = compassPoint(l.bearing, id === 'wind' ? MARK_R + 5 : TAIL_R + 2);
    return (
      <g data-compass-label={id}>
        {l.moved && (
          <line x1={from.x} y1={from.y} x2={l.x} y2={l.y} stroke="#CBD5E1" strokeWidth={1}
            data-compass="leader" />
        )}
        <text x={l.anchor === 'start' ? l.x + 3 : l.x - 3} y={l.y} fontSize={FONT_PX}
          fontWeight={600} textAnchor={l.anchor} dominantBaseline="central" className={className}
          fill={fill ?? 'currentColor'}>{l.text}</text>
      </g>
    );
  }

  return (
    <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} role="img" aria-label={said}
      className="w-full h-auto max-w-[420px]">
      <defs>
        <clipPath id="sp-beach-clip">
          <circle cx={CX} cy={CY} r={BEACH_R} />
        </clipPath>
      </defs>

      {/* The window band: grey all round, blue where open. No window: a faint dashed ring. */}
      {arcs.length ? (
        <>
          <circle cx={CX} cy={CY} r={RING_R} fill="none" stroke={BLOCKED} strokeWidth={RING_W} />
          {arcs.map((a, i) => (
            <path key={i} d={ringArcPath(a)} fill="none" stroke={OPEN} strokeWidth={RING_W}
              data-compass="open" />
          ))}
        </>
      ) : (
        <circle cx={CX} cy={CY} r={RING_R} fill="none" stroke={BLOCKED} strokeWidth={2}
          strokeDasharray="4 4" data-compass="no-window" />
      )}

      {/* The beach: water towards the facing, sand, land behind. */}
      {facingDeg !== null && facingDeg !== undefined ? (
        <g clipPath="url(#sp-beach-clip)" transform={`rotate(${facingDeg} ${CX} ${CY})`}
          data-compass="beach">
          <rect x={CX - BEACH_R} y={CY - BEACH_R} width={2 * BEACH_R} height={BEACH_R} fill={WATER} />
          <rect x={CX - BEACH_R} y={CY} width={2 * BEACH_R} height={BEACH_R} fill={LAND} />
          <rect x={CX - BEACH_R} y={CY - 3} width={2 * BEACH_R} height={7} fill={SAND} />
        </g>
      ) : (
        <circle cx={CX} cy={CY} r={BEACH_R * 0.6} fill={SAND} data-compass="beach" />
      )}

      {/* Best direction: a tick across the band. */}
      {hasBest && (() => {
        const a = compassPoint(optimalDeg, RING_R - RING_W / 2 - 3);
        const b = compassPoint(optimalDeg, RING_R + RING_W / 2 + 3);
        return <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={INK} strokeWidth={2.5}
          data-compass="best" />;
      })()}

      {/* Swells: in from the ring, all ending at the same radius by the beach. */}
      {arrows.map((a, i) => {
        const tail = compassPoint(a.dp, TAIL_R);
        const headLen = 8 + a.width / 2;
        const base = compassPoint(a.dp, HEAD_R + headLen);
        const tip = compassPoint(a.dp, HEAD_R);
        const half = a.width / 2 + 4;
        const l = compassPoint(a.dp - 90, half, base.x, base.y);
        const r = compassPoint(a.dp + 90, half, base.x, base.y);
        return (
          <g key={a.label} opacity={a.muted ? 0.6 : 1} data-compass={a.inside ? 'swell-in' : 'swell-out'}>
            <line x1={tail.x} y1={tail.y} x2={base.x} y2={base.y} stroke={a.colour}
              strokeWidth={a.width} />
            <polygon points={`${tip.x},${tip.y} ${l.x},${l.y} ${r.x},${r.y}`} fill={a.colour} />
          </g>
        );
      })}

      {/* Wind: a marker outside the ring, pointing in, in the Wind tile's colour. */}
      {hasWind && (() => {
        const tip = compassPoint(windDir, MARK_R - 4);
        const l = compassPoint(windDir - 6, MARK_R + 6);
        const r = compassPoint(windDir + 6, MARK_R + 6);
        return (
          <polygon points={`${tip.x},${tip.y} ${l.x},${l.y} ${r.x},${r.y}`} className={windClass}
            fill="currentColor" data-compass="wind" />
        );
      })()}

      {/* Label text in a readable shade of the arrow's family: the lightest arrow colours are too
          pale for text. */}
      {arrows.map((a, i) => <Label key={i} id={`swell-${i}`} fill={a.inside ? '#075985' : '#475569'} />)}
      {hasWind && <Label id="wind" className={windClass} />}
      {hasBest && <Label id="best" fill={INK} />}
    </svg>
  );
}
