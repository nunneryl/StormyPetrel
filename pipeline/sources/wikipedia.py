"""Wikipedia MediaWiki API — crawl Category:Surfing_locations_in_the_United_States."""
from __future__ import annotations

import json
import logging
import re
import time
from collections import deque
from typing import Iterable

from ..config import (
    CACHE_DIR,
    WIKIPEDIA_API_ENDPOINT,
    WIKIPEDIA_MAX_CATEGORY_DEPTH,
    WIKIPEDIA_MIN_INTERVAL_S,
    WIKIPEDIA_PAGES_PER_BATCH,
    WIKIPEDIA_ROOT_CATEGORIES,
)
from ..http import get

log = logging.getLogger(__name__)

_CACHE_FILE = CACHE_DIR / "wikipedia_raw.json"

_STATE_FROM_CATEGORY_RE = re.compile(r"Surfing locations in (.+)")


class _Pacer:
    """Simple single-threaded rate limiter."""

    def __init__(self, min_interval_s: float) -> None:
        self.min_interval_s = min_interval_s
        self._last = 0.0

    def wait(self) -> None:
        delta = time.monotonic() - self._last
        if delta < self.min_interval_s:
            time.sleep(self.min_interval_s - delta)
        self._last = time.monotonic()


def _api_get(params: dict, pacer: _Pacer) -> dict:
    pacer.wait()
    full = {"format": "json", "formatversion": "2", **params}
    resp = get(WIKIPEDIA_API_ENDPOINT, params=full)
    return resp.json()


def _crawl_categories(roots: tuple[str, ...], pacer: _Pacer) -> tuple[list[dict], dict[int, set[str]]]:
    """BFS through all root categories + subcategories.

    Returns (page records, pageid -> parent categories).
    Tries each root; logs a raw-response snippet when a root yields zero members.
    """
    seen_cats: set[str] = set()
    queue: deque[tuple[str, int]] = deque((root, 0) for root in roots)
    pages: dict[int, dict] = {}
    parents: dict[int, set[str]] = {}
    cats_yielding_members: set[str] = set()

    while queue:
        cat, depth = queue.popleft()
        if cat in seen_cats:
            continue
        seen_cats.add(cat)
        cat_member_count = 0
        cmcontinue: str | None = None
        first_response: dict | None = None
        while True:
            params = {
                "action": "query",
                "list": "categorymembers",
                "cmtitle": cat,
                "cmlimit": "500",
                "cmtype": "page|subcat",
            }
            if cmcontinue:
                params["cmcontinue"] = cmcontinue
            try:
                data = _api_get(params, pacer)
            except Exception as e:  # noqa: BLE001
                log.warning("Wikipedia: listing %s failed: %s", cat, e)
                break
            if first_response is None:
                first_response = data
            members = data.get("query", {}).get("categorymembers", []) or []
            cat_member_count += len(members)
            for m in members:
                if m.get("type") == "subcat" and depth < WIKIPEDIA_MAX_CATEGORY_DEPTH:
                    queue.append((m["title"], depth + 1))
                elif m.get("type") == "page":
                    pid = m["pageid"]
                    pages.setdefault(pid, {"pageid": pid, "title": m["title"]})
                    parents.setdefault(pid, set()).add(cat)
            cmcontinue = data.get("continue", {}).get("cmcontinue")
            if not cmcontinue:
                break

        if cat_member_count == 0 and depth == 0:
            snippet = json.dumps(first_response)[:400] if first_response else "(no response)"
            log.warning("Wikipedia: root %r returned 0 members. Raw: %s", cat, snippet)
        elif cat_member_count > 0:
            cats_yielding_members.add(cat)

    log.info(
        "Wikipedia: crawled %d categories, %d contained members, collected %d pages",
        len(seen_cats), len(cats_yielding_members), len(pages),
    )
    return list(pages.values()), parents


def _batched(seq: list, n: int) -> Iterable[list]:
    for i in range(0, len(seq), n):
        yield seq[i : i + n]


def _fetch_page_details(pages: list[dict], pacer: _Pacer) -> dict[int, dict]:
    """Fetch primary coordinates and wikibase_item (QID) for pages in batches."""
    details: dict[int, dict] = {}
    page_ids = [p["pageid"] for p in pages]
    try:
        from tqdm import tqdm
        batches = list(_batched(page_ids, WIKIPEDIA_PAGES_PER_BATCH))
        iterator = tqdm(batches, desc="Wikipedia coords", unit="batch")
    except ImportError:
        iterator = _batched(page_ids, WIKIPEDIA_PAGES_PER_BATCH)

    for batch in iterator:
        params = {
            "action": "query",
            "pageids": "|".join(str(x) for x in batch),
            "prop": "coordinates|pageprops",
            "coprimary": "primary",
            "ppprop": "wikibase_item",
        }
        try:
            data = _api_get(params, pacer)
        except Exception as e:  # noqa: BLE001
            log.warning("Wikipedia: details batch failed: %s", e)
            continue
        for page in data.get("query", {}).get("pages", []):
            details[page["pageid"]] = page
    return details


def _fetch_raw(use_cache: bool) -> dict:
    if use_cache and _CACHE_FILE.exists():
        log.info("Wikipedia: loading cached response from %s", _CACHE_FILE)
        return json.loads(_CACHE_FILE.read_text())

    pacer = _Pacer(WIKIPEDIA_MIN_INTERVAL_S)
    log.info("Wikipedia: crawling roots %s", list(WIKIPEDIA_ROOT_CATEGORIES))
    pages, parents = _crawl_categories(WIKIPEDIA_ROOT_CATEGORIES, pacer)
    log.info("Wikipedia: found %d pages, fetching coords + QIDs", len(pages))
    details = _fetch_page_details(pages, pacer)

    raw = {
        "pages": pages,
        "details": {str(k): v for k, v in details.items()},
        "parents": {str(k): sorted(v) for k, v in parents.items()},
    }
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _CACHE_FILE.write_text(json.dumps(raw))
    return raw


def _region_from_parents(parent_cats: list[str]) -> str | None:
    for cat in parent_cats:
        # strip "Category:" prefix, normalise underscores
        name = cat.split("Category:", 1)[-1].replace("_", " ")
        m = _STATE_FROM_CATEGORY_RE.match(name)
        if m:
            return m.group(1).strip()
    return None


def fetch(use_cache: bool = True) -> list[dict]:
    raw = _fetch_raw(use_cache)
    pages: list[dict] = raw["pages"]
    details: dict[str, dict] = raw.get("details", {})
    parents: dict[str, list[str]] = raw.get("parents", {})

    parsed: list[dict] = []
    for page in pages:
        pid = page["pageid"]
        det = details.get(str(pid)) or {}
        coords = det.get("coordinates") or []
        if not coords:
            continue
        primary = coords[0]
        lat = primary.get("lat")
        lng = primary.get("lon")
        if lat is None or lng is None:
            continue

        title = det.get("title") or page["title"]
        qid = (det.get("pageprops") or {}).get("wikibase_item")
        wikipedia_url = "https://en.wikipedia.org/wiki/" + title.replace(" ", "_")
        region = _region_from_parents(parents.get(str(pid), []))

        parsed.append(
            {
                "name": title,
                "lat": float(lat),
                "lng": float(lng),
                "source": "wikipedia",
                "source_ids": {
                    "osm_id": None,
                    "wikidata_id": qid,
                    "wikipedia_url": wikipedia_url,
                },
                "tags": {"wikipedia_title": title},
                "region_hint": region,
            }
        )
    log.info("Wikipedia: parsed %d pages with primary coordinates", len(parsed))
    return parsed
