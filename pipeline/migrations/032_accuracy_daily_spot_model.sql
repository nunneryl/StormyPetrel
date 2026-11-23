-- 032_accuracy_daily_spot_model.sql
-- accuracy_daily (migration 031) gains a third kind, 'spot_model', and per-spot rows.
--
--   kind 'spot_model', source 'nwps'  the spot forecast's wave height against NWPS AT THE SPOT'S
--                                     BUOY (model_at_buoys, migration 030): the same lead day, the
--                                     3-hourly time nearest the spot's hour (within 90 minutes,
--                                     the earlier on a tie). f is the spot, o is NWPS at the buoy.
--                                     Only spots whose nearest buoy is within 25 km, as kind 'spot'.
--   point = the spot's slug           kinds 'spot' and 'spot_model', wave height only, every lead
--                                     day: that spot alone, for the weekly report's 20 spots
--                                     furthest from NWPS at their buoy.
--
-- No new column: kind's CHECK widens. Size: at most about 2,400 more rows a day (about 290
-- spots x 4 lead days x 2 kinds, and 16 regions x 4 lead days), about 0.13 GB a year.
--
-- Applied in the Supabase SQL editor on 2026-11-23, before the code that writes it.
--
-- CHECK, before: CHECK ((kind = ANY (ARRAY['buoy'::text, 'spot'::text])))
-- CHECK, after:  CHECK ((kind = ANY (ARRAY['buoy'::text, 'spot'::text, 'spot_model'::text])))
--   SELECT pg_get_constraintdef(oid) FROM pg_constraint WHERE conname = 'accuracy_daily_kind_check';
--
-- CHECK, after the first nightly scoring run with this code: per-spot rows and spot_model rows.
--   SELECT day, kind, count(*) FILTER (WHERE point = '') AS region_rows,
--          count(*) FILTER (WHERE point <> '') AS point_rows
--   FROM accuracy_daily WHERE day >= current_date - 3 GROUP BY day, kind ORDER BY day DESC, kind;

ALTER TABLE accuracy_daily DROP CONSTRAINT IF EXISTS accuracy_daily_kind_check;
ALTER TABLE accuracy_daily ADD CONSTRAINT accuracy_daily_kind_check
  CHECK (kind IN ('buoy', 'spot', 'spot_model'));

COMMENT ON COLUMN accuracy_daily.kind IS
  'buoy: model at the buoy vs the buoy; spot: spot forecast vs its buoy within 25 km; '
  'spot_model: spot forecast vs NWPS at that buoy (model_at_buoys, same lead day, nearest '
  '3-hour time). point is the buoy id or the spot slug for one point, '''' for the region.';
