import { degToCardinal } from '@/lib/formatting';

export function CompassArrow({
  deg,
  size = 16,
  showLabel = true,
  // Meteorological convention: dir is the direction the swell/wind is coming
  // FROM. Arrow points the way it's heading (i.e. away from the source).
  className = '',
  color = 'currentColor',
}: {
  deg: number | null | undefined;
  size?: number;
  showLabel?: boolean;
  className?: string;
  color?: string;
}) {
  if (deg === null || deg === undefined || Number.isNaN(deg)) {
    return <span className={`text-slate-500 ${className}`}>—</span>;
  }
  const arrowDeg = (deg + 180) % 360;
  return (
    <span className={`inline-flex items-center gap-1 ${className}`}>
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        style={{ transform: `rotate(${arrowDeg}deg)` }}
        aria-hidden
      >
        <path
          d="M12 2 L18 20 L12 16 L6 20 Z"
          fill={color}
          stroke={color}
          strokeWidth="0.5"
        />
      </svg>
      {showLabel && <span className="font-mono text-xs text-slate-300">{degToCardinal(deg)}</span>}
    </span>
  );
}
