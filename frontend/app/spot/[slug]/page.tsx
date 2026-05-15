import type { Metadata } from 'next';
import { notFound } from 'next/navigation';
import Link from 'next/link';
import { supabase } from '@/lib/supabase';
import type { Forecast, Spot, BuoyObservation } from '@/lib/types';
import { StarRating } from '@/components/StarRating';
import { ForecastGrid } from '@/components/ForecastGrid';
import { SwellChart } from '@/components/SwellChart';
import { WindChart } from '@/components/WindChart';
import { TideChart } from '@/components/TideChart';
import { SwellPartitions } from '@/components/SwellPartitions';
import { CurrentConditions } from '@/components/CurrentConditions';
import { OptimalConditions } from '@/components/OptimalConditions';
import { CamSection } from '@/components/CamEmbed';
import { SectionHeader } from '@/components/SectionHeader';
import { degToCardinal, fmtSec } from '@/lib/formatting';
import { fetchCamsForSpot } from '@/lib/cams';
import { siteUrl } from '@/lib/site-url';

export const dynamic = 'force-dynamic';
export const revalidate = 600;

type Params = { slug: string };

export async function generateMetadata({ params }: { params: Promise<Params> }): Promise<Metadata> {
  const { slug } = await params;
  const spot = await loadSpot(slug);
  if (!spot) return { title: 'Spot not found' };
  const title = `${spot.name} Surf Forecast — Wave Height, Swell & Wind | Stormy Petrel`;
  const description =
    `Free 7-day surf forecast for ${spot.name}` +
    (spot.state ? `, ${spot.state}` : '') +
    `. Wave height, swell direction, period, wind, and tide — updated every 6 hours.`;
  return {
    title: { absolute: title },
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

  const [forecasts, buoy, cams] = await Promise.all([
    loadForecasts(spot.id),
    loadLatestBuoy(spot.nearest_buoy_id),
    fetchCamsForSpot(spot.slug),
  ]);

  const current = forecasts[0] ?? null;
  // Charts get a 48h slice — the full 7-day window made the curves
  // too compressed to read. The grid below still shows everything.
  const cutoff = Date.now() + 48 * 3600_000;
  const chartForecasts = forecasts.filter(
    (r) => new Date(r.valid_time).getTime() <= cutoff,
  );

  // Structured data for rich search results. Kept inline so the JSON-LD
  // ships in the initial HTML payload that crawlers parse — Next.js'
  // <Script> wrapper would defer it past Googlebot's render budget.
  const base = siteUrl();
  const jsonLd = {
    '@context': 'https://schema.org',
    '@type': 'WebPage',
    name: `${spot.name} Surf Forecast`,
    description: `Free 7-day surf forecast for ${spot.name}${
      spot.state ? `, ${spot.state}` : ''
    }. Wave height, swell direction, period, wind, and tide — updated every 6 hours.`,
    url: `${base}/spot/${spot.slug}`,
    isPartOf: {
      '@type': 'WebSite',
      name: 'Stormy Petrel',
      url: base,
    },
  };

  return (
    <div className="mx-auto max-w-7xl px-4 sm:px-6 py-5 sm:py-7 space-y-6">
      <script
        type="application/ld+json"
        // eslint-disable-next-line react/no-danger
        dangerouslySetInnerHTML={{ __html: JSON.stringify(jsonLd) }}
      />
      {/* Header */}
      <header className="flex items-start justify-between gap-4 flex-wrap">
        <div className="min-w-0">
          <div className="text-[11px] uppercase tracking-widest2 text-text-secondary">
            {(() => {
              const s = (spot.state ?? '').trim();
              const r = (spot.region ?? '').trim();
              if (!s && !r) return '';
              if (!r || r.toLowerCase() === s.toLowerCase()) return s;
              return `${s} · ${r}`;
            })()}
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
          </div>
        </div>
        <div className="flex flex-col items-end gap-1 shrink-0">
          <StarRating score={current?.stars ?? 0} size="xl" showScore />
          <span className="text-[10px] uppercase tracking-widest2 text-text-muted">
            {freshnessLabel(current)}
          </span>
        </div>
      </header>

      {/* Factual blurb from pipeline.generate_descriptions. Renders
          nothing (no placeholder) when the column is still null so
          unprocessed spots don't show a gap. */}
      {spot.description && (
        <p className="text-text-secondary text-base leading-relaxed">
          {spot.description}
        </p>
      )}

      {/* Live cams — every active cam for this spot. Embed-mode rows
          render an iframe each; link-mode rows render a banner card
          with a Watch-live button. CamSection no-ops when cams is
          empty so the section disappears cleanly. */}
      <CamSection cams={cams} lat={spot.lat} lng={spot.lng} />

      {/* Hero tiles */}
      <CurrentConditions
        current={current}
        forecasts={forecasts}
        offshoreDeg={spot.offshore_wind_deg}
      />

      {/* Spectral swell components */}
      {current && <SwellPartitions forecast={current} />}

      {/* Charts — next 48h only so curves stay legible */}
      <section className="grid grid-cols-1 lg:grid-cols-3 gap-4">
        <ChartCard title="Swell components (ft) · next 48h">
          <SwellChart forecasts={chartForecasts} />
        </ChartCard>
        <ChartCard title="Wind speed (mph) · next 48h">
          <WindChart forecasts={chartForecasts} offshoreDeg={spot.offshore_wind_deg} />
        </ChartCard>
        <ChartCard title="Tide (ft) · next 48h">
          <TideChart forecasts={chartForecasts} />
        </ChartCard>
      </section>

      {/* Spot info / conditions match / buoy */}
      <section className="grid grid-cols-1 md:grid-cols-3 gap-4">
        <InfoBlock title="Spot info">
          <Row k="Break" v={spot.break_type ?? '—'} />
          <Row k="Tide preference" v={spot.tide_preference ?? '—'} />
          <Row k="Crowd" v={spot.crowd_factor ?? '—'} />
          <Row
            k="Hazards"
            v={
              spot.hazards?.length
                ? spot.hazards.map((h) => h.replace(/_/g, ' ')).join(', ')
                : '—'
            }
          />
        </InfoBlock>

        <OptimalConditions spot={spot} />

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
            <div className="text-text-muted text-sm">No nearby buoy.</div>
          )}
        </InfoBlock>
      </section>

      {/* 7-day grid — moved to the bottom as the detailed reference
          view; the charts above carry the at-a-glance trend. Defaults
          to the next 48 hours with an in-grid expand button. */}
      <section>
        <SectionHeader title="7-day forecast" />
        <ForecastGrid forecasts={forecasts} offshoreDeg={spot.offshore_wind_deg} />
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

