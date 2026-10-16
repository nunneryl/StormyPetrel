/**
 * lib/provenance: the one list of trusted sources, held to the pipeline's, and the two
 * formatters that apply it.
 *
 * THE LIST IS THE PIPELINE'S. pipeline/descriptions.py STATED_SOURCES (what a description may
 * state) and pipeline/config.py TIDE_RATED_SOURCES (what the rating applies a tide preference
 * from) are read out of the Python source and must equal TRUSTED_SOURCES, in order. A source
 * added on one side alone fails here.
 *
 * NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST: every expected string is written out.
 *
 *     node --experimental-strip-types frontend/lib/provenance.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  TRUSTED_SOURCES, fmtBreakType, isTrustedSource, shownTidePreference,
} from './provenance.ts';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const read = (rel: string) => readFileSync(join(ROOT, rel), 'utf8');

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) console.log(`  PASS  ${name}`);
  else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}
function eq<T>(name: string, got: T, want: T): void {
  check(name, JSON.stringify(got) === JSON.stringify(want),
        `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

/** The quoted strings of a one-line Python tuple assignment `NAME = ("a", "b", ...)`. */
function pythonTuple(rel: string, name: string): string[] | null {
  const m = new RegExp(`^${name} = \\(([^)]*)\\)`, 'm').exec(read(rel));
  return m ? [...m[1].matchAll(/"([^"]*)"/g)].map((x) => x[1]) : null;
}

// 1 — the list
eq('TRUSTED_SOURCES is reviewed, researched, scraped', [...TRUSTED_SOURCES],
   ['reviewed', 'researched', 'scraped']);
eq('...the same as pipeline/descriptions.py STATED_SOURCES',
   pythonTuple('pipeline/descriptions.py', 'STATED_SOURCES'), [...TRUSTED_SOURCES]);
eq('...and pipeline/config.py TIDE_RATED_SOURCES',
   pythonTuple('pipeline/config.py', 'TIDE_RATED_SOURCES'), [...TRUSTED_SOURCES]);

// 2 — trusted or not, for every source the database admits (migrations 020 and 022) and the
//     shapes an absent one takes
const TRUSTED = ['reviewed', 'researched', 'scraped'];
const UNTRUSTED = ['model_recall', 'unattributed', 'algorithm_default', 'derived_from_break_type',
                   'unknown', '', 'Researched', ' researched'];
for (const s of TRUSTED) check(`${s} is trusted`, isTrustedSource(s) === true);
for (const s of UNTRUSTED) check(`${JSON.stringify(s)} is not trusted`, isTrustedSource(s) === false);
check('null is not trusted', isTrustedSource(null) === false);
check('undefined is not trusted', isTrustedSource(undefined) === false);

// 3 — the break type: always shown, marked when untrusted
eq('researched reef -> "reef"', fmtBreakType('reef', 'researched'), 'reef');
eq('reviewed point -> "point"', fmtBreakType('point', 'reviewed'), 'point');
eq('scraped jetty -> "jetty"', fmtBreakType('jetty', 'scraped'), 'jetty');
eq('model_recall beach -> "beach (unverified)"', fmtBreakType('beach', 'model_recall'),
   'beach (unverified)');
eq('unattributed point -> "point (unverified)"', fmtBreakType('point', 'unattributed'),
   'point (unverified)');
eq('no source -> "(unverified)"', fmtBreakType('reef', null), 'reef (unverified)');
eq('source key absent -> "(unverified)"', fmtBreakType('rivermouth', undefined),
   'rivermouth (unverified)');
eq('no break type -> null, never "(unverified)" on its own', fmtBreakType(null, 'model_recall'), null);
eq('undefined break type -> null', fmtBreakType(undefined, 'researched'), null);
eq('a blank break type is returned as it is', fmtBreakType('', 'model_recall'), '');

// 4 — the tide: shown only when trusted
eq('researched low -> "low"', shownTidePreference('low', 'researched'), 'low');
eq('reviewed low_mid -> "low_mid" (the key; spotInfo formats it)',
   shownTidePreference('low_mid', 'reviewed'), 'low_mid');
eq('scraped all -> "all"', shownTidePreference('all', 'scraped'), 'all');
eq('unattributed mid -> null', shownTidePreference('mid', 'unattributed'), null);
eq('model_recall high -> null', shownTidePreference('high', 'model_recall'), null);
eq('no source -> null', shownTidePreference('mid', null), null);
eq('source key absent -> null', shownTidePreference('mid', undefined), null);
eq('no preference -> null', shownTidePreference(null, 'researched'), null);

if (failures > 0) throw new Error(`provenance: ${failures} FAILURE(S)`);
console.log('\nprovenance: ALL PASS');
