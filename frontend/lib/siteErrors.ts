/**
 * SITE ERRORS: what is recorded, how errors are grouped, when they are critical, and what the
 * site-critical GitHub issue says. Pure and import-free, so it runs in the node and edge runtimes
 * and tests under bare node; the database and GitHub calls are in lib/siteErrorsServer.ts.
 *
 * WHAT IS RECORDED. The source (server or browser), the route pattern (/spot/[slug]), the
 * error message (normalised, at most MAX_MESSAGE characters), the path it last happened on
 * (no query string), and Next's digest when there is one. Never an IP address, browser details
 * or cookies. One row per FINGERPRINT (source + message + route), upserted, so a repeat bumps a
 * count (pipeline/migrations/033_site_errors.sql).
 *
 * CRITICAL means SERVER errors only, in the last WINDOW_MINUTES:
 *   repeat   one fingerprint SAME_FINGERPRINT_THRESHOLD or more times in its 15-minute window;
 *   spread   ROUTES_THRESHOLD or more different routes with a server error.
 * Browser-reported errors never make it critical: anyone can POST one.
 *
 * THE ISSUE, like scripts/nwps_stale_issue.py: one open issue labelled site-critical; it is
 * opened when the site goes critical, commented on only when WHAT is critical changes, and closed
 * after CLEAN_CLOSE_MINUTES with nothing critical. Its state (what was critical, and when it was
 * last seen critical) lives in a hidden marker in the issue body. The repository is public, so
 * the issue names routes, counts and fingerprints, NEVER messages: a message can carry internals.
 * The messages are on /admin/errors.
 */

export const MAX_MESSAGE = 500;
export const MAX_PATH = 300;
export const MAX_DIGEST = 100;
/** The largest body /api/client-error reads. */
export const MAX_BODY_BYTES = 4096;

export const WINDOW_MINUTES = 15;
export const SAME_FINGERPRINT_THRESHOLD = 10;
export const ROUTES_THRESHOLD = 3;
export const CLEAN_CLOSE_MINUTES = 60;
export const ISSUE_LABEL = 'site-critical';

export type ErrorSource = 'server' | 'browser';

export type ErrorReport = {
  source: ErrorSource;
  route: string;
  message: string;
  path: string;
  digest: string | null;
};

/** A message as stored: whitespace collapsed, long numbers and hex ids replaced, so one error with
 *  a different id each time is still one fingerprint; at most MAX_MESSAGE characters. */
export function normalizeMessage(message: unknown): string {
  const s = typeof message === 'string' ? message : String(message ?? '');
  const out = s
    .replace(/\s+/g, ' ')
    .replace(/\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b/gi, '<uuid>')
    // At least one letter: an all-digit run is a number, handled by the next rule.
    .replace(/\b(?=[0-9a-f]*[a-f])[0-9a-f]{12,}\b/gi, '<hex>')
    .replace(/\d{4,}/g, '<n>')
    .trim();
  return (out || '(no message)').slice(0, MAX_MESSAGE);
}

/** A path as stored: the pathname only (no query string or fragment, which can carry anything),
 *  at most MAX_PATH characters, '/' when there is none. */
export function cleanPath(path: unknown): string {
  if (typeof path !== 'string' || !path.startsWith('/')) return '/';
  const p = path.split(/[?#]/)[0];
  return p.slice(0, MAX_PATH) || '/';
}

/** The route pattern a browser path belongs to, so /spot/pipeline and /spot/rincon are one route.
 *  Server errors carry Next's own routePath and do not need this. */
export function routeOf(pathname: string): string {
  const p = cleanPath(pathname).replace(/\/+$/, '') || '/';
  const rules: [RegExp, string][] = [
    [/^\/spot\/[^/]+$/, '/spot/[slug]'],
    [/^\/region\/[^/]+$/, '/region/[state]'],
    [/^\/blog\/[^/]+$/, '/blog/[slug]'],
    [/^\/reports\/[^/]+\/[^/]+$/, '/reports/[date]/[region]'],
    [/^\/reports\/[^/]+$/, '/reports/[date]'],
    [/^\/learn\/[^/]+$/, '/learn/[slug]'],
  ];
  for (const [re, route] of rules) if (re.test(p)) return route;
  // Anything else is one of the site's fixed pages, or a path that 404s: keep the first segment
  // only, so junk paths cannot each become their own route.
  const first = p.split('/')[1];
  return first ? `/${first.slice(0, 40)}` : '/';
}

/** 32 hex characters of SHA-256 over source, route and normalised message. Web Crypto, so the same
 *  in the node and edge runtimes. */
export async function fingerprintOf(source: ErrorSource, route: string, message: string): Promise<string> {
  const bytes = new TextEncoder().encode(`${source}\n${route}\n${message}`);
  const digest = await crypto.subtle.digest('SHA-256', bytes);
  return [...new Uint8Array(digest)].map((b) => b.toString(16).padStart(2, '0')).join('').slice(0, 32);
}

/** The body /api/client-error accepts: { message, path, digest? }, all strings. Anything else is
 *  refused. Returns the report to record, or why it was refused. */
export function parseClientReport(raw: unknown): { ok: true; report: ErrorReport } | { ok: false; error: string } {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) return { ok: false, error: 'body must be an object' };
  const r = raw as Record<string, unknown>;
  if (typeof r.message !== 'string' || r.message.trim() === '') return { ok: false, error: 'message required' };
  if (typeof r.path !== 'string' || !r.path.startsWith('/')) return { ok: false, error: 'path required' };
  if (r.digest !== undefined && r.digest !== null && typeof r.digest !== 'string') {
    return { ok: false, error: 'digest must be a string' };
  }
  const path = cleanPath(r.path);
  return {
    ok: true,
    report: {
      source: 'browser',
      route: routeOf(path),
      message: normalizeMessage(r.message),
      path,
      digest: typeof r.digest === 'string' && r.digest ? r.digest.slice(0, MAX_DIGEST) : null,
    },
  };
}

// ------------------------------------------------------------------------------------------------
// Rate limiting for /api/client-error: per server instance, in memory, never stored. Keys are held
// only for the window and only to count.
// ------------------------------------------------------------------------------------------------
export class RateLimiter {
  private hits = new Map<string, number[]>();
  private readonly limit: number;
  private readonly windowMs: number;
  // Plain fields, not parameter properties: node's type stripping (the test runner) refuses those.
  constructor(limit: number, windowMs: number) {
    this.limit = limit;
    this.windowMs = windowMs;
  }
  /** True when `key` may go ahead at `nowMs`, and counts the hit. */
  allow(key: string, nowMs: number): boolean {
    const since = nowMs - this.windowMs;
    const kept = (this.hits.get(key) ?? []).filter((t) => t > since);
    if (kept.length >= this.limit) {
      this.hits.set(key, kept);
      return false;
    }
    kept.push(nowMs);
    this.hits.set(key, kept);
    if (this.hits.size > 5000) {
      for (const [k, v] of this.hits) if (v.every((t) => t <= since)) this.hits.delete(k);
    }
    return true;
  }
}

// ------------------------------------------------------------------------------------------------
// Critical.
// ------------------------------------------------------------------------------------------------
export type ErrorRow = {
  fingerprint: string;
  source: ErrorSource;
  route: string;
  count: number;
  last_seen: string;
  window_start: string;
  window_count: number;
};

export type Critical =
  | { kind: 'repeat'; fingerprint: string; route: string; count: number }
  | { kind: 'spread'; routes: string[] };

/** What is critical at `nowMs`, from the rows (any source; browser rows are ignored here). Empty
 *  when nothing is. Sorted, so the same state always gives the same list. */
export function criticalNow(rows: readonly ErrorRow[], nowMs: number): Critical[] {
  const since = nowMs - WINDOW_MINUTES * 60_000;
  const server = rows.filter((r) => r.source === 'server');
  const out: Critical[] = server
    .filter((r) => Date.parse(r.window_start) > since && r.window_count >= SAME_FINGERPRINT_THRESHOLD)
    .map((r) => ({ kind: 'repeat' as const, fingerprint: r.fingerprint, route: r.route, count: r.window_count }))
    .sort((a, b) => a.route.localeCompare(b.route) || a.fingerprint.localeCompare(b.fingerprint));
  const routes = [...new Set(server.filter((r) => Date.parse(r.last_seen) > since).map((r) => r.route))].sort();
  if (routes.length >= ROUTES_THRESHOLD) out.push({ kind: 'spread', routes });
  return out;
}

/** What the issue tracks, for "comment only when it changes": which fingerprints repeat and which
 *  routes are erroring, not the counts, which change every minute. */
export function signature(critical: readonly Critical[]): string[] {
  return critical
    .map((c) => (c.kind === 'repeat' ? `repeat:${c.fingerprint.slice(0, 12)}:${c.route}` : `spread:${c.routes.join(',')}`))
    .sort();
}

// ------------------------------------------------------------------------------------------------
// The issue.
// ------------------------------------------------------------------------------------------------
export type Marker = { signature: string[]; last_critical_at: string | null };
const MARKER_RE = /<!-- site-critical (\{.*?\}) -->/;

export function readMarker(body: string | null | undefined): Marker | null {
  const m = MARKER_RE.exec(body ?? '');
  if (!m) return null;
  try {
    const v = JSON.parse(m[1]) as Partial<Marker>;
    if (!Array.isArray(v.signature)) return null;
    return { signature: v.signature.map(String).sort(), last_critical_at: typeof v.last_critical_at === 'string' ? v.last_critical_at : null };
  } catch {
    return null;
  }
}

export function withMarker(body: string | null | undefined, marker: Marker): string {
  const tag = `<!-- site-critical ${JSON.stringify(marker)} -->`;
  const b = body ?? '';
  return MARKER_RE.test(b) ? b.replace(MARKER_RE, tag) : `${b}\n\n${tag}`;
}

export type IssueAction = 'open' | 'comment' | 'touch' | 'close' | 'none';

/**
 *   critical, no issue open            open
 *   critical, what is critical changed comment (and record it)
 *   critical, the same as recorded     touch: update the marker's last_critical_at, no comment
 *   not critical, issue open, last
 *     critical CLEAN_CLOSE_MINUTES ago close
 *   otherwise                          none
 */
export function planIssue(critical: readonly Critical[], issueBody: string | null, nowMs: number): IssueAction {
  const open = issueBody !== null;
  if (critical.length > 0) {
    if (!open) return 'open';
    const was = readMarker(issueBody)?.signature ?? [];
    return JSON.stringify(was) === JSON.stringify(signature(critical)) ? 'touch' : 'comment';
  }
  if (!open) return 'none';
  const last = readMarker(issueBody)?.last_critical_at;
  const lastMs = last ? Date.parse(last) : Number.NaN;
  // A marker with no time (or an unreadable one) is treated as critical just now, so the issue is
  // never closed early on bad state; it closes CLEAN_CLOSE_MINUTES after the next touch.
  if (!Number.isFinite(lastMs)) return 'none';
  return nowMs - lastMs >= CLEAN_CLOSE_MINUTES * 60_000 ? 'close' : 'none';
}

function criticalLines(critical: readonly Critical[]): string[] {
  return critical.map((c) => (c.kind === 'repeat'
    ? `- \`${c.route}\`: one error ${c.count} times in ${WINDOW_MINUTES} min (fingerprint \`${c.fingerprint.slice(0, 12)}\`)`
    : `- ${c.routes.length} routes erroring in ${WINDOW_MINUTES} min: ${c.routes.map((r) => `\`${r}\``).join(', ')}`));
}

export function issueTitle(): string {
  return 'Site errors: critical';
}

export function issueBody(critical: readonly Critical[], siteUrl: string, nowIso: string): string {
  return withMarker([
    `The site is throwing server errors (at ${nowIso}):`,
    '',
    ...criticalLines(critical),
    '',
    `Messages and sample paths are on ${siteUrl}/admin/errors (password protected). They are`,
    'left out of this issue because the repository is public.',
    '',
    `This issue gets a comment when what is critical changes, and closes itself after `
      + `${CLEAN_CLOSE_MINUTES} minutes with nothing critical.`,
    '',
    `Critical means server errors only: one error ${SAME_FINGERPRINT_THRESHOLD}+ times in `
      + `${WINDOW_MINUTES} minutes, or ${ROUTES_THRESHOLD}+ routes erroring in ${WINDOW_MINUTES} minutes.`,
  ].join('\n'), { signature: signature(critical), last_critical_at: nowIso });
}

export function changeComment(critical: readonly Critical[], nowIso: string): string {
  return [`What is critical changed (at ${nowIso}):`, '', ...criticalLines(critical)].join('\n');
}

export function closeComment(nowIso: string): string {
  return `Nothing critical for ${CLEAN_CLOSE_MINUTES} minutes (at ${nowIso}). Closing; a new issue opens if it comes back.`;
}
