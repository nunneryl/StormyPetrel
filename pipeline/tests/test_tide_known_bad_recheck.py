"""A station is not marked known-bad while NOAA still serves it over a short horizon.

THE INCIDENT. The 2026-10-09 10:47Z full-pipeline run marked 8665387 (Hamlin Creek, Isle of
Palms) known-bad on one interval: hilo over its own 25-30 day horizon, every datum answering
"No Predictions data was found. Please make sure the Datum input is valid." (hourly was not
asked; the station was cached hilo-only). Five spots then published no tide for three weeks.
From the same runner on 2026-10-30 (sw1-raycast run 63) the 72 h hilo request on MLLW returned
11 high/low tides.

THE RULE (forecast/tides._recheck_short_horizon). Before marking, ask again over the 7-day
output window (168 h), then over TIDE_KNOWN_BAD_PROBE_HOURS (72 h):
  * rows over 168 h  -> served, cached, refetched for the full horizon next run; not marked;
  * rows over 72 h   -> too short to serve, so the stale/no-data path; not marked;
  * nothing anywhere -> marked, as before.

NOAA is stubbed at get_once, so each test sees the exact requests the stage sends: interval,
datum and range, in order. Every expected list and count is written out by hand.

Run: python -m pipeline.tests.test_tide_known_bad_recheck   (or pytest)
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from datetime import date, timedelta
from pathlib import Path

from pipeline.forecast import tides

NO_PREDICTIONS = {"error": {"message": "No Predictions data was found. Please make sure the "
                                      "Datum input is valid."}}


class _Resp:
    def __init__(self, status, payload):
        self.status_code = status
        self._p = payload
        self.text = ""

    def json(self):
        return self._p


def _hilo_rows(days):
    today = date.today()
    return [{"t": (today + timedelta(days=k)).strftime("%Y-%m-%d 06:00"), "v": "2.5", "type": "H"}
            for k in range(days)]


class _Noaa:
    """get_once stand-in. *answers* maps (interval, datum, range) to a _Resp; anything else is
    NOAA's genuine no-predictions answer. Every request is recorded as (interval, datum, range)."""

    def __init__(self, answers=None):
        self.answers = answers or {}
        self.calls = []

    def __call__(self, url, params=None, **kw):
        key = (params["interval"], params["datum"], params["range"])
        self.calls.append(key)
        return self.answers.get(key, _Resp(200, NO_PREDICTIONS))


class _Sandbox:
    """Temp cache / output / known-bad paths, no pacing sleep, get_once restored on exit."""

    def __init__(self, noaa):
        self.noaa = noaa

    def __enter__(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.saved = (tides.TIDES_CACHE_DIR, tides.TIDES_FORECAST_FILE, tides._NO_PREDICTIONS_FILE,
                      tides.get_once, tides.NOAA_COOPS_MIN_INTERVAL_S)
        tides.TIDES_CACHE_DIR = self.tmp / "cache"
        tides.TIDES_FORECAST_FILE = self.tmp / "tides.json"
        tides._NO_PREDICTIONS_FILE = self.tmp / "cache" / "_no_predictions.json"
        tides.NOAA_COOPS_MIN_INTERVAL_S = 0.0
        tides.get_once = self.noaa
        return self

    def __exit__(self, *exc):
        (tides.TIDES_CACHE_DIR, tides.TIDES_FORECAST_FILE, tides._NO_PREDICTIONS_FILE,
         tides.get_once, tides.NOAA_COOPS_MIN_INTERVAL_S) = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)
        return False


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__(logging.DEBUG)
        self.records = []

    def emit(self, record):
        self.records.append(record.getMessage())


def _run(station, cache=None, noaa=None):
    cap = _Capture()
    log = logging.getLogger("pipeline.forecast.tides")
    level = log.level
    log.addHandler(cap)
    log.setLevel(logging.INFO)
    try:
        with _Sandbox(noaa):
            if cache is not None:
                tides._save_cache(station, cache)
            out = tides.fetch([{"name": "Isle of Palms", "nearest_tide_station_id": station}],
                              use_cache=True)
            known_bad = tides._load_no_predictions()
            cached = tides._load_cache(station)
    finally:
        log.removeHandler(cap)
        log.setLevel(level)
    return out, known_bad, cached, "\n".join(cap.records)


def _long(calls):
    """The station's own horizon, read from its first request; 600-720 h by construction."""
    h = calls[0][2]
    assert 600 <= h <= 720 and h not in (168, 72), h
    return h


def _hilo_only_lapsed_cache(station):
    """A cache like 8665387's on 2026-10-09: hilo-only, its horizon run out."""
    return {"station_id": station, "fetched_at": "2026-09-01T00:00:00+00:00",
            "covers_from": "2026-09-01", "covers_until": (date.today() - timedelta(days=1)).isoformat(),
            "hilo_only": True, "hilo": [], "hourly": []}


NO_DATA = {"station_id": "8665387", "fetched_at": None, "asof": None, "stale": True,
           "hilo": [], "hourly": []}


# --------------------------------------------------------------------------- #
# 1. The incident: hilo-only, nothing over the long horizon, rows over 72 h     #
# --------------------------------------------------------------------------- #

def test_the_2026_10_09_path_is_not_marked_when_72_h_answers():
    noaa = _Noaa({("hilo", "MLLW", 72): _Resp(200, {"predictions": _hilo_rows(3)})})
    out, known_bad, _cached, logs = _run("8665387", _hilo_only_lapsed_cache("8665387"), noaa)
    H = _long(noaa.calls)
    # hilo only (the cache says hilo-only), MLLW first, the long horizon, then 168 h, then 72 h,
    # where MLLW answers and the datum loop stops.
    assert noaa.calls == [("hilo", "MLLW", H), ("hilo", "STND", H), ("hilo", "MSL", H),
                          ("hilo", "MLLW", 168), ("hilo", "STND", 168), ("hilo", "MSL", 168),
                          ("hilo", "MLLW", 72)], noaa.calls
    assert known_bad == set(), known_bad
    # 72 h is shorter than the 7-day window, so nothing is served: the lapsed cache gives no data.
    assert out == {"8665387": NO_DATA}, out
    assert "NOT marking known-bad" in logs and "3 hilo rows over 72h" in logs, logs
    assert "marking known-bad" not in logs.replace("NOT marking known-bad", ""), logs


def test_a_cold_station_also_asks_hourly_at_each_horizon_before_the_72_h_answer():
    noaa = _Noaa({("hilo", "MLLW", 72): _Resp(200, {"predictions": _hilo_rows(3)})})
    out, known_bad, _cached, _logs = _run("8665387", None, noaa)
    H = _long(noaa.calls)
    assert noaa.calls == [("hilo", "MLLW", H), ("hilo", "STND", H), ("hilo", "MSL", H),
                          ("h", "MLLW", H), ("h", "STND", H), ("h", "MSL", H),
                          ("hilo", "MLLW", 168), ("hilo", "STND", 168), ("hilo", "MSL", 168),
                          ("h", "MLLW", 168), ("h", "STND", 168), ("h", "MSL", 168),
                          ("hilo", "MLLW", 72),
                          ("h", "MLLW", 72), ("h", "STND", 72), ("h", "MSL", 72)], noaa.calls
    assert len(noaa.calls) == 16
    assert known_bad == set(), known_bad
    assert out == {"8665387": NO_DATA}, out


# --------------------------------------------------------------------------- #
# 2. Rows over the 7-day window: served and cached, not marked                  #
# --------------------------------------------------------------------------- #

def test_rows_over_168_h_are_served_and_cached():
    rows = _hilo_rows(8)                     # today .. today+7, one high tide a day at 06:00
    noaa = _Noaa({("hilo", "MLLW", 168): _Resp(200, {"predictions": rows})})
    out, known_bad, cached, logs = _run("8665387", _hilo_only_lapsed_cache("8665387"), noaa)
    H = _long(noaa.calls)
    assert noaa.calls == [("hilo", "MLLW", H), ("hilo", "STND", H), ("hilo", "MSL", H),
                          ("hilo", "MLLW", 168)], noaa.calls
    assert known_bad == set(), known_bad
    entry = out["8665387"]
    assert entry["stale"] is False and entry["hourly"] == [], entry
    # The output keeps [today, today+7): days 0..6, seven of the eight rows.
    assert entry["hilo"] == rows[:7], entry["hilo"]
    # Coverage is measured against the 168 h asked for: clamped to today + 7 days.
    assert cached["covers_until"] == (date.today() + timedelta(days=7)).isoformat(), cached
    assert cached["hilo_only"] is True and cached["hilo"] == rows, cached
    assert "serving those and asking for the full horizon again next run" in logs, logs
    assert "covers only through" not in logs, logs      # no shortfall against what was asked


def test_hourly_rows_alone_over_168_h_also_count():
    rows = [{"t": (date.today() + timedelta(hours=h)).strftime("%Y-%m-%d %H:%M"), "v": "1.0"}
            for h in range(0, 169, 24)]      # 8 rows, today .. today+7 at 00:00
    noaa = _Noaa({("h", "MLLW", 168): _Resp(200, {"predictions": rows})})
    out, known_bad, cached, _logs = _run("8665387", None, noaa)
    assert known_bad == set(), known_bad
    assert out["8665387"]["stale"] is False and out["8665387"]["hilo"] == [], out
    assert out["8665387"]["hourly"] == rows[:7], out["8665387"]["hourly"]
    assert cached["hilo_only"] is False, cached


# --------------------------------------------------------------------------- #
# 3. Nothing anywhere: still marked                                            #
# --------------------------------------------------------------------------- #

def test_nothing_over_any_horizon_is_still_marked_known_bad():
    noaa = _Noaa()
    out, known_bad, _cached, logs = _run("8665387", None, noaa)
    H = _long(noaa.calls)
    assert len(noaa.calls) == 18, noaa.calls     # 3 horizons x 2 intervals x 3 datums
    assert [c[2] for c in noaa.calls] == [H] * 6 + [168] * 6 + [72] * 6, noaa.calls
    assert known_bad == {"8665387"}, known_bad
    assert out == {}, out
    assert f"over {H}h, 168h and 72h — marking known-bad" in logs, logs


def test_a_hilo_only_station_with_nothing_anywhere_is_marked_after_nine_requests():
    noaa = _Noaa()
    _out, known_bad, _cached, _logs = _run("8665387", _hilo_only_lapsed_cache("8665387"), noaa)
    assert [c[0] for c in noaa.calls] == ["hilo"] * 9, noaa.calls
    assert known_bad == {"8665387"}, known_bad


# --------------------------------------------------------------------------- #
# 4. An outage during the re-check is an outage, never a mark                   #
# --------------------------------------------------------------------------- #

def test_a_throttle_during_the_recheck_never_marks():
    noaa = _Noaa({("hilo", "MLLW", 168): _Resp(503, {})})
    out, known_bad, _cached, _logs = _run("8665387", _hilo_only_lapsed_cache("8665387"), noaa)
    H = _long(noaa.calls)
    assert noaa.calls == [("hilo", "MLLW", H), ("hilo", "STND", H), ("hilo", "MSL", H),
                          ("hilo", "MLLW", 168)], noaa.calls
    assert known_bad == set(), known_bad
    assert out == {"8665387": NO_DATA}, out


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\n{len(fns)} passed")


if __name__ == "__main__":
    _run_all()
