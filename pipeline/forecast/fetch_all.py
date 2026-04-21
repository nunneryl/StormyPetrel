"""Forecast orchestrator — run all (or one) fetcher against spots_enriched.json.

Usage:
    python -m pipeline.forecast.fetch_all                # tides + buoys
    python -m pipeline.forecast.fetch_all --only tides
    python -m pipeline.forecast.fetch_all --only buoys
    python -m pipeline.forecast.fetch_all --no-cache -v  # force live refetch
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from ..config import DEFAULT_ENRICHED_OUTPUT
from . import buoys as buoys_mod
from . import tides as tides_mod

log = logging.getLogger("pipeline.forecast.fetch_all")

SOURCES = {
    "tides": tides_mod.fetch,
    "buoys": buoys_mod.fetch,
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Fetch forecast/observation data for every enriched spot.")
    p.add_argument("--input", type=Path, default=DEFAULT_ENRICHED_OUTPUT,
                   help="spots_enriched.json to read station/buoy assignments from")
    p.add_argument("--only", choices=sorted(SOURCES.keys()), default=None,
                   help="Run a single fetcher instead of all")
    p.add_argument("--no-cache", action="store_true",
                   help="Ignore cached responses and refetch from live APIs")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _freshness_minutes(obs_time_iso: str | None) -> str:
    if not obs_time_iso:
        return "n/a"
    try:
        t = datetime.fromisoformat(obs_time_iso)
    except ValueError:
        return "n/a"
    delta_min = (datetime.now(tz=timezone.utc) - t).total_seconds() / 60
    return f"{delta_min:.0f}m ago"


def _summarize(source: str, result: dict) -> None:
    print()
    print("=" * 60)
    print(f"Forecast summary: {source}")
    print("=" * 60)
    print(f"  entries written: {len(result)}")

    if source == "tides":
        hilo_counts = [len(r.get("hilo") or []) for r in result.values()]
        hourly_counts = [len(r.get("hourly") or []) for r in result.values()]
        if hilo_counts:
            print(f"  hilo events/station (min/med/max): "
                  f"{min(hilo_counts)}/{sorted(hilo_counts)[len(hilo_counts)//2]}/{max(hilo_counts)}")
        if hourly_counts:
            print(f"  hourly points/station (min/med/max): "
                  f"{min(hourly_counts)}/{sorted(hourly_counts)[len(hourly_counts)//2]}/{max(hourly_counts)}")

    elif source == "buoys":
        fresh = [_freshness_minutes(r.get("latest", {}).get("time")) for r in result.values()]
        fresh_min = [int(f.rstrip("m ago")) for f in fresh if f.endswith("m ago")]
        if fresh_min:
            fresh_min.sort()
            print(f"  freshness (min/median/max): "
                  f"{fresh_min[0]}m / {fresh_min[len(fresh_min)//2]}m / {fresh_min[-1]}m")
        with_waves = sum(1 for r in result.values() if r.get("latest", {}).get("wave_height_m") is not None)
        with_spec = sum(1 for r in result.values() if r.get("spec_history_24h"))
        print(f"  buoys reporting waves (WVHT): {with_waves}/{len(result)}")
        print(f"  buoys with spectral data:     {with_spec}/{len(result)}")

    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.input.exists():
        log.error("Input file %s does not exist. Run `python -m pipeline.enrich` first.", args.input)
        return 1

    spots = json.loads(args.input.read_text())
    log.info("Loaded %d enriched spots from %s", len(spots), args.input)

    sources_to_run = {args.only: SOURCES[args.only]} if args.only else SOURCES
    any_data = False
    for name, fn in sources_to_run.items():
        log.info("=== running %s ===", name)
        try:
            result = fn(spots, use_cache=not args.no_cache)
        except Exception as e:  # noqa: BLE001
            log.exception("%s: fetch failed: %s", name, e)
            continue
        _summarize(name, result)
        any_data = any_data or bool(result)

    return 0 if any_data else 2


if __name__ == "__main__":
    sys.exit(main())
