"""Algorithm 2 — swell window via geodesic ray-casting.

Cast rays at SWELL_RAY_STEP_DEG increments outward from the spot. Each ray
starts SWELL_LOCAL_COAST_EXCLUSION_KM from the spot and extends
SWELL_MIN_FETCH_KM further, recording every land polygon it crosses and how
far away the hit is.

A bearing is *hard-blocked* (swell genuinely can't reach it) iff a ray hits
either:

  * a landmass at or above SWELL_BLOCKER_AREA_KM2 — continents and big
    islands are walls; or
  * ANY land within SWELL_LOCAL_LANDMASS_KM — the coast / headland the spot
    sits on. This stays a blocker even when the local landmass is itself a
    small island (e.g. Aquidneck Is. for a Newport RI spot), which is why the
    area filter alone can't own the "is this blocked" decision.

Sub-threshold islands beyond the local landmass are *partial* blockers. Their
per-bearing shadows are unioned, adjacent shadows separated by less than
SWELL_ISLAND_GAP_BRIDGE_DEG are merged into a "chain", and each chain is
trimmed inward on both edges by a distance-aware diffraction wrap-in
(SWELL_DIFFRACTION_WRAP_DEG + SWELL_DIFFRACTION_WRAP_PER_100KM·distance). A
lone small island has open water on both edges and wraps away to nothing
(Catalina stops walling off Huntington); a long island chain keeps its
interior blocked (the Channel Islands keep Rincon's window narrow). Anything
subtending less than SWELL_MIN_SHADOW_DEG is ignored outright.

Surviving open bearings are merged into arcs and shrunk inward by
SWELL_ARC_SHRINK_DEG on each end for diffraction. optimal_swell_dir is the
angle-weighted centre (circular mean) of the open window — a geometric proxy
for the refraction optimum, better than the bare shoreline normal for
asymmetric points but NOT true spectral refraction (a later upgrade).
"""
from __future__ import annotations

import logging
import math

from pyproj import Geod
from shapely.geometry import LineString, Point
from shapely.ops import nearest_points

from ..config import (
    SWELL_ARC_SHRINK_DEG,
    SWELL_BLOCKER_AREA_KM2,
    SWELL_DIFFRACTION_WRAP_DEG,
    SWELL_DIFFRACTION_WRAP_PER_100KM,
    SWELL_ISLAND_GAP_BRIDGE_DEG,
    SWELL_LOCAL_COAST_EXCLUSION_KM,
    SWELL_LOCAL_LANDMASS_KM,
    SWELL_MIN_FETCH_KM,
    SWELL_MIN_SHADOW_DEG,
    SWELL_RAY_STEP_DEG,
)
from .geodata import load_land_index

log = logging.getLogger(__name__)

_GEOD = Geod(ellps="WGS84")

# Geodesic polygon areas (km²) are expensive and spot-independent, so cache
# them per (land-index identity, polygon index). load_land_index is itself
# lru_cached, so id(land) is stable for the life of the process.
_AREA_CACHE: dict[tuple[int, int], float] = {}


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


def _poly_area_km2(land, idx: int) -> float:
    """Geodesic area of land polygon *idx* in km², cached."""
    key = (id(land), idx)
    a = _AREA_CACHE.get(key)
    if a is None:
        area_m2, _ = _GEOD.geometry_area_perimeter(land.polygons[idx])
        a = abs(area_m2) / 1e6
        _AREA_CACHE[key] = a
    return a


def _ray_hits(ray: LineString, land, spot_ll: Point) -> list[tuple[int, float]]:
    """Return [(polygon_index, nearest_hit_km), …] for land the ray crosses."""
    try:
        candidates = land.polygon_tree.query(ray)
    except Exception:
        return []
    hits: list[tuple[int, float]] = []
    for c in candidates:
        idx = int(c)
        poly = land.polygons[idx]
        if not ray.intersects(poly):
            continue
        inter = ray.intersection(poly)
        if inter.is_empty:
            continue
        closest_on_inter, _ = nearest_points(inter, spot_ll)
        _, _, dist_m = _GEOD.inv(spot_ll.x, spot_ll.y, closest_on_inter.x, closest_on_inter.y)
        hits.append((idx, dist_m / 1000.0))
    return hits


def _merge_runs(blocked: list[int], step_deg: int, gap_bridge_deg: float) -> list[tuple[int, int]]:
    """Collapse blocked bearings into [lo, hi] intervals (hi may exceed 360 for
    a wrapped run), merging any gap narrower than *gap_bridge_deg* so adjacent
    island shadows become one chain. Bearings inside a bridged gap are part of
    the interval (a narrow slot between islands stays closed).
    """
    if not blocked:
        return []
    bs = sorted(set(blocked))
    merge_within = step_deg + gap_bridge_deg
    intervals: list[tuple[int, int]] = []
    start = prev = bs[0]
    for b in bs[1:]:
        if b - prev <= merge_within:
            prev = b
        else:
            intervals.append((start, prev))
            start = prev = b
    intervals.append((start, prev))
    # Wraparound: bridge the last interval into the first across 0/360.
    if len(intervals) >= 2:
        first_lo, first_hi = intervals[0]
        last_lo, last_hi = intervals[-1]
        if (first_lo + 360) - last_hi <= merge_within:
            intervals = [(last_lo, first_hi + 360)] + intervals[1:-1]
    return intervals


def _island_shadow(small_hit_dist: dict[int, float], step_deg: int) -> set[int]:
    """Bearings that stay blocked after small-island chains are wrap-trimmed."""
    blocked: set[int] = set()
    for lo, hi in _merge_runs(sorted(small_hit_dist), step_deg, SWELL_ISLAND_GAP_BRIDGE_DEG):
        width = hi - lo + step_deg
        if width < SWELL_MIN_SHADOW_DEG:
            continue  # subtends < ~5°: swell wraps clean around it
        dmin = min(
            small_hit_dist[b % 360]
            for b in range(lo, hi + 1, step_deg)
            if (b % 360) in small_hit_dist
        )
        wrap = SWELL_DIFFRACTION_WRAP_DEG + SWELL_DIFFRACTION_WRAP_PER_100KM * (dmin / 100.0)
        if 2 * wrap >= width:
            continue  # fully wrapped — the whole shadow fills back in
        core_lo, core_hi = lo + wrap, hi - wrap
        b = math.ceil(core_lo / step_deg) * step_deg
        while b <= core_hi:
            blocked.add(b % 360)
            b += step_deg
    return blocked


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


def _open_window_center(open_bearings: list[int]) -> int | None:
    """Angle-weighted centre (circular mean) of the open window, snapped to an
    actually-open bearing.

    A geometric proxy for the refraction optimum: for an asymmetric point it
    pulls off the shoreline normal toward open water, but it is NOT true
    spectral refraction (that's a later upgrade). The circular mean of a
    two-lobed window can land in the blocked gap between the lobes, so we snap
    the result to the nearest open bearing — the reported optimal is always a
    direction the window is actually open to.
    """
    if not open_bearings:
        return None
    x = sum(math.cos(math.radians(b)) for b in open_bearings)
    y = sum(math.sin(math.radians(b)) for b in open_bearings)
    if abs(x) < 1e-9 and abs(y) < 1e-9:
        return None
    mean = math.degrees(math.atan2(y, x)) % 360.0
    # Snap to the nearest open bearing (circular distance).
    return min(open_bearings, key=lambda b: abs(((b - mean + 180.0) % 360.0) - 180.0)) % 360


def compute_swell_window(spot: dict) -> dict:
    """Return {swell_window_arcs, optimal_swell_dir, swell_window_confidence,
    [swell_window_source]}.

    swell_window_source = "raycast" is set only when the cast genuinely
    succeeds (≥1 open arc). When every bearing is blocked the arcs come back
    empty with no source, so the caller's orientation-derived fallback owns
    the result (sheltered bays, Great Lakes, fully-enclosed water).
    """
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
    step = SWELL_RAY_STEP_DEG

    # Pass 1 — classify each bearing as hard-blocked, partially shadowed by a
    # small island, or open.
    hard_blocked: set[int] = set()
    small_hit_dist: dict[int, float] = {}  # bearing -> nearest small-island hit (km)
    log_every = 10 * step
    for bearing in range(0, 360, step):
        ray = _ray_linestring(lat, lng, float(bearing))
        hits = _ray_hits(ray, land, spot_ll)
        is_hard = False
        nearest_small: float | None = None
        for idx, dist_km in hits:
            if dist_km <= SWELL_LOCAL_LANDMASS_KM or _poly_area_km2(land, idx) >= SWELL_BLOCKER_AREA_KM2:
                is_hard = True
                break
            if nearest_small is None or dist_km < nearest_small:
                nearest_small = dist_km
        if is_hard:
            hard_blocked.add(bearing)
        elif nearest_small is not None:
            small_hit_dist[bearing] = nearest_small
        if debug_on and bearing % log_every == 0:
            state = "HARD" if is_hard else ("ISLAND" if nearest_small is not None else "CLEAR")
            log.debug("  ray %03d°: %-6s nearest_small=%s", bearing, state,
                      "none" if nearest_small is None else f"{nearest_small:.0f}km")

    # Pass 2 — small-island chains, wrap-trimmed.
    small_blocked = _island_shadow(small_hit_dist, step)

    open_bearings = [b for b in range(0, 360, step) if b not in hard_blocked and b not in small_blocked]

    arcs = _merge_open_arcs(open_bearings, step)
    optimal = _open_window_center(open_bearings)
    # Confidence: how much of the compass is open (span-weighted).
    total_open = sum(a["span"] for a in arcs)
    confidence = min(1.0, total_open / 360.0) if arcs else 0.0

    result: dict = {
        "swell_window_arcs": arcs,
        "optimal_swell_dir": optimal,
        "swell_window_confidence": confidence,
    }
    if arcs:
        # The raycast only claims a result when it genuinely opened a window.
        result["swell_window_source"] = "raycast"
    return result
