-- Stormy Petrel — initial database schema
-- Run via Supabase SQL editor or `supabase db push`. Idempotent: safe to
-- re-run; tables / indexes / triggers use IF NOT EXISTS or OR REPLACE.

CREATE EXTENSION IF NOT EXISTS postgis;

-- ---------------------------------------------------------------------------
-- spots — one row per surfable break.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS spots (
  id SERIAL PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  name TEXT NOT NULL,
  aka_names TEXT[],
  lat DOUBLE PRECISION NOT NULL,
  lng DOUBLE PRECISION NOT NULL,
  geom GEOMETRY(Point, 4326),
  state TEXT,
  region TEXT,
  orientation_deg DOUBLE PRECISION,
  offshore_wind_deg DOUBLE PRECISION,
  optimal_swell_dir DOUBLE PRECISION,
  swell_window_arcs JSONB,
  break_type TEXT,
  break_type_confidence DOUBLE PRECISION,
  tide_preference TEXT,
  crowd_factor TEXT,
  hazards TEXT[],
  nearest_buoy_id TEXT,
  nearest_buoy_dist_km DOUBLE PRECISION,
  nearest_tide_station_id TEXT,
  nearest_tide_station_dist_km DOUBLE PRECISION,
  nwps_wfo TEXT,
  data_sources JSONB,
  review_status TEXT DEFAULT 'auto',
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_spots_geom ON spots USING GIST(geom);
CREATE INDEX IF NOT EXISTS idx_spots_state ON spots(state);
CREATE INDEX IF NOT EXISTS idx_spots_slug ON spots(slug);

-- Auto-populate geom from lat/lng on insert/update so importers can stay
-- pure SQL and not worry about PostGIS WKT serialization.
CREATE OR REPLACE FUNCTION spots_set_geom() RETURNS TRIGGER AS $$
BEGIN
  IF NEW.lat IS NOT NULL AND NEW.lng IS NOT NULL THEN
    NEW.geom := ST_SetSRID(ST_MakePoint(NEW.lng, NEW.lat), 4326);
  END IF;
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS spots_set_geom_trg ON spots;
CREATE TRIGGER spots_set_geom_trg
  BEFORE INSERT OR UPDATE OF lat, lng ON spots
  FOR EACH ROW EXECUTE FUNCTION spots_set_geom();

-- updated_at maintainer.
CREATE OR REPLACE FUNCTION set_updated_at() RETURNS TRIGGER AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS spots_updated_at_trg ON spots;
CREATE TRIGGER spots_updated_at_trg
  BEFORE UPDATE ON spots
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

-- ---------------------------------------------------------------------------
-- forecasts — one row per spot per forecast hour. Uniqueness on
-- (spot_id, valid_time, source) so re-imports replace cleanly.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS forecasts (
  id BIGSERIAL PRIMARY KEY,
  spot_id INTEGER REFERENCES spots(id) ON DELETE CASCADE,
  valid_time TIMESTAMPTZ NOT NULL,
  hs DOUBLE PRECISION,
  tp DOUBLE PRECISION,
  dp DOUBLE PRECISION,
  wind_speed DOUBLE PRECISION,
  wind_dir DOUBLE PRECISION,
  swell_hs DOUBLE PRECISION,
  tide_level_ft DOUBLE PRECISION,
  tide_norm DOUBLE PRECISION,
  face_ft DOUBLE PRECISION,
  dir_gain DOUBLE PRECISION,
  wind_mult DOUBLE PRECISION,
  tide_mult DOUBLE PRECISION,
  effective_size_ft DOUBLE PRECISION,
  stars DOUBLE PRECISION,
  source TEXT DEFAULT 'nwps',
  fetched_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(spot_id, valid_time, source)
);

CREATE INDEX IF NOT EXISTS idx_forecasts_spot_time
  ON forecasts(spot_id, valid_time);
CREATE INDEX IF NOT EXISTS idx_forecasts_valid_time
  ON forecasts(valid_time);

-- ---------------------------------------------------------------------------
-- buoy_observations — NDBC realtime data, one row per (buoy, observed_at).
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS buoy_observations (
  id BIGSERIAL PRIMARY KEY,
  buoy_id TEXT NOT NULL,
  observed_at TIMESTAMPTZ NOT NULL,
  hs DOUBLE PRECISION,
  tp DOUBLE PRECISION,
  dp DOUBLE PRECISION,
  wind_speed DOUBLE PRECISION,
  wind_dir DOUBLE PRECISION,
  water_temp DOUBLE PRECISION,
  fetched_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(buoy_id, observed_at)
);

CREATE INDEX IF NOT EXISTS idx_buoy_obs_buoy_time
  ON buoy_observations(buoy_id, observed_at);

-- ---------------------------------------------------------------------------
-- tide_predictions — NOAA CO-OPS hilo + hourly predictions, one row per
-- (station, predicted_at). NOTE: predicted_at is stored in the station's
-- local time (LST/LDT) cast as UTC — CO-OPS doesn't include tz info on the
-- timestamps it returns under time_zone=lst_ldt. Downstream consumers know
-- to treat it as local; do not naively compare against UTC valid_time.
-- ---------------------------------------------------------------------------

CREATE TABLE IF NOT EXISTS tide_predictions (
  id BIGSERIAL PRIMARY KEY,
  station_id TEXT NOT NULL,
  predicted_at TIMESTAMPTZ NOT NULL,
  level_ft DOUBLE PRECISION NOT NULL,
  type TEXT,  -- 'H', 'L', or NULL for hourly
  fetched_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(station_id, predicted_at)
);

CREATE INDEX IF NOT EXISTS idx_tide_predictions_station_time
  ON tide_predictions(station_id, predicted_at);
