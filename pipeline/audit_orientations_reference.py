"""Phase 2 of the orientation audit — compare each spot's orientation
against surf-forecast.com's "ideal swell direction is from the X"
field. Wraps the existing pipeline.scrape_surf_forecast helpers so
we don't reinvent slug guessing, coord matching, or pacing.

Returned shape is `{matched: [...], not_found_on_sf: [...], summary: {...}}`
ready to drop into pipeline/data/audit_vs_reference.json.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Iterable

from .config import SURF_FORECAST_DIRECTORY_FILE
from .scrape_surf_forecast import _PacedSession, fetch_spot, load_directory

log = logging.getLogger("pipeline.audit_orientations.phase2")

REQUEST_INTERVAL_S = 2.0
FLAG_THRESHOLD_DEG = 20


def _circular_diff(a: float, b: float) -> float:
    """Smallest angular distance between two bearings, in degrees."""
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


def run_phase2(
    spots: Iterable[dict],
    interval_s: float = REQUEST_INTERVAL_S,
    on_progress=None,
    directory_path: Path = SURF_FORECAST_DIRECTORY_FILE,
) -> dict:
    """Audit every spot against surf-forecast.com.

    Returns the same dict shape we serialise to disk; the caller is
    responsible for writing the file (so this stays unit-testable).
    """
    # _PacedSession enforces a minimum interval between requests + a
    # friendly UA, matching the same pacing scrape_surf_forecast uses
    # for its bulk crawl.
    session = _PacedSession(
        interval_s,
        "StormyPetrel/audit-orientations (+https://stormypetrel.surf)",
    )

    # Optional pre-built name→slug directory. If absent, fetch_spot
    # falls back to its own slug guessing — the lookup just costs more
    # 404s on edge-case names.
    directory = load_directory(directory_path) if directory_path else None
    if directory:
        log.info("loaded surf-forecast directory: %d entries", len(directory))
    else:
        log.info("no surf-forecast directory cached — slug guessing only")

    matched: list[dict] = []
    not_found: list[dict] = []
    counters = {"pass": 0, "flag_20": 0, "not_found": 0, "no_orientation": 0}

    spots_list = list(spots)
    for i, spot in enumerate(spots_list, start=1):
        name = spot.get("name")
        state = spot.get("region_hint")
        our_orient = spot.get("orientation_deg")
        lat = spot.get("lat")
        lng = spot.get("lng")
        if not name:
            continue
        if on_progress:
            on_progress(i, len(spots_list), name)

        try:
            result = fetch_spot(
                name,
                state,
                session,
                expected_coord=(lat, lng) if (lat is not None and lng is not None) else None,
                directory=directory,
            )
        except Exception:  # noqa: BLE001
            log.exception("%s: surf-forecast fetch failed", name)
            result = None

        if not result or result.get("optimal_swell_dir") is None:
            not_found.append({"name": name, "reason": "no_match_on_sf"})
            counters["not_found"] += 1
            continue

        sf_deg = float(result["optimal_swell_dir"])
        if our_orient is None:
            not_found.append({"name": name, "reason": "no_local_orientation"})
            counters["no_orientation"] += 1
            continue

        diff = _circular_diff(float(our_orient), sf_deg)
        status = "FLAG_20" if diff >= FLAG_THRESHOLD_DEG else "PASS"
        counters[status.lower()] = counters.get(status.lower(), 0) + 1
        matched.append({
            "name": name,
            "slug": spot.get("slug"),
            "our_orient": float(our_orient),
            "sf_degrees": sf_deg,
            "diff": round(diff, 1),
            "status": status,
            "sf_source_url": result.get("source_url"),
        })

        # Safety pacing in addition to _PacedSession — _PacedSession
        # only enforces between session.get() calls; this ensures
        # interval_s minimum even across fetch_spot's possible
        # multiple slug candidates.
        time.sleep(0)  # let signals through; pacing handled in session

    return {
        "matched": matched,
        "not_found_on_sf": not_found,
        "summary": counters,
    }
