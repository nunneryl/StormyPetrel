/**
 * THE SPOT PAGE'S TWO FRESHNESS STAMPS, READ OFF A RENDERED PAGE.
 *
 * /spot/[slug] carries two hidden tags for measuring how old a served copy is:
 *
 *   <meta name="sp-rendered-at"   content="…">   when this copy of the page was rendered
 *   <meta name="sp-headline-hour" content="…">   valid_time of the row its headline height is from
 *
 * They exist to be read from what a request gets back, so this reads them from exactly that: a
 * production `next build` against a stand-in Supabase, then `next start`, then HTTP. A check on
 * the component alone could not see whether the tags reach the served HTML, where they land, or
 * whether a cached copy keeps the time it was built.
 *
 * WHAT IS PINNED
 *   1. Both tags are in the served HTML, once each, inside <head>.
 *   2. Both parse as timestamps, in the one ISO 8601 UTC form toISOString writes.
 *   3. A prerendered copy carries its BUILD time: sp-rendered-at falls inside the build, and a
 *      later request gets the same value back.
 *   4. A regenerated copy carries ITS render time: after an on-demand revalidation the stamp
 *      moves to a moment after that revalidation was asked for.
 *   5. sp-headline-hour is the hour containing sp-rendered-at, and it is the row the Surf height
 *      tile shows: every fixture hour has its own height, so the tile's text names the row.
 *   6. With no row for the current hour the page says so, and sp-headline-hour is absent.
 *   7. The grep pattern given for reading the tags with curl finds exactly the two tags.
 *   8. Tide preference and break type follow their sources (lib/provenance) on the served page:
 *      a tide preference from an untrusted source drops the header chip and reads '—' in both
 *      cards; a break type from an untrusted source carries "(unverified)" in the header chip,
 *      both cards and the region list. Three spots are shaped like the roster's Banzai Pipeline
 *      (reef, researched; low tide, unattributed), Pensacola Beach (beach, model_recall; mid
 *      tide, unattributed) and Sunset Point (no break type, no tide), and a fourth carries a
 *      researched tide, so the chip is seen to render when it should.
 *  10. The surf-report card (components/SurfReport) is in the served HTML, not behind a link:
 *      the question "Surfed here today? How big were most waves?", the eleven body-scale chips
 *      each with its feet hint, and no "Was this right?" link. Neither a California spot nor
 *      Banzai Pipeline (Hawaii) carries a Hawaiian-scale note.
 *  11. The Nearest buoy box (lib/buoyDisplay): Pensacola Beach's buoy, 41.0 km away with a reading
 *      an hour old, reads "NDBC 42012 · 41 km away" with the reading; Sunset Point's, 380.99 km
 *      away, reads "No buoy within 150 km (nearest: NDBC 46221, 381 km)" with the station link
 *      and no reading; Jacksonville Beach Pier's has no distance on its row, reads "NDBC 41112",
 *      and its Oct 29 reading is "No recent wave reading"; a spot with no buoy says "No nearby buoy.".
 *      A buoy whose latest reading has no wave height shows its last one that did, three hours
 *      old, as "Last wave reading" (0.9 m reads "3.0 ft"). A spot whose buoy has had no wave
 *      height since Oct 13 shows its first fallback within 150 km with a recent one: of 51001
 *      (55.6 km, 30 hours old), 51002 (166.8 km, recent) and 51003 (111.2 km, recent), 51003:
 *      "Backup buoy NDBC 51003 · 111 km away", 2.0 m reading "6.6 ft", and 51003's station link.
 *  12. A blank NWPS hour (the sample cell had no wave height, period or direction, so the rater
 *      filled it from a neighbouring hour, nwps_nearshore.series_hour): every row of one spot has
 *      hs, tp and dp empty and stars, face_ft and swell_hs/swell_tp/swell_dp set, period 11 s and
 *      direction 280 deg where every other fixture row has 12 s and 270 deg. The page shows it as
 *      any other hour: the Surf height tile and sp-headline-hour as in 5, and the grid's period
 *      "11s" and direction "280°" (W), from swell_tp/swell_dp. Nothing on the page says the hour
 *      was blank.
 *  13. "Wave model run is N h old" (lib/modelRunAge): every fixture row carries an NWPS cycle six
 *      hours before the hour the test starts in, and no page says anything about it; one spot's
 *      rows carry a cycle 30 hours before instead, and its page says "Wave model run is N h old"
 *      with N the whole hours from that cycle to the page's own sp-rendered-at (30, or 31 if the
 *      build crossed an hour), worked out here, and the run's time in Pacific time.
 *   9. A spot row's time_zone reaches the page's times (lib/spotTime): Pensacola Beach, stored as
 *      America/Chicago, shows Central abbreviations only; Jacksonville Beach Pier, whose row has
 *      no time_zone in the first build (the shape of a row read before migration 026), shows the
 *      Los Angeles fallback, labelled, with its buoy's last reading, 13:50Z on Oct 29, as
 *      "No recent wave reading (last: Oct 29, 6:50am PDT)" (more than a day old: item 11).
 *      Then the stand-in gains its time_zone and the site is built AGAIN WITHOUT CLEARING
 *      .next/cache: the second build must ask the stand-in for that spot's row rather than take
 *      the copy the first build cached, and show "(last: Oct 29, 9:50am EDT)" and Eastern
 *      abbreviations only. That second build is the 2026-10-29 preview: migration 026 applied, a redeploy
 *      without build cache, and still Los Angeles times, from a spot row the Data Cache held.
 *
 * NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST. The expected hour is this file's own
 * arithmetic (the render time floored to the hour), the expected height is looked up in this
 * file's fixture, and the time bounds are this process's clock read around the build and around
 * each request.
 *
 *     cd frontend && npm run test:render      (builds the site into .next; a minute or two)
 */
import { spawn } from 'node:child_process';
import type { ChildProcess } from 'node:child_process';
import http from 'node:http';
import type { AddressInfo } from 'node:net';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const FRONTEND = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const NEXT_BIN = path.join(FRONTEND, 'node_modules', 'next', 'dist', 'bin', 'next');
const HOUR = 3_600_000;
const SECRET = 'stamp-test-secret';

// The pattern the PR gives for reading the tags off the live site with `curl … | grep -oE`,
// verbatim. Held here so the command handed over is one this test has run against a real page.
const CURL_GREP = 'name="sp-(rendered-at|headline-hour)" content="[^"]*"';

// What toISOString writes, and nothing looser: a timestamp in the one form both tags promise.
const ISO_UTC = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/;

let failures = 0;

function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}

function eq(name: string, got: unknown, want: unknown): void {
  check(name, got === want, `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

const sleep = (ms: number) => new Promise((resolve) => setTimeout(resolve, ms));

// --------------------------------------------------------------------------------------------
// Fixture: two spots, and a stand-in for the Supabase REST API that serves them.
// --------------------------------------------------------------------------------------------

// The hour this run starts in. Every fixture hour is an offset from it, so the rows are always
// around "now" whenever the test runs.
const T0 = Math.floor(Date.now() / HOUR) * HOUR;

const SPOT_FIELDS = {
  lat: 36.95, lng: -122.02, state: 'California', region: 'Santa Cruz',
  orientation_deg: 180, offshore_wind_deg: 0, optimal_swell_dir: 270, swell_window_arcs: null,
  break_type: 'reef', break_type_source: 'researched', tide_preference: null,
  tide_preference_source: null, crowd_factor: null, hazards: null,
  nearest_buoy_id: null, nearest_buoy_dist_km: null, nearest_tide_station_id: null,
  description: null, swell_window_source: null,
};
// A row for every hour from 12 back to 48 ahead, so whatever hour the page renders in has one.
const WITH_ROW = { ...SPOT_FIELDS, id: 990001, slug: 'stamp-test-point', name: 'Stamp Test Point' };
// Rows only from 3 hours ahead, so no row covers the hour the page renders in.
const NO_ROW = { ...SPOT_FIELDS, id: 990002, slug: 'stamp-test-gap', name: 'Stamp Test Gap' };
// 8: the sources. Fields from the committed roster; coordinates rounded.
const PIPELINE = {
  ...SPOT_FIELDS, id: 990003, slug: 'banzai-pipeline', name: 'Banzai Pipeline',
  lat: 21.664, lng: -158.054, state: 'Hawaii', region: 'Oahu',
  break_type: 'reef', break_type_source: 'researched',
  tide_preference: 'low', tide_preference_source: 'unattributed',
};
const PENSACOLA = {
  ...SPOT_FIELDS, id: 990004, slug: 'pensacola-beach', name: 'Pensacola Beach',
  lat: 30.329, lng: -87.140, state: 'Florida', region: 'Panhandle',
  break_type: 'beach', break_type_source: 'model_recall',
  tide_preference: 'mid', tide_preference_source: 'unattributed',
  nearest_buoy_id: '42012', nearest_buoy_dist_km: 41.0,
};
const SUNSET_POINT = {
  ...SPOT_FIELDS, id: 990005, slug: 'sunset-point', name: 'Sunset Point',
  lat: 34.037, lng: -118.554, state: 'California', region: 'Malibu',
  break_type: null, break_type_source: null, tide_preference: null, tide_preference_source: null,
  nearest_buoy_id: '46221', nearest_buoy_dist_km: 380.99,
};
const VERIFIED_TIDE = {
  ...SPOT_FIELDS, id: 990006, slug: 'stamp-test-verified-tide', name: 'Stamp Test Verified Tide',
  break_type: 'point', break_type_source: 'reviewed',
  tide_preference: 'low_mid', tide_preference_source: 'researched',
};
// 9: time zones. Pensacola carries its zone from the start; Jacksonville's is added between the
// two builds (`delete`d until then, as a row read before the column existed has no such key).
Object.assign(PENSACOLA, { time_zone: 'America/Chicago' });
const JACKSONVILLE: typeof SPOT_FIELDS & Record<string, unknown> = {
  ...SPOT_FIELDS, id: 990007, slug: 'jacksonville-beach-pier', name: 'Jacksonville Beach Pier',
  lat: 30.294, lng: -81.388, state: 'Florida', region: 'Northeast Florida',
  break_type: null, break_type_source: null, nearest_buoy_id: '41112',
};
// A fixed reading, so its local time is worked by hand: 2026-10-29 13:50Z is 9:50am EDT (UTC-4,
// before the 1 Nov change) and 6:50am PDT (UTC-7).
const BUOY = {
  buoy_id: '41112', observed_at: '2026-10-29T13:50:00+00:00', hs: 1.1, tp: 9, dp: 95,
  wind_speed: 4, wind_dir: 250, water_temp: 22.5,
};
// 11: a reading an hour before this run's hour, so it is never more than a day old.
const FRESH_BUOY = {
  buoy_id: '42012', observed_at: new Date(T0 - HOUR).toISOString(), hs: 0.8, tp: 6, dp: 160,
  wind_speed: 5, wind_dir: 180, water_temp: 24.0,
};
// 11: case 2. 46041's latest reading, an hour back, has no wave height; the one two hours
// before it does (0.9 m, 2.9528 ft, "3.0 ft").
const LAST_WAVE = {
  ...SPOT_FIELDS, id: 990008, slug: 'stamp-test-last-wave', name: 'Stamp Test Last Wave',
  lat: 47.59, lng: -124.368, state: 'Washington', region: 'Olympic Peninsula',
  time_zone: 'America/Los_Angeles', nearest_buoy_id: '46041', nearest_buoy_dist_km: 38.51,
};
// 11: case 3. 51205's last wave height is Oct 13; its fallbacks lie due north of the spot, one
// degree of latitude being 111.1949 km: 51001 0.5 deg (55.6 km) with only a 30-hour-old reading,
// 51002 1.5 deg (166.8 km), 51003 1.0 deg (111.2 km, "111 km") with a 2.0 m reading
// (6.5617 ft, "6.6 ft") two hours back.
const BACKUP = {
  ...SPOT_FIELDS, id: 990009, slug: 'stamp-test-backup', name: 'Stamp Test Backup',
  lat: 20.0, lng: -156.0, state: 'Hawaii', region: 'Maui', time_zone: 'Pacific/Honolulu',
  nearest_buoy_id: '51205', nearest_buoy_dist_km: 12.81, fallback_buoy_ids: ['51001', '51002', '51003'],
};
const obs = (buoy_id: string, at: number | string, hs: number | null) => ({
  buoy_id, observed_at: typeof at === 'string' ? at : new Date(at).toISOString(), hs, tp: 10, dp: 300,
  wind_speed: 3, wind_dir: 60, water_temp: 25.0,
});
// Every buoy's readings. The stand-in filters and orders them as PostgREST would.
const OBS: Record<string, Record<string, unknown>[]> = {
  [BUOY.buoy_id]: [BUOY],
  [FRESH_BUOY.buoy_id]: [FRESH_BUOY],
  '46041': [obs('46041', T0 - HOUR, null), obs('46041', T0 - 3 * HOUR, 0.9)],
  '51205': [obs('51205', '2026-10-13T18:56:00+00:00', 1.5)],
  '51001': [obs('51001', T0 - 30 * HOUR, 1.0)],
  '51002': [obs('51002', T0 - HOUR, 1.4)],
  '51003': [obs('51003', T0 - 2 * HOUR, 2.0)],
};
const BUOY_POSITIONS = [
  { id: '51001', lat: 20.5, lng: -156.0 },
  { id: '51002', lat: 21.5, lng: -156.0 },
  { id: '51003', lat: 21.0, lng: -156.0 },
];
// 12: a spot whose every hour is a blank NWPS hour, filled by the rater.
const BLANK_HOUR = {
  ...SPOT_FIELDS, id: 990010, slug: 'stamp-test-blank-hour', name: 'Stamp Test Blank Hour',
  time_zone: 'America/Los_Angeles',
};
// 13: a spot whose rows come from an NWPS cycle 30 hours before the test's hour.
const OLD_RUN = {
  ...SPOT_FIELDS, id: 990011, slug: 'stamp-test-old-run', name: 'Stamp Test Old Run',
  time_zone: 'America/Los_Angeles',
};
const OLD_CYCLE_MS = T0 - 30 * HOUR;
const SOURCE_SPOTS = [PIPELINE, PENSACOLA, SUNSET_POINT, VERIFIED_TIDE, JACKSONVILLE, LAST_WAVE, BACKUP,
  BLANK_HOUR, OLD_RUN];
const SPOTS = [WITH_ROW, NO_ROW, ...SOURCE_SPOTS];

// The Surf height tile shows the reading range, the standard step the height falls in
// (lib/formatting.ts fmtHeightRange), so the heights cycle through five 1 ft steps: 1.5, 2.5,
// 3.5, 4.5, 5.5 ft from twelve hours back, reading "1-2ft" ... "5-6ft". Neighbouring hours
// always read differently, so the tile still says which row it shows, to within five hours.
function heightAt(offsetHours: number): number {
  return 1.5 + ((offsetHours + 12) % 5);
}
function tileText(offsetHours: number): string {
  const n = 1 + ((offsetHours + 12) % 5);
  return `${n}-${n + 1}ft`;
}

function row(spotId: number, offsetHours: number): Record<string, unknown> {
  const face = heightAt(offsetHours);
  return {
    spot_id: spotId,
    // timestamptz as PostgREST returns it from the real table.
    valid_time: new Date(T0 + offsetHours * HOUR).toISOString().replace('.000Z', '+00:00'),
    source: 'nwps',
    hs: 1.2, swell_hs: 1.2, tp: 12, dp: 270, swell_tp: 12, swell_dp: 270,
    swell_1_hs: null, swell_1_tp: null, swell_1_dp: null,
    swell_2_hs: null, swell_2_tp: null, swell_2_dp: null,
    swell_3_hs: null, swell_3_tp: null, swell_3_dp: null,
    wind_wave_hs: null, wind_wave_tp: null, wind_wave_dp: null,
    swell_source: 'nwps', wind_speed: 4, wind_dir: 0,
    face_ft: face, face_ft_raw: face,
    dir_gain: 1, wind_mult: 1, tide_mult: 1, chop_ratio: 0.1, chop_mult: 1,
    period_quality: 1, effective_size_ft: face, stars: 3, tide_level_ft: 2.5,
    // 13: an NWPS cycle six hours before the test's hour, too young for the note.
    nwps_cycle: new Date(T0 - 6 * HOUR).toISOString().replace('.000Z', '+00:00'),
  };
}

function hours(from: number, to: number): number[] {
  const out: number[] = [];
  for (let h = from; h <= to; h += 1) out.push(h);
  return out;
}

const BLANK = { hs: null, tp: null, dp: null, swell_hs: 1.1, swell_tp: 11, swell_dp: 280,
  swell_source: 'nwps_height_ww3_dir' };
const ROWS = new Map<number, Record<string, unknown>[]>([
  [WITH_ROW.id, hours(-12, 48).map((h) => row(WITH_ROW.id, h))],
  [NO_ROW.id, hours(3, 48).map((h) => row(NO_ROW.id, h))],
  ...SOURCE_SPOTS.map((sp) => [sp.id, hours(-12, 48).map((h) =>
    (sp.id === BLANK_HOUR.id ? { ...row(sp.id, h), ...BLANK }
      : sp.id === OLD_RUN.id
        ? { ...row(sp.id, h), nwps_cycle: new Date(OLD_CYCLE_MS).toISOString().replace('.000Z', '+00:00') }
        : row(sp.id, h)))] as
    [number, Record<string, unknown>[]]),
]);

// Every request the stand-in answers, as "table?query", so a check can see what a build asked.
const asked: string[] = [];

// Enough of PostgREST for this site: the spots, their rows, the one buoy reading, and an empty
// list for anything else (every other page then renders its empty state, which is all the build
// needs).
function answer(table: string, q: URLSearchParams): unknown[] {
  if (table === 'buoy_observations') {
    let rows = [...(OBS[(q.get('buoy_id') ?? '').replace(/^eq\./, '')] ?? [])];
    if (q.get('hs') === 'not.is.null') rows = rows.filter((r) => r.hs !== null);
    const since = q.get('observed_at');
    if (since?.startsWith('gte.')) {
      rows = rows.filter((r) => Date.parse(String(r.observed_at)) >= Date.parse(since.slice(4)));
    }
    rows.sort((a, b) => Date.parse(String(b.observed_at)) - Date.parse(String(a.observed_at)));
    return rows.slice(0, Number(q.get('limit') ?? rows.length));
  }
  if (table === 'buoys') {
    const ids = (/^in\.\((.*)\)$/.exec(q.get('id') ?? '')?.[1] ?? '').split(',').map((v) => v.replace(/^"|"$/g, ''));
    return BUOY_POSITIONS.filter((b) => ids.includes(b.id));
  }
  if (table === 'spots') {
    const slug = q.get('slug');
    if (slug !== null) return SPOTS.filter((s) => slug === `eq.${s.slug}`);
    return (q.get('offset') ?? '0') === '0' ? SPOTS : [];
  }
  if (table === 'forecasts') {
    const id = q.get('spot_id');
    const spot = SPOTS.find((s) => id === `eq.${s.id}`);
    return spot ? ROWS.get(spot.id) ?? [] : [];
  }
  return [];
}

const stub = http.createServer((req, res) => {
  const url = new URL(req.url ?? '/', 'http://stub');
  const table = /^\/rest\/v1\/([a-z_]+)$/.exec(url.pathname)?.[1];
  asked.push(`${table}?${url.searchParams.toString()}`);
  res.writeHead(200, { 'content-type': 'application/json; charset=utf-8' });
  res.end(JSON.stringify(table ? answer(table, url.searchParams) : []));
});

// --------------------------------------------------------------------------------------------
// Reading a served page.
// --------------------------------------------------------------------------------------------

type Tag = { content: string; index: number };

/** Every <meta> with this name, in document order, with where it sits in the HTML. */
function metaTags(html: string, name: string): Tag[] {
  const out: Tag[] = [];
  for (const m of html.matchAll(/<meta\b[^>]*>/g)) {
    const attrs = new Map<string, string>();
    for (const a of m[0].matchAll(/([A-Za-z_:][-A-Za-z0-9_:.]*)="([^"]*)"/g)) {
      attrs.set(a[1].toLowerCase(), a[2]);
    }
    if (attrs.get('name') === name) out.push({ content: attrs.get('content') ?? '', index: m.index ?? -1 });
  }
  return out;
}

/** The value in the Surf height tile: the page's headline height. */
function surfHeightTile(html: string): string | null {
  const m = /Surf height<\/span>[\s\S]*?<span class="text-2xl[^"]*">([^<]*)<\/span>/.exec(html);
  return m ? m[1] : null;
}

/** What the page states about a spot's break and tide, read off the served HTML. React puts an
 *  empty comment between adjacent text nodes ("tide: <!-- -->low"), so those are removed. */
function sourcedFacts(html: string) {
  const text = (v: string | undefined) => (v === undefined ? null : v.replace(/<!-- -->/g, ''));
  const chip = /<span class="inline-flex items-center px-2 py-0\.5 rounded-full bg-ink-800 border border-ink-600 text-text-primary text-xs">([^<]*)<\/span>/.exec(html);
  const tideChip = /<span>tide: (?:<!-- -->)?([^<]*)<\/span>/.exec(html);
  const infoRow = (k: string) => new RegExp(
    `<span class="text-text-secondary shrink-0">${k}</span><span class="text-text-primary text-right truncate">([^<]*)</span>`,
  ).exec(html)?.[1];
  const optimalRow = (k: string) => new RegExp(
    `<span class="text-text-secondary">${k}</span><span class="text-text-primary text-right truncate tabular-nums">([^<]*)</span>`,
  ).exec(html)?.[1];
  return {
    breakChip: text(chip?.[1]) ?? '(none)',
    tideChip: text(tideChip?.[1]) ?? '(none)',
    infoBreak: text(infoRow('Break')), infoTide: text(infoRow('Tide preference')),
    optimalTide: text(optimalRow('Tide')), optimalBreak: text(optimalRow('Break')),
  };
}

/** Every US zone abbreviation the page shows, sorted: which zone its times are in. */
function zonesOn(html: string): string {
  return [...new Set(html.replace(/<!-- -->/g, '').match(/\b(PDT|PST|MDT|MST|CDT|CST|EDT|EST|HST|AST)\b/g) ?? [])]
    .sort().join(',');
}

/** The Nearest buoy card's Reading row. */
function buoyReading(html: string): string | null {
  return /<span class="text-text-secondary shrink-0">Reading<\/span><span class="text-text-primary text-right truncate">([^<]*)<\/span>/
    .exec(html)?.[1] ?? null;
}

/** The Nearest buoy card's Buoy row. */
function buoyTitle(html: string): string | null {
  return /<span class="text-text-secondary shrink-0">Buoy<\/span><span class="text-text-primary text-right truncate">([^<]*)<\/span>/
    .exec(html)?.[1] ?? null;
}

/** The Nearest buoy card's "No recent wave reading (last: ...)" date. */
function buoyLast(html: string): string | null {
  return /No recent wave reading \(last: ([^)]*)\)/.exec(html)?.[1] ?? null;
}

/** A Nearest buoy card row by its label: "Hs", "Last wave reading", ... */
function buoyRow(html: string, k: string): string | null {
  return new RegExp(`<span class="text-text-secondary shrink-0">${k}</span><span class="text-text-primary text-right truncate">([^<]*)</span>`)
    .exec(html)?.[1] ?? null;
}

/** Checks one tag is there exactly once, inside <head>, and returns its content. */
function oneTagInHead(label: string, html: string, name: string): string | undefined {
  const tags = metaTags(html, name);
  eq(`${label}: ${name} is in the served HTML exactly once`, tags.length, 1);
  const headEnd = html.indexOf('</head>');
  check(`${label}: ${name} is inside <head>`,
    tags.length > 0 && headEnd > 0 && tags.every((t) => t.index < headEnd),
    `tag at ${tags[0]?.index}, </head> at ${headEnd}`);
  return tags[0]?.content;
}

/** Checks a tag's content is a timestamp in toISOString's form, and returns it in ms. */
function asTimestamp(label: string, value: string | undefined): number {
  check(`${label} is ISO 8601 UTC, YYYY-MM-DDTHH:MM:SS.sssZ (${value})`,
    value !== undefined && ISO_UTC.test(value));
  const ms = value === undefined ? Number.NaN : Date.parse(value);
  check(`${label} parses as a timestamp`, Number.isFinite(ms), String(value));
  check(`${label} reads back unchanged`,
    Number.isFinite(ms) && new Date(ms).toISOString() === value);
  return ms;
}

/**
 * sp-headline-hour against sp-rendered-at and against the tile: the hour that contains the render
 * time, and the fixture row whose height the Surf height tile shows.
 */
function checkHeadline(label: string, html: string, renderedMs: number): string | undefined {
  const headline = oneTagInHead(label, html, 'sp-headline-hour');
  const headlineMs = asTimestamp(`${label}: sp-headline-hour`, headline);
  const containingHour = Math.floor(renderedMs / HOUR) * HOUR;
  eq(`${label}: sp-headline-hour is the hour containing sp-rendered-at`,
    headline, new Date(containingHour).toISOString());
  const offset = (headlineMs - T0) / HOUR;
  eq(`${label}: the Surf height tile shows that hour's row (${tileText(offset)})`,
    surfHeightTile(html), tileText(offset));
  return headline;
}

// --------------------------------------------------------------------------------------------
// Build, serve, read.
// --------------------------------------------------------------------------------------------

let env: NodeJS.ProcessEnv = process.env;
let server: ChildProcess | null = null;
let serverLog = '';

function nextCommand(args: string[]): Promise<{ code: number | null; log: string }> {
  return new Promise((resolve) => {
    const child = spawn(process.execPath, [NEXT_BIN, ...args], {
      cwd: FRONTEND, env, stdio: ['ignore', 'pipe', 'pipe'],
    });
    let log = '';
    child.stdout?.on('data', (d) => { log += d; });
    child.stderr?.on('data', (d) => { log += d; });
    child.on('close', (code) => resolve({ code, log }));
  });
}

async function freePort(): Promise<number> {
  const probe = http.createServer();
  await new Promise<void>((resolve) => probe.listen(0, '127.0.0.1', resolve));
  const { port } = probe.address() as AddressInfo;
  await new Promise<void>((resolve) => probe.close(() => resolve()));
  return port;
}

async function main(): Promise<void> {
  await new Promise<void>((resolve) => stub.listen(0, '127.0.0.1', resolve));
  const stubUrl = `http://127.0.0.1:${(stub.address() as AddressInfo).port}`;
  env = {
    ...process.env,
    SUPABASE_URL: stubUrl,
    NEXT_PUBLIC_SUPABASE_URL: stubUrl,
    SUPABASE_SERVICE_KEY: 'stamp-test-key',
    REVALIDATE_SECRET: SECRET,
    NEXT_TELEMETRY_DISABLED: '1',
    NO_PROXY: '127.0.0.1,localhost',
    no_proxy: '127.0.0.1,localhost',
  };

  // 9 needs the first build to start with no fetch cache, so the spot rows it caches are the
  // ones this fixture serves now; the second build then keeps what the first one left.
  fs.rmSync(path.join(FRONTEND, '.next', 'cache', 'fetch-cache'), { recursive: true, force: true });

  async function buildSite(label: string): Promise<{ buildStart: number; buildEnd: number }> {
    console.log(`Building the site against the stand-in Supabase (${label})…`);
    const buildStart = Date.now();
    const build = await nextCommand(['build']);
    const buildEnd = Date.now();
    if (build.code !== 0) {
      console.log(build.log.split('\n').slice(-80).join('\n'));
      throw new Error(`next build exited ${build.code}`);
    }
    console.log(`Built in ${((buildEnd - buildStart) / 1000).toFixed(0)} s.\n`);
    return { buildStart, buildEnd };
  }

  let site = '';
  async function startSite(): Promise<void> {
    const port = await freePort();
    site = `http://127.0.0.1:${port}`;
    server = spawn(process.execPath, [NEXT_BIN, 'start', '-p', String(port), '-H', '127.0.0.1'], {
      cwd: FRONTEND, env, stdio: ['ignore', 'pipe', 'pipe'], detached: true,
    });
    server.stdout?.on('data', (d) => { serverLog += d; });
    server.stderr?.on('data', (d) => { serverLog += d; });
    const upBy = Date.now() + 60_000;
    for (;;) {
      try {
        if ((await fetch(`${site}/robots.txt`)).ok) break;
      } catch {
        // not listening yet
      }
      if (Date.now() > upBy) throw new Error(`next start never answered:\n${serverLog}`);
      await sleep(250);
    }
  }

  const { buildStart, buildEnd } = await buildSite('first build');
  await startSite();

  async function load(slug: string) {
    const res = await fetch(`${site}/spot/${slug}`);
    const html = await res.text();
    return { status: res.status, html, doneAt: Date.now(), cache: res.headers.get('x-nextjs-cache') };
  }

  // 1-3, 5, 7: the copy the build prerendered.
  const first = await load(WITH_ROW.slug);
  eq('the spot page answers 200', first.status, 200);
  const builtStamp = oneTagInHead('prerendered copy', first.html, 'sp-rendered-at');
  const builtMs = asTimestamp('prerendered copy: sp-rendered-at', builtStamp);
  check('prerendered copy: sp-rendered-at falls inside the build, not at the request',
    builtMs >= buildStart && builtMs <= buildEnd,
    `${builtStamp} vs build ${new Date(buildStart).toISOString()}..${new Date(buildEnd).toISOString()}`);
  const builtHeadline = checkHeadline('prerendered copy', first.html, builtMs);
  console.log(`        (x-nextjs-cache: ${first.cache}; sp-rendered-at ${builtStamp}; `
    + `sp-headline-hour ${builtHeadline})`);

  const hits = [...first.html.matchAll(new RegExp(CURL_GREP, 'g'))].map((m) => m[0]);
  eq('the curl grep pattern finds exactly two tags', hits.length, 2);
  eq('...sp-rendered-at first', hits[0], `name="sp-rendered-at" content="${builtStamp}"`);
  eq('...then sp-headline-hour', hits[1], `name="sp-headline-hour" content="${builtHeadline}"`);

  const again = await load(WITH_ROW.slug);
  eq('a later request gets the same cached copy: same sp-rendered-at',
    metaTags(again.html, 'sp-rendered-at')[0]?.content, builtStamp);
  eq('...and the same sp-headline-hour',
    metaTags(again.html, 'sp-headline-hour')[0]?.content, builtHeadline);

  // 6: no row for the current hour.
  const gap = await load(NO_ROW.slug);
  eq('the no-current-row spot page answers 200', gap.status, 200);
  check('no current row: the page says there is no forecast for the current hour',
    gap.html.includes('No forecast published for the current hour'));
  const gapStamp = oneTagInHead('no current row', gap.html, 'sp-rendered-at');
  const gapMs = asTimestamp('no current row: sp-rendered-at', gapStamp);
  check('no current row: sp-rendered-at falls inside the build',
    gapMs >= buildStart && gapMs <= buildEnd, String(gapStamp));
  eq('no current row: sp-headline-hour is absent, not a neighbouring hour',
    metaTags(gap.html, 'sp-headline-hour').length, 0);
  eq('no current row: the curl grep pattern finds just sp-rendered-at',
    [...gap.html.matchAll(new RegExp(CURL_GREP, 'g'))].length, 1);

  // 8: the break type and tide, by source, on four spot pages and two region lists.
  const EXPECTED: [string, ReturnType<typeof sourcedFacts>][] = [
    [PIPELINE.slug, { breakChip: 'reef', tideChip: '(none)', infoBreak: 'reef', infoTide: '—',
                      optimalTide: '—', optimalBreak: 'reef' }],
    [PENSACOLA.slug, { breakChip: 'beach (unverified)', tideChip: '(none)',
                       infoBreak: 'beach (unverified)', infoTide: '—',
                       optimalTide: '—', optimalBreak: 'beach (unverified)' }],
    [SUNSET_POINT.slug, { breakChip: '(none)', tideChip: '(none)', infoBreak: '—', infoTide: '—',
                          optimalTide: '—', optimalBreak: '—' }],
    [VERIFIED_TIDE.slug, { breakChip: 'point', tideChip: 'Low to mid', infoBreak: 'point',
                           infoTide: 'Low to mid', optimalTide: 'Low to mid', optimalBreak: 'point' }],
  ];
  for (const [slug, want] of EXPECTED) {
    const page = await load(slug);
    eq(`${slug}: the spot page answers 200`, page.status, 200);
    const got = sourcedFacts(page.html);
    console.log(`        ${slug}: ${JSON.stringify(got)}`);
    eq(`${slug}: header chip, both cards' Break and Tide rows`, JSON.stringify(got), JSON.stringify(want));
  }
  check('no served spot page says "tide: mid" or "tide: low" for an unattributed preference',
    !(await load(PIPELINE.slug)).html.includes('>tide: ')
    && !(await load(PENSACOLA.slug)).html.includes('>tide: '));
  for (const [state, has, hasNot] of [
    ['florida', '>beach (unverified)<', '>beach<'],
    ['hawaii', '>reef<', 'reef (unverified)'],
  ] as const) {
    const res = await fetch(`${site}/region/${state}`);
    const html = (await res.text()).replace(/<!-- -->/g, '');
    eq(`/region/${state} answers 200`, res.status, 200);
    const list = /<span class="text-xs text-text-muted">([^<]*)<\/span>/.exec(html)?.[1]?.trim();
    console.log(`        /region/${state} break label: ${JSON.stringify(list)}`);
    check(`/region/${state}: the list's break label is ${has.slice(1, -1)}`,
      html.replace(/>\s+/g, '>').replace(/\s+</g, '<').includes(has)
      && !html.replace(/>\s+/g, '>').replace(/\s+</g, '<').includes(hasNot));
  }

  // 4: a regenerated copy.
  const revalidatedAt = Date.now();
  const reval = await fetch(`${site}/api/revalidate`, {
    method: 'POST',
    headers: { authorization: `Bearer ${SECRET}`, 'content-type': 'application/json' },
    body: JSON.stringify({ paths: [`/spot/${WITH_ROW.slug}`] }),
  });
  eq('the revalidation endpoint accepts the spot path', reval.status, 200);
  let fresh: { html: string; doneAt: number; stamp: string } | null = null;
  for (let i = 0; i < 40 && fresh === null; i += 1) {
    const page = await load(WITH_ROW.slug);
    const stamp = metaTags(page.html, 'sp-rendered-at')[0]?.content;
    if (stamp !== undefined && stamp !== builtStamp) fresh = { html: page.html, doneAt: page.doneAt, stamp };
    else await sleep(250);
  }
  check('after revalidation a request gets a regenerated copy with a new sp-rendered-at',
    fresh !== null);
  if (fresh !== null) {
    oneTagInHead('regenerated copy', fresh.html, 'sp-rendered-at');
    const freshMs = asTimestamp('regenerated copy: sp-rendered-at', fresh.stamp);
    check('regenerated copy: rendered after the revalidation was asked for',
      freshMs >= revalidatedAt, `${fresh.stamp} vs ${new Date(revalidatedAt).toISOString()}`);
    check('regenerated copy: rendered no later than the request that returned it',
      freshMs <= fresh.doneAt, `${fresh.stamp} vs ${new Date(fresh.doneAt).toISOString()}`);
    const freshHeadline = checkHeadline('regenerated copy', fresh.html, freshMs);
    console.log(`        (sp-rendered-at ${fresh.stamp}; sp-headline-hour ${freshHeadline})`);
  }

  // 10: the surf-report card, server-rendered.
  const CHIPS: Array<[string, string]> = [
    ['Ankle', 'under 1 ft'], ['Knee', '1–2 ft'], ['Thigh', '2–2.5 ft'], ['Waist', '2.5–3.5 ft'],
    ['Chest', '3.5–4.5 ft'], ['Shoulder', '4.5–5.5 ft'], ['Head high', '5.5–6.5 ft'],
    ['Overhead', '6.5–8 ft'], ['Well overhead', '8–10 ft'], ['Double overhead', '10–13 ft'],
    ['Triple overhead +', '13 ft +'],
  ];
  for (const slug of [WITH_ROW.slug, PIPELINE.slug]) {
    const html = (await load(slug)).html.replace(/<!-- -->/g, '');
    check(`${slug}: the card asks "Surfed here today? How big were most waves?"`,
      html.includes('Surfed here today? How big were most waves?'));
    const chips = [...html.matchAll(
      /<span class="block">([^<]*)<\/span><span class="block text-\[10px\] text-text-muted">([^<]*)<\/span>/g,
    )].map((m) => [m[1], m[2]]);
    eq(`${slug}: the eleven chips with their feet hints, in order`, JSON.stringify(chips),
      JSON.stringify(CHIPS));
    check(`${slug}: no "Was this right?" link`, !html.includes('Was this right?'));
    check(`${slug}: no Hawaiian-scale note`, !html.includes('Hawaiian scale'));
  }

  // 11: the Nearest buoy box (lib/buoyDisplay).
  const pens = (await load(PENSACOLA.slug)).html.replace(/<!-- -->/g, '');
  eq(`${PENSACOLA.slug}: the buoy with its distance`, buoyTitle(pens), 'NDBC 42012 · 41 km away');
  check(`${PENSACOLA.slug}: an hour-old reading is shown`, buoyReading(pens) !== null);
  const sunset = (await load(SUNSET_POINT.slug)).html.replace(/<!-- -->/g, '');
  check(`${SUNSET_POINT.slug}: past 150 km, no reading, the nearest named`,
    sunset.includes('No buoy within 150 km (nearest: NDBC 46221, 381 km)'));
  eq(`${SUNSET_POINT.slug}: no Buoy or Reading row`,
    JSON.stringify([buoyTitle(sunset), buoyReading(sunset)]), '[null,null]');
  check(`${SUNSET_POINT.slug}: the station link`,
    sunset.includes('https://www.ndbc.noaa.gov/station_page.php?station=46221'));
  const jax11 = (await load(JACKSONVILLE.slug)).html.replace(/<!-- -->/g, '');
  eq(`${JACKSONVILLE.slug}: no distance on the row, so the id alone`, buoyTitle(jax11), 'NDBC 41112');
  eq(`${JACKSONVILLE.slug}: an Oct 29 reading is not shown as current`, buoyReading(jax11), null);
  check(`${JACKSONVILLE.slug}: "No recent wave reading" with the last one's date`, buoyLast(jax11) !== null);
  const last = (await load(LAST_WAVE.slug)).html.replace(/<!-- -->/g, '');
  eq(`${LAST_WAVE.slug}: the main buoy`, buoyTitle(last), 'NDBC 46041 · 39 km away');
  eq(`${LAST_WAVE.slug}: the last reading with a wave height`, buoyRow(last, 'Hs'), '3.0 ft');
  check(`${LAST_WAVE.slug}: labelled "Last wave reading", in Pacific time`,
    /^[A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d{2}(am|pm) P[DS]T$/.test(buoyRow(last, 'Last wave reading') ?? ''),
    String(buoyRow(last, 'Last wave reading')));
  eq(`${LAST_WAVE.slug}: no plain Reading row`, buoyReading(last), null);
  const backup = (await load(BACKUP.slug)).html.replace(/<!-- -->/g, '');
  eq(`${BACKUP.slug}: the first fallback within 150 km with a recent wave height`,
    buoyTitle(backup), 'Backup buoy NDBC 51003 · 111 km away');
  eq(`${BACKUP.slug}: its reading`, buoyRow(backup, 'Hs'), '6.6 ft');
  check(`${BACKUP.slug}: its time, in Hawaii time`, /HST$/.test(buoyReading(backup) ?? ''), String(buoyReading(backup)));
  check(`${BACKUP.slug}: its station link`,
    backup.includes('https://www.ndbc.noaa.gov/station_page.php?station=51003'));
  check(`${BACKUP.slug}: not the main buoy's Oct 13 date`, !backup.includes('No recent wave reading'));
  // 12: a blank NWPS hour, filled by the rater.
  const blankPage = await load(BLANK_HOUR.slug);
  const blankHtml = blankPage.html.replace(/<!-- -->/g, '');
  const blankStamp = oneTagInHead(BLANK_HOUR.slug, blankPage.html, 'sp-rendered-at');
  checkHeadline(BLANK_HOUR.slug, blankPage.html, asTimestamp(`${BLANK_HOUR.slug}: sp-rendered-at`, blankStamp));
  check(`${BLANK_HOUR.slug}: the grid's period is swell_tp's, 11s`, blankHtml.includes('>11s<'));
  check(`${BLANK_HOUR.slug}: the grid's direction is swell_dp's, 280°`, blankHtml.includes('280°'));
  check(`${BLANK_HOUR.slug}: no 12 s period from anywhere`, !blankHtml.includes('>12s<'));
  // The page's visible text: scripts, styles and tags out, the fixture's own name too.
  const blankText = blankHtml.replace(/<script[\s\S]*?<\/script>|<style[\s\S]*?<\/style>/g, ' ')
    .replace(/<[^>]+>/g, ' ').replace(/Stamp Test Blank Hour/g, '');
  const said = /\b(blank|filled|missing|estimated|interpolated)\b|neighbou?ring hour/i.exec(blankText);
  check(`${BLANK_HOUR.slug}: nothing in the page's text says the hour was blank or filled`, said === null,
    said ? blankText.slice(Math.max(0, said.index - 60), said.index + 60) : '');
  const plain = (await load(WITH_ROW.slug)).html.replace(/<!-- -->/g, '');
  check(`${WITH_ROW.slug}: no buoy, "No nearby buoy."`, plain.includes('No nearby buoy.'));
  // 13: the model-run note, only on the spot whose cycle is 30 hours old.
  check(`${WITH_ROW.slug}: a cycle six hours old, no model-run note`,
    !plain.includes('Wave model run') && !plain.includes('data-model-run-age'));
  const oldPage = await load(OLD_RUN.slug);
  const oldHtml = oldPage.html.replace(/<!-- -->/g, '');
  const oldRendered = asTimestamp(`${OLD_RUN.slug}: sp-rendered-at`,
    oneTagInHead(OLD_RUN.slug, oldPage.html, 'sp-rendered-at'));
  const wantHours = Math.floor((oldRendered - OLD_CYCLE_MS) / HOUR);
  check(`${OLD_RUN.slug}: the cycle is 30 h old, 31 if the build crossed an hour`,
    wantHours === 30 || wantHours === 31, String(wantHours));
  check(`${OLD_RUN.slug}: "Wave model run is ${wantHours} h old"`,
    oldHtml.includes(`data-model-run-age="${wantHours}"`)
      && oldHtml.includes(`Wave model run is ${wantHours} h old`));
  check(`${OLD_RUN.slug}: the run's time, in Pacific time`,
    /Wave model run is \d+ h old · NOAA NWPS run of [A-Z][a-z]{2} \d{1,2}, \d{1,2}:\d{2}(am|pm) P[DS]T/
      .test(oldHtml), (/Wave model run[^<]*/.exec(oldHtml) ?? [''])[0]);
  for (const sp of [PIPELINE, PENSACOLA, BLANK_HOUR]) {
    const other = (await load(sp.slug)).html;
    check(`${sp.slug}: a cycle six hours old, no model-run note`, !other.includes('Wave model run'));
  }

  // 9: time zones. The zone abbreviations depend on the date the test runs (daylight or standard
  // time), so they are held to the zone's pair; the buoy reading is a fixed instant.
  const centralZones = new Set(['CDT', 'CST', 'CDT,CST']);
  const easternZones = new Set(['EDT', 'EST', 'EDT,EST']);
  const pacificZones = new Set(['PDT', 'PST', 'PDT,PST']);
  const pensacola = await load(PENSACOLA.slug);
  console.log(`        ${PENSACOLA.slug} zones: ${zonesOn(pensacola.html)}`);
  check(`${PENSACOLA.slug} (America/Chicago): Central time only`, centralZones.has(zonesOn(pensacola.html)),
    zonesOn(pensacola.html));
  const jaxBefore = await load(JACKSONVILLE.slug);
  console.log(`        ${JACKSONVILLE.slug}, no time_zone: ${zonesOn(jaxBefore.html)}; `
    + `last reading ${JSON.stringify(buoyLast(jaxBefore.html))}`);
  check(`${JACKSONVILLE.slug} with no time_zone: the Los Angeles fallback, labelled`,
    pacificZones.has(zonesOn(jaxBefore.html)), zonesOn(jaxBefore.html));
  eq(`${JACKSONVILLE.slug} with no time_zone: buoy's last reading`, buoyLast(jaxBefore.html), 'Oct 29, 6:50am PDT');

  // Migration 026 applied between deploys; the second build keeps the first one's .next/cache.
  const pid = server?.pid;
  if (pid !== undefined) {
    const closed = new Promise((resolve) => server?.on('close', resolve));
    process.kill(-pid, 'SIGTERM');
    await closed;
  }
  server = null;
  JACKSONVILLE.time_zone = 'America/New_York';
  const askedBefore = asked.length;
  await buildSite('second build, Jacksonville Beach Pier now has its time_zone, .next/cache kept');
  const rowRead = `spots?select=*&slug=eq.${JACKSONVILLE.slug}`;
  check(`the second build asked the stand-in for ${rowRead}, not the fetch cache`,
    asked.slice(askedBefore).includes(rowRead), `${asked.length - askedBefore} requests`);
  await startSite();
  const jaxAfter = await load(JACKSONVILLE.slug);
  console.log(`        ${JACKSONVILLE.slug}, America/New_York: ${zonesOn(jaxAfter.html)}; `
    + `last reading ${JSON.stringify(buoyLast(jaxAfter.html))}`);
  eq(`${JACKSONVILLE.slug} after the second build: answers 200`, jaxAfter.status, 200);
  check(`${JACKSONVILLE.slug} (America/New_York) after the second build: Eastern time only`,
    easternZones.has(zonesOn(jaxAfter.html)), zonesOn(jaxAfter.html));
  eq(`${JACKSONVILLE.slug} (America/New_York) after the second build: buoy's last reading`,
    buoyLast(jaxAfter.html), 'Oct 29, 9:50am EDT');
}

// A hung build or server fails the run instead of holding CI open.
const watchdog = setTimeout(() => {
  console.log('spotPageStamps: timed out after 10 minutes');
  process.exit(1);
}, 10 * 60_000);
watchdog.unref();

try {
  await main();
} finally {
  if (server?.pid !== undefined) {
    try {
      process.kill(-server.pid, 'SIGTERM');
    } catch {
      // already gone
    }
  }
  stub.closeAllConnections();
  stub.close();
}

if (failures > 0) {
  console.log(`\nnext start log:\n${serverLog.split('\n').slice(-40).join('\n')}`);
  throw new Error(`spotPageStamps: ${failures} FAILURE(S)`);
}
console.log('\nspotPageStamps: ALL PASS');
