import type { Metadata, Viewport } from 'next';
import './globals.css';
import { siteUrl } from '@/lib/site-url';
import { SiteNav, type SpotSearchItem } from '@/components/SiteNav';
import { SiteFooter } from '@/components/SiteFooter';
import { fetchAllSpots } from '@/lib/queries';

const SITE_URL = siteUrl();

export const metadata: Metadata = {
  metadataBase: new URL(SITE_URL),
  title: {
    default: 'Stormy Petrel — Free US Surf Forecasts',
    template: '%s · Stormy Petrel',
  },
  description:
    'Free surf forecasts for ~500 US spots. No paywall, no ads. Built on NOAA NWPS, NDBC, gfswave (WAVEWATCH III) and HRRR data.',
  applicationName: 'Stormy Petrel',
  keywords: [
    'surf forecast', 'surf report', 'free surf forecast', 'NOAA surf',
    'wave forecast', 'swell forecast', 'tide chart', 'WAVEWATCH III', 'HRRR',
    'US surf spots', 'east coast surf', 'west coast surf', 'hawaii surf',
  ],
  authors: [{ name: 'Stormy Petrel' }],
  category: 'weather',
  openGraph: {
    type: 'website',
    siteName: 'Stormy Petrel',
    title: 'Stormy Petrel — Free US Surf Forecasts',
    description:
      'Free surf forecasts for ~500 US spots. No paywall, no ads. Built on NOAA data.',
    url: SITE_URL,
    locale: 'en_US',
  },
  twitter: {
    card: 'summary_large_image',
    title: 'Stormy Petrel — Free US Surf Forecasts',
    description:
      'Free surf forecasts for ~500 US spots. No paywall, no ads. Built on NOAA data.',
  },
  robots: {
    index: true,
    follow: true,
    googleBot: {
      index: true,
      follow: true,
      'max-snippet': -1,
      'max-image-preview': 'large',
    },
  },
};

export const viewport: Viewport = {
  themeColor: '#FFFFFF', // matches the new white nav bar
  width: 'device-width',
  initialScale: 1,
};

export default async function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  // Fetch spot list once for the nav search dropdown. Failure is silent
  // (Vercel preview / build env may not have DB access) — the nav just
  // renders without the search box in that case.
  let searchSpots: SpotSearchItem[] = [];
  try {
    const all = await fetchAllSpots();
    searchSpots = all.map((s) => ({ slug: s.slug, name: s.name, state: s.state }));
  } catch {
    // ignore — nav handles empty list
  }

  return (
    <html lang="en">
      <body className="min-h-screen bg-ink-950 text-text-primary antialiased flex flex-col">
        <SiteNav searchSpots={searchSpots} />
        <main className="flex-1">{children}</main>
        <SiteFooter />
      </body>
    </html>
  );
}
