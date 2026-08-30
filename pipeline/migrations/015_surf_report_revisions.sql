-- 015_surf_report_revisions.sql
-- Last answer wins: let a repeat report REPLACE the earlier one, and record that it did.
--
-- WHAT WAS WRONG. 014 shipped an insert whose UNIQUE(spot_id, observed_hour, reporter_hash)
-- conflict was swallowed, so the FIRST answer survived and every correction was discarded.
-- That is backwards. Someone who reports, refreshes and reports a different size is almost
-- certainly correcting themselves, and keeping the mistake is the one outcome nobody wants.
-- /api/reports now UPDATEs the existing row instead of dropping the request on the floor.
--
-- WHICH HALF OF THE ROW IS MUTABLE. The update writes size_bucket, rating_verdict,
-- reported_at and revision — and NOTHING ELSE. In particular it does not touch
-- forecast_face_ft / forecast_stars. See the forecast_face_ft comment below for the
-- argument; the short version is that re-snapshotting can only ever move the value toward
-- the nowcast, which is the quantity 014 created the column to avoid.
--
-- That split gives the row one clean invariant, worth stating because every future writer
-- depends on it:
--
--     ORIGINAL facts are frozen:  forecast_face_ft, forecast_stars, first_reported_at
--     ANSWER facts are current:   size_bucket, rating_verdict, reported_at, revision
--
-- so a row is self-describing — the snapshot it carries is the one taken at
-- first_reported_at, not at whatever time the reporter last touched it.
--
-- Run in the Supabase SQL editor (idempotent). Does not modify 014.

-- revision — how many times the ANSWER has been replaced. 0 on a first report.
ALTER TABLE surf_reports
  ADD COLUMN IF NOT EXISTS revision INTEGER NOT NULL DEFAULT 0;

-- first_reported_at — when the ORIGINAL answer was given. Added nullable, backfilled from
-- the rows already in production (for which reported_at IS the original), then constrained.
ALTER TABLE surf_reports
  ADD COLUMN IF NOT EXISTS first_reported_at TIMESTAMPTZ;

UPDATE surf_reports
   SET first_reported_at = reported_at
 WHERE first_reported_at IS NULL;

ALTER TABLE surf_reports ALTER COLUMN first_reported_at SET DEFAULT now();
ALTER TABLE surf_reports ALTER COLUMN first_reported_at SET NOT NULL;

-- Cheap guard against a decrementing or negative counter — the route computes revision in
-- TypeScript (see below), so this is the only thing standing between an arithmetic slip and
-- a nonsense value in the calibration set.
ALTER TABLE surf_reports
  DROP CONSTRAINT IF EXISTS surf_reports_revision_check;
ALTER TABLE surf_reports
  ADD CONSTRAINT surf_reports_revision_check CHECK (revision >= 0);

-- NO INDEX ON revision, deliberately. Revised labels are expected to be a small minority,
-- and the calibration pass that cares reads the whole table anyway; an index would cost a
-- write on every report to serve a query that is run by hand a few times a year.

COMMENT ON COLUMN surf_reports.revision IS
  'How many times the ANSWER (size_bucket / rating_verdict) has been replaced by a later '
  'submission from the same reporter for the same spot-hour. 0 = never revised. Exists so a '
  'calibration pass can tell a corrected label from a first-time one and down-weight or '
  'investigate it: a label the reporter changed their mind about is weaker evidence than one '
  'they gave once, and a systematic pattern of corrections (say, knee -> waist) would be '
  'saying something about the control''s copy rather than about the surf. '
  'DELIBERATELY A COUNTER AND NOT A HISTORY TABLE. A surf_report_revisions child table would '
  'also keep the superseded ANSWERS, which is genuinely more informative, at the cost of a '
  'second table, a second write on every revision, its own RLS, and a join for every reader. '
  'This is a feedback control, not an audit system, so the cheap thing that answers the '
  'question actually asked — "was this label changed?" — wins. If the counter ever shows a '
  'meaningful revision rate, THAT is the evidence for building the history table. '
  'Computed in frontend/lib/surfReport.ts (read-then-write), not by a trigger, so the rule '
  '"only a CHANGED answer increments it" is testable without a database. The cost of that '
  'choice is that two genuinely concurrent revisions of the same row could both read the '
  'same value and undercount by one — acceptable here, and the alternative (a BEFORE UPDATE '
  'trigger) would move the rule somewhere no test in this repo can reach.';

COMMENT ON COLUMN surf_reports.first_reported_at IS
  'When the ORIGINAL answer was given. Never updated. reported_at moves to the surviving '
  'answer''s time, so without this a revision would destroy the recall-lag measurement '
  'reported_at exists for (observed_hour -> reported_at). It is also what makes the '
  'preserved forecast snapshot self-describing: forecast_face_ft is the value we held at '
  'first_reported_at, not at reported_at.';

COMMENT ON COLUMN surf_reports.reported_at IS
  'When the SURVIVING answer was given — updated on every repeat submission, including one '
  'that repeats the same answer. first_reported_at holds the original. (Superseded 014''s '
  'comment, which predates revisions.)';

COMMENT ON COLUMN surf_reports.forecast_face_ft IS
  'SNAPSHOT of forecasts.face_ft for (spot_id, observed_hour), taken when the row was FIRST '
  'inserted and NEVER re-taken on a revision. Two facts decide this. (1) forecasts is keyed '
  'UNIQUE(spot_id, valid_time, source) and every pipeline run upserts over the hours it '
  'covers, so a row for a past hour drifts toward the shortest-lead forecast made for it — a '
  'nowcast. (2) The column exists precisely to hold what the forecast said when a person '
  'acted on it, which is the one thing a later join can never reconstruct. Re-snapshotting '
  'therefore moves the value MONOTONICALLY toward the quantity you could have got by joining '
  'anyway: it can only subtract information, never add any. Preserving also makes the column '
  'immutable once written, so a calibration query does not have to reason about a value that '
  'silently depends on when the reporter last touched the row. '
  'A tempting middle path — preserve a non-NULL snapshot but FILL IN one that was NULL — was '
  'rejected: a value discovered hours later is exactly the tainted nowcast, and filling in '
  'would leave the column heterogeneous with no way to tell which rows were long-lead and '
  'which were not. NULL still means "we held no forecast row for that exact hour". '
  '(Extends 014''s comment, which predates revisions.)';

COMMENT ON COLUMN surf_reports.forecast_stars IS
  'SNAPSHOT of forecasts.stars for (spot_id, observed_hour), frozen at first insert for the '
  'same reason as forecast_face_ft. (Extends 014''s comment, which predates revisions.)';
