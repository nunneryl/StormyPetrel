import { tierFromStars } from '@/lib/ratings';

type Size = 'sm' | 'md' | 'lg';

const SIZES: Record<Size, string> = {
  sm: 'px-1.5 py-0.5 text-[10px] tracking-wider',
  md: 'px-2 py-1 text-xs tracking-wider',
  lg: 'px-3 py-1.5 text-sm tracking-widest',
};

export function RatingBadge({
  stars,
  size = 'md',
  className = '',
}: {
  stars: number | null | undefined;
  size?: Size;
  className?: string;
}) {
  const tier = tierFromStars(stars);
  return (
    <span
      className={`inline-flex items-center font-bold uppercase rounded ${tier.bg} ${tier.fg} ${SIZES[size]} ${className}`}
    >
      {tier.label}
    </span>
  );
}
