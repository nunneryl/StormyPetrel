"""pipeline/forecast/buoys._unique_buoy_ids: the buoys the pipeline fetches readings for."""
from __future__ import annotations

import json

from pipeline import config
from pipeline.forecast.buoys import _unique_buoy_ids


def test_nearest_buoys_first_then_fallbacks_not_already_listed():
    spots = [
        {"name": "a", "nearest_buoy_id": "46041", "fallback_buoy_ids": ["46099", "46029"]},
        {"name": "b", "nearest_buoy_id": "46099", "fallback_buoy_ids": ["46041", "46248"]},
        {"name": "c", "nearest_buoy_id": None, "fallback_buoy_ids": []},
        {"name": "d", "nearest_buoy_id": "46041"},
        {"name": "e", "nearest_buoy_id": "42035", "fallback_buoy_ids": None},
    ]
    assert _unique_buoy_ids(spots) == ["46041", "46099", "42035", "46029", "46248"]


def test_no_spot_with_a_buoy():
    assert _unique_buoy_ids([{"name": "a"}, {"name": "b", "nearest_buoy_id": None}]) == []


def test_the_real_roster():
    # Counted from the committed roster on 2026-11-06: 75 nearest buoys, and 26 fallbacks that
    # are not some spot's nearest buoy. Every one of them is fetched. Run 74's roster change
    # (2026-11-12): five are no spot's nearest buoy any more (46239, 51205, 51208, phct2, ssbn7)
    # and fifteen are new (41004, 41009, 41025, 41053, 41108, 42033, 42095, 44007, 44008, 44030,
    # 46014, 46025, 46028, 46042, 46213): 75 - 5 + 15 = 85. The fallbacks that are no spot's
    # nearest stay 26: six became nearest buoys (41009, 41108, 44008, 46025, 46042, 46213) and six
    # joined (41013, 42032, 42034, 44032, and 46239 and phct2, still fallbacks of spots run 74 held).
    roster = json.loads(config.DEFAULT_ENRICHED_OUTPUT.read_text())
    ids = _unique_buoy_ids(roster)
    nearest = {s["nearest_buoy_id"] for s in roster if s.get("nearest_buoy_id")}
    assert len(ids) == len(set(ids))
    assert set(ids[:len(nearest)]) == nearest
    assert (len(nearest), len(ids) - len(nearest)) == (85, 26)
