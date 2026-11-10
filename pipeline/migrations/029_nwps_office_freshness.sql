-- 029_nwps_office_freshness.sql
-- One row per NWPS office per pipeline run, offices that read no cycle included: the newest
-- cycle NOMADS listed, the cycle read, how far behind and how old it was, and the newer
-- cycles that failed and why.
--
-- WHY. NWPS v1.5 (NWS SCN 26-91) renamed every file from the 2026-11-09 12Z run. From the
-- 23:23Z run that day, every full run read cycles up to 30 h behind or none (6 offices in the
-- 11-10 08:15Z run), and nothing recorded it but log lines. nwps_cycle_reads (migration 028)
-- has a row only for a cycle that was read, so an office that read nothing was invisible.
--
-- Written by db_import (pipeline/forecast_data/nwps_freshness.json from nwps.fetch), last in
-- the run. Apply BEFORE merging the code that writes it. Idempotent; run in the Supabase SQL
-- editor.
--
-- CHECK 1, right after applying: 14 rows, one per column.
--   SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_schema = 'public' AND table_name = 'nwps_office_freshness'
--   ORDER BY ordinal_position;
--
-- CHECK 2, after the first full pipeline run with the code: one row per run, with the number
-- of offices, how many read nothing or were stale, and the oldest cycle read.
--   SELECT fetched_at, run_id, count(*) AS offices,
--          count(*) FILTER (WHERE cycle_used IS NULL) AS no_cycle,
--          count(*) FILTER (WHERE stale_reason IS NOT NULL) AS stale,
--          max(hours_behind) AS most_hours_behind, max(age_h) AS oldest_cycle_h
--   FROM nwps_office_freshness GROUP BY fetched_at, run_id
--   ORDER BY fetched_at DESC LIMIT 10;

CREATE TABLE IF NOT EXISTS nwps_office_freshness (
  id             BIGSERIAL PRIMARY KEY,
  fetched_at     TIMESTAMPTZ NOT NULL,
  wfo            TEXT        NOT NULL,
  run_id         TEXT,
  listing        TEXT        NOT NULL CHECK (listing IN ('ok', 'empty', 'unanswered')),
  newest_listed  TIMESTAMPTZ,
  cycle_used     TIMESTAMPTZ,
  source         TEXT        CHECK (source IN ('download', 'cache')),
  hours_behind   REAL,
  age_h          REAL,
  failed         JSONB       NOT NULL DEFAULT '[]'::jsonb,
  stale_reason   TEXT,
  spots          INTEGER     NOT NULL,
  spots_written  INTEGER     NOT NULL,
  CONSTRAINT nwps_office_freshness_unique UNIQUE (wfo, fetched_at)
);

CREATE INDEX IF NOT EXISTS idx_nwps_office_freshness_fetched
  ON nwps_office_freshness (fetched_at DESC);

ALTER TABLE nwps_office_freshness ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE nwps_office_freshness IS
  'One row per NWPS office per pipeline run (fetched_at = the fetch''s start, the same on every '
  'row of a run), offices that read no cycle included: newest cycle listed, cycle read, hours '
  'behind, age at fetch time, failed newer cycles, stale_reason (null when fresh). Written by '
  'db_import with the service role; RLS on with no policies, so anon and authenticated read '
  'nothing.';
