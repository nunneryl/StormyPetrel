/**
 * The site credits every data source the forecast is built on, in both places it lists them.
 *
 * WHAT THIS HOLDS TOGETHER. The footer's "Data sources" list and the About page's "How it works"
 * paragraph name the providers behind the forecast. CDIP (Scripps Institution of Oceanography's
 * Coastal Data Information Program) was missing from both, although 48 spots take their height
 * from its MOP model and 130 are calibrated against it. This checks that each list names every
 * source, that CDIP stays credited for as long as any spot is on the MOP tier, and that the
 * footer links CDIP the way it links the others.
 *
 * NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST. The source names are written out here; the
 * pipeline's own spot file says whether CDIP is in use.
 *
 *     node --experimental-strip-types frontend/lib/dataSources.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (rel: string) => readFileSync(join(FRONTEND, rel), 'utf8');

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures++;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}

// Every provider the pipeline reads, by the name each list uses for it.
const FOOTER_NAMES = ['NWPS', 'CDIP', 'WAVEWATCH III', 'HRRR', 'NDBC', 'CO-OPS'];
const ABOUT_NAMES = [
  'Nearshore Wave Prediction System', 'WAVEWATCH III', 'HRRR', 'NDBC', 'CO-OPS',
  'Scripps Institution of Oceanography', 'Coastal Data Information Program (CDIP)',
];

// --------------------------------------------------------------------------- //
// The footer                                                                  //
// --------------------------------------------------------------------------- //
const footer = read('components/SiteFooter.tsx');
const list = /Data sources\s*<\/div>\s*<ul[^>]*>([\s\S]*?)<\/ul>/.exec(footer)?.[1] ?? '';
check('the footer has its Data sources list', list.length > 0);
type SourceLink = { name: string; href: string; attrs: string };
const links: SourceLink[] = [...list.matchAll(/<a\s+href="([^"]+)"([^>]*)>\s*([^<]+?)\s*<\/a>/g)]
  .map(([, href, attrs, name]) => ({ name, href, attrs: attrs.trim().replace(/\s+/g, ' ') }));
check('every source is in the footer list, each once', JSON.stringify(links.map((l) => l.name).sort())
  === JSON.stringify([...FOOTER_NAMES].sort()), JSON.stringify(links.map((l) => l.name)));
const cdip = links.find((l) => l.name === 'CDIP');
check('the footer links CDIP to its own site', cdip?.href === 'https://cdip.ucsd.edu/', cdip?.href);
check('CDIP is linked exactly the way the others are', new Set(links.map((l) => l.attrs)).size === 1,
  JSON.stringify([...new Set(links.map((l) => l.attrs))]));
check('the footer credits Scripps beside CDIP', /CDIP\s*<\/a>\{' '\}\s*— [^<]*Scripps/.test(list));

// --------------------------------------------------------------------------- //
// The About page                                                              //
// --------------------------------------------------------------------------- //
const about = read('app/about/page.tsx').replace(/\s+/g, ' ').replace(/&rsquo;/g, '’');
const howItWorks = /How it works[\s\S]*?<p>([\s\S]*?)<\/p>/.exec(about)?.[1] ?? '';
const missing = ABOUT_NAMES.filter((n) => !howItWorks.includes(n));
check('the About page names every source in its How it works paragraph', missing.length === 0,
  missing.join(', '));
check('...and, like the others there, CDIP is named rather than linked', !/<a\b/.test(howItWorks));

// --------------------------------------------------------------------------- //
// Tied to the pipeline: CDIP is credited while the forecast still uses it       //
// --------------------------------------------------------------------------- //
const spots = JSON.parse(read('../pipeline/spots_enriched.json')) as { swell_window_source?: string }[];
const onMop = spots.filter((s) => s.swell_window_source === 'cdip_mop').length;
const calibrated = Object.keys(
  (JSON.parse(read('../pipeline/data/spot_face_factors.json')) as { factors?: object }).factors ?? {}).length;
check(`the credit is owed: ${onMop} spots take MOP's height and ${calibrated} are calibrated against it`,
  onMop + calibrated > 0, 'CDIP is no longer used; drop its credit and this check together');

if (failures > 0) {
  throw new Error(`dataSources: ${failures} FAILURE(S)`);
}
console.log('\ndataSources: ALL PASS');
