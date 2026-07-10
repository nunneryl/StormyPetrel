"""Synthetic-geometry checks for the SW-1 blocker fix.

These build hand-placed square islands in empty ocean so the three required
behaviours are tested independently of any real coastline data set:

  * area filter       — a sub-500 km² island stops hard-blocking;
  * local-landmass     — a small island AT the spot still blocks;
  * angular ignore     — an obstacle subtending < 5° is ignored;
  * distance-aware      — the same small island blocks near, wraps far;
  * chain partial block — a contiguous island wall keeps an interior shadow.

Run: python -m pipeline.tests.test_swell_window   (or pytest)
"""
from __future__ import annotations

from shapely.geometry import Polygon
from shapely.strtree import STRtree

from pipeline.enrichment import swell_window as sw
from pipeline.enrichment.geodata import LandIndex

KM_PER_DEG = 111.32  # at the equator, where we place the test spot


def _square(area_km2: float, dist_km: float, bearing_deg: float = 90.0) -> Polygon:
    """A square island of *area_km2*, centred *dist_km* from (0,0) along bearing."""
    import math
    half = (area_km2 ** 0.5) / 2.0 / KM_PER_DEG
    cx = dist_km / KM_PER_DEG * math.sin(math.radians(bearing_deg))
    cy = dist_km / KM_PER_DEG * math.cos(math.radians(bearing_deg))
    return Polygon([(cx - half, cy - half), (cx + half, cy - half),
                    (cx + half, cy + half), (cx - half, cy + half)])


def _index(polys):
    return LandIndex(polygons=polys, polygon_tree=STRtree(polys), coastlines=[], coastline_tree=None)


def _open(polys):
    """Open-bearing set for a spot at (0,0) given these island polygons."""
    land = _index(polys)
    sw._AREA_CACHE.clear()
    orig = sw.load_land_index
    sw.load_land_index = lambda: land
    try:
        r = sw.compute_swell_window({"name": "t", "lat": 0.0, "lng": 0.0})
    finally:
        sw.load_land_index = orig
    open_b = set()
    for a in r["swell_window_arcs"]:
        lo, hi = a["min"], a["max"]
        hi = hi + 360 if hi < lo else hi
        b = lo
        while b <= hi:
            open_b.add(b % 360)
            b += sw.SWELL_RAY_STEP_DEG
    return open_b, r


def _blocked_near(open_b, bearing=90, halfwidth=2):
    return all((bearing + d) % 360 not in open_b for d in range(-halfwidth, halfwidth + 1, 2))


def test_area_filter_demotes_small_island():
    # 194 km² (Catalina) island at 45 km — must NOT wall off its bearing.
    open_small, _ = _open([_square(194, 45)])
    assert 90 in open_small, "sub-500 km² island should stop hard-blocking"
    # 600 km² island, same spot — above threshold, still a hard wall.
    open_big, _ = _open([_square(600, 45)])
    assert _blocked_near(open_big, 90), "≥500 km² island must still hard-block"


def test_local_landmass_always_blocks():
    # 108 km² island (Aquidneck-sized) right at the spot (≈5 km) stays a blocker
    # even though it's sub-threshold — this is the coast the spot sits on.
    open_local, _ = _open([_square(108, 8)])
    assert _blocked_near(open_local, 90), "local landmass must block regardless of area"


def test_distance_aware_partial_block():
    # Same 108 km² island far offshore (80 km) wraps clean — distance-aware.
    open_far, _ = _open([_square(108, 80)])
    assert 90 in open_far, "a small island far offshore should wrap open"


def test_angular_shadow_ignored_under_5deg():
    # 20 km² island at 200 km subtends ~1.3° — ignored entirely.
    open_tiny, _ = _open([_square(20, 200)])
    assert 90 in open_tiny, "obstacle subtending < 5° must be ignored"


def test_chain_keeps_interior_blocked():
    # A contiguous wall of three abutting 300 km² islands (~50°+ of shadow) keeps
    # its interior blocked even though each island is individually sub-threshold.
    polys = [_square(300, 45, b) for b in (74, 90, 106)]
    open_chain, r = _open(polys)
    assert _blocked_near(open_chain, 90), "interior of an island chain stays blocked"
    assert r["swell_window_arcs"], "the rest of the compass is still open"


def _debug_cast(polys, step=None):
    """Run the two classifier passes with debug collectors attached — mirrors the
    validate harness's --debug-blockers path. Returns (debug_rays, debug_chains,
    hard, small_blocked) for a spot at (0,0)."""
    step = step or sw.SWELL_RAY_STEP_DEG
    land = _index(polys)
    sw._AREA_CACHE.clear()
    debug_rays: dict = {}
    hard, small = sw._classify_bearings(0.0, 0.0, land, step, debug=debug_rays)
    debug_chains: list = []
    small_blocked = sw._island_shadow(small, step, debug=debug_chains)
    return debug_rays, debug_chains, hard, small_blocked


def test_debug_blockers_attribution():
    # area filter: a ≥500 km² island at 45 km along bearing 90 → hard by AREA;
    # the opposite bearing (no hits) is recorded OPEN.
    dr, _, hard, _ = _debug_cast([_square(600, 45, 90)])
    assert dr[90]["result"] == "hard" and dr[90]["rule"] == "area_filter_500km2"
    assert dr[90]["area_km2"] >= sw.SWELL_BLOCKER_AREA_KM2 and len(dr[90]["centroid"]) == 2
    assert 90 in hard and dr[270]["result"] == "open"

    # local-coast guard: a sub-threshold island AT the spot (≈8 km) → hard by the
    # 30 km LOCAL rule, not area.
    dr2, _, _, _ = _debug_cast([_square(108, 8, 90)])
    assert dr2[90]["result"] == "hard" and dr2[90]["rule"] == "local_coast_30km"
    assert dr2[90]["dist_km"] <= sw.SWELL_LOCAL_LANDMASS_KM

    # island chain: three abutting 300 km² islands → a BLOCKED-core chain record
    # (wrap-distance rule), matching test_chain_keeps_interior_blocked.
    _, dcc, _, sb_chain = _debug_cast([_square(300, 45, b) for b in (74, 90, 106)])
    assert any(c["decision"] == "blocked_core" for c in dcc), "chain interior stays blocked"
    assert sb_chain, "the chain contributes blocked bearings"

    # far small island: 108 km² at 80 km wraps clean → an OPEN chain record, no block
    # (matches test_distance_aware_partial_block).
    _, dcf, _, sb_far = _debug_cast([_square(108, 80, 90)])
    assert dcf and all(c["decision"].startswith("open") for c in dcf)
    assert not sb_far


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} synthetic-geometry checks passed")


if __name__ == "__main__":
    _run_all()
