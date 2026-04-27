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
  const states = Array.from(counts.entries())
    .sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 space-y-6">
      <div>
        <h1 className="text-3xl font-bold text-white">Browse by region</h1>
        <p className="mt-1 text-slate-400 text-sm">
          {spots.length} spots across {states.length} states & territories.
        </p>
      </div>
      <div className="grid grid-cols-2 sm:grid-cols-3 lg:grid-cols-4 gap-3">
        {states.map(([state, n]) => (
          <Link
            key={state}
            href={`/region/${encodeURIComponent(state.toLowerCase())}`}
            className="rounded border border-ink-700 bg-ink-900 hover:border-sea-500 hover:bg-ink-800 transition p-4 flex items-center justify-between"
          >
            <span className="font-bold text-slate-100">{state}</span>
            <span className="text-sm text-slate-400 tabular-nums">{n}</span>
          </Link>
        ))}
      </div>
    </div>
  );
}
