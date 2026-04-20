"""Wikidata SPARQL — fetch all items of class Q1066670 (surf break) located in the US."""
from __future__ import annotations

import json
import logging
import re

from ..config import CACHE_DIR, WIKIDATA_SPARQL_ENDPOINT
from ..http import get

log = logging.getLogger(__name__)

_CACHE_FILE = CACHE_DIR / "wikidata_raw.json"

_SPARQL = """
SELECT DISTINCT ?item ?itemLabel ?coord ?stateLabel WHERE {
  ?item wdt:P31/wdt:P279* wd:Q1066670 ;
        wdt:P625 ?coord .
  { ?item wdt:P17 wd:Q30 }
  UNION
  { ?item wdt:P131+ ?container . ?container wdt:P17 wd:Q30 . }
  OPTIONAL { ?item wdt:P131 ?state . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en". }
}
"""

_POINT_RE = re.compile(r"Point\(\s*(-?\d+(?:\.\d+)?)\s+(-?\d+(?:\.\d+)?)\s*\)")
_QID_RE = re.compile(r"(Q\d+)$")


def _fetch_raw() -> dict:
    log.info("Wikidata: querying SPARQL endpoint")
    resp = get(
        WIKIDATA_SPARQL_ENDPOINT,
        params={"query": _SPARQL, "format": "json"},
        headers={"Accept": "application/sparql-results+json"},
    )
    return resp.json()


def _load_or_fetch(use_cache: bool) -> dict:
    if use_cache and _CACHE_FILE.exists():
        log.info("Wikidata: loading cached response from %s", _CACHE_FILE)
        return json.loads(_CACHE_FILE.read_text())
    raw = _fetch_raw()
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(raw))
    return raw


def _parse_binding(b: dict) -> dict | None:
    item_uri = b.get("item", {}).get("value", "")
    qid_match = _QID_RE.search(item_uri)
    if not qid_match:
        return None
    qid = qid_match.group(1)

    coord = b.get("coord", {}).get("value", "")
    m = _POINT_RE.match(coord)
    if not m:
        return None
    lng, lat = float(m.group(1)), float(m.group(2))

    label = b.get("itemLabel", {}).get("value") or qid
    state = b.get("stateLabel", {}).get("value") or None
    # If stateLabel falls back to the QID string, treat as missing.
    if state and state.startswith("Q") and state[1:].isdigit():
        state = None

    return {
        "name": label,
        "lat": lat,
        "lng": lng,
        "source": "wikidata",
        "source_ids": {"osm_id": None, "wikidata_id": qid, "wikipedia_url": None},
        "tags": {"wikidata_label": label},
        "region_hint": state,
    }


def fetch(use_cache: bool = True) -> list[dict]:
    raw = _load_or_fetch(use_cache)
    bindings = raw.get("results", {}).get("bindings", [])
    # Multiple P131 values produce duplicate bindings for the same QID; dedupe here.
    by_qid: dict[str, dict] = {}
    for b in bindings:
        try:
            rec = _parse_binding(b)
        except Exception as e:  # noqa: BLE001
            log.warning("Wikidata: failed to parse binding: %s", e)
            continue
        if rec is None:
            continue
        qid = rec["source_ids"]["wikidata_id"]
        existing = by_qid.get(qid)
        if existing is None:
            by_qid[qid] = rec
        elif not existing.get("region_hint") and rec.get("region_hint"):
            existing["region_hint"] = rec["region_hint"]
    if not by_qid:
        log.warning(
            "Wikidata: returned 0 surf breaks. US surf spots are sparsely tagged on "
            "Wikidata (P31=Q1066670 or subclasses) — this is expected; continuing "
            "with the other sources.",
        )
    else:
        log.info("Wikidata: parsed %d unique surf breaks", len(by_qid))
    return list(by_qid.values())
