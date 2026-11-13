"""Buoy assignment from the runner: what enrich's buoy rule picks today. READ-ONLY.

sw1-raycast mode buoy-assign (run 64 onward). Reads the roster, the committed buoy snapshot,
the runner's GSHHG land polygons and NDBC's station lists. Writes nothing but its own report
(--out). No Supabase.

    python -m pipeline.buoy_assign [--roster ROSTER.json] [--fresh-xml X --fresh-obs O] [--out OUT.json]

THE QUESTION. Every full pipeline run, db_import blanks 39 spots' buoy (stored distance more than
COORD_DERIVED_DIST_TOLERANCE_KM from the great-circle to the buoy's snapshot position), so their
pages say "No nearby buoy.". Another 173 roster spots carry no buoy at all. For both groups: which
buoy does the rule pick now, at the spot's current coordinates, and if none, why?

THE RULE is enrichment/buoys.compute_nearest_buoy, run here as enrich runs it:
  * from the spot's seaward-adjusted point (enrichment/adjust.seaward_adjust), when the spot sits
    inside a GSHHG polygon;
  * over NDBC's WAVE stations only: active stations (activestations.xml) that reported a wave
    height in latest_obs.txt when the files were downloaded (geodata.load_ndbc_wave_stations);
  * the 10 nearest; a candidate passes when it is within the region's cap (BUOY_CAP_KM,
    buoys._regional_cap_km) and the geodesic to it crosses no GSHHG land (buoys._los_clear);
  * the first that passes is the buoy, the next up to three are its fallbacks;
  * from 2026-11-13, the written exceptions in pipeline/data/buoy_overrides.json, as the rule
    applies them (buoys.buoy_override): a pinned buoy is the choice whatever its line of sight,
    an excluded station never passes. Each row names its exception's reason ("override").

FOR EACH SPOT the report gives the current buoy (stored distance, the real distance from the
snapshot, whether it is a wave station now, within the cap, in line of sight), the cap, the rule's
choice and distance or the reason there is none, and whether the choice changes. The reason comes
from DIAGNOSE, which walks the same 10 candidates with the rule's own cap and line-of-sight
helpers; its choice is checked against compute_nearest_buoy's on every spot.

ALL SPOTS (--spots all, run 72 onward). Every roster spot, each in its group: 'dropped' and
'none' as above, 'kept' for a spot whose buoy db_import keeps. Each spot's OUTCOME compares its
roster buoy (nearest_buoy_id) with the rule's choice:
  gain    no buoy now, the rule picks one        loss    a buoy now, the rule picks none
  change  a buoy now, the rule picks another     same    the rule picks the same buoy
  none    no buoy now and none picked
and each list reports the counts over every spot (report["lists"][label]["outcomes"]).

TWO STATION LISTS. The runner's cached copy (the geodata cache, what an enrich on the runner
would use) and, with --fresh-xml/--fresh-obs, a copy downloaded for this run. The fresh one is the
headline; the cached one's choices are given beside it.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .config import BUOY_CAP_KM, BUOY_CAP_DEFAULT, BUOY_CAP_DEFAULT_EAST, BUOY_CAP_GULF_FLORIDA
from .config import COORD_DERIVED_DIST_TOLERANCE_KM, DEFAULT_ENRICHED_OUTPUT
from .enrichment import buoys as B
from .enrichment import geodata as G
from .enrichment.adjust import seaward_adjust
from .geo import haversine_m
from . import ndbc_recent_wave as RW

K_CANDIDATES = 10          # compute_nearest_buoy's default


def _km(lat1, lng1, lat2, lng2):
    return haversine_m(lat1, lng1, lat2, lng2) / 1000.0


def scope(roster, snapshot, tol=COORD_DERIVED_DIST_TOLERANCE_KM):
    """[(spot, group)] in roster order: 'dropped' when db_import would blank the spot's buoy (its
    stored distance is more than *tol* from the great-circle to the snapshot position), 'none'
    when the spot has no buoy. A buoy missing from the snapshot is not checked by db_import, so
    it is neither."""
    out = []
    for s in roster:
        bid = s.get("nearest_buoy_id")
        if not bid:
            out.append((s, "none"))
            continue
        b = snapshot.get(str(bid).lower())
        stored = s.get("nearest_buoy_dist_km")
        if b is None or stored is None:
            continue
        if abs(_km(s["lat"], s["lng"], b["lat"], b["lng"]) - stored) > tol:
            out.append((s, "dropped"))
    return out


def everyone(roster, snapshot, tol=COORD_DERIVED_DIST_TOLERANCE_KM):
    """[(spot, group)] for EVERY roster spot, in roster order: scope()'s 'dropped' and 'none',
    and 'kept' for every other spot (its buoy db_import keeps, or one it does not check)."""
    grouped = {id(s): g for s, g in scope(roster, snapshot, tol)}
    return [(s, grouped.get(id(s), "kept")) for s in roster]


OUTCOMES = ("gain", "loss", "change", "same", "none")


def outcome(current_id, choice):
    """The spot's roster buoy against the rule's choice: gain, loss, change, same or none."""
    cur = str(current_id).lower() if current_id else None
    new = str(choice).lower() if choice else None
    if cur is None:
        return "gain" if new else "none"
    if new is None:
        return "loss"
    return "same" if new == cur else "change"


def outcome_counts(rows):
    """{outcome: spots} over *rows*, every outcome listed."""
    return {k: sum(1 for r in rows if r["outcome"] == k) for k in OUTCOMES}


def diagnose(spot_for_algo, stations, land, cap_km, los=None, k=K_CANDIDATES):
    """The rule's steps for one spot, with the reason when nothing passes.

    {"candidates": [{id, km, within_cap, los}], "choice": id|None, "choice_km", "fallbacks",
     "reason": None | "no_wave_stations" | "beyond_cap" | "no_line_of_sight"}

    The k nearest by great-circle distance: the same order as the rule's KD-tree on unit vectors,
    whose chord length grows with the great-circle distance. Line of sight is asked only of a
    candidate within the cap, as the rule does."""
    los = los or B._los_clear
    lat, lng = spot_for_algo["_algo_lat"], spot_for_algo["_algo_lng"]
    # The rule's written exceptions (pipeline/data/buoy_overrides.json), as compute_nearest_buoy
    # applies them: an excluded station is listed but never passes; a pin is the choice.
    override = B.buoy_override(spot_for_algo)
    excluded = {str(x).lower() for x in override.get("exclude") or []}
    if not stations:
        return {"candidates": [], "choice": None, "choice_km": None, "fallbacks": [],
                "reason": "no_wave_stations"}
    near = sorted(((_km(lat, lng, s["lat"], s["lng"]), s) for s in stations),
                  key=lambda t: t[0])[:k]
    cands, passing = [], []
    for km, s in near:
        if str(s["id"]).lower() in excluded:
            cands.append({"id": s["id"], "km": round(km, 2), "within_cap": km <= cap_km,
                          "los": None, "excluded": True})
            continue
        within = km <= cap_km
        clear = bool(los(spot_for_algo, s, land)) if within else None
        cands.append({"id": s["id"], "km": round(km, 2), "within_cap": within, "los": clear})
        if within and clear and len(passing) < 4:
            passing.append((s["id"], km))
    pin = B.pinned(override, stations, lat, lng, cap_km, passing)
    if pin is not None:
        return {"candidates": cands, "choice": pin["nearest_buoy_id"],
                "choice_km": pin["nearest_buoy_dist_km"], "fallbacks": pin["fallback_buoy_ids"],
                "reason": None}
    if passing:
        return {"candidates": cands, "choice": passing[0][0], "choice_km": round(passing[0][1], 2),
                "fallbacks": [p[0] for p in passing[1:]], "reason": None}
    reason = "beyond_cap" if not any(c["within_cap"] for c in cands) else "no_line_of_sight"
    return {"candidates": cands, "choice": None, "choice_km": None, "fallbacks": [],
            "reason": reason}


def algo_point(spot, land):
    """The point enrich runs the rule from (enrich._enrich_one: seaward_adjust when on land)."""
    lat, lng, adjusted = spot["lat"], spot["lng"], False
    if land is not None:
        lat, lng, adjusted = seaward_adjust(spot["lat"], spot["lng"], land)
    return {**spot, "_algo_lat": lat, "_algo_lng": lng}, adjusted


_NO_RECENT = Path("/nonexistent/ndbc_recent_wave.json")


def pick(roster, names):
    """[(spot, 'held')] for the spots named (names or slugs, as db_import slugs them)."""
    from .db_import import _slugify
    want = {_slugify(n.strip()) for n in names if n.strip()}
    return [(s, "held") for s in roster if _slugify(s["name"]) in want]


def use_wave_files(xml_path, obs_path, recent_path=None):
    """Point the rule at these NDBC files (and recent-wave list, or none) and drop the cached
    station list and KD-tree."""
    G.NDBC_STATIONS_XML = Path(xml_path)
    G.NDBC_LATEST_OBS_TXT = Path(obs_path)
    G.NDBC_RECENT_WAVE_JSON = Path(recent_path) if recent_path else _NO_RECENT
    G.load_ndbc_wave_stations.cache_clear()
    B._build_kdtree.cache_clear()
    return G.load_ndbc_wave_stations()


def assess(spot, group, land, stations, snapshot, rule=None):
    """One spot against one station list."""
    rule = rule or B.compute_nearest_buoy
    sfa, adjusted = algo_point(spot, land)
    cap = B._regional_cap_km(spot)
    d = diagnose(sfa, stations, land, cap)
    r = rule(sfa)
    wave = {s["id"]: s for s in stations}
    cur = spot.get("nearest_buoy_id")
    cur_l = str(cur).lower() if cur else None
    current = None
    if cur:
        snap = snapshot.get(cur_l)
        ws = wave.get(cur_l)
        pos = {**(ws or snap), "id": cur_l} if (ws or snap) else None
        real = round(_km(sfa["_algo_lat"], sfa["_algo_lng"], pos["lat"], pos["lng"]), 2) if pos else None
        current = {"id": cur, "stored_km": spot.get("nearest_buoy_dist_km"),
                   "snapshot_km": (round(_km(spot["lat"], spot["lng"], snap["lat"], snap["lng"]), 2)
                                   if snap else None),
                   "km_from_rule_point": real,
                   "is_wave_station": ws is not None,
                   "within_cap": (real <= cap) if real is not None else None,
                   "los": bool(B._los_clear(sfa, pos, land)) if (pos and real is not None
                                                                  and real <= cap) else None}
    choice = r["nearest_buoy_id"]
    return {
        "name": spot.get("name"), "region": spot.get("region_hint"), "group": group,
        "valid": spot.get("is_valid_surf_spot", True) is not False,
        "lat": spot["lat"], "lng": spot["lng"],
        "rule_point": [round(sfa["_algo_lat"], 5), round(sfa["_algo_lng"], 5)],
        "seaward_adjusted": adjusted, "cap_km": cap,
        "current": current,
        "choice": choice, "choice_km": r["nearest_buoy_dist_km"], "fallbacks": r["fallback_buoy_ids"],
        "reason": d["reason"] if choice is None else None,
        "override": B.buoy_override(sfa).get("reason"),
        "candidates": d["candidates"],
        "diagnose_agrees": (d["choice"] == choice
                            and (choice is None or d["fallbacks"] == r["fallback_buoy_ids"])),
        "changes": (str(choice).lower() if choice else None) != cur_l,
        "outcome": outcome(cur, choice),
    }


def caps():
    return {"by_region": dict(BUOY_CAP_KM), "gulf_florida_west_of_83W": BUOY_CAP_GULF_FLORIDA,
            "east_coast_default": BUOY_CAP_DEFAULT_EAST, "default": BUOY_CAP_DEFAULT}


def summarise(rows):
    out = {}
    for g in sorted({r["group"] for r in rows}):
        rs = [r for r in rows if r["group"] == g]
        out[g] = {"spots": len(rs),
                  "rule_picks_a_buoy": sum(1 for r in rs if r["choice"]),
                  "changes": sum(1 for r in rs if r["changes"]),
                  "same_buoy": sum(1 for r in rs if r["choice"] and not r["changes"]),
                  "no_buoy": {k: sum(1 for r in rs if r["choice"] is None and r["reason"] == k)
                              for k in ("beyond_cap", "no_line_of_sight", "no_wave_stations")},
                  "diagnose_disagrees": sum(1 for r in rs if not r["diagnose_agrees"])}
    return out


def _age(path):
    try:
        t = os.path.getmtime(path)
    except OSError:
        return None
    return datetime.fromtimestamp(t, tz=timezone.utc).isoformat()


def run(roster, snapshot, land, lists, rule=None, sc=None):
    """*lists*: [(label, stations)] — the first is the headline. *sc*: the scope, default scope()."""
    sc = scope(roster, snapshot) if sc is None else sc
    groups = {}
    for _s, g in sc:
        groups[g] = groups.get(g, 0) + 1
    report = {"caps_km": caps(), "tolerance_km": COORD_DERIVED_DIST_TOLERANCE_KM,
              "k_candidates": K_CANDIDATES, "scope": groups, "lists": {}}
    for label, stations in lists:
        rows = [assess(s, g, land, stations, snapshot, rule) for s, g in sc]
        report["lists"][label] = {"wave_stations": len(stations), "summary": summarise(rows),
                                  "outcomes": outcome_counts(rows), "spots": rows}
    return report


def print_report(rep):
    print(f"buoy-assign: scope {json.dumps(rep['scope'])}; tolerance {rep['tolerance_km']} km, "
          f"the {rep['k_candidates']} nearest wave stations")
    print(f"caps (km): {json.dumps(rep['caps_km'])}")
    rw = rep.get("recent_wave")
    if rw:
        print(f"\nrecent wave heights, last {rw['days']:g} days: {len(rw['stations'])} of "
              f"{len(rw['checked'])} stations checked")
        for sid, c in sorted(rw["checked"].items()):
            print(f"  {sid}: " + (c["error"] if "error" in c else "no realtime2 file" if c.get("no_file")
                  else f"{c['wave_rows_in_window']} wave rows, last wave {c['last_wave']}, "
                       f"newest row {c['newest_row']}"))
    for label, L in rep["lists"].items():
        print(f"\n== {label}: {L['wave_stations']} wave stations ==")
        print(json.dumps(L["summary"]))
        if L.get("outcomes"):
            print("outcomes: " + ", ".join(f"{k} {v}" for k, v in L["outcomes"].items()))
        for r in L["spots"]:
            cur = r["current"]
            c = (f"{cur['id']} stored {cur['stored_km']} real {cur['km_from_rule_point']}"
                 f"{'' if cur['is_wave_station'] else ' NOT-WAVE'}") if cur else "none"
            pick = (f"{r['choice']} {r['choice_km']} km fb {r['fallbacks']}" if r["choice"]
                    else f"none ({r['reason']})")
            print(f"  [{r['group']}] {r['name']} ({r['region']}, cap {r['cap_km']}): {c} -> {pick}"
                  f"  {r.get('outcome', '').upper()}"
                  f"{'' if r['diagnose_agrees'] else '  DIAGNOSE-DISAGREES'}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--roster", default=str(DEFAULT_ENRICHED_OUTPUT))
    p.add_argument("--fresh-xml", default="")
    p.add_argument("--fresh-obs", default="")
    p.add_argument("--spots", default="", help="comma-separated spot names or slugs, or 'all' for "
                   "every roster spot (default: the spots db_import drops and the spots with no "
                   "buoy)")
    p.add_argument("--recent-days", type=float, default=0.0,
                   help="also count a station that reported a wave height in the last N days")
    p.add_argument("--out", default="buoy_assign.json")
    a = p.parse_args(argv)
    with open(a.roster, encoding="utf-8") as f:
        roster = json.load(f)
    snapshot = G.load_buoy_snapshot()
    land = G.load_land_index()
    if land is None:
        raise SystemExit("buoy-assign: no GSHHG land index; the rule cannot test line of sight")
    if a.spots.strip().lower() == "all":
        sc = everyone(roster, snapshot)
    elif a.spots.strip():
        sc = pick(roster, a.spots.split(","))
    else:
        sc = scope(roster, snapshot)
    cached = (G.NDBC_STATIONS_XML, G.NDBC_LATEST_OBS_TXT)
    base = [("fresh", (a.fresh_xml, a.fresh_obs))] if a.fresh_xml and a.fresh_obs else []
    base.append(("cached", cached))
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "files": {label: {"xml": str(x), "xml_mtime": _age(x), "obs": str(o),
                                "obs_mtime": _age(o)} for label, (x, o) in base}}
    files = [(label, x, o, None) for label, (x, o) in base]
    if a.recent_days > 0:
        # The recent-wave list, for every active station within the cap of a spot in scope, read
        # from the headline station list; then each list is run with it and without it.
        x0 = base[0][1][0]
        ids = RW.candidates([s for s, _g in sc], RW.active_stations(x0), B._regional_cap_km)
        recent = RW.build(ids, datetime.now(timezone.utc), a.recent_days)
        recent_path = os.path.splitext(a.out)[0] + "_recent_wave.json"
        with open(recent_path, "w", encoding="utf-8") as f:
            json.dump(recent, f, indent=1)
        report["recent_wave"] = recent
        files = [(f"{label}+{a.recent_days:g}d", x, o, recent_path) for label, (x, o) in base] + files
    # compute_nearest_buoy reads whichever files are set, so each list runs with its own.
    for label, x, o, rp in files:
        part = run(roster, snapshot, land, [(label, use_wave_files(x, o, rp))], sc=sc)
        report.update({k: part[k] for k in ("caps_km", "tolerance_km", "k_candidates", "scope")})
        report.setdefault("lists", {})[label] = part["lists"][label]
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print_report(report)


if __name__ == "__main__":
    main()
