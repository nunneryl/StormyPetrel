import type { Metadata } from 'next';
import { fetchAllActiveCams } from '@/lib/cams';
import { fetchSpotsWithLatest } from '@/lib/queries';
import { CamsBrowser, type CamRow } from '@/components/CamsBrowser';

export const revalidate = 900;

export const metadata: Metadata = {
  title: {
    absolute: 'Live US Surf Cams — Every Cam We Track | Stormy Petrel',
  },
  description:
    'Every live surf cam we track, in one place. YouTube and SurfChex feeds for the Atlantic, Gulf, Pacific, Hawaii, and Puerto Rico. Filter by state, search by spot name.',
  alternates: { canonical: '/cams' },
  openGraph: {
    title: 'Live US Surf Cams | Stormy Petrel',
    description:
      'Every live surf cam we track, in one place. Click through for the full forecast.',
    type: 'website',
  },
};

export default async function CamsPage() {
  const [cams, spots] = await Promise.all([
    fetchAllActiveCams(),
    fetchSpotsWithLatest(),
  ]);
  const spotBySlug = new Map(spots.map((s) => [s.slug, s]));

  // Snapshot only the spot fields the client component reads. Keeps
  // the server-to-client payload tight (each cam carries one tiny
  // spot object instead of the entire latest-forecast row).
  const rows: CamRow[] = cams.map((cam) => {
    const s = cam.spot_slug ? spotBySlug.get(cam.spot_slug) : undefined;
    return {
      cam,
      spot: s
        ? {
            slug: s.slug,
            name: s.name,
            state: s.state,
            stars: s.latest?.stars ?? null,
            face_ft: s.latest?.face_ft ?? null,
          }
        : null,
    };
  });

  return (
    <div className="mx-auto max-w-6xl px-4 sm:px-6 py-7 space-y-4">
      <header>
        <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">
          Live cams
        </div>
        <h1 className="text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          US surf cams
        </h1>
        <p className="mt-1 text-text-secondary text-sm">
          Filter by state, search by spot name, or tap a card to watch + see
          the full forecast.
        </p>
      </header>

      <CamsBrowser rows={rows} totalCount={rows.length} />
    </div>
  );
}
