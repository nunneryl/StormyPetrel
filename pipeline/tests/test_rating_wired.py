"""The strong-wind cap and the sand beach-break ceiling, wired into all three raters.

rate_spot, apply_nwps_overrides (through nwps_stars) and apply_mop_overrides (through
mop_stars) each apply interpret.rule_keywords. Every expected value is worked by hand:

  M_TO_FT 3.281; period_factor at 12 s: NWPS table 1.5, WW3 table 1.15
  rate_spot, NWPS path   2.0 m x 1.5 x 3.281 = 9.843 ft, gain 1.0 (270 on a 250-290 window
                         aimed at 270): size 4.5 + 0.5 x 1.843 / 2 = 4.96075
  NWPS override          3.0 m x 1.15 x 3.281 = 11.31945 ft (nwps_stars rates on the WW3
                         table): size 5.0; beach curve 4.5 - 0.5 x 1.31945 = 3.840275
  MOP override           3.5 m x 3.281 = 11.4835 ft (no period factor): size 5.0; beach
                         curve 4.5 - 0.5 x 1.4835 = 3.75825
  chop                   swell height = total height: ratio 0, multiplier 1.0
  period 12 s            quality 0.95, 0.95**0.25 = 0.987259
  wind, offshore 90      from 0 at 12.5 m/s: 90 off, cross, 0.8 -> 0.8**0.35 = 0.924872;
                         24.30 kt - (12 - 8) = 20.30 > 20: cap 1.5
                         from 90 at 2.0 m/s: offshore, 1.2 blended 2/5 of the way from 1.0:
                         1.08 -> 1.08**0.35 = 1.027302; 3.89 kt, no cap
"""
from __future__ import annotations

from pipeline import interpret as I
from pipeline.forecast import mop
from pipeline.forecast import nwps_nearshore as nn

BASE = 1767225600                      # 2026-01-01T00:00:00Z
HR = BASE // 3600
ISO = "2026-01-01T00:00:00Z"
CROSS = {"wind_dir": 0.0, "wind_speed": 12.5}
LIGHT_OFFSHORE = {"wind_dir": 90.0, "wind_speed": 2.0}
BEACH = {"break_type": "beach", "bottom": "sand"}
JETTY = {"break_type": "jetty", "bottom": "sand"}
REEF = {"break_type": "reef", "bottom": "rock"}


def _spot(kind=REEF, **kw):
    s = {"name": "T", "lat": 34.0, "lng": -118.5, "orientation_deg": 270.0,
         "offshore_wind_deg": 90.0, "optimal_swell_dir": 270,
         "swell_window_arcs": [{"min": 250, "max": 290, "span": 40}], **kind}
    s.update(kw)
    return s


# --------------------------------------------------------------------------- #
# rate_spot                                                                    #
# --------------------------------------------------------------------------- #

def _rate(spot, wind, face_factor=None):
    entry = {"valid_time": ISO, "hs": 2.0, "swell_hs": 2.0, "tp": 12.0, "swell_tp": 12.0,
             "dp": 270.0, "swell_dp": 270.0, **wind}
    (row,) = I.rate_spot(spot, [entry], None, face_factor=face_factor)
    return row


def test_rate_spot_without_the_rules_would_give_4_5():
    # 4.96075 x 0.924872 x 0.987259 = 4.5296 -> round(9.059)/2 = 4.5, which the cap replaces
    row = _rate(_spot(), CROSS)
    assert (row["face_ft"], row["effective_size_ft"]) == (9.84, 9.84)
    assert I.composite_stars(9.843, 0.8, 1.0, 1.0, 0.95) == 4.5


def test_rate_spot_caps_a_cross_shore_gale():
    assert _rate(_spot(), CROSS)["stars"] == 1.5


def test_rate_spot_reef_in_light_offshore_wind_is_untouched():
    # 4.96075 x 1.027302 x 0.987259 = 5.0313 -> 5.0
    assert _rate(_spot(), LIGHT_OFFSHORE)["stars"] == 5.0


def test_rate_spot_sand_beach_break_hits_the_ceiling():
    # closeout(9.843) = 4.5 / 4.96075: 4.5 x 1.027302 x 0.987259 = 4.5640 -> round(9.128)/2
    assert _rate(_spot(BEACH), LIGHT_OFFSHORE)["stars"] == 4.5


def test_rate_spot_ceiling_reads_the_calibrated_face():
    # 9.843 / 1.5 = 6.562 ft, under 8: the ceiling keeps all of it, 5.0 as the reef
    assert _rate(_spot(BEACH), LIGHT_OFFSHORE, face_factor=1.5)["stars"] == 5.0


def test_rate_spot_jetty_over_sand_is_on_the_ceiling():
    assert _rate(_spot(JETTY), LIGHT_OFFSHORE)["stars"] == 4.5


def test_compute_ratings_passes_each_spot_its_factor():
    spot = _spot(BEACH)
    forecast = [{"valid_time": ISO, "hs": 2.0, "swell_hs": 2.0, "tp": 12.0, "swell_tp": 12.0,
                 "dp": 270.0, "swell_dp": 270.0, **LIGHT_OFFSHORE}]
    plain = I.compute_ratings([spot], {"T": forecast}, {})
    calibrated = I.compute_ratings([spot], {"T": forecast}, {}, ceiling_factors={"T": 1.5})
    assert (plain["T"][0]["stars"], calibrated["T"][0]["stars"]) == (4.5, 5.0)


def test_face_factors_by_name_follows_the_correction_seam():
    spots = [{"name": "Doran Beach", "swell_window_source": "nwps"},
             {"name": "Humboldt Bay Jetty", "swell_window_source": "cdip_mop"},
             {"name": "Avon Pier", "swell_window_source": "nwps"}]
    factors = {"doran-beach": {"factor": 2.8212}, "humboldt-bay-jetty": {"factor": 1.2}}
    assert I.face_factors_by_name(spots, factors) == {
        "Doran Beach": 2.8212, "Humboldt Bay Jetty": None, "Avon Pier": None}


# --------------------------------------------------------------------------- #
# The NWPS override                                                            #
# --------------------------------------------------------------------------- #

def _nwps(kind, wind, ceiling_factors=None):
    spot = _spot(kind, swell_window_source="nwps", nwps_wfo="lox")
    e = {"valid_time": ISO, "stars": 1.0, "wind_mult": 1.0, "tide_mult": 1.0,
         "swell_source": "ww3", "swell_dp": 270.0, "swell_tp": 12.0, **wind}
    nn.apply_nwps_overrides({"T": [e]}, [spot], _fetch=lambda _s: {HR: (3.0, 12.0, 270.0, 3.0)},
                            ceiling_factors=ceiling_factors)
    return e


def test_nwps_override_caps_a_cross_shore_gale():
    # without the cap: 5.0 x 0.924872 x 0.987259 = 4.565 -> 4.5
    e = _nwps(REEF, CROSS)
    assert (e["face_ft"], e["wind_mult"], e["stars"]) == (11.32, 0.8, 1.5)


def test_nwps_override_reef_in_light_offshore_wind():
    # 5.0 x 1.027302 x 0.987259 = 5.0711 -> 5.0
    assert _nwps(REEF, LIGHT_OFFSHORE)["stars"] == 5.0


def test_nwps_override_sand_beach_ceiling():
    # 3.840275 x 1.027302 x 0.987259 = 3.8949 -> round(7.790)/2 = 4.0
    assert _nwps(BEACH, LIGHT_OFFSHORE)["stars"] == 4.0


def test_nwps_override_ceiling_reads_the_calibrated_face():
    # 11.31945 / 1.5 = 7.5463 ft, under 8: 5.0 as the reef
    assert _nwps(BEACH, LIGHT_OFFSHORE, ceiling_factors={"T": 1.5})["stars"] == 5.0


def test_nwps_stars_without_a_spot_applies_neither_rule():
    # the replay of rows published before the rules: 4.565 -> 4.5 in a cross-shore gale
    stars = nn.nwps_stars(3.0, 12.0, 270.0, 3.0, 270.0, 0.8, 1.0, optimal=270)[0]
    assert stars == 4.5


# --------------------------------------------------------------------------- #
# The MOP override                                                             #
# --------------------------------------------------------------------------- #

def _mop(kind, wind):
    spot = _spot(kind, swell_window_source="cdip_mop", mop_shore_normal=270.0)
    e = {"valid_time": ISO, "stars": 1.0, "wind_mult": 1.0, "tide_mult": 1.0, **wind}
    mop.apply_mop_overrides({"T": [e]}, [spot], _fetch=lambda _s: {HR: (3.5, 12.0, 270.0, 3.5)})
    return e


def test_mop_override_caps_a_cross_shore_gale():
    # without the cap: 5.0 x 0.924872 x 0.987259 = 4.565 -> 4.5
    e = _mop(REEF, CROSS)
    assert (e["face_ft"], e["stars"]) == (11.48, 1.5)


def test_mop_override_sand_beach_ceiling_on_the_raw_face():
    # 3.75825 x 1.027302 x 0.987259 = 3.8117 -> round(7.623)/2 = 4.0; the MOP tier has no factor
    assert _mop(BEACH, LIGHT_OFFSHORE)["stars"] == 4.0


def test_mop_override_reef_in_light_offshore_wind():
    assert _mop(REEF, LIGHT_OFFSHORE)["stars"] == 5.0


def test_mop_stars_without_a_spot_applies_neither_rule():
    assert mop.mop_stars(3.5, 12.0, 270.0, 3.5, 270.0, 0.8, 1.0)[0] == 4.5


# --------------------------------------------------------------------------- #
# rule_keywords                                                                #
# --------------------------------------------------------------------------- #

def test_rule_keywords():
    assert I.rule_keywords(_spot(BEACH), 0.0, 12.5, 12.0, 11.0, 1.1) == {
        "star_cap": 1.5, "closeout_face_ft": 10.0}
    assert I.rule_keywords(_spot(REEF), 90.0, 2.0, 12.0, 11.0, 1.1) == {
        "star_cap": 5.0, "closeout_face_ft": None}
    assert I.rule_keywords(_spot(BEACH), None, None, None, 11.0) == {
        "star_cap": 5.0, "closeout_face_ft": 11.0}


# --------------------------------------------------------------------------- #
# The period allowance reaches every rater; the bottom matters                 #
# --------------------------------------------------------------------------- #
# Cross-shore at 8.0 m/s = 15.55 kt: over 15, cap 2.5, at 8 s; at 12 s it is read at
# 15.55 - 4 = 11.55 kt, under 13, so no cap. wind_mult stays 0.8 (5-15 m/s).
MODERATE_CROSS = {"wind_dir": 0.0, "wind_speed": 8.0}


def test_rate_spot_reads_the_period_allowance():
    # 4.96075 x 0.924872 x 0.987259 = 4.5296 -> 4.5, uncapped
    assert _rate(_spot(), MODERATE_CROSS)["stars"] == 4.5


def test_nwps_override_reads_the_period_allowance():
    # 5.0 x 0.924872 x 0.987259 = 4.565 -> 4.5, uncapped
    assert _nwps(REEF, MODERATE_CROSS)["stars"] == 4.5


def test_mop_override_reads_the_period_allowance():
    assert _mop(REEF, MODERATE_CROSS)["stars"] == 4.5


def test_a_beach_over_rock_is_not_on_the_ceiling():
    assert _rate(_spot({"break_type": "beach", "bottom": "rock"}), LIGHT_OFFSHORE)["stars"] == 5.0
