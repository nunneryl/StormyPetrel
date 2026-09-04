'use client';

// The hero star rating and its freshness label, on the VIEWER's clock.
//
// These read the same forecast row the tiles do. Leaving them on the server-render clock
// while CurrentConditions moved to the viewer's would put a star rating from one hour
// beside tiles from another — a visible contradiction on the same screen, and worse than
// the uniform staleness it replaced. Same selector, same input, so the two cannot disagree.
//
// The freshness label is the part that was actively misleading: it computed
// `Date.now() - valid_time` at RENDER time, so a statically-served page an hour old still
// announced "Updated 0 min ago". It now measures against the clock that chose the row.

import { useEffect, useState } from 'react';

import { StarRating } from './StarRating';
import { rowAgeMinutes, selectCurrentHour } from '@/lib/currentHour';
import type { Forecast } from '@/lib/types';

function freshnessLabel(minutes: number | null): string {
  if (minutes === null) return '—';
  if (minutes < 60) return `Updated ${minutes} min ago`;
  const h = Math.round(minutes / 60);
  if (h < 24) return `Updated ${h}h ago`;
  return `Updated ${Math.round(h / 24)}d ago`;
}

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
          : freshnessLabel(rowAgeMinutes(selection.row, nowMs))}
      </span>
    </div>
  );
}
