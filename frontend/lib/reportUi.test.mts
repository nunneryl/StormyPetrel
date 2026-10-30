/**
 * Pins for lib/reportUi.ts and how components/SurfReport.tsx uses it.
 *
 *     node --experimental-strip-types frontend/lib/reportUi.test.mts
 *
 * Every expected label and colour is written out by hand: the zones from the 2026 calendar
 * (Pacific daylight time until 1 November, Hawaii on standard time all year), the colours from
 * tailwind.config.js, and the contrast ratios worked from the WCAG 2 relative-luminance formula.
 */
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  CHIP_HINT_IDLE,
  CHIP_HINT_SELECTED,
  CHIP_IDLE,
  CHIP_SELECTED,
  hourOptionLabel,
} from './reportUi.ts';

const HERE = dirname(fileURLToPath(import.meta.url));
const FRONTEND = join(HERE, '..');
const read = (rel: string) => readFileSync(join(FRONTEND, rel), 'utf8');

let failures = 0;

function eq<T>(name: string, got: T, want: T): void {
  const ok = JSON.stringify(got) === JSON.stringify(want);
  if (!ok) failures += 1;
  console.log(`  ${ok ? 'PASS' : 'FAIL'}  ${name}${ok ? '' : `  — got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`}`);
}

// --------------------------------------------------------------------------- //
// 1. The hour picker's labels: the spot's zone, "(this hour)" first, no suffix   //
// --------------------------------------------------------------------------- //

console.log('\n1. Hour labels');
eq('the preview\'s hour at Steamer Lane: 18:00Z is "11am PDT (this hour)"',
  hourOptionLabel('2026-10-30T18:00:00.000Z', 0, 'America/Los_Angeles'), '11am PDT (this hour)');
eq('the hour before has no "(this hour)"',
  hourOptionLabel('2026-10-30T17:00:00.000Z', 1, 'America/Los_Angeles'), '10am PDT');
eq('Hawaii, same instant', hourOptionLabel('2026-10-30T18:00:00.000Z', 0, 'Pacific/Honolulu'),
  '8am HST (this hour)');
eq('the East Coast after the clocks change', hourOptionLabel('2026-11-02T15:00:00.000Z', 3, 'America/New_York'),
  '10am EST');

const card = read('components/SurfReport.tsx');
eq('the card labels each hour with hourOptionLabel', card.includes('{hourOptionLabel(iso, i, timeZone)}'), true);
eq('no "no forecast" anywhere in the card', card.includes('no forecast'), false);
eq('the card takes no forecast-hour list', card.includes('forecastHours'), false);
eq('the page passes none', read('app/spot/[slug]/page.tsx').includes('forecastHours'), false);

// --------------------------------------------------------------------------- //
// 2. The Hawaii note is gone                                                     //
// --------------------------------------------------------------------------- //

console.log('\n2. No Hawaiian-scale note');
eq('the card says nothing about the Hawaiian scale', card.includes('Hawaiian scale'), false);
eq('the card takes no hawaii flag', /\bhawaii\b/.test(card), false);
eq('the page passes none', read('app/spot/[slug]/page.tsx').includes('hawaii='), false);

// --------------------------------------------------------------------------- //
// 3. The selected chip is readable                                               //
// --------------------------------------------------------------------------- //

console.log('\n3. Chip colours');
eq('selected: solid cyan-600 with white text', CHIP_SELECTED, 'border-cyan-600 bg-cyan-600 text-white');
eq('selected hint: white', CHIP_HINT_SELECTED, 'text-white');
eq('idle chip unchanged', CHIP_IDLE,
  'border-ink-600 bg-ink-900 text-text-secondary hover:border-ink-500 hover:text-text-primary');
eq('idle hint unchanged (the render test reads it off the served page)', CHIP_HINT_IDLE, 'text-text-muted');

const require = createRequire(import.meta.url);
const tw = require(join(FRONTEND, 'tailwind.config.js'));
eq('cyan-600 is #0369A1', tw.theme.extend.colors.cyan['600'], '#0369A1');

/** WCAG 2 contrast ratio of two #RRGGBB colours. */
function contrast(a: string, b: string): number {
  const lum = (hex: string) => {
    const [r, g, bl] = [1, 3, 5].map((i) => {
      const c = parseInt(hex.slice(i, i + 2), 16) / 255;
      return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r + 0.7152 * g + 0.0722 * bl;
  };
  const [hi, lo] = [lum(a), lum(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}
// Worked by hand: #0369A1 has relative luminance 0.1267, white 1.0, so (1.05)/(0.1767) = 5.93.
eq('white on #0369A1 is 5.93:1', Math.round(contrast('#FFFFFF', '#0369A1') * 100) / 100, 5.93);
eq('which clears WCAG AA for small text (4.5:1)', contrast('#FFFFFF', '#0369A1') >= 4.5, true);
// The old pair: cyan-300 #67E8F9 on a 15% tint of #0284C7 over the card (#F7F9FB) = #D2E7F3.
eq('the old pair was 1.14:1', Math.round(contrast('#67E8F9', '#D2E7F3') * 100) / 100, 1.14);

// Both chip groups (size and sets share Chips; the star verdict has its own) use the constants.
eq('both selected states use CHIP_SELECTED', card.split('? CHIP_SELECTED : CHIP_IDLE').length - 1, 2);
eq('no pale cyan text left in the card', /text-cyan-300|bg-cyan-500\/15/.test(card), false);

// The same pair in light and dark mode: fixed colours, and a site pinned to light.
eq('no dark: variant on the chips', /dark:/.test(CHIP_SELECTED + CHIP_HINT_SELECTED), false);
const css = read('app/globals.css');
eq('the site pins color-scheme: light', css.includes('color-scheme: light;'), true);
eq('and has no dark-scheme override', css.includes('prefers-color-scheme: dark'), false);

if (failures > 0) {
  throw new Error(`reportUi: ${failures} FAILURE(S)`);
}
console.log('\nreportUi: ALL PASS');
