import Link from 'next/link';
import type { SpotWithLatest } from '@/lib/types';
import { RatingBadge } from './RatingBadge';
import { CompassArrow } from './CompassArrow';
import { fmtFt, fmtMph, fmtSec, pickSwell } from '@/lib/formatting';

export function SpotCard({ spot }: { spot: SpotWithLatest }) {
  const f = spot.latest;
  return (
    <Link
      href={`/spot/${spot.slug}`}
      className="block rounded border border-ink-700 bg-ink-900 hover:border-sea-500 hover:bg-ink-800 transition p-3"
    >
      <div className="flex items-start justify-between gap-2">
        <div className="min-w-0">
          <div className="font-bold text-slate-100 truncate">{spot.name}</div>
          <div className="text-xs text-slate-400 truncate">
            {spot.state ?? '—'}
            {spot.break_type ? ` · ${spot.break_type}` : ''}
          </div>
        </div>
        <RatingBadge stars={f?.stars ?? 0} size="sm" />
      </div>
      <div className="mt-2 flex items-center gap-3 text-xs text-slate-300">
        <span className="font-bold text-slate-100">{fmtFt(f?.face_ft ?? null)}</span>
        <span className="text-slate-400">{fmtSec(pickSwell(f?.swell_tp ?? null, f?.tp ?? null))}</span>
        <CompassArrow deg={pickSwell(f?.swell_dp ?? null, f?.dp ?? null)} size={12} color="#3da9d7" />
        <span className="ml-auto flex items-center gap-1 text-slate-400">
          <CompassArrow deg={f?.wind_dir ?? null} size={12} color="#9bbf3e" showLabel={false} />
          {fmtMph(f?.wind_speed ?? null)}
        </span>
      </div>
    </Link>
  );
}
