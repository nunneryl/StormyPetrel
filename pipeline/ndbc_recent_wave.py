"""Which NDBC stations reported a wave height in the last few days. Writes NDBC_RECENT_WAVE_JSON.

    python -m pipeline.ndbc_recent_wave [--roster ROSTER.json] [--spots NAME,...] [--xml X]
        [--days 7] [--out OUT.json]

WHY. The buoy rule counts a station as a wave station from NDBC's latest_obs.txt, one moment's
observations. A live buoy that missed that one reading dropped out: sw1-raycast run 64's fresh
list left out 44084, which was reporting that day. geodata.load_ndbc_wave_stations now also counts
every station listed here.

WHAT. For each candidate station, NDBC's realtime2 file (about 45 days of observations, newest
first): the station is listed when any row within the last `days` has a wave height (WVHT not MM).
Candidates are the active stations (activestations.xml) within the buoy rule's cap of at least
one roster spot, since no other station can be assigned. Read-only: NDBC only.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

from .config import (DEFAULT_ENRICHED_OUTPUT, NDBC_RECENT_WAVE_DAYS, NDBC_RECENT_WAVE_JSON,
                     NDBC_STATIONS_XML)
from .geo import haversine_m

REALTIME2 = "https://www.ndbc.noaa.gov/data/realtime2/{id}.txt"


def parse_realtime2(text):
    """[(datetime UTC, wvht metres or None)] from a realtime2 file, in file order (newest first).
    Rows that do not parse are skipped."""
    lines = text.splitlines()
    header = next((ln.lstrip("#").split() for ln in lines if ln.startswith("#")), None)
    if not header or "WVHT" not in header:
        return []
    w = header.index("WVHT")
    out = []
    for ln in lines:
        if ln.startswith("#") or not ln.strip():
            continue
        p = ln.split()
        try:
            t = datetime(int(p[0]), int(p[1]), int(p[2]), int(p[3]), int(p[4]), tzinfo=timezone.utc)
            v = None if p[w] == "MM" else float(p[w])
        except (ValueError, IndexError):
            continue
        out.append((t, v))
    return out


def wave_summary(rows, now, days):
    """{"newest_row", "last_wave", "wave_rows_in_window", "recent"} for one station."""
    since = now - timedelta(days=days)
    waves = [t for t, v in rows if v is not None]
    in_window = [t for t in waves if since <= t <= now]
    iso = lambda t: t.isoformat() if t else None                                  # noqa: E731
    return {"newest_row": iso(max((t for t, _v in rows), default=None)),
            "last_wave": iso(max(waves, default=None)),
            "wave_rows_in_window": len(in_window), "recent": bool(in_window)}


def active_stations(xml_path):
    """[{id, lat, lng, name}] from activestations.xml, ids lower-case."""
    out = []
    for el in ET.parse(xml_path).getroot().iter("station"):
        try:
            out.append({"id": (el.get("id") or "").lower(), "lat": float(el.get("lat")),
                        "lng": float(el.get("lon")), "name": el.get("name") or ""})
        except (TypeError, ValueError):
            continue
    return [s for s in out if s["id"]]


def candidates(spots, stations, cap_km):
    """Ids of stations within cap_km(spot) of at least one spot, sorted."""
    keep = set()
    for sp in spots:
        cap = cap_km(sp)
        for st in stations:
            if st["id"] not in keep and haversine_m(sp["lat"], sp["lng"], st["lat"], st["lng"]) / 1000.0 <= cap:
                keep.add(st["id"])
    return sorted(keep)


def fetch_realtime2(station_id, timeout=20):
    """The station's realtime2 text, or None when NDBC has no such file (404). Other failures
    raise, so a network problem is never read as 'no waves'."""
    try:
        with urllib.request.urlopen(REALTIME2.format(id=station_id.upper()), timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        raise


def build(station_ids, now, days=NDBC_RECENT_WAVE_DAYS, fetch=None, pace_s=0.3):
    """{"generated_at", "days", "stations": [recent ids], "checked": {id: summary}}.
    A station NDBC has no realtime2 file for is checked with "no_file"; one whose fetch failed
    with "error" and is left out (never listed on a guess)."""
    fetch = fetch or fetch_realtime2
    checked, recent = {}, []
    for i, sid in enumerate(station_ids):
        if i and pace_s:
            time.sleep(pace_s)
        try:
            text = fetch(sid)
        except Exception as e:                                       # noqa: BLE001
            checked[sid] = {"error": f"{type(e).__name__}: {e}"[:200]}
            continue
        if text is None:
            checked[sid] = {"no_file": True, "recent": False}
            continue
        summ = wave_summary(parse_realtime2(text), now, days)
        checked[sid] = summ
        if summ["recent"]:
            recent.append(sid)
    return {"generated_at": now.isoformat(), "days": days, "stations": sorted(recent),
            "checked": checked}


def main(argv=None):
    from .enrichment.buoys import _regional_cap_km
    from .db_import import _slugify
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--roster", default=str(DEFAULT_ENRICHED_OUTPUT))
    p.add_argument("--spots", default="", help="comma-separated spot names or slugs (default: every valid spot)")
    p.add_argument("--xml", default=str(NDBC_STATIONS_XML))
    p.add_argument("--days", type=float, default=NDBC_RECENT_WAVE_DAYS)
    p.add_argument("--out", default=str(NDBC_RECENT_WAVE_JSON))
    a = p.parse_args(argv)
    with open(a.roster, encoding="utf-8") as f:
        roster = [s for s in json.load(f) if s.get("is_valid_surf_spot") is not False]
    if a.spots:
        want = {_slugify(t.strip()) for t in a.spots.split(",") if t.strip()}
        roster = [s for s in roster if _slugify(s["name"]) in want]
    ids = candidates(roster, active_stations(a.xml), _regional_cap_km)
    rep = build(ids, datetime.now(timezone.utc), a.days)
    with open(a.out, "w", encoding="utf-8") as f:
        json.dump(rep, f, indent=1)
    errors = sum(1 for v in rep["checked"].values() if "error" in v)
    print(f"ndbc_recent_wave: {len(ids)} candidate stations, {len(rep['stations'])} reported a wave "
          f"height in the last {a.days:g} days, {errors} could not be read -> {a.out}")


if __name__ == "__main__":
    main()
