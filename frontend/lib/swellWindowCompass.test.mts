/**
 * The swell compass (lib/swellWindowCompass.ts, components/SwellWindowCompass.tsx): bearings to
 * points, one wedge per window arc from its padded sector, the arrows as the Swell breakdown's own
 * rows (none hidden; small ones at a minimum length and faded), and the component wired to the
 * breakdown and the tiles' hour rather than doing sums of its own.
 *
 * NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST: every point and path below is worked by hand,
 * with sin 45° = cos 45° = 0.70711, sin 5° = 0.08716, cos 5° = 0.99619, sin 30° = 0.5,
 * cos 30° = 0.86603.
 *
 *     node --experimental-strip-types frontend/lib/swellWindowCompass.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  MIN_LENGTH_FRAC, NO_WINDOW_NOTE, arcSector, compassArrows, compassPoint, wedgePath,
} from './swellWindowCompass.ts';
import { breakdownRows, type BreakdownRow } from './swellBreakdown.ts';

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}
const near = (a: number, b: number) => Math.abs(a - b) < 1e-4;
const FRONTEND = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (rel: string) => readFileSync(join(FRONTEND, rel), 'utf8');

// 1. Bearings, north up: radius 10 about (50, 50).
const pt = (b: number) => compassPoint(b, 10, 50, 50);
check('0° (N) is straight up: (50, 40)', near(pt(0).x, 50) && near(pt(0).y, 40));
check('90° (E) is right: (60, 50)', near(pt(90).x, 60) && near(pt(90).y, 50));
check('180° (S) is down: (50, 60)', near(pt(180).x, 50) && near(pt(180).y, 60));
check('270° (W) is left: (40, 50)', near(pt(270).x, 40) && near(pt(270).y, 50));
check('45° (NE): (57.0711, 42.9289)', near(pt(45).x, 57.0711) && near(pt(45).y, 42.9289));

// 2. Wedges.
// 315 to 90, no span: pad 0, sweep (90 - 315) mod 360 = 135 from 315. Start (50 - 7.071, 50 - 7.071),
// end at 90° (60, 50), small arc.
check('wedge 315°–90° crosses north: 135° from 315°',
  JSON.stringify(arcSector({ min: 315, max: 90 })) === JSON.stringify({ start: 315, sweep: 135, full: false }));
check('wedge 315°–90° path', wedgePath({ min: 315, max: 90 }, 10, 50, 50) ===
  'M 50 50 L 42.929 42.929 A 10 10 0 0 1 60 50 Z', wedgePath({ min: 315, max: 90 }, 10, 50, 50));
// 180 to 350, span 180: pad (180 - 170) / 2 = 5, so 175° for 180°. Start (50 + 0.872, 50 + 9.962),
// end at 355° (50 - 0.872, 50 - 9.962); a sweep of exactly 180 is not the large arc.
check('wedge 180°–350° with span 180 is padded to 175°–355°',
  JSON.stringify(arcSector({ min: 180, max: 350, span: 180 })) ===
    JSON.stringify({ start: 175, sweep: 180, full: false }));
check('wedge 175°–355° path', wedgePath({ min: 180, max: 350, span: 180 }, 10, 50, 50) ===
  'M 50 50 L 50.872 59.962 A 10 10 0 0 1 49.128 40.038 Z',
  wedgePath({ min: 180, max: 350, span: 180 }, 10, 50, 50));
// 90 to 330: sweep 240, the large arc. End at 330° (50 - 5, 50 - 8.660).
check('wedge 90°–330° takes the large arc', wedgePath({ min: 90, max: 330 }, 10, 50, 50) ===
  'M 50 50 L 60 50 A 10 10 0 1 1 45 41.34 Z', wedgePath({ min: 90, max: 330 }, 10, 50, 50));
// 0 to 350, span 360: pad 5, sweep 360, the whole circle.
check('an arc of 360° is the whole circle', wedgePath({ min: 0, max: 350, span: 360 }, 10, 50, 50) ===
  'M 50 40 A 10 10 0 1 1 50 60 A 10 10 0 1 1 50 40 Z');

// 3. Arrows: the breakdown's rows, in its order, none hidden.
const row = (label: string, hs: number, dp: number | null, windSea = false, muted = false): BreakdownRow =>
  ({ label, hs, tp: 10, dp, windSea, share: 0, muted });
const { arrows, noDirection } = compassArrows([
  row('P1 (primary)', 2.0, 290),
  row('P2 (secondary)', 1.0, 200),
  row('P3 (tertiary)', 0.5, 150),
  row('Wind sea', 0.2, 60, true, true),
  row('P4', 0.4, null),
]);
// Biggest 2.0 m: P1 1.0; P2 1.0 / 2.0 = 0.5; P3 0.5 / 2.0 = 0.25, exactly the minimum, not faded;
// wind sea 0.2 / 2.0 = 0.1, drawn at 0.25 and faded. P4 has no direction: listed, not drawn.
check('the minimum length is a quarter of the longest', MIN_LENGTH_FRAC === 0.25);
check('arrows in the breakdown order: P1, P2, P3, WS',
  JSON.stringify(arrows.map((a) => a.short)) === JSON.stringify(['P1', 'P2', 'P3', 'WS']));
check('lengths 1, 0.5, 0.25, 0.25', JSON.stringify(arrows.map((a) => a.lengthFrac)) ===
  JSON.stringify([1, 0.5, 0.25, 0.25]), JSON.stringify(arrows.map((a) => a.lengthFrac)));
check('only the wind sea (a tenth of the biggest) is faded',
  JSON.stringify(arrows.map((a) => a.faded)) === JSON.stringify([false, false, false, true]));
check("the wind sea keeps the breakdown's grey-out", arrows[3].muted && arrows[3].windSea);
check('a row with no direction is listed, not dropped',
  noDirection.length === 1 && noDirection[0].label === 'P4');

// 4. From an hour: the rows breakdownRows makes. Swell 2.0 m from 290 (the rated train), 1.0 m
//    from 200, a wind sea 0.2 m from 60: not shown as the surf (0.2 is not 4 x 2.0), so its label
//    stays "Wind sea" and greyed.
const hour = {
  swell_source: 'ww3', swell_tp: 14, swell_dp: 290,
  swell_1_hs: 2.0, swell_1_tp: 14, swell_1_dp: 290,
  swell_2_hs: 1.0, swell_2_tp: 9, swell_2_dp: 200,
  swell_3_hs: null, swell_3_tp: null, swell_3_dp: null,
  wind_wave_hs: 0.2, wind_wave_tp: 4, wind_wave_dp: 60, chop_ratio: 0.1,
};
const fromHour = compassArrows(breakdownRows(hour, [{ min: 180, max: 350 }], 270)).arrows;
check('an hour: P1 (primary), P2 (secondary), Wind sea',
  JSON.stringify(fromHour.map((a) => [a.label, a.dp, a.hs])) ===
    JSON.stringify([['P1 (primary)', 290, 2.0], ['P2 (secondary)', 200, 1.0], ['Wind sea', 60, 0.2]]),
  JSON.stringify(fromHour.map((a) => [a.label, a.dp, a.hs])));

// 5. The component reads the breakdown and the tiles' hour, and nothing else.
const comp = read('components/SwellWindowCompass.tsx');
check('the compass takes the hour the tiles show (useCurrentHour)',
  comp.includes('const forecast = useCurrentHour().selection.row;'));
check("the compass's rows are breakdownRows, with the spot's window and facing",
  comp.includes('const rows = breakdownRows(forecast, windowArcs, facingDeg);'));
check('the compass reads no partition column itself',
  !/swell_[123]_|wind_wave_|chop_ratio/.test(comp));
check('the wind arrow is the hour\'s wind_dir, as the Wind tile reads it',
  comp.includes('const windDir = forecast.wind_dir;') &&
  read('components/CurrentConditions.tsx').includes('current?.wind_dir'));
check('one wedge per arc', comp.includes('{arcs.map((a, i) => (') && comp.includes('d={wedgePath(a, R, C, C)}'));
check(`no arcs: no wedge and "${NO_WINDOW_NOTE}"`, NO_WINDOW_NOTE === 'No swell window set' &&
  comp.includes('{arcs.length === 0 && <span data-compass="no-window">{NO_WINDOW_NOTE}. </span>}'));
const page = read('app/spot/[slug]/page.tsx');
const provider = page.slice(page.indexOf('<CurrentHourProvider'), page.indexOf('</CurrentHourProvider>'));
check('the spot page puts the compass inside the current-hour provider, beside the breakdown',
  provider.includes('<SwellPartitions') && provider.includes('<SwellWindowCompass') &&
  provider.includes('windowArcs={spot.swell_window_arcs}') &&
  provider.includes('optimalDeg={spot.optimal_swell_dir}'));

if (failures) {
  console.log(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log('\nswellWindowCompass: ALL PASS');
