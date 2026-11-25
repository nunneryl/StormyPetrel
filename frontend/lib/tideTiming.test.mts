/**
 * The tide where only its timing is known (lib/tideTiming.ts): the caption, the high and low
 * times found on the hourly tide position, rising or falling, the station names against the
 * roster, and the page's wiring. Expected values are written out by hand.
 *
 *     node --experimental-strip-types frontend/lib/tideTiming.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  TIMING_ONLY_STATIONS, tideTimingOnly, tideTurns, timingOnlyCaption, timingSeries, timingTrend,
} from './tideTiming.ts';

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..');
const ROOT = join(FRONTEND, '..');
const read = (rel: string) => readFileSync(join(FRONTEND, rel), 'utf8');
let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) console.log(`  PASS  ${name}`);
  else { failures += 1; console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`); }
}
function eq(name: string, got: unknown, want: unknown): void {
  check(name, JSON.stringify(got) === JSON.stringify(want), `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

// The caption.
eq('the caption names the station and its distance, rounded to the km',
  timingOnlyCaption('9416841', 50.5),
  "Tide times from NOAA's Arena Cove station, 51 km away. Close enough for when high and low tide come; "
  + "too far to give this spot's tide height, so heights aren't shown.");
check('...69.6 km reads 70', timingOnlyCaption('9416841', 69.6).startsWith("Tide times from NOAA's Arena Cove station, 70 km away. "));
check('a station with no name here, and no distance, is still described truthfully',
  timingOnlyCaption('9999999', null).startsWith('Tide times from the nearest NOAA tide station. Close enough'));
check('only an explicit true turns timing-only on', tideTimingOnly({ tide_height_suppressed: true })
  && !tideTimingOnly({ tide_height_suppressed: false }) && !tideTimingOnly({}) && !tideTimingOnly(null));

// The high and low times, on a semidiurnal tide sampled on the hour for 30 hours: highs at 03:17Z,
// 15:42Z and 04:07Z next day (12 h 25 min apart), lows halfway between at 09:29Z and 21:54Z.
const T0 = Date.parse('2026-11-25T00:00:00Z');
const HIGH = Date.parse('2026-11-25T03:17:00Z');
const PERIOD = (12 * 60 + 25) * 60 * 1000;
const rows = Array.from({ length: 30 }, (_, h) => {
  const t = T0 + h * 3600 * 1000;
  const norm = Math.round((0.5 + 0.5 * Math.cos((2 * Math.PI * (t - HIGH)) / PERIOD)) * 1000) / 1000;
  return { valid_time: new Date(t).toISOString(), tide_norm: norm };
});
const turns = tideTurns(timingSeries(rows));
const at = (t: number) => new Date(t).toISOString().slice(11, 16);
eq('the turns found: high, low, high, low, high, in order', turns.map((x) => x.kind), ['high', 'low', 'high', 'low', 'high']);
eq('...at the true times to the nearest 10 minutes', turns.map((x) => at(x.t)), ['03:20', '09:30', '15:40', '21:50', '04:10']);
check('...each within 10 minutes of the true turn', turns.every((x, i) => Math.abs(x.t - (HIGH + (i * PERIOD) / 2)) <= 10 * 60 * 1000),
  turns.map((x, i) => (x.t - (HIGH + (i * PERIOD) / 2)) / 60000).join(','));
check('...the highs near the top, the lows near the bottom', turns.every((x) => (x.kind === 'high' ? x.norm > 0.95 : x.norm < 0.05)));
// hours 03-09Z: the 03:17 high falls on the first sample and the 09:29 low on the last; neither is claimed
eq('a turn at either end of the series is not claimed', tideTurns(timingSeries(rows.slice(3, 10))).map((x) => x.kind), []);
eq('...and is once the hour after it is there', tideTurns(timingSeries(rows.slice(3, 11))).map((x) => at(x.t)), ['09:30']);
const gap = rows.filter((_, h) => h !== 9);    // the hour nearest the 09:29 low missing
eq('a turn across a gap in the hours is not claimed', tideTurns(timingSeries(gap)).map((x) => at(x.t)), ['03:20', '15:40', '21:50', '04:10']);
eq('hours with no tide position are skipped, duplicates kept once',
  timingSeries([{ valid_time: '2026-11-25T01:00:00Z', tide_norm: null }, { valid_time: '2026-11-25T00:00:00Z', tide_norm: 0.2 },
    { valid_time: '2026-11-25T00:00:00Z', tide_norm: 0.9 }]).map((p) => p.norm), [0.2]);

// Rising or falling.
eq('rising, falling, slack, unknown', [timingTrend(0.4, 0.5), timingTrend(0.5, 0.4), timingTrend(0.995, 1), timingTrend(null, 0.5)],
  ['rising', 'falling', 'slack', null]);

// Every station a roster spot uses for timing only has its name here, the name the station
// override records (pipeline/data/spot_tide_stations.json), without its state.
const roster = JSON.parse(readFileSync(join(ROOT, 'pipeline/spots_enriched.json'), 'utf8')) as Record<string, unknown>[];
const flagged = roster.filter((s) => s.tide_height_suppressed === true);
eq('the roster flags the four Mendocino spots', flagged.map((s) => s.name).sort(), ['Caspar', 'Jug Handle', 'Mackerricher', 'Ten Mile Beach']);
const overrides = (JSON.parse(readFileSync(join(ROOT, 'pipeline/data/spot_tide_stations.json'), 'utf8')) as {
  stations: Record<string, { station_id: string; station_name: string }> }).stations;
for (const s of flagged) {
  const id = String(s.nearest_tide_station_id);
  const official = Object.values(overrides).find((o) => o.station_id === id)?.station_name ?? '';
  check(`${s.name}: its station ${id} is named, as "${official}" without the state`,
    TIMING_ONLY_STATIONS[id] !== undefined && official.replace(/,\s*[A-Z]{2}$/, '') === TIMING_ONLY_STATIONS[id]);
}

// The page's wiring.
const page = read('app/spot/[slug]/page.tsx');
check('the spot page reads tide_norm with the forecast rows', /tide_level_ft, tide_norm, nwps_cycle/.test(page));
check('...gives the tile and the chart the timing-only mode', page.includes('tideTimingOnly={timingOnly}')
  && page.includes('timingOnlyCaption(spot.nearest_tide_station_id, spot.nearest_tide_station_dist_km)'));
check('...and titles the chart for what it shows', page.includes("timingOnly ? 'Tide times · next 48h' : 'Tide (ft) · next 48h'"));
const chart = read('components/TideChart.tsx');
const timing = chart.slice(chart.indexOf('function TimingOnlyChart'));
check('the timing-only chart prints no feet', !timing.includes('level_ft') && !timing.includes('toFixed') && !/[}\d]ft\b/.test(timing));
check('...labels each high and low with its time', timing.includes("turn.kind === 'high' ? 'High' : 'Low'") && timing.includes('fmtClock('));
const tile = read('components/CurrentConditions.tsx');
check('the tile reads rising or falling off tide_norm where only timing is known',
  tile.includes('timingTrend(current?.tide_norm, forecasts[1]?.tide_norm)') && tile.includes("'height not shown'"));

if (failures > 0) throw new Error(`tideTiming: ${failures} FAILURE(S)`);
console.log('\ntideTiming: ALL PASS');
