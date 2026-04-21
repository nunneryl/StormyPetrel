"""Algorithm 2 — swell window via geodesic ray-casting.

Cast rays at SWELL_RAY_STEP_DEG increments outward from the spot. Each ray
starts SWELL_LOCAL_COAST_EXCLUSION_KM from the spot and extends
SWELL_MIN_FETCH_KM further. A bearing is "open" iff no land polygon
intersects that ray — i.e. there is at least SWELL_MIN_FETCH_KM of open
fetch for swell to develop. Contiguous open bearings are merged into arcs
and shrunk inward by SWELL_ARC_SHRINK_DEG on each end for diffraction.
"""
from __future__ import annotations

import logging

from pyproj import Geod
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

from ..config import (
    SWELL_ARC_SHRINK_DEG,
    SWELL_LOCAL_COAST_EXCLUSION_KM,
    SWELL_MIN_FETCH_KM,
    SWELL_RAY_STEP_DEG,
)
from .geodata import load_land_index

log = logging.getLogger(__name__)

_GEOD = Geod(ellps="WGS84")


def _ray_linestring(lat: float, lng: float, bearing_deg: float) -> LineString:
    """Densified geodesic LineString starting SWELL_LOCAL_COAST_EXCLUSION_KM
    from the spot and extending SWELL_MIN_FETCH_KM further along *bearing*.
    """
    skip_m = SWELL_LOCAL_COAST_EXCLUSION_KM * 1000.0
    fetch_m = SWELL_MIN_FETCH_KM * 1000.0
    total_m = skip_m + fetch_m
    start_lon, start_lat, _ = _GEOD.fwd(lng, lat, bearing_deg, skip_m)
    end_lon, end_lat, _ = _GEOD.fwd(lng, lat, bearing_deg, total_m)
    # ~50 km vertex spacing keeps each ray light while preserving great-circle shape.
    step_m = 50_000.0
    n = max(3, int(fetch_m // step_m))
    pts = [(start_lon, start_lat)]
    intermediate = _GEOD.npts(start_lon, start_lat, end_lon, end_lat, n - 1)
    pts.extend((lon, la) for lon, la in intermediate)
    pts.append((end_lon, end_lat))
    return LineString(pts)


def _bearing_analyze(ray: LineString, land, spot_ll: Point) -> tuple[bool, float | None]:
    """Return (is_open, first_land_hit_km_or_None).

    is_open iff no land polygon intersects the ray (within SWELL_MIN_FETCH_KM).
    first_land_hit_km is always the closest hit encountered, useful for debug
    logging — None when the ray is clear.
    """
    try:
        candidates = land.polygon_tree.query(ray)
    except Exception:
        return True, None
    best_km: float | None = None
    for c in candidates:
        poly = land.polygons[int(c)] if hasattr(c, "__index__") else c
        if not ray.intersects(poly):
            continue
        inter = ray.intersection(poly)
        if inter.is_empty:
            continue
        closest_on_inter, _ = nearest_points(inter, spot_ll)
        _, _, dist_m = _GEOD.inv(spot_ll.x, spot_ll.y, closest_on_inter.x, closest_on_inter.y)
        dist_km = dist_m / 1000.0
        if best_km is None or dist_km < best_km:
            best_km = dist_km
    return (best_km is None), best_km


def _merge_open_arcs(open_bearings: list[int], step_deg: int) -> list[dict]:
    """Collapse a sorted list of open bearings into [min, max, span] arcs.

    Handles wraparound: if 358 and 0 are both open, they belong to the same arc.
    """
    if not open_bearings:
        return []
    s = set(open_bearings)
    all_open = len(s) * step_deg >= 360
    if all_open:
        return [{"min": 0, "max": 358, "span": 360}]

    sorted_b = sorted(s)
    arcs: list[tuple[int, int]] = []
    start = prev = sorted_b[0]
    for b in sorted_b[1:]:
        if b - prev == step_deg:
            prev = b
        else:
            arcs.append((start, prev))
            start = prev = b
    arcs.append((start, prev))

    # Wraparound merge: if the first arc starts at 0 and the last ends at 360 - step,
    # splice them.
    if len(arcs) >= 2 and arcs[0][0] == 0 and arcs[-1][1] == 360 - step_deg:
        first_s, first_e = arcs[0]
        last_s, last_e = arcs[-1]
        # Represent the wrapped arc as going from last_s (e.g. 340) to first_e (e.g. 20) + 360
        arcs = arcs[1:-1] + [(last_s, first_e + 360)]

    out: list[dict] = []
    for lo, hi in arcs:
        span = hi - lo + step_deg
        shrink = SWELL_ARC_SHRINK_DEG
        if span <= 2 * shrink:
            continue  # too narrow to survive diffraction shrink
        lo_s = lo + shrink
        hi_s = hi - shrink
        span_s = hi_s - lo_s + step_deg
        out.append({"min": lo_s % 360, "max": hi_s % 360, "span": span_s})
    return out


def _widest_arc_center(arcs: list[dict]) -> int | None:
    if not arcs:
        return None
    widest = max(arcs, key=lambda a: a["span"])
    lo, hi = widest["min"], widest["max"]
    if hi < lo:  # wrapped
        center = (lo + (hi + 360 - lo) / 2) % 360
    else:
        center = (lo + hi) / 2
    return int(round(center)) % 360


def compute_swell_window(spot: dict) -> dict:
    """Return {swell_window_arcs, optimal_swell_dir, swell_window_confidence}."""
    land = load_land_index()
    if land is None:
        return {
            "swell_window_arcs": [],
            "optimal_swell_dir": None,
            "swell_window_confidence": 0.0,
        }

    lat = spot.get("_algo_lat", spot["lat"])
    lng = spot.get("_algo_lng", spot["lng"])
    debug_on = log.isEnabledFor(logging.DEBUG)
    if debug_on:
        log.debug(
            "swell_window: spot=%r algo=(%.4f, %.4f) fetch=%dkm local_exclusion=%dkm step=%d°",
            spot.get("name"), lat, lng, SWELL_MIN_FETCH_KM, SWELL_LOCAL_COAST_EXCLUSION_KM, SWELL_RAY_STEP_DEG,
        )
    spot_ll = Point(lng, lat)
    open_bearings: list[int] = []
    # Log every 10th ray (i.e. every 20° with step=2°) for diagnostics.
    log_every = 10 * SWELL_RAY_STEP_DEG
    for bearing in range(0, 360, SWELL_RAY_STEP_DEG):
        ray = _ray_linestring(lat, lng, float(bearing))
        is_open, first_km = _bearing_analyze(ray, land, spot_ll)
        if is_open:
            open_bearings.append(bearing)
        if debug_on and bearing % log_every == 0:
            first_str = "none" if first_km is None else f"{first_km:.0f}km"
            log.debug(
                "  ray %03d°: %s first_hit=%s",
                bearing, "CLEAR  " if is_open else "BLOCKED", first_str,
            )

    arcs = _merge_open_arcs(open_bearings, SWELL_RAY_STEP_DEG)
    optimal = _widest_arc_center(arcs)
    # Confidence: how much of the compass is open (span-weighted).
    total_open = sum(a["span"] for a in arcs)
    confidence = min(1.0, total_open / 360.0) if arcs else 0.0

    return {
        "swell_window_arcs": arcs,
        "optimal_swell_dir": optimal,
        "swell_window_confidence": confidence,
    }
