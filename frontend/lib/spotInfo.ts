/**
 * The Spot info panel's rows, as data.
 *
 * WHY THIS IS A FUNCTION AND NOT JSX. The panel used to be four hardcoded <Row>
 * elements inside the spot page's server component, which meant its contents could
 * only be verified by reading them. Pulling the rows out gives the one thing that
 * matters here a test: WHICH ROWS EXIST. The shape mirrors OptimalConditions.tsx,
 * which already builds a {label, value}[] and maps over it.
 *
 * NO HAZARDS ROW — THIS IS THE POINT OF THE MODULE.
 * `hazards` is populated on 173 of 648 spots and empty on 475, and NONE of it has
 * been reviewed by a human: review_status is 'auto' on every row in the table.
 * verify_spots.py's prompt explicitly tells the model not to research these fields
 * ("don't search for peripheral details you can reasonably infer (break_type,
 * hazards, crowd_factor)"), so the 173 are inference from a name and a coordinate.
 * The geography confirms it — California 135, Florida 31, Delaware 4, Hawaii 2,
 * New Jersey 1, and zero in every other state, including 0 of 39 in North Carolina,
 * 0 of 30 in New York, 0 of 27 in Rhode Island and 0 of 24 in Puerto Rico. That is
 * not a map of where hazards are; it is a map of where the model felt chatty.
 *
 * Both renderings were unsafe. An em dash in a Hazards row reads as "no hazards
 * here" — a safety claim nothing in this system can support — and a populated row
 * is an unreviewed guess presented as fact. So the row does not render at all,
 * for any spot, whatever the array holds.
 *
 * DO NOT ADD IT BACK until a human has reviewed the data and there is a column
 * that records that review. `hazards` is accepted in the input type below and
 * deliberately never read, so the decision is visible exactly where someone would
 * otherwise reach for it.
 */

import { degToCardinal } from './formatting.ts';

export type SpotInfoRow = { label: string; value: string };

/** The fields these two panels read. Structural on purpose, and the only import is
 *  formatting.ts (itself import-free), so this module runs under
 *  `node --experimental-strip-types` with no packages installed. */
export type SpotInfoInput = {
  break_type?: string | null;
  tide_preference?: string | null;
  crowd_factor?: string | null;
  optimal_swell_dir?: number | null;
  offshore_wind_deg?: number | null;
  /** Received and NOT rendered — see the module comment. */
  hazards?: string[] | null;
};

/** Absent, null, empty, or whitespace-only — all the ways a text column says "nothing
 *  here". The DB carries both NULL and '' for these fields, and a whitespace-only value
 *  renders as a blank gap, which is the same wrong claim with worse typography. */
function isBlank(v: string | null | undefined): boolean {
  return v === null || v === undefined || v.trim() === '';
}

/**
 * The Spot info panel's rows, in display order. Never includes Hazards.
 *
 * NO CROWD ROW WHEN crowd_factor IS EMPTY — 475 of 648 spots.
 * "Crowd —" is not a blank, it is an assertion: Crowd is a quantity, so an em dash
 * reads as the low end of it — "not crowded" — about a spot nothing in this system has
 * ever checked. Same defect as the Hazards row, on nearly three times as many spots.
 * An absent row says "we don't know"; an em dash says "none". Drop the row.
 *
 * Break and Tide preference KEEP their em dashes, deliberately. Every spot has a break
 * type, so a missing one reads as a gap rather than a claim. Tide is the ambiguous one
 * — "no preference" is a real answer — and it is handled by making the two cards agree
 * on '—' rather than by one card saying 'any'; see optimalConditionsRows.
 */
export function spotInfoRows(spot: SpotInfoInput): SpotInfoRow[] {
  const rows: SpotInfoRow[] = [
    { label: 'Break', value: spot.break_type ?? '—' },
    { label: 'Tide preference', value: spot.tide_preference ?? '—' },
  ];
  if (!isBlank(spot.crowd_factor)) {
    rows.push({ label: 'Crowd', value: spot.crowd_factor as string });
  }
  return rows;
}

/**
 * The Optimal conditions card's rows, in display order.
 *
 * ONE NULL, ONE RENDERING. This card used to render `tide_preference ?? 'any'` while
 * the Spot info panel beside it rendered `?? '—'`, so the same null read two different
 * ways in two cards eighteen inches apart. 'any' is the wrong direction to resolve it:
 * "works at any tide" is a real, specific answer, and asserting it from a null claims
 * something we have not checked. '—' is the honest one, and it now matches its neighbour.
 *
 * NO PERIOD ROW. This card rendered `{ label: 'Period', value: '10s+' }` — a hardcoded
 * literal, identical on all 648 spots. Steamer Lane and Point Judith are different
 * breaks in different oceans and do not share an optimal period, but both displayed
 * "10s+" as though it had been derived for them. There is nothing to derive it FROM:
 * the spots table has no period column in any migration, the Spot type has no period
 * field, and every tp in the schema (tp, swell_tp, swell_1_tp .. wind_wave_tp) belongs
 * to forecasts, which is per-hour weather, not a property of the break. interpret's
 * period_factor(tp, source) and period_quality(tp_s) take no spot argument at all — the
 * curve is global. So the row was not a simplification of a real per-spot value; no
 * such value exists anywhere in the system. It is removed rather than guessed at.
 */
export function optimalConditionsRows(spot: SpotInfoInput): SpotInfoRow[] {
  return [
    {
      label: 'Swell',
      value:
        spot.optimal_swell_dir !== null && spot.optimal_swell_dir !== undefined
          ? `${degToCardinal(spot.optimal_swell_dir)} ${Math.round(spot.optimal_swell_dir)}°`
          : '—',
    },
    {
      label: 'Wind',
      value:
        spot.offshore_wind_deg !== null && spot.offshore_wind_deg !== undefined
          ? `${degToCardinal(spot.offshore_wind_deg)} offshore`
          : 'offshore',
    },
    { label: 'Tide', value: spot.tide_preference ?? '—' },
    { label: 'Break', value: spot.break_type ?? '—' },
  ];
}
