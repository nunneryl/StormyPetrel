"""Algorithm 1 — coastline orientation.

For each spot, compute the bearing the coastline faces (seaward normal)
at 50 m, 100 m, and 200 m sampling windows. Uses the UTM zone local to
the spot for metric accuracy.
"""
from __future__ import annotations

import logging
import math

from shapely.geometry import LineString, Point

from .geodata import LandIndex, load_coastlines_for_orientation, load_land_index
from .projection import (
    from_utm_point,
    project_linestring_to_utm,
    to_utm_point,
    utm_epsg,
)

log = logging.getLogger(__name__)


def _bearing_deg(dx_east: float, dy_north: float) -> float:
    """Bearing in degrees (0=N, 90=E) from a UTM tangent vector."""
    return (math.degrees(math.atan2(dx_east, dy_north)) + 360) % 360


def _as_geom(item, geom_list):
    """Normalize an STRtree query/nearest result.

    Shapely 2 returns numpy integer indices; shapely 1 returned the geometry itself.
    Accept both by converting anything index-like to an integer.
    """
    if hasattr(item, "__index__"):
        return geom_list[int(item)]
    return item


def _nearest_coast_line(coastlines: list, coast_tree, spot_ll: Point) -> LineString | None:
    if not coastlines:
        return None
    try:
        nearest = coast_tree.nearest(spot_ll)
    except Exception:
        return None
    return _as_geom(nearest, coastlines)


def _point_on_land(test_lat: float, test_lng: float, land: LandIndex) -> bool:
    p = Point(test_lng, test_lat)
    try:
        candidates = land.polygon_tree.query(p)
    except Exception:
        return False
    for c in candidates:
        poly = _as_geom(c, land.polygons)
        if poly.contains(p) or poly.intersects(p):
            return True
    return False


def _orientation_at_window(
    coast_utm: LineString,
    spot_utm: Point,
    half_window_m: float,
    land: LandIndex,
    epsg: int,
) -> float | None:
    """Compute the seaward-facing bearing using a ±half_window_m tangent sample."""
    total_len = coast_utm.length
    if total_len < 2 * half_window_m:
        return None
    # Project spot onto the coast to get parametric distance along the line.
    d = coast_utm.project(spot_utm)
    d_back = max(0.0, d - half_window_m)
    d_fwd = min(total_len, d + half_window_m)
    if d_fwd - d_back < 1.0:
        return None
    p_back = coast_utm.interpolate(d_back)
    p_fwd = coast_utm.interpolate(d_fwd)
    dx = p_fwd.x - p_back.x
    dy = p_fwd.y - p_back.y
    if dx == 0 and dy == 0:
        return None
    tangent = _bearing_deg(dx, dy)
    # The two perpendiculars; the one whose test point is NOT on land is seaward.
    perp_a = (tangent + 90) % 360
    perp_b = (tangent - 90 + 360) % 360
    test_dist_m = 50.0
    candidates = []
    for perp in (perp_a, perp_b):
        rad = math.radians(perp)
        test_x = spot_utm.x + test_dist_m * math.sin(rad)
        test_y = spot_utm.y + test_dist_m * math.cos(rad)
        test_lat, test_lng = from_utm_point(test_x, test_y, epsg)
        candidates.append((perp, _point_on_land(test_lat, test_lng, land)))
    # Seaward = the perpendicular whose test point is NOT on land.
    seaward = [perp for perp, on_land in candidates if not on_land]
    landward = [perp for perp, on_land in candidates if on_land]
    if len(seaward) == 1:
        return seaward[0]
    if seaward and not landward:
        # Neither test point is on land (spot is on an island tip?) — pick perp_a arbitrarily
        return perp_a
    # Both on land (shouldn't happen for a coastal spot) — no confident answer.
    return None


def compute_orientation(spot: dict) -> dict:
    """Return orientation-related fields for a spot.

    Output keys: orientation_deg, orientation_50m, orientation_200m,
    offshore_wind_deg, orientation_confidence (0.0 if unresolvable).
    """
    land = load_land_index()
    coastlines = load_coastlines_for_orientation()
    if not land or not coastlines:
        return {
            "orientation_deg": None,
            "orientation_50m": None,
            "orientation_200m": None,
            "offshore_wind_deg": None,
            "orientation_confidence": 0.0,
        }

    lat = spot["lat"]
    lng = spot["lng"]
    epsg = utm_epsg(lat, lng)
    spot_utm = to_utm_point(lat, lng, epsg)

    # STRtree index over all coastlines is held in land; CUSP fallback uses same tree
    # when GSHHG is the source. If coastlines came from CUSP, we don't have that tree,
    # so do a quick linear scan restricted by bounding-box filter.
    from shapely.strtree import STRtree
    tree = land.coastline_tree if coastlines is land.coastlines else STRtree(coastlines)
    nearest_ll = _nearest_coast_line(coastlines, tree, Point(lng, lat))
    if nearest_ll is None:
        return {
            "orientation_deg": None,
            "orientation_50m": None,
            "orientation_200m": None,
            "offshore_wind_deg": None,
            "orientation_confidence": 0.0,
        }

    coast_utm = project_linestring_to_utm(nearest_ll, epsg)

    results = {}
    for key, half in (("orientation_50m", 50.0), ("orientation_deg", 100.0), ("orientation_200m", 200.0)):
        results[key] = _orientation_at_window(coast_utm, spot_utm, half, land, epsg)

    primary = results["orientation_deg"]
    offshore = ((primary + 180) % 360) if primary is not None else None
    return {
        **results,
        "offshore_wind_deg": offshore,
        "orientation_confidence": 1.0 if primary is not None else 0.0,
    }
