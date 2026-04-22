"""NWPS (Nearshore Wave Prediction System) forecast fetcher.

NOAA's operational SWAN wave model produces forecaster-triggered runs
at each coastal Weather Forecast Office (WFO). For every spot we:

1. Resolve the WFO by state + lat/lng and persist ``nwps_wfo`` to
   spots_enriched.json.
2. Group spots by WFO, download the latest CG1 GRIB2 (trying the most
   recent cycles in reverse until one exists on NOMADS), cache it.
3. Open the GRIB2 with xarray + cfgrib, merge all param groups, and
   extract the nearest grid point's full time series per spot.
4. Write pipeline/forecast_data/nwps.json keyed by spot name.

GRIB2 files are ~100–300 MB per WFO. Use ``--wfo`` to limit scope when
iterating. Requires the eccodes system lib (``apt install libeccodes0``
on Debian/Ubuntu; ``brew install eccodes`` on macOS).
"""
from __future__ import annotations

import json
import logging
import math
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import (
    NWPS_CACHE_DIR,
    NWPS_CYCLE_LOOKBACK,
    NWPS_FORECAST_FILE,
    NWPS_NOMADS_BASE,
)
from ..http import get, request, session

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

def candidate_cycles(n: int = NWPS_CYCLE_LOOKBACK) -> list[tuple[str, str]]:
    """Return up to *n* (YYYYMMDD, HH) tuples, newest-first.

    Per user spec: today's 12Z → 06Z → 00Z → yesterday's 18Z.
    Beyond that falls back through yesterday's 12/06/00Z.
    """
    now = datetime.now(tz=timezone.utc)
    today = now.date()
    yesterday = today - timedelta(days=1)
    cycles: list[tuple[str, str]] = []
    # Today: 12Z, 06Z, 00Z (skip today's 18Z — usually not yet posted)
    for hh in ("12", "06", "00"):
        cycles.append((today.strftime("%Y%m%d"), hh))
    # Fallback to yesterday in reverse
    for hh in ("18", "12", "06", "00"):
        cycles.append((yesterday.strftime("%Y%m%d"), hh))
    return cycles[:n]


def _grib_url(wfo: str, date_ymd: str, hh: str) -> str:
    return (
        f"{NWPS_NOMADS_BASE}/nwps.{date_ymd}/{wfo}/{hh}/CG1/"
        f"{wfo}_nwps_CG1_{date_ymd}_{hh}00.grib2"
    )


def _grib_path(wfo: str, date_ymd: str, hh: str) -> Path:
    return NWPS_CACHE_DIR / f"{wfo}_{date_ymd}_{hh}.grib2"


def _head_exists(url: str) -> bool:
    """HEAD the URL; return True on 200, False on 404 / other errors."""
    try:
        resp = request("HEAD", url, timeout=30)
    except Exception as e:  # noqa: BLE001
        log.debug("nwps: HEAD %s failed: %s", url, e)
        return False
    return 200 <= resp.status_code < 300


def _download(url: str, dest: Path) -> bool:
    """Stream-download to *dest*. Returns True on success."""
    try:
        # Use the shared session but stream to avoid loading 100-300MB into memory.
        s = session()
        with s.get(url, stream=True, timeout=300) as resp:
            if resp.status_code != 200:
                log.warning("nwps: GET %s returned %d", url, resp.status_code)
                return False
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp = dest.with_suffix(dest.suffix + ".partial")
            with tmp.open("wb") as f:
                for chunk in resp.iter_content(chunk_size=1 << 20):
                    if chunk:
                        f.write(chunk)
            tmp.replace(dest)
        return True
    except Exception as e:  # noqa: BLE001
        log.warning("nwps: download %s failed: %s", url, e)
        return False


def _locate_cycle(wfo: str, use_cache: bool) -> tuple[Path, str, str] | None:
    """Find a usable GRIB2 file for this WFO — cached or via cycle fallback.

    Returns (local_path, date_ymd, hh) or None if no cycle is available.
    """
    # Prefer any cached file for today first; otherwise HEAD candidate cycles.
    for date_ymd, hh in candidate_cycles():
        path = _grib_path(wfo, date_ymd, hh)
        if path.exists() and path.stat().st_size > 0:
            if use_cache:
                log.info("nwps: %s/%s %sZ — cache hit (%s)", wfo, date_ymd, hh, path.name)
                return path, date_ymd, hh

    # Download the newest cycle that exists.
    for date_ymd, hh in candidate_cycles():
        url = _grib_url(wfo, date_ymd, hh)
        if not _head_exists(url):
            log.debug("nwps: %s not posted", url)
            continue
        path = _grib_path(wfo, date_ymd, hh)
        log.info("nwps: %s/%s %sZ — downloading from NOMADS", wfo, date_ymd, hh)
        if _download(url, path):
            return path, date_ymd, hh
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
    "swell": "swell_hs",
    "swh_swell": "swell_hs",
    "swper": "swell_tp",
    "swdir": "swell_dp",
    "si10":  "wind_speed",
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


def _extract_time_series(merged, lat: float, lng: float) -> list[dict]:
    """Nearest-grid-point time series for a single spot."""
    import numpy as np
    import pandas as pd

    lng_adj = _normalize_longitude(merged, lng)
    try:
        point = merged.sel(latitude=lat, longitude=lng_adj, method="nearest")
    except Exception as e:  # noqa: BLE001
        log.warning("nwps: .sel failed for (%.4f, %.4f): %s", lat, lng, e)
        return []

    # Forecast time axis — try valid_time, else time, else time + step
    if "valid_time" in point.coords:
        times = pd.to_datetime(point["valid_time"].values, utc=True)
    elif "step" in point.coords and "time" in point.coords:
        base = pd.to_datetime(np.array([point["time"].values]), utc=True)[0]
        times = base + pd.to_timedelta(point["step"].values)
    elif "time" in point.coords:
        times = pd.to_datetime(point["time"].values, utc=True)
    else:
        log.warning("nwps: no time coordinate found in merged dataset")
        return []

    # Per-timestep record.
    results: list[dict] = []
    # Build per-variable arrays in advance.
    arrays: dict[str, list] = {}
    for var_name in point.data_vars:
        out_key = _VAR_MAP.get(str(var_name).lower())
        if out_key is None:
            continue
        vals = np.asarray(point[var_name].values)
        arrays.setdefault(out_key, []).append(vals)

    for i, t in enumerate(times):
        entry: dict = {"valid_time": t.isoformat().replace("+00:00", "Z")}
        for out_key, sources in arrays.items():
            for vals in sources:
                try:
                    val = float(vals[i])
                except (IndexError, TypeError, ValueError):
                    continue
                if math.isnan(val):
                    continue
                # First non-NaN wins (respecting _VAR_MAP priority order).
                entry.setdefault(out_key, round(val, 3))
                break
        results.append(entry)
    return results


def _merge_datasets(datasets: list):
    """Merge cfgrib param groups into a single Dataset, dropping conflicts."""
    import xarray as xr
    if not datasets:
        return None
    # cfgrib separates surface/ocean-layer variables into different groups;
    # they share latitude/longitude/step but may have distinct `level` coords.
    # Drop level coords to allow safe xr.merge.
    cleaned = []
    for ds in datasets:
        drop = [c for c in ("level", "heightAboveGround", "heightAboveSea", "surface") if c in ds.coords]
        cleaned.append(ds.drop_vars(drop, errors="ignore"))
    try:
        return xr.merge(cleaned, compat="override", join="override")
    except Exception as e:  # noqa: BLE001
        log.warning("nwps: xr.merge failed (%s); falling back to first dataset only", e)
        return cleaned[0]


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
            merged = _merge_datasets(datasets)
        except Exception as e:  # noqa: BLE001
            wfos_parse_failed += 1
            log.exception("nwps: GRIB parse failed for %s (%s): %s", wfo, grib_path, e)
            continue
        if merged is None:
            wfos_parse_failed += 1
            log.warning("nwps: merged dataset empty for %s", wfo)
            continue

        wfos_ok += 1
        wfo_spots = [s for s in spots if s.get("nwps_wfo") == wfo]
        log.info("nwps: %s (%sZ %s) — extracting %d spots from %s",
                 wfo, cycle_hh, cycle_date, len(wfo_spots), grib_path.name)
        for spot in wfo_spots:
            series = _extract_time_series(merged, float(spot["lat"]), float(spot["lng"]))
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
