"""Descriptions v2 (template t4): the facts from beyond the spot's own row, and what research says
re-said in our words. Every expected value is written by hand. Best months are held (see
pipeline/descriptions.py): pipeline/best_months.py stays, and is not read here.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pipeline import descriptions as Dn

ROOT = Path(__file__).resolve().parents[2]


# --------------------------------------------------------------------------- #
# What research says, re-said, never quoted                                    #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("evidence, traits", [
    ("15th Street is a left-hand reef in Del Mar.", ("left", None)),
    ("Garbage is a right-hand reef break in San Diego County.", ("right", None)),
    ("a quality righthander over rock", ("right", None)),
    ("Cardiff Reef breaks both left and right.", ("both", None)),
    ("These swells have lefts and rights over sandbars.", ("both", None)),
    ("a left-hand and a right-hand peak", ("both", None)),
    ("Asilomar in Monterey is an exposed beach and reef break.", (None, "exposed")),
    ("Doran is a sheltered, shallow and powerful beach break.", (None, "sheltered")),
    ("exposed to north swell but sheltered from wind", (None, None)),     # both words: neither
    ("a fun wave right next to the pier", (None, None)),                  # 'right' alone: nothing
    ("", (None, None)), (None, (None, None)),
])
def test_evidence_traits(evidence, traits):
    assert Dn.evidence_traits(evidence) == traits


def test_traits_only_come_with_a_stated_break_type():
    rec = {"name": "Garbage", "state": "California", "break_type": "reef",
           "break_type_source": "researched", "break_type_evidence": "an exposed right-hand reef"}
    ctx = Dn.Context([])
    assert Dn.description_payload(rec, ctx)["hand"] == "right"
    unstated = dict(rec, break_type_source="model_recall")
    p = Dn.description_payload(unstated, ctx)
    assert "hand" not in p and "exposure" not in p and "break_type" not in p


def test_the_evidence_is_never_quoted():
    """No run of ten words of any research quote appears in its spot's description, the spot's own
    name left out of both (the template's "X is a jetty break" may match a quote's by chance)."""
    roster = json.loads((ROOT / "pipeline" / "spots_enriched.json").read_text())
    for s in roster:
        ev = s.get("break_type_evidence") or ""
        if not ev or s.get("is_valid_surf_spot") is False:
            continue
        name = s["name"].lower()
        text = Dn.render(Dn.description_payload(Dn.spot_fields(s))).lower().replace(name, " ")
        words = ev.lower().replace(name, " ").split()
        for i in range(len(words) - 9):
            assert " ".join(words[i:i + 10]) not in text, (s["name"], words[i:i + 10])


# --------------------------------------------------------------------------- #
# Buoy and station, cam, nearby                                                #
# --------------------------------------------------------------------------- #
ROSTER = [
    {"name": "Alpha Point, California", "region_hint": "California", "lat": 36.0, "lng": -122.0},
    {"name": "Beta Reef", "region_hint": "California", "lat": 36.01, "lng": -122.0},     # 1.1 km
    {"name": "Gamma Jetty", "region_hint": "California", "lat": 36.05, "lng": -122.0},   # 5.6 km
    {"name": "Delta", "region_hint": "California", "lat": 36.10, "lng": -122.0},         # 11.1 km
    {"name": "Epsilon", "region_hint": "California", "lat": 36.30, "lng": -122.0},       # 33 km
    {"name": "Closed", "region_hint": "California", "lat": 36.001, "lng": -122.0,
     "is_valid_surf_spot": False},
]
CAMS = [{"spot_slug": "alpha-point-california", "cam_name": "Alpha Cam", "attribution": "Alpha Surf Club"}]
NAMES = {"9413745": "Santa Cruz, Monterey Bay"}
BUOYS = {"46042": {"ndbc_name": "MONTEREY - 27NM WNW of Monterey, CA", "name": "Monterey",
                   "type": "3-meter foam buoy w/ seal cage"},
         "41067": {"ndbc_name": "FRP2WAVE", "name": None, "type": "Waverider Buoy"},
         "ljpc1": {"ndbc_name": "La Jolla, CA (073)", "name": "La Jolla", "type": "C-MAN Station"}}
CTX = Dn.Context(ROSTER, CAMS, NAMES, BUOYS)
ALPHA = {"name": "Alpha Point, California", "state": "California", "lat": 36.0, "lng": -122.0,
         "break_type": "point", "break_type_source": "reviewed",
         "nearest_buoy_id": "46042", "nearest_buoy_dist_km": 29.5,
         "nearest_tide_station_id": "9413745", "nearest_tide_station_dist_km": 1.4}


def test_the_full_payload():
    assert Dn.description_payload(ALPHA, CTX) == {
        "name": "Alpha Point", "state": "California", "break_type": "point",
        "buoy": ["46042", "Monterey", 30, "buoy"],
        "tide_station": ["9413745", "Santa Cruz, Monterey Bay", 1, False],
        "cam": "Alpha Surf Club",
        "nearby": [["Beta Reef", 1], ["Gamma Jetty", 6], ["Delta", 11]]}


def test_the_full_text():
    assert Dn.render(Dn.description_payload(ALPHA, CTX)) == (
        "Alpha Point is a point break in California. Its nearest wave buoy is the Monterey buoy "
        "(NDBC 46042), 30 km away, and its tides come from NOAA's Santa Cruz, Monterey Bay station, "
        "1 km away. This page has a live cam from Alpha Surf Club. Nearby spots: Beta Reef (1 km), "
        "Gamma Jetty (6 km) and Delta (11 km).")


def test_no_noaa_station_number_in_the_text():
    text = Dn.render(Dn.description_payload(ALPHA, CTX))
    assert "9413745" not in text


def test_a_buoy_ndbc_gives_no_name_is_named_by_its_number():
    p = Dn.description_payload(dict(ALPHA, nearest_buoy_id="41067"), CTX)
    assert "Its nearest wave buoy is NDBC buoy 41067, 30 km away" in Dn.render(p)


def test_a_buoy_missing_from_the_names_file_is_named_by_its_number():
    p = Dn.description_payload(dict(ALPHA, nearest_buoy_id="46999"), CTX)
    assert "Its nearest wave buoy is NDBC buoy 46999, 30 km away" in Dn.render(p)


def test_a_station_ndbc_does_not_call_a_buoy_is_not_called_one():
    p = Dn.description_payload(dict(ALPHA, nearest_buoy_id="ljpc1"), CTX)
    assert "Its nearest wave station is the La Jolla station (NDBC LJPC1), 30 km away" in Dn.render(p)


def test_a_buoy_beyond_150_km_is_not_named():
    far = dict(ALPHA, nearest_buoy_dist_km=150.6)
    assert "buoy" not in Dn.description_payload(far, CTX)
    assert Dn.description_payload(dict(ALPHA, nearest_buoy_dist_km=150.0), CTX)["buoy"][2] == 150


def test_a_station_trusted_for_timing_only_says_so():
    caspar = dict(ALPHA, nearest_tide_station_id="9416841", nearest_tide_station_dist_km=50.5,
                  tide_height_suppressed=True)
    ctx = Dn.Context([], None, {"9416841": "Arena Cove"})
    text = Dn.render(Dn.description_payload(caspar, ctx))
    assert "its tide times (not heights) come from NOAA's Arena Cove station, 51 km away" in text, text


def test_an_unnamed_station_says_so_without_a_number():
    p = Dn.description_payload(dict(ALPHA, nearest_tide_station_id="8999999"), CTX)
    text = Dn.render(p)
    assert "its tides come from a NOAA tide station, 1 km away" in text and "8999999" not in text


def test_no_neighbours_and_no_cam_say_nothing():
    lonely = dict(ALPHA, lat=10.0, lng=10.0, name="Lonely")
    p = Dn.description_payload(lonely, CTX)
    assert "nearby" not in p and "cam" not in p
    assert "Nearby" not in Dn.render(p)


def test_best_months_are_held():
    """Not in t4: the forecast archive starts in April 2026 (no winter yet). See the module doc."""
    text = Dn.render(Dn.description_payload(ALPHA, CTX))
    assert "forecasts from" not in text and "stars" not in text
    assert not hasattr(Dn, "_best")
    assert "Revisit around April 2027" in Dn.__doc__


def test_the_nearby_sentence_keeps_the_shape_the_page_links():
    """frontend/lib/spotDescription.ts parses this sentence; the shape is a contract."""
    text = Dn.render(Dn.description_payload(ALPHA, CTX))
    assert text.endswith(". Nearby spots: Beta Reef (1 km), Gamma Jetty (6 km) and Delta (11 km).")


@pytest.mark.parametrize("name, state, shown", [
    ("Dana Point, California", "California", "Dana Point"),
    ("Sunset Beach Hawaii", "Hawaii", "Sunset Beach"),
    ("Virginia Beach Oceanfront", "Virginia", "Virginia Beach Oceanfront"),
    ("Hawaii", "Hawaii", "Hawaii"),
])
def test_the_shown_name(name, state, shown):
    assert Dn.display_name(name, state) == shown


# --------------------------------------------------------------------------- #
# The committed files                                                          #
# --------------------------------------------------------------------------- #
def test_every_roster_tide_station_has_its_name():
    roster = json.loads((ROOT / "pipeline" / "spots_enriched.json").read_text())
    names = json.loads((ROOT / "pipeline" / "data" / "tide_station_names.json").read_text())["stations"]
    used = {s["nearest_tide_station_id"] for s in roster if s.get("nearest_tide_station_id")}
    assert used - set(names) == set(), sorted(used - set(names))
    assert not [n for n in names.values() if n.isupper()]


def test_every_roster_buoy_within_150_km_has_its_ndbc_entry():
    roster = json.loads((ROOT / "pipeline" / "spots_enriched.json").read_text())
    buoys = json.loads((ROOT / "pipeline" / "data" / "buoy_names.json").read_text())["stations"]
    used = {s["nearest_buoy_id"].lower() for s in roster
            if s.get("is_valid_surf_spot") is not False and s.get("nearest_buoy_id")
            and s.get("nearest_buoy_dist_km") is not None
            and s["nearest_buoy_dist_km"] <= Dn.BUOY_MAX_KM}
    assert used - set(buoys) == set(), sorted(used - set(buoys))
    for bid, b in buoys.items():
        assert b["ndbc_name"] and b["type"], bid
        # a shortened name never keeps NDBC's codes: CDIP numbers, state abbreviations, offsets
        assert b["name"] is None or not re.search(r"\(\d+\)|, [A-Z]{2}$| NM | - ", b["name"]), b


def test_the_default_context_reads_the_committed_files():
    Dn.default_context.cache_clear()
    ctx = Dn.default_context()
    assert len(ctx.spots) >= 600 and "daytona-beach" in ctx.cams and ctx.station_names["1612340"] == "Honolulu"
    assert ctx.buoy_names["51201"]["name"] == "Waimea Bay"
