// Spot search, shared by the three search boxes (components/HeroSearch.tsx, NavSearch.tsx,
// SearchBar.tsx). Accents and the Hawaiian ʻokina do not have to be typed: "hookipa",
// "ho'okipa" and "Hoʻokipa" all find Hoʻokipa, and "pupukea" finds Pūpūkea.
//
// Both the query and the spot's name and state are folded the same way:
//   1. NFD splits a letter from its accent (ū → u + U+0304 combining macron);
//   2. every combining mark (Unicode category M) is dropped;
//   3. the ʻokina and apostrophe-like marks are dropped: ʻ (U+02BB), ' (U+0027), ’ (U+2019),
//      ‘ (U+2018) and ` (U+0060). The ʻokina is a letter in Unicode, not a combining mark, so
//      step 2 does not remove it;
//   4. lower case.

const IGNORABLE = /[ʻ'’‘`]/g;
const COMBINING = /\p{M}/gu;

/** Text folded for matching: no accents, no ʻokina or apostrophes, lower case. */
export function foldForSearch(text: string): string {
  return text.normalize('NFD').replace(COMBINING, '').replace(IGNORABLE, '').toLowerCase();
}

export type Searchable = { name: string; state: string | null };

/** Spots matching the query, best first, at most *limit*. Three groups, each in roster order:
 *  names that START with the query ("rin" finds Rincon first), then names that contain it
 *  (Dogmans Rincon), then spots whose state contains it. Query, names and states are folded the
 *  same way (foldForSearch). */
export function searchSpots<T extends Searchable>(spots: T[], query: string, limit = 8): T[] {
  const term = foldForSearch(query.trim());
  if (!term) return [];
  const starts: T[] = [];
  const contains: T[] = [];
  const inState: T[] = [];
  for (const s of spots) {
    const name = foldForSearch(s.name);
    if (name.startsWith(term)) starts.push(s);
    else if (name.includes(term)) contains.push(s);
    else if (foldForSearch(s.state ?? '').includes(term)) inState.push(s);
  }
  return [...starts, ...contains, ...inState].slice(0, limit);
}

/** What a search box says when the query matches nothing. */
export const NO_RESULTS_TEXT = 'No spots found';
