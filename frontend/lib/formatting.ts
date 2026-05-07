/**
 * Pick the swell-only value (period or direction) when NWPS published it,
 * else fall back to the total-spectrum value. Mirrors the rater logic in
 * pipeline/interpret.py — keeps the UI honest about what the rating saw.
 */
export function pickSwell<T extends number | null | undefined>(swell: T, total: T): T {
  if (swell !== null && swell !== undefined) return swell;
  return total;
}

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

export function fmtSec(v: number | null | undefined): string {
  if (v === null || v === undefined) return '—';
  return `${v.toFixed(0)}s`;
}

export function fmtMph(ms: number | null | undefined): string {
  const mph = msToMph(ms);
  if (mph === null) return '—';
  return `${mph.toFixed(0)} mph`;
}

const TIME_FMT = new Intl.DateTimeFormat('en-US', {
  hour: 'numeric',
  hour12: true,
  timeZone: 'America/Los_Angeles',
});

const DAY_FMT = new Intl.DateTimeFormat('en-US', {
  weekday: 'short',
  month: 'short',
  day: 'numeric',
  timeZone: 'America/Los_Angeles',
});

const SHORT_TIME_FMT = new Intl.DateTimeFormat('en-US', {
  hour: 'numeric',
  hour12: true,
  timeZone: 'America/Los_Angeles',
});

export function fmtHour(iso: string): string {
  return TIME_FMT.format(new Date(iso)).replace(' ', '').toLowerCase();
}

export function fmtDay(iso: string): string {
  return DAY_FMT.format(new Date(iso));
}

export function fmtShortTime(iso: string): string {
  return SHORT_TIME_FMT.format(new Date(iso)).replace(' ', '').toLowerCase();
}

const DAY_SHORT_FMT = new Intl.DateTimeFormat('en-US', {
  weekday: 'short',
  timeZone: 'America/Los_Angeles',
});

/** Combined "Wed 9am"-style tick label. Used by the 48h chart x-axis so
 *  each tick communicates BOTH the day and the time of day. */
export function fmtDayTimeTick(iso: string): string {
  const d = new Date(iso);
  const day = DAY_SHORT_FMT.format(d);
  const time = SHORT_TIME_FMT.format(d).replace(' ', '').toLowerCase();
  return `${day} ${time}`;
}

export function dayKey(iso: string): string {
  // YYYY-MM-DD bucket using local Pacific date so day boundaries feel right
  // for the largest segment of US users; can be overridden per-spot later.
  const fmt = new Intl.DateTimeFormat('en-CA', {
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    timeZone: 'America/Los_Angeles',
  });
  return fmt.format(new Date(iso));
}
