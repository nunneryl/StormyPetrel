import type { Metadata } from 'next';
import Link from 'next/link';
import { listPosts } from '@/lib/blog';

export const metadata: Metadata = {
  title: 'Blog',
  description:
    'Posts on surf forecasting methodology, our open data sources, and how Stormy Petrel rates conditions.',
  alternates: { canonical: '/blog' },
};

export default function BlogIndex() {
  const posts = listPosts();
  return (
    <div className="mx-auto max-w-3xl px-4 sm:px-6 py-7 space-y-6">
      <header>
        <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">
          Writing
        </div>
        <h1 className="text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          Blog
        </h1>
        <p className="mt-1 text-text-secondary text-sm">
          Methodology notes, surf-forecasting fundamentals, and what we&apos;re working on.
        </p>
      </header>

      {posts.length === 0 ? (
        <div className="rounded-xl border border-ink-600 bg-ink-800/60 p-8 text-center text-text-muted">
          No posts yet.
        </div>
      ) : (
        <ul className="space-y-3">
          {posts.map((p) => (
            <li key={p.slug}>
              <Link
                href={`/blog/${p.slug}`}
                className="block rounded-xl border border-ink-600 bg-ink-800/60 hover:border-cyan-500 hover:bg-ink-700/60 transition p-5 group"
              >
                <div className="text-[11px] uppercase tracking-widest2 text-text-muted">
                  {new Date(p.date).toLocaleDateString('en-US', {
                    year: 'numeric',
                    month: 'short',
                    day: 'numeric',
                  })}
                </div>
                <div className="mt-1 font-bold text-lg text-text-primary group-hover:text-cyan-400 transition-colors">
                  {p.title}
                </div>
                <div className="mt-1 text-sm text-text-secondary">
                  {p.description}
                </div>
              </Link>
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
