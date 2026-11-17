import type { Metadata } from 'next';
import {
  ALL,
  LEADS,
  LEAD_LABELS,
  MIN_PAIRS,
  PAIR_WINDOW_MINUTES,
  WINDOW_DAYS,
  buildTable,
  fetchAccuracyRows,
  fmtBias,
  fmtCount,
  fmtFt,
  fmtRange,
  type AccuracyRow,
  type SourceKey,
  type Table,
} from '@/lib/accuracy';

// Unlisted: noindex, not in the sitemap, not linked from the nav or footer (lib/accuracy.test.mts).
// The scores are written nightly (accuracy-score, 04:47 UTC), so a daily rebuild is enough.
export const revalidate = 86400;
// Static, and regenerated on the revalidate period where there is one: the root layout reads the
// spots past the Data Cache (lib/queries.ts fetchAllSpots), which would otherwise render this
// page on every request.
export const dynamic = 'force-static';

export const metadata: Metadata = {
  title: { absolute: 'How accurate are our forecasts? | Stormy Petrel' },
  description: 'Every night we check each forecast hour against what the buoys measured.',
  alternates: { canonical: '/accuracy' },
  robots: {
    index: false,
    follow: false,
    googleBot: { index: false, follow: false },
  },
};

// NWPS first: it sizes most of our spots. Our spot forecasts against their nearest buoy are left
// out on purpose: a spot is closer to shore than its buoy, often sheltered, so the gap mixes
// shelter with forecast error. That comparison stays in the weekly report.
const TABLES: { key: SourceKey; title: string; blurb: string }[] = [
  {
    key: 'nwps',
    title: 'NWPS at the buoys',
    blurb:
      "The National Weather Service's nearshore wave model, read at each buoy's own position, so " +
      'forecast and measurement are for the same place.',
  },
  {
    key: 'ww3',
    title: 'WAVEWATCH III at the buoys',
    blurb: "NOAA's global wave model, read at each buoy's own position.",
  },
];

function CellView({ table, region, lead }: { table: Table; region: string; lead: number }) {
  const c = table.cells[region]?.[lead];
  if (!c) return <td className="px-3 py-2 text-text-muted">&ndash;</td>;
  const range = fmtRange(c.first, c.last);
  if (c.biasFt === null || c.maeFt === null) {
    return (
      <td className="px-3 py-2 text-text-muted text-xs">
        Too few pairs
        <div>
          {c.n} &middot; {range}
        </div>
      </td>
    );
  }
  return (
    <td className="px-3 py-2">
      <div className="text-text-primary">
        <span title="Bias">{fmtBias(c.biasFt)}</span>
        <span className="text-text-muted"> / </span>
        <span title="Typical error">{fmtFt(c.maeFt)}</span>
      </div>
      <div className="text-xs text-text-muted">
        {fmtCount(c.n)} pairs &middot; {range}
      </div>
    </td>
  );
}

function SourceTable({ table }: { table: Table }) {
  if (table.regions.length === 0) {
    return <p className="text-sm text-text-muted">No scored pairs yet.</p>;
  }
  return (
    <div className="overflow-x-auto rounded-xl border border-ink-600 bg-white">
      <table className="min-w-full text-sm">
        <thead>
          <tr className="border-b border-ink-600 text-left text-[11px] uppercase tracking-widest2 text-text-secondary">
            <th className="px-3 py-2">Region</th>
            {LEADS.map((ld) => (
              <th key={ld} className="px-3 py-2 whitespace-nowrap">
                {LEAD_LABELS[ld]}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {table.regions.map((region) => (
            <tr
              key={region}
              className={`border-b border-ink-600 last:border-0 align-top ${
                region === ALL ? 'font-bold' : ''
              }`}
            >
              <th className="px-3 py-2 text-left font-normal whitespace-nowrap text-text-primary">
                {region === ALL ? <strong>{region}</strong> : region}
              </th>
              {LEADS.map((ld) => (
                <CellView key={ld} table={table} region={region} lead={ld} />
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export default async function AccuracyPage() {
  let rows: AccuracyRow[] = [];
  let failed = false;
  try {
    rows = await fetchAccuracyRows();
  } catch (err) {
    console.error('accuracy page: read failed', err);
    failed = true;
  }

  return (
    <article className="mx-auto max-w-5xl px-4 sm:px-6 py-10 sm:py-14 space-y-8">
      <header>
        <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">
          Forecast accuracy
        </div>
        <h1 className="mt-1 text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
          How accurate are our forecasts?
        </h1>
      </header>

      <section className="max-w-[700px] space-y-4 text-base text-text-primary leading-relaxed">
        <p>
          The buoy box on each spot page shows what a buoy is measuring right now. A forecast is
          different: it predicts the waves hours or days ahead. Every night we go back and check
          each forecast hour: what we predicted, and what the buoy actually measured when that
          hour arrived.
        </p>
        <p>
          Our forecasts are built on two wave models, NWPS and WAVEWATCH III. Below, each is read
          at the buoy&rsquo;s own position, so the forecast and the measurement are for the same
          place. A forecast hour is paired with the buoy reading nearest to it, within{' '}
          {PAIR_WINDOW_MINUTES} minutes. This page shows the last {WINDOW_DAYS} days of those pairs.
        </p>
        <p>
          <strong>Buoys measure waves offshore, not surf at the beach.</strong> These figures
          compare wave height (the average height of the biggest third of the waves) out at the
          buoy. They are not the surf heights on our spot pages, which estimate the size of the
          wave faces once the swell reaches the beach.
        </p>
        <p>
          In each cell, the first number is the <strong>bias</strong>: on average, how far the
          forecast was above (+) or below (&minus;) what the buoy measured, in feet.{' '}
          <strong>+0.3 ft</strong> means the forecast ran 0.3 ft high on average. The second
          number is the <strong>typical error</strong>: the average size of the miss, high or low,
          in feet. A forecast can have no bias and still a large typical error if it misses high
          as often as low.
        </p>
        <p>
          Under each pair of figures is the number of forecast hours paired with a buoy reading,
          and the days they came from. A cell with fewer than {MIN_PAIRS} pairs shows only its
          count: too few to say much.
        </p>
        <p>
          &ldquo;Under 24 h ahead&rdquo; is a forecast made less than a day before the hour it is
          for; &ldquo;1 day ahead&rdquo; was made 24 to 48 hours before, and so on.
        </p>
      </section>

      {failed ? (
        <div className="rounded-xl border border-ink-600 bg-white p-8 text-center text-text-muted text-sm">
          The scores could not be read just now. Try again later.
        </div>
      ) : (
        TABLES.map((t) => (
          <section key={t.key} className="space-y-3">
            <h2 className="text-xl sm:text-2xl font-bold tracking-tightish text-text-primary">
              {t.title}
            </h2>
            <p className="max-w-[700px] text-sm text-text-secondary">{t.blurb}</p>
            <SourceTable table={buildTable(rows, t.key)} />
          </section>
        ))
      )}
    </article>
  );
}
