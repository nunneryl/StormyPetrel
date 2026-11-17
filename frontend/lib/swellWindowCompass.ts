/**
 * THE SPOT PAGE'S SWELL COMPASS (components/SwellWindowCompass.tsx): the spot's swell window as
 * wedges, its optimal swell direction, the hour's swells as arrows and the hour's wind. Display
 * only: nothing rated reads this.
 *
 * IT CANNOT DISAGREE WITH THE TILES. The arrows are breakdownRows (lib/swellBreakdown.ts), the
 * rows the Swell breakdown prints, for the hour the Conditions tiles show (components/CurrentHour):
 * the same rows, labels and order, the wind sea's "shown as the surf" label from lib/shownPeriod.
 * Nothing is dropped: a swell far smaller than the biggest still gets an arrow, at least
 * MIN_LENGTH_FRAC of the longest and faded; a row with no direction cannot be drawn, so it is
 * listed under the compass instead. The wind arrow is the hour's wind_dir, as the Wind tile reads it.
 *
 * THE WEDGES are each window arc's padded sector, the sector lib/shownPeriod's bearingInArc counts
 * as inside: from min - pad clockwise to max + pad, pad = arcPadDeg. One wedge per arc; an arc of
 * 360 degrees or more is the whole circle. No arcs: no wedge, and the card says so.
 *
 * ANGLES are "from" bearings, 0 = north, clockwise, as the site prints them (degToCardinal). SVG y
 * points down, so north is up: x = cx + r sin(b), y = cy - r cos(b).
 */
import type { BreakdownRow } from './swellBreakdown.ts';
import { arcPadDeg, type WindowArc } from './shownPeriod.ts';

/** The shortest arrow, as a fraction of the longest. Below it an arrow is drawn at this length
 *  and faded. */
export const MIN_LENGTH_FRAC = 0.25;
export const NO_WINDOW_NOTE = 'No swell window set';

export type Point = { x: number; y: number };

function mod360(x: number): number {
  return ((x % 360) + 360) % 360;
}

/** The point at *bearing* degrees and radius *r* from (cx, cy), north up. */
export function compassPoint(bearing: number, r: number, cx: number, cy: number): Point {
  const rad = (bearing * Math.PI) / 180;
  return { x: cx + r * Math.sin(rad), y: cy - r * Math.cos(rad) };
}

const f = (v: number) => Number(v.toFixed(3));

/** One arc's padded sector: its start bearing, its sweep in degrees (clockwise), and whether it
 *  is the whole circle. */
export function arcSector(arc: WindowArc): { start: number; sweep: number; full: boolean } {
  const pad = arcPadDeg(arc);
  const sweep = mod360(arc.max - arc.min) + 2 * pad;
  if (sweep >= 360) return { start: 0, sweep: 360, full: true };
  return { start: mod360(arc.min - pad), sweep, full: false };
}

/** The SVG path of one arc's wedge, from the centre out to radius *r*. A full circle is drawn as
 *  two half-circle arcs (one SVG arc cannot close on itself). */
export function wedgePath(arc: WindowArc, r: number, cx: number, cy: number): string {
  const { start, sweep, full } = arcSector(arc);
  if (full) {
    return `M ${f(cx)} ${f(cy - r)} A ${r} ${r} 0 1 1 ${f(cx)} ${f(cy + r)} ` +
      `A ${r} ${r} 0 1 1 ${f(cx)} ${f(cy - r)} Z`;
  }
  const a = compassPoint(start, r, cx, cy);
  const b = compassPoint(start + sweep, r, cx, cy);
  const large = sweep > 180 ? 1 : 0;
  return `M ${f(cx)} ${f(cy)} L ${f(a.x)} ${f(a.y)} A ${r} ${r} 0 ${large} 1 ${f(b.x)} ${f(b.y)} Z`;
}

export type CompassArrow = {
  label: string;      // the breakdown row's label
  short: string;      // the label at the arrow's tail: P1, P2, P3, or WS for the wind sea
  dp: number;
  hs: number;
  /** Length as a fraction of the longest arrow: hs over the biggest row's, at least
   *  MIN_LENGTH_FRAC. */
  lengthFrac: number;
  /** Drawn at the minimum length because it is smaller than that. */
  faded: boolean;
  windSea: boolean;
  /** Greyed in the breakdown (the wind sea, unless it is shown as the surf). */
  muted: boolean;
};

/** The arrows for the breakdown's rows, in its order, and the rows that have no direction. */
export function compassArrows(rows: readonly BreakdownRow[]):
  { arrows: CompassArrow[]; noDirection: BreakdownRow[] } {
  const maxHs = Math.max(0, ...rows.map((r) => r.hs ?? 0));
  const arrows: CompassArrow[] = [];
  const noDirection: BreakdownRow[] = [];
  for (const r of rows) {
    if (r.dp === null || r.hs === null || !(maxHs > 0)) {
      noDirection.push(r);
      continue;
    }
    const frac = r.hs / maxHs;
    arrows.push({
      label: r.label,
      short: r.windSea ? 'WS' : r.label.slice(0, 2),
      dp: r.dp,
      hs: r.hs,
      lengthFrac: Math.max(MIN_LENGTH_FRAC, frac),
      faded: frac < MIN_LENGTH_FRAC,
      windSea: r.windSea,
      muted: r.muted,
    });
  }
  return { arrows, noDirection };
}
