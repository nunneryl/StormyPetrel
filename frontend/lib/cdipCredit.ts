/**
 * WHEN A SPOT PAGE CREDITS CDIP.
 *
 * CDIP's data-use page (cdip.ucsd.edu/m/documents/data_access.html, "Data Use and
 * Acknowledgements") asks that web displays of its data and products "provide a link to the CDIP
 * homepage". Two kinds of spot page show something built on CDIP MOP output:
 *
 *   the MOP tier    the 48 spots whose roster row says swell_window_source 'cdip_mop'. The
 *                   pipeline reads MOP for their past hours (pipeline/forecast/mop.py).
 *   calibrated      the 141 spots in pipeline/data/spot_face_factors.json, whose height is the
 *                   model's divided by a factor measured against MOP's nowcast. The page labels
 *                   that height "Calibrated to CDIP's nearshore model" (lib/heightBasis.ts).
 *
 * Until this file only the first kind carried the credit, and the two sets do not overlap, so 141
 * pages named CDIP beside their height and credited it nowhere on the page.
 *
 * CALIBRATION IS READ OFF THE ROWS, THE WAY THE LABEL IS. The spots table has no calibrated flag:
 * heightBasis() derives it per row from face_ft and face_ft_raw. The credit uses that same
 * derivation over every row the page has, so any page that can show the calibrated label carries
 * the credit, and the two cannot disagree. A 'cdip' row (MOP's own height) counts as well.
 */
import { heightBasis, type HeightBasisRow } from './heightBasis.ts';

/** The roster's swell_window_source for the MOP tier. */
export const CDIP_MOP_SPOT_SOURCE = 'cdip_mop';

/** The CDIP homepage, which the data-use page asks web displays to link. */
export const CDIP_HOMEPAGE = 'https://cdip.ucsd.edu';

export function showsCdipCredit(
  spot: { swell_window_source?: string | null },
  rows: readonly HeightBasisRow[],
): boolean {
  if (spot.swell_window_source === CDIP_MOP_SPOT_SOURCE) return true;
  return rows.some((r) => {
    const basis = heightBasis(r);
    return basis === 'calibrated' || basis === 'cdip';
  });
}
