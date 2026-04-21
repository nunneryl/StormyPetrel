"""NDBC realtime observation fetcher.

For each unique nearest_buoy_id in spots_enriched.json, fetch:

- {STATION}.txt  — standard meteorological (wind, pressure, temps, and if
                   the buoy reports waves: WVHT, DPD, APD, MWD)
- {STATION}.spec — spectral wave summary (SwH, SwP, WWH, WWP, MWD, …)
                   (skipped gracefully if the buoy doesn't publish it)

Parses the space-delimited fixed-format text, extracts the latest observation
and the last 24 hours of history, and writes pipeline/forecast_data/buoys.json.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ..config import (
    BUOYS_CACHE_DIR,
    BUOYS_FORECAST_FILE,
    NDBC_REALTIME2_BASE,
)
from ..http import get

log = logging.getLogger(__name__)

# Mapping from NDBC column names to normalized field names + type converters.
# NDBC always emits metric columns for wind/wave/temperature in realtime2.
_STD_FIELDS = {
    "WDIR": ("wind_dir_deg", float),
    "WSPD": ("wind_speed_ms", float),
    "GST":  ("wind_gust_ms", float),
    "WVHT": ("wave_height_m", float),
    "DPD":  ("dominant_period_s", float),
    "APD":  ("avg_period_s", float),
    "MWD":  ("mean_wave_dir_deg", float),
    "PRES": ("pressure_hpa", float),
    "ATMP": ("air_temp_c", float),
    "WTMP": ("water_temp_c", float),
    "DEWP": ("dew_point_c", float),
    "VIS":  ("visibility_nmi", float),
    "PTDY": ("pressure_tendency_hpa", float),
    "TIDE": ("tide_ft", float),
}

_SPEC_FIELDS = {
    "WVHT":      ("wave_height_m", float),
    "SwH":       ("swell_height_m", float),
    "SwP":       ("swell_period_s", float),
    "WWH":       ("wind_wave_height_m", float),
    "WWP":       ("wind_wave_period_s", float),
    "SwD":       ("swell_dir", str),
    "WWD":       ("wind_wave_dir", str),
    "STEEPNESS": ("steepness", str),
    "APD":       ("avg_period_s", float),
    "MWD":       ("mean_wave_dir_deg", float),
}


def _parse_realtime2(text: str, field_map: dict) -> list[dict]:
    """Parse the NDBC realtime2 text format into a list of observations (most recent first)."""
    lines = text.strip().split("\n")
    if len(lines) < 3:
        return []
    # Line 0: column names with leading '#'. Line 1: units.
    headers = lines[0].lstrip("#").split()
    observations: list[dict] = []
    for line in lines[2:]:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        values = line.split()
        if len(values) != len(headers):
            continue
        row = dict(zip(headers, values))
        # Timestamp (NDBC realtime2 is UTC).
        try:
            ts = datetime(
                int(row["#YY"] if "#YY" in row else row["YY"]),
                int(row["MM"]),
                int(row["DD"]),
                int(row["hh"]),
                int(row["mm"]),
                tzinfo=timezone.utc,
            )
        except (KeyError, ValueError):
            continue
        obs: dict = {"time": ts.isoformat()}
        for src, (dst, conv) in field_map.items():
            v = row.get(src)
            if v is None or v == "MM":
                obs[dst] = None
                continue
            try:
                obs[dst] = conv(v)
            except (TypeError, ValueError):
                obs[dst] = None
        observations.append(obs)
    return observations


def _fetch_text(url: str, buoy_id: str, label: str, use_cache: bool) -> str | None:
    """Fetch a realtime2 text file; cache to pipeline/cache/buoys/<buoy>.<label>.txt."""
    cache_file = BUOYS_CACHE_DIR / f"{buoy_id}.{label}.txt"
    if use_cache and cache_file.exists():
        return cache_file.read_text()

    try:
        resp = get(url)
    except Exception as e:  # noqa: BLE001
        log.debug("buoys: %s %s fetch failed: %s", buoy_id, label, e)
        return None

    if not resp.text or resp.text.strip().startswith("<"):
        # 404s often come back as HTML error pages.
        return None

    BUOYS_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(resp.text)
    return resp.text


def _filter_last_24h(observations: list[dict]) -> list[dict]:
    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=24)
    kept: list[dict] = []
    for obs in observations:
        try:
            t = datetime.fromisoformat(obs["time"])
        except (KeyError, ValueError):
            continue
        if t >= cutoff:
            kept.append(obs)
    return kept


def _unique_buoy_ids(spots: list[dict]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for s in spots:
        bid = s.get("nearest_buoy_id")
        if bid and bid not in seen:
            seen.add(bid)
            ordered.append(bid)
    return ordered


def fetch(spots: list[dict], use_cache: bool = True) -> dict[str, dict]:
    """Fetch latest buoy observations for every unique buoy in *spots*.

    Returns a dict keyed by buoy_id. Also writes BUOYS_FORECAST_FILE.
    """
    buoy_ids = _unique_buoy_ids(spots)
    log.info("buoys: %d unique buoys to fetch", len(buoy_ids))

    out: dict[str, dict] = {}
    successes = 0
    failures = 0
    no_spec = 0

    try:
        from tqdm import tqdm
        iterator = tqdm(buoy_ids, desc="buoys", unit="buoy")
    except ImportError:
        iterator = buoy_ids

    for bid in iterator:
        upper = bid.upper()

        std_text = _fetch_text(
            f"{NDBC_REALTIME2_BASE}/{upper}.txt", bid, "std", use_cache,
        )
        spec_text = _fetch_text(
            f"{NDBC_REALTIME2_BASE}/{upper}.spec", bid, "spec", use_cache,
        )

        std_obs = _parse_realtime2(std_text, _STD_FIELDS) if std_text else []
        spec_obs = _parse_realtime2(spec_text, _SPEC_FIELDS) if spec_text else []

        if not std_obs:
            failures += 1
            log.warning("buoys: %s no standard observations available", bid)
            continue
        successes += 1
        if not spec_obs:
            no_spec += 1

        latest = dict(std_obs[0])
        if spec_obs:
            for k, v in spec_obs[0].items():
                if k == "time":
                    continue
                # Spec supplements std; don't overwrite a non-null std value.
                if latest.get(k) is None:
                    latest[k] = v

        out[bid] = {
            "buoy_id": bid,
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
            "latest": latest,
            "history_24h": _filter_last_24h(std_obs),
            "spec_history_24h": _filter_last_24h(spec_obs),
        }

    BUOYS_FORECAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    BUOYS_FORECAST_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    log.info(
        "buoys: wrote %d buoys to %s (%d successes, %d failures, %d without spec)",
        len(out), BUOYS_FORECAST_FILE, successes, failures, no_spec,
    )
    return out
