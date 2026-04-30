import type { MetadataRoute } from 'next';
import { fetchAllSpots } from '@/lib/queries';
import { siteUrl } from '@/lib/site-url';

const SITE_URL = siteUrl();

// Regenerate every hour. Forecast pages themselves are force-dynamic, so
// the sitemap doesn't need to update faster than spot inventory changes.
export const revalidate = 3600;

export default async function sitemap(): Promise<MetadataRoute.Sitemap> {
  const now = new Date();

  const staticEntries: MetadataRoute.Sitemap = [
    { url: SITE_URL, lastModified: now, changeFrequency: 'hourly', priority: 1.0 },
    { url: `${SITE_URL}/map`, lastModified: now, changeFrequency: 'hourly', priority: 0.9 },
    { url: `${SITE_URL}/regions`, lastModified: now, changeFrequency: 'daily', priority: 0.6 },
  ];

  let spots: Awaited<ReturnType<typeof fetchAllSpots>> = [];
  try {
    spots = await fetchAllSpots();
  } catch (err) {
    // Build environments without DB access still get a valid sitemap stub —
    // empty spot list rather than a 500.
    console.error('sitemap: fetchAllSpots failed:', err);
  }

  const states = new Set<string>();
  for (const s of spots) {
    if (s.state) states.add(s.state);
  }
  const stateEntries: MetadataRoute.Sitemap = Array.from(states).map((state) => ({
    url: `${SITE_URL}/region/${encodeURIComponent(state.toLowerCase())}`,
    lastModified: now,
    changeFrequency: 'hourly',
    priority: 0.7,
  }));

  const spotEntries: MetadataRoute.Sitemap = spots.map((s) => ({
    url: `${SITE_URL}/spot/${encodeURIComponent(s.slug)}`,
    lastModified: now,
    changeFrequency: 'hourly',
    priority: 0.8,
  }));

  return [...staticEntries, ...stateEntries, ...spotEntries];
}
