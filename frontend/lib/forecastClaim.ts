/**
 * HOW FAR AHEAD THE SITE SAYS ITS FORECAST RUNS. The one place that number lives: every
 * piece of copy that states the forecast's length — the page description, Open Graph and
 * Twitter tags, the JSON-LD, the grid heading, its button, its empty state — reads it from
 * here. forecastClaim.test.mts fails if a literal length reappears in copy, so there is no
 * second copy to drift.
 *
 * WHY IT EXISTS. The copy said "7-day" in nine places while the rated forecast ends at the
 * NWPS cycle's f144, fetched when that cycle is typically ~10 h old. Nothing tied the words
 * to the feed, because the number was a literal repeated across files.
 *
 * A CLAIM, NOT A WINDOW. Nothing here trims the forecast: the grid still shows whatever the
 * feed holds. And the claim is not yet GUARANTEED. The shortest horizon a reader saw in the
 * 7 days to 2026-09-23 was 116.9 h (lox and sju, just before the 09:05 UTC landing), under
 * 5 × 24 = 120 h. Making it reliably true is pipeline work; the test that holds this number
 * against the feed's worst case lands with that fix, because before it the test could only
 * fail or be rigged to pass.
 *
 * READ FROM PYTHON AS WELL. That future guarantee test lives beside the pipeline. It reads
 * the number by parsing the declaration below, the same way test_ci_workflow.py reads
 * versions out of tests.yml — so keep it one line, a plain integer literal.
 */
export const CLAIMED_FORECAST_DAYS = 5;

/** "5-day" — as in "5-day forecast" and "Free 5-day surf forecast for …". */
export const CLAIMED_FORECAST_LABEL = `${CLAIMED_FORECAST_DAYS}-day`;
