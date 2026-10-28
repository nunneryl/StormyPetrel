/**
 * The swell train the site shows (lib/shownPeriod.ts), held to the hand-written cases the pipeline
 * is held to as well (lib/shownPeriod.cases.json, read by pipeline/tests/test_shown_period.py), and
 * every period and direction the site shows read through it.
 *
 *   1. THE SHARED CASES. Every row in the table gets the period, direction and train written there.
 *   2. THE RULE'S EDGES. A wind sea takes over only when it is strictly bigger and strictly longer
 *      than the rated partition; the shown period never goes down.
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
import { ratedPartition, shownTrain, windSeaDominates, type ShownTrainRow } from './shownPeriod.ts';

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
type Case = { name: string; row: ShownTrainRow; tp: number | null; dp: number | null; which: string };
const cases = (JSON.parse(read('lib/shownPeriod.cases.json')) as { cases: Case[] }).cases;
check('the shared table is not trivially small', cases.length >= 12, `${cases.length}`);
for (const c of cases) {
  const got = shownTrain(c.row);
  check(`${c.name} -> ${c.tp} s / ${c.dp}° (${c.which})`,
    got.tp === c.tp && got.dp === c.dp && got.which === c.which, JSON.stringify(got));
}
check('the table covers both trains', ['swell', 'wind_sea'].every((w) => cases.some((c) => c.which === w)));

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
check('no shown train for no row', JSON.stringify(shownTrain(null)) === '{"tp":null,"dp":null,"which":"swell"}');
// A sweep around the rated 1.0 m / 6 s partition: the wind sea takes over exactly when it is
// above both, and the shown period is never below the rated one.
let swept = 0;
const wrong: string[] = [];
for (const wh of [0.5, 0.99, 1.0, 1.01, 2.0, 5.0]) {
  for (const wt of [3.0, 5.99, 6.0, 6.01, 9.0, 16.0]) {
    swept += 1;
    const row = { ...base, wind_wave_hs: wh, wind_wave_tp: wt };
    const want = wh > 1.0 && wt > 6.0;
    const got = shownTrain(row);
    if (windSeaDominates(row) !== want || (got.which === 'wind_sea') !== want
        || (got.tp ?? 0) < 6.0) {
      wrong.push(`${wh} m ${wt} s -> ${JSON.stringify(got)}`);
    }
  }
}
check(`across ${swept} wind seas, it takes over exactly when bigger and longer, and the period never drops`,
  wrong.length === 0 && swept === 36, wrong.slice(0, 5).join('; '));

// --------------------------------------------------------------------------- //
// 3 — every display reads it                                                   //
// --------------------------------------------------------------------------- //
const SITES: [string, string][] = [
  ['components/CurrentConditions.tsx', 'const { tp, dp } = shownTrain(current);'],
  ['components/ForecastGrid.tsx', 'const { tp, dp } = shownTrain(r);'],
  ['components/SpotMap.tsx', 'const { tp: swellPeriod, dp: swellDir } = shownTrain(f);'],
  ['components/SpotCard.tsx', '{fmtSec(shownTrain(f).tp)}'],
  ['components/RegionList.tsx', '{fmtSec(shownTrain(f).tp)}'],
  ['app/page.tsx', 'const { tp } = shownTrain(f);'],
];
for (const [rel, use] of SITES) check(`${rel} shows the period through shownTrain`, read(rel).includes(use));
const direct = ['app', 'components'].flatMap((d) => walk(join(FRONTEND, d)))
  .map((abs) => relative(FRONTEND, abs))
  // A query's column list names the columns without showing them; it is left out.
  .filter((rel) => /\b(?:swell_tp|swell_dp)\b/.test(read(rel).replace(/'spot_id, valid_time[^']*'/g, '')));
check('no page or component reads swell_tp or swell_dp itself', direct.length === 0, direct.join(', '));

if (failures > 0) {
  throw new Error(`shownPeriod: ${failures} FAILURE(S)`);
}
console.log('\nshownPeriod: ALL PASS');
