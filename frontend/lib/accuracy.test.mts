/**
 * The accuracy page (/accuracy, lib/accuracy.ts): its figures from accuracy_daily's sums, the
 * numbers its text states held to the pipeline's, and that it stays unlisted.
 *
 * 1. FIGURES. bias = sum_e / n and typical error = sum_abs_e / n over the summed days, in feet;
 *    a cell under MIN_PAIRS pairs shows only its count; every cell has its first and last day.
 *    Only the models at the buoys (NWPS, then WAVEWATCH III) are shown: the spots against their
 *    nearest buoy mix shelter with forecast error, and stay in the weekly report.
 * 2. PINNED. The page says pairs are within 30 minutes and a cell needs 50 pairs. Each is held
 *    here to a number typed by hand AND to the pipeline's own (accuracy_score.py OBS_WINDOW,
 *    accuracy_report.py MIN_N), and the page's text must print the constant, not a typed copy of
 *    it. The lead-day definition the page gives is held to migration 030's, and its opening to
 *    the wording agreed for it.
 * 3. UNLISTED. noindex, not in the sitemap, linked from no other page or component.
 *
 * NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST: every figure below is worked by hand.
 *
 *     node --experimental-strip-types frontend/lib/accuracy.test.mts
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  ALL, MIN_PAIRS, PAIR_WINDOW_MINUTES, WINDOW_DAYS, buildTable, fmtBias, fmtCount, fmtFt,
  fmtRange, windowStart, type AccuracyRow,
} from './accuracy.ts';

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}
const near = (a: number | null | undefined, b: number) => a != null && Math.abs(a - b) < 1e-6;

const FRONTEND = dirname(dirname(fileURLToPath(import.meta.url)));
const REPO = dirname(FRONTEND);
const read = (rel: string) => readFileSync(join(FRONTEND, rel), 'utf8');
const readRepo = (rel: string) => readFileSync(join(REPO, rel), 'utf8');

// --------------------------------------------------------------------------- //
// 1. Figures                                                                  //
// --------------------------------------------------------------------------- //
const row = (day: string, kind: string, source: string, region: string, lead_day: number,
  n: number, sum_e: number, sum_abs_e: number): AccuracyRow =>
  ({ day, kind, source, region, lead_day, n, sum_e, sum_abs_e });

const ROWS: AccuracyRow[] = [
  // NWPS at the Florida buoys, under 24 h: two days of 30 pairs.
  row('2026-11-15', 'buoy', 'nwps', 'Florida', 0, 30, 3.0, 9.0),
  row('2026-11-16', 'buoy', 'nwps', 'Florida', 0, 30, -1.2, 6.0),
  // California, under 24 h: 49 pairs, one short.
  row('2026-11-16', 'buoy', 'nwps', 'California', 0, 49, -4.9, 9.8),
  // Exactly 50 pairs: shown.
  row('2026-11-16', 'buoy', 'nwps', 'Maine', 2, 50, 5.0, 10.0),
  // A spot against its buoy: in neither table.
  row('2026-11-16', 'spot', 'spot', 'Oregon', 0, 200, -487.7, 493.8),
  // WAVEWATCH III at a Florida buoy, 1 day ahead: in the ww3 table only.
  row('2026-11-16', 'buoy', 'ww3', 'Florida', 1, 100, 10.0, 20.0),
  // NWPS, three regions out of order, one not in REGION_ORDER.
  row('2026-11-16', 'buoy', 'nwps', 'Zanzibar', 3, 60, 0, 6.0),
  row('2026-11-16', 'buoy', 'nwps', 'Hawaii', 3, 60, 0, 6.0),
  row('2026-11-16', 'buoy', 'nwps', 'Maine', 3, 60, 0, 6.0),
];

const nwps = buildTable(ROWS, 'nwps');
// Florida, under 24 h: n 30 + 30 = 60; sum_e 3.0 - 1.2 = 1.8 m; sum_abs_e 9.0 + 6.0 = 15.0 m.
//   bias 1.8 / 60 = 0.03 m = 0.03 x 3.28084 = 0.0984252 ft -> '+0.1 ft'
//   typical error 15.0 / 60 = 0.25 m = 0.82021 ft -> '0.8 ft'
const fl = nwps.cells.Florida?.[0];
check('Florida day 0: 60 pairs over 15-16 Nov', fl?.n === 60 && fl.first === '2026-11-15' &&
  fl.last === '2026-11-16', JSON.stringify(fl));
check('Florida day 0: bias 0.0984252 ft', near(fl?.biasFt, 0.0984252), String(fl?.biasFt));
check('Florida day 0: typical error 0.82021 ft', near(fl?.maeFt, 0.82021), String(fl?.maeFt));
check("Florida day 0 prints '+0.1 ft' / '0.8 ft'",
  fmtBias(fl!.biasFt!) === '+0.1 ft' && fmtFt(fl!.maeFt!) === '0.8 ft');
// California: 49 pairs, under 50: count only.
const ca = nwps.cells.California?.[0];
check('California day 0: 49 pairs, no figures', ca?.n === 49 && ca.biasFt === null &&
  ca.maeFt === null, JSON.stringify(ca));
// Maine, 2 days ahead: 50 pairs, shown. 5.0 / 50 = 0.1 m = 0.328084 ft; 10.0 / 50 = 0.2 m = 0.656168 ft.
const me = nwps.cells.Maine?.[2];
check('Maine day 2: exactly 50 pairs is shown', near(me?.biasFt, 0.328084) &&
  near(me?.maeFt, 0.656168), JSON.stringify(me));
// All regions, under 24 h: Florida and California. n 60 + 49 = 109; sum_e 1.8 - 4.9 = -3.1;
// sum_abs_e 15.0 + 9.8 = 24.8.
//   bias -3.1 / 109 = -0.0284404 m = -0.0933083 ft -> '−0.1 ft'
//   typical error 24.8 / 109 = 0.2275229 m = 0.7464665 ft -> '0.7 ft'
const all0 = nwps.cells[ALL]?.[0];
check('All regions day 0: 109 pairs, 15-16 Nov', all0?.n === 109 && all0.first === '2026-11-15' &&
  all0.last === '2026-11-16', JSON.stringify(all0));
check('All regions day 0: bias -0.0933083 ft', near(all0?.biasFt, -0.0933083), String(all0?.biasFt));
check('All regions day 0: typical error 0.7464665 ft', near(all0?.maeFt, 0.7464665), String(all0?.maeFt));
check("All regions day 0 prints '−0.1 ft' / '0.7 ft'",
  fmtBias(all0!.biasFt!) === '−0.1 ft' && fmtFt(all0!.maeFt!) === '0.7 ft');
check('the NWPS table has no WAVEWATCH III rows', nwps.cells.Florida?.[1] === undefined);
check('no table has the spots against their buoy', nwps.cells.Oregon === undefined &&
  buildTable(ROWS, 'ww3').cells.Oregon === undefined);

// WAVEWATCH III: 10.0 / 100 = 0.1 m = 0.328084 ft; 20.0 / 100 = 0.2 m = 0.656168 ft.
const ww3 = buildTable(ROWS, 'ww3');
check('WAVEWATCH III Florida day 1: 100 pairs, +0.328084 / 0.656168 ft',
  ww3.cells.Florida?.[1]?.n === 100 && near(ww3.cells.Florida[1].biasFt, 0.328084) &&
  near(ww3.cells.Florida[1].maeFt, 0.656168), JSON.stringify(ww3.cells.Florida));
check('WAVEWATCH III has no NWPS rows', ww3.cells.Florida?.[0] === undefined &&
  ww3.cells.California === undefined);
check('NWPS regions: Maine, Florida, California, Hawaii, then one not listed, then All regions',
  JSON.stringify(nwps.regions) ===
    JSON.stringify(['Maine', 'Florida', 'California', 'Hawaii', 'Zanzibar', ALL]),
  JSON.stringify(nwps.regions));
check('NWPS zero bias prints 0.0 ft', fmtBias(nwps.cells.Maine[3].biasFt!) === '0.0 ft');
check('an empty table has no regions', buildTable([], 'nwps').regions.length === 0);

check("fmtBias(0.25) is '+0.3 ft'", fmtBias(0.25) === '+0.3 ft');
check("fmtBias(-0.04) is '0.0 ft', no sign", fmtBias(-0.04) === '0.0 ft');
check("fmtRange one day: '16 Nov'", fmtRange('2026-11-16', '2026-11-16') === '16 Nov');
check("fmtRange one month: '13–16 Nov'", fmtRange('2026-11-13', '2026-11-16') === '13–16 Nov');
check("fmtRange two months: '28 Nov – 3 Dec'",
  fmtRange('2026-11-28', '2026-12-03') === '28 Nov – 3 Dec');
check("fmtCount: '50', '1,986', '12,345'",
  fmtCount(50) === '50' && fmtCount(1986) === '1,986' && fmtCount(12345) === '12,345');
check('the window is 28 days', WINDOW_DAYS === 28);
check('window starts 28 days before 17 Nov: 20 Oct', windowStart('2026-11-17') === '2026-10-20');

// --------------------------------------------------------------------------- //
// 2. Pinned                                                                   //
// --------------------------------------------------------------------------- //
const score = readRepo('pipeline/accuracy_score.py');
const report = readRepo('pipeline/accuracy_report.py');
check('MIN_PAIRS is 50', MIN_PAIRS === 50);
check('accuracy_report.py MIN_N is 50', /^MIN_N = 50$/m.test(report));
check('PAIR_WINDOW_MINUTES is 30', PAIR_WINDOW_MINUTES === 30);
check('accuracy_score.py OBS_WINDOW is 30 minutes',
  /^OBS_WINDOW = timedelta\(minutes=30\)$/m.test(score));
check("migration 030's lead day is floor((valid_time - issued_at) / 24 h)",
  readRepo('pipeline/migrations/030_accuracy_capture.sql')
    .includes('lead_day = floor((valid_time - issued_at) / 24 h)'));
check('the bias and MAE are the report\'s: sum_e / n and sum_abs_e / n',
  report.includes('"bias": s["sum_e"] / n, "mae": s["sum_abs_e"] / n'));

const page = read('app/accuracy/page.tsx');
check('the page prints the pairing window from the constant',
  page.includes('{PAIR_WINDOW_MINUTES} minutes') && !/\b30 minutes\b/.test(page));
check('the page prints the minimum pairs from the constant',
  page.includes('{MIN_PAIRS} pairs') && !/\b50 pairs\b/.test(page));
const flat = page.replace(/\s+/g, ' ');
check('the title is "How accurate are our forecasts?", in <title> and the heading',
  page.includes("title: { absolute: 'How accurate are our forecasts? | Stormy Petrel' }") &&
  page.includes('How accurate are our forecasts?\n        </h1>'));
check('the page opens with the agreed paragraph', flat.includes(
  '<p> The buoy box on each spot page shows what a buoy is measuring right now. A forecast is ' +
  'different: it predicts the waves hours or days ahead. Every night we go back and check each ' +
  'forecast hour: what we predicted, and what the buoy actually measured when that hour ' +
  'arrived. </p>') && flat.indexOf('<p> The buoy box') < flat.indexOf('<p>'.concat(' Our forecasts')));
const caveat = flat.indexOf('Buoys measure waves offshore, not surf at the beach.');
check('the offshore-not-beach caveat follows the opening', caveat > flat.indexOf('<p> The buoy box'));
check('the tables are NWPS, then WAVEWATCH III, and no spot table',
  page.indexOf("key: 'nwps'") > page.indexOf('const TABLES') &&
  page.indexOf("key: 'ww3'") > page.indexOf("key: 'nwps'") && !page.includes("key: 'spot'") &&
  !/nearest buoy/i.test(page.replace(/^\/\/.*$/gm, '')));
check('the page prints the window from the constant',
  page.includes('{WINDOW_DAYS} days') && !/\b28 days\b/.test(page));
check("the page's lead labels say 24 to 48 hours for 1 day ahead",
  page.includes('&ldquo;1 day ahead&rdquo; was made 24 to 48 hours before'));
const lib = read('lib/accuracy.ts');
check('the read is the models at the buoys, region totals of wave height only',
  lib.includes(".eq('kind', 'buoy')") && lib.includes(".eq('point', '')") &&
  lib.includes(".eq('measure', 'hs')"));
check('the read is the uncached client, by that name, and nothing else from lib/supabase',
  lib.includes("const { supabaseUncached } = await import('./supabase');") &&
  (lib.match(/['"]\.\/supabase['"]|@\/lib\/supabase/g) ?? []).length === 1);
check("migration 031: source 'spot' is kind 'spot', and ww3 and nwps are kind 'buoy'",
  readRepo('pipeline/migrations/031_accuracy_daily.sql').includes(
    "kind 'buoy', source 'ww3' or 'nwps'") &&
  readRepo('pipeline/migrations/031_accuracy_daily.sql').includes("kind 'spot', source 'spot'"));
check('the read pages past 1,000 rows', lib.includes('.range(from, from + PAGE - 1)'));

// --------------------------------------------------------------------------- //
// 3. Unlisted                                                                 //
// --------------------------------------------------------------------------- //
check('the page is noindex, nofollow, for Googlebot too',
  /robots:\s*\{\s*index:\s*false,\s*follow:\s*false,\s*googleBot:\s*\{\s*index:\s*false,\s*follow:\s*false\s*\}/
    .test(page));
check('the page is not in the sitemap', !read('app/sitemap.ts').includes('accuracy'));
check('robots.txt does not block it (a blocked page cannot be seen to be noindex)',
  !read('app/robots.ts').includes('accuracy'));
function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}
// A link or route string: "/accuracy", '/accuracy', `/accuracy`, or a Markdown (/accuracy).
const LINK = /["'`(]\/accuracy\b/;
const linking = ['app', 'components', 'lib', 'content']
  .flatMap((d) => walk(join(FRONTEND, d)))
  .map((p) => relative(FRONTEND, p))
  .filter((p) => !p.startsWith('app/accuracy/') && !p.startsWith('lib/accuracy.'))
  .filter((p) => /\.(tsx?|mts|mdx?)$/.test(p))
  .filter((p) => LINK.test(read(p)));
check('no other page, component, Learn article or blog post links to /accuracy',
  linking.length === 0, linking.join(', '));
check('the link check finds a link written as the nav writes one',
  LINK.test("{ href: '/accuracy', label: 'Accuracy' }") && LINK.test('[scores](/accuracy)') &&
  !LINK.test('lib/accuracy.ts'));

if (failures) {
  console.log(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log('\naccuracy: ALL PASS');
