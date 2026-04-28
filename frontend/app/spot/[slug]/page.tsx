import { notFound } from 'next/navigation';
import Link from 'next/link';
import { supabase } from '@/lib/supabase';
import type { Forecast, Spot, BuoyObservation, TidePrediction } from '@/lib/types';
import { RatingBadge } from '@/components/RatingBadge';
import { ForecastGrid } from '@/components/ForecastGrid';
import { SwellChart } from '@/components/SwellChart';
import { WindChart } from '@/components/WindChart';
import { TideChart } from '@/components/TideChart';
import { CompassArrow } from '@/components/CompassArrow';
import { SwellPartitions } from '@/components/SwellPartitions';
import {
  degToCardinal,
  fmtFt,
  fmtMph,
  fmtSec,
  pickSwell,
} from '@/lib/formatting';

export const dynamic = 'force-dynamic';
export const revalidate = 600; // 10 minutes — forecasts update hourly upstream.

type Params = { slug: string };

async function loadSpot(slug: string): Promise<Spot | null> {
  const { data, error } = await supabase
    .from('spots')
    .select('*')
    .eq('slug', slug)
    .maybeSingle();
  if (error) {
    console.error('loadSpot', error);
    return null;
  }
  return data as Spot | null;
}

async function loadForecasts(spotId: number): Promise<Forecast[]> {
  const nowIso = new Date().toISOString();
  const { data, error } = await supabase
    .from('forecasts')
    .select(
      'spot_id, valid_time, hs, swell_hs, tp, dp, swell_tp, swell_dp, swell_1_hs, swell_1_tp, swell_1_dp, swell_2_hs, swell_2_tp, swell_2_dp, swell_3_hs, swell_3_tp, swell_3_dp, wind_wave_hs, wind_wave_tp, wind_wave_dp, swell_source, wind_speed, wind_dir, face_ft, dir_gain, wind_mult, tide_mult, chop_ratio, chop_mult, period_quality, effective_size_ft, stars, tide_level_ft',
    )
    .eq('spot_id', spotId)
    .gte('valid_time', nowIso)
    .order('valid_time', { ascending: true })
    .limit(200);
  if (error) {
    console.error('loadForecasts', error);
    return [];
  }
  return (data ?? []) as Forecast[];
}

async function loadLatestBuoy(buoyId: string | null): Promise<BuoyObservation | null> {
  if (!buoyId) return null;
  const { data, error } = await supabase
    .from('buoy_observations')
    .select('*')
    .eq('buoy_id', buoyId)
    .order('observed_at', { ascending: false })
    .limit(1)
    .maybeSingle();
  if (error) {
    console.error('loadLatestBuoy', error);
    return null;
  }
  return data as BuoyObservation | null;
}

async function loadHilo(stationId: string | null): Promise<TidePrediction[]> {
  if (!stationId) return [];
  const nowIso = new Date().toISOString();
  const { data, error } = await supabase
    .from('tide_predictions')
    .select('*')
    .eq('station_id', stationId)
    .in('type', ['H', 'L'])
    .gte('predicted_at', nowIso)
    .order('predicted_at', { ascending: true })
    .limit(28);
  if (error) {
    console.error('loadHilo', error);
    return [];
  }
  return (data ?? []) as TidePrediction[];
}

export default async function SpotPage({ params }: { params: Promise<Params> }) {
  const { slug } = await params;
  const spot = await loadSpot(slug);
  if (!spot) notFound();

  const [forecasts, buoy, hilo] = await Promise.all([
    loadForecasts(spot.id),
    loadLatestBuoy(spot.nearest_buoy_id),
    loadHilo(spot.nearest_tide_station_id),
  ]);

  const current = forecasts[0] ?? null;
  // Detect rising vs falling by comparing the next two hours (cheap +
  // ignores any local hilo timing edge cases).
  const tideNext = forecasts[1]?.tide_level_ft;
  const tideCur = current?.tide_level_ft;
  const tideTrend =
    tideCur !== null && tideCur !== undefined && tideNext !== null && tideNext !== undefined
      ? tideNext > tideCur
        ? 'rising'
        : tideNext < tideCur
          ? 'falling'
          : 'slack'
      : null;

  return (
    <div className="mx-auto max-w-7xl px-4 py-6 space-y-6">
      <div className="flex items-start justify-between gap-4 flex-wrap">
        <div>
          <div className="text-xs uppercase tracking-widest text-slate-400">
            {spot.state ?? ''}{spot.region ? ` · ${spot.region}` : ''}
          </div>
          <h1 className="text-3xl sm:text-4xl font-bold tracking-tight text-white">
            {spot.name}
          </h1>
          <div className="mt-1 text-sm text-slate-400 flex items-center gap-2 flex-wrap">
            {spot.break_type && <span>{spot.break_type}</span>}
            {spot.tide_preference && (
              <>
                <span>·</span>
                <span>tide: {spot.tide_preference}</span>
              </>
            )}
            {spot.crowd_factor && (
              <>
                <span>·</span>
                <span>crowd: {spot.crowd_factor}</span>
              </>
            )}
          </div>
        </div>
        <RatingBadge stars={current?.stars ?? 0} size="lg" />
      </div>

      <section className="grid grid-cols-2 sm:grid-cols-4 gap-3">
        {(() => {
          const tp = pickSwell(current?.swell_tp ?? null, current?.tp ?? null);
          const dp = pickSwell(current?.swell_dp ?? null, current?.dp ?? null);
          return (
            <>
              <Stat label="Face" value={fmtFt(current?.face_ft)} hint={tp ? `${fmtSec(tp)} period` : null} />
              <Stat
                label="Swell"
                value={degToCardinal(dp)}
                hint={dp !== null ? `${dp.toFixed(0)}°` : null}
                icon={<CompassArrow deg={dp} size={18} color="#3da9d7" showLabel={false} />}
              />
            </>
          );
        })()}
        <Stat
          label="Wind"
          value={fmtMph(current?.wind_speed)}
          hint={current?.wind_dir !== null && current?.wind_dir !== undefined
            ? `${degToCardinal(current.wind_dir)}${chopBadge(current.chop_ratio)}`
            : null}
          icon={<CompassArrow deg={current?.wind_dir ?? null} size={18} color="#9bbf3e" showLabel={false} />}
        />
        <Stat
          label="Tide"
          value={
            current?.tide_level_ft !== null && current?.tide_level_ft !== undefined
              ? `${current.tide_level_ft.toFixed(1)} ft`
              : '—'
          }
          hint={tideTrend ? tideTrend : null}
        />
      </section>

      {current && (
        <section>
          <SwellPartitions forecast={current} />
        </section>
      )}

      <section>
        <h2 className="text-sm uppercase tracking-widest text-slate-400 mb-2">
          7-day forecast
        </h2>
        <ForecastGrid forecasts={forecasts} />
      </section>

      <section className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ChartCard title="Swell — face height (ft)">
          <SwellChart forecasts={forecasts} />
        </ChartCard>
        <ChartCard title="Wind — speed (mph)">
          <WindChart forecasts={forecasts} />
        </ChartCard>
        <ChartCard title="Tide (ft)">
          <TideChart forecasts={forecasts} hilo={hilo} />
        </ChartCard>
      </section>

      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <InfoBlock title="Spot info">
          <Row k="Break" v={spot.break_type ?? '—'} />
          <Row k="Tide preference" v={spot.tide_preference ?? '—'} />
          <Row k="Crowd" v={spot.crowd_factor ?? '—'} />
          <Row k="Hazards" v={spot.hazards?.length ? spot.hazards.join(', ') : '—'} />
        </InfoBlock>

        <InfoBlock title="Orientation">
          <Row
            k="Beach faces"
            v={
              spot.orientation_deg !== null
                ? `${degToCardinal(spot.orientation_deg)} (${spot.orientation_deg.toFixed(0)}°)`
                : '—'
            }
          />
          <Row
            k="Optimal swell"
            v={
              spot.optimal_swell_dir !== null
                ? `${degToCardinal(spot.optimal_swell_dir)} (${spot.optimal_swell_dir.toFixed(0)}°)`
                : '—'
            }
          />
          <Row
            k="Offshore wind"
            v={
              spot.offshore_wind_deg !== null
                ? `${degToCardinal(spot.offshore_wind_deg)} (${spot.offshore_wind_deg.toFixed(0)}°)`
                : '—'
            }
          />
        </InfoBlock>

        <InfoBlock title="Nearest buoy">
          {buoy ? (
            <>
              <Row k="Buoy" v={`NDBC ${buoy.buoy_id}`} />
              <Row k="Hs" v={buoy.hs !== null ? `${(buoy.hs * 3.28084).toFixed(1)} ft` : '—'} />
              <Row k="Tp" v={fmtSec(buoy.tp)} />
              <Row k="Dir" v={degToCardinal(buoy.dp)} />
              <Row k="Water" v={buoy.water_temp !== null ? `${(buoy.water_temp * 9/5 + 32).toFixed(0)}°F` : '—'} />
              <Row k="Reading" v={new Date(buoy.observed_at).toLocaleString()} />
            </>
          ) : (
            <div className="text-slate-500 text-sm">No buoy linked.</div>
          )}
        </InfoBlock>
      </section>

      <div className="text-xs text-slate-500">
        <Link href="/map" className="hover:text-slate-300 underline">
          Back to map
        </Link>
        {spot.state && (
          <>
            {' · '}
            <Link
              href={`/region/${encodeURIComponent(spot.state.toLowerCase())}`}
              className="hover:text-slate-300 underline"
            >
              More spots in {spot.state}
            </Link>
          </>
        )}
      </div>
    </div>
  );
}

function chopBadge(chopRatio: number | null | undefined): string {
  if (chopRatio === null || chopRatio === undefined) return '';
  if (chopRatio < 0.2) return ' · clean';
  if (chopRatio < 0.4) return ' · mixed';
  return ' · choppy';
}

function Stat({
  label,
  value,
  hint,
  icon,
}: {
  label: string;
  value: string;
  hint: string | null;
  icon?: React.ReactNode;
}) {
  return (
    <div className="rounded border border-ink-700 bg-ink-900 p-3">
      <div className="text-[10px] uppercase tracking-widest text-slate-500">{label}</div>
      <div className="mt-1 flex items-center gap-2">
        {icon}
        <span className="text-2xl font-bold text-white tabular-nums">{value}</span>
      </div>
      {hint && <div className="mt-1 text-xs text-slate-400">{hint}</div>}
    </div>
  );
}

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-ink-700 bg-ink-900 p-3">
      <div className="text-[10px] uppercase tracking-widest text-slate-400 mb-2">{title}</div>
      {children}
    </div>
  );
}

function InfoBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded border border-ink-700 bg-ink-900 p-4">
      <div className="text-[10px] uppercase tracking-widest text-slate-400 mb-2">{title}</div>
      <div className="space-y-1">{children}</div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between text-sm">
      <span className="text-slate-400">{k}</span>
      <span className="text-slate-100 text-right">{v}</span>
    </div>
  );
}
