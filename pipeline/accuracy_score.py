"""Score the forecasts against the buoys, one UTC day at a time, into accuracy_daily (migration 031).

    python -m pipeline.accuracy_score [--days 3] [--end YYYY-MM-DD]

Nightly (.github/workflows/accuracy-score.yml). Scores the --days complete UTC days before --end
(default: today, so yesterday and the days before it); re-scoring a day overwrites its rows, and
the days before yesterday are re-scored because a buoy reading can arrive late.

PAIRS. Each forecast hour is paired with the buoy reading nearest it, within 30 minutes, that has
the measure (buoy_observations):
  kind 'buoy'  model_at_buoys (migration 030): WAVEWATCH III and NWPS at the buoy itself. The main
               measure: like is compared with like.
  kind 'spot'  the spot forecast against the spot's nearest buoy, only where that buoy is within
               SPOT_BUOY_MAX_KM. Lead days 1-3 from forecast_leads; lead day 0 from forecasts,
               at the same local hours, when its NWPS cycle puts it within a day of the hour.
               Secondary: a spot is nearshore and its buoy is not.
Measures: hs (total height), swell_hs, tp (peak period), dp (direction; only where the buoy reads
at least DIR_MIN_HS m, and the error is the signed angle between them). WAVEWATCH III has no
total-swell field, so its swell_hs is the three swell partitions combined, sqrt(s1² + s2² + s3²).

TOTALS. One accuracy_daily row per (day, kind, source, region, point, lead day, measure) holds n
and the sums the report needs (see the migration). point '' is the region; a buoy id is that buoy
alone (kind 'buoy', hs only), for the report's best and worst buoys. A buoy's region is the region
of the spots that use it, the most common one.
"""
from __future__ import annotations

import argparse
import bisect
import json
import logging
import math
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .config import DEFAULT_ENRICHED_OUTPUT

log = logging.getLogger("pipeline.accuracy_score")

OBS_WINDOW = timedelta(minutes=30)
SPOT_BUOY_MAX_KM = 25.0
DIR_MIN_HS = 0.5
MEASURES = ("hs", "swell_hs", "tp", "dp")
PAGE = 1000
BATCH = 1000
SUMS = ("sum_f", "sum_o", "sum_ff", "sum_oo", "sum_fo", "sum_e", "sum_abs_e", "sum_ee")


# --------------------------------------------------------------------------- #
# Pairing                                                                      #
# --------------------------------------------------------------------------- #

def _ts(value) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).astimezone(timezone.utc)


def index_obs(rows: list[dict]) -> dict[str, tuple[list[datetime], list[dict]]]:
    """{buoy id: (sorted times, readings in that order)} from buoy_observations rows."""
    by: dict[str, list[tuple[datetime, dict]]] = defaultdict(list)
    for r in rows:
        by[str(r["buoy_id"])].append((_ts(r["observed_at"]), r))
    out = {}
    for bid, items in by.items():
        items.sort(key=lambda x: x[0])
        out[bid] = ([t for t, _ in items], [r for _, r in items])
    return out


def nearest_obs(index: dict, buoy_id: str, t: datetime, field: str):
    """The buoy's reading of *field* nearest *t* within OBS_WINDOW (the earlier on a tie), or None."""
    if buoy_id not in index:
        return None
    times, recs = index[buoy_id]
    lo = bisect.bisect_left(times, t - OBS_WINDOW)
    hi = bisect.bisect_right(times, t + OBS_WINDOW)
    best = None
    for j in range(lo, hi):
        if recs[j].get(field) is None:
            continue
        dt = abs(times[j] - t)
        if best is None or dt < best[0]:
            best = (dt, recs[j][field])
    return None if best is None else float(best[1])


def error(measure: str, f: float, o: float) -> float:
    """Forecast minus observed; for direction the signed angle from observed to forecast."""
    if measure == "dp":
        return ((f - o + 180.0) % 360.0) - 180.0
    return f - o


def ww3_swell_hs(row: dict):
    """The three WAVEWATCH III swell partitions combined: sqrt of the sum of their squares."""
    parts = [row.get(f"swell_{n}_hs") for n in (1, 2, 3)]
    parts = [float(p) for p in parts if p is not None]
    return math.sqrt(sum(p * p for p in parts)) if parts else None


def forecast_value(row: dict, measure: str, source: str):
    if measure == "swell_hs" and source == "ww3":
        return ww3_swell_hs(row)
    v = row.get(measure)
    return None if v is None else float(v)


class Totals:
    """Running sums per key; points counts the distinct points behind each key."""

    def __init__(self):
        self.sums: dict[tuple, dict] = {}
        self.points: dict[tuple, set] = defaultdict(set)

    def add(self, key: tuple, point: str, f: float, o: float, e: float) -> None:
        s = self.sums.setdefault(key, {"n": 0, **{k: 0.0 for k in SUMS}})
        s["n"] += 1
        s["sum_f"] += f
        s["sum_o"] += o
        s["sum_ff"] += f * f
        s["sum_oo"] += o * o
        s["sum_fo"] += f * o
        s["sum_e"] += e
        s["sum_abs_e"] += abs(e)
        s["sum_ee"] += e * e
        self.points[key].add(point)

    def rows(self, day: date, scored_at: str) -> list[dict]:
        out = []
        for key, s in sorted(self.sums.items()):
            kind, source, region, point, lead_day, measure = key
            out.append({"day": day.isoformat(), "kind": kind, "source": source, "region": region,
                        "point": point, "lead_day": lead_day, "measure": measure, "n": s["n"],
                        "points": len(self.points[key]),
                        **{k: round(s[k], 6) for k in SUMS}, "scored_at": scored_at})
        return out


def _pair(totals: Totals, kind, source, region, point_id, lead_day, row, obs, buoy_id, t,
          per_point: bool) -> None:
    for m in MEASURES:
        f = forecast_value(row, m, source)
        if f is None:
            continue
        o = nearest_obs(obs, buoy_id, t, m)
        if o is None:
            continue
        if m == "dp":
            ohs = nearest_obs(obs, buoy_id, t, "hs")
            if ohs is None or ohs < DIR_MIN_HS:
                continue
        e = error(m, f, o)
        totals.add((kind, source, region, "", lead_day, m), point_id, f, o, e)
        if per_point and m == "hs":
            totals.add((kind, source, region, point_id, lead_day, m), point_id, f, o, e)


def score_buoys(totals: Totals, model_rows: list[dict], obs: dict, buoy_region: dict) -> None:
    for r in model_rows:
        bid = str(r["buoy_id"])
        _pair(totals, "buoy", r["source"], buoy_region.get(bid, "Unknown"), bid,
              int(r["lead_day"]), r, obs, bid, _ts(r["valid_time"]), per_point=True)


def score_spots(totals: Totals, lead_rows: list[dict], spot_info: dict, obs: dict) -> None:
    """lead_rows: forecast_leads rows, and lead day 0 rows built from forecasts (lead_day set)."""
    for r in lead_rows:
        info = spot_info.get(r["spot_id"])
        if not info:
            continue
        _pair(totals, "spot", "spot", info["region"], str(r["spot_id"]), int(r["lead_day"]), r,
              obs, info["buoy_id"], _ts(r["valid_time"]), per_point=False)


def day0_rows(forecast_rows: list[dict], zones: dict, local_hours=None) -> list[dict]:
    """forecasts rows at the scoreboard's local hours whose NWPS cycle is within a day of the
    hour, as lead day 0 rows (forecasts keeps each hour's last, shortest-lead write)."""
    from zoneinfo import ZoneInfo

    from .accuracy_capture import LOCAL_HOURS, lead_day
    hours = local_hours or LOCAL_HOURS
    out = []
    for r in forecast_rows:
        tz, cyc = zones.get(r["spot_id"]), r.get("nwps_cycle")
        if not tz or not cyc:
            continue
        valid = _ts(r["valid_time"])
        local = valid.astimezone(ZoneInfo(tz))
        if local.minute or local.hour not in hours or lead_day(_ts(cyc), valid) != 0:
            continue
        out.append({**r, "lead_day": 0})
    return out


# --------------------------------------------------------------------------- #
# The roster                                                                   #
# --------------------------------------------------------------------------- #

def buoy_regions(spots: list[dict]) -> dict[str, str]:
    """Each buoy's region: the most common region of the spots naming it (nearest or fallback);
    on a tie, the first alphabetically."""
    votes: dict[str, Counter] = defaultdict(Counter)
    for s in spots:
        ids = [s.get("nearest_buoy_id")] + list(s.get("fallback_buoy_ids") or [])
        for bid in ids:
            if bid:
                votes[str(bid)][s.get("region_hint") or "Unknown"] += 1
    return {bid: sorted(c.items(), key=lambda kv: (-kv[1], kv[0]))[0][0] for bid, c in votes.items()}


def spot_infos(spots: list[dict], spot_ids: dict) -> tuple[dict, dict]:
    """({spot id: {region, buoy_id}} for spots whose buoy is within SPOT_BUOY_MAX_KM,
    {spot id: time zone} for every spot)."""
    info, zones = {}, {}
    for s in spots:
        sid = spot_ids.get(s["name"])
        if sid is None:
            continue
        zones[sid] = s.get("time_zone")
        km = s.get("nearest_buoy_dist_km")
        if s.get("nearest_buoy_id") and km is not None and float(km) <= SPOT_BUOY_MAX_KM:
            info[sid] = {"region": s.get("region_hint") or "Unknown", "buoy_id": str(s["nearest_buoy_id"])}
    return info, zones


# --------------------------------------------------------------------------- #
# Reading and writing                                                          #
# --------------------------------------------------------------------------- #

def _q(v) -> str:
    """A filter value: a UTC time as YYYY-MM-DDTHH:MM:SSZ (no '+' to escape), a date as is."""
    if isinstance(v, datetime):
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return v.isoformat()


def fetch_all(client, table: str, columns: str, time_col: str, start: datetime, end: datetime,
              order: tuple[str, ...], eq: dict | None = None) -> list[dict]:
    """Every row of *table* with start <= time_col < end, paged PAGE at a time in a stable order."""
    out: list[dict] = []
    lo = 0
    while True:
        q = client.table(table).select(columns).gte(time_col, _q(start)).lt(time_col, _q(end))
        for k, v in (eq or {}).items():
            q = q.eq(k, v)
        for col in order:
            q = q.order(col)
        rows = q.range(lo, lo + PAGE - 1).execute().data or []
        out.extend(rows)
        if len(rows) < PAGE:
            return out
        lo += PAGE


def score_day(client, day: date, spots: list[dict], spot_ids: dict, scored_at: str) -> list[dict]:
    """accuracy_daily rows for one UTC day."""
    start = datetime(day.year, day.month, day.day, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    obs = index_obs(fetch_all(
        client, "buoy_observations", "buoy_id,observed_at,hs,tp,dp,swell_hs", "observed_at",
        start - OBS_WINDOW, end + OBS_WINDOW, ("buoy_id", "observed_at")))
    totals = Totals()
    model = fetch_all(client, "model_at_buoys",
                      "buoy_id,valid_time,source,lead_day,hs,tp,dp,swell_hs,swell_1_hs,swell_2_hs,swell_3_hs",
                      "valid_time", start, end, ("buoy_id", "valid_time", "source", "lead_day"))
    score_buoys(totals, model, obs, buoy_regions(spots))
    info, zones = spot_infos(spots, spot_ids)
    leads = fetch_all(client, "forecast_leads", "spot_id,valid_time,lead_day,hs,tp,dp,swell_hs",
                      "valid_time", start, end, ("spot_id", "valid_time", "lead_day"))
    now = fetch_all(client, "forecasts", "spot_id,valid_time,hs,tp,dp,swell_hs,nwps_cycle",
                    "valid_time", start, end, ("spot_id", "valid_time"), eq={"source": "nwps"})
    score_spots(totals, leads + day0_rows(now, zones), info, obs)
    rows = totals.rows(day, scored_at)
    log.info("accuracy score %s: %d model rows, %d lead rows, %d buoy readings -> %d totals",
             day, len(model), len(leads), sum(len(v[0]) for v in obs.values()), len(rows))
    return rows


def write(client, rows: list[dict]) -> int:
    for i in range(0, len(rows), BATCH):
        client.table("accuracy_daily").upsert(
            rows[i:i + BATCH], on_conflict="day,kind,source,region,point,lead_day,measure").execute()
    return len(rows)


def days_to_score(end: date, days: int) -> list[date]:
    """The *days* complete UTC days before *end*, oldest first."""
    return [end - timedelta(days=k) for k in range(days, 0, -1)]


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=3)
    ap.add_argument("--end", type=date.fromisoformat, default=None,
                    help="score the days before this UTC date (default: today)")
    a = ap.parse_args(argv)
    from .db_import import _spot_id_map, get_client
    client = get_client()
    spots = json.loads(Path(DEFAULT_ENRICHED_OUTPUT).read_text(encoding="utf-8"))
    spot_ids = _spot_id_map(client)
    scored_at = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    end = a.end or datetime.now(timezone.utc).date()
    total = 0
    for d in days_to_score(end, a.days):
        total += write(client, score_day(client, d, spots, spot_ids, scored_at))
    log.info("accuracy score: %d rows written for %d days", total, a.days)
    return 0


if __name__ == "__main__":
    sys.exit(main())
