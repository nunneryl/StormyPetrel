import Link from 'next/link';
import type { SpotWithLatest } from '@/lib/types';
import { tierFromStars } from '@/lib/ratings';
import { fmtFt } from '@/lib/formatting';

/**
 * Compact horizontal "what's good right now" strip — sits directly
 * below the nav. Each spot reads "Name 5.2ft FAIR · …" with the
 * tier label colored to its hex. Scrolls horizontally; hidden if
 * nothing's rideable.
 */
export function ConditionsTicker({ spots }: { spots: SpotWithLatest[] }) {
  const top = [...spots]
    .filter((s) => (s.latest?.stars ?? 0) >= 2.5)
    .sort((a, b) => (b.latest?.stars ?? 0) - (a.latest?.stars ?? 0))
    .slice(0, 16);

  if (top.length === 0) return null;

  return (
    <div className="border-b border-ink-600 bg-ink-900">
      <div className="mx-auto max-w-7xl px-4">
        <div className="flex items-center gap-2 py-2 overflow-x-auto scrollbar-hidden">
          <span className="shrink-0 text-[10px] uppercase tracking-widest2 text-text-muted pr-2">
            Best now
          </span>
          {top.map((s) => {
            const tier = tierFromStars(s.latest?.stars ?? 0);
            return (
              <Link
                key={s.id}
                href={`/spot/${s.slug}`}
                className="shrink-0 inline-flex items-center gap-1.5 text-xs text-text-secondary hover:text-text-primary"
              >
                <span className="font-medium text-text-primary">{s.name}</span>
                <span className="tabular-nums">{fmtFt(s.latest?.face_ft ?? null)}</span>
                <span
                  className="font-bold tracking-wider uppercase text-[10px]"
                  style={{ color: tier.hex }}
                >
                  {tier.label}
                </span>
                <span className="text-text-muted px-1">·</span>
              </Link>
            );
          })}
        </div>
      </div>
    </div>
  );
}
