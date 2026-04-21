"""NOAA CO-OPS tide-prediction fetcher.

For each unique `nearest_tide_station_id` in spots_enriched.json, fetch two
predictions series covering the next 7 days:

- high/low events (interval=hilo)
- hourly water-level curve (interval=h)

Each raw response is cached to pipeline/cache/tides/<station>_<YYYYMMDD>_<interval>.json
so same-day re-runs are free. Output is written to
pipeline/forecast_data/tides.json keyed by station_id.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timezone
from pathlib import Path

from ..config import (
    NOAA_COOPS_ENDPOINT,
    NOAA_COOPS_MIN_INTERVAL_S,
    TIDES_CACHE_DIR,
    TIDES_FORECAST_FILE,
    TIDE_PREDICTION_RANGE_HOURS,
)
from ..http import get

log = logging.getLogger(__name__)


class _Pacer:
    """Polite single-threaded rate limiter for the public CO-OPS API."""

    def __init__(self, min_interval_s: float) -> None:
        self.min_interval_s = min_interval_s
        self._last = 0.0

    def wait(self) -> None:
        delta = time.monotonic() - self._last
        if delta < self.min_interval_s:
            time.sleep(self.min_interval_s - delta)
        self._last = time.monotonic()


def _cache_path(station_id: str, today_yyyymmdd: str, interval: str) -> Path:
    return TIDES_CACHE_DIR / f"{station_id}_{today_yyyymmdd}_{interval}.json"


def _fetch_interval(station_id: str, interval: str, pacer: _Pacer, use_cache: bool) -> dict | None:
    """Fetch one predictions interval (hilo or h); return the parsed JSON body."""
    today = date.today().strftime("%Y%m%d")
    cache_file = _cache_path(station_id, today, interval)
    if use_cache and cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            log.warning("tides cache %s corrupt; refetching", cache_file)

    pacer.wait()
    params = {
        "station": station_id,
        "product": "predictions",
        "datum": "MLLW",
        "units": "english",
        "time_zone": "lst_ldt",
        "interval": interval,
        "begin_date": today,
        "range": TIDE_PREDICTION_RANGE_HOURS,
        "format": "json",
    }
    try:
        resp = get(NOAA_COOPS_ENDPOINT, params=params)
    except Exception as e:  # noqa: BLE001
        log.warning("tides: station %s interval %s HTTP failed: %s", station_id, interval, e)
        return None

    try:
        data = resp.json()
    except ValueError as e:
        log.warning("tides: station %s interval %s non-JSON response: %s", station_id, interval, e)
        return None

    # CO-OPS returns 200 with {"error": {"message": "..."}} on bad stations / dates.
    if "error" in data:
        log.warning("tides: station %s interval %s returned error: %s",
                    station_id, interval, data["error"].get("message"))
        return None

    TIDES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cache_file.write_text(json.dumps(data))
    return data


def _unique_station_ids(spots: list[dict]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for s in spots:
        sid = s.get("nearest_tide_station_id")
        if sid and sid not in seen:
            seen.add(sid)
            ordered.append(sid)
    return ordered


def fetch(spots: list[dict], use_cache: bool = True) -> dict[str, dict]:
    """Fetch tide predictions for every unique station in *spots*.

    Returns a dict keyed by station_id. Also writes TIDES_FORECAST_FILE.
    """
    station_ids = _unique_station_ids(spots)
    log.info("tides: %d unique stations to fetch", len(station_ids))

    pacer = _Pacer(NOAA_COOPS_MIN_INTERVAL_S)
    out: dict[str, dict] = {}
    successes = 0
    failures = 0

    try:
        from tqdm import tqdm
        iterator = tqdm(station_ids, desc="tides", unit="station")
    except ImportError:
        iterator = station_ids

    for sid in iterator:
        hilo = _fetch_interval(sid, "hilo", pacer, use_cache)
        hourly = _fetch_interval(sid, "h", pacer, use_cache)

        hilo_predictions = (hilo or {}).get("predictions") or []
        hourly_predictions = (hourly or {}).get("predictions") or []

        if not hilo_predictions and not hourly_predictions:
            failures += 1
            log.warning("tides: station %s returned no usable data", sid)
            continue
        successes += 1

        out[sid] = {
            "station_id": sid,
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
            "hilo": hilo_predictions,
            "hourly": hourly_predictions,
        }

    TIDES_FORECAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    TIDES_FORECAST_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    log.info(
        "tides: wrote %d stations to %s (%d successes, %d failures)",
        len(out), TIDES_FORECAST_FILE, successes, failures,
    )
    return out
