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


def test_the_rule_takes_over_exactly_when_bigger_and_longer_and_never_lowers_the_period():
    base = {"swell_source": "nwps_height_ww3_dir", "swell_tp": 6.0, "swell_dp": 90.0,
            "swell_1_hs": 1.0, "swell_1_tp": 6.0, "swell_1_dp": 90.0,
            "wind_wave_hs": 1.0, "wind_wave_tp": 6.0, "wind_wave_dp": 60.0}
    assert P.rated_partition(base) == (1.0, 6.0, 90.0)
    n = 0
    for wh in (0.5, 0.99, 1.0, 1.01, 2.0, 5.0):
        for wt in (3.0, 5.99, 6.0, 6.01, 9.0, 16.0):
            row = dict(base, wind_wave_hs=wh, wind_wave_tp=wt)
            want = wh > 1.0 and wt > 6.0
            tp, dp, which = P.shown_train(row)
            assert P.wind_sea_dominates(row) is want and (which == "wind_sea") is want, (wh, wt)
            assert tp >= 6.0 and dp == (60.0 if want else 90.0), (wh, wt, tp, dp)
            n += 1
    assert n == 36


def test_the_sources_are_the_ww3_identity_ones():
    assert P.WW3_IDENTITY_SOURCES == {"ww3", "nwps_height_ww3_dir"}
