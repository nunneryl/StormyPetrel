#!/usr/bin/env python3
"""MOP handful slice — ~5 CA spots across MOP's skill gradient (READ-ONLY MOP).

Extends the proven Blacks vertical slice to a handful of spots spanning O'Reilly
et al. 2016's MOP skill gradient, to see how the nearshore-frame ratings hold
where MOP is WEAKER before committing all ~170 CA spots. Reuses everything from
the Blacks slice (the nearshore frame, the cached mop_points.json, the prod
pipeline.interpret rating) — imported, not copied. Touches no prod path.

For each spot it (1) matches to the nearest MOP point in the FULL cache, (2) runs
~45 d of hourly MOP through the SAME nearshore chain -> per-hour stars, and (3)
JUDGES the result against both the spot's orientation-fallback rating and a nearby
buoy (does MOP track reality?), then emits a per-spot verdict CONSUME vs FALL BACK
and a checkable rollout rule.

Spots span: HIGH (San Diego / San Clemente Basin, dir R^2>0.9) -> MEDIUM (Santa
Monica / San Pedro) -> HARD (Santa Barbara Channel, dir R^2~0.04, the acid test)
-> Central/N CA coverage check.

THREDDS is egress-blocked in the dev sandbox (403); run on the Mac that pulls MOP.
Exits loudly rather than faking. --selftest validates the offline logic anywhere.

  python scripts/mop_handful_slice.py build-cache   # reuse/extend the Blacks cache
  python scripts/mop_handful_slice.py slice
  python scripts/mop_handful_slice.py --selftest
"""
from __future__ import annotations

import argparse
import bisect
import datetime
import math
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import mop_blacks_slice as B  # noqa: E402  reuse the proven chain
from mop_blacks_slice import (  # noqa: E402
    DODS, circ_offset, haversine_m, load_cache, pull_span, split_swell_hs,
    rate_nearshore, _egress_or_die,
)
from pipeline.interpret import (  # noqa: E402  the EXISTING rating, unchanged
    chop_multiplier, chop_ratio, composite_stars, directional_gain, face_ft,
    period_quality,
)
from urllib.error import HTTPError, URLError

RATING_SOURCE = "ww3"

# Stored CA spots across the skill gradient. zone/r2_dir from O'Reilly et al.
# 2016 (direction R^2 by region); buoys = ordered candidate deep-water CDIP
# stations (first reachable one wins; verify on the Mac).
SPOTS = [
    dict(name="San Diego Blacks Beach", lat=32.879677, lng=-117.252982, orient=263.0,
         arcs=[{"min": 183, "max": 343, "span": 160}], optimal=263,
         zone="HIGH", r2_dir=0.92, buoys=["100"]),          # Torrey Pines Outer
    dict(name="Lower Trestles", lat=33.38146, lng=-117.58590, orient=205.0,
         arcs=[{"min": 125, "max": 285, "span": 160}], optimal=205,
         zone="HIGH", r2_dir=0.90, buoys=["045", "100"]),   # Oceanside / Torrey Pines
    dict(name="Malibu Surfrider Beach", lat=34.03143, lng=-118.68887, orient=184.0,
         arcs=[{"min": 104, "max": 264, "span": 160}], optimal=184,
         zone="MEDIUM", r2_dir=0.60, buoys=["028", "092"]), # Santa Monica Bay / San Pedro
    dict(name="Rincon", lat=34.37181, lng=-119.47851, orient=210.0,
         arcs=[{"min": 130, "max": 290, "span": 160}], optimal=210,
         zone="HARD", r2_dir=0.04, buoys=["071", "107", "111"]),  # Harvest / Santa Barbara — ACID TEST
    dict(name="Ocean Beach SF", lat=37.75395, lng=-122.51204, orient=259.0,
         arcs=[{"min": 179, "max": 339, "span": 160}], optimal=259,
         zone="UNKNOWN", r2_dir=None, buoys=["142", "029"]),  # SF Bar / Point Reyes
]

# Distance is a WEAK proxy: MOP points sit on the 10 m contour, legitimately
# 0.5–1.5 km offshore, so we only HARD-disqualify beyond ~1.2 km. Within that,
# distance is informational; buoy agreement + shore-normal agreement are the gate.
MATCH_FALLBACK_M = 1200.0
SHORE_NORMAL_MAX_DELTA = 35.0  # |orientation_deg - metaShoreNormal|; beyond this the matched
                               # 10 m point faces a different stretch than the break -> not representative
HS_CORR_MIN = 0.80         # MOP must track the buoy's swell events
DIR_STD_MAX = 25.0         # MOP-vs-buoy direction offset must be a stable refraction
HARD_HS_CORR = 0.85        # low-skill zones need stronger agreement to override
HARD_DIR_STD = 20.0


# --------------------------------------------------------------------------- #
# Pure stats + verdict (validated by --selftest)                              #
# --------------------------------------------------------------------------- #
def pearson(a, b):
    a, b = np.asarray(a, float), np.asarray(b, float)
    if len(a) < 3 or np.std(a) == 0 or np.std(b) == 0:
        return float("nan")
    return float(np.corrcoef(a, b)[0, 1])


def circ_std_deg(offsets_deg):
    """Circular standard deviation (deg) of a set of angle offsets."""
    if len(offsets_deg) < 3:
        return float("nan")
    r = np.radians(np.asarray(offsets_deg, float))
    R = math.hypot(np.mean(np.cos(r)), np.mean(np.sin(r)))
    R = min(max(R, 1e-9), 1.0)
    return float(math.degrees(math.sqrt(-2.0 * math.log(R))))


def fallback_stars(hs, tp, dw_dp, arcs, optimal, orient):
    """Orientation-fallback rating: SAME Hs/Tp, but DEEP-WATER direction vs the
    spot's stored deep-water window/optimal — i.e. the current path's directional
    logic. Isolates the frame difference from the nearshore rating."""
    if hs is None or tp is None or dw_dp is None:
        return None
    dg = directional_gain(dw_dp, arcs, optimal, orient)
    eff = face_ft(hs, tp, RATING_SOURCE) * dg
    return composite_stars(eff, 1.0, 1.0,
                           chop_multiplier(chop_ratio(hs, hs)), period_quality(tp))


def verdict(zone, r2_dir, dist_m, hs_corr, dir_std, n_aligned, has_buoy, sn_delta):
    """CONSUME MOP vs FALL BACK. Gate = shore-normal agreement + buoy agreement;
    distance is only a far-outlier veto. *sn_delta* = orientation_deg − matched
    point metaShoreNormal (deg), or None if the point has no shore-normal."""
    # 1. distance: veto only the realistic far outliers (not 0.5–1.2 km contour offsets)
    if dist_m > MATCH_FALLBACK_M:
        return "FALL BACK", f"match {dist_m:.0f} m > {MATCH_FALLBACK_M:.0f} m — too far to be the break's contour"
    # 2. shore-normal representativeness: the matched 10 m point must face ~the break's way
    if sn_delta is not None and abs(sn_delta) > SHORE_NORMAL_MAX_DELTA:
        return "FALL BACK", (f"matched point shore-normal off the hand orientation by "
                             f"{abs(sn_delta):.0f}° (> {SHORE_NORMAL_MAX_DELTA:.0f}°) — not representative of the break")
    sn_ok = sn_delta is None or abs(sn_delta) <= SHORE_NORMAL_MAX_DELTA
    # 3. buoy verification (reality)
    if not has_buoy or n_aligned < 24:
        if zone == "HIGH" and sn_ok:
            return "CONSUME (unverified)", (f"HIGH-skill zone, dist {dist_m:.0f} m, shore-normal ok, "
                                            f"but no buoy cross-check")
        return "FALL BACK", "no buoy cross-check and not a clean HIGH-skill match — can't confirm MOP tracks reality"
    low_skill = zone == "HARD" or (r2_dir is not None and r2_dir < 0.3)
    snd = "n/a" if sn_delta is None else f"{abs(sn_delta):.0f}°"
    if low_skill:
        if hs_corr >= HARD_HS_CORR and dir_std <= HARD_DIR_STD:
            return "CONSUME (override)", (f"low-skill zone but buoy agreement strong "
                                          f"(Hs r={hs_corr:.2f}, dir_std={dir_std:.0f}°, sn Δ{snd})")
        return "FALL BACK", (f"low-skill zone (dir R²~{r2_dir}) and buoy agreement weak "
                             f"(Hs r={hs_corr:.2f}, dir_std={dir_std:.0f}°)")
    if hs_corr >= HS_CORR_MIN and dir_std <= DIR_STD_MAX:
        return "CONSUME", (f"skill {zone}, dist {dist_m:.0f} m, shore-normal Δ{snd}, "
                           f"Hs r={hs_corr:.2f}, dir_std={dir_std:.0f}°")
    return "FALL BACK", (f"buoy disagreement (Hs r={hs_corr:.2f}, dir_std={dir_std:.0f}°) "
                         f"despite {zone} zone")


# --------------------------------------------------------------------------- #
# Network: buoy pull (best effort)                                            #
# --------------------------------------------------------------------------- #
def pull_buoy(stations, t0, t1):
    """[(t, hs, dp)] for the first reachable CDIP buoy in *stations* over [t0,t1]
    (deep-water reference). Returns (series, station_id) or (None, None)."""
    import netCDF4
    if isinstance(stations, str):
        stations = [stations]
    for station in stations:
        for url in (f"{DODS}/cdip/realtime/{station}p1_rt.nc",
                    f"{DODS}/cdip/archive/{station}p1/{station}p1_historic.nc"):
            try:
                nc = netCDF4.Dataset(url)
            except Exception:  # noqa: BLE001
                continue
            try:
                t = np.asarray(nc.variables["waveTime"][:])
                hs = np.asarray(nc.variables["waveHs"][:])
                dp = np.asarray(nc.variables["waveDp"][:])
                m = (t >= t0) & (t <= t1)
                if m.any():
                    return list(zip(t[m].tolist(), hs[m].tolist(), dp[m].tolist())), station
            finally:
                nc.close()
    return None, None


def _match(cache, lat, lon):
    cand = [(pid, m) for pid, m in cache.items() if m.get("lat") is not None]
    pid, m = min(cand, key=lambda kv: haversine_m(lat, lon, kv[1]["lat"], kv[1]["lon"]))
    return pid, m, haversine_m(lat, lon, m["lat"], m["lon"])


def _sample_days(rows, n_big=3, n_small=2):
    picks, seen = [], set()
    for r in sorted(rows, key=lambda r: r["hs"], reverse=True):
        d = datetime.datetime.utcfromtimestamp(r["t"]).strftime("%Y-%m-%d")
        if d not in seen:
            seen.add(d); picks.append(r)
        if len(picks) >= n_big:
            break
    picks += sorted(rows, key=lambda r: r["hs"])[:n_small]
    return sorted(picks, key=lambda r: r["t"])


def run_slice(days=45):
    cache = load_cache()
    if cache is None:
        return 3
    ncoords = sum(1 for v in cache.values() if v.get("lat") is not None)
    print(f"cache: {len(cache)} MOP points, {ncoords} coord-resolved (full set)\n")

    summary = []
    for sp in SPOTS:
        print("=" * 78)
        print(f"{sp['name']}  [{sp['zone']} zone, dir R²~{sp['r2_dir']}]")
        pid, meta, dist = _match(cache, sp["lat"], sp["lng"])
        raw_sn = meta.get("shore_normal")              # MOP point's published shore-normal
        sn = raw_sn if raw_sn is not None else sp["orient"]   # used for the nearshore rating
        # shore-normal agreement: hand orientation vs matched point's facing
        sn_delta = circ_offset(sp["orient"], raw_sn) if raw_sn is not None else None
        sn_txt = (f"Δ{abs(sn_delta):.0f}° vs orient {sp['orient']:.0f}" if sn_delta is not None
                  else "metaShoreNormal absent")
        flag = "  *** > %.0f m ***" % MATCH_FALLBACK_M if dist > MATCH_FALLBACK_M else ""
        snflag = "  *** shore-normal mismatch ***" if (sn_delta is not None and abs(sn_delta) > SHORE_NORMAL_MAX_DELTA) else ""
        print(f"  MOP point {pid} @ {dist:.0f} m (depth {meta.get('water_depth')} m, "
              f"shore_normal {raw_sn}; {sn_txt}){flag}{snflag}")

        try:
            rows = pull_span(meta["url"], days)
        except (HTTPError, URLError, OSError) as e:
            _egress_or_die(e); return 2
        rows = [r for r in rows if r["tp"] and r["dp"] is not None]
        if not rows:
            print("  no usable MOP rows; skipping"); continue
        for r in rows:
            r["stars"], r["dg"], r["eff"] = rate_nearshore(r["hs"], r["tp"], r["dp"], r["swell_hs"], sn)
        st = np.array([r["stars"] for r in rows])

        # buoy cross-check (reality) — first reachable of the candidate stations
        buoy, bname = pull_buoy(sp["buoys"], rows[0]["t"], rows[-1]["t"])
        hs_corr = dir_std = float("nan"); n_al = 0; fb_stars = []
        if buoy:
            bt = [x[0] for x in buoy]
            mh, bh, offs = [], [], []
            for r in rows:
                j = min(bisect.bisect_left(bt, r["t"]), len(buoy) - 1)
                if abs(bt[j] - r["t"]) <= 3600:
                    mh.append(r["hs"]); bh.append(buoy[j][1])
                    offs.append(circ_offset(r["dp"], buoy[j][2]))          # nearshore - deepwater
                    fb = fallback_stars(r["hs"], r["tp"], buoy[j][2],
                                        sp["arcs"], sp["optimal"], sp["orient"])
                    if fb is not None:
                        fb_stars.append(fb)
            n_al = len(mh)
            hs_corr = pearson(mh, bh); dir_std = circ_std_deg(offs)
            dir_mean = float(np.mean(offs)) if offs else float("nan")
            print(f"  buoy {bname} (tried {sp['buoys']}): {n_al} aligned hrs | "
                  f"Hs corr r={hs_corr:.2f} | refraction offset {dir_mean:+.0f}° "
                  f"(stability dir_std={dir_std:.0f}°)")
        else:
            print(f"  buoys {sp['buoys']}: ALL UNREACHABLE — reality cross-check unavailable (not faked)")

        print(f"  MOP nearshore stars: min {st.min():.1f} / median {np.median(st):.1f} / max {st.max():.1f}"
              + (f"   |   orientation-fallback stars: median {np.median(fb_stars):.1f}"
                 if fb_stars else "   |   fallback: n/a (no deep-water dir)"))
        print(f"  {'when (UTC)':17}{'Hs':>5}{'Tp':>4}{'Dp':>5}{'offN':>6}{'swHs':>6}{'dg':>5}{'MOP★':>6}{'fb★':>5}")
        for r in _sample_days(rows):
            when = datetime.datetime.utcfromtimestamp(r["t"]).strftime("%Y-%m-%d %H:%M")
            fb = ""
            if buoy:
                j = min(bisect.bisect_left([x[0] for x in buoy], r["t"]), len(buoy) - 1)
                if abs(buoy[j][0] - r["t"]) <= 3600:
                    f = fallback_stars(r["hs"], r["tp"], buoy[j][2], sp["arcs"], sp["optimal"], sp["orient"])
                    fb = f"{f:.1f}" if f is not None else ""
            print(f"  {when:17}{r['hs']:5.2f}{r['tp']:4.0f}{r['dp']:5.0f}"
                  f"{circ_offset(r['dp'], sn):6.0f}{r['swell_hs']:6.2f}{r['dg']:5.2f}{r['stars']:6.1f}{fb:>5}")

        v, why = verdict(sp["zone"], sp["r2_dir"], dist, hs_corr, dir_std, n_al, bool(buoy), sn_delta)
        print(f"  VERDICT: {v}  — {why}")
        summary.append((sp["name"], sp["zone"], pid, dist, sn_delta, hs_corr, dir_std, v))

    print("\n" + "=" * 78 + "\nSUMMARY")
    print(f"  {'spot':26}{'zone':8}{'point':9}{'dist_m':>7}{'snΔ':>5}{'Hs_r':>6}{'dirSD':>6}  verdict")
    for name, zone, pid, dist, snd, hc, ds, v in summary:
        snds = "n/a" if snd is None else f"{abs(snd):.0f}"
        print(f"  {name:26}{zone:8}{pid:9}{dist:7.0f}{snds:>5}{hc:6.2f}{ds:6.0f}  {v}")
    print("\nRECOMMENDED ROLLOUT RULE (checkable per spot):")
    print(f"  CONSUME MOP iff  |orientation_deg - metaShoreNormal| <= {SHORE_NORMAL_MAX_DELTA:.0f}°  (matched point faces the break)")
    print(f"                   AND MOP-vs-nearest-buoy Hs corr r >= {HS_CORR_MIN}")
    print(f"                   AND direction-offset stability circ_std <= {DIR_STD_MAX}°")
    print(f"                   AND match_distance <= {MATCH_FALLBACK_M:.0f} m  (far-outlier veto only — distance is otherwise informational)")
    print(f"     low-skill zones (SB Channel / dir R²<0.3) need r >= {HARD_HS_CORR} AND circ_std <= {HARD_DIR_STD}°, else FALL BACK")
    print(f"     no buoy within range + not HIGH-skill  => FALL BACK (can't verify)")
    print(f"  everything else keeps the existing orientation path.")
    return 0


def run_selftest():
    ok = True
    def check(n, c):
        nonlocal ok; ok = ok and c; print(f"  {'PASS' if c else 'FAIL'}  {n}")

    check("pearson tracks co-moving series", pearson([1,2,3,4,5], [1.1,2,2.9,4.2,5]) > 0.95)
    check("pearson ~0 for unrelated", abs(pearson([1,2,3,4,5], [3,1,4,1,5])) < 0.6)
    check("circ_std small for stable offsets", circ_std_deg([18,20,22,19,21]) < 5)
    check("circ_std large for scattered offsets", circ_std_deg([10,180,-90,90,-170]) > 60)

    # verdict matrix (recalibrated: signature ends with sn_delta)
    v1, _ = verdict("HIGH", 0.92, 458, 0.89, 12, 600, True, 8)     # Blacks-like -> CONSUME
    v2, _ = verdict("HARD", 0.04, 150, 0.50, 70, 600, True, 10)    # Rincon weak buoy -> fall back
    v3, _ = verdict("HARD", 0.04, 150, 0.90, 12, 600, True, 10)    # low-skill but strong buoy -> override
    v4, _ = verdict("HIGH", 0.90, 1500, 0.95, 5, 600, True, 5)     # far outlier (>1.2km) -> fall back
    v5, _ = verdict("UNKNOWN", None, 200, 0.0, 0, 0, False, 5)     # no buoy, not HIGH -> fall back
    v6, _ = verdict("MEDIUM", 0.60, 300, 0.85, 20, 600, True, 70)  # Malibu: shore-normal mismatch -> fall back
    v7, _ = verdict("HIGH", 0.90, 900, 0.94, 9, 600, True, 6)      # Trestles/OBSF: 500-900m now CONSUME
    check(f"HIGH+close+good -> CONSUME ({v1})", v1.startswith("CONSUME"))
    check(f"HARD+weak buoy -> FALL BACK ({v2})", v2 == "FALL BACK")
    check(f"HARD+strong buoy -> CONSUME override ({v3})", v3.startswith("CONSUME"))
    check(f"far outlier >1.2km -> FALL BACK ({v4})", v4 == "FALL BACK")
    check(f"no buoy + not HIGH -> FALL BACK ({v5})", v5 == "FALL BACK")
    check(f"shore-normal mismatch (Δ70°) -> FALL BACK ({v6})", v6 == "FALL BACK")
    check(f"good agreement @900m -> CONSUME (relaxed distance) ({v7})", v7.startswith("CONSUME"))

    sn = 265.0
    big, _, _ = rate_nearshore(2.5, 17, 264, 2.45, sn)
    junk, _, _ = rate_nearshore(0.4, 7, 250, 0.15, sn)
    check(f"reused chain still monotonic (big {big} > junk {junk})", big > junk)
    print("\nself-test:", "ALL PASS — stats + verdict logic + reused chain sound."
          if ok else "FAILURES above")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="slice", choices=["build-cache", "slice"])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--workers", type=int, default=8)
    a = ap.parse_args(argv)
    if a.selftest:
        return run_selftest()
    if a.cmd == "build-cache":
        return B.build_cache(a.workers)   # reuse the Blacks cache builder (shared mop_points.json)
    return run_slice(a.days)


if __name__ == "__main__":
    raise SystemExit(main())
