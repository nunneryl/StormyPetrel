"""Core scraping primitives for surf-forecast.com spot pages.

Three pure(ish) building blocks the full scrape CLI (step 2) will
compose into a rate-limited, cached pipeline:

  slug_candidates(name, state)     — URL slug variants
  parse_spot_page(html)            — regex-extract fields from HTML
  fetch_spot(name, state, session) — try candidates, first hit wins

The caller is responsible for configuring `session.headers["User-Agent"]`
to identify the project and for pacing requests (surf-forecast.com
min_interval is 2 s).
"""
from __future__ import annotations

import logging
import re

from .config import SURF_FORECAST_BASE

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


def parse_spot_page(html: str) -> dict:
    """Extract surf metadata from a surf-forecast.com spot page.

    Returns a dict with six keys; any extractor that can't find its
    pattern leaves the value None (empty string for free-form crowd /
    hazards). The caller decides whether a sparse result counts as a
    successful match.
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
    }

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


def fetch_spot(name: str, state: str | None, session) -> dict | None:
    """Try each slug candidate; return the first page that parses to a
    useful record. A 200 without offshore_wind_deg OR break_type is
    treated as a miss (disambiguation / error / unrelated page).
    """
    for slug in slug_candidates(name, state):
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
        fields["source_url"] = resp.url
        fields["matched_slug"] = slug
        return fields
    return None
