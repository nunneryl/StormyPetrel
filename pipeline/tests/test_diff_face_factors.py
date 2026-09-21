"""The face-factor comparison tool must keep three kinds of change apart.

WHY THAT IS THE WHOLE TEST SUITE. P0-1 asks whether a 14-day per-spot factor is stable,
and the reading rules were fixed before the measurement: stable closes the question, churn
in both directions means hysteresis is worth building, one-way drift means a 14-day factor
does not describe the next 14 days. Those rules are only applicable if the numbers they
read describe FACTOR DRIFT ALONE.

Between 2026-09-01 and 2026-09-19 the file changes for three unrelated reasons:

  1. drift          — a spot kept in both files; the measurement moved
  2. membership     — a spot entering or leaving because the GATES moved (35 -> 90 degree
                      shore normal, the is_valid_surf_spot filter, the 2200 m separation
                      gate). No before-and-after exists for these.
  3. hold-out churn — a spot crossing between `factors` and `held_out` because its
                      measured p75/p25 crossed FACE_FACTOR_MAX_IQR_RATIO

A tool that summed them would report ~20 "changes" against 130 drift spots and make the
file look unstable on the strength of decisions we made ourselves. Every test below is
ultimately about that separation.

NOTHING HERE ASSERTS A STABILITY VERDICT, because the tool does not produce one — see its
module docstring for why choosing a threshold here would answer P0-1 on the wrong
authority.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

import diff_face_factors as D                                  # noqa: E402
from build_face_factors import GATE_CONSTANT_KEYS, HELD_OUT    # noqa: E402

BASELINE = os.path.join(ROOT, "pipeline", "data", "spot_face_factors.json")

GATES_OLD = {"FACE_SHORE_NORMAL_MAX_DELTA": 35.0, "MATCH_SEPARATION_M": None,
             "is_valid_surf_spot_filter_applied": False}
GATES_NEW = {"FACE_SHORE_NORMAL_MAX_DELTA": 90.0, "MATCH_SEPARATION_M": 2200.0,
             "is_valid_surf_spot_filter_applied": True}


# --------------------------------------------------------------------------- #
# Fixtures.                                                                    #
# --------------------------------------------------------------------------- #

def _kept(factor, p25=1.0, p75=1.5):
    return {"factor": factor, "hours": 330, "p10": 0.8, "p25": p25, "p75": p75,
            "p90": 2.0, "spread_p90_p10": 2.5}


def _held_spread(factor, iqr):
    return {"verdict": "spread", "factor": factor, "hours": 330,
            "reason": f"within-spot p75/p25 spread {iqr:.2f} exceeds 1.7 — the median is "
                      f"the centre of a cloud, not a stable offset"}


def _named(factor):
    return {"verdict": "held_out", "factor": factor, "hours": 330,
            "reason": "held out by name"}


def _doc(factors=None, held=None, gates=None, window=("2026-08-18", "2026-09-01"),
         max_iqr=1.7):
    m = {"run_on": window[1], "window": {"t0": window[0], "t1": window[1]},
         "generated_at": window[1] + "T00:00:00+00:00",
         "max_iqr_ratio_p75_p25": max_iqr}
    if gates is not None:
        m["population_gates"] = gates
    return {"_schema_version": 1, "measurement": m,
            "factors": factors or {}, "held_out": held or {}}


def _paths(old_doc, new_doc):
    d = tempfile.mkdtemp()
    po, pn = os.path.join(d, "old.json"), os.path.join(d, "new.json")
    with open(po, "w") as fh:
        json.dump(old_doc, fh)
    with open(pn, "w") as fh:
        json.dump(new_doc, fh)
    return po, pn


def _cmp(old_doc, new_doc):
    po, pn = _paths(old_doc, new_doc)
    return D.compare(D.load(po), D.load(pn))


# --------------------------------------------------------------------------- #
# SCENARIO: identical inputs.                                                  #
# --------------------------------------------------------------------------- #

def test_identical_files_report_no_change_of_any_kind():
    doc = _doc({"a": _kept(2.0), "b": _kept(1.5)},
               {"tarpits": _held_spread(0.7, 2.3), "rincon": _named(0.62)}, GATES_NEW)
    r = _cmp(doc, doc)
    assert r["drift"]["n"] == 2
    assert r["drift"]["median_ratio"] == 1.0
    assert (r["drift"]["n_up"], r["drift"]["n_down"]) == (0, 0)
    assert r["drift"]["n_unchanged"] == 2
    assert r["drift"]["direction_balance"] == 0
    assert r["membership"]["n_entered"] == r["membership"]["n_left"] == 0
    assert r["holdout"]["n_crossings"] == 0
    assert r["provenance"]["disagreement"] == []


def test_identical_files_render_without_a_disagreement_banner():
    doc = _doc({"a": _kept(2.0)}, {}, GATES_NEW)
    text = D.render(_cmp(doc, doc))
    assert "THE TWO FILES DISAGREE ON A GATE" not in text


# --------------------------------------------------------------------------- #
# SCENARIO: pure drift.                                                        #
# --------------------------------------------------------------------------- #

def test_pure_drift_is_reported_only_as_drift():
    old = _doc({"a": _kept(2.0), "b": _kept(1.0), "c": _kept(4.0)}, {}, GATES_NEW)
    new = _doc({"a": _kept(2.2), "b": _kept(0.9), "c": _kept(4.0)}, {}, GATES_NEW)
    r = _cmp(old, new)
    assert r["drift"]["n"] == 3
    assert r["membership"]["n_entered"] == r["membership"]["n_left"] == 0
    assert r["holdout"]["n_crossings"] == 0


def test_the_per_spot_ratio_is_new_over_old():
    """Direction matters for the reading, so the orientation is pinned by value."""
    r = _cmp(_doc({"a": _kept(2.0)}, {}, GATES_NEW),
             _doc({"a": _kept(3.0)}, {}, GATES_NEW))
    row = r["drift"]["rows"][0]
    assert abs(row["ratio"] - 1.5) < 1e-12
    assert abs(row["pct"] - 50.0) < 1e-9


def test_symmetric_churn_shows_a_balance_of_zero():
    """The signature the "hysteresis is worth building" rule keys on."""
    old = _doc({"a": _kept(2.0), "b": _kept(2.0)}, {}, GATES_NEW)
    new = _doc({"a": _kept(2.2), "b": _kept(1.8)}, {}, GATES_NEW)
    r = _cmp(old, new)["drift"]
    assert (r["n_up"], r["n_down"]) == (1, 1)
    assert r["direction_balance"] == 0


def test_one_way_drift_shows_a_nonzero_balance_and_a_shifted_median():
    """The signature the "more fundamental" rule keys on. Two statistics, because a
    balance alone cannot distinguish many tiny rises from a few large ones."""
    old = _doc({"a": _kept(2.0), "b": _kept(2.0), "c": _kept(2.0)}, {}, GATES_NEW)
    new = _doc({"a": _kept(2.2), "b": _kept(2.2), "c": _kept(2.2)}, {}, GATES_NEW)
    r = _cmp(old, new)["drift"]
    assert r["direction_balance"] == 3
    assert abs(r["median_ratio"] - 1.1) < 1e-9


def test_the_ratio_bands_are_symmetric_so_a_balanced_file_looks_balanced():
    """ASYMMETRIC BANDS WOULD FABRICATE ONE-WAY DRIFT. Buckets of 0.9-1.0 and 1.0-1.1 look
    even and are not: 1/0.9 is 1.111, so the down bucket is wider and a perfectly
    symmetric distribution would show more downs than ups.
    """
    for edge in D._BANDS:
        assert D.band_of(1.0 + edge) == edge, edge
        assert D.band_of(1.0 / (1.0 + edge)) == edge, edge


def test_the_band_epsilon_does_not_swallow_the_next_value():
    """The tie-breaker must resolve a representation tie and nothing wider."""
    assert D.band_of(1.0501) == 0.10
    assert D.band_of(1.0 / 1.0501) == 0.10
    assert D._BAND_EPS < 1e-9


def test_the_central_statistic_is_the_MEDIAN_not_the_mean():
    """A single spot with a huge ratio must not move the headline number.

    Four spots flat and one at 5x: the mean ratio is 1.8, the median is 1.0. Reporting the
    mean would read as violent one-way drift across a file where four of five spots did
    not move at all — and one 5x mover is far likelier to be a bad pairing than a signal.
    """
    old = _doc({s: _kept(1.0) for s in "abcde"}, {}, GATES_NEW)
    new = _doc({"a": _kept(1.0), "b": _kept(1.0), "c": _kept(1.0),
                "d": _kept(1.0), "e": _kept(5.0)}, {}, GATES_NEW)
    d = _cmp(old, new)["drift"]
    assert d["median_ratio"] == 1.0
    mean = sum(r["ratio"] for r in d["rows"]) / len(d["rows"])
    assert abs(mean - 1.8) < 1e-12
    assert d["median_ratio"] != mean


def test_a_zero_or_negative_old_factor_is_recorded_not_divided_by():
    r = _cmp(_doc({"a": {"factor": 0.0, "p25": 1.0, "p75": 1.5}}, {}, GATES_NEW),
             _doc({"a": _kept(2.0)}, {}, GATES_NEW))
    assert r["drift"]["n"] == 0
    assert [u["slug"] for u in r["drift"]["unusable"]] == ["a"]


def test_the_drift_spread_percentiles_are_reported():
    """The third statistic the rules need: a median at 1.0 with a wide spread is a
    different finding from a median at 1.0 with none."""
    old = _doc({s: _kept(2.0) for s in "abcde"}, {}, GATES_NEW)
    new = _doc({"a": _kept(1.6), "b": _kept(1.8), "c": _kept(2.0),
                "d": _kept(2.2), "e": _kept(2.4)}, {}, GATES_NEW)
    d = _cmp(old, new)["drift"]
    assert d["median_ratio"] == 1.0
    assert d["min_ratio"] == 0.8 and d["max_ratio"] == 1.2
    assert d["p10_ratio"] is not None and d["p90_ratio"] is not None
    assert d["p25_ratio"] < d["median_ratio"] < d["p75_ratio"]


# --------------------------------------------------------------------------- #
# SCENARIO: pure membership change.                                            #
# --------------------------------------------------------------------------- #

def test_membership_change_never_enters_the_drift_statistics():
    """THE LOAD-BEARING SEPARATION. A file where the gates moved and nothing drifted must
    read as perfectly stable."""
    old = _doc({"shared": _kept(2.0), "gone": _kept(3.0)}, {}, GATES_OLD)
    new = _doc({"shared": _kept(2.0), "new1": _kept(1.1), "new2": _kept(1.2)}, {},
               GATES_OLD)
    r = _cmp(old, new)
    assert r["membership"]["n_entered"] == 2
    assert r["membership"]["n_left"] == 1
    assert {x["slug"] for x in r["drift"]["rows"]} == {"shared"}
    assert r["drift"]["median_ratio"] == 1.0
    assert r["drift"]["direction_balance"] == 0
    assert r["drift"]["n_up"] == r["drift"]["n_down"] == 0


def test_an_entering_spot_is_reported_with_the_map_it_landed_in():
    old = _doc({"a": _kept(2.0)}, {}, GATES_NEW)
    new = _doc({"a": _kept(2.0)}, {"newly_excluded": _held_spread(1.0, 1.9)}, GATES_NEW)
    r = _cmp(old, new)
    (row,) = r["membership"]["entered"]
    assert row["slug"] == "newly_excluded"
    assert row["map"] == "held_out"
    assert row["verdict"] == "spread"


def test_a_leaving_spot_is_reported_with_the_map_it_left_from():
    old = _doc({"a": _kept(2.0)}, {"dropped": _held_spread(1.0, 1.9)}, GATES_NEW)
    new = _doc({"a": _kept(2.0)}, {}, GATES_NEW)
    r = _cmp(old, new)
    (row,) = r["membership"]["left"]
    assert row["slug"] == "dropped" and row["map"] == "held_out"


def test_entering_and_leaving_are_separate_lists_not_a_net_count():
    """A net count of zero would hide nine arrivals and nine departures."""
    old = _doc({"a": _kept(1.0)}, {}, GATES_NEW)
    new = _doc({"b": _kept(1.0)}, {}, GATES_NEW)
    r = _cmp(old, new)["membership"]
    assert r["n_entered"] == 1 and r["n_left"] == 1
    assert [x["slug"] for x in r["entered"]] == ["b"]
    assert [x["slug"] for x in r["left"]] == ["a"]


# --------------------------------------------------------------------------- #
# SCENARIO: hold-out crossings, both directions.                               #
# --------------------------------------------------------------------------- #

def test_a_kept_to_held_crossing_reports_both_spreads_and_the_margin():
    old = _doc({"wobbler": _kept(2.0, p25=1.0, p75=1.65)}, {}, GATES_NEW)
    new = _doc({}, {"wobbler": _held_spread(2.0, 1.73)}, GATES_NEW)
    r = _cmp(old, new)
    assert r["holdout"]["n_crossings"] == 1
    (x,) = r["holdout"]["kept_to_held"]
    assert x["direction"] == "kept -> held_out"
    assert abs(x["old_spread"] - 1.65) < 1e-12
    assert x["new_spread"] == 1.73
    assert abs(x["old_margin"] + 0.05) < 1e-12        # 1.65 - 1.70
    assert abs(x["new_margin"] - 0.03) < 1e-9         # 1.73 - 1.70


def test_a_held_to_kept_crossing_reports_the_same_shape():
    old = _doc({}, {"returner": _held_spread(1.5, 1.94)}, GATES_NEW)
    new = _doc({"returner": _kept(1.5, p25=1.0, p75=1.62)}, {}, GATES_NEW)
    r = _cmp(old, new)
    assert r["holdout"]["n_crossings"] == 1
    (x,) = r["holdout"]["held_to_kept"]
    assert x["direction"] == "held_out -> factors"
    assert x["old_spread"] == 1.94 and abs(x["new_spread"] - 1.62) < 1e-12
    assert x["old_margin"] > 0 > x["new_margin"]


def test_a_crossing_is_not_in_the_drift_statistics():
    old = _doc({"wobbler": _kept(2.0, p75=1.65)}, {}, GATES_NEW)
    new = _doc({}, {"wobbler": _held_spread(2.0, 1.73)}, GATES_NEW)
    r = _cmp(old, new)
    assert r["drift"]["n"] == 0
    assert r["drift"]["rows"] == []


def test_the_precision_asymmetry_is_not_reported_as_drift():
    """Kept factors are round(median, 4); held-out factors are the raw float. A spot
    crossing maps changes precision as well as value, and 0.4967949362146973 -> 0.4968 is
    a formatting artifact that would otherwise read as a 0.001% move.

    The value used is moonstone-beach-humboldt's real held_out factor.
    """
    raw = 0.4967949362146973
    old = _doc({}, {"returner": _held_spread(raw, 1.94)}, GATES_NEW)
    new = _doc({"returner": _kept(round(raw, 4), p75=1.6)}, {}, GATES_NEW)
    (x,) = _cmp(old, new)["holdout"]["held_to_kept"]
    assert x["old_factor"] == x["new_factor"] == 0.4968
    assert x["factor_unchanged"] is True


def test_a_real_move_across_a_crossing_is_still_reported_as_moved():
    """The converse, so the test above pins precision handling rather than a blanket
    'crossings never move' rule."""
    old = _doc({}, {"returner": _held_spread(1.50, 1.94)}, GATES_NEW)
    new = _doc({"returner": _kept(1.90, p75=1.6)}, {}, GATES_NEW)
    (x,) = _cmp(old, new)["holdout"]["held_to_kept"]
    assert x["factor_unchanged"] is False
    assert x["old_factor"] == 1.5 and x["new_factor"] == 1.9


def test_the_spread_precision_is_labelled_on_each_side():
    """A kept record's spread is computed from stored quartiles; a held record's is parsed
    at 2dp out of its reason string, because a held record carries no p25/p75. Presenting
    them as equally precise would make a 0.03 margin look better resolved than it is."""
    old = _doc({"w": _kept(2.0, p75=1.65)}, {}, GATES_NEW)
    new = _doc({}, {"w": _held_spread(2.0, 1.73)}, GATES_NEW)
    (x,) = _cmp(old, new)["holdout"]["kept_to_held"]
    assert x["old_spread_precision"] == "exact"
    assert x["new_spread_precision"].startswith("2dp")
    assert "limit of what is recorded" in D.render(_cmp(old, new))


def test_a_held_record_has_no_band_to_compare():
    """Band comparison is possible only kept-to-kept. Said, not emitted as nulls."""
    assert D.kept_spread({"p25": 1.6, "p75": 2.5}) is not None
    assert D.held_spread(_named(3.0)) is None
    assert D.held_spread({"reason": "no numbers here"}) is None


def test_the_named_holdouts_come_from_the_generators_dict():
    """Taken from HELD_OUT rather than from a transcribed list, so adding a fourth named
    hold-out cannot leave this tool silently reporting three."""
    r = _cmp(_doc({}, {s: _named(1.0) for s in HELD_OUT}, GATES_NEW),
             _doc({}, {s: _named(1.0) for s in HELD_OUT}, GATES_NEW))
    assert r["holdout"]["named_slugs"] == sorted(HELD_OUT)
    assert set(r["holdout"]["named_slugs"]) == {"fort-point", "rincon", "sandspit"}


def test_named_holdouts_are_excluded_from_the_churn_counts():
    """They are hardcoded by slug and cannot churn. Counting them as stable members of a
    recomputed filter would inflate how stable that filter looks."""
    held = {s: _named(1.0) for s in HELD_OUT}
    held["tarpits"] = _held_spread(0.7, 2.3)
    r = _cmp(_doc({}, held, GATES_NEW), _doc({}, held, GATES_NEW))
    assert set(r["holdout"]["held_in_both"]) == {"tarpits"}
    assert not (set(r["holdout"]["held_in_both"]) & set(HELD_OUT))


def test_named_holdouts_are_excluded_from_crossings_in_BOTH_directions():
    """The exclusion must not depend on which way a named hold-out appears to move. Both
    directions are pinned because a one-sided filter passes every realistic fixture — a
    named hold-out is always held — and then fails the first time one is not.
    """
    named = sorted(HELD_OUT)[0]
    # held -> factors
    r = _cmp(_doc({}, {named: _named(1.0)}, GATES_NEW),
             _doc({named: _kept(1.0)}, {}, GATES_NEW))
    assert r["holdout"]["n_crossings"] == 0
    assert [x["slug"] for x in r["holdout"]["held_to_kept"]] == []
    # factors -> held
    r = _cmp(_doc({named: _kept(1.0)}, {}, GATES_NEW),
             _doc({}, {named: _named(1.0)}, GATES_NEW))
    assert r["holdout"]["n_crossings"] == 0
    assert [x["slug"] for x in r["holdout"]["kept_to_held"]] == []


def test_a_named_holdout_that_moved_BETWEEN_MAPS_is_flagged_as_impossible():
    """Present on both sides, in different maps. HELD_OUT is consulted before the spread
    rule, so a named spot can never be written to `factors` — this one really is a bug."""
    old = _doc({}, {"rincon": _named(0.62)}, GATES_NEW)
    new = _doc({"rincon": _kept(0.62)}, {}, GATES_NEW)
    r = _cmp(old, new)
    (row,) = [x for x in r["holdout"]["named"] if x["slug"] == "rincon"]
    assert row["impossible"] is True
    assert row["status"] == "moved between maps"
    assert "should be impossible" in D.render(r)


def test_a_named_holdout_ABSENT_FROM_THE_NEW_FILE_left_the_population():
    """THE fort-point CASE, and it is not an impossibility.

    fort-point is in HELD_OUT, yet it vanished from the new file because the HARNESS
    rejected it upstream on "no orientation_deg or no metaShoreNormal" — its MOP point's
    shore normal reads as absent since the masked-scalar fix, which is that fix working.
    The named list only pins what happens to a spot that REACHES the builder; it says
    nothing about whether the spot gets there. Calling this impossible sent a reader
    hunting for a bug in the hold-out machinery.
    """
    old = _doc({}, {"fort-point": _named(3.08)}, GATES_NEW)
    new = _doc({"a": _kept(2.0)}, {}, GATES_NEW)
    r = _cmp(old, new)
    (row,) = [x for x in r["holdout"]["named"] if x["slug"] == "fort-point"]
    assert row["impossible"] is False
    assert row["status"] == "left the population"
    assert row["old"] == "held_out" and row["new"] == "absent"

    text = D.render(r)
    assert "LEFT THE POPULATION" in text
    assert "see section 2" in text
    assert "should be impossible" not in text

    # ...and it is genuinely in section 2, which is where the reader is being sent.
    assert [x["slug"] for x in r["membership"]["left"]] == ["fort-point"]


def test_a_named_holdout_in_NEITHER_file_did_not_leave_anything():
    """Absent on both sides is not a departure, and the distinction is not academic: the
    named list has three slugs and a run that never generated any of them would otherwise
    report three spots as having left a population they were never in. Found by rehearsing
    the tool against two files that shared no named hold-out."""
    r = _cmp(_doc({"a": _kept(2.0)}, {}, GATES_NEW),
             _doc({"a": _kept(2.0)}, {}, GATES_NEW))
    (row,) = [x for x in r["holdout"]["named"] if x["slug"] == "fort-point"]
    assert row["old"] == "absent" and row["new"] == "absent"
    assert row["status"] == "absent from both"
    assert row["impossible"] is False
    text = D.render(r)
    assert "in neither file" in text
    assert "LEFT THE POPULATION" not in text
    # Nothing left and nothing entered, so section 2 has nothing to point at either.
    assert r["membership"]["left"] == [] and r["membership"]["entered"] == []


def test_a_named_holdout_that_ENTERS_is_a_membership_change_too():
    """The converse direction, so the rule is 'absent on either side', not 'absent in the
    new file'."""
    old = _doc({"a": _kept(2.0)}, {}, GATES_NEW)
    new = _doc({}, {"rincon": _named(0.62)}, GATES_NEW)
    r = _cmp(old, new)
    (row,) = [x for x in r["holdout"]["named"] if x["slug"] == "rincon"]
    assert row["impossible"] is False
    assert row["status"] == "entered"
    assert "should be impossible" not in D.render(r)


def test_an_unchanged_named_holdout_is_flagged_as_nothing():
    old = new = _doc({}, {"rincon": _named(0.62)}, GATES_NEW)
    (row,) = [x for x in _cmp(old, new)["holdout"]["named"] if x["slug"] == "rincon"]
    assert row["status"] == "unchanged" and row["impossible"] is False


def test_the_named_section_says_what_the_list_does_not_pin():
    """The sentence that would have saved the reader the hunt."""
    text = D.render(_cmp(_doc({}, {"rincon": _named(0.62)}, GATES_NEW),
                         _doc({}, {"rincon": _named(0.62)}, GATES_NEW)))
    assert "REACHES the builder, not whether it does" in text


# --------------------------------------------------------------------------- #
# The drift histogram: exclusive ranges, ordered, with a cumulative column.    #
# --------------------------------------------------------------------------- #

def _bands_for(ratios):
    """The band rows for a synthetic set of new/old ratios."""
    old = _doc({f"s{i}": _kept(1.0) for i in range(len(ratios))}, {}, GATES_NEW)
    new = _doc({f"s{i}": _kept(round(r, 4)) for i, r in enumerate(ratios)}, {}, GATES_NEW)
    return _cmp(old, new)["drift"]["bands"]


def test_the_bands_are_an_ordered_list_widest_last():
    """A dict ordered them lexicographically, so a real run printed 10%, 25%, 5%, 50%.
    Order is part of the meaning, so it is a list and the upper edges ascend."""
    bands = _bands_for([1.02, 1.07, 1.2, 1.4, 2.0])
    assert isinstance(bands, list)
    labels = [b["label"] for b in bands]
    assert labels == ["0-5%", "5-10%", "10-25%", "25-50%", "beyond 50%"]
    uppers = [b["upper_pct"] for b in bands[:-1]]
    assert uppers == sorted(uppers)
    assert bands[-1]["upper_pct"] is None


def test_the_labels_are_ranges_not_the_misleading_within():
    """"within +/-10%" named a bucket holding 5-10% only, so a reader took 35 for the
    number that moved under 10% when 97 had."""
    for b in _bands_for([1.02, 1.07]):
        assert "within" not in b["label"], b


def test_the_bands_are_exclusive_and_sum_to_the_population():
    bands = _bands_for([1.01, 1.02, 1.07, 1.2, 1.4, 2.0])
    assert sum(b["total"] for b in bands) == 6
    assert [b["total"] for b in bands] == [2, 1, 1, 1, 1]


def test_the_cumulative_column_is_the_running_total():
    """The reading the old labels implied but did not provide."""
    bands = _bands_for([1.01, 1.02, 1.07, 1.2, 1.4, 2.0])
    assert [b["cumulative"] for b in bands] == [2, 3, 4, 5, 6]
    assert bands[-1]["cumulative"] == sum(b["total"] for b in bands)


def test_each_band_still_carries_its_direction_split():
    bands = _bands_for([1.02, 1 / 1.02, 1.07])
    tight = bands[0]
    assert tight["total"] == 2 and tight["up"] == 1 and tight["down"] == 1
    assert bands[1]["total"] == 1 and bands[1]["up"] == 1


def test_the_rendered_table_is_in_order_and_shows_the_cumulative_column():
    old = _doc({f"s{i}": _kept(1.0) for i in range(4)}, {}, GATES_NEW)
    new = _doc({"s0": _kept(1.02), "s1": _kept(1.07), "s2": _kept(1.2),
                "s3": _kept(2.0)}, {}, GATES_NEW)
    text = D.render(_cmp(old, new))
    for label in ("0-5%", "5-10%", "10-25%", "beyond 50%"):
        assert label in text, label
    assert "cum" in text
    assert "within +/-" not in text
    # widest last, in the rendered order
    positions = [text.index(lbl) for lbl in ("0-5%", "5-10%", "10-25%", "beyond 50%")]
    assert positions == sorted(positions)

    # THE NUMBERS ON THE PAGE, not just the headings. Checking labels and order alone let
    # a mutant that printed the band total in the `cum` column survive — and the cum
    # column is the entire point of the change, since it is what a reader was getting
    # wrong when "within +/-10%: 35" made 35 look like the count below 10%.
    #
    # Expected by hand from the four ratios above (1.02, 1.07, 1.2, 2.0), NOT from
    # compare_drift: one spot each in 0-5%, 5-10%, 10-25% and beyond 50%, none in
    # 25-50%. Running total 1, 2, 3, 3, 4.
    rows = re.findall(r"^\s+((?:\d+(?:\.\d+)?-\d+(?:\.\d+)?%)|(?:beyond \S+))\s+"
                      r"(\d+)\s+(\d+)\s", text, re.M)
    assert [(lbl, int(n), int(cum)) for lbl, n, cum in rows] == [
        ("0-5%", 1, 1), ("5-10%", 1, 2), ("10-25%", 1, 3),
        ("25-50%", 0, 3), ("beyond 50%", 1, 4)]


# --------------------------------------------------------------------------- #
# SCENARIO: gate disagreement.                                                 #
# --------------------------------------------------------------------------- #

def test_a_gate_disagreement_is_detected_and_named():
    r = _cmp(_doc({"a": _kept(2.0)}, {}, GATES_OLD),
             _doc({"a": _kept(2.0)}, {}, GATES_NEW))
    assert set(r["provenance"]["disagreement"]) == {
        "FACE_SHORE_NORMAL_MAX_DELTA", "is_valid_surf_spot_filter_applied"}
    # MATCH_SEPARATION_M is None on the old side, so it is unverifiable, not a disagreement
    assert "MATCH_SEPARATION_M" in r["provenance"]["unverifiable"]


def test_a_gate_disagreement_is_prominent_at_the_top_and_repeated_at_the_bottom():
    """A reader who skims to the drift numbers must not be able to miss it."""
    text = D.render(_cmp(_doc({"a": _kept(2.0)}, {}, GATES_OLD),
                         _doc({"a": _kept(2.0)}, {}, GATES_NEW)))
    head, _, tail = text.partition("1. FACTOR DRIFT")
    assert "DISAGREE ON A GATE" in head
    assert "DIFFERENT POPULATIONS" in head
    assert "AND THE GATES DISAGREE" in tail


def test_a_moved_exclusion_threshold_counts_as_a_gate_disagreement():
    """max_iqr_ratio_p75_p25 decides section 3's membership, so it is a gate even though
    it predates population_gates and lives elsewhere in the block."""
    r = _cmp(_doc({"a": _kept(2.0)}, {}, GATES_NEW, max_iqr=1.7),
             _doc({"a": _kept(2.0)}, {}, GATES_NEW, max_iqr=1.6))
    assert "max_iqr_ratio_p75_p25" in r["provenance"]["disagreement"]


def test_every_gate_row_carries_the_values_it_compared():
    """A row that says DISAGREE while showing None -> None is unreadable: the status is
    right and the evidence for it is missing. Both halves are pinned."""
    r = _cmp(_doc({"a": _kept(2.0)}, {}, GATES_OLD, max_iqr=1.7),
             _doc({"a": _kept(2.0)}, {}, GATES_NEW, max_iqr=1.6))
    rows = {g["gate"]: g for g in r["provenance"]["gates"]}
    assert rows["max_iqr_ratio_p75_p25"]["old"] == 1.7
    assert rows["max_iqr_ratio_p75_p25"]["new"] == 1.6
    assert rows["FACE_SHORE_NORMAL_MAX_DELTA"]["old"] == 35.0
    assert rows["FACE_SHORE_NORMAL_MAX_DELTA"]["new"] == 90.0
    for row in r["provenance"]["gates"]:
        if row["status"] == "DISAGREE":
            assert row["old"] is not None and row["new"] is not None, row
    text = D.render(r)
    assert "1.7" in text and "1.6" in text


def test_an_unrecorded_gate_is_unverifiable_not_a_disagreement():
    """Either file may predate the provenance commit. NULL MEANS UNRECORDED, NOT OFF —
    and 'we cannot check' is a weaker finding than 'we know they differ', not a safer one.
    """
    r = _cmp(_doc({"a": _kept(2.0)}, {}, None),
             _doc({"a": _kept(2.0)}, {}, GATES_NEW))
    assert r["provenance"]["disagreement"] == []
    assert set(r["provenance"]["unverifiable"]) >= set(GATE_CONSTANT_KEYS)
    assert "NULL MEANS UNRECORDED, NOT OFF" in D.render(r)


def test_the_windows_are_reported_side_by_side():
    r = _cmp(_doc({"a": _kept(2.0)}, {}, GATES_NEW, window=("2026-08-18", "2026-09-01")),
             _doc({"a": _kept(2.0)}, {}, GATES_NEW, window=("2026-09-05", "2026-09-19")))
    w = r["provenance"]["windows"]
    assert w["old"]["window"] == {"t0": "2026-08-18", "t1": "2026-09-01"}
    assert w["new"]["window"] == {"t0": "2026-09-05", "t1": "2026-09-19"}
    text = D.render(r)
    assert "2026-08-18 .. 2026-09-01" in text
    assert "2026-09-05 .. 2026-09-19" in text


# --------------------------------------------------------------------------- #
# The buckets partition. Nothing double-counted, nothing lost.                 #
# --------------------------------------------------------------------------- #

def test_every_slug_lands_in_exactly_one_bucket():
    old = _doc({"drifter": _kept(2.0), "crosser": _kept(1.0), "leaver": _kept(3.0)},
               {"tarpits": _held_spread(0.7, 2.3), "rincon": _named(0.62)}, GATES_NEW)
    new = _doc({"drifter": _kept(2.1), "arriver": _kept(1.4)},
               {"crosser": _held_spread(1.0, 1.8), "tarpits": _held_spread(0.7, 2.4),
                "rincon": _named(0.62)}, GATES_NEW)
    r = _cmp(old, new)
    assert r["reconciliation"]["ok"] is True
    assert r["reconciliation"]["union"] == r["reconciliation"]["bucketed"] == 6
    assert r["drift"]["n"] == 1
    assert r["holdout"]["n_crossings"] == 1
    assert r["membership"]["n_entered"] == 1 and r["membership"]["n_left"] == 1


def test_reconcile_refuses_a_slug_counted_twice():
    """The invariant tested directly. Inline in compare() no input could reach it while
    the bucket logic was correct, which made it defensive code no test could exercise —
    mutation testing found it survived every fixture."""
    try:
        D.reconcile({"a", "b"}, [{"a"}, {"a", "b"}])
    except AssertionError as e:
        assert "two buckets" in str(e) and "'a'" in str(e)
    else:
        raise AssertionError("a doubly-counted slug was accepted")


def test_reconcile_refuses_a_slug_counted_nowhere():
    try:
        D.reconcile({"a", "b", "lost"}, [{"a"}, {"b"}])
    except AssertionError as e:
        assert "no bucket" in str(e) and "lost" in str(e)
    else:
        raise AssertionError("a dropped slug was accepted")


def test_reconcile_accepts_a_clean_partition():
    got = D.reconcile({"a", "b", "c"}, [{"a"}, {"b", "c"}, set()])
    assert got == {"union": 3, "bucketed": 3, "ok": True}


def test_the_three_sections_are_disjoint_by_construction():
    """Stated as set arithmetic rather than counts, so a spot appearing twice fails here
    even if the totals happen to reconcile."""
    old = _doc({"a": _kept(1.0), "b": _kept(1.0)}, {"c": _held_spread(1.0, 1.9)},
               GATES_NEW)
    new = _doc({"a": _kept(1.1), "c": _kept(1.0)}, {"b": _held_spread(1.0, 1.9)},
               GATES_NEW)
    r = _cmp(old, new)
    drift = {x["slug"] for x in r["drift"]["rows"]}
    cross = ({x["slug"] for x in r["holdout"]["kept_to_held"]}
             | {x["slug"] for x in r["holdout"]["held_to_kept"]})
    member = ({x["slug"] for x in r["membership"]["entered"]}
              | {x["slug"] for x in r["membership"]["left"]})
    assert drift & cross == set()
    assert drift & member == set()
    assert cross & member == set()
    assert drift == {"a"} and cross == {"b", "c"} and member == set()


# --------------------------------------------------------------------------- #
# The real 09-01 baseline. The premises of the expected diff, checked.         #
# --------------------------------------------------------------------------- #

def test_the_committed_baseline_loads_and_has_the_expected_shape():
    side = D.load(BASELINE)
    assert len(side["kept"]) == 130
    assert len(side["held"]) == 7
    assert side["kept"] & side["held"] == set()


def test_the_baseline_records_no_gates_so_the_real_comparison_is_unverifiable():
    """The 09-01 file predates the provenance commit. The tool must say so rather than
    reading its absent gates as 'off' and declaring the populations identical."""
    new = _doc({}, {}, GATES_NEW)
    po, pn = _paths(json.load(open(BASELINE)), new)
    r = D.compare(D.load(po), D.load(pn))
    assert r["provenance"]["disagreement"] == []
    assert set(r["provenance"]["unverifiable"]) >= set(GATE_CONSTANT_KEYS)


def test_the_expected_membership_change_is_checked_against_the_files():
    """The brief's expected 09-19 diff is one leaving and nine arriving. What IS checkable
    here is the premise of that list, and it is checked rather than trusted:

      * moonstone-beach-humboldt is in the 09-01 file, so it can leave
      * none of the nine is in the 09-01 file, so each can arrive
      * all ten are in today's is_population, so the change is a GATE outcome and not a
        roster change

    What is NOT checkable in this container is the gate outcome itself — which of the ten
    passes the 90-degree and 2200 m gates needs scripts/mop_points.json, which is
    gitignored and absent. So this test pins the premises and the tool's classification,
    never the gate verdicts.
    """
    import mop_face_validation as MF
    from pipeline.enrich import _slug_for

    leaving = ["moonstone-beach-humboldt"]
    arriving = ["agate-beach", "carmel-beach", "crescent-city-beach", "cronkhite",
                "half-moon-bay-jetty", "jenner-beach", "pacifica-linda-mar", "rat-beach",
                "seal-beach-jetty"]
    assert len(arriving) == 9

    side = D.load(BASELINE)
    baseline_pop = side["kept"] | side["held"]
    for slug in leaving:
        assert slug in baseline_pop, f"{slug} cannot leave a file it is not in"
    for slug in arriving:
        assert slug not in baseline_pop, f"{slug} cannot arrive; it is already there"

    roster = json.load(open(os.path.join(ROOT, "pipeline", "spots_enriched.json")))
    population = {_slug_for(s["name"]) for s in roster if MF.is_population(s)}
    for slug in leaving + arriving:
        assert slug in population, f"{slug} is not in today's population at all"


def test_the_tool_classifies_the_expected_09_19_diff_correctly():
    """End to end against the real baseline: build the expected 09-19 population, then
    check the tool puts each change in the right section and keeps the arrivals and the
    departure out of the drift statistics entirely.
    """
    leaving = "moonstone-beach-humboldt"
    arriving = ["agate-beach", "carmel-beach", "crescent-city-beach", "cronkhite",
                "half-moon-bay-jetty", "jenner-beach", "pacifica-linda-mar", "rat-beach",
                "seal-beach-jetty"]
    base = json.load(open(BASELINE))
    new = json.loads(json.dumps(base))          # deep copy, no mutation of the original
    new["held_out"].pop(leaving)
    for slug in arriving:
        new["factors"][slug] = _kept(1.5)
    new["measurement"]["population_gates"] = GATES_NEW
    new["measurement"]["run_on"] = "2026-09-19"
    new["measurement"]["window"] = {"t0": "2026-09-05", "t1": "2026-09-19"}

    po, pn = _paths(base, new)
    r = D.compare(D.load(po), D.load(pn))

    assert r["membership"]["n_left"] == 1
    assert [x["slug"] for x in r["membership"]["left"]] == [leaving]
    assert r["membership"]["n_entered"] == 9
    assert sorted(x["slug"] for x in r["membership"]["entered"]) == sorted(arriving)

    drift_slugs = {x["slug"] for x in r["drift"]["rows"]}
    assert r["drift"]["n"] == 130
    assert leaving not in drift_slugs
    assert not (set(arriving) & drift_slugs)
    # Nothing drifted in this fixture, so it must read as perfectly stable despite ten
    # membership changes. That is the whole separation, on the real file.
    assert r["drift"]["median_ratio"] == 1.0
    assert r["drift"]["direction_balance"] == 0
    assert r["holdout"]["n_crossings"] == 0
    assert r["reconciliation"]["ok"] is True


# --------------------------------------------------------------------------- #
# Read-only, and the CLI.                                                      #
# --------------------------------------------------------------------------- #

def _sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def test_neither_input_is_modified():
    po, pn = _paths(_doc({"a": _kept(2.0)}, {}, GATES_NEW),
                    _doc({"a": _kept(2.2)}, {}, GATES_NEW))
    before = (_sha(po), _sha(pn))
    D.render(D.compare(D.load(po), D.load(pn)))
    D.main([po, pn])
    D.main([po, pn, "--json"])
    assert (_sha(po), _sha(pn)) == before


def test_the_committed_baseline_is_not_modified_by_a_run():
    """It is the comparison baseline; losing it makes the whole exercise unrepeatable."""
    before = _sha(BASELINE)
    _, pn = _paths(_doc(), _doc({"a": _kept(1.0)}, {}, GATES_NEW))
    D.main([BASELINE, pn])
    assert _sha(BASELINE) == before


def test_the_production_half_of_the_module_contains_no_write():
    src = open(os.path.join(ROOT, "scripts", "diff_face_factors.py")).read()
    production = src.split("# Selftest — pure logic")[0]
    assert '"w"' not in production and "'w'" not in production
    assert ".write(" not in production and "json.dump(" not in production


def test_the_cli_exits_zero_on_a_successful_comparison():
    po, pn = _paths(_doc({"a": _kept(2.0)}, {}, GATES_NEW),
                    _doc({"a": _kept(2.2)}, {}, GATES_NEW))
    assert D.main([po, pn]) == 0


def test_the_cli_exits_two_on_an_unreadable_input():
    po, pn = _paths(_doc({"a": _kept(2.0)}, {}, GATES_NEW), {"not": "a factor file"})
    assert D.main([po, pn]) == 2
    assert D.main([po + ".absent", pn]) == 2


def test_the_json_flag_emits_the_structured_form():
    po, pn = _paths(_doc({"a": _kept(2.0)}, {}, GATES_NEW),
                    _doc({"a": _kept(2.2)}, {}, GATES_NEW))
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "diff_face_factors.py"),
         po, pn, "--json"], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0
    doc = json.loads(proc.stdout)
    assert set(doc) >= {"drift", "membership", "holdout", "provenance", "counts",
                        "reconciliation"}
    assert doc["drift"]["n"] == 1
    assert doc["provenance"]["disagreement"] == []


def test_the_default_output_is_the_human_report_not_json():
    po, pn = _paths(_doc({"a": _kept(2.0)}, {}, GATES_NEW),
                    _doc({"a": _kept(2.2)}, {}, GATES_NEW))
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "diff_face_factors.py"), po, pn],
        capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0
    assert "FACE FACTOR COMPARISON" in proc.stdout
    assert "1. FACTOR DRIFT" in proc.stdout
    assert "2. MEMBERSHIP CHANGE" in proc.stdout
    assert "3. HOLD-OUT CHURN" in proc.stdout


def test_the_script_selftest_passes():
    proc = subprocess.run(
        [sys.executable, os.path.join(ROOT, "scripts", "diff_face_factors.py"),
         "--selftest"], capture_output=True, text=True, cwd=ROOT)
    assert proc.returncode == 0, proc.stdout[-2000:]
    assert "ALL PASS" in proc.stdout


def test_a_file_with_a_slug_in_both_maps_is_refused():
    """The generator cannot produce it, so it means a hand-edit — and which map a spot is
    in is the entirety of section 3."""
    bad = {"_schema_version": 1, "measurement": {},
           "factors": {"a": _kept(1.0)}, "held_out": {"a": _named(1.0)}}
    po, pn = _paths(bad, _doc())
    try:
        D.load(po)
    except D.FactorFileError as e:
        assert "BOTH factors and held_out" in str(e)
    else:
        raise AssertionError("a slug in both maps was accepted")


# --------------------------------------------------------------------------- #
# The tool must not answer P0-1 on its own authority.                          #
# --------------------------------------------------------------------------- #

def test_the_tool_reports_no_stability_verdict():
    """'Stable' has no numeric definition in the brief. A tool that picked one would
    answer P0-1 on the author's authority rather than the measurement's, and the reading
    rules were fixed in advance precisely so that could not happen.
    """
    text = D.render(_cmp(_doc({"a": _kept(2.0)}, {}, GATES_NEW),
                         _doc({"a": _kept(2.0)}, {}, GATES_NEW)))
    assert "does NOT classify" in text
    for verdict in ("VERDICT", "STABLE:", "UNSTABLE", "PASS", "FAIL"):
        assert verdict not in text, verdict


def test_the_report_names_the_statistics_the_fixed_rules_key_on():
    text = D.render(_cmp(_doc({"a": _kept(2.0), "b": _kept(1.0)}, {}, GATES_NEW),
                         _doc({"a": _kept(2.1), "b": _kept(0.9)}, {}, GATES_NEW)))
    assert "median ratio" in text
    assert "balance" in text
    assert "CHURN IN BOTH DIRECTIONS" in text
    assert "ONE-WAY DRIFT" in text
