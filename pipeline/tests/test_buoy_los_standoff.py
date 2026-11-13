"""Line of sight to a buoy is drawn from 250 m beyond the rule's point, away from the coast.

enrichment/buoys._los_start; config.BUOY_LOS_STANDOFF_M. Run 74 found the working buoys of 62
spots blocked by land; at Richardson Beach, Fenwick Island and Makaha the line from a point 17 to
30 m off GSHHG's shoreline grazed the shore beside the spot on its way to a buoy along the coast.

A synthetic coast at the equator, worked by hand (1 deg of longitude = 111,320 m there on the
WGS84 ellipsoid): land is every longitude west of 0 (lng -1..0, lat -1..1), with a small point
sticking out 111 m (to lng 0.001) between lat 0.001 and 0.002. The sea is east of lng 0.
"""
from __future__ import annotations

import pytest
from shapely.geometry import LineString, box
from shapely.strtree import STRtree

from pipeline import config
from pipeline.enrichment import buoys as B
from pipeline.enrichment.geodata import LandIndex


def _land(*extra):
    polys = [box(-1.0, -1.0, 0.0, 1.0), box(-0.0001, 0.001, 0.001, 0.002), *extra]
    coasts = [LineString(list(p.exterior.coords)) for p in polys]
    return LandIndex(polygons=polys, polygon_tree=STRtree(polys), coastlines=coasts,
                     coastline_tree=STRtree(coasts))


@pytest.fixture(autouse=True)
def _fresh_cache():
    B._LOS_START.clear()
    yield
    B._LOS_START.clear()


def _spot(lat, lng):
    return {"lat": lat, "lng": lng, "_algo_lat": lat, "_algo_lng": lng, "region_hint": "Hawaii"}


START = (0.0, 0.00027)            # 30 m east of the coast: 0.00027 x 111,320 = 30.06 m
ALONGSHORE = {"id": "a1", "lat": 0.05, "lng": 0.0005}   # 5.56 km north, 56 m off the coast
BEHIND = {"id": "b1", "lat": 0.0, "lng": -1.5}          # across the land, 167 km west
PUSHED_LNG = 0.00027 + 250 / 111_320                    # 0.0025158: 250 m further east


def test_the_standoff_is_250_m():
    assert config.BUOY_LOS_STANDOFF_M == 250.0


def test_the_start_moves_250_m_away_from_the_nearest_coast():
    # The nearest coast is the main shore at lng 0 (30 m west), not the point (111 m north).
    lat, lng = B._los_start(*START, _land(), 250.0)
    assert lat == pytest.approx(0.0, abs=1e-9)
    assert lng == pytest.approx(PUSHED_LNG, abs=1e-6)


def test_from_the_rules_point_the_alongshore_line_clips_the_point(monkeypatch):
    # The old rule (standoff 0): at lat 0.001 the line is at lng 0.00027 + 0.02 x 0.00023 =
    # 0.000275, inside the point (lng -0.0001..0.001): blocked.
    monkeypatch.setattr(B, "BUOY_LOS_STANDOFF_M", 0.0)
    assert B._los_clear(_spot(*START), ALONGSHORE, _land()) is False


def test_from_250_m_out_the_alongshore_line_is_clear():
    # From lng 0.0025158 the line stays east of lng 0.0024 up to lat 0.002, clear of the point.
    assert B._los_clear(_spot(*START), ALONGSHORE, _land()) is True


def test_land_in_the_way_still_blocks():
    assert B._los_clear(_spot(*START), BEHIND, _land()) is False


def test_a_start_on_land_is_pushed_out_through_the_coast():
    # 11 m inland (lng -0.0001): pushed 250 m east, to lng -0.0001 + 0.0022458 = 0.0021458.
    lat, lng = B._los_start(0.0, -0.0001, _land(), 250.0)
    assert lng == pytest.approx(-0.0001 + 250 / 111_320, abs=1e-6)
    assert B._los_clear(_spot(0.0, -0.0001), ALONGSHORE, _land()) is True


def test_a_pushed_point_on_land_falls_back_to_the_rules_point():
    # An islet where the pushed point would fall (lng 0.0024..0.0030): the start stays put, and
    # the line clips the point again.
    land = _land(box(0.0024, -0.0005, 0.0030, 0.0005))
    assert B._los_start(*START, land, 250.0) == START
    assert B._los_clear(_spot(*START), ALONGSHORE, land) is False


def test_distances_are_still_from_the_rules_point(monkeypatch):
    # compute_nearest_buoy over the synthetic coast: the alongshore buoy passes (line of sight
    # from 250 m out) and its distance is from the rule's point: haversine on R = 6371 km,
    # 0.05 deg of latitude = 5,559.5 m, 0.00023 deg of longitude = 25.6 m, together 5.56 km.
    land = _land()
    monkeypatch.setattr(B, "load_ndbc_wave_stations", lambda: [ALONGSHORE, BEHIND])
    monkeypatch.setattr(B, "load_land_index", lambda: land)
    B._build_kdtree.cache_clear()
    try:
        r = B.compute_nearest_buoy(_spot(*START))
    finally:
        B._build_kdtree.cache_clear()
    assert (r["nearest_buoy_id"], r["nearest_buoy_dist_km"], r["fallback_buoy_ids"]) == ("a1", 5.56, [])
