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
    """Point the tide stage at temp paths (and disable request pacing so tests don't sleep); return
    the saved originals for restore."""
    saved = (tides.TIDES_CACHE_DIR, tides.TIDES_FORECAST_FILE, tides._NO_PREDICTIONS_FILE,
             tides.get_once, tides.NOAA_COOPS_MIN_INTERVAL_S)
    tides.TIDES_CACHE_DIR = tmp / "cache"
    tides.TIDES_FORECAST_FILE = tmp / "tides.json"
    tides._NO_PREDICTIONS_FILE = tmp / "cache" / "_no_predictions.json"
    tides.NOAA_COOPS_MIN_INTERVAL_S = 0.0     # no real throttle sleep under test
    return saved


def _restore(saved):
    (tides.TIDES_CACHE_DIR, tides.TIDES_FORECAST_FILE, tides._NO_PREDICTIONS_FILE,
     tides.get_once, tides.NOAA_COOPS_MIN_INTERVAL_S) = saved


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
        assert "8mystery" in tides._load_no_predictions(), "recorded as known-bad (v2 versioned format)"
    finally:
        _restore(saved)
        shutil.rmtree(tmp, ignore_errors=True)


def test_cache_horizon_jitter_is_deterministic_ranged_and_desynced():
    # De-synchronize cache expiry: every station cold-starts on the same run, so a FIXED horizon would
    # lapse the whole fleet on one day (~day 23) = a thundering-herd refetch. A deterministic per-station
    # jitter of the horizon spreads covers_until (hence the refetch day) across a multi-day window.
    #
    # DETERMINISTIC: golden values pin the exact algorithm. It MUST be stable across processes/runs, so
    # it uses hashlib — NOT the builtin hash(), which is salted per process (PYTHONHASHSEED). A switch to
    # hash() would still pass a single-process equality check but break these goldens.
    assert tides._station_horizon_hours("9410170") == 669
    assert tides._station_horizon_hours("8443970") == 621
    assert tides._station_horizon_hours("9410170") == tides._station_horizon_hours("9410170")
    # BOUNDED to the [MIN, MAX] = 25-30 day band.
    ids = [f"90{n:05d}" for n in range(300)]
    hs = [tides._station_horizon_hours(s) for s in ids]
    assert all(config.TIDE_CACHE_HORIZON_MIN_HOURS <= h <= config.TIDE_CACHE_HORIZON_HOURS for h in hs)
    # DESYNCED: a common cold start lands covers_until on several distinct days, so no single run
    # refetches the whole fleet. (Pre-jitter every horizon was identical => one day => the herd.)
    today = date.today()
    covers_until_days = {(today + timedelta(hours=h)).toordinal() for h in hs}
    assert len(covers_until_days) >= 4, f"expected a multi-day spread, got {len(covers_until_days)}"


def test_covers_until_is_data_derived_clamped_and_short_triggers_refetch():
    # BUG being fixed: covers_until was computed from the REQUESTED horizon, so a station returning
    # fewer days than asked for still claimed full coverage — _cache_covers then skipped the refetch
    # and the 7-day output slice silently truncated (uncovered hours -> tide_norm None -> tide_mult 1.0,
    # scoring as if the tide were perfect). covers_until must instead come from the DATA.
    today = date(2026, 7, 20)
    horizon_h = 720  # request 30 days

    def series(last_ymd, n=4, hh="12:00"):
        base = date.fromisoformat(last_ymd)
        return [{"t": (base - timedelta(days=k)).strftime("%Y-%m-%d ") + hh, "v": f"{2.0 + k:.1f}"}
                for k in range(n)]

    old_formula = (today + timedelta(hours=horizon_h)).isoformat()   # what the buggy code stored

    # (c) FULL fetch — both series reach the requested horizon -> covers_until UNCHANGED from before,
    # and no materially-short warning.
    covers, shortfall, _ = tides._coverage_from_series(series(old_formula), series(old_formula),
                                                       today, horizon_h)
    assert covers == old_formula, f"full fetch must be unchanged: {covers!r} != {old_formula!r}"
    assert shortfall <= 24, f"full fetch must not look short, got {shortfall}h"

    # (a) SHORT fetch INSIDE the 7-day window — records the real (short) end, and _cache_covers then
    # sees it as needing a refetch, where the OLD lying value would have been served as fresh.
    refetch_until = today + timedelta(hours=config.TIDE_CACHE_REFETCH_WITHIN_HOURS)   # today + 7d
    five = (today + timedelta(days=5)).isoformat()
    covers5, shortfall5, _ = tides._coverage_from_series(series(five), series(five), today, horizon_h)
    assert covers5 == five, f"short fetch must record its real end, got {covers5!r}"
    assert shortfall5 > 24, "a 25-day shortfall must exceed the 24h warn threshold"
    assert tides._cache_covers({"covers_until": covers5}, refetch_until) is False, \
        "data-derived short covers_until must trigger a refetch"
    assert tides._cache_covers({"covers_until": old_formula}, refetch_until) is True, \
        "(demonstrates the old silent-optimism: the requested-horizon value looked fresh)"

    # (b) hilo ends before hourly -> coverage is HILO-governed (the output needs both series; an hourly
    # grid running longer than hilo is not real coverage).
    hilo_end = (today + timedelta(days=12)).isoformat()
    hourly_end = (today + timedelta(days=28)).isoformat()
    covers_b, _, governing_b = tides._coverage_from_series(series(hilo_end), series(hourly_end),
                                                           today, horizon_h)
    assert covers_b == hilo_end, f"expected hilo-governed {hilo_end!r}, got {covers_b!r}"
    assert governing_b == "hilo"

    # (4) an incomplete pair (EITHER series empty) is not cacheable coverage.
    assert tides._coverage_from_series([], series(old_formula), today, horizon_h) is None
    assert tides._coverage_from_series(series(old_formula), [], today, horizon_h) is None

    # (3) clamp — data claiming to run past the requested horizon can't inflate covers_until.
    beyond = (today + timedelta(days=60)).isoformat()
    covers_x, _, _ = tides._coverage_from_series(series(beyond), series(beyond), today, horizon_h)
    assert covers_x == old_formula, f"covers_until must clamp to the requested horizon, got {covers_x!r}"


def test_throttle_and_http_error_never_poison_known_bad():
    # The run-20:30Z bug: a THROTTLE — a 200 with a non-'no predictions' error body, or a non-429 4xx —
    # was mis-read as 'no predictions' and written to the PERMANENT known-bad list (175 stations). It
    # must instead be a transient outage: served stale, NEVER known-bad.
    class _Resp:
        def __init__(self, status, payload=None, text=""):
            self.status_code = status
            self._payload = payload
            self.text = text
        def json(self):
            if self._payload is None:
                raise ValueError("not json")
            return self._payload

    cases = {
        "200 throttle error-body": _Resp(200, {"error": {"message": "Request limit exceeded. Retry later."}},
                                         text='{"error":{"message":"Request limit exceeded"}}'),
        "403 forbidden (non-JSON)": _Resp(403, None, text="<html>403 Forbidden</html>"),
        "400 bad request": _Resp(400, {"error": {"message": "Bad Request"}}, text='{"error":...}'),
    }
    for label, resp in cases.items():
        tmp = Path(tempfile.mkdtemp())
        saved = _redirect(tmp)
        try:
            tides.get_once = lambda url, _r=resp, **kw: _r
            out = tides.fetch([{"name": "S", "nearest_tide_station_id": "8sta"}], use_cache=True)
            assert out["8sta"]["stale"] is True, f"{label}: a throttled station is served stale"
            assert "8sta" not in tides._load_no_predictions(), \
                f"{label}: a throttle / HTTP error must NEVER poison the permanent known-bad list"
        finally:
            _restore(saved)
            shutil.rmtree(tmp, ignore_errors=True)


def test_known_bad_ttl_clear_and_legacy_discard():
    tmp = Path(tempfile.mkdtemp())
    saved = _redirect(tmp)
    try:
        (tmp / "cache").mkdir(parents=True)
        today = date.today()
        # Legacy LIST-format and prior v2 files (no per-entry timestamp, possibly throttle-poisoned) are
        # DISCARDED on load so their stations are re-verified, not skipped forever (heals run 20:30Z).
        tides._NO_PREDICTIONS_FILE.write_text(json.dumps(["poison1", "poison2", "poison3"]))
        assert tides._load_no_predictions() == set(), "legacy list-format known-bad is discarded on load"
        tides._NO_PREDICTIONS_FILE.write_text(json.dumps({"version": 2, "stations": ["v2bad"]}))
        assert tides._load_no_predictions() == set(), "prior v2 (untimestamped) known-bad is discarded"
        # clear_known_bad() counts RAW entries (any format) and removes the file; idempotent when absent.
        assert tides.clear_known_bad() == 1
        assert not tides._NO_PREDICTIONS_FILE.exists()
        assert tides.clear_known_bad() == 0
        # TTL: a station confirmed past the TTL re-verifies (dropped) so it recovers on its own; a recent
        # one is still skipped, and its ORIGINAL first_seen is preserved (TTL runs from first confirmation).
        old = (today - timedelta(days=config.TIDE_KNOWN_BAD_TTL_DAYS + 10)).isoformat()
        recent = today.isoformat()
        tides._save_no_predictions({"stale_bad": old, "fresh_bad": recent})
        assert tides._load_no_predictions() == {"fresh_bad"}, "expired known-bad re-verifies (TTL)"
        assert tides._load_no_predictions_map() == {"fresh_bad": recent}, "surviving entry keeps first_seen"
        assert tides.clear_known_bad() == 2   # both raw entries counted
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
