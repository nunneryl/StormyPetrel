"""pipeline/nwps_cells.py, the read-only nwps-cells mode: the archive's height comparisons and
the grid window around a sample cell, on a fake database and a fake cycle.

NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST: every ratio, count and cell below is worked
out by hand in the comment beside it.

Run: python -m pytest pipeline/tests/test_nwps_cells.py
"""
from __future__ import annotations

import numpy as np

from pipeline import nwps_cells as N


def _row(t, hs, source="nwps_height_ww3_dir"):
    return {"valid_time": f"2026-09-{t}", "hs": hs, "swell_source": source}


def test_heights_keep_only_the_hours_nwps_sized():
    rows = [_row("23T17", 0.14), _row("23T18", 0.19, "nwps"), _row("23T19", 3.1, "ww3"),
            _row("23T20", None)]
    assert N.heights(rows) == {"2026-09-23T17": 0.14, "2026-09-23T18": 0.19}


def test_a_pair_counts_the_hours_under_forty_percent():
    # spot / pier: 0.3/3.0 = 0.1, 1.2/3.0 = 0.4 (not under), 0.2/0.4 = 0.5, 0.39/1.0 = 0.39;
    # the pier is 1 m or more on three of them (3.0, 3.0, 1.0), all in September; one spot
    # hour has no pier hour.
    spot = {"2026-09-23T17": 0.3, "2026-09-23T18": 1.2, "2026-09-24T00": 0.2,
            "2026-09-30T23": 0.39, "2026-10-01T00": 2.0}
    pier = {"2026-09-23T17": 3.0, "2026-09-23T18": 3.0, "2026-09-24T00": 0.4,
            "2026-09-30T23": 1.0}
    p = N.pair(spot, pier)
    assert p["all"] == {"hours": 4, "under_0_4": 2, "share_under_0_4": 0.5, "median_ratio": 0.395}
    assert p["reference_1m_up"] == {"hours": 3, "under_0_4": 2, "share_under_0_4": 0.667,
                                    "median_ratio": 0.39}
    assert list(p["by_month"]) == ["2026-09"]
    assert N.pair({}, pier)["all"] == {"hours": 0, "under_0_4": 0, "share_under_0_4": None,
                                       "median_ratio": None}


def test_neighbours_are_on_the_same_grid_and_within_30_km():
    # 0.27 deg of latitude is 30.0 km (0.27 x 111.2); 0.26 is 28.9 km
    nodes = {"a": ("mhx", 35.0, -75.5), "b": ("mhx", 35.26, -75.5), "c": ("mhx", 35.28, -75.5),
             "d": ("ilm", 35.1, -75.5)}
    assert N.neighbours_of(nodes) == {"a": ["b"], "b": ["a", "c"], "c": ["b"], "d": []}


def test_the_neighbour_scan_flags_a_spot_a_quarter_of_whose_hours_read_under_forty_percent():
    """Spot x against neighbours y and z over 24 hours: their median is 2.0 m every hour; x
    reads 0.5 m (0.25) on 6 hours and 2.0 m (1.0) on 18, so 6 of 24 = 0.25 under 0.4:
    flagged; median ratio 1.0. y and z each against x and the other: median of (x, z) or
    (x, y), at least 1.25 m, ratio at most 1.6, never under 0.4."""
    hours = [f"2026-09-{d:02d}T00" for d in range(1, 25)]
    hs = {"x": {t: (0.5 if k < 6 else 2.0) for k, t in enumerate(hours)},
          "y": {t: 2.0 for t in hours}, "z": {t: 2.0 for t in hours}}
    scan = N.neighbour_scan(hs, {"x": ["y", "z"], "y": ["x", "z"], "z": ["x", "y"]})
    assert scan[0] == {"hours": 24, "under_0_4": 6, "share_under_0_4": 0.25, "median_ratio": 1.0,
                       "spot": "x", "neighbours": 2, "flagged": True}
    assert [e["flagged"] for e in scan[1:]] == [False, False]
    # 23 hours is under MIN_HOURS: not flagged however low
    short = {k: {t: v for t, v in d.items() if t != hours[-1]} for k, d in hs.items()}
    assert N.neighbour_scan(short, {"x": ["y", "z"]})[0]["flagged"] is False
    # one neighbour is not enough; a neighbour median under 1 m is not counted
    assert N.neighbour_scan(hs, {"x": ["y"]})[0]["hours"] == 0
    calm = dict(hs, y={t: 0.9 for t in hours}, z={t: 0.9 for t in hours})
    assert N.neighbour_scan(calm, {"x": ["y", "z"]})[0]["hours"] == 0


def _cycle():
    """A 5 x 5 grid, rows south to north at 35.50, 35.52, ... 35.58, columns west to east at
    -75.50, -75.48, ... -75.42. Column -75.50 is land (NaN); swh is 0.2 m in column -75.48 and
    3.0 m east of it, over two steps (f000 and f001, the second 0.4 m higher)."""
    lat = np.array([35.50, 35.52, 35.54, 35.56, 35.58])
    lng = np.array([-75.50, -75.48, -75.46, -75.44, -75.42])
    lats, lons = np.meshgrid(lat, lng, indexing="ij")
    swh0 = np.full((5, 5), 3.0, dtype="float32")
    swh0[:, 1] = 0.2
    swh0[:, 0] = np.nan
    fields = {("swh", 0): swh0, ("swh", 1): swh0 + 0.4,
              ("perpw", 0): np.full((5, 5), 10.0, "float32"),
              ("perpw", 1): np.full((5, 5), 12.0, "float32")}
    return {"lats": lats, "lons": lons, "mask": np.isnan(swh0), "steps": [0, 1], "fields": fields}


def test_the_sample_cell_is_the_nearest_row_and_column():
    # 35.539 is nearest row 35.54 (index 2); -75.4749 nearest column -75.48 (index 1)
    assert N.sample_cell(_cycle(), 35.539, -75.4749) == (2, 1)


def test_the_window_maps_wet_cells_and_nwps_s_own_height_and_period():
    w = N.window(_cycle(), 35.539, -75.4749, w=1, extra={"dslm": np.full((5, 5), 7.5)})
    assert (w["cell"], w["cell_lat"], w["cell_lng"], w["cell_wet"], w["di_is_north"]) == (
        [2, 1], 35.54, -75.48, True, True)
    cells = {(c["di"], c["dj"]): c for c in w["cells"]}
    assert len(cells) == 9
    # west of the cell is land; the cell reads 0.2 then 0.6 m (mean 0.4); east 3.0 then 3.4
    assert cells[(0, -1)]["wet"] is False and cells[(0, -1)]["swh"] is None
    assert cells[(0, 0)]["swh"] == {"mean": 0.4, "max": 0.6, "n": 2}
    assert cells[(0, 1)]["swh"] == {"mean": 3.2, "max": 3.4, "n": 2}
    assert cells[(1, 1)]["perpw"] == {"mean": 11.0, "max": 12.0, "n": 2}
    assert cells[(1, 1)]["dirpw"] is None and cells[(1, 1)]["dslm"] == 7.5
    assert (cells[(1, 0)]["lat"], cells[(1, 0)]["lng"]) == (35.56, -75.48)


def test_the_window_stops_at_the_grid_edge():
    assert len(N.window(_cycle(), 35.50, -75.50, w=1)["cells"]) == 4      # a corner: 2 x 2


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_):
        return self

    def order(self, *_):
        return self

    def eq(self, k, v):
        return _Q([r for r in self.rows if r.get(k) == v])

    def gte(self, k, v):
        return _Q([r for r in self.rows if r.get(k) >= v])

    def range(self, a, b):
        return _Q(self.rows[a:b + 1])

    def execute(self):
        return type("R", (), {"data": self.rows})()


class _Client:
    def __init__(self, tables):
        self.t = tables

    def table(self, name):
        return _Q(self.t[name])


def test_run_end_to_end_on_a_fake_database_and_grid():
    """Rodanthe 0.3 m against the pier's 3.0 m on 30 hours (0.1: all under 0.4); Salvo and
    S-Turns have no rows. The grid: two cycles of _cycle() read for the four spots at Rodanthe's
    node; the inventory adds a dslm field of 7.5 m."""
    roster = [{"name": n, "lat": 35.5, "lng": -75.46, "nwps_wfo": "mhx",
               "nwps_node_lat": 35.539, "nwps_node_lng": -75.4749}
              for n in ("Rodanthe", "Salvo", "S-Turns", "Outer Banks Fishing Pier")]
    spots = [{"id": i, "name": s["name"]} for i, s in enumerate(roster, 1)]
    hours = [f"2026-09-{d:02d}T{h:02d}:00:00+00:00" for d in (23, 24) for h in range(15)]
    fc = ([{"spot_id": 1, "valid_time": t, "hs": 0.3, "swell_source": "nwps", "source": "nwps"}
           for t in hours]
          + [{"spot_id": 4, "valid_time": t, "hs": 3.0, "swell_source": "nwps", "source": "nwps"}
             for t in hours])
    loaded = []
    rep = N.run(_Client({"spots": spots, "forecasts": fc}), roster, list(N.HATTERAS),
                load=lambda wfo, cyc: loaded.append((wfo, cyc[:2])) or _cycle(),
                cycles_for=lambda wfo, n: [("20261029", "06", "u1"), ("20261029", "00", "u2")],
                grib_inventory=lambda path: ([{"short": "dslm", "name": "depth", "units": "m",
                                               "dims": ["latitude", "longitude"]}],
                                             {"dslm": np.full((5, 5), 7.5)}))
    p = rep["pairs"]["rodanthe / outer-banks-fishing-pier"]
    assert p["all"] == {"hours": 30, "under_0_4": 30, "share_under_0_4": 1.0, "median_ratio": 0.1}
    assert rep["pairs"]["salvo / outer-banks-fishing-pier"]["all"]["hours"] == 0
    assert rep["hours_read"] == {"rodanthe": 30, "salvo": 0, "s-turns": 0,
                                 "outer-banks-fishing-pier": 30}
    # four spots on one grid, two cycles each read once (the loader caches per cycle)
    assert loaded == [("mhx", ("20261029", "06")), ("mhx", ("20261029", "00"))]
    g = rep["grid"]["rodanthe"]
    assert list(g["cycles"]) == ["mhx 20261029 06Z", "mhx 20261029 00Z"]
    w = g["cycles"]["mhx 20261029 06Z"]
    assert w["cell"] == [2, 1] and len(w["cells"]) == 25
    assert {c["dslm"] for c in w["cells"]} == {7.5}
    assert rep["inventory"]["mhx"][0]["short"] == "dslm" and rep["errors"] == {}
    rows = N.cell_rows(rep)
    assert len(rows) == 4 * 2 * 25 and rows[0]["spot"] == "rodanthe"


def test_run_reads_the_grid_around_a_flagged_spot_nobody_asked_for():
    """Three spots on one grid with the same sample point: x reads 0.5 m against y and z at
    2.0 m on all 24 hours (0.25, every hour under 0.4): flagged, and its cells are read with
    no spot asked for; y and z are not."""
    roster = [{"name": n, "lat": 35.5, "lng": -75.46, "nwps_wfo": "mhx",
               "nwps_node_lat": 35.539, "nwps_node_lng": -75.4749} for n in ("X", "Y", "Z")]
    spots = [{"id": i, "name": s["name"]} for i, s in enumerate(roster, 1)]
    hours = [f"2026-09-10T{h:02d}:00:00+00:00" for h in range(24)]
    fc = [{"spot_id": i, "valid_time": t, "hs": (0.5 if i == 1 else 2.0), "swell_source": "nwps",
           "source": "nwps"} for i in (1, 2, 3) for t in hours]
    rep = N.run(_Client({"spots": spots, "forecasts": fc}), roster, [],
                load=lambda wfo, cyc: _cycle(), cycles_for=lambda wfo, n: [("20261029", "06", "u")],
                grib_inventory=lambda path: ([], {}))
    assert rep["flagged"] == ["x"] and list(rep["grid"]) == ["x"]
