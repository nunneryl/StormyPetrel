import { fetchSpotsWithLatest } from '@/lib/queries';
import { SpotMap } from '@/components/SpotMap';
import { RATING_TIERS } from '@/lib/ratings';

export const dynamic = 'force-dynamic';
export const revalidate = 600;

export default async function MapPage() {
  const spots = await fetchSpotsWithLatest();
  return (
    <div className="relative">
      {/* Top-left: small spot count chip — un-obtrusive */}
      <div className="absolute z-10 top-3 left-3 rounded-md border border-ink-600 bg-white/90 backdrop-blur-sm px-2.5 py-1.5 shadow-card">
        <span className="text-[11px] uppercase tracking-widest2 text-text-secondary tabular-nums">
          {spots.length} spots
        </span>
      </div>

      {/* Bottom-right: permanent rating legend, single horizontal row */}
      <div className="absolute z-10 bottom-4 right-4 sm:right-16 rounded-md border border-ink-600 bg-white/90 backdrop-blur-sm px-3 py-2 shadow-card">
        <div className="flex items-center gap-3 sm:gap-4 flex-wrap">
          {RATING_TIERS.map((t) => (
            <span
              key={t.label}
              className="inline-flex items-center gap-1.5 text-[10px] font-bold uppercase tracking-widest2 text-text-secondary whitespace-nowrap"
            >
              <span
                className="w-2.5 h-2.5 rounded-full shrink-0"
                style={{ background: t.hex, boxShadow: `0 0 4px ${t.glow}` }}
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
