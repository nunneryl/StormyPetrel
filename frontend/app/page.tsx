import Link from 'next/link';
import { fetchSpotsWithLatest } from '@/lib/queries';
import { ConditionsTicker } from '@/components/ConditionsTicker';
import { RatingBadge } from '@/components/RatingBadge';
import { CompassArrow } from '@/components/CompassArrow';
import { SectionHeader } from '@/components/SectionHeader';
import { fmtFt, fmtMph, fmtSec, msToMph, pickSwell } from '@/lib/formatting';
import { tierFromStars } from '@/lib/ratings';
import { listPosts } from '@/lib/blog';
import type { Forecast, SpotWithLatest } from '@/lib/types';

export const dynamic = 'force-dynamic';
export const revalidate = 600;

function freshnessLabel(latest: Forecast | null): string {
  if (!latest) return '—';
  const ms = Date.now() - new Date(latest.valid_time).getTime();
  const min = Math.max(0, Math.round(ms / 60000));
  if (min < 60) return `${min} min ago`;
  const h = Math.round(min / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.round(h / 24)}d ago`;
}

export default async function HomePage() {
  const spots = await fetchSpotsWithLatest();

  // Top 8 ridable spots right now — the leaderboard.
  const ranked = [...spots]
    .filter((s) => (s.latest?.stars ?? 0) > 0)
    .sort((a, b) => (b.latest?.stars ?? 0) - (a.latest?.stars ?? 0))
    .slice(0, 8);

  // Best spot per state — sidebar.
  const topByState = new Map<string, SpotWithLatest>();
  for (const s of spots) {
    if (!s.state) continue;
    const cur = topByState.get(s.state);
    if (!cur || (s.latest?.stars ?? 0) > (cur.latest?.stars ?? 0)) {
      topByState.set(s.state, s);
    }
  }
  const stateRows = Array.from(topByState.entries())
    .sort((a, b) => (b[1].latest?.stars ?? 0) - (a[1].latest?.stars ?? 0))
    .slice(0, 14);

  // Region overview cards (below fold) — every state with its spot count
  // and best current rating.
  const stateCounts = new Map<string, { count: number; top: SpotWithLatest }>();
  for (const s of spots) {
    if (!s.state) continue;
    const entry = stateCounts.get(s.state);
    if (!entry) {
      stateCounts.set(s.state, { count: 1, top: s });
    } else {
      entry.count += 1;
      if ((s.latest?.stars ?? 0) > (entry.top.latest?.stars ?? 0)) {
        entry.top = s;
      }
    }
  }
  const allStates = Array.from(stateCounts.entries())
    .sort((a, b) => (b[1].top.latest?.stars ?? 0) - (a[1].top.latest?.stars ?? 0));

  // Latest 3 blog posts for the sidebar.
  const latestPosts = listPosts().slice(0, 3);

  // Find the most-recent forecast across the whole DB just for a
  // freshness indicator in the sidebar.
  const newestUpdate = spots.reduce<Forecast | null>((acc, s) => {
    if (!s.latest) return acc;
    if (!acc) return s.latest;
    return new Date(s.latest.valid_time) > new Date(acc.valid_time)
      ? s.latest
      : acc;
  }, null);

  return (
    <>
      <ConditionsTicker spots={spots} />

      <div className="mx-auto max-w-7xl px-4 sm:px-6 py-6">
        {/* Two-column layout — leaderboard left, sidebar right (desktop) */}
        <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1fr)_320px] gap-8">
          {/* Leaderboard */}
          <section>
            <SectionHeader
              title="Today's highlights"
              right={
                <Link href="/map" className="text-xs text-text-secondary hover:text-cyan-600">
                  See all on the map →
                </Link>
              }
            />
            {ranked.length === 0 ? (
              <div className="rounded-xl border border-ink-600 bg-white p-8 text-center text-text-muted">
                Nothing rideable right now. The next forecast cycle runs in a few hours.
              </div>
            ) : (
              <div className="rounded-xl border border-ink-600 bg-white shadow-card overflow-hidden">
                <div className="hidden md:grid grid-cols-[minmax(0,1fr)_120px_64px_72px_120px_72px] gap-3 px-4 py-2 text-[10px] uppercase tracking-widest2 text-text-secondary border-b border-ink-600 bg-ink-900">
                  <div>Spot</div>
                  <div>Rating</div>
                  <div className="text-right">Face</div>
                  <div className="text-right">Period</div>
                  <div>Wind</div>
                  <div className="text-right">Updated</div>
                </div>
                {ranked.map((s) => {
                  const f = s.latest;
                  const tier = tierFromStars(f?.stars ?? 0);
                  const tp = pickSwell(f?.swell_tp ?? null, f?.tp ?? null);
                  const dp = pickSwell(f?.swell_dp ?? null, f?.dp ?? null);
                  const wMph = msToMph(f?.wind_speed ?? null);
                  return (
                    <Link
                      key={s.id}
                      href={`/spot/${s.slug}`}
                      className="grid grid-cols-[minmax(0,1fr)_auto] md:grid-cols-[minmax(0,1fr)_120px_64px_72px_120px_72px] gap-3 px-4 py-2.5 border-b border-ink-600 last:border-b-0 hover:bg-ink-800 transition group"
                    >
                      <div className="min-w-0">
                        <div className="font-bold text-text-primary group-hover:text-cyan-600 truncate">
                          {s.name}
                        </div>
                        <div className="text-xs text-text-secondary truncate">
                          {s.state ?? ''}
                          {s.break_type ? ` · ${s.break_type}` : ''}
                        </div>
                      </div>
                      <div className="md:flex items-center hidden">
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
                      <div className="hidden md:flex items-center justify-end text-[11px] text-text-muted tabular-nums">
                        {freshnessLabel(f)}
                      </div>
                      {/* Mobile-only summary on the right side of the row */}
                      <div className="md:hidden flex items-center gap-2 shrink-0">
                        <span className="font-bold tabular-nums text-text-primary">
                          {fmtFt(f?.face_ft ?? null)}
                        </span>
                        <RatingBadge stars={f?.stars ?? 0} size="sm" />
                      </div>
                    </Link>
                  );
                })}
              </div>
            )}
          </section>

          {/* Sidebar */}
          <aside className="space-y-6">
            {/* Browse by region — top spot per state */}
            <div>
              <SectionHeader
                title="Browse by region"
                right={
                  <Link href="/regions" className="text-xs text-text-secondary hover:text-cyan-600">
                    All →
                  </Link>
                }
              />
              <div className="rounded-xl border border-ink-600 bg-white shadow-card overflow-hidden">
                {stateRows.map(([state, top]) => {
                  const tier = tierFromStars(top.latest?.stars ?? 0);
                  return (
                    <Link
                      key={state}
                      href={`/region/${encodeURIComponent(state.toLowerCase())}`}
                      className="flex items-center justify-between gap-2 px-3 py-2 border-b border-ink-600 last:border-b-0 hover:bg-ink-800 transition group"
                    >
                      <div className="min-w-0">
                        <div className="text-sm font-medium text-text-primary group-hover:text-cyan-600 truncate">
                          {state}
                        </div>
                        <div className="text-[11px] text-text-muted truncate">
                          Top: {top.name}
                        </div>
                      </div>
                      <span
                        className="text-[10px] font-bold uppercase tracking-widest2 px-1.5 py-0.5 rounded shrink-0"
                        style={{
                          color: tier.hex,
                          background: `${tier.hex}15`,
                        }}
                      >
                        {tier.label}
                      </span>
                    </Link>
                  );
                })}
              </div>
            </div>

            {/* Latest blog posts */}
            <div>
              <SectionHeader
                title="Latest"
                right={
                  <Link href="/blog" className="text-xs text-text-secondary hover:text-cyan-600">
                    All posts →
                  </Link>
                }
              />
              <ul className="space-y-2">
                {latestPosts.map((p) => (
                  <li key={p.slug}>
                    <Link
                      href={`/blog/${p.slug}`}
                      className="block rounded-xl border border-ink-600 bg-white shadow-card p-3 hover:bg-ink-800 transition group"
                    >
                      <div className="text-[10px] uppercase tracking-widest2 text-text-muted">
                        {new Date(p.date).toLocaleDateString('en-US', {
                          month: 'short',
                          day: 'numeric',
                          year: 'numeric',
                        })}
                      </div>
                      <div className="mt-0.5 text-sm font-bold text-text-primary group-hover:text-cyan-600 leading-tight">
                        {p.title}
                      </div>
                    </Link>
                  </li>
                ))}
              </ul>
            </div>

            {/* Data freshness */}
            <div className="rounded-xl border border-ink-600 bg-ink-900 px-3 py-2.5">
              <div className="text-[10px] uppercase tracking-widest2 text-text-muted mb-1">
                Live
              </div>
              <div className="text-sm text-text-secondary">
                Forecast updated{' '}
                <span className="font-medium text-text-primary">
                  {freshnessLabel(newestUpdate)}
                </span>
              </div>
              <div className="text-sm text-text-secondary">
                Buoys:{' '}
                <span className="inline-flex items-center gap-1 text-rating-good font-medium">
                  <span className="w-1.5 h-1.5 rounded-full bg-rating-good animate-pulseSubtle" />
                  live
                </span>
              </div>
            </div>
          </aside>
        </div>

        {/* Below the fold — region quick-look strip */}
        <section className="mt-12">
          <SectionHeader title="All regions" />
          <div className="grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-6 gap-2">
            {allStates.map(([state, info]) => {
              const tier = tierFromStars(info.top.latest?.stars ?? 0);
              return (
                <Link
                  key={state}
                  href={`/region/${encodeURIComponent(state.toLowerCase())}`}
                  className="rounded-lg border border-ink-600 bg-white shadow-card p-3 hover:bg-ink-800 transition group"
                >
                  <div className="flex items-center justify-between mb-1">
                    <span className="text-sm font-bold text-text-primary group-hover:text-cyan-600 truncate">
                      {state}
                    </span>
                    <span className="text-[10px] text-text-muted tabular-nums">
                      {info.count}
                    </span>
                  </div>
                  <div className="flex items-center gap-1.5">
                    <span
                      className="w-2 h-2 rounded-full shrink-0"
                      style={{ background: tier.hex }}
                    />
                    <span className="text-[11px] text-text-secondary tabular-nums">
                      {fmtFt(info.top.latest?.face_ft ?? null)}
                    </span>
                  </div>
                </Link>
              );
            })}
          </div>
        </section>
      </div>
    </>
  );
}
