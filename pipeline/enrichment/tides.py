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


def _nearest_by_scan(spot: dict, stations: list[dict]) -> dict | None:
    """Nearest station by straight haversine scan. Used when a caller supplies its own
    station list (tests, and any future caller with a subset) — the KDTree is built once per
    process from the global file and cannot answer for a different list."""
    best = None
    for st in stations:
        d = haversine_m(spot["lat"], spot["lng"], st["lat"], st["lng"])
        if best is None or d < best[0]:
            best = (d, st)
    return best[1] if best else None


def compute_nearest_tide_station(spot: dict, stations: list[dict] | None = None) -> dict:
    """Nearest station within TIDE_STATION_MAX_DIST_KM, or a NULL pair.

    THE ID IS RETURNED VERBATIM — `s["id"]`, no case fold, no strip, no truncation. NOAA
    subordinate ids carry a trailing letter (TWC0965F); dropping it yields an id CO-OPS does
    not know, and every spot pointing at it goes tideless with no error, because the request
    is still well-formed. test_a_suffixed_subordinate_id_survives_assignment_intact pins it.

    *stations* is an injection seam. The default path builds a KDTree once per process from
    the committed station file via @lru_cache, which is right for a 648-spot run and makes
    the function impossible to test; passing a list scans it directly instead. Same rule,
    same cap, same verbatim id either way.
    """
    if stations is not None:
        s = _nearest_by_scan(spot, stations)
        if s is None:
            return {"nearest_tide_station_id": None, "nearest_tide_station_dist_km": None}
    else:
        tree, all_stations = _build_kdtree()
        if tree is None or not all_stations:
            return {"nearest_tide_station_id": None, "nearest_tide_station_dist_km": None}
        xyz = _unit_xyz(spot["lat"], spot["lng"])
        _, idx = tree.query(xyz, k=1)
        s = all_stations[idx]
    dist_km = haversine_m(spot["lat"], spot["lng"], s["lat"], s["lng"]) / 1000.0
    if dist_km > TIDE_STATION_MAX_DIST_KM:
        return {"nearest_tide_station_id": None, "nearest_tide_station_dist_km": None}
    return {
        "nearest_tide_station_id": s["id"],
        "nearest_tide_station_dist_km": round(dist_km, 2),
    }
