/**
 * Recording site errors and keeping the site-critical issue, server side. The rules are in
 * lib/siteErrors.ts; this file only talks to the database and to GitHub.
 *
 * NEVER THROWS. Reporting an error must not cause one: every failure is logged and swallowed,
 * and every outside call has a timeout.
 *
 * ENVIRONMENT
 *   GITHUB_ALERT_TOKEN  a fine-grained token with Issues read/write on this repository only.
 *                       Without it errors are still recorded; the issue is just never touched.
 *   SITE_ALERT_REPO     owner/name, default nunneryl/StormyPetrel.
 */
import { supabaseUncached } from './supabase';
import { siteUrl } from './site-url';
import {
  type ErrorReport, type ErrorRow, type IssueAction,
  ISSUE_LABEL, WINDOW_MINUTES,
  changeComment, cleanPath, closeComment, criticalNow, fingerprintOf, issueBody, issueTitle,
  normalizeMessage, planIssue, readMarker, routeOf, signature, withMarker,
} from './siteErrors';

const TIMEOUT_MS = 8000;
const REPO = (process.env.SITE_ALERT_REPO || 'nunneryl/StormyPetrel').trim();

/** Record one error. Returns the fingerprint's count in its current window, or null on failure. */
export async function recordError(report: ErrorReport): Promise<{ windowCount: number; accepted: boolean } | null> {
  try {
    const fingerprint = await fingerprintOf(report.source, report.route, report.message);
    const { data, error } = await supabaseUncached
      .rpc('record_site_error', {
        p_fingerprint: fingerprint,
        p_source: report.source,
        p_route: report.route,
        p_message: report.message,
        p_path: report.path,
        p_digest: report.digest,
        p_window_minutes: WINDOW_MINUTES,
      })
      .abortSignal(AbortSignal.timeout(TIMEOUT_MS));
    if (error) {
      console.error('site-errors: record failed:', error.message);
      return null;
    }
    const row = (Array.isArray(data) ? data[0] : data) as { window_count?: number; accepted?: boolean } | null;
    return row ? { windowCount: Number(row.window_count ?? 0), accepted: Boolean(row.accepted) } : null;
  } catch (err) {
    console.error('site-errors: record failed:', err instanceof Error ? err.message : err);
    return null;
  }
}

/** A server error from onRequestError (instrumentation.ts): record it, then check whether the
 *  site has gone critical. `routePath` is Next's route pattern (/spot/[slug]). */
export async function reportServerError(
  err: unknown,
  path: string,
  routePath: string | undefined,
  digest: string | undefined,
): Promise<void> {
  const cleaned = cleanPath(path);
  await recordError({
    source: 'server',
    route: routePath && routePath.startsWith('/') ? routePath.slice(0, 300) : routeOf(cleaned),
    message: normalizeMessage(err instanceof Error ? err.message : err),
    path: cleaned,
    digest: digest ? digest.slice(0, 100) : null,
  });
  await syncSiteCritical(Date.now());
}

// ------------------------------------------------------------------------------------------------
// The site-critical issue.
// ------------------------------------------------------------------------------------------------
type Issue = { number: number; body: string | null };

async function github(method: string, path: string, token: string, body?: unknown): Promise<unknown> {
  const res = await fetch(`https://api.github.com${path}`, {
    method,
    headers: {
      authorization: `Bearer ${token}`,
      accept: 'application/vnd.github+json',
      'x-github-api-version': '2022-11-28',
      'user-agent': 'stormypetrel-site-alerts',
      ...(body ? { 'content-type': 'application/json' } : {}),
    },
    body: body ? JSON.stringify(body) : undefined,
    cache: 'no-store',
    signal: AbortSignal.timeout(TIMEOUT_MS),
  });
  if (res.status === 404) return null;
  if (!res.ok) throw new Error(`GitHub ${method} ${path}: ${res.status}`);
  return res.status === 204 ? null : res.json();
}

/**
 * Open, comment on, touch or close the site-critical issue as lib/siteErrors.planIssue says.
 * Called after every server error and by /api/health (every 15 minutes from the outside check),
 * which is what closes the issue once things are clean. One instance at a time, through the
 * database lease. Returns what it did, for the health response and the logs.
 */
export async function syncSiteCritical(nowMs: number): Promise<IssueAction | 'no-token' | 'busy' | 'failed'> {
  const token = process.env.GITHUB_ALERT_TOKEN?.trim();
  if (!token) return 'no-token';
  try {
    const lease = await supabaseUncached
      .rpc('try_site_alert_lease', { p_seconds: 60 })
      .abortSignal(AbortSignal.timeout(TIMEOUT_MS));
    if (lease.error || lease.data !== true) return 'busy';

    const since = new Date(nowMs - WINDOW_MINUTES * 60_000).toISOString();
    const { data, error } = await supabaseUncached
      .from('site_errors')
      .select('fingerprint, source, route, count, last_seen, window_start, window_count')
      .eq('source', 'server')
      .gte('last_seen', since)
      .abortSignal(AbortSignal.timeout(TIMEOUT_MS));
    if (error) throw new Error(`site_errors read: ${error.message}`);
    const critical = criticalNow((data ?? []) as ErrorRow[], nowMs);

    const open = (await github('GET',
      `/repos/${REPO}/issues?state=open&labels=${ISSUE_LABEL}&per_page=10&sort=created&direction=asc`, token)) as Issue[] | null;
    const issue = open && open.length > 0 ? open[0] : null;
    const action = planIssue(critical, issue ? issue.body ?? '' : null, nowMs);
    const nowIso = new Date(nowMs).toISOString();

    if (action === 'open') {
      if ((await github('GET', `/repos/${REPO}/labels/${ISSUE_LABEL}`, token)) === null) {
        await github('POST', `/repos/${REPO}/labels`, token, {
          name: ISSUE_LABEL, color: 'b60205', description: 'The live site is throwing server errors',
        });
      }
      await github('POST', `/repos/${REPO}/issues`, token, {
        title: issueTitle(), body: issueBody(critical, siteUrl(), nowIso), labels: [ISSUE_LABEL],
      });
    } else if (action === 'comment' && issue) {
      await github('POST', `/repos/${REPO}/issues/${issue.number}/comments`, token, { body: changeComment(critical, nowIso) });
      await github('PATCH', `/repos/${REPO}/issues/${issue.number}`, token, {
        body: withMarker(issue.body, { signature: signature(critical), last_critical_at: nowIso }),
      });
    } else if (action === 'touch' && issue) {
      // Same news: no comment, only the time it was last seen critical, so the close clock restarts.
      await github('PATCH', `/repos/${REPO}/issues/${issue.number}`, token, {
        body: withMarker(issue.body, { signature: signature(critical), last_critical_at: nowIso }),
      });
    } else if (action === 'close' && issue) {
      await github('POST', `/repos/${REPO}/issues/${issue.number}/comments`, token, { body: closeComment(nowIso) });
      await github('PATCH', `/repos/${REPO}/issues/${issue.number}`, token, {
        state: 'closed', state_reason: 'completed',
        body: withMarker(issue.body, { signature: [], last_critical_at: readMarker(issue.body)?.last_critical_at ?? null }),
      });
    }
    if (action !== 'none') console.info(`site-errors: site-critical ${action}`);
    return action;
  } catch (err) {
    console.error('site-errors: site-critical sync failed:', err instanceof Error ? err.message : err);
    return 'failed';
  }
}
