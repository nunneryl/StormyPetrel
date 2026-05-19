// Static four-bucket period reference. Each bucket carries its
// period range, label, and a one-paragraph description on a softly
// tinted background. No interactivity — this is a glanceable card,
// not a learn-by-touch widget.

type Bucket = {
  range: string;
  title: string;
  description: string;
  bg: string;
  text: string;
  accent: string;
};

const BUCKETS: Bucket[] = [
  {
    range: '5–8s',
    title: 'Wind swell',
    description:
      'Local wind-generated waves. Short, disorganized, breaks close to its offshore height. Usually choppy and gutless.',
    bg: 'bg-red-50',
    text: 'text-red-900',
    accent: 'border-red-200',
  },
  {
    range: '9–11s',
    title: 'Mid-range swell',
    description:
      'Generated 500–1000 miles away. Starting to organize into lines. Decent surf if the direction is right and wind cooperates.',
    bg: 'bg-amber-50',
    text: 'text-amber-900',
    accent: 'border-amber-200',
  },
  {
    range: '12–15s',
    title: 'Groundswell',
    description:
      'Distant storm energy. Clean, organized lines with significant shoaling amplification. Most spots fire in this range.',
    bg: 'bg-emerald-50',
    text: 'text-emerald-900',
    accent: 'border-emerald-200',
  },
  {
    range: '16s+',
    title: 'Long-range groundswell',
    description:
      'Powerful energy from a major storm thousands of miles away. Major amplification on shallow reefs and points. These are the days you call in sick.',
    bg: 'bg-sky-50',
    text: 'text-sky-900',
    accent: 'border-sky-200',
  },
];

export function PeriodThresholds() {
  return (
    <div className="rounded-xl border border-ink-600 bg-white shadow-card p-4 sm:p-5 my-6 space-y-2.5">
      {BUCKETS.map((b) => (
        <div
          key={b.range}
          className={`rounded-lg border ${b.accent} ${b.bg} p-3.5 sm:p-4`}
        >
          <div className={`flex items-baseline gap-2.5 ${b.text}`}>
            <span className="font-mono font-bold tabular-nums text-sm">
              {b.range}
            </span>
            <span className="font-bold text-base">{b.title}</span>
          </div>
          <p className={`mt-1.5 text-sm leading-relaxed ${b.text}`}>
            {b.description}
          </p>
        </div>
      ))}
    </div>
  );
}
