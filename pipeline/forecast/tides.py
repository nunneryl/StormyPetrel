"""NOAA CO-OPS tide-prediction fetcher — resilient to a dead/slow CO-OPS backend.

For each unique `nearest_tide_station_id` in spots_enriched.json, serve two prediction
series (high/low events `hilo` + hourly water-level curve `h`) covering the next
TIDE_PREDICTION_RANGE_HOURS (7 days). Output is written to pipeline/forecast_data/tides.json
keyed by station_id, and each station entry carries a freshness marker (`asof` + `stale`).

Tides are a rating MODIFIER, not a blocker: no failure or slowness in this stage may stop the
pipeline from reaching db_import. That is enforced by four mechanisms:

  * LONG CACHE (predictions are DETERMINISTIC): fetch a ~25-30 day horizon per station (a deterministic
    per-station jitter of TIDE_CACHE_HORIZON_HOURS — see _station_horizon_hours) and persist it to
    pipeline/cache/tides/<station>.json. A station is only refetched when < 7 days of its cached horizon
    remain (TIDE_CACHE_REFETCH_WITHIN_HOURS), so a typical run touches NOAA for only a handful of
    stations. The jitter staggers those refetches across a multi-day window so a cold-started fleet
    doesn't all lapse on the same day (steady state a few dozen/day of ~230, not all at once on ~day 23).
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

import hashlib
import json
import logging
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import requests

from ..config import (
    NOAA_COOPS_DATUMS,
    NOAA_COOPS_ENDPOINT,
    NOAA_COOPS_MIN_INTERVAL_S,
    NOAA_COOPS_TIMEOUT_S,
    TIDE_CACHE_HORIZON_HOURS,
    TIDE_CACHE_HORIZON_MIN_HOURS,
    TIDE_CACHE_REFETCH_WITHIN_HOURS,
    TIDE_FETCH_MAX_CONSECUTIVE_FAILURES,
    TIDE_KNOWN_BAD_TTL_DAYS,
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
# request pacing (restore the incidental pacing the outage-proofing rewrite dropped)#
# --------------------------------------------------------------------------- #
class _Pacer:
    """Minimum-interval throttle for the public CO-OPS API. The pre-rewrite fetcher paced every request
    at NOAA_COOPS_MIN_INTERVAL_S via this same pattern; the single-attempt rewrite dropped it, so ~460
    back-to-back requests tripped CO-OPS rate-limiting mid-run. Restored here — one pacer per fetch()
    run, shared across all stations/datums/intervals."""
    def __init__(self, min_interval_s: float) -> None:
        self.min_interval_s = min_interval_s
        self._last = 0.0

    def wait(self) -> None:
        if self.min_interval_s <= 0:
            return
        delta = time.monotonic() - self._last
        if delta < self.min_interval_s:
            time.sleep(self.min_interval_s - delta)
        self._last = time.monotonic()


# --------------------------------------------------------------------------- #
# no-predictions markers (station CONFIRMED to have no predictions under any datum) #
# --------------------------------------------------------------------------- #
# Bump when the classifier's MEANING or FORMAT changes so older files are discarded, not trusted. v3
# is the genuine-'No Predictions data was found'-only classifier with a per-entry FIRST-SEEN timestamp
# for TTL re-verification; a v2/v1/list-format file predates the timestamp (and may have been poisoned
# by throttle responses mislabelled as no-data), so it is thrown away on load.
_NO_PREDICTIONS_VERSION = 3


def _load_no_predictions_map() -> dict[str, str]:
    """{station_id: first_seen_iso} for stations CONFIRMED to have no predictions, with entries past the
    TIDE_KNOWN_BAD_TTL_DAYS TTL DROPPED — so a station that comes back online recovers on its own (it is
    re-verified after the TTL) without anyone running --clear-known-bad. A permanent verdict resting
    only on our classification being correct is the assumption that failed once; the TTL bounds it. An
    old/unversioned/pre-TTL file (no per-entry timestamp, possibly throttle-poisoned) is discarded."""
    if not _NO_PREDICTIONS_FILE.exists():
        return {}
    try:
        blob = json.loads(_NO_PREDICTIONS_FILE.read_text())
    except (json.JSONDecodeError, TypeError, OSError):
        return {}
    if not (isinstance(blob, dict) and blob.get("version") == _NO_PREDICTIONS_VERSION):
        log.warning("tides: discarding pre-TTL/unversioned known-bad list (%s) — its stations will be "
                    "re-verified this run (a single bad run must not permanently poison the roster)",
                    _NO_PREDICTIONS_FILE)
        return {}
    stations = blob.get("stations")
    if not isinstance(stations, dict):
        return {}
    cutoff = datetime.now(tz=timezone.utc) - timedelta(days=TIDE_KNOWN_BAD_TTL_DAYS)
    live: dict[str, str] = {}
    expired = 0
    for sid, seen in stations.items():
        try:
            ts = datetime.fromisoformat(str(seen))
        except (TypeError, ValueError):
            expired += 1
            continue                          # garbled/absent timestamp → re-verify
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        if ts >= cutoff:
            live[sid] = seen
        else:
            expired += 1
    if expired:
        log.info("tides: %d known-bad station(s) past the %d-day TTL — re-verifying this run",
                 expired, TIDE_KNOWN_BAD_TTL_DAYS)
    return live


def _load_no_predictions() -> set[str]:
    """The set of stations to SKIP this run (non-expired known-bad). See _load_no_predictions_map."""
    return set(_load_no_predictions_map())


def _save_no_predictions(stations_map: dict[str, str]) -> None:
    """Persist {station_id: first_seen_iso}. The caller passes a MERGED map that PRESERVES each still-
    valid entry's original first_seen, so a station's TTL is measured from FIRST confirmation and is not
    refreshed every run (which would make a persistently-bad station never re-verify)."""
    TIDES_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    _NO_PREDICTIONS_FILE.write_text(
        json.dumps({"version": _NO_PREDICTIONS_VERSION, "stations": dict(stations_map)}))


def clear_known_bad() -> int:
    """Invalidate the persisted known-bad list. TTL re-verification recovers a recovered station on its
    own; this is the manual override — counts RAW entries (any format) then removes the file. Returns
    the number cleared; safe when the file is absent."""
    n = 0
    if _NO_PREDICTIONS_FILE.exists():
        try:
            blob = json.loads(_NO_PREDICTIONS_FILE.read_text())
            n = len(blob.get("stations", []) if isinstance(blob, dict) else blob)
        except (json.JSONDecodeError, TypeError, OSError):
            n = 0
        _NO_PREDICTIONS_FILE.unlink(missing_ok=True)
    return n


def _coops_error_message(data: dict) -> str:
    """The message string from a CO-OPS ``{"error": {...}}`` body (or a bare error), else ''."""
    err = data.get("error")
    if isinstance(err, dict):
        return str(err.get("message") or err)
    return str(err or "")


def _is_genuine_no_predictions(data: dict) -> bool:
    """True ONLY for CO-OPS's genuine 'No Predictions data was found ...' answer. A throttle /
    rate-limit / any other error message returns False, so it can never mark a station known-bad —
    a transient failure must never be recorded as the permanent fact 'this station has no tides'."""
    return "no predictions" in _coops_error_message(data).lower()


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


def _station_horizon_hours(station_id: str) -> int:
    """Deterministic per-station cache horizon in [TIDE_CACHE_HORIZON_MIN_HOURS, TIDE_CACHE_HORIZON_HOURS].

    Every station cold-starts on the same run with the same horizon, so without jitter they'd all lapse
    on the SAME day (~day 23) and refetch in one thundering-herd run — ~230 stations at once instead of
    a handful. A stable hash of the station id shaves 0..(MAX-MIN) hours off the max horizon, spreading
    each station's covers_until — hence its refetch day — across a ~5-6 day window. Deterministic by
    construction (hashlib, NOT the salted builtin hash() which varies per process) so every run agrees
    on a station's horizon and the persisted cache stays coherent. Predictions are deterministic and the
    OUTPUT is sliced to the 7-day window regardless, so a shorter horizon changes nothing the pipeline
    or ratings see — it only moves WHEN a station's cache lapses."""
    span = TIDE_CACHE_HORIZON_HOURS - TIDE_CACHE_HORIZON_MIN_HOURS
    if span <= 0:
        return TIDE_CACHE_HORIZON_HOURS
    jitter = int.from_bytes(hashlib.sha1(station_id.encode("utf-8")).digest()[:4], "big") % (span + 1)
    return TIDE_CACHE_HORIZON_HOURS - jitter


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
# data-derived coverage (covers_until MUST come from the data, not the request) #
# --------------------------------------------------------------------------- #
def _last_dt(rows: list | None) -> datetime | None:
    """Latest timestamp actually present in a prediction series (rows of {'t': 'YYYY-MM-DD HH:MM'}).
    None if the series is empty or carries no parseable timestamp — an incomplete series that must not
    be treated as covering anything."""
    latest: datetime | None = None
    for r in rows or []:
        t = r.get("t")
        if not t:
            continue
        try:
            d = datetime.strptime(t, "%Y-%m-%d %H:%M")
        except (ValueError, TypeError):
            try:
                d = datetime.strptime(str(t)[:10], "%Y-%m-%d")   # tolerate a date-only / odd suffix
            except (ValueError, TypeError):
                continue
        if latest is None or d > latest:
            latest = d
    return latest


def _coverage_from_series(hilo: list, hourly: list, today: date,
                          horizon_hours: int) -> tuple[str, float, str] | None:
    """Derive covers_until from the DATA actually returned, never from the requested horizon — so a
    station that returns fewer days than asked for can't masquerade as fully covered (which would make
    _cache_covers skip the refetch and let the 7-day output slice silently truncate, scoring the
    uncovered hours as if the tide were perfect).

    Returns (covers_until, shortfall_hours, governing_series), or None when EITHER series has no
    parseable timestamp — an incomplete pair the caller must NOT cache as covering anything, because
    the output window needs BOTH series. covers_until is the DATE of the EARLIER of the two series'
    last timestamps (coverage is governed by whichever series ends first), CLAMPED so it can never
    exceed the requested horizon (today + horizon_hours). It is a 'YYYY-MM-DD' string so its first 10
    chars stay compatible with _cache_covers' date.fromisoformat(cu[:10]). shortfall_hours is how far
    that governing timestamp falls short of the requested horizon; governing_series is 'hilo'/'hourly'."""
    hilo_last = _last_dt(hilo)
    hourly_last = _last_dt(hourly)
    if hilo_last is None or hourly_last is None:
        return None
    governing_dt = min(hilo_last, hourly_last)
    governing = "hilo" if hilo_last <= hourly_last else "hourly"
    cap_dt = datetime.combine(today, datetime.min.time()) + timedelta(hours=horizon_hours)
    covers_dt = min(governing_dt, cap_dt)                     # clamp: never beyond what we requested
    shortfall_h = max(0.0, (cap_dt - covers_dt).total_seconds() / 3600.0)
    return covers_dt.date().isoformat(), shortfall_h, governing


# --------------------------------------------------------------------------- #
# single-attempt fetch (no retry / no backoff)                                  #
# --------------------------------------------------------------------------- #
def _fetch_interval_once(station_id: str, interval: str, begin_yyyymmdd: str,
                         horizon_hours: int, pacer: "_Pacer | None" = None) -> list | None:
    """One interval's predictions over the station's (jittered) horizon, SINGLE attempt per datum.

    Known-bad is a PERMANENT fact, so it must require a GENUINE, well-formed CO-OPS 'no predictions'
    answer. ONLY that (HTTP 200 + JSON + an error message containing 'no predictions') cascades to the
    next datum and can ultimately mark a station known-bad. Every other condition is TRANSIENT and
    raises _TideOutage (trips the breaker, serves stale, never poisons known-bad):
      * a transport failure (connection/timeout/other);
      * ANY non-200 (429/5xx AND 4xx alike — a 403/400 throttle is not a data answer);
      * a 200 whose body is not JSON (an HTML/interstitial throttle page);
      * a 200 error body that is NOT 'no predictions' (a rate-limit/quota/anything-else message).
    This is the exact bug from run 20:30Z: a throttle (200-error-body / non-429) was mis-read as
    'no predictions' and 175 stations were written to the permanent known-bad list.
    Returns the predictions list, or None only when EVERY datum returned a genuine no-predictions
    answer. `pacer.wait()` (when given) throttles each request to NOAA_COOPS_MIN_INTERVAL_S."""
    last_status: int | None = None
    last_msg = ""
    for datum in NOAA_COOPS_DATUMS:
        params = {
            "station": station_id,
            "product": "predictions",
            "datum": datum,
            "units": "english",
            "time_zone": "lst_ldt",
            "interval": interval,
            "begin_date": begin_yyyymmdd,
            "range": horizon_hours,
            "format": "json",
        }
        if pacer is not None:
            pacer.wait()
        try:
            resp = get_once(NOAA_COOPS_ENDPOINT, params=params, timeout=NOAA_COOPS_TIMEOUT_S)
        except (requests.ConnectionError, requests.Timeout) as e:
            raise _TideOutage(f"{station_id} {interval}: {type(e).__name__}") from e
        except requests.RequestException as e:  # any other transport failure = outage for our purpose
            raise _TideOutage(f"{station_id} {interval}: {type(e).__name__}") from e
        last_status = resp.status_code
        # ANY non-200 is a transport/throttle condition, NEVER a data answer — trips the breaker via
        # _TideOutage and must never poison known-bad. Log the status + body so we are not blind.
        if resp.status_code != 200:
            raise _TideOutage(f"{station_id} {interval} datum={datum}: HTTP {resp.status_code}: "
                              f"{resp.text[:200]!r}")
        try:
            data = resp.json()
        except ValueError:
            raise _TideOutage(f"{station_id} {interval} datum={datum}: HTTP 200 non-JSON: "
                              f"{resp.text[:200]!r}")
        if "error" in data:
            last_msg = _coops_error_message(data)
            if _is_genuine_no_predictions(data):
                continue                       # this datum genuinely has none — try the next datum
            # An error body that is NOT 'no predictions' (throttle / quota / unexpected) — TRANSIENT.
            raise _TideOutage(f"{station_id} {interval} datum={datum}: CO-OPS error (not "
                              f"no-predictions), HTTP 200: {last_msg[:200]!r}")
        return data.get("predictions") or []
    # Every datum returned a genuine 'no predictions' answer → the station legitimately has none.
    log.info("tides: %s %s — genuine 'no predictions' on all %d datums (HTTP %s: %s)",
             station_id, interval, len(NOAA_COOPS_DATUMS), last_status, last_msg[:200])
    return None


def _fetch_station_30d(station_id: str, begin_yyyymmdd: str, horizon_hours: int,
                       pacer: "_Pacer | None" = None) -> tuple[list, list]:
    """Both intervals for a station in one bounded pass over its (jittered) horizon. Raises _TideOutage
    on a transport failure (the breaker signal); hilo is tried first, so an outage costs ONE timeout,
    not two. `pacer` throttles each request to NOAA_COOPS_MIN_INTERVAL_S."""
    hilo = _fetch_interval_once(station_id, "hilo", begin_yyyymmdd, horizon_hours, pacer)
    hourly = _fetch_interval_once(station_id, "h", begin_yyyymmdd, horizon_hours, pacer)
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
    known_bad_map = _load_no_predictions_map() if use_cache else {}   # {sid: first_seen_iso}, TTL-pruned
    known_bad = set(known_bad_map)
    active_ids = [sid for sid in station_ids if sid not in known_bad]
    skipped_known = len(station_ids) - len(active_ids)

    today = date.today()
    begin_yyyymmdd = today.strftime("%Y%m%d")
    win_start, win_end = today, today + timedelta(days=_OUTPUT_DAYS)   # 7-day OUTPUT slice (unchanged)
    # A cache is "fresh" (needs no refetch) while it still covers >= TIDE_CACHE_REFETCH_WITHIN_HOURS
    # of horizon from today — i.e. covers_until >= this date.
    refetch_until = today + timedelta(hours=TIDE_CACHE_REFETCH_WITHIN_HOURS)
    deadline = time.monotonic() + TIDE_STAGE_DEADLINE_S
    pacer = _Pacer(NOAA_COOPS_MIN_INTERVAL_S)   # throttle CO-OPS to avoid the rate-limit collapse

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

            # need a live refetch (no cache / cache expiring within the window). Horizon is a
            # deterministic per-station jitter (25-30 days) so a cold-started fleet doesn't all expire
            # on one day — the covers_until below inherits it.
            horizon_h = _station_horizon_hours(sid)
            try:
                hilo, hourly = _fetch_station_30d(sid, begin_yyyymmdd, horizon_h, pacer)  # (C) single attempt, paced
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
                # Reached only when BOTH intervals returned a GENUINE 'no predictions' answer on every
                # datum (a throttle/non-200/non-JSON would have raised _TideOutage above, not landed
                # here). Only then is a permanent known-bad mark warranted.
                new_bad.append(sid)
                log.info("tides: %s — genuine no-predictions on both hilo+hourly, all datums — "
                         "marking known-bad", sid)
                continue

            # covers_until MUST be data-derived (see _coverage_from_series): a station returning fewer
            # days than requested must not be cached as fully covered. None => an incomplete pair (a
            # series empty / unparseable) — don't persist a coverage-claiming entry; route it through
            # the stale/short handling so it serves best-effort now and refetches next run.
            coverage = _coverage_from_series(hilo, hourly, today, horizon_h)
            if coverage is None:
                log.warning("tides: %s returned an incomplete series (hilo=%d rows, hourly=%d rows) — "
                            "not caching; serving stale/empty, will retry next run",
                            sid, len(hilo), len(hourly))
                out[sid] = _stale_entry(sid, cache, win_start, win_end)
                n_stale += 1
                continue
            covers_until, shortfall_h, governing = coverage
            if shortfall_h > 24:
                # Materially short of the requested horizon — a station that structurally can't reach
                # it will refetch every run (correct, but must be VISIBLE, not silent). Once per station.
                log.warning("tides: %s covers only through %s — ~%.0fh short of the requested %dh "
                            "horizon (governed by the %s series); it will refetch every run until it "
                            "can cover the window", sid, covers_until, shortfall_h, horizon_h, governing)

            now_iso = datetime.now(tz=timezone.utc).isoformat()
            entry = {
                "station_id": sid,
                "fetched_at": now_iso,
                "covers_from": today.isoformat(),
                "covers_until": covers_until,
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
            # Merge: keep each still-valid entry's ORIGINAL first_seen (TTL measured from first
            # confirmation, not refreshed every run) and stamp the newly-confirmed with now. This also
            # prunes TTL-expired entries, since known_bad_map is already TTL-filtered.
            stamp = datetime.now(tz=timezone.utc).isoformat()
            merged = dict(known_bad_map)
            for sid in new_bad:
                merged[sid] = stamp
            _save_no_predictions(merged)
        TIDES_FORECAST_FILE.parent.mkdir(parents=True, exist_ok=True)
        TIDES_FORECAST_FILE.write_text(json.dumps(out, indent=2, ensure_ascii=False))

    log.info("tides: wrote %d stations to %s (live=%d, cached=%d, stale/missing=%d, known-bad-new=%d, "
             "breaker=%s)", len(out), TIDES_FORECAST_FILE, n_live, n_cache, n_stale, len(new_bad),
             "OPEN" if breaker_open else "closed")
    return out


# --------------------------------------------------------------------------- #
# maintenance CLI                                                               #
# --------------------------------------------------------------------------- #
def _main(argv: list[str] | None = None) -> int:
    import argparse
    p = argparse.ArgumentParser(description="Tide stage maintenance (the fetch itself runs via "
                                            "pipeline.forecast.fetch_all).")
    p.add_argument("--clear-known-bad", action="store_true",
                   help="Invalidate the persisted no-predictions (known-bad) station list so every "
                        "station is re-verified on the next run. Use after a throttle/outage run.")
    args = p.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.clear_known_bad:
        n = clear_known_bad()
        log.info("tides: cleared %d known-bad station(s) from %s", n, _NO_PREDICTIONS_FILE)
        return 0
    p.print_help()
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(_main())
