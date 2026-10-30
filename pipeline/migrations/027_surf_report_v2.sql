-- Migration 027 — surf reports, question version 2 (#282): "How big were most waves?", the
-- optional sets answer, and the forecast as the page showed it.
--
-- WHY. Version 1 asked "How big was it? — the average of the sets you rode". The site's height
-- is closer to most of the bigger waves than to the sets, so those answers measure a different
-- statistic from the one we publish. Version 2 asks "How big were most waves?" (size_bucket, the
-- same body scale) and, optionally, "How big were the sets?" (sets_bucket). The two versions are
-- NEVER pooled: every analysis splits on question_version.
--
-- WHAT THIS ADDS, all to surf_reports:
--   question_version     1 or 2. Every row already in the table answered version 1 and reads 1;
--                        the new card writes 2.
--   sets_bucket          version 2's optional sets answer, the same body scale; NULL when skipped
--                        and always NULL on version 1.
--   forecast_band        the Surf height tile's range for the snapshotted height ("3-4ft"), as
--                        the page showed it. The ladder is a display convention and may change.
--   forecast_basis       what that height was: calibrated, cdip or model (lib/heightBasis.ts).
--   forecast_nwps_cycle  the NWPS cycle the hour was rated from (forecasts.nwps_cycle).
-- Like forecast_face_ft and forecast_stars, the three forecast_* columns are taken at the FIRST
-- report and never re-taken on a revision (015's comments say why). They are NULL on rows from
-- before this migration, and on any report of an hour we held no forecast row for.
--
-- NOTHING NEW ABOUT THE VISITOR. No column here describes the reporter. The route's abuse
-- controls (a reporter hash keyed on the observed day, 30 new reports per hash, a hidden bot
-- field) store nothing beyond the existing reporter_hash.
--
-- ORDER OF OPERATIONS — RUN THIS MIGRATION FIRST, THEN DEPLOY THE CARD. A route that sends
-- question_version before the column exists fails every report with a missing-column error.
-- Applied first, the old route keeps working: its inserts take question_version's default, 1,
-- which is the question it asks.
--
-- IDEMPOTENT. Columns are added IF NOT EXISTS and each check is dropped and re-added.
--
-- CHECK QUERY 1 — run right after applying. Every row must read ok = true.
-- pipeline/tests/test_surf_report_v2_migration.py runs this exact text against a table shaped
-- like the live one:
--
--   SELECT check_name, expected, actual, expected = actual AS ok
--     FROM (VALUES
--       ('columns added',                      '5',
--        (SELECT count(*) FROM information_schema.columns
--          WHERE table_schema = 'public' AND table_name = 'surf_reports'
--            AND column_name IN ('question_version', 'sets_bucket', 'forecast_band',
--                                'forecast_basis', 'forecast_nwps_cycle'))::text),
--       ('question_version is smallint, not null, default 1', 'smallint NO 1',
--        (SELECT data_type || ' ' || is_nullable || ' ' || column_default
--           FROM information_schema.columns
--          WHERE table_schema = 'public' AND table_name = 'surf_reports'
--            AND column_name = 'question_version')),
--       ('checks in place',                    '4',
--        (SELECT count(*) FROM pg_constraint
--          WHERE conrelid = 'public.surf_reports'::regclass
--            AND conname IN ('surf_reports_question_version_check',
--                            'surf_reports_sets_bucket_check',
--                            'surf_reports_sets_only_v2_check',
--                            'surf_reports_forecast_basis_check'))::text),
--       ('every report so far reads version 1',
--        (SELECT count(*) FROM surf_reports)::text,
--        (SELECT count(*) FROM surf_reports WHERE question_version = 1)::text),
--       ('no version 1 row has a sets answer', '0',
--        (SELECT count(*) FROM surf_reports
--          WHERE question_version = 1 AND sets_bucket IS NOT NULL)::text)
--     ) AS c(check_name, expected, actual);
--
-- CHECK QUERY 2 — after a test report from the preview, or any time. One row per question
-- version, with how many reports it has, how many carry a sets answer and a forecast snapshot,
-- and the latest. Expect a version 1 row for the reports already in the table (none, if there
-- are none) and, once the card has been used, a version 2 row.
--
--   SELECT question_version, count(*) AS reports,
--          count(sets_bucket) AS with_sets,
--          count(forecast_band) AS with_forecast_band,
--          max(first_reported_at) AS latest
--     FROM surf_reports
--    GROUP BY question_version
--    ORDER BY question_version;
--
-- Run in the Supabase SQL editor.

ALTER TABLE surf_reports ADD COLUMN IF NOT EXISTS question_version SMALLINT NOT NULL DEFAULT 1;
ALTER TABLE surf_reports ADD COLUMN IF NOT EXISTS sets_bucket TEXT;
ALTER TABLE surf_reports ADD COLUMN IF NOT EXISTS forecast_band TEXT;
ALTER TABLE surf_reports ADD COLUMN IF NOT EXISTS forecast_basis TEXT;
ALTER TABLE surf_reports ADD COLUMN IF NOT EXISTS forecast_nwps_cycle TIMESTAMPTZ;

ALTER TABLE surf_reports DROP CONSTRAINT IF EXISTS surf_reports_question_version_check;
ALTER TABLE surf_reports ADD CONSTRAINT surf_reports_question_version_check
  CHECK (question_version IN (1, 2));

ALTER TABLE surf_reports DROP CONSTRAINT IF EXISTS surf_reports_sets_bucket_check;
ALTER TABLE surf_reports ADD CONSTRAINT surf_reports_sets_bucket_check CHECK (
  sets_bucket IS NULL OR sets_bucket IN (
    'ankle', 'knee', 'thigh', 'waist', 'chest', 'shoulder', 'head',
    'overhead', 'well_overhead', 'double_overhead', 'triple_overhead_plus'
  ));

ALTER TABLE surf_reports DROP CONSTRAINT IF EXISTS surf_reports_sets_only_v2_check;
ALTER TABLE surf_reports ADD CONSTRAINT surf_reports_sets_only_v2_check
  CHECK (question_version = 2 OR sets_bucket IS NULL);

ALTER TABLE surf_reports DROP CONSTRAINT IF EXISTS surf_reports_forecast_basis_check;
ALTER TABLE surf_reports ADD CONSTRAINT surf_reports_forecast_basis_check
  CHECK (forecast_basis IS NULL OR forecast_basis IN ('calibrated', 'cdip', 'model'));

COMMENT ON COLUMN surf_reports.question_version IS
  'Which question the answer replied to. 1: "How big was it? — the average of the sets you '
  'rode" (every row before migration 027). 2: "How big were most waves?" in size_bucket, with '
  'the optional "How big were the sets?" in sets_bucket. Never pool the two.';
COMMENT ON COLUMN surf_reports.sets_bucket IS
  'Version 2''s optional "How big were the sets?", on the same body scale as size_bucket. NULL '
  'when skipped; always NULL on version 1.';
COMMENT ON COLUMN surf_reports.forecast_band IS
  'The Surf height tile''s range for forecast_face_ft as the page showed it ("3-4ft", '
  'frontend/lib/formatting.ts fmtHeightRange), taken at the first report. NULL with no height.';
COMMENT ON COLUMN surf_reports.forecast_basis IS
  'What forecast_face_ft was: calibrated, cdip or model (frontend/lib/heightBasis.ts), taken at '
  'the first report, so a comparison never pools calibrated and model heights.';
COMMENT ON COLUMN surf_reports.forecast_nwps_cycle IS
  'forecasts.nwps_cycle for the reported hour at the first report: the NWPS cycle it was rated '
  'from.';
