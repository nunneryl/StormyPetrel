import type { Metadata } from 'next';
import Link from 'next/link';
import { fetchAllActiveCams, providerLabel } from '@/lib/cams';
import { fetchSpotsWithLatest } from '@/lib/queries';
import { StarRating } from '@/components/StarRating';

export const dynamic = 'force-dynamic';
export const revalidate = 600;

export const metadata: Metadata = {
  title: {
    absolute: 'Live US Surf Cams — Every Cam We Track | Stormy Petrel',
  },
  description:
    'Every live surf cam we track, in one place. YouTube and SurfChex feeds for the Atlantic, Gulf, Pacific, Hawaii, and Puerto Rico. Click through for the full forecast.',
  alternates: { canonical: '/cams' },
  openGraph: {
    title: 'Live US Surf Cams | Stormy Petrel',
    description:
      'Every live surf cam we track, in one place. Click through for the full forecast.',
    type: 'website',
  },
};

const PROVIDER_BG: Record<string, string> = {
  youtube:  'bg-red-100 text-red-700',
  surfchex: 'bg-cyan-100 text-cyan-700',
  explore:  'bg-emerald-100 text-emerald-700',
  hdontap:  'bg-violet-100 text-violet-700',
  nysea:    'bg-amber-100 text-amber-700',
};

export default async function CamsPage() {
  const [cams, spots] = await Promise.all([
    fetchAllActiveCams(),
    fetchSpotsWithLatest(),
  ]);

  // Join each cam to its spot for the star rating + display name.
  const spotBySlug = new Map(spots.map((s) => [s.slug, s]));

  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6 py-7 space-y-6">
      <header>
        <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">
          Live cams
        </div>
        <h1 className="text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          US surf cams
        </h1>
        <p className="mt-1 text-text-secondary text-sm">
          {cams.length} live feeds. Tap any card to watch + see the full forecast.
        </p>
      </header>

      {cams.length === 0 ? (
        <div className="rounded-xl border border-ink-600 bg-white p-8 text-center text-text-muted text-sm">
          No active cams right now.
        </div>
      ) : (
        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
          {cams.map((c) => {
            const spot = c.spot_slug ? spotBySlug.get(c.spot_slug) : undefined;
            const providerCls =
              PROVIDER_BG[c.provider] ?? 'bg-ink-800 text-text-secondary';
            return (
              <Link
                key={c.id}
                href={spot ? `/spot/${spot.slug}` : '/cams'}
                className="group rounded-xl border border-ink-600 bg-white shadow-card hover:border-cyan-500 transition overflow-hidden flex flex-col"
              >
                <div className="relative w-full bg-ink-900" style={{ paddingTop: '56.25%' }}>
                  <ThumbnailFor cam={c} />
                </div>
                <div className="p-4 space-y-2 grow">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="font-bold text-text-primary group-hover:text-cyan-600 truncate">
                        {spot?.name ?? c.spot_slug}
                      </div>
                      <div className="text-xs text-text-secondary truncate">
                        {c.cam_name}
                      </div>
                    </div>
                    <span className={`shrink-0 inline-flex items-center px-2 py-0.5 rounded text-[10px] font-bold uppercase tracking-widest2 ${providerCls}`}>
                      {providerLabel(c.provider)}
                    </span>
                  </div>
                  <div className="flex items-center justify-between gap-2">
                    {spot?.latest?.stars !== undefined && (
                      <StarRating score={spot.latest.stars} size="sm" />
                    )}
                    <span className="text-xs font-bold text-cyan-600 group-hover:underline">
                      Watch live →
                    </span>
                  </div>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

// Thumbnail strategy:
//   - YouTube cams with a resolved video ID → grab YouTube's auto-generated
//     thumbnail; it's free and updates every few minutes.
//   - Anything else (surfchex page-iframe, explore embeds) → a placeholder
//     glyph; we can't reach into the third-party feed for a frame.
function ThumbnailFor({ cam }: { cam: import('@/lib/cams').Cam }) {
  if (cam.provider === 'youtube' && cam.resolved_video_id) {
    const thumb = `https://img.youtube.com/vi/${cam.resolved_video_id}/mqdefault.jpg`;
    return (
      // eslint-disable-next-line @next/next/no-img-element
      <img
        src={thumb}
        alt={cam.cam_name}
        className="absolute inset-0 w-full h-full object-cover"
        loading="lazy"
      />
    );
  }
  return (
    <div className="absolute inset-0 flex items-center justify-center text-text-muted">
      <svg
        width="36"
        height="36"
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M23 7l-7 5 7 5V7z" />
        <rect x="1" y="5" width="15" height="14" rx="2" ry="2" />
      </svg>
    </div>
  );
}
