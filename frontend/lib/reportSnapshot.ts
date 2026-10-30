/**
 * THE FORECAST A REPORT IS SET AGAINST, as the page showed it for the reported hour.
 *
 * /api/reports reads the forecasts row for (spot, observed hour, source nwps) and stores this
 * beside the visitor's answer, frozen at the first report (lib/surfReport.ts, migration 027):
 *
 *   face_ft, stars  the numbers (forecast_face_ft, forecast_stars; migration 014)
 *   band            the Surf height tile's range for that height, "3-4ft" (fmtHeightRange). The
 *                   ladder is a display convention and may change; storing what was shown keeps
 *                   "did the answer fall in the range we showed" answerable for old rows.
 *   basis           what the height was: calibrated, cdip or model (lib/heightBasis.ts), so a
 *                   comparison never pools calibrated and model heights.
 *   nwps_cycle      the NWPS cycle the hour was rated from (forecasts.nwps_cycle, migration 019).
 *
 * No row, no snapshot: the route stores NULL in every forecast column rather than another
 * hour's numbers (submitReport's comment says why).
 */
import { fmtHeightRange } from './formatting.ts';
import { heightBasis } from './heightBasis.ts';
import type { ForecastSnapshot } from './surfReport.ts';

/** The forecasts columns the snapshot reads; the route selects exactly these. */
export const SNAPSHOT_COLUMNS =
  'face_ft, face_ft_raw, swell_source, face_correction_version, stars, nwps_cycle';

export type SnapshotRow = {
  face_ft: number | null;
  face_ft_raw: number | null;
  swell_source: string | null;
  face_correction_version: string | null;
  stars: number | null;
  nwps_cycle: string | null;
};

export function snapshotFromRow(row: SnapshotRow): ForecastSnapshot {
  const face = row.face_ft ?? null;
  return {
    face_ft: face,
    stars: row.stars ?? null,
    band: face === null ? null : fmtHeightRange(face),
    basis: heightBasis(row),
    nwps_cycle: row.nwps_cycle ?? null,
  };
}
