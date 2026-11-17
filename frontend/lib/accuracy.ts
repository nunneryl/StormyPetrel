/**
 * The accuracy page (/accuracy): forecast against buoy, by region and lead day, from accuracy_daily
 * (pipeline/migrations/031_accuracy_daily.sql, written nightly by pipeline/accuracy_score.py).
 *
 * WHAT IS READ. The region totals for wave height only: point '' (the whole region) and measure
 * 'hs', for the WINDOW_DAYS days before today (UTC). Each row holds n and the sums of the error e
 * (forecast minus buoy, metres) and of |e|, so any run of days combines by adding:
 *     bias = sum_e / n            typical error (MAE) = sum_abs_e / n
 * the same figures, from the same sums, as the weekly report (pipeline/accuracy_report.py stats).
 *
 * WHAT IS SHOWN. A cell with fewer than MIN_PAIRS pairs shows only its count, as in the weekly
 * report (MIN_N there). Every figure carries its pairs and its first and last day.
 *
 * FRESH. The read goes through supabaseUncached: a cached read of the same URL would keep serving
 * the rows of the day it was cached (lib/dataCache.test.mts). The page is force-static and
 * revalidates daily, after the nightly scoring.
 *
 * The numbers the page states (MIN_PAIRS, PAIR_WINDOW_MINUTES, SPOT_BUOY_MAX_KM) are held to the
 * pipeline's in lib/accuracy.test.mts.
 */
import { metersToFeet } from './formatting.ts';

/** Fewer pairs than this and a cell shows only its count (accuracy_report.py MIN_N). */
export const MIN_PAIRS = 50;
/** A forecast hour pairs with the buoy reading nearest it, within this (accuracy_score.py OBS_WINDOW). */
export const PAIR_WINDOW_MINUTES = 30;
/** A spot is scored only against a buoy within this distance (accuracy_score.py SPOT_BUOY_MAX_KM). */
export const SPOT_BUOY_MAX_KM = 25;
/** The days read: the last four weeks of scored days. */
export const WINDOW_DAYS = 28;

export const LEADS = [0, 1, 2, 3] as const;
/** lead_day = floor((valid time - issue time) / 24 h) (migration 030). */
export const LEAD_LABELS: Record<number, string> = {
  0: 'Under 24 h ahead',
  1: '1 day ahead',
  2: '2 days ahead',
  3: '3 days ahead',
};

/** accuracy_daily's source: 'spot' is always kind 'spot' (the spot forecast against its buoy);
 *  'ww3' and 'nwps' are always kind 'buoy' (the model at the buoy). The source alone picks a table. */
export type SourceKey = 'spot' | 'ww3' | 'nwps';

/** accuracy_daily's region is the spots' region_hint (a state, or Puerto Rico). North to south
 *  down the East Coast, round the Gulf, then up the West Coast and out. A region not listed
 *  sorts after these, by name. */
export const REGION_ORDER = [
  'Maine', 'New Hampshire', 'Massachusetts', 'Rhode Island', 'New York', 'New Jersey', 'Delaware',
  'Maryland', 'Virginia', 'North Carolina', 'South Carolina', 'Georgia', 'Florida', 'Texas',
  'California', 'Oregon', 'Washington', 'Hawaii', 'Puerto Rico',
];

export type AccuracyRow = {
  day: string;          // YYYY-MM-DD (UTC)
  kind: string;
  source: string;
  region: string;
  lead_day: number;
  n: number;
  sum_e: number;
  sum_abs_e: number;
};

export type Cell = {
  n: number;
  first: string;        // first day with pairs, YYYY-MM-DD
  last: string;         // last day with pairs
  biasFt: number | null;    // null when n < MIN_PAIRS
  maeFt: number | null;
};

/** {region: {lead day: Cell}}, and the region 'All' summed over every region. */
export type Table = { regions: string[]; cells: Record<string, Record<number, Cell>> };

export const ALL = 'All regions';

function addTo(acc: Map<string, { n: number; e: number; abs: number; first: string; last: string }>,
  key: string, r: AccuracyRow): void {
  const s = acc.get(key) ?? { n: 0, e: 0, abs: 0, first: r.day, last: r.day };
  s.n += Number(r.n);
  s.e += Number(r.sum_e);
  s.abs += Number(r.sum_abs_e);
  if (r.day < s.first) s.first = r.day;
  if (r.day > s.last) s.last = r.day;
  acc.set(key, s);
}

function regionRank(r: string): number {
  const i = REGION_ORDER.indexOf(r);
  return i < 0 ? REGION_ORDER.length : i;
}

/** The table for one source from accuracy_daily rows (region totals, wave height). Rows of other
 *  sources are ignored. */
export function buildTable(rows: AccuracyRow[], source: SourceKey): Table {
  const acc = new Map<string, { n: number; e: number; abs: number; first: string; last: string }>();
  for (const r of rows) {
    if (r.source !== source || !(Number(r.n) > 0)) continue;
    addTo(acc, `${r.region}\u0000${r.lead_day}`, r);
    addTo(acc, `${ALL}\u0000${r.lead_day}`, r);
  }
  const cells: Table['cells'] = {};
  for (const [key, s] of acc) {
    const [region, lead] = key.split('\u0000');
    const shown = s.n >= MIN_PAIRS;
    (cells[region] ??= {})[Number(lead)] = {
      n: s.n,
      first: s.first,
      last: s.last,
      biasFt: shown ? metersToFeet(s.e / s.n) : null,
      maeFt: shown ? metersToFeet(s.abs / s.n) : null,
    };
  }
  const regions = Object.keys(cells)
    .filter((r) => r !== ALL)
    .sort((a, b) => regionRank(a) - regionRank(b) || a.localeCompare(b));
  if (cells[ALL]) regions.push(ALL);
  return { regions, cells };
}

/** '+0.3 ft', '−0.4 ft', '0.0 ft' (a bias that rounds to zero carries no sign). */
export function fmtBias(ft: number): string {
  const r = Math.round(ft * 10) / 10;
  if (r === 0) return '0.0 ft';
  return `${r > 0 ? '+' : '−'}${Math.abs(r).toFixed(1)} ft`;
}

/** 1234 -> '1,234'. */
export function fmtCount(n: number): string {
  return String(n).replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

export function fmtFt(ft: number): string {
  return `${(Math.round(ft * 10) / 10).toFixed(1)} ft`;
}

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function dayMonth(iso: string): [number, string] {
  const [, m, d] = iso.split('-').map(Number);
  return [d, MONTHS[m - 1]];
}

/** '16 Nov', '13–16 Nov', '28 Nov – 3 Dec'. */
export function fmtRange(first: string, last: string): string {
  const [d1, m1] = dayMonth(first);
  const [d2, m2] = dayMonth(last);
  if (first === last) return `${d1} ${m1}`;
  if (m1 === m2 && first.slice(0, 4) === last.slice(0, 4)) return `${d1}–${d2} ${m1}`;
  return `${d1} ${m1} – ${d2} ${m2}`;
}

/** The first day read: WINDOW_DAYS days before *today* (YYYY-MM-DD, UTC). */
export function windowStart(today: string, days = WINDOW_DAYS): string {
  const t = new Date(`${today}T00:00:00Z`);
  t.setUTCDate(t.getUTCDate() - days);
  return t.toISOString().slice(0, 10);
}

const PAGE = 1000;

/** The region wave-height totals for the WINDOW_DAYS days before today, every page of them
 *  (PostgREST returns at most 1,000 rows a request). Read past the Data Cache. */
export async function fetchAccuracyRows(): Promise<AccuracyRow[]> {
  // Imported here, not at the top, so lib/accuracy.test.mts can load this file without the
  // Supabase environment (lib/supabase.ts throws at import when it is missing).
  const { supabaseUncached } = await import('./supabase');
  const start = windowStart(new Date().toISOString().slice(0, 10));
  const out: AccuracyRow[] = [];
  for (let from = 0; ; from += PAGE) {
    const { data, error } = await supabaseUncached
      .from('accuracy_daily')
      .select('day,kind,source,region,lead_day,n,sum_e,sum_abs_e')
      .eq('point', '')
      .eq('measure', 'hs')
      .gte('day', start)
      .order('day').order('kind').order('source').order('region').order('lead_day')
      .range(from, from + PAGE - 1);
    if (error) throw new Error(`accuracy_daily: ${error.message}`);
    out.push(...((data ?? []) as AccuracyRow[]));
    if (!data || data.length < PAGE) break;
  }
  return out;
}
