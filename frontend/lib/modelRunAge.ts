import type { Forecast } from './types';

// "WAVE MODEL RUN IS N H OLD" — said on a spot page only when the newest NWPS cycle behind its
// forecast is MODEL_RUN_STALE_H hours old or more at the moment the page is read.
//
// WHY. NWPS v1.5 (NWS SCN 26-91) renamed every file from the 2026-11-09 12Z run. From the
// 23:23Z run that day the pipeline read cycles up to 30 h old, or none, for a day, and the
// pages showed those forecasts with nothing to say they were old. A cycle is normally 8 to
// 20 h old when read (2026-10-25 to 11-09), so 24 h stays quiet on an ordinary day; it is the
// same line as the pipeline's own warning (pipeline/forecast/nwps.py NWPS_STALE_AGE_H).
//
// DISPLAY ONLY. Nothing here reads or changes a rating; the rows are what the page already has.
// The cycle is the row's nwps_cycle (migration 019), the cycle each hour was read from. Rows
// with no NWPS cycle (null) are not counted, and a page with none says nothing.

export const MODEL_RUN_STALE_H = 24;
const HOUR_MS = 3_600_000;

/** The newest nwps_cycle among the rows, as stored; null when no row has one. */
export function newestNwpsCycle(rows: readonly Pick<Forecast, 'nwps_cycle'>[]): string | null {
  let best: string | null = null;
  let bestMs = -Infinity;
  for (const r of rows) {
    const c = r.nwps_cycle;
    if (!c) continue;
    const ms = Date.parse(c);
    if (ms > bestMs) {           // an unreadable value parses to NaN, and NaN is never greater
      best = c;
      bestMs = ms;
    }
  }
  return best;
}

/** Whole hours from the cycle to `nowMs`, rounded down. */
export function modelRunAgeHours(cycleIso: string, nowMs: number): number {
  return Math.floor((nowMs - Date.parse(cycleIso)) / HOUR_MS);
}

export type ModelRunNote = { hours: number; cycle: string };

/** The note to show, or null: null unless the newest cycle is MODEL_RUN_STALE_H hours old or more. */
export function modelRunNote(
  rows: readonly Pick<Forecast, 'nwps_cycle'>[],
  nowMs: number,
): ModelRunNote | null {
  const cycle = newestNwpsCycle(rows);
  if (cycle === null) return null;
  const hours = modelRunAgeHours(cycle, nowMs);
  return hours >= MODEL_RUN_STALE_H ? { hours, cycle } : null;
}

/** The words: "Wave model run is 30 h old". */
export function modelRunText(hours: number): string {
  return `Wave model run is ${hours} h old`;
}
