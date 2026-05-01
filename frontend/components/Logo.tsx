import Image from 'next/image';

// Stormy Petrel brand mark.
//
// `size` controls the height of the petrel image in pixels (next/image
// scales the width to match). When `withText` is true, the wordmark
// "Stormy Petrel" sits to the right (nav usage); when false, only the
// image renders (homepage hero usage where the wordmark is implicit).
//
// The PNG itself lives at /public/logo.png. Deploy expects that file —
// if it's missing the alt-text falls back gracefully but no glyph
// renders, so don't ship without saving the image.

export function Logo({
  className = '',
  size = 28,
  withText = true,
  dark = false,
}: {
  className?: string;
  /** Height of the petrel image in px. */
  size?: number;
  /** Render "Stormy Petrel" wordmark beside the image. */
  withText?: boolean;
  /** Use the inverse text color (for the dark nav bar). */
  dark?: boolean;
}) {
  return (
    <span className={`inline-flex items-center gap-2 ${className}`}>
      <Image
        src="/logo.png"
        alt="Stormy Petrel"
        width={size * 1.6}
        height={size}
        priority
        // The image is the brand mark — disable Next's lazy loading +
        // intersection observer entirely so it doesn't flicker on
        // route changes.
        style={{ height: size, width: 'auto' }}
        unoptimized
      />
      {withText && (
        <span
          className={`font-bold tracking-tightish ${
            dark ? 'text-text_inv-primary' : 'text-text-primary'
          }`}
          style={{ fontSize: Math.max(14, size * 0.55) }}
        >
          Stormy Petrel
        </span>
      )}
    </span>
  );
}

/**
 * Stylized two-curve wave glyph. Kept around so 404 / loading states
 * and the footer can still render a recognizable mark even before the
 * petrel PNG ships.
 */
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
