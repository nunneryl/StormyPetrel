"""A spot whose nearest HRRR cell is land reads the nearest WATER cell on its seaward side
(forecast/hrrr.py: _seaward_water_cell, extract_steps with a land mask).

The grid: 3 rows x 4 columns, 0.01 deg apart, x-axis due east, cell (1, 1) on the spot at
(0, 0). The fourth column is pushed 0.005 deg north, so the x-axis read at cell (1, 2) (its
neighbours (1, 1) and (1, 3): 0.02 east, 0.005 north) is atan(0.005 / 0.02) = atan(0.25) =
14.036 deg, and at (1, 1) it is 0.

Every expected value is worked by hand in the comment beside it; none is read back from the
function under test.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from pipeline.forecast import hrrr as H

LAND, WATER = 1.0, 0.0


def _grid():
    lats, lngs = np.zeros((3, 4)), np.zeros((3, 4))
    for i in range(3):
        for j in range(4):
            lngs[i, j] = (j - 1) * 0.01
            lats[i, j] = (i - 1) * 0.01 + (0.005 if j == 3 else 0.0)
    return lats, lngs


def _mask(water=((1, 0), (1, 2))):
    """Land everywhere but *water*: by default (1, 0), 0.01 deg WEST of the spot, and (1, 2),
    0.01 deg EAST."""
    m = np.full((3, 4), LAND)
    for ij in water:
        m[ij] = WATER
    return m


def _tree():
    from scipy.spatial import cKDTree
    lats, lngs = _grid()
    return cKDTree(np.column_stack([lats.ravel(), lngs.ravel()])), lats.ravel(), lngs.ravel()


@pytest.mark.parametrize("orientation, want", [
    (90.0, 6),     # faces east: (1, 2), flat 1 x 4 + 2 = 6, bearing 90, 0 off
    (270.0, 4),    # faces west: (1, 0), flat 4, bearing 270
    (0.0, 4),      # faces north: both water cells are 90 off, inside the half-angle; equally
                   # near (1.112 km each), the first in the tree's order wins: (1, 0), flat 4
    (None, 4),     # no orientation: every water cell qualifies, the same tie, flat 4
])
def test_the_seaward_water_cell(orientation, want):
    tree, flat_lat, flat_lng = _tree()
    got = H._seaward_water_cell(tree, _mask().ravel(), flat_lat, flat_lng, 0.0, 0.0, orientation)
    assert got == want


def test_the_nearest_of_two_seaward_cells_wins():
    # Water at (1, 2), 0.01 deg east (1.112 km), and (1, 3), 0.02 east and 0.005 north
    # (2.292 km): facing east, the nearer, flat 6.
    tree, flat_lat, flat_lng = _tree()
    assert H._seaward_water_cell(tree, _mask(water=((1, 2), (1, 3))).ravel(), flat_lat,
                                 flat_lng, 0.0, 0.0, 90.0) == 6


def test_the_land_mask_download(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(H, "HRRR_CACHE_DIR", tmp_path)
    monkeypatch.setattr(H, "_download_step",
                        lambda d, hh, fh, dest, params=None, min_bytes=50_000:
                        seen.append((fh, dest.name, params, min_bytes)) or True)
    assert H._download_land("20261022", "06", use_cache=True) == tmp_path / "20261022_06_land.grib2"
    (fh, name, params, min_bytes), = seen
    assert (fh, name, min_bytes) == (0, "20261022_06_land.grib2", 1_000)
    assert (params["var_LAND"], params["lev_surface"]) == ("on", "on")
    # Cached: no second download.
    (tmp_path / "20261022_06_land.grib2").write_bytes(b"x")
    assert H._download_land("20261022", "06", use_cache=True) is not None and len(seen) == 1


def test_a_back_bay_cell_does_not_count():
    # Only (1, 0) is water, due west; a spot facing east (90) looks 180 off it: none.
    tree, flat_lat, flat_lng = _tree()
    assert H._seaward_water_cell(tree, _mask(water=((1, 0),)).ravel(), flat_lat, flat_lng,
                                 0.0, 0.0, 90.0) is None


def test_water_beyond_the_search_radius_does_not_count(monkeypatch):
    # The nearest water is 0.01 deg away; with a 0.005 deg search there is none.
    monkeypatch.setattr(H, "HRRR_WATER_SEARCH_DEG", 0.005)
    tree, flat_lat, flat_lng = _tree()
    assert H._seaward_water_cell(tree, _mask().ravel(), flat_lat, flat_lng, 0.0, 0.0,
                                 90.0) is None


def _dataset(u, v):
    import xarray as xr
    lats, lngs = _grid()
    return xr.Dataset({"u10": (("y", "x"), u), "v10": (("y", "x"), v)},
                      coords={"latitude": (("y", "x"), lats), "longitude": (("y", "x"), lngs),
                              "valid_time": np.datetime64("2026-10-22T06:00:00")})


@pytest.fixture
def run(monkeypatch):
    """extract_steps on one step: grid wind (0, -1) everywhere but (0, -5) at (1, 2)."""
    def _run(land_mask, orientation=90.0):
        u, v = np.zeros((3, 4)), np.full((3, 4), -1.0)
        v[1, 2] = -5.0
        monkeypatch.setattr(H, "_open_grib_dataset", lambda p: [_dataset(u, v)])
        monkeypatch.setattr(H, "_uv_relative_to_grid", lambda p: True)
        spot = {"name": "Spot", "lat": 0.0, "lng": 0.0, "orientation_deg": orientation}
        out, failed, meta = H.extract_steps([spot], [(0, Path("step.grib2"))], land_mask)
        assert failed == 0
        (rec,) = out["Spot"]
        return rec, meta
    return _run


def test_a_land_cell_spot_reads_the_water_cell_turned_at_the_water_cell(run):
    # Water cell (1, 2): grid wind (0, -5), x-axis 14.036 deg (sin 0.242536, cos 0.970143).
    # ue = 0 - (-5)(0.242536) = 1.212678;  ve = (-5)(0.970143) = -4.850713
    # from atan2(-1.212678, 4.850713) = -14.036 = 345.964, at 5 m/s.
    rec, meta = run(_mask())
    assert (rec["wind_speed"], rec["wind_dir"]) == (5.0, 345.964)
    assert meta["cells"] == {"Spot": 6} and meta["water"] == {"Spot": [5, 6]}


def test_a_water_cell_spot_keeps_its_cell(run):
    # (1, 1) water: the spot's own cell, x-axis 0, grid wind (0, -1): from 0 at 1 m/s.
    rec, meta = run(_mask(water=((1, 1), (1, 2))))
    assert (rec["wind_speed"], rec["wind_dir"]) == (1.0, 0.0)
    assert meta["cells"] == {"Spot": 5} and meta["water"] == {}


def test_a_land_cell_with_no_seaward_water_keeps_its_cell(run):
    rec, meta = run(_mask(water=((1, 0),)))
    assert (rec["wind_speed"], rec["wind_dir"]) == (1.0, 0.0)
    assert meta["water"] == {}


def test_no_land_mask_and_a_mismatched_one_keep_the_nearest_cell(run):
    for land_mask in (None, np.full((2, 2), LAND)):
        rec, meta = run(land_mask)
        assert (rec["wind_speed"], rec["wind_dir"]) == (1.0, 0.0)
        assert meta["cells"] == {"Spot": 5}


def test_the_land_mask_is_read_from_a_grib(tmp_path):
    import eccodes
    gid = eccodes.codes_grib_new_from_samples("GRIB2")
    for k, val in (("discipline", 2), ("parameterCategory", 0), ("parameterNumber", 0),
                   ("typeOfFirstFixedSurface", 1)):
        eccodes.codes_set(gid, k, val)
    ni, nj = eccodes.codes_get(gid, "Ni"), eccodes.codes_get(gid, "Nj")
    eccodes.codes_set_values(gid, np.ones(ni * nj))
    path = tmp_path / "land.grib2"
    with open(path, "wb") as f:
        eccodes.codes_write(gid, f)
    eccodes.codes_release(gid)
    mask = H._land_mask(path)
    assert mask.shape == (nj, ni) and float(mask.min()) == float(mask.max()) == 1.0
    # A GRIB with no land field: None.
    gid = eccodes.codes_grib_new_from_samples("GRIB2")
    other = tmp_path / "other.grib2"
    with open(other, "wb") as f:
        eccodes.codes_write(gid, f)
    eccodes.codes_release(gid)
    assert H._land_mask(other) is None


def test_the_land_mask_is_fetched_with_the_wind_subregion():
    # The NOMADS filter for the land mask: f00, LAND at the surface, the same bbox as the wind.
    p = H._filter_params("20261022", "06", 0, (H.HRRR_LAND_VAR,), H.HRRR_LAND_LEVEL)
    assert p["file"] == "hrrr.t06z.wrfsfcf00.grib2"
    assert (p["var_LAND"], p["lev_surface"]) == ("on", "on")
    assert "var_UGRD" not in p and "lev_10_m_above_ground" not in p
    assert (p["leftlon"], p["rightlon"], p["toplat"], p["bottomlat"]) == \
        ("-130.00", "-65.00", "50.00", "22.00")
