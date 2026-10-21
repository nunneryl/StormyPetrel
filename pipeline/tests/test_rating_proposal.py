"""The proposed strong-wind cap and beach-break ceiling (interpret.py), and their read-only
preview (star_preview --rating).

Every expected value is worked by hand, never read back from the functions under test:

  knots            m/s / 0.514444: 12.5 m/s = 24.298 kt, 13.4 m/s = 26.048 kt, 8 m/s = 15.551 kt;
                   6.687772 m/s = 13.0 kt and 12.8611 m/s = 25.0 kt exactly (13 and 25 x 0.514444)
                   7.2 m/s = 13.996 kt, 20 m/s = 38.877 kt
  tolerance        max(0, period - 8) knots off the speed, on the ladder only
  ladder           onshore >13: 3.0, >15: 2.0, >20: 1.5
                   cross   >13: 3.0, >15: 2.5, >20: 1.5
  above 25 kt      measured, any period: 1.0 cross-shore and onshore, 2.0 offshore
  rules            cap; ceiling (beach and jetty over sand); both
  classes          angle off the offshore bearing: < 60 offshore, 60-120 cross, >= 120 onshore
  beach size       as size_score to 8 ft (4.5), 10 ft 4.5, 12 ft 3.5, 15 ft 2.5, 20 ft+ 1.5
  closeout         1.0 to 8 ft, then beach / size: 9 ft 4.5/4.75 = 0.947368, 10 ft 0.9,
                   12 ft 0.7, 25 ft 0.3; the ceiling reads the calibrated face (raw / factor)
  0.8**0.35        exp(0.35 x ln 0.8) = 0.924872
"""
from __future__ import annotations

import pytest

from pipeline import interpret as I
from pipeline import star_preview as SP

# --------------------------------------------------------------------------- #
# wind_class: wind_multiplier's own band edges                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("wind_dir, expected", [
    (245.0, "offshore"),   # 0 off
    (304.0, "offshore"),   # 59 off
    (305.0, "cross"),      # 60 off
    (0.0, "cross"),        # 115 off: Jennettes in a north wind
    (4.0, "cross"),        # 119 off
    (5.0, "onshore"),      # 120 off
    (65.0, "onshore"),     # 180 off
])
def test_wind_class_at_jennettes(wind_dir, expected):
    assert I.wind_class(wind_dir, 245.0) == expected


# --------------------------------------------------------------------------- #
# wind_star_cap                                                                #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("speed, period, expected", [
    (13.4, 11.0, 1.0),     # 26.05 kt > 25: 1.0 whatever the period
    (13.4, 16.0, 1.0),     # still 1.0 with 8 kt of tolerance
    (12.5, 11.0, 1.5),     # 24.30 - 3 = 21.30 > 20: cross 1.5, as onshore
    (12.5, 8.0, 1.5),      # 24.30 > 20: 1.5
    (12.5, 16.0, 2.5),     # 24.30 - 8 = 16.30 > 15: 2.5
    (12.5, 7.0, 1.5),      # period under 8 gives no negative tolerance
    (12.5, None, 1.5),     # no period: no tolerance
    (8.0, 8.0, 2.5),       # 15.55 kt > 15: cross 2.5 where onshore is 2.0
    (7.2, 8.0, 3.0),       # 14.00 kt > 13: 3.0
    (6.687772, 8.0, 5.0),  # exactly 13 kt is not above 13
    (6.687772, 7.0, 5.0),  # 13 kt at 7 s: no negative tolerance to push it over 13
    (12.8611, 8.0, 1.5),   # exactly 25 kt: not above 25, > 20: 1.5
])
def test_cross_shore_cap(speed, period, expected):
    assert I.wind_star_cap(0.0, speed, 245.0, period) == expected


@pytest.mark.parametrize("speed, period, expected", [
    (8.0, 8.0, 2.0),       # 15.55 kt > 15: 2.0
    (8.0, 10.0, 3.0),      # 13.55 kt > 13: 3.0
    (8.0, 11.0, 5.0),      # 12.55 kt: no cap
    (12.5, 11.0, 1.5),     # 21.30 kt > 20: 1.5
    (12.8611, 8.0, 1.5),   # exactly 25 kt: > 20: 1.5
    (13.4, 8.0, 1.0),      # 26.05 kt: 1.0
])
def test_onshore_cap(speed, period, expected):
    assert I.wind_star_cap(65.0, speed, 245.0, period) == expected


@pytest.mark.parametrize("speed, period, expected", [
    (20.0, 8.0, 2.0),      # 38.88 kt > 25: 2.0
    (13.4, 16.0, 2.0),     # 26.05 kt measured > 25: 2.0, the period does not shift it
    (12.8611, 8.0, 5.0),   # exactly 25 kt: no cap
    (12.5, 8.0, 5.0),      # 24.30 kt: offshore has no ladder
])
def test_offshore_is_capped_only_above_25_kt(speed, period, expected):
    assert I.wind_star_cap(245.0, speed, 245.0, period) == expected


@pytest.mark.parametrize("args", [(None, 12.5, 245.0, 8.0), (0.0, None, 245.0, 8.0),
                                  (0.0, 12.5, None, 8.0)])
def test_missing_wind_or_bearing_is_no_cap(args):
    assert I.wind_star_cap(*args) == 5.0


# --------------------------------------------------------------------------- #
# The beach-break size curve                                                   #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("face, expected", [
    (7.0, 4.25),   # as size_score below 8 ft
    (9.0, 4.5),    # flat 8-10
    (11.0, 4.0),   # halfway 10 (4.5) -> 12 (3.5)
    (13.5, 3.0),   # halfway 12 (3.5) -> 15 (2.5)
    (25.0, 1.5),   # flat from 20
])
def test_beach_size_score(face, expected):
    assert I.beach_size_score(face) == pytest.approx(expected)


@pytest.mark.parametrize("spot, jetty, expected", [
    ({"break_type": "beach", "bottom": "sand"}, False, True),
    ({"break_type": "beach", "bottom": "mixed"}, False, False),
    ({"break_type": "jetty", "bottom": "sand"}, False, False),
    ({"break_type": "jetty", "bottom": "sand"}, True, True),
    ({"break_type": "point", "bottom": "sand"}, True, False),
    ({}, True, False),
])
def test_sand_beach_break(spot, jetty, expected):
    assert SP.is_sand_beach_break(spot, jetty) is expected


@pytest.mark.parametrize("bt, bo, expected", [
    ("researched", "researched", True), ("reviewed", "scraped", True),
    ("researched", "model_recall", False), ("model_recall", "researched", False),
    ("unattributed", "unattributed", False), (None, None, False),
])
def test_cited_needs_both_sources_trusted(bt, bo, expected):
    assert SP.cited({"break_type_source": bt, "bottom_source": bo}) is expected


# --------------------------------------------------------------------------- #
# composite_stars with the two keywords                                        #
# --------------------------------------------------------------------------- #

def test_defaults_change_nothing():
    # 11 ft: size 5.0 x 0.924872 = 4.624 -> round(9.249)/2 = 4.5
    assert I.composite_stars(11.0, 0.8, 1.0) == 4.5


def test_the_cap_is_a_ceiling_on_the_stars():
    assert I.composite_stars(11.0, 0.8, 1.0, star_cap=2.0) == 2.0


def test_the_cap_never_raises_a_rating():
    # 2 ft: size 2.0 x 0.924872 = 1.850 -> round(3.70)/2 = 2.0, under a 3.0 cap
    assert I.composite_stars(2.0, 0.8, 1.0, star_cap=3.0) == 2.0


@pytest.mark.parametrize("face, expected", [
    (7.0, 1.0), (8.0, 1.0), (9.0, 0.947368), (10.0, 0.9), (12.0, 0.7), (25.0, 0.3),
])
def test_closeout_factor(face, expected):
    assert round(I.closeout_factor(face), 6) == expected


def test_the_ceiling_at_the_raw_face_is_the_beach_curve():
    # 11 ft read at 11 ft: 5.0 x 0.8 = 4.0 x 0.924872 = 3.699 -> round(7.399)/2 = 3.5
    assert I.composite_stars(11.0, 0.8, 1.0, closeout_face_ft=11.0) == 3.5


def test_a_calibrated_face_under_8_ft_lifts_the_ceiling():
    # 11 ft raw, factor 1.5: the ceiling reads 7.33 ft, keeps all of size 5.0 -> 4.5
    assert I.composite_stars(11.0, 0.8, 1.0, closeout_face_ft=11.0 / 1.5) == 4.5


def test_a_calibrated_face_above_the_raw_one_closes_out_harder():
    # 9 ft raw (size 4.75) read at 12 ft (factor 0.75): 4.75 x 0.7 = 3.325 x 0.924872 = 3.075
    # -> round(6.15)/2 = 3.0. At the raw face it keeps 0.947368: 4.5 x 0.924872 = 4.162 -> 4.0
    assert I.composite_stars(9.0, 0.8, 1.0, closeout_face_ft=12.0) == 3.0
    assert I.composite_stars(9.0, 0.8, 1.0, closeout_face_ft=9.0) == 4.0


def test_both_rules_and_the_floor():
    assert I.composite_stars(11.0, 0.8, 1.0, star_cap=1.0, closeout_face_ft=11.0) == 1.0


# --------------------------------------------------------------------------- #
# The preview                                                                   #
# --------------------------------------------------------------------------- #
#
# A MOP row on a window open 250-290 aimed at 270, swell from 270 (gain 1.0), face 11 ft,
# period quality 1.0 at 13 s, wind_mult 0.8, wind from 0 at 12.5 m/s. The spot's offshore
# bearing is 90, so the wind is 90 off it: cross. 24.30 kt - (13 - 8) = 19.30 > 15: cap 2.5.
#   published / replay   5.0 x 0.924872 = 4.624 -> 4.5
#   cap                  2.5
#   ceiling (beach/sand) 4.0 x 0.924872 = 3.699 -> 3.5
#   both                 min(3.5, 2.5) = 2.5

WEST_BEACH = {"swell_window_arcs": [{"min": 250, "max": 290, "span": 40}],
              "optimal_swell_dir": 270, "orientation_deg": 270.0, "offshore_wind_deg": 90.0,
              "break_type": "beach", "bottom": "sand"}
WEST_REEF = dict(WEST_BEACH, break_type="reef", bottom="rock")
WEST_JETTY = dict(WEST_BEACH, break_type="jetty")


def _row(**kw):
    row = {"swell_source": "cdip_mop", "swell_dp": 270.0, "swell_tp": 13.0, "face_ft": 11.0,
           "face_ft_raw": None, "period_quality": 1.0, "wind_mult": 0.8, "tide_mult": 1.0,
           "chop_mult": 1.0, "wind_dir": 0.0, "wind_speed": 12.5, "stars": 4.5,
           "spot_id": 1, "valid_time": "2026-10-20T15:00:00+00:00"}
    row.update(kw)
    return row


@pytest.mark.parametrize("rule, spot, expected", [
    ("cap", WEST_BEACH, (2.5, "wind cap")),
    ("ceiling", WEST_BEACH, (3.5, "beach ceiling")),
    ("both", WEST_BEACH, (2.5, "beach ceiling + wind cap")),
    ("ceiling", WEST_REEF, (4.5, "")),
    ("ceiling", WEST_JETTY, (3.5, "beach ceiling")),
    ("both", WEST_JETTY, (2.5, "beach ceiling + wind cap")),
])
def test_rating_after(rule, spot, expected):
    row = _row()
    rb = SP.rescore(row, spot)
    assert SP.rating_after(row, spot, rb, rule) == expected


def test_the_replay_carries_the_period_it_rated_on():
    rb = SP.rescore(_row(), WEST_BEACH)
    assert (rb["eff"], rb["pq"], rb["tp"]) == (11.0, 1.0, 13.0)


NAMES = {1: ("A", "North Carolina"), 2: ("B", "California"), 3: ("C", "Hawaii")}
SPOTS = {"A": WEST_BEACH, "B": WEST_REEF, "C": WEST_BEACH}


def _rows():
    return [
        # A: the case above, 4.5 -> 2.5 under both
        _row(),
        # A, later: published 3.0 but replays 4.5 -> not reproduced, not compared
        _row(stars=3.0, valid_time="2026-10-20T16:00:00+00:00"),
        # B: reef, light offshore wind (from 90, 0 off): nothing moves. 5.0 x 1.0 = 5.0
        _row(spot_id=2, wind_dir=90.0, wind_speed=3.0, wind_mult=1.0, stars=5.0),
        # C: 6 ft, wind from 0 at 6.0 m/s = 11.66 kt, below the ladder; size 4.0 x 0.924872
        # = 3.699 -> 3.5, the beach curve is the same at 6 ft: nothing moves
        _row(spot_id=3, face_ft=6.0, wind_speed=6.0, stars=3.5),
    ]


def test_rating_compare_counts_reproduced_rows_by_source_and_cause():
    rep = SP.rating_compare(_rows(), SPOTS, NAMES, "both")
    assert rep["replay"] == {"MOP NOT reproduced": 1, "MOP reproduced": 3}
    assert rep["compared"] == 3
    assert (rep["changed_spot_hours"], rep["changed_spots"], rep["ups"], rep["downs"]) == (1, 1, 0, 1)
    assert rep["delta_by_group"] == {"MOP": {-2.0: 1, 0.0: 2}}
    assert rep["change_by_cause"] == {"beach ceiling + wind cap": 1}
    assert rep["change_by_wind"] == {"cross <=25 kt": 1}
    assert rep["ceiling_hours_by_provenance"] == {"model recall or unattributed": 1}
    assert rep["ceiling_unverified_spots"] == [{
        "spot": "A", "state": "North Carolina", "break_type": "beach", "break_type_source": None,
        "bottom": "sand", "bottom_source": None, "hours": 1, "largest_drop": -2.0,
        "largest_eff_ft": 11.0}]


def test_a_cited_spot_is_not_on_the_unverified_list():
    spots = dict(SPOTS, A=dict(WEST_BEACH, break_type_source="researched",
                               bottom_source="reviewed"))
    rep = SP.rating_compare(_rows(), spots, NAMES, "both")
    assert rep["ceiling_hours_by_provenance"] == {"cited": 1}
    assert rep["ceiling_unverified_spots"] == []


def test_the_unverified_list_counts_hours_and_keeps_the_largest_drop():
    # Two A hours: 15 ft (size 5.0 x 0.924872 = 4.5 -> 2.5 x 0.924872 = 2.312 -> 2.5, a 2.0
    # drop), then 11 ft (4.5 -> 3.5 on the ceiling alone). The larger drop comes first.
    rows = [_row(face_ft=15.0), _row(valid_time="2026-10-20T16:00:00+00:00")]
    rep = SP.rating_compare(rows, SPOTS, NAMES, "ceiling")
    (u,) = rep["ceiling_unverified_spots"]
    assert (u["hours"], u["largest_drop"], u["largest_eff_ft"]) == (2, -2.0, 15.0)
    ex = rep["top20"][0]["example"]
    assert (ex["spot"], ex["stars_before"], ex["stars_after"], ex["wind_kt"]) == ("A", 4.5, 2.5, 24.3)


def test_the_leaderboard_drops_the_capped_spot():
    # Soonest rows: A 4.5 (-> 2.5), B 5.0, C 3.5. Top 2 before: B, A. After: B 5.0, C 3.5.
    lb = SP.leaderboard(_rows(), SPOTS, NAMES, "both", n=2)
    assert [r["spot"] for r in lb["before"]] == ["B", "A"]
    assert [(r["spot"], r["stars"]) for r in lb["after"]] == [("B", 5.0), ("C", 3.5)]
    assert lb["drop_out"] == [{"spot": "A", "state": "North Carolina",
                               "stars_before": 4.5, "stars_after": 2.5}]
    assert lb["come_in"] == [{"spot": "C", "state": "Hawaii", "stars": 3.5}]


def test_a_row_that_does_not_reproduce_keeps_its_published_stars_on_the_leaderboard():
    rows = [_row(stars=3.0)]          # replays 4.5
    lb = SP.leaderboard(rows, SPOTS, NAMES, "both", n=1)
    assert lb["after"] == [{"spot": "A", "state": "North Carolina", "stars": 3.0,
                            "replayed": False}]


def test_explain_prints_every_factor():
    x = SP.explain(_row(), WEST_BEACH, ("cap", "both"))
    assert (x["wind_class"], x["wind_angle_off_offshore"]) == ("cross", 90.0)
    assert round(x["wind_kt"], 2) == 24.30 and round(x["wind_mph"], 2) == 27.96
    assert x["terms"]["size_score"] == 5.0
    assert round(x["terms"]["wind"], 6) == 0.924872
    assert (x["terms"]["tide"], x["terms"]["chop"], x["terms"]["period"]) == (1.0, 1.0, 1.0)
    assert round(x["raw"], 3) == 4.624
    assert (x["replay_stars"], x["reproduced"], x["wind_cap"]) == (4.5, True, 2.5)
    assert x["beach_size_score"] == pytest.approx(4.0)
    assert x["proposed"] == {"cap": (2.5, "wind cap"), "both": (2.5, "beach ceiling + wind cap")}


def test_slug():
    assert SP._slug("Kill Devil Hills") == "kill-devil-hills"
    assert SP._slug("Jennette's Pier") == "jennette-s-pier"


def test_the_cli_refuses_an_unknown_rule():
    with pytest.raises(SystemExit):
        SP.main(["--rating", "softer"])


def test_rating_runs_on_its_own():
    with pytest.raises(SystemExit):
        SP.main(["--rating", "cap", "--tide", "neutral"])


def test_a_cap_equal_to_the_stars_is_not_a_cause():
    # 15 ft on the beach curve: 2.5 x 0.924872 = 2.312 -> round(4.62)/2 = 2.5, and the cap is
    # 2.5 (cross, 24.30 - 5 = 19.30 kt). Only the ceiling moved the hour (4.5 -> 2.5).
    row = _row(face_ft=15.0)
    rb = SP.rescore(row, WEST_BEACH)
    assert SP.rating_after(row, WEST_BEACH, rb, "both") == (2.5, "beach ceiling")


def test_equal_stars_keep_spot_id_order_on_the_leaderboard():
    # Light offshore wind on both: 11 ft reef, 5.0 x 1.0 = 5.0, nothing moves. Spot 3 comes
    # first in the rows; the page breaks the tie by spot id, so spot 1 leads.
    calm = dict(wind_dir=90.0, wind_speed=3.0, wind_mult=1.0, stars=5.0)
    rows = [_row(spot_id=3, **calm), _row(spot_id=1, **calm)]
    spots = {"A": WEST_REEF, "C": WEST_REEF}
    lb = SP.leaderboard(rows, spots, NAMES, "both", n=2)
    assert [r["spot"] for r in lb["before"]] == ["A", "C"]
    assert [r["spot"] for r in lb["after"]] == ["A", "C"]



# --------------------------------------------------------------------------- #
# The ceiling at the calibrated face                                           #
# --------------------------------------------------------------------------- #
# Spot A's MOP factor 1.5 makes the 11 ft hour read 7.33 ft (no closeout): 4.5 stays 4.5
# under the ceiling alone, where the raw face gave 3.5. A factor of 0.75 makes a 9 ft hour
# (published 4.75 x 0.924872 = 4.393 -> 4.5) read 12 ft: 3.0, where the raw face gave 4.0.

FACTORS = {"a": {"factor": 1.5}, "c": {"factor": 0.75}}
NAMED = {"A": dict(WEST_BEACH, name="A"), "B": dict(WEST_REEF, name="B"),
         "C": dict(WEST_BEACH, name="C")}


@pytest.mark.parametrize("spot, row, calibrated, expected", [
    ("A", {}, True, (4.5, "")),
    ("A", {}, False, (3.5, "beach ceiling")),
    ("C", {"face_ft": 9.0}, True, (3.0, "beach ceiling")),
    ("C", {"face_ft": 9.0}, False, (4.0, "beach ceiling")),
])
def test_rating_after_reads_the_calibrated_face(spot, row, calibrated, expected):
    r = _row(**row)
    rb = SP.rescore(r, NAMED[spot])
    assert SP.rating_after(r, NAMED[spot], rb, "ceiling", FACTORS, calibrated) == expected


def test_the_mop_tier_is_never_calibrated():
    spot = dict(NAMED["A"], swell_window_source="cdip_mop")
    r = _row()
    assert SP.rating_after(r, spot, SP.rescore(r, spot), "ceiling", FACTORS) == (3.5, "beach ceiling")


def test_no_factor_reads_the_raw_face():
    r = _row()
    spot = dict(WEST_BEACH, name="Z")
    assert SP.rating_after(r, spot, SP.rescore(r, spot), "ceiling", FACTORS) == (3.5, "beach ceiling")


def test_the_calibration_diff_lists_every_hour_that_moves():
    rows = [
        _row(),                                            # A 11 ft: 3.5 raw -> 4.5 calibrated
        _row(spot_id=2, wind_dir=90.0, wind_speed=3.0, wind_mult=1.0, stars=5.0),  # B reef
        _row(spot_id=3, face_ft=9.0, valid_time="2026-10-20T16:00:00+00:00"),       # C 4.0 -> 3.0
        _row(spot_id=3, face_ft=6.0, stars=3.5),           # C 6 ft reads 8 ft: no closeout
    ]
    d = SP.calibration_diff(rows, NAMED, NAMES, "ceiling", FACTORS)
    assert (d["hours"], d["ups"], d["spots"]) == (2, 1, ["A", "C"])
    assert [(h["spot"], h["stars_raw_ceiling"], h["stars_calibrated_ceiling"], h["calibrated_ft"])
            for h in d["changes"]] == [("A", 3.5, 4.5, 7.33), ("C", 4.0, 3.0, 12.0)]


def test_explain_shows_the_face_the_ceiling_reads():
    x = SP.explain(_row(), NAMED["A"], ("ceiling",), FACTORS)
    assert (x["face_factor"], round(x["closeout_face_ft"], 2), x["closeout_factor"]) == (1.5, 7.33, 1.0)
    assert x["proposed"] == {"ceiling": (4.5, "")}


def test_the_leaderboard_reads_the_calibrated_face():
    # A: 11 ft with factor 1.5 keeps 4.5 under the ceiling (3.5 at the raw face).
    lb = SP.leaderboard([_row()], NAMED, NAMES, "ceiling", n=1, factors=FACTORS)
    assert lb["after"] == [{"spot": "A", "state": "North Carolina", "stars": 4.5, "replayed": True}]


# --------------------------------------------------------------------------- #
# The merge gate: the wired rater against the preview's 'both'                  #
# --------------------------------------------------------------------------- #

def test_the_rater_check_on_rate_spot_path_rows():
    # The three reproduced rows of _rows(): A 4.5 -> 2.5, B 5.0, C 3.5, through
    # composite_stars with interpret.rule_keywords; no difference from 'both'.
    c = SP.rater_check(_rows(), SPOTS, NAMES, None)
    assert (c["hours"], c["same"], c["different"], c["examples"]) == (3, 3, 0, [])
    assert c["wired_delta_by_group"] == {"MOP": {-2.0: 1, 0.0: 2}}


# An NWPS row: one partition from 270 at 12 s, face 8.0 ft (the period is unchanged, so the
# face stays 8.0), gain 1: size(8) 4.5 x 0.95**0.25 0.987259 = 4.443 -> 4.5 published.
def _nwps_row(**kw):
    row = {"swell_source": "nwps_height_ww3_dir", "face_ft": 8.0, "face_ft_raw": None,
           "swell_tp": 12.0, "dp": 300.0, "tp": 12.0, "swell_1_hs": 2.0, "swell_1_tp": 12.0,
           "swell_1_dp": 270.0, "wind_mult": 1.0, "tide_mult": 1.0, "chop_mult": 1.0,
           "stars": 4.5, "spot_id": 1, "valid_time": "2026-10-20T15:00:00+00:00",
           "wind_dir": 0.0, "wind_speed": 12.5}
    row.update(kw)
    return row


def test_the_wired_nwps_rater_caps_a_cross_shore_gale():
    # 24.30 kt - (12 - 8) = 20.30 > 20: cross 1.5; 8 ft keeps all its size credit
    rb = SP.rescore(_nwps_row(), WEST_BEACH)
    assert rb["stars"] == 4.5
    assert SP.wired_rater(_nwps_row(), WEST_BEACH, rb, None) == 1.5
    c = SP.rater_check([_nwps_row()], {"A": WEST_BEACH}, NAMES, None)
    assert (c["same"], c["different"], c["wired_delta_by_group"]) == (1, 0, {"NWPS": {-3.0: 1}})


def test_the_wired_nwps_rater_reads_the_face_factor():
    # Light offshore wind, factor 0.75: the ceiling reads 8 / 0.75 = 10.667 ft and keeps
    # (4.5 - 0.5 x 0.667) / 5 = 0.833333: 4.5 x 0.833333 x 0.987259 = 3.702 -> 3.5
    row = _nwps_row(wind_dir=90.0, wind_speed=2.0)
    rb = SP.rescore(row, NAMED["C"])
    assert SP.wired_rater(row, NAMED["C"], rb, 0.75) == 3.5
    assert SP.wired_rater(row, NAMED["C"], rb, None) == 4.5
    c = SP.rater_check([dict(row, spot_id=3)], NAMED, NAMES, FACTORS)
    assert (c["same"], c["different"]) == (1, 0)


def test_the_rater_check_reports_a_difference():
    # The two cannot disagree on a real row; force the preview's answer to see one reported.
    row = _nwps_row(wind_dir=90.0, wind_speed=2.0)
    real = SP.rating_after
    SP.rating_after = lambda *a, **k: (4.5, "")
    try:
        c = SP.rater_check([row], {"A": WEST_BEACH}, NAMES, None)
    finally:
        SP.rating_after = real
    # wired: 8 ft on the beach curve keeps everything -> 4.5; preview forced to 4.5: equal.
    assert c["different"] == 0
    SP.rating_after = lambda *a, **k: (3.0, "")
    try:
        c = SP.rater_check([row], {"A": WEST_BEACH}, NAMES, None)
    finally:
        SP.rating_after = real
    assert c["different"] == 1
    assert c["examples"] == [{"spot": "A", "state": "North Carolina",
                              "valid_time": "2026-10-20T15:00:00+00:00", "published": 4.5,
                              "preview": 3.0, "wired": 4.5}]


def test_node_position():
    n = SP.node_position({"lat": 0.0, "lng": 0.0, "nwps_node_lat": 0.01, "nwps_node_lng": 0.0})
    assert (round(n["km"], 4), round(n["bearing"], 6)) == (1.1119, 0.0)
    assert SP.node_position({"lat": 0.0, "lng": 0.0}) is None


# --------------------------------------------------------------------------- #
# --variants: against the live rater                                           #
# --------------------------------------------------------------------------- #
# An NWPS row, 8 ft at 12 s, light offshore wind (no cap), wind/tide/chop 1.0:
#   live    size(8) 4.5 x 0.987259 = 4.443 -> 4.5 (the ceiling keeps all of 8 ft or less)
# Spot A has a MOP factor of 3.0, spot B none; M = 1.6.
#   cal-median  A: 8 x 1.6 / 3 = 4.2667 ft, size 3.1333 x 0.987259 = 3.093 -> 3.0;  B: 4.5
#   cal-all     A: 8 / 3 = 2.6667 ft, size 2.3333 x 0.987259 = 2.304 -> 2.5
#               B: 8 / 1.6 = 5.0 ft, size 3.5 x 0.987259 = 3.455 -> 3.5
#   swell-height  swh = 8 / (1.15 x 3.281) = 2.12024 m; shts 1.0 m: 8 x 1.0 / 2.12024 = 3.7731 ft,
#               size 2.8866 x 0.987259 = 2.850 -> 3.0
FACTORS3 = {"a": {"factor": 3.0}}
LIGHT = {"wind_dir": 90.0, "wind_speed": 2.0}
SPOTS_V = {"A": dict(WEST_BEACH, name="A"), "B": dict(WEST_BEACH, name="B")}


@pytest.mark.parametrize("spot, variant, expected", [
    ("A", "cal-median", 3.0), ("B", "cal-median", 4.5),
    ("A", "cal-all", 2.5), ("B", "cal-all", 3.5),
])
def test_calibration_variants(spot, variant, expected):
    row = _nwps_row(**LIGHT)
    rb = SP.rescore(row, SPOTS_V[spot])
    assert SP.live_stars(row, SPOTS_V[spot], rb, FACTORS3) == 4.5
    assert SP.variant_stars(row, SPOTS_V[spot], rb, FACTORS3, variant, 1.6) == expected


def test_swell_height_variant():
    row = _nwps_row(swell_hs=1.0, **LIGHT)
    rb = SP.rescore(row, SPOTS_V["B"])
    assert round(SP.variant_scale(row, SPOTS_V["B"], FACTORS3, "swell-height", 1.6), 6) == 0.471644
    assert SP.variant_stars(row, SPOTS_V["B"], rb, FACTORS3, "swell-height", 1.6) == 3.0
    assert SP.variant_stars(_nwps_row(**LIGHT), SPOTS_V["B"], rb, FACTORS3, "swell-height", 1.6) is None


def test_a_mop_row_counts_as_factor_one_and_ww3_rows_keep_their_height():
    mop_row = _row()
    assert SP.variant_scale(mop_row, SPOTS_V["A"], FACTORS3, "cal-median", 1.6) == 1.6
    assert SP.variant_scale(mop_row, SPOTS_V["A"], FACTORS3, "cal-all", 1.6) == 1.0
    assert SP.variant_scale(mop_row, SPOTS_V["A"], FACTORS3, "swell-height", 1.6) == 1.0


def test_median_factor():
    assert SP.median_factor({"a": {"factor": 1.2}, "b": {"factor": 3.0}, "c": {"factor": 1.5}}) == 1.5
    assert SP.median_factor({}) is None


def test_variant_compare():
    rows = [_nwps_row(**LIGHT),                                         # A 4.5 -> 3.0
            _nwps_row(spot_id=2, **LIGHT),                              # B 4.5, unchanged
            _nwps_row(spot_id=2, stars=2.0, valid_time="2026-10-20T16:00:00+00:00", **LIGHT)]
    names = {1: ("A", "California"), 2: ("B", "North Carolina")}
    rep = SP.variant_compare(rows, SPOTS_V, names, FACTORS3, "cal-median", 1.6, n=1)
    assert rep["replay"] == {"NWPS NOT reproduced": 1, "NWPS reproduced": 2}
    assert (rep["compared"], rep["changed_spot_hours"], rep["ups"], rep["downs"]) == (2, 1, 0, 1)
    assert rep["delta_by_group"] == {"NWPS": {-1.5: 1, 0.0: 1}}
    assert rep["delta_by_state"] == {"California": {-1.5: 1}, "North Carolina": {0.0: 1}}
    assert rep["calibrated"] == {"calibrated hours": 1, "calibrated hours down": 1}
    lb = rep["leaderboard"]
    assert lb["before"] == [{"spot": "A", "state": "California", "stars": 4.5}]
    assert lb["after"] == [{"spot": "B", "state": "North Carolina", "stars": 4.5}]
    assert lb["drop_out"] == [{"spot": "A", "stars_before": 4.5, "stars_after": 3.0}]
    assert lb["come_in"] == [{"spot": "B", "stars": 4.5}]


def test_live_explain():
    row = _nwps_row(swell_hs=1.0, wind_wave_hs=2.0, wind_wave_tp=5.0, wind_wave_dp=30.0, **LIGHT)
    x = SP.live_explain(row, SPOTS_V["A"], FACTORS3, 1.6)
    assert (x["star_cap"], round(x["closeout_face_ft"], 4), x["stars"], x["reproduced"]) == (
        5.0, 2.6667, 4.5, True)
    assert (x["period_s"], round(x["swh_m"], 5), x["shts_m"]) == (12.0, 2.12024, 1.0)
    assert x["partitions"]["swell_1"] == (2.0, 12.0, 270.0)
    assert x["partitions"]["wind_wave"] == (2.0, 5.0, 30.0)
    assert x["variants"] == {"cal-median": 3.0, "cal-all": 2.5, "swell-height": 3.0}


def test_calibration_keeps_the_live_ceiling_face():
    # Factor 0.75, M 1.6: sized on 8 x 1.6 / 0.75 = 17.07 ft (size 5.0); the ceiling still reads
    # the calibrated 8 / 0.75 = 10.667 ft and keeps (4.5 - 0.5 x 0.667) / 5 = 0.833333:
    # 5.0 x 0.833333 x 0.987259 = 4.114 -> 4.0. (Live: 4.5 x 0.833333 x 0.987259 = 3.702 -> 3.5.)
    spot = dict(WEST_BEACH, name="C")
    row = _nwps_row(**LIGHT)
    rb = SP.rescore(row, spot)
    factors = {"c": {"factor": 0.75}}
    assert SP.live_stars(row, spot, rb, factors) == 3.5
    assert SP.variant_stars(row, spot, rb, factors, "cal-median", 1.6) == 4.0


def test_the_baseline_is_the_live_rater():
    # Published 1.5 under the cap (cross-shore 24.3 kt at 12 s); the replay without rules is 4.5.
    rep = SP.variant_compare([_nwps_row(stars=1.5)], SPOTS_V, {1: ("A", "California")},
                             FACTORS3, "cal-median", 1.6)
    assert rep["replay"] == {"NWPS reproduced": 1}


def test_a_spot_whose_factor_is_below_the_median_goes_up_and_ties_keep_id_order():
    # A: factor 1.0, M 1.6 -> 12.8 ft, size 5.0, ceiling reads 8 ft: 5.0 x 0.987259 = 4.936 -> 5.0
    # B (no factor) and a second unchanged spot C tie at 4.5 and keep id order.
    spots = {"A": dict(WEST_BEACH, name="A"), "B": dict(WEST_REEF, name="B"),
             "C": dict(WEST_REEF, name="C")}
    rows = [_nwps_row(spot_id=3, **LIGHT), _nwps_row(spot_id=2, **LIGHT), _nwps_row(**LIGHT)]
    names = {1: ("A", "California"), 2: ("B", "Oregon"), 3: ("C", "Hawaii")}
    rep = SP.variant_compare(rows, spots, names, {"a": {"factor": 1.0}}, "cal-median", 1.6, n=3)
    assert rep["calibrated"] == {"calibrated hours": 1, "calibrated hours up": 1}
    assert [(r["spot"], r["stars"]) for r in rep["leaderboard"]["after"]] == [
        ("A", 5.0), ("B", 4.5), ("C", 4.5)]


def test_live_explain_reports_a_row_that_does_not_reproduce():
    x = SP.live_explain(_nwps_row(stars=2.0, **LIGHT), SPOTS_V["A"], FACTORS3, 1.6)
    assert (x["stars"], x["reproduced"]) == (4.5, False)


# --------------------------------------------------------------------------- #
# The cal-median merge gate                                                    #
# --------------------------------------------------------------------------- #

def test_the_cal_median_gate_on_nwps_rows():
    # A (factor 3.0): live 4.5, cal-median 3.0 both ways; B (no factor): 4.5 both ways.
    rows = [_nwps_row(**LIGHT), _nwps_row(spot_id=2, **LIGHT)]
    names = {1: ("A", "California"), 2: ("B", "North Carolina")}
    c = SP.cal_rater_check(rows, SPOTS_V, names, FACTORS3, 1.6)
    assert (c["hours"], c["same"], c["different"], c["examples"]) == (2, 2, 0, [])
    rb = SP.rescore(rows[0], SPOTS_V["A"])
    assert SP.wired_rater(rows[0], SPOTS_V["A"], rb, 3.0, 1.6) == 3.0


def test_the_gate_counts_a_mop_row_as_factor_one():
    # A MOP row on a reef, 3 ft, light offshore wind, period quality 1.0: live size(3) = 2.5.
    # On the yardstick (F = 1, M = 1.6): 4.8 ft, size 3.4 -> round(6.8)/2 = 3.5, both ways.
    row = _row(face_ft=3.0, wind_mult=1.0, stars=2.5, **LIGHT)
    rb = SP.rescore(row, WEST_REEF)
    assert SP.wired_rater(row, WEST_REEF, rb, None, 1.6) == 3.5
    c = SP.cal_rater_check([row], {"A": WEST_REEF}, NAMES, FACTORS3, 1.6)
    assert (c["same"], c["different"]) == (1, 0)


def test_the_cal_median_gate_reports_a_difference():
    row = _nwps_row(**LIGHT)
    real = SP.variant_stars
    SP.variant_stars = lambda *a, **k: 4.5
    try:
        c = SP.cal_rater_check([row], SPOTS_V, {1: ("A", "California")}, FACTORS3, 1.6)
    finally:
        SP.variant_stars = real
    assert c["different"] == 1
    assert c["examples"] == [{"spot": "A", "state": "California",
                              "valid_time": "2026-10-20T15:00:00+00:00", "published": 4.5,
                              "preview": 4.5, "wired": 3.0}]


def test_the_gate_skips_rows_the_live_rater_does_not_reproduce():
    c = SP.cal_rater_check([_nwps_row(stars=2.0, **LIGHT)], SPOTS_V, {1: ("A", "California")},
                           FACTORS3, 1.6)
    assert c["hours"] == 0
