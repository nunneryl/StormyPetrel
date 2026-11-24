import type { Metadata } from 'next';
import { supabaseUncached } from '@/lib/supabase';

// /admin/errors: every error fingerprint, newest first. Behind HTTP basic auth (middleware.ts,
// ADMIN_PASSWORD), noindex, and linked from nowhere: not in the nav, footer or sitemap.
//
// DYNAMIC, unlike every public page (lib/dataCache.test.mts lists it as the one exception): it
// must show the table as it is now, and it is read rarely, by one person.
export const dynamic = 'force-dynamic';
export const revalidate = 0;

export const metadata: Metadata = {
  title: { absolute: 'Site errors | Stormy Petrel admin' },
  robots: { index: false, follow: false, googleBot: { index: false, follow: false } },
};

type Row = {
  fingerprint: string;
  source: 'server' | 'browser';
  route: string;
  message: string;
  sample_path: string | null;
  digest: string | null;
  count: number;
  first_seen: string;
  last_seen: string;
};

const LIMIT = 200;

function fmt(iso: string): string {
  return new Date(iso).toISOString().replace('T', ' ').slice(0, 16) + 'Z';
}

export default async function AdminErrorsPage() {
  const { data, error } = await supabaseUncached
    .from('site_errors')
    .select('fingerprint, source, route, message, sample_path, digest, count, first_seen, last_seen')
    .order('last_seen', { ascending: false })
    .limit(LIMIT);
  const rows = (data ?? []) as Row[];

  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6 py-8">
      <h1 className="text-2xl font-bold tracking-tightish text-text-primary">Site errors</h1>
      <p className="mt-1 text-sm text-text-secondary">
        One row per fingerprint (source, route and message), newest first, at most {LIMIT}. Server errors can raise
        the site-critical issue; browser errors never do. Times are UTC.
      </p>
      {error ? (
        <p className="mt-6 text-sm text-rating-poor">
          Could not read site_errors: {error.message}. Has migration 033 been run?
        </p>
      ) : rows.length === 0 ? (
        <p className="mt-6 text-sm text-text-muted" data-admin-empty>No errors recorded.</p>
      ) : (
        <div className="mt-6 overflow-x-auto rounded-xl border border-ink-600 bg-white">
          <table className="min-w-[900px] w-full text-sm">
            <thead className="bg-ink-900 text-[11px] uppercase tracking-widest2 text-text-secondary">
              <tr>
                <th className="px-3 py-2 text-left">Last seen</th>
                <th className="px-3 py-2 text-left">Source</th>
                <th className="px-3 py-2 text-left">Route</th>
                <th className="px-3 py-2 text-left">Message</th>
                <th className="px-3 py-2 text-right">Count</th>
                <th className="px-3 py-2 text-left">First seen</th>
                <th className="px-3 py-2 text-left">Sample path</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.fingerprint} className="border-t border-ink-600 align-top" data-fingerprint={r.fingerprint}>
                  <td className="px-3 py-2 whitespace-nowrap tabular-nums">{fmt(r.last_seen)}</td>
                  <td className="px-3 py-2">{r.source}</td>
                  <td className="px-3 py-2 font-mono text-xs">{r.route}</td>
                  <td className="px-3 py-2 break-words max-w-[420px]">
                    {r.message}
                    {r.digest && <div className="text-xs text-text-muted">digest {r.digest}</div>}
                  </td>
                  <td className="px-3 py-2 text-right tabular-nums">{r.count}</td>
                  <td className="px-3 py-2 whitespace-nowrap tabular-nums">{fmt(r.first_seen)}</td>
                  <td className="px-3 py-2 font-mono text-xs break-all">{r.sample_path ?? ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
