import { fetchSpotsWithLatest } from '@/lib/queries';
import { SpotMap } from '@/components/SpotMap';
import { RATING_TIERS } from '@/lib/ratings';

export const dynamic = 'force-dynamic';
export const revalidate = 600;

export default async function MapPage() {
  const spots = await fetchSpotsWithLatest();
  return (
    <div className="relative">
      {/* Legend overlay — top-left on desktop, full-width banner on mobile */}
      <div className="absolute z-10 top-3 left-3 right-3 sm:right-auto sm:max-w-md rounded-xl border border-ink-600 bg-ink-900/90 backdrop-blur-sm p-3 shadow-lg">
        <div className="flex items-baseline justify-between mb-2">
          <span className="text-[10px] uppercase tracking-widest2 text-text-secondary">
            {spots.length} spots
          </span>
          <span className="text-[10px] uppercase tracking-widest2 text-text-muted">
            Rating now
          </span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {RATING_TIERS.map((t) => (
            <span
              key={t.label}
              className="inline-flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest2 text-text-primary"
            >
              <span
                className="w-2.5 h-2.5 rounded-full"
                style={{ background: t.hex, boxShadow: `0 0 6px ${t.glow}` }}
              />
              {t.label}
            </span>
          ))}
        </div>
      </div>
      <SpotMap spots={spots} />
    </div>
  );
}
