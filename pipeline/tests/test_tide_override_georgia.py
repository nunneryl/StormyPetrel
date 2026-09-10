"""Two Georgia sea islands get a station; three stay blank, and now on MEASURED grounds.

WHAT CHANGED AND WHY. All five Georgia islands were declined together on the argument that
CO-OPS coverage there is predominantly inside the sounds and river mouths, where the tide lags
the open coast. The argument was right. The distances it rested on — 50 to 86 km — were upper
bounds to the nearest station a ROSTER SPOT uses, computed because
pipeline/geodata/tide_stations.json is a gitignored download that was unreadable in the
environment those entries were written in.

Reading the station file splits the five:

    St Simons Island       8677344  St. Simons Light         1.4 km   R   -> ASSIGNED
    Jekyll Island          8677344  St. Simons Light         8.3 km   R   -> ASSIGNED
    Sea Island             8677344  St. Simons Light        10.8 km   R   -> recommended
    Blackbeard Island      8674301  South Newport River      8.2 km   R   -> stays blank
    St. Catherines Island  8674301  South Newport River      6.8 km   R   -> stays blank

THE STATION POSITIONS ARE SOLVED, NOT ASSUMED, and that is what makes the split defensible
rather than a second round of reasoning from names. Three independent distances put 8677344 at
31.1318, -81.3970 to within 18 metres — the southwest tip of St Simons Island, at the MOUTH of
St Simons Sound. Two distances leave 8674301 with two exact solutions, mirror
images 3.0 km either side of the line joining the two islands; its name settles which, and it
is the landward one.

A station at an inlet MOUTH reads essentially the ocean tide — the lag develops going UP an
inlet, not at its entrance. A station up an inland river does not.

EVERY EXPECTED VALUE IS A LITERAL. The trilateration below is recomputed from the published
distances and checked against a hand-written position, never against whatever the solver
happens to return.

Run: python -m pipeline.tests.test_tide_override_georgia
"""
from __future__ import annotations

import json

import math

from pipeline import config, enrich
from pipeline.enrichment import tides as ET
from pipeline.geo import haversine_m

ST_SIMONS_LIGHT = "8677344"
SOUTH_NEWPORT_RIVER = "8674301"

# spot -> (slug, assigned station id, expect_km)
ASSIGNED = {
    "St Simons Island": ("st-simons-island", ST_SIMONS_LIGHT, 1.4),
    "Jekyll Island":    ("jekyll-island",    ST_SIMONS_LIGHT, 8.3),
}
STILL_BLANK = {
    "Blackbeard Island":     "blackbeard-island",
    "St. Catherines Island": "st-catherines-island",
    "Sea Island":            "sea-island",
}

# Spot coordinates, copied from pipeline/spots_enriched.json.
COORDS = {
    "St Simons Island":      (31.1353, -81.3830),
    "Jekyll Island":         (31.0574, -81.4057),
    "Sea Island":            (31.2041, -81.3208),
    "Blackbeard Island":     (31.5010, -81.1910),
    "St. Catherines Island": (31.6176, -81.1381),
}


def _roster():
    return json.loads(config.DEFAULT_ENRICHED_OUTPUT.read_text())


def _by_name():
    return {s.get("name"): s for s in _roster()}


def _doc():
    return json.loads(config.SPOT_TIDE_STATIONS_FILE.read_text())


# --------------------------------------------------------------------------- #
# 1 — the station positions, solved from the published distances               #
# --------------------------------------------------------------------------- #

def test_the_three_distances_put_st_simons_light_at_the_sound_mouth():
    """The claim the two assignments rest on, checked rather than repeated.

    31.1318, -81.3970 is written out by hand; the test asserts that position reproduces all
    THREE published distances to within 100 m. That is the direction that matters — a wrong
    position could not fit three independent circles — and it never asks a solver what the
    answer is.
    """
    lat, lng = 31.1318, -81.3970
    for spot, want in (("St Simons Island", 1.4), ("Jekyll Island", 8.3), ("Sea Island", 10.8)):
        got = haversine_m(lat, lng, *COORDS[spot]) / 1000.0
        assert abs(got - want) < 0.1, (spot, round(got, 3), want)
    # SOUTHWEST of the St Simons spot: lower latitude and further west. That is the sound
    # mouth, not the island's ocean-facing beach — the distinction the Jekyll entry rests on.
    assert lat < COORDS["St Simons Island"][0], "south of the spot"
    assert lng < COORDS["St Simons Island"][1], "west of the spot"


def test_south_newport_river_solves_INLAND_of_the_barrier_chain():
    """Two distances admit two positions. Both are written out; the name picks the inland one,
    and that is the whole basis for keeping Blackbeard and St. Catherines blank."""
    seaward = (31.5565, -81.1341)
    inland = (31.5747, -81.1893)
    for pos in (seaward, inland):
        for spot, want in (("Blackbeard Island", 8.2), ("St. Catherines Island", 6.8)):
            got = haversine_m(pos[0], pos[1], *COORDS[spot]) / 1000.0
            assert abs(got - want) < 0.1, (pos, spot, round(got, 3), want)
    # WHICH SIDE, measured properly. Comparing a longitude against one island's is not enough:
    # -81.1893 is west of Blackbeard's -81.1910? No — it is EAST of it, and west of St.
    # Catherines' -81.1381. The coast trends NE here, so the test has to be against the LINE
    # joining the two spots, not against either endpoint. (An earlier version of this check
    # compared against Blackbeard alone and failed, which is how the imprecision surfaced.)
    def offset_km(lat, lng):
        """Signed east-west distance from the Blackbeard-St.Catherines baseline, in km.
        Positive is seaward."""
        b, c = COORDS["Blackbeard Island"], COORDS["St. Catherines Island"]
        frac = (lat - b[0]) / (c[0] - b[0])
        base_lng = b[1] + frac * (c[1] - b[1])
        return (lng - base_lng) * 111.32 * math.cos(math.radians(lat))

    # The two solutions are mirror images about that baseline, 3.0 km either side.
    assert abs(offset_km(*inland) + 3.0) < 0.1, offset_km(*inland)
    assert abs(offset_km(*seaward) - 3.0) < 0.1, offset_km(*seaward)
    assert offset_km(*inland) < 0, "the named solution is LANDWARD of the barrier chain"
    # And the file records the one it chose, so a reader can see the ambiguity was resolved
    # rather than overlooked.
    reason = " ".join(_doc()["unassigned"]["blackbeard-island"]["reason"])
    assert "31.5747" in reason and "-81.1893" in reason, reason
    assert "TWO exact solutions" in reason


# --------------------------------------------------------------------------- #
# 2 — the two assignments                                                      #
# --------------------------------------------------------------------------- #

def test_both_assigned_islands_take_st_simons_light():
    st = _doc()["stations"]
    for name, (slug, sid, expect) in ASSIGNED.items():
        assert st[slug]["station_id"] == sid, (slug, st[slug].get("station_id"))
        assert st[slug]["expect_km"] == expect, (slug, st[slug].get("expect_km"))


def test_they_share_ONE_station_so_one_check_validates_both():
    """The same argument that kept the four Mendocino spots on Arena Cove."""
    st = _doc()["stations"]
    assert st["st-simons-island"]["station_id"] == st["jekyll-island"]["station_id"]


def test_the_roster_carries_both():
    by = _by_name()
    for name, (_slug, sid, _e) in ASSIGNED.items():
        assert by[name]["nearest_tide_station_id"] == sid, (name, by[name].get("nearest_tide_station_id"))
        assert by[name]["nearest_tide_station_source"] == "override", name
        assert by[name]["nearest_tide_station_dist_km"] is None, \
            f"{name}: a guessed distance could be NULLed by db_import; Algo 5b computes it"


def test_neither_is_height_suppressed():
    """At 1.4 and 8.3 km the height is local. Suppression is for the Mendocino case, where the
    station is 50-71 km up the coast and only its phase transfers."""
    st = _doc()["stations"]
    by = _by_name()
    for name, (slug, _sid, _e) in ASSIGNED.items():
        assert "suppress_height" not in st[slug], slug
        assert "tide_height_suppressed" not in by[name], name
    # Roster-wide, exactly the Mendocino four carry the flag.
    flagged = sorted(s["name"] for s in _roster() if s.get("tide_height_suppressed"))
    assert flagged == ["Caspar", "Jug Handle", "Mackerricher", "Ten Mile Beach"], flagged


def test_jekyll_records_why_the_NEARER_station_was_declined():
    """8678124 'Raccoon Key Spit' is 1.6 km closer and was not taken. The reason has to be
    recorded or the next reader sees an override that walked past a nearer station."""
    reason = " ".join(_doc()["stations"]["jekyll-island"]["reason"])
    low = reason.lower()
    assert "8678124" in reason, "must name the station it did NOT take"
    assert "position is unknown" in low or "not a point" in low, \
        "must say WHY: a single distance places it on a circle, not a point"
    assert "landform" in low, "...and that the name carries no water-body information"
    # It must also say what would overturn the choice, or the open question is lost.
    assert "8678124's coordinates" in reason or "reads 8678124" in low.replace("'", "'"), reason


def test_the_sound_crossing_argument_is_recorded():
    """Jekyll's path to the station crosses St Simons Sound, which LOOKS like the Casco Bay
    crossing that got Reid State Park declined. The entry has to say why it is not, or a future
    reader reconciling the two files finds a contradiction."""
    low = " ".join(_doc()["stations"]["jekyll-island"]["reason"]).lower()
    assert "reid state park" in low, "must name the case it appears to contradict"
    assert "shelf" in low, "must give the physical reason: phase is set by the shelf wave"
    assert "its own interior" in low, "...and that an inlet lags itself, not the water in front"


# --------------------------------------------------------------------------- #
# 3 — the three that stay blank                                                #
# --------------------------------------------------------------------------- #

def test_the_three_remaining_blanks_are_still_blank():
    by = _by_name()
    loaded = enrich._load_spot_tide_unassigned()
    for name, slug in STILL_BLANK.items():
        assert by[name]["nearest_tide_station_id"] is None, (name, by[name].get("nearest_tide_station_id"))
        assert by[name]["nearest_tide_station_source"] == "unassigned_override", name
        assert slug in loaded, slug
    assert sorted(loaded) == ["blackbeard-island", "sea-island", "st-catherines-island"], \
        sorted(loaded)


def test_the_two_assigned_slugs_left_the_unassigned_block_entirely():
    doc = _doc()
    loaded = enrich._load_spot_tide_unassigned()
    for name, (slug, _sid, _e) in ASSIGNED.items():
        assert slug not in doc["unassigned"], f"{slug} still listed as a deliberate blank"
        assert slug not in loaded, slug
    enrich.check_tide_override_conflicts(enrich._SPOT_TIDE_STATIONS, enrich._SPOT_TIDE_UNASSIGNED)


def test_sea_island_records_the_recommendation_rather_than_a_bare_blank():
    """It is blank because the instruction asked for a recommendation, not an assignment — not
    because the case failed. An entry that did not say so would read as a rejection."""
    low = " ".join(_doc()["unassigned"]["sea-island"]["reason"]).lower()
    assert "recommendation is to assign" in low, "the recommendation must be explicit"
    assert "8677344" in low, "must name the station recommended"
    assert "hampton river" in low, "must address the objection it was asked about"
    assert "shelf" in low, "...with the physical argument, not an assertion"


def test_the_estuarine_blanks_name_the_station_and_its_solved_position():
    doc = _doc()["unassigned"]
    for slug in ("blackbeard-island", "st-catherines-island"):
        reason = " ".join(doc[slug]["reason"])
        assert SOUTH_NEWPORT_RIVER in reason, slug
        assert "31.5747" in reason, f"{slug}: must carry the solved position"
        assert "inland" in reason.lower(), slug


# --------------------------------------------------------------------------- #
# 4 — end to end through enrich                                                #
# --------------------------------------------------------------------------- #

def test_enrich_assigns_st_simons_and_leaves_blackbeard_blank():
    """One run of the real Algo 5b/5c against injected stations, covering both outcomes."""
    saved = {k: getattr(enrich, k) for k in
             ("load_land_index", "compute_nearest_tide_station", "compute_nearest_buoy",
              "compute_orientation", "compute_break_type")}
    saved_load = ET.load_tide_stations
    try:
        enrich.load_land_index = lambda: None
        # Algo 5 returns the INLAND river station for both — what a raised cap would produce.
        enrich.compute_nearest_tide_station = lambda spot: {
            "nearest_tide_station_id": SOUTH_NEWPORT_RIVER, "nearest_tide_station_dist_km": 8.2}
        enrich.compute_nearest_buoy = lambda spot: {
            "nearest_buoy_id": None, "nearest_buoy_dist_km": None,
            "fallback_buoy_ids": [], "buoy_confidence": 0.0}
        enrich.compute_orientation = lambda spot: {"orientation_deg": 90.0,
                                                   "orientation_confidence": 0.5}
        enrich.compute_break_type = lambda spot: {"break_type": "beach",
                                                  "break_type_confidence": 0.5}
        ET.load_tide_stations = lambda: [
            {"id": ST_SIMONS_LIGHT, "lat": 31.1318, "lng": -81.3970, "name": "St. Simons Light"},
            {"id": SOUTH_NEWPORT_RIVER, "lat": 31.5747, "lng": -81.1893, "name": "South Newport River"},
        ]
        simons = enrich._enrich_one(
            {"name": "St Simons Island", "lat": 31.1353, "lng": -81.3830}, skip_raycast=True)
        black = enrich._enrich_one(
            {"name": "Blackbeard Island", "lat": 31.5010, "lng": -81.1910}, skip_raycast=True)
    finally:
        for k, v in saved.items():
            setattr(enrich, k, v)
        ET.load_tide_stations = saved_load

    # The override beat the algorithm's inland answer...
    assert simons["nearest_tide_station_id"] == ST_SIMONS_LIGHT, simons.get("nearest_tide_station_id")
    assert simons["nearest_tide_station_source"] == "override"
    assert "tide_height_suppressed" not in simons
    # 1.4 km is the published distance; the fixture places the station at the solved position,
    # so the computed value must land there. Written out, not read back from the solver.
    assert abs(simons["nearest_tide_station_dist_km"] - 1.4) < 0.1, \
        simons["nearest_tide_station_dist_km"]
    # ...and the deliberate blank cleared the algorithm's answer rather than keeping it.
    assert black["nearest_tide_station_id"] is None, black.get("nearest_tide_station_id")
    assert black["nearest_tide_station_source"] == "unassigned_override"


# --------------------------------------------------------------------------- #
# 5 — the header's type=R / type=S guidance                                     #
# --------------------------------------------------------------------------- #

def test_the_header_does_NOT_state_prefer_R_as_a_rule():
    """An earlier header said 'PREFER type=R OVER A NEARER type=S' and that was wrong twice
    over — subordinates are the roster MAJORITY and work, and the pipeline cannot see the field
    at all. Nothing tested the header, so a mutation restoring the rule passed everything."""
    hdr = " ".join(_doc()["_comment"])
    low = hdr.lower()
    assert "type=r vs type=s is not the axis" in low, "the corrected framing must be present"
    assert "prefer type=r over a nearer type=s. co-ops" not in low, \
        "the old rule must not come back"
    # The two facts that make it not a rule, by value.
    assert "125" in hdr and "108" in hdr, "must carry the roster counts that refute the rule"
    assert "load_tide_stations keeps" in hdr, \
        "must say the pipeline never sees the type field"
    assert "id, lat, lng and name" in hdr, "...and which fields it does keep"


def test_the_header_says_where_the_distinction_DOES_matter():
    """Refuting a rule is only half of it. If the header did not say where subordinates
    genuinely cost something, the next reader would conclude the type never matters."""
    low = " ".join(_doc()["_comment"]).lower()
    assert "hilo_only = not hourly" in low, "must say the flag is discovered empirically"
    assert "cosine-interpolate" in low or "cosine interpolation" in low
    # The counter-intuitive half, and the reason a subordinate is NEARLY FREE on an open coast:
    # tide_norm is (v-min)/(max-min), and hilo gets the extremes BETTER than hourly does.
    # Without this the header reads as "subordinates are merely tolerable".
    assert "hilo gets them better than hourly" in low, "must say hilo wins at the extremes"
    assert "30 minutes" in low, "...with the reason: hourly can miss the peak by that much"
    assert "overtides" in low, "the non-sinusoidal case"
    assert "max_gap_h" in low and "6.0" in low, "the missing-extreme case, the independent one"
    assert "choose on water body and distance" in low, "the actual rule"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\ntest_tide_override_georgia: {len(fns)} PASS")


if __name__ == "__main__":
    _run_all()
