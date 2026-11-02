/**
 * Pins for lib/buoyDisplay.ts: what the spot page's Nearest buoy box says.
 *
 *     node --experimental-strip-types frontend/lib/buoyDisplay.test.mts
 *
 * Every expected string is written out by hand. The times: 2026-10-13 18:56Z is 8:56am in
 * Honolulu (HST, UTC-10, no daylight time); "now" is 2026-11-02 15:00Z.
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  BUOY_DISPLAY_LIMIT_KM,
  BUOY_STALE_HOURS,
  buoyBox,
  buoyTitle,
  fmtKm,
  type BuoyReading,
} from './buoyDisplay.ts';

let failures = 0;
function eq<T>(name: string, got: T, want: T): void {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failures += 1;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : `  — got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`}`);
}

const NOW = Date.parse('2026-11-02T15:00:00Z');
const HI = 'Pacific/Honolulu';
const reading = (observed_at: string): BuoyReading => ({
  buoy_id: '44025', observed_at, hs: 1.2, tp: 8, dp: 150, water_temp: 15.5,
});
const FRESH = reading('2026-11-02T14:00:00Z');                 // an hour old

console.log('\n1. The limits');
eq('the display limit is 150 km', BUOY_DISPLAY_LIMIT_KM, 150);
eq('a reading goes stale after 24 hours', BUOY_STALE_HOURS, 24);

console.log('\n2. The distance, always');
eq('87.46 km reads "87 km"', fmtKm(87.46), '87 km');
eq('86.5 km rounds up to "87 km"', fmtKm(86.5), '87 km');
eq('the title', buoyTitle('44025', 87.46), 'NDBC 44025 · 87 km away');
eq('no distance on the row: the id alone', buoyTitle('44025', null), 'NDBC 44025');
eq('a current reading', buoyBox('44025', 87.46, FRESH, NOW, HI),
  { kind: 'reading', id: '44025', title: 'NDBC 44025 · 87 km away', reading: FRESH });

console.log('\n3. Past 150 km: no reading');
eq('Siesta Key\'s buoy, 380.99 km', buoyBox('42039', 380.99, FRESH, NOW, HI),
  { kind: 'far', id: '42039', text: 'No buoy within 150 km (nearest: NDBC 42039, 381 km)' });
eq('exactly 150 km is shown', buoyBox('44025', 150, FRESH, NOW, HI).kind, 'reading');
eq('150.01 km is not', buoyBox('44025', 150.01, FRESH, NOW, HI).kind, 'far');
eq('far wins over stale', buoyBox('42039', 380.99, null, NOW, HI).kind, 'far');

console.log('\n4. More than 24 hours old: no values');
eq('51205\'s Oct 13 reading, at Peahi Jaws (16.15 km)', buoyBox('51205', 16.15, reading('2026-10-13T18:56:00Z'), NOW, HI),
  { kind: 'stale', id: '51205', title: 'NDBC 51205 · 16 km away', text: 'No recent reading (last: Oct 13, 8:56am HST)' });
eq('exactly 24 hours old is still shown', buoyBox('44025', 20, reading('2026-11-01T15:00:00Z'), NOW, HI).kind, 'reading');
eq('24 hours and a minute is not', buoyBox('44025', 20, reading('2026-11-01T14:59:00Z'), NOW, HI).kind, 'stale');
eq('no reading at all', buoyBox('44025', 20, null, NOW, HI),
  { kind: 'stale', id: '44025', title: 'NDBC 44025 · 20 km away', text: 'No recent reading' });

console.log('\n5. No buoy');
eq('no buoy on the spot', buoyBox(null, null, null, NOW, HI), { kind: 'none' });

console.log('\n6. The page uses it');
const page = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '..', 'app', 'spot', '[slug]', 'page.tsx'), 'utf8');
eq('the box is built by buoyBox', page.includes('buoyBox(spot.nearest_buoy_id, spot.nearest_buoy_dist_km, buoy, nowMs, timeZone)'), true);
eq('no value is read past it from the raw buoy row', /\bbuoy\.(hs|tp|dp|water_temp|observed_at|buoy_id)\b/.test(page), false);

if (failures > 0) {
  throw new Error(`buoyDisplay: ${failures} FAILURE(S)`);
}
console.log('\nbuoyDisplay: ALL PASS');
