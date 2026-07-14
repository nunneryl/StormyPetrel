"""NWPS CG0_Trkng partitioned-swell reader (Stage 2b — READER ONLY, step 1 of 2).

Sibling of pipeline/forecast/nwps_nearshore.py. That module reads the NWPS **CG1
field** file (swh / shts / dirpw / perpw / ws / wdir …), which genuinely carries
NO swell direction — dirpw is the peak of the WHOLE spectrum (it tracks the
wind-chop when chop > swell). NWPS *also* publishes a wave-system **tracking**
file we had never parsed, which DOES carry per-system swell direction/period/
height (SWAN-refracted, nearshore):

    {region}.{YYYYMMDD}/{wfo}/{HH}/CG0/{wfo}_nwps_CG0_Trkng_{YYYYMMDD}_{HH}00.grib2

Exactly three fields, GRIB `level` = tracked-system index (1,2,3…), `step` =
forecast hour (hourly):
    swdir (10,0,7)  direction of swell waves, °true
    shts  (10,0,8)  significant height of swell
    mpts  (10,0,9)  mean period of swell

This module fetches + parses that file and exposes, per node per hour, the raw
list of tracked systems. It is READER-ONLY: it changes nothing in the rating,
interpret.py, the trust gate, spots_enriched.json, or any forecast output. The
rewiring (rating on the swell system instead of dirpw) is step 2, after we verify
the tracked-system direction against buoy swell partitions.

CRITICAL PARSING RULES (each silently poisons everything if missed):
  1. 9999.0 is the MISSING sentinel, NOT NaN — np.isnan does not catch it. We
     mask (>= 9999) OR (== the message's declared missingValue) and convert to
     NaN at parse time, so no sentinel ever reaches a mean / nearest-cell / query.
  2. Systems are SPARSE and OPTIONAL. A system covers ~⅓ of cells; a system can be
     entirely empty at a step. Absent system ≠ zero swell — it means "no tracked
     system here". Empty systems yield NO entry (never a crash, never a zero).
  3. System index is NOT temporally stable (Hanson–Phillips labels can swap
     between hours). This reader exposes the raw per-system data faithfully in
     system-index order; continuity/tracking is step 2's job, not this reader's.

  python -m pipeline.forecast.nwps_trkng --selftest                    # offline
  python -m pipeline.forecast.nwps_trkng --diag --wfo mhx --buoy 44095  # Mac (NOMADS+NDBC)
"""
from __future__ import annotations

import argparse
import datetime

import numpy as np

# Reuse the CG1 module's NOMADS resolution / fetch / node primitives verbatim —
# no parallel fetch path. (Importing it does NOT import cfgrib; that stays lazy.)
from . import nwps_nearshore as nn

TRKNG_SENTINEL = 9999.0
_TRKNG_SHORTS = ("swdir", "shts", "mpts")
# GRIB2 (discipline, parameterCategory, parameterNumber) → short, for messages whose
# shortName eccodes can't resolve. From the verified eccodes dump of the mhx file.
_PARAM_ID = {(10, 0, 7): "swdir", (10, 0, 8): "shts", (10, 0, 9): "mpts"}


# --------------------------------------------------------------------------- #
# URL (reuse CG1 cycle resolution; only the subdir + filename differ)          #
# --------------------------------------------------------------------------- #
def _trkng_url(wfo, date, cc, region):
    """CG0 Trkng URL for a resolved (date, cc). Lists the CG0 dir to pick the real
    filename (robust to naming quirks); falls back to the verified deterministic
    pattern if the listing is unavailable."""
    cg0 = f"{nn.NOMADS}{region}.{date}/{wfo}/{cc}/CG0/"
    hits = [n for n in nn._listdir(cg0) if "_CG0_Trkng_" in n and n.endswith(".grib2")]
    if hits:
        return cg0 + sorted(hits)[-1]
    return cg0 + f"{wfo}_nwps_CG0_Trkng_{date}_{cc}00.grib2"


# --------------------------------------------------------------------------- #
# GRIB message I/O (eccodes; lazy — --selftest and the pure parse never call it) #
# --------------------------------------------------------------------------- #
def _parse_step(s):
    try:
        return int(str(s).split("-")[-1])
    except (TypeError, ValueError):
        return None


def _read_trkng_messages(path):
    """(lats2D, lons2D, records) where records = [(short, system, fh, values2D, missing)].
    Values are RAW (sentinel NOT yet masked — parse_trkng does that, so the masking is
    unit-tested). Geolocation is taken once from the first message's grid definition
    (all messages share the grid). Regular lat/lon nest (NWPS CG grids), lon → -180/180.
    Lazy eccodes import; exercised on the Mac (needs eccodes + the real file)."""
    from eccodes import (  # noqa: PLC0415 — lazy so the pure reader/tests need no eccodes
        codes_grib_new_from_file, codes_release, codes_get, codes_get_long,
        codes_get_double, codes_get_values,
    )
    lats = lons = None
    records = []
    with open(path, "rb") as fh_file:
        while True:
            gid = codes_grib_new_from_file(fh_file)
            if gid is None:
                break
            try:
                try:
                    short = codes_get(gid, "shortName")
                except Exception:  # noqa: BLE001
                    short = None
                if short not in _TRKNG_SHORTS:
                    key = (codes_get_long(gid, "discipline"),
                           codes_get_long(gid, "parameterCategory"),
                           codes_get_long(gid, "parameterNumber"))
                    short = _PARAM_ID.get(key)
                if short not in _TRKNG_SHORTS:
                    continue
                system = codes_get_long(gid, "level")
                try:
                    fh = codes_get_long(gid, "step")
                except Exception:  # noqa: BLE001
                    fh = _parse_step(codes_get(gid, "stepRange"))
                try:
                    missing = codes_get_double(gid, "missingValue")
                except Exception:  # noqa: BLE001
                    missing = TRKNG_SENTINEL
                ni = codes_get_long(gid, "Ni")     # points along a parallel (lon)
                nj = codes_get_long(gid, "Nj")     # points along a meridian (lat)
                if lats is None:                   # geolocate once (all messages share the grid)
                    lats, lons = _latlon_axes(
                        ni, nj,
                        codes_get_double(gid, "latitudeOfFirstGridPointInDegrees"),
                        codes_get_double(gid, "longitudeOfFirstGridPointInDegrees"),
                        codes_get_double(gid, "iDirectionIncrementInDegrees"),
                        codes_get_double(gid, "jDirectionIncrementInDegrees"),
                        codes_get_long(gid, "scanningMode"))
                vals = np.asarray(codes_get_values(gid), dtype="float64").reshape(nj, ni)
                records.append((short, system, fh, vals, missing))
            finally:
                codes_release(gid)
    if lats is None:
        raise OSError(f"no swdir/shts/mpts messages in Trkng file: {path}")
    return lats, lons, records


def _latlon_axes(ni, nj, la1, lo1, di, dj, scanning_mode):
    """2-D (lat, lon) meshes for a regular_ll grid, from its GRIB grid-definition
    values. scanningMode is decoded EXPLICITLY (GRIB2 flag table 3.4) rather than
    assuming an order — getting it wrong silently flips the grid N–S:
        bit 0x80 iScansNegatively   (i runs east→west)
        bit 0x40 jScansPositively   (j runs south→north)
        bit 0x20 jPointsAreConsecutive (column-major storage)
    NWPS Trkng is scanningMode=64 → j south→north (lat ascending from
    latitudeOfFirstGridPoint, e.g. 33.85→36.6), i west→east, i fastest. The
    (unexpected) j-consecutive layout would break the reshape(nj, ni) in the caller,
    so we fail loudly instead of mis-ordering. lon → the app's -180/180 convention
    (matches load_cycle). Pure/offline — unit-tested against the real mhx keys."""
    if scanning_mode & 0x20:
        raise NotImplementedError(
            f"scanningMode={scanning_mode}: jPointsAreConsecutive (column-major) — the "
            "value reshape(nj, ni) assumes i-fastest. Add a transpose if NWPS ships this.")
    i_neg = bool(scanning_mode & 0x80)
    j_pos = bool(scanning_mode & 0x40)
    lon_axis = lo1 + (-di if i_neg else di) * np.arange(ni, dtype="float64")
    lat_axis = la1 + (dj if j_pos else -dj) * np.arange(nj, dtype="float64")
    lat2d, lon2d = np.meshgrid(lat_axis, lon_axis, indexing="ij")
    lon2d = ((lon2d + 180.0) % 360.0) - 180.0
    return lat2d, lon2d


# --------------------------------------------------------------------------- #
# Pure parse + query (unit-tested; no eccodes, no network)                     #
# --------------------------------------------------------------------------- #
def parse_trkng(lats, lons, cycle_dt, records, *, sentinel=TRKNG_SENTINEL,
                horizon_max_fh=nn.HORIZON_MAX_FH):
    """Build a Trkng cycle dict from raw records. Applies the sentinel/missingValue
    mask HERE (converting to NaN) so no 9999 survives into any value. Returns:
      {lats, lons, mask, cycle_dt, systems:[…], steps:[…], shape,
       data:{(system, fh): {short: 2D float64 with NaN for masked/missing}}}
    `mask` (True = never any tracked swell here) drives the seaward node snap when the
    Trkng grid differs from CG1. Pure/offline."""
    lats = np.asarray(lats, dtype="float64")
    lons = np.asarray(lons, dtype="float64")
    data, systems, steps = {}, set(), set()
    for short, system, fh, vals, missing in records:
        if fh is None or fh > horizon_max_fh:
            continue
        arr = np.asarray(vals, dtype="float64")
        bad = ~np.isfinite(arr) | (arr >= sentinel)   # rule 1: 9999 is NOT NaN
        if missing is not None and np.isfinite(missing):
            bad = bad | (arr == missing)
        arr = np.where(bad, np.nan, arr)
        data.setdefault((system, fh), {})[short] = arr
        systems.add(system)
        steps.add(fh)
    any_data = np.zeros(lats.shape, dtype=bool)
    for d in data.values():
        sw = d.get("swdir")
        if sw is not None:
            any_data |= np.isfinite(sw)
    return {"lats": lats, "lons": lons, "mask": ~any_data, "cycle_dt": cycle_dt,
            "systems": sorted(systems), "steps": sorted(steps), "shape": lats.shape,
            "data": data}


def _cell(arr, i, j):
    if arr is None:
        return None
    v = float(arr[i, j])
    return None if v != v else v   # NaN → None


def trkng_systems_at(cycle, i, j, fh):
    """Raw list of tracked swell systems at grid cell (i, j) for forecast hour *fh*:
        [{"system": 1, "hs": 0.61, "tp": 10.5, "dir": 138.1}, …]
    In system-index order (faithful; NO continuity/dominance assumed — that's step 2).
    A system is EMITTED only where it has real swell direction AND height at this cell
    (rule 2: absent/masked systems are omitted, never zero-filled); tp is None if the
    period alone is masked. Never returns a sentinel value (rule 1)."""
    out = []
    for sysx in cycle["systems"]:
        d = cycle["data"].get((sysx, fh))
        if not d:
            continue
        dr = _cell(d.get("swdir"), i, j)
        hs = _cell(d.get("shts"), i, j)
        if dr is None or hs is None:      # no tracked system present at this cell/hour
            continue
        out.append({"system": sysx, "hs": hs, "tp": _cell(d.get("mpts"), i, j), "dir": dr})
    return out


# --------------------------------------------------------------------------- #
# Node reconciliation (task 3): Trkng grid vs CG1 grid                          #
# --------------------------------------------------------------------------- #
def _shape(cycle):
    """Grid shape from the lat array — robust to cycle dicts that carry no 'shape' key
    (nwps_nearshore.load_cycle's CG1 dict does not)."""
    return np.asarray(cycle["lats"]).shape


def _grids_coincident(a, b, *, tol=1e-4):
    """True when two cycle dicts share the SAME lat/lon grid (identical shape and
    coincident coordinates within *tol*°). Shape is read from the lat array, not a
    'shape' key. Only then does a CG1 (i,j) index the Trkng grid directly."""
    if _shape(a) != _shape(b):
        return False
    return bool(np.nanmax(np.abs(a["lats"] - b["lats"])) < tol
                and np.nanmax(np.abs(a["lons"] - b["lons"])) < tol)


def _same_domain(a, b, *, tol=0.02):
    """True when two grids cover the SAME geographic footprint (coincident bounding
    boxes) even at different resolution — the verified CG0-vs-CG1 case (identical
    origin/extent, CG0 ~2.77× coarser)."""
    return bool(abs(np.nanmin(a["lats"]) - np.nanmin(b["lats"])) < tol
                and abs(np.nanmax(a["lats"]) - np.nanmax(b["lats"])) < tol
                and abs(np.nanmin(a["lons"]) - np.nanmin(b["lons"])) < tol
                and abs(np.nanmax(a["lons"]) - np.nanmax(b["lons"])) < tol)


def trkng_node(trkng_cycle, cg1_cycle, node_lat, node_lng):
    """(i, j, why) into the TRKNG grid for a spot whose CG1 node sits at
    (node_lat, node_lng). Same grid → reuse the index. Different resolution → an
    EXPLICIT nearest-cell remap computed FROM COORDINATES (never an index ratio — the
    CG0:CG1 ratio ~2.77 is not integer), with the crossing distance surfaced in *why*.
    For the verified CG0/CG1 pair this is a within-domain resolution offset (≤ ~half a
    CG0 cell, ~2.5 km), NOT a domain mismatch. Returns (None, None, why) if the Trkng
    grid carries no tracked swell at all."""
    if _grids_coincident(trkng_cycle, cg1_cycle):
        cell = nn._nearest_cell(cg1_cycle, node_lat, node_lng)
        if cell is None:
            return None, None, "same grid as CG1, but no cell found"
        return cell[0], cell[1], "same grid as CG1 (index reused; identical cell)"
    cell = nn._nearest_cell(trkng_cycle, node_lat, node_lng)   # nearest tracked cell BY COORDS
    if cell is None:
        return None, None, "Trkng grid has no tracked swell to sample"
    frame = ("SAME domain as CG1, coarser resolution — a within-domain resolution step"
             if _same_domain(trkng_cycle, cg1_cycle)
             else "DIFFERENT footprint from CG1 — verify the domain before trusting")
    return (cell[0], cell[1],
            f"Trkng grid {_shape(trkng_cycle)} vs CG1 {_shape(cg1_cycle)}: {frame}; "
            f"tracked cell {cell[2]:.2f} km from the CG1 node")


# --------------------------------------------------------------------------- #
# Per-spot exposure (alongside the CG1 fields; does NOT replace them)           #
# --------------------------------------------------------------------------- #
def trkng_systems_by_hour(spot, cg1_cycle, trkng_cycle):
    """({valid_hour_bucket: [systems]}, why) for a spot, sampled at the SAME node the
    CG1 path uses (baked seaward nwps_node_lat/lng, else select_node) so the partition
    data and the CG1 height/dir refer to the same geographic point. Hours with no
    tracked system are omitted. READER-ONLY — does not touch the spot or the rating."""
    nlat, nlng = spot.get("nwps_node_lat"), spot.get("nwps_node_lng")
    if nlat is not None and nlng is not None:
        node_lat, node_lng = float(nlat), float(nlng)
    else:
        sel = nn.select_node(cg1_cycle, spot["lat"], spot["lng"], spot.get("orientation_deg"))
        if sel is None:
            return {}, "no CG1 wet node for spot"
        node_lat, node_lng = sel[2], sel[3]
    ti, tj, why = trkng_node(trkng_cycle, cg1_cycle, node_lat, node_lng)
    if ti is None:
        return {}, why
    cdt = trkng_cycle["cycle_dt"]
    out = {}
    for fh in trkng_cycle["steps"]:
        systems = trkng_systems_at(trkng_cycle, ti, tj, fh)
        if systems:
            valid = int((cdt + datetime.timedelta(hours=fh)).timestamp() // 3600)
            out[valid] = systems
    return out, why


# --------------------------------------------------------------------------- #
# Live loader (reuses CG1 cycle resolution + fetch)                             #
# --------------------------------------------------------------------------- #
def load_trkng_cycle(wfo, cycle=None, region=None):
    """Fetch + parse the CG0 Trkng file for *wfo*. *cycle* = a (date, cc, url) tuple
    (e.g. from nn.find_latest_cycle / nn.recent_cycles) so it pairs with the SAME CG1
    cycle; None → latest. Raises on fetch/parse failure (callers catch). Mac-only
    (NOMADS + eccodes)."""
    import os
    region = region or nn._region_for(wfo)
    if cycle is None:
        cycle = nn.find_latest_cycle(wfo, region)
        if not cycle:
            raise OSError(f"no recent cycle for {wfo}")
    date, cc = cycle[0], cycle[1]
    url = _trkng_url(wfo, date, cc, region)
    body = nn._http_get(url)
    if body[:4] != b"GRIB":
        raise OSError(f"not GRIB (Trkng file missing?): {url}")
    path = os.path.join("/tmp", f"nwps_{wfo}_{date}_{cc}_CG0_Trkng.grib2")
    with open(path, "wb") as f:
        f.write(body)
    lats, lons, records = _read_trkng_messages(path)
    return parse_trkng(lats, lons, nn._cycle_dt(date, cc), records)


# --------------------------------------------------------------------------- #
# Diagnostic (task 4): CG1 dirpw vs tracked systems vs buoy swell partition     #
# --------------------------------------------------------------------------- #
def _spec_by_hour(buoy_id):
    """{hour_bucket: {"swh","swp","swd"}} from the buoy .spec feed (swell partition:
    height / period / direction). Reuses the buoys parser; read-only; None-safe."""
    try:
        from .buoys import _fetch_text, _parse_realtime2, _SPEC_FIELDS
    except Exception:  # noqa: BLE001
        return {}
    txt = _fetch_text(f"https://www.ndbc.noaa.gov/data/realtime2/{buoy_id.upper()}.spec",
                      buoy_id, "spec", use_cache=False)
    out = {}
    if not txt:
        return out
    for o in _parse_realtime2(txt, _SPEC_FIELDS):
        t = nn._iso_to_epoch(o.get("time"))
        if t is None:
            continue
        out[int(t // 3600)] = {"swh": o.get("swell_height_m"), "swp": o.get("swell_period_s"),
                               "swd": o.get("swell_dir_deg")}
    return out


def _sys_str(systems, idx):
    """'hs/tp/dir' for the idx-th tracked system (by system index), or dashes."""
    if idx < len(systems):
        s = systems[idx]
        tp = f"{s['tp']:.1f}" if s.get("tp") is not None else "—"
        return f"{s['hs']:.2f}/{tp}/{s['dir']:.0f}"
    return "   —   "


def diag_compare(wfo, buoy_id, blat, blng, max_rows=48):
    """Hour-by-hour: CG1 dirpw | Trkng sys1 (hs/tp/dir) | Trkng sys2 | buoy MWD |
    buoy SwH/SwP/SwD. Samples CG1 dirpw and the tracked systems at the SAME geographic
    node (the buoy's CG1 nearest cell, remapped into the Trkng grid). Read-only. The
    buoy is pre-resolved to (blat, blng) by the caller so an unknown-buoy KeyError can't
    be confused with a code bug in here."""
    cyc = nn.find_latest_cycle(wfo, nn._region_for(wfo))
    if not cyc:
        print(f"no recent cycle for {wfo}")
        return 1
    cg1 = nn.load_cycle(wfo, cyc)
    trk = load_trkng_cycle(wfo, cyc)
    pcell = nn._nearest_cell(cg1, blat, blng)
    ci, cj = pcell[0], pcell[1]
    node_lat, node_lng = float(cg1["lats"][ci, cj]), float(cg1["lons"][ci, cj])
    ti, tj, why = trkng_node(trk, cg1, node_lat, node_lng)

    print(f"=== NWPS CG0_Trkng vs CG1 dirpw vs buoy {buoy_id} ({wfo}) ===")
    print(f"cycle {cyc[0]} {cyc[1]}Z | CG1 grid {_shape(cg1)} | Trkng grid {_shape(trk)} "
          f"| systems tracked: {trk['systems']}")
    print(f"node reconciliation: {why}")
    if ti is None:
        print("→ no Trkng node — cannot compare.")
        return 1
    print(f"CG1 node {node_lat:.4f},{node_lng:.4f} (buoy {pcell[2]:.2f} km) | "
          f"Trkng node {float(trk['lats'][ti,tj]):.4f},{float(trk['lons'][ti,tj]):.4f}\n")

    std = nn._buoy_hourly(buoy_id) or {}
    spec = _spec_by_hour(buoy_id)
    from . import ndbc_spectral as ndbc_spec
    spectral = ndbc_spec.by_hour(buoy_id)   # {epoch_hour: metrics} — degree-valued swell dir
    if not spectral:
        print("note: no directional spectra (.data_spec/.swdir) for this buoy — the NEW/CONTROL "
              "spectral columns are blank (station may not report directional waves).")

    def _n(v, s="{:.0f}"):
        return s.format(v) if isinstance(v, (int, float)) and v == v else "—"

    print(f"  {'valid(fh)':>11} {'dirpw':>5} {'sys1 hs/tp/dir':>14} {'sys2 hs/tp/dir':>14} "
          f"{'MWD':>4} {'.spec H/P/D':>13} {'SPECTRAL Hsw/dir/frac/totdir':>28}")
    # paired series for the comparisons (task 3), over hours both sides cover
    old_m, old_b, new_m, new_b, ctl_m, ctl_b, ref_m, ref_b, fracs = ([] for _ in range(9))
    hs_sp, hs_ref = [], []   # spectral Hs_swell vs the buoy's own .spec SwH (band-split sanity, task 2)
    shown = 0
    for fh in cg1["steps"]:
        if shown >= max_rows:
            print(f"  … ({max_rows} rows shown; rerun with a larger --rows for more)")
            break
        dirpw = nn._node_value(cg1, "dirpw", fh, ci, cj)
        dirpw = dirpw if (dirpw is not None and dirpw == dirpw) else None
        systems = trkng_systems_at(trk, ti, tj, fh)
        sys1_dir = systems[0]["dir"] if systems else None
        valid = int((cg1["cycle_dt"] + datetime.timedelta(hours=fh)).timestamp() // 3600)
        b, sp, spx = std.get(valid), spec.get(valid), spectral.get(valid)
        mwd = b.get("mwd") if b else None
        buoy_sw = (f"{_n(sp.get('swh'),'{:.2f}')}/{_n(sp.get('swp'),'{:.1f}')}/{_n(sp.get('swd'))}"
                   if sp else "     —     ")
        if spx:
            spec_col = (f"{_n(spx['hs_swell'],'{:.2f}')}/{_n(spx['swell_dir'])}/"
                        f"{_n(spx['swell_frac'],'{:.2f}')}/{_n(spx['total_mean_dir'])}")
            fracs.append(spx.get("swell_frac"))
        else:
            spec_col = "          —          "
        # accumulate the three paired comparisons (only where both sides are present)
        if dirpw is not None and mwd is not None:
            old_m.append(dirpw); old_b.append(mwd)
        if sys1_dir is not None and spx and spx.get("swell_dir") is not None:
            new_m.append(sys1_dir); new_b.append(spx["swell_dir"])
        if dirpw is not None and spx and spx.get("total_mean_dir") is not None:
            ctl_m.append(dirpw); ctl_b.append(spx["total_mean_dir"])
        if sys1_dir is not None and sp and sp.get("swd") is not None:
            ref_m.append(sys1_dir); ref_b.append(sp["swd"])   # vs the coarse 22.5°-binned SwD
        if spx and sp and sp.get("swh") is not None:
            hs_sp.append(spx["hs_swell"]); hs_ref.append(sp["swh"])
        print(f"  {valid:>7}({fh:>3}) {_n(dirpw):>5} {_sys_str(systems,0):>14} "
              f"{_sys_str(systems,1):>14} {_n(mwd):>4} {buoy_sw:>13} {spec_col:>28}")
        shown += 1

    # THE EXPERIMENT (task 3): is the partition-matched, degree-valued comparison tighter?
    print("\n==== the experiment — paired-hour direction agreement (lower circ_std = tighter) ====")

    def _cmp(label, m, b):
        n, md, cs = ndbc_spec.delta_stats(m, b)
        print(f"  {label:<50} n={n:>3}  meanΔ {(_n(md,'{:+.1f}')):>6}°  circ_std {(_n(cs,'{:.1f}')):>5}°")

    _cmp("OLD   model dirpw    vs buoy MWD  (today's gate)", old_m, old_b)
    _cmp("NEW   model sys1 dir vs buoy spectral swell_dir", new_m, new_b)
    _cmp("REF   model sys1 dir vs buoy coarse .spec SwD", ref_m, ref_b)   # NEW vs REF = quantization gain
    _cmp("CTRL  model dirpw    vs buoy spectral total dir", ctl_m, ctl_b)
    # band-split sanity (task 2): our Hs_swell must track the buoy's OWN .spec SwH, else
    # the split is wrong. (Both use NDBC's separation frequency, so they should match.)
    if hs_sp:
        d = [abs(a - b) for a, b in zip(hs_sp, hs_ref)]
        verdict = "band split looks right" if (sum(d) / len(d)) <= 0.25 else "BAND SPLIT SUSPECT — investigate"
        print(f"\n  band-split check: mean Hs_swell(spectral) {sum(hs_sp)/len(hs_sp):.2f} m vs "
              f".spec SwH {sum(hs_ref)/len(hs_ref):.2f} m, mean|Δ| {sum(d)/len(d):.2f} m → {verdict}")
    fr = [f for f in fracs if isinstance(f, (int, float)) and f == f]
    if fr:
        avg = sum(fr) / len(fr)
        state = ("SWELL-DOMINATED — thesis won't bite here; need a mixed sea" if avg >= 0.6
                 else "MIXED — the discriminating case" if avg >= 0.4
                 else "WIND-SEA-DOMINATED — the discriminating case")
        print(f"  mean swell fraction ≈ {avg:.2f} over shown hours → {state}.")
    print("\n(Read-only: rating, trust gate, interpret.py, spots_enriched.json all untouched.)")
    return 0


# --------------------------------------------------------------------------- #
# Offline selftest (synthetic records — no eccodes, no NOMADS)                  #
# --------------------------------------------------------------------------- #
def _selftest():
    ok = True

    def check(msg, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(("  PASS " if cond else "  FAIL ") + msg)

    # a 2×2 grid; two hours; systems 1 & 2 real at (0,0); system 3 all-sentinel at fh0.
    lats = np.array([[40.0, 40.0], [39.98, 39.98]])
    lons = np.array([[-73.0, -72.98], [-73.0, -72.98]])
    cdt = datetime.datetime(2026, 7, 13, 12, tzinfo=datetime.timezone.utc)
    S = TRKNG_SENTINEL

    def grid(v00, rest=S):
        a = np.full((2, 2), rest, dtype="float64")
        a[0, 0] = v00
        return a

    records = [
        # system 1 present at (0,0): dir 138.1, hs 0.61, tp 10.5 — elsewhere sentinel
        ("swdir", 1, 0, grid(138.1), S), ("shts", 1, 0, grid(0.61), S), ("mpts", 1, 0, grid(10.5), S),
        # system 2 present at (0,0): dir 58.3, hs 0.12, tp 15.2
        ("swdir", 2, 0, grid(58.3), S), ("shts", 2, 0, grid(0.12), S), ("mpts", 2, 0, grid(15.2), S),
        # system 3 ENTIRELY sentinel at fh0 (empty system — rule 2)
        ("swdir", 3, 0, np.full((2, 2), S)), ("shts", 3, 0, np.full((2, 2), S)),
        ("mpts", 3, 0, np.full((2, 2), S)),
        # fh1: system 1 with a declared missingValue (1e20) at (0,0) → masked; real at (1,1)
        ("swdir", 1, 1, _at((2, 2), (1, 1), 200.0, fill=1e20), 1e20),
        ("shts", 1, 1, _at((2, 2), (1, 1), 0.4, fill=1e20), 1e20),
        ("mpts", 1, 1, _at((2, 2), (1, 1), 9.0, fill=1e20), 1e20),
        # system 2 fh1: swdir real at (0,0) but shts SENTINEL → partial, must be OMITTED
        ("swdir", 2, 1, grid(70.0), S), ("shts", 2, 1, np.full((2, 2), S)), ("mpts", 2, 1, grid(12.0), S),
    ]
    # records with a 4-tuple (no explicit missing) default to the sentinel
    records = [(r if len(r) == 5 else (*r, S)) for r in records]
    cyc = parse_trkng(lats, lons, cdt, records)

    check("structure: systems sorted [1,2,3], steps [0,1]",
          cyc["systems"] == [1, 2, 3] and cyc["steps"] == [0, 1])
    at00 = trkng_systems_at(cyc, 0, 0, 0)
    check("decode: cell (0,0) fh0 → sys1 then sys2, in index order",
          [s["system"] for s in at00] == [1, 2])
    check("decode: sys1 values exact (hs 0.61, tp 10.5, dir 138.1)",
          at00[0] == {"system": 1, "hs": 0.61, "tp": 10.5, "dir": 138.1})
    check("rule 1: no returned value is the 9999 sentinel",
          all(v != S for s in at00 for v in (s["hs"], s["tp"], s["dir"])))
    check("rule 1: sentinel cell (0,1) fh0 → no systems (all masked, not 9999)",
          trkng_systems_at(cyc, 0, 1, 0) == [])
    check("rule 2: empty system 3 yields NO entry anywhere at fh0 (no crash, no zero)",
          all(3 not in [s["system"] for s in trkng_systems_at(cyc, i, j, 0)]
              for i in range(2) for j in range(2)))
    check("rule 1: declared missingValue (1e20) masked → sys1 absent at (0,0) fh1",
          trkng_systems_at(cyc, 0, 0, 1) == [])
    check("rule 1: sys1 real at (1,1) fh1 (dir 200, hs 0.4)",
          trkng_systems_at(cyc, 1, 1, 1) == [{"system": 1, "hs": 0.4, "tp": 9.0, "dir": 200.0}])
    check("partial system (dir real, height masked) is OMITTED at (0,0) fh1",
          all(s["system"] != 2 for s in trkng_systems_at(cyc, 0, 0, 1)))
    check("mask: True where no tracked swell ever (cell (1,0) never has data)",
          cyc["mask"][1, 0] and not cyc["mask"][0, 0])

    # _latlon_axes against the REAL mhx grid keys (closes the eccodes seam offline):
    # scanningMode=64 → lat ASCENDING 33.85→36.6; lon 282→285.25 → −78.0→−74.75.
    la, lo = _latlon_axes(61, 62, 33.85, 282.0, 0.054167, 0.045082, 64)
    check("axes: shape (Nj,Ni)=(62,61)", la.shape == (62, 61))
    check("axes: lat row 0 is the SOUTH edge 33.85 (scanningMode=64 → ascending)",
          abs(la[0, 0] - 33.85) < 1e-6 and la[-1, 0] > la[0, 0])
    check("axes: lat last row ≈ 36.6 (33.85 + 61·0.045082)", abs(la[-1, 0] - 36.6) < 1e-3)
    check("axes: lon 282→−78 … 285.25→−74.75 (0/360 → −180/180)",
          abs(lo[0, 0] + 78.0) < 1e-6 and abs(lo[0, -1] + 74.75) < 1e-3)
    check("axes: jPointsAreConsecutive (bit 0x20) fails loudly, not silently",
          _raises_notimpl(lambda: _latlon_axes(61, 62, 33.85, 282.0, 0.054, 0.045, 64 | 0x20)))

    # node reconciliation — CG1 dicts here carry NO 'shape' key, exactly like the real
    # nwps_nearshore.load_cycle output (regression for the KeyError('shape') bug).
    cg1_same = {"lats": lats, "lons": lons, "mask": np.zeros((2, 2), bool), "cycle_dt": cdt}
    check("coincident grids detected (shape read from lats, no 'shape' key)",
          _grids_coincident(cyc, cg1_same))
    ti, tj, why = trkng_node(cyc, cg1_same, 40.0, -73.0)
    check("same-grid node reuses index (0,0) and says so",
          (ti, tj) == (0, 0) and "same grid" in why.lower())
    cg1_diff = {"lats": np.array([[40.0]]), "lons": np.array([[-73.0]]),
                "mask": np.zeros((1, 1), bool), "cycle_dt": cdt}
    _, _, why2 = trkng_node(cyc, cg1_diff, 40.0, -73.0)
    check("different-resolution remap flagged explicitly (not silent), by coords",
          "cg1 node" in why2.lower() and ("footprint" in why2.lower() or "domain" in why2.lower()))

    # per-spot exposure honours a baked seaward node + omits empty hours
    spot = {"lat": 40.0, "lng": -73.0, "nwps_node_lat": 40.0, "nwps_node_lng": -73.0}
    by_hour, _why = trkng_systems_by_hour(spot, cg1_same, cyc)
    v0 = int((cdt + datetime.timedelta(hours=0)).timestamp() // 3600)
    check("per-spot: fh0 bucket carries the two systems", len(by_hour.get(v0, [])) == 2)

    print("\nself-test:", "ALL PASS — Trkng reader sound (offline)." if ok else "FAILURES")
    return 0 if ok else 1


def _at(shape, cell, val, fill=TRKNG_SENTINEL):
    a = np.full(shape, fill, dtype="float64")
    a[cell] = val
    return a


def _raises_notimpl(fn):
    try:
        fn()
        return False
    except NotImplementedError:
        return True


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true", help="offline synthetic-record check (no NOMADS)")
    ap.add_argument("--diag", action="store_true", help="CG1 dirpw vs tracked systems vs buoy (Mac)")
    ap.add_argument("--wfo", default="mhx")
    ap.add_argument("--buoy", default="44095")
    ap.add_argument("--rows", type=int, default=48, help="max hours to print in --diag")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.diag:
        from urllib.error import HTTPError, URLError
        # Resolve the buoy first: its KeyError ("unknown station") is distinct from a
        # code-bug KeyError, so handle it on its own and with a truthful message.
        try:
            blat, blng = nn._buoy_latlng(a.buoy)
        except (KeyError, HTTPError, URLError, OSError) as e:
            print(f"⚠ buoy {a.buoy} not resolvable ({type(e).__name__}: {e}) — needs NDBC "
                  "station metadata present (run on the Mac).")
            return 0
        # ONLY genuine environment failures (no NOMADS egress, no eccodes, missing file)
        # get the "run on the Mac" message. Any OTHER exception propagates as a real
        # traceback — a code bug must never masquerade as an environment problem.
        try:
            return diag_compare(a.wfo, a.buoy, blat, blng, max_rows=a.rows)
        except (HTTPError, URLError, OSError, ImportError) as e:
            print(f"⚠ --diag needs live NOMADS + eccodes ({type(e).__name__}: {e}) — run on the Mac.")
            return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
