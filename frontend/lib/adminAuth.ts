/**
 * HTTP basic auth for /admin (middleware.ts). Any username; the password is ADMIN_PASSWORD.
 * Pure and import-free: it runs in the edge runtime and tests under bare node.
 *
 *   'ok'        the Authorization header carries the password
 *   'denied'    no header, a malformed one, or the wrong password: answer 401
 *   'disabled'  ADMIN_PASSWORD is not set: answer 503, never let anyone in
 */
export type AdminAuth = 'ok' | 'denied' | 'disabled';

/** Compare without stopping at the first difference, so the time taken does not say how much of
 *  a guess was right. */
function sameString(a: string, b: string): boolean {
  let diff = a.length ^ b.length;
  const n = Math.max(a.length, b.length);
  for (let i = 0; i < n; i += 1) diff |= (a.charCodeAt(i) || 0) ^ (b.charCodeAt(i) || 0);
  return diff === 0;
}

export function checkBasicAuth(header: string | null | undefined, password: string | null | undefined): AdminAuth {
  if (!password) return 'disabled';
  const m = /^Basic\s+([A-Za-z0-9+/=]+)$/.exec((header ?? '').trim());
  if (!m) return 'denied';
  let decoded: string;
  try {
    decoded = atob(m[1]);
  } catch {
    return 'denied';
  }
  const colon = decoded.indexOf(':');
  if (colon < 0) return 'denied';
  return sameString(decoded.slice(colon + 1), password) ? 'ok' : 'denied';
}
