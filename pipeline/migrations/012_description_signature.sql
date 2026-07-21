-- 012: description staleness signature.
--
-- Spot descriptions are generated once (pipeline.generate_descriptions, a MANUAL/one-off script that is
-- NOT wired into the forecast pipeline) and never regenerated automatically. So a later change to a
-- spot's coordinates, state, or orientation_deg silently strands the description: production has spots
-- described as "in California" that are now in Hawaii/New Jersey, and "faces south (202°)" where the
-- orientation is now 115.
--
-- This column stores a short hash of the fields a description asserts (lat, lng, state, orientation_deg
-- — see db_import.description_signature). On every full-pipeline run db_import blanks spots.description
-- whenever the current signature no longer matches the stored one, so a self-contradicting description
-- cannot persist; pipeline.generate_descriptions regenerates blanked/mismatched spots and re-stamps the
-- signature. A transient null beats a confidently-wrong description.
ALTER TABLE spots ADD COLUMN IF NOT EXISTS description_signature text;

COMMENT ON COLUMN spots.description_signature IS
  'Hash of (lat,lng,state,orientation_deg) the description was written against (db_import.'
  'description_signature). db_import NULLs spots.description when the current signature differs; '
  'generate_descriptions backfills. Guards against write-once descriptions going stale after a '
  'coordinate/state/orientation change.';
