"""Algorithm 2 — swell window via geodesic ray-casting.

Cast rays at SWELL_RAY_STEP_DEG increments outward from the spot. Each ray
starts SWELL_LOCAL_COAST_EXCLUSION_KM from the spot and extends
SWELL_MIN_FETCH_KM further, recording every land polygon it crosses and how
far away the hit is.

A bearing is *hard-blocked* (swell genuinely can't reach it) iff a ray hits
either:

  * a landmass at or above SWELL_BLOCKER_AREA_KM2 *within* SWELL_MAINLAND_SOLID_KM
    — continents and big islands in the near-to-mid field are walls; or
  * land within SWELL_LOCAL_LANDMASS_KM that is a wall by R1 or R2:
      R1 — the spot's OWN coast (the polygon's nearest point within SWELL_OWN_COAST_KM
           of the spot) or a landmass of SWELL_BLOCKER_AREA_KM2 or more. The own coast
           stays a blocker even when it is itself a small island (e.g. Aquidneck Is. for
           a Newport RI spot), which is why the area filter alone can't own the decision.
      R2 — any other island there by its Fresnel number at a SWELL_FRESNEL_PERIOD_S swell,
           N = w² / (L·r) (w its width across the ray, r the hit distance, L the
           deep-water wavelength): N ≥ SWELL_FRESNEL_WALL_N is a wall; below
           SWELL_FRESNEL_TRANSPARENT_N the ray passes it and goes on to whatever lies
           behind; in between it is a partial blocker.

A large landmass hit BEYOND SWELL_MAINLAND_SOLID_KM is NOT a wall — a coast merely
grazed far downrange under the SWELL_MIN_FETCH_KM fetch diffracts around, so it is
demoted to a *partial* blocker and wraps open by distance the same way a distant
island does (below). Sub-threshold islands beyond the local landmass are *partial*
blockers. R3 — every partial hit on a ray is kept, not just the nearest, and the hits
are grouped by island: each island's own run of rays is trimmed inward on both edges by
a distance-aware diffraction wrap-in (SWELL_DIFFRACTION_WRAP_DEG +
SWELL_DIFFRACTION_WRAP_PER_100KM·distance, each ray on that island's own distance). A
lone small island has open water on both edges and wraps away to nothing; a long island
keeps its interior blocked. An island under SWELL_FRESNEL_ISLAND_MAX_KM2 that is broad
for its distance (Fresnel N ≥ SWELL_FRESNEL_WALL_N on a ray) is trimmed instead by only
its penumbra at the design period, √(L/r) — the Fresnel edge — so Santa Cruz Island keeps
a core at Pitas Point, Kaua'i at Pipeline and Catalina at Huntington; Cuba, Hispaniola
and the continents keep the wrap. Islands are never fused: a near islet is not joined to
a far coast behind it, nor to a neighbour beside it. Anything subtending less than
SWELL_MIN_SHADOW_DEG is ignored outright.

Rays keep continuous longitudes and are mapped back across the antimeridian
(_ray_pieces), so a Hawaii spot's westward ray meets what lies past 180° and nothing else.

Surviving open bearings are merged into arcs and shrunk inward by
SWELL_ARC_SHRINK_DEG on each end for diffraction. optimal_swell_dir is the
angle-weighted centre (circular mean) of the open window — a geometric proxy
for the refraction optimum, better than the bare shoreline normal for
asymmetric points but NOT true spectral refraction (a later upgrade).
"""
from __future__ import annotations

import logging
import math
from typing import NamedTuple

from pyproj import Geod
from shapely.geometry import LineString, Point, box
from shapely.affinity import translate
from shapely.ops import nearest_points
from shapely.prepared import prep
from shapely.strtree import STRtree

from ..config import (
    SWELL_ARC_SHRINK_DEG,
    SWELL_BLOCKER_AREA_KM2,
    SWELL_DIFFRACTION_WRAP_DEG,
    SWELL_DIFFRACTION_WRAP_PER_100KM,
    SWELL_FRESNEL_GRAVITY,
    SWELL_FRESNEL_ISLAND_MAX_KM2,
    SWELL_FRESNEL_PERIOD_S,
    SWELL_FRESNEL_TRANSPARENT_N,
    SWELL_FRESNEL_WALL_N,
    SWELL_ISLAND_GAP_BRIDGE_DEG,
    SWELL_LOCAL_COAST_EXCLUSION_KM,
    SWELL_LOCAL_LANDMASS_KM,
    SWELL_MAINLAND_SOLID_KM,
    SWELL_MIN_FETCH_KM,
    SWELL_MIN_SHADOW_DEG,
    SWELL_OWN_COAST_KM,
    SWELL_OWN_LAND_CROSSING_KM,
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
# How far inside a polygon a spot sits, keyed (id(poly), lng, lat): every ray of one spot
# asks the same question of the same landmass, and the answer needs a nearest-point search
# along the whole coastline.
_DEPTH_CACHE: dict[tuple[int, float, float], float] = {}
# How far the polygon's nearest point is from the spot (R1's own-coast test), and the
# polygon's vertices as (azimuth, distance) from the spot (R2's width across a ray): both
# asked once per spot and polygon, keyed like _DEPTH_CACHE.
_NEAREST_CACHE: dict[tuple[int, float, float], float] = {}
_POLAR_CACHE: dict[tuple[int, float, float], tuple] = {}


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
    # pyproj returns longitudes in [-180, 180], so a ray crossing the antimeridian would jump
    # from +179.9 to -179.9 and its planar chord would span the whole map: every Hawaii spot's
    # westward rays then "hit" the Americas, Eurasia and Africa thousands of km beyond the
    # fetch. Keep the longitudes continuous instead (they may run past ±180);
    # _ray_pieces maps the part beyond back onto the land.
    unwrapped = [pts[0]]
    for lon, la in pts[1:]:
        prev = unwrapped[-1][0]
        lon += 360.0 * round((prev - lon) / 360.0)
        unwrapped.append((lon, la))
    return LineString(unwrapped)


def _ray_pieces(ray: LineString, spot_ll: Point) -> list[tuple[LineString, Point]]:
    """The ray as the land sees it: [(ray, spot)] for a ray within ±180° longitude; for one
    that runs past the antimeridian, also the whole ray and spot shifted by ∓360°, so the
    part beyond lands on the polygons there. Each piece is paired with the spot shifted the
    same way, which keeps planar nearest-point searches on the near side of the line."""
    pieces = [(ray, spot_ll)]
    minx, _, maxx, _ = ray.bounds
    for dx, beyond in ((-360.0, maxx > 180.0), (360.0, minx < -180.0)):
        if beyond:
            pieces.append((translate(ray, xoff=dx), Point(spot_ll.x + dx, spot_ll.y)))
    return pieces


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


def _own_intervals(ray: LineString, inter) -> list[tuple[float, float]]:
    """The ray's crossings of one polygon as sorted (start, end) planar arc-lengths."""
    intervals: list[tuple[float, float]] = []
    for g in getattr(inter, "geoms", [inter]):
        for part in getattr(g, "geoms", [g]):
            if getattr(part, "geom_type", "") != "LineString" or part.is_empty:
                continue  # a tangential point touch is not a crossing
            ts = [ray.project(Point(xy)) for xy in part.coords]
            intervals.append((min(ts), max(ts)))
    return sorted(intervals)


def _first_seaward_entry(ray: LineString, inter, origin: Point):
    """For a ray whose ORIGIN sits inside a land polygon (the spot's own coast),
    return the Point at which the ray next runs INTO that polygon — i.e. re-enters
    it after crossing open water (a coast across a bay/strait), or *origin* itself
    when the ray never exits the polygon within the fetch (the bearing faces into
    the landmass). Return None when the ray leaves the polygon and never returns:
    open water lies beyond. _ray_hits calls this only for a ray that crossed no more
    land than the own-coast allowance (see _own_exit_point); a longer crossing is
    blocked before re-entry is considered.

    Planar arc-length along the densified ray orders the crossings (it is monotone
    along a simple outbound ray); the blocking distance itself is measured
    geodesically by the caller from the returned Point.
    """
    intervals = _own_intervals(ray, inter)
    if not intervals:
        return None
    reentries = [a for a, _ in intervals[1:]]   # entries after the own-coast segment
    if reentries:
        return ray.interpolate(min(reentries))
    if intervals[0][1] >= ray.length - 1e-9:    # never exits → faces into the landmass
        return origin
    return None                                 # exits to open water, never returns


def _own_exit_point(ray: LineString, inter) -> Point | None:
    """Where the ray first leaves the polygon its origin sits inside (the ray's end when it
    never leaves within the fetch); None when the intersection has no crossing segment."""
    intervals = _own_intervals(ray, inter)
    return ray.interpolate(intervals[0][1]) if intervals else None


def _depth_inside_km(poly, spot_ll: Point) -> float:
    """How far inside *poly* the spot itself sits, in km: 0 for a spot on or off the coast,
    else the geodesic distance to the nearest point of the polygon's boundary. Cached."""
    key = (id(poly), spot_ll.x, spot_ll.y)
    d = _DEPTH_CACHE.get(key)
    if d is None:
        if not _prepared(poly).contains(spot_ll):
            d = 0.0
        else:
            coast, _ = nearest_points(poly.boundary, spot_ll)
            d = _GEOD.inv(spot_ll.x, spot_ll.y, coast.x, coast.y)[2] / 1000.0
        _DEPTH_CACHE[key] = d
    return d


def _nearest_km(poly, spot_ll: Point) -> float:
    """Geodesic distance from the spot to the nearest point of *poly*, in km; 0 when the
    spot is on or inside it. R1's own-coast test reads this. Cached."""
    key = (id(poly), spot_ll.x, spot_ll.y)
    d = _NEAREST_CACHE.get(key)
    if d is None:
        if _prepared(poly).intersects(spot_ll):
            d = 0.0
        else:
            near, _ = nearest_points(poly, spot_ll)
            d = _GEOD.inv(spot_ll.x, spot_ll.y, near.x, near.y)[2] / 1000.0
        _NEAREST_CACHE[key] = d
    return d


def _cross_width_km(poly, spot_ll: Point, bearing_deg: float) -> float:
    """The polygon's width across a ray from the spot on *bearing_deg*, in km: the spread of
    its outline's offsets from the ray line, each vertex's offset being s·sin(az − bearing)
    for its geodesic distance s and azimuth az from the spot (exact on the spot's azimuthal
    equidistant plane). The vertex (az, s) pairs are cached per spot and polygon."""
    key = (id(poly), spot_ll.x, spot_ll.y)
    polar = _POLAR_CACHE.get(key)
    if polar is None:
        lons: list[float] = []
        lats: list[float] = []
        for part in getattr(poly, "geoms", [poly]):
            xs, ys = part.exterior.coords.xy
            lons.extend(xs)
            lats.extend(ys)
        az, _, dist = _GEOD.inv([spot_ll.x] * len(lons), [spot_ll.y] * len(lats), lons, lats)
        polar = (list(az), [m / 1000.0 for m in dist])
        _POLAR_CACHE[key] = polar
    offsets = [s * math.sin(math.radians(a - bearing_deg)) for a, s in zip(*polar)]
    return max(offsets) - min(offsets)


def fresnel_number(width_km: float, dist_km: float, period_s: float = SWELL_FRESNEL_PERIOD_S) -> float:
    """N = w² / (L·r) for an obstacle *width_km* across, *dist_km* away, at a swell of
    *period_s*: L = g·T²/(2π), the deep-water wavelength (224.83 m at 12 s)."""
    wavelength_m = SWELL_FRESNEL_GRAVITY * period_s ** 2 / (2.0 * math.pi)
    return (width_km * 1000.0) ** 2 / (wavelength_m * dist_km * 1000.0)


def _ray_hits(ray: LineString, land, spot_ll: Point) -> list[tuple[object, float]]:
    """Return [(polygon, nearest_seaward_hit_km), …] for land the outbound ray runs
    INTO from open water.

    The ray starts SWELL_LOCAL_COAST_EXCLUSION_KM offshore, but for a spot sitting on
    (or just inland of) the GSHHG coastline that start can still be INSIDE the spot's
    own landmass. Counting that near-field own-coast as a hit hard-blocks even seaward
    bearings, since the ray exits to open ocean a few km out (the too-narrow-window
    bug). So a polygon the ray STARTS inside blocks the bearing if the ray crosses more
    than SWELL_OWN_LAND_CROSSING_KM beyond the spot's own depth inside it before reaching
    water (it crossed the island or peninsula — whatever it meets afterwards), never exits
    within the fetch (faces into the landmass), or, having left within that allowance,
    re-enters it after reaching open water (a coast across a bay/strait). A ray that
    leaves within the allowance and never returns is open.
    Polygons the ray enters from open water (islands, the far continent) are hits at
    the true water→land crossing, as before.
    """
    best: dict[int, tuple[object, float]] = {}
    for piece, near_ll in _ray_pieces(ray, spot_ll):
        for poly, dist_km in _piece_hits(piece, land, spot_ll, near_ll):
            if id(poly) not in best or dist_km < best[id(poly)][1]:
                best[id(poly)] = (poly, dist_km)
    return list(best.values())


def _piece_hits(ray: LineString, land, spot_ll: Point, near_ll: Point) -> list[tuple[object, float]]:
    """_ray_hits for one piece of the ray: *near_ll* is the spot shifted with the piece (for
    planar nearest-point searches), *spot_ll* the spot itself (for geodesic distances and the
    spot's depth inside its own land)."""
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
            # Ray starts inside this polygon = the spot's own landmass. FIRST: how much of
            # it does the ray cross before it reaches water? Only a little more than the
            # spot's own distance to the coast is the own-coast near field (a seaward ray
            # from a spot slightly inland). More than that and the ray has crossed the land
            # — Banzai Pipeline due south across ~40 km of Oahu — and is blocked at its
            # start, like a bearing that faces into the landmass, WHATEVER IT MEETS
            # AFTERWARDS. Checking this only for rays that never came back left a gap: a ray
            # across the Florida peninsula or the California coast that re-entered the same
            # continent more than SWELL_MAINLAND_SOLID_KM away was graded as a distant hit
            # and wrapped open (Monterey Beach's [129-147], Boca Raton's [205-219]).
            exit_pt = _own_exit_point(ray, inter)
            exit_km = (_GEOD.inv(spot_ll.x, spot_ll.y, exit_pt.x, exit_pt.y)[2] / 1000.0
                       if exit_pt is not None else 0.0)
            if exit_km > _depth_inside_km(poly, spot_ll) + SWELL_OWN_LAND_CROSSING_KM:
                blocker = origin
            else:
                # Within the allowance: skip the own-coast near field and block only on a
                # genuine seaward re-entry (a coast across a bay/strait).
                blocker = _first_seaward_entry(ray, inter, origin)
                if blocker is None:
                    continue  # leaves for open water and never returns → open
            closest_on_inter = blocker
        else:
            # Spot is seaward of this polygon: the nearest intersection point is the
            # real water→land coastline crossing.
            closest_on_inter, _ = nearest_points(inter, near_ll)
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


def _island_shadow(small_hit_dist: dict[int, float], step_deg: int, debug=None,
                  fresnel_n: dict[int, float] | None = None) -> set[int]:
    """Bearings that stay blocked after a run of partial hits is wrap-trimmed.

    The production path (_islands_shadow) calls this once per island, with that island's
    rays only, so a "chain" here is one island's contiguous run. Called directly with a
    single {bearing: dist} map it treats every hit as one obstacle, as before R3.

    Each chain member's diffraction wrap-in is governed by ITS OWN obstacle distance,
    not one chain-wide nearest distance: a member stays blocked only if it lies deeper
    than wrap(its distance) inside BOTH open edges of the run. So a near member persists
    in the interior while a distant (near-transparent) member wraps open on its own large
    wrap even mid-run — a chain that fuses a near headland with distant mainland no longer
    keeps the far core wrongly blocked. This reduces EXACTLY to the old single-wrap core
    when a run's members are all at one distance.

    THE FRESNEL EDGE. *fresnel_n* ({bearing: N}, from _classify_bearings) gives a member's
    Fresnel number where it was measured. A member with N ≥ SWELL_FRESNEL_WALL_N is broad
    enough for its distance to cast a real shadow at the design period, and needs only the
    penumbra, fresnel_edge_deg(d), inside both ends instead of the wrap: the wrap was built
    for small, distant islands and erased a broad island's whole shadow. Members without an
    N, or with N below the wall, keep the wrap.

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
        n_core = n_edge = 0
        for b in range(lo, hi + 1, step_deg):
            d = small_hit_dist.get(b % 360)
            if d is None:
                continue
            n = (fresnel_n or {}).get(b % 360)
            if n is not None and fresnel_verdict(n) == "wall":
                need = fresnel_edge_deg(d)
                n_edge += 1
            else:
                need = SWELL_DIFFRACTION_WRAP_DEG + SWELL_DIFFRACTION_WRAP_PER_100KM * (d / 100.0)
            if (b - lo) >= need and (hi - b) >= need:
                blocked.add(b % 360)
                core_lo = b if core_lo is None else core_lo
                core_hi = b
                n_core += 1
        if debug is not None:
            dmin = min(small_hit_dist[b % 360] for b in range(lo, hi + 1, step_deg)
                       if (b % 360) in small_hit_dist)
            wnear = SWELL_DIFFRACTION_WRAP_DEG + SWELL_DIFFRACTION_WRAP_PER_100KM * (dmin / 100.0)
            how = (f"Fresnel edge on {n_edge} of the rays (N ≥ {SWELL_FRESNEL_WALL_N:g}, "
                   f"{fresnel_edge_deg(dmin):.1f}° at {dmin:.0f}km), wrap on the rest"
                   if n_edge else f"per-member wrap (nearest {dmin:.0f}km → {wnear:.0f}°)")
            if core_lo is None:
                debug.append({"lo": lo % 360, "hi": hi % 360, "width": width,
                              "dmin_km": dmin, "wrap_deg": wnear, "edge_rays": n_edge,
                              "decision": "open_wrapped",
                              "reason": f"{how}: every member opens"})
            else:
                debug.append({"lo": lo % 360, "hi": hi % 360, "width": width,
                              "dmin_km": dmin, "wrap_deg": wnear, "edge_rays": n_edge,
                              "decision": "blocked_core",
                              "core": [core_lo % 360, core_hi % 360],
                              "reason": f"{how}: {n_core} ray(s) "
                                        f"{core_lo % 360}–{core_hi % 360}° stay blocked"})
    return blocked


class IslandHit(NamedTuple):
    """One partial hit on a ray: the distance to it and, where measured, its Fresnel number
    (None for a polygon of SWELL_FRESNEL_ISLAND_MAX_KM2 or more, which keeps the wrap)."""
    dist_km: float
    fresnel_n: float | None = None


def _islands_shadow(partial_hits: dict[int, dict[int, IslandHit]], step_deg: int,
                    debug=None) -> set[int]:
    """R3 — bearings that stay blocked when each island is judged on its own.

    *partial_hits* is _classify_bearings' {bearing: {island_key: IslandHit}}. The hits are
    regrouped by island and every island's own rays go through _island_shadow on their own
    distances and Fresnel numbers; a bearing is blocked if any island blocks it. Two islands
    that sit side by side, or one behind the other, are never fused into one run, so a near
    islet cannot lend a far coast its small wrap, nor a far coast lend a near islet its long
    run.

    *debug*: as _island_shadow, with each record also carrying its "island" key; islands
    are taken in order of their lowest bearing, so the dump reads round the compass."""
    by_island: dict[int, dict[int, IslandHit]] = {}
    for b, hits in partial_hits.items():
        for key, hit in hits.items():
            by_island.setdefault(key, {})[b] = hit
    blocked: set[int] = set()
    for key in sorted(by_island, key=lambda k: (min(by_island[k]), k)):
        rays = by_island[key]
        records: list | None = [] if debug is not None else None
        blocked |= _island_shadow({b: h.dist_km for b, h in rays.items()}, step_deg, debug=records,
                                  fresnel_n={b: h.fresnel_n for b, h in rays.items()
                                             if h.fresnel_n is not None})
        if debug is not None:
            for r in records:
                r["island"] = key
            debug.extend(records)
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


def fresnel_edge_deg(dist_km: float, period_s: float = SWELL_FRESNEL_PERIOD_S) -> float:
    """The penumbra of an obstacle *dist_km* away at a swell of *period_s*, in degrees:
    √(L/r) radians, the first Fresnel zone's half-width √(L·r) seen from the spot."""
    wavelength_km = SWELL_FRESNEL_GRAVITY * period_s ** 2 / (2.0 * math.pi) / 1000.0
    return math.degrees(math.sqrt(wavelength_km / dist_km))


def fresnel_verdict(n: float) -> str:
    """R2 on a Fresnel number: "wall" at SWELL_FRESNEL_WALL_N or more, "pass" below
    SWELL_FRESNEL_TRANSPARENT_N, "partial" in between (both lower ends inclusive)."""
    if n >= SWELL_FRESNEL_WALL_N:
        return "wall"
    if n < SWELL_FRESNEL_TRANSPARENT_N:
        return "pass"
    return "partial"


def _classify_bearings(lat: float, lng: float, land: LandIndex, step_deg: int, debug=None,
                       mainland_solid_km=None):
    """Pass 1 — for every bearing return (hard_blocked set, partial_hits map).

    A bearing is hard-blocked by a landmass ≥ SWELL_BLOCKER_AREA_KM2 within
    SWELL_MAINLAND_SOLID_KM, or by land within SWELL_LOCAL_LANDMASS_KM that R1 (the own
    coast, or ≥ SWELL_BLOCKER_AREA_KM2) or R2 (Fresnel N ≥ SWELL_FRESNEL_WALL_N) calls a
    wall. Otherwise EVERY partial hit on the ray is recorded (R3), keyed by island:
    partial_hits = {bearing: {island_key: IslandHit(dist_km, fresnel_n)}}, island_key being
    id(polygon), so the shadow pass can judge each island on its own run. fresnel_n is
    measured for every polygon under SWELL_FRESNEL_ISLAND_MAX_KM2 (None for larger ones), so
    the shadow pass can give a broad island the Fresnel edge. A near-field island with
    Fresnel N < SWELL_FRESNEL_TRANSPARENT_N is passed: the ray goes on to whatever lies
    behind it.
    A hard-blocked bearing records no partial hits. Shared by compute_swell_window and the
    validate harness (which also reads `hard_blocked` for the ceiling).

    *debug* (diagnostic only; None on the production/full path, so behaviour and
    cost are unchanged): a dict filled per bearing with the block decision —
    {bearing: {"result": "hard"|"small"|"open", ["rule","area_km2","dist_km",
    "centroid","own","fresnel_n","partials","passed"]}} — for the validate harness's
    --debug-blockers culprit dump. ``rule`` is "local_coast_30km" (R1), "fresnel_wall"
    (R2) or "mainland_solid". ``own`` is R1's own-coast test: the blocking polygon's
    nearest point lies within SWELL_OWN_COAST_KM of the spot. A "small" record carries the
    nearest partial hit's dist/area/centroid plus ``partials``, one entry per island hit
    ({"island","dist_km","area_km2","centroid","fresnel_n"}); ``passed`` lists the
    near-field islands the ray went through (R2, N < SWELL_FRESNEL_TRANSPARENT_N).

    *mainland_solid_km* overrides SWELL_MAINLAND_SOLID_KM (the distance within which a
    large landmass hard-blocks) for this call only — used by the validate harness's
    --sweep-mainland-solid sensitivity sweep; None keeps the committed default.
    """
    spot_ll = Point(lng, lat)
    solid_km = SWELL_MAINLAND_SOLID_KM if mainland_solid_km is None else mainland_solid_km
    hard_blocked: set[int] = set()
    partial_hits: dict[int, dict[int, IslandHit]] = {}

    def _rec(poly, dist_km, **extra):
        return {"island": id(poly), "dist_km": dist_km, "area_km2": _poly_area_km2(poly),
                "centroid": _poly_centroid(poly), **extra}

    for bearing in range(0, 360, step_deg):
        ray = _ray_linestring(lat, lng, float(bearing))
        hard = None                       # (rule, poly, dist_km, fresnel_n | None)
        partial: dict[int, IslandHit] = {}
        partial_rec: list[dict] = []
        passed: list[dict] = []
        for poly, dist_km in _ray_hits(ray, land, spot_ll):
            area = _poly_area_km2(poly)
            if dist_km <= SWELL_LOCAL_LANDMASS_KM:
                # R1: the spot's own coast, or a big landmass, is a wall in the near field.
                if area >= SWELL_BLOCKER_AREA_KM2 or _nearest_km(poly, spot_ll) <= SWELL_OWN_COAST_KM:
                    hard = ("local_coast_30km", poly, dist_km, None)
                    break
                # R2: any other near-field island by its Fresnel number at the design period.
                n = fresnel_number(_cross_width_km(poly, spot_ll, float(bearing)), dist_km)
                verdict = fresnel_verdict(n)
                if verdict == "wall":
                    hard = ("fresnel_wall", poly, dist_km, n)
                    break
                if verdict == "pass":
                    if debug is not None:
                        passed.append(_rec(poly, dist_km, fresnel_n=n))
                    continue              # too small for its distance: the ray goes on
                partial[id(poly)] = IslandHit(dist_km, n)
                if debug is not None:
                    partial_rec.append(_rec(poly, dist_km, fresnel_n=n))
                continue
            # A large landmass is SOLID only in the near/mid field; BEYOND
            # SWELL_MAINLAND_SOLID_KM it falls through to the partial-shadow pass, so the
            # distance-aware diffraction wrap graduates it (a coast grazed far downrange
            # under SWELL_MIN_FETCH_KM diffracts around instead of walling off the bearing,
            # while a near coast / bay-strait crossing still hard-blocks).
            if area >= SWELL_BLOCKER_AREA_KM2 and dist_km <= solid_km:
                hard = ("mainland_solid", poly, dist_km, None)
                break
            # Beyond the near field an island's Fresnel number decides how it wraps (the
            # Fresnel edge, in _island_shadow); a continent, Cuba or Hispaniola keeps the wrap.
            n = (fresnel_number(_cross_width_km(poly, spot_ll, float(bearing)), dist_km)
                 if area < SWELL_FRESNEL_ISLAND_MAX_KM2 else None)
            partial[id(poly)] = IslandHit(dist_km, n)
            if debug is not None:
                partial_rec.append(_rec(poly, dist_km, fresnel_n=n))
        if hard is not None:
            hard_blocked.add(bearing)
            if debug is not None:
                rule, poly, dist_km, n = hard
                debug[bearing] = {"result": "hard", "rule": rule, "island": id(poly),
                                  "area_km2": _poly_area_km2(poly), "dist_km": dist_km,
                                  "centroid": _poly_centroid(poly),
                                  "own": _nearest_km(poly, spot_ll) <= SWELL_OWN_COAST_KM}
                if n is not None:
                    debug[bearing]["fresnel_n"] = n
        elif partial:
            partial_hits[bearing] = partial
            if debug is not None:
                nearest = min(partial_rec, key=lambda r: r["dist_km"])
                debug[bearing] = {"result": "small", "dist_km": nearest["dist_km"],
                                  "area_km2": nearest["area_km2"],
                                  "centroid": nearest["centroid"],
                                  "partials": sorted(partial_rec, key=lambda r: r["dist_km"]),
                                  "passed": passed}
        elif debug is not None:
            debug[bearing] = {"result": "open", "passed": passed}
    return hard_blocked, partial_hits


def _implied_span(lo: int, hi: int, step_deg: int) -> int:
    """Span implied by an arc's min (*lo*) / max (*hi*), wrap-aware — the single source of
    truth for how a (min, max) pair reads. interpret._in_any_arc treats min>max as a 0/360
    wraparound, so an emitted arc's ``span`` MUST equal this or a small arc can be misread
    as a ~358° wrap (the degenerate {min:165, max:163, span:2} bug)."""
    return (hi - lo + step_deg) if lo <= hi else (hi + 360 - lo + step_deg)


def _make_arc(lo: int, hi: int, step_deg: int) -> dict:
    """A {min, max, span} arc whose span is DERIVED from the same endpoints (span =
    hi - lo + step over the UNWRAPPED endpoints), so it is always consistent with the
    wrap-aware min/max reading. *hi* may exceed 360 for a wrapped run."""
    return {"min": lo % 360, "max": hi % 360, "span": hi - lo + step_deg}


def _merge_open_arcs(open_bearings: list[int], step_deg: int) -> list[dict]:
    """Collapse a sorted list of open bearings into [min, max, span] arcs.

    Handles wraparound: if 358 and 0 are both open they belong to the same arc. Each arc is
    shrunk SWELL_ARC_SHRINK_DEG inward on both ends for diffraction; a run that shrinks to
    <= one ray step is noise (not a window) and is DROPPED — this is what stops the old
    degenerate sub-step arc (e.g. a 12° run shrinking to {min:165, max:163, span:2}, which
    interpret._in_any_arc mis-read as a ~358° wraparound). Every emitted arc's span agrees
    with its min/max by construction (see _make_arc / _implied_span).
    """
    if not open_bearings:
        return []
    s = set(open_bearings)
    if len(s) * step_deg >= 360:
        return [_make_arc(0, 360 - step_deg, step_deg)]  # fully open (span 360, consistent)

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

    # Wraparound merge: if the first arc starts at 0 and the last ends at 360 - step, splice
    # them into one arc going from last_s (e.g. 340) to first_e (e.g. 20) + 360.
    if len(arcs) >= 2 and arcs[0][0] == 0 and arcs[-1][1] == 360 - step_deg:
        first_s, first_e = arcs[0]
        last_s, last_e = arcs[-1]
        arcs = arcs[1:-1] + [(last_s, first_e + 360)]

    out: list[dict] = []
    for lo, hi in arcs:
        lo_s = lo + SWELL_ARC_SHRINK_DEG
        hi_s = hi - SWELL_ARC_SHRINK_DEG
        if hi_s - lo_s + step_deg <= step_deg:
            continue  # <= one ray step after diffraction shrink → noise, not a window
        out.append(_make_arc(lo_s, hi_s, step_deg))

    # Defensive guard: never return an arc whose span disagrees with what its min/max imply
    # (wrap-aware). _make_arc guarantees this, so a drop here would signal a regression.
    good = [a for a in out if a["span"] == _implied_span(a["min"], a["max"], step_deg)]
    if len(good) != len(out):
        log.warning("swell arc(s) dropped for inconsistent span: %s",
                    [a for a in out if a["span"] != _implied_span(a["min"], a["max"], step_deg)])
    return good


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

    # Pass 1 — hard-block vs partial (island) hit vs open.
    hard_blocked, partial_hits = _classify_bearings(lat, lng, land, step)
    # Pass 2 — each island's own run of rays, wrap-trimmed on its own distance (R3).
    small_blocked = _islands_shadow(partial_hits, step)

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
