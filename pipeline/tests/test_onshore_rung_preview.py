"""The onshore-rung preview (star_preview --onshore): a first onshore cap rung at the "Blown
out" label's threshold, 6 m/s = 11.663 kt, read with the period allowance as today.

  blown-2.5   from 6 m/s 2.5; over 15 kt 2.0; over 20 kt 1.5 (the over-13 3.0 rung no longer binds)
  blown-3.0   the existing first rung (3.0) moved down from over 13 kt to 6 m/s
  blown-2.5-measured   blown-2.5 with the 6 m/s rung read at the measured speed (no allowance)
  label 15kt  "Blown out" from 15 kt measured (7.717 m/s); below it onshore reads "Choppy"

Every expected value is worked by hand, never read back from the functions under test:

  knots       m/s / 0.514444: 6.0 = 11.663   5.99 = 11.644   6.7 = 13.024   8.0 = 15.551
              8.231 = 16.000   11.0 = 21.382   13.4 = 26.048   6.2586 = 12.166 (14 mph)
              6.5 = 12.635   9.5 = 18.467   7.72 = 15.007   7.71 = 14.987
  allowance   max(0, period - 8) knots off the speed, up to 20 kt measured
  published   (the cap before the 6 m/s rung was wired) onshore over 13: 3.0, over 15: 2.0,
              over 20: 1.5; over 25: 1.0
  wired       since 2026-10-23 the published cap, and onshore at 6 m/s measured or more at most
              2.5: blown-2.5-measured
  classes     offshore bearing 90: wind from 270 is 180 off (onshore), from 0 is 90 off (cross)
  label       "Blown out": more than 120 off and 6 m/s or more (classifySurface)
"""
from __future__ import annotations

import pytest

from pipeline import interpret as I
from pipeline import star_preview as SP

RULES = ("blown-2.5", "blown-3.0")      # the rules that read the rung after the allowance


@pytest.mark.parametrize("wind_dir, speed, period, published, b25, b30, wired", [
    (270.0, 6.0, 8.0, 5.0, 2.5, 3.0, 2.5),     # 11.663 kt, at the label's threshold
    (270.0, 5.99, 8.0, 5.0, 5.0, 5.0, 5.0),    # 11.644 kt: under it
    (270.0, 6.7, 8.0, 3.0, 2.5, 3.0, 2.5),     # 13.024 kt: published 3.0
    (270.0, 8.0, 8.0, 2.0, 2.0, 2.0, 2.0),     # 15.551 kt: over 15, 2.0 under all
    (270.0, 6.2586, 8.0, 5.0, 2.5, 3.0, 2.5),  # 14 mph = 12.166 kt (Indialantic) at 8 s
    (270.0, 6.2586, 10.0, 5.0, 5.0, 5.0, 2.5), # ...at 10 s read at 10.166 after the allowance;
                                               # wired reads it measured: 2.5
    (270.0, 8.231, 12.0, 5.0, 2.5, 3.0, 2.5),  # 16.000 kt at 12 s read at 12.000
    (270.0, 11.0, 16.0, 1.5, 1.5, 1.5, 1.5),   # 21.382 kt: over 20, no allowance, 1.5
    (270.0, 13.4, 8.0, 1.0, 1.0, 1.0, 1.0),    # 26.048 kt: over 25, 1.0
    (0.0, 6.7, 8.0, 3.0, 3.0, 3.0, 3.0),       # cross-shore (90 off) 13.024 kt: cross ladder
                                               # over 13, 3.0, untouched
    (None, 6.7, 8.0, 5.0, 5.0, 5.0, 5.0),
])
def test_onshore_rung_cap(wind_dir, speed, period, published, b25, b30, wired):
    assert SP.published_cap(wind_dir, speed, 90.0, period) == published
    assert SP.onshore_rung_cap(wind_dir, speed, 90.0, period, "blown-2.5") == b25
    assert SP.onshore_rung_cap(wind_dir, speed, 90.0, period, "blown-3.0") == b30
    assert I.wind_star_cap(wind_dir, speed, 90.0, period) == wired


@pytest.mark.parametrize("wind_dir, speed, want", [
    (270.0, 6.0, True), (270.0, 5.99, False),
    (210.0, 6.5, False),     # exactly 120 off: the label reads "mixed"
    (211.0, 6.5, True),      # 121 off
    (None, 6.5, False),
])
def test_blown_out_is_the_labels_rule(wind_dir, speed, want):
    assert SP.blown_out(wind_dir, speed, 90.0) is want


@pytest.mark.parametrize("speed, period, b25, measured", [
    (6.2586, 10.0, 5.0, 2.5),   # 12.166 kt at 10 s: read at 10.166 after the allowance (no rung);
                                # measured 12.166 >= 11.663: 2.5
    (6.2586, 18.0, 5.0, 2.5),   # the measured rung ignores the period entirely
    (5.99, 8.0, 5.0, 5.0),      # 11.644 kt: under 6 m/s either way
    (9.5, 12.0, 2.5, 2.5),      # 18.467 kt at 12 s: 14.467 after the allowance, not over 15: 2.5
    (8.0, 8.0, 2.0, 2.0),       # 15.551 kt at 8 s: over 15, 2.0
    (8.0, 12.0, 5.0, 2.5),      # ...at 12 s read at 11.551: under 11.663 and not over 15, so
                                # blown-2.5 has no rung (5.0); measured 15.551 >= 11.663: 2.5
    (11.0, 16.0, 1.5, 1.5),     # 21.382 kt: over 20, no allowance, 1.5
])
def test_the_measured_rung(speed, period, b25, measured):
    assert SP.onshore_rung_cap(270.0, speed, 90.0, period, "blown-2.5-measured") == measured
    assert SP.onshore_rung_cap(270.0, speed, 90.0, period, "blown-2.5") == b25


@pytest.mark.parametrize("wind_dir, speed, six, fifteen", [
    (270.0, 7.72, True, True),     # 15.007 kt
    (270.0, 7.71, True, False),    # 14.987 kt: "Blown out" today, "Choppy" from 15 kt
    (270.0, 6.5, True, False),
    (270.0, 5.99, False, False),
    (0.0, 9.0, False, False),      # 90 off: "Mixed" under both
])
def test_the_two_labels(wind_dir, speed, six, fifteen):
    assert SP.blown_out(wind_dir, speed, 90.0, "6ms") is six
    assert SP.blown_out(wind_dir, speed, 90.0, "15kt") is fifteen


@pytest.mark.parametrize("wind_dir, speed, want", [
    (270.0, 6.5, "Choppy"), (0.0, 6.5, "Mixed"), (90.0, 6.5, "Clean"), (270.0, 2.0, "Clean"),
    (210.0, 6.5, "Mixed"),     # exactly 120 off: classifySurface's "off <= 120" is mixed
    (150.0, 6.5, "Clean"),     # exactly 60 off: "off <= 60" is clean
    (None, 6.5, "unknown"),
])
def test_the_label_below_blown_out(wind_dir, speed, want):
    assert SP._surface(wind_dir, speed, 90.0) == want


def test_a_rule_only_ever_lowers_the_published_cap_and_only_onshore():
    """A property over a grid, not an expected value: every 5 deg, 0-30 kt in quarter knots,
    periods 6-18 s. Each rule's cap is never above the published cap, and differs only onshore
    with the speed less the allowance at 6 m/s or more and not over 15 kt. The WIRED cap is
    blown-2.5-measured everywhere (the merge gate's rule, hour for hour, in unit form)."""
    for d in range(0, 360, 5):
        for q in range(0, 121):
            ms = q / 4 * 0.514444
            for tp in (6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0):
                wired = SP.published_cap(float(d), ms, 90.0, tp)
                assert I.wind_star_cap(float(d), ms, 90.0, tp) == SP.onshore_rung_cap(
                    float(d), ms, 90.0, tp, "blown-2.5-measured"), (d, q, tp)
                kt = ms / 0.514444
                read = kt if kt > 20.0 else kt - max(0.0, tp - 8.0)
                for r in RULES:
                    got = SP.onshore_rung_cap(float(d), ms, 90.0, tp, r)
                    assert got <= wired, (d, q, tp, r)
                    if got != wired:
                        assert I.wind_class(float(d), 90.0) == "onshore", (d, q, tp, r)
                        assert 11.663 <= read <= 15.0 + 1e-9, (d, q, tp, r, read)
                # The measured rung is never above blown-2.5, and differs from it only onshore
                # at 6 m/s measured or more.
                got = SP.onshore_rung_cap(float(d), ms, 90.0, tp, "blown-2.5-measured")
                b25 = SP.onshore_rung_cap(float(d), ms, 90.0, tp, "blown-2.5")
                assert got <= b25, (d, q, tp)
                if got != b25:
                    assert I.wind_class(float(d), 90.0) == "onshore" and kt >= 11.663, (d, q, tp)


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
ONSHORE_12 = {"wind_dir": 270.0, "wind_speed": 6.5}         # 12.635 kt, read at 8.635 at 12 s
T0, T1, T2 = ("2026-10-23T15:00:00+00:00", "2026-10-23T16:00:00+00:00",
              "2026-10-23T17:00:00+00:00")


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
        _row(spot_id=2, stars=4.5, valid_time=T2, **ONSHORE_12),  # B 4.5; measured rung 2.5
    ]


def test_onshore_compare():
    rep = SP.onshore_compare(_rows(), SPOTS, NAMES, FACTORS, 1.6, "blown-2.5", n=2,
                             board_from=T1, focus=["B"])
    # B at T2, 12.635 kt at 12 s: read at 8.635, no rung under blown-2.5, 4.5 stays.
    assert rep["replay"] == {"published rater reproduces": 4, "published rater does NOT reproduce": 1}
    assert (rep["compared"], rep["changed"], rep["ups"], rep["downs"]) == (4, 1, 0, 1)
    assert rep["delta_by_state"] == {"California": {0.0: 1}, "North Carolina": {-2.0: 1, 0.0: 2}}
    assert rep["changed_by_wind"] == {"onshore <=20 kt": 1}
    # "Blown out" today: T0 (16 kt) and T2 (12.6 kt), both 4.5; under blown-2.5 T0 is 2.5.
    # From 15 kt only T0 is "Blown out"; T2 reads "Choppy", the one label change.
    assert rep["blown_out"] == {"6ms": {"hours": 2, "above_2_5_before": 2, "above_2_5_after": 1},
                                "15kt": {"hours": 1, "above_2_5_before": 1, "above_2_5_after": 0}}
    assert rep["label_15kt_changes"] == 1
    assert rep["homepage_before"] == [("B", "North Carolina", 4.5), ("A", "California", 3.0)]
    assert rep["homepage_after"] == rep["homepage_before"]
    (c,) = rep["focus"]["B"]["changed"]
    assert (c["valid_time"], c["wind"], c["wind_kt"], c["tp"], c["blown_out"], c["cap_before"],
            c["cap_after"], c["stars_before"], c["stars_after"]) == (
        T0, "onshore", 16.0, 12.0, True, 5.0, 2.5, 4.5, 2.5)
    rep = SP.onshore_compare(_rows(), SPOTS, NAMES, FACTORS, 1.6, "blown-3.0")
    assert rep["delta_by_state"]["North Carolina"] == {-1.5: 1, 0.0: 2}
    assert rep["blown_out"]["6ms"] == {"hours": 2, "above_2_5_before": 2, "above_2_5_after": 2}
    # The measured rung catches T2 too: 4.5 -> 2.5, and no "Blown out" hour stays above 2.5.
    rep = SP.onshore_compare(_rows(), SPOTS, NAMES, FACTORS, 1.6, "blown-2.5-measured")
    assert rep["delta_by_state"]["North Carolina"] == {-2.0: 2, 0.0: 1}
    assert rep["blown_out"]["6ms"] == {"hours": 2, "above_2_5_before": 2, "above_2_5_after": 0}


def test_the_homepage_reads_the_first_hour_at_or_after_board_from():
    # From the start: B's first hour is the onshore one, 4.5 -> 2.5, below A's 3.0 (A's first
    # hour at T0 does not reproduce, so A's first compared hour is T1).
    rep = SP.onshore_compare(_rows(), SPOTS, NAMES, FACTORS, 1.6, "blown-2.5", n=2)
    assert rep["homepage_before"] == [("B", "North Carolina", 4.5), ("A", "California", 3.0)]
    assert rep["homepage_after"] == [("A", "California", 3.0), ("B", "North Carolina", 2.5)]


def test_onshore_explain():
    x = SP.onshore_explain(_row(**ONSHORE_16), SPOTS["B"], FACTORS, 1.6)
    assert (x["stars"], x["wind"], x["wind_kt"], x["period_s"], x["allowance_kt"],
            x["blown_out"], x["published_cap"], x["wired_cap"]) == (
        4.5, "onshore", 16.0, 12.0, 4.0, True, 5.0, 2.5)
    assert x["label"] == {"6ms": "Blown out", "15kt": "Blown out"}
    assert x["rules"] == {"blown-2.5": {"cap": 2.5, "stars": 2.5},
                          "blown-3.0": {"cap": 3.0, "stars": 3.0},
                          "blown-2.5-measured": {"cap": 2.5, "stars": 2.5}}
    x = SP.onshore_explain(_row(**ONSHORE_12), SPOTS["B"], FACTORS, 1.6)
    assert x["label"] == {"6ms": "Blown out", "15kt": "Choppy"}
    assert x["rules"]["blown-2.5"] == {"cap": 5.0, "stars": 4.5}
    assert x["rules"]["blown-2.5-measured"] == {"cap": 2.5, "stars": 2.5}
    assert SP.onshore_explain(_row(stars=2.0, **ONSHORE_16), SPOTS["B"], FACTORS, 1.6) is None


def test_onshore_runs_on_its_own():
    for extra in (["--wind", "wind-both"], ["--rating", "both"], ["--nwps-shelter"]):
        with pytest.raises(SystemExit):
            SP.main(["--onshore", "blown-2.5", *extra])
    with pytest.raises(SystemExit):
        SP.main(["--onshore", "blown-4.0"])


def test_the_merge_gate_passes_when_the_wired_rater_is_the_rule():
    # Reproduced on the published cap: B T0 (16 kt, 4.5), B T1 (light, 4.5), A T1 (light, 3.0),
    # B T2 (12.6 kt, 4.5); A T0 (2.0) does not reproduce. The wired cap (6 m/s measured: 2.5)
    # gives T0 2.5 and T2 2.5, the same as blown-2.5-measured built here.
    c = SP.onshore_rater_check(_rows(), SPOTS, NAMES, FACTORS, 1.6)
    assert (c["hours"], c["same"], c["different"], c["examples"]) == (4, 4, 0, [])
    assert c["wired_delta_by_state"] == {"California": {0.0: 1},
                                         "North Carolina": {-2.0: 2, 0.0: 1}}


def test_the_merge_gate_counts_a_difference():
    # Against blown-2.5 (the rung after the allowance), T2 differs: preview 4.5 (12.635 kt at
    # 12 s read at 8.635, no rung), wired 2.5.
    c = SP.onshore_rater_check(_rows(), SPOTS, NAMES, FACTORS, 1.6, rule="blown-2.5")
    assert (c["same"], c["different"]) == (3, 1)
    (e,) = c["examples"]
    assert (e["valid_time"], e["published"], e["preview"], e["wired"]) == (T2, 4.5, 4.5, 2.5)
