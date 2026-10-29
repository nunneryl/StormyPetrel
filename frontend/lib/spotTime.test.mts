/**
 * Times in the spot's own time zone (lib/spotTime.ts), and every place the site shows a time
 * reading it.
 *
 *   1. THE FIVE ZONES. A Pacific, an Eastern, a Central-panhandle, a Hawaii and a Puerto Rico
 *      spot at one instant, and across the November and March daylight-saving changes (Hawaii
 *      and Puerto Rico have none).
 *   2. THE GRID'S DAYS AND ROWS. Rows group by the spot's local date and sample every third
 *      local hour.
 *   3. THE CAM'S SUNRISE. On the spot's clock, and no "may be dark" in a West Coast or Hawaii
 *      afternoon.
 *   4. EVERY DISPLAY READS IT. No page or component formats a time in a fixed zone or the
 *      browser's, and the spot page hands its zone to every component that shows one.
 *
 * NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST: every time below is worked out by hand from
 * the zone's UTC offset (PDT -7, PST -8, EDT -4, EST -5, CDT -5, CST -6, HST -10, AST -4).
 *
 *     node --experimental-strip-types frontend/lib/spotTime.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { walk } from './copyScan.ts';
import { camDarkness } from './cam-utils.ts';
import {
  dayKey, FALLBACK_TIME_ZONE, fmtClock, fmtDay, fmtDayTimeTick, fmtShortTime, fmtStamp,
  fmtTooltipTime, isThreeHourMark, localHour, spotTimeZone, zoneAbbr, zoneAbbrs,
} from './spotTime.ts';

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}
const eq = (name: string, got: unknown, want: unknown) =>
  check(name, JSON.stringify(got) === JSON.stringify(want), `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = dirname(HERE);
const read = (rel: string) => readFileSync(join(FRONTEND, rel), 'utf8');

// The five spots (pipeline/tests/test_time_zone.py holds the roster to these zones).
const STEAMER = 'America/Los_Angeles';       // Steamer Lane
const JAX = 'America/New_York';              // Jacksonville Beach Pier
const PENSACOLA = 'America/Chicago';         // Pensacola Beach, Florida panhandle
const PIPELINE = 'Pacific/Honolulu';         // Banzai Pipeline
const RINCON = 'America/Puerto_Rico';        // Rincon Domes

// --------------------------------------------------------------------------- //
// 1 — the five zones                                                           //
// --------------------------------------------------------------------------- //
// 2026-10-28 21:00Z: 2pm PDT, 5pm EDT, 4pm CDT, 11am HST, 5pm AST.
const T = '2026-10-28T21:00:00Z';
eq('21:00Z at the five spots', [STEAMER, JAX, PENSACOLA, PIPELINE, RINCON].map((z) => fmtShortTime(T, z)),
  ['2pm', '5pm', '4pm', '11am', '5pm']);
eq('their abbreviations', [STEAMER, JAX, PENSACOLA, PIPELINE, RINCON].map((z) => zoneAbbr(T, z)),
  ['PDT', 'EDT', 'CDT', 'HST', 'AST']);
eq('the grid day header and the chart tick', [fmtDay(T, JAX), fmtDayTimeTick(T, JAX)], ['Wed, Oct 28', 'Wed 5pm']);
eq('a tooltip carries the zone', fmtTooltipTime(T, PENSACOLA), 'Wed, Oct 28 4pm CDT');
eq('a stamp: date, clock and zone', fmtStamp('2026-10-28T20:50:00Z', JAX), 'Oct 28, 4:50pm EDT');
eq('noon and midnight', [fmtShortTime('2026-10-28T16:00:00Z', JAX), fmtShortTime('2026-10-29T04:00:00Z', JAX)],
  ['12pm', '12am']);
eq('minutes on the clock', fmtClock('2026-10-29T11:35:00Z', JAX), '7:35am');

// The November change, Sunday 2026-11-01 at 2am local: Eastern 05:00Z is 1am EDT and 06:00Z
// is 1am EST; Central 06:00Z 1am CDT, 07:00Z 1am CST; Pacific 08:00Z 1am PDT, 09:00Z 1am PST.
eq('Eastern, the hour repeats', ['2026-11-01T05:00:00Z', '2026-11-01T06:00:00Z'].map((i) => `${fmtShortTime(i, JAX)} ${zoneAbbr(i, JAX)}`),
  ['1am EDT', '1am EST']);
eq('Central panhandle, the hour repeats', ['2026-11-01T06:00:00Z', '2026-11-01T07:00:00Z'].map((i) => `${fmtShortTime(i, PENSACOLA)} ${zoneAbbr(i, PENSACOLA)}`),
  ['1am CDT', '1am CST']);
eq('Pacific, the hour repeats', ['2026-11-01T08:00:00Z', '2026-11-01T09:00:00Z'].map((i) => `${fmtShortTime(i, STEAMER)} ${zoneAbbr(i, STEAMER)}`),
  ['1am PDT', '1am PST']);
// Hawaii and Puerto Rico do not change: 06:00Z and 07:00Z are 8pm and 9pm HST on Oct 31,
// 2am and 3am AST on Nov 1.
eq('Hawaii, no change', ['2026-11-01T06:00:00Z', '2026-11-01T07:00:00Z'].map((i) => `${fmtShortTime(i, PIPELINE)} ${zoneAbbr(i, PIPELINE)} ${dayKey(i, PIPELINE)}`),
  ['8pm HST 2026-10-31', '9pm HST 2026-10-31']);
eq('Puerto Rico, no change', ['2026-11-01T06:00:00Z', '2026-11-01T07:00:00Z'].map((i) => `${fmtShortTime(i, RINCON)} ${zoneAbbr(i, RINCON)}`),
  ['2am AST', '3am AST']);
// The March change, Sunday 2027-03-14 at 2am: Eastern 06:00Z is 1am EST, 07:00Z is 3am EDT.
eq('Eastern, 2am is skipped in March', ['2027-03-14T06:00:00Z', '2027-03-14T07:00:00Z'].map((i) => `${fmtShortTime(i, JAX)} ${zoneAbbr(i, JAX)}`),
  ['1am EST', '3am EDT']);
eq('a day spanning the change shows both abbreviations',
  zoneAbbrs(['2026-11-01T04:00:00Z', '2026-11-01T05:00:00Z', '2026-11-01T07:00:00Z', '2026-11-01T10:00:00Z'], JAX),
  ['EDT', 'EST']);

eq('the stored zone, else the fallback', [
  spotTimeZone({ time_zone: PENSACOLA }), spotTimeZone({ time_zone: null }), spotTimeZone(null),
  spotTimeZone({ time_zone: 'Mars/Olympus_Mons' }),
], [PENSACOLA, FALLBACK_TIME_ZONE, FALLBACK_TIME_ZONE, FALLBACK_TIME_ZONE]);
eq('the fallback is the zone every spot showed before', FALLBACK_TIME_ZONE, 'America/Los_Angeles');

// --------------------------------------------------------------------------- //
// 2 — the grid's days and rows                                                 //
// --------------------------------------------------------------------------- //
// 2026-10-29 04:30Z: 12:30am Oct 29 EDT and AST, 11:30pm Oct 28 CDT, 9:30pm PDT, 6:30pm HST.
eq('rows group by the local date', [JAX, RINCON, PENSACOLA, STEAMER, PIPELINE].map((z) => dayKey('2026-10-29T04:30:00Z', z)),
  ['2026-10-29', '2026-10-29', '2026-10-28', '2026-10-28', '2026-10-28']);
eq('local hours', [JAX, PENSACOLA, PIPELINE].map((z) => localHour('2026-10-29T04:30:00Z', z)), [0, 23, 18]);
// 20:00Z, 21:00Z, 22:00Z: Eastern 4pm 5pm 6pm (6pm is a mark); Central 3pm 4pm 5pm (3pm);
// Hawaii 10am 11am 12pm (12pm).
const H = ['2026-10-28T20:00:00Z', '2026-10-28T21:00:00Z', '2026-10-28T22:00:00Z'];
eq('every third local hour', [JAX, PENSACOLA, PIPELINE].map((z) => H.map((i) => isThreeHourMark(i, z))),
  [[false, false, true], [true, false, false], [false, false, true]]);

// --------------------------------------------------------------------------- //
// 3 — the cam's sunrise                                                        //
// --------------------------------------------------------------------------- //
// Jacksonville Beach (30.29 N, 81.39 W) at 6am EDT on Oct 29 (10:00Z): before a sunrise of
// 7:36am EDT (US Naval Observatory). The longitude estimate this replaced said about 6:10am.
const jax = camDarkness(30.29, -81.39, JAX, new Date('2026-10-29T10:00:00Z'));
check('Jacksonville at 6am EDT is dark, sunrise 7:33 to 7:39am EDT',
  jax.isDark && /^7:3[3-9]am$/.test(jax.sunriseLabel ?? ''), JSON.stringify(jax));
// Banzai Pipeline (21.66 N, 158.05 W): solar noon 12:16pm HST (720 + 4 x 158.05 minutes UTC,
// less a 16 minute equation of time), half a day of 11h26m: 5pm HST (03:00Z) is before a
// sunset near 5:59pm; 9pm HST (07:00Z) is dark, before a sunrise near 6:33am HST.
const pipeAfternoon = camDarkness(21.66, -158.05, PIPELINE, new Date('2026-10-29T03:00:00Z'));
check('Pipeline at 5pm HST is not dark', !pipeAfternoon.isDark, JSON.stringify(pipeAfternoon));
const pipeNight = camDarkness(21.66, -158.05, PIPELINE, new Date('2026-10-29T07:00:00Z'));
check('Pipeline at 9pm HST is dark, sunrise in the 6am hour HST',
  pipeNight.isDark && /^6:\d\dam$/.test(pipeNight.sunriseLabel ?? ''), JSON.stringify(pipeNight));
// Steamer Lane (36.95 N, 122.03 W): 5pm PDT (00:00Z) is before a sunset of about 6:15pm PDT.
check('Steamer Lane at 5pm PDT is not dark',
  !camDarkness(36.95, -122.03, STEAMER, new Date('2026-10-29T00:00:00Z')).isDark);
// Rincon (18.37 N, 67.27 W) at 5am AST (09:00Z): dark. Solar noon 12:13pm AST, half a day of
// 5h45m: sunrise near 6:28am AST.
const rincon = camDarkness(18.37, -67.27, RINCON, new Date('2026-10-29T09:00:00Z'));
check('Rincon at 5am AST is dark, sunrise in the 6am hour AST',
  rincon.isDark && /^6:\d\dam$/.test(rincon.sunriseLabel ?? ''), JSON.stringify(rincon));
check('Rincon at noon AST is not dark', !camDarkness(18.37, -67.27, RINCON, new Date('2026-10-29T16:00:00Z')).isDark);

// --------------------------------------------------------------------------- //
// 4 — every display reads it                                                   //
// --------------------------------------------------------------------------- //
const files = ['app', 'components', 'lib'].flatMap((d) => walk(join(FRONTEND, d)))
  .map((abs) => relative(FRONTEND, abs))
  .filter((rel) => /\.(ts|tsx)$/.test(rel) && !rel.endsWith('.test.mts'));
// A fixed zone outside lib/spotTime.ts (the fallback) and lib/reports.ts (the region table).
const fixedZone = files.filter((rel) => !['lib/spotTime.ts', 'lib/reports.ts'].includes(rel)
  && /America\/Los_Angeles|America\/New_York|Pacific\/Honolulu/.test(read(rel)));
check('no other file names a zone', fixedZone.length === 0, fixedZone.join(', '));
// toLocaleString / toLocaleTimeString with no timeZone formats in the server's or the browser's.
const unzoned = files.filter((rel) => {
  const text = read(rel);
  return [...text.matchAll(/toLocale(?:Time|Date)?String\(([^)]*\{[^}]*\}|[^)]*)\)/g)]
    .some((m) => !/timeZone/.test(m[1]));
});
check('no toLocale*String without a timeZone', unzoned.length === 0, unzoned.join(', '));
// Each component formats with the zone it is handed, not a zone of its own.
const USES: [string, string[]][] = [
  ['components/ForecastGrid.tsx', ['isThreeHourMark(r.valid_time, timeZone)', 'dayKey(r.valid_time, timeZone)',
    'fmtDay(rows[0].valid_time, timeZone)', 'fmtShortTime(r.valid_time, timeZone)']],
  ['components/TideChart.tsx', ['fmtDayTimeTick(new Date(v as number).toISOString(), timeZone)',
    'fmtTooltipTime(new Date(v as number).toISOString(), timeZone)']],
  ['components/WindChart.tsx', ['fmtDayTimeTick(new Date(v as number).toISOString(), timeZone)',
    'fmtTooltipTime(new Date(v as number).toISOString(), timeZone)']],
  ['components/SwellChart.tsx', ['fmtDayTimeTick(new Date(ms).toISOString(), timeZone)',
    'fmtTooltipTime(new Date(v as number).toISOString(), timeZone)']],
  ['components/SurfReport.tsx', ['{fmtShortTime(iso, timeZone)} {zoneAbbr(iso, timeZone)}']],
  ['components/CamEmbed.tsx', ['camDarkness(lat, lng, timeZone)']],
];
for (const [rel, uses] of USES) {
  const text = read(rel);
  check(`${rel} formats with the spot's zone`, uses.every((u) => text.includes(u)),
    uses.filter((u) => !text.includes(u)).join(' | '));
}
const page = read('app/spot/[slug]/page.tsx');
check('the spot page hands its zone to every component that shows a time',
  ['<CamSection', '<SurfReportProvider', '<SwellChart', '<WindChart', '<TideChart', '<ForecastGrid']
    .every((tag) => new RegExp(`${tag}[^>]*timeZone=\\{timeZone\\}`).test(page))
  && page.includes('fmtStamp(buoy.observed_at, timeZone)')
  && page.includes('const timeZone = spotTimeZone(spot);'));
check('the report footer is in the region\'s zone',
  read('app/reports/[date]/[region]/page.tsx').includes('fmtStamp(report.generated_at, REGION_TIME_ZONE[report.region] ?? FALLBACK_TIME_ZONE)'));

if (failures > 0) {
  throw new Error(`spotTime: ${failures} FAILURE(S)`);
}
console.log('\nspotTime: ALL PASS');
