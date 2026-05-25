// Three side-by-side sea state cards: clean groundswell, mixed sea,
// pure wind sea. Each card has bulk parameters + a sketched energy
// breakdown so the reader can see what the DPD/APD gap means in
// terms of the actual energy distribution.

type Peak = { p: number; sigma: number; w: number };

const P_MIN = 4;
const P_MAX = 20;
const X_MIN = 10;
const X_MAX = 170;
const Y_BASE = 60;
const Y_PEAK = 14;

function pToX(p: number) {
  return X_MIN + ((p - P_MIN) * (X_MAX - X_MIN)) / (P_MAX - P_MIN);
}
function gauss(p: number, mu: number, sigma: number) {
  return Math.exp(-Math.pow(p - mu, 2) / (2 * sigma * sigma));
}
function buildSpectrum(peaks: Peak[]) {
  const fn = (p: number) => peaks.reduce((s, k) => s + k.w * gauss(p, k.p, k.sigma), 0);
  let max = 0;
  for (let p = P_MIN; p <= P_MAX; p += 0.05) max = Math.max(max, fn(p));
  let stroke = '';
  let fill = `M ${X_MIN.toFixed(1)} ${Y_BASE} `;
  let first = true;
  for (let p = P_MIN; p <= P_MAX + 0.001; p += 0.15) {
    const pp = Math.min(p, P_MAX);
    const x = pToX(pp);
    const y = Y_BASE - (fn(pp) / max) * (Y_BASE - Y_PEAK);
    stroke += (first ? 'M ' : 'L ') + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
    fill += 'L ' + x.toFixed(1) + ' ' + y.toFixed(1) + ' ';
    first = false;
  }
  fill += `L ${X_MAX.toFixed(1)} ${Y_BASE} Z`;
  return { stroke, fill };
}

type Scenario = {
  key: string;
  title: string;
  subtitle: string;
  titleColorClass: string;
  stats: { wvht: string; dpd: string; apd: string };
  peaks: Peak[];
  dpd: number;
  dpdLabel: string;
  strokeColor: string;
  fillColor: string;
  desc: string;
  takeaway: string;
};

const SCENARIOS: Scenario[] = [
  {
    key: 'clean',
    title: 'Clean groundswell',
    subtitle: 'One organized swell, nothing else',
    titleColorClass: 'text-emerald-700',
    stats: { wvht: '4 ft', dpd: '16 s', apd: '13 s' },
    peaks: [{ p: 16, sigma: 1.3, w: 1.0 }],
    dpd: 16,
    dpdLabel: 'DPD 16s',
    strokeColor: '#0F6E56',
    fillColor: '#1D9E75',
    desc: 'Energy concentrated at one period',
    takeaway: 'Smooth wave faces and good lines at the beach.',
  },
  {
    key: 'mixed',
    title: 'Mixed sea',
    subtitle: 'Swell hiding under wind chop',
    titleColorClass: 'text-amber-700',
    stats: { wvht: '4 ft', dpd: '16 s', apd: '7 s' },
    peaks: [
      { p: 16, sigma: 1.4, w: 1.0 },
      { p: 6, sigma: 1.4, w: 0.7 },
    ],
    dpd: 16,
    dpdLabel: 'DPD 16s',
    strokeColor: '#854F0B',
    fillColor: '#BA7517',
    desc: 'Energy split between long and short periods',
    takeaway: 'Bumpy, disorganized faces despite the long DPD.',
  },
  {
    key: 'wind',
    title: 'Pure wind sea',
    subtitle: 'Local chop, no groundswell',
    titleColorClass: 'text-slate-500',
    stats: { wvht: '4 ft', dpd: '6 s', apd: '5 s' },
    peaks: [{ p: 6, sigma: 1.6, w: 1.0 }],
    dpd: 6,
    dpdLabel: 'DPD 6s',
    strokeColor: '#5F5E5A',
    fillColor: '#888780',
    desc: 'Energy concentrated at short periods',
    takeaway: "Not surf. Don't drive.",
  },
];

const COMPUTED = SCENARIOS.map((s) => ({
  ...s,
  paths: buildSpectrum(s.peaks),
  dpdX: pToX(s.dpd),
}));

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="text-center">
      <div className="text-[11px] font-mono tracking-wider text-text-muted">{label}</div>
      <div className="text-sm font-mono font-medium mt-0.5 text-text-primary">{value}</div>
    </div>
  );
}

export function SeaStateTriad() {
  return (
    <div className="my-6 grid gap-3 md:grid-cols-3">
      {COMPUTED.map((s) => (
        <div
          key={s.key}
          className="rounded-xl border border-ink-600 bg-white shadow-card p-4 flex flex-col"
        >
          <div className={`text-base font-medium ${s.titleColorClass}`}>{s.title}</div>
          <div className="text-xs text-text-muted mt-0.5 mb-3">{s.subtitle}</div>
          <div className="grid grid-cols-3 gap-1.5 py-2 border-y border-ink-600 mb-2">
            <Stat label="WVHT" value={s.stats.wvht} />
            <Stat label="DPD" value={s.stats.dpd} />
            <Stat label="APD" value={s.stats.apd} />
          </div>
          <svg viewBox="0 0 180 80" className="w-full max-w-[180px] mx-auto my-1">
            <path d={s.paths.fill} fill={s.fillColor} fillOpacity="0.22" />
            <path d={s.paths.stroke} stroke={s.strokeColor} strokeWidth="1.5" fill="none" />
            <line
              x1={s.dpdX}
              y1="14"
              x2={s.dpdX}
              y2="60"
              stroke={s.strokeColor}
              strokeWidth="0.8"
              strokeDasharray="2 2"
            />
            <text
              x={s.dpdX}
              y="11"
              textAnchor="middle"
              fontSize="11"
              fontWeight="500"
              fill={s.strokeColor}
            >
              {s.dpdLabel}
            </text>
            <line x1="10" y1="60" x2="170" y2="60" stroke="#E2E8F0" strokeWidth="0.5" />
            <text x="10" y="74" fontSize="11" fill="#94A3B8">
              short
            </text>
            <text x="170" y="74" textAnchor="end" fontSize="11" fill="#94A3B8">
              long
            </text>
          </svg>
          <div className="text-xs text-text-muted text-center mb-3">{s.desc}</div>
          <div className="text-xs text-text-secondary mt-auto">{s.takeaway}</div>
        </div>
      ))}
    </div>
  );
}
