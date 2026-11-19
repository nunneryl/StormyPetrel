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

/** Spots whose name or state contains the query, both folded, in roster order, at most *limit*. */
export function searchSpots<T extends Searchable>(spots: T[], query: string, limit = 8): T[] {
  const term = foldForSearch(query.trim());
  if (!term) return [];
  return spots
    .filter((s) => foldForSearch(s.name).includes(term) || foldForSearch(s.state ?? '').includes(term))
    .slice(0, limit);
}
