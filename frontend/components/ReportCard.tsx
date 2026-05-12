import Link from 'next/link';
import type { DailyReport, ReportTrend } from '@/lib/reports';
import { StarRating } from './StarRating';

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
  const best = report.top_spots[0];

  if (variant === 'rail') {
    return (
      <Link
        href={href}
        className="block w-72 shrink-0 rounded-xl border border-ink-600 bg-white hover:border-cyan-500 shadow-card transition p-4 group"
      >
        <div className="flex items-center justify-between gap-2 mb-2">
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
          {report.summary}
        </p>
        {best && (
          <div className="mt-3 pt-3 border-t border-ink-600 flex items-center justify-between gap-2 text-xs">
            <span className="text-text-muted truncate">
              Best: <span className="text-text-primary font-bold">{best.name}</span>
            </span>
            <span className="flex items-center gap-1.5 shrink-0">
              <StarRating score={best.stars} size="xs" />
              {best.face_ft !== null && best.face_ft !== undefined && (
                <span className="font-bold text-text-primary tabular-nums">
                  {best.face_ft.toFixed(1)}ft
                </span>
              )}
            </span>
          </div>
        )}
      </Link>
    );
  }

  // full variant — for /reports list
  return (
    <article className="rounded-xl border border-ink-600 bg-white shadow-card p-5">
      <header className="flex items-center justify-between gap-3 mb-3">
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
        {report.summary}
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
    </article>
  );
}
