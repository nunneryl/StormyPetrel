"""WAVEWATCH III (NCEP gfswave) forecast fetcher.

NCEP runs the WAVEWATCH III model globally and publishes per-step GRIB2
files at https://nomads.ncep.noaa.gov/pub/data/nccf/com/wave/prod/. The
critical thing this gives us — and that NWPS doesn't — is full directional
swell decomposition: SWELL_1/SWPER_1/SWDIR_1 (primary swell partition),
SWELL_2/.../SWDIR_2, SWELL_3/.../SWDIR_3, plus wind-sea. That's the same
data Surfline / MagicSeaweed quote when they show "2ft 10s NNW + 0.4ft
15s NW + 0.4ft 11s WSW".

Cycle structure on NOMADS (gfswave is nested inside the GFS cycle tree as
of the 2022 NCEP unification — the legacy /com/wave/prod/ path now only
serves NFCENS files)::

    /pub/data/nccf/com/gfs/prod/
        gfs.YYYYMMDD/
            HH/                       # cycle hour (00, 06, 12, 18Z)
                wave/gridded/
                    gfswave.t{HH}z.{grid}.f{FFF}.grib2

For our use case the *global.0p25* grid covers everything we care about
(CONUS + HI + PR) at ~28 km spacing — coarse but plenty fine for a
direction/period field that's basically constant across that scale offshore.

Each grib_filter download is ~100 KB after variable + bbox subsetting, so a
full forecast cycle is ~5 MB across all step files. We sample every 3 h out
to 168 h to keep extraction tight; gfswave publishes hourly out to 120 h
and 3-hourly to 384 h.

Output: ``pipeline/forecast_data/ww3.json`` keyed by spot name with hourly
records carrying the three swell partitions plus wind-sea components, e.g.::

    {
      "Banzai Pipeline": [
        {
          "valid_time": "2026-04-28T18:00:00Z",
          "swell_1_hs": 0.61, "swell_1_tp": 10.5, "swell_1_dp": 337,
          "swell_2_hs": 0.12, "swell_2_tp": 15.2, "swell_2_dp": 318,
          "swell_3_hs": 0.10, "swell_3_tp": 11.4, "swell_3_dp": 250,
          "wind_wave_hs": 1.5, "wind_wave_tp": 5.2, "wind_wave_dp": 65
        },
        ...
      ]
    }
"""
from __future__ import annotations

import json
import logging
import math
import re
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path

from ..config import (
    WW3_CACHE_DIR,
    WW3_CYCLE_LOOKBACK,
    WW3_CYCLE_SUBPATH,
    WW3_DATE_PREFIX,
    WW3_FILE_PREFIX,
    WW3_FORECAST_FILE,
    WW3_GRID,
    WW3_NOMADS_BASE,
    WW3_STEP_HOURS,
)
from ..http import session

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Cycle discovery
# ---------------------------------------------------------------------------

_DATE_HREF_RE = re.compile(r'href="' + re.escape(WW3_DATE_PREFIX) + r'\.(\d{8})/"')
_HH_HREF_RE = re.compile(r'href="(\d{2})/"')


def _get_text(url: str) -> str | None:
    try:
        resp = session().get(url, timeout=60, allow_redirects=True)
    except Exception as e:  # noqa: BLE001
        log.warning("ww3: GET %s failed: %s", url, e)
        return None
    if resp.status_code != 200:
        log.warning("ww3: GET %s → %d", url, resp.status_code)
        return None
    return resp.text


@lru_cache(maxsize=1)
def _list_dates() -> list[str]:
    """Return [YYYYMMDD newest-first] from the gfswave Apache index."""
    html = _get_text(f"{WW3_NOMADS_BASE}/")
    if html is None:
        return []
    dates = sorted({m for m in _DATE_HREF_RE.findall(html)}, reverse=True)
    log.info("ww3: NOMADS lists %d %s.* date dirs (newest=%s)",
             len(dates), WW3_DATE_PREFIX, dates[0] if dates else "—")
    return dates


@lru_cache(maxsize=None)
def _list_cycles_on_date(date_ymd: str) -> list[str]:
    """Return [HH newest-first] cycle dirs under gfs.{date}/."""
    url = f"{WW3_NOMADS_BASE}/{WW3_DATE_PREFIX}.{date_ymd}/"
    html = _get_text(url)
    if html is None:
        return []
    # Cycle dirs (00/06/12/18/) live alongside non-numeric subdirs we don't
    # care about (atmos/, chem/, wave/), so the regex's \d{2} pattern is
    # already restrictive enough.
    return sorted(set(_HH_HREF_RE.findall(html)), reverse=True)


def candidate_cycles() -> list[tuple[str, str]]:
    """Up to WW3_CYCLE_LOOKBACK (date, HH) candidates, newest-first."""
    out: list[tuple[str, str]] = []
    for date in _list_dates()[:3]:
        for hh in _list_cycles_on_date(date):
            out.append((date, hh))
            if len(out) >= WW3_CYCLE_LOOKBACK:
                return out
    return out


# ---------------------------------------------------------------------------
# Per-step GRIB download via grib_filter
# ---------------------------------------------------------------------------

def _step_filename(hh: str, fhour: int) -> str:
    # Filenames inside the cycle still start with `gfswave.` even though the
    # parent directory is named `gfs.YYYYMMDD/...`.
    return f"{WW3_FILE_PREFIX}.t{hh}z.{WW3_GRID}.f{fhour:03d}.grib2"


def _step_direct_url(date_ymd: str, hh: str, fhour: int) -> str:
    """Full NOMADS URL of a single forecast-step GRIB.

    The legacy filter_gfswave.pl CGI was set up against the old
    /com/wave/prod/gfswave.YYYYMMDD/ tree and silently rejects requests
    for the new /com/gfs/prod/gfs.YYYYMMDD/HH/wave/gridded/ layout, so we
    download the per-step file directly. Each file is ~12 MB; ~640 MB
    total for a 168 h cycle at 3 h spacing. Cached under WW3_CACHE_DIR
    so subsequent runs hit disk only.
    """
    return (
        f"{WW3_NOMADS_BASE}/{WW3_DATE_PREFIX}.{date_ymd}/{hh}/"
        f"{WW3_CYCLE_SUBPATH}/{_step_filename(hh, fhour)}"
    )


def _step_cache_path(date_ymd: str, hh: str, fhour: int) -> Path:
    return WW3_CACHE_DIR / f"{date_ymd}_{hh}_f{fhour:03d}.grib2"


def _download_step(date_ymd: str, hh: str, fhour: int, dest: Path) -> bool:
    url = _step_direct_url(date_ymd, hh, fhour)
    try:
        with session().get(url, stream=True, timeout=180) as resp:
            if resp.status_code != 200:
                log.debug(
                    "ww3: GET %s → %d", url, resp.status_code,
                )
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".partial")
            written = 0
            with tmp.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
                        written += len(chunk)
            # A truncated download (NOMADS 502 gateway HTML page, etc.) is
            # smaller than any real grib2 file. Real cycle files are >5 MB.
            if written < 100_000:
                tmp.unlink(missing_ok=True)
                return False
            tmp.replace(dest)
        return True
    except Exception as e:  # noqa: BLE001
        log.debug("ww3: GET %s failed: %s", url, e)
        return False


def _locate_cycle(use_cache: bool) -> tuple[str, str, list[Path]] | None:
    """Resolve the newest gfswave cycle that yields enough step files.

    Returns (date_ymd, hh, [paths in fhour order]) or None if no cycle
    has at least 24 step files available (one full day of forecast).
    """
    cycles = candidate_cycles()
    if not cycles:
        log.warning("ww3: no cycles available on NOMADS")
        return None

    for date_ymd, hh in cycles:
        paths: list[Path] = []
        missing = 0
        for fhour in WW3_STEP_HOURS:
            p = _step_cache_path(date_ymd, hh, fhour)
            if p.exists() and p.stat().st_size > 0 and use_cache:
                paths.append(p)
                continue
            if _download_step(date_ymd, hh, fhour, p):
                paths.append(p)
            else:
                missing += 1
        # Need at least 24h of forecast to be useful.
        if len(paths) >= 8:
            log.info(
                "ww3: cycle %s %sZ — %d/%d step files available (%d missing)",
                date_ymd, hh, len(paths), len(WW3_STEP_HOURS), missing,
            )
            return date_ymd, hh, paths
        log.info(
            "ww3: cycle %s %sZ — only %d step files; trying next",
            date_ymd, hh, len(paths),
        )
    return None


# ---------------------------------------------------------------------------
# GRIB parsing — partition extraction
# ---------------------------------------------------------------------------

# cfgrib shortName → (output prefix, field) for each thing we care about. The
# prefixes mirror the JSON layout: swell_{n}_{hs|tp|dp}, wind_wave_{hs|tp|dp},
# total_{hs|tp|dp}.
_PARTITION_MAP: dict[str, tuple[str, str]] = {
    # Total (combined) — useful as a ground-truth cross-check.
    "swh":   ("total", "hs"),
    "htsgw": ("total", "hs"),
    "perpw": ("total", "tp"),
    "dirpw": ("total", "dp"),
    # Wind sea
    "wvhgt": ("wind_wave", "hs"),
    "wvper": ("wind_wave", "tp"),
    "wvdir": ("wind_wave", "dp"),
    "shww":  ("wind_wave", "hs"),
    "mpww":  ("wind_wave", "tp"),
    "mdww":  ("wind_wave", "dp"),
    # Swell partitions. cfgrib maps NCEP's _1/_2/_3 partition variables to
    # different shortNames depending on the table version; cover both
    # documented spellings.
    "swell": ("swell_1", "hs"),
    "swper": ("swell_1", "tp"),
    "swdir": ("swell_1", "dp"),
    "swell_1": ("swell_1", "hs"),
    "swper_1": ("swell_1", "tp"),
    "swdir_1": ("swell_1", "dp"),
    "swell_2": ("swell_2", "hs"),
    "swper_2": ("swell_2", "tp"),
    "swdir_2": ("swell_2", "dp"),
    "swell_3": ("swell_3", "hs"),
    "swper_3": ("swell_3", "tp"),
    "swdir_3": ("swell_3", "dp"),
    # cfgrib also surfaces partitions through these alt keys in some setups.
    "shps":  ("swell_1", "hs"),
    "mpps":  ("swell_1", "tp"),
    "mdps":  ("swell_1", "dp"),
}


def _open_grib_datasets(path: Path) -> list:
    import cfgrib
    return cfgrib.open_datasets(str(path))


def _normalize_longitude(ds, lng: float) -> float:
    try:
        lon_min = float(ds["longitude"].min())
    except (KeyError, ValueError):
        return lng
    if lon_min >= 0 and lng < 0:
        return lng + 360.0
    return lng


def _resolve_valid_time(ds) -> datetime | None:
    """Return the single forecast valid_time of *ds* (each step file holds one)."""
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


def _extract_step(datasets: list, lat: float, lng: float) -> dict | None:
    """Extract one step's worth of partition values at (lat, lng).

    Returns a flat dict like ``{swell_1_hs, swell_1_tp, swell_1_dp, ...,
    valid_time}`` or None if the step has no usable values.
    """
    import numpy as np

    out: dict = {}
    valid_time = None
    for ds in datasets:
        vt = _resolve_valid_time(ds)
        if vt is not None and valid_time is None:
            valid_time = vt
        lng_adj = _normalize_longitude(ds, lng)
        try:
            point = ds.sel(latitude=lat, longitude=lng_adj, method="nearest")
        except Exception:  # noqa: BLE001
            continue
        for var_name in point.data_vars:
            mapping = _PARTITION_MAP.get(str(var_name).lower())
            if mapping is None:
                continue
            prefix, field = mapping
            arr = np.atleast_1d(np.asarray(point[var_name].values)).ravel()
            if arr.size == 0:
                continue
            try:
                v = float(arr[0])
            except (TypeError, ValueError):
                continue
            if math.isnan(v):
                continue
            key = f"{prefix}_{field}"
            # First source wins per (prefix, field) — respects map ordering.
            out.setdefault(key, round(v, 3))
    if valid_time is None:
        return None
    out["valid_time"] = valid_time.isoformat().replace("+00:00", "Z")
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
    """Fetch the latest gfswave cycle and extract per-spot partition series.

    The cycle's step files are downloaded once (cached under WW3_CACHE_DIR)
    and then opened in turn — for each step, every spot gets its nearest
    grid-cell value extracted. Result is keyed by spot name.
    """
    located = _locate_cycle(use_cache)
    if located is None:
        log.warning("ww3: no usable cycle; returning empty result")
        return {}
    date_ymd, hh, paths = located
    log.info(
        "ww3: extracting %d steps × %d spots from cycle %s %sZ",
        len(paths), len(spots), date_ymd, hh,
    )

    out: dict[str, list[dict]] = {}
    parse_failed = 0

    try:
        from tqdm import tqdm
        iterator = tqdm(paths, desc="ww3 steps", unit="step")
    except ImportError:
        iterator = paths

    for path in iterator:
        try:
            datasets = _open_grib_datasets(path)
        except Exception as e:  # noqa: BLE001
            parse_failed += 1
            log.debug("ww3: open %s failed: %s", path.name, e)
            continue
        if not datasets:
            parse_failed += 1
            continue

        # On the very first step, log the dataset shape so we can diagnose
        # cfgrib variable naming if the partition extraction comes back empty.
        if path == paths[0]:
            for i, ds in enumerate(datasets):
                vars_ = list(ds.data_vars)
                log.info("ww3: ds[%d] vars=%s", i, vars_)

        for spot in spots:
            try:
                lat = float(spot["lat"])
                lng = float(spot["lng"])
            except (KeyError, ValueError, TypeError):
                continue
            entry = _extract_step(datasets, lat, lng)
            if entry is None:
                continue
            # Keep only entries that have at least one swell partition value;
            # gfswave masks land cells as NaN, and we don't want to pollute
            # the time series with "we got a wind value but no waves" rows.
            if not any(k.startswith(("swell_", "wind_wave_", "total_"))
                       and v is not None for k, v in entry.items() if k != "valid_time"):
                continue
            out.setdefault(spot["name"], []).append(entry)

    # Order each series by valid_time so consumers can iterate forward.
    for series in out.values():
        series.sort(key=lambda e: e.get("valid_time", ""))

    WW3_FORECAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    WW3_FORECAST_FILE.write_text(json.dumps(out, ensure_ascii=False))
    log.info(
        "ww3: wrote %d spots to %s (parse_failed=%d step files)",
        len(out), WW3_FORECAST_FILE, parse_failed,
    )
    return out
