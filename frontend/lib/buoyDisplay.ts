/**
 * What the spot page's "Nearest buoy" box says. Tested in lib/buoyDisplay.test.mts.
 *
 * THE RULE (2026-11-02):
 *   - the buoy's distance is always shown beside its id: "NDBC 44025 · 87 km away";
 *   - past BUOY_DISPLAY_LIMIT_KM the box shows no reading, only
 *     "No buoy within 150 km (nearest: NDBC 42039, 380 km)" and the station link. A buoy that far
 *     measures different water; the assignment stays in the roster for validation;
 *   - a reading more than BUOY_STALE_HOURS old is not shown as current:
 *     "No recent reading (last: Oct 13, 8:56am HST)", in the spot's own zone.
 *
 * WHY. Before this, the box showed whichever buoy the roster named with no distance and no age:
 * Siesta Key's buoy is 380 km away, and Kanaha Beach showed buoy 51205's Oct 13 reading as if it
 * were today's (sw1-raycast run 64, docs/face_validation/run-64/).
 *
 * THE CLOCK is the page's render time. Spot pages are prerendered and revalidated hourly, so a
 * served copy can be an hour or so older than the age it judged; at a 24-hour threshold that
 * matters only for a reading 23-24 hours old.
 */
import { fmtStamp } from './spotTime.ts';

export const BUOY_DISPLAY_LIMIT_KM = 150;
export const BUOY_STALE_HOURS = 24;

export type BuoyReading = {
  buoy_id: string;
  observed_at: string;
  hs: number | null;
  tp: number | null;
  dp: number | null;
  water_temp: number | null;
};

export type BuoyBox =
  | { kind: 'none' }
  | { kind: 'far'; id: string; text: string }
  | { kind: 'stale'; id: string; title: string; text: string }
  | { kind: 'reading'; id: string; title: string; reading: BuoyReading };

/** "87 km", rounded to the kilometre. */
export function fmtKm(km: number): string {
  return `${Math.round(km)} km`;
}

/** "NDBC 44025 · 87 km away", or "NDBC 44025" when the roster has no distance. */
export function buoyTitle(id: string, km: number | null): string {
  return km === null ? `NDBC ${id}` : `NDBC ${id} · ${fmtKm(km)} away`;
}

export function buoyBox(
  buoyId: string | null,
  distKm: number | null,
  reading: BuoyReading | null,
  nowMs: number,
  timeZone: string,
): BuoyBox {
  if (!buoyId) return { kind: 'none' };
  if (distKm !== null && distKm > BUOY_DISPLAY_LIMIT_KM) {
    return {
      kind: 'far',
      id: buoyId,
      text: `No buoy within ${BUOY_DISPLAY_LIMIT_KM} km (nearest: NDBC ${buoyId}, ${fmtKm(distKm)})`,
    };
  }
  const title = buoyTitle(buoyId, distKm);
  if (!reading) return { kind: 'stale', id: buoyId, title, text: 'No recent reading' };
  const ageMs = nowMs - Date.parse(reading.observed_at);
  if (ageMs > BUOY_STALE_HOURS * 3_600_000) {
    return {
      kind: 'stale',
      id: buoyId,
      title,
      text: `No recent reading (last: ${fmtStamp(reading.observed_at, timeZone)})`,
    };
  }
  return { kind: 'reading', id: buoyId, title, reading };
}
