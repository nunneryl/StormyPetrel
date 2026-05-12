import type { Metadata } from 'next';
import Link from 'next/link';
import { notFound } from 'next/navigation';
import {
  addDays,
  fetchReport,
  isAfterToday,
  prettyDate,
  REGION_TITLE_PREFIX,
  todayIso,
  truncateAt,
} from '@/lib/reports';
import { StarRating } from '@/components/StarRating';
import { StarText } from '@/components/StarText';
import { ShareButton } from '@/components/ShareButton';
import { siteUrl } from '@/lib/site-url';

export const dynamic = 'force-dynamic';
export const revalidate = 600;

type Params = { date: string; region: string };

const TREND_GLYPH: Record<string, { glyph: string; color: string; label: string }> = {
  building: { glyph: '↑', color: '#15803D', label: 'building' },
  steady:   { glyph: '→', color: '#475569', label: 'steady' },
  fading:   { glyph: '↓', color: '#B91C1C', label: 'fading' },
};

export async function generateMetadata({
  params,
}: {
  params: Promise<Params>;
}): Promise<Metadata> {
  const { date, region } = await params;
  const report = await fetchReport(date, region);
  if (!report) return { title: 'Report not found' };
  const dateLabel = prettyDate(report.report_date);
  // Region-aware title prefix so listings target "NY, NJ, DE, MD, VA
  // surf report" and similar long-tail queries instead of the generic
  // region label. Falls back to the human-readable region_label when
  // we don't have an explicit prefix mapped.
  const prefix =
    REGION_TITLE_PREFIX[report.region] ??
    `${report.region_label} Surf Report`;
  const title = `${prefix} — ${dateLabel} | Stormy Petrel`;
  const description = truncateAt(report.summary, 150);
  return {
    title: { absolute: title },
    description,
    alternates: { canonical: `/reports/${report.report_date}/${report.region}` },
    openGraph: { title, description, type: 'article' },
    twitter: { card: 'summary_large_image', title, description },
  };
}

export default async function ReportPage({
  params,
}: {
  params: Promise<Params>;
}) {
  const { date, region } = await params;
  const report = await fetchReport(date, region);
  if (!report) notFound();

  const trend = TREND_GLYPH[report.trend] ?? TREND_GLYPH.steady;
  const dateLabel = prettyDate(report.report_date);
  const base = siteUrl();
  const shareHref = `/reports/${report.report_date}/${report.region}`;
  const titlePrefix =
    REGION_TITLE_PREFIX[report.region] ??
    `${report.region_label} Surf Report`;

  // Day-to-day navigation within the same region. "Next day" hides
  // when we'd be pointing past today; if it equals today, point at
  // /reports/[today]/[region] still (which is the canonical URL for
  // today's report — the /reports list isn't region-specific).
  const prevDate = addDays(report.report_date, -1);
  const nextDate = addDays(report.report_date, 1);
  const nextIsAhead = isAfterToday(nextDate);

  // Structured data — Article schema, so Google can surface this as a
  // news-style result for "east coast surf report today" etc.
  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'Article',
    headline: `${titlePrefix} — ${dateLabel}`,
    datePublished: report.generated_at,
    dateModified: report.generated_at,
    description: report.summary,
    url: `${base}${shareHref}`,
    isPartOf: {
      '@type': 'WebSite',
      name: 'Stormy Petrel',
      url: base,
    },
    author: { '@type': 'Organization', name: 'Stormy Petrel' },
    publisher: {
      '@type': 'Organization',
      name: 'Stormy Petrel',
      url: base,
    },
  };

  const shareTitle = `${titlePrefix} — ${dateLabel}`;

  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 py-7 space-y-6">
      <script
        type="application/ld+json"
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />

      <nav className="text-xs text-text-muted">
        <Link href="/reports" className="hover:text-cyan-600">
          ← All reports
        </Link>
      </nav>

      <header className="relative">
        <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">
          {dateLabel}
        </div>
        <h1 className="text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary pr-10">
          {report.region_label} surf report
        </h1>
        <div
          className="mt-2 inline-flex items-center gap-1 text-[11px] uppercase tracking-widest2 font-bold"
          style={{ color: trend.color }}
        >
          {trend.label} {trend.glyph}
        </div>
        <span className="absolute top-0 right-0">
          <ShareButton url={shareHref} title={shareTitle} text={report.summary} />
        </span>
      </header>

      <nav className="flex items-center justify-between gap-3 text-sm border-y border-ink-600 py-2">
        <Link
          href={`/reports/${prevDate}/${report.region}`}
          className="text-text-secondary hover:text-cyan-600 transition"
        >
          ← Previous day
        </Link>
        {nextIsAhead ? (
          <span className="text-text-muted">Next day →</span>
        ) : (
          <Link
            href={`/reports/${nextDate}/${report.region}`}
            className="text-text-secondary hover:text-cyan-600 transition"
          >
            Next day →
          </Link>
        )}
      </nav>

      <p className="text-base text-text-primary leading-relaxed">
        <StarText text={report.summary} />
      </p>

      <section>
        <div className="text-[10px] uppercase tracking-widest2 text-text-secondary mb-2">
          Top spots
        </div>
        <ul className="rounded-xl border border-ink-600 bg-white shadow-card divide-y divide-ink-600 overflow-hidden">
          {report.top_spots.map((s) => (
            <li key={s.slug}>
              <Link
                href={`/spot/${s.slug}`}
                className="flex items-center justify-between gap-3 px-4 py-3 hover:bg-ink-800 transition"
              >
                <div className="min-w-0">
                  <div className="font-bold text-text-primary truncate">{s.name}</div>
                  {s.state && (
                    <div className="text-xs text-text-secondary truncate">{s.state}</div>
                  )}
                </div>
                <div className="flex items-center gap-3 shrink-0">
                  <StarRating score={s.stars} size="sm" />
                  {s.face_ft !== null && s.face_ft !== undefined && (
                    <span className="font-bold tabular-nums text-text-primary">
                      {s.face_ft.toFixed(1)}ft
                    </span>
                  )}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      </section>

      <footer className="pt-4 text-xs text-text-muted">
        Generated{' '}
        {new Date(report.generated_at).toLocaleString('en-US', {
          dateStyle: 'medium',
          timeStyle: 'short',
          timeZone: 'America/Los_Angeles',
        })}{' '}
        PT · written by Claude from the latest NOAA/NDBC forecast data.
      </footer>
    </div>
  );
}
