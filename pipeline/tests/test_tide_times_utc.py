"""Tide times are UTC end to end: CO-OPS is asked for time_zone=gmt and the times are compared with
the UTC forecast hour as they are, with no offset guessed from longitude.

Before: predictions in the station's local clock (lst_ldt, daylight saving included) read at the
forecast hour plus round(lng / 15) hours, a standard-time offset. Every tide on daylight saving
was an hour off from March to November; Oahu, Kauai and Maui (round(lng / 15) = -11, Hawaii is
UTC-10) all year; the Florida Gulf coast on Eastern time (round = -6) an hour in winter and two in
summer. Every expected value is written by hand.
"""
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

import pytest

from pipeline import config, db_import, interpret, star_preview
from pipeline.forecast import tides as T

ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# The fetch asks for UTC, and what it keeps says so                            #
# --------------------------------------------------------------------------- #
def test_co_ops_is_asked_for_gmt(monkeypatch):
    seen = []

    class R:
        status_code = 200

        def json(self):
            return {"predictions": [{"t": "2026-07-15 05:39", "v": "6.604", "type": "H"}]}

    def fake_get_once(url, params=None, timeout=None):
        seen.append(params)
        return R()
    monkeypatch.setattr(T, "get_once", fake_get_once)
    assert T._fetch_interval_once("9413745", "hilo", "20260715", 24) == [
        {"t": "2026-07-15 05:39", "v": "6.604", "type": "H"}]
    assert config.TIDE_TIME_ZONE == "gmt" and seen[0]["time_zone"] == "gmt", seen


def test_a_cache_without_the_utc_marker_is_not_used(tmp_path, monkeypatch):
    monkeypatch.setattr(T, "TIDES_CACHE_DIR", tmp_path)
    entry = {"station_id": "9413745", "covers_until": "2026-12-31", "hilo": [], "hourly": []}
    (tmp_path / "9413745.json").write_text(json.dumps(entry))
    assert T._load_cache("9413745") is None                    # local-clock cache: fetched again
    (tmp_path / "9413745.json").write_text(json.dumps({**entry, "time_zone": "lst_ldt"}))
    assert T._load_cache("9413745") is None
    (tmp_path / "9413745.json").write_text(json.dumps({**entry, "time_zone": "gmt"}))
    assert T._load_cache("9413745")["covers_until"] == "2026-12-31"


def test_every_emitted_station_carries_the_marker():
    e = T._emit("1612340", {"hilo": [], "hourly": []}, date(2026, 7, 15), date(2026, 7, 22),
                stale=False, asof="x")
    assert e["time_zone"] == "gmt"
    assert T._no_data_entry("1612340")["time_zone"] == "gmt"


# --------------------------------------------------------------------------- #
# interpret reads the times as UTC                                             #
# --------------------------------------------------------------------------- #
# Santa Cruz (9413745), 15 Jul 2026, from CO-OPS in gmt: high 05:39Z 6.604 ft, low 13:03Z -1.576 ft.
HILO = [{"t": "2026-07-15 05:39", "v": "6.604", "type": "H"},
        {"t": "2026-07-15 13:03", "v": "-1.576", "type": "L"}]


def test_a_block_without_the_marker_is_refused():
    assert interpret.build_tide_series({"station_id": "9413745", "hilo": HILO}) is None
    assert interpret.build_tide_series({"station_id": "9413745", "time_zone": "lst_ldt", "hilo": HILO}) is None


def test_the_tide_is_read_at_the_utc_hour_itself():
    series = interpret.build_tide_series({"station_id": "9413745", "time_zone": "gmt", "hilo": HILO})
    assert series["points"][0] == (datetime(2026, 7, 15, 5, 39), 6.604)
    high = interpret.lookup_tide_norm(series, datetime(2026, 7, 15, 5, 39, tzinfo=timezone.utc))
    low = interpret.lookup_tide_norm(series, datetime(2026, 7, 15, 13, 3, tzinfo=timezone.utc))
    assert high == (6.604, 1.0) and low == (-1.576, 0.0)
    # a naive datetime is UTC too
    assert interpret.lookup_tide_norm(series, datetime(2026, 7, 15, 5, 39)) == (6.604, 1.0)


def test_no_longitude_offset_is_left_for_tides():
    assert not hasattr(interpret, "_tz_offset_hours")
    import inspect
    assert list(inspect.signature(interpret.lookup_tide_norm).parameters) == ["series", "valid_time_utc"]
    hits = []
    for p in (ROOT / "pipeline").rglob("*.py"):
        if "tests" in p.parts:
            continue
        text = p.read_text(encoding="utf-8")
        if re.search(r"""["']lst_ldt["']""", text):
            hits.append(str(p.relative_to(ROOT)))
    assert hits == [], f"CO-OPS asked for local time in: {hits}"


def test_tide_predictions_are_written_in_utc():
    assert db_import._parse_coops_time("2026-07-15 05:39") == "2026-07-15T05:39:00+00:00"


# --------------------------------------------------------------------------- #
# The preview's shift: how far the old reading was from the hour itself        #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("spot, when, shift", [
    ({"time_zone": "America/Los_Angeles", "lng": -122.03}, "2026-07-15T12:00:00Z", 1),   # PDT -7, guess -8
    ({"time_zone": "America/Los_Angeles", "lng": -122.03}, "2026-12-15T12:00:00Z", 0),   # PST -8
    ({"time_zone": "America/New_York", "lng": -74.0}, "2026-07-15T12:00:00Z", 1),        # EDT -4, guess -5
    ({"time_zone": "Pacific/Honolulu", "lng": -158.05}, "2026-12-15T12:00:00Z", 1),     # HST -10, guess -11
    ({"time_zone": "Pacific/Honolulu", "lng": -155.09}, "2026-07-15T12:00:00Z", 0),     # Hilo: guess -10
    ({"time_zone": "America/New_York", "lng": -82.8}, "2026-07-15T12:00:00Z", 2),       # Gulf FL: EDT -4, guess -6
    ({"time_zone": "America/Puerto_Rico", "lng": -67.2}, "2026-07-15T12:00:00Z", 0),    # AST -4, guess -4
    ({"lng": -122.0}, "2026-07-15T12:00:00Z", None),
])
def test_the_shift(spot, when, shift):
    assert star_preview.tide_time_shift_hours(spot, when) == shift


def test_the_times_mode_moves_only_rated_spots_and_reads_the_neighbour_hour():
    roster = {"Rated": {"name": "Rated", "time_zone": "Pacific/Honolulu", "lng": -158.05,
                        "tide_preference": "low", "tide_preference_source": "researched"},
              "Unrated": {"name": "Unrated", "time_zone": "Pacific/Honolulu", "lng": -158.05,
                          "tide_preference": "low", "tide_preference_source": "model_recall"}}
    names = {1: ("Rated", "Hawaii"), 2: ("Unrated", "Hawaii")}
    rows = [{"spot_id": sid, "valid_time": f"2026-12-15T{h:02d}:00:00+00:00", "tide_norm": n,
             "tide_mult": 1.0} for sid in (1, 2) for h, n in ((10, 0.9), (11, 0.1))]
    summary = {}
    after = star_preview.times_row_after(roster, names, rows, summary)
    moved = after(rows[0])                          # Rated 10Z reads the stored 11Z tide: 0.1, low -> 1.0
    assert moved["tide_norm"] == 0.1 and moved["tide_mult"] == 1.0
    assert after(rows[1]) is rows[1]               # 11Z: its neighbour 12Z is not in the window
    assert after(rows[2]) is rows[2]               # Unrated: never moved
    after.done()
    assert summary == {"hours_by_shift": {"1": 2}, "hours_retimed": 1, "hours_without_the_neighbour": 1,
                       "spots_retimed": ["Rated"]}
