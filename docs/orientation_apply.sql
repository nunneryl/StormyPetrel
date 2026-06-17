-- Orientation apply pass — production database steps.
-- Generated 2026-06-16 alongside branch claude/orientation-apply.
--
-- This script handles ONLY the deletion. The 664 orientation overrides
-- and 133 coord refinements take effect through `db_import` (which the
-- pipeline workflow runs after you merge), and are also pre-applied to
-- spots_enriched.json on the branch so the very next import upserts
-- the new values.
--
-- The one row to drop: la-ocho. Manifest flagged it as a bogus spot
-- sitting ~9 km inland in Puerto Rico. The corresponding name "La Ocho"
-- has also been added to excluded_spots.json under reason
-- `manual_cleanup_3` so future seed runs can't re-add it.
--
-- Cam FK pre-check (already done at branch time):
--   pipeline/data/cam_seed.json has zero references to 'la-ocho'.
--   The cams.spot_slug -> spots.slug FK is ON DELETE NO ACTION, so a
--   stray cam (if any landed via an out-of-tree manual insert) would
--   abort the DELETE. The SELECT below confirms the live state before
--   the DELETE runs; if it returns rows, ROLLBACK and reassign them
--   before retrying.
--
-- forecasts.spot_id -> spots.id is ON DELETE CASCADE, so forecast rows
-- for la-ocho clear automatically with the spot deletion.

BEGIN;

-- 1) Cam FK pre-check. Expect zero rows.
SELECT id, cam_name, provider, status
FROM cams
WHERE spot_slug = 'la-ocho';

-- 2) Drop the bogus row.
DELETE FROM spots WHERE slug = 'la-ocho';

-- 3) Confirm the deletion landed. Expect zero rows.
SELECT slug, name FROM spots WHERE slug = 'la-ocho';

COMMIT;
