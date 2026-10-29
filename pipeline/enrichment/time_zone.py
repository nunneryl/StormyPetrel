"""Each spot's IANA time zone, from its coordinates.

WHY. The site showed every spot's times in America/Los_Angeles, so an East Coast spot's "5pm"
row was 8pm on the beach. The frontend now shows each spot's times in its own zone
(frontend/lib/spotTime.ts), read from spots.time_zone (migration 026), which db_import uploads
from the roster. This module fills the roster's time_zone; enrich.py calls it for every spot.

HOW. The zone polygons of timezone-boundary-builder, through the timezonefinder package, at
the spot's own (unadjusted) coordinates. Never by state: a state is not a zone. The Florida
panhandle west of Cape San Blas is America/Chicago, the rest of Florida America/New_York;
Puerto Rico is America/Puerto_Rico (Atlantic Standard Time, no daylight saving).

OFFSHORE. The polygons reach 12 nautical miles out. A point beyond them gets an "Etc/GMT+5"
style ocean zone, which has no daylight saving, so it is not used: the nearest land zone is
taken instead, searching rings of NEAR_KM around the point. On the 2026-10-29 roster no spot
needed it; a spot that finds no land zone within the last ring raises, rather than guess.
"""
from __future__ import annotations

import math
from functools import lru_cache
from zoneinfo import ZoneInfo

NEAR_KM = (2.0, 5.0, 10.0, 20.0, 40.0)
RING_BEARINGS = 16


@lru_cache(maxsize=1)
def _finder():
    from timezonefinder import TimezoneFinder        # noqa: PLC0415 - heavy; enrich time only
    return TimezoneFinder()


def is_land_zone(tz) -> bool:
    """A real IANA zone, not None and not one of the fixed-offset Etc/ ocean zones."""
    return isinstance(tz, str) and not tz.startswith("Etc/")


def _offset(lat: float, lng: float, km: float, bearing_deg: float) -> tuple[float, float]:
    """The point *km* from (lat, lng) on *bearing_deg*, on a flat-earth approximation (good to
    well under a kilometre at these distances)."""
    b = math.radians(bearing_deg)
    dlat = km * math.cos(b) / 110.57
    dlng = km * math.sin(b) / (111.32 * math.cos(math.radians(lat)))
    return lat + dlat, lng + dlng


def time_zone_for(lat: float, lng: float, finder=None) -> str:
    """The IANA zone at (lat, lng): the zone polygon the point is in, or, offshore of every
    polygon, the land zone most of the nearest ring of NEAR_KM touches (ties to the first in
    bearing order from north). Raises ValueError when no ring finds one."""
    tf = finder or _finder()
    tz = tf.timezone_at(lng=lng, lat=lat)
    if not is_land_zone(tz):
        tz = None
        for km in NEAR_KM:
            found = [z for z in (tf.timezone_at(lng=p[1], lat=p[0]) for p in
                                 (_offset(lat, lng, km, 360.0 * i / RING_BEARINGS)
                                  for i in range(RING_BEARINGS)))
                     if is_land_zone(z)]
            if found:
                tz = max(dict.fromkeys(found), key=found.count)
                break
    if tz is None:
        raise ValueError(f"no time zone within {NEAR_KM[-1]:g} km of ({lat}, {lng})")
    ZoneInfo(tz)                                       # raises on a name this system lacks
    return tz
