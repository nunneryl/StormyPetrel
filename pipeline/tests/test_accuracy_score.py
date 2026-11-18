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
    assert info == {1: {"region": "Oregon", "buoy_id": "46050"},
                    2: {"region": "Oregon", "buoy_id": "46050"}}      # C is 25.1 km; D none; E no id
    assert zones == {1: "America/Los_Angeles", 2: "America/Los_Angeles",
                     3: "America/Los_Angeles", 4: "America/Los_Angeles"}


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
    assert list(t.sums) == [("spot", "spot", "Oregon", "", 2, "hs")]
    assert _approx(t.sums[("spot", "spot", "Oregon", "", 2, "hs")], n=1, sum_e=-0.5)


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
                   ("buoy", "ww3", "", 1, "dp"): 1, ("spot", "spot", "", 3, "hs"): 1}
    forecasts_q = [q for q in c.calls if q.table == "forecasts"][0]
    assert ("source", "=", "nwps") in forecasts_q.filters


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
    assert [(r["kind"], r["region"], r["lead_day"], r["measure"], r["n"]) for r in rows] == [
        ("spot", "Oregon", 0, "hs", 2)]
    assert math.isclose(rows[0]["sum_e"], 0.4) and math.isclose(rows[0]["sum_abs_e"], 0.4)
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
    assert new == old and len(new) == 1


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
