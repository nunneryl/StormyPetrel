import { NextRequest } from 'next/server';
import { MAX_BODY_BYTES, RateLimiter, parseClientReport } from '@/lib/siteErrors';
import { recordError } from '@/lib/siteErrorsServer';

// POST /api/client-error: a browser error from app/error.tsx or app/global-error.tsx
// (lib/reportClientError.ts). Body { message, path, digest? }, at most MAX_BODY_BYTES.
//
// ANYONE CAN POST HERE, so:
//   * the body is size-capped (413) and must parse to the three fields (400);
//   * each server instance lets one client send 10 a minute and everyone 120 a minute (429).
//     The client's address is used only as an in-memory counter key for that minute; it is
//     never stored or logged;
//   * the database refuses NEW browser fingerprints past 500 a day (record_site_error);
//   * browser errors never make the site critical (lib/siteErrors criticalNow).
// Nothing personal is recorded: no address, browser details, cookies or query string.
export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

const perClient = new RateLimiter(10, 60_000);
const everyone = new RateLimiter(120, 60_000);

export async function POST(req: NextRequest): Promise<Response> {
  const declared = Number(req.headers.get('content-length') ?? '0');
  if (Number.isFinite(declared) && declared > MAX_BODY_BYTES) return new Response(null, { status: 413 });
  const text = await req.text();
  if (new TextEncoder().encode(text).length > MAX_BODY_BYTES) return new Response(null, { status: 413 });

  const nowMs = Date.now();
  const client = req.headers.get('x-forwarded-for')?.split(',')[0]?.trim() || 'unknown';
  if (!everyone.allow('all', nowMs) || !perClient.allow(client, nowMs)) {
    return new Response(null, { status: 429 });
  }

  let raw: unknown;
  try {
    raw = JSON.parse(text);
  } catch {
    return new Response(null, { status: 400 });
  }
  const parsed = parseClientReport(raw);
  if (!parsed.ok) return new Response(null, { status: 400 });

  await recordError(parsed.report);
  return new Response(null, { status: 204 });
}
