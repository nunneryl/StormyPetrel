/**
 * The swell compass (lib/swellWindowCompass.ts, components/SwellWindowCompass.tsx) inside the Swell
 * breakdown card (components/SwellPartitions.tsx): the window band, inside or outside the window,
 * arrow widths and labels, the row colours the chips and arrows share, labels that never overlap,
 * and the wiring that keeps the compass on the breakdown's own rows.
 *
 * NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST: every point, path, width, label and colour
 * below is worked by hand, with sin 45° = cos 45° = 0.70711, sin 5° = 0.08716, cos 5° = 0.99619,
 * sin 30° = 0.5, cos 30° = 0.86603, and 1 m = 3.28084 ft.
 *
 *     node --experimental-strip-types frontend/lib/swellWindowCompass.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  CX, CY, LABEL_H, LABEL_R, MIN_ARROW_W, MAX_ARROW_W, NO_WINDOW_NOTE, RING_R, RING_W, VIEW_H,
  VIEW_W, arcSector, arrowLabel, arrowWidth, boxesOverlap, compassArrows, compassPoint,
  insideWindow, layoutLabels, ringArcPath, rowStyles, type LabelIn,
} from './swellWindowCompass.ts';
import { breakdownRows, type BreakdownRow } from './swellBreakdown.ts';

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}
const near = (a: number, b: number, tol = 1e-3) => Math.abs(a - b) < tol;
const FRONTEND = dirname(dirname(fileURLToPath(import.meta.url)));
const read = (rel: string) => readFileSync(join(FRONTEND, rel), 'utf8');

// 1. Bearings, north up: radius 10 about (50, 50).
const pt = (b: number) => compassPoint(b, 10, 50, 50);
check('0° (N) is straight up: (50, 40)', near(pt(0).x, 50) && near(pt(0).y, 40));
check('90° (E) is right: (60, 50)', near(pt(90).x, 60) && near(pt(90).y, 50));
check('180° (S) is down: (50, 60)', near(pt(180).x, 50) && near(pt(180).y, 60));
check('270° (W) is left: (40, 50)', near(pt(270).x, 40) && near(pt(270).y, 50));
check('45° (NE): (57.0711, 42.9289)', near(pt(45).x, 57.0711) && near(pt(45).y, 42.9289));

// 2. The window band: arcs along the ring, never a slice from the centre.
check('315°–90° crosses north: 135° from 315°',
  JSON.stringify(arcSector({ min: 315, max: 90 })) === JSON.stringify({ start: 315, sweep: 135, full: false }));
check('band 315°–90°', ringArcPath({ min: 315, max: 90 }, 10, 50, 50) ===
  'M 42.929 42.929 A 10 10 0 0 1 60 50', ringArcPath({ min: 315, max: 90 }, 10, 50, 50));
// 180 to 350, span 180: pad (180 - 170) / 2 = 5, so 175° to 355°; exactly 180 is the small arc.
check('band 180°–350° with span 180, padded to 175°–355°', ringArcPath({ min: 180, max: 350, span: 180 }, 10, 50, 50) ===
  'M 50.872 59.962 A 10 10 0 0 1 49.128 40.038');
check('band 90°–330°: the large arc', ringArcPath({ min: 90, max: 330 }, 10, 50, 50) ===
  'M 60 50 A 10 10 0 1 1 45 41.34');
check('an arc of 360° is the whole ring', ringArcPath({ min: 0, max: 350, span: 360 }, 10, 50, 50) ===
  'M 50 40 A 10 10 0 1 1 50 60 A 10 10 0 1 1 50 40');
check('no band path draws to the centre', !ringArcPath({ min: 315, max: 90 }, 10, 50, 50).includes(' L '));

// 3. Inside or outside. Kanaha: 315–90, span 135 (pad 0). Rincon: 145–192 span 51 (pad 2, so
//    143–194) and 228–267 span 43 (pad 2, so 226–269).
const KANAHA = [{ min: 315, max: 90, span: 135 }];
const RINCON = [{ min: 145, max: 192, span: 51 }, { min: 228, max: 267, span: 43 }];
check('Kanaha: 340° inside, 90° (the edge) inside, 120° outside',
  insideWindow(340, KANAHA) && insideWindow(90, KANAHA) && !insideWindow(120, KANAHA));
check('Rincon: 193° inside (padded), 200° between the arcs outside, 250° inside, 270° outside',
  insideWindow(193, RINCON) && !insideWindow(200, RINCON) && insideWindow(250, RINCON) &&
  !insideWindow(270, RINCON));
check('no window set: nothing is outside', insideWindow(120, null) && insideWindow(120, []));

// 4. Width follows height: 10 for the biggest, in proportion, at least 3.
check('widths run from 3 to 10', MIN_ARROW_W === 3 && MAX_ARROW_W === 10);
check('2.0 of 2.0 m: 10; 1.0: 5; 0.7: 3.5; 0.6: 3 (exactly the minimum); 0.2: 3, not 1',
  arrowWidth(2.0, 2.0) === 10 && arrowWidth(1.0, 2.0) === 5 && near(arrowWidth(0.7, 2.0), 3.5) &&
  near(arrowWidth(0.6, 2.0), 3) && arrowWidth(0.2, 2.0) === 3);

// 5. Labels: 2.6822 m = 8.7999 ft; 1.0 m = 3.2808 ft.
check("'8.8 ft · 14 s'", arrowLabel(2.6822, 14) === '8.8 ft · 14 s', arrowLabel(2.6822, 14));
check("'3.3 ft · 9 s' (9.4 s rounds to 9)", arrowLabel(1.0, 9.4) === '3.3 ft · 9 s');
check("no period: '3.3 ft'", arrowLabel(1.0, null) === '3.3 ft');

// 6. Colours: the chip and the arrow share one, by the breakdown's order. Kanaha's hour: 2.4 m from
//    340 (inside: the first blue), 0.9 m from 120 (outside: the first grey), a 0.15 m wind sea
//    from 70 (inside: the second blue), and a row with no direction (no colour, no arrow).
const row = (label: string, hs: number, tp: number, dp: number | null, windSea = false): BreakdownRow =>
  ({ label, hs, tp, dp, windSea, share: 0, muted: windSea });
const KROWS = [
  row('P1 (primary)', 2.4, 13, 340), row('P2 (secondary)', 0.9, 10, 120),
  row('Wind sea', 0.15, 5, 70, true), row('P3 (tertiary)', 0.1, 7, null),
];
check('chips: dark blue, slate, mid blue, none', JSON.stringify(rowStyles(KROWS, KANAHA).map((s) => s.colour)) ===
  JSON.stringify(['#075985', '#475569', '#0284C7', null]));
const karrows = compassArrows(KROWS, KANAHA);
// Widths: 10; 10 x 0.9 / 2.4 = 3.75; 10 x 0.15 / 2.4 = 0.625, so 3. Labels: 2.4 m = 7.874 ft,
// 0.9 m = 2.953 ft, 0.15 m = 0.492 ft.
check('arrows: P1, P2, the wind sea; none for the row with no direction',
  JSON.stringify(karrows.map((a) => a.label)) === JSON.stringify(['P1 (primary)', 'P2 (secondary)', 'Wind sea']));
check('arrow colours are the chips', JSON.stringify(karrows.map((a) => a.colour)) ===
  JSON.stringify(['#075985', '#475569', '#0284C7']));
check('arrow widths 10, 3.75, 3', JSON.stringify(karrows.map((a) => a.width)) === JSON.stringify([10, 3.75, 3]),
  JSON.stringify(karrows.map((a) => a.width)));
check("labels '7.9 ft · 13 s', '3.0 ft · 10 s', '0.5 ft · 5 s'",
  JSON.stringify(karrows.map((a) => a.text)) ===
    JSON.stringify(['7.9 ft · 13 s', '3.0 ft · 10 s', '0.5 ft · 5 s']));
check('inside, outside, inside', JSON.stringify(karrows.map((a) => a.inside)) === JSON.stringify([true, false, true]));
check("the wind sea keeps the breakdown's grey-out", karrows[2].muted && !karrows[0].muted);

// 7. From an hour: the rows breakdownRows makes, in its order.
const hour = {
  swell_source: 'ww3', swell_tp: 14, swell_dp: 290,
  swell_1_hs: 2.0, swell_1_tp: 14, swell_1_dp: 290,
  swell_2_hs: 1.0, swell_2_tp: 9, swell_2_dp: 200,
  swell_3_hs: null, swell_3_tp: null, swell_3_dp: null,
  wind_wave_hs: 0.2, wind_wave_tp: 4, wind_wave_dp: 60, chop_ratio: 0.1,
};
const fromHour = compassArrows(breakdownRows(hour, [{ min: 180, max: 350 }], 270), [{ min: 180, max: 350 }]);
check('an hour: P1 from 290 (blue), P2 from 200 (blue), the wind sea from 60 (grey, outside)',
  JSON.stringify(fromHour.map((a) => [a.label, a.dp, a.colour])) === JSON.stringify([
    ['P1 (primary)', 290, '#075985'], ['P2 (secondary)', 200, '#0284C7'], ['Wind sea', 60, '#475569']]),
  JSON.stringify(fromHour.map((a) => [a.label, a.dp, a.colour])));

// 8. Labels never overlap. Geometry: centre (210, 120), labels on a circle of 110, 14 high, 16 apart.
check('the geometry: 420 x 240, centre (210, 120), ring 82 wide 10, labels at 110',
  VIEW_W === 420 && VIEW_H === 240 && CX === 210 && CY === 120 && RING_R === 82 && RING_W === 10 &&
  LABEL_R === 110 && LABEL_H === 14);
const lab = (id: string, bearing: number, text = '8.8 ft · 14 s'): LabelIn => ({ id, bearing, text });
const noOverlap = (ls: ReturnType<typeof layoutLabels>) =>
  ls.every((a, i) => ls.every((b, j) => j <= i || !boxesOverlap(a.box, b.box)));
// Two swells and the wind within 20°: 250, 262 and 268, all on the left. Their heights on the circle:
// 120 - 110 cos b = 157.62, 135.31, 123.84. Top down: 268 stays at 123.84; 262 is pushed to
// 123.84 + 16 = 139.84 (moved, so it gets a leader line); 250 stays at 157.62 (139.84 + 16 =
// 155.84 is above it).
const close = layoutLabels([lab('a', 250), lab('b', 262), lab('wind', 268, 'W 9 mph')]);
const by = new Map(close.map((l) => [l.id, l]));
check('two swells and the wind within 20°: no two labels overlap', noOverlap(close));
check('… heights 157.62, 139.84, 123.84', near(by.get('a')!.y, 157.62, 0.01) &&
  near(by.get('b')!.y, 139.84, 0.01) && near(by.get('wind')!.y, 123.84, 0.01),
  close.map((l) => `${l.id} ${l.y.toFixed(2)}`).join(', '));
check('… only the middle one moved, so only it gets a leader line',
  !by.get('a')!.moved && by.get('b')!.moved && !by.get('wind')!.moved);
check('… all on the left, written leftwards from the circle',
  close.every((l) => l.anchor === 'end' && l.box.x1 === l.x && near(Math.hypot(l.x - CX, l.y - CY), LABEL_R)));
// Three on one bearing (90): 120, 136, 152.
const same = layoutLabels([lab('p', 90), lab('q', 90), lab('r', 90)]);
check('three on the same bearing: 120, 136, 152, no overlap', noOverlap(same) &&
  JSON.stringify(same.map((l) => Math.round(l.y))) === JSON.stringify([120, 136, 152]));
// Three near the bottom (175, 178, 179 on the right): 229.58, 229.93, 229.98 on the circle; pushed
// down they would leave the drawing (bottom 240 - 7 = 233), so they stack up from it: 201, 217, 233.
const low = layoutLabels([lab('x', 175), lab('y', 178), lab('z', 179)]);
check('three at the bottom stack up from the edge: 201, 217, 233', noOverlap(low) &&
  JSON.stringify(low.map((l) => Math.round(l.y))) === JSON.stringify([201, 217, 233]),
  low.map((l) => l.y.toFixed(2)).join(', '));
// Either side of north (5° and 355°): one writes right from x = 210 + 9.59, the other left from 210 - 9.59.
const north = layoutLabels([lab('e', 5), lab('w', 355)]);
check('either side of north: no overlap', noOverlap(north) && north[0].anchor !== north[1].anchor);
// Every pair of swell bearings 0-359 in steps of 10, with the wind 10° from the first: none overlap,
// and every label stays inside the drawing.
let sweepOk = true;
let inside = true;
for (let a = 0; a < 360; a += 10) {
  for (let b = 0; b < 360; b += 10) {
    const ls = layoutLabels([lab('s1', a), lab('s2', b, '12.5 ft · 17 s'), lab('wind', a + 10, 'WSW 12 mph'),
      lab('best', b + 5, 'best')]);
    if (!noOverlap(ls)) sweepOk = false;
    if (!ls.every((l) => l.box.x0 >= 0 && l.box.x1 <= VIEW_W && l.box.y0 >= 0 && l.box.y1 <= VIEW_H)) inside = false;
  }
}
check('every pair of swells, with the wind and "best" near them: no overlap', sweepOk);
check('… and every label inside the drawing', inside);
check('boxesOverlap: touching edges do not overlap, crossing ones do',
  !boxesOverlap({ x0: 0, x1: 10, y0: 0, y1: 10 }, { x0: 10, x1: 20, y0: 0, y1: 10 }) &&
  boxesOverlap({ x0: 0, x1: 10, y0: 0, y1: 10 }, { x0: 9, x1: 20, y0: 9, y1: 20 }));

// 9. The wiring: one card, the breakdown's rows, the tiles' hour and the Wind tile's colours.
const card = read('components/SwellPartitions.tsx');
const comp = read('components/SwellWindowCompass.tsx');
check('the card takes the hour the tiles show', card.includes('const forecast = useCurrentHour().selection.row;'));
check('the card makes its rows once, with the window and facing',
  (card.match(/breakdownRows\(/g) ?? []).length === 1 &&
  card.includes('const all = breakdownRows(forecast, windowArcs, facingDeg);'));
check('the compass draws those rows', card.includes('rows={all}') &&
  comp.includes('const arrows = compassArrows(rows, arcs);') && !comp.includes('breakdownRows('));
check('the chips are rowStyles of the same rows', card.includes('const styles = rowStyles(all, windowArcs);') &&
  card.includes('const chip = styles[i].colour;'));
check('the compass reads no wave column itself', !/swell_[123]_|wind_wave_|chop_ratio|\.hs\b/.test(comp));
check("the wind is the hour's wind_dir and wind_speed",
  card.includes('windDir={forecast.wind_dir}') && card.includes('windSpeed={forecast.wind_speed}'));
check("the wind's colour is the Wind tile's: windQualityClass(classifyWind(dir, offshore))",
  comp.includes('windQualityClass(classifyWind(dir, offshore))') &&
  read('components/CurrentConditions.tsx').includes('classifyWind(current?.wind_dir ?? null, offshoreDeg ?? null)') &&
  read('components/CurrentConditions.tsx').includes('windQualityClass(wQ)'));
check('no P1/P2 on the compass: its labels are the height and period',
  comp.includes("text: a.text }))") && !/'P[123]'/.test(comp));
check('no legend paragraph and no legend list',
  !/Arrows point|<ul[\s>]|<li[\s>]/.test(comp + card));
check(`no window: a dashed ring and "${NO_WINDOW_NOTE}"`, NO_WINDOW_NOTE === 'No swell window set' &&
  comp.includes('data-compass="no-window"') && card.includes('{NO_WINDOW_NOTE}'));
check('the beach is turned to the facing', comp.includes('transform={`rotate(${facingDeg} ${CX} ${CY})`}'));
const page = read('app/spot/[slug]/page.tsx');
const provider = page.slice(page.indexOf('<CurrentHourProvider'), page.indexOf('</CurrentHourProvider>'));
check('the spot page has one card, inside the current-hour provider, with the spot\'s fields',
  provider.includes('<SwellPartitions') && !page.includes('<SwellWindowCompass') &&
  provider.includes('optimalDeg={spot.optimal_swell_dir}') && provider.includes('offshoreDeg={spot.offshore_wind_deg}'));

if (failures) {
  console.log(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log('\nswellWindowCompass: ALL PASS');
