/**
 * The site credits every data source the forecast is built on, in both places it lists them.
 *
 * WHAT THIS HOLDS TOGETHER. The footer's "Data sources" list and the About page's "How it works"
 * paragraph name the providers behind the forecast. CDIP (Scripps Institution of Oceanography's
 * Coastal Data Information Program) was missing from both, although 48 spots take their height
 * from its MOP model and 141 are calibrated against it. This checks that each list names every
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

// --------------------------------------------------------------------------- //
// The terms page credits every source too, and the legal pages and contact     //
// address are reachable from every page.                                       //
// --------------------------------------------------------------------------- //
const terms = read('app/terms/page.tsx').replace(/\s+/g, ' ').replace(/&rsquo;/g, '’');
const TERMS_NAMES = ['Nearshore Wave Prediction System', 'WAVEWATCH III', 'HRRR', 'National Data Buoy Center',
  'CO-OPS', 'CDIP, Scripps Institution of Oceanography', 'OpenStreetMap contributors', 'CARTO'];
const termsMissing = TERMS_NAMES.filter((n) => !terms.includes(n));
check('the terms page credits every data source and the map tiles', termsMissing.length === 0, termsMissing.join(', '));
check('...and links CDIP to its homepage', terms.includes('href="https://cdip.ucsd.edu/"'));
for (const [label, needle] of [
  ['forecasts are estimates', 'Forecasts are estimates'],
  ['the ocean is dangerous', 'The ocean is dangerous'],
  ['check conditions yourself', 'Check conditions yourself'],
  ['no liability', 'No warranty, no liability'],
] as const) check(`the terms say ${label}`, terms.includes(needle));

const privacy = read('app/privacy/page.tsx').replace(/\s+/g, ' ').replace(/&rsquo;/g, '’');
for (const [label, needle] of [
  ['Google Analytics and its cookies', 'Google Analytics'],
  ['the daily-salted report code', 'a secret that changes every day'],
  ['the report cap, from the route\'s own constant', 'up to {DAILY_REPORT_CAP} reports a day'],
  ['that IP addresses are never stored', 'We never store your IP address'],
  ['how long reports are kept', 'We keep them indefinitely'],
  ['error reports carry no personal data', 'no IP address, no browser details and no cookies'],
  ['no selling of data', 'sell'],
  ['Do Not Track (CalOPPA requires it)', 'Do Not Track'],
  ['CalOPPA', 'California Online Privacy Protection Act'],
  ['embedded cam players may set cookies', 'may set its own cookies'],
  ['an effective date', 'Effective {EFFECTIVE}'],
] as const) check(`the privacy policy covers ${label}`, privacy.includes(needle));
check('the privacy policy takes the cap from lib/surfReport, not a typed number',
  privacy.includes("import { DAILY_REPORT_CAP } from '@/lib/surfReport'"));

const footerLinks = read('components/SiteFooter.tsx');
check('the footer links Privacy', footerLinks.includes('href="/privacy"'));
check('the footer links Terms', footerLinks.includes('href="/terms"'));
check('the footer shows the contact address', footerLinks.includes('{CONTACT_EMAIL}') && footerLinks.includes('mailtoHref('));
const sitemap = read('app/sitemap.ts');
check('the sitemap lists /privacy and /terms', sitemap.includes('${SITE_URL}/privacy') && sitemap.includes('${SITE_URL}/terms'));

// The About page: no claims about another company's conduct, and "tell us" is a link to write to.
check('the About page makes no claims about a competitor\'s conduct',
  !/bought every other|shut them down|60-second paywall|You know who/.test(about));
const tellUs = [...about.matchAll(/<a href=\{mailtoHref\([^)]*\)\}[^>]*> tell us <\/a>/g)].length;
check('both of the About page\'s "tell us" are mailto links', tellUs === 2, String(tellUs));

// The spot page: "Report incorrect data" is an email, not GitHub's new-issue form.
const spotPage = read('app/spot/[slug]/page.tsx');
check('the spot page reports bad data by email', spotPage.includes('href={spotReportHref(spot.name, spot.slug,'));
check('...and no longer sends readers to GitHub issues', !spotPage.includes('issues/new'));

// lib/contact: the address, and the mailto encoding.
{
  const { CONTACT_EMAIL, mailtoHref, spotReportHref } = await import('./contact.ts');
  check('the contact address', CONTACT_EMAIL === 'hello@stormypetrel.surf', CONTACT_EMAIL);
  check('a bare mailto', mailtoHref() === 'mailto:hello@stormypetrel.surf', mailtoHref());
  const href = spotReportHref("Ho'okipa & Co", 'hookipa', 'https://stormypetrel.surf/spot/hookipa');
  check('the spot report subject names the spot, spaces as %20 not +',
    href.startsWith("mailto:hello@stormypetrel.surf?subject=Spot%20data%3A%20Ho'okipa%20%26%20Co&body="), href);
  check('the body carries the slug and page, newlines encoded',
    href.includes('body=Spot%3A%20Ho\'okipa%20%26%20Co%20(hookipa)%0APage%3A%20https%3A%2F%2Fstormypetrel.surf%2Fspot%2Fhookipa'), href);
  check('nothing in the link is left unencoded that would split it', !/[ \n&](?!body=)/.test(href.split('?')[1].replace(/&body=/, '')), href);
}

if (failures > 0) {
  throw new Error(`dataSources: ${failures} FAILURE(S)`);
}
console.log('\ndataSources: ALL PASS');
