"""Acceptance test for the resilient tide stage — a dead CO-OPS backend must not block the pipeline.

Simulates a TOTAL outage (get_once always fails) against a COLD cache and asserts the tide stage:
  * never raises, and writes tides.json (so db_import always has an input);
  * is bounded by the CIRCUIT BREAKER — only ~N network attempts happen, not one per station, so
    wall-clock is ~N x per-attempt-timeout, independent of station count (no retry storm);
  * marks EVERY station stale with no fabricated predictions;
  * and that db_import folds tide_stale=True into data_sources (the honesty marker).
Plus: a WARM 30-day cache serves through an outage with NO network and is NOT stale (deterministic
predictions), sliced to the unchanged 7-day output window.

Run: python -m pipeline.tests.test_tides_resilience   (or pytest)
"""
from __future__ import annotations

import json
import shutil
import tempfile
import time
from datetime import date, timedelta
from pathlib import Path

import requests

from pipeline import config
from pipeline import db_import
from pipeline.forecast import tides


def _redirect(tmp: Path):
    """Point the tide stage at temp paths; return the saved originals for restore."""
    saved = (tides.TIDES_CACHE_DIR, tides.TIDES_FORECAST_FILE, tides._NO_PREDICTIONS_FILE, tides.get_once)
    tides.TIDES_CACHE_DIR = tmp / "cache"
    tides.TIDES_FORECAST_FILE = tmp / "tides.json"
    tides._NO_PREDICTIONS_FILE = tmp / "cache" / "_no_predictions.json"
    return saved


def _restore(saved):
    (tides.TIDES_CACHE_DIR, tides.TIDES_FORECAST_FILE, tides._NO_PREDICTIONS_FILE, tides.get_once) = saved


def test_total_outage_is_bounded_marks_all_stale_and_writes_output():
    n_stations = 232
    calls = {"n": 0}

    def _boom(url, **kw):
        calls["n"] += 1
        time.sleep(0.1)          # stand-in for a per-attempt hang (real cost = NOAA_COOPS_TIMEOUT_S)
        raise requests.ConnectionError("simulated CO-OPS outage")

    tmp = Path(tempfile.mkdtemp())
    saved = _redirect(tmp)                    # COLD cache (empty dir)
    try:
        tides.get_once = _boom
        spots = [{"name": f"Spot {i}", "nearest_tide_station_id": f"900{i:04d}"} for i in range(n_stations)]

        t0 = time.monotonic()
        out = tides.fetch(spots, use_cache=True)          # MUST NOT raise
        elapsed = time.monotonic() - t0

        # Circuit breaker: exactly TIDE_FETCH_MAX_CONSECUTIVE_FAILURES network attempts, then it stops.
        assert calls["n"] == config.TIDE_FETCH_MAX_CONSECUTIVE_FAILURES, \
            f"breaker: expected {config.TIDE_FETCH_MAX_CONSECUTIVE_FAILURES} attempts, got {calls['n']}"
        # Wall-clock is breaker-bounded: ~8 x 0.1s, NOT 232 x 0.1s (= 23.2s). The station count is
        # irrelevant to the outage cost — that is the whole point.
        assert elapsed < 2.0, f"stage must stay bounded under outage, took {elapsed:.2f}s"
        # Output is complete + honest: every station present, every one stale, none fabricated.
        assert len(out) == n_stations, "every station annotated (not silently dropped)"
        assert all(e["stale"] is True for e in out.values()), "every station marked stale"
        assert all(not e["hilo"] and not e["hourly"] for e in out.values()), "no fabricated predictions"
        # tides.json was actually written, so db_import has an input.
        assert json.loads(tides.TIDES_FORECAST_FILE.read_text()) == out
        # A cold-outage station is NOT added to the permanent known-bad list (that's for genuine
        # no-predictions, not transient outages).
        assert not tides._NO_PREDICTIONS_FILE.exists()

        # Honesty reaches data_sources: a spot on a stale station reads tide_stale=True / asof None.
        freshness = db_import._load_tide_freshness(tides.TIDES_FORECAST_FILE)
        rec = db_import._spot_record(spots[0], freshness)
        assert rec["data_sources"]["tide_stale"] is True and rec["data_sources"]["tide_asof"] is None
        # A spot with NO tide station → N/A (None), never a false "stale".
        rec2 = db_import._spot_record({"name": "No Station"}, freshness)
        assert rec2["data_sources"]["tide_stale"] is None
    finally:
        _restore(saved)
        shutil.rmtree(tmp, ignore_errors=True)


def test_warm_cache_serves_through_outage_no_network_not_stale():
    calls = {"n": 0}

    def _boom(url, **kw):
        calls["n"] += 1
        raise requests.ConnectionError("outage")

    tmp = Path(tempfile.mkdtemp())
    saved = _redirect(tmp)
    try:
        tides.get_once = _boom
        (tmp / "cache").mkdir(parents=True)
        today = date.today()
        sid = "9410170"
        rows = [{"t": (today + timedelta(days=k)).strftime("%Y-%m-%d 12:00"), "v": f"{2.0 + k:.1f}"}
                for k in range(30)]
        (tmp / "cache" / f"{sid}.json").write_text(json.dumps({
            "station_id": sid, "fetched_at": "2026-07-01T00:00:00+00:00",
            "covers_from": today.isoformat(),
            "covers_until": (today + timedelta(days=30)).isoformat(),   # covers well past the 7-day window
            "hilo": rows, "hourly": rows}))

        out = tides.fetch([{"name": "S", "nearest_tide_station_id": sid}], use_cache=True)
        assert calls["n"] == 0, "a warm 30-day cache must touch NO network (deterministic predictions)"
        assert out[sid]["stale"] is False, "still-valid predictions are not stale"
        assert len(out[sid]["hilo"]) == 7, "sliced to the unchanged 7-day output window (7 daily rows)"
        # freshness = not stale, asof = the cache's fetched_at
        assert out[sid]["asof"] == "2026-07-01T00:00:00+00:00"
    finally:
        _restore(saved)
        shutil.rmtree(tmp, ignore_errors=True)


def test_data_error_marks_known_bad_not_stale():
    # A 200 with {"error": ...} for every datum = genuine no-predictions → permanent known-bad skip,
    # NOT a stale/outage. (Distinct from a transport failure.)
    class _Resp:
        status_code = 200
        def json(self):
            return {"error": {"message": "No Predictions data was found"}}

    tmp = Path(tempfile.mkdtemp())
    saved = _redirect(tmp)
    try:
        tides.get_once = lambda url, **kw: _Resp()
        out = tides.fetch([{"name": "S", "nearest_tide_station_id": "8mystery"}], use_cache=True)
        assert out == {}, "no-predictions station is not written to the output"
        assert tides._NO_PREDICTIONS_FILE.exists(), "it IS recorded as permanently known-bad"
        assert "8mystery" in json.loads(tides._NO_PREDICTIONS_FILE.read_text())
    finally:
        _restore(saved)
        shutil.rmtree(tmp, ignore_errors=True)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} tide-resilience checks passed")


if __name__ == "__main__":
    _run_all()
