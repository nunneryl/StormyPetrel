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
from pipeline.interpret import _in_any_arc

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
    sw._PREPARED_CACHE.clear()
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
    sw._PREPARED_CACHE.clear()
    debug_rays: dict = {}
    hard, small = sw._classify_bearings(0.0, 0.0, land, step, debug=debug_rays)
    debug_chains: list = []
    small_blocked = sw._island_shadow(small, step, debug=debug_chains)
    return debug_rays, debug_chains, hard, small_blocked


def test_debug_blockers_attribution():
    # large landmass within the solid range: a ≥500 km² wall at ~33 km (>30 km local
    # guard, ≤100 km SWELL_MAINLAND_SOLID_KM) → hard by the mainland-solid rule; the
    # opposite bearing (no hits) is recorded OPEN.
    dr, _, hard, _ = _debug_cast([_square(600, 45, 90)])
    assert dr[90]["result"] == "hard" and dr[90]["rule"] == "mainland_solid"
    assert sw.SWELL_LOCAL_LANDMASS_KM < dr[90]["dist_km"] <= sw.SWELL_MAINLAND_SOLID_KM
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


def test_open_ocean_window_survives_own_coast():
    # The exact failing case: a spot sitting ~5.5 km INLAND of a large landmass's west
    # coast, open ocean to the WEST. Before the fix, the ray's 2 km start sat inside the
    # continent and hard-blocked EVERY bearing (seaward included) at ~2 km. The seaward
    # half must be OPEN; only the landward half BLOCKED. Continent = everything east of
    # lng=-0.05 (west coast there); the spot at (0,0) is ~5.5 km inside it, and it is far
    # larger than SWELL_BLOCKER_AREA_KM2 so it is a genuine wall.
    continent = Polygon([(-0.05, -40), (40, -40), (40, 40), (-0.05, 40)])
    _, r = _open([continent])
    arcs = r["swell_window_arcs"]
    assert arcs, "a west-facing coastal spot must have an open window"

    def _in_open_arc(b):
        for a in arcs:
            lo, hi = a["min"], a["max"]
            if (lo <= hi and lo <= b <= hi) or (lo > hi and (b >= lo or b <= hi)):
                return True
        return False

    for seaward in (225, 250, 270, 290, 315):
        assert _in_open_arc(seaward), f"seaward bearing {seaward}° (open ocean) must be OPEN"
    for landward in (45, 90, 135):
        assert not _in_open_arc(landward), f"landward bearing {landward}° (into the continent) must be BLOCKED"
    span = sum(a["span"] for a in arcs)
    assert 120 < span < 260, f"window should be ~the seaward semicircle, not all/none (got {span}°)"


def test_own_coast_skipped_but_offshore_reentry_still_blocks():
    # Guard against over-opening: a spot ~3.3 km inland of a west coast, with a SECOND
    # coast ~28 km offshore to the west (across a strait). The own coast must NOT block
    # the west bearing, but the ray re-entering the offshore land MUST — and at the real
    # ~28 km crossing, not the 2 km own-coast start.
    mainland = Polygon([(-0.03, -40), (40, -40), (40, 40), (-0.03, 40)])       # spot ~3.3 km inland
    offshore = Polygon([(-0.30, -40), (-0.25, -40), (-0.25, 40), (-0.30, 40)])  # wall ~28 km west
    dr, _, hard, _ = _debug_cast([mainland, offshore])
    assert 270 in hard, "west bearing must still be blocked by the offshore coast across the strait"
    assert dr[270]["result"] == "hard"
    assert dr[270]["dist_km"] > 20, \
        f"blocked at the real offshore crossing, not the 2 km own coast (got {dr[270]['dist_km']:.1f} km)"


def test_distant_large_landmass_wraps_open():
    # The min-fetch relaxation: a ≥500 km² landmass at 45 km is a solid wall (within
    # SWELL_MAINLAND_SOLID_KM); the SAME compact landmass BEYOND that distance is demoted
    # to a partial blocker and wraps clean open instead of hard-blocking downrange.
    near, _ = _open([_square(600, 45)])          # ~33 km — inside the solid range
    assert _blocked_near(near, 90), "≥500 km² wall within SWELL_MAINLAND_SOLID_KM still hard-blocks"
    far, _ = _open([_square(600, 200)])          # ~188 km — beyond the 100 km default
    assert 90 in far, "the same wall beyond SWELL_MAINLAND_SOLID_KM must wrap open, not hard-block"


def test_near_mainland_across_strait_still_blocks():
    # A large landmass across a strait — beyond the 30 km local guard but WITHIN
    # SWELL_MAINLAND_SOLID_KM — is still a solid wall; only DISTANT mainland is relaxed.
    dr, _, hard, _ = _debug_cast([_square(600, 60)])   # ~48 km: >30 km guard, <100 km solid
    assert 90 in hard, "≥500 km² landmass within SWELL_MAINLAND_SOLID_KM must still hard-block"
    assert dr[90]["rule"] == "mainland_solid"
    assert sw.SWELL_LOCAL_LANDMASS_KM < dr[90]["dist_km"] <= sw.SWELL_MAINLAND_SOLID_KM


def test_island_spot_with_mainland_behind_stays_bounded():
    # RI case: a spot on a small island (<500 km²) with a large MAINLAND close behind it
    # (within SWELL_MAINLAND_SOLID_KM) and only DISTANT land seaward. The min-fetch
    # relaxation must not blow it open to 360° — the near mainland still bounds the
    # landward side; only the distant seaward landmass is relaxed (wraps open).
    island = Polygon([(-0.06, -0.04), (0.06, -0.04), (0.06, 0.06), (-0.06, 0.06)])  # spot inside, ~148 km²
    mainland = Polygon([(-40, 0.45), (40, 0.45), (40, 40), (-40, 40)])              # large; south coast ~50 km N
    distant = _square(600, 250, 180)                                                # ≥500 km², ~240 km S -> relaxed
    _, r = _open([island, mainland, distant])
    arcs = r["swell_window_arcs"]
    assert arcs, "the seaward (south) side must be open"

    def _in(b):
        return any((a["min"] <= b <= a["max"]) if a["min"] <= a["max"]
                   else (b >= a["min"] or b <= a["max"]) for a in arcs)

    assert _in(180), "due south (open ocean) must be OPEN"
    assert not _in(0), "due north (large mainland close behind, within the solid range) must stay BLOCKED"
    assert sum(a["span"] for a in arcs) < 300, "a spot with a mainland close behind must stay bounded, not ~360°"


def test_mainland_solid_km_override_sweeps():
    # The --sweep-mainland-solid path passes SWELL_MAINLAND_SOLID_KM per call. A ≥500 km²
    # wall at ~128 km is SOLID (hard) when the override is 150 km but DEMOTED when it is
    # 100 km — and the None default matches passing the committed constant.
    idx = _index([_square(600, 140)])   # nearest edge ~128 km
    sw._AREA_CACHE.clear()
    sw._PREPARED_CACHE.clear()
    hard_hi, _ = sw._classify_bearings(0.0, 0.0, idx, sw.SWELL_RAY_STEP_DEG, mainland_solid_km=150.0)
    hard_lo, _ = sw._classify_bearings(0.0, 0.0, idx, sw.SWELL_RAY_STEP_DEG, mainland_solid_km=100.0)
    assert 90 in hard_hi, "≥500 km² wall at ~128 km is SOLID when the solid range is 150 km"
    assert 90 not in hard_lo, "the same wall is DEMOTED (not hard) when the solid range is 100 km"
    hard_def, _ = sw._classify_bearings(0.0, 0.0, idx, sw.SWELL_RAY_STEP_DEG)
    hard_committed, _ = sw._classify_bearings(0.0, 0.0, idx, sw.SWELL_RAY_STEP_DEG,
                                              mainland_solid_km=sw.SWELL_MAINLAND_SOLID_KM)
    assert hard_def == hard_committed, "None override == passing the committed SWELL_MAINLAND_SOLID_KM"


def test_chain_per_member_far_core_opens_near_core_stays():
    # (a) A chain fusing a NEAR broad section (100-140 @ 40 km) with a FAR section
    # (142-200 @ 2000 km). Per-member wrap must OPEN the far members (own wrap ~176° >>
    # the run) while the near section keeps its interior core. Under the OLD single-dmin
    # wrap the far members were wrongly held in the core (dmin 40 km → wrap 19° → core).
    step = sw.SWELL_RAY_STEP_DEG
    shd = {}
    for b in range(100, 141, step):
        shd[b] = 40.0
    for b in range(142, 201, step):
        shd[b] = 2000.0
    blocked = sw._island_shadow(shd, step)
    assert all(b not in blocked for b in range(142, 201, step)), \
        "far members (2000 km) wrap open on their OWN distance, not the chain-wide dmin"
    assert 150 not in blocked, "a mid-run far member is open"
    assert any(b in blocked for b in range(100, 141, step)), \
        "the near broad section still keeps its interior core"


def test_chain_per_member_all_near_broad_keeps_core():
    # (b) An all-near broad chain (100-200 @ 40 km, ~100° wide) must still keep its core —
    # the per-member rule reduces to the old single-wrap core when distances are uniform.
    step = sw.SWELL_RAY_STEP_DEG
    blocked = sw._island_shadow({b: 40.0 for b in range(100, 201, step)}, step)
    assert 150 in blocked, "an all-near broad chain keeps its interior core"
    assert 100 not in blocked and 200 not in blocked, "the edges wrap open"


def test_ri_island_spot_stays_bounded_far_seaward_opens():
    # (c) Second Beach RI shape: a spot on a small island (~110 km²) with a large MAINLAND
    # close behind (within SWELL_MAINLAND_SOLID_KM — bounds the landward side) and only
    # DISTANT land seaward. The distant seaward land wraps open on its own distance, yet
    # the spot stays bounded — nowhere near 360°.
    island = Polygon([(-0.05, -0.04), (0.05, -0.04), (0.05, 0.05), (-0.05, 0.05)])  # spot inside, ~110 km²
    mainland = Polygon([(-40, 0.40), (40, 0.40), (40, 40), (-40, 40)])              # ~44 km N, bounds landward
    distant = Polygon([(-40, -20), (40, -20), (40, -16), (-40, -16)])               # wide, ~1800 km S (far)
    _, r = _open([island, mainland, distant])
    arcs = r["swell_window_arcs"]
    assert arcs, "the seaward side must be open"

    def _in(b):
        return any((a["min"] <= b <= a["max"]) if a["min"] <= a["max"]
                   else (b >= a["min"] or b <= a["max"]) for a in arcs)

    assert _in(180), "distant seaward land (~1800 km) wraps open by its own distance"
    assert not _in(0), "the mainland ~44 km behind stays BLOCKED"
    assert sum(a["span"] for a in arcs) < 300, "a spot on a small island stays bounded, not ~360°"


def test_merge_arcs_drops_degenerate_substep_sliver():
    # BUG 1(a): a 3-bearing open run (160,164,168 = 12° wide) shrinks to 2° and must be
    # DROPPED — never emitted as the degenerate {min:165, max:163, span:2} that
    # interpret._in_any_arc mis-read as a ~358° wrap. Check straight and wrapped slivers.
    step = 4
    assert sw._merge_open_arcs([160, 164, 168], step) == [], "12° run → 2° shrunk → dropped"
    for open_b in ([100, 104, 108], [0, 4, 8], [356, 0, 4], [352, 356, 0]):
        for a in sw._merge_open_arcs(open_b, step):
            assert not (a["min"] > a["max"] and a["span"] <= step), f"degenerate arc emitted: {a}"


def test_merge_arcs_keeps_legit_wraparound():
    # BUG 1(b): a genuine ~44° window straddling North (340..356, 0..20) must survive as a
    # wraparound arc (~34° after shrink) and read as ~40°, NOT be dropped or misread as 358°.
    step = 4
    arcs = sw._merge_open_arcs(list(range(340, 360, step)) + list(range(0, 24, step)), step)
    assert len(arcs) == 1, f"one wraparound arc expected, got {arcs}"
    a = arcs[0]
    assert a["min"] > a["max"], "a genuine wrap has min>max"
    assert a["span"] == (a["max"] + 360 - a["min"] + step), "span consistent with the wrap"
    assert 20 < a["span"] < 60, f"a ~40° window, not ~358° or dropped (got {a['span']})"
    assert _in_any_arc(0.0, arcs) and _in_any_arc(350.0, arcs) and _in_any_arc(10.0, arcs)
    assert not _in_any_arc(180.0, arcs), "a swell from the opposite side is OUTSIDE the wrap"


def test_emitted_arcs_span_consistent_with_minmax():
    # BUG 1(c): for EVERY arc the raycast can emit, span must equal what min/max imply
    # (wrap-aware) and never be sub-step — so _in_any_arc and the span field always agree.
    step = 4

    def implied(a):
        return (a["max"] - a["min"] + step) if a["min"] <= a["max"] else (a["max"] + 360 - a["min"] + step)

    patterns = [
        list(range(0, 360, step)),                                    # fully open
        list(range(100, 200, step)),                                  # one mid run
        [160, 164, 168],                                              # sub-step sliver (dropped)
        list(range(340, 360, step)) + list(range(0, 24, step)),       # wraparound
        list(range(0, 40, step)) + list(range(200, 300, step)),       # two runs
        list(range(0, 16, step)),                                     # 4-bearing run (16° → 6° arc)
        [0, 4, 8, 340, 344, 348, 352, 356],                          # wrap + short leading run
    ]
    saw_wrap = False
    for open_b in patterns:
        for a in sw._merge_open_arcs(open_b, step):
            assert a["span"] == implied(a), f"span disagrees with min/max: {a}"
            assert a["span"] > step, f"sub-step arc emitted: {a}"
            saw_wrap = saw_wrap or a["min"] > a["max"]
    assert saw_wrap, "a wraparound pattern should still produce a (consistent) min>max arc"


def test_full_cast_preserves_nwps_mop_source():
    # BUG 2: run_full must NOT demote NWPS/MOP spots to raycast. Test the pure per-spot
    # writer used by run_full.
    from pipeline.sw1_raycast import _apply_raycast_result
    r = {"swell_window_arcs": [{"min": 180, "max": 260, "span": 84}], "optimal_swell_dir": 220}
    for src in ("nwps", "cdip_mop"):
        spot = {"swell_window_source": src, "swell_window_arcs": [], "optimal_swell_dir": None}
        assert _apply_raycast_result(spot, r) == "raycast"
        assert spot["swell_window_source"] == src, f"{src} must be PRESERVED, not demoted to raycast"
        assert spot["swell_window_arcs"] == r["swell_window_arcs"], "arcs still refreshed (dormant fallback)"
    # a non-better-tier spot IS tagged raycast on a solve
    spot = {"swell_window_source": "orientation_derived"}
    _apply_raycast_result(spot, r)
    assert spot["swell_window_source"] == "raycast"
    # a better-tier spot the raycast can't solve is left untouched (arcs + source)
    keep = [{"min": 1, "max": 2, "span": 5}]
    spot = {"swell_window_source": "nwps", "swell_window_arcs": keep, "optimal_swell_dir": 90}
    assert _apply_raycast_result(spot, None) == "preserved"
    assert spot["swell_window_source"] == "nwps" and spot["swell_window_arcs"] == keep


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} synthetic-geometry checks passed")


if __name__ == "__main__":
    _run_all()
