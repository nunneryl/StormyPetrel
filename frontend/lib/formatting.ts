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
 * EXPAND OUTWARD, NEVER TO NEAREST — floor the low end, ceiling the high end.
 *
 *   WHAT WENT WRONG. This rounded both ends to nearest, so the two ends could land on the
 *   same whole foot and the band VANISHED. Measured over 17,030 banded future hours, 5,566
 *   collapsed — 32.7%. Steamer Lane rendered a bare "2ft" from a real 2.52-2.87 band, and a
 *   reader could not tell it from one of the 466 spots that were never measured at all. The
 *   collapse was worst exactly where the band is narrowest, which is where a reader most
 *   needs to be told the number is soft.
 *
 *   WHY OUTWARD IS THE RIGHT DIRECTION, and this is the part not to revert: expanding
 *   outward can only ever OVERSTATE the uncertainty, never understate it. For a quantity
 *   published with a measured +/-20% median spread, which we have been careful not to
 *   overclaim, erring wide is erring correctly. Round-to-nearest does the opposite — it can
 *   make the published band NARROWER than the one that was measured, and at 32.7% of hours
 *   it made it disappear.
 *
 *   THE COST, STATED. Floor+ceiling adds up to a foot at each end, so the displayed width is
 *   the measured width plus up to two feet, against plus-or-minus one before. At the roster
 *   median band a 4 ft face is unchanged ("3-5 ft" either way); a 6 ft face goes from
 *   "5-7 ft" to "4-8 ft". That is the price of never collapsing, and it is paid in a
 *   direction the reader can trust.
 *
 *   HALF-FOOT STEPS WERE CONSIDERED AND REJECTED. They still collapse to "2.5-2.5 ft" on
 *   ~3.5% of hours — a visibly fake range — and a decimal implies six-inch precision that
 *   the measurement does not have.
 *
 * SUB-FOOT BANDS READ "under N ft", NOT "0-N ft". A floored low end of 0 says "possibly
 * flat", and the measurement does not support that for a low end of, say, 0.95 ft. The site
 * already made this call once: StarRating refuses to draw zero stars and prints the word
 * FLAT instead, because a numeric zero at the bottom of a scale reads as missing data rather
 * than as a small value. Same reasoning, same answer — a word, not a zero. It stays an
 * outward expansion, since "under N" contains [0, N]. Given the p75/p25 <= 1.7 exclusion
 * rule the band's own ratio is at most 1.7, so a floored-to-zero low end forces a high end
 * under 1.7 and this form can only ever be "under 1ft" or "under 2ft".
 *
 * NO BAND MEANS NO BAND. 466 of 648 spots have no measured spread, and they fall through
 * to the point estimate rather than to a default width. A default would be indistinguishable
 * from a measured one to anybody reading the site, which is precisely why there isn't one.
 * The two states ARE distinguishable on the page, and after the outward-expansion change
 * that distinction is now EXACT rather than approximate: a measured spot always renders a
 * range, an unmeasured one always renders a bare "4.0ft". Before, a measured spot whose band
 * was tighter than a foot also rendered a bare point, and the three states were two.
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
  // ORDER FIRST, THEN EXPAND — and that order matters. The pipeline divides by p75 and p25
  // so lo <= hi holds by construction, but this function is fed straight from database
  // columns and a swapped pair must still render low-to-high. Swapping AFTER rounding would
  // silently narrow such a pair: [5.2, 3.1] would floor/ceil to 5 and 4, swap to "4-5ft",
  // and publish a band INSIDE the measured one — the exact understatement this change exists
  // to remove. Ordering first gives "3-6ft".
  const lowest = Math.min(lo, hi);
  const highest = Math.max(lo, hi);
  const a = Math.floor(lowest);
  // THREE FLOORS ON THE HIGH END, in one expression because they interact:
  //   Math.ceil(highest)  the outward expansion itself;
  //   a + 1               the minimum publishable width. floor(x) <= ceil(y) for x <= y so
  //                       b < a is impossible, but b === a is reachable when both ends are
  //                       the SAME whole number (p25 === p75 — a record with no measured
  //                       spread, which no committed factor has, so a guard not a case);
  //   1                   because a negative bound must not reach the string. A wholly
  //                       negative band floors and ceilings to negatives and rendered
  //                       "under -1ft" without this — found by a test written for the
  //                       neighbouring case, not by reading.
  const b = Math.max(Math.ceil(highest), a + 1, 1);
  if (a <= 0) return `under ${b}ft`;
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
