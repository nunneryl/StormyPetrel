/**
 * WHAT THE SITE SAYS ABOUT ITSELF in search results, share cards and the search box: how many
 * spots it rates, and whose data it is built on. The one place each lives — the site and home
 * page descriptions, their Open Graph and Twitter copies, the home and map page titles, the
 * share image and the search placeholder all read them from here. siteDescription.test.mts
 * holds the count to the pipeline's roster and fails if a count is written into copy by hand.
 *
 * WHY IT EXISTS. The copy said "~500 US spots" in four places and "484" in seven while the
 * pipeline rated 646, and three of the descriptions listed NOAA's feeds without CO-OPS, whose
 * tide predictions are in every rating. Each was a literal, so each drifted on its own.
 *
 * READ FROM THE ROSTER, BY HAND. The count is the number of spots in pipeline/spots_enriched.json
 * that the pipeline rates (is_valid_surf_spot not false — the same rule interpret and db_import
 * apply). It is written out rather than read at build time because the Vercel build sees only
 * this directory; the test is what keeps the two equal. Keep it one line, a plain integer.
 */
export const RATED_SPOT_COUNT = 646;

/** NOAA's feeds behind the forecast, named the way the footer names them. */
export const NOAA_SOURCES = 'NWPS, WAVEWATCH III, HRRR, NDBC and CO-OPS';
