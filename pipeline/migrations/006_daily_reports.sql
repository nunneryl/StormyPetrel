-- 006_daily_reports.sql — AI-written morning surf reports, one per region per day.
--
-- Generated nightly by pipeline.daily_report. The frontend reads from this
-- table to render the homepage "Today's Surf Reports" rail and the
-- /reports + /reports/[date]/[region] pages.
--
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS daily_reports (
  id SERIAL PRIMARY KEY,
  region TEXT NOT NULL,
  region_label TEXT NOT NULL,
  report_date DATE NOT NULL,
  summary TEXT NOT NULL,
  top_spots JSONB NOT NULL,
  trend TEXT NOT NULL,
  generated_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (region, report_date)
);

-- Two access patterns:
--   1. Newest report per region: lookup by (region, report_date DESC).
--   2. Whole "today" payload for the homepage rail: lookup by report_date.
CREATE INDEX IF NOT EXISTS daily_reports_date_idx
  ON daily_reports (report_date DESC);

CREATE INDEX IF NOT EXISTS daily_reports_region_date_idx
  ON daily_reports (region, report_date DESC);

COMMENT ON TABLE daily_reports IS
  'AI-generated regional surf reports, one row per region per day.';
COMMENT ON COLUMN daily_reports.top_spots IS
  'JSONB array of {name, slug, stars, face_ft, state} snapshots, top 10 by current stars.';
COMMENT ON COLUMN daily_reports.trend IS
  'One of: building, steady, fading. Computed from avg face_ft now vs 24h.';
