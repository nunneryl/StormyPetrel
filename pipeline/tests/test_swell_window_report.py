"""pipeline/swell_window_report.py — the before-and-after numbers for a swell-window recompute.

Expected values are worked by hand. Membership is sampled every 0.5 degree, inclusive at
both padded edges, so a pad-0 arc [250-290] holds 81 samples = 40.5 degrees.
"""
from __future__ import annotations

from pipeline import swell_window_report as R
from pipeline.descriptions import description_payload, description_signature, spot_fields

WEST = [{"min": 250, "max": 290, "span": 40}]


def _spot(name="Test Spot", state="Hawaii", arcs=WEST, orientation=270.0, optimal=270, **kw):
    s = {"name": name, "region_hint": state, "swell_window_arcs": arcs,
         "orientation_deg": orientation, "optimal_swell_dir": optimal}
    s.update(kw)
    return s


def test_measure_reads_width_facing_optimal_and_tier():
    m = R.measure(_spot(optimal=90))
    assert m["arcs"] == "[250-290]" and m["width"] == 40
    assert m["facing_in_window"] is True
    assert m["opens_past_90"] is False           # farthest open bearing is 20 off the facing
    assert m["optimal_past_90"] is True          # 90 is 180 from 270
    assert m["open_within_90"] == 40.5
    assert m["tier"] == "sheltered"              # 40 <= 90


def test_measure_flags_a_window_opening_behind_the_facing():
    # [300-100] wraps through north; facing 0, so 100 is 100 off (past 90, not past 135)
    m = R.measure(_spot(arcs=[{"min": 300, "max": 100, "span": 160}], orientation=0.0, optimal=0))
    assert (m["opens_past_90"], m["opens_past_135"]) == (True, False)
    m = R.measure(_spot(arcs=[{"min": 300, "max": 140, "span": 200}], orientation=0.0, optimal=0))
    assert (m["opens_past_90"], m["opens_past_135"], m["tier"]) == (True, True, "exposed")


def test_facing_outside_every_arc():
    assert R.measure(_spot(orientation=90.0))["facing_in_window"] is False


def test_regression_lists_only_hawaii_and_puerto_rico_spots_that_narrowed_near_the_facing():
    narrower = [{"min": 260, "max": 290, "span": 30}]        # 61 samples = 30.5 degrees
    behind = WEST + [{"min": 80, "max": 100, "span": 20}]    # adds only bearings behind
    # E loses only a window behind its facing: narrower overall, not within 90 -> not listed
    before = [_spot("A"), _spot("B", "Puerto Rico"), _spot("C", "California"), _spot("D"),
              _spot("E", arcs=behind)]
    after = [_spot("A", arcs=narrower), _spot("B", "Puerto Rico", arcs=narrower),
             _spot("C", "California", arcs=narrower), _spot("D", arcs=behind), _spot("E")]
    rep = R.build(before, after, {})
    assert rep["regression"] == [{"slug": "a", "state": "Hawaii", "lost": 10.0},
                                 {"slug": "b", "state": "Puerto Rico", "lost": 10.0}]
    assert rep["totals"]["before"]["tiers"] == {"sheltered": 5}


def test_a_description_is_stale_when_its_v2_signature_no_longer_matches():
    old = _spot()
    cache = {"test-spot": {"signature": description_signature(description_payload(spot_fields(old)))}}
    assert R.stale_descriptions([old], cache) == []
    assert R.stale_descriptions([_spot(optimal=250)], cache) == ["test-spot"]
    # a version-1 signature is never blanked by db_import, so it is not counted
    assert R.stale_descriptions([_spot(optimal=250)], {"test-spot": {"signature": "abc"}}) == []
