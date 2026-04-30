import type { Metadata } from 'next';
import { allTags, listPosts } from '@/lib/blog';
import { BlogIndex } from '@/components/BlogIndex';

export const metadata: Metadata = {
  title: 'Blog',
  description:
    'Posts on surf forecasting methodology, our open data sources, and how Stormy Petrel rates conditions.',
  alternates: { canonical: '/blog' },
};

export default function BlogIndexPage() {
  const posts = listPosts();
  const tags = allTags();

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

      <BlogIndex posts={posts} tags={tags} />
    </div>
  );
}
