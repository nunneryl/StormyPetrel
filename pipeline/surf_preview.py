"""Surf preview: THE MERGE GATE for the surf height at uncalibrated spots (forecast/surf_height.py).

READ-ONLY. It SELECTs from the spots, forecasts and surf_reports tables, reads NWS Surf Zone
Forecasts from the Iowa Environmental Mesonet and zone outlines from api.weather.gov, and writes
nothing to Supabase. The only files it writes are its own report (--out, --csv, --compare-out,
--srf-csv).

    python -m pipeline.surf_preview [--roster ROSTER.json] [--since ISO] [--archive-since ISO]
        [--out OUT.json] [--csv HOURS.csv] [--compare-out COMPARE.json] [--srf-csv SRF.csv]

1. THE REPLAY. Every published hour from --since (default: now) is run through
   apply_surf_height, the code interpret.main will run, with this checkout's factor file. The
   gate needs, over every replayed hour:
     stars        0 differences;
     other fields 0 differences outside face_ft and face_correction_version;
     where        face_ft and the mark change exactly on the NWPS-override hours of spots with no
                  factor, and nowhere else (an hour with no face or period keeps both);
     badges       no hour becomes 'calibrated', and every 'calibrated' hour stays so: the badge
                  read off the published row (daily_report.height_basis, the site's rule) against
                  the badge read off the replayed one.
   Beside the gate: the height before and after by region and by spot; the badge counts and
   transitions; and the homepage's "Best conditions right now" (each spot's soonest hour from
   --since, stars highest, ten shown) with each spot's height before and after.

2. AGAINST THE NWS (face_compare.run over the archive since --archive-since, default
   2026-09-07). Two tables by region, mainland headline hours only, as runs 52 and 53 read them:
     (a) THE REPRODUCTION, part of the gate: SURF_K x typical on uncalibrated spots' hours up to
         run 53's end (RUN53_UNTIL) must give run 53's figures (RUN53_K070, copied from
         docs/face_validation/run-53's CSV) to within REPRO_SHARE_TOL in every share and
         REPRO_N_TOL in every count.
     (b) THE DISPLAY: inside / below / above for the height the site will show (face_compare's
         'display': the surf height on uncalibrated NWPS-override hours, published elsewhere)
         against today's published height, over the whole archive, all spots and uncalibrated
         spots apart. These are the numbers for the methodology post.
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median

from .forecast.surf_height import NWPS_OVERRIDE_SOURCES, SURF_K, apply_surf_height, is_surf_row

FORECAST_COLS = ("spot_id, valid_time, hs, tp, swell_tp, swell_source, face_ft, face_ft_raw, "
                 "face_correction_version, stars, effective_size_ft, dir_gain, wind_mult, "
                 "tide_mult, chop_mult, period_quality")
ALLOWED = {"face_ft", "face_correction_version"}

# Run 53 (sw1-raycast, 3f8f7d5, window 2026-09-07 to 2026-10-27 16:00Z): SURF_K x typical on the
# mainland headline hours of spots with no factor, by region: (n, inside, below, above). From
# docs/face_validation/run-53/face_compare_srf.csv.gz on claude/practical-dirac-bshfbr-face,
# typical as the CSV carries it (2 dp) times 0.70, classified as face_compare.classify does.
RUN53_UNTIL = datetime(2026, 10, 27, 16, tzinfo=timezone.utc)
RUN53_K070 = {
    "all": (171534, 0.604, 0.34, 0.056),
    "Carolinas and Georgia": (25380, 0.814, 0.071, 0.115),
    "Florida Atlantic": (29223, 0.776, 0.103, 0.122),
    "Gulf": (10560, 0.702, 0.259, 0.04),
    "Mid-Atlantic": (25090, 0.789, 0.122, 0.088),
    "New England": (34808, 0.337, 0.656, 0.007),
    "Northern California": (5808, 0.411, 0.589, 0.0),
    "Pacific Northwest": (11451, 0.17, 0.829, 0.0),
    "Puerto Rico": (12096, 0.094, 0.906, 0.0),
    "Southern California": (17118, 0.931, 0.059, 0.01),
}
REPRO_SHARE_TOL = 0.01
REPRO_N_TOL = 0.01


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# 1. The replay (pure)                                                         #
# --------------------------------------------------------------------------- #
def replay(rows_by_name, roster, factors, slug_for):
    """(before, after, stats): the published rows by spot name, the same rows after
    apply_surf_height, and its stats. *before* is never written."""
    before = {n: [dict(r) for r in rows] for n, rows in rows_by_name.items()}
    after = copy.deepcopy(before)
    stats = apply_surf_height(after, roster, factors, slug_for)
    return before, after, stats


def expected_surf(row, calibrated):
    """Should apply_surf_height write this published row? NWPS-override hour, spot with no
    factor, a face and a positive rated period."""
    tp = _num(row.get("swell_tp"))
    return (not calibrated and row.get("swell_source") in NWPS_OVERRIDE_SOURCES
            and row.get("face_ft") is not None and tp is not None and tp > 0)


def gate_rows(before, after, calibrated_names):
    """The replay's half of the gate, and the badge counts."""
    from .daily_report import height_basis
    g = Counter()
    transitions = Counter()
    examples = defaultdict(list)
    for name, rows in before.items():
        cal = name in calibrated_names
        for b, a in zip(rows, after[name]):
            g["hours"] += 1
            moved = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
            if a.get("stars") != b.get("stars"):
                g["star_differences"] += 1
                examples["star"].append((name, b.get("valid_time")))
            if moved - ALLOWED:
                g["other_field_differences"] += 1
                examples["other"].append((name, b.get("valid_time"), sorted(moved - ALLOWED)))
            want = expected_surf(b, cal)
            marked = is_surf_row(a.get("face_correction_version"))
            if want != marked or (not want and moved):
                g["unexpected"] += 1
                examples["unexpected"].append((name, b.get("valid_time"), sorted(moved)))
            if want:
                g["rewritten"] += 1
                if b.get("face_ft_raw") is not None and b.get("face_ft") != b.get("face_ft_raw"):
                    g["uncalibrated_but_divided"] += 1   # something divided a face we thought raw
            bb, ab = height_basis(b), height_basis(a)
            transitions[(bb, ab)] += 1
            if ab == "calibrated" and bb != "calibrated":
                g["became_calibrated"] += 1
            if bb == "calibrated" and ab != "calibrated":
                g["lost_calibrated"] += 1
    badges = {"before": Counter(), "after": Counter()}
    for (bb, ab), n in transitions.items():
        badges["before"][str(bb)] += n
        badges["after"][str(ab)] += n
    for k in ("star_differences", "other_field_differences", "unexpected", "rewritten",
              "uncalibrated_but_divided", "became_calibrated", "lost_calibrated"):
        g.setdefault(k, 0)
    return {"counts": dict(g),
            "badges": {k: dict(v) for k, v in badges.items()},
            "transitions": {f"{b} -> {a}": n for (b, a), n in sorted(transitions.items(), key=str)},
            "examples": {k: v[:10] for k, v in examples.items()}}


def _summ(pairs):
    if not pairs:
        return {"n": 0}
    b = [p[0] for p in pairs]
    a = [p[1] for p in pairs]
    ratios = [y / x for x, y in pairs if x]
    return {"n": len(pairs), "median_before": round(median(b), 2), "median_after": round(median(a), 2),
            "mean_before": round(sum(b) / len(b), 2), "mean_after": round(sum(a) / len(a), 2),
            "median_ratio": round(median(ratios), 3) if ratios else None,
            "lower": round(sum(y < x for x, y in pairs) / len(pairs), 3),
            "higher": round(sum(y > x for x, y in pairs) / len(pairs), 3)}


def heights(before, after, region_by_name):
    """Height before and after on the rewritten hours, by region and by spot."""
    by_region, by_spot = defaultdict(list), defaultdict(list)
    for name, rows in before.items():
        for b, a in zip(rows, after[name]):
            if not is_surf_row(a.get("face_correction_version")):
                continue
            pair = (float(b["face_ft"]), float(a["face_ft"]))
            by_region[region_by_name.get(name, "other")].append(pair)
            by_region["all"].append(pair)
            by_spot[name].append(pair)
    return {"by_region": {k: _summ(v) for k, v in sorted(by_region.items(), key=lambda kv: (kv[0] != "all", kv[0]))},
            "by_spot": {k: _summ(v) for k, v in sorted(by_spot.items())}}


def homepage(before, after, names_order, n=10):
    """"Best conditions right now" before and after: each spot's soonest hour with stars, stars
    highest first, equal stars in spot id order (frontend/app/page.tsx), with its height and
    badge on each side. *names_order* maps a spot name to its id."""
    from .daily_report import height_basis
    rows = []
    for name, bs in before.items():
        i = next((j for j, r in enumerate(bs) if r.get("stars") is not None), None)
        if i is None:
            continue
        b, a = bs[i], after[name][i]
        rows.append((-float(b["stars"]), names_order.get(name, 0), name, b, a))
    rows.sort(key=lambda r: (r[0], r[1]))
    out = []
    for _s, _i, name, b, a in rows[:n]:
        out.append({"spot": name, "valid_time": b.get("valid_time"), "stars_before": b.get("stars"),
                    "stars_after": a.get("stars"), "face_before": b.get("face_ft"),
                    "face_after": a.get("face_ft"), "basis_before": height_basis(b),
                    "basis_after": height_basis(a)})
    return out


# --------------------------------------------------------------------------- #
# 2. Against the NWS (pure)                                                    #
# --------------------------------------------------------------------------- #
def _sides(rows, face_of):
    from .face_compare import classify
    s = Counter()
    for r in rows:
        f = face_of(r)
        side = classify(f, r["lo"], r["hi"]) if f is not None else None
        if side:
            s[side] += 1
    n = sum(s.values())
    return (n, *(round(s[k] / n, 3) if n else None for k in ("inside", "below", "above")))


def mainland_headline(srf_rows):
    return [r for r in srf_rows if r["group"] == "headline" and r["office"] != "HFO"]


def reproduce(srf_rows, until=RUN53_UNTIL, want_by_region=None):
    """(a): SURF_K x typical on uncalibrated spots' mainland headline hours before *until*, by
    region, against RUN53_K070. Returns {region: {got, want, ok}} and an overall ok."""
    cut = int(until.timestamp())
    rows = [r for r in mainland_headline(srf_rows)
            if not r.get("calibrated") and r.get("typical") is not None and r["hour"] < cut]
    by = defaultdict(list)
    for r in rows:
        by[r["region"]].append(r)
    by["all"] = rows
    out, ok = {}, True
    for region, want in (want_by_region or RUN53_K070).items():
        got = _sides(by.get(region, []), lambda r: r["typical"] * SURF_K)
        good = (got[0] > 0 and abs(got[0] - want[0]) <= REPRO_N_TOL * want[0]
                and all(abs(g - w) <= REPRO_SHARE_TOL for g, w in zip(got[1:], want[1:])))
        out[region] = {"got": got, "want": want, "ok": good}
        ok = ok and good
    return {"by_region": out, "ok": ok}


def display_vs_published(srf_rows):
    """(b): inside / below / above for published and display by region, all spots and the
    uncalibrated spots apart."""
    def table(rows):
        by = defaultdict(list)
        for r in rows:
            by[r["region"]].append(r)
        by["all"] = rows
        return {g: {"published": _sides(rs, lambda r: r.get("published")),
                    "display": _sides(rs, lambda r: r.get("display"))}
                for g, rs in sorted(by.items(), key=lambda kv: (kv[0] != "all", kv[0]))}
    head = mainland_headline(srf_rows)
    hawaii = [r for r in srf_rows if r["group"] == "headline" and r["office"] == "HFO"]
    return {"all_spots": table(head), "uncalibrated": table([r for r in head if not r.get("calibrated")]),
            "hawaii": table(hawaii)}


# --------------------------------------------------------------------------- #
# I/O                                                                          #
# --------------------------------------------------------------------------- #
def fetch_spots(client):
    out, frm = [], 0
    while True:
        rows = (client.table("spots").select("id, name, state").order("id")
                .range(frm, frm + 999).execute().data or [])
        out.extend(rows)
        if len(rows) < 1000:
            return out
        frm += 1000


def fetch_rows(client, sid, since):
    out, frm = [], 0
    while True:
        rows = (client.table("forecasts").select(FORECAST_COLS)
                .eq("spot_id", sid).eq("source", "nwps").gte("valid_time", since)
                .order("valid_time").range(frm, frm + 999).execute().data or [])
        out.extend(rows)
        if len(rows) < 1000:
            return out
        frm += 1000


def run(client, roster, since, archive_since, until, *, factors=None, compare=None):
    from .enrich import _slug_for
    from .face_compare import region_of
    from .face_compare import run as compare_run
    from .forecast.face_correction import load_face_factors, spot_factor
    factors = load_face_factors() if factors is None else factors
    compare = compare or compare_run
    by_name = {s["name"]: s for s in roster}
    spots = fetch_spots(client)
    rows_by_name, ids = {}, {}
    for s in spots:
        if s["name"] not in by_name:
            continue
        rows = fetch_rows(client, s["id"], since)
        if rows:
            rows_by_name[s["name"]] = rows
            ids[s["name"]] = s["id"]
    before, after, stats = replay(rows_by_name, roster, factors, _slug_for)
    calibrated = {n for n in rows_by_name if spot_factor(by_name[n], factors, _slug_for) is not None}
    g = gate_rows(before, after, calibrated)
    regions = {n: region_of(by_name[n]) for n in rows_by_name}

    comp = compare(client, archive_since, until, roster, factors=factors)
    srf_rows = comp["_pairs"]["srf"]
    repro = reproduce(srf_rows)
    c = g["counts"]
    passed = (c["hours"] > 0 and c["rewritten"] > 0 and c["star_differences"] == 0
              and c["other_field_differences"] == 0 and c["unexpected"] == 0
              and c["became_calibrated"] == 0 and c["lost_calibrated"] == 0 and repro["ok"])
    return {
        "window": {"replay_since": since, "archive": [archive_since.isoformat(), until.isoformat()]},
        "surf_k": SURF_K,
        "gate": {"pass": passed, **g["counts"], "reproduction_ok": repro["ok"]},
        "apply_stats": stats,
        "badges": g["badges"], "transitions": g["transitions"], "examples": g["examples"],
        "heights": heights(before, after, regions),
        "homepage": homepage(before, after, ids),
        "reproduction": repro,
        "nws": display_vs_published(srf_rows),
        "compare_errors": comp.get("errors"),
        "_compare": comp,
        "_hours": [(n, b, a) for n, rows in before.items() for b, a in zip(rows, after[n])
                   if is_surf_row(a.get("face_correction_version"))],
    }


def _pct(v):
    return "-" if v is None else f"{100 * v:.1f}%"


def _sides_str(t):
    n, i, b, a = t
    return f"n {n:6}  in {_pct(i):>6}  below {_pct(b):>6}  above {_pct(a):>6}"


def print_report(rep):
    g = rep["gate"]
    print(f"\nSurf preview (K = {rep['surf_k']}), replay from {rep['window']['replay_since']}, "
          f"archive {rep['window']['archive'][0]} to {rep['window']['archive'][1]}")
    print(f"\nMERGE GATE {'PASS' if g['pass'] else 'FAIL'}")
    for k in ("hours", "rewritten", "star_differences", "other_field_differences", "unexpected",
              "became_calibrated", "lost_calibrated", "uncalibrated_but_divided", "reproduction_ok"):
        print(f"  {k:28} {g[k]}")
    print(f"  apply_surf_height stats: {rep['apply_stats']}")
    for k, v in rep["examples"].items():
        print(f"  examples {k}: {v}")
    print("\n1. Badges (published row -> replayed row)")
    print(f"  before {rep['badges']['before']}")
    print(f"  after  {rep['badges']['after']}")
    for k, n in rep["transitions"].items():
        print(f"    {k:28} {n}")
    print("\n2. Height on the rewritten hours, before -> after (ft)")
    for r, s in rep["heights"]["by_region"].items():
        if s["n"]:
            print(f"  {r:24} n {s['n']:6}  median {s['median_before']:5} -> {s['median_after']:5}  "
                  f"mean {s['mean_before']:5} -> {s['mean_after']:5}  x{s['median_ratio']}  "
                  f"lower {_pct(s['lower'])} higher {_pct(s['higher'])}")
    print("  by spot:")
    for r, s in rep["heights"]["by_spot"].items():
        print(f"    {r:34} n {s['n']:4}  median {s['median_before']:5} -> {s['median_after']:5}  "
              f"x{s['median_ratio']}")
    print("\n3. Homepage, best conditions right now")
    for i, h in enumerate(rep["homepage"], 1):
        print(f"  {i:2}. {h['spot']:30} {h['stars_before']} -> {h['stars_after']} stars  "
              f"{h['face_before']} ft ({h['basis_before']}) -> {h['face_after']} ft ({h['basis_after']})")
    print("\n4a. Reproduction of run 53, K x typical on uncalibrated spots (got | run 53)")
    for r, e in rep["reproduction"]["by_region"].items():
        print(f"  {r:24} {_sides_str(e['got'])} | {_sides_str(e['want'])}  {'ok' if e['ok'] else 'DIFFERS'}")
    for part in ("all_spots", "uncalibrated", "hawaii"):
        print(f"\n4b. Against the NWS range, {part}: published | display")
        for r, e in rep["nws"][part].items():
            print(f"  {r:24} {_sides_str(e['published'])} | {_sides_str(e['display'])}")
    if rep.get("compare_errors"):
        print(f"\n  comparison errors: {rep['compare_errors']}")


def write_csv(path, hours):
    cols = ("spot", "valid_time", "swell_source", "swell_tp", "face_ft_raw", "face_before",
            "face_after", "stars")
    with open(path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(cols)
        for name, b, a in hours:
            w.writerow((name, b.get("valid_time"), b.get("swell_source"), b.get("swell_tp"),
                        b.get("face_ft_raw"), b.get("face_ft"), a.get("face_ft"), b.get("stars")))


def main(argv=None):
    from .face_compare import SINCE, print_report as compare_print, write_csvs
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--roster", default="pipeline/spots_enriched.json")
    p.add_argument("--since", default=None, help="ISO start of the replayed window (default: now)")
    p.add_argument("--archive-since", default=SINCE.isoformat())
    p.add_argument("--out", default=None)
    p.add_argument("--csv", default=None, help="every rewritten hour, before and after")
    p.add_argument("--compare-out", default=None, help="face_compare's JSON for the archive")
    p.add_argument("--srf-prefix", default=None, help="face_compare's CSVs, with this prefix")
    a = p.parse_args(argv)
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    now = datetime.now(timezone.utc)
    since = a.since or now.replace(microsecond=0).isoformat()
    until = now.replace(minute=0, second=0, microsecond=0)
    with open(a.roster) as fh:
        roster = json.load(fh)
    rep = run(client, roster, since, datetime.fromisoformat(a.archive_since), until)
    comp = rep.pop("_compare")
    hours = rep.pop("_hours")
    compare_print(comp)
    print_report(rep)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(rep, fh, indent=1, default=str)
    if a.csv:
        write_csv(a.csv, hours)
    if a.compare_out:
        with open(a.compare_out, "w") as fh:
            json.dump({k: v for k, v in comp.items() if k != "_pairs"}, fh, indent=1)
    if a.srf_prefix:
        write_csvs(comp, a.srf_prefix)
    return 0 if rep["gate"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
