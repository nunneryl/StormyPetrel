"""wind_multiplier — every band, every boundary, the implemented range, and two traps.

WHAT WAS ALREADY COVERED, so this file is additive rather than a rediscovery:
test_composite_aggregation.test_wind_multiplier_pinned_including_its_true_minimum already
pins eleven values including the 0.44 minimum and the 5.0 blend boundary, and
test_chop_consistency pins the chop cap through the override path. This file goes deeper on
the parts those leave open — every band edge, the interior of each blend segment, the wrap
of _angle_off, the >15 and >20 boundaries from both sides, the full signature, and the two
items below, which nothing executes today.

TRAP 1 — THE BLEND THRESHOLD AND ITS DIVISOR ARE INDEPENDENT SOURCE LITERALS.

    if wind_speed_ms < 5.0:                          # <- threshold
        blend = max(0.0, wind_speed_ms) / 5.0        # <- divisor, a SEPARATE literal

Raising the threshold without raising the divisor makes `blend` exceed 1.0, and the blend
is an unclamped linear mix — so it EXTRAPOLATES PAST NEUTRAL instead of stopping at it. At
a threshold of 8.0, a 6 m/s onshore wind gives 1.0*(1-1.2) + 0.55*1.2 = 0.46, BELOW the
un-blended 0.55 the same wind would score today. A "make the light-wind blend a bit more
generous" edit therefore makes moderate onshore winds score WORSE. test_composite_aggregation
describes this in a comment; test_the_blend_threshold_and_its_divisor_are_coupled below
actually executes it. NOT FIXED HERE — this commit documents the coupling, nothing more.

  (The brief for this file put the example at "a threshold of 6.0"; 6.0 cannot produce it,
   because `6.0 < 6.0` is False and no blend happens at all. The result 0.46 is right and
   needs a threshold ABOVE 6.0 — 8.0 is used below, matching the existing comment.)

TRAP 2 — THE CHOP CAP IS DEAD ABOVE 15 m/s. Confirmed at this HEAD by exhaustive sweep:
across every combination of nine wind speeds above 15, nine bearings and four chop ratios,
there is NOT ONE input where chop_ratio changes the result. The preceding branch has already
forced base <= 1.0, and the cap's own guard is `base > 1.0`. Reported, not fixed.

NO BEHAVIOUR IS CHANGED HERE. No threshold, no band, no curve, no docstring is touched.

EVERY EXPECTED VALUE IS HAND-COMPUTED with the arithmetic in a comment. None is derived by
calling the function under test.

Run: python -m pipeline.tests.test_wind_multiplier   (or pytest)
"""
from __future__ import annotations

import inspect
import textwrap

from pipeline import interpret as I


def _close(a, b, tol=1e-12):
    return abs(a - b) <= tol


# --------------------------------------------------------------------------- #
# 1 — the signature                                                             #
# --------------------------------------------------------------------------- #
def test_the_signature_is_four_parameters_with_chop_defaulting_to_zero():
    """Positional order is load-bearing: all three production call sites pass positionally
    (interpret.py:1253, mop.py:245, nwps_nearshore.py:994 all read
    `wind_multiplier(float(wdir), float(wspd), offshore, cr)`), so reordering the parameters
    would silently swap direction for speed rather than raise.

    NOTE, reported not fixed: chop_ratio_val is annotated `float` but None is an accepted
    and meaningful value (test 9). The annotation was not updated when chop_ratio gained its
    unknown state. Pinned here as the current state; correcting the annotation is a separate
    commit."""
    sig = inspect.signature(I.wind_multiplier)
    assert list(sig.parameters) == [
        "wind_dir", "wind_speed_ms", "offshore_wind_deg", "chop_ratio_val"], list(sig.parameters)
    assert sig.parameters["chop_ratio_val"].default == 0.0
    for name in ("wind_dir", "wind_speed_ms", "offshore_wind_deg"):
        assert sig.parameters[name].default is inspect.Parameter.empty, name
    # the annotation that no longer matches the accepted domain
    assert sig.parameters["chop_ratio_val"].annotation == "float"
    assert I.wind_multiplier(90.0, 8.0, 90.0, None) == 1.2, "None is nevertheless accepted"


# --------------------------------------------------------------------------- #
# 2 — no offshore bearing short-circuits everything                             #
# --------------------------------------------------------------------------- #
def test_a_missing_offshore_bearing_returns_neutral_before_any_other_rule():
    """`if offshore_wind_deg is None: return 1.0` is the FIRST statement, so it outranks the
    gale penalty, the >15 cap and the chop cap alike. A spot with no offshore_wind_deg scores
    a neutral 1.0 in a 25 m/s onshore gale with total chop — the wind factor simply does not
    participate. Pinned so the early return cannot drift below the other rules."""
    assert I.wind_multiplier(0.0, 10.0, None) == 1.0
    assert I.wind_multiplier(180.0, 25.0, None) == 1.0        # gale ignored
    assert I.wind_multiplier(180.0, 25.0, None, 1.0) == 1.0   # gale + total chop ignored
    assert I.wind_multiplier(0.0, 0.0, None) == 1.0           # light-wind blend ignored
    assert I.wind_multiplier(0.0, 100.0, None, None) == 1.0


# --------------------------------------------------------------------------- #
# 3 — the five direction bands, at 10 m/s where no other rule applies           #
# --------------------------------------------------------------------------- #
def test_every_direction_band_by_value_at_both_of_its_edges():
    """    ang < 30 -> 1.2   ang < 60 -> 1.0   ang < 120 -> 0.8
        ang < 150 -> 0.6  else -> 0.55

    10 m/s is chosen because it is above the 5.0 blend threshold and below the 15.0 cap and
    the 20.0 gale, so the band value reaches the return untouched. Every comparison is a
    STRICT `<`, so each boundary belongs to the band BELOW it — 30.0 scores 1.0, not 1.2."""
    b = lambda ang: I.wind_multiplier(ang, 10.0, 0.0)   # noqa: E731 — reads better inline
    assert b(0.0) == 1.2          # dead offshore: MAXIMUM
    assert b(29.9) == 1.2
    assert b(30.0) == 1.0         # boundary belongs to the band below
    assert b(59.9) == 1.0
    assert b(60.0) == 0.8         # boundary
    assert b(90.0) == 0.8         # pure cross-shore
    assert b(119.9) == 0.8
    assert b(120.0) == 0.6        # boundary
    assert b(149.9) == 0.6
    assert b(150.0) == 0.55       # boundary
    assert b(180.0) == 0.55       # dead onshore: the floor before the gale penalty
    # monotone non-increasing as the wind swings from offshore to onshore
    vals = [b(a) for a in (0.0, 30.0, 60.0, 120.0, 150.0, 180.0)]
    assert vals == sorted(vals, reverse=True), vals


# --------------------------------------------------------------------------- #
# 4 — _angle_off wraps, so the band depends on the SHORTEST arc                 #
# --------------------------------------------------------------------------- #
def test_the_band_is_chosen_from_the_shortest_angle_not_the_raw_difference():
    """_angle_off: `d = abs(a-b) % 360; return min(d, 360-d)`, so the result is in [0, 180]
    and 350 vs 0 is TEN degrees apart, not 350.

        350 vs 0   -> d = 350, min(350, 10)  = 10   -> band 1.2
        10  vs 350 -> d = 340, min(340, 20)  = 20   -> band 1.2
        200 vs 20  -> d = 180, min(180, 180) = 180  -> band 0.55
        90  vs 270 -> d = 180                        -> band 0.55
        370 vs 10  -> d = 360 % 360 = 0              -> band 1.2  (out-of-range bearing)
    """
    assert I.wind_multiplier(350.0, 10.0, 0.0) == 1.2
    assert I.wind_multiplier(10.0, 10.0, 350.0) == 1.2
    assert I.wind_multiplier(200.0, 10.0, 20.0) == 0.55
    assert I.wind_multiplier(90.0, 10.0, 270.0) == 0.55
    assert I.wind_multiplier(370.0, 10.0, 10.0) == 1.2
    # symmetric in its two bearings
    for a, o in ((17.0, 200.0), (350.0, 0.0), (95.0, 12.0)):
        assert I.wind_multiplier(a, 10.0, o) == I.wind_multiplier(o, 10.0, a), (a, o)


# --------------------------------------------------------------------------- #
# 5 — the light-wind blend, interior points on several bands                    #
# --------------------------------------------------------------------------- #
def test_the_light_wind_blend_interpolates_toward_neutral_on_every_band():
    """    blend = max(0.0, wind_speed_ms) / 5.0
        base  = 1.0 * (1.0 - blend) + base * blend

    A linear mix from neutral 1.0 at 0 m/s to the full band value at 5 m/s. It pulls a bad
    band UP and a good band DOWN — at 0 m/s every direction scores exactly 1.0.

    onshore band 0.55:
        0.0 m/s: blend 0.0  -> 1.0*1.00 + 0.55*0.00 = 1.0
        1.0 m/s: blend 0.2  -> 1.0*0.80 + 0.55*0.20 = 0.80 + 0.110 = 0.91
        2.5 m/s: blend 0.5  -> 1.0*0.50 + 0.55*0.50 = 0.50 + 0.275 = 0.775
        4.0 m/s: blend 0.8  -> 1.0*0.20 + 0.55*0.80 = 0.20 + 0.440 = 0.64
        4.9 m/s: blend 0.98 -> 1.0*0.02 + 0.55*0.98 = 0.02 + 0.539 = 0.559
    offshore band 1.2:
        1.0 m/s: blend 0.2  -> 1.0*0.80 + 1.20*0.20 = 0.80 + 0.240 = 1.04
        2.5 m/s: blend 0.5  -> 1.0*0.50 + 1.20*0.50 = 0.50 + 0.600 = 1.10
        4.0 m/s: blend 0.8  -> 1.0*0.20 + 1.20*0.80 = 0.20 + 0.960 = 1.16
    cross band 0.8 and the 0.6 band:
        2.5 m/s: 1.0*0.5 + 0.8*0.5 = 0.5 + 0.40 = 0.90
        2.5 m/s: 1.0*0.5 + 0.6*0.5 = 0.5 + 0.30 = 0.80
    """
    assert I.wind_multiplier(180.0, 0.0, 0.0) == 1.0
    assert _close(I.wind_multiplier(180.0, 1.0, 0.0), 0.91)
    assert _close(I.wind_multiplier(180.0, 2.5, 0.0), 0.775)
    assert _close(I.wind_multiplier(180.0, 4.0, 0.0), 0.64)
    assert _close(I.wind_multiplier(180.0, 4.9, 0.0), 0.559)

    assert I.wind_multiplier(0.0, 0.0, 0.0) == 1.0            # dead calm is neutral, not 1.2
    assert _close(I.wind_multiplier(0.0, 1.0, 0.0), 1.04)
    assert _close(I.wind_multiplier(0.0, 2.5, 0.0), 1.1)
    assert _close(I.wind_multiplier(0.0, 4.0, 0.0), 1.16)

    assert _close(I.wind_multiplier(90.0, 2.5, 0.0), 0.9)
    assert _close(I.wind_multiplier(120.0, 2.5, 0.0), 0.8)

    # at 0 m/s EVERY band collapses to neutral — that is the mix, not a special case
    for ang in (0.0, 45.0, 90.0, 135.0, 180.0):
        assert I.wind_multiplier(ang, 0.0, 0.0) == 1.0, ang
    # max(0.0, ...) means a negative speed clamps to blend 0 rather than extrapolating
    assert I.wind_multiplier(180.0, -3.0, 0.0) == 1.0
    assert I.wind_multiplier(0.0, -100.0, 0.0) == 1.0


# --------------------------------------------------------------------------- #
# 6 — the 5.0 threshold itself, from both sides                                 #
# --------------------------------------------------------------------------- #
def test_the_blend_threshold_is_five_and_is_exclusive():
    """`if wind_speed_ms < 5.0` — STRICT, so 5.0 itself does NOT blend and the raw band value
    stands. The two sides differ by 0.009 on the onshore band, which is what makes this
    boundary observable at all:

        4.9 m/s -> blend 0.98 -> 0.559   (blended)
        5.0 m/s -> no blend   -> 0.55    (raw band)
        5.1 m/s -> no blend   -> 0.55

    Pinned on the offshore band too, where the step runs the other way (1.196 -> 1.2):
        4.9 m/s -> 1.0*0.02 + 1.2*0.98 = 0.02 + 1.176 = 1.196
    """
    assert _close(I.wind_multiplier(180.0, 4.9, 0.0), 0.559)
    assert I.wind_multiplier(180.0, 5.0, 0.0) == 0.55
    assert I.wind_multiplier(180.0, 5.1, 0.0) == 0.55
    assert I.wind_multiplier(180.0, 6.0, 0.0) == 0.55

    assert _close(I.wind_multiplier(0.0, 4.9, 0.0), 1.196)
    assert I.wind_multiplier(0.0, 5.0, 0.0) == 1.2
    assert I.wind_multiplier(0.0, 5.1, 0.0) == 1.2

    # the blend is strictly monotone up to the threshold and flat after it
    onshore = [I.wind_multiplier(180.0, ws, 0.0) for ws in (0.0, 1.0, 2.5, 4.0, 4.9)]
    assert onshore == sorted(onshore, reverse=True), onshore
    assert onshore[-1] > I.wind_multiplier(180.0, 5.0, 0.0)


# --------------------------------------------------------------------------- #
# 7 — TRAP 1: the threshold and its divisor are coupled, and nothing enforces it #
# --------------------------------------------------------------------------- #
def test_the_blend_threshold_and_its_divisor_are_coupled_and_extrapolate_if_split():
    """RAISING THE THRESHOLD WITHOUT RAISING THE DIVISOR MAKES MODERATE WINDS SCORE WORSE.

    The blend is an unclamped linear mix, so a `blend` above 1.0 does not saturate at the
    band value — it runs PAST it, away from neutral:

        threshold 8.0, divisor still 5.0, wind 6.0 m/s onshore (band 0.55)
            blend = max(0.0, 6.0) / 5.0 = 1.2                 <- exceeds 1.0
            base  = 1.0*(1.0 - 1.2) + 0.55*1.2
                  = 1.0*(-0.2)     + 0.66
                  = -0.2 + 0.66    = 0.46
        against the 0.55 the same wind scores today -> a "more generous light-wind blend"
        edit makes a 6 m/s onshore wind score 16% WORSE.

    The threshold is an inline literal, not a named constant, so it is monkeypatched at
    SOURCE level: the real function's source is recompiled with only the threshold changed
    and the divisor left alone, which is exactly the edit a developer would make by hand.
    CPython folds the two `5.0` literals into ONE co_consts entry, so patching the compiled
    constant would move both together and could not show this at all — the independence is a
    property of the SOURCE, which is where the mistake gets made.

    THE BUG IS NOT FIXED HERE. This test documents the coupling so it is discoverable by
    reading the suite rather than by shipping it."""
    src = textwrap.dedent(inspect.getsource(I.wind_multiplier))
    threshold_line = "if wind_speed_ms < 5.0:"
    divisor_line = "blend = max(0.0, wind_speed_ms) / 5.0"
    assert src.count(threshold_line) == 1, "threshold literal is no longer unique"
    assert src.count(divisor_line) == 1, "divisor literal is no longer unique"

    variant_src = src.replace(threshold_line, "if wind_speed_ms < 8.0:", 1)
    assert "/ 5.0" in variant_src, "the divisor must stay at 5.0 — that IS the bug"
    ns = dict(I.__dict__)
    exec(compile(variant_src, "<wind_multiplier: threshold 8.0>", "exec"), ns)  # noqa: S102

    original = I.wind_multiplier
    try:
        I.wind_multiplier = ns["wind_multiplier"]
        # 1.0*(1-1.2) + 0.55*1.2 = -0.2 + 0.66 = 0.46
        got = I.wind_multiplier(180.0, 6.0, 0.0)
        assert _close(got, 0.46), got
        assert got < 0.55, "the extrapolation must land BELOW the un-blended band value"
        # and on the offshore band it overshoots the other way:
        #   1.0*(1-1.2) + 1.2*1.2 = -0.2 + 1.44 = 1.24, above the 1.2 maximum
        assert _close(I.wind_multiplier(0.0, 6.0, 0.0), 1.24)
        # below the real threshold the two spellings still agree — the divisor is unchanged
        assert _close(I.wind_multiplier(180.0, 2.5, 0.0), 0.775)
    finally:
        I.wind_multiplier = original

    # RESTORED — identity and value, so no later test inherits the variant
    assert I.wind_multiplier is original
    assert I.wind_multiplier(180.0, 6.0, 0.0) == 0.55, "the real function still does not blend at 6"
    assert I.wind_multiplier(0.0, 6.0, 0.0) == 1.2


# --------------------------------------------------------------------------- #
# 8 — the >15 m/s bonus cap and the >20 m/s gale, from both sides               #
# --------------------------------------------------------------------------- #
def test_the_strong_wind_cap_and_the_gale_penalty_at_their_boundaries():
    """Both are STRICT `>`, so each threshold value itself is on the lenient side.

    cap:   `if wind_speed_ms > 15.0 and base > 1.0: base = 1.0` — only ever touches the
           offshore bonus; a base at or below 1.0 passes through.
        15.0 m/s offshore -> 1.2 (15.0 is not > 15.0)
        15.1 m/s offshore -> 1.0
        16.0 m/s at 90 deg -> 0.8, untouched (0.8 is not > 1.0)

    gale:  `if wind_speed_ms > 20.0: base *= 0.8` — blanket, every direction.
        20.0 m/s onshore -> 0.55 (not > 20.0)
        20.1 m/s onshore -> 0.55 * 0.8 = 0.44
        20.1 m/s offshore -> 1.2 -> cap 1.0 -> 1.0 * 0.8  = 0.8
        20.1 m/s at 90    -> 0.8 * 0.8                     = 0.64
        20.1 m/s at 120   -> 0.6 * 0.8                     = 0.48
    """
    assert I.wind_multiplier(0.0, 15.0, 0.0) == 1.2
    assert I.wind_multiplier(0.0, 15.1, 0.0) == 1.0
    assert I.wind_multiplier(0.0, 16.0, 0.0) == 1.0
    assert I.wind_multiplier(90.0, 16.0, 0.0) == 0.8
    assert I.wind_multiplier(180.0, 16.0, 0.0) == 0.55

    assert I.wind_multiplier(180.0, 20.0, 0.0) == 0.55
    assert _close(I.wind_multiplier(180.0, 20.1, 0.0), 0.44)
    assert _close(I.wind_multiplier(0.0, 20.1, 0.0), 0.8)
    assert _close(I.wind_multiplier(90.0, 20.1, 0.0), 0.64)
    assert _close(I.wind_multiplier(120.0, 20.1, 0.0), 0.48)
    # the gale is multiplicative and applies once, not per-band
    assert _close(I.wind_multiplier(150.0, 25.0, 0.0), 0.44)   # 0.55 * 0.8
    assert _close(I.wind_multiplier(180.0, 100.0, 0.0), 0.44)  # no further decay


# --------------------------------------------------------------------------- #
# 9 — the chop cap, including its Optional argument                             #
# --------------------------------------------------------------------------- #
def test_the_chop_cap_trips_above_zero_point_four_and_only_on_an_offshore_bonus():
    """`if chop_ratio_val is not None and chop_ratio_val > 0.4 and base > 1.0:
            base = min(base, 0.8)`

    Three conditions, and all three are pinned:
      * the 0.4 trigger is STRICT, so exactly 0.4 does not cap
      * `base > 1.0` means it only ever touches an offshore bonus — a cross-shore 0.8 or an
        onshore 0.55 is left alone no matter how junked the water is
      * None is UNKNOWN, not zero: it must neither raise nor cap

    A 1.2 bonus becomes min(1.2, 0.8) = 0.8 — a 33% cut, the largest single adjustment in
    the function."""
    assert I.wind_multiplier(0.0, 10.0, 0.0, 0.0) == 1.2       # clean water keeps the bonus
    assert I.wind_multiplier(0.0, 10.0, 0.0, 0.4) == 1.2       # boundary: NOT > 0.4
    assert I.wind_multiplier(0.0, 10.0, 0.0, 0.41) == 0.8      # just over -> capped
    assert I.wind_multiplier(0.0, 10.0, 0.0, 0.5) == 0.8
    assert I.wind_multiplier(0.0, 10.0, 0.0, 1.0) == 0.8       # total chop, same cap

    # None: unknown chop cannot be judged, so the cap is not applied and nothing raises
    assert I.wind_multiplier(0.0, 10.0, 0.0, None) == 1.2
    assert I.wind_multiplier(90.0, 8.0, 90.0, None) == 1.2
    assert I.wind_multiplier(180.0, 25.0, 0.0, None) == I.wind_multiplier(180.0, 25.0, 0.0, 0.0)
    # the default is 0.0, so an omitted argument behaves as "clean", not as "unknown"
    assert I.wind_multiplier(0.0, 10.0, 0.0) == I.wind_multiplier(0.0, 10.0, 0.0, 0.0)

    # base <= 1.0 is never touched, at any chop
    for ang, want in ((30.0, 1.0), (90.0, 0.8), (120.0, 0.6), (180.0, 0.55)):
        for cr in (0.41, 0.5, 1.0, None):
            assert I.wind_multiplier(ang, 10.0, 0.0, cr) == want, (ang, cr)

    # the cap is a MIN, not an assignment: a base already below 0.8 could not be raised to it
    # (unreachable today because base > 1.0 gates it, but pinned as the operator's semantics)
    assert I.wind_multiplier(0.0, 10.0, 0.0, 0.9) == 0.8


def test_the_chop_cap_has_a_cliff_just_above_dead_calm():
    """The blend and the cap interact at the very bottom of the wind range. `base > 1.0` is
    strict, and at 0 m/s the blend lands base at exactly 1.0 — so a fraction of a metre per
    second decides whether a 33% cut applies:

        0.0   m/s: blend 0.0     -> base 1.0*(1.0) + 1.2*0.0     = 1.0      -> NOT > 1.0, no cap
        0.001 m/s: blend 0.0002  -> base 1.0*0.9998 + 1.2*0.0002
                                  = 0.9998 + 0.00024            = 1.00004  -> capped to 0.8

    A 0.001 m/s change in wind speed moves the multiplier from 1.0 to 0.8. Pinned as
    observed behaviour, not endorsed."""
    assert I.wind_multiplier(0.0, 0.0, 0.0, 0.9) == 1.0
    assert _close(I.wind_multiplier(0.0, 0.001, 0.0, 0.0), 1.00004)   # uncapped base
    assert I.wind_multiplier(0.0, 0.001, 0.0, 0.9) == 0.8             # capped
    assert I.wind_multiplier(0.0, 0.0, 0.0, 0.9) > I.wind_multiplier(0.0, 0.001, 0.0, 0.9)


# --------------------------------------------------------------------------- #
# 10 — TRAP 2: the chop cap is unreachable above 15 m/s                         #
# --------------------------------------------------------------------------- #
def test_the_chop_cap_is_dead_above_fifteen_metres_per_second():
    """REPORTED, NOT FIXED. The order of the two guards makes the chop cap unreachable once
    the wind is strong:

        if wind_speed_ms > 15.0 and base > 1.0:   base = 1.0     # runs FIRST
        ...
        if chop_ratio_val ... and base > 1.0:     base = min(base, 0.8)

    Above 15 m/s the first branch has already driven any bonus down to exactly 1.0, and every
    other band is at or below 1.0 to begin with — so the second branch's `base > 1.0` can
    never hold. Confirmed by exhaustive sweep at this HEAD: nine speeds above 15 x nine
    bearings x four chop ratios = 324 inputs, and chop changes the result in ZERO of them.

    That is a real dead branch, but a benign one: above 15 m/s the wind penalty is already
    doing the work the chop cap would have done. Fixing the order would change published
    ratings, so it belongs in its own commit with its own before/after."""
    changed = []
    for ws in (15.001, 15.1, 16.0, 18.0, 20.0, 20.1, 25.0, 40.0, 100.0):
        for ang in (0.0, 10.0, 29.0, 29.9, 30.0, 45.0, 90.0, 130.0, 180.0):
            for cr in (0.41, 0.5, 0.75, 1.0):
                with_chop = I.wind_multiplier(ang, ws, 0.0, cr)
                without = I.wind_multiplier(ang, ws, 0.0, 0.0)
                if with_chop != without:
                    changed.append((ws, ang, cr, without, with_chop))
    assert changed == [], f"the chop cap became reachable above 15 m/s: {changed[:5]}"

    # ...and it IS alive at and below 15, so this is an ordering artefact and not a
    # dead condition everywhere: 1.2 -> min(1.2, 0.8) = 0.8
    assert I.wind_multiplier(0.0, 15.0, 0.0, 0.5) == 0.8
    assert I.wind_multiplier(0.0, 15.0, 0.0, 0.0) == 1.2
    assert I.wind_multiplier(0.0, 15.001, 0.0, 0.5) == 1.0    # cap already dead one ulp later


# --------------------------------------------------------------------------- #
# 11 — the implemented range                                                    #
# --------------------------------------------------------------------------- #
def test_the_implemented_range_is_zero_point_four_four_to_one_point_two():
    """THE DOCSTRING SAYS "0.4–1.2". THE TRUE MINIMUM IS 0.44 AND 0.4 IS NOT REACHABLE.

        deepest band 0.55 (ang >= 150), gale factor 0.8 -> 0.55 * 0.8 = 0.44

    Nothing goes lower: the light-wind blend only pulls values UP toward 1.0 and cannot
    coexist with the gale (one needs < 5 m/s, the other > 20), and the chop cap floors at 0.8
    and needs base > 1.0, which the >15 branch has already ruled out at gale speeds.

    THE DOCSTRING IS NOT CHANGED IN THIS COMMIT — that is a separate, one-line edit with its
    own review. It is pinned here as WRONG so the correction is deliberate. The module
    docstring at interpret.py:13-16 already carries the corrected figure and flags the
    disagreement, so the two are inconsistent with each other as well.

    Measured over 3601 bearings x 15 speeds x 5 chop values = 270k inputs."""
    lo, hi = 9e9, -9e9
    lo_at = hi_at = None
    for ang10 in range(0, 3601):
        ang = ang10 / 10.0
        for ws in (0.0, 0.5, 1.0, 2.5, 4.0, 4.9, 5.0, 5.1, 10.0, 15.0, 15.1, 20.0, 20.1, 25.0, 50.0):
            for cr in (None, 0.0, 0.4, 0.41, 1.0):
                v = I.wind_multiplier(ang, ws, 0.0, cr)
                if v < lo:
                    lo, lo_at = v, (ang, ws, cr)
                if v > hi:
                    hi, hi_at = v, (ang, ws, cr)
    assert _close(lo, 0.44), (lo, lo_at)          # 0.55 * 0.8
    assert _close(hi, 1.2), (hi, hi_at)
    assert lo > 0.4, "0.4 is NOT reachable — the function docstring's lower bound is wrong"

    # the docstring still claims it, and this test is the record that it does
    assert "0.4–1.2" in (I.wind_multiplier.__doc__ or ""), \
        "the docstring's range changed — if it was corrected to 0.44, update this test"
    # the module docstring already carries the correction, so the two disagree in-tree
    assert "0.44" in (I.__doc__ or "")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} wind-multiplier checks passed")


if __name__ == "__main__":
    _run_all()
