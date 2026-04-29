"""HRRR (High-Resolution Rapid Refresh) wind forecast fetcher.

NCEP's HRRR runs every hour at 3 km Lambert-conformal resolution over
CONUS. The 00 / 06 / 12 / 18 Z cycles publish 49 step files (f000–f048);
off-cycle hours only go to f018. We always pick the most recent long
cycle so the fetched window covers two full forecast days hour-by-hour.

Per-step file layout on NOMADS::

    /pub/data/nccf/com/hrrr/prod/
        hrrr.YYYYMMDD/
            conus/
                hrrr.t{HH}z.wrfsfcf{FH}.grib2     # one per forecast hour

Each full file is ~150 MB; after grib_filter subsetting to UGRD + VGRD
at 10 m above ground level the per-step download drops to ~5 MB. A full
49-step cycle is therefore ~250 MB cached locally.

Output: ``pipeline/forecast_data/hrrr.json`` keyed by spot name with
hourly records {valid_time, wind_speed (m/s), wind_dir (deg, met
convention — direction wind is *coming from*)}. Spots outside the
CONUS bbox (Hawaii / Puerto Rico / Alaska) are skipped — interpret.py
falls back to NWPS wind for them.
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timezone
from functools import lru_cache
from pathlib import Path

from ..config import (
    HRRR_CACHE_DIR,
    HRRR_CONUS_BBOX,
    HRRR_CYCLE_LOOKBACK,
    HRRR_FORECAST_FILE,
    HRRR_GRIB_FILTER_URL,
    HRRR_GRIB_LEVEL,
    HRRR_GRIB_VARS,
    HRRR_LONG_CYCLES,
    HRRR_NOMADS_BASE,
    HRRR_STEP_HOURS,
)
from ..http import session

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cycle discovery
# ---------------------------------------------------------------------------

_DATE_HREF_RE = re.compile(r'href="hrrr\.(\d{8})/"')
_FILE_HREF_RE = re.compile(r'href="hrrr\.t(\d{2})z\.wrfsfcf(\d{2})\.grib2"')


def _get_text(url: str) -> str | None:
    try:
        resp = session().get(url, timeout=60, allow_redirects=True)
    except Exception as e:  # noqa: BLE001
        log.warning("hrrr: GET %s failed: %s", url, e)
        return None
    if resp.status_code != 200:
        log.warning("hrrr: GET %s → %d", url, resp.status_code)
        return None
    return resp.text


@lru_cache(maxsize=1)
def _list_dates() -> list[str]:
    """Return [YYYYMMDD newest-first] from the HRRR Apache index."""
    html = _get_text(f"{HRRR_NOMADS_BASE}/")
    if html is None:
        return []
    dates = sorted({m for m in _DATE_HREF_RE.findall(html)}, reverse=True)
    log.info("hrrr: NOMADS lists %d hrrr.* date dirs (newest=%s)",
             len(dates), dates[0] if dates else "—")
    return dates


def _list_conus_files(date_ymd: str) -> set[tuple[str, str]]:
    """Return the set of (HH, FH) pairs published under hrrr.{date}/conus/."""
    url = f"{HRRR_NOMADS_BASE}/hrrr.{date_ymd}/conus/"
    html = _get_text(url)
    if html is None:
        return set()
    return {(hh, fh) for hh, fh in _FILE_HREF_RE.findall(html)}


def candidate_cycles() -> list[tuple[str, str]]:
    """Up to HRRR_CYCLE_LOOKBACK (date, HH) long-horizon candidates,
    newest-first. A candidate qualifies only when the f048 step is
    published — partial-cycle uploads are dropped.
    """
    out: list[tuple[str, str]] = []
    for date in _list_dates()[:3]:
        files = _list_conus_files(date)
        if not files:
            continue
        # Take the newest long-cycle HH on this date that has f048
        # available. HRRR uploads are sequential, so the presence of
        # f048 is a reliable "cycle complete" signal.
        long_cycles_on_date = sorted(
            {hh for hh, fh in files if hh in HRRR_LONG_CYCLES and fh == "48"},
            reverse=True,
        )
        for hh in long_cycles_on_date:
            out.append((date, hh))
            if len(out) >= HRRR_CYCLE_LOOKBACK:
                return out
    return out


# ---------------------------------------------------------------------------
# Per-step grib_filter download
# ---------------------------------------------------------------------------

def _step_filename(hh: str, fh: int) -> str:
    return f"hrrr.t{hh}z.wrfsfcf{fh:02d}.grib2"


def _step_cache_path(date_ymd: str, hh: str, fh: int) -> Path:
    return HRRR_CACHE_DIR / f"{date_ymd}_{hh}_f{fh:02d}.grib2"


def _filter_params(date_ymd: str, hh: str, fh: int) -> dict:
    params = {
        "file": _step_filename(hh, fh),
        "dir": f"/hrrr.{date_ymd}/conus",
        HRRR_GRIB_LEVEL: "on",
    }
    for var in HRRR_GRIB_VARS:
        params[f"var_{var}"] = "on"
    lat_min, lat_max, lng_min, lng_max = HRRR_CONUS_BBOX
    params.update({
        "subregion": "",
        "leftlon": f"{lng_min:.2f}",
        "rightlon": f"{lng_max:.2f}",
        "toplat": f"{lat_max:.2f}",
        "bottomlat": f"{lat_min:.2f}",
    })
    return params


def _download_step(date_ymd: str, hh: str, fh: int, dest: Path) -> bool:
    params = _filter_params(date_ymd, hh, fh)
    try:
        with session().get(HRRR_GRIB_FILTER_URL, params=params, stream=True, timeout=180) as resp:
            if resp.status_code != 200:
                log.debug("hrrr: filter %s/%sZ f%02d → %d",
                          date_ymd, hh, fh, resp.status_code)
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".partial")
            written = 0
            with tmp.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
            # An empty filter response is a tiny HTML error page; a real
            # subset of UGRD/VGRD over the CONUS bbox is at least a few
            # hundred KB. Floor at 50 KB to be safe.
            if written < 50_000:
                tmp.unlink(missing_ok=True)
                return False
            tmp.replace(dest)
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("hrrr: filter %s/%sZ f%02d failed: %s", date_ymd, hh, fh, e)
        return False


def _locate_cycle(use_cache: bool) -> tuple[str, str, list[tuple[int, Path]]] | None:
    """Resolve the newest long cycle that yields enough step files to be
    useful. Returns (date_ymd, hh, [(fh, path), ...]) or None.
    """
    cycles = candidate_cycles()
    if not cycles:
        log.warning("hrrr: no long cycles available on NOMADS")
        return None
    for date_ymd, hh in cycles:
        steps: list[tuple[int, Path]] = []
        missing = 0
        for fh in HRRR_STEP_HOURS:
            p = _step_cache_path(date_ymd, hh, fh)
            if p.exists() and p.stat().st_size > 0 and use_cache:
                steps.append((fh, p))
                continue
            if _download_step(date_ymd, hh, fh, p):
                steps.append((fh, p))
            else:
                missing += 1
        # Need 24 h of forecast minimum to be worth integrating.
        if len(steps) >= 24:
            log.info(
                "hrrr: cycle %s %sZ — %d/%d step files available (%d missing)",
                date_ymd, hh, len(steps), len(HRRR_STEP_HOURS), missing,
            )
            return date_ymd, hh, steps
        log.info(
            "hrrr: cycle %s %sZ — only %d step files; trying next",
            date_ymd, hh, len(steps),
        )
    return None


# ---------------------------------------------------------------------------
# Lambert grid extraction — KDTree over flattened lat/lng
# ---------------------------------------------------------------------------

# cfgrib shortName for HRRR 10 m wind components. Tuple covers historical
# variant naming (some cfgrib tables expose them under different keys).
_U_NAMES = ("u10", "ugrd", "10u")
_V_NAMES = ("v10", "vgrd", "10v")


def _open_grib_dataset(path: Path):
    import cfgrib
    return cfgrib.open_datasets(str(path))


def _close_datasets(datasets) -> None:
    for ds in datasets:
        try:
            ds.close()
        except Exception:  # noqa: BLE001
            pass


def _resolve_valid_time(ds) -> datetime | None:
    import numpy as np
    import pandas as pd
    if "valid_time" in ds.coords:
        vt = np.atleast_1d(np.asarray(ds["valid_time"].values)).ravel()
    elif "time" in ds.coords and "step" in ds.coords:
        base = np.atleast_1d(np.asarray(ds["time"].values)).ravel()
        step = np.atleast_1d(np.asarray(ds["step"].values)).ravel()
        if base.size and step.size:
            vt = base + step
        else:
            return None
    elif "time" in ds.coords:
        vt = np.atleast_1d(np.asarray(ds["time"].values)).ravel()
    else:
        return None
    if vt.size == 0:
        return None
    return pd.to_datetime(vt[0], utc=True).to_pydatetime()


def _find_uv_dataset(datasets):
    """Return (dataset, u_name, v_name) for the first dataset that has both
    a U and V wind component variable.
    """
    for ds in datasets:
        u_match = next((v for v in ds.data_vars if str(v).lower() in _U_NAMES), None)
        v_match = next((v for v in ds.data_vars if str(v).lower() in _V_NAMES), None)
        if u_match and v_match:
            return ds, str(u_match), str(v_match)
    return None, None, None


def _build_kdtree_for_grid(ds):
    """Return (kdtree, lat_grid, lng_grid, shape) for the dataset's
    2D lat/lng coordinates. Caches on the dataset's id so we don't rebuild
    once per spot.
    """
    import numpy as np
    from scipy.spatial import cKDTree
    if "latitude" in ds.coords:
        lats = np.asarray(ds["latitude"].values, dtype=float)
        lngs = np.asarray(ds["longitude"].values, dtype=float)
    elif "lat" in ds.coords:
        lats = np.asarray(ds["lat"].values, dtype=float)
        lngs = np.asarray(ds["lon"].values, dtype=float)
    else:
        return None
    # HRRR ships longitudes as 0–360. Wrap to -180..180 so spot lngs match.
    lngs = np.where(lngs > 180, lngs - 360.0, lngs)
    flat = np.column_stack([lats.ravel(), lngs.ravel()])
    tree = cKDTree(flat)
    return tree, lats.shape, lats, lngs


def _wind_speed_dir(u: "np.ndarray", v: "np.ndarray") -> tuple["np.ndarray", "np.ndarray"]:
    """U/V (m/s, mathematical convention) → speed (m/s), direction (deg met).

    Meteorological wind direction is the direction the wind is coming
    *from*, measured clockwise from north. atan2(-u, -v) gives that
    directly when u/v are easting/northing components.
    """
    import numpy as np
    speed = np.sqrt(u * u + v * v)
    direction = (np.degrees(np.arctan2(-u, -v)) + 360.0) % 360.0
    return speed, direction


def _spots_in_conus(spots: list[dict]) -> list[dict]:
    lat_min, lat_max, lng_min, lng_max = HRRR_CONUS_BBOX
    out: list[dict] = []
    for s in spots:
        try:
            lat = float(s["lat"])
            lng = float(s["lng"])
        except (KeyError, ValueError, TypeError):
            continue
        if lat_min <= lat <= lat_max and lng_min <= lng <= lng_max:
            out.append(s)
    return out


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def fetch(
    spots: list[dict],
    use_cache: bool = True,
    wfo_filter: list[str] | None = None,  # accepted for fetch_all signature parity
    input_path: Path | None = None,        # ditto; not used here
) -> dict[str, list[dict]]:
    """Fetch the latest long HRRR cycle and extract per-spot hourly wind.

    Returns a dict keyed by spot name with hourly records — same shape as
    nwps.json so interpret.py can join the two trivially.
    """
    conus_spots = _spots_in_conus(spots)
    log.info(
        "hrrr: %d / %d spots inside CONUS bbox — non-CONUS spots fall back "
        "to NWPS wind",
        len(conus_spots), len(spots),
    )
    if not conus_spots:
        return {}

    located = _locate_cycle(use_cache)
    if located is None:
        log.warning("hrrr: no usable cycle; returning empty result")
        return {}
    date_ymd, hh, steps = located
    log.info(
        "hrrr: extracting %d steps × %d CONUS spots from cycle %s %sZ",
        len(steps), len(conus_spots), date_ymd, hh,
    )

    import numpy as np

    spot_names: list[str] = [s["name"] for s in conus_spots]
    spot_lats = np.asarray([float(s["lat"]) for s in conus_spots], dtype=float)
    spot_lngs = np.asarray([float(s["lng"]) for s in conus_spots], dtype=float)
    spot_pts = np.column_stack([spot_lats, spot_lngs])

    out: dict[str, list[dict]] = {}
    parse_failed = 0
    flat_idx: "np.ndarray" | None = None
    grid_shape: tuple[int, int] | None = None

    try:
        from tqdm import tqdm
        iterator = tqdm(steps, desc="hrrr steps", unit="step")
    except ImportError:
        iterator = steps

    for fh, path in iterator:
        try:
            datasets = _open_grib_dataset(path)
        except Exception as e:  # noqa: BLE001
            parse_failed += 1
            log.debug("hrrr: open %s failed: %s", path.name, e)
            continue
        if not datasets:
            parse_failed += 1
            continue

        try:
            ds, u_name, v_name = _find_uv_dataset(datasets)
            if ds is None:
                if path == steps[0][1]:
                    for i, d in enumerate(datasets):
                        log.info("hrrr: ds[%d] vars=%s", i, list(d.data_vars))
                parse_failed += 1
                continue
            if path == steps[0][1]:
                log.info(
                    "hrrr: U/V vars resolved as %s / %s on dataset with dims %s",
                    u_name, v_name, dict(ds.sizes),
                )

            # Build the KDTree on the first usable step and reuse it —
            # HRRR's Lambert grid is identical across every step file in a
            # cycle, so the (spot → flat grid index) mapping is constant.
            if flat_idx is None:
                tree_pack = _build_kdtree_for_grid(ds)
                if tree_pack is None:
                    parse_failed += 1
                    continue
                tree, grid_shape, _, _ = tree_pack
                _, flat_idx = tree.query(spot_pts)

            valid_time = _resolve_valid_time(ds)
            if valid_time is None:
                continue
            valid_iso = valid_time.isoformat().replace("+00:00", "Z")

            u_arr = np.squeeze(np.asarray(ds[u_name].values, dtype=float))
            v_arr = np.squeeze(np.asarray(ds[v_name].values, dtype=float))
            if u_arr.shape != grid_shape or v_arr.shape != grid_shape:
                continue
            u_flat = u_arr.ravel()
            v_flat = v_arr.ravel()
            u_at_spots = u_flat[flat_idx]
            v_at_spots = v_flat[flat_idx]
            speed_at_spots, dir_at_spots = _wind_speed_dir(u_at_spots, v_at_spots)

            for i, name in enumerate(spot_names):
                u = u_at_spots[i]
                v = v_at_spots[i]
                if not (np.isfinite(u) and np.isfinite(v)):
                    continue
                out.setdefault(name, []).append({
                    "valid_time": valid_iso,
                    "wind_speed": round(float(speed_at_spots[i]), 3),
                    "wind_dir": round(float(dir_at_spots[i]), 3),
                })
        finally:
            _close_datasets(datasets)

    # Order each series by valid_time so consumers can iterate forward.
    for series in out.values():
        series.sort(key=lambda e: e.get("valid_time", ""))

    HRRR_FORECAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    HRRR_FORECAST_FILE.write_text(json.dumps(out, ensure_ascii=False))
    log.info(
        "hrrr: wrote %d spots to %s (parse_failed=%d step files)",
        len(out), HRRR_FORECAST_FILE, parse_failed,
    )
    return out
