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
        self.calls, self._data, self._table = [], data, None

    def __getattr__(self, name):
        def call(*args, **kw):
            self.calls.append(name)
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
