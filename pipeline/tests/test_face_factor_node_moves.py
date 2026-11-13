"""A face factor measured at an NWPS node the spot no longer samples is not applied.

A factor is the ratio of the face at ONE node to CDIP MOP. Pacifica Linda Mar's 0.8799 (and
Rockaway Beach Pacifica's 0.8335, Avila Beach's 1.2515) was measured 2026-09-08 to 10-06 at the
node #313 moved on 2026-11-12. forecast/face_correction.suspend_moved_nodes keeps the record but
marks it suspended unless it was measured after the move; spot_factor then returns None, so the
display, the raters and the surf height all treat the spot as uncalibrated, while the median
factor M is still taken over every measurement.

Every expected value is written by hand.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging

from pipeline.forecast import face_correction as FC
from pipeline.interpret import face_factors_by_name

TODAY = datetime.date(2026, 11, 13)


def _rec(factor, measured_on="2026-10-06"):
    return {"factor": factor, "measured_on": measured_on,
            "window": {"t0": "2026-09-08", "t1": "2026-10-06"}}


def _slug(name):
    from pipeline.enrich import _slug_for
    return _slug_for(name)


def _spot(name):
    return {"name": name, "swell_window_source": "nwps"}


# --------------------------------------------------------------------------- #
# The rule                                                                     #
# --------------------------------------------------------------------------- #

def test_measured_before_the_move_is_suspended():
    out = FC.suspend_moved_nodes({"a": _rec(0.88, "2026-10-06")}, {"a": "2026-11-12"})
    assert out["a"]["suspended"] == ("measured 2026-10-06 at the NWPS node it no longer samples; "
                                     "nwps_node_overrides.json moved it on 2026-11-12")
    assert out["a"]["factor"] == 0.88


def test_measured_after_the_move_is_applied():
    out = FC.suspend_moved_nodes({"a": _rec(1.46, "2026-11-20")}, {"a": "2026-11-12"})
    assert "suspended" not in out["a"]


def test_measured_on_the_move_day_is_suspended():
    # The measurement could predate the move within the day; only a later day proves it did not.
    out = FC.suspend_moved_nodes({"a": _rec(1.0, "2026-11-12")}, {"a": "2026-11-12"})
    assert "suspended" in out["a"]


def test_a_move_with_no_date_or_an_unreadable_date_is_suspended():
    out = FC.suspend_moved_nodes(
        {"a": _rec(1.0, "2026-12-01"), "b": _rec(1.0, None), "c": _rec(1.0, "soon")},
        {"a": None, "b": "2026-11-12", "c": "2026-11-12"})
    assert all("suspended" in out[s] for s in "abc"), out


def test_a_spot_whose_node_never_moved_is_untouched():
    rec = _rec(2.0)
    out = FC.suspend_moved_nodes({"a": rec}, {"b": "2026-11-12"})
    assert out["a"] == rec and "suspended" not in out["a"]


# --------------------------------------------------------------------------- #
# Every reader                                                                 #
# --------------------------------------------------------------------------- #

FACTORS = FC.suspend_moved_nodes(
    {"pacifica-linda-mar": _rec(0.8799), "sharp-park": _rec(1.2033), "half-moon-bay": _rec(1.7041)},
    {"pacifica-linda-mar": "2026-11-12"})


def test_spot_factor_is_none_for_a_suspended_record():
    assert FC.spot_factor(_spot("Pacifica Linda Mar"), FACTORS, _slug) is None
    assert FC.spot_factor(_spot("Sharp Park"), FACTORS, _slug) == 1.2033


def test_the_raters_get_no_factor_for_it():
    by_name = face_factors_by_name([_spot("Pacifica Linda Mar"), _spot("Sharp Park")], FACTORS)
    assert by_name == {"Pacifica Linda Mar": None, "Sharp Park": 1.2033}


def test_the_median_still_reads_every_measurement():
    # 0.8799, 1.2033, 1.7041: the middle one, suspended or not.
    assert FC.median_face_factor(FACTORS) == 1.2033


def test_the_display_leaves_a_suspended_spot_as_the_producer_wrote_it():
    ratings = {"Pacifica Linda Mar": [{"face_ft": 15.71, "stars": 2.0}],
               "Sharp Park": [{"face_ft": 10.25, "stars": 4.0}]}
    spots = [_spot("Pacifica Linda Mar"), _spot("Sharp Park"), _spot("Half Moon Bay")]
    st = FC.apply_face_corrections(ratings, spots,
                                   factors=FACTORS, slug_for=_slug, now=TODAY)
    assert ratings["Pacifica Linda Mar"][0]["face_ft"] == 15.71
    assert ratings["Pacifica Linda Mar"][0]["face_ft_raw"] == 15.71
    assert ratings["Sharp Park"][0]["face_ft"] == 8.52        # 10.25 / 1.2033 = 8.518...
    assert (st["corrected_spots"], st["suspended"], st["no_factor"]) == (1, 1, 0), st


def test_a_suspension_changes_the_stamp():
    # sha256 over "slug=factor" lines, a suspended record's line ending " suspended".
    one = {"a": {"factor": 2.0, "measured_on": "2026-10-06"}}
    off = {"a": {"factor": 2.0, "measured_on": "2026-10-06", "suspended": "moved"}}
    assert FC.face_correction_stamp(one) == \
        "2:2026-10-06:" + hashlib.sha256(b"a=2").hexdigest()[:8]
    assert FC.face_correction_stamp(off) == \
        "2:2026-10-06:" + hashlib.sha256(b"a=2 suspended").hexdigest()[:8]


# --------------------------------------------------------------------------- #
# Loading                                                                      #
# --------------------------------------------------------------------------- #

def test_load_reads_the_move_dates_and_warns(tmp_path, caplog):
    factors = tmp_path / "factors.json"
    factors.write_text(json.dumps({"factors": {"x": _rec(0.9), "y": _rec(1.5)}}))
    overrides = tmp_path / "overrides.json"
    overrides.write_text(json.dumps({"overrides": {"x": {"moved_on": "2026-11-12"}, "z": {}}}))
    with caplog.at_level(logging.WARNING, logger="pipeline.forecast.face_correction"):
        out = FC.load_face_factors(factors, overrides)
    assert "suspended" in out["x"] and "suspended" not in out["y"]
    assert [r.getMessage() for r in caplog.records] == [
        "face factor for 'x' (0.9) not applied: measured 2026-10-06 at the NWPS node it no longer "
        "samples; nwps_node_overrides.json moved it on 2026-11-12. Re-measure it at the new node: "
        "python3 scripts/build_face_factors.py --apply"]


def test_load_node_moves(tmp_path):
    p = tmp_path / "o.json"
    p.write_text(json.dumps({"overrides": {"x": {"moved_on": "2026-11-12"}, "y": {"nwps_wfo": "mtr"}}}))
    assert FC.load_node_moves(p) == {"x": "2026-11-12", "y": None}
    assert FC.load_node_moves(tmp_path / "absent.json") == {}


# --------------------------------------------------------------------------- #
# The committed files                                                          #
# --------------------------------------------------------------------------- #

def test_the_committed_files_suspend_exactly_the_three_moved_calibrated_spots():
    f = FC.load_face_factors()
    assert {s: r["factor"] for s, r in f.items() if r.get("suspended")} == {
        "pacifica-linda-mar": 0.8799, "rockaway-beach-pacifica": 0.8335, "avila-beach": 1.2515}
    assert len(f) == 141
    assert FC.median_face_factor(f) == 1.5907       # M as measured, unchanged by the moves


def test_every_moved_calibrated_spot_records_its_move_date():
    moves = FC.load_node_moves()
    factors = FC.load_face_factors()
    assert {s: moves[s] for s in moves if s in factors} == {
        "pacifica-linda-mar": "2026-11-12", "rockaway-beach-pacifica": "2026-11-12",
        "avila-beach": "2026-11-12"}
