-- 010_swell_window_source.sql
-- Per-spot swell-source provenance flag for frontend attribution.
--
-- The forecast pipeline tags each spot's swell source in spots_enriched.json
-- (swell_window_source). Most spots are "orientation_derived" (the default);
-- the CDIP-MOP-fed spots are "cdip_mop". This adds a clean, queryable top-level
-- column so the frontend can render the required "Data courtesy of CDIP" credit
-- wherever swell_window_source = 'cdip_mop'.
--
-- Nullable; the default (orientation-derived) stays NULL — db_import writes only
-- the non-default source. Full verbatim provenance for every spot also remains
-- in the existing data_sources JSONB (data_sources->>'swell_window_source'), so
-- the frontend could read it there too without this column; this is the clean
-- flat field for that read + future filtering.
--
-- Run in the Supabase SQL editor (idempotent).

ALTER TABLE spots ADD COLUMN IF NOT EXISTS swell_window_source TEXT;

-- Optional — only if you'll filter on it (e.g. "list every CDIP-fed spot"):
-- CREATE INDEX IF NOT EXISTS idx_spots_swell_window_source
--   ON spots (swell_window_source) WHERE swell_window_source IS NOT NULL;
