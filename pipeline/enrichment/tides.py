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


def resolve_tide_station_override(spot: dict, station_id: str,
                                  stations: list[dict] | None = None) -> dict:
    """The {id, dist_km} pair for a HAND-ASSIGNED station. Same shape as the algorithm's.

    THE DISTANCE IS COMPUTED, NOT DECLARED. It would be easier to let the override file carry
    the kilometres its author measured, and it would be wrong: db_import._validate_coord_derived
    recomputes the great-circle distance to the station's real coordinates and NULLs the ENTIRE
    pairing — id and distance both — when the stored value disagrees by more than
    COORD_DERIVED_DIST_TOLERANCE_KM. A hand-written distance that drifted from tide_stations.json
    (the file is a downloaded artifact, refreshed independently) would delete the override at
    import time, one stage after the override appeared to work. Computing it here means the
    stored distance is true by construction and that rule can never fire on an override.

    NO DISTANCE CAP. compute_nearest_tide_station drops anything past TIDE_STATION_MAX_DIST_KM;
    an override is by definition a deliberate departure from that rule, and capping it would make
    the channel fail for the one case it exists for — a station further away that actually works.
    The caller warns when the result exceeds the cap; it does not drop it.

    AN UNRESOLVABLE ID IS KEPT, NOT DISCARDED, with a null distance. Discarding would silently
    restore the algorithm's answer, which is the revert this whole channel exists to prevent;
    keeping it routes the spot into forecast.tides.unresolvable_station_ids, which names the id
    and every spot on it. The null distance is deliberate too — db_import's `_check` skips a
    record whose station it cannot find, so a null cannot trigger the NULLing rule.

    *stations* is the same injection seam compute_nearest_tide_station carries: the default path
    reads the committed station file, a supplied list is used verbatim.
    """
    all_stations = load_tide_stations() if stations is None else stations
    match = None
    for st in all_stations or []:
        if str(st.get("id")) == str(station_id):        # EXACT — TWC0965 is not TWC0965F
            match = st
            break
    if match is None:
        log.warning(
            "tide-station override for %r names %r, which is in no entry of the tide station "
            "list — keeping the id (so it is reported as unresolvable) with a null distance",
            spot.get("name"), station_id)
        return {"nearest_tide_station_id": str(station_id), "nearest_tide_station_dist_km": None}
    dist_km = haversine_m(spot["lat"], spot["lng"], match["lat"], match["lng"]) / 1000.0
    return {
        "nearest_tide_station_id": str(match["id"]),    # verbatim, from the station file
        "nearest_tide_station_dist_km": round(dist_km, 2),
    }
