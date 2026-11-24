export type RatingLabel =
  | 'NO DATA'
  | 'FLAT'
  | 'POOR'
  | 'POOR TO FAIR'
  | 'FAIR'
  | 'FAIR TO GOOD'
  | 'GOOD'
  | 'GOOD TO EPIC'
  | 'EPIC';

type RatingTier = {
  label: RatingLabel;
  bg: string;
  fg: string;
  /** Raw hex — used by chart fills, leaflet markers, anywhere a CSS color is needed. */
  hex: string;
  /** Glow / aura color used behind big rating pills on the spot page. */
  glow: string;
};

// NO RATING IS NOT FLAT. A spot with no forecast row for the hour, or a row with no stars, has
// no verdict at all; FLAT is the verdict "no surf". They used to be the same thing on every card,
// pin and badge (a missing rating was read as 0), so a spot the pipeline had not rated, or the
// whole site during an outage, read as confidently flat. Grey like FLAT, but never filled: on the
// map it is a hollow ring (pinStyle below).
const NO_DATA: RatingTier =
  { label: 'NO DATA',      bg: 'bg-ink-700',         fg: 'text-text-muted', hex: '#94A3B8', glow: 'rgba(148,163,184,0.20)' };
const FLAT: RatingTier =
  { label: 'FLAT',         bg: 'bg-rating-flat',     fg: 'text-white',    hex: '#6B7280', glow: 'rgba(107,114,128,0.25)' };
const POOR: RatingTier =
  { label: 'POOR',         bg: 'bg-rating-poor',     fg: 'text-white',    hex: '#EF4444', glow: 'rgba(239,68,68,0.30)' };
const POOR_FAIR: RatingTier =
  { label: 'POOR TO FAIR', bg: 'bg-rating-poorfair', fg: 'text-white',    hex: '#F97316', glow: 'rgba(249,115,22,0.30)' };
const FAIR: RatingTier =
  { label: 'FAIR',         bg: 'bg-rating-fair',     fg: 'text-ink-950',  hex: '#EAB308', glow: 'rgba(234,179,8,0.30)' };
const FAIR_GOOD: RatingTier =
  { label: 'FAIR TO GOOD', bg: 'bg-rating-fairgood', fg: 'text-ink-950',  hex: '#84CC16', glow: 'rgba(132,204,22,0.30)' };
const GOOD: RatingTier =
  { label: 'GOOD',         bg: 'bg-rating-good',     fg: 'text-white',    hex: '#22C55E', glow: 'rgba(34,197,94,0.30)' };
const GOOD_EPIC: RatingTier =
  { label: 'GOOD TO EPIC', bg: 'bg-rating-goodepic', fg: 'text-white',    hex: '#14B8A6', glow: 'rgba(20,184,166,0.35)' };
const EPIC: RatingTier =
  { label: 'EPIC',         bg: 'bg-rating-epic',     fg: 'text-white',    hex: '#8B5CF6', glow: 'rgba(139,92,246,0.40)' };

/** What a stars value says: no rating at all, the FLAT verdict (0 or below), or a star rating. */
export type RatingState = 'none' | 'flat' | 'rated';

/** The words shown where a rating is missing (StarRating, the map popup). */
export const NO_DATA_TEXT = 'No data';

export function ratingState(stars: number | null | undefined): RatingState {
  if (stars === null || stars === undefined || !Number.isFinite(stars)) return 'none';
  return stars <= 0 ? 'flat' : 'rated';
}

export function tierFromStars(stars: number | null | undefined): RatingTier {
  if (ratingState(stars) === 'none') return NO_DATA;
  const s = stars as number;
  if (s <= 0) return FLAT;
  if (s <= 1.5) return POOR;
  if (s < 2.5) return POOR_FAIR;
  if (s < 3.5) return FAIR;
  if (s < 4) return FAIR_GOOD;
  if (s < 4.5) return GOOD;
  if (s < 5) return GOOD_EPIC;
  return EPIC;
}

/** The best rating among several (a region's spots, a map cluster's pins), or null when none of
 *  them has one. FLAT (0) counts as a rating: a region whose only verdict is FLAT is FLAT. */
export function bestStars(values: readonly (number | null | undefined)[]): number | null {
  let best: number | null = null;
  for (const v of values) {
    if (ratingState(v) === 'none') continue;
    if (best === null || (v as number) > best) best = v as number;
  }
  return best;
}

/** The homepage's "Best conditions right now": the n best RATED spots, best first. A spot with no
 *  rating cannot be one of the best, so it is left out rather than sorted to the bottom, and the
 *  list is empty, not padded, when nothing is rated. Ties keep their input order. */
export function topRated<T>(items: readonly T[], starsOf: (t: T) => number | null | undefined, n: number): T[] {
  return items
    .filter((t) => ratingState(starsOf(t)) !== 'none')
    .map((t, i) => ({ t, i, s: starsOf(t) as number }))
    .sort((a, b) => b.s - a.s || a.i - b.i)
    .slice(0, n)
    .map((x) => x.t);
}

/** A map pin or cluster: filled in its tier's colour, or, with no rating, a hollow grey ring, so
 *  it cannot be mistaken for FLAT's filled grey. `text` is the cluster count's colour. */
export function pinStyle(stars: number | null | undefined): { fill: string; ring: string; text: string; hollow: boolean } {
  const tier = tierFromStars(stars);
  if (ratingState(stars) === 'none') {
    return { fill: '#FFFFFF', ring: tier.hex, text: '#475569', hollow: true };
  }
  const darkText = tier.label === 'FAIR' || tier.label === 'FAIR TO GOOD';
  return { fill: tier.hex, ring: '#FFFFFF', text: darkText ? '#0F172A' : '#FFFFFF', hollow: false };
}

export const RATING_TIERS = [
  NO_DATA, FLAT, POOR, POOR_FAIR, FAIR, FAIR_GOOD, GOOD, GOOD_EPIC, EPIC,
];

// Wind quality classification — used by the wind tile + grid micro-label
// to color "offshore / cross / onshore". offshore_wind_deg is the spot's
// directly-offshore bearing; deviation is mod 180.
export type WindQuality = 'offshore' | 'cross-offshore' | 'cross' | 'cross-onshore' | 'onshore' | 'unknown';

const WIND_Q_BG: Record<WindQuality, string> = {
  offshore:        'bg-wind_q-offshore/15 text-wind_q-offshore',
  'cross-offshore': 'bg-wind_q-offshore/10 text-wind_q-offshore',
  cross:           'bg-wind_q-cross/15 text-wind_q-cross',
  'cross-onshore': 'bg-wind_q-onshore/10 text-wind_q-onshore',
  onshore:         'bg-wind_q-onshore/15 text-wind_q-onshore',
  unknown:         'bg-ink-700 text-text-muted',
};

export function windQualityClass(q: WindQuality): string {
  return WIND_Q_BG[q];
}

export function classifyWind(
  windDirDeg: number | null | undefined,
  offshoreDeg: number | null | undefined,
): WindQuality {
  if (windDirDeg === null || windDirDeg === undefined) return 'unknown';
  if (offshoreDeg === null || offshoreDeg === undefined) return 'unknown';
  const diff = Math.abs(((windDirDeg - offshoreDeg + 540) % 360) - 180);
  if (diff < 30) return 'offshore';
  if (diff < 60) return 'cross-offshore';
  if (diff < 120) return 'cross';
  if (diff < 150) return 'cross-onshore';
  return 'onshore';
}

export function windQualityLabel(q: WindQuality): string {
  switch (q) {
    case 'offshore':       return 'offshore';
    case 'cross-offshore': return 'cross-off';
    case 'cross':          return 'cross';
    case 'cross-onshore':  return 'cross-on';
    case 'onshore':        return 'onshore';
    default:               return '';
  }
}

// Surface state — the Clean / Mixed / Choppy / Blown out vocabulary shown on the
// Conditions tile. Named ChopLevel historically, when chop_ratio was the only input;
// SurfaceState is the honest name and ChopLevel is kept as an alias so nothing breaks.
export type SurfaceState = 'clean' | 'mixed' | 'choppy' | 'blown' | 'unknown';
export type ChopLevel = SurfaceState;

/**
 * Absolute angular difference between the wind's FROM-bearing and the spot's
 * directly-offshore bearing, wrapped into [0, 180]. 0 = straight offshore,
 * 180 = straight onshore.
 *
 * classifyWind computes this same quantity inline; it is deliberately NOT
 * refactored to share this helper, because that function is unchanged by this
 * commit and the Wind tile must keep behaving exactly as it does today. If the
 * two are ever unified, unify them in a commit that does only that.
 */
export function offAngleDeg(
  windDirDeg: number,
  offshoreDeg: number,
): number {
  return Math.abs(((windDirDeg - offshoreDeg + 540) % 360) - 180);
}

/**
 * SURFACE STATE FROM WIND — what the Conditions tile shows.
 *
 * Blown out is a WIND condition. The Encyclopedia of Surfing: "ocean surface
 * condition created by a moderate-to-strong onshore wind, which, by degrees,
 * produces chopped-up, crumbly, messy surf." Direction and speed, nothing else.
 *
 *   wind_speed / wind_dir / offshore_wind_deg missing  -> unknown
 *   wind_speed < 2.5 m/s                              -> clean   (glassy; direction stops mattering)
 *   off_angle <= 60                                    -> clean   (offshore)
 *   off_angle < 120                                    -> mixed   (cross-shore)
 *   off_angle >= 120 and wind_speed < 6 m/s            -> choppy  (light onshore)
 *   off_angle >= 120 and wind_speed >= 6 m/s           -> blown   (moderate-to-strong onshore)
 *
 * 120 itself is ONSHORE, as in the wind cap (pipeline/interpret.py wind_class: 120 and over),
 * so an hour the cap holds to 2.5 stars as onshore at 6 m/s is never labelled "Mixed".
 *
 * SPEED IS TESTED BEFORE DIRECTION, deliberately: a 1 m/s straight-onshore hour is
 * glassy, not choppy, and the glassy case has to win or dawn patrol reads wrong.
 *
 * *windSpeedMs is in METRES PER SECOND* — the raw stored unit. The frontend converts
 * to mph only at render (fmtMph/msToMph), so pass forecast.wind_speed straight in.
 *
 * Measured over 84,774 production spot-hours: Clean 43.5%, Mixed 23.8%, Choppy 28.3%,
 * Blown out 4.4%, with wind_mult monotonically decreasing across the bands
 * (0.926/0.897 -> 0.832 -> 0.662 -> 0.578) and star ceilings 4.5 / 4.0 / 4.0 / 3.5.
 */
export function classifySurface(
  windDirDeg: number | null | undefined,
  windSpeedMs: number | null | undefined,
  offshoreDeg: number | null | undefined,
): SurfaceState {
  if (windSpeedMs === null || windSpeedMs === undefined) return 'unknown';
  if (windDirDeg === null || windDirDeg === undefined) return 'unknown';
  if (offshoreDeg === null || offshoreDeg === undefined) return 'unknown';
  if (windSpeedMs < 2.5) return 'clean';
  const off = offAngleDeg(windDirDeg, offshoreDeg);
  if (off <= 60) return 'clean';
  if (off < 120) return 'mixed';
  if (windSpeedMs < 6) return 'choppy';
  return 'blown';
}

/**
 * Chop classification from chop_ratio (the wind-sea fraction of total Hs).
 *
 * @deprecated FOR LABELLING. Still correct for what it measures, and kept because
 * chop_ratio remains a real published statistic — but it must not drive the
 * Clean/Mixed/Choppy/Blown out words. Use classifySurface for those.
 *
 * WHY IT WAS REPOINTED. chop_ratio is a sea/swell HEIGHT FRACTION describing water
 * offshore; "blown out" is a surface condition produced by onshore wind. They are
 * different quantities, and measured over 84,774 production spot-hours this function's
 * output was essentially INDEPENDENT of wind: "Blown out" fired at 57-64% in every
 * wind-direction band and "Clean" at 2-6%, with "Clean" firing MORE often onshore
 * (5.9%) than offshore (1.9%). 57.3% of offshore-wind hours were labelled "Blown out"
 * — while the Wind tile two columns to the left showed an offshore badge for the same
 * hour. The two tiles contradicted each other in the same strip.
 */
export function classifyChop(chopRatio: number | null | undefined): ChopLevel {
  if (chopRatio === null || chopRatio === undefined) return 'unknown';
  if (chopRatio < 0.2) return 'clean';
  if (chopRatio < 0.4) return 'mixed';
  if (chopRatio < 0.6) return 'choppy';
  return 'blown';
}

export function chopBadgeClass(c: ChopLevel): string {
  switch (c) {
    case 'clean':   return 'bg-wind_q-offshore/15 text-wind_q-offshore';
    case 'mixed':   return 'bg-wind_q-cross/15 text-wind_q-cross';
    case 'choppy':  return 'bg-rating-poorfair/15 text-rating-poorfair';
    case 'blown':   return 'bg-rating-poor/15 text-rating-poor';
    default:        return 'bg-ink-700 text-text-muted';
  }
}

/**
 * TEXT-ONLY colour for a surface state, for rendering the word itself rather than a
 * chip. chopBadgeClass returns a background AND a foreground; this returns only the
 * foreground, so the Conditions tile can carry its colour on the value text now that
 * it no longer renders a separate badge.
 *
 * 'unknown' returns the EMPTY STRING on purpose — that leaves BigTile's own
 * text-text-primary in place, so the em-dash renders exactly as it does today.
 */
export function surfaceTextClass(c: SurfaceState): string {
  switch (c) {
    case 'clean':   return 'text-wind_q-offshore';
    case 'mixed':   return 'text-wind_q-cross';
    case 'choppy':  return 'text-rating-poorfair';
    case 'blown':   return 'text-rating-poor';
    default:        return '';
  }
}

export function chopLabel(c: ChopLevel): string {
  switch (c) {
    case 'clean':   return 'Clean';
    case 'mixed':   return 'Mixed';
    case 'choppy':  return 'Choppy';
    case 'blown':   return 'Blown out';
    default:        return '—';
  }
}
