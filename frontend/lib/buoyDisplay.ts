/**
 * What the spot page's "Nearest buoy" box says. Tested in lib/buoyDisplay.test.mts.
 *
 * THE RULE (2026-11-06). A buoy is never shown past BUOY_DISPLAY_LIMIT_KM, its distance is always
 * shown beside its id ("NDBC 44025 · 87 km away"), and a wave reading counts as recent when it
 * has a wave height and is at most BUOY_STALE_HOURS old. The box shows the first of:
 *   1. the main buoy's latest reading, if it is recent: labelled "Reading";
 *   2. else the main buoy's last recent reading WITH a wave height: labelled "Last wave reading";
 *   3. else the first of the spot's fallback buoys (the roster's order, nearest first) within
 *      BUOY_DISPLAY_LIMIT_KM with a recent wave reading: "Backup buoy NDBC 51101 · 140 km away";
 *   4. else "No recent wave reading (last: Oct 13, 8:56am HST)", the main buoy's last reading
 *      with a wave height, in the spot's own zone, and the main buoy's station link.
 * A main buoy past the limit shows "No buoy within 150 km (nearest: NDBC 42039, 380 km)" and the
 * station link, and nothing else: its fallbacks lie farther still.
 *
 * WHY. NDBC's own buoys can report for hours with no wave height (42035, 46041 and 46053 from
 * 13:50Z on 2026-11-02): rule 1 alone showed a reading time with Hs "—". And a buoy that is quiet
 * for a day (51205 since Oct 13) left the box empty when a fallback had a reading.
 *
 * DISTANCES. The main buoy's is the roster's, from the point the buoy rule runs from. A fallback's
 * is worked here, kmBetween the spot's coordinates and the buoy's (the buoys table), on the
 * pipeline's sphere; the two points differ by at most a few km on a seaward-adjusted spot.
 *
 * THE CLOCK is the page's render time. Spot pages are prerendered and revalidated hourly, so a
 * served copy can be an hour or so older than the age it judged; at a 24-hour threshold that
 * matters only for a reading 23-24 hours old.
 */
import { fmtStamp } from './spotTime.ts';

export const BUOY_DISPLAY_LIMIT_KM = 150;
export const BUOY_STALE_HOURS = 24;
/** The pipeline's sphere (pipeline/geo.py): 1 degree of latitude is 111.1949 km. */
export const EARTH_RADIUS_KM = 6371.0;

export type BuoyReading = {
  buoy_id: string;
  observed_at: string;
  hs: number | null;
  tp: number | null;
  dp: number | null;
  water_temp: number | null;
};

/** A fallback buoy as the page found it: its distance (null if its position is unknown) and its
 *  latest reading with a wave height in the last BUOY_STALE_HOURS (null if none). */
export type BackupBuoy = { id: string; km: number | null; wave: BuoyReading | null };

export type BuoyBox =
  | { kind: 'none' }
  | { kind: 'far'; id: string; text: string }
  | { kind: 'stale'; id: string; title: string; text: string }
  | {
      kind: 'reading';
      id: string;
      title: string;
      label: 'Reading' | 'Last wave reading';
      reading: BuoyReading;
    };

/** "87 km", rounded to the kilometre. */
export function fmtKm(km: number): string {
  return `${Math.round(km)} km`;
}

/** "NDBC 44025 · 87 km away", or "NDBC 44025" when the roster has no distance. */
export function buoyTitle(id: string, km: number | null): string {
  return km === null ? `NDBC ${id}` : `NDBC ${id} · ${fmtKm(km)} away`;
}

/** Great-circle distance in km on the pipeline's sphere. */
export function kmBetween(lat1: number, lng1: number, lat2: number, lng2: number): number {
  const rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad;
  const dLng = (lng2 - lng1) * rad;
  const a = Math.sin(dLat / 2) ** 2
    + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLng / 2) ** 2;
  return 2 * EARTH_RADIUS_KM * Math.asin(Math.min(1, Math.sqrt(a)));
}

/** The oldest observed_at a recent wave reading can have, as an ISO string. */
export function recentSince(nowMs: number): string {
  return new Date(nowMs - BUOY_STALE_HOURS * 3_600_000).toISOString();
}

/** A reading with a wave height, at most BUOY_STALE_HOURS old. */
export function isRecentWave(reading: BuoyReading | null, nowMs: number): boolean {
  return reading !== null
    && reading.hs !== null
    && nowMs - Date.parse(reading.observed_at) <= BUOY_STALE_HOURS * 3_600_000;
}

export type BuoyBoxInput = {
  buoyId: string | null;
  distKm: number | null;
  /** The main buoy's latest reading, with or without a wave height. */
  latest: BuoyReading | null;
  /** The main buoy's latest reading with a wave height, however old. */
  lastWave: BuoyReading | null;
  /** The spot's fallback buoys, in the roster's order. */
  backups: BackupBuoy[];
  nowMs: number;
  timeZone: string;
};

export function buoyBox(input: BuoyBoxInput): BuoyBox {
  const { buoyId, distKm, latest, lastWave, backups, nowMs, timeZone } = input;
  if (!buoyId) return { kind: 'none' };
  if (distKm !== null && distKm > BUOY_DISPLAY_LIMIT_KM) {
    return {
      kind: 'far',
      id: buoyId,
      text: `No buoy within ${BUOY_DISPLAY_LIMIT_KM} km (nearest: NDBC ${buoyId}, ${fmtKm(distKm)})`,
    };
  }
  const title = buoyTitle(buoyId, distKm);
  if (latest && isRecentWave(latest, nowMs)) {
    return { kind: 'reading', id: buoyId, title, label: 'Reading', reading: latest };
  }
  if (lastWave && isRecentWave(lastWave, nowMs)) {
    return { kind: 'reading', id: buoyId, title, label: 'Last wave reading', reading: lastWave };
  }
  for (const b of backups) {
    if (b.km !== null && b.km <= BUOY_DISPLAY_LIMIT_KM && b.wave && isRecentWave(b.wave, nowMs)) {
      return {
        kind: 'reading',
        id: b.id,
        title: `Backup buoy ${buoyTitle(b.id, b.km)}`,
        label: 'Reading',
        reading: b.wave,
      };
    }
  }
  return {
    kind: 'stale',
    id: buoyId,
    title,
    text: lastWave
      ? `No recent wave reading (last: ${fmtStamp(lastWave.observed_at, timeZone)})`
      : 'No recent wave reading',
  };
}
