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


def run_validate() -> int:
    """Cast the 5 gate spots against GSHHG and print diagnostics. No writes."""
    land = load_land_index()
    if land is None:
        log.error("GSHHG land index unavailable — cannot validate. Did download_geodata.sh run?")
        return 1
    log.info("GSHHG loaded: %d polygons. Casting 5 gate spots at %d° (%d rays).",
             len(land.polygons), RUN_STEP_DEG, 360 // RUN_STEP_DEG)

    print(f"\n{'spot':20}{'width':>6}{'ceil':>6}{'target':>9}{'opt':>5}{'nrm':>5}  source    arcs")
    print("-" * 96)
    for name, lat, lng, normal, tlo, thi, note in VALIDATION_SPOTS:
        local = sw.local_land_index(land, lat, lng)
        hard, small = sw._classify_bearings(lat, lng, local, RUN_STEP_DEG)
        small_blocked = sw._island_shadow(small, RUN_STEP_DEG)
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
    print("-" * 96)
    print("width = open window (sum of arc spans); ceil = all-islands-removed ceiling.")
    print("If a number is off, tune the §3 thresholds against GSHHG and re-run validate —")
    print("do NOT loosen the 30 km local-coast guard (Second Beach RI must stay bounded).")
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
    args = p.parse_args(argv)

    if args.mode == "validate":
        return run_validate()
    return run_full(args.input, args.output, max(1, args.workers))


if __name__ == "__main__":
    raise SystemExit(main())
