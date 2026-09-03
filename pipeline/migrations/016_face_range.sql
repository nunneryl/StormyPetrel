-- Migration 016 — the published SWELL HEIGHT band.
--
-- face_ft has always been a point estimate with no stated uncertainty, which reads as more
-- precision than the measurement supports. The MOP face-correction measured a per-spot
-- scatter around the median at the same time it measured the median; these two columns
-- carry it so the site can publish a range instead of a bare number.
--
-- p25/p75 AND NOT p10/p90. p10/p90 is roughly -18% / +23% and rounds a 4 ft number to
-- 2-6 ft: honest, and wide enough to be right about everything and useful for nothing.
-- p25/p75 is roughly +/-20%, rounds 4.0 to 3-5, and puts about half the measured hours
-- inside the band.
--
-- NULLABLE, AND MEANT TO BE. Only spots with a measured spread get values; the rest keep
-- NULL and the site publishes the point alone. That is deliberate — a default band would
-- invent a measurement that was never taken and no reader could tell it from a real one.
-- See pipeline/forecast/face_correction.py:face_range.
--
-- THE COLUMN NAME KEEPS "face". The DISPLAY LABEL is now "Swell height" (CDIP publishes
-- this at the 10-15 m contour, generally outside the surf zone, so "face" was inaccurate),
-- but face_ft is referenced by db_import, the frontend queries, daily_report,
-- surf_reports.forecast_face_ft and migration 015. Renaming the column is a much larger
-- change than relabelling the tile and is deliberately NOT part of this one.
--
-- Run in the Supabase SQL editor (idempotent).

ALTER TABLE forecasts
  ADD COLUMN IF NOT EXISTS face_lo_ft DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS face_hi_ft DOUBLE PRECISION;

COMMENT ON COLUMN forecasts.face_lo_ft IS
  'Low end of the published swell-height band, in feet: corrected face_ft / p75 of the '
  'measured face-ratio distribution. NULL when the spot has no measured spread — that is '
  'not a defect, it is the absence of a measurement, and no default is substituted.';
COMMENT ON COLUMN forecasts.face_hi_ft IS
  'High end of the published swell-height band, in feet: corrected face_ft / p25. NULL '
  'when the spot has no measured spread. face_lo_ft <= face_ft <= face_hi_ft whenever '
  'both are present, because face_ft divides by the median of the same distribution.';
