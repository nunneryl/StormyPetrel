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
    ];
  },
};

module.exports = nextConfig;
