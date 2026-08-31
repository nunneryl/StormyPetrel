/**
 * Pins for the surf-report feedback path: the size ladder, the request-validation rules,
 * and the submit flow that turns a request into at most one row.
 *
 * NO TEST FRAMEWORK IS ADDED — the frontend has none. package.json's test script runs bare
 * Node with type-stripping, and this file follows lib/spotInfo.test.mts exactly. surfReport.ts
 * is deliberately import-free (no React, no Supabase), so this runs with nothing installed:
 *
 *     node --experimental-strip-types frontend/lib/surfReport.test.mts
 *     (or: npm --prefix frontend run test)
 *
 * EVERY EXPECTED VALUE IS WRITTEN LITERALLY. The ladder's feet, the bucket order, the hour
 * strings and the boundary timestamps are all typed out, with the arithmetic in a comment
 * where it is not obvious. Nothing is produced by calling the function under test — a test
 * that asks the subject what the answer is cannot notice the subject changing its mind.
 *
 * WHAT THIS CAN AND CANNOT PIN. submitReport takes its database as an injected ReportDb, so
 * the flow — resolve, snapshot, insert, revise, and what each failure does — is pinned
 * exactly. The FAKE below re-implements the UNIQUE(spot_id, observed_hour, reporter_hash)
 * constraint so the duplicate path can be exercised; that is a stand-in for Postgres, not a
 * test of it, and the constraint itself is pinned only by migration 014. There is no DOM
 * here: with no framework and no installed packages there is no way to mount SurfReport.tsx,
 * so the panel's copy and wiring are not covered.
 *
 * SECTION 5 IS THE REVISION PATH (migration 015): last answer wins, the ORIGINAL forecast
 * snapshot is frozen, and `revision` counts corrections rather than submissions. The fake's
 * updateAnswer applies its patch with Object.assign, so a snapshot column smuggled into the
 * patch would move the row and be caught — the frozen half is protected by the patch's TYPE,
 * and these tests check that the type is actually what gets sent.
 */
import {
  SIZE_BUCKETS,
  BUCKET_FACE_FT,
  RATING_VERDICTS,
  MAX_REPORT_AGE_HOURS,
  HOUR_PICKER_SPAN,
  floorToHourMs,
  toUtcHourIso,
  hourOptions,
  isSizeBucket,
  isRatingVerdict,
  validateReport,
  submitReport,
  type ReportDb,
  type ReportRow,
  type ReportKey,
  type ReportUpdate,
  type ForecastSnapshot,
} from './surfReport.ts';

let failures = 0;

function check(name: string, cond: boolean, detail = ''): void {
  if (cond) {
    console.log(`  PASS  ${name}`);
  } else {
    failures += 1;
    console.log(`  FAIL  ${name}${detail ? `  — ${detail}` : ''}`);
  }
}

function eq<T>(name: string, got: T, want: T): void {
  check(name, JSON.stringify(got) === JSON.stringify(want),
        `got ${JSON.stringify(got)}, want ${JSON.stringify(want)}`);
}

// --------------------------------------------------------------------------- //
// Fixtures                                                                     //
// --------------------------------------------------------------------------- //

// A fixed clock, so every window rule is exact rather than "about now".
// 2026-08-30T14:37Z sits inside the 14:00 UTC hour.
const NOW = Date.parse('2026-08-30T14:37:00.000Z');
const NOW_ISO = '2026-08-30T14:37:00.000Z';
// Five minutes later, still inside the same UTC hour, so the same observed_hour stays valid.
const LATER = Date.parse('2026-08-30T14:42:00.000Z');
const LATER_ISO = '2026-08-30T14:42:00.000Z';

const HOUR_NOW = '2026-08-30T14:00:00.000Z';
const HOUR_NEXT = '2026-08-30T15:00:00.000Z';   // future
const HOUR_BACK_1 = '2026-08-30T13:00:00.000Z';
// 14:00 on the 30th minus 48 h = 14:00 on the 28th. Exactly at the limit, so ACCEPTED.
const HOUR_BACK_48 = '2026-08-28T14:00:00.000Z';
// One hour older than that: 49 h. REJECTED.
const HOUR_BACK_49 = '2026-08-28T13:00:00.000Z';

const HASH = 'a1b2c3d4e5f60718';

type Fake = {
  db: ReportDb;
  rows: ReportRow[];
  forecastLookups: string[];
  spotLookups: string[];
  /** Every patch updateAnswer was handed, so a test can assert what the update is ALLOWED
   *  to touch — the frozen half of the row is protected by the patch's shape, not by the
   *  fake choosing to ignore columns. */
  patches: ReportUpdate[];
};

type FakeOpts = {
  /** Mutable map, so a test can change what we forecast BETWEEN two submissions. */
  forecasts?: Record<string, ForecastSnapshot>;
  insertError?: string | null;
  updateError?: string | null;
  /** Drop the row after the insert conflicts, to simulate a lost race. */
  vanishOnLookup?: boolean;
};

/** A stand-in ReportDb. `spots` maps slug -> id; `forecasts` maps "id|hour" -> snapshot.
 *  insert() re-implements the migration's UNIQUE(spot_id, observed_hour, reporter_hash);
 *  updateAnswer() applies the patch VERBATIM with Object.assign, so if submitReport ever put
 *  a forecast column into the patch the frozen-half assertions below would see it move. */
function fake(spots: Record<string, number>, opts: FakeOpts = {}): Fake {
  const forecasts = opts.forecasts ?? {};
  const rows: ReportRow[] = [];
  const forecastLookups: string[] = [];
  const spotLookups: string[] = [];
  const patches: ReportUpdate[] = [];
  const find = (k: ReportKey) =>
    rows.find(
      (r) =>
        r.spot_id === k.spot_id &&
        r.observed_hour === k.observed_hour &&
        r.reporter_hash === k.reporter_hash,
    );
  const db: ReportDb = {
    async findSpotId(slug) {
      spotLookups.push(slug);
      return slug in spots ? spots[slug] : null;
    },
    async findForecast(spotId, iso) {
      forecastLookups.push(`${spotId}|${iso}`);
      const hit = forecasts[`${spotId}|${iso}`];
      return hit === undefined ? null : hit;
    },
    async insert(row) {
      if (opts.insertError) {
        return { ok: false as const, duplicate: false, message: opts.insertError };
      }
      if (find(row)) {
        return { ok: false as const, duplicate: true, message: 'unique violation' };
      }
      rows.push({ ...row });
      return { ok: true as const };
    },
    async findExisting(key) {
      if (opts.vanishOnLookup) return null;
      const row = find(key);
      if (!row) return null;
      return {
        size_bucket: row.size_bucket,
        rating_verdict: row.rating_verdict,
        revision: row.revision,
      };
    },
    async updateAnswer(key, patch) {
      if (opts.updateError) return { ok: false as const, message: opts.updateError };
      patches.push(patch);
      const row = find(key);
      if (!row) return { ok: false as const, message: 'no such row' };
      Object.assign(row, patch);
      return { ok: true as const };
    },
  };
  return { db, rows, forecastLookups, spotLookups, patches };
}

const body = (over: Record<string, unknown> = {}) => ({
  slug: 'steamer-lane',
  observed_hour: HOUR_BACK_1,
  size_bucket: 'chest',
  ...over,
});

// --------------------------------------------------------------------------- //
// 1 — the ladder                                                               //
// --------------------------------------------------------------------------- //
console.log('\n1 — the size ladder');

eq('bucket order is the ordinal scale, ascending', [...SIZE_BUCKETS], [
  'ankle', 'knee', 'thigh', 'waist', 'chest', 'shoulder', 'head',
  'overhead', 'well_overhead', 'double_overhead', 'triple_overhead_plus',
]);

// Written out, not read back from the module: these ARE the assumption under test.
const LADDER_FT: Array<[string, number]> = [
  ['ankle', 0.5], ['knee', 1.5], ['thigh', 2.25], ['waist', 3], ['chest', 4],
  ['shoulder', 5], ['head', 6], ['overhead', 7], ['well_overhead', 9],
  ['double_overhead', 11], ['triple_overhead_plus', 15],
];
for (const [bucket, ft] of LADDER_FT) {
  eq(`${bucket} maps to ${ft} ft`, BUCKET_FACE_FT[bucket as keyof typeof BUCKET_FACE_FT], ft);
}

// Monotonicity is the property the whole scale rests on: an ordinal label is only
// computable if a later bucket is always bigger than an earlier one.
let monotonic = true;
let firstBreak = '';
for (let i = 1; i < SIZE_BUCKETS.length; i += 1) {
  const prev = BUCKET_FACE_FT[SIZE_BUCKETS[i - 1]];
  const curr = BUCKET_FACE_FT[SIZE_BUCKETS[i]];
  if (!(curr > prev)) {
    monotonic = false;
    if (!firstBreak) firstBreak = `${SIZE_BUCKETS[i - 1]}=${prev} then ${SIZE_BUCKETS[i]}=${curr}`;
  }
}
check('ladder is strictly increasing across every adjacent pair', monotonic, firstBreak);

eq('every bucket has a feet value', Object.keys(BUCKET_FACE_FT).length, 11);
eq('the ladder has no bucket the CHECK constraint does not list', SIZE_BUCKETS.length, 11);
eq('verdicts are exactly the three the CHECK constraint lists',
   [...RATING_VERDICTS], ['too_low', 'about_right', 'too_high']);

check('isSizeBucket accepts a listed bucket', isSizeBucket('shoulder'));
check('isSizeBucket rejects an unlisted one', !isSizeBucket('gigantic'));
check('isSizeBucket rejects a non-string', !isSizeBucket(4));
check('isRatingVerdict accepts a listed verdict', isRatingVerdict('too_high'));
check('isRatingVerdict rejects an unlisted one', !isRatingVerdict('perfect'));

// --------------------------------------------------------------------------- //
// 2 — hours                                                                    //
// --------------------------------------------------------------------------- //
console.log('\n2 — hours');

eq('floorToHourMs drops minutes and seconds',
   floorToHourMs(Date.parse('2026-08-30T14:37:12.345Z')), Date.parse('2026-08-30T14:00:00.000Z'));
eq('floorToHourMs is a no-op exactly on the hour',
   floorToHourMs(Date.parse('2026-08-30T14:00:00.000Z')), Date.parse('2026-08-30T14:00:00.000Z'));
eq('toUtcHourIso renders the containing UTC hour', toUtcHourIso(NOW), '2026-08-30T14:00:00.000Z');
eq('toUtcHourIso at 23:59 stays on the same day',
   toUtcHourIso(Date.parse('2026-08-30T23:59:59.999Z')), '2026-08-30T23:00:00.000Z');

eq('hourOptions is newest-first and steps back one hour at a time',
   hourOptions(NOW, 4),
   ['2026-08-30T14:00:00.000Z', '2026-08-30T13:00:00.000Z',
    '2026-08-30T12:00:00.000Z', '2026-08-30T11:00:00.000Z']);
eq('hourOptions crosses midnight backwards',
   hourOptions(Date.parse('2026-08-30T01:20:00.000Z'), 3),
   ['2026-08-30T01:00:00.000Z', '2026-08-30T00:00:00.000Z', '2026-08-29T23:00:00.000Z']);
eq('hourOptions defaults to the picker span', hourOptions(NOW).length, 12);
eq('the picker span is 12 hours', HOUR_PICKER_SPAN, 12);
eq('the accept window is 48 hours', MAX_REPORT_AGE_HOURS, 48);

// --------------------------------------------------------------------------- //
// 3 — validation                                                               //
// --------------------------------------------------------------------------- //
console.log('\n3 — request validation');

const ok1 = validateReport(body(), NOW);
eq('a well-formed body validates', ok1.ok, true);
eq('a well-formed body normalises to the UTC hour',
   ok1.ok ? ok1.value.observedHourIso : '', HOUR_BACK_1);
eq('an omitted rating_verdict becomes null',
   ok1.ok ? ok1.value.ratingVerdict : 'unset', null);

const err = (raw: unknown, now = NOW) => {
  const r = validateReport(raw, now);
  return r.ok ? '(accepted)' : r.error;
};

eq('a non-object body is rejected', err('nope'), 'body must be a JSON object');
eq('a null body is rejected', err(null), 'body must be a JSON object');
eq('a missing slug is rejected', err({ observed_hour: HOUR_NOW, size_bucket: 'chest' }),
   'slug is required');
eq('a whitespace-only slug is rejected', err(body({ slug: '   ' })), 'slug is required');
eq('a missing observed_hour is rejected', err({ slug: 'x', size_bucket: 'chest' }),
   'observed_hour is required');
eq('a non-string observed_hour is rejected', err(body({ observed_hour: 1756564800000 })),
   'observed_hour is required');
eq('an unparseable observed_hour is rejected', err(body({ observed_hour: 'yesterday' })),
   'observed_hour is not a valid timestamp');

eq('a FUTURE observed_hour is rejected', err(body({ observed_hour: HOUR_NEXT })),
   'observed_hour is in the future');
eq('the current hour is NOT in the future', err(body({ observed_hour: HOUR_NOW })), '(accepted)');
// 14:52 is later than the 14:37 clock but names the hour that has already started.
eq('a timestamp later in the current hour is not in the future',
   err(body({ observed_hour: '2026-08-30T14:52:00.000Z' })), '(accepted)');

eq('an observed_hour OLDER THAN 48 h is rejected', err(body({ observed_hour: HOUR_BACK_49 })),
   'observed_hour is older than 48 hours');
eq('exactly 48 h old is accepted (inclusive boundary)',
   err(body({ observed_hour: HOUR_BACK_48 })), '(accepted)');

eq('an unlisted size_bucket is rejected', err(body({ size_bucket: 'gigantic' })),
   'size_bucket is not one of the allowed values');
eq('a missing size_bucket is rejected', err({ slug: 'x', observed_hour: HOUR_NOW }),
   'size_bucket is not one of the allowed values');
eq('a numeric size_bucket is rejected', err(body({ size_bucket: 4 })),
   'size_bucket is not one of the allowed values');
eq('an unlisted rating_verdict is rejected', err(body({ rating_verdict: 'perfect' })),
   'rating_verdict is not one of the allowed values');
eq('an explicit null rating_verdict is accepted', err(body({ rating_verdict: null })), '(accepted)');

// --------------------------------------------------------------------------- //
// 4 — submit: resolve, snapshot, insert                                        //
// --------------------------------------------------------------------------- //
console.log('\n4 — submit');

// -- unknown slug -----------------------------------------------------------
{
  const f = fake({ 'steamer-lane': 7 });
  const out = await submitReport(f.db, body({ slug: 'not-a-spot' }), NOW, HASH);
  eq('an unknown slug is rejected with 404', out.status, 404);
  eq('an unknown slug returns the unknown-spot error', out.body, { ok: false, error: 'unknown spot' });
  eq('an unknown slug writes no row', f.rows.length, 0);
  eq('an unknown slug never reaches the forecast lookup', f.forecastLookups.length, 0);
}

// -- validation failures short-circuit before any database work --------------
{
  const f = fake({ 'steamer-lane': 7 });
  const out = await submitReport(f.db, body({ observed_hour: HOUR_NEXT }), NOW, HASH);
  eq('a future hour is rejected with 400', out.status, 400);
  eq('a future hour never looks a spot up', f.spotLookups.length, 0);
  eq('a future hour writes no row', f.rows.length, 0);
}
{
  const f = fake({ 'steamer-lane': 7 });
  const out = await submitReport(f.db, body({ size_bucket: 'gigantic' }), NOW, HASH);
  eq('an invalid size_bucket is rejected with 400', out.status, 400);
  eq('an invalid size_bucket writes no row', f.rows.length, 0);
}

// -- the snapshot -----------------------------------------------------------
{
  const f = fake({ 'steamer-lane': 7 }, {
    forecasts: {
      // The hour being reported on.
      [`7|${HOUR_BACK_1}`]: { face_ft: 4.7, stars: 2.5 },
      // A DECOY: the current hour, with numbers that must never appear on the row.
      [`7|${HOUR_NOW}`]: { face_ft: 9.9, stars: 5 },
    },
  });
  const out = await submitReport(f.db, body({ observed_hour: HOUR_BACK_1 }), NOW, HASH);
  eq('a good report returns 200', out.status, 200);
  eq('a good report is not flagged duplicate or revised', out.body,
     { ok: true, duplicate: false, revised: false });
  eq('exactly one row is written', f.rows.length, 1);
  eq('the row is the full record', f.rows[0], {
    spot_id: 7,
    observed_hour: HOUR_BACK_1,
    reporter_hash: HASH,
    size_bucket: 'chest',
    forecast_face_ft: 4.7,
    forecast_stars: 2.5,
    rating_verdict: null,
    revision: 0,
    reported_at: NOW_ISO,
    first_reported_at: NOW_ISO,
  });
  eq('the snapshot is looked up for the REPORTED hour and no other',
     f.forecastLookups, [`7|${HOUR_BACK_1}`]);
}

// THE ONE THAT MATTERS: no forecast row for that hour must store NULL, never a substitute.
{
  const f = fake({ 'steamer-lane': 7 }, {
    // Nothing at HOUR_BACK_1. The current hour IS populated — if the code ever fell back to
    // "whatever we have now", these are the numbers that would show up on the row.
    forecasts: { [`7|${HOUR_NOW}`]: { face_ft: 9.9, stars: 5 } },
  });
  const out = await submitReport(f.db, body({ observed_hour: HOUR_BACK_1 }), NOW, HASH);
  eq('a hour with no forecast still returns 200', out.status, 200);
  eq('a hour with no forecast stores NULL face', f.rows[0].forecast_face_ft, null);
  eq('a hour with no forecast stores NULL stars', f.rows[0].forecast_stars, null);
  check('the current hour\'s numbers are NOT substituted',
        f.rows[0].forecast_face_ft !== 9.9 && f.rows[0].forecast_stars !== 5);
  eq('only the reported hour was ever asked for', f.forecastLookups, [`7|${HOUR_BACK_1}`]);
}

// A real zero is a measurement, not an absence.
{
  const f = fake({ 'steamer-lane': 7 }, { forecasts: { [`7|${HOUR_BACK_1}`]: { face_ft: 0, stars: 0 } } });
  await submitReport(f.db, body({ observed_hour: HOUR_BACK_1 }), NOW, HASH);
  eq('a forecast of 0 ft is stored as 0, not nulled', f.rows[0].forecast_face_ft, 0);
  eq('a rating of 0 stars is stored as 0, not nulled', f.rows[0].forecast_stars, 0);
}

// A row that exists but carries nulls is still "we had a forecast row"; both columns null.
{
  const f = fake({ 'steamer-lane': 7 }, { forecasts: { [`7|${HOUR_BACK_1}`]: { face_ft: null, stars: null } } });
  await submitReport(f.db, body({ observed_hour: HOUR_BACK_1 }), NOW, HASH);
  eq('a forecast row of nulls stores nulls', f.rows[0].forecast_face_ft, null);
}

// -- the optional verdict ---------------------------------------------------
{
  const f = fake({ 'steamer-lane': 7 });
  await submitReport(f.db, body({ rating_verdict: 'too_high' }), NOW, HASH);
  eq('a supplied verdict is stored', f.rows[0].rating_verdict, 'too_high');
}
{
  const f = fake({ 'steamer-lane': 7 });
  await submitReport(f.db, body(), NOW, HASH);
  eq('an omitted verdict is stored as null, not as about_right', f.rows[0].rating_verdict, null);
}

// -- the size label, not feet -----------------------------------------------
{
  const f = fake({ 'steamer-lane': 7 });
  await submitReport(f.db, body({ size_bucket: 'well_overhead' }), NOW, HASH);
  eq('the BUCKET LABEL is stored', f.rows[0].size_bucket, 'well_overhead');
  check('no feet value is written anywhere on the row',
        !Object.values(f.rows[0]).includes(9));
}

// -- the mid-hour timestamp is floored before storage ------------------------
{
  const f = fake({ 'steamer-lane': 7 });
  await submitReport(f.db, body({ observed_hour: '2026-08-30T13:44:21.500Z' }), NOW, HASH);
  eq('a mid-hour timestamp is stored as the top of that hour',
     f.rows[0].observed_hour, '2026-08-30T13:00:00.000Z');
}

// -- the duplicate -----------------------------------------------------------
{
  const f = fake({ 'steamer-lane': 7 });
  const first = await submitReport(f.db, body(), NOW, HASH);
  const second = await submitReport(f.db, body(), NOW, HASH);
  eq('the first submission succeeds', first.body, { ok: true, duplicate: false, revised: false });
  eq('a repeat submission reads as SUCCESS to the user', second.status, 200);
  eq('a repeat submission with the SAME answer is not a revision', second.body,
     { ok: true, duplicate: true, revised: false });
  eq('a repeat submission does NOT create a second row', f.rows.length, 1);
}
{
  // Same spot-hour, DIFFERENT reporter — two distinct labels, both wanted.
  const f = fake({ 'steamer-lane': 7 });
  await submitReport(f.db, body(), NOW, HASH);
  const other = await submitReport(f.db, body(), NOW, '00112233445566ff');
  eq('a different reporter for the same spot-hour is not a duplicate',
     other.body, { ok: true, duplicate: false, revised: false });
  eq('a different reporter adds a second row', f.rows.length, 2);
}
{
  // Same reporter, DIFFERENT hour — also not a duplicate.
  const f = fake({ 'steamer-lane': 7 });
  await submitReport(f.db, body({ observed_hour: HOUR_BACK_1 }), NOW, HASH);
  const later = await submitReport(f.db, body({ observed_hour: HOUR_NOW }), NOW, HASH);
  eq('the same reporter on a different hour is not a duplicate',
     later.body, { ok: true, duplicate: false, revised: false });
  eq('the same reporter on a different hour adds a second row', f.rows.length, 2);
}

// -- a genuine insert failure is NOT reported as success ---------------------
{
  const f = fake({ 'steamer-lane': 7 }, { insertError: 'connection reset' });
  const out = await submitReport(f.db, body(), NOW, HASH);
  eq('a non-duplicate insert failure returns 500', out.status, 500);
  eq('a non-duplicate insert failure returns an error body',
     out.body, { ok: false, error: 'could not save the report' });
}

// --------------------------------------------------------------------------- //
// 5 — revisions: last answer wins, and the row says it was changed             //
// --------------------------------------------------------------------------- //
console.log('\n5 — revisions');

// THE ONE THAT MATTERS. A corrected size replaces the first answer, and the ORIGINAL
// snapshot survives — proved by moving the forecast underneath, the way a pipeline run
// does as an hour recedes toward its nowcast.
{
  const fc: Record<string, ForecastSnapshot> = {
    [`7|${HOUR_BACK_1}`]: { face_ft: 4.7, stars: 2.5 },
  };
  const f = fake({ 'steamer-lane': 7 }, { forecasts: fc });
  await submitReport(f.db, body({ size_bucket: 'knee' }), NOW, HASH);
  // A pipeline run lands between the two submissions and rewrites that hour.
  fc[`7|${HOUR_BACK_1}`] = { face_ft: 1.2, stars: 1 };
  const out = await submitReport(f.db, body({ size_bucket: 'waist' }), LATER, HASH);

  eq('a corrected size returns 200', out.status, 200);
  eq('a corrected size is reported as a revision', out.body,
     { ok: true, duplicate: true, revised: true });
  eq('a correction leaves exactly ONE row', f.rows.length, 1);
  eq('the LATEST answer survives, not the first', f.rows[0].size_bucket, 'waist');
  eq('the revision counter advances to 1', f.rows[0].revision, 1);
  eq('reported_at moves to the surviving answer', f.rows[0].reported_at, LATER_ISO);
  eq('first_reported_at is frozen at the original', f.rows[0].first_reported_at, NOW_ISO);
  eq('the ORIGINAL forecast snapshot survives the revision', f.rows[0].forecast_face_ft, 4.7);
  eq('the original star snapshot survives too', f.rows[0].forecast_stars, 2.5);
  check('the drifted nowcast value did NOT overwrite the snapshot',
        f.rows[0].forecast_face_ft !== 1.2 && f.rows[0].forecast_stars !== 1);
}

// The update is only ALLOWED to touch the answer half of the row. Written out literally:
// if a forecast column ever appears here, the frozen half stops being frozen.
{
  const f = fake({ 'steamer-lane': 7 });
  await submitReport(f.db, body({ size_bucket: 'knee' }), NOW, HASH);
  await submitReport(f.db, body({ size_bucket: 'waist' }), LATER, HASH);
  eq('exactly one update was issued', f.patches.length, 1);
  eq('the update patch carries ONLY the mutable columns',
     Object.keys(f.patches[0]).sort(),
     ['rating_verdict', 'reported_at', 'revision', 'size_bucket']);
  eq('the update patch is the new answer', f.patches[0],
     { size_bucket: 'waist', rating_verdict: null, revision: 1, reported_at: LATER_ISO });
}

// Idempotence: the same answer twice changes nothing observable but reported_at.
{
  const f = fake({ 'steamer-lane': 7 }, {
    forecasts: { [`7|${HOUR_BACK_1}`]: { face_ft: 4.7, stars: 2.5 } },
  });
  await submitReport(f.db, body({ size_bucket: 'knee', rating_verdict: 'too_high' }), NOW, HASH);
  const again = await submitReport(
    f.db, body({ size_bucket: 'knee', rating_verdict: 'too_high' }), LATER, HASH);
  eq('a same-answer resubmit is not a revision', again.body,
     { ok: true, duplicate: true, revised: false });
  eq('a same-answer resubmit leaves one row', f.rows.length, 1);
  eq('a same-answer resubmit does NOT advance the counter', f.rows[0].revision, 0);
  eq('a same-answer resubmit keeps the size', f.rows[0].size_bucket, 'knee');
  eq('a same-answer resubmit keeps the verdict', f.rows[0].rating_verdict, 'too_high');
  eq('a same-answer resubmit keeps the snapshot', f.rows[0].forecast_face_ft, 4.7);
  eq('a same-answer resubmit keeps first_reported_at', f.rows[0].first_reported_at, NOW_ISO);
  eq('reported_at IS allowed to move on a same-answer resubmit',
     f.rows[0].reported_at, LATER_ISO);
}

// The counter counts CORRECTIONS, not submissions.
{
  const f = fake({ 'steamer-lane': 7 });
  await submitReport(f.db, body({ size_bucket: 'knee' }), NOW, HASH);
  eq('a first report is revision 0', f.rows[0].revision, 0);
  await submitReport(f.db, body({ size_bucket: 'waist' }), NOW, HASH);
  eq('the first correction is revision 1', f.rows[0].revision, 1);
  await submitReport(f.db, body({ size_bucket: 'waist' }), NOW, HASH);
  eq('an unchanged resubmit in between does not advance it', f.rows[0].revision, 1);
  await submitReport(f.db, body({ size_bucket: 'chest' }), NOW, HASH);
  eq('the second correction is revision 2', f.rows[0].revision, 2);
  eq('four submissions still leave ONE row', f.rows.length, 1);
  eq('the last answer is the one standing', f.rows[0].size_bucket, 'chest');
}

// A verdict-only change is still a correction — the size question is not the only answer.
{
  const f = fake({ 'steamer-lane': 7 });
  await submitReport(f.db, body({ size_bucket: 'knee', rating_verdict: 'too_high' }), NOW, HASH);
  const out = await submitReport(
    f.db, body({ size_bucket: 'knee', rating_verdict: 'about_right' }), LATER, HASH);
  eq('changing only the verdict is a revision', out.body,
     { ok: true, duplicate: true, revised: true });
  eq('changing only the verdict advances the counter', f.rows[0].revision, 1);
  eq('the new verdict survives', f.rows[0].rating_verdict, 'about_right');
}

// Clearing a verdict is a change too: null (skipped) is not the same answer as a verdict.
{
  const f = fake({ 'steamer-lane': 7 });
  await submitReport(f.db, body({ size_bucket: 'knee', rating_verdict: 'too_low' }), NOW, HASH);
  const out = await submitReport(f.db, body({ size_bucket: 'knee' }), LATER, HASH);
  eq('dropping the verdict is a revision', out.body,
     { ok: true, duplicate: true, revised: true });
  eq('the verdict is cleared to null', f.rows[0].rating_verdict, null);
  eq('dropping the verdict advances the counter', f.rows[0].revision, 1);
}

// The other two keys of the constraint still separate rows, unchanged by any of this.
{
  const f = fake({ 'steamer-lane': 7 });
  await submitReport(f.db, body({ size_bucket: 'knee' }), NOW, HASH);
  const other = await submitReport(f.db, body({ size_bucket: 'waist' }), LATER, '00112233445566ff');
  eq('a different reporter with a different answer is NOT a revision',
     other.body, { ok: true, duplicate: false, revised: false });
  eq('a different reporter still gets its own row', f.rows.length, 2);
  eq('the first reporter\'s answer is untouched', f.rows[0].size_bucket, 'knee');
  eq('no update was issued for a different reporter', f.patches.length, 0);
}
{
  const f = fake({ 'steamer-lane': 7 });
  await submitReport(f.db, body({ observed_hour: HOUR_BACK_1, size_bucket: 'knee' }), NOW, HASH);
  const later = await submitReport(
    f.db, body({ observed_hour: HOUR_NOW, size_bucket: 'waist' }), LATER, HASH);
  eq('the same reporter on a different HOUR is not a revision',
     later.body, { ok: true, duplicate: false, revised: false });
  eq('a different hour still gets its own row', f.rows.length, 2);
  eq('the earlier hour\'s answer is untouched', f.rows[0].size_bucket, 'knee');
}

// -- revision-path failures are not reported as success ----------------------
{
  const f = fake({ 'steamer-lane': 7 }, { updateError: 'connection reset' });
  await submitReport(f.db, body({ size_bucket: 'knee' }), NOW, HASH);
  const out = await submitReport(f.db, body({ size_bucket: 'waist' }), LATER, HASH);
  eq('a failed update returns 500', out.status, 500);
  eq('a failed update returns an error body', out.body,
     { ok: false, error: 'could not save the report' });
  eq('a failed update leaves the earlier answer standing', f.rows[0].size_bucket, 'knee');
}
{
  // The row conflicted a moment ago and is gone now. We cannot compute the next revision,
  // and guessing 0 would erase a correction history — so this is a failure, not a success.
  const f = fake({ 'steamer-lane': 7 }, { vanishOnLookup: true });
  await submitReport(f.db, body({ size_bucket: 'knee' }), NOW, HASH);
  const out = await submitReport(f.db, body({ size_bucket: 'waist' }), LATER, HASH);
  eq('a vanished row on the revision path returns 500', out.status, 500);
  eq('a vanished row attempts no update', f.patches.length, 0);
}

// Throw rather than process.exit: a thrown error still gives node a non-zero exit code,
// and it keeps this file free of any ambient Node types, so it typechecks and runs with
// nothing installed at all.
if (failures > 0) {
  throw new Error(`surfReport: ${failures} FAILURE(S)`);
}
console.log('\nsurfReport: ALL PASS');
