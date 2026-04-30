import type { Metadata } from 'next';
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
import { CurrentConditions } from '@/components/CurrentConditions';
import { SectionHeader } from '@/components/SectionHeader';
import { degToCardinal, fmtSec } from '@/lib/formatting';

export const dynamic = 'force-dynamic';
export const revalidate = 600;

type Params = { slug: string };

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { slug } = await params;
  const spot = await loadSpot(slug);
  if (!spot) return { title: 'Spot not found' };
  const region = [spot.state, spot.region].filter(Boolean).join(' · ');
  const title = `${spot.name} surf forecast`;
  const description =
    `Free 7-day surf forecast for ${spot.name}` +
    (region ? `, ${region}` : '') +
    `. Wave height, swell direction, wind, and tide — built on NOAA NWPS, gfswave (WAVEWATCH III), HRRR and NDBC data.`;
  return {
    title,
    description,
    alternates: { canonical: `/spot/${spot.slug}` },
    openGraph: { title, description, type: 'website' },
    twitter: { card: 'summary_large_image', title, description },
  };
}

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

function freshnessLabel(latest: Forecast | null): string {
  if (!latest) return '—';
  // The most recent forecast row is always current-or-future hour. The
  // best signal of "data freshness" is the hour-of-day distance from
  // now to the most recent record's valid_time. Cap at 24h for display.
  const ms = Date.now() - new Date(latest.valid_time).getTime();
  const min = Math.max(0, Math.round(ms / 60000));
  if (min < 60) return `Updated ${min} min ago`;
  const h = Math.round(min / 60);
  if (h < 24) return `Updated ${h}h ago`;
  return `Updated ${Math.round(h / 24)}d ago`;
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

  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6 py-5 sm:py-7 space-y-6">
      {/* Header */}
      <header className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">
            {spot.state ?? ''}{spot.region ? ` · ${spot.region}` : ''}
          </div>
          <h1 className="text-3xl sm:text-4xl font-bold tracking-tightish text-text-primary">
            {spot.name}
          </h1>
          <div className="mt-1 text-sm text-text-secondary flex items-center gap-2 flex-wrap">
            {spot.break_type && (
              <span className="inline-flex items-center px-2 py-0.5 rounded-full bg-ink-800 border border-ink-600 text-text-primary text-xs">
                {spot.break_type}
              </span>
            )}
            {spot.tide_preference && <span>tide: {spot.tide_preference}</span>}
            {spot.crowd_factor && <span>· crowd: {spot.crowd_factor}</span>}
          </div>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <RatingBadge stars={current?.stars ?? 0} size="xl" glow />
          <span className="text-[10px] uppercase tracking-widest2 text-text-muted">
            {freshnessLabel(current)}
          </span>
        </div>
      </header>

      {/* Hero tiles */}
      <CurrentConditions
        current={current}
        forecasts={forecasts}
        offshoreDeg={spot.offshore_wind_deg}
      />

      {/* Spectral swell components */}
      {current && <SwellPartitions forecast={current} />}

      {/* 7-day grid */}
      <section>
        <SectionHeader
          title="7-day forecast"
          right={
            <span className="text-[10px] uppercase tracking-widest2 text-text-muted hidden sm:inline">
              3-hour blocks · scroll right for more →
            </span>
          }
        />
        <ForecastGrid forecasts={forecasts} offshoreDeg={spot.offshore_wind_deg} />
      </section>

      {/* Charts */}
      <section className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ChartCard title="Swell components (ft)">
          <SwellChart forecasts={forecasts} />
        </ChartCard>
        <ChartCard title="Wind speed (mph)">
          <WindChart forecasts={forecasts} offshoreDeg={spot.offshore_wind_deg} />
        </ChartCard>
        <ChartCard title="Tide (ft)">
          <TideChart forecasts={forecasts} hilo={hilo} />
        </ChartCard>
      </section>

      {/* Spot info / orientation / buoy */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <InfoBlock title="Spot info">
          <Row k="Break" v={spot.break_type ?? '—'} />
          <Row k="Tide preference" v={spot.tide_preference ?? '—'} />
          <Row k="Crowd" v={spot.crowd_factor ?? '—'} />
          <Row k="Hazards" v={spot.hazards?.length ? spot.hazards.join(', ') : '—'} />
        </InfoBlock>

        <InfoBlock title="Orientation">
          {spot.orientation_deg !== null && (
            <CompassDiagram
              orientation={spot.orientation_deg}
              optimal={spot.optimal_swell_dir}
              offshore={spot.offshore_wind_deg}
            />
          )}
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
              <Row
                k="Water"
                v={
                  buoy.water_temp !== null
                    ? `${(buoy.water_temp * 9 / 5 + 32).toFixed(0)}°F`
                    : '—'
                }
              />
              <Row k="Reading" v={new Date(buoy.observed_at).toLocaleString()} />
              <a
                href={`https://www.ndbc.noaa.gov/station_page.php?station=${buoy.buoy_id}`}
                target="_blank"
                rel="noreferrer"
                className="inline-block mt-2 text-xs text-cyan-400 hover:underline"
              >
                View NDBC station page →
              </a>
            </>
          ) : (
            <div className="text-text-muted text-sm">No buoy linked.</div>
          )}
        </InfoBlock>
      </section>

      {/* Footer breadcrumbs */}
      <div className="flex items-center justify-between flex-wrap gap-2 pt-2 text-xs text-text-muted">
        <div className="flex items-center gap-3">
          <Link href="/map" className="hover:text-cyan-400">
            ← Back to map
          </Link>
          {spot.state && (
            <Link
              href={`/region/${encodeURIComponent(spot.state.toLowerCase())}`}
              className="hover:text-cyan-400"
            >
              More spots in {spot.state}
            </Link>
          )}
        </div>
        <a
          href={`https://github.com/nunneryl/StormyPetrel/issues/new?title=Spot+data+issue:+${encodeURIComponent(spot.name)}&body=Slug:+${encodeURIComponent(spot.slug)}%0A%0AWhat%27s+wrong:`}
          target="_blank"
          rel="noreferrer"
          className="hover:text-cyan-400"
        >
          Report incorrect data
        </a>
      </div>
    </div>
  );
}

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-ink-600 bg-ink-800/60 p-3.5">
      <div className="text-[10px] uppercase tracking-widest2 text-text-secondary mb-2">
        {title}
      </div>
      {children}
    </div>
  );
}

function InfoBlock({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-ink-600 bg-ink-800/60 p-4">
      <div className="text-[10px] uppercase tracking-widest2 text-text-secondary mb-2">
        {title}
      </div>
      <div className="space-y-1.5">{children}</div>
    </div>
  );
}

function Row({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between text-sm gap-3">
      <span className="text-text-secondary shrink-0">{k}</span>
      <span className="text-text-primary text-right truncate">{v}</span>
    </div>
  );
}

/** Tiny SVG compass — orientation arrow points off the beach toward open
 *  water, optimal-swell arrow points where the best swell comes from,
 *  offshore-wind arrow shows the offshore bearing (a glanceable summary
 *  of the spot's geometry). */
function CompassDiagram({
  orientation,
  optimal,
  offshore,
}: {
  orientation: number;
  optimal: number | null;
  offshore: number | null;
}) {
  const size = 120;
  const cx = size / 2;
  const cy = size / 2;
  const r = size / 2 - 14;
  function endpoint(deg: number, len = r) {
    const rad = ((deg - 90) * Math.PI) / 180;
    return { x: cx + Math.cos(rad) * len, y: cy + Math.sin(rad) * len };
  }
  const orientEnd = endpoint(orientation);
  return (
    <div className="flex justify-center mb-3">
      <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`}>
        <circle cx={cx} cy={cy} r={r} stroke="#1E3048" fill="#0B1426" strokeWidth="1" />
        <text x={cx} y={cy - r - 2} textAnchor="middle" fill="#94A3B8" fontSize="9">N</text>
        <text x={cx + r + 6} y={cy + 3} textAnchor="middle" fill="#94A3B8" fontSize="9">E</text>
        <text x={cx} y={cy + r + 9} textAnchor="middle" fill="#94A3B8" fontSize="9">S</text>
        <text x={cx - r - 6} y={cy + 3} textAnchor="middle" fill="#94A3B8" fontSize="9">W</text>
        {/* Beach orientation (where the spot faces) */}
        <line x1={cx} y1={cy} x2={orientEnd.x} y2={orientEnd.y} stroke="#F1F5F9" strokeWidth="2" />
        <circle cx={orientEnd.x} cy={orientEnd.y} r={3} fill="#F1F5F9" />
        {/* Optimal swell arrow (incoming, so 180-deg flipped) */}
        {optimal !== null && (() => {
          const e = endpoint((optimal + 180) % 360, r - 6);
          return <line x1={cx} y1={cy} x2={e.x} y2={e.y} stroke="#38BDF8" strokeWidth="2" strokeDasharray="3 2" />;
        })()}
        {/* Offshore wind direction (outgoing) */}
        {offshore !== null && (() => {
          const e = endpoint((offshore + 180) % 360, r - 12);
          return <line x1={cx} y1={cy} x2={e.x} y2={e.y} stroke="#A3E635" strokeWidth="1.5" strokeDasharray="2 2" />;
        })()}
      </svg>
    </div>
  );
}
