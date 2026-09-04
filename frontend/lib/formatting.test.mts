/**
 * Numeric pins for fmtFtRange — the published SWELL HEIGHT band.
 *
 * WHAT IS BEING PINNED, and why each part matters:
 *
 *  1. WHOLE FEET. The measured band is roughly +/-20%, so tenths are precision the
 *     measurement does not have. A regression to toFixed(1) would publish "3.3-4.9ft".
 *
 *  2. NEVER "3-3ft". When both ends round to the same whole foot the band is narrower than
 *     the display resolution, and the honest render is that single number. A repeated bound
 *     reads as a rendering bug and tells the reader nothing the single value did not.
 *     This is the case the brief called out by name and it is pinned three ways.
 *
 *  3. NO BAND MEANS THE POINT, NOT A FABRICATED BAND. 466 of 648 spots have no measured
 *     spread. They must fall through to fmtFt's one-decimal point estimate — the SAME
 *     string the site published before this change — and never to a default width. A
 *     default would be indistinguishable from a measured band to anyone reading the page.
 *
 * EVERY EXPECTED VALUE IS HAND-COMPUTED, with the arithmetic in a comment. None is derived
 * by calling the function under test — in particular the rounding is written out as a
 * literal rather than as Math.round of the input.
 *
 *     node --experimental-strip-types frontend/lib/formatting.test.mts
 *     (or: npm --prefix frontend run test)
 */
import { fmtFt, fmtFtRange } from './formatting.ts';

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
// 1 — the worked example from the decision                                     //
// --------------------------------------------------------------------------- //
// A 4.0 ft corrected face with the roster-median p25/p75 band:
//   lo = 4.0 / 1.22 = 3.2787 -> rounds to 3
//   hi = 4.0 / 0.81 = 4.9383 -> rounds to 5
eq('4.0 ft with the median band publishes 3-5ft', fmtFtRange(3.2787, 4.9383, 4.0), '3-5ft');

// --------------------------------------------------------------------------- //
// 2 — whole feet, never tenths                                                 //
// --------------------------------------------------------------------------- //
eq('tenths are rounded away on the low end', fmtFtRange(2.9, 6.1, 4.0), '3-6ft');
eq('tenths are rounded away on the high end', fmtFtRange(2.4, 6.4, 4.0), '2-6ft');
// Math.round is half-UP for positives: 2.5 -> 3, 5.5 -> 6. Written out, not computed.
eq('a .5 low end rounds up', fmtFtRange(2.5, 7.4, 5.0), '3-7ft');
eq('a .5 high end rounds up', fmtFtRange(1.6, 5.5, 3.0), '2-6ft');

// --------------------------------------------------------------------------- //
// 3 — THE EQUAL-AFTER-ROUNDING CASE. "3-3ft" must never appear.                //
// --------------------------------------------------------------------------- //
// A 1.2 ft face with the same +/-20% band is narrower than one whole foot:
//   lo = 1.2 / 1.22 = 0.9836 -> 1
//   hi = 1.2 / 0.81 = 1.4815 -> 1
eq('a band narrower than a foot collapses to the single value', fmtFtRange(0.9836, 1.4815, 1.2), '1ft');
eq('exactly equal bounds collapse', fmtFtRange(3.0, 3.0, 3.0), '3ft');
// 3.4 and 2.6 both round to 3 from opposite sides — the case a naive `lo === hi` misses.
eq('bounds equal only AFTER rounding still collapse', fmtFtRange(2.6, 3.4, 3.0), '3ft');
check('no collapsed render ever contains a hyphen', !fmtFtRange(2.6, 3.4, 3.0).includes('-'));

// --------------------------------------------------------------------------- //
// 4 — the 466 spots with no measured spread                                    //
// --------------------------------------------------------------------------- //
// Both ends absent -> the point estimate, at one decimal, exactly as before this change.
eq('no band at all publishes the point estimate', fmtFtRange(null, null, 4.0), '4.0ft');
eq('undefined bounds publish the point estimate', fmtFtRange(undefined, undefined, 3.7), '3.7ft');
// A HALF-PRESENT record must not be half-used: one measured end and one invented one is
// worse than no band, because it looks measured.
eq('a missing high end publishes the point, not a one-sided band', fmtFtRange(3.2, null, 4.0), '4.0ft');
eq('a missing low end publishes the point, not a one-sided band', fmtFtRange(null, 4.9, 4.0), '4.0ft');
eq('NaN bounds fall through to the point', fmtFtRange(NaN, 4.9, 4.0), '4.0ft');
// And with no point either, the em dash the rest of the site uses for "no data".
eq('no band and no point is the em dash', fmtFtRange(null, null, null), '—');

// THE ANTI-FABRICATION PIN. An unmeasured spot must render the SAME string fmtFt gives —
// so a future "helpful" default width fails here rather than shipping silently.
eq('an unmeasured spot renders exactly fmtFt(point)', fmtFtRange(null, null, 4.0), fmtFt(4.0));
check(
  'a measured band and an unmeasured spot are visibly different strings',
  fmtFtRange(3.2787, 4.9383, 4.0) !== fmtFtRange(null, null, 4.0),
);

// --------------------------------------------------------------------------- //
// 4b — THE INVARIANT BACKSTOP: a band that misses its own point                 //
// --------------------------------------------------------------------------- //
// What actually shipped. Steamer Lane published face 1.45 with a band of 0.45-0.65 —
// computed from the already-corrected face, so BOTH ends sat below the point. The band was
// well-ordered, so the lo>hi swap never fired, and this rendered "0-1ft" for a 1.45 ft
// spot. It is now dropped in favour of the point, and logged.
{
  const errs: unknown[][] = [];
  const real = console.error;
  console.error = (...a: unknown[]) => { errs.push(a); };
  try {
    eq('a band entirely below its point is dropped', fmtFtRange(0.45, 0.65, 1.45), '1.4ft');
    eq("...and Cowell's the same", fmtFtRange(0.44, 0.65, 1.45), '1.4ft');
    eq('a band entirely above its point is dropped too', fmtFtRange(5.0, 7.0, 1.45), '1.4ft');
    check('each violation is logged, not silently suppressed', errs.length === 3,
      `logged ${errs.length} time(s)`);
    check('the log names the offending numbers',
      String(errs[0]?.[0] ?? '').includes('0.45') && String(errs[0]?.[0] ?? '').includes('1.45'));
  } finally {
    console.error = real;
  }
}
// The corrected values for the same spot render the band the fix produces.
//   raw 4.07218 / p75 3.2558 = 1.2507 -> 1 ;  / p25 2.2337 = 1.8230 -> 2
eq('the FIXED Steamer Lane band renders 1-2ft', fmtFtRange(1.25, 1.82, 1.45), '1-2ft');
// A point exactly on either bound is INSIDE the band and must still render as a band.
eq('a point exactly on the low bound is inside', fmtFtRange(3.0, 5.0, 3.0), '3-5ft');
eq('a point exactly on the high bound is inside', fmtFtRange(3.0, 5.0, 5.0), '3-5ft');
// With no point at all there is nothing to check against, so the band still renders.
eq('no point means no invariant to violate', fmtFtRange(3.2, 4.9, null), '3-5ft');

// --------------------------------------------------------------------------- //
// 5 — defensive ordering                                                       //
// --------------------------------------------------------------------------- //
// lo <= hi holds by construction in the pipeline (divide by p75 and p25), but these come
// straight off database columns, so a swapped pair renders as a band and not as "5-3ft".
eq('swapped bounds still render low-to-high', fmtFtRange(4.9383, 3.2787, 4.0), '3-5ft');

// --------------------------------------------------------------------------- //
// 6 — the small end, where the band reaches zero                               //
// --------------------------------------------------------------------------- //
// 0.5 ft face: lo = 0.41 -> 0, hi = 0.62 -> 1. "0-1ft" is meaningful and is kept.
eq('a band spanning zero is published, not clamped away', fmtFtRange(0.41, 0.62, 0.5), '0-1ft');

// --------------------------------------------------------------------------- //
// 7 — fmtFt itself is unchanged                                                //
// --------------------------------------------------------------------------- //
// The point-estimate formatter is untouched by this change; pinned so the fall-through
// above cannot drift underneath fmtFtRange.
eq('fmtFt still publishes one decimal', fmtFt(4.0), '4.0ft');
eq('fmtFt null is the em dash', fmtFt(null), '—');

if (failures > 0) {
  throw new Error(`formatting: ${failures} FAILURE(S)`);
}
console.log('\nformatting: ALL PASS');
