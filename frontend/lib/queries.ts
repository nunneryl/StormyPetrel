import { supabase } from './supabase';
import type { Forecast, Spot, SpotWithLatest } from './types';

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
        'spot_id, valid_time, hs, swell_hs, tp, dp, swell_tp, swell_dp, swell_1_hs, swell_1_tp, swell_1_dp, swell_2_hs, swell_2_tp, swell_2_dp, swell_3_hs, swell_3_tp, swell_3_dp, wind_wave_hs, wind_wave_tp, wind_wave_dp, swell_source, wind_speed, wind_dir, face_ft, dir_gain, wind_mult, tide_mult, chop_ratio, chop_mult, period_quality, effective_size_ft, stars, tide_level_ft',
      )
      .gte('valid_time', nowIso)
      .lte('valid_time', sixHoursLater)
      .order('valid_time', { ascending: true })
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
      .gte('valid_time', nowIso)
      .lte('valid_time', cap)
      .order('valid_time', { ascending: true })
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
