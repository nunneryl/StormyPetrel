#!/usr/bin/env python3
"""What a per-spot MOP face correction does to the STAR RATING.

THE MEASUREMENT THIS ANSWERS. A per-spot multiplicative correction against CDIP MOP is
viable for 132 of 137 California spots: each spot's factor is the median of
face_ft / (MOP Hs x 3.281) over ~334 hours, with within-spot p90/p10 spread of 1.52
(median), i.e. a residual of about -18% / +23% after correction — close to the 15-25%
observer-noise floor. Applied to 21 spots over 7 days (3,528 spot-hours) the average face
goes 4.68 -> 2.23 ft with 6.2% of hours dropping under 1 ft.

WHY A SCRIPT AND NOT SQL. face_ft does not reach `stars` linearly. It enters through
size_score(), a piecewise-linear curve with knots at 1/2/3/4/5/6/8/10 ft, which is then
one term of a WEIGHTED GEOMETRIC MEAN against wind / tide / chop / period-quality
(interpret.COMPOSITE_FACTOR_EXPONENTS, summing to 1.0), and the result is snapped to half
stars and clamped to [1, 5] — with a separate hard cutoff to 0 stars below 0.5 ft
effective. Halving the face does not halve the stars, and the same halving moves a 6 ft
hour and a 1.5 ft hour by different amounts. The only honest way to get the number is to
run the production function on real rows.

WHAT IT DOES. For every fetched row it calls interpret.composite_stars TWICE — once on the
published effective size and once on effective / factor — with every other input held
byte-identical, and reports the distribution of the difference.

  SCALING effective_size_ft, NOT face_ft, IS DELIBERATE AND EXACT. rate_spot computes
  `effective = face * dir_gain` on the orientation/NWPS path and `effective = face` on the
  WW3 path (interpret.py, the `face_source == "ww3"` branch), and the NWPS override writes
  `effective_size_ft = face * dir_gain`. Effective is therefore LINEAR in face under both
  branches, so dividing face by k divides effective by k either way. Scaling the stored
  effective needs no guess about which branch produced the row, and cannot disagree with
  dir_gain.

THE SELF-CHECK THAT MAKES THE REST TRUSTWORTHY. Before reporting anything the script
recomputes the BEFORE stars from the stored factors and compares them to the published
`stars` column. If that does not reproduce, our model of the pipeline is wrong and every
delta below is meaningless — so the reproduction rate is printed first, loudly, and a rate
below --min-reproduction aborts rather than reporting.

WHAT THIS CANNOT TELL US, STATED HERE AND AGAIN IN THE OUTPUT. It measures the CHANGE, not
its CORRECTNESS. Nothing here says the corrected stars are right. The factors are anchored
to CDIP MOP, so this inherits every assumption in that anchoring, and MOP is a model, not a
measurement of the breaking wave. Against ground truth we have TWO user labels. Two.
A clean before/after table is not evidence the after column is true.

USAGE
    python3 scripts/star_impact_of_face_correction.py                    # 7 days, all factored spots
    python3 scripts/star_impact_of_face_correction.py --days-back 14
    python3 scripts/star_impact_of_face_correction.py --rows-cache scripts/star_impact_rows.json
    python3 scripts/star_impact_of_face_correction.py --selftest         # offline; no DB, no network

INPUTS
    scripts/mop_spread.json   by_spot[].slug -> by_spot[].face_ratio.median. Mac-local and
                              GITIGNORED — read at runtime, never committed.
    Supabase                  forecasts rows, source='nwps'. READ-ONLY: this script issues
                              SELECTs only and has no write path at all.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import statistics
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

# THE PRODUCTION STAR FUNCTION, IMPORTED NOT REIMPLEMENTED. A local copy would drift from
# the curve, the exponents or the clamp without anything failing — and this script exists
# precisely because that chain is non-linear. selftest_uses_production_star_function pins
# the identity, and a tripwire proves it is actually called rather than shadowed.
from pipeline.interpret import composite_stars, size_score            # noqa: E402

MOP_SPREAD_PATH = os.path.join(HERE, "mop_spread.json")
OUT = os.path.join(HERE, "star_impact_of_face_correction_out.json")

DEFAULT_DAYS_BACK = 7

# PostgREST caps a select at 1000 rows, so paging inside a chunk is load-bearing.
FORECAST_PAGE_ROWS = 1000
# How many spot ids one forecasts statement may ask about. 8 is not a guess: at any limit
# above 8 the un-chunked query raised PostgREST 57014 "canceling statement due to statement
# timeout", measured during the MOP face validation. Kept identical to
# mop_face_validation.FORECAST_CHUNK_SPOTS so the two scripts fetch the same way.
FORECAST_CHUNK_SPOTS = 8

# Star ratings live on a half-star lattice. Boundaries are the midpoints an hour can cross.
HALF_STAR_BOUNDARIES = [1.5, 2.0, 2.5, 3.0, 3.5, 4.0, 4.5, 5.0]
# Below this fraction of rows reproducing their published stars, abort instead of reporting.
DEFAULT_MIN_REPRODUCTION = 0.98


# --------------------------------------------------------------------------- #
# Factors                                                                      #
# --------------------------------------------------------------------------- #

def load_factors(path=MOP_SPREAD_PATH):
    """{slug: factor} from mop_spread.json's by_spot[].face_ratio.median.

    A factor is a DIVISOR: corrected_face = published_face / factor, so a factor of 2.87
    means we publish 2.87x what MOP implies. Rows whose median is absent, non-numeric or
    non-positive are skipped and counted rather than defaulting to 1.0 — a silent 1.0 would
    read as "this spot needs no correction", which is the opposite of "we do not know".
    """
    if not os.path.exists(path):
        return None, f"no factor file at {path}"
    try:
        doc = json.load(open(path))
    except (json.JSONDecodeError, OSError) as e:
        return None, f"could not read {path}: {e}"
    by_spot = doc.get("by_spot")
    if not isinstance(by_spot, list):
        return None, f"{path}: expected a top-level 'by_spot' list"
    out, skipped = {}, []
    for rec in by_spot:
        if not isinstance(rec, dict):
            continue
        slug = rec.get("slug")
        med = (rec.get("face_ratio") or {}).get("median")
        if not slug:
            continue
        try:
            f = float(med)
        except (TypeError, ValueError):
            skipped.append((slug, repr(med)))
            continue
        if not (f > 0.0):
            skipped.append((slug, repr(med)))
            continue
        out[slug] = f
    return {"factors": out, "skipped": skipped}, None


# --------------------------------------------------------------------------- #
# The recomputation                                                            #
# --------------------------------------------------------------------------- #

def star_pair(row, factor):
    """(before_stars, after_stars) for one row, or None when the row cannot be rated.

    Both halves call the SAME production composite_stars with the SAME four quality
    factors; only the effective size differs, and only by the divisor. Nothing else about
    the row is touched, so any difference in the output is attributable to the correction
    and to nothing else.

    A None factor returns the pair unchanged (before == after) rather than skipping the
    row: a spot with no measured factor still belongs in the roster-wide denominator, and
    dropping it would flatter the summary by removing every hour that cannot move.
    """
    eff = row.get("effective_size_ft")
    if eff is None:
        return None
    wm = row.get("wind_mult")
    tm = row.get("tide_mult")
    cm = row.get("chop_mult")
    pq = row.get("period_quality")
    if wm is None or tm is None:
        return None
    # chop_mult / period_quality are NULL on rows written before migration 002; the rater
    # defaults both to 1.0 (composite_stars' own signature defaults), so mirror that rather
    # than dropping the row.
    cm = 1.0 if cm is None else cm
    pq = 1.0 if pq is None else pq
    before = composite_stars(float(eff), float(wm), float(tm), float(cm), float(pq))
    if factor is None:
        return before, before
    after = composite_stars(float(eff) / float(factor), float(wm), float(tm),
                            float(cm), float(pq))
    return before, after


def crossings(before, after):
    """Half-star boundaries this hour crossed DOWNWARD (before >= b > after).

    A correction that shrinks the face can only move stars down, but the direction is
    checked rather than assumed: an upward crossing would mean a factor below 1.0, which is
    a real possibility for a spot we UNDER-publish, and it must not be silently miscounted.
    """
    down = [b for b in HALF_STAR_BOUNDARIES if before >= b > after]
    up = [b for b in HALF_STAR_BOUNDARIES if after >= b > before]
    return down, up


# --------------------------------------------------------------------------- #
# Aggregation                                                                  #
# --------------------------------------------------------------------------- #

def summarise(pairs):
    """Roster-wide or per-spot statistics over [(before, after), ...]."""
    if not pairs:
        return None
    deltas = [a - b for b, a in pairs]
    down = Counter()
    up = Counter()
    for b, a in pairs:
        d, u = crossings(b, a)
        for x in d:
            down[x] += 1
        for x in u:
            up[x] += 1
    return {
        "hours": len(pairs),
        "mean_delta": statistics.fmean(deltas),
        "median_delta": statistics.median(deltas),
        "unchanged": sum(1 for d in deltas if d == 0.0),
        "down_crossings": {str(k): v for k, v in sorted(down.items())},
        "up_crossings": {str(k): v for k, v in sorted(up.items())},
        # The headline editorial move: an hour that read "worth checking" and now does not.
        "three_plus_to_below_three": sum(1 for b, a in pairs if b >= 3.0 > a),
        "at_one_star_floor_before": sum(1 for b, _ in pairs if b == 1.0),
        "at_one_star_floor_after": sum(1 for _, a in pairs if a == 1.0),
        # 0.0 is NOT the floor — it is the sub-0.5 ft "flat" cutoff, a different state.
        "flat_zero_before": sum(1 for b, _ in pairs if b == 0.0),
        "flat_zero_after": sum(1 for _, a in pairs if a == 0.0),
        "dist_before": {f"{k:.1f}": v for k, v in sorted(Counter(b for b, _ in pairs).items())},
        "dist_after": {f"{k:.1f}": v for k, v in sorted(Counter(a for _, a in pairs).items())},
    }


# --------------------------------------------------------------------------- #
# Supabase (read-only)                                                         #
# --------------------------------------------------------------------------- #

def chunk_ids(ids, size):
    ids = list(ids)
    return [ids[i:i + size] for i in range(0, len(ids), size)]


def _fetch_chunk(client, ids, t0_iso, t1_iso, page=FORECAST_PAGE_ROWS):
    """Every source='nwps' row in the window for ONE chunk of spot ids, paginated.

    `order("id")` makes offset paging a TOTAL order — id is the primary key, so no row can
    be skipped or repeated across a page boundary. Ordering by valid_time instead would tie
    across spots at the same hour and make page boundaries non-deterministic.
    """
    out, frm = [], 0
    while True:
        resp = (
            client.table("forecasts")
            .select("spot_id, valid_time, face_ft, effective_size_ft, dir_gain, "
                    "wind_mult, tide_mult, chop_mult, period_quality, stars")
            .in_("spot_id", list(ids))
            .eq("source", "nwps")
            .gte("valid_time", t0_iso)
            .lte("valid_time", t1_iso)
            .order("id")
            .range(frm, frm + page - 1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break
        out.extend(rows)
        if len(rows) < page:
            break
        frm += page
    return out


def fetch_rows(client, spot_ids, t0_iso, t1_iso, chunk_size=FORECAST_CHUNK_SPOTS):
    rows = []
    chunks = chunk_ids(spot_ids, chunk_size)
    for i, chunk in enumerate(chunks, 1):
        got = _fetch_chunk(client, chunk, t0_iso, t1_iso)
        rows.extend(got)
        print(f"    chunk {i:3d}/{len(chunks)}  {len(chunk):2d} spots  "
              f"+{len(got):5d} rows  ({len(rows)} total)", flush=True)
    return rows


def fetch_spots(client):
    """[{id, name, slug}] for every spot, paginated."""
    out, frm, page = [], 0, 1000
    while True:
        resp = (client.table("spots").select("id, name, slug")
                .order("id").range(frm, frm + page - 1).execute())
        rows = resp.data or []
        if not rows:
            break
        out.extend(rows)
        if len(rows) < page:
            break
        frm += page
    return out


# --------------------------------------------------------------------------- #
# Run                                                                          #
# --------------------------------------------------------------------------- #

def run(days_back=DEFAULT_DAYS_BACK, factors_path=MOP_SPREAD_PATH, out_path=OUT,
        rows_cache=None, chunk_size=FORECAST_CHUNK_SPOTS,
        min_reproduction=DEFAULT_MIN_REPRODUCTION, limit=None):
    loaded, err = load_factors(factors_path)
    if err:
        print(err, file=sys.stderr)
        print("  mop_spread.json is Mac-local and gitignored; it is not in the repo.",
              file=sys.stderr)
        return 2
    factors, skipped = loaded["factors"], loaded["skipped"]
    print(f"factors: {len(factors)} spots from {factors_path}"
          + (f"  ({len(skipped)} skipped for a missing/invalid median)" if skipped else ""),
          flush=True)
    if skipped:
        for slug, raw in skipped[:10]:
            print(f"    skipped {slug}: face_ratio.median={raw}")

    from pipeline.db_import import get_client
    client = get_client()

    spots = fetch_spots(client)
    by_id = {s["id"]: s for s in spots}
    matched = [s for s in spots if s.get("slug") in factors]
    print(f"spots: {len(spots)} in the table, {len(matched)} carry a measured factor",
          flush=True)
    unmatched = sorted(set(factors) - {s.get("slug") for s in spots})
    if unmatched:
        print(f"  {len(unmatched)} factor slugs match no spot row: {unmatched[:8]}")
    if limit:
        matched = matched[:limit]
        print(f"  --limit {limit}: using the first {len(matched)}")
    if not matched:
        print("no spot carries a factor — nothing to measure", file=sys.stderr)
        return 2

    now = datetime.datetime.now(datetime.timezone.utc)
    t0 = now - datetime.timedelta(days=days_back)
    t0_iso, t1_iso = t0.isoformat(), now.isoformat()

    if rows_cache and os.path.exists(rows_cache):
        rows = json.load(open(rows_cache))
        print(f"rows: {len(rows)} replayed from {rows_cache} (FROZEN — the window flags are "
              f"ignored on a replay)", flush=True)
    else:
        print(f"rows: fetching {t0_iso} .. {t1_iso} for {len(matched)} spots "
              f"({len(chunk_ids([s['id'] for s in matched], chunk_size))} chunks)", flush=True)
        rows = fetch_rows(client, [s["id"] for s in matched], t0_iso, t1_iso, chunk_size)
        if rows_cache:
            json.dump(rows, open(rows_cache, "w"))
            print(f"  froze {len(rows)} rows to {rows_cache}", flush=True)

    # --- the self-check that makes everything below trustworthy ------------- #
    repro_ok = repro_total = 0
    worst = []
    for r in rows:
        p = star_pair(r, None)
        if p is None or r.get("stars") is None:
            continue
        repro_total += 1
        if abs(p[0] - float(r["stars"])) < 1e-9:
            repro_ok += 1
        elif len(worst) < 5:
            worst.append((by_id.get(r["spot_id"], {}).get("slug"), r["valid_time"],
                          r["stars"], p[0]))
    rate = (repro_ok / repro_total) if repro_total else 0.0
    print()
    print("=" * 78)
    print(f"REPRODUCTION CHECK — recomputed BEFORE stars vs the published `stars` column")
    print(f"  {repro_ok}/{repro_total} rows reproduce exactly ({100 * rate:.2f}%)")
    if worst:
        print("  first mismatches (slug, valid_time, published, recomputed):")
        for w in worst:
            print(f"    {w}")
    print("=" * 78)
    if rate < min_reproduction:
        print(f"\nABORTING: reproduction {100 * rate:.2f}% is below "
              f"--min-reproduction {100 * min_reproduction:.2f}%.\n"
              "Our model of how face_ft reaches `stars` does not match production, so every\n"
              "delta this script would print is meaningless. Fix the model, not the threshold.",
              file=sys.stderr)
        return 3

    # --- the measurement ---------------------------------------------------- #
    all_pairs, per_spot, unrateable = [], {}, 0
    for r in rows:
        slug = by_id.get(r["spot_id"], {}).get("slug")
        p = star_pair(r, factors.get(slug))
        if p is None:
            unrateable += 1
            continue
        all_pairs.append(p)
        per_spot.setdefault(slug, []).append(p)

    roster = summarise(all_pairs)
    spot_stats = {s: summarise(p) for s, p in per_spot.items()}
    result = {
        "generated_at": now.isoformat(),
        "window": {"t0": t0_iso, "t1": t1_iso, "days_back": days_back,
                   "replayed_from": rows_cache if (rows_cache and os.path.exists(rows_cache)) else None},
        "factors_path": factors_path,
        "n_factors": len(factors),
        "n_spots_measured": len(per_spot),
        "n_rows": len(rows),
        "n_unrateable_rows": unrateable,
        "reproduction": {"ok": repro_ok, "total": repro_total, "rate": rate},
        "roster": roster,
        "by_spot": spot_stats,
        "cannot_tell_us": (
            "This measures the CHANGE, not its CORRECTNESS. The factors are anchored to "
            "CDIP MOP, a model rather than a measurement of the breaking wave, so every "
            "assumption in that anchoring is inherited here. Against ground truth we have "
            "two user labels. A clean before/after table is not evidence the after column "
            "is true."
        ),
    }
    _print_report(result, by_id, factors)
    json.dump(result, open(out_path, "w"), indent=2)
    print(f"\nwrote {out_path}")
    return 0


def _fmt_dist(d):
    return "  ".join(f"{k}:{v}" for k, v in sorted(d.items(), key=lambda kv: float(kv[0])))


def _print_report(res, by_id, factors):
    r = res["roster"]
    print()
    print("=" * 78)
    print("ROSTER-WIDE")
    print("=" * 78)
    print(f"  spots measured        {res['n_spots_measured']}")
    print(f"  rows                  {res['n_rows']}  "
          f"({res['n_unrateable_rows']} unrateable, missing effective/wind/tide)")
    print(f"  mean star change      {r['mean_delta']:+.3f}")
    print(f"  median star change    {r['median_delta']:+.3f}")
    print(f"  unchanged hours       {r['unchanged']}  ({100 * r['unchanged'] / r['hours']:.1f}%)")
    print(f"  3+ -> below 3         {r['three_plus_to_below_three']}  "
          f"({100 * r['three_plus_to_below_three'] / r['hours']:.1f}%)")
    print(f"  at the 1-star floor   {r['at_one_star_floor_before']} -> {r['at_one_star_floor_after']}")
    print(f"  flat (0 stars, <0.5ft) {r['flat_zero_before']} -> {r['flat_zero_after']}")
    print()
    print("  half-star boundaries crossed DOWNWARD (hours):")
    for b in HALF_STAR_BOUNDARIES:
        n = r["down_crossings"].get(str(b), 0)
        print(f"    {b:.1f}  {n:6d}")
    if any(r["up_crossings"].values()):
        print("  crossed UPWARD (a factor below 1.0 — we UNDER-publish that spot):")
        for k, v in r["up_crossings"].items():
            print(f"    {k}  {v:6d}")
    print()
    print(f"  distribution BEFORE   {_fmt_dist(r['dist_before'])}")
    print(f"  distribution AFTER    {_fmt_dist(r['dist_after'])}")

    ranked = sorted(((v["mean_delta"], k) for k, v in res["by_spot"].items() if v))
    print()
    print("=" * 78)
    print("MOVED MOST (largest star drop)")
    print("=" * 78)
    print(f"  {'spot':<38}{'factor':>8}{'hours':>7}{'mean d':>9}{'3+ ->':>7}")
    for d, slug in ranked[:12]:
        v = res["by_spot"][slug]
        print(f"  {slug[:37]:<38}{factors.get(slug, float('nan')):>8.2f}{v['hours']:>7}"
              f"{d:>+9.3f}{v['three_plus_to_below_three']:>7}")
    print()
    print("MOVED LEAST")
    print(f"  {'spot':<38}{'factor':>8}{'hours':>7}{'mean d':>9}{'3+ ->':>7}")
    for d, slug in ranked[-12:][::-1]:
        v = res["by_spot"][slug]
        print(f"  {slug[:37]:<38}{factors.get(slug, float('nan')):>8.2f}{v['hours']:>7}"
              f"{d:>+9.3f}{v['three_plus_to_below_three']:>7}")

    print()
    print("=" * 78)
    print("WHAT THIS CANNOT TELL US")
    print("=" * 78)
    for line in (
        "This measures the CHANGE, not its CORRECTNESS. Nothing above says the corrected",
        "stars are RIGHT.",
        "",
        "The factors are anchored to CDIP MOP. MOP is a model — a SWAN run over real",
        "bathymetry, which is the best nearshore reference available to us, but still a",
        "model and not a measurement of the breaking wave. Every assumption in that",
        "anchoring is inherited by every number above.",
        "",
        "Against actual ground truth we have TWO user labels. Two. A clean before/after",
        "table is not evidence that the after column is true, and a large, tidy, plausible",
        "shift is exactly what a systematically wrong factor would also produce.",
    ):
        print(f"  {line}")


# --------------------------------------------------------------------------- #
# Selftest — offline, no DB, no network                                        #
# --------------------------------------------------------------------------- #

def run_selftest():
    ok = True

    def check(n, c):
        nonlocal ok
        ok = ok and c
        print(f"  {'PASS' if c else 'FAIL'}  {n}")

    # --- the recomputation uses the PRODUCTION star function ---------------- #
    import pipeline.interpret as I
    check("composite_stars is imported from pipeline.interpret, not redefined here",
          composite_stars is I.composite_stars)
    check("size_score is the production one too", size_score is I.size_score)

    # A tripwire proves it is CALLED, not merely imported and then shadowed by a local
    # copy: swap the name this module resolves and the output must follow.
    import star_impact_of_face_correction as M
    saved = M.composite_stars
    try:
        M.composite_stars = lambda *a, **k: 4.25
        got = M.star_pair({"effective_size_ft": 3.0, "wind_mult": 1.0, "tide_mult": 1.0,
                           "chop_mult": 1.0, "period_quality": 1.0}, 2.0)
        check("star_pair calls the module-level composite_stars (tripwire returns 4.25)",
              got == (4.25, 4.25))
    finally:
        M.composite_stars = saved

    # --- arithmetic, pinned by LITERALS ------------------------------------- #
    # size_score knots: (0,0) (1,1) (2,2) (3,2.5) (4,3) (5,3.5) (6,4) (8,4.5) (10,5).
    # With all four quality factors at 1.0 the geometric mean is 1.0, so raw == size_score
    # and stars = clamp(round(size_score * 2) / 2, 1, 5).
    neutral = {"wind_mult": 1.0, "tide_mult": 1.0, "chop_mult": 1.0, "period_quality": 1.0}

    #   eff 3.0 -> size_score 2.5 (a knot) -> round(5.0)/2 = 2.5
    #   eff 3.0 / 2.0 = 1.5 -> size_score 1.5 (midway 1->2) -> round(3.0)/2 = 1.5
    check("eff 3.0, factor 2.0 -> 2.5 then 1.5 stars",
          star_pair({"effective_size_ft": 3.0, **neutral}, 2.0) == (2.5, 1.5))

    #   eff 6.0 -> size_score 4.0 (a knot) -> 4.0 stars
    #   eff 6.0 / 3.0 = 2.0 -> size_score 2.0 (a knot) -> 2.0 stars
    check("eff 6.0, factor 3.0 -> 4.0 then 2.0 stars",
          star_pair({"effective_size_ft": 6.0, **neutral}, 3.0) == (4.0, 2.0))

    #   eff 2.0 -> size_score 2.0 -> 2.0 stars
    #   eff 2.0 / 2.87 = 0.6969... -> size_score 0.6969 -> round(1.3937)/2 = 0.5 -> CLAMPED to 1.0
    check("the [1,5] clamp: eff 2.0, factor 2.87 -> 2.0 then 1.0 stars (not 0.5)",
          star_pair({"effective_size_ft": 2.0, **neutral}, 2.87) == (2.0, 1.0))

    #   eff 1.0 / 2.87 = 0.3484... which is BELOW the 0.5 ft cutoff -> 0.0, not the 1.0 floor
    check("the sub-0.5 ft cutoff returns 0.0, a different state from the 1.0 floor",
          star_pair({"effective_size_ft": 1.0, **neutral}, 2.87) == (1.0, 0.0))

    #   0.5 ft is the cutoff boundary and is INCLUSIVE of rating (< 0.5 is flat).
    check("eff exactly 0.5 rates 1.0, not 0.0",
          star_pair({"effective_size_ft": 0.5, **neutral}, None) == (1.0, 1.0))

    # A factor below 1.0 raises the rating — the direction is checked, not assumed.
    #   eff 2.0 / 0.5 = 4.0 -> size_score 3.0 -> 3.0 stars
    check("a factor below 1.0 moves stars UP (eff 2.0, factor 0.5 -> 2.0 then 3.0)",
          star_pair({"effective_size_ft": 2.0, **neutral}, 0.5) == (2.0, 3.0))

    # --- a missing factor leaves the row untouched -------------------------- #
    p = star_pair({"effective_size_ft": 6.0, **neutral}, None)
    check("a missing factor returns before == after", p == (4.0, 4.0))
    check("a missing factor is NOT dropped (it stays in the denominator)", p is not None)
    s = summarise([p])
    check("an unfactored hour contributes 0.0 delta, not an omission",
          s["hours"] == 1 and s["mean_delta"] == 0.0 and s["unchanged"] == 1)

    # --- rows that cannot be rated ------------------------------------------ #
    check("a null effective_size_ft is unrateable",
          star_pair({"effective_size_ft": None, **neutral}, 2.0) is None)
    check("a null wind_mult is unrateable",
          star_pair({"effective_size_ft": 3.0, "wind_mult": None, "tide_mult": 1.0}, 2.0) is None)
    #   null chop/period default to 1.0, mirroring composite_stars' own signature defaults
    check("null chop_mult / period_quality default to 1.0 rather than dropping the row",
          star_pair({"effective_size_ft": 3.0, "wind_mult": 1.0, "tide_mult": 1.0,
                     "chop_mult": None, "period_quality": None}, 2.0) == (2.5, 1.5))

    # --- boundary crossings -------------------------------------------------- #
    check("3.0 -> 2.5 crosses exactly the 3.0 boundary downward",
          crossings(3.0, 2.5) == ([3.0], []))
    check("4.0 -> 1.5 crosses 2.0, 2.5, 3.0, 3.5 and 4.0",
          crossings(4.0, 1.5) == ([2.0, 2.5, 3.0, 3.5, 4.0], []))
    check("no move crosses nothing", crossings(3.0, 3.0) == ([], []))
    check("an upward move is counted upward, not downward",
          crossings(2.0, 3.0) == ([], [2.5, 3.0]))

    # --- summarise ----------------------------------------------------------- #
    s = summarise([(4.0, 2.0), (3.0, 1.0), (1.0, 1.0)])
    #   deltas -2.0, -2.0, 0.0 -> mean -4.0/3 = -1.3333..., median -2.0
    check("summarise mean delta is -1.3333 (deltas -2, -2, 0)",
          abs(s["mean_delta"] - (-4.0 / 3.0)) < 1e-12)
    check("summarise median delta is -2.0", s["median_delta"] == -2.0)
    check("two hours cross 3+ -> below 3", s["three_plus_to_below_three"] == 2)
    #   before values 4.0/3.0/1.0 -> one at the floor; after values 2.0/1.0/1.0 -> two.
    check("one hour was at the 1-star floor before, two after",
          s["at_one_star_floor_before"] == 1 and s["at_one_star_floor_after"] == 2)
    check("distributions bucket by half star",
          s["dist_before"] == {"1.0": 1, "3.0": 1, "4.0": 1}
          and s["dist_after"] == {"1.0": 2, "2.0": 1})
    check("an empty input summarises to None", summarise([]) is None)

    # --- factor loading ------------------------------------------------------ #
    import tempfile
    d = tempfile.mkdtemp()
    p1 = os.path.join(d, "spread.json")
    json.dump({"by_spot": [
        {"slug": "good", "face_ratio": {"median": 2.87}},
        {"slug": "zero", "face_ratio": {"median": 0.0}},
        {"slug": "neg", "face_ratio": {"median": -1.0}},
        {"slug": "nul", "face_ratio": {"median": None}},
        {"slug": "nokey", "face_ratio": {}},
        {"noslug": True, "face_ratio": {"median": 3.0}},
    ]}, open(p1, "w"))
    loaded, err = load_factors(p1)
    check("load_factors keeps only the positive numeric median", err is None
          and loaded["factors"] == {"good": 2.87})
    check("zero / negative / null / absent medians are skipped and COUNTED, not defaulted to 1.0",
          sorted(s for s, _ in loaded["skipped"]) == ["neg", "nokey", "nul", "zero"])
    check("a record with no slug is dropped before the skip list (nothing to record it under)",
          "noslug" not in dict(loaded["skipped"]))
    _, err2 = load_factors(os.path.join(d, "definitely_absent.json"))
    check("a missing factor file is an error, not an empty dict", err2 is not None)
    p2 = os.path.join(d, "bad.json")
    open(p2, "w").write("{not json")
    _, err3 = load_factors(p2)
    check("a corrupt factor file is an error", err3 is not None)
    p3 = os.path.join(d, "noshape.json")
    json.dump({"spots": []}, open(p3, "w"))
    _, err4 = load_factors(p3)
    check("a file with no by_spot list is an error", err4 is not None)

    # --- chunking ------------------------------------------------------------ #
    check("chunk_ids splits 20 into 3 chunks of 8/8/4 at the proven size 8",
          [len(c) for c in chunk_ids(range(20), FORECAST_CHUNK_SPOTS)] == [8, 8, 4])
    check("the shipped chunk size is the proven 8", FORECAST_CHUNK_SPOTS == 8)
    check("chunking preserves every id exactly once",
          [i for c in chunk_ids(range(20), 8) for i in c] == list(range(20)))

    # --- Fort Point: is the 1-star floor permanent below ~1.25 ft? ----------- #
    #   size_score is the identity below 1 ft, so raw = eff and stars = round(2*eff)/2,
    #   clamped up to 1.0. raw must reach 1.25 to round to 1.5.
    check("0.90 ft effective rates 1.0 star with neutral factors",
          star_pair({"effective_size_ft": 0.90, **neutral}, None) == (1.0, 1.0))
    check("1.24 ft still rates 1.0; 1.30 ft reaches 1.5",
          star_pair({"effective_size_ft": 1.24, **neutral}, None)[0] == 1.0
          and star_pair({"effective_size_ft": 1.30, **neutral}, None)[0] == 1.5)
    check("0.49 ft is flat (0.0), 0.50 ft is the 1.0 floor",
          star_pair({"effective_size_ft": 0.49, **neutral}, None)[0] == 0.0
          and star_pair({"effective_size_ft": 0.50, **neutral}, None)[0] == 1.0)

    print()
    print("selftest: " + ("ALL PASS" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days-back", type=int, default=DEFAULT_DAYS_BACK,
                    help=f"window length in days (default {DEFAULT_DAYS_BACK})")
    ap.add_argument("--factors", default=MOP_SPREAD_PATH,
                    help=f"mop_spread.json (default {MOP_SPREAD_PATH}; Mac-local, gitignored)")
    ap.add_argument("--rows-cache", default=None,
                    help="save the fetched rows here, and replay them on a later run so the "
                         "measured set is genuinely FROZEN across runs")
    ap.add_argument("--chunk-size", type=int, default=FORECAST_CHUNK_SPOTS,
                    help=f"spot ids per forecasts statement (default {FORECAST_CHUNK_SPOTS})")
    ap.add_argument("--min-reproduction", type=float, default=DEFAULT_MIN_REPRODUCTION,
                    help="abort if fewer than this fraction of rows reproduce their published "
                         f"stars (default {DEFAULT_MIN_REPRODUCTION})")
    ap.add_argument("--limit", type=int, default=None, help="only the first N spots (smoke test)")
    ap.add_argument("--out", default=OUT, help=f"results JSON (default {OUT})")
    ap.add_argument("--selftest", action="store_true",
                    help="offline logic proof; no network, no DB")
    a = ap.parse_args(argv)
    if a.selftest:
        return run_selftest()
    return run(days_back=a.days_back, factors_path=a.factors, out_path=a.out,
               rows_cache=a.rows_cache, chunk_size=a.chunk_size,
               min_reproduction=a.min_reproduction, limit=a.limit)


if __name__ == "__main__":
    raise SystemExit(main())
