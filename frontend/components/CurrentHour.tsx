'use client';

// ONE CURRENT HOUR FOR THE CONDITIONS TILES AND THE SWELL BREAKDOWN.
//
// The Conditions tile re-picks its hour on the viewer's clock (lib/currentHour.selectCurrentHour),
// while the swell breakdown used to get the row the SERVER picked when the page was built, and the
// spot routes are prerendered with `revalidate = 3600`. A reader could therefore see two hours at
// once: on 2026-11-12, Jacksonville Beach Pier's tile said "57% wind sea" (the 18Z row, chop_ratio
// 0.571) while the breakdown's wind sea row said 56% (the 17Z row, 0.561).
//
// This provider holds the ONE clock both read: the server's at first paint (so the markup matches
// on both sides), then the viewer's on mount and once a minute after. Both components take their
// row from `useCurrentHour()`, so they change hour in the same render and never disagree.
import { createContext, useContext, useEffect, useState } from 'react';

import { selectCurrentHour, type HourSelection } from '@/lib/currentHour';
import type { Forecast } from '@/lib/types';

export type CurrentHour = {
  /** The spot's forecast rows, including the recent past (see selectCurrentHour). */
  rows: Forecast[];
  nowMs: number;
  selection: HourSelection;
};

const Ctx = createContext<CurrentHour | null>(null);

export function CurrentHourProvider({
  rows,
  serverNowMs,
  children,
}: {
  rows: Forecast[];
  /** The server's clock at render time: the first paint's, on both sides. */
  serverNowMs: number;
  children: React.ReactNode;
}) {
  const [nowMs, setNowMs] = useState(serverNowMs);
  useEffect(() => {
    setNowMs(Date.now());
    const id = setInterval(() => setNowMs(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);
  const selection = selectCurrentHour(rows, nowMs);
  return <Ctx.Provider value={{ rows, nowMs, selection }}>{children}</Ctx.Provider>;
}

/** The current hour every reader inside CurrentHourProvider shares. */
export function useCurrentHour(): CurrentHour {
  const v = useContext(Ctx);
  if (!v) throw new Error('useCurrentHour must be inside <CurrentHourProvider>');
  return v;
}
