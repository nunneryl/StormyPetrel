import Link from 'next/link';
import type { DailyReport, ReportTrend } from '@/lib/reports';
import { StarRating } from './StarRating';
import { StarText } from './StarText';
import { ShareButton } from './ShareButton';

const TREND_GLYPH: Record<ReportTrend, { glyph: string; color: string; label: string }> = {
  building: { glyph: '↑', color: '#15803D', label: 'building' },
  steady:   { glyph: '→', color: '#475569', label: 'steady' },
  fading:   { glyph: '↓', color: '#B91C1C', label: 'fading' },
};

export function ReportCard({
  report,
  variant = 'rail',
}: {
  report: DailyReport;
  /** rail = compact horizontal-scroll card on the homepage.
   *  full = expanded card for the /reports list. */
  variant?: 'rail' | 'full';
}) {
  const trend = TREND_GLYPH[report.trend];
  const href = `/reports/${report.report_date}/${report.region}`;
  const shareTitle = `${report.region_label} Surf Report`;

  if (variant === 'rail') {
    // Top 2 spots on the rail card — gives the visitor enough to decide
    // whether to drill in without making the card too tall.
    const topTwo = report.top_spots.slice(0, 2);
    return (
      <div className="relative w-72 shrink-0">
        <Link
          href={href}
          className="block rounded-xl border border-ink-600 bg-white hover:border-cyan-500 shadow-card transition p-4 group"
        >
          <div className="flex items-center justify-between gap-2 mb-2 pr-8">
            <span className="text-[11px] uppercase tracking-widest2 font-bold text-text-primary group-hover:text-cyan-600">
              {report.region_label}
            </span>
            <span
              className="text-[10px] uppercase tracking-widest2 font-bold inline-flex items-center gap-0.5"
              style={{ color: trend.color }}
            >
              {trend.label} {trend.glyph}
            </span>
          </div>
          <p className="text-sm text-text-secondary leading-snug line-clamp-3">
            <StarText text={report.summary} />
          </p>
          {topTwo.length > 0 && (
            <div className="mt-3 pt-3 border-t border-ink-600 space-y-1">
              <div className="text-[10px] uppercase tracking-widest2 text-text-muted">
                Best
              </div>
              <ul className="space-y-0.5">
                {topTwo.map((s) => (
                  <li
                    key={s.slug}
                    className="grid grid-cols-[minmax(0,1fr)_auto_auto] items-center gap-2 text-xs"
                  >
                    <span className="text-text-primary font-bold truncate">
                      {s.name}
                    </span>
                    <StarRating score={s.stars} size="xs" />
                    <span className="font-bold text-text-primary tabular-nums w-12 text-right">
                      {s.face_ft !== null && s.face_ft !== undefined
                        ? `${s.face_ft.toFixed(1)}ft`
                        : '—'}
                    </span>
                  </li>
                ))}
              </ul>
            </div>
          )}
        </Link>
        <span className="absolute top-2.5 right-2.5">
          <ShareButton url={href} title={shareTitle} text={report.summary} />
        </span>
      </div>
    );
  }

  // full variant — for /reports list. Region link at the bottom,
  // share button top-right, gold-tinted ★ glyphs inside the summary.
  return (
    <article className="relative rounded-xl border border-ink-600 bg-white shadow-card p-5">
      <span className="absolute top-3 right-3">
        <ShareButton url={href} title={shareTitle} text={report.summary} />
      </span>
      <header className="flex items-center justify-between gap-3 mb-3 pr-8">
        <h2 className="text-lg font-bold tracking-tightish text-text-primary">
          <Link href={href} className="hover:text-cyan-600">
            {report.region_label}
          </Link>
        </h2>
        <span
          className="text-[11px] uppercase tracking-widest2 font-bold inline-flex items-center gap-1"
          style={{ color: trend.color }}
        >
          {trend.label} {trend.glyph}
        </span>
      </header>
      <p className="text-sm text-text-primary leading-relaxed mb-4">
        <StarText text={report.summary} />
      </p>
      <div className="space-y-1.5">
        <div className="text-[10px] uppercase tracking-widest2 text-text-secondary">
          Top spots
        </div>
        <ul className="grid grid-cols-1 sm:grid-cols-2 gap-x-4 gap-y-1.5">
          {report.top_spots.slice(0, 6).map((s) => (
            <li key={s.slug} className="flex items-center justify-between gap-2 text-sm">
              <Link
                href={`/spot/${s.slug}`}
                className="text-text-primary hover:text-cyan-600 truncate"
              >
                {s.name}
              </Link>
              <span className="flex items-center gap-1.5 shrink-0">
                <StarRating score={s.stars} size="xs" />
                {s.face_ft !== null && s.face_ft !== undefined && (
                  <span className="font-bold text-text-primary tabular-nums text-xs">
                    {s.face_ft.toFixed(1)}ft
                  </span>
                )}
              </span>
            </li>
          ))}
        </ul>
      </div>
      <div className="mt-4 pt-3 border-t border-ink-600">
        <Link
          href="/regions"
          className="text-xs text-cyan-600 hover:underline"
        >
          Browse all regions →
        </Link>
      </div>
    </article>
  );
}
