"""The partition is scored in the SAME geometric frame it was selected in.

THE DEFECT. combine_ww3_partitions picks the winning swell partition with
directional_gain(dp, spot's real arcs, spot's optimal_swell_dir, orientation_deg).
apply_nwps_overrides then called nwps_stars, which recomputed the gain as
directional_gain(swell_dir, [], orientation, orientation) — EMPTY arcs, with
orientation_deg jammed into the optimal_swell_dir slot — and overwrote the selection
value. So every nwps spot chose its partition in one frame and scored it in another,
and directional_gain's arc branch was unreachable for all 598 of them. mop_stars had
the identical defect, passing [] and the shore normal twice.

THE KERNEL. The in-window branch also used cos²(delta) where the no-arcs branch used
cos²(delta/2). Calibrated against CDIP MOP refraction transfer coefficients (swl_et,
72 directions x 10 swell frequency bands, ~14 s band) at 31 California sites with
shoreFlag == 1:

    arcs + cos²(delta/2) in-window   mean r=+0.645  median +0.672  best at 29/31
    cos²(delta/2), no arcs           mean r=+0.598  median +0.617  best at  2/31
    cos²(delta) in-window            mean r=+0.295  median +0.341  best at  0/31

Arc gating helps, and the in-window kernel must be the WIDE half-angle form. Both
branches now share one curve and differ only in TARGET (optimal vs orientation) and
in GATING (arc membership vs none). Sample caveat: 31 sites, all California.

EVERY EXPECTED VALUE BELOW IS HAND-COMPUTED, with the arithmetic in a comment, and
every one is stated alongside what the OLD code returned for the same input. None is
derived by calling the function under test: cos²(30°) = 0.750 under the new form and
0.250 under the old, and a test that cannot tell those apart is not a test.

ALL GEOMETRY IS LIVE, read from spots_enriched.json, never invented — the frames only
diverge where optimal_swell_dir actually differs from orientation_deg, and a synthetic
fixture would be free to make that gap whatever flattered the change.

Run: python -m pipeline.tests.test_directional_gain_frame   (or pytest)
"""
from __future__ import annotations

import datetime
import json
import math
import os

from pipeline.interpret import directional_gain
from pipeline.forecast import mop, nwps_nearshore
from pipeline.forecast.mop import mop_stars
from pipeline.forecast.nwps_nearshore import nwps_stars

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_BASE = 1767225600                     # 2026-01-01T00:00:00Z, epoch seconds


def _iso(epoch):
    return datetime.datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%dT%H:00:00Z")


def _close(a, b, tol=1e-12):
    return abs(a - b) <= tol


# --------------------------------------------------------------------------- #
# Live geometry. Every number here is copied out of pipeline/spots_enriched.json. #
# --------------------------------------------------------------------------- #

# North Buxton, NC. One very wide arc, and optimal (120) is NOT the orientation (98).
#   arc {9, 231, span 226}: width (231-9) = 222, pad = (226-222)/2 = 2, sector [7, 233].
# Wide enough to hold optimal +/- 90 INSIDE the window, which is the only way to pin the
# in-window kernel out to 90° off without leaving the branch under test.
NB_ARCS = [{"min": 9, "max": 231, "span": 226}]
NB_OPTIMAL = 120
NB_ORIENT = 98.0

# Steamer Lane, CA. Two arcs; optimal 244 sits 116° away from orientation 128, the widest
# frame gap in the roster's top handful — which is exactly why it exposes the defect.
#   arc1 {181, 251, span 74}: width 70, pad = (74-70)/2 = 2, sector [179, 253]
#   arc2 {317, 331, span 18}: width 14, pad = (18-14)/2 = 2, sector [315, 333]
SL_ARCS = [{"min": 181, "max": 251, "span": 74}, {"min": 317, "max": 331, "span": 18}]
SL_OPTIMAL = 244
SL_ORIENT = 128.0

# San Simeon, CA. optimal_swell_dir == orientation_deg == 220 — the no-gap case.
#   arc1 {101, 111, span 14}: width 10, pad 2, sector [ 99, 113]
#   arc2 {157, 287, span 134}: width 130, pad 2, sector [155, 289]
#   arc3 {333, 351, span 22}: width 18, pad 2, sector [331, 353]
SS_ARCS = [{"min": 101, "max": 111, "span": 14},
           {"min": 157, "max": 287, "span": 134},
           {"min": 333, "max": 351, "span": 22}]
SS_OPTIMAL = 220
SS_ORIENT = 220.0

# 15th Street Del Mar, CA. A real MOP spot: mop_shore_normal 264.5299987792969 is what
# mop_stars used to score against, while its optimal_swell_dir is 212.
#   arc1 { 93, 151, span  62}: width  58, pad 2, sector [ 91, 153]
#   arc2 {193, 311, span 122}: width 118, pad 2, sector [191, 313]
DM_ARCS = [{"min": 93, "max": 151, "span": 62}, {"min": 193, "max": 311, "span": 122}]
DM_OPTIMAL = 212
DM_SHORE_NORMAL = 264.5299987792969


# --------------------------------------------------------------------------- #
# 1 — the in-window kernel is the HALF-ANGLE form                              #
# --------------------------------------------------------------------------- #
def test_the_fixtures_have_the_geometry_these_tests_assume():
    """Guard the premises. If a pad or a frame gap differs from what is written above,
    every expectation below shifts and the failure should say so here, not there.
        North Buxton  : pad (226-222)/2 = 2, and optimal 120 != orientation 98
        Steamer Lane  : pads (74-70)/2 = 2 and (18-14)/2 = 2, optimal 244 != orient 128
        San Simeon    : optimal 220 == orientation 220, the deliberate no-gap case
        Del Mar       : optimal 212 != shore normal 264.53
    """
    def pad(a):
        return (float(a["span"]) - ((float(a["max"]) - float(a["min"])) % 360.0)) / 2.0

    assert pad(NB_ARCS[0]) == 2.0
    assert pad(SL_ARCS[0]) == 2.0 and pad(SL_ARCS[1]) == 2.0
    assert [pad(a) for a in SS_ARCS] == [2.0, 2.0, 2.0]
    assert [pad(a) for a in DM_ARCS] == [2.0, 2.0]
    assert NB_OPTIMAL != NB_ORIENT
    assert SL_OPTIMAL != SL_ORIENT
    assert float(SS_OPTIMAL) == SS_ORIENT          # the control
    assert DM_OPTIMAL != DM_SHORE_NORMAL


def test_the_fixtures_are_copied_from_the_live_roster_not_invented():
    """Backs the claim in this module's docstring. Every fixture above is asserted to be
    byte-equal to what pipeline/spots_enriched.json holds for that spot. If the roster is
    re-enriched and a window moves, this fails loudly instead of leaving the tests pinning
    a geometry that no longer exists anywhere."""
    spots = json.load(open(os.path.join(_REPO, "pipeline", "spots_enriched.json")))
    by_name = {s.get("name"): s for s in spots}
    for name, arcs, opt, target_key, target in (
        ("North Buxton", NB_ARCS, NB_OPTIMAL, "orientation_deg", NB_ORIENT),
        ("Steamer Lane", SL_ARCS, SL_OPTIMAL, "orientation_deg", SL_ORIENT),
        ("San Simeon", SS_ARCS, SS_OPTIMAL, "orientation_deg", SS_ORIENT),
        ("15th Street Del Mar", DM_ARCS, DM_OPTIMAL, "mop_shore_normal", DM_SHORE_NORMAL),
    ):
        s = by_name.get(name)
        assert s is not None, f"fixture spot vanished from the roster: {name}"
        assert s.get("swell_window_arcs") == arcs, (name, s.get("swell_window_arcs"))
        assert s.get("optimal_swell_dir") == opt, (name, s.get("optimal_swell_dir"))
        assert s.get(target_key) == target, (name, target_key, s.get(target_key))


def test_in_window_kernel_is_cos_squared_half_the_offset_from_optimal():
    """THE KERNEL PIN. North Buxton's sector is [7, 233] and optimal is 120, so 120 +/- 90
    is still inside the window and all four points exercise the IN-WINDOW branch.

    diff = ((dp - 120 + 540) mod 360) - 180, then gain = max(0.25, cos²(diff / 2)):

        dp 120, off  0 : cos²( 0°) = 1.000000000000  (old cos²( 0°) = 1.000  — same)
        dp 150, off 30 : cos²(15°) = 0.933012701892  (old cos²(30°) = 0.750)
        dp 180, off 60 : cos²(30°) = 0.750000000000  (old cos²(60°) = 0.250)
        dp 210, off 90 : cos²(45°) = 0.500000000000  (old cos²(90°) = 0.000 -> floor 0.250)

        cos(15°) = 0.965925826289, squared = 0.933012701892
        cos(30°) = 0.866025403784, squared = 0.750000000000
        cos(45°) = 0.707106781187, squared = 0.500000000000

    The 0° point is deliberately included and deliberately does NOT discriminate: both
    forms peak at 1.0 there. The other three do, by 0.183, 0.500 and 0.250 — a mutant
    reverting the half-angle to cos²(diff) fails all three."""
    assert _close(directional_gain(120.0, NB_ARCS, NB_OPTIMAL, NB_ORIENT), 1.0)
    assert _close(directional_gain(150.0, NB_ARCS, NB_OPTIMAL, NB_ORIENT), 0.933012701892)
    assert _close(directional_gain(180.0, NB_ARCS, NB_OPTIMAL, NB_ORIENT), 0.750000000000)
    assert _close(directional_gain(210.0, NB_ARCS, NB_OPTIMAL, NB_ORIENT), 0.500000000000)
    # and explicitly NOT the full-angle values, so a reverted kernel cannot pass on tolerance
    for dp, old in ((150.0, 0.75), (180.0, 0.25), (210.0, 0.25)):
        got = directional_gain(dp, NB_ARCS, NB_OPTIMAL, NB_ORIENT)
        assert abs(got - old) > 0.15, (dp, got, old)


def test_the_kernel_is_symmetric_about_optimal():
    """Sign of the offset must not matter — 120-30 = 90 scores what 120+30 = 150 scores.
    Both are inside [7, 233].
        dp  90: diff = ((90 - 120 + 540) mod 360) - 180 = -30 -> cos²(-15°) = 0.933012701892
        dp  60: diff = -60 -> cos²(-30°) = 0.750000000000
        dp  30: diff = -90 -> cos²(-45°) = 0.500000000000
    """
    assert _close(directional_gain(90.0, NB_ARCS, NB_OPTIMAL, NB_ORIENT), 0.933012701892)
    assert _close(directional_gain(60.0, NB_ARCS, NB_OPTIMAL, NB_ORIENT), 0.750000000000)
    assert _close(directional_gain(30.0, NB_ARCS, NB_OPTIMAL, NB_ORIENT), 0.500000000000)


def test_both_branches_now_share_one_curve():
    """The whole point of the kernel change: in-window and no-arcs differ only in TARGET
    and GATING, never in shape. Same dp, same target, one with arcs and one without —
    identical to the last bit.
        dp 180 vs optimal 120: diff 60 -> cos²(30°) = 0.750000000000 either way
        dp 210 vs optimal 120: diff 90 -> cos²(45°) = 0.500000000000 either way
    Under the old code the arc branch gave 0.250 and 0.250 for these while the no-arcs
    branch gave 0.750 and 0.500 — the same bearing scored differently depending only on
    whether the spot happened to have a window."""
    for dp in (150.0, 180.0, 210.0, 90.0, 60.0, 30.0):
        gated = directional_gain(dp, NB_ARCS, NB_OPTIMAL, NB_ORIENT)
        ungated = directional_gain(dp, [], NB_OPTIMAL, NB_ORIENT)
        assert _close(gated, ungated, tol=1e-15), (dp, gated, ungated)


def test_the_in_window_floor_is_still_zero_point_two_five():
    """Untouched by this work, and the half-angle curve still reaches it: cos²(diff/2)
    hits 0 only at diff = +/-180.
        North Buxton sector [7, 233]; take optimal 30 and dp 210 -> diff 180
        cos²(90°) = 0.0 -> max(0.25, 0.0) = 0.25
    The floor is what stops a fully-reversed swell inside a very wide window from
    zeroing the rating outright."""
    assert directional_gain(210.0, NB_ARCS, 30, NB_ORIENT) == 0.25


# --------------------------------------------------------------------------- #
# 2 — nwps_stars scores in the ARC frame                                       #
# --------------------------------------------------------------------------- #
def test_nwps_stars_scores_with_the_spots_arcs_and_optimal():
    """THE FIX. Steamer Lane, live geometry, at the WW3 partition bearing dp = 220.56.

    NEW — arc branch, target = optimal 244:
        220.56 is inside arc1's sector [179, 253]
        diff = ((220.56 - 244 + 540) mod 360) - 180 = -23.44
        cos(-23.44/2°) = cos(-11.72°) = 0.979152476, squared = 0.958738570261
        floor max(0.25, 0.958738570261) does not bite

    OLD — no-arcs branch, target = orientation 128 (arcs were []):
        diff = ((220.56 - 128 + 540) mod 360) - 180 = 92.56
        cos²(46.28°) = 0.477667217946

    A swell 23° off the spot's true optimal was being scored as though it were 93° off,
    because it was measured against the shore orientation instead. 0.959 vs 0.478 — the
    partition combine_ww3_partitions had already chosen ON THAT SAME 244 TARGET."""
    hs, per, swell_hs = 2.0, 14.0, 1.8
    _, _, dg, _, _ = nwps_stars(hs, per, 220.56, swell_hs, SL_ORIENT,
                                arcs=SL_ARCS, optimal=SL_OPTIMAL)
    assert _close(dg, 0.958738570261), dg
    assert abs(dg - 0.477667217946) > 0.4, dg          # unmistakably not the old frame


def test_nwps_stars_without_geometry_still_behaves_exactly_as_before():
    """The new parameters default to None so a caller that has no spot geometry — the
    module selftest, any diagnostic — is bit-for-bit unchanged. Same inputs as above:
        arcs default [] -> no-arcs branch, target = orientation 128
        diff 92.56 -> cos²(46.28°) = 0.477667217946
    This is the legacy value, pinned so the defaults can never silently drift into the
    new frame for callers that never opted in."""
    hs, per, swell_hs = 2.0, 14.0, 1.8
    _, _, dg, _, _ = nwps_stars(hs, per, 220.56, swell_hs, SL_ORIENT)
    assert _close(dg, 0.477667217946), dg


def test_nwps_stars_passes_arcs_through_and_not_an_empty_list():
    """Distinguishes "the arcs reached directional_gain" from "the arcs were accepted and
    dropped". A bearing OUTSIDE every Steamer Lane arc must land on the soft-outside
    ladder, which is unreachable when arcs are [].
        dp 273: 20° past arc1's padded edge 253; arc2's edge 315 is 42° away
                min offset 20 -> band <45° -> 0.40 exactly
        with arcs=[] the same dp takes the no-arcs branch instead:
                diff = ((273 - 128 + 540) mod 360) - 180 = 145
                cos²(72.5°) = 0.090423977856 -> floored to 0.25
    0.40 is a ladder constant and 0.25 is the floor; neither can be reached by accident
    from the other branch."""
    hs, per, swell_hs = 2.0, 14.0, 1.8
    _, _, with_arcs, _, _ = nwps_stars(hs, per, 273.0, swell_hs, SL_ORIENT,
                                       arcs=SL_ARCS, optimal=SL_OPTIMAL)
    _, _, no_arcs, _, _ = nwps_stars(hs, per, 273.0, swell_hs, SL_ORIENT)
    assert with_arcs == 0.40, with_arcs
    assert no_arcs == 0.25, no_arcs


def test_apply_nwps_overrides_hands_the_spots_geometry_to_the_scorer():
    """THE CALL SITE, which is where the defect actually lived. nwps_stars gaining two
    parameters fixes nothing if apply_nwps_overrides does not pass them, so drive the real
    override over a Steamer-Lane-shaped spot and read dir_gain off the entry it writes.

    Both entries carry a WW3-resolved direction (swell_source "ww3", so
    _ww3_swell_identity returns it and the NWPS dirpw of 255 is not substituted).

    HOUR 0, dp 220.56, INSIDE arc1's sector [179, 253] — proves `optimal` arrives:
        diff vs optimal 244 = -23.44 -> cos²(-11.72°) = 0.958738570261 -> round 0.959
        before this change: diff vs orientation 128 = 92.56 -> 0.477667217946 -> 0.478

    HOUR 1, dp 273.0, OUTSIDE every arc — proves `arcs` arrives too, which hour 0 alone
    cannot: with the arcs dropped but optimal still passed, 273 takes the no-arcs branch
    about 244 and scores cos²(14.5°) = 0.937309853570 -> 0.937. Only the ladder gives 0.4.
        20° past arc1's padded edge 253 -> band <45° -> 0.40"""
    spot = {"name": "T", "swell_window_source": "nwps", "nwps_wfo": "mtr",
            "orientation_deg": SL_ORIENT,
            "swell_window_arcs": SL_ARCS, "optimal_swell_dir": SL_OPTIMAL}
    inside = {"valid_time": _iso(_BASE), "stars": 1.0, "wind_mult": 0.85, "tide_mult": 0.72,
              "swell_source": "ww3", "swell_dp": 220.56, "swell_tp": 15.0}
    outside = {"valid_time": _iso(_BASE + 3600), "stars": 1.0, "wind_mult": 0.85,
               "tide_mult": 0.72, "swell_source": "ww3", "swell_dp": 273.0, "swell_tp": 15.0}
    series = {_BASE // 3600: (1.6, 7.8, 255.0, 1.1),
              (_BASE + 3600) // 3600: (1.6, 7.8, 255.0, 1.1)}
    stats = nwps_nearshore.apply_nwps_overrides(
        {"T": [inside, outside]}, [spot], _fetch=lambda _s: series)

    assert stats["fed"] == 1, stats
    assert inside["dir_gain"] == 0.959, inside["dir_gain"]
    assert inside["dir_gain"] != 0.478, "the call site is still scoring in the orientation frame"
    assert outside["dir_gain"] == 0.40, outside["dir_gain"]
    assert outside["dir_gain"] != 0.937, "the call site is not passing the arcs through"


def test_nwps_stars_optimal_alone_moves_the_target_even_with_no_arcs():
    """The two new parameters are independent. optimal without arcs still retargets the
    no-arcs branch from orientation to optimal:
        dp 220.56 vs optimal 244 -> diff -23.44 -> cos²(-11.72°) = 0.958738570261
    Same value as the arc case here only because 220.56 is well inside the window; what
    it proves is that `optimal` is not being ignored when arcs are absent."""
    hs, per, swell_hs = 2.0, 14.0, 1.8
    _, _, dg, _, _ = nwps_stars(hs, per, 220.56, swell_hs, SL_ORIENT, optimal=SL_OPTIMAL)
    assert _close(dg, 0.958738570261), dg


# --------------------------------------------------------------------------- #
# 3 — a spot whose optimal equals its orientation is unaffected                 #
# --------------------------------------------------------------------------- #
def test_optimal_equal_to_orientation_is_unaffected_in_window():
    """San Simeon: optimal_swell_dir 220 == orientation_deg 220. For any bearing INSIDE
    the window the new code returns exactly what the old code returned, to the last bit —
    and it does so ONLY because the kernel change unified the two branches. Revert the
    in-window kernel to cos²(diff) and this equality breaks even though the target never
    moved.
        dp 200 is inside arc2's sector [155, 289]
        diff = ((200 - 220 + 540) mod 360) - 180 = -20
        NEW arc branch, target optimal 220 : cos²(-10°) = 0.969846310393
        OLD no-arcs branch, target orient 220: cos²(-10°) = 0.969846310393
        cos(10°) = 0.984807753012, squared = 0.969846310393
    """
    new = directional_gain(200.0, SS_ARCS, SS_OPTIMAL, SS_ORIENT)
    old_equivalent = directional_gain(200.0, [], SS_ORIENT, SS_ORIENT)
    assert _close(new, 0.969846310393), new
    assert new == old_equivalent, (new, old_equivalent)
    # through nwps_stars, the path that actually changed
    _, _, dg, _, _ = nwps_stars(1.5, 12.0, 200.0, 1.4, SS_ORIENT,
                                arcs=SS_ARCS, optimal=SS_OPTIMAL)
    _, _, dg_legacy, _, _ = nwps_stars(1.5, 12.0, 200.0, 1.4, SS_ORIENT)
    assert dg == dg_legacy == new, (dg, dg_legacy, new)


def test_optimal_equal_to_orientation_holds_across_the_whole_window():
    """Not a single lucky bearing: every 2° step across San Simeon's widest sector
    [155, 289] scores identically with and without arcs, because target and kernel now
    agree. Sampled, not asserted by formula — the formula is pinned above."""
    for dp in range(156, 289, 2):
        gated = directional_gain(float(dp), SS_ARCS, SS_OPTIMAL, SS_ORIENT)
        ungated = directional_gain(float(dp), [], SS_ORIENT, SS_ORIENT)
        assert gated == ungated, (dp, gated, ungated)


def test_optimal_equal_to_orientation_still_changes_outside_the_window():
    """The honest other half: "unaffected" means unaffected IN-WINDOW. Gating is new, so a
    bearing outside every arc does change even when optimal == orientation, and that is
    the intended behaviour rather than an escape.
        San Simeon padded sectors [99,113], [155,289], [331,353]; dp 60
            arc1: min(|60-99|, |60-113|)  = 39
            arc2: min( 95, 131)           = 95
            arc3: min( 89,  67)           = 67
            min offset 39 -> band <45° -> 0.40
        OLD no-arcs, target 220: diff = ((60-220+540) mod 360) - 180 = -160
            cos²(-80°) = 0.030153689607 -> floored to 0.25
    """
    assert directional_gain(60.0, SS_ARCS, SS_OPTIMAL, SS_ORIENT) == 0.40
    assert directional_gain(60.0, [], SS_ORIENT, SS_ORIENT) == 0.25


# --------------------------------------------------------------------------- #
# 4 — the soft-outside ladder is untouched, and now reachable                   #
# --------------------------------------------------------------------------- #
def test_the_soft_outside_ladder_fires_unchanged_well_outside_every_arc():
    """0.40 / 0.15 / 0.0 and their 45° / 90° band edges are all forbidden to move by this
    work. Steamer Lane padded sectors [179, 253] and [315, 333]; offsets are the smallest
    distance to any padded edge:

        dp 273 -> arc1 min(|273-179| = 94, |273-253| = 20) = 20
                  arc2 min(42, 60) = 42                     -> min 20  -> <45   -> 0.40
        dp 100 -> arc1 min(79, 153) = 79
                  arc2 min(145, 127) = 127                  -> min 79  -> 45-90 -> 0.15
        dp  76 -> arc1 min(103, 177) = 103
                  arc2 min(239->121, 257->103) = 103        -> min 103 -> >90   -> 0.00
    dp 76 sits in the middle of the 206°-wide blocked sector behind the headland, which
    is what >90° off is meant to describe."""
    assert directional_gain(273.0, SL_ARCS, SL_OPTIMAL, SL_ORIENT) == 0.40
    assert directional_gain(100.0, SL_ARCS, SL_OPTIMAL, SL_ORIENT) == 0.15
    assert directional_gain(76.0, SL_ARCS, SL_OPTIMAL, SL_ORIENT) == 0.0


def test_the_ladder_is_now_reachable_from_nwps_stars_at_all_three_rungs():
    """Before the fix nwps_stars passed [], so NO nwps spot-hour could ever take the
    ladder — the outside-the-window concept did not exist on that path. All three rungs
    now arrive intact, and each is far from what the old no-arcs branch returned:
        dp 273: ladder 0.40  vs old cos²(72.5°) = 0.090423977856 -> floored 0.25
        dp 100: ladder 0.15  vs old diff -28 -> cos²(-14°) = 0.941473796429
        dp  76: ladder 0.00  vs old diff -52 -> cos²(-26°) = 0.807830737663
    """
    hs, per, swell_hs = 2.0, 14.0, 1.8
    expected = {273.0: (0.40, 0.25), 100.0: (0.15, 0.941473796429),
                76.0: (0.0, 0.807830737663)}
    for dp, (rung, legacy) in expected.items():
        _, _, dg, _, _ = nwps_stars(hs, per, dp, swell_hs, SL_ORIENT,
                                    arcs=SL_ARCS, optimal=SL_OPTIMAL)
        _, _, old, _, _ = nwps_stars(hs, per, dp, swell_hs, SL_ORIENT)
        assert dg == rung, (dp, dg, rung)
        assert _close(old, legacy), (dp, old, legacy)


def test_the_ladder_band_edges_have_not_moved():
    """45° and 90° exactly, measured from the padded edge. Steamer Lane arc1 ends at 253:
        dp 297.99 -> offset 44.99 (arc2's edge 315 is 17.01 away, so arc2 wins at 17.01)
    — arc2 interferes, so pin the edges on a single-arc geometry instead. San Simeon
    arc2's sector is [155, 289] and its neighbours are 42° and 42° further out, so use a
    lone copy of it:
        dp 334.0 -> offset from 289 is 45.0  -> NOT <45  -> 0.15
        dp 333.9 -> offset 44.9              -> <45      -> 0.40
        dp 379 = 19 -> offset from 289 is 90.0 -> NOT <90 -> 0.00
        dp 18.9  -> offset 89.9              -> <90      -> 0.15
    """
    lone = [SS_ARCS[1]]                       # {157, 287, span 134} -> sector [155, 289]
    assert directional_gain(333.9, lone, SS_OPTIMAL, SS_ORIENT) == 0.40
    assert directional_gain(334.0, lone, SS_OPTIMAL, SS_ORIENT) == 0.15
    assert directional_gain(18.9, lone, SS_OPTIMAL, SS_ORIENT) == 0.15
    assert directional_gain(19.0, lone, SS_OPTIMAL, SS_ORIENT) == 0.0


# --------------------------------------------------------------------------- #
# 5 — mop_stars carries the identical fix                                      #
# --------------------------------------------------------------------------- #
def test_mop_stars_scores_with_the_spots_arcs_and_optimal():
    """15th Street Del Mar, a live MOP spot: mop_shore_normal 264.5299987792969 is what
    mop_stars used to score against; its optimal_swell_dir is 212.

    Take dp = 212, a swell arriving from EXACTLY the spot's optimal direction:
        NEW — inside arc2's sector [191, 313], target = optimal 212
              diff = 0 -> cos²(0°) = 1.0 exactly
        OLD — no-arcs branch, target = shore normal 264.5299987792969
              diff = ((212 - 264.53 + 540) mod 360) - 180 = -52.5299987792969
              cos²(-26.2649993896°) = 0.804172981826

    A perfectly on-axis swell was scoring 0.804 because the shore normal is 52.5° off the
    direction the window actually opens toward."""
    _, _, dg, _, _ = mop_stars(1.6, 14.0, 212.0, 1.5, DM_SHORE_NORMAL,
                               arcs=DM_ARCS, optimal=DM_OPTIMAL)
    assert dg == 1.0, dg
    _, _, old, _, _ = mop_stars(1.6, 14.0, 212.0, 1.5, DM_SHORE_NORMAL)
    assert _close(old, 0.804172981826), old


def test_mop_stars_without_geometry_still_behaves_exactly_as_before():
    """Defaults preserve the legacy frame for any caller with no spot dict.
        dp 265 vs shore normal 264.5299987792969
        diff = ((265 - 264.53 + 540) mod 360) - 180 = 0.4700012207031
        cos²(0.2350006104°) = 0.999983177510
    """
    _, _, dg, _, _ = mop_stars(1.6, 14.0, 265.0, 1.5, DM_SHORE_NORMAL)
    assert _close(dg, 0.999983177510), dg


def test_mop_stars_reaches_the_ladder_which_the_old_frame_could_not():
    """Del Mar padded sectors [91, 153] and [191, 313]. dp 350:
        arc1: min(|350-91| -> 101, |350-153| -> 163) = 101
        arc2: min(|350-191| -> 159, |350-313| ->  37) =  37
        min offset 37 -> band <45° -> 0.40
    OLD, no arcs, target shore normal 264.53:
        diff = ((350 - 264.5299987792969 + 540) mod 360) - 180 = 85.4700012207031
        cos(42.7350006104°) = 0.734500186, squared = 0.539490524203
    """
    _, _, dg, _, _ = mop_stars(1.6, 14.0, 350.0, 1.5, DM_SHORE_NORMAL,
                               arcs=DM_ARCS, optimal=DM_OPTIMAL)
    assert dg == 0.40, dg
    _, _, old, _, _ = mop_stars(1.6, 14.0, 350.0, 1.5, DM_SHORE_NORMAL)
    assert _close(old, 0.539490524203), old


def test_apply_mop_overrides_hands_the_spots_geometry_to_the_scorer():
    """The MOP call site, same reasoning and the same two-hour shape as the nwps one.

    HOUR 0, dp 212, EXACTLY on Del Mar's optimal and inside arc2's sector [191, 313]:
        diff 0 -> cos²(0°) = 1.0 -> round 1.0
        before this change, scored against shore normal 264.5299987792969:
        diff -52.529998779 -> cos²(-26.264999390°) = 0.804172981826 -> 0.804

    HOUR 1, dp 350, OUTSIDE every arc — the only hour that can tell "arcs passed" from
    "arcs dropped but optimal passed":
        arc2's padded edge 313 is 37° away -> band <45° -> 0.40
        with the arcs dropped: diff vs optimal 212 = 138 -> cos²(69°) = 0.128427587261
        -> floored to 0.25"""
    spot = {"name": "T", "swell_window_source": "cdip_mop",
            "mop_shore_normal": DM_SHORE_NORMAL,
            "swell_window_arcs": DM_ARCS, "optimal_swell_dir": DM_OPTIMAL}
    on_axis = {"valid_time": _iso(_BASE), "stars": 1.0, "wind_mult": 1.0, "tide_mult": 1.0}
    outside = {"valid_time": _iso(_BASE + 3600), "stars": 1.0,
               "wind_mult": 1.0, "tide_mult": 1.0}
    series = {_BASE // 3600: (1.6, 14.0, 212.0, 1.5),
              (_BASE + 3600) // 3600: (1.6, 14.0, 350.0, 1.5)}
    stats = mop.apply_mop_overrides({"T": [on_axis, outside]}, [spot], _fetch=lambda _s: series)

    assert stats["fed"] == 1, stats
    assert on_axis["dir_gain"] == 1.0, on_axis["dir_gain"]
    assert on_axis["dir_gain"] != 0.804, "the call site is still scoring in the shore-normal frame"
    assert outside["dir_gain"] == 0.40, outside["dir_gain"]
    assert outside["dir_gain"] != 0.25, "the call site is not passing the arcs through"


def test_mop_stars_unusable_inputs_still_short_circuit():
    """The guard clause is ahead of the gain and must stay there — passing arcs must not
    make a None-height hour rateable."""
    assert mop_stars(None, 14.0, 212.0, 1.5, DM_SHORE_NORMAL,
                     arcs=DM_ARCS, optimal=DM_OPTIMAL) == (None, None, None, None, None)
    assert mop_stars(1.6, 14.0, 212.0, 1.5, None,
                     arcs=DM_ARCS, optimal=DM_OPTIMAL) == (None, None, None, None, None)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} directional-gain-frame checks passed")


if __name__ == "__main__":
    _run_all()
