import Link from 'next/link';
import { fetchAllSpots } from '@/lib/queries';

export const dynamic = 'force-dynamic';
export const revalidate = 3600;

export default async function RegionsIndex() {
  const spots = await fetchAllSpots();
  const counts = new Map<string, number>();
  for (const s of spots) {
    if (!s.state) continue;
    counts.set(s.state, (counts.get(s.state) ?? 0) + 1);
  }
  const states = Array.from(counts.entries()).sort(
    (a, b) => b[1] - a[1] || a[0].localeCompare(b[0]),
  );

  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6 py-7 space-y-6">
      <header>
        <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">
          Region index
        </div>
        <h1 className="text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          Browse by region
        </h1>
        <p className="mt-1 text-text-secondary text-sm">
          {spots.length} spots across {states.length} states &amp; territories.
        </p>
      </header>

      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
        {states.map(([state, n]) => (
          <Link
            key={state}
            href={`/region/${encodeURIComponent(state.toLowerCase())}`}
            className="group rounded-xl border border-ink-600 bg-ink-800/60 hover:border-cyan-500 hover:bg-ink-700/60 transition p-4 flex items-center justify-between"
          >
            <span className="font-bold text-text-primary group-hover:text-cyan-400 transition-colors">
              {state}
            </span>
            <span className="text-sm text-text-muted tabular-nums">{n}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
