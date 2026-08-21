"""Arc membership uses the arc's TRUE sector, not its ray-centre bounds.

THE DEFECT. swell_window._make_arc emits {min, max, span} where min and max are the FIRST and
LAST RAY BEARINGS that passed the raycast, and span = (max - min) + step. Each ray stands for
the whole step-wide sector it samples, so the open window really runs half a step beyond each
bound and span is the correct sector measure. Six membership implementations — three in
production, three re-implemented inside tests — instead compared `min <= dp <= max`, clipping
half a step off each end of every window. Measured on live WW3 partition bearings: 2019 of
94898 misclassified (2.13%), gain swings from -0.150 to +0.600, 901 worse and 1111 better.

THE PAD IS DERIVED, NEVER ASSUMED: pad = (span - ((max - min) mod 360)) / 2. Four conventions
are live in spots_enriched.json and one formula reproduces all four, which is exactly why it
is computed rather than tabled:
    pad 2.0  1377 arcs  raycast at sw1_raycast.RUN_STEP_DEG = 4  (how the roster was built)
    pad 1.0    12 arcs  raycast at config.SWELL_RAY_STEP_DEG = 2
    pad 0.5    14 arcs  swell_window_fallback wrap-split halves
    pad 0.0    72 arcs  swell_window_fallback._centered_arc, non-wrapping
An earlier brief for this work assumed pad 1.0 everywhere because config.SWELL_RAY_STEP_DEG is
2; the roster was actually cast at 4 by a different constant. That is the trap these tests
exist to hold shut — see test_pad_is_derived_not_assumed_from_a_step_constant.

WHAT IS DELIBERATELY NOT CHANGED, and these tests pin it: the 0.25 in-window floor, the
soft-outside ladder values 0.40 / 0.15 / 0.0, and every stored arc.

LATER CHANGE, not part of this work. The in-window kernel was subsequently narrowed from
cos²(diff) to cos²(diff/2) — the same half-angle curve the no-arcs branch already used — on
MOP calibration evidence recorded in directional_gain's docstring. Four expectations here
moved with it and say so at the assertion; the kernel itself is pinned by
test_directional_gain_frame.py, not here.

EVERY EXPECTED VALUE IS HAND-COMPUTED with the arithmetic in a comment. None is derived by
calling the function under test — the three deleted test-side re-implementations
(test_swell_window.py:159, :220, :286) are precisely why: a test that re-implements its
subject cannot catch a change to it.

Run: python -m pipeline.tests.test_arc_membership   (or pytest)
"""
from __future__ import annotations

import json
import math
import os

from pipeline.interpret import (
    arc_pad_deg, bearing_in_arc, directional_gain, in_any_arc, _min_offset_from_arcs,
)

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# A raycast-at-step-4 arc: width = 200 - 100 = 100, span = 100 + 4 = 104, so pad = 4/2 = 2.
# True sector = [100 - 2, 200 + 2] = [98, 202].
ARC4 = {"min": 100, "max": 200, "span": 104}

# A wrap-through-zero arc at the same step: width = (20 - 340) mod 360 = 40,
# span = 40 + 4 = 44, pad = 2. True sector = [338, 22] through 0.
WRAP4 = {"min": 340, "max": 20, "span": 44}


def _close(a, b, tol=1e-9):
    return abs(a - b) <= tol


# --------------------------------------------------------------------------- #
# 10 — the pad derivation, on all four live conventions                        #
# --------------------------------------------------------------------------- #
def test_pad_derivation_on_all_four_live_conventions():
    """pad = (span - width) / 2, width = (max - min) mod 360. Hand arithmetic per row:

      raycast step 4  {181, 251, 74}: width 251-181=70,  (74-70)/2  = 2.0
      raycast step 2  { 65,  99, 36}: width  99-65 =34,  (36-34)/2  = 1.0
      fallback centred{145, 305,160}: width 305-145=160, (160-160)/2= 0.0
      fallback wrap-hi{  0, 136,137}: width 136-0  =136, (137-136)/2= 0.5
      fallback wrap-lo{336, 359, 24}: width 359-336=23,  (24-23)/2  = 0.5
    """
    assert arc_pad_deg({"min": 181, "max": 251, "span": 74}) == 2.0
    assert arc_pad_deg({"min": 65, "max": 99, "span": 36}) == 1.0
    assert arc_pad_deg({"min": 145, "max": 305, "span": 160}) == 0.0
    assert arc_pad_deg({"min": 0, "max": 136, "span": 137}) == 0.5
    assert arc_pad_deg({"min": 336, "max": 359, "span": 24}) == 0.5


def test_pad_derivation_is_wrap_aware():
    """A wrap arc's width must go the short way through 0, not come out negative.
      {340, 20, 44}: width = (20 - 340) mod 360 = 40; (44 - 40)/2 = 2.0
      {359, 149, 152} (North Jetty, live): width = (149-359) mod 360 = 150; (152-150)/2 = 1.0
    """
    assert arc_pad_deg(WRAP4) == 2.0
    assert arc_pad_deg({"min": 359, "max": 149, "span": 152}) == 1.0


def test_pad_is_zero_and_survives_when_span_is_absent_or_arc_is_malformed():
    """No span means no provenance for a sector width, so the bounds are taken as the sector
    edges (pad 0) rather than a step being guessed. Must not raise."""
    assert arc_pad_deg({"min": 100, "max": 200}) == 0.0
    assert arc_pad_deg({"min": 100, "max": 200, "span": None}) == 0.0
    assert arc_pad_deg({}) == 0.0
    assert arc_pad_deg({"max": 200, "span": 104}) == 0.0


def test_pad_is_derived_not_assumed_from_a_step_constant():
    """THE TRAP THIS BUG CAME FROM. config.SWELL_RAY_STEP_DEG is 2, but the production roster
    was cast at sw1_raycast.RUN_STEP_DEG = 4, so any implementation keyed to the config
    constant computes half the real pad. Reading it from span makes the source constant
    irrelevant: the same code yields 2.0 and 1.0 from arcs that differ only in span."""
    same_bounds_step4 = {"min": 100, "max": 200, "span": 104}   # (104-100)/2 = 2.0
    same_bounds_step2 = {"min": 100, "max": 200, "span": 102}   # (102-100)/2 = 1.0
    assert arc_pad_deg(same_bounds_step4) == 2.0
    assert arc_pad_deg(same_bounds_step2) == 1.0
    assert arc_pad_deg(same_bounds_step4) != arc_pad_deg(same_bounds_step2), \
        "identical bounds must give different pads — the pad comes from span, not a constant"


def test_every_live_arc_yields_one_of_the_four_known_pads():
    """Regression net over the real data: 1475 arcs, no fifth convention, no arc without span.
    A new value here is a finding about the data, not a reason to clamp."""
    spots = json.load(open(os.path.join(_REPO, "pipeline", "spots_enriched.json")))
    seen, n = {}, 0
    for s in spots:
        for a in (s.get("swell_window_arcs") or []):
            n += 1
            p = arc_pad_deg(a)
            seen[p] = seen.get(p, 0) + 1
            assert a.get("span") is not None, f"live arc without span: {s.get('name')} {a}"
    assert set(seen) == {0.0, 0.5, 1.0, 2.0}, f"unexpected pad convention in live data: {seen}"
    assert seen[2.0] > seen[1.0], "the roster is step-4 dominant; step-2 is the rare case"
    assert n == sum(seen.values()) and n > 1000, n


# --------------------------------------------------------------------------- #
# 8 — membership by exact expected boolean, straight arc and wrap arc          #
# --------------------------------------------------------------------------- #
def test_membership_at_every_boundary_position_straight_arc():
    """ARC4 = {100, 200, span 104} -> width 100, pad 2, true sector [98, 202].
    Inclusive at BOTH padded edges. Half a step (2°) outside a bound is INSIDE — that is the
    fix. A full step (4°) outside is OUTSIDE — the pad is half a step, not a step."""
    assert in_any_arc(100, [ARC4]) is True      # exactly on min
    assert in_any_arc(200, [ARC4]) is True      # exactly on max
    assert in_any_arc(98, [ARC4]) is True       # min - 2, the padded edge itself
    assert in_any_arc(202, [ARC4]) is True      # max + 2, the padded edge itself
    assert in_any_arc(96, [ARC4]) is False      # min - 4, a full step out
    assert in_any_arc(204, [ARC4]) is False     # max + 4, a full step out
    assert in_any_arc(150, [ARC4]) is True      # well inside
    assert in_any_arc(300, [ARC4]) is False     # well outside
    # just past the padded edge, either side
    assert in_any_arc(97.99, [ARC4]) is False
    assert in_any_arc(202.01, [ARC4]) is False


def test_membership_at_every_boundary_position_wrap_arc():
    """WRAP4 = {340, 20, span 44} -> width (20-340) mod 360 = 40, pad 2, sector [338, 22]."""
    assert in_any_arc(340, [WRAP4]) is True     # exactly on min
    assert in_any_arc(20, [WRAP4]) is True      # exactly on max
    assert in_any_arc(338, [WRAP4]) is True     # min - 2, padded edge
    assert in_any_arc(22, [WRAP4]) is True      # max + 2, padded edge
    assert in_any_arc(336, [WRAP4]) is False    # min - 4, full step out
    assert in_any_arc(24, [WRAP4]) is False     # max + 4, full step out
    assert in_any_arc(0, [WRAP4]) is True       # straight through 0
    assert in_any_arc(359, [WRAP4]) is True
    assert in_any_arc(180, [WRAP4]) is False    # opposite side
    assert in_any_arc(337.99, [WRAP4]) is False
    assert in_any_arc(22.01, [WRAP4]) is False


def test_membership_edge_cases_that_are_not_boundary_arithmetic():
    """Empty / missing / malformed input, and the fully-open window."""
    assert in_any_arc(123, []) is False
    assert in_any_arc(123, None) is False
    assert in_any_arc(123, [{"min": 100}]) is False          # no max -> skipped, not a crash
    assert in_any_arc(123, [{}]) is False
    # multi-arc: inside the SECOND arc still counts
    assert in_any_arc(345, [ARC4, WRAP4]) is True
    # fully open: swell_window emits _make_arc(0, 360-step, step) -> {0, 356, 360} at step 4.
    # width 356, pad 2, total 360 -> every bearing is inside.
    full = {"min": 0, "max": 356, "span": 360}
    assert arc_pad_deg(full) == 2.0
    for b in (0, 90, 180, 270, 358, 359.9):
        assert in_any_arc(b, [full]) is True, b


def test_a_very_wide_arc_is_still_bounded():
    """A wide window must not collapse into "everything is inside". Eight live arcs sit
    between 180° and 360° of total sector — the widest is North Buxton, {9, 231, span 226}:
    width 231-9 = 222, pad (226-222)/2 = 2, padded sector [7, 233].
        dp = 7   -> (7 - 9 + 2)   mod 360 = 0   <= 226 -> inside  (padded min)
        dp = 233 -> (233 - 9 + 2) mod 360 = 226 <= 226 -> inside  (padded max)
        dp = 5   -> (5 - 9 + 2)   mod 360 = 358 >  226 -> OUTSIDE
        dp = 300 -> (300 - 9 + 2) mod 360 = 293 >  226 -> OUTSIDE
    A short-circuit keyed to anything below 360 would swallow all four."""
    wide = {"min": 9, "max": 231, "span": 226}
    assert arc_pad_deg(wide) == 2.0
    assert in_any_arc(7, [wide]) is True
    assert in_any_arc(233, [wide]) is True
    assert in_any_arc(5, [wide]) is False
    assert in_any_arc(300, [wide]) is False
    assert in_any_arc(120, [wide]) is True      # mid-sector


def test_bearing_in_arc_is_the_single_arc_primitive():
    """in_any_arc is a thin any() over bearing_in_arc; both must agree per arc."""
    for b in (96, 98, 100, 150, 200, 202, 204):
        assert bearing_in_arc(b, ARC4) == in_any_arc(b, [ARC4]), b
    assert bearing_in_arc(345, ARC4) is False
    assert bearing_in_arc(345, WRAP4) is True


# --------------------------------------------------------------------------- #
# 9 — directional_gain: the discontinuity is gone (Steamer Lane, real data)     #
# --------------------------------------------------------------------------- #
# Steamer Lane, live geometry: arcs [181-251] span 74 and [317-331] span 18, optimal 244.
# Both pad 2.0: (74 - 70)/2 = 2 and (18 - 14)/2 = 2. Arc 1's padded sector is [179, 253].
# 251 is the OPTIMAL-FACING edge (optimal 244 sits 7° inside it), which is why a near-miss
# there was the expensive one: it fell to the soft-outside ladder's 0.40 while the in-window
# kernel would have returned ~0.98.
SL_ARCS = [{"min": 181, "max": 251, "span": 74}, {"min": 317, "max": 331, "span": 18}]
SL_OPTIMAL = 244
SL_ORIENT = 128.0


def test_steamer_lane_arcs_have_the_pad_this_test_assumes():
    """Guard the premise: if the fixture's pad were not 2.0 every expectation below shifts."""
    assert arc_pad_deg(SL_ARCS[0]) == 2.0
    assert arc_pad_deg(SL_ARCS[1]) == 2.0


def test_directional_gain_half_a_degree_outside_the_optimal_facing_edge():
    """dp = 251.5, i.e. max + 0.5. Padded sector reaches 253, so this is now INSIDE and takes
    the in-window kernel:
        diff = ((251.5 - 244 + 540) mod 360) - 180 = 7.5
        gain = cos²(7.5/2) = cos²(3.75°); cos(3.75°) = 0.997858923, squared = 0.995722431
        floor max(0.25, 0.995722431) does not bite
    BEFORE THE FIX this bearing was outside and returned the ladder's 0.40 — a 0.596 cliff
    across half a degree. That cliff is what this pins shut.
    VALUE CHANGED from 0.982962913 when the in-window kernel moved from cos²(diff) to
    cos²(diff/2) — see directional_gain's docstring for the MOP calibration behind that."""
    got = directional_gain(251.5, SL_ARCS, SL_OPTIMAL, SL_ORIENT)
    assert _close(got, 0.995722430687, tol=1e-9), got
    assert got != 0.40


def test_directional_gain_one_and_a_half_degrees_outside_the_optimal_facing_edge():
    """dp = 252.5, i.e. max + 1.5, still within the 2° pad.
        diff = 252.5 - 244 = 8.5
        gain = cos²(8.5/2) = cos²(4.25°); cos(4.25°) = 0.997250185, squared = 0.994507932
    VALUE CHANGED from 0.978152378 with the half-angle kernel.
    """
    got = directional_gain(252.5, SL_ARCS, SL_OPTIMAL, SL_ORIENT)
    assert _close(got, 0.994507931681, tol=1e-9), got


def test_directional_gain_is_continuous_across_the_padded_edge():
    """The sector now ENDS at 253. Just inside keeps the kernel; just outside takes the ladder.
    A step remains — the ladder is a different function and the brief forbids changing it — but
    it now sits at the TRUE window edge instead of half a step inside it, and nothing between
    251 and 253 is misclassified any more.
        253.0 inside : diff 9.0, cos(9/2°) = cos(4.5°) = 0.996917334, squared = 0.993844170
        253.5 outside: offset from padded edge 253 is 0.5 -> ladder band <45° -> 0.40
    VALUE CHANGED from 0.975528258 with the half-angle kernel; the ladder side is untouched.
    """
    assert _close(directional_gain(253.0, SL_ARCS, SL_OPTIMAL, SL_ORIENT),
                  0.993844170298, tol=1e-9)
    assert directional_gain(253.5, SL_ARCS, SL_OPTIMAL, SL_ORIENT) == 0.40


def test_the_soft_outside_ladder_values_are_untouched():
    """0.40 / 0.15 / 0.0 unchanged — only WHERE each band starts moved, because offsets are
    now taken from the padded edge. One arc, {100, 120, span 24}: width 20, pad 2, padded
    sector [98, 122]. Offsets are the min over BOTH padded edges:
        dp = 130 -> |130-122| = 8,   |130-98| = 32  -> 8   -> band <45   -> 0.40
        dp = 182 -> |182-122| = 60,  |182-98| = 84  -> 60  -> band 45-90 -> 0.15
        dp = 280 -> |280-122| = 158, |280-98| = 178 -> 158 -> band >90   -> 0.0
    """
    far = [{"min": 100, "max": 120, "span": 24}]
    assert directional_gain(130.0, far, 110, 110) == 0.40
    assert directional_gain(182.0, far, 110, 110) == 0.15
    assert directional_gain(280.0, far, 110, 110) == 0.0
    # and on the real Steamer Lane geometry: dp 273 is 20° past arc1's padded edge 253
    # (arc2's padded edge 315 is 42° away), so min offset 20 -> 0.40
    assert directional_gain(273.0, SL_ARCS, SL_OPTIMAL, SL_ORIENT) == 0.40


def test_min_offset_measures_from_the_padded_edge_not_the_raw_bound():
    """Requirement 3: membership and offset must use the SAME edge or the cliff relocates.
    ARC4 padded sector is [98, 202].
        dp = 202   -> inside -> 0.0
        dp = 203   -> 1° past the padded edge (NOT 3° past the raw bound 200)
        dp = 210   -> 8° past
        dp = 97    -> 1° before the padded edge 98
    """
    # strictly INSIDE short-circuits to 0, per the docstring's "Returns 0 when dp is inside
    # any arc". Without that branch dp=150 would report min(|150-98|, |150-202|) = 52.
    assert _min_offset_from_arcs(150, [ARC4]) == 0.0
    assert _min_offset_from_arcs(120, [ARC4]) == 0.0
    assert _min_offset_from_arcs(202, [ARC4]) == 0.0
    assert _close(_min_offset_from_arcs(203, [ARC4]), 1.0)
    assert _close(_min_offset_from_arcs(210, [ARC4]), 8.0)
    assert _close(_min_offset_from_arcs(97, [ARC4]), 1.0)
    # and it is continuous: offset -> 0 as the bearing approaches the sector from outside
    assert _close(_min_offset_from_arcs(202.001, [ARC4]), 0.001, tol=1e-6)


def test_untouched_kernels_and_floor():
    """The comparison changed; the scoring did not."""
    # A bearing inside the sector but exactly 90° off optimal.
    #   diff = ((154 - 244 + 540) mod 360) - 180 = -90
    #   gain = cos²(-90/2) = cos²(45°) = 0.5 exactly; the 0.25 floor does not bite.
    # VALUE CHANGED from 0.25: under the OLD full-angle kernel this was cos²(90°) = 0.0,
    # floored up to 0.25. The floor itself is untouched — see the dedicated floor pin below.
    inside_far = directional_gain(154.0, [{"min": 100, "max": 200, "span": 104}], 244, 128.0)
    assert _close(inside_far, 0.5, tol=1e-12), inside_far
    # the in-window floor is still 0.25 and still 0.25: 180° off optimal gives cos²(90°) = 0.0
    #   arc [100,200] span 104 -> pad 2 -> padded sector [98, 202]; dp 100 is inside it
    #   diff = ((100 - 280 + 540) mod 360) - 180 = -180 -> cos²(-90°) = 0.0 -> floored to 0.25
    assert directional_gain(100.0, [{"min": 100, "max": 200, "span": 104}], 280, 128.0) == 0.25
    # empty-arcs branch is unchanged (cos²(diff/2) about optimal, floored at 0.25)
    #   dp 244 vs optimal 244 -> diff 0 -> cos²(0) = 1.0
    assert _close(directional_gain(244.0, [], 244, 128.0), 1.0)
    #   dp 64 vs optimal 244 -> diff 180 -> cos²(90°) = 0 -> floored to 0.25
    assert directional_gain(64.0, [], 244, 128.0) == 0.25
    # no arcs and no target at all -> 0.0
    assert directional_gain(100.0, [], None, None) == 0.0
    # soft_outside=False still hard-zeros outside the sector
    assert directional_gain(300.0, [ARC4], 150, 150, soft_outside=False) == 0.0


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} arc-membership checks passed")


if __name__ == "__main__":
    _run_all()
