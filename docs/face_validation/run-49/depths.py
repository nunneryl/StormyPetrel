"""Depths that run 49 could not read on the runner (every ETOPO request there came back empty),
computed afterwards from the same source: ETOPO 2022, 15 arc-second, via NOAA CoastWatch
ERDDAP. For each spot in run 49's coastline table: the depth at its NWPS sample point (the grid
cell nearest it), and the nearest ETOPO cell within 30 deg of the spot's facing that is at least
10 m deep (MOP's contour), with its distance and bearing from the spot; and the depth at the
two seaward NWPS cells of section 4.

    python -I depths.py face_followup.json > depths.json
"""
import json
import math
import sys
import urllib.request

URL = ("https://coastwatch.pfeg.noaa.gov/erddap/griddap/ETOPO_2022_v1_15s.csv"
       "?z%5B({a}):1:({b})%5D%5B({c}):1:({d})%5D")
PAD = 0.15
DEEP_M = 10.0
HALF_DEG = 30.0


def grid(lat, lng):
    padl = PAD / math.cos(math.radians(lat))
    req = urllib.request.Request(URL.format(a=round(lat - PAD, 4), b=round(lat + PAD, 4),
                                            c=round(lng - padl, 4), d=round(lng + padl, 4)),
                                 headers={"User-Agent": "StormyPetrel-analysis"})
    rows = urllib.request.urlopen(req, timeout=120).read().decode().splitlines()[2:]
    return [(float(a), float(b), -float(z)) for a, b, z in (r.split(",") for r in rows)]


def km(a, b, c, d):
    r = math.radians
    h = math.sin(r(c - a) / 2) ** 2 + math.cos(r(a)) * math.cos(r(c)) * math.sin(r(d - b) / 2) ** 2
    return 6371.0 * 2 * math.asin(math.sqrt(h))


def bearing(a, b, c, d):
    dl = math.radians(d - b)
    y = math.sin(dl) * math.cos(math.radians(c))
    x = (math.cos(math.radians(a)) * math.sin(math.radians(c))
         - math.sin(math.radians(a)) * math.cos(math.radians(c)) * math.cos(dl))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def depth_at(g, lat, lng):
    return min(g, key=lambda p: (p[0] - lat) ** 2 + ((p[1] - lng) * math.cos(math.radians(lat))) ** 2)[2]


def main(path):
    rep = json.load(open(path))
    spots = {s["name"]: s for s in json.load(open("pipeline/spots_enriched.json"))}
    out = {"source": "ETOPO 2022 v1 15 arc-second, coastwatch.pfeg.noaa.gov ERDDAP",
           "deep_m": DEEP_M, "half_deg": HALF_DEG, "spots": {}, "seaward_cells": {}}
    for r in rep["coastline"]:
        s = spots[r["spot"]]
        g = grid(s["lat"], s["lng"])
        facing = s["orientation_deg"]
        cand = []
        for la, ln, dep in g:
            if dep < DEEP_M:
                continue
            brg = bearing(s["lat"], s["lng"], la, ln)
            if abs(((brg - facing + 180) % 360) - 180) <= HALF_DEG:
                cand.append((km(s["lat"], s["lng"], la, ln), la, ln, dep, brg))
        near = min(cand) if cand else None
        out["spots"][r["spot"]] = {
            "node_depth_m": round(depth_at(g, r["node_lat"], r["node_lng"]), 1),
            "spot_cell_depth_m": round(depth_at(g, s["lat"], s["lng"]), 1),
            "nearest_10m_within_30deg": None if near is None else {
                "lat": near[1], "lng": near[2], "km": round(near[0], 2), "depth_m": round(near[3], 1),
                "bearing_deg": round(near[4], 1)}}
    for name, r in rep["seaward"].items():
        c = r["seaward_cell"]
        g = grid(c["lat"], c["lng"])
        out["seaward_cells"][name] = {"lat": c["lat"], "lng": c["lng"],
                                      "depth_m": round(depth_at(g, c["lat"], c["lng"]), 1)}
    json.dump(out, sys.stdout, indent=1)


if __name__ == "__main__":
    main(sys.argv[1])
