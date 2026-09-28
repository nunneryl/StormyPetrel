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
 *   5. sp-headline-hour is the hour containing sp-rendered-at, and it is the row the Swell height
 *      tile shows: every fixture hour has its own height, so the tile's text names the row.
 *   6. With no row for the current hour the page says so, and sp-headline-hour is absent.
 *   7. The grep pattern given for reading the tags with curl finds exactly the two tags.
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
  break_type: 'reef', tide_preference: null, crowd_factor: null, hazards: null,
  nearest_buoy_id: null, nearest_buoy_dist_km: null, nearest_tide_station_id: null,
  description: null, swell_window_source: null,
};
// A row for every hour from 12 back to 48 ahead, so whatever hour the page renders in has one.
const WITH_ROW = { ...SPOT_FIELDS, id: 990001, slug: 'stamp-test-point', name: 'Stamp Test Point' };
// Rows only from 3 hours ahead, so no row covers the hour the page renders in.
const NO_ROW = { ...SPOT_FIELDS, id: 990002, slug: 'stamp-test-gap', name: 'Stamp Test Gap' };
const SPOTS = [WITH_ROW, NO_ROW];

// Every hour has its own height: 2.0 ft twelve hours back, rising 0.1 ft an hour. The Swell
// height tile shows one decimal, so the tile's text names exactly one row.
function heightAt(offsetHours: number): number {
  return 2 + (offsetHours + 12) / 10;
}
function tileText(offsetHours: number): string {
  return `${heightAt(offsetHours).toFixed(1)}ft`;
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
    face_ft: face, face_ft_raw: face, face_lo_ft: null, face_hi_ft: null,
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
]);

// Enough of PostgREST for this site: the two spots, their rows, and an empty list for anything
// else (every other page then renders its empty state, which is all the build needs).
function answer(table: string, q: URLSearchParams): unknown[] {
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

/** The value in the Swell height tile: the page's headline height. */
function swellHeightTile(html: string): string | null {
  const m = /Swell height<\/span>[\s\S]*?<span class="text-2xl[^"]*">([^<]*)<\/span>/.exec(html);
  return m ? m[1] : null;
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
 * time, and the fixture row whose height the Swell height tile shows.
 */
function checkHeadline(label: string, html: string, renderedMs: number): string | undefined {
  const headline = oneTagInHead(label, html, 'sp-headline-hour');
  const headlineMs = asTimestamp(`${label}: sp-headline-hour`, headline);
  const containingHour = Math.floor(renderedMs / HOUR) * HOUR;
  eq(`${label}: sp-headline-hour is the hour containing sp-rendered-at`,
    headline, new Date(containingHour).toISOString());
  const offset = (headlineMs - T0) / HOUR;
  eq(`${label}: the Swell height tile shows that hour's row (${tileText(offset)})`,
    swellHeightTile(html), tileText(offset));
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

  console.log('Building the site against the stand-in Supabase…');
  const buildStart = Date.now();
  const build = await nextCommand(['build']);
  const buildEnd = Date.now();
  if (build.code !== 0) {
    console.log(build.log.split('\n').slice(-80).join('\n'));
    throw new Error(`next build exited ${build.code}`);
  }
  console.log(`Built in ${((buildEnd - buildStart) / 1000).toFixed(0)} s.\n`);

  const port = await freePort();
  const site = `http://127.0.0.1:${port}`;
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
