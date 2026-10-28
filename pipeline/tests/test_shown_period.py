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
    assert len(cases) >= 12, len(cases)
    wrong = [(c["name"], P.shown_train(c["row"]), (c["tp"], c["dp"], c["which"])) for c in cases
             if P.shown_train(c["row"]) != (c["tp"], c["dp"], c["which"])]
    assert not wrong, wrong
    assert {c["which"] for c in cases} == {"swell", "wind_sea"}


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
            tp, dp, which = P.shown_train(row)
            assert P.switch_case(row) == want, (wh, wt)
            assert P.wind_sea_dominates(row) is (want is not None) and (which == "wind_sea") is (want is not None)
            assert dp == (60.0 if want else 90.0), (wh, wt)
            assert tp >= 6.0 or want == "dominant", (wh, wt, tp)
            n += 1
    assert n == 56


def test_k_is_a_parameter_for_the_preview():
    row = {"swell_source": "ww3", "swell_tp": 14.0, "swell_dp": 100.0,
           "swell_1_hs": 0.5, "swell_1_tp": 14.0, "swell_1_dp": 100.0,
           "wind_wave_hs": 1.6, "wind_wave_tp": 8.0, "wind_wave_dp": 60.0}
    # 1.6 / 0.5 = 3.2: dominant at K 3, not at K 4 or 6
    assert [P.switch_case(row, k) for k in (3, 4, 6)] == ["dominant", None, None]
    assert P.shown_train(row, 3) == (8.0, 60.0, "wind_sea") and P.shown_train(row)[2] == "swell"


def test_the_constants():
    assert (P.MIN_LONGER_S, P.DOMINANCE_K) == (0.5, 4.0)


def test_the_sources_are_the_ww3_identity_ones():
    assert P.WW3_IDENTITY_SOURCES == {"ww3", "nwps_height_ww3_dir"}
