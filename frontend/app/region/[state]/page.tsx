import { notFound } from 'next/navigation';
import Link from 'next/link';
import { fetchSpotsWithLatest, fetchSparklineData } from '@/lib/queries';
import { RatingBadge } from '@/components/RatingBadge';
import { CompassArrow } from '@/components/CompassArrow';
import { Sparkline } from '@/components/Sparkline';
import { fmtFt, fmtMph, fmtSec, pickSwell } from '@/lib/formatting';

export const dynamic = 'force-dynamic';
export const revalidate = 600;

type Params = { state: string };

export default async function RegionPage({ params }: { params: Promise<Params> }) {
  const { state } = await params;
  const decoded = decodeURIComponent(state).toLowerCase();
  const [spots, sparks] = await Promise.all([
    fetchSpotsWithLatest(),
    fetchSparklineData(),
  ]);
  const inState = spots.filter((s) => (s.state ?? '').toLowerCase() === decoded);
  if (inState.length === 0) notFound();

  inState.sort((a, b) => (b.latest?.stars ?? -1) - (a.latest?.stars ?? -1));
  const stateLabel = inState[0].state ?? decoded;

  return (
    <div className="mx-auto max-w-7xl px-4 py-8 space-y-6">
      <div>
        <div className="text-xs uppercase tracking-widest text-slate-400">Region</div>
        <h1 className="text-3xl font-bold text-white">{stateLabel}</h1>
        <p className="mt-1 text-slate-400 text-sm">
          {inState.length} spots · sorted by current rating.
        </p>
      </div>

      <div className="space-y-2">
        {inState.map((s) => {
          const f = s.latest;
          const series = sparks.get(s.id) ?? [];
          return (
            <Link
              key={s.id}
              href={`/spot/${s.slug}`}
              className="grid grid-cols-[1fr_auto] gap-3 items-center rounded border border-ink-700 bg-ink-900 hover:border-sea-500 hover:bg-ink-800 transition p-3"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2 flex-wrap">
                  <span className="font-bold text-slate-100">{s.name}</span>
                  <RatingBadge stars={f?.stars ?? 0} size="sm" />
                  {s.break_type && (
                    <span className="text-xs text-slate-500">{s.break_type}</span>
                  )}
                </div>
                <div className="mt-1 flex items-center gap-3 text-xs text-slate-300 flex-wrap">
                  <span className="font-bold text-slate-100">{fmtFt(f?.face_ft ?? null)}</span>
                  <span className="text-slate-400">{fmtSec(pickSwell(f?.swell_tp ?? null, f?.tp ?? null))}</span>
                  <CompassArrow deg={pickSwell(f?.swell_dp ?? null, f?.dp ?? null)} size={12} color="#3da9d7" />
                  <span className="flex items-center gap-1 text-slate-400">
                    <CompassArrow deg={f?.wind_dir ?? null} size={12} color="#9bbf3e" showLabel={false} />
                    {fmtMph(f?.wind_speed ?? null)}
                  </span>
                </div>
              </div>
              {series.length > 1 && <Sparkline values={series} />}
            </Link>
          );
        })}
      </div>
    </div>
  );
}
