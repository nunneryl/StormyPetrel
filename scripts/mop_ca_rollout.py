#!/usr/bin/env python3
"""MOP CA rollout Stage 1 — full-California verdict table (READ-ONLY MOP).

Scales the proven handful slice to EVERY CA spot in the roster, to size the MOP
rollout before Stage 2 (integration). For each CA spot it (1) matches to the
nearest MOP point in the FULL cache, (2) pulls ~45 d of hourly MOP through the
SAME nearshore chain, (3) cross-checks against the spot's nearest NDBC buoy, and
(4) emits CONSUME vs FALL BACK per the calibrated adoption rule.

REUSED, not copied:
  * nearshore chain  -> mop_blacks_slice.rate_nearshore / split_swell_hs / load_cache
  * adoption rule    -> mop_handful_slice.verdict (+ its thresholds)
  * MOP cache        -> scripts/mop_points.json (the full ~11.7k-point set)
  * buoy parser      -> pipeline.forecast.buoys._parse_realtime2 (NDBC realtime2)
New logic: roster selection (region_hint == "California"), per-spot skill-zone
(mirrors orientation_relook.ca_zone), wiring the roster's NDBC nearest_buoy_id
into the cross-check, and a window-aware MOP pull (pull_mop_window) that fetches
the NOWCAST flavor for the buoy's live span — the cached hindcast flavor ends
~early 2025 and never overlaps the live buoy, so its trailing window can't align.

THREDDS is egress-blocked in the dev sandbox (403); run on the Mac that pulls
MOP (and has scripts/mop_points.json). It exits loudly rather than faking.
--selftest validates the offline logic anywhere. Nothing here touches prod.

  python3 scripts/mop_ca_rollout.py                 # full CA table (Mac: needs cache + egress)
  python3 scripts/mop_ca_rollout.py --limit 8       # smoke test on the first 8 CA spots
  python3 scripts/mop_ca_rollout.py --selftest      # offline logic proof
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)   # mop_blacks_slice / mop_handful_slice
sys.path.insert(0, ROOT)   # pipeline.*

from mop_blacks_slice import (  # noqa: E402  reuse the proven nearshore chain
    circ_offset, haversine_m, load_cache, rate_nearshore, split_swell_hs, _egress_or_die,
)
from mop_handful_slice import (  # noqa: E402  reuse the calibrated rule + thresholds + stats
    DIR_STD_MAX, HARD_DIR_STD, HARD_HS_CORR, HS_CORR_MIN, MATCH_FALLBACK_M,
    SHORE_NORMAL_MAX_DELTA, circ_std_deg, pearson, verdict,
)
from pipeline.forecast.buoys import _SPEC_FIELDS, _STD_FIELDS, _parse_realtime2  # noqa: E402
from pipeline.config import NDBC_REALTIME2_BASE  # noqa: E402
from pipeline.http import get as http_get  # noqa: E402  proven NDBC fetch (USER_AGENT, retries, CA, gzip)
from urllib.error import HTTPError, URLError  # noqa: E402

ROSTER = os.path.join(ROOT, "pipeline", "spots_enriched.json")
OUT = os.path.join(HERE, "mop_ca_verdicts.json")
MAPPING_OUT = os.path.join(HERE, "mop_ca_buoy_recovery.json")

# Nominal direction R² per skill zone (O'Reilly et al. 2016 gradient). Only used
# by verdict()'s low-skill branch, which also keys on zone=="HARD"; carried for
# transparency in the output.
ZONE_R2 = {"HIGH": 0.9, "MEDIUM": 0.6, "HARD": 0.04, "UNKNOWN": None}

# MOP covers the CA mainland 10 m contour, so a real CA spot matches within a few
# km. A match beyond this means the spot isn't near any MOP point — an off-coast
# coord (e.g. a mis-tagged region_hint) or a coverage gap — so skip it rather than
# pull MOP and emit a bogus verdict.
MATCH_SANITY_M = 25_000.0

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(name):
    return _SLUG_RE.sub("-", (name or "").lower()).strip("-")


def ca_zone(lat, lng):
    """Coarse CA skill zone by latitude band — mirrors orientation_relook.ca_zone
    and reproduces the handful slice's hand-set zones (Blacks/Trestles HIGH,
    Malibu MEDIUM, Rincon HARD, OB SF UNKNOWN)."""
    if lng > -114 or lat < 32 or lat > 42:
        return None
    if 32.5 <= lat <= 33.5:
        return "HIGH"      # San Diego / San Clemente Basin (dir R²>0.9)
    if 33.5 < lat <= 34.05:
        return "MEDIUM"    # San Pedro / Santa Monica
    if 34.05 < lat <= 34.6:
        return "HARD"      # Santa Barbara Channel (dir R²~0.04)
    return "UNKNOWN"       # Central / Northern CA


def _match(cache, lat, lon):
    """Nearest coord-resolved MOP point to (lat,lon). (pid, meta, dist_m)."""
    cand = [(pid, m) for pid, m in cache.items() if m.get("lat") is not None]
    pid, m = min(cand, key=lambda kv: haversine_m(lat, lon, kv[1]["lat"], kv[1]["lon"]))
    return pid, m, haversine_m(lat, lon, m["lat"], m["lon"])


# --------------------------------------------------------------------------- #
# MOP pull for a SPECIFIC time window (not "last N days of the dataset").       #
# The cache prefers the HINDCAST flavor, whose data ends ~early 2025, so its    #
# trailing 45 d never overlapped the live NDBC buoy (last ~45 d). Pull the      #
# NOWCAST flavor for the buoy's exact span instead — guaranteed overlap, and    #
# the [t0,t1] slice excludes any forecast tail (t > t1) and fill values.        #
# --------------------------------------------------------------------------- #
def _nowcast_url(url):
    """Swap the cached flavor to nowcast (the one that reaches the live window)."""
    for fl in ("_hindcast", "_forecast", "_ecmwf_fc"):
        if fl in url:
            return url.replace(fl, "_nowcast")
    return url  # already nowcast / unknown flavor


def _rows_in_window(times, freq, hs, tp, dp, ed, t0, t1):
    """Pure selector → MOP row dicts whose waveTime is in [t0,t1] (epoch sec),
    with out-of-range/fill times dropped (so a forecast tail or netCDF fill can't
    leak in). Kept separate from the netCDF I/O so --selftest can exercise it."""
    times = np.asarray(times)
    lo = int(np.searchsorted(times, t0))
    hi = int(np.searchsorted(times, t1, side="right"))
    rows = []
    for k in range(lo, min(hi, len(times))):
        t = _norm_epoch(times[k])
        if t is None or not (t0 <= t <= t1):
            continue
        s_hs = split_swell_hs(ed[k], freq)[1] if (ed is not None and freq is not None) else 0.0
        rows.append(dict(
            t=t,
            hs=float(hs[k]) if hs is not None else None,
            tp=float(tp[k]) if tp is not None else None,
            dp=float(dp[k]) if dp is not None else None,
            swell_hs=s_hs,
        ))
    return rows


def pull_mop_window(url, t0, t1):
    """MOP rows with waveTime in [t0,t1] (epoch sec). Reads only the windowed
    slice over OPeNDAP. Real data only — forecast (t > t1) and fill excluded."""
    import netCDF4
    nc = netCDF4.Dataset(url)
    try:
        times_full = np.asarray(nc.variables["waveTime"][:])
        lo = int(np.searchsorted(times_full, t0))
        hi = int(np.searchsorted(times_full, t1, side="right"))
        if hi <= lo:
            return []
        freq = np.asarray(nc.variables["waveFrequency"][:]) if "waveFrequency" in nc.variables else None
        def v(n):
            return np.asarray(nc.variables[n][lo:hi]) if n in nc.variables else None
        return _rows_in_window(times_full[lo:hi], freq, v("waveHs"), v("waveTp"),
                               v("waveDp"), v("waveEnergyDensity"), t0, t1)
    finally:
        nc.close()


# --------------------------------------------------------------------------- #
# Buoy reality cross-check — NDBC realtime2 via the pipeline's PROVEN fetcher.   #
# Primary source is .txt (standard met: WVHT + MWD), exactly like the prod buoy  #
# fetcher (pipeline.forecast.buoys), with .spec as a fallback. (Stage-1 smoke    #
# test used .spec-only over raw urllib with a custom UA and got nothing for      #
# every station, incl. the standard NOAA buoy 46012 — the prod path, .txt first  #
# via pipeline.http.get, is what works. Failures are now surfaced, not silent.)  #
# --------------------------------------------------------------------------- #
def _iso_to_epoch(iso):
    try:
        return datetime.datetime.fromisoformat(iso).timestamp()
    except (TypeError, ValueError):
        return None


def _norm_epoch(t):
    """Normalize a timestamp to epoch SECONDS, or None if implausible. Tolerates
    milliseconds (s-vs-ms mismatch) and rejects netCDF fill/garbage values (e.g.
    9.96e36 from a masked waveTime tail) so they can't masquerade as real times
    in the MOP↔buoy join and silently zero the overlap."""
    if t is None:
        return None
    try:
        t = float(t)
    except (TypeError, ValueError):
        return None
    if t > 1e12:                   # milliseconds → seconds
        t /= 1000.0
    if not (9.5e8 < t < 4.1e9):    # ~2000 .. ~2100; rejects fills/garbage
        return None
    return t


def _fetch_realtime2(station_id, label):
    """Raw text of an NDBC realtime2 file (label 'txt' or 'spec') via the proven
    pipeline fetcher (USER_AGENT, retries, CA, gzip), or None. 404s come back as
    HTML error pages -> treated as None, like pipeline.forecast.buoys does."""
    url = f"{NDBC_REALTIME2_BASE}/{station_id.upper()}.{label}"
    try:
        resp = http_get(url)
    except Exception:  # noqa: BLE001  network/HTTP/SSL -> no buoy (never faked)
        return None
    txt = getattr(resp, "text", None)
    if not txt or txt.strip().startswith("<"):
        return None
    return txt


def _series_from_text(text, field_map):
    """Sorted [(t_epoch, hs_m, dir_deg)] from realtime2 text: Hs = WVHT,
    direction = MWD (mean wave dir). Rows missing either are dropped."""
    rows = []
    for o in _parse_realtime2(text, field_map):
        hs, dp, t = o.get("wave_height_m"), o.get("mean_wave_dir_deg"), _iso_to_epoch(o.get("time"))
        if hs is not None and dp is not None and t is not None:
            rows.append((t, float(hs), float(dp)))
    rows.sort()
    return rows


_BUOY_CACHE: dict = {}


def buoy_series(station_id):
    """(series, reason) for an NDBC buoy. series = sorted [(t,hs,dp)] (~45 d) or
    None; *reason* is a short human string so failures are VISIBLE, not silent.
    Tries .txt (standard WVHT+MWD — universal) then .spec, via the prod fetcher.
    Cached per station. Never fabricated."""
    if not station_id:
        return None, "no buoy id in roster"
    if station_id in _BUOY_CACHE:
        return _BUOY_CACHE[station_id]
    tried = []
    result = (None, "no wave data")
    for label, fmap in (("txt", _STD_FIELDS), ("spec", _SPEC_FIELDS)):   # .txt first (universal)
        text = _fetch_realtime2(station_id, label)
        if not text:
            tried.append(f".{label}✗404")
            continue
        rows = _series_from_text(text, fmap)
        if len(rows) >= 3:
            result = (rows, f".{label}:{len(rows)} rows")
            break
        tried.append(f".{label}:{len(rows)}rows")
    else:  # no break -> neither file yielded usable rows; name BOTH attempts (not just .spec)
        result = (None, "fetch failed (" + "+".join(tried) + ")")
    _BUOY_CACHE[station_id] = result
    return result


def cross_check(rows, buoy):
    """(hs_corr, dir_std, n_aligned) of MOP vs buoy — Hs correlation +
    refraction-offset stability. Joined by UTC HOUR BUCKET (floor(epoch/3600))
    with a ±1h fallback, after normalizing both sides to epoch seconds via
    _norm_epoch (so a s-vs-ms or fill-value mismatch can't zero the overlap, and
    sub-hourly buoy sampling buckets cleanly to the hourly MOP series)."""
    bucket = {}
    for t, hs, dp in buoy:
        tn = _norm_epoch(t)
        if tn is not None:
            bucket[int(tn // 3600)] = (hs, dp)   # last obs in the hour wins
    mh, bh, offs = [], [], []
    for r in rows:
        tn = _norm_epoch(r["t"])
        if tn is None:
            continue
        k = int(tn // 3600)
        b = bucket.get(k) or bucket.get(k - 1) or bucket.get(k + 1)
        if b:
            mh.append(r["hs"]); bh.append(b[0]); offs.append(circ_offset(r["dp"], b[1]))
    return pearson(mh, bh), circ_std_deg(offs), len(mh)


# --------------------------------------------------------------------------- #
# Per-spot rating + the full CA run                                           #
# --------------------------------------------------------------------------- #
def _row(s, zone, pid, dist, raw_sn, orient, sn_delta, buoy_id, has_buoy,
         hs_corr, dir_std, n_al, v, why, stars=None, buoy_reason=""):
    f = lambda x: None if x is None or (isinstance(x, float) and x != x) else round(float(x), 2)
    return {
        "slug": _slug(s["name"]), "name": s["name"], "lat": s["lat"], "lng": s["lng"],
        "zone": zone, "mop_point": pid, "dist_m": round(dist) if dist is not None else None,
        "shore_normal": raw_sn, "orientation_deg": orient,
        "sn_delta": None if sn_delta is None else round(abs(sn_delta), 1),
        "buoy_id": buoy_id, "has_buoy": has_buoy, "n_aligned": n_al, "buoy_reason": buoy_reason,
        "hs_corr": f(hs_corr), "dir_std": None if dir_std is None else (None if dir_std != dir_std else round(dir_std, 1)),
        "mop_stars_median": None if stars is None else round(float(np.median(stars)), 2),
        "verdict": v, "consume": v.startswith("CONSUME"), "reason": why, "skipped": False,
    }


def _skip(s, reason, pid=None, dist=None, zone=None):
    """A data-quality exclusion (off-coast coord / no MOP coverage) — recorded and
    flagged, NOT counted as CONSUME/FALL BACK (it was never rated)."""
    return {
        "slug": _slug(s["name"]), "name": s["name"], "lat": s["lat"], "lng": s["lng"],
        "zone": zone, "mop_point": pid, "dist_m": round(dist) if dist is not None else None,
        "shore_normal": None, "orientation_deg": s.get("orientation_deg"), "sn_delta": None,
        "buoy_id": s.get("nearest_buoy_id"), "has_buoy": False, "n_aligned": 0, "buoy_reason": "",
        "hs_corr": None, "dir_std": None, "mop_stars_median": None,
        "verdict": "SKIP", "consume": False, "reason": reason, "skipped": True,
    }


def run(days=45, limit=None):
    spots = json.load(open(ROSTER))
    ca_all = [s for s in spots if s.get("region_hint") == "California"]
    ca = [s for s in ca_all if isinstance(s.get("orientation_deg"), (int, float))]
    skipped_no_orient = len(ca_all) - len(ca)
    print(f"CA spots in roster (region_hint=California): {len(ca_all)}"
          + (f"   ({skipped_no_orient} without orientation_deg — skipped)" if skipped_no_orient else ""))
    cache = load_cache()
    if cache is None:
        print("no scripts/mop_points.json cache — run "
              "`python3 scripts/mop_handful_slice.py build-cache` on the Mac first", file=sys.stderr)
        return 3
    ncoords = sum(1 for v in cache.values() if v.get("lat") is not None)
    if limit:
        ca = ca[:limit]
    print(f"cache: {len(cache)} MOP points ({ncoords} coord-resolved)")
    print(f"rating {len(ca)} CA spots against MOP ({days}d window)\n")

    results, consec_net_fail, any_mop_ok = [], 0, False
    for idx, s in enumerate(ca, 1):
        lat, lng, orient = s["lat"], s["lng"], float(s["orientation_deg"])
        slug = _slug(s["name"])

        # Bug-2 guard A: region_hint says CA but the coords aren't on the CA coast
        # (e.g. 56th Street -> -74.7°E = New Jersey). Skip before any matching/pull.
        zone = ca_zone(lat, lng)
        if zone is None:
            results.append(_skip(s, f"off-coast coord ({lat:.3f},{lng:.3f}) — region_hint=CA but not the CA coast"))
            print(f"  [{idx:>3}/{len(ca)}] {slug:30} SKIP off-coast ({lat:.3f},{lng:.3f})")
            continue

        pid, meta, dist = _match(cache, lat, lng)
        # Bug-2 guard B: nearest MOP point absurdly far -> off-coast/coverage gap.
        if dist > MATCH_SANITY_M:
            results.append(_skip(s, f"nearest MOP point {dist/1000:.0f} km away (> {MATCH_SANITY_M/1000:.0f} km) "
                                    f"— no MOP coverage", pid, dist, zone))
            print(f"  [{idx:>3}/{len(ca)}] {slug:30} SKIP no MOP coverage (nearest {dist/1000:.0f} km)")
            continue

        raw_sn = meta.get("shore_normal")
        sn = raw_sn if raw_sn is not None else orient
        sn_delta = circ_offset(orient, raw_sn) if raw_sn is not None else None
        bid = s.get("nearest_buoy_id")

        # Buoy FIRST (NDBC realtime2 = the live ~45 d) so we can pull MOP for the
        # SAME window. The cached MOP url is the hindcast flavor (ends ~early 2025);
        # its trailing 45 d is a year+ before the live buoy → never overlaps. Pull
        # the NOWCAST for the buoy's exact span so the two series align.
        series, breason = buoy_series(bid)
        bts = [t for t in (_norm_epoch(b[0]) for b in (series or [])) if t is not None]
        if bts:
            t0, t1 = min(bts), max(bts)
        else:
            now = datetime.datetime.now(datetime.timezone.utc).timestamp()
            t0, t1 = now - days * 86400.0, now

        mop_url = _nowcast_url(meta["url"])
        try:
            rows = pull_mop_window(mop_url, t0, t1)
            if not rows and mop_url != meta["url"]:
                rows = pull_mop_window(meta["url"], t0, t1)  # fall back to the cached flavor
            consec_net_fail = 0; any_mop_ok = True
        except (HTTPError, URLError, OSError) as e:
            consec_net_fail += 1
            if consec_net_fail >= 3 and not any_mop_ok:
                _egress_or_die(e)  # 3 straight failures, no MOP success yet → total egress block; bail loudly
                return 2
            print(f"  [{idx:>3}/{len(ca)}] {slug:30} MOP pull failed ({type(e).__name__}) — FALL BACK")
            results.append(_row(s, zone, pid, dist, raw_sn, orient, sn_delta, bid, False,
                                float("nan"), float("nan"), 0, "FALL BACK",
                                f"MOP pull failed: {type(e).__name__}", buoy_reason="(MOP failed)"))
            continue

        rows = [r for r in rows if r["tp"] and r["dp"] is not None]
        if not rows:
            results.append(_row(s, zone, pid, dist, raw_sn, orient, sn_delta, bid, False,
                                float("nan"), float("nan"), 0, "FALL BACK",
                                "no MOP rows in the buoy window (nowcast may not cover it — rebuild cache)",
                                buoy_reason=breason))
            print(f"  [{idx:>3}/{len(ca)}] {slug:30} no MOP in buoy window — FALL BACK")
            continue
        for r in rows:
            r["stars"], _, _ = rate_nearshore(r["hs"], r["tp"], r["dp"], r["swell_hs"], sn)
        stars = np.array([r["stars"] for r in rows])

        # buoy reality cross-check over the now-overlapping window.
        if series:
            hs_corr, dir_std, n_al = cross_check(rows, series)
            if n_al:
                breason = f"{breason}, {n_al} aligned"
        else:
            hs_corr, dir_std, n_al = float("nan"), float("nan"), 0
        has_buoy = n_al > 0

        v, why = verdict(zone, ZONE_R2.get(zone), dist, hs_corr, dir_std, n_al, has_buoy, sn_delta)
        results.append(_row(s, zone, pid, dist, raw_sn, orient, sn_delta, bid, has_buoy,
                            hs_corr, dir_std, n_al, v, why, stars, breason))
        sd = "n/a" if sn_delta is None else f"{abs(sn_delta):.0f}"
        hc = "  nan" if hs_corr != hs_corr else f"{hs_corr:5.2f}"
        ds = " nan" if dir_std != dir_std else f"{dir_std:4.0f}"
        tag = "" if has_buoy else f"  [no buoy: {breason}]"
        print(f"  [{idx:>3}/{len(ca)}] {slug:30} {zone:7} pt {pid:>7} "
              f"{dist:5.0f}m snΔ{sd:>3} r{hc} sd{ds} -> {v}{tag}")

    _write_and_summarize(results, len(ca_all), skipped_no_orient, days)
    return 0


def _write_and_summarize(results, n_ca_all, skipped_no_orient, days):
    zones = ["HIGH", "MEDIUM", "HARD", "UNKNOWN"]
    rated = [r for r in results if not r.get("skipped")]
    skipped = [r for r in results if r.get("skipped")]
    totals = {z: {"CONSUME": 0, "FALL BACK": 0} for z in zones}
    consume = fallback = 0
    for r in rated:
        b = "CONSUME" if r["consume"] else "FALL BACK"
        totals.setdefault(r["zone"], {"CONSUME": 0, "FALL BACK": 0})[b] += 1
        consume += r["consume"]; fallback += not r["consume"]
    no_buoy = [r for r in rated if not r["has_buoy"]]

    payload = {
        "_comment": "MOP CA rollout Stage 1 — per-spot CONSUME/FALL BACK to size the rollout. "
                    "Read-only; nothing wired into prod. Reuses the handful slice's chain + rule.",
        "_meta": {
            "window_days": days, "n_ca_roster": n_ca_all, "n_rated": len(rated),
            "skipped_no_orientation": skipped_no_orient, "skipped_data_quality": len(skipped),
            "consume": consume, "fall_back": fallback, "no_buoy": len(no_buoy),
            "by_zone": totals,
            "thresholds": {
                "shore_normal_max_delta": SHORE_NORMAL_MAX_DELTA, "match_fallback_m": MATCH_FALLBACK_M,
                "hs_corr_min": HS_CORR_MIN, "dir_std_max": DIR_STD_MAX,
                "hard_hs_corr": HARD_HS_CORR, "hard_dir_std": HARD_DIR_STD, "match_sanity_m": MATCH_SANITY_M,
            },
        },
        "spots": sorted(rated, key=lambda r: (not r["consume"], r["zone"], r["slug"])) + skipped,
    }
    with open(OUT, "w") as f:
        json.dump(payload, f, indent=2, ensure_ascii=False)

    print("\n" + "=" * 96 + "\nPER-SPOT TABLE (rated; CONSUME first, then by zone)")
    print(f"  {'slug':30}{'point':>8}{'dist':>6}{'snΔ':>5}{'Hs_r':>6}{'dirSD':>6}  {'zone':8} verdict")
    for r in sorted(rated, key=lambda r: (not r["consume"], r["zone"], r["slug"])):
        sd = "n/a" if r["sn_delta"] is None else f"{r['sn_delta']:.0f}"
        hc = "  -- " if r["hs_corr"] is None else f"{r['hs_corr']:5.2f}"
        ds = "  --" if r["dir_std"] is None else f"{r['dir_std']:4.0f}"
        nb = "" if r["has_buoy"] else f"  [no buoy: {r.get('buoy_reason','')}]"
        print(f"  {r['slug']:30}{str(r['mop_point']):>8}{r['dist_m']:>6}{sd:>5}{hc:>6}{ds:>6}  {r['zone']:8} {r['verdict']}{nb}")

    print("\n" + "=" * 96 + "\nTOTALS")
    print(f"  CA roster {n_ca_all}  ·  rated {len(rated)}  ·  skipped {len(skipped)} (data quality)  "
          f"·  {skipped_no_orient} no-orientation")
    print(f"  rated → CONSUME {consume}  ·  FALL BACK {fallback}   (no usable buoy: {len(no_buoy)})")
    print(f"  {'zone':10}{'CONSUME':>9}{'FALL BACK':>11}{'total':>7}")
    for z in zones:
        c, fb = totals[z]["CONSUME"], totals[z]["FALL BACK"]
        if c or fb:
            print(f"  {z:10}{c:>9}{fb:>11}{c+fb:>7}")
    if skipped:
        print(f"\n  {len(skipped)} SKIPPED (data quality — not rated):")
        for r in skipped[:20]:
            print(f"    {r['slug']:30} {r['reason']}")
        if len(skipped) > 20:
            print(f"    … and {len(skipped) - 20} more")
    if no_buoy:
        print(f"\n  {len(no_buoy)} rated spots with NO usable buoy cross-check (verdict via shore-normal/distance only):")
        for r in no_buoy[:20]:
            print(f"    {r['slug']:30} buoy={r['buoy_id'] or 'none'}  ({r.get('buoy_reason','')})")
        if len(no_buoy) > 20:
            print(f"    … and {len(no_buoy) - 20} more")
    print(f"\nwrote {OUT}")
    print("\nADOPTION RULE (reused from the handful slice; this table only sizes the rollout — review before Stage 2):")
    print(f"  CONSUME iff |orientation_deg - metaShoreNormal| <= {SHORE_NORMAL_MAX_DELTA:.0f}°"
          f"  AND Hs corr r >= {HS_CORR_MIN}  AND dir stability <= {DIR_STD_MAX:.0f}°  AND dist <= {MATCH_FALLBACK_M:.0f} m")
    print(f"    low-skill (HARD / dir R²<0.3): r >= {HARD_HS_CORR} AND stability <= {HARD_DIR_STD:.0f}°")
    print(f"    no buoy + not HIGH-skill: FALL BACK")


# --------------------------------------------------------------------------- #
# --recover: re-cross-check the previously-UNVERIFIED spots against the nearest #
# ACTIVE NDBC buoy (recovers both class-(a) dead-station ids and class-(b) no-id #
# spots). Reuses the pipeline's active wave-station list for coords. Proposes a  #
# slug->buoy_id mapping for review; does NOT write the roster.                   #
# --------------------------------------------------------------------------- #
def _load_active_buoys():
    """[{id,lat,lng,name}] of NDBC stations CURRENTLY reporting WVHT — reuses the
    pipeline's loader (activestations.xml + latest_obs.txt). Lazy import so
    --selftest stays offline. Empty if the geodata files aren't present."""
    try:
        from pipeline.enrichment.geodata import load_ndbc_wave_stations
    except Exception as e:  # noqa: BLE001
        print(f"could not import buoy station loader: {e}", file=sys.stderr)
        return []
    return [b for b in load_ndbc_wave_stations()
            if isinstance(b.get("lat"), (int, float)) and isinstance(b.get("lng"), (int, float))]


def _nearest_active(active, lat, lng, k=8):
    """(buoy_id, dist_km) for the k nearest active wave buoys, nearest first."""
    ds = sorted((haversine_m(lat, lng, b["lat"], b["lng"]) / 1000.0, str(b["id"])) for b in active)
    return [(bid, round(dkm, 1)) for dkm, bid in ds[:k]]


def _recover_spot(s, cache, active, days=45, min_aligned=24):
    """Try the nearest active buoys (skipping the spot's known-dead id) until one
    has usable realtime2 data that aligns with the MOP nowcast window. Returns a
    recovery dict (recovered True/False + the new verdict + evidence)."""
    lat, lng, orient = s["lat"], s["lng"], float(s["orientation_deg"])
    zone = ca_zone(lat, lng) or "UNKNOWN"
    pid, meta, dist = _match(cache, lat, lng)
    raw_sn = meta.get("shore_normal")
    sn = raw_sn if raw_sn is not None else orient
    sn_delta = circ_offset(orient, raw_sn) if raw_sn is not None else None
    dead = (s.get("nearest_buoy_id") or "").lower()
    mop_url = _nowcast_url(meta["url"])
    for bid2, dkm in _nearest_active(active, lat, lng, k=8):
        if bid2.lower() == dead:
            continue
        series, _reason = buoy_series(bid2)
        if not series:
            continue
        bts = [t for t in (_norm_epoch(b[0]) for b in series) if t is not None]
        if not bts:
            continue
        t0, t1 = min(bts), max(bts)
        try:
            rows = pull_mop_window(mop_url, t0, t1)
            if not rows and mop_url != meta["url"]:
                rows = pull_mop_window(meta["url"], t0, t1)
        except (HTTPError, URLError, OSError):
            continue
        rows = [r for r in rows if r["tp"] and r["dp"] is not None]
        if not rows:
            continue
        hs_corr, dir_std, n_al = cross_check(rows, series)
        if n_al >= min_aligned:
            v, why = verdict(zone, ZONE_R2.get(zone), dist, hs_corr, dir_std, n_al, True, sn_delta)
            return {"recovered": True, "buoy_id": bid2, "buoy_dist_km": dkm, "n_aligned": n_al,
                    "hs_corr": round(hs_corr, 2), "dir_std": round(dir_std, 1),
                    "verdict": v, "reason": why}
    return {"recovered": False, "buoy_id": None,
            "reason": "no active buoy within range had usable realtime2 data aligning with MOP"}


def _recompute_meta(payload):
    rated = [r for r in payload["spots"] if not r.get("skipped")]
    consume = sum(1 for r in rated if r.get("consume"))
    by_zone = {}
    for r in rated:
        z = by_zone.setdefault(r["zone"], {"CONSUME": 0, "FALL BACK": 0})
        z["CONSUME" if r.get("consume") else "FALL BACK"] += 1
    m = payload.setdefault("_meta", {})
    m["consume"], m["fall_back"] = consume, len(rated) - consume
    m["no_buoy"] = sum(1 for r in rated if not r.get("has_buoy"))
    m["by_zone"], m["recovered_pass"] = by_zone, True


def run_recover(days=45):
    cache = load_cache()
    if cache is None:
        print("no scripts/mop_points.json cache — run on the Mac", file=sys.stderr)
        return 3
    if not os.path.exists(OUT):
        print(f"no {OUT} — run the full rollout first, then --recover", file=sys.stderr)
        return 2
    payload = json.load(open(OUT))
    active = _load_active_buoys()
    if not active:
        print("no active NDBC wave stations (need pipeline/geodata/ndbc_stations.xml + "
              "ndbc_latest_obs.txt) — run on the Mac after download_geodata.sh", file=sys.stderr)
        return 3
    roster = {_slug(s["name"]): s for s in json.load(open(ROSTER))}
    unverified = [r for r in payload["spots"] if not r.get("skipped") and not r.get("has_buoy")]
    consume_before = sum(1 for r in payload["spots"] if not r.get("skipped") and r.get("consume"))
    print(f"active NDBC wave stations: {len(active)}")
    print(f"unverified rated spots to recover: {len(unverified)}\n")

    proposed, rows_out = {}, []
    for i, r in enumerate(unverified, 1):
        s = roster.get(r["slug"])
        if not s:
            print(f"  [{i:>2}/{len(unverified)}] {r['slug']:28} (not in roster — skip)")
            continue
        old_v, old_buoy = r["verdict"], (s.get("nearest_buoy_id") or "none")
        rec = _recover_spot(s, cache, active, days)
        if rec["recovered"]:
            r.update(buoy_id=rec["buoy_id"], has_buoy=True, n_aligned=rec["n_aligned"],
                     hs_corr=rec["hs_corr"], dir_std=rec["dir_std"], verdict=rec["verdict"],
                     consume=rec["verdict"].startswith("CONSUME"), reason=rec["reason"],
                     buoy_reason=f"recovered {rec['buoy_id']} @ {rec['buoy_dist_km']}km, {rec['n_aligned']} aligned")
            proposed[r["slug"]] = {"buoy_id": rec["buoy_id"], "dist_km": rec["buoy_dist_km"],
                                   "prev_buoy_id": s.get("nearest_buoy_id"), "source": "nearest_active_ndbc",
                                   "n_aligned": rec["n_aligned"], "hs_corr": rec["hs_corr"],
                                   "verdict_after": rec["verdict"]}
        new_v = r["verdict"]
        rows_out.append((r["slug"], r["zone"], old_buoy, rec.get("buoy_id") or "—", old_v, new_v,
                         rec.get("n_aligned"), rec.get("hs_corr"), rec.get("dir_std"), rec["recovered"]))
        tail = (f"via {rec['buoy_id']} @ {rec['buoy_dist_km']}km ({rec['n_aligned']} al, r={rec['hs_corr']})"
                if rec["recovered"] else "still unverified")
        print(f"  [{i:>2}/{len(unverified)}] {r['slug']:28} {old_v:18} -> {new_v:18}  {tail}")

    _recompute_meta(payload)
    json.dump(payload, open(OUT, "w"), indent=2, ensure_ascii=False)
    json.dump({"_comment": "Proposed nearest-ACTIVE-buoy assignments for spots that had no usable "
                           "cross-check (class a: dead/decommissioned id; class b: no id in roster). "
                           "Review before writing to the roster; NOT applied to prod.",
               "assignments": proposed},
              open(MAPPING_OUT, "w"), indent=2, ensure_ascii=False)

    consume_after = sum(1 for r in payload["spots"] if not r.get("skipped") and r.get("consume"))
    recovered = sum(1 for x in rows_out if x[9])
    flipped = [x for x in rows_out if x[4] != x[5]]
    print("\n" + "=" * 96 + "\nRECOVERY — before → after (the previously-unverified spots)")
    print(f"  {'slug':28}{'zone':8}{'old buoy':>9}{'new buoy':>9}  {'before':18} {'after':18}{'r':>6}{'dirSD':>6}")
    for slug, zone, ob, nb, ov, nv, n, r_, ds, ok in rows_out:
        rs = "  -- " if r_ is None else f"{r_:5.2f}"
        dss = "  --" if ds is None else f"{ds:4.0f}"
        print(f"  {slug:28}{zone:8}{ob:>9}{nb:>9}  {ov:18} {nv:18}{rs:>6}{dss:>6}")
    print("\n" + "=" * 96)
    print(f"recovered a working buoy for {recovered}/{len(unverified)} previously-unverified spots")
    print(f"verdict changed for {len(flipped)}:")
    for slug, zone, ob, nb, ov, nv, *_ in flipped:
        print(f"    {slug:28} {ov:18} -> {nv}")
    print(f"\nCONSUME: {consume_before} -> {consume_after}   (buoy-backed after recovery)")
    print(f"wrote {OUT}")
    print(f"wrote {MAPPING_OUT}  (proposed slug→buoy_id mapping — review before touching the roster)")
    return 0


def diagnose_align(slug_arg):
    """One-spot MOP↔buoy alignment dump: buoy span, MOP-in-window span, the join,
    and the aligned-pair count — confirms the windows now overlap after the
    hindcast→nowcast + buoy-window fix."""
    cache = load_cache()
    if cache is None:
        print("no scripts/mop_points.json cache — run on the Mac", file=sys.stderr)
        return 3
    spots = json.load(open(ROSTER))
    hit = [s for s in spots if _slug(s["name"]) == slug_arg]
    if not hit:
        print(f"no roster spot with slug {slug_arg!r}", file=sys.stderr)
        return 2
    s = hit[0]
    print(f"spot: {s['name']} ({slug_arg})  ({s['lat']:.4f},{s['lng']:.4f})  buoy={s.get('nearest_buoy_id')}\n")

    def span(ts):
        ts = sorted(t for t in (_norm_epoch(x) for x in ts) if t is not None)
        if not ts:
            return "(no valid timestamps)"
        f = lambda x: datetime.datetime.utcfromtimestamp(x).strftime("%Y-%m-%d %H:%M")
        return f"{f(ts[0])} .. {f(ts[-1])} UTC  ({len(ts)} pts)"

    pid, meta, dist = _match(cache, s["lat"], s["lng"])
    cached_url, mop_url = meta["url"], _nowcast_url(meta["url"])
    print(f"MOP point {pid} @ {dist:.0f} m")
    print(f"  cached url: {cached_url}")
    print(f"  pull  url: {mop_url}" + ("   (swapped hindcast→nowcast)" if mop_url != cached_url else ""))

    series, reason = buoy_series(s.get("nearest_buoy_id"))
    if not series:
        print(f"\nbuoy: NONE ({reason}) — fix the fetch first")
        return 0
    bts = [t for t in (_norm_epoch(b[0]) for b in series) if t is not None]
    t0, t1 = min(bts), max(bts)
    print(f"\nbuoy series: {len(series)} rows ({reason})")
    print(f"  span: {span([b[0] for b in series])}")
    print(f"  → MOP pull window = buoy span")

    try:
        rows = pull_mop_window(mop_url, t0, t1)
        if not rows and mop_url != cached_url:
            print("  nowcast empty in window → falling back to cached (hindcast) flavor")
            rows = pull_mop_window(cached_url, t0, t1)
    except (HTTPError, URLError, OSError) as e:
        _egress_or_die(e)
        return 2
    rows = [r for r in rows if r.get("tp") and r.get("dp") is not None]
    print(f"\nMOP series (in buoy window): {len(rows)} rows")
    print(f"  span: {span([r['t'] for r in rows])}")

    hs_corr, dir_std, n_al = cross_check(rows, series)
    print("\nmatch method: UTC hour-bucket floor(epoch/3600), ±1h, epochs normalized (s/ms, fill-rejected)")
    print(f"  aligned pairs: {n_al}")
    if n_al:
        print(f"  → Hs r={hs_corr:.2f}, dir_std={dir_std:.0f}°  ✓ windows overlap, alignment works")
    else:
        print("  → 0 aligned. If MOP-in-window is empty, the nowcast doesn't cover the buoy span "
              "(rebuild the cache for current URLs); otherwise compare the two spans above.")
    return 0


def diagnose_buoy(station_id):
    """One-station raw dump: fetch .txt and .spec, show first lines + parsed
    (t,hs,dp) count + time span, so we can SEE where the cross-check breaks."""
    print(f"NDBC realtime2 diagnostic — station {station_id}\n")
    any_ok = False
    for label, fmap in (("txt", _STD_FIELDS), ("spec", _SPEC_FIELDS)):
        url = f"{NDBC_REALTIME2_BASE}/{station_id.upper()}.{label}"
        print(f"--- {url} ---")
        text = _fetch_realtime2(station_id, label)
        if not text:
            print("  FETCH: failed / 404 / HTML error page → None\n")
            continue
        lines = text.splitlines()
        print(f"  FETCH OK: {len(text)} bytes, {len(lines)} lines. First 4:")
        for ln in lines[:4]:
            print(f"    {ln}")
        obs = _parse_realtime2(text, fmap)
        ser = _series_from_text(text, fmap)
        if ser:
            any_ok = True
            t0 = datetime.datetime.utcfromtimestamp(ser[0][0]).strftime("%Y-%m-%d %H:%M")
            t1 = datetime.datetime.utcfromtimestamp(ser[-1][0]).strftime("%Y-%m-%d %H:%M")
            print(f"  parsed {len(obs)} obs → {len(ser)} usable (t,hs,dp); span {t0} .. {t1} UTC")
            print(f"  latest (t,hs,dp): {ser[-1]}")
        else:
            print(f"  parsed {len(obs)} obs → 0 usable (t,hs,dp) — WVHT/MWD missing in this file")
        print()
    series, reason = buoy_series(station_id)
    print(f"buoy_series() → {'OK, ' + str(len(series)) + ' rows' if series else 'None'}  ({reason})")
    if not any_ok:
        print("\nNo usable rows from either file. If FETCH failed here but works in a browser, "
              "this host may be egress-blocked in this environment — run on the Mac.")
    return 0


# --------------------------------------------------------------------------- #
def run_selftest():
    ok = True
    def check(n, c):
        nonlocal ok; ok = ok and c; print(f"  {'PASS' if c else 'FAIL'}  {n}")

    # zone classifier reproduces the handful's hand-set zones
    check("ca_zone Blacks -> HIGH", ca_zone(32.88, -117.25) == "HIGH")
    check("ca_zone Malibu -> MEDIUM", ca_zone(34.03, -118.69) == "MEDIUM")
    check("ca_zone Rincon -> HARD", ca_zone(34.37, -119.48) == "HARD")
    check("ca_zone OB SF -> UNKNOWN", ca_zone(37.75, -122.51) == "UNKNOWN")
    check("slug", _slug("Lower Trestles") == "lower-trestles")

    # reused adoption rule behaves (imported from the handful)
    v1, _ = verdict("HIGH", 0.9, 458, 0.89, 12, 600, True, 8)
    v2, _ = verdict("UNKNOWN", None, 200, float("nan"), float("nan"), 0, False, 5)
    v3, _ = verdict("MEDIUM", 0.6, 300, 0.85, 20, 600, True, 70)
    check(f"HIGH+close+good -> CONSUME ({v1})", v1.startswith("CONSUME"))
    check(f"no buoy + not HIGH -> FALL BACK ({v2})", v2 == "FALL BACK")
    check(f"shore-normal mismatch Δ70 -> FALL BACK ({v3})", v3 == "FALL BACK")

    # NDBC .txt parsing (the PRIMARY source now: WVHT + MWD) -> (t,hs,dp) series
    std = ("#YY  MM DD hh mm WDIR WSPD GST WVHT  DPD APD MWD   PRES ATMP WTMP DEWP VIS PTDY TIDE\n"
           "#yr  mo dy hr mn degT  m/s m/s    m  sec sec degT   hPa degC degC degC nmi  hPa   ft\n"
           "2026 06 27 12 00  280  5.0 6.0  1.5 14.0 9.0 285 1015.0 15.0 14.0 10.0  MM   MM   MM\n"
           "2026 06 27 11 00  280  5.0 6.0  1.6 13.0 9.0 290 1015.0 15.0 14.0 10.0  MM   MM   MM\n")
    ser_txt = _series_from_text(std, _STD_FIELDS)
    check(f"NDBC .txt -> 2 (t,hs,dp) rows ({len(ser_txt)})", len(ser_txt) == 2)
    check("NDBC .txt parses WVHT/MWD", {round(r[1], 1) for r in ser_txt} == {1.5, 1.6}
          and {round(r[2]) for r in ser_txt} == {285, 290})

    # .spec fallback parsing, with a missing-value (MM) row dropped
    spec = ("#YY  MM DD hh mm WVHT  SwH  SwP  WWH  WWP SwD WWD  STEEPNESS  APD MWD\n"
            "#yr  mo dy hr mn    m    m  sec    m  sec   -   -          -  sec degT\n"
            "2026 06 27 12 00  1.5  1.2 14.0  0.5  5.0 WNW   W    AVERAGE  9.0 280\n"
            "2026 06 27 11 00  1.6  1.3 13.0  0.4  4.0 WNW   W    AVERAGE  9.5 285\n"
            "2026 06 27 10 00   MM  1.1 12.0  0.3  4.0 WNW   W    AVERAGE  9.0  MM\n")
    ser_spec = _series_from_text(spec, _SPEC_FIELDS)
    check(f"NDBC .spec -> 2 usable rows (MM dropped) ({len(ser_spec)})", len(ser_spec) == 2)
    check("buoy_series no-id -> (None, reason)", buoy_series(None) == (None, "no buoy id in roster"))

    # epoch normalization (the alignment fix): ms->s, fill rejected, sane sec kept
    base = 1767225600  # 2026-01-01 00:00 UTC
    check("norm_epoch ms->s", _norm_epoch(base * 1000) == base)
    check("norm_epoch keeps sane sec", _norm_epoch(base) == base)
    check("norm_epoch rejects fill (9.96e36)", _norm_epoch(9.969e36) is None)

    # cross_check: realistic epochs, buoy SUB-HOURLY and in MILLISECONDS — must
    # still hour-bucket and align to the hourly MOP series.
    hsv = [1.0, 1.5, 2.2, 1.8, 1.1, 2.5, 0.9, 1.3]
    rows = [{"t": base + i * 3600, "hs": h, "tp": 14, "dp": 280, "swell_hs": h} for i, h in enumerate(hsv)]
    buoy_ms = []
    for i, h in enumerate(hsv):
        buoy_ms.append(((base + i * 3600) * 1000, h * 1.05, 260))         # on the hour, ms
        buoy_ms.append(((base + i * 3600 + 1800) * 1000, h * 1.05, 261))  # :30 past, ms
    hc, ds, n = cross_check(rows, buoy_ms)
    check(f"cross_check aligns ms+sub-hourly buoy (r={hc:.2f} ds={ds:.0f}° n={n})", hc > 0.95 and ds < 5 and n == 8)
    _, _, nfill = cross_check([{"t": 9.969e36, "hs": 2.0, "tp": 14, "dp": 280, "swell_hs": 2.0}], buoy_ms)
    check(f"fill-time MOP row not aligned to garbage (n={nfill})", nfill == 0)

    # the window fix: hindcast→nowcast url swap + MOP pull restricted to [t0,t1]
    check("nowcast url swap",
          _nowcast_url("https://x/cdip/model/MOP_alongshore/D0045_hindcast.nc")
          == "https://x/cdip/model/MOP_alongshore/D0045_nowcast.nc")
    check("nowcast url left alone", _nowcast_url("https://x/D0045_nowcast.nc") == "https://x/D0045_nowcast.nc")
    # _rows_in_window keeps only in-[t0,t1] real rows: drops a pre-window row, a
    # forecast row (t>t1) and a fill row (9.96e36).
    tw = np.array([base - 3600, base, base + 3600, base + 2 * 3600, base + 5 * 3600,
                   base + 10 * 3600, 9.969e36])
    hw = np.array([0.9, 1.0, 1.1, 1.2, 1.3, 9.9, 9.9])
    wrows = _rows_in_window(tw, None, hw, hw, hw, None, base, base + 5 * 3600)
    check(f"_rows_in_window keeps only in-window real rows (n={len(wrows)})", len(wrows) == 4)
    check("_rows_in_window excludes forecast+fill", all(base <= r["t"] <= base + 5 * 3600 for r in wrows))

    # recovery helpers: nearest-active ordering + meta recompute
    stns = [{"id": "46258", "lat": 32.75, "lng": -117.50, "name": "Mission Bay"},
            {"id": "46254", "lat": 32.87, "lng": -117.26, "name": "Scripps Nearshore"},
            {"id": "46086", "lat": 32.50, "lng": -118.00, "name": "far"}]
    near = _nearest_active(stns, 32.87, -117.25, k=2)
    check(f"_nearest_active orders by distance ({[b for b,_ in near]})", [b for b, _ in near] == ["46254", "46258"])
    pay = {"spots": [{"zone": "HIGH", "consume": True, "has_buoy": True, "skipped": False},
                     {"zone": "HIGH", "consume": False, "has_buoy": False, "skipped": False},
                     {"zone": "HARD", "consume": True, "has_buoy": True, "skipped": False},
                     {"zone": "X", "skipped": True}]}
    _recompute_meta(pay)
    check("_recompute_meta consume/no_buoy",
          pay["_meta"]["consume"] == 2 and pay["_meta"]["fall_back"] == 1 and pay["_meta"]["no_buoy"] == 1)

    print("\nself-test:", "ALL PASS — zone, reused rule, NDBC parse, and cross-check sound."
          if ok else "FAILURES above")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--limit", type=int, default=None, help="rate only the first N CA spots (smoke test)")
    ap.add_argument("--diagnose-buoy", metavar="ID", default=None,
                    help="dump one NDBC station's raw fetch + parsed (t,hs,dp) counts and exit")
    ap.add_argument("--diagnose-align", metavar="SLUG", default=None,
                    help="dump MOP vs buoy series spans + join + aligned-pair count for one spot and exit")
    ap.add_argument("--recover", action="store_true",
                    help="re-cross-check the previously-unverified spots in mop_ca_verdicts.json against the "
                         "nearest ACTIVE buoy; update verdicts + propose a slug→buoy_id mapping (no roster write)")
    a = ap.parse_args(argv)
    if a.selftest:
        return run_selftest()
    if a.diagnose_buoy:
        return diagnose_buoy(a.diagnose_buoy)
    if a.diagnose_align:
        return diagnose_align(a.diagnose_align)
    if a.recover:
        return run_recover(a.days)
    return run(a.days, a.limit)


if __name__ == "__main__":
    raise SystemExit(main())
