"""The SE Florida hold (pipeline/data/spot_swell_window_holds.json).

The R1–R3 island rules judge the Bimini group on its own run instead of borrowing Cuba's,
which on the land data alone opens or reshuffles the south-east gap at SE Florida spots. The
Great Bahama Bank's shallow water shelters them more than its islands do, so they keep the
windows they had: ten from Hillsboro Beach to Hobe Beach (2026-10-19), and eleven more from
South Beach Miami to Bathtub Beach after full run 24 (2026-10-20). Every expected value is
transcribed from main's spots_enriched.json at 7839cc0, not read back from the loader.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import enrich as E
from pipeline.config import DEFAULT_ENRICHED_OUTPUT, SPOT_SWELL_WINDOW_HOLDS_FILE
from pipeline.enrichment.swell_window_holds import apply_hold, load_holds, slug
from pipeline.sw1_raycast import _apply_raycast_result


def _arcs(*triples):
    return [{"min": lo, "max": hi, "span": sp} for lo, hi, sp in triples]


HELD = {
    "hillsboro-beach": ("Hillsboro Beach", _arcs((17, 127, 114), (145, 175, 34)), 96),
    "hillsboro-inlet": ("Hillsboro Inlet", _arcs((21, 119, 102), (141, 179, 42)), 96),
    "deerfield-beach-pier": ("Deerfield Beach Pier", _arcs((17, 127, 114), (145, 175, 34)), 96),
    "pompano-beach-pier": ("Pompano Beach Pier", _arcs((25, 123, 102), (145, 175, 34)), 96),
    "dania-beach-pier": ("Dania Beach Pier", _arcs((13, 123, 114), (141, 175, 38)), 92),
    "delray-beach": ("Delray Beach", _arcs((17, 127, 114), (153, 179, 30)), 96),
    "lake-worth-pier": ("Lake Worth Pier", _arcs((9, 131, 126), (157, 175, 22)), 88),
    "boynton-beach-inlet": ("Boynton Beach Inlet", _arcs((21, 111, 94), (153, 179, 30)), 92),
    "jupiter-inlet": ("Jupiter Inlet", _arcs((145, 159, 18), (353, 111, 122)), 72),
    "hobe-beach": ("Hobe Beach", _arcs((121, 155, 38), (349, 103, 118)), 72),
    # added after full run 24
    "south-beach-miami": ("South Beach Miami", _arcs((17, 75, 62), (141, 183, 46)), 80),
    "haulover-inlet": ("Haulover Inlet", _arcs((13, 79, 70), (137, 167, 34)), 80),
    "sunny-isles": ("Sunny Isles", _arcs((13, 171, 162)), 92),
    "spoils": ("Spoils", _arcs((13, 83, 74)), 40.0),
    "fort-lauderdale-beach": ("Fort Lauderdale Beach", _arcs((17, 87, 74), (145, 175, 34)), 84),
    "commercial-pier": ("Commercial Pier", _arcs((17, 87, 74), (145, 175, 34)), 84),
    "boca-raton-inlet": ("Boca Raton Inlet", _arcs((13, 91, 82), (145, 179, 38)), 84),
    "palmetto-pavilion": ("Palmetto Pavilion", _arcs((13, 91, 82), (149, 179, 34)), 84),
    "palm-beach": ("Palm Beach", _arcs((9, 107, 102), (153, 175, 26)), 80),
    "ocean-reef-park": ("Ocean Reef Park", _arcs((5, 111, 110), (157, 171, 18)), 80),
    "bathtub-beach": ("Bathtub Beach", _arcs((349, 155, 170)), 72),
}

# What R1–R3 would give Hillsboro Beach on run 18's rays: the south-east gap open.
OPENED = _arcs((17, 175, 162))


def test_the_twenty_one_held_spots_and_their_windows():
    holds = load_holds()
    assert {k: (v["name"], v["swell_window_arcs"], v["optimal_swell_dir"]) for k, v in holds.items()} == HELD


def test_the_hold_is_main_s_window_today():
    """The hold changes nothing on the day it is set: each entry is the roster's window."""
    roster = {slug(s["name"]): s for s in json.loads(Path(DEFAULT_ENRICHED_OUTPUT).read_text())}
    for key, (name, arcs, optimal) in HELD.items():
        s = roster[key]
        assert (s["name"], s["swell_window_arcs"], s["optimal_swell_dir"]) == (name, arcs, optimal), key


def test_the_note_says_why():
    doc = json.loads(SPOT_SWELL_WINDOW_HOLDS_FILE.read_text())
    assert "Great Bahama Bank" in doc["_comment"] and "TO RELEASE A SPOT" in doc["_comment"]
    assert doc["note"] == ("SE Florida, South Beach Miami to Bathtub Beach: the Great Bahama "
                           "Bank's shallow water shelters more than the land data shows.")
    assert "Eleven more were held on 2026-10-20 after full run 24" in doc["_comment"]


def test_a_full_raycast_does_not_write_a_held_spot():
    spot = {"name": "Hillsboro Beach", "swell_window_source": "nwps",
            "swell_window_arcs": _arcs((17, 127, 114), (145, 175, 34)), "optimal_swell_dir": 96}
    r = {"swell_window_arcs": OPENED, "optimal_swell_dir": 96, "swell_window_source": "raycast"}
    assert _apply_raycast_result(spot, r, load_holds()) == "held"
    assert spot["swell_window_arcs"] == _arcs((17, 127, 114), (145, 175, 34))
    assert (spot["optimal_swell_dir"], spot["swell_window_source"]) == (96, "nwps")


def test_a_full_raycast_still_writes_a_spot_not_held():
    spot = {"name": "Steamer Lane", "swell_window_source": "nwps",
            "swell_window_arcs": _arcs((181, 251, 74)), "optimal_swell_dir": 244}
    r = {"swell_window_arcs": _arcs((175, 255, 84)), "optimal_swell_dir": 230}
    assert _apply_raycast_result(spot, r, load_holds()) == "raycast"
    assert (spot["swell_window_arcs"], spot["optimal_swell_dir"]) == (_arcs((175, 255, 84)), 230)


def test_enrich_keeps_a_held_spot_over_carried_forward_arcs():
    """Algo 2a: enrich --skip-raycast carries the review branch's arcs forward; a held spot
    takes the hold instead."""
    spot = {"name": "Jupiter Inlet", "lat": 26.9437, "lng": -80.0712, "orientation_deg": 75.0,
            "swell_window_source": "nwps", "swell_window_arcs": OPENED, "optimal_swell_dir": 80}
    prior = {"Jupiter Inlet": {"swell_window_arcs": OPENED, "optimal_swell_dir": 80,
                               "swell_window_source": "nwps", "swell_window_confidence": 0.5}}
    out = E._enrich_one(spot, skip_raycast=True, prior_arcs=prior)
    assert out["swell_window_arcs"] == _arcs((145, 159, 18), (353, 111, 122))
    assert out["optimal_swell_dir"] == 72


def test_the_hold_returns_a_copy():
    holds = load_holds()
    spot = {"name": "Hobe Beach"}
    assert apply_hold(spot, holds)
    spot["swell_window_arcs"][0]["min"] = 0
    assert holds["hobe-beach"]["swell_window_arcs"][0]["min"] == 121


def test_no_file_is_no_holds(tmp_path):
    assert load_holds(tmp_path / "absent.json") == {}


def test_a_file_that_cannot_be_read_stops_the_run(tmp_path):
    bad = tmp_path / "holds.json"
    bad.write_text("{not json")
    with pytest.raises(ValueError, match="cannot be applied"):
        load_holds(bad)
    bad.write_text(json.dumps({"holds": {"x": {"optimal_swell_dir": 90}}}))
    with pytest.raises(ValueError, match="'x'"):
        load_holds(bad)
    bad.write_text(json.dumps({"holds": {"x": {"swell_window_arcs": _arcs((1, 9, 12))}}}))
    with pytest.raises(ValueError, match="'x'"):
        load_holds(bad)


def test_spoils_keeps_its_hand_set_optimal_through_enrich():
    """Spoils is held AND has an optimal override (spot_swell_windows.json, 40). Algo 2a writes
    the held [13-83] and 40; Algo 2d writes 40 and prunes arcs further than 90° from it:
    [13-83] reaches 13 - 2 (pad) = 11°, 29° from 40, so it is kept. Carried forward here are
    run 24's arcs for Spoils, [13-72, 92-175]."""
    spot = {"name": "Spoils", "lat": 26.0956, "lng": -80.1, "orientation_deg": 95.0,
            "swell_window_source": "nwps", "swell_window_arcs": _arcs((13, 72, 63), (92, 175, 87)),
            "optimal_swell_dir": 40.0}
    prior = {"Spoils": {"swell_window_arcs": _arcs((13, 72, 63), (92, 175, 87)),
                        "optimal_swell_dir": 40.0, "swell_window_source": "nwps",
                        "swell_window_confidence": 0.5}}
    out = E._enrich_one(spot, skip_raycast=True, prior_arcs=prior)
    assert (out["swell_window_arcs"], out["optimal_swell_dir"]) == (_arcs((13, 83, 74)), 40.0)
