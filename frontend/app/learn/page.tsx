import type { Metadata } from 'next';
import Link from 'next/link';
import { LEARN_ARTICLES } from '@/lib/learn';

export const revalidate = 3600;

export const metadata: Metadata = {
  title: { absolute: 'Learn to read the forecast | Stormy Petrel' },
  description:
    'Guides that help you understand what the surf forecast numbers mean and why they matter — swell period, wind quality, tides, and more.',
  alternates: { canonical: '/learn' },
  openGraph: {
    title: 'Learn to read the forecast | Stormy Petrel',
    description:
      'Guides that help you understand what the surf forecast numbers mean and why they matter.',
    type: 'website',
  },
};

export default function LearnIndexPage() {
  return (
    <div className="mx-auto max-w-5xl px-4 sm:px-6 py-10 sm:py-14">
      <header className="mb-8 sm:mb-10 max-w-[700px]">
        <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">
          Learn
        </div>
        <h1 className="mt-1 text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          Learn to read the forecast
        </h1>
        <p className="mt-3 text-base sm:text-lg text-text-secondary leading-relaxed">
          Guides that help you understand what the numbers mean and why they
          matter.
        </p>
      </header>

      <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4">
        {LEARN_ARTICLES.map((a) => (
          <Link
            key={a.slug}
            href={`/learn/${a.slug}`}
            className="group block rounded-xl border border-ink-600 bg-white shadow-card hover:border-cyan-500 transition p-5 flex flex-col"
          >
            <div className="text-[10px] uppercase tracking-widest2 text-text-muted">
              {a.readTime}
            </div>
            <h2 className="mt-2 text-lg font-bold tracking-tightish text-text-primary group-hover:text-cyan-600">
              {a.title}
            </h2>
            <p className="mt-2 text-sm text-text-secondary leading-relaxed flex-1">
              {a.description}
            </p>
            <span className="mt-4 text-xs font-bold text-cyan-600 group-hover:underline">
              Read →
            </span>
          </Link>
        ))}
      </div>
    </div>
  );
}
