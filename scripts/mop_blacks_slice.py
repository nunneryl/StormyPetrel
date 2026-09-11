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

CACHE SHAPE — AN ABSENT SCALAR IS WRITTEN AS null, THE KEY IS NEVER OMITTED.
Every point carries all of lat/lon/water_depth/shore_normal, with null where CDIP
has no value. Three reasons this beats dropping the key:

  1. It is already the schema. meta_scalar returns None for a variable that is
     absent, unreadable or masked, and json.dump writes that as null, so nothing
     downstream needs teaching and no existing reader changes.
  2. null distinguishes "the builder looked and CDIP had nothing" from "this
     cache predates the field existing". An omitted key conflates them, and this
     cache is gitignored — it is routinely rebuilt by whatever copy of this file
     a given machine happens to have. The same conflation bit db_import's
     preserve-merge, where absent keys were refilled from the database row while
     explicit nulls were honoured.
  3. `.get(field)` returns None either way, so an omitted key buys no safety at
     the read side while costing the distinction above.

`absent` carries the REASON per field (masked / absent / unreadable / empty /
non-finite) and is written only when something is missing, so a healthy point
keeps the shape it has always had. The read side already declines None:
mop_face_validation.shore_normal_delta returns None for a None normal and
match_verdict rejects with "no orientation_deg or no metaShoreNormal to compare",
which is the honest outcome — the spot loses MOP eligibility and keeps its NWPS
face. mop_handful_slice documents its own reader the same way ("or None if the
point has no shore-normal").
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


def meta_scalar(nc, *names):
    """(value, status) for the first of *names* that yields a real number, else (None, why).

    A MASKED VALUE IS ABSENT, NOT A NUMBER. This used to read

        float(np.asarray(nc.variables[n][:]).ravel()[0])

    and np.asarray() DISCARDS a numpy mask, exposing the raw fill underneath with no
    warning and no exception. netCDF4 masks an element precisely to say "there is no value
    here", so that call turned CDIP's "unknown" into a confident number. Reading
    float(arr[0]) directly would at least have produced nan and a UserWarning; np.asarray
    produced a plausible-looking bearing instead.

    IT HAD FIRED. 129 of the 11,677 points in the MOP cache carried shore_normal exactly
    0.0 — 1.1% — while the other 11,548 spanned 0.56 to 359.21 at full float precision.
    Queried against THREDDS directly, D0930 / OC643 / OC642 / SF071 all report the variable
    PRESENT and its data MASKED: CDIP simply has no shore normal for those points. The
    zeros were this function inventing one. Four spots (oceanside-harbor, seal-beach-pier,
    seal-beach-california, surfside-jetty) were rejected by the face harness on a
    shore-normal delta that was really the circular distance from their own orientation to
    the number zero.

    The 129 are scattered across nine region prefixes — F 81, L 12, MO 9, SF 9, HU 8, D 6,
    OC 2, MA 1, VE 1 — so this was never one bad batch to re-fetch.

    DO NOT DERIVE A SHORE NORMAL FOR THESE POINTS FROM THEIR NEIGHBOURS, however tempting
    the alongshore geometry makes it look. Neighbouring MOP points are 100 m apart along a
    coastline that bends, and a run of 81 consecutive missing points spans kilometres; an
    interpolated bearing would be a number we made up, wearing the same field name as a
    number CDIP measured, with nothing downstream able to tell them apart. The honest cost
    is that the four affected spots lose MOP eligibility. Pay it.

    A masked first alias falls through to the next name rather than giving up: the three
    names are alternative spellings of one quantity, and a file may carry a value under the
    second when the first is masked.

    Status is one of ok / masked / absent / unreadable / empty / non-finite, so the caller
    can record WHY a field is missing rather than only that it is.
    """
    why = "absent"
    for n in names:
        if n not in getattr(nc, "variables", {}):
            continue
        try:
            raw = nc.variables[n][:]
        except Exception:  # noqa: BLE001
            why = "unreadable"
            continue
        # getdata/getmaskarray work on a plain ndarray too (mask reads all-False), so this
        # does not assume netCDF4 handed back a MaskedArray, and it handles the 0-d scalar
        # case and the np.ma.masked singleton without special-casing either.
        data = np.ma.getdata(raw).ravel()
        mask = np.ma.getmaskarray(raw).ravel()
        if data.size == 0:
            why = "empty"
            continue
        if bool(mask[0]):
            why = "masked"
            continue
        value = float(data[0])
        if not math.isfinite(value):
            why = "non-finite"
            continue
        return value, "ok"
    return None, why


def _read_meta(args):
    pid, url = args
    import netCDF4
    try:
        nc = netCDF4.Dataset(url)
    except Exception as e:  # noqa: BLE001
        return pid, {"error": str(e)[:80]}
    try:
        lat, lat_why = meta_scalar(nc, "metaLatitude", "metaDeployLatitude")
        lon, lon_why = meta_scalar(nc, "metaLongitude", "metaDeployLongitude")
        if lat is None and hasattr(nc, "geospatial_lat_min"):
            # Reached on a MASKED latitude now, not only a missing one. The old test was
            # `lat is None`, and a masked lat arrived as 0.0 rather than None, so the one
            # recovery path in this function was disabled by exactly the failure it exists
            # to catch. water_depth came back clean across all 11,677 points, so this has
            # not fired on coordinates — but the mechanism was identical.
            lat, lon = float(nc.geospatial_lat_min), float(nc.geospatial_lon_min)
            lat_why = lon_why = "ok (geospatial_lat_min fallback)"
        depth, depth_why = meta_scalar(nc, "metaWaterDepth", "metaWaterDepths",
                                       "metaGridMappingDepth")
        normal, normal_why = meta_scalar(nc, "metaShoreNormal", "metaShoreNormalOrientation",
                                         "metaShorelineAngle")
        meta = {
            "url": url, "lat": lat, "lon": lon,
            "water_depth": depth,
            # PRESENT AND NULL, never omitted — see the module docstring on cache shape.
            "shore_normal": normal,
        }
        # Why each missing field is missing, recorded only when something IS missing so a
        # healthy point keeps the shape it has always had and the cache does not churn.
        absent = {k: w for k, w in (("lat", lat_why), ("lon", lon_why),
                                    ("water_depth", depth_why), ("shore_normal", normal_why))
                  if not w.startswith("ok")}
        if absent:
            meta["absent"] = absent
        return pid, meta
    finally:
        nc.close()


# Bounds a cached value must satisfy to be a measurement rather than damage. Low bound is
# EXCLUSIVE for water_depth: these points sit on the 10 m contour and a depth of 0 is not a
# shallow reading, it is a missing one. The 12000 m ceiling matches the sanity bound the
# NDBC path already applies in nwps_nearshore._parse_water_depth — the pattern existed in
# this repo, it had just never been pointed at the MOP cache.
_FIELD_BOUNDS = {
    "lat":          (-90.0, 90.0, True),     # (lo, hi, lo_inclusive)
    "lon":          (-180.0, 180.0, True),
    "water_depth":  (0.0, 12000.0, False),
    "shore_normal": (0.0, 360.0, True),
}
# Fields where an exact 0.0 is the historical fill signature rather than a plausible value.
# shore_normal 0.0 is a legal bearing in principle, so it is reported as SUSPECT rather than
# impossible: after the meta_scalar fix a 0.0 here would mean CDIP really published one.
_SUSPECT_ZERO = ("shore_normal", "lat", "lon")


def audit_cache(cache):
    """Per-field census of a built MOP cache. Pure — no network, no file I/O.

    THE POINT OF THIS FUNCTION IS THAT 129 OF 11,677 POINTS CARRIED AN IMPOSSIBLE VALUE AND
    NOTHING SAID SO. build_cache reported only "N points, M with coordinates", which counted
    the one field that happened to be healthy. A 1.1% corruption rate is invisible in a
    summary like that and obvious in one that names the field.

    Returns {field: {present, absent, zero, impossible, ids}} plus a top-level "errors"
    count, where `ids` lists up to _AUDIT_ID_SAMPLE offending point ids so a run can be
    chased without re-reading the cache by hand.
    """
    report = {"points": len(cache),
              "errors": sum(1 for m in cache.values() if isinstance(m, dict) and "error" in m)}
    for field, (lo, hi, lo_inc) in _FIELD_BOUNDS.items():
        present = absent = zero = 0
        bad_ids, zero_ids = [], []
        for pid, m in cache.items():
            if not isinstance(m, dict) or "error" in m:
                continue
            v = m.get(field)
            if v is None:
                absent += 1
                continue
            try:
                v = float(v)
            except (TypeError, ValueError):
                bad_ids.append(pid)
                continue
            in_range = (lo <= v <= hi) if lo_inc else (lo < v <= hi)
            if not math.isfinite(v) or not in_range:
                bad_ids.append(pid)
                continue
            present += 1
            if v == 0.0 and field in _SUSPECT_ZERO:
                zero += 1
                zero_ids.append(pid)
        report[field] = {
            "present": present, "absent": absent, "zero": zero,
            "impossible": len(bad_ids),
            "impossible_ids": sorted(bad_ids)[:_AUDIT_ID_SAMPLE],
            "zero_ids": sorted(zero_ids)[:_AUDIT_ID_SAMPLE],
        }
    return report


_AUDIT_ID_SAMPLE = 12


def needs_reread(cache, pid):
    """Should build_cache re-read *pid*? Pure, so the sticky-zero bug stays pinned.

    The predicate used to be `pid not in cache or cache[pid].get("lat") is None`, which
    re-read a point only when it was missing or had no coordinates. Every one of the 129
    damaged points had a perfectly good latitude, so a rebuild over an existing cache
    skipped all of them and printed a success line. Repairing them required deleting the
    whole cache and re-pulling 11,677 points over OPeNDAP.
    """
    if pid not in cache:
        return True
    m = cache[pid]
    if not isinstance(m, dict) or "error" in m:
        return True
    if m.get("lat") is None:
        return True
    return any(m.get(f) == 0.0 for f in _SUSPECT_ZERO)


def print_cache_audit(report):
    """Render an audit_cache report and return the number of findings that should fail a run.

    Impossible values are findings. So is a suspect zero: it is what this whole change is
    about, and a run that emits one has either hit a genuine CDIP zero or been produced by a
    builder predating the meta_scalar fix. Both deserve a look before the cache is used.
    """
    print(f"cache audit: {report['points']} points, {report['errors']} error entries")
    findings = 0
    for field in _FIELD_BOUNDS:
        s = report[field]
        pct = (100.0 * s["absent"] / report["points"]) if report["points"] else 0.0
        line = (f"  {field:13} present {s['present']:6}  absent {s['absent']:5} ({pct:4.1f}%)"
                f"  impossible {s['impossible']:5}  zero {s['zero']:5}")
        print(line)
        if s["impossible"]:
            findings += s["impossible"]
            print(f"      IMPOSSIBLE values at: {', '.join(s['impossible_ids'])}"
                  f"{' …' if s['impossible'] > _AUDIT_ID_SAMPLE else ''}")
        if s["zero"]:
            findings += s["zero"]
            print(f"      SUSPECT exact zeros at: {', '.join(s['zero_ids'])}"
                  f"{' …' if s['zero'] > _AUDIT_ID_SAMPLE else ''}")
            print("      An exact 0.0 here was the masked-fill signature. If this cache was "
                  "built by a\n      current builder the value is CDIP's own; if not, rebuild "
                  "it. Do NOT interpolate\n      a replacement from neighbouring points.")
    if findings:
        print(f"  -> {findings} finding(s). The cache is written; inspect before relying on it.")
    return findings


CACHE_AUDIT_EXIT = 3


def audit_exit_code(cache):
    """Print the audit and return build_cache's exit status. Split out to be testable.

    build_cache cannot run offline — it opens the THREDDS catalog on its first line — so
    with this inline, "the audit runs at all" and "findings reach the exit code" were the
    two things no test could reach. Both are the point of the change: an audit nobody acts
    on is the silence it replaced.
    """
    return CACHE_AUDIT_EXIT if print_cache_audit(audit_cache(cache)) else 0


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
    # RETRY KNOWN-BAD POINTS, not only missing ones. This tested `lat is None` alone, so a
    # point with a good latitude and a fabricated shore_normal of 0.0 was never re-read —
    # the 129 damaged points were STICKY, and re-running build-cache over an existing cache
    # skipped every one of them while reporting success. A cache written before the
    # meta_scalar fix is repaired by re-running this, now that the predicate can see it.
    todo = [(pid, url) for pid, url in points if needs_reread(cache, pid)]
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
    # AUDIT AFTER WRITING, DELIBERATELY. This build takes hours over OPeNDAP and is
    # resumable; refusing to write on a finding would throw away good work and force the
    # whole pull again. The cache lands, the findings are named, and the exit code carries
    # them so a script or a person notices rather than reading "wrote N points" as success.
    return audit_exit_code(cache)


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

    # --- the masked-scalar guard, offline ------------------------------------ #
    # These need no netCDF4: meta_scalar only ever does `nc.variables[n][:]`.
    class _V:
        def __init__(self, p): self._p = p
        def __getitem__(self, _k): return self._p

    class _N:
        def __init__(self, **v): self.variables = dict(v)

    _masked = np.ma.MaskedArray([0.0], mask=[True])
    check("a masked scalar reads as ABSENT, not as the 0.0 underneath",
          meta_scalar(_N(metaShoreNormal=_V(_masked)), "metaShoreNormal") == (None, "masked"))
    check("the OLD expression really did yield 0.0 from that same input",
          float(np.asarray(_masked).ravel()[0]) == 0.0)
    check("an UNMASKED 0.0 is a real reading and survives",
          meta_scalar(_N(metaShoreNormal=_V(np.array([0.0]))), "metaShoreNormal") == (0.0, "ok"))
    check("a masked first alias falls through to a good second",
          meta_scalar(_N(a=_V(_masked), b=_V(np.array([147.25]))), "a", "b") == (147.25, "ok"))
    check("a missing variable is absent", meta_scalar(_N(), "metaShoreNormal") == (None, "absent"))
    check("the audit counts 3 suspect zeros among 4 points and flags them",
          audit_cache({
              "D0930": {"lat": 33.2, "lon": -117.4, "water_depth": 10.0, "shore_normal": 0.0},
              "OC642": {"lat": 33.7, "lon": -118.1, "water_depth": 11.0, "shore_normal": 0.0},
              "OC643": {"lat": 33.7, "lon": -118.1, "water_depth": 11.0, "shore_normal": 0.0},
              "SM297": {"lat": 37.5, "lon": -122.5, "water_depth": 12.0, "shore_normal": 158.6},
          })["shore_normal"]["zero"] == 3)
    check("a null shore normal is absent, not a finding",
          audit_cache({"X": {"lat": 1.0, "lon": 2.0, "water_depth": 10.0,
                             "shore_normal": None}})["shore_normal"]["absent"] == 1)
    check("a zero water depth is IMPOSSIBLE, not merely suspect",
          audit_cache({"X": {"lat": 1.0, "lon": 2.0, "water_depth": 0.0,
                             "shore_normal": 231.0}})["water_depth"]["impossible"] == 1)
    check("a damaged point is re-read; a healthy one is not",
          needs_reread({"X": {"lat": 1.0, "shore_normal": 0.0}}, "X") is True
          and needs_reread({"X": {"lat": 1.0, "lon": 2.0, "water_depth": 10.0,
                                  "shore_normal": 231.0}}, "X") is False)

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
