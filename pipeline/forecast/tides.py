"""NOAA CO-OPS tide-prediction fetcher — resilient to a dead/slow CO-OPS backend.

For each unique `nearest_tide_station_id` in spots_enriched.json, serve two prediction
series (high/low events `hilo` + hourly water-level curve `h`) covering the next
TIDE_PREDICTION_RANGE_HOURS (7 days). Output is written to pipeline/forecast_data/tides.json
keyed by station_id, and each station entry carries a freshness marker (`asof` + `stale`).

Tides are a rating MODIFIER, not a blocker: no failure or slowness in this stage may stop the
pipeline from reaching db_import. That is enforced by four mechanisms:

  * LONG CACHE (predictions are DETERMINISTIC): fetch a 30-day horizon per station
    (TIDE_CACHE_HORIZON_HOURS) and persist it to pipeline/cache/tides/<station>.json. A station
    is only refetched when < 7 days of its cached horizon remain (TIDE_CACHE_REFETCH_WITHIN_HOURS),
    so a typical run touches NOAA for only a handful of stations (steady state ~10/day of ~230).
  * PER-STATION CAP: a SINGLE attempt with an explicit short socket timeout (NOAA_COOPS_TIMEOUT_S),
    no retry/backoff loop (http.get_once). A connection error / timeout / 5xx ABORTS the station
    immediately (an outage will fail every datum identically — don't burn 3 datums x timeout).
  * CIRCUIT BREAKER: after TIDE_FETCH_MAX_CONSECUTIVE_FAILURES station failures in a row, stop
    contacting NOAA for the rest of the run and mark the remaining stations stale.
  * STAGE DEADLINE: the whole stage is bounded to TIDE_STAGE_DEADLINE_S; on expiry it bails with
    what it has. It also NEVER raises out (the write + return happen in a finally).

The OUTPUT window (and therefore interpret's tide_norm and the ratings) is unchanged: the 30-day
cache is SLICED to the 7-day horizon on the way out, so a good fetch returns exactly what it did
before, plus the freshness marker. When a station is stale/missing, that is recorded honestly
(`stale: true`) so downstream ratings/UI can degrade or annotate rather than presenting old tides
as current.
"""
from __future__ import annotations

import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from ..config import (
    NOAA_COOPS_DATUMS,
    NOAA_COOPS_ENDPOINT,
    NOAA_COOPS_TIMEOUT_S,
    TIDE_CACHE_HORIZON_HOURS,
    TIDE_CACHE_REFETCH_WITHIN_HOURS,
    TIDE_FETCH_MAX_CONSECUTIVE_FAILURES,
    TIDE_PREDICTION_RANGE_HOURS,
    TIDE_STAGE_DEADLINE_S,
    TIDES_CACHE_DIR,
    TIDES_FORECAST_FILE,
)
from ..http import get_once

log = logging.getLogger(__name__)

# Stations already observed to have NO predictions under any datum (a genuine data condition, NOT
# an outage) are persisted here so later runs skip them without hitting the API.
_NO_PREDICTIONS_FILE = TIDES_CACHE_DIR / "_no_predictions.json"

_OUTPUT_DAYS = TIDE_PREDICTION_RANGE_HOURS // 24     # 7-day output horizon (unchanged)


class _TideOutage(Exception):
    """CO-OPS unreachable for a station (connection error / timeout / 5xx). Distinct from a station
    that simply has no predictions — this is what trips the circuit breaker."""


# --------------------------------------------------------------------------- #
# no-predictions markers                                                        #
# --------------------------------------------------------------------------- #
def _load_no_predictions() -> set[str]:
    if not _NO_PREDICTIONS_FILE.exists():
        return set()
    try:
        return set(json.loads(_NO_PREDICTIONS_FILE.read_text()))
    except (json.JSONDecodeError, TypeError, OSError):
        return set()


def _save_no_predictions(known: set[str]) -> None:
    TIDES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _NO_PREDICTIONS_FILE.write_text(json.dumps(sorted(known)))


# --------------------------------------------------------------------------- #
# persistent per-station cache (30-day deterministic predictions)               #
# --------------------------------------------------------------------------- #
def _station_cache_path(station_id: str) -> Path:
    return TIDES_CACHE_DIR / f"{station_id}.json"


def _load_cache(station_id: str) -> dict | None:
    p = _station_cache_path(station_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text())
    except (json.JSONDecodeError, OSError):
        return None


def _save_cache(station_id: str, entry: dict) -> None:
    TIDES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _station_cache_path(station_id).write_text(json.dumps(entry))


def _cache_covers(cache: dict | None, end_date: date) -> bool:
    """True when the cache still covers the full OUTPUT window (covers_until >= end_date) — i.e.
    it has >= TIDE_CACHE_REFETCH_WITHIN_HOURS of horizon left and needs no refetch."""
    if not cache:
        return False
    cu = cache.get("covers_until")
    try:
        return bool(cu) and date.fromisoformat(cu[:10]) >= end_date
    except (ValueError, TypeError):
        return False


# --------------------------------------------------------------------------- #
# output shaping (slice the 30-day cache to the unchanged 7-day window)         #
# --------------------------------------------------------------------------- #
def _slice(rows: list | None, start_date: date, end_date: date) -> list:
    """Keep prediction rows whose date is in [start_date, end_date) — the legacy 7-day horizon, so
    the emitted series (hence interpret's min/max -> tide_norm -> ratings) is identical to the old
    7-day fetch even though the CACHE holds 30 days."""
    out = []
    for r in rows or []:
        t = r.get("t")
        if not t:
            continue
        try:
            d = date.fromisoformat(t[:10])
        except (ValueError, TypeError):
            continue
        if start_date <= d < end_date:
            out.append(r)
    return out


def _emit(station_id: str, entry: dict, start_date: date, end_date: date,
          *, stale: bool, asof: str | None) -> dict:
    """Station output: the 7-day-sliced series + the freshness marker. `fetched_at` is kept for
    back-compat; `asof`/`stale` are the honest freshness signal db_import folds into data_sources."""
    return {
        "station_id": station_id,
        "fetched_at": asof,
        "asof": asof,
        "stale": stale,
        "hilo": _slice(entry.get("hilo"), start_date, end_date),
        "hourly": _slice(entry.get("hourly"), start_date, end_date),
    }


def _stale_entry(station_id: str, cache: dict | None, start_date: date, end_date: date) -> dict:
    """Best-effort stale output: serve the (sliced) cache if we have one — marked stale — else an
    empty, explicitly-stale entry so the spot is annotated rather than silently missing tides."""
    if cache:
        return _emit(station_id, cache, start_date, end_date, stale=True, asof=cache.get("fetched_at"))
    return {"station_id": station_id, "fetched_at": None, "asof": None,
            "stale": True, "hilo": [], "hourly": []}


# --------------------------------------------------------------------------- #
# single-attempt fetch (no retry / no backoff)                                  #
# --------------------------------------------------------------------------- #
def _fetch_interval_once(station_id: str, interval: str, begin_yyyymmdd: str) -> list | None:
    """One interval's predictions over the 30-day horizon, SINGLE attempt per datum. Cascades
    NOAA_COOPS_DATUMS on a DATA-level error (that datum has none for this station) but raises
    _TideOutage on any transport failure / 5xx (backend down — don't waste the other datums).
    Returns the predictions list, or None if every datum returns a data-level error."""
    for datum in NOAA_COOPS_DATUMS:
        params = {
            "station": station_id,
            "product": "predictions",
            "datum": datum,
            "units": "english",
            "time_zone": "lst_ldt",
            "interval": interval,
            "begin_date": begin_yyyymmdd,
            "range": TIDE_CACHE_HORIZON_HOURS,
            "format": "json",
        }
        try:
            resp = get_once(NOAA_COOPS_ENDPOINT, params=params, timeout=NOAA_COOPS_TIMEOUT_S)
        except (requests.ConnectionError, requests.Timeout) as e:
            raise _TideOutage(f"{station_id} {interval}: {type(e).__name__}") from e
        except requests.RequestException as e:  # any other transport failure = outage for our purpose
            raise _TideOutage(f"{station_id} {interval}: {type(e).__name__}") from e
        if resp.status_code == 429 or resp.status_code >= 500:
            raise _TideOutage(f"{station_id} {interval}: HTTP {resp.status_code}")
        try:
            data = resp.json()
        except ValueError:
            continue                          # non-JSON from THIS datum — try the next
        if "error" in data:
            continue                          # this datum has no predictions — try the next
        return data.get("predictions") or []
    return None                               # all datums returned a data-level error


def _fetch_station_30d(station_id: str, begin_yyyymmdd: str) -> tuple[list, list]:
    """Both intervals for a station in one bounded pass. Raises _TideOutage on a transport failure
    (the breaker signal); hilo is tried first, so an outage costs ONE timeout, not two."""
    hilo = _fetch_interval_once(station_id, "hilo", begin_yyyymmdd)
    hourly = _fetch_interval_once(station_id, "h", begin_yyyymmdd)
    return (hilo or []), (hourly or [])


def _unique_station_ids(spots: list[dict]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for s in spots:
        sid = s.get("nearest_tide_station_id")
        if sid and sid not in seen:
            seen.add(sid)
            ordered.append(sid)
    return ordered


# --------------------------------------------------------------------------- #
# stage entry point                                                             #
# --------------------------------------------------------------------------- #
def fetch(spots: list[dict], use_cache: bool = True) -> dict[str, dict]:
    """Fetch/serve tide predictions for every unique station in *spots*. NEVER raises and is bounded
    in wall-clock (cap + breaker + deadline) so a dead CO-OPS backend cannot block the pipeline.
    Returns a dict keyed by station_id (each with `asof`/`stale`) and writes TIDES_FORECAST_FILE."""
    station_ids = _unique_station_ids(spots)
    known_bad = _load_no_predictions() if use_cache else set()
    active_ids = [sid for sid in station_ids if sid not in known_bad]
    skipped_known = len(station_ids) - len(active_ids)

    today = date.today()
    begin_yyyymmdd = today.strftime("%Y%m%d")
    win_start, win_end = today, today + timedelta(days=_OUTPUT_DAYS)   # 7-day OUTPUT slice (unchanged)
    # A cache is "fresh" (needs no refetch) while it still covers >= TIDE_CACHE_REFETCH_WITHIN_HOURS
    # of horizon from today — i.e. covers_until >= this date.
    refetch_until = today + timedelta(hours=TIDE_CACHE_REFETCH_WITHIN_HOURS)
    deadline = time.monotonic() + TIDE_STAGE_DEADLINE_S

    out: dict[str, dict] = {}
    new_bad: list[str] = []
    consecutive_failures = 0
    breaker_open = False
    n_live = n_cache = n_stale = 0

    try:
        from tqdm import tqdm
        iterator = tqdm(active_ids, desc="tides", unit="station")
    except ImportError:
        iterator = active_ids

    log.info("tides: %d unique stations (%d active, %d known-no-predictions)",
             len(station_ids), len(active_ids), skipped_known)

    try:
        for idx, sid in enumerate(active_ids):
            cache = _load_cache(sid) if use_cache else None

            # (E) stage deadline — bail with what we have; mark every remaining station stale.
            if time.monotonic() > deadline:
                log.error("tides: stage deadline (%.0fs) reached after %d stations — marking the "
                          "remaining %d stale and continuing the pipeline",
                          TIDE_STAGE_DEADLINE_S, idx, len(active_ids) - idx)
                for rsid in active_ids[idx:]:
                    rcache = _load_cache(rsid) if use_cache else None
                    out[rsid] = _stale_entry(rsid, rcache, win_start, win_end)
                    n_stale += 1
                break

            # (B) fresh long-cache — no network at all (deterministic predictions still valid).
            if _cache_covers(cache, refetch_until):
                out[sid] = _emit(sid, cache, win_start, win_end, stale=False, asof=cache.get("fetched_at"))
                n_cache += 1
                continue

            # (D) breaker open — don't touch NOAA; serve stale-or-empty.
            if breaker_open:
                out[sid] = _stale_entry(sid, cache, win_start, win_end)
                n_stale += 1
                continue

            # need a live refetch (no cache / cache expiring within the window)
            try:
                hilo, hourly = _fetch_station_30d(sid, begin_yyyymmdd)     # (C) single attempt, short timeout
            except _TideOutage as e:
                consecutive_failures += 1
                log.debug("tides: %s unreachable (%s) [%d in a row]", sid, e, consecutive_failures)
                out[sid] = _stale_entry(sid, cache, win_start, win_end)
                n_stale += 1
                if consecutive_failures >= TIDE_FETCH_MAX_CONSECUTIVE_FAILURES and not breaker_open:
                    breaker_open = True
                    log.error("tides: NOAA CO-OPS unreachable — %d consecutive station failures; "
                              "CIRCUIT BREAKER OPEN. No more requests this run; all remaining stations "
                              "marked stale. The pipeline continues to db_import with the tides it has.",
                              consecutive_failures)
                continue

            consecutive_failures = 0
            if not hilo and not hourly:
                # genuine no-predictions (a DATA error on every datum) — permanent skip, NOT stale.
                new_bad.append(sid)
                log.info("tides: %s has no predictions in any datum — marking known-bad", sid)
                continue

            now_iso = datetime.now(tz=timezone.utc).isoformat()
            entry = {
                "station_id": sid,
                "fetched_at": now_iso,
                "covers_from": today.isoformat(),
                "covers_until": (today + timedelta(hours=TIDE_CACHE_HORIZON_HOURS)).isoformat(),
                "hilo": hilo,
                "hourly": hourly,
            }
            _save_cache(sid, entry)
            out[sid] = _emit(sid, entry, win_start, win_end, stale=False, asof=now_iso)
            n_live += 1
    finally:
        # STAGE ISOLATION (A): always persist what we have + the no-pred markers, no matter how we
        # exit (success, break, or an unexpected error) — so db_import always has a tides.json.
        if new_bad:
            _save_no_predictions(known_bad | set(new_bad))
        TIDES_FORECAST_FILE.parent.mkdir(parents=True, exist_ok=True)
        TIDES_FORECAST_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    log.info("tides: wrote %d stations to %s (live=%d, cached=%d, stale/missing=%d, known-bad-new=%d, "
             "breaker=%s)", len(out), TIDES_FORECAST_FILE, n_live, n_cache, n_stale, len(new_bad),
             "OPEN" if breaker_open else "closed")
    return out
