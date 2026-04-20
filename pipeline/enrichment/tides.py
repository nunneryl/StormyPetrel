"""Algorithm 5 — nearest NOAA CO-OPS tide station within TIDE_STATION_MAX_DIST_KM."""
from __future__ import annotations

import logging
import math
from functools import lru_cache

from ..config import TIDE_STATION_MAX_DIST_KM
from ..geo import haversine_m
from .geodata import load_tide_stations

log = logging.getLogger(__name__)


def _unit_xyz(lat: float, lng: float) -> tuple[float, float, float]:
    phi = math.radians(lat)
    lam = math.radians(lng)
    return (math.cos(phi) * math.cos(lam), math.cos(phi) * math.sin(lam), math.sin(phi))


@lru_cache(maxsize=1)
def _build_kdtree():
    stations = load_tide_stations()
    if not stations:
        return None, []
    try:
        from scipy.spatial import cKDTree
    except ImportError:
        log.warning("scipy not available; tide KDTree disabled")
        return None, stations
    xyz = [_unit_xyz(s["lat"], s["lng"]) for s in stations]
    return cKDTree(xyz), stations


def compute_nearest_tide_station(spot: dict) -> dict:
    tree, stations = _build_kdtree()
    if tree is None or not stations:
        return {"nearest_tide_station_id": None, "nearest_tide_station_dist_km": None}

    xyz = _unit_xyz(spot["lat"], spot["lng"])
    _, idx = tree.query(xyz, k=1)
    s = stations[idx]
    dist_km = haversine_m(spot["lat"], spot["lng"], s["lat"], s["lng"]) / 1000.0
    if dist_km > TIDE_STATION_MAX_DIST_KM:
        return {"nearest_tide_station_id": None, "nearest_tide_station_dist_km": None}
    return {
        "nearest_tide_station_id": s["id"],
        "nearest_tide_station_dist_km": round(dist_km, 2),
    }
