/**
 * No spot count may be written into copy by hand. The one number lives in siteDescription.ts,
 * and it is the pipeline's roster.
 *
 * WHAT WENT WRONG. The site description, its Open Graph and Twitter copies and the share image
 * said "~500 US spots"; the home and map page titles, their descriptions and the search box said
 * 484; an unused search bar said "500+". The pipeline rates 646. Every one was a literal, so every
 * one drifted on its own, and nothing noticed. Three of the descriptions also listed NOAA's feeds
 * without CO-OPS, whose tide predictions go into every rating.
 *
 * WHAT IS HELD HERE.
 *   1. RATED_SPOT_COUNT is the number of spots in pipeline/spots_enriched.json that the pipeline
 *      rates (is_valid_surf_spot not false — the rule interpret and db_import both apply).
 *   2. It is defined once, on one line, as a plain integer.
 *   3. No copy anywhere states a count of spots in three or more digits except through it. The
 *      scanner is copyScan.ts, shared with the forecast-length and cadence guards, so comments
 *      may say what they like; subset counts ("48 California spots", "130 calibrated
 *      California spots") are two digits or qualified, and pass.
 *   4. The copy sites read it, and the descriptions that list NOAA's feeds read NOAA_SOURCES,
 *      which names every NOAA feed the footer credits, CO-OPS included.
 *
 * NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST: the count comes from the pipeline's file, the
 * feed names are written out here.
 *
 *     node --experimental-strip-types frontend/lib/siteDescription.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  copyOf, copyStrings, definitionsOf, isTestFile, references, scannedFiles, walkedFiles,
} from './copyScan.ts';
import { NOAA_SOURCES, RATED_SPOT_COUNT } from './siteDescription.ts';

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
// 1 — the number is the roster's                                               //
// --------------------------------------------------------------------------- //
const roster = JSON.parse(read('../pipeline/spots_enriched.json')) as { is_valid_surf_spot?: boolean }[];
const rated = roster.filter((s) => s.is_valid_surf_spot !== false).length;
check(`RATED_SPOT_COUNT is the number of spots the pipeline rates (${rated})`,
  RATED_SPOT_COUNT === rated, `setting ${RATED_SPOT_COUNT}, roster ${rated}`);
check('...which is not the whole file: the filter is doing something',
  rated < roster.length, `${rated} of ${roster.length}`);

// --------------------------------------------------------------------------- //
// 2 — one definition, in a shape anything can read                             //
// --------------------------------------------------------------------------- //
const defs = definitionsOf(FRONTEND, 'RATED_SPOT_COUNT');
check('RATED_SPOT_COUNT is defined exactly once, in lib/siteDescription.ts',
  defs.length === 1 && defs[0] === 'lib/siteDescription.ts', defs.join(', '));
check('it is declared on one line as a plain integer',
  (read('lib/siteDescription.ts').match(/^export const RATED_SPOT_COUNT = \d+;$/gm) ?? []).length === 1);

// --------------------------------------------------------------------------- //
// 3 — no count written into copy by hand                                       //
// --------------------------------------------------------------------------- //
// Three or more digits, then "spots" with at most US / surf / rated between: "484 US Spots",
// "~500 US spots", "Search 484 spots", "500+ spots". Plural only, so "84,774 spot-hours" (a
// measurement in the methodology post) is not a count of spots.
const COUNT = /\b\d{3,}\+?\s+(?:(?:US|U\.S\.|surf|rated)\s+)*spots\b/i;

function fixture(src: string, name = 'fixture.tsx'): string[] {
  return copyStrings(name, src).filter((s) => COUNT.test(s.text)).map((s) => s.text);
}
for (const t of ['Free Surf Forecasts for 484 US Spots', '~500 US spots · NOAA-powered',
                 'Search 484 spots — Mavericks', 'Search 500+ spots...', 'map of 484 US surf spots',
                 'forecasts for 646 US spots']) {
  check(`a hand-written count is caught: ${JSON.stringify(t)}`, fixture(`const s = ${JSON.stringify(t)};`).length === 1);
}
check('...in JSX text too, and split into its own expression',
  fixture('const A = () => <p>{500} US spots</p>;').length === 1);
check('the setting, used the way the site uses it, passes',
  fixture('const d = `Free surf forecasts for ${RATED_SPOT_COUNT} US spots.`;'
    + 'const A = () => <p>{`${RATED_SPOT_COUNT} US spots · NOAA-powered`}</p>;').length === 0);
check('subset counts and a measurement pass',
  fixture('const a = "CDIP MOP at 48 California spots"; const b = "the 130 calibrated California spots";'
    + 'const c = "across 84,774 spot-hours"; const d = "48 MOP spots";').length === 0);
check('a comment is not copy', fixture('// ~500 US spots\n/* 484 spots */\nconst x = 1;').length === 0);

const SELF = relative(FRONTEND, fileURLToPath(import.meta.url));
check('this file would be flagged if it were scanned — the test-file exclusion is load-bearing',
  copyStrings(SELF, read(SELF)).some((s) => COUNT.test(s.text)));
check('the walk reaches this file, so it is the exclusion that keeps it out',
  walkedFiles(FRONTEND).includes(SELF) && !scannedFiles(FRONTEND).includes(SELF) && isTestFile(SELF));

const found = scannedFiles(FRONTEND).flatMap((rel) => copyOf(rel, read(rel))
  .filter((s) => COUNT.test(s.text))
  .map((s) => `${rel}:${s.line}  ${JSON.stringify(s.text.trim().slice(0, 100))}`));
check('no copy states a spot count except through RATED_SPOT_COUNT',
  found.length === 0, found.map((f) => `\n        ${f}`).join(''));

// --------------------------------------------------------------------------- //
// 4 — the copy sites read the settings                                         //
// --------------------------------------------------------------------------- //
const READERS: [string, number][] = [
  ['app/layout.tsx', 3],                 // description, Open Graph, Twitter
  ['app/page.tsx', 2],                   // title, Open Graph title
  ['app/map/page.tsx', 4],               // title, description, Open Graph title and description
  ['app/opengraph-image.tsx', 1],        // the share image
  ['components/HeroSearch.tsx', 1],      // the home page search box
];
for (const [file, n] of READERS) {
  const got = references(FRONTEND, file, 'RATED_SPOT_COUNT');
  check(`${file} reads RATED_SPOT_COUNT at least ${n} time(s)`, got >= n, `${got}`);
}

const NOAA = ['NWPS', 'WAVEWATCH III', 'HRRR', 'NDBC', 'CO-OPS'];
check(`NOAA_SOURCES names every NOAA feed the footer credits: ${NOAA.join(', ')}`,
  NOAA.every((n) => NOAA_SOURCES.includes(n)), NOAA_SOURCES);
for (const [file, n] of [['app/layout.tsx', 1], ['app/page.tsx', 2]] as [string, number][]) {
  const got = references(FRONTEND, file, 'NOAA_SOURCES');
  check(`${file} lists the feeds through NOAA_SOURCES (${n}+)`, got >= n, `${got}`);
}

if (failures > 0) {
  throw new Error(`siteDescription: ${failures} FAILURE(S)`);
}
console.log('\nsiteDescription: ALL PASS');
