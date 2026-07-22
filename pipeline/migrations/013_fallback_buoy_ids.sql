-- 013_fallback_buoy_ids.sql
-- Add the spots.fallback_buoy_ids column that db_import writes but no migration ever created.
--
-- The enrichment step (pipeline/enrichment/buoys.py) computes, for every spot, up to three
-- SECONDARY NDBC wave-buoy ids to fall back to when the nearest buoy is offline — a list of
-- station-id strings, e.g. '{46012,46026}', or empty '{}' when the spot has no buoy. db_import's
-- import-time validation (pipeline/db_import._validate_coord_derived) writes fallback_buoy_ids = []
-- whenever it NULLs an inconsistent buoy pairing, and the column is listed among the coordinate-derived
-- fields the preserve-merge must drop on a coordinate change. But the spots table was never given the
-- column, so any run in which the validation nulls a pairing sends a key PostgREST can't resolve:
--   PGRST204: Could not find the 'fallback_buoy_ids' column of 'spots' in the schema cache
-- and the whole upsert batch fails. (Latent until the committed buoy snapshot activated the validation.)
--
-- Type mirrors the existing text-array columns (hazards, aka_names): a list of NDBC buoy-id strings.
-- Nullable — db_import does not carry it in every record, and PostgREST NULLs any key absent from an
-- upserted row, so a NOT NULL constraint would itself break the upsert. DEFAULT '{}' (empty array) is
-- the code's own "no fallback buoys" value, so existing rows read as [] rather than NULL.
--
-- Run in the Supabase SQL editor (idempotent).

ALTER TABLE spots
  ADD COLUMN IF NOT EXISTS fallback_buoy_ids TEXT[] DEFAULT '{}'::text[];

COMMENT ON COLUMN spots.fallback_buoy_ids IS
  'Secondary NDBC wave-buoy ids (station-id strings) to fall back to when nearest_buoy_id is offline; '
  'up to 3, empty when the spot has no buoy. Computed in pipeline/enrichment/buoys.py, written by '
  'db_import. Coordinate-derived: dropped by the preserve-merge on a coordinate change so it recomputes.';
