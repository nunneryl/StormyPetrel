"""pipeline/accuracy_report.py: the weekly report's figures and tables, from accuracy_daily and
partition_archive. Every expected value is worked by hand in the comments.
"""
from __future__ import annotations

import csv
import math
from datetime import date
from pathlib import Path

import pytest

from pipeline import accuracy_report as R
from pipeline.accuracy_score import Totals
from pipeline.tests.test_ci_workflow import yaml_code
from pipeline.tests.test_column_preflight import steps

ROOT = Path(__file__).resolve().parents[2]


def _sums(pairs):
    """Sums for (forecast, observed) pairs."""
    t = Totals()
    for f, o in pairs:
        t.add(("k",), "p", f, o, f - o)
    return t.sums[("k",)]


def test_stats_from_the_sums():
    # (2,1), (1,2), (3,3): errors 1, -1, 0. bias 0; MAE 2/3; RMSE sqrt(2/3) = 0.8165;
    # mean observed 2, so SI 0.4082; r = (3*13 - 6*6) / sqrt((3*14 - 36)(3*14 - 36)) = 3/6.
    st = R.stats(_sums([(2, 1), (1, 2), (3, 3)]))
    assert st["n"] == 3 and st["bias"] == 0.0
    assert math.isclose(st["mae"], 2 / 3) and math.isclose(st["rmse"], math.sqrt(2 / 3))
    assert math.isclose(st["si"], math.sqrt(2 / 3) / 2) and math.isclose(st["r"], 0.5)


def test_scatter_index_is_over_the_mean_observed():
    # (2,1), (4,3): errors 1, 1. RMSE 1; mean observed 2, so SI 0.5 (over the mean forecast, 3,
    # it would be 0.33). r = (2*14 - 6*4) / sqrt((2*20 - 36)(2*10 - 16)) = 4/4 = 1.
    st = R.stats(_sums([(2, 1), (4, 3)]))
    assert (st["bias"], st["rmse"], st["si"], st["r"]) == (1.0, 1.0, 0.5, 1.0)


def test_correlation_with_unequal_spreads():
    # (1,1), (3,2): sum_f 4, sum_o 3, sum_ff 10, sum_oo 5, sum_fo 7. n*sum_ff - sum_f^2 = 4,
    # n*sum_oo - sum_o^2 = 1, so r = (14 - 12) / sqrt(4 * 1) = 1.
    assert R.stats(_sums([(1, 1), (3, 2)]))["r"] == 1.0


def test_stats_undefined_parts():
    assert R.stats({"n": 0}) == {"n": 0, "bias": None, "mae": None, "rmse": None, "si": None, "r": None}
    st = R.stats(_sums([(1, 2), (1, 2)]))          # forecast never varies: no correlation
    assert st["r"] is None and st["bias"] == -1.0


def _row(day, region, lead, n, e, kind="buoy", source="ww3", measure="hs", point="", points=1,
         f=None, o=None):
    """A daily row of n pairs each with forecast f, observed o and error e = f - o."""
    f = 1.0 + e if f is None else f
    o = 1.0 if o is None else o
    return {"day": day, "kind": kind, "source": source, "region": region, "point": point,
            "lead_day": lead, "measure": measure, "n": n, "points": points,
            "sum_f": n * f, "sum_o": n * o, "sum_ff": n * f * f, "sum_oo": n * o * o, "sum_fo": n * f * o,
            "sum_e": n * e, "sum_abs_e": n * abs(e), "sum_ee": n * e * e}


def test_combine_adds_the_days_and_keeps_the_largest_point_count():
    rows = [_row("2026-11-09", "Oregon", 1, 30, 0.2, points=4), _row("2026-11-10", "Oregon", 1, 30, -0.4, points=6)]
    s = R.combine(rows, lambda r: r["region"])["Oregon"]
    # n 60; sum_e 30*0.2 + 30*-0.4 = -6; |e| 6 + 12 = 18
    assert (s["n"], s["points"]) == (60, 6)
    assert math.isclose(s["sum_e"], -6.0) and math.isclose(s["sum_abs_e"], 18.0)


def test_a_cell_below_50_pairs_shows_only_n():
    assert R.cell(R.stats(_sums([(2, 1)] * 49))) == "n=49"
    assert R.cell(R.stats(_sums([(2, 1)] * 50))) == "1.00 / 1.00 (50)"
    assert R.cell(R.stats({"n": 0})) == ""


def test_region_lead_table():
    rows = [_row("2026-11-09", "Oregon", 1, 30, 0.2), _row("2026-11-10", "Oregon", 1, 30, -0.4),
            _row("2026-11-10", "Texas", 1, 20, 0.1), _row("2026-11-10", "Oregon", 2, 50, 0.3),
            _row("2026-11-10", "Oregon", 2, 50, 9.9, source="nwps")]          # another source
    t = R.region_lead_table(rows, "buoy", "ww3")
    # Oregon day 1: bias -6/60 = -0.10, MAE 18/60 = 0.30; Texas 20 pairs: n only. All day 1: 80
    # pairs, bias (-6 + 2)/80 = -0.05, MAE (18 + 2)/80 = 0.25. Day 2 Oregon: 0.30 / 0.30.
    assert t.splitlines()[2:] == [
        "| Oregon | -0.10 / 0.30 (60) | 0.30 / 0.30 (50) |",
        "| Texas | n=20 |  |",
        "| **All** | -0.05 / 0.25 (80) | 0.30 / 0.30 (50) |"]


def test_buoy_ranking_by_day_2_mae():
    rows = [_row("d", "Oregon", 2, 20, e, point=p) for p, e in
            (("46050", 0.5), ("46029", -0.1), ("46089", 0.3), ("46041", 0.3))]
    rows += [_row("d", "Oregon", 2, 19, 0.0, point="46211"),          # too few pairs
             _row("d", "Oregon", 1, 20, 0.0, point="46243"),          # another lead day
             _row("d", "Oregon", 2, 99, 0.0)]                         # the region row
    best, worst = R.buoy_ranking(rows, "ww3", k=3)
    assert [b[0] for b in best] == ["46029", "46041", "46089"]       # 0.1, 0.3, 0.3 (ties by id)
    assert [w[0] for w in worst] == ["46050", "46041", "46089"]


@pytest.mark.parametrize("lead, b", [(0, 0), (23, 0), (24, 1), (47, 1), (48, 2), (72, 2)])
def test_partition_lead_bins(lead, b):
    assert R.pa_lead_bin(lead) == b


def test_partition_skill():
    vh = "2026-11-10T12:00:00+00:00"
    buoy = [{"valid_hour": vh, "wfo": "okx", "buoy_id": "44025", "spec_wvht": 1.5, "hs_total": 1.45,
             "spec_swh": 1.0, "spec_mwd": 170.0},                        # .txt WVHT preferred
            {"valid_hour": vh, "wfo": "box", "buoy_id": "44097", "spec_wvht": None, "hs_total": 0.4,
             "spec_swh": None, "hs_swell": 0.3, "spec_mwd": 100.0}]
    model = [{"valid_hour": vh, "wfo": "okx", "buoy_id": "44025", "lead_hours": 5, "swh": 1.7,
              "shts": 0.9, "dirpw": 190.0},
             {"valid_hour": vh, "wfo": "okx", "buoy_id": "44025", "lead_hours": 30, "swh": 1.2,
              "shts": None, "dirpw": 150.0},
             {"valid_hour": vh, "wfo": "box", "buoy_id": "44097", "lead_hours": 72, "swh": 0.6,
              "shts": 0.5, "dirpw": 90.0},                               # buoy 0.4 m: no direction
             {"valid_hour": "2026-11-10T13:00:00+00:00", "wfo": "okx", "buoy_id": "44025",
              "lead_hours": 6, "swh": 9.0}]                              # no buoy row that hour
    t = R.partition_skill(model, buoy)
    s = t.sums
    assert math.isclose(s[("pa", "nwps", "okx/44025", "", 0, "hs")]["sum_e"], 0.2)     # 1.7 - 1.5
    assert math.isclose(s[("pa", "nwps", "okx/44025", "", 0, "swell_hs")]["sum_e"], -0.1)
    assert s[("pa", "nwps", "okx/44025", "", 0, "dp")]["sum_e"] == 20.0
    assert math.isclose(s[("pa", "nwps", "okx/44025", "", 1, "hs")]["sum_e"], -0.3)
    assert s[("pa", "nwps", "okx/44025", "", 1, "dp")]["sum_e"] == -20.0
    assert math.isclose(s[("pa", "nwps", "box/44097", "", 2, "hs")]["sum_e"], 0.2)     # hs_total fallback
    assert math.isclose(s[("pa", "nwps", "box/44097", "", 2, "swell_hs")]["sum_e"], 0.2)
    assert ("pa", "nwps", "box/44097", "", 2, "dp") not in s
    assert s[("pa", "nwps", "", "", 0, "hs")]["n"] == 1 and s[("pa", "nwps", "", "", 2, "hs")]["n"] == 1


def test_build_and_csvs(tmp_path):
    rows = [_row("2026-11-10", "Oregon", 1, 60, 0.2, points=5),
            _row("2026-11-10", "Oregon", 2, 25, 0.5, point="46050"),
            _row("2026-11-10", "Oregon", 1, 60, -0.1, kind="spot", source="spot"),
            _row("2026-11-10", "Oregon", 1, 60, 5.0, measure="dp", f=250.0, o=245.0)]
    pa = Totals()
    for _ in range(50):
        pa.add(("pa", "nwps", "", "", 0, "hs"), "okx/44025", 1.2, 1.0, 0.2)
        pa.add(("pa", "nwps", "okx/44025", "", 0, "hs"), "okx/44025", 1.2, 1.0, 0.2)
    summary, full = R.build(rows, pa, date(2026, 11, 9), date(2026, 11, 16))
    assert summary.startswith("## Accuracy scoreboard: 2026-11-09 to 2026-11-15 (UTC)")
    assert "| Oregon | 5 / 60 |  | 1 / 60 |" in summary                      # coverage
    assert "| Oregon | 0.20 / 0.20 (60) |" in summary                        # ww3 height
    assert "| Spots against their buoy (within 25 km) | Day 1 |" not in summary  # no spot period
    assert "| WAVEWATCH III at the buoys | Day 1 | 5 | 5 | 5 | 60 |" in summary   # direction
    assert "| 46050 | Oregon | 0.50 | 0.50 | 0.50 | 25 |" in summary          # best and worst
    assert "| Wave height (m) | 0-23 h | 0.20 | 0.20 | 0.20 | – | 50 |" in summary  # r undefined
    assert "### okx/44025" in full and "### okx/44025" not in summary
    R.write_csvs(tmp_path, rows, pa)
    acc = list(csv.DictReader(open(tmp_path / "accuracy.csv")))
    assert {(r["kind"], r["source"], r["lead_day"], r["measure"], r["n"], r["bias"]) for r in acc} == {
        ("buoy", "ww3", "1", "hs", "60", "0.2000"), ("spot", "spot", "1", "hs", "60", "-0.1000"),
        ("buoy", "ww3", "1", "dp", "60", "5.0000")}
    assert list(csv.DictReader(open(tmp_path / "buoys.csv")))[0]["buoy"] == "46050"
    pls = list(csv.DictReader(open(tmp_path / "partition_lead_skill.csv")))
    assert [(r["zone"], r["lead_bin"], r["n"]) for r in pls] == [("all", "0-23 h", "50"),
                                                               ("okx/44025", "0-23 h", "50")]


def test_the_weekly_workflow_keeps_the_report_it_writes():
    text = yaml_code((ROOT / ".github" / "workflows" / "accuracy-report.yml").read_text())
    ss = steps(text, "report")
    runs = [s.get("run", "") for s in ss]
    assert 'python -m pipeline.accuracy_report --days "$DAYS" --out accuracy-report' in runs
    assert any(s.get("uses", "").startswith("actions/upload-artifact@") for s in ss)
    assert ("        with:\n          name: accuracy-report\n          path: accuracy-report/\n"
            "          retention-days: 90\n") in text
    assert "- cron: '53 5 * * 1'" in text          # the fallback; cron-job.org dispatches 05:23
