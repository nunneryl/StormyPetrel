"""Build etopo_15s.json: ETOPO 2022 (v1, 15 arc-second) water depth around each spot the face
follow-up measures, from NOAA CoastWatch ERDDAP, so the runner does not depend on ERDDAP
answering it (in run 49 every request from the runner came back empty).

Each grid covers PAD_DEG of latitude either side of the spot and the same distance in longitude
(PAD_DEG / cos(lat)), at ETOPO's own 15 arc-second spacing. depth_m is positive in water and
negative on land, one row per latitude from south to north, one value per longitude from west to
east, rounded to 0.1 m.

    python -I docs/face_validation/etopo/build_etopo.py > docs/face_validation/etopo/etopo_15s.json
"""
import json
import math
import sys
import urllib.request
from datetime import date

URL = ("https://coastwatch.pfeg.noaa.gov/erddap/griddap/ETOPO_2022_v1_15s.csv"
       "?z%5B({a}):1:({b})%5D%5B({c}):1:({d})%5D")
PAD_DEG = 0.2
SPOTS = ["Mexico Beach", "Spring Ave", "St. Pete Beach", "Treasure Island FL", "Madeira Beach",
         "Whitney Beach", "Oak Island Pier", "Coolidge", "Siesta Key", "Gasparilla", "Navarre Beach",
         "Twin Piers", "Naples Pier", "St Andrews Park", "Redington Pier", "Destin Jetty",
         "Duck Pier", "Jetty Park Cocoa Beach"]


def grid(lat, lng):
    padl = PAD_DEG / math.cos(math.radians(lat))
    req = urllib.request.Request(URL.format(a=round(lat - PAD_DEG, 4), b=round(lat + PAD_DEG, 4),
                                            c=round(lng - padl, 4), d=round(lng + padl, 4)),
                                 headers={"User-Agent": "StormyPetrel-analysis"})
    rows = urllib.request.urlopen(req, timeout=180).read().decode().splitlines()[2:]
    cells = {}
    for r in rows:
        a, b, z = r.split(",")
        cells[(float(a), float(b))] = round(-float(z), 1)
    lats = sorted({k[0] for k in cells})
    lngs = sorted({k[1] for k in cells})
    return {"lat0": lats[0], "lng0": lngs[0],
            "dlat": (lats[-1] - lats[0]) / (len(lats) - 1), "dlng": (lngs[-1] - lngs[0]) / (len(lngs) - 1),
            "nlat": len(lats), "nlng": len(lngs),
            "depth_m": [[cells[(a, b)] for b in lngs] for a in lats]}


def main():
    roster = {s["name"]: s for s in json.load(open("pipeline/spots_enriched.json"))}
    out = {"source": "ETOPO 2022 v1 15 arc-second via coastwatch.pfeg.noaa.gov ERDDAP",
           "made": date.today().isoformat(), "pad_deg": PAD_DEG, "grids": {}}
    for name in SPOTS:
        s = roster[name]
        out["grids"][name] = grid(s["lat"], s["lng"])
    json.dump(out, sys.stdout, separators=(",", ":"))


if __name__ == "__main__":
    main()
