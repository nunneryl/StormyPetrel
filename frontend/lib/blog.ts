import fs from 'node:fs';
import path from 'node:path';
import matter from 'gray-matter';
import { remark } from 'remark';
import remarkHtml from 'remark-html';

const BLOG_DIR = path.join(process.cwd(), 'content', 'blog');
// Average words-per-minute used by the reading-time estimator. 220 is the
// industry default for educated readers on English content.
const WPM = 220;

export type BlogFrontmatter = {
  title: string;
  description: string;
  date: string; // ISO yyyy-mm-dd
  author?: string;
  /** Optional category tag — surfaced as a pill on /blog and as a filter. */
  tag?: string;
};

export type BlogPost = BlogFrontmatter & {
  slug: string;
  contentHtml: string;
  readingMinutes: number;
};

export type BlogPostMeta = BlogFrontmatter & {
  slug: string;
  readingMinutes: number;
};

function readingTimeMinutes(text: string): number {
  const words = text.trim().split(/\s+/).filter(Boolean).length;
  return Math.max(1, Math.round(words / WPM));
}

function readFiles(): string[] {
  if (!fs.existsSync(BLOG_DIR)) return [];
  return fs
    .readdirSync(BLOG_DIR)
    .filter((f) => f.endsWith('.md') || f.endsWith('.mdx'));
}

export function listPosts(): BlogPostMeta[] {
  const files = readFiles();
  const posts = files.map((file) => {
    const slug = file.replace(/\.(md|mdx)$/, '');
    const raw = fs.readFileSync(path.join(BLOG_DIR, file), 'utf-8');
    const { data, content } = matter(raw);
    return {
      slug,
      readingMinutes: readingTimeMinutes(content),
      ...(data as BlogFrontmatter),
    };
  });
  // Newest first.
  return posts.sort((a, b) => (a.date > b.date ? -1 : 1));
}

export async function getPost(slug: string): Promise<BlogPost | null> {
  const candidates = [`${slug}.md`, `${slug}.mdx`];
  const found = candidates.find((f) =>
    fs.existsSync(path.join(BLOG_DIR, f)),
  );
  if (!found) return null;
  const raw = fs.readFileSync(path.join(BLOG_DIR, found), 'utf-8');
  const { data, content } = matter(raw);
  const processed = await remark().use(remarkHtml).process(content);
  return {
    slug,
    contentHtml: processed.toString(),
    readingMinutes: readingTimeMinutes(content),
    ...(data as BlogFrontmatter),
  };
}

/**
 * Return the previous (newer) and next (older) post in publication order
 * for a given slug. Used to render prev/next links at the bottom of each
 * post.
 */
export function adjacentPosts(slug: string): {
  prev: BlogPostMeta | null;
  next: BlogPostMeta | null;
} {
  const posts = listPosts();
  const idx = posts.findIndex((p) => p.slug === slug);
  if (idx < 0) return { prev: null, next: null };
  return {
    prev: idx > 0 ? posts[idx - 1] : null,
    next: idx < posts.length - 1 ? posts[idx + 1] : null,
  };
}

/** Sorted unique tag list across all posts. */
export function allTags(): string[] {
  const tags = new Set<string>();
  for (const p of listPosts()) {
    if (p.tag) tags.add(p.tag);
  }
  return Array.from(tags).sort();
}
