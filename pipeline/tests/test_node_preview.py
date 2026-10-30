"""pipeline/node_preview.py: the published window replayed at moved NWPS sample points.

Every expected value is worked by hand from the stand-in cycle below, never read back from the
module. The stand-in is a 3 x 4 CG1 grid, rows 36.18 / 36.19 / 36.20, columns -75.76 / -75.75 /
-75.74 / -75.73, cycle 2026-10-26 12Z, steps f000..f002:

  cell     (1, 1) the OLD node      (1, 3) the NEW node
  swh      1.23456 -> 1.235         2.0
  perpw    7.9                      8.1
  dirpw    75.0                     80.0
  shts     1.0 (NaN at f002)        1.8
  ws       5.5 (f001: 7.0)          6.25
  wdir     200.0                    210.0

Rounding is to 3 decimals, as nwps.fetch writes forecast_data/nwps.json.
"""
from __future__ import annotations

import datetime
import json
import os

import numpy as np

from pipeline import node_preview as NP
from pipeline.forecast.nwps_nearshore import apply_nwps_overrides

LATS = (36.18, 36.19, 36.20)
LONS = (-75.76, -75.75, -75.74, -75.73)
CDT = datetime.datetime(2026, 10, 26, 12, tzinfo=datetime.timezone.utc)
STAMP = "2026-10-26T12:00:00+00:00"
OLD = {"name": "Duck Pier", "swell_window_source": "nwps", "nwps_wfo": "mhx",
       "nwps_node_lat": 36.19, "nwps_node_lng": -75.75, "nwps_node_distance_m": 1140}
NEW = dict(OLD, nwps_node_lng=-75.73, nwps_node_distance_m=2074)
OLD_CELL = {"swh": 1.23456, "perpw": 7.9, "dirpw": 75.0, "shts": 1.0, "ws": 5.5, "wdir": 200.0}
NEW_CELL = {"swh": 2.0, "perpw": 8.1, "dirpw": 80.0, "shts": 1.8, "ws": 6.25, "wdir": 210.0}


def _vt(fh):
    return (CDT + datetime.timedelta(hours=fh)).isoformat()


def _k(fh):
    return int((CDT + datetime.timedelta(hours=fh)).timestamp() // 3600)


def _cycle(land=()):
    lats, lons = np.meshgrid(np.array(LATS), np.array(LONS), indexing="ij")
    fields = {}
    for fh in (0, 1, 2):
        for short in ("swh", "perpw", "dirpw", "shts", "ws", "wdir"):
            g = np.full((3, 4), 0.5, dtype="float32")
            g[1, 1] = OLD_CELL[short]
            g[1, 3] = NEW_CELL[short]
            if short == "shts" and fh == 2:
                g[1, 1] = np.nan
            if short == "ws" and fh == 1:
                g[1, 1] = 7.0
            for i, j in land:
                g[i, j] = np.nan
            fields[(short, fh)] = g
    mask = np.isnan(fields[("swh", 0)])
    return {"lats": lats, "lons": lons, "mask": mask, "cycle_dt": CDT, "steps": [0, 1, 2],
            "fields": fields}


def _row(fh, **kw):
    """A published nwps_height_ww3_dir hour rated at the OLD node, NWPS wind."""
    row = {"spot_id": 1, "valid_time": _vt(fh), "nwps_cycle": STAMP,
           "swell_source": "nwps_height_ww3_dir", "hs": 1.235, "tp": 7.9, "dp": 75.0,
           "swell_hs": 1.0, "swell_dp": 70.0, "swell_tp": 11.0, "wind_speed": 5.5,
           "wind_dir": 200.0, "wind_mult": 0.9, "tide_mult": 0.95, "stars": 1.235,
           "face_ft_raw": 3.0}
    row.update(kw)
    return row


def _stub(ratings, spots, *, _fetch, ceiling_factors, median_factor):
    """Stands in for apply_nwps_overrides: stars = the series' swh at the hour, face = the
    entry's wind speed, so what reached the rater is readable from the result."""
    series = _fetch(spots[0])
    for e in ratings[spots[0]["name"]]:
        m = (series or {}).get(int(datetime.datetime.fromisoformat(e["valid_time"]).timestamp()
                                   // 3600))
        if m:
            e.update(stars=m[0], face_ft=e["wind_speed"], swell_hs=m[3], swell_tp=e.get("swell_tp"))


# --------------------------------------------------------------------------- #
# Pure helpers                                                                 #
# --------------------------------------------------------------------------- #
def test_moved_spots_are_the_nwps_spots_whose_node_differs():
    base = [OLD, {"name": "Same", "swell_window_source": "nwps", "nwps_node_lat": 1.0,
                  "nwps_node_lng": 2.0},
            {"name": "Mop", "swell_window_source": "cdip_mop", "nwps_node_lat": 1.0,
             "nwps_node_lng": 2.0}]
    roster = [NEW, dict(base[1]), dict(base[2], nwps_node_lat=1.5),
              {"name": "New spot", "swell_window_source": "nwps", "nwps_node_lat": 3.0,
               "nwps_node_lng": 4.0}]
    got = NP.moved_spots(base, roster)
    assert [(o["name"], n["nwps_node_lng"]) for o, n in got] == [("Duck Pier", -75.73)]
    assert got[0][0]["nwps_node_lng"] == -75.75


def test_a_latitude_change_alone_is_a_move():
    got = NP.moved_spots([OLD], [dict(OLD, nwps_node_lat=36.2)])
    assert len(got) == 1


def test_cycle_key_reads_both_stamp_forms():
    assert NP.cycle_key("2026-10-26T12:00:00Z") == ("20261026", "12")
    assert NP.cycle_key("2026-10-27T00:00:00+00:00") == ("20261027", "00")
    assert NP.cycle_key(None) is None
    assert NP.cycle_key("not a time") is None


def test_node_series_rounds_to_three_decimals_and_keeps_a_missing_shts():
    got = NP.node_series(OLD, _cycle())
    assert got == {_k(0): (1.235, 7.9, 75.0, 1.0), _k(1): (1.235, 7.9, 75.0, 1.0),
                   _k(2): (1.235, 7.9, 75.0, None)}
    assert NP.node_series(NEW, _cycle())[_k(0)] == (2.0, 8.1, 80.0, 1.8)


def test_node_series_on_an_all_land_grid_is_none():
    land = [(i, j) for i in range(3) for j in range(4)]
    assert NP.node_series(OLD, _cycle(land=land)) is None


def test_node_wind_reads_the_forecast_hour_of_the_valid_time():
    c = _cycle()
    t1 = (CDT + datetime.timedelta(hours=1)).timestamp()
    assert NP.node_wind(c, 1, 1, t1) == (7.0, 200.0)
    assert NP.node_wind(c, 1, 3, t1) == (6.25, 210.0)
    assert NP.node_wind(c, 1, 1, (CDT + datetime.timedelta(hours=5)).timestamp()) is None


def test_node_wind_is_none_on_a_nan_cell():
    c = _cycle(land=[(0, 0)])
    assert NP.node_wind(c, 0, 0, CDT.timestamp()) is None


def test_node_check_a_cell_centre():
    assert NP.node_check(_cycle(), 36.19, -75.73) == {"wet": True, "same_cell": True,
                                                       "offset_km": 0.0}


def test_node_check_offset_from_the_centre():
    # 0.0001 deg of longitude at 36.19 N: 6371 x pi/180 x 0.0001 x cos(36.19) = 0.00897 km.
    got = NP.node_check(_cycle(), 36.19, -75.7299)
    assert got["wet"] and got["same_cell"] and got["offset_km"] == 0.009


def test_node_check_a_land_cell_is_neither_wet_nor_the_cell_sampled():
    # (1, 2) is land, so nwps.fetch's nearest row and column is dry, and the nearest WET cell
    # (1, 1), 0.01 deg away like (1, 3) and first in grid order, is another cell.
    got = NP.node_check(_cycle(land=[(1, 2)]), 36.19, -75.74)
    assert got["wet"] is False and got["same_cell"] is False


def test_pre_override_entry_restores_the_ww3_identity():
    e = NP.pre_override_entry(_row(0))
    assert e == {"valid_time": _vt(0), "wind_dir": 200.0, "wind_speed": 5.5, "wind_mult": 0.9,
                 "tide_mult": 0.95, "swell_source": "ww3", "swell_dp": 70.0, "swell_tp": 11.0}


def test_pre_override_entry_on_a_dirpw_hour_has_no_ww3_identity():
    e = NP.pre_override_entry(_row(0, swell_source="nwps", wind_mult=None, tide_mult=None))
    assert e["swell_source"] is None and "swell_dp" not in e and "swell_tp" not in e
    assert e["wind_mult"] == 1.0 and e["tide_mult"] == 1.0


def test_wind_was_nwps_within_rounding_only():
    assert NP.wind_was_nwps(_row(0), (5.5, 200.0))
    assert NP.wind_was_nwps(_row(0, wind_speed=5.5004), (5.5, 200.0))
    assert not NP.wind_was_nwps(_row(0, wind_speed=5.501), (5.5, 200.0))
    assert not NP.wind_was_nwps(_row(0, wind_dir=200.001), (5.5, 200.0))
    assert not NP.wind_was_nwps(_row(0), None)
    assert not NP.wind_was_nwps(_row(0, wind_speed=None), (5.5, 200.0))


WANT = {"hs": 1.235, "tp": 7.9, "dp": 75.0, "swell_hs": 1.0}


def test_input_diffs_all_four_values():
    assert NP.input_diffs(_row(0), WANT) == []
    assert NP.input_diffs(_row(0, hs=1.2354), WANT) == []
    assert NP.input_diffs(_row(0, hs=1.236), WANT) == ["hs"]
    assert NP.input_diffs(_row(0, tp=7.91), WANT) == ["tp"]
    assert NP.input_diffs(_row(0, dp=75.001), WANT) == ["dp"]
    assert NP.input_diffs(_row(0, swell_hs=1.001), WANT) == ["swell_hs"]


def test_input_diffs_an_empty_value_only_against_an_empty_one():
    assert NP.input_diffs(_row(0, swell_hs=None), dict(WANT, swell_hs=None)) == []
    assert NP.input_diffs(_row(0), dict(WANT, swell_hs=None)) == ["swell_hs"]
    assert NP.input_diffs(_row(0, swell_hs=None), WANT) == ["swell_hs"]
    assert NP.input_diffs(_row(0, hs=None), dict(WANT, hs=None)) == []
    assert NP.input_diffs(_row(0), dict(WANT, hs=None)) == ["hs"]


def test_expected_inputs_take_the_exact_hour_and_the_rated_shts():
    assert NP.expected_inputs((None, 7.9, 75.0), (1.1, 7.9, 75.0, 1.0)) == {
        "hs": None, "tp": 7.9, "dp": 75.0, "swell_hs": 1.0}
    assert NP.expected_inputs((1.2, 7.9, 75.0), None)["swell_hs"] is None


def test_face_differs_within_two_decimals():
    assert NP.face_differs(3.0, 3.004) is False and NP.face_differs(3.0, 3.006) is True
    assert NP.face_differs(None, None) is False and NP.face_differs(None, 3.0) is True


def test_replay_hands_the_override_the_series_and_the_factors():
    seen = {}

    def spy(ratings, spots, *, _fetch, ceiling_factors, median_factor):
        seen.update(ratings=ratings, spots=spots, series=_fetch(None),
                    cf=ceiling_factors, m=median_factor)

    entries = [{"valid_time": _vt(0)}]
    NP.replay(OLD, entries, {"s": 1}, {"Duck Pier": 1.4}, 1.59, apply=spy)
    assert seen["ratings"] == {"Duck Pier": entries} and seen["spots"] == [OLD]
    assert seen["series"] == {"s": 1} and seen["cf"] == {"Duck Pier": 1.4} and seen["m"] == 1.59


def test_gate_passes_only_with_hours_and_no_difference():
    ok = {"inputs_match": True, "stars_old": 2.0, "stars_published": 2.0,
          "face_published": 3.0, "face_old": 3.0}
    assert NP.gate([ok, ok]) == {"hours": 2, "input_differences": 0, "star_differences": 0,
                                 "face_differences": 0, "filled": 0, "pass": True}
    assert NP.gate([])["pass"] is False
    bad_in = dict(ok, inputs_match=False)
    assert NP.gate([ok, bad_in]) == {"hours": 2, "input_differences": 1, "star_differences": 0,
                                     "face_differences": 0, "filled": 0, "pass": False}
    bad_st = dict(ok, stars_old=2.5)
    assert NP.gate([bad_st, ok]) == {"hours": 2, "input_differences": 0, "star_differences": 1,
                                     "face_differences": 0, "filled": 0, "pass": False}
    bad_face = dict(ok, face_old=3.01)
    assert NP.gate([ok, bad_face]) == {"hours": 2, "input_differences": 0, "star_differences": 0,
                                       "face_differences": 1, "filled": 0, "pass": False}
    assert NP.gate([dict(ok, stars_old=None)])["star_differences"] == 1


def test_spot_summary_counts_and_means():
    def h(old, new, fo, fn, ho, hn):
        return {"spot": "A", "stars_old": old, "stars_new": new, "face_old": fo, "face_new": fn,
                "hs_old": ho, "hs_new": hn}
    got = NP.spot_summary([h(2.0, 2.5, 3.0, 4.0, 1.0, 1.5), h(3.0, 2.5, 4.0, 3.0, 2.0, 1.0),
                           h(2.0, 2.0, 2.0, 2.5, 1.0, None), h(None, 3.0, None, 5.0, 1.0, 1.0)])
    # stars old 2, 3, 2 -> 7/3 = 2.333; new 2.5, 2.5, 2, 3 -> 10/4 = 2.5
    # face old 3, 4, 2 -> 3.0; new 4, 3, 2.5, 5 -> 3.625; hs old 1, 2, 1, 1 -> 1.25; new 1.5, 1, 1
    assert got == {"A": {"hours": 4, "stars_up": 1, "stars_down": 1, "stars_same": 1,
                         "mean_stars_old": 2.333, "mean_stars_new": 2.5,
                         "mean_face_old": 3.0, "mean_face_new": 3.625,
                         "mean_hs_old": 1.25, "mean_hs_new": 1.167}}


def test_homepage_orders_by_stars_then_spot_id_and_shows_ten():
    names = {i: (f"S{i}", "NC") for i in range(1, 13)}
    first = {i: {"stars": 1.0 + 0.5 * (i % 3)} for i in range(1, 13)}
    # stars: i%3==2 -> 2.0 (2, 5, 8, 11), ==1 -> 1.5 (1, 4, 7, 10), ==0 -> 1.0 (3, 6, 9, 12)
    got = NP.homepage(first, names, {12: 4.0})
    assert [r["spot"] for r in got["before"]] == ["S2", "S5", "S8", "S11", "S1", "S4", "S7",
                                                   "S10", "S3", "S6"]
    assert [r["spot"] for r in got["after"]] == ["S12", "S2", "S5", "S8", "S11", "S1", "S4",
                                                  "S7", "S10", "S3"]
    assert got["after"][0] == {"spot": "S12", "state": "NC", "stars": 4.0, "moved": True}
    assert got["after"][1]["moved"] is False
    assert got["drop_out"] == ["S6"] and got["come_in"] == ["S12"]


# --------------------------------------------------------------------------- #
# One cycle replayed                                                           #
# --------------------------------------------------------------------------- #
def test_replay_cycle_old_and_new_node_and_the_wind_that_follows_the_node():
    rows = [_row(0), _row(1, wind_speed=9.0, wind_dir=90.0), _row(2, swell_hs=None)]
    hours, checks = NP.replay_cycle([(OLD, NEW)], {"Duck Pier": rows}, _cycle(), {}, None,
                                    apply=_stub)
    assert checks == {"Duck Pier": {"wet": True, "same_cell": True, "offset_km": 0.0}}
    assert [h["valid_time"] for h in hours] == [_vt(0), _vt(1), _vt(2)]
    # f000: stored wind 5.5 / 200 is the old node's NWPS wind, so the new node's 6.25 replaces it.
    assert hours[0]["wind"] == "nwps" and hours[0]["face_old"] == 5.5 and hours[0]["face_new"] == 6.25
    assert hours[0]["wind_old"] == [5.5, 200.0] and hours[0]["wind_new"] == [6.25, 210.0]
    # f001: the old node's NWPS wind is 7.0 there; 9.0 is another source's, kept at both nodes.
    assert hours[1]["wind"] == "other" and hours[1]["face_old"] == 9.0 and hours[1]["face_new"] == 9.0
    assert hours[1]["wind_old"] == [9.0, 90.0] and hours[1]["wind_new"] == [9.0, 90.0]
    for h in hours:
        assert h["stars_old"] == 1.235 and h["stars_new"] == 2.0
        assert h["hs_old"] == 1.235 and h["hs_new"] == 2.0
        assert h["tp_old"] == 7.9 and h["tp_new"] == 8.1
        assert h["inputs_match"] and h["stars_published"] == 1.235
        assert h["rated_tp_old"] == 11.0 and h["rated_tp_new"] == 11.0
    assert hours[2]["swell_hs_old"] is None and hours[2]["swell_hs_new"] == 1.8


def test_replay_cycle_flags_a_row_not_rated_from_the_old_node():
    hours, _ = NP.replay_cycle([(OLD, NEW)], {"Duck Pier": [_row(0, hs=2.0)]}, _cycle(), {},
                               None, apply=_stub)
    assert hours[0]["inputs_match"] is False


def test_replay_cycle_skips_a_spot_with_no_rows():
    hours, checks = NP.replay_cycle([(OLD, NEW)], {}, _cycle(), {}, None, apply=_stub)
    assert hours == [] and checks == {}


# --------------------------------------------------------------------------- #
# run(): the published rows, the cycles NOMADS holds, the homepage             #
# --------------------------------------------------------------------------- #
class _Query:
    def __init__(self, data):
        self.data, self.filters, self.n = data, {}, None

    def select(self, _cols):
        return self

    def eq(self, col, val):
        self.filters[col] = val
        return self

    def gte(self, col, val):
        self.filters["since"] = val
        return self

    def order(self, _col):
        return self

    def range(self, a, b):
        self.n = (a, b)
        return self

    def limit(self, n):
        self.n = (0, n - 1)
        return self

    def execute(self):
        rows = [r for r in self.data
                if all(r.get(c) == v for c, v in self.filters.items() if c != "since")
                and r.get("valid_time", "") >= self.filters.get("since", "")]
        a, b = self.n if self.n else (0, len(rows))
        return type("R", (), {"data": rows[a:b + 1]})()


class _Client:
    def __init__(self, spots, forecasts):
        self.spots, self.forecasts = spots, forecasts

    def table(self, name):
        return _Query(self.spots if name == "spots" else self.forecasts)


def _client():
    duck = [_row(0, source="nwps", face_ft_raw=5.5),
            _row(1, source="nwps", wind_speed=9.0, wind_dir=90.0, face_ft_raw=9.0),
            _row(2, source="nwps", swell_source="ww3"),
            _row(2, source="nwps", valid_time=_vt(3), nwps_cycle="2026-10-26T06:00:00Z"),
            _row(2, source="nwps", valid_time=_vt(4), stars=None)]
    other = [{"spot_id": 2, "source": "nwps", "valid_time": _vt(0), "stars": 1.5,
              "face_ft_raw": 2.75}]
    return _Client([{"id": 1, "name": "Duck Pier", "state": "NC"},
                    {"id": 2, "name": "Other", "state": "VA"}], duck + other)


def _run(apply=_stub):
    opened = []

    def open_cycle(wfo, cyc):
        opened.append((wfo, cyc))
        return _cycle()

    rep = NP.run(_client(), [OLD], [NEW], _vt(0), factors={},
                 list_cycles=lambda w: {("20261026", "12"): "url-12"}, open_cycle=open_cycle,
                 apply=apply)
    return rep, opened


def test_run_replays_the_hours_on_a_listed_cycle_and_counts_the_rest():
    rep, opened = _run()
    assert opened == [("mhx", ("20261026", "12", "url-12"))]
    assert rep["cycles"] == {"mhx": {"listed": ["2026102612"], "read": ["2026102612"]}}
    assert rep["not_replayed"] == {"Duck Pier": {
        "another rater (ww3)": 1, "cycle 2026-10-26T06:00:00Z not on NOMADS": 1, "no stars": 1}}
    assert [h["valid_time"] for h in rep["hours"]] == [_vt(0), _vt(1)]
    assert rep["gate"] == {"hours": 2, "input_differences": 0, "star_differences": 0,
                           "face_differences": 0, "filled": 0, "pass": True}
    assert rep["moved"] == [{"spot": "Duck Pier", "wfo": "mhx", "old": [36.19, -75.75],
                             "new": [36.19, -75.73], "new_distance_m": 2074}]
    assert rep["node_checks"] == {"Duck Pier": {"2026102612": {"wet": True, "same_cell": True,
                                                               "offset_km": 0.0}}}


def test_run_homepage_takes_the_moved_spots_soonest_hour_at_the_new_node():
    rep, _ = _run()
    hp = rep["homepage"]
    assert [(r["spot"], r["stars"]) for r in hp["before"]] == [("Other", 1.5), ("Duck Pier", 1.235)]
    assert [(r["spot"], r["stars"]) for r in hp["after"]] == [("Duck Pier", 2.0), ("Other", 1.5)]
    assert hp["moved_soonest_replayed"] == ["Duck Pier"]


def test_fetch_first_skips_an_hour_without_stars():
    c = _Client([], [{"spot_id": 3, "source": "nwps", "valid_time": _vt(0), "stars": None},
                     {"spot_id": 3, "source": "nwps", "valid_time": _vt(1), "stars": 2.5}])
    assert NP.fetch_first(c, 3, _vt(0))["valid_time"] == _vt(1)
    assert NP.fetch_first(c, 4, _vt(0)) is None


def test_run_homepage_uses_only_the_soonest_hours_replay():
    # Duck Pier's soonest hour (f000 here) is another rater's, so it is not replayed: the
    # homepage keeps its published stars even though f001 was replayed at the new node.
    c = _client()
    c.forecasts[0]["swell_source"] = "cdip_mop"
    rep = NP.run(c, [OLD], [NEW], _vt(0), factors={},
                 list_cycles=lambda w: {("20261026", "12"): "url-12"},
                 open_cycle=lambda w, cyc: _cycle(), apply=_stub)
    assert [h["valid_time"] for h in rep["hours"]] == [_vt(1)]
    assert rep["homepage"]["moved_soonest_replayed"] == []
    assert [(r["spot"], r["stars"]) for r in rep["homepage"]["after"]] == [
        ("Other", 1.5), ("Duck Pier", 1.235)]


def test_run_homepage_keeps_published_stars_when_the_soonest_hour_does_not_reproduce():
    def off_by_half(ratings, spots, **kw):
        _stub(ratings, spots, **kw)
        for e in ratings[spots[0]["name"]]:
            e["stars"] += 0.5 if spots[0]["nwps_node_lng"] == -75.75 else 0.0

    rep, _ = _run(off_by_half)
    assert rep["gate"]["star_differences"] == 2 and rep["gate"]["pass"] is False
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        NP.print_report(rep)
    assert ("0 input differences (hs, tp, dp, swell_hs), 2 star differences, 0 face differences, "
            "0 filled from a neighbouring hour (the hour blank in the file) -> FAIL ===") in buf.getvalue()
    assert (f"    Duck Pier {_vt(0)}: inputs ok (series hour the hour, hour blank in the file "
            "False); stars published 1.235 replayed 1.735; face published 5.50 replayed 5.50"
            ) in buf.getvalue()
    assert ("      stored   hs 1.235  tp 7.900  dp 75.000  swell_hs 1.000\n"
            "      expected hs 1.235  tp 7.900  dp 75.000  swell_hs 1.000") in buf.getvalue()
    assert rep["homepage"]["moved_soonest_replayed"] == []
    assert [(r["spot"], r["stars"]) for r in rep["homepage"]["after"]] == [
        ("Other", 1.5), ("Duck Pier", 1.235)]


# --------------------------------------------------------------------------- #
# Against the override itself                                                  #
# --------------------------------------------------------------------------- #
def _duck():
    path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "spots_enriched.json")
    with open(path) as f:
        return next(s for s in json.load(f) if s["name"] == "Duck Pier")


def test_the_gate_passes_on_hours_the_override_itself_wrote():
    # Publish three hours the way interpret does: rate_spot's entry, then the override reading
    # the OLD node's series, stored as db_import writes them. The gate must reproduce them.
    old = dict(_duck(), nwps_node_lat=36.19, nwps_node_lng=-75.75)
    new = dict(old, nwps_node_lng=-75.73)
    cyc = _cycle()
    series = NP.node_series(old, cyc)
    entries = [{"valid_time": _vt(fh), "swell_source": "ww3", "swell_dp": 70.0 + fh,
                "swell_tp": 11.0, "wind_speed": 5.5 if fh != 1 else 7.0, "wind_dir": 200.0,
                "wind_mult": 1.0, "tide_mult": 0.95} for fh in (0, 1, 2)]
    apply_nwps_overrides({old["name"]: entries}, [old], _fetch=lambda _s: series)
    rows = []
    for fh, e in zip((0, 1, 2), entries):
        swh, per, dpw, shts = series[_k(fh)]
        rows.append({"valid_time": e["valid_time"], "nwps_cycle": STAMP, "hs": swh, "tp": per,
                     "dp": dpw, "swell_hs": shts, "swell_source": e["swell_source"],
                     "swell_dp": e["swell_dp"], "swell_tp": e["swell_tp"],
                     "wind_speed": e["wind_speed"], "wind_dir": e["wind_dir"],
                     "wind_mult": e["wind_mult"], "tide_mult": e["tide_mult"],
                     "stars": e["stars"], "face_ft_raw": e["face_ft"]})
    hours, _ = NP.replay_cycle([(old, new)], {old["name"]: rows}, cyc, {}, None)
    assert NP.gate(hours) == {"hours": 3, "input_differences": 0, "star_differences": 0,
                              "face_differences": 0, "filled": 0, "pass": True}
    assert [h["face_old"] for h in hours] == [r["face_ft_raw"] for r in rows]
    assert all(h["stars_new"] is not None for h in hours)


def test_the_report_prints_the_gate_and_every_hour():
    import contextlib
    import io
    rep, _ = _run()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        NP.print_report(rep)
    out = buf.getvalue()
    assert ("=== MERGE GATE: the old points against the published window: 2 hours, "
            "0 input differences (hs, tp, dp, swell_hs), 0 star differences, 0 face differences, "
            "0 filled from a neighbouring hour (the hour blank in the file) -> PASS ===") in out
    assert "  Duck Pier (mhx): (36.19, -75.75) -> (36.19, -75.73), 2074 m from the spot" in out
    assert "  mhx: NOMADS lists 1 cycles 2026102612; read 2026102612" in out
    hours = [ln for ln in out.splitlines() if ln.startswith("  Duck Pier ") and "stars " in ln]
    assert len(hours) == 2 and all(ln.endswith("*") for ln in hours)
    assert "stars 1.2 -> 2.0  face 5.50 -> 6.25  hs 1.24 -> 2.00  tp 7.90 -> 8.10" in hours[0]
    assert "   1. Other (VA) 1.5" in out and "| Duck Pier (NC) 2.0 [moved]" in out


def test_the_hours_csv_has_one_row_per_replayed_hour(tmp_path):
    import csv as _csv
    rep, _ = _run()
    path = tmp_path / "h.csv"
    NP.write_csv(path, rep["hours"])
    with open(path) as f:
        rows = list(_csv.DictReader(f))
    assert [r["valid_time"] for r in rows] == [_vt(0), _vt(1)]
    assert rows[0]["stars_old"] == "1.235" and rows[0]["stars_new"] == "2.0"
    assert rows[0]["wind_new"] == "[6.25, 210.0]"


# --------------------------------------------------------------------------- #
# A blank hour: the override's own fill (nwps_nearshore.series_hour)           #
# --------------------------------------------------------------------------- #
def test_series_hour_takes_the_hour_then_the_one_before_then_the_one_after():
    from pipeline.forecast.nwps_nearshore import series_hour
    a, b, c = (1.0, 7.0, 90.0, 0.5), (2.0, 8.0, 95.0, 0.6), (3.0, 9.0, 100.0, None)
    assert series_hour({9: a, 10: b, 11: c}, 10) == (b, 0)
    assert series_hour({9: a, 11: c}, 10) == (a, -1)
    assert series_hour({11: c}, 10) == (c, 1)
    assert series_hour({8: a, 12: c}, 10) == (None, None)


def test_the_gate_and_the_override_fill_through_one_function():
    import inspect
    from pipeline.forecast import nwps_nearshore as NN
    assert NP.series_hour is NN.series_hour
    src = inspect.getsource(NN.apply_nwps_overrides)
    assert "series_hour(series, k)" in src and "series.get(k - 1)" not in src


def _gappy_cycle(present_f1=None):
    """The stand-in cycle with a fourth step, f003, and the OLD cell's swh by step:
    f000 1.1, f001 blank (NaN), f002 blank (NaN), f003 1.4. With *present_f1*, f001 holds that
    swh instead of a blank. perpw 7.9, dirpw 75.0 at every step; shts 1.0, NaN at f002 and f003
    (f003's fields are copies of f002's)."""
    c = _cycle()
    for short in ("swh", "perpw", "dirpw", "shts", "ws", "wdir"):
        c["fields"][(short, 3)] = c["fields"][(short, 2)].copy()
    c["steps"] = [0, 1, 2, 3]
    for fh, v in ((0, 1.1), (1, np.nan if present_f1 is None else present_f1), (2, np.nan),
                  (3, 1.4)):
        c["fields"][("swh", fh)][1, 1] = v
    return c


def _published(old, cyc, stored):
    """Publish hours f000..f003 through the override itself from the OLD node's series, with
    *stored* {fh: (hs, tp, dp)} as the stored inputs; swell_hs, stars and face as it wrote them."""
    series = NP.node_series(old, cyc)
    entries = [{"valid_time": _vt(fh), "swell_source": "ww3", "swell_dp": 70.0,
                "swell_tp": 11.0, "wind_speed": 5.5 if fh != 1 else 7.0, "wind_dir": 200.0,
                "wind_mult": 1.0, "tide_mult": 0.95} for fh in (0, 1, 2, 3)]
    apply_nwps_overrides({old["name"]: entries}, [old], _fetch=lambda _s: series)
    rows = []
    for fh, e in zip((0, 1, 2, 3), entries):
        hs, tp, dp = stored[fh]
        rows.append({"valid_time": e["valid_time"], "nwps_cycle": STAMP, "hs": hs, "tp": tp,
                     "dp": dp, "swell_hs": e.get("swell_hs"), "swell_source": e["swell_source"],
                     "swell_dp": e["swell_dp"], "swell_tp": e["swell_tp"],
                     "wind_speed": e["wind_speed"], "wind_dir": e["wind_dir"],
                     "wind_mult": e["wind_mult"], "tide_mult": e["tide_mult"],
                     "stars": e["stars"], "face_ft_raw": e["face_ft"]})
    return rows


def _duck_pair():
    old = dict(_duck(), nwps_node_lat=36.19, nwps_node_lng=-75.75)
    return old, dict(old, nwps_node_lng=-75.73)


# As the pipeline stores the four hours: hs, tp and dp are each hour's own values in the file
# (f001 and f002 have no swh, so their hs is empty; perpw 7.9 and dirpw 75.0 are there). f001 is
# rated from f000 (swell_hs 1.0), f002 from f003 (no shts, swell_hs empty).
FILLED = {0: (1.1, 7.9, 75.0), 1: (None, 7.9, 75.0), 2: (None, 7.9, 75.0), 3: (1.4, 7.9, 75.0)}


def test_a_blank_hour_filled_from_the_hour_before_and_one_from_the_hour_after_pass():
    old, new = _duck_pair()
    cyc = _gappy_cycle()
    rows = _published(old, cyc, FILLED)
    assert [r["swell_hs"] for r in rows] == [1.0, 1.0, None, None]
    hours, _ = NP.replay_cycle([(old, new)], {old["name"]: rows}, cyc, {}, None)
    assert [h["filled_from"] for h in hours] == [0, -1, 1, 0]
    assert [h["exact_hour_blank"] for h in hours] == [False, True, True, False]
    assert [h["hs_old"] for h in hours] == [1.1, 1.1, 1.4, 1.4]
    assert all(h["inputs_match"] for h in hours)
    assert hours[1]["expected"] == {"hs": None, "tp": 7.9, "dp": 75.0, "swell_hs": 1.0}
    assert hours[2]["expected"] == {"hs": None, "tp": 7.9, "dp": 75.0, "swell_hs": None}
    assert NP.gate(hours) == {"hours": 4, "input_differences": 0, "star_differences": 0,
                              "face_differences": 0, "filled": 2, "pass": True}


def test_a_blank_hour_stored_with_the_hour_before_s_height_fails():
    # f001 is blank in the file; a row carrying f000's height 1.1 there is not what the
    # pipeline writes (the hour's own, empty), so the gate fails on hs alone.
    old, new = _duck_pair()
    cyc = _gappy_cycle()
    rows = _published(old, cyc, {**FILLED, 1: (1.1, 7.9, 75.0)})
    hours, _ = NP.replay_cycle([(old, new)], {old["name"]: rows}, cyc, {}, None)
    assert hours[1]["filled_from"] == -1 and hours[1]["exact_hour_blank"] is True
    assert hours[1]["inputs_match"] is False and hours[1]["input_diffs"] == ["hs"]
    assert hours[1]["stored"]["hs"] == 1.1 and hours[1]["expected"]["hs"] is None
    assert NP.gate(hours) == {"hours": 4, "input_differences": 1, "star_differences": 0,
                              "face_differences": 0, "filled": 1, "pass": False}
    import contextlib
    import io
    rep = {"since": STAMP, "moved": [], "cycles": {}, "node_checks": {}, "gate": NP.gate(hours),
           "hours": hours, "not_replayed": {}, "by_spot": NP.spot_summary(hours),
           "homepage": {"before": [], "after": [], "drop_out": [], "come_in": [],
                        "moved_soonest_replayed": []}}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        NP.print_report(rep)
    out = buf.getvalue()
    assert (f"    Duck Pier {_vt(1)}: inputs DIFFER hs (series hour the hour before, hour blank "
            "in the file True)") in out
    assert ("      stored   hs 1.100  tp 7.900  dp 75.000  swell_hs 1.000\n"
            "      expected hs -  tp 7.900  dp 75.000  swell_hs 1.000") in out


def test_an_hour_the_file_holds_is_not_filled_even_when_the_row_carries_the_hour_before():
    # f001 holds 1.25 in the file; the row stores f000's 1.1 there. The gate compares with
    # 1.25, the hour itself, and fails. f002 is still blank and is rated from f001 (1.25).
    old, new = _duck_pair()
    cyc = _gappy_cycle(present_f1=1.25)
    rows = _published(old, cyc, {**FILLED, 1: (1.1, 7.9, 75.0)})
    hours, _ = NP.replay_cycle([(old, new)], {old["name"]: rows}, cyc, {}, None)
    assert hours[1]["filled_from"] == 0 and hours[1]["exact_hour_blank"] is False
    assert hours[1]["hs_old"] == 1.25 and hours[1]["inputs_match"] is False
    assert hours[1]["input_diffs"] == ["hs"] and hours[1]["expected"]["hs"] == 1.25
    assert NP.gate(hours) == {"hours": 4, "input_differences": 1, "star_differences": 0,
                              "face_differences": 0, "filled": 1, "pass": False}


def test_a_fill_over_an_hour_the_file_holds_is_a_difference(monkeypatch):
    # A series missing f001 while the file holds it there (1.25): the neighbour's values match
    # the row, but the hour is not blank in the file, so the gate does not take the fill.
    old, new = _duck_pair()
    cyc = _gappy_cycle(present_f1=1.25)
    rows = _published(old, _gappy_cycle(), FILLED)
    real = NP.node_series

    def without_f001(spot, cycle):
        got = real(spot, cycle)
        if spot is old:
            got.pop(_k(1))
        return got
    monkeypatch.setattr(NP, "node_series", without_f001)
    hours, _ = NP.replay_cycle([(old, new)], {old["name"]: rows}, cyc, {}, None)
    assert hours[1]["filled_from"] == -1 and hours[1]["exact_hour_blank"] is False
    assert hours[1]["inputs_match"] is False
    # f002 (blank) still comes from f003 and matches; the refused f001 is not counted as filled
    assert "fill over an hour the file holds" in hours[1]["input_diffs"]
    assert NP.gate(hours) == {"hours": 4, "input_differences": 1, "star_differences": 0,
                              "face_differences": 0, "filled": 1, "pass": False}


def test_exact_hour_blank_reads_the_file():
    c = _gappy_cycle()
    assert [NP.exact_hour_blank(c, (1, 1), CDT.timestamp() + 3600 * fh) for fh in (0, 1, 2, 3)] \
        == [False, True, True, False]
    assert NP.exact_hour_blank(c, (1, 1), CDT.timestamp() + 3600 * 4) is True   # no f004
    assert NP.exact_hour_blank(c, (1, 3), CDT.timestamp() + 3600) is False      # NEW cell
    c["fields"][("perpw", 0)][1, 1] = np.nan                       # swh there, perpw blank
    assert NP.exact_hour_blank(c, (1, 1), CDT.timestamp()) is True
    c = _gappy_cycle()
    c["fields"][("dirpw", 3)][1, 1] = np.nan                       # swh there, dirpw blank
    assert NP.exact_hour_blank(c, (1, 1), CDT.timestamp() + 3 * 3600) is True


def test_the_report_prints_the_filled_hours():
    import contextlib
    import io
    old, new = _duck_pair()
    cyc = _gappy_cycle()
    hours, _ = NP.replay_cycle([(old, new)], {old["name"]: _published(old, cyc, FILLED)}, cyc,
                               {}, None)
    rep = {"since": STAMP, "moved": [], "cycles": {}, "node_checks": {}, "gate": NP.gate(hours),
           "hours": hours, "not_replayed": {}, "by_spot": NP.spot_summary(hours),
           "homepage": {"before": [], "after": [], "drop_out": [], "come_in": [],
                        "moved_soonest_replayed": []}}
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        NP.print_report(rep)
    out = buf.getvalue()
    assert ("4 hours, 0 input differences (hs, tp, dp, swell_hs), 0 star differences, "
            "0 face differences, 2 filled from a neighbouring hour (the hour blank in the file) "
            "-> PASS ===") in out
    assert f"    filled: Duck Pier {_vt(1)} from the hour before" in out
    assert f"    filled: Duck Pier {_vt(2)} from the hour after" in out


# --------------------------------------------------------------------------- #
# Reference spots                                                              #
# --------------------------------------------------------------------------- #
def test_reference_summary_means_on_the_moved_spots_hours():
    hours = [{"spot": "A", "valid_time": "t1", "stars_old": 1.0, "stars_new": 2.0,
              "face_old": 1.0, "face_new": 4.0},
             {"spot": "A", "valid_time": "t2", "stars_old": 0.0, "stars_new": 3.0,
              "face_old": 0.5, "face_new": 5.0},
             {"spot": "A", "valid_time": "t3", "stars_old": 2.0, "stars_new": 2.0,
              "face_old": 2.0, "face_new": 2.0}]
    refs = {"Pier": {"t1": {"stars": 2.5, "face_ft_raw": 4.5}, "t2": {"stars": 1.5,
                                                                      "face_ft_raw": 3.5}}}
    # t3 has no reference row; over t1 and t2: old 0.5, new 2.5, ref 2.0; face 0.75, 4.5, 4.0
    assert NP.reference_summary(hours, refs) == {"A": {"Pier": {
        "hours": 2, "stars_old": 0.5, "stars_new": 2.5, "stars_reference": 2.0,
        "face_old": 0.75, "face_new": 4.5, "face_reference": 4.0}}}


def test_run_shows_the_reference_spots_on_the_moved_spots_hours():
    rep = NP.run(_client(), [OLD], [NEW], _vt(0), factors={},
                 list_cycles=lambda w: {("20261026", "12"): "url-12"},
                 open_cycle=lambda w, cyc: _cycle(), apply=_stub,
                 references=["other", "nowhere"])
    # Other has one published hour, f000, stars 1.5 and face 2.75. Duck Pier's f000 through the
    # stub: stars 1.235 at the old node, 2.0 at the new; face 5.5 and 6.25 (the NWPS winds).
    assert rep["references"] == {"Duck Pier": {"Other": {
        "hours": 1, "stars_old": 1.235, "stars_new": 2.0, "stars_reference": 1.5,
        "face_old": 5.5, "face_new": 6.25, "face_reference": 2.75}}}
    assert rep["references_missing"] == ["nowhere"]
    assert [h["reference"] for h in rep["hours"]] == [{"Other": {"stars": 1.5, "face": 2.75}}, {}]
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        NP.print_report(rep)
    line = next(ln for ln in buf.getvalue().splitlines()
                if ln.startswith("  Duck Pier") and _vt(0)[:16] in ln)
    assert line.endswith("wind nwps  | Other stars 1.5 face 2.75  *")


def test_an_hour_blank_only_in_its_period_is_filled_and_keeps_its_own_height():
    # f001 holds swh 1.25 but no perpw: the series leaves it out, so the override rates it from
    # f000. The pipeline stores the file's own hs 1.25 and dp 75.0, an empty tp, and f000's shts.
    old, new = _duck_pair()
    cyc = _gappy_cycle(present_f1=1.25)
    cyc["fields"][("perpw", 1)][1, 1] = np.nan
    rows = _published(old, cyc, {**FILLED, 1: (1.25, None, 75.0)})
    hours, _ = NP.replay_cycle([(old, new)], {old["name"]: rows}, cyc, {}, None)
    assert hours[1]["filled_from"] == -1 and hours[1]["exact_hour_blank"] is True
    assert hours[1]["expected"] == {"hs": 1.25, "tp": None, "dp": 75.0, "swell_hs": 1.0}
    assert hours[1]["inputs_match"] is True
    assert NP.gate(hours)["filled"] == 2 and NP.gate(hours)["pass"] is True
