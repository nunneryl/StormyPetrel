"""An expired tide cache must NOT be re-served, and every station must land in exactly one bucket.

TWO DEFECTS, ONE RUN. A station whose cache had lapsed and whose refetch failed was served its
own expired predictions, marked `stale: true`, and counted nowhere.

  THE RE-ADOPT. _stale_entry was `if cache: return _emit(...)` — any non-None cache was
  re-adopted with no date check. Coverage is tested exactly once, at the (B) gate, against
  refetch_until; a station that falls THROUGH that gate and then fails its refetch (breaker,
  outage, stage deadline, coverage anomaly) reached _stale_entry, where covers_until was never
  consulted again. TWC0965F: cached 2026-07-30, covering to 2026-08-27, re-served every run
  through September.

  THE PARTIAL WINDOW is the case that makes this more than cosmetic, and it is pinned here
  (test_a_cache_covering_only_part_of_the_window_is_treated_as_missing). A cache that has lapsed
  PART-WAY into the 7-day window slices to a non-empty series with a plausible asof, so nothing
  flags it — and downstream that is not "some days missing": build_tide_series takes min/max
  from the rows present, so a 4-day series rescales tide_norm for the days it DOES cover.

  THE COUNTERS. n_live/n_cache/n_stale were incremented at the branches that happened to have an
  increment; the genuine-no-predictions branch and the known-bad skip had none, and n_hilo_only
  was an attribute of stations already in n_live. The three printed buckets summed to 233 over
  234 stations. _Partition makes the partition exhaustive by construction and checkable by
  arithmetic on one line.

EVERY EXPECTED VALUE IS A LITERAL — every fixture date, every count, every bucket name is
written out by hand. Nothing here is produced by calling the function under test, and in
particular no expected count is read back from _Partition to check _Partition.

NO TEST TOUCHES A REAL CACHE FILE: _Sandbox redirects TIDES_CACHE_DIR, TIDES_FORECAST_FILE and
_NO_PREDICTIONS_FILE, because fetch()'s `finally` writes the last two unconditionally.

Run: python -m pipeline.tests.test_tide_cache_expiry
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

from pipeline.forecast import tides as T


# --------------------------------------------------------------------------- #
# fixtures                                                                     #
# --------------------------------------------------------------------------- #
class _Sandbox:
    """Redirect the stage's three write targets. See test_tides_resilience._redirect."""

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._saved = (T.TIDES_CACHE_DIR, T.TIDES_FORECAST_FILE, T._NO_PREDICTIONS_FILE,
                       T.NOAA_COOPS_MIN_INTERVAL_S, T.load_tide_stations)
        T.TIDES_CACHE_DIR = self.tmp / "cache"
        T.TIDES_FORECAST_FILE = self.tmp / "tides.json"
        T._NO_PREDICTIONS_FILE = self.tmp / "cache" / "_no_predictions.json"
        T.NOAA_COOPS_MIN_INTERVAL_S = 0.0
        T.load_tide_stations = lambda: []          # roster-resolution check stands down
        T.TIDES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        return self

    def write_cache(self, station_id: str, entry: dict) -> None:
        (T.TIDES_CACHE_DIR / f"{station_id}.json").write_text(json.dumps(entry))

    def __exit__(self, *exc):
        (T.TIDES_CACHE_DIR, T.TIDES_FORECAST_FILE, T._NO_PREDICTIONS_FILE,
         T.NOAA_COOPS_MIN_INTERVAL_S, T.load_tide_stations) = self._saved
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False


class _CaptureLog(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


def _capture():
    """Capture the stage's log. The level MUST be lowered: the run summary is at INFO and the
    default root level is WARNING, so a handler alone sees only the warnings — which would make
    every assertion about the summary line vacuously unreachable rather than failing loudly."""
    cap = _CaptureLog()
    lg = logging.getLogger("pipeline.forecast.tides")
    cap.saved_level = lg.level
    lg.addHandler(cap)
    lg.setLevel(logging.INFO)
    return cap


def _release(cap):
    lg = logging.getLogger("pipeline.forecast.tides")
    lg.removeHandler(cap)
    lg.setLevel(cap.saved_level)


def _msgs(cap) -> str:
    return " ".join(r.getMessage() for r in cap.records)


def _one(cap, needle: str) -> str:
    """The SINGLE captured record containing *needle*.

    WHY NOT JUST SEARCH THE JOINED BLOB: two warnings fire for the same failing station — the
    stale-serve one and the broader "returned NO rows" one — and both name the spots. Asserting
    against the concatenation therefore cannot tell which warning carried them, and a mutant
    that stripped the spot names out of the stale-serve line passed (M11). Assertions about a
    specific message must be made against that message alone."""
    hits = [r.getMessage() for r in cap.records if needle in r.getMessage()]
    assert len(hits) == 1, f"expected exactly one record containing {needle!r}, got {len(hits)}"
    return hits[0]


def _spot(name, station_id):
    return {"name": name, "nearest_tide_station_id": station_id}


def _rows(first: date, n_days: int) -> list[dict]:
    """One prediction row per day at 03:00, starting at *first*. Values are irrelevant to every
    assertion here — only the dates are read (by _slice and _coverage_from_series)."""
    return [{"t": (first + timedelta(days=i)).strftime("%Y-%m-%d 03:00"), "v": "3.0"}
            for i in range(n_days)]


# The window every _stale_entry test below uses, written out rather than derived from a clock:
#   start 2026-09-05, end 2026-09-12 — half-open, so the last published day is 2026-09-11.
WIN_START = date(2026, 9, 5)
WIN_END = date(2026, 9, 12)


# --------------------------------------------------------------------------- #
# 1 — AN EXPIRED CACHE IS NOT RE-ADOPTED                                       #
# --------------------------------------------------------------------------- #

def test_the_window_fixture_is_what_it_says_it_is():
    """Guards the fixture itself: if these two dates drift, every literal below is meaningless."""
    assert WIN_START.isoformat() == "2026-09-05"
    assert WIN_END.isoformat() == "2026-09-12"
    assert (WIN_END - WIN_START).days == 7


def test_an_expired_cache_is_NOT_re_adopted():
    """TWC0965F's exact shape: fetched in July, covering to 2026-08-27, asked for September.

    The whole entry is asserted as a literal dict — not just `hilo == []` — because the
    distinguishing field is `asof`. Under the bug the series came back empty ANYWAY (every row
    predates the window, so _slice dropped them all) and the entry still carried
    `asof: 2026-07-30...`, which reads downstream as "we have data, it is just old" rather than
    "we have nothing for these dates". Asserting only the series would have passed on the bug.
    """
    cache = {
        "station_id": "TWC0965F",
        "fetched_at": "2026-07-30T18:04:11.221000+00:00",
        "covers_from": "2026-07-30",
        "covers_until": "2026-08-27",
        "hilo_only": True,
        "hilo": _rows(date(2026, 8, 20), 8),     # 2026-08-20 .. 2026-08-27
        "hourly": [],
    }
    assert T._stale_entry("TWC0965F", cache, WIN_START, WIN_END) == {
        "station_id": "TWC0965F",
        "fetched_at": None,
        "asof": None,
        "stale": True,
        "hilo": [],
        "hourly": [],
    }


def test_a_cache_covering_only_part_of_the_window_is_treated_as_missing():
    """THE CASE THAT WOULD NOT HAVE BEEN CAUGHT BY AN EMPTINESS CHECK.

    covers_until 2026-09-08 against a window ending 2026-09-12: four days of real, in-window
    predictions. The old code sliced them out and emitted them — non-empty series, plausible
    asof, and stations_with_no_rows does not flag it because hilo is non-empty. So it looked
    healthier than a station that returned nothing, while feeding interpret a 4-day min/max to
    normalise 7 days against. All-or-nothing: the four good days are discarded on purpose.
    """
    cache = {
        "station_id": "9410230",
        "fetched_at": "2026-09-01T00:00:00+00:00",
        "covers_until": "2026-09-08",
        "hilo": _rows(date(2026, 9, 5), 4),      # 09-05, 09-06, 09-07, 09-08 — all IN the window
        "hourly": [],
    }
    # Sanity on the fixture: those rows really are inside [2026-09-05, 2026-09-12), so a slice
    # would have returned all four. This is what the old code published.
    assert len(T._slice(cache["hilo"], WIN_START, WIN_END)) == 4
    got = T._stale_entry("9410230", cache, WIN_START, WIN_END)
    assert got["hilo"] == [], "a partially-covering cache must not be served"
    assert got["asof"] is None
    assert got["stale"] is True


def test_a_cache_that_still_covers_the_window_IS_served_and_still_sliced():
    """The fix must not turn every stale serve into a no-data. A cache covering past the window
    end is served, marked stale, and STILL sliced to the 7 days — rows outside are dropped."""
    cache = {
        "station_id": "9410230",
        "fetched_at": "2026-09-02T00:00:00+00:00",
        "covers_until": "2026-09-30",
        "hilo": [
            {"t": "2026-09-04 03:00", "v": "1.0"},   # before the window — dropped
            {"t": "2026-09-05 03:00", "v": "2.0"},   # in
            {"t": "2026-09-11 03:00", "v": "3.0"},   # in (last published day)
            {"t": "2026-09-12 03:00", "v": "4.0"},   # ON the exclusive end — dropped
            {"t": "2026-09-20 03:00", "v": "5.0"},   # after — dropped
        ],
        "hourly": [],
    }
    got = T._stale_entry("9410230", cache, WIN_START, WIN_END)
    assert got["stale"] is True
    assert got["asof"] == "2026-09-02T00:00:00+00:00"
    assert [r["v"] for r in got["hilo"]] == ["2.0", "3.0"]


def test_no_cache_at_all_is_the_same_no_data_state():
    """Absent and expired must be indistinguishable downstream — both are "no tides for these
    dates". A separate shape for each would give db_import two things to handle for one fact."""
    assert T._stale_entry("9999999", None, WIN_START, WIN_END) == {
        "station_id": "9999999", "fetched_at": None, "asof": None,
        "stale": True, "hilo": [], "hourly": [],
    }
    assert T._stale_entry("9999999", {}, WIN_START, WIN_END)["asof"] is None


def test_a_garbled_covers_until_is_treated_as_expired():
    """Unparseable coverage is not evidence of coverage. _cache_covers already returns False for
    these; this pins that _stale_entry inherits that rather than falling back to `if cache:`."""
    for bad in ("", None, "not-a-date", "2026-13-99", 20260908):
        cache = {"covers_until": bad, "fetched_at": "2026-09-01T00:00:00+00:00",
                 "hilo": _rows(date(2026, 9, 5), 3), "hourly": []}
        assert T._stale_entry("x", cache, WIN_START, WIN_END)["hilo"] == [], f"covers_until={bad!r}"
    # And a cache with no covers_until key at all (a pre-versioning cache file).
    assert T._stale_entry("x", {"hilo": _rows(date(2026, 9, 5), 3)},
                          WIN_START, WIN_END)["hilo"] == []


def test_coverage_exactly_at_the_window_end_is_served():
    """The boundary, stated. covers_until == end_date passes (>=), one day short does not. The
    check is deliberately conservative by a day against _slice's half-open range — a cache
    ending 2026-09-11 does cover every published row but is still rejected, and erring toward
    not publishing a truncated series is the intended direction."""
    def _entry(cu):
        return T._stale_entry("x", {"covers_until": cu, "fetched_at": "2026-09-01T00:00:00+00:00",
                                    "hilo": _rows(date(2026, 9, 5), 7), "hourly": []},
                              WIN_START, WIN_END)
    assert _entry("2026-09-12")["hilo"] != [], "covers_until == end_date is covered"
    assert _entry("2026-09-13")["hilo"] != []
    assert _entry("2026-09-11")["hilo"] == [], "one day short is rejected (conservative by design)"


# --------------------------------------------------------------------------- #
# 2 — THE COUNTERS PARTITION THE STATION LIST                                  #
# --------------------------------------------------------------------------- #

def test_the_partition_is_exhaustive_and_disjoint():
    p = T._Partition(["a", "b", "c"])
    assert p.uncounted() == ["a", "b", "c"], "nothing claimed yet"
    assert p.balanced() is False, "an unclaimed station is NOT balanced"
    p.put("a", "live")
    p.put("b", "cached")
    assert p.uncounted() == ["c"]
    assert p.balanced() is False
    p.put("c", "no-predictions")
    assert p.uncounted() == []
    assert p.total() == 3
    assert p.balanced() is True
    assert p.counts() == {"live": 1, "cached": 1, "stale": 0, "no-data": 0,
                          "no-predictions": 1, "known-bad-skipped": 0, "uncounted": 0}


def test_re_bucketing_a_station_is_recorded_not_silently_overwritten():
    """Two branches claiming one station is a control-flow bug — the counts would still sum, so
    only the record of the conflict can show it."""
    p = T._Partition(["a"])
    p.put("a", "live")
    p.put("a", "stale")
    assert p.counts()["live"] == 0 and p.counts()["stale"] == 1, "last write wins for the count"
    assert p.double_assigned == ["a:live->stale"]
    assert p.balanced() is False, "a conflict is an imbalance even though the sum is right"
    # Re-asserting the SAME bucket is not a conflict (idempotent).
    q = T._Partition(["a"])
    q.put("a", "live")
    q.put("a", "live")
    assert q.double_assigned == [] and q.balanced() is True


def test_a_station_in_no_bucket_is_counted_as_uncounted_and_the_sum_still_holds():
    """THE REGRESSION, in miniature: 3 stations, one taking a path with no counter. The old
    accounting printed 2 and left the reader to notice. Here the sum still equals the total and
    the missing one is named."""
    p = T._Partition(["a", "b", "c"])
    p.put("a", "cached")
    p.put("b", "cached")
    assert p.counts()["uncounted"] == 1
    assert p.total() == 3, "the printed parts still add up to the station total"
    assert p.uncounted() == ["c"]
    assert p.balanced() is False
    assert "MISMATCH" in p.render()
    assert "3 stations = " in p.render()
    assert "cached 2" in p.render()
    assert "uncounted 1" in p.render()


def test_the_rendered_line_carries_the_denominator_and_a_verdict():
    p = T._Partition(["a", "b"])
    p.put("a", "live")
    p.put("b", "no-data")
    r = p.render()
    assert r == ("2 stations = live 1 + cached 0 + stale 0 + no-data 1 + no-predictions 0 + "
                 "known-bad-skipped 0 + uncounted 0 [sum 2, balanced]"), r


def test_an_empty_run_is_balanced():
    p = T._Partition([])
    assert p.balanced() is True and p.total() == 0
    assert "0 stations = " in p.render()


# --------------------------------------------------------------------------- #
# 3 — END TO END through fetch(): every path, counted; the stale serve, named   #
# --------------------------------------------------------------------------- #
# Fixture stations, one per terminal bucket. Dates are relative to the real clock because
# fetch() reads date.today(); the BUCKET each lands in is what is asserted, and every expected
# count below is a literal.
_TODAY = date.today()
_LONG = (_TODAY + timedelta(days=60)).isoformat()      # comfortably past refetch_until
_LAPSED = (_TODAY - timedelta(days=9)).isoformat()     # covers_until in the PAST


def _cache(covers_until, *, hilo_only=False, first=None, n=30):
    return {"fetched_at": "2026-07-30T18:04:11+00:00", "covers_until": covers_until,
            "hilo_only": hilo_only, "hilo": _rows(first or _TODAY, n), "hourly": []}


def test_every_path_is_counted_and_the_buckets_sum_to_the_station_total():
    """Five stations, five different terminal paths, including THE HILO-ONLY STATION WHOSE
    REFETCH FAILED — the shape that produced `live=0, cached=233, stale/missing=0` over 234."""
    cap = _capture()
    seen_skip_hourly = {}

    def _fake(sid, begin, horizon_h, pacer, skip_hourly):
        seen_skip_hourly[sid] = skip_hourly
        if sid == "S_LIVE":
            return _rows(_TODAY, 30), []
        if sid == "S_HILO_FAIL":
            raise T._TideOutage("simulated CO-OPS outage")
        if sid == "S_NOPRED":
            return [], []
        raise AssertionError(f"unexpected fetch for {sid}")

    try:
        with _Sandbox() as box:
            box.write_cache("S_CACHED", _cache(_LONG))
            box.write_cache("S_HILO_FAIL", _cache(_LAPSED, hilo_only=True,
                                                  first=_TODAY - timedelta(days=39)))
            T._save_no_predictions({"S_KNOWNBAD": "2026-09-01T00:00:00+00:00"})
            spots = [_spot("Cached Spot", "S_CACHED"), _spot("Live Spot", "S_LIVE"),
                     _spot("Kalaloch Beach", "S_HILO_FAIL"), _spot("Ruby Beach", "S_HILO_FAIL"),
                     _spot("Nopred Spot", "S_NOPRED"), _spot("Knownbad Spot", "S_KNOWNBAD")]
            out = T.fetch(spots, use_cache=True, _fetch_station=_fake)
    finally:
        _release(cap)
    m = _msgs(cap)

    # 5 unique stations, one in each bucket. Every number here is written by hand.
    assert ("5 stations = live 1 + cached 1 + stale 0 + no-data 1 + no-predictions 1 + "
            "known-bad-skipped 1 + uncounted 0 [sum 5, balanced]") in m, m
    assert "RUN ACCOUNTING BROKEN" not in m, m
    # The two buckets that produce no output are exactly the difference from len(out).
    assert len(out) == 3, sorted(out)
    assert "wrote 3 entries" in m and "the 2 not written" in m, m
    # The hilo_only cache flag was honoured on the failed station (it still skips hourly) — the
    # detail that made this station's path different in the first place.
    assert seen_skip_hourly == {"S_LIVE": False, "S_HILO_FAIL": True, "S_NOPRED": False}, \
        seen_skip_hourly
    # The failed hilo-only station is NOT served its lapsed cache.
    assert out["S_HILO_FAIL"] == {"station_id": "S_HILO_FAIL", "fetched_at": None, "asof": None,
                                  "stale": True, "hilo": [], "hourly": []}, out["S_HILO_FAIL"]


def test_the_stale_serve_is_named_with_its_station_its_expiry_and_its_spots():
    """A station with an expired cache and a failed refetch must be LOUD — station, the date its
    cache ran out, and the spots that go tideless — like the roster-resolution warning."""
    cap = _capture()

    def _fake(sid, begin, horizon_h, pacer, skip_hourly):
        raise T._TideOutage("simulated CO-OPS outage")

    try:
        with _Sandbox() as box:
            box.write_cache("S_HILO_FAIL", _cache(_LAPSED, hilo_only=True,
                                                  first=_TODAY - timedelta(days=39)))
            T.fetch([_spot("Kalaloch Beach", "S_HILO_FAIL"), _spot("Ruby Beach", "S_HILO_FAIL")],
                    use_cache=True, _fetch_station=_fake)
    finally:
        _release(cap)
    # Asserted against THE STALE-SERVE RECORD ITSELF, not the joined log: the broader
    # "returned NO rows" warning fires for this same station and also names the spots, so a
    # search over the concatenation cannot tell which line carried what.
    w = _one(cap, "NO usable predictions")
    assert f"S_HILO_FAIL (cache covered to {_LAPSED})" in w, w
    assert "Kalaloch Beach" in w and "Ruby Beach" in w, w
    assert "1 station(s)" in w and "2 spot(s) publish no tide" in w, w
    assert "1 stations = live 0 + cached 0 + stale 0 + no-data 1" in _msgs(cap), _msgs(cap)


def test_a_hilo_only_station_with_a_SUCCESSFUL_fetch_is_unaffected():
    """The control. Same station shape — subordinate, hilo-only, lapsed cache — but CO-OPS
    answers. It must refetch, count as `live`, publish non-stale hilo rows, and appear in NO
    warning. If the fix made this station loud it would cry wolf on 106 healthy subordinates."""
    cap = _capture()
    try:
        with _Sandbox() as box:
            box.write_cache("S_HILO_OK", _cache(_LAPSED, hilo_only=True,
                                                first=_TODAY - timedelta(days=39)))
            out = T.fetch([_spot("Trestles", "S_HILO_OK")], use_cache=True,
                          _fetch_station=lambda *a, **k: (_rows(_TODAY, 30), []))
    finally:
        _release(cap)
    m = _msgs(cap)
    assert out["S_HILO_OK"]["stale"] is False
    assert out["S_HILO_OK"]["hourly"] == [], "still hilo-only — no hourly curve invented"
    # 7-day slice of one row per day from today: today .. today+6 inclusive.
    assert len(out["S_HILO_OK"]["hilo"]) == 7, out["S_HILO_OK"]["hilo"]
    assert "1 stations = live 1 + cached 0 + stale 0 + no-data 0" in m, m
    assert "hilo-only 1 of live" in m, m
    assert "NO usable predictions" not in m, m
    assert "returned NO rows" not in m, m
    assert "RUN ACCOUNTING BROKEN" not in m, m


def test_the_deadline_path_separates_a_good_cache_from_a_lapsed_one():
    """The stage deadline abandons the remaining stations WITHOUT evaluating the (B) gate, so it
    is the one path that can produce both buckets. A still-covering cache is `stale` (served,
    just not refreshed); a lapsed one is `no-data` (nothing to serve). Same branch, different
    verdict — which is the distinction the old single n_stale counter erased."""
    cap = _capture()
    saved_deadline = T.TIDE_STAGE_DEADLINE_S
    try:
        with _Sandbox() as box:
            T.TIDE_STAGE_DEADLINE_S = -1.0        # every station is past the deadline
            box.write_cache("S_GOOD", _cache(_LONG))
            box.write_cache("S_LAPSED", _cache(_LAPSED, first=_TODAY - timedelta(days=39)))
            out = T.fetch([_spot("Good Spot", "S_GOOD"), _spot("Lapsed Spot", "S_LAPSED")],
                          use_cache=True,
                          _fetch_station=lambda *a, **k: (_ for _ in ()).throw(
                              AssertionError("deadline must not touch the network")))
    finally:
        T.TIDE_STAGE_DEADLINE_S = saved_deadline
        _release(cap)
    m = _msgs(cap)
    assert "2 stations = live 0 + cached 0 + stale 1 + no-data 1" in m, m
    assert out["S_GOOD"]["stale"] is True and len(out["S_GOOD"]["hilo"]) == 7
    assert out["S_LAPSED"]["hilo"] == [] and out["S_LAPSED"]["asof"] is None
    # Only the lapsed one is named loud; the good one is not cried wolf about.
    assert "Lapsed Spot" in m and "S_LAPSED" in m, m
    assert "S_GOOD (cache covered to" not in m, m


def test_a_station_that_reaches_no_bucket_is_named_at_ERROR_in_a_real_run():
    """THE LINE THAT WOULD HAVE MADE THE MISSING STATION VISIBLE, exercised end to end.

    _Partition.balanced() is unit-tested above, but that proves nothing about fetch() actually
    CHECKING it — a mutant that deleted the `if not part.balanced()` block survived the unit
    tests untouched. So here a branch is made to take no counter (the exact regression: a code
    path that increments nothing) by substituting a _Partition that ignores one station, and the
    run must report the imbalance and NAME the station rather than quietly printing a short sum.
    """
    class _Leaky(T._Partition):
        def put(self, station_id, bucket):
            if str(station_id) == "S_LOST":
                return                      # stand-in for a branch with no counter
            super().put(station_id, bucket)

    cap = _capture()
    saved = T._Partition
    try:
        with _Sandbox() as box:
            T._Partition = _Leaky
            box.write_cache("S_KEPT", _cache(_LONG))
            box.write_cache("S_LOST", _cache(_LONG))
            T.fetch([_spot("Kept Spot", "S_KEPT"), _spot("Lost Spot", "S_LOST")],
                    use_cache=True,
                    _fetch_station=lambda *a, **k: (_ for _ in ()).throw(
                        AssertionError("both caches are fresh; no fetch expected")))
    finally:
        T._Partition = saved
        _release(cap)
    err = _one(cap, "RUN ACCOUNTING BROKEN")
    assert "Unaccounted: S_LOST" in err, err
    assert "2 stations = " in err and "uncounted 1" in err and "MISMATCH" in err, err
    assert "S_KEPT" not in err.split("Unaccounted:")[1], err
    # And the normal summary still printed — the error supplements it, not replaces it.
    assert "cached 1" in _one(cap, "wrote 2 entries")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\ntest_tide_cache_expiry: {len(fns)} PASS")


if __name__ == "__main__":
    _run_all()
