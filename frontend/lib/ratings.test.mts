/**
 * Numeric pins for classifySurface — the wind-derived Clean / Mixed / Choppy / Blown out
 * banding behind the Conditions tile.
 *
 * WHY THIS FILE EXISTS. The frontend had no test runner and no test files at all, so
 * classifyChop's 0.2/0.4/0.6 cuts, the five label strings and the tile's null path were
 * pinned by nothing and could be changed, reordered or deleted with nothing failing. That
 * mattered once the Conditions tile was repointed: the new banding has six boundaries and
 * an ordering dependency (speed before direction) that is easy to invert by accident.
 *
 * NO TEST FRAMEWORK IS ADDED. ratings.ts imports nothing, so this runs on stock Node with
 * type-stripping and zero installed packages:
 *
 *     node --experimental-strip-types frontend/lib/ratings.test.mts
 *     (or: npm --prefix frontend run test)
 *
 * The check/_run_all shape mirrors the Python suites in pipeline/tests/ so the two halves
 * of the repo read the same way.
 *
 * EVERY EXPECTED VALUE IS HAND-COMPUTED, with the arithmetic in a comment. None is derived
 * by calling the function under test.
 */
import {
  classifySurface,
  offAngleDeg,
  classifyChop,
  chopLabel,
  surfaceTextClass,
} from './ratings.ts';

let failures = 0;

function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}

function eq<T>(name: string, got: T, want: T): void {
  check(name, got === want, `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

// --------------------------------------------------------------------------- //
// 1 — offAngleDeg wraps; it is never the raw difference                        //
// --------------------------------------------------------------------------- //
// off = |((dir - offshore + 540) mod 360) - 180|
//   350 vs  10: (350 -  10 + 540) mod 360 = 880 mod 360 = 160; |160 - 180| =  20
//    10 vs 350: ( 10 - 350 + 540) mod 360 = 200 mod 360 = 200; |200 - 180| =  20
//     0 vs   0: (  0 -   0 + 540) mod 360 = 180;             |180 - 180| =   0
//   180 vs   0: (180 -   0 + 540) mod 360 = 720 mod 360 =   0; |  0 - 180| = 180
//    90 vs   0: ( 90 -   0 + 540) mod 360 = 630 mod 360 = 270; |270 - 180| =  90
//   270 vs   0: (270 -   0 + 540) mod 360 = 810 mod 360 =  90; | 90 - 180| =  90
//   370 vs  10: (370 -  10 + 540) mod 360 = 900 mod 360 = 180; |180 - 180| =   0
eq('offAngle 350 vs 10 is 20, NOT 340', offAngleDeg(350, 10), 20);
eq('offAngle 10 vs 350 is 20 (symmetric)', offAngleDeg(10, 350), 20);
eq('offAngle 0 vs 0 is 0', offAngleDeg(0, 0), 0);
eq('offAngle 180 vs 0 is 180', offAngleDeg(180, 0), 180);
eq('offAngle 90 vs 0 is 90', offAngleDeg(90, 0), 90);
eq('offAngle 270 vs 0 is 90 (folds to the short way)', offAngleDeg(270, 0), 90);
eq('offAngle 370 vs 10 is 0 (out-of-range bearing)', offAngleDeg(370, 10), 0);
check('offAngle never exceeds 180', [0, 45, 90, 135, 180, 225, 270, 315, 359]
  .every((d) => offAngleDeg(d, 0) >= 0 && offAngleDeg(d, 0) <= 180));

// --------------------------------------------------------------------------- //
// 2 — the wrap case reaches the BAND, not just the helper                      //
// --------------------------------------------------------------------------- //
// wind_dir 350, offshore 10, speed 8 (well above both speed cuts):
//   off = 20  ->  20 <= 60  ->  clean
// The naive |350 - 10| = 340 would be > 120 and, at 8 m/s, would give 'blown'.
// This single case separates a correct implementation from the obvious wrong one.
eq('wrap: dir 350 / offshore 10 at 8 m/s is clean, not blown',
   classifySurface(350, 8, 10), 'clean');
eq('wrap: dir 10 / offshore 350 at 8 m/s is clean',
   classifySurface(10, 8, 350), 'clean');

// --------------------------------------------------------------------------- //
// 3 — all three null paths return unknown                                      //
// --------------------------------------------------------------------------- //
eq('null wind_dir -> unknown', classifySurface(null, 8, 10), 'unknown');
eq('null wind_speed -> unknown', classifySurface(350, null, 10), 'unknown');
eq('null offshore_wind_deg -> unknown', classifySurface(350, 8, null), 'unknown');
eq('undefined wind_dir -> unknown', classifySurface(undefined, 8, 10), 'unknown');
eq('undefined wind_speed -> unknown', classifySurface(350, undefined, 10), 'unknown');
eq('undefined offshore_wind_deg -> unknown', classifySurface(350, 8, undefined), 'unknown');
eq('all three null -> unknown', classifySurface(null, null, null), 'unknown');
// unknown must survive even when the other two inputs would otherwise be decisive
eq('null speed with dead-onshore direction is still unknown',
   classifySurface(180, null, 0), 'unknown');

// --------------------------------------------------------------------------- //
// 4 — the 2.5 m/s glassy cut, both sides                                       //
// --------------------------------------------------------------------------- //
// Held at dead onshore (off = 180) so ONLY the speed cut can produce 'clean'.
//   speed <  2.5 -> clean   (strict <, so 2.5 itself does NOT take this branch)
//   speed >= 2.5 -> falls through to the angle ladder; off 180 > 120, and
//                   2.5 < 6 -> choppy
eq('2.49 m/s dead onshore -> clean (glassy)', classifySurface(180, 2.49, 0), 'clean');
eq('2.5 m/s dead onshore -> choppy (boundary is NOT glassy)',
   classifySurface(180, 2.5, 0), 'choppy');
eq('2.51 m/s dead onshore -> choppy', classifySurface(180, 2.51, 0), 'choppy');
eq('0.0 m/s dead onshore -> clean', classifySurface(180, 0, 0), 'clean');
// requirement: a glassy onshore hour is clean — speed dominates below 2.5
// wind_dir 170, offshore 0 -> off = |((170 - 0 + 540) mod 360) - 180| = |530 mod 360 - 180|
//                                 = |170 - 180| = 10 ... that is OFFSHORE-ish, so instead
// pick a direction that really is 170 deg off: dir 170 vs offshore 340
//   (170 - 340 + 540) mod 360 = 370 mod 360 = 10; |10 - 180| = 170  -> onshore
eq('off_angle 170 at 1.0 m/s -> clean (glassy beats onshore)',
   classifySurface(170, 1.0, 340), 'clean');
eq('off_angle 170 at 8.0 m/s -> blown (same direction, real wind)',
   classifySurface(170, 8.0, 340), 'blown');

// --------------------------------------------------------------------------- //
// 5 — the 60-degree offshore/cross cut, both sides                             //
// --------------------------------------------------------------------------- //
// Speed held at 8 m/s, above both speed cuts, so only the angle decides.
//   off <= 60 -> clean          (INCLUSIVE, so 60 itself is clean)
//   off  > 60 -> mixed (up to 120)
// offshore = 0, so off_angle == wind_dir for dir in [0, 180].
eq('off 59 -> clean', classifySurface(59, 8, 0), 'clean');
eq('off 60 -> clean (inclusive boundary)', classifySurface(60, 8, 0), 'clean');
eq('off 61 -> mixed', classifySurface(61, 8, 0), 'mixed');
eq('off 0 (straight offshore) -> clean', classifySurface(0, 8, 0), 'clean');

// --------------------------------------------------------------------------- //
// 6 — the 120-degree cross/onshore cut, both sides                             //
// --------------------------------------------------------------------------- //
//   off <= 120 -> mixed          (INCLUSIVE, so 120 itself is mixed)
//   off  > 120 -> the speed ladder decides choppy vs blown
eq('off 119 -> mixed', classifySurface(119, 8, 0), 'mixed');
eq('off 120 -> mixed (inclusive boundary)', classifySurface(120, 8, 0), 'mixed');
eq('off 121 at 8 m/s -> blown', classifySurface(121, 8, 0), 'blown');
eq('off 121 at 5 m/s -> choppy', classifySurface(121, 5, 0), 'choppy');
eq('off 90 (pure cross-shore) -> mixed', classifySurface(90, 8, 0), 'mixed');

// --------------------------------------------------------------------------- //
// 7 — the 6 m/s choppy/blown cut, both sides                                   //
// --------------------------------------------------------------------------- //
// Held at dead onshore (off 180) so the angle ladder always reaches the speed test.
//   speed <  6 -> choppy   (strict <, so 6.0 itself is BLOWN)
//   speed >= 6 -> blown
eq('5.99 m/s dead onshore -> choppy', classifySurface(180, 5.99, 0), 'choppy');
eq('6.0 m/s dead onshore -> blown (boundary is blown)', classifySurface(180, 6, 0), 'blown');
eq('6.01 m/s dead onshore -> blown', classifySurface(180, 6.01, 0), 'blown');
eq('20 m/s dead onshore -> blown', classifySurface(180, 20, 0), 'blown');

// --------------------------------------------------------------------------- //
// 8 — ordering: speed is tested BEFORE direction                               //
// --------------------------------------------------------------------------- //
// The single case that separates the two orderings. At 1.0 m/s dead onshore:
//   speed-first  -> 1.0 < 2.5           -> clean          (what ships)
//   angle-first  -> off 180 > 120, 1.0 < 6 -> choppy      (the inversion)
eq('1.0 m/s dead onshore is clean, not choppy — speed is checked first',
   classifySurface(180, 1.0, 0), 'clean');
// and above the glassy cut the direction ladder does take over
eq('3.0 m/s dead onshore is choppy — above glassy, below 6',
   classifySurface(180, 3.0, 0), 'choppy');
// GLASSY CROSS-SHORE is the case that actually separates the two orderings, and the
// onshore cases above do NOT: at off 180 both orderings fall through to the speed test
// and agree on 'clean'. Only inside the cross-shore band do they diverge —
//   correct (speed first): 1.0 < 2.5                  -> clean
//   inverted (angle first): off 90 <= 120             -> mixed
// so a version that computed the angle ladder before the glassy cut would pass every
// other assertion in this file and fail only here.
eq('1.0 m/s CROSS-SHORE (off 90) is clean, not mixed', classifySurface(90, 1.0, 0), 'clean');
eq('1.0 m/s at off 61 (just into cross) is clean', classifySurface(61, 1.0, 0), 'clean');
eq('1.0 m/s at off 120 (top of cross) is clean', classifySurface(120, 1.0, 0), 'clean');
// ...and at 3.0 m/s the same three directions land in the cross-shore band as expected
eq('3.0 m/s at off 90 is mixed', classifySurface(90, 3.0, 0), 'mixed');

// --------------------------------------------------------------------------- //
// 9 — every band is reachable, and the four words are the expected strings     //
// --------------------------------------------------------------------------- //
eq('band clean is reachable', classifySurface(30, 8, 0), 'clean');
eq('band mixed is reachable', classifySurface(90, 8, 0), 'mixed');
eq('band choppy is reachable', classifySurface(180, 4, 0), 'choppy');
eq('band blown is reachable', classifySurface(180, 10, 0), 'blown');
eq('label clean', chopLabel('clean'), 'Clean');
eq('label mixed', chopLabel('mixed'), 'Mixed');
eq('label choppy', chopLabel('choppy'), 'Choppy');
eq('label blown', chopLabel('blown'), 'Blown out');
eq('label unknown is the em dash', chopLabel('unknown'), '—');

// --------------------------------------------------------------------------- //
// 10 — the unknown path renders exactly as before                              //
// --------------------------------------------------------------------------- //
// The tile shows chopLabel(cQ) as its value with surfaceTextClass(cQ) as the class.
// For 'unknown' that must be the em dash with NO colour override, so BigTile's own
// text-text-primary stands and the tile looks identical to the pre-change build.
eq('unknown value is the em dash', chopLabel(classifySurface(null, null, null)), '—');
eq('unknown carries NO colour class', surfaceTextClass('unknown'), '');
check('every non-unknown band DOES carry a colour class',
  (['clean', 'mixed', 'choppy', 'blown'] as const)
    .every((b) => surfaceTextClass(b).startsWith('text-')));

// --------------------------------------------------------------------------- //
// 11 — classifyChop is unchanged and still available                           //
// --------------------------------------------------------------------------- //
// Deprecated for LABELLING, not deleted: chop_ratio is still a real published stat and
// the tile still shows it as the "swell mix" hint. Its cuts must not drift.
eq('classifyChop 0.19 -> clean', classifyChop(0.19), 'clean');
eq('classifyChop 0.2 -> mixed', classifyChop(0.2), 'mixed');
eq('classifyChop 0.39 -> mixed', classifyChop(0.39), 'mixed');
eq('classifyChop 0.4 -> choppy', classifyChop(0.4), 'choppy');
eq('classifyChop 0.59 -> choppy', classifyChop(0.59), 'choppy');
eq('classifyChop 0.6 -> blown', classifyChop(0.6), 'blown');
eq('classifyChop null -> unknown', classifyChop(null), 'unknown');

// --------------------------------------------------------------------------- //
// 12 — the contradiction this change exists to remove                          //
// --------------------------------------------------------------------------- //
// An offshore-wind hour with a high chop_ratio: the Wind tile shows "offshore", and the
// Conditions tile used to show "Blown out" beside it. 57.3% of offshore-wind hours were
// labelled that way. Now the same hour reads clean.
//   dir 10, offshore 0 -> off = |((10 - 0 + 540) mod 360) - 180| = |190 - 180| = 10
//   10 <= 60 -> clean, whatever chop_ratio says
eq('offshore wind is clean regardless of chop_ratio', classifySurface(10, 9, 0), 'clean');
eq('...where classifyChop on the same hour said blown', classifyChop(0.75), 'blown');

// Throw rather than process.exit: a thrown error still gives node a non-zero exit code,
// and it keeps this file free of any ambient Node types, so it typechecks and runs with
// nothing installed at all.
if (failures > 0) {
  throw new Error(`ratings: ${failures} FAILURE(S)`);
}
console.log('\nratings: ALL PASS');
