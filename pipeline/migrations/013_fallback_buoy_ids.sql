-- 013: fallback buoy ids per spot.
--
-- Schema/code drift fix. The enrichment step (pipeline.enrichment.buoys.compute_nearest_buoy)
-- has long emitted `fallback_buoy_ids` — the 2nd-4th nearest NDBC wave buoys that pass the
-- regional distance cap AND the line-of-sight land check, i.e. the standby buoys to swap in when
-- the primary nearest_buoy_id goes offline. db_import carries the field through (it's in the
-- coord-derived preserve-exclusion set, and the import-time distance validator resets it to '{}'
-- alongside a NULLed primary). But no migration ever created the column, so a production
-- db_import upsert that includes the key fails with:
--
--   PGRST204: Could not find the 'fallback_buoy_ids' column of 'spots' in the schema cache
--
-- Type: TEXT[] — a list of NDBC station id strings (e.g. {'46221','46053'}), same shape as the
-- existing hazards / aka_names arrays. Default '{}' so existing rows and future inserts get an
-- empty array; nullable (no NOT NULL) to match the other TEXT[] columns and to avoid a second
-- failure mode — an outage fix should not swap PGRST204 for a NOT NULL violation. db_import only
-- ever writes a list here (the validator resets to '{}', never NULL), so '{}' is the effective
-- floor in practice.
--
-- Run in the Supabase SQL editor (idempotent).

ALTER TABLE spots
  ADD COLUMN IF NOT EXISTS fallback_buoy_ids TEXT[] DEFAULT '{}';

COMMENT ON COLUMN spots.fallback_buoy_ids IS
  'Ordered standby NDBC wave-buoy ids (2nd-4th nearest that pass the regional cap + line-of-sight '
  'check), to swap in when nearest_buoy_id goes offline. Written by pipeline.enrichment.buoys; '
  'reset to ''{}'' by db_import when the primary buoy pairing fails import-time distance validation. '
  'Empty array = no qualifying fallback.';
