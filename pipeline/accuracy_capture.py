"""Capture the accuracy scoreboard's raw material after the database push (migration 030).

    python -m pipeline.accuracy_capture

The last step of the full pipeline (.github/workflows/forecast-pipeline.yml), after db_import has
pushed every spot, forecast, buoy reading and tide. It writes two tables nothing else reads yet:

  forecast_leads   the spot forecast as issued 1, 2 and 3 days ahead, for local hours 06, 09, 12,
                   15 and 18, from the same ratings.json db_import just pushed;
  model_at_buoys   WAVEWATCH III and NWPS sampled at the roster's buoys every 3 hours UTC, lead
                   days 0 to 3, from the GRIB files this run's fetch left in pipeline/cache.

WHY. forecasts keeps one row per spot and hour, overwritten by every run, so a past hour holds only
its last, shortest-lead forecast; NOMADS keeps about 5 days of model files. Neither the day-ahead
forecasts nor the model at the buoys can be rebuilt later. Decided 2026-11-13 (scoreboard design).

LEAD DAY. lead_day = floor((valid_time - issued_at) / 24 h), issued_at being the NWPS cycle (spots,
and NWPS at a buoy) or the WAVEWATCH III cycle. Each run writes every hour it has; the database
keeps, per (spot or buoy, hour, lead day), the newest issue (migration 030's trigger), which is the
latest forecast issued at least that many days ahead.

IT NEVER STOPS THE UPLOAD, by three walls: it runs after db_import, as its own step; the step is
continue-on-error with its own timeout; and this module catches every failure, prints it as a
::warning:: and exits 0. Each part (forecast_leads, WAVEWATCH III, NWPS) is tried on its own, so one
failing does not stop the others. Sampling stops at a time budget (SAMPLE_BUDGET_S) and says what
it skipped.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from .config import (
    DEFAULT_ENRICHED_OUTPUT,
    NWPS_CACHE_DIR,
    RATINGS_FILE,
    WW3_FORECAST_FILE,
    WW3_STEP_HOURS,
)

log = logging.getLogger("pipeline.accuracy_capture")

LOCAL_HOURS = (6, 9, 12, 15, 18)     # spot hours kept, in the spot's own time zone
SPOT_LEAD_DAYS = (1, 2, 3)           # lead day 0 is what forecasts already keeps
BUOY_LEAD_DAYS = (0, 1, 2, 3)
BUOY_EVERY_H = 3                     # buoy hours kept: 00, 03, ... 21 UTC
SAMPLE_BUDGET_S = 360.0              # model sampling stops here; the step's timeout is 10 min
BATCH = 1000

# The model fields each source's extractor writes -> model_at_buoys columns.
MODEL_COLUMNS = (
    "hs", "tp", "dp", "swell_hs",
    "swell_1_hs", "swell_1_tp", "swell_1_dp", "swell_2_hs", "swell_2_tp", "swell_2_dp",
    "swell_3_hs", "swell_3_tp", "swell_3_dp",
    "wind_wave_hs", "wind_wave_tp", "wind_wave_dp", "wind_speed", "wind_dir",
)
FIELD_MAP = {
    # forecast/ww3._extract_step_vectorized: total_* from swh/perpw/dirpw, wind sea, 3 swells.
    "ww3": {"total_hs": "hs", "total_tp": "tp", "total_dp": "dp",
            **{f"swell_{n}_{f}": f"swell_{n}_{f}" for n in (1, 2, 3) for f in ("hs", "tp", "dp")},
            **{f"wind_wave_{f}": f"wind_wave_{f}" for f in ("hs", "tp", "dp")}},
    # forecast/nwps._extract_time_series_from_datasets (its _VAR_MAP).
    "nwps": {"hs": "hs", "tp": "tp", "dp": "dp", "swell_hs": "swell_hs",
             "windsea_hs": "wind_wave_hs", "wind_speed": "wind_speed", "wind_dir": "wind_dir"},
}
SPOT_FIELDS = ("hs", "tp", "dp", "swell_hs", "swell_tp", "swell_dp", "wind_speed", "wind_dir",
               "swell_source", "chop_ratio", "face_ft", "face_ft_raw", "face_correction_version",
               "stars")

_NWPS_FILE = re.compile(r"^([a-z]{3})_(\d{8})_(\d{2})\.grib2$")


# --------------------------------------------------------------------------- #
# Times and lead days                                                          #
# --------------------------------------------------------------------------- #

def _ts(value: str) -> datetime:
    """An ISO time (Z or offset) as an aware UTC datetime."""
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def lead_hours(issued: datetime, valid: datetime) -> float:
    return (valid - issued).total_seconds() / 3600.0


def lead_day(issued: datetime, valid: datetime) -> int | None:
    """floor(lead / 24 h); None for an hour before the issue."""
    h = lead_hours(issued, valid)
    return None if h < 0 else int(h // 24)


# --------------------------------------------------------------------------- #
# forecast_leads                                                               #
# --------------------------------------------------------------------------- #

def spot_lead_rows(ratings: dict, spot_ids: dict, zones: dict, captured_at: str,
                   run_id: str | None) -> tuple[list[dict], dict]:
    """forecast_leads rows from ratings.json ({spot name: [hour]}): hours at LOCAL_HOURS on the
    hour in the spot's time zone, lead day 1-3 from the hour's nwps_cycle. Returns (rows, skipped
    counts by reason)."""
    from zoneinfo import ZoneInfo

    rows: list[dict] = []
    skipped = {"no spot id": 0, "no time zone": 0, "no cycle": 0}
    for name, hours in ratings.items():
        sid = spot_ids.get(name)
        if sid is None:
            skipped["no spot id"] += 1
            continue
        tz = zones.get(name)
        if not tz:
            skipped["no time zone"] += 1
            continue
        zone = ZoneInfo(tz)
        for h in hours:
            vt, cyc = h.get("valid_time"), h.get("nwps_cycle")
            if not vt:
                continue
            valid = _ts(vt)
            local = valid.astimezone(zone)
            if local.minute or local.second or local.hour not in LOCAL_HOURS:
                continue
            if not cyc:
                skipped["no cycle"] += 1
                continue
            issued = _ts(cyc)
            d = lead_day(issued, valid)
            if d not in SPOT_LEAD_DAYS:
                continue
            row = {"spot_id": sid, "valid_time": _iso(valid), "lead_day": d,
                   "issued_at": _iso(issued), "lead_hours": int(round(lead_hours(issued, valid)))}
            row.update({k: h.get(k) for k in SPOT_FIELDS})
            row.update({"captured_at": captured_at, "run_id": run_id})
            rows.append(row)
    return rows, skipped


# --------------------------------------------------------------------------- #
# model_at_buoys                                                               #
# --------------------------------------------------------------------------- #

def buoy_lead_rows(buoy_id: str, source: str, issued: datetime, entries: list[dict],
                   cell: dict | None, wfo: str | None, captured_at: str,
                   run_id: str | None) -> list[dict]:
    """model_at_buoys rows for one buoy and source: hours every BUOY_EVERY_H UTC, lead day 0-3."""
    fmap = FIELD_MAP[source]
    cell = cell or {}
    rows = []
    for e in entries:
        vt = e.get("valid_time")
        if not vt:
            continue
        valid = _ts(vt)
        if valid.minute or valid.second or valid.hour % BUOY_EVERY_H:
            continue
        d = lead_day(issued, valid)
        if d not in BUOY_LEAD_DAYS:
            continue
        vals = {c: None for c in MODEL_COLUMNS}
        for k, col in fmap.items():
            if e.get(k) is not None:
                vals[col] = e[k]
        if all(v is None for v in vals.values()):
            continue
        rows.append({"buoy_id": buoy_id, "valid_time": _iso(valid), "source": source,
                     "lead_day": d, "issued_at": _iso(issued),
                     "lead_hours": int(round(lead_hours(issued, valid))), "wfo": wfo,
                     "cell_lat": cell.get("lat"), "cell_lng": cell.get("lng"),
                     "cell_km": cell.get("km"), **vals,
                     "captured_at": captured_at, "run_id": run_id})
    return rows


def roster_buoys(spots: list[dict], snapshot: dict) -> tuple[list[tuple[str, float, float]], list[str]]:
    """[(id, lat, lng)] for every buoy the pipeline fetches (forecast/buoys._unique_buoy_ids),
    positioned from the committed NDBC snapshot; and the ids the snapshot lacks."""
    from .forecast.buoys import _unique_buoy_ids

    out, missing = [], []
    for bid in _unique_buoy_ids(spots):
        pos = snapshot.get(str(bid).lower())
        if not pos:
            missing.append(bid)
            continue
        out.append((bid, float(pos["lat"]), float(pos["lng"])))
    return out, missing


def ww3_cycle(ww3_json: dict) -> datetime | None:
    """The WAVEWATCH III cycle this run used: the earliest valid_time in ww3.json (its f000)."""
    times = [e["valid_time"] for series in ww3_json.values() for e in series if e.get("valid_time")]
    return min((_ts(t) for t in times), default=None)


def _km(a_lat, a_lng, b_lat, b_lng) -> float:
    from .forecast.nwps_nearshore import _haversine_km
    return _haversine_km(a_lat, a_lng, b_lat, b_lng)


def _lng180(x: float) -> float:
    return x - 360.0 if x > 180.0 else x


def sample_ww3(buoys, cycle: datetime, deadline: float) -> tuple[dict, dict, list[int]]:
    """({buoy id: [entries]}, {buoy id: cell}, forecast hours not read) from this run's cached
    WAVEWATCH III step files for *cycle*, every 3 h up to lead day 3."""
    import numpy as np

    from .forecast import ww3 as W

    ids = [b[0] for b in buoys]
    lats = np.asarray([b[1] for b in buoys], dtype=float)
    lngs = np.asarray([b[2] for b in buoys], dtype=float)
    date_ymd, hh = cycle.strftime("%Y%m%d"), cycle.strftime("%H")
    wanted = [fh for fh in WW3_STEP_HOURS if fh % BUOY_EVERY_H == 0 and fh // 24 in BUOY_LEAD_DAYS]
    series: dict[str, list[dict]] = {}
    cells: dict[str, dict] = {}
    not_read: list[int] = []
    for fh in wanted:
        path = W._step_cache_path(date_ymd, hh, fh)
        if time.monotonic() > deadline or not path.exists():
            not_read.append(fh)
            continue
        datasets = W._open_grib_datasets(path)
        try:
            if not cells:
                cells = _ww3_cells(W, datasets, ids, lats, lngs)
            vt, entries = W._extract_step_vectorized(datasets, ids, lats, lngs)
        finally:
            W._close_datasets(datasets)
        if vt is None:
            not_read.append(fh)
            continue
        for bid, e in entries.items():
            e["valid_time"] = vt
            series.setdefault(bid, []).append(e)
    return series, cells, not_read


def _ww3_cells(W, datasets, ids, lats, lngs) -> dict:
    """The cell each buoy is sampled at, as forecast/ww3 picks it (nearest, or the nearest wet
    cell when that one is land), on the dataset that carries the total height."""
    import numpy as np

    for ds in datasets:
        if not any(str(v).lower() in ("swh", "htsgw") for v in ds.data_vars):
            continue
        lat_name = next((n for n in ("latitude", "lat", "y") if n in ds.coords), None)
        lng_name = next((n for n in ("longitude", "lon", "x") if n in ds.coords), None)
        if lat_name is None or lng_name is None:
            return {}
        grid_lng = np.asarray(ds[lng_name].values, dtype=float)
        lngs_ds = np.where(lngs < 0, lngs + 360.0, lngs) if grid_lng.min() >= 0 else lngs
        li, lj, _ = W._resolve_offshore_indices(ds, lat_name, lng_name, lats, lngs_ds)
        glat = np.asarray(ds[lat_name].values, dtype=float)
        out = {}
        for k, bid in enumerate(ids):
            clat, clng = float(glat[int(li[k])]), _lng180(float(grid_lng[int(lj[k])]))
            out[bid] = {"lat": round(clat, 4), "lng": round(clng, 4),
                        "km": round(_km(lats[k], lngs[k], clat, clng), 2)}
        return out
    return {}


def newest_nwps_files(cache_dir: Path) -> dict[str, tuple[Path, datetime]]:
    """{wfo: (path, cycle)} for the newest cycle file of each office in the NWPS cache."""
    out: dict[str, tuple[Path, datetime]] = {}
    for p in sorted(Path(cache_dir).glob("*.grib2")):
        m = _NWPS_FILE.match(p.name)
        if not m or p.stat().st_size == 0:
            continue
        cyc = datetime.strptime(m.group(2) + m.group(3), "%Y%m%d%H").replace(tzinfo=timezone.utc)
        if m.group(1) not in out or cyc > out[m.group(1)][1]:
            out[m.group(1)] = (p, cyc)
    return out


def sample_nwps(buoys, files: dict, deadline: float) -> tuple[dict, list[str]]:
    """({buoy id: (wfo, cycle, cell, entries)}, offices not read): each buoy inside an office's
    CG1 grid (its latitude and longitude range), at its nearest cell when that cell is wet. A buoy
    inside two grids takes the nearer cell."""
    import numpy as np

    from .forecast import nwps as N
    from .forecast.ww3 import _close_datasets

    found: dict[str, tuple] = {}
    not_read: list[str] = []
    for wfo in sorted(files):
        path, cyc = files[wfo]
        if time.monotonic() > deadline:
            not_read.append(wfo)
            continue
        datasets = N._open_grib_datasets(path)
        try:
            wave_ds, _var = N._find_wave_dataset(datasets)
            if wave_ds is None:
                continue
            glat = np.asarray(wave_ds["latitude"].values, dtype=float).ravel()
            glng = np.asarray(wave_ds["longitude"].values, dtype=float).ravel()
            for bid, lat, lng in buoys:
                lng_ds = N._normalize_longitude(wave_ds, lng)
                if not (glat.min() <= lat <= glat.max() and glng.min() <= lng_ds <= glng.max()):
                    continue
                clat = float(glat[int(np.abs(glat - lat).argmin())])
                clng = _lng180(float(glng[int(np.abs(glng - lng_ds).argmin())]))
                km = _km(lat, lng, clat, clng)
                if bid in found and found[bid][2]["km"] <= km:
                    continue
                entries = N._extract_time_series_from_datasets(datasets, lat, lng)
                if not any(e.get("hs") is not None for e in entries):
                    continue        # a land cell: every wave field is NaN there
                found[bid] = (wfo, cyc, {"lat": round(clat, 4), "lng": round(clng, 4),
                                         "km": round(km, 2)}, entries)
        finally:
            _close_datasets(datasets)
    return found, not_read


# --------------------------------------------------------------------------- #
# Writing                                                                      #
# --------------------------------------------------------------------------- #

def dedupe(rows: list[dict], key: tuple[str, ...]) -> list[dict]:
    """One row per key, the newest issue kept (one upsert statement may not touch a row twice)."""
    best: dict[tuple, dict] = {}
    for r in rows:
        k = tuple(r[c] for c in key)
        if k not in best or r["issued_at"] > best[k]["issued_at"]:
            best[k] = r
    return list(best.values())


def upsert(client, table: str, rows: list[dict], on_conflict: str) -> tuple[int, int]:
    """(sent, applied). Rows the trigger keeps (a newer issue already wrote them) are absent from
    what PostgREST returns: not an error."""
    sent = applied = 0
    for i in range(0, len(rows), BATCH):
        chunk = rows[i:i + BATCH]
        res = client.table(table).upsert(chunk, on_conflict=on_conflict).execute()
        data = getattr(res, "data", None)
        sent += len(chunk)
        applied += len(data) if isinstance(data, list) else len(chunk)
    return sent, applied


def warn(message: str) -> None:
    """A GitHub Actions warning annotation, and the same line in the log."""
    line = " ".join(str(message).split())
    print(f"::warning title=accuracy capture::{line}", flush=True)
    log.warning("%s", line)


def _count(rows: list[dict]) -> dict:
    out: dict[int, int] = {}
    for r in rows:
        out[r["lead_day"]] = out.get(r["lead_day"], 0) + 1
    return dict(sorted(out.items()))


# --------------------------------------------------------------------------- #
# The run                                                                      #
# --------------------------------------------------------------------------- #

def capture_forecast_leads(client, ratings_path: Path, spots: list[dict], captured_at: str,
                           run_id: str | None) -> dict:
    from .db_import import _spot_id_map

    ratings = json.loads(Path(ratings_path).read_text(encoding="utf-8"))
    zones = {s["name"]: s.get("time_zone") for s in spots}
    rows, skipped = spot_lead_rows(ratings, _spot_id_map(client), zones, captured_at, run_id)
    rows = dedupe(rows, ("spot_id", "valid_time", "lead_day"))
    sent, applied = upsert(client, "forecast_leads", rows, "spot_id,valid_time,lead_day")
    log.info("accuracy capture: forecast_leads sent %d, applied %d (by lead day %s); skipped %s",
             sent, applied, _count(rows), skipped)
    return {"sent": sent, "applied": applied, "by_lead_day": _count(rows), "skipped": skipped}


def capture_model_at_buoys(client, buoys, ww3_path: Path, nwps_cache: Path, captured_at: str,
                           run_id: str | None, deadline: float) -> dict:
    """WAVEWATCH III then NWPS, each tried on its own."""
    stats: dict = {}
    try:
        cyc = ww3_cycle(json.loads(Path(ww3_path).read_text(encoding="utf-8")))
        if cyc is None:
            raise ValueError(f"no WAVEWATCH III hours in {ww3_path}")
        series, cells, not_read = sample_ww3(buoys, cyc, deadline)
        rows = [r for bid, entries in series.items()
                for r in buoy_lead_rows(bid, "ww3", cyc, entries, cells.get(bid), None,
                                        captured_at, run_id)]
        rows = dedupe(rows, ("buoy_id", "valid_time", "source", "lead_day"))
        sent, applied = upsert(client, "model_at_buoys", rows, "buoy_id,valid_time,source,lead_day")
        stats["ww3"] = {"cycle": _iso(cyc), "buoys": len(series), "sent": sent,
                        "applied": applied, "by_lead_day": _count(rows), "steps_not_read": not_read}
        log.info("accuracy capture: model_at_buoys ww3 %s", stats["ww3"])
        if not_read:
            warn(f"WAVEWATCH III steps not read (absent or past the time budget): {not_read}")
    except Exception as e:  # noqa: BLE001  a failure here must not stop NWPS or the run
        stats["ww3"] = {"failed": f"{type(e).__name__}: {e}"}
        warn(f"model_at_buoys (WAVEWATCH III) not written: {type(e).__name__}: {e}")
    try:
        found, not_read = sample_nwps(buoys, newest_nwps_files(nwps_cache), deadline)
        rows = [r for bid, (wfo, cyc, cell, entries) in found.items()
                for r in buoy_lead_rows(bid, "nwps", cyc, entries, cell, wfo, captured_at, run_id)]
        rows = dedupe(rows, ("buoy_id", "valid_time", "source", "lead_day"))
        sent, applied = upsert(client, "model_at_buoys", rows, "buoy_id,valid_time,source,lead_day")
        stats["nwps"] = {"buoys": len(found), "sent": sent, "applied": applied,
                         "by_lead_day": _count(rows), "offices_not_read": not_read}
        log.info("accuracy capture: model_at_buoys nwps %s", stats["nwps"])
        if not_read:
            warn(f"NWPS offices not read (past the time budget): {', '.join(not_read)}")
    except Exception as e:  # noqa: BLE001
        stats["nwps"] = {"failed": f"{type(e).__name__}: {e}"}
        warn(f"model_at_buoys (NWPS) not written: {type(e).__name__}: {e}")
    return stats


def capture(client=None, ratings_path: Path = RATINGS_FILE,
            spots_path: Path = DEFAULT_ENRICHED_OUTPUT, ww3_path: Path = WW3_FORECAST_FILE,
            nwps_cache: Path = NWPS_CACHE_DIR, budget_s: float = SAMPLE_BUDGET_S,
            now: datetime | None = None) -> dict:
    """Write both tables; each part is tried on its own. Returns what each part did."""
    from .enrichment.geodata import load_buoy_snapshot

    if client is None:
        from .db_import import get_client
        client = get_client()
    deadline = time.monotonic() + budget_s
    captured_at = _iso(now or datetime.now(timezone.utc))
    run_id = os.environ.get("GITHUB_RUN_ID") or None
    spots = json.loads(Path(spots_path).read_text(encoding="utf-8"))
    stats: dict = {}
    try:
        stats["forecast_leads"] = capture_forecast_leads(client, ratings_path, spots,
                                                         captured_at, run_id)
    except Exception as e:  # noqa: BLE001  a failure here must not stop the buoy part
        stats["forecast_leads"] = {"failed": f"{type(e).__name__}: {e}"}
        warn(f"forecast_leads not written: {type(e).__name__}: {e}")
    try:
        buoys, missing = roster_buoys(spots, load_buoy_snapshot())
        if missing:
            log.info("accuracy capture: %d roster buoys have no snapshot position, not sampled: %s",
                     len(missing), ", ".join(missing))
        stats["model_at_buoys"] = capture_model_at_buoys(client, buoys, ww3_path, nwps_cache,
                                                         captured_at, run_id, deadline)
    except Exception as e:  # noqa: BLE001
        stats["model_at_buoys"] = {"failed": f"{type(e).__name__}: {e}"}
        warn(f"model_at_buoys not written: {type(e).__name__}: {e}")
    return stats


def main(argv=None) -> int:
    """Always 0: the capture is extra, and the run's upload is already done."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    try:
        stats = capture()
        print(json.dumps(stats, indent=1, sort_keys=True, default=str), flush=True)
    except Exception as e:  # noqa: BLE001
        warn(f"accuracy capture did not run: {type(e).__name__}: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
