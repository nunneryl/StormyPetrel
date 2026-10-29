const CARDINAL_16 = [
  'N', 'NNE', 'NE', 'ENE',
  'E', 'ESE', 'SE', 'SSE',
  'S', 'SSW', 'SW', 'WSW',
  'W', 'WNW', 'NW', 'NNW',
];

export function degToCardinal(deg: number | null | undefined): string {
  if (deg === null || deg === undefined || Number.isNaN(deg)) return '—';
  const norm = ((deg % 360) + 360) % 360;
  const idx = Math.round(norm / 22.5) % 16;
  return CARDINAL_16[idx];
}

export function msToMph(ms: number | null | undefined): number | null {
  if (ms === null || ms === undefined) return null;
  return ms * 2.23694;
}

export function metersToFeet(m: number | null | undefined): number | null {
  if (m === null || m === undefined) return null;
  return m * 3.28084;
}

export function fmtNum(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return v.toFixed(digits);
}

export function fmtFt(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return `${v.toFixed(1)}ft`;
}

/**
 * The surf height (the headline height) as a READING RANGE: the standard step of a fixed ladder that the height
 * falls in. One meaning everywhere, at every spot:
 *
 *   under 1 · 1-2 · 2-3 · 3-4 · 4-5 · 5-6 · 6-8 · 8-10 · 10-12 · 12-15 · 15-20 · 20-25 ·
 *   25-30, then 5 ft steps (30-35, 35-40, ...)
 *
 * A height exactly on a boundary goes to the HIGHER step: 6.0 ft reads "6-8ft", 5.99 "5-6ft".
 *
 * THE HEIGHT IS face_ft, THE ONE THE PAGE SHOWS. That is the calibrated height at the
 * calibrated California spots, CDIP's own on a MOP-fed hour and the model's everywhere else;
 * lib/heightBasis.ts says which, beside every height this string is printed with.
 *
 * WHAT IT IS NOT. Until October 2026 this printed face_lo_ft-face_hi_ft, a band divided out of
 * each calibrated spot's measured p25/p75 face ratio, and the 500-odd spots without one printed
 * a bare "4.3ft". Two meanings, and most spots had no range at all. A step on a fixed ladder is
 * a reading convention, not a measurement: individual waves in an hour vary more than any one
 * step. The pipeline writes NULL to face_lo_ft / face_hi_ft (migration 023 retires them) and
 * nothing here reads them.
 *
 * '—' when there is no height.
 */
export const HEIGHT_STEPS_FT: readonly number[] = [1, 2, 3, 4, 5, 6, 8, 10, 12, 15, 20, 25, 30];

export function fmtHeightRange(point: number | null | undefined): string {
  if (point === null || point === undefined || Number.isNaN(point)) return '—';
  if (point < HEIGHT_STEPS_FT[0]) return `under ${HEIGHT_STEPS_FT[0]}ft`;
  const top = HEIGHT_STEPS_FT[HEIGHT_STEPS_FT.length - 1];
  if (point >= top) {
    const lo = top + 5 * Math.floor((point - top) / 5);
    return `${lo}-${lo + 5}ft`;
  }
  // The largest step at or below the height: a boundary belongs to the step it starts.
  let i = 0;
  while (point >= HEIGHT_STEPS_FT[i + 1]) i++;
  return `${HEIGHT_STEPS_FT[i]}-${HEIGHT_STEPS_FT[i + 1]}ft`;
}

export function fmtSec(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return `${v.toFixed(0)}s`;
}

export function fmtMph(ms: number | null | undefined): string {
  const mph = msToMph(ms);
  if (mph === null) return '—';
  return `${mph.toFixed(0)} mph`;
}

// Clock times and dates for a spot are in lib/spotTime.ts, in the spot's own time zone.
