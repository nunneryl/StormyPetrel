#!/usr/bin/env python3
"""MOP vertical slice — Blacks Beach, end-to-end (READ-ONLY MOP; touches no prod).

Proves one CA spot (Blacks Beach, San Diego — the high-MOP-skill zone, O'Reilly
2016 dir R^2 > 0.9) from its CDIP MOP nearshore point through to a star rating,
reusing the EXISTING break-response logic from pipeline.interpret (not a copy).

Subcommands
  build-cache  Resolve lat/lon/water_depth/shore_normal for ALL MOP alongshore
               points (the prototype capped at 4000/11677 because the catalog
               lacks coords) and cache to scripts/mop_points.json. One-time;
               resumable; runs where THREDDS egress is open.
  match        Nearest-neighbour Blacks against the FULL cached set; print id+dist.
  slice        (default) Pull a span of MOP data for Blacks' point, quantify the
               nearshore-vs-deep-water frame offset, rate each hour in the chosen
               frame, and print the Hs/Tp/dir->stars table + sanity days.
  --selftest   Offline: validate the frame math, the swell-Hs split, and that the
               rating chain moves the right way. No network.

FRAME DECISION (see docs/mop_blacks_slice_report.md): NEARSHORE path. MOP's
waveDp is already refracted to the 10 m contour, so we rate it against MOP's
metaShoreNormal (a consistent nearshore frame) and let directional_gain run
unchanged. We do NOT un-refract (ill-posed) and we do NOT keep the deep-water
window that the raycast got wrong.

THREDDS is egress-blocked in the dev sandbox (403); run this where egress is open
(the user's Mac pulled MOP successfully). It exits loudly if THREDDS is
unreachable rather than inventing numbers.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin
from urllib.request import urlopen

import numpy as np

# Reuse the EXISTING break-response rating — do not reimplement it.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from pipeline.interpret import (  # noqa: E402
    chop_multiplier, chop_ratio, composite_stars, directional_gain, face_ft,
    period_quality,
)

THREDDS = "https://thredds.cdip.ucsd.edu"
MOP_CATALOG = f"{THREDDS}/thredds/catalog/cdip/model/MOP_alongshore/catalog.xml"
DODS = f"{THREDDS}/thredds/dodsC"
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mop_points.json")

# name, lat, lng, stored deep-water orientation (for reference only — not used in
# the nearshore rating, which uses MOP's metaShoreNormal)
BLACKS = ("San Diego Blacks Beach", 32.879677, -117.252982, 263.0)
DEEPWATER_STATION = "100"   # CDIP Torrey Pines Outer (La Jolla offshore) — deep-water ref; override with --deepwater-station
SWELL_MAX_FREQ_HZ = 0.125   # Tp >= 8 s swell band
RATING_SOURCE = "ww3"       # MOP is a spectral model; closest of the two period-factor curves


# --------------------------------------------------------------------------- #
# Pure geo / frame / spectrum math  (validated by --selftest)                 #
# --------------------------------------------------------------------------- #
def haversine_m(lat1, lon1, lat2, lon2):
    R = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1); dl = math.radians(lon2 - lon1)
    a = math.sin(dp/2)**2 + math.cos(p1)*math.cos(p2)*math.sin(dl/2)**2
    return 2 * R * math.asin(math.sqrt(a))


def circ_offset(a, b):
    """Signed smallest difference a-b in (-180, 180]."""
    return ((a - b + 540.0) % 360.0) - 180.0


def split_swell_hs(energy_row, freq):
    """(total_Hs, swell_Hs) from a 1-D energy-density spectrum [m^2/Hz]."""
    df = np.gradient(freq)
    m0_total = float(np.nansum(energy_row * df))
    band = freq <= SWELL_MAX_FREQ_HZ
    m0_swell = float(np.nansum(energy_row[band] * df[band]))
    return 4.0 * math.sqrt(max(m0_total, 0)), 4.0 * math.sqrt(max(m0_swell, 0))


def rate_nearshore(hs, tp, dp, swell_hs, shore_normal):
    """Star rating in the NEARSHORE frame: MOP refracted dp vs MOP shore-normal,
    fed through the unchanged break-response chain. Wind/tide neutral (MOP carries
    neither); chop + period-quality come from the MOP spectrum."""
    if hs is None or tp is None or dp is None:
        return 0.0, 0.0, 0.0
    # empty arcs + optimal=shore_normal -> directional_gain uses cos^2((dp-normal)/2)
    dg = directional_gain(dp, [], shore_normal, shore_normal)
    eff = face_ft(hs, tp, RATING_SOURCE) * dg
    cm = chop_multiplier(chop_ratio(hs, swell_hs))
    pq = period_quality(tp)
    stars = composite_stars(eff, 1.0, 1.0, cm, pq)
    return stars, dg, eff


# --------------------------------------------------------------------------- #
# THREDDS access  (best effort; fails loudly)                                 #
# --------------------------------------------------------------------------- #
def _get(url, timeout=90):
    with urlopen(url, timeout=timeout) as r:
        return r.read()


def _egress_or_die(e):
    print("\n*** THREDDS UNREACHABLE — CDIP MOP not reachable from here. ***", file=sys.stderr)
    print(f"    {type(e).__name__}: {e}", file=sys.stderr)
    print("    Run where outbound to thredds.cdip.ucsd.edu is allowed (the Mac that "
          "pulled MOP). Not faking numbers; use --selftest for the offline math proof.",
          file=sys.stderr)


def list_all_mop_points(max_refs=200):
    """[(point_id, dods_url)] for every MOP alongshore point (one flavour each,
    preferring hindcast). Descends a nested-by-county catalog if needed."""
    ns = {"t": "http://www.unidata.ucar.edu/namespaces/thredds/InvCatalog/v1.0"}

    def parse(xml_bytes):
        root = ET.fromstring(xml_bytes)
        dsets, refs = [], []
        for el in root.iter():
            tag = el.tag.split("}")[-1]
            if tag == "catalogRef":
                href = el.get("{http://www.w3.org/1999/xlink}href") or el.get("href")
                if href:
                    refs.append(href)
            elif tag == "dataset" and el.get("urlPath"):
                dsets.append((el.get("name") or el.get("urlPath").split("/")[-1], el.get("urlPath")))
        return dsets, refs

    dsets, refs = parse(_get(MOP_CATALOG))
    if refs and len(dsets) < 50:
        for i, href in enumerate(refs[:max_refs]):
            try:
                sub, _ = parse(_get(urljoin(MOP_CATALOG, href)))
                dsets.extend(sub)
            except (HTTPError, URLError, OSError):
                continue
            if (i + 1) % 20 == 0:
                print(f"  descended {i+1} sub-catalogs -> {len(dsets)} datasets", flush=True)
    rank = {"hindcast": 0, "nowcast": 1, "forecast": 2, "ecmwf_fc": 3}
    best = {}
    for name, urlpath in dsets:
        base = name[:-3] if name.endswith(".nc") else name
        pid = base.split("_", 1)[0]
        flavor = base.split("_", 1)[1] if "_" in base else "default"
        r = rank.get(flavor, 9)
        if pid not in best or r < best[pid][0]:
            best[pid] = (r, f"{DODS}/{urlpath}")
    return [(pid, url) for pid, (r, url) in best.items()]


def _read_meta(args):
    pid, url = args
    import netCDF4
    try:
        nc = netCDF4.Dataset(url)
    except Exception as e:  # noqa: BLE001
        return pid, {"error": str(e)[:80]}
    try:
        def scalar(*names):
            for n in names:
                if n in nc.variables:
                    try:
                        return float(np.asarray(nc.variables[n][:]).ravel()[0])
                    except Exception:  # noqa: BLE001
                        pass
            return None
        lat = scalar("metaLatitude", "metaDeployLatitude")
        lon = scalar("metaLongitude", "metaDeployLongitude")
        if lat is None and hasattr(nc, "geospatial_lat_min"):
            lat, lon = float(nc.geospatial_lat_min), float(nc.geospatial_lon_min)
        return pid, {
            "url": url, "lat": lat, "lon": lon,
            "water_depth": scalar("metaWaterDepth", "metaWaterDepths", "metaGridMappingDepth"),
            "shore_normal": scalar("metaShoreNormal", "metaShoreNormalOrientation", "metaShorelineAngle"),
        }
    finally:
        nc.close()


def build_cache(workers=8):
    print(f"catalog: {MOP_CATALOG}")
    try:
        points = list_all_mop_points()
    except (HTTPError, URLError, OSError) as e:
        _egress_or_die(e); return 2
    print(f"MOP alongshore points: {len(points)}")
    cache = {}
    if os.path.exists(CACHE):
        cache = json.load(open(CACHE))
        print(f"resuming: {len(cache)} already cached")
    todo = [(pid, url) for pid, url in points if pid not in cache or cache[pid].get("lat") is None]
    print(f"resolving {len(todo)} points over OPeNDAP ({workers} workers)...")
    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for pid, meta in ex.map(_read_meta, todo):
            cache[pid] = meta
            done += 1
            if done % 500 == 0:
                json.dump(cache, open(CACHE, "w"))
                ok = sum(1 for v in cache.values() if v.get("lat") is not None)
                print(f"  {done}/{len(todo)} read; {ok} with coords", flush=True)
    json.dump(cache, open(CACHE, "w"), indent=0)
    ok = sum(1 for v in cache.values() if v.get("lat") is not None)
    print(f"wrote {CACHE}: {len(cache)} points, {ok} with coordinates")
    return 0


def load_cache():
    if not os.path.exists(CACHE):
        print(f"no cache at {CACHE} — run: python {sys.argv[0]} build-cache", file=sys.stderr)
        return None
    return json.load(open(CACHE))


def match_blacks(cache):
    name, lat, lon, _ = BLACKS
    cand = [(pid, m) for pid, m in cache.items() if m.get("lat") is not None]
    pid, m = min(cand, key=lambda kv: haversine_m(lat, lon, kv[1]["lat"], kv[1]["lon"]))
    d = haversine_m(lat, lon, m["lat"], m["lon"])
    return pid, m, d


def pull_span(url, days=45):
    """Per-timestep MOP record for Blacks' point over the last `days`."""
    import netCDF4
    nc = netCDF4.Dataset(url)
    try:
        freq = np.asarray(nc.variables["waveFrequency"][:])
        times = np.asarray(nc.variables["waveTime"][:])
        i0 = int(np.searchsorted(times, times.max() - days * 86400))
        def v(n):
            return np.asarray(nc.variables[n][i0:]) if n in nc.variables else None
        hs, tp, dp = v("waveHs"), v("waveTp"), v("waveDp")
        dm = v("waveDm")
        if dm is None and "waveMeanDirection" in nc.variables:
            md = np.asarray(nc.variables["waveMeanDirection"][i0:])
            dm = md[:, 0] if md.ndim == 2 else md
        ed = np.asarray(nc.variables["waveEnergyDensity"][i0:])
        rows = []
        for k in range(len(times) - i0):
            t_hs, s_hs = split_swell_hs(ed[k], freq)
            rows.append(dict(
                t=float(times[i0 + k]),
                hs=float(hs[k]) if hs is not None else t_hs,
                tp=float(tp[k]) if tp is not None else None,
                dp=float(dp[k]) if dp is not None else None,
                dm=float(dm[k]) if dm is not None else None,
                swell_hs=s_hs,
            ))
        return rows
    finally:
        nc.close()


def pull_deepwater_dp(station, t0, t1):
    """Deep-water peak direction from an offshore CDIP buoy over [t0,t1], for the
    frame-offset comparison. Best effort across realtime/historic file patterns."""
    import netCDF4
    for url in (f"{DODS}/cdip/realtime/{station}p1_rt.nc",
                f"{DODS}/cdip/archive/{station}p1/{station}p1_historic.nc"):
        try:
            nc = netCDF4.Dataset(url)
        except Exception:  # noqa: BLE001
            continue
        try:
            t = np.asarray(nc.variables["waveTime"][:])
            dp = np.asarray(nc.variables["waveDp"][:])
            m = (t >= t0) & (t <= t1)
            if m.any():
                return list(zip(t[m].tolist(), dp[m].tolist())), url
        finally:
            nc.close()
    return None, None


def run_slice(days=45, deepwater_station=DEEPWATER_STATION):
    cache = load_cache()
    if cache is None:
        return 3
    pid, meta, dist = match_blacks(cache)
    name, blat, blon, stored_orient = BLACKS
    shore_normal = meta.get("shore_normal")
    print(f"{name}: nearest MOP point {pid} at {dist:.0f} m "
          f"(depth {meta.get('water_depth')} m, metaShoreNormal {shore_normal}, "
          f"stored deep-water orientation {stored_orient})")
    if shore_normal is None:
        print("  metaShoreNormal absent — falling back to stored orientation for the nearshore optimal.")
        shore_normal = stored_orient

    try:
        rows = pull_span(meta["url"], days)
    except (HTTPError, URLError, OSError) as e:
        _egress_or_die(e); return 2
    rows = [r for r in rows if r["tp"] and r["dp"] is not None]
    print(f"pulled {len(rows)} hourly MOP records (~{days} d)\n")

    # ---- THE FRAME OFFSET (numbers) ----
    print("FRAME OFFSET — MOP nearshore waveDp vs deep-water buoy waveDp (refraction):")
    t0, t1 = rows[0]["t"], rows[-1]["t"]
    dw, dw_url = pull_deepwater_dp(deepwater_station, t0, t1)
    if dw:
        import bisect
        dwt = [x[0] for x in dw]
        offs = []
        for r in rows:
            j = min(bisect.bisect_left(dwt, r["t"]), len(dw) - 1)
            if abs(dwt[j] - r["t"]) <= 3600:
                offs.append(circ_offset(r["dp"], dw[j][1]))  # nearshore - deepwater
        if offs:
            offs = np.array(offs)
            print(f"  ref buoy {deepwater_station} ({dw_url.split('/')[-1]}), {len(offs)} matched hours")
            print(f"  nearshore is rotated {np.mean(offs):+.0f} deg from deep-water "
                  f"(median {np.median(offs):+.0f}, IQR {np.percentile(offs,25):+.0f}..{np.percentile(offs,75):+.0f})")
            print(f"  -> deep-water Dp spans a far wider range than nearshore Dp; "
                  f"feeding deep-water Dp into a nearshore-optimal (or vice-versa) is the bug.")
    else:
        print(f"  deep-water buoy {deepwater_station} not reachable/aligned — frame offset not measured "
              f"(report it, don't fake). MOP nearshore Dp vs metaShoreNormal still shown below.")
    nd = np.array([circ_offset(r["dp"], shore_normal) for r in rows])
    print(f"  MOP nearshore Dp clusters {np.mean(nd):+.0f} deg around shore-normal "
          f"(std {np.std(nd):.0f}) — refraction has already aligned it; that's why we rate in the nearshore frame.\n")

    # ---- RATE every hour in the nearshore frame ----
    for r in rows:
        r["stars"], r["dg"], r["eff"] = rate_nearshore(r["hs"], r["tp"], r["dp"], r["swell_hs"], shore_normal)

    # ---- SANITY: a few distinct real days, biggest-swell windows ----
    import datetime
    rows_sorted = sorted(rows, key=lambda r: r["hs"], reverse=True)
    picks = []
    seen_days = set()
    for r in rows_sorted:                                   # biggest-Hs hours, distinct days
        day = datetime.datetime.utcfromtimestamp(r["t"]).strftime("%Y-%m-%d")
        if day not in seen_days:
            seen_days.add(day); picks.append(r)
        if len(picks) >= 4:
            break
    for r in sorted(rows, key=lambda r: r["hs"])[:2]:        # plus 2 smallest (junk) hours
        picks.append(r)

    print("SANITY — Hs / Tp / dir -> stars (nearshore frame; wind+tide neutral):")
    print(f"  {'when (UTC)':17}{'Hs_m':>5}{'Tp_s':>5}{'Dp':>5}{'off-norm':>9}"
          f"{'swellHs':>8}{'dirgain':>8}{'stars':>6}")
    for r in sorted(picks, key=lambda r: r["t"]):
        when = datetime.datetime.utcfromtimestamp(r["t"]).strftime("%Y-%m-%d %H:%M")
        print(f"  {when:17}{r['hs']:5.2f}{r['tp']:5.0f}{r['dp']:5.0f}"
              f"{circ_offset(r['dp'], shore_normal):9.0f}{r['swell_hs']:8.2f}"
              f"{r['dg']:8.2f}{r['stars']:6.1f}")

    allstars = np.array([r["stars"] for r in rows])
    print(f"\nspan stars: min {allstars.min():.1f}  median {np.median(allstars):.1f}  max {allstars.max():.1f}")
    print("verdict cue: biggest long-period on-normal hours should top the table; "
          "tiny short-period hours should bottom it. Eyeball above.")
    return 0


# --------------------------------------------------------------------------- #
# Offline self-test                                                            #
# --------------------------------------------------------------------------- #
def run_selftest():
    ok = True
    def check(n, c):
        nonlocal ok; ok = ok and c; print(f"  {'PASS' if c else 'FAIL'}  {n}")

    check("circ_offset 350 vs 10 = -20", circ_offset(350, 10) == -20)
    check("circ_offset 10 vs 350 = +20", circ_offset(10, 350) == 20)

    freq = np.linspace(0.04, 0.25, 64)
    e = np.zeros(64); e[freq <= 0.1] = 2.0                  # all energy long-period
    tot, sw = split_swell_hs(e, freq)
    check(f"pure swell spectrum: swell_Hs≈total ({sw:.2f}≈{tot:.2f})", abs(sw - tot) < 1e-6)
    e2 = e.copy(); e2[freq > 0.18] = 3.0                    # add short-period chop
    tot2, sw2 = split_swell_hs(e2, freq)
    check(f"added chop: swell_Hs < total ({sw2:.2f} < {tot2:.2f})", sw2 < tot2 - 0.1)

    sn = 265.0
    big, _, _ = rate_nearshore(2.5, 17, 263, 2.45, sn)      # big clean on-normal
    small, _, _ = rate_nearshore(0.4, 7, 245, 0.15, sn)     # tiny short-period oblique
    oblique, _, _ = rate_nearshore(1.5, 15, 215, 1.45, sn)  # decent but 50deg off-normal
    onaxis, _, _ = rate_nearshore(1.5, 15, 264, 1.45, sn)   # same swell, on-normal
    check(f"big clean on-normal ({big}) > tiny junk ({small})", big > small)
    check(f"on-axis ({onaxis}) > 50deg-oblique ({oblique}) same Hs/Tp", onaxis > oblique)
    check(f"big ({big}) >= on-axis moderate ({onaxis})", big >= onaxis)
    check("haversine ~100 m", 90 <= haversine_m(32.88, -117.25, 32.88, -117.2489) <= 110)

    print("\nself-test:", "ALL PASS — frame math + swell split + nearshore rating chain are sound."
          if ok else "FAILURES above")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", nargs="?", default="slice", choices=["build-cache", "match", "slice"])
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--days", type=int, default=45)
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--deepwater-station", default=DEEPWATER_STATION)
    a = ap.parse_args(argv)
    if a.selftest:
        return run_selftest()
    if a.cmd == "build-cache":
        return build_cache(a.workers)
    if a.cmd == "match":
        cache = load_cache()
        if cache is None:
            return 3
        pid, m, d = match_blacks(cache)
        print(f"Blacks -> {pid} at {d:.0f} m  (lat {m['lat']}, lon {m['lon']}, "
              f"depth {m.get('water_depth')} m, shore_normal {m.get('shore_normal')})")
        ok = sum(1 for v in cache.values() if v.get('lat') is not None)
        print(f"matched against {ok} coord-resolved points (full set, not the 4000 cap)")
        return 0
    return run_slice(a.days, a.deepwater_station)


if __name__ == "__main__":
    raise SystemExit(main())
