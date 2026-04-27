import Link from 'next/link';
import { fetchSpotsWithLatest } from '@/lib/queries';
import { SpotCard } from '@/components/SpotCard';
import { SearchBar } from '@/components/SearchBar';

export const dynamic = 'force-dynamic';
export const revalidate = 600;

export default async function HomePage() {
  const spots = await fetchSpotsWithLatest();
  const ranked = [...spots]
    .filter((s) => (s.latest?.stars ?? 0) > 0)
    .sort((a, b) => (b.latest?.stars ?? 0) - (a.latest?.stars ?? 0))
    .slice(0, 10);

  const stateCounts = new Map<string, number>();
  for (const s of spots) {
    if (!s.state) continue;
    stateCounts.set(s.state, (stateCounts.get(s.state) ?? 0) + 1);
  }
  const states = Array.from(stateCounts.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]))
    .slice(0, 12);

  const searchItems = spots.map((s) => ({
    slug: s.slug,
    name: s.name,
    state: s.state,
  }));

  return (
    <div className="mx-auto max-w-7xl px-4">
      <section className="py-10 sm:py-16 flex flex-col items-center text-center gap-4">
        <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-white">
          Free surf forecasts.
          <br />
          <span className="text-sea-400">No paywall. No ads.</span>
        </h1>
        <p className="text-slate-400 max-w-2xl">
          {spots.length} US surf spots, rated hourly out to 6 days. Powered by NOAA NWPS,
          NDBC, and CO-OPS — the same data the pros pay for, surfaced for free.
        </p>
        <div className="w-full mt-2 flex justify-center">
          <SearchBar spots={searchItems} />
        </div>
      </section>

      {ranked.length > 0 && (
        <section className="space-y-3">
          <div className="flex items-end justify-between">
            <h2 className="text-sm uppercase tracking-widest text-slate-400">
              Best conditions right now
            </h2>
            <Link href="/map" className="text-xs text-slate-400 hover:text-slate-200 underline">
              See map →
            </Link>
          </div>
          <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-5 gap-3">
            {ranked.map((s) => (
              <SpotCard key={s.id} spot={s} />
            ))}
          </div>
        </section>
      )}

      <section className="mt-10 space-y-3">
        <div className="flex items-end justify-between">
          <h2 className="text-sm uppercase tracking-widest text-slate-400">
            Browse by region
          </h2>
          <Link href="/regions" className="text-xs text-slate-400 hover:text-slate-200 underline">
            All regions →
          </Link>
        </div>
        <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-6 gap-3">
          {states.map(([state, n]) => (
            <Link
              key={state}
              href={`/region/${encodeURIComponent(state.toLowerCase())}`}
              className="rounded border border-ink-700 bg-ink-900 hover:border-sea-500 hover:bg-ink-800 transition p-3 flex items-center justify-between"
            >
              <span className="font-bold text-slate-100 truncate">{state}</span>
              <span className="text-sm text-slate-400 tabular-nums">{n}</span>
            </Link>
          ))}
        </div>
      </section>
    </div>
  );
}
