"""The buoy rule's written exceptions (pipeline/data/buoy_overrides.json), honoured by enrich's
compute_nearest_buoy and by buoy-assign's diagnose, and the committed roster they produce.

Decided 2026-11-13 after sw1-raycast run 75 (docs/face_validation/run-75): run 75's choice for
every spot, except 15 spots that keep a partly blocked working buoy (pins) and Kiahuna Beach and
Poipu Beach, which must not take 51212 (exclusions). Expected values are written by hand, or read
from run 75's report as the runner wrote it, never from the code under test.
"""
from __future__ import annotations

import gzip
import json
import logging
import re
from pathlib import Path

import pytest

from pipeline import buoy_assign as A
from pipeline import config
from pipeline.db_import import _validate_coord_derived
from pipeline.enrichment import buoys as B

ROOT = Path(__file__).resolve().parents[2]
RUN_75 = ROOT / "docs" / "face_validation" / "run-75" / "buoy_assign.json.gz"

PINS = {    # group D, decided 2026-11-13: slug -> the buoy kept
    "narrow-river": "44085", "rialto-beach": "46206", "first-beach": "44085",
    "baileys-beach": "44085", "east-beach": "44085", "morro-rock": "46259",
    "breachway": "44085", "dana-point-california": "46285", "doran-beach": "46214",
    "the-k-s": "44085", "weekapaug": "44085", "salty-brine": "44085", "dunes-park": "44085",
    "squibnocket": "44085", "lucy-vincent": "44085",
}
EXCLUDES = {"kiahuna-beach": ["51212"], "poipu-beach": ["51212"]}


# --------------------------------------------------------------------------- #
# The file                                                                     #
# --------------------------------------------------------------------------- #

def _file():
    return json.loads(config.BUOY_OVERRIDES_FILE.read_text(encoding="utf-8"))["overrides"]


def test_the_file_holds_the_fifteen_pins_and_two_exclusions():
    ov = _file()
    assert {s: o["pin"] for s, o in ov.items() if "pin" in o} == PINS
    assert {s: o["exclude"] for s, o in ov.items() if "exclude" in o} == EXCLUDES
    assert len(ov) == 17


def test_every_exception_says_why_and_on_what_evidence():
    for slug, o in _file().items():
        assert o.get("reason") and o.get("evidence"), slug
        assert "run 75" in o["evidence"], slug


# --------------------------------------------------------------------------- #
# The rule, on a made-up list                                                  #
# --------------------------------------------------------------------------- #
# Five stations due north of the spot at 30 N, 80 W. Haversine on R = 6371 km: 0.1 deg of
# latitude is 11.12 km, 0.2 is 22.24, 0.3 is 33.36, 0.4 is 44.48, 0.5 is 55.60. x2 is out of
# sight; the rest are in sight. California: cap 150 km, so 1.0 confidence up to 75 km.

SPOT = {"name": "Test Spot", "lat": 30.0, "lng": -80.0, "region_hint": "California",
        "_algo_lat": 30.0, "_algo_lng": -80.0}


def _st(sid, dlat):
    return {"id": sid, "lat": 30.0 + dlat, "lng": -80.0, "name": sid}


STATIONS = [_st("w1", 0.1), _st("x2", 0.2), _st("w3", 0.3), _st("w4", 0.4), _st("w5", 0.5)]


def _los(_spot, station, _land):
    return not station["id"].startswith("x")


@pytest.fixture()
def world(monkeypatch, tmp_path):
    def set_overrides(ov):
        f = tmp_path / "buoy_overrides.json"
        f.write_text(json.dumps({"overrides": ov}))
        monkeypatch.setattr(B, "BUOY_OVERRIDES_FILE", f)
        B.load_buoy_overrides.cache_clear()
    monkeypatch.setattr(B, "load_ndbc_wave_stations", lambda: STATIONS)
    monkeypatch.setattr(B, "load_land_index", lambda: object())
    monkeypatch.setattr(B, "_los_clear", _los)
    B._build_kdtree.cache_clear()
    set_overrides({})
    yield set_overrides
    B._build_kdtree.cache_clear()
    B.load_buoy_overrides.cache_clear()


def _rule():
    r = B.compute_nearest_buoy(SPOT)
    return (r["nearest_buoy_id"], r["nearest_buoy_dist_km"], r["fallback_buoy_ids"],
            r["buoy_confidence"])


def test_no_override_is_the_rule(world):
    assert _rule() == ("w1", 11.12, ["w3", "w4", "w5"], 1.0)


def test_a_pin_keeps_a_buoy_out_of_sight(world):
    world({"test-spot": {"pin": "x2", "reason": "r"}})
    assert _rule() == ("x2", 22.24, ["w1", "w3", "w4"], 1.0)


def test_a_pin_in_sight_still_leaves_three_fallbacks(world):
    world({"test-spot": {"pin": "w3", "reason": "r"}})
    assert _rule() == ("w3", 33.36, ["w1", "w4", "w5"], 1.0)


def test_a_pin_to_a_station_not_on_the_list_is_ignored(world, caplog):
    world({"test-spot": {"pin": "51205", "reason": "r"}})
    with caplog.at_level(logging.WARNING, logger=B.log.name):
        assert _rule() == ("w1", 11.12, ["w3", "w4", "w5"], 1.0)
    assert [r.getMessage() for r in caplog.records] == [
        "buoy override pins 51205, which is not a wave station on the list: ignored"]


def test_an_excluded_station_is_never_the_buoy_or_a_fallback(world):
    world({"test-spot": {"exclude": ["w1"], "reason": "r"}})
    assert _rule() == ("w3", 33.36, ["w4", "w5"], 1.0)


def test_excluding_every_station_in_sight_leaves_none(world):
    world({"test-spot": {"exclude": ["w1", "w3", "w4", "w5"], "reason": "r"}})
    assert _rule() == (None, None, [], 0.0)


def test_another_spot_is_not_touched(world):
    world({"other-spot": {"pin": "x2", "reason": "r"}})
    assert _rule() == ("w1", 11.12, ["w3", "w4", "w5"], 1.0)


@pytest.mark.parametrize("ov", [{}, {"pin": "x2"}, {"pin": "w3"}, {"exclude": ["w1"]},
                                {"exclude": ["w1", "w3", "w4", "w5"]}])
def test_buoy_assigns_diagnose_agrees_with_the_rule(world, ov):
    world({"test-spot": {**ov, "reason": "r"}} if ov else {})
    d = A.diagnose(SPOT, STATIONS, object(), 150.0, los=_los)
    r = B.compute_nearest_buoy(SPOT)
    assert (d["choice"], d["choice_km"], d["fallbacks"]) == \
        (r["nearest_buoy_id"], r["nearest_buoy_dist_km"], r["fallback_buoy_ids"])


# --------------------------------------------------------------------------- #
# The committed roster: run 75's rule plus the exceptions                      #
# --------------------------------------------------------------------------- #

def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _expected(row):
    """The roster fields for *row* as the runner reported it, with the exceptions as written:
    worked here from the row's candidates, independently of apply_buoy_assign."""
    slug = _slug(row["name"])
    passing = [c for c in row["candidates"] if c["within_cap"] and c["los"]]
    if slug in PINS:
        pin = next(c for c in row["candidates"] if c["id"] == PINS[slug])
        return (pin["id"], round(pin["km"], 2), [c["id"] for c in passing if c["id"] != pin["id"]][:3])
    if slug in EXCLUDES:
        passing = [c for c in passing if c["id"] not in EXCLUDES[slug]]
        return (passing[0]["id"], round(passing[0]["km"], 2), [c["id"] for c in passing[1:4]]) \
            if passing else (None, None, [])
    return (row["choice"], row["choice_km"], row["fallbacks"] or [])


def _run_75():
    with gzip.open(RUN_75, "rt", encoding="utf-8") as f:
        return json.load(f)["lists"]["fresh+7d"]["spots"]


def _roster():
    return {s["name"]: s for s in json.loads(config.DEFAULT_ENRICHED_OUTPUT.read_text(encoding="utf-8"))}


def test_the_committed_roster_is_run_75s_rule_plus_the_exceptions():
    rows, roster = _run_75(), _roster()
    assert len(rows) == len(roster) == 648
    wrong = []
    for r in rows:
        s = roster[r["name"]]
        got = (s.get("nearest_buoy_id"), s.get("nearest_buoy_dist_km"), s.get("fallback_buoy_ids") or [])
        if got != _expected(r):
            wrong.append((r["name"], got, _expected(r)))
    assert wrong == [], wrong[:5]


@pytest.mark.parametrize("name, buoy, km", [
    # pinned, the buoy kept (run 75: blocked; the rule would take 44097 / none / 46253)
    ("Narrow River", "44085", 34.71), ("Dana Point, California", "46285", 4.14),
    ("Rialto Beach", "46206", 143.79), ("Weekapaug", "44085", 61.23),
    # excluded 51212: nothing else in sight
    ("Kiahuna Beach", None, None), ("Poipu Beach", None, None),
    # group C, cleared
    ("Palm Beach", None, None), ("Caspar", None, None), ("Jug Handle", None, None),
    # group A and B, the rule's choice
    ("Mendocino", "46014", 17.8), ("Keyes Beach", "44020", 15.29),
    ("Rat Beach", "46221", 23.33), ("Ventnor Pier", "44084", 101.42),
    # the fix's gains and nearer buoys
    ("Steps Beach Rincon", "41115", 2.93), ("Swamis", "46274", 3.66),
    ("Upper Trestles", "46277", 7.87),
    # the 250 m standoff kept their buoys
    ("Richardson Beach", "51206", 6.68), ("Fenwick Island", "44084", 8.85),
])
def test_named_spots_by_hand(name, buoy, km):
    s = _roster()[name]
    assert (s["nearest_buoy_id"], s["nearest_buoy_dist_km"]) == (buoy, km)


def test_db_import_keeps_every_pairing():
    recs = [{k: s.get(k) for k in ("name", "lat", "lng", "nearest_buoy_id",
                                   "nearest_buoy_dist_km", "fallback_buoy_ids")}
            for s in _roster().values() if s.get("nearest_buoy_id")]
    assert len(recs) == 599
    assert _validate_coord_derived(recs) == 0
