/**
 * The /map basemap: a CARTO key gives CARTO's light tiles with the key on every URL; no key
 * gives OpenStreetMap's keyless tiles, never a keyless CARTO URL (which CARTO now serves
 * watermarked "API KEY REQUIRED"). Every expected value is written out by hand.
 *
 *     node --experimental-strip-types frontend/lib/basemap.test.mts
 */
import { basemap } from './basemap.ts';

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) console.log(`  PASS  ${name}`);
  else { failures += 1; console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`); }
}

const OSM = '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors';

// With a key: CARTO light_all, on its a-d hosts, the key on the URL, both credits.
{
  const b = basemap('abc123');
  check('a key gives CARTO light_all with ?key=',
    b.url === 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png?key=abc123', b.url);
  check('CARTO tiles rotate over a-d', b.options.subdomains === 'abcd', String(b.options.subdomains));
  check('CARTO tiles credit OpenStreetMap contributors and CARTO',
    b.options.attribution === `${OSM} &copy; <a href="https://carto.com/attributions">CARTO</a>`,
    b.options.attribution);
  check('maxZoom 19', b.options.maxZoom === 19);
}
check('a key is URL-encoded', basemap('a b&c').url.endsWith('?key=a%20b%26c'), basemap('a b&c').url);
check('surrounding whitespace is trimmed', basemap('  abc123\n').url.endsWith('?key=abc123'), basemap('  abc123\n').url);

// Without one: OpenStreetMap, never a keyless CARTO URL.
for (const [label, k] of [['undefined', undefined], ['null', null], ['empty', ''], ['blank', '   ']] as const) {
  const b = basemap(k);
  check(`${label} key falls back to OpenStreetMap's standard tiles`,
    b.url === 'https://tile.openstreetmap.org/{z}/{x}/{y}.png', b.url);
  check(`${label} key: no CARTO host anywhere`, !b.url.includes('cartocdn'), b.url);
  check(`${label} key: OpenStreetMap contributors credited, and not CARTO`,
    b.options.attribution === OSM, b.options.attribution);
  check(`${label} key: no subdomains (OSM serves one host)`, b.options.subdomains === undefined);
}

if (failures > 0) throw new Error(`basemap: ${failures} FAILURE(S)`);
console.log('\nbasemap: ALL PASS');
