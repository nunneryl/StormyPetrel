"""A read-only data-quality sweep: HRRR cells and wind direction, swell partitions, tide
stations, orphan cams. Nothing here writes anywhere but the report (stdout, and --out).

SECTIONS

  hrrr        For every CONUS spot, the HRRR grid cell its wind is read from (the same nearest-
              point lookup forecast/hrrr.py makes), whether HRRR classes that cell as land, the
              share of land in its 3x3 neighbourhood, and, for a land cell, the nearest water
              cell on the seaward side (bearing within 90 deg of the spot's orientation_deg), with
              the wind at both over one full 48 h cycle. Also the angle of HRRR's grid x-axis at
              each cell: HRRR's 10 m winds are GRID-RELATIVE (uvRelativeToGrid = 1) and
              forecast/hrrr.py reads them as if they were earth-relative.
  wind-preview  (Supabase) Every published hour whose wind is HRRR's, matched exactly to a cycle,
              re-rated by the live rater (wired_rater) with the wind from: the water cell
              (land-cell spots only), the same cell turned to true north, and both.
  partitions  (Supabase) The swell partitions behind the Swell breakdown card, hour by hour, for
              the --explain spots: the card is hidden when at most one component is above
              0.05 m (frontend/components/SwellPartitions.tsx).
  tides       (CO-OPS) Every spot's station: name, position and mean tidal range over the next
              three days, against every station within TIDE_NEIGHBOUR_KM. A station under
              TIDE_DAMPED_SHARE of the widest nearby range is flagged as damped (behind an
              inlet, up a lagoon), with the nearest station near that widest range proposed.
              With Supabase, flagged spots that have a rated tide preference are re-rated on
              the proposed station.
  cams        (Supabase) Every cam whose spot_slug is NULL, with candidate spots from
              cam_seed.json and by name.

HRRR LAND IS A MASK, NOT A FRACTION. wrfsfc carries LAND:surface (1 land, 0 water) and nothing
fractional; it is also what sets HRRR's surface roughness, so it is the field that explains a
lighter wind. The 3x3 share is reported beside it as a measure of how coastal the cell is.

HRRR files come from NOAA's archive on AWS (noaa-hrrr-bdp-pds), by byte range from each file's
.idx, so only the fields used are downloaded.

    python -m pipeline.data_quality --sections hrrr [--cycle 2026102206] [--out report.json]
    python -m pipeline.data_quality --sections hrrr,wind-preview,partitions,tides,cams \\
        --since ISO --explain slug,slug --baseline spots.json --out report.json
"""
from __future__ import annotations

import argparse
import json
import math
import re
import tempfile
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from .config import DEFAULT_ENRICHED_OUTPUT, PIPELINE_DIR

HRRR_AWS = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
HRRR_STEPS = range(0, 49)
KT_PER_MS = 1 / 0.514444
WATER_SEARCH_DEG = 0.3          # candidate cells within this many degrees of the spot
SEAWARD_HALF_ANGLE = 90.0       # a water cell counts as seaward within this of orientation_deg
PARTITION_MIN_M = 0.05          # SwellPartitions.tsx drops components at or below this
TIDE_NEIGHBOUR_KM = 30.0
TIDE_DAMPED_SHARE = 0.6         # a station under this share of the widest nearby range is flagged
TIDE_NEAR_WIDEST_SHARE = 0.8    # the proposed station's range is at least this share of the widest
COOPS_STATIONS = ("https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi/stations.json"
                  "?type=tidepredictions")
CAM_SEED = PIPELINE_DIR / "data" / "cam_seed.json"


# --------------------------------------------------------------------------- #
# Geometry and wind vectors                                                    #
# --------------------------------------------------------------------------- #

def haversine_km(lat1, lng1, lat2, lng2):
    r = 6371.0088
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp, dl = p2 - p1, math.radians(lng2 - lng1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def bearing_deg(lat1, lng1, lat2, lng2):
    """Initial great-circle bearing from point 1 to point 2, 0-360, clockwise from north."""
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dl = math.radians(lng2 - lng1)
    x = math.sin(dl) * math.cos(p2)
    y = math.cos(p1) * math.sin(p2) - math.sin(p1) * math.cos(p2) * math.cos(dl)
    return (math.degrees(math.atan2(x, y)) + 360.0) % 360.0


def angle_off(a, b):
    """Smallest angle between two bearings, 0-180."""
    d = abs((a - b) % 360.0)
    return 360.0 - d if d > 180.0 else d


def met_dir(u, v):
    """The direction a (u east, v north) wind blows FROM, 0-360 clockwise from north."""
    return (math.degrees(math.atan2(-u, -v)) + 360.0) % 360.0


def grid_x_axis_deg(lats, lngs, i, j):
    """The angle of the grid's x-axis at (i, j), anticlockwise from true east, read off the
    grid's own coordinates (the neighbours along j), so no projection constant is assumed."""
    nj = len(lats[0])
    a, b = max(0, j - 1), min(nj - 1, j + 1)
    north = lats[i][b] - lats[i][a]
    east = (lngs[i][b] - lngs[i][a]) * math.cos(math.radians(lats[i][j]))
    return math.degrees(math.atan2(north, east))


def grid_to_earth(u, v, axis_deg):
    """Grid-relative (u along the grid x-axis, v along y) to earth-relative (east, north). The
    grid is right-handed (y is x turned 90 deg anticlockwise), as HRRR's is."""
    a = math.radians(axis_deg)
    return u * math.cos(a) - v * math.sin(a), u * math.sin(a) + v * math.cos(a)


def neighbour_land_share(mask, i, j):
    """Share of land in the 3x3 block centred on (i, j), clipped at the grid edge."""
    ni, nj = len(mask), len(mask[0])
    cells = [mask[a][b] for a in range(max(0, i - 1), min(ni, i + 2))
             for b in range(max(0, j - 1), min(nj, j + 2))]
    return sum(1 for c in cells if c >= 0.5) / len(cells)


def nearest_seaward_water(spot_lat, spot_lng, orientation, candidates):
    """The nearest water cell on the seaward side, from *candidates* [(flat, lat, lng, land)].

    Seaward: bearing from the spot within SEAWARD_HALF_ANGLE of *orientation*, which keeps
    back-bay and lagoon cells out. With no orientation every water cell qualifies.
    Returns (flat, lat, lng, km, bearing) or None."""
    best = None
    for flat, lat, lng, land in candidates:
        if land >= 0.5:
            continue
        brg = bearing_deg(spot_lat, spot_lng, lat, lng)
        if orientation is not None and angle_off(brg, orientation) > SEAWARD_HALF_ANGLE:
            continue
        km = haversine_km(spot_lat, spot_lng, lat, lng)
        if best is None or km < best[3]:
            best = (flat, lat, lng, km, brg)
    return best


# --------------------------------------------------------------------------- #
# HRRR from the AWS archive                                                    #
# --------------------------------------------------------------------------- #

def _key(cycle, fh):
    return f"hrrr.{cycle[:8]}/conus/hrrr.t{cycle[8:]}z.wrfsfcf{fh:02d}.grib2"


def parse_idx(text):
    """{(var, level): (start, end_or_None)} from a GRIB .idx file."""
    rows = [ln.split(":") for ln in text.splitlines() if ln.strip()]
    out = {}
    for n, row in enumerate(rows):
        start = int(row[1])
        end = int(rows[n + 1][1]) - 1 if n + 1 < len(rows) else None
        out[(row[3], row[4])] = (start, end)
    return out


def _get(session, url, **kw):
    r = session.get(url, timeout=120, **kw)
    r.raise_for_status()
    return r


def full_cycles(session, now=None, n=2):
    """The newest *n* 00/06/12/18Z cycles whose f48 file exists, newest first, 'YYYYMMDDHH'."""
    now = now or datetime.now(timezone.utc)
    out = []
    for back in range(0, 96, 6):
        t = now - timedelta(hours=back)
        cycle = t.strftime("%Y%m%d") + f"{(t.hour // 6) * 6:02d}"
        if session.head(f"{HRRR_AWS}/{_key(cycle, 48)}", timeout=60).status_code == 200:
            out.append(cycle)
            if len(out) == n:
                return out
    if not out:
        raise RuntimeError("no full HRRR cycle found in the last 96 h on the AWS archive")
    return out


def fetch_fields(session, cycle, fh, wanted, dest):
    """Download the byte range covering *wanted* [(var, level)] from one step file."""
    idx = parse_idx(_get(session, f"{HRRR_AWS}/{_key(cycle, fh)}.idx").text)
    spans = [idx[w] for w in wanted]
    start = min(s for s, _ in spans)
    ends = [e for _, e in spans]
    end = None if any(e is None for e in ends) else max(ends)
    rng = f"bytes={start}-" + ("" if end is None else str(end))
    dest.write_bytes(_get(session, f"{HRRR_AWS}/{_key(cycle, fh)}",
                          headers={"Range": rng}).content)
    return dest


def _open(path):
    from .forecast.hrrr import _open_grib_dataset
    return _open_grib_dataset(path)


def _var(datasets, names):
    for ds in datasets:
        for v in ds.data_vars:
            if str(v).lower() in names:
                return ds, ds[v]
    return None, None


def load_grid(session, cycle, workdir):
    """(tree, shape, lats, lngs, mask) from the cycle's f00 LAND field."""
    import numpy as np
    from .forecast.hrrr import _build_kdtree_for_grid, _close_datasets
    path = fetch_fields(session, cycle, 0, [("LAND", "surface")], workdir / "land.grib2")
    dss = _open(path)
    ds, land = _var(dss, ("lsm", "land"))
    if land is None:
        raise RuntimeError(f"no LAND field decoded from {path}")
    tree, shape, lats, lngs = _build_kdtree_for_grid(ds)
    mask = np.asarray(land.values, dtype=float)
    _close_datasets(dss)
    return tree, shape, lats, lngs, mask


def cell_winds(session, cycle, flats, workdir):
    """{flat: [(valid_iso, u, v)]} over the cycle's 49 steps, grid-relative as HRRR ships them."""
    import numpy as np
    from .forecast.hrrr import _close_datasets
    out = defaultdict(list)
    base = datetime.strptime(cycle, "%Y%m%d%H").replace(tzinfo=timezone.utc)
    for fh in HRRR_STEPS:
        p = fetch_fields(session, cycle, fh, [("UGRD", "10 m above ground"),
                                              ("VGRD", "10 m above ground")],
                         workdir / f"uv{cycle}{fh:02d}.grib2")
        dss = _open(p)
        _, u = _var(dss, ("u10", "ugrd", "10u"))
        _, v = _var(dss, ("v10", "vgrd", "10v"))
        u = np.asarray(u.values, dtype=float).ravel()
        v = np.asarray(v.values, dtype=float).ravel()
        valid = (base + timedelta(hours=fh)).isoformat()
        for f in flats:
            out[f].append((valid, float(u[f]), float(v[f])))
        _close_datasets(dss)
        p.unlink()
    return out


def hrrr_cells(spots, tree, shape, lats, lngs, mask):
    """Every CONUS spot's cell, its land flag and 3x3 share, its grid x-axis angle, and for a
    land cell the nearest seaward water cell."""
    from .forecast.hrrr import _spots_in_conus
    flat_mask = mask.ravel()
    flat_lat, flat_lng = lats.ravel(), lngs.ravel()
    nx = shape[1]
    conus = _spots_in_conus(spots)
    _, idxs = tree.query([[float(s["lat"]), float(s["lng"])] for s in conus])
    rows = []
    for s, flat in zip(conus, idxs):
        flat = int(flat)
        i, j = divmod(flat, nx)
        lat, lng = float(s["lat"]), float(s["lng"])
        row = {"spot": s["name"], "state": s.get("region_hint"), "lat": lat, "lng": lng,
               "orientation_deg": s.get("orientation_deg"),
               "offshore_wind_deg": s.get("offshore_wind_deg"),
               "cell": [i, j], "flat": flat,
               "cell_lat": round(float(flat_lat[flat]), 5),
               "cell_lng": round(float(flat_lng[flat]), 5),
               "cell_km": round(haversine_km(lat, lng, flat_lat[flat], flat_lng[flat]), 2),
               "cell_is_land": bool(flat_mask[flat] >= 0.5),
               "land_share_3x3": round(neighbour_land_share(mask, i, j), 3),
               "x_axis_deg": round(grid_x_axis_deg(lats, lngs, i, j), 3)}
        if row["cell_is_land"]:
            near = tree.query_ball_point([lat, lng], WATER_SEARCH_DEG)
            cands = [(int(k), float(flat_lat[k]), float(flat_lng[k]), float(flat_mask[k]))
                     for k in near]
            w = nearest_seaward_water(lat, lng, s.get("orientation_deg"), cands)
            if w is not None:
                wi, wj = divmod(w[0], nx)
                row.update(water_cell=[wi, wj], water_flat=w[0],
                           water_lat=round(w[1], 5), water_lng=round(w[2], 5),
                           water_km=round(w[3], 2), water_bearing=round(w[4]),
                           water_x_axis_deg=round(grid_x_axis_deg(lats, lngs, wi, wj), 3))
        rows.append(row)
    return rows


def wind_summary(series_land, series_water):
    """Median land and water speeds (kt), median difference and ratio, the largest difference,
    and the hours the water cell is over 20 kt where the land cell is not (where #270's 20 kt
    rules would bite)."""
    land = [math.hypot(u, v) * KT_PER_MS for _, u, v in series_land]
    water = [math.hypot(u, v) * KT_PER_MS for _, u, v in series_water]
    diffs = [w - l for l, w in zip(land, water)]
    ratios = [w / l for l, w in zip(land, water) if l > 1.0]
    return {"hours": len(land),
            "median_land_kt": round(median(land), 1), "median_water_kt": round(median(water), 1),
            "median_diff_kt": round(median(diffs), 1),
            "median_ratio": round(median(ratios), 2) if ratios else None,
            "max_diff_kt": round(max(diffs), 1),
            "hours_water_over_20_land_not": sum(1 for l, w in zip(land, water) if w > 20.0 >= l)}


def hrrr_section(spots, cycles, session, workdir, log=print):
    """The land check on the newest cycle, with every needed cell's wind on each of *cycles*."""
    tree, shape, lats, lngs, mask = load_grid(session, cycles[0], workdir)
    rows = hrrr_cells(spots, tree, shape, lats, lngs, mask)
    flats = sorted({r["flat"] for r in rows} | {r["water_flat"] for r in rows if "water_flat" in r})
    winds = {}
    for c in cycles:
        log(f"HRRR cycle {c}: wind at {len(flats)} cells over 49 steps")
        winds[c] = cell_winds(session, c, flats, workdir)
    newest = winds[cycles[0]]
    for r in rows:
        if "water_flat" in r:
            r["wind"] = wind_summary(newest[r["flat"]], newest[r["water_flat"]])
    return {"cycles": cycles, "spots": len(rows), "rows": rows,
            "land_cell_spots": [r for r in rows if r["cell_is_land"]], "_winds": winds}


def _print_hrrr(rep):
    land = rep["land_cell_spots"]
    print(f"\n=== HRRR land cells — cycle {rep['cycles'][0]}: {rep['spots']} CONUS spots, "
          f"{len(land)} read their wind from a cell HRRR classes as land ===")
    for r in sorted(land, key=lambda r: (str(r["state"]), r["spot"])):
        w = r.get("wind") or {}
        water = (f"water cell {r['water_cell']} at {r['water_lat']},{r['water_lng']} "
                 f"({r['water_km']} km, bearing {r['water_bearing']}°)" if "water_cell" in r
                 else "NO seaward water cell within the search radius")
        print(f"  {r['spot']} ({r['state']}): cell {r['cell']} {r['cell_km']} km away; 3x3 land "
              f"share {r['land_share_3x3']}; {water}")
        if w:
            print(f"      {w['hours']} h: median {w['median_land_kt']} kt land vs "
                  f"{w['median_water_kt']} kt water (diff {w['median_diff_kt']}, ratio "
                  f"{w['median_ratio']}), max diff {w['max_diff_kt']} kt; water over 20 kt "
                  f"where land is not: {w['hours_water_over_20_land_not']} h")
    turns = [abs(r["x_axis_deg"]) for r in rep["rows"]]
    print(f"grid x-axis angle from true east at the spots' cells: median {median(turns):.1f}°, "
          f"max {max(turns):.1f}° — HRRR winds are grid-relative and published unrotated")


# --------------------------------------------------------------------------- #
# Re-rating published hours with another wind                                  #
# --------------------------------------------------------------------------- #

WIND_VARIANTS = ("water", "true-north", "water+true-north")


def match_cycle(row, flat, winds):
    """(cycle, step index, u, v) of the HRRR step that published *row*'s wind at *flat*, by
    exact match of speed and direction to the 3 decimals forecast/hrrr.py stores; None when the
    hour's wind is not HRRR's (NWPS beyond 48 h) or came from a cycle not fetched."""
    ws, wd = row.get("wind_speed"), row.get("wind_dir")
    if ws is None or wd is None:
        return None
    vt = _iso(row["valid_time"])
    for cycle, cells in winds.items():
        for n, (valid, u, v) in enumerate(cells.get(flat, ())):
            if _iso(valid) != vt:
                continue
            if (abs(round(math.hypot(u, v), 3) - float(ws)) <= 0.0015
                    and angle_off(round(met_dir(u, v), 3), float(wd)) <= 0.0015):
                return cycle, n, u, v
    return None


def _iso(t):
    return datetime.fromisoformat(str(t).replace("Z", "+00:00")).astimezone(timezone.utc)


def variant_wind(variant, cell, winds, cycle, n, u, v):
    """(speed m/s, direction) of *variant* for one matched hour, or None when it has no value."""
    if variant == "true-north":
        ue, ve = grid_to_earth(u, v, cell["x_axis_deg"])
        return math.hypot(ue, ve), met_dir(ue, ve)
    if "water_flat" not in cell:
        return None
    _, wu, wv = winds[cycle][cell["water_flat"]][n]
    if variant == "water":
        return math.hypot(wu, wv), met_dir(wu, wv)
    ue, ve = grid_to_earth(wu, wv, cell["water_x_axis_deg"])
    return math.hypot(ue, ve), met_dir(ue, ve)


def rerate(row, spot, factors, m, speed, direction):
    """The live rater on *row* with the wind replaced: wind_mult recomputed by the rater's own
    wind_multiplier from the row's chop_ratio, then wired_rater."""
    from .forecast.face_correction import spot_factor
    from .interpret import wind_multiplier
    from .star_preview import _slug, rescore, wired_rater
    off = spot.get("offshore_wind_deg")
    new = dict(row, wind_speed=speed, wind_dir=direction,
               wind_mult=round(wind_multiplier(direction, speed, off, row.get("chop_ratio")), 3))
    rb = rescore(new, spot)
    if rb["stars"] is None:
        return None
    return wired_rater(new, spot, rb, spot_factor(spot, factors or {}, _slug), m)


def wind_preview(rows, spots, names, factors, m, cells, winds, now, n=10, focus=()):
    """Every published hour whose wind is HRRR's and which the live rater reproduces, re-rated
    under each of WIND_VARIANTS; by state, wind class change, and the homepage list."""
    from .forecast.face_correction import spot_factor
    from .interpret import wind_class, wind_multiplier
    from .star_preview import _slug, rescore, wired_rater
    by_name = {c["spot"]: c for c in cells}
    counts = Counter()
    rep = {v: {"by_state": defaultdict(Counter), "changed": [], "class_changed": 0}
           for v in WIND_VARIANTS}
    first = {}
    for row in rows:
        name, state = names.get(row["spot_id"], (None, None))
        spot, cell = spots.get(name), by_name.get(name)
        if spot is None or cell is None or row.get("stars") is None:
            continue
        hit = match_cycle(row, cell["flat"], winds)
        if hit is None:
            counts["not HRRR (or an unfetched cycle)"] += 1
            continue
        off = spot.get("offshore_wind_deg")
        wm = row.get("wind_mult")
        if off is None or wm is None or round(wind_multiplier(
                float(row["wind_dir"]), float(row["wind_speed"]), off,
                row.get("chop_ratio")), 3) != round(float(wm), 3):
            counts["HRRR, but wind_mult not reproduced"] += 1
            continue
        rb = rescore(row, spot)
        live = (wired_rater(row, spot, rb, spot_factor(spot, factors or {}, _slug), m)
                if rb["stars"] is not None else None)
        if live is None or live != float(row["stars"]):
            counts["HRRR, stars not reproduced"] += 1
            continue
        counts["HRRR, reproduced"] += 1
        afters = {}
        for variant in WIND_VARIANTS:
            w = variant_wind(variant, cell, winds, *hit)
            if w is None:
                continue
            after = rerate(row, spot, factors, m, *w)
            if after is None:
                continue
            afters[variant] = after
            r = rep[variant]
            delta = round(after - live, 1)
            r["by_state"][state][delta] += 1
            before_cls = wind_class(float(row["wind_dir"]), off)
            after_cls = wind_class(w[1], off)
            if before_cls != after_cls:
                r["class_changed"] += 1
            if delta:
                r["changed"].append({"spot": name, "state": state, "valid_time": row["valid_time"],
                                     "before": live, "after": after, "delta": delta,
                                     "wind_before": f"{before_cls} {float(row['wind_speed']) * KT_PER_MS:.1f} kt from {float(row['wind_dir']):.0f}°",
                                     "wind_after": f"{after_cls} {w[0] * KT_PER_MS:.1f} kt from {w[1]:.0f}°"})
        if row["spot_id"] not in first and _iso(row["valid_time"]) >= now:
            first[row["spot_id"]] = (live, afters)
    out = {"replay": dict(counts), "variants": {}}
    for variant, r in rep.items():
        per_spot = Counter(c["spot"] for c in r["changed"])
        board_b = sorted(((b, sid) for sid, (b, _) in first.items()), key=lambda x: (-x[0], x[1]))
        board_a = sorted(((a.get(variant, b), sid) for sid, (b, a) in first.items()),
                         key=lambda x: (-x[0], x[1]))
        nm = lambda sid: names.get(sid, (None, None))[0]
        out["variants"][variant] = {
            "compared": sum(sum(c.values()) for c in r["by_state"].values()),
            "changed": len(r["changed"]), "ups": sum(c["delta"] > 0 for c in r["changed"]),
            "downs": sum(c["delta"] < 0 for c in r["changed"]),
            "wind_class_changed": r["class_changed"],
            "by_state": {s: dict(sorted(c.items())) for s, c in
                         sorted(r["by_state"].items(), key=lambda kv: str(kv[0]))},
            "top_spots": per_spot.most_common(20),
            "homepage_before": [(nm(sid), st) for st, sid in board_b[:n]],
            "homepage_after": [(nm(sid), st) for st, sid in board_a[:n]],
            "focus": {f: [c for c in r["changed"] if c["spot"] == f] for f in focus},
        }
    return out


def _print_wind_preview(rep):
    print(f"\n=== Re-rating published HRRR hours: {rep['replay']} ===")
    for variant, r in rep["variants"].items():
        print(f"\n--- {variant}: {r['changed']} of {r['compared']} hours change "
              f"(up {r['ups']}, down {r['downs']}); wind class changed on {r['wind_class_changed']} h")
        for s, c in r["by_state"].items():
            print(f"  {s}: {c}")
        print(f"  most changed: {r['top_spots']}")
        print(f"  homepage before: {r['homepage_before']}")
        print(f"  homepage after:  {r['homepage_after']}")
        for f, cs in r["focus"].items():
            print(f"  {f}: {len(cs)} h change")
            for c in cs[:24]:
                print(f"    {c['valid_time']}: {c['before']} -> {c['after']} "
                      f"({c['wind_before']} -> {c['wind_after']})")


# --------------------------------------------------------------------------- #
# Swell partitions behind the Swell breakdown card                              #
# --------------------------------------------------------------------------- #

def card_components(row):
    """The components SwellPartitions.tsx keeps: height above PARTITION_MIN_M."""
    comps = []
    for p in ("swell_1", "swell_2", "swell_3", "wind_wave"):
        hs = row.get(f"{p}_hs")
        if hs is not None and float(hs) > PARTITION_MIN_M:
            comps.append(p)
    return comps


def card_shown(row):
    """SwellPartitions.tsx: hidden when nothing is kept, or one swell and nothing else."""
    comps = card_components(row)
    swells = [c for c in comps if c != "wind_wave"]
    return not (len(comps) == 0 or (len(swells) <= 1 and len(comps) <= 1))


def partitions_section(rows, names, focus):
    out = {}
    for name in focus:
        hours = [r for r in rows if names.get(r["spot_id"], (None,))[0] == name]
        out[name] = [{"valid_time": r["valid_time"], "swell_source": r.get("swell_source"),
                      "components": card_components(r), "card": card_shown(r),
                      "hs": {p: r.get(f"{p}_hs") for p in ("swell_1", "swell_2", "swell_3",
                                                         "wind_wave")}}
                     for r in sorted(hours, key=lambda r: r["valid_time"])]
    return out


def _print_partitions(rep):
    print("\n=== Swell breakdown card, by hour ===")
    for name, hours in rep.items():
        shown = sum(h["card"] for h in hours)
        src = Counter(h["swell_source"] for h in hours)
        print(f"--- {name}: card on {shown} of {len(hours)} hours; swell_source {dict(src)}")
        for h in hours:
            if not h["card"]:
                print(f"    {h['valid_time']} no card [{h['swell_source']}] {h['hs']}")


# --------------------------------------------------------------------------- #
# Tide stations                                                                #
# --------------------------------------------------------------------------- #

def mean_range(hilo):
    """Mean height difference between consecutive opposite extremes of a hilo list
    [{'t', 'v', 'type'}], or None with fewer than two."""
    pts = [(p["type"], float(p["v"])) for p in hilo if p.get("v") not in (None, "")]
    diffs = [abs(b[1] - a[1]) for a, b in zip(pts, pts[1:]) if a[0][0] != b[0][0]]
    return round(sum(diffs) / len(diffs), 2) if diffs else None


def damped(assigned_range, nearby):
    """(flag, widest, proposed) for a station range against [(id, name, km, range)] nearby:
    flagged when under TIDE_DAMPED_SHARE of the widest; proposed is the NEAREST station whose
    range is at least TIDE_NEAR_WIDEST_SHARE of the widest."""
    ranged = [s for s in nearby if s[3] is not None]
    if assigned_range is None or not ranged:
        return False, None, None
    widest = max(ranged, key=lambda s: s[3])
    if assigned_range >= TIDE_DAMPED_SHARE * widest[3]:
        return False, widest, None
    ok = [s for s in ranged if s[3] >= TIDE_NEAR_WIDEST_SHARE * widest[3]]
    return True, widest, min(ok, key=lambda s: s[2])


def _hilo(session, station, begin, hours=72):
    from .config import NOAA_COOPS_ENDPOINT
    r = session.get(NOAA_COOPS_ENDPOINT, timeout=30, params={
        "station": station, "product": "predictions", "datum": "MLLW", "units": "english",
        "time_zone": "lst_ldt", "interval": "hilo", "begin_date": begin, "range": hours,
        "format": "json"})
    if r.status_code != 200:
        return None
    try:
        return r.json().get("predictions")
    except ValueError:
        return None


def tides_section(spots, session, log=print, pace_s=0.5):
    stations = {str(s["id"]): s for s in session.get(COOPS_STATIONS, timeout=60).json()["stations"]}
    begin = datetime.now(timezone.utc).strftime("%Y%m%d")
    need, plan = set(), []
    for sp in spots:
        sid = sp.get("nearest_tide_station_id")
        if not sid:
            continue
        lat, lng = float(sp["lat"]), float(sp["lng"])
        near = [(k, s["name"], round(haversine_km(lat, lng, float(s["lat"]), float(s["lng"])), 1))
                for k, s in stations.items() if s.get("lat") is not None
                and haversine_km(lat, lng, float(s["lat"]), float(s["lng"])) <= TIDE_NEIGHBOUR_KM]
        need |= {sid} | {k for k, _, _ in near}
        plan.append((sp, str(sid), near))
    log(f"tides: {len(plan)} spots with a station; fetching 72 h hilo for {len(need)} stations")
    ranges = {}
    for k in sorted(need):
        ranges[k] = mean_range(_hilo(session, k, begin) or [])
        time.sleep(pace_s)
    out = []
    for sp, sid, near in plan:
        st = stations.get(sid, {})
        flag, widest, proposed = damped(ranges.get(sid), [(k, n, km, ranges.get(k))
                                                          for k, n, km in near])
        out.append({"spot": sp["name"], "state": sp.get("region_hint"),
                    "tide_preference": sp.get("tide_preference"),
                    "tide_preference_source": sp.get("tide_preference_source"),
                    "station": sid, "station_name": st.get("name"),
                    "station_type": st.get("type"),
                    "station_km": sp.get("nearest_tide_station_dist_km"),
                    "range_ft": ranges.get(sid), "flagged": flag,
                    "widest_nearby": widest and {"station": widest[0], "name": widest[1],
                                                 "km": widest[2], "range_ft": widest[3]},
                    "proposed": proposed and {"station": proposed[0], "name": proposed[1],
                                              "km": proposed[2], "range_ft": proposed[3]}})
    return out


def _print_tides(rep):
    flagged = [r for r in rep if r["flagged"]]
    print(f"\n=== Tide stations: {len(flagged)} of {len(rep)} spots on a damped station ===")
    for r in sorted(flagged, key=lambda r: (str(r["state"]), r["spot"])):
        p, w = r["proposed"], r["widest_nearby"]
        print(f"  {r['spot']} ({r['state']}): {r['station']} {r['station_name']} "
              f"{r['station_km']} km, range {r['range_ft']} ft; widest within "
              f"{TIDE_NEIGHBOUR_KM:.0f} km {w['station']} {w['name']} {w['range_ft']} ft; proposed "
              f"{p['station']} {p['name']} ({p['km']} km, {p['range_ft']} ft); preference "
              f"{r['tide_preference']} [{r['tide_preference_source']}]")


def tide_preview(rows, spots, names, factors, m, tides, session, now):
    """Flagged spots with a rated tide preference, re-rated on the proposed station."""
    from .forecast.face_correction import spot_factor
    from .interpret import (build_tide_series, lookup_tide_norm, rated_tide_preference,
                            tide_multiplier)
    from .star_preview import _slug, rescore, wired_rater
    from .config import NOAA_COOPS_ENDPOINT
    todo = {r["spot"]: r for r in tides if r["flagged"] and rated_tide_preference(
        spots.get(r["spot"], {}))}
    out = {}
    for name, t in todo.items():
        spot = spots[name]
        begin = (now - timedelta(days=1)).strftime("%Y%m%d")
        block = {}
        for interval, key in (("h", "hourly"), ("hilo", "hilo")):
            r = session.get(NOAA_COOPS_ENDPOINT, timeout=30, params={
                "station": t["proposed"]["station"], "product": "predictions", "datum": "MLLW",
                "units": "english", "time_zone": "lst_ldt", "interval": interval,
                "begin_date": begin, "range": 24 * 12, "format": "json"})
            block[key] = (r.json().get("predictions") or []) if r.status_code == 200 else []
        series = build_tide_series(block)
        pref = rated_tide_preference(spot)
        deltas, compared = Counter(), 0
        for row in rows:
            if names.get(row["spot_id"], (None,))[0] != name or row.get("stars") is None:
                continue
            rb = rescore(row, spot)
            f = spot_factor(spot, factors or {}, _slug)
            if rb["stars"] is None or wired_rater(row, spot, rb, f, m) != float(row["stars"]):
                continue
            norm = lookup_tide_norm(series, _iso(row["valid_time"]), float(spot["lng"]))[1] \
                if series else None
            new = dict(row, tide_norm=norm, tide_mult=tide_multiplier(norm, pref))
            rb2 = rescore(new, spot)
            after = wired_rater(new, spot, rb2, f, m)
            compared += 1
            deltas[round(after - float(row["stars"]), 1)] += 1
        out[name] = {"proposed": t["proposed"], "preference": pref, "compared": compared,
                     "delta": dict(sorted(deltas.items()))}
    return out


# --------------------------------------------------------------------------- #
# Orphan cams                                                                  #
# --------------------------------------------------------------------------- #

def _tokens(s):
    return {t for t in re.findall(r"[a-z0-9]+", (s or "").lower())
            if t not in {"live", "cam", "beach", "surf", "the", "pier", "st", "of"}}


def cam_candidates(cam, roster_names, seed):
    """Candidate spots for an orphan cam: the cam_seed.json entry with the same cam_name (its
    spot_slug, and whether that slug is on the roster), and roster spots sharing name tokens."""
    from .star_preview import _slug
    slugs = {_slug(n): n for n in roster_names}
    seeded = [{"spot_slug": e["spot_slug"], "on_roster": e["spot_slug"] in slugs}
              for e in seed if e.get("cam_name") == cam.get("cam_name")]
    toks = _tokens(cam.get("cam_name")) | _tokens(cam.get("attribution"))
    scored = sorted(((len(toks & _tokens(n)), n) for n in roster_names), reverse=True)
    by_name = [{"spot": n, "slug": _slug(n), "shared": s} for s, n in scored[:3] if s > 0]
    return {"seed": seeded, "by_name": by_name}


def cams_section(client, roster_names):
    rows = (client.table("cams").select("*").is_("spot_slug", "null").execute().data or [])
    seed = json.loads(CAM_SEED.read_text())["cams"]
    return [dict(c, candidates=cam_candidates(c, roster_names, seed)) for c in rows]


def _print_cams(rep):
    print(f"\n=== Cams with no spot: {len(rep)} ===")
    for c in rep:
        print(f"  id {c.get('id')} {c.get('cam_name')!r} [{c.get('provider')}, status "
              f"{c.get('status')}, created {c.get('created_at')}] attribution "
              f"{c.get('attribution')!r}; candidates {c['candidates']}")


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

SECTIONS = ("hrrr", "wind-preview", "partitions", "tides", "cams")


def _strip(obj):
    """Drop the private _keys before writing JSON."""
    if isinstance(obj, dict):
        return {k: _strip(v) for k, v in obj.items() if not str(k).startswith("_")}
    if isinstance(obj, list):
        return [_strip(v) for v in obj]
    return obj


def main(argv=None):
    import os
    import requests
    p = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    p.add_argument("--sections", default="hrrr")
    p.add_argument("--cycles", default=None,
                   help="HRRR cycles YYYYMMDDHH,... (default: the two newest full ones)")
    p.add_argument("--baseline", type=Path, default=DEFAULT_ENRICHED_OUTPUT)
    p.add_argument("--since", default=None, help="ISO start of the published window (default: now)")
    p.add_argument("--explain", default="", help="comma-separated slugs to list hour by hour")
    p.add_argument("--workdir", type=Path, default=None)
    p.add_argument("--out", type=Path, default=None)
    args = p.parse_args(argv)
    sections = [s.strip() for s in args.sections.split(",") if s.strip()]
    for s in sections:
        if s not in SECTIONS:
            p.error(f"unknown section {s!r}: {', '.join(SECTIONS)}")
    if "wind-preview" in sections and "hrrr" not in sections:
        p.error("wind-preview needs the hrrr section")
    roster = json.loads(Path(args.baseline).read_text())
    spots = {s["name"]: s for s in roster if s.get("name")}
    session = requests.Session()
    workdir = Path(args.workdir or tempfile.mkdtemp(prefix="dq_"))
    workdir.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc)
    since = args.since or now.replace(microsecond=0).isoformat()
    out = {"since": since}

    if "hrrr" in sections:
        cycles = ([c.strip() for c in args.cycles.split(",")] if args.cycles
                  else full_cycles(session, n=2))
        out["hrrr"] = hrrr_section(roster, cycles, session, workdir)
        _print_hrrr(out["hrrr"])

    client = rows = names = None
    if any(s in sections for s in ("wind-preview", "partitions", "cams", "tides")):
        from supabase import create_client
        from .star_preview import _fetch, _slug
        client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
        names, rows = _fetch(client, since)
    from .forecast.face_correction import load_face_factors, median_face_factor
    factors = load_face_factors()
    m = median_face_factor(factors)
    focus = []
    if args.explain:
        from .star_preview import _slug
        by_slug = {_slug(n): n for n in spots}
        focus = [by_slug[s.strip()] for s in args.explain.split(",") if s.strip() in by_slug]

    if "wind-preview" in sections:
        out["wind_preview"] = wind_preview(rows, spots, names, factors, m, out["hrrr"]["rows"],
                                           out["hrrr"]["_winds"], now, focus=focus)
        _print_wind_preview(out["wind_preview"])
    if "partitions" in sections:
        out["partitions"] = partitions_section(rows, names, focus)
        _print_partitions(out["partitions"])
    if "tides" in sections:
        out["tides"] = tides_section(roster, session)
        _print_tides(out["tides"])
        if rows is not None:
            out["tide_preview"] = tide_preview(rows, spots, names, factors, m, out["tides"],
                                               session, now)
            print(f"\n=== Tide preview (flagged spots with a rated preference): "
                  f"{out['tide_preview']} ===")
    if "cams" in sections:
        out["cams"] = cams_section(client, list(spots))
        _print_cams(out["cams"])
    if args.out:
        args.out.write_text(json.dumps(_strip(out), indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
