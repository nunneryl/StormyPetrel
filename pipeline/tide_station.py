"""Tide stations from the runner: what NOAA CO-OPS answers about a spot's station, and the
working stations near it. READ-ONLY (sw1-raycast mode tide-station, run 63 onward).

It reads the roster and NOAA CO-OPS and writes nothing but its own report (--out). No Supabase.

    python -m pipeline.tide_station [--spots SLUG,...] [--roster ROSTER.json] [--out OUT.json]

THE QUESTION. Since at least 2026-10-14 every full pipeline run has skipped station 8665387 as
known-bad (pipeline/forecast/tides.py: every datum answered "No Predictions data was found"), so
Berts Bar, Dewees Island, Isle of Palms, Wild Dunes and Windjammer publish no tide. Is the station
retired, a station NOAA publishes no predictions for, or one that fails only from GitHub's
runners, as TWC0965 did for Kalaloch Beach (pipeline/data/spot_tide_stations.json)? And which
working station is nearest each spot?

FOR EACH SPOT in --spots (default the five above):
  assigned    its nearest_tide_station_id: whether NOAA's prediction-station list
              (mdapi stations.json?type=tidepredictions) carries it, and that entry (name, type R
              reference or S subordinate, reference station); the station's own mdapi record; and
              the pipeline's own request, product=predictions for intervals hilo and h on every
              NOAA_COOPS_DATUMS datum over the next 72 h, each answer classified as tides.py
              classifies it: rows, a genuine "no predictions", another CO-OPS error, or an HTTP or
              transport failure.
  candidates  every station in the prediction list within NEAR_KM of the spot, nearest first:
              its id, name, type, reference station, distance, the hilo request (MLLW, 72 h) from
              this runner with its row count and mean range, and how many roster spots already use
              it (a station in use is fetched every run, so it works from the runner).
"""
from __future__ import annotations

import argparse
import json
import math
import time
from datetime import datetime, timezone

MDAPI = "https://api.tidesandcurrents.noaa.gov/mdapi/prod/webapi"
PREDICTION_LIST = MDAPI + "/stations.json?type=tidepredictions"
NEAR_KM = 30.0
PROBE_HOURS = 72
DEFAULT_SPOTS = ("berts-bar", "dewees-island", "isle-of-palms", "wild-dunes", "windjammer")


def km(lat1, lng1, lat2, lng2):
    p = math.pi / 180.0
    a = (math.sin((lat2 - lat1) * p / 2) ** 2
         + math.cos(lat1 * p) * math.cos(lat2 * p) * math.sin((lng2 - lng1) * p / 2) ** 2)
    return 12742.0 * math.asin(math.sqrt(a))


def classify(status, body):
    """One CO-OPS predictions answer, classified as forecast/tides.py classifies it:
    {"result": "rows", "rows": n} | {"result": "no_predictions", "message": ...} |
    {"result": "error", "message": ...} | {"result": "http", "status": status}.
    *body* is the decoded JSON, or None when the response was not JSON."""
    from .forecast.tides import _coops_error_message, _is_genuine_no_predictions
    if status != 200:
        return {"result": "http", "status": status}
    if not isinstance(body, dict):
        return {"result": "error", "message": "HTTP 200, not JSON"}
    if "error" in body:
        msg = _coops_error_message(body)[:200]
        return {"result": "no_predictions" if _is_genuine_no_predictions(body) else "error",
                "message": msg}
    return {"result": "rows", "rows": len(body.get("predictions") or [])}


def mean_range(hilo):
    """Mean height difference between consecutive opposite extremes of a hilo list, or None."""
    pts = [(str(p.get("type", ""))[:1], float(p["v"])) for p in hilo or []
           if p.get("v") not in (None, "")]
    diffs = [abs(b[1] - a[1]) for a, b in zip(pts, pts[1:]) if a[0] != b[0]]
    return round(sum(diffs) / len(diffs), 2) if diffs else None


def request(session, station, interval, datum, begin, hours=PROBE_HOURS, pace_s=1.0):
    """The pipeline's predictions request for one station, interval and datum: (classified, body)."""
    from .config import NOAA_COOPS_ENDPOINT
    time.sleep(pace_s)
    try:
        r = session.get(NOAA_COOPS_ENDPOINT, timeout=30, params={
            "station": station, "product": "predictions", "datum": datum, "units": "english",
            "time_zone": "lst_ldt", "interval": interval, "begin_date": begin, "range": hours,
            "format": "json"})
    except Exception as e:                                      # noqa: BLE001 - reported
        return {"result": "transport", "message": f"{type(e).__name__}: {e}"[:200]}, None
    try:
        body = r.json()
    except ValueError:
        body = None
    return classify(r.status_code, body), body


def candidates(spot, listing, assigned, used, near_km=NEAR_KM):
    """Every prediction station within *near_km* of *spot* except *assigned*, nearest first:
    {id, name, type, reference_id, km, roster_spots}. *listing*: {id: list entry};
    *used*: {station id: roster spots assigned to it}."""
    out = []
    for sid, s in listing.items():
        if sid == assigned or s.get("lat") is None or s.get("lng") is None:
            continue
        d = km(float(spot["lat"]), float(spot["lng"]), float(s["lat"]), float(s["lng"]))
        if d <= near_km:
            out.append({"id": sid, "name": s.get("name"), "type": s.get("type"),
                        "reference_id": s.get("reference_id"), "km": round(d, 2),
                        "roster_spots": used.get(sid, 0)})
    return sorted(out, key=lambda c: (c["km"], c["id"]))


def run(session, roster, slugs, begin=None, pace_s=1.0):
    from .config import NOAA_COOPS_DATUMS
    from .db_import import _slugify
    begin = begin or datetime.now(timezone.utc).strftime("%Y%m%d")
    errors = {}
    try:
        listing = {str(s["id"]): s for s in session.get(PREDICTION_LIST, timeout=60).json()["stations"]}
    except Exception as e:                                      # noqa: BLE001 - reported
        listing, errors["prediction list"] = {}, f"{type(e).__name__}: {e}"[:200]
    by_slug = {_slugify(s["name"]): s for s in roster if s.get("name")}
    used = {}
    for s in roster:
        if s.get("is_valid_surf_spot") is not False and s.get("nearest_tide_station_id"):
            sid = str(s["nearest_tide_station_id"])
            used[sid] = used.get(sid, 0) + 1
    stations, spots, probed = {}, [], {}

    def hilo(sid):
        if sid not in probed:
            res, body = request(session, sid, "hilo", "MLLW", begin, pace_s=pace_s)
            res["mean_range_ft"] = mean_range((body or {}).get("predictions")) if res["result"] == "rows" else None
            probed[sid] = res
        return probed[sid]

    for slug in slugs:
        sp = by_slug.get(slug)
        if sp is None:
            errors[f"spot {slug}"] = "not in the roster"
            continue
        sid = str(sp.get("nearest_tide_station_id") or "")
        if sid and sid not in stations:
            try:
                record = session.get(f"{MDAPI}/stations/{sid}.json", timeout=30,
                                     params={"expand": "details,tidepredoffsets"}).json()
            except Exception as e:                              # noqa: BLE001 - reported
                record = {"error": f"{type(e).__name__}: {e}"[:200]}
            answers = {f"{iv} {dt}": request(session, sid, iv, dt, begin, pace_s=pace_s)[0]
                       for iv in ("hilo", "h") for dt in NOAA_COOPS_DATUMS}
            stations[sid] = {"in_prediction_list": sid in listing, "list_entry": listing.get(sid),
                             "mdapi_record": record, "answers": answers,
                             "roster_spots": used.get(sid, 0)}
        near = candidates(sp, listing, sid, used)
        for c in near:
            c["hilo"] = hilo(c["id"])
        spots.append({"spot": sp["name"], "slug": slug, "lat": sp["lat"], "lng": sp["lng"],
                      "station": sid or None, "station_km": sp.get("nearest_tide_station_dist_km"),
                      "candidates": near})
    return {"begin": begin, "probe_hours": PROBE_HOURS, "near_km": NEAR_KM,
            "prediction_list_size": len(listing), "stations": stations, "spots": spots,
            "errors": errors}


def print_report(rep):
    print(f"\nTide stations from the runner, predictions from {rep['begin']} ({rep['probe_hours']} h); "
          f"NOAA's prediction list holds {rep['prediction_list_size']} stations")
    for sid, st in rep["stations"].items():
        e = st["list_entry"] or {}
        print(f"\n  station {sid}: in the prediction list {st['in_prediction_list']}"
              + (f" ({e.get('name')}, type {e.get('type')}, reference {e.get('reference_id')})"
                 if e else "") + f"; assigned to {st['roster_spots']} roster spot(s)")
        rec = st["mdapi_record"]
        meta = (rec.get("stations") or [{}])[0] if isinstance(rec, dict) else {}
        keys = ("name", "state", "type", "reference_id", "tidal", "greatlakes", "affiliations",
                "timezonecorr")
        print("    mdapi record: " + (json.dumps({k: meta.get(k) for k in keys if k in meta})
                                       if meta else json.dumps(rec)[:300]))
        for k, a in st["answers"].items():
            print(f"    predictions {k}: {a}")
    for sp in rep["spots"]:
        print(f"\n  {sp['spot']} ({sp['lat']}, {sp['lng']}): station {sp['station']} "
              f"{sp['station_km']} km; prediction stations within {rep['near_km']:.0f} km:")
        for c in sp["candidates"]:
            h = c["hilo"]
            got = (f"{h['rows']} hilo rows, mean range {h.get('mean_range_ft')} ft"
                   if h["result"] == "rows" else f"{h['result']} {h.get('message') or h.get('status') or ''}")
            print(f"    {c['id']} {c['name']} type {c['type']} ref {c['reference_id']}  "
                  f"{c['km']} km  in use by {c['roster_spots']} spot(s)  {got}")
    if rep["errors"]:
        print(f"\n  errors: {rep['errors']}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--roster", default="pipeline/spots_enriched.json")
    p.add_argument("--spots", default="", help="comma-separated slugs (default: the five on 8665387)")
    p.add_argument("--out", default=None)
    a = p.parse_args(argv)
    from .http import session
    with open(a.roster) as fh:
        roster = json.load(fh)
    slugs = [s.strip() for s in a.spots.split(",") if s.strip()] or list(DEFAULT_SPOTS)
    rep = run(session(), roster, slugs)
    rep["generated_at"] = datetime.now(timezone.utc).isoformat()
    print_report(rep)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(rep, fh, indent=1, default=str)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
