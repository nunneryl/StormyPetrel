"""spots.tide_height_suppressed (migration 034): the station gives the tide's timing, not its height.

db_import writes it on every spot, true where the roster says so and false otherwise, so the page
can show the four Mendocino spots their tide times (frontend/lib/tideTiming.ts). Every expected
value is written by hand.
"""
from __future__ import annotations

import json
from pathlib import Path

from pipeline import db_import as D

ROOT = Path(__file__).resolve().parents[2]


def _spot(**over):
    s = {"name": "Caspar", "lat": 39.36, "lng": -123.82, "region_hint": "California",
         "nearest_tide_station_id": "9416841", "nearest_tide_station_dist_km": 50.5}
    s.update(over)
    return s


def test_a_flagged_spot_is_written_true():
    assert D._spot_record(_spot(tide_height_suppressed=True))["tide_height_suppressed"] is True


def test_every_other_spot_is_written_false_not_left_to_the_preserve_merge():
    # absent from the roster (the usual case) and an explicit false both write false, so a
    # spot whose flag is dropped reads false at the next import rather than keeping the old true
    assert D._spot_record(_spot())["tide_height_suppressed"] is False
    assert D._spot_record(_spot(tide_height_suppressed=False))["tide_height_suppressed"] is False


def test_the_column_is_in_the_preflight_list():
    assert "tide_height_suppressed" in D.WRITTEN_COLUMNS["spots"]


def test_the_roster_flags_the_four_mendocino_spots_on_arena_cove():
    roster = json.loads((ROOT / "pipeline" / "spots_enriched.json").read_text())
    flagged = sorted((s["name"], s["nearest_tide_station_id"], s["nearest_tide_station_dist_km"])
                     for s in roster if s.get("tide_height_suppressed"))
    assert flagged == [("Caspar", "9416841", 50.5), ("Jug Handle", "9416841", 52.2),
                       ("Mackerricher", "9416841", 64.5), ("Ten Mile Beach", "9416841", 69.6)]


def test_the_migration_adds_the_column_idempotently_with_a_false_default():
    sql = (ROOT / "pipeline" / "migrations" / "034_spots_tide_height_suppressed.sql").read_text()
    assert "ADD COLUMN IF NOT EXISTS tide_height_suppressed BOOLEAN NOT NULL DEFAULT false" in sql
