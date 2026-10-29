-- Migration 026 — each spot's IANA time zone (spots.time_zone).
--
-- WHY. The site showed every spot's times in America/Los_Angeles: the forecast grid, the chart
-- axes and tooltips, the buoy reading and the surf-report hour picker. An East Coast spot's
-- "5pm" row was 8pm on the beach. The frontend now shows each spot's times in its own zone
-- (frontend/lib/spotTime.ts) and reads that zone from this column, so it never works one out
-- per request.
--
-- WHERE THE VALUE COMES FROM. pipeline/enrichment/time_zone.py looks each spot's coordinates
-- up in the timezone-boundary-builder polygons (the timezonefinder package), never by state,
-- and writes it to pipeline/spots_enriched.json; db_import uploads it on every pipeline run,
-- like every other spots column. On the 2026-10-29 roster: 235 spots America/Los_Angeles,
-- 315 America/New_York, 19 America/Chicago (12 in Texas and the 7 Florida panhandle spots
-- from Pensacola Beach to Mexico Beach), 55 Pacific/Honolulu and 24 America/Puerto_Rico.
--
-- WHAT THIS ADDS. One nullable text column and a shape check. NULL means the pipeline has not
-- uploaded a zone yet; the frontend then shows America/Los_Angeles, as before, with the zone's
-- abbreviation beside every time. No backfill here: the next pipeline run fills every row.
--
-- ORDER OF OPERATIONS — RUN THIS MIGRATION FIRST, THEN MERGE. Merged first, the run's column
-- preflight finds time_zone missing, leaves it out of the spots upload and ends the run
-- failed, naming this file; forecasts are still published.
--
-- IDEMPOTENT. The column is added IF NOT EXISTS and the check is dropped and re-added.
--
-- CHECK QUERY 1 — run right after applying. Every row must read ok = true.
-- pipeline/tests/test_time_zone_migration.py runs this exact text against a database shaped
-- like the live one:
--
--   SELECT check_name, expected, actual, expected = actual AS ok
--     FROM (VALUES
--       ('time_zone column added',            'text',
--        (SELECT data_type FROM information_schema.columns
--          WHERE table_schema = 'public' AND table_name = 'spots'
--            AND column_name = 'time_zone')),
--       ('time_zone check in place',          '1',
--        (SELECT count(*) FROM pg_constraint
--          WHERE conrelid = 'public.spots'::regclass
--            AND conname = 'spots_time_zone_check')::text),
--       ('zones Postgres does not know',      '0',
--        (SELECT count(*) FROM spots
--          WHERE time_zone IS NOT NULL
--            AND time_zone NOT IN (SELECT name FROM pg_timezone_names))::text)
--     ) AS c(check_name, expected, actual);
--
-- CHECK QUERY 2 — run after the first pipeline run following the merge. One row per zone,
-- most spots first, with how many spots are in it and a few of their names; expect the five
-- zones above and no '(none)' row. Rows without a zone would show as '(none)', last.
--
--   SELECT coalesce(time_zone, '(none)') AS time_zone, count(*) AS spots,
--          string_agg(name, ', ' ORDER BY lng) FILTER (WHERE state = 'Florida'
--            AND time_zone = 'America/Chicago') AS florida_central,
--          (array_agg(name ORDER BY name))[1:3] AS e_g
--     FROM spots
--    GROUP BY 1
--    ORDER BY 2 DESC, min(time_zone) NULLS LAST;
--
-- Run in the Supabase SQL editor.

ALTER TABLE spots ADD COLUMN IF NOT EXISTS time_zone TEXT;

-- An IANA name's shape: Area/Location, optionally Area/Location/Sub. A shape check, because a
-- CHECK cannot look the name up; check query 1 holds every value to pg_timezone_names.
ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_time_zone_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_time_zone_check CHECK (
    time_zone IS NULL OR time_zone ~ '^[A-Za-z]+(/[A-Za-z0-9_+-]+){1,2}$'
  );
