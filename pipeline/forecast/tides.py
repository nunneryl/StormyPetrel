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
from ..enrichment.geodata import load_tide_stations
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


def _no_data_entry(station_id: str) -> dict:
    """The explicit NO-DATA state: we have nothing to serve for this window and say so.

    Same shape as every other entry so downstream never special-cases it, but `asof` is None —
    which is the honest distinction from a stale-but-usable entry. An entry carrying
    `asof: "2026-07-30..."` reads as "we have July's tides, just old"; this one reads as "we
    have no tides for these dates at all", which is what is true. stations_with_no_rows picks it
    up (both series empty) so the station and its spots are NAMED in the run summary."""
    return {"station_id": station_id, "fetched_at": None, "asof": None,
            "stale": True, "hilo": [], "hourly": []}


def _stale_entry(station_id: str, cache: dict | None, start_date: date, end_date: date) -> dict:
    """Best-effort stale output — but ONLY from a cache that still covers the OUTPUT WINDOW.

    THE BUG THIS FIXES. This used to be `if cache:` — any non-None cache was re-adopted and
    emitted with stale=True, with no date check anywhere on the path. Coverage is tested once,
    at the (B) gate, against `refetch_until`; a station that falls THROUGH that gate (its cache
    has lapsed) and then fails its refetch — breaker open, outage, stage deadline, coverage
    anomaly — landed here, where `covers_until` was never consulted again. So a cache whose
    predictions stopped weeks ago was served as "stale but fine" indefinitely, and because the
    only thing distinguishing it from a healthy stale entry is a `fetched_at` nobody reads,
    nothing said so. TWC0965F sat in exactly that state: cached 2026-07-30, covering to
    2026-08-27, re-served every run through September.

    A PARTIAL WINDOW IS THE DANGEROUS CASE, not an empty one. If covers_until has merely fallen
    INSIDE the 7-day window — say it covers 4 of the 7 days — _slice returns those 4 days and
    the entry looks healthy: non-empty series, plausible asof, and stations_with_no_rows does
    NOT flag it because hilo is non-empty. Downstream that is not "4 good days and 3 missing":
    interpret.build_tide_series takes min/max from whatever rows are present, so a 4-day series
    RESCALES tide_norm = (v-min)/(max-min) for the days it does cover (a 4-day tidal range is
    not a 7-day one across the spring/neap cycle), and lookup_tide_norm returns None past the
    last point, scoring the uncovered hours at a neutral tide_multiplier of 1.0. Wrong numbers
    on the covered days and silent neutrality on the rest. Hence all-or-nothing: partial
    coverage is treated as missing, and the good days are deliberately discarded rather than
    published under a normalisation that does not apply to them.

    The test is `_cache_covers(cache, end_date)` — the window we are about to EMIT, not the
    refetch threshold. (They happen to be the same date today, TIDE_CACHE_REFETCH_WITHIN_HOURS
    being 168h and _OUTPUT_DAYS 7, but they answer different questions and must not be tied:
    one asks "is it worth a network round-trip", this one asks "can we honestly publish this".)
    It is conservative by up to a day, since _slice's range is half-open [start, end) while
    covers_until is a date — a cache ending ON end_date-1 covers the window but is rejected. A
    day of margin on the side of not publishing a truncated series is the right way to be wrong.
    """
    if _cache_covers(cache, end_date):
        return _emit(station_id, cache, start_date, end_date, stale=True, asof=cache.get("fetched_at"))
    return _no_data_entry(station_id)


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

    Returns (covers_until, shortfall_hours, governing_series), or None only when BOTH series are empty/
    unparseable (the caller has already routed genuine both-empty to known-bad, so None here means a rows-
    present-but-unparseable anomaly). Coverage is governed by whichever PRESENT series ends first: a
    CO-OPS SUBORDINATE station publishes high/low predictions ONLY and structurally never an hourly
    harmonic curve, so hilo-only is VALID coverage (interpret.build_tide_series already falls back to
    hilo), not an incomplete fetch — it is governed by hilo alone. covers_until is that governing DATE,
    CLAMPED so it can never exceed the requested horizon (today + horizon_hours); it is a 'YYYY-MM-DD'
    string so its first 10 chars stay compatible with _cache_covers' date.fromisoformat(cu[:10]).
    shortfall_hours is how far that governing timestamp falls short of the requested horizon."""
    lasts = [(name, dt) for name, dt in (("hilo", _last_dt(hilo)), ("hourly", _last_dt(hourly)))
             if dt is not None]
    if not lasts:
        return None                                          # both empty/unparseable — nothing to cache
    governing, governing_dt = min(lasts, key=lambda nd: nd[1])   # the present series that ends FIRST
    cap_dt = datetime.combine(today, datetime.min.time()) + timedelta(hours=horizon_hours)
    covers_dt = min(governing_dt, cap_dt)                    # clamp: never beyond what we requested
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
                       pacer: "_Pacer | None" = None, skip_hourly: bool = False) -> tuple[list, list]:
    """Both intervals for a station in one bounded pass over its (jittered) horizon. Raises _TideOutage
    on a transport failure (the breaker signal); hilo is tried first, so an outage costs ONE timeout,
    not two. `pacer` throttles each request to NOAA_COOPS_MIN_INTERVAL_S. `skip_hourly` omits the hourly
    interval for a station already KNOWN to be hilo-only (subordinate) — a subordinate station returns a
    genuine no-predictions on the hourly interval for every datum, so the attempt is 3 wasted paced
    requests each refetch; once discovered (cached hilo_only) we don't repeat it."""
    hilo = _fetch_interval_once(station_id, "hilo", begin_yyyymmdd, horizon_hours, pacer)
    hourly = [] if skip_hourly else _fetch_interval_once(station_id, "h", begin_yyyymmdd, horizon_hours, pacer)
    return (hilo or []), (hourly or [])


# --------------------------------------------------------------------------- #
# visibility: a station that produces nothing must SAY SO, by name               #
# --------------------------------------------------------------------------- #
# A station could return zero rows on every hour and leave no trace in a run log. The two
# steady states both hide:
#   * KNOWN-BAD — skipped at the top of fetch(); named once, on the run that marked it, then
#     forever after only a count in "%d known-no-predictions".
#   * UNREACHABLE — _stale_entry with no cache emits empty series; logged at DEBUG only.
# Either way the spots pointing at it are silently tideless, and the only symptom is an
# em-dash on a page nobody is watching. Kalaloch Beach sat like that until it was found by
# hand. These two helpers are pure so the warnings can be tested without a network.
MAX_NAMED_IN_WARNING = 12


def stations_with_no_rows(emitted: dict[str, dict], requested: list[str]) -> list[str]:
    """Requested station ids that will give no spot a tide, sorted.

    ABSENT COUNTS, and getting this wrong was the first thing the end-to-end test caught.
    Scanning only the emitted dict misses the loudest case: a station that returns nothing
    on both intervals is marked known-bad and `continue`s WITHOUT being added to `out`
    (fetch, the `if not hilo and not hourly` branch), and one already on the known-bad list
    is filtered out of `active_ids` before the loop starts. Neither ever appears in
    `emitted`, so a check over `emitted.items()` would have reported zero on exactly the
    station that prompted this work.

    So the definition is taken from what was ASKED FOR, not from what came back:
      * requested and missing from `emitted`  -> skipped or abandoned
      * requested and present but empty       -> unreachable, or a coverage anomaly
    Both mean the same thing downstream — no tide for any spot on that station.
    """
    got = emitted or {}
    out = []
    for sid in set(requested or []):
        entry = got.get(sid)
        if entry is None or (not entry.get("hilo") and not entry.get("hourly")):
            out.append(sid)
    return sorted(out)


def unresolvable_station_ids(spots: list[dict], stations: list[dict]) -> dict[str, list[str]]:
    """{station_id: [spot names]} for roster ids that match no entry in the station list.

    THE ROSTER AND THE STATION FILE CAN DISAGREE, and nothing checked that they didn't. A
    spot's nearest_tide_station_id is written by a PAST enrich run against the station file
    as it was THEN; the file is a downloaded artifact (gitignored) that can be refreshed
    independently. So an id can stop resolving without any code changing and without any
    error: the fetcher asks CO-OPS for a station that is no longer in our list, and every
    spot pointing at it goes quietly tideless.

    Exact string comparison, deliberately. A near-match — differing case, or a dropped
    suffix on a subordinate id like TWC0965 vs TWC0965F — is exactly the failure this exists
    to surface, so normalising the two sides before comparing would hide it.

    Returns {} when the station list is EMPTY: that means the file is missing, not that all
    234 ids went bad, and reporting the whole roster as unresolvable would be a false alarm
    louder than the signal.
    """
    known = {str(st.get("id")) for st in (stations or []) if st.get("id") is not None}
    if not known:
        return {}
    out: dict[str, list[str]] = {}
    for sp in spots or []:
        sid = sp.get("nearest_tide_station_id")
        if not sid:
            continue
        if str(sid) not in known:
            out.setdefault(str(sid), []).append(str(sp.get("name") or "?"))
    return {k: sorted(v) for k, v in sorted(out.items())}


def _format_named(items: list[str], limit: int = MAX_NAMED_IN_WARNING) -> str:
    """Comma-joined, capped, with the remainder counted. A warning that dumps 200 ids is
    scrolled past as fast as one that names none."""
    if len(items) <= limit:
        return ", ".join(items)
    return f"{', '.join(items[:limit])} (+{len(items) - limit} more)"


# --------------------------------------------------------------------------- #
# run accounting — the summary must be reconcilable to the station list          #
# --------------------------------------------------------------------------- #
class _Partition:
    """Every station in the run lands in EXACTLY ONE bucket, and the buckets sum to the total.

    WHAT WENT WRONG. The old accounting was four loose ints (n_live, n_cache, n_stale,
    n_hilo_only) incremented at the branches that happened to have an increment. Two paths had
    none — the genuine-no-predictions branch and the known-bad skip — and n_hilo_only was not a
    bucket at all but an ATTRIBUTE of stations already counted in n_live, so adding the printed
    numbers up gave a total that matched nothing. A run then reported `live=0, cached=233,
    stale/missing=0` over 234 stations: three buckets summing to 233, one station simply absent,
    and no line anywhere that would have shown it. The count that WOULD have exposed it —
    len(station_ids) — was printed once, before the loop, forty lines away from the summary.

    THIS IS THE FOURTH TIME A RUN SUMMARY HAS REPORTED HEALTH IT DID NOT HAVE, and the four
    share one shape — a number that is true about the subset the code walked, presented as
    though it were about the job:
      1. the sampled-distance warning counted CELLS it had examined and reported that count as
         though it were about DISTANCE, which it had never measured;
      2. db_import counted the spots it SKIPPED without naming any of them, so the count was
         un-actionable and nobody could tell which spots were missing;
      3. WW3 reported a STEP COUNT with no horizon, so "48 steps" could mean 48 hours or 6 days
         and the summary could not distinguish a full run from a truncated one;
      4. this one — three health buckets that did not add up to the station list, so a station
         could take a path with no increment and vanish from its own run's totals.
    The lesson each time is the same and it is not "add another counter": a summary must be
    RECONCILABLE against the thing it summarises, by arithmetic the reader can do on one line.
    So the denominator is printed beside the parts, the parts are disjoint and exhaustive by
    construction rather than by inspection, and a station that reaches no bucket is not merely
    absent — it is counted as `uncounted` and NAMED at ERROR.

    Disjoint by construction: bucketing is a dict keyed by station id, so a station cannot be in
    two buckets; a second, conflicting put() is recorded in `double_assigned` and reported.
    Exhaustive by check, not by hope: uncounted() is the ids in the roster that no branch
    claimed, which is precisely the failure above.
    """

    #: In report order. Each is a TERMINAL state of one station in one run.
    BUCKETS = ("live", "cached", "stale", "no-data", "no-predictions", "known-bad-skipped")

    def __init__(self, station_ids: list[str]) -> None:
        self._all: list[str] = [str(s) for s in station_ids or []]
        self._of: dict[str, str] = {}
        self.double_assigned: list[str] = []

    def put(self, station_id: str, bucket: str) -> None:
        sid = str(station_id)
        if bucket not in self.BUCKETS:                       # programming error, never data
            raise ValueError(f"unknown bucket {bucket!r}")
        prev = self._of.get(sid)
        if prev is not None and prev != bucket:
            self.double_assigned.append(f"{sid}:{prev}->{bucket}")
        self._of[sid] = bucket

    def uncounted(self) -> list[str]:
        """Roster ids no branch claimed. MUST be empty; when it is not, these are the names."""
        return [sid for sid in self._all if sid not in self._of]

    def counts(self) -> dict[str, int]:
        c = {b: 0 for b in self.BUCKETS}
        for b in self._of.values():
            c[b] += 1
        c["uncounted"] = len(self.uncounted())
        return c

    def total(self) -> int:
        """The sum the reader would get by adding the printed parts."""
        return sum(self.counts().values())

    def balanced(self) -> bool:
        return (self.total() == len(self._all)
                and not self.uncounted() and not self.double_assigned)

    def render(self) -> str:
        """One line the reader can check by adding up, with the denominator on it."""
        c = self.counts()
        parts = " + ".join(f"{b} {c[b]}" for b in (*self.BUCKETS, "uncounted"))
        return (f"{len(self._all)} stations = {parts} "
                f"[sum {self.total()}, {'balanced' if self.balanced() else 'MISMATCH'}]")


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
def fetch(spots: list[dict], use_cache: bool = True,
          _fetch_station=_fetch_station_30d) -> dict[str, dict]:
    """Fetch/serve tide predictions for every unique station in *spots*. NEVER raises and is bounded
    in wall-clock (cap + breaker + deadline) so a dead CO-OPS backend cannot block the pipeline.
    Returns a dict keyed by station_id (each with `asof`/`stale`) and writes TIDES_FORECAST_FILE.

    *_fetch_station* is injectable so the run summary's reporting can be tested without a
    network, matching the `_fetch=` seam on mop.apply_mop_overrides. Production never passes
    it."""
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
    n_hilo_only = 0          # an ATTRIBUTE of the `live` bucket, not a bucket — see _Partition

    # Exhaustive accounting over the FULL station list, not just the ones we walk. The known-bad
    # skips are the stations that never enter the loop at all; seeding them here is what makes
    # the partition cover `station_ids` rather than `active_ids`.
    part = _Partition(station_ids)
    for sid in station_ids:
        if sid in known_bad:
            part.put(sid, "known-bad-skipped")

    # {station_id: covers_until (or None when there was no cache at all)} for stations that
    # ended the run with nothing publishable. Named in a WARNING below — see _no_data_entry.
    no_data: dict[str, str | None] = {}

    def _serve_stale(sid: str, cache: dict | None) -> dict:
        """Route every stale exit through ONE place, so the entry, the bucket and the warning
        cannot disagree about whether the cache was usable. _stale_entry makes the call; this
        re-asks the same predicate to classify, rather than inferring it from the entry."""
        entry = _stale_entry(sid, cache, win_start, win_end)
        if _cache_covers(cache, win_end):
            part.put(sid, "stale")                    # cache still covers the window; just not refreshed
        else:
            part.put(sid, "no-data")
            no_data[sid] = (cache or {}).get("covers_until")
        return entry

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
                    out[rsid] = _serve_stale(rsid, rcache)
                break

            # (B) fresh long-cache — no network at all (deterministic predictions still valid).
            if _cache_covers(cache, refetch_until):
                out[sid] = _emit(sid, cache, win_start, win_end, stale=False, asof=cache.get("fetched_at"))
                part.put(sid, "cached")
                continue

            # (D) breaker open — don't touch NOAA; serve stale-or-empty.
            if breaker_open:
                out[sid] = _serve_stale(sid, cache)
                continue

            # need a live refetch (no cache / cache expiring within the window). Horizon is a
            # deterministic per-station jitter (25-30 days) so a cold-started fleet doesn't all expire
            # on one day — the covers_until below inherits it.
            horizon_h = _station_horizon_hours(sid)
            skip_hourly = bool(cache and cache.get("hilo_only"))   # subordinate: skip the always-empty hourly
            try:
                hilo, hourly = _fetch_station(sid, begin_yyyymmdd, horizon_h, pacer, skip_hourly)  # (C) single attempt, paced
            except _TideOutage as e:
                consecutive_failures += 1
                log.debug("tides: %s unreachable (%s) [%d in a row]", sid, e, consecutive_failures)
                out[sid] = _serve_stale(sid, cache)
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
                part.put(sid, "no-predictions")   # THE PATH THAT USED TO INCREMENT NOTHING
                log.info("tides: %s — genuine no-predictions on both hilo+hourly, all datums — "
                         "marking known-bad", sid)
                continue

            # covers_until MUST be data-derived (see _coverage_from_series): a station returning fewer
            # days than requested must not be cached as fully covered. coverage is None ONLY when both
            # series are empty/unparseable despite passing the both-empty check above — a genuine
            # (rows-present-but-garbled) anomaly → don't persist; serve stale/empty and retry next run.
            coverage = _coverage_from_series(hilo, hourly, today, horizon_h)
            if coverage is None:
                log.warning("tides: %s returned rows with no parseable timestamp (hilo=%d, hourly=%d) — "
                            "not caching; serving stale/empty, will retry next run",
                            sid, len(hilo), len(hourly))
                out[sid] = _serve_stale(sid, cache)
                continue
            covers_until, shortfall_h, governing = coverage
            if shortfall_h > 24:
                # Materially short of the requested horizon — a station that structurally can't reach
                # it will refetch every run (correct, but must be VISIBLE, not silent). Once per station.
                log.warning("tides: %s covers only through %s — ~%.0fh short of the requested %dh "
                            "horizon (governed by the %s series); it will refetch every run until it "
                            "can cover the window", sid, covers_until, shortfall_h, horizon_h, governing)

            # A CO-OPS SUBORDINATE station publishes hilo only (no hourly harmonic curve). That is NORMAL
            # coverage — interpret.build_tide_series falls back to hilo — NOT a failed fetch, so it is
            # cached like any other and logged at DEBUG (never WARNING). The persisted hilo_only flag lets
            # the next refetch skip the always-empty hourly interval (3 fewer paced requests/refetch).
            hilo_only = not hourly
            if hilo_only:
                n_hilo_only += 1
                log.debug("tides: %s is hilo-only (subordinate) — %d hilo rows, no hourly curve",
                          sid, len(hilo))

            now_iso = datetime.now(tz=timezone.utc).isoformat()
            entry = {
                "station_id": sid,
                "fetched_at": now_iso,
                "covers_from": today.isoformat(),
                "covers_until": covers_until,
                "hilo_only": hilo_only,
                "hilo": hilo,
                "hourly": hourly,
            }
            _save_cache(sid, entry)
            out[sid] = _emit(sid, entry, win_start, win_end, stale=False, asof=now_iso)
            part.put(sid, "live")
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

    # ---- THE SUMMARY, RECONCILABLE ON ONE LINE ---------------------------------------- #
    # The denominator sits beside the parts so the reader can add them up without going back
    # forty lines for len(station_ids), and `wrote N` is explained rather than left to differ
    # from the total for unstated reasons: the entries NOT written are exactly the two buckets
    # that produce no output (no-predictions + known-bad-skipped). hilo-only is stated as an
    # attribute of `live`, not as a bucket, because it was previously printed alongside the
    # buckets and silently double-counted stations already in them.
    log.info("tides: %s; wrote %d entries to %s (the %d not written = no-predictions + "
             "known-bad-skipped); hilo-only %d of live; breaker %s",
             part.render(), len(out), TIDES_FORECAST_FILE, len(station_ids) - len(out),
             n_hilo_only, "OPEN" if breaker_open else "closed")

    # THE LINE THAT WOULD HAVE MADE THE MISSING STATION VISIBLE. A bare `assert` is wrong here
    # twice over: this stage's contract is that it NEVER raises (see the module docstring), and
    # -O would strip it. So the imbalance is reported at ERROR, with the ids — a count of
    # unaccounted stations is the same un-actionable number that started this.
    if not part.balanced():
        log.error("tides: RUN ACCOUNTING BROKEN — the buckets do not partition the station list "
                  "(%s). Unaccounted: %s. Double-assigned: %s. Every station must reach exactly "
                  "one terminal bucket; a station that reaches none took a code path with no "
                  "counter, which is how a station vanished from its own run's totals before.",
                  part.render(), _format_named(part.uncounted()) or "(none)",
                  _format_named(part.double_assigned) or "(none)")

    # ---- COUNTED AND NAMED, because a count alone is not a symptom -------------------- #
    # Both of these were previously invisible: a station could produce nothing on every run
    # and the only trace was an increment in an aggregate above. Named at WARNING so a run
    # log shows WHICH station and WHICH spots, which is what anyone acts on.
    spots_by_station: dict[str, list[str]] = {}
    for sp in spots or []:
        sid = sp.get("nearest_tide_station_id")
        if sid:
            spots_by_station.setdefault(str(sid), []).append(str(sp.get("name") or "?"))

    # ---- THE STALE SERVE, NAMED --------------------------------------------------------- #
    # A station whose cache had lapsed PAST the output window and whose refetch did not land is
    # the loudest thing this stage can find, and until now it was the quietest: _stale_entry
    # re-adopted the expired cache, marked it stale=True, and the run said nothing. `stale` is
    # not a severity — a cache that still covers the window and merely wasn't refreshed is fine
    # and stays at DEBUG; this is the other kind, where the spots publish no tide at all.
    # Named, with the expiry date, in the same shape as the roster-resolution warning below.
    if no_data:
        detail = [f"{sid} (cache covered to {cu})" if cu else f"{sid} (no cache)"
                  for sid, cu in sorted(no_data.items())]
        nd_spots = sorted({n for sid in no_data for n in spots_by_station.get(sid, [])})
        log.warning(
            "tides: %d station(s) had NO usable predictions for the %s..%s window and were NOT "
            "served from cache — %d spot(s) publish no tide. Stations: %s. Spots: %s. An expired "
            "cache is deliberately treated as missing rather than re-served: a cache that has "
            "lapsed part-way into the window would rescale tide_norm for the days it does cover "
            "(see _stale_entry). These recover by themselves on the next run that reaches "
            "CO-OPS; if they persist, the refetch is failing and %s is where to look.",
            len(no_data), win_start.isoformat(), win_end.isoformat(), len(nd_spots),
            _format_named(detail), _format_named(nd_spots), _NO_PREDICTIONS_FILE,
        )

    # The broader check: anything that ended up with an empty window for ANY reason — the
    # no-data stations above, plus known-bad skips and stations that never reached `out` at all.
    # Deliberately overlapping; this one answers "which spots are tideless", that one "why".
    empty = stations_with_no_rows(out, station_ids)
    if empty:
        affected = sorted({n for sid in empty for n in spots_by_station.get(sid, [])})
        log.warning(
            "tides: %d station(s) returned NO rows for any hour — %d spot(s) will publish no "
            "tide at all. Stations: %s. Spots: %s. Check %s for these ids (a station confirmed "
            "to have no predictions is skipped for %d days) and, if one is listed there in "
            "error, delete its entry and re-run.",
            len(empty), len(affected), _format_named(empty), _format_named(affected),
            _NO_PREDICTIONS_FILE, TIDE_KNOWN_BAD_TTL_DAYS,
        )

    # The roster and the station file are produced by different runs and can drift apart —
    # see unresolvable_station_ids. Checked here because this is the first place both are in
    # scope, and skipped entirely when the station file is absent.
    try:
        unresolved = unresolvable_station_ids(spots, load_tide_stations())
    except Exception as e:                                       # noqa: BLE001
        # Visibility must never take the stage down; tides are a rating MODIFIER.
        log.debug("tides: could not check roster station ids against the station list (%s)", e)
        unresolved = {}
    if unresolved:
        n_spots = sum(len(v) for v in unresolved.values())
        log.warning(
            "tides: %d station id(s) on the roster match NO entry in the tide station list — "
            "%d spot(s) are pointing at a station we cannot resolve and will publish no tide. "
            "Ids: %s. Spots: %s. The comparison is exact, so a dropped suffix or a case "
            "difference (TWC0965 vs TWC0965F) lands here; re-run enrich to re-assign against "
            "the current station file.",
            len(unresolved), n_spots, _format_named(sorted(unresolved)),
            _format_named(sorted({n for v in unresolved.values() for n in v})),
        )
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
