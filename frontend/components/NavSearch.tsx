'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';
import type { SpotSearchItem } from './SiteNav';

export function NavSearch({ spots }: { spots: SpotSearchItem[] }) {
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
    <div ref={ref} className="relative w-full">
      <div className="relative">
        <span className="absolute left-3 top-1/2 -translate-y-1/2 text-text_inv-muted">
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
          placeholder="Search spots..."
          className="w-full h-9 pl-10 pr-14 text-sm rounded-md bg-deep-800/80 border border-deep-600 text-text_inv-primary placeholder:text-text_inv-muted focus:border-cyan-500 focus:outline-none transition"
        />
        <kbd className="hidden lg:flex absolute right-3 top-1/2 -translate-y-1/2 items-center px-1.5 py-0.5 text-[10px] font-mono text-text_inv-muted border border-deep-600 rounded">
          ⌘K
        </kbd>
      </div>
      {open && results.length > 0 && (
        <div className="absolute z-40 mt-1 w-full rounded-md border border-ink-600 bg-white shadow-2xl max-h-96 overflow-auto">
          {results.map((r, i) => (
            <Link
              key={r.slug}
              href={`/spot/${r.slug}`}
              onMouseEnter={() => setActiveIdx(i)}
              className={`flex items-center justify-between px-3 py-2 text-sm transition ${
                i === activeIdx ? 'bg-ink-800' : ''
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
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <circle cx="11" cy="11" r="7" />
      <path d="M21 21l-4.3-4.3" />
    </svg>
  );
}
