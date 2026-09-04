/**
 * Numeric pins for selectCurrentHour — which forecast row the hero tiles show.
 *
 * WHAT WENT WRONG. The spot page picked `forecasts.filter(vt >= now)[0]` — the next hour
 * BOUNDARY, not the hour containing now — and evaluated `now` on the SERVER, in a route
 * statically generated with `revalidate = 3600`. So the tiles could show a row chosen up to
 * an hour before the page was read. The Tide tile was the visible symptom only because tide
 * is the one field that is null at the start of its window; swell, wind, conditions and the
 * hero star rating were reading the same stale row.
 *
 * THE THREE THINGS PINNED HERE:
 *   1. The selected row is the one whose hour CONTAINS now — never the earliest available.
 *   2. Absent is its own state and is NOT the next row. A missing hour is a fact about the
 *      data, distinct from a present hour whose tide field is null.
 *   3. The boundary belongs to the hour it starts, so a just-expired row is not reused.
 *
 * EVERY EXPECTED VALUE IS A LITERAL. The fixture times are written as explicit epoch ms
 * with the ISO string beside them, and no expectation is produced by calling
 * selectCurrentHour. That matters here more than usual: the bug this replaces was locked in
 * by a test whose literals were hand-computed from the wrong formula.
 *
 *     node --experimental-strip-types frontend/lib/currentHour.test.mts
 */
import {
  CHART_WINDOW_HOURS, chartWindow, HOUR_MS, rowAgeMinutes, selectCurrentHour,
} from './currentHour.ts';
import type { Forecast } from './types.ts';

let failures = 0;

function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}

function eq(name: string, got: unknown, want: unknown): void {
  check(name, got === want, `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

// Epoch ms for 2026-09-04T10:00:00Z, written out rather than computed:
//   Date.parse('2026-09-04T10:00:00Z') === 1788516000000
const T10 = 1788516000000;
eq('the fixture anchor really is 2026-09-04T10:00:00Z', Date.parse('2026-09-04T10:00:00Z'), T10);
eq('an hour is 3600000 ms', HOUR_MS, 3600000);

function row(iso: string, stars: number): Forecast {
  // Only the two fields the selector and the age helper read; the rest of Forecast is not
  // consulted, and casting keeps the fixture honest about that.
  return { valid_time: iso, stars } as unknown as Forecast;
}

// A window reaching two hours BACK and four forward, as the page now passes it.
const rows: Forecast[] = [
  row('2026-09-04T08:00:00Z', 1),
  row('2026-09-04T09:00:00Z', 2),
  row('2026-09-04T10:00:00Z', 3),
  row('2026-09-04T11:00:00Z', 4),
  row('2026-09-04T12:00:00Z', 5),
];

// --------------------------------------------------------------------------- //
// 1 — the hour CONTAINING now, never the earliest row                          //
// --------------------------------------------------------------------------- //
eq('exactly on the hour selects that hour', selectCurrentHour(rows, T10).row?.stars, 3);
eq('mid-hour selects the hour it is inside',
   selectCurrentHour(rows, T10 + 30 * 60000).row?.stars, 3);
eq('one ms before the next hour is still this hour',
   selectCurrentHour(rows, T10 + HOUR_MS - 1).row?.stars, 3);
// THE REGRESSION. The old code returned rows[0] (08:00, stars 1) for every one of these.
check('never returns the earliest row when a later hour is current',
      selectCurrentHour(rows, T10).row?.stars !== 1);
eq('an hour into the future window selects the later row',
   selectCurrentHour(rows, T10 + HOUR_MS).row?.stars, 4);
eq('two hours back selects the earliest row, when that IS the current hour',
   selectCurrentHour(rows, T10 - 2 * HOUR_MS).row?.stars, 1);

// --------------------------------------------------------------------------- //
// 2 — the boundary belongs to the hour it starts                               //
// --------------------------------------------------------------------------- //
// At exactly 11:00:00 the 11:00 row is current and the 10:00 row has expired. Getting this
// backwards is precisely how a just-expired row stayed on screen for another hour.
eq('at the boundary the NEW hour wins', selectCurrentHour(rows, T10 + HOUR_MS).row?.stars, 4);
check('...and the expired hour is not reused',
      selectCurrentHour(rows, T10 + HOUR_MS).row?.stars !== 3);
eq('index is reported alongside the row', selectCurrentHour(rows, T10).index, 2);

// --------------------------------------------------------------------------- //
// 3 — ABSENT is its own state, not the next available row                      //
// --------------------------------------------------------------------------- //
// A window with 10:00 missing: 09:00 and 11:00 exist either side of it.
const gapped: Forecast[] = [
  row('2026-09-04T09:00:00Z', 2),
  row('2026-09-04T11:00:00Z', 4),
];
eq('a missing current hour reports absent', selectCurrentHour(gapped, T10).state, 'absent');
eq('...and returns no row at all', selectCurrentHour(gapped, T10).row, null);
eq('...and index -1', selectCurrentHour(gapped, T10).index, -1);
// THE POINT OF THE STATE: it must not fall through to 11:00 (stars 4) or back to 09:00 (2).
check('absent does not silently become the NEXT row',
      selectCurrentHour(gapped, T10).row === null);
eq('the state is "current" when a row does cover the hour',
   selectCurrentHour(rows, T10).state, 'current');

// Empty and malformed input are absent, not a throw.
eq('no rows is absent', selectCurrentHour([], T10).state, 'absent');
eq('null rows is absent', selectCurrentHour(null, T10).state, 'absent');
eq('a NaN clock is absent', selectCurrentHour(rows, Number.NaN).state, 'absent');
// An unparseable valid_time is skipped, and a good row after it is still found.
const dirty: Forecast[] = [row('not-a-time', 9), row('2026-09-04T10:00:00Z', 3)];
eq('an unparseable row is skipped, not fatal', selectCurrentHour(dirty, T10).row?.stars, 3);
// Ordering is not assumed — the query orders by valid_time, but the selector must not rely on it.
const shuffled: Forecast[] = [
  row('2026-09-04T12:00:00Z', 5),
  row('2026-09-04T10:00:00Z', 3),
  row('2026-09-04T08:00:00Z', 1),
];
eq('an unordered array still finds the current hour',
   selectCurrentHour(shuffled, T10 + 15 * 60000).row?.stars, 3);

// --------------------------------------------------------------------------- //
// 4 — the freshness measure uses the SAME clock that chose the row             //
// --------------------------------------------------------------------------- //
// The old label did Date.now() - valid_time at render time, so a page served an hour after
// generation still said "Updated 0 min ago".
eq('a row selected exactly on its hour is 0 min old', rowAgeMinutes(rows[2], T10), 0);
eq('30 minutes into the hour reads 30', rowAgeMinutes(rows[2], T10 + 30 * 60000), 30);
// 90 minutes past the 10:00 row — what a stale static page was silently showing as "0 min".
eq('90 minutes past reads 90, not 0', rowAgeMinutes(rows[2], T10 + 90 * 60000), 90);
eq('a future row never reads negative', rowAgeMinutes(rows[4], T10), 0);
eq('no row has no age', rowAgeMinutes(null, T10), null);
eq('an unparseable time has no age', rowAgeMinutes(row('nope', 1), T10), null);

// --------------------------------------------------------------------------- //
// 5 — THE CHART WINDOW IS UNCHANGED by the current-hour fix                     //
// --------------------------------------------------------------------------- //
// The charts and the 7-day grid keep the forward window they always had: >= now, <= now+48h,
// inclusive at BOTH ends. Only the hero tiles moved to bucket matching. Bounds written out.
eq('the chart window is 48 hours', CHART_WINDOW_HOURS, 48);
// From the 5-row fixture at now = 10:00, the forward rows are 10:00, 11:00, 12:00.
eq('the chart starts at now, not before it', chartWindow(rows, T10).length, 3);
eq('...and its first row is the current hour', chartWindow(rows, T10)[0]?.stars, 3);
eq('...and the two past rows are excluded', chartWindow(rows, T10)[0]?.stars !== 1, true);
// A row exactly ON the far cutoff is INCLUDED (<=, not <).
const far: Forecast[] = [
  row('2026-09-04T10:00:00Z', 3),
  row('2026-09-06T10:00:00Z', 8),   // exactly now + 48h
  row('2026-09-06T11:00:00Z', 9),   // now + 49h, outside
];
eq('a row exactly on the 48h cutoff is included', chartWindow(far, T10).length, 2);
eq('...and the row one hour past it is not', chartWindow(far, T10)[1]?.stars, 8);
// Mid-hour: the chart's lower bound is now, so the current partial hour drops out. That is
// PRE-EXISTING behaviour and is pinned here precisely so the fix cannot be blamed for it.
eq('mid-hour the chart starts at the NEXT boundary, as it always did',
   chartWindow(rows, T10 + 30 * 60000)[0]?.stars, 4);
eq('no rows gives an empty window', chartWindow([], T10).length, 0);
eq('a NaN clock gives an empty window', chartWindow(rows, Number.NaN).length, 0);

if (failures > 0) {
  throw new Error(`currentHour: ${failures} FAILURE(S)`);
}
console.log('\ncurrentHour: ALL PASS');
