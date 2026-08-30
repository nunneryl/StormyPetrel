-- 014_surf_reports.sql
-- surf_reports — ground-truth observations from site visitors, joinable to a forecast hour.
--
-- WHY THIS TABLE EXISTS. face_ft has never been calibrated against an observation and the
-- star rating has never been validated against one. Both are model output all the way down:
-- face_ft = hs_m * period_factor(tp) * 3.281, and stars is a composite of that face plus
-- dir_gain / wind_mult / tide_mult / chop_mult. Nothing in the pipeline closes the loop. A
-- report row is the first label: what a person actually saw at a spot in a given hour.
--
-- THE WHOLE POINT IS THE JOIN. A free-text opinion is unusable; the schema is designed so
-- every row joins to exactly one forecast row:
--     surf_reports (spot_id, observed_hour)  ->  forecasts (spot_id, valid_time)
-- forecasts is UNIQUE(spot_id, valid_time, source) with valid_time on the UTC hour, so
-- observed_hour is stored as a UTC hour timestamptz and nothing else will do.
--
-- ACCESS. Written ONLY by frontend/app/api/reports/route.ts using the service-role client
-- (frontend/lib/supabase.ts). RLS is enabled with NO policies below: service_role bypasses
-- RLS, so the route keeps working while anon and authenticated get nothing at all. That is
-- deliberate belt-and-braces — the site ships no anon key today, and if one is ever added
-- this table does not silently become publicly readable or writable.
--
-- Run in the Supabase SQL editor (idempotent).

CREATE TABLE IF NOT EXISTS surf_reports (
  id                BIGSERIAL PRIMARY KEY,
  spot_id           INTEGER NOT NULL REFERENCES spots(id) ON DELETE CASCADE,
  observed_hour     TIMESTAMPTZ NOT NULL,
  size_bucket       TEXT NOT NULL,
  reporter_hash     TEXT NOT NULL,
  forecast_face_ft  DOUBLE PRECISION,
  forecast_stars    DOUBLE PRECISION,
  rating_verdict    TEXT,
  reported_at       TIMESTAMPTZ NOT NULL DEFAULT now(),

  -- The size ladder, in ascending order. Stored as the LABEL, never as feet: the
  -- label-to-feet mapping is a revisable assumption (see frontend/lib/surfReport.ts),
  -- and storing feet would bake today's mapping into the data permanently.
  CONSTRAINT surf_reports_size_bucket_check CHECK (size_bucket IN (
    'ankle', 'knee', 'thigh', 'waist', 'chest', 'shoulder', 'head',
    'overhead', 'well_overhead', 'double_overhead', 'triple_overhead_plus'
  )),

  -- Optional. NULL means the reporter answered the size question and skipped this one,
  -- which is the common case and must stay distinguishable from 'about_right'.
  CONSTRAINT surf_reports_rating_verdict_check CHECK (
    rating_verdict IS NULL OR rating_verdict IN ('too_low', 'about_right', 'too_high')
  ),

  -- Idempotence AND the rate limiter, in one constraint. A repeat submission for the same
  -- spot-hour by the same reporter is a conflict the route swallows, so a double-tap or a
  -- refresh cannot produce two rows. Enforced by the database, so no client can bypass it.
  CONSTRAINT surf_reports_unique_report UNIQUE (spot_id, observed_hour, reporter_hash)
);

-- The analysis query is "every report for this spot, oldest first" and "every report in a
-- window"; both are served by the unique index above on its leading columns. This second
-- index serves the cross-spot sweep ("all reports in the last week") that has no spot_id.
CREATE INDEX IF NOT EXISTS idx_surf_reports_observed_hour
  ON surf_reports (observed_hour);

ALTER TABLE surf_reports ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE surf_reports IS
  'Visitor-reported surf size, one row per (spot, UTC hour, reporter). The ground-truth '
  'label set for calibrating forecasts.face_ft and validating forecasts.stars. Joins to '
  'forecasts on (spot_id, observed_hour = valid_time). Written only by the service-role '
  'API route /api/reports; RLS enabled with no policies so anon/authenticated get nothing.';

COMMENT ON COLUMN surf_reports.spot_id IS
  'FK to spots(id), NOT the slug. forecasts.spot_id keys on id, so storing a slug would '
  'force an extra join on every analysis query. Slugs are also unstable: next.config.js '
  'carries eight permanent /spot/OLD -> /spot/NEW redirects (trees->3-mile, '
  'marias-rincon->maria-s, ...), so slugs have already changed in production and ids have '
  'not. The route accepts a slug from the browser (which only knows the URL) and resolves '
  'it here, which doubles as validation: an unknown slug is a rejected request.';

COMMENT ON COLUMN surf_reports.observed_hour IS
  'The UTC hour BEING REPORTED ON — asked, never inferred from click time. Someone may '
  'report at 4pm about a dawn session, so report time and observed time are different '
  'quantities and reported_at holds the other one. Truncated to the hour to match '
  'forecasts.valid_time exactly; this is the join key and the table is worthless without it.';

COMMENT ON COLUMN surf_reports.size_bucket IS
  'Body-part size label (ankle..triple_overhead_plus), the vocabulary surfers actually use, '
  'so the answer is low-friction and honest. Ordinal and therefore computable. Means the '
  'AVERAGE OF THE SETS THE REPORTER RODE, which is what the panel copy asks for — a label '
  'meaning "the biggest one" would be a different statistic from the one face_ft publishes.';

COMMENT ON COLUMN surf_reports.reporter_hash IS
  'HMAC(daily-rotated secret salt, ip + user-agent), truncated to 16 hex chars. NOT an '
  'identity: the salt rotates every UTC day, so the same person hashes differently tomorrow '
  'and cannot be linked across days; truncation and the secret salt make it unreversible to '
  'an IP. No raw IP and no cookie id is ever stored. Its job is the UNIQUE constraint above, '
  'and later, spotting a reporter whose labels are consistently biased WITHIN a day.';

COMMENT ON COLUMN surf_reports.forecast_face_ft IS
  'SNAPSHOT of forecasts.face_ft for (spot_id, observed_hour), taken at submit time. This is '
  'the non-obvious column, so: it is NOT redundant with a later join. forecasts is keyed '
  'UNIQUE(spot_id, valid_time, source) and every pipeline run UPSERTS over every hour it '
  'covers. The pipeline runs three times a day and each run starts at its own cycle analysis '
  'hour, so once an hour has passed it stops being covered and the row freezes at the LAST, '
  'SHORTEST-LEAD forecast made for it — effectively a nowcast. The 30-hour-lead forecast a '
  'surfer actually read at 6am is gone by the time anyone joins. Joining later can only ever '
  'answer "was the nowcast right"; this column answers "was the forecast people acted on '
  'right", which is the question worth asking. NULL when no forecast row exists for that '
  'exact hour — the route never substitutes another hour''s number.';

COMMENT ON COLUMN surf_reports.forecast_stars IS
  'SNAPSHOT of forecasts.stars for (spot_id, observed_hour), for the same reason as '
  'forecast_face_ft: the stored row drifts to a nowcast once the hour passes. Everything '
  'else (dir_gain, wind_mult, tide_mult, chop_ratio, period, swell direction) is '
  'deliberately NOT snapshotted — that is diagnostic context, not the label, and a '
  'post-mortem wants the revised values anyway.';

COMMENT ON COLUMN surf_reports.rating_verdict IS
  'Optional too_low / about_right / too_high on the star rating. Kept SEPARATE from '
  'size_bucket because they measure different things: size is a physical quantity with a '
  'right answer and calibrates face_ft directly, while stars is a composite of size x '
  'direction x wind x tide x chop, so a "wrong" verdict does not say which factor failed. '
  'Nullable and never blocking: it is asked after the size answer lands.';

COMMENT ON COLUMN surf_reports.reported_at IS
  'When the report was submitted, as distinct from observed_hour. Lets a later calibration '
  'pass measure recall lag and down-weight a report filed days after the session.';
