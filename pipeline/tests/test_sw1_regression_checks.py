"""The validate run's regression checks (pipeline/sw1_raycast.py REGRESSION_CHECKS).

Real shadows the R1–R3 island rules must keep: Kaho'olawe at Big Beach Makena, Lāna'i at
Lahaina, Diamond Head at Canoes; Second Beach RI bounded; Pipeline's window unchanged; and
Santa Cruz Island at Pitas Point / Mondos reported either way, with its own run and wrap.
The checks read the validate run's own debug records; these tests feed them by hand.
"""
from __future__ import annotations

import json
import re

from pipeline import sw1_raycast as R
from pipeline.config import DEFAULT_ENRICHED_OUTPUT

KAHO = next(c for c in R.REGRESSION_CHECKS if c["slug"] == "big-beach-makena")
SANTA_CRUZ = next(c for c in R.REGRESSION_CHECKS if c["slug"] == "pitas-point")
CANOES = next(c for c in R.REGRESSION_CHECKS if c["slug"] == "canoes-waikiki")


def _kaho(island=7, **kw):
    return {"island": island, "area_km2": 123.0, "centroid": (20.548, -156.605),
            "dist_km": 12.0, **kw}


def _rays():
    """Kaho'olawe walls ray 232 (R2) and is a partial hit on 236; ray 240 passes an islet."""
    return {
        232: {"result": "hard", "rule": "fresnel_wall", **_kaho(dist_km=11.4, fresnel_n=42.0)},
        236: {"result": "small", "dist_km": 12.0, "area_km2": 123.0, "centroid": (20.548, -156.605),
              "partials": [_kaho()], "passed": []},
        240: {"result": "open", "passed": [{"island": 9, "area_km2": 0.4, "centroid": (20.64, -156.50),
                                            "dist_km": 6.0, "fresnel_n": 0.2}]},
    }


def test_every_check_names_a_roster_spot():
    roster = json.loads(DEFAULT_ENRICHED_OUTPUT.read_text())
    slugs = {re.sub(r"[^a-z0-9]+", "-", s["name"].lower()).strip("-") for s in roster}
    assert {c["slug"] for c in R.REGRESSION_CHECKS} == {
        "big-beach-makena", "lahaina-breakwall", "pitas-point", "mondos", "canoes-waikiki",
        "second-beach-newport", "banzai-pipeline"}
    assert {c["slug"] for c in R.REGRESSION_CHECKS} <= slugs


def test_an_island_whose_rays_stay_blocked_passes():
    status, detail = R.regression_verdict(KAHO, 20.63, -156.45, _rays(), [], {240}, [], None, 4)
    assert status == "PASS", detail
    assert "rays 232-236 (fresnel_wall, small)" in detail and "open —" in detail


def test_an_island_with_an_open_ray_fails():
    status, detail = R.regression_verdict(KAHO, 20.63, -156.45, _rays(), [], {236, 240}, [], None, 4)
    assert status == "FAIL" and "open 236" in detail


def test_an_island_not_found_fails():
    rays = {b: {"result": "open", "passed": []} for b in range(0, 360, 4)}
    status, detail = R.regression_verdict(KAHO, 20.63, -156.45, rays, [], set(range(0, 360, 4)),
                                          [], None, 4)
    assert status == "FAIL" and "cannot verify" in detail


def test_a_different_island_at_the_same_place_does_not_match():
    """Area outside 80–200 km² (Maui's 1,900 km² own coast) is not Kaho'olawe."""
    rays = {232: {"result": "hard", "rule": "local_coast_30km", "island": 1, "area_km2": 1900.0,
                  "centroid": (20.55, -156.6), "dist_km": 2.0}}
    assert R.regression_verdict(KAHO, 20.63, -156.45, rays, [], set(), [], None, 4)[0] == "FAIL"


def test_santa_cruz_is_reported_with_its_own_run():
    sc = {"island": 3, "area_km2": 257.0, "centroid": (34.0, -119.7), "dist_km": 33.0}
    rays = {b: {"result": "small", "partials": [dict(sc, dist_km=33.0 + b - 208)], "passed": []}
            for b in range(208, 233, 4)}
    chains = [{"lo": 208, "hi": 232, "width": 28, "dmin_km": 33.0, "wrap_deg": 18.6,
               "decision": "open_wrapped", "island": 3}]
    status, detail = R.regression_verdict(SANTA_CRUZ, 34.32, -119.39, rays, chains,
                                          set(range(208, 233, 4)), [], None, 4)
    assert status == "OPENS"
    assert detail.endswith("own run 208–232 (28°), wrap 18.6° → open_wrapped")
    status, _ = R.regression_verdict(SANTA_CRUZ, 34.32, -119.39, rays, chains, set(), [], None, 4)
    assert status == "STAYS"


def test_diamond_heads_rays_from_canoes():
    """From Canoes (21.274499, -157.828324): to the summit (21.2614, -157.8053) is 1.4485 km
    south and 0.023024° × 111.32 × cos 21.27° = 2.388 km east, bearing 180 - atan(2.388 /
    1.4485) = 121.2°; to the point (21.2555, -157.8097) 2.101 km south and 1.932 km east,
    180 - atan(1.932 / 2.101) = 137.4°. The 4° rays between: 124, 128, 132, 136."""
    assert R._sector_rays(21.274499, -157.828324, CANOES["points"], 4) == [124, 128, 132, 136]
    rays = {b: {"result": "hard", "rule": "local_coast_30km"} for b in (124, 128, 132, 136)}
    status, detail = R.regression_verdict(CANOES, 21.274499, -157.828324, rays, [], {160}, [], None, 4)
    assert status == "PASS" and "blocked by local_coast_30km" in detail
    assert R.regression_verdict(CANOES, 21.274499, -157.828324, rays, [], {136}, [], None, 4)[0] == "FAIL"


def test_a_sector_across_north_and_a_sector_between_rays():
    assert R._sector_rays(0.0, 0.0, ((1.0, -0.035), (1.0, 0.035)), 4) == [0]       # ≈ 358°–2°
    assert R._sector_rays(0.0, 0.0, ((-0.52, 0.86), (-0.55, 0.85)), 4) == [120]   # ≈ 121–123°


def test_bounded_and_unchanged():
    ri = next(c for c in R.REGRESSION_CHECKS if c["slug"] == "second-beach-newport")
    arcs = [{"min": 133, "max": 223, "span": 94}]
    assert R.regression_verdict(ri, 0, 0, {}, [], set(), arcs, None, 4)[0] == "PASS"
    wide = [{"min": 100, "max": 283, "span": 187}]
    assert R.regression_verdict(ri, 0, 0, {}, [], set(), wide, None, 4)[0] == "FAIL"
    assert R.regression_verdict(ri, 0, 0, {}, [], set(), [], None, 4)[0] == "FAIL"
    pipe = next(c for c in R.REGRESSION_CHECKS if c["slug"] == "banzai-pipeline")
    stored = [{"min": 257, "max": 271, "span": 18}, {"min": 301, "max": 31, "span": 94}]
    assert R.regression_verdict(pipe, 0, 0, {}, [], set(), list(stored), stored, 4)[0] == "PASS"
    moved = [{"min": 257, "max": 271, "span": 18}, {"min": 297, "max": 31, "span": 98}]
    status, detail = R.regression_verdict(pipe, 0, 0, {}, [], set(), moved, stored, 4)
    assert status == "FAIL" and detail == ("now [257–271(18), 297–31(98)], "
                                           "stored [257–271(18), 301–31(94)]")


def test_print_returns_false_on_a_failure_and_skips_spots_not_cast(capsys):
    cast = {"big-beach-makena": (20.63, -156.45, _rays(), [], {236}, [])}
    assert R.print_regression_checks(cast, {}, 4) is False
    out = capsys.readouterr().out
    assert "FAIL   big-beach-makena" in out
    assert "—      lahaina-breakwall      Lāna'i: not in this run" in out
    cast = {"big-beach-makena": (20.63, -156.45, _rays(), [], set(), [])}
    assert R.print_regression_checks(cast, {}, 4) is True
