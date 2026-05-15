import type { Metadata } from 'next';
import Link from 'next/link';
import { ReportCard } from '@/components/ReportCard';
import {
  addDays,
  fetchReportsForDate,
  isAfterToday,
  prettyDate,
  todayIso,
} from '@/lib/reports';

export const revalidate = 1800;

type Params = { date: string };

function looksLikeDate(s: string): boolean {
  return /^\d{4}-\d{2}-\d{2}$/.test(s);
}

export async function generateMetadata({
  params,
}: {
  params: Promise<Params>;
}): Promise<Metadata> {
  const { date } = await params;
  if (!looksLikeDate(date)) return { title: 'Report not found' };
  const dateLabel = prettyDate(date);
  const title = `Surf Reports — ${dateLabel} | Stormy Petrel`;
  const description = `Surf reports for every US region on ${dateLabel}. Conditions, top spots, and the trend for the next 2-3 days.`;
  return {
    title: { absolute: title },
    description,
    alternates: { canonical: `/reports/${date}` },
    openGraph: { title, description, type: 'website' },
  };
}

export default async function ReportsForDatePage({
  params,
}: {
  params: Promise<Params>;
}) {
  const { date } = await params;

  // Guard against the [date] segment swallowing requests that were
  // actually meant for the nested /[region] route (e.g. typed paths)
  // — if it doesn't look like an ISO date we just render an empty
  // state with the navigation rather than 500.
  const validDate = looksLikeDate(date);
  const reports = validDate ? await fetchReportsForDate(date) : [];

  const prevDate = validDate ? addDays(date, -1) : todayIso();
  const nextDate = validDate ? addDays(date, 1) : todayIso();
  const nextIsAhead = isAfterToday(nextDate);
  const dateLabel = validDate ? prettyDate(date) : 'Reports';

  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-6 py-7 space-y-6">
      <header>
        <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">
          Surf reports
        </div>
        <h1 className="text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          {dateLabel}
        </h1>
        <p className="mt-1 text-text-secondary text-sm">
          One AI-written report per region, generated from that morning&rsquo;s forecast.
        </p>
      </header>

      <nav className="flex items-center justify-between gap-3 text-sm border-y border-ink-600 py-2">
        <Link
          href={`/reports/${prevDate}`}
          className="text-text-secondary hover:text-cyan-600 transition"
        >
          ← Previous day
        </Link>
        {nextIsAhead ? (
          <span className="text-text-muted">Next day →</span>
        ) : (
          <Link
            href={nextDate === todayIso() ? '/reports' : `/reports/${nextDate}`}
            className="text-text-secondary hover:text-cyan-600 transition"
          >
            Next day →
          </Link>
        )}
      </nav>

      {reports.length === 0 ? (
        <div className="rounded-xl border border-ink-600 bg-white p-8 text-center text-text-muted text-sm">
          No reports for {dateLabel}.
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {reports.map((r) => (
            <ReportCard key={r.region} report={r} variant="full" />
          ))}
        </div>
      )}
    </div>
  );
}
