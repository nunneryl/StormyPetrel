"""The onshore-rung preview (star_preview --onshore): a first onshore cap rung at the "Blown
out" label's threshold, 6 m/s = 11.663 kt, read with the period allowance as today.

  blown-2.5   from 6 m/s 2.5; over 15 kt 2.0; over 20 kt 1.5 (the over-13 3.0 rung no longer binds)
  blown-3.0   the existing first rung (3.0) moved down from over 13 kt to 6 m/s

Every expected value is worked by hand, never read back from the functions under test:

  knots       m/s / 0.514444: 6.0 = 11.663   5.99 = 11.644   6.7 = 13.024   8.0 = 15.551
              8.231 = 16.000   11.0 = 21.382   13.4 = 26.048
  allowance   max(0, period - 8) knots off the speed, up to 20 kt measured
  wired       onshore over 13: 3.0, over 15: 2.0, over 20: 1.5; over 25: 1.0
  classes     offshore bearing 90: wind from 270 is 180 off (onshore), from 0 is 90 off (cross)
  label       "Blown out": more than 120 off and 6 m/s or more (classifySurface)
"""
from __future__ import annotations

import pytest

from pipeline import interpret as I
from pipeline import star_preview as SP

RULES = ("blown-2.5", "blown-3.0")


@pytest.mark.parametrize("wind_dir, speed, period, wired, b25, b30", [
    (270.0, 6.0, 8.0, 5.0, 2.5, 3.0),     # 11.663 kt, at the label's threshold: the new rung
    (270.0, 5.99, 8.0, 5.0, 5.0, 5.0),    # 11.644 kt: under it
    (270.0, 6.7, 8.0, 3.0, 2.5, 3.0),     # 13.024 kt: wired 3.0; blown-2.5 reads 2.5
    (270.0, 8.0, 8.0, 2.0, 2.0, 2.0),     # 15.551 kt: over 15, 2.0 under all
    (270.0, 6.2586, 8.0, 5.0, 2.5, 3.0),  # 14 mph = 12.166 kt (Indialantic) at 8 s
    (270.0, 6.2586, 10.0, 5.0, 5.0, 5.0), # ...at 10 s read at 10.166 kt: the allowance keeps it
    (270.0, 8.231, 12.0, 5.0, 2.5, 3.0),  # 16.000 kt at 12 s read at 12.000: the new rung
    (270.0, 11.0, 16.0, 1.5, 1.5, 1.5),   # 21.382 kt: over 20, no allowance, 1.5
    (270.0, 13.4, 8.0, 1.0, 1.0, 1.0),    # 26.048 kt: over 25, 1.0
    (0.0, 6.7, 8.0, 3.0, 3.0, 3.0),       # cross-shore (90 off) 13.024 kt: cross ladder over 13,
                                          # 3.0, untouched by either rule
    (None, 6.7, 8.0, 5.0, 5.0, 5.0),
])
def test_onshore_rung_cap(wind_dir, speed, period, wired, b25, b30):
    assert I.wind_star_cap(wind_dir, speed, 90.0, period) == wired
    assert SP.onshore_rung_cap(wind_dir, speed, 90.0, period, "blown-2.5") == b25
    assert SP.onshore_rung_cap(wind_dir, speed, 90.0, period, "blown-3.0") == b30


@pytest.mark.parametrize("wind_dir, speed, want", [
    (270.0, 6.0, True), (270.0, 5.99, False),
    (210.0, 6.5, False),     # exactly 120 off: the label reads "mixed"
    (211.0, 6.5, True),      # 121 off
    (None, 6.5, False),
])
def test_blown_out_is_the_labels_rule(wind_dir, speed, want):
    assert SP.blown_out(wind_dir, speed, 90.0) is want


def test_a_rule_only_ever_lowers_the_wired_cap_and_only_onshore():
    """A property over a grid, not an expected value: every 5 deg, 0-30 kt in quarter knots,
    periods 6-18 s. Each rule's cap is never above the wired cap, and differs only onshore
    with the speed less the allowance at 6 m/s or more and not over 15 kt."""
    for d in range(0, 360, 5):
        for q in range(0, 121):
            ms = q / 4 * 0.514444
            for tp in (6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0):
                wired = I.wind_star_cap(float(d), ms, 90.0, tp)
                kt = ms / 0.514444
                read = kt if kt > 20.0 else kt - max(0.0, tp - 8.0)
                for r in RULES:
                    got = SP.onshore_rung_cap(float(d), ms, 90.0, tp, r)
                    assert got <= wired, (d, q, tp, r)
                    if got != wired:
                        assert I.wind_class(float(d), 90.0) == "onshore", (d, q, tp, r)
                        assert 11.663 <= read <= 15.0 + 1e-9, (d, q, tp, r, read)


def test_the_cap_goes_back_after_the_replay():
    wired = I.wind_star_cap
    with SP._cap_in_place(lambda *a: 0.0):
        assert I.wind_star_cap(270.0, 6.0, 90.0, 8.0) == 0.0
    assert I.wind_star_cap is wired
    with pytest.raises(RuntimeError):
        with SP._cap_in_place(lambda *a: 0.0):
            raise RuntimeError
    assert I.wind_star_cap is wired


# --------------------------------------------------------------------------- #
# Against the wired rater                                                      #
# --------------------------------------------------------------------------- #
# The NWPS row of test_wind_proposal: face 8.0 ft at 12 s, gain 1, wind_mult 1.0: 4.443 -> 4.5
# uncapped at B (uncalibrated). A (factor 3.0, M 1.6) is 3.0 on light wind.

WEST_BEACH = {"swell_window_arcs": [{"min": 250, "max": 290, "span": 40}],
              "optimal_swell_dir": 270, "orientation_deg": 270.0, "offshore_wind_deg": 90.0,
              "break_type": "beach", "bottom": "sand"}
SPOTS = {"A": dict(WEST_BEACH, name="A"), "B": dict(WEST_BEACH, name="B")}
FACTORS = {"a": {"factor": 3.0}}
NAMES = {1: ("A", "California"), 2: ("B", "North Carolina")}
LIGHT = {"wind_dir": 90.0, "wind_speed": 2.0}
ONSHORE_16 = {"wind_dir": 270.0, "wind_speed": 8.231}      # 16.000 kt, read at 12.000 at 12 s
T0, T1 = "2026-10-23T15:00:00+00:00", "2026-10-23T16:00:00+00:00"


def _row(**kw):
    row = {"swell_source": "nwps_height_ww3_dir", "face_ft": 8.0, "face_ft_raw": None,
           "swell_tp": 12.0, "dp": 300.0, "tp": 12.0, "swell_1_hs": 2.0, "swell_1_tp": 12.0,
           "swell_1_dp": 270.0, "wind_mult": 1.0, "tide_mult": 1.0, "chop_mult": 1.0,
           "stars": 4.5, "spot_id": 2, "valid_time": T0}
    row.update(kw)
    return row


def _rows():
    return [
        _row(spot_id=2, stars=4.5, valid_time=T0, **ONSHORE_16),  # B 4.5 -> 2.5 / 3.0
        _row(spot_id=2, stars=4.5, valid_time=T1, **LIGHT),       # B 4.5, unchanged
        _row(spot_id=1, stars=2.0, valid_time=T0, **LIGHT),       # A: the wired rater gives 3.0
        _row(spot_id=1, stars=3.0, valid_time=T1, **LIGHT),       # A 3.0, unchanged
    ]


def test_onshore_compare():
    rep = SP.onshore_compare(_rows(), SPOTS, NAMES, FACTORS, 1.6, "blown-2.5", n=2,
                             board_from=T1, focus=["B"])
    assert rep["replay"] == {"wired rater reproduces": 3, "wired rater does NOT reproduce": 1}
    assert (rep["compared"], rep["changed"], rep["ups"], rep["downs"]) == (3, 1, 0, 1)
    assert rep["delta_by_state"] == {"California": {0.0: 1}, "North Carolina": {-2.0: 1, 0.0: 1}}
    assert rep["changed_by_wind"] == {"onshore <=20 kt": 1}
    assert rep["blown_out_hours"] == 1
    assert rep["blown_out_above_2_5"] == {"before": 1, "after": 0}
    assert rep["homepage_before"] == [("B", "North Carolina", 4.5), ("A", "California", 3.0)]
    assert rep["homepage_after"] == rep["homepage_before"]
    (c,) = rep["focus"]["B"]["changed"]
    assert (c["valid_time"], c["wind"], c["wind_kt"], c["tp"], c["blown_out"], c["cap_before"],
            c["cap_after"], c["stars_before"], c["stars_after"]) == (
        T0, "onshore", 16.0, 12.0, True, 5.0, 2.5, 4.5, 2.5)
    rep = SP.onshore_compare(_rows(), SPOTS, NAMES, FACTORS, 1.6, "blown-3.0")
    assert rep["delta_by_state"]["North Carolina"] == {-1.5: 1, 0.0: 1}
    assert rep["blown_out_above_2_5"] == {"before": 1, "after": 1}


def test_the_homepage_reads_the_first_hour_at_or_after_board_from():
    # From the start: B's first hour is the onshore one, 4.5 -> 2.5, below A's 3.0 (A's first
    # hour at T0 does not reproduce, so A's first compared hour is T1).
    rep = SP.onshore_compare(_rows(), SPOTS, NAMES, FACTORS, 1.6, "blown-2.5", n=2)
    assert rep["homepage_before"] == [("B", "North Carolina", 4.5), ("A", "California", 3.0)]
    assert rep["homepage_after"] == [("A", "California", 3.0), ("B", "North Carolina", 2.5)]


def test_onshore_explain():
    x = SP.onshore_explain(_row(**ONSHORE_16), SPOTS["B"], FACTORS, 1.6)
    assert (x["stars"], x["wind"], x["wind_kt"], x["period_s"], x["allowance_kt"],
            x["blown_out"], x["wired_cap"]) == (4.5, "onshore", 16.0, 12.0, 4.0, True, 5.0)
    assert x["rules"] == {"blown-2.5": {"cap": 2.5, "stars": 2.5},
                          "blown-3.0": {"cap": 3.0, "stars": 3.0}}
    assert SP.onshore_explain(_row(stars=2.0, **ONSHORE_16), SPOTS["B"], FACTORS, 1.6) is None


def test_onshore_runs_on_its_own():
    for extra in (["--wind", "wind-both"], ["--rating", "both"], ["--nwps-shelter"]):
        with pytest.raises(SystemExit):
            SP.main(["--onshore", "blown-2.5", *extra])
    with pytest.raises(SystemExit):
        SP.main(["--onshore", "blown-4.0"])
