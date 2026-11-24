'use client';

import { createContext, useContext } from 'react';

/** One spot as every search box lists it: what it matches on and where it links. */
export type SpotSearchItem = {
  slug: string;
  name: string;
  state: string | null;
};

const Ctx = createContext<SpotSearchItem[]>([]);

/**
 * THE ONE SEARCH LIST. The root layout reads every spot's slug, name and state once and provides
 * them here; the nav's search and the homepage's search both read this. The homepage used to send
 * its own second copy of the list (with ratings) beside the nav's, so the browser got two.
 */
export function SpotSearchListProvider({ spots, children }: { spots: SpotSearchItem[]; children: React.ReactNode }) {
  return <Ctx.Provider value={spots}>{children}</Ctx.Provider>;
}

export function useSpotSearchList(): SpotSearchItem[] {
  return useContext(Ctx);
}
