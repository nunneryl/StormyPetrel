"""The five Isle of Palms spots keep their ocean-side stations through a re-enrich.

THE CHANGE. Berts Bar, Dewees Island, Isle of Palms, Wild Dunes and Windjammer were on 8665387
(Hamlin Creek, Isle of Palms), a sound-side subordinate gauge behind the island, marked
known-bad on 2026-10-09. sw1-raycast run 63 read NOAA's prediction list from the runner and
found an ocean-side station nearer each spot:

    Berts Bar      TEC3047  Sullivans Island (outer coast)      1.03 km
    Dewees Island  8665111  South Dewees Island, Dewees Inlet   1.71 km
    Isle of Palms  8665494  Isle of Palms Pier                  0.83 km
    Wild Dunes     8665494  Isle of Palms Pier                  3.86 km
    Windjammer     8665494  Isle of Palms Pier                  0.17 km

WHY OVERRIDES. The roster was built from a station list without these three stations, on which
the nearest was 8665387, and the runner's cached copy may still be that list. Enrich's Algo 5
rewrites nearest_tide_station_id every run, so without an entry in spot_tide_stations.json a
re-enrich from that list would put all five back on 8665387.

THE RE-ENRICH, end to end through enrich._enrich_one, with the real nearest-station rule run on
a stand-in station list, in two worlds:
  (a) the list does not have the new stations: 8665387 is the only station, sitting ON the
      spot. The override still assigns the new id, with a null distance, which is how
      resolve_tide_station_override keeps an id it cannot find (the tide stage then names it
      as unresolvable rather than the spot losing it).
  (b) the list has the new station 0.01 deg of latitude north of the spot, with 8665387 still
      ON the spot. The override beats a station at zero distance, and the distance is computed:
      1 deg of latitude is 111.19 km on the pipeline's sphere (test_tide_station_override.py
      pins that figure), so 0.01 deg is 1.11 km.

Every expected value is written out by hand.

Run: python -m pytest pipeline/tests/test_tide_override_isle_of_palms.py
"""
from __future__ import annotations

import json

from pipeline import config, enrich
from pipeline.enrichment import tides as ET

OLD = "8665387"
FIVE = {
    # name: (slug, station, name in the entry, expect_km, the old distance)
    "Berts Bar":     ("berts-bar",     "TEC3047", "Sullivans Island (outer coast)", 1.03, 6.15),
    "Dewees Island": ("dewees-island", "8665111", "South Dewees Island, Dewees Inlet", 1.71, 9.03),
    "Isle of Palms": ("isle-of-palms", "8665494", "Isle of Palms Pier", 0.83, 1.91),
    "Wild Dunes":    ("wild-dunes",    "8665494", "Isle of Palms Pier", 3.86, 4.76),
    "Windjammer":    ("windjammer",    "8665494", "Isle of Palms Pier", 0.17, 2.34),
}


def _doc():
    return json.loads(config.SPOT_TIDE_STATIONS_FILE.read_text())


def _roster():
    return {s["name"]: s for s in json.loads(config.DEFAULT_ENRICHED_OUTPUT.read_text())}


# --------------------------------------------------------------------------- #
# 1 — the entries                                                              #
# --------------------------------------------------------------------------- #

def test_the_override_file_pins_each_of_the_five():
    st = _doc()["stations"]
    for name, (slug, sid, sname, km, old_km) in FIVE.items():
        e = st[slug]
        assert (e["station_id"], e["station_name"], e["expect_km"]) == (sid, sname, km), (slug, e)
        assert (e["replaces"], e["replaces_km"]) == (OLD, old_km), (slug, e)


def test_each_entry_says_why():
    st = _doc()["stations"]
    for name, (slug, sid, *_rest) in FIVE.items():
        why = " ".join(st[slug]["reason"])
        assert "8665387 (Hamlin Creek, Isle of Palms) is a sound-side subordinate" in why, slug
        assert "marked known-bad on 2026-10-09" in why, slug
        assert "a station list without it" in why and "3499 stations against NOAA's 3502" in why, slug
        assert why.startswith("8665387") and f"{sid} " in why, slug


def test_none_is_also_marked_unassigned():
    unassigned = _doc()["unassigned"]
    assert [s for _n, (s, *_r) in FIVE.items() if s in unassigned] == []


def test_the_loader_reads_them_under_the_slugs_enrich_computes():
    for name, (slug, sid, *_rest) in FIVE.items():
        assert enrich._slug_for(name) == slug, name
        assert enrich._load_spot_tide_stations()[slug]["station_id"] == sid, slug


def test_the_roster_carries_them_as_overrides():
    roster = _roster()
    for name, (_slug, sid, _sname, km, _old) in FIVE.items():
        r = roster[name]
        assert (r["nearest_tide_station_id"], r["nearest_tide_station_dist_km"],
                r["nearest_tide_station_source"]) == (sid, km, "override"), (name, r)


# --------------------------------------------------------------------------- #
# 2 — a re-enrich keeps them                                                   #
# --------------------------------------------------------------------------- #

def _reenrich(name, stations):
    """_enrich_one on the roster's own coordinates for *name*, with the real nearest-station rule
    run on *stations* and the override resolved against the same list. The heavy algorithms are
    stubbed: each is wrapped in try/except in _enrich_one and none touches the tide field."""
    spot = _roster()[name]
    saved = {k: getattr(enrich, k) for k in
             ("load_land_index", "compute_nearest_tide_station", "compute_nearest_buoy",
              "compute_orientation")}
    saved_load = ET.load_tide_stations
    try:
        enrich.load_land_index = lambda: None
        enrich.compute_nearest_tide_station = lambda s: ET.compute_nearest_tide_station(
            s, stations=stations)
        enrich.compute_nearest_buoy = lambda s: {
            "nearest_buoy_id": None, "nearest_buoy_dist_km": None,
            "fallback_buoy_ids": [], "buoy_confidence": 0.0}
        enrich.compute_orientation = lambda s: {"orientation_deg": 150.0,
                                                "orientation_confidence": 0.5}
        ET.load_tide_stations = lambda: stations
        return enrich._enrich_one({"name": name, "lat": spot["lat"], "lng": spot["lng"]},
                                  skip_raycast=True)
    finally:
        for k, v in saved.items():
            setattr(enrich, k, v)
        ET.load_tide_stations = saved_load


def _old_station_on(spot):
    return {"id": OLD, "lat": spot["lat"], "lng": spot["lng"], "name": "Hamlin Creek, Isle of Palms"}


def test_on_a_station_list_without_the_new_stations_the_rule_alone_picks_8665387():
    """The premise: without the entries, a re-enrich from such a list reverts all five."""
    roster = _roster()
    for name in FIVE:
        got = ET.compute_nearest_tide_station(roster[name], stations=[_old_station_on(roster[name])])
        assert got == {"nearest_tide_station_id": OLD, "nearest_tide_station_dist_km": 0.0}, (name, got)


def test_a_reenrich_from_a_list_without_them_keeps_the_new_ids():
    roster = _roster()
    for name, (_slug, sid, *_rest) in FIVE.items():
        out = _reenrich(name, [_old_station_on(roster[name])])
        assert (out["nearest_tide_station_id"], out["nearest_tide_station_dist_km"],
                out["nearest_tide_station_source"]) == (sid, None, "override"), (name, out)


def test_a_reenrich_from_a_list_with_them_keeps_the_new_ids_and_computes_the_distance():
    roster = _roster()
    for name, (_slug, sid, sname, *_rest) in FIVE.items():
        spot = roster[name]
        stations = [_old_station_on(spot),
                    {"id": sid, "lat": spot["lat"] + 0.01, "lng": spot["lng"], "name": sname}]
        out = _reenrich(name, stations)
        assert (out["nearest_tide_station_id"], out["nearest_tide_station_dist_km"],
                out["nearest_tide_station_source"]) == (sid, 1.11, "override"), (name, out)
