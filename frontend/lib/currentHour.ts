import type { Forecast } from './types';

/** One forecast hour, in ms. `forecasts` is UNIQUE(spot_id, valid_time, source) with
 *  valid_time on the UTC hour, so an hour bucket is exactly this wide. */
export const HOUR_MS = 3_600_000;

/**
 * Which forecast row the hero tiles should show, and whether one exists at all.
 *
 * THE ROW WHOSE HOUR CONTAINS `nowMs`, not the earliest row at or after it. The page used
 * to take `forecasts.filter(vt >= now)[0]`, which is the NEXT hour boundary rather than the
 * current one, and — because the page is statically generated with `revalidate = 3600` —
 * that `now` was the moment the HTML was rendered, not the moment it was read. A viewer
 * could be looking at a row picked up to an hour earlier. See selectCurrentHour's callers:
 * the server picks with its own clock for the prerendered HTML, and the client re-picks
 * with the viewer's clock on mount.
 *
 * ABSENT IS ITS OWN STATE, and is deliberately NOT the next available row. A missing row
 * for the current hour means the pipeline did not publish that hour — a different fact from
 * "the hour exists and its tide is null", and one the reader should be told rather than
 * shown a neighbouring hour's numbers under a heading that says now. Falling through to the
 * next row is exactly how an expired hour got rendered as current in the first place.
 *
 * Pure and total: no clock of its own, no sorting assumption (a linear scan over ~180 rows
 * costs nothing and cannot be broken by an unordered query), and a bad or missing
 * valid_time is skipped rather than thrown on.
 */
export type HourSelection =
  | { state: 'current'; row: Forecast; index: number }
  | { state: 'absent'; row: null; index: -1 };

export function selectCurrentHour(
  rows: readonly Forecast[] | null | undefined,
  nowMs: number,
): HourSelection {
  if (!rows || rows.length === 0 || !Number.isFinite(nowMs)) {
    return { state: 'absent', row: null, index: -1 };
  }
  for (let i = 0; i < rows.length; i += 1) {
    const t = Date.parse(rows[i].valid_time);
    // DOCUMENTARY, NOT LOAD-BEARING: every comparison with NaN is false, so an unparseable
    // row is skipped by the condition below whether or not this line is here (verified —
    // removing it is an equivalent mutant). It stays because relying on IEEE-754 NaN
    // semantics to skip bad data is a thing a reader should not have to work out.
    if (Number.isNaN(t)) continue;
    // Half-open [t, t + 1h): the hour that CONTAINS now. An hour boundary belongs to the
    // hour it starts, so at exactly 13:00:00 the 13:00 row is current and the 12:00 row is
    // not — which is what stops a just-expired row being shown for another hour.
    if (t <= nowMs && nowMs < t + HOUR_MS) {
      return { state: 'current', row: rows[i], index: i };
    }
  }
  return { state: 'absent', row: null, index: -1 };
}

/**
 * How stale the displayed row is, in whole minutes, or null when there is nothing to
 * measure. Reported against the SAME `nowMs` the selection used, so the label cannot claim
 * "0 min ago" about a row picked by a different clock — which is what the old
 * freshnessLabel did on every statically-served page.
 */
export function rowAgeMinutes(row: Forecast | null, nowMs: number): number | null {
  if (!row) return null;
  const t = Date.parse(row.valid_time);
  if (Number.isNaN(t) || !Number.isFinite(nowMs)) return null;
  return Math.max(0, Math.round((nowMs - t) / 60000));
}

/** The 48-hour chart window, in hours. Lives beside the selector because both read the same
 *  row array and must not drift apart about what "the forecast rows" are. */
export const CHART_WINDOW_HOURS = 48;

/**
 * Rows for the 48-hour charts: `nowMs` forward, inclusive at both ends.
 *
 * EXTRACTED UNCHANGED from the spot page so it can be pinned. The semantics are exactly
 * what `upcoming.filter(vt <= now + 48h)` produced — `vt >= nowMs && vt <= nowMs + 48h` —
 * and the current-hour fix deliberately does NOT touch them. The charts want a forward
 * window and the grid wants the same rows; only the hero tiles moved to bucket matching.
 */
export function chartWindow(
  rows: readonly Forecast[] | null | undefined,
  nowMs: number,
  hours: number = CHART_WINDOW_HOURS,
): Forecast[] {
  if (!rows || !Number.isFinite(nowMs)) return [];
  const cutoff = nowMs + hours * HOUR_MS;
  const out: Forecast[] = [];
  for (const r of rows) {
    const t = Date.parse(r.valid_time);
    if (Number.isNaN(t)) continue;
    if (t >= nowMs && t <= cutoff) out.push(r);
  }
  return out;
}
