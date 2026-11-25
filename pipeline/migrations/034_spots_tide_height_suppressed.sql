-- 034_spots_tide_height_suppressed.sql
-- spots.tide_height_suppressed: the spot's tide station is trusted for the tide's TIMING but not
-- for its HEIGHT.
--
-- WHY. Four Mendocino spots (Caspar, Jug Handle, Mackerricher, Ten Mile Beach) take their tide
-- from NOAA CO-OPS 9416841, Arena Cove, 50 to 70 km away down the same open coast
-- (pipeline/data/spot_tide_stations.json, "suppress_height"). The pipeline keeps the station's
-- phase (forecasts.tide_norm) and withholds its height (forecasts.tide_level_ft NULL), so the
-- spot page said "No tide data". With this flag the page knows the tide's times are good and
-- shows them, with the high and low times and rising or falling, and no feet
-- (frontend/lib/tideTiming.ts).
--
-- WRITTEN BY pipeline/db_import.py on every spot, true or false, at each import. Until the first
-- import after this migration every spot reads false (the default), and the page behaves as before.
--
-- Run in the Supabase SQL editor BEFORE merging the code that writes it. Idempotent: safe to run
-- again. (If the code ran first, the column preflight would leave the column out of the spots
-- upsert and the import would carry on without it.)
--
-- CHECK 1, after running (one row: boolean, NO, false):
--   SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns
--   WHERE table_schema = 'public' AND table_name = 'spots' AND column_name = 'tide_height_suppressed';
-- CHECK 2, after the first pipeline run that follows the merge (four rows, all on 9416841,
-- 50.5 to 69.6 km):
--   SELECT slug, nearest_tide_station_id, nearest_tide_station_dist_km FROM spots
--   WHERE tide_height_suppressed ORDER BY slug;

ALTER TABLE spots
  ADD COLUMN IF NOT EXISTS tide_height_suppressed BOOLEAN NOT NULL DEFAULT false;

COMMENT ON COLUMN spots.tide_height_suppressed IS
  'The tide station is trusted for timing (forecasts.tide_norm) but not height (tide_level_ft NULL); the page shows tide times only. Written by pipeline/db_import.py from spots_enriched.json.';
