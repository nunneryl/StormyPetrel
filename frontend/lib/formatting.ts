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

/**
 * The published SWELL HEIGHT, as a whole-foot band when one was measured and as the point
 * estimate when one was not.
 *
 * THE LABEL IS "SWELL HEIGHT", NOT "FACE". What the pipeline publishes is CDIP MOP
 * significant wave height at the 10-15 m contour, which CDIP states is generally outside
 * the surf zone. It is not a breaking face and is no longer called one. Only the DISPLAY
 * changed: face_ft / face_lo_ft / face_hi_ft keep their names in the database, the API and
 * every query, because those are parsed and this is not.
 *
 * WHOLE FEET. The measured band is roughly +/-20%, so a tenth of a foot is precision the
 * measurement does not have; "3-5 ft" is the honest resolution and "3.3-4.9 ft" is not.
 *
 * NEVER "3-3 ft". When both ends round to the same whole foot the band is narrower than
 * the display can show, and the honest render is that single number — a repeated bound
 * reads as a bug and tells the reader nothing the single value did not.
 *
 * NO BAND MEANS NO BAND. 466 of 648 spots have no measured spread, and they fall through
 * to the point estimate rather than to a default width. A default would be indistinguishable
 * from a measured one to anybody reading the site, which is precisely why there isn't one.
 * The two states ARE distinguishable on the page: a band renders as "3-5 ft" and an
 * unmeasured spot as "4.0ft", so the presence of a range is itself the signal that a
 * spread was measured.
 */
export function fmtFtRange(
  lo: number | null | undefined,
  hi: number | null | undefined,
  point: number | null | undefined,
): string {
  if (lo === null || lo === undefined || hi === null || hi === undefined) {
    return fmtFt(point);
  }
  if (Number.isNaN(lo) || Number.isNaN(hi)) return fmtFt(point);
  // THE INVARIANT, AS A BACKSTOP. lo <= point <= hi is guaranteed by construction upstream
  // (all three divide one raw face by three quantiles of one distribution), and the
  // pipeline now asserts it at the point of writing. It is re-checked here because these
  // arrive as three independent database columns and a stale row from an older pipeline
  // run carries an older arithmetic.
  //
  // WHAT WENT WRONG WITHOUT IT: the band was computed from the already-corrected face and
  // so divided twice. Steamer Lane published face 1.45 with lo 0.45 and hi 0.65, and this
  // function rendered "0-1ft" — a well-ordered band, so the lo>hi swap below never fired,
  // and nothing else looked. It took a manual SQL query to find. LOUD, not silent: a band
  // that cannot contain its own point is dropped and the point published alone, and the
  // violation is logged with the numbers so it shows up in the server log rather than
  // only on the page.
  if (point !== null && point !== undefined && !Number.isNaN(point)) {
    const lowest = Math.min(lo, hi);
    const highest = Math.max(lo, hi);
    if (point < lowest || point > highest) {
      console.error(
        `fmtFtRange: band [${lo}, ${hi}] does not contain its point ${point} — ` +
          `publishing the point alone. The three values must divide one raw face by ` +
          `p75 / median / p25; a band that misses its point means they did not.`,
      );
      return fmtFt(point);
    }
  }
  const l = Math.round(lo);
  const h = Math.round(hi);
  // Guard the ordering rather than assuming it. The pipeline divides by p75 and p25 so
  // lo <= hi holds by construction, but this function is also fed straight from database
  // columns, and a swapped pair should render as a band rather than as "5-3 ft".
  const [a, b] = l <= h ? [l, h] : [h, l];
  if (a === b) return `${a}ft`;
  return `${a}-${b}ft`;
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
