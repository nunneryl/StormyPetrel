// Hand-curated preset spots used by the /learn/swell-direction and
// /learn/wind interactive maps. Geometry is verified against real
// shorelines so the "spot faces" arrow always points over open water.
// SwellDirectionMap augments these with swell-window arcs +
// optimal_swell_dir; WindDirectionMap consumes them as-is.

export type LearnSpotCore = {
  slug: string;
  label: string;
  lat: number;
  lng: number;
  /** Seaward-facing bearing in degrees, 0..359. */
  orientationDeg: number;
};

export const LEARN_SPOT_PRESETS: LearnSpotCore[] = [
  { slug: 'banzai-pipeline',          label: 'Pipeline, HI',         lat: 21.6651, lng: -158.0539, orientationDeg: 315 },
  { slug: 'huntington-beach-pier',    label: 'Huntington Beach, CA', lat: 33.6553, lng: -117.9988, orientationDeg: 220 },
  { slug: 'narragansett-beach',       label: 'Narragansett, RI',     lat: 41.4490, lng:  -71.4545, orientationDeg: 170 },
  { slug: 'sebastian-inlet',          label: 'Sebastian Inlet, FL',  lat: 27.8576, lng:  -80.4487, orientationDeg:  80 },
  { slug: 'rincon',                   label: 'Rincon, CA',           lat: 34.3731, lng: -119.4782, orientationDeg: 210 },
  { slug: 'cape-hatteras-lighthouse', label: 'Cape Hatteras, NC',    lat: 35.2228, lng:  -75.5356, orientationDeg: 110 },
];
