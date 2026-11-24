/**
 * Send a browser error to /api/client-error, from app/error.tsx and app/global-error.tsx. Only the
 * message, the page's path (no query string) and Next's digest go. Never throws, and sends each
 * distinct error once per page load, so an error page that re-renders cannot flood the route.
 *
 * Imports nothing, so it is safe in client code (lib/serverOnly.test.mts).
 */
const sent = new Set<string>();

export function reportClientError(error: unknown): void {
  try {
    if (typeof window === 'undefined') return;
    const e = error as { message?: unknown; digest?: unknown } | null;
    const message = String(e?.message ?? error ?? 'Unknown error').slice(0, 1000);
    const path = window.location.pathname;
    const digest = typeof e?.digest === 'string' ? e.digest : undefined;
    const key = `${path}\n${message}\n${digest ?? ''}`;
    if (sent.has(key)) return;
    sent.add(key);
    const body = JSON.stringify({ message, path, digest });
    if (navigator.sendBeacon && navigator.sendBeacon('/api/client-error', new Blob([body], { type: 'application/json' }))) {
      return;
    }
    void fetch('/api/client-error', {
      method: 'POST', headers: { 'content-type': 'application/json' }, body, keepalive: true,
    }).catch(() => {});
  } catch {
    // Reporting must never break the error page.
  }
}
