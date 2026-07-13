"""SW-1 swell-window raycast runner (Phase 1).

Two modes, both run against the **real GSHHG L1** land mask (downloaded by
`pipeline/download_geodata.sh`), which is why this lives in a dedicated
GitHub Actions job rather than the normal pipeline — the runner has the open
egress to fetch GSHHG and the headroom to run longer.

  validate  — cast ONLY the 5 Phase-0 spots and print, per spot: window WIDTH,
              the all-islands-removed CEILING, the open arcs, and
              optimal_swell_dir. Writes nothing. This is the gate that must
              pass (thresholds re-checked against GSHHG) before a full run.

  full      — cast the whole roster with the Phase-0 blocker-geometry fix and
              the perf path (4° / 90 rays, prepared geometries, per-spot bbox
              pre-clip, multiprocessing). Writes swell_window_arcs +
              optimal_swell_dir + swell_window_source="raycast" for spots the
              raycast solves; leaves the orientation-derived fallback for spots
              it can't. orientation_deg is never touched. The workflow commits
              the updated spots_enriched.json on a review branch (never main).

CLI:
    python -m pipeline.sw1_raycast --mode validate
    python -m pipeline.sw1_raycast --mode full [--workers N] [--input ...] [--output ...]
"""
from __future__ import annotations

import argparse
import json
import logging
import multiprocessing as mp
import os
import statistics
import time
from pathlib import Path

from .config import DEFAULT_ENRICHED_OUTPUT
from .enrichment import swell_window as sw
from .enrichment.geodata import load_land_index
from .enrichment.swell_window_fallback import compute_swell_window_fallback

log = logging.getLogger("pipeline.sw1_raycast")

# Production roster step — 90 rays at 4° (perf plan). The 5-spot gate uses the
# same step so its numbers are a faithful preview of the full run.
RUN_STEP_DEG = 4

# The Phase-0 validation set. Coordinates are the algo coords from
# spots_enriched.json; targets are window WIDTHS (degrees).
VALIDATION_SPOTS = [
    # name, lat, lng, normal, target_lo, target_hi, note
    ("Huntington Beach", 33.640633, -117.986298, 220, 150, 180, "wide open SSW–W"),
    ("Malibu Surfrider", 34.03143, -118.688865, 184, 100, 140, "partial Channel Is. block"),
    ("Rincon (CA)", 34.371814, -119.478507, 210, 80, 100, "W–NW window"),
    ("Blacks Beach", 32.879677, -117.252982, 263, 140, 170, "open SW–WNW"),
    ("Second Beach (RI)", 41.48582, -71.254406, 209, 80, None, "widest RI window, S–SSE"),
]


def _fmt_arcs(arcs: list[dict]) -> str:
    return ", ".join(f"{a['min']}–{a['max']}({a['span']})" for a in arcs) or "(none)"


def _chain_member_bearings(lo, hi, step):
    """Bearings that make up a chain [lo, hi] (handles the 0/360 wrap)."""
    if lo <= hi:
        return list(range(lo, hi + 1, step))
    return list(range(lo, 360, step)) + list(range(0, hi + 1, step))


def _print_blocker_detail(name, lat, lng, normal, hard_sorted, debug_rays, debug_chains, step) -> None:
    """Per-spot culprit dump for --debug-blockers. Reads only what the classifier
    already recorded; writes nothing, changes no threshold. HARD rays explain the
    ceiling; ISLAND chains explain any mid-window split."""
    print(f"\n── blockers: {name} ({lat:.4f},{lng:.4f})  normal={normal}°  step={step}° ──")
    print(f"   thresholds: local-coast ≤{sw.SWELL_LOCAL_LANDMASS_KM:.0f}km · area ≥{sw.SWELL_BLOCKER_AREA_KM2:.0f}km²"
          f" · subtend ≥{sw.SWELL_MIN_SHADOW_DEG:.0f}° · wrap {sw.SWELL_DIFFRACTION_WRAP_DEG:.0f}°"
          f"+{sw.SWELL_DIFFRACTION_WRAP_PER_100KM:.0f}°/100km")
    n_local = sum(1 for b in hard_sorted if debug_rays[b].get("rule") == "local_coast_30km")
    n_solid = sum(1 for b in hard_sorted if debug_rays[b].get("rule") == "mainland_solid")
    ceil = (360 // step - len(hard_sorted)) * step
    print(f"   HARD-blocked {len(hard_sorted)} rays → ceiling {ceil}°  (local-coast {n_local}, "
          f"mainland-solid {n_solid})  — bearing / rule / area / dist / centroid:")
    for b in hard_sorted:
        r = debug_rays[b]
        print(f"     b={b:3d}  {r['rule']:18} area≈{r['area_km2']:>11.0f} km²  dist={r['dist_km']:6.1f} km  "
              f"centroid({r['centroid'][0]:.3f},{r['centroid'][1]:.3f})")
    if debug_chains:
        print(f"   ISLAND chains {len(debug_chains)} — subtend / nearest dist / wrap-in / decision:")
        for c in debug_chains:
            if c["decision"] == "open_subtend":
                print(f"     chain {c['lo']:3d}–{c['hi']:3d} (w={c['width']:>3}°)  → OPEN      [{c['reason']}]")
                continue
            tail = "" if c["decision"] == "open_wrapped" else \
                f" core {'–'.join(map(str, c['core'])) if c.get('core') else '(none)'}"
            verb = "OPEN" if c["decision"] == "open_wrapped" else "BLOCKED"
            print(f"     chain {c['lo']:3d}–{c['hi']:3d} (w={c['width']:>3}°)  dmin={c['dmin_km']:5.0f}km  "
                  f"wrap={c['wrap_deg']:4.1f}°  → {verb}{tail}   [{c['reason']}]")
            # member composition: is this ONE landmass or near+far fused across a bay mouth?
            # Reads the per-bearing 'small' records — area/centroid identify mainland vs island.
            members = [debug_rays[b] for b in _chain_member_bearings(c["lo"], c["hi"], step)
                       if debug_rays.get(b, {}).get("result") == "small"]
            if members:
                dists = [m["dist_km"] for m in members]
                near = min(members, key=lambda m: m["dist_km"])
                far = max(members, key=lambda m: m["dist_km"])
                fused = "  ← NEAR+FAR fused (one wrap from dmin over both)" \
                    if max(dists) > 3 * max(1.0, min(dists)) else ""
                print(f"        members {len(members)}: dist {min(dists):.0f}–{max(dists):.0f}km · "
                      f"near {near['area_km2']:.0f}km²@({near['centroid'][0]:.1f},{near['centroid'][1]:.1f}) · "
                      f"far {far['area_km2']:.0f}km²@({far['centroid'][0]:.1f},{far['centroid'][1]:.1f}){fused}")
    else:
        print("   ISLAND chains 0 — no sub-threshold island shadows (every non-hard bearing is fully open).")


# Diagnostic bucketing distance cutoff for the A/B/C split — a REPORTING boundary
# only, NOT a model threshold (it changes nothing the raycast computes).
_DISTANT_MAINLAND_KM = 100.0


def _classify_hard(rec) -> str:
    """Bucket one HARD-blocked bearing: A) own-coast near field (the spot's own
    landmass within the local-coast guard), B) distant-mainland (a ≥SWELL_BLOCKER_AREA_KM2
    polygon hit beyond _DISTANT_MAINLAND_KM — the SWELL_MIN_FETCH_KM downrange clip),
    C) island / other."""
    dist = rec.get("dist_km", 0.0)
    if rec.get("own") and dist <= sw.SWELL_LOCAL_LANDMASS_KM:
        return "A"
    if rec.get("area_km2", 0.0) >= sw.SWELL_BLOCKER_AREA_KM2 and dist > _DISTANT_MAINLAND_KM:
        return "B"
    return "C"


def _fmt_ranges(bearings, step) -> str:
    """Collapse bearings into compact contiguous 'lo-hi' ranges."""
    bs = sorted(bearings)
    if not bs:
        return "—"
    out, lo, prev = [], bs[0], bs[0]
    for b in bs[1:]:
        if b - prev == step:
            prev = b
        else:
            out.append(f"{lo}-{prev}" if lo != prev else f"{lo}")
            lo = prev = b
    out.append(f"{lo}-{prev}" if lo != prev else f"{lo}")
    return ",".join(out)


def _print_bucket_summary(name, debug_rays, small_blocked_sorted, open_b, step) -> None:
    """Classify every BLOCKED bearing into A) own-coast near field, B) distant-mainland
    min-fetch clip, C) island/other, and print per-spot counts / ranges / degrees, plus a
    read-only what-if window width if bucket B were treated as open. Changes no threshold,
    writes nothing (it only re-reads what the classifier already recorded)."""
    buckets: dict[str, list[int]] = {"A": [], "B": [], "C": []}
    for b in sorted(debug_rays):
        if debug_rays[b].get("result") == "hard":
            buckets[_classify_hard(debug_rays[b])].append(b)
    buckets["C"] = sorted(set(buckets["C"]) | set(small_blocked_sorted))  # island-chain shadows → C

    labels = {
        "A": f"own-coast near field  (≤{sw.SWELL_LOCAL_LANDMASS_KM:.0f}km, spot's own landmass)",
        "B": f"distant-mainland      (≥{sw.SWELL_BLOCKER_AREA_KM2:.0f}km², >{_DISTANT_MAINLAND_KM:.0f}km · min-fetch clip)",
        "C": "island / other",
    }
    print(f"   ── A/B/C blocker buckets — {name} ──")
    for k in ("A", "B", "C"):
        n = len(buckets[k])
        print(f"     {k}) {labels[k]:52} {n:3d} rays · {n * step:3d}° blocked  [{_fmt_ranges(buckets[k], step)}]")

    # read-only what-if: window width if bucket B (distant-mainland min-fetch) were open
    cur_arcs = sw._merge_open_arcs(sorted(open_b), step)
    cur_w = sum(a["span"] for a in cur_arcs)
    wif_arcs = sw._merge_open_arcs(sorted(set(open_b) | set(buckets["B"])), step)
    wif_w = sum(a["span"] for a in wif_arcs)
    print(f"     what-if: width {cur_w}° → {wif_w}°  (+{wif_w - cur_w}°) if bucket B were open "
          f"— SWELL_MIN_FETCH_KM unchanged, read-only estimate")
    print(f"        current arcs: [{_fmt_arcs(cur_arcs)}]")
    print(f"        B-open  arcs: [{_fmt_arcs(wif_arcs)}]")


def run_validate(debug_blockers: bool = False) -> int:
    """Cast the 5 gate spots against GSHHG and print diagnostics. No writes.

    *debug_blockers* (validate-only): after the summary table, dump the per-ray
    culprit list for each spot — for every hard-blocked bearing the rule
    (local-coast 30 km / area filter 500 km²) plus the blocking polygon's area,
    distance and centroid; and for every small-island chain the subtend, nearest
    distance, wrap-in and whether it stayed BLOCKED (wrap-distance) or opened
    (subtend cutoff / fully wrapped). Then an A/B/C blocker-bucket summary per spot
    (own-coast near field / distant-mainland min-fetch clip / island-other) with a
    read-only what-if window width if bucket B were open. Writes nothing, tunes nothing."""
    land = load_land_index()
    if land is None:
        log.error("GSHHG land index unavailable — cannot validate. Did download_geodata.sh run?")
        return 1
    log.info("GSHHG loaded: %d polygons. Casting 5 gate spots at %d° (%d rays).%s",
             len(land.polygons), RUN_STEP_DEG, 360 // RUN_STEP_DEG,
             "  [--debug-blockers ON]" if debug_blockers else "")

    print(f"\n{'spot':20}{'width':>6}{'ceil':>6}{'target':>9}{'opt':>5}{'nrm':>5}  source    arcs")
    print("-" * 96)
    detail: list[tuple] = []
    for name, lat, lng, normal, tlo, thi, note in VALIDATION_SPOTS:
        local = sw.local_land_index(land, lat, lng)
        debug_rays = {} if debug_blockers else None
        debug_chains: list | None = [] if debug_blockers else None
        hard, small = sw._classify_bearings(lat, lng, local, RUN_STEP_DEG, debug=debug_rays)
        small_blocked = sw._island_shadow(small, RUN_STEP_DEG, debug=debug_chains)
        open_b = [b for b in range(0, 360, RUN_STEP_DEG)
                  if b not in hard and b not in small_blocked]
        ceil_b = [b for b in range(0, 360, RUN_STEP_DEG) if b not in hard]
        arcs = sw._merge_open_arcs(open_b, RUN_STEP_DEG)
        optimal = sw._open_window_center(open_b)
        width = sum(a["span"] for a in arcs)
        ceil = len(ceil_b) * RUN_STEP_DEG
        source = "raycast" if arcs else "(empty→fallback)"
        tgt = f"{tlo}-{'+' if thi is None else thi}"
        print(f"{name:20}{width:6d}{ceil:6d}{tgt:>9}{str(optimal):>5}{normal:>5}  {source:9} [{_fmt_arcs(arcs)}]")
        if debug_blockers:
            detail.append((name, lat, lng, normal, sorted(hard), debug_rays, debug_chains,
                           sorted(small_blocked), open_b))
    print("-" * 96)
    print("width = open window (sum of arc spans); ceil = all-islands-removed ceiling.")
    print("If a number is off, tune the §3 thresholds against GSHHG and re-run validate —")
    print("do NOT loosen the 30 km local-coast guard (Second Beach RI must stay bounded).")
    if debug_blockers:
        for name, lat, lng, normal, hard_sorted, dbg_rays, dbg_chains, sb_sorted, ob in detail:
            _print_blocker_detail(name, lat, lng, normal, hard_sorted, dbg_rays, dbg_chains, RUN_STEP_DEG)
            _print_bucket_summary(name, dbg_rays, sb_sorted, ob, RUN_STEP_DEG)
    return 0


def run_sweep_mainland_solid(values) -> int:
    """Sweep SWELL_MAINLAND_SOLID_KM over *values* (km) and print the open-window WIDTH per
    gate spot in ONE run — a sensitivity table for picking the value. Read-only: each value
    is passed per call to _classify_bearings, so the committed default is never mutated and
    spots_enriched.json is never written."""
    land = load_land_index()
    if land is None:
        log.error("GSHHG land index unavailable — cannot sweep. Did download_geodata.sh run?")
        return 1
    default = sw.SWELL_MAINLAND_SOLID_KM
    log.info("GSHHG loaded: %d polygons. Sweeping SWELL_MAINLAND_SOLID_KM over %s km (committed default %g).",
             len(land.polygons), ",".join(f"{v:g}" for v in values), default)

    labels = ["Hunt", "Malibu", "Rincon", "Blacks", "RI"]   # aligned to VALIDATION_SPOTS order
    cols = []   # (lat, lng, header) per spot
    for (name, lat, lng, normal, tlo, thi, note), lbl in zip(VALIDATION_SPOTS, labels):
        cols.append((lat, lng, f"{lbl}({tlo}-{'+' if thi is None else thi})"))

    # width[value][spot] — cast each spot once per value (local index reused across values;
    # only the hard/small split depends on the swept SWELL_MAINLAND_SOLID_KM).
    matrix: dict[float, list[int]] = {v: [] for v in values}
    for lat, lng, _lbl in cols:
        local = sw.local_land_index(land, lat, lng)
        for v in values:
            hard, small = sw._classify_bearings(lat, lng, local, RUN_STEP_DEG, mainland_solid_km=v)
            small_blocked = sw._island_shadow(small, RUN_STEP_DEG)
            open_b = [b for b in range(0, 360, RUN_STEP_DEG) if b not in hard and b not in small_blocked]
            matrix[v].append(sum(a["span"] for a in sw._merge_open_arcs(open_b, RUN_STEP_DEG)))

    print("\nSWELL_MAINLAND_SOLID_KM sweep — open-window width per spot (°)")
    print(f"committed default = {default:g} km; this run changes nothing (per-call override, read-only).")
    header = f"{'km':>5}   " + "  ".join(f"{c[2]:>15}" for c in cols)
    print(header)
    print("-" * len(header))
    for v in values:
        mark = " *" if v == default else ""
        print(f"{v:>5g}   " + "  ".join(f"{w:>15d}" for w in matrix[v]) + mark)
    print("-" * len(header))
    print("* = committed default.  width = sum of open-arc spans (same metric as the gate table).")
    return 0


# ---- full roster (multiprocessing) -----------------------------------------

def _raycast_worker(item: tuple[int, str, float, float]) -> tuple[int, dict | None]:
    """Worker: cast one spot against the (fork-inherited) GSHHG index."""
    idx, name, lat, lng = item
    try:
        land = load_land_index()  # inherited via fork; lru_cached
        local = sw.local_land_index(land, lat, lng)
        r = sw.compute_swell_window(
            {"name": name, "_algo_lat": lat, "_algo_lng": lng, "lat": lat, "lng": lng},
            local, ray_step=RUN_STEP_DEG,
        )
        return idx, r
    except Exception as e:  # noqa: BLE001 — never let one spot kill the pool
        log.warning("%s: raycast failed: %s", name, e)
        return idx, None


def _arc_width(arcs: list[dict]) -> int:
    return sum(a.get("span", 0) for a in arcs)


def run_full(input_path: Path, output_path: Path, workers: int) -> int:
    spots = json.loads(input_path.read_text())
    land = load_land_index()  # load in the PARENT so forked workers inherit it
    if land is None:
        log.error("GSHHG land index unavailable — cannot run. Did download_geodata.sh run?")
        return 1

    items: list[tuple[int, str, float, float]] = []
    for i, s in enumerate(spots):
        if s.get("is_valid_surf_spot") is False:
            continue
        lat = s.get("_algo_lat", s.get("lat"))
        lng = s.get("_algo_lng", s.get("lng"))
        if lat is None or lng is None:
            continue
        items.append((i, s.get("name") or f"#{i}", float(lat), float(lng)))

    log.info("GSHHG loaded: %d polygons. Casting %d spots at %d° on %d workers.",
             len(land.polygons), len(items), RUN_STEP_DEG, workers)

    t0 = time.time()
    if workers > 1:
        with mp.Pool(processes=workers) as pool:
            results = pool.map(_raycast_worker, items, chunksize=8)
    else:
        results = [_raycast_worker(it) for it in items]
    elapsed = time.time() - t0

    raycast_n = fallback_n = empty_n = 0
    ca_widths: list[int] = []
    ri_widths: list[int] = []
    for idx, r in results:
        s = spots[idx]
        region = s.get("region_hint") or ""
        if r and r.get("swell_window_arcs"):
            s["swell_window_arcs"] = r["swell_window_arcs"]
            s["optimal_swell_dir"] = r["optimal_swell_dir"]
            s["swell_window_source"] = "raycast"
            raycast_n += 1
            w = _arc_width(r["swell_window_arcs"])
            if region == "California":
                ca_widths.append(w)
            elif region == "Rhode Island":
                ri_widths.append(w)
        else:
            # Raycast found no open arc → keep the orientation-derived fallback.
            # orientation_deg is read, never written.
            s["swell_window_arcs"] = []
            fb = compute_swell_window_fallback(s)
            if fb:
                s["swell_window_arcs"] = fb["swell_window_arcs"]
                s["optimal_swell_dir"] = fb["optimal_swell_dir"]
                s["swell_window_source"] = fb["swell_window_source"]
                fallback_n += 1
            else:
                s["optimal_swell_dir"] = None
                empty_n += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(spots, indent=2, ensure_ascii=False) + "\n")

    per_spot = elapsed / max(1, len(items))
    print("\n==== SW-1 full roster raycast — summary ====")
    print(f"  spots cast:            {len(items)}")
    print(f"  source=raycast:        {raycast_n}")
    print(f"  source=orientation:    {fallback_n}  (no open arc; fallback kept)")
    print(f"  unresolved (no arcs):  {empty_n}")
    if ca_widths:
        print(f"  CA median width:       {int(statistics.median(ca_widths))}°  (n={len(ca_widths)})")
    if ri_widths:
        print(f"  RI median width:       {int(statistics.median(ri_widths))}°  (n={len(ri_widths)})")
    print(f"  per-spot time:         {per_spot:.2f}s   total: {elapsed/60:.1f} min on {workers} workers")
    print(f"  wrote:                 {output_path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    p = argparse.ArgumentParser(description="SW-1 swell-window raycast (validate | full)")
    p.add_argument("--mode", choices=["validate", "full"], default="validate")
    p.add_argument("--input", type=Path, default=DEFAULT_ENRICHED_OUTPUT)
    p.add_argument("--output", type=Path, default=DEFAULT_ENRICHED_OUTPUT)
    p.add_argument("--workers", type=int, default=min(os.cpu_count() or 2, 4))
    p.add_argument("--debug-blockers", action="store_true",
                   help="(validate only) after the table, dump the per-ray blocker culprit "
                        "breakdown — the rule (local-coast / mainland-solid / subtend / wrap), the "
                        "polygon area / distance / centroid, and chain subtend that killed each "
                        "bearing. Read-only: writes nothing, tunes nothing.")
    p.add_argument("--sweep-mainland-solid", nargs="?", const="60,80,100,120,150", default=None,
                   metavar="KM_CSV",
                   help="(validate only) sweep SWELL_MAINLAND_SOLID_KM over these comma-separated km "
                        "values (bare flag = 60,80,100,120,150) and print open-window width per gate "
                        "spot. Read-only: does not change the committed default or write spots_enriched.json.")
    args = p.parse_args(argv)

    if args.mode == "validate":
        if args.sweep_mainland_solid is not None:
            try:
                values = [float(x) for x in args.sweep_mainland_solid.split(",") if x.strip()]
            except ValueError:
                log.error("--sweep-mainland-solid expects comma-separated km numbers, got %r",
                          args.sweep_mainland_solid)
                return 2
            return run_sweep_mainland_solid(values)
        return run_validate(debug_blockers=args.debug_blockers)
    if args.debug_blockers or args.sweep_mainland_solid is not None:
        log.warning("--debug-blockers / --sweep-mainland-solid are validate-only; ignoring for --mode full.")
    return run_full(args.input, args.output, max(1, args.workers))


if __name__ == "__main__":
    raise SystemExit(main())
