"""The 20 kt changes to the strong-wind cap: wired into interpret.wind_star_cap after
rating-preview run 34, previewed by star_preview --wind, and the merge gate between the two.

  no-allowance-20  above 20 kt MEASURED the period allowance stops (cross-shore and onshore)
  offshore-20      offshore above 20 kt measured is capped at 3.0; above 25 kt stays 2.0
  wind-both        the two together

Every expected value is worked by hand, never read back from the functions under test:

  knots       m/s / 0.514444:  11.623 m/s = 22.594 kt (26 mph x 0.44704: Jennettes)
                               11.176 m/s = 21.725 kt (25 mph: Ocean City NJ)
                               11.0 m/s = 21.382 kt   10.4 m/s = 20.216 kt
                               10.2 m/s = 19.827 kt   8.0 m/s = 15.551 kt   13.4 m/s = 26.048 kt
  allowance   max(0, period - 8) knots off the speed, on the ladder only
  ladder      onshore >13: 3.0, >15: 2.0, >20: 1.5;  cross >13: 3.0, >15: 2.5, >20: 1.5
  above 25    1.0 cross-shore and onshore, 2.0 offshore, any period, under every rule
  classes     Jennettes' offshore bearing 245: wind from 0 is 115 off (cross), from 65 is 180
              off (onshore), from 245 is 0 off (offshore)
"""
from __future__ import annotations

import pytest

from pipeline import interpret as I
from pipeline import star_preview as SP

RULES = ("no-allowance-20", "offshore-20", "wind-both")

# --------------------------------------------------------------------------- #
# proposed_wind_cap                                                            #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("wind_dir, speed, period, live, expected", [
    # Jennettes, 2026-10-21: 22.594 - 4 = 18.594 > 15: cross 2.5 live. Without the allowance
    # 22.594 > 20: 1.5. Offshore-20 does not touch a cross-shore wind.
    (0.0, 11.623, 12.0, 2.5, {"no-allowance-20": 1.5, "offshore-20": 2.5, "wind-both": 1.5}),
    # Just over 20 kt measured: 20.216 - 4 = 16.216 -> 2.5 live; 20.216 > 20 -> 1.5.
    (0.0, 10.4, 12.0, 2.5, {"no-allowance-20": 1.5, "offshore-20": 2.5, "wind-both": 1.5}),
    # Just under 20 kt measured: the allowance stays. 19.827 - 4 = 15.827 > 15 -> 2.5 under all.
    (0.0, 10.2, 12.0, 2.5, {"no-allowance-20": 2.5, "offshore-20": 2.5, "wind-both": 2.5}),
    # Onshore, 16 s: 20.216 - 8 = 12.216, no cap live; without the allowance > 20: 1.5.
    (65.0, 10.4, 16.0, 5.0, {"no-allowance-20": 1.5, "offshore-20": 5.0, "wind-both": 1.5}),
    # Onshore below 20 kt keeps its allowance: 15.551 - 3 = 12.551, no cap under all.
    (65.0, 8.0, 11.0, 5.0, {"no-allowance-20": 5.0, "offshore-20": 5.0, "wind-both": 5.0}),
    # Ocean City NJ shape: offshore 21.725 kt, uncapped live; offshore-20 caps at 3.0.
    (245.0, 11.176, 10.0, 5.0, {"no-allowance-20": 5.0, "offshore-20": 3.0, "wind-both": 3.0}),
    # Offshore just over 20 kt: 20.216 -> 3.0; just under, 19.827 -> no cap.
    (245.0, 10.4, 10.0, 5.0, {"no-allowance-20": 5.0, "offshore-20": 3.0, "wind-both": 3.0}),
    (245.0, 10.2, 10.0, 5.0, {"no-allowance-20": 5.0, "offshore-20": 5.0, "wind-both": 5.0}),
    # Above 25 kt measured nothing changes: offshore 2.0, cross-shore 1.0, onshore 1.0.
    (245.0, 13.4, 16.0, 2.0, {"no-allowance-20": 2.0, "offshore-20": 2.0, "wind-both": 2.0}),
    (0.0, 13.4, 16.0, 1.0, {"no-allowance-20": 1.0, "offshore-20": 1.0, "wind-both": 1.0}),
    (65.0, 13.4, 8.0, 1.0, {"no-allowance-20": 1.0, "offshore-20": 1.0, "wind-both": 1.0}),
])
def test_proposed_wind_cap(wind_dir, speed, period, live, expected):
    # *live* is the cap the rows published before the change carry; the wired rater is now
    # wind-both.
    assert SP.previous_wind_cap(wind_dir, speed, 245.0, period) == live
    assert {r: SP.proposed_wind_cap(wind_dir, speed, 245.0, period, r) for r in RULES} == expected
    assert I.wind_star_cap(wind_dir, speed, 245.0, period) == expected["wind-both"]


@pytest.mark.parametrize("args", [(None, 11.0, 245.0, 12.0), (0.0, None, 245.0, 12.0),
                                  (0.0, 11.0, None, 12.0)])
def test_missing_wind_or_bearing_is_still_no_cap(args):
    assert {SP.proposed_wind_cap(*args, r) for r in RULES} == {5.0}


def test_no_rule_ever_raises_the_previous_cap_and_each_moves_only_its_own_case():
    """A property over a grid, not an expected value: every direction in 5° steps, 0-30 kt
    in quarter knots, periods 6-18 s. The wired cap and the preview's wind-both, built
    independently, agree everywhere on it."""
    for d in range(0, 360, 5):
        for q in range(0, 121):
            ms = q / 4 * 0.514444
            for tp in (6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0):
                live = SP.previous_wind_cap(float(d), ms, 245.0, tp)
                assert I.wind_star_cap(float(d), ms, 245.0, tp) == \
                    SP.proposed_wind_cap(float(d), ms, 245.0, tp, "wind-both"), (d, q, tp)
                cls, kt = SP.wind_class(float(d), 245.0), ms / 0.514444
                for r in RULES:
                    assert SP.proposed_wind_cap(float(d), ms, 245.0, tp, r) <= live, (d, q, tp, r)
                if cls == "offshore" or kt <= 20.0:
                    assert SP.proposed_wind_cap(float(d), ms, 245.0, tp, "no-allowance-20") == live
                if cls != "offshore" or kt <= 20.0:
                    assert SP.proposed_wind_cap(float(d), ms, 245.0, tp, "offshore-20") == live


# --------------------------------------------------------------------------- #
# Stars against the live rater                                                 #
# --------------------------------------------------------------------------- #
# The NWPS row of test_rating_proposal: face 8.0 ft at 12 s, gain 1, wind_mult 1.0:
# size(8) 4.5 x 0.95**0.25 (0.987259) = 4.443 -> 4.5 uncapped. WEST_BEACH's offshore bearing
# is 90, so wind from 0 is 90 off (cross) and from 90 is offshore.

WEST_BEACH = {"swell_window_arcs": [{"min": 250, "max": 290, "span": 40}],
              "optimal_swell_dir": 270, "orientation_deg": 270.0, "offshore_wind_deg": 90.0,
              "break_type": "beach", "bottom": "sand"}
SPOTS = {"A": dict(WEST_BEACH, name="A"), "B": dict(WEST_BEACH, name="B")}
FACTORS = {"a": {"factor": 3.0}}      # A is calibrated, B is not; M = 1.6 in every call
LIGHT = {"wind_dir": 90.0, "wind_speed": 2.0}
CROSS_21 = {"wind_dir": 0.0, "wind_speed": 11.0}       # 21.382 kt cross-shore
OFFSHORE_21 = {"wind_dir": 90.0, "wind_speed": 11.176}  # 21.725 kt offshore
T0, T1 = "2026-10-20T15:00:00+00:00", "2026-10-20T16:00:00+00:00"


def _row(**kw):
    row = {"swell_source": "nwps_height_ww3_dir", "face_ft": 8.0, "face_ft_raw": None,
           "swell_tp": 12.0, "dp": 300.0, "tp": 12.0, "swell_1_hs": 2.0, "swell_1_tp": 12.0,
           "swell_1_dp": 270.0, "wind_mult": 1.0, "tide_mult": 1.0, "chop_mult": 1.0,
           "stars": 4.5, "spot_id": 1, "valid_time": T0}
    row.update(kw)
    return row


def _stars(spot, rule, **kw):
    row = _row(**kw)
    rb = SP.rescore(row, SPOTS[spot])
    return SP.wind_stars(row, SPOTS[spot], rb, FACTORS, rule, 1.6)


def test_a_cross_shore_gale_loses_its_allowance():
    # B, 21.382 kt at 12 s: live 17.382 > 15 -> 2.5 (published 2.5, reproduced by the live rater,
    # which at an uncalibrated spot is the one before #267 too). No allowance: > 20 -> 1.5.
    assert _stars("B", "no-allowance-20", stars=2.5, **CROSS_21) == (2.5, 1.5, "cal-median")
    assert _stars("B", "offshore-20", stars=2.5, **CROSS_21) == (2.5, 2.5, "cal-median")
    assert _stars("B", "wind-both", stars=2.5, **CROSS_21) == (2.5, 1.5, "cal-median")


def test_a_strong_offshore_wind_is_capped_at_three():
    # B, 21.725 kt offshore: uncapped 4.5 live; 3.0 under offshore-20.
    assert _stars("B", "offshore-20", **OFFSHORE_21) == (4.5, 3.0, "cal-median")
    assert _stars("B", "no-allowance-20", **OFFSHORE_21) == (4.5, 4.5, "cal-median")


def test_the_baseline_is_whichever_rater_reproduces_the_row():
    # A (factor 3.0, M 1.6) sized at 8 x 1.6 / 3 = 4.267 ft: size 3.0 + 0.267 x 0.5 = 3.1333,
    # x 0.987259 = 3.093 -> 3.0 live (cal-median). The rater before #267 gives 4.5.
    assert _stars("A", "offshore-20", stars=3.0, **LIGHT) == (3.0, 3.0, "cal-median")
    assert _stars("A", "offshore-20", stars=4.5, **LIGHT) == (4.5, 4.5, "before #267")
    assert _stars("A", "offshore-20", stars=2.0, **LIGHT) is None
    # The change is applied on the rater that reproduced: published 4.5 before #267, offshore
    # 21.725 kt -> min(4.5, 3.0) = 3.0; published 3.0 by cal-median -> min(3.0, 3.0) = 3.0.
    assert _stars("A", "offshore-20", stars=4.5, **OFFSHORE_21) == (4.5, 3.0, "before #267")
    assert _stars("A", "offshore-20", stars=3.0, **OFFSHORE_21) == (3.0, 3.0, "cal-median")


NAMES = {1: ("A", "California"), 2: ("B", "North Carolina")}


def _rows():
    return [
        _row(spot_id=2, stars=2.5, valid_time=T0, **CROSS_21),   # B 2.5 -> 1.5
        _row(spot_id=2, stars=4.5, valid_time=T1, **LIGHT),      # B 4.5, unchanged
        _row(spot_id=1, stars=2.0, valid_time=T0, **LIGHT),      # A: no rater reproduces 2.0
        _row(spot_id=1, stars=3.0, valid_time=T1, **LIGHT),      # A 3.0 (cal-median), unchanged
    ]


def test_wind_compare_counts_by_state_and_wind():
    rep = SP.wind_compare(_rows(), SPOTS, NAMES, FACTORS, "no-allowance-20", 1.6, n=1,
                          board_from=T1, focus=["B"])
    assert rep["replay"] == {"NWPS NOT reproduced": 1, "NWPS reproduced": 3}
    assert rep["reproduced_by"] == {"cal-median": 3}
    assert (rep["compared"], rep["changed_spot_hours"], rep["ups"], rep["downs"]) == (3, 1, 0, 1)
    assert rep["delta_by_state"] == {"California": {0.0: 1}, "North Carolina": {-1.0: 1, 0.0: 1}}
    assert rep["changed_by_wind"] == {"cross <=25 kt": 1}
    f = rep["focus"]["B"]
    assert f["compared"] == 2 and len(f["changed"]) == 1
    c = f["changed"][0]
    assert (c["valid_time"], c["wind"], c["wind_kt"], c["tp"], c["stars_before"],
            c["stars_after"]) == (T0, "cross", 21.4, 12.0, 2.5, 1.5)


def test_the_homepage_list_reads_the_first_hour_at_or_after_board_from():
    # From T1: B 4.5 and A 3.0, neither moved.
    lb = SP.wind_compare(_rows(), SPOTS, NAMES, FACTORS, "no-allowance-20", 1.6, n=1,
                         board_from=T1)["leaderboard"]
    assert lb["before"] == [{"spot": "B", "state": "North Carolina", "stars": 4.5}]
    assert lb["after"] == lb["before"] and lb["drop_out"] == [] and lb["come_in"] == []
    # From the start of the window: B's 2.5 -> 1.5 drops it below A's unreproduced 2.0.
    lb = SP.wind_compare(_rows(), SPOTS, NAMES, FACTORS, "no-allowance-20", 1.6, n=1)["leaderboard"]
    assert lb["before"] == [{"spot": "B", "state": "North Carolina", "stars": 2.5}]
    assert lb["after"] == [{"spot": "A", "state": "California", "stars": 2.0}]
    assert lb["drop_out"] == [{"spot": "B", "stars_before": 2.5, "stars_after": 1.5}]
    assert lb["come_in"] == [{"spot": "A", "stars": 2.0}]


def test_wind_explain():
    row = _row(spot_id=2, stars=2.5, **CROSS_21)
    x = SP.wind_explain(row, SPOTS["B"], FACTORS, 1.6)
    assert (x["rater"], x["stars"], x["wind"], x["wind_kt"], x["period_s"], x["allowance_kt"],
            x["published_cap"], x["wired_cap"]) == ("cal-median", 2.5, "cross", 21.38, 12.0,
                                                    4.0, 2.5, 1.5)
    assert x["rules"] == {"no-allowance-20": {"cap": 1.5, "stars": 1.5},
                          "offshore-20": {"cap": 2.5, "stars": 2.5},
                          "wind-both": {"cap": 1.5, "stars": 1.5}}
    assert SP.wind_explain(_row(spot_id=2, stars=2.0, **LIGHT), SPOTS["B"], FACTORS, 1.6) is None


def test_at_or_after():
    assert SP._at_or_after(T1, T1) and SP._at_or_after(T1, T0) and not SP._at_or_after(T0, T1)
    assert SP._at_or_after("2026-10-20T16:00:00Z", T1)
    assert SP._at_or_after(T0, None)


def test_the_cli_refuses_an_unknown_wind_rule():
    with pytest.raises(SystemExit):
        SP.main(["--wind", "softer"])


def test_wind_runs_on_its_own():
    with pytest.raises(SystemExit):
        SP.main(["--wind", "wind-both", "--tide", "neutral"])


def test_the_rater_now_applies_both_changes():
    """Jennettes' 22.594 kt cross-shore at 12 s: 1.5 wired, 2.5 as published before. Ocean
    City NJ's 21.725 kt offshore: 3.0 wired, no cap before."""
    assert (I.wind_star_cap(0.0, 11.623, 245.0, 12.0),
            SP.previous_wind_cap(0.0, 11.623, 245.0, 12.0)) == (1.5, 2.5)
    assert (I.wind_star_cap(245.0, 11.176, 245.0, 10.0),
            SP.previous_wind_cap(245.0, 11.176, 245.0, 10.0)) == (3.0, 5.0)


# --------------------------------------------------------------------------- #
# The merge gate                                                               #
# --------------------------------------------------------------------------- #

def test_the_wind_gate_finds_no_difference_and_skips_rows_published_before_267():
    # _rows(): B 2.5 -> 1.5 (wired: nwps_stars with the wind, 21.382 kt cross > 20 measured,
    # 1.5), B 4.5 and A 3.0 unchanged, A 2.0 reproduced by neither rater (not counted); plus A
    # published 4.5, reproduced only by the rater before #267: skipped.
    rows = _rows() + [_row(spot_id=1, stars=4.5, valid_time="2026-10-20T17:00:00+00:00", **LIGHT)]
    c = SP.wind_rater_check(rows, SPOTS, NAMES, FACTORS, 1.6)
    assert (c["hours"], c["same"], c["different"], c["skipped_before_267"], c["examples"]) == (
        3, 3, 0, 1, [])
    assert c["wired_delta_by_group"] == {"NWPS": {-1.0: 1, 0.0: 2}}


def test_the_wind_gate_reports_a_difference():
    real = SP.wired_rater
    SP.wired_rater = lambda *a, **k: 2.5
    try:
        c = SP.wind_rater_check(_rows()[:1], SPOTS, NAMES, FACTORS, 1.6)
    finally:
        SP.wired_rater = real
    assert (c["hours"], c["same"], c["different"]) == (1, 0, 1)
    assert c["examples"] == [{"spot": "B", "state": "North Carolina", "valid_time": T0,
                              "published": 2.5, "preview": 1.5, "wired": 2.5}]
