/**
 * THE SWELL TRAIN THE SITE SHOWS for an hour: its period and direction. Display only.
 *
 * The rated train is WW3's dominant SWELL partition (swell_tp / swell_dp); the wind sea is a
 * separate channel. In a nor'easter the swell partitions can hold almost nothing while the wind
 * sea carries the energy: Jacksonville Beach Pier on 2026-10-27 showed a 3 s period from a
 * 0.8 ft "swell" beside a 7.8 ft, 9 s wind sea.
 *
 * So on an hour rated on a WW3 swell partition (swell_source 'ww3' or 'nwps_height_ww3_dir'),
 * the wind sea is shown instead when it is both BIGGER than the rated partition and
 * LONGER-PERIOD than it. "Longer" as well, so a fat short-period chop never replaces a lean
 * groundswell: the shown period can only go up. The rated partition is the swell_N whose tp and
 * dp equal swell_tp and swell_dp to within 0.001; no match, no switch.
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

export const WW3_IDENTITY_SOURCES: readonly string[] = ['ww3', 'nwps_height_ww3_dir'];
const MATCH_TOL = 0.001;

function num(v: number | null | undefined): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
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

/** Is the wind sea bigger AND longer-period than the rated swell partition? */
export function windSeaDominates(row: ShownTrainRow): boolean {
  if (!WW3_IDENTITY_SOURCES.includes(row.swell_source ?? '')) return false;
  const rated = ratedPartition(row);
  const wh = num(row.wind_wave_hs);
  const wt = num(row.wind_wave_tp);
  const wd = num(row.wind_wave_dp);
  if (rated === null || wh === null || wt === null || wd === null) return false;
  return wh > rated[0] && wt > rated[1];
}

/** The period and direction to show for the row, and which train they belong to. */
export function shownTrain(row: ShownTrainRow | null | undefined): ShownTrain {
  if (!row) return { tp: null, dp: null, which: 'swell' };
  if (windSeaDominates(row)) {
    return { tp: num(row.wind_wave_tp), dp: num(row.wind_wave_dp), which: 'wind_sea' };
  }
  return {
    tp: num(row.swell_tp) ?? num(row.tp),
    dp: num(row.swell_dp) ?? num(row.dp),
    which: 'swell',
  };
}
