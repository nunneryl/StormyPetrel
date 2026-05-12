-- 008_cam_display_mode.sql — split cams into embed (iframe in-page)
-- vs link (external-only). Some providers block iframing or have a
-- nicer first-party experience that's better as a link-out.
--
-- The frontend reads this column to decide whether to render a
-- responsive iframe or a banner card with a "Watch live on …" button.

ALTER TABLE cams
  ADD COLUMN IF NOT EXISTS display_mode TEXT NOT NULL DEFAULT 'embed';

-- Backfill: anything not youtube/explore is link-out by default. Idem-
-- potent — uses the post-add column directly so re-running the file
-- doesn't blow away curated overrides.
UPDATE cams
   SET display_mode = 'link'
 WHERE provider NOT IN ('youtube', 'explore')
   AND display_mode = 'embed';

COMMENT ON COLUMN cams.display_mode IS
  '''embed'' = render an iframe on the spot page; ''link'' = show a banner card with a Watch-live external link.';
