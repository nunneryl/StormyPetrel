"""Freeze the NDBC buoy id -> lat/lng map into a committed snapshot.

NDBC's activestations.xml is downloaded at runtime and ephemeral, and there is no `buoys` table — so
a stored nearest_buoy_id can go stale (an id relocated by NDBC) or inconsistent (a spot moved away from
its buoy) with nothing to validate it against, and buoy assignments can't be audited in SQL. This CLI
parses the XML into pipeline/data/ndbc_buoy_snapshot.json ({id: {lat, lng, name}}), which is committed;
db_import mirrors it into the `buoys` table, and validate_coord_derived (db_import) checks every stored
nearest_buoy_dist_km against it.

Run on a host where the NDBC XML is present (Mac / CI):
    python -m pipeline.snapshot_buoys                    # writes the default snapshot file
    python -m pipeline.snapshot_buoys --output PATH
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import NDBC_BUOY_SNAPSHOT_FILE
from .enrichment.geodata import load_ndbc_active_stations

log = logging.getLogger("pipeline.snapshot_buoys")


def build_snapshot() -> dict[str, dict]:
    """{buoy_id: {lat, lng, name}} for every active NDBC station with valid coords. Uses the FULL active
    list (not the momentary wave-reporting subset) so the coordinate metadata is stable run to run."""
    stations = load_ndbc_active_stations()
    return {s["id"]: {"lat": s["lat"], "lng": s["lng"], "name": s.get("name", "")}
            for s in stations if s.get("id")}


def write_snapshot(path: Path = NDBC_BUOY_SNAPSHOT_FILE) -> int:
    snap = build_snapshot()
    if not snap:
        log.error("no NDBC stations parsed (is %s present?) — refusing to overwrite the snapshot with "
                  "an empty map", "ndbc_stations.xml")
        return 0
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {k: snap[k] for k in sorted(snap)}
    path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n")
    log.info("wrote %d buoys to %s", len(ordered), path)
    return len(ordered)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--output", type=Path, default=NDBC_BUOY_SNAPSHOT_FILE)
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    n = write_snapshot(args.output)
    return 0 if n else 1


if __name__ == "__main__":
    sys.exit(main())
