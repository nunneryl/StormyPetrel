#!/usr/bin/env python3
"""Why placement does NO land-crossing check at node scale — the record, and the evidence.

FINAL FINDING. Two land-crossing guards were built for select_node and BOTH were dropped after
being tested against real coastline across SEVEN WFOs (mfl, mhx, okx, phi, hgx, bro, box).
Neither is needed: select_node's existing SEAWARD HALF-PLANE rule already prevents the failure
they were built for. A cell behind a barrier is landward of the shore normal, so the +-90 filter
drops it before distance is ever considered.

  GSHHG polygon check (the --find-buoy _headland_verdict, re-scaled): 23 FALSE POSITIVES across
  the seven, penetrations 24-133 m, mostly against the single North America polygon, plus
  Martha's Vineyard (243.2 km2) and Nantucket (132.0 km2) clipping their own shorelines. box
  alone would have lost 17 correctly-placed spots. No setting of its four constants is both safe
  and useful:
    * phase 1 on the 491 placed spots: 36+ rejected their own correct baked node.
    * phase 3: 28 along-shore nodes rejected, clip penetration 2 m to 396 m.
    * the AREA floor cannot filter. The crossed polygon reads 20,154,740 km2 over and over --
      full-res GSHHG holds North America as ONE polygon. A PER-GRID floor of one cell (1.00 km2
      at phi up to 9.30 at lox) removes only 2 of the 9 areas actually crossed: 0.6 km2
      everywhere and 4.4 km2 on coarse grids. 26.8, 29.4, 55.2, 107, 132, 243 km2 and the
      continent survive on EVERY grid.
    * PENETRATION cannot separate. must-CLEAR reaches 396 m; must-FLAG for a barrier of width W
      is ~W/2, so it only clears 396 m once W > 0.79 km -- and a barrier that wide is about one
      grid cell, which is exactly when the model's own mask already marks it land. The band where
      GSHHG could add anything is the band where its threshold is unsettable.

  MODEL-MASK walk (no tuned constant at all -- sample the spot->cell segment against the NWPS
  land mask): moved ZERO nodes on all seven WFOs, INCLUDING mhx, where Hatteras is a resolved
  ~1 km barrier with open Pamlico Sound behind it. That was the strongest candidate geometry in
  the set and it stayed silent, because the half-plane rule had already excluded every landward
  cell before the walk ran.

CONSEQUENCE FOR ANYONE CHANGING select_node: the +-90 seaward filter is LOAD-BEARING. It is not
a redundant heuristic sitting in front of a real check -- it IS the check. Weaken it and this
conclusion no longer holds; re-run this script before assuming otherwise.

This script is kept as that record and as the harness to re-run. READ-ONLY: it loads
spots_enriched.json, the assignments file and GSHHG, and writes nothing except its own JSON
summary with --json.

  phase 1  the GSHHG mechanism's false positives on the 491 placed spots, plus the two trim
           distributions a trim-based approach would have needed (spot -> GSHHG shore signed,
           and node -> GSHHG shore incl. model-wet nodes GSHHG places inside land). GSHHG only,
           no NOMADS. Both distributions are repeated in a trailing SUMMARY block so a piped
           `tail` still shows them.
  phase 2  per-WFO counterfactual: for every spot the WFO will decide for -- INCLUDING the ones
           not yet placed, which is the whole point on mfl -- what select_node picks today and
           what the GSHHG mechanism would have rejected, with the crossed polygon's area (at the
           per-grid one-cell floor) and the crossing's inland penetration. Needs a CG1 cycle.
  phase 3  over-rejection probe: placed spots whose node sits >=55 deg off the shore normal.

Run:
  python3 scripts/node_headland_calibrate.py --phase1 --phase3 --json
  python3 scripts/node_headland_calibrate.py --phase2 --wfo mfl
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from pipeline.forecast import nwps_nearshore as nn   # noqa: E402

ENRICHED = Path(__file__).resolve().parents[1] / "pipeline" / "spots_enriched.json"
SUMMARY = {}          # machine-readable results; written by --json

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


# The GSHHG node-scale settings under measurement. They deliberately do NOT live in
# nwps_nearshore: placement carries no land-crossing check at all, so these belong to the record
# of the rejected mechanism, not to production. They stay HERE so the counterfactual remains
# reproducible and a future reader can re-derive the finding rather than take it on trust.
# Phase 2 substitutes the PER-GRID area floor (_one_cell_km2 — one grid cell, below which NWPS
# cannot see the land at all) for the fixed value, which is the strongest form the area test can
# take; the docstring records that even so it removes only 2 of the 9 areas actually crossed.
GSHHG_NEAR_TRIM_KM = 0.10
GSHHG_END_TRIM_KM = 0.10
GSHHG_AREA_KM2 = 0.5
GSHHG_OWN_GRAZE_KM = 0.0


def _one_cell_km2(cycle):
    """Per-grid land-area floor = ONE grid cell = grid_spacing_km(cycle)**2 (phi 1.0 km -> 1.00
    km2; okx/akq 1.80 -> 3.24; box 1.99 -> 3.96; mtr 2.48 -> 6.15; gyx 2.49 -> 6.20; lox ~3.05 ->
    9.30), derived from the grid the way grid_far_cap_km derives the placement cap. Lives here
    rather than in nwps_nearshore because nothing in production uses it."""
    sp = nn.grid_spacing_km(cycle)
    return sp * sp if sp > 0 else nn.FAR_CAP_FLOOR_KM ** 2


def _node_scale_kw(area_km2=None):
    return dict(max_km=float("inf"),
                near_trim_km=GSHHG_NEAR_TRIM_KM,
                end_trim_km=GSHHG_END_TRIM_KM,
                area_km2=GSHHG_AREA_KM2 if area_km2 is None else area_km2,
                own_graze_km=GSHHG_OWN_GRAZE_KM)


def _describe_crossing(spot, node, land, geod):
    """(chord_km, area_km2, penetration_km) of the LONGEST land crossing on the trimmed
    spot->node path — measured directly rather than read off _headland_land_chord_km's detail
    out-param, which only populates own_penetration_km when the crossed polygon happens to be the
    node's own landmass AND the graze branch runs. Penetration is the number that sets
    the graze ceiling, so it has to be reported for EVERY crossing, not just some.
    Mirrors the chord function's densify/trim so the geometry described is the geometry judged."""
    from shapely.geometry import LineString
    (sla, sln), (nla, nln) = spot, node
    _, _, dist_m = geod.inv(sln, sla, nln, nla)
    total_km = dist_m / 1000.0
    near, end = GSHHG_NEAR_TRIM_KM, GSHHG_END_TRIM_KM
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
    print(f"constants under test: near_trim={GSHHG_NEAR_TRIM_KM} km  "
          f"end_trim={GSHHG_END_TRIM_KM} km  "
          f"area_floor={GSHHG_AREA_KM2} km2  "
          f"graze={GSHHG_OWN_GRAZE_KM} km")
    print(f"expected verdict: ZERO rejects across {len(spots)} placed spots\n")

    spot_in, node_in, rejects, inert = [], [], [], []
    trim_sum = GSHHG_NEAR_TRIM_KM + GSHHG_END_TRIM_KM
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
          f"it is {GSHHG_NEAR_TRIM_KM*1000:.0f} m  "
          f"[{'OK' if GSHHG_NEAR_TRIM_KM > need_near else 'TOO SMALL'}]\n")

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
          f"it is {GSHHG_END_TRIM_KM*1000:.0f} m  "
          f"[{'OK' if GSHHG_END_TRIM_KM > need_end else 'TOO SMALL'}]\n")

    if inert:
        print(f"NOT TESTED — {len(inert)} path(s) shorter than the combined trim "
              f"({trim_sum*1000:.0f} m), where the chord fn returns 0.0 before querying land:")
        for d, nm, w in sorted(inert):
            print(f"     {d*1000:6.0f} m  {w:4}  {nm}")
        print()

    SUMMARY["phase1"] = {
        "n_spots": len(spots), "n_tested": len(spots) - len(inert), "n_rejects": len(rejects),
        "spot_inland_m": {"p50": _pct(so_sorted, .50) * 1000, "p95": _pct(so_sorted, .95) * 1000,
                          "max": so_sorted[-1] * 1000,
                          "worst": [{"m": v * 1000, "wfo": w, "spot": nm} for v, nm, w in worst]},
        "node_inside_land_m": {"count": len(inside), "max": need_end * 1000,
                               "worst": [{"m": v * 1000, "wfo": w, "spot": nm}
                                         for v, nm, w in sorted(inside, reverse=True)[:8]]},
        "near_trim_needed_m": need_near * 1000, "end_trim_needed_m": need_end * 1000,
        "untested_short_paths": [{"m": d * 1000, "wfo": w, "spot": nm} for d, nm, w in sorted(inert)],
        "rejects": [{"spot": nm, "wfo": w, "dist_km": d, "chord_km": ch,
                     "area_km2": ar, "pen_km": pen} for nm, w, d, ch, ar, pen in rejects],
    }
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
    # repeated at the END so a piped `tail` still catches the two numbers that matter
    print("\n" + "-" * 78)
    print("PHASE 1 SUMMARY (repeated here so `| tail` does not cut it off)")
    print(f"  spots {len(spots)}   tested {len(spots) - len(inert)}   REJECTS {len(rejects)}")
    print(f"  spot inland of GSHHG shore:  p50 {_pct(so_sorted,.50)*1000:.0f} m   "
          f"p95 {_pct(so_sorted,.95)*1000:.0f} m   MAX {so_sorted[-1]*1000:.0f} m")
    print(f"  model-wet nodes GSHHG calls land: {len(inside)}   deepest {need_end*1000:.0f} m")
    print(f"  => a near trim would need > {need_near*1000:.0f} m; an end trim > {need_end*1000:.0f} m")
    print("-" * 78)
    return 0 if not rejects else 1


def phase3(land, geod, spots):
    print("=" * 78)
    print("PHASE 3 — graze ceiling over-rejection probe (GSHHG only, no NOMADS)")
    print("=" * 78)
    print("the graze ceiling = %.1f DISABLES the coast-parallel exclusion. On full-res"
          % GSHHG_OWN_GRAZE_KM)
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
            print("  A positive the graze ceiling must sit ABOVE that max and BELOW the")
            print("  smallest phase-2 must-FLAG penetration — the same 2.15 / 4.14 shape that set")
            print("  the buoy-scale value. If those two ranges OVERLAP, penetration alone cannot")
            print("  separate them at node scale and the guard needs a different discriminator.")
    else:
        print(f"\nPASS — all {len(cands)} along-shore nodes clear at graze="
              f"{GSHHG_OWN_GRAZE_KM}.")
    return 0 if not bad else 1


def _wfo_roster(wfo, placed):
    """Every spot this WFO will decide for: the ones already PLACED (with a baked node) plus the
    ones AWAITING placement, resolved from the assignments file's pending[]/unverifiable[] slugs
    back to their lat/lng in spots_enriched.json. The awaiting set is the whole point on mfl —
    all 22 of its spots are unplaced, so a placed-only roster iterates ZERO of them."""
    doc = json.loads((Path(__file__).resolve().parents[1] / "scripts" /
                      "nwps_okx_assignments.json").read_text())
    applied = {s.get("slug") for s in doc["spots"]}
    todo = []
    for r in (doc["buoy_reference"].get("pending") or []) + \
             (doc["buoy_reference"].get("unverifiable") or []):
        if r.get("wfo") == wfo:
            todo += [s for s in (r.get("slugs") or []) if s not in applied]
    by_slug = {nn._slug(s.get("name", "")): s for s in json.loads(ENRICHED.read_text())}
    out = [dict(s, _state="placed") for s in placed if s.get("nwps_wfo") == wfo]
    seen = {nn._slug(s.get("name", "")) for s in out}
    for slug in todo:
        s = by_slug.get(slug)
        if s and s.get("lat") is not None and slug not in seen:
            out.append(dict(s, _state="awaiting"))
    return out


def phase2(land, geod, placed, wfo):
    print("=" * 78)
    print(f"PHASE 2 — must-FLAG side + mask-vs-GSHHG head-to-head on {wfo} (needs a CG1 cycle)")
    print("=" * 78)
    note = BARRIER_WFOS.get(wfo)
    print(f"why {wfo}: {note}\n" if note else
          f"NOTE: {wfo} is not in BARRIER_WFOS — expect nothing to flag here.\n")
    roster = _wfo_roster(wfo, placed)
    n_await = sum(1 for s in roster if s["_state"] == "awaiting")
    print(f"roster: {len(roster)} spot(s) — {len(roster) - n_await} placed, {n_await} AWAITING "
          "placement (the ones this guard will actually decide for)")
    if not roster:
        print(f"nothing to test on {wfo}.")
        return 0
    try:
        cycle = nn.load_cycle(wfo)
    except Exception as e:  # noqa: BLE001
        print(f"could not load a {wfo.upper()} cycle ({type(e).__name__}: {e}). "
              "Live NOMADS + cfgrib needed — run on the Mac.", file=sys.stderr)
        return 2
    spacing = nn.grid_spacing_km(cycle)
    per_grid_area = _one_cell_km2(cycle)
    print(f"grid spacing {spacing:.2f} km   far cap {nn.grid_far_cap_km(cycle):.2f} km   "
          f"one cell = {per_grid_area:.2f} km2")
    print(f"(GSHHG column uses the PER-GRID area floor {per_grid_area:.2f} km2, not the old "
          f"fixed {GSHHG_AREA_KM2} — land below one cell is invisible to NWPS.)\n")

    print(f"  {'spot':24}{'st':>4}{'node_km':>9}{'brg_off':>9}  {'gshhg':>7}"
          f"{'area_km2':>10}{'pen_km':>9}")
    gshhg_rejects = []
    for s in roster:
        p = (s["lat"], s["lng"])
        o = s.get("orientation_deg")
        sel = nn.select_node(cycle, p[0], p[1], o)
        if sel is None:
            print(f"  {s['name'][:23]:24}{s['_state'][:4]:>4}   no wet cell in grid")
            continue
        node = (sel[2], sel[3])
        off = (abs(((nn._bearing(p[0], p[1], *node) - o + 180) % 360) - 180)
               if o is not None else float("nan"))
        gv = nn._headland_verdict(p, node, land,
                                  **_node_scale_kw(area_km2=per_grid_area))["reject"]
        ch, ar, pen = _describe_crossing(p, node, land, geod)
        print(f"  {s['name'][:23]:24}{s['_state'][:4]:>4}{sel[4]:9.2f}{off:9.0f}  "
              f"{'REJECT' if gv else 'clear':>7}"
              f"{(ar if ar is not None else float('nan')):10.1f}"
              f"{(pen if pen is not None else float('nan')):9.3f}")
        if gv:
            gshhg_rejects.append((s['name'], s['_state'], pen, ar))

    print(f"\nselect_node's half-plane rule chose all {len(roster)} of these. The GSHHG mechanism")
    print(f"would have REJECTED {len(gshhg_rejects)} of them — every one a false positive, since the")
    print("chosen node is the correct seaward cell:")
    for nm, st, pen, ar in gshhg_rejects:
        print(f"    {nm}  [{st}]  polygon {ar if ar else float('nan'):.1f} km2, "
              f"penetration {pen*1000 if pen else float('nan'):.0f} m")
    moved = []
    if not gshhg_rejects:
        print("    (none)")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--phase1", action="store_true", help="false positives + trim calibration")
    ap.add_argument("--phase2", action="store_true", help="false negatives (needs --wfo + NOMADS)")
    ap.add_argument("--phase3", action="store_true", help="graze over-rejection probe")
    ap.add_argument("--wfo", help="WFO for --phase2; see BARRIER_WFOS")
    ap.add_argument("--json", metavar="PATH", nargs="?", const="node_headland_calibrate_out.json",
                    help="also write the numbers to JSON (default: ./node_headland_calibrate_out.json)")
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
    if a.json:
        Path(a.json).write_text(json.dumps(SUMMARY, indent=2))
        print(f"\nwrote {a.json} — the distributions in machine-readable form.")
    return rc


if __name__ == "__main__":
    raise SystemExit(main())
