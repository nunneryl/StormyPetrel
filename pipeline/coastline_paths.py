"""The land around each spot, as a small SVG path, for the swell compass on the spot page.

    python -m pipeline.coastline_paths [--roster pipeline/spots_enriched.json]
                                       [--out frontend/data/coastlines.json] [--only SLUG,...]

Run once, and again whenever a spot is added or moved: pipeline/tests/test_coastlines_current.py
fails until every roster spot has a path drawn at its current position.

SOURCE. The GSHHG full-resolution L1 shoreline (pipeline/geodata/GSHHS_f_L1.shp), the shoreline
the swell-window ray-cast reads (pipeline/enrichment/geodata.load_land_index), so the land on
the compass and the window band around it come from the same coast. GSHHG can be a few hundred
metres off (about 300 m on Oahu's North Shore); at RADIUS_KM that is about 1% of the drawing.

THE PATH. For each spot: the land within RADIUS_KM, projected flat about the spot (x east,
y SOUTH, so north is up in SVG), scaled so RADIUS_KM is UNITS, clipped to that circle, rounded to
whole units (UNITS / RADIUS_KM = 4 units a km, so 250 m), simplified with the smallest tolerance
in TOLERANCES that brings the path under MAX_BYTES. Absolute moves, then relative lines:
"M-12 40l3-4 5 0...z". A spot with no land within RADIUS_KM gets "".

WHERE IT GOES. frontend/data/coastlines.json, keyed by the spot's slug (db_import._slugify, the
slug the site uses), with the spot's lat/lng so a moved spot shows as stale. A committed file, not
a database column: it changes only when a spot is added or moved, it needs no migration, its
diff shows in review, and the spot page imports it at build time, server-side, passing one
spot's path to the page.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import sys
from datetime import date
from pathlib import Path

from .config import DEFAULT_ENRICHED_OUTPUT
from .db_import import _slugify

log = logging.getLogger("pipeline.coastline_paths")

REPO = Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "frontend" / "data" / "coastlines.json"

RADIUS_KM = 25.0
UNITS = 100                      # RADIUS_KM is this many path units
MAX_BYTES = 4000
TOLERANCES = (0.25, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 4.0, 6.0)   # in units (1 unit = 250 m)
MIN_AREA = 2.0                   # square units: land smaller than this (125 m square) is left out
KM_PER_DEG_LAT = 110.574
KM_PER_DEG_LNG_EQ = 111.320


def project(lat0: float, lng0: float):
    """An (N, 2) array of (lng, lat) -> (x, y) in path units about the spot: x east, y south
    (SVG), so north is up."""
    kx = KM_PER_DEG_LNG_EQ * math.cos(math.radians(lat0)) * UNITS / RADIUS_KM
    ky = KM_PER_DEG_LAT * UNITS / RADIUS_KM

    def f(xy):
        out = xy.copy()
        out[:, 0] = (xy[:, 0] - lng0) * kx
        out[:, 1] = -(xy[:, 1] - lat0) * ky
        return out
    return f


def _ring_path(coords) -> str:
    pts = [(int(round(x)), int(round(y))) for x, y in coords]
    if len(pts) > 1 and pts[0] == pts[-1]:
        pts = pts[:-1]
    out: list[tuple[int, int]] = []
    for p in pts:
        if not out or p != out[-1]:
            out.append(p)
    if len(out) < 3:
        return ""
    x0, y0 = out[0]
    parts = [f"M{x0} {y0}l"]
    deltas = []
    for (ax, ay), (bx, by) in zip(out, out[1:]):
        deltas.append(_pair(bx - ax, by - ay))
    parts.append(_join(deltas))
    parts.append("z")
    return "".join(parts)


def _pair(dx: int, dy: int) -> str:
    return f"{dx}{'' if dy < 0 else ' '}{dy}"


def _join(pairs: list[str]) -> str:
    """Pairs separated by a space, or by nothing where the next starts with '-'."""
    s = ""
    for p in pairs:
        s += p if (not s or p.startswith("-")) else " " + p
    return s


def geometry_path(geom) -> str:
    """The SVG path of a (Multi)Polygon: every ring, exterior and holes."""
    polys = list(geom.geoms) if geom.geom_type in ("MultiPolygon", "GeometryCollection") else [geom]
    out = []
    for p in polys:
        if p.geom_type != "Polygon" or p.is_empty or p.area < MIN_AREA:
            continue
        for ring in [p.exterior, *p.interiors]:
            d = _ring_path(ring.coords)
            if d:
                out.append(d)
    return "".join(out)


def land_near(index, lat: float, lng: float):
    """The land within RADIUS_KM of (lat, lng), projected and clipped to the circle (or None)."""
    from shapely import box, clip_by_rect, make_valid, transform
    from shapely.geometry import Point
    from shapely.ops import unary_union

    dlat = RADIUS_KM / KM_PER_DEG_LAT * 1.05
    dlng = RADIUS_KM / (KM_PER_DEG_LNG_EQ * math.cos(math.radians(lat))) * 1.05
    rect = (lng - dlng, lat - dlat, lng + dlng, lat + dlat)
    hits = index.polygon_tree.query(box(*rect))
    if len(hits) == 0:
        return None
    f = project(lat, lng)
    parts = []
    for i in hits:
        clipped = clip_by_rect(index.polygons[int(i)], *rect)
        if not clipped.is_empty:
            # Clipping can leave a self-touching ring that GEOS refuses to union; repair it.
            parts.append(make_valid(transform(clipped, f)))
    if not parts:
        return None
    circle = Point(0, 0).buffer(UNITS, quad_segs=64)
    land = make_valid(unary_union(parts)).intersection(circle)
    polys = [g for g in getattr(land, "geoms", [land]) if g.geom_type in ("Polygon", "MultiPolygon")]
    if not polys:
        return None
    land = unary_union(polys)
    return None if land.is_empty else land


def spot_path(land) -> tuple[str, float | None]:
    """(path, tolerance) under MAX_BYTES, the least simplified that fits."""
    if land is None:
        return "", None
    for tol in TOLERANCES:
        d = geometry_path(land.simplify(tol, preserve_topology=True))
        if len(d.encode()) <= MAX_BYTES:
            return d, tol
    d = geometry_path(land.simplify(TOLERANCES[-1] * 2, preserve_topology=True))
    return d, TOLERANCES[-1] * 2


def stale(spots: list[dict], data: dict) -> list[str]:
    """Slugs of roster spots with no path in *data*, or one drawn at another position (moved more
    than about 1 m: 6 decimal places differ by more than 1e-5)."""
    paths = data.get("spots", {})
    out = []
    for s in spots:
        slug = _slugify(s.get("name") or "")
        e = paths.get(slug)
        if (e is None or abs(e["lat"] - float(s["lat"])) > 1e-5
                or abs(e["lng"] - float(s["lng"])) > 1e-5):
            out.append(slug)
    return sorted(out)


def build(spots: list[dict], index, only: set[str] | None = None, prior: dict | None = None) -> dict:
    paths = dict((prior or {}).get("spots", {}))
    for s in spots:
        slug = _slugify(s.get("name") or "")
        if not slug or (only and slug not in only):
            continue
        lat, lng = float(s["lat"]), float(s["lng"])
        d, tol = spot_path(land_near(index, lat, lng))
        paths[slug] = {"lat": round(lat, 6), "lng": round(lng, 6), "d": d}
        log.info("%-40s %5d bytes (tolerance %s)", slug, len(d), tol)
    return {
        "source": "GSHHG f L1 (pipeline/geodata/GSHHS_f_L1.shp), as the swell-window ray-cast",
        "radius_km": RADIUS_KM,
        "units": UNITS,
        "generated": date.today().isoformat(),
        "spots": dict(sorted(paths.items())),
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--roster", type=Path, default=DEFAULT_ENRICHED_OUTPUT)
    ap.add_argument("--out", type=Path, default=DEFAULT_OUT)
    ap.add_argument("--only", default="", help="comma-separated slugs; the rest are kept as they are")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from .enrichment.geodata import load_land_index
    index = load_land_index()
    if index is None:
        log.error("no GSHHG shapefile: run the geodata download first")
        return 1
    raw = json.loads(args.roster.read_text())
    spots = raw if isinstance(raw, list) else raw.get("spots", [])
    only = {x.strip() for x in args.only.split(",") if x.strip()} or None
    prior = json.loads(args.out.read_text()) if only and args.out.exists() else None
    out = build(spots, index, only, prior)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, separators=(",", ":"), ensure_ascii=False) + "\n")
    sizes = [len(v["d"]) for v in out["spots"].values()]
    log.info("%d spots, %d with land, largest %d bytes, file %d bytes", len(sizes),
             sum(1 for n in sizes if n), max(sizes or [0]), args.out.stat().st_size)
    return 0


if __name__ == "__main__":
    sys.exit(main())
