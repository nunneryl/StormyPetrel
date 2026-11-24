/**
 * WHAT /map AND /region/* SEND THE BROWSER for each spot: a dozen small, already-computed fields,
 * not the spot row and its forecast row.
 *
 * WHY. Both pages handed their client component the full spots row (36 columns, the research
 * evidence text and source URLs included) and the spot's whole forecast row (about 40 columns),
 * then derived what they show in the browser. On a production build with the 648-spot roster
 * that was 1.73 MB of HTML for /map and 1.51 MB for /region/california. The derivations
 * (shownTrain, heightBasis, fmtBreakType, classifyWind) now run on the server, once, and the
 * browser gets their answers.
 *
 * Numbers are rounded to what the pages print: they format heights to the foot band, periods to
 * the second, directions to the degree. Coordinates keep 4 decimals (about 11 m).
 */
import { heightBasis, type HeightBasis } from './heightBasis.ts';
import { fmtBreakType } from './provenance.ts';
import { classifyWind, type WindQuality } from './ratings.ts';
import { shownTrain } from './shownPeriod.ts';
import type { SpotWithLatest } from './types.ts';

export type ListSpot = {
  id: number;
  slug: string;
  name: string;
  state: string | null;
  lat: number | null;
  lng: number | null;
  /** "reef", "beach (unverified)", ... (lib/provenance fmtBreakType), null with no break type. */
  breakLabel: string | null;
  /** The current rating; null when there is none (lib/ratings ratingState). */
  stars: number | null;
  faceFt: number | null;
  basis: HeightBasis | null;
  /** The shown train's period and direction (lib/shownPeriod shownTrain). */
  tp: number | null;
  dp: number | null;
  /** m/s, as the forecasts table stores it; the pages convert to mph. */
  windSpeed: number | null;
  windDir: number | null;
  windQuality: WindQuality;
  tideFt: number | null;
  tideTrend: 'rising' | 'falling' | null;
};

function round(v: number | null | undefined, places: number): number | null {
  if (v === null || v === undefined || !Number.isFinite(v)) return null;
  const k = 10 ** places;
  return Math.round(v * k) / k;
}

export function toListSpot(s: SpotWithLatest): ListSpot {
  const f = s.latest;
  const { tp, dp } = shownTrain(f, s.swell_window_arcs, s.orientation_deg);
  return {
    id: s.id,
    slug: s.slug,
    name: s.name,
    state: s.state ?? null,
    lat: round(s.lat, 4),
    lng: round(s.lng, 4),
    breakLabel: s.break_type ? fmtBreakType(s.break_type, s.break_type_source) ?? null : null,
    stars: f?.stars ?? null,
    faceFt: round(f?.face_ft, 2),
    basis: heightBasis(f),
    tp: round(tp, 1),
    dp: round(dp, 0),
    windSpeed: round(f?.wind_speed, 2),
    windDir: round(f?.wind_dir, 0),
    windQuality: classifyWind(f?.wind_dir ?? null, s.offshore_wind_deg ?? null),
    tideFt: round(f?.tide_level_ft, 2),
    tideTrend: s.tide_trend ?? null,
  };
}
