"""UTM projection helpers: pick the right zone for a lat/lng and transform geometries."""
from __future__ import annotations

from functools import lru_cache

from pyproj import CRS, Transformer
from shapely.geometry import LineString, Point
from shapely.ops import transform

WGS84 = CRS.from_epsg(4326)


def utm_epsg(lat: float, lng: float) -> int:
    """EPSG code for the appropriate UTM zone."""
    zone = int((lng + 180) / 6) + 1
    zone = min(max(zone, 1), 60)
    return (32600 if lat >= 0 else 32700) + zone


@lru_cache(maxsize=256)
def _transformers(epsg: int) -> tuple[Transformer, Transformer]:
    to_utm = Transformer.from_crs(WGS84, CRS.from_epsg(epsg), always_xy=True)
    from_utm = Transformer.from_crs(CRS.from_epsg(epsg), WGS84, always_xy=True)
    return to_utm, from_utm


def to_utm_point(lat: float, lng: float, epsg: int) -> Point:
    to_utm, _ = _transformers(epsg)
    x, y = to_utm.transform(lng, lat)
    return Point(x, y)


def from_utm_point(x: float, y: float, epsg: int) -> tuple[float, float]:
    """Return (lat, lng) for a UTM (x, y)."""
    _, from_utm = _transformers(epsg)
    lng, lat = from_utm.transform(x, y)
    return lat, lng


def project_linestring_to_utm(line: LineString, epsg: int) -> LineString:
    to_utm, _ = _transformers(epsg)
    return transform(lambda x, y, z=None: to_utm.transform(x, y), line)
