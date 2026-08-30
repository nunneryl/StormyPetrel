'use client';

import { createContext, useContext, useEffect, useState } from 'react';
import {
  SIZE_BUCKETS,
  SIZE_BUCKET_LABELS,
  RATING_VERDICTS,
  RATING_VERDICT_LABELS,
  hourOptions,
  type SizeBucket,
  type RatingVerdict,
} from '@/lib/surfReport';

/**
 * The surf-report feedback control: a trigger in the Face tile's footer and a panel below
 * the hero tiles. Split in two because they live in different places in the page tree —
 * the trigger belongs against the number it is reporting on, the panel needs full width for
 * the hour picker — and joined by context so they share one open/submitted state.
 *
 * SERVER/ISR CONTRACT. The spot page and CurrentConditions stay server components; the
 * provider takes them as `children`, which is what lets a client component wrap a server
 * subtree. Nothing here reads cookies() or headers(), so all 648 routes keep their
 * generateStaticParams prerender and `revalidate = 3600`.
 *
 * THE HOURS ARE COMPUTED AFTER MOUNT, deliberately. The page is statically prerendered at
 * deploy time, so anything derived from Date.now() during render would be baked into the
 * HTML and served stale for up to an hour — a picker offering last Tuesday's hours. The
 * options are therefore filled in an effect, and the panel shows a disabled placeholder
 * until they arrive. The user cannot see the panel before hydration anyway (it opens on a
 * click), so nothing is lost.
 *
 * SUBMITTED STATE IS CLIENT-SIDE ONLY, and does not survive a refresh. That is a deliberate
 * v1 choice, not an oversight: persisting it would need localStorage (per-browser, silently
 * empty in private windows) and the database already makes a re-submit harmless — the
 * UNIQUE(spot_id, observed_hour, reporter_hash) constraint turns it into an idempotent
 * no-op that the route reports as success.
 */

type Ctx = {
  open: boolean;
  setOpen: (v: boolean) => void;
  submitted: boolean;
  setSubmitted: (v: boolean) => void;
  slug: string;
  /** UTC-hour ISO strings the page actually holds an NWPS forecast row for. Used only to
   *  warn the reporter that a chosen hour will not be joinable; never to block a report —
   *  an observation of an hour we failed to forecast is still worth having. */
  forecastHours: string[];
};

const SurfReportCtx = createContext<Ctx | null>(null);

function useSurfReport(): Ctx {
  const ctx = useContext(SurfReportCtx);
  if (!ctx) throw new Error('SurfReport components must be inside <SurfReportProvider>');
  return ctx;
}

export function SurfReportProvider({
  slug,
  forecastHours,
  children,
}: {
  slug: string;
  forecastHours: string[];
  children: React.ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const [submitted, setSubmitted] = useState(false);
  return (
    <SurfReportCtx.Provider value={{ open, setOpen, submitted, setSubmitted, slug, forecastHours }}>
      {children}
    </SurfReportCtx.Provider>
  );
}

export function SurfReportTrigger() {
  const { open, setOpen, submitted } = useSurfReport();
  if (submitted) {
    return <span className="text-[11px] text-cyan-400">Thanks — logged</span>;
  }
  return (
    <button
      type="button"
      onClick={() => setOpen(!open)}
      aria-expanded={open}
      className="text-[11px] text-text-muted hover:text-cyan-400 underline underline-offset-2 transition"
    >
      {open ? 'Close' : 'Was this right?'}
    </button>
  );
}

export function SurfReportPanel() {
  const { open, setOpen, submitted, setSubmitted, slug, forecastHours } = useSurfReport();

  const [hours, setHours] = useState<string[]>([]);
  const [hour, setHour] = useState<string>('');
  const [size, setSize] = useState<SizeBucket | null>(null);
  const [verdict, setVerdict] = useState<RatingVerdict | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // See the module comment: never during render, or the prerender bakes the hours in.
  useEffect(() => {
    const opts = hourOptions(Date.now());
    setHours(opts);
    setHour(opts[0]);
  }, []);

  if (!open || submitted) return null;

  const known = new Set(forecastHours);

  async function send() {
    if (!size || !hour) return;
    setBusy(true);
    setError(null);
    try {
      const res = await fetch('/api/reports', {
        method: 'POST',
        headers: { 'content-type': 'application/json' },
        body: JSON.stringify({
          slug,
          observed_hour: hour,
          size_bucket: size,
          rating_verdict: verdict,
        }),
      });
      const json = (await res.json()) as { ok?: boolean; error?: string };
      // A duplicate comes back ok:true — the reporter already told us this and the row is
      // already there, so the honest thing to show them is success.
      if (res.ok && json.ok) {
        setSubmitted(true);
        setOpen(false);
      } else {
        setError(json.error || 'Could not save that — try again.');
      }
    } catch {
      setError('Could not reach the server — try again.');
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="rounded-xl border border-cyan-900/60 bg-ink-800/60 p-4 space-y-4">
      <div className="flex items-start justify-between gap-3">
        <div>
          <div className="text-[10px] uppercase tracking-widest2 text-text-secondary">
            Report what you saw
          </div>
          <p className="mt-1 text-xs text-text-muted max-w-prose">
            We have never checked our size numbers against a real observation. This is how we
            start.
          </p>
        </div>
        <button
          type="button"
          onClick={() => setOpen(false)}
          aria-label="Close report panel"
          className="text-text-muted hover:text-text-primary text-lg leading-none"
        >
          ×
        </button>
      </div>

      {/* 1 — WHEN. Asked, never inferred: someone may report at 4pm about a dawn session,
          so click time and observed time are different quantities. */}
      <div>
        <label htmlFor="surf-report-hour" className="block text-xs text-text-secondary mb-1.5">
          When were you out?
        </label>
        <select
          id="surf-report-hour"
          value={hour}
          disabled={hours.length === 0}
          onChange={(e) => setHour(e.target.value)}
          className="w-full sm:w-auto rounded-md border border-ink-600 bg-ink-900 px-2.5 py-1.5 text-sm text-text-primary disabled:opacity-50"
        >
          {hours.length === 0 && <option>Loading…</option>}
          {hours.map((iso, i) => (
            <option key={iso} value={iso}>
              {new Date(iso).toLocaleTimeString([], { hour: 'numeric' })}
              {i === 0 ? ' (this hour)' : ''}
              {known.size > 0 && !known.has(iso) ? ' · no forecast' : ''}
            </option>
          ))}
        </select>
      </div>

      {/* 2 — HOW BIG. The label matters as much as the buttons: face_ft publishes an
          average-of-the-sets number, so a reporter answering with the biggest wave of the
          day would be handing us a different statistic from the one we are calibrating. */}
      <div>
        <div className="text-xs text-text-secondary mb-1.5">
          How big was it?{' '}
          <span className="text-text-muted">— the average of the sets you rode, not the biggest one</span>
        </div>
        <div className="flex flex-wrap gap-1.5">
          {SIZE_BUCKETS.map((b) => (
            <button
              key={b}
              type="button"
              onClick={() => setSize(b)}
              aria-pressed={size === b}
              className={`px-2.5 py-1 rounded-md border text-xs transition ${
                size === b
                  ? 'border-cyan-500 bg-cyan-500/15 text-cyan-300'
                  : 'border-ink-600 bg-ink-900 text-text-secondary hover:border-ink-500 hover:text-text-primary'
              }`}
            >
              {SIZE_BUCKET_LABELS[b]}
            </button>
          ))}
        </div>
      </div>

      {/* 3 — RATING. Optional, and asked only once a size answer exists, so it can never
          block the label we actually came for. */}
      {size && (
        <div>
          <div className="text-xs text-text-secondary mb-1.5">
            And our star rating? <span className="text-text-muted">— optional</span>
          </div>
          <div className="flex flex-wrap gap-1.5">
            {RATING_VERDICTS.map((v) => (
              <button
                key={v}
                type="button"
                onClick={() => setVerdict(verdict === v ? null : v)}
                aria-pressed={verdict === v}
                className={`px-2.5 py-1 rounded-md border text-xs transition ${
                  verdict === v
                    ? 'border-cyan-500 bg-cyan-500/15 text-cyan-300'
                    : 'border-ink-600 bg-ink-900 text-text-secondary hover:border-ink-500 hover:text-text-primary'
                }`}
              >
                {RATING_VERDICT_LABELS[v]}
              </button>
            ))}
          </div>
        </div>
      )}

      {error && <p className="text-xs text-amber-400">{error}</p>}

      <div className="flex items-center gap-3">
        <button
          type="button"
          onClick={send}
          disabled={!size || !hour || busy}
          className="px-3 py-1.5 rounded-md bg-cyan-600 text-white text-sm font-medium disabled:opacity-40 disabled:cursor-not-allowed hover:bg-cyan-500 transition"
        >
          {busy ? 'Sending…' : 'Send report'}
        </button>
        <span className="text-[11px] text-text-muted">
          Anonymous — no account, no name, no IP stored.
        </span>
      </div>
    </section>
  );
}
