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


def test_description_signature_unit():
    s1 = db_import.description_signature(39.14, -74.70, "New Jersey", 115)
    assert s1 == db_import.description_signature(39.1401, -74.6999, "new jersey", 115.0), \
        "rounded coords / case-folded state / int-vs-float orientation are stable"
    assert s1 != db_import.description_signature(33.62, -117.95, "California", 115), "coords+state change"
    assert s1 != db_import.description_signature(39.14, -74.70, "New Jersey", 202), "orientation change"


def test_stale_description_is_blanked_on_signature_mismatch():
    # DB description was written against California metadata; the spot is now New Jersey -> the
    # description ("in California") contradicts the record and must be blanked so it can't persist.
    ca_sig = db_import.description_signature(33.6239, -117.9459, "California", 115)
    existing = [{"slug": "56th-street", "name": "56th Street", "lat": 33.6239, "lng": -117.9459,
                 "state": "California", "orientation_deg": 115,
                 "description": "A fun California beach break.", "description_signature": ca_sig}]
    enriched = [{"name": "56th Street", "lat": 39.1416, "lng": -74.6968, "region_hint": "New Jersey",
                 "orientation_deg": 115, "is_valid_surf_spot": True}]
    up = _run_import(existing, enriched)["56th-street"]
    assert up["description"] is None, "a description that no longer matches its record must be blanked"
    assert up["description_signature"] == db_import.description_signature(39.1416, -74.6968, "New Jersey", 115)


def test_matching_signature_preserves_description():
    sig = db_import.description_signature(36.9513, -122.0266, "California", 128)
    existing = [{"slug": "steamer-lane", "name": "Steamer Lane", "lat": 36.9513, "lng": -122.0266,
                 "state": "California", "orientation_deg": 128,
                 "description": "World-class right at Santa Cruz.", "description_signature": sig}]
    enriched = [{"name": "Steamer Lane", "lat": 36.9513, "lng": -122.0266, "region_hint": "California",
                 "orientation_deg": 128, "is_valid_surf_spot": True}]
    up = _run_import(existing, enriched)["steamer-lane"]
    assert up.get("description") == "World-class right at Santa Cruz.", "matching signature keeps the text"


def test_null_stored_signature_does_not_blank_and_backfills():
    # First run after migration 012 adds description_signature: it is NULL for all 668 existing rows. A
    # NULL stored signature means "not yet tracked", NOT "mismatch" — so it must NOT blank the (correct)
    # description. If it did, every description would blank in one run and stay blank (the offline
    # generator isn't in the pipeline). The guard is `base.get("description_signature") and ...`: a NULL
    # (or absent) stored signature is falsy and short-circuits the blank. The signature is still stamped,
    # giving every subsequent run a baseline to compare against.
    #   case "null"   -> column present, value None (the realistic production first-run for all 668 rows)
    #   case "absent" -> column entirely missing from the row (defensive: same falsy path)
    for case in ("null", "absent"):
        base = {"slug": "steamer-lane", "name": "Steamer Lane", "lat": 36.9513, "lng": -122.0266,
                "state": "California", "orientation_deg": 128,
                "description": "World-class right at Santa Cruz."}
        if case == "null":
            base["description_signature"] = None
        enriched = [{"name": "Steamer Lane", "lat": 36.9513, "lng": -122.0266, "region_hint": "California",
                     "orientation_deg": 128, "is_valid_surf_spot": True}]
        up = _run_import([base], enriched)["steamer-lane"]
        assert up.get("description") == "World-class right at Santa Cruz.", \
            f"a {case} stored signature is 'not yet tracked', not a mismatch — the description must survive"
        assert up["description_signature"] == db_import.description_signature(36.9513, -122.0266, "California", 128), \
            f"the signature must be backfilled ({case} case) so the next run has a baseline"


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
