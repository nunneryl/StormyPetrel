'use client';

import { useEffect, useMemo, useRef, useState } from 'react';
import Link from 'next/link';

type Item = { slug: string; name: string; state: string | null };

export function SearchBar({ spots }: { spots: Item[] }) {
  const [q, setQ] = useState('');
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement | null>(null);

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

  return (
    <div ref={ref} className="relative w-full max-w-xl">
      <input
        type="search"
        value={q}
        onChange={(e) => {
          setQ(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        placeholder="Search 500+ spots…"
        className="w-full rounded-md border border-ink-600 bg-ink-900 px-4 py-3 text-base placeholder:text-slate-500 focus:border-sea-400 focus:outline-none"
      />
      {open && results.length > 0 && (
        <div className="absolute z-20 mt-1 w-full rounded-md border border-ink-600 bg-ink-900 shadow-xl max-h-80 overflow-auto">
          {results.map((r) => (
            <Link
              key={r.slug}
              href={`/spot/${r.slug}`}
              className="flex items-center justify-between px-3 py-2 text-sm hover:bg-ink-800"
              onClick={() => setOpen(false)}
            >
              <span className="font-medium text-slate-100">{r.name}</span>
              <span className="text-xs text-slate-400">{r.state ?? ''}</span>
            </Link>
          ))}
        </div>
      )}
    </div>
  );
}
