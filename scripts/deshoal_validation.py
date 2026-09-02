#!/usr/bin/env python3
"""PRE-SHIP GATE for the 10 m-to-breaking transform: validate the DESHOALING step alone.

WHY THIS EXISTS, AND WHY THE MOP HARNESS CANNOT DO IT
=====================================================
The planned transform takes MOP's contour Hs and shoals it forward to a breaking height.
scripts/mop_face_validation.py cannot test it: after the per-spot correction shipped, our
published face IS MOP Hs in feet, so any transform applied to it is a deterministic
function of MOP's own output. Scoring that against MOP reproduces the formula and calls it
agreement. It is the same circularity the MOP harness already documents for the 48
cdip_mop spots, one level up.

The NDBC buoy is independent: a different instrument, at a different place, in a different
depth, with no MOP in its provenance. So the one step of the transform that CAN be checked
without circularity is the REVERSE one — deshoal MOP's contour Hs back to a deep-water
equivalent and ask whether it agrees with what the buoy measured that hour.

If the reverse step is sound, the forward step uses the same relation and the same inputs.
If it is not, the transform does not ship, and this script says so.

THE METHOD, AND ITS SOURCE
==========================
Linear (Airy) wave theory, the standard shoaling relation as given in the US Army Corps
Shore Protection Manual / Coastal Engineering Manual and in Dean & Dalrymple, "Water Wave
Mechanics for Engineers and Scientists" (ch. 4-5). Steps, for height H at depth d and peak
period T:

  1. Angular frequency          w  = 2*pi / T
  2. Dispersion relation        w^2 = g*k*tanh(k*d), solved for the dimensionless kd by
                                Newton iteration on  x*tanh(x) = w^2*d/g.
  3. Shoaling coefficient       n  = 0.5 * (1 + 2kd / sinh(2kd))        (group/phase ratio)
                                C/C0   = tanh(kd)
                                Cg0/C0 = 0.5                            (deep water)
                                Ks = sqrt( (Cg0/C0) / (n * C/C0) )
                                   = sqrt( 0.5 / (n * tanh(kd)) )
  4. Deep-water equivalent      H0 = H(d) / Ks

Ks is BELOW 1 in the intermediate band (the wave is still slowing but not yet piling up)
and rises above 1 as it shallows, which is why the correction is not monotone in depth and
why a fixed depth cannot stand in for the real one.

WHAT IS DELIBERATELY NOT IN THE METHOD, AND WHY IT MATTERS TO THE VERDICT
========================================================================
REFRACTION IS NOT REMOVED. The full relation is H(d) = H0 * Ks * Kr, where Kr is the
refraction coefficient. This deshoals with Ks ONLY, so the recovered quantity is H0 * Kr,
not H0. For an obliquely-approaching swell Kr < 1 (energy spreads along the crest), so the
deshoaled value reads LOW against the buoy, and the shortfall grows with obliquity.

That matters because Kr is ALSO period-dependent — a longer wave feels the bottom sooner
and refracts more — so refraction alone can manufacture exactly the period-dependent
residual this gate is looking for. A period-dependent residual therefore has two possible
causes and the period regression cannot separate them:

    (a) the shoaling relation is wrong                  -> the transform must not ship
    (b) refraction, which we did not remove             -> expected, and not disqualifying

THE DISCRIMINATOR, which is why this script also regresses the residual on OBLIQUITY:
refraction's signature is a residual that scales with |MOP waveDp - the point's
metaShoreNormal|; a broken shoaling relation has no reason to care about approach angle.
Both regressions are reported side by side, and the verdict reads them together. Without
the obliquity term a period-dependent residual would fail the gate for the wrong reason.

BOTH SIDES ARE DESHOALED. An NDBC buoy is not automatically in deep water: at 30 m and
16 s the deep-water wavelength is 400 m, so d/L0 = 0.075 and the buoy itself sits in the
intermediate band. Comparing a deshoaled MOP value against a raw buoy Hs would then charge
the buoy's own shoaling to the shoaling relation under test. Buoy depth comes from
nwps_nearshore._ndbc_station_meta (a known table, augmented by scraping the NDBC station
page on a machine with egress); when it is unknown the buoy is treated as deep (Ks = 1)
and the spot is COUNTED AND REPORTED as such rather than silently mixed in.

THE PRE-SHIP PREDICTION, registered before the run and printed beside the result
===============================================================================
    deshoaled H0 agrees with buoy Hs within about 20% RMS
    correlation above 0.7
    no systematic period-dependent bias

Printed next to the measured values so a reader sees whether it held, and the script exits
non-zero and says the transform must not ship if it did not.

WHAT THIS STILL CANNOT TELL US. It tests the deshoaling step, not the breaking step. A
clean pass says the wave mechanics between the contour and deep water are right; it says
NOTHING about the gamma closure that turns a shoaled height into a breaking one, and
nothing about whether a breaking height is the face a surfer reports. Those need the
surf_reports labels.

USAGE
    python3 scripts/deshoal_validation.py                  # 14 days, buoys within 80 km
    python3 scripts/deshoal_validation.py --days-back 30 --max-buoy-km 60
    python3 scripts/deshoal_validation.py --selftest       # offline; no network, no DB

INPUTS
    scripts/mop_points.json   MOP point cache: lat/lon/url/water_depth/shore_normal.
                              Mac-local and gitignored; build with
                              `python3 scripts/mop_blacks_slice.py build-cache`.
    pipeline/data/spot_face_factors.json   the 132 corrected slugs (committed).
    CDIP THREDDS              MOP nowcast per point. 403 in the dev container.
    Supabase                  buoy_observations. READ-ONLY: SELECTs only, no write path.
"""
from __future__ import annotations

import argparse
import contextlib
import datetime
import io
import json
import math
import os
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)
sys.path.insert(0, ROOT)

# REUSED, NOT REIMPLEMENTED — the match, the cache and the MOP pull are the ones the
# rollout already validated. A second copy could drift from them silently.
from mop_blacks_slice import CACHE as MOP_CACHE_PATH               # noqa: E402
from mop_blacks_slice import load_cache                            # noqa: E402
from mop_ca_rollout import MATCH_SANITY_M, _match, _slug           # noqa: E402
from mop_face_validation import FORECAST_CHUNK_SPOTS, ROSTER, _stats, chunk_ids  # noqa: E402
from pipeline.config import SPOT_FACE_FACTORS_FILE                 # noqa: E402
from pipeline.forecast.mop import _iso_to_epoch, _norm_epoch, nowcast_url, pull_mop_window  # noqa: E402

OUT = os.path.join(HERE, "deshoal_validation_out.json")

DEFAULT_DAYS_BACK = 14
DEFAULT_MAX_BUOY_KM = 80.0

# Standard gravity, m/s^2. The value matters at the 4th decimal of Ks; stated rather than
# left to a library so the selftest can assert an exact literal.
G = 9.80665

# Hour-join tolerance. NDBC observations land at station-specific minutes (:26, :50, ...),
# never on the hour, while MOP rows are hourly on the hour. A floor-to-the-hour bucket
# would pair a :50 reading with a :00 row and call that a match; this pairs on absolute
# time difference instead, and the tolerance is stated rather than implied.
JOIN_TOLERANCE_S = 1800

# A buoy deeper than this is treated as deep water (Ks = 1) with no further correction.
# At 200 m the deep-water limit d/L0 >= 0.5 holds for every period below 16 s, and the
# residual shoaling above that is under 0.1%.
DEEP_WATER_M = 200.0

# Below this the ratio is noise rather than a measurement: a 0.05 m Hs on either side turns
# any comparison into a three-figure ratio. Excluded hours are counted, not silently
# dropped.
HS_FLOOR_M = 0.25

# THE REGISTERED PREDICTION. Named constants so the printed prediction and the pass/fail
# test read the same numbers — a prediction that can drift from its own test is not one.
PREDICT_RMS_PCT_MAX = 20.0
PREDICT_CORR_MIN = 0.70
# |slope of residual vs period|, in residual-fraction per second. 0.02/s means a 10 s
# spread in period moves the residual by 20% — the same size as the whole error budget, so
# anything at or above this is a systematic trend rather than scatter.
PREDICT_PERIOD_SLOPE_MAX = 0.02

# ---------------------------------------------------------------------------------------- #
# RUN 2 PREDICTIONS. Run 1's thresholds above are UNCHANGED and still evaluated; these are
# added alongside, not in place of them, and they are registered here — in code, before any
# number is fetched — for the same reason the first set was.
#
# WHAT RUN 1 MEASURED: bias -0.325 (median -0.337), RMS 40.1%, correlation 0.376, period
# slope -0.00871/s, obliquity slope -0.001295/deg, over 33,620 pairs on 104 spots. The
# PERIOD slope passed comfortably; everything else failed on a one-third deficit in the same
# direction everywhere. Run 2 exists to decide WHY, between two mechanisms that predict the
# same sign and the same rough size but differ sharply on where the deficit concentrates.
# ---------------------------------------------------------------------------------------- #

# P2 — EXPOSURE. If the deficit is MOP's directional filtering, it must concentrate where
# there is something to filter. Bias should move toward zero as the open swell window widens,
# monotonically across window-width quartiles.
#
# THE MAGNITUDE THRESHOLD IS DERIVED, NOT PICKED. Over the 132 corrected spots the open
# window (union of swell_window_arcs under interpret.bearing_in_arc) runs 62 deg to 201 deg,
# median 148, quartile cuts near 122 / 148 / 164. A spot in the top quartile has 164-201 deg
# of open water: there is almost no window left for MOP to remove. If those spots still show
# the full roster deficit, window geometry cannot be what produces it.
PREDICT_EXPOSURE_WIDEST_BIAS_MAX = 0.15    # |median bias| in the widest quartile
PREDICT_EXPOSURE_SPREAD_MIN = 0.15         # widest-quartile median minus narrowest-quartile
PREDICT_EXPOSURE_SPEARMAN_MIN = 0.40       # rank corr of per-spot median bias vs window_deg
# Below this spread the bias is flat across exposure and the hypothesis is REFUTED outright
# rather than merely unsupported.
REFUTE_EXPOSURE_SPREAD_MAX = 0.05

# P3 — OBLIQUITY WITHIN SPOT. Sharper than P2 because it varies inside one spot and so cannot
# be produced by which spots happen to be sheltered. Each spot's residuals are demeaned by
# that spot's OWN median before banding, which removes the between-spot term entirely; what
# survives is the hour-to-hour effect of swell angle at fixed geometry.
PREDICT_OBLIQUITY_WITHIN_SLOPE_MAX = -0.0005   # per degree; must be at least this negative
# A within-spot slope this small means run 1's pooled -0.001295/deg was a between-spot
# artefact — oblique spots being sheltered spots — and says nothing about filtering.
REFUTE_OBLIQUITY_WITHIN_SLOPE_ABS = 0.0002

# P4 — THE COMPETING HYPOTHESIS, which the brief does not name and which predicts the SAME
# signature. buoy_observations.hs is NDBC .std, the DOMINANT (wind sea + swell) height —
# migration 004's own header says so. MOP's waveHs at a nearshore point carries the offshore
# spectrum propagated in, so summer NW wind sea generated locally at the buoy is in the buoy
# number and not in MOP's. A uniform negative bias, a badly degraded correlation and NO period
# dependence is exactly what that produces, because it is an additive uncorrelated energy term
# rather than a period-dependent transfer.
#
# Both sides carry a swell-only height already: MOP's from _split_swell_hs over
# waveEnergyDensity (f <= 0.10 Hz), the buoy's from NDBC .spec SwH (migration 004). Rerunning
# on those, over the IDENTICAL joined hours with the IDENTICAL Tp and depths, changes exactly
# one variable. If the bias barely moves, wind sea is not the explanation and filtering
# survives. If it collapses toward zero, filtering is at best a partial account.
PREDICT_SWELL_ONLY_DELTA_MAX = 0.05        # |bias(swell) - bias(total)| if filtering is it
REFUTE_SWELL_ONLY_DELTA_MIN = 0.15         # movement toward zero this large indicts wind sea

# Fixed geometric bands, not data-derived: the question is about angle, and degrees are the
# natural unit to report it in. The last band is open-ended because swell more than 90 deg off
# the shore normal is arriving from behind the shore-normal half-plane.
OBLIQUITY_BAND_EDGES = (0.0, 15.0, 30.0, 45.0, 60.0, 90.0)

# PostgREST caps a select at 1000 rows, so paging inside a chunk is load-bearing.
BUOY_PAGE_ROWS = 1000

# THE BUOY FETCH: ONE BUOY PER STATEMENT, ORDERED BY observed_at. Same symptom as the
# forecasts fetch, a DIFFERENT cure, because the table is shaped differently.
#
# THE SYMPTOM. This function raised PostgREST 57014 ("canceling statement due to statement
# timeout") on the FIRST page, offset 0, before a row came back — the same signature as the
# forecasts fetch and the same meaning: a plan problem, not a volume one. But it failed
# while ALREADY chunked at FORECAST_CHUNK_SPOTS = 8, so chunking is not what was missing.
# The variable that was still wrong is the ORDER BY.
#
# WHAT ACTUALLY DIFFERS, read from 001_initial_schema.sql rather than assumed:
#
#   forecasts          (79-105)   PK(id) · UNIQUE(spot_id, valid_time, source)
#                                 idx_forecasts_spot_time(spot_id, valid_time)
#                                 idx_forecasts_valid_time(valid_time)
#   buoy_observations (111-126)   PK(id) · UNIQUE(buoy_id, observed_at)
#                                 idx_buoy_obs_buoy_time(buoy_id, observed_at)
#
# Two consequences. (1) buoy_observations has NO standalone observed_at index — its only
# route into a time range leads with buoy_id, so an unconstrained time scan has nothing to
# use, where forecasts always has idx_forecasts_valid_time to fall back on. (2) It carries a
# UNIQUE constraint on exactly the columns its one time-bearing index leads with. forecasts
# does not, and (2) is the whole fix.
#
# `order("id")` is on the forecasts fetch because id is the only TOTAL order it has:
# valid_time ties across spots at the same hour, so offset paging on it can skip or repeat
# rows across a page boundary (mop_face_validation.py:379-382 says so). Here that constraint
# lifts. Fix buoy_id to ONE value and observed_at is unique WITHIN THE STATEMENT by the
# UNIQUE constraint, so order("observed_at") is a total order — proved from the schema, not
# measured — and it is the physical order of idx_buoy_obs_buoy_time, so the statement is a
# plain range scan with no sort node and nothing to tempt the planner into walking the PK
# from id=1.
#
# WHY THE PK WALK IS WORSE HERE THAN ON forecasts. Nothing prunes this table: db_import
# upserts a rolling 24 h per buoy on every run (db_import.py:736-796) and the only delete()
# anywhere in pipeline/ is on `spots`. It is append-only, so the older rows an id-ascending
# walk must cross grow every day the deployment runs. The failure gets worse with age.
#
# WHY 1 AND NOT THE 8 PROVEN FOR forecasts. 8 was the largest value with direct POSITIVE
# evidence at a planner cliff whose location was unknown — a guess bounded by evidence. 1 is
# not that kind of number: it is the size at which the total-order argument above holds, so
# it is chosen by proof rather than by probing. It also deletes the IN list, so the
# selectivity misestimate that drove the forecasts cliff cannot arise at all. And it is the
# shape of the one reader of this table that has never timed out —
# frontend/app/spot/[slug]/page.tsx:116 runs .eq('buoy_id', …).order('observed_at', …).
# The cost is 23 statements instead of 3, each a clean index range scan, against a query
# that currently never returns.
#
# buoy_id BEING TEXT changes none of this. Equality on a text column uses the index the same
# way; what text costs is that `id` order carries no correlation with buoy_id at all, so the
# id-walk has no clustering to make it accidentally cheap.
#
# ABOVE 1 IS A PROBE, NOT A SETTING. With more than one buoy in a statement observed_at
# stops being unique — NDBC stations report on aligned minutes, so ties across buoys are the
# common case rather than a corner — and offset paging stops being deterministic. The
# --chunk-size flag exists to measure the planner, not to run production.
BUOY_CHUNK_IDS = 1


# --------------------------------------------------------------------------- #
# Linear wave theory                                                           #
# --------------------------------------------------------------------------- #

def dispersion_kd(period_s: float, depth_m: float) -> float | None:
    """Dimensionless wavenumber kd solving w^2 = g*k*tanh(k*d), by Newton iteration.

    Newton on f(x) = x*tanh(x) - y with y = w^2*d/g converges in a handful of steps from
    the standard initial guess (y for y > 1, sqrt(y) otherwise), and the derivative
    tanh(x) + x*sech^2(x) is strictly positive so there is no turning point to fall off.
    Cross-checked against a 300-step bisection on the same equation: the two agree to
    machine precision (<= 2.2e-16) at every (T, d) in the selftest.
    """
    if not period_s or not depth_m or period_s <= 0 or depth_m <= 0:
        return None
    w = 2.0 * math.pi / float(period_s)
    y = w * w * float(depth_m) / G
    x = y if y > 1.0 else math.sqrt(y)
    for _ in range(100):
        th = math.tanh(x)
        f = x * th - y
        fp = th + x * (1.0 - th * th)
        if fp == 0.0:
            break
        step = f / fp
        x -= step
        if abs(step) < 1e-14:
            break
    return x


def shoaling_coefficient(period_s: float, depth_m: float) -> float | None:
    """Ks = sqrt(0.5 / (n * tanh(kd))), the linear-theory shoaling coefficient.

    None when the inputs are unusable. For a depth at or beyond DEEP_WATER_M the caller
    should skip this entirely — sinh(2kd) overflows for large kd long before the answer
    stops being 1.0 — so this guards that case by returning 1.0 directly.
    """
    if not period_s or not depth_m or period_s <= 0 or depth_m <= 0:
        return None
    if depth_m >= DEEP_WATER_M:
        return 1.0
    kd = dispersion_kd(period_s, depth_m)
    if kd is None or kd <= 0:
        return None
    if kd > 20.0:                      # deep by any measure; sinh(2kd) would overflow
        return 1.0
    n = 0.5 * (1.0 + 2.0 * kd / math.sinh(2.0 * kd))
    denom = n * math.tanh(kd)
    if denom <= 0:
        return None
    return math.sqrt(0.5 / denom)


def deshoal(height_m: float, period_s: float, depth_m: float | None) -> float | None:
    """H0 = H(d) / Ks(T, d) — the deep-water-equivalent height.

    *depth_m* None means "deep water, unknown or beyond DEEP_WATER_M": Ks = 1 and the
    height passes through. That is a real modelling choice and the caller counts how often
    it was taken, because a buoy silently assumed deep is a silently wrong comparison.
    """
    if height_m is None or height_m < 0:
        return None
    if depth_m is None:
        return float(height_m)
    ks = shoaling_coefficient(period_s, depth_m)
    if ks is None or ks <= 0:
        return None
    return float(height_m) / ks


# --------------------------------------------------------------------------- #
# Joining, and the statistics the gate reads                                   #
# --------------------------------------------------------------------------- #

def join_within(mop_rows, buoy_rows, tol_s: int = JOIN_TOLERANCE_S):
    """Pair each MOP row with the NEAREST-IN-TIME buoy row inside *tol_s*.

    NOT mop_face_validation.join_on_hour, and the difference is deliberate: that function
    buckets both sides by floor(epoch/3600) and requires an exact bucket match, which is
    right when both sides are hourly on the hour. NDBC observations are not — they land at
    station-specific minutes — so an hour bucket would pair a :50 reading with a :00 row
    and record it as an exact match. This pairs on absolute time difference and keeps the
    gap, so the join can be audited.

    Nearest, not first: a buoy reporting twice inside the window must not have the pairing
    decided by list order.
    """
    if not mop_rows or not buoy_rows:
        return []
    buoy = sorted(buoy_rows, key=lambda r: r["t"])
    times = [r["t"] for r in buoy]
    out = []
    for m in mop_rows:
        t = m.get("t")
        if t is None:
            continue
        i = _bisect(times, t)
        best, best_dt = None, None
        for j in (i - 1, i, i + 1):
            if 0 <= j < len(buoy):
                dt = abs(buoy[j]["t"] - t)
                if best_dt is None or dt < best_dt:
                    best, best_dt = buoy[j], dt
        if best is None or best_dt > tol_s:
            continue
        out.append({"t": t, "dt_s": best_dt, "mop": m, "buoy": best})
    return out


def _bisect(sorted_vals, x):
    lo, hi = 0, len(sorted_vals)
    while lo < hi:
        mid = (lo + hi) // 2
        if sorted_vals[mid] < x:
            lo = mid + 1
        else:
            hi = mid
    return lo


def pearson(a, b):
    """Pearson r, or None when either series is constant or shorter than 3."""
    if len(a) != len(b) or len(a) < 3:
        return None
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    va = sum((x - ma) ** 2 for x in a)
    vb = sum((y - mb) ** 2 for y in b)
    if va <= 0 or vb <= 0:
        return None
    cov = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    return cov / math.sqrt(va * vb)


def ols_slope(xs, ys):
    """Least-squares slope of ys on xs, or None. The period- and obliquity-dependence
    tests are both this: a residual that trends with a covariate has a non-zero slope."""
    if len(xs) != len(ys) or len(xs) < 3:
        return None
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return None
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx


def _rank(vals):
    """Ranks of *vals*, 1-based, TIES AVERAGED. The tie handling is not cosmetic here:
    window_deg is an integer count, so ties are common and competition-ranking them would
    bias the rank correlation."""
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    ranks = [0.0] * len(vals)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and vals[order[j + 1]] == vals[order[i]]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[order[k]] = avg
        i = j + 1
    return ranks


def spearman(a, b):
    """Rank correlation, or None. Pearson of the averaged ranks — one number for whether the
    trend across exposure is monotonic, which is what P2 asks and what a slope does not say."""
    if len(a) != len(b) or len(a) < 3:
        return None
    return pearson(_rank(list(a)), _rank(list(b)))


def band_stats(residuals):
    """n / mean / median / RMS% over a plain list of residual fractions, or None if empty."""
    vals = [r for r in residuals if r is not None]
    if not vals:
        return None
    return {"n": len(vals),
            "bias_frac": statistics.fmean(vals),
            "median_frac": statistics.median(vals),
            "rms_pct": 100.0 * math.sqrt(statistics.fmean([v * v for v in vals]))}


def residual_stats(pairs):
    """bias / RMS% / correlation / period slope / obliquity slope over joined pairs.

    residual = H0_mop / H0_buoy - 1, a FRACTION. RMS is reported as a percentage of the
    buoy height so it can be read against the 20% prediction directly.
    """
    if not pairs:
        return None
    res = [p["residual"] for p in pairs]
    mop = [p["h0_mop_m"] for p in pairs]
    buoy = [p["h0_buoy_m"] for p in pairs]
    tp = [p["tp_s"] for p in pairs]
    obl = [p["obliquity_deg"] for p in pairs if p["obliquity_deg"] is not None]
    obl_res = [p["residual"] for p in pairs if p["obliquity_deg"] is not None]
    return {
        "n": len(pairs),
        "bias_frac": statistics.fmean(res),
        "median_frac": statistics.median(res),
        "rms_pct": 100.0 * math.sqrt(statistics.fmean([r * r for r in res])),
        "corr": pearson(mop, buoy),
        "period_slope_per_s": ols_slope(tp, res),
        "obliquity_slope_per_deg": ols_slope(obl, obl_res) if len(obl) >= 3 else None,
        "n_with_obliquity": len(obl),
        "h0_mop": _stats(mop),
        "h0_buoy": _stats(buoy),
    }


def verdict(stats):
    """(passed: bool, [reason lines]) against the registered prediction.

    A period-dependent residual is only disqualifying when obliquity does NOT explain it —
    see the module docstring. When both slopes are present and the obliquity term is the
    stronger, the period trend is attributed to refraction and reported as EXPLAINED rather
    than failed. That is a judgement the script states out loud rather than hiding in a
    threshold.
    """
    if not stats or not stats["n"]:
        return False, ["no joined pairs — nothing was measured"]
    lines, ok = [], True
    rms, corr = stats["rms_pct"], stats["corr"]
    if rms <= PREDICT_RMS_PCT_MAX:
        lines.append(f"PASS  RMS {rms:.1f}% <= {PREDICT_RMS_PCT_MAX:.0f}%")
    else:
        ok = False
        lines.append(f"FAIL  RMS {rms:.1f}% > {PREDICT_RMS_PCT_MAX:.0f}%")
    if corr is None:
        ok = False
        lines.append("FAIL  correlation undefined (constant or too-short series)")
    elif corr >= PREDICT_CORR_MIN:
        lines.append(f"PASS  correlation {corr:.3f} >= {PREDICT_CORR_MIN:.2f}")
    else:
        ok = False
        lines.append(f"FAIL  correlation {corr:.3f} < {PREDICT_CORR_MIN:.2f}")
    ps, os_ = stats["period_slope_per_s"], stats["obliquity_slope_per_deg"]
    if ps is None:
        ok = False
        lines.append("FAIL  period slope undefined")
    elif abs(ps) <= PREDICT_PERIOD_SLOPE_MAX:
        lines.append(f"PASS  period slope {ps:+.4f}/s within +/-{PREDICT_PERIOD_SLOPE_MAX}")
    elif os_ is not None and abs(os_) * 45.0 >= abs(ps) * 5.0:
        # A 45 deg obliquity swing moving the residual at least as much as a 5 s period
        # swing: refraction is the better explanation of the two, so the period trend is
        # reported as explained rather than counted as a failure of the shoaling relation.
        lines.append(f"PASS* period slope {ps:+.4f}/s EXCEEDS the threshold, but the "
                     f"obliquity slope {os_:+.5f}/deg explains at least as much — "
                     f"attributed to refraction (Kr), which this method does not remove")
    else:
        ok = False
        lines.append(f"FAIL  period slope {ps:+.4f}/s > +/-{PREDICT_PERIOD_SLOPE_MAX} and "
                     f"obliquity does not explain it — the shoaling relation is suspect")
    return ok, lines


def hypothesis_verdict(bands, exp_rho, obl, swl):
    """(supported: bool, [lines]) for the DIRECTIONAL-FILTERING hypothesis, against P2-P4.

    Separate from verdict() on purpose. verdict() asks whether the shoaling relation is
    validated; this asks why it appeared not to be. A run can fail the first and still answer
    the second, and conflating them into one boolean would lose exactly the information the
    second run exists to produce.

    SUPPORTED requires all three: exposure monotone and large enough (P2), a within-spot
    obliquity slope (P3), and the deficit surviving the swell-only rerun (P4). Any one of the
    three refutation thresholds being hit is reported as REFUTED rather than as "not
    supported" — they are stated separately in the constants so that a null result and a
    contrary result cannot be read as the same thing.
    """
    lines, ok = [], True
    st = [b["stats"] for b in bands if b.get("stats")]
    if len(st) < 2:
        return False, ["P2 exposure: too few populated bands to test"]

    narrow, wide = st[0]["median_frac"], st[-1]["median_frac"]
    spread = wide - narrow
    med_ok = all(st[i]["median_frac"] <= st[i + 1]["median_frac"] + 1e-12
                 for i in range(len(st) - 1))
    if spread < REFUTE_EXPOSURE_SPREAD_MAX:
        ok = False
        lines.append(f"REFUTED  P2 exposure: median bias is FLAT across the window — "
                     f"{narrow:+.3f} narrowest vs {wide:+.3f} widest, spread {spread:+.3f} "
                     f"< {REFUTE_EXPOSURE_SPREAD_MAX}. The deficit does not live where the "
                     f"filtering is, so filtering is not what produces it.")
    elif spread < PREDICT_EXPOSURE_SPREAD_MIN:
        ok = False
        lines.append(f"FAIL     P2 exposure spread {spread:+.3f} < {PREDICT_EXPOSURE_SPREAD_MIN}")
    else:
        lines.append(f"PASS     P2 exposure spread {spread:+.3f} "
                     f"({narrow:+.3f} narrowest -> {wide:+.3f} widest)")
    lines.append(("PASS     " if med_ok else "FAIL     ")
                 + "P2 median bias is monotonic across window quartiles"
                 + ("" if med_ok else " — it is not"))
    ok = ok and med_ok
    if abs(wide) <= PREDICT_EXPOSURE_WIDEST_BIAS_MAX:
        lines.append(f"PASS     P2 widest quartile |bias| {abs(wide):.3f} <= "
                     f"{PREDICT_EXPOSURE_WIDEST_BIAS_MAX}")
    else:
        ok = False
        lines.append(f"FAIL     P2 widest quartile |bias| {abs(wide):.3f} > "
                     f"{PREDICT_EXPOSURE_WIDEST_BIAS_MAX} — spots with almost no window left "
                     f"to filter still show most of the deficit")
    if exp_rho is None:
        ok = False
        lines.append("FAIL     P2 rank correlation undefined")
    elif exp_rho >= PREDICT_EXPOSURE_SPEARMAN_MIN:
        lines.append(f"PASS     P2 Spearman(window, per-spot median bias) {exp_rho:+.3f} >= "
                     f"{PREDICT_EXPOSURE_SPEARMAN_MIN}")
    else:
        ok = False
        lines.append(f"FAIL     P2 Spearman {exp_rho:+.3f} < {PREDICT_EXPOSURE_SPEARMAN_MIN}")

    ws = obl.get("within_slope")
    if ws is None:
        ok = False
        lines.append("FAIL     P3 within-spot obliquity slope undefined")
    elif abs(ws) < REFUTE_OBLIQUITY_WITHIN_SLOPE_ABS:
        ok = False
        lines.append(f"REFUTED  P3 within-spot obliquity slope {ws:+.6f}/deg is ~zero "
                     f"(|.| < {REFUTE_OBLIQUITY_WITHIN_SLOPE_ABS}). Run 1's pooled "
                     f"{obl.get('pooled_slope') or float('nan'):+.6f}/deg was a BETWEEN-spot "
                     f"artefact: oblique spots are sheltered spots. It says nothing about "
                     f"filtering.")
    elif ws <= PREDICT_OBLIQUITY_WITHIN_SLOPE_MAX:
        lines.append(f"PASS     P3 within-spot obliquity slope {ws:+.6f}/deg <= "
                     f"{PREDICT_OBLIQUITY_WITHIN_SLOPE_MAX} "
                     f"({100.0 * abs(ws) * 90.0:.1f}% over 90 deg, at fixed geometry)")
    else:
        ok = False
        lines.append(f"FAIL     P3 within-spot obliquity slope {ws:+.6f}/deg is the WRONG "
                     f"SIGN or too small — more oblique swell does not widen the gap")

    if not swl.get("n"):
        ok = False
        lines.append("FAIL     P4 no hour carries a swell height on BOTH sides — untestable")
    else:
        d = swl["delta_bias"]
        lines.append(f"         P4 subsample: {swl['n']} pairs "
                     f"({100.0 * swl['coverage_frac']:.0f}% of the roster) carry both "
                     f"swell partitions")
        if abs(d) <= PREDICT_SWELL_ONLY_DELTA_MAX:
            lines.append(f"PASS     P4 swell-only bias moves {d:+.3f} (<= "
                         f"{PREDICT_SWELL_ONLY_DELTA_MAX}) — the deficit is NOT wind sea")
        elif d >= REFUTE_SWELL_ONLY_DELTA_MIN:
            ok = False
            lines.append(f"REFUTED  P4 swell-only bias moves {d:+.3f} toward zero "
                         f"({swl['total']['bias_frac']:+.3f} -> "
                         f"{swl['swell']['bias_frac']:+.3f}). Most of the deficit was WIND "
                         f"SEA in the buoy's .std height, not energy MOP removed.")
        else:
            ok = False
            lines.append(f"FAIL     P4 swell-only bias moves {d:+.3f}, between the "
                         f"{PREDICT_SWELL_ONLY_DELTA_MAX} the hypothesis allows and the "
                         f"{REFUTE_SWELL_ONLY_DELTA_MIN} that would indict wind sea — both "
                         f"mechanisms are contributing")
    return ok, lines


# --------------------------------------------------------------------------- #
# Population + fetches                                                         #
# --------------------------------------------------------------------------- #

def corrected_population(roster_path=ROSTER, factors_path=None, max_buoy_km=DEFAULT_MAX_BUOY_KM):
    """The corrected spots that carry a buoy inside *max_buoy_km*, plus the ones dropped."""
    factors_path = factors_path or SPOT_FACE_FACTORS_FILE
    doc = json.loads(open(factors_path).read())
    slugs = set(doc.get("factors") or {})
    roster = json.load(open(roster_path))
    kept, no_buoy, too_far = [], [], []
    for s in roster:
        if _slug(s.get("name")) not in slugs:
            continue
        bid, dist = s.get("nearest_buoy_id"), s.get("nearest_buoy_dist_km")
        if not bid or dist is None:
            no_buoy.append(_slug(s.get("name")))
        elif float(dist) > max_buoy_km:
            too_far.append((_slug(s.get("name")), float(dist)))
        else:
            kept.append(s)
    return kept, {"no_buoy": no_buoy, "too_far": too_far, "n_factors": len(slugs)}


def fetch_mop_series(url, t0, t1):
    """[{t, hs, tp, dp, swell_hs}] for one MOP point.

    NOT mop_face_validation.fetch_mop_by_hour: that deliberately discards Tp and Dp
    ("carrying MOP's period would invite someone to feed it into face_ft and re-create the
    circularity"). This study NEEDS both — Tp drives the dispersion relation and Dp drives
    the obliquity discriminator — so it reads the same pull_mop_window and keeps them.

    swell_hs is kept for P4 and costs nothing: pull_mop_window already computes it from
    waveEnergyDensity via _split_swell_hs, and run 1 simply threw it away. It is None for a
    point whose file carries no spectrum, which is counted rather than assumed present.
    """
    rows = pull_mop_window(url, t0, t1)
    out = []
    for r in rows:
        if r.get("hs") is None or not r.get("tp"):
            continue
        t = _norm_epoch(r.get("t"))
        if t is None:
            continue
        out.append({"t": t, "hs": float(r["hs"]), "tp": float(r["tp"]),
                    "dp": (float(r["dp"]) if r.get("dp") is not None else None),
                    "swell_hs": (float(r["swell_hs"]) if r.get("swell_hs") is not None
                                 else None)})
    return out


def _fetch_buoy_chunk(client, ids, t0_iso, t1_iso, page=BUOY_PAGE_ROWS):
    """Every row in the window for ONE chunk of buoy ids, paginated. Raw rows, unparsed.

    A one-id chunk — the shipped path — uses .eq rather than a one-element .in_. Postgres
    plans them the same, but .eq is the exact shape page.tsx:116 already runs against this
    table without timing out, and there is no reason to ship a second shape when the proven
    one is available. See BUOY_CHUNK_IDS for why the order is observed_at and not id.

    *page* is a parameter only so --selftest can exercise multi-page paging on a handful of
    rows; production always uses BUOY_PAGE_ROWS.
    """
    out, frm = [], 0
    while True:
        q = client.table("buoy_observations").select(
            "buoy_id, observed_at, hs, tp, swell_hs")
        q = q.eq("buoy_id", ids[0]) if len(ids) == 1 else q.in_("buoy_id", list(ids))
        resp = (q.gte("observed_at", t0_iso).lte("observed_at", t1_iso)
                 .order("observed_at").range(frm, frm + page - 1).execute())
        rows = resp.data or []
        if not rows:
            break
        out.extend(rows)
        if len(rows) < page:
            break
        frm += page
    return out


def fetch_buoy_series(client, buoy_ids, t0_iso, t1_iso,
                      chunk_size=BUOY_CHUNK_IDS, page=BUOY_PAGE_ROWS):
    """{buoy_id: [{t, hs, tp}]} from buoy_observations, one buoy per statement.

    Chunked over buoy ids — see BUOY_CHUNK_IDS for why, and why 1 rather than the 8 the
    forecasts fetch uses. Each id lands in exactly one chunk, so the reassembled dict is
    what a single un-chunked fetch would have produced; --selftest pins that against a
    literal.

    PROGRESS IS PRINTED PER CHUNK, not at the end. At one buoy per statement that is one
    line per buoy naming the buoy, so a run that stalls says which station it stalled on —
    a 23-buoy fetch that prints nothing until it finishes is indistinguishable from a hung
    one, which is how the 57014 presented in the first place.

    Rows carrying no parseable observed_at or no hs are dropped here rather than filtered in
    SQL: a NULL hs is a real NDBC row that simply reported no wave height that minute, and
    counting it out at the parse is what makes "rows" and "usable" separately visible in the
    progress line.
    """
    ids = sorted(set(buoy_ids))
    chunks = chunk_ids(ids, chunk_size)
    out, kept = {}, 0
    for i, chunk in enumerate(chunks, 1):
        rows = _fetch_buoy_chunk(client, chunk, t0_iso, t1_iso, page=page)
        before = kept
        for r in rows:
            t = _norm_epoch(_iso_to_epoch(r.get("observed_at")))
            if t is None or r.get("hs") is None:
                continue
            out.setdefault(r["buoy_id"], []).append(
                {"t": t, "hs": float(r["hs"]),
                 "tp": (float(r["tp"]) if r.get("tp") is not None else None),
                 # NDBC .spec SwH — the swell partition only, migration 004. Sparser than hs:
                 # not every station files a .spec. None here is normal and is counted.
                 "swell_hs": (float(r["swell_hs"]) if r.get("swell_hs") is not None
                              else None)})
            kept += 1
        print(f"    chunk {i:3d}/{len(chunks)}  {','.join(map(str, chunk)):<14s} "
              f"{len(rows):5d} rows  {kept - before:5d} usable  ({kept} total)", flush=True)
    return out


def buoy_depth_m(buoy_id):
    """Mooring depth in metres, or None. Known table first, NDBC station page on egress."""
    try:
        from pipeline.forecast.nwps_nearshore import _ndbc_station_meta
        return (_ndbc_station_meta(buoy_id) or {}).get("depth_m")
    except Exception:  # noqa: BLE001 - offline or import failure -> unknown, counted
        return None


def window_deg(arcs):
    """Open swell window, in whole degrees of bearing, as the UNION of *arcs*.

    THE EXPOSURE MEASURE, and it is not invented here. Membership is
    interpret.bearing_in_arc — the same predicate the production directional gain uses — so
    "open" means exactly what the rating means by it, and a spot's exposure cannot disagree
    with its own rating. Sweeping integer bearings rather than doing interval arithmetic on
    min/max/span is what makes overlapping arcs count once and a 0/360 wrap need no case:
    both are properties of the predicate, not of this function.

    NON-CIRCULAR with respect to what is being tested. The arcs come from the coastline
    raycast (enrichment/swell_window.py), which never sees MOP, a buoy, or a face factor. So
    banding the MOP-vs-buoy residual by this is not banding it by a function of itself.

    The unit is a COUNT of integer bearings, 0..360, not an exact angular measure: both padded
    edges are inclusive, so each disjoint arc reads about 1 deg wide of its own span. With at
    most a handful of arcs per spot that is bounded by a few degrees against band widths of
    tens, and it is uniform across spots, so it cannot move a ranking.
    """
    from pipeline.interpret import bearing_in_arc
    if not arcs:
        return 0
    return sum(1 for d in range(360)
               if any(bearing_in_arc(float(d), a) for a in arcs))


def isotropic_ratio(w_deg):
    """H0_mop/H0_buoy predicted by window geometry ALONE, for a broad offshore sea.

    If the offshore directional spectrum were isotropic over the 180 deg seaward half-plane
    and MOP removed every direction outside the window completely, the surviving energy
    fraction would be w/180 and the height ratio sqrt(w/180).

    THIS IS A SCALE REFERENCE, NOT A PREDICTION, and the difference matters. Real swell is
    narrow in direction, so a window that CONTAINS the peak removes almost nothing and one
    that excludes it removes almost everything — the isotropic figure is neither an upper nor
    a lower bound in general. It is reported because it answers one specific question that
    the raw bias cannot: how much of the deficit could window geometry produce at all. At the
    population median window of 148 deg it is -0.093, against a measured -0.337.
    """
    if w_deg is None or w_deg < 0:
        return None
    return math.sqrt(min(float(w_deg), 360.0) / 180.0)


def exposure_bands(spots, n_bands=4):
    """Bias/RMS grouped by open-window quartile. *spots* is [{slug, window_deg, residuals}].

    Bands are POPULATION QUARTILES of window_deg, not a hand-drawn classification: the cuts
    are whatever the 104 spots turn out to be, and they are reported so the reader can see
    them. Returns (bands, spearman_of_per_spot_median_vs_window, cuts).
    """
    usable = [s for s in spots if s.get("window_deg") is not None and s.get("residuals")]
    if len(usable) < n_bands:
        return [], None, []
    widths = [float(s["window_deg"]) for s in usable]
    cuts = statistics.quantiles(widths, n=n_bands, method="inclusive")
    bands = []
    for i in range(n_bands):
        lo = -math.inf if i == 0 else cuts[i - 1]
        hi = math.inf if i == n_bands - 1 else cuts[i]
        members = [s for s in usable
                   if (lo < float(s["window_deg"]) <= hi or (i == 0 and float(s["window_deg"]) <= hi))]
        res = [r for s in members for r in s["residuals"]]
        st = band_stats(res)
        w = [float(s["window_deg"]) for s in members]
        bands.append({
            "band": i + 1,
            "window_lo": (min(w) if w else None), "window_hi": (max(w) if w else None),
            "n_spots": len(members), "stats": st,
            # The geometry-only expectation at this band's MEDIAN window, for scale.
            "isotropic_bias": ((isotropic_ratio(statistics.median(w)) - 1.0) if w else None),
        })
    per_spot_w = [float(s["window_deg"]) for s in usable]
    per_spot_med = [statistics.median(s["residuals"]) for s in usable]
    return bands, spearman(per_spot_w, per_spot_med), cuts


def obliquity_bands(pairs, edges=OBLIQUITY_BAND_EDGES):
    """Residual by |swell dir - shore normal|, POOLED and DEMEANED WITHIN SPOT.

    The demeaned half is the one that answers Q3. Pooling across spots confounds obliquity
    with exposure — the spots that see oblique swell are largely the sheltered ones — so a
    pooled trend is consistent with the hypothesis without being evidence for it. Subtracting
    each spot's own median residual removes the whole between-spot term, and what remains is
    the hour-to-hour effect at fixed geometry. That is the signature the brief calls hard to
    explain any other way, and it is the one that can actually carry that weight.

    Returns {bands: [...], pooled_slope, within_slope, n}.
    """
    usable = [p for p in pairs if p.get("obliquity_deg") is not None]
    if not usable:
        return {"bands": [], "pooled_slope": None, "within_slope": None, "n": 0}
    by_slug = {}
    for p in usable:
        by_slug.setdefault(p.get("slug"), []).append(p["residual"])
    med = {k: statistics.median(v) for k, v in by_slug.items()}
    dem = [p["residual"] - med[p.get("slug")] for p in usable]
    obl = [p["obliquity_deg"] for p in usable]
    bands = []
    for i, lo in enumerate(edges):
        hi = edges[i + 1] if i + 1 < len(edges) else math.inf
        idx = [k for k, o in enumerate(obl) if lo <= o < hi]
        bands.append({
            "lo": lo, "hi": (None if hi == math.inf else hi),
            "pooled": band_stats([usable[k]["residual"] for k in idx]),
            "within_spot": band_stats([dem[k] for k in idx]),
            "n_spots": len({usable[k].get("slug") for k in idx}),
        })
    return {"bands": bands,
            "pooled_slope": ols_slope(obl, [p["residual"] for p in usable]),
            "within_slope": ols_slope(obl, dem),
            "n": len(usable)}


def swell_only_comparison(pairs):
    """Total-Hs against swell-only Hs on the IDENTICAL subsample of joined pairs.

    ONE VARIABLE MOVES. Same hours, same Tp, same two depths, same deshoal — only which
    height column feeds it changes. Reporting the total-Hs numbers restricted to this same
    subsample, rather than against the whole-roster total, is what makes the delta
    attributable to wind sea instead of to a change in which hours were included; the swell
    columns are sparser than the total ones (not every NDBC station files a .spec) and an
    unrestricted comparison would confound the two.

    The two partitions are not defined identically — MOP's is an energy integral below
    0.10 Hz, NDBC's SwH is the station's own spectral separation — so this bounds the wind-sea
    contribution rather than measuring it exactly. That is enough to decide P4.
    """
    both = [p for p in pairs if p.get("residual_swell") is not None]
    if not both:
        return {"n": 0, "total": None, "swell": None, "corr_total": None,
                "corr_swell": None, "delta_bias": None, "coverage_frac": 0.0}
    tot = band_stats([p["residual"] for p in both])
    swl = band_stats([p["residual_swell"] for p in both])
    return {
        "n": len(both),
        "coverage_frac": len(both) / len(pairs) if pairs else 0.0,
        "total": tot, "swell": swl,
        "corr_total": pearson([p["h0_mop_m"] for p in both], [p["h0_buoy_m"] for p in both]),
        "corr_swell": pearson([p["h0_mop_swell_m"] for p in both],
                              [p["h0_buoy_swell_m"] for p in both]),
        "delta_bias": swl["bias_frac"] - tot["bias_frac"],
    }


def circ_delta(a, b):
    """Smallest absolute angle between two bearings, in [0, 180]."""
    if a is None or b is None:
        return None
    d = abs(float(a) - float(b)) % 360.0
    return min(d, 360.0 - d)


def build_pairs(mop_rows, buoy_rows, mop_depth_m, buoy_dep_m, shore_normal,
                tol_s=JOIN_TOLERANCE_S, hs_floor=HS_FLOOR_M, slug=None):
    """Joined, deshoaled pairs for one spot. Pure — no network, so the selftest drives it.

    Each pair carries TWO residuals. `residual` is run 1's, on the total heights, unchanged.
    `residual_swell` is the same arithmetic on the swell-only heights and is None whenever
    either side lacks one. They are built on the same joined hour with the same Tp and the
    same two depths, which is what lets P4 attribute any difference between them to wind sea
    and to nothing else. *slug* is carried so obliquity_bands can demean within spot.
    """
    pairs, blocked = [], 0
    for j in join_within(mop_rows, buoy_rows, tol_s):
        m, b = j["mop"], j["buoy"]
        if m["hs"] < hs_floor or b["hs"] < hs_floor:
            blocked += 1
            continue
        # ONE period drives both sides. MOP's Tp is the better-resolved of the two and using
        # the buoy's where present would make the two Ks values answer different questions.
        tp = m["tp"]
        h0_mop = deshoal(m["hs"], tp, mop_depth_m)
        h0_buoy = deshoal(b["hs"], tp, buoy_dep_m)
        if h0_mop is None or h0_buoy is None or h0_buoy <= 0:
            continue
        # THE SAME Tp DELIBERATELY. The swell partition's own peak period would be the more
        # physical choice in isolation, but it would move two variables at once and destroy
        # the discriminator: with Tp held fixed, the only thing separating residual_swell
        # from residual is the height. The floor applies to the swell heights too — a 0.1 m
        # swell partition is noise on both sides for the same reason a 0.1 m total is.
        ms, bs = m.get("swell_hs"), b.get("swell_hs")
        h0_ms = h0_bs = res_s = None
        if ms is not None and bs is not None and ms >= hs_floor and bs >= hs_floor:
            h0_ms = deshoal(ms, tp, mop_depth_m)
            h0_bs = deshoal(bs, tp, buoy_dep_m)
            if h0_ms is not None and h0_bs is not None and h0_bs > 0:
                res_s = h0_ms / h0_bs - 1.0
            else:
                h0_ms = h0_bs = None
        pairs.append({
            "t": j["t"], "dt_s": j["dt_s"], "tp_s": tp, "slug": slug,
            "mop_hs_m": m["hs"], "buoy_hs_m": b["hs"],
            "h0_mop_m": h0_mop, "h0_buoy_m": h0_buoy,
            "residual": h0_mop / h0_buoy - 1.0,
            "h0_mop_swell_m": h0_ms, "h0_buoy_swell_m": h0_bs,
            "residual_swell": res_s,
            "mop_depth_m": mop_depth_m,
            "obliquity_deg": circ_delta(m.get("dp"), shore_normal),
        })
    return pairs, blocked


# --------------------------------------------------------------------------- #
# Run                                                                          #
# --------------------------------------------------------------------------- #

def run(days_back=DEFAULT_DAYS_BACK, max_buoy_km=DEFAULT_MAX_BUOY_KM, out_path=OUT,
        limit=None, chunk_size=BUOY_CHUNK_IDS):
    if not os.path.exists(MOP_CACHE_PATH):
        print(f"no MOP point cache at {MOP_CACHE_PATH}\n"
              "  build it once (needs open CDIP THREDDS egress, ~11.7k points, resumable):\n"
              "      python3 scripts/mop_blacks_slice.py build-cache", file=sys.stderr)
        return 2
    cache = load_cache()
    if cache is None:
        return 2
    if not os.path.exists(SPOT_FACE_FACTORS_FILE):
        print(f"no factor file at {SPOT_FACE_FACTORS_FILE}", file=sys.stderr)
        return 2

    pop, dropped = corrected_population(max_buoy_km=max_buoy_km)
    print(f"population: {len(pop)} corrected spots with a buoy within {max_buoy_km:.0f} km "
          f"(of {dropped['n_factors']} corrected; {len(dropped['no_buoy'])} have no buoy, "
          f"{len(dropped['too_far'])} are further than {max_buoy_km:.0f} km)", flush=True)
    if limit:
        pop = pop[:limit]
        print(f"  --limit {limit}: using the first {len(pop)}")
    if not pop:
        print("nothing to validate", file=sys.stderr)
        return 2

    now = datetime.datetime.now(datetime.timezone.utc)
    t0 = now - datetime.timedelta(days=days_back)
    t0_e, t1_e = t0.timestamp(), now.timestamp()

    from pipeline.db_import import get_client
    client = get_client()
    buoy_ids = {s["nearest_buoy_id"] for s in pop}
    print(f"buoys: {len(buoy_ids)} distinct, fetching {t0.isoformat()} .. {now.isoformat()}",
          flush=True)
    n_chunks = len(chunk_ids(sorted(buoy_ids), chunk_size))
    print(f"  {chunk_size} buoy id(s) per statement, {n_chunks} chunks, ordered by "
          f"observed_at — see BUOY_CHUNK_IDS", flush=True)
    buoys = fetch_buoy_series(client, buoy_ids, t0.isoformat(), now.isoformat(),
                              chunk_size=chunk_size)
    print(f"  {sum(len(v) for v in buoys.values())} observations across "
          f"{len(buoys)} buoys", flush=True)
    depths = {b: buoy_depth_m(b) for b in buoy_ids}
    n_unknown = sum(1 for d in depths.values() if d is None)
    print(f"  buoy depth known for {len(depths) - n_unknown}/{len(depths)}; the rest are "
          f"treated as deep (Ks = 1) and counted", flush=True)

    all_pairs, by_spot, skipped = [], {}, []
    for i, s in enumerate(pop, 1):
        slug = _slug(s.get("name"))
        pid, meta, dist_m = _match(cache, s["lat"], s["lng"])
        if dist_m > MATCH_SANITY_M:
            skipped.append((slug, f"nearest MOP point {dist_m:.0f} m away"))
            continue
        depth = meta.get("water_depth")
        if depth is None:
            skipped.append((slug, f"MOP point {pid} has no metaWaterDepth"))
            continue
        try:
            mop_rows = fetch_mop_series(nowcast_url(meta.get("url")), t0_e, t1_e)
        except Exception as e:  # noqa: BLE001
            skipped.append((slug, f"MOP pull failed: {type(e).__name__}"))
            continue
        brows = buoys.get(s["nearest_buoy_id"]) or []
        pairs, blocked = build_pairs(mop_rows, brows, depth,
                                     depths.get(s["nearest_buoy_id"]),
                                     meta.get("shore_normal"), slug=slug)
        st = residual_stats(pairs)
        wdeg = window_deg(s.get("swell_window_arcs"))
        by_spot[slug] = {
            "mop_point": pid, "mop_depth_m": depth,
            "mop_match_m": round(dist_m, 1),
            "buoy_id": s["nearest_buoy_id"],
            "buoy_km": s.get("nearest_buoy_dist_km"),
            "buoy_depth_m": depths.get(s["nearest_buoy_id"]),
            "n_mop_rows": len(mop_rows), "n_buoy_rows": len(brows),
            # EXPOSURE, from the coastline raycast — see window_deg for why it is not
            # circular against the MOP/buoy residual it is used to band.
            "window_deg": wdeg,
            "n_arcs": len(s.get("swell_window_arcs") or []),
            "shore_normal": meta.get("shore_normal"),
            "blocked_small_hs": blocked, "stats": st,
        }
        all_pairs.extend(pairs)
        print(f"  [{i:3d}/{len(pop)}] {slug:<34} pt {pid} d={depth:.1f}m  "
              f"buoy {s['nearest_buoy_id']}  pairs={len(pairs):4d}", flush=True)

    roster_stats = residual_stats(all_pairs)
    passed, lines = verdict(roster_stats)
    # Grouped in ONE pass. The obvious comprehension rescans all_pairs per spot, which is
    # 104 x 33,620 on the real population.
    res_by_slug = {}
    for p in all_pairs:
        res_by_slug.setdefault(p.get("slug"), []).append(p["residual"])
    exp_spots = [{"slug": k, "window_deg": v["window_deg"],
                  "residuals": res_by_slug.get(k, [])}
                 for k, v in by_spot.items()]
    bands, exp_rho, cuts = exposure_bands(exp_spots)
    obl = obliquity_bands(all_pairs)
    swl = swell_only_comparison(all_pairs)
    h2_ok, h2_lines = hypothesis_verdict(bands, exp_rho, obl, swl)
    result = {
        "generated_at": now.isoformat(),
        "window": {"t0": t0.isoformat(), "t1": now.isoformat(), "days_back": days_back},
        "max_buoy_km": max_buoy_km,
        "buoy_chunk_ids": chunk_size,
        "method": "linear (Airy) shoaling; Ks = sqrt(0.5 / (n * tanh(kd))); H0 = H(d)/Ks",
        "prediction": {"rms_pct_max": PREDICT_RMS_PCT_MAX,
                       "corr_min": PREDICT_CORR_MIN,
                       "period_slope_max_per_s": PREDICT_PERIOD_SLOPE_MAX},
        "prediction_run2": {
            "exposure_widest_bias_max": PREDICT_EXPOSURE_WIDEST_BIAS_MAX,
            "exposure_spread_min": PREDICT_EXPOSURE_SPREAD_MIN,
            "exposure_spearman_min": PREDICT_EXPOSURE_SPEARMAN_MIN,
            "refute_exposure_spread_max": REFUTE_EXPOSURE_SPREAD_MAX,
            "obliquity_within_slope_max": PREDICT_OBLIQUITY_WITHIN_SLOPE_MAX,
            "refute_obliquity_within_slope_abs": REFUTE_OBLIQUITY_WITHIN_SLOPE_ABS,
            "swell_only_delta_max": PREDICT_SWELL_ONLY_DELTA_MAX,
            "refute_swell_only_delta_min": REFUTE_SWELL_ONLY_DELTA_MIN,
        },
        "roster": roster_stats, "by_spot": by_spot,
        "exposure": {"bands": bands, "spearman": exp_rho, "quartile_cuts": cuts},
        "obliquity": obl,
        "swell_only": swl,
        # Still buoy-dependent, so NOT an answer to "a test without a buoy" — but free, and
        # it probes the shoaling relation directly: if Ks were wrong, the residual would
        # depend on how much deshoaling each point needed.
        "residual_vs_mop_depth_slope_per_m": ols_slope(
            [p["mop_depth_m"] for p in all_pairs if p.get("mop_depth_m") is not None],
            [p["residual"] for p in all_pairs if p.get("mop_depth_m") is not None]),
        "skipped": skipped, "dropped": dropped,
        "buoy_depth_unknown": n_unknown,
        "passed": passed, "verdict_lines": lines,
        "filtering_hypothesis_supported": h2_ok, "hypothesis_lines": h2_lines,
    }
    _print_report(result)
    json.dump(result, open(out_path, "w"), indent=2, default=str)
    print(f"\nwrote {out_path}")
    return 0 if passed else 1


def _print_report(res):
    r = res["roster"]
    print()
    print("=" * 78)
    print("DESHOALING VALIDATION — MOP contour Hs deshoaled to deep water vs NDBC buoy Hs")
    print("=" * 78)
    if not r:
        print("  no joined pairs")
        return
    print(f"  pairs                 {r['n']}")
    print(f"  bias (H0mop/H0buoy-1) {r['bias_frac']:+.3f}   median {r['median_frac']:+.3f}")
    print(f"  RMS error             {r['rms_pct']:.1f}%")
    print(f"  correlation           {r['corr'] if r['corr'] is None else round(r['corr'], 3)}")
    print(f"  period slope          {r['period_slope_per_s']:+.5f} per second"
          if r["period_slope_per_s"] is not None else "  period slope          n/a")
    print("  obliquity slope       "
          + (f"{r['obliquity_slope_per_deg']:+.6f} per degree "
             f"({r['n_with_obliquity']} pairs with a shore normal)"
             if r["obliquity_slope_per_deg"] is not None else "n/a"))
    print(f"  H0 mop  {r['h0_mop']['median']:.2f} m median   "
          f"H0 buoy {r['h0_buoy']['median']:.2f} m median")
    print()
    print("-" * 78)
    print("REGISTERED PRE-SHIP PREDICTION vs MEASURED")
    print("-" * 78)
    p = res["prediction"]
    print(f"  predicted: RMS <= {p['rms_pct_max']:.0f}%, correlation >= {p['corr_min']:.2f}, "
          f"no systematic period dependence (|slope| <= {p['period_slope_max_per_s']}/s)")
    for line in res["verdict_lines"]:
        print(f"  {line}")
    print()
    if res["passed"]:
        print("  VERDICT: PREDICTION HELD. The deshoaling step is sound on this evidence.")
        print("  This does NOT clear the breaking step — see the module docstring. It says the")
        print("  wave mechanics between the contour and deep water are right, and nothing about")
        print("  the gamma closure or about whether a breaking height is the face a surfer reports.")
    else:
        print("  VERDICT: PREDICTION FAILED. THE TRANSFORM MUST NOT SHIP.")
        print("  The deshoaling step does not reproduce an independent measurement, so the same")
        print("  relation run forward cannot be trusted either. Fix or abandon the relation; do")
        print("  not widen the thresholds — they were registered before the run for that reason.")
    if res["buoy_depth_unknown"]:
        print(f"\n  CAVEAT: {res['buoy_depth_unknown']} buoy(s) had no known depth and were "
              "treated as deep water.\n  Their own shoaling is charged to the residual.")
    print()
    ranked = sorted(((v["stats"]["rms_pct"], k) for k, v in res["by_spot"].items()
                     if v["stats"]), reverse=True)
    print("WORST 10 SPOTS BY RMS")
    print(f"  {'spot':<34}{'pairs':>7}{'RMS%':>8}{'bias':>8}{'depth':>7}{'window':>8}  buoy")
    for rms, slug in ranked[:10]:
        v = res["by_spot"][slug]
        print(f"  {slug[:33]:<34}{v['stats']['n']:>7}{rms:>8.1f}"
              f"{v['stats']['bias_frac']:>+8.2f}{v['mop_depth_m']:>7.1f}"
              f"{v.get('window_deg', 0):>8}  {v['buoy_id']}")
    _print_hypothesis(res)


def _print_hypothesis(res):
    """The run-2 tests. Printed AFTER run 1's verdict so the original result stands first."""
    print()
    print("=" * 78)
    print("RUN 2 — WHY? TESTING THE DIRECTIONAL-FILTERING HYPOTHESIS")
    print("=" * 78)
    print("  The claim under test: MOP is a spectral refraction model, so its waveHs at a")
    print("  nearshore point has ALREADY had energy removed that never reaches that point.")
    print("  A buoy offshore measures the whole sea. If so we are not comparing a shoaled")
    print("  height against its own deep-water source, and deshoaling cannot recover energy")
    print("  MOP removed on purpose.")
    print()
    print("  REGISTERED BEFORE THE NUMBERS (constants at the top of this file):")
    print("    P2  bias must move toward zero as the window widens, monotonically;")
    print(f"        widest quartile |median bias| <= {PREDICT_EXPOSURE_WIDEST_BIAS_MAX}; "
          f"spread >= {PREDICT_EXPOSURE_SPREAD_MIN};")
    print(f"        Spearman >= {PREDICT_EXPOSURE_SPEARMAN_MIN}.  "
          f"REFUTED if the spread is < {REFUTE_EXPOSURE_SPREAD_MAX} (flat).")
    print(f"    P3  within-spot obliquity slope <= {PREDICT_OBLIQUITY_WITHIN_SLOPE_MAX}/deg.")
    print(f"        REFUTED if |slope| < {REFUTE_OBLIQUITY_WITHIN_SLOPE_ABS} — then run 1's")
    print( "        pooled slope was a between-spot artefact and means nothing.")
    print(f"    P4  swell-only rerun must move the bias by <= "
          f"{PREDICT_SWELL_ONLY_DELTA_MAX}.")
    print(f"        REFUTED if it moves >= {REFUTE_SWELL_ONLY_DELTA_MIN} toward zero — then")
    print( "        the deficit was wind sea in the buoy's .std height, not MOP filtering.")

    exp = res.get("exposure") or {}
    print()
    print("P2 — BIAS BY EXPOSURE (open swell window, union of raycast arcs)")
    cuts = exp.get("quartile_cuts") or []
    if cuts:
        print(f"  quartile cuts at {', '.join(f'{c:.0f}' for c in cuts)} deg "
              f"(data-derived, not chosen)")
    print(f"  {'band':<6}{'window deg':>12}{'spots':>7}{'pairs':>8}{'median':>9}{'bias':>8}"
          f"{'RMS%':>8}{'geom-only':>11}")
    for b in exp.get("bands") or []:
        s = b.get("stats")
        if not s:
            continue
        iso = b.get("isotropic_bias")
        rng = "%.0f-%.0f" % (b["window_lo"], b["window_hi"])
        iso_s = "n/a" if iso is None else "%+.3f" % iso
        print(f"  {b['band']:<6}{rng:>12}"
              f"{b['n_spots']:>7}{s['n']:>8}{s['median_frac']:>+9.3f}{s['bias_frac']:>+8.3f}"
              f"{s['rms_pct']:>8.1f}{iso_s:>11}")
    rho = exp.get("spearman")
    rho_s = "n/a" if rho is None else "%+.3f" % rho
    print(f"  Spearman(window_deg, per-spot median bias) = {rho_s}")
    print("  'geom-only' is the bias window geometry ALONE could produce for a BROAD sea:")
    print("  sqrt(w/180) - 1. Narrow real swell can exceed it either way — it is a scale")
    print("  reference for how much of the deficit geometry can account for, not a bound.")

    obl = res.get("obliquity") or {}
    print()
    print("P3 — BIAS BY OBLIQUITY |MOP Dp - shore normal|, PER HOUR")
    print(f"  {'band':<10}{'pairs':>8}{'spots':>7}{'pooled med':>12}{'within-spot med':>17}")
    for b in obl.get("bands") or []:
        p, w = b.get("pooled"), b.get("within_spot")
        if not p:
            continue
        lab = f"{b['lo']:.0f}-{b['hi']:.0f}" if b["hi"] is not None else f"{b['lo']:.0f}+"
        print(f"  {lab:<10}{p['n']:>8}{b['n_spots']:>7}{p['median_frac']:>+12.3f}"
              f"{w['median_frac']:>+17.3f}")
    ps, ws = obl.get("pooled_slope"), obl.get("within_slope")
    print(f"  pooled slope      {'n/a' if ps is None else f'{ps:+.6f}'} /deg")
    print(f"  within-spot slope {'n/a' if ws is None else f'{ws:+.6f}'} /deg   "
          f"<- THE TEST. The pooled column mixes obliquity with which spots are sheltered;")
    print("                                       the within-spot column removes each spot's "
          "own median first.")

    sw = res.get("swell_only") or {}
    print()
    print("P4 — COMPETING HYPOTHESIS: wind sea in the buoy's .std height")
    print("  buoy_observations.hs is NDBC .std, the DOMINANT (wind sea + swell) height")
    print("  (migration 004). Both sides also carry a swell-only height. Same hours, same Tp,")
    print("  same depths — only the height column changes.")
    if not sw.get("n"):
        print("  NO PAIRS carry a swell partition on both sides — P4 is untestable on this run.")
    else:
        t, s = sw["total"], sw["swell"]
        ct = "n/a" if sw["corr_total"] is None else "%.3f" % sw["corr_total"]
        cs = "n/a" if sw["corr_swell"] is None else "%.3f" % sw["corr_swell"]
        print(f"  subsample             {sw['n']} pairs "
              f"({100.0 * sw['coverage_frac']:.0f}% of the roster)")
        print(f"  {'':<22}{'bias':>9}{'median':>9}{'RMS%':>8}{'corr':>8}")
        print(f"  {'total Hs (run 1)':<22}{t['bias_frac']:>+9.3f}{t['median_frac']:>+9.3f}"
              f"{t['rms_pct']:>8.1f}{ct:>8}")
        print(f"  {'swell only':<22}{s['bias_frac']:>+9.3f}{s['median_frac']:>+9.3f}"
              f"{s['rms_pct']:>8.1f}{cs:>8}")
        print(f"  delta bias            {sw['delta_bias']:+.3f}")

    ds = res.get("residual_vs_mop_depth_slope_per_m")
    print()
    print("  DIAGNOSTIC (still buoy-dependent, so NOT a buoy-free test): residual vs MOP")
    print(f"  point depth = {'n/a' if ds is None else f'{ds:+.5f}'} /m. If Ks were wrong the")
    print( "  residual would track how much deshoaling each point needed.")

    print()
    for ln in res.get("hypothesis_lines") or []:
        print("  " + ln)
    print()
    if res.get("filtering_hypothesis_supported"):
        print("  HYPOTHESIS SUPPORTED. The deficit concentrates where MOP filters, varies with")
        print("  swell angle INSIDE a spot, and survives the swell-only rerun. Run 1's absolute")
        print("  bias and correlation were therefore never diagnostic of the shoaling relation:")
        print("  they measured the MOP-to-buoy transfer function, which is not what was on test.")
        print("  The PERIOD slope was the part of run 1 that could test Ks, and it passed.")
    else:
        print("  HYPOTHESIS NOT SUPPORTED on this evidence. Read the lines above: a REFUTED")
        print("  line means the deficit is something else and the transform is genuinely")
        print("  suspect; a FAIL line means this run could not separate the mechanisms.")


# --------------------------------------------------------------------------- #
# Selftest — offline                                                           #
# --------------------------------------------------------------------------- #

def run_selftest():
    ok = True

    def check(n, c):
        nonlocal ok
        ok = ok and c
        print(f"  {'PASS' if c else 'FAIL'}  {n}")

    def close(a, b, tol=1e-6):
        return a is not None and abs(a - b) <= tol

    # --- dispersion + shoaling, LITERALS ------------------------------------ #
    # kd solves x*tanh(x) = w^2*d/g with g = 9.80665. Every value below was computed by
    # Newton iteration AND by a 300-step bisection on the same equation; the two agreed to
    # <= 2.2e-16 at every case. Ks = sqrt(0.5 / (n * tanh(kd))), n = 0.5(1 + 2kd/sinh 2kd).
    #
    #   T=12.6 d=10   kd = 0.5258544183   Ks = 1.062516
    #   T=12.6 d=15   kd = 0.6586580982   Ks = 0.991858
    #   T= 8.0 d=10   kd = 0.8864112882   Ks = 0.932638
    #   T=18.0 d=15   kd = 0.4455878252   Ks = 1.128019
    #   T=16.0 d=10   kd = 0.4072493158   Ks = 1.168282
    #   T=16.0 d=15   kd = 0.5056141567   Ks = 1.077018
    #   T= 6.0 d=10   kd = 1.2983315898   Ks = 0.914209
    #   T=10.0 d=10   kd = 0.6803237213   Ks = 0.983497
    check("kd at T=12.6 d=10 (the SoCal contour at the measured mean period)",
          close(dispersion_kd(12.6, 10.0), 0.5258544183, 1e-9))
    check("kd at T=12.6 d=15 (the NorCal contour)",
          close(dispersion_kd(12.6, 15.0), 0.6586580982, 1e-9))
    check("kd at T=18.0 d=15", close(dispersion_kd(18.0, 15.0), 0.4455878252, 1e-9))
    check("kd at T=6.0 d=10", close(dispersion_kd(6.0, 10.0), 1.2983315898, 1e-9))

    check("Ks at T=12.6 d=10 is 1.062516", close(shoaling_coefficient(12.6, 10.0), 1.062516, 1e-6))
    check("Ks at T=12.6 d=15 is 0.991858", close(shoaling_coefficient(12.6, 15.0), 0.991858, 1e-6))
    check("Ks at T=8.0  d=10 is 0.932638", close(shoaling_coefficient(8.0, 10.0), 0.932638, 1e-6))
    check("Ks at T=18.0 d=15 is 1.128019", close(shoaling_coefficient(18.0, 15.0), 1.128019, 1e-6))
    check("Ks at T=16.0 d=10 is 1.168282", close(shoaling_coefficient(16.0, 10.0), 1.168282, 1e-6))
    check("Ks at T=16.0 d=15 is 1.077018", close(shoaling_coefficient(16.0, 15.0), 1.077018, 1e-6))
    check("Ks at T=10.0 d=10 is 0.983497", close(shoaling_coefficient(10.0, 10.0), 0.983497, 1e-6))
    check("Ks at T=6.0  d=10 is 0.914209", close(shoaling_coefficient(6.0, 10.0), 0.914209, 1e-6))

    # Ks crosses 1 between 10 s and 12.6 s at 10 m — the wave is still slowing there, not
    # yet piling up. A relation that were monotone in period would be wrong.
    check("Ks is BELOW 1 at 10 s and ABOVE 1 at 12.6 s on the 10 m contour",
          shoaling_coefficient(10.0, 10.0) < 1.0 < shoaling_coefficient(12.6, 10.0))

    check("deep water: Ks = 1 at 1000 m", shoaling_coefficient(12.0, 1000.0) == 1.0)
    check("at or beyond DEEP_WATER_M the coefficient short-circuits to exactly 1.0",
          shoaling_coefficient(12.0, DEEP_WATER_M) == 1.0)

    # THE DEPTH ERROR THE PER-POINT VALUE AVOIDS. Assuming 10 m on a 15 m point at 12.6 s
    # scales H0 by Ks(12.6,15)/Ks(12.6,10) = 0.991858 / 1.062516 = 0.933500, a 6.65% low
    # bias on the 55 spots north of Point Conception.
    check("a fixed 10 m depth mis-scales a 15 m point by 6.65% at 12.6 s",
          close(shoaling_coefficient(12.6, 15.0) / shoaling_coefficient(12.6, 10.0),
                0.933500, 1e-5))

    # --- deshoal ------------------------------------------------------------ #
    #   H = 1.0 m at d = 10 m, T = 12.6 s  ->  H0 = 1.0 / 1.062516 = 0.941163 m
    check("deshoal 1.0 m at 10 m / 12.6 s -> 0.941163 m",
          close(deshoal(1.0, 12.6, 10.0), 0.941163, 1e-6))
    #   H = 2.0 m at d = 15 m, T = 18.0 s  ->  H0 = 2.0 / 1.128019 = 1.773019 m
    check("deshoal 2.0 m at 15 m / 18.0 s -> 1.773019 m",
          close(deshoal(2.0, 18.0, 15.0), 1.773019, 1e-6))
    check("a None depth means deep water and passes the height through unchanged",
          deshoal(1.7, 12.6, None) == 1.7)
    check("a zero or negative depth is unusable", deshoal(1.0, 12.6, 0.0) is None
          and deshoal(1.0, 12.6, -5.0) is None)
    check("a zero or negative period is unusable", deshoal(1.0, 0.0, 10.0) is None)
    check("a None height is unusable", deshoal(None, 12.6, 10.0) is None)

    # --- the hour join ------------------------------------------------------- #
    mop = [{"t": 3600, "hs": 1.0, "tp": 12.0, "dp": 270.0},
           {"t": 7200, "hs": 1.1, "tp": 12.0, "dp": 270.0},
           {"t": 10800, "hs": 1.2, "tp": 12.0, "dp": 270.0}]
    # Buoy rows at 5100 and 9600. Gaps to the nearest buoy row, computed by hand:
    #   mop 3600  -> 5100 is 1500 s (25 min)   INSIDE  1800
    #   mop 7200  -> 5100 is 2100 s (35 min)   OUTSIDE 1800, and 9600 is 2400 s, further
    #   mop 10800 -> 9600 is 1200 s (20 min)   INSIDE  1800
    # so two of the three join at a 30-minute tolerance, and all three at 45 minutes.
    buoy = [{"t": 5100, "hs": 1.5, "tp": 12.0}, {"t": 9600, "hs": 1.6, "tp": 12.0}]
    j = join_within(mop, buoy, tol_s=1800)
    check("two of the three MOP rows join at a 30-minute tolerance", len(j) == 2)
    check("the joined pair keeps its gap for auditing", j[0]["dt_s"] == 1500)
    check("the joined pair is the right MOP row", j[0]["mop"]["t"] == 3600)
    check("the 35-minute gap does NOT join at a 30-minute tolerance",
          all(q["mop"]["t"] != 7200 for q in j))
    check("widening the tolerance to 45 minutes admits the 35-minute gap too",
          len(join_within(mop, buoy, tol_s=2700)) == 3)
    # NEAREST, not first: two candidates inside the window must pick the closer one.
    two = [{"t": 4200, "hs": 9.0, "tp": 12.0}, {"t": 3900, "hs": 7.0, "tp": 12.0}]
    check("the NEAREST buoy row wins when two are inside the window",
          join_within([mop[0]], two, tol_s=1800)[0]["buoy"]["hs"] == 7.0)
    check("an empty side joins to nothing",
          join_within([], buoy) == [] and join_within(mop, []) == [])

    # --- statistics ---------------------------------------------------------- #
    #   residuals -0.1, 0.0, +0.1 -> mean 0, RMS = sqrt((0.01+0+0.01)/3) = 0.0816497
    ps = [{"residual": -0.1, "h0_mop_m": 0.9, "h0_buoy_m": 1.0, "tp_s": 10.0, "obliquity_deg": 10.0},
          {"residual": 0.0, "h0_mop_m": 1.0, "h0_buoy_m": 1.0, "tp_s": 12.0, "obliquity_deg": 20.0},
          {"residual": 0.1, "h0_mop_m": 1.1, "h0_buoy_m": 1.0, "tp_s": 14.0, "obliquity_deg": 30.0}]
    st = residual_stats(ps)
    check("bias is the mean residual (0.0)", close(st["bias_frac"], 0.0))
    check("RMS% is 8.16497", close(st["rms_pct"], 8.164966, 1e-5))
    #   residual vs period: rise 0.2 over run 4 s -> slope 0.05 per second
    check("period slope is +0.05 per second", close(st["period_slope_per_s"], 0.05, 1e-9))
    #   residual vs obliquity: rise 0.2 over run 20 deg -> 0.01 per degree
    check("obliquity slope is +0.01 per degree",
          close(st["obliquity_slope_per_deg"], 0.01, 1e-9))
    check("pearson r of a perfectly increasing pair is 1.0",
          close(pearson([1.0, 2.0, 3.0], [2.0, 4.0, 6.0]), 1.0))
    check("pearson r of a perfectly decreasing pair is -1.0",
          close(pearson([1.0, 2.0, 3.0], [6.0, 4.0, 2.0]), -1.0))
    check("pearson r is None on a constant series", pearson([1.0, 1.0, 1.0], [1.0, 2.0, 3.0]) is None)
    check("ols_slope is None when x is constant", ols_slope([2.0, 2.0, 2.0], [1.0, 2.0, 3.0]) is None)
    check("residual_stats of nothing is None", residual_stats([]) is None)

    # --- the verdict --------------------------------------------------------- #
    good = {"n": 100, "rms_pct": 15.0, "corr": 0.85, "period_slope_per_s": 0.005,
            "obliquity_slope_per_deg": 0.0, "bias_frac": 0.0, "median_frac": 0.0,
            "n_with_obliquity": 100, "h0_mop": {}, "h0_buoy": {}}
    check("a run inside every threshold passes", verdict(good)[0] is True)
    check("RMS above 20% fails", verdict({**good, "rms_pct": 25.0})[0] is False)
    check("correlation below 0.70 fails", verdict({**good, "corr": 0.5})[0] is False)
    check("a period slope above the threshold with no obliquity term FAILS",
          verdict({**good, "period_slope_per_s": 0.05,
                   "obliquity_slope_per_deg": None})[0] is False)
    # 45 deg * 0.006/deg = 0.27 >= 5 s * 0.05/s = 0.25 -> refraction explains it
    check("a period slope above the threshold that obliquity explains PASSES with a note",
          verdict({**good, "period_slope_per_s": 0.05,
                   "obliquity_slope_per_deg": 0.006})[0] is True)
    check("a period slope obliquity does NOT explain fails",
          verdict({**good, "period_slope_per_s": 0.05,
                   "obliquity_slope_per_deg": 0.0001})[0] is False)
    check("an empty run fails rather than vacuously passing", verdict(None)[0] is False)

    # --- build_pairs, end to end, offline ------------------------------------ #
    #   MOP 1.0 m at 10 m / 12.6 s -> H0 = 0.941163; buoy 1.0 m treated as deep -> 1.0
    #   residual = 0.941163 - 1 = -0.058837
    p, blocked = build_pairs([{"t": 3600, "hs": 1.0, "tp": 12.6, "dp": 280.0}],
                             [{"t": 3900, "hs": 1.0, "tp": 12.0}],
                             10.0, None, 270.0)
    check("build_pairs deshoals the MOP side only when the buoy depth is unknown",
          len(p) == 1 and close(p[0]["residual"], -0.058837, 1e-6))
    check("obliquity is |MOP dp - point shore normal|", close(p[0]["obliquity_deg"], 10.0))
    check("the small-Hs floor blocks and COUNTS rather than silently dropping",
          build_pairs([{"t": 3600, "hs": 0.1, "tp": 12.6, "dp": 280.0}],
                      [{"t": 3900, "hs": 1.0, "tp": 12.0}], 10.0, None, 270.0)[1] == 1)

    # --- RUN 2: exposure, obliquity, swell-only ------------------------------- #
    # window_deg uses interpret.bearing_in_arc, so these literals were cross-checked by hand
    # from the pad formula pad = (span - ((max-min) mod 360))/2 before being written down.
    # {min 189, max 251, span 66}: pad 2, sector [187, 253], 67 integer bearings.
    # {min 285, max 327, span 46}: pad 2, sector [283, 329], 47. Disjoint -> 114.
    a38 = [{"min": 189, "max": 251, "span": 66}, {"min": 285, "max": 327, "span": 46}]
    check("window_deg of 38th Avenue's two arcs is 114", window_deg(a38) == 114)
    check("window_deg of one arc alone is 67", window_deg(a38[:1]) == 67)
    check("no arcs is a zero window, not a full one", window_deg([]) == 0)
    check("window_deg of None is 0", window_deg(None) == 0)
    # A 0/360 wrap needs no special case: {340, 20, span 44} -> pad 2 -> [338, 22] -> 45.
    check("a wrapping arc is measured across 0/360",
          window_deg([{"min": 340, "max": 20, "span": 44}]) == 45)
    # OVERLAP MUST COUNT ONCE. [98,202] u [148,252] = [98,252] = 155, not 104+104.
    check("overlapping arcs count their union, not their sum",
          window_deg([{"min": 100, "max": 200, "span": 104},
                      {"min": 150, "max": 250, "span": 104}]) == 155)
    check("a fully open window saturates at 360",
          window_deg([{"min": 0, "max": 359, "span": 360}]) == 360)

    # sqrt(w/180) - 1, to 6 dp: w=45 -> -0.5, w=90 -> -0.292893, w=180 -> 0.0,
    # w=148 (the population median) -> -0.093235, which is the number that matters.
    check("isotropic_ratio at a 45 deg window is 0.5", close(isotropic_ratio(45), 0.5))
    check("isotropic_ratio at 90 deg is 0.707107", close(isotropic_ratio(90), 0.707107, 1e-6))
    check("a full half-plane window predicts no deficit", close(isotropic_ratio(180), 1.0))
    check("at the population median window of 148 deg, geometry alone gives -0.0932",
          close(isotropic_ratio(148) - 1.0, -0.093235, 1e-6))
    check("isotropic_ratio of None is None", isotropic_ratio(None) is None)

    # Ranks with ties AVERAGED, not competition-ranked: [10,20,20,40] -> [1, 2.5, 2.5, 4].
    check("tied ranks are averaged", _rank([10, 20, 20, 40]) == [1.0, 2.5, 2.5, 4.0])
    check("spearman of a monotone pair is 1.0",
          close(spearman([1, 2, 3, 4], [10, 20, 30, 40]), 1.0))
    check("spearman is 1.0 for a NON-linear monotone pair, where pearson is not",
          close(spearman([1, 2, 3, 4], [1, 4, 9, 1000]), 1.0))
    check("spearman of a reversed pair is -1.0",
          close(spearman([1, 2, 3, 4], [40, 30, 20, 10]), -1.0))
    check("spearman of too few points is None", spearman([1, 2], [1, 2]) is None)

    check("band_stats of nothing is None", band_stats([]) is None)
    check("band_stats ignores None entries", band_stats([None, 0.0, None])["n"] == 1)
    # mean 0, median 0, RMS = sqrt((0.09+0+0.09)/3) = 0.244949 -> 24.4949%
    bs = band_stats([-0.3, 0.0, 0.3])
    check("band_stats mean/median/RMS on a symmetric triple",
          bs["n"] == 3 and close(bs["bias_frac"], 0.0) and close(bs["median_frac"], 0.0)
          and close(bs["rms_pct"], 24.494897, 1e-5))

    # Quartile cuts of the 8 widths below are 95 / 130 / 165 under the inclusive method.
    ex_spots = [{"slug": f"s{i}", "window_deg": w, "residuals": [r]}
                for i, (w, r) in enumerate([(60.0, -0.50), (80.0, -0.40), (100.0, -0.30),
                                            (120.0, -0.20), (140.0, -0.15), (160.0, -0.10),
                                            (180.0, -0.05), (200.0, 0.00)])]
    eb, rho, cuts = exposure_bands(ex_spots)
    check("exposure quartile cuts are data-derived, not chosen",
          [round(c, 1) for c in cuts] == [95.0, 130.0, 165.0])
    check("four bands, two spots each on this fixture",
          [b["n_spots"] for b in eb] == [2, 2, 2, 2])
    check("band 1 is the narrowest windows", (eb[0]["window_lo"], eb[0]["window_hi"]) == (60.0, 80.0))
    check("band 4 is the widest windows", (eb[3]["window_lo"], eb[3]["window_hi"]) == (180.0, 200.0))
    # Band 1 medians -0.50 and -0.40 -> -0.45; band 4 -0.05 and 0.00 -> -0.025.
    check("band medians read the right residuals",
          close(eb[0]["stats"]["median_frac"], -0.45)
          and close(eb[3]["stats"]["median_frac"], -0.025))
    check("a perfectly monotone exposure trend gives Spearman 1.0", close(rho, 1.0))
    # THE REFUTATION CASE MUST ALSO WORK: a flat bias must NOT produce a trend.
    flat = [{"slug": f"f{i}", "window_deg": w, "residuals": [-0.33]}
            for i, w in enumerate([60.0, 80.0, 100.0, 120.0, 140.0, 160.0, 180.0, 200.0])]
    fb, frho, _ = exposure_bands(flat)
    check("a bias that is flat across exposure yields zero spread",
          close(fb[3]["stats"]["median_frac"] - fb[0]["stats"]["median_frac"], 0.0))
    check("a flat bias has an undefined rank correlation, not a spurious one", frho is None)

    # OBLIQUITY, and the point of the within-spot column. Two spots with DIFFERENT levels
    # (-0.5 and -0.1) and NO within-spot obliquity effect at all. Pooled, the bias looks like
    # it tracks obliquity, purely because the sheltered spot sees the oblique hours. Demeaned,
    # it must vanish — that is the whole reason the demeaned column exists.
    fake = ([{"slug": "sheltered", "residual": -0.5, "obliquity_deg": o} for o in (50.0, 70.0)]
            + [{"slug": "open", "residual": -0.1, "obliquity_deg": o} for o in (5.0, 20.0)])
    ob = obliquity_bands(fake)
    check("pooled obliquity slope is NEGATIVE on a purely between-spot fixture",
          ob["pooled_slope"] is not None and ob["pooled_slope"] < -0.001)
    check("the within-spot slope is EXACTLY zero there — the artefact is removed",
          close(ob["within_slope"], 0.0, 1e-12))
    check("obliquity bands land in the right buckets",
          [b["pooled"]["n"] if b["pooled"] else 0 for b in ob["bands"]] == [1, 1, 0, 1, 1, 0])
    # And a REAL within-spot effect must survive demeaning: one spot, residual falling with
    # obliquity. Slope over (0,-0.10) (30,-0.13) (60,-0.16) (90,-0.19) is exactly -0.001/deg.
    real = [{"slug": "one", "residual": r, "obliquity_deg": o}
            for o, r in ((0.0, -0.10), (30.0, -0.13), (60.0, -0.16), (90.0, -0.19))]
    rb = obliquity_bands(real)
    check("a genuine within-spot obliquity trend survives demeaning",
          close(rb["within_slope"], -0.001, 1e-12))
    check("obliquity_bands on nothing reports zero rather than raising",
          obliquity_bands([])["n"] == 0)

    # SWELL-ONLY: the like-for-like restriction is the load-bearing part. Pair 3 has no
    # swell residual, so it must be excluded from BOTH columns — including the total one.
    sp = [{"residual": -0.30, "residual_swell": -0.05, "h0_mop_m": 1.0, "h0_buoy_m": 1.4,
           "h0_mop_swell_m": 1.0, "h0_buoy_swell_m": 1.05},
          {"residual": -0.40, "residual_swell": -0.15, "h0_mop_m": 2.0, "h0_buoy_m": 3.3,
           "h0_mop_swell_m": 2.0, "h0_buoy_swell_m": 2.35},
          {"residual": 0.90, "residual_swell": None, "h0_mop_m": 9.0, "h0_buoy_m": 4.7,
           "h0_mop_swell_m": None, "h0_buoy_swell_m": None}]
    sc = swell_only_comparison(sp)
    check("swell-only uses only the pairs that carry BOTH partitions", sc["n"] == 2)
    check("the total column is restricted to that SAME subsample, not the whole roster",
          close(sc["total"]["bias_frac"], -0.35))
    check("the swell column is the swell residuals", close(sc["swell"]["bias_frac"], -0.10))
    check("delta bias is swell minus total", close(sc["delta_bias"], 0.25))
    check("coverage is reported, not assumed", close(sc["coverage_frac"], 2.0 / 3.0))
    check("swell-only on pairs with no partitions reports n=0 rather than raising",
          swell_only_comparison([{"residual": -0.3, "residual_swell": None}])["n"] == 0)

    # build_pairs must now carry BOTH residuals off the SAME joined hour.
    bp, _ = build_pairs([{"t": 3600, "hs": 1.0, "tp": 12.6, "dp": 280.0, "swell_hs": 0.8}],
                        [{"t": 3900, "hs": 1.0, "tp": 12.0, "swell_hs": 0.8}],
                        10.0, None, 270.0, slug="x")
    check("build_pairs carries the spot slug for within-spot demeaning", bp[0]["slug"] == "x")
    # H0 = 1.0/Ks(12.6,10) = 1/1.062516 = 0.941163 -> residual -0.058837. The swell pair is
    # 0.8/1.062516 = 0.752930 against 0.8 -> the SAME -0.058837, because the ratio is what
    # is measured and both sides were scaled alike.
    check("the swell residual is computed on the same hour with the same Tp",
          close(bp[0]["residual_swell"], -0.058837, 1e-6))
    check("MOP point depth is carried for the depth diagnostic", bp[0]["mop_depth_m"] == 10.0)
    # A missing partition on EITHER side must yield None, never a silent fallback to total.
    bp2, _ = build_pairs([{"t": 3600, "hs": 1.0, "tp": 12.6, "dp": 280.0, "swell_hs": None}],
                         [{"t": 3900, "hs": 1.0, "tp": 12.0, "swell_hs": 0.8}],
                         10.0, None, 270.0)
    check("a missing MOP partition gives residual_swell None, not a fallback to total Hs",
          bp2[0]["residual_swell"] is None and bp2[0]["residual"] is not None)
    # The small-Hs floor applies to the partitions too: 0.1 m of swell is noise either side.
    bp3, _ = build_pairs([{"t": 3600, "hs": 1.0, "tp": 12.6, "dp": 280.0, "swell_hs": 0.1}],
                         [{"t": 3900, "hs": 1.0, "tp": 12.0, "swell_hs": 0.8}],
                         10.0, None, 270.0)
    check("the Hs floor blocks a noise-level swell partition too",
          bp3[0]["residual_swell"] is None)

    # --- chunking: the split itself ------------------------------------------ #
    # chunk_ids is mop_face_validation's and is pinned there too; these cases are the ones
    # THIS caller depends on, at THIS chunk size, over TEXT ids. Expected chunks are written
    # out, never produced by calling the function.
    b23 = [f"460{i:02d}" for i in range(23)]        # 23 ids, the real population size
    check("23 buoy ids at size 1 -> 23 chunks (one statement per buoy)",
          len(chunk_ids(b23, 1)) == 23)
    check("every chunk at size 1 holds exactly one id",
          {len(c) for c in chunk_ids(b23, 1)} == {1})
    check("23 ids at size 8 would be 3 chunks (8+8+7), not the 23 the fix uses",
          [len(c) for c in chunk_ids(b23, 8)] == [8, 8, 7])
    check("a list longer than the chunk size splits, order preserved",
          chunk_ids(["a", "b", "c", "d", "e"], 2) == [["a", "b"], ["c", "d"], ["e"]])
    # Every id exactly once, over text ids at the shipped size.
    flat1 = [i for c in chunk_ids(b23, 1) for i in c]
    check("chunking loses no buoy id", len(flat1) == 23)
    check("chunking duplicates no buoy id", len(set(flat1)) == 23)
    check("chunking preserves order", flat1 == b23)
    check("no buoys -> no chunks at all (never an empty IN list)", chunk_ids([], 1) == [])

    # --- the buoy fetch, against a fake client -------------------------------- #
    # WHAT THIS PROVES: the chunking, the reassembly, and the QUERY SHAPE — that the fetch
    # asks by buoy_id with .eq and orders by observed_at, which is the entire fix. WHAT IT
    # CANNOT PROVE: that the resulting plan is fast. Only Postgres can say that, and the
    # 57014 this replaces was a plan failure, not a logic one.
    class _Resp:
        def __init__(self, data):
            self.data = data

    class _FakeQuery:
        def __init__(self, rows, log):
            self._rows, self._log = rows, log
            self._ids, self._order, self._frm, self._to = None, None, 0, 0

        def select(self, *a, **k):
            return self

        def gte(self, *a, **k):
            return self

        def lte(self, *a, **k):
            return self

        def eq(self, _col, val):
            self._log["eq"] += 1
            self._ids = {val}
            return self

        def in_(self, _col, vals):
            self._log["in_"] += 1
            self._ids = set(vals)
            return self

        def order(self, col, **k):
            self._log["order_cols"].add(col)
            self._order = col
            return self

        def range(self, frm, to):
            self._frm, self._to = frm, to
            return self

        def execute(self):
            sel = sorted((r for r in self._rows
                          if self._ids is None or r["buoy_id"] in self._ids),
                         key=lambda r: r[self._order])
            return _Resp(sel[self._frm:self._to + 1])

    class _FakeClient:
        def __init__(self, rows):
            self._rows, self.statements = rows, 0
            self.log = {"eq": 0, "in_": 0, "order_cols": set()}

        def table(self, _name):
            self.statements += 1
            return _FakeQuery(self._rows, self.log)

    # Three buoys. `id` runs ANTI-correlated with observed_at inside 46042 (id 90 is 01:30,
    # id 92 is 00:00) so that ordering by id and ordering by observed_at give DIFFERENT
    # answers — a revert to .order("id") fails the expected literal below rather than
    # passing quietly. Epochs are written out; they are the exact .timestamp() of each ISO
    # string, not a value read back from the code under test.
    #   2026-08-01T00:00Z 1785542400   00:30Z 1785544200   01:00Z 1785546000
    #                     01:30Z 1785547800   02:00Z 1785549600
    brows = [
        {"id": 90, "buoy_id": "46042", "observed_at": "2026-08-01T01:30:00+00:00",
         "hs": 1.5, "tp": 12.0, "swell_hs": 1.2},
        {"id": 91, "buoy_id": "46053", "observed_at": "2026-08-01T00:30:00+00:00",
         "hs": 2.0, "tp": 14.0, "swell_hs": 1.8},
        {"id": 92, "buoy_id": "46042", "observed_at": "2026-08-01T00:00:00+00:00",
         "hs": 1.0, "tp": 11.0, "swell_hs": None},     # NULL .spec: row KEPT, unlike NULL hs
        {"id": 93, "buoy_id": "46086", "observed_at": "2026-08-01T02:00:00+00:00",
         "hs": 3.0, "tp": None, "swell_hs": 2.5},      # NULL tp survives, as None
        {"id": 94, "buoy_id": "46053", "observed_at": "2026-08-01T01:00:00+00:00",
         "hs": None, "tp": 13.0, "swell_hs": 1.0},     # NULL hs is dropped
        {"id": 95, "buoy_id": "46053", "observed_at": "2026-08-01T01:30:00+00:00",
         "hs": 2.5, "tp": 15.0, "swell_hs": 2.0},
    ]
    b_expect = {
        "46042": [{"t": 1785542400.0, "hs": 1.0, "tp": 11.0, "swell_hs": None},
                  {"t": 1785547800.0, "hs": 1.5, "tp": 12.0, "swell_hs": 1.2}],
        "46053": [{"t": 1785544200.0, "hs": 2.0, "tp": 14.0, "swell_hs": 1.8},
                  {"t": 1785547800.0, "hs": 2.5, "tp": 15.0, "swell_hs": 2.0}],
        "46086": [{"t": 1785549600.0, "hs": 3.0, "tp": None, "swell_hs": 2.5}],
    }
    bids = ["46042", "46053", "46086"]
    fc1 = _FakeClient(brows)
    got_1 = fetch_buoy_series(fc1, bids, "t0", "t1", chunk_size=1, page=1000)
    got_2 = fetch_buoy_series(_FakeClient(brows), bids, "t0", "t1", chunk_size=2, page=1000)
    got_all = fetch_buoy_series(_FakeClient(brows), bids, "t0", "t1", chunk_size=99, page=1000)
    check("the shipped chunk size 1 reassembles to the expected dict", got_1 == b_expect)
    check("chunk size 2 reassembles to the expected dict", got_2 == b_expect)
    check("one chunk (un-chunked) reassembles to the expected dict", got_all == b_expect)
    check("every chunk size agrees with every other", got_1 == got_2 == got_all)
    check("each buoy's rows come back in observed_at order",
          [r["t"] for r in got_1["46042"]] == [1785542400.0, 1785547800.0])
    check("a NULL hs row is dropped, not carried as None",
          len(got_1["46053"]) == 2)
    check("a NULL tp row is KEPT with tp None", got_1["46086"][0]["tp"] is None)
    # The two NULLs are treated DIFFERENTLY on purpose: hs is what the comparison needs, so
    # its absence drops the row; swell_hs only feeds P4, so its absence must not cost the
    # total-Hs comparison an hour it could otherwise have used.
    check("a NULL swell_hs KEEPS its row — P4's sparseness must not shrink the run 1 sample",
          got_1["46042"][0]["swell_hs"] is None and got_1["46042"][0]["hs"] == 1.0)
    check("the swell partition is carried through when present",
          got_1["46053"][1]["swell_hs"] == 2.0)
    check("a buoy absent from the data is absent from the result, not empty",
          fetch_buoy_series(_FakeClient(brows), ["46042", "99999"], "t0", "t1",
                            chunk_size=1, page=1000).keys() == {"46042"})

    # THE FIX ITSELF. At the shipped size the fetch must ask with .eq and order by
    # observed_at — never by id, which is what timed out.
    check("the shipped path uses .eq per buoy, never an IN list",
          fc1.log["eq"] == 3 and fc1.log["in_"] == 0)
    check("the shipped path orders by observed_at and by nothing else",
          fc1.log["order_cols"] == {"observed_at"})
    check("one statement per buoy at the shipped size", fc1.statements == 3)

    # PROGRESS IS A REQUIREMENT, SO IT IS PINNED. Without this, deleting the print leaves
    # every other test green and hands back the silent fetch the fix exists to end.
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        fetch_buoy_series(_FakeClient(brows), bids, "t0", "t1", chunk_size=1, page=1000)
    lines = [ln for ln in buf.getvalue().splitlines() if ln.strip()]
    check("one progress line per chunk, printed as it goes", len(lines) == 3)
    check("each progress line names its buoy and its position",
          all(f"chunk {i:3d}/3" in ln and b in ln
              for i, (ln, b) in enumerate(zip(lines, bids), 1)))

    # Paging INSIDE a chunk still applies: 5 rows for one buoy at page=2 is 3 statements.
    solo = [{"id": 500 + i, "buoy_id": "46042",
             "observed_at": f"2026-08-01T0{i}:00:00+00:00", "hs": float(i), "tp": 10.0}
            for i in range(1, 6)]
    fcp = _FakeClient(solo)
    paged = fetch_buoy_series(fcp, ["46042"], "t0", "t1", chunk_size=1, page=2)
    check("paging inside a chunk returns every row", len(paged["46042"]) == 5)
    check("paged rows stay in observed_at order",
          [r["hs"] for r in paged["46042"]] == [1.0, 2.0, 3.0, 4.0, 5.0])
    # 5 rows at 2/page: pages of 2, 2, 1 — the short third page ends the loop.
    check(f"5 rows at page 2 took 3 statements ({fcp.statements})", fcp.statements == 3)
    # An exact multiple needs the extra empty page to learn it is done: 4 rows -> 2, 2, 0.
    fcp4 = _FakeClient(solo[:4])
    fetch_buoy_series(fcp4, ["46042"], "t0", "t1", chunk_size=1, page=2)
    check(f"4 rows at page 2 took 3 statements, the last empty ({fcp4.statements})",
          fcp4.statements == 3)

    # The two sizes are deliberately DIFFERENT, for reasons in BUOY_CHUNK_IDS. Pinned so
    # that "harmonising" them to one number fails here and reads why.
    check(f"the buoy chunk size is 1, not the forecasts 8 "
          f"({BUOY_CHUNK_IDS} vs {FORECAST_CHUNK_SPOTS})",
          BUOY_CHUNK_IDS == 1 and FORECAST_CHUNK_SPOTS == 8)
    check(f"the buoy page size is PostgREST's cap ({BUOY_PAGE_ROWS})",
          BUOY_PAGE_ROWS == 1000)

    print()
    print("  NOT TESTED, AND CANNOT BE WITHOUT NETWORK: fetch_mop_series (CDIP THREDDS over")
    print("  OPeNDAP, 403 in this container) and the DATABASE half of fetch_buoy_series.")
    print("  The fake client above pins its chunking, its reassembly and its query SHAPE —")
    print("  .eq per buoy, ordered by observed_at — because those are what the 57014 fix")
    print("  changed. It cannot pin that the resulting PLAN is fast: only Postgres can say")
    print("  that, and the timeout being replaced was a plan failure, not a logic one.")
    print()
    print("selftest: " + ("ALL PASS" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days-back", type=int, default=DEFAULT_DAYS_BACK,
                    help=f"window length in days (default {DEFAULT_DAYS_BACK})")
    ap.add_argument("--max-buoy-km", type=float, default=DEFAULT_MAX_BUOY_KM,
                    help=f"drop spots whose nearest buoy is further (default {DEFAULT_MAX_BUOY_KM})")
    ap.add_argument("--limit", type=int, default=None, help="only the first N spots (smoke test)")
    ap.add_argument("--out", default=OUT, help=f"results JSON (default {OUT})")
    ap.add_argument("--chunk-size", type=int, default=BUOY_CHUNK_IDS,
                    help=f"buoy ids per statement (default {BUOY_CHUNK_IDS}). A PROBE, not a "
                         f"setting: above 1, observed_at is no longer unique within the "
                         f"statement and offset paging stops being deterministic. See "
                         f"BUOY_CHUNK_IDS")
    ap.add_argument("--selftest", action="store_true", help="offline logic proof; no network, no DB")
    a = ap.parse_args(argv)
    if a.selftest:
        return run_selftest()
    return run(days_back=a.days_back, max_buoy_km=a.max_buoy_km, out_path=a.out, limit=a.limit,
               chunk_size=a.chunk_size)


if __name__ == "__main__":
    raise SystemExit(main())
