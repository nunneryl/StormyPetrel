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
    NOAA_COOPS_DATUMS,
    NOAA_COOPS_ENDPOINT,
    NOAA_COOPS_MIN_INTERVAL_S,
    TIDES_CACHE_DIR,
    TIDES_FORECAST_FILE,
    TIDE_PREDICTION_RANGE_HOURS,
)
from ..http import get

log = logging.getLogger(__name__)

# Stations we've already observed to have no predictions under any datum are
# persisted here so subsequent runs skip them without hitting the API.
_NO_PREDICTIONS_FILE = TIDES_CACHE_DIR / "_no_predictions.json"


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


def _load_no_predictions() -> set[str]:
    if not _NO_PREDICTIONS_FILE.exists():
        return set()
    try:
        return set(json.loads(_NO_PREDICTIONS_FILE.read_text()))
    except (json.JSONDecodeError, TypeError):
        return set()


def _save_no_predictions(known: set[str]) -> None:
    TIDES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _NO_PREDICTIONS_FILE.write_text(json.dumps(sorted(known)))


def _fetch_interval(station_id: str, interval: str, pacer: _Pacer, use_cache: bool) -> dict | None:
    """Fetch predictions for one interval, cascading through NOAA_COOPS_DATUMS.

    Returns the parsed JSON body of the first datum that yields predictions,
    or None if every datum returns an error / non-JSON / HTTP failure.
    """
    today = date.today().strftime("%Y%m%d")
    cache_file = _cache_path(station_id, today, interval)
    if use_cache and cache_file.exists():
        try:
            return json.loads(cache_file.read_text())
        except json.JSONDecodeError:
            log.warning("tides cache %s corrupt; refetching", cache_file)

    for datum in NOAA_COOPS_DATUMS:
        pacer.wait()
        params = {
            "station": station_id,
            "product": "predictions",
            "datum": datum,
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
            log.debug("tides: %s %s datum=%s HTTP failed: %s", station_id, interval, datum, e)
            continue

        try:
            data = resp.json()
        except ValueError as e:
            log.debug("tides: %s %s datum=%s non-JSON response: %s", station_id, interval, datum, e)
            continue

        # CO-OPS returns 200 with {"error": {"message": "..."}} when predictions are
        # unavailable for the given (station, datum, date range).
        if "error" in data:
            log.debug("tides: %s %s datum=%s error: %s",
                      station_id, interval, datum, data["error"].get("message"))
            continue

        # Success — cache and return. First successful datum wins.
        TIDES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data))
        return data

    return None


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
    known_bad = _load_no_predictions() if use_cache else set()
    active_ids = [sid for sid in station_ids if sid not in known_bad]
    skipped_known = len(station_ids) - len(active_ids)
    log.info(
        "tides: %d unique stations (%d active, %d skipped as known-no-predictions)",
        len(station_ids), len(active_ids), skipped_known,
    )

    pacer = _Pacer(NOAA_COOPS_MIN_INTERVAL_S)
    out: dict[str, dict] = {}
    successes = 0
    failures = 0
    new_bad: list[str] = []

    try:
        from tqdm import tqdm
        iterator = tqdm(active_ids, desc="tides", unit="station")
    except ImportError:
        iterator = active_ids

    for sid in iterator:
        hilo = _fetch_interval(sid, "hilo", pacer, use_cache)
        hourly = _fetch_interval(sid, "h", pacer, use_cache)

        hilo_predictions = (hilo or {}).get("predictions") or []
        hourly_predictions = (hourly or {}).get("predictions") or []

        if not hilo_predictions and not hourly_predictions:
            failures += 1
            new_bad.append(sid)
            log.info("tides: %s has no predictions in any datum — marking as bad", sid)
            continue
        successes += 1

        out[sid] = {
            "station_id": sid,
            "fetched_at": datetime.now(tz=timezone.utc).isoformat(),
            "hilo": hilo_predictions,
            "hourly": hourly_predictions,
        }

    # Persist the no-predictions markers so subsequent runs don't re-probe them.
    if new_bad:
        _save_no_predictions(known_bad | set(new_bad))

    TIDES_FORECAST_FILE.parent.mkdir(parents=True, exist_ok=True)
    TIDES_FORECAST_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False))
    log.info(
        "tides: wrote %d stations to %s (successes=%d, failures=%d, skipped_known=%d)",
        len(out), TIDES_FORECAST_FILE, successes, failures, skipped_known,
    )
    return out
