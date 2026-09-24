/**
 * Every markdown table on the site renders as a table, and a readable one, from the renderer.
 *
 * WHAT WENT WRONG. lib/blog.ts rendered posts with remark + remark-html alone. That is
 * CommonMark, which has no tables, so each GitHub-flavoured table came out as a single <p> of
 * pipes and dashes and the browser ran its rows together into one line. Every table in the
 * methodology post (data sources, period quality, chop penalty, surface conditions) showed
 * that way on the live site. The fix is in the renderer (lib/markdown.ts), so it covers every
 * post, and these checks go through the blog's own loader so a revert of either file fails.
 *
 * NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST. Counts, headers and alignments are
 * written by hand from the post. "Nothing else changed" is checked against a plain CommonMark
 * render of the same text, which is what the blog produced before this change.
 *
 *     node --experimental-strip-types frontend/lib/markdown.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import matter from 'gray-matter';
import { remark } from 'remark';
import remarkHtml from 'remark-html';
import { getPost } from './blog.ts';
import { isNumericCell, renderMarkdown } from './markdown.ts';
import { BUOY_UPDATES, FORECAST_UPDATES, fillCopyTokens } from './updateCadence.ts';

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..');
process.chdir(FRONTEND); // lib/blog.ts reads content/blog relative to the working directory

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures++;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}

type Parsed = { box: string; headers: string[]; aligns: (string | null)[]; rows: number };
function tables(html: string): Parsed[] {
  return [...html.matchAll(/<div class="table-scroll"([^>]*)><table>([\s\S]*?)<\/table><\/div>/g)].map((m) => {
    const head = m[2].split('</thead>')[0];
    const ths = [...head.matchAll(/<th( align="(\w+)")?>([\s\S]*?)<\/th>/g)];
    return {
      box: m[1],
      headers: ths.map((t) => t[3]),
      aligns: ths.map((t) => t[2] ?? null),
      rows: (m[2].split('<tbody>')[1]?.match(/<tr>/g) ?? []).length,
    };
  });
}
const commonmark = async (md: string) => String(await remark().use(remarkHtml).process(md));
const outsideTables = (html: string) =>
  html.replace(/<div class="table-scroll"[\s\S]*?<\/table><\/div>\n?/g, '').replace(/<p>\|[\s\S]*?<\/p>\n?/g, '');

// --------------------------------------------------------------------------- //
// 1 — the methodology post, through the blog's own loader                      //
// --------------------------------------------------------------------------- //
const post = await getPost('methodology');
const html = post?.contentHtml ?? '';
const found = tables(html);
check('the methodology post renders its seven tables as tables', found.length === 7, `${found.length}`);
check('no table is left as a paragraph of pipes', !/<p>\|/.test(html));
check('the tables are the seven the post writes, in order', JSON.stringify(found.map((t) => t.headers)) ===
  JSON.stringify([['Height', 'Size score'], ['Quality score', 'Weight'], ['What', 'Source', 'Notes'],
    ['Swell direction', 'Direction gain'], ['Period', 'Score'], ['chop_ratio', 'Score'], ['Condition', 'Label']]),
  JSON.stringify(found.map((t) => t.headers)));
check('every row arrives', JSON.stringify(found.map((t) => t.rows)) === JSON.stringify([8, 4, 6, 4, 9, 6, 5]),
  JSON.stringify(found.map((t) => t.rows)));
// A column goes right only when every body cell is a number: "10 ft or more", "6 s or less" and the
// cos² formula keep their columns left, and the all-number columns go right.
check('numeric columns are right-aligned, text columns are not', JSON.stringify(found.map((t) => t.aligns)) ===
  JSON.stringify([[null, 'right'], [null, 'right'], [null, null, null], [null, null], [null, 'right'],
    ['right', 'right'], [null, null]]),
  JSON.stringify(found.map((t) => t.aligns)));
check('each table sits in a box that scrolls on its own, reachable by keyboard and labelled',
  found.every((t) => t.box.includes('role="region"') && t.box.includes('tabindex="0"')) &&
  found[4]?.box.includes('aria-label="Table: Period, Score"') === true, found[4]?.box);

// --------------------------------------------------------------------------- //
// 2 — nothing but the tables changed, in any post                             //
// --------------------------------------------------------------------------- //
for (const slug of ['methodology', 'understanding-forecasts']) {
  const { content } = matter(fillCopyTokens(readFileSync(join(FRONTEND, 'content/blog', `${slug}.md`), 'utf8')));
  const before = await commonmark(content);
  const after = (await getPost(slug))?.contentHtml ?? '';
  check(`${slug}: outside its tables, byte-identical to the CommonMark render it replaced`,
    outsideTables(before) === outsideTables(after));
}

// --------------------------------------------------------------------------- //
// 3 — #226's copy tokens still reach the page filled                          //
// --------------------------------------------------------------------------- //
check('the rendered post carries the cadence settings, filled', html.includes(FORECAST_UPDATES) && html.includes(BUOY_UPDATES));
check('no token reaches a reader', !/\{\{|\}\}/.test(html));

// --------------------------------------------------------------------------- //
// 4 — the renderer on its own                                                  //
// --------------------------------------------------------------------------- //
const mixed = tables(await renderMarkdown(
  '| Spot | Hs | Note |\n|---|---|---|\n| a | 1.2 | fine |\n| b | 0.8 ft | 12 |\n'))[0];
check('a column mixing text and numbers stays left-aligned; an all-number column goes right',
  JSON.stringify(mixed?.aligns) === JSON.stringify([null, 'right', null]), JSON.stringify(mixed?.aligns));
const authored = tables(await renderMarkdown('| n | m |\n|:---|:---:|\n| 1 | 2 |\n'))[0];
check("an author's alignment wins over the numeric rule",
  JSON.stringify(authored?.aligns) === JSON.stringify(['left', 'center']), JSON.stringify(authored?.aligns));
const headOnly = tables(await renderMarkdown('| 1 | 2 |\n|---|---|\n'))[0];
check('a table with no body rows is left alone', JSON.stringify(headOnly?.aligns) === JSON.stringify([null, null]));
const quoted = await renderMarkdown('> | Period | Multiplier |\n> |---|---|\n> | 6s | 0.50 |\n');
check('a table inside a blockquote gets the same box and alignment',
  JSON.stringify(tables(quoted).map((t) => t.aligns)) === JSON.stringify([['right', 'right']]), quoted);
// With single-tilde strikethrough on, the "~" before 6 opens and the one in "6~8" closes, and
// "6 km grid, periods of 6" would be struck through. The double tilde must still strike.
const prose = await renderMarkdown('On a ~6 km grid, periods of 6~8 s, and ~~gone~~.\n');
check('a single tilde in prose is never strikethrough; a double one still is',
  prose.includes('On a ~6 km grid, periods of 6~8 s, and <del>gone</del>.'), prose);
const hostile = await renderMarkdown('| a |\n|---|\n| <script>x()</script><img src=x onerror=y> |\n\n<div class="table-scroll" onclick="z">q</div>\n');
check('sanitizing stays on inside and outside tables',
  !/<script|onerror|onclick/.test(hostile), hostile);
for (const [text, want] of [
  ['6s', true], ['16s+', true], ['0.85', true], ['1.00', true], ['2.5 m/s', true], ['≤ 60°', true],
  ['−12%', true], ['12 ft', true], ['NWPS', false], ['wind under 2.5 m/s', false], ['6s mush', false],
  ['', false],
] as const) {
  check(`isNumericCell(${JSON.stringify(text)}) is ${want}`, isNumericCell(text) === want);
}

if (failures > 0) {
  throw new Error(`markdown: ${failures} FAILURE(S)`);
}
console.log('\nmarkdown: ALL PASS');
