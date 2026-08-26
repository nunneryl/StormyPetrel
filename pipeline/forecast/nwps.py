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

    if s in ("Rhode Island", "Massachusetts"):
        return "box"

    if s == "New Hampshire":
        return "gyx"   # Gray/Portland ME WFO covers the NH coast

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
    # swell_hs comes ONLY from shts ("Significant height of total swell") so it is
    # deterministic. shww is "Significant height of WIND WAVES" (windsea) — NOT swell —
    # so it maps to its own key and must never land in swell_hs. `swell`/`swh_swell`
    # were nondeterministic substitutes for shts (first-wins on cfgrib dataset order)
    # and are DROPPED: if a grid lacks shts, swell_hs stays None rather than silently
    # borrowing a different field.
    "shts":  "swell_hs",         # eccodes "Significant height of total swell"
    "shww":  "windsea_hs",       # eccodes "Significant height of wind waves" (windsea, not swell)
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


# Wave variables we test against to detect a land cell. NWPS land cells return
# all-NaN for every wave field, so any of these going non-NaN means we hit
# water. Order doesn't matter — we use the first one that exists in a dataset.
_WAVE_VARS_FOR_LAND_CHECK = ("swh", "htsgw", "shts", "shww", "swell")

# How far from the nominal nearest-cell to search before giving up. 5 cells on
# the NWPS CG1 grid is roughly 2–3 km — enough to walk past most jetties /
# breakwaters / barrier islands without straying into a different surf zone.
_LAND_SEARCH_MAX_RADIUS = 5


def _find_wave_dataset(datasets: list):
    """Return (dataset, var_name) of the first wave-bearing dataset, else (None, None)."""
    for ds in datasets:
        for v in ds.data_vars:
            if str(v).lower() in _WAVE_VARS_FOR_LAND_CHECK:
                return ds, str(v)
    return None, None


def _cell_is_water(datasets: list, li: int, lj: int) -> bool:
    """Is grid cell (li, lj) a wet cell? Water iff ANY wave variable in ANY dataset
    carries ANY finite value there; land only when every one of them is entirely NaN.

    THE ONE land test. Both callers route through it — _baked_node_is_water and
    _find_offshore_point's `_is_water` closure — so the two paths cannot drift apart
    and reach different cells for the same reason. Do not add a second copy.

    WHY THE UNION, AND WHY IT MUST NOT BE "OPTIMISED" BACK TO FIRST-MATCH.
    This used to take one (dataset, variable) pair from _find_wave_dataset — the FIRST
    match, in cfgrib grouping order — and test only that. Measured against the real
    gyx 2026-08-24 18Z CG1 cycle, cfgrib splits that file into two datasets carrying the
    SAME six variables: ds[0] is the analysis hour f000 with no step dimension (scalar
    step 0), ds[1] is steps 1..144. _find_wave_dataset returned ds[0], so the docstring's
    old claim that land is "all-NaN across the step axis" was false here — there is no
    step axis on ds[0], and the array evaluated had shape (1,). The whole land verdict
    rested on ONE forecast hour.

    That is not a rare edge. On the gyx grid 108 of 13287 cells (0.81%) are NaN at step 0
    and finite at some later step. 95% of them have at least one wet neighbour at step 0
    (mean 3.37, against 0.19 for stable-land cells): they are the wet/dry boundary, which
    is exactly where surf spots sit. Short Sands Beach York's baked node is cell
    (li=34, lj=38) — NaN at step 0, so production called it LAND, while carrying 69
    finite values across ds[1] ranging 0.3401 to 0.7895 m. A rejected node with two
    thirds of a forecast behind it.

    Step 0 is not anomalously sparse — 6629 finite cells against a forecast-step range of
    6605 to 6736. ANY single step is a coin flip for a boundary cell; that is the defect,
    not something special about the analysis hour.

    AND IT IS NOT ONE-DIRECTIONAL, which is why "just read the multi-step dataset" is
    also wrong. On the same grid, cell (51, 15) — 43.5501N, 288.7070E — is finite at
    step 0 (swh 0.0480) and all-NaN across every one of the 144 forecast steps. Reading
    all steps is NOT a strict superset of reading step 0. Only the union over every
    dataset and every wave variable is correct in both directions.

    A total absence of wave variables returns WATER, not land: with nothing to read we
    cannot tell them apart, so we trust the input rather than discard the spot. That is
    the same posture _find_offshore_point and _baked_node_is_water take when
    _find_wave_dataset comes back empty.

    A read that RAISES is not evidence of land — it is the absence of evidence — so it
    skips to the next variable instead of returning False. With a single wave variable
    that fails, the loop still ends in the all-NaN branch and returns land, exactly as
    the old try/except did.
    """
    import numpy as np

    # Indices are grid-relative: the callers derive li/lj from _find_wave_dataset's
    # dataset, so only datasets on THAT grid describe the same cell. A dataset with a
    # different shape would silently answer for a different location, so it is skipped
    # rather than trusted. On NWPS CG1 every group shares the nest, so this is a no-op.
    ref_shape = None
    seen_a_wave_var = False

    for ds in datasets:
        try:
            names = [str(v) for v in ds.data_vars]
        except Exception:  # noqa: BLE001
            continue
        wave_names = [v for v in names if v.lower() in _WAVE_VARS_FOR_LAND_CHECK]
        if not wave_names:
            continue

        try:
            shape = (int(ds.sizes["latitude"]), int(ds.sizes["longitude"]))
        except Exception:  # noqa: BLE001
            shape = None
        if ref_shape is None:
            ref_shape = shape
        elif shape is not None and shape != ref_shape:
            continue

        for v in wave_names:
            seen_a_wave_var = True
            try:
                sample = ds[v].isel(latitude=li, longitude=lj)
                arr = np.atleast_1d(np.asarray(sample.values)).ravel()
                if not np.all(np.isnan(arr)):
                    return True
            except Exception:  # noqa: BLE001
                continue

    # Every wave variable we could read was entirely NaN -> land. No wave variable at
    # all -> water (trust the input).
    return not seen_a_wave_var


def _baked_node_is_water(datasets: list, lat: float, lng: float) -> bool:
    """Does the baked seaward node land on a wet cell in this cycle's grid?

    A baked nwps_node_lat/lng is a NODE CENTRE that select_node already resolved on a
    wet cell, so this should always be True. It is checked anyway because the roster is
    baked once and the grid is re-fetched every cycle: a mask change, a regridded nest,
    or a stale assignment would otherwise sample land silently and return all-null.
    A False here is a DATA problem worth surfacing, not a condition to swallow.
    """
    wave_ds, _wave_var = _find_wave_dataset(datasets)
    if wave_ds is None:
        # No wave variable in this run — can't tell land from water. Same posture as
        # _find_offshore_point takes in this case: trust the input.
        return True

    import numpy as np

    # _find_wave_dataset still picks the REFERENCE GRID here — the axes li/lj are
    # resolved against. The land verdict itself is the union over every dataset; see
    # _cell_is_water for why one dataset's answer is not enough.
    lng_adj = _normalize_longitude(wave_ds, lng)
    lats = np.asarray(wave_ds["latitude"].values)
    lngs = np.asarray(wave_ds["longitude"].values)
    li = int(np.argmin(np.abs(lats - lat)))
    lj = int(np.argmin(np.abs(lngs - lng_adj)))
    return _cell_is_water(datasets, li, lj)


def _seaward_diag(spot: dict, point_lat: float, point_lng: float):
    """(bearing, off_normal_deg, is_seaward) from *spot* to the chosen cell, or None.

    DIAGNOSTIC ONLY — nothing here selects a node. It exists because the fallback log
    line reported distance and never direction, so a walk into the sound behind a
    barrier island and a walk out to sea printed identically as "fell back at N cells
    away". 47 spots were doing the former silently.

    "Seaward" is the same half-plane select_node uses to CHOOSE nodes: within ±90° of
    orientation_deg. The bearing maths is imported from nwps_nearshore rather than
    re-derived here so the two cannot drift — the import is function-local to keep this
    fetcher's import-time dependencies unchanged. Returns None when the spot carries no
    orientation_deg, since then there is no normal to measure against.
    """
    orientation = spot.get("orientation_deg")
    if orientation is None:
        return None
    from .nwps_nearshore import _ang_within, _bearing   # local: no import-time coupling

    brg = _bearing(float(spot["lat"]), float(spot["lng"]), point_lat, point_lng)
    off = abs(((brg - float(orientation) + 180.0) % 360.0) - 180.0)
    return brg, off, _ang_within(brg, float(orientation), 90.0)


def _sampled_distance_km(spot_lat: float, spot_lng: float,
                         point_lat: float, point_lng: float) -> float:
    """Great-circle km from the spot to the cell actually sampled.

    DIAGNOSTIC ONLY — nothing here selects or rejects a cell. This fetcher computed no
    metric distance at all: the fallback line reported a CELL COUNT ("fell back at 1
    cells away") and, later, a bearing, but never a distance. The Wedge sampled a cell
    305 km away for two months behind a stale nwps_wfo and no log said so.

    The maths is imported from nwps_nearshore rather than re-derived so the fetcher and
    the placement pass cannot disagree about what a distance is; the import is
    function-local to keep this module's import-time dependencies unchanged, the same
    way _seaward_diag borrows _bearing / _ang_within.
    """
    from .nwps_nearshore import _haversine_km   # local: no import-time coupling

    return _haversine_km(spot_lat, spot_lng, point_lat, point_lng)


def _grid_spacing_and_cap_km(datasets: list) -> tuple[float, float]:
    """(node spacing km, far cap km) for this cycle's grid. (0.0, floor) if undeterminable.

    Mirrors nwps_nearshore.grid_spacing_km / grid_far_cap_km — the MEDIAN adjacent-node
    step on each axis, converted to km at the grid's MEDIAN latitude and averaged, then
    capped as max(FAR_CAP_FLOOR_KM, FAR_CAP_MULT * spacing). The constants are imported
    rather than restated so the fetcher's cap and the placement pass's cap are the same
    number by construction.

    ONE DELIBERATE DIFFERENCE FROM THE SIBLING. grid_spacing_km takes a `cycle` whose
    lats/lons are 2-D meshgrids and guards on `ndim != 2`, slicing the axes back out as
    lats[:, 0] / lons[0, :]. cfgrib hands THIS module the 1-D axes directly, so that
    guard does not transfer and is not reproduced: the axes are used as they arrive. A
    degenerate axis (fewer than two points) contributes no step, and a grid that yields
    no step at all falls back to the floor.

    Computed once per WFO — the grid does not vary across the spots of one cycle.
    """
    import numpy as np

    from .nwps_nearshore import FAR_CAP_FLOOR_KM, FAR_CAP_MULT, _haversine_km

    wave_ds, _var = _find_wave_dataset(datasets)
    if wave_ds is None:
        return 0.0, FAR_CAP_FLOOR_KM
    try:
        lat1d = np.asarray(wave_ds["latitude"].values).ravel()
        lng1d = np.asarray(wave_ds["longitude"].values).ravel()
    except Exception:  # noqa: BLE001
        return 0.0, FAR_CAP_FLOOR_KM

    mid_lat = float(np.median(lat1d)) if lat1d.size else 0.0
    steps = []
    if lat1d.size > 1:
        dlat = float(np.median(np.abs(np.diff(lat1d))))
        if dlat > 0:
            steps.append(_haversine_km(mid_lat, 0.0, mid_lat + dlat, 0.0))
    if lng1d.size > 1:
        dlng = float(np.median(np.abs(np.diff(lng1d))))
        if dlng > 0:
            steps.append(_haversine_km(mid_lat, 0.0, mid_lat, dlng))
    spacing = sum(steps) / len(steps) if steps else 0.0
    cap = max(FAR_CAP_FLOOR_KM, FAR_CAP_MULT * spacing) if spacing > 0 else FAR_CAP_FLOOR_KM
    return spacing, cap


def _warn_impossible_swell_pairs(series: list[dict], spot_name: str) -> int:
    """Count and log records where swell_hs > hs. Returns the count; MUTATES NOTHING.

    Significant swell height is a COMPONENT of total significant height, so
    swell_hs > hs is physically impossible — it cannot be a real sea state, only a
    sampling or parsing fault. The values are deliberately left exactly as read:
    clamping or dropping them would hide the fault while still corrupting the rating
    downstream. Making it loud is the point.
    """
    bad = 0
    for rec in series:
        hs, swell_hs = rec.get("hs"), rec.get("swell_hs")
        if hs is None or swell_hs is None:
            continue
        if swell_hs > hs:
            bad += 1
            log.warning(
                "nwps: %s %s — IMPOSSIBLE swell_hs %.3f m > hs %.3f m "
                "(swell is a component of total; values left unmodified)",
                spot_name, rec.get("valid_time"), swell_hs, hs,
            )
    return bad


def _find_offshore_point(
    datasets: list,
    lat: float,
    lng: float,
    max_radius: int = _LAND_SEARCH_MAX_RADIUS,
    orientation: float | None = None,
) -> tuple[float, float, int, bool] | None:
    """Nearest SEAWARD ocean grid cell to (lat, lng).

    The NWPS wave model masks land cells as NaN. For spots tucked behind
    jetties, sand spits, or whose lat/lng resolves to a coastline cell on
    the model grid, the naive "nearest neighbor" lookup lands on land and
    every wave variable comes back null.

    Returns ``(corrected_lat, corrected_lng, fallback_cells, seaward_ok)``, where
    ``fallback_cells`` is the chosen cell's ring distance — 0 when the nominal nearest
    cell was taken — or ``None`` when every cell within *max_radius* is land.

    THE DIRECTION IS PART OF THE SELECTION, NOT A NOTE ADDED AFTERWARDS.
    This used to walk outward in expanding rings and accept the FIRST wet cell it
    touched, with no directional constraint at all. `_seaward_diag` then measured which
    way it had gone and wrote the answer into a log line — after the cell was already
    committed. On a barrier-island or point coast the first cell examined at radius 1 is
    a landward diagonal, so the walk sampled the water BEHIND the break: measured on the
    2026-08-24 cycles, Mole Point sampled 3.80 km away at 117° off its own shore normal
    and Mavericks 1.27 km at 100° off, both reported and neither corrected.

    THE RULE IS select_node's, NOT A SECOND ONE. Filter the candidates to the ±90°
    half-plane around *orientation* FIRST, then take the nearest survivor — the same
    order nwps_nearshore.select_node applies to the 600 baked nodes, using the same
    imported `_ang_within` / `_bearing`. `_ang_within` compares with `<=`, so exactly
    90.0° off normal counts as INSIDE the half-plane; that boundary is inherited, not
    restated.

    THIS CHANGES TWO THINGS AT ONCE, AND THE SECOND IS THE BIGGER ONE. The half-plane
    filter is the stated fix, but adopting select_node's `min(sea, key=dist)` also
    replaces the traversal: the old walk took the first wet cell in CHEBYSHEV RING order
    with index-order tie-breaking, and this one takes the nearest survivor by GREAT
    CIRCLE. Those two metrics disagree constantly — a ring-1 diagonal is reached before a
    ring-2 edge cell but is not necessarily nearer, and within one ring the visit order is
    an artefact of index arithmetic, not geometry.

    Measured against THIS implementation on the sgx 2026-08-24 12Z, mtr 2026-08-24 00Z,
    lox 2026-08-24 06Z and eka 2026-08-22 12Z cycles, 31 of the 48 ring-walk spots change
    cell — sgx 20 of 31, mtr 6 of 10, lox 4 of 6, eka 1 of 1. Only three of those were
    sampling landward. The other 28 move because of the metric change, not the filter.

    29 of the 31 move CLOSER, several substantially:
        The Wedge        6.09 km -> 2.75 km
        Lunada Bay       6.70 km -> 3.82 km
        Pescadero        4.38 km -> 1.62 km
        Stinson Beach    3.90 km -> 1.77 km
    That improvement is a CONSEQUENCE of matching select_node's min-by-distance, not a
    separate optimisation bolted on. Do not treat it as a tunable knob: it is whatever
    picking the nearest survivor happens to give.

    Only two move farther, and both were sampling landward — the trade the filter exists
    to make:
        Mavericks, California  1.27 km at 100° off -> 1.98 km at  7° off
        Humboldt Bay Jetty     1.80 km at 131° off -> 2.23 km at 54° off
    Mole Point, the third landward spot, gets both: 3.80 km at 117° -> 1.79 km at 66°.

    DISTANCE DECIDES AMONG SURVIVORS, so a spot can take a WORSE off-normal angle while
    getting closer — T Street Beach 3° -> 69°, Swamis 1° -> 63°. Every one of them is
    still inside the half-plane, which is the only guarantee the rule makes. That is
    select_node's trade-off and it is inherited deliberately; a variant that minimised
    off-normal angle instead would be a different rule from the one placing the 600 baked
    nodes, and the two would drift.

    IT MUST NEVER REFUSE A SPOT. If the half-plane admits no wet cell inside
    *max_radius* the pick falls back to the nearest wet cell regardless of direction and
    ``seaward_ok`` comes back False, so the caller can name it. A skipped spot produces
    NO forecast rows at all and blanks its page — see the block at the sampled-distance
    check below. Measured over all 48 ring-walk spots on the four cycles above, the
    fallback fired ZERO times; it is loud precisely because it should stay that way.

    ``seaward_ok`` is True when the pick satisfies the half-plane, and also True when no
    *orientation* was supplied — there is no constraint to fail.

    WITHOUT an *orientation* the original ring walk runs UNCHANGED, first-wet-in-ring-
    order and all. That is deliberate rather than tidy: with no shore normal there is no
    basis on which to prefer one direction, and the two traversals disagree far more
    often than on ties alone — 28 of the 48 spots above move on the metric change with no
    landward problem to fix. Switching unoriented spots to great-circle-nearest would
    move their cells too, for a reason nothing in their record states. All 48 ring-walk
    spots carry an orientation_deg today, so this branch is unreached in production; it
    exists so that a future spot without one behaves exactly as it always has.
    """
    import numpy as np

    wave_ds, _wave_var = _find_wave_dataset(datasets)
    if wave_ds is None:
        # No wave variable in this run — can't tell land from water; trust
        # the input. (Wind-only runs etc.)
        return lat, lng, 0, True

    # As in _baked_node_is_water: this picks the REFERENCE GRID for the indices, while
    # the land verdict is the union over every dataset — see _cell_is_water.
    lng_adj = _normalize_longitude(wave_ds, lng)
    lats = np.asarray(wave_ds["latitude"].values)
    lngs = np.asarray(wave_ds["longitude"].values)

    lat_idx = int(np.argmin(np.abs(lats - lat)))
    lng_idx = int(np.argmin(np.abs(lngs - lng_adj)))

    def _to_input_convention(ds_lng: float) -> float:
        # Reverse _normalize_longitude: if the dataset uses 0–360 and the
        # input was a negative (-180/180) lng, return to the negative form.
        if lng < 0 and ds_lng > 180:
            return ds_lng - 360.0
        return ds_lng

    n_lat = len(lats)
    n_lng = len(lngs)

    def _is_water(li: int, lj: int) -> bool:
        return _cell_is_water(datasets, li, lj)

    if orientation is None:
        # THE ORIGINAL RING WALK, unchanged — see the docstring for why it is kept
        # rather than folded into the oriented path.
        if _is_water(lat_idx, lng_idx):
            return (float(lats[lat_idx]),
                    _to_input_convention(float(lngs[lng_idx])), 0, True)
        for radius in range(1, max_radius + 1):
            for di in range(-radius, radius + 1):
                for dj in range(-radius, radius + 1):
                    # Only walk the outer ring at this radius — the interior was
                    # checked on previous iterations.
                    if max(abs(di), abs(dj)) != radius:
                        continue
                    ni = lat_idx + di
                    nj = lng_idx + dj
                    if 0 <= ni < n_lat and 0 <= nj < n_lng and _is_water(ni, nj):
                        return (float(lats[ni]),
                                _to_input_convention(float(lngs[nj])), radius, True)
        return None

    # Same maths select_node uses, imported rather than re-derived so the placement pass
    # and the walk cannot drift. Function-local, like _seaward_diag's, to keep this
    # module's import-time dependencies unchanged.
    from .nwps_nearshore import _ang_within, _bearing, _haversine_km

    # Every wet cell in the (2*max_radius+1)^2 box, each with its ring distance and its
    # true great-circle distance. The whole box is collected BEFORE anything is chosen,
    # because "nearest survivor of the filtered set" cannot be decided ring by ring —
    # the candidate set has to exist before the filter runs, exactly as in select_node.
    wet: list[tuple[float, float, int, float]] = []
    for di in range(-max_radius, max_radius + 1):
        for dj in range(-max_radius, max_radius + 1):
            ni = lat_idx + di
            nj = lng_idx + dj
            if not (0 <= ni < n_lat and 0 <= nj < n_lng):
                continue
            if not _is_water(ni, nj):
                continue
            clat = float(lats[ni])
            clng = _to_input_convention(float(lngs[nj]))
            wet.append((clat, clng, max(abs(di), abs(dj)),
                        _haversine_km(lat, lng, clat, clng)))

    if not wet:
        return None

    sea = [c for c in wet
           if _ang_within(_bearing(lat, lng, c[0], c[1]), float(orientation), 90)]
    if sea:
        c = min(sea, key=lambda c: c[3])
        return c[0], c[1], c[2], True

    # No seaward wet cell in range. Publish the nearest wet cell anyway and flag it —
    # a landward sample beats no forecast at all. select_node's own fallback is the
    # same `min(wet, key=dist)`.
    c = min(wet, key=lambda c: c[3])
    return c[0], c[1], c[2], False


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
    spots_offshore_ok = 0      # nominal nearest cell was already water
    spots_fallback = 0         # found water within search radius
    spots_skipped_land = 0     # no water cell within search radius
    # HOW the node was chosen, and — for the walk path — which WAY it went. The two
    # axes are orthogonal to the three counters above, which only count ring hops:
    # a 0-ring "nominal nearest cell" can still sit landward of the spot.
    nodes_baked = 0            # baked seaward node used (the good path)
    nodes_baked_land = 0       # baked node present but tested as land -> fell to the walk
    walk_seaward = 0           # ring walk ended within ±90° of orientation_deg
    walk_landward = 0          # ring walk ended OUTSIDE that half-plane — the defect
    walk_no_orientation = 0    # no orientation_deg, so direction is not assessable
    walk_no_seaward = 0        # ±90° filter found nothing in range -> landward by necessity
    baked_land_names: list[str] = []
    walk_landward_names: list[str] = []
    walk_no_seaward_names: list[str] = []
    impossible_pairs = 0       # records where swell_hs > hs (physically impossible)
    impossible_pair_spots: list[str] = []
    far_sampled = 0            # sampled cell beyond this grid's far cap — reported, NEVER refused
    far_sampled_names: list[str] = []

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
        # ONE grid per cycle, so derive the spacing and the far cap ONCE per WFO rather
        # than per spot. Same formula the placement pass uses — see _grid_spacing_and_cap_km.
        grid_spacing_km_v, far_cap_km = _grid_spacing_and_cap_km(datasets)
        log.info("nwps: %s grid spacing %.2f km -> sampled-distance cap %.2f km",
                 wfo, grid_spacing_km_v, far_cap_km)
        wfo_spots = [s for s in spots if s.get("nwps_wfo") == wfo]
        log.info("nwps: %s (%sZ %s) — extracting %d spots from %s",
                 wfo, cycle_hh, cycle_date, len(wfo_spots), grib_path.name)
        for spot in wfo_spots:
            spot_lat = float(spot["lat"])
            spot_lng = float(spot["lng"])
            # PREFER THE BAKED SEAWARD NODE. spots_enriched.json carries
            # nwps_node_lat/lng for most of the roster — the node select_node already
            # chose WITH the ±90° seaward half-plane rule and the placement far-cap.
            # This fetcher used to ignore it and re-derive a point from the raw spot
            # coordinate with _find_offshore_point, whose ring walk has no directional
            # constraint and accepts the first wet cell it touches. On a barrier-island
            # coast the first cell examined at radius 1 is a landward diagonal, so the
            # walk sampled the sound behind the island — where shts reads exactly 0.0
            # while swh carries local ripple. That is the source of swell_hs > hs.
            nlat, nlng = spot.get("nwps_node_lat"), spot.get("nwps_node_lng")
            corr_lat = corr_lng = None
            if nlat is not None and nlng is not None:
                if _baked_node_is_water(datasets, float(nlat), float(nlng)):
                    corr_lat, corr_lng = float(nlat), float(nlng)
                    nodes_baked += 1
                    log.info(
                        "nwps: %s: using baked seaward node (%.4f, %.4f) — %.2f km away",
                        spot["name"], corr_lat, corr_lng,
                        _sampled_distance_km(spot_lat, spot_lng, corr_lat, corr_lng),
                    )
                else:
                    # Stale assignment or a regridded nest — surface it, don't swallow it.
                    nodes_baked_land += 1
                    baked_land_names.append(spot["name"])
                    log.warning(
                        "nwps: %s — BAKED NODE (%.4f, %.4f) tests as LAND in this cycle's "
                        "grid; falling back to the ring walk. The assignment is stale or "
                        "the nest was regridded.",
                        spot["name"], float(nlat), float(nlng),
                    )

            if corr_lat is None:
                # The spot's own shore normal drives the walk now — the ±90° half-plane
                # is applied DURING selection, not measured after it. See
                # _find_offshore_point.
                found = _find_offshore_point(
                    datasets, spot_lat, spot_lng,
                    orientation=spot.get("orientation_deg"),
                )
                if found is None:
                    spots_skipped_land += 1
                    log.warning(
                        "nwps: %s — no ocean grid cell within %d cells of (%.4f, %.4f); skipping",
                        spot["name"], _LAND_SEARCH_MAX_RADIUS, spot_lat, spot_lng,
                    )
                    continue
                corr_lat, corr_lng, fallback_cells, seaward_ok = found
                if not seaward_ok:
                    # The half-plane admitted nothing in range, so the pick is landward
                    # by necessity rather than by accident. Measured zero times across
                    # all 48 ring-walk spots on sgx / mtr / lox / eka; if it ever fires,
                    # the spot is sampling behind its own break and someone has to look.
                    walk_no_seaward += 1
                    walk_no_seaward_names.append(spot["name"])
                    log.warning(
                        "nwps: %s — NO SEAWARD wet cell within %d cells of (%.4f, %.4f); "
                        "fell back to the nearest wet cell regardless of direction. "
                        "Published anyway, but the sample may be behind the break.",
                        spot["name"], _LAND_SEARCH_MAX_RADIUS, spot_lat, spot_lng,
                    )
                # Direction of the walk, which the old log line never reported.
                diag = _seaward_diag(spot, corr_lat, corr_lng)
                if diag is None:
                    walk_no_orientation += 1
                    where = "bearing unknown (spot has no orientation_deg)"
                elif diag[2]:
                    walk_seaward += 1
                    where = f"bearing {diag[0]:.0f}° = {diag[1]:.0f}° off normal, SEAWARD"
                else:
                    walk_landward += 1
                    walk_landward_names.append(spot["name"])
                    where = f"bearing {diag[0]:.0f}° = {diag[1]:.0f}° off normal, LANDWARD"
                walk_km = _sampled_distance_km(spot_lat, spot_lng, corr_lat, corr_lng)
                if fallback_cells > 0:
                    spots_fallback += 1
                    log.info(
                        "nwps: %s: nearest grid (%.4f, %.4f) was land, fell back to "
                        "(%.4f, %.4f) at %d cells / %.2f km away — %s",
                        spot["name"], spot_lat, spot_lng, corr_lat, corr_lng,
                        fallback_cells, walk_km, where,
                    )
                else:
                    spots_offshore_ok += 1
                    log.info("nwps: %s: nominal nearest cell (%.4f, %.4f) — %.2f km away — %s",
                             spot["name"], corr_lat, corr_lng, walk_km, where)

            # HOW FAR did we actually sample, on WHICHEVER path got us here? Checked once,
            # after both branches, so the baked node and the ring walk are measured alike.
            #
            # THIS REPORTS. IT MUST NEVER REFUSE. Do not "improve" this into a skip, a
            # None return, a substituted cell, or a clamp. A skipped spot produces NO
            # forecast rows AT ALL: it is absent from nwps.json, compute_ratings never
            # visits it, db_import writes nothing, and the frontend has nothing to
            # render. There is no fallback rater behind this — no buoy-only path, no
            # orientation stub. A slightly-wrong forecast beats a blank page, so an
            # over-cap sample is published and made loud, not withheld.
            sampled_km = _sampled_distance_km(spot_lat, spot_lng, corr_lat, corr_lng)
            if sampled_km > far_cap_km:
                far_sampled += 1
                far_sampled_names.append(spot["name"])
                log.warning(
                    "nwps: %s — sampled cell (%.4f, %.4f) is %.2f km away, OVER this "
                    "grid's %.2f km cap (spacing %.2f km). Published anyway; the "
                    "forecast is from a cell that far off.",
                    spot["name"], corr_lat, corr_lng, sampled_km, far_cap_km,
                    grid_spacing_km_v,
                )

            series = _extract_time_series_from_datasets(datasets, corr_lat, corr_lng)
            if series:
                # Physically impossible pairs are LOGGED, never clamped — see
                # _warn_impossible_swell_pairs. Counted so a run says how bad it was.
                n_bad = _warn_impossible_swell_pairs(series, spot["name"])
                if n_bad:
                    impossible_pairs += n_bad
                    impossible_pair_spots.append(spot["name"])
                out[spot["name"]] = series
                spots_with_data += 1

    NWPS_FORECAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    NWPS_FORECAST_FILE.write_text(json.dumps(out, ensure_ascii=False))  # no indent — large
    log.info(
        "nwps: wrote %d spots to %s (WFOs ok=%d, missing=%d, parse_failed=%d)",
        spots_with_data, NWPS_FORECAST_FILE, wfos_ok, wfos_missing, wfos_parse_failed,
    )
    log.info(
        "nwps: land-fallback summary — nearest-water=%d, fell-back=%d, no-water-within-%d-cells=%d",
        spots_offshore_ok, spots_fallback, _LAND_SEARCH_MAX_RADIUS, spots_skipped_land,
    )
    log.info(
        "nwps: node-selection summary — baked-node=%d, baked-node-rejected-as-land=%d, "
        "ring-walk-seaward=%d, ring-walk-LANDWARD=%d, ring-walk-no-orientation=%d, "
        "ring-walk-no-seaward-cell=%d, over-far-cap=%d",
        nodes_baked, nodes_baked_land, walk_seaward, walk_landward, walk_no_orientation,
        walk_no_seaward, far_sampled,
    )
    # NAME the bad ones. A bare count is easy to scroll past, and both of these are
    # conditions someone has to act on rather than watch.
    if walk_landward_names:
        log.warning("nwps: %d spot(s) sampled LANDWARD of their own shore normal: %s",
                    len(walk_landward_names), ", ".join(sorted(walk_landward_names)))
    if walk_no_seaward_names:
        log.warning("nwps: %d spot(s) had NO seaward wet cell within %d cells and sampled "
                    "a landward one instead: %s",
                    len(walk_no_seaward_names), _LAND_SEARCH_MAX_RADIUS,
                    ", ".join(sorted(walk_no_seaward_names)))
    if baked_land_names:
        log.warning("nwps: %d baked node(s) tested as land: %s",
                    len(baked_land_names), ", ".join(sorted(baked_land_names)))
    if far_sampled_names:
        log.warning("nwps: %d spot(s) sampled BEYOND their grid's far cap (published "
                    "anyway, not skipped): %s",
                    len(far_sampled_names), ", ".join(sorted(far_sampled_names)))
    if impossible_pairs:
        log.warning(
            "nwps: %d record(s) across %d spot(s) have swell_hs > hs — physically "
            "impossible, left unmodified: %s",
            impossible_pairs, len(set(impossible_pair_spots)),
            ", ".join(sorted(set(impossible_pair_spots))),
        )
    return out
