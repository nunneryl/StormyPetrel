-- Migration 005 — WAVEWATCH III spectral swell partitions per forecast hour.
--
-- gfswave (NCEP's operational WW3) publishes 3 swell partitions plus a
-- wind sea component at every grid cell every forecast hour. This is the
-- data Surfline / MagicSeaweed quote when they show "2ft 10s NNW + 0.4ft
-- 15s NW + 0.4ft 11s WSW" — three numbers per direction, three periods,
-- three heights. Persisting all three (instead of collapsing to a single
-- "swell") lets the rater pick the best in-window partition and lets the
-- frontend render the same multi-component readout. Idempotent.
--
-- swell_source records which feed actually drove the rating for this hour
-- so audits can tell ww3_partition apart from buoy / nwps_total fallback.

ALTER TABLE forecasts
  ADD COLUMN IF NOT EXISTS swell_1_hs DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS swell_1_tp DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS swell_1_dp DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS swell_2_hs DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS swell_2_tp DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS swell_2_dp DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS swell_3_hs DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS swell_3_tp DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS swell_3_dp DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS wind_wave_hs DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS wind_wave_tp DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS wind_wave_dp DOUBLE PRECISION,
  ADD COLUMN IF NOT EXISTS swell_source TEXT;

COMMENT ON COLUMN forecasts.swell_1_hs IS 'WW3 partition 1 (primary) significant height in m.';
COMMENT ON COLUMN forecasts.swell_1_tp IS 'WW3 partition 1 peak period in s.';
COMMENT ON COLUMN forecasts.swell_1_dp IS 'WW3 partition 1 peak direction in deg.';
COMMENT ON COLUMN forecasts.swell_2_hs IS 'WW3 partition 2 (secondary) significant height in m.';
COMMENT ON COLUMN forecasts.swell_3_hs IS 'WW3 partition 3 (tertiary) significant height in m.';
COMMENT ON COLUMN forecasts.wind_wave_hs IS 'WW3 wind-wave partition Hs (locally generated, short-period).';
COMMENT ON COLUMN forecasts.swell_source IS
  'Which feed drove dir_gain for this hour: ww3 (best in-window WW3 partition), nwps_swell (NWPS SWDIR), buoy (NDBC SwD), nwps_total (DIRPW last resort), or none.';
