"""Synthetic tests for the coord-derived validation (item 4) and buoy snapshot (item 5).

Item 4 is the check whose absence let a fabricated 22 km sit next to a 4000 km truth: at import time,
recompute great-circle(spot, station) from the station's real coordinates and NULL any stored pairing
that disagrees. Item 5 is the committed id->lat/lng snapshot that makes that possible without a live
NDBC fetch. Both degrade to no-ops when the snapshot/metadata are absent (the sandbox case).

Run: python -m pipeline.tests.test_buoy_validation   (or pytest)
"""
from __future__ import annotations

from pipeline import db_import, snapshot_buoys
from pipeline.enrichment import geodata


def test_validate_nulls_inconsistent_buoy_keeps_consistent():
    saved_b, saved_t = geodata.load_buoy_snapshot, geodata.load_tide_stations
    geodata.load_buoy_snapshot = lambda: {
        "46253": {"lat": 33.576, "lng": -118.181, "name": "San Pedro"},   # California
        "44091": {"lat": 39.778, "lng": -73.770, "name": "Barnegat"},     # New Jersey
    }
    geodata.load_tide_stations = lambda: []
    try:
        recs = [
            # 56th St NJ coords with the CALIFORNIA buoy 46253 and a fabricated 22 km -> ~3900 km truth.
            {"name": "56th Street", "lat": 39.1416, "lng": -74.6968,
             "nearest_buoy_id": "46253", "nearest_buoy_dist_km": 22.26, "fallback_buoy_ids": ["x"]},
            # A genuine NJ spot near buoy 44091 -> distance reproduces -> kept.
            {"name": "Good NJ", "lat": 39.75, "lng": -74.10,
             "nearest_buoy_id": "44091", "nearest_buoy_dist_km": None},
            # Unknown id -> can't compute -> left untouched (incomplete metadata is not proof of error).
            {"name": "Unknown", "lat": 34.0, "lng": -118.0,
             "nearest_buoy_id": "99999", "nearest_buoy_dist_km": 5.0},
        ]
        n = db_import._validate_coord_derived(recs)
        assert n == 1
        assert recs[0]["nearest_buoy_id"] is None and recs[0]["nearest_buoy_dist_km"] is None
        assert recs[0]["fallback_buoy_ids"] == []
        assert recs[1]["nearest_buoy_id"] == "44091", "a distance-consistent pairing is kept"
        assert recs[2]["nearest_buoy_id"] == "99999", "an unknown id can't be validated, so it's not touched"
    finally:
        geodata.load_buoy_snapshot, geodata.load_tide_stations = saved_b, saved_t


def test_validate_nulls_inconsistent_tide_station():
    saved_b, saved_t = geodata.load_buoy_snapshot, geodata.load_tide_stations
    geodata.load_buoy_snapshot = lambda: {}
    geodata.load_tide_stations = lambda: [{"id": "9410170", "lat": 32.71, "lng": -117.17, "name": "San Diego"}]
    try:
        recs = [{"name": "North Jetty (FL, stale SD tide)", "lat": 27.4742, "lng": -80.2889,
                 "nearest_tide_station_id": "9410170", "nearest_tide_station_dist_km": 3.0}]
        n = db_import._validate_coord_derived(recs)
        assert n == 1 and recs[0]["nearest_tide_station_id"] is None
    finally:
        geodata.load_buoy_snapshot, geodata.load_tide_stations = saved_b, saved_t


def test_validate_is_noop_without_snapshot():
    saved_b, saved_t = geodata.load_buoy_snapshot, geodata.load_tide_stations
    geodata.load_buoy_snapshot = lambda: {}
    geodata.load_tide_stations = lambda: []
    try:
        recs = [{"name": "X", "lat": 1.0, "lng": 2.0, "nearest_buoy_id": "46253", "nearest_buoy_dist_km": 22.0}]
        assert db_import._validate_coord_derived(recs) == 0
        assert recs[0]["nearest_buoy_id"] == "46253", "no snapshot -> no changes (can't fail CI)"
    finally:
        geodata.load_buoy_snapshot, geodata.load_tide_stations = saved_b, saved_t


def test_build_snapshot_shape():
    saved = snapshot_buoys.load_ndbc_active_stations
    snapshot_buoys.load_ndbc_active_stations = lambda: [
        {"id": "46042", "lat": 36.75, "lng": -122.40, "name": "Monterey"},
        {"id": "", "lat": 1.0, "lng": 2.0, "name": "no id — dropped"},
    ]
    try:
        snap = snapshot_buoys.build_snapshot()
        assert snap == {"46042": {"lat": 36.75, "lng": -122.40, "name": "Monterey"}}
    finally:
        snapshot_buoys.load_ndbc_active_stations = saved


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} buoy-validation checks passed")


if __name__ == "__main__":
    _run_all()
