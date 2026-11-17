/**
 * THE SPOT PAGE'S SWELL COMPASS, drawn in the Swell breakdown card (components/SwellPartitions.tsx,
 * components/SwellWindowCompass.tsx). Display only: nothing rated reads this.
 *
 * WHAT IT DRAWS
 *   - The land within 25 km, inside the ring, from the GSHHG shoreline the window ray-cast reads
 *     (frontend/data/coastlines.json, written by pipeline/coastline_paths.py), with a pin at the
 *     spot. North up, so the blue stretch of the band faces the open water.
 *   - The swell window as a band on the ring: blue where open, light grey where blocked. Each open
 *     stretch is a window arc's padded sector, the sector lib/shownPeriod counts as inside.
 *   - One arrow per breakdown row, in from the ring to the same inner radius by the beach. Width
 *     follows height (at least MIN_ARROW_W); blue inside the window, grey outside; labelled with its
 *     height and period ("8.8 ft · 14 s").
 *   - "best": a tick on the ring at the optimal swell direction.
 *   - The wind: a marker outside the ring at its direction, "S 7 mph", in the Wind tile's colour.
 *
 * IT CANNOT DISAGREE WITH THE BREAKDOWN. The arrows are breakdownRows (lib/swellBreakdown.ts), the
 * rows the card prints beside the compass, for the hour the Conditions tiles show
 * (components/CurrentHour). Each row's chip is its arrow's colour (rowStyles). A row with no
 * direction gets no arrow and a hollow chip.
 *
 * LABELS NEVER OVERLAP. layoutLabels puts each label just outside the ring at its bearing, on the
 * right of the circle for bearings 0-179 and the left for 180-359, then pushes labels on the same
 * side apart vertically and slides each back to the circle's edge at its new height. A label that
 * moved gets a leader line.
 *
 * ANGLES are "from" bearings, 0 = north, clockwise. SVG y points down, so north is up:
 * x = cx + r sin(b), y = cy - r cos(b).
 */
import type { BreakdownRow } from './swellBreakdown.ts';
import { arcPadDeg, outsideWindowDeg, type WindowArc } from './shownPeriod.ts';

// Geometry, in the SVG's own units.
export const VIEW_W = 420;
export const VIEW_H = 240;
export const CX = VIEW_W / 2;
export const CY = VIEW_H / 2;
export const RING_R = 82;          // the window band's centre line
export const RING_W = 10;
/** The land drawing's radius: just inside the band. The stored paths have radius PATH_UNITS
 *  (pipeline/coastline_paths.py UNITS, for RADIUS_KM = 25 km). */
export const LAND_R = RING_R - RING_W / 2 - 1;
export const PATH_UNITS = 100;
export const LAND_KM = 25;
export const PIN_R = 4;
export const HEAD_R = PIN_R + 8;   // every arrow ends here, by the pin
export const TAIL_R = RING_R + RING_W / 2 + 2;
export const MARK_R = RING_R + RING_W / 2 + 9;  // the wind marker
export const LABEL_R = RING_R + 28;
export const LABEL_H = 14;
export const LABEL_GAP = 2;
export const FONT_PX = 12;
/** Width per character at FONT_PX, generous so the boxes over-cover the text. */
export const CHAR_W = 7;

export const MAX_ARROW_W = 10;
export const MIN_ARROW_W = 3;
export const NO_WINDOW_NOTE = 'No swell window set';

/** Arrow and chip colours: blue shades inside the window, greys outside, by order. */
export const INSIDE_COLOURS = ['#075985', '#0284C7', '#38BDF8', '#7DD3FC'];
export const OUTSIDE_COLOURS = ['#475569', '#94A3B8', '#B6C2CF', '#CBD5E1'];

export type Point = { x: number; y: number };

function mod360(x: number): number {
  return ((x % 360) + 360) % 360;
}

/** The point at *bearing* degrees and radius *r* from (cx, cy), north up. */
export function compassPoint(bearing: number, r: number, cx = CX, cy = CY): Point {
  const rad = (bearing * Math.PI) / 180;
  return { x: cx + r * Math.sin(rad), y: cy - r * Math.cos(rad) };
}

const f = (v: number) => Number(v.toFixed(3));

/** One arc's padded sector: start bearing, clockwise sweep, and whether it is the whole circle. */
export function arcSector(arc: WindowArc): { start: number; sweep: number; full: boolean } {
  const pad = arcPadDeg(arc);
  const sweep = mod360(arc.max - arc.min) + 2 * pad;
  if (sweep >= 360) return { start: 0, sweep: 360, full: true };
  return { start: mod360(arc.min - pad), sweep, full: false };
}

/** The SVG path along the ring (radius *r*) for one arc's open stretch: no line to the centre. */
export function ringArcPath(arc: WindowArc, r = RING_R, cx = CX, cy = CY): string {
  const { start, sweep, full } = arcSector(arc);
  if (full) {
    return `M ${f(cx)} ${f(cy - r)} A ${r} ${r} 0 1 1 ${f(cx)} ${f(cy + r)} ` +
      `A ${r} ${r} 0 1 1 ${f(cx)} ${f(cy - r)}`;
  }
  const a = compassPoint(start, r, cx, cy);
  const b = compassPoint(start + sweep, r, cx, cy);
  return `M ${f(a.x)} ${f(a.y)} A ${r} ${r} 0 ${sweep > 180 ? 1 : 0} 1 ${f(b.x)} ${f(b.y)}`;
}

/** The SVG transform that puts a stored land path (radius PATH_UNITS about the spot) inside the
 *  ring, centred on the pin. */
export function landTransform(): string {
  return `translate(${CX} ${CY}) scale(${LAND_R / PATH_UNITS})`;
}

/** Is a direction inside the window? With no window set nothing is outside it. */
export function insideWindow(dp: number, arcs: readonly WindowArc[] | null | undefined): boolean {
  return !arcs || arcs.length === 0 || outsideWindowDeg(dp, arcs) === 0;
}

/** Arrow width: MAX_ARROW_W for the biggest row, in proportion below it, never under MIN_ARROW_W. */
export function arrowWidth(hs: number, maxHs: number): number {
  return maxHs > 0 ? Math.max(MIN_ARROW_W, (MAX_ARROW_W * hs) / maxHs) : MIN_ARROW_W;
}

/** "8.8 ft · 14 s" from metres and seconds; "8.8 ft" with no period. */
export function arrowLabel(hsM: number, tp: number | null): string {
  const ft = (hsM * 3.28084).toFixed(1);
  return tp === null ? `${ft} ft` : `${ft} ft · ${Math.round(tp)} s`;
}

export type RowStyle = {
  /** The chip's and arrow's colour; null for a row with no direction (a hollow chip). */
  colour: string | null;
  inside: boolean | null;
};

/** Each breakdown row's colour, in the breakdown's order: inside the window the next blue, outside
 *  the next grey; no direction, none. */
export function rowStyles(rows: readonly BreakdownRow[],
  arcs: readonly WindowArc[] | null | undefined): RowStyle[] {
  let i = 0;
  let o = 0;
  return rows.map((r) => {
    if (r.dp === null) return { colour: null, inside: null };
    const inside = insideWindow(r.dp, arcs);
    const colour = inside
      ? INSIDE_COLOURS[Math.min(i++, INSIDE_COLOURS.length - 1)]
      : OUTSIDE_COLOURS[Math.min(o++, OUTSIDE_COLOURS.length - 1)];
    return { colour, inside };
  });
}

export type CompassArrow = {
  label: string;      // the breakdown row's label (for the accessible description)
  text: string;       // drawn on the compass: "8.8 ft · 14 s"
  dp: number;
  hs: number;
  width: number;
  colour: string;
  inside: boolean;
  muted: boolean;     // greyed in the breakdown (the wind sea, unless shown as the surf)
};

/** The arrows for the breakdown's rows, in its order. Every row with a direction has one. */
export function compassArrows(rows: readonly BreakdownRow[],
  arcs: readonly WindowArc[] | null | undefined): CompassArrow[] {
  const maxHs = Math.max(0, ...rows.map((r) => r.hs ?? 0));
  const styles = rowStyles(rows, arcs);
  const out: CompassArrow[] = [];
  rows.forEach((r, k) => {
    const s = styles[k];
    if (r.dp === null || r.hs === null || s.colour === null) return;
    out.push({
      label: r.label, text: arrowLabel(r.hs, r.tp), dp: r.dp, hs: r.hs,
      width: arrowWidth(r.hs, maxHs), colour: s.colour, inside: s.inside === true, muted: r.muted,
    });
  });
  return out;
}

// --------------------------------------------------------------------------- //
// Labels                                                                       //
// --------------------------------------------------------------------------- //

export type LabelIn = { id: string; bearing: number; text: string };
export type LabelOut = LabelIn & {
  x: number; y: number;              // the text's anchor point (its vertical middle)
  anchor: 'start' | 'end';
  box: { x0: number; x1: number; y0: number; y1: number };
  moved: boolean;                    // pushed off its bearing: draw a leader line
};

export function labelWidth(text: string): number {
  return text.length * CHAR_W;
}

/** Places every label just outside the ring with none overlapping (see the file header). */
export function layoutLabels(items: readonly LabelIn[], r = LABEL_R): LabelOut[] {
  const step = LABEL_H + LABEL_GAP;
  const top = LABEL_H / 2;
  const bottom = VIEW_H - LABEL_H / 2;
  const out: LabelOut[] = [];
  for (const right of [true, false]) {
    const side = items
      .filter((it) => (mod360(it.bearing) < 180) === right)
      .map((it) => ({ it, ideal: compassPoint(it.bearing, r).y }))
      .sort((a, b) => a.ideal - b.ideal || a.it.id.localeCompare(b.it.id));
    const ys = side.map((s) => Math.min(bottom, Math.max(top, s.ideal)));
    for (let k = 1; k < ys.length; k++) ys[k] = Math.max(ys[k], ys[k - 1] + step);
    if (ys.length && ys[ys.length - 1] > bottom) {
      ys[ys.length - 1] = bottom;
      for (let k = ys.length - 2; k >= 0; k--) ys[k] = Math.min(ys[k], ys[k + 1] - step);
    }
    side.forEach((s, k) => {
      const y = ys[k];
      const dy = y - CY;
      const half = Math.sqrt(Math.max(0, r * r - dy * dy));
      const x = right ? CX + half : CX - half;
      const w = labelWidth(s.it.text);
      out.push({
        ...s.it, x, y, anchor: right ? 'start' : 'end',
        box: right ? { x0: x, x1: x + w, y0: y - LABEL_H / 2, y1: y + LABEL_H / 2 }
          : { x0: x - w, x1: x, y0: y - LABEL_H / 2, y1: y + LABEL_H / 2 },
        moved: Math.abs(y - s.ideal) > 1,
      });
    });
  }
  return out;
}

/** Do two label boxes overlap (touching edges do not)? */
export function boxesOverlap(a: LabelOut['box'], b: LabelOut['box']): boolean {
  return a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1;
}
