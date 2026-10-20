"""The proposed strong-wind cap and beach-break ceiling (interpret.py), and their read-only
preview (star_preview --rating).

Every expected value is worked by hand, never read back from the functions under test:

  knots            m/s / 0.514444: 12.5 m/s = 24.298 kt, 13.4 m/s = 26.048 kt, 8 m/s = 15.551 kt;
                   6.687772 m/s = 13.0 kt and 12.8611 m/s = 25.0 kt exactly (13 and 25 x 0.514444)
  tolerance        max(0, period - 8) knots off the speed, every rung but "above 25 kt"
  ladder           onshore >13: 3.0, >15: 2.0, >20: 1.5, >25: 1.0
                   cross   >13: 3.5, >15: 2.5, >20: 2.0, >25: 1.0
  classes          angle off the offshore bearing: < 60 offshore, 60-120 cross, >= 120 onshore
  beach size       as size_score to 8 ft (4.5), 10 ft 4.5, 12 ft 3.5, 15 ft 2.5, 20 ft+ 1.5
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
    (12.5, 11.0, 2.0),     # 24.30 - 3 = 21.30 > 20: cross 2.0
    (12.5, 8.0, 2.0),      # 24.30 > 20: 2.0
    (12.5, 16.0, 2.5),     # 24.30 - 8 = 16.30 > 15: 2.5
    (12.5, 7.0, 2.0),      # period under 8 gives no negative tolerance
    (12.5, None, 2.0),     # no period: no tolerance
    (6.687772, 8.0, 5.0),  # exactly 13 kt is not above 13
    (12.8611, 8.0, 2.0),   # exactly 25 kt: not the top rung, > 20: 2.0
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


def test_offshore_is_never_capped_even_above_25_kt():
    assert I.wind_star_cap(245.0, 20.0, 245.0, 8.0) == 5.0


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


def test_the_beach_curve_replaces_size_score():
    # 11 ft on the beach curve: 4.0 x 0.924872 = 3.699 -> round(7.399)/2 = 3.5
    assert I.composite_stars(11.0, 0.8, 1.0, beach_break=True) == 3.5


def test_both_rules_and_the_floor():
    assert I.composite_stars(11.0, 0.8, 1.0, star_cap=1.0, beach_break=True) == 1.0


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
    ("ceiling", WEST_JETTY, (4.5, "")),
    ("both+jetty", WEST_JETTY, (2.5, "beach ceiling + wind cap")),
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
