/**
 * No literal forecast length may appear in copy. The one number lives in forecastClaim.ts.
 *
 * WHAT WENT WRONG. The site said "7-day" in nine places — page description, Open Graph,
 * Twitter, JSON-LD, the grid heading, its button, two empty states, a blog post — while the
 * rated forecast ends at the NWPS cycle's f144. The claim and the feed drifted apart because
 * the number was a literal repeated across files, and nothing noticed. This test is what
 * notices: it fails on a stale "7-day" or any of its variants, and on ANY length written
 * out by hand — "5-day grid", "next 5 days" — so a literal "5-day" is as illegal as a
 * literal "7-day". The length in copy has to come from CLAIMED_FORECAST_DAYS.
 *
 * IT READS COPY, NOT SOURCE, through the scanner in copyScan.ts, which the update-cadence
 * guard shares. Every .ts/.tsx file is parsed with the TypeScript compiler and
 * only reader-facing text is checked: string literals, template text and JSX text. Comments
 * never become nodes, so a comment may say whatever it likes — which matters, because
 * page.tsx documents a query limit sized for "a 7-day horizon" and that sizing is a real
 * assumption to report, not copy to police. Markdown in content/ is prose throughout and is
 * read whole.
 *
 * TEMPLATES AND JSX ARE REASSEMBLED, so a split literal cannot slip through. `next {7} days`
 * and `${'7'}-day` are both caught: literal expressions are inlined, anything else stands
 * as "…", which is what makes `Free ${CLAIMED_FORECAST_LABEL} surf forecast` pass.
 *
 * THE SELF-REFERENCE TRAP. This file has to contain the very phrases it forbids — the
 * patterns, and the fixtures that prove the patterns work. So test files are never scanned,
 * and the exclusion is shown to be load-bearing below: fed to the same scanner, this file's
 * own source DOES produce findings. The #217 grep, the selftest-wiring scan and the CI
 * workflow test all tripped on a literal in their own prose; this one is built not to.
 *
 *     node --experimental-strip-types frontend/lib/forecastClaim.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  copyOf, copyStrings, definitionsOf, isTestFile, references, scannedFiles, walkedFiles,
} from './copyScan.ts';
import { CLAIMED_FORECAST_DAYS, CLAIMED_FORECAST_LABEL } from './forecastClaim.ts';

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

// --------------------------------------------------------------------------- //
// The rules                                                                   //
// --------------------------------------------------------------------------- //
// STALE: the old claim in every spelling the inventory found or the brief named. Bare
// "week" is deliberately NOT here — learn/tides says "spring weeks" and the date formatters
// use `weekday`, both legitimately. Only phrasing that states a LENGTH is.
const STALE: RegExp[] = [
  /\b(?:7|seven)[\s-]?days?\b/i,        // 7-day, 7 day, 7 days, seven-day, ~7 days, next 7 days
  /\b(?:a|one|full|whole)\s+week\b/i,   // a week, one week, full week
  /\bweek[\s-]?long\b/i,                // week-long, weeklong
  /\bweek\s+ahead\b/i,
];
// LITERAL: ANY length written out by hand. The length in copy must come from the setting, so
// these reject the CURRENT claim spelled as a literal ("5-day grid", "next 5 days") as firmly
// as the stale one. They stop short of plain "N days", which general prose uses legitimately:
// learn/forecasts grades skill at "72 hours (3 days)", and reports cover "the next 2-3 days".
const N = String.raw`(?:\d+|one|two|three|four|five|six|seven|eight|nine|ten)`;
const LITERAL: RegExp[] = [
  new RegExp(String.raw`\b${N}-day\b`, 'i'),                                  // 5-day anything
  new RegExp(String.raw`\b${N}\s+day\s+(?:surf\s+)?forecasts?\b`, 'i'),       // 5 day forecast
  new RegExp(String.raw`\bnext\s+${N}\s+days\b`, 'i'),                         // next 5 days
];

type Finding = { file: string; line: number; text: string; rule: string };

function violations(file: string, line: number, text: string): Finding[] {
  const out: Finding[] = [];
  const hit = (rule: string) => out.push({ file, line, text: text.trim().slice(0, 120), rule });
  for (const re of STALE) if (re.test(text)) hit(`stale ${re}`);
  for (const re of LITERAL) if (re.test(text)) hit(`literal length ${re}`);
  return out;
}

// --------------------------------------------------------------------------- //
// Copy extraction lives in copyScan.ts, shared with the update-cadence guard.  //
// --------------------------------------------------------------------------- //
function scanFile(rel: string): Finding[] {
  return copyOf(rel, readFileSync(join(FRONTEND, rel), 'utf8'))
    .flatMap((s) => violations(rel, s.line, s.text));
}

function fixture(src: string, name = 'fixture.tsx'): Finding[] {
  return copyStrings(name, src).flatMap((s) => violations(name, s.line, s.text));
}

// --------------------------------------------------------------------------- //
// 1 — the extractor works. A scanner that silently finds nothing would pass   //
//     every check below, so it is exercised on hand-written input first.      //
// --------------------------------------------------------------------------- //
check('JSX text with the stale length is caught',
  fixture('const A = () => <p>Show full 7-day forecast</p>;').length > 0);
check('a JSX attribute string is caught',
  fixture('const A = () => <H title="7-day forecast" />;').length > 0);
check('a template literal is caught',
  fixture('const d = `Free 7-day surf forecast for ${name}`;').length > 0);
check('a number split into its own JSX expression is still caught',
  fixture('const A = () => <p>No data in the next {7} days.</p>;').length > 0);
check('a number split into its own template span is still caught',
  fixture("const d = `Free ${'7'}-day surf forecast`;").length > 0);
for (const t of ['seven-day outlook', 'the next 7 days', '~7 days out', 'a week of surf',
                 'week-long view', 'the week ahead']) {
  check(`the stale spelling ${JSON.stringify(t)} is caught`,
    fixture(`const s = ${JSON.stringify(t)};`).length > 0);
}
for (const t of ['Free 5-day surf forecast', '5 day forecast', 'the 5-day grid below',
                 'No forecast data in the next 5 days.']) {
  check(`a hardcoded CURRENT length is caught too: ${JSON.stringify(t)}`,
    fixture(`const A = () => <p>${t}</p>;`).length > 0);
}

check('a line comment is not copy',
  fixture('// the 7-day grid lives below\nconst x = 1;').length === 0);
check('a block comment is not copy',
  fixture('/* sized for a 7-day horizon */\nconst x = 1;').length === 0);
check('a JSX comment is not copy',
  fixture('const A = () => <div>{/* 7-day grid */}<p>ok</p></div>;').length === 0);
check('the setting, used the way the site uses it, passes',
  fixture('const d = `Free ${CLAIMED_FORECAST_LABEL} surf forecast for ${n}`;'
    + 'const A = () => <p>Show full {CLAIMED_FORECAST_LABEL} forecast. None in the next '
    + '{CLAIMED_FORECAST_DAYS} days.</p>;').length === 0);
check('an import path is not copy, even one that names a length',
  fixture("import { a } from './seven-day-helpers';\nexport { b } from './5-day';").length === 0);
check('legitimate week words pass: spring weeks, weekday, weekly',
  fixture('const a = "spring weeks"; const b = { weekday: "short" }; const c = "weekly";').length === 0);
check('general prose passes: "7 to 10 days", "72 hours (3 days)", "the next 2-3 days"',
  fixture('const A = () => <p><b>7 to 10 days:</b> vibes. <b>72 hours (3 days):</b> solid. '
    + 'The trend for the next 2-3 days.</p>;').length === 0);

// --------------------------------------------------------------------------- //
// 2 — the self-reference trap is real, and the exclusion is what avoids it      //
// --------------------------------------------------------------------------- //
check('the exclusion recognises test files in every extension the walk reads',
  ['lib/a.test.ts', 'lib/a.test.tsx', 'lib/a.test.mts', 'lib/a.test.cts'].every(isTestFile)
  && !['lib/a.ts', 'lib/a.mts', 'components/Tested.tsx', 'lib/contest.ts'].some(isTestFile));
const SELF = relative(FRONTEND, fileURLToPath(import.meta.url));
check('this file would be flagged if it were scanned — the exclusion is load-bearing',
  copyStrings(SELF, readFileSync(fileURLToPath(import.meta.url), 'utf8'))
    .some((s) => violations(SELF, s.line, s.text).length > 0));
check('the walk reaches this very file, so it is the exclusion — not the extension — that keeps it out',
  walkedFiles(FRONTEND).includes(SELF));
const files = scannedFiles(FRONTEND);
check('no test file is scanned, this one included',
  !files.some(isTestFile) && !files.includes(SELF), files.filter(isTestFile).join(', '));

// --------------------------------------------------------------------------- //
// 3 — the scan really covers the site                                          //
// --------------------------------------------------------------------------- //
const MUST_COVER = [
  'app/spot/[slug]/page.tsx',
  'components/ForecastGrid.tsx',
  'components/CurrentConditions.tsx',
  'content/blog/understanding-forecasts.md',
  'lib/forecastClaim.ts',
];
check('every known copy site is in the scan',
  MUST_COVER.every((f) => files.includes(f)),
  MUST_COVER.filter((f) => !files.includes(f)).join(', '));
check('the scan is not trivially small', files.length >= 40, `${files.length} files`);

// --------------------------------------------------------------------------- //
// 4 — THE GUARD: no literal length anywhere in the site's copy                  //
// --------------------------------------------------------------------------- //
const found = files.flatMap(scanFile);
check('no copy states a forecast length except through CLAIMED_FORECAST_DAYS',
  found.length === 0,
  found.map((f) => `\n        ${f.file}:${f.line}  [${f.rule}]  ${JSON.stringify(f.text)}`).join(''));

// --------------------------------------------------------------------------- //
// 5 — one definition, in a shape the pipeline's future guarantee test can read  //
// --------------------------------------------------------------------------- //
const definitions = definitionsOf(FRONTEND, 'CLAIMED_FORECAST_DAYS');
check('CLAIMED_FORECAST_DAYS is defined exactly once, in lib/forecastClaim.ts',
  definitions.length === 1 && definitions[0] === 'lib/forecastClaim.ts', definitions.join(', '));
const claimSrc = readFileSync(join(HERE, 'forecastClaim.ts'), 'utf8');
check('it is declared on one line as a plain integer, so Python can read it without a TS parser',
  (claimSrc.match(/^export const CLAIMED_FORECAST_DAYS = \d+;$/gm) ?? []).length === 1);
check('the value is a positive whole number of days',
  Number.isInteger(CLAIMED_FORECAST_DAYS) && CLAIMED_FORECAST_DAYS >= 1, String(CLAIMED_FORECAST_DAYS));
check('the label is that number followed by "-day"',
  CLAIMED_FORECAST_LABEL === `${CLAIMED_FORECAST_DAYS}-day`, CLAIMED_FORECAST_LABEL);

// --------------------------------------------------------------------------- //
// 6 — the copy sites read the setting, rather than merely not spelling a length //
// --------------------------------------------------------------------------- //
check('the spot page reads the label for its description, JSON-LD and heading',
  references(FRONTEND, 'app/spot/[slug]/page.tsx', 'CLAIMED_FORECAST_LABEL') >= 3);
check('the grid reads the setting for its button and its empty state',
  references(FRONTEND, 'components/ForecastGrid.tsx', 'CLAIMED_FORECAST_LABEL') >= 1
  && references(FRONTEND, 'components/ForecastGrid.tsx', 'CLAIMED_FORECAST_DAYS') >= 1);
check('the absent-hour notice reads the label',
  references(FRONTEND, 'components/CurrentConditions.tsx', 'CLAIMED_FORECAST_LABEL') >= 1);

if (failures > 0) {
  throw new Error(`forecastClaim: ${failures} FAILURE(S)`);
}
console.log('\nforecastClaim: ALL PASS');
