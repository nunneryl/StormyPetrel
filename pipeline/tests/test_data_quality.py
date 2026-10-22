"""The read-only data-quality sweep (pipeline/data_quality.py): geometry, the grid-to-earth wind
rotation, the land/water search, matching a published hour to its HRRR step, the Swell
breakdown card rule, tidal range and the damped-station flag, and orphan-cam candidates.

Every expected value is worked by hand in the comment beside it; none is read back from the
function under test.
"""
from __future__ import annotations

import math

import pytest

from pipeline import data_quality as DQ


# --------------------------------------------------------------------------- #
# Geometry                                                                     #
# --------------------------------------------------------------------------- #

def test_haversine_one_degree_of_latitude():
    # R x pi / 180 = 6371.0088 x 0.01745329 = 111.195 km
    assert round(DQ.haversine_km(0.0, 0.0, 1.0, 0.0), 3) == 111.195


@pytest.mark.parametrize("to, want", [((1.0, 0.0), 0.0), ((0.0, 1.0), 90.0),
                                      ((-1.0, 0.0), 180.0), ((0.0, -1.0), 270.0)])
def test_bearing_on_the_equator(to, want):
    assert round(DQ.bearing_deg(0.0, 0.0, *to), 6) % 360.0 == want


@pytest.mark.parametrize("a, b, want", [(350, 10, 20), (10, 350, 20), (0, 180, 180), (90, 90, 0)])
def test_angle_off(a, b, want):
    assert DQ.angle_off(a, b) == want


@pytest.mark.parametrize("u, v, want", [(0.0, -1.0, 0.0),    # blowing south: from the north
                                        (-1.0, 0.0, 90.0),   # blowing west: from the east
                                        (0.0, 1.0, 180.0), (1.0, 0.0, 270.0)])
def test_met_dir(u, v, want):
    assert DQ.met_dir(u, v) == want


# --------------------------------------------------------------------------- #
# Grid-relative to earth-relative                                              #
# --------------------------------------------------------------------------- #

def test_the_x_axis_is_read_off_the_grid():
    # Rows of constant latitude, longitude rising with j: the x-axis points due east, 0 deg.
    lats = [[10.0, 10.0, 10.0]]
    lngs = [[0.0, 0.1, 0.2]]
    assert DQ.grid_x_axis_deg(lats, lngs, 0, 1) == 0.0
    # On the equator (cos 0 = 1), latitude and longitude rising together: 45 deg.
    lats = [[0.0, 0.01, 0.02]]
    lngs = [[0.0, 0.01, 0.02]]
    assert round(DQ.grid_x_axis_deg(lats, lngs, 0, 1), 6) == 45.0


def test_grid_to_earth_with_the_x_axis_pointing_north():
    # x-axis at 90 deg: a wind along +x blows toward true north, so it comes FROM the south.
    ue, ve = DQ.grid_to_earth(1.0, 0.0, 90.0)
    assert (round(ue, 9), round(ve, 9)) == (0.0, 1.0)
    assert DQ.met_dir(ue, ve) == 180.0


def test_the_new_jersey_turn():
    # At Atlantic City's cell the x-axis is -14.3 deg (14.3 deg clockwise of east). A grid wind
    # of u=1, v=0 reads "from 270" unrotated. Earth: (cos -14.3, sin -14.3) = (0.969016, -0.246999),
    # from atan2(-0.969016, 0.246999) = -75.70 deg = 284.30: 14.3 deg clockwise of the unrotated.
    ue, ve = DQ.grid_to_earth(1.0, 0.0, -14.3)
    assert (round(ue, 5), round(ve, 5)) == (0.96902, -0.24700)
    assert round(DQ.met_dir(ue, ve), 2) == 284.30


# --------------------------------------------------------------------------- #
# Land and water                                                               #
# --------------------------------------------------------------------------- #

def test_neighbour_land_share():
    mask = [[1, 0, 1],
            [0, 1, 0],
            [1, 0, 1]]
    assert DQ.neighbour_land_share(mask, 1, 1) == 5 / 9      # five land of nine
    assert DQ.neighbour_land_share(mask, 0, 0) == 2 / 4      # corner block [[1,0],[0,1]]: two of four


def test_the_nearest_water_cell_must_be_seaward():
    # Spot at (0, 0) facing east (90). Candidates: land just east, water 0.005 deg WEST (nearer,
    # but 180 deg off the orientation: a back bay), water 0.01 deg EAST.
    cands = [(1, 0.0, 0.002, 1.0), (2, 0.0, -0.005, 0.0), (3, 0.0, 0.01, 0.0)]
    best = DQ.nearest_seaward_water(0.0, 0.0, 90.0, cands)
    assert best[0] == 3 and round(best[4]) == 90
    # With no orientation the nearer water cell wins.
    assert DQ.nearest_seaward_water(0.0, 0.0, None, cands)[0] == 2
    assert DQ.nearest_seaward_water(0.0, 0.0, 90.0, [(1, 0.0, 0.002, 1.0)]) is None


def test_parse_idx():
    text = ("77:43043689:d=2026102206:UGRD:10 m above ground:anl:\n"
            "78:45187161:d=2026102206:VGRD:10 m above ground:anl:\n"
            "79:47000000:d=2026102206:TMP:2 m above ground:anl:\n")
    idx = DQ.parse_idx(text)
    assert idx[("UGRD", "10 m above ground")] == (43043689, 45187160)
    assert idx[("VGRD", "10 m above ground")] == (45187161, 46999999)
    assert idx[("TMP", "2 m above ground")] == (47000000, None)


def test_wind_summary():
    # Land 5 and 10 m/s, water 10 and 12 m/s, all along x. In knots (/0.514444): land 9.719,
    # 19.439; water 19.439, 23.326. Diffs 9.719 and 3.887: median 6.8. Ratios 2.0 and 1.2:
    # median 1.6. Water over 20 kt where land is not: the second hour (23.3 vs 19.4).
    land = [("t0", 5.0, 0.0), ("t1", 10.0, 0.0)]
    water = [("t0", 10.0, 0.0), ("t1", 12.0, 0.0)]
    assert DQ.wind_summary(land, water) == {
        "hours": 2, "median_land_kt": 14.6, "median_water_kt": 21.4, "median_diff_kt": 6.8,
        "median_ratio": 1.6, "max_diff_kt": 9.7, "hours_water_over_20_land_not": 1}


# --------------------------------------------------------------------------- #
# Matching a published hour to its HRRR step                                  #
# --------------------------------------------------------------------------- #

def _winds():
    # u = 3, v = 4: speed 5.000, from atan2(-3, -4) = -143.13 deg = 216.870 deg.
    return {"2026102206": {7: [("2026-10-22T06:00:00+00:00", 0.0, -1.0),
                               ("2026-10-22T07:00:00+00:00", 3.0, 4.0)]}}


def test_an_hour_matches_its_step_exactly():
    row = {"valid_time": "2026-10-22T07:00:00+00:00", "wind_speed": 5.0, "wind_dir": 216.87}
    assert DQ.match_cycle(row, 7, _winds()) == ("2026102206", 1, 3.0, 4.0)


@pytest.mark.parametrize("row", [
    {"valid_time": "2026-10-22T07:00:00+00:00", "wind_speed": 5.01, "wind_dir": 216.87},
    {"valid_time": "2026-10-22T07:00:00+00:00", "wind_speed": 5.0, "wind_dir": 217.0},
    {"valid_time": "2026-10-22T08:00:00+00:00", "wind_speed": 5.0, "wind_dir": 216.87},
    {"valid_time": "2026-10-22T07:00:00+00:00", "wind_speed": None, "wind_dir": 216.87},
])
def test_an_hour_that_is_not_hrrrs_matches_nothing(row):
    assert DQ.match_cycle(row, 7, _winds()) is None


def test_the_true_north_variant_turns_the_same_cell():
    # x-axis -14.3: (1, 0) grid becomes from 284.30 at the same 1 m/s (see the NJ test).
    cell = {"x_axis_deg": -14.3}
    speed, direction = DQ.variant_wind("true-north", cell, {}, "c", 0, 1.0, 0.0)
    assert (round(speed, 9), round(direction, 2)) == (1.0, 284.30)
    assert DQ.variant_wind("water", cell, {}, "c", 0, 1.0, 0.0) is None   # no water cell


def test_the_water_variants_read_the_water_cell():
    cell = {"x_axis_deg": 0.0, "water_flat": 9, "water_x_axis_deg": 90.0}
    winds = {"c": {9: [("t", 0.0, -2.0)]}}                    # 2 m/s from the north, grid
    assert DQ.variant_wind("water", cell, winds, "c", 0, 1.0, 0.0) == (2.0, 0.0)
    # Turned by a 90 deg x-axis: grid (0, -2) -> earth (2, 0), blowing east: from 270.
    s, d = DQ.variant_wind("water+true-north", cell, winds, "c", 0, 1.0, 0.0)
    assert (round(s, 9), round(d, 6)) == (2.0, 270.0)


# --------------------------------------------------------------------------- #
# The Swell breakdown card                                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("hs, shown", [
    ({}, False),                                                  # nothing
    ({"swell_1_hs": 1.2}, False),                                 # one swell only
    ({"swell_1_hs": 1.2, "wind_wave_hs": 0.3}, True),             # one swell and a wind sea
    ({"swell_1_hs": 1.2, "swell_2_hs": 0.4}, True),               # two swells
    ({"swell_1_hs": 1.2, "swell_2_hs": 0.05}, False),             # 0.05 is not above 0.05
    ({"wind_wave_hs": 0.6}, False),                               # a wind sea alone
])
def test_the_card_rule_is_swellpartitions_tsx(hs, shown):
    assert DQ.card_shown(hs) is shown


# --------------------------------------------------------------------------- #
# Tides                                                                        #
# --------------------------------------------------------------------------- #

def test_mean_range():
    # H 3.0, L 0.5, H 3.2: 2.5 and 2.7, mean 2.6. A repeated type is not a range.
    hilo = [{"type": "H", "v": "3.0"}, {"type": "L", "v": "0.5"}, {"type": "H", "v": "3.2"}]
    assert DQ.mean_range(hilo) == 2.6
    assert DQ.mean_range([{"type": "H", "v": "3.0"}, {"type": "HH", "v": "3.1"}]) is None
    assert DQ.mean_range([]) is None


def test_a_damped_station_is_flagged_and_the_nearest_near_widest_is_proposed():
    nearby = [("a", "Lagoon", 2.0, 0.7), ("b", "Ocean", 10.0, 3.5), ("c", "Ocean 2", 20.0, 3.4)]
    # 0.7 < 0.6 x 3.5 = 2.1: flagged. Near-widest (>= 0.8 x 3.5 = 2.8): b at 10 km, c at 20 km.
    flag, widest, proposed = DQ.damped(0.7, nearby)
    assert flag and widest[0] == "b" and proposed[0] == "b"
    # 2.2 >= 2.1: not flagged.
    assert DQ.damped(2.2, nearby)[0] is False
    assert DQ.damped(None, nearby) == (False, None, None)


# --------------------------------------------------------------------------- #
# Orphan cams                                                                  #
# --------------------------------------------------------------------------- #

def test_cam_candidates():
    roster = ["Daytona Beach", "New Smyrna Beach", "Ponce Inlet"]
    seed = [{"cam_name": "Daytona Beach Live", "spot_slug": "daytona-beach"},
            {"cam_name": "Old Cam", "spot_slug": "gone-spot"}]
    c = DQ.cam_candidates({"cam_name": "Daytona Beach Live", "attribution": "Volusia County"},
                          roster, seed)
    assert c["seed"] == [{"spot_slug": "daytona-beach", "on_roster": True}]
    assert c["by_name"][0] == {"spot": "Daytona Beach", "slug": "daytona-beach", "shared": 1}
    c = DQ.cam_candidates({"cam_name": "Old Cam"}, roster, seed)
    assert c["seed"] == [{"spot_slug": "gone-spot", "on_roster": False}] and c["by_name"] == []


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def test_the_cli_refuses_an_unknown_section():
    with pytest.raises(SystemExit):
        DQ.main(["--sections", "everything"])


def test_the_wind_preview_needs_the_hrrr_section():
    with pytest.raises(SystemExit):
        DQ.main(["--sections", "wind-preview"])


def test_the_true_north_gate_needs_the_hrrr_section():
    with pytest.raises(SystemExit):
        DQ.main(["--sections", "true-north"])


# --------------------------------------------------------------------------- #
# The true-north gate                                                          #
# --------------------------------------------------------------------------- #
# Atlantic City's cell, x-axis -14.3, grid wind (0, -5): unrotated "from 0", true from 14.3 at
# 5 m/s (worked in test_hrrr_true_north: ue = -1.234995, ve = -4.845080).
GATE_CELLS = [{"spot": "A", "flat": 7, "x_axis_deg": -14.3}]
GATE_SWEEP = {7: [("2026-10-22T06:00:00+00:00", 0.0, -5.0)]}
GATE_META = {"grid_relative": True, "cells": {"A": 7}}


def _published(wind_dir=14.3, wind_speed=5.0):
    return {"A": [{"valid_time": "2026-10-22T06:00:00Z", "wind_speed": wind_speed,
                   "wind_dir": wind_dir}]}


def test_the_gate_passes_when_the_pipeline_turns_the_wind_as_the_sweep_does():
    rep = DQ.compare_true_north(GATE_CELLS, GATE_SWEEP, _published(), GATE_META)
    assert rep["verdict"] == "PASS"
    assert (rep["spot_hours"], rep["differences"], rep["missing"], rep["cell_moved"]) == (1, [], [], [])


def test_the_gate_fails_on_the_unrotated_direction():
    rep = DQ.compare_true_north(GATE_CELLS, GATE_SWEEP, _published(wind_dir=0.0), GATE_META)
    assert rep["verdict"] == "FAIL"
    assert len(rep["differences"]) == 1
    assert round(rep["max_dir_diff"], 3) == 14.3


@pytest.mark.parametrize("wind_dir, wind_speed, verdict", [
    (14.301, 5.0, "PASS"),     # 0.001 apart: within the 3-decimal storage tolerance (0.0015)
    (14.302, 5.0, "FAIL"),     # 0.002 apart
    (14.3, 5.002, "FAIL"),     # the speed 0.002 apart
])
def test_the_gate_tolerance(wind_dir, wind_speed, verdict):
    rep = DQ.compare_true_north(GATE_CELLS, GATE_SWEEP, _published(wind_dir, wind_speed),
                                GATE_META)
    assert rep["verdict"] == verdict


def test_the_gate_fails_when_a_spot_reads_another_cell():
    rep = DQ.compare_true_north(GATE_CELLS, GATE_SWEEP, _published(),
                                {"grid_relative": True, "cells": {"A": 8}})
    assert (rep["verdict"], rep["cell_moved"]) == ("FAIL", ["A"])


def test_the_gate_fails_on_a_missing_hour():
    # 06Z published and matching, 07Z (same grid wind) never published.
    sweep = {7: GATE_SWEEP[7] + [("2026-10-22T07:00:00+00:00", 0.0, -5.0)]}
    rep = DQ.compare_true_north(GATE_CELLS, sweep, _published(), GATE_META)
    assert (rep["verdict"], rep["spot_hours"], rep["differences"]) == ("FAIL", 1, [])
    assert rep["missing"] == [["A", "2026-10-22T07:00:00+00:00"]]


def test_the_gate_fails_when_the_files_were_not_read_as_grid_relative():
    rep = DQ.compare_true_north(GATE_CELLS, GATE_SWEEP, _published(),
                                {"grid_relative": False, "cells": {"A": 7}})
    assert rep["verdict"] == "FAIL"


# A land-cell spot: its cell 7, water cell 9 with x-axis +15.3 and grid wind (0, -5) there:
# from 344.7 at 5 m/s (worked in test_hrrr_true_north's California test).
WATER_CELLS = [{"spot": "A", "flat": 7, "x_axis_deg": -14.3, "water_flat": 9,
                "water_x_axis_deg": 15.3}]
WATER_SWEEP = {7: [("2026-10-22T06:00:00+00:00", 0.0, -1.0)],
               9: [("2026-10-22T06:00:00+00:00", 0.0, -5.0)]}


def test_the_gate_follows_the_water_cell():
    rep = DQ.compare_true_north(WATER_CELLS, WATER_SWEEP, _published(wind_dir=344.7),
                                {"grid_relative": True, "cells": {"A": 9}, "water": {"A": [7, 9]}})
    assert (rep["verdict"], rep["spot_hours"], rep["water_cells"]) == ("PASS", 1, 1)


def test_the_gate_fails_when_the_pipeline_keeps_the_land_cell():
    # The land cell turned: (0, -1) at -14.3 is from 14.3 at 1 m/s; the cell is 7, not 9.
    rep = DQ.compare_true_north(WATER_CELLS, WATER_SWEEP, _published(wind_dir=14.3, wind_speed=1.0),
                                {"grid_relative": True, "cells": {"A": 7}, "water": {}})
    assert (rep["verdict"], rep["cell_moved"], len(rep["differences"])) == ("FAIL", ["A"], 1)


@pytest.mark.parametrize("wind_dir, wind_speed, frame", [
    (0.0, 1.0, "grid"),                    # the land cell's (0, -1) unturned: from 0
    (14.3, 1.0, "true-north"),             # turned at -14.3: from 14.3
    (344.7, 5.0, "water+true-north"),      # the water cell's (0, -5) turned at +15.3
])
def test_a_published_hour_is_matched_in_whichever_frame_it_was_published(wind_dir, wind_speed, frame):
    row = {"valid_time": "2026-10-22T06:00:00+00:00", "wind_speed": wind_speed,
           "wind_dir": wind_dir}
    assert DQ.match_published(row, WATER_CELLS[0], {"c": WATER_SWEEP}) == ("c", 0, frame)


def test_an_hour_in_no_frame_is_not_matched():
    row = {"valid_time": "2026-10-22T06:00:00+00:00", "wind_speed": 5.0, "wind_dir": 0.0}
    assert DQ.match_published(row, WATER_CELLS[0], {"c": WATER_SWEEP}) is None


def test_the_gate_fails_with_nothing_compared():
    rep = DQ.compare_true_north([], GATE_SWEEP, _published(), GATE_META)
    assert (rep["verdict"], rep["spot_hours"]) == ("FAIL", 0)


# --------------------------------------------------------------------------- #
# Re-rating a published HRRR hour                                              #
# --------------------------------------------------------------------------- #
# The NWPS row of test_wind_proposal at a west-facing sand beach (offshore bearing 90): face
# 8 ft at 12 s, uncapped 4.5. The published wind is HRRR's land cell: 2 m/s from 90 (u -2,
# v 0), light and offshore. Its water cell reads 11.0 m/s from 0 (u 0, v -11): 21.382 kt,
# 90 deg off the offshore bearing, cross-shore, over 20 kt measured, so the live cap is 1.5.

def _preview_fixture():
    from pipeline.interpret import wind_multiplier
    from pipeline.star_preview import rescore, wired_rater
    spot = {"name": "B", "swell_window_arcs": [{"min": 250, "max": 290, "span": 40}],
            "optimal_swell_dir": 270, "orientation_deg": 270.0, "offshore_wind_deg": 90.0,
            "break_type": "beach", "bottom": "sand"}
    vt = "2099-01-01T06:00:00+00:00"
    row = {"swell_source": "nwps_height_ww3_dir", "face_ft": 8.0, "face_ft_raw": None,
           "swell_tp": 12.0, "dp": 300.0, "tp": 12.0, "swell_1_hs": 2.0, "swell_1_tp": 12.0,
           "swell_1_dp": 270.0, "tide_mult": 1.0, "chop_mult": 1.0, "chop_ratio": None,
           "spot_id": 2, "valid_time": vt, "wind_dir": 90.0, "wind_speed": 2.0}
    row["wind_mult"] = round(wind_multiplier(90.0, 2.0, 90.0, None), 3)
    row["stars"] = wired_rater(row, spot, rescore(row, spot), None, None)   # the fixture's own
    cells = [{"spot": "B", "flat": 5, "x_axis_deg": 0.0, "water_flat": 6,
              "water_x_axis_deg": 0.0}]
    winds = {"c": {5: [(vt, -2.0, 0.0)], 6: [(vt, 0.0, -11.0)]}}
    return spot, row, cells, winds


def test_a_published_hrrr_hour_is_re_rated_with_the_water_cell_wind():
    spot, row, cells, winds = _preview_fixture()
    now = DQ._iso("2099-01-01T00:00:00+00:00")
    rep = DQ.wind_preview([row], {"B": spot}, {2: ("B", "North Carolina")}, None, None, cells,
                          winds, now, n=1, focus=["B"])
    assert rep["replay"] == {"HRRR, reproduced": 1}
    water = rep["variants"]["water"]
    assert (water["compared"], water["changed"], water["downs"], water["wind_class_changed"]) == \
        (1, 1, 1, 1)                                            # offshore -> cross
    assert water["focus"]["B"][0]["after"] == 1.5               # the 21.4 kt cross-shore cap
    assert water["homepage_after"] == [("B", 1.5)]
    # An x-axis of 0 deg turns nothing: the true-north variant changes no hour.
    assert rep["variants"]["true-north"]["changed"] == 0


def test_an_hour_whose_wind_is_not_hrrrs_is_not_re_rated():
    spot, row, cells, winds = _preview_fixture()
    row = dict(row, wind_speed=2.5)                             # NWPS wind, say: no step matches
    rep = DQ.wind_preview([row], {"B": spot}, {2: ("B", "North Carolina")}, None, None, cells,
                          winds, DQ._iso("2099-01-01T00:00:00+00:00"))
    assert rep["replay"] == {"not HRRR (or an unfetched cycle)": 1}
    assert rep["variants"]["water"]["compared"] == 0


def test_the_damped_threshold_is_sixty_percent_of_the_widest():
    nearby = [("b", "Ocean", 10.0, 3.5)]
    # 1.5 < 0.6 x 3.5 = 2.1: flagged (it would not be at 30%: 1.5 >= 1.05).
    assert DQ.damped(1.5, nearby)[0] is True


def test_the_proposal_is_the_nearest_station_near_the_widest_not_the_widest():
    # Widest is b at 25 km (3.5 ft); c at 8 km has 3.0 >= 0.8 x 3.5 = 2.8, and is nearer.
    nearby = [("a", "Lagoon", 2.0, 0.7), ("b", "Ocean", 25.0, 3.5), ("c", "Ocean 2", 8.0, 3.0)]
    flag, widest, proposed = DQ.damped(0.7, nearby)
    assert flag and widest[0] == "b" and proposed[0] == "c"


def test_the_wind_score_is_recomputed_for_the_new_wind_not_kept():
    # Water cell 4.5 m/s from 270: onshore (180 deg off 90) at 8.7 kt, under every cap rung, so
    # only the wind score can move the stars. An onshore score is below the light offshore one
    # the hour was published with, so the stars fall from the published 4.5.
    spot, row, cells, winds = _preview_fixture()
    winds = {"c": {5: winds["c"][5], 6: [(row["valid_time"], 4.5, 0.0)]}}
    rep = DQ.wind_preview([row], {"B": spot}, {2: ("B", "North Carolina")}, None, None, cells,
                          winds, DQ._iso("2099-01-01T00:00:00+00:00"), focus=["B"])
    assert row["stars"] == 4.5
    after = rep["variants"]["water"]["focus"]["B"][0]["after"]
    assert after < 4.5, after


# --------------------------------------------------------------------------- #
# Cross-offshore capped like offshore (true-north+cap90)                        #
# --------------------------------------------------------------------------- #
# Offshore bearing 90. From 20 deg is 70 off (cross-offshore), from 350 is 100 off
# (cross-onshore), from 300 is 150 off (onshore). 11.0 m/s = 21.382 kt, 13.4 = 26.048 kt,
# 10.2 = 19.827 kt.

@pytest.mark.parametrize("wind_dir, speed, period, want", [
    (20.0, 11.0, 12.0, 3.0),    # 70 off, > 20 kt: the offshore rung (#270: cross, 1.5)
    (20.0, 13.4, 12.0, 2.0),    # 70 off, > 25 kt: offshore 2.0 (#270: 1.0)
    (20.0, 10.2, 8.0, 5.0),     # 70 off, <= 20 kt: offshore has no lower rung (#270: 2.5)
    (1.0, 11.0, 12.0, 3.0),     # 89 off: still under 90, the offshore rung
    (0.0, 11.0, 12.0, 1.5),     # 90 off exactly: cross-shore ladder, > 20 kt measured, 1.5
    (65.0, 11.0, 12.0, 3.0),    # 25 off: offshore, as #270
    (350.0, 11.0, 12.0, 1.5),   # 100 off: cross-shore ladder, as #270
    (300.0, 10.2, 16.0, 5.0),   # 150 off, 19.83 - 8 = 11.83 kt: no onshore rung, as #270
    (None, 11.0, 12.0, 5.0),
])
def test_cap90(wind_dir, speed, period, want):
    from pipeline.interpret import wind_star_cap
    assert DQ.cap90_star_cap(wind_dir, speed, 90.0, period) == want
    assert wind_star_cap(wind_dir, speed, 90.0, period) == want      # wired


def _cap90_fixture():
    # Published: 11.0 m/s from 20 deg grid (u = -11 sin 20 = -3.7622, v = -11 cos 20 = -10.3366),
    # 70 off: cross-shore over 20 kt (21.382 kt), so the published (#270) cap is 1.5; the
    # uncapped 4.443 x 0.8**0.35 (0.924872) = 4.109 -> 4.0 is above it: published 1.5 stars.
    from pipeline.interpret import wind_multiplier
    spot, row, cells, winds = _preview_fixture()
    u, v = -11.0 * math.sin(math.radians(20.0)), -11.0 * math.cos(math.radians(20.0))
    ws, wd = round(math.hypot(u, v), 3), round(DQ.met_dir(u, v), 3)
    row = dict(row, wind_speed=ws, wind_dir=wd, stars=1.5,
               wind_mult=round(wind_multiplier(wd, ws, 90.0, None), 3))
    winds = {"c": {5: [(row["valid_time"], u, v)], 6: winds["c"][6]}}
    return spot, row, cells, winds


def test_the_published_cap_is_270s_and_the_wired_cap_is_cap90():
    # 70 off at 21.382 kt: #270 reads it cross-shore over 20 kt, 1.5; the wired cap counts
    # 60-90 off as offshore, over 20 kt, 3.0.
    _, row, _, _ = _cap90_fixture()
    args = (row["wind_dir"], row["wind_speed"], 90.0, 12.0)
    assert DQ.published_wind_cap(*args) == 1.5
    from pipeline.interpret import wind_star_cap
    assert wind_star_cap(*args) == 3.0


def test_the_cap90_variant_is_the_wired_rater_and_the_gate_passes():
    # An x-axis of 0 turns nothing, so the true-north variant (published cap) changes no hour,
    # and the wired rater lifts the cap to 3.0: 4.0 uncapped, so 3.0.
    spot, row, cells, winds = _cap90_fixture()
    rep = DQ.wind_preview([row], {"B": spot}, {2: ("B", "North Carolina")}, None, None, cells,
                          winds, DQ._iso("2099-01-01T00:00:00+00:00"), focus=["B"])
    assert rep["replay"] == {"HRRR, reproduced": 1}     # no cap90 gate or construction mismatch
    assert rep["variants"]["true-north"]["changed"] == 0
    cap90 = rep["variants"]["true-north+cap90"]
    assert cap90["changed"] == 1 and cap90["focus"]["B"][0]["after"] == 3.0


def test_an_hour_published_on_the_wired_cap_is_reproduced_on_it():
    # The same hour published after the cap90 rule: 3.0 (wired). It reproduces on the wired cap,
    # and the true-north variant (x-axis 0, #270 cap) re-rates it 1.5.
    spot, row, cells, winds = _cap90_fixture()
    row = dict(row, stars=3.0)
    rep = DQ.wind_preview([row], {"B": spot}, {2: ("B", "North Carolina")}, None, None, cells,
                          winds, DQ._iso("2099-01-01T00:00:00+00:00"), focus=["B"])
    assert rep["replay"] == {"HRRR, reproduced": 1}
    assert rep["published_frames"] == {"grid": 1}
    assert rep["variants"]["true-north"]["focus"]["B"][0]["after"] == 1.5
    assert rep["variants"]["true-north+cap90"]["changed"] == 0


def test_an_hour_published_on_the_water_cell_is_re_rated_from_the_spots_own_cell():
    # Published from the water cell (x-axis 0): 11.0 m/s from 0, 90 off, cross-shore over 20 kt,
    # 1.5 on either cap (4.0 uncapped). The true-north variant is the spot's OWN cell turned:
    # 2 m/s from 90, light offshore, back to the fixture's 4.5.
    from pipeline.interpret import wind_multiplier
    spot, row, cells, winds = _preview_fixture()
    row = dict(row, wind_speed=11.0, wind_dir=0.0, stars=1.5,
               wind_mult=round(wind_multiplier(0.0, 11.0, 90.0, None), 3))
    rep = DQ.wind_preview([row], {"B": spot}, {2: ("B", "North Carolina")}, None, None, cells,
                          winds, DQ._iso("2099-01-01T00:00:00+00:00"), focus=["B"])
    assert rep["replay"] == {"HRRR, reproduced": 1}
    assert rep["published_frames"] == {"water+true-north": 1}
    assert rep["variants"]["true-north"]["focus"]["B"][0]["after"] == 4.5


def test_water_plus_true_north_is_on_the_wired_rater():
    # The water cell's wind from 20 deg at 11.0 m/s (21.382 kt), 70 off: #270 caps it 1.5 (the
    # water variant), the wired cap 3.0 (water+true-north; x-axis 0 turns nothing). Uncapped
    # 4.443 x 0.8**0.35 = 4.109 -> 4.0, so 1.5 and 3.0. Published 4.5 (light offshore).
    import math as _m
    spot, row, cells, winds = _preview_fixture()
    u, v = -11.0 * _m.sin(_m.radians(20.0)), -11.0 * _m.cos(_m.radians(20.0))
    winds = {"c": {5: winds["c"][5], 6: [(row["valid_time"], u, v)]}}
    rep = DQ.wind_preview([row], {"B": spot}, {2: ("B", "North Carolina")}, None, None, cells,
                          winds, DQ._iso("2099-01-01T00:00:00+00:00"), focus=["B"])
    assert rep["variants"]["water"]["focus"]["B"][0]["after"] == 1.5
    assert rep["variants"]["water+true-north"]["focus"]["B"][0]["after"] == 3.0


def test_the_cap90_gate_counts_a_wired_rater_that_differs_from_the_rule(monkeypatch):
    # The rule built here held at the #270 cap (1.5) while the wired rater gives 3.0.
    spot, row, cells, winds = _cap90_fixture()
    monkeypatch.setattr(DQ, "cap90_star_cap", DQ.published_wind_cap)
    rep = DQ.wind_preview([row], {"B": spot}, {2: ("B", "North Carolina")}, None, None, cells,
                          winds, DQ._iso("2099-01-01T00:00:00+00:00"))
    assert rep["replay"] == {"HRRR, reproduced": 1, "cap90 gate: DIFFERENT (must be 0)": 1}


@pytest.mark.parametrize("d", range(0, 360, 5))
def test_the_wired_cap_is_the_cap90_rule_everywhere(d):
    """A property over a grid, not an expected value: every direction in 5 deg steps, 0-30 kt
    in quarter knots, periods 6-18 s, offshore bearing 245. The wired cap and the rule built
    here independently agree everywhere, and the rule never lowers the #270 cap."""
    from pipeline.interpret import wind_star_cap
    for q in range(0, 121):
        ms = q / 4 * 0.514444
        for tp in (6.0, 8.0, 10.0, 12.0, 14.0, 16.0, 18.0):
            rule = DQ.cap90_star_cap(float(d), ms, 245.0, tp)
            assert wind_star_cap(float(d), ms, 245.0, tp) == rule, (d, q, tp)
            assert rule >= DQ.published_wind_cap(float(d), ms, 245.0, tp), (d, q, tp)


def test_class_moves_are_counted_by_kind():
    # The water-cell fixture: light offshore (from 90) -> 21.4 kt from 0, 90 off: cross.
    spot, row, cells, winds = _preview_fixture()
    rep = DQ.wind_preview([row], {"B": spot}, {2: ("B", "North Carolina")}, None, None, cells,
                          winds, DQ._iso("2099-01-01T00:00:00+00:00"))
    assert rep["variants"]["water"]["class_moves"] == {"offshore -> cross": 1}
