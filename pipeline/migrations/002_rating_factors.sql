-- Migration 002 — add the chop and period-quality factors that interpret.py
-- writes alongside face_ft / dir_gain / wind_mult / tide_mult. Idempotent.

ALTER TABLE forecasts
  ADD COLUMN IF NOT EXISTS chop_ratio DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS chop_mult DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS period_quality DOUBLE PRECISION;

COMMENT ON COLUMN forecasts.chop_ratio IS
  'Fraction of total Hs that is wind sea (1 - swell_hs/hs). 0 = pure swell, 1 = pure chop.';
COMMENT ON COLUMN forecasts.chop_mult IS
  'Rating multiplier from chop_ratio: 1.0 when clean, 0.3 when nearly all wind sea.';
COMMENT ON COLUMN forecasts.period_quality IS
  'Rating multiplier from peak period: 0.5 at <=6s, 1.0 at 13s, 1.05 at 16s+.';
