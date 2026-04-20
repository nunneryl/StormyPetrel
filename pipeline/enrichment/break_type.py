"""Algorithm 3 — break type inference from coastline curvature.

The OSM spatial-join approach requires a Geofabrik extract that the user
may or may not have pre-processed; for now we implement the fallback
(coastline curvature in UTM) which the task description calls out as
acceptable. Defaults to "beach" @ 0.5 when the signal is weak.
"""
from __future__ import annotations

import logging
import math

from shapely.geometry import LineString, Point

from .geodata import load_coastlines_for_orientation, load_land_index
from .projection import project_linestring_to_utm, to_utm_point, utm_epsg

log = logging.getLogger(__name__)

_DEFAULT = {"break_type": "beach", "break_type_confidence": 0.5}

# Curvature thresholds (1/metres). 1e-3 = radius 1 km; 5e-3 = radius 200 m.
_CURV_POINT_STRONG = 5e-3
_CURV_POINT_WEAK = float("inf")  # disabled — coarse GSHHG vertices spike curvature falsely; require strong signal


def _sample_curvature(coast_utm: LineString, spot_utm: Point) -> float:
    """Peak |curvature| sampled across 21 points spanning ±500 m of the nearest point."""
    total = coast_utm.length
    if total < 100:
        return 0.0
    d = coast_utm.project(spot_utm)
    d_min = max(0.0, d - 500.0)
    d_max = min(total, d + 500.0)
    if d_max - d_min < 100:
        return 0.0

    n = 21
    step = (d_max - d_min) / (n - 1)
    pts = [coast_utm.interpolate(d_min + i * step) for i in range(n)]
    xs = [p.x for p in pts]
    ys = [p.y for p in pts]

    peak = 0.0
    # Central-difference derivatives. Discrete curvature:
    #   κ = |x' y'' - y' x''| / (x'^2 + y'^2)^(3/2)
    for i in range(1, n - 1):
        dx = (xs[i + 1] - xs[i - 1]) / (2 * step)
        dy = (ys[i + 1] - ys[i - 1]) / (2 * step)
        ddx = (xs[i + 1] - 2 * xs[i] + xs[i - 1]) / (step * step)
        ddy = (ys[i + 1] - 2 * ys[i] + ys[i - 1]) / (step * step)
        denom = (dx * dx + dy * dy) ** 1.5
        if denom <= 0:
            continue
        kappa = abs(dx * ddy - dy * ddx) / denom
        if kappa > peak:
            peak = kappa
    return peak


def compute_break_type(spot: dict) -> dict:
    land = load_land_index()
    coastlines = load_coastlines_for_orientation()
    if not land or not coastlines:
        return dict(_DEFAULT)

    lat = spot["lat"]
    lng = spot["lng"]
    epsg = utm_epsg(lat, lng)
    spot_ll = Point(lng, lat)

    try:
        idx = land.coastline_tree.nearest(spot_ll)
    except Exception:
        return dict(_DEFAULT)
    # shapely 2 returns numpy int indices; shapely 1 returns the geometry itself.
    if hasattr(idx, "__index__"):
        i = int(idx)
        nearest_ll = coastlines[i] if i < len(coastlines) else land.coastlines[i]
    else:
        nearest_ll = idx

    spot_utm = to_utm_point(lat, lng, epsg)
    coast_utm = project_linestring_to_utm(nearest_ll, epsg)
    kappa = _sample_curvature(coast_utm, spot_utm)

    if kappa >= _CURV_POINT_STRONG:
        confidence = min(0.85, 0.6 + (kappa - _CURV_POINT_STRONG) * 20)
        return {"break_type": "point", "break_type_confidence": round(confidence, 2)}
    if kappa >= _CURV_POINT_WEAK:
        return {"break_type": "point", "break_type_confidence": 0.6}
    # Low curvature → beach default. Confidence held at 0.5 as specified.
    return dict(_DEFAULT)
