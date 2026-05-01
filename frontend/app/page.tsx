import Image from 'next/image';
import Link from 'next/link';
import { fetchSpotsWithLatest } from '@/lib/queries';
import { HeroSearch, type HeroSearchItem } from '@/components/HeroSearch';
import { RatingBadge } from '@/components/RatingBadge';
import { CompassArrow } from '@/components/CompassArrow';
import { SectionHeader } from '@/components/SectionHeader';
import { fmtFt, fmtSec, msToMph, pickSwell } from '@/lib/formatting';
import type { SpotWithLatest } from '@/lib/types';

export const dynamic = 'force-dynamic';
export const revalidate = 600;

// Quick-access regions surfaced as pills under the hero search. Order
// matters: California / Hawaii first because they're the search bait.
const QUICK_REGIONS = ['California', 'Hawaii', 'New Jersey', 'New York'];

export default async function HomePage() {
  const spots = await fetchSpotsWithLatest();

  // Top 10 ranked — even if they're all FAIR / POOR, we show them.
  const top10: SpotWithLatest[] = [...spots]
    .sort((a, b) => (b.latest?.stars ?? -1) - (a.latest?.stars ?? -1))
    .slice(0, 10);

  // Per-state aggregation for "Browse by region": count + best current
  // rating tier (used for the badge on each card).
  const byState = new Map<string, { count: number; best: number }>();
  for (const s of spots) {
    if (!s.state) continue;
    const cur = byState.get(s.state);
    const stars = s.latest?.stars ?? 0;
    if (!cur) {
      byState.set(s.state, { count: 1, best: stars });
    } else {
      cur.count += 1;
      if (stars > cur.best) cur.best = stars;
    }
  }
  const states = Array.from(byState.entries()).sort(
    (a, b) => b[1].count - a[1].count || a[0].localeCompare(b[0]),
  );

  // Search payload — strip to just what HeroSearch renders.
  const searchItems: HeroSearchItem[] = spots.map((s) => ({
    slug: s.slug,
    name: s.name,
    state: s.state,
    stars: s.latest?.stars ?? null,
    face_ft: s.latest?.face_ft ?? null,
  }));

  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-6">
      {/* HERO — search-first. Logo overhead, big input below. The whole
          above-the-fold is this section: a surfer lands, types a name,
          and is on the forecast page in two seconds. */}
      <section className="pt-10 sm:pt-14 pb-8 sm:pb-10 flex flex-col items-center text-center gap-5">
        <div className="flex items-center justify-center gap-4 sm:gap-6">
          <Image
            src="/logo.png"
            alt="Stormy Petrel"
            width={150}
            height={150}
            priority
            unoptimized
            className="h-[120px] w-auto sm:h-[150px]"
          />
          <h1
            className="font-bold tracking-tightish text-4xl sm:text-5xl lg:text-6xl"
            style={{ color: '#0F172A' }}
          >
            Stormy Petrel
          </h1>
        </div>

        <div className="w-full max-w-2xl">
          <HeroSearch spots={searchItems} />
        </div>

        <div className="flex items-center gap-2 flex-wrap justify-center">
          {QUICK_REGIONS.filter((r) => byState.has(r)).map((region) => (
            <Link
              key={region}
              href={`/region/${encodeURIComponent(region.toLowerCase())}`}
              className="inline-flex items-center px-3.5 py-1.5 rounded-full bg-ink-900 border border-ink-600 text-sm text-text-secondary hover:text-text-primary hover:border-cyan-500 transition"
            >
              {region}
            </Link>
          ))}
          <Link
            href="/regions"
            className="inline-flex items-center px-3.5 py-1.5 rounded-full text-sm text-cyan-600 hover:underline"
          >
            All regions →
          </Link>
        </div>
      </section>

      {/* FUTURE: Featured content / cam of the day goes here.
          When that ships, this also flips to a two-column desktop
          layout (left = featured + leaderboard, right = sidebar
          for daily reports / cam highlights). */}

      {/* "Best now" — flat leaderboard table, not cards. Compact +
          scannable; mobile collapses to a tighter row. */}
      <section className="mb-10">
        <SectionHeader
          title="Best conditions right now"
          right={
            <Link href="/map" className="text-xs text-text-secondary hover:text-cyan-600">
              See on map →
            </Link>
          }
        />
        <div className="rounded-xl border border-ink-600 bg-white shadow-card overflow-hidden">
          <div className="hidden md:grid grid-cols-[minmax(0,2fr)_minmax(0,1fr)_120px_64px_72px_120px] gap-3 px-4 py-2 text-[10px] uppercase tracking-widest2 text-text-secondary border-b border-ink-600 bg-ink-900">
            <div>Spot</div>
            <div>State</div>
            <div>Rating</div>
            <div className="text-right">Face</div>
            <div className="text-right">Period</div>
            <div>Wind</div>
          </div>
          {top10.map((s) => {
            const f = s.latest;
            const tp = pickSwell(f?.swell_tp ?? null, f?.tp ?? null);
            const wMph = msToMph(f?.wind_speed ?? null);
            return (
              <Link
                key={s.id}
                href={`/spot/${s.slug}`}
                className="grid grid-cols-[minmax(0,1fr)_auto] md:grid-cols-[minmax(0,2fr)_minmax(0,1fr)_120px_64px_72px_120px] gap-3 px-4 py-2.5 border-b border-ink-600 last:border-b-0 hover:bg-ink-800 transition group"
              >
                <div className="min-w-0">
                  <div className="font-bold text-text-primary group-hover:text-cyan-600 truncate">
                    {s.name}
                  </div>
                  {/* On mobile, state goes under the name */}
                  <div className="md:hidden text-xs text-text-secondary truncate">
                    {s.state ?? ''}
                  </div>
                </div>
                <div className="hidden md:block text-text-secondary truncate text-sm self-center">
                  {s.state ?? ''}
                </div>
                <div className="hidden md:flex items-center">
                  <RatingBadge stars={f?.stars ?? 0} size="sm" />
                </div>
                <div className="hidden md:flex items-center justify-end font-bold tabular-nums text-text-primary">
                  {fmtFt(f?.face_ft ?? null)}
                </div>
                <div className="hidden md:flex items-center justify-end text-text-secondary tabular-nums text-sm">
                  {fmtSec(tp)}
                </div>
                <div className="hidden md:flex items-center gap-1.5 text-sm text-text-secondary">
                  <CompassArrow deg={f?.wind_dir ?? null} size={12} variant="wind" showLabel={false} />
                  <span className="tabular-nums">
                    {wMph !== null ? `${wMph.toFixed(0)} mph` : '—'}
                  </span>
                </div>
                {/* Mobile-only summary on the right side */}
                <div className="md:hidden flex items-center gap-2 shrink-0 self-start">
                  <span className="font-bold tabular-nums text-text-primary">
                    {fmtFt(f?.face_ft ?? null)}
                  </span>
                  <RatingBadge stars={f?.stars ?? 0} size="sm" />
                </div>
              </Link>
            );
          })}
        </div>
      </section>

      {/* Browse by region — simple grid, no per-card hex dots / face
          height. Just state name + count + best-rating badge. */}
      <section className="mb-12">
        <SectionHeader
          title="Browse by region"
          right={
            <Link href="/regions" className="text-xs text-text-secondary hover:text-cyan-600">
              All →
            </Link>
          }
        />
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-2.5">
          {states.map(([state, info]) => (
            <Link
              key={state}
              href={`/region/${encodeURIComponent(state.toLowerCase())}`}
              className="flex items-center justify-between gap-3 rounded-xl border border-ink-600 bg-white shadow-card px-3.5 py-3 hover:bg-ink-800 transition group"
            >
              <div className="min-w-0">
                <div className="font-bold text-text-primary group-hover:text-cyan-600 truncate">
                  {state}
                </div>
                <div className="text-[11px] text-text-muted tabular-nums">
                  {info.count} spot{info.count === 1 ? '' : 's'}
                </div>
              </div>
              <RatingBadge stars={info.best} size="sm" className="shrink-0" />
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
