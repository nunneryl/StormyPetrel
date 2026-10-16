/**
 * Which stored facts about a spot the page may state, by where they came from.
 *
 * ONE RULE, THREE PLACES. The pipeline already holds tide preference and break type to their
 * source: a description states either only when it came from a person's review or a page we
 * cite (pipeline/descriptions.py STATED_SOURCES), and the rating applies a tide preference
 * only from those same sources (pipeline/config.py TIDE_RATED_SOURCES). This module is that
 * rule on the page, so the words, the stars and the spot page all rest on the same evidence.
 * lib/provenance.test.mts reads the two Python lists and fails if this one drifts from them.
 *
 *   reviewed    a person set it
 *   researched  a page about this break was read, cited and quoted
 *   scraped     read off the break's own surf-forecast.com page, cited
 *
 * Anything else — model_recall (a language model's uncited answer), unattributed (held, but
 * no writer recorded itself), derived_from_break_type, unknown, no source, or a source column
 * the database does not have yet — is UNVERIFIED.
 *
 * WHAT THE PAGE DOES WITH AN UNVERIFIED VALUE.
 *   Tide preference  is not shown: the header chip is dropped and both cards show '—'. Every
 *                    tide preference held today (444) is unverified, and the rating no longer
 *                    uses them; a page that printed "tide: mid" beside a rating that ignores it
 *                    would be stating a guess as a fact.
 *   Break type       is shown, marked "(unverified)". Every spot has a break type, it is
 *                    usually right in kind, and hiding it would cost more than it protects.
 *
 * Import-free on purpose, like formatting.ts: it runs under `node --experimental-strip-types`
 * with no packages installed, and it is safe in both server and client components.
 */

/** The sources whose values the page states plainly. Same list, same order, as the pipeline's. */
export const TRUSTED_SOURCES = ['reviewed', 'researched', 'scraped'] as const;

export function isTrustedSource(source: string | null | undefined): boolean {
  return typeof source === 'string' && (TRUSTED_SOURCES as readonly string[]).includes(source);
}

/** The suffix an unverified break type carries. */
export const UNVERIFIED = '(unverified)';

/**
 * A break type as the page states it: the stored value, with " (unverified)" when its source
 * is not trusted. null / undefined stay null (callers render their own '—' or nothing), and a
 * blank string is returned as it is — there is no type to qualify.
 */
export function fmtBreakType(
  value: string | null | undefined,
  source: string | null | undefined,
): string | null {
  if (value === null || value === undefined) return null;
  if (value.trim() === '') return value;
  return isTrustedSource(source) ? value : `${value} ${UNVERIFIED}`;
}

/** The tide preference the page may show: the stored key when its source is trusted, else null. */
export function shownTidePreference(
  value: string | null | undefined,
  source: string | null | undefined,
): string | null {
  if (value === null || value === undefined) return null;
  return isTrustedSource(source) ? value : null;
}
