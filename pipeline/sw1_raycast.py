"""SW-1 swell-window raycast runner (Phase 1).

Two modes, both run against the **real GSHHG L1** land mask (downloaded by
`pipeline/download_geodata.sh`), which is why this lives in a dedicated
GitHub Actions job rather than the normal pipeline — the runner has the open
egress to fetch GSHHG and the headroom to run longer.

  validate  — cast ONLY the 5 Phase-0 spots and print, per spot: window WIDTH,
              the all-islands-removed CEILING, the open arcs, and
              optimal_swell_dir. Writes nothing. This is the gate that must
              pass (thresholds re-checked against GSHHG) before a full run.
              Then the regression checks (REGRESSION_CHECKS) for any of their
              spots in the run — real shadows the island rules must keep — and
              exit 1 if one fails. Held spots (spot_swell_window_holds.json)
              are marked: a full run keeps their stored window.

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
import re
import statistics
import time
from pathlib import Path

from .config import DEFAULT_ENRICHED_OUTPUT
from .enrichment import swell_window as sw
from .enrichment.geodata import load_land_index
from .enrichment.swell_window_fallback import compute_swell_window_fallback
from .enrichment.swell_window_holds import apply_hold, load_holds

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


def _short_label(name):
    """Short column label from a spot name (for --spots sweep headers)."""
    return (name or "?").split(" (")[0].split(",")[0][:12]


def _slug(n):
    return re.sub(r"[^a-z0-9]+", "-", (n or "").lower()).strip("-")


# ---- regression checks (validate) --------------------------------------------
# Real shadows the island rules must keep, read from the validate run's own debug records.
# A check runs only when its spot is in the run (the 5-spot gate, or --spots).
#   island    the island's core must stay blocked: every ray that meets it (matched by
#             centroid within tol_deg on both axes and area in range) and lies at least
#             core_deg inside both ends of its run. The rays nearer the ends are the
#             penumbra, which may open. There must be at least one core ray to test.
#   sector    every ray between the bearings to the named points must be blocked.
#   bounded   the open window must total no more than max_width degrees.
_SANTA_CRUZ = {"label": "Santa Cruz Island's core", "kind": "island", "core_deg": 8,
               "centroid": (34.015, -119.747), "tol_deg": 0.1, "area_km2": (200.0, 320.0)}
REGRESSION_CHECKS = (
    {"slug": "big-beach-makena", "label": "Kaho'olawe's core", "kind": "island", "core_deg": 8,
     "centroid": (20.55, -156.61), "tol_deg": 0.1, "area_km2": (80.0, 200.0)},
    {"slug": "lahaina-breakwall", "label": "Lāna'i's core", "kind": "island", "core_deg": 8,
     "centroid": (20.83, -156.92), "tol_deg": 0.1, "area_km2": (250.0, 500.0)},
    *({"slug": slug, **_SANTA_CRUZ} for slug in ("pitas-point", "mondos", "ventura-point",
                                                 "emma-wood-state-beach", "rincon",
                                                 "leadbetter-beach")),
    {"slug": "banzai-pipeline", "label": "Kaua'i's core", "kind": "island", "core_deg": 4,
     "centroid": (22.06, -159.526), "tol_deg": 0.1, "area_km2": (1000.0, 2000.0)},
    {"slug": "canoes-waikiki", "label": "Diamond Head", "kind": "sector",
     "points": ((21.2614, -157.8053), (21.2555, -157.8097))},
    {"slug": "second-beach-newport", "label": "Second Beach RI bounded", "kind": "bounded",
     "max_width": 180},
)


def _ray_islands(rec: dict) -> list[dict]:
    """Every island a ray's debug record names: the wall, the partial hits, the islets passed."""
    if rec.get("result") == "hard":
        return [rec]
    return list(rec.get("partials", [])) + list(rec.get("passed", []))


def _matches(m: dict, check: dict) -> bool:
    (clat, clng), tol = check["centroid"], check["tol_deg"]
    lo, hi = check["area_km2"]
    return (abs(m["centroid"][0] - clat) <= tol and abs(m["centroid"][1] - clng) <= tol
            and lo <= m["area_km2"] <= hi)


def _sector_rays(lat: float, lng: float, points, step: int) -> list[int]:
    """Ray bearings (multiples of *step*) between the bearings from the spot to *points*,
    taking the shorter way round; the nearest ray when no multiple falls inside."""
    azs = [sw._GEOD.inv(lng, lat, plng, plat)[0] % 360.0 for plat, plng in points]
    lo, hi = min(azs), max(azs)
    if hi - lo > 180.0:                       # the short way round crosses north
        lo, hi = hi, lo + 360.0
    rays = [b % 360 for b in range(0, 720, step) if lo <= b <= hi]
    if not rays:
        mid = (lo + hi) / 2.0
        rays = [int(round(mid / step) * step) % 360]
    return sorted(set(rays))


def _core_rays(rays: list[int], step: int, core_deg: float) -> list[int]:
    """The rays at least *core_deg* inside both ends of their contiguous run."""
    core = []
    for lo, hi in sw._merge_runs(rays, step, 0):
        core += [b % 360 for b in range(lo, hi + 1, step) if min(b - lo, hi - b) >= core_deg]
    return sorted(core)


def regression_verdict(check: dict, lat: float, lng: float, debug_rays: dict,
                       debug_chains: list, open_b: set, arcs: list, step: int):
    """(status, detail) for one check: status PASS or FAIL."""
    kind = check["kind"]
    if kind == "bounded":
        width = sum(a["span"] for a in arcs)
        return ("PASS" if 0 < width <= check["max_width"] else "FAIL",
                f"width {width}° (bound {check['max_width']}°), [{_fmt_arcs(arcs)}]")
    if kind == "sector":
        rays = _sector_rays(lat, lng, check["points"], step)
        opened = [b for b in rays if b in open_b]
        rules = sorted({debug_rays.get(b, {}).get("rule", debug_rays.get(b, {}).get("result", "?"))
                        for b in rays})
        return ("FAIL" if opened else "PASS",
                f"rays {_fmt_ranges(rays, step)} — open {_fmt_ranges(opened, step)}; "
                f"blocked by {', '.join(rules)}")
    # island
    hits = {b: [m for m in _ray_islands(debug_rays.get(b, {})) if _matches(m, check)]
            for b in range(0, 360, step)}
    rays = sorted(b for b, ms in hits.items() if ms)
    if not rays:
        return "FAIL", "no ray meets an island matching the centroid/area given (cannot verify)"
    core = _core_rays(rays, step, check["core_deg"])
    opened = [b for b in core if b in open_b]
    ms = [m for b in rays for m in hits[b]]
    how = sorted({debug_rays[b].get("rule", debug_rays[b]["result"]) for b in rays})
    detail = (f"{ms[0]['area_km2']:.0f} km² @({ms[0]['centroid'][0]:.3f},{ms[0]['centroid'][1]:.3f}), "
              f"{min(m['dist_km'] for m in ms):.1f}–{max(m['dist_km'] for m in ms):.1f} km, "
              f"rays {_fmt_ranges(rays, step)} ({', '.join(how)}); core (≥{check['core_deg']}° in) "
              f"{_fmt_ranges(core, step)}, open in core {_fmt_ranges(opened, step)}, "
              f"open overall {_fmt_ranges([b for b in rays if b in open_b], step)}")
    keys = {m["island"] for m in ms}
    for c in debug_chains:
        if c.get("island") in keys:
            kept = f", core {c['core'][0]}–{c['core'][1]}" if c.get("core") else ""
            detail += (f"; own run {c['lo']}–{c['hi']} ({c['width']}°), Fresnel edge on "
                       f"{c.get('edge_rays', 0)} ray(s) → {c['decision']}{kept}")
    if not core:
        return "FAIL", detail + "; no ray far enough inside the run to test"
    return ("FAIL" if opened else "PASS"), detail


def print_regression_checks(cast: dict, step: int) -> bool:
    """Print every regression check whose spot was cast; True when none FAILed."""
    ok = True
    print("\nRegression checks — real shadows the island rules must keep:")
    for check in REGRESSION_CHECKS:
        if check["slug"] not in cast:
            print(f"  {'—':6} {check['slug']:22} {check['label']}: not in this run")
            continue
        lat, lng, dr, dc, open_b, arcs = cast[check["slug"]]
        status, detail = regression_verdict(check, lat, lng, dr, dc, open_b, arcs, step)
        ok = ok and status != "FAIL"
        print(f"  {status:6} {check['slug']:22} {check['label']}: {detail}")
    return ok


def _load_spots_by_slug(slugs):
    """VALIDATION_SPOTS-shaped tuples for *slugs*, loaded from spots_enriched.json with their
    EXACT prod lat/lng/orientation — so `--spots` debugs the real reported spots against GSHHG.
    Returns (name, lat, lng, orientation, None, None, note) per resolved slug. Read-only."""
    try:
        roster = json.loads(DEFAULT_ENRICHED_OUTPUT.read_text())
    except Exception as e:  # noqa: BLE001
        log.error("--spots: cannot read %s (%s)", DEFAULT_ENRICHED_OUTPUT, e)
        return []

    by = {_slug(s.get("name")): s for s in roster}
    out, missing = [], []
    for sl in slugs:
        s = by.get(sl)
        lat = s.get("_algo_lat", s.get("lat")) if s else None
        lng = s.get("_algo_lng", s.get("lng")) if s else None
        if s is None or lat is None or lng is None:
            missing.append(sl)
            continue
        note = f"src={s.get('swell_window_source')} region={s.get('region_hint')}"
        out.append((s.get("name") or sl, float(lat), float(lng),
                    int(round(s.get("orientation_deg") or 0)), None, None, note))
    if missing:
        log.warning("--spots: %d slug(s) not resolved in spots_enriched.json: %s",
                    len(missing), ", ".join(missing))
    return out


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
    n_fresnel = sum(1 for b in hard_sorted if debug_rays[b].get("rule") == "fresnel_wall")
    ceil = (360 // step - len(hard_sorted)) * step
    print(f"   R1 own coast ≤{sw.SWELL_OWN_COAST_KM:.0f}km · R2 Fresnel at {sw.SWELL_FRESNEL_PERIOD_S:.0f}s: "
          f"pass N<{sw.SWELL_FRESNEL_TRANSPARENT_N:g}, wall N≥{sw.SWELL_FRESNEL_WALL_N:g} · R3 each island on its own run"
          f" · beyond, islands <{sw.SWELL_FRESNEL_ISLAND_MAX_KM2:.0f}km² with N≥{sw.SWELL_FRESNEL_WALL_N:g}: "
          f"Fresnel edge √(L/r)")
    print(f"   HARD-blocked {len(hard_sorted)} rays → ceiling {ceil}°  (local-coast {n_local}, "
          f"Fresnel wall {n_fresnel}, mainland-solid {n_solid})  — bearing / rule / area / dist / centroid:")
    for b in hard_sorted:
        r = debug_rays[b]
        n = f"  N={r['fresnel_n']:.1f}" if "fresnel_n" in r else ""
        print(f"     b={b:3d}  {r['rule']:18} area≈{r['area_km2']:>11.0f} km²  dist={r['dist_km']:6.1f} km  "
              f"centroid({r['centroid'][0]:.3f},{r['centroid'][1]:.3f}){n}")
    passed = [(b, m) for b in sorted(debug_rays) for m in debug_rays[b].get("passed", [])]
    if passed:
        print(f"   PASSED (R2, N<{sw.SWELL_FRESNEL_TRANSPARENT_N:g}) {len(passed)} — the ray goes on behind these islets:")
        for b, m in passed:
            print(f"     b={b:3d}  area≈{m['area_km2']:>6.0f} km²  dist={m['dist_km']:6.1f} km  "
                  f"centroid({m['centroid'][0]:.3f},{m['centroid'][1]:.3f})  N={m['fresnel_n']:.2f}")
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
            # which island: R3 judges each on its own run, so a chain is one polygon's rays.
            members = [m for b in _chain_member_bearings(c["lo"], c["hi"], step)
                       for m in debug_rays.get(b, {}).get("partials", [])
                       if m["island"] == c.get("island")]
            if members:
                dists = [m["dist_km"] for m in members]
                m0 = members[0]
                print(f"        island {m0['area_km2']:.0f}km²@({m0['centroid'][0]:.3f},{m0['centroid'][1]:.3f}) · "
                      f"{len(members)} rays · dist {min(dists):.0f}–{max(dists):.0f}km")
                rays = [(b, m) for b in _chain_member_bearings(c["lo"], c["hi"], step)
                        for m in debug_rays.get(b, {}).get("partials", [])
                        if m["island"] == c.get("island")]
                print("        per ray (bearing dist N): " + " · ".join(
                    f"{b} {m['dist_km']:.1f}km " + ("N=—" if m.get("fresnel_n") is None
                                                    else f"N={m['fresnel_n']:.1f}")
                    for b, m in rays))
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


def run_validate(debug_blockers: bool = False, spots=None) -> int:
    """Cast the gate spots (or the --spots set) against GSHHG and print diagnostics. No writes.

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
    src = spots if spots is not None else VALIDATION_SPOTS
    log.info("GSHHG loaded: %d polygons. Casting %d spot(s) at %d° (%d rays).%s",
             len(land.polygons), len(src), RUN_STEP_DEG, 360 // RUN_STEP_DEG,
             "  [--debug-blockers ON]" if debug_blockers else "")

    holds = load_holds()
    cast: dict[str, tuple] = {}
    print(f"\n{'spot':20}{'width':>6}{'ceil':>6}{'target':>9}{'opt':>5}{'nrm':>5}  source    arcs")
    print("-" * 96)
    detail: list[tuple] = []
    for name, lat, lng, normal, tlo, thi, note in src:
        local = sw.local_land_index(land, lat, lng)
        # Debug records are kept for every spot: the regression checks below read them.
        debug_rays: dict = {}
        debug_chains: list = []
        hard, partial = sw._classify_bearings(lat, lng, local, RUN_STEP_DEG, debug=debug_rays)
        small_blocked = sw._islands_shadow(partial, RUN_STEP_DEG, debug=debug_chains)
        open_b = [b for b in range(0, 360, RUN_STEP_DEG)
                  if b not in hard and b not in small_blocked]
        ceil_b = [b for b in range(0, 360, RUN_STEP_DEG) if b not in hard]
        arcs = sw._merge_open_arcs(open_b, RUN_STEP_DEG)
        optimal = sw._open_window_center(open_b)
        width = sum(a["span"] for a in arcs)
        ceil = len(ceil_b) * RUN_STEP_DEG
        source = "raycast" if arcs else "(empty→fallback)"
        tgt = f"{tlo}-{'+' if thi is None else thi}" if tlo is not None else "—"
        held = "  HELD: a full run keeps the stored window" if _slug(name) in holds else ""
        print(f"{name[:20]:20}{width:6d}{ceil:6d}{tgt:>9}{str(optimal):>5}{normal:>5}  {source:9} "
              f"[{_fmt_arcs(arcs)}]{held}")
        cast[_slug(name)] = (lat, lng, debug_rays, debug_chains, set(open_b), arcs)
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
    return 0 if print_regression_checks(cast, RUN_STEP_DEG) else 1


def run_sweep_mainland_solid(values, spots=None) -> int:
    """Sweep SWELL_MAINLAND_SOLID_KM over *values* (km) and print the open-window WIDTH per
    gate spot (or the --spots set) in ONE run — a sensitivity table for picking the value.
    Read-only: each value is passed per call to _classify_bearings, so the committed default
    is never mutated and spots_enriched.json is never written."""
    land = load_land_index()
    if land is None:
        log.error("GSHHG land index unavailable — cannot sweep. Did download_geodata.sh run?")
        return 1
    default = sw.SWELL_MAINLAND_SOLID_KM
    log.info("GSHHG loaded: %d polygons. Sweeping SWELL_MAINLAND_SOLID_KM over %s km (committed default %g).",
             len(land.polygons), ",".join(f"{v:g}" for v in values), default)

    src = spots if spots is not None else VALIDATION_SPOTS
    labels = (["Hunt", "Malibu", "Rincon", "Blacks", "RI"]
              if spots is None else [_short_label(s[0]) for s in src])
    cols = []   # (lat, lng, header) per spot
    for (name, lat, lng, normal, tlo, thi, note), lbl in zip(src, labels):
        tgt = f"({tlo}-{'+' if thi is None else thi})" if tlo is not None else ""
        cols.append((lat, lng, f"{lbl}{tgt}"))

    # width[value][spot] — cast each spot once per value (local index reused across values;
    # only the hard/small split depends on the swept SWELL_MAINLAND_SOLID_KM).
    matrix: dict[float, list[int]] = {v: [] for v in values}
    for lat, lng, _lbl in cols:
        local = sw.local_land_index(land, lat, lng)
        for v in values:
            hard, partial = sw._classify_bearings(lat, lng, local, RUN_STEP_DEG, mainland_solid_km=v)
            small_blocked = sw._islands_shadow(partial, RUN_STEP_DEG)
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


# Tiers the raycast must never overwrite — NWPS / MOP are real nearshore wave models,
# a strictly better swell source than the geometry-derived window.
_PRESERVE_SOURCES = ("nwps", "cdip_mop")


def _apply_raycast_result(spot: dict, r: dict | None, holds: dict | None = None) -> str:
    """Write a raycast result onto *spot* IN PLACE and return a category tag for the summary.

    swell_window_arcs / optimal_swell_dir are refreshed from the raycast (better geometry,
    useful as a dormant fallback), but a spot already on the better 'nwps'/'cdip_mop' tier
    KEEPS its swell_window_source — the raycast never demotes it. A better-tier spot the
    raycast can't solve is left entirely untouched (never downgraded to an orientation
    fallback). Every other spot follows the prior behaviour: 'raycast' on a solve, the
    orientation-derived fallback otherwise (orientation_deg is read, never written).

    A spot on the hold list (*holds*, spot_swell_window_holds.json) is not written from the
    ray-cast at all: it keeps the arcs and optimal the list holds for it, and returns "held"."""
    if holds and apply_hold(spot, holds):
        return "held"
    preserve = spot.get("swell_window_source") in _PRESERVE_SOURCES
    if r and r.get("swell_window_arcs"):
        spot["swell_window_arcs"] = r["swell_window_arcs"]
        spot["optimal_swell_dir"] = r["optimal_swell_dir"]
        if not preserve:
            spot["swell_window_source"] = "raycast"
        return "raycast"
    if preserve:
        return "preserved"  # NWPS/MOP spot the raycast couldn't solve → leave arcs/source as-is
    spot["swell_window_arcs"] = []
    fb = compute_swell_window_fallback(spot)
    if fb:
        spot["swell_window_arcs"] = fb["swell_window_arcs"]
        spot["optimal_swell_dir"] = fb["optimal_swell_dir"]
        spot["swell_window_source"] = fb["swell_window_source"]
        return "fallback"
    spot["optimal_swell_dir"] = None
    return "empty"


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

    holds = load_holds()
    raycast_n = fallback_n = empty_n = preserved_n = 0
    preserved_names: list[str] = []
    held_names: list[str] = []
    ca_widths: list[int] = []
    ri_widths: list[int] = []
    for idx, r in results:
        s = spots[idx]
        region = s.get("region_hint") or ""
        cat = _apply_raycast_result(s, r, holds)   # preserves NWPS/MOP source; never demotes a better tier
        if cat == "held":
            held_names.append(s.get("name") or f"#{idx}")
        elif cat == "raycast":
            raycast_n += 1
            w = _arc_width(s["swell_window_arcs"])
            if region == "California":
                ca_widths.append(w)
            elif region == "Rhode Island":
                ri_widths.append(w)
        elif cat == "preserved":
            preserved_n += 1
            preserved_names.append(s.get("name") or f"#{idx}")
        elif cat == "fallback":
            fallback_n += 1
        else:
            empty_n += 1

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(spots, indent=2, ensure_ascii=False) + "\n")

    per_spot = elapsed / max(1, len(items))
    print("\n==== SW-1 full roster raycast — summary ====")
    print(f"  spots cast:            {len(items)}")
    print(f"  source=raycast:        {raycast_n}")
    print(f"  source=nwps/cdip_mop:  {preserved_n}  (better tier, raycast found NO open arc: "
          "the stored arcs and optimal are KEPT, not recomputed)")
    for name in sorted(preserved_names):
        print(f"    kept stored window:  {name}")
    print(f"  held (not written):    {len(held_names)}  (spot_swell_window_holds.json)")
    for name in sorted(held_names):
        print(f"    held window:         {name}")
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
    p.add_argument("--spots", default=None, metavar="SLUGS",
                   help="(validate only) comma-separated spot slugs to cast INSTEAD of the 5-spot gate, "
                        "loaded from spots_enriched.json with their exact lat/lng/orientation. Combine with "
                        "--debug-blockers to dump per-ray blockers + A/B/C buckets for reported spots. Read-only.")
    args = p.parse_args(argv)

    if args.mode == "validate":
        spots = None
        if args.spots:
            spots = _load_spots_by_slug([x.strip() for x in args.spots.split(",") if x.strip()])
            if not spots:
                log.error("--spots: no valid spots resolved; nothing to cast.")
                return 2
        if args.sweep_mainland_solid is not None:
            try:
                values = [float(x) for x in args.sweep_mainland_solid.split(",") if x.strip()]
            except ValueError:
                log.error("--sweep-mainland-solid expects comma-separated km numbers, got %r",
                          args.sweep_mainland_solid)
                return 2
            return run_sweep_mainland_solid(values, spots=spots)
        return run_validate(debug_blockers=args.debug_blockers, spots=spots)
    if args.debug_blockers or args.sweep_mainland_solid is not None or args.spots:
        log.warning("--debug-blockers / --sweep-mainland-solid / --spots are validate-only; ignoring for --mode full.")
    return run_full(args.input, args.output, max(1, args.workers))


if __name__ == "__main__":
    raise SystemExit(main())
