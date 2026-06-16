const path = require('path');

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  // Pin tracing root so Next stops climbing past frontend/ into the
  // home-directory lockfile it discovered upstream.
  outputFileTracingRoot: path.join(__dirname),
  experimental: {
    serverActions: {
      bodySizeLimit: '2mb',
    },
  },
  // 308 permanent redirect from the old /blog/about post URL to the
  // standalone /about page so external links + already-indexed
  // search results stay live.
  async redirects() {
    return [
      { source: '/blog/about', destination: '/about', permanent: true },
      { source: '/spot/trees', destination: '/spot/3-mile', permanent: true },
      { source: '/spot/little-wind-an-sea', destination: '/spot/wind-and-sea', permanent: true },
      { source: '/spot/p-b-boys-club', destination: '/spot/palm-beach', permanent: true },
      { source: '/spot/antonio-s-rincon', destination: '/spot/antonio-s', permanent: true },
      { source: '/spot/sandy-beach-rincon', destination: '/spot/sandy-beach', permanent: true },
      { source: '/spot/pools-rincon', destination: '/spot/pools', permanent: true },
      { source: '/spot/marias-rincon', destination: '/spot/maria-s', permanent: true },
      { source: '/spot/indicators-rincon', destination: '/spot/indicators', permanent: true },
    ];
  },
};

module.exports = nextConfig;
