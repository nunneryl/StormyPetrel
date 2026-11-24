/**
 * The /map basemap: a CARTO key gives CARTO's light tiles with the key on every URL; no key
 * gives OpenStreetMap's keyless tiles, never a keyless CARTO URL (which CARTO now serves
 * watermarked "API KEY REQUIRED"). Every expected value is written out by hand.
 *
 *     node --experimental-strip-types frontend/lib/basemap.test.mts
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

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

// EVERY LEAFLET MAP ON THE SITE credits its tiles. /learn/swell-direction and /learn/wind turned
// the credit off (attributionControl: false) and loaded keyless CARTO tiles; /learn/buoys wrote a
// credit of its own without the OpenStreetMap link. So: every file that creates a tile layer
// takes its URL and options from basemap() (whose credits are pinned above), none switches the
// credit off, and no CARTO URL is written anywhere but lib/basemap.ts.
{
  const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..');
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const name of readdirSync(dir)) {
      const p = join(dir, name);
      if (statSync(p).isDirectory()) walk(p);
      else if (/\.(tsx?|mts)$/.test(name) && !name.includes('.test.')) files.push(p);
    }
  };
  walk(join(FRONTEND, 'components'));
  walk(join(FRONTEND, 'app'));
  const mapFiles = files.filter((f) => readFileSync(f, 'utf8').includes('tileLayer('));
  const names = mapFiles.map((f) => relative(FRONTEND, f)).sort();
  // Written out, so a new map is seen here and has to be added on purpose.
  check('the Leaflet maps are the four known ones',
    JSON.stringify(names) === JSON.stringify([
      'components/SpotMap.tsx',
      'components/learn/BuoyMap.tsx',
      'components/learn/SwellDirectionMap.tsx',
      'components/learn/WindDirectionMap.tsx',
    ]), JSON.stringify(names));
  for (const f of mapFiles) {
    const src = readFileSync(f, 'utf8');
    const rel = relative(FRONTEND, f);
    check(`${rel}: tiles come from basemap()`,
      src.includes('basemap(process.env.NEXT_PUBLIC_CARTO_BASEMAPS_KEY)')
        && /tileLayer\(tiles\.url, (tiles\.options|\{ \.\.\.tiles\.options[^}]*\})\)/.test(src));
    check(`${rel}: the credit is not switched off`, !/attributionControl:\s*false/.test(src));
    check(`${rel}: no tile layer sets an attribution of its own`, !/attribution:\s*['"`]/.test(src));
  }
  for (const f of files) {
    const rel = relative(FRONTEND, f);
    check(`${rel}: no CARTO tile URL outside lib/basemap.ts`, !readFileSync(f, 'utf8').includes('basemaps.cartocdn.com'));
  }
}

if (failures > 0) throw new Error(`basemap: ${failures} FAILURE(S)`);
console.log('\nbasemap: ALL PASS');
