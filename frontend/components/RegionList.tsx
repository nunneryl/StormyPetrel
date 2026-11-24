'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import type { ListSpot } from '@/lib/listSpot';
import { StarRating } from './StarRating';
import { CompassArrow } from './CompassArrow';
import { SwellCompass } from './SwellCompass';
import { CamBadge } from './CamBadge';
import { HeightBasisMark } from './HeightBasisMark';
import { Sparkline } from './Sparkline';
import { degToCardinal, fmtHeightRange, fmtMph, fmtSec } from '@/lib/formatting';
import { ratingState, tierFromStars } from '@/lib/ratings';

type Filter = 'all' | 'fair' | 'good';

// "All" lists every spot, a spot with no rating included (it sorts last: app/region/[state]).
// FAIR+ and GOOD+ need a rating at or above their floor, so a spot with no rating is in neither.
function passes(stars: number | null | undefined, f: { id: Filter; min: number }): boolean {
  if (f.id === 'all') return true;
  return ratingState(stars) !== 'none' && (stars as number) >= f.min;
}

const FILTERS: { id: Filter; label: string; min: number }[] = [
  { id: 'all',  label: 'All',    min: 0 },
  { id: 'fair', label: 'FAIR+',  min: 2.5 },
  { id: 'good', label: 'GOOD+',  min: 4.0 },
];

export function RegionList({
  spots,
  sparks,
  camSlugs = [],
}: {
  /** One slim item per spot, computed on the server (lib/listSpot.ts). */
  spots: ListSpot[];
  sparks: Record<number, number[]>;
  /** Slugs of spots that have an active cam — drives the cam glyph
   *  next to the spot name. Array because Sets don't cross the
   *  server/client boundary as plain props. */
  camSlugs?: string[];
}) {
  const camSet = useMemo(() => new Set(camSlugs), [camSlugs]);
  const [filter, setFilter] = useState<Filter>('all');

  const filtered = useMemo(() => {
    const f = FILTERS.find((x) => x.id === filter) ?? FILTERS[0];
    return spots.filter((s) => passes(s.stars, f));
  }, [spots, filter]);

  return (
    <>
      <div className="flex items-center gap-2">
        {FILTERS.map((f) => {
          const active = filter === f.id;
          const count = spots.filter((s) => passes(s.stars, f)).length;
          return (
            <button
              key={f.id}
              type="button"
              onClick={() => setFilter(f.id)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium transition ${
                active
                  ? 'bg-cyan-500 text-ink-950'
                  : 'bg-ink-800 text-text-secondary hover:text-text-primary hover:bg-ink-700'
              }`}
            >
              {f.label}{' '}
              <span className={`ml-1 tabular-nums ${active ? 'text-ink-950/70' : 'text-text-muted'}`}>
                {count}
              </span>
            </button>
          );
        })}
      </div>

      {filtered.length === 0 ? (
        <div className="rounded-xl border border-ink-600 bg-ink-800/60 p-8 text-center text-text-muted text-sm">
          Nothing rates that high right now in this region. Try a lower filter.
        </div>
      ) : (
        <div className="space-y-2">
          {filtered.map((s) => {
            const tier = tierFromStars(s.stars);
            const series = sparks[s.id] ?? [];
            return (
              <Link
                key={s.id}
                href={`/spot/${s.slug}`}
                className="grid grid-cols-[1fr_auto] gap-3 items-center rounded-xl border border-ink-600 bg-ink-800/60 hover:border-cyan-500 hover:bg-ink-700/60 transition p-3 group"
              >
                <div className="min-w-0">
                  <div className="flex items-center gap-2 flex-wrap">
                    <span className="font-bold text-text-primary group-hover:text-cyan-400 transition-colors inline-flex items-center gap-1.5">
                      {s.name}
                      <CamBadge hasCam={camSet.has(s.slug)} size={12} />
                    </span>
                    <StarRating score={s.stars} size="sm" />
                    {s.breakLabel && (
                      <span className="text-xs text-text-muted">
                        {s.breakLabel}
                      </span>
                    )}
                  </div>
                  <div className="mt-1 flex items-center gap-3 text-xs text-text-secondary flex-wrap">
                    <span className="inline-flex items-baseline gap-1">
                      <span className="font-bold text-text-primary tabular-nums">
                        {fmtHeightRange(s.faceFt)}
                      </span>
                      <HeightBasisMark basis={s.basis} />
                    </span>
                    <span className="tabular-nums">
                      {fmtSec(s.tp)}
                    </span>
                    <span className="inline-flex items-center gap-1">
                      <SwellCompass
                        deg={s.dp}
                        size={16}
                      />
                      <span className="font-mono text-[11px] text-text-secondary tabular-nums">
                        {degToCardinal(s.dp)}
                      </span>
                    </span>
                    <span className="flex items-center gap-1 text-text-muted">
                      <CompassArrow
                        deg={s.windDir}
                        size={12}
                        variant="wind"
                        showLabel={false}
                      />
                      {fmtMph(s.windSpeed)}
                    </span>
                  </div>
                </div>
                {series.length > 1 && (
                  <div className="hidden sm:block w-28">
                    <Sparkline values={series} color={tier.hex} height={32} />
                  </div>
                )}
              </Link>
            );
          })}
        </div>
      )}
    </>
  );
}
