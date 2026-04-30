/**
 * Resolve the public site URL with defensive trimming.
 *
 * Vercel env-var inputs occasionally carry trailing whitespace that
 * silently corrupts every URL we concatenate (sitemap entries, robots
 * Host line, OpenGraph absolute URLs, canonical links). Centralizing the
 * read + sanitize keeps that bug isolated to one place.
 */
export function siteUrl(): string {
  const raw = process.env.NEXT_PUBLIC_SITE_URL ?? 'https://stormypetrel.surf';
  // Strip whitespace, then trailing slash so callers can do `${url}/path`
  // without producing `//path`.
  return raw.trim().replace(/\/+$/, '');
}
