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


def test_inputs_match_all_four_values():
    vals = (1.235, 7.9, 75.0, 1.0)
    assert NP.inputs_match(_row(0), vals)
    assert NP.inputs_match(_row(0, hs=1.2354), vals)
    assert not NP.inputs_match(_row(0, hs=1.236), vals)
    assert not NP.inputs_match(_row(0, tp=7.91), vals)
    assert not NP.inputs_match(_row(0, dp=75.001), vals)
    assert not NP.inputs_match(_row(0, swell_hs=1.001), vals)
    assert not NP.inputs_match(_row(0), None)


def test_inputs_match_a_missing_shts_only_against_a_missing_swell_hs():
    assert NP.inputs_match(_row(0, swell_hs=None), (1.235, 7.9, 75.0, None))
    assert not NP.inputs_match(_row(0), (1.235, 7.9, 75.0, None))
    assert not NP.inputs_match(_row(0, swell_hs=None), (1.235, 7.9, 75.0, 1.0))


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
    ok = {"inputs_match": True, "stars_old": 2.0, "stars_published": 2.0}
    assert NP.gate([ok, ok]) == {"hours": 2, "input_differences": 0, "star_differences": 0,
                                 "pass": True}
    assert NP.gate([])["pass"] is False
    bad_in = dict(ok, inputs_match=False)
    assert NP.gate([ok, bad_in]) == {"hours": 2, "input_differences": 1, "star_differences": 0,
                                     "pass": False}
    bad_st = dict(ok, stars_old=2.5)
    assert NP.gate([bad_st, ok]) == {"hours": 2, "input_differences": 0, "star_differences": 1,
                                     "pass": False}
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
    duck = [_row(0, source="nwps"), _row(1, source="nwps", wind_speed=9.0, wind_dir=90.0),
            _row(2, source="nwps", swell_source="ww3"),
            _row(2, source="nwps", valid_time=_vt(3), nwps_cycle="2026-10-26T06:00:00Z"),
            _row(2, source="nwps", valid_time=_vt(4), stars=None)]
    other = [{"spot_id": 2, "source": "nwps", "valid_time": _vt(0), "stars": 1.5}]
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
                           "pass": True}
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
    assert "0 input differences (hs, tp, dp, swell_hs), 2 star differences -> FAIL ===" in buf.getvalue()
    assert f"    Duck Pier {_vt(0)}: inputs ok, published 1.235 replayed 1.735" in buf.getvalue()
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
                              "pass": True}
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
            "0 input differences (hs, tp, dp, swell_hs), 0 star differences -> PASS ===") in out
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
