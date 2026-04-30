import fs from 'node:fs';
import path from 'node:path';
import matter from 'gray-matter';
import { remark } from 'remark';
import remarkHtml from 'remark-html';

const BLOG_DIR = path.join(process.cwd(), 'content', 'blog');

export type BlogFrontmatter = {
  title: string;
  description: string;
  date: string; // ISO yyyy-mm-dd
  author?: string;
};

export type BlogPost = BlogFrontmatter & {
  slug: string;
  contentHtml: string;
};

export type BlogPostMeta = BlogFrontmatter & {
  slug: string;
};

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
    const { data } = matter(raw);
    return { slug, ...(data as BlogFrontmatter) };
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
    ...(data as BlogFrontmatter),
  };
}
