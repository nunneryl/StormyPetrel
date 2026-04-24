"""Scrape surf-forecast.com spot pages for verified metadata.

Three primitives (slug_candidates / parse_spot_page / fetch_spot) compose
into a rate-limited, cached pipeline driven by the CLI below. The CLI:

  - loads spots_enriched.json
  - looks up each valid spot on surf-forecast.com, pacing at
    SURF_FORECAST_MIN_INTERVAL_S between every HTTP request
  - writes results progressively to SURF_FORECAST_CACHE_FILE so a crash
    doesn't lose work; re-runs skip spots already in the cache
  - merges matched results back into spots_enriched.json
    (orientation_deg / offshore_wind_deg / optimal_swell_dir /
    break_type / tide_preference)

CLI:
    python -m pipeline.scrape_surf_forecast
    python -m pipeline.scrape_surf_forecast --merge-only   # skip HTTP
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    DEFAULT_ENRICHED_OUTPUT,
    SURF_FORECAST_BASE,
    SURF_FORECAST_CACHE_FILE,
    SURF_FORECAST_DIRECTORY_FILE,
    SURF_FORECAST_FUZZY_THRESHOLD,
    SURF_FORECAST_MIN_INTERVAL_S,
)
from .geo import haversine_m

_USER_AGENT = "StormyPetrel/0.1 (surf forecast project)"

# Default radius within which a surf-forecast.com page's own lat/lng must
# fall for its match to be trusted. Generous because some pages publish
# the nearest-town coord rather than the break itself, but tight enough
# to reject the "Pillar Point" → different state / different break
# class of false positive that the previous slug-only matcher produced.
_DEFAULT_MAX_DISTANCE_KM = 20.0

log = logging.getLogger(__name__)

# 16-point compass → degrees. Built from base lists so every text
# variation surf-forecast.com alternates between ("NNE" / "north-northeast"
# / "north northeast") maps to the same degree.
_ABBRS = ["n", "nne", "ne", "ene", "e", "ese", "se", "sse",
          "s", "ssw", "sw", "wsw", "w", "wnw", "nw", "nnw"]
_FULLS = ["north", "north-northeast", "northeast", "east-northeast",
          "east", "east-southeast", "southeast", "south-southeast",
          "south", "south-southwest", "southwest", "west-southwest",
          "west", "west-northwest", "northwest", "north-northwest"]
_CARDINAL_TO_DEG: dict[str, float] = {}
for _i, (_abbr, _full) in enumerate(zip(_ABBRS, _FULLS)):
    _deg = _i * 22.5
    _CARDINAL_TO_DEG[_abbr] = _deg
    _CARDINAL_TO_DEG[_full] = _deg
    _CARDINAL_TO_DEG[_full.replace("-", " ")] = _deg
# Also accept the hyphenated primary intercardinals which _FULLS spells
# as solid words ("northeast" etc).
_CARDINAL_TO_DEG.update({
    "north-east": 45.0, "south-east": 135.0,
    "south-west": 225.0, "north-west": 315.0,
})


def _direction_to_deg(text: str) -> float | None:
    """Normalize a cardinal phrase to degrees, or None if unrecognized."""
    if not text:
        return None
    t = re.sub(r"\s+", " ", text.lower().strip().rstrip(".,;:"))
    return _CARDINAL_TO_DEG.get(t)


def _slugify(s: str) -> str:
    """Title-case-preserving slug: strip punctuation, space→hyphen, collapse."""
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE)
    s = re.sub(r"\s+", "-", s.strip())
    return re.sub(r"-+", "-", s).strip("-")


def slug_candidates(name: str, state: str | None = None) -> list[str]:
    """Return URL slug variants for surf-forecast.com ``/breaks/<slug>``.

    Slugs sometimes match an abbreviated form of a longer OSM/Wikipedia
    name, so we try progressively-shorter prefixes down to the first
    word, then state-qualified variants.
    """
    if not name or not name.strip():
        return []

    out: list[str] = []
    seen: set[str] = set()

    def _add(slug: str) -> None:
        if slug and slug not in seen:
            seen.add(slug)
            out.append(slug)

    words = name.split()
    for i in range(len(words), 1, -1):
        _add(_slugify(" ".join(words[:i])))
    if words:
        _add(_slugify(words[0]))
    if state:
        _add(_slugify(f"{name} {state}"))
        _add(_slugify(f"{name}-{state}"))
    return out


def _find_geo_coords(obj) -> tuple[float, float] | None:
    """Walk a JSON-LD node recursively for {"latitude": ..., "longitude": ...}."""
    if isinstance(obj, dict):
        lat = obj.get("latitude")
        lng = obj.get("longitude")
        if lat is not None and lng is not None:
            try:
                return float(lat), float(lng)
            except (ValueError, TypeError):
                pass
        for v in obj.values():
            r = _find_geo_coords(v)
            if r:
                return r
    elif isinstance(obj, list):
        for item in obj:
            r = _find_geo_coords(item)
            if r:
                return r
    return None


def extract_page_coords(html: str) -> tuple[float, float] | None:
    """Return (lat, lng) if surf-forecast.com published them on this page.

    surf-forecast.com emits two coord formats on every spot page — prefer
    the embedded JS widget (4-decimal precision) over the JSON-LD block
    (2-decimal precision). Falls back to OG meta tags and a generic
    regex. Returns None only when the page genuinely has no coord
    (index / search / 404 stubs).
    """
    from bs4 import BeautifulSoup

    # 1. Embedded spot-locator JS widget: "lat":36.6289,"lng":-121.9412
    #    (appears on every spot page with ~10m precision.)
    m = re.search(
        r'"lat"\s*:\s*(-?\d+\.\d+)\s*,\s*"lng"\s*:\s*(-?\d+\.\d+)',
        html,
    )
    if m:
        try:
            return float(m.group(1)), float(m.group(2))
        except ValueError:
            pass

    soup = BeautifulSoup(html, "html.parser")

    # 2. JSON-LD Place / GeoCoordinates — reliable schema.org markup.
    #    Use script.get_text() rather than script.string; .string returns
    #    None whenever the <script> has mixed/fragmented content, which
    #    was silently skipping almost every page in practice.
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        content = (script.get_text() or "").strip()
        if not content:
            continue
        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            continue
        coords = _find_geo_coords(data)
        if coords:
            return coords

    # 3. OG / place:location meta tags (unused by surf-forecast.com but
    #    handy for future sources).
    lat_tag = soup.find("meta", attrs={"property": re.compile(r"(?:og|place:location):latitude")})
    lng_tag = soup.find("meta", attrs={"property": re.compile(r"(?:og|place:location):longitude")})
    if lat_tag and lng_tag:
        try:
            return float(lat_tag["content"]), float(lng_tag["content"])
        except (KeyError, ValueError, TypeError):
            pass

    # 4. Generic fallback — matches both unquoted ("lat": 36.6) and
    #    quoted ("latitude":"36.63") forms. Requires both sides to parse.
    m_lat = re.search(r'["\']?lat(?:itude)?["\']?\s*[:=]\s*["\']?(-?\d+\.\d+)', html)
    m_lng = re.search(r'["\']?l(?:ng|ongitude|on)["\']?\s*[:=]\s*["\']?(-?\d+\.\d+)', html)
    if m_lat and m_lng:
        try:
            return float(m_lat.group(1)), float(m_lng.group(1))
        except ValueError:
            pass

    return None


def parse_spot_page(html: str) -> dict:
    """Extract surf metadata from a surf-forecast.com spot page.

    Returns a dict with seven keys; any extractor that can't find its
    pattern leaves the value None (empty string for free-form crowd /
    hazards). The caller decides whether a sparse result counts as a
    successful match. ``page_lat`` / ``page_lng`` are the coordinates
    surf-forecast.com publishes for the break, used downstream to
    reject slug matches that point at the wrong spot.
    """
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ", strip=True)
    lower = text.lower()

    fields: dict = {
        "offshore_wind_deg": None,
        "optimal_swell_dir": None,
        "break_type": None,
        "tide_preference": None,
        "crowd": None,
        "hazards": None,
        "page_lat": None,
        "page_lng": None,
    }
    coords = extract_page_coords(html)
    if coords:
        fields["page_lat"], fields["page_lng"] = coords

    m = re.search(
        r"offshore winds?\s+(?:blow|are|come)\s+from\s+the\s+([\w\s-]+?)(?=[\.,;]|\s+and\s)",
        lower,
    )
    if m:
        fields["offshore_wind_deg"] = _direction_to_deg(m.group(1))

    m = re.search(
        r"ideal swell direction is from the\s+([\w\s-]+?)(?=[\.,;]|\s+and\s)",
        lower,
    )
    if m:
        fields["optimal_swell_dir"] = _direction_to_deg(m.group(1))

    for bt in ("beach break", "reef break", "point break", "jetty break"):
        if bt in lower:
            fields["break_type"] = bt.split()[0]
            break

    if re.search(r"at all stages|works at all tides|all\s+tides?\b", lower):
        fields["tide_preference"] = "all"
    else:
        for tp in ("low", "mid", "high"):
            if re.search(rf"best\s+(?:around|at)\s+{tp}\s+tide", lower):
                fields["tide_preference"] = tp
                break

    # Free-form sentence extractors — preserve the whole sentence so a
    # later merge can classify into heavy/moderate/light etc.
    for key, pattern in (
        ("crowd", r"([^\.\n]*?(?:crowd|lineup|busy|uncrowded|empty)[^\.\n]*\.)"),
        ("hazards", r"((?:beware|watch\s+out|hazards?)[^\.\n]*\.)"),
    ):
        m = re.search(pattern, text, flags=re.IGNORECASE)
        if m:
            fields[key] = m.group(1).strip()

    return fields


def fetch_spot(
    name: str,
    state: str | None,
    session,
    expected_coord: tuple[float, float] | None = None,
    max_distance_km: float = _DEFAULT_MAX_DISTANCE_KM,
    directory: dict | None = None,
) -> dict | None:
    """Try each slug candidate; return the first page that parses to a
    useful record. A 200 without offshore_wind_deg OR break_type is
    treated as a miss (disambiguation / error / unrelated page).

    When ``expected_coord`` is supplied, pages whose published lat/lng
    sit more than ``max_distance_km`` from it are also treated as
    misses — this rejects the "Pillar Point" → wrong-break-same-name
    class of slug collision. Pages that publish no coord fall through
    the check unchanged (trust the slug).

    When *directory* is supplied (from build_directory), the best fuzzy
    name match is prepended to the slug-candidate list — so spots whose
    naive hyphen-join slugs 404 still get a chance via the canonical
    slug surf-forecast.com actually uses.
    """
    candidates: list[str] = []
    if directory is not None:
        dir_slug = match_directory(name, directory)
        if dir_slug:
            candidates.append(dir_slug)
    for c in slug_candidates(name, state):
        if c not in candidates:
            candidates.append(c)

    for slug in candidates:
        url = f"{SURF_FORECAST_BASE}/breaks/{slug}"
        try:
            resp = session.get(url, timeout=30, allow_redirects=True)
        except Exception as e:  # noqa: BLE001
            log.debug("fetch failed %s: %s", url, e)
            continue
        if resp.status_code != 200:
            continue
        fields = parse_spot_page(resp.text)
        if fields.get("offshore_wind_deg") is None and fields.get("break_type") is None:
            continue

        if expected_coord is not None and fields.get("page_lat") is not None:
            plat, plng = fields["page_lat"], fields["page_lng"]
            elat, elng = expected_coord
            dist_km = haversine_m(elat, elng, plat, plng) / 1000.0
            if dist_km > max_distance_km:
                log.info(
                    "%s: %s matched but coords %.1f km apart (> %.1f cap) — skipping",
                    name, url, dist_km, max_distance_km,
                )
                continue
            fields["match_distance_km"] = round(dist_km, 3)

        fields["source_url"] = resp.url
        fields["matched_slug"] = slug
        return fields
    return None


# ---------------------------------------------------------------------------
# Directory — crawl surf-forecast.com's listing pages to build a canonical
# {name: slug} index, then fuzzy-match spots against it. Lets us catch
# spots where our naive hyphen-join-the-name guesses all 404.
# ---------------------------------------------------------------------------

# Starting points for the directory crawl. surf-forecast.com groups US
# breaks under /provinces/<State>-USA/breaks — not /countries/USA/... as
# a reasonable person would guess. Each state's /breaks page lists every
# break in that state; the crawler follows secondary /provinces/ and
# /countries/ links for territories and edge cases. Territories
# (Puerto Rico, Guam) are provinces too. Homepage is a fallback for
# stragglers.
_US_STATE_SLUGS = (
    "Alabama", "Alaska", "California", "Connecticut", "Delaware", "Florida",
    "Georgia", "Guam", "Hawaii", "Illinois", "Indiana", "Louisiana", "Maine",
    "Maryland", "Massachusetts", "Michigan", "Minnesota", "Mississippi",
    "New-Hampshire", "New-Jersey", "New-York", "North-Carolina", "Ohio",
    "Oregon", "Pennsylvania", "Puerto-Rico", "Rhode-Island", "South-Carolina",
    "Texas", "Virginia", "Washington", "Wisconsin",
)

_DEFAULT_CRAWL_SEEDS = tuple(
    [f"{SURF_FORECAST_BASE}/provinces/{s}-USA/breaks" for s in _US_STATE_SLUGS]
    + [f"{SURF_FORECAST_BASE}/countries", f"{SURF_FORECAST_BASE}/"]
)

_BREAK_HREF_RE = re.compile(r'/breaks/([^/"\?#\s]+)')


def _normalize_for_match(name: str | None) -> str:
    """Lower-case + strip common noise words so fuzzy matches are robust to
    phrasing differences ("State Beach" / "Beach Park" / "Point" suffixes).
    """
    if not name:
        return ""
    # Fold the same Unicode apostrophe variants we normalize elsewhere.
    from .cleanup_spots import normalize_name
    name = normalize_name(name).lower()
    # Drop a handful of low-signal suffix words that otherwise suppress
    # token_set_ratio when the directory uses a shorter form.
    name = re.sub(r"\b(state|beach|park|point|surf|break|breaks)\b", " ", name)
    return re.sub(r"\s+", " ", name).strip()


def _extract_break_slugs(html: str) -> set[str]:
    """Scan a page body for every unique ``/breaks/<slug>`` reference."""
    slugs: set[str] = set()
    for m in _BREAK_HREF_RE.finditer(html):
        slug = m.group(1)
        # Skip obvious non-break resources (forecasts, photos, reviews).
        if slug in {"forecasts", "photos", "reviews", "tides", "metars", "buoys",
                    "webcams", "seatemp", "alert_conditions"}:
            continue
        # Skip suffix paths like "breaks/Pipeline/forecasts" — we want root slugs.
        slugs.add(slug)
    return slugs


def build_directory(
    session,
    seed_urls: list[str] | None = None,
    max_pages: int = 500,
) -> dict:
    """Crawl surf-forecast.com listing pages and return a directory of every
    ``/breaks/<slug>`` link we find.

    Breadth-first; each fetched page contributes both new break slugs (saved
    into the directory) and new crawl candidates (other country/region index
    pages) up to *max_pages* total fetches. The caller supplies the session
    so rate-limiting works.
    """
    from urllib.parse import urljoin, urlparse

    seeds = list(seed_urls or _DEFAULT_CRAWL_SEEDS)
    visited: set[str] = set()
    to_visit: list[str] = list(seeds)
    entries: dict[str, dict] = {}  # slug → {slug, url, display}

    while to_visit and len(visited) < max_pages:
        url = to_visit.pop(0)
        if url in visited:
            continue
        visited.add(url)
        try:
            resp = session.get(url, timeout=30, allow_redirects=True)
        except Exception as e:  # noqa: BLE001
            log.debug("directory: fetch %s failed: %s", url, e)
            continue
        if resp.status_code != 200 or not resp.text:
            log.debug("directory: %s → %d", url, resp.status_code)
            continue

        for slug in _extract_break_slugs(resp.text):
            if slug in entries:
                continue
            # Replace hyphens with spaces for display; URL slug is the truth.
            display = slug.replace("-", " ")
            entries[slug] = {
                "slug": slug,
                "url": f"{SURF_FORECAST_BASE}/breaks/{slug}",
                "display": display,
                "normalized": _normalize_for_match(display),
            }

        # Follow any country/region index links we see on this page, up to
        # the page cap. Conservative: only follow in-site links under
        # /countries/ or /regions/, not every href.
        from bs4 import BeautifulSoup
        soup = BeautifulSoup(resp.text, "html.parser")
        for a in soup.find_all("a", href=True):
            href = a["href"]
            if not href or href.startswith("#"):
                continue
            absolute = urljoin(url, href)
            if urlparse(absolute).netloc and urlparse(absolute).netloc != urlparse(SURF_FORECAST_BASE).netloc:
                continue
            if "/countries/" in absolute or "/regions/" in absolute or "/provinces/" in absolute or "sitemap" in absolute:
                if absolute not in visited and absolute not in to_visit:
                    to_visit.append(absolute)

    log.info(
        "directory: crawled %d pages, collected %d break entries",
        len(visited), len(entries),
    )
    return {
        "crawled_at": datetime.now(tz=timezone.utc).isoformat(),
        "source_urls": seeds,
        "pages_crawled": len(visited),
        "entries": list(entries.values()),
    }


def save_directory(path: Path, directory: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(directory, indent=2, ensure_ascii=False, sort_keys=True))


def load_directory(path: Path = SURF_FORECAST_DIRECTORY_FILE) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        log.warning("directory %s corrupt (%s); ignoring", path, e)
        return None


def match_directory(
    name: str,
    directory: dict,
    threshold: int = SURF_FORECAST_FUZZY_THRESHOLD,
) -> str | None:
    """Return the best-matching slug from *directory*, or None below threshold.

    Uses rapidfuzz token_set_ratio so ordering differences ("Ocean Beach" ↔
    "Beach at Ocean") don't kill the match. Suffix noise like "State Beach"
    is stripped by _normalize_for_match on both sides.
    """
    entries = directory.get("entries") or []
    if not entries:
        return None
    from rapidfuzz import fuzz, process
    target = _normalize_for_match(name)
    if not target:
        return None
    choices = [e["normalized"] for e in entries]
    best = process.extractOne(target, choices, scorer=fuzz.token_set_ratio)
    if not best:
        return None
    score = best[1]
    idx = best[2]
    if score < threshold:
        return None
    return entries[idx]["slug"]


# ---------------------------------------------------------------------------
# CLI — rate-limited batch scrape + merge into spots_enriched.json
# ---------------------------------------------------------------------------

class _PacedSession:
    """Requests-session wrapper that enforces a minimum interval between
    every GET — so fetch_spot's internal candidate loop still respects
    the rate limit without needing to know about it.
    """

    def __init__(self, min_interval_s: float, user_agent: str) -> None:
        import requests  # lazy — keeps the module importable without requests
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent
        self._min_interval = min_interval_s
        self._last = 0.0

    def get(self, url: str, **kwargs):
        delta = time.monotonic() - self._last
        if delta < self._min_interval:
            time.sleep(self._min_interval - delta)
        self._last = time.monotonic()
        return self._session.get(url, **kwargs)


def _load_cache(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        log.warning("cache %s corrupt (%s); starting fresh", path, e)
        return {}


def _save_cache(path: Path, cache: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cache, indent=2, ensure_ascii=False, sort_keys=True))


def _is_scrapable(spot: dict) -> bool:
    """Skip unnamed and is_valid_surf_spot=false entries."""
    return bool(spot.get("name")) and spot.get("is_valid_surf_spot") is not False


def scrape_all(
    spots: list[dict],
    cache_path: Path,
    use_cache: bool = True,
    min_interval_s: float = SURF_FORECAST_MIN_INTERVAL_S,
    user_agent: str = _USER_AGENT,
    max_distance_km: float = _DEFAULT_MAX_DISTANCE_KM,
    directory: dict | None = None,
) -> tuple[dict[str, dict], dict]:
    """Scrape each spot not already in the cache; persist after every spot.

    Misses are cached with ``source_url: None`` so subsequent runs don't
    re-probe them. Pass ``use_cache=False`` to force a full rescrape.
    Each fetch is validated against the spot's lat/lng — matches whose
    surf-forecast.com page coord is more than ``max_distance_km`` away
    are rejected.

    When *directory* is supplied, every fetch_spot call gets a fuzzy-
    matched canonical slug prepended to its candidate list — catching
    spots whose naive hyphen-join slugs 404.
    """
    cache = _load_cache(cache_path) if use_cache else {}
    pending = [s for s in spots if _is_scrapable(s) and s["name"] not in cache]
    skipped_invalid = sum(1 for s in spots if not _is_scrapable(s))
    log.info(
        "scrape: %d spots total, %d cached, %d pending, %d skipped (invalid/unnamed)",
        len(spots), len(spots) - len(pending) - skipped_invalid, len(pending),
        skipped_invalid,
    )

    stats = {"matched": 0, "missed": 0, "errors": 0, "requests": 0}
    if not pending:
        return cache, stats

    session = _PacedSession(min_interval_s, user_agent)

    try:
        from tqdm import tqdm
        iterator = tqdm(pending, desc="scrape surf-forecast", unit="spot")
    except ImportError:
        iterator = pending

    now = lambda: datetime.now(tz=timezone.utc).isoformat()

    for spot in iterator:
        name = spot["name"]
        expected = None
        if spot.get("lat") is not None and spot.get("lng") is not None:
            expected = (float(spot["lat"]), float(spot["lng"]))
        try:
            result = fetch_spot(
                name, spot.get("region_hint"), session,
                expected_coord=expected, max_distance_km=max_distance_km,
                directory=directory,
            )
        except Exception as e:  # noqa: BLE001
            log.warning("scrape: %r raised %s; recording as error", name, e)
            cache[name] = {"name": name, "source_url": None, "error": str(e)[:200],
                           "scraped_at": now()}
            stats["errors"] += 1
            _save_cache(cache_path, cache)
            continue

        if result is None:
            cache[name] = {"name": name, "source_url": None, "scraped_at": now()}
            stats["missed"] += 1
        else:
            cache[name] = {"name": name, "scraped_at": now(), **result}
            stats["matched"] += 1

        _save_cache(cache_path, cache)

    return cache, stats


def revalidate_cached_matches(
    spots: list[dict],
    cache: dict[str, dict],
    cache_path: Path,
    max_distance_km: float = _DEFAULT_MAX_DISTANCE_KM,
    min_interval_s: float = SURF_FORECAST_MIN_INTERVAL_S,
    user_agent: str = _USER_AGENT,
) -> dict:
    """Re-fetch every matched cache entry and drop matches whose page
    coordinates fall more than ``max_distance_km`` from the spot.

    Useful after enabling coord validation: existing cache entries were
    written before the check existed, and the audit shows ~30 %
    disagreement between scraped and computed orientations driven by
    wrong-slug matches. Non-matched entries (``source_url: None``) are
    left alone; pages that publish no coord are trusted as before.
    """
    spot_coords = {
        s["name"]: (float(s["lat"]), float(s["lng"]))
        for s in spots
        if s.get("name") and s.get("lat") is not None and s.get("lng") is not None
    }
    to_check = [(name, rec) for name, rec in cache.items() if rec.get("source_url")]
    log.info("revalidate: %d matched entries to re-fetch", len(to_check))

    stats = {"rechecked": 0, "dropped": 0, "kept": 0, "unknown_coord": 0, "errors": 0}
    if not to_check:
        return stats

    session = _PacedSession(min_interval_s, user_agent)
    try:
        from tqdm import tqdm
        iterator = tqdm(to_check, desc="revalidate", unit="spot")
    except ImportError:
        iterator = to_check

    for name, rec in iterator:
        expected = spot_coords.get(name)
        if expected is None:
            stats["unknown_coord"] += 1
            continue
        url = rec["source_url"]
        try:
            resp = session.get(url, timeout=30)
        except Exception as e:  # noqa: BLE001
            log.warning("revalidate %s: GET failed: %s", name, e)
            stats["errors"] += 1
            continue
        stats["rechecked"] += 1
        if resp.status_code != 200:
            cache[name] = {
                "name": name, "source_url": None,
                "scraped_at": rec.get("scraped_at"),
                "previously_matched_url": url,
                "revalidation_status": resp.status_code,
            }
            stats["dropped"] += 1
            _save_cache(cache_path, cache)
            continue
        coords = extract_page_coords(resp.text)
        if coords is None:
            # No coord published — can't validate. Preserve the existing match
            # but stamp page_lat/lng=None so later runs still see the attempt.
            rec["page_lat"] = None
            rec["page_lng"] = None
            rec["match_distance_km"] = None
            stats["kept"] += 1
            _save_cache(cache_path, cache)
            continue
        plat, plng = coords
        dist_km = haversine_m(expected[0], expected[1], plat, plng) / 1000.0
        if dist_km > max_distance_km:
            log.info("revalidate %s: %.1f km from spot — dropping match", name, dist_km)
            cache[name] = {
                "name": name, "source_url": None,
                "scraped_at": rec.get("scraped_at"),
                "previously_matched_url": url,
                "previously_matched_distance_km": round(dist_km, 2),
            }
            stats["dropped"] += 1
        else:
            rec["page_lat"] = plat
            rec["page_lng"] = plng
            rec["match_distance_km"] = round(dist_km, 3)
            stats["kept"] += 1
        _save_cache(cache_path, cache)

    return stats


# Fields the scrape authoritatively overwrites on matched spots. Order
# matters for the summary print only.
_MERGE_FIELDS = (
    "orientation_deg", "offshore_wind_deg", "optimal_swell_dir",
    "break_type", "tide_preference",
)


def merge_into_spots(spots: list[dict], cache: dict[str, dict]) -> dict:
    """Apply matched scrape records to *spots* in place.

    For every spot with a cache entry containing source_url, overwrites
    the five fields above. orientation_deg is derived from
    offshore_wind_deg + 180° (mod 360).
    """
    stats = {
        "matched": 0,
        "no_match": 0,
        "no_cache_entry": 0,
        "field_changes": {f: 0 for f in _MERGE_FIELDS},
    }

    for spot in spots:
        rec = cache.get(spot.get("name"))
        if rec is None:
            stats["no_cache_entry"] += 1
            continue
        if not rec.get("source_url"):
            stats["no_match"] += 1
            continue

        stats["matched"] += 1
        spot["surf_forecast_url"] = rec["source_url"]

        # Manual orientation overrides: hand-curated values in
        # data/manual_orientations.json are marked with
        # orientation_source="manual" and are never overwritten by a
        # scrape — the human review is the ground truth.
        orient_locked = spot.get("orientation_source") == "manual"

        ow = rec.get("offshore_wind_deg")
        if ow is not None and not orient_locked:
            new_ow = int(ow) % 360
            if new_ow != spot.get("offshore_wind_deg"):
                spot["offshore_wind_deg"] = new_ow
                stats["field_changes"]["offshore_wind_deg"] += 1
            new_orient = (new_ow + 180) % 360
            if new_orient != spot.get("orientation_deg"):
                spot["orientation_deg"] = new_orient
                stats["field_changes"]["orientation_deg"] += 1

        osd = rec.get("optimal_swell_dir")
        if osd is not None:
            new_osd = int(osd) % 360
            if new_osd != spot.get("optimal_swell_dir"):
                spot["optimal_swell_dir"] = new_osd
                stats["field_changes"]["optimal_swell_dir"] += 1

        bt = rec.get("break_type")
        if bt and bt != spot.get("break_type"):
            spot["break_type"] = bt
            stats["field_changes"]["break_type"] += 1

        tp = rec.get("tide_preference")
        if tp and tp != spot.get("tide_preference"):
            spot["tide_preference"] = tp
            stats["field_changes"]["tide_preference"] += 1

    return stats


def unmerge_stale_matches(spots: list[dict], cache: dict[str, dict]) -> dict:
    """Revert scrape-derived fields for spots whose match was dropped by
    revalidation.

    Identifies spots where the cache entry has ``previously_matched_url``
    but no current ``source_url`` — the revalidation pass dropped a bad
    match, but the earlier merge already wrote its orientation / swell /
    break-type / tide values into spots_enriched.json. Clear those five
    fields (plus ``surf_forecast_url``) so the next enrich / verify pass
    can recompute them from scratch.
    """
    stats = {
        "unmerged": 0,
        "cleared_fields": {f: 0 for f in _MERGE_FIELDS},
    }
    for spot in spots:
        rec = cache.get(spot.get("name"))
        if rec is None:
            continue
        # A dropped match is a cache entry with previously_matched_url and
        # no current source_url. Skip entries that are still matched (the
        # merge step handles those) and entries that were never a match.
        if not rec.get("previously_matched_url"):
            continue
        if rec.get("source_url"):
            continue
        stats["unmerged"] += 1
        for f in _MERGE_FIELDS:
            if spot.get(f) is not None:
                spot[f] = None
                stats["cleared_fields"][f] += 1
        spot.pop("surf_forecast_url", None)
    return stats


def _summarize(
    scrape_stats: dict | None,
    merge_stats: dict,
    revalidate_stats: dict | None = None,
    unmerge_stats: dict | None = None,
) -> None:
    print()
    print("=" * 60)
    print("surf-forecast.com scrape summary")
    print("=" * 60)
    if revalidate_stats is not None:
        print("  coord revalidation:")
        print(f"    rechecked:           {revalidate_stats['rechecked']}")
        print(f"    kept (coord OK):     {revalidate_stats['kept']}")
        print(f"    dropped (too far):   {revalidate_stats['dropped']}")
        if revalidate_stats.get("unknown_coord"):
            print(f"    spot coord unknown:  {revalidate_stats['unknown_coord']}")
        if revalidate_stats.get("errors"):
            print(f"    fetch errors:        {revalidate_stats['errors']}")
    if scrape_stats is not None:
        print(f"  matched this run:    {scrape_stats['matched']}")
        print(f"  missed this run:     {scrape_stats['missed']}")
        if scrape_stats["errors"]:
            print(f"  errors this run:     {scrape_stats['errors']}")
    if unmerge_stats is not None and unmerge_stats.get("unmerged"):
        print()
        print("  reverted dropped-match writes:")
        print(f"    spots reverted:      {unmerge_stats['unmerged']}")
        for field, n in unmerge_stats["cleared_fields"].items():
            if n:
                print(f"      {field:<22} cleared on {n}")
    print()
    print("  merge into spots_enriched.json:")
    print(f"    total matched in cache:    {merge_stats['matched']}")
    print(f"    no match for spot:         {merge_stats['no_match']}")
    print(f"    no cache entry (unscraped):{merge_stats['no_cache_entry']}")
    print("    field overwrites:")
    for field in _MERGE_FIELDS:
        print(f"      {field:<22} {merge_stats['field_changes'][field]}")
    print("=" * 60)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Scrape surf-forecast.com for verified spot metadata.")
    p.add_argument("--input", type=Path, default=DEFAULT_ENRICHED_OUTPUT,
                   help="Input/output spots_enriched.json (updated in place).")
    p.add_argument("--output", type=Path, default=None,
                   help="Output path (defaults to --input).")
    p.add_argument("--cache-file", type=Path, default=SURF_FORECAST_CACHE_FILE,
                   help="Where to read/write per-spot scrape records.")
    p.add_argument("--no-cache", action="store_true",
                   help="Ignore the existing cache and re-scrape every spot "
                        "(still writes to the same cache file).")
    p.add_argument("--merge-only", action="store_true",
                   help="Skip the HTTP scrape and only apply the existing "
                        "cache file to spots_enriched.json.")
    p.add_argument("--validate-cache", action="store_true",
                   help="Re-fetch every matched cache entry and drop matches "
                        "whose page coord is more than --max-distance-km from "
                        "the spot. Runs before scrape_all so the new scrape "
                        "can fill the newly-opened slots.")
    p.add_argument("--max-distance-km", type=float,
                   default=_DEFAULT_MAX_DISTANCE_KM,
                   help=f"Reject scrape matches whose page coord is farther "
                        f"than this from the spot (default "
                        f"{_DEFAULT_MAX_DISTANCE_KM}).")
    p.add_argument("--min-interval-seconds", type=float,
                   default=SURF_FORECAST_MIN_INTERVAL_S,
                   help=f"Minimum seconds between HTTP requests (default "
                        f"{SURF_FORECAST_MIN_INTERVAL_S}).")
    p.add_argument("--build-directory", action="store_true",
                   help="Crawl surf-forecast.com's country/region index pages "
                        "to build a canonical {name: slug} directory, then "
                        "proceed with the rest of the pipeline. The directory "
                        "is cached — subsequent runs reuse it unless rebuilt.")
    p.add_argument("--rebuild-directory", action="store_true",
                   help="Like --build-directory but forces a fresh crawl even "
                        "if the cached directory exists.")
    p.add_argument("--directory-file", type=Path,
                   default=SURF_FORECAST_DIRECTORY_FILE,
                   help="Path to the cached directory JSON.")
    p.add_argument("--no-directory", action="store_true",
                   help="Ignore the cached directory even when present (e.g. "
                        "to A/B-test naive slug matching).")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.input.exists():
        log.error("Input file %s does not exist.", args.input)
        return 1

    spots = json.loads(args.input.read_text())
    log.info("Loaded %d spots from %s", len(spots), args.input)

    # Directory: build on demand, reuse otherwise. --build-directory and
    # --rebuild-directory always run (even alongside --merge-only), since
    # the build is itself an HTTP-only operation separate from the scrape.
    # --no-directory disables both use and build.
    directory: dict | None = None
    if not args.no_directory:
        need_build = args.build_directory or args.rebuild_directory or (
            not args.merge_only and not args.directory_file.exists()
        )
        if need_build:
            if args.rebuild_directory or not args.directory_file.exists():
                log.info("directory: building (this will take a few minutes)…")
                session = _PacedSession(args.min_interval_seconds, _USER_AGENT)
                directory = build_directory(session)
                save_directory(args.directory_file, directory)
                log.info("directory: saved %d entries to %s",
                         len(directory.get("entries", [])), args.directory_file)
            else:
                directory = load_directory(args.directory_file)
        else:
            directory = load_directory(args.directory_file)
            if directory:
                log.info("directory: loaded %d entries from %s",
                         len(directory.get("entries", [])), args.directory_file)

    scrape_stats: dict | None = None
    revalidate_stats: dict | None = None
    if args.merge_only:
        cache = _load_cache(args.cache_file)
        log.info("--merge-only: %d cache entries from %s", len(cache), args.cache_file)
    else:
        cache = _load_cache(args.cache_file) if not args.no_cache else {}
        if args.validate_cache and cache:
            revalidate_stats = revalidate_cached_matches(
                spots, cache, args.cache_file,
                max_distance_km=args.max_distance_km,
                min_interval_s=args.min_interval_seconds,
            )
        cache, scrape_stats = scrape_all(
            spots,
            cache_path=args.cache_file,
            use_cache=not args.no_cache,
            min_interval_s=args.min_interval_seconds,
            max_distance_km=args.max_distance_km,
            directory=directory,
        )

    merge_stats = merge_into_spots(spots, cache)
    # Revert scrape-derived writes for matches that revalidation dropped.
    # Runs after merge so a spot that's both (previously matched elsewhere
    # AND newly matched again this run) keeps the fresh write.
    unmerge_stats = unmerge_stale_matches(spots, cache)

    output_path = args.output or args.input
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(spots, indent=2, ensure_ascii=False))
    log.info("Wrote %d spots back to %s", len(spots), output_path)

    _summarize(scrape_stats, merge_stats, revalidate_stats, unmerge_stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
