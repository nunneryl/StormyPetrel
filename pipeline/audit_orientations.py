"""Audit spot orientations for plausibility.

Two phases, both optional via flags:

  Phase 1 — Land check
      Project a 1 km ray from each spot in its orientation_deg
      direction. If that ray intersects GSHHG L1 land within the
      kilometer, the orientation is probably pointed at land instead
      of open ocean. Writes pipeline/data/audit_inland.json.

  Phase 2 — surf-forecast.com reference
      Look each spot up on surf-forecast.com, extract their
      "ideal swell direction is from the X" field, and compare to our
      orientation_deg. Flag spots whose angular difference is ≥ 20°.
      Wrapped by pipeline.audit_orientations_reference (which reuses
      the scrape_surf_forecast scraper). Writes
      pipeline/data/audit_vs_reference.json.

CLI:
    python -m pipeline.audit_orientations               # both phases
    python -m pipeline.audit_orientations --phase1-only
    python -m pipeline.audit_orientations --phase2-only
    python -m pipeline.audit_orientations -v            # debug logs

Env (phase 2 only): network access to surf-forecast.com.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from pathlib import Path

from .config import DEFAULT_ENRICHED_OUTPUT

log = logging.getLogger("pipeline.audit_orientations")

DATA_DIR = Path(__file__).parent / "data"
INLAND_OUTPUT = DATA_DIR / "audit_inland.json"
REFERENCE_OUTPUT = DATA_DIR / "audit_vs_reference.json"

RAY_LENGTH_M = 1000.0
# Start the ray 30 m past the spot so a coastline point that GSHHG
# happens to classify as "just on land" doesn't auto-trigger a false
# POINTS_INLAND on a spot whose orientation is actually fine.
RAY_OFFSHORE_BUFFER_M = 30.0

EARTH_RADIUS_M = 6_371_000.0


# ---------------------------------------------------------------------------
# Geodesy
# ---------------------------------------------------------------------------

def destination(lat: float, lng: float, bearing_deg: float, dist_m: float) -> tuple[float, float]:
    """Great-circle destination given a bearing + distance in meters."""
    d = dist_m / EARTH_RADIUS_M
    b = math.radians(bearing_deg)
    φ1 = math.radians(lat)
    λ1 = math.radians(lng)
    φ2 = math.asin(math.sin(φ1) * math.cos(d) + math.cos(φ1) * math.sin(d) * math.cos(b))
    λ2 = λ1 + math.atan2(
        math.sin(b) * math.sin(d) * math.cos(φ1),
        math.cos(d) - math.sin(φ1) * math.sin(φ2),
    )
    return math.degrees(φ2), math.degrees(λ2)


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    φ1 = math.radians(lat1)
    φ2 = math.radians(lat2)
    dφ = math.radians(lat2 - lat1)
    dλ = math.radians(lng2 - lng1)
    a = math.sin(dφ / 2) ** 2 + math.cos(φ1) * math.cos(φ2) * math.sin(dλ / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _flatten_coords(geom) -> list[tuple[float, float]]:
    """Pull out (x, y) tuples from any shapely geometry. Used to walk
    intersection results that may be a Point / MultiPoint / LineString
    / GeometryCollection."""
    if geom.is_empty:
        return []
    if hasattr(geom, "geoms"):
        out: list[tuple[float, float]] = []
        for g in geom.geoms:
            out.extend(_flatten_coords(g))
        return out
    if hasattr(geom, "coords"):
        return [(x, y) for (x, y, *_) in geom.coords]
    return []


# ---------------------------------------------------------------------------
# Phase 1 — land check
# ---------------------------------------------------------------------------

def phase1_land_check(spots: list[dict]) -> dict:
    """For each spot, project a 1 km ray in orientation_deg and look
    for an intersection with GSHHG L1 land. Returns the audit dict."""
    from shapely.geometry import LineString

    from .enrichment.geodata import load_land_index
    land = load_land_index()
    if land is None:
        raise RuntimeError(
            "GSHHG land index unavailable — run `python -m pipeline.scripts.download_geodata` "
            "to fetch the shapefile before phase 1 can run.",
        )

    flagged: list[dict] = []
    passed = 0
    skipped = 0

    for spot in spots:
        name = spot.get("name")
        lat = spot.get("lat")
        lng = spot.get("lng")
        orient = spot.get("orientation_deg")
        if not name or lat is None or lng is None or orient is None:
            skipped += 1
            continue

        # Build a ray from (spot offset 30m offshore) to (spot + 1km).
        # The 30m buffer skips the spot's own potentially-on-land
        # coordinate so GSHHG coarseness doesn't auto-trigger every
        # close-to-shore point.
        start_lat, start_lng = destination(lat, lng, orient, RAY_OFFSHORE_BUFFER_M)
        end_lat, end_lng = destination(lat, lng, orient, RAY_LENGTH_M)
        ray = LineString([(start_lng, start_lat), (end_lng, end_lat)])

        nearest_hit_m: float | None = None
        for idx in land.polygon_tree.query(ray):
            poly = land.polygons[idx]
            inter = ray.intersection(poly)
            if inter.is_empty:
                continue
            for (x, y) in _flatten_coords(inter):
                d = haversine_m(lat, lng, y, x)
                if nearest_hit_m is None or d < nearest_hit_m:
                    nearest_hit_m = d

        if nearest_hit_m is not None and nearest_hit_m <= RAY_LENGTH_M:
            flagged.append({
                "name": name,
                "slug": spot.get("slug"),
                "lat": lat,
                "lng": lng,
                "orientation_deg": orient,
                "issue": "POINTS_INLAND",
                "land_hit_at_m": round(nearest_hit_m, 1),
            })
        else:
            passed += 1

    # Most-severe first — closest land hit = orientation is most
    # blatantly wrong.
    flagged.sort(key=lambda x: x["land_hit_at_m"])

    return {
        "flagged": flagged,
        "passed": passed,
        "skipped": skipped,
        "flagged_count": len(flagged),
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input", type=Path, default=DEFAULT_ENRICHED_OUTPUT,
                   help="spots_enriched.json")
    phase = p.add_mutually_exclusive_group()
    phase.add_argument("--phase1-only", action="store_true",
                       help="Run only the GSHHG land check.")
    phase.add_argument("--phase2-only", action="store_true",
                       help="Run only the surf-forecast.com comparison.")
    p.add_argument("--interval", type=float, default=2.0,
                   help="Phase 2 minimum seconds between surf-forecast requests.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _print_progress(i: int, total: int, name: str) -> None:
    if i % 25 == 1 or i == total:
        log.info("phase 2: %d / %d — %s", i, total, name)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.input.exists():
        log.error("input %s not found", args.input)
        return 2
    spots = json.loads(args.input.read_text())
    if not isinstance(spots, list):
        log.error("expected JSON array at %s", args.input)
        return 2
    log.info("loaded %d spots", len(spots))

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    p1_result: dict | None = None
    p2_result: dict | None = None

    if not args.phase2_only:
        log.info("phase 1: land check")
        p1_result = phase1_land_check(spots)
        INLAND_OUTPUT.write_text(json.dumps(p1_result, indent=2) + "\n")
        log.info("phase 1: flagged=%d passed=%d skipped=%d → %s",
                 p1_result["flagged_count"], p1_result["passed"], p1_result["skipped"],
                 INLAND_OUTPUT)

    if not args.phase1_only:
        log.info("phase 2: surf-forecast.com comparison (~%d sec/spot)", args.interval)
        from .audit_orientations_reference import run_phase2
        p2_result = run_phase2(spots, interval_s=args.interval, on_progress=_print_progress)
        REFERENCE_OUTPUT.write_text(json.dumps(p2_result, indent=2) + "\n")
        s = p2_result["summary"]
        log.info("phase 2: pass=%d flag_20=%d not_found=%d → %s",
                 s.get("pass", 0), s.get("flag_20", 0), s.get("not_found", 0),
                 REFERENCE_OUTPUT)

    _print_summary(p1_result, p2_result)
    return 0


def _print_summary(p1: dict | None, p2: dict | None) -> None:
    """Combined punch list — every unique spot that needs review,
    sorted by severity (closest land hit OR largest angular diff)."""
    log.info("--- AUDIT SUMMARY ---")
    if p1:
        log.info("Phase 1: %d spots point inland (out of %d checked)",
                 p1["flagged_count"], p1["flagged_count"] + p1["passed"])
    if p2:
        s = p2["summary"]
        log.info("Phase 2: %d spots differ from surf-forecast by ≥ 20° (pass=%d, not_found=%d)",
                 s.get("flag_20", 0), s.get("pass", 0), s.get("not_found", 0))

    # Combined punch list keyed by spot slug/name with the worst signal
    # from each phase, sorted by severity.
    combined: dict[str, dict] = {}
    if p1:
        for row in p1["flagged"]:
            key = row.get("slug") or row.get("name")
            combined[key] = {
                "name": row["name"],
                "phase1_hit_m": row["land_hit_at_m"],
                "phase2_diff": None,
            }
    if p2:
        for row in p2["matched"]:
            if row.get("status") != "FLAG_20":
                continue
            key = row.get("slug") or row.get("name")
            entry = combined.setdefault(key, {
                "name": row["name"],
                "phase1_hit_m": None,
                "phase2_diff": None,
            })
            entry["phase2_diff"] = row["diff"]

    if not combined:
        log.info("No spots flagged. Audit clean.")
        return

    # Sort: phase-1 hits first (sorted by closest hit), then phase-2 by
    # largest angular diff. A spot that fails both lands at the top.
    def _severity(item: dict) -> tuple:
        # Lower tuple sorts first. Negative diff so larger diffs sort
        # earlier within the phase-2 bucket; phase-1 hits get a
        # "fails-both" boost.
        fails_both = (item["phase1_hit_m"] is not None and item["phase2_diff"] is not None)
        return (
            0 if fails_both else (1 if item["phase1_hit_m"] is not None else 2),
            item["phase1_hit_m"] if item["phase1_hit_m"] is not None else 1e9,
            -(item["phase2_diff"] or 0),
        )

    rows = sorted(combined.values(), key=_severity)
    log.info("Combined: %d unique spots need manual review", len(rows))
    for row in rows:
        bits = [row["name"]]
        if row["phase1_hit_m"] is not None:
            bits.append(f"land at {row['phase1_hit_m']:.0f}m")
        if row["phase2_diff"] is not None:
            bits.append(f"SF differs {row['phase2_diff']:.0f}°")
        log.info("  - %s", " · ".join(bits))


if __name__ == "__main__":
    sys.exit(main())
