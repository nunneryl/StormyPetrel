/**
 * Spot search ignores accents and the Hawaiian ʻokina (lib/spotSearch.ts).
 *
 * THE ROSTER NAMES BELOW ARE COPIED FROM pipeline/spots_enriched.json, with the code point of
 * each mark noted: Hoʻokipa (ʻ U+02BB), Honoli‘i (‘ U+2018), Pūpūkea, Hawaii (ū U+016B),
 * Haleiwa Ali'i Beach (' U+0027), Cowell's Beach Children's Surfing Area. The roster has no
 * Lānaʻi spot today, so "lanai" is checked on the folding of the word itself.
 *
 * EVERY EXPECTED VALUE IS A LITERAL, worked by hand: no expected value comes from calling the
 * function under test.
 *
 *     node --experimental-strip-types frontend/lib/spotSearch.test.mts
 */
import { foldForSearch, NO_RESULTS_TEXT, searchSpots } from './spotSearch.ts';

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}
const names = (xs: { name: string }[]) => JSON.stringify(xs.map((x) => x.name));

// The marks, written as escapes so the test does not depend on how an editor saves them.
const HOOKIPA = 'Hoʻokipa';                  // Hoʻokipa
const HONOLII = 'Honoli‘i';                  // Honoli‘i
const PUPUKEA = 'Pūpūkea, Hawaii';      // Pūpūkea, Hawaii (precomposed ū)
const ALII = "Haleiwa Ali'i Beach";
const COWELL = "Cowell's Beach Children's Surfing Area";

check('the roster spellings carry the marks this test is about',
  // H-o-ʻ-o-k-i-p-a is 8; H-o-n-o-l-i-‘-i is 8; P-ū-p-ū-k-e-a + ", " + H-a-w-a-i-i is 15.
  HOOKIPA.length === 8 && HONOLII.length === 8 && PUPUKEA.length === 15);

// foldForSearch, worked by hand.
check('Hoʻokipa folds to hookipa (ʻokina dropped, lower case)', foldForSearch(HOOKIPA) === 'hookipa');
check('Honoli‘i folds to honolii (left quote dropped)', foldForSearch(HONOLII) === 'honolii');
check('Pūpūkea, Hawaii folds to pupukea, hawaii (macrons dropped, comma kept)',
  foldForSearch(PUPUKEA) === 'pupukea, hawaii');
check('Pūpūkea typed as u + combining macron (U+0304) folds the same',
  foldForSearch('Pūpūkea') === 'pupukea');
check("Lānaʻi folds to lanai (macron and ʻokina dropped)", foldForSearch('Lānaʻi') === 'lanai');
check("ho'okipa, ho’okipa, ho‘okipa and ho`okipa all fold to hookipa",
  ['ho\'okipa', 'ho’okipa', 'ho‘okipa', 'ho`okipa'].every((q) => foldForSearch(q) === 'hookipa'));
check("Cowell's Beach Children's Surfing Area folds to cowells beach childrens surfing area",
  foldForSearch(COWELL) === 'cowells beach childrens surfing area');
check('a plain name is only lower-cased', foldForSearch('Kanaha Beach') === 'kanaha beach');

// searchSpots on a slice of the roster, in roster order.
const ROSTER = [
  { slug: 'haleiwa-ali-i-beach', name: ALII, state: 'HI' },
  { slug: 'honoli-i', name: HONOLII, state: 'HI' },
  { slug: 'ho-okipa', name: HOOKIPA, state: 'HI' },
  { slug: 'kanaha-beach', name: 'Kanaha Beach', state: 'HI' },
  { slug: 'pupukea-hawaii', name: PUPUKEA, state: 'HI' },
  { slug: 'cowell-s-beach', name: COWELL, state: 'CA' },
  { slug: 'solana-beach', name: 'Solana Beach', state: 'CA' },
];

for (const q of ['hookipa', "ho'okipa", 'Hoʻokipa', 'HOOKIPA', '  hookipa  ', 'ho’okipa']) {
  check(`"${q}" finds Hoʻokipa and nothing else`, names(searchSpots(ROSTER, q)) === JSON.stringify([HOOKIPA]),
    names(searchSpots(ROSTER, q)));
}
check('"honolii" finds Honoli‘i', names(searchSpots(ROSTER, 'honolii')) === JSON.stringify([HONOLII]));
check('"pupukea" finds Pūpūkea, Hawaii', names(searchSpots(ROSTER, 'pupukea')) === JSON.stringify([PUPUKEA]));
check('"Pūpūkea" typed with the macrons finds it too',
  names(searchSpots(ROSTER, 'Pūpūkea')) === JSON.stringify([PUPUKEA]));
check('"alii" finds Haleiwa Ali\'i Beach', names(searchSpots(ROSTER, 'alii')) === JSON.stringify([ALII]));
check('"cowells" finds Cowell\'s Beach', names(searchSpots(ROSTER, 'cowells')) === JSON.stringify([COWELL]));
check('"lana" finds Solana Beach (a plain name still matches inside a word)',
  names(searchSpots(ROSTER, 'lana')) === JSON.stringify(['Solana Beach']));
check('"lanai" finds no spot in this slice (the roster has no Lānaʻi spot)',
  searchSpots(ROSTER, 'lanai').length === 0);
check('a Lānaʻi spot would be found by "lanai"',
  names(searchSpots([{ name: 'Lānaʻi Test', state: null }], 'lanai')) === JSON.stringify(['Lānaʻi Test']));
check('the state still matches: "ca" finds the two California spots plus any name containing "ca"',
  // "ca": Cowell's (state CA), Solana Beach (state CA). No HI name contains "ca".
  names(searchSpots(ROSTER, 'ca')) === JSON.stringify([COWELL, 'Solana Beach']),
  names(searchSpots(ROSTER, 'ca')));
check('a state with a mark matches when typed without it',
  names(searchSpots([{ name: 'X', state: 'Hawaiʻi' }], 'hawaii')) === JSON.stringify(['X']));
check('an empty or blank query finds nothing', searchSpots(ROSTER, '').length === 0 && searchSpots(ROSTER, '   ').length === 0);
check('a query made only of marks finds nothing', searchSpots(ROSTER, "'ʻ").length === 0);
check('"beach" finds the four beaches in roster order',
  names(searchSpots(ROSTER, 'beach')) === JSON.stringify([ALII, 'Kanaha Beach', COWELL, 'Solana Beach']));
check('at most 8 results by default, and the limit is honoured',
  searchSpots(Array.from({ length: 12 }, (_, i) => ({ name: `Spot ${i}`, state: null })), 'spot').length === 8 &&
  searchSpots(ROSTER, 'beach', 2).length === 2);

// The three search boxes use the shared search, not their own copy.
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
const FRONTEND = dirname(dirname(fileURLToPath(import.meta.url)));
for (const f of ['components/HeroSearch.tsx', 'components/NavSearch.tsx', 'components/SearchBar.tsx']) {
  const src = readFileSync(join(FRONTEND, f), 'utf8');
  check(`${f} searches with searchSpots and not its own toLowerCase filter`,
    src.includes("from '@/lib/spotSearch'") && /searchSpots\(spots, q\)/.test(src) && !/\.toLowerCase\(\)\.includes/.test(src));
}

// RANKED: names that start with the query, then names that contain it, then the state; each group
// in roster order.
{
  const R = [
    { name: 'Dogmans Rincon', state: 'Puerto Rico' },
    { name: 'Steps Beach Rincon', state: 'Puerto Rico' },
    { name: 'Rincon', state: 'California' },
    { name: 'Rincon Domes', state: 'Puerto Rico' },
    { name: 'Coast Guard', state: 'Rincon County' },
  ];
  check('"rinc": starts-with first, then contains, then state, each in roster order',
    names(searchSpots(R, 'rinc')) === JSON.stringify(['Rincon', 'Rincon Domes', 'Dogmans Rincon', 'Steps Beach Rincon', 'Coast Guard']),
    names(searchSpots(R, 'rinc')));
  check('a name match outranks a state match that comes first in the roster',
    names(searchSpots([{ name: 'Kahului', state: 'Hawaii' }, { name: 'Hawaiian Hole', state: 'X' }], 'hawai'))
      === JSON.stringify(['Hawaiian Hole', 'Kahului']));
  check('folding still applies to the ranking', names(searchSpots([{ name: 'Rincón', state: null }, { name: 'Pt Rincon', state: null }], 'rinco'))
    === JSON.stringify(['Rincón', 'Pt Rincon']));
  check('the limit applies after ranking', names(searchSpots(R, 'rinc', 2)) === JSON.stringify(['Rincon', 'Rincon Domes']));
  check('the empty-result words', NO_RESULTS_TEXT === 'No spots found');
}

// ONE SEARCH LIST: the root layout provides it; the nav's and the homepage's search read it; the
// homepage sends only the ratings beside it, not a second list.
{
  const read = (f: string) => readFileSync(join(FRONTEND, f), 'utf8');
  const layout = read('app/layout.tsx');
  check('the layout provides the list once', (layout.match(/<SpotSearchListProvider /g) ?? []).length === 1);
  for (const f of ['components/HeroSearch.tsx', 'components/NavSearch.tsx']) {
    const src = read(f);
    check(`${f} reads the shared list`, src.includes('const spots = useSpotSearchList();'));
    check(`${f} says "No spots found" when nothing matches`, src.includes('{NO_RESULTS_TEXT}') && src.includes('results.length === 0'));
  }
  check('HeroSearch takes ratings, not a list', /export function HeroSearch\(\{ ratings \}: \{ ratings: HeroRatings \}\)/.test(read('components/HeroSearch.tsx')));
  const home = read('app/page.tsx');
  check('the homepage passes ratings only', home.includes('<HeroSearch ratings={ratings} />') && !home.includes('searchItems'));
  const nav = read('components/SiteNav.tsx');
  check('the mobile header has a search button that opens the search panel', nav.includes('data-mobile-search-button')
    && nav.includes('data-mobile-search-panel') && nav.includes('<NavSearch light autoFocus />'));
  check('...and focuses it within the tap (iOS keyboard)', nav.includes("document.querySelector<HTMLInputElement>('[data-mobile-search-panel] input')?.focus()"));
}

if (failures) {
  console.log(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log('\nspotSearch: ALL PASS');
