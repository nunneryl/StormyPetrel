/**
 * THE NAME A SPOT IS SHOWN UNDER: its roster name without a trailing ", <its state>" or
 * " <its state>". Twelve roster names carry their own state ("Mavericks, California",
 * "Pūpūkea, Hawaii", "Sunset Beach Hawaii"), which then read twice on the page: "Mavericks,
 * California" over "California · Half Moon Bay", and "Mavericks, California Surf Forecast" as
 * the title.
 *
 * DISPLAY ONLY. The roster keeps the name, and the slug, which the pipeline derives from the
 * name (pipeline/db_import.py _slugify) and keys a dozen data files on, stays exactly as it is:
 * no URL, redirect, override or description changes. A name where the state is part of a place
 * ("Virginia Beach Oceanfront", "North End Virginia Beach") does not END with it and is left
 * alone. lib/spotName.test.mts holds the result against the whole roster: twelve names change,
 * and every shown name stays unique.
 *
 * Imports nothing, so client code and tests can use it.
 */
function escapeRe(s: string): string {
  return s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

export function displaySpotName(name: string, state: string | null | undefined): string {
  if (!name || !state) return name;
  const stripped = name.replace(new RegExp(`(?:,\\s*|\\s+)${escapeRe(state)}$`, 'i'), '').trim();
  return stripped.length > 0 ? stripped : name;
}

/** A spot row with its shown name in `name`. Everything else, the slug included, as it was. */
export function withDisplayName<T extends { name: string; state?: string | null }>(spot: T): T {
  const shown = displaySpotName(spot.name, spot.state);
  return shown === spot.name ? spot : { ...spot, name: shown };
}
