/**
 * The pre-launch cleanup (launch-readiness #22, #26, #27 and the soft 404), held at the source.
 * The served behaviour is checked by the render test (tests/spotPageStamps.test.mts item 25-26).
 *
 *     node --experimental-strip-types frontend/lib/prelaunch.test.mts
 */
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { analyticsEnabled, GA_ID } from './analytics.ts';

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (rel: string) => readFileSync(join(FRONTEND, rel), 'utf8');
let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) console.log(`  PASS  ${name}`);
  else { failures += 1; console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`); }
}

// A real 404 for a missing spot: no loading boundary above /spot/[slug] streams the status out
// before notFound() runs. The homepage keeps its skeleton, scoped to the (home) route group.
check('/spot/[slug] has no loading.tsx', !existsSync(join(FRONTEND, 'app/spot/[slug]/loading.tsx')));
check('the app root has no loading.tsx (it wrapped every route)', !existsSync(join(FRONTEND, 'app/loading.tsx')));
check('the homepage and its skeleton live in the (home) group',
  existsSync(join(FRONTEND, 'app/(home)/page.tsx')) && existsSync(join(FRONTEND, 'app/(home)/loading.tsx'))
  && !existsSync(join(FRONTEND, 'app/page.tsx')));
function loadings(dir: string): string[] {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? loadings(p) : n === 'loading.tsx' ? [p.slice(FRONTEND.length + 1)] : [];
  });
}
const all = loadings(join(FRONTEND, 'app'));
check('the only loading.tsx left is the homepage\'s (none wraps /spot or /region)',
  all.length === 1 && all[0] === 'app/(home)/loading.tsx', all.join(', '));
check('a new region still renders on first visit (no dynamicParams = false)',
  !/dynamicParams\s*=\s*false/.test(read('app/region/[state]/page.tsx')));
check('a new spot still renders on first visit (no dynamicParams = false)',
  !/dynamicParams\s*=\s*false/.test(read('app/spot/[slug]/page.tsx')));

// #22 /cams in the sitemap.
check('the sitemap lists /cams', read('app/sitemap.ts').includes('`${SITE_URL}/cams`'));

// #26 analytics on production only.
check('analytics on the production deployment', analyticsEnabled({ VERCEL_ENV: 'production' }) === true);
check('...not on a preview', analyticsEnabled({ VERCEL_ENV: 'preview' }) === false);
check('...not under vercel dev', analyticsEnabled({ VERCEL_ENV: 'development' }) === false);
check('...not locally or in CI (unset)', analyticsEnabled({}) === false);
check('the measurement id is unchanged', GA_ID === 'G-S7Y5BXQ125');
const layout = read('app/layout.tsx');
check('the layout renders the tag only when analyticsEnabled()',
  layout.includes('{analyticsEnabled() && (') && layout.indexOf('{analyticsEnabled() && (') < layout.indexOf('googletagmanager.com'));

// #26 Leaflet's stylesheets bundled, never fetched from a CDN at run time.
for (const f of ['components/SpotMap.tsx', 'components/learn/BuoyMap.tsx', 'components/learn/SwellDirectionMap.tsx',
  'components/learn/WindDirectionMap.tsx']) {
  const src = read(f);
  check(`${f} imports leaflet.css and loads nothing from unpkg`, src.includes("import 'leaflet/dist/leaflet.css';")
    && !src.includes('unpkg.com') && !src.includes('ensureCss('));
}
const map = read('components/SpotMap.tsx');
check('the map bundles both marker-cluster stylesheets', map.includes("import 'leaflet.markercluster/dist/MarkerCluster.css';")
  && map.includes("import 'leaflet.markercluster/dist/MarkerCluster.Default.css';"));

// #27 the reports empty state names no clock time.
const reports = read('app/reports/page.tsx');
check('the reports empty state has no UTC time', !/\d{1,2}:\d{2}\s*UTC/.test(reports) && reports.includes('No reports yet.'));

if (failures > 0) throw new Error(`prelaunch: ${failures} FAILURE(S)`);
console.log('\nprelaunch: ALL PASS');
