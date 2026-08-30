/**
 * Surf-report feedback: the size ladder, the validation rules, and the submit flow.
 *
 * This module is the ONE place the vocabulary and its feet mapping live. It is deliberately
 * import-free — no React, no Supabase, no formatting.ts — so that the API route, the client
 * component and the zero-dependency test file can all read it, and so the tests run under
 * bare `node --experimental-strip-types` with nothing installed.
 *
 * The database side is injected (see ReportDb) rather than imported, for the same reason:
 * lib/supabase.ts throws at import time when the env is absent, which would make this file
 * untestable. The route supplies a real ReportDb; the tests supply a fake.
 */

// --------------------------------------------------------------------------- //
// The size ladder                                                              //
// --------------------------------------------------------------------------- //

/** Ascending. The order IS the ordinal scale; the CHECK constraint in
 *  pipeline/migrations/014_surf_reports.sql lists exactly these values. */
export const SIZE_BUCKETS = [
  'ankle',
  'knee',
  'thigh',
  'waist',
  'chest',
  'shoulder',
  'head',
  'overhead',
  'well_overhead',
  'double_overhead',
  'triple_overhead_plus',
] as const;

export type SizeBucket = (typeof SIZE_BUCKETS)[number];

/** What the reporter sees. Kept next to the stored value so the two can never drift. */
export const SIZE_BUCKET_LABELS: Record<SizeBucket, string> = {
  ankle: 'Ankle',
  knee: 'Knee',
  thigh: 'Thigh',
  waist: 'Waist',
  chest: 'Chest',
  shoulder: 'Shoulder',
  head: 'Head high',
  overhead: 'Overhead',
  well_overhead: 'Well overhead',
  double_overhead: 'Double overhead',
  triple_overhead_plus: 'Triple overhead +',
};

/**
 * Bucket -> face height in feet. THE ONE PLACE THIS MAPPING EXISTS.
 *
 * THIS IS AN ASSUMPTION, NOT A MEASUREMENT, and four parts of it are contestable:
 *
 *  1. IT ASSUMES A ~6 FT REPORTER. "Head high" is ~6 ft of face only for a ~6 ft surfer;
 *     for a 5'4" reporter it is ~5.3 ft. The scale is a RATIO to the reporter's own body
 *     and this table collapses that to a constant — plausibly the largest single error
 *     source in a label, of order +/-15%.
 *
 *  2. IT IS THE MAINLAND FACE SCALE. Hawaii reads roughly HALF the face: "6 foot Hawaiian"
 *     is ~10-12 ft of face. There are 89 Hawaii spots in the roster, and Hawaii is one of
 *     only two states where the model currently correlates POSITIVELY with buoys — so
 *     mis-scaling HI labels would corrupt exactly the region where the signal is best.
 *     HAWAII LABELS MUST BE HELD OUT OF THE FIRST CALIBRATION PASS until enough HI reports
 *     exist to test whether they cluster at about half this mapping. That test is itself a
 *     finding; do not assume the answer either way.
 *
 *  3. THE BUCKETS ARE NOT EVENLY INFORMATIVE. Steps are ~1 ft at the bottom and 2-4 ft at
 *     the top, mirroring how people talk. So the error metric is heteroscedastic: an
 *     "overhead" label carries ~1.5 ft of intrinsic uncertainty against ~0.5 ft for "knee".
 *     Weight by bucket width when computing bias.
 *
 *  4. IT MEANS THE AVERAGE OF THE SETS RIDDEN, matching the panel copy. A label meaning
 *     "the biggest one" would be a different statistic from the one face_ft publishes.
 *
 * Because all four are revisable, surf_reports stores the BUCKET LABEL and never feet.
 * Change this table and every historical report re-derives; store feet and they cannot.
 */
export const BUCKET_FACE_FT: Record<SizeBucket, number> = {
  ankle: 0.5,
  knee: 1.5,
  thigh: 2.25,
  waist: 3,
  chest: 4,
  shoulder: 5,
  head: 6,
  overhead: 7,
  well_overhead: 9,
  double_overhead: 11,
  triple_overhead_plus: 15,
};

export const RATING_VERDICTS = ['too_low', 'about_right', 'too_high'] as const;
export type RatingVerdict = (typeof RATING_VERDICTS)[number];

export const RATING_VERDICT_LABELS: Record<RatingVerdict, string> = {
  too_low: 'Underrated',
  about_right: 'About right',
  too_high: 'Overrated',
};

/** How far back a report may reach. Beyond this, recall is poor enough that the label is
 *  worth less than the noise it adds — and the forecast row for the hour has long since
 *  drifted to a nowcast anyway (see the forecast_face_ft column comment). */
export const MAX_REPORT_AGE_HOURS = 48;

/** How many hours the picker offers, counting the current hour. Deliberately shorter than
 *  MAX_REPORT_AGE_HOURS: 12 covers "this morning" from any time of day, and a longer list
 *  is a worse control. The route accepts anything inside 48 h, so widening the picker later
 *  needs no server change. */
export const HOUR_PICKER_SPAN = 12;

export function isSizeBucket(v: unknown): v is SizeBucket {
  return typeof v === 'string' && (SIZE_BUCKETS as readonly string[]).includes(v);
}

export function isRatingVerdict(v: unknown): v is RatingVerdict {
  return typeof v === 'string' && (RATING_VERDICTS as readonly string[]).includes(v);
}

// --------------------------------------------------------------------------- //
// Hours                                                                        //
// --------------------------------------------------------------------------- //

const MS_PER_HOUR = 3_600_000;

/** Milliseconds since epoch, floored to the top of the UTC hour. */
export function floorToHourMs(ms: number): number {
  return Math.floor(ms / MS_PER_HOUR) * MS_PER_HOUR;
}

/** The UTC hour containing *ms*, as the ISO string forecasts.valid_time is stored in. */
export function toUtcHourIso(ms: number): string {
  return new Date(floorToHourMs(ms)).toISOString();
}

/**
 * The hours the picker offers: the current UTC hour first, then backwards.
 * Returned newest-first because that is the order the control renders and the default
 * selection is the head of the list.
 */
export function hourOptions(nowMs: number, span: number = HOUR_PICKER_SPAN): string[] {
  const top = floorToHourMs(nowMs);
  const out: string[] = [];
  for (let i = 0; i < span; i += 1) {
    out.push(new Date(top - i * MS_PER_HOUR).toISOString());
  }
  return out;
}

// --------------------------------------------------------------------------- //
// Request validation                                                           //
// --------------------------------------------------------------------------- //

export type ReportInput = {
  slug: string;
  observedHourIso: string;
  sizeBucket: SizeBucket;
  ratingVerdict: RatingVerdict | null;
};

export type ValidationResult =
  | { ok: true; value: ReportInput }
  | { ok: false; error: string };

/**
 * Validate and normalise a submitted body. Pure: *nowMs* is passed in so the time-window
 * rules are testable without touching the clock.
 *
 * observed_hour is floored to the UTC hour BEFORE the window checks, so a timestamp a few
 * minutes into the current hour is not "in the future" — the hour it names has started.
 */
export function validateReport(raw: unknown, nowMs: number): ValidationResult {
  if (raw === null || typeof raw !== 'object') {
    return { ok: false, error: 'body must be a JSON object' };
  }
  const body = raw as Record<string, unknown>;

  const slug = typeof body.slug === 'string' ? body.slug.trim() : '';
  if (!slug) {
    return { ok: false, error: 'slug is required' };
  }

  if (typeof body.observed_hour !== 'string') {
    return { ok: false, error: 'observed_hour is required' };
  }
  const parsedMs = Date.parse(body.observed_hour);
  if (Number.isNaN(parsedMs)) {
    return { ok: false, error: 'observed_hour is not a valid timestamp' };
  }
  const hourMs = floorToHourMs(parsedMs);
  const currentHourMs = floorToHourMs(nowMs);
  if (hourMs > currentHourMs) {
    return { ok: false, error: 'observed_hour is in the future' };
  }
  // Inclusive at the boundary: exactly MAX_REPORT_AGE_HOURS old is still accepted.
  if (currentHourMs - hourMs > MAX_REPORT_AGE_HOURS * MS_PER_HOUR) {
    return { ok: false, error: `observed_hour is older than ${MAX_REPORT_AGE_HOURS} hours` };
  }

  if (!isSizeBucket(body.size_bucket)) {
    return { ok: false, error: 'size_bucket is not one of the allowed values' };
  }

  // Absent and null both mean "skipped" — the question is optional and never blocks submit.
  const rawVerdict = body.rating_verdict;
  let ratingVerdict: RatingVerdict | null = null;
  if (rawVerdict !== undefined && rawVerdict !== null) {
    if (!isRatingVerdict(rawVerdict)) {
      return { ok: false, error: 'rating_verdict is not one of the allowed values' };
    }
    ratingVerdict = rawVerdict;
  }

  return {
    ok: true,
    value: {
      slug,
      observedHourIso: new Date(hourMs).toISOString(),
      sizeBucket: body.size_bucket,
      ratingVerdict,
    },
  };
}

// --------------------------------------------------------------------------- //
// Submit                                                                       //
// --------------------------------------------------------------------------- //

export type ForecastSnapshot = { face_ft: number | null; stars: number | null };

/** The three database operations a submit needs, injected so this module stays import-free
 *  and the flow is testable without a database. */
export type ReportDb = {
  /** null when the slug matches no spot. */
  findSpotId(slug: string): Promise<number | null>;
  /** null when we hold NO forecast row for that exact spot-hour. */
  findForecast(spotId: number, observedHourIso: string): Promise<ForecastSnapshot | null>;
  /** `duplicate` is the UNIQUE(spot_id, observed_hour, reporter_hash) conflict. */
  insert(row: ReportRow): Promise<{ ok: true } | { ok: false; duplicate: boolean; message: string }>;
};

export type ReportRow = {
  spot_id: number;
  observed_hour: string;
  size_bucket: SizeBucket;
  reporter_hash: string;
  forecast_face_ft: number | null;
  forecast_stars: number | null;
  rating_verdict: RatingVerdict | null;
};

export type SubmitOutcome = {
  status: number;
  body: { ok: true; duplicate: boolean } | { ok: false; error: string };
};

/**
 * Validate, resolve, snapshot, insert.
 *
 * THE SNAPSHOT IS NEVER SUBSTITUTED. findForecast is asked for the reported hour and no
 * other; when it returns null the row stores NULL for both forecast columns. Writing the
 * CURRENT hour's numbers against a PAST observed_hour would silently mislabel the data —
 * the record would claim we forecast something we did not — so this function has no access
 * to a current-hour value at all. It is impossible by construction, not by discipline.
 *
 * A UNIQUE violation is a repeat submission, not an error: the reporter already told us
 * this, the row is already there, and the honest thing to show them is success.
 */
export async function submitReport(
  db: ReportDb,
  raw: unknown,
  nowMs: number,
  reporterHash: string,
): Promise<SubmitOutcome> {
  const parsed = validateReport(raw, nowMs);
  if (!parsed.ok) {
    return { status: 400, body: { ok: false, error: parsed.error } };
  }
  const input = parsed.value;

  const spotId = await db.findSpotId(input.slug);
  if (spotId === null) {
    return { status: 404, body: { ok: false, error: 'unknown spot' } };
  }

  const snapshot = await db.findForecast(spotId, input.observedHourIso);

  const written = await db.insert({
    spot_id: spotId,
    observed_hour: input.observedHourIso,
    size_bucket: input.sizeBucket,
    reporter_hash: reporterHash,
    forecast_face_ft: snapshot ? snapshot.face_ft : null,
    forecast_stars: snapshot ? snapshot.stars : null,
    rating_verdict: input.ratingVerdict,
  });

  if (!written.ok) {
    if (written.duplicate) {
      return { status: 200, body: { ok: true, duplicate: true } };
    }
    return { status: 500, body: { ok: false, error: 'could not save the report' } };
  }
  return { status: 200, body: { ok: true, duplicate: false } };
}
