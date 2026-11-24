import { NextResponse, type NextRequest } from 'next/server';
import { checkBasicAuth } from './lib/adminAuth';

// /admin is behind HTTP basic auth: any username, the password is ADMIN_PASSWORD
// (lib/adminAuth.ts). With ADMIN_PASSWORD unset nobody gets in. Only /admin runs this.
export function middleware(req: NextRequest): NextResponse {
  const verdict = checkBasicAuth(req.headers.get('authorization'), process.env.ADMIN_PASSWORD);
  if (verdict === 'ok') return NextResponse.next();
  const headers: Record<string, string> = { 'x-robots-tag': 'noindex, nofollow', 'cache-control': 'no-store' };
  if (verdict === 'disabled') return new NextResponse('Admin is not configured.', { status: 503, headers });
  return new NextResponse('Authentication required.', {
    status: 401,
    headers: { ...headers, 'www-authenticate': 'Basic realm="Stormy Petrel admin", charset="UTF-8"' },
  });
}

export const config = { matcher: ['/admin', '/admin/:path*'] };
