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
  * the first that passes is the buoy, the next up to three are its fallbacks.

FOR EACH SPOT the report gives the current buoy (stored distance, the real distance from the
snapshot, whether it is a wave station now, within the cap, in line of sight), the cap, the rule's
choice and distance or the reason there is none, and whether the choice changes. The reason comes
from DIAGNOSE, which walks the same 10 candidates with the rule's own cap and line-of-sight
helpers; its choice is checked against compute_nearest_buoy's on every spot.

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


def diagnose(spot_for_algo, stations, land, cap_km, los=None, k=K_CANDIDATES):
    """The rule's steps for one spot, with the reason when nothing passes.

    {"candidates": [{id, km, within_cap, los}], "choice": id|None, "choice_km", "fallbacks",
     "reason": None | "no_wave_stations" | "beyond_cap" | "no_line_of_sight"}

    The k nearest by great-circle distance: the same order as the rule's KD-tree on unit vectors,
    whose chord length grows with the great-circle distance. Line of sight is asked only of a
    candidate within the cap, as the rule does."""
    los = los or B._los_clear
    lat, lng = spot_for_algo["_algo_lat"], spot_for_algo["_algo_lng"]
    if not stations:
        return {"candidates": [], "choice": None, "choice_km": None, "fallbacks": [],
                "reason": "no_wave_stations"}
    near = sorted(((_km(lat, lng, s["lat"], s["lng"]), s) for s in stations),
                  key=lambda t: t[0])[:k]
    cands, passing = [], []
    for km, s in near:
        within = km <= cap_km
        clear = bool(los(spot_for_algo, s, land)) if within else None
        cands.append({"id": s["id"], "km": round(km, 2), "within_cap": within, "los": clear})
        if within and clear and len(passing) < 4:
            passing.append((s["id"], km))
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


def use_wave_files(xml_path, obs_path):
    """Point the rule at these NDBC files and drop the cached station list and KD-tree."""
    G.NDBC_STATIONS_XML = Path(xml_path)
    G.NDBC_LATEST_OBS_TXT = Path(obs_path)
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
        "candidates": d["candidates"],
        "diagnose_agrees": (d["choice"] == choice
                            and (choice is None or d["fallbacks"] == r["fallback_buoy_ids"])),
        "changes": (str(choice).lower() if choice else None) != cur_l,
    }


def caps():
    return {"by_region": dict(BUOY_CAP_KM), "gulf_florida_west_of_83W": BUOY_CAP_GULF_FLORIDA,
            "east_coast_default": BUOY_CAP_DEFAULT_EAST, "default": BUOY_CAP_DEFAULT}


def summarise(rows):
    out = {}
    for g in ("dropped", "none"):
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


def run(roster, snapshot, land, lists, rule=None):
    """*lists*: [(label, stations)] — the first is the headline."""
    sc = scope(roster, snapshot)
    report = {"caps_km": caps(), "tolerance_km": COORD_DERIVED_DIST_TOLERANCE_KM,
              "k_candidates": K_CANDIDATES, "scope": {"dropped": sum(1 for _s, g in sc if g == "dropped"),
                                                     "none": sum(1 for _s, g in sc if g == "none")},
              "lists": {}}
    for label, stations in lists:
        rows = [assess(s, g, land, stations, snapshot, rule) for s, g in sc]
        report["lists"][label] = {"wave_stations": len(stations), "summary": summarise(rows),
                                  "spots": rows}
    return report


def print_report(rep):
    print(f"buoy-assign: {rep['scope']['dropped']} spots whose buoy db_import drops, "
          f"{rep['scope']['none']} with no buoy; tolerance {rep['tolerance_km']} km, "
          f"the {rep['k_candidates']} nearest wave stations")
    print(f"caps (km): {json.dumps(rep['caps_km'])}")
    for label, L in rep["lists"].items():
        print(f"\n== {label}: {L['wave_stations']} wave stations ==")
        print(json.dumps(L["summary"]))
        for r in L["spots"]:
            cur = r["current"]
            c = (f"{cur['id']} stored {cur['stored_km']} real {cur['km_from_rule_point']}"
                 f"{'' if cur['is_wave_station'] else ' NOT-WAVE'}") if cur else "none"
            pick = (f"{r['choice']} {r['choice_km']} km fb {r['fallbacks']}" if r["choice"]
                    else f"none ({r['reason']})")
            print(f"  [{r['group']}] {r['name']} ({r['region']}, cap {r['cap_km']}): {c} -> {pick}"
                  f"{'  CHANGES' if r['changes'] else ''}"
                  f"{'' if r['diagnose_agrees'] else '  DIAGNOSE-DISAGREES'}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--roster", default=str(DEFAULT_ENRICHED_OUTPUT))
    p.add_argument("--fresh-xml", default="")
    p.add_argument("--fresh-obs", default="")
    p.add_argument("--out", default="buoy_assign.json")
    a = p.parse_args(argv)
    with open(a.roster, encoding="utf-8") as f:
        roster = json.load(f)
    snapshot = G.load_buoy_snapshot()
    land = G.load_land_index()
    if land is None:
        raise SystemExit("buoy-assign: no GSHHG land index; the rule cannot test line of sight")
    cached = (G.NDBC_STATIONS_XML, G.NDBC_LATEST_OBS_TXT)
    files = [("fresh", (a.fresh_xml, a.fresh_obs))] if a.fresh_xml and a.fresh_obs else []
    files.append(("cached", cached))
    report = {"generated_at": datetime.now(timezone.utc).isoformat(),
              "files": {label: {"xml": str(x), "xml_mtime": _age(x), "obs": str(o),
                                "obs_mtime": _age(o)} for label, (x, o) in files}}
    # compute_nearest_buoy reads whichever files are set, so each list runs with its own.
    for label, (x, o) in files:
        part = run(roster, snapshot, land, [(label, use_wave_files(x, o))])
        report.update({k: part[k] for k in ("caps_km", "tolerance_km", "k_candidates", "scope")})
        report.setdefault("lists", {})[label] = part["lists"][label]
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)
    print_report(report)


if __name__ == "__main__":
    main()
