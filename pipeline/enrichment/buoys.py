"""Algorithm 4 — nearest NDBC wave buoy with line-of-sight filter + regional caps."""
from __future__ import annotations

import json
import logging
import math
import re
from functools import lru_cache

from pyproj import Geod
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

from ..config import (
    BUOY_CAP_DEFAULT,
    BUOY_CAP_DEFAULT_EAST,
    BUOY_CAP_GULF_FLORIDA,
    BUOY_CAP_KM,
    BUOY_LOS_STANDOFF_M,
    BUOY_OVERRIDES_FILE,
)
from ..geo import haversine_m
from .geodata import load_land_index, load_ndbc_wave_stations

log = logging.getLogger(__name__)

_GEOD = Geod(ellps="WGS84")
_GREAT_LAKES_STATES = frozenset({
    "Michigan", "Wisconsin", "Minnesota", "Illinois", "Indiana", "Ohio",
    "Pennsylvania", "New York",
})
# New York has two coasts. Its Great Lakes shore runs from Lake Erie at Barcelona (42.3 N) to
# Lake Ontario (43.2-43.5 N); its Atlantic coast, the city beaches, Long Island and Montauk, lies
# between 40.5 and 41.3 N. Only a New York spot at or north of this latitude takes the lake cap.
# Before 2026-11-02 every New York spot north of 40 N did, so Mecox Beach's nearest buoy
# (44097, 100.89 km) fell outside a 100 km cap meant for the lakes (sw1-raycast run 64).
_NY_GREAT_LAKES_MIN_LAT = 42.0


def _regional_cap_km(spot: dict) -> float:
    region = (spot.get("region_hint") or "").strip()
    # Great Lakes override: the eight bordering states surf the lakes.
    if region in _GREAT_LAKES_STATES and spot.get("lat", 0) > 40 and spot.get("lng", 0) < -70:
        if not (region == "New York" and spot.get("lat", 0) < _NY_GREAT_LAKES_MIN_LAT):
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


def _on_land(lat: float, lng: float, land) -> bool:
    pt = Point(lng, lat)
    for c in land.polygon_tree.query(pt):
        poly = land.polygons[int(c)] if hasattr(c, "__index__") else c
        if poly.contains(pt):
            return True
    return False


_LOS_START: dict = {}


def _los_start(lat: float, lng: float, land, standoff_m: float = BUOY_LOS_STANDOFF_M):
    """(lat, lng) the line of sight is drawn from: *standoff_m* beyond the rule's point, away from
    the nearest coastline (config.BUOY_LOS_STANDOFF_M says why). A point on land is pushed out
    through the coast. When the pushed point is on land (another island, the far side of a narrow
    bay) or no coastline direction can be taken, the rule's point itself. Distances are not
    affected: they are still measured from the rule's point."""
    key = (id(land), lat, lng, standoff_m)
    if key in _LOS_START:
        return _LOS_START[key]
    out = (lat, lng)
    pt = Point(lng, lat)
    try:
        idx = land.coastline_tree.query_nearest(pt)
        coast = land.coastlines[int(idx[0])] if len(idx) else None
    except Exception:
        coast = None
    if coast is not None:
        q = nearest_points(coast, pt)[0]
        if (q.x, q.y) != (lng, lat):
            az = _GEOD.inv(q.x, q.y, lng, lat)[0]
            if _on_land(lat, lng, land):
                az += 180.0
            lng2, lat2, _ = _GEOD.fwd(lng, lat, az, standoff_m)
            if not _on_land(lat2, lng2, land):
                out = (lat2, lng2)
    _LOS_START[key] = out
    return out


def _los_clear(spot: dict, station: dict, land) -> bool:
    lat = spot.get("_algo_lat", spot["lat"])
    lng = spot.get("_algo_lng", spot["lng"])
    lat, lng = _los_start(lat, lng, land, BUOY_LOS_STANDOFF_M)
    line = _geodesic_line(lat, lng, station["lat"], station["lng"])
    try:
        candidates = land.polygon_tree.query(line)
    except Exception:
        return True
    for c in candidates:
        poly = land.polygons[int(c)] if hasattr(c, "__index__") else c
        if line.intersects(poly):
            return False
    return True


def _slug(name) -> str:
    """A spot name as db_import slugs it."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


@lru_cache(maxsize=1)
def load_buoy_overrides() -> dict:
    """{slug: override} from config.BUOY_OVERRIDES_FILE (the module global, read at call time),
    or {} when the file is absent."""
    path = BUOY_OVERRIDES_FILE
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("overrides") or {}


def buoy_override(spot: dict) -> dict:
    """The spot's written exception, {} for none: {"pin": id} keeps that buoy whatever the rule
    finds; {"exclude": [ids]} keeps those stations out of its buoy and fallbacks."""
    return load_buoy_overrides().get(_slug(spot.get("name"))) or {}


def _confidence(dist_km: float, cap_km: float) -> float:
    return 1.0 if dist_km <= cap_km * 0.5 else 0.75


def pinned(override: dict, stations, algo_lat: float, algo_lng: float, cap_km: float, passing):
    """compute_nearest_buoy's result for a spot whose override pins a buoy, or None when there is
    no pin or the pinned station is not on the wave-station list (a pin never holds a silent
    buoy; the rule then runs as usual). The distance is from the rule's point, as for any buoy;
    the fallbacks are the rule's passing buoys other than the pin, up to three."""
    pid = override.get("pin")
    if not pid:
        return None
    st = next((s for s in stations if str(s["id"]).lower() == str(pid).lower()), None)
    if st is None:
        log.warning("buoy override pins %s, which is not a wave station on the list: ignored", pid)
        return None
    dist_km = haversine_m(algo_lat, algo_lng, st["lat"], st["lng"]) / 1000.0
    return {
        "nearest_buoy_id": st["id"],
        "nearest_buoy_dist_km": round(dist_km, 2),
        "fallback_buoy_ids": [s for s, _ in passing if str(s).lower() != str(pid).lower()][:3],
        "buoy_confidence": _confidence(dist_km, cap_km),
    }


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

    algo_lat = spot.get("_algo_lat", spot["lat"])
    algo_lng = spot.get("_algo_lng", spot["lng"])
    cap_km = _regional_cap_km(spot)
    xyz = _latlng_to_unit_xyz(algo_lat, algo_lng)
    k = min(k_candidates, len(stations))
    _, idx = tree.query(xyz, k=k)
    if isinstance(idx, int):
        idx = [idx]
    else:
        idx = list(idx)

    override = buoy_override(spot)
    excluded = {str(x).lower() for x in override.get("exclude") or []}
    passing: list[tuple[str, float]] = []
    for i in idx:
        s = stations[i]
        if str(s["id"]).lower() in excluded:
            continue
        dist_m = haversine_m(algo_lat, algo_lng, s["lat"], s["lng"])
        dist_km = dist_m / 1000.0
        if dist_km > cap_km:
            continue
        if not _los_clear(spot, s, land):
            continue
        passing.append((s["id"], dist_km))
        if len(passing) >= 4:
            break

    pin = pinned(override, stations, algo_lat, algo_lng, cap_km, passing)
    if pin is not None:
        return pin
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
