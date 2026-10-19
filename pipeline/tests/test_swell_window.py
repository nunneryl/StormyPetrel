"""Synthetic-geometry checks for the SW-1 blocker fix.

These build hand-placed square islands in empty ocean so the three required
behaviours are tested independently of any real coastline data set:

  * area filter       — a sub-500 km² island stops hard-blocking;
  * local-landmass     — a small island AT the spot still blocks;
  * angular ignore     — an obstacle subtending < 5° is ignored;
  * distance-aware      — the same small island blocks near, wraps far;
  * island partial block — one long island keeps an interior shadow;
  * R1–R3               — the near-field rules (see the section at the end).

Run: python -m pipeline.tests.test_swell_window   (or pytest)
"""
from __future__ import annotations

from shapely.geometry import Point, Polygon
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
    sw._NEAREST_CACHE.clear()
    sw._POLAR_CACHE.clear()
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
    """A 194 km² (Catalina-sized) island centred 45 km due east is not a wall but a partial
    blocker: only the core of its shadow stays shut (the Fresnel edge). Side 13.928 km, near
    face 38.036 km out, half-height 6.918 km on the ground: half-angle atan(6.918/38.036) =
    10.31°, so rays 80..100 meet it. N on ray 90 = 13.835² / (0.224829 × 38.036) = 22.4 ≥ 10,
    so each ray needs only its penumbra √(L/r) inside both ends: ray 84 (φ 6°, r 38.245 km)
    needs 4.393°, offset 4 — open; ray 86 (φ 4°) needs 4.40° and has 6 — blocked. Core
    86..94. The same spot with a 600 km² island is a wall on every ray it meets."""
    dr, _, hard, blocked = _debug_cast([_square(194, 45)])
    assert not hard and sorted(blocked) == list(range(86, 95, 2))
    assert round(dr[90]["partials"][0]["fresnel_n"], 1) == 22.4
    assert sorted(b for b in dr if dr[b]["result"] == "small") == list(range(80, 101, 2))
    open_big, _ = _open([_square(600, 45)])
    assert _blocked_near(open_big, 90, halfwidth=10), "≥500 km² island must still hard-block"


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


# One long island, and the same footprint as three islands. A band at the equator from
# x 45 to 50 km east, y ±30 km (in the 111.32 km/deg units _square uses; a degree of
# latitude at the equator is 110.574 km, so the band reaches ±29.799 km). ~298 km², under
# SWELL_BLOCKER_AREA_KM2, and beyond 30 km, so every hit is partial.
_KM = 1.0 / KM_PER_DEG
_BAND = Polygon([(45 * _KM, -30 * _KM), (50 * _KM, -30 * _KM), (50 * _KM, 30 * _KM), (45 * _KM, 30 * _KM)])
_BAND_THIRDS = [Polygon([(45 * _KM, lo * _KM), (50 * _KM, lo * _KM), (50 * _KM, hi * _KM), (45 * _KM, hi * _KM)])
                for lo, hi in ((-30, -10), (-10, 10), (10, 30))]


def test_one_long_island_keeps_its_interior_blocked():
    """By hand, at the 2° step. A ray φ off due east meets the band's face (x = 45 km) at
    y = 45·tan φ, inside ±29.799 km while φ <= atan(29.799/45) = 33.51°: rays 58..122 (φ 32
    in, 34 out), at r = 45/cos φ. Seen broadside the band is ~55 km across every ray, N in
    the hundreds, so each ray needs only its penumbra √(L/r) inside both ends (the Fresnel
    edge). Ray 62 (φ 28°): r = 50.965 km, √(0.224829/50.965) = 3.806°, offset 4 — blocked.
    Ray 60 (φ 30°): offset 2 < 3.769° — open. By symmetry 118 is blocked and 120 open: core
    62..118, within 60 km, so it blocks. Open 120..60 round north; both its ends touch the
    island's core, so it is not shrunk: [120-60], span 60 + 360 - 120 + 2 = 302; optimal 270
    by symmetry. (The wrap, 16 + 8·r/100 ≈ 19.7°, kept only 78..102.)"""
    open_b, r = _open([_BAND])
    assert r["swell_window_arcs"] == [{"min": 120, "max": 60, "span": 302}], r["swell_window_arcs"]
    assert r["optimal_swell_dir"] == 270
    assert _blocked_near(open_b, 90)


def test_three_islands_side_by_side_are_judged_each_on_its_own():
    """R3: the band cut into three islands at y = ±10 km (±9.933 km on the ground). Each is
    ~20 km across, N ≈ 38 at 45 km, so each keeps its own Fresnel-edge core, and no core
    reaches across the joins:
      middle, rays 78..102 (45·tan φ <= 9.933, φ <= 12.45°): ray 82 (φ 8°, r 45.442 km) needs
        4.030° and has 4 — open; ray 84 (φ 6°) needs 4.039° and has 6 — blocked. Core 84..96.
      north, rays 58..78 (ray 78 enters it at x = 9.933/tan 12° = 46.73 km, behind the middle
        one): ray 62 (φ 28°) needs 3.806°, has 4 — blocked; ray 74 (φ 16°, r 46.813 km) needs
        3.971°, has 4 from 78 — blocked; 76 has 2 — open. Core 62..74. South: 106..118.
    Every core is within 60 km, so all three block, the northern one though it is only 14°
    wide (74 - 62 + 2). The joins 76..82 and 98..104 stay open: each lies between two island
    cores, so neither end is shrunk and they are kept as arcs, 8° each. Arcs: [76-82],
    [98-104] and [120-60] (span 302, as the single band).

    Fused into one chain, as before R3, the nearest distances of the single band give its
    one core, 62..118 (test_one_long_island_keeps_its_interior_blocked)."""
    _, r = _open(_BAND_THIRDS)
    assert r["swell_window_arcs"] == [{"min": 76, "max": 82, "span": 8},
                                      {"min": 98, "max": 104, "span": 8},
                                      {"min": 120, "max": 60, "span": 302}], r["swell_window_arcs"]
    _, _, _, blocked, partial = _debug_cast_partial(_BAND_THIRDS)
    assert sorted(blocked) == (list(range(62, 75, 2)) + list(range(84, 97, 2))
                               + list(range(106, 119, 2)))
    fused = {b: min(h.dist_km for h in hits.values()) for b, hits in partial.items()}
    fused_n = {b: max(h.fresnel_n for h in hits.values()) for b, hits in partial.items()}
    assert sorted(fused) == list(range(58, 123, 2))
    assert sorted(sw._island_shadow(fused, 2, fresnel_n=fused_n)) == list(range(62, 119, 2))


def _debug_cast_partial(polys, step=None):
    """Run the two classifier passes with debug collectors attached — mirrors the
    validate harness's --debug-blockers path. Returns (debug_rays, debug_chains, hard,
    small_blocked, partial_hits) for a spot at (0,0)."""
    step = step or sw.SWELL_RAY_STEP_DEG
    land = _index(polys)
    sw._AREA_CACHE.clear()
    sw._PREPARED_CACHE.clear()
    sw._DEPTH_CACHE.clear()
    sw._NEAREST_CACHE.clear()
    sw._POLAR_CACHE.clear()
    debug_rays: dict = {}
    hard, partial = sw._classify_bearings(0.0, 0.0, land, step, debug=debug_rays)
    debug_chains: list = []
    small_blocked = sw._islands_shadow(partial, step, debug=debug_chains)
    return debug_rays, debug_chains, hard, small_blocked, partial


def _debug_cast(polys, step=None):
    """_debug_cast_partial without the partial hits: (debug_rays, debug_chains, hard,
    small_blocked)."""
    return _debug_cast_partial(polys, step)[:4]


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

    # one long island: a BLOCKED-core chain record (wrap-distance rule) naming the island,
    # matching test_one_long_island_keeps_its_interior_blocked.
    _, dcc, _, sb_chain = _debug_cast([_BAND])
    assert [(c["lo"], c["hi"], c["decision"], c["core"], c["edge_rays"]) for c in dcc] == [
        (58, 122, "blocked_core", [62, 118], 33)]
    assert sb_chain == set(range(62, 119, 2))
    # the same footprint as three islands: three records, one per island, each its own core
    _, dcc3, _, sb3 = _debug_cast(_BAND_THIRDS)
    assert sorted((c["lo"], c["hi"], c["decision"], c["core"]) for c in dcc3) == [
        (58, 78, "blocked_core", [62, 74]), (78, 102, "blocked_core", [84, 96]),
        (102, 122, "blocked_core", [106, 118])]
    assert len({c["island"] for c in dcc3}) == 3

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
    sw._NEAREST_CACHE.clear()
    sw._POLAR_CACHE.clear()
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
    sw._NEAREST_CACHE.clear()
    sw._POLAR_CACHE.clear()
    dr = {}
    lat, lng = _OFFSHORE
    sw._classify_bearings(lat, lng, _index([_ISLAND]), 4, debug=dr)
    assert (dr[180]["result"], dr[180]["rule"], dr[180]["own"]) == ("hard", "local_coast_30km", True)
    assert round(dr[180]["dist_km"], 3) == 2.0, dr[180]["dist_km"]


def test_a_ray_across_a_peninsula_is_blocked_even_when_it_re_enters_the_same_land_far_away():
    """THE GAP THE FIRST FIX LEFT. One C-shaped polygon at the equator (~7,300 km², a solid
    landmass): a peninsula from lng -0.02 to 0.27 (lat -0.5 to 0.8), a band along its top
    (lat 0.7-0.8) running east to a far arm at lng 1.5-1.6 (lat -0.1 to 0.8). The spot at
    (0, 0) sits 0.02 deg x 111.32 = 2.226 km inside the peninsula's west coast.

    Due east, the ray leaves the peninsula at 0.27 deg = 30.1 km, crosses open water, and
    re-enters the SAME polygon at the far arm, 1.5 deg = 167 km out. The first fix checked
    the own-land allowance only for a ray that never came back, so this one was graded a
    distant hit beyond SWELL_MAINLAND_SOLID_KM ('small', 167 km) and the rays across the
    peninsula to the north-east wrapped open as [45-87]. Now the crossing is checked first:
    30.1 km > 2.226 + 2 km, blocked at the ray's start by the local-coast rule.

    The whole window, by hand: every east-half ray crosses 30-89 km of the peninsula and is
    blocked. A west-half ray phi degrees off due west leaves after 2.226 / cos(phi) km and is
    open while that is within 4.226 km: cos(phi) >= 0.5267, phi <= 58.2 deg, i.e. 212..328 at
    the 2-degree step (cos 58 = 0.530 passes, cos 60 = 0.5 fails); shrunk 5 deg each end:
    [217-323], span 323-217+2 = 108, optimal 270."""
    pen = Polygon([(-0.02, -0.5), (0.27, -0.5), (0.27, 0.7), (1.5, 0.7), (1.5, -0.1),
                   (1.6, -0.1), (1.6, 0.8), (-0.02, 0.8)])
    dr, _, hard, _ = _debug_cast([pen])
    assert 90 in hard
    assert (dr[90]["result"], dr[90]["rule"], dr[90]["own"]) == ("hard", "local_coast_30km", True), dr[90]
    assert round(dr[90]["dist_km"], 3) == 2.0, dr[90]["dist_km"]
    _, r = _open([pen])
    assert r["swell_window_arcs"] == [{"min": 217, "max": 323, "span": 108}], r["swell_window_arcs"]
    assert r["optimal_swell_dir"] == 270, r["optimal_swell_dir"]
    for crossing in (45, 60, 87, 90):
        assert not in_any_arc(crossing, r["swell_window_arcs"]), crossing


def test_a_ray_leaving_within_the_allowance_still_blocks_on_the_same_land_across_a_bay():
    """The Fort Point case (Marin across the Golden Gate, 9.8 km): one polygon wrapped round
    a bay, at the equator. The west land runs to lng 0.02695 (3.0 km east of the spot) and
    lat 0.01348 (1.5 km north of it); the east land starts at lng 0.11678 (13.0 km); a band
    along the south (lat -0.5 to -0.4) joins them. The spot at (0, 0) is 1.5 km inside the
    north coast, its nearest — so the allowance is 1.5 + 2 = 3.5 km. Due east the ray starts
    2 km out still on land, leaves at 3.0 km (within the allowance), crosses 10 km of bay and
    re-enters the SAME polygon at 13.0 km: a coast across a bay, hard-blocked there by the
    local-coast rule and flagged own — not opened as the spot's own coast."""
    bay = Polygon([(-1.0, -0.5), (1.0, -0.5), (1.0, 0.5), (0.11678, 0.5), (0.11678, -0.4),
                   (0.02695, -0.4), (0.02695, 0.01348), (-1.0, 0.01348)])
    dr, _, hard, _ = _debug_cast([bay])
    assert 90 in hard
    assert (dr[90]["result"], dr[90]["rule"], dr[90]["own"]) == ("hard", "local_coast_30km", True), dr[90]
    assert round(dr[90]["dist_km"], 1) == 13.0, dr[90]["dist_km"]


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
    # to a partial blocker. At 400 km (near face 387.75 km) it subtends atan(12.165/387.75)
    # = 1.80° each side: ray 90 alone, under SWELL_MIN_SHADOW_DEG, so it opens.
    near, _ = _open([_square(600, 45)])          # ~33 km — inside the solid range
    assert _blocked_near(near, 90), "≥500 km² wall within SWELL_MAINLAND_SOLID_KM still hard-blocks"
    _, _, hard, blocked = _debug_cast([_square(600, 400)])
    assert not hard and not blocked, "the same wall beyond SWELL_MAINLAND_SOLID_KM is no wall"


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
    distant = _square(600, 400, 180)                                                # ≥500 km², ~388 km S -> relaxed
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
    sw._NEAREST_CACHE.clear()
    sw._POLAR_CACHE.clear()
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


# --------------------------------------------------------------------------- #
# R1–R3: the near-field island rules                                           #
# --------------------------------------------------------------------------- #
# Hand arithmetic shared by the tests below (calculator, not the code under test):
#   L at 12 s = 9.81 × 144 / (2π) = 224.8286 m
#   on the ground at the equator a degree of longitude is 111.3195 km and of latitude
#   110.5743 km, so a polygon side of s km in _square's 111.32 km/deg units spans
#   s × 111.3195/111.32 east-west and s × 110.5743/111.32 = 0.993301·s north-south.
#   For a ray due east the width across it is the north-south extent.
# Old arcs are the rules before R1–R3: any land within 30 km a wall, and only the nearest
# partial hit on a ray, fused into one chain with its neighbours.
_FULLY_OPEN = [{"min": 0, "max": 358, "span": 360}]
_ONE_RAY_SHUT = [{"min": 97, "max": 83, "span": 348}]   # ray 90 shut: 92..88 open, shrunk 5°


def test_fresnel_number_by_hand():
    """1 km wide at 20 km: 1000² / (224.8286 × 20000) = 0.222392. At 8 s, L = 9.81 × 64 /
    (2π) = 99.9238 m: 1000² / (99.9238 × 20000) = 0.500381. 3 km at 20 km: 9e6 / 4,496,573
    = 2.001524."""
    assert round(sw.fresnel_number(1.0, 20.0), 6) == 0.222392
    assert round(sw.fresnel_number(1.0, 20.0, period_s=8.0), 6) == 0.500381
    assert round(sw.fresnel_number(3.0, 20.0), 6) == 2.001524


def test_fresnel_verdict_boundaries():
    assert [sw.fresnel_verdict(n) for n in (0.999999, 1.0, 9.999999, 10.0, 62.7)] == [
        "pass", "partial", "partial", "wall", "wall"]


def test_r2_a_tiny_islet_at_20_km_opens():
    """A 1 km² islet centred 20 km due east. Its near face is 19.5 km out (19.49991 km on the
    ground), its nearest point too, so it is not the own coast (> 5 km). Width across ray 90:
    0.993301 km. N = 993.301² / (224.8286 × 19499.91) = 0.225049 < 1: the ray passes it.
    It is met by ray 90 alone (ray 88 is at y = 19.5·tan 2° = 0.681 km > 0.497 at its face).
    Before R2 it walled ray 90: [97-83], span 348. Now nothing blocks: [0-358], span 360."""
    dr, _, hard, _ = _debug_cast([_square(1, 20, 90)])
    assert not hard
    assert dr[90]["result"] == "open"
    (islet,) = dr[90]["passed"]
    assert round(islet["fresnel_n"], 3) == 0.225 and round(islet["dist_km"], 2) == 19.50
    assert [b for b in dr if dr[b].get("passed")] == [90]
    _, r = _open([_square(1, 20, 90)])
    assert r["swell_window_arcs"] == _FULLY_OPEN, r["swell_window_arcs"]


def test_r2_a_large_island_at_12_km_stays_a_wall():
    """A 100 km² island centred 12 km due east: side 10 km, near face 7 km out (6.99997 km),
    nearest point 7 km, so not the own coast, and under 500 km², so R1 does not wall it.
    Ray 90: width 9.93301 km, N = 9933.01² / (224.8286 × 6999.97) = 62.69 ≥ 10: a wall.
    Half-angle of the near face, atan(4.9665/7.0) = 35.36°: rays 56..124 (34° in, 36° out);
    every one of them meets at least this width within 12.2 km (N > 50), all walls. Open
    126..54 round north, shrunk: [131-49], span 49 + 360 - 131 + 2 = 280, optimal 270."""
    dr, _, hard, _ = _debug_cast([_square(100, 12, 90)])
    assert sorted(hard) == list(range(56, 125, 2))
    assert (dr[90]["rule"], dr[90]["own"], round(dr[90]["fresnel_n"], 1)) == ("fresnel_wall", False, 62.7)
    _, r = _open([_square(100, 12, 90)])
    assert r["swell_window_arcs"] == [{"min": 131, "max": 49, "span": 280}], r["swell_window_arcs"]
    assert r["optimal_swell_dir"] == 270


# A thin strip lying along ray 90, 0.2 km wide (0.198660 km on the ground).
def _strip(x0_km, x1_km, width_km=0.2):
    h = width_km / 2.0 * _KM
    return Polygon([(x0_km * _KM, -h), (x1_km * _KM, -h), (x1_km * _KM, h), (x0_km * _KM, h)])


def test_r1_the_own_coast_is_a_wall_however_thin():
    """The strip from 4 to 14 km: nearest point 4 km, within SWELL_OWN_COAST_KM, so R1 walls
    it although its Fresnel number is tiny (198.66² / (224.8286 × 4000) = 0.0439). Only ray 90
    meets it (ray 88 is at y = 4·tan 2° = 0.140 km > 0.0993 at x = 4 and moving away):
    [97-83], span 348."""
    dr, _, hard, _ = _debug_cast([_strip(4, 14)])
    assert sorted(hard) == [90]
    assert (dr[90]["rule"], dr[90]["own"]) == ("local_coast_30km", True)
    assert "fresnel_n" not in dr[90]
    _, r = _open([_strip(4, 14)])
    assert r["swell_window_arcs"] == _ONE_RAY_SHUT, r["swell_window_arcs"]


def test_r1_the_same_strip_6_km_out_is_not_the_own_coast():
    """Moved to 6–16 km its nearest point is 6 km, past SWELL_OWN_COAST_KM, so R2 judges it:
    N = 198.66² / (224.8286 × 6000) = 0.0293 < 1, passed. [0-358], span 360."""
    dr, _, hard, _ = _debug_cast([_strip(6, 16)])
    assert not hard
    assert round(dr[90]["passed"][0]["fresnel_n"], 4) == 0.0293
    _, r = _open([_strip(6, 16)])
    assert r["swell_window_arcs"] == _FULLY_OPEN, r["swell_window_arcs"]


def test_r1_a_landmass_of_500_km2_within_30_km_is_a_wall_end_on():
    """A strip 0.5 km wide from 10 to 1110 km along ray 90: 0.496651 × 1100 × 111.3195/111.32
    = 546.3 km² as a rectangle (a little more measured on geodesic edges, which bow out),
    at least SWELL_BLOCKER_AREA_KM2, so R1 walls it at 10 km although end-on its N is
    496.651² / (224.8286 × 10000) = 0.1097. Cut to 10–910 km (447.0 km² as a rectangle) it
    is an island, N the same, and the ray passes."""
    dr, _, hard, _ = _debug_cast([_strip(10, 1110, 0.5)])
    assert sorted(hard) == [90] and dr[90]["rule"] == "local_coast_30km" and not dr[90]["own"]
    assert 546 <= dr[90]["area_km2"] < 550
    dr2, _, hard2, _ = _debug_cast([_strip(10, 910, 0.5)])
    assert 447 <= dr2[90]["passed"][0]["area_km2"] < 450
    assert not hard2 and round(dr2[90]["passed"][0]["fresnel_n"], 3) == 0.110


# A broad far coast: x 1500–1600 km east, y ±1000 km in _square's units. Beyond
# SWELL_MAINLAND_SOLID_KM, so a partial blocker whatever its size; its wrap at 1500 km is
# 16 + 8 × 15 = 136° per edge, more than half of any run it can make, so alone it opens.
_FAR_COAST = Polygon([(1500 * _KM, -1000 * _KM), (1600 * _KM, -1000 * _KM),
                      (1600 * _KM, 1000 * _KM), (1500 * _KM, 1000 * _KM)])


def test_r3_a_near_islet_fused_with_a_far_coast_is_judged_separately():
    """A 16 km² islet centred 25 km due east in front of _FAR_COAST. Islet: side 4 km, near
    face 23 km out (22.99989 km), width across ray 90 3.973204 km, N = 3973.204² /
    (224.8286 × 22999.89) = 3.053: partial (R2). Rays 86..94 meet it (half-angle
    atan(1.98660/23.0) = 4.94°; ray 84 is at y = 23·tan 6° = 2.417 km at its face, outside).

    Every one of those rays meets the far coast too, and R3 keeps both hits. Judged alone the
    islet's run is 86..94, 10° wide, and its wrap at 23 km is 16 + 1.84 = 17.84°: open. The
    coast alone opens (wrap 136°+). So the whole compass is open: [0-358], span 360.

    Fused, as before R3, the islet lends the chain its 23 km distance: with the coast's run
    reaching at least 68..112 (it does), each islet ray 86..94 sits ≥ 28° inside both ends,
    past 17.84°, and stays blocked — the old core, [101-79], span 340."""
    polys = [_square(16, 25, 90), _FAR_COAST]
    _, _, hard, small_blocked, partial = _debug_cast_partial(polys)
    assert not hard and not small_blocked
    assert [b for b in partial if len(partial[b]) == 2] == [86, 88, 90, 92, 94]
    dists = sorted(h.dist_km for h in partial[90].values())
    assert round(dists[0], 2) == 23.00 and dists[1] > 1400
    assert 68 in partial and 112 in partial
    _, r = _open(polys)
    assert r["swell_window_arcs"] == _FULLY_OPEN, r["swell_window_arcs"]
    fused = {b: min(h.dist_km for h in hits.values()) for b, hits in partial.items()}
    assert sorted(sw._island_shadow(fused, 2)) == [86, 88, 90, 92, 94]


def test_r3_a_near_islet_that_would_block_alone_still_does_behind_a_far_coast():
    """The long band of test_one_long_island_keeps_its_interior_blocked, with _FAR_COAST
    behind it: R3 judges the band on its own run, so its core 62..118 stands, exactly as
    without the coast."""
    _, _, _, small_blocked, partial = _debug_cast_partial([_BAND, _FAR_COAST])
    assert small_blocked == set(range(62, 119, 2))
    assert all(len(partial[b]) == 2 for b in range(58, 123, 2))


def test_r3_never_blocks_a_ray_the_fused_chain_would_not():
    """Per island against one chain on the nearest hit, over random partial hits judged by
    the wrap (no Fresnel numbers): an island's run lies inside the fused run and its distance
    on a ray is never nearer than the nearest, so its wrap is never smaller. Any ray it
    blocks, the fused chain blocks."""
    import random
    rng = random.Random(20261019)
    for _ in range(300):
        partial: dict = {}
        for island in range(rng.randint(1, 4)):
            lo = rng.randrange(0, 360, 2)
            dist = rng.choice([8.0, 25.0, 40.0, 90.0, 400.0, 2500.0])
            for b in range(lo, lo + rng.randrange(2, 140, 2), 2):
                partial.setdefault(b % 360, {})[island] = sw.IslandHit(dist * rng.uniform(1.0, 1.3))
        fused = {b: min(x.dist_km for x in h.values()) for b, h in partial.items()}
        assert sw._islands_shadow(partial, 2) <= sw._island_shadow(fused, 2)


# --------------------------------------------------------------------------- #
# The Fresnel edge beyond 30 km                                                #
# --------------------------------------------------------------------------- #
# A Kaua'i-sized island (1,453 km²) centred 155 km due east: side 38.118 km, near face
# 135.940 km out, half-height 18.931 km on the ground.
_KAUAI = _square(1453, 155, 90)


def test_fresnel_edge_by_hand():
    """√(L/r) in degrees, L = 0.224829 km: at 33 km √(0.0068130) = 0.082541 rad = 4.7292°;
    at 136 km √(0.0016532) = 0.040659 rad = 2.3296°; at 8 s (L = 0.099924 km), 136 km:
    √(0.00073473) = 0.027106 rad = 1.5531°."""
    assert round(sw.fresnel_edge_deg(33.0), 4) == 4.7292
    assert round(sw.fresnel_edge_deg(136.0), 4) == 2.3296
    assert round(sw.fresnel_edge_deg(136.0, period_s=8.0), 4) == 1.5531


def test_a_narrow_core_beyond_60_km_opens():
    """Past SWELL_MAINLAND_SOLID_KM a 1,453 km² island is a partial blocker. Half-angle
    atan(18.931/135.940) = 7.93°: rays 84..96. N on ray 90 = 37.865² / (0.224829 × 135.940)
    = 46.9 ≥ 10, so the Fresnel edge: ray 88 (φ 2°, r 136.023 km) needs 2.329° and has 4 —
    in the core; ray 86 has 2 — not. Core 88..92: 92 - 88 + 2 = 6° wide, under 16°, every
    ray of it 136.0 km out, beyond 60 km — it does not block. The compass is open."""
    dr, dc, hard, blocked = _debug_cast([_KAUAI])
    assert not hard and not blocked
    assert round(dr[90]["partials"][0]["fresnel_n"], 1) == 46.9
    assert [(c["lo"], c["hi"], c["decision"], c["core"], c["edge_rays"]) for c in dc] == [
        (84, 96, "open_narrow_core", [88, 92], 7)]
    _, r = _open([_KAUAI])
    assert r["swell_window_arcs"] == _FULLY_OPEN, r["swell_window_arcs"]


def test_above_the_island_cap_the_wrap_still_applies():
    """Cuba, Hispaniola and the continents keep the wrap: with the cap lowered below the
    island's 1,453 km² it records no Fresnel number and opens (wrap 26.9° > its 12° run)."""
    saved = sw.SWELL_FRESNEL_ISLAND_MAX_KM2
    try:
        sw.SWELL_FRESNEL_ISLAND_MAX_KM2 = 1000.0
        dr, dc, hard, blocked = _debug_cast([_KAUAI])
        _, r = _open([_KAUAI])
    finally:
        sw.SWELL_FRESNEL_ISLAND_MAX_KM2 = saved
    assert not hard and not blocked
    assert dr[90]["partials"][0]["fresnel_n"] is None and dc[0]["edge_rays"] == 0
    assert r["swell_window_arcs"] == _FULLY_OPEN


def test_beyond_30_km_an_island_with_n_below_10_keeps_the_wrap():
    """108 km² centred 80 km due east: side 10.392 km, near face 74.804 km, width across
    ray 90 10.3227 km: N = 10.3227² / (0.224829 × 74.804) = 6.34 < 10, so the wrap; its run
    is 8° wide (half-angle atan(5.161/74.804) = 3.95°: rays 88..92), it opens."""
    dr, dc, hard, blocked = _debug_cast([_square(108, 80, 90)])
    assert round(dr[90]["partials"][0]["fresnel_n"], 2) == 6.34
    assert not hard and not blocked and dc[0]["edge_rays"] == 0


# --------------------------------------------------------------------------- #
# Rays across the antimeridian                                                 #
# --------------------------------------------------------------------------- #
# A spot 0.1° west of 180°, a 600 km² island centred 45 km due east of it (so wholly east of
# 180°: lng -179.806 to -179.586), and a decoy of the same size on the prime meridian,
# 20,000 km away. The near face is 45 - 12.247 = 32.753 km out (32.752 km on the ground):
# beyond 30 km, within SWELL_MAINLAND_SOLID_KM, ≥ 500 km² — a wall. Half-angle
# atan(12.165/32.752) = 20.38°: rays 70..110. Open 112..68 round north, shrunk: [117-63],
# span 63 + 360 - 117 + 2 = 308, optimal 270.
#
# Before the split, a ray crossing 180° jumped from +179.9 to -179.9 and its chord ran the
# width of the map: every eastward ray "hit" the decoy, and ray 90 met the island's FAR side
# first (65 km) from the wrong end of that chord.
def _antimeridian_land(east):
    """The island 45 km east of a spot at lng 179.9 (centre 179.9 + 45/111.32 - 360 =
    -179.696), or 45 km west of one at -179.9 (centre +179.696), plus the decoy."""
    h = 600 ** 0.5 / 2 / KM_PER_DEG
    c = 179.9 + 45 / KM_PER_DEG - 360
    c = c if east else -c
    island = Polygon([(c - h, -h), (c + h, -h), (c + h, h), (c - h, h)])
    return _index([island, _square(600, 0.0, 90)])


def _cast_at(lng, land):
    sw._AREA_CACHE.clear(); sw._PREPARED_CACHE.clear(); sw._DEPTH_CACHE.clear()
    sw._NEAREST_CACHE.clear(); sw._POLAR_CACHE.clear()
    dr: dict = {}
    hard, partial = sw._classify_bearings(0.0, lng, land, 2, debug=dr)
    return dr, hard, partial, sw.compute_swell_window({"name": "t", "lat": 0.0, "lng": lng}, land=land)


def test_a_ray_keeps_its_longitudes_continuous_across_180():
    xs = [x for x, _ in sw._ray_linestring(0.0, 179.9, 90.0).coords]
    assert max(xs) > 180.0 and max(abs(b - a) for a, b in zip(xs, xs[1:])) < 1.0
    pieces = sw._ray_pieces(sw._ray_linestring(0.0, 179.9, 90.0), Point(179.9, 0.0))
    assert [round(p.x, 1) for _, p in pieces] == [179.9, -180.1]


def test_an_island_across_180_to_the_east():
    dr, hard, partial, r = _cast_at(179.9, _antimeridian_land(east=True))
    assert sorted(hard) == list(range(70, 111, 2)) and not partial
    assert (dr[90]["rule"], round(dr[90]["dist_km"], 2)) == ("mainland_solid", 32.75)
    assert all(rec["result"] != "small" for rec in dr.values()), "no hit on the decoy"
    assert r["swell_window_arcs"] == [{"min": 117, "max": 63, "span": 308}], r["swell_window_arcs"]
    assert r["optimal_swell_dir"] == 270


def test_an_island_across_180_to_the_west():
    """The mirror image: spot at -179.9, island 45 km due west at +179.586..+179.806. Rays
    250..290 walled, [297-243], span 243 + 360 - 297 + 2 = 308, optimal 90."""
    dr, hard, partial, r = _cast_at(-179.9, _antimeridian_land(east=False))
    assert sorted(hard) == list(range(250, 291, 2)) and not partial
    assert round(dr[270]["dist_km"], 2) == 32.75
    assert r["swell_window_arcs"] == [{"min": 297, "max": 243, "span": 308}], r["swell_window_arcs"]
    assert r["optimal_swell_dir"] == 90


def test_the_fresnel_edge_is_only_for_n_of_10_or_more():
    """A strip 1 km deep (x 60–61 km) and 10.330 km across (y ±5.2 in _square's units): on
    ray 90 N = 10.330² / (0.224829 × 59.9997) = 7.91 < 10. Half-angle atan(5.165/60.0) =
    4.92°: rays 86..94. Ray 90 is 4° from each end — past its penumbra √(L/r) = 3.51°, short
    of the wrap 16 + 8 × 0.6 = 20.8°. With N below 10 it takes the wrap and opens."""
    strip = Polygon([(60 * _KM, -5.2 * _KM), (61 * _KM, -5.2 * _KM),
                     (61 * _KM, 5.2 * _KM), (60 * _KM, 5.2 * _KM)])
    dr, dc, hard, blocked = _debug_cast([strip])
    assert round(dr[90]["partials"][0]["fresnel_n"], 2) == 7.91
    assert sorted(b for b in dr if dr[b]["result"] == "small") == [86, 88, 90, 92, 94]
    assert not hard and not blocked and dc[0]["edge_rays"] == 0


def test_the_blocker_dump_prints_each_rays_distance_and_n(capsys):
    """The Kaua'i-sized island at the 4° step: rays 84, 88, 92, 96 (φ 6°, 2°, 2°, 6°), at
    135.940/cos φ = 136.7 and 136.0 km. Width across a ray φ off the square's axis is
    38.118 × (0.993301·cos φ + 0.999995·sin φ): 41.640 km at 6°, 39.170 km at 2°, so
    N = 41.640² / (0.224829 × 136.689) = 56.4 and 39.170² / (0.224829 × 136.023) = 50.2."""
    from pipeline import sw1_raycast as R
    land = _index([_KAUAI])
    saved = R.load_land_index
    R.load_land_index = lambda: land
    try:
        R.run_validate(debug_blockers=True, spots=[("T", 0.0, 0.0, 90, None, None, "")])
    finally:
        R.load_land_index = saved
    out = capsys.readouterr().out
    assert ("per ray (bearing dist N): 84 136.7km N=56.4 · 88 136.0km N=50.2 · "
            "92 136.0km N=50.2 · 96 136.7km N=56.4") in out
    assert "→ OPEN narrow core 88–92" in out and "Fresnel edge on 4 of the rays" in out
    assert "core 88–92° is 8° wide (< 16°), all beyond 60 km (nearest 136 km): opens" in out


# --------------------------------------------------------------------------- #
# A narrow, distant core does not block                                        #
# --------------------------------------------------------------------------- #
# _island_shadow on one island's rays at the 4° step, every ray with N = 50 (the Fresnel
# edge). √(L/r): at 61 km √(0.224829/61) = 0.060710 rad = 3.478°; at 59 km 0.061730 rad =
# 3.537°; at 60 km 3.507°. Each is under 4, so a run lo..hi has core lo+4 .. hi-4, and the
# core's width is its sector, (hi - 4) - (lo + 4) + 4.

def _core(run_hi, dists):
    rays = list(range(100, run_hi + 1, 4))
    d = dict(zip(rays, dists)) if isinstance(dists, list) else {b: dists for b in rays}
    return sorted(sw._island_shadow(d, 4, fresnel_n={b: 50.0 for b in rays}))


def test_a_12_degree_core_at_61_km_opens():
    """Run 100..116: core 104..112, 12° wide, all at 61 km — narrower than 16°, beyond 60 km."""
    assert _core(116, 61.0) == []


def test_a_12_degree_core_at_59_km_blocks():
    """The same core at 59 km is not beyond 60 km."""
    assert _core(116, 59.0) == [104, 108, 112]


def test_a_12_degree_core_at_exactly_60_km_blocks():
    """"Beyond 60 km" is strict."""
    assert _core(116, 60.0) == [104, 108, 112]


def test_a_16_degree_core_at_61_km_blocks():
    """Run 100..120: core 104..116, 16° wide — not narrower than 16°."""
    assert _core(120, 61.0) == [104, 108, 112, 116]


def test_a_narrow_core_with_one_ray_within_60_km_blocks():
    """Core 104..112 at 61, 59, 61 km: not every ray of it is beyond 60 km."""
    assert _core(116, [61.0, 61.0, 59.0, 61.0, 61.0]) == [104, 108, 112]


# --------------------------------------------------------------------------- #
# The 5° shrink only at a hard wall                                            #
# --------------------------------------------------------------------------- #
# _merge_open_arcs at the 4° step. Every ray not listed open is blocked; those in `cores`
# by an island core, the rest by a hard wall.

def test_a_run_between_two_hard_walls_is_shrunk_at_both_ends():
    """Huntington's 140..200 when both neighbours are walls: [145-195], span 195-145+4 = 54."""
    assert sw._merge_open_arcs(list(range(140, 201, 4)), 4) == [{"min": 145, "max": 195, "span": 54}]


def test_a_run_between_two_island_cores_is_kept_whole():
    """216..224 between cores at 212 and 228: [216-224], span 12 — the shrink used to drop it."""
    assert sw._merge_open_arcs([216, 220, 224], 4, {212, 228}) == [
        {"min": 216, "max": 224, "span": 12}]
    assert sw._merge_open_arcs([216, 220, 224], 4) == []


def test_a_single_ray_between_two_island_cores_is_kept():
    """Ray 216 alone between cores at 212 and 220: [216-216], span 4 (read as 214-218)."""
    assert sw._merge_open_arcs([216], 4, {212, 220}) == [{"min": 216, "max": 216, "span": 4}]


def test_a_run_with_one_core_end_is_shrunk_only_at_the_wall():
    """248..280 between Catalina's core at 244 and the coast at 284: [248-275], span 31."""
    assert sw._merge_open_arcs(list(range(248, 281, 4)), 4, {244}) == [
        {"min": 248, "max": 275, "span": 31}]
    assert sw._merge_open_arcs(list(range(248, 281, 4)), 4, {284}) == [
        {"min": 253, "max": 280, "span": 31}]


def test_a_short_run_shrunk_at_its_wall_end_is_still_dropped():
    """296..300 between a core at 292 and a wall at 304: 296 .. 300-5 = 295, 295-296+4 = 3,
    one step or less — dropped (Big Beach Makena's gap between Lāna'i and O'ahu)."""
    assert sw._merge_open_arcs([296, 300], 4, {292}) == []


def test_the_shrink_rule_across_north():
    """358..2 at the 2° step between cores at 356 and 4: kept whole, [358-2], span 6."""
    assert sw._merge_open_arcs([358, 0, 2], 2, {356, 4}) == [{"min": 358, "max": 2, "span": 6}]


def test_a_run_shrunk_to_one_step_at_its_wall_end_is_dropped():
    """At a 1° step the drop rule decides on its own: 100..105 with a core at 99 and a wall
    at 106 shrinks to 100 .. 105-5 = 100, one step wide — dropped. 100..106 keeps [100-101]."""
    assert sw._merge_open_arcs(list(range(100, 106)), 1, {99}) == []
    assert sw._merge_open_arcs(list(range(100, 107)), 1, {99}) == [{"min": 100, "max": 101, "span": 2}]


def test_validate_reads_the_island_cores_too(capsys):
    """The long band in the validate table, at the 4° step: rays 60..120 meet it (φ 30 in,
    34 out); ray 64 (φ 26°, r = 45/cos 26° = 50.067 km) needs √(0.224829/50.067) = 3.840°
    and has 4 — core 64..116, within 60 km. Open 120..60 touches the core at both ends:
    unshrunk, [120-60], span 60 + 360 - 120 + 4 = 304."""
    from pipeline import sw1_raycast as R
    land = _index([_BAND])
    saved = R.load_land_index
    R.load_land_index = lambda: land
    try:
        R.run_validate(spots=[("T", 0.0, 0.0, 90, None, None, "")])
    finally:
        R.load_land_index = saved
    assert "[120–60(304)]" in capsys.readouterr().out
