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


def test_coverage_counts_only_the_three_sources():
    # Texas has only spot_model rows (its buoys silent, so no spot or buoy pairs): no line for it.
    # Oregon's NWPS column is its buoys, not its spots against NWPS at the buoy.
    rows = [_row("d", "Oregon", 1, 60, 0.2, points=5),
            _row("d", "Oregon", 1, 40, -0.3, kind="spot_model", source="nwps", points=4),
            _row("d", "Texas", 1, 30, -0.3, kind="spot_model", source="nwps", points=3)]
    assert R.coverage_table(rows).splitlines()[2:] == ["| Oregon | 5 / 60 |  |  |"]


def test_buoy_ranking_leaves_out_the_spots():
    # A spot's own row against NWPS at its buoy is source nwps with a point too: not a buoy.
    rows = [_row("d", "Oregon", 2, 20, 0.4, source="nwps", point="46050"),
            _row("d", "Oregon", 2, 30, 0.0, kind="spot_model", source="nwps", point="dania-beach-pier"),
            _row("d", "Oregon", 2, 30, 0.0, kind="spot", source="spot", point="ponce-inlet")]
    best, worst = R.buoy_ranking(rows, "nwps")
    assert [b[0] for b in best] == ["46050"] and [w[0] for w in worst] == ["46050"]


def _gap_rows():
    # Day 1 wave height. Spot minus NWPS at the buoy: a -0.5 (20), b +0.7 (30), c -0.7 (25), d -2.0
    # but only 19 pairs, e +3.0 on day 2. Spot a against its buoy over two days: 10 at -1.0 and 10
    # at -0.6, so -0.8 (20). NWPS at 46050 against 46050: -0.3 (24); its WAVEWATCH III row and
    # day-2 row must not be used. A spot_model region row (point '') is not a spot.
    return [_row("d1", "Oregon", 1, 20, -0.5, kind="spot_model", source="nwps", point="a"),
            _row("d1", "Oregon", 1, 30, 0.7, kind="spot_model", source="nwps", point="b"),
            _row("d1", "Texas", 1, 25, -0.7, kind="spot_model", source="nwps", point="c"),
            _row("d1", "Texas", 1, 19, -2.0, kind="spot_model", source="nwps", point="d"),
            _row("d1", "Texas", 2, 40, 3.0, kind="spot_model", source="nwps", point="e"),
            _row("d1", "Oregon", 1, 50, 9.0, kind="spot_model", source="nwps"),
            _row("d1", "Oregon", 1, 10, -1.0, kind="spot", source="spot", point="a"),
            _row("d2", "Oregon", 1, 10, -0.6, kind="spot", source="spot", point="a"),
            _row("d1", "Oregon", 1, 24, -0.3, source="nwps", point="46050"),
            _row("d1", "Oregon", 1, 24, 5.0, source="ww3", point="46050"),
            _row("d1", "Oregon", 2, 24, 4.0, source="nwps", point="46050")]


GAP_ROSTER = {"a": {"buoy_id": "46050", "km": 6.5, "node_m": 1551},
              "b": {"buoy_id": "46050", "km": 25.0, "node_m": None}}


def test_spot_gaps_largest_first():
    gaps = R.spot_gaps(_gap_rows(), GAP_ROSTER, k=3)
    # |0.7| twice: b before c by slug; then a (0.5). d too few pairs; e another lead day.
    assert [g["slug"] for g in gaps] == ["b", "c", "a"]
    b, c, a = gaps
    assert (b["region"], b["gap"]["n"], b["vs_buoy"]["n"], b["nwps_vs_buoy"]["n"]) == ("Oregon", 30, 0, 24)
    assert math.isclose(b["gap"]["bias"], 0.7) and math.isclose(b["nwps_vs_buoy"]["bias"], -0.3)
    assert (c["region"], c.get("buoy_id"), c["nwps_vs_buoy"]["n"]) == ("Texas", None, 0)
    assert math.isclose(a["vs_buoy"]["bias"], -0.8) and a["vs_buoy"]["n"] == 20
    assert [g["slug"] for g in R.spot_gaps(_gap_rows(), GAP_ROSTER, k=2)] == ["b", "c"]
    assert R.GAP_K == 20 and R.GAP_LEAD_DAY == 1 and R.GAP_MIN_N == 20


def test_gap_table():
    lines = R.gap_table(R.spot_gaps(_gap_rows(), GAP_ROSTER, k=3)).splitlines()
    assert lines[0] == ("| Spot | Region | Buoy | Spot − NWPS at buoy (n) | Spot − buoy (n) | "
                        "NWPS at buoy − buoy (n) | Sample point from spot (m) |")
    assert lines[2:] == [
        "| b | Oregon | 46050 (25.0 km) | 0.70 (30) |  | -0.30 (24) | – |",
        "| c | Texas | – | -0.70 (25) |  |  | – |",
        "| a | Oregon | 46050 (6.5 km) | -0.50 (20) | -0.80 (20) | -0.30 (24) | 1551 |"]


def test_spot_roster_keys_are_the_database_slugs():
    roster = R.spot_roster([
        {"name": "Ponce Inlet", "nearest_buoy_id": "41070", "nearest_buoy_dist_km": 23.4,
         "nwps_node_distance_m": 820},
        {"name": "Somewhere Else", "nearest_buoy_id": None},
        {"name": None}])
    assert roster == {"ponce-inlet": {"buoy_id": "41070", "km": 23.4, "node_m": 820},
                      "somewhere-else": {"buoy_id": None, "km": None, "node_m": None}}


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
            _row("2026-11-10", "Oregon", 1, 60, 5.0, measure="dp", f=250.0, o=245.0),
            # per-spot rows and the spot_model kind: in none of the tables above, nor buoys.csv
            _row("2026-11-10", "Oregon", 1, 40, -0.3, kind="spot_model", source="nwps", points=4),
            _row("2026-11-10", "Oregon", 2, 25, -0.3, kind="spot_model", source="nwps", point="a"),
            _row("2026-11-10", "Oregon", 1, 25, -0.4, kind="spot_model", source="nwps", point="a"),
            _row("2026-11-10", "Oregon", 1, 22, -0.2, kind="spot", source="spot", point="a")]
    pa = Totals()
    for _ in range(50):
        pa.add(("pa", "nwps", "", "", 0, "hs"), "okx/44025", 1.2, 1.0, 0.2)
        pa.add(("pa", "nwps", "okx/44025", "", 0, "hs"), "okx/44025", 1.2, 1.0, 0.2)
    summary, full = R.build(rows, pa, date(2026, 11, 9), date(2026, 11, 16),
                            {"a": {"buoy_id": "46050", "km": 6.5, "node_m": 1551}})
    assert summary.startswith("## Accuracy scoreboard: 2026-11-09 to 2026-11-15 (UTC)")
    assert "| Oregon | 5 / 60 |  | 1 / 60 |" in summary                      # coverage
    assert "| Oregon | 0.20 / 0.20 (60) |" in summary                        # ww3 height
    assert "| Spots against their buoy (within 25 km) | Day 1 |" not in summary  # no spot period
    assert "| WAVEWATCH III at the buoys | Day 1 | 5 | 5 | 5 | 60 |" in summary   # direction
    assert "| 46050 | Oregon | 0.50 | 0.50 | 0.50 | 25 |" in summary          # best and worst
    assert "| a | Oregon |" not in summary.split("### Spots furthest")[0]      # no spot as a buoy
    assert "### Spots furthest from NWPS at their buoy: top 20, day-1" in summary
    # a: day 1 spot - NWPS -0.40 (25); spot - buoy -0.20 (22); no NWPS row at 46050 on day 1
    assert "| a | Oregon | 46050 (6.5 km) | -0.40 (25) | -0.20 (22) |  | 1551 |" in summary
    assert "| Wave height (m) | 0-23 h | 0.20 | 0.20 | 0.20 | – | 50 |" in summary  # r undefined
    assert "### okx/44025" in full and "### okx/44025" not in summary
    R.write_csvs(tmp_path, rows, pa)
    acc = list(csv.DictReader(open(tmp_path / "accuracy.csv")))
    assert {(r["kind"], r["source"], r["lead_day"], r["measure"], r["n"], r["bias"]) for r in acc} == {
        ("buoy", "ww3", "1", "hs", "60", "0.2000"), ("spot", "spot", "1", "hs", "60", "-0.1000"),
        ("buoy", "ww3", "1", "dp", "60", "5.0000"), ("spot_model", "nwps", "1", "hs", "40", "-0.3000")}
    assert [r["buoy"] for r in csv.DictReader(open(tmp_path / "buoys.csv"))] == ["46050"]
    spots = list(csv.DictReader(open(tmp_path / "spots.csv")))
    assert [(r["spot"], r["region"], r["lead_day"], r["against"], r["n"], r["bias"]) for r in spots] == [
        ("a", "Oregon", "1", "buoy", "22", "-0.2000"), ("a", "Oregon", "1", "nwps_at_buoy", "25", "-0.4000"),
        ("a", "Oregon", "2", "nwps_at_buoy", "25", "-0.3000")]
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


# --------------------------------------------------------------------------- #
# Spots whose sample point moved                                               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("day, lead, want", [
    # moved 24 Nov. Day 1: valid 23 Nov from cycles dated 21-22 Nov (old); valid 24 and 25 Nov
    # from cycles dated 22-24 Nov, which the old and the new runs could both have read; valid
    # 26 Nov from cycles dated 24-25 Nov (new).
    ("2026-11-23", 1, "before"), ("2026-11-24", 1, None), ("2026-11-25", 1, None),
    ("2026-11-26", 1, "after"),
    # day 0: valid 22 Nov old, 23 and 24 Nov mixed, 25 Nov new; day 3: 25 Nov old, 28 Nov new
    ("2026-11-22", 0, "before"), ("2026-11-23", 0, None), ("2026-11-24", 0, None),
    ("2026-11-25", 0, "after"), ("2026-11-25", 3, "before"), ("2026-11-27", 3, None),
    ("2026-11-28", 3, "after"),
])
def test_move_split(day, lead, want):
    assert R.move_split(date.fromisoformat(day), date(2026, 11, 24), lead) == want


def test_load_moves(tmp_path):
    p = tmp_path / "o.json"
    p.write_text('{"overrides": {"a": {"moved_on": "2026-11-24"}, "b": {}, "c": {"moved_on": "soon"}}}')
    assert R.load_moves(p) == {"a": date(2026, 11, 24)}
    assert R.load_moves(tmp_path / "absent.json") == {}


def test_the_committed_moves_carry_their_dates():
    m = R.load_moves()
    # run 82's thirteen took effect with #353 (merged 00:44Z on 24 Nov); run 71's six on 12 Nov
    for slug in ("brookings-beach", "carolina-beach", "oregon-inlet", "nye-beach", "agate-beach-newport",
                 "indicators", "rincon-domes", "maria-s", "steps-beach-rincon", "tres-palmas",
                 "table-tops-aguadilla", "gas-chambers-aguadilla", "crash-boat-beach"):
        assert m[slug] == date(2026, 11, 24), slug
    assert m["pacifica-linda-mar"] == date(2026, 11, 12)


def _moved_rows():
    # Spot a moved 24 Nov. Day 1 spot - NWPS at buoy: 23 Nov 10 pairs at -0.5 (before); 24 and 25
    # Nov 10 each at +9 (mixed, left out); 26 Nov 10 at -0.1 and 27 Nov 10 at -0.3 (after:
    # (-1 - 3) / 20 = -0.20). Spot a against its buoy: 27 Nov 5 at -0.4 (after). A day-2 row and a
    # region row are not the spot's day 1.
    k = dict(kind="spot_model", source="nwps", point="a")
    return [_row("2026-11-23", "Oregon", 1, 10, -0.5, **k), _row("2026-11-24", "Oregon", 1, 10, 9.0, **k),
            _row("2026-11-25", "Oregon", 1, 10, 9.0, **k), _row("2026-11-26", "Oregon", 1, 10, -0.1, **k),
            _row("2026-11-27", "Oregon", 1, 10, -0.3, **k),
            _row("2026-11-27", "Oregon", 1, 5, -0.4, kind="spot", source="spot", point="a"),
            _row("2026-11-27", "Oregon", 2, 10, 5.0, **k),
            _row("2026-11-27", "Oregon", 1, 10, 5.0, kind="spot_model", source="nwps")]


def test_moved_table_before_and_after():
    moves = {"a": date(2026, 11, 24), "z": date(2026, 11, 20),        # z: moved, no rows this week
             "old": date(2026, 10, 25),                               # 29 days before the week
             "late": date(2026, 11, 30)}                              # after the week
    t = R.moved_table(_moved_rows(), moves, date(2026, 11, 23), date(2026, 11, 30))
    assert t.splitlines()[0] == ("| Spot | Region | Point moved | Spot − NWPS at buoy, before (n) | After (n) | "
                                 "Spot − buoy, before (n) | After (n) |")
    assert t.splitlines()[2:] == ["| z | – | 2026-11-20 |  |  |  |  |",
                                  "| a | Oregon | 2026-11-24 | -0.50 (10) | -0.20 (20) |  | -0.40 (5) |"]
    # the 28-day edge: a move on 26 Oct (28 days before 23 Nov) is out, 27 Oct in
    edge = R.moved_table([], {"in": date(2026, 10, 27), "out": date(2026, 10, 26)},
                         date(2026, 11, 23), date(2026, 11, 30))
    assert [l.split(" | ")[0] for l in edge.splitlines()[2:]] == ["| in"]
    assert R.moved_table([], {}, date(2026, 11, 23), date(2026, 11, 30)).startswith("None in the 28 days")


def test_the_gap_table_marks_a_moved_spot():
    lines = R.gap_table(R.spot_gaps(_gap_rows(), GAP_ROSTER, k=3), {"a": date(2026, 11, 24)}).splitlines()
    assert lines[4].startswith("| a (moved 2026-11-24) | Oregon |") and lines[2].startswith("| b | Oregon |")


def test_build_shows_the_moved_spots():
    summary, full = R.build(_moved_rows(), Totals(), date(2026, 11, 23), date(2026, 11, 30), {},
                            {"a": date(2026, 11, 24)})
    assert "### Spots whose NWPS sample point moved: day-1 wave height bias, m, before and after the move" in summary
    assert "| a | Oregon | 2026-11-24 | -0.50 (10) | -0.20 (20) |  | -0.40 (5) |" in summary
    assert "the two days around the move mix the old point and the new" in full


def test_main_reads_the_committed_move_dates(tmp_path, monkeypatch):
    # The week's rows name Brookings Beach, whose committed override moved it on 24 Nov: the
    # report main writes must show it in the moved table with that date (day 1 23 Nov before,
    # 26-27 Nov after: the same figures as _moved_rows).
    rows = [dict(r, point="brookings-beach") if r["point"] == "a" else r for r in _moved_rows()]
    monkeypatch.setattr("pipeline.db_import.get_client", lambda: object())
    monkeypatch.setattr(R, "fetch_all", lambda client, table, *a, **k: rows if table == "accuracy_daily" else [])
    monkeypatch.delenv("GITHUB_STEP_SUMMARY", raising=False)
    assert R.main(["--end", "2026-11-30", "--out", str(tmp_path)]) == 0
    report = (tmp_path / "report.md").read_text()
    assert "| brookings-beach | Oregon | 2026-11-24 | -0.50 (10) | -0.20 (20) |  | -0.40 (5) |" in report
