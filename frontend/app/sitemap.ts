import type { MetadataRoute } from 'next';
import { fetchAllSpots } from '@/lib/queries';
import { fetchReportIndex } from '@/lib/reports';
import { siteUrl } from '@/lib/site-url';
import { listPosts } from '@/lib/blog';
import { LEARN_ARTICLES } from '@/lib/learn';

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
    { url: `${SITE_URL}/reports`, lastModified: now, changeFrequency: 'daily', priority: 0.7 },
    { url: `${SITE_URL}/blog`, lastModified: now, changeFrequency: 'weekly', priority: 0.5 },
    { url: `${SITE_URL}/learn`, lastModified: now, changeFrequency: 'monthly', priority: 0.7 },
    { url: `${SITE_URL}/about`, lastModified: now, changeFrequency: 'monthly', priority: 0.6 },
  ];

  const learnEntries: MetadataRoute.Sitemap = LEARN_ARTICLES.map((a) => ({
    url: `${SITE_URL}/learn/${a.slug}`,
    lastModified: now,
    changeFrequency: 'monthly',
    priority: 0.7,
  }));

  const blogEntries: MetadataRoute.Sitemap = listPosts().map((p) => ({
    url: `${SITE_URL}/blog/${p.slug}`,
    lastModified: new Date(p.date),
    changeFrequency: 'monthly',
    priority: 0.5,
  }));

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

  // One sitemap row per (date, region) report. lastModified uses the
  // row's generated_at so search engines can tell when an existing
  // report URL got refreshed by a same-day re-run.
  let reportIndex: Awaited<ReturnType<typeof fetchReportIndex>> = [];
  try {
    reportIndex = await fetchReportIndex();
  } catch (err) {
    console.error('sitemap: fetchReportIndex failed:', err);
  }
  const reportEntries: MetadataRoute.Sitemap = reportIndex.map((r) => ({
    url: `${SITE_URL}/reports/${r.report_date}/${r.region}`,
    lastModified: new Date(r.generated_at),
    changeFrequency: 'daily',
    priority: 0.6,
  }));

  return [
    ...staticEntries,
    ...blogEntries,
    ...learnEntries,
    ...stateEntries,
    ...spotEntries,
    ...reportEntries,
  ];
}
