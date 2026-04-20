"""Gapfill source: geocode a hand-curated spot list via Nominatim.

Reads pipeline/data/llm_spots.json (name + nearest_town + state for each spot)
and resolves each to a lat/lng via the OSM Nominatim API with a three-tier query
fallback. Results are cached on disk so re-runs skip the API entirely.

This source is intended to run LAST in the pipeline — any spots already seeded
by OSM/Wikidata/Wikipedia get collapsed into those richer records by the
existing dedupe (same QID, or within 500m + fuzzy name match).
"""
from __future__ import annotations

import json
import logging
import time

from ..config import (
    CACHE_DIR,
    GAPFILL_DATA_FILE,
    NOMINATIM_ENDPOINT,
    NOMINATIM_MIN_INTERVAL_S,
)
from ..http import get

log = logging.getLogger(__name__)

_CACHE_FILE = CACHE_DIR / "gapfill_geocoded.json"

# Confidence tiers recorded in tags.geocode_confidence.
_CONFIDENCE_EXACT = "exact"
_CONFIDENCE_STATE = "state"
_CONFIDENCE_TOWN = "town_fallback"
_CONFIDENCE_FAILED = "failed"


class _Pacer:
    """Single-threaded rate limiter for polite Nominatim use (1 req/s)."""

    def __init__(self, min_interval_s: float) -> None:
        self.min_interval_s = min_interval_s
        self._last = 0.0

    def wait(self) -> None:
        delta = time.monotonic() - self._last
        if delta < self.min_interval_s:
            time.sleep(self.min_interval_s - delta)
        self._last = time.monotonic()


def _cache_key(name: str, nearest_town: str) -> str:
    return f"{name}||{nearest_town}"


def _load_cache() -> dict[str, dict]:
    if not _CACHE_FILE.exists():
        return {}
    try:
        return json.loads(_CACHE_FILE.read_text())
    except json.JSONDecodeError as e:
        log.warning("gapfill: cache file %s is corrupt (%s); starting fresh", _CACHE_FILE, e)
        return {}


def _save_cache(cache: dict[str, dict]) -> None:
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def _flatten(data: dict) -> list[dict]:
    """Turn the regions → spots map into a flat list of {name, nearest_town, state}."""
    flat: list[dict] = []
    for region_name, region in data.get("regions", {}).items():
        state_varies = bool(region.get("state_varies"))
        region_state = region.get("state")
        for spot in region.get("spots", []):
            state = spot.get("state") if state_varies else region_state
            if not state:
                log.warning("gapfill: skipping %r in %s — no state", spot.get("name"), region_name)
                continue
            flat.append(
                {
                    "name": spot["name"],
                    "nearest_town": spot.get("nearest_town") or "",
                    "state": state,
                    "region": region_name,
                }
            )
    return flat


def _nominatim_search(query: str, pacer: _Pacer) -> dict | None:
    """Hit Nominatim /search with the given free-text query; return first result or None."""
    pacer.wait()
    params = {"q": query, "format": "json", "limit": 1}
    try:
        resp = get(NOMINATIM_ENDPOINT, params=params)
    except Exception as e:  # noqa: BLE001
        log.warning("gapfill: Nominatim error for %r: %s", query, e)
        return None
    try:
        results = resp.json()
    except ValueError as e:
        log.warning("gapfill: Nominatim returned non-JSON for %r: %s", query, e)
        return None
    if not results:
        return None
    return results[0]


def _geocode_spot(spot: dict, pacer: _Pacer) -> dict:
    """Try the three-tier cascade and return a cache record."""
    name = spot["name"]
    town = spot["nearest_town"]
    state = spot["state"]

    tiers = []
    if town:
        tiers.append((f"{name}, {town}", _CONFIDENCE_EXACT))
    tiers.append((f"{name}, {state}", _CONFIDENCE_STATE))
    if town:
        tiers.append((town, _CONFIDENCE_TOWN))

    for query, confidence in tiers:
        hit = _nominatim_search(query, pacer)
        if hit is None:
            continue
        try:
            lat = float(hit["lat"])
            lng = float(hit["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        return {
            "lat": lat,
            "lng": lng,
            "display_name": hit.get("display_name"),
            "query_used": query,
            "confidence": confidence,
        }

    return {
        "lat": None,
        "lng": None,
        "display_name": None,
        "query_used": None,
        "confidence": _CONFIDENCE_FAILED,
    }


def _to_candidate(spot: dict, geo: dict) -> dict | None:
    if geo.get("confidence") == _CONFIDENCE_FAILED or geo.get("lat") is None:
        return None
    return {
        "name": spot["name"],
        "lat": geo["lat"],
        "lng": geo["lng"],
        "source": "gapfill",
        "source_ids": {"osm_id": None, "wikidata_id": None, "wikipedia_url": None},
        "tags": {
            "geocode_confidence": geo["confidence"],
            "nominatim_query": geo.get("query_used") or "",
            "nominatim_display_name": geo.get("display_name") or "",
        },
        "region_hint": spot["state"],
    }


def fetch(use_cache: bool = True) -> list[dict]:
    if not GAPFILL_DATA_FILE.exists():
        log.warning("gapfill: data file %s missing; skipping", GAPFILL_DATA_FILE)
        return []

    try:
        data = json.loads(GAPFILL_DATA_FILE.read_text())
    except json.JSONDecodeError as e:
        log.error("gapfill: failed to parse %s: %s", GAPFILL_DATA_FILE, e)
        return []

    spots = _flatten(data)
    log.info("gapfill: loaded %d spots from %s", len(spots), GAPFILL_DATA_FILE.name)

    cache = _load_cache() if use_cache else {}
    pacer = _Pacer(NOMINATIM_MIN_INTERVAL_S)

    try:
        from tqdm import tqdm
        iterator = tqdm(spots, desc="gapfill: geocoding", unit="spot")
    except ImportError:
        iterator = spots

    failures = 0
    new_since_flush = 0
    confidence_counts: dict[str, int] = {}

    for spot in iterator:
        key = _cache_key(spot["name"], spot["nearest_town"])
        geo = cache.get(key)
        if geo is None:
            geo = _geocode_spot(spot, pacer)
            cache[key] = geo
            new_since_flush += 1
            if new_since_flush >= 25:
                _save_cache(cache)
                new_since_flush = 0
        confidence_counts[geo.get("confidence") or _CONFIDENCE_FAILED] = (
            confidence_counts.get(geo.get("confidence") or _CONFIDENCE_FAILED, 0) + 1
        )
        if geo.get("confidence") == _CONFIDENCE_FAILED:
            failures += 1
            log.warning(
                "gapfill: geocoding failed for %r (%s) — all three tiers returned no result",
                spot["name"], spot["nearest_town"],
            )

    if new_since_flush:
        _save_cache(cache)

    candidates: list[dict] = []
    for spot in spots:
        key = _cache_key(spot["name"], spot["nearest_town"])
        rec = _to_candidate(spot, cache.get(key, {}))
        if rec is not None:
            candidates.append(rec)

    log.info(
        "gapfill: emitted %d candidates (failures=%d, confidence=%s)",
        len(candidates), failures, confidence_counts,
    )
    return candidates
