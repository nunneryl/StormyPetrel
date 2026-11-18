'use client';

// The hero star rating and its freshness label, on the VIEWER's clock.
//
// These read the same forecast row the tiles do. Leaving them on the server-render clock
// while CurrentConditions moved to the viewer's would put a star rating from one hour
// beside tiles from another — a visible contradiction on the same screen, and worse than
// the uniform staleness it replaced. Same selector, same input, so the two cannot disagree.
//
// The freshness label is the age of the model run behind the forecast ("Model run 8 h ago",
// lib/modelRunAge.ts), on the viewer's clock. It used to say "Updated N min ago" from the start of
// the current hour, which an old copy of the page still said with a forecast 36 h old.

import { useEffect, useState } from 'react';

import { StarRating } from './StarRating';
import { selectCurrentHour } from '@/lib/currentHour';
import { modelRunLabel } from '@/lib/modelRunAge';
import type { Forecast } from '@/lib/types';

export function CurrentHero({
  rows,
  serverNowMs,
}: {
  rows: Forecast[];
  serverNowMs: number;
}) {
  const [nowMs, setNowMs] = useState(serverNowMs);
  useEffect(() => {
    setNowMs(Date.now());
    const id = setInterval(() => setNowMs(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);

  const selection = selectCurrentHour(rows, nowMs);
  return (
    <div className="flex flex-col items-end gap-1 shrink-0">
      <StarRating score={selection.row?.stars ?? 0} size="xl" showScore />
      <span className="text-[10px] uppercase tracking-widest2 text-text-muted">
        {selection.state === 'absent'
          ? 'No current hour'
          : modelRunLabel(rows, nowMs)}
      </span>
    </div>
  );
}
