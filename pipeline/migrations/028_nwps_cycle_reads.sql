-- 028_nwps_cycle_reads.sql
-- One row per NWPS cycle the pipeline read: what NOMADS's GRIB filter returned (its size,
-- SHA-256 and response headers) and what a HEAD on the full CG1 file said just after.
--
-- WHY. An office can post a cycle twice, and NOMADS then serves either copy from the same URL
-- for a day or more (MFR 2026-11-07 12Z: 55,879,399 bytes last modified 19:07:09Z, and
-- 43,017,313 bytes last modified 20:01:05Z). Nothing recorded which copy a published forecast
-- came from. A HEAD can reach a different NOMADS server than the filter request (run 70: a
-- download and a HEAD seconds apart got the two copies), so subset_sha256 is the record of
-- what was read and the file_* columns are best-effort context.
--
-- Written by db_import (pipeline/forecast_data/nwps_reads.json from nwps.fetch), last in the
-- run. Apply BEFORE merging the code that writes it. Idempotent; run in the Supabase SQL editor.
--
-- CHECK 1, right after applying: 11 rows, one per column.
--   SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_schema = 'public' AND table_name = 'nwps_cycle_reads'
--   ORDER BY ordinal_position;
--
-- CHECK 2, after the first full pipeline run with the code: one row per office and cycle read.
--   SELECT wfo, nwps_cycle, count(*) AS reads, count(DISTINCT subset_sha256) AS copies_read,
--          min(read_at) AS first_read, max(read_at) AS last_read
--   FROM nwps_cycle_reads GROUP BY wfo, nwps_cycle ORDER BY last_read DESC, wfo LIMIT 40;

CREATE TABLE IF NOT EXISTS nwps_cycle_reads (
  id                 BIGSERIAL PRIMARY KEY,
  wfo                TEXT        NOT NULL,
  nwps_cycle         TIMESTAMPTZ NOT NULL,
  read_at            TIMESTAMPTZ NOT NULL,
  run_id             TEXT,
  subset_bytes       BIGINT      NOT NULL,
  subset_sha256      TEXT        NOT NULL,
  filter_headers     JSONB,
  file_last_modified TIMESTAMPTZ,
  file_bytes         BIGINT,
  head_error         TEXT,
  CONSTRAINT nwps_cycle_reads_unique UNIQUE (wfo, nwps_cycle, read_at)
);

CREATE INDEX IF NOT EXISTS idx_nwps_cycle_reads_cycle ON nwps_cycle_reads (wfo, nwps_cycle);

ALTER TABLE nwps_cycle_reads ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE nwps_cycle_reads IS
  'Each NWPS cycle the pipeline read, per run: the GRIB filter subset''s size and SHA-256 '
  '(what was read) and a HEAD on the full file (may reach another NOMADS server). Joins to '
  'forecasts on (spots.nwps_wfo, forecasts.nwps_cycle); a published row came from the latest '
  'read of its cycle. Written by db_import with the service role; RLS on with no policies, so '
  'anon and authenticated read nothing.';
