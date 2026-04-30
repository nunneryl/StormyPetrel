export type RatingLabel =
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

export function tierFromStars(stars: number | null | undefined): RatingTier {
  if (stars === null || stars === undefined) return FLAT;
  if (stars <= 0) return FLAT;
  if (stars <= 1.5) return POOR;
  if (stars < 2.5) return POOR_FAIR;
  if (stars < 3.5) return FAIR;
  if (stars < 4) return FAIR_GOOD;
  if (stars < 4.5) return GOOD;
  if (stars < 5) return GOOD_EPIC;
  return EPIC;
}

export const RATING_TIERS = [
  FLAT, POOR, POOR_FAIR, FAIR, FAIR_GOOD, GOOD, GOOD_EPIC, EPIC,
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

// Chop classification — derived from chop_ratio (wind sea / total Hs).
export type ChopLevel = 'clean' | 'mixed' | 'choppy' | 'blown' | 'unknown';

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

export function chopLabel(c: ChopLevel): string {
  switch (c) {
    case 'clean':   return 'Clean';
    case 'mixed':   return 'Mixed';
    case 'choppy':  return 'Choppy';
    case 'blown':   return 'Blown out';
    default:        return '—';
  }
}
