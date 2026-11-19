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
 *   - One mark per breakdown row. The two largest by the breakdown's share (the nearshore split,
 *     LABELLED_ARROWS) are labelled with height and period ("8.8 ft · 14 s") and drawn at their
 *     width; the rest are thin and unlabelled, their numbers in the rows beside. A swell
 *     from inside the window (it can reach the spot) is a blue arrow in from the ring to the same
 *     inner radius by the pin, or stops just short of the first land on its straight way in
 *     (firstLandHit, arrowTipR: Kanaha's ENE wind sea stops at Maui's coast). A swell from outside
 *     it is a short grey stub at the ring: it never
 *     crosses the land to the spot. Width is on one feet scale for every spot (arrowWidth: 0.8
 *     units a foot, 2 to 12), so a flat day looks flat.
 *   - The best direction: a notch on the ring at the optimal swell direction, no text; the line
 *     under the compass says it in words ("Best swell N · Window NW–E", compassCaption).
 *   - The wind: a marker outside the ring at its direction, "S 7 mph", in the Wind tile's colour.
 *
 * IT CANNOT DISAGREE WITH THE BREAKDOWN. The arrows are breakdownRows (lib/swellBreakdown.ts), the
 * rows the card prints beside the compass, for the hour the Conditions tiles show
 * (components/CurrentHour). One colour rule for marks and row chips (rowStyles): blue inside the
 * window, grey outside. A row with no direction gets no mark and a hollow chip.
 *
 * LABELS NEVER OVERLAP. layoutLabels puts each label just outside the ring at its bearing, on the
 * right of the circle for bearings 0-179 and the left for 180-359, then pushes labels on the same
 * side apart vertically and slides each back to the circle's edge at its new height. A label that
 * moved gets a leader line.
 *
 * ANGLES are "from" bearings, 0 = north, clockwise. SVG y points down, so north is up:
 * x = cx + r sin(b), y = cy - r cos(b).
 */
import { degToCardinal } from './formatting.ts';
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
/** A swell from outside the window is a stub across the band only: from just outside it to its
 *  inner edge, never into the land inside the ring. */
export const STUB_OUTER_R = TAIL_R + 6;
export const STUB_TIP_R = RING_R - RING_W / 2;
export const MARK_R = RING_R + RING_W / 2 + 9;  // the wind marker
export const LABEL_R = RING_R + 28;
export const LABEL_H = 14;
export const LABEL_GAP = 2;
export const FONT_PX = 12;
/** Width per character at FONT_PX, generous so the boxes over-cover the text. */
export const CHAR_W = 7;

/** Arrow width on one scale for every spot: WIDTH_PER_FT units a foot of height, never under
 *  MIN_ARROW_W (a foot or two reads as a thin line) nor over MAX_ARROW_W (15 ft and up). */
export const WIDTH_PER_FT = 0.8;
export const MAX_ARROW_W = 12;
export const MIN_ARROW_W = 2;

export const NO_WINDOW_NOTE = 'No swell window set';
/** Arrows labelled and drawn at their width: the largest by share. The rest are THIN_ARROW_W. */
export const LABELLED_ARROWS = 2;
export const THIN_ARROW_W = MIN_ARROW_W;

/** Mark and chip colours: blue can reach the spot (inside the window), grey cannot. */
export const INSIDE_COLOUR = '#0284C7';
export const OUTSIDE_COLOUR = '#94A3B8';

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

/** The rings of a stored land path ("M-12 40l3-4 5 0...z", pipeline/coastline_paths.py) as
 *  absolute points in path units. */
export function parseLandPath(d: string): [number, number][][] {
  const rings: [number, number][][] = [];
  const tokens = d.match(/[Mlz]|-?\d+(?:\.\d+)?/g) ?? [];
  let ring: [number, number][] = [];
  let cmd = '';
  let x = 0;
  let y = 0;
  for (let i = 0; i < tokens.length; i++) {
    const t = tokens[i];
    if (t === 'M' || t === 'l') { cmd = t; continue; }
    if (t === 'z') { if (ring.length) rings.push(ring); ring = []; continue; }
    const nx = Number(t);
    const ny = Number(tokens[++i]);
    if (cmd === 'M') { x = nx; y = ny; } else { x += nx; y += ny; }
    ring.push([x, y]);
  }
  if (ring.length) rings.push(ring);
  return rings;
}

function insideRings(px: number, py: number, rings: [number, number][][]): boolean {
  let inside = false;                                   // even-odd, so holes count out
  for (const ring of rings) {
    for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
      const [xi, yi] = ring[i];
      const [xj, yj] = ring[j];
      if ((yi > py) !== (yj > py) && px < ((xj - xi) * (py - yi)) / (yj - yi) + xi) inside = !inside;
    }
  }
  return inside;
}

/** Where a swell from *bearing*, travelling in from the edge of the land drawing (radius
 *  PATH_UNITS) toward the spot, first meets land: its distance from the spot in path units, or
 *  null when it reaches the spot over water. PATH_UNITS when it starts on land. */
export function firstLandHit(bearing: number, rings: [number, number][][]): number | null {
  const rad = (bearing * Math.PI) / 180;
  const ux = Math.sin(rad);
  const uy = -Math.cos(rad);                            // SVG y points down: north is -y
  if (insideRings(ux * PATH_UNITS, uy * PATH_UNITS, rings)) return PATH_UNITS;
  let best: number | null = null;
  for (const ring of rings) {
    for (let i = 0; i < ring.length; i++) {
      const [ax, ay] = ring[i];
      const [bx, by] = ring[(i + 1) % ring.length];
      const ex = bx - ax;
      const ey = by - ay;
      const den = ux * ey - uy * ex;
      if (Math.abs(den) < 1e-12) continue;              // parallel
      const t = (ax * ey - ay * ex) / den;              // along the ray from the spot
      const s = (ax * uy - ay * ux) / den;              // along the edge
      if (s >= 0 && s <= 1 && t > 0 && t <= PATH_UNITS && (best === null || t > best)) best = t;
    }
  }
  return best;
}

/** How far from the centre (drawing units) an arrow's tip stops: by the pin over open water,
 *  just short of the first land on its way in, at the band's inner edge for a stub or for a
 *  swell whose way in starts on land. Land within HEAD_R of the pin (the spot's own beach) is
 *  where the arrow stops anyway. */
export function arrowTipR(stub: boolean, landHit: number | null): number {
  if (stub) return STUB_TIP_R;
  if (landHit === null) return HEAD_R;
  return Math.min(STUB_TIP_R, Math.max(HEAD_R, (landHit * LAND_R) / PATH_UNITS + 1));
}

/** Is a direction inside the window? With no window set nothing is outside it. */
export function insideWindow(dp: number, arcs: readonly WindowArc[] | null | undefined): boolean {
  return !arcs || arcs.length === 0 || outsideWindowDeg(dp, arcs) === 0;
}

/** Arrow width from a height in metres, on the one feet scale: WIDTH_PER_FT a foot, clamped to
 *  MIN_ARROW_W..MAX_ARROW_W. */
export function arrowWidth(hsM: number): number {
  const w = WIDTH_PER_FT * hsM * 3.28084;
  return Math.min(MAX_ARROW_W, Math.max(MIN_ARROW_W, w));
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

/** Each breakdown row's colour: blue inside the window, grey outside; no direction, none. */
export function rowStyles(rows: readonly BreakdownRow[],
  arcs: readonly WindowArc[] | null | undefined): RowStyle[] {
  return rows.map((r) => {
    if (r.dp === null) return { colour: null, inside: null };
    const inside = insideWindow(r.dp, arcs);
    return { colour: inside ? INSIDE_COLOUR : OUTSIDE_COLOUR, inside };
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
  /** Outside the window: drawn as a short stub at the ring, not an arrow to the spot. */
  stub: boolean;
  /** Where the tip stops, from the centre in drawing units (arrowTipR). */
  tipR: number;
  muted: boolean;     // greyed in the breakdown (the wind sea, unless shown as the surf)
  /** The row's share in the breakdown (its percent), 0 to 1. */
  share: number;
  /** One of the LABELLED_ARROWS largest by share: labelled, and drawn at its width. */
  labelled: boolean;
};

/** The arrows for the breakdown's rows, in its order. Every row with a direction has one. */
export function compassArrows(rows: readonly BreakdownRow[],
  arcs: readonly WindowArc[] | null | undefined, coastPath: string | null = null): CompassArrow[] {
  const styles = rowStyles(rows, arcs);
  const rings = coastPath ? parseLandPath(coastPath) : [];
  const out: CompassArrow[] = [];
  rows.forEach((r, k) => {
    const s = styles[k];
    if (r.dp === null || r.hs === null || s.colour === null) return;
    out.push({
      label: r.label, text: arrowLabel(r.hs, r.tp), dp: r.dp, hs: r.hs,
      width: arrowWidth(r.hs), colour: s.colour, inside: s.inside === true, stub: s.inside !== true,
      tipR: arrowTipR(s.inside !== true, s.inside === true && rings.length ? firstLandHit(r.dp, rings) : null),
      muted: r.muted, share: r.share, labelled: false,
    });
  });
  // The largest by share, ties to the earlier row (the bigger height: rows come biggest first).
  const top = out.map((a, i) => i)
    .sort((i, j) => out[j].share - out[i].share || i - j)
    .slice(0, LABELLED_ARROWS);
  return out.map((a, i) => top.includes(i) ? { ...a, labelled: true } : { ...a, width: THIN_ARROW_W });
}

/** The window's arcs as compass points from their stored bounds: "NW–E", "WSW–NW, S–SW";
 *  "all round" for a whole circle; null with no window. */
export function windowText(arcs: readonly WindowArc[] | null | undefined): string | null {
  if (!arcs || arcs.length === 0) return null;
  if (arcs.some((a) => (a.span ?? 0) >= 360)) return 'all round';
  return arcs.map((a) => `${degToCardinal(a.min)}–${degToCardinal(a.max)}`).join(', ');
}

/** The line under the compass: "Best swell N · Window NW–E", either half alone, or null. */
export function compassCaption(optimalDeg: number | null | undefined,
  arcs: readonly WindowArc[] | null | undefined): string | null {
  const best = optimalDeg === null || optimalDeg === undefined || !Number.isFinite(optimalDeg)
    ? null : `Best swell ${degToCardinal(optimalDeg)}`;
  const w = windowText(arcs);
  const parts = [best, w === null ? null : `Window ${w}`].filter((x): x is string => x !== null);
  return parts.length ? parts.join(' · ') : null;
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
