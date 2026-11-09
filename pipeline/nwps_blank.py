"""Blank NWPS hours: how often each spot's sample cell has no wave height, period or direction,
and whether those hours follow the tide. READ-ONLY (sw1-raycast mode nwps-blank, run 66 onward).

It SELECTs from the spots and forecasts tables, reads NWPS cycles from NOMADS through the
pipeline's own reader, and writes nothing to Supabase. The only files it writes are its own
report (--out, --csv); the reader also leaves its usual pipeline/forecast_data/nwps.json and
GRIB cache on the runner, which nothing reads afterwards.

    python -m pipeline.nwps_blank [--spots SLUG,...] [--roster ROSTER.json] [--since ISO]
        [--cycles N] [--out OUT.json] [--csv SPOTS.csv]

THE QUESTION. Some sample cells go blank at certain hours: Rodanthe's old cell did, about twice a
day, in a tidal pattern (a cell that dries at low water reads NaN). nwps.fetch keeps an hour's
hs, tp and dp only where the cell has them; apply_nwps_overrides rates only hours that have all
three, and nwps_nearshore.series_hour fills a missing one from the hour before or after. So a
blank hour is published with stars, face_ft and swell_* from a neighbour and hs, tp, dp empty.

1. THE CYCLES. For each of the --cycles newest cycles NOMADS lists for each office
   (nwps.candidate_cycles), nwps.fetch itself is run on the roster with only that cycle on
   offer: the same subset download, the same sample point (the baked node if wet, else the
   seaward ring walk) and the same extraction as production. Per NWPS spot and cycle:
     hours   the valid hours the office's cycle has for any spot;
     blank   those where the spot's entry is missing or lacks hs, tp or dp, the hours the
             override fills from a neighbour (or leaves to the orientation path).
   An office whose k-th cycle could not be read is reported, not counted.
2. THE ARCHIVE (forecasts since --since). Rows are rated hours; per spot:
     filled     NWPS-rated (swell_source nwps or nwps_height_ww3_dir) with hs, tp or dp empty:
                exactly the blank hours that were published, filled from a neighbour;
     unrated    hs, tp or dp empty on a row that is not NWPS-rated;
     by cycle   per nwps_cycle, the hours that cycle still holds and how many are filled.
3. THE TIDE. Both views are set against the stored tide_level_ft of the same spot and hour.
   Over the hours with a stored tide: the low quarter is the hours at or below the value at
   rank floor(0.25 (n-1)) of the sorted tides, the high quarter at or above rank
   floor(0.75 (n-1)). A spot with at least MIN_BLANK blank hours FOLLOWS THE TIDE when at
   least TIDE_SHARE of them are in the low quarter ("low tide") or the high quarter ("high
   tide"); else "not tidal"; under MIN_BLANK, "too few". Also: how many separate blank runs a
   day, and the median hours from one run's start to the next (about 12.4 for a semidiurnal
   tide).
"""
from __future__ import annotations

import argparse
import copy
import csv
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from statistics import median

NWPS_KINDS = ("nwps", "nwps_height_ww3_dir")
SINCE = "2026-10-07T00:00:00+00:00"
CYCLES = 3
MIN_BLANK = 6
TIDE_SHARE = 0.6
NAMED = ("moonstone-beach-humboldt", "bandon-beach", "rodanthe")
FIELDS = ("hs", "tp", "dp")


def _slug(name):
    from .enrich import _slug_for
    return _slug_for(name)


def _hour(iso):
    """Hours since the epoch, UTC, for an ISO time (Z or an offset)."""
    t = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
    return int(t.timestamp() // 3600)


def _iso(h):
    return datetime.fromtimestamp(h * 3600, timezone.utc).strftime("%Y-%m-%dT%HZ")


def _complete(e):
    return all(e.get(k) is not None for k in FIELDS)


# --------------------------------------------------------------------------- #
# The tide                                                                    #
# --------------------------------------------------------------------------- #
def runs(hours):
    """Consecutive runs in a set of hours: [(first hour, length)], in order."""
    out = []
    for h in sorted(hours):
        if out and h == out[-1][0] + out[-1][1]:
            out[-1] = (out[-1][0], out[-1][1] + 1)
        else:
            out.append((h, 1))
    return out


def tide_test(obs):
    """obs: [(tide_ft, blank)] observations with a stored tide. The verdict and the numbers
    behind it (module docstring, 3)."""
    tides = sorted(t for t, _b in obs)
    blank = [t for t, b in obs if b]
    out = {"hours_with_tide": len(tides), "blank_with_tide": len(blank)}
    if len(tides) < 4:
        return {**out, "verdict": "too few"}
    lo = tides[int(0.25 * (len(tides) - 1))]
    hi = tides[int(0.75 * (len(tides) - 1))]
    out.update(low_quarter_ft=lo, high_quarter_ft=hi,
               median_tide_blank=median(blank) if blank else None,
               median_tide_other=median([t for t, b in obs if not b]) if len(blank) < len(obs) else None)
    if len(blank) < MIN_BLANK:
        return {**out, "verdict": "too few"}
    low = sum(1 for t in blank if t <= lo) / len(blank)
    high = sum(1 for t in blank if t >= hi) / len(blank)
    out.update(low_share=round(low, 3), high_share=round(high, 3))
    out["verdict"] = ("low tide" if low >= TIDE_SHARE else "high tide" if high >= TIDE_SHARE
                      else "not tidal")
    return out


def run_pattern(blank_hours, span_hours):
    """Blank runs a day over the span, and the median hours between consecutive run starts."""
    r = runs(blank_hours)
    gaps = [b[0] - a[0] for a, b in zip(r, r[1:])]
    return {"runs": len(r), "runs_per_day": round(len(r) * 24 / span_hours, 2) if span_hours else None,
            "median_gap_h": median(gaps) if gaps else None}


# --------------------------------------------------------------------------- #
# 1. The cycles                                                               #
# --------------------------------------------------------------------------- #
def blank_by_cycle(by_spot, wfo_of, names):
    """For one cycle's fetch output (nwps.fetch's {spot name: [entry]}): {name: {"hours":
    [hour, ...], "blank": [hour, ...]}} for each of *names* whose office has any hour in it.
    The office's hours are every valid hour any of its spots has; a spot with no entries at all
    is blank on every one of them."""
    office_hours = defaultdict(set)
    for name, entries in by_spot.items():
        for e in entries:
            office_hours[wfo_of.get(name)].add(_hour(e["valid_time"]))
    out = {}
    for name in names:
        hours = office_hours.get(wfo_of.get(name))
        if not hours:
            continue
        have = {_hour(e["valid_time"]) for e in by_spot.get(name, []) if _complete(e)}
        out[name] = {"hours": sorted(hours), "blank": sorted(hours - have)}
    return out


def fetch_cycle(roster, k, cands):
    """nwps.fetch on *roster* with only the k-th newest listed cycle of each office on offer.
    An office with fewer than k+1 listed cycles is offered none and sits the cycle out."""
    from .forecast import nwps as N
    orig = N.candidate_cycles
    N.candidate_cycles = lambda w: ([cands[w][k]] if len(cands.get(w) or []) > k else [])
    try:
        return N.fetch(copy.deepcopy(roster), use_cache=True)
    finally:
        N.candidate_cycles = orig


# --------------------------------------------------------------------------- #
# 2. The archive                                                              #
# --------------------------------------------------------------------------- #
def _page(q):
    out, frm = [], 0
    while True:
        rows = q().range(frm, frm + 999).execute().data or []
        out.extend(rows)
        if len(rows) < 1000:
            return out
        frm += 1000


def fetch_rows(client, spot_id, since):
    return _page(lambda: client.table("forecasts")
                 .select("valid_time, hs, tp, dp, stars, swell_source, tide_level_ft, nwps_cycle")
                 .eq("spot_id", spot_id).eq("source", "nwps")
                 .gte("valid_time", since).order("valid_time"))


def archive(rows, since_hour, now_hour):
    """The archive view of one spot's rows: rated hours before *now_hour* and from *since_hour*."""
    rated = [r for r in rows if r.get("stars") is not None
             and since_hour <= _hour(r["valid_time"]) < now_hour]
    filled = [r for r in rated if r.get("swell_source") in NWPS_KINDS and not _complete(r)]
    unrated = [r for r in rated if r.get("swell_source") not in NWPS_KINDS and not _complete(r)]
    by_cycle = defaultdict(lambda: [0, 0])
    for r in rated:
        if r.get("nwps_cycle"):
            c = by_cycle[str(r["nwps_cycle"])]
            c[0] += 1
            c[1] += r in filled
    held = list(by_cycle.values())
    fh = {_hour(r["valid_time"]) for r in filled}
    tides = [(float(r["tide_level_ft"]), _hour(r["valid_time"]) in fh)
             for r in rated if r.get("tide_level_ft") is not None]
    return {"hours": len(rated), "filled": len(filled), "unrated_blank": len(unrated),
            "cycles": len(held), "cycles_with_filled": sum(1 for _h, f in held if f),
            "tide": tide_test(tides), **run_pattern(fh, len(rated)),
            "filled_hours": [_iso(h) for h in sorted(fh)]}


# --------------------------------------------------------------------------- #
# Both                                                                         #
# --------------------------------------------------------------------------- #
def run(client, roster, slugs=(), since=SINCE, n_cycles=CYCLES, *, now=None, fetch=None,
        candidates=None):
    """The report. *fetch*(roster, k, cands) and *candidates*(wfo) are injectable for tests."""
    from .forecast import nwps as N
    fetch = fetch or fetch_cycle
    candidates = candidates or N.candidate_cycles
    now = now or datetime.now(timezone.utc)
    spots = [s for s in roster if s.get("swell_window_source") == "nwps"
             and s.get("is_valid_surf_spot") is not False]
    wfo_of = {s["name"]: (s.get("nwps_wfo") or "").lower() for s in roster}
    names = [s["name"] for s in spots]
    errors = {}

    cands = {}
    for w in sorted({wfo_of[n] for n in names if wfo_of[n]}):
        try:
            cands[w] = candidates(w) or []
        except Exception as e:                                     # noqa: BLE001 - reported
            errors[f"cycles {w}"] = f"{type(e).__name__}: {e}"[:200]
            cands[w] = []
    per_cycle = defaultdict(list)          # name -> [{cycle, hours, blank}]
    for k in range(n_cycles):
        try:
            by_spot = fetch(roster, k, cands)
        except Exception as e:                                     # noqa: BLE001 - reported
            errors[f"fetch cycle {k}"] = f"{type(e).__name__}: {e}"[:200]
            continue
        got = blank_by_cycle(by_spot, wfo_of, names)
        for name in names:
            w = wfo_of[name]
            label = (f"{w} {cands[w][k][0]} {cands[w][k][1]}Z" if len(cands.get(w) or []) > k
                     else f"{w} (no cycle {k})")
            if name in got:
                per_cycle[name].append({"cycle": label, **got[name]})
            elif len(cands.get(w) or []) > k:
                errors.setdefault(f"read {label}", "the office's cycle gave no hours")

    ids, frm = {}, 0
    while True:
        page = client.table("spots").select("id, name").order("id").range(frm, frm + 999).execute().data or []
        for r in page:
            ids[_slug(r["name"])] = r["id"]
        if len(page) < 1000:
            break
        frm += 1000
    cyc_hours = [h for name in names for c in per_cycle.get(name, []) for h in c["hours"]]
    first = min([_hour(since)] + cyc_hours) if cyc_hours else _hour(since)
    now_hour = int(now.timestamp() // 3600)
    out = []
    for s in spots:
        name, sl = s["name"], _slug(s["name"])
        rows = []
        if sl in ids:
            try:
                rows = fetch_rows(client, ids[sl], _iso(first).replace("Z", ":00:00+00:00"))
            except Exception as e:                                 # noqa: BLE001 - reported
                errors[f"forecasts {sl}"] = f"{type(e).__name__}: {e}"[:200]
        else:
            errors[f"no spots row {sl}"] = "not in the spots table"
        tide = {_hour(r["valid_time"]): float(r["tide_level_ft"]) for r in rows
                if r.get("tide_level_ft") is not None}
        cycles = per_cycle.get(name, [])
        # every (cycle, hour) the spot was read on, with a stored tide for that hour
        obs = [(tide[h], h in set(c["blank"])) for c in cycles for h in c["hours"] if h in tide]
        all_blank = {h for c in cycles for h in c["blank"]}
        hours = sum(len(c["hours"]) for c in cycles)
        blank_n = sum(len(c["blank"]) for c in cycles)
        out.append({
            "spot": sl, "name": name, "wfo": wfo_of[name],
            "node": [s.get("nwps_node_lat"), s.get("nwps_node_lng")],
            "cycles": [{"cycle": c["cycle"], "hours": len(c["hours"]), "blank": len(c["blank"]),
                        "blank_runs": [f"{_iso(h)}+{n}h" for h, n in runs(c["blank"])]}
                       for c in cycles],
            "cycle_hours": hours, "cycle_blank": blank_n,
            "cycle_blank_share": round(blank_n / hours, 4) if hours else None,
            "cycles_with_blank": sum(1 for c in cycles if c["blank"]),
            "cycle_tide": tide_test(obs),
            "cycle_pattern": run_pattern(all_blank, len({h for c in cycles for h in c["hours"]})),
            "archive": archive(rows, _hour(since), now_hour),
        })
    out.sort(key=lambda e: (-(e["cycle_blank_share"] or 0), -e["archive"]["filled"], e["spot"]))
    tally = defaultdict(int)
    for e in out:
        tally[e["cycle_tide"]["verdict"]] += 1
    summary = {
        "nwps_spots": len(out),
        "with_blank_in_a_cycle": sum(1 for e in out if e["cycle_blank"]),
        "with_blank_in_every_cycle": sum(1 for e in out if e["cycles"]
                                         and e["cycles_with_blank"] == len(e["cycles"])),
        "with_filled_in_archive": sum(1 for e in out if e["archive"]["filled"]),
        "cycle_verdicts": dict(sorted(tally.items())),
        "archive_verdicts": dict(sorted(_count(e["archive"]["tide"]["verdict"] for e in out).items())),
    }
    return {"window": {"archive_since": since, "cycles_per_office": n_cycles,
                       "now": now.isoformat(), "min_blank": MIN_BLANK, "tide_share": TIDE_SHARE},
            "candidates": {w: [f"{d} {h}Z" for d, h in c] for w, c in cands.items()},
            "summary": summary, "named": [e for e in out if e["spot"] in set(NAMED) | set(slugs)],
            "spots": out, "errors": errors}


def _count(xs):
    c = defaultdict(int)
    for x in xs:
        c[x] += 1
    return c


def csv_rows(rep):
    """One CSV row per NWPS spot."""
    rows = []
    for e in rep["spots"]:
        ct, at = e["cycle_tide"], e["archive"]["tide"]
        rows.append({
            "spot": e["spot"], "wfo": e["wfo"], "cycles": len(e["cycles"]),
            "cycle_hours": e["cycle_hours"], "cycle_blank": e["cycle_blank"],
            "cycle_blank_share": e["cycle_blank_share"], "cycles_with_blank": e["cycles_with_blank"],
            "cycle_verdict": ct["verdict"], "cycle_low_share": ct.get("low_share"),
            "cycle_high_share": ct.get("high_share"),
            "cycle_runs_per_day": e["cycle_pattern"]["runs_per_day"],
            "cycle_median_gap_h": e["cycle_pattern"]["median_gap_h"],
            "archive_hours": e["archive"]["hours"], "archive_filled": e["archive"]["filled"],
            "archive_unrated_blank": e["archive"]["unrated_blank"],
            "archive_cycles": e["archive"]["cycles"],
            "archive_cycles_with_filled": e["archive"]["cycles_with_filled"],
            "archive_verdict": at["verdict"], "archive_low_share": at.get("low_share"),
            "archive_high_share": at.get("high_share"),
            "archive_runs_per_day": e["archive"]["runs_per_day"],
            "archive_median_gap_h": e["archive"]["median_gap_h"],
        })
    return rows


def print_report(rep):
    w = rep["window"]
    print(f"nwps-blank: {w['cycles_per_office']} newest cycles per office; archive since "
          f"{w['archive_since']}; a spot follows the tide with {w['min_blank']}+ blank hours, "
          f"{w['tide_share']:.0%} of them in one quarter of its tide range")
    print("summary:", json.dumps(rep["summary"]))
    print("\nmost blank in the cycles (share of cycle hours):")
    for e in [e for e in rep["spots"] if e["cycle_blank"]][:30]:
        ct, a = e["cycle_tide"], e["archive"]
        print(f"  {e['spot']:32s} {e['wfo']}  {e['cycle_blank']:4d}/{e['cycle_hours']:<4d} "
              f"{ct['verdict']:10s} low {ct.get('low_share')} high {ct.get('high_share')}  "
              f"runs/day {e['cycle_pattern']['runs_per_day']} gap {e['cycle_pattern']['median_gap_h']}h  "
              f"| archive filled {a['filled']}/{a['hours']} {a['tide']['verdict']}")
    print("\nnamed spots:")
    for e in rep["named"]:
        print(f"  {e['spot']} ({e['wfo']}, node {e['node']}):")
        for c in e["cycles"]:
            print(f"    {c['cycle']}: {c['blank']}/{c['hours']} blank  {' '.join(c['blank_runs'])}")
        print(f"    cycles: {json.dumps(e['cycle_tide'])}")
        print(f"    archive: {e['archive']['filled']} filled of {e['archive']['hours']}, "
              f"{json.dumps(e['archive']['tide'])}")
    if rep["errors"]:
        print("\nerrors:")
        for k, v in sorted(rep["errors"].items()):
            print(f"  {k}: {v}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--roster", default="pipeline/spots_enriched.json")
    p.add_argument("--spots", default="", help="comma-separated slugs to list hour by hour too")
    p.add_argument("--since", default=SINCE)
    p.add_argument("--cycles", type=int, default=CYCLES)
    p.add_argument("--out", default=None)
    p.add_argument("--csv", default=None)
    a = p.parse_args(argv)
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    with open(a.roster) as fh:
        roster = json.load(fh)
    slugs = [s.strip() for s in a.spots.split(",") if s.strip()]
    rep = run(client, roster, slugs, a.since, a.cycles)
    print_report(rep)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(rep, fh, indent=1, default=str)
    if a.csv:
        rows = csv_rows(rep)
        if rows:
            with open(a.csv, "w", newline="") as fh:
                wr = csv.DictWriter(fh, fieldnames=list(rows[0]))
                wr.writeheader()
                wr.writerows(rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
