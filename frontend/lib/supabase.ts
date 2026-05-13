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
