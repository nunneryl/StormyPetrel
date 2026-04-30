import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import Link from 'next/link';
import { getPost, listPosts } from '@/lib/blog';

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

  return (
    <article className="mx-auto max-w-3xl px-4 sm:px-6 py-7 space-y-5">
      <header>
        <div className="text-[11px] uppercase tracking-widest2 text-text-muted">
          {new Date(post.date).toLocaleDateString('en-US', {
            year: 'numeric',
            month: 'long',
            day: 'numeric',
          })}
        </div>
        <h1 className="mt-1 text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          {post.title}
        </h1>
        <p className="mt-2 text-text-secondary text-base">{post.description}</p>
      </header>

      <div className="prose-blog">
        <div dangerouslySetInnerHTML={{ __html: post.contentHtml }} />
      </div>

      <div className="pt-6 border-t border-ink-600 text-sm">
        <Link href="/blog" className="text-text-secondary hover:text-cyan-400">
          ← Back to all posts
        </Link>
      </div>
    </article>
  );
}
