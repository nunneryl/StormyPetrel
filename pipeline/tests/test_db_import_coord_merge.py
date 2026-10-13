"""Synthetic tests for db_import's coord-change preserve-merge exclusion (mode-(b) fix).

The SELECT-then-merge fills any column absent from the freshly-enriched record with the current DB
value. That is right for most columns, but for coordinate-DERIVED fields (buoy, tide, nwps_wfo) it
resurrected a value computed for the OLD location onto a spot that had MOVED — how a Newport-Beach
buoy stayed on 56th Street after it moved to New Jersey. When coordinates change, those fields must be
left absent (→ NULLed) so the next enrich recomputes them.

Run: python -m pipeline.tests.test_db_import_coord_merge   (or pytest)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path

from pipeline import db_import


def test_coords_changed_unit():
    same = {"lat": 33.6239, "lng": -117.9459}
    assert db_import._coords_changed(same, dict(same)) is False
    assert db_import._coords_changed(same, {"lat": 33.62391, "lng": -117.94591}) is False  # sub-epsilon
    assert db_import._coords_changed(same, {"lat": 39.14, "lng": -74.70}) is True           # moved states
    assert db_import._coords_changed({"lat": None, "lng": None}, same) is False             # missing -> preserve


class _Result:
    def __init__(self, data): self.data = data


class _Query:
    def __init__(self, client): self.c = client; self._sel = None; self._rng = None; self._in = None
    def select(self, cols): self._sel = cols; return self
    def range(self, a, b): self._rng = (a, b); return self
    def in_(self, col, vals): self._in = (col, vals); return self
    def eq(self, *a): return self
    def upsert(self, chunk, on_conflict=None): self.c.upserted.extend(chunk); return self
    def delete(self): self._del = True; return self
    def execute(self):
        if self._sel and "*" in self._sel and self._in is None:
            a, b = self._rng or (0, 10**9)
            return _Result([dict(r) for r in self.c.rows[a:b + 1]])
        return _Result([])          # excluded-lookup / delete


class _Client:
    def __init__(self, rows): self.rows = rows; self.upserted = []
    def table(self, _name): return _Query(self)


def _run_import(existing_rows, enriched_spots):
    saved = db_import._excluded_slugs
    db_import._excluded_slugs = lambda: set()          # no exclusion-file dependency
    try:
        tmp = Path(tempfile.mkdtemp()) / "spots.json"
        tmp.write_text(json.dumps(enriched_spots))
        client = _Client(existing_rows)
        db_import.import_spots(client, spots_path=tmp)
        return {r["slug"]: r for r in client.upserted}
    finally:
        db_import._excluded_slugs = saved


def test_moved_spot_does_not_inherit_stale_buoy_tide_wfo():
    # DB row: a California spot with CA buoy/tide/wfo. Enriched: same spot MOVED to New Jersey with its
    # coord-derived fields cleared (as cleanup_spots leaves them). They must NOT be resurrected.
    existing = [{
        "slug": "56th-street", "name": "56th Street", "lat": 33.6239, "lng": -117.9459,
        "nearest_buoy_id": "46253", "nearest_buoy_dist_km": 22.26, "nwps_wfo": "lox",
        "nearest_tide_station_id": "9410660", "state": "California",
    }]
    enriched = [{"name": "56th Street", "lat": 39.1416, "lng": -74.6968, "region_hint": "New Jersey",
                 "is_valid_surf_spot": True}]
    up = _run_import(existing, enriched)["56th-street"]
    assert up["lat"] == 39.1416
    for f in ("nearest_buoy_id", "nearest_buoy_dist_km", "nwps_wfo", "nearest_tide_station_id"):
        assert f not in up or up[f] is None, f"{f} must NOT be preserved onto moved coords (got {up.get(f)!r})"


def test_unmoved_spot_still_preserves_absent_fields():
    # Same coords -> the preserve safety net still fills an absent buoy from the DB (unchanged behavior).
    existing = [{
        "slug": "steamer-lane", "name": "Steamer Lane", "lat": 36.9513, "lng": -122.0266,
        "nearest_buoy_id": "46042", "nearest_buoy_dist_km": 40.0, "nwps_wfo": "mtr",
        "state": "California",
    }]
    enriched = [{"name": "Steamer Lane", "lat": 36.9513, "lng": -122.0266, "region_hint": "California",
                 "is_valid_surf_spot": True}]
    up = _run_import(existing, enriched)["steamer-lane"]
    assert up.get("nearest_buoy_id") == "46042", "unmoved spot must still inherit its buoy from the DB"
    assert up.get("nwps_wfo") == "mtr"


# --------------------------------------------------------------------------- #
# Descriptions under signature v2 (pipeline/descriptions.py)                   #
# --------------------------------------------------------------------------- #
# Steamer Lane as the roster gives it. Its payload, written out by hand (no swell window, which is
# never sent; tide stated because its source is researched):
#   {"bottom":"rock","break_type":"point","faces":"SE","name":"Steamer Lane","offshore_wind_deg":308,
#    "offshore_wind_from":"NW","optimal_swell_from":"SW","orientation_deg":128,"state":"California",
#    "tide_preference":"all"}
# and its signature, 'v2:' + sha1("t1\n" + that)[:16], computed with hashlib directly:
STEAMER = {"name": "Steamer Lane", "lat": 36.9513, "lng": -122.0266, "region_hint": "California",
           "orientation_deg": 128, "offshore_wind_deg": 308, "optimal_swell_dir": 225,
           "swell_window_arcs": [{"min": 180, "max": 315}], "tide_preference": "all",
           "tide_preference_source": "researched",
           "break_type": "point", "break_type_source": "researched",
           "bottom": "rock", "bottom_source": "researched", "is_valid_surf_spot": True}
STEAMER_V2 = "v2:98f76133915fd7cc"
STEAMER_ROW = {"slug": "steamer-lane", "name": "Steamer Lane", "lat": 36.9513, "lng": -122.0266,
               "state": "California", "orientation_deg": 128,
               "description": "Steamer Lane is a point break over rock.",
               "description_signature": STEAMER_V2}


def test_a_matching_v2_signature_keeps_the_description_and_its_signature():
    up = _run_import([dict(STEAMER_ROW)], [dict(STEAMER)])["steamer-lane"]
    assert up["description"] == "Steamer Lane is a point break over rock."
    assert up["description_signature"] == STEAMER_V2


def test_a_v2_signature_that_no_longer_matches_blanks_the_text_and_keeps_the_signature():
    """The research import corrects a break type: the description states the old one, so it goes.
    db_import never writes a signature: the stored one stays until the generator replaces both."""
    moved = dict(STEAMER, break_type="reef")
    up = _run_import([dict(STEAMER_ROW)], [moved])["steamer-lane"]
    assert up["description"] is None
    assert up["description_signature"] == STEAMER_V2


def test_a_source_crossing_the_stated_line_is_a_change():
    """researched -> model_recall takes break_type and bottom out of the payload, so the text that
    states them is stale even though the values did not change."""
    recalled = dict(STEAMER, break_type_source="model_recall", bottom_source="model_recall")
    up = _run_import([dict(STEAMER_ROW)], [recalled])["steamer-lane"]
    assert up["description"] is None


def test_the_payload_is_read_from_the_roster_before_the_database_fills_it():
    """A roster spot without tide_preference or its source: the database's researched 'low' is merged
    into the row, but the generator never saw it, so it must not count as a change. The payload with
    the database's tide would differ from the stored one; only the roster's view matches."""
    no_tide = {k: v for k, v in STEAMER.items()
               if k not in ("tide_preference", "tide_preference_source")}
    stored = "v2:e625e37d8a182bf3"   # the payload above without "tide_preference":"all", by hashlib
    row = dict(STEAMER_ROW, description_signature=stored, tide_preference="low",
               tide_preference_source="researched")
    up = _run_import([row], [no_tide])["steamer-lane"]
    assert up["description"] == "Steamer Lane is a point break over rock."
    assert up["tide_preference"] == "low"


def test_a_version_1_signature_leaves_the_text_alone_and_is_not_rewritten():
    """The changeover: every stored signature is version 1 when v2 is merged. Comparing it would blank
    all 646 descriptions in one run; re-stamping it would hide the old text from the generator. So
    both the text and the version-1 signature pass through untouched, whatever has changed."""
    v1 = "a1b2c3d4e5f60718"
    row = dict(STEAMER_ROW, description_signature=v1)
    up = _run_import([row], [dict(STEAMER, break_type="reef", region_hint="Oregon")])["steamer-lane"]
    assert up["description"] == "Steamer Lane is a point break over rock."
    assert up["description_signature"] == v1


def test_a_missing_signature_leaves_the_text_alone():
    for case in ("null", "absent"):
        row = dict(STEAMER_ROW)
        if case == "null":
            row["description_signature"] = None
        else:
            del row["description_signature"]
        up = _run_import([row], [dict(STEAMER, break_type="reef")])["steamer-lane"]
        assert up["description"] == "Steamer Lane is a point break over rock.", case
        assert up.get("description_signature") is None, case


def test_an_already_blank_description_is_not_counted_again():
    row = dict(STEAMER_ROW, description=None)
    assert db_import.description_blank_reason(
        db_import._spot_record(dict(STEAMER, break_type="reef")), row) is None
    # the reef payload, signed with hashlib: break_type "reef" in place of "point"
    assert db_import.description_blank_reason(
        db_import._spot_record(dict(STEAMER, break_type="reef")), STEAMER_ROW) == (
        "signature v2:98f76133915fd7cc != v2:4e084f7fc1acd615")


def test_fallback_buoy_ids_maps_from_source_defaulting_to_empty():
    # Migration 013 adds the column; db_import must now write the REAL fallback list from source (not
    # only [] from validation / preserved-from-DB), and [] — never null — when there is none, matching
    # the migration's DEFAULT '{}'. Same coords => no move, no validation snapshot => the mapped value
    # survives untouched to the upsert.
    for label, val, want in (("real list", ["46012", "46026"], ["46012", "46026"]),
                             ("empty", [], []),
                             ("null coerced", None, [])):
        existing = [{"slug": "x", "name": "X", "lat": 1.0, "lng": 2.0}]
        enriched = [{"name": "X", "lat": 1.0, "lng": 2.0, "region_hint": "California",
                     "fallback_buoy_ids": val, "is_valid_surf_spot": True}]
        up = _run_import(existing, enriched)["x"]
        assert up.get("fallback_buoy_ids") == want, f"{label}: got {up.get('fallback_buoy_ids')!r}"
        assert up["fallback_buoy_ids"] is not None, f"{label}: must be [] not null (matches DEFAULT '{{}}')"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} db_import coord-merge checks passed")


if __name__ == "__main__":
    _run_all()
