-- 033_site_errors.sql
-- Site errors, one row per kind of error, for the error alerts (frontend/lib/siteErrors.ts).
--
-- WHAT. The site records every error a visitor hits: server errors from Next's onRequestError
-- (frontend/instrumentation.ts) and browser errors POSTed by the error pages to
-- /api/client-error. Each error is FINGERPRINTED (source + normalised message + route) and
-- UPSERTED, so a repeat bumps a count and never adds a row. Nothing personal is stored: no IP
-- address, no browser details, no cookies, no query string.
--
-- Run in the Supabase SQL editor BEFORE merging the code that writes it. Idempotent: safe to
-- run again. Until it is run the site still works; recording an error just fails quietly.
--
-- CHECK 1, after running:
--   SELECT column_name, data_type FROM information_schema.columns
--   WHERE table_schema = 'public' AND table_name = 'site_errors' ORDER BY ordinal_position;
--   -> 11 rows.
-- CHECK 2, the two functions answer (the first call inserts one test row; the delete removes it):
--   SELECT * FROM record_site_error('migration-check', 'browser', '/', 'migration check', '/', NULL);
--   DELETE FROM site_errors WHERE fingerprint = 'migration-check';
--   SELECT try_site_alert_lease(1);   -> true

CREATE TABLE IF NOT EXISTS site_errors (
  fingerprint   TEXT        PRIMARY KEY,
  source        TEXT        NOT NULL CHECK (source IN ('server', 'browser')),
  route         TEXT        NOT NULL,            -- the route pattern, e.g. /spot/[slug]
  message       TEXT        NOT NULL,            -- normalised, at most 500 characters
  sample_path   TEXT,                            -- the latest path it happened on, no query string
  digest        TEXT,                            -- Next's error digest, when there is one
  count         BIGINT      NOT NULL DEFAULT 1,
  first_seen    TIMESTAMPTZ NOT NULL DEFAULT now(),
  last_seen     TIMESTAMPTZ NOT NULL DEFAULT now(),
  -- The alert window: when the current window opened and how many errors it has had. A repeat
  -- more than p_window_minutes after window_start opens a new window at 1.
  window_start  TIMESTAMPTZ NOT NULL DEFAULT now(),
  window_count  INTEGER     NOT NULL DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_site_errors_last_seen ON site_errors (last_seen DESC);
CREATE INDEX IF NOT EXISTS idx_site_errors_first_seen ON site_errors (first_seen);

-- Service role only, like every other table: RLS on, no policies.
ALTER TABLE site_errors ENABLE ROW LEVEL SECURITY;

COMMENT ON TABLE site_errors IS
  'One row per error fingerprint (source + normalised message + route), upserted by '
  'record_site_error: count, first and last seen, a sample path, and the 15-minute alert window. '
  'No IP address, browser details, cookies or query strings. Written by the site with the service '
  'role; read by /admin/errors and the weekly accuracy report.';

-- Record one error, atomically: insert or bump the fingerprint's row, roll its alert window, and
-- return the window's count. A NEW browser fingerprint is refused (accepted = false) once
-- p_browser_daily_cap browser fingerprints have first appeared in the last day, so a script
-- POSTing junk to /api/client-error cannot grow the table without limit. Server errors and
-- repeats of known fingerprints are always recorded.
CREATE OR REPLACE FUNCTION record_site_error(
  p_fingerprint       TEXT,
  p_source            TEXT,
  p_route             TEXT,
  p_message           TEXT,
  p_path              TEXT,
  p_digest            TEXT,
  p_window_minutes    INTEGER DEFAULT 15,
  p_browser_daily_cap INTEGER DEFAULT 500
) RETURNS TABLE (window_count INTEGER, accepted BOOLEAN)
LANGUAGE plpgsql
SECURITY INVOKER
SET search_path = public
AS $$
DECLARE
  wc INTEGER;
BEGIN
  IF p_source = 'browser'
     AND NOT EXISTS (SELECT 1 FROM site_errors s WHERE s.fingerprint = p_fingerprint)
     AND (SELECT count(*) FROM site_errors s
          WHERE s.source = 'browser' AND s.first_seen > now() - interval '1 day') >= p_browser_daily_cap
  THEN
    RETURN QUERY SELECT 0, false;
    RETURN;
  END IF;

  INSERT INTO site_errors AS s (fingerprint, source, route, message, sample_path, digest)
  VALUES (p_fingerprint, p_source, left(p_route, 300), left(p_message, 500), left(p_path, 300), left(p_digest, 100))
  ON CONFLICT (fingerprint) DO UPDATE SET
    count        = s.count + 1,
    last_seen    = now(),
    sample_path  = EXCLUDED.sample_path,
    digest       = COALESCE(EXCLUDED.digest, s.digest),
    window_start = CASE WHEN s.window_start < now() - make_interval(mins => p_window_minutes)
                        THEN now() ELSE s.window_start END,
    window_count = CASE WHEN s.window_start < now() - make_interval(mins => p_window_minutes)
                        THEN 1 ELSE s.window_count + 1 END
  RETURNING s.window_count INTO wc;

  RETURN QUERY SELECT wc, true;
END;
$$;

-- One lease, so only one server instance at a time opens, comments on or closes the
-- site-critical GitHub issue: a burst of errors on several instances must not open several
-- issues. try_site_alert_lease(seconds) returns true to exactly one caller until the lease runs
-- out.
CREATE TABLE IF NOT EXISTS site_alert_lease (
  id          INTEGER     PRIMARY KEY CHECK (id = 1),
  held_until  TIMESTAMPTZ NOT NULL DEFAULT 'epoch'
);
INSERT INTO site_alert_lease (id) VALUES (1) ON CONFLICT (id) DO NOTHING;
ALTER TABLE site_alert_lease ENABLE ROW LEVEL SECURITY;

CREATE OR REPLACE FUNCTION try_site_alert_lease(p_seconds INTEGER DEFAULT 60) RETURNS BOOLEAN
LANGUAGE sql
SECURITY INVOKER
SET search_path = public
AS $$
  WITH taken AS (
    UPDATE site_alert_lease
       SET held_until = now() + make_interval(secs => p_seconds)
     WHERE id = 1 AND held_until < now()
    RETURNING 1
  )
  SELECT EXISTS (SELECT 1 FROM taken);
$$;

-- The site calls both functions with the service role; nobody else may.
REVOKE ALL ON FUNCTION record_site_error(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER) FROM PUBLIC, anon, authenticated;
REVOKE ALL ON FUNCTION try_site_alert_lease(INTEGER) FROM PUBLIC, anon, authenticated;
GRANT EXECUTE ON FUNCTION record_site_error(TEXT, TEXT, TEXT, TEXT, TEXT, TEXT, INTEGER, INTEGER) TO service_role;
GRANT EXECUTE ON FUNCTION try_site_alert_lease(INTEGER) TO service_role;
