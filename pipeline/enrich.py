"""Phase 0B enrichment orchestrator.

Reads spots_seed.json, runs the five enrichment algorithms on each spot,
and writes spots_enriched.json with the full schema.

CLI:
    python -m pipeline.enrich [--input ...] [--output ...] [--skip-raycast]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import DEFAULT_ENRICHED_OUTPUT, DEFAULT_OUTPUT
from .enrichment.break_type import compute_break_type
from .enrichment.buoys import compute_nearest_buoy
from .enrichment.orientation import compute_orientation
from .enrichment.swell_window import compute_swell_window
from .enrichment.tides import compute_nearest_tide_station

log = logging.getLogger("pipeline.enrich")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Enrich seed spots with forecast-grade metadata.")
    p.add_argument("--input", type=Path, default=DEFAULT_OUTPUT, help="Input spots_seed.json")
    p.add_argument("--output", type=Path, default=DEFAULT_ENRICHED_OUTPUT, help="Output spots_enriched.json")
    p.add_argument(
        "--skip-raycast",
        action="store_true",
        help="Skip Algorithm 2 (swell window ray-casting) — much faster for dev iteration.",
    )
    p.add_argument("--limit", type=int, default=None, help="Only enrich the first N spots (dev).")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _enrich_one(spot: dict, skip_raycast: bool) -> dict:
    """Run all algorithms on one spot; return the enriched record."""
    enriched = {
        "name": spot.get("name"),
        "lat": spot["lat"],
        "lng": spot["lng"],
        "region_hint": spot.get("region_hint"),
    }
    confidence: dict = {}

    # Algo 1 — orientation
    try:
        r = compute_orientation(spot)
        enriched.update(r)
        confidence["orientation"] = r.pop("orientation_confidence", 0.0)
        enriched.pop("orientation_confidence", None)
    except Exception as e:  # noqa: BLE001
        log.warning("%s: orientation failed: %s", spot.get("name"), e)
        enriched.update(orientation_deg=None, orientation_50m=None, orientation_200m=None, offshore_wind_deg=None)
        confidence["orientation"] = 0.0

    # Algo 2 — swell window (optional)
    if skip_raycast:
        enriched.update(swell_window_arcs=[], optimal_swell_dir=None)
        confidence["swell_window"] = 0.0
    else:
        try:
            r = compute_swell_window(spot)
            enriched["swell_window_arcs"] = r["swell_window_arcs"]
            enriched["optimal_swell_dir"] = r["optimal_swell_dir"]
            confidence["swell_window"] = r["swell_window_confidence"]
        except Exception as e:  # noqa: BLE001
            log.warning("%s: swell window failed: %s", spot.get("name"), e)
            enriched.update(swell_window_arcs=[], optimal_swell_dir=None)
            confidence["swell_window"] = 0.0

    # Algo 3 — break type
    try:
        r = compute_break_type(spot)
        enriched["break_type"] = r["break_type"]
        enriched["break_type_confidence"] = r["break_type_confidence"]
        confidence["break_type"] = r["break_type_confidence"]
    except Exception as e:  # noqa: BLE001
        log.warning("%s: break_type failed: %s", spot.get("name"), e)
        enriched.update(break_type="beach", break_type_confidence=0.5)
        confidence["break_type"] = 0.5

    # Algo 4 — nearest buoy
    try:
        r = compute_nearest_buoy(spot)
        enriched["nearest_buoy_id"] = r["nearest_buoy_id"]
        enriched["nearest_buoy_dist_km"] = r["nearest_buoy_dist_km"]
        enriched["fallback_buoy_ids"] = r["fallback_buoy_ids"]
        confidence["nearest_buoy"] = r["buoy_confidence"]
    except Exception as e:  # noqa: BLE001
        log.warning("%s: buoy failed: %s", spot.get("name"), e)
        enriched.update(nearest_buoy_id=None, nearest_buoy_dist_km=None, fallback_buoy_ids=[])
        confidence["nearest_buoy"] = 0.0

    # Algo 5 — tide station
    try:
        r = compute_nearest_tide_station(spot)
        enriched["nearest_tide_station_id"] = r["nearest_tide_station_id"]
        enriched["nearest_tide_station_dist_km"] = r["nearest_tide_station_dist_km"]
        confidence["nearest_tide_station"] = 1.0 if r["nearest_tide_station_id"] else 0.0
    except Exception as e:  # noqa: BLE001
        log.warning("%s: tide failed: %s", spot.get("name"), e)
        enriched.update(nearest_tide_station_id=None, nearest_tide_station_dist_km=None)
        confidence["nearest_tide_station"] = 0.0

    enriched["sources"] = spot.get("sources", {})
    enriched["tags"] = spot.get("tags", {})
    enriched["enrichment_confidence"] = confidence
    return enriched


def _summarize(records: list[dict]) -> None:
    n = len(records)
    print()
    print("=" * 60)
    print(f"Enrichment summary ({n} spots)")
    print("=" * 60)
    def pct(f):
        return f"{100 * sum(1 for r in records if f(r)) / max(n, 1):.0f}%"
    print(f"  orientation resolved:       {pct(lambda r: r.get('orientation_deg') is not None)}")
    print(f"  swell window resolved:      {pct(lambda r: r.get('optimal_swell_dir') is not None)}")
    print(f"  break type = point:         {pct(lambda r: r.get('break_type') == 'point')}")
    print(f"  nearest buoy assigned:      {pct(lambda r: r.get('nearest_buoy_id'))}")
    print(f"  nearest tide station ≤50km: {pct(lambda r: r.get('nearest_tide_station_id'))}")
    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.input.exists():
        log.error("Input file %s does not exist. Run `python -m pipeline.seed_spots` first.", args.input)
        return 1

    spots = json.loads(args.input.read_text())
    if args.limit:
        spots = spots[: args.limit]
    log.info("Enriching %d spots from %s", len(spots), args.input)

    try:
        from tqdm import tqdm
        iterator = tqdm(spots, desc="enrich", unit="spot")
    except ImportError:
        iterator = spots

    enriched = [_enrich_one(spot, args.skip_raycast) for spot in iterator]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(enriched, indent=2, ensure_ascii=False))
    log.info("Wrote %d enriched spots to %s", len(enriched), args.output)
    _summarize(enriched)
    return 0


if __name__ == "__main__":
    sys.exit(main())
