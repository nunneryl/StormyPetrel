-- Spot-delete workflow — one-time cams FK alter.
-- Generated alongside branch `claude/spot-delete-workflow`.
--
-- Why this exists
-- ---------------
-- `pipeline/migrations/007_cams.sql` defined `cams.spot_slug` as an inline
-- FK to `spots(slug)` with no `ON DELETE` clause. Postgres defaults inline
-- FKs to `ON DELETE NO ACTION`, so `DELETE FROM spots WHERE slug = ?`
-- aborts whenever any cam still references that slug. The only way to
-- delete a spot today is to manually reassign or null its cams first,
-- then run the DELETE. That's the pain that made the La Ocho cleanup
-- need a hand-written SQL script.
--
-- After this alter, deleting a spot orphans its cam(s) by setting
-- `cams.spot_slug` to NULL. The cam row itself stays — useful for later
-- reassignment (e.g. a new spot at the same beach can adopt the cam).
-- Combined with the `db_import` deletion pass added on the same branch,
-- the new routine is just: add a name to `excluded_spots.json`, run the
-- pipeline, done.
--
-- Postgres autonames inline FKs `<table>_<column>_fkey`, so the
-- constraint on this column is `cams_spot_slug_fkey`. The diagnostic
-- SELECT confirms the current rule (NO ACTION before, SET NULL after).
-- If the constraint name differs for any reason — surface it via the
-- SELECT and substitute below before running the ALTER.

BEGIN;

-- 1) Confirm current state. Expect: constraint_name = 'cams_spot_slug_fkey',
--    delete_rule = 'NO ACTION'.
SELECT
  rc.constraint_name,
  rc.delete_rule
FROM information_schema.referential_constraints rc
JOIN information_schema.table_constraints tc
  ON tc.constraint_name = rc.constraint_name
WHERE tc.table_name = 'cams'
  AND tc.constraint_type = 'FOREIGN KEY'
  AND rc.constraint_name LIKE '%spot_slug%';

-- 2) Drop the old FK and re-add it with ON DELETE SET NULL.
ALTER TABLE cams DROP CONSTRAINT cams_spot_slug_fkey;

-- 2a) Allow NULLs in spot_slug. Without this, ON DELETE SET NULL would
--     fail at delete-time with a NOT NULL violation as soon as the first
--     cam tries to orphan. The column was declared without NOT NULL in
--     007_cams.sql but a later migration / manual ALTER may have added
--     it; this DROP NOT NULL is idempotent if the column was already
--     nullable.
ALTER TABLE cams ALTER COLUMN spot_slug DROP NOT NULL;

ALTER TABLE cams
  ADD CONSTRAINT cams_spot_slug_fkey
  FOREIGN KEY (spot_slug)
  REFERENCES spots(slug)
  ON DELETE SET NULL;

-- 3) Re-confirm. Expect: delete_rule = 'SET NULL'.
SELECT
  rc.constraint_name,
  rc.delete_rule
FROM information_schema.referential_constraints rc
JOIN information_schema.table_constraints tc
  ON tc.constraint_name = rc.constraint_name
WHERE tc.table_name = 'cams'
  AND tc.constraint_type = 'FOREIGN KEY'
  AND rc.constraint_name LIKE '%spot_slug%';

COMMIT;
