/**
 * The spot page credits CDIP wherever it shows a height built on CDIP MOP: the MOP tier, and
 * every spot with a calibrated (or CDIP) hour. Every expected value is written out by hand.
 *
 *     node --experimental-strip-types frontend/lib/cdipCredit.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { CDIP_HOMEPAGE, showsCdipCredit } from './cdipCredit.ts';

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) console.log(`  PASS  ${name}`);
  else { failures += 1; console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`); }
}

// Row shapes as the forecasts table holds them.
const model = { face_ft: 3.0, face_ft_raw: 3.0, swell_source: 'nwps', face_correction_version: null };
// 3-mile's factor is above 1: the published height is the raw one divided by it, so they differ.
const calibrated = { face_ft: 2.4, face_ft_raw: 3.0, swell_source: 'nwps', face_correction_version: 'v3' };
// A factor below 1 (point-arena's 0.812): the published height is LARGER than the raw one.
const calibratedUp = { face_ft: 3.7, face_ft_raw: 3.0, swell_source: 'nwps', face_correction_version: 'v3' };
const cdip = { face_ft: 3.0, face_ft_raw: 3.0, swell_source: 'cdip_mop', face_correction_version: null };
// Rewritten by apply_surf_height: a model estimate, although face_ft and face_ft_raw differ.
const surf = { face_ft: 2.1, face_ft_raw: 3.0, swell_source: 'nwps', face_correction_version: 'v3+surf:k0.70:d10:g0.78' };
const noHeight = { face_ft: null, face_ft_raw: null, swell_source: 'nwps', face_correction_version: null };

const nwpsSpot = { swell_window_source: 'nwps' };

check('a calibrated spot is credited', showsCdipCredit(nwpsSpot, [model, calibrated, model]));
check('a calibrated spot with a factor below 1 is credited', showsCdipCredit(nwpsSpot, [calibratedUp]));
check('one calibrated hour among model hours is enough', showsCdipCredit(nwpsSpot, [model, model, calibrated]));
check('a CDIP-height hour is credited', showsCdipCredit(nwpsSpot, [cdip]));
check('the MOP tier is credited with model rows only',
  showsCdipCredit({ swell_window_source: 'cdip_mop' }, [model, model]));
check('the MOP tier is credited with no rows at all', showsCdipCredit({ swell_window_source: 'cdip_mop' }, []));

check('a model-only spot is not credited', !showsCdipCredit(nwpsSpot, [model, model]));
check('surf-height rows are model estimates, not credited', !showsCdipCredit(nwpsSpot, [surf, surf]));
check('rows with no height are not credited', !showsCdipCredit(nwpsSpot, [noHeight]));
check('no rows and no MOP tier is not credited', !showsCdipCredit({ swell_window_source: null }, []));
check('a missing swell_window_source is not the MOP tier', !showsCdipCredit({}, [model]));

check('the link is the CDIP homepage the data-use page names', CDIP_HOMEPAGE === 'https://cdip.ucsd.edu');

// The spot page renders the credit from this rule, not from the MOP tier alone.
const page = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'app', 'spot', '[slug]', 'page.tsx'), 'utf8');
check('the spot page gates the credit on showsCdipCredit', /\{showsCdipCredit\(spot, forecasts\) && \(/.test(page));
check('the spot page no longer gates the credit on the MOP tier alone',
  !/\{spot\.swell_window_source === 'cdip_mop' && \(/.test(page));

if (failures > 0) {
  console.log(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log('\nall cdip credit checks passed');
