"""pipeline/swell_window_soft.py: the candidate soft window, worked by hand.

Every expected value below is arithmetic on the formulas in the module docstring, done apart
from the module (no call to it supplies an expected value):

  L(T) = 9.81 T² / 2π:  8 s 99.92384 m, 12 s 224.82864 m, 18 s 505.86444 m
  √(2r/L) at r = 5000 m:  8 s 10.003810, 12 s 6.669207, 18 s 4.446138
  K(v) = √((f² + g'²)/2), f = (1 + 0.926v)/(2 + 1.792v + 3.104v²),
         g' = 1/(2 + 4.142v + 3.492v² + 6.670v³)
    v = 0:  f = g' = 0.5, K = √(0.25) = 0.5
    v = 1:  f = 1.926/6.896 = 0.279292, g' = 1/16.304 = 0.061335, K = 0.202196
    v = 2:  f = 2.852/18.0 = 0.158444, g' = 1/77.612 = 0.012885, K = 0.112407
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from pipeline import swell_window_soft as S

ROOT = Path(__file__).resolve().parents[2]

RAY = {"min": 317, "max": 319, "span": 6}          # pad (6 - 2)/2 = 2: a ray sample
PAD0 = {"min": 250, "max": 290, "span": 40}        # pad 0: synthesised, never trimmed
PAD_HALF = {"min": 300, "max": 359, "span": 60}    # pad 0.5: a wrap-split half
WRAP = {"min": 340, "max": 20, "span": 44}         # pad 2, across north


def r6(x):
    return round(x, 6)


def test_wavelength():
    assert round(S.wavelength_m(12.0), 4) == 224.8286
    assert round(S.wavelength_m(8.0), 4) == 99.9238


@pytest.mark.parametrize("v, k", [(0.0, 0.5), (1.0, 0.202196), (2.0, 0.112407),
                                  (-1.0, 0.797804), (-2.0, 0.887593)])
def test_fresnel_k(v, k):
    assert r6(S.fresnel_k(v)) == k


@pytest.mark.parametrize("arc, sector", [
    (RAY, (310.0, 16.0)),          # padded 315..321, widened 5 each way
    (PAD0, (250.0, 40.0)),         # not a ray sample: as stored
    (PAD_HALF, (299.5, 60.0)),     # pad 0.5 only
    (WRAP, (333.0, 54.0)),         # 340 - 2 - 5; 40 + 4 + 10
])
def test_geometric_sectors(arc, sector):
    assert S.geometric_sectors([arc]) == [sector]


def test_padded_sectors_are_todays_window():
    assert S.padded_sectors([RAY, PAD0]) == [(315.0, 6.0), (250.0, 40.0)]


def test_two_arcs_whose_widened_sectors_overlap_have_one_pair_of_edges():
    # A: padded 248..272 -> 243..277. B: padded 280..302 -> 275..307. They overlap at 275..277,
    # so the gap 272..280 that today's window leaves is inside the geometric window.
    a = {"min": 250, "max": 270, "span": 24}
    b = {"min": 282, "max": 300, "span": 22}
    assert S.union_edges(S.geometric_sectors([a, b])) == [243.0, 307.0]
    # 276: 33 from 243, 31 from 307, inside -> -31. Today it is 4 outside -> the 0.40 rung.
    assert S.edge_offset(276.0, [a, b]) == -31.0
    assert S.today_height_factor(276.0, [a, b], 275.0, 275.0) == 0.40
    # 12 s: v = 31° x 6.669207 = 3.608388, K = 0.062829, E = 0.937171; A = cos²(0.5°) = 0.999924
    assert r6(S.candidate_height_factor(276.0, 12.0, [a, b], 275.0, 275.0)) == 0.937100


@pytest.mark.parametrize("sectors, edges", [
    ([(0.0, 10.0), (10.0, 10.0)], [0.0, 20.0]),          # touching: one window 0..20
    ([(0.0, 20.0), (0.0, 10.0)], [0.0, 20.0]),           # nested, sharing the start
    ([(0.0, 20.0), (10.0, 10.0)], [0.0, 20.0]),          # nested, sharing the end
    ([(0.0, 10.0), (0.0, 10.0)], [0.0, 10.0]),           # the same sector twice
    ([(350.0, 20.0), (20.0, 10.0)], [10.0, 20.0, 30.0, 350.0]),  # 350..10 and 20..30
    ([(350.0, 20.0), (10.0, 10.0)], [20.0, 350.0]),      # touching across north
])
def test_union_edges(sectors, edges):
    assert S.union_edges(sectors) == edges


def test_edge_offset_signs_and_wrap():
    assert S.edge_offset(0.0, [WRAP]) == -27.0      # 333..27: 27 from either end
    assert S.edge_offset(30.0, [WRAP]) == 3.0
    assert S.edge_offset(320.0, [WRAP]) == 13.0


def test_edge_offset_whole_compass_and_none():
    assert S.edge_offset(123.0, [{"min": 0, "max": 356, "span": 360}]) == -180.0
    assert S.edge_offset(123.0, []) is None
    assert S.edge_offset(123.0, None) is None


def test_edge_factor_is_one_half_on_the_edge_at_every_period():
    assert [S.edge_factor(0.0, t) for t in (8.0, 12.0, 18.0)] == [0.5, 0.5, 0.5]


def test_edge_factor_is_zero_beyond_ninety():
    assert S.edge_factor(90.5, 18.0) == 0.0


def test_no_refraction_floor_at_or_below_nine_seconds():
    # 9 s: ramp (9-9)/5 = 0, so E is K alone. L(9) = 126.466109 m, √(10000/L) = 8.892276,
    # v = 0.593412 x 8.892276 = 5.276783: f = 0.060135, g' = 0.000908, K = 0.042527
    assert r6(S.edge_factor(34.0, 9.0)) == 0.042527


# --------------------------------------------------------------------------- #
# Lovers Point, as the committed roster holds it                               #
# --------------------------------------------------------------------------- #
# Arc 317..319 span 6 (pad 2): today's window 315..321, geometric 310..326; optimal 315.

def _lovers():
    spots = json.loads((ROOT / "pipeline" / "spots_enriched.json").read_text(encoding="utf-8"))
    s, = [x for x in spots if x.get("name") == "Lovers Point"]
    assert (s["swell_window_arcs"], s["optimal_swell_dir"], s["orientation_deg"]) == (
        [RAY], 315, 29.0)
    return s["swell_window_arcs"], s["optimal_swell_dir"], s["orientation_deg"]


def test_lovers_point_inside():
    arcs, opt, ori = _lovers()
    # today: cos²(2.5°) = 0.998097. δ = -min(10, 6) = -6.
    # 12 s: v = 6° x 6.669207 = 0.698398, K = 0.257020, E = 0.742980; H = 0.998097 x E
    assert r6(S.today_height_factor(320.0, arcs, opt, ori)) == 0.998097
    assert S.edge_offset(320.0, arcs) == -6.0
    assert r6(S.candidate_height_factor(320.0, 12.0, arcs, opt, ori)) == 0.741567


def test_lovers_point_just_outside():
    arcs, opt, ori = _lovers()
    # today: 9 past 321 -> 0.40. δ = +4; A = cos²(7.5°) = 0.982963.
    #  8 s: v = 4° x 10.003810 = 0.698398, K = 0.257020, no floor        -> H = 0.252641
    # 12 s: v = 0.465598, K = 0.317279; floor 0.1 x 0.6 x (1 - 4/90) = 0.057333, √ = 0.239444
    #       -> E = K                                                      -> H = 0.311873
    # 18 s: v = 0.310399, K = 0.368629; floor 0.1 x (1 - 4/90), √ = 0.309121 -> H = 0.362349
    assert S.today_height_factor(330.0, arcs, opt, ori) == 0.40
    assert S.edge_offset(330.0, arcs) == 4.0
    got = [r6(S.candidate_height_factor(330.0, t, arcs, opt, ori)) for t in (8.0, 12.0, 18.0)]
    assert got == [0.252641, 0.311873, 0.362349]


def test_lovers_point_north_where_the_floor_takes_over():
    arcs, opt, ori = _lovers()
    # 0°: today 39 past 321 -> 0.40. δ = 34 from 326; A = cos²(22.5°) = 0.853553.
    #  8 s: v = 5.936380, K = 0.037652, no floor                          -> H = 0.032138
    # 12 s: K(3.957587) = 0.057176 < √(0.1 x 0.6 x 56/90) = 0.193218       -> H = 0.164922
    # 18 s: K(2.638391) = 0.086038 < √(0.1 x 56/90) = 0.249444             -> H = 0.212914
    assert S.today_height_factor(0.0, arcs, opt, ori) == 0.40
    got = [r6(S.candidate_height_factor(0.0, t, arcs, opt, ori)) for t in (8.0, 12.0, 18.0)]
    assert got == [0.032138, 0.164922, 0.212914]


def test_lovers_point_behind_the_point_is_zero_both_ways():
    arcs, opt, ori = _lovers()
    # 200: today 115 from 315 -> 0; δ = 110 from 310 -> beyond 90 -> 0.
    assert S.today_height_factor(200.0, arcs, opt, ori) == 0.0
    assert [S.candidate_height_factor(200.0, t, arcs, opt, ori) for t in (8.0, 12.0, 18.0)] == [
        0.0, 0.0, 0.0]


# --------------------------------------------------------------------------- #
# No target, no arcs                                                           #
# --------------------------------------------------------------------------- #

def test_inside_with_no_target_uses_todays_neutral_half():
    # PAD0 is 250..290, dp 270: δ = -20. 12 s: v = 2.327992, K = 0.097239 -> 0.5 x 0.902761
    assert r6(S.candidate_height_factor(270.0, 12.0, [PAD0], None, None)) == 0.451380


def test_no_arcs_is_the_alignment_alone():
    assert r6(S.candidate_height_factor(300.0, 12.0, [], 270.0, 0.0)) == 0.933013  # cos²(15°)
    assert S.candidate_height_factor(300.0, 12.0, [], None, None) == 0.0


def test_nothing_in_the_pipeline_imports_it():
    """Read-only: no pipeline module but this one names it, so no rating can read it."""
    users = sorted(str(p.relative_to(ROOT)) for p in (ROOT / "pipeline").rglob("*.py")
                   if "tests" not in p.parts and p.name != "swell_window_soft.py"
                   and "swell_window_soft" in p.read_text(encoding="utf-8"))
    assert users == []
