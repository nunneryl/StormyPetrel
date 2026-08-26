"""The NWPS fetcher samples the BAKED SEAWARD NODE, not a re-derived ring-walk point.

THE DEFECT. pipeline/forecast/nwps.py writes forecasts.hs and forecasts.swell_hs. For
each spot it called _find_offshore_point(datasets, spot["lat"], spot["lng"]), whose ring
walk accepts THE FIRST WET CELL it touches with no directional or distance constraint.
The radius-1 visit order starts at (di=-1, dj=-1) — a landward diagonal on an
east-facing barrier island — so the walk sampled the sound behind the island, where
shts reads exactly 0.0 while swh carries local wind ripple. Meanwhile
spots_enriched.json already carried nwps_node_lat/lng for 600 of 648 spots: the node
select_node had chosen WITH the ±90° seaward half-plane rule. The fetcher never read it.

Measured: 47 spots sampled landward of their own shore normal; 332 sampled more than
0.3 km from their baked node (worst 27.9 km). Downstream, swell_hs > hs at 3.4% of
spot-hours roster-wide — 26.8% at mhx, 20.5% at mlb, both barrier-island coasts.

THE FIXTURE MIRRORS THAT GEOMETRY rather than describing it abstractly. A 3x3 grid:
the middle column is the barrier island (land), the west column is the sound (water),
the east column is the ocean (water). The spot sits on the island with orientation 90
(east-facing). The ring walk therefore lands in the SOUND and the baked node is in the
OCEAN — the two differ, which is what makes "which one did it use" a real question.

EVERY EXPECTED VALUE IS HAND-COMPUTED with the arithmetic in a comment. Nothing is
derived by calling the function under test. The collaborator
_extract_time_series_from_datasets is stubbed to RECORD the coordinates it is handed,
because "extracts at the baked coordinates" is a claim about the wiring in fetch(),
and no unit test on the helpers alone can catch the fetcher ignoring the node.

Run: python -m pipeline.tests.test_nwps_baked_node   (or pytest)
"""
from __future__ import annotations

import logging
import math
from pathlib import Path

import numpy as np

from pipeline.forecast import nwps


def _close(a, b, tol=1e-9):
    return abs(a - b) <= tol


# --------------------------------------------------------------------------- #
# A fake cfgrib dataset, exposing only what the code under test actually uses   #
# --------------------------------------------------------------------------- #
# The real code touches: ds.data_vars, ds["latitude"].values, ds["longitude"].values,
# ds["longitude"].min(), ds[var].isel(latitude=, longitude=), and .values on the result.
NAN = float("nan")

# lats ASCENDING south->north, lngs ASCENDING west->east — the cfgrib convention the
# reader assumes (it does np.argmin(np.abs(lats - lat)) with no ordering check).
LATS = [36.30, 36.35, 36.40]
LNGS = [-75.90, -75.85, -75.80]

#            j=0 sound   j=1 island   j=2 ocean
# i=0 south    water        LAND        water
# i=1 mid      water        LAND        water     <- the spot sits at (1,1), on the island
# i=2 north    water        LAND        water
GRID = [
    [0.42, NAN, 1.30],
    [0.44, NAN, 1.35],
    [0.46, NAN, 1.40],
]

SPOT_LAT, SPOT_LNG = 36.35, -75.85        # index (1,1) — land
BAKED_LAT, BAKED_LNG = 36.35, -75.80      # index (1,2) — the ocean cell, seaward
# The ring walk's radius-1 order is (-1,-1), (-1,0), (-1,+1), (0,-1), (0,+1), (+1,-1),
# (+1,0), (+1,+1). From (1,1) the first candidate is (0,0) = lat 36.30, lng -75.90 —
# the SOUND. It is water, so first-wet-wins accepts it and the walk stops there.
WALK_LAT, WALK_LNG = 36.30, -75.90

ORIENTATION = 90.0                        # east-facing


class _FakeSample:
    def __init__(self, value):
        # A cell carries a whole STEP AXIS, not one number — the real reader always gets
        # all 145 forecast steps for the cell it samples. A grid entry may therefore be a
        # sequence, which is what makes the all-NaN vs any-NaN distinction observable.
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
    def __init__(self, var_name="swh", grid=None):
        self._var = var_name
        self._grid = grid if grid is not None else GRID
        self.data_vars = [var_name]
        # only for _describe_dataset's diagnostic log line
        self.sizes = {"latitude": len(LATS), "longitude": len(LNGS)}
        self.coords = ["latitude", "longitude"]

    def __getitem__(self, key):
        if key == "latitude":
            return _FakeCoord(LATS)
        if key == "longitude":
            return _FakeCoord(LNGS)
        if key == self._var:
            return _FakeVar(self._grid)
        raise KeyError(key)


def _datasets():
    return [_FakeDataset()]


def _spot(name="T", baked=True, orientation=ORIENTATION):
    s = {"name": name, "lat": SPOT_LAT, "lng": SPOT_LNG,
         "nwps_wfo": "mhx", "orientation_deg": orientation}
    if baked:
        s["nwps_node_lat"], s["nwps_node_lng"] = BAKED_LAT, BAKED_LNG
    return s


class _Capture(logging.Handler):
    """Collect records off the module's logger so a warning can be asserted, not assumed."""
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def text(self):
        return "\n".join(r.getMessage() for r in self.records)


def _run_fetch(spots, tmp_name, grid=None, extract=None):
    """Drive nwps.fetch() with the network, the GRIB, and the extractor stubbed out.

    Returns (result, list_of_(lat, lng)_the_extractor_was_handed, captured_log_records).
    Only collaborators are stubbed — the node-selection wiring under test runs for real.
    """
    seen = []

    def _fake_extract(datasets, lat, lng):
        seen.append((round(lat, 6), round(lng, 6)))
        return extract(lat, lng) if extract else [
            {"valid_time": "2026-08-22T12:00:00Z", "hs": 1.0, "swell_hs": 0.5}]

    saved = (nwps._locate_cycle, nwps._open_grib_datasets,
             nwps._extract_time_series_from_datasets, nwps.NWPS_FORECAST_FILE,
             nwps.NWPS_CACHE_DIR)
    cap = _Capture()
    nwps.log.addHandler(cap)
    prev_level = nwps.log.level
    nwps.log.setLevel(logging.INFO)
    tmp = Path("/tmp/claude-0/-home-user-StormyPetrel/"
               "c0d14651-f350-5987-84e1-cf8ad55860d6/scratchpad")
    tmp.mkdir(parents=True, exist_ok=True)
    try:
        nwps._locate_cycle = lambda wfo, use_cache: (Path("/dev/null"), "20260822", "12")
        nwps._open_grib_datasets = lambda p: (
            [_FakeDataset(grid=grid)] if grid is not None else _datasets())
        nwps._extract_time_series_from_datasets = _fake_extract
        nwps.NWPS_FORECAST_FILE = tmp / tmp_name
        nwps.NWPS_CACHE_DIR = tmp
        result = nwps.fetch(spots, use_cache=True)
    finally:
        (nwps._locate_cycle, nwps._open_grib_datasets,
         nwps._extract_time_series_from_datasets, nwps.NWPS_FORECAST_FILE,
         nwps.NWPS_CACHE_DIR) = saved
        nwps.log.removeHandler(cap)
        nwps.log.setLevel(prev_level)
    return result, seen, cap


# --------------------------------------------------------------------------- #
# 0 — the fixture really does separate the two candidate points                #
# --------------------------------------------------------------------------- #
def test_the_fixture_puts_the_walk_and_the_baked_node_in_different_places():
    """Guard the premise. If the ring walk and the baked node coincided, every test
    below would pass with the defect still present.

        spot   (36.35, -75.85)  index (1,1), GRID[1][1] = NaN -> LAND
        baked  (36.35, -75.80)  index (1,2), GRID[1][2] = 1.35 -> water, due EAST
        walk   (36.30, -75.90)  index (0,0), GRID[0][0] = 0.42 -> water, to the SW

    Bearings from the spot, orientation 90:
        to baked: due east          -> 90.0°,  |90.0 - 90| =   0.0° off  -> SEAWARD
        to walk : south-west        -> 218.9°, |218.9 - 90| = 128.9° off -> LANDWARD
    """
    assert (BAKED_LAT, BAKED_LNG) != (WALK_LAT, WALK_LNG)
    assert math.isnan(GRID[1][1]), "the spot's own cell must be land or the walk never runs"
    assert not math.isnan(GRID[1][2]) and not math.isnan(GRID[0][0])
    # the walk really does go to the sound — asserted against _find_offshore_point itself,
    # which this commit does not change
    lat, lng, rings = nwps._find_offshore_point(_datasets(), SPOT_LAT, SPOT_LNG)
    assert (round(lat, 2), round(lng, 2)) == (WALK_LAT, WALK_LNG), (lat, lng)
    assert rings == 1, rings


# --------------------------------------------------------------------------- #
# 1 — a spot WITH a baked node extracts at the baked coordinates               #
# --------------------------------------------------------------------------- #
def test_a_baked_node_is_used_instead_of_the_ring_walk():
    """THE FIX. The extractor must be handed (36.35, -75.80) — the ocean cell — and NOT
    (36.30, -75.90), the sound cell the ring walk would have chosen. Before this change
    the fetcher never read nwps_node_lat/lng at all and always got the sound."""
    _, seen, cap = _run_fetch([_spot()], "baked.json")
    assert seen == [(BAKED_LAT, BAKED_LNG)], seen
    assert seen != [(WALK_LAT, WALK_LNG)], "still sampling the ring-walk point"
    assert "using baked seaward node" in cap.text()


def test_the_baked_node_is_used_even_when_the_walk_would_have_succeeded():
    """The baked node wins on merit, not merely as a fallback for a failed walk. Here
    the spot's OWN cell is water, so the walk would return it at 0 rings — and the baked
    node must still take precedence.

        GRID[1][1] = 0.90 (water) -> _find_offshore_point returns (36.35, -75.85), 0 rings
        the fetcher must still hand the extractor (36.35, -75.80)
    """
    grid = [row[:] for row in GRID]
    grid[1][1] = 0.90
    _, seen, _ = _run_fetch([_spot()], "baked2.json", grid=grid)
    assert seen == [(BAKED_LAT, BAKED_LNG)], seen
    assert seen != [(SPOT_LAT, SPOT_LNG)], "the nominal cell beat the baked node"


# --------------------------------------------------------------------------- #
# 2 — a spot WITHOUT a baked node still walks                                  #
# --------------------------------------------------------------------------- #
def test_a_spot_with_no_baked_node_still_uses_the_ring_walk():
    """48 spots have no baked node (sgx 30, mtr 11, lox 6, eka 1) and must keep working
    exactly as before. The walk lands in the sound at (36.30, -75.90) — that is the
    ring walk's unchanged behaviour, not an endorsement of it."""
    _, seen, _ = _run_fetch([_spot(baked=False)], "nobake.json")
    assert seen == [(WALK_LAT, WALK_LNG)], seen


def test_a_half_baked_node_falls_back_rather_than_using_one_coordinate():
    """Either coordinate missing means no usable node. A latitude paired with a None
    longitude must not be assembled into a point."""
    for missing in ("nwps_node_lat", "nwps_node_lng"):
        s = _spot()
        s[missing] = None
        _, seen, _ = _run_fetch([s], "half.json")
        assert seen == [(WALK_LAT, WALK_LNG)], (missing, seen)


# --------------------------------------------------------------------------- #
# 3 — a baked node that tests as LAND falls back and warns                     #
# --------------------------------------------------------------------------- #
def test_a_baked_node_that_tests_as_land_falls_back_to_the_walk_and_warns():
    """A stale assignment or a regridded nest. The node must not be trusted silently.

        GRID[1][2] = NaN -> the baked node (36.35, -75.80) is now LAND
        -> fall back to the ring walk, which returns (36.30, -75.90)
        -> and WARN, naming the spot
    """
    grid = [row[:] for row in GRID]
    grid[1][2] = NAN
    _, seen, cap = _run_fetch([_spot(name="Corolla Beach")], "bakedland.json", grid=grid)
    assert seen == [(WALK_LAT, WALK_LNG)], seen
    warnings = [r for r in cap.records if r.levelno >= logging.WARNING]
    assert warnings, "a baked node testing as land must not be swallowed"
    txt = "\n".join(r.getMessage() for r in warnings)
    assert "Corolla Beach" in txt, txt
    assert "BAKED NODE" in txt and "LAND" in txt, txt


def test_the_land_test_is_the_same_predicate_the_walk_uses():
    """_baked_node_is_water and _find_offshore_point must not drift apart: both route
    through _cell_is_water. Pinned by exact boolean at every cell of the fixture grid.

        column 0 (sound)  = 0.42 / 0.44 / 0.46  -> water  -> True
        column 1 (island) = NaN                 -> land   -> False
        column 2 (ocean)  = 1.30 / 1.35 / 1.40  -> water  -> True
    """
    ds = _FakeDataset()
    for i in range(3):
        assert nwps._cell_is_water([ds], i, 0) is True, i
        assert nwps._cell_is_water([ds], i, 1) is False, i
        assert nwps._cell_is_water([ds], i, 2) is True, i
    # and via the baked-node wrapper, at the coordinates rather than the indices
    assert nwps._baked_node_is_water(_datasets(), BAKED_LAT, BAKED_LNG) is True
    assert nwps._baked_node_is_water(_datasets(), SPOT_LAT, SPOT_LNG) is False


def test_a_partly_masked_cell_counts_as_water_across_the_whole_step_axis():
    """A cell carries 145 forecast steps, and the predicate is ALL-NaN, not ANY-NaN:
    land is a cell with NO data at ANY step. A cell that goes NaN for part of the
    horizon — a nest edge, a drying cell, a step the model did not write — still has
    real wave data and must not be discarded as land.

        [NaN, 0.50, NaN] -> np.all(isnan) is False -> not False -> WATER  (True)
        [NaN, NaN,  NaN] -> np.all(isnan) is True  -> not True  -> LAND   (False)
        [0.4, 0.50, 0.6] -> np.all(isnan) is False                -> WATER  (True)

    Under an ANY-NaN predicate the first row would read LAND, which would throw away a
    usable cell and silently push the walk further out. A single-value fixture cannot
    tell the two predicates apart at all — hence the step axis here.
    """
    grid = [row[:] for row in GRID]
    grid[0][2] = [NAN, 0.50, NAN]     # partly masked -> still water
    grid[1][2] = [NAN, NAN, NAN]      # fully masked  -> land
    grid[2][2] = [0.40, 0.50, 0.60]   # clean         -> water
    ds = _FakeDataset(grid=grid)
    assert nwps._cell_is_water([ds], 0, 2) is True
    assert nwps._cell_is_water([ds], 1, 2) is False
    assert nwps._cell_is_water([ds], 2, 2) is True
    # and the baked-node wrapper agrees, since it routes through the same predicate:
    # the baked node (36.35, -75.80) is index (1,2), now fully masked
    assert nwps._baked_node_is_water([ds], BAKED_LAT, BAKED_LNG) is False
    assert nwps._baked_node_is_water([ds], 36.30, -75.80) is True


# --------------------------------------------------------------------------- #
# 4 — the walk's direction is reported, not just its distance                  #
# --------------------------------------------------------------------------- #
def test_the_seaward_diagnostic_is_exact():
    """Hand-computed from the GREAT-CIRCLE initial-bearing formula, orientation 90:

        y = sin(dlng)·cos(lat2)
        x = cos(lat1)·sin(lat2) − sin(lat1)·cos(lat2)·cos(dlng)
        bearing = (degrees(atan2(y, x)) + 360) mod 360

    spot (36.35, -75.85) -> baked (36.35, -75.80). Same latitude, dlng = +0.05°.
    Due east is NOT exactly 90° on a great circle — a rhumb line east curves — and
    with lat1 == lat2 the x term collapses to sin·cos·(1 − cos dlng):
        y = sin(0.05°)·cos(36.35°)                 = 7.028539022409e-04
        x = sin(36.35°)·cos(36.35°)·(1−cos(0.05°)) = 1.817729705032e-07
        atan2(y, x) = 89.985182093°  -> off = |89.985182093 − 90| = 0.014817907
    That 0.0148° is real spherical geometry, not float noise, so it is pinned as such.

    spot (36.35, -75.85) -> walk (36.30, -75.90). 0.05° south, 0.05° west:
        bearing = 218.872158773°  -> off = 128.872158773  -> NOT seaward
    """
    s = _spot()
    brg, off, sea = nwps._seaward_diag(s, BAKED_LAT, BAKED_LNG)
    assert _close(brg, 89.985182093), brg
    assert _close(off, 0.014817907), off
    assert sea is True

    brg, off, sea = nwps._seaward_diag(s, WALK_LAT, WALK_LNG)
    assert _close(brg, 218.872158773), brg
    assert _close(off, 128.872158773), off
    assert sea is False


def test_a_spot_with_no_orientation_has_no_assessable_direction():
    """No shore normal, no half-plane. Returns None rather than guessing a bearing —
    the 0/648 case today, but the guard is what keeps it from becoming a crash."""
    assert nwps._seaward_diag(_spot(orientation=None), WALK_LAT, WALK_LNG) is None


def test_a_landward_walk_is_logged_with_its_bearing_and_named_in_the_summary():
    """The old line read "fell back to (...) at 1 cells away" and stopped there, so a
    walk into the sound and a walk out to sea printed identically. Both the per-spot
    line and the run summary must now say LANDWARD and name the spot."""
    _, seen, cap = _run_fetch([_spot(name="Salvo", baked=False)], "landward.json")
    assert seen == [(WALK_LAT, WALK_LNG)]
    txt = cap.text()
    assert "LANDWARD" in txt, txt
    assert "129° off normal" in txt or "128° off normal" in txt, txt
    assert "ring-walk-LANDWARD=1" in txt, txt
    warned = "\n".join(r.getMessage() for r in cap.records if r.levelno >= logging.WARNING)
    assert "Salvo" in warned and "LANDWARD" in warned, warned


def test_a_seaward_walk_is_counted_separately_and_not_warned():
    """The other six of the twelve measured MHX spots walked correctly. A seaward walk
    must not be reported as a problem.

        GRID[1][2] stays water and GRID[0][0] becomes LAND, so the radius-1 order
        skips (0,0) and the first WET candidate is (0,2) = (36.30, -75.80).
        bearing from (36.35,-75.85) to (36.30,-75.80) = 141.127841227°
        off = |141.127841227 - 90| = 51.127841227 -> within ±90 -> SEAWARD
    """
    grid = [row[:] for row in GRID]
    grid[0][0] = NAN
    _, seen, cap = _run_fetch([_spot(baked=False)], "seaward.json", grid=grid)
    assert seen == [(36.30, -75.80)], seen
    txt = cap.text()
    assert "SEAWARD" in txt, txt
    assert "ring-walk-seaward=1" in txt, txt
    assert "ring-walk-LANDWARD=0" in txt, txt


# --------------------------------------------------------------------------- #
# 5 — the summary counters                                                     #
# --------------------------------------------------------------------------- #
def test_the_node_selection_summary_counts_all_four_outcomes():
    """Four spots in one run, each taking a different route.

        Good     baked (36.35, -75.80), water            -> baked-node = 1
        Stale    baked (36.40, -75.80), GRID[2][2] = NaN -> rejected as land = 1,
                                                            THEN walks -> landward
        Walker   no baked node                           -> walks -> landward
        NoOrient no baked node, no orientation_deg       -> walks -> no-orientation

    So ring-walk-LANDWARD = 2, not 1: the two axes are orthogonal, and a spot whose
    baked node is rejected still has its FALLBACK WALK direction classified. That is
    the point of counting both — "baked node rejected" alone would not tell you the
    replacement was worse.
        seaward = 0, landward = 2 (Stale, Walker), no-orientation = 1 (NoOrient)
    """
    grid = [row[:] for row in GRID]
    grid[2][2] = NAN                                  # the north ocean cell -> land
    good = _spot(name="Good")
    stale = _spot(name="Stale")
    stale["nwps_node_lat"], stale["nwps_node_lng"] = 36.40, -75.80
    walker = _spot(name="Walker", baked=False)
    noorient = _spot(name="NoOrient", baked=False, orientation=None)
    _, seen, cap = _run_fetch([good, stale, walker, noorient], "summary.json", grid=grid)
    assert len(seen) == 4, seen
    assert seen[0] == (BAKED_LAT, BAKED_LNG), seen        # Good took the baked node
    assert seen[1:] == [(WALK_LAT, WALK_LNG)] * 3, seen   # the other three all walked
    txt = cap.text()
    assert "baked-node=1" in txt, txt
    assert "baked-node-rejected-as-land=1" in txt, txt
    assert "ring-walk-LANDWARD=2" in txt, txt
    assert "ring-walk-no-orientation=1" in txt, txt
    assert "ring-walk-seaward=0" in txt, txt
    warned = "\n".join(r.getMessage() for r in cap.records if r.levelno >= logging.WARNING)
    assert "Stale, Walker" in warned, warned                # named, sorted
    assert "1 baked node(s) tested as land: Stale" in warned, warned


# --------------------------------------------------------------------------- #
# 6 — swell_hs > hs is surfaced, never repaired                                #
# --------------------------------------------------------------------------- #
def test_an_impossible_swell_pair_is_warned_and_left_exactly_as_read():
    """Swell is a COMPONENT of total, so swell_hs > hs cannot be a real sea state. It
    must be loud and it must be unmodified — clamping would hide the fault while the
    corrupt value still flowed into chop_ratio and the rating.

        hs 0.108, swell_hs 0.133 -> 0.133 > 0.108 -> impossible, warn, keep both
        hs 0.500, swell_hs 0.500 -> equal, NOT greater -> no warning (pure swell)
        hs 1.000, swell_hs 0.400 -> normal
        hs None,  swell_hs 0.400 -> incomparable, skipped
    """
    cap = _Capture()
    nwps.log.addHandler(cap)
    try:
        series = [
            {"valid_time": "2026-08-21T19:00:00Z", "hs": 0.108, "swell_hs": 0.133},
            {"valid_time": "2026-08-21T20:00:00Z", "hs": 0.500, "swell_hs": 0.500},
            {"valid_time": "2026-08-21T21:00:00Z", "hs": 1.000, "swell_hs": 0.400},
            {"valid_time": "2026-08-21T22:00:00Z", "hs": None, "swell_hs": 0.400},
        ]
        before = [dict(r) for r in series]
        n = nwps._warn_impossible_swell_pairs(series, "Corolla Beach")
    finally:
        nwps.log.removeHandler(cap)

    assert n == 1, n
    assert series == before, "the checker must not modify the records"
    txt = "\n".join(r.getMessage() for r in cap.records if r.levelno >= logging.WARNING)
    assert "Corolla Beach" in txt and "2026-08-21T19:00:00Z" in txt, txt
    assert "IMPOSSIBLE" in txt, txt


def test_impossible_pairs_are_counted_and_named_in_the_run_summary():
    """End to end: the fetcher must roll the per-record count up to the run summary and
    name the spots, so a bad cycle is visible without grepping every line.

        two impossible records on one spot -> "2 record(s) across 1 spot(s)"
    """
    def _extract(lat, lng):
        return [
            {"valid_time": "2026-08-21T19:00:00Z", "hs": 0.108, "swell_hs": 0.133},
            {"valid_time": "2026-08-21T20:00:00Z", "hs": 0.110, "swell_hs": 0.140},
            {"valid_time": "2026-08-21T21:00:00Z", "hs": 1.000, "swell_hs": 0.400},
        ]
    _, _, cap = _run_fetch([_spot(name="Corolla Beach")], "impossible.json",
                           extract=_extract)
    txt = "\n".join(r.getMessage() for r in cap.records if r.levelno >= logging.WARNING)
    assert "2 record(s) across 1 spot(s)" in txt, txt
    assert "Corolla Beach" in txt, txt


def test_a_clean_run_reports_no_impossible_pairs_at_all():
    """The warning must be conditional — a clean cycle should not print a zero line
    that trains the reader to ignore it."""
    _, _, cap = _run_fetch([_spot()], "clean.json")
    txt = cap.text()
    assert "swell_hs > hs" not in txt, txt
    assert "IMPOSSIBLE" not in txt, txt


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} nwps-baked-node checks passed")


if __name__ == "__main__":
    _run_all()
