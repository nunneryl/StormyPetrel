/**
 * WHAT A PUBLISHED HEIGHT IS, read off the row that carries it.
 *
 * Three different things reach readers under the one "Swell height" heading, and until this
 * file nothing on the page said which one a number was:
 *
 *   calibrated  the model's height divided by the spot's measured factor, so its typical value
 *               matches CDIP's nearshore height. The 141 spots in
 *               pipeline/data/spot_face_factors.json.
 *   cdip        CDIP MOP's own nearshore height, from its nowcast (pipeline/forecast/mop.py).
 *   model       everything else: the wave models' height with the period boost, uncorrected.
 *               It can read well above the other two — about 1.6 times MOP's at the
 *               California spots where it was measured.
 *
 * DERIVED PER ROW, FROM TWO COLUMNS, WITH NO NEW ONE. face_correction.stamp_provenance copies
 * face_ft into face_ft_raw on every rated hour BEFORE anything divides, nothing writes face_ft
 * after the correction step, and db_import passes both through untouched. So an hour the
 * correction never touched carries two identical values, and one it divided carries two
 * different ones. apply_mop_overrides tags every hour it writes with swell_source 'cdip_mop'.
 *
 * "DIFFERS", NOT "IS LARGER". A factor is usually above 1, so a calibrated height is usually
 * the smaller of the two, but five spots' factors are below 1 — point-arena's 0.812 the lowest,
 * mendocino's 0.9818 the nearest to 1: dividing by them makes the published height LARGER than
 * the raw one. A test for raw > face would call all five of them model estimates.
 *
 * WHEN IN DOUBT, IT IS A MODEL ESTIMATE. That is the direction that cannot overclaim. A row
 * whose raw value is missing (written before migration 017) or not a finite number has no
 * evidence of calibration, so it is not called calibrated. The same rule makes one honest
 * edge visible: at a calibrated spot, an hour so small that dividing it does not move the
 * second decimal — a flat 0.00 ft hour, say — reads as a model estimate, because the number
 * shown is exactly the model's.
 *
 * AS OF 2026-09-28 NO READER SEES A 'cdip' ROW. MOP's nowcast ends before the current hour,
 * and every surface reads the current hour onward, so the heights shown at the 48 MOP-tier
 * spots are model estimates like anywhere else. The label is here so that the day MOP rows
 * do reach the page they are named for what they are, rather than falling into 'model'.
 *
 * pipeline/daily_report.py derives the same three values for the report prompt, and stores each
 * top spot's in daily_reports.top_spots for the report cards (read back by storedHeightBasis,
 * below); the two derivations are held to one table of cases by heightBasis.test.mts and
 * test_daily_report_height_basis.py.
 */

export type HeightBasis = 'calibrated' | 'cdip' | 'model';

/** The columns the derivation reads. Every reader query selects all three. */
export type HeightBasisRow = {
  face_ft: number | null | undefined;
  face_ft_raw: number | null | undefined;
  swell_source: string | null | undefined;
};

/** The swell_source apply_mop_overrides writes on every hour it feeds. */
export const CDIP_MOP_SWELL_SOURCE = 'cdip_mop';

/** What the row's height is, or null when there is no height to describe. */
export function heightBasis(row: HeightBasisRow | null | undefined): HeightBasis | null {
  if (!row || row.face_ft === null || row.face_ft === undefined) return null;
  const face = row.face_ft;
  const raw = row.face_ft_raw;
  if (
    raw !== null && raw !== undefined
    && Number.isFinite(raw) && Number.isFinite(face)
    && raw !== face
  ) {
    return 'calibrated';
  }
  if (row.swell_source === CDIP_MOP_SWELL_SOURCE) return 'cdip';
  return 'model';
}

/**
 * The basis a daily report STORED for one of its top spots, or null for no tag.
 *
 * pipeline/daily_report.py writes `height_basis` into each daily_reports.top_spots entry, by the
 * same rule as heightBasis(), off the very row whose face_ft it stores. Reports stored before it
 * did have no such field, and for them this is null: the row their height came from is gone, and
 * anything else — the spot's current row, the spot's calibration status — would be a guess about
 * a different number. Only the three exact strings are accepted, since top_spots is JSON from the
 * database and a value this code never writes is not a basis.
 */
export function storedHeightBasis(value: unknown): HeightBasis | null {
  return value === 'calibrated' || value === 'cdip' || value === 'model' ? value : null;
}

/** Beside the height on the spot page.
 *
 *  "CDIP'S NEARSHORE MODEL", NOT "CDIP MEASUREMENTS". The factors divide our height by its
 *  ratio to MOP's nowcast, and MOP is CDIP's buoy-driven nearshore MODEL at points where
 *  there is no buoy. Calling it a measurement claimed an observation that was never made. */
export const HEIGHT_BASIS_LABEL: Record<HeightBasis, string> = {
  calibrated: "Calibrated to CDIP's nearshore model",
  cdip: 'CDIP nearshore height',
  model: 'Model estimate',
};

/** The compact marker after a height on cards, the map popup, search and the region list. */
export const HEIGHT_BASIS_MARK: Record<HeightBasis, string> = {
  calibrated: 'calibrated',
  cdip: 'CDIP',
  model: 'model',
};

/** The one line under the spot page's tiles, ahead of the link to the methodology post. */
export const HEIGHT_BASIS_NOTE: Record<HeightBasis, string> = {
  calibrated: "Scaled so its typical height matches CDIP's nearshore height at this spot.",
  cdip: "CDIP's own nearshore height for this hour.",
  model: 'Not calibrated against CDIP, so it can read higher than nearshore swell height.',
};

/** Where the note links: the post that explains all three. */
export const HEIGHT_METHOD_HREF = '/blog/methodology';
export const HEIGHT_METHOD_LINK_TEXT = 'How our heights work';

/** Text colour of the marker: calibrated and CDIP heights share CDIP's scale, a model estimate
 *  does not. Hex values, so the map popup's inline styles and the Tailwind classes agree. */
export const HEIGHT_BASIS_COLOR: Record<HeightBasis, string> = {
  calibrated: '#0369A1',   // cyan-600, the brand blue
  cdip: '#0369A1',
  model: '#475569',        // text-secondary
};

function escapeHtml(s: string): string {
  return s
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

/** The compact marker as HTML, for the Leaflet popup, which is built as a string. */
export function heightBasisMarkHtml(basis: HeightBasis | null): string {
  if (basis === null) return '';
  return `<span title="${escapeHtml(HEIGHT_BASIS_LABEL[basis])}" `
    + `style="font-size:10px;font-weight:500;color:${HEIGHT_BASIS_COLOR[basis]};">`
    + `${escapeHtml(HEIGHT_BASIS_MARK[basis])}</span>`;
}
