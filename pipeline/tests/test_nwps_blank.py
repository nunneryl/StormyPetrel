"""pipeline/nwps_blank.py: blank NWPS hours per cycle and in the archive, against the tide.

Every expected value is worked by hand. 2026-11-06T00:00Z is hour 498312 since the epoch:
20763 days (56 years with 14 leap days, then 309 days into 2026) times 24.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from pipeline import nwps_blank as B

H0 = 498312                                  # 2026-11-06T00Z


def iso(h):
    """The ISO time of hour H0 + h, written out: 2026-11-06T00:00:00Z plus h hours."""
    day, hour = divmod(h, 24)
    return f"2026-11-{6 + day:02d}T{hour:02d}:00:00Z"


def test_the_hour_number():
    assert B._hour("2026-11-06T00:00:00Z") == H0
    assert B._hour("2026-11-06T05:00:00+00:00") == H0 + 5
    assert B._iso(H0 + 27) == "2026-11-07T03Z"


def test_runs():
    assert B.runs({10, 11, 12, 20, 21, 40}) == [(10, 3), (20, 2), (40, 1)]
    assert B.runs(set()) == []
    assert B.runs({1, 3, 4}) == [(1, 1), (3, 2)]                # one hour apart: two runs


# The tide: 24 hours with tides 0..23 ft. Sorted, the low quarter is at rank floor(0.25 * 23)
# = 5, 5 ft; the high quarter at rank floor(0.75 * 23) = 17, 17 ft.
def _obs(blank_tides):
    return [(float(t), t in blank_tides) for t in range(24)]


@pytest.mark.parametrize("blank, verdict, low, high", [
    ({0, 1, 2, 3, 4, 5}, "low tide", 1.0, 0.0),                    # 6 of 6 at or under 5 ft
    ({0, 1, 2, 3, 12, 13}, "low tide", 0.667, 0.0),                # 4 of 6
    ({0, 1, 2, 12, 13, 14}, "not tidal", 0.5, 0.0),                # 3 of 6
    ({18, 19, 20, 21, 22, 23}, "high tide", 0.0, 1.0),             # at or over 17 ft
    ({0, 1, 2, 3, 4, 5, 8, 9, 10, 11}, "low tide", 0.6, 0.0),      # exactly 6 of 10
    ({0, 1, 2, 3, 4, 8, 9, 10, 11, 12}, "not tidal", 0.5, 0.0),    # 5 of 10
])
def test_the_tide_verdict(blank, verdict, low, high):
    got = B.tide_test(_obs(blank))
    assert (got["verdict"], got["low_share"], got["high_share"]) == (verdict, low, high)
    assert (got["low_quarter_ft"], got["high_quarter_ft"]) == (5.0, 17.0)


def test_too_few_blank_hours_or_tides():
    assert B.tide_test(_obs({0, 1, 2, 3, 4}))["verdict"] == "too few"           # 5 blank
    assert B.tide_test([(1.0, True), (2.0, True), (3.0, False)])["verdict"] == "too few"


def test_the_medians():
    got = B.tide_test(_obs({0, 1, 2, 3, 4, 5}))
    assert (got["median_tide_blank"], got["median_tide_other"]) == (2.5, 14.5)   # 6..23


def test_the_run_pattern():
    # Runs start at 0, 12, 25 and 37: gaps 12, 13, 12, median 12; 4 runs over 48 hours is 2 a day.
    assert B.run_pattern({0, 1, 12, 13, 25, 37}, 48) == {"runs": 4, "runs_per_day": 2.0,
                                                          "median_gap_h": 12}
    assert B.run_pattern(set(), 48) == {"runs": 0, "runs_per_day": 0.0, "median_gap_h": None}


def _e(h, hs=1.0, tp=10.0, dp=270.0):
    return {"valid_time": iso(h), "hs": hs, "tp": tp, "dp": dp}


def test_blank_by_cycle():
    # eka has hours 0..3 (B has all four). A lacks hour 3 and hour 1's dp; D has no entries.
    # mfr's only spot C has no entries, so mfr has no hours and C is left out.
    by_spot = {"A": [_e(0), _e(1, dp=None), _e(2)], "B": [_e(0), _e(1), _e(2), _e(3)]}
    wfo = {"A": "eka", "B": "eka", "C": "mfr", "D": "eka"}
    got = B.blank_by_cycle(by_spot, wfo, ["A", "B", "C", "D"])
    hours = [H0, H0 + 1, H0 + 2, H0 + 3]
    assert got == {"A": {"hours": hours, "blank": [H0 + 1, H0 + 3]},
                   "B": {"hours": hours, "blank": []},
                   "D": {"hours": hours, "blank": hours}}


def _row(h, *, hs=1.0, stars=3, src="nwps_height_ww3_dir", tide=None, cycle=None):
    return {"valid_time": iso(h), "hs": hs, "tp": 10.0, "dp": 270.0, "stars": stars,
            "swell_source": src, "tide_level_ft": tide, "nwps_cycle": cycle}


def test_the_archive():
    rows = [
        _row(0, cycle="c1"), _row(1, hs=None, cycle="c1"), _row(2, cycle="c1"),
        _row(3, hs=None, src="ww3", cycle="c2"),           # empty, not NWPS-rated
        _row(4, stars=None, hs=None, cycle="c2"),          # not rated: left out
        _row(5, hs=None, cycle="c2"),
        _row(6, hs=None, cycle="c3"),                      # at "now": left out
    ]
    got = B.archive(rows, H0, H0 + 6)
    assert (got["hours"], got["filled"], got["unrated_blank"]) == (5, 2, 1)
    assert (got["cycles"], got["cycles_with_filled"]) == (2, 2)
    assert got["filled_hours"] == ["2026-11-06T01Z", "2026-11-06T05Z"]
    assert (got["runs"], got["median_gap_h"]) == (2, 4)
    assert got["tide"]["verdict"] == "too few"              # no stored tide


class _Q:
    def __init__(self, rows):
        self.rows = rows

    def select(self, *_):
        return self

    def order(self, *_):
        return self

    def eq(self, k, v):
        return _Q([r for r in self.rows if r.get(k) == v])

    def gte(self, k, v):
        return _Q([r for r in self.rows if B._hour(r[k]) >= B._hour(v)] if k == "valid_time"
                  else [r for r in self.rows if r.get(k) >= v])

    def range(self, a, b):
        return _Q(self.rows[a:b + 1])

    def execute(self):
        return type("R", (), {"data": self.rows})()


class _Client:
    def __init__(self, tables):
        self.t = tables

    def table(self, name):
        return _Q(self.t[name])


def test_run_end_to_end():
    """Moonstone Beach Humboldt and Trinidad on eka, Bandon Beach on mfr, one non-NWPS spot.
    Two cycles listed for eka, one for mfr. Cycle 0: eka has hours 0..23; Moonstone is blank
    at 0..5 and 12..17 (two runs, 12 hours apart, when the stored tide is lowest); Bandon (mfr)
    has hours 0..23, all complete. Cycle 1: the fetch fails. The stored tide is 0 ft at the
    blank hours and 4 ft at the others."""
    roster = [
        {"name": "Moonstone Beach Humboldt", "swell_window_source": "nwps", "nwps_wfo": "eka",
         "nwps_node_lat": 41.04, "nwps_node_lng": -124.14},
        {"name": "Trinidad", "swell_window_source": "nwps", "nwps_wfo": "eka"},
        {"name": "Bandon Beach", "swell_window_source": "nwps", "nwps_wfo": "mfr"},
        {"name": "Somewhere Else", "swell_window_source": "cdip_mop", "nwps_wfo": "sgx"},
    ]
    blank = set(range(0, 6)) | set(range(12, 18))
    cyc0 = {"Moonstone Beach Humboldt": [_e(h) for h in range(24) if h not in blank],
            "Trinidad": [_e(h) for h in range(24)],
            "Bandon Beach": [_e(h) for h in range(24)]}

    def fetch(r, k, cands):
        assert r is roster
        if k == 0:
            return cyc0
        raise OSError("NOMADS down")

    cands = {"eka": [("20261106", "00"), ("20261105", "18")], "mfr": [("20261106", "00")]}
    forecasts = [{"spot_id": sid, "source": "nwps", **_row(h, tide=0.0 if (sid == 1 and h in blank) else 4.0)}
                 for sid in (1, 2, 3) for h in range(24)]
    client = _Client({"spots": [{"id": 1, "name": "Moonstone Beach Humboldt"}, {"id": 2, "name": "Trinidad"},
                                {"id": 3, "name": "Bandon Beach"}],
                      "forecasts": forecasts})
    rep = B.run(client, roster, ["trinidad"], since=iso(0), n_cycles=2,
                now=datetime(2026, 11, 7, 0, tzinfo=timezone.utc), fetch=fetch,
                candidates=lambda w: cands[w])
    assert rep["candidates"] == {"eka": ["20261106 00Z", "20261105 18Z"], "mfr": ["20261106 00Z"]}
    assert rep["errors"] == {"fetch cycle 1": "OSError: NOMADS down"}
    by = {e["spot"]: e for e in rep["spots"]}
    m = by["moonstone-beach-humboldt"]
    assert m["cycles"] == [{"cycle": "eka 20261106 00Z", "hours": 24, "blank": 12,
                            "blank_runs": ["2026-11-06T00Z+6h", "2026-11-06T12Z+6h"]}]
    assert (m["cycle_blank"], m["cycle_hours"], m["cycle_blank_share"]) == (12, 24, 0.5)
    # 24 tides: twelve 0 ft, twelve 4 ft. Low quarter at rank 5 is 0 ft: all 12 blank hours.
    assert (m["cycle_tide"]["verdict"], m["cycle_tide"]["low_share"]) == ("low tide", 1.0)
    assert m["cycle_pattern"] == {"runs": 2, "runs_per_day": 2.0, "median_gap_h": 12}
    assert by["bandon-beach"]["cycle_blank"] == 0
    assert by["bandon-beach"]["cycle_tide"]["verdict"] == "too few"
    assert "somewhere-else" not in by
    assert [e["spot"] for e in rep["spots"]][0] == "moonstone-beach-humboldt"
    assert {e["spot"] for e in rep["named"]} == {"moonstone-beach-humboldt", "bandon-beach", "trinidad"}
    assert rep["summary"] == {"nwps_spots": 3, "with_blank_in_a_cycle": 1,
                              "with_blank_in_every_cycle": 1, "with_filled_in_archive": 0,
                              "cycle_verdicts": {"low tide": 1, "too few": 2},
                              "archive_verdicts": {"too few": 3}}
    assert len(B.csv_rows(rep)) == 3


def test_fetch_cycle_offers_each_office_only_its_kth_cycle(monkeypatch):
    from pipeline.forecast import nwps as N
    orig = N.candidate_cycles
    seen = {}

    def fake_fetch(spots, use_cache=True):
        seen.update({w: N.candidate_cycles(w) for w in ("eka", "mfr", "box")}, use_cache=use_cache)
        spots[0]["name"] = "changed"
        return {"x": []}

    monkeypatch.setattr(N, "fetch", fake_fetch)
    roster = [{"name": "A"}]
    cands = {"eka": [("20261106", "00"), ("20261105", "18")], "mfr": [("20261106", "00")]}
    assert B.fetch_cycle(roster, 1, cands) == {"x": []}
    assert seen == {"eka": [("20261105", "18")], "mfr": [], "box": [], "use_cache": True}
    assert roster[0]["name"] == "A"                  # the fetch had a copy
    assert N.candidate_cycles is orig                # and the real listing is back

    def failing(spots, use_cache=True):
        raise OSError("down")

    monkeypatch.setattr(N, "fetch", failing)
    with pytest.raises(OSError):
        B.fetch_cycle(roster, 0, cands)
    assert N.candidate_cycles is orig                # back after a failure too
