-- 007_cams.sql — surf cam registry. One row per (spot, cam) pairing.
--
-- Multiple cams can point at the same spot, and multiple spots can
-- share an upstream channel (e.g. Volusia County's beach feed covers
-- Daytona + New Smyrna + Ponce Inlet from one YouTube channel). The
-- resolver script keeps `resolved_video_id` + `embed_url` fresh for
-- youtube-provider rows; surfchex / explore rows store their iframe
-- URL once and don't need resolution.
--
-- Idempotent.

CREATE TABLE IF NOT EXISTS cams (
  id SERIAL PRIMARY KEY,
  spot_slug TEXT REFERENCES spots(slug),
  cam_name TEXT NOT NULL,
  provider TEXT NOT NULL,        -- 'youtube' | 'surfchex' | 'explore'
  channel_id TEXT,               -- YouTube channel ID (youtube provider only)
  iframe_url TEXT,               -- SurfChex / Explore static embed URL
  resolved_video_id TEXT,        -- current YouTube live video ID
  embed_url TEXT,                -- final embed URL the frontend renders
  attribution TEXT,
  attribution_url TEXT,
  status TEXT DEFAULT 'active',  -- 'active' | 'offline' | 'pending'
  last_resolved_at TIMESTAMPTZ,
  last_checked_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE (spot_slug, cam_name)
);

-- Two access patterns:
--   1. Per-spot lookup ("does this spot have a cam?"): spot_slug.
--   2. /cams page + the cam-badge join from spot listings: status.
CREATE INDEX IF NOT EXISTS cams_spot_slug_idx ON cams (spot_slug);
CREATE INDEX IF NOT EXISTS cams_status_idx    ON cams (status);
-- Resolver groups rows by channel_id so it only hits the YouTube API
-- once per channel even when many spots share one feed.
CREATE INDEX IF NOT EXISTS cams_channel_id_idx ON cams (channel_id)
  WHERE channel_id IS NOT NULL;

COMMENT ON TABLE cams IS
  'Surf cam registry. youtube provider rows are refreshed by '
  'pipeline.resolve_cams; surfchex/explore rows are static.';
