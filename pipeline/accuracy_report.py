"""The weekly accuracy report: tables in the run summary, the full report and CSVs as an artifact.

    python -m pipeline.accuracy_report [--days 7] [--end YYYY-MM-DD] [--out accuracy-report]

Weekly (.github/workflows/accuracy-report.yml). Reads accuracy_daily (migrations 031 and 032,
written nightly by pipeline/accuracy_score.py) for the --days UTC days before --end, the spot
roster (pipeline/spots_enriched.json) for each spot's buoy, and partition_archive
(scripts/archive_partitions.py) for the same days. Writes, under --out:
  report.md               every table below, in full;
  accuracy.csv            the week's figures per kind, source, region, lead day and measure;
  buoys.csv               the week's height figures per buoy, lead day and source;
  spots.csv               the week's height figures per spot and lead day: against its buoy, and
                          against NWPS at that buoy;
  partition_lead_skill.csv  NWPS at the partition-archive buoys, per zone, lead and measure;
and appends the summary tables to $GITHUB_STEP_SUMMARY (printed when it is unset).

THE TABLES. Coverage by region; wave height by region and lead day for each source (WAVEWATCH III
and NWPS at the buoys, spots against their buoy within 25 km); period and direction by source and
lead day; the 10 best and worst buoys by day-2 height error; the GAP_K spots whose day-1 height
sits furthest from NWPS at their own buoy (spot minus NWPS at the buoy, beside spot minus the buoy
and NWPS at the buoy minus the buoy, at least GAP_MIN_N pairs), a spot whose sample point moved
marked with the date; every spot whose sample point moved in the last MOVED_RECENT_DAYS days, with
its day-1 gap before and after the move (see move_split); and NWPS's lead skill at the
partition-archive buoys, which the archive has kept since August, so it fills from the first run.
Figures: bias (forecast minus observed), MAE, RMSE, scatter index (RMSE over the mean observed),
correlation, and n. A cell with fewer than MIN_N pairs shows only its n; a buoy needs MIN_N_BUOY.

DIRECTION. The models' direction is the peak direction; the buoys' (MWD) is the mean direction,
so a steady offset is expected in mixed seas. Only pairs where the buoy reads at least 0.5 m.
"""
from __future__ import annotations

import argparse
import csv
import logging
import math
import os
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .accuracy_score import DIR_MIN_HS, SUMS, Totals, error, fetch_all

log = logging.getLogger("pipeline.accuracy_report")

MIN_N = 50
MIN_N_BUOY = 20
RANK_LEAD_DAY = 2
RANK_K = 10
GAP_LEAD_DAY = 1
GAP_K = 20
GAP_MIN_N = 20      # about 5 hours a day for 7 days is 35 pairs a spot a lead day
MOVED_RECENT_DAYS = 28
SOURCES = (("buoy", "ww3", "WAVEWATCH III at the buoys"), ("buoy", "nwps", "NWPS at the buoys"),
           ("spot", "spot", "Spots against their buoy (within 25 km)"))
MEASURE_NAMES = {"hs": "Wave height (m)", "swell_hs": "Swell height (m)", "tp": "Peak period (s)",
                 "dp": "Direction (degrees)"}
LEAD_NAMES = {0: "Day 0", 1: "Day 1", 2: "Day 2", 3: "Day 3"}
PA_LEAD_NAMES = {0: "0-23 h", 1: "24-47 h", 2: "48-72 h"}


# --------------------------------------------------------------------------- #
# Figures                                                                      #
# --------------------------------------------------------------------------- #

def combine(rows: list[dict], key) -> dict:
    """{key(row): summed n, points (largest daily count) and sums}."""
    out: dict = {}
    for r in rows:
        k = key(r)
        s = out.setdefault(k, {"n": 0, "points": 0, **{c: 0.0 for c in SUMS}})
        s["n"] += int(r["n"])
        s["points"] = max(s["points"], int(r.get("points") or 0))
        for c in SUMS:
            s[c] += float(r[c])
    return out


def stats(s: dict) -> dict:
    """bias, MAE, RMSE, scatter index and correlation from summed n and sums (None when undefined)."""
    n = s["n"]
    if not n:
        return {"n": 0, "bias": None, "mae": None, "rmse": None, "si": None, "r": None}
    rmse = math.sqrt(s["sum_ee"] / n)
    mean_o = s["sum_o"] / n
    var_f = n * s["sum_ff"] - s["sum_f"] ** 2
    var_o = n * s["sum_oo"] - s["sum_o"] ** 2
    r = None
    if var_f > 1e-12 and var_o > 1e-12:
        r = (n * s["sum_fo"] - s["sum_f"] * s["sum_o"]) / math.sqrt(var_f * var_o)
    return {"n": n, "bias": s["sum_e"] / n, "mae": s["sum_abs_e"] / n, "rmse": rmse,
            "si": rmse / mean_o if mean_o > 1e-12 else None, "r": r}


def _f(x, nd=2) -> str:
    return "–" if x is None else f"{x:.{nd}f}"


def cell(st: dict, min_n: int = MIN_N, nd: int = 2) -> str:
    """'bias / MAE (n)', or only the n below *min_n*."""
    if st["n"] < min_n:
        return f"n={st['n']}" if st["n"] else ""
    return f"{_f(st['bias'], nd)} / {_f(st['mae'], nd)} ({st['n']})"


def md_table(header: list[str], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(r) + " |" for r in rows]
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# The tables                                                                   #
# --------------------------------------------------------------------------- #

def region_rows(rows):
    return [r for r in rows if r["point"] == ""]


def coverage_table(rows: list[dict]) -> str:
    """Per region and source: points (the largest daily count) and wave-height pairs, all leads.
    The three SOURCES only (kind 'spot_model' is NWPS too, but not at the buoys)."""
    agg = combine([r for r in region_rows(rows) if r["measure"] == "hs"
                   and (r["kind"], r["source"]) in {(k, s) for k, s, _ in SOURCES}],
                  lambda r: (r["region"], r["kind"], r["source"]))
    regions = sorted({k[0] for k in agg})
    body = []
    for reg in regions:
        line = [reg]
        for kind, src, _ in SOURCES:
            s = agg.get((reg, kind, src))
            line.append(f"{s['points']} / {s['n']}" if s else "")
        body.append(line)
    return md_table(["Region", "WW3 buoys / pairs", "NWPS buoys / pairs", "Spots / pairs"], body)


def region_lead_table(rows: list[dict], kind: str, source: str, measure: str = "hs") -> str:
    agg = combine([r for r in region_rows(rows) if (r["kind"], r["source"], r["measure"]) ==
                   (kind, source, measure)], lambda r: (r["region"], int(r["lead_day"])))
    leads = sorted({k[1] for k in agg})
    regions = sorted({k[0] for k in agg})
    allr = combine([r for r in region_rows(rows) if (r["kind"], r["source"], r["measure"]) ==
                    (kind, source, measure)], lambda r: int(r["lead_day"]))
    body = [[reg] + [cell(stats(agg[(reg, ld)])) if (reg, ld) in agg else "" for ld in leads]
            for reg in regions]
    body.append(["**All**"] + [cell(stats(allr[ld])) for ld in leads])
    return md_table(["Region"] + [LEAD_NAMES[ld] + " bias / MAE (n)" for ld in leads], body)


def measure_lead_table(rows: list[dict], measure: str) -> str:
    agg = combine([r for r in region_rows(rows) if r["measure"] == measure],
                  lambda r: (r["kind"], r["source"], int(r["lead_day"])))
    nd = {"dp": 0, "tp": 1}.get(measure, 2)
    body = []
    for kind, src, label in SOURCES:
        for ld in sorted({k[2] for k in agg if k[:2] == (kind, src)}):
            st = stats(agg[(kind, src, ld)])
            shown = st["n"] >= MIN_N
            body.append([label, LEAD_NAMES[ld],
                         _f(st["bias"], nd) if shown else "", _f(st["mae"], nd) if shown else "",
                         _f(st["rmse"], nd) if shown else "", str(st["n"])])
    return md_table(["Source", "Lead", "Bias", "MAE", "RMSE", "n"], body)


def buoy_ranking(rows: list[dict], source: str, lead_day: int = RANK_LEAD_DAY,
                 k: int = RANK_K) -> tuple[list[tuple], list[tuple]]:
    """(best, worst): up to k (buoy, region, stats) by height MAE at *lead_day*, n >= MIN_N_BUOY;
    ties by buoy id."""
    agg = combine([r for r in rows if r["kind"] == "buoy" and r["point"] and r["source"] == source
                   and int(r["lead_day"]) == lead_day and r["measure"] == "hs"],
                  lambda r: (r["point"], r["region"]))
    ranked = sorted(((p, reg, stats(s)) for (p, reg), s in agg.items() if s["n"] >= MIN_N_BUOY),
                    key=lambda x: (x[2]["mae"], x[0]))
    worst = sorted(ranked, key=lambda x: (-x[2]["mae"], x[0]))[:k]
    return ranked[:k], worst


def ranking_table(entries: list[tuple]) -> str:
    return md_table(["Buoy", "Region", "Bias", "MAE", "RMSE", "n"],
                    [[p, reg, _f(st["bias"]), _f(st["mae"]), _f(st["rmse"]), str(st["n"])]
                     for p, reg, st in entries])


def spot_roster(spots: list[dict]) -> dict[str, dict]:
    """{slug: {buoy_id, km, node_m}} from the enriched roster: the spot's nearest buoy, its
    distance, and how far the spot's NWPS sample point is from the spot (m)."""
    from .db_import import _slugify
    out = {}
    for s in spots:
        if not s.get("name"):
            continue
        out[_slugify(s["name"])] = {"buoy_id": str(s["nearest_buoy_id"]) if s.get("nearest_buoy_id") else None,
                                    "km": s.get("nearest_buoy_dist_km"),
                                    "node_m": s.get("nwps_node_distance_m")}
    return out


def spot_gaps(rows: list[dict], roster: dict, lead_day: int = GAP_LEAD_DAY, k: int = GAP_K,
              min_n: int = GAP_MIN_N) -> list[dict]:
    """Up to k spots with the largest |spot minus NWPS at its buoy| height bias at *lead_day*
    (kind 'spot_model', at least *min_n* pairs), largest first, ties by slug. Each: slug, region,
    gap (stats), vs_buoy (the spot against its buoy, kind 'spot'), nwps_vs_buoy (NWPS at that buoy
    against the buoy, kind 'buoy', from the roster's buoy), and the roster entry. Each figure has
    its own pairs, so the gap and the two others need not add up exactly."""
    def pick(kind, source):
        return combine([r for r in rows if (r["kind"], r["source"], r["measure"]) == (kind, source, "hs")
                        and r["point"] and int(r["lead_day"]) == lead_day], lambda r: r["point"])
    model, spot, buoy = pick("spot_model", "nwps"), pick("spot", "spot"), pick("buoy", "nwps")
    region = {r["point"]: r["region"] for r in rows if r["kind"] == "spot_model" and r["point"]}
    out = []
    for slug, s in model.items():
        st = stats(s)
        if st["n"] < min_n:
            continue
        info = roster.get(slug) or {}
        bid = info.get("buoy_id")
        out.append({"slug": slug, "region": region[slug], "gap": st,
                    "vs_buoy": stats(spot[slug]) if slug in spot else stats({"n": 0}),
                    "nwps_vs_buoy": stats(buoy[bid]) if bid in buoy else stats({"n": 0}),
                    **info})
    out.sort(key=lambda g: (-abs(g["gap"]["bias"]), g["slug"]))
    return out[:k]


def _bias_n(st: dict) -> str:
    return f"{_f(st['bias'])} ({st['n']})" if st["n"] else ""


def load_moves(path=None) -> dict[str, date]:
    """{spot slug: moved_on} from nwps_node_overrides.json: the first UTC day the pipeline rated
    the spot from its moved sample point. Overrides with no readable date are left out."""
    import json
    path = Path(path) if path is not None else \
        Path(__file__).resolve().parent / "data" / "nwps_node_overrides.json"
    if not path.exists():
        return {}
    out = {}
    for slug, o in (json.loads(path.read_text(encoding="utf-8")).get("overrides") or {}).items():
        try:
            out[slug] = date.fromisoformat(str(o.get("moved_on")))
        except ValueError:
            continue
    return out


def move_split(day: date, moved_on: date, lead_day: int):
    """'before', 'after' or None for a spot's row valid on *day* at *lead_day*, its point moved on
    *moved_on*. A lead-L forecast valid on day D comes from a cycle 24L to 24L + 24 hours earlier,
    so D up to moved_on - 2 + L comes only from cycles dated before moved_on - 1 (the old point:
    the pipeline reads each office's newest cycle), and D from moved_on + L + 1 only from cycles
    dated moved_on or later (the new point). The two days between can be either: None."""
    d = (day - moved_on).days - lead_day
    if d <= -2:
        return "before"
    if d >= 1:
        return "after"
    return None


def _day(v) -> date:
    return date.fromisoformat(str(v)[:10])


def moved_table(rows: list[dict], moves: dict, start: date, end: date,
                lead_day: int = GAP_LEAD_DAY) -> str:
    """Every spot whose point moved in the MOVED_RECENT_DAYS days before *start* or during the
    window, oldest move first: its *lead_day* height bias against NWPS at its buoy and against its
    buoy, before and after the move (move_split), whatever the pairs."""
    recent = {s: m for s, m in moves.items() if start - timedelta(days=MOVED_RECENT_DAYS) < m < end}
    if not recent:
        return f"None in the {MOVED_RECENT_DAYS} days before this week or during it."
    body = []
    for slug in sorted(recent, key=lambda x: (recent[x], x)):
        m = recent[slug]
        mine = [r for r in rows if r["point"] == slug and r["measure"] == "hs"
                and int(r["lead_day"]) == lead_day]

        def part(kind, source, which):
            agg = combine([r for r in mine if (r["kind"], r["source"]) == (kind, source)
                           and move_split(_day(r["day"]), m, lead_day) == which], lambda r: 0)
            return stats(agg.get(0, {"n": 0}))
        region = next((r["region"] for r in mine), "–")
        body.append([slug, region, m.isoformat(),
                     _bias_n(part("spot_model", "nwps", "before")), _bias_n(part("spot_model", "nwps", "after")),
                     _bias_n(part("spot", "spot", "before")), _bias_n(part("spot", "spot", "after"))])
    return md_table(["Spot", "Region", "Point moved", "Spot − NWPS at buoy, before (n)", "After (n)",
                     "Spot − buoy, before (n)", "After (n)"], body)


def gap_table(gaps: list[dict], moves: dict | None = None) -> str:
    moves = moves or {}
    body = []
    for g in gaps:
        buoy = g.get("buoy_id") or "–"
        if g.get("km") is not None:
            buoy += f" ({float(g['km']):.1f} km)"
        node = "–" if g.get("node_m") is None else f"{float(g['node_m']):.0f}"
        spot = g["slug"] + (f" (moved {moves[g['slug']].isoformat()})" if g["slug"] in moves else "")
        body.append([spot, g["region"], buoy, _bias_n(g["gap"]), _bias_n(g["vs_buoy"]),
                     _bias_n(g["nwps_vs_buoy"]), node])
    return md_table(["Spot", "Region", "Buoy", "Spot − NWPS at buoy (n)", "Spot − buoy (n)",
                     "NWPS at buoy − buoy (n)", "Sample point from spot (m)"], body)


# --------------------------------------------------------------------------- #
# partition_archive lead skill                                                 #
# --------------------------------------------------------------------------- #

PA_PAIRS = (("hs", "swh", ("spec_wvht", "hs_total")),
            ("swell_hs", "shts", ("spec_swh", "hs_swell")),
            ("dp", "dirpw", ("spec_mwd", "total_mean_dir")))


def pa_lead_bin(lead_hours: int) -> int:
    """0-23 h, 24-47 h, 48-72 h (the archive keeps leads up to 72 h)."""
    return min(int(lead_hours) // 24, 2)


def _first(row: dict, cols):
    for c in cols:
        if row.get(c) is not None:
            return float(row[c])
    return None


def partition_skill(model_rows: list[dict], buoy_rows: list[dict]) -> Totals:
    """NWPS model rows against the buoy row of the same hour and zone, per (zone, lead bin,
    measure); zone '' is every zone together."""
    obs = {(r["valid_hour"], r["wfo"], r["buoy_id"]): r for r in buoy_rows}
    t = Totals()
    for m in model_rows:
        b = obs.get((m["valid_hour"], m["wfo"], m["buoy_id"]))
        if b is None:
            continue
        zone = f"{m['wfo']}/{m['buoy_id']}"
        lb = pa_lead_bin(m["lead_hours"])
        for measure, mcol, bcols in PA_PAIRS:
            f, o = m.get(mcol), _first(b, bcols)
            if f is None or o is None:
                continue
            if measure == "dp":
                ohs = _first(b, ("spec_wvht", "hs_total"))
                if ohs is None or ohs < DIR_MIN_HS:
                    continue
            e = error(measure, float(f), o)
            for z in ("", zone):
                t.add(("pa", "nwps", z, "", lb, measure), zone, float(f), o, e)
    return t


def partition_table(t: Totals, zone: str = "") -> str:
    body = []
    for measure, _, _ in PA_PAIRS:
        nd = 0 if measure == "dp" else 2
        for lb in (0, 1, 2):
            s = t.sums.get(("pa", "nwps", zone, "", lb, measure))
            if not s:
                continue
            st = stats(s)
            shown = st["n"] >= MIN_N
            body.append([MEASURE_NAMES[measure], PA_LEAD_NAMES[lb],
                         _f(st["bias"], nd) if shown else "", _f(st["mae"], nd) if shown else "",
                         _f(st["rmse"], nd) if shown else "",
                         _f(st["r"]) if shown and measure != "dp" else "", str(st["n"])])
    return md_table(["Measure", "Lead", "Bias", "MAE", "RMSE", "r", "n"], body)


# --------------------------------------------------------------------------- #
# The report                                                                   #
# --------------------------------------------------------------------------- #

def build(rows: list[dict], pa: Totals, start: date, end: date,
          roster: dict | None = None, moves: dict | None = None) -> tuple[str, str]:
    """(summary markdown, full report markdown). *roster*: spot_roster(), for the gap table's
    buoys and sample points; *moves*: load_moves(), the spots whose sample point moved."""
    head = (f"## Accuracy scoreboard: {start.isoformat()} to {(end - timedelta(days=1)).isoformat()}"
            f" (UTC)\n\nCells: bias / MAE (n), forecast minus observed. Cells with fewer than "
            f"{MIN_N} pairs show only n.\n")
    parts = [head, "### Coverage (largest daily count / pairs, wave height)\n", coverage_table(rows)]
    for kind, src, label in SOURCES:
        parts += [f"\n### Wave height (m): {label}\n", region_lead_table(rows, kind, src)]
    for m in ("tp", "dp", "swell_hs"):
        parts += [f"\n### {MEASURE_NAMES[m]}, all regions\n", measure_lead_table(rows, m)]
    for src, label in (("ww3", "WAVEWATCH III"), ("nwps", "NWPS")):
        best, worst = buoy_ranking(rows, src)
        parts += [f"\n### {label}: best {RANK_K} buoys, day-{RANK_LEAD_DAY} wave height MAE "
                  f"(at least {MIN_N_BUOY} pairs)\n", ranking_table(best),
                  f"\n### {label}: worst {RANK_K} buoys\n", ranking_table(worst)]
    parts += [f"\n### Spots furthest from NWPS at their buoy: top {GAP_K}, day-{GAP_LEAD_DAY} wave "
              f"height bias, m (at least {GAP_MIN_N} pairs)\n",
              gap_table(spot_gaps(rows, roster or {}), moves)]
    parts += [f"\n### Spots whose NWPS sample point moved: day-{GAP_LEAD_DAY} wave height bias, m, "
              "before and after the move\n", moved_table(rows, moves or {}, start, end)]
    parts += ["\n### NWPS lead skill at the partition-archive buoys (all zones)\n",
              partition_table(pa)]
    summary = "\n".join(parts) + "\n"
    zones = sorted({k[2] for k in pa.sums if k[2]})
    full = [summary, "\n## NWPS lead skill per partition-archive zone\n"]
    for z in zones:
        full += [f"\n### {z}\n", partition_table(pa, z)]
    full += ["\n## Notes\n",
             "- Direction: the models give the peak direction, the buoys the mean (MWD); only "
             f"pairs where the buoy reads at least {DIR_MIN_HS} m.",
             "- WAVEWATCH III swell height is its three swell partitions combined.",
             "- Spots are nearshore and their buoys are not: read the spot rows for change over "
             "time and lead, not as the model's error.",
             "- Spots furthest from NWPS at their buoy: the spot forecast against NWPS at the "
             "spot's nearest buoy (same lead day, nearest 3-hourly time). Both come from NWPS, so "
             "the gap is what the spot's sample point and processing add. Each column has its own "
             "pairs, so they need not add up exactly.",
             "- Spots whose NWPS sample point moved: the date is the first UTC day the pipeline "
             f"used the new point (nwps_node_overrides.json moved_on). A day-{GAP_LEAD_DAY} forecast "
             "valid on a given day comes from a cycle one to two days earlier, so the two days "
             "around the move mix the old point and the new and are left out; 'before' and "
             "'after' are the days on either side within this week."]
    return summary, "\n".join(full) + "\n"


def write_csvs(out: Path, rows: list[dict], pa: Totals) -> None:
    agg = combine(region_rows(rows), lambda r: (r["kind"], r["source"], r["region"],
                                                 int(r["lead_day"]), r["measure"]))
    with open(out / "accuracy.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["kind", "source", "region", "lead_day", "measure", "n", "points", "bias",
                    "mae", "rmse", "scatter_index", "r"])
        for k, s in sorted(agg.items()):
            st = stats(s)
            w.writerow([*k, st["n"], s["points"], *(_f(st[c], 4) for c in ("bias", "mae", "rmse", "si", "r"))])
    agg = combine([r for r in rows if r["kind"] == "buoy" and r["point"]],
                  lambda r: (r["source"], r["point"], r["region"], int(r["lead_day"])))
    with open(out / "buoys.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["source", "buoy", "region", "lead_day", "n", "bias", "mae", "rmse"])
        for k, s in sorted(agg.items()):
            st = stats(s)
            w.writerow([*k, st["n"], *(_f(st[c], 4) for c in ("bias", "mae", "rmse"))])
    agg = combine([r for r in rows if r["kind"] in ("spot", "spot_model") and r["point"]],
                  lambda r: (r["point"], r["region"], int(r["lead_day"]), r["kind"]))
    with open(out / "spots.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["spot", "region", "lead_day", "against", "n", "bias", "mae", "rmse"])
        for (spot, region, ld, kind), s in sorted(agg.items()):
            st = stats(s)
            w.writerow([spot, region, ld, "buoy" if kind == "spot" else "nwps_at_buoy", st["n"],
                        *(_f(st[c], 4) for c in ("bias", "mae", "rmse"))])
    with open(out / "partition_lead_skill.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["zone", "lead_bin", "measure", "n", "bias", "mae", "rmse", "r"])
        for (_, _, zone, _, lb, m), s in sorted(pa.sums.items()):
            st = stats(s)
            w.writerow([zone or "all", PA_LEAD_NAMES[lb], m, st["n"],
                        *(_f(st[c], 4) for c in ("bias", "mae", "rmse", "r"))])


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=7)
    ap.add_argument("--end", type=date.fromisoformat, default=None)
    ap.add_argument("--out", default="accuracy-report")
    a = ap.parse_args(argv)
    from .db_import import get_client
    client = get_client()
    end = a.end or datetime.now(timezone.utc).date()
    start = end - timedelta(days=a.days)
    rows = fetch_all(client, "accuracy_daily",
                     "day,kind,source,region,point,lead_day,measure,n,points," + ",".join(SUMS),
                     "day", start, end, ("day", "kind", "source", "region", "point", "lead_day", "measure"))
    t0 = datetime(start.year, start.month, start.day, tzinfo=timezone.utc)
    t1 = datetime(end.year, end.month, end.day, tzinfo=timezone.utc)
    model = fetch_all(client, "partition_archive", "valid_hour,wfo,buoy_id,lead_hours,swh,shts,dirpw",
                      "valid_hour", t0, t1, ("valid_hour", "wfo", "buoy_id", "lead_hours"),
                      eq={"source": "model"})
    buoy = fetch_all(client, "partition_archive",
                     "valid_hour,wfo,buoy_id,spec_wvht,hs_total,spec_swh,hs_swell,spec_mwd,total_mean_dir",
                     "valid_hour", t0, t1, ("valid_hour", "wfo", "buoy_id"), eq={"source": "buoy"})
    pa = partition_skill(model, buoy)
    import json
    from .config import DEFAULT_ENRICHED_OUTPUT
    roster = spot_roster(json.loads(Path(DEFAULT_ENRICHED_OUTPUT).read_text(encoding="utf-8")))
    summary, full = build(rows, pa, start, end, roster, load_moves())
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.md").write_text(full, encoding="utf-8")
    write_csvs(out, rows, pa)
    target = os.environ.get("GITHUB_STEP_SUMMARY")
    if target:
        with open(target, "a", encoding="utf-8") as f:
            f.write(summary)
    else:
        print(summary)
    log.info("accuracy report: %d daily rows, %d partition model rows, %d buoy rows -> %s",
             len(rows), len(model), len(buoy), out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
