"""Geo utilities: Haversine distance + offline state reverse geocoding + state normalization."""
from __future__ import annotations

import logging
import math
from typing import Iterable

log = logging.getLogger(__name__)

EARTH_RADIUS_M = 6_371_000.0

# US state / territory two-letter codes → canonical full names.
_STATE_CODE_TO_NAME = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas",
    "CA": "California", "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware",
    "FL": "Florida", "GA": "Georgia", "HI": "Hawaii", "ID": "Idaho",
    "IL": "Illinois", "IN": "Indiana", "IA": "Iowa", "KS": "Kansas",
    "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi",
    "MO": "Missouri", "MT": "Montana", "NE": "Nebraska", "NV": "Nevada",
    "NH": "New Hampshire", "NJ": "New Jersey", "NM": "New Mexico", "NY": "New York",
    "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio", "OK": "Oklahoma",
    "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah",
    "VT": "Vermont", "VA": "Virginia", "WA": "Washington", "WV": "West Virginia",
    "WI": "Wisconsin", "WY": "Wyoming", "DC": "District of Columbia",
    "PR": "Puerto Rico", "VI": "Virgin Islands", "GU": "Guam", "AS": "American Samoa",
    "MP": "Northern Mariana Islands",
}

_FULL_NAMES = {v.lower(): v for v in _STATE_CODE_TO_NAME.values()}

# Landlocked / non-coastal US states. A surf spot resolving here is almost always
# a reverse-geocoding miss (coord on a lake/river snapped to an inland city) — we
# warn loudly when this happens so the user can audit.
LANDLOCKED_STATES = frozenset({
    "Arizona", "Arkansas", "Colorado", "Idaho", "Illinois", "Indiana", "Iowa",
    "Kansas", "Kentucky", "Minnesota", "Missouri", "Montana", "Nebraska",
    "Nevada", "New Mexico", "North Dakota", "Ohio", "Oklahoma", "South Dakota",
    "Tennessee", "Utah", "Vermont", "West Virginia", "Wisconsin", "Wyoming",
    "District of Columbia",
})


def normalize_state(value: str | None) -> str | None:
    """Return the canonical full US-state name for *value*, or None if unrecognized.

    Accepts two-letter codes (CA, hi), full names ("California", "california"),
    and common parenthetical suffixes ("California (state)").
    """
    if not value:
        return None
    v = str(value).strip()
    if not v:
        return None
    # Drop parenthetical suffixes commonly used in Wikipedia/Wikidata labels.
    paren = v.find("(")
    if paren > 0:
        v = v[:paren].strip()
    # Drop trailing ", United States" etc.
    for suffix in (", United States", ", USA", ", US"):
        if v.endswith(suffix):
            v = v[: -len(suffix)].strip()
    upper = v.upper()
    if len(upper) == 2 and upper in _STATE_CODE_TO_NAME:
        return _STATE_CODE_TO_NAME[upper]
    return _FULL_NAMES.get(v.lower())


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two lat/lng points, in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def fill_region_hint(records: list[dict]) -> None:
    """Normalize every region_hint; reverse-geocode records missing one. Mutates in place."""
    # First pass: normalize anything that's already set.
    for r in records:
        if r.get("region_hint"):
            r["region_hint"] = normalize_state(r["region_hint"]) or r["region_hint"]

    missing = [(i, r) for i, r in enumerate(records) if not normalize_state(r.get("region_hint"))]
    if not missing:
        return
    try:
        import reverse_geocoder as rg
    except ImportError:
        log.warning("reverse_geocoder not installed; skipping region_hint fill")
        return

    coords = [(r["lat"], r["lng"]) for _, r in missing]
    # mode=1 → single-threaded; friendlier in small scripts / sandboxes.
    results = rg.search(coords, mode=1)
    # Territories surface as their own ISO country code in reverse_geocoder
    # rather than as a US admin1 — promote them to a region_hint directly so
    # downstream filters (manual orientation, hemisphere check, audit) see
    # them as PR / Guam / AS / VI rather than null.
    _TERRITORY_REGIONS = {
        "PR": "Puerto Rico",
        "GU": "Guam",
        "AS": "American Samoa",
        "VI": "U.S. Virgin Islands",
    }
    landlocked_hits: list[tuple[str, float, float, str]] = []
    for (_idx, record), result in zip(missing, results):
        cc = result.get("cc")
        if cc in _TERRITORY_REGIONS:
            record["region_hint"] = _TERRITORY_REGIONS[cc]
            continue
        if cc != "US":
            continue
        admin1 = result.get("admin1") or ""
        canonical = normalize_state(admin1)
        if not canonical:
            record["region_hint"] = admin1 or None
            continue
        record["region_hint"] = canonical
        if canonical in LANDLOCKED_STATES:
            landlocked_hits.append(
                (record.get("name") or "(unnamed)", record["lat"], record["lng"], canonical)
            )
    for name, lat, lng, state in landlocked_hits:
        log.warning(
            "region_hint: %r @ (%.4f, %.4f) resolved to landlocked state %s — audit suggested",
            name, lat, lng, state,
        )


def ensure_iter_records(records: Iterable[dict]) -> list[dict]:
    return list(records)
