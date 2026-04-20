"""Geo utilities: Haversine distance + offline state reverse geocoding."""
from __future__ import annotations

import logging
import math
from typing import Iterable

log = logging.getLogger(__name__)

EARTH_RADIUS_M = 6_371_000.0

# Maps US Census / ISO state codes used by reverse_geocoder's admin1 field to full names.
# reverse_geocoder returns the state name directly for the US, so this is a safety net
# for the rare cases where it returns a code.
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


def haversine_m(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """Great-circle distance between two lat/lng points, in metres."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * EARTH_RADIUS_M * math.asin(math.sqrt(a))


def fill_region_hint(records: list[dict]) -> None:
    """For records missing region_hint, fill it via offline reverse geocoding. Mutates in place."""
    missing = [(i, r) for i, r in enumerate(records) if not r.get("region_hint")]
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
    for (idx, record), result in zip(missing, results):
        if result.get("cc") != "US":
            continue
        admin1 = result.get("admin1") or ""
        record["region_hint"] = _STATE_CODE_TO_NAME.get(admin1, admin1) or None


def ensure_iter_records(records: Iterable[dict]) -> list[dict]:
    return list(records)
