import type { MetadataRoute } from 'next';
import { RATED_SPOT_COUNT } from '@/lib/siteDescription';

// The web app manifest, served at /manifest.webmanifest and linked from every page's <head> by
// Next. The icons are the SP monogram the favicon and the nav use (public/brand/sp-initials.png),
// on white: 192 and 512 for Android and desktop installs, and a 512 maskable copy with the art
// inside the safe zone, for launchers that crop to a circle. The iPhone home-screen icon is
// app/apple-icon.png (180x180), which Next links as apple-touch-icon.
export default function manifest(): MetadataRoute.Manifest {
  return {
    name: 'Stormy Petrel — Free US Surf Forecasts',
    short_name: 'Stormy Petrel',
    description: `Free surf forecasts for ${RATED_SPOT_COUNT} US spots. No paywall, no ads.`,
    start_url: '/',
    display: 'standalone',
    background_color: '#FFFFFF',
    theme_color: '#FFFFFF',
    icons: [
      { src: '/icons/icon-192.png', sizes: '192x192', type: 'image/png' },
      { src: '/icons/icon-512.png', sizes: '512x512', type: 'image/png' },
      { src: '/icons/icon-maskable-512.png', sizes: '512x512', type: 'image/png', purpose: 'maskable' },
    ],
  };
}
