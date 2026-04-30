import type { Metadata, Viewport } from 'next';
import './globals.css';
import Link from 'next/link';
import { siteUrl } from '@/lib/site-url';

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
  themeColor: '#04080f',
  width: 'device-width',
  initialScale: 1,
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body className="min-h-screen bg-ink-950 text-slate-100 antialiased">
        <header className="border-b border-ink-700/60 bg-ink-900/80 backdrop-blur sticky top-0 z-30">
          <div className="mx-auto max-w-7xl px-4 py-3 flex items-center justify-between">
            <Link href="/" className="flex items-center gap-2 group">
              <span className="text-sea-400 font-bold tracking-tight text-xl">
                Stormy Petrel
              </span>
              <span className="hidden sm:inline text-xs uppercase tracking-widest text-slate-500 group-hover:text-slate-300">
                surf
              </span>
            </Link>
            <nav className="flex items-center gap-1 sm:gap-3 text-sm">
              <Link href="/map" className="px-2 py-1 text-slate-300 hover:text-white">
                Map
              </Link>
              <Link href="/regions" className="px-2 py-1 text-slate-300 hover:text-white">
                Regions
              </Link>
            </nav>
          </div>
        </header>
        <main>{children}</main>
        <footer className="border-t border-ink-700/60 text-slate-500 text-xs py-6 mt-12">
          <div className="mx-auto max-w-7xl px-4 flex flex-col sm:flex-row items-start sm:items-center justify-between gap-2">
            <span>
              Stormy Petrel · Free surf forecasts · Powered by NOAA, NWPS, NDBC, and CO-OPS data.
            </span>
            <a
              href="https://github.com/nunneryl/StormyPetrel"
              className="hover:text-slate-300"
              target="_blank"
              rel="noreferrer"
            >
              GitHub
            </a>
          </div>
        </footer>
      </body>
    </html>
  );
}
