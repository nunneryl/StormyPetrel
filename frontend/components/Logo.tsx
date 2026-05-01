// Stormy Petrel wordmark — wave glyph + project name. The `dark` prop
// flips the text color to white so the same component reads on the
// dark nav bar AND on light page surfaces.

export function Logo({
  className = '',
  dark = false,
}: {
  className?: string;
  dark?: boolean;
}) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <WaveGlyph className={dark ? 'text-cyan-400' : 'text-cyan-500'} />
      <span
        className={`font-bold tracking-tightish ${
          dark ? 'text-text_inv-primary' : 'text-text-primary'
        }`}
      >
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
