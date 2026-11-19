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
import { readFileSync, readdirSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  CX, CY, HEAD_R, arrowTipR, firstLandHit, parseLandPath, INSIDE_COLOUR, OUTSIDE_COLOUR, STUB_OUTER_R, STUB_TIP_R, WIDTH_PER_FT, LAND_KM, LAND_R, LABEL_H, LABEL_R, PATH_UNITS, PIN_R, landTransform, MIN_ARROW_W, MAX_ARROW_W, NO_WINDOW_NOTE, RING_R, RING_W, VIEW_H,
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

// 4. Width on one feet scale for every spot: 0.8 a foot, from 2 to 12.
//    2.4 m = 7.874 ft: 6.2992. 1.0 m = 3.2808 ft: 2.6247. 0.762 m = 2.5000 ft: 2.0000, exactly the
//    minimum. 0.15 m = 0.49 ft: 0.39, so 2. 5.0 m = 16.40 ft: 13.12, so 12.
check('the scale: 0.8 units a foot, 2 to 12', WIDTH_PER_FT === 0.8 && MIN_ARROW_W === 2 && MAX_ARROW_W === 12);
check('2.4 m: 6.2992; 1.0 m: 2.6247; 0.762 m: 2.0', near(arrowWidth(2.4), 6.2992) &&
  near(arrowWidth(1.0), 2.6247) && near(arrowWidth(0.762), 2.0));
check('0.15 m: the minimum 2; 5.0 m: the maximum 12', arrowWidth(0.15) === 2 && arrowWidth(5.0) === 12);
// A flat day looks flat: a 1.5 m swell (4.92 ft, width 3.937) is as wide alone as beside a 4 m one.
const r15 = (extra: BreakdownRow[]) => compassArrows([...extra,
  { label: 'Secondary swell', hs: 1.5, tp: 10, dp: 0, windSea: false, share: 0, muted: false }], null)
  .find((a) => a.label === 'Secondary swell')!.width;
check('a 1.5 m swell is 3.937 wide whether it is the biggest or not',
  near(r15([]), 3.937) && near(r15([{ label: 'Primary swell', hs: 4, tp: 16, dp: 10, windSea: false,
    share: 0, muted: false }]), 3.937));

// 5. Labels: 2.6822 m = 8.7999 ft; 1.0 m = 3.2808 ft.
check("'8.8 ft · 14 s'", arrowLabel(2.6822, 14) === '8.8 ft · 14 s', arrowLabel(2.6822, 14));
check("'3.3 ft · 9 s' (9.4 s rounds to 9)", arrowLabel(1.0, 9.4) === '3.3 ft · 9 s');
check("no period: '3.3 ft'", arrowLabel(1.0, null) === '3.3 ft');

// 6. One colour rule for marks and chips: blue inside the window, grey outside. Kanaha's hour:
//    2.4 m from 340 (inside: blue arrow), 0.9 m from 120 (outside: grey stub), a 0.15 m wind sea
//    from 70 (inside: blue arrow), and a row with no direction (no colour, no mark).
const row = (label: string, hs: number, tp: number, dp: number | null, windSea = false): BreakdownRow =>
  ({ label, hs, tp, dp, windSea, share: 0, muted: windSea });
const KROWS = [
  row('Primary swell', 2.4, 13, 340), row('Secondary swell', 0.9, 10, 120),
  row('Wind sea', 0.15, 5, 70, true), row('Tertiary swell', 0.1, 7, null),
];
check('the two colours', INSIDE_COLOUR === '#0284C7' && OUTSIDE_COLOUR === '#94A3B8');
check('chips: blue, grey, blue, none', JSON.stringify(rowStyles(KROWS, KANAHA).map((s) => s.colour)) ===
  JSON.stringify(['#0284C7', '#94A3B8', '#0284C7', null]));
const karrows = compassArrows(KROWS, KANAHA);
// Widths: 2.4 m 6.2992; 0.9 m = 2.953 ft, 2.3622; 0.15 m, the minimum 2. Labels: 2.4 m = 7.874 ft,
// 0.9 m = 2.953 ft, 0.15 m = 0.492 ft.
check('arrows: P1, P2, the wind sea; none for the row with no direction',
  JSON.stringify(karrows.map((a) => a.label)) === JSON.stringify(['Primary swell', 'Secondary swell', 'Wind sea']));
check('mark colours are the chips', JSON.stringify(karrows.map((a) => a.colour)) ===
  JSON.stringify(['#0284C7', '#94A3B8', '#0284C7']));
check('widths 6.2992, 2.3622, 2', near(karrows[0].width, 6.2992) && near(karrows[1].width, 2.3622) &&
  karrows[2].width === 2, JSON.stringify(karrows.map((a) => a.width)));
check('only the swell from outside the window (120) is a stub',
  JSON.stringify(karrows.map((a) => a.stub)) === JSON.stringify([false, true, false]));
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
check('an hour: primary from 290 (blue), secondary from 200 (blue), the wind sea from 60 (grey)',
  JSON.stringify(fromHour.map((a) => [a.label, a.dp, a.colour])) === JSON.stringify([
    ['Primary swell', 290, '#0284C7'], ['Secondary swell', 200, '#0284C7'], ['Wind sea', 60, '#94A3B8']]),
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
const page = read('app/spot/[slug]/page.tsx');
const comp = read('components/SwellWindowCompass.tsx');
check('the card takes the hour the tiles show', card.includes('const forecast = useCurrentHour().selection.row;'));
check('the card makes its rows once, with the window and facing',
  (card.match(/breakdownRows\(/g) ?? []).length === 1 &&
  card.includes('const all = breakdownRows(forecast, windowArcs, facingDeg);'));
check('the compass draws those rows', card.includes('rows={all}') &&
  comp.includes('const arrows = compassArrows(rows, arcs, coastPath);') && !comp.includes('breakdownRows('));
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
check('the land is the stored path, scaled into the ring, with a pin at the spot',
  comp.includes('<path d={coastPath} transform={landTransform()}') && comp.includes('data-compass="pin"') &&
  !comp.includes('rotate('));
// 25 km is 100 path units; the land fills the ring's inside: 82 - 10 / 2 - 1 = 76, so scale 0.76.
check('landTransform: translate to the centre, scale 76 / 100',
  landTransform() === 'translate(210 120) scale(0.76)' && LAND_R === 76 && PATH_UNITS === 100 && LAND_KM === 25);
check('arrows end by the pin: 4 + 8 = 12 from the centre', PIN_R === 4 && HEAD_R === 12);
// A stub runs from 82 + 5 + 2 + 6 = 95 to the band's inner edge, 82 - 5 = 77: it never reaches the
// land, drawn out to 76.
check('a stub spans 95 to 77, outside the land (76)', STUB_OUTER_R === 95 && STUB_TIP_R === 77 && STUB_TIP_R > LAND_R);
check('the compass draws short marks from the stub radii, tips where arrowTipR says',
  comp.includes('const short = a.stub || a.tipR >= STUB_TIP_R;') &&
  comp.includes('short ? STUB_OUTER_R : TAIL_R') && comp.includes('const tipR = a.tipR;') &&
  comp.includes('compassArrows(rows, arcs, coastPath)'));

// Arrows stop at the first land (#39). A square of land east of the spot, x 20-60, y -10 to 10
// (path units, y down): "M20 -10l40 0 0 20-40 0z".
const SQUARE = 'M20 -10l40 0 0 20-40 0z';
check('parseLandPath reads M then relative l to absolute points',
  JSON.stringify(parseLandPath(SQUARE)) === JSON.stringify([[[20, -10], [60, -10], [60, 10], [20, 10]]]));
check('two rings, negative numbers run together', JSON.stringify(parseLandPath('M1 2l3-4z M-5 -6l1 1z')) ===
  JSON.stringify([[[1, 2], [4, -2]], [[-5, -6], [-4, -5]]]));
const sq = parseLandPath(SQUARE);
// From the east (90): the way in crosses x = 60 first, 60 units out.
check('from 90: first land 60 out', near(firstLandHit(90, sq)!, 60));
// From the north (0): straight down x = 0, no land.
check('from 0: no land', firstLandHit(0, sq) === null);
// From 80: u = (sin 80, -cos 80) = (0.98481, -0.17365). It meets the top edge y = -10 at
// t = 10 / 0.17365 = 57.5877 (x = 56.71, inside 20-60) before x = 60 (t = 60.93, y = -10.58, off).
check('from 80: first land 57.5877 out, through the top edge', near(firstLandHit(80, sq)!, 57.5877));
// A way in that starts on land: a square at the top edge, x -10 to 10, y -110 to -90.
check('from 0 with land at the ring: 100 (starts on land)',
  firstLandHit(0, parseLandPath('M-10 -110l20 0 0 20-20 0z')) === 100);
// The tip: 60 path units is 60 x 0.76 = 45.6 drawing units, plus 1: 46.6. Starting on land:
// 76 + 1 = 77, the band's inner edge. Land within the pin's 12 (10 x 0.76 + 1 = 8.6): 12.
check('arrowTipR: over water 12; land 60 out 46.6; on land 77; the own beach 12; a stub 77',
  arrowTipR(false, null) === 12 && near(arrowTipR(false, 60), 46.6) && arrowTipR(false, 100) === 77 &&
  arrowTipR(false, 10) === 12 && arrowTipR(true, null) === 77 && arrowTipR(true, 60) === 77);
const landRow = (dp: number): BreakdownRow =>
  ({ label: 'Wind sea', hs: 0.15, tp: 5, dp, windSea: true, share: 0, muted: true });
check('compassArrows stops an in-window arrow at the land, and only with a coast',
  near(compassArrows([landRow(90)], null, SQUARE)[0].tipR, 46.6) &&
  compassArrows([landRow(0)], null, SQUARE)[0].tipR === 12 &&
  compassArrows([landRow(90)], null, null)[0].tipR === 12);
check('the card passes the server-read path down', card.includes('coastPath={coastPath}'));
check("the spot page reads the spot's path server-side",
  page.includes("import { coastlinePath } from '@/lib/coastline';") &&
  page.includes('coastPath={coastlinePath(spot.slug)}') && !page.startsWith("'use client'"));
// The whole file (every spot) must never reach the browser: no client module may import it.
const walkFiles = (dir: string): string[] => readdirSync(join(FRONTEND, dir), { withFileTypes: true })
  .flatMap((e) => e.isDirectory() ? walkFiles(join(dir, e.name)) : [join(dir, e.name)]);
const clientImporters = [...walkFiles('app'), ...walkFiles('components'), ...walkFiles('lib')]
  .filter((f) => /\.tsx?$/.test(f))
  .filter((f) => /^['"]use client['"]/.test(read(f)))
  .filter((f) => /lib\/coastline'|coastlines\.json/.test(read(f)));
check('no client module imports lib/coastline or coastlines.json', clientImporters.length === 0,
  clientImporters.join(', '));
const provider = page.slice(page.indexOf('<CurrentHourProvider'), page.indexOf('</CurrentHourProvider>'));
check('the spot page has one card, inside the current-hour provider, with the spot\'s fields',
  provider.includes('<SwellPartitions') && !page.includes('<SwellWindowCompass') &&
  provider.includes('optimalDeg={spot.optimal_swell_dir}') && provider.includes('offshoreDeg={spot.offshore_wind_deg}'));

if (failures) {
  console.log(`\n${failures} check(s) failed`);
  process.exit(1);
}
console.log('\nswellWindowCompass: ALL PASS');
