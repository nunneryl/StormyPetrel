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
 * For each spot, the soonest forecast row with valid_time >= now().
 *
 * We do this with one query (sorted ascending) and pick the first row per
 * spot in JS — Supabase REST has no DISTINCT ON. With ~500 spots and a 6h
 * window the result set is small and easy to dedupe client-side.
 */
export async function fetchLatestForecastPerSpot(): Promise<Map<number, Forecast>> {
  const nowIso = new Date().toISOString();
  const sixHoursLater = new Date(Date.now() + 6 * 3600_000).toISOString();
  const result = new Map<number, Forecast>();
  const page = 1000;
  let from = 0;
  while (true) {
    const { data, error } = await supabase
      .from('forecasts')
      .select(
        'spot_id, valid_time, hs, swell_hs, tp, dp, swell_tp, swell_dp, wind_speed, wind_dir, face_ft, dir_gain, wind_mult, tide_mult, chop_ratio, chop_mult, period_quality, effective_size_ft, stars, tide_level_ft',
      )
      .gte('valid_time', nowIso)
      .lte('valid_time', sixHoursLater)
      .order('valid_time', { ascending: true })
      .range(from, from + page - 1);
    if (error) throw error;
    if (!data || data.length === 0) break;
    for (const row of data as Forecast[]) {
      if (!result.has(row.spot_id)) result.set(row.spot_id, row);
    }
    if (data.length < page) break;
    from += page;
  }
  return result;
}

export async function fetchSpotsWithLatest(): Promise<SpotWithLatest[]> {
  const [spots, latest] = await Promise.all([
    fetchAllSpots(),
    fetchLatestForecastPerSpot(),
  ]);
  return spots.map((s) => ({ ...s, latest: latest.get(s.id) ?? null }));
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
