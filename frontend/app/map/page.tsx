import type { Metadata } from 'next';
import { fetchSpotsWithLatest } from '@/lib/queries';
import { fetchCamSlugSet } from '@/lib/cams';
import { SpotMap } from '@/components/SpotMap';
import { tierFromStars } from '@/lib/ratings';

export const revalidate = 900;

export const metadata: Metadata = {
  title: { absolute: 'Surf Spot Map — 484 US Spots | Stormy Petrel' },
  description:
    'Interactive map of 484 US surf spots with live ratings, wave height, and conditions. Find the best surf near you.',
  alternates: { canonical: '/map' },
  openGraph: {
    title: 'Surf Spot Map — 484 US Spots | Stormy Petrel',
    description:
      'Interactive map of 484 US surf spots with live ratings, wave height, and conditions. Find the best surf near you.',
    type: 'website',
  },
};

// Compact legend — five canonical tier labels (the in-between ones
// like "POOR TO FAIR" stay in the data but aren't repeated as legend
// pills since the visual scale is obvious from the surrounding hues).
const LEGEND = [
  { label: 'EPIC', stars: 5 },
  { label: 'GOOD', stars: 4 },
  { label: 'FAIR', stars: 3 },
  { label: 'POOR', stars: 1 },
  { label: 'FLAT', stars: 0 },
];

export default async function MapPage() {
  const [spots, camSlugs] = await Promise.all([
    fetchSpotsWithLatest(),
    fetchCamSlugSet(),
  ]);
  const camSlugArr = Array.from(camSlugs);
  return (
    <div className="relative">
      {/* Top-left: spot count chip. z-[1100] keeps it above Leaflet's
          internal panes (which max out around z-index 1000). */}
      <div className="absolute z-[1100] top-3 left-3 rounded-md border border-ink-600 bg-white/90 backdrop-blur-sm px-2.5 py-1.5 shadow-card">
        <span className="text-[11px] uppercase tracking-widest2 text-text-secondary tabular-nums">
          {spots.length} spots
        </span>
      </div>

      {/* Bottom-right: permanent rating legend, 5 pills + dots, single row */}
      <div className="absolute z-[1100] bottom-4 right-4 sm:right-16 rounded-lg border border-ink-600 bg-white/95 backdrop-blur-sm px-3 py-2 shadow-card">
        <div className="flex items-center gap-3 sm:gap-4">
          {LEGEND.map((l) => {
            const tier = tierFromStars(l.stars);
            return (
              <span
                key={l.label}
                className="inline-flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest2 text-text-secondary whitespace-nowrap"
              >
                <span
                  className="w-2.5 h-2.5 rounded-full shrink-0"
                  style={{
                    background: tier.hex,
                    border: '1px solid #0F172A',
                  }}
                />
                {l.label}
              </span>
            );
          })}
        </div>
      </div>

      <SpotMap spots={spots} camSlugs={camSlugArr} />
    </div>
  );
}
