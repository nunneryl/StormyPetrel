/**
 * The shown spot name (lib/spotName.ts), against the whole roster. Expected names written out.
 *
 *     node --experimental-strip-types frontend/lib/spotName.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { displaySpotName, withDisplayName } from './spotName.ts';

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..');
let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) console.log(`  PASS  ${name}`);
  else { failures += 1; console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`); }
}
function eq(name: string, got: unknown, want: unknown): void {
  check(name, JSON.stringify(got) === JSON.stringify(want), `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

const roster = JSON.parse(readFileSync(join(FRONTEND, '..', 'pipeline', 'spots_enriched.json'), 'utf8')) as
  { name: string; region_hint: string | null }[];
const changed = roster.filter((s) => displaySpotName(s.name, s.region_hint) !== s.name)
  .map((s) => [s.name, displaySpotName(s.name, s.region_hint)]).sort();
eq('exactly these twelve names change, to these', changed, [
  ['Dana Point, California', 'Dana Point'],
  ['Hermosa Beach, California', 'Hermosa Beach'],
  ['Huntington Beach, California', 'Huntington Beach'],
  ['Imperial Beach, California', 'Imperial Beach'],
  ['Mavericks, California', 'Mavericks'],
  ['Newport Beach, California', 'Newport Beach'],
  ['Pine Trees Hawaii', 'Pine Trees'],
  ['Pūpūkea, Hawaii', 'Pūpūkea'],
  ['Redondo Beach, California', 'Redondo Beach'],
  ['Seal Beach, California', 'Seal Beach'],
  ['Sunset Beach Hawaii', 'Sunset Beach'],
  ['Surfside, California', 'Surfside'],
]);
const shown = roster.map((s) => displaySpotName(s.name, s.region_hint).toLowerCase());
const dupes = shown.filter((n, i) => shown.indexOf(n) !== i);
eq('every shown name is unique across the roster (no two pages titled alike)', dupes, []);
eq('the state as part of a place stays: Virginia Beach Oceanfront', displaySpotName('Virginia Beach Oceanfront', 'Virginia'), 'Virginia Beach Oceanfront');
eq('...and North End Virginia Beach', displaySpotName('North End Virginia Beach', 'Virginia'), 'North End Virginia Beach');
eq('another state is not stripped', displaySpotName('Sunset Beach Hawaii', 'California'), 'Sunset Beach Hawaii');
eq('a name that is only the state stays', displaySpotName('Hawaii', 'Hawaii'), 'Hawaii');
eq('no state: unchanged', displaySpotName('Mavericks, California', null), 'Mavericks, California');
const row = { id: 3, slug: 'mavericks-california', name: 'Mavericks, California', state: 'California' };
eq('the row keeps its slug and everything else', withDisplayName(row), { id: 3, slug: 'mavericks-california', name: 'Mavericks', state: 'California' });
check('an unchanged row is the same object', withDisplayName({ name: 'Rincon', state: 'California' }).name === 'Rincon');

const queries = readFileSync(join(FRONTEND, 'lib', 'queries.ts'), 'utf8');
check('every spots read in lib/queries.ts gives the shown name',
  (queries.match(/withDisplayName/g) ?? []).length === 4, String((queries.match(/withDisplayName/g) ?? []).length));

if (failures > 0) throw new Error(`spotName: ${failures} FAILURE(S)`);
console.log('\nspotName: ALL PASS');
