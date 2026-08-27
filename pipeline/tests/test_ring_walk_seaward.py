"""The ring walk filters by DIRECTION during selection, not after it.

THE DEFECT. _find_offshore_point walked outward in expanding rings and accepted the
FIRST wet cell it touched, with no directional constraint at all. _seaward_diag then
measured which way it had gone and wrote the answer into a log line — after the cell was
already committed. On a barrier-island or point coast the first cell examined at radius 1
is a landward diagonal, so the walk sampled the water BEHIND the break.

Measured on the 2026-08-24 sgx / mtr / lox CG1 cycles, over all 47 ring-walk spots on
those grids: applying select_node's ±90° half-plane inside the walk moves exactly two
spots, and both are the two already known to be sampling landward.

    Mole Point   3.80 km at 117° off normal  ->  1.86 km at 82° off  (nearer AND correct)
    Mavericks    1.27 km at 100° off normal  ->  3.93 km at 40° off  (costs 2.66 km to
                                                 stop sampling behind the point)

sgx 0 of 31 move, mtr 2 of 10, lox 0 of 6. Zero spots fail to find a seaward cell inside
max_radius, so the fallback below is unexercised in production — which is exactly why it
has to be loud if it ever fires.

THE RULE IS select_node's, NOT A SECOND ONE. Filter to the half-plane FIRST, then take
the nearest survivor, using the same imported _ang_within / _bearing. _ang_within compares
with `<=`, so exactly 90.0° off normal counts as INSIDE; test_a_cell_exactly_ninety_
degrees_off_normal_counts_as_seaward pins that boundary.

THE FIXTURE is a 5x5 lat/lon grid with the spot at its centre, so "east", "west", "due
north" and "due south" of the spot are single index steps and every bearing is either 90°,
270°, 0° or 180° by construction. Every expected value is written literally beside its
assertion; nothing is obtained by calling the function under test.

Run: python -m pipeline.tests.test_ring_walk_seaward   (or pytest)
"""
from __future__ import annotations

import numpy as np

from pipeline.forecast import nwps

NAN = float("nan")

# Ascending south->north and west->east, the cfgrib convention the reader assumes.
LATS = [36.20, 36.25, 36.30, 36.35, 36.40]
LNGS = [-76.00, -75.95, -75.90, -75.85, -75.80]

SPOT_LAT, SPOT_LNG = 36.30, -75.90       # index (2,2), dead centre

# Bearings from the spot, by construction of the grid:
#   east  (i=2, j>2) -> ~90°     west  (i=2, j<2) -> ~270°
#   north (i>2, j=2) ->   0.0°   south (i<2, j=2) -> 180.0°   (dlng = 0 exactly)
EAST_1 = (36.30, -75.85)                 # index (2,3), ring 1
EAST_2 = (36.30, -75.80)                 # index (2,4), ring 2
WEST_1 = (36.30, -75.95)                 # index (2,1), ring 1
WEST_2 = (36.30, -76.00)                 # index (2,0), ring 2
NORTH_2 = (36.40, -75.90)                # index (4,2), ring 2
SOUTH_2 = (36.20, -75.90)                # index (0,2), ring 2

EAST_FACING = 90.0
SOUTH_FACING = 180.0


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
    def __init__(self, grid):
        self._grid = grid
        self.data_vars = ["swh"]
        self.sizes = {"latitude": len(LATS), "longitude": len(LNGS)}
        self.coords = ["latitude", "longitude"]

    def __getitem__(self, key):
        if key == "latitude":
            return _FakeCoord(LATS)
        if key == "longitude":
            return _FakeCoord(LNGS)
        if key == "swh":
            return _FakeVar(self._grid)
        raise KeyError(key)


def _grid(*wet_indices):
    """All land except the named (i, j) cells, which carry 1.0 m."""
    g = [[NAN] * 5 for _ in range(5)]
    for i, j in wet_indices:
        g[i][j] = 1.0
    return [_FakeDataset(g)]


def _walk(datasets, orientation=None):
    return nwps._find_offshore_point(datasets, SPOT_LAT, SPOT_LNG, orientation=orientation)


def _at(got):
    """(lat, lng) of a result, rounded to the grid's own precision."""
    return (round(got[0], 2), round(got[1], 2))


# --------------------------------------------------------------------------- #
# 1 — direction decides, at equal range and at unequal range                   #
# --------------------------------------------------------------------------- #
def test_at_the_same_ring_the_seaward_cell_is_chosen_over_the_landward_one():
    """Two wet cells, both one step from the spot: (2,1) due WEST at bearing ~270° and
    (2,3) due EAST at bearing ~90°. With an east-facing shore normal the west cell is
    180° off and the east cell 0° off.

    Expected: (36.30, -75.85), ring 1, seaward_ok True — the EAST cell.

    Under the old first-wet-in-ring-order walk the radius-1 visit order reaches (0,-1)
    = the west cell before (0,+1) = the east cell, so the old code returned the west one.
    """
    got = _walk(_grid((2, 1), (2, 3)), orientation=EAST_FACING)
    assert got is not None
    assert _at(got) == EAST_1, got
    assert got[2] == 1, got
    assert got[3] is True, got


def test_a_further_seaward_cell_beats_a_nearer_landward_one():
    """DIRECTION BEATS DISTANCE — the property that costs Mavericks 2.66 km and is worth
    it. (2,1) is WEST at ring 1; (2,4) is EAST at ring 2, twice as far. The filter runs
    FIRST, so the ring-1 cell is discarded before distance is ever compared.

    Expected: (36.30, -75.80), ring 2, seaward_ok True.
    """
    got = _walk(_grid((2, 1), (2, 4)), orientation=EAST_FACING)
    assert got is not None
    assert _at(got) == EAST_2, got
    assert got[2] == 2, got
    assert got[3] is True, got


def test_the_nearest_of_several_seaward_cells_is_chosen():
    """Filter first, THEN nearest — not first-found among the survivors. Three seaward
    cells at rings 1 and 2; the ring-1 one must win.

    Expected: (36.30, -75.85), ring 1.
    """
    got = _walk(_grid((2, 3), (2, 4), (4, 2)), orientation=EAST_FACING)
    assert got is not None
    assert _at(got) == EAST_1, got
    assert got[2] == 1, got


# --------------------------------------------------------------------------- #
# 2 — the ±90° boundary is inclusive, inherited from _ang_within               #
# --------------------------------------------------------------------------- #
def test_a_cell_exactly_ninety_degrees_off_normal_counts_as_seaward():
    """(4,2) is due NORTH of the spot: same longitude, so dlng = 0, y = 0 and x > 0,
    giving a great-circle bearing of exactly 0.0°. Against an east-facing normal that is
    |0 - 90| = 90.0° off — the exact boundary. _ang_within compares `<= half`, so it is
    INSIDE, and this is the only wet cell on the grid.

    Expected: (36.40, -75.90), ring 2, seaward_ok True. A strict `<` would report
    seaward_ok False here and fall back instead.
    """
    got = _walk(_grid((4, 2)), orientation=EAST_FACING)
    assert got is not None
    assert _at(got) == NORTH_2, got
    assert got[3] is True, got


def test_the_boundary_holds_on_the_other_side_too():
    """(0,2) is due SOUTH: bearing exactly 180.0°, |180 - 90| = 90.0° off. Also inside.

    Expected: (36.20, -75.90), ring 2, seaward_ok True.
    """
    got = _walk(_grid((0, 2)), orientation=EAST_FACING)
    assert got is not None
    assert _at(got) == SOUTH_2, got
    assert got[3] is True, got


# --------------------------------------------------------------------------- #
# 3 — no seaward cell: fall back, never refuse                                 #
# --------------------------------------------------------------------------- #
def test_only_landward_cells_falls_back_to_nearest_wet_and_flags_it():
    """THE FALLBACK. Both wet cells are WEST — 180° off an east-facing normal — so the
    half-plane admits nothing. The walk must publish the nearest wet cell anyway rather
    than returning None: a skipped spot produces no forecast rows at all and blanks its
    page.

    wet: (2,1) ring 1 and (2,0) ring 2, both due west. Nearest is (2,1).
    Expected: (36.30, -75.95), ring 1, seaward_ok FALSE.

    seaward_ok False is what the caller counts and names; the fetch-level counter and the
    named run-summary warning are pinned in test_nwps_baked_node's
    test_a_landward_walk_only_happens_when_no_seaward_cell_exists_and_is_named_twice.
    """
    got = _walk(_grid((2, 1), (2, 0)), orientation=EAST_FACING)
    assert got is not None, "must NOT return None — that would skip the spot entirely"
    assert _at(got) == WEST_1, got
    assert got[2] == 1, got
    assert got[3] is False, got


def test_no_wet_cell_at_all_still_returns_none():
    """The fallback rescues a spot from the DIRECTION filter, not from an empty grid.
    With no wet cell anywhere there is nothing to publish, and None remains the answer —
    the pre-existing behaviour the fetcher turns into its 'skipping' warning.

    Expected: None.
    """
    assert _walk(_grid(), orientation=EAST_FACING) is None
    assert _walk(_grid()) is None


# --------------------------------------------------------------------------- #
# 4 — radius 0                                                                 #
# --------------------------------------------------------------------------- #
def test_a_wet_and_seaward_nominal_cell_is_returned_at_radius_zero():
    """The spot's own cell (2,2) is wet. Its bearing from the spot is 0.0° (atan2(0,0)),
    which against an east-facing normal is 90.0° off — inside, by the same `<=` boundary
    as above — and its distance is 0, so it is the nearest survivor.

    Expected: (36.30, -75.90), ring 0, seaward_ok True. Unchanged from before the filter.
    """
    got = _walk(_grid((2, 2), (2, 3)), orientation=EAST_FACING)
    assert got is not None
    assert _at(got) == (SPOT_LAT, SPOT_LNG), got
    assert got[2] == 0, got
    assert got[3] is True, got


def test_a_wet_but_landward_nominal_cell_is_not_returned_when_a_seaward_cell_exists():
    """THE CASE THE OLD CODE COULD NOT EXPRESS. The spot's own cell is wet, so the old
    walk returned it immediately at radius 0 without ever considering direction.

    Here the normal faces SOUTH (180°), so the nominal cell's 0.0° bearing is 180.0° off
    — landward — while (0,2) due south is 180.0° bearing, 0.0° off — seaward.

    Expected: (36.20, -75.90), ring 2, seaward_ok True. NOT (36.30, -75.90) at ring 0.
    """
    got = _walk(_grid((2, 2), (0, 2)), orientation=SOUTH_FACING)
    assert got is not None
    assert _at(got) == SOUTH_2, got
    assert _at(got) != (SPOT_LAT, SPOT_LNG), "returned the landward nominal cell"
    assert got[2] == 2, got
    assert got[3] is True, got


# --------------------------------------------------------------------------- #
# 5 — no orientation: the original walk, untouched                             #
# --------------------------------------------------------------------------- #
def test_without_an_orientation_the_original_ring_walk_runs_unchanged():
    """No shore normal means no half-plane and no filtering — first wet cell in the
    original radius-then-index order, exactly as before this parameter existed.

    wet: (2,1) WEST ring 1 and (2,3) EAST ring 1. The radius-1 visit order is
    (-1,-1) (-1,0) (-1,+1) (0,-1) (0,+1) (+1,-1) (+1,0) (+1,+1), so (0,-1) = (2,1), the
    WEST cell, is reached first.

    Expected: (36.30, -75.95), ring 1, seaward_ok True — landward, and deliberately so.
    With an east-facing normal the very same grid yields the EAST cell, which is what
    test_at_the_same_ring_the_seaward_cell_is_chosen_over_the_landward_one asserts.
    """
    got = _walk(_grid((2, 1), (2, 3)))
    assert got is not None
    assert _at(got) == WEST_1, got
    assert got[2] == 1, got
    # no constraint was applied, so none can have failed
    assert got[3] is True, got


def test_without_an_orientation_a_wet_nominal_cell_short_circuits_at_radius_zero():
    """The unoriented path keeps its radius-0 short circuit: the nominal cell is checked
    before any ring is walked.

    Expected: (36.30, -75.90), ring 0.
    """
    got = _walk(_grid((2, 2), (2, 3)))
    assert got is not None
    assert _at(got) == (SPOT_LAT, SPOT_LNG), got
    assert got[2] == 0, got


# --------------------------------------------------------------------------- #
# 6 — the rule is select_node's, by identity not by imitation                  #
# --------------------------------------------------------------------------- #
def test_the_half_plane_test_is_the_one_select_node_uses():
    """Not a second copy. Both route through nwps_nearshore._ang_within / _bearing, and
    the half-width is the same literal 90. Pinned by exercising the primitives directly
    on the fixture's own geometry, with values written out here:

        bearing spot -> due north  =   0.0  ->  |0 - 90|   =  90.0  -> inside (<=)
        bearing spot -> due south  = 180.0  ->  |180 - 90| =  90.0  -> inside (<=)
        89.9999 off  -> inside      90.0001 off -> outside
    """
    from pipeline.forecast.nwps_nearshore import _ang_within, _bearing

    assert _bearing(SPOT_LAT, SPOT_LNG, NORTH_2[0], NORTH_2[1]) == 0.0
    assert _bearing(SPOT_LAT, SPOT_LNG, SOUTH_2[0], SOUTH_2[1]) == 180.0
    assert _ang_within(0.0, EAST_FACING, 90) is True
    assert _ang_within(180.0, EAST_FACING, 90) is True
    assert _ang_within(90.0 - 89.9999, EAST_FACING, 90) is True
    assert _ang_within(EAST_FACING + 90.0001, EAST_FACING, 90) is False


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} ring-walk-seaward checks passed")


if __name__ == "__main__":
    _run_all()
