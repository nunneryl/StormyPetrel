"""A tide station that produces nothing must say so, by name.

WHY THIS FILE EXISTS. A station could return zero rows on every hour and leave no trace in a
run log. Both steady states hide:

  * KNOWN-BAD  — skipped at the top of fetch(); named once, on the run that first marked it,
                 and forever after only a count in "%d known-no-predictions".
  * UNREACHABLE — _stale_entry with no cache emits empty series; logged at DEBUG only.

Either way the spots pointing at that station are silently tideless. Kalaloch Beach sat in
that state until it was found by hand in SQL. Nothing anywhere joined "this spot HAS a
station id" to "that station produced rows", which is the check that would have caught it on
day one.

THE SECOND FAILURE THIS PINS is drift between the roster and the station file. A spot's
nearest_tide_station_id is written by a PAST enrich run against the station file as it was
THEN; the file is a downloaded artifact (gitignored) refreshed independently. An id can stop
resolving with no code change and no error. The comparison is EXACT so that a dropped suffix
or a case difference — TWC0965 against TWC0965F — surfaces rather than being normalised away.

EVERY EXPECTED VALUE IS A LITERAL. None is produced by calling the function under test.

Run: python -m pipeline.tests.test_tide_station_visibility
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path

from pipeline.enrichment.tides import compute_nearest_tide_station
from pipeline.forecast import tides as T


class _Sandbox:
    """Point the tide stage's THREE write targets at a temp dir for the duration of a test.

    THIS WAS A DEFECT IN THIS FILE. The end-to-end test below called T.fetch() with the module
    paths untouched, and fetch()'s `finally` writes tides.json and _no_predictions.json
    unconditionally — `use_cache=False` governs READS, not writes. So running the suite
    overwrote a developer's real known-bad list with the test's fixture station, silently
    changing which stations the next pipeline run would skip. A test that mutates the state it
    is testing against is worse than no test. (No workflow runs this suite, so CI never did it;
    the damage was local, which is exactly why it went unnoticed.) Mirrors _redirect/_restore in
    test_tides_resilience.py."""

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = (T.TIDES_CACHE_DIR, T.TIDES_FORECAST_FILE, T._NO_PREDICTIONS_FILE,
                       T.NOAA_COOPS_MIN_INTERVAL_S)
        T.TIDES_CACHE_DIR = self.tmp / "cache"
        T.TIDES_FORECAST_FILE = self.tmp / "tides.json"
        T._NO_PREDICTIONS_FILE = self.tmp / "cache" / "_no_predictions.json"
        T.NOAA_COOPS_MIN_INTERVAL_S = 0.0
        return self

    def __exit__(self, *exc):
        (T.TIDES_CACHE_DIR, T.TIDES_FORECAST_FILE, T._NO_PREDICTIONS_FILE,
         T.NOAA_COOPS_MIN_INTERVAL_S) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False


class _CaptureLog(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _msgs(cap) -> str:
    return " ".join(r.getMessage() for r in cap.records)


def _spot(name, station_id=None):
    return {"name": name, "nearest_tide_station_id": station_id}


# --------------------------------------------------------------------------- #
# 1 — a station id survives assignment CHARACTER FOR CHARACTER                 #
# --------------------------------------------------------------------------- #

def test_a_suffixed_subordinate_id_survives_assignment_intact():
    """TWC0965F must come out of the assignment as TWC0965F.

    NOAA subordinate ids carry a trailing letter. Truncating one to TWC0965 produces an id
    that CO-OPS does not know, and every spot pointing at it goes tideless with no error —
    the request is well-formed, it just names a station that does not exist.

    The whole path is three verbatim passes and none of them may start normalising:
        geodata.load_tide_stations   "id": str(s["id"])
        tides.compute_nearest_tide_station   "nearest_tide_station_id": s["id"]
        forecast.tides._fetch_interval_once  {"station": station_id, ...}
    This pins the middle one, which is where the roster value is decided.
    """
    stations = [{"id": "TWC0965F", "lat": 47.60, "lng": -124.37, "name": "Kalaloch"}]
    got = compute_nearest_tide_station(
        {"lat": 47.5897, "lng": -124.3683}, stations=stations)
    assert got["nearest_tide_station_id"] == "TWC0965F", got
    # Spelled out so a truncation cannot pass by being "close enough".
    assert got["nearest_tide_station_id"] != "TWC0965"
    assert len(got["nearest_tide_station_id"]) == 8


def test_ids_of_every_shape_survive_intact():
    """The roster carries three shapes today — 7-digit numeric, AAA9999, and (per the bug
    report) AAA9999 + a letter. None may be altered: not case-folded, not stripped, not
    zero-padded. Each expected value is the input written out again by hand."""
    cases = [
        ("9410230", 32.87, -117.257),      # numeric NWLON
        ("TWC0419", 33.38, -117.590),      # subordinate, no suffix
        ("TWC0965F", 47.60, -124.370),     # subordinate, suffixed
        ("TEC3783A", 26.95, -82.350),      # east-coast subordinate, suffixed
        ("tpt2799", 21.00, -157.000),      # lower case must NOT be upper-cased
    ]
    for sid, lat, lng in cases:
        got = compute_nearest_tide_station(
            {"lat": lat, "lng": lng},
            stations=[{"id": sid, "lat": lat, "lng": lng, "name": "x"}])
        assert got["nearest_tide_station_id"] == sid, (sid, got)


# --------------------------------------------------------------------------- #
# 2 — a station that produced nothing is COUNTED and NAMED                     #
# --------------------------------------------------------------------------- #

def test_a_station_with_no_rows_is_reported_not_silently_empty():
    """Neither hilo nor hourly is the honest definition of "produced nothing" — not "the
    fetch raised", not "the cache was stale", but "the window we are about to hand
    downstream is empty"."""
    emitted = {
        "9410230": {"hilo": [{"t": "2026-09-04 03:00", "v": "1.2"}], "hourly": []},
        "TWC0965F": {"hilo": [], "hourly": []},
        "8720086": {"hilo": [], "hourly": [{"t": "2026-09-04 03:00", "v": "0.9"}]},
        "TEC3783A": {"hilo": [], "hourly": []},
    }
    requested = ["9410230", "TWC0965F", "8720086", "TEC3783A", "9999999"]
    # 9999999 was requested and never came back at all — skipped or abandoned — which is
    # the case a scan over `emitted` alone would silently miss.
    assert T.stations_with_no_rows(emitted, requested) == ["9999999", "TEC3783A", "TWC0965F"]


def test_hilo_only_is_NOT_no_rows():
    """A subordinate station serves hilo and no hourly curve. That is normal coverage —
    build_tide_series falls back to hilo — and must not be reported as a failure, or the
    warning cries wolf on 15 healthy stations every run."""
    assert T.stations_with_no_rows(
        {"TWC0419": {"hilo": [{"t": "x", "v": "1"}], "hourly": []}}, ["TWC0419"]) == []


def test_no_rows_handles_missing_keys_and_empty_input():
    assert T.stations_with_no_rows({}, []) == []
    assert T.stations_with_no_rows(None, None) == []
    assert T.stations_with_no_rows({"A": {}}, ["A"]) == ["A"]
    assert T.stations_with_no_rows({}, ["A"]) == ["A"], "requested but never emitted"
    # Emitted but never requested is NOT reported — it is not costing any spot a tide.
    assert T.stations_with_no_rows({"B": {"hilo": [], "hourly": []}}, []) == []


# --------------------------------------------------------------------------- #
# 3 — roster ids that match no station are reported, WITH their spots          #
# --------------------------------------------------------------------------- #

def test_an_unresolvable_id_names_the_station_and_every_spot_on_it():
    """The spots are the point. A station id means nothing to a reader; "Kalaloch Beach
    publishes no tide" is what gets acted on."""
    spots = [
        _spot("Kalaloch Beach", "TWC0965"),      # roster has the truncated form
        _spot("Ruby Beach", "TWC0965"),          # same broken id, second spot
        _spot("Trestles", "TWC0419"),            # resolves
        _spot("Nowhere", None),                  # no station at all — not a failure
    ]
    stations = [
        {"id": "TWC0965F", "lat": 47.6, "lng": -124.4},   # the FILE has the suffix
        {"id": "TWC0419", "lat": 33.4, "lng": -117.6},
    ]
    got = T.unresolvable_station_ids(spots, stations)
    assert got == {"TWC0965": ["Kalaloch Beach", "Ruby Beach"]}, got


def test_the_comparison_is_exact_so_a_case_difference_is_caught():
    """Normalising before comparing would hide exactly the class of bug this exists for."""
    got = T.unresolvable_station_ids(
        [_spot("A", "twc0419")], [{"id": "TWC0419", "lat": 1.0, "lng": 2.0}])
    assert got == {"twc0419": ["A"]}, got


def test_an_empty_station_list_reports_NOTHING_rather_than_the_whole_roster():
    """An absent station file means the file is missing, not that all 234 ids went bad. A
    warning naming the entire roster would be louder than the signal and would train the
    reader to ignore it."""
    spots = [_spot("A", "9410230"), _spot("B", "TWC0419")]
    assert T.unresolvable_station_ids(spots, []) == {}
    assert T.unresolvable_station_ids(spots, None) == {}


def test_a_fully_resolving_roster_reports_nothing():
    spots = [_spot("A", "9410230"), _spot("B", "TWC0965F")]
    stations = [{"id": "9410230", "lat": 1.0, "lng": 2.0},
                {"id": "TWC0965F", "lat": 3.0, "lng": 4.0}]
    assert T.unresolvable_station_ids(spots, stations) == {}


def test_a_numeric_id_compares_as_a_string_not_an_int():
    """The station file may hold ids as JSON numbers. 9410230 and "9410230" must resolve to
    each other, or every numeric station on the roster reports as broken."""
    got = T.unresolvable_station_ids(
        [_spot("A", 9410230)], [{"id": 9410230, "lat": 1.0, "lng": 2.0}])
    assert got == {}, got
    got2 = T.unresolvable_station_ids(
        [_spot("A", "9410230")], [{"id": 9410230, "lat": 1.0, "lng": 2.0}])
    assert got2 == {}, got2


# --------------------------------------------------------------------------- #
# 4 — the warning text itself                                                  #
# --------------------------------------------------------------------------- #

def test_the_named_list_is_capped_and_counts_the_remainder():
    """12 named, the rest counted. A warning that dumps 200 ids is scrolled past as fast as
    one that names none."""
    assert T._format_named(["a", "b", "c"]) == "a, b, c"
    fifteen = [f"s{i:02d}" for i in range(15)]
    out = T._format_named(fifteen)
    assert out.endswith("(+3 more)"), out
    assert out.startswith("s00, s01, s02"), out
    assert "s12" not in out, out
    assert T.MAX_NAMED_IN_WARNING == 12


def test_the_run_summary_warns_by_name_when_a_station_produced_nothing():
    """END TO END through fetch()'s reporting, with the network stubbed out. Pins that the
    warning fires, counts both stations and spots, and NAMES them — the whole point being
    that a count alone is not a symptom."""
    cap = _CaptureLog()
    log = logging.getLogger("pipeline.forecast.tides")
    log.addHandler(cap)
    saved_load = T.load_tide_stations
    try:
        # No station file (so the resolution check stands down), and every station fetch returns
        # empty — the shape of a station CO-OPS has nothing for. The sandbox is what keeps
        # fetch()'s unconditional `finally` write off the real cache directory.
        with _Sandbox():
            T.load_tide_stations = lambda: []
            spots = [_spot("Kalaloch Beach", "TWC0965F"), _spot("Ruby Beach", "TWC0965F")]
            T.fetch(spots, use_cache=True, _fetch_station=lambda *a, **k: ([], []))
    finally:
        T.load_tide_stations = saved_load
        log.removeHandler(cap)
    m = _msgs(cap)
    assert "returned NO rows" in m, m
    assert "TWC0965F" in m, m
    assert "Kalaloch Beach" in m, m
    assert "Ruby Beach" in m, m
    # Counted, not just listed: one station, two spots.
    assert "1 station(s)" in m, m
    assert "2 spot(s)" in m, m


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\ntest_tide_station_visibility: {len(fns)} PASS")


if __name__ == "__main__":
    _run_all()
