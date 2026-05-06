import Link from 'next/link';
import type { SpotWithLatest } from '@/lib/types';
import { StarRating } from './StarRating';
import { CompassArrow } from './CompassArrow';
import { SwellCompass } from './SwellCompass';
import { fmtFt, fmtMph, fmtSec, pickSwell } from '@/lib/formatting';
import { tierFromStars } from '@/lib/ratings';

type Variant = 'default' | 'rail';

export function SpotCard({
  spot,
  variant = 'default',
}: {
  spot: SpotWithLatest;
  variant?: Variant;
}) {
  const f = spot.latest;
  const tier = tierFromStars(f?.stars ?? 0);
  const isRail = variant === 'rail';

  return (
    <Link
      href={`/spot/${spot.slug}`}
      className={
        isRail
          ? 'block w-64 shrink-0 rounded-xl border border-ink-600 bg-ink-800 hover:border-cyan-500 hover:bg-ink-700 transition p-3 group'
          : 'block rounded-xl border border-ink-600 bg-ink-800 hover:border-cyan-500 hover:bg-ink-700 transition p-3 group'
      }
      style={
        isRail
          ? {
              backgroundImage: `linear-gradient(135deg, ${tier.glow} 0%, transparent 60%)`,
            }
          : undefined
      }
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-bold text-text-primary truncate group-hover:text-cyan-400 transition-colors">
            {spot.name}
          </div>
          <div className="text-xs text-text-secondary truncate">
            {spot.state ?? '—'}
            {spot.break_type ? ` · ${spot.break_type}` : ''}
          </div>
        </div>
        <StarRating score={f?.stars ?? 0} size="sm" />
      </div>
      <div className="mt-2.5 flex items-center gap-3 text-xs text-text-secondary">
        <span className="font-bold text-text-primary text-base tabular-nums">
          {fmtFt(f?.face_ft ?? null)}
        </span>
        <span className="text-text-muted tabular-nums">
          {fmtSec(pickSwell(f?.swell_tp ?? null, f?.tp ?? null))}
        </span>
        <SwellCompass
          deg={pickSwell(f?.swell_dp ?? null, f?.dp ?? null)}
          size={16}
        />
        <span className="ml-auto flex items-center gap-1 text-text-muted">
          <CompassArrow deg={f?.wind_dir ?? null} size={12} variant="wind" showLabel={false} />
          {fmtMph(f?.wind_speed ?? null)}
        </span>
      </div>
    </Link>
  );
}
