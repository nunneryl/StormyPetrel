"""OpenStreetMap Overpass API — fetch US surf spots tagged sport=surfing or surfing=*."""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ..config import CACHE_DIR, OVERPASS_ENDPOINTS
from ..http import RetryableHTTPError, post

log = logging.getLogger(__name__)

_CACHE_FILE = CACHE_DIR / "osm_raw.json"

_QUERY = """
[out:json][timeout:180];
area["ISO3166-1"="US"][admin_level=2]->.us;
(
  node(area.us)["sport"="surfing"];
  way(area.us)["sport"="surfing"];
  relation(area.us)["sport"="surfing"];
  node(area.us)["surfing"];
  way(area.us)["surfing"];
  relation(area.us)["surfing"];
);
out center tags;
"""


def _fetch_raw() -> dict:
    """POST the Overpass query, falling back across endpoints on failure."""
    last_err: Exception | None = None
    for endpoint in OVERPASS_ENDPOINTS:
        try:
            log.info("Overpass: querying %s", endpoint)
            resp = post(endpoint, data={"data": _QUERY})
            return resp.json()
        except (RetryableHTTPError, Exception) as e:  # noqa: BLE001
            log.warning("Overpass endpoint %s failed: %s", endpoint, e)
            last_err = e
    raise RuntimeError(f"All Overpass endpoints failed: {last_err}")


def _load_or_fetch(use_cache: bool) -> dict:
    if use_cache and _CACHE_FILE.exists():
        log.info("OSM: loading cached response from %s", _CACHE_FILE)
        return json.loads(_CACHE_FILE.read_text())
    raw = _fetch_raw()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(raw))
    return raw


def _parse_element(el: dict) -> dict | None:
    tags = el.get("tags", {}) or {}
    name = tags.get("name") or tags.get("name:en")
    wikidata_id = tags.get("wikidata")
    if not name and not wikidata_id:
        return None

    if el.get("type") == "node":
        lat, lng = el.get("lat"), el.get("lon")
    else:
        center = el.get("center") or {}
        lat, lng = center.get("lat"), center.get("lon")
    if lat is None or lng is None:
        return None

    wikipedia_url: str | None = None
    wp = tags.get("wikipedia")
    if wp and ":" in wp:
        lang, title = wp.split(":", 1)
        wikipedia_url = f"https://{lang}.wikipedia.org/wiki/{title.replace(' ', '_')}"

    extra_tags = {k: v for k, v in tags.items() if k.startswith("surfing")}
    # Preserve a couple of useful generic tags too.
    for k in ("sport", "natural", "leisure"):
        if k in tags:
            extra_tags[k] = tags[k]

    return {
        "name": name or (wikidata_id or ""),
        "lat": float(lat),
        "lng": float(lng),
        "source": "osm",
        "source_ids": {
            "osm_id": f"{el['type']}/{el['id']}",
            "wikidata_id": wikidata_id,
            "wikipedia_url": wikipedia_url,
        },
        "tags": extra_tags,
        "region_hint": tags.get("addr:state") or None,
    }


def fetch(use_cache: bool = True) -> list[dict]:
    raw = _load_or_fetch(use_cache)
    elements = raw.get("elements", [])
    parsed: list[dict] = []
    for el in elements:
        try:
            rec = _parse_element(el)
            if rec is not None:
                parsed.append(rec)
        except Exception as e:  # noqa: BLE001
            log.warning("OSM: failed to parse element %s: %s", el.get("id"), e)
    log.info("OSM: parsed %d spots from %d elements", len(parsed), len(elements))
    return parsed
