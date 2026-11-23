"""pipeline/accuracy_score.py: forecasts paired with the buoy reading nearest their hour, summed per
day into accuracy_daily (migration 031). Every expected value is worked by hand in the comments.
"""
from __future__ import annotations

import math
from datetime import date, datetime, timezone

import pytest

from pipeline import accuracy_score as S
from pipeline.tests.test_ci_workflow import yaml_code
from pipeline.tests.test_column_preflight import steps

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


OBS = S.index_obs([
    {"buoy_id": "b", "observed_at": "2026-11-14T12:50:00Z", "hs": 2.0},
    {"buoy_id": "b", "observed_at": "2026-11-14T11:40:00Z", "hs": 1.0},
    {"buoy_id": "b", "observed_at": "2026-11-14T11:56:00Z", "hs": None, "tp": 12.0},
    {"buoy_id": "b", "observed_at": "2026-11-14T12:10:00Z", "hs": 1.4},
    {"buoy_id": "t", "observed_at": "2026-11-14T11:50:00Z", "hs": 1.1},
    {"buoy_id": "t", "observed_at": "2026-11-14T12:10:00Z", "hs": 1.4},
    {"buoy_id": "e", "observed_at": "2026-11-14T12:30:00Z", "hs": 0.9},
])


@pytest.mark.parametrize("buoy, t, field, want", [
    ("b", "2026-11-14T12:00:00Z", "hs", 1.4),     # 11:56 has no height; 12:10 (10 min) beats 11:40 (20)
    ("b", "2026-11-14T12:00:00Z", "tp", 12.0),    # 11:56, 4 min
    ("b", "2026-11-14T13:30:00Z", "hs", None),    # 12:50 is 40 min away
    ("b", "2026-11-14T13:20:00Z", "hs", 2.0),     # 30 min exactly: inside
    ("t", "2026-11-14T12:00:00Z", "hs", 1.1),     # 10 min either side: the earlier
    ("e", "2026-11-14T12:00:00Z", "hs", 0.9),     # 30 min after exactly: inside
    ("x", "2026-11-14T12:00:00Z", "hs", None),    # no such buoy
])
def test_nearest_obs(buoy, t, field, want):
    assert S.nearest_obs(OBS, buoy, _t(t), field) == want


@pytest.mark.parametrize("measure, f, o, e", [
    ("dp", 350.0, 10.0, -20.0),
    ("dp", 10.0, 350.0, 20.0),
    ("dp", 180.0, 0.0, -180.0),
    ("hs", 1.5, 1.25, 0.25),
])
def test_error(measure, f, o, e):
    assert S.error(measure, f, o) == e


def test_ww3_swell_is_the_partitions_combined():
    assert S.ww3_swell_hs({"swell_1_hs": 0.3, "swell_2_hs": 0.4, "swell_3_hs": None}) == 0.5   # 3-4-5
    assert S.ww3_swell_hs({}) is None


def test_totals_sum_what_the_report_needs():
    t = S.Totals()
    t.add(("k",), "p1", 2.0, 1.0, 1.0)
    t.add(("k",), "p2", 1.0, 2.0, -1.0)
    s = t.sums[("k",)]
    assert s == {"n": 2, "sum_f": 3.0, "sum_o": 3.0, "sum_ff": 5.0, "sum_oo": 5.0, "sum_fo": 4.0,
                 "sum_e": 0.0, "sum_abs_e": 2.0, "sum_ee": 2.0}
    assert len(t.points[("k",)]) == 2


SPOTS = [
    {"name": "A", "region_hint": "Oregon", "nearest_buoy_id": "46050", "fallback_buoy_ids": ["46029"],
     "nearest_buoy_dist_km": 24.9, "time_zone": "America/Los_Angeles"},
    {"name": "B", "region_hint": "Oregon", "nearest_buoy_id": "46050", "nearest_buoy_dist_km": 25.0,
     "time_zone": "America/Los_Angeles"},
    {"name": "C", "region_hint": "Washington", "nearest_buoy_id": "46029", "fallback_buoy_ids": ["46050"],
     "nearest_buoy_dist_km": 25.1, "time_zone": "America/Los_Angeles"},
    {"name": "D", "region_hint": "Washington", "nearest_buoy_id": None, "time_zone": "America/Los_Angeles"},
    {"name": "E", "region_hint": "Oregon", "nearest_buoy_id": "46050", "nearest_buoy_dist_km": 3.0},
]
IDS = {"A": 1, "B": 2, "C": 3, "D": 4}


def test_a_buoys_region_is_the_commonest_among_its_spots():
    # 46050: Oregon (A, B, E) 3, Washington (C) 1. 46029: Oregon (A) 1, Washington (C) 1: a tie,
    # Oregon first alphabetically.
    assert S.buoy_regions(SPOTS) == {"46050": "Oregon", "46029": "Oregon"}


def test_spot_infos_take_buoys_within_25_km():
    info, zones = S.spot_infos(SPOTS, IDS)
    assert info == {1: {"region": "Oregon", "buoy_id": "46050", "slug": "a"},
                    2: {"region": "Oregon", "buoy_id": "46050", "slug": "b"}}   # C 25.1 km; D none; E no id
    assert zones == {1: "America/Los_Angeles", 2: "America/Los_Angeles",
                     3: "America/Los_Angeles", 4: "America/Los_Angeles"}


def test_a_spots_point_is_its_database_slug():
    # db_import._slugify: lowercase, every run of other characters one hyphen, ends trimmed.
    info, _ = S.spot_infos([{"name": "Dania Beach Pier (North)", "region_hint": "Florida",
                             "nearest_buoy_id": "41122", "nearest_buoy_dist_km": 6.5}],
                           {"Dania Beach Pier (North)": 7})
    assert info[7]["slug"] == "dania-beach-pier-north"


def test_day0_rows_are_the_scoreboard_hours_within_a_day_of_their_cycle():
    rows = [
        {"spot_id": 1, "valid_time": "2026-11-14T14:00:00Z", "nwps_cycle": "2026-11-14T06:00:00Z"},  # 06 PST, 8 h
        {"spot_id": 2, "valid_time": "2026-11-14T14:00:00Z", "nwps_cycle": "2026-11-13T12:00:00Z"},  # 26 h: day 1
        {"spot_id": 1, "valid_time": "2026-11-14T15:00:00Z", "nwps_cycle": "2026-11-14T06:00:00Z"},  # 07 PST
        {"spot_id": 1, "valid_time": "2026-11-14T17:00:00Z", "nwps_cycle": None},                    # 09 PST, no cycle
        {"spot_id": 9, "valid_time": "2026-11-14T14:00:00Z", "nwps_cycle": "2026-11-14T06:00:00Z"},  # no zone
    ]
    got = S.day0_rows(rows, {1: "America/Los_Angeles", 2: "America/Los_Angeles"})
    assert got == [{**rows[0], "lead_day": 0}]


MODEL = [
    # ww3, day 1 at 12:00; buoy at 12:10: hs 1.5, tp 14, dp 250, swell 1.2.
    {"buoy_id": "46050", "valid_time": "2026-11-14T12:00:00Z", "source": "ww3", "lead_day": 1,
     "hs": 2.0, "tp": 12.0, "dp": 270.0, "swell_hs": None, "swell_1_hs": 0.6, "swell_2_hs": 0.8},
    # nwps, day 0 at 15:00; buoy at 15:20 reads 0.4 m, so no direction pair.
    {"buoy_id": "46050", "valid_time": "2026-11-14T15:00:00Z", "source": "nwps", "lead_day": 0,
     "hs": 0.5, "tp": None, "dp": 200.0, "swell_hs": 0.3},
    # a buoy with no readings
    {"buoy_id": "46999", "valid_time": "2026-11-14T12:00:00Z", "source": "ww3", "lead_day": 1, "hs": 1.0},
]
BUOY_OBS = S.index_obs([
    {"buoy_id": "46050", "observed_at": "2026-11-14T12:10:00Z", "hs": 1.5, "tp": 14.0, "dp": 250.0, "swell_hs": 1.2},
    {"buoy_id": "46050", "observed_at": "2026-11-14T15:20:00Z", "hs": 0.4, "tp": 6.0, "dp": 180.0, "swell_hs": 0.2},
])


def _approx(sums, **want):
    return all(math.isclose(sums[k], v, abs_tol=1e-9) for k, v in want.items())


def test_score_buoys():
    t = S.Totals()
    S.score_buoys(t, MODEL, BUOY_OBS, {"46050": "Oregon"})
    key = lambda src, ld, m, p="": ("buoy", src, "Oregon", p, ld, m)
    assert set(t.sums) == {key("ww3", 1, "hs"), key("ww3", 1, "swell_hs"), key("ww3", 1, "tp"),
                           key("ww3", 1, "dp"), key("ww3", 1, "hs", "46050"),
                           key("nwps", 0, "hs"), key("nwps", 0, "swell_hs"), key("nwps", 0, "hs", "46050")}
    assert _approx(t.sums[key("ww3", 1, "hs")], n=1, sum_e=0.5, sum_ee=0.25)          # 2.0 - 1.5
    assert _approx(t.sums[key("ww3", 1, "swell_hs")], sum_f=1.0, sum_e=-0.2)          # sqrt(.36+.64) - 1.2
    assert _approx(t.sums[key("ww3", 1, "tp")], sum_e=-2.0)
    assert _approx(t.sums[key("ww3", 1, "dp")], sum_e=20.0)                           # 270 - 250
    assert _approx(t.sums[key("nwps", 0, "swell_hs")], sum_e=0.1)                     # 0.3 - 0.2


def test_score_spots_uses_the_spots_buoy_and_skips_far_ones():
    t = S.Totals()
    info, _ = S.spot_infos(SPOTS, IDS)
    rows = [{"spot_id": 1, "valid_time": "2026-11-14T12:00:00Z", "lead_day": 2, "hs": 1.0},
            {"spot_id": 3, "valid_time": "2026-11-14T12:00:00Z", "lead_day": 2, "hs": 9.0}]   # 25.1 km
    S.score_spots(t, rows, info, BUOY_OBS)
    # 1.0 against the 12:10 reading, 1.5: e = -0.5, for Oregon and for spot A alone.
    assert list(t.sums) == [("spot", "spot", "Oregon", "", 2, "hs"), ("spot", "spot", "Oregon", "a", 2, "hs")]
    assert _approx(t.sums[("spot", "spot", "Oregon", "", 2, "hs")], n=1, sum_e=-0.5)
    assert _approx(t.sums[("spot", "spot", "Oregon", "a", 2, "hs")], n=1, sum_e=-0.5)


def test_score_spots_keeps_the_spot_alone_for_height_only():
    t = S.Totals()
    info, _ = S.spot_infos(SPOTS, IDS)
    # 12:00, day 1: height 1.0 against 1.5, period 12 against 14, swell 1.0 against 1.2.
    S.score_spots(t, [{"spot_id": 2, "valid_time": "2026-11-14T12:00:00Z", "lead_day": 1, "hs": 1.0,
                       "tp": 12.0, "swell_hs": 1.0}], info, BUOY_OBS)
    assert set(t.sums) == {("spot", "spot", "Oregon", "", 1, "hs"), ("spot", "spot", "Oregon", "", 1, "tp"),
                           ("spot", "spot", "Oregon", "", 1, "swell_hs"),
                           ("spot", "spot", "Oregon", "b", 1, "hs")}
    assert t.points[("spot", "spot", "Oregon", "b", 1, "hs")] == {"b"}


NWPS_AT_B = [
    {"buoy_id": "b", "valid_time": "2026-11-14T09:00:00Z", "source": "nwps", "lead_day": 1, "hs": 1.0},
    {"buoy_id": "b", "valid_time": "2026-11-14T12:00:00Z", "source": "nwps", "lead_day": 1, "hs": 2.0},
    {"buoy_id": "b", "valid_time": "2026-11-14T10:30:00Z", "source": "nwps", "lead_day": 1, "hs": None},
    {"buoy_id": "b", "valid_time": "2026-11-14T11:50:00Z", "source": "ww3", "lead_day": 1, "hs": 9.0},
    {"buoy_id": "b", "valid_time": "2026-11-14T12:00:00Z", "source": "nwps", "lead_day": 2, "hs": 5.0},
]


@pytest.mark.parametrize("buoy, lead, t, want", [
    ("b", 1, "2026-11-14T10:30:00Z", 1.0),   # 90 min from 09:00 and 12:00: the earlier (10:30 has none)
    ("b", 1, "2026-11-14T11:00:00Z", 2.0),   # 12:00 is 60 min, 09:00 120
    ("b", 1, "2026-11-14T11:45:00Z", 2.0),   # ww3 at 11:50 is nearer but not NWPS
    ("b", 1, "2026-11-14T13:30:00Z", 2.0),   # 90 min exactly: inside
    ("b", 1, "2026-11-14T13:31:00Z", None),  # 91 min
    ("b", 1, "2026-11-14T07:29:00Z", None),  # 91 min before 09:00
    ("b", 2, "2026-11-14T12:00:00Z", 5.0),   # its own lead day, not day 1's 2.0
    ("b", 3, "2026-11-14T12:00:00Z", None),  # no day 3
    ("x", 1, "2026-11-14T12:00:00Z", None),  # no such buoy
])
def test_nearest_model(buoy, lead, t, want):
    assert S.nearest_model(S.index_model(NWPS_AT_B), buoy, lead, _t(t)) == want


def test_score_spot_model_by_hand():
    t = S.Totals()
    info, _ = S.spot_infos(SPOTS, IDS)
    model = S.index_model([
        {"buoy_id": "46050", "valid_time": "2026-11-14T12:00:00Z", "source": "nwps", "lead_day": 1, "hs": 2.0},
        {"buoy_id": "46050", "valid_time": "2026-11-14T15:00:00Z", "source": "nwps", "lead_day": 1, "hs": 2.4},
        {"buoy_id": "46050", "valid_time": "2026-11-14T12:00:00Z", "source": "nwps", "lead_day": 0, "hs": 1.0},
    ])
    S.score_spot_model(t, [
        {"spot_id": 1, "valid_time": "2026-11-14T13:00:00Z", "lead_day": 1, "hs": 1.5},   # 12:00: 2.0, e -0.5
        {"spot_id": 1, "valid_time": "2026-11-14T14:00:00Z", "lead_day": 1, "hs": 1.6},   # 15:00: 2.4, e -0.8
        {"spot_id": 1, "valid_time": "2026-11-14T12:00:00Z", "lead_day": 0, "hs": 1.2},   # 1.0, e +0.2
        {"spot_id": 1, "valid_time": "2026-11-14T12:00:00Z", "lead_day": 2, "hs": 1.0},   # no day 2
        {"spot_id": 1, "valid_time": "2026-11-14T12:00:00Z", "lead_day": 1, "hs": None},  # no height
        {"spot_id": 3, "valid_time": "2026-11-14T12:00:00Z", "lead_day": 1, "hs": 1.0},   # C, 25.1 km
    ], info, model)
    k = lambda point, ld: ("spot_model", "nwps", "Oregon", point, ld, "hs")
    assert set(t.sums) == {k("", 1), k("a", 1), k("", 0), k("a", 0)}
    for point in ("", "a"):
        # day 1: f 1.5 + 1.6 = 3.1; o 2.0 + 2.4 = 4.4; fo 3.0 + 3.84 = 6.84; ee 0.25 + 0.64 = 0.89
        assert _approx(t.sums[k(point, 1)], n=2, sum_f=3.1, sum_o=4.4, sum_fo=6.84, sum_e=-1.3,
                       sum_abs_e=1.3, sum_ee=0.89)
        assert _approx(t.sums[k(point, 0)], n=1, sum_f=1.2, sum_o=1.0, sum_e=0.2)
        assert t.points[k(point, 1)] == {"a"}


def test_totals_rows():
    t = S.Totals()
    t.add(("buoy", "ww3", "Oregon", "", 1, "hs"), "46050", 2.0, 1.5, 0.5)
    t.add(("buoy", "ww3", "Oregon", "", 1, "hs"), "46029", 1.0, 1.0, 0.0)
    assert t.rows(date(2026, 11, 14), "2026-11-15T04:47:00Z") == [{
        "day": "2026-11-14", "kind": "buoy", "source": "ww3", "region": "Oregon", "point": "",
        "lead_day": 1, "measure": "hs", "n": 2, "points": 2, "sum_f": 3.0, "sum_o": 2.5,
        "sum_ff": 5.0, "sum_oo": 3.25, "sum_fo": 4.0, "sum_e": 0.5, "sum_abs_e": 0.5,
        "sum_ee": 0.25, "scored_at": "2026-11-15T04:47:00Z"}]


def test_days_to_score_are_the_complete_days_before_the_end():
    assert S.days_to_score(date(2026, 11, 16), 3) == [date(2026, 11, 13), date(2026, 11, 14),
                                                       date(2026, 11, 15)]


# --------------------------------------------------------------------------- #
# Reading, with a stand-in for the Supabase client                             #
# --------------------------------------------------------------------------- #

class _Res:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table):
        self.c, self.table, self.filters, self.orders, self.rng = client, table, [], [], None

    def select(self, cols):
        self.cols = cols
        return self

    def gte(self, col, v):
        self.filters.append((col, ">=", v))
        return self

    def lt(self, col, v):
        self.filters.append((col, "<", v))
        return self

    def gt(self, col, v):
        self.filters.append((col, ">", v))
        return self

    def eq(self, col, v):
        self.filters.append((col, "=", v))
        return self

    def in_(self, col, values):
        self.filters.append((col, "in", list(values)))
        return self

    def order(self, col):
        self.orders.append(col)
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def upsert(self, rows, on_conflict=None):
        self.c.upserts.append((self.table, rows, on_conflict))
        return self

    def execute(self):
        self.c.calls.append(self)
        if self.rng is None:
            return _Res([])
        def val(x):
            return S._ts(x) if isinstance(x, str) and "T" in x else x
        def ok(r, col, op, v):
            if op == ">=":
                return val(r[col]) >= val(v)
            if op == "<":
                return val(r[col]) < val(v)
            if op == ">":
                return val(r[col]) > val(v)
            if op == "in":
                return r[col] in v
            return r[col] == v
        data = [r for r in self.c.data.get(self.table, []) if all(ok(r, *f) for f in self.filters)]
        for col in reversed(self.orders):                  # ORDER BY the listed columns, stably
            data.sort(key=lambda r, c=col: val(r[c]))
        a, b = self.rng
        return _Res(data[a:b + 1])


class FakeClient:
    def __init__(self, data):
        self.data, self.calls, self.upserts = data, [], []

    def table(self, name):
        return FakeQuery(self, name)


def test_fetch_all_pages_in_a_stable_order(monkeypatch):
    monkeypatch.setattr(S, "PAGE", 2)
    rows = [{"valid_time": f"2026-11-14T0{h}:00:00+00:00", "x": h} for h in range(5)]
    c = FakeClient({"t": rows})
    got = S.fetch_all(c, "t", "valid_time,x", "valid_time", _t("2026-11-14T00:00:00Z"),
                      _t("2026-11-14T04:00:00Z"), ("valid_time",), eq=None)
    assert [r["x"] for r in got] == [0, 1, 2, 3]                 # 04:00 is the end, excluded
    assert [q.rng for q in c.calls] == [(0, 1), (2, 3), (4, 5)]  # a short page ends it
    assert c.calls[0].filters == [("valid_time", ">=", "2026-11-14T00:00:00Z"),
                                  ("valid_time", "<", "2026-11-14T04:00:00Z")]
    assert all(q.orders == ["valid_time"] for q in c.calls)


def test_score_day_end_to_end():
    c = FakeClient({
        "buoy_observations": [{"buoy_id": "46050", "observed_at": "2026-11-14T12:10:00+00:00", "hs": 1.5,
                               "tp": 14.0, "dp": 250.0, "swell_hs": 1.2}],
        "model_at_buoys": [dict(MODEL[0], valid_time="2026-11-14T12:00:00+00:00")],
        "forecast_leads": [{"spot_id": 1, "valid_time": "2026-11-14T12:00:00+00:00", "lead_day": 3,
                            "hs": 1.0, "tp": None, "dp": None, "swell_hs": None}],
        "forecasts": [{"spot_id": 2, "valid_time": "2026-11-14T12:00:00+00:00", "source": "nwps",
                       "hs": 1.8, "tp": None, "dp": None, "swell_hs": None,
                       "nwps_cycle": "2026-11-14T00:00:00+00:00"}],   # 04 PST: not a kept hour
    })
    rows = S.score_day(c, date(2026, 11, 14), SPOTS, IDS, "now")
    got = {(r["kind"], r["source"], r["point"], r["lead_day"], r["measure"]): r["n"] for r in rows}
    assert got == {("buoy", "ww3", "", 1, "hs"): 1, ("buoy", "ww3", "46050", 1, "hs"): 1,
                   ("buoy", "ww3", "", 1, "swell_hs"): 1, ("buoy", "ww3", "", 1, "tp"): 1,
                   ("buoy", "ww3", "", 1, "dp"): 1, ("spot", "spot", "", 3, "hs"): 1,
                   ("spot", "spot", "a", 3, "hs"): 1}
    forecasts_q = [q for q in c.calls if q.table == "forecasts"][0]
    assert ("source", "=", "nwps") in forecasts_q.filters


def test_score_day_spot_model_reaches_past_midnight_but_buoys_keep_their_day():
    # Spot A, day 1 at 23:00Z on the 14th, 1.0 m. NWPS at 46050 day 1: 21:00Z 9.0 m (120 min) and
    # 00:00Z on the 15th 1.6 m (60 min): the pair is 1.0 against 1.6, e = -0.6. The buoy reads at
    # 00:05Z on the 15th, so the 00:00Z model row would pair with it if the buoy rows took the
    # widened read: they must not (it is the 15th's). No reading within 30 min of 23:00Z: no spot row.
    c = FakeClient({
        "buoy_observations": [{"buoy_id": "46050", "observed_at": "2026-11-15T00:05:00+00:00", "hs": 1.5,
                               "tp": None, "dp": None, "swell_hs": None}],
        "model_at_buoys": [
            {"buoy_id": "46050", "valid_time": "2026-11-14T21:00:00+00:00", "source": "nwps", "lead_day": 1,
             "hs": 9.0, "tp": None, "dp": None, "swell_hs": None},
            {"buoy_id": "46050", "valid_time": "2026-11-15T00:00:00+00:00", "source": "nwps", "lead_day": 1,
             "hs": 1.6, "tp": None, "dp": None, "swell_hs": None},
            {"buoy_id": "46050", "valid_time": "2026-11-15T03:00:00+00:00", "source": "nwps", "lead_day": 1,
             "hs": 1.0, "tp": None, "dp": None, "swell_hs": None}],          # outside the read
        "forecast_leads": [{"spot_id": 1, "valid_time": "2026-11-14T23:00:00+00:00", "lead_day": 1,
                            "hs": 1.0, "tp": None, "dp": None, "swell_hs": None}],
        "forecasts": [],
    })
    rows = S.score_day(c, date(2026, 11, 14), SPOTS, IDS, "now")
    assert [(r["kind"], r["source"], r["point"], r["lead_day"], r["n"]) for r in rows] == [
        ("spot_model", "nwps", "", 1, 1), ("spot_model", "nwps", "a", 1, 1)]
    assert all(math.isclose(r["sum_e"], -0.6) and math.isclose(r["sum_o"], 1.6) for r in rows)
    model_q = [q for q in c.calls if q.table == "model_at_buoys"][0]
    assert ("valid_time", ">=", "2026-11-13T22:30:00Z") in model_q.filters
    assert ("valid_time", "<", "2026-11-15T01:30:00Z") in model_q.filters


# --------------------------------------------------------------------------- #
# #338: lead day 0 read in batches of paired spots, one page each, from offset 0 #
# --------------------------------------------------------------------------- #

def _fc(sid, t, hs, source="nwps", cycle="2026-11-14T12:00:00+00:00"):
    return {"spot_id": sid, "valid_time": t, "source": source, "hs": hs, "tp": None, "dp": None,
            "swell_hs": None, "nwps_cycle": cycle}


def test_day0_reads_batches_of_spots_from_offset_zero_in_index_order(monkeypatch):
    monkeypatch.setattr(S, "SPOT_BATCH", 2)
    c = FakeClient({"forecasts": [_fc(i, "2026-11-14T20:00:00+00:00", 1.0) for i in (1, 2, 3, 4, 5)]})
    got = S.fetch_day0_forecasts(c, [5, 3, 1, 4, 2], _t("2026-11-14T00:00:00Z"), _t("2026-11-15T00:00:00Z"))
    assert [r["spot_id"] for r in got] == [1, 2, 3, 4, 5]
    # Sorted ids in batches of 2: [1, 2], [3, 4], [5]. Every request: one page from 0, nwps, the
    # day, ordered (spot_id, valid_time).
    assert [q.filters for q in c.calls] == [
        [("spot_id", "in", b), ("source", "=", "nwps"), ("valid_time", ">=", "2026-11-14T00:00:00Z"),
         ("valid_time", "<", "2026-11-15T00:00:00Z")] for b in ([1, 2], [3, 4], [5])]
    assert all(q.orders == ["spot_id", "valid_time"] and q.rng == (0, S.PAGE - 1) for q in c.calls)


def test_a_full_page_splits_the_batch_and_never_pages_past_it(monkeypatch):
    # PAGE 3: spots 1 and 2 have 2 rows each, so [1, 2] fills a page (3 of 4 rows) and is re-read
    # as [1] and [2], each from offset 0.
    monkeypatch.setattr(S, "PAGE", 3)
    rows = [_fc(1, "2026-11-14T20:00:00+00:00", 1.0), _fc(1, "2026-11-14T23:00:00+00:00", 1.1),
            _fc(2, "2026-11-14T20:00:00+00:00", 1.2), _fc(2, "2026-11-14T23:00:00+00:00", 1.3)]
    c = FakeClient({"forecasts": rows})
    got = S.fetch_day0_forecasts(c, [1, 2], _t("2026-11-14T00:00:00Z"), _t("2026-11-15T00:00:00Z"))
    assert [(r["spot_id"], r["hs"]) for r in got] == [(1, 1.0), (1, 1.1), (2, 1.2), (2, 1.3)]
    assert [(q.filters[0][2], q.rng) for q in c.calls] == [([1, 2], (0, 2)), ([1], (0, 2)), ([2], (0, 2))]


def test_one_spot_filling_a_page_carries_on_after_its_last_hour(monkeypatch):
    # PAGE 2, one spot with 3 rows (10, 11, 12Z): the first request returns 10 and 11Z, the next
    # asks for valid_time > 11Z, again from offset 0, and returns 12Z; a short page ends it.
    monkeypatch.setattr(S, "PAGE", 2)
    c = FakeClient({"forecasts": [_fc(1, f"2026-11-14T1{h}:00:00+00:00", float(h)) for h in range(3)]})
    got = S.fetch_day0_forecasts(c, [1], _t("2026-11-14T00:00:00Z"), _t("2026-11-15T00:00:00Z"))
    assert [r["hs"] for r in got] == [0.0, 1.0, 2.0]
    assert [q.filters[2] for q in c.calls] == [("valid_time", ">=", "2026-11-14T00:00:00Z"),
                                               ("valid_time", ">", "2026-11-14T11:00:00+00:00")]
    assert all(q.rng == (0, 1) for q in c.calls)


def _day_fixture():
    # 20:00Z is 12:00 PST, a kept hour; cycle 12Z is 8 h before it: lead day 0. Buoy 46050 reads
    # 1.5 m at 20:10Z. A (spot 1, 24.9 km) 1.8 m: e = 0.3; B (spot 2, 25.0 km) 1.6 m: e = 0.1.
    # C (spot 3, 25.1 km) and D (spot 4, no buoy) are not paired. Also: an ecmwf row, a row the
    # next day, and 21:00Z (13:00 PST, not a kept hour), none of which may count.
    return {
        "buoy_observations": [{"buoy_id": "46050", "observed_at": "2026-11-14T20:10:00+00:00", "hs": 1.5,
                               "tp": None, "dp": None, "swell_hs": None}],
        "model_at_buoys": [], "forecast_leads": [],
        "forecasts": [_fc(1, "2026-11-14T20:00:00+00:00", 1.8), _fc(2, "2026-11-14T20:00:00+00:00", 1.6),
                      _fc(3, "2026-11-14T20:00:00+00:00", 9.0), _fc(4, "2026-11-14T20:00:00+00:00", 9.0),
                      _fc(1, "2026-11-14T20:00:00+00:00", 5.0, source="ecmwf"),
                      _fc(1, "2026-11-15T20:00:00+00:00", 5.0), _fc(2, "2026-11-14T21:00:00+00:00", 5.0)],
    }


def test_day0_scores_by_hand():
    c = FakeClient(_day_fixture())
    rows = S.score_day(c, date(2026, 11, 14), SPOTS, IDS, "now")
    assert [(r["kind"], r["region"], r["point"], r["lead_day"], r["measure"], r["n"]) for r in rows] == [
        ("spot", "Oregon", "", 0, "hs", 2), ("spot", "Oregon", "a", 0, "hs", 1),
        ("spot", "Oregon", "b", 0, "hs", 1)]
    assert math.isclose(rows[0]["sum_e"], 0.4) and math.isclose(rows[0]["sum_abs_e"], 0.4)
    assert math.isclose(rows[1]["sum_e"], 0.3) and math.isclose(rows[2]["sum_e"], 0.1)
    reads = [q for q in c.calls if q.table == "forecasts"]
    assert [q.filters[0] for q in reads] == [("spot_id", "in", [1, 2])]     # only A and B
    assert all(q.rng[0] == 0 for q in reads)


def test_day0_scores_identical_to_the_old_whole_day_read(monkeypatch):
    """The old read: every spot's nwps rows for the day, OFFSET-paged, ordered (spot_id,
    valid_time). Same accuracy_daily rows, to the last field, with a page of 2 so the old read
    pages and the new one splits."""
    monkeypatch.setattr(S, "PAGE", 2)
    monkeypatch.setattr(S, "SPOT_BATCH", 3)
    new = S.score_day(FakeClient(_day_fixture()), date(2026, 11, 14), SPOTS, IDS, "now")
    monkeypatch.setattr(S, "fetch_day0_forecasts", lambda client, ids, start, end: S.fetch_all(
        client, "forecasts", S.FORECAST_COLS, "valid_time", start, end, ("spot_id", "valid_time"),
        eq={"source": "nwps"}))
    old = S.score_day(FakeClient(_day_fixture()), date(2026, 11, 14), SPOTS, IDS, "now")
    assert new == old and len(new) == 3


def test_write_upserts_on_the_key():
    c = FakeClient({})
    assert S.write(c, [{"a": 1}]) == 1
    assert c.upserts == [("accuracy_daily", [{"a": 1}], "day,kind,source,region,point,lead_day,measure")]


# --------------------------------------------------------------------------- #
# Catch-up: a missed night is scored the next time                             #
# --------------------------------------------------------------------------- #

def _scored(*days):
    return FakeClient({"accuracy_daily": [{"day": d} for d in days]})


def test_is_scored_asks_for_one_row_of_that_day():
    c = _scored("2026-11-14")
    assert S.is_scored(c, date(2026, 11, 14)) is True
    assert S.is_scored(c, date(2026, 11, 15)) is False
    q = c.calls[0]
    assert (q.table, q.filters, q.rng) == ("accuracy_daily", [("day", "=", "2026-11-14")], (0, 0))


def test_a_hole_before_the_window_is_scored_first():
    # 20 Nov, 3 days: the window is 17-19 Nov; 13-16 Nov are checked; 15 Nov has no rows.
    c = _scored("2026-11-13", "2026-11-14", "2026-11-16")
    assert S.plan(c, date(2026, 11, 20), 3) == [date(2026, 11, 15), date(2026, 11, 17),
                                                date(2026, 11, 18), date(2026, 11, 19)]
    checked = [q.filters[0][2] for q in c.calls]
    assert checked == ["2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16"]   # not 17-19


def test_the_missed_17_november_run_is_covered_by_the_window():
    # Scored 13-15 Nov on 16 Nov; nothing on 17 Nov. A run on 18 Nov re-scores 15-17 Nov and
    # finds no hole before them: 13 and 14 Nov hold rows.
    c = _scored("2026-11-13", "2026-11-14", "2026-11-15")
    assert S.plan(c, date(2026, 11, 18), 3) == [date(2026, 11, 15), date(2026, 11, 16),
                                                date(2026, 11, 17)]


def test_catch_up_looks_back_45_days_and_scores_10_a_run():
    # 1 Mar 2027 with an empty table: the check starts 45 days back, 15 Jan (after 13 Nov), and
    # the ten oldest holes, 15-24 Jan, go before the window 26-28 Feb.
    got = S.plan(_scored(), date(2027, 3, 1), 3)
    assert got[:10] == [date(2027, 1, d) for d in range(15, 25)]
    assert got[10:] == [date(2027, 2, 26), date(2027, 2, 27), date(2027, 2, 28)]
    assert len(got) == 13


def test_catch_up_never_reaches_before_the_scoreboard_began():
    # 16 Nov: the window 13-15 Nov starts at SCOREBOARD_START, so nothing before it is checked.
    c = _scored()
    assert S.plan(c, date(2026, 11, 16), 3) == [date(2026, 11, 13), date(2026, 11, 14),
                                                date(2026, 11, 15)]
    assert c.calls == []
    # 17 Nov with an empty table: 13 Nov, before the window, is a hole; 12 Nov is not checked.
    c = _scored()
    assert S.plan(c, date(2026, 11, 17), 3)[0] == date(2026, 11, 13)
    assert [q.filters[0][2] for q in c.calls] == ["2026-11-13"]


def test_no_catch_up_is_the_window_alone():
    c = _scored()
    assert S.plan(c, date(2026, 11, 20), 3, catch_up=False) == [
        date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19)]
    assert c.calls == []


def test_main_scores_the_holes_then_the_window(monkeypatch):
    from pipeline import db_import
    c = _scored("2026-11-13", "2026-11-14", "2026-11-16")
    monkeypatch.setattr(db_import, "get_client", lambda: c)
    monkeypatch.setattr(db_import, "_spot_id_map", lambda client: {})
    scored = []
    monkeypatch.setattr(S, "score_day", lambda client, d, spots, ids, at: scored.append(d) or [])
    assert S.main(["--end", "2026-11-20"]) == 0
    assert scored == [date(2026, 11, 15), date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19)]
    scored.clear()
    assert S.main(["--end", "2026-11-20", "--no-catch-up"]) == 0
    assert scored == [date(2026, 11, 17), date(2026, 11, 18), date(2026, 11, 19)]


# --------------------------------------------------------------------------- #
# The workflow                                                                 #
# --------------------------------------------------------------------------- #

def test_the_nightly_workflow_scores_three_days():
    text = yaml_code((ROOT / ".github" / "workflows" / "accuracy-score.yml").read_text())
    ss = steps(text, "score")
    assert [s.get("run") for s in ss][-1] == 'python -m pipeline.accuracy_score --days "$DAYS"'
    assert "- cron: '17 5 * * *'" in text          # the fallback; cron-job.org dispatches 04:47
    assert "DAYS: ${{ inputs.days || '3' }}" in text
