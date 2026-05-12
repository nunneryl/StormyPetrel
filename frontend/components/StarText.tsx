import { Fragment } from 'react';

// Wrap runs of star-rating glyphs (★ ☆ ½ ¼ ¾) in a gold span so star
// ratings inside the AI-written report copy stand out from the
// surrounding paragraph text.
//
// The model is prompted to use the ★ form (e.g. "★★½ 3.8ft"); the
// regex is liberal so that ☆ for half-empty stars or stray vulgar
// fractions also get colored consistently.

const STAR_RE = /([★☆½¼¾]+)/g;
const STAR_CHARS = new Set(['★', '☆', '½', '¼', '¾']);
const STAR_COLOR = '#F59E0B'; // amber/gold — same as StarRating

function isStarRun(s: string): boolean {
  return s.length > 0 && STAR_CHARS.has(s[0]);
}

export function StarText({
  text,
  className,
}: {
  text: string;
  className?: string;
}) {
  // String.split with a capturing group returns alternating
  // non-match / match segments, so isStarRun on the first char is
  // enough to identify the matched runs without re-testing the regex
  // (which is stateful when global).
  const parts = text.split(STAR_RE);
  return (
    <span className={className}>
      {parts.map((part, i) =>
        isStarRun(part) ? (
          <span key={i} style={{ color: STAR_COLOR, fontWeight: 700 }}>
            {part}
          </span>
        ) : (
          <Fragment key={i}>{part}</Fragment>
        ),
      )}
    </span>
  );
}
