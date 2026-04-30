// Stormy Petrel wordmark — a small wave glyph + the project name in a
// bold tightly-tracked Inter cap. Renders as inline SVG so it shows up
// before any web fonts load and stays crisp at any zoom level.

export function Logo({ className = '' }: { className?: string }) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <WaveGlyph className="text-cyan-500" />
      <span className="font-bold tracking-tightish text-text-primary">
        Stormy Petrel
      </span>
    </span>
  );
}

export function WaveGlyph({
  className = '',
  size = 24,
}: {
  className?: string;
  size?: number;
}) {
  // Two stacked wave curves — quick to read at favicon size, holds up
  // at hero size. Stroke is currentColor so callers can recolor easily.
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      className={className}
    >
      <path d="M2 12 C 5 8, 8 8, 12 12 S 19 16, 22 12" />
      <path d="M2 17 C 5 13, 8 13, 12 17 S 19 21, 22 17" opacity="0.5" />
    </svg>
  );
}
