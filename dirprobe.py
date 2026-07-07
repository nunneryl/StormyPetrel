#!/usr/bin/env python3
"""THROWAWAY diagnostic — do NOT commit. Read-only.

Compare the NWPS box model's PRIMARY SWELL DIRECTION (dirpw) + swh sampled at:
  1. buoy 44097 (where trust_check samples),
  2. our RI south-shore spot nodes (by slug from spots_enriched.json; nwps_node_lat/lng
     if present, else the spot's lat/lng),
  3. a few points stepped seaward (south) of the buoy toward open ocean.

Reuses the existing NWPS helpers — changes nothing, tags nothing. Needs NOMADS +
cfgrib/eccodes, so run it on the Mac:

    python3 dirprobe.py                # latest box cycle, f000
    python3 dirprobe.py --wfo box --fh 0
"""
import argparse
import json
import sys
from pathlib import Path

# Find the repo root so `import pipeline...` resolves, whether this file lives in the
# repo or is copied elsewhere and run from within a checkout.
_start = Path(__file__).resolve()
REPO = None
for base in (Path.cwd(), *Path.cwd().parents, *_start.parents):
    if (base / "pipeline" / "forecast" / "nwps_nearshore.py").exists():
        REPO = base
        break
if REPO is None:
    sys.exit("run this from inside the StormyPetrel checkout (couldn't find pipeline/)")
sys.path.insert(0, str(REPO))

from pipeline.forecast.nwps_nearshore import (   # noqa: E402
    load_cycle, _nearest_cell, _node_value, _slug, ENRICHED,
)

BUOY = ("44097 buoy (trust_check samples here)", 40.967, -71.124)
SPOT_SLUGS = ["point-judith", "point-judith-south", "narragansett-beach", "scarborough",
              "matunuck", "east-matunuck-state-beach", "misquamicut-state-beach", "weekapaug"]
SEAWARD_STEPS = (0.1, 0.2, 0.3)   # degrees south of the buoy, toward open ocean


def _resolve_spots():
    """(label, lat, lng, src) per requested slug; None lat when absent from roster."""
    roster = {}
    for s in json.loads(ENRICHED.read_text()):
        roster.setdefault(_slug(s.get("name")), s)
        if s.get("slug"):
            roster.setdefault(s["slug"], s)
    pts = []
    for slug in SPOT_SLUGS:
        s = roster.get(slug)
        if not s:
            pts.append((f"{slug} (NOT in roster)", None, None, "-"))
            continue
        nlat, nlng = s.get("nwps_node_lat"), s.get("nwps_node_lng")
        if nlat is not None and nlng is not None:
            pts.append((slug, float(nlat), float(nlng), "nwps_node"))
        else:
            pts.append((slug, float(s["lat"]), float(s["lng"]), "spot lat/lng"))
    return pts


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--wfo", default="box")
    ap.add_argument("--fh", type=int, default=0, help="forecast hour to sample (default f000)")
    a = ap.parse_args()

    points = [(BUOY[0], BUOY[1], BUOY[2], "given (buoy)")]
    points += _resolve_spots()
    for d in SEAWARD_STEPS:
        points.append((f"seaward {d:.1f} deg S of 44097", round(BUOY[1] - d, 4), BUOY[2], "offset"))

    try:
        cyc = load_cycle(a.wfo)   # cycle=None -> find_latest_cycle(wfo); needs NOMADS+cfgrib
    except Exception as e:  # noqa: BLE001
        cyc = None
        print(f"\n[!] could not load a live {a.wfo} cycle ({type(e).__name__}: {e}).")
        print("    This probe needs NOMADS + cfgrib/eccodes — run it on the Mac.")
        print("    (Spot resolution below is still real; model columns are '—'.)")

    fh = a.fh
    if cyc and fh not in cyc["steps"]:
        fh = min(cyc["steps"])
        print(f"\n[i] fh {a.fh} not in cycle; using nearest available fh={fh}")

    when = f"cycle {cyc['cycle_dt']:%Y-%m-%d %HZ}" if cyc else "(no live cycle — Mac only)"
    print(f"\nNWPS {a.wfo.upper()} dirpw / swh probe — fh={fh}  ·  {when}")
    hdr = (f"  {'label':42}{'req lat,lng':21}{'node lat,lng':21}"
           f"{'dist_m':>7}{'dirpw':>7}{'swh_m':>7}  src")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    for label, lat, lng, src in points:
        if lat is None:
            print(f"  {label:42}{'—':21}{'—':21}{'—':>7}{'—':>7}{'—':>7}  {src}")
            continue
        req = f"{lat:.4f},{lng:.4f}"
        if not cyc:
            print(f"  {label:42}{req:21}{'—':21}{'—':>7}{'—':>7}{'—':>7}  {src}")
            continue
        cell = _nearest_cell(cyc, lat, lng)
        if not cell:
            print(f"  {label:42}{req:21}{'no wet cell':21}{'—':>7}{'—':>7}{'—':>7}  {src}")
            continue
        i, j = cell[0], cell[1]
        nlat, nlng = float(cyc["lats"][i, j]), float(cyc["lons"][i, j])
        dirpw = _node_value(cyc, "dirpw", fh, i, j)
        swh = _node_value(cyc, "swh", fh, i, j)
        node = f"{nlat:.4f},{nlng:.4f}"
        dm = str(round(cell[2] * 1000))
        dd = f"{dirpw:.0f}" if dirpw is not None else "—"
        sh = f"{swh:.2f}" if swh is not None else "—"
        print(f"  {label:42}{req:21}{node:21}{dm:>7}{dd:>7}{sh:>7}  {src}")


if __name__ == "__main__":
    main()
