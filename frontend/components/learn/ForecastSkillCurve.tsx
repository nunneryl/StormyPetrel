// Static SVG. Forecast skill curve declining from 100% at day 0 to
// ~10% at day 14, with four colored trust zones in the background
// (Fact / Hypothesis / Pattern / Vibes). Skill points are
// interpolated linearly between published anchors.

const KEYS: [number, number][] = [
  [0, 100], [1, 96], [2, 92], [3, 86], [4, 79], [5, 71],
  [6, 63], [7, 55], [8, 47], [9, 38], [10, 30], [11, 23],
  [12, 17], [13, 13], [14, 10],
];

function interp(d: number): number {
  for (let i = 0; i < KEYS.length - 1; i++) {
    const [d1, s1] = KEYS[i];
    const [d2, s2] = KEYS[i + 1];
    if (d >= d1 && d <= d2) {
      const t = (d - d1) / (d2 - d1);
      return s1 + t * (s2 - s1);
    }
  }
  return KEYS[KEYS.length - 1][1];
}

const X_MIN = 70;
const X_MAX = 640;
const DAY_MAX = 14;
const Y_TOP = 40;
const Y_BOT = 240;

function tx(d: number) {
  return X_MIN + ((X_MAX - X_MIN) * d) / DAY_MAX;
}
function ty(s: number) {
  return Y_BOT - ((Y_BOT - Y_TOP) * s) / 100;
}

function buildPaths() {
  let stroke = '';
  let fill = `M ${X_MIN.toFixed(1)} ${Y_BOT} `;
  let first = true;
  for (let d = 0; d <= DAY_MAX + 0.001; d += 0.1) {
    const dd = Math.min(d, DAY_MAX);
    const x = tx(dd);
    const y = ty(interp(dd));
    stroke += (first ? 'M ' : 'L ') + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
    fill += 'L ' + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
    first = false;
  }
  fill += `L ${X_MAX.toFixed(1)} ${Y_BOT} Z`;
  return { strokePath: stroke, fillPath: fill };
}

const { strokePath, fillPath } = buildPaths();

const X_TICKS: [number, string][] = [
  [70, '0'],
  [151.4, '2'],
  [232.8, '4'],
  [354.9, '7'],
  [640, '14'],
];

export function ForecastSkillCurve() {
  return (
    <div className="my-6 rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5">
      <svg viewBox="0 0 680 320" className="block w-full h-auto" role="img">
        <title>Forecast skill vs lead time</title>
        <desc>
          A curve showing how surf forecast accuracy declines with forecast
          lead time, with annotated trust zones from Fact to Vibes.
        </desc>

        <text x="76" y="32" fontSize="12" textAnchor="end" fill="#64748B">
          Forecast accuracy
        </text>

        <rect x="70" y="40" width="81.4" height="200" fill="#1D9E75" fillOpacity="0.14" />
        <rect x="151.4" y="40" width="81.4" height="200" fill="#185FA5" fillOpacity="0.12" />
        <rect x="232.8" y="40" width="122.1" height="200" fill="#BA7517" fillOpacity="0.12" />
        <rect x="354.9" y="40" width="285.1" height="200" fill="#D85A30" fillOpacity="0.10" />

        <text x="110.7" y="58" textAnchor="middle" fontSize="13" fontWeight="500" fill="#0F6E56">
          Fact
        </text>
        <text x="192.1" y="58" textAnchor="middle" fontSize="13" fontWeight="500" fill="#0F4B85">
          Hypothesis
        </text>
        <text x="293.8" y="58" textAnchor="middle" fontSize="13" fontWeight="500" fill="#854F0B">
          Pattern
        </text>
        <text x="497.4" y="58" textAnchor="middle" fontSize="13" fontWeight="500" fill="#993C1D">
          Vibes
        </text>

        <text x="110.7" y="74" textAnchor="middle" fontSize="11" fill="#64748B">
          Treat as fact
        </text>
        <text x="192.1" y="74" textAnchor="middle" fontSize="11" fill="#64748B">
          Working hypothesis
        </text>
        <text x="293.8" y="74" textAnchor="middle" fontSize="11" fill="#64748B">
          Pattern recognition
        </text>
        <text x="497.4" y="74" textAnchor="middle" fontSize="11" fill="#64748B">
          Don&rsquo;t lock plans
        </text>

        <line x1="66" y1="40" x2="70" y2="40" stroke="#E2E8F0" strokeWidth="0.5" />
        <text x="62" y="44" textAnchor="end" fontSize="11" fill="#64748B">
          100%
        </text>
        <line x1="66" y1="140" x2="70" y2="140" stroke="#E2E8F0" strokeWidth="0.5" />
        <text x="62" y="144" textAnchor="end" fontSize="11" fill="#64748B">
          50%
        </text>
        <line x1="66" y1="240" x2="70" y2="240" stroke="#E2E8F0" strokeWidth="0.5" />
        <text x="62" y="244" textAnchor="end" fontSize="11" fill="#64748B">
          0%
        </text>

        <line
          x1="70"
          y1="140"
          x2="640"
          y2="140"
          stroke="#E2E8F0"
          strokeWidth="0.5"
          strokeDasharray="2 3"
        />
        <line x1="70" y1="40" x2="70" y2="240" stroke="#E2E8F0" strokeWidth="0.5" />
        <line x1="70" y1="240" x2="640" y2="240" stroke="#CBD5E1" strokeWidth="0.5" />

        <path d={fillPath} fill="#185FA5" fillOpacity="0.10" />
        <path d={strokePath} stroke="#0F4B85" strokeWidth="2" fill="none" />

        {X_TICKS.map(([x, label]) => (
          <g key={label}>
            <line x1={x} y1="240" x2={x} y2="246" stroke="#64748B" strokeWidth="0.5" />
            <text x={x} y="262" textAnchor="middle" fontSize="11" fill="#64748B">
              {label}
            </text>
          </g>
        ))}

        <text x="355" y="288" textAnchor="middle" fontSize="12" fill="#64748B">
          Forecast lead time (days)
        </text>
      </svg>
    </div>
  );
}
