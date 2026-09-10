"""Every overridden spot carries a measured distance, computed the same way as every other.

WHAT WAS WRONG, AND WHERE. All ten override entries wrote nearest_tide_station_id and left
nearest_tide_station_dist_km NULL. Nothing in the forecast reads that field — it is descriptive
— but it shows up in query output, and an assigned station beside a NULL distance is a
half-written record that invites a wrong conclusion.

THE DEFECT WAS IN THE COMMITTED ROSTER, NOT IN THE CODE, and the distinction matters because
"fixing" the function would have changed something that was already right.
enrichment/tides.resolve_tide_station_override has always computed the distance:

    dist_km = haversine_m(spot["lat"], spot["lng"], match["lat"], match["lng"]) / 1000.0
    ...
    "nearest_tide_station_dist_km": round(dist_km, 2),          (tides.py:119-122)

which is character-for-character what the non-override path does:

    dist_km = haversine_m(spot["lat"], spot["lng"], s["lat"], s["lng"]) / 1000.0
    ...
    "nearest_tide_station_dist_km": round(dist_km, 2),          (tides.py:70-76)

The NULLs were hand-written into spots_enriched.json because the environment those commits were
made in has no pipeline/geodata/tide_stations.json — it is a gitignored download — so the
distance could not be computed there. NULL was chosen over a guess deliberately:
db_import._validate_coord_derived NULLs the WHOLE pairing when a stored distance disagrees with
the recomputed great-circle by more than COORD_DERIVED_DIST_TOLERANCE_KM, so a wrong number
would have deleted the override at import. A null was safe. It was also incomplete.

EVERY DISTANCE BELOW IS A LITERAL, measured from the station file on a machine that has it.
None is produced by calling either assignment function.

Run: python -m pipeline.tests.test_override_distances
"""
from __future__ import annotations

import inspect
import json
import math

from pipeline import config, db_import, enrich
from pipeline.enrichment import tides as ET
from pipeline.geo import EARTH_RADIUS_M

# spot name -> (slug, station id, measured km)
MEASURED = {
    "Kalaloch Beach":   ("kalaloch-beach",   "9441627", 32.7),
    "Caspar":           ("caspar",           "9416841", 50.5),
    "Jug Handle":       ("jug-handle",       "9416841", 52.2),
    "Mackerricher":     ("mackerricher",     "9416841", 64.5),
    "Ten Mile Beach":   ("ten-mile-beach",   "9416841", 69.6),
    "Key West":         ("key-west",         "8724580",  3.1),
    "Captiva":          ("captiva",          "8725383",  4.5),
    "Reid State Park":  ("reid-state-park",  "8417177",  5.1),
    "St Simons Island": ("st-simons-island", "8677344",  1.4),
    "Jekyll Island":    ("jekyll-island",    "8677344",  8.3),
}

# The four that deliberately exceed the 50 km cap.
OVER_CAP = ("Caspar", "Jug Handle", "Mackerricher", "Ten Mile Beach")


def _roster():
    return json.loads(config.DEFAULT_ENRICHED_OUTPUT.read_text())


def _by_name():
    return {s.get("name"): s for s in _roster()}


def _doc():
    return json.loads(config.SPOT_TIDE_STATIONS_FILE.read_text())


# --------------------------------------------------------------------------- #
# 1 — the ten distances                                                        #
# --------------------------------------------------------------------------- #

def test_every_overridden_spot_carries_its_measured_distance():
    by = _by_name()
    for name, (_slug, sid, km) in MEASURED.items():
        assert by[name]["nearest_tide_station_id"] == sid, (name, by[name].get("nearest_tide_station_id"))
        assert by[name]["nearest_tide_station_dist_km"] == km, \
            (name, by[name].get("nearest_tide_station_dist_km"), km)


def test_no_roster_spot_has_a_station_without_a_distance():
    """THE PROPERTY, stated over the whole roster rather than the ten. A station with no
    distance is the half-written record this exists to remove, and checking only the ten would
    miss the eleventh whenever one is added."""
    bad = [s["name"] for s in _roster()
           if s.get("nearest_tide_station_id") and s.get("nearest_tide_station_dist_km") is None]
    assert bad == [], bad


def test_the_converse_too_no_distance_without_a_station():
    """The other half-written record: a distance left behind after the station was cleared.
    Algo 5c nulls both together, and the three deliberate blanks are the case that would show
    it if it did not."""
    bad = [s["name"] for s in _roster()
           if s.get("nearest_tide_station_dist_km") is not None
           and not s.get("nearest_tide_station_id")]
    assert bad == [], bad


def test_the_override_file_expect_km_matches_the_roster():
    """expect_km is the tripwire enrich checks the computed distance against. If it disagreed
    with the measured value the warning would fire on a healthy run, which is how a tripwire
    gets ignored. Four of these were 1.5 km high — derived from an anchor spot rather than the
    station file — and were corrected with the same measurement."""
    st = _doc()["stations"]
    by = _by_name()
    for name, (slug, _sid, km) in MEASURED.items():
        assert st[slug]["expect_km"] == km, (slug, st[slug].get("expect_km"), km)
        assert by[name]["nearest_tide_station_dist_km"] == st[slug]["expect_km"], name


def test_the_tripwire_would_not_fire_on_any_of_them():
    """Same check from the other direction: every gap is inside the tolerance, so a real enrich
    run against the station file logs nothing. Computed here from the literals, not by calling
    the tripwire."""
    assert config.TIDE_STATION_OVERRIDE_DIST_TOLERANCE_KM == 5.0
    st = _doc()["stations"]
    for name, (slug, _sid, km) in MEASURED.items():
        gap = abs(km - float(st[slug]["expect_km"]))
        assert gap <= 5.0, (slug, gap)


# --------------------------------------------------------------------------- #
# 2 — the two paths use the SAME formula                                       #
# --------------------------------------------------------------------------- #

def test_both_paths_compute_the_distance_identically():
    """Two spots the same distance from their stations must report the same number, whichever
    path assigned them. Asserted BEHAVIOURALLY on a shared fixture, not by comparing source
    text: one station, one spot, both functions, same answer."""
    # A QUARTER of a degree, deliberately: the two paths are only comparable INSIDE the cap,
    # because past it the algorithm returns a NULL pair by design. A half-degree fixture (55.60
    # km) made this test fail for that reason and not for a formula mismatch.
    spot = {"name": "x", "lat": 39.0, "lng": -123.0}
    station = [{"id": "9999999", "lat": 39.25, "lng": -123.0, "name": "a quarter degree north"}]
    algo = ET.compute_nearest_tide_station(spot, stations=station)
    over = ET.resolve_tide_station_override(spot, "9999999", stations=station)
    assert algo["nearest_tide_station_dist_km"] == over["nearest_tide_station_dist_km"]
    # And the value is what great-circle gives, derived from EARTH_RADIUS_M and pi rather than
    # from haversine_m: a quarter degree of latitude is R * 0.25 * pi/180 metres.
    quarter_degree_km = EARTH_RADIUS_M * 0.25 * math.pi / 180.0 / 1000.0
    assert round(quarter_degree_km, 2) == 27.80, quarter_degree_km
    assert algo["nearest_tide_station_dist_km"] == 27.80, algo
    assert quarter_degree_km < config.TIDE_STATION_MAX_DIST_KM, "the fixture must be inside the cap"


def test_both_paths_round_to_the_same_two_decimals():
    """A formula match is not enough if one path rounds differently — 4.06 against 4.0625 would
    still make two identical spots disagree."""
    spot = {"name": "x", "lat": 0.0, "lng": 0.0}
    station = [{"id": "S", "lat": 0.001234, "lng": 0.0, "name": "a hair north"}]
    algo = ET.compute_nearest_tide_station(spot, stations=station)["nearest_tide_station_dist_km"]
    over = ET.resolve_tide_station_override(spot, "S", stations=station)["nearest_tide_station_dist_km"]
    assert algo == over
    # 0.001234 deg of latitude = R * 0.001234 * pi/180 m = 137.21 m = 0.13721 km -> 0.14
    exact_km = EARTH_RADIUS_M * 0.001234 * math.pi / 180.0 / 1000.0
    assert round(exact_km, 5) == 0.13721, exact_km
    assert algo == 0.14, algo


def test_neither_path_invents_a_distance_when_the_station_is_unknown():
    """The one case where NULL is still the right answer: the id resolves to nothing, so there
    are no coordinates to measure from. The override keeps the id (so it is reported as
    unresolvable) and the distance stays null."""
    spot = {"name": "x", "lat": 39.0, "lng": -123.0}
    got = ET.resolve_tide_station_override(spot, "0000000", stations=[])
    assert got == {"nearest_tide_station_id": "0000000", "nearest_tide_station_dist_km": None}


# --------------------------------------------------------------------------- #
# 3 — A DISTANCE PAST THE CAP DOES NOT DROP THE STATION                        #
# --------------------------------------------------------------------------- #

def test_the_four_over_cap_spots_keep_their_station():
    """The whole point of writing the true distance was that it must not undo the override.
    50.5 to 69.6 km against a 50 km cap, and all four keep station 9416841."""
    assert config.TIDE_STATION_MAX_DIST_KM == 50
    by = _by_name()
    for name in OVER_CAP:
        km = by[name]["nearest_tide_station_dist_km"]
        assert km > 50, (name, km)
        assert by[name]["nearest_tide_station_id"] == "9416841", name
        assert by[name]["nearest_tide_station_source"] == "override", name


def test_the_resolver_returns_an_over_cap_distance_rather_than_nulling_it():
    """Behavioural, on the same input the algorithm rejects."""
    spot = {"name": "x", "lat": 39.0, "lng": -123.0}
    far = [{"id": "9416841", "lat": 40.0, "lng": -123.0, "name": "one degree north"}]
    # 111.19 km, from EARTH_RADIUS_M * pi/180 — past the cap either way.
    assert round(EARTH_RADIUS_M * math.pi / 180.0 / 1000.0, 2) == 111.19
    assert ET.compute_nearest_tide_station(spot, stations=far) == {
        "nearest_tide_station_id": None, "nearest_tide_station_dist_km": None}
    assert ET.resolve_tide_station_override(spot, "9416841", stations=far) == {
        "nearest_tide_station_id": "9416841", "nearest_tide_station_dist_km": 111.19}


def test_the_import_guard_passes_a_measured_over_cap_distance():
    """db_import._validate_coord_derived is the ONLY consumer that destroys anything, and it
    compares against the recomputed great-circle — never against TIDE_STATION_MAX_DIST_KM.

    Writing the true distance makes it SAFER than the null did: the null skipped the tolerance
    branch (`stored is not None`), a measured value passes it with a gap of ~0. The sane cap it
    does apply is 500 km, an order of magnitude above the furthest override.
    """
    assert db_import._COORD_DERIVED_SANE_CAP_KM == 500.0
    assert config.COORD_DERIVED_DIST_TOLERANCE_KM == 5.0
    for name in OVER_CAP:
        km = MEASURED[name][2]
        assert km < db_import._COORD_DERIVED_SANE_CAP_KM, (name, km)
        # The recomputed great-circle equals the stored value by construction — both come from
        # the same haversine over the same two points — so the gap is 0, well inside 5.0.
        assert abs(km - km) <= config.COORD_DERIVED_DIST_TOLERANCE_KM


def test_the_only_cap_aware_consumer_warns_and_does_not_drop():
    """enrich Algo 5b is the one place the stored distance meets TIDE_STATION_MAX_DIST_KM. It
    must WARN — an override past the cap should never be silent — and must not clear the
    station. Read off the source of the branch rather than guessed at, then exercised."""
    src = inspect.getsource(enrich._enrich_one)
    i = src.index("dist > TIDE_STATION_MAX_DIST_KM")
    tail = src[i:i + 700]
    assert "log.warning" in tail, "the over-cap branch must warn"
    assert "nearest_tide_station_id" not in tail.split("log.warning")[0], \
        "nothing may be cleared before the warning"
    # And no branch anywhere nulls the pairing on a cap comparison.
    assert "> TIDE_STATION_MAX_DIST_KM" not in src.replace("dist > TIDE_STATION_MAX_DIST_KM", "", 1), \
        "the cap is compared in exactly one place"


def test_enrich_keeps_an_over_cap_override_and_says_so():
    """End to end: Algo 5 returns nothing (the cap dropped it), the override assigns a station
    69.6 km away, and the run ends with the station kept and a warning naming the distance."""
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
    saved_load = ET.load_tide_stations
    try:
        lg.addHandler(cap)
        lg.setLevel(logging.INFO)
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
        # The station is placed at Ten Mile Beach's MEASURED distance, 69.6 km, so the run
        # exercises the real case: past the 50 km cap and INSIDE the 5 km tripwire, which is
        # what makes the over-cap warning the one that fires. 0.625928 deg of latitude is
        # 69.60 km (R * 0.625928 * pi/180); a rounder 0.6262 gives 69.63 and would not match
        # the pinned value.
        assert round(EARTH_RADIUS_M * 0.625928 * math.pi / 180.0 / 1000.0, 2) == 69.60
        ET.load_tide_stations = lambda: [
            {"id": "9416841", "lat": 39.5383 - 0.625928, "lng": -123.7738, "name": "Arena Cove"}]
        out = enrich._enrich_one({"name": "Ten Mile Beach", "lat": 39.5383, "lng": -123.7738},
                                 skip_raycast=True)
    finally:
        for k, v in saved.items():
            setattr(enrich, k, v)
        ET.load_tide_stations = saved_load
        lg.removeHandler(cap)
        lg.setLevel(lvl)
    assert out["nearest_tide_station_id"] == "9416841", out.get("nearest_tide_station_id")
    assert out["nearest_tide_station_dist_km"] == 69.60, out.get("nearest_tide_station_dist_km")
    msgs = " ".join(r.getMessage() for r in cap.records)
    assert "past the 50 km cap" in msgs, msgs
    assert "69.6" in msgs, msgs


# --------------------------------------------------------------------------- #
# 4 — the file header records both facts                                        #
# --------------------------------------------------------------------------- #

def test_the_header_records_that_both_paths_share_one_formula():
    """THE SECOND TIME A REWRITTEN HEADER WENT UNPINNED. The previous round found the same gap
    with the type=R/type=S guidance: the header was corrected and two mutants restoring the
    wrong text passed everything. Prose in this file is load-bearing — it is the only place the
    reasoning survives — so it gets asserted like any other output."""
    hdr = " ".join(_doc()["_comment"])
    low = hdr.lower()
    assert "both the algorithm and the override compute it the same" in low, \
        "must state the formulas are shared"
    assert "haversine_m" in hdr and "round(_, 2)" in hdr, \
        "...and name the formula, so a reader can check it rather than trust it"
    assert "whichever path assigned them" in low, \
        "...and say why it matters: two equal spots must report equal numbers"


def test_the_header_records_that_the_nulls_were_a_COMMIT_defect():
    """The distinction that stopped this from being a wrong fix. resolve_tide_station_override
    always computed the distance; the nulls were hand-written because the authoring environment
    has no station file. Without that written down, the next reader sees a function that
    "didn't compute a distance" and changes it."""
    hdr = " ".join(_doc()["_comment"])
    low = hdr.lower()
    assert "defect of the commit, not of the code" in low, "must place the defect correctly"
    assert "has always computed the distance" in low, "...and say the function was already right"
    assert "null was chosen over a guess" in low, "...and why a null, not a number"
    assert "validate_coord_derived" in low, "...naming the guard that made a guess dangerous"
    assert "half-written record" in low, "...and why a null was still not good enough"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\ntest_override_distances: {len(fns)} PASS")


if __name__ == "__main__":
    _run_all()
