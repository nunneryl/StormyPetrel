"""shown_period.shown_train, the swell train the site shows, held to the hand-written cases the
frontend is held to (frontend/lib/shownPeriod.cases.json), so the period and direction on the page,
in the daily report and in the surf height's shoaling cannot disagree.

NO EXPECTED VALUE COMES FROM THE CODE UNDER TEST: every one is a literal here or in the cases file.

Run: python -m pytest pipeline/tests/test_shown_period.py
"""
from __future__ import annotations

import json
import os

from pipeline.forecast import shown_period as P

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CASES = os.path.join(ROOT, "frontend", "lib", "shownPeriod.cases.json")


def test_every_shared_case():
    with open(CASES) as f:
        cases = json.load(f)["cases"]
    assert len(cases) >= 32, len(cases)
    wrong = [(c["name"], P.shown_train(c["row"], c.get("arcs")), (c["tp"], c["dp"], c["which"]))
             for c in cases if P.shown_train(c["row"], c.get("arcs")) != (c["tp"], c["dp"], c["which"])]
    assert not wrong, wrong
    assert {c["which"] for c in cases} == {"swell", "wind_sea"}
    # the window's edge is in the table, both ways, and the spots with no arcs
    windowed = [c for c in cases if c.get("arcs")]
    assert {c["which"] for c in windowed} == {"swell", "wind_sea"}
    assert any("arcs" in c and not c["arcs"] for c in cases)


def test_the_rule_takes_over_exactly_on_its_two_cases():
    """Around a rated 1.0 m / 6 s partition: LONGER when the wind sea is above 1.0 m and at least
    6.5 s; DOMINANT when it is 4.0 m or more, whatever its period. Only DOMINANT can lower the
    shown period."""
    base = {"swell_source": "nwps_height_ww3_dir", "swell_tp": 6.0, "swell_dp": 90.0,
            "swell_1_hs": 1.0, "swell_1_tp": 6.0, "swell_1_dp": 90.0,
            "wind_wave_hs": 1.0, "wind_wave_tp": 6.0, "wind_wave_dp": 60.0}
    assert P.rated_partition(base) == (1.0, 6.0, 90.0)
    n = 0
    for wh in (0.5, 0.99, 1.0, 1.01, 2.0, 3.99, 4.0, 5.0):
        for wt in (3.0, 5.99, 6.0, 6.49, 6.5, 9.0, 16.0):
            row = dict(base, wind_wave_hs=wh, wind_wave_tp=wt)
            longer = wh > 1.0 and wt >= 6.5
            dominant = wh >= 4.0
            want = "longer" if longer else ("dominant" if dominant else None)
            tp, dp, which = P.shown_train(row, None)
            assert P.switch_case(row, None) == want, (wh, wt)
            assert P.wind_sea_dominates(row, None) is (want is not None) and (which == "wind_sea") is (want is not None)
            assert dp == (60.0 if want else 90.0), (wh, wt)
            assert tp >= 6.0 or want == "dominant", (wh, wt, tp)
            n += 1
    assert n == 56


def test_k_is_a_parameter_for_the_preview():
    row = {"swell_source": "ww3", "swell_tp": 14.0, "swell_dp": 100.0,
           "swell_1_hs": 0.5, "swell_1_tp": 14.0, "swell_1_dp": 100.0,
           "wind_wave_hs": 1.6, "wind_wave_tp": 8.0, "wind_wave_dp": 60.0}
    # 1.6 / 0.5 = 3.2: dominant at K 3, not at K 4 or 6
    assert [P.switch_case(row, None, k) for k in (3, 4, 6)] == ["dominant", None, None]
    assert P.shown_train(row, None, 3) == (8.0, 60.0, "wind_sea") and P.shown_train(row, None)[2] == "swell"


# The window 30-150 with span 124: pad 2, sector 28 to 152. A bearing is 90 or more outside it
# from 152 + 90 = 242 round to 28 - 90 = -62, i.e. 298: blocked exactly on 242..298.
ONE = [{"min": 30, "max": 150, "span": 124}]
BLOCKED = set(range(242, 299))


def test_blocked_is_the_ratings_rung_on_every_bearing():
    """wind_sea_blocked is the rating's blocked rung: on every whole bearing it holds exactly
    where directional_gain gives 0, and that is the hand-worked 242..298."""
    from pipeline.interpret import directional_gain
    got = {d for d in range(360) if P.wind_sea_blocked(float(d), ONE)}
    rung = {d for d in range(360) if directional_gain(float(d), ONE, None, 90.0) == 0.0}
    assert got == BLOCKED == rung
    assert not any(P.wind_sea_blocked(float(d), arcs) for d in range(360) for arcs in (None, []))


def test_the_exclusion_applies_to_both_cases_and_k():
    """Across every bearing, a LONGER and a DOMINANT wind sea switch except on 242..298, at any
    K; with no arcs they switch on every bearing."""
    longer = {"swell_source": "nwps_height_ww3_dir", "swell_tp": 3.0, "swell_dp": 80.0,
              "swell_1_hs": 0.24, "swell_1_tp": 3.0, "swell_1_dp": 80.0,
              "wind_wave_hs": 2.4, "wind_wave_tp": 9.0}
    dominant = dict(longer, swell_tp=14.0, swell_1_tp=14.0, swell_1_hs=0.5, wind_wave_hs=2.5,
                    wind_wave_tp=8.0)
    for row, case in ((longer, "longer"), (dominant, "dominant")):
        for k in (3.0, 4.0, 6.0) if case == "longer" else (3.0, 4.0):
            for d in range(360):
                r = dict(row, wind_wave_dp=float(d))
                assert P.switch_case(r, ONE, k) == (None if d in BLOCKED else case), (case, k, d)
                assert P.switch_case(r, None, k) == case and P.switch_case(r, [], k) == case
                assert P.shown_train(r, ONE, k)[2] == ("swell" if d in BLOCKED else "wind_sea")


def test_the_constants():
    assert (P.MIN_LONGER_S, P.DOMINANCE_K, P.BLOCKED_OUTSIDE_DEG) == (0.5, 4.0, 90.0)


def test_the_sources_are_the_ww3_identity_ones():
    assert P.WW3_IDENTITY_SOURCES == {"ww3", "nwps_height_ww3_dir"}
