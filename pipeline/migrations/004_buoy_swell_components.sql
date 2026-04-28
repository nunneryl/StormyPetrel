-- Migration 004 — capture the swell-only spectral components NDBC publishes
-- in each buoy's .spec file (SwH / SwP / SwD). The existing hs / tp / dp on
-- buoy_observations come from the .std file, which only knows the dominant
-- (combined wind sea + swell) wave; without these we have no observed
-- ground-truth swell direction to fall back on when NWPS doesn't carry
-- SWPER / SWDIR — and that gap is what makes Hawaii / SoCal spots rate
-- FLAT all summer under trade-wind sea. Idempotent.

ALTER TABLE buoy_observations
  ADD COLUMN IF NOT EXISTS swell_hs DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS swell_tp DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS swell_dp DOUBLE PRECISION;

COMMENT ON COLUMN buoy_observations.swell_hs IS
  'NDBC .spec SwH — significant height of the swell partition only (excludes wind sea).';
COMMENT ON COLUMN buoy_observations.swell_tp IS
  'NDBC .spec SwP — peak period of the swell partition only.';
COMMENT ON COLUMN buoy_observations.swell_dp IS
  'NDBC .spec SwD converted from cardinal (NNW, etc.) to degrees. The interpret step uses this as ground truth swell direction for any spot whose NWPS GRIB lacks SWDIR.';
