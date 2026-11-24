'use client';

import { useEffect } from 'react';
import { reportClientError } from '@/lib/reportClientError';

// Shown when the root layout itself fails, so it renders its own <html> and <body> and cannot use
// the site's nav, footer or stylesheet. Reports the error like app/error.tsx does.
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // eslint-disable-next-line no-console
    console.error('Root error:', error);
    reportClientError(error);
  }, [error]);

  return (
    <html lang="en">
      <body style={{ margin: 0, fontFamily: 'system-ui, -apple-system, sans-serif', color: '#0F172A', background: '#FFFFFF' }}>
        <main style={{ maxWidth: 560, margin: '0 auto', padding: '96px 16px', textAlign: 'center' }}>
          <h1 style={{ fontSize: 28, margin: 0 }}>Something went wrong</h1>
          <p style={{ color: '#475569', marginTop: 8 }}>
            The page couldn&rsquo;t load. We&rsquo;ve been told about it. Please try again.
          </p>
          <div style={{ marginTop: 24, display: 'flex', gap: 12, justifyContent: 'center' }}>
            <button
              type="button"
              onClick={() => reset()}
              style={{ padding: '8px 16px', borderRadius: 6, border: 'none', background: '#0369A1', color: '#FFFFFF', fontSize: 15, cursor: 'pointer' }}
            >
              Try again
            </button>
            {/* A plain link, not next/link: the router may be what broke. */}
            {/* eslint-disable-next-line @next/next/no-html-link-for-pages */}
            <a href="/" style={{ padding: '8px 16px', borderRadius: 6, border: '1px solid #CBD5E1', color: '#0F172A', textDecoration: 'none', fontSize: 15 }}>
              Home
            </a>
          </div>
          {error.digest && (
            <p style={{ marginTop: 24, fontSize: 12, color: '#64748B' }}>
              Error reference: <code>{error.digest}</code>
            </p>
          )}
        </main>
      </body>
    </html>
  );
}
