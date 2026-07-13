"""Algorithm 2 — swell window via geodesic ray-casting.

Cast rays at SWELL_RAY_STEP_DEG increments outward from the spot. Each ray
starts SWELL_LOCAL_COAST_EXCLUSION_KM from the spot and extends
SWELL_MIN_FETCH_KM further, recording every land polygon it crosses and how
far away the hit is.

A bearing is *hard-blocked* (swell genuinely can't reach it) iff a ray hits
either:

  * a landmass at or above SWELL_BLOCKER_AREA_KM2 *within* SWELL_MAINLAND_SOLID_KM
    — continents and big islands in the near-to-mid field are walls; or
  * ANY land within SWELL_LOCAL_LANDMASS_KM — the coast / headland the spot
    sits on. This stays a blocker even when the local landmass is itself a
    small island (e.g. Aquidneck Is. for a Newport RI spot), which is why the
    area filter alone can't own the "is this blocked" decision.

A large landmass hit BEYOND SWELL_MAINLAND_SOLID_KM is NOT a wall — a coast merely
grazed far downrange under the SWELL_MIN_FETCH_KM fetch diffracts around, so it is
demoted to a *partial* blocker and wraps open by distance the same way a distant
island does (below). Sub-threshold islands beyond the local landmass are *partial*
blockers. Their
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
from shapely.geometry import LineString, Point, box
from shapely.ops import nearest_points
from shapely.prepared import prep
from shapely.strtree import STRtree

from ..config import (
    SWELL_ARC_SHRINK_DEG,
    SWELL_BLOCKER_AREA_KM2,
    SWELL_DIFFRACTION_WRAP_DEG,
    SWELL_DIFFRACTION_WRAP_PER_100KM,
    SWELL_ISLAND_GAP_BRIDGE_DEG,
    SWELL_LOCAL_COAST_EXCLUSION_KM,
    SWELL_LOCAL_LANDMASS_KM,
    SWELL_MAINLAND_SOLID_KM,
    SWELL_MIN_FETCH_KM,
    SWELL_MIN_SHADOW_DEG,
    SWELL_RAY_STEP_DEG,
)
from .geodata import LandIndex, load_land_index

log = logging.getLogger(__name__)

_GEOD = Geod(ellps="WGS84")

# Geodesic polygon areas (km²) and prepared geometries are expensive and
# spot-independent, so cache them per polygon *identity*. The global land index
# is held for the process lifetime (lru_cached) and local sub-indexes reference
# the same polygon objects, so id(poly) is stable and shared across both.
_AREA_CACHE: dict[int, float] = {}
_PREPARED_CACHE: dict[int, object] = {}


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


def _poly_area_km2(poly) -> float:
    """Geodesic area of a land polygon in km², cached by polygon identity."""
    pid = id(poly)
    a = _AREA_CACHE.get(pid)
    if a is None:
        area_m2, _ = _GEOD.geometry_area_perimeter(poly)
        a = abs(area_m2) / 1e6
        _AREA_CACHE[pid] = a
    return a


def _poly_centroid(poly) -> tuple[float, float]:
    """(lat, lng) of a polygon's centroid — a stable fingerprint for a blocker,
    since GSHHG L1 polygons carry no id/name. Diagnostic use only (never on the
    production path)."""
    c = poly.centroid
    return round(c.y, 3), round(c.x, 3)


def _prepared(poly):
    """Prepared geometry for *poly*, cached. Prepared intersects() builds an
    internal segment index once, so the 90 per-spot ray tests against the
    huge continental polygon are far cheaper than a raw intersects()."""
    pid = id(poly)
    p = _PREPARED_CACHE.get(pid)
    if p is None:
        p = prep(poly)
        _PREPARED_CACHE[pid] = p
    return p


def _first_seaward_entry(ray: LineString, inter, origin: Point):
    """For a ray whose ORIGIN sits inside a land polygon (the spot's own coast),
    return the Point at which the ray next runs INTO that polygon — i.e. re-enters
    it after crossing open water (a coast across a bay/strait), or *origin* itself
    when the ray never exits the polygon within the fetch (the bearing faces into
    the landmass). Return None when the ray leaves the polygon and never returns:
    open water lies seaward, so the polygon does not block this bearing.

    Planar arc-length along the densified ray orders the crossings (it is monotone
    along a simple outbound ray); the blocking distance itself is measured
    geodesically by the caller from the returned Point.
    """
    intervals: list[tuple[float, float]] = []
    for g in getattr(inter, "geoms", [inter]):
        for part in getattr(g, "geoms", [g]):
            if getattr(part, "geom_type", "") != "LineString" or part.is_empty:
                continue  # a tangential point touch is not a crossing
            ts = [ray.project(Point(xy)) for xy in part.coords]
            intervals.append((min(ts), max(ts)))
    if not intervals:
        return None
    intervals.sort()
    reentries = [a for a, _ in intervals[1:]]   # entries after the own-coast segment
    if reentries:
        return ray.interpolate(min(reentries))
    if intervals[0][1] >= ray.length - 1e-9:    # never exits → faces into the landmass
        return origin
    return None                                 # exits to open water, never returns → open


def _ray_hits(ray: LineString, land, spot_ll: Point) -> list[tuple[object, float]]:
    """Return [(polygon, nearest_seaward_hit_km), …] for land the outbound ray runs
    INTO from open water.

    The ray starts SWELL_LOCAL_COAST_EXCLUSION_KM offshore, but for a spot sitting on
    (or just inland of) the GSHHG coastline that start can still be INSIDE the spot's
    own landmass. Counting that near-field own-coast as a hit hard-blocks even seaward
    bearings, since the ray exits to open ocean a few km out (the too-narrow-window
    bug). So a polygon the ray STARTS inside blocks the bearing only if the ray
    re-enters it after reaching open water (a coast across a bay/strait) or never
    exits within the fetch (faces into the landmass); a ray that exits to open water
    and never returns is open. Polygons the ray enters from open water (islands, the
    far continent) are hits at the true water→land crossing, as before.
    """
    try:
        candidates = land.polygon_tree.query(ray)
    except Exception:
        return []
    origin = Point(ray.coords[0])
    hits: list[tuple[object, float]] = []
    for c in candidates:
        poly = land.polygons[int(c)]
        prepared = _prepared(poly)
        if not prepared.intersects(ray):
            continue
        inter = ray.intersection(poly)
        if inter.is_empty:
            continue
        if prepared.contains(origin):
            # Ray starts inside this polygon = the spot's own landmass: skip the
            # own-coast near field; block only on a genuine seaward re-entry (or a
            # bearing that never escapes the landmass).
            blocker = _first_seaward_entry(ray, inter, origin)
            if blocker is None:
                continue  # exits to open water → open along this bearing
            closest_on_inter = blocker
        else:
            # Spot is seaward of this polygon: the nearest intersection point is the
            # real water→land coastline crossing.
            closest_on_inter, _ = nearest_points(inter, spot_ll)
        _, _, dist_m = _GEOD.inv(spot_ll.x, spot_ll.y, closest_on_inter.x, closest_on_inter.y)
        hits.append((poly, dist_m / 1000.0))
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


def _island_shadow(small_hit_dist: dict[int, float], step_deg: int, debug=None) -> set[int]:
    """Bearings that stay blocked after small-island chains are wrap-trimmed.

    Each chain member's diffraction wrap-in is governed by ITS OWN obstacle distance,
    not one chain-wide nearest distance: a member stays blocked only if it lies deeper
    than wrap(its distance) inside BOTH open edges of the run. So a near member persists
    in the interior while a distant (near-transparent) member wraps open on its own large
    wrap even mid-run — a chain that fuses a near headland with distant mainland no longer
    keeps the far core wrongly blocked. This reduces EXACTLY to the old single-wrap core
    when a run's members are all at one distance.

    *debug* (diagnostic only; None on the production/full path, so behaviour and cost are
    unchanged): a list appended with one record per merged chain —
    {"lo","hi","width","dmin_km","wrap_deg","decision","reason","core"} — so the validate
    harness's --debug-blockers dump can show WHY each chain opened or kept a core.
    """
    blocked: set[int] = set()
    for lo, hi in _merge_runs(sorted(small_hit_dist), step_deg, SWELL_ISLAND_GAP_BRIDGE_DEG):
        width = hi - lo + step_deg
        if width < SWELL_MIN_SHADOW_DEG:
            if debug is not None:
                debug.append({"lo": lo % 360, "hi": hi % 360, "width": width,
                              "decision": "open_subtend",
                              "reason": f"width {width}° < {SWELL_MIN_SHADOW_DEG:.0f}° subtend cutoff"})
            continue  # subtends < ~5°: swell wraps clean around it
        # Per-member wrap: a member at (bearing b, distance d) stays blocked only if it
        # sits ≥ wrap(d) inside BOTH open edges of the run. wrap grows with the member's
        # OWN distance, so a far member (huge wrap) wraps open even mid-run while a near
        # member persists. Uniform-distance runs give the same core as the old single wrap.
        core_lo = core_hi = None
        n_core = 0
        for b in range(lo, hi + 1, step_deg):
            d = small_hit_dist.get(b % 360)
            if d is None:
                continue
            wrap_b = SWELL_DIFFRACTION_WRAP_DEG + SWELL_DIFFRACTION_WRAP_PER_100KM * (d / 100.0)
            if (b - lo) >= wrap_b and (hi - b) >= wrap_b:
                blocked.add(b % 360)
                core_lo = b if core_lo is None else core_lo
                core_hi = b
                n_core += 1
        if debug is not None:
            dmin = min(small_hit_dist[b % 360] for b in range(lo, hi + 1, step_deg)
                       if (b % 360) in small_hit_dist)
            wnear = SWELL_DIFFRACTION_WRAP_DEG + SWELL_DIFFRACTION_WRAP_PER_100KM * (dmin / 100.0)
            if core_lo is None:
                debug.append({"lo": lo % 360, "hi": hi % 360, "width": width,
                              "dmin_km": dmin, "wrap_deg": wnear, "decision": "open_wrapped",
                              "reason": f"per-member wrap: every member wraps open on its own "
                                        f"distance (nearest {dmin:.0f}km → {wnear:.0f}°)"})
            else:
                debug.append({"lo": lo % 360, "hi": hi % 360, "width": width,
                              "dmin_km": dmin, "wrap_deg": wnear, "decision": "blocked_core",
                              "core": [core_lo % 360, core_hi % 360],
                              "reason": f"per-member wrap: {n_core} near-member ray(s) "
                                        f"{core_lo % 360}–{core_hi % 360}° stay blocked (nearest "
                                        f"{dmin:.0f}km → {wnear:.0f}°); far members wrap open on own distance"})
    return blocked


def local_land_index(global_land: LandIndex, lat: float, lng: float) -> LandIndex:
    """Subset *global_land* to polygons within one fetch radius of the spot.

    Perf optimisation for the full roster: each ray then queries a tiny local
    STRtree instead of the global one. The subset references the SAME polygon
    objects, so the area / prepared-geometry caches stay shared. Falls back to
    the global index near the antimeridian (where a lon/lat box would split).
    """
    if global_land is None or not global_land.polygons:
        return global_land
    reach_deg = (SWELL_MIN_FETCH_KM + SWELL_LOCAL_COAST_EXCLUSION_KM) / 111.0 + 1.0
    dlng = reach_deg / max(0.15, math.cos(math.radians(lat)))
    if lng - dlng < -180.0 or lng + dlng > 180.0:
        return global_land  # antimeridian: keep the global index (correct, just slower)
    window = box(lng - dlng, lat - reach_deg, lng + dlng, lat + reach_deg)
    try:
        idxs = global_land.polygon_tree.query(window)
    except Exception:
        return global_land
    polys = [global_land.polygons[int(c)] for c in idxs
             if global_land.polygons[int(c)].intersects(window)]
    return LandIndex(
        polygons=polys,
        polygon_tree=STRtree(polys),
        coastlines=[],
        coastline_tree=None,
    )


def _classify_bearings(lat: float, lng: float, land: LandIndex, step_deg: int, debug=None,
                       mainland_solid_km=None):
    """Pass 1 — for every bearing return (hard_blocked set, small_hit_dist map).

    A bearing is hard-blocked by a landmass ≥ SWELL_BLOCKER_AREA_KM2 or any land
    within SWELL_LOCAL_LANDMASS_KM; otherwise the nearest small-island hit (if
    any) is recorded for the partial-shadow pass. Shared by compute_swell_window
    and the validate harness (which also reads `hard_blocked` for the ceiling).

    *debug* (diagnostic only; None on the production/full path, so behaviour and
    cost are unchanged): a dict filled per bearing with the block decision —
    {bearing: {"result": "hard"|"small"|"open", ["rule","area_km2","dist_km",
    "centroid","own"]}} — for the validate harness's --debug-blockers culprit dump.
    ``own`` (hard blocks) reuses the own-coast test: True when the ray origin sits
    inside the blocking polygon (the landmass the spot is on), letting the harness
    separate own-coast near field from distant-mainland min-fetch clipping.

    *mainland_solid_km* overrides SWELL_MAINLAND_SOLID_KM (the distance within which a
    large landmass hard-blocks) for this call only — used by the validate harness's
    --sweep-mainland-solid sensitivity sweep; None keeps the committed default.
    """
    spot_ll = Point(lng, lat)
    solid_km = SWELL_MAINLAND_SOLID_KM if mainland_solid_km is None else mainland_solid_km
    hard_blocked: set[int] = set()
    small_hit_dist: dict[int, float] = {}
    for bearing in range(0, 360, step_deg):
        ray = _ray_linestring(lat, lng, float(bearing))
        is_hard = False
        nearest_small: float | None = None
        nearest_small_poly = None
        for poly, dist_km in _ray_hits(ray, land, spot_ll):
            # A large landmass is SOLID only in the near/mid field; BEYOND
            # SWELL_MAINLAND_SOLID_KM it falls through to the small-shadow pass, so the
            # distance-aware diffraction wrap graduates it (a coast grazed far downrange
            # under SWELL_MIN_FETCH_KM diffracts around instead of walling off the bearing,
            # while a near coast / bay-strait crossing still hard-blocks).
            if dist_km <= SWELL_LOCAL_LANDMASS_KM or (
                    _poly_area_km2(poly) >= SWELL_BLOCKER_AREA_KM2
                    and dist_km <= solid_km):
                is_hard = True
                if debug is not None:
                    rule = ("local_coast_30km" if dist_km <= SWELL_LOCAL_LANDMASS_KM
                            else "mainland_solid")
                    debug[bearing] = {"result": "hard", "rule": rule,
                                      "area_km2": _poly_area_km2(poly), "dist_km": dist_km,
                                      "centroid": _poly_centroid(poly),
                                      "own": _prepared(poly).contains(Point(ray.coords[0]))}
                break
            if nearest_small is None or dist_km < nearest_small:
                nearest_small = dist_km
                nearest_small_poly = poly
        if is_hard:
            hard_blocked.add(bearing)
        elif nearest_small is not None:
            small_hit_dist[bearing] = nearest_small
            if debug is not None:
                debug[bearing] = {"result": "small", "dist_km": nearest_small,
                                  "area_km2": _poly_area_km2(nearest_small_poly),
                                  "centroid": _poly_centroid(nearest_small_poly)}
        elif debug is not None:
            debug[bearing] = {"result": "open"}
    return hard_blocked, small_hit_dist


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


def compute_swell_window(spot: dict, land: LandIndex | None = None,
                         ray_step: int | None = None) -> dict:
    """Return {swell_window_arcs, optimal_swell_dir, swell_window_confidence,
    [swell_window_source]}.

    *land* may be an injected (e.g. spot-local, pre-clipped) index; when None the
    global GSHHG index is loaded. *ray_step* overrides the angular step in
    degrees (the production roster run uses 4° / 90 rays for speed; defaults to
    SWELL_RAY_STEP_DEG). swell_window_source = "raycast" is set only when the
    cast genuinely succeeds (≥1 open arc). When every bearing is blocked the arcs
    come back empty with no source, so the caller's orientation-derived fallback
    owns the result (sheltered bays, Great Lakes, fully-enclosed water).
    """
    if land is None:
        land = load_land_index()
    if land is None:
        return {
            "swell_window_arcs": [],
            "optimal_swell_dir": None,
            "swell_window_confidence": 0.0,
        }

    lat = spot.get("_algo_lat", spot["lat"])
    lng = spot.get("_algo_lng", spot["lng"])
    step = ray_step if ray_step is not None else SWELL_RAY_STEP_DEG
    if log.isEnabledFor(logging.DEBUG):
        log.debug(
            "swell_window: spot=%r algo=(%.4f, %.4f) fetch=%dkm local_exclusion=%dkm step=%d°",
            spot.get("name"), lat, lng, SWELL_MIN_FETCH_KM, SWELL_LOCAL_COAST_EXCLUSION_KM, step,
        )

    # Pass 1 — hard-block vs small-island shadow vs open.
    hard_blocked, small_hit_dist = _classify_bearings(lat, lng, land, step)
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
