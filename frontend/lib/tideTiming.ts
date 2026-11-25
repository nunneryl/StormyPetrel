/**
 * The tide at a spot whose station is trusted for TIMING but not for HEIGHT.
 *
 * Four Mendocino spots (Caspar, Jug Handle, Mackerricher, Ten Mile Beach) take their tide from
 * NOAA CO-OPS 9416841, Arena Cove, 50 to 70 km south down the same open coast. The pipeline
 * keeps that station's phase (pipeline/interpret.py lookup_tide_norm: each hour's tide_norm,
 * 0 at the lowest predicted level to 1 at the highest) and withholds its height (tide_level_ft
 * null), because 50 to 70 km away the range is not this spot's (spots.tide_height_suppressed,
 * migration 034; pipeline/data/spot_tide_stations.json "suppress_height"). So the page shows
 * when the tide is high and low, and whether it is rising or falling, with no feet.
 *
 * Nothing here is rated: tide_mult is 1.0 at these spots, which have no verified tide preference.
 */
import type { Forecast, Spot } from './types.ts';

/** Display names of the stations a spot uses for timing only, by CO-OPS id. Every station a
 *  roster spot uses this way must be here (lib/tideTiming.test.mts reads the roster); the name
 *  is the one in pipeline/data/spot_tide_stations.json without its state. */
export const TIMING_ONLY_STATIONS: Readonly<Record<string, string>> = { '9416841': 'Arena Cove' };

export function tideTimingOnly(spot: Pick<Spot, 'tide_height_suppressed'> | null | undefined): boolean {
  return spot?.tide_height_suppressed === true;
}

/** The sentence under the chart: which station, how far, and why there are no feet. */
export function timingOnlyCaption(stationId: string | null | undefined, distKm: number | null | undefined): string {
  const name = stationId ? TIMING_ONLY_STATIONS[stationId] : undefined;
  const where = name ? `NOAA's ${name} station` : 'the nearest NOAA tide station';
  const far = typeof distKm === 'number' && Number.isFinite(distKm) ? `, ${Math.round(distKm)} km away` : '';
  return `Tide times from ${where}${far}. Close enough for when high and low tide come; `
    + `too far to give this spot's tide height, so heights aren't shown.`;
}

export type TimingPoint = { t: number; iso: string; norm: number };

/** The hours that carry a tide position, in time order, one per instant. */
export function timingSeries(rows: readonly Pick<Forecast, 'valid_time' | 'tide_norm'>[]): TimingPoint[] {
  const out: TimingPoint[] = [];
  const seen = new Set<number>();
  for (const r of rows) {
    const n = r.tide_norm;
    if (n === null || n === undefined || !Number.isFinite(n)) continue;
    const t = Date.parse(r.valid_time);
    if (!Number.isFinite(t) || seen.has(t)) continue;
    seen.add(t);
    out.push({ t, iso: new Date(t).toISOString(), norm: n });
  }
  return out.sort((a, b) => a.t - b.t);
}

export type TideTurn = { kind: 'high' | 'low'; t: number; norm: number };

const MAX_STEP_MS = 90 * 60 * 1000;     // neighbours more than 1.5 h apart are not one curve
const ROUND_MS = 10 * 60 * 1000;        // times shown to the nearest 10 minutes

/**
 * The highs and lows inside the series: each sample at least as high (low) as the one before and
 * higher (lower) than the one after, its time refined by the parabola through the three samples
 * (the vertex lies within half a step of the middle one) and rounded to 10 minutes. A turn at
 * either end of the series, or across a gap in it, is not found: it is not known to be one.
 */
export function tideTurns(pts: readonly TimingPoint[]): TideTurn[] {
  const out: TideTurn[] = [];
  for (let i = 1; i < pts.length - 1; i += 1) {
    const [a, b, c] = [pts[i - 1], pts[i], pts[i + 1]];
    if (b.t - a.t > MAX_STEP_MS || c.t - b.t > MAX_STEP_MS) continue;
    const high = b.norm >= a.norm && b.norm > c.norm;
    const low = b.norm <= a.norm && b.norm < c.norm;
    if (!high && !low) continue;
    const step = (c.t - a.t) / 2;
    const curve = a.norm - 2 * b.norm + c.norm;
    const off = curve === 0 ? 0 : Math.max(-0.5, Math.min(0.5, (a.norm - c.norm) / (2 * curve)));
    const t = Math.round((b.t + off * step) / ROUND_MS) * ROUND_MS;
    const norm = b.norm - ((a.norm - c.norm) * off) / 4;
    out.push({ kind: high ? 'high' : 'low', t, norm: Math.max(0, Math.min(1, norm)) });
  }
  return out;
}

/** Rising, falling or slack from this hour to the next, read off the tide position. */
export function timingTrend(a: number | null | undefined, b: number | null | undefined):
  'rising' | 'falling' | 'slack' | null {
  if (a === null || a === undefined || b === null || b === undefined) return null;
  if (b > a + 0.01) return 'rising';
  if (b < a - 0.01) return 'falling';
  return 'slack';
}
