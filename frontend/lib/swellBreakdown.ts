/**
 * THE SWELL BREAKDOWN'S ROWS: each partition and the wind sea, with ONE shared measure.
 *
 * The breakdown used to give each swell partition its share of the swell's energy and label the
 * wind sea "not surfable". Two things then disagreed on the same hour:
 *   - a lone swell partition read 100% while the Conditions tile said "64% wind sea";
 *   - the wind sea read "not surfable" while lib/shownPeriod.ts (#284) showed it as the surf.
 *
 * Now every row shows its share of the total wave height, on the same measure as the Conditions
 * tile: the wind sea's share IS windSeaShare(row), the hour's chop_ratio (the wind-sea fraction of
 * total Hs), and the tile prints that same number through the same sharePercent; the swell
 * partitions split the rest in proportion to their energy (Hs²). Where chop_ratio is unknown,
 * every row's share is its energy over all rows' energy. Both read the same hour's row too:
 * components/CurrentHour.tsx.
 *
 * The wind sea row is labelled "Wind sea", or "Wind sea · shown as the surf" when the #284 rule
 * shows it instead of the rated swell (windSeaDominates); then it is not greyed out either.
 * Display only: nothing rated reads this.
 */
import { windSeaDominates, type ShownTrainRow, type WindowArc } from './shownPeriod.ts';

export type BreakdownInput = ShownTrainRow & { chop_ratio?: number | null };

export type BreakdownRow = {
  label: string;
  hs: number | null;
  tp: number | null;
  dp: number | null;
  windSea: boolean;
  /** Share of the total wave height, 0 to 1. */
  share: number;
  /** Greyed out: the wind sea, unless it is shown as the surf. */
  muted: boolean;
};

/** Rows below this height (m) are left out, as before. */
export const MIN_ROW_HS = 0.05;

export const WIND_SEA_LABEL = 'Wind sea';
export const WIND_SEA_SHOWN_LABEL = 'Wind sea · shown as the surf';

function num(v: number | null | undefined): number | null {
  return v === null || v === undefined || !Number.isFinite(v) ? null : v;
}

/** The wind sea's share of the hour's wave height (its chop_ratio), or null when unknown or not
 *  a fraction. The Conditions tile's "% wind sea" and the breakdown's wind sea row both print it. */
export function windSeaShare(f: { chop_ratio?: number | null } | null | undefined): number | null {
  const chop = num(f?.chop_ratio);
  return chop !== null && chop >= 0 && chop <= 1 ? chop : null;
}

/** The rows to show for an hour at a spot whose swell window is *arcs*, biggest first. */
export function breakdownRows(
  f: BreakdownInput, arcs: readonly WindowArc[] | null | undefined,
): BreakdownRow[] {
  const shown = windSeaDominates(f, arcs);
  const raw = [
    { label: 'P1 (primary)', hs: f.swell_1_hs, tp: f.swell_1_tp, dp: f.swell_1_dp, windSea: false },
    { label: 'P2 (secondary)', hs: f.swell_2_hs, tp: f.swell_2_tp, dp: f.swell_2_dp, windSea: false },
    { label: 'P3 (tertiary)', hs: f.swell_3_hs, tp: f.swell_3_tp, dp: f.swell_3_dp, windSea: false },
    {
      label: shown ? WIND_SEA_SHOWN_LABEL : WIND_SEA_LABEL,
      hs: f.wind_wave_hs, tp: f.wind_wave_tp, dp: f.wind_wave_dp, windSea: true,
    },
  ]
    .map((r) => ({ ...r, hs: num(r.hs), tp: num(r.tp), dp: num(r.dp) }))
    .filter((r) => r.hs !== null && r.hs > MIN_ROW_HS)
    .sort((a, b) => (b.hs ?? 0) - (a.hs ?? 0));

  const energy = (r: { hs: number | null }) => (r.hs ?? 0) ** 2;
  const chop = windSeaShare(f);
  const swellEnergy = raw.filter((r) => !r.windSea).reduce((a, r) => a + energy(r), 0);
  const allEnergy = raw.reduce((a, r) => a + energy(r), 0);

  return raw.map((r) => {
    let share: number;
    if (chop !== null) {
      share = r.windSea ? chop : swellEnergy > 0 ? (1 - chop) * energy(r) / swellEnergy : 0;
    } else {
      share = allEnergy > 0 ? energy(r) / allEnergy : 0;
    }
    return { ...r, share, muted: r.windSea && !shown };
  });
}

/** A share as the breakdown and the Conditions tile print it: a whole percent. */
export function sharePercent(share: number): string {
  return `${(share * 100).toFixed(0)}%`;
}
