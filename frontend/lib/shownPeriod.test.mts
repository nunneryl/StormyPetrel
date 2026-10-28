/**
 * The swell train the site shows (lib/shownPeriod.ts), held to the hand-written cases the pipeline
 * is held to as well (lib/shownPeriod.cases.json, read by pipeline/tests/test_shown_period.py), and
 * every period and direction the site shows read through it.
 *
 *   1. THE SHARED CASES. Every row in the table gets the period, direction and train written there.
 *   2. THE RULE'S EDGES. A wind sea takes over when it is bigger and at least 0.5 s longer than the
 *      rated partition, or at least 4x its height; only the second can lower the shown period.
 *      Neither switches from 90 degrees or more outside the spot's swell window. The constants
 *      are the pipeline's.
 *   3. EVERY DISPLAY READS IT. The six places that show a period or swell direction call shownTrain,
 *      and no page or component reads swell_tp / swell_dp itself.
 *
 * NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST: they are literals here or in the cases file.
 *
 *     node --experimental-strip-types frontend/lib/shownPeriod.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { walk } from './copyScan.ts';
import {
  BLOCKED_OUTSIDE_DEG, DOMINANCE_K, MIN_LONGER_S, ratedPartition, shownTrain, switchCase, windSeaBlocked,
  windSeaDominates, type ShownTrainRow, type WindowArc,
} from './shownPeriod.ts';

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = dirname(HERE);
const read = (rel: string) => readFileSync(join(FRONTEND, rel), 'utf8');

// --------------------------------------------------------------------------- //
// 1 — the shared cases                                                         //
// --------------------------------------------------------------------------- //
type Case = {
  name: string; row: ShownTrainRow; arcs?: WindowArc[] | null; tp: number | null; dp: number | null; which: string;
};
const cases = (JSON.parse(read('lib/shownPeriod.cases.json')) as { cases: Case[] }).cases;
check('the shared table is not trivially small', cases.length >= 32, `${cases.length}`);
for (const c of cases) {
  const got = shownTrain(c.row, c.arcs ?? null);
  check(`${c.name} -> ${c.tp} s / ${c.dp}° (${c.which})`,
    got.tp === c.tp && got.dp === c.dp && got.which === c.which, JSON.stringify(got));
}
check('the table covers both trains', ['swell', 'wind_sea'].every((w) => cases.some((c) => c.which === w)));
check('the table has windowed cases both ways, and spots with no arcs',
  ['swell', 'wind_sea'].every((w) => cases.some((c) => (c.arcs ?? []).length > 0 && c.which === w))
  && cases.some((c) => 'arcs' in c && (c.arcs ?? []).length === 0));

// --------------------------------------------------------------------------- //
// 2 — the rule's edges                                                         //
// --------------------------------------------------------------------------- //
const base: ShownTrainRow = {
  swell_source: 'nwps_height_ww3_dir', swell_tp: 6.0, swell_dp: 90.0, tp: 7.0, dp: 85.0,
  swell_1_hs: 1.0, swell_1_tp: 6.0, swell_1_dp: 90.0,
  wind_wave_hs: 1.0, wind_wave_tp: 6.0, wind_wave_dp: 60.0,
};
check('the rated partition is found by its period and direction',
  JSON.stringify(ratedPartition(base)) === JSON.stringify([1.0, 6.0, 90.0]));
check('no shown train for no row', JSON.stringify(shownTrain(null, null)) === '{"tp":null,"dp":null,"which":"swell"}');
// A sweep around the rated 1.0 m / 6 s partition: LONGER when the wind sea is above 1.0 m and at
// least 6.5 s; DOMINANT at 4.0 m or more, whatever its period. Only DOMINANT can lower the period.
let swept = 0;
const wrong: string[] = [];
for (const wh of [0.5, 0.99, 1.0, 1.01, 2.0, 3.99, 4.0, 5.0]) {
  for (const wt of [3.0, 5.99, 6.0, 6.49, 6.5, 9.0, 16.0]) {
    swept += 1;
    const row = { ...base, wind_wave_hs: wh, wind_wave_tp: wt };
    const want = wh > 1.0 && wt >= 6.5 ? 'longer' : (wh >= 4.0 ? 'dominant' : null);
    const got = shownTrain(row, null);
    if (switchCase(row, null) !== want || windSeaDominates(row, null) !== (want !== null)
        || (got.which === 'wind_sea') !== (want !== null)
        || ((got.tp ?? 0) < 6.0 && want !== 'dominant')) {
      wrong.push(`${wh} m ${wt} s -> ${switchCase(row, null)} ${JSON.stringify(got)}`);
    }
  }
}
check(`across ${swept} wind seas, it switches exactly on its two cases, and only DOMINANT lowers the period`,
  wrong.length === 0 && swept === 56, wrong.slice(0, 5).join('; '));
// The window 30-150 with span 124: pad 2, sector 28 to 152. A bearing is 90 or more outside it from
// 152 + 90 = 242 round to 28 - 90 = -62, i.e. 298: blocked exactly on 242..298, for both cases.
const ONE: WindowArc[] = [{ min: 30, max: 150, span: 124 }];
const longerRow: ShownTrainRow = {
  swell_source: 'nwps_height_ww3_dir', swell_tp: 3.0, swell_dp: 80.0,
  swell_1_hs: 0.24, swell_1_tp: 3.0, swell_1_dp: 80.0, wind_wave_hs: 2.4, wind_wave_tp: 9.0,
};
const dominantRow: ShownTrainRow = {
  ...longerRow, swell_tp: 14.0, swell_1_tp: 14.0, swell_1_hs: 0.5, wind_wave_hs: 2.5, wind_wave_tp: 8.0,
};
const offEdge: string[] = [];
for (let d = 0; d < 360; d += 1) {
  const blocked = d >= 242 && d <= 298;
  if (windSeaBlocked(d, ONE) !== blocked || windSeaBlocked(d, null) || windSeaBlocked(d, [])) offEdge.push(`${d}`);
  for (const [row, c] of [[longerRow, 'longer'], [dominantRow, 'dominant']] as const) {
    const r = { ...row, wind_wave_dp: d };
    if (switchCase(r, ONE) !== (blocked ? null : c) || switchCase(r, null) !== c || switchCase(r, []) !== c
        || shownTrain(r, ONE).which !== (blocked ? 'swell' : 'wind_sea')) offEdge.push(`${c} ${d}`);
  }
}
check('on every bearing, both cases switch except 90 deg or more outside the window (242..298); no arcs, always',
  offEdge.length === 0, offEdge.slice(0, 5).join('; '));
// One rule, two languages: the constants are the pipeline's.
const pyRule = readFileSync(join(FRONTEND, '..', 'pipeline', 'forecast', 'shown_period.py'), 'utf8');
check('MIN_LONGER_S, DOMINANCE_K and BLOCKED_OUTSIDE_DEG are pipeline/forecast/shown_period.py\'s',
  /^MIN_LONGER_S = 0\.5$/m.test(pyRule) && /^DOMINANCE_K = 4\.0$/m.test(pyRule)
  && /^BLOCKED_OUTSIDE_DEG = 90\.0$/m.test(pyRule)
  && MIN_LONGER_S === 0.5 && DOMINANCE_K === 4 && BLOCKED_OUTSIDE_DEG === 90,
  `${MIN_LONGER_S} ${DOMINANCE_K} ${BLOCKED_OUTSIDE_DEG}`);

// --------------------------------------------------------------------------- //
// 3 — every display reads it                                                   //
// --------------------------------------------------------------------------- //
// Each with the spot's window: the two components that take rows alone get it from the spot page.
const SITES: [string, string][] = [
  ['components/CurrentConditions.tsx', 'const { tp, dp } = shownTrain(current, windowArcs);'],
  ['components/ForecastGrid.tsx', 'const { tp, dp } = shownTrain(r, windowArcs);'],
  ['components/SpotMap.tsx', 'const { tp: swellPeriod, dp: swellDir } = shownTrain(f, s.swell_window_arcs);'],
  ['components/SpotCard.tsx', '{fmtSec(shownTrain(f, spot.swell_window_arcs).tp)}'],
  ['components/RegionList.tsx', '{fmtSec(shownTrain(f, s.swell_window_arcs).tp)}'],
  ['app/page.tsx', 'const { tp } = shownTrain(f, s.swell_window_arcs);'],
];
for (const [rel, use] of SITES) check(`${rel} shows the period through shownTrain`, read(rel).includes(use));
const spotPage = read('app/spot/[slug]/page.tsx');
check('the spot page hands both components the spot\'s window',
  (spotPage.match(/windowArcs=\{spot\.swell_window_arcs\}/g) ?? []).length === 2);
const direct = ['app', 'components'].flatMap((d) => walk(join(FRONTEND, d)))
  .map((abs) => relative(FRONTEND, abs))
  // A query's column list names the columns without showing them; it is left out.
  .filter((rel) => /\b(?:swell_tp|swell_dp)\b/.test(read(rel).replace(/'spot_id, valid_time[^']*'/g, '')));
check('no page or component reads swell_tp or swell_dp itself', direct.length === 0, direct.join(', '));

if (failures > 0) {
  throw new Error(`shownPeriod: ${failures} FAILURE(S)`);
}
console.log('\nshownPeriod: ALL PASS');
