import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import { fetchAllSpots, fetchSpotsWithLatest, fetchSparklineData } from '@/lib/queries';
import { fetchCamSlugSet } from '@/lib/cams';
import { RegionList } from '@/components/RegionList';

export const revalidate = 900;

type Params = { state: string };

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { state } = await params;
  const decoded = decodeURIComponent(state);
  const pretty = decoded
    .split(/[\s-]+/)
    .map((w) => (w ? w[0].toUpperCase() + w.slice(1).toLowerCase() : ''))
    .join(' ');

  // Count spots in this state so the title carries the precise N.
  // Failures fall back to a generic count-less description so the
  // page still ships valid metadata.
  let count = 0;
  try {
    const spots = await fetchAllSpots();
    count = spots.filter(
      (s) => (s.state ?? '').toLowerCase() === decoded.toLowerCase(),
    ).length;
  } catch {
    // ignore — leave count at 0; description below handles 0 gracefully
  }

  const title = `${pretty} Surf Forecast${count ? ` — ${count} Spots` : ''} | Stormy Petrel`;
  const description = count
    ? `Free surf forecasts for ${count} spots in ${pretty}. Wave height, swell, wind, and tide for every break.`
    : `Free surf forecasts for ${pretty}. Wave height, swell, wind, and tide for every break.`;
  return {
    title: { absolute: title },
    description,
    alternates: { canonical: `/region/${encodeURIComponent(decoded.toLowerCase())}` },
    openGraph: { title, description, type: 'website' },
    twitter: { card: 'summary_large_image', title, description },
  };
}

export default async function RegionPage({ params }: { params: Promise<Params> }) {
  const { state } = await params;
  const decoded = decodeURIComponent(state).toLowerCase();
  const [spots, sparksMap, camSlugs] = await Promise.all([
    fetchSpotsWithLatest(),
    fetchSparklineData(),
    fetchCamSlugSet(),
  ]);
  const inState = spots.filter((s) => (s.state ?? '').toLowerCase() === decoded);
  if (inState.length === 0) notFound();

  inState.sort((a, b) => (b.latest?.stars ?? -1) - (a.latest?.stars ?? -1));
  const stateLabel = inState[0].state ?? decoded;

  // RegionList is a client component — convert collections to plain
  // structures so they cross the server / client boundary.
  const sparks: Record<number, number[]> = {};
  sparksMap.forEach((v, k) => { sparks[k] = v; });
  const camSlugArr = Array.from(camSlugs);

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

      <RegionList spots={inState} sparks={sparks} camSlugs={camSlugArr} />
    </div>
  );
}
