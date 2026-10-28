/**
 * THE SWELL TRAIN THE SITE SHOWS for an hour: its period and direction. Display only.
 *
 * The rated train is WW3's dominant SWELL partition (swell_tp / swell_dp); the wind sea is a
 * separate channel. In a nor'easter the swell partitions can hold almost nothing while the wind
 * sea carries the energy: Jacksonville Beach Pier on 2026-10-27 showed a 3 s period from a
 * 0.8 ft "swell" beside a 7.8 ft, 9 s wind sea.
 *
 * So on an hour rated on a WW3 swell partition (swell_source 'ww3' or 'nwps_height_ww3_dir'),
 * the wind sea is shown instead when either
 *   LONGER    it is bigger than the rated partition AND at least MIN_LONGER_S (0.5 s) longer;
 *   DOMINANT  its height is at least DOMINANCE_K (4) times the rated partition's, whatever its
 *             period — the rated "swell" is then a scrap, and the shown period can go down.
 * A lean groundswell under a moderate chop (2x) stays the swell. The rated partition is the
 * swell_N whose tp and dp equal swell_tp and swell_dp to within 0.001; no match, no switch.
 *
 * Neither case switches when the wind sea comes from BLOCKED_OUTSIDE_DEG (90) or more outside the
 * spot's swell window: the rating's blocked rung, measured as the pipeline measures it
 * (interpret._min_offset_from_arcs, from each arc's padded edge). A spot with no window arcs
 * switches as before.
 *
 * The stars are untouched: they stay on the rated partition. pipeline/forecast/shown_period.py is
 * the same rule (it also sets the period the surf height is shoaled at), and both are held to
 * lib/shownPeriod.cases.json.
 */

export type ShownTrainRow = {
  swell_source?: string | null;
  swell_tp?: number | null;
  swell_dp?: number | null;
  tp?: number | null;
  dp?: number | null;
  swell_1_hs?: number | null; swell_1_tp?: number | null; swell_1_dp?: number | null;
  swell_2_hs?: number | null; swell_2_tp?: number | null; swell_2_dp?: number | null;
  swell_3_hs?: number | null; swell_3_tp?: number | null; swell_3_dp?: number | null;
  wind_wave_hs?: number | null; wind_wave_tp?: number | null; wind_wave_dp?: number | null;
};

export type ShownTrain = { tp: number | null; dp: number | null; which: 'swell' | 'wind_sea' };

/** A spot's swell-window arc, as Spot.swell_window_arcs stores it. */
export type WindowArc = { min: number; max: number; span?: number | null };

export const WW3_IDENTITY_SOURCES: readonly string[] = ['ww3', 'nwps_height_ww3_dir'];
const MATCH_TOL = 0.001;
/** The rule's constants: the same values as pipeline/forecast/shown_period.py, whose test
 *  reads them from that file. */
export const MIN_LONGER_S = 0.5;
export const DOMINANCE_K = 4;
export const BLOCKED_OUTSIDE_DEG = 90;

function num(v: number | null | undefined): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

/** Half-step pad between an arc's stored bounds and its true sector edges: interpret.arc_pad_deg,
 *  (span - ((max - min) mod 360)) / 2, and 0 for an arc with no span. */
export function arcPadDeg(arc: WindowArc): number {
  const span = num(arc.span ?? null);
  if (span === null) return 0;
  return (span - mod360(arc.max - arc.min)) / 2;
}

function mod360(x: number): number {
  return ((x % 360) + 360) % 360;
}

function angleOff(a: number, b: number): number {
  const d = mod360(Math.abs(a - b));
  return Math.min(d, 360 - d);
}

/** Is bearing dp inside one arc's padded sector (interpret.bearing_in_arc)? */
function bearingInArc(dp: number, arc: WindowArc): boolean {
  const pad = arcPadDeg(arc);
  const total = mod360(arc.max - arc.min) + 2 * pad;
  if (total >= 360) return true;
  return mod360(dp - arc.min + pad) <= total;
}

/** Degrees bearing dp lies outside the window, from the nearest padded edge; 0 inside
 *  (interpret._min_offset_from_arcs). */
export function outsideWindowDeg(dp: number, arcs: readonly WindowArc[]): number {
  if (arcs.some((a) => bearingInArc(dp, a))) return 0;
  let off = 360;
  for (const a of arcs) {
    const pad = arcPadDeg(a);
    off = Math.min(off, angleOff(dp, a.min - pad), angleOff(dp, a.max + pad));
  }
  return off;
}

/** Does bearing dp come from BLOCKED_OUTSIDE_DEG or more outside the window? false with no arcs. */
export function windSeaBlocked(dp: number, arcs: readonly WindowArc[] | null | undefined): boolean {
  if (!arcs || arcs.length === 0) return false;
  return outsideWindowDeg(dp, arcs) >= BLOCKED_OUTSIDE_DEG;
}

/** [hs, tp, dp] of the swell partition the hour was rated on, or null. */
export function ratedPartition(row: ShownTrainRow): [number, number, number] | null {
  const tp = num(row.swell_tp);
  const dp = num(row.swell_dp);
  if (tp === null || dp === null) return null;
  const parts: [number | null, number | null, number | null][] = [
    [num(row.swell_1_hs), num(row.swell_1_tp), num(row.swell_1_dp)],
    [num(row.swell_2_hs), num(row.swell_2_tp), num(row.swell_2_dp)],
    [num(row.swell_3_hs), num(row.swell_3_tp), num(row.swell_3_dp)],
  ];
  for (const [h, t, d] of parts) {
    if (h === null || t === null || d === null) continue;
    if (Math.abs(t - tp) <= MATCH_TOL && Math.abs(d - dp) <= MATCH_TOL) return [h, t, d];
  }
  return null;
}

/** 'longer' or 'dominant' when the wind sea is shown ('longer' first when both hold), else null.
 *  arcs: the spot's swell_window_arcs. */
export function switchCase(
  row: ShownTrainRow, arcs: readonly WindowArc[] | null | undefined,
): 'longer' | 'dominant' | null {
  if (!WW3_IDENTITY_SOURCES.includes(row.swell_source ?? '')) return null;
  const rated = ratedPartition(row);
  const wh = num(row.wind_wave_hs);
  const wt = num(row.wind_wave_tp);
  const wd = num(row.wind_wave_dp);
  if (rated === null || wh === null || wt === null || wd === null) return null;
  if (windSeaBlocked(wd, arcs)) return null;
  if (wh > rated[0] && wt - rated[1] >= MIN_LONGER_S) return 'longer';
  if (wh >= DOMINANCE_K * rated[0]) return 'dominant';
  return null;
}

/** Is the wind sea shown for the row? */
export function windSeaDominates(row: ShownTrainRow, arcs: readonly WindowArc[] | null | undefined): boolean {
  return switchCase(row, arcs) !== null;
}

/** The period and direction to show for the row at a spot whose window is arcs, and which train
 *  they belong to. */
export function shownTrain(
  row: ShownTrainRow | null | undefined, arcs: readonly WindowArc[] | null | undefined,
): ShownTrain {
  if (!row) return { tp: null, dp: null, which: 'swell' };
  if (windSeaDominates(row, arcs)) {
    return { tp: num(row.wind_wave_tp), dp: num(row.wind_wave_dp), which: 'wind_sea' };
  }
  return {
    tp: num(row.swell_tp) ?? num(row.tp),
    dp: num(row.swell_dp) ?? num(row.dp),
    which: 'swell',
  };
}
