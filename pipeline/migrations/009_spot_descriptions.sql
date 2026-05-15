-- 009_spot_descriptions.sql — short factual blurb per spot, generated
-- once by pipeline.generate_descriptions from the enriched metadata.
-- (The spec called this 008 but 008 was already taken by
-- cam_display_mode; bumping to 009 keeps the sequential numbering.)
--
-- Manual edits are expected over time — surfers will spot wrong
-- claims and the file/SQL can be patched directly.

ALTER TABLE spots
  ADD COLUMN IF NOT EXISTS description TEXT;

COMMENT ON COLUMN spots.description IS
  'Short 2-3 sentence factual blurb. Seeded by '
  'pipeline.generate_descriptions, edited manually thereafter.';
