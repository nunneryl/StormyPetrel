"""Four Mendocino spots get a distant station for the RATING and nothing on the TILE.

WHY THIS FILE EXISTS. Twelve valid roster spots had no tide station at all: their nearest
CO-OPS station sits past TIDE_STATION_MAX_DIST_KM (50 km), so compute_nearest_tide_station
returned a NULL pair and they published no tide of any kind.

THE STANDARD THEY ARE JUDGED AGAINST, and it decides every case here: a spot with no tide
gets tide_mult = 1.0, because interpret.tide_multiplier short-circuits on a None tide_norm.
1.0 is the neutral TOP of the 0.6-1.0 range, so a WRONG tide is strictly worse than none — it
can only move the rating down, and a phase error of a few hours inverts the low/mid/high
verdict outright.

THE SPLIT. Four Mendocino spots take station 9416841 (Arena Cove) at 50-71 km up the same
open, unindented coast. The rating consumes PHASE — lookup_tide_norm normalises to
(v-min)/(max-min), so an error in absolute range divides out — and the tile is suppressed,
because a height in feet from 70 km up the coast is a different spot's height and no
presentation makes it honest. The other eight are left deliberately blank, each with its
reason recorded in the override file. Five of those eight have since been assigned local
stations (Key West, Captiva, Reid State Park, St Simons Island, Jekyll Island) and three
remain blank — the Georgia islands whose only reference station is up an inland river.

EVERY EXPECTED VALUE IS A LITERAL. Station ids, slugs, spot names and the tide_multiplier
outputs are all written out; nothing is produced by calling the function under test.

Run: python -m pipeline.tests.test_tide_override_mendocino
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline import config, enrich, interpret
from pipeline.enrichment import tides as ET

MENDOCINO = ("Caspar", "Jug Handle", "Mackerricher", "Ten Mile Beach")
MENDOCINO_SLUGS = ("caspar", "jug-handle", "mackerricher", "ten-mile-beach")
ARENA_COVE = "9416841"
# St Simons Island and Jekyll Island were in this group and are not any more: reading the
# station file showed 8677344 at the mouth of St Simons Sound, 1.4 and 8.3 km away. The three
# that remain are declined on MEASURED grounds — their only reference station, 8674301, solves
# to 31.5747, -81.1893, inland of the barrier chain. See test_tide_override_georgia.py.
GEORGIA = ("Blackbeard Island", "Sea Island", "St. Catherines Island")
GEORGIA_SLUGS = ("blackbeard-island", "sea-island", "st-catherines-island")
# Key West, Captiva and Reid State Park WERE in this group and are not any more — see
# test_tide_override_local_stations.py. They were declined on regime and geometry arguments
# resting on distances of 50-176 km that were upper bounds over the wrong candidate set, and
# their real nearest stations are 3-5 km away. Nothing but the Georgia five is blank now.
LOCAL = {"Key West": "8724580", "Captiva": "8725383", "Reid State Park": "8417177"}


def _roster():
    return json.loads(config.DEFAULT_ENRICHED_OUTPUT.read_text())


def _by_name():
    return {s.get("name"): s for s in _roster()}


def _override_doc():
    return json.loads(config.SPOT_TIDE_STATIONS_FILE.read_text())


# --------------------------------------------------------------------------- #
# 1 — the four Mendocino spots resolve to Arena Cove                           #
# --------------------------------------------------------------------------- #

def test_the_override_file_assigns_arena_cove_to_all_four():
    st = _override_doc()["stations"]
    for slug in MENDOCINO_SLUGS:
        assert st[slug]["station_id"] == "9416841", (slug, st[slug].get("station_id"))
        assert st[slug]["suppress_height"] is True, slug


def test_the_committed_roster_puts_all_four_on_arena_cove():
    """The forecast workflow does NOT run enrich — it goes download_geodata -> fetch_all ->
    interpret -> db_import against the committed roster — so the roster is what carries the
    change until someone re-enriches. Algo 5b is what makes it survive that re-enrich."""
    by = _by_name()
    for n in MENDOCINO:
        assert by[n]["nearest_tide_station_id"] == "9416841", (n, by[n].get("nearest_tide_station_id"))
        assert by[n]["nearest_tide_station_source"] == "override", n


def test_the_resolver_returns_arena_cove_over_a_nearer_station():
    """The override must beat the algorithm, and the fixture makes that unambiguous: a decoy
    station sits at ZERO distance from the spot and still loses."""
    caspar = {"name": "Caspar", "lat": 39.3611, "lng": -123.8178}
    stations = [
        {"id": "0000000", "lat": 39.3611, "lng": -123.8178, "name": "decoy on the spot"},
        {"id": ARENA_COVE, "lat": 38.9019, "lng": -123.7038, "name": "Arena Cove"},
    ]
    # The algorithm prefers the decoy — 0.0 km, written out rather than computed.
    assert ET.compute_nearest_tide_station(caspar, stations=stations) == {
        "nearest_tide_station_id": "0000000", "nearest_tide_station_dist_km": 0.0}
    # The override does not.
    got = ET.resolve_tide_station_override(caspar, ARENA_COVE, stations=stations)
    assert got["nearest_tide_station_id"] == "9416841", got


# --------------------------------------------------------------------------- #
# 2 — AN OVERRIDE BEYOND THE CAP IS HONOURED, NOT DROPPED                      #
# --------------------------------------------------------------------------- #

def test_an_override_past_the_distance_cap_is_returned_not_rejected():
    """The cap that governs the ALGORITHM does not govern an override, and that is the whole
    point: every spot here is past it by construction. Pinned against a station placed one
    degree of latitude north — 111.19 km, from EARTH_RADIUS_M * pi/180, not from haversine_m."""
    assert config.TIDE_STATION_MAX_DIST_KM == 50
    spot = {"name": "Caspar", "lat": 39.3611, "lng": -123.8178}
    far = [{"id": ARENA_COVE, "lat": 40.3611, "lng": -123.8178, "name": "far"}]
    # The algorithm drops it...
    assert ET.compute_nearest_tide_station(spot, stations=far) == {
        "nearest_tide_station_id": None, "nearest_tide_station_dist_km": None}
    # ...the override keeps it, with the true distance.
    assert ET.resolve_tide_station_override(spot, ARENA_COVE, stations=far) == {
        "nearest_tide_station_id": "9416841", "nearest_tide_station_dist_km": 111.19}


# The measured distances, read from the station file on a machine that has it. Written out
# here rather than imported so this file stands alone; test_override_distances.py owns them.
MENDOCINO_KM = {"Caspar": 50.5, "Jug Handle": 52.2, "Mackerricher": 64.5, "Ten Mile Beach": 69.6}


def test_the_import_guard_cannot_delete_an_over_cap_override():
    """THE CHECK THAT COULD HAVE SILENTLY REJECTED IT, and the reason it does not.

    db_import._validate_coord_derived recomputes the great-circle distance and NULLs the whole
    pairing when the stored value disagrees by more than COORD_DERIVED_DIST_TOLERANCE_KM, or
    when the true distance exceeds _COORD_DERIVED_SANE_CAP_KM. Neither fires: the sane cap is
    500 km, far above the 50-70 km these overrides sit at, and the stored value now IS the
    great-circle distance, so the tolerance branch compares a number against itself.

    THIS TEST USED TO ASSERT THE DISTANCE WAS NULL, and that was right for the state it was
    written in: the environment those commits were made in has no tide_stations.json, so a
    number would have been a guess, and a guess more than 5 km off would have deleted the
    assignment at import. Null was the safe placeholder. It is no longer a placeholder — the
    distances were measured — and the assertion follows the data rather than the other way
    round. Note the guard is now STRICTER than it was: a null skipped the tolerance branch
    entirely, a measured value passes it.
    """
    from pipeline import db_import
    assert config.COORD_DERIVED_DIST_TOLERANCE_KM == 5.0
    assert db_import._COORD_DERIVED_SANE_CAP_KM == 500.0
    by = _by_name()
    for n in MENDOCINO:
        km = by[n]["nearest_tide_station_dist_km"]
        assert km == MENDOCINO_KM[n], (n, km, MENDOCINO_KM[n])
        assert km > config.TIDE_STATION_MAX_DIST_KM, f"{n} is meant to exceed the cap"
        assert km < db_import._COORD_DERIVED_SANE_CAP_KM, n


# --------------------------------------------------------------------------- #
# 3 — the tile is suppressed while tide_norm survives                          #
# --------------------------------------------------------------------------- #

def _rate_one(spot, tide_series):
    forecast = [{"valid_time": "2026-09-09T18:00:00Z", "hs": 1.5, "tp": 12.0, "dp": 280.0,
                 "wind_speed": 2.0, "wind_dir": 90.0, "swell_hs": 1.5,
                 "swell_tp": 12.0, "swell_dp": 280.0}]
    return interpret.rate_spot(spot, forecast, tide_series)


# A tide series whose min/max and sample point are all written out. lookup_tide_norm reads
# {"min","max","points","source"} and interpolates; a single point at the exact hour makes the
# normalised value arithmetic anyone can check: (3.0 - 1.0) / (5.0 - 1.0) = 0.5.
from datetime import datetime  # noqa: E402

_TIDE_SERIES = {
    "min": 1.0, "max": 5.0, "source": "hourly",
    "points": [(datetime(2026, 9, 9, 11, 0), 3.0)],   # 18:00Z at lng -123.8 is 11:00 local
}


def test_a_suppressed_spot_publishes_tide_norm_but_no_tide_level():
    """THE SPLIT, END TO END. Same spot, same series, one flag — the rating input survives and
    the display value does not."""
    base = {"name": "Caspar", "lat": 39.3611, "lng": -123.8178, "tide_preference": "mid",
            "orientation_deg": 270.0, "offshore_wind_deg": 90.0, "optimal_swell_dir": 270.0}
    normal = _rate_one(dict(base), _TIDE_SERIES)[0]
    assert normal["tide_level_ft"] == 3.0, normal["tide_level_ft"]
    assert normal["tide_norm"] == 0.5, normal["tide_norm"]

    suppressed = _rate_one(dict(base, tide_height_suppressed=True), _TIDE_SERIES)[0]
    assert suppressed["tide_level_ft"] is None, suppressed["tide_level_ft"]
    assert suppressed["tide_norm"] == 0.5, "the rating input is untouched"
    # And the rating itself is bit-for-bit the same — the flag must not move a star.
    assert suppressed["tide_mult"] == normal["tide_mult"]
    assert suppressed["stars"] == normal["stars"]


def test_the_suppression_flag_is_only_read_when_present():
    """A spot without the flag is unaffected, which is what keeps this a per-spot decision
    rather than a behaviour change for the other 634."""
    base = {"name": "Elsewhere", "lat": 39.3611, "lng": -123.8178, "tide_preference": "mid",
            "orientation_deg": 270.0, "offshore_wind_deg": 90.0, "optimal_swell_dir": 270.0}
    assert _rate_one(dict(base), _TIDE_SERIES)[0]["tide_level_ft"] == 3.0
    assert _rate_one(dict(base, tide_height_suppressed=False), _TIDE_SERIES)[0]["tide_level_ft"] == 3.0


def test_the_committed_roster_carries_the_flag_on_exactly_the_four():
    by = _by_name()
    flagged = sorted(s["name"] for s in _roster() if s.get("tide_height_suppressed"))
    assert flagged == sorted(MENDOCINO), flagged
    for n in MENDOCINO:
        assert by[n]["tide_height_suppressed"] is True, n


def test_enrich_writes_the_flag_from_the_override_file():
    saved = {k: getattr(enrich, k) for k in
             ("load_land_index", "compute_nearest_tide_station", "compute_nearest_buoy",
              "compute_orientation", "compute_break_type")}
    saved_load = ET.load_tide_stations
    try:
        enrich.load_land_index = lambda: None
        enrich.compute_nearest_tide_station = lambda spot: {
            "nearest_tide_station_id": None, "nearest_tide_station_dist_km": None}
        enrich.compute_nearest_buoy = lambda spot: {
            "nearest_buoy_id": None, "nearest_buoy_dist_km": None,
            "fallback_buoy_ids": [], "buoy_confidence": 0.0}
        enrich.compute_orientation = lambda spot: {"orientation_deg": 270.0,
                                                   "orientation_confidence": 0.5}
        enrich.compute_break_type = lambda spot: {"break_type": "beach",
                                                  "break_type_confidence": 0.5}
        ET.load_tide_stations = lambda: [
            {"id": ARENA_COVE, "lat": 38.9019, "lng": -123.7038, "name": "Arena Cove"}]
        out = enrich._enrich_one({"name": "Caspar", "lat": 39.3611, "lng": -123.8178},
                                 skip_raycast=True)
    finally:
        for k, v in saved.items():
            setattr(enrich, k, v)
        ET.load_tide_stations = saved_load
    assert out["nearest_tide_station_id"] == "9416841", out.get("nearest_tide_station_id")
    assert out["tide_height_suppressed"] is True
    assert out["nearest_tide_station_source"] == "override"


# --------------------------------------------------------------------------- #
# 4 — the eight deliberate blanks stay blank                                   #
# --------------------------------------------------------------------------- #

def test_the_georgia_five_have_no_station_in_the_roster():
    by = _by_name()
    for n in GEORGIA:
        assert by[n]["nearest_tide_station_id"] is None, (n, by[n].get("nearest_tide_station_id"))
        assert by[n]["nearest_tide_station_dist_km"] is None, n
        assert by[n]["nearest_tide_station_source"] == "unassigned_override", n


def test_every_blank_is_PRESENT_and_null_never_absent():
    """FOUND BY THIS TEST, NOT BY READING. All eight blanks had the key ABSENT rather than
    null, and db_import's preserve-merge fills an absent column from the existing DB row —
    nearest_tide_station_id is in _COORD_DERIVED_FIELDS, but those are only dropped from the
    preserve on a COORD change, which none of these had. So an absent key would have let a
    stale station survive the very import meant to clear it. Present-and-null cannot."""
    by = _by_name()
    for n in GEORGIA:
        assert "nearest_tide_station_id" in by[n], f"{n}: key must be present, not absent"
        assert "nearest_tide_station_dist_km" in by[n], n
        assert by[n]["nearest_tide_station_id"] is None, n
    # And nothing else on the roster carries an absent key either.
    absent = [s["name"] for s in _roster() if "nearest_tide_station_id" not in s]
    assert absent == [], absent


def test_the_three_former_blanks_are_now_assigned_locally():
    """The inverse of what this test used to assert, and it fired the moment they moved —
    which is the point of pinning a blank rather than leaving it implicit."""
    by = _by_name()
    for n, sid in LOCAL.items():
        assert by[n]["nearest_tide_station_id"] == sid, (n, by[n].get("nearest_tide_station_id"))
        assert by[n]["nearest_tide_station_source"] == "override", n
        assert "tide_height_suppressed" not in by[n], \
            f"{n} is a LOCAL station — nothing to withhold"


def test_a_slug_in_BOTH_blocks_is_fatal():
    """The two blocks mean opposite things, so an entry in both is not an ambiguity to resolve
    by ordering — it is a decision the file does not make. Fatal rather than warned, because
    letting Algo 5c quietly win would look like it worked.

    Extracted from the import-time call site precisely so it can be reached: left inline it
    survived a mutation that deleted it outright, since nothing exercised it."""
    enrich.check_tide_override_conflicts({"a": {}}, {"b"})          # disjoint: fine
    enrich.check_tide_override_conflicts({}, set())                 # empty: fine
    try:
        enrich.check_tide_override_conflicts({"caspar": {}, "x": {}}, {"caspar", "y"})
    except ValueError as e:
        assert "caspar" in str(e), e
        assert "opposite things" in str(e), e
        assert "'x'" not in str(e) and "'y'" not in str(e), "only the conflicts are named"
    else:
        raise AssertionError("a slug in both blocks must raise")
    # ...and the committed file passes it, which is what the import-time call asserts.
    enrich.check_tide_override_conflicts(enrich._SPOT_TIDE_STATIONS, enrich._SPOT_TIDE_UNASSIGNED)


def test_the_conflict_check_actually_RUNS_at_import():
    """A pure function nobody calls is a comment. Deleting the import-time call left every
    other test passing — the check still worked, it just never ran — so this drives a real
    import against a doctored file.

    Reload rather than a source grep: a grep asserts the line exists, this asserts the module
    refuses to load. The file is restored and the module reloaded again in the finally, so the
    rest of this module sees the committed state."""
    import importlib
    import tempfile

    tmp = Path(tempfile.mkdtemp()) / "conflict.json"
    # "caspar" in BOTH blocks — assign this station, and assign nothing.
    tmp.write_text(json.dumps({
        "stations": {"caspar": {"station_id": "9416841"}},
        "unassigned": {"caspar": {"reason": ["deliberately blank"]}},
    }))
    saved = config.SPOT_TIDE_STATIONS_FILE
    try:
        config.SPOT_TIDE_STATIONS_FILE = tmp
        try:
            importlib.reload(enrich)
        except ValueError as e:
            assert "caspar" in str(e) and "BOTH" in str(e), e
        else:
            raise AssertionError("importing enrich with a conflicting file must raise")
    finally:
        config.SPOT_TIDE_STATIONS_FILE = saved
        importlib.reload(enrich)
    # Back to the committed state, and the real file still passes the check.
    assert "caspar" in enrich._SPOT_TIDE_STATIONS
    assert "caspar" not in enrich._SPOT_TIDE_UNASSIGNED
    assert len(enrich._SPOT_TIDE_UNASSIGNED) == 3


def test_an_override_WITHOUT_suppress_height_does_not_get_the_flag():
    """Kalaloch is the control: a hand-set station whose HEIGHT is trustworthy, because Point
    Grenville is 32.7 km away on the same coast rather than 71. The flag must be per-entry, or
    every override silently loses its tide tile."""
    doc = _override_doc()
    assert "suppress_height" not in doc["stations"]["kalaloch-beach"], \
        "the control entry must not carry the flag"
    saved = {k: getattr(enrich, k) for k in
             ("load_land_index", "compute_nearest_tide_station", "compute_nearest_buoy",
              "compute_orientation", "compute_break_type")}
    saved_load = ET.load_tide_stations
    try:
        enrich.load_land_index = lambda: None
        enrich.compute_nearest_tide_station = lambda spot: {
            "nearest_tide_station_id": None, "nearest_tide_station_dist_km": None}
        enrich.compute_nearest_buoy = lambda spot: {
            "nearest_buoy_id": None, "nearest_buoy_dist_km": None,
            "fallback_buoy_ids": [], "buoy_confidence": 0.0}
        enrich.compute_orientation = lambda spot: {"orientation_deg": 270.0,
                                                   "orientation_confidence": 0.5}
        enrich.compute_break_type = lambda spot: {"break_type": "beach",
                                                  "break_type_confidence": 0.5}
        ET.load_tide_stations = lambda: [
            {"id": "9441627", "lat": 47.30, "lng": -124.27, "name": "Point Grenville"}]
        out = enrich._enrich_one({"name": "Kalaloch Beach", "lat": 47.5897, "lng": -124.3683},
                                 skip_raycast=True)
    finally:
        for k, v in saved.items():
            setattr(enrich, k, v)
        ET.load_tide_stations = saved_load
    assert out["nearest_tide_station_id"] == "9441627"
    assert "tide_height_suppressed" not in out, \
        "an override without suppress_height must keep its tide tile"


def test_the_unassigned_block_lists_the_georgia_five_and_no_slug_is_in_both():
    doc = _override_doc()
    loaded = enrich._load_spot_tide_unassigned()
    for slug in GEORGIA_SLUGS:
        assert slug in doc["unassigned"], slug
        assert slug in loaded, slug
    assert "_comment" not in loaded, "the prose key is not a slug"
    assert len(loaded) == 3, sorted(loaded)
    # ...and everything that moved is gone from it entirely, not left in both blocks.
    for slug in ("key-west", "captiva", "reid-state-park",
                 "st-simons-island", "jekyll-island"):
        assert slug not in loaded, slug
        assert slug not in doc["unassigned"], slug
    # The two blocks mean opposite things; a slug in both is a decision the file does not make.
    assert set(enrich._SPOT_TIDE_STATIONS) & loaded == set()


def test_a_deliberate_blank_CLEARS_a_station_the_algorithm_assigned():
    """THE POINT OF THE BLOCK. Without it, a refreshed station file or a raised cap hands the
    spot a station and the recorded judgement is silently overturned. Jekyll Island is the live
    case was Jekyll Island, which missed the 50 km cap by at most a kilometre — and which has
    since been ASSIGNED, because reading the station file turned up 8677344 at 8.3 km. This
    test moved to Blackbeard Island, which stays blank on measured grounds: its only reference
    station solves to 31.5747, -81.1893, up the South Newport River inland of the barrier
    chain, and no cap change makes that the right station.

    Algo 5 is stubbed to return a station — what a raised cap would produce — and the run must
    end with none, loudly."""
    import logging

    class _Cap(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, record):
            self.records.append(record)

    cap = _Cap()
    lg = logging.getLogger("pipeline.enrich")
    lvl = lg.level
    saved = {k: getattr(enrich, k) for k in
             ("load_land_index", "compute_nearest_tide_station", "compute_nearest_buoy",
              "compute_orientation", "compute_break_type")}
    try:
        lg.addHandler(cap)
        lg.setLevel(logging.INFO)
        enrich.load_land_index = lambda: None
        enrich.compute_nearest_tide_station = lambda spot: {
            "nearest_tide_station_id": "8720086", "nearest_tide_station_dist_km": 49.5}
        enrich.compute_nearest_buoy = lambda spot: {
            "nearest_buoy_id": None, "nearest_buoy_dist_km": None,
            "fallback_buoy_ids": [], "buoy_confidence": 0.0}
        enrich.compute_orientation = lambda spot: {"orientation_deg": 90.0,
                                                   "orientation_confidence": 0.5}
        enrich.compute_break_type = lambda spot: {"break_type": "beach",
                                                  "break_type_confidence": 0.5}
        out = enrich._enrich_one({"name": "Blackbeard Island", "lat": 31.5010, "lng": -81.1910},
                                 skip_raycast=True)
    finally:
        for k, v in saved.items():
            setattr(enrich, k, v)
        lg.removeHandler(cap)
        lg.setLevel(lvl)
    assert out["nearest_tide_station_id"] is None, out.get("nearest_tide_station_id")
    assert out["nearest_tide_station_dist_km"] is None
    assert out["nearest_tide_station_source"] == "unassigned_override"
    assert out["enrichment_confidence"]["nearest_tide_station"] == 0.0
    msgs = " ".join(r.getMessage() for r in cap.records)
    assert "8720086" in msgs and "UNASSIGNED" in msgs, msgs
    assert "Blackbeard Island" in msgs, msgs


def test_clearing_is_SILENT_when_the_algorithm_already_returned_nothing():
    """The normal case today. A warning that fires every run on eight spots is a warning
    nobody reads, and the signal here is specifically 'the world changed under this entry'."""
    import logging

    class _Cap(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, record):
            self.records.append(record)

    cap = _Cap()
    lg = logging.getLogger("pipeline.enrich")
    lvl = lg.level
    saved = {k: getattr(enrich, k) for k in
             ("load_land_index", "compute_nearest_tide_station", "compute_nearest_buoy",
              "compute_orientation", "compute_break_type")}
    try:
        lg.addHandler(cap)
        lg.setLevel(logging.INFO)
        enrich.load_land_index = lambda: None
        enrich.compute_nearest_tide_station = lambda spot: {
            "nearest_tide_station_id": None, "nearest_tide_station_dist_km": None}
        enrich.compute_nearest_buoy = lambda spot: {
            "nearest_buoy_id": None, "nearest_buoy_dist_km": None,
            "fallback_buoy_ids": [], "buoy_confidence": 0.0}
        enrich.compute_orientation = lambda spot: {"orientation_deg": 90.0,
                                                   "orientation_confidence": 0.5}
        enrich.compute_break_type = lambda spot: {"break_type": "beach",
                                                  "break_type_confidence": 0.5}
        out = enrich._enrich_one({"name": "Blackbeard Island", "lat": 31.5010, "lng": -81.1910},
                                 skip_raycast=True)
    finally:
        for k, v in saved.items():
            setattr(enrich, k, v)
        lg.removeHandler(cap)
        lg.setLevel(lvl)
    assert out["nearest_tide_station_id"] is None
    assert "UNASSIGNED" not in " ".join(r.getMessage() for r in cap.records)


# --------------------------------------------------------------------------- #
# 5 — the standard the decisions were judged against                           #
# --------------------------------------------------------------------------- #

def test_no_tide_scores_the_NEUTRAL_TOP_of_the_multiplier_range():
    """The fact the whole decision rests on, pinned so it cannot drift under the file.

    tide_multiplier returns 1.0 for an absent tide, which is the MAXIMUM of its range — not a
    midpoint. So a wrong tide can only move a rating DOWN, and "no station" is the correct
    answer rather than a gap. Every value here is written out."""
    assert interpret.tide_multiplier(None, "mid") == 1.0
    assert interpret.tide_multiplier(None, "low") == 1.0
    assert interpret.tide_multiplier(None, "high") == 1.0
    # ...and the range it is the top of.
    assert interpret.tide_multiplier(0.5, "mid") == 1.0
    assert interpret.tide_multiplier(0.9, "mid") == 0.7
    assert interpret.tide_multiplier(0.1, "low") == 1.0
    assert interpret.tide_multiplier(0.9, "low") == 0.6
    assert interpret.tide_multiplier(0.9, "high") == 1.0
    assert interpret.tide_multiplier(0.1, "high") == 0.6


def test_a_phase_error_can_invert_the_verdict():
    """Why phase, not range, is the thing that has to be right. Two tide_norm values a few
    hours apart on a semidiurnal curve, same preference, opposite ends of the multiplier."""
    assert interpret.tide_multiplier(0.05, "low") == 1.0    # near low water: ideal
    assert interpret.tide_multiplier(0.95, "low") == 0.6    # near high water: worst
    # A 0.4 swing in tide_norm is roughly three hours on a 12.4-hour cycle.
    assert interpret.tide_multiplier(0.5, "low") == 0.8


def test_every_spot_in_the_file_matches_a_roster_slug():
    """A slug that resolves to nothing is a silent no-op — the same failure validate_factor_
    slugs exists to prevent for face factors. Both blocks are checked."""
    slugs = {enrich._slug_for(s.get("name")) for s in _roster()}
    doc = _override_doc()
    for block in ("stations", "unassigned"):
        for slug in doc[block]:
            if slug == "_comment":
                continue
            assert slug in slugs, f"{block}/{slug} matches no spot on the roster"


def test_the_file_records_WHY_for_every_entry():
    """A future reader re-runs the assignment, gets NULL again for the four and a station for
    Jekyll, and needs to know which outcome is expected. That sentence is load-bearing."""
    doc = _override_doc()
    for slug in MENDOCINO_SLUGS:
        reason = " ".join(doc["stations"][slug]["reason"]).lower()
        assert "caspar" in reason or "open" in reason, slug
    caspar = " ".join(doc["stations"]["caspar"]["reason"]).lower()
    assert "phase" in caspar and "range" in caspar, "must say which quantity the rating uses"
    assert "expected outcome" in caspar, "must tell a re-runner that NULL is expected"
    assert "suppress" in caspar or "height" in caspar
    for slug in GEORGIA_SLUGS:
        assert doc["unassigned"][slug]["reason"], slug
    hdr = " ".join(doc["unassigned"]["_comment"]).lower()
    # THE STANDARD, IN THREE PARTS. A mutation that deleted the consequence while leaving the
    # claim passed an earlier version of this check, so all three are pinned: the value, why
    # it is the neutral end, and what follows from that.
    assert "1.0" in hdr, "the value tide_mult takes with no tide"
    assert "neutral top" in hdr, "...and that it is the TOP of the range, not a midpoint"
    assert "worse than none" in hdr, "...and the conclusion that follows"
    assert "only move the rating down" in hdr, "...and why: the error is one-directional"
    assert "phase error" in hdr and "invert" in hdr, "...and how a wrong tide goes wrong"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\ntest_tide_override_mendocino: {len(fns)} PASS")


if __name__ == "__main__":
    _run_all()
