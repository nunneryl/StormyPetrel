/**
 * No update cadence may be claimed in copy except through lib/updateCadence.ts.
 *
 * WHAT WENT WRONG. The spot page description and its JSON-LD said "updated every 6 hours", the
 * About page said "Every six hours, we pull fresh data", the methodology post had a "6-hour
 * refresh latency" with buoys that "refresh hourly", and the footer said "every 8 h · buoys
 * every 3 h". None of it matched the pipeline: forecasts landed 5.4-11.4 hours apart and the
 * hourly buoy job ran in about a quarter of its hours. Five hand-written numbers, three
 * different claims, no owner. The words now live in one setting, and this test fails on a
 * cadence written into copy by hand — including the setting's own words ("several times a
 * day", "throughout the day") typed out anywhere but the setting.
 *
 * THE SAME SCANNER AS THE FORECAST-LENGTH GUARD. Copy is read through copyScan.ts: string
 * literals, template text and JSX text, reassembled, with comments ignored; markdown whole.
 * Only the rules below are this file's own.
 *
 * MODEL FACTS ARE NOT OUR CADENCE, AND ARE LISTED BY NAME. The learn pages and the
 * methodology post state true things about the MODELS and the ocean — GFS and ECMWF run "four
 * times a day", HRRR "updates every hour", GFS-derived wind "only updates every 6 hours", a
 * buoy reports "every 20 minutes", the tide turns "twice per day". Nothing in the
 * shape of a sentence tells "HRRR updates every hour" from "the forecast updates every hour",
 * so no pattern can let one through and stop the other. They are allowed by exact phrase, one
 * reviewed entry each, and an entry that stops matching anything fails too, so the list
 * cannot silently outlive its text. A NEW cadence phrase anywhere fails until it reads the
 * setting or earns an entry here with a reason.
 *
 * "hourly" AS A RESOLUTION IS NOT A CADENCE. "the hourly wind forecast", "3 km × hourly
 * resolution" and the sitemap's changeFrequency: 'hourly' describe the data's time step or a
 * crawler hint, not how often we update. Only "refresh/update/pull … hourly" is a claim.
 *
 * THE SELF-REFERENCE TRAP, as in forecastClaim.test.mts: this file contains every phrase it
 * forbids, so test files are never scanned, and the exclusion is shown to be load-bearing.
 *
 *     node --experimental-strip-types frontend/lib/updateCadence.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  copyOf, copyStrings, definitionsOf, isTestFile, references, scannedFiles, walkedFiles,
} from './copyScan.ts';
import { BUOY_UPDATES, FORECAST_UPDATES, fillCopyTokens } from './updateCadence.ts';

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
const SETTING = 'lib/updateCadence.ts';

// --------------------------------------------------------------------------- //
// The rules                                                                   //
// --------------------------------------------------------------------------- //
const COUNT = String.raw`(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|twenty-four|half(?:\s+an)?|a\s+few|several)`;
const CADENCE: RegExp[] = [
  // every 6 hours, every six hours, every 8 h, every hour, every 30 minutes, every half hour
  new RegExp(String.raw`\bevery\s+(?:${COUNT}\s*-?\s*)?(?:h|hrs?|hours?|mins?|minutes?)\b`, 'gi'),
  // four times a day, several times a day — and the setting's own words typed by hand
  new RegExp(String.raw`\b(?:${COUNT}|many|multiple)\s+times\s+(?:a|per|each)\s+day\b`, 'gi'),
  /\b(?:twice|once|thrice)\s+(?:a|per|each)\s+day\b/gi,
  /\bthroughout\s+the\s+day\b/gi,
  // "hourly" as a CADENCE: refresh hourly, updated hourly, hourly updates
  /\b(?:refresh|update|pull|fetch|poll|check|run)(?:e?s|e?d|ing)?\s+hourly\b/gi,
  /\bhourly\s+(?:refresh|update|pull|fetch|poll)/gi,
  // the 6-hour refresh latency
  new RegExp(String.raw`\b${COUNT}[\s-]hours?\s+(?:refresh|update|cadence|latency|lag)`, 'gi'),
];

// True statements about the MODELS, allowed by exact phrase. Each must keep matching.
const MODEL_FACTS: { file: string; phrase: string; why: string }[] = [
  { file: 'app/learn/forecasts/page.tsx', phrase: 'run on supercomputers four times a day',
    why: 'GFS and ECMWF run cycles, not our updates' },
  { file: 'app/learn/forecasts/page.tsx', phrase: 'a 3-km model that updates every hour',
    why: "HRRR's own cycle" },
  { file: 'app/learn/wind/page.tsx', phrase: 'updates every hour out to 18 hours',
    why: "HRRR's own cycle and horizon" },
  { file: 'content/blog/methodology.md', phrase: 'only updates every 6 hours',
    why: 'GFS-derived wind, the reason the pipeline moved to HRRR' },
  { file: 'app/learn/buoys/page.tsx', phrase: 'Every 20 minutes, it sends a summary',
    why: "an NDBC buoy's own reporting interval" },
  { file: 'app/learn/tides/page.tsx', phrase: 'high and low water roughly twice per day',
    why: 'the tide itself, not an update' },
];

type Finding = { file: string; line: number; text: string; match: string; rule: string };

/** Every cadence match in one piece of copy, with where it sits in that text. */
function matches(text: string): { index: number; match: string; rule: string }[] {
  const out: { index: number; match: string; rule: string }[] = [];
  for (const re of CADENCE) {
    for (const m of text.matchAll(re)) out.push({ index: m.index ?? 0, match: m[0], rule: String(re) });
  }
  return out;
}

/** Is this match inside a listed model-fact phrase, in the file that entry names? */
function modelFact(file: string, text: string, index: number, match: string): number {
  return MODEL_FACTS.findIndex((f) => {
    if (f.file !== file) return false;
    for (let at = text.indexOf(f.phrase); at >= 0; at = text.indexOf(f.phrase, at + 1)) {
      if (at <= index && index + match.length <= at + f.phrase.length) return true;
    }
    return false;
  });
}

const usedFacts = new Set<number>();

function violations(file: string, line: number, text: string): Finding[] {
  // The setting's own two strings are the one place these words may be written out.
  if (file === SETTING && (text === FORECAST_UPDATES || text === BUOY_UPDATES)) return [];
  const out: Finding[] = [];
  for (const m of matches(text)) {
    const fact = modelFact(file, text, m.index, m.match);
    if (fact >= 0) {
      usedFacts.add(fact);
      continue;
    }
    out.push({ file, line, text: text.trim().slice(0, 140), match: m.match, rule: m.rule });
  }
  return out;
}

function scanFile(rel: string): Finding[] {
  return copyOf(rel, readFileSync(join(FRONTEND, rel), 'utf8'))
    .flatMap((s) => violations(rel, s.line, s.text));
}

function fixture(src: string, name = 'fixture.tsx'): Finding[] {
  return copyStrings(name, src).flatMap((s) => violations(name, s.line, s.text));
}

// --------------------------------------------------------------------------- //
// 1 — the rules catch every stale claim, and the setting's words typed by hand //
// --------------------------------------------------------------------------- //
for (const t of [
  'Wave height, swell direction, period, wind, and tide — updated every 6 hours.',
  'Every six hours, we pull fresh data from NOAA',
  'Forecasts refresh every 8 h',
  'buoys every 3 h',
  'The 6-hour refresh latency.',
  'Forecasts update every 6 hours',
  'they refresh hourly and we display the latest reading',
  'updated every hour', 'refreshed every 30 minutes', 'updated twice a day',
  'Buoys: hourly updates.',
]) {
  check(`a hand-written cadence is caught: ${JSON.stringify(t)}`,
    fixture(`const A = () => <p>${t}</p>;`).length > 0);
}
for (const t of [FORECAST_UPDATES, BUOY_UPDATES]) {
  check(`the setting's own words, typed out by hand, are caught: ${JSON.stringify(t)}`,
    fixture(`const s = ${JSON.stringify(`Forecasts ${t}.`)};`).length > 0);
  // ...even as a bare copy of the exact value: only the setting FILE may hold that string
  check(`an exact copy of the setting's value outside the setting is caught: ${JSON.stringify(t)}`,
    fixture(`const s = ${JSON.stringify(t)};`, 'components/Elsewhere.tsx').length > 0);
}
check('a cadence split into template spans is still caught',
  fixture("const d = `updated every ${'6'} hours`;").length > 0);

// ...and lets through what is not a claim about our cadence
check('"hourly" as a time step or a crawler hint passes',
  fixture('const A = () => <p>Check the hourly wind forecast, 3 km × hourly resolution.</p>;'
    + "const s = { changeFrequency: 'hourly' };").length === 0);
check('the setting, used the way the site uses it, passes',
  fixture('const d = `Wave height and tide — ${FORECAST_UPDATES}.`;'
    + 'const A = () => <p>Forecasts {FORECAST_UPDATES} · buoys {BUOY_UPDATES}</p>;').length === 0);
check('a comment is not copy',
  fixture('// forecasts land every 6 hours, give or take\nconst x = 1;').length === 0);
check('a markdown token is not a claim — it is the setting, filled in at render',
  violations('content/blog/x.md', 1, 'Forecasts are {{FORECAST_UPDATES}}; buoys {{BUOY_UPDATES}}.')
    .length === 0);
check('a model fact is allowed only in the file its entry names',
  fixture('const A = () => <p>HRRR is a 3-km model that updates every hour.</p>;').length > 0);
check('a new claim inside an allowed sentence is still caught',
  violations('content/blog/methodology.md', 47,
    'GFS-derived wind only updates every 6 hours, and our forecasts refresh hourly.')
    .map((f) => f.match).join('|') === 'refresh hourly');

// --------------------------------------------------------------------------- //
// 2 — the self-reference trap is real, and the exclusion is what avoids it      //
// --------------------------------------------------------------------------- //
const SELF = relative(FRONTEND, fileURLToPath(import.meta.url));
check('this file would be flagged if it were scanned — the exclusion is load-bearing',
  isTestFile(SELF) && copyStrings(SELF, readFileSync(fileURLToPath(import.meta.url), 'utf8'))
    .some((s) => violations(SELF, s.line, s.text).length > 0));
check('the walk reaches this very file, so it is the exclusion that keeps it out',
  walkedFiles(FRONTEND).includes(SELF));
const files = scannedFiles(FRONTEND);
check('no test file is scanned, this one included',
  !files.some(isTestFile) && !files.includes(SELF));

// --------------------------------------------------------------------------- //
// 3 — the scan really covers the site                                          //
// --------------------------------------------------------------------------- //
const MUST_COVER = [
  'app/spot/[slug]/page.tsx', 'app/about/page.tsx', 'components/SiteFooter.tsx',
  'content/blog/methodology.md', SETTING,
];
check('every known cadence site is in the scan', MUST_COVER.every((f) => files.includes(f)),
  MUST_COVER.filter((f) => !files.includes(f)).join(', '));
check('the scan is not trivially small', files.length >= 40, `${files.length} files`);

// --------------------------------------------------------------------------- //
// 4 — THE GUARD: no cadence claimed anywhere except through the setting         //
// --------------------------------------------------------------------------- //
usedFacts.clear();
const found = files.flatMap(scanFile);
check('no copy claims an update cadence except through lib/updateCadence.ts',
  found.length === 0,
  found.map((f) => `\n        ${f.file}:${f.line}  "${f.match}"  in ${JSON.stringify(f.text)}`).join(''));
const stale = MODEL_FACTS.filter((_f, i) => !usedFacts.has(i));
check('every model-fact entry still matches its sentence, so the list cannot outlive its text',
  stale.length === 0, stale.map((f) => `${f.file}: ${JSON.stringify(f.phrase)}`).join('; '));

// --------------------------------------------------------------------------- //
// 5 — one definition each                                                      //
// --------------------------------------------------------------------------- //
for (const name of ['FORECAST_UPDATES', 'BUOY_UPDATES']) {
  const defs = definitionsOf(FRONTEND, name);
  check(`${name} is defined exactly once, in ${SETTING}`,
    defs.length === 1 && defs[0] === SETTING, defs.join(', '));
}

// --------------------------------------------------------------------------- //
// 6 — the copy sites read the setting, rather than merely not stating a number  //
// --------------------------------------------------------------------------- //
check('the spot page reads it for its description and its JSON-LD',
  references(FRONTEND, 'app/spot/[slug]/page.tsx', 'FORECAST_UPDATES') >= 2);
for (const rel of ['app/about/page.tsx', 'components/SiteFooter.tsx']) {
  check(`${rel} reads both settings`,
    references(FRONTEND, rel, 'FORECAST_UPDATES') >= 1 && references(FRONTEND, rel, 'BUOY_UPDATES') >= 1);
}
const methodology = readFileSync(join(FRONTEND, 'content/blog/methodology.md'), 'utf8');
check('the methodology post reads both settings through their tokens',
  methodology.includes('{{FORECAST_UPDATES}}') && methodology.includes('{{BUOY_UPDATES}}'));
check('the blog loader fills tokens for the post list and for each post',
  references(FRONTEND, 'lib/blog.ts', 'fillCopyTokens') >= 2);

// --------------------------------------------------------------------------- //
// 7 — the markdown tokens are filled, and a typo cannot reach a reader          //
// --------------------------------------------------------------------------- //
check('a token is replaced by its setting',
  fillCopyTokens('Forecasts are {{FORECAST_UPDATES}}; buoys arrive {{ BUOY_UPDATES }}.')
    === `Forecasts are ${FORECAST_UPDATES}; buoys arrive ${BUOY_UPDATES}.`);
check('text without tokens is untouched', fillCopyTokens('a {b} c') === 'a {b} c');
let threw = '';
try { fillCopyTokens('x {{FORCAST_UPDATES}} y'); } catch (e) { threw = String(e); }
check('an unknown token throws instead of rendering', threw.includes('{{FORCAST_UPDATES}}'), threw);
for (const rel of files.filter((f) => f.startsWith('content/') && f.endsWith('.md'))) {
  let out = '';
  try { out = fillCopyTokens(readFileSync(join(FRONTEND, rel), 'utf8')); } catch (e) { out = String(e); }
  check(`${rel} fills cleanly, with no token left over`, !/\{\{|\}\}/.test(out));
}

if (failures > 0) {
  throw new Error(`updateCadence: ${failures} FAILURE(S)`);
}
console.log('\nupdateCadence: ALL PASS');
