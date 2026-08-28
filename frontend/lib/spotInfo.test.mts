/**
 * Pins for the Spot info panel's row set — specifically, that HAZARDS IS NOT ONE OF THEM.
 *
 * WHY THIS FILE EXISTS. `hazards` is unreviewed on every spot in the table (review_status
 * is 'auto' on all 648), and it renders two different wrong things: an em dash on the 475
 * spots with nothing in the column, which reads as "no hazards here", and an inferred list
 * on the other 173, which reads as fact. The row was removed. This pins the removal so it
 * cannot come back by accident — through a revert, a merge, or someone "restoring a missing
 * field" — and, in particular, so it stays gone for ALL THREE shapes the column takes in
 * production: a populated array, an empty array (473 rows), and NULL (2 rows).
 *
 * NO TEST FRAMEWORK IS ADDED — the frontend has none. package.json's only test script runs
 * lib/ratings.test.mts directly on stock Node with type-stripping, and this file follows it
 * exactly. spotInfo.ts imports nothing, so this runs with zero installed packages:
 *
 *     node --experimental-strip-types frontend/lib/spotInfo.test.mts
 *     (or: npm --prefix frontend run test, which now runs both files)
 *
 * The check/eq shape mirrors ratings.test.mts and the Python suites in pipeline/tests/.
 *
 * WHAT THIS CAN AND CANNOT PIN. spotInfoRows is the panel's row set, so "no Hazards row"
 * is pinned exactly. It is not a DOM render: with no test framework, no jsdom and no
 * installed packages there is no way to mount a React server component here. The page maps
 * over this function's output and adds no rows of its own, which is the seam that makes the
 * data-level pin meaningful.
 */
import { spotInfoRows, type SpotInfoInput } from './spotInfo.ts';

let failures = 0;

function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}

function eq<T>(name: string, got: T, want: T): void {
  check(name, JSON.stringify(got) === JSON.stringify(want),
        `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

const labels = (s: SpotInfoInput): string[] => spotInfoRows(s).map((r) => r.label);
const values = (s: SpotInfoInput): string[] => spotInfoRows(s).map((r) => r.value);
const hasHazards = (s: SpotInfoInput): boolean =>
  labels(s).some((l) => l.toLowerCase().includes('hazard'));

// --------------------------------------------------------------------------- //
// 1 — no Hazards row, for every shape the column takes in production           //
// --------------------------------------------------------------------------- //
// Measured distribution: 173 spots populated, 473 empty array '{}', 2 NULL.
// All three must produce the same three-row panel.
const POPULATED: SpotInfoInput = {
  break_type: 'reef', tide_preference: 'mid', crowd_factor: 'high',
  hazards: ['sharp_reef', 'strong_rip', 'localism'],
};
const EMPTY_ARRAY: SpotInfoInput = { ...POPULATED, hazards: [] };
const NULL_HAZARDS: SpotInfoInput = { ...POPULATED, hazards: null };
const ABSENT: SpotInfoInput = {
  break_type: 'reef', tide_preference: 'mid', crowd_factor: 'high',
};

check('populated hazards array -> NO Hazards row', !hasHazards(POPULATED));
check('empty hazards array -> NO Hazards row', !hasHazards(EMPTY_ARRAY));
check('null hazards -> NO Hazards row', !hasHazards(NULL_HAZARDS));
check('hazards key absent entirely -> NO Hazards row', !hasHazards(ABSENT));
check('undefined hazards -> NO Hazards row',
      !hasHazards({ ...POPULATED, hazards: undefined }));

// A single-element array is the shape most likely to be waved through by a
// `hazards?.length` guard written the other way round.
check('one-element hazards array -> NO Hazards row',
      !hasHazards({ ...POPULATED, hazards: ['rocks'] }));

// --------------------------------------------------------------------------- //
// 2 — the hazard STRINGS never reach the panel by any other route              //
// --------------------------------------------------------------------------- //
// Removing the row but folding the values into a neighbouring row would defeat the
// point, so assert on the rendered values as well as the labels. 'sharp_reef' is
// checked both raw and in the underscore-stripped form the old row produced
// (`h.replace(/_/g, ' ')`), so neither spelling can leak.
const joined = values(POPULATED).join(' | ');
check('no raw hazard token in any value', !joined.includes('sharp_reef'));
check('no de-underscored hazard token in any value', !joined.includes('sharp reef'));
check('no second hazard token in any value', !joined.includes('localism'));
check('no strong_rip in any value', !joined.includes('strong_rip') && !joined.includes('strong rip'));

// --------------------------------------------------------------------------- //
// 3 — the panel is exactly the three surviving rows, in order                  //
// --------------------------------------------------------------------------- //
// Pins the count too: a fourth row of ANY label would fail here, so a Hazards row
// reintroduced under a different name ("Warnings", "Cautions") is caught as well.
eq('exactly three rows, in display order', labels(POPULATED),
   ['Break', 'Tide preference', 'Crowd']);
eq('row count is 3', spotInfoRows(POPULATED).length, 3);
eq('the three surviving values still render', values(POPULATED),
   ['reef', 'mid', 'high']);

// The row set must not depend on the hazards value in any way.
eq('row set is identical for populated and empty', labels(POPULATED), labels(EMPTY_ARRAY));
eq('row set is identical for populated and null', labels(POPULATED), labels(NULL_HAZARDS));
eq('rows are byte-identical for populated and null',
   spotInfoRows(POPULATED), spotInfoRows(NULL_HAZARDS));

// --------------------------------------------------------------------------- //
// 4 — the surviving rows keep their em-dash fallbacks                          //
// --------------------------------------------------------------------------- //
// Unchanged behaviour, pinned because Step 3 is an open question about exactly these
// three rows: 204 spots have all three empty and 475 have crowd_factor empty. If that
// decision lands later, these assertions are what it has to move.
const ALL_EMPTY: SpotInfoInput = {
  break_type: null, tide_preference: null, crowd_factor: null, hazards: null,
};
eq('all-null spot renders three em dashes', values(ALL_EMPTY), ['—', '—', '—']);
eq('all-null spot still renders three rows', labels(ALL_EMPTY),
   ['Break', 'Tide preference', 'Crowd']);
eq('crowd_factor alone empty -> only Crowd is an em dash',
   values({ break_type: 'beach break', tide_preference: 'low', crowd_factor: null }),
   ['beach break', 'low', '—']);
eq('empty string is passed through, not coerced to an em dash',
   values({ break_type: '', tide_preference: 'mid', crowd_factor: 'low' }),
   ['', 'mid', 'low']);

// --------------------------------------------------------------------------- //
// 5 — the function is pure and does not mutate its input                       //
// --------------------------------------------------------------------------- //
const probe: SpotInfoInput = { ...POPULATED };
spotInfoRows(probe);
eq('input is unmutated', probe, POPULATED);
check('two calls return equal but distinct arrays',
      JSON.stringify(spotInfoRows(POPULATED)) === JSON.stringify(spotInfoRows(POPULATED))
      && spotInfoRows(POPULATED) !== spotInfoRows(POPULATED));

// Throw rather than process.exit: a thrown error still gives node a non-zero exit code,
// and it keeps this file free of any ambient Node types, so it typechecks and runs with
// nothing installed at all.
if (failures > 0) {
  throw new Error(`spotInfo: ${failures} FAILURE(S)`);
}
console.log('\nspotInfo: ALL PASS');
