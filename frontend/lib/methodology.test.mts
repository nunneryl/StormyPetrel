/**
 * The methodology post's surface-conditions table is classifySurface, and CI says so if either
 * moves alone.
 *
 * WHAT THIS HOLDS TOGETHER. content/blog/methodology.md tells readers exactly how the
 * Clean / Mixed / Choppy / Blown out word is chosen: five rows of wind-speed and off_angle
 * thresholds, read top to bottom ("Speed is checked before direction"). The word on every spot
 * page comes from classifySurface in lib/ratings.ts. Nothing tied the two. This reads the table
 * out of the post, turns each row into the rule it states, and holds classifySurface to those
 * rules on a grid that lands on, just under and just over every threshold the post names. A
 * moved threshold, a flipped < / <=, or a reordered check fails, on either side.
 *
 * ONLY TABLES THAT MATCH TODAY ARE PINNED. The chop table is held against interpret.py's
 * _CHOP_POINTS in pipeline/tests/test_methodology_tables.py. The period-quality table does not
 * match the code (its 14 s row), so it is reported for the prose rewrite, not pinned.
 *
 * NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST. The expected word for every probe is the
 * post's; classifySurface supplies only the actual.
 *
 *     node --experimental-strip-types frontend/lib/methodology.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import matter from 'gray-matter';
import { remark } from 'remark';
import remarkGfm from 'remark-gfm';
import type { Nodes, PhrasingContent, TableCell } from 'mdast';
import { chopLabel, classifySurface } from './ratings.ts';

const POST = join(dirname(fileURLToPath(import.meta.url)), '..', 'content', 'blog', 'methodology.md');

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures++;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}

// --------------------------------------------------------------------------- //
// Reading the table                                                           //
// --------------------------------------------------------------------------- //
function text(node: TableCell | PhrasingContent): string {
  if ('value' in node && typeof node.value === 'string') return node.value;
  if ('children' in node) return (node.children as PhrasingContent[]).map(text).join('');
  return '';
}

/** Every table in the post, as rows of cell text, parsed the way the site renders it (GFM). */
function tables(markdown: string): string[][][] {
  const found: string[][][] = [];
  const walk = (node: Nodes): void => {
    if (node.type === 'table') {
      found.push(node.children.map((row) => row.children.map((cell) => text(cell).trim())));
      return;
    }
    if ('children' in node) for (const child of node.children) walk(child as Nodes);
  };
  walk(remark().use(remarkGfm).parse(matter(markdown).content));
  return found;
}

type Axis = 'speed' | 'offAngle';
type Clause = { axis: Axis; at: number; holds: (value: number) => boolean };

const COMPARE: Record<string, (a: number, b: number) => boolean> = {
  '<': (a, b) => a < b,
  '≤': (a, b) => a <= b,
  '>': (a, b) => a > b,
  '≥': (a, b) => a >= b,
};

/** One condition phrase from the table, as the rule it states. Unknown wording fails loudly. */
function clause(phrase: string): Clause {
  let m = /^wind under (\d+(?:\.\d+)?) m\/s$/.exec(phrase);
  if (m) {
    const at = Number(m[1]);
    return { axis: 'speed', at, holds: (v) => v < at };
  }
  m = /^wind (\d+(?:\.\d+)?) m\/s or more$/.exec(phrase);
  if (m) {
    const at = Number(m[1]);
    return { axis: 'speed', at, holds: (v) => v >= at };
  }
  m = /^off_angle ([<>≤≥]) (\d+(?:\.\d+)?)°$/.exec(phrase);
  if (m) {
    const at = Number(m[2]);
    const compare = COMPARE[m[1]];
    return { axis: 'offAngle', at, holds: (v) => compare(v, at) };
  }
  throw new Error(`methodology.test: the surface-conditions table says ${JSON.stringify(phrase)}, `
    + 'which this test cannot read. Teach it the new wording in the same change as the post.');
}

const surface = tables(readFileSync(POST, 'utf8'))
  .filter((t) => JSON.stringify(t[0]) === JSON.stringify(['Condition', 'Label']));
check('the post carries exactly one surface-conditions table', surface.length === 1, `${surface.length}`);

// "Clean (glassy — direction stops mattering)" states the word "Clean"; the parenthesis is gloss.
const rows = (surface[0] ?? [[]]).slice(1).map(([condition, label]) => ({
  clauses: condition.split(',').map((phrase) => clause(phrase.trim())),
  word: label.split(' (')[0].trim(),
}));
check('the table has rows to hold the code to', rows.length > 0, `${rows.length}`);

/** The post's word for an hour: its rows, top to bottom, first match wins. */
function postWord(speed: number, offAngle: number): string | null {
  const row = rows.find((r) => r.clauses.every((c) => c.holds(c.axis === 'speed' ? speed : offAngle)));
  return row ? row.word : null;
}

// --------------------------------------------------------------------------- //
// Holding classifySurface to it                                               //
// --------------------------------------------------------------------------- //
const thresholds = (axis: Axis) =>
  [...new Set(rows.flatMap((r) => r.clauses.filter((c) => c.axis === axis).map((c) => c.at)))];
const speedThresholds = thresholds('speed');
const offThresholds = thresholds('offAngle');
check('the table names thresholds on both axes, so the grid below cannot be vacuous',
  speedThresholds.length > 0 && offThresholds.length > 0,
  `speed ${JSON.stringify(speedThresholds)}, off_angle ${JSON.stringify(offThresholds)}`);

// On, just under and just over every threshold the post names, plus an even spread.
const near = (ts: number[]) => ts.flatMap((t) => [t - 0.01, t, t + 0.01]);
const speeds = [...new Set([...Array.from({ length: 61 }, (_, i) => i * 0.25), ...near(speedThresholds)])]
  .filter((v) => v >= 0);
const offAngles = [...new Set([...Array.from({ length: 181 }, (_, i) => i), ...near(offThresholds)])]
  .filter((v) => v >= 0 && v <= 180);

// off_angle is the wind's angle off the directly-offshore bearing, "wrapped to 0–180°": probe it
// from both sides of an offshore bearing chosen so the wind direction crosses north.
const OFFSHORE = 250;
let probes = 0;
let disagreements = 0;
const examples: string[] = [];
for (const speed of speeds) {
  for (const offAngle of offAngles) {
    const expected = postWord(speed, offAngle);
    for (const windDir of [(OFFSHORE + offAngle) % 360, (OFFSHORE - offAngle + 360) % 360]) {
      probes++;
      const actual = chopLabel(classifySurface(windDir, speed, OFFSHORE));
      if (actual === expected) continue;
      disagreements++;
      if (examples.length < 5) {
        examples.push(`${speed} m/s at off_angle ${offAngle}° (wind from ${windDir}°): `
          + `post says ${JSON.stringify(expected)}, classifySurface says ${JSON.stringify(actual)}`);
      }
    }
  }
}
check(`on all ${probes} probes, classifySurface gives the word the post's table gives`,
  disagreements === 0, `${disagreements} disagree, e.g. ${examples.join('; ')}`);

if (failures > 0) {
  throw new Error(`methodology: ${failures} FAILURE(S)`);
}
console.log('\nmethodology: ALL PASS');
