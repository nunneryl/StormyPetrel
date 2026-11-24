/**
 * What /map and /region/* send per spot (lib/listSpot.ts): exactly these fields, worked out on the
 * server, and nothing else from the spot or forecast row. Expected values are written out by hand.
 *
 *     node --experimental-strip-types frontend/lib/listSpot.test.mts
 */
import { toListSpot } from './listSpot.ts';
import type { SpotWithLatest } from './types.ts';

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) console.log(`  PASS  ${name}`);
  else { failures += 1; console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`); }
}
function eq(name: string, got: unknown, want: unknown): void {
  check(name, JSON.stringify(got) === JSON.stringify(want), `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

// A spot row as the database holds it, with the big columns the pages never show.
const spot = {
  id: 7, slug: 'banzai-pipeline', name: 'Banzai Pipeline', state: 'Hawaii', region: 'Oahu',
  lat: 21.66448812, lng: -158.05371234,
  orientation_deg: 315, offshore_wind_deg: 135, optimal_swell_dir: 320,
  swell_window_arcs: [{ min: 270, max: 30 }],
  break_type: 'reef', break_type_source: 'researched',
  break_type_evidence: 'A long quote from a cited page about the reef.', bottom_evidence: 'More quoted text.',
  description: 'Banzai Pipeline is a reef break in Hawaii.', time_zone: 'Pacific/Honolulu',
  tide_trend: 'rising',
  latest: {
    spot_id: 7, valid_time: '2026-11-24T20:00:00+00:00', source: 'nwps',
    hs: 2.5, swell_hs: 2.4, tp: 14.2, dp: 318.4, swell_tp: 14.26, swell_dp: 318.6,
    swell_1_hs: 2.3, swell_1_tp: 14.2, swell_1_dp: 318, swell_2_hs: 0.4, swell_2_tp: 8, swell_2_dp: 40,
    swell_3_hs: null, swell_3_tp: null, swell_3_dp: null, wind_wave_hs: 0.2, wind_wave_tp: 4, wind_wave_dp: 60,
    swell_source: 'nwps', wind_speed: 4.123456, wind_dir: 130.6, face_ft: 7.891234, face_ft_raw: 7.891234,
    face_correction_version: null, dir_gain: 0.98, wind_mult: 1, tide_mult: 0.95, chop_ratio: 0.08, chop_mult: 1,
    period_quality: 1, effective_size_ft: 7.7, stars: 4.5, tide_level_ft: 1.2345, nwps_cycle: '2026-11-24T12:00:00+00:00',
  },
} as unknown as SpotWithLatest;

const item = toListSpot(spot);
eq('exactly the fields the pages show', Object.keys(item).sort(), [
  'basis', 'breakLabel', 'dp', 'faceFt', 'id', 'lat', 'lng', 'name', 'slug', 'stars', 'state', 'tideFt',
  'tideTrend', 'tp', 'windDir', 'windQuality', 'windSpeed',
]);
eq('the values, rounded to what is printed', item, {
  id: 7, slug: 'banzai-pipeline', name: 'Banzai Pipeline', state: 'Hawaii',
  lat: 21.6645, lng: -158.0537,                 // 4 decimals, about 11 m
  breakLabel: 'reef',                           // researched: no "(unverified)"
  stars: 4.5, faceFt: 7.89, basis: 'model',     // face_ft equals face_ft_raw: a model estimate
  tp: 14.3, dp: 319,                            // the swell's train: swell_tp 14.26, swell_dp 318.6
  windSpeed: 4.12, windDir: 131,
  windQuality: 'offshore',                      // from 130.6 with offshore at 135
  tideFt: 1.23, tideTrend: 'rising',
});
const json = JSON.stringify(item);
for (const leaked of ['evidence', 'description', 'swell_window_arcs', 'face_ft_raw', 'swell_1_hs', 'nwps_cycle', 'time_zone']) {
  check(`nothing from the rows' ${leaked}`, !json.includes(leaked));
}
check('the item is a small fraction of the rows it replaces',
  json.length * 4 < JSON.stringify(spot).length, `${json.length} vs ${JSON.stringify(spot).length}`);

eq('an unverified break type says so', toListSpot({ ...spot, break_type_source: 'model_recall' } as SpotWithLatest).breakLabel,
  'reef (unverified)');
const none = toListSpot({ ...spot, latest: null, tide_trend: null } as SpotWithLatest);
eq('no forecast row: no rating, height, train, wind or tide', [none.stars, none.faceFt, none.basis, none.tp, none.dp,
  none.windSpeed, none.windDir, none.windQuality, none.tideFt, none.tideTrend],
  [null, null, null, null, null, null, null, 'unknown', null, null]);

if (failures > 0) throw new Error(`listSpot: ${failures} FAILURE(S)`);
console.log('\nlistSpot: ALL PASS');
