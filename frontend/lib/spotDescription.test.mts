/**
 * The description's nearby spots as links (lib/spotDescription.ts): the parser by hand, then
 * against every committed description (pipeline/data/spot_descriptions.json), whose nearby names
 * must each link to exactly one roster spot. Expected values written out.
 *
 *     node --experimental-strip-types frontend/lib/spotDescription.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { descriptionParts, nearbyBox, nearbyItems } from './spotDescription.ts';
import { displaySpotName } from './spotName.ts';

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..');
let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) console.log(`  PASS  ${name}`);
  else { failures += 1; console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`); }
}
function eq(name: string, got: unknown, want: unknown): void {
  check(name, JSON.stringify(got) === JSON.stringify(want), `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

const PIPE = 'Banzai Pipeline is a reef break in Hawaii over a rock bottom. This page has a live cam '
  + 'from Explore.org. Nearby spots: Backdoor Pipeline (1 km), Off The Wall (1 km) and Ehukai Beach Park (1 km).';
const NEAR = [
  { slug: 'backdoor-pipeline', name: 'Backdoor Pipeline' },
  { slug: 'off-the-wall', name: 'Off The Wall' },
  { slug: 'ehukai-beach-park', name: 'Ehukai Beach Park' },
  { slug: 'banzai-pipeline', name: 'Banzai Pipeline' },
  { slug: 'pipeline-other', name: 'Pipeline' },   // a name inside another must not match it
];

eq('three linked names, the text between them kept', descriptionParts(PIPE, NEAR, 'banzai-pipeline'), [
  { text: 'Banzai Pipeline is a reef break in Hawaii over a rock bottom. This page has a live cam from Explore.org. Nearby spots: ' },
  { text: 'Backdoor Pipeline', slug: 'backdoor-pipeline' },
  { text: ' (1 km), ' },
  { text: 'Off The Wall', slug: 'off-the-wall' },
  { text: ' (1 km) and ' },
  { text: 'Ehukai Beach Park', slug: 'ehukai-beach-park' },
  { text: ' (1 km).' },
]);
eq('the parts put back together are the description, unchanged',
  descriptionParts(PIPE, NEAR, 'banzai-pipeline').map((p) => p.text).join(''), PIPE);

eq('a name with no candidate stays text',
  descriptionParts('A is a reef. Nearby spots: B (2 km) and C (3 km).', [{ slug: 'b', name: 'B' }]), [
    { text: 'A is a reef. Nearby spots: ' }, { text: 'B', slug: 'b' }, { text: ' (2 km) and C (3 km).' },
  ]);
eq('a name two candidates share stays text',
  descriptionParts('A is a reef. Nearby spots: B (2 km).', [{ slug: 'b1', name: 'B' }, { slug: 'b2', name: 'B' }]),
  [{ text: 'A is a reef. Nearby spots: B (2 km).' }]);
eq('one nearby spot', nearbyItems('A is a reef. Nearby spots: Fiji (14 km).')?.items,
  [{ name: 'Fiji', km: '14 km', sep: '.' }]);
eq('a name with a comma in it', nearbyItems('A. Nearby spots: Haleiwa, North (3 km) and B (4 km).')?.items,
  [{ name: 'Haleiwa, North', km: '3 km', sep: ' and ' }, { name: 'B', km: '4 km', sep: '.' }]);

for (const [label, text] of [
  ['no nearby sentence', 'A is a reef break in Hawaii.'],
  ['not the template shape (no distance)', 'A is a reef. Nearby spots: B and C.'],
  ['text after the sentence', 'A is a reef. Nearby spots: B (2 km). More.'],
  ['no full stop', 'A is a reef. Nearby spots: B (2 km)'],
  ['the lead mid-sentence', 'A says Nearby spots: B (2 km).'],
] as const) {
  eq(`left whole: ${label}`, descriptionParts(text, [{ slug: 'b', name: 'B' }]), [{ text }]);
}

// The box holds 25 km in every direction, at the equator and at 48 N.
for (const lat of [18, 48]) {
  const b = nearbyBox(lat, -120);
  const kmLat = (b.latMax - lat) * 111.2;
  const kmLng = (b.lngMax + 120) * 111.2 * Math.cos((lat * Math.PI) / 180);
  check(`the box at ${lat} N reaches past 25 km both ways`, kmLat > 25 && kmLng > 25, `${kmLat} ${kmLng}`);
}

// --------------------------------------------------------------------------------------------
// Every committed description: its nearby names each link to exactly one roster spot, the one
// at the distance it says (within rounding).
// --------------------------------------------------------------------------------------------
type RosterSpot = { name: string; region_hint: string | null; lat: number; lng: number; is_valid_surf_spot?: boolean };
const roster = (JSON.parse(readFileSync(join(FRONTEND, '..', 'pipeline', 'spots_enriched.json'), 'utf8')) as RosterSpot[])
  .filter((s) => s.is_valid_surf_spot !== false);
// pipeline/db_import.py _slugify, exactly.
const slugOf = (name: string) => name.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
const shown = roster.map((s) => ({ slug: slugOf(s.name), name: displaySpotName(s.name, s.region_hint), lat: s.lat, lng: s.lng }));
const at = new Map(shown.map((s) => [s.slug, s]));
const cache = JSON.parse(readFileSync(join(FRONTEND, '..', 'pipeline', 'data', 'spot_descriptions.json'), 'utf8')) as
  Record<string, { description: string }>;

let withNearby = 0;
let links = 0;
const unlinked: string[] = [];
const farOff: string[] = [];
for (const [slug, { description }] of Object.entries(cache)) {
  const parsed = nearbyItems(description);
  if (description.includes('Nearby spots: ') && !parsed) unlinked.push(`${slug}: does not parse`);
  if (!parsed) continue;
  withNearby += 1;
  const self = at.get(slug);
  if (!self) { unlinked.push(`${slug}: not in the roster`); continue; }
  const parts = descriptionParts(description, shown, slug);
  for (const it of parsed.items) {
    const link = parts.find((p) => p.text === it.name && p.slug);
    if (!link) { unlinked.push(`${slug}: ${it.name}`); continue; }
    links += 1;
    const other = at.get(link.slug!);
    if (other) {
      const km = 2 * 6371 * Math.asin(Math.sqrt(
        Math.sin(((other.lat - self.lat) * Math.PI) / 360) ** 2
        + Math.cos((self.lat * Math.PI) / 180) * Math.cos((other.lat * Math.PI) / 180)
          * Math.sin(((other.lng - self.lng) * Math.PI) / 360) ** 2));
      if (Math.abs(km - Number(it.km.split(' ')[0])) > 1) farOff.push(`${slug} -> ${link.slug}: ${km.toFixed(1)} vs ${it.km}`);
    }
  }
}
check('most descriptions have nearby spots', withNearby > 500, String(withNearby));
eq('every nearby name in every committed description links to one roster spot', unlinked, []);
eq('each link goes to the spot at the distance the text gives', farOff, []);
console.log(`  (${withNearby} descriptions with nearby spots, ${links} links)`);

if (failures) {
  console.log(`\n${failures} failure(s)`);
  process.exit(1);
}
console.log('\nall spotDescription checks passed');
