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
from pipeline.interpret import in_any_arc

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
    sw._DEPTH_CACHE.clear()
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
    sw._DEPTH_CACHE.clear()
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
    # The #64 case: a spot sitting ~5.5 km INLAND of a large landmass's west coast, open
    # ocean to the WEST. Before #64, the ray's 2 km start sat inside the continent and
    # hard-blocked EVERY bearing (seaward included) at ~2 km. The seaward side must be OPEN,
    # the landward side BLOCKED. Continent = everything east of lng=-0.05 (west coast
    # there); it is far larger than SWELL_BLOCKER_AREA_KM2, so it is a genuine wall.
    #
    # WHAT THE OWN-LAND ALLOWANCE DOES HERE, BY HAND. The spot's depth inside the continent
    # is 0.05 deg x 111.32 = 5.566 km (nearest coast due west). A seaward ray phi degrees
    # off due west leaves the land after 5.566 / cos(phi) km, and is open while that is
    # within depth + SWELL_OWN_LAND_CROSSING_KM = 7.566 km, i.e. cos(phi) >= 0.7357,
    # phi <= 42.6 deg. At the 2-degree step that is 228..312 (phi 42 at both ends;
    # cos 44 = 0.719 fails). Shrunk 5 deg each end: [233-307], span 307-233+2 = 76.
    # #64 left this spot the whole western semicircle; the grazing bearings it now drops
    # cross 7.6 to 16 km of land, which is the landward-ray fault the allowance stops.
    continent = Polygon([(-0.05, -40), (40, -40), (40, 40), (-0.05, 40)])
    _, r = _open([continent])
    assert r["swell_window_arcs"] == [{"min": 233, "max": 307, "span": 76}], r["swell_window_arcs"]
    assert r["optimal_swell_dir"] == 270, r["optimal_swell_dir"]
    arcs = r["swell_window_arcs"]

    # Membership comes from the SHARED helper, never a local copy: a test that
    # re-implements its subject cannot catch a change to it (this copy compared raw
    # min/max and so would have passed straight through the half-step-pad fix).
    def _in_open_arc(b):
        return in_any_arc(b, arcs)

    for seaward in (240, 250, 270, 290, 300):
        assert _in_open_arc(seaward), f"seaward bearing {seaward}° (open ocean) must be OPEN"
    for landward in (45, 90, 135):
        assert not _in_open_arc(landward), f"landward bearing {landward}° (into the continent) must be BLOCKED"


# --------------------------------------------------------------------------- #
# The own-land allowance on an island: Banzai Pipeline's S-SW window          #
# --------------------------------------------------------------------------- #
# A 0.65 x 0.416 degree rectangle at Oahu's latitude: west edge -158.30, east edge -157.65,
# south edge 21.25, north coast 21.666 (~67 x 46 km, ~3,100 km2, so a SOLID landmass). The
# spot is at lng -158.053, which puts it 0.247 deg x 103.4 km/deg = 25.5 km from the west
# edge and 0.403 deg x 103.4 = 41.7 km from the east edge, with ~46 km of island to its
# south. Rays at the roster's 4-degree step.
#
# Every expected window below is worked by hand, never read back from compute_swell_window.
# Two spot positions, each with and without the 2 km ray-start skip:
#
#   OFFSHORE, lat 21.669: 0.003 deg x 110.77 km/deg = 0.332 km north of the coast.
#     Bearing 92 heads sin(2) = 0.0349 south per km and so meets the coast at
#     0.332 / 0.0349 = 9.5 km (inside the 41.7 km east edge): land within 30 km, hard. 268
#     the same against the 25.5 km west edge. 88 and 272 head north, away from the island.
#     With the skip, bearings 100..260 start 2 km out INSIDE the island (2 x sin 10 =
#     0.347 > 0.332 km) and cross it: 25-62 km of land, far past the 2 km allowance
#     (depth 0, the spot is not on land), so blocked. Window: 272..88 open, shrunk 5 deg
#     -> [277-83], span (83-277) mod 360 + 4 = 170; symmetric about north, optimal 0.
#     BEFORE THE FIX, with the skip, 100..260 crossed the island, never came back and
#     counted OPEN: arcs [105-255] and [277-83], Pipeline's S-SW arc in miniature.
#
#   INSIDE, lat 21.665: 0.001 deg x 110.77 = 0.111 km inside the north coast (the depth).
#     Allowance = 0.111 + 2 = 2.111 km. Bearing 84 leaves the land after
#     0.111 / cos 84 = 0.111 / 0.1045 = 1.06 km: open. Bearing 88 only after
#     0.111 / cos 88 = 0.111 / 0.0349 = 3.18 km (3.24 km along the ray, which is drawn as
#     50 km chords): past the allowance either way, blocked; 272 the same.
#     With the skip, 84 starts 2 x cos 84 = 0.209 km north, already at sea: open. Window:
#     276..84, shrunk -> [281-79], span (79-281) mod 360 + 4 = 162, optimal 0.
#     BEFORE THE FIX every landward ray left the island and never came back, so the whole
#     compass was open: [0-356], span 360, no optimal.
_ISLAND = Polygon([(-158.30, 21.25), (-157.65, 21.25), (-157.65, 21.666), (-158.30, 21.666)])
_OFFSHORE = (21.669, -158.053)
_INSIDE = (21.665, -158.053)
_SEAWARD_ONLY = [{"min": 277, "max": 83, "span": 170}]
_INSIDE_WINDOW = [{"min": 281, "max": 79, "span": 162}]


def _cast_island(lat_lng, skip_km=None, allowance_km=None):
    """compute_swell_window for one spot against _ISLAND alone, at the 4-degree step, with
    the ray-start skip and the own-land allowance optionally overridden for this call."""
    sw._AREA_CACHE.clear()
    sw._PREPARED_CACHE.clear()
    sw._DEPTH_CACHE.clear()
    saved = sw.SWELL_LOCAL_COAST_EXCLUSION_KM, sw.SWELL_OWN_LAND_CROSSING_KM
    try:
        if skip_km is not None:
            sw.SWELL_LOCAL_COAST_EXCLUSION_KM = skip_km
        if allowance_km is not None:
            sw.SWELL_OWN_LAND_CROSSING_KM = allowance_km
        lat, lng = lat_lng
        return sw.compute_swell_window({"name": "t", "lat": lat, "lng": lng},
                                       land=_index([_ISLAND]), ray_step=4)
    finally:
        sw.SWELL_LOCAL_COAST_EXCLUSION_KM, sw.SWELL_OWN_LAND_CROSSING_KM = saved


def test_island_offshore_spot_with_the_skip_blocks_the_rays_across_the_island():
    r = _cast_island(_OFFSHORE)
    assert r["swell_window_arcs"] == _SEAWARD_ONLY, r["swell_window_arcs"]
    assert r["optimal_swell_dir"] == 0, r["optimal_swell_dir"]
    assert not in_any_arc(190, r["swell_window_arcs"]), "due S-SW crosses the island"


def test_island_offshore_spot_without_the_skip():
    r = _cast_island(_OFFSHORE, skip_km=0)
    assert r["swell_window_arcs"] == _SEAWARD_ONLY, r["swell_window_arcs"]
    assert r["optimal_swell_dir"] == 0, r["optimal_swell_dir"]


def test_island_spot_inside_the_polygon_with_the_skip():
    r = _cast_island(_INSIDE)
    assert r["swell_window_arcs"] == _INSIDE_WINDOW, r["swell_window_arcs"]
    assert r["optimal_swell_dir"] == 0, r["optimal_swell_dir"]


def test_island_spot_inside_the_polygon_without_the_skip():
    r = _cast_island(_INSIDE, skip_km=0)
    assert r["swell_window_arcs"] == _INSIDE_WINDOW, r["swell_window_arcs"]
    assert r["optimal_swell_dir"] == 0, r["optimal_swell_dir"]


def test_the_allowance_is_measured_from_the_spots_own_depth():
    """The continent case with the allowance raised to 5 km. The spot is 5.566 km inside, so
    a seaward ray is open while 5.566 / cos(phi) <= 5.566 + 5 = 10.566 km: cos(phi) >= 0.5268,
    phi <= 58.2 deg. At the 2-degree step that is 212..328 (cos 58 = 0.530 passes, cos 60 =
    0.5 fails), shrunk 5 deg each end: [217-323], span 323-217+2 = 108. Measured from the
    spot alone, with no depth, no ray could leave the continent within 5 km and the window
    would be empty."""
    continent = Polygon([(-0.05, -40), (40, -40), (40, 40), (-0.05, 40)])
    saved = sw.SWELL_OWN_LAND_CROSSING_KM
    try:
        sw.SWELL_OWN_LAND_CROSSING_KM = 5.0
        _, r = _open([continent])
    finally:
        sw.SWELL_OWN_LAND_CROSSING_KM = saved
    assert r["swell_window_arcs"] == [{"min": 217, "max": 323, "span": 108}], r["swell_window_arcs"]


def test_a_ray_across_the_island_is_blocked_at_its_start_by_the_local_coast_rule():
    """Where the block is attributed, for --debug-blockers: the due-south ray from the
    offshore spot starts 2 km out inside the island and leaves it ~46 km later. It is
    blocked AT ITS START, 2 km from the spot, by the local-coast rule, and flagged own —
    not at the far shore 46 km away under the mainland-solid rule."""
    sw._AREA_CACHE.clear()
    sw._PREPARED_CACHE.clear()
    sw._DEPTH_CACHE.clear()
    dr = {}
    lat, lng = _OFFSHORE
    sw._classify_bearings(lat, lng, _index([_ISLAND]), 4, debug=dr)
    assert (dr[180]["result"], dr[180]["rule"], dr[180]["own"]) == ("hard", "local_coast_30km", True)
    assert round(dr[180]["dist_km"], 3) == 2.0, dr[180]["dist_km"]


def test_a_spot_off_the_coast_has_no_depth_allowance():
    """A spot OFF the land gets no depth allowance, however close the coast. A strip of land
    1.5-2.5 km east of a spot at (0,0) (0.01348-0.02246 deg of longitude at the equator,
    222 km long, ~222 km2): the due-east ray starts 2 km out inside the strip and leaves it
    at 2.5 km. The spot is not on that land, so its depth is 0 and the allowance 2 km:
    2.5 > 2, blocked. Taking the depth from the distance to the strip (1.5 km) would allow
    3.5 km and let the ray through."""
    strip = Polygon([(0.01348, -1.0), (0.02246, -1.0), (0.02246, 1.0), (0.01348, 1.0)])
    _, r = _open([strip])
    assert not in_any_arc(90, r["swell_window_arcs"]), r["swell_window_arcs"]


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

    def _in(b):                      # shared helper, not a local re-implementation
        return in_any_arc(b, arcs)

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
    sw._DEPTH_CACHE.clear()
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

    def _in(b):                      # shared helper, not a local re-implementation
        return in_any_arc(b, arcs)

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
    assert in_any_arc(0.0, arcs) and in_any_arc(350.0, arcs) and in_any_arc(10.0, arcs)
    assert not in_any_arc(180.0, arcs), "a swell from the opposite side is OUTSIDE the wrap"


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
