// Learn-section article index. Each route under /learn/[slug] is
// hand-built (not MDX) so it can embed its own interactive
// components; this list just powers the index card grid + future
// "next article" navigation.

export type LearnArticle = {
  slug: string;
  title: string;
  description: string;
  /** Plain "8 min read" string; displayed on the index card. */
  readTime: string;
};

export const LEARN_ARTICLES: LearnArticle[] = [
  {
    slug: 'swell-period',
    title: 'Understanding swell period',
    description:
      'Why the number labeled Tp on a buoy report matters more than wave height — and how to read it like a local.',
    readTime: '8 min read',
  },
];

export function getLearnArticle(slug: string): LearnArticle | null {
  return LEARN_ARTICLES.find((a) => a.slug === slug) ?? null;
}
