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

export type SpotInfoRow = { label: string; value: string };

/** The fields this panel reads. Structural on purpose: the module imports nothing,
 *  so it runs under `node --experimental-strip-types` with no packages installed. */
export type SpotInfoInput = {
  break_type?: string | null;
  tide_preference?: string | null;
  crowd_factor?: string | null;
  /** Received and NOT rendered — see the module comment. */
  hazards?: string[] | null;
};

/** The Spot info panel's rows, in display order. Never includes Hazards. */
export function spotInfoRows(spot: SpotInfoInput): SpotInfoRow[] {
  return [
    { label: 'Break', value: spot.break_type ?? '—' },
    { label: 'Tide preference', value: spot.tide_preference ?? '—' },
    { label: 'Crowd', value: spot.crowd_factor ?? '—' },
  ];
}
