-- Migration 025 — rename two spots IN PLACE: Indiatlantic -> Indialantic, Ventor Pier -> Ventnor Pier.
-- (024 is held for dropping forecasts.face_lo_ft / face_hi_ft, retired by 023.)
--
-- Both names were misspelt in the roster (pipeline/data/llm_spots.json). The slug is derived from
-- the name, so the fix moves each spot to a new slug: indiatlantic -> indialantic and
-- ventor-pier -> ventnor-pier. The site redirects the old URLs (frontend/next.config.js).
--
-- WHY IN PLACE, AND NOT THE EXCLUDED-NAME ROUTE THE 2026 CLEANUP USED. db_import upserts spots
-- on slug, so the renamed roster alone would insert a second row under the new slug and leave
-- the old one behind. Listing the old names in excluded_spots.json deletes the old row, and
-- surf_reports (014) cascade with it: every report a surfer filed at either spot would go.
-- Renaming the row keeps its id, so its forecasts, its surf reports and any cam stay attached.
--
-- WHAT IT ALSO WRITES. The description and its v2 signature. The signature hashes the spot's
-- name (pipeline/descriptions.py, PAYLOAD_INPUTS), so a renamed row keeping the old pair would
-- have its description blanked by the next import (description_blank_reason). The two texts and
-- signatures are exactly what render() and description_signature() give for the new names, and
-- are the entries now in pipeline/data/spot_descriptions.json.
--
-- CAMS. cams.spot_slug references spots(slug) with no ON UPDATE, so a cam on either spot would
-- block the rename. Any such cam is unhooked (spot_slug NULL, which the column admits) and
-- re-pointed at the new slug after the rename, in the same transaction.
--
-- ORDER: MERGE THE PR, THEN RUN THIS, before the next pipeline run if you can. If a run on the
-- renamed roster gets in first, it inserts the second row under the new slug; this migration
-- then moves any surf report filed on that row to the original (skipping one that would repeat
-- a report already there), deletes it (its forecasts are rewritten by the next run anyway), and
-- renames the original. Running it BEFORE the merge is the wrong order: the deployed pipeline
-- would recreate the old slug and leave a stale row behind.
--
-- Idempotent: once both spots are renamed every statement matches nothing.
-- Run in the Supabase SQL editor.
--
-- CHECK QUERY (expect two rows, the new slugs, and nothing under the old ones):
--   SELECT id, slug, name, description_signature FROM spots
--   WHERE slug IN ('indiatlantic', 'indialantic', 'ventor-pier', 'ventnor-pier') ORDER BY slug;

BEGIN;

CREATE TEMP TABLE _spot_renames (
  old_slug TEXT PRIMARY KEY,
  new_slug TEXT NOT NULL,
  new_name TEXT NOT NULL,
  new_description TEXT NOT NULL,
  new_signature TEXT NOT NULL
) ON COMMIT DROP;

INSERT INTO _spot_renames VALUES
  ('indiatlantic', 'indialantic', 'Indialantic',
   'Indialantic is a surf spot in Florida. It faces east, and its optimal swell direction is east. Offshore winds blow from the west.',
   'v2:f6bfe509bd2e9a55'),
  ('ventor-pier', 'ventnor-pier', 'Ventnor Pier',
   'Facing southeast, Ventnor Pier is a beach break in New Jersey. The optimal swell comes from the southeast, and the offshore wind is from the northwest.',
   'v2:d3366e883d461d37');

-- 1. Unhook any cam on either slug; re-pointed in step 4.
CREATE TEMP TABLE _spot_rename_cams ON COMMIT DROP AS
  SELECT c.id, r.new_slug
  FROM cams c JOIN _spot_renames r ON c.spot_slug IN (r.old_slug, r.new_slug);
UPDATE cams SET spot_slug = NULL WHERE id IN (SELECT id FROM _spot_rename_cams);

-- 2. A second row under the new slug (a pipeline run got in first): move its surf reports to the
--    original row, then delete it. Only when the original still exists to take them.
UPDATE surf_reports s
SET spot_id = o.id
FROM _spot_renames r
JOIN spots n ON n.slug = r.new_slug
JOIN spots o ON o.slug = r.old_slug
WHERE s.spot_id = n.id
  AND NOT EXISTS (SELECT 1 FROM surf_reports d
                  WHERE d.spot_id = o.id AND d.observed_hour = s.observed_hour
                    AND d.reporter_hash = s.reporter_hash);

DELETE FROM spots n
USING _spot_renames r, spots o
WHERE n.slug = r.new_slug AND o.slug = r.old_slug;

-- 3. The rename itself: same id, new slug, name, description and signature.
UPDATE spots s
SET slug = r.new_slug, name = r.new_name,
    description = r.new_description, description_signature = r.new_signature
FROM _spot_renames r
WHERE s.slug = r.old_slug;

-- 4. Cams back on, at the new slug.
UPDATE cams c SET spot_slug = k.new_slug FROM _spot_rename_cams k WHERE c.id = k.id;

COMMIT;
