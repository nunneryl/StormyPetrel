-- Migration 003 — persist swell-only direction & period alongside the
-- existing total-spectrum dp/tp. NWPS publishes both via DIRPW/PERPW
-- (total) and SWDIR/SWPER (swell only); we already extracted the swell
-- variants into ratings.json but never stored them, which forced the
-- frontend (and any ad-hoc SQL) to fall back to the total values that
-- get dragged off-axis by trade-wind sea. Idempotent.

ALTER TABLE forecasts
  ADD COLUMN IF NOT EXISTS swell_tp DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS swell_dp DOUBLE PRECISION;

COMMENT ON COLUMN forecasts.swell_tp IS
  'NWPS SWPER — peak period of the swell partition only (excludes wind sea). Use for "groundswell period".';
COMMENT ON COLUMN forecasts.swell_dp IS
  'NWPS SWDIR — peak direction of the swell partition only. The rater uses this (not dp/DIRPW) to evaluate the spot''s swell window so trade-wind sea cannot pull the apparent direction off-axis.';
