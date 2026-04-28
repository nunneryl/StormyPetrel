export type Spot = {
  id: number;
  slug: string;
  name: string;
  lat: number;
  lng: number;
  state: string | null;
  region: string | null;
  orientation_deg: number | null;
  offshore_wind_deg: number | null;
  optimal_swell_dir: number | null;
  break_type: string | null;
  tide_preference: string | null;
  crowd_factor: string | null;
  hazards: string[] | null;
  nearest_buoy_id: string | null;
  nearest_buoy_dist_km: number | null;
  nearest_tide_station_id: string | null;
};

export type Forecast = {
  spot_id: number;
  valid_time: string;
  hs: number | null;
  swell_hs: number | null;
  tp: number | null;
  dp: number | null;
  swell_tp: number | null;
  swell_dp: number | null;
  swell_1_hs: number | null;
  swell_1_tp: number | null;
  swell_1_dp: number | null;
  swell_2_hs: number | null;
  swell_2_tp: number | null;
  swell_2_dp: number | null;
  swell_3_hs: number | null;
  swell_3_tp: number | null;
  swell_3_dp: number | null;
  wind_wave_hs: number | null;
  wind_wave_tp: number | null;
  wind_wave_dp: number | null;
  swell_source: string | null;
  wind_speed: number | null;
  wind_dir: number | null;
  face_ft: number | null;
  dir_gain: number | null;
  wind_mult: number | null;
  tide_mult: number | null;
  chop_ratio: number | null;
  chop_mult: number | null;
  period_quality: number | null;
  effective_size_ft: number | null;
  stars: number | null;
  tide_level_ft: number | null;
};

export type BuoyObservation = {
  buoy_id: string;
  observed_at: string;
  hs: number | null;
  tp: number | null;
  dp: number | null;
  wind_speed: number | null;
  wind_dir: number | null;
  water_temp: number | null;
};

export type TidePrediction = {
  station_id: string;
  predicted_at: string;
  level_ft: number;
  type: string | null;
};

export type SpotWithLatest = Spot & {
  latest: Forecast | null;
};
