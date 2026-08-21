"""WW3 partition SELECTION is period-weighted; the combined HEIGHT is not.

THE DEFECT. combine_ww3_partitions scored candidates as `energy = hs * hs * gain`
with no period term, so a fat short-period windsea beat a lean groundswell every
time and the spot's published tp/dp described the chop rather than the surf. The
score now carries period_quality(tp) ** SELECTION_PERIOD_QUALITY_EXPONENT.

THE TRAP THIS FILE EXISTS TO HOLD SHUT. `energy` used to serve double duty — it
was both the selection key AND the term summed into combined_hs. Weighting it in
place would have silently rescaled every WW3-path face height in the roster. The
weight therefore lives in a separate `selection_score` field, and
test_combined_height_is_untouched_by_the_period_weight is the pin that catches it
leaking back.

EVERY EXPECTED VALUE BELOW IS HAND-COMPUTED, with the arithmetic in a comment, and
both the OLD and NEW score sets are stated so a reader can see which way each
candidate moved. None is derived by calling combine_ww3_partitions.

GEOMETRY IS LIVE, read from spots_enriched.json — including the `span` on each arc,
which the arc's pad is derived from and which a hand-written fixture would be free
to get wrong.

Run: python -m pipeline.tests.test_ww3_period_weighted_selection   (or pytest)
"""
from __future__ import annotations

import json
import math
import os

from pipeline.interpret import (
    SELECTION_PERIOD_QUALITY_EXPONENT,
    combine_ww3_partitions,
    period_quality,
)

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _close(a, b, tol=1e-12):
    return abs(a - b) <= tol


def _entry(*parts):
    """(hs, tp, dp) triples -> a WW3 entry dict shaped like the frozen file's records."""
    e = {}
    for i, (hs, tp, dp) in enumerate(parts, start=1):
        e[f"swell_{i}_hs"], e[f"swell_{i}_tp"], e[f"swell_{i}_dp"] = hs, tp, dp
    return e


# --------------------------------------------------------------------------- #
# Live geometry, copied from pipeline/spots_enriched.json                       #
# --------------------------------------------------------------------------- #

# Lower Trestles. All three arcs are step-4 raycast, pad = (span - width)/2 = 2:
#   {93,115,span 26}   width 22  -> sector [ 91, 117]
#   {165,291,span 130} width 126 -> sector [163, 293]
#   {325,343,span 22}  width 18  -> sector [323, 345]
LT_ARCS = [{"min": 93, "max": 115, "span": 26},
           {"min": 165, "max": 291, "span": 130},
           {"min": 325, "max": 343, "span": 22}]
LT_OPTIMAL = 228
LT_ORIENT = 205.0

# Huntington Beach Pier. Four arcs, every pad 2:
#   { 93,111,span 22} width 18 -> sector [ 91, 113]
#   {145,211,span 70} width 66 -> sector [143, 213]
#   {241,275,span 38} width 34 -> sector [239, 277]
#   {325,343,span 22} width 18 -> sector [323, 345]
HB_ARCS = [{"min": 93, "max": 111, "span": 22},
           {"min": 145, "max": 211, "span": 70},
           {"min": 241, "max": 275, "span": 38},
           {"min": 325, "max": 343, "span": 22}]
HB_OPTIMAL = 204
HB_ORIENT = 220.0


def test_the_fixtures_are_copied_from_the_live_roster_not_invented():
    """Backs the claim in the docstring. Arc bounds AND spans, optimal, orientation —
    all asserted byte-equal to the roster, because the pad is derived from span and a
    wrong span moves every gain below."""
    spots = json.load(open(os.path.join(_REPO, "pipeline", "spots_enriched.json")))
    by_name = {s.get("name"): s for s in spots}
    for name, arcs, opt, orient in (
        ("Lower Trestles", LT_ARCS, LT_OPTIMAL, LT_ORIENT),
        ("Huntington Beach Pier", HB_ARCS, HB_OPTIMAL, HB_ORIENT),
    ):
        s = by_name.get(name)
        assert s is not None, f"fixture spot vanished from the roster: {name}"
        assert s.get("swell_window_arcs") == arcs, (name, s.get("swell_window_arcs"))
        assert s.get("optimal_swell_dir") == opt, (name, s.get("optimal_swell_dir"))
        assert s.get("orientation_deg") == orient, (name, s.get("orientation_deg"))


# --------------------------------------------------------------------------- #
# 1 — period_quality itself, pinned by exact value                             #
# --------------------------------------------------------------------------- #
def test_period_quality_is_pinned_at_the_periods_this_selection_depends_on():
    """The selection score is now a function of period_quality, so its curve is
    load-bearing here and not only in composite_stars. _PERIOD_QUALITY_POINTS =
    [(0,.5),(6,.5),(7,.6),(8,.7),(9,.8),(10,.85),(11,.9),(12,.95),(13,1.0),
     (16,1.05),(99,1.05)], piecewise-linear.

        tp  5.2 -> inside the flat [0, 6] segment          -> 0.5 exactly
        tp 14.0 -> between (13, 1.0) and (16, 1.05)
                   1.0 + (14.0-13)/(16-13) * 0.05 = 1.0 + (1/3)(0.05) = 1.016666666667
        tp 15.1 -> 1.0 + (2.1/3)(0.05) = 1.0 + 0.035      = 1.035 exactly
    """
    assert period_quality(5.2) == 0.5
    assert _close(period_quality(14.0), 1.0166666666666666)
    assert _close(period_quality(15.1), 1.035)
    # the two ends of the curve, so a table edit cannot slide past on the middle alone
    assert period_quality(0.0) == 0.5           # MINIMUM
    assert period_quality(99.0) == 1.05         # MAXIMUM, the only factor above 1.0


def test_the_exponent_is_named_and_is_two():
    """An empirical calibration, not a physical constant — pinned so a change has to
    be deliberate. Sweeping it is the documented way to move it; editing the literal
    at the call site is not."""
    assert SELECTION_PERIOD_QUALITY_EXPONENT == 2


def test_the_named_constant_is_the_one_actually_used():
    """The constant must be READ at the call site, not merely declared beside an inline
    `** 2`. Rebind it to 1 and Lower Trestles must revert to the old winner; a hardcoded
    literal ignores the rebind and keeps flipping.

    This also pins the sweep result the constant's comment cites — that exponent 1 is
    too weak to flip the controls:
        s1  0.305407202438 * 0.5            = 0.152703601219   <- still the winner
        s2  0.135948816204 * 1.016666666667 = 0.138214629808
        s3  0.122015004017 * 1.035          = 0.126285529158
    """
    import pipeline.interpret as I
    saved = I.SELECTION_PERIOD_QUALITY_EXPONENT
    try:
        I.SELECTION_PERIOD_QUALITY_EXPONENT = 1
        r = I.combine_ww3_partitions(LT_ENTRY, LT_ARCS, LT_OPTIMAL, LT_ORIENT)
        assert r["dominant_partition"] == "swell_1", (
            "exponent 1 should be too weak to flip Lower Trestles; either the sweep "
            f"claim is wrong or the constant is not being read: {r['dominant_partition']}")
        by = {c["partition"]: c for c in r["contributions"]}
        assert _close(by["swell_1"]["selection_score"], 0.152703601219)
        assert _close(by["swell_2"]["selection_score"], 0.138214629808)
        assert _close(by["swell_3"]["selection_score"], 0.126285529158)
    finally:
        I.SELECTION_PERIOD_QUALITY_EXPONENT = saved
    # and back to 2, the flip returns
    assert combine_ww3_partitions(
        LT_ENTRY, LT_ARCS, LT_OPTIMAL, LT_ORIENT)["dominant_partition"] == "swell_2"


# --------------------------------------------------------------------------- #
# 2 — Lower Trestles: the control that flips                                   #
# --------------------------------------------------------------------------- #
# Record 08-19T06:00 from the frozen WW3 file:
#   swell_1  0.59 m / 5.2 s / 269°     swell_2  0.38 m / 14.0 s / 200°
#   swell_3  0.36 m / 15.1 s / 200°
#
# Gains, optimal 228, half-angle in-window kernel max(0.25, cos²(diff/2)):
#   s1 dp 269, inside arc2's sector [163, 293]. diff = 269-228 = 41
#      cos²(20.5°) = 0.877354790111
#   s2 dp 200, inside [163, 293]. diff = -28 -> cos²(-14°) = 0.941473796429
#   s3 dp 200, same                           -> 0.941473796429
#
# period_quality: s1 0.5, s2 1.016666666667, s3 1.035
#
#            OLD  hs²·gain                    NEW  hs²·gain·pq²
#   s1   0.59²·0.877354790111 = 0.305407202438   ·0.25         = 0.076351800609
#   s2   0.38²·0.941473796429 = 0.135948816204   ·1.033611111  = 0.140518206971
#   s3   0.36²·0.941473796429 = 0.122015004017   ·1.071225     = 0.130705522678
#
# OLD winner: swell_1  (0.3054 — more than twice either groundswell)
# NEW winner: swell_2  (0.1405, ahead of s3's 0.1307 and s1's 0.0764)
# The 5.2 s windsea loses three quarters of its score to pq² = 0.25 and drops to last.
LT_ENTRY = _entry((0.59, 5.2, 269), (0.38, 14.0, 200), (0.36, 15.1, 200))


def test_lower_trestles_selection_flips_off_the_short_period_windsea():
    """THE FIX, on the control spot. The published tp/dp must describe the groundswell
    that is actually shaping the break, not the 5.2 s chop that merely carries more
    bulk energy."""
    r = combine_ww3_partitions(LT_ENTRY, LT_ARCS, LT_OPTIMAL, LT_ORIENT)
    assert r is not None
    assert r["dominant_partition"] == "swell_2", r["dominant_partition"]
    assert r["tp"] == 14.0, r["tp"]
    assert r["dp"] == 200.0, r["dp"]
    # explicitly NOT the old pick — a mutant that drops the weight lands back here
    assert r["dominant_partition"] != "swell_1"
    assert r["tp"] != 5.2


def test_lower_trestles_selection_scores_are_exactly_as_hand_computed():
    """Pins the score arithmetic itself, not just its argmax, so a weight that is
    applied with the wrong exponent or to the wrong field fails here even when the
    winner happens to survive."""
    r = combine_ww3_partitions(LT_ENTRY, LT_ARCS, LT_OPTIMAL, LT_ORIENT)
    by = {c["partition"]: c for c in r["contributions"]}
    assert _close(by["swell_1"]["selection_score"], 0.076351800609)
    assert _close(by["swell_2"]["selection_score"], 0.140518206971)
    assert _close(by["swell_3"]["selection_score"], 0.130705522678)
    # the unweighted energy field is still the OLD score, untouched
    assert _close(by["swell_1"]["energy"], 0.305407202438)
    assert _close(by["swell_2"]["energy"], 0.135948816204)
    assert _close(by["swell_3"]["energy"], 0.122015004017)


# --------------------------------------------------------------------------- #
# 3 — Huntington Beach Pier: the control that does NOT flip at exponent 2      #
# --------------------------------------------------------------------------- #
# swell_1 0.76 m / 5.6 s / 270°, swell_2 0.26 m / 13.4 s / 198°,
# swell_3 0.24 m / 15.8 s / 195°.
#
# Gains, optimal 204:
#   s1 dp 270, inside arc3's sector [239, 277]. diff = 66 -> cos²(33°)  = 0.703368321538
#   s2 dp 198, inside arc2's sector [143, 213]. diff = -6 -> cos²(-3°)  = 0.997260947684
#   s3 dp 195, inside [143, 213].               diff = -9 -> cos²(-4.5°)= 0.993844170298
#
# period_quality: s1 0.5, s2 1.006666666667, s3 1.046666666667
#
#            OLD  hs²·gain                    NEW  hs²·gain·pq²
#   s1   0.76²·0.703368321538 = 0.406265542520  ·0.25          = 0.101566385630
#   s2   0.26²·0.997260947684 = 0.067414840063  ·1.013377778   = 0.068316700813
#   s3   0.24²·0.993844170298 = 0.057245424209  ·1.095511111   = 0.062712998281
#
# OLD winner: swell_1.  NEW winner: swell_1 — IT DOES NOT FLIP AT EXPONENT 2.
#
# This is a real finding, not a gap in the test. The windsea leads by 7.10x on raw
# gain-weighted energy (0.4063 / 0.0572) because 0.76 m against 0.24 m is 10.0x in
# hs² before gain. The most the weight can claw back is (1.046666667/0.5)² = 4.38x.
# The flip threshold is the exponent N solving (0.4063/0.0572) = (1.046666667/0.5)**N,
# i.e. N = ln(7.0969)/ln(2.0933) = 2.653. Exponent 2 is below it; exponent 4 clears it.
# So this fixture pins the SHAPE of the effect — the gap narrows from 7.10x to 1.62x —
# and it is the test that fails if anyone raises the exponent to 4.
HB_ENTRY = _entry((0.76, 5.6, 270), (0.26, 13.4, 198), (0.24, 15.8, 195))


def test_huntington_narrows_the_gap_but_does_not_flip_at_exponent_two():
    """Honest pin of what exponent 2 actually does here. See the block comment above
    for the 2.653 flip threshold — this fixture is the guard on the exponent's upper
    side, the way Lower Trestles is the guard on its lower side."""
    r = combine_ww3_partitions(HB_ENTRY, HB_ARCS, HB_OPTIMAL, HB_ORIENT)
    assert r is not None
    assert r["dominant_partition"] == "swell_1", r["dominant_partition"]
    assert r["tp"] == 5.6, r["tp"]
    by = {c["partition"]: c for c in r["contributions"]}
    assert _close(by["swell_1"]["selection_score"], 0.101566385630)
    assert _close(by["swell_2"]["selection_score"], 0.068316700813)
    assert _close(by["swell_3"]["selection_score"], 0.062712998281)
    # the gap really does narrow: 7.0969x on the old score, 1.6196x on the new one
    old_ratio = by["swell_1"]["energy"] / by["swell_3"]["energy"]
    new_ratio = by["swell_1"]["selection_score"] / by["swell_3"]["selection_score"]
    assert _close(old_ratio, 7.096908584973, tol=1e-9), old_ratio
    assert _close(new_ratio, 1.619542812709, tol=1e-9), new_ratio
    assert new_ratio < old_ratio


# --------------------------------------------------------------------------- #
# 4 — the height must not move                                                 #
# --------------------------------------------------------------------------- #
def test_combined_height_is_untouched_by_the_period_weight():
    """THE LEAK PIN. combined_hs = sqrt(sum(hs² * gain)) with NO period term, on a
    fixture whose three partitions have wildly different periods (5.2 / 14.0 / 15.1),
    so any weight reaching the RMS sum moves the answer.

        s1  0.59² * 0.877354790111 = 0.305407202438
        s2  0.38² * 0.941473796429 = 0.135948816204
        s3  0.36² * 0.941473796429 = 0.122015004017
        sum                        = 0.563371022659
        sqrt                       = 0.750580457153

    If the weight were applied inside the sum instead, the total would be
    0.076351800609 + 0.140518206971 + 0.130705522678 = 0.347575530258 and the height
    would read 0.589555366 — a 21.5% drop across the whole WW3 path."""
    r = combine_ww3_partitions(LT_ENTRY, LT_ARCS, LT_OPTIMAL, LT_ORIENT)
    assert _close(r["hs"], 0.750580457153), r["hs"]
    assert not _close(r["hs"], 0.589555366, tol=1e-6), "the period weight leaked into the RMS sum"
    # and the same on Huntington, whose winner does NOT change — proving the height
    # pin is independent of whether the selection flipped
    #   0.406265542520 + 0.067414840063 + 0.057245424209 = 0.530925806793
    #   sqrt = 0.728646558211
    h = combine_ww3_partitions(HB_ENTRY, HB_ARCS, HB_OPTIMAL, HB_ORIENT)
    assert _close(h["hs"], 0.728646558211), h["hs"]


def test_combined_height_equals_the_rms_of_the_energy_field_itself():
    """Structural restatement: whatever the selection does, hs is the quadrature sum of
    the `energy` fields and nothing else. Catches a weight applied to `energy` at
    construction time, which the numeric pin above would also catch but this localises."""
    for entry, arcs, opt, orient in ((LT_ENTRY, LT_ARCS, LT_OPTIMAL, LT_ORIENT),
                                     (HB_ENTRY, HB_ARCS, HB_OPTIMAL, HB_ORIENT)):
        r = combine_ww3_partitions(entry, arcs, opt, orient)
        rms = math.sqrt(sum(c["hs"] ** 2 * c["gain"] for c in r["contributions"]))
        assert _close(r["hs"], rms, tol=1e-15), (r["hs"], rms)


# --------------------------------------------------------------------------- #
# 5 — the weight cancels when it cannot discriminate                           #
# --------------------------------------------------------------------------- #
def test_equal_periods_leave_the_winner_exactly_where_the_old_rule_put_it():
    """With every candidate at the same tp, period_quality is a common factor and the
    argmax is unchanged — the rule only ever reorders candidates that differ in period.

    All three at 12.0 s, on Lower Trestles geometry:
        pq(12.0) = 0.95 exactly (a table point, no interpolation), pq² = 0.9025
        s1 dp 269 gain 0.877354790111, s2/s3 dp 200 gain 0.941473796429
            s1  0.50² * 0.877354790111 = 0.219338697528  -> *0.9025 = 0.197953174519
            s2  0.40² * 0.941473796429 = 0.150635807429  -> *0.9025 = 0.135948816204
            s3  0.30² * 0.941473796429 = 0.084732641679  -> *0.9025 = 0.076471209115
        Same ordering both ways: s1 > s2 > s3. Winner swell_1 under either rule.
    """
    entry = _entry((0.50, 12.0, 269), (0.40, 12.0, 200), (0.30, 12.0, 200))
    r = combine_ww3_partitions(entry, LT_ARCS, LT_OPTIMAL, LT_ORIENT)
    assert r["dominant_partition"] == "swell_1", r["dominant_partition"]
    by = {c["partition"]: c for c in r["contributions"]}
    # every selection_score is its energy scaled by the SAME 0.9025 — a common factor
    for slot, energy in (("swell_1", 0.219338697528), ("swell_2", 0.150635807429),
                         ("swell_3", 0.084732641679)):
        assert _close(by[slot]["energy"], energy), (slot, by[slot]["energy"])
        assert _close(by[slot]["selection_score"], energy * 0.9025), slot
    # the ordering is identical under both scores
    order_old = sorted(by, key=lambda k: by[k]["energy"], reverse=True)
    order_new = sorted(by, key=lambda k: by[k]["selection_score"], reverse=True)
    assert order_old == order_new == ["swell_1", "swell_2", "swell_3"], (order_old, order_new)


def test_a_single_partition_is_unaffected_by_the_weight():
    """One candidate, so the argmax is forced and the weight cannot change anything.
    The height is still the plain RMS of that one partition:
        0.44² * 0.941473796429 = 0.182269326989, sqrt = 0.426930119561
    """
    entry = _entry((0.44, 9.0, 200))
    r = combine_ww3_partitions(entry, LT_ARCS, LT_OPTIMAL, LT_ORIENT)
    assert r["dominant_partition"] == "swell_1"
    assert r["tp"] == 9.0
    assert _close(r["hs"], 0.426930119561), r["hs"]


def test_the_weight_never_changes_which_partitions_qualify():
    """Membership is decided by hs/tp/dp validity and gain > 0, all upstream of the
    score. A 4.0 s partition scores pq(4.0)² = 0.25 of its energy but still CONTRIBUTES
    its full energy to the height — being outscored is not being excluded.
        s1 0.30² * 0.877354790111 = 0.078961931110   (4.0 s, pq 0.5)
        s2 0.20² * 0.941473796429 = 0.037658951857  (14.0 s)
        sum = 0.116620882967, sqrt = 0.341497998482
    """
    entry = _entry((0.30, 4.0, 269), (0.20, 14.0, 200))
    r = combine_ww3_partitions(entry, LT_ARCS, LT_OPTIMAL, LT_ORIENT)
    assert len(r["contributions"]) == 2, r["contributions"]
    assert {c["partition"] for c in r["contributions"]} == {"swell_1", "swell_2"}
    assert _close(r["hs"], 0.341497998482), r["hs"]
    # s1 keeps its full energy in the height while losing the selection
    by = {c["partition"]: c for c in r["contributions"]}
    assert _close(by["swell_1"]["energy"], 0.078961931110)
    #   0.078961931110 * 0.25 = 0.019740482778  vs  0.037658951857 * 1.033611111 = 0.038924711072
    assert _close(by["swell_1"]["selection_score"], 0.019740482778)
    assert r["dominant_partition"] == "swell_2", r["dominant_partition"]


def test_no_contributing_partition_still_returns_none():
    """The reject path sits entirely AHEAD of the score, so the weight cannot reach it:
    a missing / non-numeric / non-positive hs or tp is dropped before period_quality is
    ever called, and an entry with nothing left returns None rather than a zero-height
    dict. Driven here by unusable partition values rather than by a blocked bearing —
    Lower Trestles' padded sectors [91,117], [163,293] and [323,345] leave no bearing
    more than 90° from every edge, so on this geometry the ladder never reaches 0.0."""
    assert combine_ww3_partitions({}, LT_ARCS, LT_OPTIMAL, LT_ORIENT) is None
    assert combine_ww3_partitions(None, LT_ARCS, LT_OPTIMAL, LT_ORIENT) is None
    # non-positive hs and tp are rejected before the score
    assert combine_ww3_partitions(_entry((0.0, 14.0, 200)), LT_ARCS,
                                  LT_OPTIMAL, LT_ORIENT) is None
    assert combine_ww3_partitions(_entry((0.5, 0.0, 200)), LT_ARCS,
                                  LT_OPTIMAL, LT_ORIENT) is None


def test_a_blocked_partition_is_dropped_before_it_can_be_scored():
    """gain > 0 is a membership test, upstream of the score, and stays there — a
    partition >90° outside the window is PHYSICALLY BLOCKED and must not enter the
    height sum at all, however long its period.

    Ocean Beach SF, live geometry: arcs {153,159,span 10} and {189,295,span 110},
    both pad 2, so the padded sectors are [151, 161] and [187, 297]. Optimal 232.
    dp 44 sits in the middle of the 214°-wide blocked gap — 107° from the nearest
    edge either way — so the ladder's >90° rung gives gain 0.0 exactly.

        s1 dp  44, 16.0 s, 0.90 m -> gain 0.0 -> DROPPED (never scored, never summed)
        s2 dp 220, 12.0 s, 0.30 m -> diff -12 -> cos²(-6°) = 0.989073800367
           energy 0.30² * 0.989073800367 = 0.089016642033
        combined_hs = sqrt(0.089016642033) = 0.298356568610

    Without the guard the blocked 0.90 m partition would dominate on both scores and
    the height would read sqrt(0.81*0 + 0.089...) — the same, since gain 0 zeroes its
    energy — but it would WIN the selection at score 0, so tp/dp would describe a swell
    the headland blocks.
    """
    ob_arcs = [{"min": 153, "max": 159, "span": 10}, {"min": 189, "max": 295, "span": 110}]
    entry = _entry((0.90, 16.0, 44), (0.30, 12.0, 220))
    r = combine_ww3_partitions(entry, ob_arcs, 232, 259.0)
    assert len(r["contributions"]) == 1, r["contributions"]
    assert r["contributions"][0]["partition"] == "swell_2"
    assert r["dominant_partition"] == "swell_2", r["dominant_partition"]
    assert r["tp"] == 12.0 and r["dp"] == 220.0
    assert _close(r["hs"], 0.298356568610), r["hs"]
    # every blocked partition -> None, not a zero-height dict
    assert combine_ww3_partitions(_entry((0.90, 16.0, 44)), ob_arcs, 232, 259.0) is None


def test_wind_wave_is_still_excluded_from_the_swell_combine():
    """wind_wave is a separate channel — it already feeds chop_ratio and must not enter
    the swell combine, on either score. Untouched by this change and pinned so.

    Lower Trestles geometry, a big on-axis 4.0 s wind_wave alongside one real swell:
        swell_1  0.30 m / 14.0 s / 200 -> gain 0.941473796429
                 energy 0.30² * 0.941473796429 = 0.084732641679
                 combined_hs = sqrt(0.084732641679) = 0.291088717883
    If wind_wave were admitted it would add 0.80² * 0.941473796429 = 0.602543229715,
    taking the height to sqrt(0.687275871394) = 0.829021031937 and — at 4.0 s, pq 0.5,
    score 0.150635807429 — it would also outscore the swell's 0.087606... and steal
    tp/dp.
    """
    entry = _entry((0.30, 14.0, 200))
    entry.update(wind_wave_hs=0.80, wind_wave_tp=4.0, wind_wave_dp=200)
    r = combine_ww3_partitions(entry, LT_ARCS, LT_OPTIMAL, LT_ORIENT)
    assert [c["partition"] for c in r["contributions"]] == ["swell_1"], r["contributions"]
    assert r["dominant_partition"] == "swell_1"
    assert r["tp"] == 14.0
    assert _close(r["hs"], 0.291088717883), r["hs"]
    assert not _close(r["hs"], 0.829021031937, tol=1e-6), "wind_wave leaked into the combine"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} ww3-period-weighted-selection checks passed")


if __name__ == "__main__":
    _run_all()
