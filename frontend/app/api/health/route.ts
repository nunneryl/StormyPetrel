import { supabaseUncached } from '@/lib/supabase';
import { healthFrom } from '@/lib/health';
import { syncSiteCritical } from '@/lib/siteErrorsServer';

// GET /api/health, for the outside uptime check (cron-job.org, every 15 minutes).
//
//   200  the database answered and the last pipeline run that wrote forecasts is under 12 h old
//   503  the database did not answer, no run is recorded, or the last run is 12 h old or more
//
// The verdict is lib/health.ts healthFrom. Every call also runs the site-critical issue check
// (lib/siteErrorsServer.ts syncSiteCritical): this regular call is what closes the issue once
// nothing has been critical for an hour. That check never changes the status code.
export const dynamic = 'force-dynamic';
export const runtime = 'nodejs';

export async function GET(): Promise<Response> {
  const nowMs = Date.now();
  let lastRun: string | null = null;
  let dbError: string | null = null;
  try {
    const { data, error } = await supabaseUncached
      .from('nwps_office_freshness')
      .select('fetched_at')
      .order('fetched_at', { ascending: false })
      .limit(1)
      .abortSignal(AbortSignal.timeout(8000));
    if (error) dbError = error.message;
    else lastRun = ((data ?? [])[0] as { fetched_at?: string } | undefined)?.fetched_at ?? null;
  } catch (err) {
    dbError = err instanceof Error ? err.message : String(err);
  }
  if (dbError) console.error('health: database read failed:', dbError);

  const health = healthFrom(lastRun, nowMs, dbError);
  // At most 15 s, so a slow GitHub cannot hold the check past the outside monitor's timeout.
  const alerts = await Promise.race([
    syncSiteCritical(nowMs),
    new Promise<'timeout'>((resolve) => setTimeout(() => resolve('timeout'), 15_000)),
  ]);
  const { http, ...body } = health;
  return Response.json(
    { ...body, checked_at: new Date(nowMs).toISOString(), alerts },
    { status: http, headers: { 'cache-control': 'no-store' } },
  );
}
