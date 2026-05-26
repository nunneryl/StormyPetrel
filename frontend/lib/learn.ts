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
  {
    slug: 'swell-direction',
    title: 'Why swell direction matters',
    description:
      'The reason two spots ten miles apart get completely different waves. Interactive maps show how spot orientation and the swell window decide what your beach receives.',
    readTime: '7 min read',
  },
  {
    slug: 'wind',
    title: 'How Wind Makes or Breaks a Surf Session',
    description:
      'Why offshore wind makes good waves and onshore ruins them, the daily sea breeze cycle, regional winds like Santa Anas and trades, and how to read a wind forecast.',
    readTime: '9 min read',
  },
  {
    slug: 'tides',
    title: 'Tides and Surfing: Why Depth Changes Everything',
    description:
      'How tide moves the depth at your break — and why a foot of swing reshapes the wave. Why reefs like mid tide, points are forgiving, beach breaks want incoming mid, and shorebreaks need low.',
    readTime: '8 min read',
  },
  {
    slug: 'buoys',
    title: 'How to Read a Buoy Report',
    description:
      'What WVHT, DPD, APD, and MWD actually mean, why the gap between DPD and APD is the most useful single diagnostic, why you should read two buoys, and how to calculate lead time from a deep-water station.',
    readTime: '10 min read',
  },
  {
    slug: 'forecasts',
    title: 'How a Surf Forecast Actually Works',
    description:
      'A surf forecast is a stack of nested models: weather → global wave → nearshore transformation → wind and tide blend → human overlay → star rating. How each layer works and why accuracy drops off after three days.',
    readTime: '11 min read',
  },
];

export function getLearnArticle(slug: string): LearnArticle | null {
  return LEARN_ARTICLES.find((a) => a.slug === slug) ?? null;
}
