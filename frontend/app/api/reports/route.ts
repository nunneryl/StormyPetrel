import { createHmac } from 'node:crypto';
import { NextRequest, NextResponse } from 'next/server';
import { supabase } from '@/lib/supabase';
import {
  submitReport,
  type ReportDb,
  type ReportRow,
  type ReportKey,
  type ReportUpdate,
  type ExistingReport,
} from '@/lib/surfReport';

// POST /api/reports — a visitor's ground-truth surf-size report.
//
// WHY A ROUTE AND NOT A DIRECT CLIENT WRITE. The site has no anon key: lib/supabase.ts is
// built with SUPABASE_SERVICE_KEY and is server-only (no 'use client' file imports it, and
// the var has no NEXT_PUBLIC_ prefix so Next cannot inline it). Writing from the browser
// would mean shipping an anon key and opening PostgREST to public INSERTs, with a CHECK
// constraint as the only validation. Routing through here keeps the entire write surface to
// one function we control, and keeps validation in TypeScript where nonsense is rejected
// before it becomes a row. surf_reports has RLS enabled with no policies (migration 014):
// service_role bypasses it, anon and authenticated get nothing.
//
// This is the site's FIRST unauthenticated write endpoint. There is no middleware, no WAF
// rule in vercel.json, and no rate-limiting dependency, so the abuse controls are the ones
// below plus the UNIQUE constraint, which is the only one a client cannot bypass.

export const runtime = 'nodejs'; // node:crypto for the HMAC

// Truncated to 16 hex chars. Full-length would be no more private and no more useful: 64
// bits is far past any collision concern at this volume, and the shorter value keeps the
// column cheap in the unique index.
const HASH_LENGTH = 16;

/**
 * Non-identifying reporter fingerprint.
 *
 * ROTATION IS WHAT MAKES IT NON-IDENTIFYING, and it is the whole design. A fixed salt would
 * give every visitor one stable pseudonym for life — a durable identifier, i.e. exactly the
 * thing we are avoiding, and re-identifiable by anyone who can brute-force the small space
 * of IP+UA pairs against a known salt. Deriving a PER-DAY key instead means the same person
 * hashes to a different value tomorrow, so reports cannot be linked into a history of one
 * individual. What survives rotation is the only property we actually need: within a single
 * day, one person submitting twice for the same spot-hour collides on the UNIQUE constraint.
 *
 * The secret never leaves the server, and the value is truncated, so the hash is not
 * reversible to an IP. No raw IP and no cookie id is stored anywhere.
 */
function reporterHash(ip: string, userAgent: string, nowMs: number): string | null {
  const secret = process.env.SURF_REPORT_SALT;
  if (!secret) return null;
  const day = new Date(nowMs).toISOString().slice(0, 10); // UTC calendar day
  const dailyKey = createHmac('sha256', secret).update(day).digest();
  return createHmac('sha256', dailyKey)
    .update(`${ip}\n${userAgent}`)
    .digest('hex')
    .slice(0, HASH_LENGTH);
}

function clientIp(req: NextRequest): string {
  // Vercel sets x-forwarded-for; the client's address is the first entry. Falls back to a
  // constant rather than throwing — a missing header must not stop us recording the label,
  // it only means several such reporters share one bucket for the UNIQUE constraint.
  const fwd = req.headers.get('x-forwarded-for');
  if (fwd) return fwd.split(',')[0].trim();
  return req.headers.get('x-real-ip')?.trim() || 'unknown';
}

// Supabase-backed ReportDb. Kept here, not in lib/surfReport.ts, so that module stays
// import-free and testable under bare node with nothing installed.
const db: ReportDb = {
  async findSpotId(slug: string) {
    const { data, error } = await supabase
      .from('spots')
      .select('id')
      .eq('slug', slug)
      .maybeSingle();
    if (error) {
      console.error('reports.findSpotId', error);
      return null;
    }
    return data ? (data as { id: number }).id : null;
  },

  async findForecast(spotId: number, observedHourIso: string) {
    // source='nwps' for the same reason the spot page filters on it: `forecasts` has two
    // writers keyed UNIQUE(spot_id, valid_time, source), and the ecmwf writer produces rows
    // for these same hours carrying hs/tp/dp and nothing else — no face_ft, no stars.
    // Without the filter a report could snapshot an ecmwf row's two NULLs and look like a
    // genuine "we had no forecast".
    const { data, error } = await supabase
      .from('forecasts')
      .select('face_ft, stars')
      .eq('spot_id', spotId)
      .eq('source', 'nwps')
      .eq('valid_time', observedHourIso)
      .maybeSingle();
    if (error) {
      console.error('reports.findForecast', error);
      return null;
    }
    if (!data) return null;
    const row = data as { face_ft: number | null; stars: number | null };
    return { face_ft: row.face_ft ?? null, stars: row.stars ?? null };
  },

  async insert(row: ReportRow) {
    const { error } = await supabase.from('surf_reports').insert(row);
    if (!error) return { ok: true as const };
    // 23505 = unique_violation. That is the UNIQUE(spot_id, observed_hour, reporter_hash)
    // constraint firing on a repeat submission, which submitReport handles by replacing the
    // earlier answer rather than dropping this one.
    const duplicate = error.code === '23505';
    if (!duplicate) console.error('reports.insert', error);
    return { ok: false as const, duplicate, message: error.message };
  },

  async findExisting(key: ReportKey) {
    const { data, error } = await supabase
      .from('surf_reports')
      .select('size_bucket, rating_verdict, revision')
      .eq('spot_id', key.spot_id)
      .eq('observed_hour', key.observed_hour)
      .eq('reporter_hash', key.reporter_hash)
      .maybeSingle();
    if (error) {
      console.error('reports.findExisting', error);
      return null;
    }
    return (data as ExistingReport | null) ?? null;
  },

  async updateAnswer(key: ReportKey, patch: ReportUpdate) {
    // `patch` is a ReportUpdate, which carries no forecast columns — so the original
    // snapshot and first_reported_at survive a revision because they are not in the
    // payload, not because this function remembers to leave them alone.
    const { error } = await supabase
      .from('surf_reports')
      .update(patch)
      .eq('spot_id', key.spot_id)
      .eq('observed_hour', key.observed_hour)
      .eq('reporter_hash', key.reporter_hash);
    if (error) {
      console.error('reports.updateAnswer', error);
      return { ok: false as const, message: error.message };
    }
    return { ok: true as const };
  },
};

export async function POST(req: NextRequest) {
  let raw: unknown;
  try {
    raw = await req.json();
  } catch {
    return NextResponse.json({ ok: false, error: 'invalid json' }, { status: 400 });
  }

  const now = Date.now();
  const hash = reporterHash(clientIp(req), req.headers.get('user-agent') ?? '', now);
  if (hash === null) {
    // Refuse rather than degrade. Without the salt the only options are a fixed-salt hash (a
    // durable identifier) or a constant (which would collapse every reporter onto one bucket
    // and let the UNIQUE constraint reject everyone else's first report for that spot-hour).
    console.error('reports: SURF_REPORT_SALT is not set — refusing to write');
    return NextResponse.json(
      { ok: false, error: 'reporting is temporarily unavailable' },
      { status: 503 },
    );
  }

  const outcome = await submitReport(db, raw, now, hash);
  return NextResponse.json(outcome.body, { status: outcome.status });
}
