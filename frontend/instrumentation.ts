/**
 * Next's instrumentation hook. onRequestError runs for every error Next catches while rendering a
 * page, running a route handler or a server action, on the server. It records the error in
 * site_errors and checks whether the site has gone critical (lib/siteErrorsServer.ts), which can
 * open the site-critical GitHub issue.
 *
 * Imported lazily, so a page that never errors never loads the reporting code.
 */
export function register(): void {
  // Nothing to set up.
}

type RequestInfo = { path: string; method: string; headers: Record<string, string | string[] | undefined> };
type ErrorContext = { routerKind: string; routePath: string; routeType: string };

export async function onRequestError(err: unknown, request: RequestInfo, context: ErrorContext): Promise<void> {
  try {
    const { reportServerError } = await import('./lib/siteErrorsServer');
    const digest = err && typeof err === 'object' && 'digest' in err ? String((err as { digest?: unknown }).digest ?? '') : '';
    await reportServerError(err, request.path, context.routePath, digest || undefined);
  } catch (e) {
    // Reporting must never throw out of here.
    console.error('onRequestError: could not report:', e instanceof Error ? e.message : e);
  }
}
