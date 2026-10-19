"""pipeline/star_preview.py: re-scoring a published row against a different swell window.

Every expected star value is worked by hand from the published curves in pipeline/interpret.py,
never read back from rescore() or compare():

  size_score       0->0, 1->1, 2->2, 3->2.5, 4->3, 5->3.5, 6->4, 8->4.5, 10->5 (linear between)
  period_quality   12 s -> 0.95, 14 s -> 1.0 + 1/3 x 0.05 = 1.016667, 16 s -> 1.05
  period_factor    (WW3 table) 8 s -> 1.05, 12 s -> 1.15, 14 s -> 1.2, 16 s -> 1.25
  composite_stars  with wind, tide and chop at 1.0: round(2 x size x pq**0.25) / 2,
                   0 below 0.5 ft, clamped to 1..5 above it
  M_TO_FT          3.281
"""
from __future__ import annotations

from pipeline import star_preview as SP

# A window open 250-290 (pad 0: span == max - min), aimed at 270.
WEST = {"swell_window_arcs": [{"min": 250, "max": 290, "span": 40}],
        "optimal_swell_dir": 270, "orientation_deg": 270.0}
# Open 290-340 only, aimed at 315.
NW = {"swell_window_arcs": [{"min": 290, "max": 340, "span": 50}],
      "optimal_swell_dir": 315, "orientation_deg": 315.0}
# Open 0-60 only, aimed at 30: 270 is 90 degrees from its nearest edge (0).
NE = {"swell_window_arcs": [{"min": 0, "max": 60, "span": 60}],
      "optimal_swell_dir": 30, "orientation_deg": 30.0}

ONES = {"wind_mult": 1.0, "tide_mult": 1.0, "chop_mult": 1.0}


def _mop(**kw):
    row = {"swell_source": "cdip_mop", "swell_dp": 270.0, "swell_tp": 13.0, "face_ft": 4.0,
           "face_ft_raw": None, "period_quality": 1.0, **ONES}
    row.update(kw)
    return row


# --------------------------------------------------------------------------- #
# MOP: face x gain, the face fixed                                             #
# --------------------------------------------------------------------------- #

def test_mop_row_in_window_head_on():
    # gain 1 (270 is the optimal), eff 4.0 -> size 3.0, pq 1 -> 3.0 stars
    r = SP.rescore(_mop(), WEST)
    assert (r["stars"], r["gain"], r["dir"]) == (3.0, 1.0, 270.0)


def test_mop_row_inside_45_degrees_of_an_edge_is_on_the_040_rung():
    # 270 is 20 outside [290-340]: gain 0.40, eff 1.6 -> size 1.6 -> round(3.2)/2 = 1.5
    r = SP.rescore(_mop(), NW)
    assert (r["stars"], r["gain"]) == (1.5, 0.40)


def test_mop_row_90_degrees_outside_scores_nothing():
    # 270 is 90 from 0 and 150 from 60: not < 90, gain 0, eff 0 -> 0 stars
    r = SP.rescore(_mop(), NE)
    assert (r["stars"], r["gain"]) == (0.0, 0.0)


def test_mop_uses_the_raw_face_when_a_correction_divided_the_published_one():
    # face_ft 2.0 is the corrected display; the rater used face_ft_raw 4.0 -> 3.0 stars
    assert SP.rescore(_mop(face_ft=2.0, face_ft_raw=4.0), WEST)["stars"] == 3.0


def test_mop_aims_at_the_optimal_not_the_shore_normal():
    # Optimal 250, MOP shore normal 270, swell from 270 inside [250-290]: the in-window
    # gain is cos(20/2)**2 = cos(10)**2 = 0.969846, not the 1.0 aiming at the normal gives.
    spot = dict(WEST, optimal_swell_dir=250, mop_shore_normal=270.0)
    assert round(SP.rescore(_mop(), spot)["gain"], 6) == 0.969846


# --------------------------------------------------------------------------- #
# NWPS override: direction and period from the partitions, else dirpw/perpw   #
# --------------------------------------------------------------------------- #

def _nwps(**kw):
    row = {"swell_source": "nwps_height_ww3_dir", "face_ft": 8.0, "face_ft_raw": None,
           "swell_tp": 12.0, "dp": 300.0, "tp": 12.0,
           "swell_1_hs": 2.0, "swell_1_tp": 12.0, "swell_1_dp": 270.0, **ONES}
    row.update(kw)
    return row


def test_nwps_row_same_partition_same_window():
    # dir 270, per 12 (unchanged, so the face stays 8.0), gain 1: size(8) = 4.5,
    # pq 0.95**0.25 = 0.987258 -> 4.4427 -> round(8.885)/2 = 4.5
    r = SP.rescore(_nwps(), WEST)
    assert (r["stars"], r["gain"], r["dir"], r["path"]) == (4.5, 1.0, 270.0, "nwps_height_ww3_dir")


def test_nwps_row_partition_on_the_040_rung():
    # 270 is 20 outside [290-340]: gain 0.40, eff 3.2 -> size 2.6, x 0.987258 = 2.5669
    # -> round(5.134)/2 = 2.5
    r = SP.rescore(_nwps(), NW)
    assert (r["stars"], r["gain"]) == (2.5, 0.40)


def test_nwps_row_falls_back_to_dirpw_when_no_partition_keeps_gain():
    # The partition at 180 is 110 outside [290-340]: gain 0, so combine returns None and the
    # direction is the row's dirpw (dp 300) at perpw 12. 300 is inside, 15 off the optimal
    # 315: cos(7.5)**2 = 0.982963; eff 8 x 0.982963 = 7.8637 -> size 4.0 + 0.9319 x 0.5
    # = 4.4659, x 0.987258 = 4.4090 -> round(8.818)/2 = 4.5
    r = SP.rescore(_nwps(swell_1_dp=180.0), NW)
    assert (r["stars"], r["path"], r["dir"]) == (4.5, "nwps", 300.0)
    assert round(r["gain"], 6) == 0.982963


def test_nwps_row_face_follows_the_new_period():
    # Published at period 8 s (face 3.0); the window now picks a 16 s partition. The face
    # scales by period_factor 1.25 / 1.05: 3.0 x 1.190476 = 3.5714 -> size 2.7857;
    # pq 1.05**0.25 = 1.012272 -> 2.8199 -> round(5.640)/2 = 3.0. Unscaled it would be
    # size(3.0) = 2.5 x 1.012272 = 2.5307 -> 2.5.
    row = _nwps(face_ft=3.0, swell_tp=8.0, swell_1_hs=1.0, swell_1_tp=16.0, swell_1_dp=270.0)
    assert SP.rescore(row, WEST)["stars"] == 3.0


# --------------------------------------------------------------------------- #
# rate_spot rows                                                               #
# --------------------------------------------------------------------------- #

def _ww3(**kw):
    row = {"swell_source": "ww3", "face_ft": 99.0, "face_ft_raw": None, "period_quality": 0.5,
           "swell_1_hs": 1.0, "swell_1_tp": 14.0, "swell_1_dp": 270.0,
           "swell_2_hs": 1.0, "swell_2_tp": 8.0, "swell_2_dp": 180.0, **ONES}
    row.update(kw)
    return row


def test_ww3_row_recombines_the_partitions():
    # 270 in [250-290] on the optimal: gain 1. 180 is 70 outside: gain 0.15. Combined
    # hs = sqrt(1 + 0.15) = 1.072381; the 14 s partition dominates. face = 1.072381 x 1.2 x
    # 3.281 = 4.2222 -> size 3.1111, pq(14)**0.25 = 1.004141 -> 3.1240 -> round(6.248)/2
    # = 3.0. The stored face (99) and period quality (0.5) are NOT used on this path.
    r = SP.rescore(_ww3(), WEST)
    assert (r["stars"], r["gain"], r["dir"], r["path"]) == (3.0, 1.0, 270.0, "ww3")


def test_ww3_row_with_no_partition_left_is_not_replayable():
    r = SP.rescore(_ww3(swell_2_dp=270.0), NE)   # both at 270, 90 outside [0-60]
    assert r["stars"] is None and "not stored" in r["why"]


def test_nwps_path_row_uses_the_stored_direction_and_face():
    # No partitions: face 4.0 x gain on dp 270. Optimal None, so the target is the
    # orientation 270: gain 1 -> 3.0 stars.
    row = {"swell_source": "nwps_total", "face_ft": 4.0, "swell_dp": None, "dp": 270.0,
           "period_quality": 1.0, **ONES}
    spot = dict(WEST, optimal_swell_dir=None)
    r = SP.rescore(row, spot)
    assert (r["stars"], r["gain"], r["path"]) == (3.0, 1.0, "nwps_total")


# --------------------------------------------------------------------------- #
# compare(): only rows that reproduce are compared                             #
# --------------------------------------------------------------------------- #

def test_compare_counts_only_reproduced_rows():
    names = {1: ("A", "Hawaii"), 2: ("B", "California")}
    before = {"A": WEST, "B": WEST}
    after = {"A": NW, "B": WEST}
    rows = [
        dict(_mop(), spot_id=1, valid_time="2026-10-15T00:00:00+00:00", stars=3.0),  # 3.0 -> 1.5
        dict(_mop(), spot_id=1, valid_time="2026-10-15T01:00:00+00:00", stars=2.0),  # replays 3.0
        dict(_mop(), spot_id=2, valid_time="2026-10-15T00:00:00+00:00", stars=3.0),  # unchanged
    ]
    rep = SP.compare(rows, before, after, names)
    assert rep["replay"] == {"MOP NOT reproduced": 1, "MOP reproduced": 2}
    assert rep["compared"] == 2
    assert (rep["changed_spot_hours"], rep["changed_spots"]) == (1, 1)
    assert rep["delta_by_group"] == {"MOP": {-1.5: 1, 0.0: 1}}
    assert rep["delta_by_state"] == {"California": {0.0: 1}, "Hawaii": {-1.5: 1}}
    top = rep["top20"]
    assert [t["spot"] for t in top] == ["A"] and top[0]["changed_hours"] == 1
    ex = top[0]["example"]
    assert (ex["valid_time"], ex["gain_before"], ex["gain_after"],
            ex["stars_before"], ex["stars_after"]) == ("2026-10-15T00:00:00+00:00", 1.0, 0.4, 3.0, 1.5)


def test_compare_reports_rows_it_cannot_replay_by_reason():
    names = {1: ("A", "Hawaii")}
    rows = [dict(_ww3(swell_2_dp=270.0), spot_id=1, valid_time="t", stars=3.0)]
    rep = SP.compare(rows, {"A": NE}, {"A": NE}, names)
    assert rep["compared"] == 0
    assert list(rep["unreplayable"].values()) == [1]
    assert list(rep["unreplayable"])[0].startswith("WW3: ww3: no partition keeps any gain")


class _Recorder:
    """A stand-in Supabase client that records every method called on it."""

    def __init__(self, data):
        self.calls, self.args, self._data, self._table = [], [], data, None

    def __getattr__(self, name):
        def call(*args, **kw):
            self.calls.append(name)
            self.args.append((name, args))
            if name == "table":
                self._table = args[0]
            return self
        return call

    def execute(self):
        self.calls.append("execute")

        class R:
            data = self._data[self._table]
        return R()


def test_the_fetch_only_reads():
    """Read-only by construction: the fetch uses select/filter/order/range and nothing else."""
    client = _Recorder({"spots": [{"id": 1, "name": "A", "state": "Hawaii"}],
                        "forecasts": [{"spot_id": 1, "valid_time": "t"}]})
    names, rows = SP._fetch(client, "2026-10-15T00:00:00+00:00")
    assert names == {1: ("A", "Hawaii")} and rows == [{"spot_id": 1, "valid_time": "t"}]
    assert set(client.calls) == {"table", "select", "eq", "gte", "order", "range", "execute"}


def test_the_window_is_read_one_spot_at_a_time():
    """Run 11 timed out paging the whole window by OFFSET. Each forecasts read must now carry
    its spot's id, so it is a short range on the UNIQUE(spot_id, valid_time, source) key."""
    client = _Recorder({"spots": [{"id": 7, "name": "A", "state": "Hawaii"},
                                  {"id": 3, "name": "B", "state": "California"}],
                        "forecasts": []})
    SP._fetch(client, "2026-10-15T00:00:00+00:00")
    tables = [a[0] for n, a in client.args if n == "table"]
    assert tables == ["spots", "forecasts", "forecasts"]
    assert [a for n, a in client.args if n == "eq" and a[0] == "spot_id"] == [("spot_id", 3), ("spot_id", 7)]
    assert [a for n, a in client.args if n == "eq" and a[0] == "source"] == [("source", "nwps")] * 2


# --------------------------------------------------------------------------- #
# The tide preview: the stored tide_mult swapped on unverified preferences      #
# --------------------------------------------------------------------------- #
#
# Worked by hand. tide_mult enters composite_stars as tide_mult**0.15:
#   0.6**0.15 = exp(0.15 x ln 0.6) = exp(-0.076624) = 0.926238
#   0.8**0.15 = exp(0.15 x ln 0.8) = exp(-0.033472) = 0.967082
# A MOP row on WEST at 270 has gain 1.0; with wind, chop and period quality at 1.0,
# stars = round(2 x size x tide_mult**0.15) / 2, and size_score is 3 + 0.5 x (face - 4)
# between 4 and 5 ft:
#   face 4.9: size 3.45, 2 x size = 6.9
#       tide 0.6 -> 6.9 x 0.926238 = 6.391 -> 6 -> 3.0     (published)
#       tide 0.8 -> 6.9 x 0.967082 = 6.673 -> 7 -> 3.5     (damp:0.5 keeps 1 - 0.5 x 0.4 = 0.8)
#       tide 1.0 -> 6.9                    -> 7 -> 3.5     (neutral)
#   face 4.6: size 3.3, 2 x size = 6.6
#       tide 0.6 -> 6.6 x 0.926238 = 6.113 -> 6 -> 3.0     (published)
#       tide 0.8 -> 6.6 x 0.967082 = 6.383 -> 6 -> 3.0     (damp:0.5: no change)
#       tide 1.0 -> 6.6                    -> 7 -> 3.5     (neutral)

import pytest  # noqa: E402

TIDES = {1: {"tide_preference": "low", "tide_preference_source": "unattributed"},
         2: {"tide_preference": "low", "tide_preference_source": "scraped"},
         3: {"tide_preference": None, "tide_preference_source": None}}
TIDE_NAMES = {1: ("A", "Hawaii"), 2: ("B", "California"), 3: ("C", "Florida")}
TIDE_SPOTS = {"A": WEST, "B": WEST, "C": WEST}


def _tide_rows():
    return [
        dict(_mop(face_ft=4.9, tide_mult=0.6, tide_norm=0.9), spot_id=1, valid_time="t1", stars=3.0),
        dict(_mop(face_ft=4.6, tide_mult=0.6, tide_norm=0.9), spot_id=1, valid_time="t2", stars=3.0),
        dict(_mop(face_ft=4.9, tide_mult=0.6, tide_norm=0.9), spot_id=2, valid_time="t1", stars=3.0),
        dict(_mop(face_ft=4.9, tide_mult=1.0, tide_norm=0.1), spot_id=3, valid_time="t1", stars=3.5),
    ]


def _tide_compare(spec):
    _, f = SP.tide_mode(spec)
    return SP.compare(_tide_rows(), TIDE_SPOTS, TIDE_SPOTS, TIDE_NAMES,
                      row_after=SP.tide_row_after(TIDES, f))


@pytest.mark.parametrize("spec, tm, expected", [
    ("keep", 0.6, 0.6),
    ("neutral", 0.6, 1.0),
    ("neutral", 0.8, 1.0),
    ("damp:0.5", 0.6, 0.8),          # 1 - 0.5 x 0.4
    ("damp:0.25", 0.7, 0.925),       # 1 - 0.25 x 0.3
    ("damp:0.5", 1.0, 1.0),          # no penalty to keep
])
def test_tide_modes(spec, tm, expected):
    assert SP.tide_mode(spec)[1](tm) == pytest.approx(expected, abs=1e-12)


@pytest.mark.parametrize("spec", ["damp:0", "damp:1", "damp:1.5", "half", ""])
def test_a_tide_mode_outside_the_three_is_refused(spec):
    with pytest.raises(ValueError):
        SP.tide_mode(spec)


@pytest.mark.parametrize("source, expected", [
    ("reviewed", False), ("researched", False), ("scraped", False), ("model_recall", True),
    ("unattributed", True), ("derived_from_break_type", True), (None, True),
])
def test_unverified_means_no_cited_source(source, expected):
    assert SP.unverified({"tide_preference": "mid", "tide_preference_source": source}) is expected
    assert SP.unverified(None) is True


def test_neutral_lifts_both_unverified_off_tide_hours_and_leaves_the_scraped_spot():
    rep = _tide_compare("neutral")
    assert rep["replay"] == {"MOP reproduced": 4}
    assert (rep["changed_spot_hours"], rep["changed_spots"]) == (2, 1)
    assert rep["delta_by_state"] == {"California": {0.0: 1}, "Florida": {0.0: 1},
                                     "Hawaii": {0.5: 2}}
    top, = rep["top20"]
    assert (top["spot"], top["changed_hours"]) == ("A", 2)
    ex = top["example"]
    assert (ex["valid_time"], ex["tide_norm"], ex["tide_mult_before"], ex["tide_mult_after"],
            ex["stars_before"], ex["stars_after"]) == ("t1", 0.9, 0.6, 1.0, 3.0, 3.5)


def test_damping_by_half_moves_only_the_hour_near_the_boundary():
    rep = _tide_compare("damp:0.5")
    assert (rep["changed_spot_hours"], rep["changed_spots"]) == (1, 1)
    assert rep["delta_by_state"]["Hawaii"] == {0.0: 1, 0.5: 1}
    ex = rep["top20"][0]["example"]
    assert (ex["valid_time"], ex["stars_after"]) == ("t1", 3.5)
    assert ex["tide_mult_after"] == pytest.approx(0.8, abs=1e-12)


def test_keep_changes_nothing():
    rep = _tide_compare("keep")
    assert (rep["compared"], rep["changed_spot_hours"]) == (4, 0)


def test_the_swell_window_report_carries_no_tide_fields():
    rows = [dict(_mop(), spot_id=1, valid_time="t", stars=3.0)]
    rep = SP.compare(rows, {"A": WEST}, {"A": NW}, {1: ("A", "Hawaii")})
    assert "tide_mult_before" not in rep["top20"][0]["example"]


def test_the_tide_census():
    """The rater's rule: a researched preference is applied (mid is 1.0 inside [0.3, 0.7] and 0.7
    outside; low is 1.0 below 0.3, 0.8 below 0.7), an unattributed one rates 1.0 (since #251)."""
    tides = {1: {"tide_preference": "mid", "tide_preference_source": "researched"},
             2: {"tide_preference": "low", "tide_preference_source": "researched"},
             3: {"tide_preference": "mid", "tide_preference_source": "unattributed"}}
    rows = [{"spot_id": 1, "tide_norm": 0.5, "tide_mult": 1.0},     # mid in range: 1.0
            {"spot_id": 1, "tide_norm": 0.9, "tide_mult": 0.7},     # mid out of range: 0.7
            {"spot_id": 2, "tide_norm": 0.5, "tide_mult": 0.8},     # low, 0.3..0.7: 0.8
            {"spot_id": 2, "tide_norm": 0.1, "tide_mult": 0.6},     # low below 0.3 is 1.0, not 0.6
            {"spot_id": 1, "tide_norm": None, "tide_mult": None},
            {"spot_id": 3, "tide_norm": 0.9, "tide_mult": 1.0}]     # unattributed: 1.0, not 0.7
    c = SP.tide_census(rows, tides)
    assert c["spots"] == {"low / researched": 1, "mid / researched": 1, "mid / unattributed": 1}
    assert c["rows"] == 6
    assert c["tide_mult_by_preference"] == {"low": {"0.6": 1, "0.8": 1},
                                            "mid": {"0.7": 1, "1": 2, "None": 1}}
    assert c["hours_below_1_by_preference"] == {"low": 2, "mid": 1}
    # Ungated, spot 3's 1.0 at tide_norm 0.9 would be read as a wrong 0.7.
    assert c["tide_mult_from_tide_norm_and_preference"] == {"NOT reproduced": 1, "reproduced": 4}


def test_the_tide_fetch_only_reads():
    client = _Recorder({"spots": [{"id": 1, "tide_preference": "mid",
                                   "tide_preference_source": "unattributed"}]})
    assert SP._fetch_tides(client) == {1: {"tide_preference": "mid",
                                           "tide_preference_source": "unattributed"}}
    assert set(client.calls) == {"table", "select", "order", "range", "execute"}


def test_the_tide_fetch_without_the_source_column_reads_every_source_as_none():
    class NoSource(_Recorder):
        def execute(self):
            if any(n == "select" and "tide_preference_source" in a[0] for n, a in self.args):
                self.args.clear()
                raise RuntimeError("column spots.tide_preference_source does not exist")
            return super().execute()

    client = NoSource({"spots": [{"id": 1, "tide_preference": "mid"}]})
    assert SP._fetch_tides(client) == {1: {"tide_preference": "mid",
                                           "tide_preference_source": None}}


def test_the_cli_needs_arcs_or_tide():
    with pytest.raises(SystemExit):
        SP.main([])


# --------------------------------------------------------------------------- #
# 'rated': the rater's own rule, held to neutral                               #
# --------------------------------------------------------------------------- #
# The rows of _tide_rows, recomputed with interpret's rule from the row's tide_norm and the
# roster's source: low at tide_norm 0.9 is 0.6 when trusted, 1.0 when not (see the tide
# preview's hand arithmetic above for the stars).

def _rated(roster):
    return SP.compare(_tide_rows(), TIDE_SPOTS, TIDE_SPOTS, TIDE_NAMES,
                      row_after=SP.rated_row_after(roster, TIDE_NAMES))


ROSTER_AS_LIVE = {   # the sources TIDES holds, as a roster
    "A": dict(WEST, name="A", tide_preference="low", tide_preference_source="unattributed"),
    "B": dict(WEST, name="B", tide_preference="low", tide_preference_source="scraped"),
    "C": dict(WEST, name="C", tide_preference=None, tide_preference_source=None),
}


def test_rated_is_neutral_hour_for_hour_when_the_sources_agree():
    """The check that the rating publishes what the neutral preview showed: the same rows,
    the same changes, the same examples."""
    assert _rated(ROSTER_AS_LIVE) == _tide_compare("neutral")


def test_rated_keeps_a_trusted_penalty_and_lifts_an_untrusted_one():
    rep = _rated(ROSTER_AS_LIVE)
    assert (rep["changed_spot_hours"], rep["changed_spots"]) == (2, 1)
    assert rep["delta_by_state"]["California"] == {0.0: 1}     # B, scraped: 0.6 kept, 3.0
    assert rep["delta_by_state"]["Hawaii"] == {0.5: 2}         # A: 0.6 -> 1.0, 3.0 -> 3.5


def test_rated_reapplies_a_reviewed_preference_the_live_table_calls_unattributed():
    """'rated' reads the roster, as the rater does; a roster that trusts A's 'low' leaves its
    stored 0.6 in place, so nothing changes."""
    roster = dict(ROSTER_AS_LIVE, A=dict(ROSTER_AS_LIVE["A"], tide_preference_source="reviewed"))
    assert _rated(roster)["changed_spot_hours"] == 0



# --------------------------------------------------------------------------- #
# --nwps-shelter: the window's cut on NWPS rows                               #
# --------------------------------------------------------------------------- #
# Two spots, both WEST (open 250-290, pad 0, optimal 270): S in California with its node 1.5 km
# out, T in Hawaii with its node 0.6 km out. Each row has the dominant partition's hs = 2.0, so
# R = swell_hs / 2. Worked by hand:
#
#   S, 9-13 s inside:  r1 R 0.5, r2 R 0.7                 -> reference median 0.6
#   T, 9-13 s inside:  t1 R 0.5                           -> reference 0.5
#   r3  300 (10 out, 0.40)  R 0.30 -> R_rel 0.5   real (the boundary is inclusive)
#   r4  340 (50 out, 0.15)  R 0.06 -> R_rel 0.1
#   r5  300 + a 305 partition of hs 1.1: share 4 / 5.21 = 0.768 -> not attributable
#   r6  300 at 8 s (<=9 s band): R 0.4, no inside <=9 s reference at S -> no R_rel
#   r9  300 + a 305 partition of hs 1.0: share 4 / 5 = 0.8 -> attributable; R 0.18 -> R_rel 0.3
#       (its wind sea, hs 0.5, is not swell: counted, the share would be 4 / 5.25 = 0.762)
#   r12 300, no node swell height -> not attributable
#   r13 150 (100 out): no partition keeps gain
#   t2  300 (0.40)  R 0.45 -> R_rel 0.9   real
# Outside, attributable: r3 r4 r6 r9 t2; with a reference r3 r4 r9 t2, R_rel 0.5 0.1 0.3 0.9:
# median (0.3 + 0.5)/2 = 0.4, real 2 of 4.

SHELTER_NAMES = {1: ("S", "California"), 2: ("T", "Hawaii")}
SHELTER_SPOTS = {"S": dict(WEST, nwps_node_distance_m=1500.0),
                 "T": dict(WEST, nwps_node_distance_m=600.0)}


def _sh(sid, p1_dp, swell_hs, dirpw=None, tp=12.0, swell_dp=None, **kw):
    row = {"spot_id": sid, "swell_source": "nwps_height_ww3_dir", "swell_hs": swell_hs,
           "dp": dirpw, "swell_1_hs": 2.0, "swell_1_tp": tp, "swell_1_dp": p1_dp,
           "swell_dp": p1_dp if swell_dp is None else swell_dp}
    row.update(kw)
    return row


def _shelter_rows():
    return [
        _sh(1, 270.0, 1.0, dirpw=275.0),                        # r1
        _sh(1, 270.0, 1.4, swell_dp=999.0),                     # r2: published on another dir
        _sh(1, 300.0, 0.6, dirpw=280.0),                        # r3
        _sh(1, 340.0, 0.12, dirpw=330.0),                       # r4
        _sh(1, 300.0, 1.0, swell_2_hs=1.1, swell_2_tp=12.0, swell_2_dp=305.0),   # r5
        _sh(1, 300.0, 0.8, dirpw=300.0, tp=8.0),                # r6
        {"spot_id": 1, "swell_source": "nwps"},                 # r7: dirpw fallback
        {"spot_id": 1, "swell_source": "ww3"},                  # r8: not NWPS
        _sh(1, 300.0, 0.36, dirpw=299.0, swell_2_hs=1.0, swell_2_tp=12.0, swell_2_dp=305.0,
            wind_wave_hs=0.5, wind_wave_tp=4.0, wind_wave_dp=270.0),  # r9: wind sea not counted
        _sh(1, 300.0, None),                                    # r12
        _sh(1, 150.0, 1.0),                                     # r13
        _sh(2, 270.0, 1.0),                                     # t1
        _sh(2, 300.0, 0.9, dirpw=285.0),                        # t2
        {"spot_id": 9, "swell_source": "nwps"},                 # a spot the baseline lacks
    ]


def _shelter():
    return SP.shelter_report(_shelter_rows(), SHELTER_SPOTS, SHELTER_NAMES)


@pytest.mark.parametrize("tp, band", [(8.0, "<=9 s"), (9.0, "<=9 s"), (9.1, "9-13 s"),
                                      (13.0, "9-13 s"), (13.5, ">13 s")])
def test_period_band(tp, band):
    assert SP.period_band(tp) == band


@pytest.mark.parametrize("m, band", [(None, "unknown"), (999.0, "<1 km"), (1000.0, "1-2.5 km"),
                                     (2500.0, "1-2.5 km"), (2501.0, ">2.5 km")])
def test_node_band(m, band):
    assert SP.node_band(m) == band


@pytest.mark.parametrize("dp, rung", [(270.0, "inside"), (290.0, "inside"), (300.0, "0.40"),
                                      (334.9, "0.40"), (335.0, "0.15"), (150.0, "blocked")])
def test_window_rung(dp, rung):
    # 335 is 45 past 290: the 0.15 rung starts there, as in directional_gain. 150 is 100 out.
    assert SP.window_rung(dp, WEST["swell_window_arcs"]) == rung


def test_window_rung_with_no_window():
    assert SP.window_rung(10.0, []) == "no window"


def test_shelter_counts():
    rep = _shelter()
    assert rep["rows"] == 14
    assert rep["by_swell_source"] == {"nwps": 2, "nwps_height_ww3_dir": 11, "ww3": 1}
    assert rep["nwps_rows_without_a_spot"] == 1
    assert (rep["ww3_dir_rows"], rep["no_partition_keeps_gain"]) == (11, 1)
    assert rep["dominant_matches_published_direction"] == {False: 1, True: 9}
    assert rep["by_rung"] == {"0.15": 1, "0.40": 6, "inside": 3}
    assert rep["outside_not_attributable"] == {"share below 0.80": 1, "no node swell height": 1}


def test_shelter_inside_reference():
    # S: R 0.5 and 0.7; T: 0.5. Pairs (S, 9-13 s) and (T, 9-13 s); median of 0.5, 0.7, 0.5 = 0.5
    assert _shelter()["inside_reference"] == {"hours": 3, "spot_bands": 2, "median_R": 0.5}


def test_shelter_outside_overall_and_by_band():
    out = _shelter()["outside"]
    assert out["all"] == {"hours": 5, "with_reference": 4, "median_R_rel": 0.4,
                          "real_energy": 2, "real_share": 0.5}
    assert out["by_period_band"] == {
        "9-13 s": {"hours": 4, "with_reference": 4, "median_R_rel": 0.4, "real_energy": 2,
                   "real_share": 0.5},
        "<=9 s": {"hours": 1, "with_reference": 0, "median_R_rel": None, "real_energy": 0,
                  "real_share": None},
    }


def test_shelter_outside_by_state_rung_and_node():
    out = _shelter()["outside"]
    # California: r3 r4 r6 r9, R_rel 0.5 0.1 0.3 -> median 0.3, real 1 of 3.
    assert out["by_state"] == {
        "California": {"hours": 4, "with_reference": 3, "median_R_rel": 0.3, "real_energy": 1,
                       "real_share": 0.333},
        "Hawaii": {"hours": 1, "with_reference": 1, "median_R_rel": 0.9, "real_energy": 1,
                   "real_share": 1.0},
    }
    # 0.40: r3 r6 r9 t2, R_rel 0.5 0.3 0.9 -> median 0.5, real 2 of 3. 0.15: r4 alone.
    assert out["by_rung"] == {
        "0.15": {"hours": 1, "with_reference": 1, "median_R_rel": 0.1, "real_energy": 0,
                 "real_share": 0.0},
        "0.40": {"hours": 4, "with_reference": 3, "median_R_rel": 0.5, "real_energy": 2,
                 "real_share": 0.667},
    }
    assert out["by_node_distance"]["<1 km"] == out["by_state"]["Hawaii"]
    assert out["by_node_distance"]["1-2.5 km"] == out["by_state"]["California"]


def test_shelter_dirpw_on_outside_hours():
    # Outside: r3 280 in, r4 330 out, r5 none, r6 300 out, r9 299 out, r12 none, t2 285 in.
    assert _shelter()["dirpw_inside_window_on_outside_hours"] == {
        "False": 3, "None": 2, "True": 2}


def test_shelter_with_no_outside_hour():
    rep = SP.shelter_report([_sh(1, 270.0, 1.0)], SHELTER_SPOTS, SHELTER_NAMES)
    assert rep["outside"]["all"] == {"hours": 0, "with_reference": 0, "median_R_rel": None,
                                     "real_energy": 0, "real_share": None}


def test_no_gate_lifts_the_040_rung_to_the_alignment_term():
    # _nwps on NW was published at 2.5 on the 0.40 rung (test above). With no window the gain is
    # cos²((270 - 315)/2) = cos²(22.5°) = 0.853553: eff 8 x 0.853553 = 6.828427, size
    # 4 + 0.5 x 0.828427/2 = 4.207107, x 0.987258 = 4.153500 -> round(8.307)/2 = 4.0.
    names = {1: ("A", "Hawaii")}
    rows = [dict(_nwps(), spot_id=1, valid_time="t", stars=2.5),
            dict(_ww3(), spot_id=1, valid_time="t", stars=3.0)]    # not NWPS: not replayed
    rep = SP.no_gate_compare(rows, {"A": NW}, names)
    assert rep["replay"] == {"NWPS reproduced": 1}
    assert rep["delta_by_group"] == {"NWPS": {1.5: 1}}
    ex = rep["top20"][0]["example"]
    assert (ex["gain_before"], ex["gain_after"], ex["stars_after"]) == (0.4, 0.854, 4.0)


def test_ungated_removes_only_the_arcs():
    spots = {"A": dict(NW, name="A")}
    assert SP.ungated(spots) == {"A": dict(NW, name="A", swell_window_arcs=[])}
    assert spots["A"]["swell_window_arcs"] == NW["swell_window_arcs"]      # not mutated


def test_nwps_shelter_runs_on_its_own():
    with pytest.raises(SystemExit):
        SP.main(["--nwps-shelter", "--tide", "neutral"])
    with pytest.raises(SystemExit):
        SP.main(["--nwps-shelter", "--arcs", "x.json"])
