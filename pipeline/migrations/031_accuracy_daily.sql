-- 031_accuracy_daily.sql
-- The accuracy scoreboard's daily totals: forecast against buoy, summed per day, so the weekly
-- report reads a few thousand small rows instead of the raw forecasts.
--
-- Written nightly by pipeline/accuracy_score.py (.github/workflows/accuracy-score.yml), which
-- pairs each forecast with the buoy reading nearest its hour (within 30 minutes):
--   kind 'buoy', source 'ww3' or 'nwps'  model_at_buoys (migration 030) at the buoy itself;
--   kind 'spot', source 'spot'           the spot forecast (forecast_leads for lead days 1-3,
--                                        forecasts for lead day 0) against the spot's nearest
--                                        buoy, only where that buoy is within 25 km.
-- Direction pairs only where the buoy's wave height is at least 0.5 m.
--
-- ONE ROW = the sums for one (day, kind, source, region, point, lead day, measure), from which
-- the report works out, over any run of days:
--   bias = sum_e / n          MAE = sum_abs_e / n          RMSE = sqrt(sum_ee / n)
--   scatter index = RMSE / (sum_o / n)
--   correlation = (n sum_fo - sum_f sum_o) / sqrt((n sum_ff - sum_f^2)(n sum_oo - sum_o^2))
-- e is forecast minus observed; for direction, the signed angle between them (-180 to 180).
-- point '' is every point in the region; a buoy id is that buoy alone (kind 'buoy', height only,
-- for the report's best and worst buoys).
--
-- Re-scoring a day overwrites its rows. Apply BEFORE merging the code that writes it.
-- Idempotent; run in the Supabase SQL editor. Size: at most about 1,800 rows a day, about
-- 0.1 GB a year.
--
-- CHECK 1, right after applying: 18 columns.
--   SELECT count(*) FROM information_schema.columns
--   WHERE table_schema = 'public' AND table_name = 'accuracy_daily';
--
-- CHECK 2, after the first nightly scoring run: rows and pairs per day, kind and source.
--   SELECT day, kind, source, count(*) AS rows, sum(n) FILTER (WHERE point = '') AS pairs
--   FROM accuracy_daily GROUP BY day, kind, source ORDER BY day DESC, kind, source LIMIT 30;

CREATE TABLE IF NOT EXISTS accuracy_daily (
  day        DATE             NOT NULL,
  kind       TEXT             NOT NULL CHECK (kind IN ('buoy', 'spot')),
  source     TEXT             NOT NULL CHECK (source IN ('ww3', 'nwps', 'spot')),
  region     TEXT             NOT NULL,
  point      TEXT             NOT NULL DEFAULT '',
  lead_day   SMALLINT         NOT NULL CHECK (lead_day BETWEEN 0 AND 3),
  measure    TEXT             NOT NULL CHECK (measure IN ('hs', 'swell_hs', 'tp', 'dp')),
  n          INTEGER          NOT NULL CHECK (n > 0),
  points     INTEGER          NOT NULL,
  sum_f      DOUBLE PRECISION NOT NULL,
  sum_o      DOUBLE PRECISION NOT NULL,
  sum_ff     DOUBLE PRECISION NOT NULL,
  sum_oo     DOUBLE PRECISION NOT NULL,
  sum_fo     DOUBLE PRECISION NOT NULL,
  sum_e      DOUBLE PRECISION NOT NULL,
  sum_abs_e  DOUBLE PRECISION NOT NULL,
  sum_ee     DOUBLE PRECISION NOT NULL,
  scored_at  TIMESTAMPTZ      NOT NULL,
  PRIMARY KEY (day, kind, source, region, point, lead_day, measure)
);

ALTER TABLE accuracy_daily ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE accuracy_daily IS
  'Daily forecast-against-buoy sums (n, sums of forecast, observed, their squares and product, '
  'and of the error, its absolute value and its square) per day, kind (buoy: model at the buoy; '
  'spot: spot forecast against its buoy within 25 km), source, region, point ('''' = the whole '
  'region), lead day and measure. Written nightly by pipeline/accuracy_score.py; read by '
  'pipeline/accuracy_report.py. RLS on with no policies.';
