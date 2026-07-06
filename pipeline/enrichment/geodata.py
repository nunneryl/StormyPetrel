"""Shared geodata loaders and spatial indices for the enrichment pipeline.

All loaders are lazy and cached — the GSHHG shapefile is expensive to parse
and we only want to pay the cost once per process.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from functools import lru_cache

from ..config import (
    CUSP_DIR,
    GSHHG_L1_SHP,
    NDBC_LATEST_OBS_TXT,
    NDBC_STATIONS_XML,
    TIDE_STATIONS_JSON,
)

log = logging.getLogger(__name__)


@dataclass
class LandIndex:
    """GSHHG L1 land polygons + STRtree for fast lookup.

    Coastlines are the exterior rings of each land polygon exposed as
    LineStrings for nearest-segment queries (orientation + curvature).
    """
    polygons: list           # list[shapely.geometry.Polygon]
    polygon_tree: object     # shapely.strtree.STRtree over polygons
    coastlines: list         # list[shapely.geometry.LineString] (polygon exteriors)
    coastline_tree: object   # shapely.strtree.STRtree over coastlines


@lru_cache(maxsize=1)
def load_land_index() -> LandIndex | None:
    """Load GSHHG L1 polygons and build a spatial index.

    Returns None (with a warning) if the shapefile is missing so the pipeline
    can still run the algorithms that don't require land data.
    """
    if not GSHHG_L1_SHP.exists():
        log.warning("GSHHG L1 shapefile not found at %s — land-dependent algorithms will be skipped", GSHHG_L1_SHP)
        return None

    import fiona
    from shapely.geometry import LineString, shape
    from shapely.strtree import STRtree

    polygons = []
    coastlines = []
    log.info("Loading GSHHG L1 polygons from %s ...", GSHHG_L1_SHP)
    with fiona.open(GSHHG_L1_SHP) as src:
        for feature in src:
            geom = shape(feature["geometry"])
            # L1 polygons are always single polygons but be defensive.
            geoms = list(geom.geoms) if geom.geom_type == "MultiPolygon" else [geom]
            for g in geoms:
                polygons.append(g)
                coastlines.append(LineString(list(g.exterior.coords)))

    log.info("GSHHG: %d polygons / coastlines loaded", len(polygons))
    return LandIndex(
        polygons=polygons,
        polygon_tree=STRtree(polygons),
        coastlines=coastlines,
        coastline_tree=STRtree(coastlines),
    )


@lru_cache(maxsize=1)
def load_coastlines_for_orientation() -> list | None:
    """Prefer CUSP shorelines for orientation; fall back to GSHHG exteriors.

    Returns a list of shapely LineStrings or None.
    """
    cusp_shps = sorted(CUSP_DIR.glob("CUSP*.shp")) if CUSP_DIR.exists() else []
    if cusp_shps:
        try:
            import fiona
            from shapely.geometry import LineString, shape
            lines: list = []
            for shp in cusp_shps:
                log.info("Loading CUSP shoreline %s", shp.name)
                with fiona.open(shp) as src:
                    for feature in src:
                        geom = shape(feature["geometry"])
                        if geom.geom_type == "LineString":
                            lines.append(geom)
                        elif geom.geom_type == "MultiLineString":
                            lines.extend(list(geom.geoms))
                        elif geom.geom_type == "Polygon":
                            lines.append(LineString(list(geom.exterior.coords)))
            log.info("CUSP: %d shoreline segments loaded", len(lines))
            return lines
        except Exception as e:  # noqa: BLE001
            log.warning("CUSP load failed (%s); falling back to GSHHG exteriors", e)

    land = load_land_index()
    return land.coastlines if land else None


@lru_cache(maxsize=1)
def load_ndbc_wave_stations() -> list[dict]:
    """Parse NDBC activestations.xml + latest_obs.txt to produce the wave-reporting station list.

    Returns a list of {"id", "lat", "lng", "name"} for stations that reported
    non-missing WVHT in the most recent observations file. If either file is
    missing, returns [].
    """
    if not NDBC_STATIONS_XML.exists() or not NDBC_LATEST_OBS_TXT.exists():
        log.warning(
            "NDBC files missing (%s, %s) — buoy assignment will be skipped",
            NDBC_STATIONS_XML, NDBC_LATEST_OBS_TXT,
        )
        return []

    # latest_obs.txt is whitespace-separated with a '#STN' header and a units row.
    # We only need the set of station IDs whose WVHT column is not 'MM'.
    wave_reporters: set[str] = set()
    with NDBC_LATEST_OBS_TXT.open() as f:
        header: list[str] | None = None
        for line in f:
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("#"):
                # First '#' line is the header (e.g. "#STN LAT LON YYYY MM DD ... WVHT ...")
                if header is None:
                    header = stripped.lstrip("#").split()
                continue
            if header is None:
                continue
            parts = stripped.split()
            if len(parts) < len(header):
                continue
            row = dict(zip(header, parts))
            stn = row.get("STN") or row.get("#STN")
            wvht = row.get("WVHT")
            if stn and wvht and wvht.upper() != "MM":
                wave_reporters.add(stn.lower())

    log.info("NDBC: %d stations reporting wave height", len(wave_reporters))

    # activestations.xml: <stations> containing <station id=".." lat=".." lon=".." name=".." .../>
    import xml.etree.ElementTree as ET
    stations: list[dict] = []
    try:
        tree = ET.parse(NDBC_STATIONS_XML)
    except ET.ParseError as e:
        log.warning("NDBC stations XML parse failed: %s", e)
        return []
    for el in tree.getroot().iter("station"):
        stn_id = (el.get("id") or "").lower()
        if stn_id not in wave_reporters:
            continue
        try:
            lat = float(el.get("lat"))
            lng = float(el.get("lon"))
        except (TypeError, ValueError):
            continue
        stations.append({
            "id": stn_id,
            "lat": lat,
            "lng": lng,
            "name": el.get("name") or "",
        })
    log.info("NDBC: %d wave stations matched to active stations metadata", len(stations))
    return stations


def load_ndbc_active_stations() -> list[dict]:
    """Parse NDBC activestations.xml into the FULL active-station list — every
    station with valid coordinates, REGARDLESS of whether it is currently reporting
    a wave height. Sibling of load_ndbc_wave_stations (which filters to the
    wave-reporting subset via latest_obs.txt); use this when you need a station's
    static coordinate metadata, not its momentary WVHT status. Returns a list of
    {"id", "lat", "lng", "name"}; [] if the XML is missing / unparseable.
    """
    if not NDBC_STATIONS_XML.exists():
        log.warning("NDBC stations XML missing (%s) — active-station metadata unavailable",
                    NDBC_STATIONS_XML)
        return []
    import xml.etree.ElementTree as ET
    try:
        tree = ET.parse(NDBC_STATIONS_XML)
    except ET.ParseError as e:
        log.warning("NDBC stations XML parse failed: %s", e)
        return []
    stations: list[dict] = []
    for el in tree.getroot().iter("station"):
        stn_id = (el.get("id") or "").lower()
        if not stn_id:
            continue
        try:
            lat = float(el.get("lat"))
            lng = float(el.get("lon"))
        except (TypeError, ValueError):
            continue
        stations.append({
            "id": stn_id,
            "lat": lat,
            "lng": lng,
            "name": el.get("name") or "",
        })
    log.info("NDBC: %d active stations (full metadata list)", len(stations))
    return stations


@lru_cache(maxsize=1)
def load_tide_stations() -> list[dict]:
    """Parse tide_stations.json from NOAA CO-OPS. Returns [] if missing."""
    if not TIDE_STATIONS_JSON.exists():
        log.warning("Tide stations JSON missing at %s", TIDE_STATIONS_JSON)
        return []
    import json
    raw = json.loads(TIDE_STATIONS_JSON.read_text())
    stations: list[dict] = []
    for s in raw.get("stations", []):
        try:
            stations.append({
                "id": str(s["id"]),
                "lat": float(s["lat"]),
                "lng": float(s.get("lng", s.get("lon"))),
                "name": s.get("name") or "",
            })
        except (KeyError, TypeError, ValueError):
            continue
    log.info("Tide stations: %d loaded", len(stations))
    return stations
