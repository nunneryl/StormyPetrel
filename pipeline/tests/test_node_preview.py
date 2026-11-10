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
    assert rep["cycles"] == {"mhx": {"listed": ["2026102612"], "read": ["2026102612"],
                                     "files": {}}}
    assert rep["not_replayed"] == {"Duck Pier": {
        "another rater (ww3)": 1, "cycle 2026-10-26T06:00:00Z not on NOMADS": 1, "no stars": 1}}
    assert [h["valid_time"] for h in rep["hours"]] == [_vt(0), _vt(1)]
    assert rep["gate"] == {"hours": 2, "input_differences": 0, "star_differences": 0,
                           "face_differences": 0, "filled": 0, "pass": True,
                           "excluded_cycles": [], "excluded_land": []}
    assert rep["excluded"] == {} and rep["excluded_land"] == {}
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


# --------------------------------------------------------------------------- #
# Buoy references (SLUG=BUOY in --reference)                                   #
# --------------------------------------------------------------------------- #
def test_parse_references_splits_spots_from_buoys():
    assert NP.parse_references(" lido-beach,hubbard-creek=46015 , galveston-seawall = 42035X,,bad=") == (
        ["lido-beach"], {"hubbard-creek": "46015", "galveston-seawall": "42035x"})
    assert NP.parse_references("") == ([], {})


def test_buoy_pairs_takes_the_nearest_reading_within_30_minutes_of_a_past_hour():
    t0 = CDT.timestamp()
    hours = [{"valid_time": _vt(h)} for h in (0, 1, 2, 5)]
    obs = [(t0 + 70 * 60, 2.0), (t0 + 20 * 60, 1.0), (t0 + 5 * 3600, 9.9)]   # out of order
    NP.buoy_pairs(hours, obs, now_epoch=t0 + 3 * 3600)
    # 12Z: 20 min off -> 1.0. 13Z: 10 min off -> 2.0. 14Z: 50 min from 13:10 -> none.
    # 17Z: a reading at the very hour, but the hour is after "now" -> none.
    assert [h["buoy_hs"] for h in hours] == [1.0, 2.0, None, None]


def test_buoy_pairs_at_exactly_30_minutes_and_a_tie():
    t0 = CDT.timestamp()
    h = [{"valid_time": _vt(0)}]
    NP.buoy_pairs(h, [(t0 + 30 * 60, 1.5)], now_epoch=t0 + 3600)
    assert h[0]["buoy_hs"] == 1.5
    NP.buoy_pairs(h, [(t0 + 31 * 60, 1.5)], now_epoch=t0 + 3600)
    assert h[0]["buoy_hs"] is None
    NP.buoy_pairs(h, [(t0 + 20 * 60, 2.5), (t0 - 20 * 60, 0.5)], now_epoch=t0 + 3600)
    assert h[0]["buoy_hs"] == 0.5                             # the earlier of two equally near


def test_buoy_summary_counts_only_hours_the_cell_holds():
    hours = [
        {"buoy_hs": 2.0, "hs_old": 1.0, "filled_from": 0, "hs_new": 2.2, "filled_from_new": 0},
        {"buoy_hs": 1.0, "hs_old": 0.9, "filled_from": 0, "hs_new": 1.3, "filled_from_new": 0},
        {"buoy_hs": 1.0, "hs_old": 0.5, "filled_from": -1, "hs_new": 1.0, "filled_from_new": 0},
        {"buoy_hs": None, "hs_old": 1.0, "filled_from": 0, "hs_new": 1.0, "filled_from_new": 0},
    ]
    # old: 0.5 and 0.9 (the filled hour left out): median 0.7, one of two within 20%.
    # new: 1.1, 1.3, 1.0: median 1.1, two of three within 20%. Buoy mean (2 + 1 + 1) / 3.
    assert NP.buoy_summary(hours, "46015") == {
        "buoy": "46015", "hours": 3, "buoy_mean": 1.333,
        "old": {"hours": 2, "median": 0.7, "within_20": 0.5},
        "new": {"hours": 3, "median": 1.1, "within_20": 0.667}}
    assert NP.buoy_summary([], "46015")["old"] == {"hours": 0, "median": None, "within_20": None}


def test_run_with_a_buoy_replays_from_48_hours_back_and_sets_the_buoy_beside_both_nodes():
    t0 = CDT.timestamp()
    asked = []

    def buoy_obs(b, start):
        asked.append((b, start))
        return [(t0, 2.0), (t0 + 3600 + 600, 4.0)]

    rep = NP.run(_client(), [OLD], [NEW], _vt(0), factors={},
                 list_cycles=lambda w: {("20261026", "12"): "url-12"},
                 open_cycle=lambda w, c: _cycle(), apply=_stub,
                 buoys={"duck-pier": "44100", "nowhere": "41001"},
                 now=CDT + datetime.timedelta(hours=3), buoy_obs=buoy_obs)
    assert rep["replayed_from"] == "2026-10-24T12:00:00+00:00"
    assert asked == [("44100", "2026-10-24T12:00:00+00:00")]
    # 12Z and 13Z replay: old node 1.235 m, new 2.0 m; buoy 2.0 and 4.0.
    # old 0.6175 and 0.30875: median 0.463, none within 20%. new 1.0 and 0.5: median 0.75, one.
    assert rep["buoys"] == {"Duck Pier": {
        "buoy": "44100", "hours": 2, "buoy_mean": 3.0,
        "old": {"hours": 2, "median": 0.463, "within_20": 0.0},
        "new": {"hours": 2, "median": 0.75, "within_20": 0.5}}}
    assert rep["buoys_missing"] == ["nowhere"]
    assert [h["buoy_hs"] for h in rep["hours"]] == [2.0, 4.0]


def test_run_without_a_buoy_replays_from_since():
    rep, _ = _run()
    assert rep["replayed_from"] == rep["since"] == _vt(0)
    assert rep["buoys"] == {} and rep["buoys_missing"] == []


def test_with_a_buoy_the_rows_come_from_48_hours_back_and_the_homepage_from_since(monkeypatch):
    seen = {"rows": [], "first": set()}
    real_rows, real_first = NP.fetch_rows, NP.fetch_first

    def rows(client, sid, since):
        seen["rows"].append((sid, since))
        return real_rows(client, sid, since)

    def first(client, sid, since):
        seen["first"].add(since)
        return real_first(client, sid, since)

    monkeypatch.setattr(NP, "fetch_rows", rows)
    monkeypatch.setattr(NP, "fetch_first", first)
    NP.run(_client(), [OLD], [NEW], _vt(0), factors={},
           list_cycles=lambda w: {("20261026", "12"): "url-12"},
           open_cycle=lambda w, c: _cycle(), apply=_stub, references=["other"],
           buoys={"duck-pier": "44100"}, now=CDT + datetime.timedelta(hours=3),
           buoy_obs=lambda b, start: [])
    back = "2026-10-24T12:00:00+00:00"
    assert seen["rows"] == [(1, back), (2, back)]          # the moved spot, then the reference
    assert seen["first"] == {_vt(0)}


# --------------------------------------------------------------------------- #
# A cell on the wet/dry line: blank at f000, wet afterwards (run 68)           #
# --------------------------------------------------------------------------- #
def _dry_at_f000():
    """_cycle() with the old node's cell (1, 1) blank in every field at f000 only, as a cell
    that dries at low water reads in a cycle starting at low tide. The f000 land mask then
    calls it land; its later hours still carry OLD_CELL's values."""
    c = _cycle()
    for short in ("swh", "perpw", "dirpw", "shts", "ws", "wdir"):
        c["fields"][(short, 0)][1, 1] = np.nan
    c["mask"] = np.isnan(c["fields"][("swh", 0)])
    return c


def test_a_cell_blank_at_f000_is_still_the_cell_replayed():
    c = _dry_at_f000()
    assert bool(c["mask"][1, 1]) is True                       # the f000 mask calls it land
    assert NP.node_cell(c, OLD) == (1, 1)
    # f001 and f002 from the cell itself (OLD_CELL, swh 1.23456 -> 1.235); f000 left out,
    # as nwps.fetch's record leaves a blank hour out; not a neighbour's 0.5.
    assert NP.node_series(OLD, c) == {_k(1): (1.235, 7.9, 75.0, 1.0),
                                      _k(2): (1.235, 7.9, 75.0, None)}
    assert NP.exact_hour_blank(c, (1, 1), CDT.timestamp()) is True
    assert NP.exact_hour_blank(c, (1, 1), (CDT + datetime.timedelta(hours=1)).timestamp()) is False


def test_node_check_on_a_cell_blank_at_f000():
    # 36.19 N, 75.75 W is cell (1, 1): dry by the f000 mask, but the cell replayed.
    assert NP.node_check(_dry_at_f000(), 36.19, -75.75) == {"wet": False, "same_cell": True,
                                                            "offset_km": 0.0}


def test_a_cell_with_no_wave_value_at_any_step_falls_back_to_the_nearest_wet_cell():
    # (1, 1) land in every step: the nearest cell wet at f000. 0.01 deg of longitude at 36.19 N
    # is 6371 x pi/180 x 0.01 x cos(36.19) = 0.897 km, nearer than 0.01 deg of latitude
    # (1.112 km), so (1, 0) and (1, 2) tie; the first in grid order, (1, 0).
    c = _cycle(land=[(1, 1)])
    assert NP.node_cell(c, OLD) == (1, 0)
    assert NP.node_series(OLD, c)[_k(0)] == (0.5, 0.5, 0.5, 0.5)


def test_wind_alone_is_not_water():
    # (1, 1) has no wave value at any step but keeps its wind: nwps._cell_is_water tests wave
    # variables only, so it is land and the nearest cell wet at f000, (1, 0), is replayed.
    c = _cycle()
    for fh in (0, 1, 2):
        for short in ("swh", "perpw", "dirpw", "shts"):
            c["fields"][(short, fh)][1, 1] = np.nan
    c["mask"] = np.isnan(c["fields"][("swh", 0)])
    assert NP.node_cell(c, OLD) == (1, 0)


def test_an_hour_without_a_direction_is_left_out():
    c = _cycle()
    c["fields"][("dirpw", 1)][1, 1] = np.nan
    assert sorted(NP.node_series(OLD, c)) == [_k(0), _k(2)]


# --- The cycle files: NOMADS's Last-Modified and size beside the file read --------------------
_CHECKED = datetime.datetime(2026, 11, 8, 19, 3, 4, tzinfo=datetime.timezone.utc)


def test_an_http_date_in_utc():
    assert NP.http_date_iso("Sun, 08 Nov 2026 05:12:33 GMT") == "2026-11-08T05:12:33Z"
    # 06:12:33 at +01:00 is 05:12:33 UTC; 23:30 at -05:00 on Nov 7 is 04:30 UTC on Nov 8
    assert NP.http_date_iso("Sun, 08 Nov 2026 06:12:33 +0100") == "2026-11-08T05:12:33Z"
    assert NP.http_date_iso("Sat, 07 Nov 2026 23:30:00 -0500") == "2026-11-08T04:30:00Z"
    assert NP.http_date_iso(None) is None
    assert NP.http_date_iso("") is None
    assert NP.http_date_iso("not a date") is None


def test_a_file_stamp_beside_the_file_read(tmp_path):
    f = tmp_path / "c.grib2"
    f.write_bytes(b"GRIB" + b"x" * 8)                       # 12 bytes
    seen = []

    def head(url):
        seen.append(url)
        return {"Last-Modified": "Sun, 08 Nov 2026 05:12:33 GMT", "Content-Length": "12"}
    assert NP.file_stamp("u-00", str(f), head=head, now=_CHECKED) == {
        "url": "u-00", "checked_at": "2026-11-08T19:03:04Z", "size_read": 12,
        "last_modified": "2026-11-08T05:12:33Z", "content_length": 12}
    assert seen == ["u-00"]


def test_a_file_stamp_without_the_headers_or_the_file(tmp_path):
    got = NP.file_stamp("u", str(tmp_path / "none.grib2"), head=lambda u: {}, now=_CHECKED)
    assert got == {"url": "u", "checked_at": "2026-11-08T19:03:04Z", "size_read": None,
                   "last_modified": None, "content_length": None}
    got = NP.file_stamp("u", None, head=lambda u: {"Content-Length": "12x"}, now=_CHECKED)
    assert got["content_length"] is None


def test_a_failed_head_is_recorded_not_raised(tmp_path):
    f = tmp_path / "c.grib2"
    f.write_bytes(b"GRIB")

    def head(url):
        raise OSError("timed out")
    assert NP.file_stamp("u", str(f), head=head, now=_CHECKED) == {
        "url": "u", "checked_at": "2026-11-08T19:03:04Z", "size_read": 4,
        "last_modified": None, "content_length": None, "error": "OSError: timed out"}


def test_the_stamp_of_a_cycle_is_taken_of_the_file_load_cycle_wrote(monkeypatch):
    got = []
    monkeypatch.setattr(NP, "file_stamp", lambda url, path: got.append((url, path)) or "st")
    assert NP.stat_nomads_cycle("mfr", ("20261108", "00", "https://n/mfr.grib2")) == "st"
    assert got == [("https://n/mfr.grib2", "/tmp/nwps_mfr_20261108_00_CG1.grib2")]


def test_run_records_each_cycle_files_stamp_once():
    calls = []

    def stat(wfo, cyc):
        calls.append((wfo, cyc))
        return {"last_modified": "2026-10-26T15:00:00Z"}
    rep = NP.run(_client(), [OLD], [NEW], _vt(0), factors={},
                 list_cycles=lambda w: {("20261026", "12"): "url-12"},
                 open_cycle=lambda w, cyc: _cycle(), apply=_stub, stat_cycle=stat)
    assert calls == [(NEW["nwps_wfo"], ("20261026", "12", "url-12"))]
    assert rep["cycles"][NEW["nwps_wfo"]]["files"] == {
        "2026102612": {"last_modified": "2026-10-26T15:00:00Z"}}
    rep = NP.run(_client(), [OLD], [NEW], _vt(0), factors={},
                 list_cycles=lambda w: {("20261026", "12"): "url-12"},
                 open_cycle=lambda w, cyc: _cycle(), apply=_stub)
    assert rep["cycles"][NEW["nwps_wfo"]]["files"] == {}


def test_the_file_lines():
    base = {"checked_at": "2026-11-08T19:03:04Z", "last_modified": "2026-11-08T05:12:33Z"}
    assert NP.file_line("mfr", "2026110800", {**base, "content_length": 12, "size_read": 12}) == (
        "mfr 2026110800: last modified 2026-11-08T05:12:33Z, 12 bytes on NOMADS; read 12 bytes "
        "at 2026-11-08T19:03:04Z; same size")
    assert NP.file_line("mfr", "2026110800", {**base, "content_length": 13, "size_read": 12}) == (
        "mfr 2026110800: last modified 2026-11-08T05:12:33Z, 13 bytes on NOMADS; read 12 bytes "
        "at 2026-11-08T19:03:04Z; SIZE DIFFERS from the file read")
    assert NP.file_line("eka", "2026110812", {"checked_at": "2026-11-08T19:03:04Z",
                                              "last_modified": None, "content_length": None,
                                              "size_read": None}) == (
        "eka 2026110812: last modified not given, size unknown on NOMADS; read size unknown "
        "at 2026-11-08T19:03:04Z")
    assert NP.file_line("okx", "2026110800", {"checked_at": "2026-11-08T19:03:04Z",
                                              "size_read": 4, "error": "OSError: timed out"}) == (
        "okx 2026110800: NOMADS HEAD failed (OSError: timed out); read 4 bytes "
        "at 2026-11-08T19:03:04Z")


def test_the_report_prints_the_file_lines():
    import contextlib
    import io
    rep = NP.run(_client(), [OLD], [NEW], _vt(0), factors={},
                 list_cycles=lambda w: {("20261026", "12"): "url-12"},
                 open_cycle=lambda w, cyc: _cycle(), apply=_stub,
                 stat_cycle=lambda w, c: {"checked_at": "2026-11-08T19:03:04Z",
                                          "last_modified": "2026-10-26T15:00:00Z",
                                          "content_length": 7, "size_read": 7})
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        NP.print_report(rep)
    assert (f"    {NEW['nwps_wfo']} 2026102612: last modified 2026-10-26T15:00:00Z, 7 bytes on "
            "NOMADS; read 7 bytes at 2026-11-08T19:03:04Z; same size") in buf.getvalue().splitlines()


def test_main_stamps_the_cycle_files(monkeypatch, tmp_path):
    import sys
    import types
    seen = {}
    monkeypatch.setitem(sys.modules, "supabase",
                        types.SimpleNamespace(create_client=lambda url, key: "client"))
    monkeypatch.setenv("SUPABASE_URL", "u")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    monkeypatch.setattr(NP, "run", lambda *a, **kw: seen.update(kw) or {"gate": {"pass": True},
                                                                        "hours": []})
    monkeypatch.setattr(NP, "print_report", lambda rep: None)
    b = tmp_path / "b.json"
    b.write_text("[]")
    assert NP.main(["--baseline", str(b), "--roster", str(b), "--since", "2026-11-08T19:00:00Z"]) == 0
    assert seen["stat_cycle"] is NP.stat_nomads_cycle


def test_the_stamp_asks_nomads_for_the_headers_only(monkeypatch):
    # A HEAD, not a GET: a CG1 file is tens of megabytes and has just been downloaded.
    import urllib.request
    sent = []

    class _Resp:
        headers = {"Last-Modified": "Sun, 08 Nov 2026 05:12:33 GMT"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def urlopen(req, timeout):
        sent.append((req.get_method(), req.full_url, req.get_header("User-agent"), timeout))
        return _Resp()
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    assert NP.nomads_head("https://n/f.grib2") == {"Last-Modified": "Sun, 08 Nov 2026 05:12:33 GMT"}
    assert sent == [("HEAD", "https://n/f.grib2", "stormy-petrel-nwps", 60)]


# --- The double-post rule: a cycle NOMADS serves in two versions is kept out of the gate -------
_PUB = "NWPSPUB "


def _pub(**kw):
    rec = {"kind": "file", "status": 200, "wfo": "mfr", "date": "20261107", "cycle": "12"}
    rec.update(kw)
    return "log\tLog publication times\t2026-11-07T22:24:06Z " + _PUB + json.dumps(rec) + "\n"


# Run 70's MFR 2026-11-07 12Z, as the logger saw it (docs/face_validation/run-70/).
_A = dict(bytes=55879399, last_modified="2026-11-07T19:07:09Z")
_B = dict(bytes=43017313, last_modified="2026-11-07T20:01:05Z")


def test_the_logger_lines_give_each_version_once_in_the_order_first_seen():
    lines = [_pub(checked_at="2026-11-08T06:48:06Z", **_B),
             _pub(checked_at="2026-11-07T22:24:06Z", **_A),
             _pub(checked_at="2026-11-08T18:11:02Z", **_B),
             _pub(checked_at="2026-11-08T22:35:15Z", **_B),
             _pub(checked_at="2026-11-07T22:20:15Z", wfo="eka", bytes=12492532,
                  last_modified="2026-11-07T18:40:05Z"),
             _pub(checked_at="2026-11-07T22:23:00Z", kind="listing", bytes=1,
                  last_modified="2026-11-07T22:00:00Z"),                           # not a file
             _pub(checked_at="2026-11-07T22:23:01Z", status=403, **_A),            # refused
             _pub(checked_at="2026-11-07T22:23:02Z", bytes=None,
                  last_modified="2026-11-07T19:00:00Z"),                           # no size
             "log\tstep\t" + _PUB + "{not json\n",
             "an ordinary log line\n"]
    assert NP.parse_logger_lines(lines) == {
        ("mfr", "2026110712"): [
            {"bytes": 55879399, "last_modified": "2026-11-07T19:07:09Z",
             "first_seen": "2026-11-07T22:24:06Z"},
            {"bytes": 43017313, "last_modified": "2026-11-07T20:01:05Z",
             "first_seen": "2026-11-08T06:48:06Z"}],
        ("eka", "2026110712"): [
            {"bytes": 12492532, "last_modified": "2026-11-07T18:40:05Z",
             "first_seen": "2026-11-07T22:20:15Z"}]}


def test_a_later_sighting_does_not_move_first_seen_and_an_earlier_one_does():
    lines = [_pub(checked_at="2026-11-08T06:48:06Z", **_B),
             _pub(checked_at="2026-11-08T22:35:15Z", **_B),
             _pub(checked_at="2026-11-08T01:00:00Z", **_B)]
    assert NP.parse_logger_lines(lines)[("mfr", "2026110712")] == [
        {"bytes": 43017313, "last_modified": "2026-11-07T20:01:05Z",
         "first_seen": "2026-11-08T01:00:00Z"}]


_V2 = [{"bytes": 55879399, "last_modified": "2026-11-07T19:07:09Z", "first_seen": "2026-11-07T22:24:06Z"},
       {"bytes": 43017313, "last_modified": "2026-11-07T20:01:05Z", "first_seen": "2026-11-08T06:48:06Z"}]
_STAMP70 = {"size_read": 55879399, "content_length": 43017313,
            "last_modified": "2026-11-07T20:01:05Z", "checked_at": "2026-11-08T21:15:59Z"}
_SIZE_REASON = ("downloaded 55879399 bytes; NOMADS's HEAD gave 43017313 bytes, last modified "
                "2026-11-07T20:01:05Z")
_LOGGER_REASON = ("the publication logger saw 2 versions: 55879399 bytes, last modified "
                  "2026-11-07T19:07:09Z (first seen 2026-11-07T22:24:06Z); 43017313 bytes, last "
                  "modified 2026-11-07T20:01:05Z (first seen 2026-11-08T06:48:06Z)")


def test_the_reasons_a_cycle_is_double_posted():
    assert NP.double_post_reasons(_STAMP70, _V2) == [_SIZE_REASON, _LOGGER_REASON]
    assert NP.double_post_reasons(_STAMP70, None) == [_SIZE_REASON]
    assert NP.double_post_reasons(None, _V2) == [_LOGGER_REASON]
    # run 70's MFR 2026-11-08 00Z: one size, two last-modified times, by the logger alone
    same_size = [{"bytes": 43017415, "last_modified": "2026-11-08T03:37:29Z", "first_seen": "a"},
                 {"bytes": 43017415, "last_modified": "2026-11-08T07:05:08Z", "first_seen": "b"}]
    assert len(NP.double_post_reasons({"size_read": 43017415, "content_length": 43017415},
                                      same_size)) == 1


def test_one_version_and_matching_sizes_are_not_double_posted():
    assert NP.double_post_reasons({"size_read": 12, "content_length": 12}, _V2[:1]) == []
    assert NP.double_post_reasons(None, None) == []
    assert NP.double_post_reasons({"size_read": None, "content_length": 12}, []) == []
    assert NP.double_post_reasons({"size_read": 12, "content_length": None}, None) == []
    assert NP.double_post_reasons({"error": "OSError: timed out", "size_read": 12}, None) == []


def _stars5(ratings, spots, *, _fetch, ceiling_factors, median_factor):
    """A rater that disagrees with every published hour: stars 5.0."""
    _stub(ratings, spots, _fetch=_fetch, ceiling_factors=ceiling_factors,
          median_factor=median_factor)
    for e in ratings[spots[0]["name"]]:
        e["stars"] = 5.0


def _run_with(stat=None, logger=None, apply=_stub):
    return NP.run(_client(), [OLD], [NEW], _vt(0), factors={},
                  list_cycles=lambda w: {("20261026", "12"): "url-12"},
                  open_cycle=lambda w, cyc: _cycle(), apply=apply, stat_cycle=stat,
                  logger_versions=logger)


def test_a_double_posted_cycle_is_kept_out_of_the_gate_and_reported_apart():
    # Duck Pier's two replayed hours are on mhx 2026-10-26 12Z; with stars 5.0 both differ.
    rep = _run_with(logger={("mhx", "2026102612"): _V2}, apply=_stars5)
    assert rep["excluded"] == {"mhx 2026102612": {
        "reasons": [_LOGGER_REASON], "hours": 2, "input_differences": 0, "star_differences": 2,
        "face_differences": 0, "filled": 0}}
    assert rep["gate"] == {"hours": 0, "input_differences": 0, "star_differences": 0,
                           "face_differences": 0, "filled": 0, "pass": False,
                           "excluded_cycles": ["mhx 2026102612"], "excluded_land": []}
    assert [(h["cycle_file"], h["gate_excluded"]) for h in rep["hours"]] == [
        ("mhx 2026102612", True), ("mhx 2026102612", True)]
    assert rep["logger_log"] is True
    # still in the per-spot figures
    assert rep["by_spot"]["Duck Pier"]["hours"] == 2


def test_a_size_mismatch_alone_excludes_the_cycle():
    rep = _run_with(stat=lambda w, c: dict(_STAMP70))
    assert rep["excluded"]["mhx 2026102612"]["reasons"] == [_SIZE_REASON]
    assert rep["gate"]["hours"] == 0
    assert rep["logger_log"] is False


def test_a_single_version_cycle_stays_in_the_gate():
    rep = _run_with(stat=lambda w, c: {"size_read": 7, "content_length": 7},
                    logger={("mhx", "2026102612"): _V2[:1], ("mfr", "2026110712"): _V2},
                    apply=_stars5)
    assert rep["excluded"] == {}
    assert rep["gate"]["hours"] == 2 and rep["gate"]["star_differences"] == 2
    assert rep["gate"]["pass"] is False
    assert [h["gate_excluded"] for h in rep["hours"]] == [False, False]


def test_the_report_names_the_excluded_cycle_and_its_evidence():
    import contextlib
    import io
    rep = _run_with(stat=lambda w, c: dict(_STAMP70), logger={("mhx", "2026102612"): _V2},
                    apply=_stars5)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        NP.print_report(rep)
    lines = buf.getvalue().splitlines()
    i = lines.index("  EXCLUDED from the gate, double-posted on NOMADS: mhx 2026102612: 2 hours, "
                    "0 input, 2 star and 0 face differences")
    assert lines[i + 1:i + 3] == ["    - " + _SIZE_REASON, "    - " + _LOGGER_REASON]
    assert any(ln.startswith(f"    Duck Pier {_vt(0)} [mhx 2026102612, excluded]: inputs ok")
               for ln in lines)
    rep = _run_with()
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        NP.print_report(rep)
    assert "  (no publication-logger log given: only the size test applied)" in buf.getvalue()


def test_main_reads_the_logger_log(monkeypatch, tmp_path):
    import sys
    import types
    seen = {}
    monkeypatch.setitem(sys.modules, "supabase",
                        types.SimpleNamespace(create_client=lambda url, key: "client"))
    monkeypatch.setenv("SUPABASE_URL", "u")
    monkeypatch.setenv("SUPABASE_SERVICE_KEY", "k")
    monkeypatch.setattr(NP, "run", lambda *a, **kw: seen.update(kw) or {"gate": {"pass": False},
                                                                        "hours": []})
    monkeypatch.setattr(NP, "print_report", lambda rep: None)
    b = tmp_path / "b.json"
    b.write_text("[]")
    log = tmp_path / "pub.log"
    log.write_text(_pub(checked_at="2026-11-07T22:24:06Z", **_A)
                   + _pub(checked_at="2026-11-08T06:48:06Z", **_B))
    assert NP.main(["--baseline", str(b), "--roster", str(b), "--logger-log", str(log)]) == 1
    assert seen["logger_versions"] == {("mfr", "2026110712"): _V2}
    seen.clear()
    NP.main(["--baseline", str(b), "--roster", str(b)])
    assert seen["logger_versions"] is None


# --- Two sightings are two posts only when the size differs or the times are 10+ minutes apart
# Every file the publication logger saw in more than one (size, last modified) pair over its
# cycles of 2026-10-30 to 11-09 (runs 141, 143, 145, 147, 149, 151 to 154 and 156), copied
# from the NWPSPUB lines: (office, cycle, the date of the last-modified times, [(bytes, time)]).
_SEEN_TWICE = [
    ("akq", "2026110206", "2026-11-02", [(61941847, "07:50:56"), (61941847, "07:50:57")]),
    ("alu", "2026110118", "2026-11-01", [(39355582, "20:44:57"), (39355582, "20:44:58")]),
    ("bro", "2026110112", "2026-11-01", [(47673172, "18:36:53"), (47673172, "18:36:54")]),
    ("bro", "2026110200", "2026-11-02", [(47674180, "06:38:46"), (47674180, "06:38:47")]),
    ("crp", "2026110100", "2026-11-01", [(64069438, "06:36:51"), (64069438, "06:36:52")]),
    ("crp", "2026110200", "2026-11-02", [(64070806, "06:36:45"), (64070806, "06:36:52")]),
    ("eka", "2026110112", "2026-11-01", [(9541640, "18:47:46"), (9541640, "18:47:59")]),
    ("eka", "2026110312", "2026-11-03", [(12492448, "18:38:41"), (9541400, "19:04:10")]),
    ("gum", "2026110100", "2026-11-01", [(25506549, "06:54:15"), (25506549, "06:54:16")]),
    ("gyx", "2026110100", "2026-11-01", [(22880214, "07:07:16"), (22880214, "07:07:17")]),
    ("hgx", "2026110100", "2026-11-01", [(86386333, "06:43:48"), (86386333, "06:43:52")]),
    ("hgx", "2026110112", "2026-11-01", [(65423088, "16:46:06"), (65423088, "16:46:07")]),
    ("hgx", "2026110200", "2026-11-02", [(65435033, "03:36:42"), (65435033, "03:36:43")]),
    ("hgx", "2026110300", "2026-11-03", [(65431170, "00:24:47"), (86370285, "06:45:02")]),
    ("ilm", "2026110200", "2026-11-02", [(27902021, "04:20:23"), (27902021, "04:20:24")]),
    ("jax", "2026110200", "2026-11-02", [(43670377, "03:49:42"), (43670377, "03:50:10")]),
    ("key", "2026110100", "2026-11-01", [(25552889, "07:22:28"), (25552889, "07:22:30")]),
    ("key", "2026110112", "2026-11-01", [(25560016, "19:23:54"), (25560016, "19:23:49")]),
    ("lch", "2026110100", "2026-11-01", [(84441285, "06:41:55"), (84441285, "06:42:46")]),
    ("lch", "2026110112", "2026-11-01", [(84428460, "18:42:44"), (84428460, "18:42:43")]),
    ("lch", "2026110200", "2026-11-02", [(84445554, "06:42:19"), (84445554, "06:42:21")]),
    ("lix", "2026110100", "2026-11-01", [(87709773, "06:47:29"), (87709773, "06:47:42")]),
    ("lix", "2026110112", "2026-11-01", [(87704706, "18:50:04"), (87704706, "18:50:14")]),
    ("lix", "2026110200", "2026-11-02", [(87709906, "06:47:33"), (87709906, "06:47:56")]),
    ("lox", "2026110112", "2026-11-01", [(24144213, "18:52:51"), (24144213, "18:52:52")]),
    ("lox", "2026110200", "2026-11-02", [(24144245, "06:52:43"), (24144245, "06:52:42")]),
    ("mfl", "2026110100", "2026-11-01", [(105676419, "07:39:51"), (105676419, "07:39:48")]),
    ("mfl", "2026110112", "2026-11-01", [(105676765, "19:39:58"), (105676765, "19:39:57")]),
    ("mfl", "2026110200", "2026-11-02", [(105676442, "07:41:14"), (105676442, "07:41:19")]),
    ("mfr", "2026110200", "2026-11-02", [(43017007, "03:33:47"), (43017007, "03:33:48")]),
    ("mfr", "2026110712", "2026-11-07", [(55879399, "19:07:09"), (43017313, "20:01:05")]),
    ("mfr", "2026110800", "2026-11-08", [(43017415, "03:37:29"), (43017415, "07:05:08")]),
    ("mlb", "2026110200", "2026-11-02", [(67898283, "04:33:53"), (67898283, "04:33:57")]),
    ("mob", "2026110100", "2026-11-01", [(53729500, "06:42:51"), (53729500, "06:42:59")]),
    ("mob", "2026110112", "2026-11-01", [(53710518, "18:43:10"), (53710518, "18:43:11")]),
    ("mob", "2026110200", "2026-11-02", [(41961996, "04:32:34"), (41961996, "04:32:36")]),
    ("mtr", "2026110112", "2026-11-01", [(64314035, "18:52:51"), (64314035, "18:52:52")]),
    ("mtr", "2026110200", "2026-11-02", [(64313867, "06:52:26"), (64313867, "06:52:27")]),
    ("okx", "2026110200", "2026-11-02", [(25876461, "04:22:44"), (25876461, "04:23:03")]),
    ("phi", "2026110106", "2026-11-01", [(31381689, "10:03:19"), (31381689, "10:03:21")]),
    ("phi", "2026110200", "2026-11-02", [(31401585, "04:03:39"), (31401585, "04:04:41")]),
    ("phi", "2026110206", "2026-11-02", [(31397510, "10:02:50"), (31397510, "10:03:03")]),
    ("pqr", "2026110100", "2026-11-01", [(44700762, "06:45:19"), (44700762, "06:45:20")]),
    ("pqr", "2026110200", "2026-11-02", [(44701156, "06:45:25"), (44701156, "06:45:26")]),
    ("sew", "2026110112", "2026-11-01", [(15393975, "18:57:42"), (15393975, "18:57:45")]),
    ("sew", "2026110200", "2026-11-02", [(15393469, "06:53:13"), (15393469, "06:53:11")]),
    ("sju", "2026110118", "2026-11-02", [(73302723, "01:19:11"), (73302723, "01:19:38")]),
    ("tae", "2026110112", "2026-11-01", [(69661565, "18:54:56"), (69661565, "18:55:21")]),
    ("tae", "2026110312", "2026-11-03", [(53885123, "16:16:42"), (69658701, "18:52:44")]),
    ("tbw", "2026110100", "2026-11-01", [(94772898, "06:46:54"), (94772898, "06:46:43")]),
    ("tbw", "2026110200", "2026-11-02", [(94763859, "06:47:21"), (94763859, "06:47:39")]),
]
# The re-posts among them, by hand: a new size, or last-modified times 10 minutes or more apart.
#   eka 11-03 12Z  12492448 -> 9541400 bytes, 18:38:41 -> 19:04:10 (25.5 min)
#   hgx 11-03 00Z  65431170 -> 86370285 bytes, 00:24:47 -> 06:45:02 (6 h 20.2 min)
#   mfr 11-07 12Z  55879399 -> 43017313 bytes, 19:07:09 -> 20:01:05 (53.9 min)
#   mfr 11-08 00Z  43017415 bytes both, 03:37:29 -> 07:05:08 (3 h 27.7 min)
#   tae 11-03 12Z  53885123 -> 69658701 bytes, 16:16:42 -> 18:52:44 (2 h 36.0 min)
# The other 46 keep one size, their times 1 to 62 seconds apart (lch 11-01 00Z 06:41:55 and
# 06:42:46 is 51 s; phi 11-02 00Z 04:03:39 and 04:04:41 is 62 s, the widest).
_REPOSTS = {("eka", "2026110312"), ("hgx", "2026110300"), ("mfr", "2026110712"),
            ("mfr", "2026110800"), ("tae", "2026110312")}


def _versions(date, pairs):
    return [{"bytes": b, "last_modified": f"{date}T{t}Z", "first_seen": None} for b, t in pairs]


def test_the_rule_window_is_ten_minutes():
    assert NP.REPOST_MIN_GAP_S == 600


def test_the_fifty_one_files_seen_twice_are_five_reposts_and_forty_six_server_copies():
    assert len(_SEEN_TWICE) == 51
    reposts = {(w, c) for w, c, d, pairs in _SEEN_TWICE if NP.distinct_versions(_versions(d, pairs))}
    assert reposts == _REPOSTS
    copies = [(w, c) for w, c, d, pairs in _SEEN_TWICE
              if not NP.distinct_versions(_versions(d, pairs))]
    assert len(copies) == 46
    # and so only the reposts give a reason to keep a cycle out of the gate
    for w, c, d, pairs in _SEEN_TWICE:
        got = NP.double_post_reasons(None, _versions(d, pairs))
        assert bool(got) == ((w, c) in _REPOSTS), (w, c)


def test_the_edges_of_the_rule():
    v = lambda *pairs: _versions("2026-11-08", pairs)
    assert NP.distinct_versions(v((100, "03:00:00"), (100, "03:09:59"))) is False   # 599 s
    assert NP.distinct_versions(v((100, "03:00:00"), (100, "03:10:00"))) is True    # 600 s
    assert NP.distinct_versions(v((100, "03:10:00"), (100, "03:00:00"))) is True    # either order
    assert NP.distinct_versions(v((100, "03:00:00"), (101, "03:00:01"))) is True    # a new size
    assert NP.distinct_versions(v((100, "03:00:00"))) is False
    assert NP.distinct_versions([]) is False
    assert NP.distinct_versions(None) is False
    # three sightings: two copies of one post and a later post
    assert NP.distinct_versions(v((100, "03:00:00"), (100, "03:00:30"), (100, "05:00:00"))) is True
    assert NP.distinct_versions(v((100, "03:00:00"), (100, "03:00:30"), (100, "03:01:00"))) is False
    # an unreadable time is counted as different, so the cycle is kept out of the gate
    bad = [{"bytes": 100, "last_modified": "2026-11-08T03:00:00Z"},
           {"bytes": 100, "last_modified": "not a time"}]
    assert NP.distinct_versions(bad) is True


def test_server_copies_keep_a_cycle_in_the_gate():
    # lch 11-01 00Z, two copies 51 s apart: no reason, and run() keeps its hours in the gate
    copies = _versions("2026-11-01", [(84441285, "06:41:55"), (84441285, "06:42:46")])
    assert NP.double_post_reasons({"size_read": 7, "content_length": 7}, copies) == []
    rep = _run_with(logger={("mhx", "2026102612"): copies})
    assert rep["excluded"] == {} and rep["gate"]["hours"] == 2


# --------------------------------------------------------------------------- #
# Old node on land: excluded from the gate for that cycle, reported apart      #
# --------------------------------------------------------------------------- #
# Duck Pier's old node (36.19, -75.75) is cell (1, 1), made land in every field and step;
# Kitty Hawk did not change cell: its old node (36.18, -75.76) is cell (0, 0), every value 0.5,
# and it moves to (36.20, -75.73), cell (2, 3). The replay's fallback for Duck Pier is (1, 0),
# centre (36.19, -75.76): 0.01 deg of longitude at 36.19 N, 6371 x pi/180 x 0.01 x
# cos(36.19 deg) = 111.19 x 0.01 x 0.80706 = 0.897 km.
KH_OLD = {"name": "Kitty Hawk", "swell_window_source": "nwps", "nwps_wfo": "mhx",
          "nwps_node_lat": 36.18, "nwps_node_lng": -75.76, "nwps_node_distance_m": 900}
KH_NEW = dict(KH_OLD, nwps_node_lat=36.20, nwps_node_lng=-75.73, nwps_node_distance_m=1500)
LAND_KEY = "Duck Pier, mhx 2026102612"
LAND_EVIDENCE = {"node": [36.19, -75.75], "cell": [1, 1], "cell_centre": [36.19, -75.75],
                 "offset_km": 0.0, "steps": 3, "replayed_cell": [1, 0],
                 "replayed_centre": [36.19, -75.76], "replayed_km": 0.897}


def _kh_row(fh):
    """A published Kitty Hawk hour rated at its old cell (0, 0): every value 0.5; its wind
    (4.0 kt from 90) is not NWPS's, and the stand-in rater's face is the wind speed."""
    return {"spot_id": 3, "source": "nwps", "valid_time": _vt(fh), "nwps_cycle": STAMP,
            "swell_source": "nwps_height_ww3_dir", "hs": 0.5, "tp": 0.5, "dp": 0.5,
            "swell_hs": 0.5, "swell_dp": 70.0, "swell_tp": 11.0, "wind_speed": 4.0,
            "wind_dir": 90.0, "wind_mult": 1.0, "tide_mult": 1.0, "stars": 0.5,
            "face_ft_raw": 4.0}


def _run_land(cycle):
    # Duck Pier's published hours were rated from the cell nwps.fetch walked to (swh 1.235):
    # at the fallback cell (1, 0) the replay reads 0.5, so both hours differ in inputs and stars.
    duck = [_row(0, source="nwps", face_ft_raw=5.5),
            _row(1, source="nwps", wind_speed=9.0, wind_dir=90.0, face_ft_raw=9.0)]
    client = _Client([{"id": 1, "name": "Duck Pier", "state": "NC"},
                      {"id": 3, "name": "Kitty Hawk", "state": "NC"}],
                     duck + [_kh_row(0), _kh_row(1)])
    return NP.run(client, [OLD, KH_OLD], [NEW, KH_NEW], _vt(0), factors={},
                  list_cycles=lambda w: {("20261026", "12"): "url-12"},
                  open_cycle=lambda w, cyc: cycle, apply=_stub)


def test_the_evidence_that_an_old_node_is_land():
    assert NP.old_node_on_land(_cycle(land=[(1, 1)]), OLD) == LAND_EVIDENCE


def test_a_wet_old_node_is_not_land():
    assert NP.old_node_on_land(_cycle(), OLD) is None
    # a cell blank at f000 but wet later (one that dries at low water) is still the cell read
    assert NP.old_node_on_land(_dry_at_f000(), OLD) is None
    # the rule is about the fixed node: a NEW node on land does not make the old one land
    assert NP.old_node_on_land(_cycle(land=[(1, 3)]), OLD) is None


def test_a_spot_whose_old_node_is_land_is_kept_out_of_the_gate_and_the_rest_must_match():
    rep = _run_land(_cycle(land=[(1, 1)]))
    assert rep["excluded_land"] == {LAND_KEY: {
        "spot": "Duck Pier", "cycle_file": "mhx 2026102612", "evidence": LAND_EVIDENCE,
        "hours": 2, "input_differences": 2, "star_differences": 2, "face_differences": 0,
        "filled": 0}}
    # Kitty Hawk's two hours alone make the gate, and they match exactly
    assert rep["gate"] == {"hours": 2, "input_differences": 0, "star_differences": 0,
                           "face_differences": 0, "filled": 0, "pass": True,
                           "excluded_cycles": [], "excluded_land": [LAND_KEY]}
    assert rep["excluded"] == {}
    assert [(h["spot"], h["gate_excluded"], h["excluded_for"]) for h in rep["hours"]] == [
        ("Duck Pier", True, ["old node on land"]), ("Duck Pier", True, ["old node on land"]),
        ("Kitty Hawk", False, []), ("Kitty Hawk", False, [])]
    # still in the per-spot figures
    assert rep["by_spot"]["Duck Pier"]["hours"] == 2


def test_a_difference_on_a_spot_with_a_wet_old_node_still_fails_the_gate():
    # Kitty Hawk's old node is wet, so a stored height off by 0.1 m is a difference
    # and the gate fails, though Duck Pier is excluded.
    cyc = _cycle(land=[(1, 1)])
    duck = [_row(0, source="nwps", face_ft_raw=5.5)]
    client = _Client([{"id": 1, "name": "Duck Pier", "state": "NC"},
                      {"id": 3, "name": "Kitty Hawk", "state": "NC"}],
                     duck + [dict(_kh_row(0), hs=0.6)])
    rep = NP.run(client, [OLD, KH_OLD], [NEW, KH_NEW], _vt(0), factors={},
                 list_cycles=lambda w: {("20261026", "12"): "url-12"},
                 open_cycle=lambda w, cyc_: cyc, apply=_stub)
    assert rep["gate"]["hours"] == 1 and rep["gate"]["input_differences"] == 1
    assert rep["gate"]["pass"] is False
    assert rep["gate"]["excluded_land"] == [LAND_KEY]


def test_with_both_old_nodes_wet_nothing_is_excluded():
    rep = _run_land(_cycle())
    assert rep["excluded_land"] == {} and rep["gate"]["excluded_land"] == []
    # Duck Pier's rows read 1.235 at its own cell, so all four hours are in the gate and match
    assert rep["gate"]["hours"] == 4 and rep["gate"]["pass"] is True


def test_the_report_names_the_land_node_and_its_evidence():
    import contextlib
    import io
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        NP.print_report(_run_land(_cycle(land=[(1, 1)])))
    lines = buf.getvalue().splitlines()
    i = lines.index("  EXCLUDED from the gate, old node on land in this cycle: Duck Pier, "
                    "mhx 2026102612: 2 hours, 2 input, 2 star and 0 face differences")
    assert lines[i + 1] == (
        "    - node (36.19, -75.75) is cell (1, 1), centre (36.19, -75.75), 0.0 km away: no wave "
        "value (swh or shts) there at any of the cycle's 3 steps, so nwps.fetch walked seaward "
        "and the published hours are not from this node; the replay read cell (1, 0), centre "
        "(36.19, -75.76), 0.897 km away, instead")
    assert any(ln.startswith(f"    Duck Pier {_vt(0)} [mhx 2026102612, excluded]: inputs DIFFER")
               for ln in lines)
    assert any(ln.startswith("=== MERGE GATE: the old points against the published window: "
                             "2 hours, 0 input differences") and ln.endswith("-> PASS ===")
               for ln in lines)


def test_an_all_land_grid_leaves_no_cell_to_replay():
    every = [(i, j) for i in range(3) for j in range(4)]
    ev = NP.old_node_on_land(_cycle(land=every), OLD)
    assert ev["replayed_cell"] is None and ev["replayed_centre"] is None
    assert ev["replayed_km"] is None
    assert NP.land_evidence_line(ev).endswith("the replay found no wet cell to read instead")


def test_a_land_node_with_no_hours_in_the_cycle_is_not_listed():
    # Duck Pier's only hour was written by another rater (WW3), so nothing of it is replayed
    # on this cycle and there is nothing to exclude.
    client = _Client([{"id": 1, "name": "Duck Pier", "state": "NC"},
                      {"id": 3, "name": "Kitty Hawk", "state": "NC"}],
                     [_row(0, source="nwps", swell_source="ww3"), _kh_row(0)])
    cyc = _cycle(land=[(1, 1)])
    rep = NP.run(client, [OLD, KH_OLD], [NEW, KH_NEW], _vt(0), factors={},
                 list_cycles=lambda w: {("20261026", "12"): "url-12"},
                 open_cycle=lambda w, c: cyc, apply=_stub)
    assert rep["excluded_land"] == {}
    assert rep["gate"]["hours"] == 1 and rep["gate"]["pass"] is True
