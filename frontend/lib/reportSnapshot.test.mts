/**
 * Pins for lib/reportSnapshot.ts: what a report stores about the forecast for its hour.
 *
 *     node --experimental-strip-types frontend/lib/reportSnapshot.test.mts
 *
 * Every expected value is written out: the tile ranges from the ladder in lib/formatting.ts
 * (1, 2, 3, 4, 5, 6, 8, 10, ...; a boundary goes to the higher step), the basis from the rules
 * in lib/heightBasis.ts.
 */
import { SNAPSHOT_COLUMNS, snapshotFromRow, type SnapshotRow } from './reportSnapshot.ts';

let failures = 0;

function eq<T>(name: string, got: T, want: T): void {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failures += 1;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : `  — got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`}`);
}

const ROW: SnapshotRow = {
  face_ft: 3.4, face_ft_raw: 3.4, swell_source: 'nwps_height_ww3_dir',
  face_correction_version: null, stars: 2, nwps_cycle: '2026-10-30T00:00:00+00:00',
};

eq('the route selects exactly the columns the snapshot reads', SNAPSHOT_COLUMNS,
   'face_ft, face_ft_raw, swell_source, face_correction_version, stars, nwps_cycle');
eq('a model height: its numbers, the tile range, the basis and the cycle', snapshotFromRow(ROW),
   { face_ft: 3.4, stars: 2, band: '3-4ft', basis: 'model', nwps_cycle: '2026-10-30T00:00:00+00:00' });
eq('a height on a boundary takes the higher step', snapshotFromRow({ ...ROW, face_ft: 6, face_ft_raw: 6 }).band,
   '6-8ft');
eq('under 1 ft', snapshotFromRow({ ...ROW, face_ft: 0.6, face_ft_raw: 0.6 }).band, 'under 1ft');
eq('a divided height is calibrated', snapshotFromRow({ ...ROW, face_ft: 3.4, face_ft_raw: 4.1 }).basis,
   'calibrated');
eq('a calibrated hour\'s range is the SHOWN height\'s (3.4 ft), not the raw 4.1 ft\'s',
   snapshotFromRow({ ...ROW, face_ft: 3.4, face_ft_raw: 4.1 }).band, '3-4ft');
eq('a surf-height row is a model estimate even though raw differs',
   snapshotFromRow({ ...ROW, face_ft_raw: 4.9, face_correction_version: 'v3+surf:k0.70:d10:g0.78' }).basis,
   'model');
eq('a CDIP MOP hour', snapshotFromRow({ ...ROW, swell_source: 'cdip_mop' }).basis, 'cdip');
eq('no height: no range and no basis, the stars and cycle kept',
   snapshotFromRow({ ...ROW, face_ft: null, face_ft_raw: null }),
   { face_ft: null, stars: 2, band: null, basis: null, nwps_cycle: '2026-10-30T00:00:00+00:00' });
eq('a row with no cycle stores none', snapshotFromRow({ ...ROW, nwps_cycle: null }).nwps_cycle, null);

if (failures > 0) {
  throw new Error(`reportSnapshot: ${failures} FAILURE(S)`);
}
console.log('\nreportSnapshot: ALL PASS');
