"""The roster's station distances agree with where the stations are, as db_import checks them.

db_import._validate_coord_derived NULLs a spot's buoy or tide station when the stored distance
is more than COORD_DERIVED_DIST_TOLERANCE_KM (5 km) from the great-circle to the station's real
position. A NULLed buoy puts "No nearby buoy." on the spot's page. Every full run up to 2026-10-31
dropped 56 pairings: 39 buoys and 17 tide stations, the same 56 since at least 2026-10-04, with
coordinates, stations and distances unchanged since the roster's first commit (8de2fdb, 2026-08-31).

BUOYS. The committed snapshot (pipeline/data/ndbc_buoy_snapshot.json) is the position db_import
checks against, so every roster buoy distance is checked here. sw1-raycast run 64 (mode
buoy-assign, 2026-10-31) reran the buoy rule for the 39 dropped spots and the 173 with no buoy,
and the user approved its result on 2026-11-02 (docs/face_validation/run-64/proposal.csv): the
fresh NDBC list's choice for 198 spots, 44084 from the runner's cached list for five New Jersey
spots (it reports, though the fresh list missed it), and 51205 for Peahi Jaws. Eight are HELD
for a follow-up check of buoys 46453, 46041, 42358 and 46054 (run 65); two of them still carry a
stale buoy, so the mismatch list is exactly those two, and the check fails on any other.
Mecox Beach is held too: the New York cap fix brings its nearest buoy within the cap, but run 64
never tested line of sight past the old 100 km cap.

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

HELD_FOR_RUN_65 = {
    "Kalaloch Beach", "La Push First Beach", "La Push Second Beach", "La Push Third Beach",
    "Galveston Seawall", "61st Street Galveston", "Ventura Harbor", "Ventura Point",
}
STILL_STALE = {"Galveston Seawall", "Ventura Harbor"}     # held, with their old buoy

# Run 64's result as approved, written out from the artifact. name: (buoy, km)
CHANGED = {     # was: Berts Bar, Dewees Island, Wild Dunes, Windjammer 41076; Church, Lower
                # Trestles 46285; Stone Steps 46266; Oregon Inlet 44086; Oak Island Pier 41110;
                # Monster Hole 41113; Green Harbor, Scituate, Winthrop Jetty 44013
    "Berts Bar": ("41065", 20.79), "Dewees Island": ("41065", 9.05),
    "Wild Dunes": ("41065", 12.0), "Windjammer": ("41065", 15.68),
    "Church": ("46277", 8.77), "Lower Trestles": ("46277", 8.23),
    "Stone Steps": ("46274", 4.39), "Oregon Inlet": ("44095", 19.04),
    "Oak Island Pier": ("ssbn7", 37.79), "Monster Hole": ("41114", 40.78),
    "Green Harbor": ("44090", 36.95), "Scituate": ("44090", 50.08),
    "Winthrop Jetty": ("44098", 81.07),
}
KEPT = {        # same buoy, the distance corrected
    "Blackbeard Island": ("41112", 88.59), "Captiva": ("42098", 139.9),
    "Carolina Beach Pier": ("41110", 18.1), "Englewood": ("42098", 90.09),
    "Fort Clinch": ("41112", 12.87), "Fort Fisher": ("41110", 26.92),
    "Gasparilla": ("42098", 108.54), "Jekyll Island": ("41112", 40.23),
    "Jolly Roger Pier": ("41110", 25.76), "Mayport Poles": ("41112", 37.59),
    "Montauk Point": ("44097", 62.48), "Newport Point": ("46253", 23.69),
    "Pescadero": ("46012", 42.51), "Playalinda Beach": ("41070", 61.78),
    "Point Reyes North": ("46214", 45.54), "Sea Island": ("41112", 55.08),
    "South Island": ("41076", 79.56), "St Simons Island": ("41112", 48.19),
    "Turtle Cove Montauk": ("44097", 62.95), "Wilbur by the Sea": ("41070", 22.75),
    "Wrightsville Beach South": ("41110", 9.68),
}
CLEARED = {"Devereaux", "Miramar", "Revere Beach"}   # 44013 / 46053 report no waves; nothing else in sight
FROM_CACHED = {
    "Atlantic City": ("44084", 105.77), "Margate": ("44084", 98.86),
    "Ocean City NJ": ("44084", 92.33), "Avalon NJ": ("44084", 68.17),
    "Stone Harbor": ("44084", 61.25), "Peahi Jaws": ("51205", 16.15),
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


def test_every_roster_buoy_distance_matches_the_snapshot_but_the_two_held_stale():
    got = _buoy_mismatches(_roster(), G.load_buoy_snapshot())
    assert got == STILL_STALE, f"new: {sorted(got - STILL_STALE)}; now agree: {sorted(STILL_STALE - got)}"
    assert STILL_STALE <= HELD_FOR_RUN_65 and len(HELD_FOR_RUN_65) == 8


def test_the_other_six_held_and_mecox_still_have_no_buoy():
    by = {s["name"]: s for s in _roster()}
    for name in (HELD_FOR_RUN_65 - STILL_STALE) | {"Mecox Beach"}:
        assert by[name].get("nearest_buoy_id") is None, (name, by[name].get("nearest_buoy_id"))


@pytest.mark.parametrize("table", ["CHANGED", "KEPT", "FROM_CACHED"])
def test_the_approved_assignments(table):
    by = {s["name"]: s for s in _roster()}
    for name, (bid, km) in globals()[table].items():
        assert (by[name]["nearest_buoy_id"], by[name]["nearest_buoy_dist_km"]) == (bid, km), name


def test_the_three_cleared_spots_have_no_buoy_and_no_fallbacks():
    by = {s["name"]: s for s in _roster()}
    for name in CLEARED:
        s = by[name]
        assert (s["nearest_buoy_id"], s["nearest_buoy_dist_km"], s["fallback_buoy_ids"]) == \
            (None, None, []), name


def test_fallbacks_and_confidence_follow_the_rule():
    # buoy_confidence is 1.0 within half the cap, else 0.75 (enrichment/buoys.compute_nearest_buoy).
    # Cupsogue Beach, New York, 67.89 km: the cap is now 150 (it was the lakes' 100), so 1.0.
    # Flying Point Beach, 95.41 km: 0.75. Peahi Jaws, Hawaii (cap 150), 16.15 km: 1.0.
    by = {s["name"]: s for s in _roster()}
    assert (by["Cupsogue Beach"]["nearest_buoy_id"], by["Cupsogue Beach"]["nearest_buoy_dist_km"],
            by["Cupsogue Beach"]["enrichment_confidence"]["nearest_buoy"]) == ("44025", 67.89, 1.0)
    assert by["Flying Point Beach"]["enrichment_confidence"]["nearest_buoy"] == 0.75
    assert by["Peahi Jaws"]["enrichment_confidence"]["nearest_buoy"] == 1.0
    assert by["Oregon Inlet"]["fallback_buoy_ids"] == ["44086", "44100", "44088"]
    assert by["Berts Bar"]["fallback_buoy_ids"] == ["41076"]


def test_the_check_would_catch_a_drifted_distance():
    # Berts Bar to 41076 (32.536, -79.659), by hand: 0.2215 deg north is 24.63 km, 0.1760 deg east
    # at 32.65 N is 16.48 km, so about 29.6 km. Before run 64 the roster said 19.28; 29.64 agrees,
    # and so does anything within 5 km of it.
    snap = G.load_buoy_snapshot()
    berts = {"name": "Berts Bar", "lat": 32.757505, "lng": -79.835029, "nearest_buoy_id": "41076",
             "nearest_buoy_dist_km": 19.28}
    assert _buoy_mismatches([berts], snap) == {"Berts Bar"}
    assert _buoy_mismatches([{**berts, "nearest_buoy_dist_km": 29.64}], snap) == set()
    assert _buoy_mismatches([{**berts, "nearest_buoy_dist_km": 24.65}], snap) == set()
    assert _buoy_mismatches([{**berts, "nearest_buoy_dist_km": 24.6}], snap) == {"Berts Bar"}


def test_db_imports_own_check_drops_only_the_two_held_buoys_and_no_tide(monkeypatch):
    # The tide list is the runner's download; without it db_import checks buoys only, as here.
    monkeypatch.setattr(G, "load_tide_stations", lambda: [])
    recs = [{k: s.get(k) for k in ("name", "lat", "lng", "nearest_buoy_id", "nearest_buoy_dist_km",
                                   "fallback_buoy_ids", "nearest_tide_station_id",
                                   "nearest_tide_station_dist_km")} for s in _roster()]
    before = {r["name"] for r in recs if r["nearest_buoy_id"]}
    assert db_import._validate_coord_derived(recs) == 2
    assert before - {r["name"] for r in recs if r["nearest_buoy_id"]} == STILL_STALE


@pytest.mark.parametrize("name", sorted(TIDE_FIXED))
def test_the_17_tide_spots_carry_the_runner_measured_distance_on_the_same_station(name):
    sid, km, _old = TIDE_FIXED[name]
    s = next(r for r in _roster() if r["name"] == name)
    assert (s["nearest_tide_station_id"], s["nearest_tide_station_dist_km"]) == (sid, km), s


def test_each_new_tide_distance_is_more_than_the_tolerance_from_the_old():
    # Why the import dropped them: every old value was more than 5 km out.
    assert all(abs(km - old) > TOL for _sid, km, old in TIDE_FIXED.values())
