'use client';

import { useEffect } from 'react';
import Link from 'next/link';
import { WaveGlyph } from '@/components/Logo';

export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}) {
  useEffect(() => {
    // Log to console; production error tracking can read it later.
    // Avoiding any noisy 3rd-party SDK for now.
    // eslint-disable-next-line no-console
    console.error('Page error:', error);
  }, [error]);

  return (
    <div className="mx-auto max-w-2xl px-4 py-24 text-center">
      <div className="flex justify-center mb-4">
        <WaveGlyph className="text-rating-poor" size={48} />
      </div>
      <h1 className="text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
        Something broke loading this page
      </h1>
      <p className="mt-2 text-text-secondary">
        Most likely the upstream forecast database is having a moment. The
        scheduled cron will refresh data on the next cycle. If it persists,
        the issue is probably something we should know about.
      </p>
      <div className="mt-6 flex items-center justify-center gap-3">
        <button
          type="button"
          onClick={() => reset()}
          className="px-4 py-2 rounded-md bg-cyan-500 text-ink-950 font-medium hover:bg-cyan-400 transition"
        >
          Try again
        </button>
        <Link
          href="/"
          className="px-4 py-2 rounded-md border border-ink-600 text-text-primary hover:bg-ink-700 transition"
        >
          Home
        </Link>
      </div>
      {error.digest && (
        <p className="mt-6 text-xs text-text-muted">
          Error reference: <code className="font-mono">{error.digest}</code>
        </p>
      )}
    </div>
  );
}
