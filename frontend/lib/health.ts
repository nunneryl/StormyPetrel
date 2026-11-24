/**
 * /api/health's verdict, pure. HEALTHY when the database answers and the last pipeline run that
 * wrote forecasts is under MAX_RUN_AGE_HOURS old. "Last run" is the newest fetched_at in
 * nwps_office_freshness (migration 029): one row per NWPS office per full pipeline run, written
 * last in the run, with an index on fetched_at, so the check is one indexed read and not a scan
 * of the forecasts table. Full runs are about six hours apart, so twelve hours is two missed runs.
 */
export const MAX_RUN_AGE_HOURS = 12;

export type Health =
  | { status: 'ok' | 'stale'; http: 200 | 503; last_pipeline_run: string; age_hours: number }
  | { status: 'error'; http: 503; last_pipeline_run: null; age_hours: null; reason: string };

export function healthFrom(lastRunIso: string | null | undefined, nowMs: number, dbError: string | null): Health {
  if (dbError) return { status: 'error', http: 503, last_pipeline_run: null, age_hours: null, reason: 'database' };
  const t = lastRunIso ? Date.parse(lastRunIso) : Number.NaN;
  if (!Number.isFinite(t)) {
    return { status: 'error', http: 503, last_pipeline_run: null, age_hours: null, reason: 'no pipeline run recorded' };
  }
  const age = Math.round(((nowMs - t) / 3_600_000) * 10) / 10;
  return age < MAX_RUN_AGE_HOURS
    ? { status: 'ok', http: 200, last_pipeline_run: new Date(t).toISOString(), age_hours: age }
    : { status: 'stale', http: 503, last_pipeline_run: new Date(t).toISOString(), age_hours: age };
}
