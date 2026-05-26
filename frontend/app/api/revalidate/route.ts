import { NextRequest, NextResponse } from 'next/server';
import { revalidatePath } from 'next/cache';

// On-demand revalidation endpoint hit by the pipeline crons after they
// write to Supabase. Each ingestion job POSTs a list of paths it
// just made stale; we drop the ISR cache for each one so the next
// user request sees fresh data instead of waiting for the timed
// revalidate window.
//
// Auth: shared bearer token. Same secret lives in Vercel env vars and
// GitHub Actions secrets. Anything else returns 401.
//
// Body: { "paths": ["/", "/spot/foo", "/region/california", ...] }
//
// We accept either string paths or { path, type } objects so callers
// can hit layout-level revalidation later if needed.

export const runtime = 'nodejs';

type RevalidateItem = string | { path: string; type?: 'layout' | 'page' };

function isAuthorized(req: NextRequest): boolean {
  const secret = process.env.REVALIDATE_SECRET;
  if (!secret) return false;
  const header = req.headers.get('authorization');
  if (!header) return false;
  return header === `Bearer ${secret}`;
}

export async function POST(req: NextRequest) {
  if (!isAuthorized(req)) {
    return NextResponse.json({ ok: false, error: 'unauthorized' }, { status: 401 });
  }

  let body: { paths?: RevalidateItem[] };
  try {
    body = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: 'invalid json' }, { status: 400 });
  }

  const paths = body.paths;
  if (!Array.isArray(paths) || paths.length === 0) {
    return NextResponse.json({ ok: false, error: 'paths[] required' }, { status: 400 });
  }

  let count = 0;
  const errors: string[] = [];
  for (const item of paths) {
    const path = typeof item === 'string' ? item : item?.path;
    const type = typeof item === 'string' ? undefined : item?.type;
    if (!path || typeof path !== 'string' || !path.startsWith('/')) {
      errors.push(`invalid path: ${JSON.stringify(item)}`);
      continue;
    }
    try {
      if (type === 'layout') revalidatePath(path, 'layout');
      else revalidatePath(path);
      count++;
    } catch (err) {
      errors.push(`${path}: ${err instanceof Error ? err.message : String(err)}`);
    }
  }

  return NextResponse.json({ ok: true, revalidated: count, errors });
}
