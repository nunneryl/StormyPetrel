'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import { NO_RESULTS_TEXT, searchSpots } from '@/lib/spotSearch';
import { tierFromStars } from '@/lib/ratings';
import { fmtHeightRange } from '@/lib/formatting';
import type { HeightBasis } from '@/lib/heightBasis';
import { RATED_SPOT_COUNT } from '@/lib/siteDescription';
import { HeightBasisMark } from './HeightBasisMark';
import { useSpotSearchList } from './SpotSearchList';

/** A spot's current rating, height and height basis, for the result rows: [stars, face_ft,
 *  heightBasis()], keyed by slug. Only these: the names, states and slugs the search matches on
 *  come from the one shared list (components/SpotSearchList), not a second copy. */
export type HeroRating = [stars: number | null, faceFt: number | null, basis: HeightBasis | null];
export type HeroRatings = Record<string, HeroRating>;

/**
 * The big homepage search. Instant filter; results show the spot's
 * current rating + face height inline so a user can pick the best
 * match without leaving the page first.
 *
 * Keyboard-first: ↑/↓ to navigate, Enter to pick, Esc to close,
 * ⌘K from anywhere on the page focuses the input.
 */
export function HeroSearch({ ratings }: { ratings: HeroRatings }) {
  const spots = useSpotSearchList();
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const wrap = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  // Accents and the ʻokina are optional (lib/spotSearch.ts).
  const results = useMemo(() => searchSpots(spots, q), [q, spots]);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (wrap.current && !wrap.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        inputRef.current?.focus();
      }
    }
    document.addEventListener('keydown', onKey);
    return () => document.removeEventListener('keydown', onKey);
  }, []);

  return (
    <div ref={wrap} className="relative w-full">
      <div className="relative">
        <span className="absolute left-5 top-1/2 -translate-y-1/2 text-text-muted">
          <SearchIcon />
        </span>
        <input
          ref={inputRef}
          type="search"
          inputMode="search"
          value={q}
          onChange={(e) => {
            setQ(e.target.value);
            setOpen(true);
            setActiveIdx(0);
          }}
          onFocus={() => setOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'ArrowDown') {
              e.preventDefault();
              setActiveIdx((i) => Math.min(results.length - 1, i + 1));
            } else if (e.key === 'ArrowUp') {
              e.preventDefault();
              setActiveIdx((i) => Math.max(0, i - 1));
            } else if (e.key === 'Enter' && results[activeIdx]) {
              window.location.href = `/spot/${results[activeIdx].slug}`;
            } else if (e.key === 'Escape') {
              setOpen(false);
            }
          }}
          placeholder={`Search ${RATED_SPOT_COUNT} spots — Mavericks, Pipeline, Rockaway...`}
          aria-label="Search surf spots"
          className="w-full h-14 sm:h-16 pl-14 pr-20 text-base sm:text-lg rounded-xl border border-ink-600 bg-white text-text-primary placeholder:text-text-muted focus:border-cyan-500 focus:outline-none shadow-card transition"
        />
        <kbd className="hidden sm:flex absolute right-5 top-1/2 -translate-y-1/2 items-center px-2 py-1 text-[11px] font-mono text-text-muted border border-ink-600 rounded">
          ⌘K
        </kbd>
      </div>

      {open && q.trim() !== '' && results.length === 0 && (
        <div className="absolute z-30 mt-2 w-full rounded-xl border border-ink-600 bg-white shadow-2xl px-4 py-3 text-sm text-text-muted" role="status">
          {NO_RESULTS_TEXT}
        </div>
      )}
      {open && results.length > 0 && (
        <div className="absolute z-30 mt-2 w-full rounded-xl border border-ink-600 bg-white shadow-2xl max-h-[420px] overflow-auto">
          {results.map((r, i) => {
            const [stars, faceFt, basis] = ratings[r.slug] ?? [null, null, null];
            const tier = tierFromStars(stars);
            return (
              <Link
                key={r.slug}
                href={`/spot/${r.slug}`}
                onMouseEnter={() => setActiveIdx(i)}
                onClick={() => setOpen(false)}
                className={`flex items-center justify-between gap-3 px-4 py-3 text-sm border-b border-ink-600 last:border-b-0 transition ${
                  i === activeIdx ? 'bg-ink-800' : ''
                }`}
              >
                <div className="min-w-0">
                  <div className="font-bold text-text-primary truncate">{r.name}</div>
                  <div className="text-xs text-text-muted truncate">{r.state ?? ''}</div>
                </div>
                <div className="flex items-center gap-2 shrink-0">
                  <span className="font-bold tabular-nums text-text-primary text-sm">
                    {fmtHeightRange(faceFt)}
                  </span>
                  <HeightBasisMark basis={basis} />
                  <span
                    className="text-[10px] font-bold tracking-widest2 uppercase px-1.5 py-0.5 rounded"
                    style={{
                      color: tier.hex,
                      background: `${tier.hex}15`,
                    }}
                  >
                    {tier.label}
                  </span>
                </div>
              </Link>
            );
          })}
        </div>
      )}
    </div>
  );
}

function SearchIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </svg>
  );
}
