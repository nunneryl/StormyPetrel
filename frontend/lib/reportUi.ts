/**
 * What the surf-report card shows: the hour picker's labels and the chips' colours.
 *
 * Kept out of lib/surfReport.ts, which is import-free on purpose (the route and its tests read
 * it); the labels need lib/spotTime.ts. Tested in lib/reportUi.test.mts.
 */
import { fmtShortTime, zoneAbbr } from './spotTime.ts';

/**
 * One hour in the picker: "11am PDT (this hour)", then "10am PDT", ... in the spot's zone.
 *
 * NO "· no forecast" SUFFIX. It used to be added for an hour missing from the page's list of
 * forecast hours, and the preview showed it on an hour the report's snapshot then found a
 * forecast for. The page is a prerendered copy (revalidated hourly, served stale until then),
 * and the list was cut off at the copy's render time, so the hour that had started since was
 * never in it. The route's snapshot reads the forecasts table at submit time, which the page
 * copy cannot reproduce, and the suffix never blocked a report anyway, so it is gone.
 */
export function hourOptionLabel(iso: string, index: number, timeZone: string): string {
  return `${fmtShortTime(iso, timeZone)} ${zoneAbbr(iso, timeZone)}${index === 0 ? ' (this hour)' : ''}`;
}

/**
 * The chips' colours (size, sets and star verdict).
 *
 * SELECTED is solid cyan-600 (#0369A1) with white text, 5.9:1, above the 4.5:1 WCAG AA asks of
 * small text. It replaced cyan-300 (#67E8F9) text on a 15% cyan-500 tint over the card, which
works out at #D2E7F3 and about 1.1:1.
 * Both colours are fixed values, not theme variables, so the pair is the same whatever the
 * viewer's system colour scheme: the site pins `color-scheme: light` (app/globals.css) and has
 * no dark variants.
 */
export const CHIP_SELECTED = 'border-cyan-600 bg-cyan-600 text-white';
export const CHIP_IDLE =
  'border-ink-600 bg-ink-900 text-text-secondary hover:border-ink-500 hover:text-text-primary';
export const CHIP_HINT_SELECTED = 'text-white';
export const CHIP_HINT_IDLE = 'text-text-muted';
