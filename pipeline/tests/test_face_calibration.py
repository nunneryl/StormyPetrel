"""The nearshore-Hs calibration check (pipeline/face_calibration.py), offline. Every expected
value is worked by hand in the comment beside it; none is read back from the function under
test.

  stats over three pairs (ours, measured): (1.2, 1.0), (0.9, 1.0), (2.5, 2.0)
    differences  0.2, -0.1, 0.5            bias = 0.6 / 3 = 0.200
    ratios       1.2, 0.9, 1.25            median 1.2
    rmse         sqrt((0.04 + 0.01 + 0.25) / 3) = sqrt(0.1) = 0.316228
    mean measured 4 / 3 = 1.333333         scatter index 0.316228 / 1.333333 = 0.237171
    mean ours    4.6 / 3 = 1.533333
    within 20%   1.2 and 0.9 (1.25 is 25% off): 2 / 3 = 0.667;  within 30%: all three, 1.0
    r            ours - mean: -0.333333, -0.633333, 0.966667; measured - mean: -0.333333,
                 -0.333333, 0.666667
                 sum of products 0.111111 + 0.211111 + 0.644444 = 0.966667
                 sum of squares  ours 0.111111 + 0.401111 + 0.934444 = 1.446667;
                                 measured 0.111111 + 0.111111 + 0.444444 = 0.666667
                 r = 0.966667 / sqrt(1.446667 x 0.666667) = 0.966667 / 0.982061 = 0.984324

  2026-10-25 00:00 UTC = 1,792,886,400 s (56 x 365 + 14 leap days = 20,454 days to 1 Jan 2026,
  + 297 days to 25 Oct = 20,751 days x 86,400 s).
"""
from __future__ import annotations

import math
from datetime import datetime, timezone

import pytest

from pipeline import face_calibration as C

H0 = 1792886400            # 2026-10-25 00:00 UTC


@pytest.mark.parametrize("tp, band", [
    (7.99, "<8 s"), (8.0, "8-10 s"), (9.99, "8-10 s"), (10.0, "10-12 s"), (13.99, "12-14 s"),
    (14.0, "14+ s"), (22.0, "14+ s"), (None, None), (float("nan"), None),
])
def test_period_band(tp, band):
    assert C.period_band(tp) == band


def test_hourly_counts_a_reading_toward_every_hour_within_half_an_hour():
    # Readings at 01:00 (1.0 m, 9 s), 01:30 (2.0 m, 11 s), 02:30 (4.0 m, no period).
    #   hour 01:00 <- 01:00 and 01:30: Hs (1 + 2) / 2 = 1.5, Tp (9 + 11) / 2 = 10, 2 readings
    #   hour 02:00 <- 01:30 and 02:30: Hs (2 + 4) / 2 = 3.0, Tp 11 (the one with a period), 2
    #   hour 03:00 <- 02:30:           Hs 4.0, no Tp, 1
    got = C.hourly([3600.0, 5400.0, 9000.0], [1.0, 2.0, 4.0], [9.0, 11.0, None], [True] * 3)
    assert got == {3600: (1.5, 10.0, 2), 7200: (3.0, 11.0, 2), 10800: (4.0, None, 1)}


def test_hourly_drops_flagged_and_missing_readings_and_takes_frf_stamps_to_the_hour():
    # FRF stamps an hour as xx:59:59.99: 3599.99 s is within half an hour of 01:00 only
    # (00:00 is 3599.99 s away). The flagged and the NaN reading count nowhere.
    got = C.hourly([3599.99, 3600.0, 3600.0], [1.25, 9.0, float("nan")], [8.0, 8.0, 8.0],
                   [True, False, True])
    assert got == {3600: (1.25, 8.0, 1)}


def test_stats():
    pairs = [{"ours": 1.2, "measured": 1.0}, {"ours": 0.9, "measured": 1.0},
             {"ours": 2.5, "measured": 2.0}]
    assert C.stats(pairs) == {
        "n": 3, "bias_m": 0.2, "ratio_median": 1.2, "rmse_m": 0.316, "scatter_index": 0.237,
        "mean_measured_m": 1.333, "mean_ours_m": 1.533, "within_20": 0.667, "within_30": 1.0,
        "r": 0.984}


def test_stats_skips_unusable_pairs_and_needs_three_for_r():
    pairs = [{"ours": 1.2, "measured": 1.0}, {"ours": None, "measured": 1.0},
             {"ours": 1.0, "measured": 0.0}, {"ours": 0.9, "measured": 1.0}]
    got = C.stats(pairs)
    assert got["n"] == 2 and got["r"] is None
    assert got["bias_m"] == 0.05                       # (0.2 - 0.1) / 2
    assert C.stats([]) == {"n": 0}
    # Two pairs that both vary would give r = 1 exactly; two points always fit a line, so r
    # is not reported below three.
    assert C.stats([{"ours": 1.0, "measured": 1.0}, {"ours": 2.0, "measured": 3.0}])["r"] is None


def test_pair_rows():
    obs = {H0: (1.0, 9.0, 2)}
    rows = [
        {"valid_time": "2026-10-25T00:00:00+00:00", "hs": 1.2, "tp": 10.0, "swell_hs": 0.9,
         "swell_source": "nwps", "nwps_cycle": "2026102418"},
        {"valid_time": "2026-10-25T01:00:00+00:00", "hs": 1.3, "tp": 10.0},     # no measurement
        {"valid_time": "2026-10-25T00:00:00Z", "hs": None, "tp": 10.0},          # no forecast Hs
    ]
    assert C.pair_rows("Duck Pier", "frf-8m", rows, obs) == [{
        "spot": "Duck Pier", "instrument": "frf-8m", "valid_time": "2026-10-25T00:00Z",
        "ours": 1.2, "measured": 1.0, "ours_tp": 10.0, "measured_tp": 9.0, "band": "8-10 s",
        "readings": 2, "swell_hs": 0.9, "swell_source": "nwps", "nwps_cycle": "2026102418"}]


def test_summarize():
    p = [{"spot": "A", "band": "8-10 s", "ours": 1.2, "measured": 1.0, "swell_source": "nwps"},
         {"spot": "A", "band": "12-14 s", "ours": 0.9, "measured": 1.0, "swell_source": "nwps"},
         {"spot": "B", "band": "8-10 s", "ours": 2.5, "measured": 2.0, "swell_source": "cdip_mop"}]
    s = C.summarize(p)
    assert s["all"]["n"] == 3 and s["all"]["bias_m"] == 0.2
    assert {k: v["n"] for k, v in s["by_spot"].items()} == {"A": 2, "B": 1}
    assert list(s["by_spot_band"]["A"]) == ["8-10 s", "12-14 s"]       # in band order
    assert s["by_spot_band"]["A"]["12-14 s"]["bias_m"] == -0.1
    assert {k: v["n"] for k, v in s["by_band"].items()} == {"8-10 s": 2, "12-14 s": 1}
    assert s["by_band"]["8-10 s"]["bias_m"] == 0.35                    # (0.2 + 0.5) / 2
    assert s["swell_source"] == {"A": {"nwps": 2}, "B": {"cdip_mop": 1}}


def test_months():
    assert C.months(datetime(2025, 11, 15, tzinfo=timezone.utc),
                    datetime(2026, 2, 1, tzinfo=timezone.utc)) == ["202511", "202512", "202601", "202602"]


class _Var:
    def __init__(self, vals):
        import numpy as np
        self._v = np.ma.masked_invalid(np.array(vals, dtype=float))

    def __getitem__(self, k):
        return self._v[k]


class _DS:
    def __init__(self, **v):
        self.variables = {k: _Var(x) for k, x in v.items()}


def test_frf_8m_hourly_keeps_passed_readings_and_skips_missing_months():
    def open_nc(url):
        if url.endswith("_202609.nc"):
            raise OSError("404")
        return _DS(time=[H0 + 0.0, H0 + 3600.0], waveHs=[1.5, 2.0], waveTp=[10.0, 12.0],
                   qcFlagE=[1, 3])                      # the second is 'questionable'
    got, read = C.frf_8m_hourly(datetime(2026, 9, 1, tzinfo=timezone.utc),
                                datetime(2026, 10, 25, tzinfo=timezone.utc), open_nc)
    assert read == ["202610"]
    assert got == {H0: (1.5, 10.0, 1)}


def test_cdip_hourly_reads_the_realtime_file_with_good_flags():
    seen = []

    def open_nc(url):
        seen.append(url)
        return _DS(waveTime=[H0 - 1800.0, H0, H0 + 1800.0], waveHs=[1.0, 2.0, 9.0],
                   waveTp=[8.0, 10.0, 12.0], waveFlagPrimary=[1, 1, 4])
    got, span = C.cdip_hourly("263", open_nc)
    assert seen == [C.CDIP_DODS + "263p1_rt.nc"]
    # 23:30 counts to 23:00 and 00:00; 00:00 to 00:00; the 00:30 reading is flagged bad.
    #   23:00: 1.0 m, 8 s;  00:00: (1 + 2) / 2 = 1.5 m, (8 + 10) / 2 = 9 s
    assert got == {H0 - 3600: (1.0, 8.0, 1), H0: (1.5, 9.0, 2)}
    assert span == ["2026-10-24", "2026-10-25"]


class _Query:
    def __init__(self, fn):
        self.fn, self.f = fn, {}

    def select(self, cols):
        self.f["select"] = cols
        return self

    def in_(self, k, v):
        self.f[k] = v
        return self

    def eq(self, k, v):
        self.f[k] = v
        return self

    def gte(self, k, v):
        self.f["gte"] = (k, v)
        return self

    def order(self, *a):
        return self

    def range(self, a, b):
        self.f["range"] = (a, b)
        return self

    def execute(self):
        class R:
            pass
        r = R()
        r.data = self.fn(self.f)
        return r


class _Client:
    def __init__(self, spots, forecasts):
        self.spots, self.forecasts, self.calls = spots, forecasts, []

    def table(self, name):
        def fn(f):
            self.calls.append((name, dict(f)))
            if name == "spots":
                return [s for s in self.spots if s["name"] in f["name"]]
            a, b = f["range"]
            return [r for r in self.forecasts.get(f["spot_id"], [])][a:b + 1]
        return _Query(fn)


def test_fetch_forecasts_reads_only_the_rated_rows_page_by_page():
    rows = [{"valid_time": f"2026-10-25T00:00:{i % 60:02d}+00:00", "hs": 1.0} for i in range(1500)]
    c = _Client([{"id": 7, "name": "Duck Pier", "lat": 0, "lng": 0},
                 {"id": 8, "name": "Elsewhere", "lat": 0, "lng": 0}], {7: rows})
    got = C.fetch_forecasts(c, ["Duck Pier"], datetime(2026, 10, 1, tzinfo=timezone.utc))
    assert list(got) == ["Duck Pier"] and len(got["Duck Pier"][1]) == 1500
    fc = [f for n, f in c.calls if n == "forecasts"]
    assert [f["range"] for f in fc] == [(0, 999), (1000, 1999)]
    assert all(f["source"] == "nwps" and f["spot_id"] == 7 for f in fc)
    assert fc[0]["gte"] == ("valid_time", "2026-10-01T00:00:00+00:00")
    assert fc[0]["select"] == C.FORECAST_COLS


def test_run_pairs_each_spot_with_its_instrument(monkeypatch):
    # Duck Pier at the FRF 8 m array's own position is 0 km from it.
    monkeypatch.setattr(C, "PAIRS", [("Duck Pier", "frf-8m"), ("Bethany Beach", "cdip-263")])
    spots = [{"id": 1, "name": "Duck Pier", "lat": 36.1872331, "lng": -75.7428842},
             {"id": 2, "name": "Bethany Beach", "lat": 38.537, "lng": -75.044}]
    fc = {1: [{"valid_time": "2026-10-25T00:00:00+00:00", "hs": 1.2, "tp": 10.0}],
          2: [{"valid_time": "2026-10-25T00:00:00+00:00", "hs": 2.5, "tp": 10.0}]}

    def open_nc(url):
        if "8m-array" in url:
            return _DS(time=[float(H0)], waveHs=[1.0], waveTp=[9.0], qcFlagE=[1])
        if "263p1" in url:
            return _DS(waveTime=[float(H0)], waveHs=[2.0], waveTp=[13.0], waveFlagPrimary=[1])
        raise OSError("404")
    rep = C.run(_Client(spots, fc), datetime(2026, 10, 1, tzinfo=timezone.utc),
                datetime(2026, 10, 25, tzinfo=timezone.utc), open_nc)
    assert [(p["spot"], p["ours"], p["measured"], p["band"]) for p in rep["pairs"]] == [
        ("Duck Pier", 1.2, 1.0, "8-10 s"), ("Bethany Beach", 2.5, 2.0, "12-14 s")]
    cov = {c["spot"]: c for c in rep["coverage"]}
    assert cov["Duck Pier"]["km"] == 0.0 and cov["Duck Pier"]["measured_span"] == ["202610"]
    assert cov["Bethany Beach"]["paired"] == 1 and cov["Bethany Beach"]["depth_m"] == 10.6
    assert rep["summary"]["by_spot"]["Bethany Beach"]["bias_m"] == 0.5


def test_an_unreadable_instrument_is_reported_not_raised(monkeypatch):
    monkeypatch.setattr(C, "PAIRS", [("Bethany Beach", "cdip-263")])
    spots = [{"id": 2, "name": "Bethany Beach", "lat": 38.537, "lng": -75.044}]

    def open_nc(url):
        raise OSError("403 Forbidden")
    rep = C.run(_Client(spots, {2: []}), datetime(2026, 10, 1, tzinfo=timezone.utc),
                datetime(2026, 10, 25, tzinfo=timezone.utc), open_nc)
    (c,) = rep["coverage"]
    assert c["paired"] == 0 and c["measured_span"][0].startswith("unreadable: 403 Forbidden")
