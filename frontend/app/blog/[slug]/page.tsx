import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import Link from 'next/link';
import { adjacentPosts, getPost, listPosts } from '@/lib/blog';

type Params = { slug: string };

export async function generateStaticParams() {
  return listPosts().map((p) => ({ slug: p.slug }));
}

export async function generateMetadata({
  params,
}: {
  params: Promise<Params>;
}): Promise<Metadata> {
  const { slug } = await params;
  const post = await getPost(slug);
  if (!post) return { title: 'Post not found' };
  return {
    title: post.title,
    description: post.description,
    alternates: { canonical: `/blog/${post.slug}` },
    openGraph: {
      type: 'article',
      title: post.title,
      description: post.description,
      publishedTime: post.date,
    },
    twitter: {
      card: 'summary_large_image',
      title: post.title,
      description: post.description,
    },
  };
}

export default async function BlogPostPage({
  params,
}: {
  params: Promise<Params>;
}) {
  const { slug } = await params;
  const post = await getPost(slug);
  if (!post) notFound();

  const { prev, next } = adjacentPosts(slug);

  return (
    <article className="mx-auto max-w-3xl px-4 sm:px-6 py-7 space-y-5">
      <Link
        href="/blog"
        className="inline-flex items-center text-sm text-text-secondary hover:text-cyan-600"
      >
        ← Back to blog
      </Link>

      <header>
        <div className="flex items-center gap-2 text-[11px] uppercase tracking-widest2 text-text-muted">
          <time>
            {new Date(post.date).toLocaleDateString('en-US', {
              year: 'numeric',
              month: 'long',
              day: 'numeric',
            })}
          </time>
          <span>·</span>
          <span>{post.readingMinutes} min read</span>
          {post.tag && (
            <>
              <span>·</span>
              <span className="capitalize text-text-secondary">{post.tag}</span>
            </>
          )}
        </div>
        <h1 className="mt-1 text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          {post.title}
        </h1>
        <p className="mt-2 text-text-secondary text-base">{post.description}</p>
      </header>

      <div className="prose-blog">
        <div dangerouslySetInnerHTML={{ __html: post.contentHtml }} />
      </div>

      {/* Prev / next nav */}
      {(prev || next) && (
        <nav className="pt-6 border-t border-ink-600 grid gap-3 sm:grid-cols-2">
          {prev ? (
            <Link
              href={`/blog/${prev.slug}`}
              className="rounded-xl border border-ink-600 bg-white shadow-card p-4 hover:bg-ink-800 transition group"
            >
              <div className="text-[10px] uppercase tracking-widest2 text-text-muted">
                ← Newer post
              </div>
              <div className="mt-0.5 text-sm font-bold text-text-primary group-hover:text-cyan-600">
                {prev.title}
              </div>
            </Link>
          ) : (
            <span />
          )}
          {next ? (
            <Link
              href={`/blog/${next.slug}`}
              className="rounded-xl border border-ink-600 bg-white shadow-card p-4 hover:bg-ink-800 transition group sm:text-right"
            >
              <div className="text-[10px] uppercase tracking-widest2 text-text-muted">
                Older post →
              </div>
              <div className="mt-0.5 text-sm font-bold text-text-primary group-hover:text-cyan-600">
                {next.title}
              </div>
            </Link>
          ) : (
            <span />
          )}
        </nav>
      )}
    </article>
  );
}
