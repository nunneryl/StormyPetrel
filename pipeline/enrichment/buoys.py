"""Algorithm 4 — nearest NDBC wave buoy with line-of-sight filter + regional caps."""
from __future__ import annotations

import logging
import math
from functools import lru_cache

from pyproj import Geod
from shapely.geometry import LineString

from ..config import (
    BUOY_CAP_DEFAULT,
    BUOY_CAP_DEFAULT_EAST,
    BUOY_CAP_GULF_FLORIDA,
    BUOY_CAP_KM,
)
from ..geo import haversine_m
from .geodata import load_land_index, load_ndbc_wave_stations

log = logging.getLogger(__name__)

_GEOD = Geod(ellps="WGS84")
_GREAT_LAKES_STATES = frozenset({
    "Michigan", "Wisconsin", "Minnesota", "Illinois", "Indiana", "Ohio",
    "Pennsylvania", "New York",
})


def _regional_cap_km(spot: dict) -> float:
    region = (spot.get("region_hint") or "").strip()
    # Great Lakes override: the eight bordering states surf the lakes.
    if region in _GREAT_LAKES_STATES and spot.get("lat", 0) > 40 and spot.get("lng", 0) < -70:
        return BUOY_CAP_KM["Great Lakes"]
    if region == "Florida" and spot.get("lng", 0) < -83:
        return BUOY_CAP_GULF_FLORIDA
    if region in BUOY_CAP_KM:
        return BUOY_CAP_KM[region]
    # Atlantic east-coast default.
    east_coast = {
        "Maine", "New Hampshire", "Massachusetts", "Rhode Island", "Connecticut",
        "New York", "New Jersey", "Delaware", "Maryland", "Virginia",
        "North Carolina", "South Carolina", "Georgia", "Florida",
    }
    if region in east_coast:
        return BUOY_CAP_DEFAULT_EAST
    return BUOY_CAP_DEFAULT


def _latlng_to_unit_xyz(lat: float, lng: float) -> tuple[float, float, float]:
    phi = math.radians(lat)
    lam = math.radians(lng)
    return (math.cos(phi) * math.cos(lam), math.cos(phi) * math.sin(lam), math.sin(phi))


@lru_cache(maxsize=1)
def _build_kdtree():
    """Return (kdtree, stations) or (None, []) if scipy/stations are unavailable."""
    stations = load_ndbc_wave_stations()
    if not stations:
        return None, []
    try:
        from scipy.spatial import cKDTree
    except ImportError:
        log.warning("scipy not available; buoy KDTree disabled")
        return None, stations
    xyz = [_latlng_to_unit_xyz(s["lat"], s["lng"]) for s in stations]
    return cKDTree(xyz), stations


def _geodesic_line(lat1: float, lng1: float, lat2: float, lng2: float, n_intermediate: int = 50) -> LineString:
    pts = [(lng1, lat1)]
    if n_intermediate > 0:
        pts.extend((lon, la) for lon, la in _GEOD.npts(lng1, lat1, lng2, lat2, n_intermediate))
    pts.append((lng2, lat2))
    return LineString(pts)


def _los_clear(spot: dict, station: dict, land) -> bool:
    line = _geodesic_line(spot["lat"], spot["lng"], station["lat"], station["lng"])
    try:
        candidates = land.polygon_tree.query(line)
    except Exception:
        return True
    for c in candidates:
        poly = land.polygons[int(c)] if hasattr(c, "__index__") else c
        if line.intersects(poly):
            return False
    return True


def compute_nearest_buoy(spot: dict, k_candidates: int = 10) -> dict:
    tree, stations = _build_kdtree()
    land = load_land_index()
    if tree is None or land is None or not stations:
        return {
            "nearest_buoy_id": None,
            "nearest_buoy_dist_km": None,
            "fallback_buoy_ids": [],
            "buoy_confidence": 0.0,
        }

    cap_km = _regional_cap_km(spot)
    xyz = _latlng_to_unit_xyz(spot["lat"], spot["lng"])
    k = min(k_candidates, len(stations))
    _, idx = tree.query(xyz, k=k)
    if isinstance(idx, int):
        idx = [idx]
    else:
        idx = list(idx)

    passing: list[tuple[str, float]] = []
    for i in idx:
        s = stations[i]
        dist_m = haversine_m(spot["lat"], spot["lng"], s["lat"], s["lng"])
        dist_km = dist_m / 1000.0
        if dist_km > cap_km:
            continue
        if not _los_clear(spot, s, land):
            continue
        passing.append((s["id"], dist_km))
        if len(passing) >= 4:
            break

    if not passing:
        return {
            "nearest_buoy_id": None,
            "nearest_buoy_dist_km": None,
            "fallback_buoy_ids": [],
            "buoy_confidence": 0.0,
        }

    primary_id, primary_dist = passing[0]
    return {
        "nearest_buoy_id": primary_id,
        "nearest_buoy_dist_km": round(primary_dist, 2),
        "fallback_buoy_ids": [s for s, _ in passing[1:4]],
        "buoy_confidence": 1.0 if primary_dist <= cap_km * 0.5 else 0.75,
    }
