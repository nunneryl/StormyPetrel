"""NWPS cells: what the CG1 grid holds around a spot's sample point, and which spots read far
below their neighbours. READ-ONLY (sw1-raycast mode nwps-cells, run 58 onward).

It SELECTs from the spots and forecasts tables, reads NWPS cycles from NOMADS, and writes
nothing to Supabase. The only files it writes are its own report (--out, --csv).

    python -m pipeline.nwps_cells [--spots SLUG,...] [--roster ROSTER.json] [--since ISO]
        [--cycles N] [--out OUT.json] [--csv CELLS.csv]

THE QUESTION (Hatteras, 2026-09-23 17-19Z). Rodanthe, Salvo and S-Turns published NWPS Hs of
0.14 to 0.58 m while Outer Banks Fishing Pier, 35 km north on the same MHX grid, read about
3.1 m. Their three sample points share one grid column (lng -75.45482). By ETOPO 2022 each sits
in 10 to 13 m of open ocean about 800 m off the beach, as the pier's does, so the question is
what NWPS's own grid holds there.

1. THE ARCHIVE (since --since, default 2026-09-07). On the hours NWPS sized (swell_source nwps
   or nwps_height_ww3_dir), the stored hs is the NWPS Hs at the spot's sample cell.
     pairs       each PAIRS spot against its reference, hour for hour: how many hours, how
                 many under LOW_RATIO (0.4) of the reference, the median ratio; all paired hours
                 and those with the reference at MIN_REF_M (1 m) or more, by month.
     neighbours  every NWPS spot against the median of the other NWPS spots on its office's
                 grid whose sample points lie within NEIGHBOUR_KM (30 km), on hours where at
                 least two neighbours have a height and their median is MIN_REF_M or more:
                 the median ratio and the share of hours under LOW_RATIO. A spot is FLAGGED
                 with at least MIN_HOURS such hours and a quarter or more of them under 0.4.
2. THE GRID (the --cycles most recent cycles NOMADS holds, per office). For the --spots
   (default the four Hatteras spots) and the MAX_FLAGGED_CELLS most often low flagged spots:
     the sample cell     the cell nwps.fetch reads: the nearest grid row and column to the
                         stored point, the same argmin;
     the window          every cell within WINDOW cells of it: centre, wet or dry (NaN Hs at
                         f000, the loader's land mask), and per cycle the mean and maximum of
                         NWPS's Hs (swh), peak period (perpw) and direction (dirpw) over the
                         cycle's hours;
     the inventory       every field the cycle's GRIB carries (shortName, name, units), and,
                         for any field that is not one of the six the loader keeps, its value at
                         its first step in each window cell: if NWPS ships its bathymetry or a
                         depth, it is there.
   Depth from ETOPO is not read here: the runner could not reach NOAA's ERDDAP in run 49. The
   report gives each cell's centre so the depth can be joined afterwards.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median

NWPS_KINDS = ("nwps", "nwps_height_ww3_dir")
HATTERAS = ("rodanthe", "salvo", "s-turns", "outer-banks-fishing-pier")
PAIRS = (("rodanthe", "outer-banks-fishing-pier"), ("salvo", "outer-banks-fishing-pier"),
         ("s-turns", "outer-banks-fishing-pier"))
LOW_RATIO = 0.4
MIN_REF_M = 1.0
NEIGHBOUR_KM = 30.0
MIN_NEIGHBOURS = 2
MIN_HOURS = 24
FLAG_SHARE = 0.25
WINDOW = 3
MAX_FLAGGED_CELLS = 12
LOADER_FIELDS = ("swh", "shts", "perpw", "dirpw", "ws", "wdir")
SINCE = "2026-09-07T00:00:00+00:00"


def _slug(name):
    from .enrich import _slug_for
    return _slug_for(name)


def _km(lat1, lon1, lat2, lon2):
    p = math.pi / 180.0
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lon2 - lon1) * p / 2) ** 2)
    return 12742.0 * math.asin(math.sqrt(a))


def _num(v):
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


# --------------------------------------------------------------------------- #
# 1. The archive (pure)                                                        #
# --------------------------------------------------------------------------- #
def heights(rows):
    """{valid_time: hs} of the NWPS-sized hours among *rows* (forecast rows)."""
    out = {}
    for r in rows:
        hs = _num(r.get("hs"))
        if r.get("swell_source") in NWPS_KINDS and hs is not None:
            out[r["valid_time"]] = hs
    return out


def _under(x):
    """Under LOW_RATIO; a ratio that is LOW_RATIO but for float rounding (1.2 / 3.0 is
    0.39999999999999997) is not."""
    return x < LOW_RATIO - 1e-9


def _summary(ratios):
    return {"hours": len(ratios), "under_0_4": sum(_under(x) for x in ratios),
            "share_under_0_4": round(sum(_under(x) for x in ratios) / len(ratios), 3)
            if ratios else None,
            "median_ratio": round(median(ratios), 3) if ratios else None}


def pair(spot_hs, ref_hs):
    """The spot's NWPS Hs over the reference's, hour for hour: all paired hours, those with
    the reference at MIN_REF_M or more, and the latter by month."""
    both = sorted(set(spot_hs) & set(ref_hs))
    all_r = [spot_hs[t] / ref_hs[t] for t in both if ref_hs[t] > 0]
    big = [(t, spot_hs[t] / ref_hs[t]) for t in both if ref_hs[t] >= MIN_REF_M]
    months = defaultdict(list)
    for t, x in big:
        months[t[:7]].append(x)
    return {"all": _summary(all_r), "reference_1m_up": _summary([x for _, x in big]),
            "by_month": {m: _summary(xs) for m, xs in sorted(months.items())}}


def neighbours_of(nodes, km=NEIGHBOUR_KM):
    """{slug: [neighbour slugs]}: the other spots on the same office grid whose sample points
    lie within *km*. *nodes*: {slug: (wfo, lat, lng)}."""
    out = {}
    for s, (wfo, la, lo) in nodes.items():
        out[s] = sorted(o for o, (w2, la2, lo2) in nodes.items()
                        if o != s and w2 == wfo and _km(la, lo, la2, lo2) <= km)
    return out


def neighbour_scan(hs_by_spot, nbrs):
    """Each spot's NWPS Hs over its neighbours' median, on hours where MIN_NEIGHBOURS or more
    have a height and that median is MIN_REF_M or more. Sorted by share under LOW_RATIO, then
    median ratio; each entry flagged as described in the module docstring."""
    out = []
    for s, ns in nbrs.items():
        mine = hs_by_spot.get(s) or {}
        ratios = []
        for t, h in mine.items():
            vals = [hs_by_spot[n][t] for n in ns if t in (hs_by_spot.get(n) or {})]
            if len(vals) < MIN_NEIGHBOURS:
                continue
            m = median(vals)
            if m >= MIN_REF_M:
                ratios.append(h / m)
        e = dict(_summary(ratios), spot=s, neighbours=len(ns))
        e["flagged"] = bool(e["hours"] >= MIN_HOURS and e["share_under_0_4"] is not None
                            and e["share_under_0_4"] >= FLAG_SHARE)
        out.append(e)
    out.sort(key=lambda e: (-(e["share_under_0_4"] or 0), e["median_ratio"] or 9, e["spot"]))
    return out


# --------------------------------------------------------------------------- #
# 2. The grid (pure, on a loaded cycle)                                        #
# --------------------------------------------------------------------------- #
def sample_cell(cycle, lat, lng):
    """(i, j) of the cell nwps.fetch reads for a stored point: the nearest grid row and column
    (an argmin on each axis, as xarray's .sel(method="nearest") takes them)."""
    import numpy as np
    lat_axis = cycle["lats"][:, 0]
    lng_axis = cycle["lons"][0, :]
    return int(np.argmin(np.abs(lat_axis - lat))), int(np.argmin(np.abs(lng_axis - lng)))


def _stats(cycle, short, i, j):
    import numpy as np
    vals = [float(cycle["fields"][(short, fh)][i, j]) for fh in cycle["steps"]
            if (short, fh) in cycle["fields"]]
    vals = [v for v in vals if np.isfinite(v)]
    if not vals:
        return None
    return {"mean": round(sum(vals) / len(vals), 3), "max": round(max(vals), 3), "n": len(vals)}


def window(cycle, lat, lng, w=WINDOW, extra=None):
    """The sample cell for (lat, lng) and every cell within *w* cells of it: its offset
    (di north, dj east), centre, wet flag, and the mean and maximum over the cycle of swh,
    perpw and dirpw; plus *extra* {field: 2-D grid} sampled at each cell."""
    i0, j0 = sample_cell(cycle, lat, lng)
    ni, nj = cycle["lats"].shape
    cells = []
    for di in range(-w, w + 1):
        for dj in range(-w, w + 1):
            i, j = i0 + di, j0 + dj
            if not (0 <= i < ni and 0 <= j < nj):
                continue
            c = {"di": di, "dj": dj, "i": i, "j": j,
                 "lat": round(float(cycle["lats"][i, j]), 5),
                 "lng": round(float(cycle["lons"][i, j]), 5),
                 "wet": not bool(cycle["mask"][i, j])}
            for short in ("swh", "perpw", "dirpw"):
                c[short] = _stats(cycle, short, i, j)
            for name, grid in (extra or {}).items():
                v = float(grid[i, j])
                c[name] = round(v, 3) if math.isfinite(v) else None
            cells.append(c)
    # lat axes run south to north or north to south; say which way di points
    return {"cell": [i0, j0], "cell_lat": round(float(cycle["lats"][i0, j0]), 5),
            "cell_lng": round(float(cycle["lons"][i0, j0]), 5),
            "cell_wet": not bool(cycle["mask"][i0, j0]),
            "di_is_north": bool(cycle["lats"][min(i0 + 1, ni - 1), j0] > cycle["lats"][i0, j0]),
            "cells": cells}


def inventory(path):
    """Every field in a CG1 GRIB: (shortName, name, units, dims), and the 2-D grid at the first
    step of each field the loader does not keep, by shortName."""
    import cfgrib
    import numpy as np
    fields, extra = [], {}
    for ds in cfgrib.open_datasets(path):
        for var in ds.data_vars:
            da = ds[var]
            fields.append({"short": str(var), "name": da.attrs.get("GRIB_name"),
                           "units": da.attrs.get("GRIB_units"), "dims": list(da.dims)})
            short = str(var).lower()
            if short in LOADER_FIELDS or short in extra:
                continue
            slab = da.isel(step=0) if "step" in da.dims else da
            try:
                slab = slab.transpose("latitude", "longitude")
            except ValueError:
                continue
            extra[short] = np.asarray(slab.values, dtype="float64")
    return fields, extra


# --------------------------------------------------------------------------- #
# I/O                                                                          #
# --------------------------------------------------------------------------- #
def _page(q):
    out, frm = [], 0
    while True:
        rows = q().range(frm, frm + 999).execute().data or []
        out.extend(rows)
        if len(rows) < 1000:
            return out
        frm += 1000


def fetch_heights(client, spot_id, since):
    return heights(_page(lambda: client.table("forecasts")
                         .select("valid_time, hs, swell_source")
                         .eq("spot_id", spot_id).eq("source", "nwps")
                         .gte("valid_time", since).order("valid_time")))


def run(client, roster, slugs, since=SINCE, n_cycles=4, *, load=None, cycles_for=None,
        grib_inventory=None):
    from .forecast.nwps_nearshore import _region_for, cycle_path, load_cycle, recent_cycles
    load = load or load_cycle
    cycles_for = cycles_for or (lambda wfo, n: recent_cycles(wfo, n, _region_for(wfo)))
    grib_inventory = grib_inventory or inventory
    by_slug = {_slug(s["name"]): s for s in roster}
    nodes = {sl: ((s.get("nwps_wfo") or "").lower(), s["nwps_node_lat"], s["nwps_node_lng"])
             for sl, s in by_slug.items()
             if s.get("nwps_wfo") and s.get("nwps_node_lat") is not None
             and s.get("nwps_node_lng") is not None}
    ids = {}
    frm = 0
    while True:
        page = client.table("spots").select("id, name").order("id").range(frm, frm + 999).execute().data or []
        for r in page:
            ids[_slug(r["name"])] = r["id"]
        if len(page) < 1000:
            break
        frm += 1000
    errors = {}
    hs_by_spot = {}
    for sl in sorted(nodes):
        if sl not in ids:
            errors[f"no spots row {sl}"] = "not in the spots table"
            continue
        try:
            hs_by_spot[sl] = fetch_heights(client, ids[sl], since)
        except Exception as e:                                     # noqa: BLE001 - reported
            errors[f"forecasts {sl}"] = f"{type(e).__name__}: {e}"[:200]
    pairs = {f"{a} / {b}": pair(hs_by_spot.get(a, {}), hs_by_spot.get(b, {})) for a, b in PAIRS}
    scan = neighbour_scan(hs_by_spot, neighbours_of(nodes))
    flagged = [e["spot"] for e in scan if e["flagged"]]

    # 2. the grid, for the asked spots and the most often low flagged ones
    want = list(dict.fromkeys(list(slugs) + flagged[:MAX_FLAGGED_CELLS]))
    grid, inv = {}, {}
    by_wfo = defaultdict(list)
    for sl in want:
        if sl in nodes:
            by_wfo[nodes[sl][0]].append(sl)
        else:
            errors[f"no node {sl}"] = "no NWPS sample point in the roster"
    for wfo, sls in sorted(by_wfo.items()):
        try:
            cycles = cycles_for(wfo, n_cycles)
        except Exception as e:                                     # noqa: BLE001 - reported
            errors[f"cycles {wfo}"] = f"{type(e).__name__}: {e}"[:200]
            continue
        for cyc in cycles:
            date, cc = cyc[0], cyc[1]
            label = f"{wfo} {date} {cc}Z"
            try:
                cycle = load(wfo, cyc)
            except Exception as e:                                 # noqa: BLE001 - reported
                errors[f"load {label}"] = f"{type(e).__name__}: {e}"[:200]
                continue
            extra = {}
            try:
                fields, extra = grib_inventory(cycle_path(wfo, date, cc))
                inv.setdefault(wfo, fields)
            except Exception as e:                                 # noqa: BLE001 - reported
                errors[f"inventory {label}"] = f"{type(e).__name__}: {e}"[:200]
            for sl in sls:
                _w, la, lo = nodes[sl]
                grid.setdefault(sl, {"wfo": wfo, "node": [la, lo], "spot": [
                    by_slug[sl]["lat"], by_slug[sl]["lng"]], "cycles": {}})
                grid[sl]["cycles"][label] = window(cycle, la, lo, extra=extra)
    return {"window": {"archive_since": since, "cycles_per_office": n_cycles},
            "pairs": pairs, "neighbour_scan": scan, "flagged": flagged,
            "grid": grid, "inventory": inv, "errors": errors,
            "hours_read": {sl: len(v) for sl, v in hs_by_spot.items()}}


def cell_rows(rep):
    """One CSV row per spot, cycle and cell."""
    out = []
    for sl, g in rep["grid"].items():
        for label, w in g["cycles"].items():
            for c in w["cells"]:
                row = {"spot": sl, "cycle": label, "node_lat": g["node"][0], "node_lng": g["node"][1]}
                row.update({k: v for k, v in c.items() if k not in ("swh", "perpw", "dirpw")})
                for f in ("swh", "perpw", "dirpw"):
                    st = c.get(f) or {}
                    row[f"{f}_mean"], row[f"{f}_max"] = st.get("mean"), st.get("max")
                out.append(row)
    return out


def print_report(rep):
    print(f"\nNWPS cells, archive since {rep['window']['archive_since']}")
    print("\n1a. Each Hatteras spot against Outer Banks Fishing Pier (NWPS Hs, hour for hour)")
    for k, e in rep["pairs"].items():
        print(f"  {k:42} all {e['all']}  |  pier >= 1 m {e['reference_1m_up']}")
        for m, s in e["by_month"].items():
            print(f"  {'':42}   {m}: {s}")
    print(f"\n1b. Spots under {LOW_RATIO} of their neighbours' median (flagged first)")
    for e in rep["neighbour_scan"][:40]:
        print(f"  {'FLAG ' if e['flagged'] else '     '}{e['spot']:36} hours {e['hours']:5} "
              f"under 0.4 {e['share_under_0_4']}  median ratio {e['median_ratio']}  "
              f"neighbours {e['neighbours']}")
    print("\n2. The grid around each sample cell (wet W / dry . ; swh mean m)")
    for sl, g in rep["grid"].items():
        for label, w in g["cycles"].items():
            print(f"  {sl} {label}: cell {w['cell']} at {w['cell_lat']}, {w['cell_lng']} "
                  f"wet {w['cell_wet']} (node {g['node']}, spot {g['spot']})")
            rows = defaultdict(dict)
            for c in w["cells"]:
                rows[c["di"]][c["dj"]] = c
            for di in sorted(rows, reverse=w["di_is_north"]):
                line = []
                for dj in sorted(rows[di]):
                    c = rows[di][dj]
                    m = (c["swh"] or {}).get("mean")
                    line.append(f"{'W' if c['wet'] else '.'}{'' if m is None else f'{m:4.2f}':>5}")
                print(f"    {di:+d} " + " ".join(line))
            break        # one cycle's map per spot; the JSON holds every cycle
    for wfo, fields in rep["inventory"].items():
        print(f"\n  {wfo} GRIB fields: {[(f['short'], f['name'], f['units']) for f in fields]}")
    if rep["errors"]:
        print(f"\n  errors: {rep['errors']}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--roster", default="pipeline/spots_enriched.json")
    p.add_argument("--spots", default="", help="comma-separated slugs (default: the Hatteras four)")
    p.add_argument("--since", default=SINCE)
    p.add_argument("--cycles", type=int, default=4)
    p.add_argument("--out", default=None)
    p.add_argument("--csv", default=None)
    a = p.parse_args(argv)
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    with open(a.roster) as fh:
        roster = json.load(fh)
    slugs = [s.strip() for s in a.spots.split(",") if s.strip()] or list(HATTERAS)
    rep = run(client, roster, slugs, a.since, a.cycles)
    rep["window"]["generated_at"] = datetime.now(timezone.utc).isoformat()
    print_report(rep)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(rep, fh, indent=1, default=str)
    if a.csv:
        rows = cell_rows(rep)
        if rows:
            cols = list(dict.fromkeys(k for r in rows for k in r))
            with open(a.csv, "w", newline="") as fh:
                w = csv.DictWriter(fh, fieldnames=cols)
                w.writeheader()
                w.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
