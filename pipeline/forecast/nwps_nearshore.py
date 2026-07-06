"""NOAA NWPS nearshore forecast integration (Stage 2 — OKX pilot).

Sibling of pipeline/forecast/mop.py. For spots tagged
``swell_window_source == "nwps"`` in spots_enriched.json (set by
``pipeline.apply_nwps_assignments`` once placement + the buoy trust check pass),
override the spot's swell rating with its NWPS CG1 nearshore-field node, run
through the SAME break-response chain as the normal path (interpret.py: face_ft ×
directional_gain in the nearshore frame, period quality, chop), while KEEPING the
per-hour wind and tide multipliers the normal rater already computed. Spots
without a fresh NWPS read this cycle are left exactly as the orientation path
produced them.

This is the productionised form of the validated prototypes
(scripts/nwps_okx_probe_v3.py = discovery + seaward node placement + plausibility;
scripts/nwps_okx_buoycheck.py = NWPS-vs-NDBC trust gate). The logic is ported
here so the pipeline has no dependency on scripts/; interpret.py is reused for the
rating primitives. NWPS gives no shore normal (unlike MOP's metaShoreNormal), so
the "representative cell" check is replaced by: seaward-half-plane node selection
(±90° of orientation_deg) + a period floor + an in-swell-window direction gate.

Design guarantees (additive + reversible), mirroring apply_mop_overrides:
  * No-op until a spot carries swell_window_source == "nwps".
  * Any failure (no cycle, NOMADS hiccup, missing hour, bad node) → that spot
    keeps its orientation-path rating for the cycle. Never errors, never blanks.
  * HORIZON departure from MOP: NWPS carries the full 145-hr horizon (f000..f144),
    so we feed NWPS for EVERY valid hour it covers, not just near-now; fall back
    to WW3/orientation only beyond f144 or where a cycle lacks that hour.

  python -m pipeline.forecast.nwps_nearshore --selftest
  python -m pipeline.forecast.nwps_nearshore --validate            # Mac: fetch one OKX cycle, place + sample vs fallback
  python -m pipeline.forecast.nwps_nearshore --trustcheck          # Mac: NWPS-vs-buoy 44025 trust gate
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import math
import os
import re
from pathlib import Path

import numpy as np

from ..interpret import (
    chop_multiplier, chop_ratio, composite_stars, directional_gain, face_ft,
    period_quality,
)
from urllib.error import HTTPError, URLError

log = logging.getLogger("pipeline.forecast.nwps_nearshore")

RATING_SOURCE = "ww3"          # face_ft shoaling factor — same as the validated chain
NOMADS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwps/prod/"
PER_FLOOR_S = 3.0              # period below this = dead/sheltered (back-bay) cell
FAR_CAP_KM = 3.0              # nearest seaward wet cell beyond this (~2× the 1.82 km spacing) → unplaced
HORIZON_MAX_FH = 144          # CG1 carries f000..f144 hourly (145 steps)
# Trust-gate thresholds — identical to the MOP rollout (scripts/nwps_okx_buoycheck.py).
TRUST_R_MIN = 0.80
TRUST_CIRC_MAX = 25.0
TRUST_BUOY_RANGE_MIN_M = 0.5  # below this Hs span the window is flat → INCONCLUSIVE
TRUST_MIN_PAIRS = 6

# eccodes short names (NOT NCEP abbreviations):
#   swh   = sig height of combined wind waves + swell (headline Hs)
#   shts  = sig height of total swell (swell only) — the windsea split for chop
#   perpw = primary wave period   dirpw = primary wave direction (deg, FROM)
_SHORTS = ("swh", "shts", "perpw", "dirpw")
_SLUG_RE = re.compile(r"[^a-z0-9]+")

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]
SCRIPTS_DIR = _ROOT / "scripts"
ENRICHED = _ROOT / "pipeline" / "spots_enriched.json"
# --validate writes a per-region diagnostic dump, scripts/nwps_{wfo}_validate_out.json
# (computed per run in validate_batch) — NOT the apply input.


def _slug(name):
    return _SLUG_RE.sub("-", (name or "").lower()).strip("-")


# --------------------------------------------------------------------------- #
# Geometry + window helpers (ported verbatim from nwps_okx_probe_v3.py)        #
# --------------------------------------------------------------------------- #
def _haversine_km(a, b, c, d):
    R = 6371.0
    p1, p2 = math.radians(a), math.radians(c)
    dphi = math.radians(c - a); dl = math.radians(d - b)
    x = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1, math.sqrt(x)))


def _bearing(lat1, lon1, lat2, lon2):
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dl))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _ang_within(deg, center, half):
    return abs(((deg - center + 180) % 360) - 180) <= half


def _in_arcs(deg, arcs):
    """deg inside any swell-window arc; handles multi-arc + 0/360 wrap."""
    if not arcs:
        return True
    for a in arcs:
        lo, hi = a["min"], a["max"]
        if lo <= hi:
            if lo <= deg <= hi:
                return True
        elif deg >= lo or deg <= hi:   # wraps through 0/360
            return True
    return False


def placement_verdict(dist_km, per, dirpw, arcs):
    """OK / FAR / DEAD / OFFWIN for a placed node — the clause-1 replacement.
    FAR = no seaward wet cell within FAR_CAP_KM; DEAD = period below floor
    (sheltered); OFFWIN = direction outside the spot's swell window."""
    if dist_km is None or dist_km > FAR_CAP_KM:
        return "FAR"
    if per is None or per != per or per < PER_FLOOR_S:
        return "DEAD"
    if dirpw is not None and dirpw == dirpw and not _in_arcs(dirpw, arcs):
        return "OFFWIN"
    return "OK"


def _is_domain_miss(outcome):
    """Explicit rollup of the placement outcome: True when the spot fell OUTSIDE
    this WFO's grid domain — FAR (nearest wet cell beyond FAR_CAP_KM) or NO_WET_CELL
    (no water in the grid at all) — so the grid-edge mop-up should retry it on
    another WFO. False for in-domain disqualifiers (DEAD / OFFWIN) and OK. Purely
    derived — it does NOT change how the outcomes themselves are computed."""
    return outcome in ("FAR", "NO_WET_CELL")


# --------------------------------------------------------------------------- #
# NOMADS discovery (ported from the probe / buoycheck)                         #
# --------------------------------------------------------------------------- #
def _http_get(url, timeout=180):
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "stormy-petrel-nwps"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _listdir(url):
    try:
        html = _http_get(url, 60).decode("utf-8", "replace")
    except (HTTPError, URLError, OSError):
        return []
    return re.findall(r'href="([^"?][^"]*)"', html)


def _cycle_files(wfo, date, cc, region="er"):
    cg1 = f"{NOMADS}{region}.{date}/{wfo}/{cc}/CG1/"
    files = [n for n in _listdir(cg1)
             if n.endswith(".grib2") and "Trkng" not in n and "CG1" in n]   # field file, NOT CG0 tracking
    return cg1, sorted(files)


def find_latest_cycle(wfo, region="er", lookback_days=4):
    """(date, cc, url) of the latest existing CG1 field file for *wfo*, or None."""
    dates = sorted({m for n in _listdir(NOMADS)
                    for m in re.findall(rf'^{region}\.(\d{{8}})/$', n)}, reverse=True)
    for date in dates[:lookback_days]:
        wfo_url = f"{NOMADS}{region}.{date}/{wfo}/"
        for cc in sorted({c for n in _listdir(wfo_url) for c in re.findall(r'^(\d\d)/$', n)},
                         reverse=True):
            cg1, files = _cycle_files(wfo, date, cc, region)
            if files:
                return date, cc, cg1 + files[-1]
    return None


def recent_cycles(wfo, n, region="er"):
    """Up to *n* most recent (date, cc, url) CG1 cycles — for the trust gate's
    elapsed-forecast-hour assembly."""
    out = []
    dates = sorted({m for x in _listdir(NOMADS)
                    for m in re.findall(rf'^{region}\.(\d{{8}})/$', x)}, reverse=True)
    for date in dates:
        wfo_url = f"{NOMADS}{region}.{date}/{wfo}/"
        for cc in sorted({c for x in _listdir(wfo_url) for c in re.findall(r'^(\d\d)/$', x)},
                         reverse=True):
            cg1, files = _cycle_files(wfo, date, cc, region)
            if files:
                out.append((date, cc, cg1 + files[-1]))
            if len(out) >= n:
                return out
    return out


# --------------------------------------------------------------------------- #
# GRIB load + node sampling (cfgrib; lazy so --selftest needs no eccodes)      #
# --------------------------------------------------------------------------- #
def _cycle_dt(date, cc):
    return datetime.datetime(int(date[:4]), int(date[4:6]), int(date[6:]), int(cc),
                             tzinfo=datetime.timezone.utc)


def _step_hours(da):
    """Forecast hours aligned to a DataArray's step axis (list), or [0] when the
    run carries a single scalar step. NWPS steps are timedelta64 offsets."""
    if "step" not in da.coords:
        return [0]
    steps = np.atleast_1d(np.asarray(da["step"].values))
    return [int(round(float(s / np.timedelta64(1, "h")))) for s in steps]


def load_cycle(wfo, cycle=None):
    """Fetch + parse the latest (or given) CG1 cycle. Returns a dict:
      {lats, lons, mask, cycle_dt, steps, fields:{(short,fh): float32 grid}}
    Read with xarray + cfgrib, mirroring the sibling fetcher pipeline/forecast/
    nwps.py — the pipeline runner ships cfgrib/eccodes, not pygrib. cfgrib opens
    an NWPS GRIB as MULTIPLE datasets (one per param group), so the 4 wave fields
    can span datasets; we union them keyed by (shortName, forecast hour), the way
    nwps._extract_time_series_from_datasets unions by valid_time. Land cells read
    back NaN under cfgrib (they were masked arrays under pygrib), so `mask` is the
    NaN footprint of swh@f000 and `_wet_nodes` still selects on ``not mask``.
    Holds only the 4 wave fields × 145 steps in memory (≈tens of MB per WFO nest).
    Raises on fetch/parse failure (callers catch)."""
    import cfgrib    # lazy: --selftest never calls load_cycle, so needs no eccodes
    import warnings  # to scope the cfgrib/xarray merge FutureWarning below
    if cycle is None:
        cycle = find_latest_cycle(wfo)
        if not cycle:
            raise OSError(f"no recent CG1 cycle for {wfo}")
    date, cc, url = cycle
    path = os.path.join("/tmp", f"nwps_{wfo}_{date}_{cc}_CG1.grib2")
    body = _http_get(url)
    if body[:4] != b"GRIB":
        raise OSError(f"not GRIB: {url}")
    with open(path, "wb") as f:
        f.write(body)
    with warnings.catch_warnings():
        # cfgrib.open_datasets merges each param group internally with xarray's
        # default `compat`, emitting one FutureWarning per group ("the default
        # value for compat will change ... set compat explicitly"). We can't thread
        # compat into cfgrib's internal merge, and these groups carry no conflicting
        # duplicate variables (the current default and the future "override" give
        # identical values), so silence just that warning — the merged
        # swh/shts/perpw/dirpw values are unchanged.
        warnings.filterwarnings("ignore", category=FutureWarning, message=".*compat.*")
        datasets = cfgrib.open_datasets(path)
    if not datasets:
        raise OSError(f"cfgrib produced no datasets: {url}")

    # NWPS CG1 is a regular lat/lon nest — take the 1-D axes from the first
    # wave-bearing dataset and mesh them into the 2-D (lat, lng) frame the
    # seaward-node selector expects. Longitude → the app's -180/180 convention.
    lat1d = lng1d = None
    fields, steps = {}, set()
    for ds in datasets:
        if lat1d is None and "latitude" in ds.coords and "longitude" in ds.coords:
            lat1d = np.asarray(ds["latitude"].values, dtype="float64").ravel()
            raw = np.asarray(ds["longitude"].values, dtype="float64").ravel()
            lng1d = ((raw + 180.0) % 360.0) - 180.0
        for var in ds.data_vars:
            short = str(var).lower()
            if short not in _SHORTS:
                continue
            da = ds[var]
            for si, fh in enumerate(_step_hours(da)):
                if fh > HORIZON_MAX_FH:
                    continue
                slab = da.isel(step=si) if "step" in da.dims else da
                try:
                    slab = slab.transpose("latitude", "longitude")
                except ValueError:
                    continue   # not a plain lat/lon slab (e.g. wave partitions) — skip
                fields[(short, fh)] = np.asarray(slab.values, dtype="float32")
                steps.add(fh)
    if lat1d is None:
        raise OSError(f"cfgrib datasets carry no lat/lon grid: {url}")
    swh0 = fields.get(("swh", 0))
    if swh0 is None:   # mirror pygrib's swh@f000 anchor; else the earliest swh step
        swh_fhs = sorted(fh for (s, fh) in fields if s == "swh")
        if not swh_fhs:
            raise OSError(f"no swh field in cycle: {url}")
        swh0 = fields[("swh", swh_fhs[0])]
    lats, lons = np.meshgrid(lat1d, lng1d, indexing="ij")
    mask = np.isnan(swh0)   # land = NaN wave cell (was a masked array under pygrib)
    return {"lats": lats, "lons": lons, "mask": mask, "cycle_dt": _cycle_dt(date, cc),
            "steps": sorted(steps), "fields": fields}


def _wet_nodes(lats, lons, mask):
    return [(float(lats[i, j]), float(lons[i, j]), i, j)
            for i in range(lats.shape[0]) for j in range(lats.shape[1]) if not mask[i, j]]


def select_node(cycle, lat, lng, orientation):
    """Seaward-aware nearest WET cell (replaces MOP's metaShoreNormal clause):
    prefer wet cells whose bearing from the spot is within ±90° of orientation_deg
    (the open-ocean half-plane), else nearest wet. Returns
    (i, j, node_lat, node_lng, dist_km, moved) or None if no wet cell."""
    wet = _wet_nodes(cycle["lats"], cycle["lons"], cycle["mask"])
    if not wet:
        return None
    def dist(n):
        return _haversine_km(lat, lng, n[0], n[1])
    naive = min(wet, key=dist)
    if orientation is not None:
        sea = [n for n in wet if _ang_within(_bearing(lat, lng, n[0], n[1]), orientation, 90)]
    else:
        sea = wet
    best = min(sea, key=dist) if sea else naive
    return best[2], best[3], best[0], best[1], dist(best), best is not naive


def _nearest_cell(cycle, lat, lng):
    """(i, j, dist_km) of the nearest WET cell to a baked node lat/lng."""
    wet = _wet_nodes(cycle["lats"], cycle["lons"], cycle["mask"])
    if not wet:
        return None
    best = min(wet, key=lambda n: _haversine_km(lat, lng, n[0], n[1]))
    return best[2], best[3], _haversine_km(lat, lng, best[0], best[1])   # (i, j, dist_km)


def _node_value(cycle, short, fh, i, j):
    arr = cycle["fields"].get((short, fh))
    if arr is None:
        return None
    v = float(arr[i, j])
    return None if v != v else v   # NaN (land/missing) → None


def nwps_series_by_hour(spot, cycle):
    """{valid_hour_bucket: (swh, perpw, dirpw, shts)} for the spot's node across
    the full f000..f144 horizon, or None. hour_bucket = floor(valid_epoch/3600).
    Node = nearest wet cell to the baked nwps_node_lat/lng, else seaward-selected."""
    nlat, nlng = spot.get("nwps_node_lat"), spot.get("nwps_node_lng")
    if nlat is not None and nlng is not None:
        cell = _nearest_cell(cycle, nlat, nlng)
        ij = (cell[0], cell[1]) if cell else None
    else:
        sel = select_node(cycle, spot["lat"], spot["lng"], spot.get("orientation_deg"))
        ij = (sel[0], sel[1]) if sel else None
    if ij is None:
        return None
    i, j = ij
    cdt = cycle["cycle_dt"]
    out = {}
    for fh in cycle["steps"]:
        swh = _node_value(cycle, "swh", fh, i, j)
        per = _node_value(cycle, "perpw", fh, i, j)
        dpw = _node_value(cycle, "dirpw", fh, i, j)
        if swh is None or per is None or dpw is None:
            continue
        shts = _node_value(cycle, "shts", fh, i, j)
        valid = cdt + datetime.timedelta(hours=fh)
        out[int(valid.timestamp() // 3600)] = (swh, per, dpw, shts)
    return out or None


# --------------------------------------------------------------------------- #
# Rating + override (mirror mop_stars / apply_mop_overrides)                   #
# --------------------------------------------------------------------------- #
def nwps_stars(hs, per, dirpw, swell_hs, orientation, wind_mult=1.0, tide_mult=1.0):
    """Nearshore-frame star rating for one NWPS hour, reusing interpret.py exactly
    (face_ft from swh × directional_gain(dirpw vs orientation), period quality from
    perpw), with per-hour wind/tide injected from the normal rater. Chop is derived
    from the windsea split (swh vs shts); if shts is missing, chop falls to neutral
    (the entry's wind-based texture still applies via wind_mult). Returns
    (stars, face_ft, dir_gain, chop_mult, period_quality) or (None, …)."""
    if hs is None or per is None or dirpw is None or orientation is None:
        return None, None, None, None, None
    dg = directional_gain(dirpw, [], orientation, orientation)   # cos²((dir−orientation)/2)
    face = face_ft(hs, per, RATING_SOURCE)
    eff = face * dg
    cm = chop_multiplier(chop_ratio(hs, swell_hs if swell_hs else hs))   # windsea-derived
    pq = period_quality(per)
    stars = composite_stars(eff, wind_mult, tide_mult, cm, pq)
    return stars, face, dg, cm, pq


def _make_default_fetch():
    """Per-WFO cycle cache so all OKX spots share one fetch+parse. Returns a
    fetch(spot) → series-by-hour closure for apply_nwps_overrides."""
    cache = {}

    def fetch(spot):
        wfo = spot.get("nwps_wfo")
        if not wfo:
            return None
        if wfo not in cache:
            cache[wfo] = load_cycle(wfo)   # may raise — caught per spot by the caller
        return nwps_series_by_hour(spot, cache[wfo])
    return fetch


def apply_nwps_overrides(ratings, spots, *, dry_run=False, only=None, _fetch=None):
    """Override the swell rating of every swell_window_source=="nwps" spot with its
    NWPS node series, keeping each hour's wind/tide. Mutates *ratings* in place
    unless dry_run. *only* = slugs to restrict to. *_fetch* injectable for tests.
    Returns stats {fed, fell_back, errored, details}. Mirrors apply_mop_overrides;
    the one difference is HORIZON — NWPS covers the full f000..f144, so every
    overlapping valid hour is fed (not just near-now), and only hours beyond the
    cycle's coverage fall back to the orientation/WW3 path."""
    fetch = _fetch or _make_default_fetch()
    fed = fell_back = errored = 0
    details = []
    for s in spots:
        if s.get("swell_window_source") != "nwps":
            continue
        name = s.get("name")
        slug = _slug(name)
        if only is not None and slug not in only:
            continue
        entries = ratings.get(name)
        if not entries:
            details.append((slug, "no base ratings (skip)", 0))
            continue
        orient = s.get("orientation_deg")
        try:
            series = fetch(s)
        except (HTTPError, URLError, OSError, KeyError, ValueError, ImportError) as e:
            errored += 1
            details.append((slug, f"error: {type(e).__name__} → fallback", 0))
            continue
        if not series:
            fell_back += 1
            details.append((slug, "no fresh NWPS data → fallback", 0))
            continue
        n_over = 0
        for e in entries:
            t = _iso_to_epoch(e.get("valid_time"))
            if t is None:
                continue
            k = int(t // 3600)
            m = series.get(k) or series.get(k - 1) or series.get(k + 1)
            if not m:
                continue   # hour beyond f144 or missing → keep orientation path for it
            swh, per, dpw, shts = m
            st, face, dg, cm, pq = nwps_stars(swh, per, dpw, shts, orient,
                                              e.get("wind_mult", 1.0), e.get("tide_mult", 1.0))
            if st is None:
                continue
            if not dry_run:
                e.update(
                    face_ft=round(face, 2), dir_gain=round(dg, 3), chop_mult=round(cm, 3),
                    period_quality=round(pq, 3), effective_size_ft=round(face * dg, 2),
                    stars=st, swell_dp=round(dpw, 3), swell_tp=round(per, 3),
                    swell_hs=round(shts, 3) if shts is not None else None,
                    swell_source="nwps",
                )
            n_over += 1
        if n_over:
            fed += 1
            details.append((slug, f"{n_over} hrs NWPS-fed", n_over))
        else:
            fell_back += 1
            details.append((slug, "NWPS had no overlapping hour → fallback", 0))
    return {"fed": fed, "fell_back": fell_back, "errored": errored, "details": details}


def _iso_to_epoch(iso):
    try:
        return datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Trust gate (ported from nwps_okx_buoycheck.py) — NWPS vs nearest NDBC buoy    #
# --------------------------------------------------------------------------- #
def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs); vy = sum((y - my) ** 2 for y in ys)
    return cov / math.sqrt(vx * vy) if vx > 0 and vy > 0 else float("nan")


def _circ_std(diffs):
    n = len(diffs)
    if n == 0:
        return float("nan")
    s = sum(math.sin(math.radians(d)) for d in diffs) / n
    c = sum(math.cos(math.radians(d)) for d in diffs) / n
    rbar = min(1.0, math.hypot(s, c))   # clamp: a constant offset gives rbar≈1 (float can overshoot)
    if rbar <= 1e-9:
        return float("inf")
    return math.degrees(math.sqrt(max(0.0, -2 * math.log(rbar))))


def trust_verdict(pairs):
    """pairs = [(nwps_hs, buoy_hs, nwps_dir, buoy_dir)]. Returns (verdict, r,
    circ_std, n). INCONCLUSIVE on a flat spell / too few hours, else PASS/FAIL vs
    the MOP thresholds (r≥0.80, circ_std≤25). Pure — selftest-able offline."""
    if len(pairs) < TRUST_MIN_PAIRS:
        return "INCONCLUSIVE", float("nan"), float("nan"), len(pairs)
    nhs = [p[0] for p in pairs]; bhs = [p[1] for p in pairs]
    if max(bhs) - min(bhs) < TRUST_BUOY_RANGE_MIN_M:
        return "INCONCLUSIVE", float("nan"), float("nan"), len(pairs)   # flat — r is noise
    r = _pearson(nhs, bhs)
    diffs = [p[2] - p[3] for p in pairs if p[3] is not None and p[2] == p[2]]
    cs = _circ_std(diffs)
    if r >= TRUST_R_MIN and cs <= TRUST_CIRC_MAX:
        return "PASS", r, cs, len(pairs)
    return "FAIL", r, cs, len(pairs)


def _buoy_hourly(buoy_id):
    """{hour_bucket: (hs_m, dir_deg)} from the buoy's NDBC realtime2 .txt, reusing
    the pipeline's fetcher + parser (lazy import; needs requests). None on failure."""
    try:
        from .buoys import _fetch_text, _parse_realtime2, _STD_FIELDS
    except Exception:  # noqa: BLE001
        return None
    txt = _fetch_text(f"https://www.ndbc.noaa.gov/data/realtime2/{buoy_id.upper()}.txt",
                      buoy_id, "std", use_cache=False)
    if not txt:
        return None
    out = {}
    for o in _parse_realtime2(txt, _STD_FIELDS):
        hs, mwd, t = o.get("wave_height_m"), o.get("mean_wave_dir_deg"), _iso_to_epoch(o.get("time"))
        if hs is not None and t is not None and hs < 90:
            out[int(t // 3600)] = (float(hs), float(mwd) if mwd is not None else None)
    return out or None


def trust_check(wfo, buoy_id, blat, blng, n_cycles=4):
    """Live NWPS-vs-buoy trust gate (Mac). Assembles NWPS Hs/dir at the buoy's node
    from recent cycles' elapsed forecast hours (shortest lead per valid hour),
    joins to the buoy's hourly obs, returns trust_verdict(...). Needs NOMADS+NDBC."""
    buoy = _buoy_hourly(buoy_id)
    if not buoy:
        return "INCONCLUSIVE", float("nan"), float("nan"), 0
    now = datetime.datetime.now(datetime.timezone.utc)
    series = {}   # valid_hour -> (nwps_hs, nwps_dir, lead)
    for date, cc, url in recent_cycles(wfo, n_cycles):
        cyc = load_cycle(wfo, (date, cc, url))
        elapsed = int((now - cyc["cycle_dt"]).total_seconds() // 3600)
        if elapsed < 0:
            continue
        cell = _nearest_cell(cyc, blat, blng)
        if cell is None:
            continue
        i, j = cell[0], cell[1]
        for fh in cyc["steps"]:
            if fh > elapsed:
                continue
            hs = _node_value(cyc, "swh", fh, i, j)
            if hs is None:
                continue
            valid = int((cyc["cycle_dt"] + datetime.timedelta(hours=fh)).timestamp() // 3600)
            if valid in series and series[valid][2] <= fh:
                continue
            series[valid] = (hs, _node_value(cyc, "dirpw", fh, i, j), fh)
    pairs = [(series[t][0], buoy[t][0], series[t][1], buoy[t][1])
             for t in sorted(series) if t in buoy]
    return trust_verdict(pairs)


# --------------------------------------------------------------------------- #
# --validate / --trustcheck (Mac; offline-degrading like mop.validate_batch)   #
# --------------------------------------------------------------------------- #
def _load_pilot_spots():
    """Pilot spots from scripts/okx_pilot.json (the probe's input) joined to the
    roster for full fields, or a 3-spot fallback. (spot dicts, note)."""
    pj = SCRIPTS_DIR / "okx_pilot.json"
    roster = {_slug(s["name"]): s for s in json.loads(ENRICHED.read_text())} if ENRICHED.exists() else {}
    if pj.exists():
        pilots = json.loads(pj.read_text())
        out = []
        for p in pilots:
            base = roster.get(p.get("slug"), {})
            s = dict(base); s.update(p); s.setdefault("nwps_wfo", "okx")
            out.append(s)
        return out, None
    return [{"slug": "rockaway-beach", "name": "Rockaway Beach", "lat": 40.58329, "lng": -73.806882,
             "nwps_wfo": "okx", "orientation_deg": 160.0, "swell_window_arcs": [{"min": 90, "max": 230}]},
            {"slug": "lido-beach", "name": "Lido Beach", "lat": 40.583714, "lng": -73.606746,
             "nwps_wfo": "okx", "orientation_deg": 170.0, "swell_window_arcs": [{"min": 100, "max": 240}]},
            {"slug": "montauk-point", "name": "Montauk Point", "lat": 41.071004, "lng": -71.855135,
             "nwps_wfo": "okx", "orientation_deg": 130.0, "swell_window_arcs": [{"min": 40, "max": 220}]}], \
        "okx_pilot.json not found — 3-spot fallback"


def _load_roster_spots(slugs):
    """Load specific spots straight from spots_enriched.json by slug (read-only),
    independent of the pilot file. Each record keeps its real fields — lat/lng,
    orientation_deg, swell_window_arcs and its OWN nwps_wfo tag (nothing is
    force-stamped). Raises ValueError naming any slug not in the roster, so a
    typo / absent spot is loud instead of silently dropped."""
    if not ENRICHED.exists():
        raise FileNotFoundError(f"--batch needs {ENRICHED}; not found")
    roster = {}
    for s in json.loads(ENRICHED.read_text()):
        roster.setdefault(_slug(s.get("name")), s)
    out, missing = [], []
    for raw in slugs:
        sl = raw.strip()
        if not sl:
            continue
        s = roster.get(sl)
        if s is None:
            missing.append(sl)
        else:
            out.append(s)
    if missing:
        raise ValueError(f"--batch: slug(s) not found in spots_enriched.json: {', '.join(missing)}")
    return out


def _warn_if_roster_stale(max_age_days=2):
    """Non-blocking: warn once if the local NDBC roster files are older than
    *max_age_days*. Never fails — a missing / unstat-able file is just skipped."""
    import time
    from ..config import NDBC_STATIONS_XML, NDBC_LATEST_OBS_TXT
    now = time.time()
    stale = []
    for p in (NDBC_STATIONS_XML, NDBC_LATEST_OBS_TXT):
        try:
            age = (now - p.stat().st_mtime) / 86400.0
        except OSError:
            continue
        if age > max_age_days:
            stale.append((p.name, age))
    if stale:
        name, age = max(stale, key=lambda x: x[1])
        print(f"warning: NDBC roster is stale — {name} is {age:.1f} days old; refresh "
              "activestations.xml / latest_obs.txt for current station metadata.")


def _buoy_latlng(buoy_id, *, _active=None, _reporting=None):
    """(lat, lng) for an NDBC buoy id, resolved from the FULL active-station metadata
    (enrichment.geodata.load_ndbc_active_stations — every active station with
    coordinates), NOT the wave-reporting-only subset. Coordinates are static metadata
    and must not depend on a momentary WVHT reading, so a real buoy that isn't
    transmitting a wave height right now still resolves and the trust check can
    proceed. Raises KeyError only if the id is absent from the full active list
    (genuinely unknown) — never falls back to typed coordinates. Prints a
    non-blocking note if the buoy resolves but isn't in the wave-reporting subset.
    *_active* / *_reporting* are injectable station lists for offline tests."""
    from ..enrichment.geodata import load_ndbc_active_stations, load_ndbc_wave_stations
    if _active is None:
        _warn_if_roster_stale()
    bid = str(buoy_id).lower()
    active = {s["id"]: s for s in (_active if _active is not None else load_ndbc_active_stations())}
    st = active.get(bid)
    if st is None:
        raise KeyError(f"buoy {buoy_id!r} not in the NDBC active-station list "
                       "(activestations.xml) — genuinely unknown; cannot resolve its lat/lng")
    reporting = {s["id"] for s in (_reporting if _reporting is not None else load_ndbc_wave_stations())}
    if reporting and bid not in reporting:
        print(f"note: {buoy_id} resolved from station metadata but isn't currently reporting "
              "waves; trust check may return INCONCLUSIVE.")
    return st["lat"], st["lng"]


def validate_batch(batch=None, wfo="okx"):
    """Part C — fetch one *wfo* cycle (default okx), place each spot's seaward node,
    sample its f000 swh/perpw/dirpw, print placement verdict + NWPS★ vs the
    orientation fallback★, plus a forced-empty test. Spots come from --batch (loaded
    from spots_enriched.json by slug, keeping each spot's real nwps_wfo tag) or, with
    no batch, the okx_pilot.json pilot set. Writes the placement results to
    scripts/nwps_{wfo}_validate_out.json — a DIAGNOSTIC dump only (records every spot's
    outcome: OK / FAR / DEAD / OFFWIN / NO_WET_CELL); it does NOT touch the curated
    apply input scripts/nwps_okx_assignments.json (promote by hand after review).
    Mac-only (NOMADS); degrades to a clear message offline."""
    if batch:
        want = batch.split(",") if isinstance(batch, str) else list(batch)
        spots = _load_roster_spots(want)   # from spots_enriched.json, real tags, raise on missing slug
    else:
        spots, note = _load_pilot_spots()
        if note:
            print(note)
    print(f"NWPS {wfo.upper()} validate — {len(spots)} spots\n")
    try:
        cycle = load_cycle(wfo)
    except Exception as e:  # noqa: BLE001  NOMADS/cfgrib unavailable here
        print(f"⚠ could not load a {wfo.upper()} cycle ({type(e).__name__}: {e}). "
              "Live NOMADS + cfgrib/eccodes needed — run on the Mac. Offline logic is covered by --selftest.")
        return 0
    print(f"cycle {cycle['cycle_dt']:%Y-%m-%d %HZ}  ·  {len(cycle['steps'])} steps  ·  grid {cycle['lats'].shape}\n")

    # Real fallback baseline = the orientation path via the EXISTING NWPS fetcher
    # (interpret.compute_ratings), so NWPS★ (nearshore node) is asserted against
    # what the spot gets today. Lazy + degrades to NWPS-only sanity if unavailable.
    fb = None
    try:
        from ..interpret import compute_ratings
        from . import nwps as nwps_mod, tides as tides_mod
        # Redirect the fetchers' diagnostic writes to scratch files so a Mac
        # --validate can never clobber the real forecast_data/{nwps,tides}.json.
        _saved = (nwps_mod.NWPS_FORECAST_FILE, tides_mod.TIDES_FORECAST_FILE)
        nwps_mod.NWPS_FORECAST_FILE = _saved[0].parent / "nwps_validate_scratch.json"
        tides_mod.TIDES_FORECAST_FILE = _saved[1].parent / "tides_validate_scratch.json"
        try:
            fb = compute_ratings(spots, nwps_mod.fetch(spots), tides_mod.fetch(spots), {}, {})
        finally:
            nwps_mod.NWPS_FORECAST_FILE, tides_mod.TIDES_FORECAST_FILE = _saved
    except Exception as e:  # noqa: BLE001
        print(f"(fallback baseline unavailable — {type(e).__name__}; showing NWPS sanity only)\n")

    print(f"  {'slug':22}{'node_km':>8}{'swh':>6}{'per':>6}{'dir':>6}  verdict   NWPS★   fb★")
    placed, outcomes = [], []
    for s in spots:
        slug = _slug(s["name"]); wfo_tag = s.get("nwps_wfo", wfo)
        sel = select_node(cycle, s["lat"], s["lng"], s.get("orientation_deg"))
        if sel is None:
            # No water anywhere in the fetched grid near this spot: the spot is
            # OUTSIDE this WFO's marine domain. A DISTINCT outcome from FAR/DEAD/
            # OFFWIN (those found a cell but disqualified it) — NO_WET_CELL means
            # "retry against another WFO grid", not "genuinely off-window".
            print(f"  {slug:22}{'—':>8}  NO_WET_CELL  (no water in {wfo} grid — outside its marine domain)")
            outcomes.append({"slug": slug, "name": s["name"], "nwps_wfo": wfo_tag,
                             "grid_wfo": wfo, "outcome": "NO_WET_CELL",
                             "domain_miss": _is_domain_miss("NO_WET_CELL")})
            continue
        i, j, nlat, nlng, dkm, moved = sel
        swh = _node_value(cycle, "swh", 0, i, j); per = _node_value(cycle, "perpw", 0, i, j)
        dpw = _node_value(cycle, "dirpw", 0, i, j); shts = _node_value(cycle, "shts", 0, i, j)
        v = placement_verdict(dkm, per, dpw, s.get("swell_window_arcs", []))
        # match the fallback's f000 valid hour for a same-hour comparison
        wm = tm = 1.0; fbstar = None
        ents = (fb or {}).get(s["name"]) or []
        k0 = int(cycle["cycle_dt"].timestamp() // 3600)
        for e in ents:
            t = _iso_to_epoch(e.get("valid_time"))
            if t is not None and int(t // 3600) in (k0, k0 - 1, k0 + 1):
                wm, tm, fbstar = e.get("wind_mult", 1.0), e.get("tide_mult", 1.0), e.get("stars")
                break
        st, *_ = nwps_stars(swh, per, dpw, shts, s.get("orientation_deg"), wm, tm)
        sval = f"{st:.1f}" if st is not None else "—"
        fval = f"{fbstar:.1f}" if fbstar is not None else "—"
        dm = _is_domain_miss(v)
        print(f"  {slug:22}{dkm:8.2f}{(swh or 0):6.1f}{(per or 0):6.1f}{(dpw or 0):6.0f}"
              f"  {v:8}{sval:>6}{fval:>6}{'  *' if moved else ''}{'  domain-miss' if dm else ''}")
        # FAR = nearest seaward wet cell beyond FAR_CAP_KM (spot outside this WFO's
        # nearshore nest — a domain miss, like NO_WET_CELL); DEAD = period floor;
        # OFFWIN = swell direction outside the spot's window (in-domain, not a miss).
        # domain_miss is the explicit rollup the grid-edge mop-up filters on.
        outcomes.append({"slug": slug, "name": s["name"], "nwps_wfo": wfo_tag, "grid_wfo": wfo,
                         "outcome": v, "domain_miss": dm, "nwps_node_distance_m": round(dkm * 1000),
                         "nwps_node_lat": round(nlat, 5), "nwps_node_lng": round(nlng, 5)})
        if v == "OK":
            placed.append({"slug": slug, "name": s["name"], "nwps_wfo": wfo_tag,
                           "nwps_grid": "CG1", "nwps_node_lat": round(nlat, 5), "nwps_node_lng": round(nlng, 5),
                           "nwps_node_distance_m": round(dkm * 1000), "nwps_buoy_id": s.get("nwps_buoy_id")})
    # forced-empty test — fallback must engage cleanly
    if spots:
        tname = spots[0]["name"]
        test = {tname: [dict(valid_time="2026-06-27T12:00:00Z", stars=2.5, wind_mult=1.0, tide_mult=1.0)]}
        st = apply_nwps_overrides(test, [dict(spots[0], swell_window_source="nwps")], _fetch=lambda _s: None)
        print(f"\nforced-empty test: fed={st['fed']} fell_back={st['fell_back']} errored={st['errored']}; "
              f"base preserved: {'YES' if test[tname][0]['stars'] == 2.5 else 'NO'}")
    if outcomes:
        validate_out = SCRIPTS_DIR / f"nwps_{wfo}_validate_out.json"   # per-region; no cross-region clobber
        validate_out.write_text(json.dumps(
            {"_comment": f"{wfo} --validate diagnostic. 'spots' = placed-OK (node fields); 'outcomes' = every "
             "spot's category (OK / FAR / DEAD / OFFWIN / NO_WET_CELL). NOT the apply input — review, then "
             "promote OK spots into scripts/nwps_okx_assignments.json by hand.",
             "grid_wfo": wfo, "spots": placed, "outcomes": outcomes}, indent=2))
        n_ok = len(placed); n_other = len(outcomes) - n_ok
        print(f"\nwrote {validate_out} ({n_ok} placed-OK, {n_other} other outcomes on the {wfo} grid) — "
              "diagnostic only. The apply input scripts/nwps_okx_assignments.json is left untouched; review + "
              "promote OK spots into it, then --trustcheck and apply_nwps_assignments --apply once the gate PASSES.")
    print("\nfb★ = the orientation-path baseline (interpret.compute_ratings via the existing NWPS "
          "fetcher) at the same f000 hour, when NWPS+tides fetch succeeds — NWPS★ should be sane "
          "next to it. Trust the WFO (--trustcheck) before apply_nwps_assignments --apply.")
    return 0


def _selftest():
    ok = True

    def check(n, c):
        nonlocal ok; ok = ok and c; print(f"  {'PASS' if c else 'FAIL'}  {n}")

    # window + geometry helpers
    check("in_arcs simple", _in_arcs(120, [{"min": 90, "max": 230}]) and not _in_arcs(300, [{"min": 90, "max": 230}]))
    check("in_arcs wrap 0/360", _in_arcs(10, [{"min": 340, "max": 30}]) and not _in_arcs(180, [{"min": 340, "max": 30}]))
    check("ang_within ±90", _ang_within(200, 180, 90) and not _ang_within(350, 180, 90))
    check("bearing east ≈90", abs(_bearing(40, -74, 40, -73) - 90) < 1)

    # placement verdict (clause-1 replacement)
    check("verdict FAR", placement_verdict(5.0, 10, 150, []) == "FAR")
    check("verdict DEAD (period floor)", placement_verdict(1.0, 2.0, 150, []) == "DEAD")
    check("verdict OFFWIN", placement_verdict(1.0, 10, 300, [{"min": 90, "max": 230}]) == "OFFWIN")
    check("verdict OK", placement_verdict(1.0, 10, 150, [{"min": 90, "max": 230}]) == "OK")
    check("domain_miss rollup", _is_domain_miss("FAR") and _is_domain_miss("NO_WET_CELL")
          and not _is_domain_miss("OFFWIN") and not _is_domain_miss("DEAD") and not _is_domain_miss("OK"))

    # cfgrib land semantics: mask = np.isnan(swh) must drive wet-cell selection
    # (was a masked-array test under pygrib). Load-bearing for the seaward snap.
    la = np.array([[40.0, 40.0], [41.0, 41.0]])
    lo = np.array([[-74.0, -73.0], [-74.0, -73.0]])
    swh_grid = np.array([[1.2, np.nan], [np.nan, 0.8]])   # only (0,0) and (1,1) wet
    wet = _wet_nodes(la, lo, np.isnan(swh_grid))
    check("NaN land mask → only wet cells", sorted((i, j) for _, _, i, j in wet) == [(0, 0), (1, 1)])
    row_lat = np.array([[40.0, 40.0, 40.0]]); row_lng = np.array([[-73.02, -73.01, -73.00]])
    only_east = {"lats": row_lat, "lons": row_lng, "mask": np.isnan(np.array([[np.nan, np.nan, 1.5]]))}
    sel = select_node(only_east, 40.0, -73.0, None)
    check("select_node snaps past NaN land to the wet cell",
          sel is not None and (sel[0], sel[1]) == (0, 2))

    # nwps_stars mirrors mop_stars
    st, face, dg, cm, pq = nwps_stars(2.0, 12, 160, 1.9, 160, 1.0, 1.0)
    st_off, *_ = nwps_stars(2.0, 12, 70, 1.9, 160, 1.0, 1.0)   # 90° off-axis
    check(f"nwps_stars on-axis > off-axis ({st} > {st_off})", st > st_off)
    check("nwps_stars unusable -> None", nwps_stars(None, 12, 160, 1.9, 160)[0] is None)
    st_neutral, *_ = nwps_stars(2.0, 12, 160, 1.9, 160, 1.0, 1.0)
    st_windy, *_ = nwps_stars(2.0, 12, 160, 1.9, 160, 0.5, 1.0)
    check(f"wind_mult injected ({st_windy} < {st_neutral})", st_windy < st_neutral)

    # apply_nwps_overrides — fed / fallback / reversible / error / non-nwps ignored / FULL HORIZON
    base = 1767225600
    spots = [{"name": "T", "swell_window_source": "nwps", "orientation_deg": 160, "nwps_wfo": "okx"}]
    far = base + 100 * 3600    # +100h — within NWPS's 145-hr horizon (unlike MOP near-now)
    ratings = {"T": [
        {"valid_time": "2026-01-01T00:00:00Z", "stars": 1.0, "wind_mult": 1.0, "tide_mult": 1.0},
        {"valid_time": datetime.datetime.utcfromtimestamp(far).strftime("%Y-%m-%dT%H:00:00Z"),
         "stars": 1.0, "wind_mult": 1.0, "tide_mult": 1.0},
    ]}
    series = {int(base // 3600): (2.0, 14, 160, 1.9), int(far // 3600): (2.5, 15, 160, 2.4)}
    stats = apply_nwps_overrides(ratings, spots, _fetch=lambda _s: series)
    e0, e1 = ratings["T"]
    check(f"override fed 1 spot ({stats['fed']})", stats["fed"] == 1)
    check("near hour fed", e0["swell_source"] == "nwps" and e0["stars"] != 1.0)
    check("FULL-HORIZON: +100h hour also fed", e1["swell_source"] == "nwps" and e1["stars"] != 1.0)
    nstats = apply_nwps_overrides({"T": [dict(e0)]}, spots, _fetch=lambda _s: None)
    check("no NWPS -> fell_back, no error", nstats["fell_back"] == 1 and nstats["errored"] == 0)
    estats = apply_nwps_overrides({"T": [dict(e0)]}, spots,
                                  _fetch=lambda _s: (_ for _ in ()).throw(OSError("nomads")))
    check("error -> errored, never raises", estats["errored"] == 1)
    plain = apply_nwps_overrides({"P": [{"valid_time": "2026-01-01T00:00:00Z", "stars": 3.0}]},
                                 [{"name": "P", "swell_window_source": "orientation_derived"}],
                                 _fetch=lambda _s: series)
    check("non-nwps spot ignored", plain["fed"] == 0 and plain["fell_back"] == 0)

    # trust gate math (ported from nwps_okx_buoycheck)
    co = [(h, h * 1.02 + 0.05, 150, 130) for h in (0.8, 1.4, 2.1, 1.7, 1.0, 2.4)]   # tracks + steady offset + range
    v, r, cs, n = trust_verdict(co)
    check(f"trust PASS on co-moving ({v} r={r:.2f} cs={cs:.0f})", v == "PASS" and r >= 0.80 and cs <= 25)
    flat = [(1.0, 1.0 + 0.01 * i, 150, 130) for i in range(8)]   # buoy range < 0.5 m
    check("trust INCONCLUSIVE on flat spell", trust_verdict(flat)[0] == "INCONCLUSIVE")
    check("trust INCONCLUSIVE on few pairs", trust_verdict(co[:3])[0] == "INCONCLUSIVE")
    scatter = [(h, b, 150, d) for h, b, d in
               [(0.8, 2.2, 10), (1.4, 0.6, 200), (2.1, 1.5, 95), (1.7, 2.4, 300), (1.0, 0.7, 20), (2.4, 1.1, 170)]]
    check("trust FAIL on scatter", trust_verdict(scatter)[0] == "FAIL")

    # _buoy_latlng: coordinates resolve from the FULL active-station metadata, not the
    # wave-reporting subset; an unknown id raises; there is never a hardcoded fallback.
    active_fx = [{"id": "44065", "lat": 40.369, "lng": -73.703, "name": "Long Island Sound"}]
    reporting_fx = [{"id": "44025", "lat": 0.0, "lng": 0.0, "name": ""}]   # 44065 present but NOT reporting waves
    check("buoy resolves from metadata even when NOT wave-reporting",
          _buoy_latlng("44065", _active=active_fx, _reporting=reporting_fx) == (40.369, -73.703))

    def _raises(bid, act):
        try:
            _buoy_latlng(bid, _active=act, _reporting=[]); return False
        except KeyError:
            return True
    check("unknown buoy (absent from metadata) raises", _raises("99999", active_fx))
    check("empty metadata raises — never returns typed/hardcoded coords", _raises("44065", []))

    print("\nself-test:", "ALL PASS — NWPS placement, rating, override (full horizon), trust gate sound."
          if ok else "FAILURES")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--validate", action="store_true", help="fetch one WFO cycle, place + sample (Mac)")
    ap.add_argument("--trustcheck", action="store_true", help="NWPS-vs-buoy trust gate (Mac)")
    ap.add_argument("--wfo", default="okx", help="NWPS WFO grid to fetch (default okx)")
    ap.add_argument("--batch", default=None,
                    help="comma-separated slugs to validate, loaded from spots_enriched.json "
                         "(each spot keeps its own nwps_wfo tag); default = the okx_pilot.json set")
    ap.add_argument("--buoy", default="44025", help="trust-check buoy id (default 44025)")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.validate:
        return validate_batch(a.batch, a.wfo)
    if a.trustcheck:
        blat, blng = _buoy_latlng(a.buoy)   # coords from NDBC active-station metadata; raises if unknown (no fallback)
        print(f"=== NWPS {a.wfo.upper()} trust check vs NDBC {a.buoy} (Mac; needs NOMADS+NDBC) ===")
        try:
            v, r, cs, n = trust_check(a.wfo, a.buoy, blat, blng)
            print(f"buoy {a.buoy}: verdict {v}  r={r:.3f}  circ_std={cs:.1f}°  pairs={n}")
            print({"PASS": f"Regional trust supports consuming the placed {a.wfo.upper()} spots.",
                   "FAIL": "Hold consume; investigate before tagging.",
                   "INCONCLUSIVE": "Flat/short window — rerun after a real swell (>0.5 m Hs range)."}.get(v, ""))
        except Exception as e:  # noqa: BLE001
            print(f"⚠ trust check needs live NOMADS+NDBC+cfgrib/eccodes ({type(e).__name__}: {e}) — run on the Mac.")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
