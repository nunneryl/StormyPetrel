// The swell compass drawn in the Swell breakdown card (components/SwellPartitions.tsx). It takes
// the card's own rows and draws nothing else for the swells, so the two cannot disagree
// (lib/swellWindowCompass.ts). The land is the spot's stored GSHHG path (frontend/data/
// coastlines.json), read server-side by the spot page and passed down as one string. Display only.

import { classifyWind, windQualityClass } from '@/lib/ratings';
import { degToCardinal, fmtMph } from '@/lib/formatting';
import type { BreakdownRow } from '@/lib/swellBreakdown';
import {
  CX, CY, FONT_PX, HEAD_R, LAND_R, MARK_R, NO_WINDOW_NOTE, PIN_R, RING_R, RING_W, STUB_OUTER_R,
  STUB_TIP_R, TAIL_R, VIEW_H,
  VIEW_W, compassArrows, compassPoint, landTransform, layoutLabels, ringArcPath, type LabelIn,
} from '@/lib/swellWindowCompass';
import type { Spot } from '@/lib/types';

const OPEN = '#0EA5E9';
const BLOCKED = '#E2E8F0';
const LAND = '#E8DFC8';      // muted sand; the water is left plain
const SHORE = '#CBBF9E';
const PIN = '#0F172A';
const INK = '#475569';

/** The Wind tile's text colour for this wind (lib/ratings.ts windQualityClass). */
function windTextClass(dir: number, offshore: number | null | undefined): string {
  return windQualityClass(classifyWind(dir, offshore)).split(' ').find((c) => c.startsWith('text-'))
    ?? 'text-text-muted';
}

export function SwellWindowCompass({
  rows,
  windowArcs,
  coastPath,
  optimalDeg,
  offshoreDeg,
  windDir,
  windSpeed,
}: {
  rows: BreakdownRow[];
  windowArcs: Spot['swell_window_arcs'];
  /** The land within 25 km of the spot, radius 100 units, north up; '' or null for none. */
  coastPath: string | null;
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
      className="w-full h-auto">
      <defs>
        <clipPath id="sp-land-clip">
          <circle cx={CX} cy={CY} r={LAND_R} />
        </clipPath>
      </defs>

      {/* The land within 25 km, inside the ring, north up. */}
      {coastPath ? (
        <g clipPath="url(#sp-land-clip)">
          <path d={coastPath} transform={landTransform()} fill={LAND} stroke={SHORE}
            strokeWidth={1} vectorEffect="non-scaling-stroke" data-compass="land" />
        </g>
      ) : null}

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

      {/* The spot. */}
      <circle cx={CX} cy={CY} r={PIN_R} fill={PIN} stroke="#FFFFFF" strokeWidth={1.5}
        data-compass="pin" />

      {/* Best direction: a tick across the band. */}
      {hasBest && (() => {
        const a = compassPoint(optimalDeg, RING_R - RING_W / 2 - 3);
        const b = compassPoint(optimalDeg, RING_R + RING_W / 2 + 3);
        return <line x1={a.x} y1={a.y} x2={b.x} y2={b.y} stroke={INK} strokeWidth={2.5}
          data-compass="best" />;
      })()}

      {/* Swells that can reach the spot: in from the ring, all ending at the same radius by the
          pin. Swells from outside the window: a short stub across the band, never over the land. */}
      {arrows.map((a, i) => {
        const tail = compassPoint(a.dp, a.stub ? STUB_OUTER_R : TAIL_R);
        const headLen = (a.stub ? 5 : 8) + a.width / 2;
        const tipR = a.stub ? STUB_TIP_R : HEAD_R;
        const base = compassPoint(a.dp, tipR + headLen);
        const tip = compassPoint(a.dp, tipR);
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

      {/* Label text a shade darker than its mark, to read as text. */}
      {arrows.map((a, i) => <Label key={i} id={`swell-${i}`} fill={a.inside ? '#075985' : '#475569'} />)}
      {hasWind && <Label id="wind" className={windClass} />}
      {hasBest && <Label id="best" fill={INK} />}
    </svg>
  );
}
