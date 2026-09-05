"""Kalaloch Beach is on Point Grenville, and stays there.

WHY THIS FILE EXISTS. Kalaloch Beach was the only spot on the roster with a tide station
assigned and no tide. Its nearest station, TWC0965 (Destruction Island, 12.1 km), returns a
full hilo series when called from a developer machine and fails from the GitHub Actions runner
every run — the tide stage marks it known-bad and skips it. So the assignment is geometrically
correct and operationally useless, and no amount of fixing the fetcher changes that.

The station was reassigned to Point Grenville (9441627, ~32.7 km). THE POINT OF THESE TESTS IS
THAT IT STAYS REASSIGNED. nearest_tide_station_id is rewritten from
compute_nearest_tide_station by enrich.py on EVERY run, so before this change there was nowhere
to record the decision: an edit to spots_enriched.json or to the DB row was gone the next time
enrich ran, and enrich reads spots_enriched.json as its own input, so the edit was read in and
overwritten in the same pass. spot_tide_stations.json is the durable channel, mirroring
spot_orientations.json for orientation and spot_swell_windows.json for the swell window.

EVERY EXPECTED VALUE IS A LITERAL. Station ids and slugs are written out; the one distance
literal is derived from EARTH_RADIUS_M and pi, not from haversine_m; and the "nothing else
moved" digest is computed from origin/main's committed roster, i.e. the state BEFORE this
change, never from the file the test is checking.

Run: python -m pipeline.tests.test_tide_station_override
"""
from __future__ import annotations

import hashlib
import json
import logging
import math

from pipeline import config, enrich
from pipeline.enrichment import tides as ET
from pipeline.geo import EARTH_RADIUS_M

# Kalaloch Beach's coordinates, copied from pipeline/spots_enriched.json.
KALALOCH = {"name": "Kalaloch Beach", "lat": 47.5897279, "lng": -124.3683486}

# The two stations, both written out by hand.
NEAREST = "TWC0965"      # Destruction Island — 12.1 km, works locally, fails from CI
CHOSEN = "9441627"       # Point Grenville — ~32.7 km, numeric primary station


def _stations(*, nearest_at_spot=True):
    """A two-station world in which TWC0965 is unambiguously the nearest.

    TWC0965 sits ON the spot (distance exactly 0.0, no arithmetic needed to know that) so the
    fixture cannot be accused of making the override win by accident — any override that
    resolves to 9441627 has beaten a station at zero distance."""
    return [
        {"id": NEAREST, "lat": KALALOCH["lat"] if nearest_at_spot else 40.0,
         "lng": KALALOCH["lng"] if nearest_at_spot else -120.0, "name": "Destruction Island"},
        {"id": CHOSEN, "lat": KALALOCH["lat"] + 1.0, "lng": KALALOCH["lng"],
         "name": "Point Grenville"},
    ]


class _CaptureLog(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


# --------------------------------------------------------------------------- #
# 1 — the resolver picks the NAMED station, not the nearest                    #
# --------------------------------------------------------------------------- #

def test_the_fixture_really_does_make_TWC0965_the_nearest():
    """Guards the fixture. If the algorithm did not prefer TWC0965 here, every test below
    would pass without the override doing any work at all."""
    got = ET.compute_nearest_tide_station(KALALOCH, stations=_stations())
    assert got["nearest_tide_station_id"] == "TWC0965"
    assert got["nearest_tide_station_dist_km"] == 0.0, "co-located station is exactly 0 km"


def test_the_override_resolves_to_point_grenville_over_the_nearer_station():
    got = ET.resolve_tide_station_override(KALALOCH, CHOSEN, stations=_stations())
    assert got["nearest_tide_station_id"] == "9441627", got
    assert got["nearest_tide_station_id"] != "TWC0965"


def test_the_distance_is_COMPUTED_from_the_station_file_not_declared():
    """The load-bearing property. db_import._validate_coord_derived recomputes the great-circle
    distance and NULLs the WHOLE pairing — id and distance both — when the stored value
    disagrees by more than COORD_DERIVED_DIST_TOLERANCE_KM. A distance carried in the override
    file would therefore delete the override at import, one stage after it appeared to work.

    The expected value is derived from the earth radius and pi, NOT by calling haversine_m:
    the fixture station is exactly 1 degree of latitude north, and a pure meridian arc of one
    degree is R * pi/180 metres.
    """
    one_degree_km = EARTH_RADIUS_M * math.pi / 180.0 / 1000.0
    assert round(one_degree_km, 2) == 111.19, one_degree_km    # 6_371_000 * pi/180 = 111194.93 m
    got = ET.resolve_tide_station_override(KALALOCH, CHOSEN, stations=_stations())
    assert got["nearest_tide_station_dist_km"] == 111.19, got


def test_an_unresolvable_override_KEEPS_the_id_with_a_null_distance():
    """Discarding would silently restore the algorithm's answer — the exact revert this channel
    exists to prevent. Keeping the id routes the spot into
    forecast.tides.unresolvable_station_ids, which names it and every spot on it. The null
    distance matters too: db_import's check skips a record whose station it cannot find, and a
    null stored distance can never trip the NULLing rule."""
    cap = _CaptureLog()
    logging.getLogger("pipeline.enrichment.tides").addHandler(cap)
    try:
        got = ET.resolve_tide_station_override(KALALOCH, "9999999", stations=_stations())
    finally:
        logging.getLogger("pipeline.enrichment.tides").removeHandler(cap)
    assert got == {"nearest_tide_station_id": "9999999", "nearest_tide_station_dist_km": None}, got
    assert any("9999999" in r.getMessage() for r in cap.records), "must be loud"


def test_the_id_match_is_EXACT():
    """TWC0965 is not TWC0965F. A near-match that resolved would hand CO-OPS an id it does not
    know and the spot would go tideless with no error, because the request is still well-formed
    — the failure this whole tide investigation started from."""
    stations = [{"id": "TWC0965F", "lat": 47.6, "lng": -124.4, "name": "x"}]
    got = ET.resolve_tide_station_override(KALALOCH, "TWC0965", stations=stations)
    assert got["nearest_tide_station_dist_km"] is None, "TWC0965 must NOT match TWC0965F"
    assert got["nearest_tide_station_id"] == "TWC0965"
    # ...and the exact spelling does resolve.
    got2 = ET.resolve_tide_station_override(KALALOCH, "TWC0965F", stations=stations)
    assert got2["nearest_tide_station_dist_km"] is not None


def test_a_numeric_station_id_in_the_file_still_matches_a_string_override():
    """tide_stations.json may hold ids as JSON numbers. Comparison is on str() of both sides."""
    got = ET.resolve_tide_station_override(
        KALALOCH, "9441627",
        stations=[{"id": 9441627, "lat": KALALOCH["lat"], "lng": KALALOCH["lng"], "name": "PG"}])
    assert got == {"nearest_tide_station_id": "9441627", "nearest_tide_station_dist_km": 0.0}, got


def test_the_override_is_NOT_capped_at_the_automatic_distance_limit():
    """compute_nearest_tide_station drops anything past TIDE_STATION_MAX_DIST_KM. An override is
    a deliberate departure from that rule, and capping it would break the channel for the one
    case it exists for — a further station that actually works."""
    assert config.TIDE_STATION_MAX_DIST_KM == 50
    far = [{"id": CHOSEN, "lat": KALALOCH["lat"] + 1.0, "lng": KALALOCH["lng"], "name": "PG"}]
    # 111.19 km, well past the 50 km cap — and still returned.
    assert ET.resolve_tide_station_override(KALALOCH, CHOSEN, stations=far) == {
        "nearest_tide_station_id": "9441627", "nearest_tide_station_dist_km": 111.19}
    # The algorithm, on the same input, returns nothing — which is the difference being pinned.
    assert ET.compute_nearest_tide_station(KALALOCH, stations=far) == {
        "nearest_tide_station_id": None, "nearest_tide_station_dist_km": None}


# --------------------------------------------------------------------------- #
# 2 — the override FILE says Point Grenville, and says why                     #
# --------------------------------------------------------------------------- #

def test_the_override_file_assigns_kalaloch_to_9441627():
    data = json.loads(config.SPOT_TIDE_STATIONS_FILE.read_text())
    entry = data["stations"]["kalaloch-beach"]
    assert entry["station_id"] == "9441627", entry
    assert entry["replaces"] == "TWC0965", entry


def test_the_slug_the_file_is_keyed_by_is_the_slug_enrich_computes():
    """The file is keyed by slug and enrich looks it up by _slug_for(name). If those disagree
    the override is a silent no-op — it would not error, the spot would just keep the nearest
    station and nobody would know."""
    assert enrich._slug_for("Kalaloch Beach") == "kalaloch-beach"


def test_the_file_records_WHY_so_a_future_reader_does_not_undo_it():
    """A future reader re-runs the assignment, gets the nearer station, and concludes the
    override is stale — unless the entry says that getting something nearer is the EXPECTED
    outcome. That sentence is load-bearing, so its substance is pinned, not just its presence."""
    data = json.loads(config.SPOT_TIDE_STATIONS_FILE.read_text())
    entry = data["stations"]["kalaloch-beach"]
    reason = " ".join(entry["reason"]).lower()
    assert "nearer" in reason, "must say the replaced station is NEARER, not broken-and-replaced"
    assert "github" in reason and "laptop" in reason, "must name the CI/local asymmetry"
    header = " ".join(data["_comment"]).lower()
    assert "deliberate departure" in header
    assert "not a correction" in header


def test_the_loader_returns_the_entry_keyed_by_slug():
    loaded = enrich._load_spot_tide_stations()
    assert loaded["kalaloch-beach"]["station_id"] == "9441627", loaded.get("kalaloch-beach")


def test_the_loader_drops_an_entry_with_no_usable_station_id(tmp_path=None):
    """A null id must be DROPPED, not written through. Writing None would read as "this spot has
    no tide station" and be indistinguishable from the algorithm's own out-of-range answer."""
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp()) / "ov.json"
    tmp.write_text(json.dumps({"stations": {
        "good": {"station_id": "9441627"},
        "nulled": {"station_id": None},
        "blank": {"station_id": "  "},
        "notadict": "nope",
    }}))
    saved = enrich.SPOT_TIDE_STATIONS_FILE
    try:
        enrich.SPOT_TIDE_STATIONS_FILE = tmp
        loaded = enrich._load_spot_tide_stations()
    finally:
        enrich.SPOT_TIDE_STATIONS_FILE = saved
    assert sorted(loaded) == ["good"], loaded


def test_a_corrupt_or_missing_override_file_degrades_to_no_overrides():
    import tempfile
    from pathlib import Path
    tmp = Path(tempfile.mkdtemp())
    saved = enrich.SPOT_TIDE_STATIONS_FILE
    try:
        enrich.SPOT_TIDE_STATIONS_FILE = tmp / "absent.json"
        assert enrich._load_spot_tide_stations() == {}
        bad = tmp / "bad.json"
        bad.write_text("{not json")
        enrich.SPOT_TIDE_STATIONS_FILE = bad
        assert enrich._load_spot_tide_stations() == {}
    finally:
        enrich.SPOT_TIDE_STATIONS_FILE = saved


# --------------------------------------------------------------------------- #
# 3 — THE OVERRIDE SURVIVES THE THING THAT WRITES nearest_tide_station_id      #
# --------------------------------------------------------------------------- #

def test_the_override_survives_algo_5_which_rewrites_the_field_every_run():
    """END TO END through _enrich_one, which is the ONLY writer that authors this field.

    Algo 5 is stubbed to return TWC0965 — what the real algorithm returns for these coordinates
    — so this pins the ORDERING: Algo 5b runs after it and wins. Before spot_tide_stations.json
    existed there was no ordering to pin, because there was nothing for Algo 5 to lose to.

    The heavy algorithms are stubbed or allowed to fail: every one of them is wrapped in
    try/except in _enrich_one, and none of them touches the tide field.
    """
    saved = {k: getattr(enrich, k) for k in
             ("load_land_index", "compute_nearest_tide_station", "compute_nearest_buoy",
              "compute_orientation", "compute_break_type")}
    saved_load = ET.load_tide_stations
    try:
        enrich.load_land_index = lambda: None                       # no GSHHG in a test env
        enrich.compute_nearest_tide_station = lambda spot: {
            "nearest_tide_station_id": "TWC0965", "nearest_tide_station_dist_km": 12.14}
        enrich.compute_nearest_buoy = lambda spot: {
            "nearest_buoy_id": None, "nearest_buoy_dist_km": None,
            "fallback_buoy_ids": [], "buoy_confidence": 0.0}
        enrich.compute_orientation = lambda spot: {"orientation_deg": 260.0,
                                                   "orientation_confidence": 0.5}
        enrich.compute_break_type = lambda spot: {"break_type": "beach",
                                                  "break_type_confidence": 0.5}
        ET.load_tide_stations = lambda: _stations()
        out = enrich._enrich_one(dict(KALALOCH), skip_raycast=True)
    finally:
        for k, v in saved.items():
            setattr(enrich, k, v)
        ET.load_tide_stations = saved_load

    assert out["nearest_tide_station_id"] == "9441627", out.get("nearest_tide_station_id")
    assert out["nearest_tide_station_dist_km"] == 111.19, out.get("nearest_tide_station_dist_km")
    assert out["nearest_tide_station_source"] == "override"
    assert out["enrichment_confidence"]["nearest_tide_station"] == 1.0


def _enrich_with(station_lat_offset, capture=True):
    """Run _enrich_one on Kalaloch with the override station placed *station_lat_offset* degrees
    of latitude north of it, returning (record, captured log messages)."""
    cap = _CaptureLog()
    lg = logging.getLogger("pipeline.enrich")
    lvl = lg.level
    saved = {k: getattr(enrich, k) for k in
             ("load_land_index", "compute_nearest_tide_station", "compute_nearest_buoy",
              "compute_orientation", "compute_break_type")}
    saved_load = ET.load_tide_stations
    try:
        lg.addHandler(cap)
        lg.setLevel(logging.DEBUG)
        enrich.load_land_index = lambda: None
        enrich.compute_nearest_tide_station = lambda spot: {
            "nearest_tide_station_id": "TWC0965", "nearest_tide_station_dist_km": 12.14}
        enrich.compute_nearest_buoy = lambda spot: {
            "nearest_buoy_id": None, "nearest_buoy_dist_km": None,
            "fallback_buoy_ids": [], "buoy_confidence": 0.0}
        enrich.compute_orientation = lambda spot: {"orientation_deg": 260.0,
                                                   "orientation_confidence": 0.5}
        enrich.compute_break_type = lambda spot: {"break_type": "beach",
                                                  "break_type_confidence": 0.5}
        ET.load_tide_stations = lambda: [
            {"id": CHOSEN, "lat": KALALOCH["lat"] + station_lat_offset,
             "lng": KALALOCH["lng"], "name": "Point Grenville"}]
        out = enrich._enrich_one(dict(KALALOCH), skip_raycast=True)
    finally:
        for k, v in saved.items():
            setattr(enrich, k, v)
        ET.load_tide_stations = saved_load
        lg.removeHandler(cap)
        lg.setLevel(lvl)
    return out, " ".join(r.getMessage() for r in cap.records)


def test_the_expect_km_tripwire_warns_when_the_id_names_a_different_station():
    """The one check on the override file's own honesty. `expect_km` documents the distance its
    author measured; if the id actually resolves somewhere else, the entry is describing a
    station it does not name — the class of error nothing else here would catch, since a wrong
    but resolvable id produces a perfectly consistent record."""
    assert config.TIDE_STATION_OVERRIDE_DIST_TOLERANCE_KM == 5.0
    # 1 degree north = 111.19 km against a documented 32.7 — far outside the 5 km tolerance.
    out, msgs = _enrich_with(1.0)
    assert "may not be the station the entry describes" in msgs, msgs
    assert "32.7" in msgs and "111.2" in msgs, msgs
    # WARNED, NEVER DROPPED: the computed distance is the true one and still wins.
    assert out["nearest_tide_station_id"] == "9441627"
    assert out["nearest_tide_station_dist_km"] == 111.19, out.get("nearest_tide_station_dist_km")


def test_the_tripwire_stays_quiet_when_the_computed_distance_matches_the_entry():
    """A warning that fires on the healthy case trains the reader to ignore it. 0.3 degrees of
    latitude is 33.36 km (EARTH_RADIUS_M * 0.3 * pi/180), within 5 km of the documented 32.7."""
    thirty_three = EARTH_RADIUS_M * 0.3 * math.pi / 180.0 / 1000.0
    assert round(thirty_three, 2) == 33.36, thirty_three
    out, msgs = _enrich_with(0.3)
    assert "may not be the station" not in msgs, msgs
    assert "past the" not in msgs, "33.4 km is inside the 50 km cap — no cap warning either"
    assert out["nearest_tide_station_dist_km"] == 33.36, out.get("nearest_tide_station_dist_km")


def test_an_override_past_the_automatic_cap_is_applied_but_never_silent():
    """Deliberately allowed — a further station that works is the whole point — but a 60 km
    assignment appearing with no trace in the log is how a bad override would live forever."""
    # 0.55 degrees = 61.16 km: past the 50 km cap, and its gap from the documented 32.7 also
    # exceeds the tripwire, so the more specific "wrong station" warning is the one that fires.
    out, msgs = _enrich_with(0.55)
    assert out["nearest_tide_station_dist_km"] == 61.16, out.get("nearest_tide_station_dist_km")
    assert "may not be the station the entry describes" in msgs, msgs


def test_a_spot_with_no_override_entry_keeps_the_algorithms_answer():
    """The control. Algo 5b must touch ONLY the spots named in the file — if it rewrote or
    stamped every spot, the 'no other spot changed' guarantee below would be worthless."""
    saved = {k: getattr(enrich, k) for k in
             ("load_land_index", "compute_nearest_tide_station", "compute_nearest_buoy",
              "compute_orientation", "compute_break_type")}
    try:
        enrich.load_land_index = lambda: None
        enrich.compute_nearest_tide_station = lambda spot: {
            "nearest_tide_station_id": "9441627", "nearest_tide_station_dist_km": 11.09}
        enrich.compute_nearest_buoy = lambda spot: {
            "nearest_buoy_id": None, "nearest_buoy_dist_km": None,
            "fallback_buoy_ids": [], "buoy_confidence": 0.0}
        enrich.compute_orientation = lambda spot: {"orientation_deg": 260.0,
                                                   "orientation_confidence": 0.5}
        enrich.compute_break_type = lambda spot: {"break_type": "beach",
                                                  "break_type_confidence": 0.5}
        out = enrich._enrich_one(
            {"name": "Pacific Beach WA", "lat": 47.210153, "lng": -124.211426},
            skip_raycast=True)
    finally:
        for k, v in saved.items():
            setattr(enrich, k, v)
    assert out["nearest_tide_station_id"] == "9441627"
    assert out["nearest_tide_station_dist_km"] == 11.09, "the algorithm's own distance, untouched"
    assert "nearest_tide_station_source" not in out, "no stamp on a spot with no override"


# --------------------------------------------------------------------------- #
# 4 — the committed roster, and NOTHING ELSE MOVED                             #
# --------------------------------------------------------------------------- #
# The forecast workflow does NOT run pipeline.enrich — it goes download_geodata -> fetch_all ->
# interpret -> db_import, reading the committed spots_enriched.json. So the roster carries the
# change until someone re-enriches, and Algo 5b is what makes it survive that re-enrich.

def _roster():
    return json.loads(config.DEFAULT_ENRICHED_OUTPUT.read_text())


def test_the_committed_roster_puts_kalaloch_on_9441627():
    hits = [s for s in _roster() if s.get("name") == "Kalaloch Beach"]
    assert len(hits) == 1, f"expected exactly one Kalaloch Beach, got {len(hits)}"
    assert hits[0]["nearest_tide_station_id"] == "9441627", hits[0].get("nearest_tide_station_id")


def test_the_rosters_distance_is_null_so_the_import_guard_cannot_delete_the_pairing():
    """db_import._validate_coord_derived NULLs the pairing when the stored distance disagrees
    with the great-circle distance by more than COORD_DERIVED_DIST_TOLERANCE_KM. Its check is
    `stored is not None and abs(gc - stored) > tol`, so a null distance is skipped and the id
    survives. The true distance was not measurable where this edit was made — tide_stations.json
    is a gitignored download and the CO-OPS API is unreachable from that environment — and a
    guessed number that turned out to be more than 5 km off would have silently deleted the
    whole assignment at import. Algo 5b fills it in on the next enrich."""
    assert config.COORD_DERIVED_DIST_TOLERANCE_KM == 5.0
    kal = next(s for s in _roster() if s.get("name") == "Kalaloch Beach")
    assert kal["nearest_tide_station_dist_km"] is None, kal.get("nearest_tide_station_dist_km")
    assert "nearest_tide_station_dist_km" in kal, \
        "the key must be PRESENT-and-null, not absent: db_import preserves absent keys from the DB"


def test_no_other_spot_changed_station():
    """EXHAUSTIVE, against the state before this change.

    The digest covers (name, nearest_tide_station_id) for all 647 non-Kalaloch spots and was
    computed from origin/main's committed spots_enriched.json — an independent source, not the
    file under test and not any function in this repository.

        git show origin/main:pipeline/spots_enriched.json
        sha256 over sorted "name\\tstation_id" lines, excluding Kalaloch Beach
    """
    roster = _roster()
    assert len(roster) == 648, len(roster)
    pairs = sorted((str(s.get("name")), str(s.get("nearest_tide_station_id")))
                   for s in roster if s.get("name") != "Kalaloch Beach")
    assert len(pairs) == 647, len(pairs)
    digest = hashlib.sha256("\n".join(f"{a}\t{b}" for a, b in pairs).encode()).hexdigest()
    assert digest == "444e597bfba1e64d81c5756cf7322787176304e3d5c02a8260be95b4592c891e", digest


def test_TWC0965_is_off_the_roster_and_point_grenville_now_serves_two_spots():
    """Stated as counts so the consequence is legible: the fetcher stops requesting TWC0965
    entirely (nothing points at it), and 9441627 — already in service for Pacific Beach WA
    since the roster was built — picks up a second spot."""
    ids = [s.get("nearest_tide_station_id") for s in _roster()]
    assert ids.count("TWC0965") == 0
    assert ids.count("9441627") == 2
    assert len({i for i in ids if i}) == 233, "one fewer distinct station than before (was 234)"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\ntest_tide_station_override: {len(fns)} PASS")


if __name__ == "__main__":
    _run_all()
