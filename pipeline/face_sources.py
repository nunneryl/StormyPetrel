"""Read-only reachability check for the face-height validation sources, run from the GitHub
runner (sw1-raycast mode=face-sources). Nothing here writes anywhere but the report (stdout,
and --out); no Supabase, no secrets.

THE SOURCES, and what each check proves

  frf     USACE Field Research Facility, Duck NC, on the CHL THREDDS server
          (chldata.erdc.dren.mil). The 8 m array (Hs at ~8 m depth, hourly since 1990) and
          the lidar transect off the pier (lidarHydrodynamics: Hs every 0.1 m cross-shore
          from about x = 43 to 198 m). PASS needs the catalog listed, the newest monthly file
          opened over OPeNDAP with a time and a waveHs read from it, and the same file's first
          kilobyte fetched over plain HTTP (fileServer), the fallback if OPeNDAP is refused.
  iem     NWS Surf Zone Forecasts (AFOS PIL SRFxxx) from the Iowa Environmental Mesonet text
          archive (mesonet.agron.iastate.edu). PASS needs the newest SRFMHX with the Northern
          Outer Banks segment (NCZ203, which names Duck) and a surf height parsed from it, and
          at least one product from a past date range (the archive, not only the latest).
  cdip    CDIP's THREDDS (thredds.cdip.ucsd.edu), which the pipeline's MOP fetch already
          reaches: the Cape Canaveral nearshore buoy (143, ~10 m) opened and a waveHs read.
  zone    api.weather.gov forecast zone NCZ203's outline, and Duck Pier's distance from it:
          the mapping from an SRF segment to the spots it covers. The zones are drawn on
          land and a spot's coordinates are on the water (Duck Pier is 0.22 km seaward of
          NCZ203), so a spot belongs to a zone within ZONE_TOLERANCE_KM of it.

The exit status is 1 if any check fails, so the run's colour says it; the report is
printed and written either way.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone

from .config import USER_AGENT

FRF_CATALOG = "https://chldata.erdc.dren.mil/thredds/catalog/frf/oceanography/waves/"
FRF_DODS = "https://chldata.erdc.dren.mil/thredds/dodsC/frf/oceanography/waves/"
FRF_FILES = "https://chldata.erdc.dren.mil/thredds/fileServer/frf/oceanography/waves/"
IEM_AFOS = "https://mesonet.agron.iastate.edu/cgi-bin/afos/retrieve.py"
CDIP_DODS = "https://thredds.cdip.ucsd.edu/thredds/dodsC/cdip/realtime/"
NWS_ZONE = "https://api.weather.gov/zones/forecast/"

DUCK_PIER = (36.1829196, -75.7481164)      # lat, lng from spots_enriched.json
SRF_PIL, SRF_ZONE = "SRFMHX", "NCZ203"     # Northern Outer Banks: Duck ... Nags Head
ARCHIVE_RANGE = ("2025-10-20", "2025-10-22")
ZONE_TOLERANCE_KM = 2.0
TIMEOUT = 60


# --------------------------------------------------------------------------- #
# Parsing (pure)                                                                #
# --------------------------------------------------------------------------- #
def catalog_years(html: str) -> list[str]:
    """The four-digit year folders a THREDDS catalog page lists, oldest first."""
    return sorted(set(re.findall(r'href="(\d{4})/catalog\.html"', html)))


def catalog_files(html: str) -> list[str]:
    """The .nc file names (not .ncml aggregations) a THREDDS catalog page lists, sorted, so
    the last is the newest monthly file (FRF names end _YYYYMM.nc)."""
    return sorted({p.rsplit("/", 1)[-1]
                   for p in re.findall(r"dataset=([^\"'&]+?\.nc)[\"'&]", html)})


_FEET = r"(\d+(?:\.\d+)?)"
_UNIT = r"(?:feet|foot|ft)\b"


def parse_surf_height(text: str) -> dict | None:
    """One SRF surf-height phrase as feet: '6 to 8 feet' -> lo 6, hi 8; 'Around 6 feet' ->
    lo = hi = 6; '5 to 7 ft' the same as feet; ', local sets to 6 feet' is kept apart as
    sets. None when no height is stated (a bare 'Surf Height...' line, 'Flat')."""
    t = text.lower()
    out = None
    m = re.search(_FEET + r"\s*(?:to|-)\s*" + _FEET + r"\s*" + _UNIT, t)
    if m:
        out = {"lo": float(m.group(1)), "hi": float(m.group(2))}
    else:
        m = re.search(r"(?:around|near|about)\s+" + _FEET + r"\s*" + _UNIT, t)
        if m:
            out = {"lo": float(m.group(1)), "hi": float(m.group(1))}
    if out is None:
        return None
    s = re.search(r"sets?\s+(?:to|of|up to)\s+" + _FEET + r"\s*" + _UNIT, t)
    out["sets"] = float(s.group(1)) if s else None
    return out


_UGC_LINE = re.compile(r"^([A-Z]{2}Z\d{3}(?:[->]\d{3}|-[A-Z]{2}Z\d{3})*)-\d{6}-\s*$")


def _ugc_codes(header: str) -> list[str]:
    """'NCZ203-250830-' -> ['NCZ203']; 'HIZ003-029>031-260245-' -> HIZ003, HIZ029..HIZ031."""
    codes, state = [], None
    for tok in header.split("-")[:-2]:
        m = re.fullmatch(r"([A-Z]{2}Z)(\d{3})(?:>(\d{3}))?", tok)
        if m:
            state = m.group(1)
            lo, hi = int(m.group(2)), int(m.group(3) or m.group(2))
        else:
            m = re.fullmatch(r"(\d{3})(?:>(\d{3}))?", tok)
            if not (m and state):
                continue
            lo, hi = int(m.group(1)), int(m.group(2) or m.group(1))
        codes += [f"{state}{n:03d}" for n in range(lo, hi + 1)]
    return codes


def srf_segments(product: str) -> list[dict]:
    """The zone segments of an SRF product: each {zones, name, periods}, periods a list of
    (period name, surf height dict or None) from the 'Surf Height....' line under each
    '.PERIOD...' heading. A segment runs from its UGC line to '$$'."""
    segs, cur, period = [], None, None
    for raw in product.splitlines():
        line = raw.rstrip()
        m = _UGC_LINE.match(line.strip())
        if m:
            cur = {"zones": _ugc_codes(m.group(1) + "-000000-"), "name": None, "periods": []}
            segs.append(cur)
            period = None
            continue
        if cur is None:
            continue
        if line.strip() == "$$":
            cur, period = None, None
            continue
        if cur["name"] is None and line.strip().endswith("-") and line.strip():
            cur["name"] = line.strip().rstrip("-").strip()
            continue
        pm = re.match(r"^\.([A-Z][A-Z ]+?)\.\.\.", line)
        if pm:
            period = pm.group(1).strip()
            # The extended outlook puts the height on the heading line itself.
            rest = line[pm.end():]
            if re.search(r"surf height", rest, re.I):
                cur["periods"].append((period, parse_surf_height(rest)))
                period = None
            continue
        if period and re.match(r"^\s*Surf Height", line, re.I):
            cur["periods"].append((period, parse_surf_height(line.split(".", 1)[-1]
                                                              if "..." in line else line)))
            period = None
    return segs


def distance_to_geometry_km(lat: float, lng: float, geometry: dict) -> float:
    """Kilometres from (lat, lng) to a GeoJSON Polygon or MultiPolygon: to its nearest point,
    which is the point itself (0 km) inside it, on a local flat-earth scale (111.32 km per
    degree of longitude times cos(lat), 110.57 km per degree of latitude), which is well
    inside a metre at the few kilometres this is used for."""
    import math
    from shapely.geometry import Point, shape
    from shapely.ops import nearest_points
    q = nearest_points(shape(geometry), Point(lng, lat))[0]
    dx = (q.x - lng) * 111.32 * math.cos(math.radians(lat))
    dy = (q.y - lat) * 110.57
    return math.hypot(dx, dy)


# --------------------------------------------------------------------------- #
# Fetching                                                                      #
# --------------------------------------------------------------------------- #
def _http(url: str, *, headers: dict | None = None, timeout: int = TIMEOUT):
    import requests
    h = {"User-Agent": USER_AGENT}
    h.update(headers or {})
    return requests.get(url, headers=h, timeout=timeout)


def _text(url: str, **kw) -> str:
    r = _http(url, **kw)
    r.raise_for_status()
    return r.text


def _open_nc(url: str):
    import netCDF4
    return netCDF4.Dataset(url)


def _last(ds, name: str) -> float | None:
    import numpy as np
    v = np.ma.filled(ds.variables[name][-1:].astype(float), np.nan)
    x = float(v[-1])
    return None if x != x else round(x, 3)


def _last_time(ds, name: str = "time") -> str:
    import netCDF4
    t = ds.variables[name]
    return netCDF4.num2date(t[-1], t.units).strftime("%Y-%m-%dT%H:%MZ")


def _check(name: str, fn, *args) -> dict:
    t0 = time.monotonic()
    try:
        detail = fn(*args)
        res = {"check": name, "ok": True, **detail}
    except Exception as e:                                          # report, never raise
        res = {"check": name, "ok": False, "error": f"{type(e).__name__}: {e}"[:400]}
    res["seconds"] = round(time.monotonic() - t0, 1)
    return res


def frf_newest(inst: str, text=_text) -> tuple[str, str]:
    """(year, file) of an FRF instrument's newest monthly file, from its catalog pages."""
    years = catalog_years(text(f"{FRF_CATALOG}{inst}/catalog.html"))
    if not years:
        raise ValueError(f"{inst}: no year folders in the catalog")
    files = catalog_files(text(f"{FRF_CATALOG}{inst}/{years[-1]}/catalog.html"))
    if not files:
        raise ValueError(f"{inst}: no files in {years[-1]}")
    return years[-1], files[-1]


def check_frf_8m(text=_text, open_nc=_open_nc, http=_http) -> dict:
    year, f = frf_newest("8m-array", text)
    ds = open_nc(f"{FRF_DODS}8m-array/{year}/{f}")
    out = {"file": f, "last_time": _last_time(ds), "last_waveHs_m": _last(ds, "waveHs"),
           "times": len(ds.variables["time"])}
    r = http(f"{FRF_FILES}8m-array/{year}/{f}", headers={"Range": "bytes=0-1023"})
    out["fileServer_status"] = r.status_code
    if r.status_code not in (200, 206):
        raise ValueError(f"fileServer returned HTTP {r.status_code} (OPeNDAP worked: {out})")
    return out


def check_frf_lidar(text=_text, open_nc=_open_nc) -> dict:
    year, f = frf_newest("lidarHydrodynamics", text)
    ds = open_nc(f"{FRF_DODS}lidarHydrodynamics/{year}/{f}")
    x = ds.variables["xFRF"][:]
    return {"file": f, "last_time": _last_time(ds), "times": len(ds.variables["time"]),
            "xFRF_m": [round(float(x.min()), 1), round(float(x.max()), 1)]}


def check_iem_latest(text=_text) -> dict:
    prod = text(f"{IEM_AFOS}?pil={SRF_PIL}&limit=1&fmt=text")
    issued = re.search(r"^\d{3,4} [AP]M [A-Z]{3} .+\d{4}$", prod, re.M)
    seg = next((s for s in srf_segments(prod) if SRF_ZONE in s["zones"]), None)
    if seg is None:
        raise ValueError(f"{SRF_PIL}: no {SRF_ZONE} segment in the newest product")
    heights = [(p, h) for p, h in seg["periods"] if h]
    if not heights:
        raise ValueError(f"{SRF_ZONE}: no surf height parsed ({seg['periods']})")
    return {"issued": issued.group(0) if issued else None, "segment": seg["name"],
            "surf_height_ft": heights[:3], "segments_in_product": len(srf_segments(prod))}


def check_iem_archive(text=_text) -> dict:
    a, b = ARCHIVE_RANGE
    prod = text(f"{IEM_AFOS}?pil={SRF_PIL}&sdate={a}&edate={b}&limit=20&fmt=text")
    n = prod.count("Surf Zone Forecast")
    if n == 0:
        raise ValueError(f"no {SRF_PIL} products between {a} and {b}")
    return {"range": [a, b], "products": n}


def check_cdip(open_nc=_open_nc) -> dict:
    ds = open_nc(f"{CDIP_DODS}143p1_rt.nc")
    return {"station": "143 Cape Canaveral nearshore", "last_time": _last_time(ds, "waveTime"),
            "last_waveHs_m": _last(ds, "waveHs"), "depth_m": round(float(ds.variables["metaWaterDepth"][:]), 1)}


def check_zone(text=_text) -> dict:
    z = json.loads(text(f"{NWS_ZONE}{SRF_ZONE}", headers={"Accept": "application/geo+json"}))
    geom = z.get("geometry")
    if not geom:
        raise ValueError(f"{SRF_ZONE}: no geometry in the zone record")
    km = distance_to_geometry_km(*DUCK_PIER, geom)
    if km > ZONE_TOLERANCE_KM:
        raise ValueError(f"Duck Pier is {km:.2f} km from {SRF_ZONE}'s outline "
                         f"(more than {ZONE_TOLERANCE_KM} km)")
    return {"zone": SRF_ZONE, "name": z.get("properties", {}).get("name"),
            "geometry": geom.get("type"), "duck_pier_km_from_zone": round(km, 3)}


CHECKS = [
    ("frf 8m-array (OPeNDAP + fileServer)", check_frf_8m),
    ("frf lidarHydrodynamics (OPeNDAP)", check_frf_lidar),
    ("iem newest SRFMHX, NCZ203 parsed", check_iem_latest),
    ("iem archive SRFMHX", check_iem_archive),
    ("cdip 143 realtime (OPeNDAP)", check_cdip),
    ("api.weather.gov zone NCZ203 outline", check_zone),
]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--out", help="write the report as JSON here")
    args = ap.parse_args(argv)
    results = [_check(name, fn) for name, fn in CHECKS]
    print(f"=== face-height sources from this runner, {datetime.now(timezone.utc):%Y-%m-%dT%H:%MZ} ===")
    for r in results:
        detail = {k: v for k, v in r.items() if k not in ("check", "ok", "seconds")}
        print(f"{'PASS' if r['ok'] else 'FAIL'}  {r['check']}  ({r['seconds']} s)  {json.dumps(detail, default=str)}")
    failed = [r["check"] for r in results if not r["ok"]]
    print(f"{len(results) - len(failed)} of {len(results)} reachable"
          + (f"; FAILED: {', '.join(failed)}" if failed else ""))
    if args.out:
        with open(args.out, "w") as f:
            json.dump(results, f, indent=2, default=str)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
