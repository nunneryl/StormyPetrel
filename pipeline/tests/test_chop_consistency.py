"""Chop is ONE computation, and an unknown says so instead of claiming zero.

THREE DEFECTS THIS PINS SHUT.

A — SPLIT WRITERS. chop_ratio had one writer (rate_spot) and chop_mult had three
(rate_spot, nwps_stars, mop_stars). The two overrides wrote the multiplier and never
the ratio, so a persisted row could carry a ratio and a multiplier computed from
different hours (the overrides hour-match within +/-1 bucket), different node
resolutions, and on the MOP path different source data entirely — the ratio from NWPS
shts, the multiplier from a CDIP spectral integral. Measured at 16115 rows (19.5%)
disagreeing; Corolla Beach carried chop_ratio 1.0 with chop_mult 0.507 where the curve
says 1.0 must give 0.30.

B — ZERO vs MISSING. chop_ratio returned 0.0 for six conditions and only one of them
(swell_hs == hs) meant clean water. All six mapped to chop_multiplier's MAXIMUM of 1.0,
so absent shts scored as glassy. It reached users: classifyChop renders ratio < 0.2 as
"Clean" and CurrentConditions prints "{n}% wind sea", so missing data displayed as
"Clean — 0% wind sea".

C — A FROZEN WIND CAP. wind_multiplier caps an offshore bonus at 0.8 when
chop_ratio > 0.4. That fired once in rate_spot and was frozen into the stored
wind_mult; an override writing a contradictory chop_mult never revisited it.

THE ACCEPTANCE CRITERION, and what test_the_round_trip_invariant_holds_on_every_path
exists to enforce: chop_multiplier(row["chop_ratio"]) reproduces row["chop_mult"] at the
stored precision, for every row, on every path.

NO CALIBRATION IS CHANGED HERE and these tests would fail if it were: _CHOP_POINTS,
the curve's shape, and every threshold are pinned by value in
test_composite_aggregation.py and are not touched.

EVERY EXPECTED VALUE IS HAND-COMPUTED with the arithmetic in a comment. None is derived
by calling the function under test.

Run: python -m pipeline.tests.test_chop_consistency   (or pytest)
"""
from __future__ import annotations

import datetime

from pipeline import interpret as I
from pipeline.forecast import mop, nwps_nearshore as nn


def _close(a, b, tol=1e-12):
    return abs(a - b) <= tol


_BASE = 1767225600                     # 2026-01-01T00:00:00Z, epoch seconds


def _iso(epoch):
    return datetime.datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%dT%H:00:00Z")


# --------------------------------------------------------------------------- #
# 1 — UNKNOWN is None, and exactly one input combination is a real ratio        #
# --------------------------------------------------------------------------- #
def test_chop_ratio_is_none_for_every_unknown_condition():
    """Six conditions used to return 0.0. Five of them are UNKNOWN and must now be None;
    only swell_hs == hs is a real zero. A sixth — a negative swell_hs — was not guarded
    at all and produced maximum chop from garbage.

        hs is None       -> no total height was read
        hs == 0          -> ratio undefined at zero total (land / sheltered-node signature)
        hs < 0           -> impossible total
        swell_hs is None -> shts absent from the grid, or dropped as NaN
        swell_hs > hs    -> impossible; swell is a COMPONENT of total
        swell_hs < 0     -> impossible
    """
    assert I.chop_ratio(None, 0.5) is None
    assert I.chop_ratio(0.0, 0.5) is None
    assert I.chop_ratio(-1.0, 0.5) is None
    assert I.chop_ratio(1.0, None) is None
    assert I.chop_ratio(1.0, 2.0) is None
    assert I.chop_ratio(1.0, -0.5) is None
    # The hs guard must stand on its OWN, not lean on the swell_hs guard catching the
    # same rows by accident. These two reach the division if hs <= 0 is not guarded:
    #   hs 0.0, swell_hs 0.0 -> 0.0 is neither None, negative, nor > hs, so an unguarded
    #                           hs divides by zero
    #   hs -1.0, swell_hs -1.0 -> likewise passes every swell_hs test
    assert I.chop_ratio(0.0, 0.0) is None
    assert I.chop_ratio(-1.0, -1.0) is None


def test_chop_ratio_is_a_real_number_only_when_the_inputs_support_one():
    """hs > 0 with 0 <= swell_hs <= hs. Hand-computed, rounded to the 3 dp both columns
    persist at:

        hs 1.0, swell_hs 1.0  -> (1.0-1.0)/1.0 = 0.0    -> 0.0    (pure swell, the one
                                                                   genuine zero)
        hs 1.0, swell_hs 0.6  -> (1.0-0.6)/1.0 = 0.4    -> 0.4
        hs 1.0, swell_hs 0.0  -> (1.0-0.0)/1.0 = 1.0    -> 1.0    (pure wind sea)
        hs 2.0, swell_hs 0.5  -> (2.0-0.5)/2.0 = 0.75   -> 0.75
        hs 1.6, swell_hs 1.1  -> (1.6-1.1)/1.6 = 0.3125 -> 0.312  (rounds DOWN)
    """
    assert I.chop_ratio(1.0, 1.0) == 0.0
    assert _close(I.chop_ratio(1.0, 0.6), 0.4)
    assert I.chop_ratio(1.0, 0.0) == 1.0
    assert _close(I.chop_ratio(2.0, 0.5), 0.75)
    assert _close(I.chop_ratio(1.6, 1.1), 0.3125)     # chop_ratio itself is unrounded


def test_zero_and_unknown_are_distinguishable():
    """The whole point of defect B. A genuine zero and an unknown must not be the same
    value, or nothing downstream can tell "clean" from "we have no idea"."""
    genuine_zero = I.chop_ratio(1.0, 1.0)
    unknown = I.chop_ratio(1.0, None)
    assert genuine_zero == 0.0
    assert unknown is None
    assert genuine_zero is not None
    # and they are not merely != — the unknown is not a number at all
    assert isinstance(genuine_zero, float) and unknown is None


# --------------------------------------------------------------------------- #
# 2 — an unknown scores NEUTRAL, and the ratio still persists as None           #
# --------------------------------------------------------------------------- #
def test_an_unknown_ratio_scores_neutral_without_becoming_zero():
    """chop_multiplier(None) is the neutral 1.0 — an unknown must not silently penalise,
    and 1.0 is the identity for the composite's geometric mean. But the RATIO stays None
    so the frontend can render 'unknown' rather than 'Clean'.

    Note this is the same multiplier a genuine 0.0 gets, which is exactly why the ratio
    has to carry the distinction: the two are indistinguishable in chop_mult alone.
        chop_multiplier(0.0)  = 1.0   (curve's first knot)
        chop_multiplier(None) = 1.0   (neutral, by the unknown branch)
    """
    assert I.chop_multiplier(None) == 1.0
    assert I.chop_multiplier(0.0) == 1.0
    for hs, swell_hs in ((None, 0.5), (0.0, 0.5), (-1.0, 0.5),
                         (1.0, None), (1.0, 2.0), (1.0, -0.5)):
        ratio, mult = I.chop_factors(hs, swell_hs)
        assert ratio is None, (hs, swell_hs, ratio)
        assert mult == 1.0, (hs, swell_hs, mult)


def test_the_unknown_neutral_is_one_by_construction_not_by_the_curve():
    """The neutral must be 1.0 because an unknown should not move the rating — NOT
    because the curve's first knot happens to be (0.0, 1.0). Coercing None to 0.0 and
    interpolating gives the same answer today and would silently follow any future
    recalibration of that knot.

    Pinned by temporarily bending the curve so _interp(0.0) is 0.5, and asserting the
    unknown still scores 1.0. The curve is restored in a finally, and
    test_the_curve_itself_is_untouched pins its real values.
        bent curve: [(0.0, 0.5), (1.0, 0.5)] -> _interp(0.0) = 0.5
        chop_multiplier(None) must still be 1.0
    """
    saved = I._CHOP_POINTS
    try:
        I._CHOP_POINTS = [(0.0, 0.5), (1.0, 0.5)]
        assert I.chop_multiplier(0.0) == 0.5, "the bend did not take effect"
        assert I.chop_multiplier(None) == 1.0, (
            "the unknown neutral is following the curve instead of being 1.0 outright")
    finally:
        I._CHOP_POINTS = saved
    assert I.chop_multiplier(0.0) == 1.0        # restored


def test_a_negative_swell_height_does_not_produce_maximum_chop():
    """Before the guard, swell_hs = -0.5 with hs = 1.0 computed (1.0 - -0.5)/1.0 = 1.5,
    clamped to exactly 1.0 — MAXIMUM chop, multiplier 0.30, from an impossible input.
    It is now an unknown and scores neutral.
        old: min(1.0, 1.5) = 1.0 -> chop_multiplier(1.0) = 0.30
        new: None                -> 1.0
    """
    ratio, mult = I.chop_factors(1.0, -0.5)
    assert ratio is None, ratio
    assert mult == 1.0, mult
    assert mult != 0.3, "an impossible input is still scoring as maximum chop"


def test_the_curve_itself_is_untouched():
    """This commit is consistency, not recalibration. The knots and their multipliers are
    pinned by value here as well as in test_composite_aggregation, so a calibration change
    smuggled in alongside a consistency fix fails in both places.
        _CHOP_POINTS = [(0,1.0),(0.2,1.0),(0.4,0.85),(0.6,0.65),(0.8,0.45),(1.0,0.3)]
    """
    assert I._CHOP_POINTS == [
        (0.0, 1.0), (0.2, 1.0), (0.4, 0.85), (0.6, 0.65), (0.8, 0.45), (1.0, 0.3)]
    assert _close(I.chop_multiplier(0.0), 1.0)
    assert _close(I.chop_multiplier(0.2), 1.0)
    assert _close(I.chop_multiplier(0.4), 0.85)
    assert _close(I.chop_multiplier(0.6), 0.65)
    assert _close(I.chop_multiplier(0.8), 0.45)
    assert _close(I.chop_multiplier(1.0), 0.3)


# --------------------------------------------------------------------------- #
# 3 — chop_factors: one computation, both values                               #
# --------------------------------------------------------------------------- #
def test_chop_factors_returns_the_matching_pair():
    """Hand-computed. The ratio is rounded to the 3 dp both columns persist at, and the
    multiplier is derived FROM THE ROUNDED RATIO so the round trip is exact at the only
    precision anyone can read back.

        hs 1.0, sh 0.6  -> ratio 0.4    -> curve knot                    -> 0.85
        hs 1.0, sh 1.0  -> ratio 0.0    -> curve knot                    -> 1.0
        hs 1.0, sh 0.0  -> ratio 1.0    -> curve knot                    -> 0.3
        hs 2.0, sh 0.5  -> ratio 0.75   -> between (0.6,0.65),(0.8,0.45):
                                           0.65 + (0.75-0.6)/0.2*(-0.20) = 0.65-0.15 = 0.5
        hs 1.6, sh 1.1  -> 0.3125 rounds to 0.312 -> between (0.2,1.0),(0.4,0.85):
                                           1.0 + (0.312-0.2)/0.2*(-0.15) = 1.0-0.084 = 0.916
    """
    assert I.chop_factors(1.0, 0.6) == (0.4, 0.85)
    assert I.chop_factors(1.0, 1.0) == (0.0, 1.0)
    assert I.chop_factors(1.0, 0.0) == (1.0, 0.3)
    r, m = I.chop_factors(2.0, 0.5)
    assert _close(r, 0.75) and _close(m, 0.5), (r, m)
    r, m = I.chop_factors(1.6, 1.1)
    assert _close(r, 0.312), r          # ROUNDED, not 0.3125
    assert _close(m, 0.916), m


def test_the_multiplier_is_derived_from_the_rounded_ratio():
    """Why the rounding lives inside chop_factors. Both columns persist at 3 dp and the
    curve has non-integer slopes, so a multiplier derived from the FULL-precision ratio
    would not reproduce from the stored ratio.

        hs 1.6, sh 1.1: exact ratio 0.3125
            from the exact ratio:   1.0 + (0.3125-0.2)/0.2*(-0.15) = 1.0 - 0.084375 = 0.915625
            from the rounded 0.312: 1.0 + (0.312 -0.2)/0.2*(-0.15) = 1.0 - 0.084     = 0.916
        The pair must use the second, or chop_multiplier(0.312) = 0.916 would not equal a
        stored 0.916-vs-0.915625 rounded to 0.916 by luck rather than by construction.
    """
    ratio, mult = I.chop_factors(1.6, 1.1)
    assert _close(ratio, 0.312)
    assert _close(mult, 0.916)
    assert not _close(mult, 0.915625, tol=1e-6), "multiplier came from the unrounded ratio"


# --------------------------------------------------------------------------- #
# 4 — the round-trip invariant, on all three writer paths                      #
# --------------------------------------------------------------------------- #
def _spot_nwps(name="T"):
    return {"name": name, "swell_window_source": "nwps", "nwps_wfo": "mtr",
            "orientation_deg": 270.0, "offshore_wind_deg": 90.0,
            "swell_window_arcs": [{"min": 200, "max": 340, "span": 144}],
            "optimal_swell_dir": 270}


def _spot_mop(name="T"):
    return {"name": name, "swell_window_source": "cdip_mop",
            "mop_shore_normal": 270.0, "orientation_deg": 270.0,
            "offshore_wind_deg": 90.0,
            "swell_window_arcs": [{"min": 200, "max": 340, "span": 144}],
            "optimal_swell_dir": 270}


def _entry(t=_BASE, **kw):
    e = {"valid_time": _iso(t), "stars": 1.0, "wind_mult": 0.85, "tide_mult": 0.72,
         "wind_dir": 90.0, "wind_speed": 8.0}
    e.update(kw)
    return e


def test_the_round_trip_invariant_holds_on_every_path():
    """THE ACCEPTANCE CRITERION. For every rated entry, on every writer path,
    recomputing the multiplier from the PERSISTED ratio reproduces the PERSISTED
    multiplier at the stored precision.

    Covered here: the nwps override path and the mop override path, each over a set of
    hours spanning a real ratio, a genuine zero, a present-but-zero swell height, and an
    unknown. rate_spot's own path is covered by the direct chop_factors pins above and by
    test_rate_spot_writes_a_matching_pair below.

    Before this change the overrides wrote chop_mult and left rate_spot's chop_ratio in
    place, so this equality was false for 19.5% of production rows.
    """
    # --- nwps override ---
    hours = [
        (1.00, 0.60),   # ratio 0.400 -> 0.85
        (1.00, 1.00),   # ratio 0.000 -> 1.00   genuine zero
        (1.00, 0.00),   # ratio 1.000 -> 0.30   present-but-zero swell
        (1.00, None),   # UNKNOWN     -> 1.00, ratio None
    ]
    entries = [_entry(_BASE + i * 3600) for i in range(len(hours))]
    series = {(_BASE + i * 3600) // 3600: (hs, 12.0, 270.0, sh)
              for i, (hs, sh) in enumerate(hours)}
    stats = nn.apply_nwps_overrides({"T": entries}, [_spot_nwps()],
                                    _fetch=lambda _s: series)
    assert stats["fed"] == 1, stats
    for e in entries:
        assert "chop_ratio" in e, "the override must write the ratio, not only the mult"
        assert _close(round(I.chop_multiplier(e["chop_ratio"]), 3), e["chop_mult"]), e

    # --- mop override ---
    m_entries = [_entry(_BASE + i * 3600) for i in range(len(hours))]
    m_series = {(_BASE + i * 3600) // 3600: (hs, 14.0, 270.0, sh)
                for i, (hs, sh) in enumerate(hours)}
    m_stats = mop.apply_mop_overrides({"T": m_entries}, [_spot_mop()],
                                      _fetch=lambda _s: m_series)
    assert m_stats["fed"] == 1, m_stats
    for e in m_entries:
        assert "chop_ratio" in e, "the mop override must write the ratio too"
        assert _close(round(I.chop_multiplier(e["chop_ratio"]), 3), e["chop_mult"]), e


def test_the_override_writes_the_exact_pair_hand_computed():
    """Not just self-consistent — the right values. nwps override, one hour each:

        swh 1.00, shts 0.60 -> ratio (1.0-0.6)/1.0 = 0.400 -> knot        -> 0.85
        swh 1.00, shts 1.00 -> ratio 0.000                 -> knot        -> 1.00
        swh 1.00, shts 0.00 -> ratio 1.000                 -> knot        -> 0.30
        swh 1.00, shts None -> UNKNOWN                     -> None / 1.00
    """
    hours = [(1.00, 0.60), (1.00, 1.00), (1.00, 0.00), (1.00, None)]
    expected = [(0.4, 0.85), (0.0, 1.0), (1.0, 0.3), (None, 1.0)]
    entries = [_entry(_BASE + i * 3600) for i in range(len(hours))]
    series = {(_BASE + i * 3600) // 3600: (hs, 12.0, 270.0, sh)
              for i, (hs, sh) in enumerate(hours)}
    nn.apply_nwps_overrides({"T": entries}, [_spot_nwps()], _fetch=lambda _s: series)
    for e, (er, em) in zip(entries, expected):
        assert e["chop_ratio"] == er, (e["chop_ratio"], er)
        assert _close(e["chop_mult"], em), (e["chop_mult"], em)


def test_the_mop_override_writes_the_exact_pair_hand_computed():
    """The MOP twin of the nwps pin above. Needed on its own because the round-trip
    invariant alone cannot catch a pair computed from the WRONG fields: chop_factors with
    hs and swell_hs swapped yields (None, 1.0), which is perfectly self-consistent and
    perfectly wrong.

    MOP series records are (hs, tp, dp, swell_hs), so the pair is chop_factors(hs, swh):
        hs 1.00, swh 0.60 -> ratio (1.0-0.6)/1.0 = 0.400 -> knot -> 0.85
        hs 1.00, swh 1.00 -> ratio 0.000                 -> knot -> 1.00
        hs 1.00, swh 0.00 -> ratio 1.000                 -> knot -> 0.30
        hs 1.00, swh None -> UNKNOWN                     -> None / 1.00
    Swapped, the first row would be chop_factors(0.6, 1.0): swell 1.0 > total 0.6 -> None.
    """
    hours = [(1.00, 0.60), (1.00, 1.00), (1.00, 0.00), (1.00, None)]
    expected = [(0.4, 0.85), (0.0, 1.0), (1.0, 0.3), (None, 1.0)]
    entries = [_entry(_BASE + i * 3600) for i in range(len(hours))]
    series = {(_BASE + i * 3600) // 3600: (hs, 14.0, 270.0, sh)
              for i, (hs, sh) in enumerate(hours)}
    mop.apply_mop_overrides({"T": entries}, [_spot_mop()], _fetch=lambda _s: series)
    for e, (er, em) in zip(entries, expected):
        assert e["chop_ratio"] == er, (e["chop_ratio"], er)
        assert _close(e["chop_mult"], em), (e["chop_mult"], em)


def test_rate_spot_writes_a_matching_pair_and_a_null_for_an_unknown():
    """rate_spot is the third writer and needs its own pin: the two overrides can be
    perfectly consistent while rate_spot still persists an unknown as 0.0.

    Two hours on one spot, driven through rate_spot directly:
        hs 1.0, swell_hs 0.6 -> ratio (1.0-0.6)/1.0 = 0.400 -> knot -> 0.85
        hs 1.0, swell_hs None-> UNKNOWN                     -> None / 1.00
    """
    spot = {"name": "T", "lat": 36.0, "lng": -75.0, "orientation_deg": 270.0,
            "offshore_wind_deg": 90.0,
            "swell_window_arcs": [{"min": 200, "max": 340, "span": 144}],
            "optimal_swell_dir": 270}
    forecast = [
        {"valid_time": _iso(_BASE), "hs": 1.0, "swell_hs": 0.6, "tp": 12.0, "dp": 270.0},
        {"valid_time": _iso(_BASE + 3600), "hs": 1.0, "tp": 12.0, "dp": 270.0},
    ]
    rated = I.rate_spot(spot, forecast, None)
    assert len(rated) == 2, rated

    assert _close(rated[0]["chop_ratio"], 0.4), rated[0]["chop_ratio"]
    assert _close(rated[0]["chop_mult"], 0.85), rated[0]["chop_mult"]

    assert rated[1]["chop_ratio"] is None, rated[1]["chop_ratio"]
    assert rated[1]["chop_ratio"] != 0.0
    assert _close(rated[1]["chop_mult"], 1.0), rated[1]["chop_mult"]

    for r in rated:
        assert _close(round(I.chop_multiplier(r["chop_ratio"]), 3), r["chop_mult"]), r


def test_an_unknown_persists_as_none_not_zero_through_the_override():
    """The frontend distinction, end to end. A missing shts must leave chop_ratio None so
    classifyChop returns 'unknown'; a 0.0 there would render as "Clean — 0% wind sea"."""
    e = _entry()
    series = {_BASE // 3600: (1.0, 12.0, 270.0, None)}
    nn.apply_nwps_overrides({"T": [e]}, [_spot_nwps()], _fetch=lambda _s: series)
    assert e["chop_ratio"] is None, e["chop_ratio"]
    assert e["chop_ratio"] != 0.0
    assert e["chop_mult"] == 1.0, e["chop_mult"]


# --------------------------------------------------------------------------- #
# 5 — the two paths agree on a present-but-zero swell height                    #
# --------------------------------------------------------------------------- #
def test_a_present_but_zero_swell_height_gives_the_same_answer_on_both_paths():
    """Defect B's second half. `swell_hs if swell_hs else hs` could not tell None from
    0.0: a zero shts substituted hs for itself and scored ratio 0 -> mult 1.00, while
    rate_spot read the identical input as ratio 1.0 -> mult 0.30. Opposite ends of the
    curve from the same data, and the override wrote last.

        hs 1.0, swell_hs 0.0 -> ratio (1.0-0.0)/1.0 = 1.0 -> mult 0.30, BOTH paths
    """
    direct = I.chop_factors(1.0, 0.0)
    assert direct == (1.0, 0.3), direct

    _, _, _, cm_nwps, _ = nn.nwps_stars(1.0, 12.0, 270.0, 0.0, 270.0)
    _, _, _, cm_mop, _ = mop.mop_stars(1.0, 14.0, 270.0, 0.0, 270.0)
    assert _close(cm_nwps, 0.3), cm_nwps
    assert _close(cm_mop, 0.3), cm_mop
    assert cm_nwps == cm_mop == direct[1]
    # and NOT the old answer
    assert not _close(cm_nwps, 1.0), "the swell_hs-if-else-hs idiom is still in place"


def test_a_missing_swell_height_still_falls_to_neutral_on_both_paths():
    """The behaviour nwps_stars' docstring promises — "if shts is missing, chop falls to
    neutral" — is preserved, but now via an explicit unknown rather than by pretending
    the swell equals the total.
        hs 1.0, swell_hs None -> UNKNOWN -> mult 1.0
    """
    assert I.chop_factors(1.0, None) == (None, 1.0)
    _, _, _, cm_nwps, _ = nn.nwps_stars(1.0, 12.0, 270.0, None, 270.0)
    _, _, _, cm_mop, _ = mop.mop_stars(1.0, 14.0, 270.0, None, 270.0)
    assert cm_nwps == 1.0 and cm_mop == 1.0, (cm_nwps, cm_mop)


def test_the_injected_chop_pair_is_used_when_given():
    """The override computes the pair once and hands it in; the stars function must use
    it rather than recomputing. Passing a deliberately distinctive pair proves the
    parameter is wired, not ignored."""
    _, _, _, cm, _ = nn.nwps_stars(1.0, 12.0, 270.0, 0.6, 270.0, chop=(0.9, 0.375))
    assert _close(cm, 0.375), cm
    _, _, _, cm, _ = mop.mop_stars(1.0, 14.0, 270.0, 0.6, 270.0, chop=(0.9, 0.375))
    assert _close(cm, 0.375), cm


# --------------------------------------------------------------------------- #
# 6 — the wind cap is re-judged against the chop the row actually has           #
# --------------------------------------------------------------------------- #
def test_the_wind_cap_is_recomputed_against_the_overrides_chop():
    """Defect C. wind_multiplier caps an offshore bonus at 0.8 when chop_ratio > 0.4.
    The entry arrives carrying wind_mult 1.2 — the uncapped offshore bonus rate_spot
    computed when it thought the chop was low. The override's own chop is 0.75, above
    the threshold, so the cap must now fire and the stored wind_mult must drop.

        wind_dir 90 == offshore_wind_deg 90 -> 0 deg off -> base 1.2
        wind_speed 8.0 -> above the 5 m/s light-wind blend, no blending
        override chop: swh 2.0, shts 0.5 -> ratio (2.0-0.5)/2.0 = 0.75 > 0.4
        -> base = min(1.2, 0.8) = 0.8
    """
    e = _entry(wind_mult=1.2, wind_dir=90.0, wind_speed=8.0)
    series = {_BASE // 3600: (2.0, 12.0, 270.0, 0.5)}
    nn.apply_nwps_overrides({"T": [e]}, [_spot_nwps()], _fetch=lambda _s: series)
    assert _close(e["chop_ratio"], 0.75), e["chop_ratio"]
    assert _close(e["wind_mult"], 0.8), e["wind_mult"]
    assert not _close(e["wind_mult"], 1.2), "the frozen offshore bonus survived"


def test_the_wind_cap_does_not_fire_when_the_overrides_chop_is_low():
    """The other direction — the cap must not be applied indiscriminately.
        override chop: swh 1.0, shts 0.8 -> ratio 0.2, NOT > 0.4
        -> base stays 1.2
    """
    e = _entry(wind_mult=1.2, wind_dir=90.0, wind_speed=8.0)
    series = {_BASE // 3600: (1.0, 12.0, 270.0, 0.8)}
    nn.apply_nwps_overrides({"T": [e]}, [_spot_nwps()], _fetch=lambda _s: series)
    assert _close(e["chop_ratio"], 0.2), e["chop_ratio"]
    assert _close(e["wind_mult"], 1.2), e["wind_mult"]


def test_an_unknown_chop_leaves_the_wind_cap_unapplied_rather_than_crashing():
    """chop_ratio can now be None, and wind_multiplier's cap compares it with > 0.4.
    None must be treated as "cannot judge" — not raise, and not cap.
        chop unknown -> no cap -> base 1.2 survives
    """
    assert _close(I.wind_multiplier(90.0, 8.0, 90.0, None), 1.2)
    e = _entry(wind_mult=1.2, wind_dir=90.0, wind_speed=8.0)
    series = {_BASE // 3600: (1.0, 12.0, 270.0, None)}
    nn.apply_nwps_overrides({"T": [e]}, [_spot_nwps()], _fetch=lambda _s: series)
    assert e["chop_ratio"] is None
    assert _close(e["wind_mult"], 1.2), e["wind_mult"]


def test_the_wind_mult_is_left_alone_when_the_inputs_are_missing():
    """No wind direction, no speed, or no offshore bearing means the cap cannot be
    re-judged. The entry's existing wind_mult must be kept as-is rather than guessed at
    or reset to neutral."""
    for kw in ({"wind_dir": None}, {"wind_speed": None}):
        e = _entry(wind_mult=0.44, **kw)
        series = {_BASE // 3600: (2.0, 12.0, 270.0, 0.5)}
        nn.apply_nwps_overrides({"T": [e]}, [_spot_nwps()], _fetch=lambda _s: series)
        assert _close(e["wind_mult"], 0.44), (kw, e["wind_mult"])
    spot = _spot_nwps()
    spot["offshore_wind_deg"] = None
    e = _entry(wind_mult=0.44)
    series = {_BASE // 3600: (2.0, 12.0, 270.0, 0.5)}
    nn.apply_nwps_overrides({"T": [e]}, [spot], _fetch=lambda _s: series)
    assert _close(e["wind_mult"], 0.44), e["wind_mult"]


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} chop-consistency checks passed")


if __name__ == "__main__":
    _run_all()
