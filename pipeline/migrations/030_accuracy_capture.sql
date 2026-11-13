-- 030_accuracy_capture.sql
-- The accuracy scoreboard's raw material: what we forecast 1, 2 and 3 days ahead, kept before the
-- next run overwrites it, and the wave models sampled at the buoys we compare against.
--
-- WHY. forecasts is UNIQUE(spot_id, valid_time, source) and every run upserts over it, so a past
-- hour ends up holding only its last, shortest-lead forecast. Nothing keeps the day-ahead
-- forecasts, and NOMADS keeps only about 5 days of model files, so they cannot be rebuilt later.
-- Decided 2026-11-13: spot forecasts at lead days 1-3 for 5 local daylight hours, and the models
-- at the roster's buoys every 3 hours. Both written by pipeline/accuracy_capture.py, the last
-- step of the full pipeline, after the database push (a failure there never stops the upload).
--
-- LEAD DAY. lead_day = floor((valid_time - issued_at) / 24 h). Each (spot or buoy, hour, lead
-- day) keeps the NEWEST issue in that day's window: the trigger below skips a write from an older
-- issue, row by row, as forecasts_keep_newest_nwps_cycle does (migration 019). The same issue may
-- rewrite its own row (a re-rate with a new face correction).
--
-- Apply BEFORE merging the code that writes it. Idempotent; run in the Supabase SQL editor.
-- (If the code ran first, each run would log a warning and write nothing; nothing else breaks.)
--
-- CHECK 1, right after applying: both tables, with their columns (21 and 30).
--   SELECT table_name, count(*) AS columns FROM information_schema.columns
--   WHERE table_schema = 'public' AND table_name IN ('forecast_leads', 'model_at_buoys')
--   GROUP BY table_name ORDER BY table_name;
--
-- CHECK 2, after the first full pipeline run with the code: rows by lead day.
--   SELECT 'forecast_leads' AS t, lead_day, count(*) AS rows, count(DISTINCT spot_id::text) AS points,
--          min(valid_time) AS first_hour, max(valid_time) AS last_hour, max(captured_at) AS captured
--   FROM forecast_leads GROUP BY lead_day
--   UNION ALL
--   SELECT 'model_at_buoys:' || source, lead_day, count(*), count(DISTINCT buoy_id),
--          min(valid_time), max(valid_time), max(captured_at)
--   FROM model_at_buoys GROUP BY source, lead_day
--   ORDER BY 1, 2;
--
-- CHECK 3, the tables' size on disk (the scoreboard budget: about 0.5 GB a year for
-- forecast_leads and 0.2 GB a year for model_at_buoys; recheck after a week).
--   SELECT relname, pg_size_pretty(pg_total_relation_size(oid)) AS total
--   FROM pg_class WHERE relname IN ('forecast_leads', 'model_at_buoys');

-- --------------------------------------------------------------------------------------------
-- forecast_leads: the spot forecast as issued 1, 2 and 3 days ahead
-- --------------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS forecast_leads (
  spot_id                  INTEGER     NOT NULL REFERENCES spots(id) ON DELETE CASCADE,
  valid_time               TIMESTAMPTZ NOT NULL,
  lead_day                 SMALLINT    NOT NULL CHECK (lead_day BETWEEN 1 AND 3),
  issued_at                TIMESTAMPTZ NOT NULL,
  lead_hours               SMALLINT    NOT NULL,
  hs                       REAL,
  tp                       REAL,
  dp                       REAL,
  swell_hs                 REAL,
  swell_tp                 REAL,
  swell_dp                 REAL,
  wind_speed               REAL,
  wind_dir                 REAL,
  swell_source             TEXT,
  chop_ratio               REAL,
  face_ft                  REAL,
  face_ft_raw              REAL,
  face_correction_version  TEXT,
  stars                    REAL,
  captured_at              TIMESTAMPTZ NOT NULL,
  run_id                   TEXT,
  PRIMARY KEY (spot_id, valid_time, lead_day)
);

CREATE INDEX IF NOT EXISTS idx_forecast_leads_valid ON forecast_leads (valid_time);

ALTER TABLE forecast_leads ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE forecast_leads IS
  'The spot forecast as issued 1, 2 and 3 days ahead (lead_day = floor((valid_time - issued_at) '
  '/ 24 h); issued_at is the NWPS cycle), for local hours 06, 09, 12, 15 and 18, the newest issue '
  'in each lead day''s window. Written by pipeline/accuracy_capture.py after the database push. '
  'RLS on with no policies: anon and authenticated read nothing.';

-- --------------------------------------------------------------------------------------------
-- model_at_buoys: the wave models at each roster buoy, every 3 hours, lead days 0-3
-- --------------------------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS model_at_buoys (
  buoy_id         TEXT        NOT NULL,
  valid_time      TIMESTAMPTZ NOT NULL,
  source          TEXT        NOT NULL CHECK (source IN ('ww3', 'nwps')),
  lead_day        SMALLINT    NOT NULL CHECK (lead_day BETWEEN 0 AND 3),
  issued_at       TIMESTAMPTZ NOT NULL,
  lead_hours      SMALLINT    NOT NULL,
  wfo             TEXT,
  cell_lat        REAL,
  cell_lng        REAL,
  cell_km         REAL,
  hs              REAL,
  tp              REAL,
  dp              REAL,
  swell_hs        REAL,
  swell_1_hs      REAL,
  swell_1_tp      REAL,
  swell_1_dp      REAL,
  swell_2_hs      REAL,
  swell_2_tp      REAL,
  swell_2_dp      REAL,
  swell_3_hs      REAL,
  swell_3_tp      REAL,
  swell_3_dp      REAL,
  wind_wave_hs    REAL,
  wind_wave_tp    REAL,
  wind_wave_dp    REAL,
  wind_speed      REAL,
  wind_dir        REAL,
  captured_at     TIMESTAMPTZ NOT NULL,
  run_id          TEXT,
  PRIMARY KEY (buoy_id, valid_time, source, lead_day)
);

CREATE INDEX IF NOT EXISTS idx_model_at_buoys_valid ON model_at_buoys (valid_time);

ALTER TABLE model_at_buoys ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE model_at_buoys IS
  'WAVEWATCH III (ww3, every buoy) and NWPS CG1 (nwps, buoys inside an office''s grid) sampled at '
  'the roster''s buoys (nearest and fallback, positions from ndbc_buoy_snapshot.json) every 3 '
  'hours UTC, lead_day = floor((valid_time - issued_at) / 24 h) from 0 to 3, the newest issue in '
  'each window. cell_* is the grid cell sampled and its distance from the buoy. Written by '
  'pipeline/accuracy_capture.py after the database push. RLS on with no policies.';

-- --------------------------------------------------------------------------------------------
-- Keep the newest issue in each lead day's window
-- --------------------------------------------------------------------------------------------
-- BEFORE UPDATE returning NULL skips that one row of an INSERT ... ON CONFLICT DO UPDATE; the rest
-- of the statement proceeds (the same mechanism as migration 019).

CREATE OR REPLACE FUNCTION accuracy_keep_newest_issue() RETURNS TRIGGER
LANGUAGE plpgsql
SET search_path = ''
AS $$
BEGIN
  IF NEW.issued_at < OLD.issued_at THEN
    RETURN NULL;   -- keep the row: a newer issue already wrote this lead day
  END IF;
  RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS forecast_leads_keep_newest_issue ON forecast_leads;
CREATE TRIGGER forecast_leads_keep_newest_issue
  BEFORE UPDATE ON forecast_leads
  FOR EACH ROW EXECUTE FUNCTION accuracy_keep_newest_issue();

DROP TRIGGER IF EXISTS model_at_buoys_keep_newest_issue ON model_at_buoys;
CREATE TRIGGER model_at_buoys_keep_newest_issue
  BEFORE UPDATE ON model_at_buoys
  FOR EACH ROW EXECUTE FUNCTION accuracy_keep_newest_issue();
