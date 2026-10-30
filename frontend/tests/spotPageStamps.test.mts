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
 *      each with its feet hint, and no "Was this right?" link. Banzai Pipeline (Hawaii) adds
 *      "Face height, not the Hawaiian scale."; a California spot does not.
 *   9. A spot row's time_zone reaches the page's times (lib/spotTime): Pensacola Beach, stored as
 *      America/Chicago, shows Central abbreviations only; Jacksonville Beach Pier, whose row has
 *      no time_zone in the first build (the shape of a row read before migration 026), shows the
 *      Los Angeles fallback, labelled, with its buoy reading 13:50Z as "Oct 29, 6:50am PDT".
 *      Then the stand-in gains its time_zone and the site is built AGAIN WITHOUT CLEARING
 *      .next/cache: the second build must ask the stand-in for that spot's row rather than take
 *      the copy the first build cached, and show "Oct 29, 9:50am EDT" and Eastern abbreviations
 *      only. That second build is the 2026-10-29 preview: migration 026 applied, a redeploy
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
};
const SUNSET_POINT = {
  ...SPOT_FIELDS, id: 990005, slug: 'sunset-point', name: 'Sunset Point',
  lat: 34.037, lng: -118.554, state: 'California', region: 'Malibu',
  break_type: null, break_type_source: null, tide_preference: null, tide_preference_source: null,
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
const SOURCE_SPOTS = [PIPELINE, PENSACOLA, SUNSET_POINT, VERIFIED_TIDE, JACKSONVILLE];
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
  };
}

function hours(from: number, to: number): number[] {
  const out: number[] = [];
  for (let h = from; h <= to; h += 1) out.push(h);
  return out;
}

const ROWS = new Map<number, Record<string, unknown>[]>([
  [WITH_ROW.id, hours(-12, 48).map((h) => row(WITH_ROW.id, h))],
  [NO_ROW.id, hours(3, 48).map((h) => row(NO_ROW.id, h))],
  ...SOURCE_SPOTS.map((sp) => [sp.id, hours(-12, 48).map((h) => row(sp.id, h))] as
    [number, Record<string, unknown>[]]),
]);

// Every request the stand-in answers, as "table?query", so a check can see what a build asked.
const asked: string[] = [];

// Enough of PostgREST for this site: the spots, their rows, the one buoy reading, and an empty
// list for anything else (every other page then renders its empty state, which is all the build
// needs).
function answer(table: string, q: URLSearchParams): unknown[] {
  if (table === 'buoy_observations') return q.get('buoy_id') === `eq.${BUOY.buoy_id}` ? [BUOY] : [];
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
  for (const [slug, hawaii] of [[WITH_ROW.slug, false], [PIPELINE.slug, true]] as const) {
    const html = (await load(slug)).html.replace(/<!-- -->/g, '');
    check(`${slug}: the card asks "Surfed here today? How big were most waves?"`,
      html.includes('Surfed here today? How big were most waves?'));
    const chips = [...html.matchAll(
      /<span class="block">([^<]*)<\/span><span class="block text-\[10px\] text-text-muted">([^<]*)<\/span>/g,
    )].map((m) => [m[1], m[2]]);
    eq(`${slug}: the eleven chips with their feet hints, in order`, JSON.stringify(chips),
      JSON.stringify(CHIPS));
    check(`${slug}: no "Was this right?" link`, !html.includes('Was this right?'));
    eq(`${slug}: the Hawaiian-scale note ${hawaii ? 'is' : 'is not'} there`,
      html.includes('Face height, not the Hawaiian scale.'), hawaii);
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
    + `Reading ${JSON.stringify(buoyReading(jaxBefore.html))}`);
  check(`${JACKSONVILLE.slug} with no time_zone: the Los Angeles fallback, labelled`,
    pacificZones.has(zonesOn(jaxBefore.html)), zonesOn(jaxBefore.html));
  eq(`${JACKSONVILLE.slug} with no time_zone: buoy reading`, buoyReading(jaxBefore.html), 'Oct 29, 6:50am PDT');

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
    + `Reading ${JSON.stringify(buoyReading(jaxAfter.html))}`);
  eq(`${JACKSONVILLE.slug} after the second build: answers 200`, jaxAfter.status, 200);
  check(`${JACKSONVILLE.slug} (America/New_York) after the second build: Eastern time only`,
    easternZones.has(zonesOn(jaxAfter.html)), zonesOn(jaxAfter.html));
  eq(`${JACKSONVILLE.slug} (America/New_York) after the second build: buoy reading`,
    buoyReading(jaxAfter.html), 'Oct 29, 9:50am EDT');
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
