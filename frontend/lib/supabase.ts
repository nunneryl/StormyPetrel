import { createClient } from '@supabase/supabase-js';

// Server-only client. We read SUPABASE_URL first because that's the
// canonical secret name in Vercel; NEXT_PUBLIC_SUPABASE_URL is the
// historical fallback for local-dev .env files. Either one works.
const url =
  process.env.SUPABASE_URL ?? process.env.NEXT_PUBLIC_SUPABASE_URL;
const key = process.env.SUPABASE_SERVICE_KEY;

if (!url || !key) {
  throw new Error(
    'Missing Supabase env. Set SUPABASE_URL (or NEXT_PUBLIC_SUPABASE_URL) and SUPABASE_SERVICE_KEY in the environment.',
  );
}

export const supabase = createClient(url, key, {
  auth: { persistSession: false, autoRefreshToken: false },
});

// The same database, read past Next's fetch cache (the Vercel Data Cache). A plain read on a page
// with `revalidate` is stored in that cache under its URL, and a deploy's build is handed the
// stored copy, however old, instead of asking the database: a spot row cached before migration
// 026 kept showing no time_zone, and so Los Angeles times, through a redeploy without build cache,
// because the Data Cache is not the build cache. Only for a route that sets
// `dynamic = 'force-static'`: elsewhere a 'no-store' read turns the whole page dynamic.
export const supabaseUncached = createClient(url, key, {
  auth: { persistSession: false, autoRefreshToken: false },
  global: { fetch: (input, init) => fetch(input, { ...init, cache: 'no-store' }) },
});
