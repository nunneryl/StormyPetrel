/**
 * Numeric pins for fmtHeightRange — the swell height as a READING RANGE: the standard step of a
 * fixed ladder that the height falls in.
 *
 *   under 1 · 1-2 · 2-3 · 3-4 · 4-5 · 5-6 · 6-8 · 8-10 · 10-12 · 12-15 · 15-20 · 20-25 · 25-30,
 *   then 5 ft steps. A height exactly on a boundary goes to the HIGHER step.
 *
 * WHAT IT REPLACED. fmtFtRange(lo, hi, point) printed face_lo_ft-face_hi_ft, a band divided out
 * of each calibrated spot's measured p25/p75 face ratio, and a bare "4.3ft" at the 500-odd
 * spots without one: two meanings, and most spots had no range. The ladder gives every height
 * one meaning. Its pins (outward rounding, the collapse sweep, the band-misses-its-point
 * backstop) went with it; there is no band left for them to hold.
 *
 * EVERY EXPECTED VALUE IS WRITTEN OUT BY HAND from the ladder above. None is derived by calling
 * the function under test; the sweep at the end asserts a property read back off the string.
 *
 *     node --experimental-strip-types frontend/lib/formatting.test.mts
 *     (or: npm --prefix frontend run test)
 */
import { fmtFt, fmtHeightRange } from './formatting.ts';

let failures = 0;

function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}

function eq(name: string, got: string, want: string): void {
  check(name, got === want, `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

// --------------------------------------------------------------------------- //
// 1 — every boundary: just under it, and on it (which goes to the higher step)  //
// --------------------------------------------------------------------------- //
const BOUNDARIES: [number, string][] = [
  [0.99, 'under 1ft'], [1.0, '1-2ft'],
  [1.99, '1-2ft'], [2.0, '2-3ft'],
  [2.99, '2-3ft'], [3.0, '3-4ft'],
  [3.99, '3-4ft'], [4.0, '4-5ft'],
  [4.99, '4-5ft'], [5.0, '5-6ft'],
  [5.99, '5-6ft'], [6.0, '6-8ft'],
  [7.99, '6-8ft'], [8.0, '8-10ft'],
  [9.99, '8-10ft'], [10.0, '10-12ft'],
  [11.99, '10-12ft'], [12.0, '12-15ft'],
  [14.99, '12-15ft'], [15.0, '15-20ft'],
  [19.99, '15-20ft'], [20.0, '20-25ft'],
  [24.99, '20-25ft'], [25.0, '25-30ft'],
  [29.99, '25-30ft'], [30.0, '30-35ft'],
  [34.99, '30-35ft'], [35.0, '35-40ft'],
];
for (const [h, want] of BOUNDARIES) eq(`${h} ft reads ${want}`, fmtHeightRange(h), want);

// --------------------------------------------------------------------------- //
// 2 — the worked examples (gate run 33, 21 October 2026 14:00Z)                 //
// --------------------------------------------------------------------------- //
eq('Jennettes Pier 11.62 ft reads 10-12ft', fmtHeightRange(11.62), '10-12ft');
eq('Avalon NJ 9.75 ft reads 8-10ft', fmtHeightRange(9.75), '8-10ft');
eq('Tres Palmas 4.34 ft reads 4-5ft', fmtHeightRange(4.34), '4-5ft');
eq('Huntington Beach Pier 2.75 ft reads 2-3ft', fmtHeightRange(2.75), '2-3ft');
eq('Steamer Lane 2.11 ft reads 2-3ft', fmtHeightRange(2.11), '2-3ft');

// --------------------------------------------------------------------------- //
// 3 — small waves, big waves, nothing                                          //
// --------------------------------------------------------------------------- //
eq('a flat 0 ft reads under 1ft, never 0-1ft', fmtHeightRange(0), 'under 1ft');
eq('0.4 ft reads under 1ft', fmtHeightRange(0.4), 'under 1ft');
eq('a negative height (a stale row) still reads under 1ft', fmtHeightRange(-0.5), 'under 1ft');
eq('47 ft reads 45-50ft (5 ft steps past 30)', fmtHeightRange(47), '45-50ft');
eq('60 ft reads 60-65ft', fmtHeightRange(60), '60-65ft');
eq('null is the em dash', fmtHeightRange(null), '—');
eq('undefined is the em dash', fmtHeightRange(undefined), '—');
eq('NaN is the em dash', fmtHeightRange(NaN), '—');

// --------------------------------------------------------------------------- //
// 4 — the property, for any height: low <= height < high, on the ladder         //
// --------------------------------------------------------------------------- //
{
  const LADDER = new Set([1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60]);
  let bad = 0;
  let n = 0;
  for (let i = 100; i < 6000; i++) {
    const h = i / 100;
    const m = /^(\d+)-(\d+)ft$/.exec(fmtHeightRange(h));
    n += 1;
    if (!m) { bad += 1; continue; }
    const lo = Number(m[1]);
    const hi = Number(m[2]);
    if (!(lo <= h && h < hi && LADDER.has(lo) && LADDER.has(hi) && hi > lo)) bad += 1;
  }
  check(`every height from 1.00 to 59.99 ft sits in a ladder step (${bad} of ${n} did not)`, bad === 0);
}

// --------------------------------------------------------------------------- //
// 5 — fmtFt itself is unchanged                                                //
// --------------------------------------------------------------------------- //
eq('fmtFt still publishes one decimal', fmtFt(4.0), '4.0ft');
eq('fmtFt null is the em dash', fmtFt(null), '—');

if (failures > 0) {
  throw new Error(`formatting: ${failures} FAILURE(S)`);
}
console.log('\nformatting: ALL PASS');
