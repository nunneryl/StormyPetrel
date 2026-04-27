import { fetchSpotsWithLatest } from '@/lib/queries';
import { SpotMap } from '@/components/SpotMap';
import { RATING_TIERS } from '@/lib/ratings';

export const dynamic = 'force-dynamic';
export const revalidate = 600;

export default async function MapPage() {
  const spots = await fetchSpotsWithLatest();
  return (
    <div className="relative">
      <div className="absolute z-10 top-3 left-3 right-3 sm:right-auto rounded border border-ink-700 bg-ink-900/90 backdrop-blur p-3 max-w-sm pointer-events-auto">
        <div className="text-xs uppercase tracking-widest text-slate-400 mb-1">
          {spots.length} spots
        </div>
        <div className="flex flex-wrap gap-1.5 text-[10px] font-bold uppercase">
          {RATING_TIERS.map((t) => (
            <span
              key={t.label}
              className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-white"
              style={{ background: t.hex }}
            >
              {t.label}
            </span>
          ))}
        </div>
      </div>
      <SpotMap spots={spots} />
    </div>
  );
}
