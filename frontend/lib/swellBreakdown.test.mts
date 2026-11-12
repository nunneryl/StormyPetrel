/**
 * The swell breakdown's rows (lib/swellBreakdown.ts): one shared measure, each row's share of the
 * total wave height, the wind sea's share being the hour's chop_ratio as the Conditions tile
 * prints it; and the wind sea labelled as the surf when lib/shownPeriod.ts shows it.
 *
 * NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST: every share below is worked by hand.
 *
 *     node --experimental-strip-types frontend/lib/swellBreakdown.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  breakdownRows, sharePercent, WIND_SEA_LABEL, WIND_SEA_SHOWN_LABEL, type BreakdownInput,
} from './swellBreakdown.ts';

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}
const near = (a: number, b: number) => Math.abs(a - b) < 1e-9;

const EMPTY: BreakdownInput = {
  swell_source: 'ww3', swell_tp: null, swell_dp: null,
  swell_1_hs: null, swell_1_tp: null, swell_1_dp: null,
  swell_2_hs: null, swell_2_tp: null, swell_2_dp: null,
  swell_3_hs: null, swell_3_tp: null, swell_3_dp: null,
  wind_wave_hs: null, wind_wave_tp: null, wind_wave_dp: null, chop_ratio: null,
};

// 1. The reported hour: one swell partition and a wind sea, 64% wind sea on the Conditions tile.
//    Wind sea 0.64 (the chop_ratio itself); P1 1 - 0.64 = 0.36, the only swell. Biggest first.
//    The wind sea is shorter than P1 (6 s against 12 s) and under 4x its height, so not shown
//    as the surf: "Wind sea", greyed.
{
  const rows = breakdownRows({
    ...EMPTY, swell_tp: 12, swell_dp: 270,
    swell_1_hs: 1.2, swell_1_tp: 12, swell_1_dp: 270,
    wind_wave_hs: 1.5, wind_wave_tp: 6, wind_wave_dp: 250, chop_ratio: 0.64,
  }, null);
  check('reported hour: two rows, wind sea first (1.5 m over 1.2 m)',
    rows.length === 2 && rows[0].windSea && rows[1].label === 'P1 (primary)');
  check('reported hour: wind sea 64%, as the Conditions tile', near(rows[0].share, 0.64)
    && sharePercent(rows[0].share) === '64%');
  check('reported hour: P1 36%, not 100%', near(rows[1].share, 0.36)
    && sharePercent(rows[1].share) === '36%');
  check('reported hour: labelled "Wind sea" and greyed', rows[0].label === WIND_SEA_LABEL
    && rows[0].muted === true && rows[1].muted === false);
}

// 2. Two swells and a wind sea, chop_ratio 0.2. Swell energies 1.0² = 1.0 and 0.5² = 0.25,
//    1.25 in all: P1 0.8 x 1.0 / 1.25 = 0.64, P2 0.8 x 0.25 / 1.25 = 0.16; wind sea 0.2.
{
  const rows = breakdownRows({
    ...EMPTY, swell_1_hs: 1.0, swell_1_tp: 14, swell_1_dp: 280,
    swell_2_hs: 0.5, swell_2_tp: 9, swell_2_dp: 200,
    wind_wave_hs: 0.3, wind_wave_tp: 4, wind_wave_dp: 250, chop_ratio: 0.2,
  }, null);
  check('two swells: P1 0.64, P2 0.16, wind sea 0.20, summing to 1',
    rows.length === 3 && near(rows[0].share, 0.64) && near(rows[1].share, 0.16)
    && near(rows[2].share, 0.2) && near(rows.reduce((a, r) => a + r.share, 0), 1));
}

// 3. chop_ratio unknown: each row's energy over all rows'. 0.6² = 0.36 and 0.8² = 0.64.
for (const chop of [null, undefined, Number.NaN, -0.1, 1.2]) {
  const rows = breakdownRows({
    ...EMPTY, swell_1_hs: 0.6, swell_1_tp: 11, swell_1_dp: 270,
    wind_wave_hs: 0.8, wind_wave_tp: 5, wind_wave_dp: 250, chop_ratio: chop as number | null,
  }, null);
  check(`chop_ratio ${String(chop)}: energy shares, wind sea 0.64 and P1 0.36`,
    near(rows[0].share, 0.64) && near(rows[1].share, 0.36));
}

// 4. The wind sea shown as the surf (#284, 'longer'): rated on P1 (0.25 m, 3 s, from 90), the
//    wind sea 2.4 m and 9 s, bigger and 6 s longer. Labelled so and not greyed; shares still
//    the chop_ratio's: 0.9 and 0.1.
{
  const rows = breakdownRows({
    ...EMPTY, swell_tp: 3, swell_dp: 90,
    swell_1_hs: 0.25, swell_1_tp: 3, swell_1_dp: 90,
    wind_wave_hs: 2.4, wind_wave_tp: 9, wind_wave_dp: 80, chop_ratio: 0.9,
  }, null);
  check('shown as the surf: labelled so', rows[0].label === WIND_SEA_SHOWN_LABEL
    && WIND_SEA_SHOWN_LABEL === 'Wind sea · shown as the surf');
  check('shown as the surf: not greyed', rows[0].muted === false);
  check('shown as the surf: 90% and 10%', sharePercent(rows[0].share) === '90%'
    && sharePercent(rows[1].share) === '10%');
}

// 5. Rows of 0.05 m or less are left out; a lone swell with no wind sea is 100% of 1 - chop.
{
  const rows = breakdownRows({
    ...EMPTY, swell_1_hs: 1.0, swell_1_tp: 12, swell_1_dp: 270,
    swell_2_hs: 0.05, swell_2_tp: 8, swell_2_dp: 200,
    wind_wave_hs: 0.04, wind_wave_tp: 3, wind_wave_dp: 250, chop_ratio: 0.0,
  }, null);
  check('rows of 0.05 m or less left out', rows.length === 1 && rows[0].label === 'P1 (primary)');
  check('a lone swell with chop 0 is 100%', sharePercent(rows[0].share) === '100%');
}

// 6. The component reads these rows, with the spot's window; nothing says "not surfable".
{
  const HERE = dirname(fileURLToPath(import.meta.url));
  const read = (rel: string) => readFileSync(join(dirname(HERE), rel), 'utf8');
  const comp = read('components/SwellPartitions.tsx');
  check('SwellPartitions reads breakdownRows with the window arcs',
    comp.includes('breakdownRows(forecast, windowArcs)'));
  check('SwellPartitions prints sharePercent', comp.includes('sharePercent(c.share)'));
  check('no "not surfable" left', !comp.includes('not surfable'));
  check('the spot page passes the arcs',
    read('app/spot/[slug]/page.tsx').includes(
      '<SwellPartitions forecast={current} windowArcs={spot.swell_window_arcs} />'));
  check('the Conditions tile prints the same whole percent of chop_ratio',
    read('components/CurrentConditions.tsx').includes('(current.chop_ratio * 100).toFixed(0)}% wind sea'));
}

if (failures) {
  console.log(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log('\nswellBreakdown: ALL PASS');
