/**
 * Pins for lib/buoyDisplay.ts: what the spot page's Nearest buoy box says.
 *
 *     node --experimental-strip-types frontend/lib/buoyDisplay.test.mts
 *
 * Every expected string and number is worked out by hand. The times: "now" is 2026-11-02 15:00Z;
 * 2026-10-13 18:56Z is 8:56am in Honolulu (HST, UTC-10, no daylight time); 24 hours before now is
 * 2026-11-01 15:00Z. One degree on the pipeline's sphere (radius 6371 km) is
 * 6371 * pi / 180 = 111.1949 km.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  BUOY_DISPLAY_LIMIT_KM,
  BUOY_STALE_HOURS,
  EARTH_RADIUS_KM,
  buoyBox,
  buoyTitle,
  fmtKm,
  isRecentWave,
  kmBetween,
  recentSince,
  type BackupBuoy,
  type BuoyBoxInput,
  type BuoyReading,
} from './buoyDisplay.ts';

let failures = 0;
function eq<T>(name: string, got: T, want: T): void {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failures += 1;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : `  — got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`}`);
}
function near(name: string, got: number, want: number, tol: number): void {
  const ok = Math.abs(got - want) <= tol;
  if (!ok) failures += 1;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : `  — got ${got}, want ${want} ± ${tol}`}`);
}

const NOW = Date.parse('2026-11-02T15:00:00Z');
const HI = 'Pacific/Honolulu';
const wave = (buoy_id: string, observed_at: string, hs: number | null = 1.2): BuoyReading => ({
  buoy_id, observed_at, hs, tp: 8, dp: 150, water_temp: 15.5,
});
const HOUR_OLD = '2026-11-02T14:00:00Z';
const DAY_OLD = '2026-11-01T15:00:00Z';            // exactly 24 hours
const DAY_AND_A_MINUTE = '2026-11-01T14:59:00Z';
const OCT_13 = '2026-10-13T18:56:00Z';

// The main buoy, 44025 at 20 km, with no backups unless a case gives them.
const base: BuoyBoxInput = {
  buoyId: '44025', distKm: 20, latest: null, lastWave: null, backups: [], nowMs: NOW, timeZone: HI,
};
const box = (over: Partial<BuoyBoxInput>) => buoyBox({ ...base, ...over });

console.log('\n1. The limits and helpers');
eq('the display limit is 150 km', BUOY_DISPLAY_LIMIT_KM, 150);
eq('a reading is recent for 24 hours', BUOY_STALE_HOURS, 24);
eq('the sphere is the pipeline\'s', EARTH_RADIUS_KM, 6371.0);
eq('87.46 km reads "87 km"', fmtKm(87.46), '87 km');
eq('86.5 km rounds up to "87 km"', fmtKm(86.5), '87 km');
eq('the title', buoyTitle('44025', 87.46), 'NDBC 44025 · 87 km away');
eq('no distance on the row: the id alone', buoyTitle('44025', null), 'NDBC 44025');
near('one degree of latitude', kmBetween(21, -157, 22, -157), 111.1949, 0.0001);
near('one degree of longitude on the equator', kmBetween(0, 0, 0, 1), 111.1949, 0.0001);
near('the same point', kmBetween(20.9, -156.4, 20.9, -156.4), 0, 0);
eq('the window opens 24 hours back', recentSince(NOW), '2026-11-01T15:00:00.000Z');
eq('recent: a wave height an hour old', isRecentWave(wave('a', HOUR_OLD), NOW), true);
eq('recent: exactly 24 hours old', isRecentWave(wave('a', DAY_OLD), NOW), true);
eq('not recent: 24 hours and a minute', isRecentWave(wave('a', DAY_AND_A_MINUTE), NOW), false);
eq('not recent: no wave height', isRecentWave(wave('a', HOUR_OLD, null), NOW), false);
eq('not recent: no reading', isRecentWave(null, NOW), false);

console.log('\n2. Case 1: the main buoy\'s latest reading, with a wave height');
const fresh = wave('44025', HOUR_OLD);
eq('an hour old', box({ distKm: 87.46, latest: fresh, lastWave: fresh }),
  { kind: 'reading', id: '44025', title: 'NDBC 44025 · 87 km away', label: 'Reading', reading: fresh });
const atDay = wave('44025', DAY_OLD);
eq('exactly 24 hours old is still case 1', box({ latest: atDay, lastWave: atDay }),
  { kind: 'reading', id: '44025', title: 'NDBC 44025 · 20 km away', label: 'Reading', reading: atDay });
eq('24 hours and a minute is not', box({ latest: wave('44025', DAY_AND_A_MINUTE), lastWave: wave('44025', DAY_AND_A_MINUTE) }).kind, 'stale');

console.log('\n3. Case 2: the latest reading has no wave height; the last one that did, within 24 h');
const noWave = wave('44025', HOUR_OLD, null);
const earlier = wave('44025', '2026-11-02T10:00:00Z');
eq('Kalaloch\'s shape: latest without Hs, one 5 hours old with it',
  box({ latest: noWave, lastWave: earlier }),
  { kind: 'reading', id: '44025', title: 'NDBC 44025 · 20 km away', label: 'Last wave reading', reading: earlier });
eq('the last wave reading exactly 24 hours old', box({ latest: noWave, lastWave: atDay }).kind, 'reading');
eq('... and labelled so', (box({ latest: noWave, lastWave: atDay }) as { label: string }).label, 'Last wave reading');
eq('24 hours and a minute: not case 2', box({ latest: noWave, lastWave: wave('44025', DAY_AND_A_MINUTE) }).kind, 'stale');
eq('no reading at all, no wave reading: not case 2', box({ latest: null, lastWave: null }).kind, 'stale');

console.log('\n4. Case 3: a backup buoy within 150 km with a wave height within 24 h');
const quietMain = { latest: noWave, lastWave: wave('51205', OCT_13) };
const ok140 = wave('51101', HOUR_OLD);
const backup = (id: string, km: number | null, w: BuoyReading | null): BackupBuoy => ({ id, km, wave: w });
eq('the backup, its own id and distance',
  box({ ...quietMain, buoyId: '51205', distKm: 12.81, backups: [backup('51101', 140.2, ok140)] }),
  { kind: 'reading', id: '51101', title: 'Backup buoy NDBC 51101 · 140 km away', label: 'Reading', reading: ok140 });
eq('the first that qualifies, in the roster\'s order',
  box({ ...quietMain, backups: [
    backup('a', 30, null),                                    // no recent wave reading
    backup('b', 40, wave('b', HOUR_OLD, null)),               // a reading, no wave height
    backup('c', 55, wave('c', DAY_AND_A_MINUTE)),             // too old
    backup('d', 150.01, wave('d', HOUR_OLD)),                 // past 150 km
    backup('e', null, wave('e', HOUR_OLD)),                   // position unknown
    backup('f', 150, wave('f', DAY_OLD)),                     // exactly 150 km and exactly 24 h
    backup('g', 60, wave('g', HOUR_OLD)),
  ] }),
  { kind: 'reading', id: 'f', title: 'Backup buoy NDBC f · 150 km away', label: 'Reading', reading: wave('f', DAY_OLD) });
eq('the main buoy\'s own recent wave reading comes before any backup',
  box({ latest: noWave, lastWave: earlier, backups: [backup('g', 60, wave('g', HOUR_OLD))] }).id, '44025');

console.log('\n5. Case 4: nothing recent with a wave height');
eq('51205 at Peahi Jaws (16.15 km), last wave Oct 13, no backups',
  box({ buoyId: '51205', distKm: 16.15, latest: wave('51205', OCT_13), lastWave: wave('51205', OCT_13) }),
  { kind: 'stale', id: '51205', title: 'NDBC 51205 · 16 km away', text: 'No recent wave reading (last: Oct 13, 8:56am HST)' });
eq('a recent reading without Hs still dates the last wave reading',
  box({ buoyId: '51205', distKm: 16.15, latest: noWave, lastWave: wave('51205', OCT_13),
    backups: [backup('d', 150.01, wave('d', HOUR_OLD))] }),
  { kind: 'stale', id: '51205', title: 'NDBC 51205 · 16 km away', text: 'No recent wave reading (last: Oct 13, 8:56am HST)' });
eq('never a wave reading: no date',
  box({ latest: noWave, lastWave: null }),
  { kind: 'stale', id: '44025', title: 'NDBC 44025 · 20 km away', text: 'No recent wave reading' });

console.log('\n6. Past 150 km and no buoy (#295)');
eq('Siesta Key\'s buoy, 380.99 km', box({ buoyId: '42039', distKm: 380.99, latest: fresh, lastWave: fresh }),
  { kind: 'far', id: '42039', text: 'No buoy within 150 km (nearest: NDBC 42039, 381 km)' });
eq('exactly 150 km is shown', box({ distKm: 150, latest: fresh, lastWave: fresh }).kind, 'reading');
eq('150.01 km is not', box({ distKm: 150.01, latest: fresh, lastWave: fresh }).kind, 'far');
eq('far wins over a backup', box({ distKm: 380.99, backups: [backup('g', 60, wave('g', HOUR_OLD))] }).kind, 'far');
eq('no buoy on the spot', box({ buoyId: null, distKm: null }), { kind: 'none' });

console.log('\n7. The page uses it');
const page = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'app', 'spot', '[slug]', 'page.tsx'), 'utf8');
eq('the box is built by buoyBox', (page.match(/buoyBox\(/g) ?? []).length, 2);
eq('fallbacks are read only when the main buoy has nothing recent',
  page.includes("if (buoyShown.kind === 'stale') {\n    buoyShown = buoyBox({ ...buoyInput, backups: await loadBackups(spot, nowMs) });"), true);
eq('the reading row carries the box\'s label', page.includes('<Row k={buoyShown.label} v={fmtStamp(buoyShown.reading.observed_at, timeZone)} />'), true);
eq('no value is read past it from a raw buoy row', /\b(buoy|lastWave)\.(hs|tp|dp|water_temp|observed_at|buoy_id)\b/.test(page), false);
eq('the page imports only the uncached client', page.includes("import { supabaseUncached } from '@/lib/supabase';")
  && !/\bsupabase\s*\.\s*from\(/.test(page), true);
eq('the backup reads: the buoys table and each fallback\'s wave reading since the window opened',
  page.includes("supabaseUncached.from('buoys').select('id, lat, lng').in('id', ids)")
  && page.includes('Promise.all(ids.map((id) => loadLatestWave(id, since)))'), true);
eq('a wave reading is one with a wave height', page.includes(".not('hs', 'is', null)"), true);

if (failures > 0) {
  throw new Error(`buoyDisplay: ${failures} FAILURE(S)`);
}
console.log('\nbuoyDisplay: ALL PASS');
