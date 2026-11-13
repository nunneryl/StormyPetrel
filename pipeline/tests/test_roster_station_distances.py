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
spots (it reports, though the fresh list missed it), and 51205 for Peahi Jaws. Nine were held for
run 65 (2026-11-02, docs/face_validation/run-65/), which reran the rule for them on NDBC's list as
downloaded plus every station with a wave height in the last 7 days: all nine get a buoy within
the cap and in line of sight (RUN_65). Every roster buoy distance now matches the snapshot.

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

# Run 65's headline answer, written out from the artifact: name: (buoy, km, fallbacks, confidence).
# Confidence is 1.0 within half the cap, else 0.75 (California and New York 150, Washington 200,
# Texas 350): only Mecox Beach, 100.89 km against 75, is past half. Ventura Harbor's record has no
# confidence for its buoy.
RUN_65 = {
    "Kalaloch Beach":        ("46041", 38.51, ["46099", "46211", "46029"], 1.0),   # fallbacks: run 75
    "La Push First Beach":   ("46041", 62.12, ["46099", "46243"], 1.0),
    "La Push Second Beach":  ("46041", 59.92, ["46099", "46211", "46243"], 1.0),
    "La Push Third Beach":   ("46099", 98.68, ["46211", "46243"], 1.0),   # 46041 out of sight
    "Galveston Seawall":     ("42035", 37.55, ["42032", "42092"], 1.0),   # was 42.79 km; fallbacks: run 75
    "61st Street Galveston": ("42035", 41.3, ["42032", "42092"], 1.0),   # fallbacks: run 75
    "Ventura Harbor":        ("46053", 52.55, ["46054"], None),           # was 57.93 km
    "Ventura Point":         ("46053", 49.46, ["46025", "46054", "46219"], 1.0),
    "Mecox Beach":           ("44097", 100.89, ["44025", "44065"], 0.75),  # fallbacks: run 75
}

# Run 64's result as approved, written out from the artifact. name: (buoy, km)
CHANGED = {     # was: Berts Bar, Dewees Island, Wild Dunes, Windjammer 41076; Church, Lower
                # Trestles 46285; Stone Steps 46266; Oregon Inlet 44086; Oak Island Pier 41110;
                # Monster Hole 41113; Green Harbor, Scituate, Winthrop Jetty 44013
    "Berts Bar": ("41065", 20.79), "Dewees Island": ("41065", 9.05),
    "Wild Dunes": ("41065", 12.0), "Windjammer": ("41065", 15.68),
    "Church": ("46277", 8.77), "Lower Trestles": ("46277", 8.23),
    "Stone Steps": ("46274", 4.39), "Oregon Inlet": ("44095", 19.04),
    "Oak Island Pier": ("41108", 21.36),    # run 74: ssbn7 silent since 2026-11-04
    "Monster Hole": ("41114", 40.78),
    "Green Harbor": ("44090", 36.95),
    "Scituate": ("44013", 18.47), "Winthrop Jetty": ("44013", 25.76),   # run 75: 44013 nearer, in sight
}
KEPT = {        # same buoy, the distance corrected (run 64); five marked run 75 moved to a nearer buoy
    "Blackbeard Island": ("41008", 32.81),     # run 75: nearer, in sight
    "Captiva": ("42098", 139.9),
    "Carolina Beach Pier": ("41110", 18.1), "Englewood": ("42098", 90.09),
    "Fort Clinch": ("41112", 12.87), "Fort Fisher": ("41110", 26.92),
    "Gasparilla": ("42098", 108.54), "Jekyll Island": ("41112", 40.23),
    "Jolly Roger Pier": ("41110", 25.76), "Mayport Poles": ("41112", 37.59),
    "Montauk Point": ("44097", 62.48), "Newport Point": ("46253", 23.69),
    "Pescadero": ("46012", 42.51),
    "Playalinda Beach": ("41009", 56.15), "Point Reyes North": ("46013", 34.28),   # run 75
    "Sea Island": ("41008", 48.38), "South Island": ("41004", 72.93),             # run 75
    "St Simons Island": ("41112", 48.19),
    "Turtle Cove Montauk": ("44097", 62.95), "Wilbur by the Sea": ("41070", 22.75),
    "Wrightsville Beach South": ("41110", 9.68),
}
# Run 64 cleared Devereaux, Miramar and Revere Beach (44013 / 46053 reported no waves); run 74
# gives Miramar 46053 and Revere Beach 44013 back (both report again), and clears the five Hawaii
# spots on silent 51205 / 51208 and Siesta Key (42039 at 381 km, past the cap, nothing in sight).
CLEARED = {"Devereaux", "Kanaha Beach", "Hoʻokipa", "Peahi Jaws", "Hanalei Bay", "Tunnels Beach",
           "Siesta Key",
           # run 75 (2026-11-13): the line to 41122 / 46013 crosses 14 to 70 km of land and nothing
           # else is in sight
           "Palm Beach", "Caspar", "Jug Handle"}
FROM_CACHED = {
    "Atlantic City": ("44091", 72.58),   # run 75: 44091 in sight with the 250 m standoff
    "Margate": ("44084", 98.86),
    "Ocean City NJ": ("44084", 92.33), "Avalon NJ": ("44084", 68.17),
    "Stone Harbor": ("44084", 61.25),   # Peahi Jaws' 51205, from this list, cleared by run 74
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


def test_every_roster_buoy_distance_matches_the_snapshot():
    got = _buoy_mismatches(_roster(), G.load_buoy_snapshot())
    assert got == set(), sorted(got)


@pytest.mark.parametrize("name", sorted(RUN_65))
def test_run_65s_assignments(name):
    bid, km, fallbacks, conf = RUN_65[name]
    s = next(r for r in _roster() if r["name"] == name)
    assert (s["nearest_buoy_id"], s["nearest_buoy_dist_km"], s["fallback_buoy_ids"]) == (bid, km, fallbacks)
    assert (s.get("enrichment_confidence") or {}).get("nearest_buoy") == conf


def test_galveston_seawalls_distance_by_hand():
    # Spot 29.284396 N, 94.792893 W; 42035 in the snapshot 29.235 N, 94.41 W. By hand: 0.049396 deg
    # of latitude is 5.49 km; 0.382893 deg of longitude at 29.26 N (cos 0.8725) is 37.15 km;
    # together 37.55 km. The roster carried 42.79, more than 5 km out, so db_import dropped it.
    snap = G.load_buoy_snapshot()["42035"]
    assert (snap["lat"], snap["lng"]) == (29.235, -94.41)
    km = haversine_m(29.284396, -94.792893, 29.235, -94.41) / 1000.0
    assert abs(km - 37.55) < 0.05
    assert abs(km - 42.79) > TOL


@pytest.mark.parametrize("table", ["CHANGED", "KEPT", "FROM_CACHED"])
def test_the_approved_assignments(table):
    by = {s["name"]: s for s in _roster()}
    for name, (bid, km) in globals()[table].items():
        assert (by[name]["nearest_buoy_id"], by[name]["nearest_buoy_dist_km"]) == (bid, km), name


def test_the_cleared_spots_have_no_buoy_and_no_fallbacks():
    by = {s["name"]: s for s in _roster()}
    for name in CLEARED:
        s = by[name]
        assert (s["nearest_buoy_id"], s["nearest_buoy_dist_km"], s["fallback_buoy_ids"]) == \
            (None, None, []), name


def test_fallbacks_and_confidence_follow_the_rule():
    # buoy_confidence is 1.0 within half the cap, else 0.75 (enrichment/buoys.compute_nearest_buoy).
    # Cupsogue Beach, New York, 67.89 km: the cap is now 150 (it was the lakes' 100), so 1.0.
    # Flying Point Beach, 95.41 km: 0.75. Peahi Jaws, cleared by run 74: 0.0.
    by = {s["name"]: s for s in _roster()}
    assert (by["Cupsogue Beach"]["nearest_buoy_id"], by["Cupsogue Beach"]["nearest_buoy_dist_km"],
            by["Cupsogue Beach"]["enrichment_confidence"]["nearest_buoy"]) == ("44025", 67.89, 1.0)
    assert by["Flying Point Beach"]["enrichment_confidence"]["nearest_buoy"] == 0.75
    assert by["Peahi Jaws"]["enrichment_confidence"]["nearest_buoy"] == 0.0
    # Fallbacks as run 75 lists them (more pass with the 250 m line-of-sight standoff).
    assert by["Oregon Inlet"]["fallback_buoy_ids"] == ["44086", "44056", "44100"]
    assert by["Berts Bar"]["fallback_buoy_ids"] == ["41076", "41004"]


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


def test_db_imports_own_check_drops_no_buoy_and_no_tide(monkeypatch):
    # The tide list is the runner's download; without it db_import checks buoys only, as here.
    monkeypatch.setattr(G, "load_tide_stations", lambda: [])
    recs = [{k: s.get(k) for k in ("name", "lat", "lng", "nearest_buoy_id", "nearest_buoy_dist_km",
                                   "fallback_buoy_ids", "nearest_tide_station_id",
                                   "nearest_tide_station_dist_km")} for s in _roster()]
    before = {r["name"] for r in recs if r["nearest_buoy_id"]}
    assert db_import._validate_coord_derived(recs) == 0
    assert {r["name"] for r in recs if r["nearest_buoy_id"]} == before


@pytest.mark.parametrize("name", sorted(TIDE_FIXED))
def test_the_17_tide_spots_carry_the_runner_measured_distance_on_the_same_station(name):
    sid, km, _old = TIDE_FIXED[name]
    s = next(r for r in _roster() if r["name"] == name)
    assert (s["nearest_tide_station_id"], s["nearest_tide_station_dist_km"]) == (sid, km), s


def test_each_new_tide_distance_is_more_than_the_tolerance_from_the_old():
    # Why the import dropped them: every old value was more than 5 km out.
    assert all(abs(km - old) > TOL for _sid, km, old in TIDE_FIXED.values())
