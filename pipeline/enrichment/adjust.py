"""Seaward coordinate adjustment.

Many OSM-tagged surf spots sit a few metres *inside* the coarse GSHHG L1
land polygon. That breaks every land-sensitive algorithm:

- orientation's 50 m ± perpendicular LOS test finds BOTH test points on land
- buoy LOS rejects every candidate (the geodesic line starts in land)
- swell-window ray-casting finds every ray blocked immediately

``seaward_adjust`` detects this case and returns a point ``offset_m`` outside
the nearest polygon edge so the downstream algorithms see a spot that is
genuinely in the water. Spots already outside all polygons are returned
unchanged.
"""
from __future__ import annotations

import logging
import math

from shapely.geometry import Point
from shapely.ops import nearest_points

from .geodata import LandIndex
from .projection import from_utm_point, to_utm_point, utm_epsg

log = logging.getLogger(__name__)


def seaward_adjust(
    lat: float,
    lng: float,
    land: LandIndex,
    offset_m: float = 30.0,
) -> tuple[float, float, bool]:
    """Return (lat, lng, was_adjusted). If the input point is inside any GSHHG
    polygon, project it to the nearest polygon-edge point and step ``offset_m``
    further in the same direction (away from the spot → through the edge →
    into open water).
    """
    spot_ll = Point(lng, lat)
    container_poly = None
    try:
        candidates = land.polygon_tree.query(spot_ll)
    except Exception:
        candidates = []
    for c in candidates:
        poly = land.polygons[int(c)] if hasattr(c, "__index__") else c
        if poly.contains(spot_ll):
            container_poly = poly
            break
    if container_poly is None:
        return lat, lng, False

    # Nearest point on the polygon exterior — compute in WGS84 (fast) then
    # project only the two points of interest to UTM for a metric offset.
    nearest_on_boundary_ll, _ = nearest_points(container_poly.exterior, spot_ll)
    epsg = utm_epsg(lat, lng)
    spot_utm = to_utm_point(lat, lng, epsg)
    near_utm = to_utm_point(nearest_on_boundary_ll.y, nearest_on_boundary_ll.x, epsg)

    dx = near_utm.x - spot_utm.x
    dy = near_utm.y - spot_utm.y
    norm = math.hypot(dx, dy)
    if norm < 1e-6:
        return lat, lng, False
    ux, uy = dx / norm, dy / norm

    adj_x = near_utm.x + offset_m * ux
    adj_y = near_utm.y + offset_m * uy
    adj_lat, adj_lng = from_utm_point(adj_x, adj_y, epsg)
    return adj_lat, adj_lng, True
