import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { fetchSpotsWithLatest, fetchSparklineData } from '@/lib/queries';
import { RegionList } from '@/components/RegionList';

export const dynamic = 'force-dynamic';
export const revalidate = 600;

type Params = { state: string };

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { state } = await params;
  const decoded = decodeURIComponent(state);
  const pretty = decoded
    .split(/[\s-]+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1).toLowerCase() : ''))
    .join(' ');
  const title = `${pretty} surf forecasts`;
  const description = `Live surf conditions and 7-day forecasts for every spot in ${pretty}. Sorted by current rating. Free, no paywall.`;
  return {
    title,
    description,
    alternates: { canonical: `/region/${encodeURIComponent(decoded.toLowerCase())}` },
    openGraph: { title, description, type: 'website' },
    twitter: { card: 'summary_large_image', title, description },
  };
}

export default async function RegionPage({ params }: { params: Promise<Params> }) {
  const { state } = await params;
  const decoded = decodeURIComponent(state).toLowerCase();
  const [spots, sparksMap] = await Promise.all([
    fetchSpotsWithLatest(),
    fetchSparklineData(),
  ]);
  const inState = spots.filter((s) => (s.state ?? '').toLowerCase() === decoded);
  if (inState.length === 0) notFound();

  inState.sort((a, b) => (b.latest?.stars ?? -1) - (a.latest?.stars ?? -1));
  const stateLabel = inState[0].state ?? decoded;

  // RegionList is a client component — convert the Map to a plain object
  // so it can cross the server / client boundary.
  const sparks: Record<number, number[]> = {};
  sparksMap.forEach((v, k) => { sparks[k] = v; });

  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6 py-7 space-y-6">
      <header>
        <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">Region</div>
        <h1 className="text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          {stateLabel}
        </h1>
        <p className="mt-1 text-text-secondary text-sm">
          {inState.length} spots · sorted by current rating.
        </p>
      </header>

      <RegionList spots={inState} sparks={sparks} />
    </div>
  );
}
