"""The validate run's regression checks (pipeline/sw1_raycast.py REGRESSION_CHECKS).

Real shadows the island rules must keep: the cores of Kaho'olawe at Big Beach Makena,
Lāna'i at Lahaina, Santa Cruz Island at Pitas Point, Mondos, Ventura Point, Emma Wood,
Rincon and Leadbetter, and Kaua'i at Pipeline; Diamond Head at Canoes; Second Beach RI
bounded. The checks read the validate run's own debug records; these tests feed them by
hand.
"""
from __future__ import annotations

import json
import re

from pipeline import sw1_raycast as R
from pipeline.config import DEFAULT_ENRICHED_OUTPUT

KAHO = next(c for c in R.REGRESSION_CHECKS if c["slug"] == "big-beach-makena")
SANTA_CRUZ = next(c for c in R.REGRESSION_CHECKS if c["slug"] == "pitas-point")
KAUAI = next(c for c in R.REGRESSION_CHECKS if c["slug"] == "banzai-pipeline")
HUNTINGTON = next(c for c in R.REGRESSION_CHECKS if c["slug"] == "huntington-beach-pier")
CANOES = next(c for c in R.REGRESSION_CHECKS if c["slug"] == "canoes-waikiki")


def _kaho(island=7, **kw):
    return {"island": island, "area_km2": 123.0, "centroid": (20.548, -156.605),
            "dist_km": 12.0, **kw}


def _rays():
    """Kaho'olawe walls rays 220..236 (R2) and is a partial hit on 240; ray 244 passes an
    islet. Its run is 220..240, so its core (≥ 8° inside both ends) is 228..232."""
    rays = {b: {"result": "hard", "rule": "fresnel_wall", **_kaho(dist_km=11.4, fresnel_n=42.0)}
            for b in range(220, 237, 4)}
    rays[240] = {"result": "small", "dist_km": 12.0, "area_km2": 123.0,
                 "centroid": (20.548, -156.605), "partials": [_kaho()], "passed": []}
    rays[244] = {"result": "open", "passed": [{"island": 9, "area_km2": 0.4,
                                               "centroid": (20.64, -156.50), "dist_km": 6.0,
                                               "fresnel_n": 0.2}]}
    return rays


def test_every_check_names_a_roster_spot():
    roster = json.loads(DEFAULT_ENRICHED_OUTPUT.read_text())
    slugs = {re.sub(r"[^a-z0-9]+", "-", s["name"].lower()).strip("-") for s in roster}
    assert [c["slug"] for c in R.REGRESSION_CHECKS] == [
        "big-beach-makena", "lahaina-breakwall", "pitas-point", "mondos", "ventura-point",
        "emma-wood-state-beach", "rincon", "leadbetter-beach", "banzai-pipeline",
        "huntington-beach-pier", "canoes-waikiki", "second-beach-newport"]
    assert [c.get("core_deg") for c in R.REGRESSION_CHECKS if c["kind"] == "island"] == [8] * 8 + [None]
    assert KAUAI["expect"] == "open"
    assert (HUNTINGTON["blocked"], HUNTINGTON["open"]) == ((228, 244), (196, 224))
    assert {c["slug"] for c in R.REGRESSION_CHECKS} <= slugs


def test_an_island_whose_core_stays_blocked_passes_though_its_ends_open():
    status, detail = R.regression_verdict(KAHO, 20.63, -156.45, _rays(), [], {220, 240, 244}, [], 4)
    assert status == "PASS", detail
    assert ("rays 220-240 (fresnel_wall, small); core (≥8° in) 228-232, open in core —, "
            "open overall 220,240") in detail


def test_an_island_with_an_open_core_ray_fails():
    status, detail = R.regression_verdict(KAHO, 20.63, -156.45, _rays(), [], {232, 244}, [], 4)
    assert status == "FAIL" and "open in core 232" in detail


def test_an_island_too_short_to_have_a_core_fails():
    rays = {b: r for b, r in _rays().items() if b in (236, 240, 244)}
    status, detail = R.regression_verdict(KAHO, 20.63, -156.45, rays, [], set(), [], 4)
    assert status == "FAIL" and detail.endswith("no ray far enough inside the run to test")


def test_the_core_wraps_through_north():
    """A run 340..20 (unwrapped 340..380): 8° in from both ends, 348..12."""
    assert R._core_rays([340, 344, 348, 352, 356, 0, 4, 8, 12, 16, 20], 4, 8) == [
        0, 4, 8, 12, 348, 352, 356]


def test_an_island_not_found_fails():
    rays = {b: {"result": "open", "passed": []} for b in range(0, 360, 4)}
    status, detail = R.regression_verdict(KAHO, 20.63, -156.45, rays, [], set(range(0, 360, 4)),
                                          [], 4)
    assert status == "FAIL" and "cannot verify" in detail


def test_a_different_island_at_the_same_place_does_not_match():
    """Area outside 80–200 km² (Maui's 1,900 km² own coast) is not Kaho'olawe."""
    rays = {232: {"result": "hard", "rule": "local_coast_30km", "island": 1, "area_km2": 1900.0,
                  "centroid": (20.55, -156.6), "dist_km": 2.0}}
    assert R.regression_verdict(KAHO, 20.63, -156.45, rays, [], set(), [], 4)[0] == "FAIL"


def test_santa_cruz_core_with_the_fresnel_edge():
    """Pitas Point's run 204..240 (run 21): the core 8° in from both ends is 212..232. With
    the Fresnel edge 208 and 236 may also be blocked; only the core is checked."""
    sc = {"island": 3, "area_km2": 257.0, "centroid": (34.015, -119.747)}
    rays = {b: {"result": "small", "partials": [dict(sc, dist_km=33.0 + b - 204, fresnel_n=80.0)],
                "passed": []} for b in range(204, 241, 4)}
    chains = [{"lo": 204, "hi": 240, "width": 40, "dmin_km": 33.0, "wrap_deg": 18.6,
               "edge_rays": 10, "decision": "blocked_core", "core": [208, 236], "island": 3}]
    status, detail = R.regression_verdict(SANTA_CRUZ, 34.32, -119.39, rays, chains,
                                          {204, 240}, [], 4)
    assert status == "PASS", detail
    assert detail.endswith("; own run 204–240 (40°), Fresnel edge on 10 ray(s) → blocked_core, "
                           "core 208–236")
    status, detail = R.regression_verdict(SANTA_CRUZ, 34.32, -119.39, rays, chains,
                                          set(range(204, 241, 4)), [], 4)
    assert status == "FAIL" and "open in core 212-232" in detail


def test_kauai_opens_at_pipeline():
    """Kaua'i at Pipeline, rays 280..292 at 136-143 km: every one must lie inside an arc.
    [257-31] (padded 255-33) holds them; [257-275, 297-31] leaves 280-292 outside."""
    k = {"island": 5, "area_km2": 1453.0, "centroid": (22.06, -159.526)}
    rays = {b: {"result": "small", "partials": [dict(k, dist_km=136.0, fresnel_n=47.0)],
                "passed": []} for b in range(280, 293, 4)}
    opened = [{"min": 257, "max": 31, "span": 138}]
    status, detail = R.regression_verdict(KAUAI, 21.664, -158.054, rays, [], set(), opened, 4)
    assert status == "PASS" and "outside every arc —" in detail, detail
    shut = [{"min": 257, "max": 275, "span": 22}, {"min": 297, "max": 31, "span": 98}]
    status, detail = R.regression_verdict(KAUAI, 21.664, -158.054, rays, [], set(), shut, 4)
    assert status == "FAIL" and "outside every arc 280-292" in detail


def test_huntingtons_window():
    """Run 22 gave [145-195, 253-275] (padded 143-197, 251-277): 200-224 outside every arc
    (FAIL; 196 is inside the padded 143-197). With the shrink fix
    and San Clemente open, [145-224, 248-275] (padded 143-226, 246-277): 196-224 inside, and
    228-244 outside both."""
    run22 = [{"min": 145, "max": 195, "span": 54}, {"min": 253, "max": 275, "span": 26}]
    status, detail = R.regression_verdict(HUNTINGTON, 0, 0, {}, [], set(), run22, 4)
    assert status == "FAIL" and detail.endswith("outside every arc of the open 196–224: 200-224")
    fixed = [{"min": 145, "max": 224, "span": 83}, {"min": 248, "max": 275, "span": 31}]
    assert R.regression_verdict(HUNTINGTON, 0, 0, {}, [], set(), fixed, 4)[0] == "PASS"
    leaky = [{"min": 145, "max": 228, "span": 87}, {"min": 248, "max": 275, "span": 31}]
    status, detail = R.regression_verdict(HUNTINGTON, 0, 0, {}, [], set(), leaky, 4)
    assert status == "FAIL" and "inside an arc of the blocked 228–244: 228;" in detail


def test_diamond_heads_rays_from_canoes():
    """From Canoes (21.274499, -157.828324): to the summit (21.2614, -157.8053) is 1.4485 km
    south and 0.023024° × 111.32 × cos 21.27° = 2.388 km east, bearing 180 - atan(2.388 /
    1.4485) = 121.2°; to the point (21.2555, -157.8097) 2.101 km south and 1.932 km east,
    180 - atan(1.932 / 2.101) = 137.4°. The 4° rays between: 124, 128, 132, 136."""
    assert R._sector_rays(21.274499, -157.828324, CANOES["points"], 4) == [124, 128, 132, 136]
    rays = {b: {"result": "hard", "rule": "local_coast_30km"} for b in (124, 128, 132, 136)}
    status, detail = R.regression_verdict(CANOES, 21.274499, -157.828324, rays, [], {160}, [], 4)
    assert status == "PASS" and "blocked by local_coast_30km" in detail
    assert R.regression_verdict(CANOES, 21.274499, -157.828324, rays, [], {136}, [], 4)[0] == "FAIL"


def test_a_sector_across_north_and_a_sector_between_rays():
    assert R._sector_rays(0.0, 0.0, ((1.0, -0.035), (1.0, 0.035)), 4) == [0]       # ≈ 358°–2°
    assert R._sector_rays(0.0, 0.0, ((-0.52, 0.86), (-0.55, 0.85)), 4) == [120]   # ≈ 121–123°


def test_bounded():
    ri = next(c for c in R.REGRESSION_CHECKS if c["slug"] == "second-beach-newport")
    arcs = [{"min": 133, "max": 223, "span": 94}]
    assert R.regression_verdict(ri, 0, 0, {}, [], set(), arcs, 4)[0] == "PASS"
    wide = [{"min": 100, "max": 283, "span": 187}]
    assert R.regression_verdict(ri, 0, 0, {}, [], set(), wide, 4)[0] == "FAIL"
    assert R.regression_verdict(ri, 0, 0, {}, [], set(), [], 4)[0] == "FAIL"


def test_print_returns_false_on_a_failure_and_skips_spots_not_cast(capsys):
    cast = {"big-beach-makena": (20.63, -156.45, _rays(), [], {228}, [])}
    assert R.print_regression_checks(cast, 4) is False
    out = capsys.readouterr().out
    assert "FAIL   big-beach-makena" in out
    assert "—      lahaina-breakwall      Lāna'i's core: not in this run" in out
    cast = {"big-beach-makena": (20.63, -156.45, _rays(), [], set(), [])}
    assert R.print_regression_checks(cast, 4) is True
