/**
 * Every published height says what it is, and a model estimate is never called anything else.
 *
 * WHAT WENT WRONG. 516 of the 646 rated spots publish an uncalibrated model estimate — boosted
 * for long-period swell, about 1.5 times MOP's at the California spots where the two were
 * compared — and the 130 calibrated spots publish a height scaled to CDIP's. Both reached the page
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
import { walk } from './copyScan.ts';
import {
  heightBasis, heightBasisMarkHtml, HEIGHT_BASIS_LABEL, HEIGHT_BASIS_MARK, HEIGHT_BASIS_NOTE,
  HEIGHT_METHOD_HREF, type HeightBasisRow,
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
// point-arena: factor 0.9313, so 3.1 / 0.9313 = 3.3287 -> 3.33, LARGER than the raw 3.1.
check('point-arena (factor 0.9313) is calibrated although its published height is the larger',
  heightBasis({ face_ft: 3.33, face_ft_raw: 3.1, swell_source: 'nwps_height_ww3_dir' }) === 'calibrated');
check('...and its spot-page label says so',
  HEIGHT_BASIS_LABEL[heightBasis({ face_ft: 3.33, face_ft_raw: 3.1, swell_source: 'nwps_height_ww3_dir' })!]
    === 'Calibrated to CDIP measurements');
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
check('the spot-page labels', HEIGHT_BASIS_LABEL.calibrated === 'Calibrated to CDIP measurements'
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
// back a model estimate: safe, but wrong at the 130 calibrated spots, and silent.
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

if (failures > 0) {
  throw new Error(`heightBasis: ${failures} FAILURE(S)`);
}
console.log('\nheightBasis: ALL PASS');
