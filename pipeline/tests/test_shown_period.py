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
    wrong = [(c["name"], P.shown_train(c["row"], c.get("arcs"), c.get("facing")), (c["tp"], c["dp"], c["which"]))
             for c in cases if P.shown_train(c["row"], c.get("arcs"), c.get("facing")) != (c["tp"], c["dp"], c["which"])]
    assert not wrong, wrong
    assert {c["which"] for c in cases} == {"swell", "wind_sea"}
    # the window's edge is in the table, both ways, and the spots with no arcs
    windowed = [c for c in cases if c.get("arcs")]
    assert {c["which"] for c in windowed} == {"swell", "wind_sea"}
    assert any("arcs" in c and not c["arcs"] for c in cases)
    # run 76's facing cases, both ways, and its boundary
    faced = [c for c in cases if c.get("facing") is not None]
    assert {c["which"] for c in faced} == {"swell", "wind_sea"}
    assert any(c["name"].startswith("St. Pete Beach, 2026-10-28") for c in faced)
    assert any(c["name"].startswith("Exactly 90 deg off") for c in faced)


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
            tp, dp, which = P.shown_train(row, None, None)
            assert P.switch_case(row, None, None) == want, (wh, wt)
            assert P.wind_sea_dominates(row, None, None) is (want is not None) and (which == "wind_sea") is (want is not None)
            assert dp == (60.0 if want else 90.0), (wh, wt)
            assert tp >= 6.0 or want == "dominant", (wh, wt, tp)
            n += 1
    assert n == 56


def test_k_is_a_parameter_for_the_preview():
    row = {"swell_source": "ww3", "swell_tp": 14.0, "swell_dp": 100.0,
           "swell_1_hs": 0.5, "swell_1_tp": 14.0, "swell_1_dp": 100.0,
           "wind_wave_hs": 1.6, "wind_wave_tp": 8.0, "wind_wave_dp": 60.0}
    # 1.6 / 0.5 = 3.2: dominant at K 3, not at K 4 or 6
    assert [P.switch_case(row, None, None, k) for k in (3, 4, 6)] == ["dominant", None, None]
    assert P.shown_train(row, None, None, 3) == (8.0, 60.0, "wind_sea") and P.shown_train(row, None, None)[2] == "swell"


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
                assert P.switch_case(r, ONE, None, k) == (None if d in BLOCKED else case), (case, k, d)
                assert P.switch_case(r, None, None, k) == case and P.switch_case(r, [], None, k) == case
                assert P.shown_train(r, ONE, None, k)[2] == ("swell" if d in BLOCKED else "wind_sea")


def test_the_constants():
    assert (P.MIN_LONGER_S, P.DOMINANCE_K, P.BLOCKED_OUTSIDE_DEG) == (0.5, 4.0, 90.0)
    assert P.FACING_OFF_DEG == 90.0


# --------------------------------------------------------------------------- #
# Run 76: a wind sea travelling away from the beach                            #
# --------------------------------------------------------------------------- #
# St. Pete Beach, 2026-10-28 14Z, as run 57's archive recorded it: the rated 0.06 m, 3.86 s swell
# partition from 329.68 beside a 1.05 m, 4.69 s wind sea from 15.98. The spot faces 240; its
# window is 173-315 with span 146 (pad 2: sector 171-317).
STP_ARCS = [{"min": 173, "max": 315, "span": 146}]
STP = {"swell_source": "nwps_height_ww3_dir", "swell_tp": 3.86, "swell_dp": 329.68,
       "swell_1_hs": 0.06, "swell_1_tp": 3.86, "swell_1_dp": 329.68,
       "wind_wave_hs": 1.05, "wind_wave_tp": 4.69, "wind_wave_dp": 15.98}


def test_st_pete_beach_28_oct_no_longer_switches():
    # 15.98 is past the sector's 317 edge by 360 - 317 + 15.98 = 58.98 deg (< 90, so the window
    # rung lets it through), and |15.98 - 240| = 224.02, the short way 135.98 deg off the facing.
    assert P.wind_sea_blocked(15.98, STP_ARCS) is False
    assert round(P.off_facing_deg(15.98, 240.0), 2) == 135.98
    assert P.switch_case(STP, STP_ARCS, None) == "longer"        # the rule before run 76
    assert P.switch_case(STP, STP_ARCS, 240.0) is None
    assert P.shown_train(STP, STP_ARCS, 240.0) == (3.86, 329.68, "swell")


def test_a_wind_sea_within_90_of_the_facing_still_switches():
    # from 300: 60 off the facing, inside the window; from 200: 40 off, inside; LONGER and
    # (at 4x: 1.05 >= 4 x 0.06 = 0.24) DOMINANT alike
    for d in (300.0, 200.0, 240.0):
        r = dict(STP, wind_wave_dp=d)
        assert P.switch_case(r, STP_ARCS, 240.0) == "longer", d
        assert P.shown_train(r, STP_ARCS, 240.0) == (4.69, d, "wind_sea"), d
    dom = dict(STP, wind_wave_tp=3.0, wind_wave_dp=300.0)       # shorter: only DOMINANT can hold
    assert P.switch_case(dom, STP_ARCS, 240.0) == "dominant"


def test_exactly_90_off_the_facing_is_away_as_279_puts_its_boundary_on_the_adverse_side():
    # #279: exactly 120 deg off reads onshore (the adverse class takes the boundary). Here exactly
    # 90 deg off reads away: no switch. 89.9 off switches. Both sides of the facing, no window.
    for d, want in ((150.0, None), (150.1, "longer"), (330.0, None), (329.9, "longer"),
                    (149.9, None), (330.1, None)):
        assert P.switch_case(dict(STP, wind_wave_dp=d), None, 240.0) == want, d
    assert P.wind_sea_away(150.0, 240.0) is True and P.wind_sea_away(150.1, 240.0) is False


def test_no_facing_leaves_the_window_to_decide():
    assert P.off_facing_deg(15.98, None) is None
    assert P.wind_sea_away(15.98, None) is False
    assert P.switch_case(STP, STP_ARCS, None) == "longer"
    assert P.switch_case(STP, None, None) == "longer"


def test_the_short_way_round():
    # 350 at a beach facing 10: |350 - 10| = 340, the short way 20
    assert P.off_facing_deg(350.0, 10.0) == 20.0
    assert P.off_facing_deg(190.0, 10.0) == 180.0
    assert P.switch_case(dict(STP, wind_wave_dp=350.0), None, 10.0) == "longer"


def test_every_spot_has_a_facing():
    with open(os.path.join(ROOT, "pipeline", "spots_enriched.json")) as f:
        spots = json.load(f)
    assert len(spots) == 648
    assert [s["name"] for s in spots if s.get("orientation_deg") is None] == []


def test_the_sources_are_the_ww3_identity_ones():
    assert P.WW3_IDENTITY_SOURCES == {"ww3", "nwps_height_ww3_dir"}
