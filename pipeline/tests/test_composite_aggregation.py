"""Numeric pins for the rating composite — size_score, the four quality factors, and
composite_stars under WEIGHTED GEOMETRIC-MEAN aggregation.

WHY THIS FILE EXISTS. Before it, the whole scoring subsystem had ZERO numeric assertions:
every test in the repo checked stars only relatively (on-axis > off-axis, windy < neutral) or
against a fixture sentinel. _SIZE_POINTS, _CHOP_POINTS, _PERIOD_QUALITY_POINTS, the 0.5 zero
gate, the 1.0 floor and the 5.0 ceiling could all be edited without failing anything.

THE CHANGE THESE PIN. composite_stars used to combine the four quality factors as a RAW
PRODUCT, so four independent sub-1.0 factors compounded: a large clean-ish day at wind 0.44,
tide 0.6, chop 0.3, period 0.5 scored 5.0 * 0.0396 = 0.198 raw and bottomed out on the 1.0
floor regardless of size. They now combine as a weighted geometric mean with unit-sum
exponents (COMPOSITE_FACTOR_EXPONENTS), so the aggregate is a MEAN: the same hour scores
2.16 raw -> 2.0 stars, and — the property that motivates the whole change — all factors at 1.0
reproduces size_score exactly.

THE EXPONENTS ARE NO LONGER EQUAL. They were 0.25 each when this file was written; the
2026-08-25 penalty-ceiling sweep moved them to wind 0.35 / tide 0.15 / chop 0.25 / period 0.25,
so the aggregate is a WEIGHTED mean and the old shortcut of raising the plain product to a
single 0.25 power no longer applies. Every hand-computed expectation below was recomputed
against the new set; the star VALUES all happen to be unchanged, because quantising to 0.5
increments absorbs the raw differences, but the arithmetic behind them is not.

EVERY EXPECTED VALUE BELOW IS HAND-COMPUTED, with the arithmetic stated in a comment. None is
derived by calling the production function it checks; a test that computes its expectation from
the code under test pins nothing.

Run: python -m pipeline.tests.test_composite_aggregation   (or pytest)
"""
from __future__ import annotations

import inspect
import math

from pipeline import interpret as I


def _close(a, b, tol=1e-12):
    return abs(a - b) <= tol


# --------------------------------------------------------------------------- #
# The exponents themselves                                                     #
# --------------------------------------------------------------------------- #
def test_the_exponents_are_pinned_by_exact_value_and_sum_to_one():
    """THE TWO PROPERTIES THAT MATTER, pinned separately because they fail differently.

    VALUE — the set is 0.35 / 0.15 / 0.25 / 0.25 (wind / tide / chop / period). These are
    an operating point chosen by the 2026-08-25 penalty-ceiling sweep, NOT a derived truth,
    so a change must be deliberate and must fail here. The previous set was 0.25 across the
    board; reverting any single one of these to 0.25 breaks this assertion.

    SUM — 0.35 + 0.15 + 0.25 + 0.25 = 1.0, and in binary64 that is EXACT (the residual is
    0.0, not an epsilon). Unit sum is what makes the aggregate a weighted geometric MEAN
    rather than a product: it is the property that keeps size_score's scale, so all-factors-
    at-1.0 returns size_score untouched. If it drifts, every rating on the site silently
    rescales with no other symptom."""
    e = I.COMPOSITE_FACTOR_EXPONENTS
    assert set(e) == {"wind_mult", "tide_mult", "chop_mult", "period_quality"}, e
    assert e["wind_mult"] == 0.35, e["wind_mult"]
    assert e["tide_mult"] == 0.15, e["tide_mult"]
    assert e["chop_mult"] == 0.25, e["chop_mult"]
    assert e["period_quality"] == 0.25, e["period_quality"]
    # exact, not approximate — 0.35 + 0.15 + 0.25 + 0.25 lands on 1.0 with zero residual
    assert sum(e.values()) == 1.0, sum(e.values())
    assert abs(sum(e.values()) - 1.0) == 0.0, abs(sum(e.values()) - 1.0)
    # WIND OUTWEIGHS TIDE — that asymmetry IS the change, and equal weights must not return
    assert e["wind_mult"] > e["tide_mult"], e
    assert e["wind_mult"] != 0.25 and e["tide_mult"] != 0.25, "the old equal-weight set is back"
    # the epsilon floor is numeric, and far below every production factor minimum
    # (wind 0.44, tide 0.6, chop 0.3, period 0.5) so it is inert on real paths
    assert 0.0 < I._FACTOR_EPSILON <= 1e-6, I._FACTOR_EPSILON
    assert I._FACTOR_EPSILON < 0.3


def test_each_factor_is_raised_to_its_own_exponent_not_a_neighbours():
    """THE WIRING, not just the values. composite_stars looks each exponent up by key:

        max(_FACTOR_EPSILON, wind_mult) ** e["wind_mult"]
        max(_FACTOR_EPSILON, tide_mult) ** e["tide_mult"]   ...

    Now that the four are no longer equal, a mis-keyed lookup is a real defect that the
    value pin above cannot see — the dict would still hold 0.35/0.15/0.25/0.25 while the
    tide factor was raised to wind's power.

    Most cases cannot detect it, because quantising to 0.5 increments absorbs the
    difference whenever the mis-keyed factor is near 1.0. These two are chosen to
    discriminate, one per direction:

      TIDE at its 0.6 minimum, eff 2.0 (size_score 2.0), everything else neutral:
        correct  0.6**0.15 = 0.9262381985
                 raw = 2.0 * 0.9262381985 = 1.8524763971
                 raw*2 = 3.7049527942 -> round -> 4 -> 4/2 = 2.0
        if tide were raised to wind's 0.35 instead:
                 0.6**0.35 = 0.8362823629
                 raw = 1.6725647257 -> raw*2 = 3.3451294514 -> round -> 3 -> 1.5
        A half-star apart, so the key matters here.

      WIND at its 0.550 minimum, eff 10.0 (size_score 5.0) — the mirror case, already
      pinned in the penalty-ceiling test: correct 0.550**0.35 -> 4.0; if wind were raised
      to tide's 0.15, 0.550**0.15 = 0.9142277582, raw 4.5711387912 -> 4.5.
    """
    assert I.composite_stars(2.0, 1.0, 0.6, 1.0, 1.0) == 2.0, "tide must use its OWN 0.15"
    assert I.composite_stars(10.0, 0.550, 1.0, 1.0, 1.0) == 4.0, "wind must use its OWN 0.35"
    # the two hand-computed alternatives, so the comparison is stated and not just implied
    assert _close(0.6 ** 0.15, 0.9262381985, tol=1e-10)
    assert _close(0.6 ** 0.35, 0.8362823629, tol=1e-10)
    assert _close(0.550 ** 0.15, 0.9142277582, tol=1e-10)
    assert round(2.0 * 0.6 ** 0.35 * 2) / 2 == 1.5, "the mis-keyed tide value this rejects"
    assert round(5.0 * 0.550 ** 0.15 * 2) / 2 == 4.5, "the mis-keyed wind value this rejects"


def test_the_unit_sum_invariant_is_actually_asserted_at_import():
    """The guard that stands between a bad edit and a silent rescale of every rating on the
    site. It is defensive, so with correct values its presence changes nothing observable —
    which is exactly why it needs pinning by source: deleting it is invisible until someone
    also edits the exponents, and then the failure is a quiet global rescale rather than an
    error. Checked against the module SOURCE because a passing assert leaves no trace.

    Its own comment records the limit honestly: a bare `assert` is stripped under `python -O`,
    so this guards development, not a hardened runtime."""
    src = inspect.getsource(I)
    assert "assert abs(sum(COMPOSITE_FACTOR_EXPONENTS.values()) - 1.0) < 1e-12" in src, \
        "the unit-sum import-time invariant was removed or weakened"
    assert "must sum to 1.0 or the aggregate stops being a" in src, \
        "the invariant's failure message must explain the consequence"
    assert "stripped under `python -O`" in src, \
        "the assert's own limitation must stay documented next to it"
    # and the invariant it guards actually holds right now
    assert abs(sum(I.COMPOSITE_FACTOR_EXPONENTS.values()) - 1.0) < 1e-12


def test_the_exponents_carry_their_provenance_and_its_caveats():
    """These four numbers are an OPERATING POINT, not a derived truth, and the block comment
    above them is the only place that says so. It is a `#` comment, not a docstring, so it
    does not survive into __doc__ and can only be checked against the module SOURCE — which
    is worth doing, because a future editor who strips the reasoning leaves behind four
    magic numbers that look derived.

    Three things the comment must keep, all of them required by the brief that set these
    values and none of them recoverable from the numbers themselves:
      * BOTH sweeps, and that they optimised for different criteria (floor vs penalty
        ceiling) — otherwise the 2026-08-21 equal-weight choice reads as simply wrong
        rather than as correct for the question it was asked
      * that wind-vs-tide is a JUDGEMENT the physics does not settle
      * what 0.50 would have bought and why it was rejected
      * the 247-hour sample caveat, so the percentages are not later quoted as precise
    """
    src = inspect.getsource(I)
    assert "2026-08-21" in src and "2026-08-25" in src, "both sweeps must be on the record"
    assert "optimising THE FLOOR" in src and "optimising THE PENALTY CEILING" in src, \
        "the two sweeps optimised different criteria and the comment must say which"
    assert "IS A JUDGEMENT, NOT A DERIVATION" in src, \
        "the wind-vs-tide weighting is not derivable from physics and must not read as if it were"
    assert "0.50/0.10 bought" in src and "REJECTED" in src, \
        "the rejected stronger candidate and its cost must stay recorded"
    assert "0.6**0.10 = 0.95" in src, "the arithmetic behind rejecting 0.50 must stay checkable"
    assert "only 247 hours" in src, "the blown-out band's sample size is a load-bearing caveat"
    assert "DIRECTION of the effect is firm" in src, \
        "the caveat must distinguish the firm direction from the imprecise magnitude"


# --------------------------------------------------------------------------- #
# 6 — size_score at every _SIZE_POINTS anchor, two midpoints, both clamps       #
# --------------------------------------------------------------------------- #
def test_size_score_at_every_anchor_point():
    """_SIZE_POINTS = [(0,0),(1,1),(2,2),(3,2.5),(4,3),(5,3.5),(6,4),(8,4.5),(10,5),(50,5)].
    At an anchor the interpolator returns that anchor's y exactly."""
    expected = {0.0: 0.0, 1.0: 1.0, 2.0: 2.0, 3.0: 2.5, 4.0: 3.0,
                5.0: 3.5, 6.0: 4.0, 8.0: 4.5, 10.0: 5.0, 50.0: 5.0}
    assert [p[0] for p in I._SIZE_POINTS] == sorted(expected), \
        "anchor set changed — update this test deliberately, not reflexively"
    for ft, want in expected.items():
        assert _close(I.size_score(ft), want), f"size_score({ft}) = {I.size_score(ft)}, want {want}"


def test_size_score_interpolated_midpoints():
    """Piecewise LINEAR between anchors.
      1.5 ft: between (1,1) and (2,2) -> 1 + (1.5-1)/(2-1) * (2-1)     = 1.5
      7.0 ft: between (6,4) and (8,4.5) -> 4 + (7-6)/(8-6) * (4.5-4)   = 4 + 0.25 = 4.25
      9.0 ft: between (8,4.5) and (10,5) -> 4.5 + (9-8)/(10-8) * 0.5   = 4.75
      0.5 ft: between (0,0) and (1,1) -> 0 + 0.5/1 * 1                 = 0.5
    """
    assert _close(I.size_score(1.5), 1.5)
    assert _close(I.size_score(7.0), 4.25)
    assert _close(I.size_score(9.0), 4.75)
    assert _close(I.size_score(0.5), 0.5)


def test_size_score_clamps_at_both_ends():
    """_interp clamps: below the first x it returns the first y, above the last the last y.
    Below 0 ft -> 0.0 (including negatives); above 50 ft -> 5.0."""
    assert _close(I.size_score(-5.0), 0.0)
    assert _close(I.size_score(-1e9), 0.0)
    assert _close(I.size_score(1000.0), 5.0)
    assert _close(I.size_score(1e9), 5.0)
    # 10 ft already reaches the ceiling; 10 -> 50 is a flat segment, not a ramp
    assert _close(I.size_score(10.0), 5.0) and _close(I.size_score(30.0), 5.0)


# --------------------------------------------------------------------------- #
# 7 — the four quality factors, at their implemented extremes                   #
# --------------------------------------------------------------------------- #
def test_wind_multiplier_pinned_including_its_true_minimum():
    """Bands: ang<30 ->1.2, <60 ->1.0, <120 ->0.8, <150 ->0.6, else 0.55.
    Then: light-wind blend (<5 m/s) toward 1.0; bonus capped to 1.0 above 15 m/s;
    offshore bonus capped to 0.8 when chop_ratio > 0.4; gale (>20 m/s) multiplies by 0.8.

    THE TRUE MINIMUM IS 0.44, NOT the 0.4 the function's docstring claims:
        onshore band 0.55, gale factor 0.8 -> 0.55 * 0.8 = 0.44
    """
    assert I.wind_multiplier(0.0, 10.0, None) == 1.0            # no offshore bearing -> neutral
    assert I.wind_multiplier(0.0, 10.0, 0.0) == 1.2             # dead offshore, moderate: MAX
    assert I.wind_multiplier(90.0, 10.0, 0.0) == 0.8            # 90 deg off -> the <120 band
    assert I.wind_multiplier(180.0, 10.0, 0.0) == 0.55          # dead onshore, no gale
    assert _close(I.wind_multiplier(180.0, 25.0, 0.0), 0.44)    # 0.55 * 0.8 = 0.44  <- MINIMUM
    assert _close(I.wind_multiplier(0.0, 20.1, 0.0), 0.8)       # 1.2 -> cap 1.0 -> gale 1.0*0.8
    # light-wind blend: 2.5 m/s -> blend 2.5/5 = 0.5; 1.0*0.5 + 0.55*0.5 = 0.775
    assert _close(I.wind_multiplier(180.0, 2.5, 0.0), 0.775)
    # THE BLEND BOUNDARY, pinned on both sides. The condition is `< 5.0`, so 5.0 itself does
    # NOT blend and the raw band value stands. 6.0 m/s is the discriminating input: if the
    # threshold ever moved up (say to 8.0) while the divisor stayed /5.0, 6.0 would blend with
    # blend = 6/5 = 1.2 > 1 and EXTRAPOLATE past neutral: 1.0*(1-1.2) + 0.55*1.2 = 0.46.
    assert _close(I.wind_multiplier(180.0, 5.0, 0.0), 0.55)    # boundary: no blend at exactly 5
    assert _close(I.wind_multiplier(180.0, 6.0, 0.0), 0.55)    # above the boundary: no blend
    # 4.9 m/s: blend 4.9/5 = 0.98 -> 1.0*0.02 + 0.55*0.98 = 0.02 + 0.539 = 0.559
    assert _close(I.wind_multiplier(180.0, 4.9, 0.0), 0.559)
    # chop caps the offshore bonus: base 1.2, chop_ratio 0.5 > 0.4 -> min(1.2, 0.8) = 0.8
    assert _close(I.wind_multiplier(0.0, 10.0, 0.0, 0.5), 0.8)
    assert 0.44 <= I.wind_multiplier(180.0, 25.0, 0.0) < 0.4 + 0.05, \
        "0.4 is NOT reachable — the docstring's lower bound is wrong"


def test_tide_multiplier_pinned_at_every_bucket():
    """Discrete, not continuous. Implemented value set is exactly {0.6, 0.7, 0.8, 1.0}."""
    assert I.tide_multiplier(None, "low") == 1.0        # no tide -> neutral
    assert I.tide_multiplier(0.5, None) == 1.0          # no preference -> neutral
    assert I.tide_multiplier(0.5, "all") == 1.0
    assert I.tide_multiplier(0.1, "low") == 1.0         # low pref, low tide
    assert I.tide_multiplier(0.5, "low") == 0.8
    assert I.tide_multiplier(0.9, "low") == 0.6         # MINIMUM
    assert I.tide_multiplier(0.5, "mid") == 1.0
    assert I.tide_multiplier(0.1, "mid") == 0.7
    assert I.tide_multiplier(0.9, "high") == 1.0
    assert I.tide_multiplier(0.5, "high") == 0.8
    assert I.tide_multiplier(0.1, "high") == 0.6        # MINIMUM
    assert I.tide_multiplier(0.5, "bogus") == 1.0       # unknown preference -> neutral
    got = {I.tide_multiplier(t, p)
           for p in (None, "", "all", "low", "mid", "high", "bogus")
           for t in (None, 0.0, 0.29, 0.3, 0.5, 0.7, 0.71, 1.0)}
    assert got == {0.6, 0.7, 0.8, 1.0}, got


def test_chop_multiplier_pinned():
    """_CHOP_POINTS = [(0,1),(0.2,1),(0.4,0.85),(0.6,0.65),(0.8,0.45),(1.0,0.3)].
      0.5 -> between (0.4,0.85) and (0.6,0.65): 0.85 + (0.5-0.4)/0.2 * (0.65-0.85) = 0.75
    """
    assert _close(I.chop_multiplier(0.0), 1.0)          # pure swell: MAXIMUM
    assert _close(I.chop_multiplier(0.2), 1.0)          # flat shoulder
    assert _close(I.chop_multiplier(0.4), 0.85)
    assert _close(I.chop_multiplier(0.5), 0.75)         # interpolated
    assert _close(I.chop_multiplier(0.6), 0.65)
    assert _close(I.chop_multiplier(1.0), 0.3)          # pure chop: MINIMUM
    assert _close(I.chop_multiplier(1.5), 0.3)          # clamped above
    assert _close(I.chop_multiplier(-0.5), 1.0)         # clamped below


def test_period_quality_pinned_including_the_above_one_bonus():
    """_PERIOD_QUALITY_POINTS = [(0,.5),(6,.5),(7,.6),(8,.7),(9,.8),(10,.85),(11,.9),
    (12,.95),(13,1.0),(16,1.05),(99,1.05)].
      6.5 s -> between (6,0.5) and (7,0.6): 0.5 + 0.5*0.1  = 0.55
     14.5 s -> between (13,1.0) and (16,1.05): 1.0 + (1.5/3)*0.05 = 1.025
    This is the ONLY factor that can exceed 1.0 — a long-period BONUS, not just a penalty."""
    assert _close(I.period_quality(0.0), 0.5)           # MINIMUM
    assert _close(I.period_quality(6.0), 0.5)
    assert _close(I.period_quality(6.5), 0.55)          # interpolated
    assert _close(I.period_quality(7.0), 0.6)
    assert _close(I.period_quality(13.0), 1.0)
    assert _close(I.period_quality(14.5), 1.025)        # interpolated
    assert _close(I.period_quality(16.0), 1.05)         # MAXIMUM
    assert _close(I.period_quality(99.0), 1.05)
    assert _close(I.period_quality(1000.0), 1.05)       # clamped above
    assert I.period_quality(16.0) > 1.0, "the >1.0 bonus is load-bearing for the ceiling case"


# --------------------------------------------------------------------------- #
# 8 — composite_stars, nine hand-computed cases                                 #
# --------------------------------------------------------------------------- #
def test_composite_zero_gate_below_half_a_foot():
    """eff < 0.5 returns 0.0 BEFORE any arithmetic — the one path that yields 0 stars.
    The gate is strict: 0.49 -> 0.0, and 0.5 clears it (see the floor test)."""
    assert I.composite_stars(0.49, 1.0, 1.0, 1.0, 1.0) == 0.0
    assert I.composite_stars(0.0, 1.0, 1.0, 1.0, 1.0) == 0.0
    assert I.composite_stars(-3.0, 1.2, 1.0, 1.0, 1.05) == 0.0


def test_composite_floor_lifts_a_half_foot_case_to_one_star():
    """eff = 0.5, all factors 1.0.
      size_score(0.5) = 0.5   (interp (0,0)-(1,1))
      factor          = 1.0**0.25 x4 = 1.0
      raw             = 0.5 * 1.0 = 0.5
      quantised       = round(0.5*2)/2 = round(1.0)/2 = 0.5
      floor           = max(1.0, 0.5) = 1.0
    So the 1.0 floor — not the arithmetic — produces this star value, and the output is
    discontinuous across 0.5 ft: 0.499 -> 0.0, 0.500 -> 1.0."""
    assert I.composite_stars(0.5, 1.0, 1.0, 1.0, 1.0) == 1.0
    assert I.composite_stars(0.499, 1.0, 1.0, 1.0, 1.0) == 0.0


def test_composite_mid_range():
    """eff = 3.0, all factors 1.0.
      size_score(3.0) = 2.5 ; factor = 1.0 ; raw = 2.5
      round(5.0)/2 = 2.5 ; within [1,5] -> 2.5"""
    assert I.composite_stars(3.0, 1.0, 1.0, 1.0, 1.0) == 2.5


def test_composite_lands_exactly_on_three_stars():
    """eff = 4.0, wind 0.8, others 1.0. Wind's exponent is 0.35.
      size_score(4.0) = 3.0
      0.8**0.35 = 0.9248717100
      raw   = 3.0 * 0.9248717100 = 2.7746151301
      raw*2 = 5.5492302601 -> round -> 6 -> 6/2 = 3.0

    Under the OLD 0.25 exponent the same case gave 0.8**0.25 = 0.9457416090, raw
    2.8372248270, raw*2 5.6744496540 -> also 3.0. The star value is unchanged; the
    arithmetic behind it is not, which is why the comment moved even though the
    assertion did not."""
    assert I.composite_stars(4.0, 0.8, 1.0, 1.0, 1.0) == 3.0


def test_composite_large_and_clean():
    """eff = 6.0, period 1.05, others 1.0. period_quality's exponent stayed at 0.25 through
    the 2026-08-25 reweighting, so this case is arithmetically UNCHANGED — the only one of
    the non-neutral cases in this file that is.
      size_score(6.0) = 4.0
      1.05**0.25 = sqrt(sqrt(1.05)) = sqrt(1.0246950766) = 1.0122722344
      raw   = 4.0 * 1.0122722344 = 4.0490889377
      raw*2 = 8.0981778753 -> round -> 8 -> 8/2 = 4.0"""
    assert I.composite_stars(6.0, 1.0, 1.0, 1.0, 1.05) == 4.0


def test_composite_large_and_blown_out():
    """eff = 10.0 with EVERY factor at its implemented minimum
    (wind 0.44, tide 0.6, chop 0.3, period 0.5).

    THE EXPONENTS ARE NO LONGER EQUAL, so this is a genuinely weighted mean and the old
    shortcut — take the plain product, raise it to 0.25 — no longer applies. Each factor
    carries its own power:
      size_score(10.0) = 5.0
      0.44**0.35 = 0.7502542025      (wind, the heaviest weight)
      0.6**0.15  = 0.9262381985      (tide, the lightest)
      0.3**0.25  = 0.7400828045
      0.5**0.25  = 0.8408964153
      factor = 0.7502542025 * 0.9262381985 * 0.7400828045 * 0.8408964153
             = 0.4324679614
      raw   = 5.0 * 0.4324679614 = 2.1623398071
      raw*2 = 4.3246796141 -> round -> 4 -> 4/2 = 2.0

    Under the OLD equal-weight set the shortcut did apply: 0.0396**0.25 = 0.4460913443,
    raw 2.2304567213 -> also 2.0. Same star, harsher arithmetic — the reweighting moves
    raw down by 3.1% here because wind is at 0.44, well below the other factors.

    THE OLD PRODUCT FORM (before the geometric mean, both sweeps ago): raw = 5.0 * 0.0396
    = 0.198 -> round(0.396) = 0 -> 0.0 -> max(1.0, 0.0) = 1.0. A 10 ft day and a 1 ft day
    were indistinguishable once four factors compounded. That collapse is what the
    geometric mean removes, and what the reweighting deliberately does NOT reintroduce."""
    assert I.composite_stars(10.0, 0.44, 0.6, 0.3, 0.5) == 2.0


def test_composite_all_factors_at_one_returns_size_score_unchanged():
    """The scale-preservation property, at every anchor whose size_score is already a
    multiple of 0.5 (so quantisation is the identity) and inside the [1,5] clamp:
      eff 2.0 -> ss 2.0 ; 3.0 -> 2.5 ; 4.0 -> 3.0 ; 5.0 -> 3.5 ; 6.0 -> 4.0 ; 8.0 -> 4.5
    With all four factors at 1.0 every power is 1.0 whatever its exponent — 1.0**0.35 =
    1.0**0.15 = 1.0**0.25 = 1.0 — so raw == size_score(eff). This property is INDEPENDENT
    of how the weight is split across the four factors and survived the 2026-08-25
    reweighting untouched; what it depends on is only that the exponents sum to 1."""
    for eff, want in ((2.0, 2.0), (3.0, 2.5), (4.0, 3.0),
                      (5.0, 3.5), (6.0, 4.0), (8.0, 4.5)):
        got = I.composite_stars(eff, 1.0, 1.0, 1.0, 1.0)
        assert got == want, f"eff {eff}: got {got}, want size_score {want} unchanged"


def test_composite_ceiling_clamps_at_five():
    """eff = 10.0, wind 1.2, period 1.05, others 1.0. Wind's exponent is 0.35.
      size_score(10.0) = 5.0
      1.2**0.35  = 1.0658925730
      1.05**0.25 = 1.0122722344
      factor = 1.0658925730 * 1.0122722344 = 1.0789734565
      raw    = 5.0 * 1.0789734565 = 5.3948672827
      raw*2  = 10.7897345654 -> round -> 11 -> 11/2 = 5.5
      clamp  = min(5.0, 5.5) = 5.0
    The pre-clamp value is 5.5, so the ceiling is load-bearing here, not incidental.

    THE REWEIGHTING PUSHES THIS CASE FURTHER PAST THE CEILING, not less far: a heavier
    wind exponent amplifies a BONUS as well as a penalty, and 1.2 is a bonus. Old set:
    1.2**0.25 = 1.0466351394, factor 1.0594796912, raw 5.2973984559. New raw is 1.8%
    higher. The clamp absorbs both, which is the point of pinning the pre-clamp value
    below rather than only the clamped output."""
    assert I.composite_stars(10.0, 1.2, 1.0, 1.0, 1.05) == 5.0
    # and the clamp is genuinely doing work: without it this would be 5.5
    assert 5.3948672827 * 2 > 11.0 - 0.5, "pre-clamp quantised value must exceed 5.0"


# --------------------------------------------------------------------------- #
# 9 — the property that motivates the change                                    #
# --------------------------------------------------------------------------- #
def test_scale_preservation_raw_equals_size_score_when_factors_are_neutral():
    """raw must be EXACTLY size_score(eff) when all four factors are 1.0 — that is what
    unit-sum exponents buy, and it is the property the raw product did not have.

    Checked without touching the private raw: rebuild it from the documented formula with
    hand-written size_score values, and require the observable stars to match the quantised,
    clamped result. Under the OLD product form the same identity happened to hold at 1.0
    (1*1*1*1 = 1), so the discriminating cases are the ones above where factors differ from
    1.0 — this test pins the neutral anchor those depend on."""
    # (eff, hand-written size_score(eff), expected stars after quantise+clamp)
    rows = [(1.0, 1.0, 1.0), (2.0, 2.0, 2.0), (3.0, 2.5, 2.5), (4.0, 3.0, 3.0),
            (5.0, 3.5, 3.5), (6.0, 4.0, 4.0), (8.0, 4.5, 4.5), (10.0, 5.0, 5.0),
            (7.0, 4.25, 4.5)]   # 4.25 -> round(8.5)/2; banker's rounding: round(8.5) = 8 -> 4.0
    for eff, ss, _ in rows[:-1]:
        # the four PRODUCTION exponents, hand-written: wind .35, tide .15, chop .25, pq .25
        raw = ss * (1.0 ** 0.35) * (1.0 ** 0.15) * (1.0 ** 0.25) * (1.0 ** 0.25)
        assert raw == ss, f"unit exponents must leave the scale untouched: {raw} != {ss}"
        assert I.composite_stars(eff, 1.0, 1.0, 1.0, 1.0) == max(1.0, min(5.0, round(raw * 2) / 2))
    # 7.0 ft: size_score 4.25 -> raw*2 = 8.5 -> Python round() is banker's: round(8.5) = 8
    # -> 8/2 = 4.0, NOT 4.5. Pinned because it is the one place half-even rounding shows.
    assert I.composite_stars(7.0, 1.0, 1.0, 1.0, 1.0) == 4.0
    assert round(8.5) == 8, "Python uses round-half-to-even; the line above depends on it"


def test_geometric_mean_does_not_compound_sub_one_factors():
    """The defect in one line. Four factors at 0.8 under the OLD product form gave
    0.8**4 = 0.4096; under the geometric mean they give 0.8 — the weighted mean of four
    EQUAL values is that value, whatever the weights, because the exponents sum to 1:

        0.8**0.35 * 0.8**0.15 * 0.8**0.25 * 0.8**0.25
      = 0.8**(0.35 + 0.15 + 0.25 + 0.25)
      = 0.8**1.0  =  0.8

    THIS CASE IS INVARIANT UNDER THE 2026-08-25 REWEIGHTING and would be invariant under
    any other unit-sum split — which is exactly why it cannot stand alone as the pin on
    the exponents. It pins the unit-sum property; the VALUES are pinned by
    test_the_exponents_are_pinned_by_exact_value_and_sum_to_one, and the asymmetry is
    pinned by the penalty-ceiling test below.

    eff 8.0 -> size_score 4.5.
      raw(mean)    = 4.5 * 0.8 = 3.6      -> round(7.2)/2 = 7/2 = 3.5
      raw(product) = 4.5 * 0.4096 = 1.8432 -> round(3.6864)/2 = 4/2 = 2.0"""
    assert _close(0.8 * 0.8 * 0.8 * 0.8, 0.4096)
    # spelled with the real, UNEQUAL exponents rather than a single 0.25 power
    assert _close(0.8 ** 0.35 * 0.8 ** 0.15 * 0.8 ** 0.25 * 0.8 ** 0.25, 0.8, tol=1e-12)
    assert I.composite_stars(8.0, 0.8, 0.8, 0.8, 0.8) == 3.5


# --------------------------------------------------------------------------- #
# 10 — THE PENALTY CEILING, which is what the 2026-08-25 reweighting bought      #
# --------------------------------------------------------------------------- #
def test_a_blown_out_big_day_now_drops_a_full_star_below_the_same_day_clean():
    """THE REASON THE EXPONENTS MOVED. wind_mult bottoms at 0.550 — the deepest direction
    band (>=150 deg off the offshore bearing) with no gale penalty, i.e. moderate-to-strong
    straight onshore, the Encyclopedia of Surfing definition of "blown out".

    Under the OLD 0.25 exponent that worst-case wind cost 0.550**0.25 = 0.8611735300, a
    14% haircut, and a big clean day dropped only HALF a star when blown out. Under 0.35 it
    costs 0.550**0.35 = 0.8111981309, a 19% haircut, and the same day drops a FULL star.

    Three sizes, all with tide/chop/period neutral so the wind exponent is the only thing
    acting. size_score is hand-written from _SIZE_POINTS:

      eff 6.0, size_score 4.0
        clean: raw = 4.0 * 1.0 = 4.0            -> round(8.0)/2  = 4.0
        windy: raw = 4.0 * 0.8111981309
                   = 3.2447925236               -> round(6.4895850472)/2 = 6/2 = 3.0
        drop 1.0   (old set gave raw 3.4446941199 -> round(6.8893882398)/2 = 7/2 = 3.5)

      eff 8.0, size_score 4.5
        clean: raw = 4.5                        -> round(9.0)/2  = 4.5
        windy: raw = 4.5 * 0.8111981309
                   = 3.6503915891               -> round(7.3007831781)/2 = 7/2 = 3.5
        drop 1.0   (old set gave raw 3.8752808848 -> round(7.7505617696)/2 = 8/2 = 4.0)

      eff 10.0, size_score 5.0
        clean: raw = 5.0                        -> round(10.0)/2 = 5.0
        windy: raw = 5.0 * 0.8111981309
                   = 4.0559906545               -> round(8.1119813090)/2 = 8/2 = 4.0
        drop 1.0   (old set gave raw 4.3058676498 -> round(8.6117353000)/2 = 9/2 = 4.5)

    A 10 ft day straight onshore can no longer publish 4.5 stars."""
    for eff, clean_want, windy_want in ((6.0, 4.0, 3.0), (8.0, 4.5, 3.5), (10.0, 5.0, 4.0)):
        clean = I.composite_stars(eff, 1.0, 1.0, 1.0, 1.0)
        windy = I.composite_stars(eff, 0.550, 1.0, 1.0, 1.0)
        assert clean == clean_want, f"eff {eff} clean: got {clean}, want {clean_want}"
        assert windy == windy_want, f"eff {eff} blown out: got {windy}, want {windy_want}"
        assert clean - windy >= 1.0, (
            f"eff {eff}: the worst wind the system can represent must cost at least a full "
            f"star, got {clean} -> {windy} (drop {clean - windy})")
    # the haircut itself, hand-computed, so a change to the exponent fails here too
    assert _close(0.550 ** 0.35, 0.8111981309, tol=1e-10)
    assert 0.550 ** 0.35 < 0.550 ** 0.25, "a larger exponent must bite harder on a penalty"
    # and the OLD behaviour is pinned as what was rejected: half a star, not a full one
    assert round(5.0 * 0.550 ** 0.25 * 2) / 2 == 4.5, "the 0.25 exponent gave a 10 ft day 4.5"


# --------------------------------------------------------------------------- #
# The epsilon floor — numeric guard only                                        #
# --------------------------------------------------------------------------- #
def test_negative_factor_is_floored_instead_of_going_complex():
    """A negative base with a fractional exponent does NOT raise in Python — it returns a
    COMPLEX number, which then survives the multiply and fails several lines later inside
    round(). The epsilon floor turns that into a defined value at the point of the error."""
    assert isinstance((-0.5) ** 0.25, complex), "the hazard this floor exists for"
    got = I.composite_stars(6.0, -0.5, 1.0, 1.0, 1.0)
    assert isinstance(got, float) and got == 1.0, got
    # a zero factor is likewise defined rather than fatal
    assert I.composite_stars(6.0, 0.0, 1.0, 1.0, 1.0) == 1.0


def test_epsilon_floor_is_inert_on_every_production_factor_range():
    """It is a NUMERIC guard, not a physical bound: every factor's implemented minimum
    (wind 0.44, tide 0.6, chop 0.3, period 0.5) is orders of magnitude above it, so on real
    inputs max(eps, x) == x and no rating is altered."""
    for lo in (0.44, 0.6, 0.3, 0.5):
        assert max(I._FACTOR_EPSILON, lo) == lo
    assert I.composite_stars(10.0, 0.44, 0.6, 0.3, 0.5) == 2.0   # same as the pinned case


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} composite-aggregation checks passed")


if __name__ == "__main__":
    _run_all()
