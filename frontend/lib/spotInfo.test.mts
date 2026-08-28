/**
 * Pins for the two side-by-side cards on the spot page — the Spot info panel and the
 * Optimal conditions card — and specifically for the four rows that used to render a null
 * as a claim:
 *
 *   Hazards  never renders. Unreviewed on all 648 spots; "—" read as "no hazards here".
 *   Crowd    renders only when crowd_factor has a value. Null on 475 of 648, where
 *            "Crowd —" read as "not crowded" about a spot nothing has ever checked.
 *   Tide     renders '—' in BOTH cards. Optimal conditions used to say 'any', so one null
 *            read two different ways in two cards eighteen inches apart.
 *   Period   never renders. It was the literal '10s+' on all 648 spots, and nothing in
 *            the schema can source a per-spot period.
 *
 * Break and Tide preference keep their em dashes on purpose — see spotInfo.ts.
 *
 * NO TEST FRAMEWORK IS ADDED — the frontend has none. package.json's test script runs bare
 * Node with type-stripping, and this file follows lib/ratings.test.mts exactly. spotInfo.ts
 * imports only formatting.ts, which is itself import-free, so this runs with no packages:
 *
 *     node --experimental-strip-types frontend/lib/spotInfo.test.mts
 *     (or: npm --prefix frontend run test)
 *
 * EVERY EXPECTED VALUE IS WRITTEN LITERALLY, including the cardinal strings, which are
 * hand-computed from CARDINAL_16 with the arithmetic in a comment. None is produced by
 * calling the function under test.
 *
 * WHAT THIS CAN AND CANNOT PIN. spotInfoRows and optimalConditionsRows ARE the two cards'
 * row sets, so "no Hazards row", "no Period row" and "no Crowd row when empty" are pinned
 * exactly. Neither is a DOM render: with no framework, no jsdom and no installed packages
 * there is no way to mount a React component here. Both components map over these
 * functions' output and add no rows of their own, which is the seam that makes the
 * data-level pin meaningful.
 */
import { spotInfoRows, optimalConditionsRows, type SpotInfoInput } from './spotInfo.ts';

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
const optLabels = (s: SpotInfoInput): string[] => optimalConditionsRows(s).map((r) => r.label);
const optValues = (s: SpotInfoInput): string[] => optimalConditionsRows(s).map((r) => r.value);
const hasRow = (rows: string[], word: string): boolean =>
  rows.some((l) => l.toLowerCase().includes(word));

// A fully-populated spot. Directions chosen so their cardinal strings are exact:
//   optimal_swell_dir 315 -> ((315 % 360) + 360) % 360 = 315; 315 / 22.5 = 14 exactly;
//                            CARDINAL_16[14] = 'NW';  Math.round(315) = 315  -> 'NW 315°'
//   offshore_wind_deg  45 -> 45 / 22.5 = 2 exactly;   CARDINAL_16[2]  = 'NE' -> 'NE offshore'
const POPULATED: SpotInfoInput = {
  break_type: 'reef', tide_preference: 'mid', crowd_factor: 'high',
  optimal_swell_dir: 315, offshore_wind_deg: 45,
  hazards: ['sharp_reef', 'strong_rip', 'localism'],
};

// --------------------------------------------------------------------------- //
// 1 — HAZARDS: no row, for every shape the column takes in production          //
// --------------------------------------------------------------------------- //
// Measured distribution: 173 spots populated, 473 empty array '{}', 2 NULL.
const EMPTY_ARRAY: SpotInfoInput = { ...POPULATED, hazards: [] };
const NULL_HAZARDS: SpotInfoInput = { ...POPULATED, hazards: null };
const ABSENT: SpotInfoInput = {
  break_type: 'reef', tide_preference: 'mid', crowd_factor: 'high',
};

check('populated hazards array -> NO Hazards row', !hasRow(labels(POPULATED), 'hazard'));
check('empty hazards array -> NO Hazards row', !hasRow(labels(EMPTY_ARRAY), 'hazard'));
check('null hazards -> NO Hazards row', !hasRow(labels(NULL_HAZARDS), 'hazard'));
check('hazards key absent entirely -> NO Hazards row', !hasRow(labels(ABSENT), 'hazard'));
check('undefined hazards -> NO Hazards row',
      !hasRow(labels({ ...POPULATED, hazards: undefined }), 'hazard'));
check('one-element hazards array -> NO Hazards row',
      !hasRow(labels({ ...POPULATED, hazards: ['rocks'] }), 'hazard'));
check('no Hazards row in the Optimal conditions card either',
      !hasRow(optLabels(POPULATED), 'hazard'));

// The hazard STRINGS must not reach either card by any other route — a removed row whose
// values were folded into a neighbour would defeat the point. 'sharp_reef' is checked in
// both the raw and the de-underscored spelling the old row produced (`replace(/_/g, ' ')`).
const joined = values(POPULATED).join(' | ') + ' | ' + optValues(POPULATED).join(' | ');
check('no raw hazard token in any value', !joined.includes('sharp_reef'));
check('no de-underscored hazard token in any value', !joined.includes('sharp reef'));
check('no second hazard token in any value', !joined.includes('localism'));
check('no strong_rip in any value',
      !joined.includes('strong_rip') && !joined.includes('strong rip'));

// --------------------------------------------------------------------------- //
// 2 — CROWD: the row exists only when crowd_factor has a value                 //
// --------------------------------------------------------------------------- //
// crowd_factor is null on 475 of 648 spots. Every blank shape must drop the row, and the
// empty-string case matters because the column carries '' as well as NULL.
eq('populated crowd_factor -> the Crowd row renders', labels(POPULATED),
   ['Break', 'Tide preference', 'Crowd']);
eq('null crowd_factor -> NO Crowd row',
   labels({ ...POPULATED, crowd_factor: null }), ['Break', 'Tide preference']);
eq('undefined crowd_factor -> NO Crowd row',
   labels({ ...POPULATED, crowd_factor: undefined }), ['Break', 'Tide preference']);
eq('absent crowd_factor key -> NO Crowd row',
   labels({ break_type: 'reef', tide_preference: 'mid' }), ['Break', 'Tide preference']);
eq('EMPTY STRING crowd_factor -> NO Crowd row',
   labels({ ...POPULATED, crowd_factor: '' }), ['Break', 'Tide preference']);
eq('whitespace-only crowd_factor -> NO Crowd row',
   labels({ ...POPULATED, crowd_factor: '   ' }), ['Break', 'Tide preference']);
// The row must never be an em dash: that was the claim ("not crowded"), not the fix.
check('no em-dash Crowd value is ever produced',
      !spotInfoRows({ ...POPULATED, crowd_factor: null }).some((r) => r.label === 'Crowd'));
eq('a real crowd value still renders verbatim',
   values({ ...POPULATED, crowd_factor: 'moderate' }), ['reef', 'mid', 'moderate']);
// A numeric-looking value is still a value. ('0' is truthy in JS, so this does NOT
// discriminate a falsy check from the blank check — '' and '   ' above do that. It pins
// that nothing coerces or number-parses the column on the way to the row.)
eq("crowd_factor '0' is a value, not a blank",
   labels({ ...POPULATED, crowd_factor: '0' }), ['Break', 'Tide preference', 'Crowd']);

// --------------------------------------------------------------------------- //
// 3 — BREAK and TIDE keep their em dashes in the Spot info panel               //
// --------------------------------------------------------------------------- //
// Unchanged behaviour, pinned so change 2 cannot quietly spread to the other two rows.
const ALL_EMPTY: SpotInfoInput = {
  break_type: null, tide_preference: null, crowd_factor: null, hazards: null,
};
eq('all-empty spot renders exactly two rows', labels(ALL_EMPTY), ['Break', 'Tide preference']);
eq('...both as em dashes', values(ALL_EMPTY), ['—', '—']);
eq('null break_type alone -> em dash, row kept',
   values({ break_type: null, tide_preference: 'low', crowd_factor: 'high' }),
   ['—', 'low', 'high']);
eq('null tide_preference alone -> em dash, row kept',
   values({ break_type: 'point', tide_preference: null, crowd_factor: 'high' }),
   ['point', '—', 'high']);
eq('empty-string break_type is passed through, not coerced',
   values({ break_type: '', tide_preference: 'mid', crowd_factor: 'low' }),
   ['', 'mid', 'low']);

// --------------------------------------------------------------------------- //
// 4 — TIDE: one null, one rendering, in BOTH cards                            //
// --------------------------------------------------------------------------- //
// The Optimal conditions card used to render `tide_preference ?? 'any'`. 'any' is a real
// answer ("works at any tide"), so producing it from a null asserts something unchecked.
const tideOf = (rows: { label: string; value: string }[], label: string): string =>
  rows.filter((r) => r.label === label).map((r) => r.value)[0];

eq('Optimal conditions renders a null tide as an em dash, NOT "any"',
   tideOf(optimalConditionsRows({ ...POPULATED, tide_preference: null }), 'Tide'), '—');
eq('...and undefined too',
   tideOf(optimalConditionsRows({ ...POPULATED, tide_preference: undefined }), 'Tide'), '—');
eq('...and an absent key too',
   tideOf(optimalConditionsRows({ break_type: 'reef' }), 'Tide'), '—');
check('the string "any" appears nowhere in the card for a null tide',
      !optValues({ ...POPULATED, tide_preference: null }).join(' | ').includes('any'));
eq('a real tide preference still renders verbatim',
   tideOf(optimalConditionsRows({ ...POPULATED, tide_preference: 'low' }), 'Tide'), 'low');
// THE CROSS-CARD PIN: the same null must read the same way in both cards.
eq('both cards render a null tide identically',
   tideOf(optimalConditionsRows({ ...POPULATED, tide_preference: null }), 'Tide'),
   tideOf(spotInfoRows({ ...POPULATED, tide_preference: null }), 'Tide preference'));
eq('both cards render a real tide identically',
   tideOf(optimalConditionsRows({ ...POPULATED, tide_preference: 'mid' }), 'Tide'),
   tideOf(spotInfoRows({ ...POPULATED, tide_preference: 'mid' }), 'Tide preference'));

// --------------------------------------------------------------------------- //
// 5 — PERIOD: the row is gone                                                  //
// --------------------------------------------------------------------------- //
// It was `{ label: 'Period', value: '10s+' }` — one literal for all 648 spots.
check('no Period row for a fully-populated spot', !hasRow(optLabels(POPULATED), 'period'));
check('no Period row for an empty spot', !hasRow(optLabels(ALL_EMPTY), 'period'));
check('the literal "10s+" appears in no value',
      !optValues(POPULATED).join(' | ').includes('10s+')
      && !optValues(ALL_EMPTY).join(' | ').includes('10s+'));
check('no value ends in "s+" — the row is not hiding under another label',
      !optValues(POPULATED).some((v) => v.endsWith('s+')));

// --------------------------------------------------------------------------- //
// 6 — the Optimal conditions card is exactly its four surviving rows           //
// --------------------------------------------------------------------------- //
// Pins the count, so a Period row reintroduced under any other name fails here too.
eq('exactly four rows, in display order', optLabels(POPULATED),
   ['Swell', 'Wind', 'Tide', 'Break']);
eq('row count is 4', optimalConditionsRows(POPULATED).length, 4);
// Cardinals hand-computed above: 315 -> CARDINAL_16[14] 'NW'; 45 -> CARDINAL_16[2] 'NE'.
eq('the four surviving values render', optValues(POPULATED),
   ['NW 315°', 'NE offshore', 'mid', 'reef']);
// Rounding is part of the Swell value: 292.5 / 22.5 = 13 -> CARDINAL_16[13] 'WNW',
// and Math.round(292.5) = 293 (JS rounds .5 toward +Infinity).
eq('swell direction rounds to a whole degree',
   tideOf(optimalConditionsRows({ ...POPULATED, optimal_swell_dir: 292.5 }), 'Swell'),
   'WNW 293°');
eq('null swell direction -> em dash',
   tideOf(optimalConditionsRows({ ...POPULATED, optimal_swell_dir: null }), 'Swell'), '—');
// Wind keeps its bare 'offshore' when the bearing is unknown — unchanged, and correct:
// it names the direction the wind should come FROM relative to the beach, not a claim
// about a measured bearing.
eq('null offshore bearing -> bare "offshore"',
   tideOf(optimalConditionsRows({ ...POPULATED, offshore_wind_deg: null }), 'Wind'), 'offshore');
// 0° must not be swallowed by a falsy check: due north is a real bearing.
eq('a 0-degree bearing is a value, not a blank',
   tideOf(optimalConditionsRows({ ...POPULATED, offshore_wind_deg: 0 }), 'Wind'), 'N offshore');
eq('a 0-degree swell direction is a value, not a blank',
   tideOf(optimalConditionsRows({ ...POPULATED, optimal_swell_dir: 0 }), 'Swell'), 'N 0°');

// --------------------------------------------------------------------------- //
// 7 — both builders are pure                                                   //
// --------------------------------------------------------------------------- //
const probe: SpotInfoInput = { ...POPULATED };
spotInfoRows(probe);
optimalConditionsRows(probe);
eq('input is unmutated by either builder', probe, POPULATED);
check('spotInfoRows returns a fresh array each call',
      spotInfoRows(POPULATED) !== spotInfoRows(POPULATED));
check('optimalConditionsRows returns a fresh array each call',
      optimalConditionsRows(POPULATED) !== optimalConditionsRows(POPULATED));
eq('spot-info rows are stable across calls', spotInfoRows(POPULATED), spotInfoRows(POPULATED));
eq('optimal rows are stable across calls',
   optimalConditionsRows(POPULATED), optimalConditionsRows(POPULATED));

// Throw rather than process.exit: a thrown error still gives node a non-zero exit code,
// and it keeps this file free of any ambient Node types, so it typechecks and runs with
// nothing installed at all.
if (failures > 0) {
  throw new Error(`spotInfo: ${failures} FAILURE(S)`);
}
console.log('\nspotInfo: ALL PASS');
