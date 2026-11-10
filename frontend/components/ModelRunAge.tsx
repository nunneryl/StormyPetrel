'use client';

// "Wave model run is N h old" under the spot's header, on the VIEWER's clock (lib/modelRunAge).
//
// The page is prerendered and kept for an hour, so the age is measured again once mounted, the
// way CurrentHero measures its "Updated" label: the server's clock for the first paint, then the
// viewer's, once a minute. Renders nothing while the newest cycle is under 24 hours old.

import { useEffect, useState } from 'react';

import { modelRunNote, modelRunText } from '@/lib/modelRunAge';
import { fmtStamp } from '@/lib/spotTime';
import type { Forecast } from '@/lib/types';

export function ModelRunAge({
  rows,
  serverNowMs,
  timeZone,
}: {
  rows: Forecast[];
  serverNowMs: number;
  timeZone: string;
}) {
  const [nowMs, setNowMs] = useState(serverNowMs);
  useEffect(() => {
    setNowMs(Date.now());
    const id = setInterval(() => setNowMs(Date.now()), 60_000);
    return () => clearInterval(id);
  }, []);

  const note = modelRunNote(rows, nowMs);
  if (!note) return null;
  return (
    <p className="text-xs text-text-muted" data-model-run-age={note.hours}>
      {modelRunText(note.hours)} · NOAA NWPS run of {fmtStamp(note.cycle, timeZone)}
    </p>
  );
}
