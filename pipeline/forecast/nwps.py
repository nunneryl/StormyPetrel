"""NWPS (Nearshore Wave Prediction System) forecast fetcher.

NOAA's operational SWAN wave model produces forecaster-triggered runs at each
coastal Weather Forecast Office (WFO). WFOs are grouped under an NWS region
(er/sr/wr/pr/ar) on NOMADS, so the download path is
``{region}.{YYYYMMDD}/{wfo}/{HH}/CG1/<wfo>_nwps_CG1_{YYYYMMDD}_{HH}00.grib2``.

Downloads flow through NOMADS's grib_filter CGI (``filter_{region}nwps.pl``)
so we pull only a handful of variables — HTSGW, PERPW, DIRPW, SWELL, SWPER,
SWDIR, WIND, WDIR — which shrinks a per-WFO run from 100–300 MB to 30–50 MB.

For every spot we:

1. Resolve the WFO by state + lat/lng and persist ``nwps_wfo`` to
   spots_enriched.json.
2. Group spots by WFO, fetch the region's grib_filter listing once, take the
   newest (date, HH) tuple that lists this WFO, download the subsetted GRIB,
   and cache it.
3. Open the GRIB2 with xarray + cfgrib, merge all param groups, and extract
   the nearest grid point's full time series per spot.
4. Write ``pipeline/forecast_data/nwps.json`` keyed by spot name.

Requires the eccodes system lib (``apt install libeccodes0`` on Debian/Ubuntu;
``brew install eccodes`` on macOS).
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
    NWPS_CACHE_DIR,
    NWPS_CYCLE_LOOKBACK,
    NWPS_FORECAST_FILE,
    NWPS_GRIB_FILTER_BASE,
    NWPS_GRIB_VARS,
    NWPS_NOMADS_BASE,
    WFO_TO_REGION,
)
from ..http import session

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# WFO assignment
# ---------------------------------------------------------------------------

# Known NWPS-capable WFO codes (subset relevant to US surfing coast).
KNOWN_WFOS = frozenset({
    "sgx", "lox", "mtr", "eka",
    "mfr", "pqr", "sew",
    "hfo",
    "mfl", "tbw", "jax", "mlb",
    "mhx", "ilm", "chs",
    "akq", "phi", "okx", "box", "gyx", "car",
    "bro", "crp", "hgx",
    "sju",
})


def assign_wfo(state: str | None, lat: float, lng: float) -> str | None:
    """Return the NWPS WFO code for a coastal spot, or None if unmapped.

    Splits are lat/lng-based since several states span multiple WFOs.
    """
    s = (state or "").strip()

    if s == "Hawaii":
        return "hfo"
    if s == "Puerto Rico":
        return "sju"

    if s == "California":
        # SGX: San Diego county (≤ ~33.5°N)
        # LOX: Orange / LA / Ventura / Santa Barbara (≤ ~34.9°N)
        # MTR: Monterey / Bay Area / northern coast (≤ ~39.0°N)
        # EKA: Eureka and north
        if lat < 33.55:
            return "sgx"
        if lat < 34.9:
            return "lox"
        if lat < 39.0:
            return "mtr"
        return "eka"

    if s == "Oregon":
        # MFR: southern OR (≤ ~43.5°N), PQR: northern OR / SW WA
        return "mfr" if lat < 43.5 else "pqr"

    if s == "Washington":
        # PQR covers the southern WA outer coast; SEW covers the outer coast
        # north of ~46.7°N plus the inner Puget Sound waters.
        if lng > -123.5:
            return "sew"
        return "pqr" if lat < 46.7 else "sew"

    if s == "Florida":
        # Florida Keys (south of ~25.5°N) — mfl regardless of longitude.
        if lat < 25.5:
            return "mfl"
        # Peninsular west vs east split: the geographic median of the
        # peninsula sits around lng = -81.7. West of that is Gulf (tbw);
        # east is Atlantic (mfl/mlb/jax by latitude).
        if lng < -81.7:
            return "tbw"
        if lat < 27.1:
            return "mfl"   # Miami / Palm Beach south
        if lat < 28.7:
            return "mlb"   # Melbourne (covers Sebastian → Daytona)
        return "jax"       # Jacksonville

    if s == "North Carolina":
        # MHX covers Outer Banks / Hatteras / northern NC coast.
        # ILM covers Wilmington / Topsail / southern NC coast.
        return "mhx" if lat >= 35.0 else "ilm"

    if s in ("South Carolina", "Georgia"):
        return "chs"

    if s in ("Virginia", "Maryland"):
        return "akq"

    if s in ("Delaware", "New Jersey", "Pennsylvania"):
        return "phi"

    if s in ("New York", "Connecticut"):
        return "okx"

    if s in ("Rhode Island", "Massachusetts", "New Hampshire"):
        return "box"

    if s == "Maine":
        # GYX: southern Maine. CAR: Caribou / far northern Maine (rare for surf).
        return "car" if lat > 45.5 else "gyx"

    if s == "Texas":
        if lat < 27.0:
            return "bro"   # Brownsville
        if lat < 28.5:
            return "crp"   # Corpus Christi
        return "hgx"       # Houston / Galveston

    return None


def apply_wfos(spots: list[dict]) -> dict[str, int]:
    """Populate `nwps_wfo` on each spot in place. Returns per-WFO spot counts."""
    counts: dict[str, int] = {}
    unmapped = 0
    for s in spots:
        if "nwps_wfo" in s and s["nwps_wfo"]:
            w = s["nwps_wfo"]
        else:
            w = assign_wfo(s.get("region_hint"), s.get("lat"), s.get("lng"))
            s["nwps_wfo"] = w
        if w is None:
            unmapped += 1
        else:
            counts[w] = counts.get(w, 0) + 1
    if unmapped:
        log.info("nwps: %d spots have no WFO (unmapped region) — they will be skipped", unmapped)
    return counts


# ---------------------------------------------------------------------------
# Cycle selection + GRIB download
# ---------------------------------------------------------------------------

def _grib_filename(wfo: str, date_ymd: str, hh: str) -> str:
    return f"{wfo}_nwps_CG1_{date_ymd}_{hh}00.grib2"


def _grib_dir_path(region: str, wfo: str, date_ymd: str, hh: str) -> str:
    """The ``dir`` query-param value the grib_filter CGI expects."""
    return f"/{region}.{date_ymd}/{wfo}/{hh}/CG1"


def _grib_path(wfo: str, date_ymd: str, hh: str) -> Path:
    return NWPS_CACHE_DIR / f"{wfo}_{date_ymd}_{hh}.grib2"


def _direct_grib_url(region: str, wfo: str, date_ymd: str, hh: str) -> str:
    """Full-file URL on NOMADS (useful for manual verification / debugging)."""
    return (
        f"{NWPS_NOMADS_BASE}/{region}.{date_ymd}/{wfo}/{hh}/CG1/"
        f"{_grib_filename(wfo, date_ymd, hh)}"
    )


def _filter_url(region: str) -> str:
    return f"{NWPS_GRIB_FILTER_BASE}/filter_{region}nwps.pl"


def _filter_download_params(region: str, wfo: str, date_ymd: str, hh: str) -> dict:
    """Query params for the grib_filter download with variable + level subsetting."""
    params = {
        "file": _grib_filename(wfo, date_ymd, hh),
        "dir": _grib_dir_path(region, wfo, date_ymd, hh),
    }
    for var in NWPS_GRIB_VARS:
        params[f"var_{var}"] = "on"
    # NWPS wave/wind variables live at surface.
    params["lev_surface"] = "on"
    return params


# ---------------------------------------------------------------------------
# Cycle discovery via the NOMADS Apache directory listing
# ---------------------------------------------------------------------------
#
# The grib_filter CGI page is populated by JavaScript (its initial HTML
# doesn't contain the directory options), so scraping it returns nothing.
# The /pub/data/nccf/com/nwps/prod/ tree, by contrast, is a plain Apache
# autoindex — the HTML has <a href="name/"> links we can parse with a regex.

_DATE_HREF_RE = re.compile(r'href="([a-z]{2})\.(\d{8})/"', re.IGNORECASE)
_HH_HREF_RE = re.compile(r'href="(\d{2})/"')


def _get_text(url: str) -> str | None:
    """GET a URL via the shared session; return body text, or None on failure."""
    try:
        resp = session().get(url, timeout=60, allow_redirects=True)
    except Exception as e:  # noqa: BLE001
        log.warning("nwps: GET %s failed: %s", url, e)
        return None
    if resp.status_code != 200:
        log.warning("nwps: GET %s → %d", url, resp.status_code)
        return None
    return resp.text


@lru_cache(maxsize=1)
def _list_root_dates() -> dict[str, list[str]]:
    """Return {region_code: [YYYYMMDD newest-first]} from the NOMADS root
    index at /pub/data/nccf/com/nwps/prod/. Memoized once per process.
    """
    html = _get_text(f"{NWPS_NOMADS_BASE}/")
    if html is None:
        return {}
    out: dict[str, list[str]] = {}
    for region, date in _DATE_HREF_RE.findall(html):
        out.setdefault(region.lower(), []).append(date)
    for dates in out.values():
        dates.sort(reverse=True)  # newest first
    log.info(
        "nwps: NOMADS root lists dates for %d regions — %s",
        len(out), ", ".join(f"{r}:{len(d)}" for r, d in sorted(out.items())),
    )
    if not out:
        snippet = html[:400].replace("\n", " ")
        log.info("nwps: root listing yielded nothing; first 400 chars: %s", snippet)
    return out


@lru_cache(maxsize=None)
def _list_wfo_cycles(region: str, date_ymd: str, wfo: str) -> list[str]:
    """Return [HH newest-first] for the given {region}.{date}/{wfo}/ dir.

    Each NWPS WFO run has its cycle as a numeric subdirectory (e.g. 00/, 06/,
    12/, 18/). Empty list means the WFO hasn't run on this date yet.
    """
    url = f"{NWPS_NOMADS_BASE}/{region}.{date_ymd}/{wfo}/"
    html = _get_text(url)
    if html is None:
        return []
    hhs = sorted(set(_HH_HREF_RE.findall(html)), reverse=True)
    return hhs


def candidate_cycles(wfo: str) -> list[tuple[str, str]]:
    """Up to NWPS_CYCLE_LOOKBACK (date, HH) candidates for *wfo*, newest-first,
    from the NOMADS directory listing.
    """
    region = WFO_TO_REGION.get(wfo)
    if region is None:
        return []
    dates = _list_root_dates().get(region, [])
    if not dates:
        return []
    result: list[tuple[str, str]] = []
    # Only the three most recent date dirs — cycles don't persist longer than
    # that on NOMADS, and each date lookup is one extra HTTP request per WFO.
    for date_ymd in dates[:3]:
        for hh in _list_wfo_cycles(region, date_ymd, wfo):
            result.append((date_ymd, hh))
            if len(result) >= NWPS_CYCLE_LOOKBACK:
                return result
    return result


def _download_filtered(region: str, wfo: str, date_ymd: str, hh: str, dest: Path) -> bool:
    """Stream-download the subsetted GRIB via grib_filter to *dest*."""
    url = _filter_url(region)
    params = _filter_download_params(region, wfo, date_ymd, hh)
    try:
        s = session()
        with s.get(url, params=params, stream=True, timeout=300) as resp:
            if resp.status_code != 200:
                log.warning(
                    "nwps: grib_filter %s/%s/%s%sZ returned %d",
                    region, wfo, date_ymd, hh, resp.status_code,
                )
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".partial")
            bytes_written = 0
            with tmp.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
                        bytes_written += len(chunk)
            if bytes_written < 1024:
                # A 0-variable filter or an error-page render returns a tiny body.
                log.warning(
                    "nwps: grib_filter returned suspiciously small body (%d bytes) for %s/%s/%s%sZ — discarding",
                    bytes_written, region, wfo, date_ymd, hh,
                )
                tmp.unlink(missing_ok=True)
                return False
            tmp.replace(dest)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("nwps: grib_filter download %s/%s/%s%sZ failed: %s",
                    region, wfo, date_ymd, hh, e)
        return False


def _locate_cycle(wfo: str, use_cache: bool) -> tuple[Path, str, str] | None:
    """Find a usable GRIB2 file for this WFO — cached or via cycle fallback.

    Returns (local_path, date_ymd, hh) or None if no cycle is available.
    """
    region = WFO_TO_REGION.get(wfo)
    if region is None:
        log.warning("nwps: WFO %s has no region mapping", wfo)
        return None

    cycles = candidate_cycles(wfo)
    if not cycles:
        dates = _list_root_dates().get(region, [])
        latest_dir = (
            f"{NWPS_NOMADS_BASE}/{region}.{dates[0]}/{wfo}/"
            if dates else f"{NWPS_NOMADS_BASE}/"
        )
        log.info(
            "nwps: %s — no cycles listed in NOMADS index. Check: %s",
            wfo, latest_dir,
        )
        return None

    # Cache-first check against every candidate.
    for date_ymd, hh in cycles:
        path = _grib_path(wfo, date_ymd, hh)
        if path.exists() and path.stat().st_size > 0 and use_cache:
            log.info("nwps: %s/%s %sZ — cache hit (%s)", wfo, date_ymd, hh, path.name)
            return path, date_ymd, hh

    # Download the newest cycle that exists.
    for date_ymd, hh in cycles:
        path = _grib_path(wfo, date_ymd, hh)
        log.info(
            "nwps: %s/%s %sZ — downloading subset (%s) via %s",
            wfo, date_ymd, hh, ",".join(NWPS_GRIB_VARS), _filter_url(region),
        )
        if _download_filtered(region, wfo, date_ymd, hh, path):
            return path, date_ymd, hh

    log.info(
        "nwps: %s — %d cycles listed but none downloaded successfully. Sample file URL: %s",
        wfo, len(cycles), _direct_grib_url(region, wfo, *cycles[0]),
    )
    return None


# ---------------------------------------------------------------------------
# GRIB parsing
# ---------------------------------------------------------------------------

# Map cfgrib shortName (lowercase) to output key.
_VAR_MAP = {
    "swh":   "hs",
    "htsgw": "hs",
    "perpw": "tp",
    "dirpw": "dp",
    "mwp":   "tp",           # NWPS sometimes publishes mean period
    "mwd":   "dp",
    "shww":  "swell_hs",
    "shts":  "swell_hs",         # significant height of total swell (NWPS)
    "swell": "swell_hs",
    "swh_swell": "swell_hs",
    "swper": "swell_tp",
    "swdir": "swell_dp",
    "si10":  "wind_speed",
    "ws":    "wind_speed",       # NWPS GRIB uses `ws`
    "wind":  "wind_speed",
    "10u":   "wind_u_ms",
    "10v":   "wind_v_ms",
    "wdir10": "wind_dir",
    "wdir":  "wind_dir",
}


def _open_grib_datasets(path: Path) -> list:
    """Open a GRIB2 file and return the list of cfgrib-grouped datasets."""
    import cfgrib  # lazy — eccodes may not be installed on dev machines
    return cfgrib.open_datasets(str(path))


def _normalize_longitude(ds, lng: float) -> float:
    """Pick the right lng convention for the dataset (0-360 vs -180/180)."""
    try:
        lon_min = float(ds["longitude"].min())
    except (KeyError, ValueError):
        return lng
    if lon_min >= 0 and lng < 0:
        return lng + 360.0
    return lng


def _describe_dataset(ds, idx: int) -> str:
    """One-line summary of an xarray Dataset for diagnostic logging."""
    dims = dict(ds.sizes)
    coords = list(ds.coords)
    vars_ = list(ds.data_vars)
    return f"ds[{idx}] dims={dims} coords={coords} vars={vars_}"


def _resolve_time_axis(obj):
    """Return a 1-D numpy array of valid_time values for *obj* (Dataset or DataArray).

    NWPS GRIBs expose `time` as the forecast reference (cycle run) and `step`
    as offsets; cfgrib also derives `valid_time` as a coordinate. Any of the
    three can arrive as a 0-d scalar when there's a single step.
    """
    import numpy as np

    if "valid_time" in obj.coords:
        vt = np.asarray(obj["valid_time"].values)
    elif "step" in obj.coords and "time" in obj.coords:
        base = np.asarray(obj["time"].values)
        step = np.asarray(obj["step"].values)
        if base.ndim == 0:
            vt = base + np.atleast_1d(step)
        else:
            vt = (base.reshape(-1, 1) + step.reshape(1, -1)).ravel()
    elif "time" in obj.coords:
        vt = np.asarray(obj["time"].values)
    else:
        return None
    return np.atleast_1d(vt).ravel()


def _extract_time_series_from_datasets(datasets: list, lat: float, lng: float) -> list[dict]:
    """Nearest-grid-point time series, combined across cfgrib param groups.

    NWPS GRIBs are opened by cfgrib as multiple datasets (one per param group:
    wave surface, wind at 10m, swell components, ...), each with its own
    `step` grid. Merging them collapses the time dimension when step grids
    differ, so we extract per-dataset and union records by valid_time.
    """
    import numpy as np
    import pandas as pd

    records: dict[str, dict] = {}

    for ds in datasets:
        lng_adj = _normalize_longitude(ds, lng)
        try:
            point = ds.sel(latitude=lat, longitude=lng_adj, method="nearest")
        except Exception as e:  # noqa: BLE001
            log.debug("nwps: .sel failed on a dataset for (%.4f, %.4f): %s", lat, lng, e)
            continue

        vt = _resolve_time_axis(point)
        if vt is None or vt.size == 0:
            continue
        times = pd.DatetimeIndex(pd.to_datetime(vt, utc=True))

        for var_name in point.data_vars:
            out_key = _VAR_MAP.get(str(var_name).lower())
            if out_key is None:
                continue
            vals = np.atleast_1d(np.asarray(point[var_name].values)).ravel()
            n = min(len(times), len(vals))
            for i in range(n):
                try:
                    val = float(vals[i])
                except (TypeError, ValueError):
                    continue
                if math.isnan(val):
                    continue
                t_iso = times[i].isoformat().replace("+00:00", "Z")
                entry = records.setdefault(t_iso, {"valid_time": t_iso})
                # First source wins per output key (respects _VAR_MAP priority).
                entry.setdefault(out_key, round(val, 3))

    return [records[t] for t in sorted(records.keys())]


# ---------------------------------------------------------------------------
# Public entrypoint
# ---------------------------------------------------------------------------

def fetch(
    spots: list[dict],
    use_cache: bool = True,
    wfo_filter: list[str] | None = None,
    input_path: Path | None = None,
) -> dict[str, list[dict]]:
    """Populate nwps_wfo on every spot, then fetch NWPS forecasts for every
    requested WFO and extract per-spot hourly time series.

    Writes pipeline/forecast_data/nwps.json and (if *input_path* is given)
    the updated spots back to that path so nwps_wfo persists.

    Parameters
    ----------
    spots : list of enriched-spot dicts (mutated to add nwps_wfo).
    use_cache : reuse previously-downloaded GRIB2 files when present.
    wfo_filter : limit to this list of WFOs (lowercase codes).
    input_path : if set, re-serialize the spots list here after WFO
        assignment so nwps_wfo persists to disk.
    """
    wfo_counts = apply_wfos(spots)
    log.info("nwps: WFO distribution — %s", dict(sorted(wfo_counts.items())))

    # Persist nwps_wfo to spots_enriched.json (only if something changed).
    if input_path is not None:
        try:
            input_path.write_text(json.dumps(spots, indent=2, ensure_ascii=False))
            log.info("nwps: wrote %d spots with nwps_wfo back to %s", len(spots), input_path)
        except Exception as e:  # noqa: BLE001
            log.warning("nwps: failed to persist nwps_wfo to %s: %s", input_path, e)

    if wfo_filter:
        wfos_to_fetch = [w for w in wfo_counts if w in set(wfo_filter)]
        log.info("nwps: --wfo filter narrowed to %s (from %d WFOs)",
                 wfos_to_fetch, len(wfo_counts))
    else:
        wfos_to_fetch = sorted(wfo_counts.keys())
    if not wfos_to_fetch:
        log.warning("nwps: no WFOs to fetch — exiting")
        return {}

    NWPS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out: dict[str, list[dict]] = {}
    wfos_ok = 0
    wfos_missing = 0
    wfos_parse_failed = 0
    spots_with_data = 0

    try:
        from tqdm import tqdm
        iterator = tqdm(wfos_to_fetch, desc="nwps wfos", unit="wfo")
    except ImportError:
        iterator = wfos_to_fetch

    for wfo in iterator:
        located = _locate_cycle(wfo, use_cache)
        if located is None:
            wfos_missing += 1
            log.warning("nwps: no cycle available for WFO %s in the lookback window", wfo)
            continue
        grib_path, cycle_date, cycle_hh = located

        try:
            datasets = _open_grib_datasets(grib_path)
        except Exception as e:  # noqa: BLE001
            wfos_parse_failed += 1
            log.exception("nwps: GRIB parse failed for %s (%s): %s", wfo, grib_path, e)
            continue
        if not datasets:
            wfos_parse_failed += 1
            log.warning("nwps: cfgrib produced no datasets for %s", wfo)
            continue

        # Log dims/coords/vars for each cfgrib-grouped dataset — essential for
        # diagnosing step-axis or variable-name mismatches.
        for i, ds in enumerate(datasets):
            log.info("nwps: %s %s", wfo, _describe_dataset(ds, i))

        wfos_ok += 1
        wfo_spots = [s for s in spots if s.get("nwps_wfo") == wfo]
        log.info("nwps: %s (%sZ %s) — extracting %d spots from %s",
                 wfo, cycle_hh, cycle_date, len(wfo_spots), grib_path.name)
        for spot in wfo_spots:
            series = _extract_time_series_from_datasets(
                datasets, float(spot["lat"]), float(spot["lng"])
            )
            if series:
                out[spot["name"]] = series
                spots_with_data += 1

    NWPS_FORECAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    NWPS_FORECAST_FILE.write_text(json.dumps(out, ensure_ascii=False))  # no indent — large
    log.info(
        "nwps: wrote %d spots to %s (WFOs ok=%d, missing=%d, parse_failed=%d)",
        spots_with_data, NWPS_FORECAST_FILE, wfos_ok, wfos_missing, wfos_parse_failed,
    )
    return out
