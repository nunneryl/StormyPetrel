"""Read-only follow-up to face-calibration run 48 (sw1-raycast mode=face-followup). Nothing in
the pipeline changes; nothing is written but the report (stdout, --out JSON and --csv-dir).

SECTIONS (each runs on its own; one that fails is reported and the others still run)

  suspects   Run 48's 16 sheltered-water suspects (docs/face_validation/run-48/face_sheltered.csv)
             over run 48's window: our stored hs and tp, hour by hour, against the nearest of
             six NDBC buoys (BUOYS) from NDBC's realtime files: the median ratio of heights,
             the median periods, and the share of hours under 3 s on each side. The buoys sit
             15 to 100 km off these beaches, so the heights are a rough guide; the periods are
             not (a period does not shorten between the buoy and the beach).
  coastline  Every suspect's baked NWPS sample point (and Duck Pier's and Jetty Park's) against
             the GSHHG L1 coastline (pipeline/geodata, cached on the runner):
               on land       the point is inside a land polygon
               behind land   the ray from the point along the spot's facing hits land within
                             RAY_KM (a bay, sound or lagoon, or an island offshore)
               pass/inlet    that ray is open, but the open sector around it is under
                             OPEN_WIDTH_DEG wide
               open ocean    that ray is open and the open sector is OPEN_WIDTH_DEG or wider
             with the depth there (ETOPO 2022, 15 arc-second, from NOAA's ERDDAP), and the
             nearest point within 90 deg of the facing that is clearly open ocean.
  mop        MOP's own height against CDIP 153 Del Mar, 262 Leucadia, 264 Red Beach and 284
             Capistrano (the Doheny comparison): MOP at the buoy's own position (CDIP's
             MOP_validation BPnnn nowcast) against the buoy, and our stored MOP-tier height
             (face_ft_raw / 3.281) at the nearest MOP-tier spot within MOP_SPOT_KM of the buoy;
             by measured height and measured period.
  seaward    Duck Pier (FRF 8 m array) and Jetty Park Cocoa Beach (CDIP 143): the nearest NWPS
             CG1 cell within SEAWARD_HALF_DEG of the spot's facing that is wet and open ocean,
             and its height over every cycle NOMADS still holds (the newest cycle at or before
             each hour), beside the baked node's height over the same hours, both against the
             instrument. NWPS is read with forecast/nwps.py's own listing, download, wet-cell
             and extraction functions.

The statistics are face_calibration.stats: n, bias, median ratio (ours / measured), RMSE,
scatter index, r, and the share within 20% and 30%.
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median

from .config import DEFAULT_ENRICHED_OUTPUT, PIPELINE_DIR
from .face_calibration import (BANDS, M_TO_FT, _arr, _open_nc, cdip_hourly, frf_8m_hourly,
                               hourly, period_band, rater_of, sized_from, stats)
from .forecast.nwps_nearshore import _bearing, _haversine_km

REPO = PIPELINE_DIR.parent
RUN48 = REPO / "docs" / "face_validation" / "run-48"
BUOYS = ("42098", "42099", "42036", "42012", "42039", "41110")
NDBC_REALTIME = "https://www.ndbc.noaa.gov/data/realtime2/{}.txt"
NDBC_STATIONS = "https://www.ndbc.noaa.gov/activestations.xml"
ETOPO = ("https://coastwatch.pfeg.noaa.gov/erddap/griddap/ETOPO_2022_v1_15s.csv"
         "?z%5B({lat}):1:({lat})%5D%5B({lng}):1:({lng})%5D")
MOP_VALIDATION = "https://thredds.cdip.ucsd.edu/thredds/dodsC/cdip/model/MOP_validation/BP{}_nowcast.nc"
MOP_BUOYS = ("153", "262", "264", "284")
MOP_SPOT_KM = 5.0
SHELTERED_TP_S = 3.0

RAY_KM = 10.0              # how far a ray looks for land
RAY_STEP_KM = 0.1          # sample spacing along a ray
RAY_EVERY_DEG = 10         # rays round the compass for the open sector
OPEN_WIDTH_DEG = 90        # an open sector this wide or wider is open ocean
SEARCH_STEP_KM = 0.25      # nearest-open-ocean search: distance step ...
SEARCH_MAX_KM = 10.0       # ... out to here
SEARCH_EVERY_DEG = 5       # ... bearing step
SEAWARD_HALF_DEG = 30      # the seaward NWPS cell: within this of the spot's facing
SEAWARD_MAX_KM = 15.0
SEAWARD_SPOTS = (("Duck Pier", "frf-8m"), ("Jetty Park Cocoa Beach", "cdip-143"))
HEIGHT_BANDS = ((0.0, 0.5), (0.5, 1.0), (1.0, 1.5), (1.5, 99.0))


# --------------------------------------------------------------------------- #
# Pure                                                                          #
# --------------------------------------------------------------------------- #
def parse_stations(xml: str) -> dict:
    """{station id: (lat, lng)} from NDBC's activestations.xml."""
    out = {}
    for m in re.finditer(r"<station\s+([^>]*?)/?>", xml):
        attrs = dict(re.findall(r'(\w+)="([^"]*)"', m.group(1)))
        try:
            out[attrs["id"].upper()] = (float(attrs["lat"]), float(attrs["lon"]))
        except (KeyError, ValueError):
            continue
    return out


def parse_ndbc_realtime(text: str) -> list[tuple[float, float | None, float | None]]:
    """[(epoch s, WVHT m, DPD s)] from an NDBC realtime2 standard-meteorological file; MM is
    None. Columns are read off the header, not assumed."""
    cols, out = None, []
    for line in text.splitlines():
        if line.startswith("#"):
            if cols is None:
                cols = line.lstrip("#").split()
            continue
        f = line.split()
        if cols is None or len(f) < len(cols):
            continue
        row = dict(zip(cols, f))
        t = datetime(int(row["YY"]), int(row["MM"]), int(row["DD"]), int(row["hh"]),
                     int(row["mm"]), tzinfo=timezone.utc).timestamp()
        num = lambda k: None if row.get(k) in (None, "MM") else float(row[k])  # noqa: E731
        out.append((t, num("WVHT"), num("DPD")))
    return out


def nearest_buoy(lat, lng, positions: dict, ids=BUOYS):
    """(buoy id, km) of the nearest of *ids* that has a position."""
    best = None
    for b in ids:
        if b in positions:
            km = _haversine_km(lat, lng, *positions[b])
            if best is None or km < best[1]:
                best = (b, km)
    return best


def offset_point(lat, lng, bearing_deg, km):
    """The point *km* from (lat, lng) along *bearing_deg*, on a local flat-earth scale
    (110.574 km per degree of latitude, 111.320 x cos(lat) per degree of longitude): well
    inside a metre at the 10 km this is used for."""
    b = math.radians(bearing_deg)
    return (lat + km * math.cos(b) / 110.574,
            lng + km * math.sin(b) / (111.320 * math.cos(math.radians(lat))))


def first_land_km(land, lat, lng, bearings):
    """For each bearing, the distance (km) to the first land sample along the ray (samples
    every RAY_STEP_KM out to RAY_KM), None if the ray is open. One land query for all rays."""
    n = int(round(RAY_KM / RAY_STEP_KM))
    pts = [offset_point(lat, lng, b, RAY_STEP_KM * (i + 1)) for b in bearings for i in range(n)]
    hits = land.contains([p[0] for p in pts], [p[1] for p in pts])
    out = []
    for k in range(len(bearings)):
        ray = hits[k * n:(k + 1) * n]
        first = next((i for i, h in enumerate(ray) if h), None)
        out.append(None if first is None else round(RAY_STEP_KM * (first + 1), 2))
    return out


def classify(land, lat, lng, facing_deg) -> dict:
    """The point's coastline class (module docstring), the facing ray's first land, and the
    width of the open sector of rays (every RAY_EVERY_DEG) that holds the ray nearest the
    facing."""
    if land.contains([lat], [lng])[0]:
        return {"class": "on land", "facing_land_km": 0.0, "open_width_deg": 0}
    n = 360 // RAY_EVERY_DEG
    hits = first_land_km(land, lat, lng, [facing_deg] + [i * RAY_EVERY_DEG for i in range(n)])
    facing_km = hits[0]
    open_ray = [h is None for h in hits[1:]]
    k = int(round(facing_deg / RAY_EVERY_DEG)) % n
    width = 0
    if open_ray[k]:
        if all(open_ray):
            width = 360
        else:
            width = RAY_EVERY_DEG
            for step in (1, -1):
                j = (k + step) % n
                while open_ray[j]:
                    width += RAY_EVERY_DEG
                    j = (j + step) % n
    if facing_km is not None:
        cls = "behind land"
    elif width >= OPEN_WIDTH_DEG:
        cls = "open ocean"
    else:
        cls = "pass/inlet"
    return {"class": cls, "facing_land_km": facing_km, "open_width_deg": width}


def nearest_open_ocean(land, lat, lng, facing_deg, half_deg=90):
    """The nearest point within *half_deg* of the facing, on rings SEARCH_STEP_KM apart, that
    classifies as open ocean: {lat, lng, km, bearing_deg}, or None within SEARCH_MAX_KM."""
    offsets = sorted(range(-half_deg, half_deg + 1, SEARCH_EVERY_DEG), key=lambda o: (abs(o), o))
    for i in range(1, int(round(SEARCH_MAX_KM / SEARCH_STEP_KM)) + 1):
        km = SEARCH_STEP_KM * i
        for off in offsets:
            brg = (facing_deg + off) % 360
            plat, plng = offset_point(lat, lng, brg, km)
            if classify(land, plat, plng, facing_deg)["class"] == "open ocean":
                return {"lat": round(plat, 5), "lng": round(plng, 5), "km": round(km, 2),
                        "bearing_deg": round(brg, 1)}
    return None


def off_angle(a, b):
    return abs(((a - b + 180.0) % 360.0) - 180.0)


def seaward_cell(cells, spot_lat, spot_lng, facing_deg, is_open=None):
    """The nearest cell (from *cells*, [(li, lj, lat, lng, is_wet)]) within SEAWARD_HALF_DEG of
    the facing and SEAWARD_MAX_KM of the spot that is wet and, when *is_open* is given,
    open ocean by it. {li, lj, lat, lng, km, bearing_deg} or None."""
    cand = []
    for li, lj, lat, lng, wet in cells:
        km = _haversine_km(spot_lat, spot_lng, lat, lng)
        brg = _bearing(spot_lat, spot_lng, lat, lng)
        if km <= SEAWARD_MAX_KM and off_angle(brg, facing_deg) <= SEAWARD_HALF_DEG:
            cand.append((km, li, lj, lat, lng, brg, wet))
    for km, li, lj, lat, lng, brg, wet in sorted(cand):
        if wet() and (is_open is None or is_open(lat, lng)):
            return {"li": li, "lj": lj, "lat": round(lat, 5), "lng": round(lng, 5),
                    "km": round(km, 2), "bearing_deg": round(brg, 1)}
    return None


def shortest_lead(series_by_cycle: list[tuple[float, list[dict]]]) -> dict:
    """{hour epoch: (hs, tp, lead h)} taking, for every hour, the newest cycle at or before it.
    *series_by_cycle* is [(cycle epoch, [{valid_time, hs, tp}, ...])]."""
    out: dict = {}
    for cyc, series in sorted(series_by_cycle):
        for e in series:
            if e.get("hs") is None:
                continue
            t = datetime.fromisoformat(e["valid_time"].replace("Z", "+00:00")).timestamp()
            if t < cyc:
                continue
            out[int(t)] = (float(e["hs"]), e.get("tp"), round((t - cyc) / 3600.0, 1))
    return out


def pairs_vs(ours: dict, measured: dict) -> list[dict]:
    """[{hour, ours, measured, band, ...}] for hours in both {hour: (hs, ...)} dicts."""
    out = []
    for h in sorted(set(ours) & set(measured)):
        m = measured[h]
        out.append({"hour": h, "ours": ours[h][0], "measured": m[0],
                    "measured_tp": m[1], "band": period_band(m[1])})
    return out


def banded(pairs: list[dict]) -> dict:
    """stats over all pairs, by measured height (HEIGHT_BANDS) and by measured period."""
    return {"all": stats(pairs),
            "by_measured_height": {f"{lo}-{hi} m": stats([p for p in pairs if lo <= p["measured"] < hi])
                                   for lo, hi in HEIGHT_BANDS},
            "by_measured_period": {name: stats([p for p in pairs if p["band"] == name])
                                   for _, name in BANDS if any(p["band"] == name for p in pairs)}}


def suspect_vs_buoy(rows: list[dict], buoy_hourly: dict) -> dict:
    """Our stored hs/tp (*rows*: [{valid_time, hs, tp}]) against a buoy's hourly (hs, dpd)."""
    ours = {}
    for r in rows:
        if r.get("hs") is None:
            continue
        t = int(datetime.fromisoformat(r["valid_time"].replace("Z", "+00:00")).timestamp())
        ours[t] = (float(r["hs"]), None if r.get("tp") is None else float(r["tp"]))
    hours = sorted(set(ours) & set(buoy_hourly))
    tps = [ours[h][1] for h in hours if ours[h][1] is not None]
    dpds = [buoy_hourly[h][1] for h in hours if buoy_hourly[h][1] is not None]
    ratios = [ours[h][0] / buoy_hourly[h][0] for h in hours if buoy_hourly[h][0]]
    return {
        "paired_hours": len(hours),
        "our_hs_median_m": round(median([ours[h][0] for h in hours]), 2) if hours else None,
        "buoy_hs_median_m": round(median([buoy_hourly[h][0] for h in hours]), 2) if hours else None,
        "hs_ratio_median": round(median(ratios), 2) if ratios else None,
        "our_tp_median_s": round(median(tps), 2) if tps else None,
        "buoy_dpd_median_s": round(median(dpds), 2) if dpds else None,
        "our_share_under_3s": round(sum(t < SHELTERED_TP_S for t in tps) / len(tps), 3) if tps else None,
        "buoy_share_under_3s": round(sum(d < SHELTERED_TP_S for d in dpds) / len(dpds), 3) if dpds else None,
    }


# --------------------------------------------------------------------------- #
# Fetching                                                                      #
# --------------------------------------------------------------------------- #
def _text(url, timeout=60):
    import requests
    from .config import USER_AGENT
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=timeout)
    r.raise_for_status()
    return r.text


def etopo_depth_m(lat, lng, text=_text) -> float | None:
    """Water depth (m, positive) at the ETOPO 2022 15 arc-second cell nearest (lat, lng); a
    land elevation comes back negative. None when ERDDAP does not answer."""
    try:
        lines = text(ETOPO.format(lat=round(lat, 5), lng=round(lng, 5))).strip().splitlines()
        return round(-float(lines[2].split(",")[2]), 1)
    except Exception:  # noqa: BLE001
        return None


def buoy_hourly(bid, text=_text) -> dict:
    rows = parse_ndbc_realtime(text(NDBC_REALTIME.format(bid)))
    return hourly([r[0] for r in rows], [r[1] for r in rows], [r[2] for r in rows],
                  [r[1] is not None for r in rows])


def mop_point_hourly(station, open_nc=_open_nc) -> dict:
    ds = open_nc(MOP_VALIDATION.format(station))
    good = ([q == 1 for q in _arr(ds, "waveFlagPrimary")] if "waveFlagPrimary" in ds.variables
            else [True] * len(ds.variables["waveTime"]))
    return hourly(_arr(ds, "waveTime"), _arr(ds, "waveHs"), _arr(ds, "waveTp"), good)


def fetch_rows(client, name, cols, since, until=None):
    """A spot's rated forecast rows (source 'nwps'), SELECT only, paged."""
    spot = (client.table("spots").select("id").eq("name", name).execute().data or [])
    if not spot:
        return []
    rows, frm = [], 0
    while True:
        q = (client.table("forecasts").select(cols).eq("spot_id", spot[0]["id"])
             .eq("source", "nwps").gte("valid_time", since.isoformat()))
        if until is not None:
            q = q.lt("valid_time", until.isoformat())
        page = q.order("valid_time").range(frm, frm + 999).execute().data or []
        rows += page
        if len(page) < 1000:
            return rows
        frm += 1000


class Land:
    """GSHHG L1 land near a set of points: contains(lats, lngs) -> [bool]."""

    def __init__(self, geom):
        import shapely
        shapely.prepare(geom)
        self.geom = geom

    def contains(self, lats, lngs):
        import shapely
        return list(shapely.contains_xy(self.geom, list(lngs), list(lats)))

    @classmethod
    def around(cls, index, lat, lng, pad_deg=0.3):
        from shapely.geometry import box
        from shapely.ops import unary_union
        b = box(lng - pad_deg, lat - pad_deg, lng + pad_deg, lat + pad_deg)
        hits = [index.polygons[i] for i in index.polygon_tree.query(b)]
        return cls(unary_union([h.intersection(b) for h in hits]) if hits else b.difference(b))


# --------------------------------------------------------------------------- #
# Sections                                                                      #
# --------------------------------------------------------------------------- #
def load_suspects(path=RUN48 / "face_sheltered.csv"):
    with open(path) as f:
        return list(csv.DictReader(f))


def run48_window(path=RUN48 / "face_calibration_summary.json"):
    sh = json.load(open(path))["sheltered"]
    return datetime.fromisoformat(sh["since"]), datetime.fromisoformat(sh["until"])


def section_suspects(client, roster, suspects, since, until, text=_text) -> list[dict]:
    positions = parse_stations(text(NDBC_STATIONS))
    cache, out = {}, []
    for s in suspects:
        spot = roster.get(s["spot"], {})
        nb = nearest_buoy(spot["lat"], spot["lng"], positions)
        rec = {"spot": s["spot"], "state": s["state"], "buoy": None, "buoy_km": None}
        if nb:
            bid, km = nb
            if bid not in cache:
                cache[bid] = buoy_hourly(bid, text)
            rec.update(buoy=bid, buoy_km=round(km, 1),
                       **suspect_vs_buoy(fetch_rows(client, s["spot"], "valid_time, hs, tp",
                                                    since, until), cache[bid]))
        out.append(rec)
    return out


def section_coastline(index, roster, names, depth=etopo_depth_m) -> list[dict]:
    out = []
    for name in names:
        s = roster.get(name, {})
        nl, ng, facing = s.get("nwps_node_lat"), s.get("nwps_node_lng"), s.get("orientation_deg")
        rec = {"spot": name, "node_lat": nl, "node_lng": ng, "orientation_deg": facing}
        if nl is None or facing is None:
            rec["class"] = "no baked node" if nl is None else "no orientation"
            out.append(rec)
            continue
        land = Land.around(index, nl, ng)
        rec.update(classify(land, nl, ng, facing))
        rec["node_depth_m"] = depth(nl, ng)
        rec["node_km_from_spot"] = round(_haversine_km(s["lat"], s["lng"], nl, ng), 2)
        near = nearest_open_ocean(Land.around(index, s["lat"], s["lng"]), s["lat"], s["lng"], facing)
        rec["nearest_open_ocean"] = near
        if near:
            near["depth_m"] = depth(near["lat"], near["lng"])
        out.append(rec)
    return out


def section_mop(client, roster, since_ours, open_nc=_open_nc) -> dict:
    out = {}
    mop_spots = [s for s in roster.values() if s.get("swell_window_source") == "cdip_mop"]
    for st in MOP_BUOYS:
        rec = {}
        try:
            buoy, span = cdip_hourly(st, open_nc)
            rec["buoy_span"] = span
            mop = mop_point_hourly(st, open_nc)
            rec["mop_at_buoy"] = banded(pairs_vs(mop, buoy))
        except OSError as e:
            rec["error"] = f"{type(e).__name__}: {e}"[:200]
            out[st] = rec
            continue
        ds = open_nc(f"https://thredds.cdip.ucsd.edu/thredds/dodsC/cdip/realtime/{st}p1_rt.nc")
        blat, blng = float(ds.variables["metaDeployLatitude"][:]), float(ds.variables["metaDeployLongitude"][:])
        near = sorted((_haversine_km(blat, blng, s["lat"], s["lng"]), s["name"]) for s in mop_spots)
        if near and near[0][0] <= MOP_SPOT_KM:
            km, name = near[0]
            rows = fetch_rows(client, name, "valid_time, tp, swell_tp, swell_source, face_ft_raw",
                              since_ours)
            ours = {}
            for r in rows:
                if rater_of(r.get("swell_source")) != "MOP":
                    continue
                h = sized_from(r)
                if h is not None:
                    t = int(datetime.fromisoformat(r["valid_time"].replace("Z", "+00:00")).timestamp())
                    ours[t] = (h,)
            rec["our_mop_spot"] = {"spot": name, "km": round(km, 1), **banded(pairs_vs(ours, buoy))}
        else:
            rec["our_mop_spot"] = None
        out[st] = rec
    return out


def list_all_cycles(wfo):
    """Every (date, HH) NOMADS lists for *wfo* in the newest three date folders, oldest first,
    from forecast/nwps.py's own listing functions."""
    from .forecast import nwps
    region = nwps.WFO_TO_REGION.get(wfo)
    root = nwps._list_root_dates() or {}
    cycles = []
    for d in root.get(region, [])[:3]:
        lst = nwps._list_wfo_cycles(region, d, wfo)
        cycles += [(d, hh) for hh in (lst.cycles or ())]
    return region, sorted(cycles)


def nwps_cells(datasets, spot_lat, spot_lng, pad_deg=0.15):
    """[(li, lj, lat, lng, is_wet)] for the cells of the wave grid within *pad_deg* of the spot;
    is_wet is forecast/nwps.py's own _cell_is_water, deferred."""
    import numpy as np
    from .forecast import nwps
    ds, _ = nwps._find_wave_dataset(datasets)
    lats = np.asarray(ds["latitude"].values)
    lngs = np.asarray(ds["longitude"].values)
    lng0 = nwps._normalize_longitude(ds, spot_lng)
    shift = lng0 - spot_lng
    out = []
    for li in np.where(np.abs(lats - spot_lat) <= pad_deg)[0]:
        for lj in np.where(np.abs(lngs - lng0) <= pad_deg)[0]:
            out.append((int(li), int(lj), float(lats[li]), float(lngs[lj]) - shift,
                        lambda li=int(li), lj=int(lj): nwps._cell_is_water(datasets, li, lj)))
    return out


def section_seaward(roster, index, since, until, workdir, open_nc=_open_nc, depth=etopo_depth_m):
    from .forecast import nwps
    out = {}
    for name, inst in SEAWARD_SPOTS:
        s = roster[name]
        wfo, facing = s.get("nwps_wfo"), float(s["orientation_deg"])
        rec = {"spot": name, "wfo": wfo, "orientation_deg": facing,
               "node": {"lat": s.get("nwps_node_lat"), "lng": s.get("nwps_node_lng")}}
        rec["node"]["km"] = round(_haversine_km(s["lat"], s["lng"], s["nwps_node_lat"], s["nwps_node_lng"]), 2)
        rec["node"]["bearing_deg"] = round(_bearing(s["lat"], s["lng"], s["nwps_node_lat"], s["nwps_node_lng"]), 1)
        rec["node"]["depth_m"] = depth(s["nwps_node_lat"], s["nwps_node_lng"])
        region, cycles = list_all_cycles(wfo)
        rec["cycles_listed"] = [f"{d}{hh}" for d, hh in cycles]
        node_series, cell_series, cell = [], [], None
        land = Land.around(index, s["lat"], s["lng"]) if index is not None else None
        for d, hh in cycles:
            path = Path(workdir) / f"{wfo}_{d}_{hh}.grib2"
            if not nwps._download_filtered(region, wfo, d, hh, path):
                continue
            try:
                ds = nwps._open_grib_datasets(path)
                if cell is None:
                    is_open = None
                    if land is not None:
                        is_open = (lambda la, ln: classify(land, la, ln, facing)["class"]
                                   == "open ocean")
                    cell = seaward_cell(nwps_cells(ds, s["lat"], s["lng"]), s["lat"], s["lng"],
                                        facing, is_open)
                cyc = datetime(int(d[:4]), int(d[4:6]), int(d[6:]), int(hh), tzinfo=timezone.utc).timestamp()
                node_series.append((cyc, nwps._extract_time_series_from_datasets(
                    ds, s["nwps_node_lat"], s["nwps_node_lng"])))
                if cell is not None:
                    cell_series.append((cyc, nwps._extract_time_series_from_datasets(ds, cell["lat"], cell["lng"])))
            finally:
                path.unlink(missing_ok=True)
        rec["cycles_read"] = len(node_series)
        if cell is not None:
            cell["depth_m"] = depth(cell["lat"], cell["lng"])
            cell["coastline_checked"] = index is not None
        rec["seaward_cell"] = cell
        measured = (frf_8m_hourly(since, until, open_nc)[0] if inst == "frf-8m"
                    else cdip_hourly(inst.split("-")[1], open_nc)[0])
        node_h, cell_h = shortest_lead(node_series), shortest_lead(cell_series)
        both = set(node_h) & set(cell_h) & set(measured)
        rec["node_vs_measured"] = banded(pairs_vs({h: node_h[h] for h in both}, measured))
        rec["cell_vs_measured"] = banded(pairs_vs({h: cell_h[h] for h in both}, measured))
        rec["hours"] = len(both)
        out[name] = rec
    return out


# --------------------------------------------------------------------------- #
# Report                                                                        #
# --------------------------------------------------------------------------- #
def _fmt(s):
    if not s or not s.get("n"):
        return "n 0"
    return (f"n {s['n']:5d}  ratio {s['ratio_median']:.2f}  bias {s['bias_m']:+.2f} m  "
            f"within 20% {s['within_20']:.0%}  30% {s['within_30']:.0%}  r {s['r']}  "
            f"(measured {s['mean_measured_m']:.2f} m, ours {s['mean_ours_m']:.2f} m)")


def _print_banded(label, b):
    print(f"  {label}: {_fmt(b['all'])}")
    for k, v in b["by_measured_height"].items():
        print(f"      measured {k:10s} {_fmt(v)}")
    for k, v in b["by_measured_period"].items():
        print(f"      period   {k:10s} {_fmt(v)}")


def print_report(rep):
    if "suspects" in rep:
        print(f"=== 1. sheltered suspects against the nearest buoy, {rep['window'][0]} to {rep['window'][1]} ===")
        for r in rep["suspects"]:
            print(f"  {r['spot']:22s} buoy {r['buoy']} {r['buoy_km']} km, {r.get('paired_hours')} h: "
                  f"hs ours {r.get('our_hs_median_m')} / buoy {r.get('buoy_hs_median_m')} m "
                  f"(ratio {r.get('hs_ratio_median')}); period ours {r.get('our_tp_median_s')} / "
                  f"buoy {r.get('buoy_dpd_median_s')} s; under 3 s ours {r.get('our_share_under_3s')} "
                  f"/ buoy {r.get('buoy_share_under_3s')}")
    if "coastline" in rep:
        print("\n=== 2. NWPS sample points against the GSHHG coastline ===")
        for r in rep["coastline"]:
            n = r.get("nearest_open_ocean")
            print(f"  {r['spot']:22s} {r.get('class')}: node ({r.get('node_lat')}, {r.get('node_lng')}) "
                  f"{r.get('node_km_from_spot')} km from the spot, depth {r.get('node_depth_m')} m, "
                  f"facing ray land at {r.get('facing_land_km')} km, open sector {r.get('open_width_deg')} deg; "
                  f"nearest open ocean: " + ("none within 10 km" if not n else
                  f"({n['lat']}, {n['lng']}) {n['km']} km at {n['bearing_deg']} deg, depth {n.get('depth_m')} m"))
    if "mop" in rep:
        print("\n=== 3. MOP's own height against CDIP ===")
        for st, r in rep["mop"].items():
            if "error" in r:
                print(f"  CDIP {st}: {r['error']}")
                continue
            print(f"  CDIP {st} (buoy record {r['buoy_span']})")
            _print_banded("MOP at the buoy (BP nowcast)", r["mop_at_buoy"])
            o = r["our_mop_spot"]
            if o:
                _print_banded(f"our MOP-tier height at {o['spot']} ({o['km']} km)", o)
            else:
                print(f"  no MOP-tier spot within {MOP_SPOT_KM} km")
    if "seaward" in rep:
        print("\n=== 4. the nearest clearly seaward NWPS cell ===")
        for name, r in rep["seaward"].items():
            c, n = r.get("seaward_cell"), r["node"]
            print(f"  {name} ({r['wfo']}, facing {r['orientation_deg']}): cycles listed {len(r['cycles_listed'])}, "
                  f"read {r['cycles_read']}, paired hours {r['hours']}")
            print(f"    baked node ({n['lat']}, {n['lng']}) {n['km']} km at {n['bearing_deg']} deg, depth {n['depth_m']} m")
            print("    seaward cell " + ("none found" if not c else
                  f"({c['lat']}, {c['lng']}) {c['km']} km at {c['bearing_deg']} deg, depth {c['depth_m']} m, "
                  f"coastline checked {c['coastline_checked']}"))
            _print_banded("baked node vs measured", r["node_vs_measured"])
            _print_banded("seaward cell vs measured", r["cell_vs_measured"])
    for k, v in rep.get("errors", {}).items():
        print(f"\nSECTION {k} FAILED: {v}")


def write_csvs(rep, d):
    os.makedirs(d, exist_ok=True)
    if rep.get("suspects"):
        with open(os.path.join(d, "suspects_vs_buoys.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rep["suspects"][0]))
            w.writeheader()
            w.writerows(rep["suspects"])
    if rep.get("coastline"):
        cols = ["spot", "class", "node_lat", "node_lng", "orientation_deg", "node_km_from_spot",
                "node_depth_m", "facing_land_km", "open_width_deg", "open_lat", "open_lng",
                "open_km", "open_bearing_deg", "open_depth_m"]
        with open(os.path.join(d, "coastline.csv"), "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rep["coastline"]:
                n = r.get("nearest_open_ocean") or {}
                w.writerow({**{k: r.get(k) for k in cols[:9]}, "open_lat": n.get("lat"),
                            "open_lng": n.get("lng"), "open_km": n.get("km"),
                            "open_bearing_deg": n.get("bearing_deg"), "open_depth_m": n.get("depth_m")})


SECTIONS = ("suspects", "coastline", "mop", "seaward")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--sections", default=",".join(SECTIONS))
    ap.add_argument("--out")
    ap.add_argument("--csv-dir")
    ap.add_argument("--roster", default=str(DEFAULT_ENRICHED_OUTPUT))
    ap.add_argument("--mop-since", default="2026-09-07T00:00:00+00:00",
                    help="our stored MOP-tier heights from here (face_ft_raw is stored since)")
    args = ap.parse_args(argv)
    wanted = [s for s in args.sections.split(",") if s]
    roster = {s["name"]: s for s in json.load(open(args.roster)) if s.get("name")}
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    suspects = load_suspects()
    since, until = run48_window()
    rep = {"window": [since.isoformat(), until.isoformat()], "errors": {}}
    index = None
    if "coastline" in wanted or "seaward" in wanted:
        from .enrichment.geodata import load_land_index
        index = load_land_index()
        if index is None:
            rep["errors"]["coastline"] = "GSHHG L1 not found in pipeline/geodata"
    jobs = {
        "suspects": lambda: section_suspects(client, roster, suspects, since, until),
        "coastline": lambda: section_coastline(
            index, roster, [s["spot"] for s in suspects] + [n for n, _ in SEAWARD_SPOTS]),
        "mop": lambda: section_mop(client, roster, datetime.fromisoformat(args.mop_since)),
        "seaward": lambda: section_seaward(roster, index, datetime.now(timezone.utc) - timedelta(days=5),
                                           datetime.now(timezone.utc), tempfile.mkdtemp()),
    }
    for name in wanted:
        if name == "coastline" and index is None:
            continue
        try:
            rep[name] = jobs[name]()
        except Exception as e:  # noqa: BLE001 — report and run the rest
            rep["errors"][name] = f"{type(e).__name__}: {e}"[:400]
    print_report(rep)
    if args.out:
        with open(args.out, "w") as f:
            json.dump(rep, f, indent=1, default=str)
    if args.csv_dir:
        write_csvs(rep, args.csv_dir)
    return 0


if __name__ == "__main__":
    sys.exit(main())
