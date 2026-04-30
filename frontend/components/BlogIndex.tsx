'use client';

import { useMemo, useState } from 'react';
import Link from 'next/link';
import type { BlogPostMeta } from '@/lib/blog';

const TAG_ALL = '__all__';

export function BlogIndex({
  posts,
  tags,
}: {
  posts: BlogPostMeta[];
  tags: string[];
}) {
  const [tag, setTag] = useState<string>(TAG_ALL);
  const filtered = useMemo(
    () => (tag === TAG_ALL ? posts : posts.filter((p) => p.tag === tag)),
    [posts, tag],
  );

  return (
    <>
      {tags.length > 0 && (
        <div className="flex items-center gap-2 mb-2 flex-wrap">
          <button
            type="button"
            onClick={() => setTag(TAG_ALL)}
            className={`px-3 py-1.5 rounded-full text-xs font-medium transition ${
              tag === TAG_ALL
                ? 'bg-cyan-500 text-white'
                : 'bg-ink-900 text-text-secondary hover:text-text-primary border border-ink-600'
            }`}
          >
            All
          </button>
          {tags.map((t) => (
            <button
              key={t}
              type="button"
              onClick={() => setTag(t)}
              className={`px-3 py-1.5 rounded-full text-xs font-medium transition capitalize ${
                tag === t
                  ? 'bg-cyan-500 text-white'
                  : 'bg-ink-900 text-text-secondary hover:text-text-primary border border-ink-600'
              }`}
            >
              {t}
            </button>
          ))}
        </div>
      )}

      {filtered.length === 0 ? (
        <div className="rounded-xl border border-ink-600 bg-white p-8 text-center text-text-muted">
          No posts in this category yet.
        </div>
      ) : (
        <ul className="space-y-3">
          {filtered.map((p) => (
            <li key={p.slug}>
              <Link
                href={`/blog/${p.slug}`}
                className="block rounded-xl border border-ink-600 bg-white shadow-card hover:bg-ink-800 transition p-5 group"
              >
                <div className="flex items-center gap-2 text-[11px] uppercase tracking-widest2 text-text-muted">
                  <time>
                    {new Date(p.date).toLocaleDateString('en-US', {
                      year: 'numeric',
                      month: 'short',
                      day: 'numeric',
                    })}
                  </time>
                  <span>·</span>
                  <span>{p.readingMinutes} min read</span>
                  {p.tag && (
                    <>
                      <span>·</span>
                      <span className="capitalize text-text-secondary">{p.tag}</span>
                    </>
                  )}
                </div>
                <div className="mt-1 font-bold text-lg text-text-primary group-hover:text-cyan-600 transition-colors">
                  {p.title}
                </div>
                <div className="mt-1 text-sm text-text-secondary">{p.description}</div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </>
  );
}
