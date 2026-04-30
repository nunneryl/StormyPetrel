import { tierFromStars } from '@/lib/ratings';

type Size = 'sm' | 'md' | 'lg' | 'xl';

const SIZES: Record<Size, string> = {
  sm: 'px-1.5 py-0.5 text-[10px] tracking-widest2',
  md: 'px-2 py-1 text-xs tracking-widest2',
  lg: 'px-3 py-1.5 text-sm tracking-widest2',
  xl: 'px-4 py-2 text-base tracking-widest2',
};

export function RatingBadge({
  stars,
  size = 'md',
  className = '',
  glow = false,
}: {
  stars: number | null | undefined;
  size?: Size;
  className?: string;
  /** Add a subtle drop-glow halo. Use on prominent hero placements only. */
  glow?: boolean;
}) {
  const tier = tierFromStars(stars);
  const style = glow
    ? { boxShadow: `0 0 24px -4px ${tier.glow}` }
    : undefined;
  return (
    <span
      style={style}
      className={`inline-flex items-center font-bold uppercase rounded-md ${tier.bg} ${tier.fg} ${SIZES[size]} ${className}`}
    >
      {tier.label}
    </span>
  );
}
