/**
 * Every published height says what it is, and a model estimate is never called anything else.
 *
 * WHAT WENT WRONG. 505 of the 646 rated spots publish an uncalibrated model estimate — boosted
 * for long-period swell, about 1.6 times MOP's at the California spots where the two were
 * compared — and the 141 calibrated spots publish a height scaled to CDIP's. Both reached the page
 * as a bare "4.0ft" under one "Swell height" heading, and formatting.ts told the next reader they
 * were all CDIP MOP significant wave height.
 *
 * WHAT IS HELD HERE.
 *   1. heightBasis gives every row in heightBasis.cases.json the basis written there. The same
 *      table holds pipeline/daily_report.height_basis, so the page and the report agree.
 *   2. The cases the brief named: a calibrated spot whose factor is below 1 (point-arena), and a
 *      MOP-tier spot's model row.
 *   3. THE GUARD: across every swell_source the pipeline writes, a sweep of heights and every way
 *      a raw value can be missing, a model-estimate row gets 'model', the label "Model
 *      estimate", the mark "model" — and never the word calibrated or CDIP.
 *   4. The words themselves, and the methodology post quoting the same two labels.
 *   5. STRUCTURE: every component that prints a height with fmtFtRange prints a basis beside it,
 *      from a value, never a hard-coded one; and the two queries those rows come from select the
 *      columns the basis is read from.
 *   6. STORED BASES. A daily report's top_spots carries the basis daily_report.py stored with each
 *      height; storedHeightBasis accepts only the three exact strings and gives null — no tag —
 *      for a report stored before the field existed, or for anything else.
 *   7. THE REPORT CARDS print that stored height with the stored basis beside it, read from the
 *      entry's own height_basis and never derived: the row the height came from is gone, so any
 *      other source would be a guess about a different number.
 *   8. NO COPY CALLS MOP A MEASUREMENT. MOP is CDIP's nearshore model; "Calibrated to CDIP
 *      measurements" was the one place that said otherwise, and this keeps it from coming back.
 *
 * NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST: bases, labels and marks are literals here or
 * in the cases file, and the post's quoted labels come from the post.
 *
 *     node --experimental-strip-types frontend/lib/heightBasis.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import ts from 'typescript';
import { copyOf, copyStrings, scannedFiles, walk } from './copyScan.ts';
import {
  heightBasis, heightBasisMarkHtml, HEIGHT_BASIS_LABEL, HEIGHT_BASIS_MARK, HEIGHT_BASIS_NOTE,
  HEIGHT_METHOD_HREF, storedHeightBasis, type HeightBasisRow,
} from './heightBasis.ts';

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
type Case = { name: string; row: HeightBasisRow; basis: string | null };
const cases = (JSON.parse(read('lib/heightBasis.cases.json')) as { cases: Case[] }).cases;
check('the shared table is not trivially small', cases.length >= 15, `${cases.length}`);
for (const c of cases) {
  const got = heightBasis(c.row);
  check(`${c.name} -> ${c.basis}`, got === c.basis, `got ${got}`);
}
for (const basis of ['calibrated', 'cdip', 'model', null]) {
  check(`the table covers ${basis}`, cases.some((c) => c.basis === basis));
}

// --------------------------------------------------------------------------- //
// 2 — the named cases                                                          //
// --------------------------------------------------------------------------- //
// point-arena: factor 0.9313 in the 2026-09-01 file, so 3.1 / 0.9313 = 3.3287 -> 3.33, LARGER than
// the raw 3.1. (Its factor in the 2026-10-06 file, 0.812, is further below 1 still.)
check('point-arena (factor 0.9313, 2026-09-01 file) is calibrated although its published height is the larger',
  heightBasis({ face_ft: 3.33, face_ft_raw: 3.1, swell_source: 'nwps_height_ww3_dir' }) === 'calibrated');
check('...and its spot-page label says so',
  HEIGHT_BASIS_LABEL[heightBasis({ face_ft: 3.33, face_ft_raw: 3.1, swell_source: 'nwps_height_ww3_dir' })!]
    === "Calibrated to CDIP's nearshore model");
// A MOP-tier spot is never corrected, so raw === face; the hours readers see are not MOP-fed.
check("a MOP-tier spot's model row is a model estimate",
  heightBasis({ face_ft: 5.12, face_ft_raw: 5.12, swell_source: 'ww3' }) === 'model');
check("...and so is its NWPS-fallback row",
  heightBasis({ face_ft: 3.7, face_ft_raw: 3.7, swell_source: 'nwps_swell' }) === 'model');
check('a MOP-fed row is CDIP\'s own',
  heightBasis({ face_ft: 3.28, face_ft_raw: 3.28, swell_source: 'cdip_mop' }) === 'cdip');
check('an uncalibrated spot is a model estimate',
  heightBasis({ face_ft: 4.4, face_ft_raw: 4.4, swell_source: 'nwps_height_ww3_dir' }) === 'model');
check('no row, or no height, has no label',
  heightBasis(null) === null && heightBasis(undefined) === null
  && heightBasis({ face_ft: null, face_ft_raw: 2.0, swell_source: 'cdip_mop' }) === null);

// --------------------------------------------------------------------------- //
// 3 — THE GUARD: a model estimate is never labelled calibrated or CDIP         //
// --------------------------------------------------------------------------- //
// Every swell_source the pipeline writes except cdip_mop, and near misses of it.
const NOT_MOP_SOURCES: (string | null | undefined)[] = [
  'ww3', 'nwps_swell', 'buoy', 'nwps_total', 'none', 'nwps_height_ww3_dir', 'nwps',
  null, undefined, '', 'CDIP_MOP', 'cdip', 'cdip_mop ', ' cdip_mop', 'mop',
];
const HEIGHTS = [0, 0.01, 0.2, 0.5, 1, 2.44, 3.28, 4, 5.12, 9.99, 12.3, 30];
// raw === face is the uncorrected row; the rest are every way a raw value can fail to be evidence.
const RAWS = (h: number): (number | null | undefined)[] => [h, null, undefined, Number.NaN, Infinity];
let swept = 0;
const leaks: string[] = [];
for (const src of NOT_MOP_SOURCES) {
  for (const h of HEIGHTS) {
    for (const raw of RAWS(h)) {
      swept += 1;
      const basis = heightBasis({ face_ft: h, face_ft_raw: raw, swell_source: src });
      const label = basis ? HEIGHT_BASIS_LABEL[basis] : '';
      const mark = basis ? HEIGHT_BASIS_MARK[basis] : '';
      const html = heightBasisMarkHtml(basis);
      const said = `${label} ${mark} ${html}`.toLowerCase();
      if (basis !== 'model' || label !== 'Model estimate' || mark !== 'model'
          || said.includes('calibrated') || said.includes('cdip')) {
        // String(), not JSON: JSON prints NaN and Infinity as null, which hides the very case.
        if (leaks.length < 5) leaks.push(`face ${h}, raw ${String(raw)}, source ${String(src)} -> ${basis} / ${label} / ${mark}`);
      }
    }
  }
}
check(`all ${swept} model-estimate rows are labelled "Model estimate" and marked "model", never calibrated or CDIP`,
  leaks.length === 0 && swept === NOT_MOP_SOURCES.length * HEIGHTS.length * 5, leaks.join('; '));

// --------------------------------------------------------------------------- //
// 4 — the words                                                                //
// --------------------------------------------------------------------------- //
check('the spot-page labels', HEIGHT_BASIS_LABEL.calibrated === "Calibrated to CDIP's nearshore model"
  && HEIGHT_BASIS_LABEL.model === 'Model estimate' && HEIGHT_BASIS_LABEL.cdip === 'CDIP nearshore height',
  JSON.stringify(HEIGHT_BASIS_LABEL));
check('the compact marks', HEIGHT_BASIS_MARK.calibrated === 'calibrated'
  && HEIGHT_BASIS_MARK.model === 'model' && HEIGHT_BASIS_MARK.cdip === 'CDIP',
  JSON.stringify(HEIGHT_BASIS_MARK));
check('the model note says it is uncalibrated and can read high, and claims no measurement',
  /not calibrated/i.test(HEIGHT_BASIS_NOTE.model) && /read higher/.test(HEIGHT_BASIS_NOTE.model)
  && !/measur/i.test(HEIGHT_BASIS_NOTE.model), HEIGHT_BASIS_NOTE.model);
check('the note links to the methodology post', HEIGHT_METHOD_HREF === '/blog/methodology');
check('the map marker is the mark, with the label as its tooltip',
  heightBasisMarkHtml('model').includes('title="Model estimate"') && heightBasisMarkHtml('model').includes('>model</span>')
  && heightBasisMarkHtml('calibrated').includes('>calibrated</span>'), heightBasisMarkHtml('model'));
check('no basis, no marker', heightBasisMarkHtml(null) === '');

const post = read('content/blog/methodology.md');
const quoted = /each spot's current height is marked with which it is: "([^"]+)" or "([^"]+)"\./.exec(post);
check('the methodology post still names the labels', quoted !== null);
if (quoted) {
  check(`the post's first label is the site's calibrated label: ${JSON.stringify(quoted[1])}`,
    quoted[1] === HEIGHT_BASIS_LABEL.calibrated, HEIGHT_BASIS_LABEL.calibrated);
  check(`the post's second label is the site's model label: ${JSON.stringify(quoted[2])}`,
    quoted[2] === HEIGHT_BASIS_LABEL.model, HEIGHT_BASIS_LABEL.model);
}

// --------------------------------------------------------------------------- //
// 5 — structure: every printed height carries its basis                        //
// --------------------------------------------------------------------------- //
type Tally = { heights: number; marks: number; literalBasis: string[] };

/** Heights printed with fmtFtRange, basis markers beside them, and markers fed a literal. */
function tally(fileName: string, source: string): Tally {
  const sf = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: Tally = { heights: 0, marks: 0, literalBasis: [] };
  const isLiteral = (e: ts.Expression | undefined): boolean => !!e
    && (ts.isStringLiteral(e) || ts.isNoSubstitutionTemplateLiteral(e));
  const line = (n: ts.Node) => sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1;
  const visit = (n: ts.Node): void => {
    if (ts.isCallExpression(n) && ts.isIdentifier(n.expression)) {
      if (n.expression.text === 'fmtFtRange') out.heights += 1;
      if (n.expression.text === 'heightBasisMarkHtml') {
        out.marks += 1;
        if (isLiteral(n.arguments[0])) out.literalBasis.push(`${fileName}:${line(n)}`);
      }
    }
    if ((ts.isJsxSelfClosingElement(n) || ts.isJsxOpeningElement(n))
        && ts.isIdentifier(n.tagName) && n.tagName.text === 'HeightBasisMark') {
      out.marks += 1;
      for (const a of n.attributes.properties) {
        if (!ts.isJsxAttribute(a) || a.name.getText(sf) !== 'basis') continue;
        const init = a.initializer;
        if (!init || ts.isStringLiteral(init)
            || (ts.isJsxExpression(init) && isLiteral(init.expression))) {
          out.literalBasis.push(`${fileName}:${line(n)}`);
        }
      }
    }
    // The spot page prints the full label rather than the mark.
    if (ts.isElementAccessExpression(n) && ts.isIdentifier(n.expression)
        && n.expression.text === 'HEIGHT_BASIS_LABEL') {
      out.marks += 1;
      if (isLiteral(n.argumentExpression)) out.literalBasis.push(`${fileName}:${line(n)}`);
    }
    ts.forEachChild(n, visit);
  };
  visit(sf);
  return out;
}

// The scanner, on hand-written input first: one that finds nothing would pass everything below.
const bare = tally('a.tsx', 'const A = () => <span>{fmtFtRange(lo, hi, f)}</span>;');
check('a height printed with no basis is seen as one', bare.heights === 1 && bare.marks === 0, JSON.stringify(bare));
const marked = tally('b.tsx', 'const A = () => <><span>{fmtFtRange(lo, hi, f)}</span><HeightBasisMark basis={heightBasis(row)} /></>;');
check('...and with its basis beside it, as marked', marked.heights === 1 && marked.marks === 1
  && marked.literalBasis.length === 0, JSON.stringify(marked));
const fixed = tally('c.tsx', 'const A = () => <><HeightBasisMark basis="calibrated" /><HeightBasisMark basis={\'cdip\'} /></>;'
  + 'const h = heightBasisMarkHtml("model"); const l = HEIGHT_BASIS_LABEL["calibrated"];');
check('a hard-coded basis is caught in every form', fixed.literalBasis.length === 4, JSON.stringify(fixed));
const twice = tally('d.tsx', 'const A = () => <><b>{fmtFtRange(a, b, c)}</b><i>{fmtFtRange(a, b, c)}</i><HeightBasisMark basis={x} /></>;');
check('two heights with one marker are one short', twice.heights === 2 && twice.marks === 1, JSON.stringify(twice));

const files = ['app', 'components'].flatMap((d) => walk(join(FRONTEND, d)))
  .map((abs) => abs.slice(FRONTEND.length + 1))
  .filter((rel) => /\.tsx?$/.test(rel) && !/\.test\.[cm]?tsx?$/.test(rel));
const printing = files.map((rel) => ({ rel, t: tally(rel, read(rel)) })).filter((f) => f.t.heights > 0);
const MUST_PRINT = ['app/page.tsx', 'components/CamsBrowser.tsx', 'components/CurrentConditions.tsx',
  'components/HeroSearch.tsx', 'components/RegionList.tsx', 'components/SpotMap.tsx'];
check('the scan reaches every surface that prints a height: home, cams, spot page, search, region, map',
  MUST_PRINT.every((f) => printing.some((p) => p.rel === f)),
  MUST_PRINT.filter((f) => !printing.some((p) => p.rel === f)).join(', '));
const short = printing.filter((p) => p.t.marks < p.t.heights);
check('every height printed with fmtFtRange has its basis beside it',
  short.length === 0, short.map((p) => `${p.rel}: ${p.t.heights} heights, ${p.t.marks} markers`).join('; '));
const literal = files.flatMap((rel) => tally(rel, read(rel)).literalBasis);
check('no marker or label is fed a hard-coded basis', literal.length === 0, literal.join(', '));

// The rows those surfaces read must carry the two columns. Missing either, every row would come
// back a model estimate: safe, but wrong at the 141 calibrated spots, and silent.
const SELECTS: { file: string; fn: string }[] = [
  { file: 'lib/queries.ts', fn: 'fetchLatestForecastPerSpot' },
  { file: 'app/spot/[slug]/page.tsx', fn: 'loadForecasts' },
];
for (const { file, fn } of SELECTS) {
  const src = read(file);
  const body = src.slice(src.indexOf(`function ${fn}`));
  const select = /\.select\(\s*'([^']+)'/.exec(body)?.[1] ?? '';
  const cols = select.split(',').map((c) => c.trim());
  check(`${fn} selects face_ft, face_ft_raw and swell_source`,
    ['face_ft', 'face_ft_raw', 'swell_source'].every((c) => cols.includes(c)), select);
}

// --------------------------------------------------------------------------- //
// 6 — the basis a report STORED                                                //
// --------------------------------------------------------------------------- //
check('each basis daily_report.py writes is read back as itself',
  storedHeightBasis('calibrated') === 'calibrated' && storedHeightBasis('cdip') === 'cdip'
  && storedHeightBasis('model') === 'model');
// A report stored before the field existed has no key at all: undefined. None stored for a spot
// with no height arrives as null. Neither is a basis, and nothing else is either.
const NOT_STORED: unknown[] = [undefined, null, '', 'Calibrated', 'MODEL', ' model', 'model ',
  'calibrated to CDIP', 'measured', 'cdip_mop', 0, 1, true, false, {}, [], ['model'], Number.NaN];
const guessed = NOT_STORED.filter((v) => storedHeightBasis(v) !== null);
check(`no tag for a report stored before the field, or for any value daily_report.py never writes (${NOT_STORED.length})`,
  guessed.length === 0, guessed.map((v) => String(v)).join(', '));

// --------------------------------------------------------------------------- //
// 7 — the report cards: the stored height, with the stored basis               //
// --------------------------------------------------------------------------- //
type ReportTally = { prints: number; marks: number; notStored: string[]; derived: number };

/** Stored heights printed as `x.face_ft.toFixed(…)`, basis markers, markers not fed from the
 *  entry's own stored field, and any call to heightBasis() (a derivation, i.e. a guess). */
function reportTally(fileName: string, source: string): ReportTally {
  const sf = ts.createSourceFile(fileName, source, ts.ScriptTarget.Latest, true, ts.ScriptKind.TSX);
  const out: ReportTally = { prints: 0, marks: 0, notStored: [], derived: 0 };
  const line = (n: ts.Node) => sf.getLineAndCharacterOfPosition(n.getStart(sf)).line + 1;
  const visit = (n: ts.Node): void => {
    if (ts.isCallExpression(n) && ts.isPropertyAccessExpression(n.expression)
        && n.expression.name.text === 'toFixed' && ts.isPropertyAccessExpression(n.expression.expression)
        && n.expression.expression.name.text === 'face_ft') {
      out.prints += 1;
    }
    if (ts.isCallExpression(n) && ts.isIdentifier(n.expression) && n.expression.text === 'heightBasis') {
      out.derived += 1;
    }
    if ((ts.isJsxSelfClosingElement(n) || ts.isJsxOpeningElement(n))
        && ts.isIdentifier(n.tagName) && n.tagName.text === 'HeightBasisMark') {
      out.marks += 1;
      const attr = n.attributes.properties.find(
        (a): a is ts.JsxAttribute => ts.isJsxAttribute(a) && a.name.getText(sf) === 'basis');
      const e = attr?.initializer && ts.isJsxExpression(attr.initializer) ? attr.initializer.expression : undefined;
      const fromStore = !!e && ts.isCallExpression(e) && ts.isIdentifier(e.expression)
        && e.expression.text === 'storedHeightBasis' && e.arguments.length === 1
        && ts.isPropertyAccessExpression(e.arguments[0]) && e.arguments[0].name.text === 'height_basis';
      if (!fromStore) out.notStored.push(`${fileName}:${line(n)}`);
    }
    ts.forEachChild(n, visit);
  };
  visit(sf);
  return out;
}

const good = reportTally('r1.tsx', '<><b>{s.face_ft.toFixed(1)}ft</b><HeightBasisMark basis={storedHeightBasis(s.height_basis)} /></>');
check('a stored height with its stored basis is seen as one of each', good.prints === 1 && good.marks === 1
  && good.notStored.length === 0 && good.derived === 0, JSON.stringify(good));
const guess = reportTally('r2.tsx', '<><b>{s.face_ft.toFixed(1)}ft</b><HeightBasisMark basis={heightBasis(latest)} />'
  + '<HeightBasisMark basis={storedHeightBasis(s.basis)} /><HeightBasisMark basis={"model"} /></>');
check('a derived, a wrong-field and a literal basis are each caught', guess.notStored.length === 3 && guess.derived === 1,
  JSON.stringify(guess));
check('an unmarked stored height is seen as one short',
  reportTally('r3.tsx', '<b>{s.face_ft.toFixed(1)}ft</b>').marks === 0);

// Every file that prints a height as `x.face_ft.toFixed(…)` is a report surface held to the rule
// above, or is exempt by name, with the reason.
const TOFIXED_EXEMPT: Record<string, string> = {
  'components/ForecastGrid.tsx': "the spot page's grid: every row at one spot shares the basis the page labels",
};
const REPORT_SURFACES = ['components/ReportCard.tsx', 'app/reports/[date]/[region]/page.tsx'];
const toFixedFiles = files.filter((rel) => reportTally(rel, read(rel)).prints > 0);
check('the stored-height scan reaches both report surfaces',
  REPORT_SURFACES.every((f) => toFixedFiles.includes(f)), toFixedFiles.join(', '));
const unknown = toFixedFiles.filter((f) => !REPORT_SURFACES.includes(f) && !(f in TOFIXED_EXEMPT));
check('nothing else prints a face_ft with toFixed unlabelled', unknown.length === 0, unknown.join(', '));
for (const f of REPORT_SURFACES) {
  const t = reportTally(f, read(f));
  check(`${f}: every stored height has a marker (${t.prints} printed, ${t.marks} marked)`, t.marks >= t.prints && t.prints > 0);
  check(`${f}: every marker reads the entry's stored height_basis, and nothing derives one`,
    t.notStored.length === 0 && t.derived === 0, `${t.notStored.join(', ')} derived=${t.derived}`);
}
const reportsType = read('lib/reports.ts');
check('ReportTopSpot declares height_basis as optional, because old reports lack it',
  /height_basis\?: string \| null;/.test(reportsType));

// --------------------------------------------------------------------------- //
// 8 — no copy calls MOP, or CDIP's model output, a measurement                //
// --------------------------------------------------------------------------- //
const MEASUREMENT: RegExp[] = [
  /\bCDIP(?:'s)?(?:\s+MOP)?\s+measurements?\b/i,   // "Calibrated to CDIP measurements"
  /\bMOP(?:'s)?\s+measurements?\b/i,                 // "MOP's measurement"
  /\bmeasured\s+by\s+(?:CDIP|MOP)\b/i,             // "measured by CDIP"
  /\b(?:CDIP|MOP)[-\s]measured\b/i,                 // "CDIP-measured height"
];
const says = (text: string) => MEASUREMENT.some((re) => re.test(text));
for (const t of ['Calibrated to CDIP measurements', "the MOP's measurement", 'as measured by CDIP',
                 'a CDIP-measured height', 'CDIP MOP measurements']) {
  check(`calling MOP a measurement is caught: ${JSON.stringify(t)}`,
    copyStrings('m.tsx', `const s = ${JSON.stringify(t)};`).some((c) => says(c.text)));
}
check("the site's own wording passes: the new label, CDIP's buoys, and a measured factor",
  !["Calibrated to CDIP's nearshore model", 'using actual buoy measurements as the initial condition',
    'CDIP buoy measurements', 'a factor measured against CDIP MOP'].some(says));
const claims = scannedFiles(FRONTEND).flatMap((rel) => copyOf(rel, read(rel))
  .filter((c) => says(c.text)).map((c) => `${rel}:${c.line}  ${JSON.stringify(c.text.trim().slice(0, 100))}`));
check('no copy calls MOP a measurement', claims.length === 0, claims.join('; '));

if (failures > 0) {
  throw new Error(`heightBasis: ${failures} FAILURE(S)`);
}
console.log('\nheightBasis: ALL PASS');
