// Mini compass showing where a swell is coming FROM (NWS bearing).
//
// A thin gray ring with N/E/S/W tick marks, plus a colored line
// from the edge at `deg` toward the center. So a 315° (NW) swell
// renders the line in the NW quadrant pointing inward — visually
// "the swell is arriving from over there."
//
// The cardinal label and degree text live OUTSIDE the circle so the
// glyph reads cleanly at 24px in dense forecast rows. Both are
// rendered by callers (so they can lay them out as flex siblings).

const RING_COLOR = '#D1D5DB';
const TICK_COLOR = '#94A3B8';
const DEFAULT_DIR_COLOR = '#0369A1'; // brand swell blue

export function SwellCompass({
  deg,
  size = 24,
  color = DEFAULT_DIR_COLOR,
  className = '',
}: {
  /** Meteorological "from" bearing in degrees, 0 = north. */
  deg: number | null | undefined;
  size?: number;
  color?: string;
  className?: string;
}) {
  const radius = size / 2;
  const cx = radius;
  const cy = radius;
  // Stroke width scales with size — at 24px we want ~2px, at 40px ~3px.
  const strokeW = Math.max(2, size / 14);
  const ringStroke = 1;
  const ringR = radius - ringStroke / 2;
  const tickInner = ringR - Math.max(2, size / 10);

  if (deg === null || deg === undefined || Number.isNaN(deg)) {
    return (
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className={className}
        aria-hidden
      >
        <circle cx={cx} cy={cy} r={ringR} fill="none" stroke={RING_COLOR} strokeWidth={ringStroke} />
        {[0, 90, 180, 270].map((a) => (
          <Tick key={a} cx={cx} cy={cy} outer={ringR} inner={tickInner} angle={a} />
        ))}
      </svg>
    );
  }

  // Endpoint at the ring on the FROM bearing — line goes from that
  // edge point inward to the center. 0° is north; SVG y points down,
  // so cos drives Y inverted.
  const rad = (deg * Math.PI) / 180;
  const tipX = cx + ringR * Math.sin(rad);
  const tipY = cy - ringR * Math.cos(rad);

  return (
    <svg
      width={size}
      height={size}
      viewBox={`0 0 ${size} ${size}`}
      className={className}
      aria-hidden
    >
      <circle cx={cx} cy={cy} r={ringR} fill="none" stroke={RING_COLOR} strokeWidth={ringStroke} />
      {[0, 90, 180, 270].map((a) => (
        <Tick key={a} cx={cx} cy={cy} outer={ringR} inner={tickInner} angle={a} />
      ))}
      <line
        x1={tipX}
        y1={tipY}
        x2={cx}
        y2={cy}
        stroke={color}
        strokeWidth={strokeW}
        strokeLinecap="round"
      />
      <circle cx={cx} cy={cy} r={Math.max(1.2, size / 18)} fill={color} />
    </svg>
  );
}

function Tick({
  cx,
  cy,
  outer,
  inner,
  angle,
}: {
  cx: number;
  cy: number;
  outer: number;
  inner: number;
  angle: number;
}) {
  const rad = (angle * Math.PI) / 180;
  const sx = cx + outer * Math.sin(rad);
  const sy = cy - outer * Math.cos(rad);
  const ex = cx + inner * Math.sin(rad);
  const ey = cy - inner * Math.cos(rad);
  return <line x1={sx} y1={sy} x2={ex} y2={ey} stroke={TICK_COLOR} strokeWidth={1} />;
}
