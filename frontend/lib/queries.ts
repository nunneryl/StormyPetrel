import { supabase } from './supabase';
import type { Forecast, Spot, SpotWithLatest } from './types';

/**
 * Single-spot lookup by slug. Used by /spot/[slug] for both the page
 * body and generateMetadata — calling it twice per render is fine
 * because both call sites land inside the same RSC and Next dedupes.
 */
export async function fetchSpotBySlug(slug: string): Promise<Spot | null> {
  const { data, error } = await supabase
    .from('spots')
    .select('*')
    .eq('slug', slug)
    .maybeSingle();
  if (error) {
    // eslint-disable-next-line no-console
    console.error('fetchSpotBySlug', error);
    return null;
  }
  return data as Spot | null;
}

/**
 * Targeted lookup for a handful of spots by slug. Used by content
 * pages (e.g. /learn/swell-direction) that want real spot geometry —
 * orientation, optimal swell dir, swell-window arcs — for a curated
 * set of examples without dragging the entire 500-row spots table
 * into the page payload.
 *
 * Returns spots in the requested order; missing slugs are dropped.
 */
export async function fetchSpotsBySlug(slugs: string[]): Promise<Spot[]> {
  if (slugs.length === 0) return [];
  const { data, error } = await supabase
    .from('spots')
    .select('*')
    .in('slug', slugs);
  if (error) {
    // eslint-disable-next-line no-console
    console.error('fetchSpotsBySlug', error);
    return [];
  }
  const bySlug = new Map((data ?? []).map((s) => [(s as Spot).slug, s as Spot]));
  return slugs.map((s) => bySlug.get(s)).filter((s): s is Spot => Boolean(s));
}

/**
 * Fetch every spot, paginated past Supabase's default 1000-row REST cap.
 */
export async function fetchAllSpots(): Promise<Spot[]> {
  const all: Spot[] = [];
  const page = 1000;
  let from = 0;
  while (true) {
    const { data, error } = await supabase
      .from('spots')
      .select('*')
      .order('id', { ascending: true })
      .range(from, from + page - 1);
    if (error) throw error;
    if (!data || data.length === 0) break;
    all.push(...(data as Spot[]));
    if (data.length < page) break;
    from += page;
  }
  return all;
}

/**
 * For each spot, the soonest forecast row with valid_time >= now() AND
 * the next subsequent row (used to derive tide trend).
 *
 * One query (sorted ascending) — we keep up to 2 rows per spot in JS;
 * Supabase REST has no DISTINCT ON. With ~500 spots and a 6h window
 * the result set is small.
 *
 * NWPS ROWS ONLY — see loadForecasts in app/spot/[slug]/page.tsx for the full
 * reasoning. The failure here is sharper than a duplicated grid row: "first row
 * per spot wins" means an unrated source='ecmwf' row that ties the soonest nwps
 * row becomes that spot's `latest`, and every card on the index, map and region
 * pages then reads its stars/face_ft/wind off a row that carries only hs/tp/dp.
 */
export async function fetchLatestForecastPerSpot(): Promise<{
  latest: Map<number, Forecast>;
  next: Map<number, Forecast>;
}> {
  const nowIso = new Date().toISOString();
  const sixHoursLater = new Date(Date.now() + 6 * 3600_000).toISOString();
  const latest = new Map<number, Forecast>();
  const next = new Map<number, Forecast>();
  const page = 1000;
  let from = 0;
  while (true) {
    const { data, error } = await supabase
      .from('forecasts')
      .select(
        'spot_id, valid_time, source, hs, swell_hs, tp, dp, swell_tp, swell_dp, swell_1_hs, swell_1_tp, swell_1_dp, swell_2_hs, swell_2_tp, swell_2_dp, swell_3_hs, swell_3_tp, swell_3_dp, wind_wave_hs, wind_wave_tp, wind_wave_dp, swell_source, wind_speed, wind_dir, face_ft, face_lo_ft, face_hi_ft, dir_gain, wind_mult, tide_mult, chop_ratio, chop_mult, period_quality, effective_size_ft, stars, tide_level_ft',
      )
      .eq('source', 'nwps')
      .gte('valid_time', nowIso)
      .lte('valid_time', sixHoursLater)
      .order('valid_time', { ascending: true })
      .order('id', { ascending: true })
      .range(from, from + page - 1);
    if (error) throw error;
    if (!data || data.length === 0) break;
    for (const row of data as Forecast[]) {
      if (!latest.has(row.spot_id)) {
        latest.set(row.spot_id, row);
      } else if (!next.has(row.spot_id)) {
        next.set(row.spot_id, row);
      }
    }
    if (data.length < page) break;
    from += page;
  }
  return { latest, next };
}

function tideTrend(
  latest: Forecast | null,
  next: Forecast | null,
): 'rising' | 'falling' | null {
  const a = latest?.tide_level_ft;
  const b = next?.tide_level_ft;
  if (a === null || a === undefined || b === null || b === undefined) return null;
  const delta = b - a;
  if (Math.abs(delta) < 0.05) return null; // ~slack tide; no clear direction
  return delta > 0 ? 'rising' : 'falling';
}

export async function fetchSpotsWithLatest(): Promise<SpotWithLatest[]> {
  const [spots, byTime] = await Promise.all([
    fetchAllSpots(),
    fetchLatestForecastPerSpot(),
  ]);
  return spots.map((s) => {
    const latest = byTime.latest.get(s.id) ?? null;
    const next = byTime.next.get(s.id) ?? null;
    return { ...s, latest, tide_trend: tideTrend(latest, next) };
  });
}

/**
 * Next-N-hours of face_ft for sparkline rendering.
 *
 * NWPS ROWS ONLY. This one query is the least visibly broken of the three — the
 * `row.face_ft === null` skip below already drops every source='ecmwf' row, because
 * ecmwf_wam writes no face_ft. That is an ACCIDENT of the other writer's column set,
 * not a filter, and it would stop protecting this query the moment ecmwf_wam gained a
 * face_ft or a second unrated writer appeared. The `.eq('source', 'nwps')` states the
 * intent; the null skip stays because it is also a genuine guard against a rated nwps
 * hour whose face_ft could not be computed.
 *
 * The `id` secondary sort matters more here than elsewhere: `.range()` is OFFSET
 * pagination issued as separate queries, and ORDER BY valid_time alone is not a total
 * order across ~500 spots sharing one timestamp, so Postgres is free to break those
 * ties differently per page and drop or repeat a row at a page boundary. Ordering by
 * the primary key makes the sequence deterministic.
 */
export async function fetchSparklineData(): Promise<Map<number, number[]>> {
  const nowIso = new Date().toISOString();
  const cap = new Date(Date.now() + 24 * 3600_000).toISOString();
  const out = new Map<number, number[]>();
  const page = 1000;
  let from = 0;
  while (true) {
    const { data, error } = await supabase
      .from('forecasts')
      .select('spot_id, valid_time, face_ft')
      .eq('source', 'nwps')
      .gte('valid_time', nowIso)
      .lte('valid_time', cap)
      .order('valid_time', { ascending: true })
      .order('id', { ascending: true })
      .range(from, from + page - 1);
    if (error) throw error;
    if (!data || data.length === 0) break;
    for (const row of data as { spot_id: number; face_ft: number | null }[]) {
      if (row.face_ft === null) continue;
      const arr = out.get(row.spot_id) ?? [];
      arr.push(row.face_ft);
      out.set(row.spot_id, arr);
    }
    if (data.length < page) break;
    from += page;
  }
  return out;
}
