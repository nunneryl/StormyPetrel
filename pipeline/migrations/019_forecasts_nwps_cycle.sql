-- Migration 019 — record which NWPS cycle wrote each forecast row, and never let an older
-- cycle overwrite a newer one.
--
-- WHAT WENT WRONG. forecasts is UNIQUE(spot_id, valid_time, source) and db_import upserts over
-- every hour a run covers, with no condition. So whatever a run brings wins, even when it is
-- OLDER than what the table already holds. In the 7 days to 2026-09-23 that happened six times:
--
--   * sju, four times (09-17, 09-18, 09-21, 09-22). Each time the listing of today's sju folder
--     timed out; the fetcher read the failure as "no cycle yet today" and fell back to
--     yesterday's 18Z. That older cycle then replaced the newer one for about five days of sju's
--     forecast, leaving a seam where its f144 ended.
--   * ilm, twice (09-16). A listed cycle answered 404 on download and the fetcher took the
--     cycle before it.
--
-- Nothing recorded which cycle a row came from, so none of it was visible in the data: the
-- furthest forecast hour never moved backwards, only the content of the overlapping hours did.
--
-- THE RULE: an 'nwps' row is only ever overwritten by the same cycle or a newer one. It lives
-- HERE, in the database, rather than in the fetcher, so that no code path can break it — not a
-- failed listing, not a download that falls back, not a replayed artifact, not a manual run,
-- and not a LEGITIMATE fallback to yesterday either. The fetcher is fixed separately; this is
-- what makes the rule hold whatever the fetcher does.
--
--   * A write from an older cycle is skipped, row by row. BEFORE UPDATE returning NULL is how
--     Postgres skips one row of an INSERT ... ON CONFLICT DO UPDATE — the rest of the statement
--     proceeds, and the skipped row is simply absent from RETURNING, which is what PostgREST
--     reports back. db_import counts those as "kept a newer cycle", not as an error.
--   * The same cycle may always rewrite its own rows. Re-rating one cycle with a newer WW3 or
--     HRRR join, a new tide, or a new face correction is normal and must keep working.
--   * A write that does not say its cycle (NULL) cannot overwrite a row that does. Once a row
--     is stamped, only a stamped cycle at least as new can replace it.
--   * Rows written before this migration have NULL here and accept the first stamped write,
--     which is how every existing row gets its cycle. There is no backfill: the cycle of an
--     existing row is not recorded anywhere to backfill it from.
--   * Other sources (ecmwf_wam writes source='ecmwf') are untouched.
--
-- ORDER OF OPERATIONS — READ THIS BEFORE MERGING THE CODE:
--
--     RUN THIS MIGRATION FIRST, THEN MERGE.
--
-- db_import sends every key in its record dict to PostgREST in one bulk upsert. If the code
-- that sends nwps_cycle ships before this column exists, PGRST204 ("column not found") fails
-- the whole forecasts upsert and the run publishes NO forecast rows at all. The reverse order
-- is harmless: the column sits NULL and the trigger lets every write through until the first
-- run stamps it.
--
-- Idempotent. Run in the Supabase SQL editor.

ALTER TABLE forecasts
  ADD COLUMN IF NOT EXISTS nwps_cycle TIMESTAMPTZ;

COMMENT ON COLUMN forecasts.nwps_cycle IS
  'Nominal time of the NWPS cycle whose run wrote this row (e.g. 2026-09-22 12:00+00 for the '
  '12Z cycle of 09-22), stamped by pipeline/forecast/nwps.py on every hour it extracts and '
  'carried through interpret and db_import. Trigger forecasts_keep_newest_nwps_cycle refuses '
  'to overwrite a stamped nwps row with an older or unstamped cycle. NULL: written before '
  'migration 019, cycle unknown.';

CREATE OR REPLACE FUNCTION forecasts_keep_newest_nwps_cycle() RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
  IF NEW.source = 'nwps'
     AND OLD.nwps_cycle IS NOT NULL
     AND (NEW.nwps_cycle IS NULL OR NEW.nwps_cycle < OLD.nwps_cycle) THEN
    RETURN NULL;   -- keep the row as it is: a newer cycle already wrote it
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS forecasts_keep_newest_nwps_cycle ON forecasts;
CREATE TRIGGER forecasts_keep_newest_nwps_cycle
  BEFORE UPDATE ON forecasts
  FOR EACH ROW EXECUTE FUNCTION forecasts_keep_newest_nwps_cycle();
