/**
 * THE BRAND IMAGES A PAGE LOADS: the nav monogram on every page and the homepage logo, plus the
 * app icons. Both logos used to be the full artwork served past the image optimizer
 * (`unoptimized`): the nav's 479x581, 242,749-byte PNG at 32 px tall on every page, and the
 * homepage's 646x556, 431,549-byte PNG at 60-120 px. This holds them to 2x their shown size,
 * through the optimizer, with their space reserved, and the icons to the sizes they declare.
 *
 * Every expected value is written out by hand; the sizes are read from the PNG headers.
 *
 *     node --experimental-strip-types frontend/lib/brandImages.test.mts
 */
import { readFileSync, statSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (rel: string) => readFileSync(join(FRONTEND, rel), 'utf8');

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) console.log(`  PASS  ${name}`);
  else { failures += 1; console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`); }
}

/** Width, height and colour type from a PNG's IHDR chunk. Colour type 2 is RGB (opaque), 6 RGBA. */
function png(rel: string): { w: number; h: number; colourType: number; bytes: number } {
  const b = readFileSync(join(FRONTEND, rel));
  const sig = b.subarray(0, 8).toString('hex');
  if (sig !== '89504e470d0a1a0a' || b.subarray(12, 16).toString('latin1') !== 'IHDR') {
    throw new Error(`${rel} is not a PNG`);
  }
  return { w: b.readUInt32BE(16), h: b.readUInt32BE(20), colourType: b[25], bytes: statSync(join(FRONTEND, rel)).size };
}

// 1. The two logos: 2x the shown height, the artwork's aspect, a byte budget.
const nav = png('public/brand/sp-initials-nav.png');
check('nav monogram is 53x64: 2x the 32 px it is shown at', nav.w === 53 && nav.h === 64, `${nav.w}x${nav.h}`);
check('nav monogram under 10 KB', nav.bytes < 10_000, String(nav.bytes));
const home = png('public/brand/logo-home.png');
check('homepage logo is 279x240: 2x the 120 px it is shown at on desktop',
  home.w === 279 && home.h === 240, `${home.w}x${home.h}`);
check('homepage logo under 120 KB', home.bytes < 120_000, String(home.bytes));

// 2. Where they are used: through the optimizer, with the shown size as width/height.
const navSrc = read('components/SiteNav.tsx');
const homeSrc = read('app/page.tsx');
check('the nav uses the 2x monogram', navSrc.includes('src="/brand/sp-initials-nav.png"'));
check('...at 26x32, its shown size (53x64 halved)', /width=\{26\}\s+height=\{32\}/.test(navSrc));
check('the homepage uses the 2x logo', homeSrc.includes('src="/brand/logo-home.png"'));
check('...at 139x120, its desktop size (279x240 halved)', /width=\{139\}\s+height=\{120\}/.test(homeSrc));
for (const [label, src] of [['components/SiteNav.tsx', navSrc], ['app/page.tsx', homeSrc]] as const) {
  check(`${label}: no image skips the optimizer`, !/\bunoptimized\b/.test(src));
  check(`${label}: the full-size artwork is not loaded`,
    !src.includes('"/logo.png"') && !src.includes('"/brand/sp-initials.png"'));
}

// 3. The app icons: their declared sizes, and opaque (iOS fills a transparent icon with black).
const apple = png('app/apple-icon.png');
check('apple-icon is 180x180', apple.w === 180 && apple.h === 180, `${apple.w}x${apple.h}`);
check('apple-icon is opaque RGB', apple.colourType === 2, String(apple.colourType));
const manifestSrc = read('app/manifest.ts');
for (const [rel, size] of [['public/icons/icon-192.png', 192], ['public/icons/icon-512.png', 512],
  ['public/icons/icon-maskable-512.png', 512]] as const) {
  const p = png(rel);
  check(`${rel} is ${size}x${size}`, p.w === size && p.h === size, `${p.w}x${p.h}`);
  check(`${rel} is opaque RGB`, p.colourType === 2, String(p.colourType));
  const src = rel.replace('public', '');
  check(`the manifest lists ${src} as ${size}x${size}`,
    manifestSrc.includes(`src: '${src}', sizes: '${size}x${size}'`));
}
check('the maskable icon is marked maskable', /icon-maskable-512\.png'[^}]*purpose: 'maskable'/.test(manifestSrc));

if (failures > 0) throw new Error(`brandImages: ${failures} FAILURE(S)`);
console.log('\nbrandImages: ALL PASS');
