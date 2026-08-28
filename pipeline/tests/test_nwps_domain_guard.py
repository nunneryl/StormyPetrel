"""The NWPS fetcher checks that a node is on the grid its nwps_wfo actually names.

THE GAP. Every coordinate lookup in nwps.py is an argmin — in _baked_node_is_water, in
_find_offshore_point, and inside xarray's .sel(method="nearest") in
_extract_time_series_from_datasets. An argmin never errors and never reports: handed a
coordinate past the end of an axis it returns the last index. So a node outside the loaded
grid reads that grid's EDGE CELL, and the forecast published for the spot is the edge
cell's forecast, silently.

Nothing caught it, and the far-cap check structurally could not: it measures how far the
REQUESTED node is from the spot, and on all six affected spots that number was correct and
comfortably inside the cap. Cherry Grove Pier carried an ilm-nest node (33.83039,
-78.61027) under nwps_wfo='chs', recorded 2077 m, and published from chs's 33.5800 boundary
row 27.8 km away for months. The distance that was never computed is the one to the cell
the lookup ACTUALLY RESOLVED TO — which is what these tests pin.

THE POSTURE IS REPORT, NEVER REFUSE, exactly as for the over-cap check next to it:
test_an_out_of_domain_node_is_published_not_withheld is the pin. A skipped spot produces no
forecast rows at all and blanks its page; an off-grid node is a wrong LABEL, and the edge
cell it reads is still a real forecast.

EVERY EXPECTED VALUE IS WRITTEN LITERALLY, derived offline from the haversine and the
domain arithmetic with the working in the docstring. None is produced by calling the code
under test.

THE DOMAIN NUMBERS BELOW ARE FIXTURE VALUES, NOT CONSTANTS OF THE SYSTEM. The guard reads
the bounds off the opened dataset's own axes; these axes are shaped after the real mtr
eastern boundary (lon -120.7400) only so the fixtures read like the failure they came from.

Run: python -m pipeline.tests.test_nwps_domain_guard   (or pytest)
"""
from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from pipeline.forecast import nwps

NAN = float("nan")

# --------------------------------------------------------------------------- #
# The fixture grid                                                             #
# --------------------------------------------------------------------------- #
# 5x5, 0.05 deg in latitude and 0.04 deg in longitude, with its eastern edge on
# -120.7400 — the column the four SLO spots were clamped to on the real mtr nest.
#   domain: lat 35.0000 .. 35.2000, lon -120.9000 .. -120.7400
LATS = [35.00, 35.05, 35.10, 35.15, 35.20]
LNGS = [-120.90, -120.86, -120.82, -120.78, -120.74]

# Same grid expressed 0-360, to exercise the longitude-convention round trip.
#   239.10 .. 239.26  ==  -120.90 .. -120.74
LNGS_360 = [239.10, 239.14, 239.18, 239.22, 239.26]

# The far cap this grid produces, so the domain assertions can be kept clear of it.
#   mid_lat = median(LATS) = 35.10
#   lat step 0.05 deg along a meridian            = 5.559746332228 km
#   lng step 0.04 deg along the 35.10 parallel    = 3.638963888100 km
#   spacing = (5.559746332228 + 3.638963888100)/2 = 4.599355110164 km
#   1.5 x spacing = 6.899032665246 > 3.0 floor -> cap = 6.899032665246 km
CAP_KM = 6.899032665245883


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
    diagnostic log line. Same shape as the sibling suites' fixture."""

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


def _grid(lngs=None, cells=None):
    return [_FakeDataset(LATS, lngs if lngs is not None else LNGS,
                         cells if cells is not None else _all_water())]


def _spot(name, lat, lng, baked=None, orientation=270.0, wfo="mtr"):
    s = {"name": name, "lat": lat, "lng": lng,
         "nwps_wfo": wfo, "orientation_deg": orientation}
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


def _run_fetch(spots, tmp_name, datasets, walk_stub=None):
    """Drive nwps.fetch() with the network, the GRIB and the extractor stubbed out.

    Returns (result, [(lat, lng) handed to the extractor], captured log). Only
    collaborators are stubbed — the domain wiring under test runs for real. *walk_stub*,
    when given, replaces _find_offshore_point so the ring-walk BRANCH can be driven to a
    chosen cell; the guard itself is untouched.
    """
    seen = []

    def _fake_extract(_ds, lat, lng):
        seen.append((round(lat, 6), round(lng, 6)))
        return [{"valid_time": "2026-08-24T12:00:00Z", "hs": 1.0, "swell_hs": 0.5}]

    saved = (nwps._locate_cycle, nwps._open_grib_datasets,
             nwps._extract_time_series_from_datasets, nwps.NWPS_FORECAST_FILE,
             nwps.NWPS_CACHE_DIR, nwps._find_offshore_point)
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
        if walk_stub is not None:
            nwps._find_offshore_point = walk_stub
        result = nwps.fetch(spots, use_cache=True)
    finally:
        (nwps._locate_cycle, nwps._open_grib_datasets,
         nwps._extract_time_series_from_datasets, nwps.NWPS_FORECAST_FILE,
         nwps.NWPS_CACHE_DIR, nwps._find_offshore_point) = saved
        nwps.log.removeHandler(cap)
        nwps.log.setLevel(prev)
    return result, seen, cap


# --------------------------------------------------------------------------- #
# 1 — the predicate itself                                                     #
# --------------------------------------------------------------------------- #
def test_a_node_inside_the_domain_is_not_a_miss():
    """The grid centre. Inside on both axes, so the guard must return None and say
    nothing at all."""
    assert nwps._grid_domain_miss(_grid(), 35.10, -120.82) is None


def test_a_node_outside_in_longitude_is_a_miss_and_reports_the_edge_cell():
    """-120.70 is 0.04 deg EAST of the -120.7400 edge — the SLO failure exactly.

    argmin over the longitude axis returns the last index, so the lookup resolves to
    (35.10, -120.74): the latitude is an exact axis value and survives, the longitude
    clamps. The bounds returned are the axis min/max, unchanged.
    """
    got = nwps._grid_domain_miss(_grid(), 35.10, -120.70)
    assert got == (35.00, 35.20, -120.90, -120.74, 35.10, -120.74), got


def test_a_node_outside_in_latitude_is_a_miss():
    """The other axis, which a longitude-only check would wave through.
    35.24 is 0.04 deg NORTH of the 35.2000 edge -> resolves to (35.20, -120.82)."""
    got = nwps._grid_domain_miss(_grid(), 35.24, -120.82)
    assert got == (35.00, 35.20, -120.90, -120.74, 35.20, -120.82), got


def test_a_node_outside_below_the_minimum_is_a_miss_on_both_axes():
    """Past the LOW end of each axis, so a check written only against the maxima fails.
    (34.90, -121.00) resolves to the south-west corner cell (35.00, -120.90)."""
    got = nwps._grid_domain_miss(_grid(), 34.90, -121.00)
    assert got == (35.00, 35.20, -120.90, -120.74, 35.00, -120.90), got
    assert nwps._grid_domain_miss(_grid(), 34.90, -120.82) is not None   # latitude alone
    assert nwps._grid_domain_miss(_grid(), 35.10, -121.00) is not None   # longitude alone


def test_a_node_exactly_on_the_boundary_is_INSIDE():
    """THE AVILA BEACH PIN. Its node 35.15796,-120.74000 sits exactly on mtr's edge
    column: an edge cell centre is a legitimate placement, not a miss, and flagging it
    would make the warning noise instead of signal.

    All four corners of the fixture, plus the mid-edge cells — every one an exact axis
    value at a limit. A strict `<` on either side turns each of these into a false
    positive.
    """
    for lat, lng in ((35.00, -120.90), (35.00, -120.74),
                     (35.20, -120.90), (35.20, -120.74),
                     (35.20, -120.82), (35.00, -120.82),
                     (35.10, -120.90), (35.10, -120.74)):
        assert nwps._grid_domain_miss(_grid(), lat, lng) is None, (lat, lng)


def test_the_boundary_survives_float_noise_but_not_a_real_excursion():
    """A node that IS an exact cell centre must still read as inside after a JSON round
    trip moves its last bit — which is what _DOMAIN_EDGE_EPS_DEG absorbs, and all it
    absorbs. 1e-6 deg is ~0.1 m; the fixture's own cells are 0.04-0.05 deg apart.

        35.20 + 1e-12  -> inside  (float noise)
        35.20 + 1e-4   -> MISS    (11 m past the edge is still past the edge)
    """
    assert nwps._grid_domain_miss(_grid(), 35.20 + 1e-12, -120.82) is None
    assert nwps._grid_domain_miss(_grid(), 35.20 + 1e-4, -120.82) is not None
    assert nwps._grid_domain_miss(_grid(), -120.90 * 0 + 35.10, -120.74 + 1e-12) is None
    assert nwps._grid_domain_miss(_grid(), 35.10, -120.74 + 1e-4) is not None


def test_no_wave_dataset_means_no_verdict():
    """Same trust-the-input posture _baked_node_is_water and _find_offshore_point take
    when there is nothing to judge against. A grid we cannot read is not a miss."""
    assert nwps._grid_domain_miss([], 99.0, 99.0) is None


def test_a_zero_to_threesixty_grid_is_judged_and_reported_in_the_input_convention():
    """NWPS nests are not all signed. The same domain written 239.10..239.26 must reach
    the same verdict for -120.70, and must report its bounds and its resolved cell back
    in the caller's convention rather than leaking 239.30 into a log line.

        -120.70 normalises to 239.30, which is past the 239.26 axis maximum -> MISS
        resolved cell 239.26 -> printed as -120.74; bounds 239.10/239.26 -> -120.90/-120.74
    """
    inside = nwps._grid_domain_miss(_grid(lngs=LNGS_360), 35.10, -120.82)
    assert inside is None, inside
    got = nwps._grid_domain_miss(_grid(lngs=LNGS_360), 35.10, -120.70)
    assert got is not None
    lat_min, lat_max, lng_min, lng_max, res_lat, res_lng = got
    assert (lat_min, lat_max, res_lat) == (35.00, 35.20, 35.10), got
    assert abs(lng_min - (-120.90)) < 1e-9, got
    assert abs(lng_max - (-120.74)) < 1e-9, got
    assert abs(res_lng - (-120.74)) < 1e-9, got


# --------------------------------------------------------------------------- #
# 2 — the warning, at fetch time                                               #
# --------------------------------------------------------------------------- #
def test_an_out_of_domain_node_is_published_not_withheld():
    """THE PIN THAT KEEPS THIS A REPORT. Warn, count, name — and still hand the extractor
    the node's own coordinates, unchanged. No skip, no clamp, no substitution.

        spot/node (35.10, -120.70), 0.04 deg east of the -120.7400 edge
        the extractor must still receive (35.10, -120.70)
    """
    spot = _spot("Pismo Beach Pier", 35.10, -120.70, baked=(35.10, -120.70))
    result, seen, cap = _run_fetch([spot], "dom_lng.json", _grid())

    assert seen == [(35.10, -120.70)], seen            # published, at the SAME cell
    assert "Pismo Beach Pier" in result, result.keys()
    assert len(result["Pismo Beach Pier"]) == 1, result
    w = cap.warnings()
    assert "Pismo Beach Pier" in w and "OUTSIDE" in w, w
    assert "out-of-domain=1" in cap.text(), cap.text()


def test_the_warning_names_the_spot_its_wfo_the_node_and_the_bounds():
    """Everything a reader needs to act without opening a GRIB: which spot, which grid
    label sent it there, where the node is, and what the domain actually was.

        node (35.10, -120.70), nwps_wfo 'mtr'
        domain lat 35.0000..35.2000, lon -120.9000..-120.7400
    """
    spot = _spot("Shell Beach", 35.10, -120.70, baked=(35.10, -120.70))
    _, _, cap = _run_fetch([spot], "dom_text.json", _grid())
    w = cap.warnings()
    assert "Shell Beach" in w, w
    assert "'mtr' grid its nwps_wfo" in w, w
    assert "node (35.10000, -120.70000)" in w, w
    assert "domain lat 35.0000..35.2000, lon -120.9000..-120.7400" in w, w
    assert "cell (35.10000, -120.74000)" in w, w


def test_the_warning_carries_the_distance_to_the_RESOLVED_CELL_not_to_the_node():
    """THE NUMBER NOTHING REPORTED. Cherry Grove Pier's shape: a node 0.91 km from the
    spot — correct, and well inside the 6.90 km cap, so the cap check stayed silent — and
    an edge cell 4.55 km away that is where the forecast actually came from.

        spot (35.10, -120.69)   node (35.10, -120.70)   resolved (35.10, -120.74)
        spot -> node  0.909740977752 km  = "0.91 km"   <- what every existing check saw
        node -> cell  3.638963888100 km  = "3.64 km"   <- the clamp displacement
        spot -> cell  4.548704842945 km  = "4.55 km"   <- what the forecast is really from

    The last two are the new figures. The first must not be mistaken for them: if the
    warning reported the node distance it would read 0.91 km and understate the problem
    fivefold.
    """
    spot = _spot("Cherry Grove Pier", 35.10, -120.69, baked=(35.10, -120.70))
    _, _, cap = _run_fetch([spot], "dom_dist.json", _grid())
    w = cap.warnings()
    assert "3.64 km from the node" in w, w
    assert "4.55 km from the spot" in w, w
    assert "0.91 km" not in w, w
    assert "OVER" not in w, "the far-cap check must stay silent — that is the whole point"


def test_a_node_inside_the_domain_warns_nothing_and_still_publishes():
    """The negative case, which is most of the roster. An in-domain baked node produces
    no warning, a zero count, and the same extraction it always did."""
    spot = _spot("Avila Beach", 35.10, -120.82, baked=(35.15, -120.78))
    result, seen, cap = _run_fetch([spot], "dom_in.json", _grid())
    assert seen == [(35.15, -120.78)], seen
    assert "Avila Beach" in result
    assert "OUTSIDE" not in cap.warnings(), cap.warnings()
    assert "out-of-domain=0" in cap.text(), cap.text()
    assert "have a node OUTSIDE the grid" not in cap.warnings(), cap.warnings()


def test_an_out_of_domain_node_in_LATITUDE_warns():
    """The latitude axis, kept clear of the far cap so the two warnings cannot be
    confused for one another.

        spot (35.19, -120.82)  node (35.24, -120.82)  resolved (35.20, -120.82)
        spot -> node  5.559746332228 km  < 6.899032665246 cap -> the cap stays SILENT
        node -> cell  4.447797065782 km  = "4.45 km"
        spot -> cell  1.111949266446 km  = "1.11 km"
    """
    spot = _spot("North Edge", 35.19, -120.82, baked=(35.24, -120.82))
    _, seen, cap = _run_fetch([spot], "dom_lat.json", _grid())
    w = cap.warnings()
    assert seen == [(35.24, -120.82)], seen
    assert "North Edge" in w and "OUTSIDE" in w, w
    assert "cell (35.20000, -120.82000)" in w, w
    assert "4.45 km from the node" in w, w
    assert "1.11 km from the spot" in w, w
    assert "OVER" not in w, w
    assert "out-of-domain=1" in cap.text(), cap.text()


# --------------------------------------------------------------------------- #
# 3 — both paths                                                               #
# --------------------------------------------------------------------------- #
def test_a_spot_with_no_baked_node_is_unaffected():
    """The ring-walk path, running for real. It indexes the same reference grid, so the
    cell it returns is an exact cell centre and cannot be out of domain — nothing must
    warn, and the walk's own reporting is untouched.

        only (2,2) = (35.10, -120.82) is wet; the spot sits on it, so the walk takes the
        nominal nearest cell at ring 0
    """
    cells = [[NAN] * 5 for _ in range(5)]
    cells[2][2] = 1.2
    spot = _spot("Walker", 35.10, -120.82)          # no baked node
    result, seen, cap = _run_fetch([spot], "dom_walk_ok.json", _grid(cells=cells))
    assert seen == [(35.10, -120.82)], seen
    assert "Walker" in result
    assert "OUTSIDE" not in cap.warnings(), cap.warnings()
    assert "out-of-domain=0" in cap.text(), cap.text()


def test_the_ring_walk_path_is_guarded_too_not_just_the_baked_node():
    """THE BOTH-PATHS PIN. The guard sits after the two branches converge, so it must see
    whichever cell got chosen — not only a baked one.

    Today's walk cannot itself produce an out-of-domain cell: _find_offshore_point indexes
    the very axes the guard reads. So the walk BRANCH is driven with a stubbed
    _find_offshore_point returning a cell off the grid, which is exactly what a future
    walk resolving against a different dataset would hand over. Move the guard inside the
    baked-node branch and this test goes silent while everything else still passes.

        walk returns (35.24, -120.82) at ring 1, seaward
        spot (35.00, -120.90)          resolved (35.20, -120.82)
        node -> cell   4.447797065782 km = "4.45 km"
        spot -> cell  23.399584730493 km = "23.40 km"
    """
    spot = _spot("Stubbed Walker", 35.00, -120.90)   # no baked node -> the walk branch
    _, seen, cap = _run_fetch(
        [spot], "dom_walk_miss.json", _grid(),
        walk_stub=lambda datasets, lat, lng, **kw: (35.24, -120.82, 1, True))
    w = cap.warnings()
    assert seen == [(35.24, -120.82)], seen          # published, unchanged
    assert "Stubbed Walker" in w and "OUTSIDE" in w, w
    assert "node (35.24000, -120.82000)" in w, w
    assert "4.45 km from the node" in w, w
    assert "23.40 km from the spot" in w, w
    assert "out-of-domain=1" in cap.text(), cap.text()


# --------------------------------------------------------------------------- #
# 4 — the run summary                                                          #
# --------------------------------------------------------------------------- #
def test_the_run_summary_counts_and_names_every_out_of_domain_spot():
    """A bare count is easy to scroll past; these are names someone has to act on. Two
    out-of-domain spots and one clean one in a single run.

        Grover Beach       node (35.10, -120.70) -> cell (35.10, -120.74)  MISS
        Shell Beach North  node (35.05, -120.70) -> cell (35.05, -120.74)  MISS
        Avila Beach        node (35.15, -120.78)                           inside
    """
    spots = [
        _spot("Grover Beach", 35.10, -120.70, baked=(35.10, -120.70)),
        _spot("Avila Beach", 35.10, -120.82, baked=(35.15, -120.78)),
        _spot("Shell Beach North", 35.05, -120.70, baked=(35.05, -120.70)),
    ]
    result, seen, cap = _run_fetch(spots, "dom_summary.json", _grid())

    assert seen == [(35.10, -120.70), (35.15, -120.78), (35.05, -120.70)], seen
    assert set(result) == {"Grover Beach", "Avila Beach", "Shell Beach North"}, result.keys()
    assert "out-of-domain=2" in cap.text(), cap.text()
    w = cap.warnings()
    assert "have a node OUTSIDE the grid their nwps_wfo names" in w, w
    roll = w.split("have a node OUTSIDE the grid their nwps_wfo names")[1]
    assert "Grover Beach, Shell Beach North" in roll, roll   # named, sorted
    assert "Avila Beach" not in roll, roll                   # and the clean one is not


def test_the_summary_line_reports_zero_when_the_whole_run_is_clean():
    """The counter must appear on every run, not only on a bad one — a line that shows up
    only when something is wrong cannot be used to confirm nothing is."""
    spot = _spot("Fine", 35.10, -120.82, baked=(35.10, -120.82))
    _, _, cap = _run_fetch([spot], "dom_zero.json", _grid())
    assert "out-of-domain=0" in cap.text(), cap.text()
    assert "node OUTSIDE the grid" not in cap.warnings(), cap.warnings()


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} nwps-domain-guard checks passed")


if __name__ == "__main__":
    _run_all()
