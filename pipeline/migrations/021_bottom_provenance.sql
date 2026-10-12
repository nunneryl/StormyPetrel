-- Migration 021 — what each spot's waves break over (bottom), with 020's provenance rules.
--
-- WHY. break_type mixes two things: the shape of the break ('point') and what it breaks over
-- ('reef'). A close-out ceiling depends mainly on the second: shifting sand closes out, a fixed
-- bottom holds. pipeline/research_break_type.py researched both separately, each from a page
-- that says so; this gives the bottom somewhere to live beside break_type.
--
-- WHAT THIS ADDS, mirroring 020 exactly — only the values differ:
--
--   bottom                  a fixed list: sand, rock, coral, cobble, mixed, unknown.
--                           'rock' includes any reef not said to be coral. 'mixed' means more
--                           than one, such as sand over rock. 'unknown' means someone looked
--                           and could not tell; NULL means nobody has looked.
--   bottom_source           where the value came from, ranked with 020's ladder and numbers
--                           (pipeline/config.py BREAK_TYPE_SOURCE_RANK; a lower rank may never
--                           overwrite a higher one, an equal rank may):
--                             reviewed           6  a person set it
--                             researched         5  a source was consulted and is cited
--                             scraped            4  read off the break's own surf-forecast.com page
--                             model_recall       3  a language model answered with no citation
--                             unattributed       2  held, but no writer recorded itself
--                             algorithm_default  1  an algorithm's fallback answer
--   value and source        both set or both NULL.
--   bottom_source_url       the page cited for the value. researched and scraped must carry one.
--   bottom_evidence         a short quote from that page, at most 300 characters.
--   bottom_confidence       COMPUTED BY THE DATABASE from bottom_source, with 020's mapping:
--                           high for reviewed, medium for researched or scraped, low otherwise,
--                           NULL with no source. db_import never sends it (_DB_MANAGED_COLUMNS).
--
-- NO BACKFILL AND NO DEFAULT. No spot has a bottom today, so every row starts NULL — "nobody
-- has looked" — and every check passes on every existing row. The bottom arrives through the
-- roster: pipeline/import_break_type_research.py writes it into pipeline/spots_enriched.json,
-- and db_import uploads it on the next pipeline run, like every other spots column.
--
-- ORDER OF OPERATIONS — RUN THIS MIGRATION FIRST, THEN MERGE, after the check query below
-- returns ok on every row. Merged first, the run's column preflight finds bottom,
-- bottom_source, bottom_source_url and bottom_evidence missing, leaves them out of the upload
-- and ends the run failed, naming this file; forecasts are still published.
--
-- IDEMPOTENT. Columns are added IF NOT EXISTS (bottom_confidence is new, so it is created
-- generated or not at all), and constraints use 015's DROP CONSTRAINT IF EXISTS + ADD
-- CONSTRAINT, so a re-run replaces them rather than leaving a stale one.
--
-- CHECK QUERY — run after applying. Every row must read ok = true.
-- pipeline/tests/test_bottom_migration.py runs this exact text against a database shaped
-- like the live one:
--
--   SELECT check_name, expected, actual, expected = actual AS ok
--     FROM (VALUES
--       ('bottom columns added',              '5',
--        (SELECT count(*) FROM information_schema.columns
--          WHERE table_schema = 'public' AND table_name = 'spots'
--            AND column_name IN ('bottom', 'bottom_source', 'bottom_confidence',
--                                'bottom_source_url', 'bottom_evidence'))::text),
--       ('bottom_confidence generated',       'ALWAYS',
--        (SELECT is_generated::text FROM information_schema.columns
--          WHERE table_schema = 'public' AND table_name = 'spots'
--            AND column_name = 'bottom_confidence')),
--       ('bottom checks in place',            '6',
--        (SELECT count(*) FROM pg_constraint
--          WHERE conrelid = 'public.spots'::regclass
--            AND conname IN ('spots_bottom_check',
--                            'spots_bottom_source_check',
--                            'spots_bottom_source_paired_check',
--                            'spots_bottom_citation_check',
--                            'spots_bottom_source_url_check',
--                            'spots_bottom_evidence_check'))::text),
--       ('rows with a bottom yet',            '0',
--        (SELECT count(*) FROM spots
--          WHERE bottom IS NOT NULL OR bottom_source IS NOT NULL
--             OR bottom_source_url IS NOT NULL OR bottom_evidence IS NOT NULL
--             OR bottom_confidence IS NOT NULL)::text),
--       ('break_type checks still in place',  '6',
--        (SELECT count(*) FROM pg_constraint
--          WHERE conrelid = 'public.spots'::regclass
--            AND conname LIKE 'spots_break_type%check')::text)
--     ) AS c(check_name, expected, actual);
--
-- Run in the Supabase SQL editor.

ALTER TABLE spots ADD COLUMN IF NOT EXISTS bottom TEXT;
ALTER TABLE spots ADD COLUMN IF NOT EXISTS bottom_source TEXT;
ALTER TABLE spots ADD COLUMN IF NOT EXISTS bottom_source_url TEXT;
ALTER TABLE spots ADD COLUMN IF NOT EXISTS bottom_evidence TEXT;

-- The confidence, computed from the source with 020's mapping. No writer can set it.
ALTER TABLE spots ADD COLUMN IF NOT EXISTS bottom_confidence TEXT
  GENERATED ALWAYS AS (
    CASE
      WHEN bottom_source IS NULL THEN NULL
      WHEN bottom_source = 'reviewed' THEN 'high'
      WHEN bottom_source IN ('researched', 'scraped') THEN 'medium'
      ELSE 'low'
    END
  ) STORED;

-- The fixed list. NULL (nobody looked) stays distinguishable from 'unknown'.
ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_bottom_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_bottom_check CHECK (
    bottom IS NULL OR bottom IN (
      'sand', 'rock', 'coral', 'cobble', 'mixed', 'unknown'
    )
  );

ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_bottom_source_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_bottom_source_check CHECK (
    bottom_source IS NULL OR bottom_source IN (
      'reviewed', 'researched', 'scraped', 'model_recall', 'unattributed', 'algorithm_default'
    )
  );

-- Value and source: both set or both NULL.
ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_bottom_source_paired_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_bottom_source_paired_check CHECK (
    (bottom IS NULL) = (bottom_source IS NULL)
  );

-- A citation only beside a source, and the two sources that are citations carry their page.
ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_bottom_citation_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_bottom_citation_check CHECK (
    (bottom_source IS NOT NULL
       OR (bottom_source_url IS NULL AND bottom_evidence IS NULL))
    AND (bottom_source IS NULL
       OR bottom_source NOT IN ('researched', 'scraped')
       OR bottom_source_url IS NOT NULL)
  );

ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_bottom_source_url_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_bottom_source_url_check CHECK (
    bottom_source_url IS NULL OR bottom_source_url ~ '^https?://[^[:space:]]+$'
  );

ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_bottom_evidence_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_bottom_evidence_check CHECK (
    bottom_evidence IS NULL OR char_length(bottom_evidence) BETWEEN 1 AND 300
  );

COMMENT ON COLUMN spots.bottom IS
  'What the waves break over: sand / rock / coral / cobble / mixed / unknown, or NULL. ''rock'' '
  'includes any reef not said to be coral. ''unknown'' means someone looked and could not tell; '
  'NULL means nobody has looked. Always set together with bottom_source '
  '(spots_bottom_source_paired_check).';

COMMENT ON COLUMN spots.bottom_source IS
  'Where bottom came from, ranked as break_type_source: reviewed 6 > researched 5 > scraped 4 > '
  'model_recall 3 > unattributed 2 > algorithm_default 1 (pipeline/config.py '
  'BREAK_TYPE_SOURCE_RANK). A lower rank never overwrites a higher one; an equal rank may.';

COMMENT ON COLUMN spots.bottom_source_url IS
  'The page cited for bottom. Required when bottom_source is researched or scraped.';

COMMENT ON COLUMN spots.bottom_evidence IS
  'A short quote (at most 300 characters) from bottom_source_url naming the bottom.';

COMMENT ON COLUMN spots.bottom_confidence IS
  'Generated from bottom_source: high for reviewed, medium for researched or scraped, low '
  'otherwise, NULL when there is no source. No writer sets it.';
