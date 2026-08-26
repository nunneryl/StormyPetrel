"""The NWPS fetcher measures how far away the cell it sampled actually is.

THE GAP. _find_offshore_point has no distance cap, and nwps.py computed no metric
distance at all. The fallback log line reported a CELL COUNT ("fell back at 1 cells
away") and, later, a bearing — never a distance. The Wedge sampled a cell 305 km away
for two months behind a stale nwps_wfo and no log said so; it was found by hand, three
layers into an unrelated investigation.

Meanwhile the codebase already knew how to compute an appropriate cap:
nwps_nearshore.grid_far_cap_km = max(FAR_CAP_FLOOR_KM 3.0, FAR_CAP_MULT 1.5 x spacing),
enforced in the placement pass and nowhere in the fetcher.

THIS REPORTS, IT NEVER REFUSES, and test_an_over_cap_sample_is_published_not_withheld
is the pin that keeps it that way. A skipped spot produces NO forecast rows at all — it
is absent from nwps.json, compute_ratings never visits it, db_import writes nothing, and
the frontend has nothing to render. There is no fallback rater behind this fetcher. A
slightly-wrong forecast beats a blank page.

EVERY EXPECTED VALUE IS HAND-COMPUTED with the arithmetic in a comment, from the
haversine and the cap formula directly. None is derived by calling the function under
test.

THE TWO GRID FIXTURES ARE CHOSEN TO SEPARATE THE CAP'S BRANCHES: the fine grid floors
to 3.0, the coarse grid clears the floor and lands on 1.5 x spacing. A fixture where
both branches gave the same answer would pin nothing.

Run: python -m pipeline.tests.test_nwps_sampled_distance   (or pytest)
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

from pipeline.forecast import nwps


def _close(a, b, tol=1e-9):
    return abs(a - b) <= tol


NAN = float("nan")

# --------------------------------------------------------------------------- #
# Two grids, chosen so the cap formula's two branches give different answers    #
# --------------------------------------------------------------------------- #
# COARSE: 0.045 deg steps, median latitude 36.0. 5x5 so a radius-2 walk fits.
#   lat step = 0.045 deg along a meridian      = 5.003771699005 km
#   lng step = 0.045 deg along the 36.0 parallel, x cos(36) = 0.809016994375
#            = 4.048136304521 km
#   spacing  = mean(5.003771699005, 4.048136304521) = 4.525954001763 km
#   1.5 x spacing = 6.788931002645 > 3.0 floor -> cap = 6.788931002645 km
COARSE_LATS = [35.910, 35.955, 36.000, 36.045, 36.090]
COARSE_LNGS = [-75.940, -75.895, -75.850, -75.805, -75.760]
COARSE_SPACING = 4.525954001763015
COARSE_CAP = 6.788931002644523

# FINE: 0.018 deg steps, same median latitude.
#   lat step = 2.001508679602 km,  lng step = 1.619254533886 km
#   spacing  = 1.810381606744 km
#   1.5 x spacing = 2.715572410116 <= 3.0 -> cap FLOORS to 3.0 km
FINE_LATS = [35.964, 35.982, 36.000, 36.018, 36.036]
FINE_LNGS = [-75.886, -75.868, -75.850, -75.832, -75.814]

SPOT_LAT, SPOT_LNG = 36.000, -75.850        # coarse index (2,2), the grid centre

# The two cells the ring walk reaches, from the spot at the grid centre — the SOUTH-WEST
# diagonals. That matters: the longitude leg is measured at a LOWER latitude than the
# north-east diagonal, so cos(lat) is larger and the same degree offset spans further.
#
# The walk now filters to the ±90° half-plane around orientation_deg and takes the
# NEAREST survivor, so these tests give their spot a SOUTH-WEST normal (225°) and mask
# every cell but the target. A diagonal can never be the nearest seaward cell while its
# adjacent edge cells are also wet and seaward — there is no orientation that admits the
# SW corner while excluding both due-south and due-west — so isolating it is the only
# way to keep these hand-computed distances as the values under test.
#   radius 1 -> (35.955, -75.895) =  6.436962517655 km   (UNDER the 6.7889 cap)
#   radius 2 -> (35.910, -75.940) = 12.875375961950 km   (OVER  the 6.7889 cap)
# The mirror-image north-east diagonals are 6.435509639394 and 12.869564447747 — the
# same to 2 dp at one cell, but 12.87 against 12.88 at two, which is the difference
# between a passing and a failing assertion on the log text.
WALK1 = (35.955, -75.895)
WALK2 = (35.910, -75.940)

# A baked node due north of the spot: a pure 0.09 deg meridian arc, so cos(lat) drops
# out entirely.  0.09 * (pi/180) * 6371 = 10.007543398011 km, OVER the 6.7889 cap.
FAR_BAKED = (36.090, -75.850)


class _FakeSample:
    def __init__(self, value):
        seq = value if isinstance(value, (list, tuple)) else [value]
        self.values = np.asarray(seq, dtype="float64")


class _FakeVar:
    def __init__(self, grid):
        self._grid = grid

    def isel(self, latitude, longitude):
        return _FakeSample(self._grid[latitude][longitude])


class _FakeCoord:
    def __init__(self, arr):
        self.values = np.asarray(arr, dtype="float64")

    def min(self):
        return float(np.min(self.values))


class _FakeDataset:
    """Exposes only what the code under test touches: data_vars, ['latitude'/'longitude']
    (.values and .min()), [var].isel(latitude=, longitude=), plus sizes/coords for the
    diagnostic log line."""

    def __init__(self, lats, lngs, grid, var_name="swh"):
        self._var, self._lats, self._lngs, self._grid = var_name, lats, lngs, grid
        self.data_vars = [var_name]
        self.sizes = {"latitude": len(lats), "longitude": len(lngs)}
        self.coords = ["latitude", "longitude"]

    def __getitem__(self, key):
        if key == "latitude":
            return _FakeCoord(self._lats)
        if key == "longitude":
            return _FakeCoord(self._lngs)
        if key == self._var:
            return _FakeVar(self._grid)
        raise KeyError(key)


def _all_water(n=5, m=5, v=1.2):
    return [[v] * m for _ in range(n)]


def _coarse(grid=None):
    return [_FakeDataset(COARSE_LATS, COARSE_LNGS, grid if grid is not None else _all_water())]


def _spot(name="T", baked=None, orientation=90.0):
    s = {"name": name, "lat": SPOT_LAT, "lng": SPOT_LNG,
         "nwps_wfo": "mtr", "orientation_deg": orientation}
    if baked is not None:
        s["nwps_node_lat"], s["nwps_node_lng"] = baked
    return s


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def text(self):
        return "\n".join(r.getMessage() for r in self.records)

    def warnings(self):
        return "\n".join(r.getMessage() for r in self.records
                         if r.levelno >= logging.WARNING)


def _run_fetch(spots, tmp_name, datasets):
    """Drive nwps.fetch() with the network, the GRIB and the extractor stubbed out.

    Returns (result, [(lat, lng) handed to the extractor], captured log).
    Only collaborators are stubbed — the distance/cap wiring under test runs for real.
    """
    seen = []

    def _fake_extract(_ds, lat, lng):
        seen.append((round(lat, 6), round(lng, 6)))
        return [{"valid_time": "2026-08-24T12:00:00Z", "hs": 1.0, "swell_hs": 0.5}]

    saved = (nwps._locate_cycle, nwps._open_grib_datasets,
             nwps._extract_time_series_from_datasets, nwps.NWPS_FORECAST_FILE,
             nwps.NWPS_CACHE_DIR)
    cap = _Capture()
    nwps.log.addHandler(cap)
    prev = nwps.log.level
    nwps.log.setLevel(logging.INFO)
    tmp = Path("/tmp/claude-0/-home-user-StormyPetrel/"
               "c0d14651-f350-5987-84e1-cf8ad55860d6/scratchpad")
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        nwps._locate_cycle = lambda wfo, use_cache: (Path("/dev/null"), "20260824", "12")
        nwps._open_grib_datasets = lambda p: datasets
        nwps._extract_time_series_from_datasets = _fake_extract
        nwps.NWPS_FORECAST_FILE = tmp / tmp_name
        nwps.NWPS_CACHE_DIR = tmp
        result = nwps.fetch(spots, use_cache=True)
    finally:
        (nwps._locate_cycle, nwps._open_grib_datasets,
         nwps._extract_time_series_from_datasets, nwps.NWPS_FORECAST_FILE,
         nwps.NWPS_CACHE_DIR) = saved
        nwps.log.removeHandler(cap)
        nwps.log.setLevel(prev)
    return result, seen, cap


# --------------------------------------------------------------------------- #
# 1 — the distance itself                                                      #
# --------------------------------------------------------------------------- #
def test_the_sampled_distance_is_a_hand_computed_haversine():
    """A PURE MERIDIAN separation, so the cos(latitude) factor drops out and the answer
    can be checked two independent ways.

        spot (36.00, -75.85) -> cell (36.09, -75.85): dlat 0.09 deg, dlng 0
        haversine: x   = sin^2(0.045 deg) = sin^2(7.853981633974e-4 rad)
                   d   = 2 * 6371 * asin(sqrt(x)) = 10.007543398010665 km
        arc-length cross-check, valid only because dlng is 0:
                   d   = 0.09 * (pi/180) * 6371 = 10.007543398010286 km
        The two agree to 4e-13 km, which is float noise, not a second formula.
    """
    got = nwps._sampled_distance_km(SPOT_LAT, SPOT_LNG, *FAR_BAKED)
    assert _close(got, 10.007543398010665, tol=1e-12), got
    assert _close(got, 0.09 * math.pi / 180.0 * 6371.0, tol=1e-9), got


def test_the_sampled_distance_carries_the_cosine_factor_on_a_parallel():
    """The east-west case, where cos(latitude) must appear or every longitude offset is
    overstated.

        spot (36.00, -75.85) -> cell (36.00, -75.805): dlng 0.045 deg, dlat 0
        along the 36.0 parallel: 0.045 * (pi/180) * 6371 * cos(36 deg)
                               = 5.003771699005 * 0.809016994375 = 4.048136304521 km
        Without the cosine it would read 5.003771699005 km, 23.6% too far.
    """
    got = nwps._sampled_distance_km(36.0, -75.850, 36.0, -75.805)
    assert _close(got, 4.048136304520698, tol=1e-9), got
    assert not _close(got, 5.003771699005332, tol=1e-3), "cos(latitude) is missing"


def test_a_zero_separation_is_zero():
    """Degenerate but real: a baked node exactly on the spot."""
    assert nwps._sampled_distance_km(36.0, -75.85, 36.0, -75.85) == 0.0


# --------------------------------------------------------------------------- #
# 2 — the cap, on both branches                                                #
# --------------------------------------------------------------------------- #
def test_a_fine_grid_floors_to_three_km():
    """FINE fixture, 0.018 deg steps at median latitude 36.0:

        lat step = 0.018 * (pi/180) * 6371                 = 2.001508679602 km
        lng step = 2.001508679602 * cos(36 deg)            = 1.619254533886 km
        spacing  = (2.001508679602 + 1.619254533886) / 2   = 1.810381606744 km
        1.5 * spacing = 2.715572410116, which is BELOW the 3.0 floor
        -> cap = max(3.0, 2.715572410116) = 3.0 exactly
    """
    sp, cap = nwps._grid_spacing_and_cap_km(
        [_FakeDataset(FINE_LATS, FINE_LNGS, _all_water())])
    assert _close(sp, 1.8103816067442797), sp
    assert cap == 3.0, cap
    assert not _close(cap, 1.5 * sp), "the floor did not apply"


def test_a_coarse_grid_clears_the_floor_and_uses_the_multiplier():
    """COARSE fixture, 0.045 deg steps at median latitude 36.0:

        lat step = 0.045 * (pi/180) * 6371                 = 5.003771699005 km
        lng step = 5.003771699005 * cos(36 deg)            = 4.048136304521 km
        spacing  = (5.003771699005 + 4.048136304521) / 2   = 4.525954001763 km
        1.5 * spacing = 6.788931002645, ABOVE the 3.0 floor
        -> cap = 6.788931002645 km
    """
    sp, cap = nwps._grid_spacing_and_cap_km(_coarse())
    assert _close(sp, COARSE_SPACING), sp
    assert _close(cap, COARSE_CAP), cap
    assert _close(cap, 1.5 * sp), "the multiplier branch did not apply"
    assert cap > 3.0


def test_the_cap_constants_come_from_the_placement_pass_not_from_literals():
    """3.0 and 1.5 are imported, not restated, so the fetcher's cap and the placement
    pass's cap are the same number by construction. Rebind them and the fetcher must
    follow.
        FAR_CAP_MULT 1.5 -> 3.0 on the coarse grid:
            3.0 * 4.525954001763 = 13.577862005289 km
    """
    from pipeline.forecast import nwps_nearshore as nn
    saved = nn.FAR_CAP_MULT
    try:
        nn.FAR_CAP_MULT = 3.0
        _sp, cap = nwps._grid_spacing_and_cap_km(_coarse())
        assert _close(cap, 13.577862005289046), cap
    finally:
        nn.FAR_CAP_MULT = saved
    _sp, cap = nwps._grid_spacing_and_cap_km(_coarse())
    assert _close(cap, COARSE_CAP), cap          # restored


def test_a_degenerate_grid_falls_back_to_the_floor():
    """A single-point axis yields no step on that axis; no step at all yields the floor.
    The sibling guards on `ndim != 2` because it is handed a meshgrid; these axes arrive
    1-D, so that guard does not transfer and the size check does the work instead."""
    one = [_FakeDataset([36.0], [-75.85], [[1.2]])]
    sp, cap = nwps._grid_spacing_and_cap_km(one)
    assert sp == 0.0, sp
    assert cap == 3.0, cap
    # a 1-D lng axis alone still yields the lng step, so spacing is that step
    #   0.045 deg along the 36.0 parallel = 4.048136304521 km; 1.5 x = 6.072204456781
    lng_only = [_FakeDataset([36.0], COARSE_LNGS, [[1.2] * 5])]
    sp, cap = nwps._grid_spacing_and_cap_km(lng_only)
    assert _close(sp, 4.048136304520698), sp
    assert _close(cap, 6.072204456781047), cap


def test_the_spacing_is_the_MEDIAN_adjacent_step_not_the_mean():
    """A ragged axis, which every uniform fixture above cannot distinguish. One outsized
    gap — a nest edge, a dropped row — must not drag the spacing up, which is why the
    sibling uses a median and this must too.

        lats [36.000, 36.045, 36.090, 36.135, 36.600]
        adjacent diffs: 0.045, 0.045, 0.045, 0.465
            MEDIAN 0.045   <- correct
            MEAN   0.150   <- would be 3.33x too coarse
        mid_lat = median(lats) = 36.09
        MEDIAN path: lat step 0.045 deg           = 5.003771699005 km
                     lng step 0.045 deg at 36.09  = 4.043511375168 km
                     spacing 4.523641537086 -> cap 6.785462305630 km
        MEAN path:   lat step 0.150 deg           = 16.679238996684 km
                     spacing 10.361375185926 -> cap 15.542062778888 km
    """
    ragged = [_FakeDataset([36.000, 36.045, 36.090, 36.135, 36.600],
                           COARSE_LNGS, _all_water())]
    sp, cap = nwps._grid_spacing_and_cap_km(ragged)
    assert _close(sp, 4.523641537086489), sp
    assert _close(cap, 6.785462305629733), cap
    assert not _close(sp, 10.361375185925649, tol=1e-3), "spacing used the MEAN step"
    assert not _close(cap, 15.542062778888473, tol=1e-3), "cap followed the MEAN step"


def test_a_sample_exactly_AT_the_cap_does_not_warn():
    """The boundary is strict: over the cap warns, exactly at it does not. No fixture
    above sits on the boundary, so a `>` silently becoming `>=` would go unnoticed.

    Constructed by pinning the FLOOR to the fixture's own distance on a FINE grid,
    where 1.5 x 1.810381606744 = 2.715572410116 is below the floor so the floor wins
    and the cap is exactly the number set:
        spot (36.000, -75.850) -> baked (36.045, -75.850)
        pure 0.045 deg meridian arc = 5.003771699005332 km
    The first assertion checks the two really are the same float, so if the haversine
    ever drifts this test fails loudly instead of passing for the wrong reason.
    """
    from pipeline.forecast import nwps_nearshore as nn
    node = (36.045, -75.850)
    boundary_km = 5.003771699005332

    assert nwps._sampled_distance_km(SPOT_LAT, SPOT_LNG, *node) == boundary_km, (
        "the fixture no longer sits exactly on the boundary")

    saved = nn.FAR_CAP_FLOOR_KM
    try:
        nn.FAR_CAP_FLOOR_KM = boundary_km
        fine = [_FakeDataset(FINE_LATS, FINE_LNGS, _all_water())]
        _sp, cap_km = nwps._grid_spacing_and_cap_km(fine)
        assert cap_km == boundary_km, cap_km        # the floor won, exactly
        _, seen, cap = _run_fetch([_spot(baked=node)], "boundary.json", fine)
    finally:
        nn.FAR_CAP_FLOOR_KM = saved

    assert seen == [node], seen
    assert "OVER" not in cap.warnings(), cap.warnings()
    assert "over-far-cap=0" in cap.text(), cap.text()


def test_no_wave_dataset_yields_the_floor():
    """Same posture the land test takes when it cannot tell water from land."""
    sp, cap = nwps._grid_spacing_and_cap_km([])
    assert sp == 0.0 and cap == 3.0, (sp, cap)


# --------------------------------------------------------------------------- #
# 3 — over-cap REPORTS and never refuses                                       #
# --------------------------------------------------------------------------- #
def test_an_over_cap_sample_is_published_not_withheld():
    """THE PIN THAT KEEPS THIS A REPORT. A baked node 10.0075 km from the spot on a grid
    whose cap is 6.7889 km must WARN, COUNT, and still hand those exact coordinates to
    the extractor — because a skipped spot produces no forecast rows at all.

        baked (36.09, -75.85) is water in the all-water fixture
        distance = 10.007543398011 km  >  cap 6.788931002645 km  -> over
        the extractor must still receive (36.09, -75.85), unchanged
    """
    spot = _spot(name="The Wedge", baked=FAR_BAKED)
    _, seen, cap = _run_fetch([spot], "over.json", _coarse())

    assert seen == [FAR_BAKED], seen              # published, and at the SAME cell
    w = cap.warnings()
    assert "The Wedge" in w and "OVER" in w, w
    assert "10.01 km" in w, w
    assert "6.79 km cap" in w, w
    assert "4.53 km" in w, w                      # the spacing it was derived from
    assert "over-far-cap=1" in cap.text(), cap.text()
    assert "sampled BEYOND their grid's far cap" in w, w
    assert "The Wedge" in w.split("sampled BEYOND")[1], w


def test_a_within_cap_sample_is_silent():
    """5.0038 km against a 6.7889 km cap. No warning, no count, and the spot is published
    exactly as before.
        baked (36.045, -75.85): pure 0.045 deg meridian arc = 5.003771699005 km
    """
    spot = _spot(baked=(36.045, -75.850))
    _, seen, cap = _run_fetch([spot], "under.json", _coarse())
    assert seen == [(36.045, -75.850)], seen
    assert "OVER" not in cap.warnings(), cap.warnings()
    assert "over-far-cap=0" in cap.text(), cap.text()
    assert "sampled BEYOND" not in cap.warnings()


def test_the_returned_coordinates_are_identical_over_cap_and_under_cap():
    """The check must be observationally inert on the value path: the same baked node
    yields the same extraction point whether it is over or under the cap. Driven by
    swapping the GRID (fine vs coarse) rather than the node, so the only thing that
    changes is the cap.

        node (36.045, -75.805) is 6.4355 km from the spot
          on the COARSE grid: cap 6.7889 -> UNDER, silent
          on the FINE   grid: cap 3.0000 -> OVER,  warns
        both must extract at (36.045, -75.805).
    """
    node = (36.045, -75.805)
    _, seen_c, cap_c = _run_fetch([_spot(baked=node)], "inert_c.json", _coarse())
    fine = [_FakeDataset(FINE_LATS, FINE_LNGS, _all_water())]
    _, seen_f, cap_f = _run_fetch([_spot(baked=node)], "inert_f.json", fine)

    assert seen_c == seen_f == [node], (seen_c, seen_f)
    assert "OVER" not in cap_c.warnings()
    assert "OVER" in cap_f.warnings(), cap_f.warnings()


# --------------------------------------------------------------------------- #
# 4 — BOTH paths are measured                                                  #
# --------------------------------------------------------------------------- #
def test_the_ring_walk_path_is_measured_too():
    """The walk, not the baked node. Only the SW radius-2 corner is wet, and the spot's
    normal faces SW, so that corner is the only seaward candidate.

        grid centre is index (2,2); the wet cell is (0,0) =
        (35.910, -75.940), dlat = dlng = -0.09 deg (SOUTH-west)
        distance = 12.875375961950 km  >  cap 6.788931002645  -> over, warns
    """
    grid = [[NAN] * 5 for _ in range(5)]  # everything land...
    grid[0][0] = 1.2                      # ...except the SW radius-2 corner
    spot = _spot(name="Walker", orientation=225.0)   # SW normal -> that corner is seaward
    _, seen, cap = _run_fetch([spot], "walk.json", _coarse(grid))

    assert seen == [WALK2], seen
    w = cap.warnings()
    assert "Walker" in w and "OVER" in w, w
    assert "12.88 km" in w, w
    assert "at 2 cells / 12.88 km away" in cap.text(), cap.text()


def test_a_within_cap_ring_walk_reports_its_distance_without_warning():
    """One ring out, 6.4355 km, under the 6.7889 cap. The fallback line must still carry
    the distance — that line is why this work exists — but nothing warns.

        only (1,1) is wet, at (-1,-1) from the centre =
        (35.955, -75.895), dlat = dlng = -0.045 deg -> 6.436962517655 km
    """
    grid = [[NAN] * 5 for _ in range(5)]  # everything land...
    grid[1][1] = 1.2                      # ...except the SW radius-1 diagonal
    _, seen, cap = _run_fetch([_spot(orientation=225.0)], "walk_ok.json", _coarse(grid))

    assert seen == [WALK1], seen
    assert "at 1 cells / 6.44 km away" in cap.text(), cap.text()
    assert "OVER" not in cap.warnings(), cap.warnings()


def test_the_zero_ring_nominal_cell_line_carries_the_distance():
    """The nominal-cell path walks no rings at all, so its line never had a cells-away
    figure to hang a distance on. It must report one now.

        the spot sits exactly on grid index (2,2), so distance is 0.00 km
    """
    grid = _all_water()
    _, seen, cap = _run_fetch([_spot()], "nominal.json", _coarse(grid))
    assert seen == [(SPOT_LAT, SPOT_LNG)], seen
    assert "nominal nearest cell" in cap.text(), cap.text()
    assert "0.00 km away" in cap.text(), cap.text()


def test_the_baked_node_line_carries_the_distance():
    """The baked path's INFO line must report the distance too — before this change it
    named only the coordinates.
        baked (36.045, -75.85) = 5.003771699005 km -> "5.00 km away"
    """
    _, _, cap = _run_fetch([_spot(baked=(36.045, -75.850))], "bakedline.json", _coarse())
    assert "using baked seaward node" in cap.text(), cap.text()
    assert "5.00 km away" in cap.text(), cap.text()


def test_the_per_wfo_cap_is_logged_once_with_its_spacing():
    """A run should say what cap it is judging against, and what spacing produced it —
    otherwise an over-cap warning cannot be checked by a reader.
        coarse: spacing 4.53 km -> cap 6.79 km
    """
    _, _, cap = _run_fetch([_spot()], "capline.json", _coarse())
    assert "grid spacing 4.53 km -> sampled-distance cap 6.79 km" in cap.text(), cap.text()


def test_both_paths_in_one_run_are_each_measured_and_counted():
    """One baked-node spot over cap and one ring-walk spot over cap in the same run:
    both must warn, both must be named, and the counter must read 2.

        Baked  : (36.09, -75.85) = 10.007543398011 km  -> over
        Walker : radius-2 (35.910, -75.940) = 12.875375961950 km -> over
    """
    grid = [[NAN] * 5 for _ in range(5)]
    grid[0][0] = 1.2                      # the walker's only seaward cell
    grid[4][2] = 1.2                      # keep the baked node itself wet. Due NORTH of
                                          # the spot, 135° off a SW normal, so the walker
                                          # cannot take it.
    baked = _spot(name="Baked", baked=FAR_BAKED, orientation=225.0)
    walker = _spot(name="Walker", orientation=225.0)
    _, seen, cap = _run_fetch([baked, walker], "both.json", _coarse(grid))

    assert seen == [FAR_BAKED, WALK2], seen
    assert "over-far-cap=2" in cap.text(), cap.text()
    w = cap.warnings()
    assert "Baked, Walker" in w, w        # named, sorted, in the summary


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} nwps-sampled-distance checks passed")


if __name__ == "__main__":
    _run_all()
