"""HRRR's 10 m wind is grid-relative; forecast/hrrr.py turns it to true north before taking the
direction. On a three-by-three grid whose x-axis is turned the way HRRR's is at our spots, a
wind blowing along the grid's -y axis (unrotated it reads "from 0 deg") must come out from the
true direction worked by hand below, at the same speed.

Every expected value is worked by hand in the comment beside it; none is read back from the
function under test.
"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

from pipeline.forecast import hrrr as H


def _grid(axis_deg, d=0.01):
    """3 rows x 4 columns of lat/lng with the x-axis (along j) at *axis_deg* from east and y at
    axis + 90, cell (1, 1) on (0, 0) so cos(lat) there is exactly 1. The fourth column is
    pushed 0.005 deg north, so the axis read at cell (1, 2) is NOT axis_deg: a spot on (1, 1)
    read as (1, 2) (the grid's rows and columns swapped) comes out wrong."""
    a = math.radians(axis_deg)
    lats, lngs = np.zeros((3, 4)), np.zeros((3, 4))
    for i in range(3):
        for j in range(4):
            x, y = (j - 1) * d, (i - 1) * d
            lngs[i, j] = x * math.cos(a) - y * math.sin(a)
            lats[i, j] = x * math.sin(a) + y * math.cos(a) + (0.005 if j == 3 else 0.0)
    return lats, lngs


def _dataset(lats, lngs, u, v):
    import xarray as xr
    return xr.Dataset(
        {"u10": (("y", "x"), np.full((3, 4), float(u))),
         "v10": (("y", "x"), np.full((3, 4), float(v)))},
        coords={"latitude": (("y", "x"), lats), "longitude": (("y", "x"), lngs),
                "valid_time": np.datetime64("2026-10-22T06:00:00")})


@pytest.fixture
def run(monkeypatch):
    """extract_steps over one step whose dataset is built here; returns (record, meta)."""
    def _run(axis_deg, u, v, grid_relative=True):
        lats, lngs = _grid(axis_deg)
        monkeypatch.setattr(H, "_open_grib_dataset", lambda p: [_dataset(lats, lngs, u, v)])
        monkeypatch.setattr(H, "_uv_relative_to_grid", lambda p: grid_relative)
        out, failed, meta = H.extract_steps([{"name": "Spot", "lat": 0.0, "lng": 0.0}],
                                            [(0, Path("step.grib2"))])
        assert failed == 0
        (rec,) = out["Spot"]
        assert rec["valid_time"] == "2026-10-22T06:00:00Z"
        return rec, meta
    return _run


def test_new_jersey_turns_14_3_degrees_clockwise(run):
    # Atlantic City's cell: x-axis 14.3 deg clockwise of east (-14.3). Grid wind u=0, v=-5.
    # sin 14.3 = 0.246999, cos 14.3 = 0.969016.
    # ue = u cos a - v sin a = 0 - (-5)(-0.246999) = -1.234995
    # ve = u sin a + v cos a = 0 + (-5)(0.969016) = -4.845080
    # from atan2(-ue, -ve) = atan2(1.234995, 4.845080) = 14.300 deg: published 0, true 14.3.
    rec, meta = run(-14.3, 0.0, -5.0)
    assert rec["wind_dir"] == 14.3
    assert rec["wind_speed"] == 5.0
    assert meta == {"grid_relative": True, "cells": {"Spot": 5}, "water": {}}   # (1, 1): 1 x 4 + 1


def test_california_turns_15_3_degrees_anticlockwise(run):
    # Steamer Lane's cell: x-axis 15.3 deg anticlockwise of east (+15.3). Same grid wind.
    # sin 15.3 = 0.263873, cos 15.3 = 0.964557.
    # ue = 0 - (-5)(0.263873) = 1.319365;  ve = (-5)(0.964557) = -4.822785
    # from atan2(-1.319365, 4.822785) = -15.300 deg = 344.7: published 0, true 344.7.
    rec, _ = run(15.3, 0.0, -5.0)
    assert rec["wind_dir"] == 344.7
    assert rec["wind_speed"] == 5.0


def test_a_turn_keeps_the_speed(run):
    # u=3, v=4: |(3, 4)| = 5 however the axes are turned.
    # Direction with a=-14.3: ue = 3(0.969016) - 4(-0.246999) = 2.907048 + 0.987996 = 3.895044
    #                         ve = 3(-0.246999) + 4(0.969016) = -0.740997 + 3.876064 = 3.135067
    # from atan2(-3.895044, -3.135067) = -128.83 deg = 231.17; unrotated it is
    # atan2(-3, -4) = -143.13 = 216.87, so the turn is again +14.3.
    rec, _ = run(-14.3, 3.0, 4.0)
    assert rec["wind_speed"] == 5.0
    assert round(rec["wind_dir"], 2) == 231.17


def test_an_earth_relative_file_is_not_turned(run):
    # uvRelativeToGrid = 0: u/v are already east/north, so (0, -5) is from 0 deg however the grid
    # is laid out.
    rec, meta = run(-14.3, 0.0, -5.0, grid_relative=False)
    assert rec["wind_dir"] == 0.0
    assert meta["grid_relative"] is False


def test_the_x_axis_is_read_off_the_grid():
    # Centre cell of a grid laid out at -14.3: the j-neighbours differ by 2d (cos -14.3,
    # sin -14.3) in (lng, lat), and cos(0) = 1, so atan2 gives back -14.3.
    lats, lngs = _grid(-14.3)
    assert round(H._grid_x_axis_deg(lats, lngs, 1, 1), 6) == -14.3
    # Rows of constant latitude at 40 N, longitude rising with j: due east, 0 deg.
    lats = np.full((1, 3), 40.0)
    lngs = np.array([[-74.2, -74.1, -74.0]])
    assert H._grid_x_axis_deg(lats, lngs, 0, 1) == 0.0
    # At 40 N a degree of longitude is cos 40 = 0.766044 of a degree of latitude. Neighbours
    # 0.1 deg apart in latitude and 0.1 / 0.766044 = 0.130541 deg apart in longitude are as far
    # east as north: 45 deg (without the cos 40 it would read atan(0.1 / 0.130541) = 37.45).
    lats = np.array([[39.95, 40.0, 40.05]])
    lngs = np.array([[-74.065270, -74.0, -73.934730]])
    assert round(H._grid_x_axis_deg(lats, lngs, 0, 1), 2) == 45.0


def _grib(path, flag):
    import eccodes
    gid = eccodes.codes_grib_new_from_samples("GRIB2")
    eccodes.codes_set(gid, "uvRelativeToGrid", flag)
    with open(path, "wb") as f:
        eccodes.codes_write(gid, f)
    eccodes.codes_release(gid)
    return path


def test_the_flag_is_read_from_the_file(tmp_path):
    assert H._uv_relative_to_grid(_grib(tmp_path / "grid.grib2", 1)) is True
    assert H._uv_relative_to_grid(_grib(tmp_path / "earth.grib2", 0)) is False


def test_an_unreadable_flag_is_taken_as_grid_relative(tmp_path):
    # Every HRRR wrfsfc file is grid-relative, so a file the flag cannot be read from is turned.
    p = tmp_path / "empty.grib2"
    p.write_bytes(b"")
    assert H._uv_relative_to_grid(p) is True
