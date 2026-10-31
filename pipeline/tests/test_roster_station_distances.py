"""The roster's station distances agree with where the stations are, as db_import checks them.

db_import._validate_coord_derived NULLs a spot's buoy or tide station when the stored distance
is more than COORD_DERIVED_DIST_TOLERANCE_KM (5 km) from the great-circle to the station's real
position. A NULLed buoy puts "No nearby buoy." on the spot's page. Every full run up to 2026-10-31
dropped 56 pairings: 39 buoys and 17 tide stations, the same 56 since at least 2026-10-04, with
coordinates, stations and distances unchanged since the roster's first commit (8de2fdb, 2026-08-31).

BUOYS. The committed snapshot (pipeline/data/ndbc_buoy_snapshot.json) is the position db_import
checks against, so every roster buoy distance is checked here. 39 spots are PENDING: their
buoy is not to be changed until the user has seen sw1-raycast run 64 (mode buoy-assign, which
reruns the buoy rule for them), and restoring their old distances alone would bring back buoys
now far from the spot or no longer the nearest. The list is exact both ways: a new mismatch
fails, and a pending spot that comes to agree must be taken off the list.

TIDE STATIONS. The station list db_import checks against is a download the runner caches
(pipeline/geodata/tide_stations.json, not in the repo). The 17 dropped spots now carry the
great-circle distance the runner measured, read from run 41156870591's db_import log (2026-10-31
01:45Z) to one decimal, with their stations unchanged; the values are written out below.
"""
from __future__ import annotations

import json

import pytest

from pipeline import config, db_import
from pipeline.enrichment import geodata as G
from pipeline.geo import haversine_m

TOL = config.COORD_DERIVED_DIST_TOLERANCE_KM

PENDING_RUN_64 = {
    "Berts Bar", "Blackbeard Island", "Captiva", "Carolina Beach Pier", "Church", "Devereaux",
    "Dewees Island", "Englewood", "Fort Clinch", "Fort Fisher", "Galveston Seawall", "Gasparilla",
    "Green Harbor", "Jekyll Island", "Jolly Roger Pier", "Lower Trestles", "Mayport Poles",
    "Miramar", "Monster Hole", "Montauk Point", "Newport Point", "Oak Island Pier", "Oregon Inlet",
    "Pescadero", "Playalinda Beach", "Point Reyes North", "Revere Beach", "Scituate", "Sea Island",
    "South Island", "St Simons Island", "Stone Steps", "Turtle Cove Montauk", "Ventura Harbor",
    "Wilbur by the Sea", "Wild Dunes", "Windjammer", "Winthrop Jetty", "Wrightsville Beach South",
}

# name: (station, distance the runner measured, the distance the roster carried before)
TIDE_FIXED = {
    "Antonio's":             ("9759394", 19.4, 10.75),
    "Avila Beach":           ("9412110", 2.2, 7.46),
    "Backdoor Pipeline":     ("TPT2779", 12.6, 7.42),
    "Costellos":             ("8424601", 12.2, 3.5),
    "Fox Hill":              ("8424601", 11.2, 2.59),
    "Gooch's Beach":         ("8418911", 1.4, 6.69),
    "Indicators":            ("9759394", 19.5, 12.86),
    "Jolly Roger Pier":      ("8657813", 7.7, 1.22),
    "Keyes Beach":           ("8447291", 28.0, 33.94),
    "Maria's":               ("9759394", 19.2, 13.07),
    "Pools":                 ("9759394", 19.8, 11.67),
    "Rincon Domes":          ("9759394", 19.9, 12.34),
    "Sandy Beach":           ("9759394", 19.6, 11.3),
    "Seaview Pier":          ("8657419", 10.7, 16.21),
    "Straws Point":          ("8424601", 7.9, 1.01),
    "Surf Beach Vandenberg": ("TWC0473", 11.9, 32.25),
    "Velzyland":             ("TPT2779", 10.9, 4.23),
}


def _roster():
    return json.loads(config.DEFAULT_ENRICHED_OUTPUT.read_text())


def _buoy_mismatches(roster, snapshot):
    out = set()
    for s in roster:
        bid, stored = s.get("nearest_buoy_id"), s.get("nearest_buoy_dist_km")
        b = snapshot.get(str(bid).lower()) if bid else None
        if b is None or stored is None:
            continue
        if abs(haversine_m(s["lat"], s["lng"], b["lat"], b["lng"]) / 1000.0 - stored) > TOL:
            out.add(s["name"])
    return out


def test_every_roster_buoy_distance_matches_the_snapshot_but_the_pending_39():
    got = _buoy_mismatches(_roster(), G.load_buoy_snapshot())
    assert got - PENDING_RUN_64 == set(), f"new mismatches: {sorted(got - PENDING_RUN_64)}"
    assert PENDING_RUN_64 - got == set(), f"now agree, take off the list: {sorted(PENDING_RUN_64 - got)}"
    assert len(PENDING_RUN_64) == 39


def test_the_check_would_catch_a_drifted_distance():
    # Berts Bar to 41076 (32.536, -79.659), by hand: 0.2215 deg north is 24.63 km, 0.1760 deg east
    # at 32.65 N is 16.48 km, so about 29.6 km; the roster says 19.28. Writing 29.64 makes it agree,
    # and so does anything within 5 km of it.
    roster = _roster()
    snap = G.load_buoy_snapshot()
    berts = next(s for s in roster if s["name"] == "Berts Bar")
    assert berts["nearest_buoy_id"] == "41076" and berts["nearest_buoy_dist_km"] == 19.28
    assert "Berts Bar" in _buoy_mismatches([berts], snap)
    assert _buoy_mismatches([{**berts, "nearest_buoy_dist_km": 29.64}], snap) == set()
    assert _buoy_mismatches([{**berts, "nearest_buoy_dist_km": 24.65}], snap) == set()
    assert _buoy_mismatches([{**berts, "nearest_buoy_dist_km": 24.6}], snap) == {"Berts Bar"}


def test_db_imports_own_check_drops_exactly_the_pending_buoys_and_no_tide(monkeypatch):
    # The tide list is the runner's download; without it db_import checks buoys only, as here.
    monkeypatch.setattr(G, "load_tide_stations", lambda: [])
    recs = [{k: s.get(k) for k in ("name", "lat", "lng", "nearest_buoy_id", "nearest_buoy_dist_km",
                                   "fallback_buoy_ids", "nearest_tide_station_id",
                                   "nearest_tide_station_dist_km")} for s in _roster()]
    assert db_import._validate_coord_derived(recs) == 39
    assert {r["name"] for r in recs if r["nearest_buoy_id"] is None
            and r["nearest_buoy_dist_km"] is None} >= PENDING_RUN_64


@pytest.mark.parametrize("name", sorted(TIDE_FIXED))
def test_the_17_tide_spots_carry_the_runner_measured_distance_on_the_same_station(name):
    sid, km, _old = TIDE_FIXED[name]
    s = next(r for r in _roster() if r["name"] == name)
    assert (s["nearest_tide_station_id"], s["nearest_tide_station_dist_km"]) == (sid, km), s


def test_each_new_tide_distance_is_more_than_the_tolerance_from_the_old():
    # Why the import dropped them: every old value was more than 5 km out.
    assert all(abs(km - old) > TOL for _sid, km, old in TIDE_FIXED.values())
