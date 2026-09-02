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
import datetime
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
    """[{t, hs, tp, dp}] for one MOP point.

    NOT mop_face_validation.fetch_mop_by_hour: that deliberately discards Tp and Dp
    ("carrying MOP's period would invite someone to feed it into face_ft and re-create the
    circularity"). This study NEEDS both — Tp drives the dispersion relation and Dp drives
    the obliquity discriminator — so it reads the same pull_mop_window and keeps them.
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
                    "dp": (float(r["dp"]) if r.get("dp") is not None else None)})
    return out


def fetch_buoy_series(client, buoy_ids, t0_iso, t1_iso, page=1000):
    """{buoy_id: [{t, hs, tp}]} from buoy_observations, paginated and chunked.

    Chunked at FORECAST_CHUNK_SPOTS ids per statement for the same reason the forecasts
    fetch is: anything larger raised PostgREST 57014 statement timeouts during the MOP
    validation. `order("id")` makes offset paging a total order.
    """
    out = {}
    for chunk in chunk_ids(sorted(set(buoy_ids)), FORECAST_CHUNK_SPOTS):
        frm = 0
        while True:
            resp = (client.table("buoy_observations")
                    .select("buoy_id, observed_at, hs, tp")
                    .in_("buoy_id", list(chunk))
                    .gte("observed_at", t0_iso).lte("observed_at", t1_iso)
                    .order("id").range(frm, frm + page - 1).execute())
            rows = resp.data or []
            if not rows:
                break
            for r in rows:
                t = _norm_epoch(_iso_to_epoch(r.get("observed_at")))
                if t is None or r.get("hs") is None:
                    continue
                out.setdefault(r["buoy_id"], []).append(
                    {"t": t, "hs": float(r["hs"]),
                     "tp": (float(r["tp"]) if r.get("tp") is not None else None)})
            if len(rows) < page:
                break
            frm += page
    return out


def buoy_depth_m(buoy_id):
    """Mooring depth in metres, or None. Known table first, NDBC station page on egress."""
    try:
        from pipeline.forecast.nwps_nearshore import _ndbc_station_meta
        return (_ndbc_station_meta(buoy_id) or {}).get("depth_m")
    except Exception:  # noqa: BLE001 - offline or import failure -> unknown, counted
        return None


def circ_delta(a, b):
    """Smallest absolute angle between two bearings, in [0, 180]."""
    if a is None or b is None:
        return None
    d = abs(float(a) - float(b)) % 360.0
    return min(d, 360.0 - d)


def build_pairs(mop_rows, buoy_rows, mop_depth_m, buoy_dep_m, shore_normal,
                tol_s=JOIN_TOLERANCE_S, hs_floor=HS_FLOOR_M):
    """Joined, deshoaled pairs for one spot. Pure — no network, so the selftest drives it."""
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
        pairs.append({
            "t": j["t"], "dt_s": j["dt_s"], "tp_s": tp,
            "mop_hs_m": m["hs"], "buoy_hs_m": b["hs"],
            "h0_mop_m": h0_mop, "h0_buoy_m": h0_buoy,
            "residual": h0_mop / h0_buoy - 1.0,
            "obliquity_deg": circ_delta(m.get("dp"), shore_normal),
        })
    return pairs, blocked


# --------------------------------------------------------------------------- #
# Run                                                                          #
# --------------------------------------------------------------------------- #

def run(days_back=DEFAULT_DAYS_BACK, max_buoy_km=DEFAULT_MAX_BUOY_KM, out_path=OUT,
        limit=None):
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
    buoys = fetch_buoy_series(client, buoy_ids, t0.isoformat(), now.isoformat())
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
                                     meta.get("shore_normal"))
        st = residual_stats(pairs)
        by_spot[slug] = {
            "mop_point": pid, "mop_depth_m": depth,
            "mop_match_m": round(dist_m, 1),
            "buoy_id": s["nearest_buoy_id"],
            "buoy_km": s.get("nearest_buoy_dist_km"),
            "buoy_depth_m": depths.get(s["nearest_buoy_id"]),
            "n_mop_rows": len(mop_rows), "n_buoy_rows": len(brows),
            "blocked_small_hs": blocked, "stats": st,
        }
        all_pairs.extend(pairs)
        print(f"  [{i:3d}/{len(pop)}] {slug:<34} pt {pid} d={depth:.1f}m  "
              f"buoy {s['nearest_buoy_id']}  pairs={len(pairs):4d}", flush=True)

    roster_stats = residual_stats(all_pairs)
    passed, lines = verdict(roster_stats)
    result = {
        "generated_at": now.isoformat(),
        "window": {"t0": t0.isoformat(), "t1": now.isoformat(), "days_back": days_back},
        "max_buoy_km": max_buoy_km,
        "method": "linear (Airy) shoaling; Ks = sqrt(0.5 / (n * tanh(kd))); H0 = H(d)/Ks",
        "prediction": {"rms_pct_max": PREDICT_RMS_PCT_MAX,
                       "corr_min": PREDICT_CORR_MIN,
                       "period_slope_max_per_s": PREDICT_PERIOD_SLOPE_MAX},
        "roster": roster_stats, "by_spot": by_spot,
        "skipped": skipped, "dropped": dropped,
        "buoy_depth_unknown": n_unknown,
        "passed": passed, "verdict_lines": lines,
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
    print(f"  obliquity slope       "
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
    print(f"  {'spot':<34}{'pairs':>7}{'RMS%':>8}{'bias':>8}{'depth':>7}  buoy")
    for rms, slug in ranked[:10]:
        v = res["by_spot"][slug]
        print(f"  {slug[:33]:<34}{v['stats']['n']:>7}{rms:>8.1f}"
              f"{v['stats']['bias_frac']:>+8.2f}{v['mop_depth_m']:>7.1f}  {v['buoy_id']}")


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

    print()
    print("  NOT TESTED, AND CANNOT BE WITHOUT NETWORK: fetch_mop_series (CDIP THREDDS over")
    print("  OPeNDAP, 403 in this container) and fetch_buoy_series (Supabase). Mocking either")
    print("  would prove only that the mock returns what it was told to. Their inputs and")
    print("  outputs are exercised through build_pairs / join_within, which are pure.")
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
    ap.add_argument("--selftest", action="store_true", help="offline logic proof; no network, no DB")
    a = ap.parse_args(argv)
    if a.selftest:
        return run_selftest()
    return run(days_back=a.days_back, max_buoy_km=a.max_buoy_km, out_path=a.out, limit=a.limit)


if __name__ == "__main__":
    raise SystemExit(main())
