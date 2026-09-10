"""Key West, Captiva and Reid State Park take LOCAL reference stations, 3-5 km away.

WHY THIS FILE EXISTS, AND WHAT IT RECORDS ABOUT HOW THE EARLIER DECISION WENT WRONG.

These three were deliberate blanks. Each was declined on an argument that was individually
sound — the Atlantic/Gulf tidal transition running through the Keys, mixed-semidiurnal
inequality on the SW Florida coast, a path crossing Casco Bay in Maine — and every one of
those arguments was answering "should we use a station 50-176 km away".

That was never the question. pipeline/geodata/tide_stations.json is a GITIGNORED DOWNLOAD and
was absent from the environment those entries were written in, so the distances came from the
only station positions recoverable there: the ones OTHER ROSTER SPOTS already use, which is a
small subset of the file. They were correctly labelled as upper bounds. They were then reasoned
on as though they were the nearest stations. The real nearest are:

    Key West         8724580  KEY WEST                             3.1 km   type=R
    Captiva          8725383  Captiva Island (outside)             4.5 km   type=R
    Reid State Park  8417177  Fort Popham, Hunniwell Point         5.1 km   type=R

NO HEIGHT SUPPRESSION. The Mendocino four withhold tide_level_ft because their station is
50-71 km up the coast and only its PHASE transfers. At 3-5 km there is nothing to withhold:
the height is the spot's own.

REFERENCE OVER A NEARER SUBORDINATE. Both Florida spots have a type=S station closer than the
one chosen — 0.7 km in each case. A subordinate is published as time and height offsets applied
to a reference station's harmonic prediction and serves interval=hilo only, which forces
interpret.build_tide_series onto its cosine-interpolated hilo fallback instead of the hourly
branch. Captiva's subordinate is additionally named "Pine Island Sound" — the estuary behind
the island — against 8725383's "(outside)", the open-Gulf side the spot faces.

EVERY EXPECTED VALUE IS A LITERAL. Station ids, slugs and names are written out; nothing is
produced by calling the function under test.

Run: python -m pipeline.tests.test_tide_override_local_stations
"""
from __future__ import annotations

import json

from pipeline import config, enrich
from pipeline.enrichment import tides as ET

# spot name -> (slug, chosen station id, the nearer station NOT chosen or None)
LOCAL = {
    "Key West":        ("key-west",        "8724580", "8724557"),
    "Captiva":         ("captiva",         "8725383", "8725417"),
    "Reid State Park": ("reid-state-park", "8417177", None),
}


def _roster():
    return json.loads(config.DEFAULT_ENRICHED_OUTPUT.read_text())


def _by_name():
    return {s.get("name"): s for s in _roster()}


def _doc():
    return json.loads(config.SPOT_TIDE_STATIONS_FILE.read_text())


# --------------------------------------------------------------------------- #
# 1 — the three assignments                                                    #
# --------------------------------------------------------------------------- #

def test_the_override_file_assigns_each_of_the_three():
    st = _doc()["stations"]
    for name, (slug, sid, _nearer) in LOCAL.items():
        assert slug in st, f"{slug} missing from the stations block"
        assert st[slug]["station_id"] == sid, (slug, st[slug].get("station_id"))


def test_the_committed_roster_carries_them():
    """The forecast workflow does not run enrich, so the roster is what takes effect on the
    next run; Algo 5b is what makes it survive a re-enrich."""
    by = _by_name()
    for name, (_slug, sid, _n) in LOCAL.items():
        assert by[name]["nearest_tide_station_id"] == sid, (name, by[name].get("nearest_tide_station_id"))
        assert by[name]["nearest_tide_station_source"] == "override", name


def test_the_three_are_gone_from_the_unassigned_block():
    doc = _doc()
    loaded = enrich._load_spot_tide_unassigned()
    for name, (slug, _sid, _n) in LOCAL.items():
        assert slug not in doc["unassigned"], f"{slug} still listed as a deliberate blank"
        assert slug not in loaded, slug
    # Only the Georgia five remain.
    assert sorted(loaded) == ["blackbeard-island", "sea-island",
                              "st-catherines-island"], sorted(loaded)


def test_the_roster_carries_the_measured_distance():
    """These three are 3-5 km from their stations and the roster says so.

    IT USED TO ASSERT NULL. That was correct for the state it was written in — the environment
    lacked tide_stations.json, so any number would have been a guess, and a guess more than
    COORD_DERIVED_DIST_TOLERANCE_KM off would have made db_import NULL the whole pairing. The
    distances have since been measured from the station file, so the placeholder is gone."""
    by = _by_name()
    want = {"Key West": 3.1, "Captiva": 4.5, "Reid State Park": 5.1}
    for name in LOCAL:
        assert by[name]["nearest_tide_station_dist_km"] == want[name], \
            (name, by[name].get("nearest_tide_station_dist_km"), want[name])
        # Local, by the definition that separates these from the Mendocino four.
        assert by[name]["nearest_tide_station_dist_km"] < config.TIDE_STATION_MAX_DIST_KM, name


# --------------------------------------------------------------------------- #
# 2 — NONE of the three is height-suppressed                                   #
# --------------------------------------------------------------------------- #

def test_no_local_assignment_carries_suppress_height():
    """The distinguishing property against the Mendocino four. Suppression exists for a
    station whose PHASE transfers but whose HEIGHT does not; at 3-5 km both do."""
    st = _doc()["stations"]
    for name, (slug, _sid, _n) in LOCAL.items():
        assert "suppress_height" not in st[slug], \
            f"{slug} is a local station — there is nothing to withhold"
    # ...and the four that DO suppress still do, so this is a per-entry property and not a
    # change of default.
    for slug in ("caspar", "jug-handle", "mackerricher", "ten-mile-beach"):
        assert st[slug]["suppress_height"] is True, slug


def test_the_roster_carries_no_suppression_flag_for_the_three():
    by = _by_name()
    for name in LOCAL:
        assert "tide_height_suppressed" not in by[name], name
    # Exactly the Mendocino four are flagged, roster-wide.
    flagged = sorted(s["name"] for s in _roster() if s.get("tide_height_suppressed"))
    assert flagged == ["Caspar", "Jug Handle", "Mackerricher", "Ten Mile Beach"], flagged


def test_enrich_assigns_the_station_without_the_flag():
    """End to end through Algo 5b for one of the three, with the station list injected."""
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
        enrich.compute_orientation = lambda spot: {"orientation_deg": 180.0,
                                                   "orientation_confidence": 0.5}
        enrich.compute_break_type = lambda spot: {"break_type": "reef",
                                                  "break_type_confidence": 0.5}
        # The chosen station sits ON the spot so the computed distance is exactly 0.0 — a
        # literal, not an arithmetic result — and the subordinate is placed further away so a
        # nearest-wins fallback would be visible.
        ET.load_tide_stations = lambda: [
            {"id": "8724580", "lat": 24.5491, "lng": -81.7783, "name": "KEY WEST"},
            {"id": "8724557", "lat": 24.6000, "lng": -81.8000, "name": "White St Pier"},
        ]
        out = enrich._enrich_one({"name": "Key West", "lat": 24.5491, "lng": -81.7783},
                                 skip_raycast=True)
    finally:
        for k, v in saved.items():
            setattr(enrich, k, v)
        ET.load_tide_stations = saved_load
    assert out["nearest_tide_station_id"] == "8724580", out.get("nearest_tide_station_id")
    assert out["nearest_tide_station_dist_km"] == 0.0, out.get("nearest_tide_station_dist_km")
    assert out["nearest_tide_station_source"] == "override"
    assert "tide_height_suppressed" not in out, "a local station keeps its tide tile"


# --------------------------------------------------------------------------- #
# 3 — the conflict check fires if a slug is left in BOTH blocks                 #
# --------------------------------------------------------------------------- #

def test_leaving_a_moved_slug_in_unassigned_is_fatal():
    """THE EXACT MISTAKE THIS MOVE COULD HAVE MADE: adding to `stations` and forgetting to
    delete from `unassigned`. The two blocks mean opposite things, so that is not an ambiguity
    to resolve by ordering — it is a decision the file does not make."""
    stations = dict(enrich._SPOT_TIDE_STATIONS)
    for name, (slug, _sid, _n) in LOCAL.items():
        try:
            enrich.check_tide_override_conflicts(stations, {slug})
        except ValueError as e:
            assert slug in str(e), (slug, str(e))
            assert "opposite things" in str(e), e
        else:
            raise AssertionError(f"{slug} in both blocks must raise")


def test_the_committed_file_has_no_conflict():
    """What the import-time call asserts, exercised directly."""
    enrich.check_tide_override_conflicts(enrich._SPOT_TIDE_STATIONS,
                                         enrich._SPOT_TIDE_UNASSIGNED)
    doc = _doc()
    assert set(doc["stations"]) & set(doc["unassigned"]) == set()


def test_the_conflict_check_names_only_the_offenders():
    try:
        enrich.check_tide_override_conflicts({"key-west": {}, "caspar": {}},
                                             {"key-west", "jekyll-island"})
    except ValueError as e:
        assert "key-west" in str(e)
        assert "caspar" not in str(e) and "jekyll-island" not in str(e), str(e)
    else:
        raise AssertionError("must raise")


# --------------------------------------------------------------------------- #
# 4 — the reasoning is recorded where a future reader will find it              #
# --------------------------------------------------------------------------- #

def test_each_entry_records_why_the_reference_station_beat_the_subordinate():
    st = _doc()["stations"]
    for name, (slug, _sid, nearer) in LOCAL.items():
        reason = " ".join(st[slug]["reason"])
        low = reason.lower()
        assert "subordinate" in low, slug
        if nearer is not None:
            # A nearer type=S existed and was declined: the entry must name it and say what
            # taking it would have cost, or "reference station" is an unexplained preference.
            assert nearer in reason, f"{slug}: must name the station it did NOT take ({nearer})"
            assert "hilo" in low, f"{slug}: must say what a subordinate costs downstream"
        else:
            # No nearer subordinate was offered. The entry must SAY so rather than being
            # silent, or a reader comparing the three cannot tell whether it was overlooked.
            assert "does not arise" in low, \
                f"{slug}: must state that the R-over-S question did not arise"
    # Captiva's second, independent reason: the nearer one is in the estuary behind the island.
    captiva = " ".join(st["captiva"]["reason"]).lower()
    assert "pine island sound" in captiva and "outside" in captiva


def test_each_entry_records_that_the_earlier_blank_rested_on_an_estimate():
    """The durable lesson. Without it a future reader sees a spot that was blank and is now
    assigned, and cannot tell whether the earlier judgement was overturned or merely
    mis-founded."""
    # The STALE figure each spot was declined on. Requiring it by value is what makes the note
    # useful: a reader needs to see how big the error was, not merely that there was one.
    # An earlier version of this check accepted either of two phrases, and a mutation that
    # deleted the sentence carrying the number passed on the strength of the other.
    STALE_KM = {"key-west": "176", "captiva": "57.5", "reid-state-park": "51.5"}
    st = _doc()["stations"]
    for name, (slug, _sid, _n) in LOCAL.items():
        low = " ".join(st[slug]["reason"]).lower()
        assert "upper bound" in low, f"{slug}: must say the old distance was a BOUND"
        assert "tide_stations.json" in low, f"{slug}: must name the file that was missing"
        assert "gitignored" in low or "unreadable" in low or "not available" in low, \
            f"{slug}: must say WHY the file was missing, or it reads as carelessness"
        assert STALE_KM[slug] in low, \
            f"{slug}: must name the stale distance ({STALE_KM[slug]} km) it was declined on"


def test_the_file_header_records_the_lesson_and_the_R_over_S_rule():
    hdr = " ".join(_doc()["_comment"]).lower()
    assert "check the station file before declining a spot" in hdr
    assert "gitignored" in hdr, "must say why the file was unreadable"
    assert "3.1 km" in hdr and "4.5 km" in hdr and "5.1 km" in hdr, \
        "must carry the three corrected distances, so the scale of the error is legible"
    assert "reasoning on them anyway" in hdr, "must name the failure, not just the fact"
    assert "prefer type=r over a nearer type=s" in hdr


def test_every_slug_in_the_file_still_matches_a_roster_spot():
    slugs = {enrich._slug_for(s.get("name")) for s in _roster()}
    doc = _doc()
    for block in ("stations", "unassigned"):
        for slug in doc[block]:
            if slug == "_comment":
                continue
            assert slug in slugs, f"{block}/{slug} matches no spot on the roster"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\ntest_tide_override_local_stations: {len(fns)} PASS")


if __name__ == "__main__":
    _run_all()
