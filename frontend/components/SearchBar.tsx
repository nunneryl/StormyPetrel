'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';

type Item = { slug: string; name: string; state: string | null };

export function SearchBar({
  spots,
  size = 'md',
}: {
  spots: Item[];
  size?: 'md' | 'lg';
}) {
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const [activeIdx, setActiveIdx] = useState(0);
  const ref = useRef<HTMLDivElement | null>(null);
  const inputRef = useRef<HTMLInputElement | null>(null);

  const results = useMemo(() => {
    const term = q.trim().toLowerCase();
    if (!term) return [];
    return spots
      .filter(
        (s) =>
          s.name.toLowerCase().includes(term) ||
          (s.state ?? '').toLowerCase().includes(term),
      )
      .slice(0, 8);
  }, [q, spots]);

  useEffect(() => {
    function onClick(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    }
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, []);

  // ⌘K / Ctrl-K to focus the bar from anywhere on the page.
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

  const inputCls =
    size === 'lg'
      ? 'h-14 text-lg px-5'
      : 'h-12 text-base px-4';

  return (
    <div ref={ref} className="relative w-full max-w-2xl">
      <div className="relative">
        <span className="absolute left-4 top-1/2 -translate-y-1/2 text-text-muted">
          <SearchIcon />
        </span>
        <input
          ref={inputRef}
          type="search"
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
          placeholder="Search 500+ spots..."
          className={`w-full pl-12 pr-16 ${inputCls} rounded-xl border border-ink-600 bg-ink-800 text-text-primary placeholder:text-text-muted focus:border-cyan-500 focus:outline-none transition`}
        />
        <kbd className="hidden sm:flex absolute right-4 top-1/2 -translate-y-1/2 items-center gap-1 px-1.5 py-0.5 text-[10px] font-mono text-text-muted border border-ink-600 rounded">
          ⌘K
        </kbd>
      </div>
      {open && results.length > 0 && (
        <div className="absolute z-20 mt-1.5 w-full rounded-xl border border-ink-600 bg-ink-800 shadow-2xl max-h-96 overflow-auto">
          {results.map((r, i) => (
            <Link
              key={r.slug}
              href={`/spot/${r.slug}`}
              onMouseEnter={() => setActiveIdx(i)}
              className={`flex items-center justify-between px-4 py-2.5 text-sm transition ${
                i === activeIdx ? 'bg-ink-700' : ''
              }`}
              onClick={() => setOpen(false)}
            >
              <span className="font-medium text-text-primary">{r.name}</span>
              <span className="text-xs text-text-muted">{r.state ?? ''}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}

function SearchIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </svg>
  );
}
