'use client';

import { createContext, useContext, useEffect, useState } from 'react';

import {
  CHIP_HINT_IDLE,
  CHIP_HINT_SELECTED,
  CHIP_IDLE,
  CHIP_SELECTED,
  hourOptionLabel,
} from '@/lib/reportUi';
import {
  SIZE_BUCKETS,
  SIZE_BUCKET_LABELS,
  BUCKET_HINT_FT,
  RATING_VERDICTS,
  RATING_VERDICT_LABELS,
  HONEYPOT_FIELD,
  QUESTION_VERSION,
  hourOptions,
  type SizeBucket,
  type RatingVerdict,
} from '@/lib/surfReport';

/**
 * The surf-report card: always visible under the hero tiles, asking "How big were most waves?".
 *
 * WHY ALWAYS VISIBLE. Until 2026-10-30 the form opened from a small "Was this right?" link in
 * the Surf height tile, and face-height comparison run 52 found 0 reports since 2026-09-07; the
 * route itself answered fine. So the question is now on the page, and tapping a size starts the
 * report. The rest (the hour, the optional sets and star questions, Send) opens below it.
 *
 * THE QUESTION (question_version 2, #282). "How big were most waves?" on the body scale, with
 * a small feet hint per chip (BUCKET_HINT_FT). Not feet chips that echo the tile just above:
 * that would anchor answers to our own forecast and flatter any accuracy figure. Optional:
 * "How big were the sets?" on the same scale, and the star verdict.
 *
 * WHAT THE ANSWERS ARE FOR. Checking the forecast, and nothing else: no rating, height or
 * forecast the site shows ever reads them (docs/face_validation/README.md, enforced by
 * pipeline/tests/test_reports_never_feed_forecasts.py).
 *
 * SERVER/ISR CONTRACT. The spot page and CurrentConditions stay server components; the
 * provider takes them as `children`. Nothing here reads cookies() or headers(), so the routes
 * keep their prerender and `revalidate`. The question and the chips render on the server; the
 * hours are filled in after mount, since anything from Date.now() during render would be baked
 * into the prerendered HTML and served stale.
 *
 * SUBMITTED STATE IS CLIENT-SIDE ONLY, and does not survive a refresh. The database makes a
 * re-submit harmless: UNIQUE(spot_id, observed_hour, reporter_hash) turns it into a revision.
 */

type Ctx = {
  slug: string;
  /** The spot's IANA time zone (lib/spotTime.ts): the hours are offered in the spot's local
   *  time, the clock the reporter surfed by, not the browser's. */
  timeZone: string;
};

const SurfReportCtx = createContext<Ctx | null>(null);

function useSurfReport(): Ctx {
  const ctx = useContext(SurfReportCtx);
  if (!ctx) throw new Error('SurfReport components must be inside <SurfReportProvider>');
  return ctx;
}

export function SurfReportProvider({
  slug,
  timeZone,
  children,
}: {
  slug: string;
  timeZone: string;
  children: React.ReactNode;
}) {
  return (
    <SurfReportCtx.Provider value={{ slug, timeZone }}>
      {children}
    </SurfReportCtx.Provider>
  );
}

function Chips({
  name,
  value,
  onPick,
}: {
  name: string;
  value: SizeBucket | null;
  onPick: (b: SizeBucket | null) => void;
}) {
  return (
    <div className="flex flex-wrap gap-1.5" role="group" aria-label={name}>
      {SIZE_BUCKETS.map((b) => (
        <button
          key={b}
          type="button"
          onClick={() => onPick(value === b ? null : b)}
          aria-pressed={value === b}
          className={`px-2.5 py-1 rounded-md border text-xs transition text-left leading-tight ${
            value === b ? CHIP_SELECTED : CHIP_IDLE
          }`}
        >
          <span className="block">{SIZE_BUCKET_LABELS[b]}</span>
          <span className={`block text-[10px] ${value === b ? CHIP_HINT_SELECTED : CHIP_HINT_IDLE}`}>
            {BUCKET_HINT_FT[b]}
          </span>
        </button>
      ))}
    </div>
  );
}

export function SurfReportPanel() {
  const { slug, timeZone } = useSurfReport();

  const [hours, setHours] = useState<string[]>([]);
  const [hour, setHour] = useState<string>('');
  const [size, setSize] = useState<SizeBucket | null>(null);
  const [sets, setSets] = useState<SizeBucket | null>(null);
  const [verdict, setVerdict] = useState<RatingVerdict | null>(null);
  const [trap, setTrap] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [submitted, setSubmitted] = useState(false);

  // See the module comment: never during render, or the prerender bakes the hours in.
  useEffect(() => {
    const opts = hourOptions(Date.now());
    setHours(opts);
    setHour(opts[0]);
  }, []);

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
          question_version: QUESTION_VERSION,
          size_bucket: size,
          sets_bucket: sets,
          rating_verdict: verdict,
          [HONEYPOT_FIELD]: trap,
        }),
      });
      const json = (await res.json()) as { ok?: boolean; error?: string };
      // A duplicate comes back ok:true — the reporter already told us this and the row is
      // already there, so the honest thing to show them is success.
      if (res.ok && json.ok) {
        setSubmitted(true);
      } else {
        setError(json.error || 'Could not save that — try again.');
      }
    } catch {
      setError('Could not reach the server — try again.');
    } finally {
      setBusy(false);
    }
  }

  if (submitted) {
    // The reporter can still correct themselves: a repeat replaces the earlier answer.
    return (
      <section className="rounded-xl border border-cyan-900/60 bg-ink-800/60 px-4 py-3 text-sm">
        <span className="text-cyan-400">Thanks — your report is logged.</span>{' '}
        <button
          type="button"
          onClick={() => setSubmitted(false)}
          className="text-text-muted hover:text-cyan-400 underline underline-offset-2"
        >
          change it
        </button>
      </section>
    );
  }

  return (
    <section
      aria-labelledby="surf-report-heading"
      className="rounded-xl border border-cyan-900/60 bg-ink-800/60 p-4 space-y-3"
    >
      <div>
        <h2 id="surf-report-heading" className="text-sm font-semibold text-text-primary">
          Surfed here today? How big were most waves?
        </h2>
        <p className="mt-0.5 text-xs text-text-muted max-w-prose">
          Most of the waves, not the biggest sets. Your answer is how we check our forecast.
        </p>
      </div>

      {/* 1 — HOW BIG (required). Tapping a size opens the rest. */}
      <Chips name="How big were most waves?" value={size} onPick={setSize} />

      {size && (
        <div className="space-y-3 border-t border-ink-600 pt-3">
          {/* 2 — WHEN. Asked, never inferred: someone may report at 4pm about a dawn session. */}
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
                  {hourOptionLabel(iso, i, timeZone)}
                </option>
              ))}
            </select>
          </div>

          {/* 3 — THE SETS (optional), on the same scale. */}
          <div>
            <div className="text-xs text-text-secondary mb-1.5">
              How big were the sets? <span className="text-text-muted">— optional</span>
            </div>
            <Chips name="How big were the sets?" value={sets} onPick={setSets} />
          </div>

          {/* 4 — THE STARS (optional). */}
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
                    verdict === v ? CHIP_SELECTED : CHIP_IDLE
                  }`}
                >
                  {RATING_VERDICT_LABELS[v]}
                </button>
              ))}
            </div>
          </div>

          {/* The bot field: off-screen, out of the tab order and hidden from screen readers. */}
          <div aria-hidden="true" className="absolute -left-[9999px] h-px w-px overflow-hidden">
            <label>
              Leave this empty
              <input
                type="text"
                name={HONEYPOT_FIELD}
                tabIndex={-1}
                autoComplete="off"
                value={trap}
                onChange={(e) => setTrap(e.target.value)}
              />
            </label>
          </div>

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
        </div>
      )}
    </section>
  );
}
