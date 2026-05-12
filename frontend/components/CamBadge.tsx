// Small camera glyph that flags a spot as having a live cam. Drops in
// next to spot names on the homepage leaderboard, region lists, and
// (as an inline SVG string) inside the map popup. Renders nothing when
// `hasCam` is false so callers can stamp it unconditionally.

export function CamBadge({
  hasCam,
  size = 12,
  className = '',
}: {
  hasCam: boolean;
  size?: number;
  className?: string;
}) {
  if (!hasCam) return null;
  return (
    <span
      title="Live cam available"
      aria-label="Live cam available"
      className={`inline-flex items-center text-cyan-600 ${className}`}
    >
      <svg
        width={size}
        height={size}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
        aria-hidden
      >
        <path d="M23 7l-7 5 7 5V7z" />
        <rect x="1" y="5" width="15" height="14" rx="2" ry="2" />
      </svg>
    </span>
  );
}

/** Equivalent inline SVG markup for Leaflet popups, which take HTML
 *  strings rather than React. Same glyph, same color. */
export function camBadgeHtml(size = 12): string {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="#0369A1" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round" style="display:inline-block;vertical-align:-1px;"><path d="M23 7l-7 5 7 5V7z"/><rect x="1" y="5" width="15" height="14" rx="2" ry="2"/></svg>`;
}
