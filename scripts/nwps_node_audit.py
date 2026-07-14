#!/usr/bin/env python3
"""NWPS trust-node blast-radius audit (Mac; needs NOMADS + NDBC metadata).

READ-ONLY DIAGNOSIS. Changes NOTHING: not the gate, not node selection, not a
verdict, not a tag. It answers one question across every placed NWPS zone at
once, so you don't re-run trust_check by hand per buoy:

    For each watched buoy, does the cell trust_check ACTUALLY samples
    (the plain-nearest wet cell, pipeline.forecast.nwps_nearshore._nearest_cell,
    line ~676 of that module) differ from the seaward-aware pick? And by how far?

Why it matters: the trust gate correlates model dirpw-vs-buoy MWD at the buoy's
PLAIN-NEAREST wet cell. When that cell sits SHOREWARD of the buoy (a landmask
shadow / sheltered, shallower cell), it is compared against an open-water buoy,
and near-shore refraction rotates the model direction — a systematic offset that
inflates circ_std and can FAIL a WFO whose model is actually fine (the 44095 /
mhx signature: +8..+16° consistent positive dir delta). Note the asymmetry the
gate lives with: the FORECAST samples each spot's node via select_node (seaward,
±90° of orientation_deg), but the TRUST CHECK samples the buoy's plain-nearest
cell — so a zone can be judged at a shoreward node it never actually forecasts on.

This tool reuses the gate's OWN primitives (_nearest_cell + _node_diag), so what
it reports is exactly what trust_check would sample — no re-implementation, no
drift. It prints, per buoy: plain-nearest cell, nearest-seaward cell, whether
they differ, and the distance + bearing between them. If nothing differs except
the known 44095, the blast radius is small (live PASSes stand). If live,
already-trusted zones differ, their PASS was taken at a shoreward node and is
suspect — list in hand to the human before any fix to node selection.

Default buoy set = every distinct (nwps_wfo, nwps_buoy_id) among the placed
swell_window_source=="nwps" spots in pipeline/spots_enriched.json (the live,
trusted zones). Add not-yet-placed candidates with --extra wfo:buoy,... .

    # audit every live zone, plus today's four FAIL candidates:
    python scripts/nwps_node_audit.py --extra mhx:44095,mhx:41025,ilm:41108,ilm:41110

    python scripts/nwps_node_audit.py --json          # machine-readable rows too
    python scripts/nwps_node_audit.py --selftest      # OFFLINE synthetic-grid check (no NOMADS)

Degrades cleanly offline: a buoy whose cycle/metadata can't be fetched prints a
one-line note and the audit continues. The grid/land-mask is static, so one
recent cycle per WFO is fetched and reused for every buoy in that WFO.
"""
from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_ROOT))
# load_cycle lazily imports cfgrib INSIDE the call, so importing the module is
# safe with no eccodes/NOMADS (mirrors the module's own --selftest).
from pipeline.forecast import nwps_nearshore as nn  # noqa: E402

ENRICHED = _ROOT / "pipeline" / "spots_enriched.json"


def watched_buoys_from_roster():
    """[(wfo, buoy_id, spot_count)] — the distinct nwps (wfo, buoy) pairs anchoring
    the placed spots. This IS the live/trusted-zone universe; ordering is stable."""
    if not ENRICHED.exists():
        return []
    d = json.loads(ENRICHED.read_text())
    c = collections.Counter()
    for s in d:
        if s.get("swell_window_source") == "nwps":
            c[(s.get("nwps_wfo"), s.get("nwps_buoy_id"))] += 1
    return [(w, b, n) for (w, b), n in
            sorted(c.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or ""))]


def audit_one(cyc, buoy_id, blat, blng, radius_km=5.0):
    """Read-only. Reproduce EXACTLY what trust_check samples for this buoy on this
    cycle (the plain-nearest wet cell) and compare it to the seaward-aware pick,
    using the gate's own _nearest_cell + _node_diag. Returns a dict:
        {plain: {lat,lng,dist_km,is_seaward}, seaward: {lat,lng,dist_km} | None,
         differs: bool, gap_km, gap_brg, n_within_radius, shore_brg, sea_brg,
         land_dist_km, reason}
    or None if the grid has no wet cell (can't happen on a real CG1 nest)."""
    cell = nn._nearest_cell(cyc, blat, blng)          # <- the gate's own selection
    if cell is None:
        return None
    i, j, dist = cell
    d = nn._node_diag(cyc, blat, blng, i, j, dist, radius_km=radius_km)
    gap_km = gap_brg = None
    if d.get("seaward_differs") and d.get("seaward_nearest_lat") is not None:
        # distance + bearing FROM the sampled (plain-nearest) cell TO the seaward pick
        gap_km = nn._haversine_km(d["lat"], d["lng"],
                                  d["seaward_nearest_lat"], d["seaward_nearest_lng"])
        gap_brg = nn._bearing(d["lat"], d["lng"],
                              d["seaward_nearest_lat"], d["seaward_nearest_lng"])
    return {
        "plain": {"lat": d["lat"], "lng": d["lng"], "dist_km": d["dist_km"],
                  "is_seaward": d["sampled_is_seaward"]},
        "seaward": ({"lat": d["seaward_nearest_lat"], "lng": d["seaward_nearest_lng"],
                     "dist_km": d["seaward_nearest_dist_km"]}
                    if d.get("seaward_nearest_lat") is not None else None),
        "differs": bool(d.get("seaward_differs")),
        "gap_km": gap_km, "gap_brg": gap_brg,
        "n_within_radius": d["n_within_radius"], "radius_km": d["radius_km"],
        "shore_brg": d["shore_bearing"], "sea_brg": d["seaward_bearing"],
        "land_dist_km": d["land_dist_km"], "reason": d["reason"],
    }


def _parse_pairs(spec):
    """'wfo:buoy,wfo:buoy' -> [(wfo, buoy)]. Raises on a malformed token."""
    out = []
    for tok in (spec or "").split(","):
        tok = tok.strip()
        if not tok:
            continue
        if ":" not in tok:
            raise SystemExit(f"--extra/--buoys token {tok!r} must be wfo:buoy (e.g. mhx:44095)")
        w, b = tok.split(":", 1)
        out.append((w.strip().lower(), b.strip()))
    return out


def _fmt(v, spec="{:.4f}", dash="  —  "):
    return spec.format(v) if isinstance(v, (int, float)) and v == v else dash


def run(extra=None, override=None, radius_km=5.0, as_json=False):
    if override:
        buoys = [(w, b, None) for (w, b) in override]
    else:
        buoys = watched_buoys_from_roster()
        live_keys = {(w, b) for (w, b, _) in buoys}
        for (w, b) in (extra or []):
            if (w, b) not in live_keys:
                buoys.append((w, b, None))   # candidate (not yet placed): spot_count None

    if not buoys:
        print("no buoys to audit (no placed nwps spots and no --extra/--buoys given).")
        return 0

    print("=== NWPS trust-node blast-radius audit (READ-ONLY; needs NOMADS+NDBC) ===")
    print("plain = the cell trust_check ACTUALLY samples (nearest wet to buoy); "
          "seaward = the seaward-aware pick it currently only PRINTS.\n")
    hdr = (f"{'wfo':<4} {'buoy':<7} {'spots':>5}  {'plain d_km':>10} {'sea?':>4}  "
           f"{'seaward d_km':>12} {'DIFFERS':>7}  {'gap_km':>7} {'gap_brg':>7}  {'≤rad':>4}")
    print(hdr)
    print("-" * len(hdr))

    cyc_cache = {}
    rows, errors = [], []
    n_differ_live = n_differ_extra = n_ok = 0
    for (wfo, buoy, nspots) in buoys:
        try:
            blat, blng = nn._buoy_latlng(buoy)
            if wfo not in cyc_cache:
                cyc_cache[wfo] = nn.load_cycle(wfo)   # latest cycle; grid/mask static
            res = audit_one(cyc_cache[wfo], buoy, blat, blng, radius_km=radius_km)
        except Exception as e:  # noqa: BLE001 — degrade per buoy; keep auditing the rest
            errors.append((wfo, buoy, f"{type(e).__name__}: {e}"))
            print(f"{wfo:<4} {buoy:<7} {(_fmt(nspots,'{:d}','   —')):>5}  "
                  f"⚠ skipped ({type(e).__name__}) — needs live NOMADS+NDBC on the Mac")
            continue
        if res is None:
            errors.append((wfo, buoy, "no wet cell in grid"))
            continue
        is_live = nspots is not None
        if res["differs"]:
            if is_live:
                n_differ_live += 1
            else:
                n_differ_extra += 1
        else:
            n_ok += 1
        sea_d = res["seaward"]["dist_km"] if res["seaward"] else float("nan")
        print(f"{wfo:<4} {buoy:<7} {(_fmt(nspots,'{:d}','   —')):>5}  "
              f"{_fmt(res['plain']['dist_km'],'{:.2f}'):>10} "
              f"{('Y' if res['plain']['is_seaward'] else 'N'):>4}  "
              f"{_fmt(sea_d,'{:.2f}'):>12} {('*** YES' if res['differs'] else 'no'):>7}  "
              f"{_fmt(res['gap_km'],'{:.2f}'):>7} {_fmt(res['gap_brg'],'{:.0f}°'):>7}  "
              f"{res['n_within_radius']:>4}")
        rows.append({"wfo": wfo, "buoy": buoy, "spots": nspots, "live": is_live, **res})

    # ---- per-divergence detail (the evidence for the at-risk zones) ----
    div = [r for r in rows if r["differs"]]
    if div:
        print("\n--- divergences (sampled node is NOT the seaward pick) ---")
        for r in div:
            tag = f"{r['spots']} live spots" if r["live"] else "candidate (unplaced)"
            print(f"  {r['wfo']}/{r['buoy']} [{tag}]: sampled {r['plain']['dist_km']:.2f} km "
                  f"({'seaward' if r['plain']['is_seaward'] else 'SHOREWARD'}) vs seaward "
                  f"{r['seaward']['dist_km']:.2f} km — {r['gap_km']:.2f} km apart @ {r['gap_brg']:.0f}° "
                  f"(shore brg {r['shore_brg']:.0f}°, seaward brg {r['sea_brg']:.0f}°, "
                  f"land {r['land_dist_km']:.1f} km, {r['n_within_radius']} wet cells ≤{r['radius_km']:.0f} km)")

    # ---- blast-radius summary + interpretation ----
    print("\n==== blast radius ====")
    audited = len(rows)
    print(f"  buoys audited: {audited}   consistent (no divergence): {n_ok}")
    print(f"  divergent LIVE/trusted zones:   {n_differ_live}"
          + ("  <-- their PASS was taken at a shoreward node; re-verify before trusting" if n_differ_live else ""))
    print(f"  divergent candidate zones:      {n_differ_extra}")
    if errors:
        print(f"  not assessed (offline/unknown): {len(errors)} "
              f"({', '.join(f'{w}/{b}' for w, b, _ in errors)})")
    if audited:
        if n_differ_live == 0:
            print("  → SMALL blast radius: every live zone samples a seaward-consistent node; "
                  "existing PASS verdicts stand. Any divergence is confined to candidates.")
        else:
            print("  → SYSTEMIC: one or more already-trusted zones were validated at a shoreward "
                  "node. Their PASS is unreliable — hold and re-run against the seaward node.")
    print("\n(Read-only: nothing was changed. Node selection is unchanged; decide the fix "
          "AFTER reading this table — see the diagnosis report.)")

    if as_json:
        print("\n--- JSON ---")
        print(json.dumps({"rows": rows,
                          "summary": {"audited": audited, "consistent": n_ok,
                                      "divergent_live": n_differ_live,
                                      "divergent_candidate": n_differ_extra,
                                      "not_assessed": [{"wfo": w, "buoy": b, "why": why}
                                                       for w, b, why in errors]}},
                         indent=2))
    return 0


def _selftest():
    """OFFLINE — proves the audit classifies both geometries with no NOMADS, reusing
    the gate's _nearest_cell + _node_diag on hand-built grids (same technique as
    nwps_nearshore._selftest). One masked cell is required for a seaward direction."""
    import numpy as np
    ok = True

    def check(msg, cond):
        nonlocal ok
        ok = ok and bool(cond)
        print(("  PASS " if cond else "  FAIL ") + msg)

    # 1) SHOREWARD-SHADOW grid: land to the north, the plain-nearest wet cell just
    #    north (shoreward) of the buoy, seaward wet cells to the south → must DIFFER.
    lat = np.array([[40.030], [40.008], [39.980], [39.960]])
    lng = np.array([[-73.000], [-73.000], [-73.000], [-73.000]])
    mask = np.array([[True], [False], [False], [False]])
    r = audit_one({"lats": lat, "lons": lng, "mask": mask}, "SHADOW", 40.000, -73.000)
    check("shadow grid: plain-nearest flagged SHOREWARD", r["plain"]["is_seaward"] is False)
    check("shadow grid: seaward pick DIFFERS", r["differs"] is True)
    check("shadow grid: seaward pick is farther than the sampled cell",
          r["seaward"]["dist_km"] > r["plain"]["dist_km"])
    check("shadow grid: inter-cell gap + bearing computed",
          r["gap_km"] is not None and r["gap_km"] > 0 and r["gap_brg"] is not None)

    # 2) OPEN-WATER grid: land far north, the plain-nearest wet cell already SEAWARD
    #    (just south of the buoy) → must NOT differ (the common, healthy case).
    lat2 = np.array([[40.050], [39.990], [39.970], [39.950]])
    lng2 = np.array([[-73.000], [-73.000], [-73.000], [-73.000]])
    mask2 = np.array([[True], [False], [False], [False]])
    r2 = audit_one({"lats": lat2, "lons": lng2, "mask": mask2}, "OPEN", 40.000, -73.000)
    check("open-water grid: plain-nearest already SEAWARD", r2["plain"]["is_seaward"] is True)
    check("open-water grid: no seaward divergence", r2["differs"] is False)
    check("open-water grid: no inter-cell gap reported", r2["gap_km"] is None)

    # 3) roster reader returns well-formed rows if spots_enriched.json is present
    rb = watched_buoys_from_roster()
    check(f"roster reader returns (wfo,buoy,count) tuples ({len(rb)} live pairs)",
          all(isinstance(t, tuple) and len(t) == 3 for t in rb))

    print("\nself-test:", "ALL PASS — audit logic sound (offline)." if ok else "FAILURES")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--extra", default=None,
                    help="add candidate buoys not yet placed, wfo:buoy,... (e.g. mhx:44095,ilm:41108)")
    ap.add_argument("--buoys", default=None,
                    help="override the roster-derived set entirely, wfo:buoy,... ")
    ap.add_argument("--radius", type=float, default=5.0,
                    help="clutter radius km for the wet-cell count (default 5)")
    ap.add_argument("--json", action="store_true", help="also emit machine-readable rows")
    ap.add_argument("--selftest", action="store_true", help="offline synthetic-grid check (no NOMADS)")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    return run(extra=_parse_pairs(a.extra), override=_parse_pairs(a.buoys) or None,
               radius_km=a.radius, as_json=a.json)


if __name__ == "__main__":
    raise SystemExit(main())
