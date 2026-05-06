// 0–5 star rating display, classic MSW-style. Half-step granularity.
//
// 0 stars renders as the FLAT word in muted gray — empty stars look
// confusing for "no surf" because they imply a rating exists; FLAT
// reads as an explicit "ocean's flat" verdict.
//
// Filled = #F59E0B (amber/gold), empty outline = #D1D5DB (light gray).
// Half-stars are a left-half gold over a full gray base, clipped via
// inset() so we don't pull in another SVG asset.

type Size = 'xs' | 'sm' | 'md' | 'lg' | 'xl';

const SIZE_PX: Record<Size, number> = {
  xs: 12,
  sm: 14,
  md: 16,
  lg: 20,
  xl: 24,
};

const FILLED = '#F59E0B';
const EMPTY = '#D1D5DB';
const FLAT_COLOR = '#94A3B8'; // text-muted

export function StarRating({
  score,
  size = 'md',
  showScore = false,
  className = '',
}: {
  /** 0..5, half-step granularity. null/undefined treated as 0 → FLAT. */
  score: number | null | undefined;
  size?: Size;
  /** Append the numeric score (e.g. "3.5") next to the stars. */
  showScore?: boolean;
  className?: string;
}) {
  const s = score ?? 0;
  const px = SIZE_PX[size];

  if (s <= 0) {
    return (
      <span
        className={`inline-flex items-center font-bold uppercase tracking-widest2 ${className}`}
        style={{ color: FLAT_COLOR, fontSize: Math.max(10, px * 0.65) }}
      >
        FLAT
      </span>
    );
  }

  const rounded = Math.round(s * 2) / 2;

  return (
    <span
      className={`inline-flex items-center ${className}`}
      style={{ gap: Math.max(1, px / 12) }}
      aria-label={`${rounded} out of 5 stars`}
    >
      {[0, 1, 2, 3, 4].map((i) => {
        const fill = Math.max(0, Math.min(1, rounded - i));
        return <Star key={i} fill={fill > 0 && fill < 1 ? 0.5 : (fill >= 1 ? 1 : 0)} size={px} />;
      })}
      {showScore && (
        <span
          className="ml-1.5 font-bold tabular-nums"
          style={{ color: '#0F172A', fontSize: Math.max(11, px * 0.75) }}
        >
          {rounded.toFixed(1)}
        </span>
      )}
    </span>
  );
}

function Star({ fill, size }: { fill: 0 | 0.5 | 1; size: number }) {
  if (fill === 1) return <StarSvg size={size} color={FILLED} />;
  if (fill === 0) return <StarSvg size={size} color={EMPTY} />;
  return (
    <span
      style={{
        position: 'relative',
        display: 'inline-block',
        width: size,
        height: size,
        lineHeight: 0,
      }}
    >
      <span style={{ position: 'absolute', inset: 0 }}>
        <StarSvg size={size} color={EMPTY} />
      </span>
      <span style={{ position: 'absolute', inset: 0, clipPath: 'inset(0 50% 0 0)' }}>
        <StarSvg size={size} color={FILLED} />
      </span>
    </span>
  );
}

function StarSvg({ size, color }: { size: number; color: string }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill={color}
      style={{ display: 'block' }}
      aria-hidden
    >
      <path d="M12 2 L14.6 8.6 L21.6 9.2 L16.3 13.8 L18 20.6 L12 16.9 L6 20.6 L7.7 13.8 L2.4 9.2 L9.4 8.6 Z" />
    </svg>
  );
}

/**
 * Rating-cell tint used in the forecast grid. Single function so the
 * forecast grid + any future leaderboard share the same buckets:
 *   4+   green tint
 *   2.5+ amber tint
 *   1+   red tint
 *   else gray
 */
export function ratingCellBg(score: number | null | undefined): string {
  const s = score ?? 0;
  if (s >= 4) return '#DCFCE7';   // green-100
  if (s >= 2.5) return '#FEF3C7'; // amber-100
  if (s >= 1) return '#FEE2E2';   // red-100
  return '#F3F4F6';               // gray-100
}
