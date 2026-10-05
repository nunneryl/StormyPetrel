-- Migration 020 — record where each spot's break_type came from, fix the list of values it may
-- hold, and stop break_type_confidence describing nothing.
--
-- WHAT WENT WRONG. break_type is filled on 444 of the 646 spots and nothing says where any of
-- the 444 came from. Three writers set it, and none recorded itself:
--
--     enrich (Algo 3)       pipeline/enrichment/break_type.py answered 'beach' for every spot:
--                           both of its curvature thresholds were set to infinity, so its only
--                           other answer needed an infinite curvature to fire. It wrote
--                           break_type_confidence = 0.5 beside that answer on every run —
--                           including on the 173 spots whose break_type it was not allowed to
--                           touch.
--     verify_spots          an LLM pass with a web_search tool whose prompt told it NOT to
--                           search for break_type ("details you can reasonably infer"), showed
--                           it the computed 'beach', and said to return computed values
--                           unchanged when they looked reasonable.
--     scrape_surf_forecast  took the first of "beach break" / "reef break" / "point break" /
--                           "jetty break" found ANYWHERE in a surf-forecast.com page's text, so
--                           any page mentioning a beach break anywhere came out 'beach'.
--
-- verify_spots and the scraper overwrote break_type on 83 spots without touching the confidence
-- beside it. So break_type_confidence reads 0.5 on all 444 rows and describes nothing: not the
-- 271 'beach' values the algorithm wrote, and not the 173 the model checked. This change removes
-- the algorithm, makes every writer stamp a source in the same statement as the value, and
-- makes the database compute the confidence from that source.
--
-- WHAT THIS ADDS, mirroring 018:
--
--   break_type              a fixed list: beach, reef, point, jetty, rivermouth, unknown.
--                           'unknown' means someone looked and could not tell. NULL means
--                           nobody has looked. They are different answers and both are allowed.
--   break_type_source       where the value came from, ranked (pipeline/config.py
--                           BREAK_TYPE_SOURCE_RANK holds the same numbers; a lower rank may
--                           never overwrite a higher one, an equal rank may):
--                             reviewed           6  a person set it
--                             researched         5  a source was consulted and is cited
--                             scraped            4  read off the break's own surf-forecast.com page
--                             model_recall       3  a language model answered with no citation
--                             unattributed       2  held, but no writer recorded itself
--                             algorithm_default  1  an algorithm's fallback answer
--   value and source        both set or both NULL, enforced here, so a source can never outlive
--                           the value it describes — which is the 0.5 defect, in reverse.
--   break_type_source_url   the page cited for the value. researched and scraped must carry one.
--   break_type_evidence     a short quote from that page, at most 300 characters.
--   break_type_confidence   COMPUTED BY THE DATABASE from break_type_source: high for reviewed,
--                           medium for researched or scraped, low otherwise, NULL with no source.
--                           pipeline/config.py break_type_confidence() is the same mapping, and
--                           the writers use it to stamp spots_enriched.json in the same
--                           statement as the value; pipeline/tests/test_break_type_migration.py
--                           holds the two to one table.
--
-- WHY THE CONFIDENCE IS A GENERATED COLUMN. Two reasons, the second decisive.
--   1. A column no writer can set cannot drift from the source it is computed from. 0.5 drifted
--      from its value precisely because it was written by hand beside it.
--   2. It keeps the merge order safe. The old column is DOUBLE PRECISION. If db_import kept
--      sending a text confidence and the code merged before this migration ran, the spots upsert
--      would fail on the type, run_all would stop there, and no forecast row would be written —
--      the column preflight checks that columns exist, not their types. With the database
--      computing it, db_import never sends the column at all (it joins id, geom, created_at and
--      updated_at in db_import._DB_MANAGED_COLUMNS), so merging first can only leave the three
--      new columns missing — which the preflight names and leaves out, and the run ends failed
--      with forecasts published.
-- The 0.5 values are dropped with the old column. They carry no information to keep.
--
-- WHY THE CHECKS REJECT NOTHING TODAY. Derived from the committed roster
-- (pipeline/spots_enriched.json, its 646 rated spots), not from the live table, which this
-- change did not query; the pull request gives a pre-check that shows the live values before
-- anything is applied:
--
--     stored value   rated spots   in the list?
--     NULL                   202   allowed by the IS NULL branch
--     'beach'                361   yes
--     'reef'                  36   yes
--     'point'                 23   yes
--     'jetty'                 22   yes
--     'rivermouth'             2   yes
--                           ---
--                           646
--
-- No row has a URL or a quote yet, and the backfill gives every one of the 444 a source, so the
-- pairing and citation checks have nothing to reject either. Postgres runs a multi-statement
-- query as one implicit transaction, so if any check does find a value it rejects, nothing in
-- this file is applied.
--
-- THE BACKFILL CANNOT ATTRIBUTE WHAT IT SETS. The verification cache
-- (pipeline/data/spot_verification.json) and the scrape cache are both absent from the
-- repository, and the three writers ran in whatever order the operator chose. 'unattributed' is
-- the honest label for all 444 existing values, exactly as 018 used it for tide_preference: "a
-- value we hold and cannot source". The roster gets the same stamp in the commit this migration
-- accompanies.
--
-- NO DEFAULT ON THE NEW COLUMNS, DELIBERATELY. A new spot arrives with break_type and the four
-- columns beside it NULL, which reads correctly as "nobody has looked".
--
-- ORDER OF OPERATIONS — RUN THIS MIGRATION FIRST, THEN MERGE, after the check query below
-- returns ok on every row. Merged first, the run's column preflight finds break_type_source,
-- break_type_source_url and break_type_evidence missing, leaves them out of the upload and ends
-- the run failed, naming this file.
--
-- IDEMPOTENT. Constraints use 015's DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT, as 018 does, so
-- a re-run replaces them rather than leaving a stale one. The confidence column is replaced only
-- while it is not yet generated, and without CASCADE: if anything outside these migrations
-- depends on the old column, the drop fails loudly instead of taking that object with it.
--
-- CHECK QUERY — run after applying. Every row must read ok = true. The counts are the committed
-- roster's; pipeline/tests/test_break_type_migration.py runs this exact text against a database
-- shaped like it:
--
--   SELECT check_name, expected, actual, expected = actual AS ok
--     FROM (VALUES
--       ('spots',                            '646',
--        (SELECT count(*) FROM spots)::text),
--       ('spots with a break_type',          '444',
--        (SELECT count(*) FROM spots WHERE break_type IS NOT NULL)::text),
--       ('... stamped unattributed and low', '444',
--        (SELECT count(*) FROM spots
--          WHERE break_type IS NOT NULL
--            AND break_type_source = 'unattributed'
--            AND break_type_confidence = 'low')::text),
--       ('spots with no break_type',         '202',
--        (SELECT count(*) FROM spots
--          WHERE break_type IS NULL
--            AND break_type_source IS NULL
--            AND break_type_confidence IS NULL)::text),
--       ('rows with a URL or a quote',       '0',
--        (SELECT count(*) FROM spots
--          WHERE break_type_source_url IS NOT NULL
--             OR break_type_evidence IS NOT NULL)::text),
--       ('break_type_confidence generated',  'ALWAYS',
--        (SELECT is_generated::text FROM information_schema.columns
--          WHERE table_schema = 'public' AND table_name = 'spots'
--            AND column_name = 'break_type_confidence')),
--       ('break_type checks in place',       '6',
--        (SELECT count(*) FROM pg_constraint
--          WHERE conrelid = 'public.spots'::regclass
--            AND conname IN ('spots_break_type_check',
--                            'spots_break_type_source_check',
--                            'spots_break_type_source_paired_check',
--                            'spots_break_type_citation_check',
--                            'spots_break_type_source_url_check',
--                            'spots_break_type_evidence_check'))::text)
--     ) AS c(check_name, expected, actual);
--
-- Run in the Supabase SQL editor.

ALTER TABLE spots ADD COLUMN IF NOT EXISTS break_type_source TEXT;
ALTER TABLE spots ADD COLUMN IF NOT EXISTS break_type_source_url TEXT;
ALTER TABLE spots ADD COLUMN IF NOT EXISTS break_type_evidence TEXT;

-- Idempotent by the IS NULL predicate: a re-run finds nothing left to set, and a row whose
-- source has since been set to something real is not reverted to 'unattributed'.
UPDATE spots
   SET break_type_source = 'unattributed'
 WHERE break_type IS NOT NULL
   AND break_type_source IS NULL;

-- The confidence, computed from the source. Replaced only while it is still the old hand-written
-- column, so a re-run leaves the generated one alone.
DO $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM information_schema.columns
     WHERE table_schema = 'public' AND table_name = 'spots'
       AND column_name = 'break_type_confidence' AND is_generated = 'ALWAYS'
  ) THEN
    ALTER TABLE spots DROP COLUMN IF EXISTS break_type_confidence;
    ALTER TABLE spots ADD COLUMN break_type_confidence TEXT
      GENERATED ALWAYS AS (
        CASE
          WHEN break_type_source IS NULL THEN NULL
          WHEN break_type_source = 'reviewed' THEN 'high'
          WHEN break_type_source IN ('researched', 'scraped') THEN 'medium'
          ELSE 'low'
        END
      ) STORED;
  END IF;
END $$;

-- The fixed list. IS NULL OR ... IN (...) per 014 and 018: NULL (nobody looked) stays
-- distinguishable from 'unknown' (someone looked and could not tell).
ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_break_type_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_break_type_check CHECK (
    break_type IS NULL OR break_type IN (
      'beach', 'reef', 'point', 'jetty', 'rivermouth', 'unknown'
    )
  );

ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_break_type_source_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_break_type_source_check CHECK (
    break_type_source IS NULL OR break_type_source IN (
      'reviewed', 'researched', 'scraped', 'model_recall', 'unattributed', 'algorithm_default'
    )
  );

-- Value and source: both set or both NULL.
ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_break_type_source_paired_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_break_type_source_paired_check CHECK (
    (break_type IS NULL) = (break_type_source IS NULL)
  );

-- A citation only beside a source, and the two sources that are citations carry their page.
ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_break_type_citation_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_break_type_citation_check CHECK (
    (break_type_source IS NOT NULL
       OR (break_type_source_url IS NULL AND break_type_evidence IS NULL))
    AND (break_type_source IS NULL
       OR break_type_source NOT IN ('researched', 'scraped')
       OR break_type_source_url IS NOT NULL)
  );

ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_break_type_source_url_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_break_type_source_url_check CHECK (
    break_type_source_url IS NULL OR break_type_source_url ~ '^https?://[^[:space:]]+$'
  );

ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_break_type_evidence_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_break_type_evidence_check CHECK (
    break_type_evidence IS NULL OR char_length(break_type_evidence) BETWEEN 1 AND 300
  );

COMMENT ON COLUMN spots.break_type IS
  'The break''s type: beach / reef / point / jetty / rivermouth / unknown, or NULL. ''unknown'' '
  'means someone looked and could not tell; NULL means nobody has looked. Always set together '
  'with break_type_source (spots_break_type_source_paired_check).';

COMMENT ON COLUMN spots.break_type_source IS
  'Where break_type came from, ranked: reviewed 6 > researched 5 > scraped 4 > model_recall 3 > '
  'unattributed 2 > algorithm_default 1 (pipeline/config.py BREAK_TYPE_SOURCE_RANK). A lower '
  'rank never overwrites a higher one; an equal rank may. ''unattributed'' is the state of all '
  '444 values that predate migration 020: enrich, verify_spots and scrape_surf_forecast all '
  'wrote the field and none recorded itself.';

COMMENT ON COLUMN spots.break_type_source_url IS
  'The page cited for break_type. Required when break_type_source is researched or scraped.';

COMMENT ON COLUMN spots.break_type_evidence IS
  'A short quote (at most 300 characters) from break_type_source_url naming the type.';

COMMENT ON COLUMN spots.break_type_confidence IS
  'Generated from break_type_source: high for reviewed, medium for researched or scraped, low '
  'otherwise, NULL when there is no source. No writer sets it. Replaced in migration 020: the '
  'old DOUBLE PRECISION column read 0.5 on every row, written beside a value it did not '
  'describe.';
