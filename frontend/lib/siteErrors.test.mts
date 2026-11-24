/**
 * Site errors, health and admin auth: the rules, written out by hand.
 *
 *     node --experimental-strip-types frontend/lib/siteErrors.test.mts
 */
import { readdirSync, readFileSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

import {
  CLEAN_CLOSE_MINUTES, ISSUE_LABEL, MAX_BODY_BYTES, MAX_MESSAGE, RateLimiter,
  type Critical, type ErrorRow,
  cleanPath, criticalNow, fingerprintOf, issueBody, normalizeMessage, parseClientReport, planIssue,
  readMarker, routeOf, signature, withMarker, changeComment,
} from './siteErrors.ts';
import { healthFrom, MAX_RUN_AGE_HOURS } from './health.ts';
import { checkBasicAuth } from './adminAuth.ts';

const FRONTEND = join(dirname(fileURLToPath(import.meta.url)), '..');
const read = (rel: string) => readFileSync(join(FRONTEND, rel), 'utf8');

let failures = 0;
function check(name: string, cond: boolean, detail = ''): void {
  if (cond) console.log(`  PASS  ${name}`);
  else { failures += 1; console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`); }
}
function eq(name: string, got: unknown, want: unknown): void {
  check(name, JSON.stringify(got) === JSON.stringify(want), `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

// --- What is stored --------------------------------------------------------------------------
eq('whitespace collapses', normalizeMessage('a\n  b\tc'), 'a b c');
eq('a uuid is one placeholder', normalizeMessage('row 0b1c2d3e-4f50-6172-8394-a5b6c7d8e9f0 missing'), 'row <uuid> missing');
eq('a long hex id is one placeholder', normalizeMessage('cache abcdef0123456789 miss'), 'cache <hex> miss');
eq('long numbers are one placeholder', normalizeMessage('spot 990001 at 1788516000000'), 'spot <n> at <n>');
eq('short numbers stay', normalizeMessage('HTTP 503 on try 2'), 'HTTP 503 on try 2');
eq('no message', normalizeMessage(''), '(no message)');
eq('a message is capped', normalizeMessage('x'.repeat(2000)).length, MAX_MESSAGE);
eq('a path loses its query string', cleanPath('/spot/rincon?email=a@b.c#top'), '/spot/rincon');
eq('a non-path is /', cleanPath('https://evil.example/'), '/');
eq('a spot path is its route', routeOf('/spot/banzai-pipeline'), '/spot/[slug]');
eq('a region path is its route', routeOf('/region/new%20jersey'), '/region/[state]');
eq('a report path is its route', routeOf('/reports/2026-11-24/socal'), '/reports/[date]/[region]');
eq('a fixed page is itself', routeOf('/map'), '/map');
eq('junk paths collapse to their first segment', routeOf('/wp-admin/x/y/z'), '/wp-admin');
eq('home', routeOf('/'), '/');

const fpA = await fingerprintOf('server', '/spot/[slug]', 'boom');
check('a fingerprint is 32 hex characters', /^[0-9a-f]{32}$/.test(fpA), fpA);
eq('the same error gives the same fingerprint', await fingerprintOf('server', '/spot/[slug]', 'boom'), fpA);
check('another route gives another fingerprint', (await fingerprintOf('server', '/map', 'boom')) !== fpA);
check('another source gives another fingerprint', (await fingerprintOf('browser', '/spot/[slug]', 'boom')) !== fpA);

// --- The client body ---------------------------------------------------------------------------
{
  const ok = parseClientReport({ message: 'TypeError: x is undefined', path: '/spot/rincon?q=1', digest: 'abc' });
  check('a good body parses', ok.ok);
  if (ok.ok) {
    eq('...as a browser error', ok.report.source, 'browser');
    eq('...on the spot route', ok.report.route, '/spot/[slug]');
    eq('...without the query string', ok.report.path, '/spot/rincon');
    eq('...with the digest', ok.report.digest, 'abc');
    eq('...and nothing else', Object.keys(ok.report).sort(), ['digest', 'message', 'path', 'route', 'source']);
  }
  for (const [label, body] of [
    ['an array', []], ['a string', 'x'], ['no message', { path: '/' }], ['an empty message', { message: ' ', path: '/' }],
    ['no path', { message: 'x' }], ['a full URL for a path', { message: 'x', path: 'https://a.b/' }],
    ['a numeric digest', { message: 'x', path: '/', digest: 5 }],
  ] as const) check(`refused: ${label}`, !parseClientReport(body).ok);
  eq('the body cap is 4 KB', MAX_BODY_BYTES, 4096);
}

{
  const rl = new RateLimiter(3, 60_000);
  eq('three within a minute go ahead', [rl.allow('a', 0), rl.allow('a', 1), rl.allow('a', 2)], [true, true, true]);
  eq('the fourth does not', rl.allow('a', 3), false);
  eq('another key has its own count', rl.allow('b', 3), true);
  eq('a minute later it goes ahead again', rl.allow('a', 60_001), true);
}

// --- Critical ------------------------------------------------------------------------------------
const NOW = Date.parse('2026-11-24T12:00:00Z');
const ago = (min: number) => new Date(NOW - min * 60_000).toISOString();
const row = (o: Partial<ErrorRow>): ErrorRow => ({
  fingerprint: 'f'.repeat(32), source: 'server', route: '/spot/[slug]', count: 1,
  last_seen: ago(1), window_start: ago(5), window_count: 1, ...o,
});
eq('nothing recorded: not critical', criticalNow([], NOW), []);
eq('9 of one error in its window: not critical', criticalNow([row({ window_count: 9 })], NOW), []);
eq('10 of one error in its window: critical', criticalNow([row({ window_count: 10, count: 40 })], NOW),
  [{ kind: 'repeat', fingerprint: 'f'.repeat(32), route: '/spot/[slug]', count: 10 }]);
eq('10 in a window that opened 16 min ago: not critical', criticalNow([row({ window_count: 10, window_start: ago(16) })], NOW), []);
eq('a browser error 50 times: never critical', criticalNow([row({ source: 'browser', window_count: 50 })], NOW), []);
eq('2 routes erroring: not critical', criticalNow([row({ route: '/a' }), row({ route: '/b', fingerprint: 'b' })], NOW), []);
eq('3 routes erroring in 15 min: critical', criticalNow([
  row({ route: '/map', fingerprint: '1' }), row({ route: '/', fingerprint: '2' }), row({ route: '/spot/[slug]', fingerprint: '3' }),
], NOW), [{ kind: 'spread', routes: ['/', '/map', '/spot/[slug]'] }]);
eq('a third route last seen 16 min ago does not count', criticalNow([
  row({ route: '/map', fingerprint: '1' }), row({ route: '/', fingerprint: '2' }), row({ route: '/x', fingerprint: '3', last_seen: ago(16) }),
], NOW), []);
eq('browser routes do not count toward 3', criticalNow([
  row({ route: '/map', fingerprint: '1' }), row({ route: '/', fingerprint: '2' }), row({ route: '/x', fingerprint: '3', source: 'browser' }),
], NOW), []);

// --- The issue -----------------------------------------------------------------------------------
const crit: Critical[] = [{ kind: 'repeat', fingerprint: 'abcdef0123456789abcdef0123456789', route: '/spot/[slug]', count: 12 }];
const body = issueBody(crit, 'https://stormypetrel.surf', ago(0));
eq('the body carries its marker', readMarker(body), { signature: signature(crit), last_critical_at: ago(0) });
check('the body names the route and the count', body.includes('`/spot/[slug]`') && body.includes('12 times'));
check('the body points to /admin/errors for messages', body.includes('https://stormypetrel.surf/admin/errors'));
check('the counts are not in the signature, so a new count is not news', signature([{ ...crit[0], count: 40 }]).join() === signature(crit).join());
eq('critical, no issue: open', planIssue(crit, null, NOW), 'open');
eq('critical, same as recorded: touch (no comment)', planIssue(crit, body, NOW), 'touch');
eq('critical, something new: comment', planIssue([...crit, { kind: 'spread', routes: ['/', '/a', '/b'] }], body, NOW), 'comment');
eq('critical, an issue with no marker: comment', planIssue(crit, 'hand-written', NOW), 'comment');
eq('not critical, no issue: none', planIssue([], null, NOW), 'none');
const lastAt = (min: number) => withMarker(body, { signature: signature(crit), last_critical_at: ago(min) });
eq(`not critical, last critical ${CLEAN_CLOSE_MINUTES - 1} min ago: none`, planIssue([], lastAt(CLEAN_CLOSE_MINUTES - 1), NOW), 'none');
eq(`not critical, last critical ${CLEAN_CLOSE_MINUTES} min ago: close`, planIssue([], lastAt(CLEAN_CLOSE_MINUTES), NOW), 'close');
eq('not critical, a marker with no time: none (never closed early on bad state)',
  planIssue([], withMarker('x', { signature: [], last_critical_at: null }), NOW), 'none');
check('withMarker replaces, never adds a second marker',
  (withMarker(body, { signature: [], last_critical_at: null }).match(/<!-- site-critical/g) ?? []).length === 1);
check('a change comment names what is critical', changeComment(crit, ago(0)).includes('`/spot/[slug]`'));
eq('the label', ISSUE_LABEL, 'site-critical');

// --- Health --------------------------------------------------------------------------------------
eq('the age limit is 12 h', MAX_RUN_AGE_HOURS, 12);
eq('a run 6 h ago: 200', healthFrom(ago(360), NOW, null), { status: 'ok', http: 200, last_pipeline_run: ago(360), age_hours: 6 });
eq('a run 11.9 h ago: 200', healthFrom(ago(714), NOW, null).http, 200);
eq('a run 12 h ago: 503 stale', [healthFrom(ago(720), NOW, null).http, healthFrom(ago(720), NOW, null).status], [503, 'stale']);
eq('no run recorded: 503', healthFrom(null, NOW, null).http, 503);
eq('the database failed: 503, and its message is not in the answer',
  healthFrom(ago(60), NOW, 'password authentication failed for user x'),
  { status: 'error', http: 503, last_pipeline_run: null, age_hours: null, reason: 'database' });

// --- Admin auth ----------------------------------------------------------------------------------
const basic = (user: string, pw: string) => `Basic ${btoa(`${user}:${pw}`)}`;
eq('no password set: disabled, whatever is sent', checkBasicAuth(basic('a', ''), undefined), 'disabled');
eq('the right password, any user: ok', checkBasicAuth(basic('anyone', 's3cret:with:colons'), 's3cret:with:colons'), 'ok');
eq('the wrong password: denied', checkBasicAuth(basic('a', 's3cret'), 's3cret!'), 'denied');
eq('a prefix of the password: denied', checkBasicAuth(basic('a', 's3c'), 's3cret'), 'denied');
eq('no header: denied', checkBasicAuth(null, 's3cret'), 'denied');
eq('not basic: denied', checkBasicAuth('Bearer s3cret', 's3cret'), 'denied');
eq('not base64: denied', checkBasicAuth('Basic !!!', 's3cret'), 'denied');
eq('no colon: denied', checkBasicAuth(`Basic ${btoa('s3cret')}`, 's3cret'), 'denied');

// --- Where it is wired ---------------------------------------------------------------------------
const instr = read('instrumentation.ts');
check('instrumentation.ts exports onRequestError and reports through reportServerError',
  /export async function onRequestError/.test(instr) && instr.includes('reportServerError('));
for (const p of ['app/error.tsx', 'app/global-error.tsx']) {
  check(`${p} reports the browser error`, read(p).includes('reportClientError(error)'));
}
check('global-error.tsx renders its own <html> and <body>', /<html[\s>]/.test(read('app/global-error.tsx')) && read('app/global-error.tsx').includes('<body'));
const reporter = read('lib/reportClientError.ts');
check('the browser sends only message, path and digest', reporter.includes('JSON.stringify({ message, path, digest })'));
check('...and the path without its query string', reporter.includes('window.location.pathname') && !reporter.includes('location.href'));
const route = read('app/api/client-error/route.ts');
check('/api/client-error caps the body', route.includes('MAX_BODY_BYTES') && route.includes('status: 413'));
check('/api/client-error rate-limits', route.includes('status: 429') && route.includes('new RateLimiter('));
check('/api/client-error records only the parsed report', route.includes('recordError(parsed.report)'));
const server = read('lib/siteErrorsServer.ts');
// The issue can only say what the sync reads, and it never reads a message.
const syncSelect = (/\.from\('site_errors'\)\s*\.select\('([^']*)'\)/.exec(server.slice(server.indexOf('export async function syncSiteCritical'))) ?? [])[1] ?? '';
eq('the issue sync reads these columns of site_errors, and not the message', syncSelect,
  'fingerprint, source, route, count, last_seen, window_start, window_count');
const mw = read('middleware.ts');
check('middleware guards /admin and nothing else', mw.includes("matcher: ['/admin', '/admin/:path*']"));
check('middleware reads ADMIN_PASSWORD', mw.includes('process.env.ADMIN_PASSWORD'));
const admin = read('app/admin/errors/page.tsx');
check('/admin/errors is noindex', /robots: \{ index: false, follow: false/.test(admin));
check('/admin/errors is not in the sitemap', !read('app/sitemap.ts').includes('/admin'));
{
  const files: string[] = [];
  const walk = (dir: string) => {
    for (const n of readdirSync(dir)) {
      const p = join(dir, n);
      if (statSync(p).isDirectory()) walk(p);
      else if (/\.(tsx?|md)$/.test(n) && !n.includes('.test.')) files.push(p);
    }
  };
  for (const d of ['app', 'components', 'content']) walk(join(FRONTEND, d));
  const linking = files.filter((f) => /href=["'{`][^"'}`]*\/admin/.test(readFileSync(f, 'utf8'))).map((f) => relative(FRONTEND, f));
  eq('no page, component or post links to /admin', linking, []);
}
check('the migration creates site_errors, its upsert function and the lease',
  ['CREATE TABLE IF NOT EXISTS site_errors', 'CREATE OR REPLACE FUNCTION record_site_error', 'CREATE OR REPLACE FUNCTION try_site_alert_lease',
    'ON CONFLICT (fingerprint) DO UPDATE', 'ENABLE ROW LEVEL SECURITY'].every((s) => read('../pipeline/migrations/033_site_errors.sql').includes(s)));
check('the migration stores no address or browser column', !/\b(ip|user_agent|ua|cookie)\b\s+TEXT/i.test(read('../pipeline/migrations/033_site_errors.sql')));

if (failures > 0) throw new Error(`siteErrors: ${failures} FAILURE(S)`);
console.log('\nsiteErrors: ALL PASS');
