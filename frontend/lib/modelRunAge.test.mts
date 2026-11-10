/**
 * "Wave model run is N h old" (lib/modelRunAge): shown only when the newest NWPS cycle behind a
 * spot's rows is 24 hours old or more when the page is read.
 *
 * THE CASE IT IS FOR. On 2026-11-10 at 08:15Z the pipeline read box's 2026-11-09 00Z cycle
 * (NWPS v1.5 had renamed the newer files): 32 h 15 min old, so the page says "32 h old". The
 * same office's 11-10 00Z cycle read at 14:07Z is 14 h old and the page says nothing.
 *
 * EVERY EXPECTED VALUE IS A LITERAL, with its epoch ms written beside the ISO string and the
 * subtraction in a comment. None is produced by calling the function under test.
 *
 *     node --experimental-strip-types frontend/lib/modelRunAge.test.mts
 */
import {
  MODEL_RUN_STALE_H, modelRunAgeHours, modelRunNote, modelRunText, newestNwpsCycle,
} from './modelRunAge.ts';

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
  check(name, JSON.stringify(got) === JSON.stringify(want),
    `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

// Epoch ms, written out:
//   2026-11-09T00:00:00Z = 1794182400000
//   2026-11-10T00:00:00Z = 1794268800000   (+86400000)
//   2026-11-10T08:15:40Z = 1794298540000   (+8 h 15 min 40 s = 29740000)
//   2026-11-10T14:07:58Z = 1794319678000   (+14 h 7 min 58 s = 50878000)
const C_1109_00 = 1794182400000;
const C_1110_00 = 1794268800000;
const AT_0815 = 1794298540000;
const AT_1407 = 1794319678000;
eq('anchor 2026-11-09T00:00:00Z', Date.parse('2026-11-09T00:00:00Z'), C_1109_00);
eq('anchor 2026-11-10T00:00:00Z', Date.parse('2026-11-10T00:00:00Z'), C_1110_00);
eq('anchor 2026-11-10T08:15:40Z', Date.parse('2026-11-10T08:15:40Z'), AT_0815);
eq('anchor 2026-11-10T14:07:58Z', Date.parse('2026-11-10T14:07:58Z'), AT_1407);
eq('the line is 24 hours', MODEL_RUN_STALE_H, 24);

const r = (nwps_cycle: string | null) => ({ nwps_cycle });

// newestNwpsCycle: the latest by time, as stored, nulls and junk skipped.
eq('newest of three, PostgREST timestamptz form kept as stored',
  newestNwpsCycle([r('2026-11-09T00:00:00+00:00'), r('2026-11-09T18:00:00+00:00'),
    r('2026-11-09T06:00:00+00:00')]), '2026-11-09T18:00:00+00:00');
eq('compared by time, not by text: 11-09 23Z+00 is older than 11-09 18Z-08 (11-10 02Z)',
  newestNwpsCycle([r('2026-11-09T18:00:00-08:00'), r('2026-11-09T23:00:00+00:00')]),
  '2026-11-09T18:00:00-08:00');
eq('nulls are skipped', newestNwpsCycle([r(null), r('2026-11-09T00:00:00Z'), r(null)]),
  '2026-11-09T00:00:00Z');
eq('an unreadable value is skipped', newestNwpsCycle([r('not a time'), r('2026-11-09T00:00:00Z')]),
  '2026-11-09T00:00:00Z');
eq('no cycle at all: null', newestNwpsCycle([r(null), r(null)]), null);
eq('no rows: null', newestNwpsCycle([]), null);

// modelRunAgeHours: whole hours, rounded down.
// 08:15:40 on 11-10 - 00Z on 11-09 = 32 h 15 min 40 s -> 32
eq('box at 08:15Z read 11-09 00Z: 32 h', modelRunAgeHours('2026-11-09T00:00:00Z', AT_0815), 32);
// 14:07:58 - 00:00 on 11-10 = 14 h 7 min 58 s -> 14
eq('box at 14:07Z read 11-10 00Z: 14 h', modelRunAgeHours('2026-11-10T00:00:00Z', AT_1407), 14);

// modelRunNote: only from 24 h.
eq('32 h old: the note', modelRunNote([r('2026-11-09T00:00:00Z')], AT_0815),
  { hours: 32, cycle: '2026-11-09T00:00:00Z' });
eq('14 h old: nothing', modelRunNote([r('2026-11-10T00:00:00Z')], AT_1407), null);
// 24 h exactly after 11-09 00Z is 1794268800000; one ms before is 23 h 59 min 59.999 s.
eq('exactly 24 h: the note', modelRunNote([r('2026-11-09T00:00:00Z')], 1794268800000),
  { hours: 24, cycle: '2026-11-09T00:00:00Z' });
eq('one ms under 24 h: nothing', modelRunNote([r('2026-11-09T00:00:00Z')], 1794268799999), null);
eq('the newest cycle decides: an old row beside a fresh one says nothing',
  modelRunNote([r('2026-11-08T00:00:00Z'), r('2026-11-10T00:00:00Z')], AT_1407), null);
eq('no NWPS cycle on the page: nothing, however late', modelRunNote([r(null)], AT_1407), null);

eq('the words', modelRunText(32), 'Wave model run is 32 h old');

if (failures > 0) {
  console.log(`\nmodelRunAge: ${failures} FAILED`);
  process.exit(1);
}
console.log('\nmodelRunAge: ALL PASS');
