/**
 * WHICH SUPABASE READS GO THROUGH NEXT'S FETCH CACHE (the Vercel Data Cache), and why each may.
 *
 * A plain read on a page with `revalidate` is stored in the Data Cache under its URL, and a
 * deploy's build is handed the stored copy, however old (lib/supabase.ts). A read whose URL stays
 * the same across a migration therefore keeps pre-migration rows: the spot page showed Los Angeles
 * times from a row cached before migration 026 (#286). This file holds the audit:
 *
 *   1. FRESH. Every read of `spots` (select('*'), the same URL whatever a migration changes) and of
 *      `cams` (migration 025 re-pointed cams.spot_slug when it renamed two spots) goes through
 *      supabaseUncached.
 *   2. CACHED, ON PURPOSE, AND NO OTHERS:
 *        fetchLatestForecastPerSpot, fetchSparklineData   the current time is in the query
 *                                                          (valid_time >= now), so its URL never
 *                                                          repeats and no cached copy is served;
 *        fetchLatestReports, fetchReportsForDate,
 *        fetchReportIndex, fetchReport                     daily_reports: written whole each day,
 *                                                          no migration has changed its rows.
 *      A new cached read fails here until it is added to this list with its reason.
 *   3. STATIC. Every page, and the sitemap, sets dynamic = 'force-static': the root layout reads
 *      the spots for the nav search past the cache, and that would otherwise render every page on
 *      every request.
 *
 * The API route (app/api/reports, POST) is not in the Data Cache: a POST handler is dynamic.
 *
 *     node --experimental-strip-types frontend/lib/dataCache.test.mts
 */
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}

const FRONTEND = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (rel: string) => readFileSync(join(FRONTEND, rel), 'utf8');

/** {function name: 'cached' | 'fresh' | 'none'} for every exported async function in a lib file. */
function reads(rel: string): Record<string, string> {
  const src = read(rel);
  const out: Record<string, string> = {};
  const starts = [...src.matchAll(/^export async function (\w+)/gm)];
  starts.forEach((m, i) => {
    const body = src.slice(m.index, i + 1 < starts.length ? starts[i + 1].index : src.length);
    const cached = /\bsupabase\s*\n?\s*\.from\(|await supabase\b(?!Uncached)/.test(body);
    const fresh = /\bsupabaseUncached\b/.test(body);
    out[m[1]] = cached ? 'cached' : fresh ? 'fresh' : 'none';
  });
  return out;
}

// 1 and 2.
const all = { ...reads('lib/queries.ts'), ...reads('lib/cams.ts'), ...reads('lib/reports.ts') };
for (const fn of ['fetchSpotBySlug', 'fetchSpotsBySlug', 'fetchAllSpots', 'fetchCamsForSpot',
  'fetchAllActiveCams', 'fetchCamSlugSet']) {
  check(`${fn} reads past the Data Cache`, all[fn] === 'fresh', String(all[fn]));
}
const CACHED = ['fetchLatestForecastPerSpot', 'fetchSparklineData', 'fetchLatestReports',
  'fetchReportsForDate', 'fetchReportIndex', 'fetchReport'];
const cachedNow = Object.keys(all).filter((k) => all[k] === 'cached').sort();
check('the cached reads are exactly the six listed', JSON.stringify(cachedNow) === JSON.stringify([...CACHED].sort()),
  cachedNow.join(', '));
const queries = read('lib/queries.ts');
for (const fn of ['fetchLatestForecastPerSpot', 'fetchSparklineData']) {
  const start = queries.indexOf(`export async function ${fn}`);
  const body = queries.slice(start, queries.indexOf('\nexport ', start + 1));
  check(`${fn} puts the current time in its query`,
    body.includes("new Date().toISOString()") && body.includes(".gte('valid_time', nowIso)"));
}
check('no other file queries Supabase through the cached client',
  ['lib/surfReport.ts', 'lib/cams.ts'].every((f) => !/import \{[^}]*\bsupabase\b[^}]*\} from/.test(read(f))));

// 3.
function walk(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : [p];
  });
}
const pages = walk(join(FRONTEND, 'app'))
  .map((p) => relative(FRONTEND, p))
  .filter((p) => p.endsWith('/page.tsx') || p === 'app/sitemap.ts')
  .sort();
check('every page is found (19 pages and the sitemap)', pages.length === 20, String(pages.length));
for (const p of pages) {
  check(`${p} is force-static`, read(p).includes("export const dynamic = 'force-static';"));
}
check('the root layout reads the spots for the nav search', read('app/layout.tsx').includes('await fetchAllSpots()'));

if (failures) {
  console.log(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log('\ndataCache: ALL PASS');
