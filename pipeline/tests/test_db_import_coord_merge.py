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


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} db_import coord-merge checks passed")


if __name__ == "__main__":
    _run_all()
