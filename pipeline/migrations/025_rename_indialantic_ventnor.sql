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
-- are the entries in pipeline/data/spot_descriptions.json.
--
-- ONE STATEMENT. The whole migration is a single DO block, which Postgres runs atomically as one
-- statement however the client sends it. The first version used a BEGIN ... COMMIT block and two
-- temp tables declared ON COMMIT DROP; pasted into the Supabase SQL editor on 2026-10-21 it
-- applied the rename once and, on another execution, failed with
--   ERROR: 42P01: relation "_spot_renames" does not exist
-- because a temp table dropped at the end of one transaction is gone for the next statement.
-- Nothing here depends on state surviving between statements.
--
-- CAMS. cams.spot_slug references spots(slug) with no ON UPDATE, so a cam on either spot would
-- block the rename. Any such cam is unhooked (spot_slug NULL, which the column admits) and
-- re-pointed at the new slug after the rename, inside the same statement.
--
-- ORDER: MERGE THE PR, THEN RUN THIS. If a pipeline run on the renamed roster gets in first, it
-- inserts a second row under the new slug; this moves any surf report filed on that row to the
-- original (skipping one that would repeat a report already there), deletes it (its forecasts
-- are rewritten by the next run), and renames the original.
--
-- SAFE TO RE-RUN. Once a spot is renamed, its old slug matches nothing. The description is
-- written again only where the row still carries the OLD signature or none, so a re-run never
-- overwrites a newer description the generator has written since.
--
-- CHECK QUERY (read-only; expect the two new slugs with their original ids, created before the
-- rename, signature_matches_roster true, and nothing under the old slugs):
--   WITH want(slug, renamed_from, roster_signature) AS (VALUES
--     ('indialantic',  'indiatlantic', 'v2:f6bfe509bd2e9a55'),
--     ('ventnor-pier', 'ventor-pier',  'v2:d3366e883d461d37'),
--     ('indiatlantic', NULL, NULL),
--     ('ventor-pier',  NULL, NULL))
--   SELECT w.slug, s.id, s.name, s.created_at,
--          s.description IS NOT NULL AS has_description,
--          s.description_signature = w.roster_signature AS signature_matches_roster,
--          (SELECT count(*) FROM forecasts f WHERE f.spot_id = s.id) AS forecast_rows,
--          (SELECT min(f.valid_time) FROM forecasts f WHERE f.spot_id = s.id) AS earliest_forecast,
--          (SELECT count(*) FROM surf_reports r WHERE r.spot_id = s.id) AS surf_reports,
--          (SELECT string_agg(c.id::text || ' ' || c.cam_name || ' -> ' || c.spot_slug, '; ' ORDER BY c.id)
--             FROM cams c WHERE c.spot_slug IN (w.slug, w.renamed_from)) AS cams_by_old_or_new_slug,
--          (SELECT count(*) FROM cams c WHERE c.spot_slug IS NULL) AS cams_with_no_spot
--   FROM want w LEFT JOIN spots s ON s.slug = w.slug
--   ORDER BY w.slug;

DO $migration_025$
DECLARE
  r RECORD;
  old_id INTEGER;
  new_id INTEGER;
  cam_ids INTEGER[];
BEGIN
  FOR r IN
    SELECT * FROM (VALUES
      ('indiatlantic', 'indialantic', 'Indialantic',
       'Indialantic is a surf spot in Florida. It faces east, and its optimal swell direction is east. Offshore winds blow from the west.',
       'v2:f6bfe509bd2e9a55', 'v2:6afa2725115ca62f'),
      ('ventor-pier', 'ventnor-pier', 'Ventnor Pier',
       'Facing southeast, Ventnor Pier is a beach break in New Jersey. The optimal swell comes from the southeast, and the offshore wind is from the northwest.',
       'v2:d3366e883d461d37', 'v2:a7d8dfda534b4b61')
    ) AS v(old_slug, new_slug, new_name, new_description, new_signature, old_signature)
  LOOP
    SELECT id INTO old_id FROM spots WHERE slug = r.old_slug;
    SELECT id INTO new_id FROM spots WHERE slug = r.new_slug;

    IF old_id IS NOT NULL THEN
      -- 1. Unhook any cam on either slug; re-pointed in step 4.
      SELECT coalesce(array_agg(id), '{}') INTO cam_ids
        FROM cams WHERE spot_slug IN (r.old_slug, r.new_slug);
      UPDATE cams SET spot_slug = NULL WHERE id = ANY (cam_ids);

      -- 2. A second row under the new slug (a pipeline run got in first): its surf reports go
      --    to the original row, then the row goes.
      IF new_id IS NOT NULL THEN
        UPDATE surf_reports s SET spot_id = old_id
         WHERE s.spot_id = new_id
           AND NOT EXISTS (SELECT 1 FROM surf_reports d
                            WHERE d.spot_id = old_id AND d.observed_hour = s.observed_hour
                              AND d.reporter_hash = s.reporter_hash);
        DELETE FROM spots WHERE id = new_id;
      END IF;

      -- 3. The rename itself: same id, new slug, name, description and signature.
      UPDATE spots
         SET slug = r.new_slug, name = r.new_name,
             description = r.new_description, description_signature = r.new_signature
       WHERE id = old_id;

      -- 4. Cams back on, at the new slug.
      UPDATE cams SET spot_slug = r.new_slug WHERE id = ANY (cam_ids);

    ELSIF new_id IS NOT NULL THEN
      -- Already renamed. Write the name, and the description only where the row still carries
      -- the old signature or none, so a newer generated description is never overwritten.
      UPDATE spots SET name = r.new_name WHERE id = new_id AND name IS DISTINCT FROM r.new_name;
      UPDATE spots
         SET description = r.new_description, description_signature = r.new_signature
       WHERE id = new_id
         AND (description_signature IS NULL OR description_signature = r.old_signature
              OR description IS NULL);
    END IF;
  END LOOP;
END
$migration_025$;
