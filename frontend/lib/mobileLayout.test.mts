/**
 * The phone fixes, held where they live (launch-readiness #11, #12, #16, #19, #21 and the region
 * cards). Source checks: what each fix needs in place, so a later edit cannot quietly drop it.
 *
 *     node --experimental-strip-types frontend/lib/mobileLayout.test.mts
 */
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (rel: string) => readFileSync(join(FRONTEND, rel), 'utf8');
let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) console.log(`  PASS  ${name}`);
  else { failures += 1; console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`); }
}

// #11 /map: the visible screen's height, so the legend clears Safari's toolbar.
check('/map is dvh tall, with vh as the fallback',
  read('components/SpotMap.tsx').includes('h-[calc(100vh-3.5rem)] supports-[height:100dvh]:h-[calc(100dvh-3.5rem)]'));

// #12 the forecast table: a hint while it scrolls sideways, and headers that stick.
const grid = read('components/ForecastGrid.tsx');
check('the table scrolls both ways inside a box on a phone', grid.includes('overflow-auto max-h-[70vh] lg:max-h-none lg:overflow-visible'));
check('...says so while there is more to the right, over its header', grid.includes('<MoreToTheRight state={moreToTheRight} at="top" />') && grid.includes('useMoreToTheRight(scrollRef)'));
check('the swipe words go after the first swipe; the fade stays while there is more',
  read('components/WideScroll.tsx').includes('{!state.swiped && <div') && read('components/WideScroll.tsx').includes('if (!state.more) return null;'));
check('the column header sticks', grid.includes('sticky top-0 lg:top-14 z-20 grid'));
check('each day header sticks right under it, at its measured height',
  grid.includes('sticky top-[var(--grid-head)] lg:top-[calc(3.5rem+var(--grid-head))] z-[15]')
  && grid.includes("'--grid-head': `${headH}px`") && grid.includes('<div ref={headRef} className="sticky top-0 lg:top-14 z-20 grid'));
check('the day label stays in view when scrolled sideways', grid.includes('<span className="sticky left-3">'));
check('the outer box clips without becoming a scroll container (overflow-clip, not -hidden)',
  /rounded-xl border border-ink-600 bg-white overflow-clip shadow-card/.test(grid));

// #16 the surf-report card: every control at least 44 px tall; the cam after it on a phone.
const card = read('components/SurfReport.tsx');
const controls = (card.match(/<(button|select)\b[\s\S]*?className=\{?[`"][^`"]*[`"]/g) ?? []);
check('the card has its five controls', controls.length === 5, String(controls.length));
check('...each at least 44 px tall', controls.every((c) => c.includes('min-h-[44px]')),
  controls.filter((c) => !c.includes('min-h-[44px]')).join(' | '));
const page = read('app/spot/[slug]/page.tsx');
check('on a phone the cam comes after the tiles and the card (order-2), above them from lg (lg:order-1)',
  page.includes('<div className="order-2 lg:order-1 empty:hidden" data-cam-block>')
  && page.includes('<div className="order-1 lg:order-2 space-y-6" data-forecast-block>'));
check('...one cam section, so one iframe', (page.match(/<CamSection /g) ?? []).length === 1);
const camAt = page.indexOf('<CamSection ');
const cardAt = page.indexOf('<SurfReportPanel />');
check('the cam and the card are in their own blocks', page.indexOf('data-cam-block') < camAt
  && camAt < page.indexOf('data-forecast-block') && page.indexOf('data-forecast-block') < cardAt);

// #19 the error page in plain words; the 404 page searches.
const err = read('app/error.tsx');
check('the error page has no jargon', !/upstream|cron|database|cycle/i.test(err.slice(err.indexOf('return ('))));
check('...says it is on our side and offers the contact address', err.includes('on our side, not yours') && err.includes('{CONTACT_EMAIL}'));
check('the 404 page has a spot search', read('app/not-found.tsx').includes('<NavSearch light />'));

// #21 the Learn diagrams that drew text under 7 px at 390 px keep a readable width and scroll.
const DIAGRAMS: [string, number][] = [
  ['SeaBreezeCycle', 600], ['TideBreakerVisualizer', 600], ['BreakTypeTideWindows', 600], ['TideSessionWindow', 600],
  ['ForecastPipelineDiagram', 620], ['ForecastSkillCurve', 600], ['PointVsBay', 460],
];
for (const [name, w] of DIAGRAMS) {
  const src = read(`components/learn/${name}.tsx`);
  check(`${name} keeps at least ${w} px and scrolls on a phone`, src.includes(`<WideScroll minWidth={${w}}>`) && src.includes('</WideScroll>'));
}

// Region cards: the state name has the card's width, not cut beside the stars.
const home = read('app/(home)/page.tsx');
const cardSrc = home.slice(home.indexOf('data-region-card'), home.indexOf('</Link>', home.indexOf('data-region-card')));
check('the homepage region card does not truncate the name', cardSrc.includes('break-words') && !cardSrc.includes('truncate'));

if (failures > 0) throw new Error(`mobileLayout: ${failures} FAILURE(S)`);
console.log('\nmobileLayout: ALL PASS');
