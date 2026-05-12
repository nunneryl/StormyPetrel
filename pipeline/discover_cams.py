"""One-shot webcam discovery against the Windy Webcam API v3.

Walks every spot in spots_enriched.json, queries Windy for cams within
2 km of the spot's coordinates, and writes the merged result to
pipeline/data/cam_discovery.json. This is a *manual curation aid* —
the output isn't read by the frontend or by seed_cams; a human picks
the good entries out of cam_discovery.json and copies them into
cam_seed.json as link-mode cams.

Spots that already have an entry in cam_seed.json are skipped, so
re-runs only burn API quota on unexplored coastline.

CLI:
    python -m pipeline.discover_cams [--limit N] [--radius KM] [-v]

Env:
    WINDY_API_KEY — required. (Free tier; rate-limited.)

Output:
    pipeline/data/cam_discovery.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .db_import import _slugify

log = logging.getLogger("pipeline.discover_cams")

DEFAULT_SPOTS_INPUT = Path(__file__).parent / "spots_enriched.json"
DEFAULT_SEED_FILE   = Path(__file__).parent / "data" / "cam_seed.json"
DEFAULT_OUTPUT      = Path(__file__).parent / "data" / "cam_discovery.json"

WINDY_URL = "https://api.windy.com/webcams/api/v3/webcams"
# 1 second between calls — Windy's free tier publishes a per-minute
# limit; staying well under it makes the script kind to the API and to
# anyone else using the same key.
RATE_LIMIT_SECONDS = 1.0
REQUEST_TIMEOUT = 20


def _load_json(path: Path) -> list | dict:
    if not path.exists():
        return [] if path.suffix == ".json" else {}
    return json.loads(path.read_text())


def _load_seed_slugs(seed_path: Path) -> set[str]:
    """Slugs already covered by cam_seed.json — we skip these."""
    if not seed_path.exists():
        return set()
    payload = json.loads(seed_path.read_text())
    entries = payload.get("cams") if isinstance(payload, dict) else payload
    return {e["spot_slug"] for e in entries or [] if e.get("spot_slug")}


def _build_query(lat: float, lng: float, radius_km: float) -> str:
    """Windy's nearby filter is `lat,lng,radius_km`."""
    return urlencode({
        "nearby": f"{lat:.6f},{lng:.6f},{radius_km:g}",
        "category": "beach",
        "include": "location,urls",
        "limit": "20",
    })


def _fetch(api_key: str, query: str) -> dict:
    req = Request(
        f"{WINDY_URL}?{query}",
        headers={
            "x-windy-api-key": api_key,
            "Accept": "application/json",
            "User-Agent": "StormyPetrel/discover-cams",
        },
    )
    with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return json.loads(resp.read())


def _normalize_cam(c: dict) -> dict:
    """Project Windy's response into the shape we want in our file."""
    loc = c.get("location") or {}
    urls = c.get("urls") or {}
    return {
        "windy_id": c.get("webcamId") or c.get("id"),
        "title": c.get("title"),
        "lat": loc.get("latitude"),
        "lng": loc.get("longitude"),
        "distance_m": c.get("distance"),
        "provider_url": (urls.get("provider") or [{}])[0].get("href")
                       if isinstance(urls.get("provider"), list)
                       else urls.get("provider"),
        "windy_url": (
            f"https://www.windy.com/webcams/{c.get('webcamId') or c.get('id')}"
            if c.get("webcamId") or c.get("id") else None
        ),
        "status": c.get("status"),
    }


def discover(
    spots: list[dict],
    seed_slugs: set[str],
    api_key: str,
    radius_km: float,
    limit: int | None,
) -> tuple[list[dict], dict]:
    """Walk spots, return (results, counters)."""
    out: list[dict] = []
    counters = {"checked": 0, "skipped_seeded": 0, "skipped_no_slug": 0,
                "api_errors": 0, "spots_with_cams": 0, "total_cams": 0}

    processed = 0
    for spot in spots:
        name = spot.get("name")
        lat = spot.get("lat")
        lng = spot.get("lng")
        if not name or lat is None or lng is None:
            continue
        slug = _slugify(name)
        if not slug:
            counters["skipped_no_slug"] += 1
            continue
        if slug in seed_slugs:
            counters["skipped_seeded"] += 1
            continue

        if limit is not None and processed >= limit:
            log.info("limit %d reached — stopping", limit)
            break

        # Polite rate limit. Honor it BEFORE the first call too if we
        # care about hammering on retries, but a single 1s wait between
        # calls is what we promised in the docstring.
        if processed > 0:
            time.sleep(RATE_LIMIT_SECONDS)
        processed += 1
        counters["checked"] += 1

        try:
            query = _build_query(lat, lng, radius_km)
            payload = _fetch(api_key, query)
        except HTTPError as e:
            log.warning("%s: HTTP %d %s", slug, e.code, e.reason)
            counters["api_errors"] += 1
            continue
        except (URLError, TimeoutError) as e:
            log.warning("%s: network error %s", slug, e)
            counters["api_errors"] += 1
            continue
        except Exception:  # noqa: BLE001
            log.exception("%s: unexpected error", slug)
            counters["api_errors"] += 1
            continue

        webcams = payload.get("webcams") or []
        if not webcams:
            log.debug("%s: 0 cams within %skm", slug, radius_km)
            continue

        normalized = [_normalize_cam(c) for c in webcams]
        out.append({
            "spot_slug": slug,
            "spot_name": name,
            "spot_lat": lat,
            "spot_lng": lng,
            "nearby_cams": normalized,
        })
        counters["spots_with_cams"] += 1
        counters["total_cams"] += len(normalized)
        log.info("%s: %d cam(s)", slug, len(normalized))

    return out, counters


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input", type=Path, default=DEFAULT_SPOTS_INPUT,
                   help="spots_enriched.json")
    p.add_argument("--seed", type=Path, default=DEFAULT_SEED_FILE,
                   help="cam_seed.json (slugs in here are skipped)")
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    p.add_argument("--radius", type=float, default=2.0,
                   help="Search radius in km (default 2).")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap on spots to query (dev — keep API quota low).")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    api_key = os.environ.get("WINDY_API_KEY")
    if not api_key:
        log.error("WINDY_API_KEY is required")
        return 1

    spots = _load_json(args.input)
    if not isinstance(spots, list) or not spots:
        log.error("no spots loaded from %s", args.input)
        return 2

    seed_slugs = _load_seed_slugs(args.seed)
    log.info("loaded %d spots; %d already covered by seed",
             len(spots), len(seed_slugs))

    started = datetime.now(timezone.utc)
    results, counters = discover(spots, seed_slugs, api_key,
                                 radius_km=args.radius, limit=args.limit)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "generated_at": started.isoformat(),
        "radius_km": args.radius,
        "totals": counters,
        "spots": results,
    }, indent=2))

    log.info(
        "done in %.1fs — checked=%d spots_with_cams=%d total_cams=%d "
        "skipped_seeded=%d api_errors=%d → wrote %s",
        elapsed, counters["checked"], counters["spots_with_cams"],
        counters["total_cams"], counters["skipped_seeded"],
        counters["api_errors"], args.output,
    )
    return 0 if counters["api_errors"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
