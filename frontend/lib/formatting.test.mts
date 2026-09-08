/**
 * Numeric pins for fmtFtRange — the published SWELL HEIGHT band.
 *
 * THESE PINS WERE WRONG AND ARE INVERTED HERE. The previous version of this file asserted
 * the collapse as correct behaviour — "a band narrower than a foot collapses to the single
 * value", pinned three ways and called out in its own header as the thing being protected.
 * It was protecting a bug. Round-to-nearest made 5,566 of 17,030 banded future hours (32.7%)
 * render a bare point, indistinguishable from the 466 spots that were never measured, and
 * every one of those tests passed the whole time. A test that pins the wrong behaviour is
 * worse than no test: it makes the bug look deliberate. Noted here rather than quietly
 * rewritten.
 *
 * WHAT IS PINNED NOW:
 *
 *  1. EXPAND OUTWARD. Floor the low end, ceiling the high end. The ends can only move APART,
 *     so the published width is at least one foot and can never be narrower than measured.
 *
 *  2. NO COLLAPSE, FOR ANY INPUT. Pinned by the worked examples, by a swept range of a
 *     thousand synthetic bands, and by the degenerate lo === hi case.
 *
 *  3. SUB-FOOT BANDS READ "under N ft", never "0-N ft". A floored zero says "possibly flat"
 *     and the measurement does not support that. Same call StarRating already made when it
 *     refused to draw zero stars and printed FLAT instead.
 *
 *  4. NO BAND MEANS THE POINT, NOT A FABRICATED BAND. 466 of 648 spots have no measured
 *     spread. They fall through to fmtFt's one-decimal point estimate and never to a default
 *     width. After this change that fall-through is the ONLY way to get a bare point, so the
 *     absence of a range now means exactly one thing.
 *
 * EVERY EXPECTED VALUE IS HAND-COMPUTED, with the arithmetic in a comment. None is derived
 * by calling the function under test — in particular every bound is written out as a literal
 * rather than as Math.floor/Math.ceil of the input.
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
// 1 — THE WORKED EXAMPLES, taken from real production rows                     //
// --------------------------------------------------------------------------- //
// Each bound is written out by hand. The right-hand comment gives what
// round-to-nearest WOULD have produced, so the three collapse cases are visible as the
// cases they are rather than as four lines that look alike.
eq('2.21-2.52 publishes 2-3ft', fmtFtRange(2.21, 2.52, 2.4), '2-3ft');   // nearest: 2-3
eq('2.44-2.79 publishes 2-3ft', fmtFtRange(2.44, 2.79, 2.6), '2-3ft');   // nearest: 2-3
eq('2.52-2.87 publishes 2-3ft', fmtFtRange(2.52, 2.87, 2.7), '2-3ft');   // nearest: 3-3 COLLAPSE
eq('2.60-2.66 publishes 2-3ft', fmtFtRange(2.60, 2.66, 2.63), '2-3ft');  // nearest: 3-3 COLLAPSE
eq('1.65-2.40 publishes 1-3ft', fmtFtRange(1.65, 2.40, 2.0), '1-3ft');   // nearest: 2-2 COLLAPSE

// The Steamer Lane row from the report: a real 2.52-2.87 band that rendered a bare "2ft".
check('the Steamer Lane collapse is gone', fmtFtRange(2.52, 2.87, 2.7).includes('-'));

// --------------------------------------------------------------------------- //
// 2 — floor and ceiling, not nearest                                           //
// --------------------------------------------------------------------------- //
// A .1 low end floors DOWN (round-to-nearest would have kept 3); a .1 high end ceilings UP.
eq('a .9 low end floors down to 2', fmtFtRange(2.9, 6.1, 4.0), '2-7ft');
eq('a .4 low end floors down to 2', fmtFtRange(2.4, 6.4, 4.0), '2-7ft');
// A .5 end is the case round-half-up used to move the other way: 2.5 became 3.
eq('a .5 low end floors DOWN, not up', fmtFtRange(2.5, 7.4, 5.0), '2-8ft');
eq('a .5 high end ceilings UP', fmtFtRange(1.6, 5.5, 3.0), '1-6ft');
// An exact whole number does not move: floor(3) === 3 and ceil(5) === 5.
eq('exact whole bounds are left alone', fmtFtRange(3.0, 5.0, 4.0), '3-5ft');
// The roster-median band on a 4 ft face is unchanged by the switch, which is worth pinning:
// the change costs nothing at the size most spots sit at.
//   lo = 4.0 / 1.22 = 3.2787 -> floor 3 ;  hi = 4.0 / 0.81 = 4.9383 -> ceil 5
eq('the median band on a 4ft face is still 3-5ft', fmtFtRange(3.2787, 4.9383, 4.0), '3-5ft');

// --------------------------------------------------------------------------- //
// 3 — A BAND CAN NEVER COLLAPSE. "3-3ft" and a bare "3ft" are both impossible.  //
// --------------------------------------------------------------------------- //
// The case that used to collapse: a 1.2 ft face with the same +/-20% band.
//   lo = 1.2 / 1.22 = 0.9836 -> floor 0 ;  hi = 1.2 / 0.81 = 1.4815 -> ceil 2
eq('a band narrower than a foot still publishes a range', fmtFtRange(0.9836, 1.4815, 1.2), 'under 2ft');
// Both ends the SAME whole number — p25 === p75, no measured spread at all. No committed
// factor is in that state; this is the guard. One foot is the minimum publishable width.
eq('exactly equal whole bounds widen rather than collapse', fmtFtRange(3.0, 3.0, 3.0), '3-4ft');
eq('exactly equal fractional bounds span their foot', fmtFtRange(3.5, 3.5, 3.5), '3-4ft');
// The pair that landed on 3 from opposite sides under round-to-nearest.
eq('bounds either side of a whole foot span it', fmtFtRange(2.6, 3.4, 3.0), '2-4ft');

// THE SWEEP. Every band from 0.00-0.05 up to ~12 ft, at the widest ratio the exclusion rule
// admits (p75/p25 <= 1.7) and at a very narrow one, must render a range. This is the
// "for any input" pin: it does not compute an expected string, it asserts a PROPERTY.
{
  let collapsed = 0;
  let checked = 0;
  for (let i = 1; i <= 1000; i += 1) {
    const low = i * 0.012;                    // 0.012 .. 12.0
    for (const ratio of [1.001, 1.05, 1.2, 1.46, 1.7]) {
      // The point must sit INSIDE the band or the invariant backstop drops it and publishes
      // the point alone — which is a correct bare render, not a collapse. The geometric
      // middle is inside [low, low*ratio] for every ratio >= 1. (Getting this wrong is how
      // the first version of this sweep reported 2000 false collapses.)
      const out = fmtFtRange(low, low * ratio, low * Math.sqrt(ratio));
      checked += 1;
      // A range is either "a-bft" or "under Nft". Neither is a bare number.
      if (!out.includes('-') && !out.startsWith('under ')) collapsed += 1;
    }
  }
  check(`no band collapses across ${checked} synthetic bands (${collapsed} did)`, collapsed === 0);
}

// LOW <= HIGH, ALWAYS — read back off the rendered string rather than assumed.
{
  let violations = 0;
  let matched = 0;
  for (let i = 1; i <= 1000; i += 1) {
    const low = i * 0.012;
    for (const ratio of [1.001, 1.2, 1.7]) {
      const out = fmtFtRange(low, low * ratio, low * Math.sqrt(ratio));
      const m = out.match(/^(\d+)-(\d+)ft$/);
      if (m) {
        matched += 1;
        if (Number(m[1]) >= Number(m[2])) violations += 1;
      }
    }
  }
  check(`the low end is always strictly below the high end (${violations} violations)`,
        violations === 0);
  // NOT VACUOUS. If the regex stopped matching — a format change, or every band falling to
  // the "under N" form — the check above would pass while testing nothing.
  check(`...over a non-empty sample (${matched} two-sided bands)`, matched > 2500);
}

// --------------------------------------------------------------------------- //
// 3b — THE SUB-FOOT END: "under N ft", never "0-N ft"                          //
// --------------------------------------------------------------------------- //
// A floored low end of 0 would say "possibly flat", and the measurement does not support
// that for a low end of 0.95 ft. StarRating already refused to draw a zero for the same
// reason and printed FLAT instead.
eq('0.30-0.55 reads "under 1ft"', fmtFtRange(0.30, 0.55, 0.4), 'under 1ft');
eq('0.41-0.62 reads "under 1ft"', fmtFtRange(0.41, 0.62, 0.5), 'under 1ft');
// The near-foot case, which is the one a "0" would misrepresent most: 0.99 ft is
// ankle-to-knee, not flat.
eq('0.99-1.48 reads "under 2ft", not "0-2ft"', fmtFtRange(0.99, 1.48, 1.2), 'under 2ft');
check('no rendered band ever contains a zero low end',
      !fmtFtRange(0.30, 0.55, 0.4).startsWith('0') &&
      !fmtFtRange(0.99, 1.48, 1.2).startsWith('0'));
// A band at exactly zero — nothing to publish, but still not a bare point or "under 0ft".
eq('a zero-width band at zero is still a range', fmtFtRange(0.0, 0.0, 0.0), 'under 1ft');
// A NEGATIVE low end. Not reachable from the pipeline — lo = raw/p75 with raw >= 0 and
// p75 > 0 — but this function reads database columns directly and is written defensively
// against a stale row. floor(-0.5) is -1, and "-1-2ft" is not a string anyone should see.
// The test is `a <= 0`, not `a === 0`, and that difference is what this pins.
eq('a negative low end reads "under N", not "-1-Nft"', fmtFtRange(-0.5, 1.2, 0.5), 'under 2ft');
eq('a wholly negative band still renders a range', fmtFtRange(-2.0, -1.0, -1.5), 'under 1ft');
check('no rendered band ever starts with a minus sign',
      !fmtFtRange(-0.5, 1.2, 0.5).startsWith('-') &&
      !fmtFtRange(-2.0, -1.0, -1.5).startsWith('-'));
// Given p75/p25 <= 1.7, a floored-to-zero low end forces hi < 1.7, so "under" can only ever
// be 1 or 2 feet. Swept rather than argued.
{
  let outOfRange = 0;
  for (let i = 1; i <= 1000; i += 1) {
    const low = (i / 1000) * 0.999;           // every low end below one foot
    const out = fmtFtRange(low, low * 1.7, low * 1.2);
    if (out !== 'under 1ft' && out !== 'under 2ft') outOfRange += 1;
  }
  check(`"under" is only ever 1 or 2 feet (${outOfRange} outside)`, outOfRange === 0);
}

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
// AND WITH THE POINT ON THE PRESENT BOUND. The two lines above pass even against a version
// that copies the present bound into the absent one — because the resulting zero-width band
// misses its point and the invariant backstop rescues it. That is the right answer reached
// by the wrong route, and it left the half-present case genuinely untested (found by
// mutation). Here the point sits ON the present bound, so a copied bound would render a
// band and only the real guard produces the point.
eq('a missing high end with the point on the low bound is still the point',
   fmtFtRange(3.2, null, 3.2), '3.2ft');
eq('a missing low end with the point on the high bound is still the point',
   fmtFtRange(null, 4.9, 4.9), '4.9ft');
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
// THE STATE COLLAPSE THIS FIX REMOVES. A measured spot whose band was tighter than a foot
// used to render the same bare point as an unmeasured one. Now a bare point means exactly
// one thing: nobody measured the spread here.
check('a measured narrow band no longer looks like an unmeasured spot',
      fmtFtRange(2.52, 2.87, 2.7) !== fmtFtRange(null, null, 2.7));

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
//   raw 4.07218 / p75 3.2558 = 1.2507 -> floor 1 ;  / p25 2.2337 = 1.8230 -> ceil 2
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
// AND THE SWAP MUST HAPPEN BEFORE THE EXPANSION. Swapping after would floor 5.2 to 5 and
// ceiling 3.1 to 4, then order them to "4-5ft" — a band INSIDE the one measured, which is
// the understatement this whole change removes. Ordering first gives 3-6.
eq('a swapped pair is expanded outward, not narrowed', fmtFtRange(5.2, 3.1, 4.0), '3-6ft');

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
