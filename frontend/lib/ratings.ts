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
  // CSS color (used by chart fills, popovers, anywhere we need a raw value).
  hex: string;
};

const FLAT: RatingTier = { label: 'FLAT', bg: 'bg-rating-flat', fg: 'text-white', hex: '#5a6a7a' };
const POOR: RatingTier = { label: 'POOR', bg: 'bg-rating-poor', fg: 'text-white', hex: '#c2362f' };
const POOR_FAIR: RatingTier = { label: 'POOR TO FAIR', bg: 'bg-rating-poorfair', fg: 'text-white', hex: '#d97a2b' };
const FAIR: RatingTier = { label: 'FAIR', bg: 'bg-rating-fair', fg: 'text-ink-950', hex: '#d8b13a' };
const FAIR_GOOD: RatingTier = { label: 'FAIR TO GOOD', bg: 'bg-rating-fairgood', fg: 'text-ink-950', hex: '#9bbf3e' };
const GOOD: RatingTier = { label: 'GOOD', bg: 'bg-rating-good', fg: 'text-white', hex: '#3aa55c' };
const GOOD_EPIC: RatingTier = { label: 'GOOD TO EPIC', bg: 'bg-rating-goodepic', fg: 'text-white', hex: '#1ea098' };
const EPIC: RatingTier = { label: 'EPIC', bg: 'bg-rating-epic', fg: 'text-white', hex: '#8b5fbf' };

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

export const RATING_TIERS = [FLAT, POOR, POOR_FAIR, FAIR, FAIR_GOOD, GOOD, GOOD_EPIC, EPIC];
