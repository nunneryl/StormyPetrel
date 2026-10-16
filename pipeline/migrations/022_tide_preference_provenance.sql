-- Migration 022 — say which tide preferences a model recalled, and hold 'researched' to a citation.
--
-- WHAT WENT WRONG AFTER 018. 018 gave tide_preference a source and stamped the 444 values then
-- held 'unattributed'. Every writer since stamps its own source — but two of the three stamped
-- the wrong one:
--
--     classify_tides   asks a language model with no tools at all (its messages.create call has
--                      no tools= argument) and stamped 'researched'.
--     verify_spots     asks a model that may search the web, never asks it to cite anything for
--                      the tide, and stamped 'researched'.
--     scrape_surf_forecast  reads the break's own surf-forecast.com page and stamped 'scraped',
--                      without recording the page.
--
-- 'researched' sat at the top of 018's ladder and reads as "a page was consulted for this
-- break". Neither model writer recorded a page, so the column could not tell a model's recall
-- from a finding. break_type had the same defect and migration 020 fixed it with model_recall
-- and a citation; this migration does the same for the tide.
--
-- WHAT THIS ADDS, mirroring 020:
--
--   tide_preference_source      reviewed and model_recall join the list. The ladder
--                               (pipeline/config.py TIDE_SOURCE_RANK holds the same numbers; a
--                               lower rank may never overwrite a higher one, an equal rank may):
--                                 reviewed                 6  a person set it
--                                 researched               5  a page was read, cited and quoted
--                                 scraped                  4  read off the break's own surf-forecast.com page, cited
--                                 model_recall             3  a language model answered with no citation
--                                 derived_from_break_type  2  inferred from the break's class
--                                 unattributed             1  held, but no writer recorded itself
--                               'unknown' stays admitted, unranked, as 018 left it: a source that
--                               makes no provenance claim. No writer stamps it.
--   value and source            both set or both NULL, enforced here, as 020 does for break_type.
--   tide_preference_source_url  the page cited for the value. researched and scraped must carry one.
--   tide_preference_evidence    a short quote from that page, at most 300 characters, only beside
--                               a URL. researched must carry one: a cited page alone is what
--                               verify_spots could produce without reading anything about the tide.
--
-- THE RELABEL. A row stamped 'researched' with no URL can only have come from classify_tides or
-- verify_spots before this change — neither recorded a page — so it is a model's answer and is
-- relabelled 'model_recall'. The committed roster carries no such row (no spot in
-- pipeline/spots_enriched.json had a tide source before this change, and db_import sends only
-- the keys a spot carries), so the expected count is 0; the pre-check in the pull request shows
-- the live number before anything is applied. A 'scraped' row with no URL is NOT relabelled:
-- it was read off a page, just not cited, and the citation check below refuses it, so the whole
-- file fails and the pre-check names it, rather than this file guessing a label.
--
-- WHY THE CHECKS REJECT NOTHING TODAY. From 018's header and the committed roster, not from the
-- live table, which this change did not query:
--
--     stored value   rated spots   source after 018
--     'mid'                  365   unattributed
--     'low'                   62   unattributed
--     'high'                   9   unattributed
--     'all'                    8   unattributed
--     NULL                   202   NULL
--                           ---
--                           646
--
-- 444 values, every one stamped by 018's backfill; this file re-runs that backfill, so a value
-- written since with no source is stamped too. No row has a URL or a quote. If a live row breaks
-- a rule anyway — a source beside a NULL value, say — Postgres runs a multi-statement query as
-- one implicit transaction, so nothing in this file is applied.
--
-- NO DEFAULT ON THE NEW COLUMNS, DELIBERATELY. A new spot arrives with the tide and the three
-- columns beside it NULL, which reads correctly as "nobody has looked".
--
-- ORDER OF OPERATIONS — RUN THIS MIGRATION FIRST, THEN MERGE, after the check query below
-- returns ok on every row. Merged first, the run's column preflight finds
-- tide_preference_source_url and tide_preference_evidence missing, leaves them out of the
-- upload and ends the run failed, naming this file; and a roster carrying a model_recall tide
-- would fail the spots upsert on 018's source check.
--
-- IDEMPOTENT. Constraints use 015's DROP CONSTRAINT IF EXISTS + ADD CONSTRAINT, as 018 and 020
-- do, so a re-run replaces them; both updates find nothing left to change on a second run.
--
-- CHECK QUERY — run after applying. Every row must read ok = true. The counts are the live
-- table's as 018 recorded them; pipeline/tests/test_tide_provenance_migration.py runs this exact
-- text against a database shaped like it:
--
--   SELECT check_name, expected, actual, expected = actual AS ok
--     FROM (VALUES
--       ('spots',                                '646',
--        (SELECT count(*) FROM spots)::text),
--       ('spots with a tide_preference',         '444',
--        (SELECT count(*) FROM spots WHERE tide_preference IS NOT NULL)::text),
--       ('... stamped unattributed',             '444',
--        (SELECT count(*) FROM spots
--          WHERE tide_preference IS NOT NULL
--            AND tide_preference_source = 'unattributed')::text),
--       ('spots with no tide_preference',        '202',
--        (SELECT count(*) FROM spots
--          WHERE tide_preference IS NULL
--            AND tide_preference_source IS NULL)::text),
--       ('rows with a URL or a quote',           '0',
--        (SELECT count(*) FROM spots
--          WHERE tide_preference_source_url IS NOT NULL
--             OR tide_preference_evidence IS NOT NULL)::text),
--       ('source check admits model_recall',     'true',
--        (SELECT (pg_get_constraintdef(oid) LIKE '%model_recall%')::text FROM pg_constraint
--          WHERE conrelid = 'public.spots'::regclass
--            AND conname = 'spots_tide_preference_source_check')),
--       ('tide_preference checks in place',      '6',
--        (SELECT count(*) FROM pg_constraint
--          WHERE conrelid = 'public.spots'::regclass
--            AND conname IN ('spots_tide_preference_check',
--                            'spots_tide_preference_source_check',
--                            'spots_tide_preference_source_paired_check',
--                            'spots_tide_preference_citation_check',
--                            'spots_tide_preference_source_url_check',
--                            'spots_tide_preference_evidence_check'))::text)
--     ) AS c(check_name, expected, actual);
--
-- Run in the Supabase SQL editor.

-- tide_preference_source is 018's; 022 needs it in place (the column preflight names 018 for it).
ALTER TABLE spots ADD COLUMN IF NOT EXISTS tide_preference_source_url TEXT;
ALTER TABLE spots ADD COLUMN IF NOT EXISTS tide_preference_evidence TEXT;

-- 018's list, unchanged, re-added by name so the six checks below are one set.
ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_tide_preference_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_tide_preference_check CHECK (
    tide_preference IS NULL OR tide_preference IN (
      'low', 'low_mid', 'mid', 'mid_high', 'high', 'all', 'unknown'
    )
  );

-- The ladder in rank order, then 'unknown'. Widened before the relabel below writes model_recall.
ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_tide_preference_source_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_tide_preference_source_check CHECK (
    tide_preference_source IS NULL OR tide_preference_source IN (
      'reviewed', 'researched', 'scraped', 'model_recall', 'derived_from_break_type',
      'unattributed', 'unknown'
    )
  );

-- 018's backfill again, idempotent by the IS NULL predicate: a value written since 018 with no
-- source is stamped, and a row with a real source is not reverted.
UPDATE spots
   SET tide_preference_source = 'unattributed'
 WHERE tide_preference IS NOT NULL
   AND tide_preference_source IS NULL;

-- A 'researched' tide with no page is a model's answer (see THE RELABEL above). Idempotent: a
-- relabelled row is no longer 'researched'.
UPDATE spots
   SET tide_preference_source = 'model_recall'
 WHERE tide_preference_source = 'researched'
   AND tide_preference_source_url IS NULL;

-- Value and source: both set or both NULL.
ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_tide_preference_source_paired_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_tide_preference_source_paired_check CHECK (
    (tide_preference IS NULL) = (tide_preference_source IS NULL)
  );

-- A citation only beside a source, a quote only beside its page, the sources that are
-- citations carry their page, and researched also carries its quote.
ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_tide_preference_citation_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_tide_preference_citation_check CHECK (
    (tide_preference_source IS NOT NULL
       OR (tide_preference_source_url IS NULL AND tide_preference_evidence IS NULL))
    AND (tide_preference_evidence IS NULL OR tide_preference_source_url IS NOT NULL)
    AND (tide_preference_source IS NULL
       OR tide_preference_source NOT IN ('researched', 'scraped')
       OR tide_preference_source_url IS NOT NULL)
    AND (tide_preference_source IS NULL
       OR tide_preference_source NOT IN ('researched')
       OR tide_preference_evidence IS NOT NULL)
  );

ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_tide_preference_source_url_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_tide_preference_source_url_check CHECK (
    tide_preference_source_url IS NULL OR tide_preference_source_url ~ '^https?://[^[:space:]]+$'
  );

ALTER TABLE spots
  DROP CONSTRAINT IF EXISTS spots_tide_preference_evidence_check;
ALTER TABLE spots
  ADD CONSTRAINT spots_tide_preference_evidence_check CHECK (
    tide_preference_evidence IS NULL OR char_length(tide_preference_evidence) BETWEEN 1 AND 300
  );

COMMENT ON COLUMN spots.tide_preference_source IS
  'Where tide_preference came from, ranked: reviewed 6 > researched 5 > scraped 4 > '
  'model_recall 3 > '
  'derived_from_break_type 2 > unattributed 1 (pipeline/config.py TIDE_SOURCE_RANK); '
  '''unknown'' is admitted and unranked. A lower rank never overwrites a higher one; an equal '
  'rank may. Always set together with tide_preference '
  '(spots_tide_preference_source_paired_check). researched needs a cited page and a quote, '
  'scraped a cited page (spots_tide_preference_citation_check). ''unattributed'' is the state of '
  'the 444 values that predate migration 018; a model''s uncited answer is model_recall '
  '(migration 022). Ratings apply tide_preference only when this is reviewed, researched or '
  'scraped (pipeline/interpret.py rated_tide_preference).';

COMMENT ON COLUMN spots.tide_preference_source_url IS
  'The page cited for tide_preference. Required when tide_preference_source is researched or '
  'scraped.';

COMMENT ON COLUMN spots.tide_preference_evidence IS
  'A short quote (at most 300 characters) from tide_preference_source_url stating the tide. '
  'Required when tide_preference_source is researched.';
