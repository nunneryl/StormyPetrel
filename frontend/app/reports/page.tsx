import type { Metadata } from 'next';
import Link from 'next/link';
import { ReportCard } from '@/components/ReportCard';
import { addDays, fetchLatestReports, todayIso } from '@/lib/reports';

export const revalidate = 1800;

export const metadata: Metadata = {
  title: {
    absolute: "Today's Surf Reports — Every US Region | Stormy Petrel",
  },
  description:
    'Daily AI-written surf reports for every US region. Conditions, top spots, and the next 2-3 days for the East Coast, West Coast, Gulf, Hawaii, and Puerto Rico.',
  alternates: { canonical: '/reports' },
  openGraph: {
    title: "Today's Surf Reports — Every US Region | Stormy Petrel",
    description:
      'Daily AI-written surf reports for every US region. Conditions, top spots, and the next 2-3 days.',
    type: 'website',
  },
};

export default async function ReportsPage() {
  const reports = await fetchLatestReports();
  const yesterday = addDays(todayIso(), -1);

  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-6 py-7 space-y-6">
      <header>
        <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">
          Surf reports
        </div>
        <h1 className="text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          Today&rsquo;s reports
        </h1>
        <p className="mt-1 text-text-secondary text-sm">
          Auto-generated each morning from the latest forecast data. One per region.
        </p>
      </header>

      <nav className="flex items-center gap-3 text-sm border-y border-ink-600 py-2">
        <Link
          href={`/reports/${yesterday}`}
          className="text-text-secondary hover:text-cyan-600 transition"
        >
          ← Yesterday
        </Link>
        <span className="text-text-muted">|</span>
        <span className="text-text-primary font-bold">Today</span>
      </nav>

      {reports.length === 0 ? (
        <div className="rounded-xl border border-ink-600 bg-white p-8 text-center text-text-muted text-sm">
          No reports yet. Check back after the morning batch lands (around 11:00 UTC / 7am ET).
        </div>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {reports.map((r) => (
            <ReportCard key={r.region} report={r} variant="full" />
          ))}
        </div>
      )}

      <div className="pt-2 text-xs text-text-muted">
        Want a different region or coverage gap?{' '}
        <Link href="/blog" className="text-cyan-600 hover:underline">
          Read about how the reports are written
        </Link>
        .
      </div>
    </div>
  );
}
