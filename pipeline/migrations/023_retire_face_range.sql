-- Migration 023 — retire the p25/p75 swell-height band. COMMENTS ONLY; nothing is dropped.
--
-- WHAT 016 ADDED. face_lo_ft / face_hi_ft carried a band divided out of each calibrated spot's
-- measured p25/p75 face ratio. Only the 141 calibrated California spots had one; every other
-- spot published a bare number. So a range on the site meant "half the measured hours fell
-- in here" at some spots and nothing at all at the rest.
--
-- WHAT REPLACES IT. The site prints one fixed ladder of standard ranges, computed on the page
-- from face_ft (frontend/lib/formatting.ts, fmtHeightRange):
--
--     under 1 · 1-2 · 2-3 · 3-4 · 4-5 · 5-6 · 6-8 · 8-10 · 10-12 · 12-15 · 15-20 · 20-25 ·
--     25-30, then 5 ft steps; a height exactly on a boundary reads as the higher step.
--
-- It is a reading range, the standard step the height falls in, and means the same at every
-- spot. Nothing about it is stored.
--
-- WHY THE COLUMNS STAY FOR NOW. pipeline/db_import.py writes an explicit NULL to both from
-- this change on (leaving the keys out would let the upsert keep the band an earlier run
-- wrote for the same hour). Dropping them while a deployed importer still names them would
-- fail the whole forecasts upsert with PGRST204, so the drop waits for migration 024, after
-- this code is live and the preflight (pipeline/tests/test_column_preflight.py) no longer
-- lists them.
--
-- The factor file's p25/p75 are untouched: build_face_factors still excludes a spot on
-- p75/p25 > FACE_FACTOR_MAX_IQR_RATIO and diff_face_factors still tracks their drift.
--
-- Run in the Supabase SQL editor (idempotent: COMMENT ON replaces).

COMMENT ON COLUMN forecasts.face_lo_ft IS
  'RETIRED by migration 023; migration 024 drops it. Written NULL by every import since. '
  'Was the low end of the p25/p75 swell-height band at calibrated spots (migration 016). The '
  'site now prints the fixed ladder step face_ft falls in (frontend/lib/formatting.ts, '
  'fmtHeightRange) at every spot. Do not read it.';
COMMENT ON COLUMN forecasts.face_hi_ft IS
  'RETIRED by migration 023; migration 024 drops it. Written NULL by every import since. '
  'Was the high end of the p25/p75 swell-height band at calibrated spots (migration 016). The '
  'site now prints the fixed ladder step face_ft falls in (frontend/lib/formatting.ts, '
  'fmtHeightRange) at every spot. Do not read it.';
