import { supabase } from './supabase';

export type ReportTrend = 'building' | 'steady' | 'fading';

export type ReportTopSpot = {
  name: string;
  slug: string;
  state: string | null;
  stars: number | null;
  face_ft: number | null;
};

export type DailyReport = {
  region: string;
  region_label: string;
  report_date: string;       // YYYY-MM-DD
  summary: string;
  top_spots: ReportTopSpot[];
  trend: ReportTrend;
  generated_at: string;      // ISO timestamp
};

// Canonical region order used for both the homepage rail and the
// /reports list. East coast first, then south, then west, then
// outlying — matches the order surfers usually scan.
export const REGION_ORDER: string[] = [
  'northeast',
  'mid_atlantic',
  'southeast',
  'florida',
  'gulf',
  'socal',
  'norcal',
  'pacific_northwest',
  'hawaii',
  'puerto_rico',
];

function sortByRegion(rows: DailyReport[]): DailyReport[] {
  const idx = new Map(REGION_ORDER.map((k, i) => [k, i]));
  return [...rows].sort(
    (a, b) =>
      (idx.get(a.region) ?? 99) - (idx.get(b.region) ?? 99),
  );
}

/**
 * The latest daily_report per region. Pulls a small window (the last
 * 3 days) and dedupes client-side, keeping the newest per region —
 * Supabase REST doesn't support DISTINCT ON. If today's batch hasn't
 * landed yet for some region we surface yesterday's automatically.
 */
export async function fetchLatestReports(): Promise<DailyReport[]> {
  const { data, error } = await supabase
    .from('daily_reports')
    .select('region, region_label, report_date, summary, top_spots, trend, generated_at')
    .gte('report_date', isoDateOffset(-3))
    .order('report_date', { ascending: false });
  if (error) {
    // eslint-disable-next-line no-console
    console.error('fetchLatestReports', error);
    return [];
  }
  const seen = new Set<string>();
  const out: DailyReport[] = [];
  for (const row of (data ?? []) as DailyReport[]) {
    if (seen.has(row.region)) continue;
    seen.add(row.region);
    out.push(row);
  }
  return sortByRegion(out);
}

export async function fetchReportsForDate(date: string): Promise<DailyReport[]> {
  const { data, error } = await supabase
    .from('daily_reports')
    .select('region, region_label, report_date, summary, top_spots, trend, generated_at')
    .eq('report_date', date)
    .order('region');
  if (error) {
    // eslint-disable-next-line no-console
    console.error('fetchReportsForDate', error);
    return [];
  }
  return sortByRegion((data ?? []) as DailyReport[]);
}

/** Lightweight index of every published report — for sitemap building.
 *  Only returns (region, report_date) so the sitemap doesn't pull
 *  summary text it isn't going to render. */
export async function fetchReportIndex(): Promise<
  Array<{ region: string; report_date: string; generated_at: string }>
> {
  const { data, error } = await supabase
    .from('daily_reports')
    .select('region, report_date, generated_at')
    .order('report_date', { ascending: false })
    .limit(5000);
  if (error) {
    // eslint-disable-next-line no-console
    console.error('fetchReportIndex', error);
    return [];
  }
  return (data ?? []) as Array<{
    region: string;
    report_date: string;
    generated_at: string;
  }>;
}

/** Region-aware title prefix tuned for SEO targeting. Drives both the
 *  per-report page title and the share-sheet title. */
export const REGION_TITLE_PREFIX: Record<string, string> = {
  northeast:         'ME, NH, MA, RI Surf Report',
  mid_atlantic:      'NY, NJ, DE, MD, VA Surf Report',
  southeast:         'NC, SC Surf Report',
  florida:           'Florida Surf Report',
  gulf:              'Texas Gulf Surf Report',
  socal:             'Southern California Surf Report',
  norcal:            'Northern California & Oregon Surf Report',
  pacific_northwest: 'Washington Surf Report',
  hawaii:            'Hawaii Surf Report',
  puerto_rico:       'Puerto Rico Surf Report',
};

/** Trim a long string to roughly `max` chars, breaking on the nearest
 *  word boundary so meta descriptions don't end mid-word. */
export function truncateAt(text: string, max = 150): string {
  const collapsed = text.replace(/\s+/g, ' ').trim();
  if (collapsed.length <= max) return collapsed;
  const slice = collapsed.slice(0, max);
  const lastSpace = slice.lastIndexOf(' ');
  const cut = lastSpace > max - 30 ? slice.slice(0, lastSpace) : slice;
  return cut.replace(/[,;:.\-–—]+$/, '') + '…';
}

export async function fetchReport(
  date: string,
  region: string,
): Promise<DailyReport | null> {
  const { data, error } = await supabase
    .from('daily_reports')
    .select('region, region_label, report_date, summary, top_spots, trend, generated_at')
    .eq('report_date', date)
    .eq('region', region)
    .maybeSingle();
  if (error) {
    // eslint-disable-next-line no-console
    console.error('fetchReport', error);
    return null;
  }
  return (data as DailyReport | null) ?? null;
}

/** Returns ISO YYYY-MM-DD `dayOffset` days from today (UTC). */
function isoDateOffset(dayOffset: number): string {
  const d = new Date();
  d.setUTCDate(d.getUTCDate() + dayOffset);
  return d.toISOString().slice(0, 10);
}

export function todayIso(): string {
  return isoDateOffset(0);
}

/** Add `n` days (positive or negative) to a YYYY-MM-DD string. */
export function addDays(iso: string, n: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCDate(d.getUTCDate() + n);
  return d.toISOString().slice(0, 10);
}

export function isAfterToday(iso: string): boolean {
  return iso > todayIso();
}

/** Long date label like "Wednesday, May 12, 2026" for headings. */
export function prettyDate(iso: string): string {
  const d = new Date(`${iso}T00:00:00Z`);
  return new Intl.DateTimeFormat('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(d);
}
