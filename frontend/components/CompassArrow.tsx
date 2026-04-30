import { degToCardinal } from '@/lib/formatting';

type Variant = 'swell' | 'wind' | 'neutral';

const COLOR: Record<Variant, string> = {
  swell:   '#38BDF8',
  wind:    '#A3E635',
  neutral: '#94A3B8',
};

export function CompassArrow({
  deg,
  size = 16,
  variant = 'swell',
  showLabel = true,
  className = '',
}: {
  deg: number | null | undefined;
  size?: number;
  variant?: Variant;
  showLabel?: boolean;
  className?: string;
}) {
  if (deg === null || deg === undefined || Number.isNaN(deg)) {
    return <span className={`text-text-muted ${className}`}>—</span>;
  }
  const color = COLOR[variant];
  // Meteorological direction is the bearing the swell/wind is coming
  // FROM. The arrow points the way energy is heading (away from source).
  const arrowDeg = (deg + 180) % 360;
  return (
    <span className={`inline-flex items-center gap-1.5 ${className}`}>
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        style={{ transform: `rotate(${arrowDeg}deg)`, color }}
        aria-hidden
      >
        <path
          d="M12 3 L18 18 L12 14 L6 18 Z"
          fill="currentColor"
          stroke="currentColor"
          strokeWidth="0.6"
          strokeLinejoin="round"
        />
      </svg>
      {showLabel && (
        <span className="font-mono text-[11px] text-text-secondary tabular-nums">
          {degToCardinal(deg)}
        </span>
      )}
    </span>
  );
}
