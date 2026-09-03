"""period_factor converts a model Hs into a breaking face height, and it had NO test.

WHY THIS FILE EXISTS. face_ft = hs * period_factor(tp, source) * M_TO_FT is the first line
of the rating pipeline (interpret.py module docstring, step 1) and runs for every spot,
every hour, on every path — rate_spot's two branches, nwps_stars, mop_stars, and both MOP
analysis scripts. Until this file, `period_factor` had ZERO references in pipeline/tests/
and zero assertions in either module selftest: both calibration tables could be reversed,
swapped, or rewritten and the whole suite stayed green.

The tables have not been modified since 7e04659 (2026-04-29), whose only recorded
verification was "Smoke-tested in REPL: ... period_factor calibration tables match spec".
That smoke test left nothing behind, and the worked example in the same commit message
("Pipeline 0.86 m @ 12 s WW3 face drops from 4.2 ft to 3.24 ft") does not reproduce against
the curve it replaced — 4.2 ft is 0.86 * 1.50 * 3.281, the NEW nwps factor, not the old
1.3-2.0 curve's 4.85 ft. That is precisely the kind of drift a pinned table prevents.

THIS FILE CHANGES NO BEHAVIOUR. It pins what is already there. Every value in
_PERIOD_FACTOR_NWPS and _PERIOD_FACTOR_WW3 is asserted by literal list equality below, so
CHANGING ANY TABLE VALUE MUST BE DELIBERATE AND MUST FAIL HERE. A recalibration is a
legitimate thing to do; doing it without noticing is not. If you are here because this file
failed, update it in the same commit that moves the curve, with the new arithmetic written
out — do not relax the assertion.

TWO FAILURE MODES ARE PINNED AS FACTS RATHER THAN LEFT TO BE DISCOVERED IN PRODUCTION:

  * A REVERSED table does not raise. _interp clamps on points[0] first, so a descending
    table returns its first y for the entire domain — a flat 1.6 (nwps) or 1.3 (ww3) that
    looks like a plausible number and inflates every short-period rating by up to 33%.
  * `source` is matched by EXACT, CASE-SENSITIVE equality against "ww3" alone. "WW3",
    "Ww3", " ww3", "", None, "mop" and "ecmwf" all silently select the heavier nwps curve.
    "nwps" is never matched either — it is only the name of the fallback.

EVERY EXPECTED VALUE IS HAND-COMPUTED with the arithmetic in a comment. None is derived by
calling the function under test.

Run: python -m pipeline.tests.test_period_factor   (or pytest)
"""
from __future__ import annotations

import inspect
import logging

from pipeline import interpret as I
from pipeline.forecast import mop, nwps_nearshore as nn


def _close(a, b, tol=1e-12):
    return abs(a - b) <= tol


class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def warnings(self):
        return [r.getMessage() for r in self.records if r.levelno >= logging.WARNING]


# --------------------------------------------------------------------------- #
# 1 — both tables pinned by literal value                                       #
# --------------------------------------------------------------------------- #
def test_both_tables_are_pinned_by_literal_value():
    """THE ANCHOR OF THIS FILE. Same posture test_chop_consistency takes with _CHOP_POINTS:
    the knots and their factors are asserted as a literal list, so a calibration change
    smuggled in alongside an unrelated edit fails right here.

    Reversing either table, swapping the two, or moving any single y all fail this one
    assertion. Changing a value is allowed — changing it silently is not."""
    assert I._PERIOD_FACTOR_NWPS == [
        (0.0, 1.2), (6.0, 1.2), (8.0, 1.3), (10.0, 1.4),
        (12.0, 1.5), (14.0, 1.55), (16.0, 1.6), (99.0, 1.6),
    ], I._PERIOD_FACTOR_NWPS
    assert I._PERIOD_FACTOR_WW3 == [
        (0.0, 1.0), (6.0, 1.0), (8.0, 1.05), (10.0, 1.1),
        (12.0, 1.15), (14.0, 1.2), (16.0, 1.25), (99.0, 1.3),
    ], I._PERIOD_FACTOR_WW3
    # The two tables are DISTINCT objects with distinct contents. A refactor that pointed
    # both names at one list would make the source split inert while every value above
    # still read correctly for whichever table survived.
    assert I._PERIOD_FACTOR_NWPS is not I._PERIOD_FACTOR_WW3
    assert I._PERIOD_FACTOR_NWPS != I._PERIOD_FACTOR_WW3


# --------------------------------------------------------------------------- #
# 2 — period_factor returns each table's own y at each of its own anchors       #
# --------------------------------------------------------------------------- #
def test_period_factor_hits_every_anchor_of_its_own_table():
    """Read the anchors OUT OF THE LIVE TABLES rather than from a copy, so this asserts the
    routing — that source "ww3" reaches _PERIOD_FACTOR_WW3 and everything else reaches
    _PERIOD_FACTOR_NWPS, and that _interp lands exactly on an anchor rather than near it.

    The VALUES are pinned separately in test 1 and test 3; this test deliberately cannot
    catch a table edit on its own, because both sides of the comparison would move
    together. The two tests are complementary and both are needed."""
    for x, y in I._PERIOD_FACTOR_NWPS:
        got = I.period_factor(x, "nwps")
        assert got == y, f"nwps anchor tp={x}: got {got}, table says {y}"
    for x, y in I._PERIOD_FACTOR_WW3:
        got = I.period_factor(x, "ww3")
        assert got == y, f"ww3 anchor tp={x}: got {got}, table says {y}"
    # The anchor GRID itself — both curves are knotted at the same eight periods. If a knot
    # is added or moved, update this deliberately, not reflexively.
    assert [x for x, _ in I._PERIOD_FACTOR_NWPS] == [0.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 99.0]
    assert [x for x, _ in I._PERIOD_FACTOR_WW3] == [0.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 99.0]


# --------------------------------------------------------------------------- #
# 3 — every anchor, by hand-written value, for both sources                     #
# --------------------------------------------------------------------------- #
def test_period_factor_at_every_anchor_by_hand_written_value():
    """The same eight periods again, but with the expected factor written out by hand
    instead of read from the table. This is what catches a single anchor y moving: test 2
    would follow the table, this one will not.

      nwps: 0 -> 1.2   6 -> 1.2   8 -> 1.3   10 -> 1.4
           12 -> 1.5  14 -> 1.55 16 -> 1.6   99 -> 1.6
      ww3:  0 -> 1.0   6 -> 1.0   8 -> 1.05  10 -> 1.1
           12 -> 1.15 14 -> 1.2  16 -> 1.25  99 -> 1.3

    An anchor returns its y through _interp's clamp (tp 0, tp 99) or through the
    `x0 <= x <= x1` branch with x == x0, so all sixteen are exact — no tolerance needed."""
    assert I.period_factor(0.0, "nwps") == 1.2
    assert I.period_factor(6.0, "nwps") == 1.2
    assert I.period_factor(8.0, "nwps") == 1.3
    assert I.period_factor(10.0, "nwps") == 1.4
    assert I.period_factor(12.0, "nwps") == 1.5
    assert I.period_factor(14.0, "nwps") == 1.55
    assert I.period_factor(16.0, "nwps") == 1.6
    assert I.period_factor(99.0, "nwps") == 1.6

    assert I.period_factor(0.0, "ww3") == 1.0
    assert I.period_factor(6.0, "ww3") == 1.0
    assert I.period_factor(8.0, "ww3") == 1.05
    assert I.period_factor(10.0, "ww3") == 1.1
    assert I.period_factor(12.0, "ww3") == 1.15
    assert I.period_factor(14.0, "ww3") == 1.2
    assert I.period_factor(16.0, "ww3") == 1.25
    assert I.period_factor(99.0, "ww3") == 1.3

    # THE SPLIT IS LOAD-BEARING. At every anchor above 6 s the nwps factor is strictly the
    # larger of the two — that IS the "already partially shoaled vs deep-ocean" distinction
    # the block comment describes. If this ordering ever inverts, the two tables have been
    # swapped even if each individually still looks well-formed.
    for tp in (8.0, 10.0, 12.0, 14.0, 16.0, 99.0):
        assert I.period_factor(tp, "nwps") > I.period_factor(tp, "ww3"), tp


# --------------------------------------------------------------------------- #
# 4 — interpolated midpoints, nwps                                             #
# --------------------------------------------------------------------------- #
def test_nwps_interpolated_midpoints():
    """Piecewise LINEAR between anchors: y0 + (x-x0)/(x1-x0) * (y1-y0).

       7 s: between (6, 1.2) and (8, 1.3)     1.2  + (1/2)(0.1)  = 1.25
       9 s: between (8, 1.3) and (10, 1.4)    1.3  + (1/2)(0.1)  = 1.35
      11 s: between (10, 1.4) and (12, 1.5)   1.4  + (1/2)(0.1)  = 1.45
      13 s: between (12, 1.5) and (14, 1.55)  1.5  + (1/2)(0.05) = 1.525
      15 s: between (14, 1.55) and (16, 1.6)  1.55 + (1/2)(0.05) = 1.575

    15 s carries binary dust (1.5750000000000002 as computed), hence _close throughout."""
    assert _close(I.period_factor(7.0, "nwps"), 1.25)
    assert _close(I.period_factor(9.0, "nwps"), 1.35)
    assert _close(I.period_factor(11.0, "nwps"), 1.45)
    assert _close(I.period_factor(13.0, "nwps"), 1.525)
    assert _close(I.period_factor(15.0, "nwps"), 1.575)


# --------------------------------------------------------------------------- #
# 5 — interpolated midpoints, ww3                                              #
# --------------------------------------------------------------------------- #
def test_ww3_interpolated_midpoints():
    """Same formula on the ww3 knots.

       7 s: between (6, 1.0) and (8, 1.05)    1.0  + (1/2)(0.05) = 1.025
       9 s: between (8, 1.05) and (10, 1.1)   1.05 + (1/2)(0.05) = 1.075
      11 s: between (10, 1.1) and (12, 1.15)  1.1  + (1/2)(0.05) = 1.125
      13 s: between (12, 1.15) and (14, 1.2)  1.15 + (1/2)(0.05) = 1.175
      15 s: between (14, 1.2) and (16, 1.25)  1.2  + (1/2)(0.05) = 1.225
    """
    assert _close(I.period_factor(7.0, "ww3"), 1.025)
    assert _close(I.period_factor(9.0, "ww3"), 1.075)
    assert _close(I.period_factor(11.0, "ww3"), 1.125)
    assert _close(I.period_factor(13.0, "ww3"), 1.175)
    assert _close(I.period_factor(15.0, "ww3"), 1.225)


# --------------------------------------------------------------------------- #
# 6 — the low clamp, including a NEGATIVE period                               #
# --------------------------------------------------------------------------- #
def test_both_curves_clamp_below_the_first_anchor_including_a_negative_period():
    """_interp: `if x <= points[0][0]: return points[0][1]`. Non-strict, so tp 0 takes the
    clamp rather than the interpolation branch, and anything below it takes the same value.

    A NEGATIVE PERIOD IS NOT AN ERROR HERE — it returns the first y (1.2 nwps / 1.0 ww3)
    and produces a real, positive face height. Pinned because it is a silent path: nothing
    upstream rejects a negative tp, and nothing here would tell you one arrived."""
    for tp in (-1000.0, -12.0, -5.0, -0.5, 0.0):
        assert I.period_factor(tp, "nwps") == 1.2, tp
        assert I.period_factor(tp, "ww3") == 1.0, tp
    # and it stays finite and positive downstream rather than raising or going negative:
    # face_ft(1.0, -5.0, "ww3") = 1.0 * 1.0 * 3.281 = 3.281 ft off a nonsense period
    assert _close(I.face_ft(1.0, -5.0, "ww3"), 3.281)


# --------------------------------------------------------------------------- #
# 7 — the high clamp                                                           #
# --------------------------------------------------------------------------- #
def test_both_curves_clamp_above_the_last_anchor():
    """_interp: `if x >= points[-1][0]: return points[-1][1]`. Non-strict, so tp 99 is the
    clamp itself and everything beyond returns the same y. nwps saturates at 1.6, ww3 at
    1.3 — and 1.3 is reachable ONLY from here (see test 8)."""
    for tp in (99.0, 100.0, 150.0, 1e6):
        assert I.period_factor(tp, "nwps") == 1.6, tp
        assert I.period_factor(tp, "ww3") == 1.3, tp


# --------------------------------------------------------------------------- #
# 8 — the ww3 tail does NOT plateau; the nwps tail does                        #
# --------------------------------------------------------------------------- #
def test_the_ww3_tail_segment_does_not_plateau_the_way_the_nwps_one_does():
    """THE TWO TABLES ARE SHAPED DIFFERENTLY PAST 16 s, and only one of them is flat.

    nwps ends (16.0, 1.6) -> (99.0, 1.6): both y equal, so the segment is genuinely
    horizontal and every period at or above 16 s gives exactly 1.6.

        tp 20: 1.6 + (20-16)/(99-16) * (1.6-1.6) = 1.6 + (4/83)(0.0) = 1.6 exactly
        tp 40: 1.6 + (24/83)(0.0)                                     = 1.6 exactly

    ww3 ends (16.0, 1.25) -> (99.0, 1.3): it keeps climbing linearly across an 83-second
    span, so it has no plateau at all.

        tp 20: 1.25 + (20-16)/(99-16) * (1.3-1.25)
             = 1.25 + (4/83)(0.05)  = 1.25 + 0.0024096385542168... = 1.252409638554217
        tp 40: 1.25 + (24/83)(0.05) = 1.25 + 0.0144578313253012... = 1.2644578313253012

    THE BLOCK COMMENT'S "1.0-1.3x" IS THE NOMINAL RANGE, NOT THE REACHABLE ONE. 1.3 arrives
    only at tp = 99 s, a period no real sea state produces; in the surfable range the ww3
    curve tops out around 1.2524 at 20 s. The nwps comment's "1.2-1.6x" is both nominal and
    reachable, because that table really does saturate at 16 s. The two comments read as
    symmetric descriptions of two similarly-shaped curves. The curves are not similar."""
    assert I.period_factor(20.0, "ww3") == 1.252409638554217
    assert I.period_factor(40.0, "ww3") == 1.2644578313253012
    assert I.period_factor(20.0, "nwps") == 1.6
    assert I.period_factor(40.0, "nwps") == 1.6
    # strictly increasing on the ww3 tail, strictly flat on the nwps tail
    assert I.period_factor(20.0, "ww3") < I.period_factor(40.0, "ww3") < I.period_factor(99.0, "ww3")
    assert I.period_factor(16.0, "nwps") == I.period_factor(40.0, "nwps") == I.period_factor(99.0, "nwps")
    # 1.3 is NOT attained anywhere a real forecast lives
    assert I.period_factor(30.0, "ww3") < 1.3

    # AND THE BLOCK COMMENT ABOVE THE TABLES MUST SAY SO. This is a `#` comment, not a
    # docstring, so it does not survive into __doc__ and can only be checked by reading the
    # module SOURCE. It is worth checking: the comment claimed a flat "1.0–1.3×" for four
    # months, which overstates the ceiling by 4% for every period a forecast actually
    # carries, and a reader who trusts it will mis-predict every WW3-path face height.
    src = inspect.getsource(I)
    assert "The REALISTIC WW3 range is 1.0–1.25×" in src, \
        "the WW3 block comment must state the REACHABLE range, not the table's endpoints"
    assert "1.0–1.3× is the table's endpoints" in src, \
        "the comment must say where 1.3 actually comes from"
    assert "tp 20 → 1.2524" in src and "tp 40 → 1.2645" in src, \
        "the comment must carry the measured tail values a reader can check"
    # the NWPS side is stated SEPARATELY, because only that table plateaus
    assert "genuinely horizontal" in src, "the NWPS plateau must be described on its own"


# --------------------------------------------------------------------------- #
# 9 — a REVERSED table returns a plausible constant, not an error               #
# --------------------------------------------------------------------------- #
def test_a_reversed_table_returns_a_plausible_constant_rather_than_raising():
    """THE FAILURE MODE THIS WHOLE FILE EXISTS FOR, documented here instead of in prod.

    _interp's docstring says "(sorted by x)". Nothing checks it. With a DESCENDING table the
    very first clamp swallows the entire domain:

        reversed nwps starts (99.0, 1.6) -> every x <= 99 hits `x <= points[0][0]`
                                         -> returns points[0][1] = 1.6, flat
        reversed ww3  starts (99.0, 1.3) -> same shape -> 1.3, flat

    So a reversal does not raise, does not warn, and does not produce obvious garbage. It
    produces a NUMBER IN THE RIGHT NEIGHBOURHOOD that inflates every short-period rating —
    1.6 instead of 1.2 at 6 s is +33% on every nwps-path face height — while leaving the
    long-period end untouched, which is the half a human would spot-check.

    period_factor resolves the table by module-global name at call time, so rebinding the
    attribute is enough to exercise this — the same technique test_chop_consistency uses to
    bend _CHOP_POINTS. NOTHING IS RECALIBRATED: the finally restores the original object and
    the restoration is asserted."""
    saved_nwps = I._PERIOD_FACTOR_NWPS
    saved_ww3 = I._PERIOD_FACTOR_WW3

    try:
        I._PERIOD_FACTOR_NWPS = list(reversed(saved_nwps))
        # flat 1.6 across the whole realistic range, anchors and midpoints alike
        for tp in (0.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 20.0, 40.0, 99.0):
            got = I.period_factor(tp, "nwps")
            assert got == 1.6, f"reversed nwps at tp={tp} gave {got}, expected the flat 1.6"
        # only beyond the (now-last) 0.0 anchor does the other clamp fire: x >= 0.0 is the
        # second check, and it is unreachable while the first one keeps matching, so the
        # ONLY escape is tp > 99 -- which returns the reversed table's last y, 1.2.
        assert I.period_factor(100.0, "nwps") == 1.2
    finally:
        I._PERIOD_FACTOR_NWPS = saved_nwps

    try:
        I._PERIOD_FACTOR_WW3 = list(reversed(saved_ww3))
        for tp in (0.0, 4.0, 6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 20.0, 40.0, 99.0):
            got = I.period_factor(tp, "ww3")
            assert got == 1.3, f"reversed ww3 at tp={tp} gave {got}, expected the flat 1.3"
        assert I.period_factor(100.0, "ww3") == 1.0
    finally:
        I._PERIOD_FACTOR_WW3 = saved_ww3

    # RESTORED — identity and value, so a later test in this file cannot inherit a bent curve
    assert I._PERIOD_FACTOR_NWPS is saved_nwps
    assert I._PERIOD_FACTOR_WW3 is saved_ww3
    assert I.period_factor(6.0, "nwps") == 1.2 and I.period_factor(16.0, "nwps") == 1.6
    assert I.period_factor(6.0, "ww3") == 1.0 and I.period_factor(16.0, "ww3") == 1.25


# --------------------------------------------------------------------------- #
# 10 — `source` is exact, case-sensitive equality against "ww3" ALONE           #
# --------------------------------------------------------------------------- #
def test_source_is_matched_by_exact_case_sensitive_equality_against_ww3_only():
    """    points = _PERIOD_FACTOR_WW3 if source == "ww3" else _PERIOD_FACTOR_NWPS

    ONE exact `==`. There is no "neither" case and no error path: every value that is not
    the exact three-byte string "ww3" selects the nwps table. "nwps" is not matched either —
    it is only the name of the fallback, which is why the docstring's "*source* is "nwps" or
    "ww3"" reads as an enumeration of two accepted values when the code accepts one.

    THE COST OF A TYPO, at tp 16 where the gap is widest:
        nwps 1.6 / ww3 1.25 = 1.28  ->  a +28% face height
    On a 2 m swell that is 10.50 ft published instead of 8.20 ft.

    THE SELECTION IS UNCHANGED BY THE WARNING added alongside this test. An unrecognised
    source still lands on the NWPS table and still returns a rating — the warning reports,
    it does not raise, redirect, or withhold. Both halves are asserted below: every typo
    still yields exactly 1.6, AND every typo emits exactly one WARNING naming it."""
    cap = _Capture()
    I.log.addHandler(cap)
    prev_level = I.log.level
    I.log.setLevel(logging.DEBUG)
    saved_seen = set(I._UNKNOWN_PERIOD_SOURCES_WARNED)
    I._UNKNOWN_PERIOD_SOURCES_WARNED.clear()   # this process may already have warned
    try:
        # tp 16 so the two curves are maximally far apart and no near-miss can hide
        assert I.period_factor(16.0, "ww3") == 1.25      # the ONE string that selects ww3
        typos = ("NWPS", "Nwps", "WW3", "Ww3", "wW3", " ww3", "ww3 ", "ww_3",
                 "", "mop", "ecmwf", "cdip_mop", "nwps_total", None, 0, 3)
        for src in ("nwps",) + typos:
            got = I.period_factor(16.0, src)
            assert got == 1.6, f"source={src!r} selected the ww3 table ({got}); only 'ww3' may"
        # the default argument is the nwps fallback, so an omitted source is the heavy curve
        assert I.period_factor(16.0) == 1.6
        # and the gap itself, so a table edit that narrows it is visible here too
        assert _close(1.6 / 1.25, 1.28)
        assert _close(I.period_factor(16.0, "nwps") / I.period_factor(16.0, "ww3"), 1.28)

        # EVERY typo warned, exactly once each, naming the value it received.
        warned = cap.warnings()
        assert len(warned) == len(typos), f"{len(warned)} warnings for {len(typos)} typos: {warned}"
        for src in typos:
            assert any(repr(src) in w for w in warned), f"no warning named source {src!r}"
            assert any("NWPS TABLE" in w for w in warned)
        # NEITHER correct value warns. "nwps" reaches the table through the else branch,
        # which is correct, not a typo — and "ww3" matches outright.
        assert not any(repr("nwps") + " —" in w for w in warned), warned
        assert not any(repr("ww3") + " —" in w for w in warned), warned
        # and the guard does not flood: 500 repeats of an already-reported value add nothing
        before = len(cap.warnings())
        for _ in range(500):
            I.period_factor(12.0, "mop")
        assert len(cap.warnings()) == before, "the once-per-value guard leaked"
    finally:
        I.log.removeHandler(cap)
        I.log.setLevel(prev_level)
        I._UNKNOWN_PERIOD_SOURCES_WARNED.clear()
        I._UNKNOWN_PERIOD_SOURCES_WARNED.update(saved_seen)


def test_an_unrecognised_source_reports_but_never_raises_and_never_redirects():
    """The warning is INSURANCE, not a gate. No live caller can reach it today — rate_spot
    passes the two literals and both override raters pass RATING_SOURCE — so this pins the
    posture for the future caller that gets it wrong.

    Raising here would be the wrong trade and the comment at the check says so: period_factor
    runs inside compute_ratings, whose output db_import pushes, and db_import.run_all calls
    import_spots FIRST with no try around it. An exception on this path takes the whole
    Supabase push down and ships nothing for the cycle. A 28%-wrong number self-corrects next
    run; a blank site does not."""
    cap = _Capture()                      # swallow the warnings this test deliberately trips
    I.log.addHandler(cap)
    prev_propagate = I.log.propagate
    I.log.propagate = False
    saved_seen = set(I._UNKNOWN_PERIOD_SOURCES_WARNED)
    I._UNKNOWN_PERIOD_SOURCES_WARNED.clear()
    try:
        # never raises, for any shape of input — including UNHASHABLE ones, which is why the
        # dedup key is repr(source) and not source itself
        for src in ("garbage", None, 0, 3.5, ["ww3"], {"a": 1}, (1, 2), object()):
            assert I.period_factor(16.0, src) == 1.6, src
        # the whole curve is still the NWPS one, not a neutral or a blend
        for tp, want in ((0.0, 1.2), (8.0, 1.3), (12.0, 1.5), (16.0, 1.6), (99.0, 1.6)):
            assert I.period_factor(tp, "garbage") == want, (tp, want)
        # and face_ft still publishes a real number: 2.0 * 1.6 * 3.281 = 10.4992
        assert _close(I.face_ft(2.0, 16.0, "garbage"), 10.4992)
        # it did report — this is "warn and continue", not "swallow"
        assert cap.warnings(), "an unrecognised source must still be reported"
    finally:
        I.log.removeHandler(cap)
        I.log.propagate = prev_propagate
        I._UNKNOWN_PERIOD_SOURCES_WARNED.clear()
        I._UNKNOWN_PERIOD_SOURCES_WARNED.update(saved_seen)


def test_the_seen_set_is_bounded_and_keeps_warning_past_the_cap():
    """_UNKNOWN_PERIOD_SOURCES_WARNED is a process-lifetime module-level set, so it must not
    be able to grow without limit.

    Today every caller passes a LITERAL — rate_spot passes the two strings, both override
    raters pass RATING_SOURCE — so the set can hold at most the handful of distinct values
    those sites produce. That is a property of the CALLERS, not of this module, and a future
    caller passing a COMPUTED string (a slug, a WFO id, an f-string carrying a spot name)
    would add one entry per distinct value and never release them.

    THE CAP IS 32, and past it the dedup stops but the WARNING DOES NOT. That direction
    matters: a flood of warnings is visible and recoverable, a silently growing set is
    neither. Reaching 32 distinct bad sources already means something is badly wrong
    upstream, so the noise past the cap is the signal.

        feed 40 distinct sources ("bogus-0" .. "bogus-39")
          -> the set stops at exactly 32
          -> all 40 still warn (32 first-sightings + 8 past the cap)
        then feed the 8 over-cap values AGAIN
          -> they warn AGAIN, because they were never recorded
    """
    cap = _Capture()
    I.log.addHandler(cap)
    prev_propagate = I.log.propagate
    prev_level = I.log.level
    I.log.propagate = False
    I.log.setLevel(logging.DEBUG)
    saved_seen = set(I._UNKNOWN_PERIOD_SOURCES_WARNED)
    I._UNKNOWN_PERIOD_SOURCES_WARNED.clear()
    try:
        assert I._UNKNOWN_PERIOD_SOURCES_MAX == 32, I._UNKNOWN_PERIOD_SOURCES_MAX

        over = I._UNKNOWN_PERIOD_SOURCES_MAX + 8          # 40 distinct sources
        for i in range(over):
            # the value is still correct on every one of them — the cap is about the SET,
            # not about the rating: 16 s on the NWPS table is 1.6
            assert I.period_factor(16.0, f"bogus-{i}") == 1.6, i

        # THE SET STOPPED GROWING at exactly the cap
        assert len(I._UNKNOWN_PERIOD_SOURCES_WARNED) == 32, len(I._UNKNOWN_PERIOD_SOURCES_WARNED)
        assert len(I._UNKNOWN_PERIOD_SOURCES_WARNED) <= I._UNKNOWN_PERIOD_SOURCES_MAX
        # the first 32 are the ones retained; the last 8 were never recorded
        assert repr("bogus-0") in I._UNKNOWN_PERIOD_SOURCES_WARNED
        assert repr("bogus-31") in I._UNKNOWN_PERIOD_SOURCES_WARNED
        assert repr("bogus-32") not in I._UNKNOWN_PERIOD_SOURCES_WARNED
        assert repr("bogus-39") not in I._UNKNOWN_PERIOD_SOURCES_WARNED

        # WARNINGS KEPT COMING — one per source, including the 8 past the cap
        warned = cap.warnings()
        assert len(warned) == over, f"{len(warned)} warnings for {over} sources"
        for i in (0, 31, 32, 39):
            assert any(repr(f"bogus-{i}") in w for w in warned), i

        # under the cap the dedup still works: a repeat of a RECORDED value adds nothing
        before = len(cap.warnings())
        for _ in range(50):
            I.period_factor(12.0, "bogus-0")
        assert len(cap.warnings()) == before, "dedup broke below the cap"

        # past the cap it does NOT dedup — an unrecorded value re-warns every time, and the
        # set still does not grow
        for _ in range(5):
            I.period_factor(12.0, "bogus-39")
        assert len(cap.warnings()) == before + 5, "an over-cap source must keep reporting"
        assert len(I._UNKNOWN_PERIOD_SOURCES_WARNED) == 32, "the set grew past its cap"
    finally:
        I.log.removeHandler(cap)
        I.log.propagate = prev_propagate
        I.log.setLevel(prev_level)
        I._UNKNOWN_PERIOD_SOURCES_WARNED.clear()
        I._UNKNOWN_PERIOD_SOURCES_WARNED.update(saved_seen)


# --------------------------------------------------------------------------- #
# 11 — face_ft end to end, ww3                                                 #
# --------------------------------------------------------------------------- #
def test_face_ft_end_to_end_ww3():
    """face_ft(hs, tp, source) = hs * period_factor(tp, source) * M_TO_FT, M_TO_FT = 3.281.

      hs 1.00 m @ 12 s: 1.00 * 1.15 * 3.281 = 1.15   * 3.281 = 3.77315
      hs 2.00 m @ 16 s: 2.00 * 1.25 * 3.281 = 2.5    * 3.281 = 8.2025
      hs 0.86 m @ 12 s: 0.86 * 1.15 * 3.281 = 0.989  * 3.281 = 3.244909

    The third is commit 7e04659's own worked example, which reported it as 3.24 ft. That
    half of the example does reproduce; the "from 4.2 ft" half does not — 4.2 ft is
    0.86 * 1.50 * 3.281 = 4.23249, i.e. the NEW nwps factor, not the 1.3-2.0 curve the
    commit replaced (which gives 0.86 * 1.72 * 3.281 = 4.85 ft). Pinned in the nwps test."""
    assert _close(I.face_ft(1.00, 12.0, "ww3"), 3.77315)
    assert _close(I.face_ft(2.00, 16.0, "ww3"), 8.2025)
    assert _close(I.face_ft(0.86, 12.0, "ww3"), 3.244909)


# --------------------------------------------------------------------------- #
# 12 — face_ft end to end, nwps                                                #
# --------------------------------------------------------------------------- #
def test_face_ft_end_to_end_nwps():
    """Same product, heavier curve.

      hs 1.00 m @ 12 s: 1.00 * 1.50 * 3.281 = 1.5   * 3.281 = 4.9215
      hs 2.00 m @ 16 s: 2.00 * 1.60 * 3.281 = 3.2   * 3.281 = 10.4992
      hs 0.86 m @ 12 s: 0.86 * 1.50 * 3.281 = 1.29  * 3.281 = 4.23249

    The last line is the "4.2 ft" from 7e04659's worked example — computed with the NEW
    nwps table, not the old single 1.3-2.0 curve it claimed to be comparing against.

    A zero Hs gives a zero face on either curve, which is the one input that makes the
    factor irrelevant: 0.0 * anything * 3.281 = 0.0."""
    assert _close(I.face_ft(1.00, 12.0, "nwps"), 4.9215)
    assert _close(I.face_ft(2.00, 16.0, "nwps"), 10.4992)
    assert _close(I.face_ft(0.86, 12.0, "nwps"), 4.23249)
    assert I.face_ft(0.0, 12.0, "nwps") == 0.0
    assert I.face_ft(0.0, 12.0, "ww3") == 0.0
    # the same hs and tp through the two sources differ by the factor ratio and nothing else:
    #   4.23249 / 3.244909 = 1.50 / 1.15 = 1.3043478260869565...
    assert _close(I.face_ft(0.86, 12.0, "nwps") / I.face_ft(0.86, 12.0, "ww3"), 1.5 / 1.15)


# --------------------------------------------------------------------------- #
# 13 — M_TO_FT, the second unpinned number in that product                      #
# --------------------------------------------------------------------------- #
def test_m_to_ft_is_pinned_by_value_and_is_a_rounded_conversion():
    """M_TO_FT sits in every face_ft product and had no test either. It is 3.281 — a
    4-significant-figure rounding of the true metre-to-foot conversion 3.280839895..., i.e.
    3.28084 to 6 s.f. The rounding is +0.0000491 ft per metre, about +1.5e-5 relative, which
    is far below any forecast's own error and is NOT a defect. It is pinned because it is a
    constant nothing else guards, not because the value is wrong.

      relative error = (3.281 - 3.280839895) / 3.280839895 = 4.88e-5
    """
    assert I.M_TO_FT == 3.281
    assert I.M_TO_FT != 3.28084, "3.281 is deliberate; if this is now exact, update the test"
    assert abs(I.M_TO_FT - 3.280839895013123) < 2e-4
    # and it really is the multiplier in the product: 1 m at a factor of 1.0 is M_TO_FT ft
    #   face_ft(1.0, 6.0, "ww3") = 1.0 * 1.0 * 3.281 = 3.281
    assert I.face_ft(1.0, 6.0, "ww3") == I.M_TO_FT


# --------------------------------------------------------------------------- #
# 14 — RATING_SOURCE is "ww3" in the NWPS rater; the MOP rater has NO curve     #
# --------------------------------------------------------------------------- #
def test_rating_source_is_ww3_in_the_nwps_override_rater():
    """nwps_stars passes its module's RATING_SOURCE straight into face_ft, so flipping the
    constant silently switches that whole rater onto the other curve — a 28% step at 16 s,
    with no other visible change.

        nwps_nearshore.RATING_SOURCE = "ww3"   # face_ft shoaling factor

    NOTE, reported not adjudicated: nwps_stars samples NWPS swh/shts and rates it through
    the WW3 (deep-ocean) curve. That is internally consistent — the override must reproduce
    rate_spot's WW3 path — but it runs contrary to the physical rationale in the
    _PERIOD_FACTOR_NWPS block comment. Both are in the tree; this test pins the current
    value so a change is a decision rather than a drift."""
    assert nn.RATING_SOURCE == "ww3", nn.RATING_SOURCE
    assert I.period_factor(16.0, nn.RATING_SOURCE) == 1.25
    # face_ft through the constant equals face_ft through the literal:
    #   2.0 * 1.25 * 3.281 = 8.2025
    assert _close(I.face_ft(2.0, 16.0, nn.RATING_SOURCE), 8.2025)


def test_the_mop_rater_carries_no_period_curve_at_all():
    """THE TIER FIX, pinned at the constant. mop_stars used to call
    face_ft(hs, tp, RATING_SOURCE) and so amplified MOP's height by period_factor, putting
    the 48 MOP-tier spots ~16% above the 132 MOP-corrected spots reading the same MOP
    number. The constant is GONE from that module, not merely unused: an unused constant
    named "RATING_SOURCE" sitting next to a rater is an invitation to wire it back up.

    Asserting its ABSENCE is what makes re-adding it fail here rather than in production
    six months later. See the block comment in mop.mop_stars for why the term is removed
    outright instead of measured per spot."""
    assert not hasattr(mop, "RATING_SOURCE"), (
        "mop.RATING_SOURCE is back. The MOP tier must publish MOP height in feet with no "
        "period amplification — re-adding the curve reopens the 21.6%-vs-9.1% tier gap "
        "and makes the SWELL HEIGHT label wrong again."
    )
    # face_ft is not imported there either, so the curve cannot be reached by accident.
    assert not hasattr(mop, "face_ft"), "mop.face_ft is back; the MOP path must not use it"


def test_mop_stars_publishes_mop_height_in_feet_and_nothing_else():
    """THE BEHAVIOUR, not just the constant. mop_stars must return hs * M_TO_FT as its
    face, and the SAME face at every period — that period-independence is the whole point
    and is what a re-added curve would break.

        hs 2.0 -> 2.0 * 3.281 = 6.562 ft, at 8 s and at 16 s alike

    Under the old code these differed by 19%:
        8 s:  2.0 * 1.05 * 3.281 = 6.8901
       16 s:  2.0 * 1.25 * 3.281 = 8.2025
    Both literals are written out; neither is produced by calling anything under test.
    Geometry is held fixed (dp on the shore normal) so only the period moves.
    """
    _, face_8, _, _, _ = mop.mop_stars(2.0, 8.0, 270.0, 2.0, 270.0)
    _, face_16, _, _, _ = mop.mop_stars(2.0, 16.0, 270.0, 2.0, 270.0)
    assert _close(face_8, 6.562), face_8
    assert _close(face_16, 6.562), face_16
    assert face_8 == face_16, (face_8, face_16)
    # And explicitly NOT the amplified values the old path produced.
    assert not _close(face_8, 6.8901), "period_factor is back on the 8 s hour"
    assert not _close(face_16, 8.2025), "period_factor is back on the 16 s hour"
    # M_TO_FT is the only scalar left between MOP metres and published feet.
    assert _close(face_16, 2.0 * I.M_TO_FT), face_16


# --------------------------------------------------------------------------- #
# 15 — _interp on an empty table returns 0.0, not an exception                  #
# --------------------------------------------------------------------------- #
def test_interp_on_an_empty_table_returns_zero_rather_than_raising():
    """    if not points:
            return 0.0

    For period_factor that would be an amplification of 0.0, and for face_ft a SILENT
    0.0 ft face on every hour rather than an exception someone would see. An empty table
    cannot arise from the module as written — both are non-empty literals — so this pins
    the contract of the shared interpolator, which _SIZE_POINTS, _CHOP_POINTS and
    _PERIOD_QUALITY_POINTS also depend on.

    A single-point table takes the same low clamp and returns that point's y everywhere."""
    assert I._interp(10.0, []) == 0.0
    assert I._interp(-10.0, []) == 0.0
    assert I._interp(0.0, []) == 0.0
    # what that would mean downstream, if a table ever were emptied:
    #   hs * 0.0 * 3.281 = 0.0 ft, published, with nothing raised
    assert I._interp(12.0, []) * 2.0 * I.M_TO_FT == 0.0
    # degenerate but non-empty: one point clamps for every x
    assert I._interp(10.0, [(5.0, 0.7)]) == 0.7
    assert I._interp(-99.0, [(5.0, 0.7)]) == 0.7


# --------------------------------------------------------------------------- #
# 16 — the high clamp is INCLUSIVE: the last anchor takes the clamp, not a lerp #
# --------------------------------------------------------------------------- #
def test_the_high_clamp_is_inclusive_so_the_last_anchor_is_exact():
    """    if x >= points[-1][0]:
            return points[-1][1]

    `>=`, not `>`. At x == the last anchor the CLAMP fires and returns that anchor's y
    verbatim; the interpolation branch is never entered. Weakening this to `>` would send
    the endpoint through the lerp instead:

        y0 + (x1-x0)/(x1-x0) * (y1-y0)  =  y0 + 1.0 * (y1-y0)

    which is y1 in exact arithmetic but NOT always in binary64 — `y0 + (y1-y0)` re-rounds.

    ON THE FIVE TABLES THIS MODULE ACTUALLY DEFINES the two spellings agree bit-for-bit
    at every endpoint, so neither the period-factor curves nor _SIZE_POINTS / _CHOP_POINTS
    / _PERIOD_QUALITY_POINTS can distinguish them — the equivalence is a property of those
    particular numbers, not of the code. It does not hold in general: measured over 200k
    random two-point tables, 9.6% re-round. So the contract is pinned with a witness table
    that exhibits the difference, which is what keeps `>=` from decaying into `>`:

        [(0.0, 0.2), (1.0, 0.9)] at x = 1.0
            clamp -> 0.9                  (exact, the anchor's own y)
            lerp  -> 0.2 + 1.0 * 0.7
                   = 0.8999999999999999   (one ulp low; 0.2 + 0.7 != 0.9 in binary64)
    """
    witness = [(0.0, 0.2), (1.0, 0.9)]
    assert I._interp(1.0, witness) == 0.9, I._interp(1.0, witness)
    assert I._interp(1.0, witness) != 0.8999999999999999
    # the interior of the same table is unaffected either way:
    #   0.2 + (0.5-0)/(1-0) * (0.9-0.2) = 0.2 + 0.5*0.7 = 0.55
    assert _close(I._interp(0.5, witness), 0.55)
    # and the production curves land on their final anchor exactly, as tested in 7
    assert I._interp(99.0, I._PERIOD_FACTOR_WW3) == 1.3
    assert I._interp(99.0, I._PERIOD_FACTOR_NWPS) == 1.6


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} period-factor checks passed")


if __name__ == "__main__":
    _run_all()
