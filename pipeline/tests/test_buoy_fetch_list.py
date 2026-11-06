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
    # are not some spot's nearest buoy. Every one of them is fetched.
    roster = json.loads(config.DEFAULT_ENRICHED_OUTPUT.read_text())
    ids = _unique_buoy_ids(roster)
    nearest = {s["nearest_buoy_id"] for s in roster if s.get("nearest_buoy_id")}
    assert len(ids) == len(set(ids))
    assert set(ids[:len(nearest)]) == nearest
    assert (len(nearest), len(ids) - len(nearest)) == (75, 26)
