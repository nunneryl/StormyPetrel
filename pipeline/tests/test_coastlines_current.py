"""The swell compass's land (pipeline/coastline_paths.py, frontend/data/coastlines.json).

1. CURRENT. Every roster spot has a path drawn at its current position, so a spot added or moved
   fails CI until `python -m pipeline.coastline_paths --only SLUG` (or a full run) is committed.
2. SMALL AND WELL FORMED. Every path is under MAX_BYTES and is moves and relative lines in whole
   units, inside the RADIUS_KM circle.
3. THE GEOMETRY, worked by hand on a made-up coast: the projection (north up), the path format, and
   the land clipped to the circle.

No expected value comes from the code under test.
"""
from __future__ import annotations

import copy
import json
import re
from pathlib import Path

from shapely.geometry import box
from shapely.strtree import STRtree

from pipeline import coastline_paths as C
from pipeline.enrichment.geodata import LandIndex

ROOT = Path(__file__).resolve().parents[2]
ROSTER = json.loads((ROOT / "pipeline" / "spots_enriched.json").read_text(encoding="utf-8"))
DATA = json.loads((ROOT / "frontend" / "data" / "coastlines.json").read_text(encoding="utf-8"))


# 1. Current.
def test_every_roster_spot_has_land_drawn_at_its_position():
    stale = C.stale(ROSTER, DATA)
    assert stale == [], (
        f"{len(stale)} spot(s) have no compass land, or land drawn where the spot used to be: "
        f"{stale}. Run `python -m pipeline.coastline_paths --only {','.join(stale[:5])}` (needs "
        f"pipeline/geodata) and commit frontend/data/coastlines.json.")


def test_a_moved_or_new_spot_is_stale():
    roster = copy.deepcopy(ROSTER)
    lovers = next(s for s in roster if s["name"] == "Lovers Point")
    lovers["lat"] = float(lovers["lat"]) + 0.001          # about 110 m north
    roster.append({"name": "Nowhere Point", "lat": 0.0, "lng": 0.0})
    assert C.stale(roster, DATA) == ["lovers-point", "nowhere-point"]


# 2. Small and well formed.
PATH = re.compile(r"^(M-?\d+ -?\d+l(?:-?\d+(?: |(?=-))-?\d+(?: |(?=-))?)+z)+$")


def test_the_file_states_its_source_and_scale():
    assert DATA["radius_km"] == 25.0 and DATA["units"] == 100
    assert DATA["source"].startswith("GSHHG f L1")


def test_every_path_is_under_4000_bytes_and_well_formed():
    assert C.MAX_BYTES == 4000
    bad = [k for k, v in DATA["spots"].items() if len(v["d"].encode()) > 4000 or not PATH.match(v["d"])]
    assert bad == []


def test_every_path_stays_inside_the_circle():
    out = []
    for k, v in DATA["spots"].items():
        for m in re.finditer(r"M(-?\d+) (-?\d+)l([^Mz]*)z", v["d"]):
            x, y = int(m.group(1)), int(m.group(2))
            nums = [int(n) for n in re.findall(r"-?\d+", m.group(3))]
            for dx, dy in zip(nums[0::2], nums[1::2]):
                x, y = x + dx, y + dy
                if x * x + y * y > 101 * 101:     # rounding can put a point half a unit out
                    out.append(k)
                    break
    assert out == []


# 3. The geometry, by hand.
def test_projection_north_up():
    # At the equator 0.1 deg of longitude is 111.320 x 4 x 0.1 = 44.528 units east; 0.2 deg of
    # latitude is 110.574 x 4 x 0.2 = 88.4592 units north, so y = -88.4592 (SVG y points down).
    f = C.project(0.0, 0.0)
    import numpy as np
    xy = f(np.array([[0.1, 0.2]]))
    assert abs(xy[0, 0] - 44.528) < 1e-6 and abs(xy[0, 1] + 88.4592) < 1e-6
    # At 60 deg N a degree of longitude is half as long (cos 60 = 0.5): 0.1 deg east of the spot is
    # 111.320 x 0.5 x 4 x 0.1 = 22.264 units; latitude is unchanged, 88.4592 units for 0.2 deg.
    f60 = C.project(60.0, -120.0)
    xy60 = f60(np.array([[-119.9, 60.2]]))
    assert abs(xy60[0, 0] - 22.264) < 1e-6 and abs(xy60[0, 1] + 88.4592) < 1e-6


def test_ring_path_format():
    assert C._ring_path([(0, 0), (10, 0), (10, -5), (0, 0)]) == "M0 0l10 0 0-5z"
    assert C._ring_path([(-3, 4), (-5, 4), (-5, 1), (-3, 4)]) == "M-3 4l-2 0 0-3z"
    # Rounded to whole units, a repeated point is dropped; under three points is nothing.
    assert C._ring_path([(0.4, 0), (0.2, 0.1), (10, 0), (10, -5)]) == "M0 0l10 0 0-5z"
    assert C._ring_path([(0, 0), (0.3, 0.2), (0, 0)]) == ""


def _index(*polys):
    return LandIndex(polygons=list(polys), polygon_tree=STRtree(list(polys)), coastlines=[],
                     coastline_tree=None)


def test_land_to_the_north_is_drawn_above_and_clipped_to_the_circle():
    # Land from 0.1 deg north of the spot to well past 25 km (1 deg), 2 deg wide: its southern
    # edge is 0.1 x 110.574 x 4 = 44.2296 units north, y = -44.23; the circle cuts it off at 100.
    spot_lat, spot_lng = 0.0, 0.0
    land = C.land_near(_index(box(-1.0, 0.1, 1.0, 1.0)), spot_lat, spot_lng)
    minx, miny, maxx, maxy = land.bounds
    assert abs(maxy + 44.2296) < 1e-3          # its south edge, above the spot
    assert abs(miny + 100) < 0.01               # the circle's top
    assert abs(minx + 89.7) < 0.2 and abs(maxx - 89.7) < 0.2   # sqrt(100^2 - 44.23^2) = 89.69
    d, tol = C.spot_path(land)
    assert tol == 0.25 and d.startswith("M") and d.endswith("z") and len(d) < 4000


def test_no_land_within_25_km_is_an_empty_path():
    # Land 0.3 deg east is 0.3 x 111.32 = 33.4 km away.
    land = C.land_near(_index(box(0.3, -0.5, 1.0, 0.5)), 0.0, 0.0)
    assert land is None and C.spot_path(land) == ("", None)
