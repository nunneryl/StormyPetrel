#!/usr/bin/env python3
"""Real-GSHHG acceptance bar for the NODE-SCALE headland constants (NODE_HEADLAND_*).

WHY THIS EXISTS. The buoy-scale constants have measured evidence behind them:
FIND_BUOY_HEADLAND_OWN_GRAZE_KM was set 8.0 -> 3.0 because must-CLEAR along-coast pairings
reach at most 2.15 km inland while must-FLAG Canaveral crossings reach at least 4.14 km, and
3.0 sits in that gap. Their acceptance bars are 27 pairs at Canaveral (exactly 6 FLAG / 21
clear) and 6 at mfl (4 clear / 2 FLAG).

The four NODE_HEADLAND_* constants have NO such measurement. They were reasoned from the
geometry of a ~1.35 km median spot->node path and are covered offline only by synthetic
polygons. This script is the equivalent bar. It is READ-ONLY: it loads spots_enriched.json,
the assignments file and GSHHG, and writes nothing.

WHAT EACH PHASE CALIBRATES
  phase 1  FALSE POSITIVES, and the two trims. GSHHG only — NO NOMADS, so it runs anywhere the
           shapefile is present. Every placed spot against its already-baked node. Expected:
           ZERO rejects. Reports the two distributions that SET the trims:
             * spot -> GSHHG shore, signed (+ = the spot's own coordinate sits inland). The
               near trim must EXCEED the largest inland value or those spots reject on their
               own beach, silently, and every candidate after them too.
             * node -> GSHHG shore, and any model-wet node that GSHHG places INSIDE land (the
               NWPS mask and GSHHG are different datasets; a 1-3 km cell centre can disagree).
               The end trim must exceed that penetration.
  phase 2  FALSE NEGATIVES. Needs a CG1 cycle per WFO (Mac/NOMADS). Enumerates wet cells near
           each spot and reports any that is NEARER than the chosen node with land in between —
           the case the guard exists to catch. Records the crossed polygon's AREA and the
           crossing's inland PENETRATION, which are what set NODE_HEADLAND_AREA_KM2 and
           NODE_HEADLAND_OWN_GRAZE_KM. Run it on the barrier coasts listed in BARRIER_WFOS.
  phase 3  OVER-REJECTION by the graze ceiling. NODE_HEADLAND_OWN_GRAZE_KM is 0.0, which
           DISABLES the coast-parallel exclusion. On full-res GSHHG the whole North American
           mainland is ONE polygon, so that exclusion is the normal path, not an edge case —
           the same fact that made the buoy-scale value load-bearing. A spot on a straight
           beach whose nearest cell lies along-shore rather than straight out can clip its own
           beach for a couple of hundred metres. Expected: clear. Any reject here means 0.0 is
           too strict and the measured clip penetration is the floor for a positive value.

THE NUMBER YOU ARE LOOKING FOR is the same shape as 2.15 / 4.14: the largest must-CLEAR value
against the smallest must-FLAG value, with the constant set in the gap. Phases 1 and 3 produce
the must-CLEAR side; phase 2 produces the must-FLAG side. Until both exist, the four constants
are reasoned, not measured.

Run:
  python3 scripts/node_headland_calibrate.py --phase1
  python3 scripts/node_headland_calibrate.py --phase3
  python3 scripts/node_headland_calibrate.py --phase2 --wfo mfl     # then mhx, okx, phi, hgx, box
Exit status is 1 if a phase fails its expected verdict, so it can gate a merge.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.forecast import nwps_nearshore as nn   # noqa: E402

ENRICHED = Path(__file__).resolve().parents[1] / "pipeline" / "spots_enriched.json"

# Barrier / back-bay coasts where a wet cell BEHIND the land can be nearer than the ocean cell.
# These are the discriminating geographies for phase 2 — everywhere else the nearest wet cell is
# straight offshore and the guard has nothing to decide.
BARRIER_WFOS = {
    "mfl": "Intracoastal Waterway 0.3-1.0 km behind the SE Florida strip. RUN THIS FIRST: all "
           "22 spots currently awaiting placement are mfl, so this is the only geography where "
           "the guard fires in anger before anything else is placed.",
    "mhx": "Pamlico / Albemarle Sound behind Hatteras. The canonical case — a ~1 km wide barrier "
           "with wide open sound behind it; frisco-pier and hatteras-ferry-docks sit on it.",
    "okx": "Great South Bay behind Long Beach / Lido Beach (whose node is already only 137 m).",
    "phi": "Barnegat Bay behind the New Jersey barrier.",
    "hgx": "West Bay behind Galveston (galveston-seawall's node is 66 m — the shortest in the set).",
    "bro": "Laguna Madre behind South Padre Island.",
    "box": "Nantucket Sound on the far side of the outer Cape.",
}


def _load_land():
    from pipeline.enrichment.geodata import load_land_index
    land = load_land_index()
    if land is None or not getattr(land, "polygons", None):
        print("GSHHG full-res index unavailable — this bar REQUIRES it. Run on the Mac.",
              file=sys.stderr)
        raise SystemExit(2)
    return land


def _geod():
    from pyproj import Geod
    return Geod(ellps="WGS84")


def _shore_offset_km(lat, lng, land, geod):
    """(signed_km, polygon_index) to the nearest GSHHG shoreline. POSITIVE = the point is INSIDE
    land (that many km from the nearest shore); negative = offshore. This is the quantity the two
    trims have to clear: a point *x* km inland makes the first *x* km of its path a land crossing."""
    from shapely.geometry import Point
    from shapely.ops import nearest_points
    pt = Point(lng, lat)
    idx = nn._tree_idx(land.polygon_tree.nearest(pt), land.polygons)
    if idx is None:
        return None, None
    poly = land.polygons[idx]
    a, b = nearest_points(pt, poly.exterior)
    _, _, d = geod.inv(a.x, a.y, b.x, b.y)
    inside = poly.contains(pt)
    return (d / 1000.0) * (1.0 if inside else -1.0), idx


def _placed_spots():
    spots = json.loads(ENRICHED.read_text())
    return [s for s in spots
            if s.get("swell_window_source") == "nwps" and s.get("nwps_node_lat") is not None]


def _node_scale_kw():
    return dict(max_km=float("inf"),
                near_trim_km=nn.NODE_HEADLAND_NEAR_TRIM_KM,
                end_trim_km=nn.NODE_HEADLAND_END_TRIM_KM,
                area_km2=nn.NODE_HEADLAND_AREA_KM2,
                own_graze_km=nn.NODE_HEADLAND_OWN_GRAZE_KM)


def _describe_crossing(spot, node, land, geod):
    """(chord_km, area_km2, penetration_km) of the LONGEST land crossing on the trimmed
    spot->node path — measured directly rather than read off _headland_land_chord_km's detail
    out-param, which only populates own_penetration_km when the crossed polygon happens to be the
    node's own landmass AND the graze branch runs. Penetration is the number that sets
    NODE_HEADLAND_OWN_GRAZE_KM, so it has to be reported for EVERY crossing, not just some.
    Mirrors the chord function's densify/trim so the geometry described is the geometry judged."""
    from shapely.geometry import LineString
    (sla, sln), (nla, nln) = spot, node
    _, _, dist_m = geod.inv(sln, sla, nln, nla)
    total_km = dist_m / 1000.0
    near, end = nn.NODE_HEADLAND_NEAR_TRIM_KM, nn.NODE_HEADLAND_END_TRIM_KM
    if total_km <= near + end:
        return 0.0, None, None
    n = max(4, int(dist_m / nn.FIND_BUOY_HEADLAND_DENSIFY_M))
    pts = [(sln, sla)] + [(lo, la) for lo, la in geod.npts(sln, sla, nln, nla, n - 1)] + [(nln, nla)]
    cum, acc = [0.0], 0.0
    for (l1, a1), (l2, a2) in zip(pts[:-1], pts[1:]):
        _, _, d = geod.inv(l1, a1, l2, a2); acc += d / 1000.0; cum.append(acc)
    mid = [p for p, c in zip(pts, cum) if near <= c <= total_km - end]
    if len(mid) < 2:
        return 0.0, None, None
    line = LineString(mid)
    best = (0.0, None, None)
    for item in land.polygon_tree.query(line):
        i = nn._tree_idx(item, land.polygons)
        if i is None:
            continue
        poly = land.polygons[i]
        inter = line.intersection(poly)
        for g in getattr(inter, "geoms", [inter]):
            if getattr(g, "geom_type", "") != "LineString" or g.is_empty:
                continue
            cs = list(g.coords)
            chord = 0.0
            for (l1, a1), (l2, a2) in zip(cs[:-1], cs[1:]):
                _, _, d = geod.inv(l1, a1, l2, a2); chord += d / 1000.0
            if chord > best[0]:
                area = abs(geod.geometry_area_perimeter(poly)[0]) / 1e6
                pen = nn._headland_inland_penetration_km(g, poly, geod)
                best = (chord, area, pen)
    return best


def _pct(sorted_vals, q):
    if not sorted_vals:
        return float("nan")
    return sorted_vals[min(len(sorted_vals) - 1, int(q * (len(sorted_vals) - 1)))]


def phase1(land, geod, spots):
    print("=" * 78)
    print("PHASE 1 — FALSE POSITIVES + trim calibration (GSHHG only, no NOMADS)")
    print("=" * 78)
    print(f"constants under test: near_trim={nn.NODE_HEADLAND_NEAR_TRIM_KM} km  "
          f"end_trim={nn.NODE_HEADLAND_END_TRIM_KM} km  "
          f"area_floor={nn.NODE_HEADLAND_AREA_KM2} km2  "
          f"graze={nn.NODE_HEADLAND_OWN_GRAZE_KM} km")
    print(f"expected verdict: ZERO rejects across {len(spots)} placed spots\n")

    spot_in, node_in, rejects, inert = [], [], [], []
    trim_sum = nn.NODE_HEADLAND_NEAR_TRIM_KM + nn.NODE_HEADLAND_END_TRIM_KM
    for s in spots:
        p = (s["lat"], s["lng"])
        n = (s["nwps_node_lat"], s["nwps_node_lng"])
        d = nn._haversine_km(*p, *n)
        so, _ = _shore_offset_km(*p, land, geod)
        no, _ = _shore_offset_km(*n, land, geod)
        if so is not None:
            spot_in.append((so, s["name"], s.get("nwps_wfo")))
        if no is not None:
            node_in.append((no, s["name"], s.get("nwps_wfo")))
        if d <= trim_sum:
            inert.append((d, s["name"], s.get("nwps_wfo")))
            continue                      # chord fn returns 0.0 before querying land
        if nn._headland_verdict(p, n, land, **_node_scale_kw())["reject"]:
            chord, area, pen = _describe_crossing(p, n, land, geod)
            rejects.append((s["name"], s.get("nwps_wfo"), d, chord, area, pen))

    so_sorted = sorted(x[0] for x in spot_in)
    print("spot coordinate -> GSHHG shore, signed (+ = INLAND of the shoreline):")
    print(f"  p05 {_pct(so_sorted,.05)*1000:8.0f} m   median {_pct(so_sorted,.50)*1000:8.0f} m   "
          f"p95 {_pct(so_sorted,.95)*1000:8.0f} m   MAX {so_sorted[-1]*1000:8.0f} m")
    worst = sorted(spot_in, reverse=True)[:8]
    print("  the eight furthest INLAND (these are what the NEAR trim must clear):")
    for v, nm, w in worst:
        print(f"     {v*1000:8.0f} m  {w:4}  {nm}")
    need_near = max(0.0, so_sorted[-1])
    print(f"  => NEAR trim must exceed {need_near*1000:.0f} m; "
          f"it is {nn.NODE_HEADLAND_NEAR_TRIM_KM*1000:.0f} m  "
          f"[{'OK' if nn.NODE_HEADLAND_NEAR_TRIM_KM > need_near else 'TOO SMALL'}]\n")

    no_sorted = sorted(x[0] for x in node_in)
    inside = [x for x in node_in if x[0] > 0]
    print("model-wet node -> GSHHG shore, signed (+ = GSHHG says the node is ON LAND):")
    print(f"  median {_pct(no_sorted,.50)*1000:8.0f} m   p95 {_pct(no_sorted,.95)*1000:8.0f} m   "
          f"MAX {no_sorted[-1]*1000:8.0f} m")
    print(f"  nodes GSHHG places inside land: {len(inside)}")
    for v, nm, w in sorted(inside, reverse=True)[:8]:
        print(f"     {v*1000:8.0f} m  {w:4}  {nm}")
    need_end = max([0.0] + [x[0] for x in inside])
    print(f"  => END trim must exceed {need_end*1000:.0f} m; "
          f"it is {nn.NODE_HEADLAND_END_TRIM_KM*1000:.0f} m  "
          f"[{'OK' if nn.NODE_HEADLAND_END_TRIM_KM > need_end else 'TOO SMALL'}]\n")

    if inert:
        print(f"NOT TESTED — {len(inert)} path(s) shorter than the combined trim "
              f"({trim_sum*1000:.0f} m), where the chord fn returns 0.0 before querying land:")
        for d, nm, w in sorted(inert):
            print(f"     {d*1000:6.0f} m  {w:4}  {nm}")
        print()

    if rejects:
        print(f"FAIL — {len(rejects)} placed spot(s) REJECT their own baked node:")
        print(f"  {'spot':26}{'wfo':5}{'dist_km':>9}{'chord_km':>10}{'area_km2':>10}{'pen_km':>9}")
        for nm, w, d, ch, ar, pen in rejects:
            print(f"  {nm[:25]:26}{w or '':5}{d:9.2f}{ch:10.3f}"
                  f"{(ar if ar is not None else float('nan')):10.1f}"
                  f"{(pen if pen is not None else float('nan')):9.3f}")
        print("\n  Each of these is a FALSE POSITIVE: the guard would move a node that is already")
        print("  correct. Use the area/penetration columns to see WHICH constant is at fault.")
    else:
        print(f"PASS — 0 rejects across {len(spots) - len(inert)} tested paths.")
    return 0 if not rejects else 1


def phase3(land, geod, spots):
    print("=" * 78)
    print("PHASE 3 — graze ceiling over-rejection probe (GSHHG only, no NOMADS)")
    print("=" * 78)
    print("NODE_HEADLAND_OWN_GRAZE_KM = %.1f DISABLES the coast-parallel exclusion. On full-res"
          % nn.NODE_HEADLAND_OWN_GRAZE_KM)
    print("GSHHG the North American mainland is ONE polygon, so that exclusion is the normal")
    print("path. A node lying ALONG-SHORE rather than straight out can clip the spot's own beach.")
    print("expected verdict: these clear. Any reject means 0.0 is too strict.\n")

    # along-shore geometry = the bearing spot->node is far from the shore normal (orientation)
    cands = []
    for s in spots:
        o = s.get("orientation_deg")
        if o is None:
            continue
        b = nn._bearing(s["lat"], s["lng"], s["nwps_node_lat"], s["nwps_node_lng"])
        off = abs(((b - o + 180) % 360) - 180)
        if off >= 55.0:                    # well off the shore normal, still inside the +-90 filter
            cands.append((off, s))
    cands.sort(reverse=True, key=lambda x: x[0])
    print(f"{len(cands)} placed spot(s) whose node sits >=55 deg off the shore normal:\n")
    print(f"  {'spot':26}{'wfo':5}{'off_deg':>8}{'dist_km':>9}{'chord_km':>10}{'pen_km':>9}  verdict")
    bad, rejected_pens = 0, []
    for off, s in cands:
        p = (s["lat"], s["lng"]); n = (s["nwps_node_lat"], s["nwps_node_lng"])
        d = nn._haversine_km(*p, *n)
        v = nn._headland_verdict(p, n, land, **_node_scale_kw())
        ch, _, pen = _describe_crossing(p, n, land, geod)
        if v["reject"]:
            bad += 1
            if pen:
                rejected_pens.append(pen)
        print(f"  {s['name'][:25]:26}{s.get('nwps_wfo') or '':5}{off:8.0f}{d:9.2f}{ch:10.3f}"
              f"{(pen if pen is not None else float('nan')):9.3f}  "
              f"{'REJECT' if v['reject'] else 'clear'}")
    if bad:
        print(f"\nFAIL — {bad} along-shore node(s) rejected. The graze ceiling is too strict.")
        if rejected_pens:
            print(f"  measured clip penetration on the REJECTED paths: "
                  f"max {max(rejected_pens)*1000:.0f} m, min {min(rejected_pens)*1000:.0f} m.")
            print("  A positive NODE_HEADLAND_OWN_GRAZE_KM must sit ABOVE that max and BELOW the")
            print("  smallest phase-2 must-FLAG penetration — the same 2.15 / 4.14 shape that set")
            print("  the buoy-scale value. If those two ranges OVERLAP, penetration alone cannot")
            print("  separate them at node scale and the guard needs a different discriminator.")
    else:
        print(f"\nPASS — all {len(cands)} along-shore nodes clear at graze="
              f"{nn.NODE_HEADLAND_OWN_GRAZE_KM}.")
    return 0 if not bad else 1


def phase2(land, geod, spots, wfo):
    print("=" * 78)
    print(f"PHASE 2 — FALSE NEGATIVES on {wfo} (needs a CG1 cycle: Mac/NOMADS)")
    print("=" * 78)
    note = BARRIER_WFOS.get(wfo)
    print(f"why {wfo}: {note}\n" if note else
          f"NOTE: {wfo} is not in BARRIER_WFOS — expect nothing to flag here.\n")
    try:
        cycle = nn.load_cycle(wfo)
    except Exception as e:  # noqa: BLE001
        print(f"could not load a {wfo.upper()} cycle ({type(e).__name__}: {e}). "
              "Live NOMADS + cfgrib needed — run on the Mac.", file=sys.stderr)
        return 2
    far_cap = nn.grid_far_cap_km(cycle)
    print(f"grid spacing {nn.grid_spacing_km(cycle):.2f} km  far cap {far_cap:.2f} km  "
          f"cell area ~{nn.grid_spacing_km(cycle) ** 2:.2f} km2")
    print("(NOTE the cell area against NODE_HEADLAND_AREA_KM2 = "
          f"{nn.NODE_HEADLAND_AREA_KM2}: land smaller than one cell is invisible to NWPS.)\n")
    wet = nn._wet_nodes(cycle["lats"], cycle["lons"], cycle["mask"])
    mine = [s for s in spots if s.get("nwps_wfo") == wfo]
    print(f"{len(mine)} placed {wfo} spot(s); {len(wet)} wet cells in the grid\n")
    print(f"  {'spot':24}{'chosen_km':>10}{'nearer_blocked_km':>18}{'area_km2':>10}{'pen_km':>9}")
    found = 0
    for s in mine:
        p = (s["lat"], s["lng"])
        chosen = nn._haversine_km(*p, s["nwps_node_lat"], s["nwps_node_lng"])
        near = sorted(((nn._haversine_km(*p, c[0], c[1]), c) for c in wet
                       if nn._haversine_km(*p, c[0], c[1]) < min(chosen, far_cap)),
                      key=lambda x: x[0])
        hit = None
        for d, c in near:
            if d <= nn.NODE_HEADLAND_NEAR_TRIM_KM + nn.NODE_HEADLAND_END_TRIM_KM:
                continue
            if nn._headland_verdict(p, (c[0], c[1]), land, **_node_scale_kw())["reject"]:
                ch, ar, pen = _describe_crossing(p, (c[0], c[1]), land, geod)
                hit = (d, ar, pen)
                break
        if hit:
            found += 1
            print(f"  {s['name'][:23]:24}{chosen:10.2f}{hit[0]:18.2f}"
                  f"{(hit[1] if hit[1] is not None else float('nan')):10.1f}"
                  f"{(hit[2] if hit[2] is not None else float('nan')):9.3f}")
    if found:
        print(f"\n{found} must-FLAG case(s) on {wfo}. These are the guard working as intended: a")
        print("nearer wet cell with land in between, which nearest-first alone would have taken.")
        print("The area and penetration columns are the must-FLAG side of the calibration gap —")
        print("NODE_HEADLAND_AREA_KM2 must sit BELOW the smallest area and")
        print("NODE_HEADLAND_OWN_GRAZE_KM BELOW the smallest penetration.")
    else:
        print(f"\nNo must-FLAG case found on {wfo}. Either the barrier is not resolved at this")
        print("grid spacing, or no back-bay cell is nearer than the ocean cell here. That is a")
        print("real result, not a pass — the bar needs at least one FLAG somewhere in BARRIER_WFOS")
        print("or the guard has never been shown to fire on real coastline.")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase1", action="store_true", help="false positives + trim calibration")
    ap.add_argument("--phase2", action="store_true", help="false negatives (needs --wfo + NOMADS)")
    ap.add_argument("--phase3", action="store_true", help="graze over-rejection probe")
    ap.add_argument("--wfo", help="WFO for --phase2; see BARRIER_WFOS")
    a = ap.parse_args()
    if not (a.phase1 or a.phase2 or a.phase3):
        a.phase1 = a.phase3 = True          # the two that need no NOMADS
    land, geod, spots = _load_land(), _geod(), _placed_spots()
    print(f"GSHHG polygons: {len(land.polygons)}   placed spots: {len(spots)}\n")
    rc = 0
    if a.phase1:
        rc |= phase1(land, geod, spots); print()
    if a.phase3:
        rc |= phase3(land, geod, spots); print()
    if a.phase2:
        if not a.wfo:
            print("--phase2 needs --wfo (suggest: " + ", ".join(sorted(BARRIER_WFOS)) + ")",
                  file=sys.stderr)
            return 2
        rc |= phase2(land, geod, spots, a.wfo)
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
