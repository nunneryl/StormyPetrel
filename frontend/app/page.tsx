import Link from 'next/link';
import { fetchSpotsWithLatest } from '@/lib/queries';
import { SpotCard } from '@/components/SpotCard';
import { SearchBar } from '@/components/SearchBar';
import { SectionHeader } from '@/components/SectionHeader';
import { RatingBadge } from '@/components/RatingBadge';
import { CompassArrow } from '@/components/CompassArrow';
import { fmtFt } from '@/lib/formatting';
import { tierFromStars } from '@/lib/ratings';

export const dynamic = 'force-dynamic';
export const revalidate = 600;

export default async function HomePage() {
  const spots = await fetchSpotsWithLatest();

  // Best 12 right now — sorted by current stars, dropped if no rating yet.
  const ranked = [...spots]
    .filter((s) => (s.latest?.stars ?? 0) > 0)
    .sort((a, b) => (b.latest?.stars ?? 0) - (a.latest?.stars ?? 0))
    .slice(0, 12);

  // Top spot per state. Used by the "Browse by region" grid.
  const topByState = new Map<string, (typeof spots)[number]>();
  for (const s of spots) {
    if (!s.state) continue;
    const cur = topByState.get(s.state);
    if (!cur || (s.latest?.stars ?? 0) > (cur.latest?.stars ?? 0)) {
      topByState.set(s.state, s);
    }
  }
  const stateRows = Array.from(topByState.entries())
    .sort((a, b) => (b[1].latest?.stars ?? 0) - (a[1].latest?.stars ?? 0))
    .slice(0, 12);

  const searchItems = spots.map((s) => ({
    slug: s.slug,
    name: s.name,
    state: s.state,
  }));

  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6">
      {/* Hero */}
      <section className="py-12 sm:py-20 flex flex-col items-center text-center gap-5">
        <h1 className="text-4xl sm:text-6xl font-bold tracking-tightish text-text-primary leading-[1.05]">
          Free surf forecasts.
          <br />
          <span className="bg-gradient-to-r from-cyan-400 to-cyan-500 bg-clip-text text-transparent">
            No paywall.
          </span>
        </h1>
        <p className="text-text-secondary max-w-2xl text-base sm:text-lg leading-relaxed">
          {spots.length} US surf spots, rated hourly out to 6 days. Powered by
          NOAA NWPS, WAVEWATCH III, HRRR, and NDBC — the same models the pros
          pay for, surfaced for free.
        </p>
        <div className="w-full mt-2 flex justify-center">
          <SearchBar spots={searchItems} size="lg" />
        </div>
        <div className="flex items-center gap-2 text-xs text-text-muted">
          <span className="inline-flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-rating-good animate-pulseSubtle" />
            Live · forecast refreshes every 6 h, buoys hourly
          </span>
        </div>
      </section>

      {/* Best now — horizontal scroller */}
      {ranked.length > 0 && (
        <section className="mb-10">
          <SectionHeader
            title="Best conditions right now"
            right={
              <Link
                href="/map"
                className="text-xs text-text-secondary hover:text-cyan-400"
              >
                See all on the map →
              </Link>
            }
          />
          <div className="flex gap-3 overflow-x-auto scrollbar-hidden -mx-4 px-4 pb-2">
            {ranked.map((s) => (
              <SpotCard key={s.id} spot={s} variant="rail" />
            ))}
          </div>
        </section>
      )}

      {/* Regional overview — top spot per state */}
      <section className="mb-12">
        <SectionHeader
          title="Best in each region"
          right={
            <Link
              href="/regions"
              className="text-xs text-text-secondary hover:text-cyan-400"
            >
              All regions →
            </Link>
          }
        />
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-3">
          {stateRows.map(([state, top]) => {
            const tier = tierFromStars(top.latest?.stars ?? 0);
            return (
              <Link
                key={state}
                href={`/region/${encodeURIComponent(state.toLowerCase())}`}
                className="rounded-xl border border-ink-600 bg-ink-800/60 hover:border-cyan-500 hover:bg-ink-700/60 transition p-4 group"
              >
                <div className="flex items-start justify-between gap-2 mb-2">
                  <div className="min-w-0">
                    <div className="text-sm font-bold text-text-primary group-hover:text-cyan-400 transition-colors">
                      {state}
                    </div>
                    <div className="text-xs text-text-muted truncate">
                      Top: {top.name}
                    </div>
                  </div>
                  <RatingBadge stars={top.latest?.stars ?? 0} size="sm" />
                </div>
                <div className="flex items-center gap-2 text-sm">
                  <span className="font-bold text-text-primary tabular-nums">
                    {fmtFt(top.latest?.face_ft ?? null)}
                  </span>
                  <CompassArrow
                    deg={top.latest?.swell_dp ?? top.latest?.dp ?? null}
                    size={12}
                    variant="swell"
                    showLabel={false}
                  />
                  <span
                    className="ml-auto text-[10px] uppercase tracking-widest2 px-1.5 py-0.5 rounded"
                    style={{ background: tier.glow, color: tier.hex }}
                  >
                    {tier.label}
                  </span>
                </div>
              </Link>
            );
          })}
        </div>
      </section>
    </div>
  );
}
