"""NWPS rows: the swell window CHOOSES the partition, the NWPS height takes the alignment only.

Decided on sw1-raycast run 19 (2026-10-19): of 78,817 reproduced NWPS spot-hours, scoring the
NWPS height with the alignment term instead of the window's rung changed 7,162 (6,875 up,
287 down), every one on the direction the window chose. WW3 rows and MOP keep the window.

Every expected value is worked by hand. The hours below run through the real override
(apply_nwps_overrides) with NWPS swh 1.6 m, the WW3 period 14 s, wind and tide 1.0 and no
NWPS swell height (so chop is the neutral 1.0):

  face  = 1.6 x period_factor(14, ww3) 1.2 x 3.281 = 6.29952 ft
  stars = round(2 x size_score(face x gain) x period_quality(14)**0.25) / 2
          period_quality(14) = 1 + (14 - 13)/3 x 0.05 = 1.016667, **0.25 = 1.004141
  size_score: 0->0, 1->1, 2->2, 3->2.5, 4->3, 5->3.5, 6->4, 8->4.5, 10->5, linear between

Steamer Lane as the frame tests freeze it (pipeline/tests/test_directional_gain_frame.py):
padded sectors [179, 253] and [315, 333], optimal 244, orientation 128.
"""
from __future__ import annotations

import datetime
import json
import os

from pipeline.forecast import mop, nwps_nearshore
from pipeline.interpret import combine_ww3_partitions, directional_gain

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_BASE = 1767225600                     # 2026-01-01T00:00:00Z

SL_ARCS = [{"min": 181, "max": 251, "span": 74}, {"min": 317, "max": 331, "span": 18}]
SL_OPTIMAL = 244
SL_ORIENT = 128.0


def _iso(epoch):
    return datetime.datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%dT%H:00:00Z")


def _override(spot, swell_dp):
    """One NWPS hour through apply_nwps_overrides; returns the entry it wrote."""
    entry = {"valid_time": _iso(_BASE), "stars": 9.9, "wind_mult": 1.0, "tide_mult": 1.0,
             "swell_source": "ww3", "swell_dp": swell_dp, "swell_tp": 14.0}
    stats = nwps_nearshore.apply_nwps_overrides(
        {spot["name"]: [entry]}, [spot], _fetch=lambda _s: {_BASE // 3600: (1.6, 7.8, 255.0, None)})
    assert stats["fed"] == 1, stats
    return entry


SL_SPOT = {"name": "T", "swell_window_source": "nwps", "nwps_wfo": "mtr",
           "orientation_deg": SL_ORIENT, "swell_window_arcs": SL_ARCS,
           "optimal_swell_dir": SL_OPTIMAL}


def test_in_window_is_unchanged():
    """dp 220.56 inside [179, 253]: the window's gain and the alignment agree.
        cos²(-23.44/2°) = 0.958739 -> eff 6.039593 -> size 4 + 0.5 x 0.039593/2 = 4.009898
        x 1.004141 = 4.026503 -> round(8.053)/2 = 4.0"""
    assert round(directional_gain(220.56, SL_ARCS, SL_OPTIMAL, SL_ORIENT), 6) == 0.958739
    e = _override(SL_SPOT, 220.56)
    assert (e["dir_gain"], e["stars"], e["face_ft"]) == (0.959, 4.0, 6.3)


def test_the_040_rung_is_no_longer_taken():
    """dp 273, 20° past 253: the window's rung is 0.40 —
        eff 2.519808 -> size 2.259904 x 1.004141 = 2.269262 -> round(4.539)/2 = 2.5
    — the NWPS height takes the alignment instead: diff 29 -> cos²(14.5°) = 0.937310
        eff 5.904602 -> size 3.5 + 0.5 x 0.904602 = 3.952301 x 1.004141 = 3.968667
        -> round(7.937)/2 = 4.0"""
    assert directional_gain(273.0, SL_ARCS, SL_OPTIMAL, SL_ORIENT) == 0.40
    e = _override(SL_SPOT, 273.0)
    assert (e["dir_gain"], e["stars"], e["swell_dp"]) == (0.937, 4.0, 273.0)


def test_the_015_rung_is_no_longer_taken():
    """dp 130, 49° short of 179: the window's rung is 0.15 —
        eff 0.944928 -> size 0.944928 x 1.004141 = 0.948841 -> round(1.898)/2 = 1.0
    — the alignment instead: diff -114 -> cos²(-57°) = 0.296632
        eff 1.868637 -> size 1.868637 x 1.004141 = 1.876375 -> round(3.753)/2 = 2.0"""
    assert directional_gain(130.0, SL_ARCS, SL_OPTIMAL, SL_ORIENT) == 0.15
    e = _override(SL_SPOT, 130.0)
    assert (e["dir_gain"], e["stars"]) == (0.297, 2.0)


def test_the_window_still_chooses_the_partition_the_bolinas_shape():
    """Bolinas, live geometry: one arc {169, 227, span 62} (pad 2, sector [167, 229]),
    optimal 196. Run 17 replayed it with no window at all and saw 215.61 -> 294.89. Two
    partitions, both 14 s, so the selection weight is hs² x gain:
        215.61 inside:   gain cos²(9.805°) = 0.970999, weight 1.0 x 0.970999 = 0.970999
        294.89, 65.89° past 229: the window's 0.15 rung, weight 4.0 x 0.15 = 0.6
    The window picks 215.61 although 294.89 is the larger swell (2.0 m against 1.0 m). With
    no window the 294.89 one would weigh 4.0 x cos²(49.445°) = 4.0 x 0.422731 = 1.690924 and
    win — run 17's variant A. The NWPS hour is then scored on 215.61:
        eff 6.29952 x 0.970999 = 6.116830 -> size 4.029208 x 1.004141 = 4.045892 -> 4.0"""
    spots = json.load(open(os.path.join(_REPO, "pipeline", "spots_enriched.json")))
    b, = [s for s in spots if s.get("name") == "Bolinas"]
    assert (b["swell_window_arcs"], b["optimal_swell_dir"], b["swell_window_source"]) == (
        [{"min": 169, "max": 227, "span": 62}], 196, "nwps")
    parts = {"swell_1_hs": 1.0, "swell_1_tp": 14.0, "swell_1_dp": 215.61,
             "swell_2_hs": 2.0, "swell_2_tp": 14.0, "swell_2_dp": 294.89}
    comb = combine_ww3_partitions(parts, b["swell_window_arcs"], b["optimal_swell_dir"],
                                  b["orientation_deg"])
    assert (comb["dominant_partition"], comb["dp"]) == ("swell_1", 215.61)
    no_window = combine_ww3_partitions(parts, [], b["optimal_swell_dir"], b["orientation_deg"])
    assert no_window["dp"] == 294.89
    e = _override(dict(b, nwps_wfo="mtr"), comb["dp"])
    assert (e["swell_dp"], e["dir_gain"], e["stars"]) == (215.61, 0.971, 4.0)


def test_mop_still_takes_the_window():
    """MOP is unchanged: Steamer Lane's frozen arcs at dp 273 -> the 0.40 rung."""
    _, _, dg, _, _ = mop.mop_stars(1.6, 14.0, 273.0, None, SL_ORIENT,
                                   arcs=SL_ARCS, optimal=SL_OPTIMAL)
    assert dg == 0.40


# --------------------------------------------------------------------------- #
# The methodology post                                                         #
# --------------------------------------------------------------------------- #

def _post():
    with open(os.path.join(_REPO, "frontend", "content", "blog", "methodology.md"),
              encoding="utf-8") as f:
        post = f.read()
    return post[post.index("## The swell window\n"):post.index("## Wind\n")]


def test_the_post_states_the_rule_and_run_19s_counts():
    """The counts are run 19's variant B (sw1-raycast, 2026-10-19, window from 12:58Z): 78,817
    reproduced NWPS spot-hours, 7,162 changed (7,162 / 78,817 = 9.09% -> 9.1%) at 230 spots,
    6,875 up and 287 down; variant A's "another partition" downs, 2,053."""
    post = _post()
    assert ("**Where the height comes from NWPS, the window chooses the swell but doesn't cut "
            "the height.**") in post
    assert ("cos²(offset ÷ 2) from the best swell direction, never below 0.25, inside the "
            "window or out. Heights from WW3 and MOP still take the window's gain in full.") in post
    assert ("7,162 of 78,817 NWPS spot-hours (9.1%), at 230 of our spots, changed: 6,875 came "
            "out higher and 287 lower") in post
    assert "2,053 spot-hours came out lower on its direction" in post
    assert "more than about 100° from the spot's best direction" in post


def test_the_posts_rule_is_the_codes():
    """NWPS: cos²(Δ/2) floored at 0.25 whatever the window. At dp 76 the window gives 0 and
    the alignment (diff -168 from 244) cos²(84°) = 0.010926 -> the floor 0.25. MOP keeps the
    window at the same bearing: 0."""
    _, _, dg, _, _ = nwps_nearshore.nwps_stars(1.6, 14.0, 76.0, None, SL_ORIENT,
                                               optimal=SL_OPTIMAL)
    assert dg == 0.25
    _, _, mop_dg, _, _ = mop.mop_stars(1.6, 14.0, 76.0, None, SL_ORIENT,
                                       arcs=SL_ARCS, optimal=SL_OPTIMAL)
    assert mop_dg == 0.0


def test_below_the_040_rung_only_past_about_100_degrees():
    """cos²(Δ/2) < 0.40 when Δ/2 > acos(√0.40) = acos(0.632456) = 50.768°, i.e. Δ > 101.54°:
        Δ 101 -> cos²(50.5°) = 0.404596;  Δ 102 -> cos²(51°) = 0.396044"""
    def dg(delta):
        return nwps_nearshore.nwps_stars(1.6, 14.0, SL_OPTIMAL + delta, None, SL_ORIENT,
                                         optimal=SL_OPTIMAL)[2]
    assert round(dg(101.0), 6) == 0.404596
    assert round(dg(102.0), 6) == 0.396044
