"""pipeline/tide_station.py: what NOAA CO-OPS answers about a spot's tide station, from a
stand-in session. Every expected value is worked by hand below, never read back from the module.

Geometry: a spot at (32.80, -79.75). On the module's 6371 km sphere one degree of arc is
111.195 km, and cos(32.80 deg) = 0.84057, so a station 0.05 deg of longitude east on the same
latitude is 0.05 x 111.195 x 0.84057 = 4.673 km away, and one 0.10 deg north is 11.12 km away.
"""
from __future__ import annotations

import pytest

from pipeline import tide_station as TS

NO_PRED = {"error": {"message": "No Predictions data was found. Please make sure the Datum "
                                "input is valid."}}
THROTTLE = {"error": {"message": "Too many requests"}}
HILO = [{"t": "2026-10-30 01:00", "v": "5.0", "type": "H"},
        {"t": "2026-10-30 07:10", "v": "0.5", "type": "L"},
        {"t": "2026-10-30 13:20", "v": "5.2", "type": "H"}]


def test_classify_reads_the_answer_as_the_tide_fetcher_does():
    assert TS.classify(200, {"predictions": HILO}) == {"result": "rows", "rows": 3}
    assert TS.classify(200, {"predictions": []}) == {"result": "rows", "rows": 0}
    got = TS.classify(200, NO_PRED)
    assert got["result"] == "no_predictions" and got["message"].startswith("No Predictions")
    assert TS.classify(200, THROTTLE) == {"result": "error", "message": "Too many requests"}
    assert TS.classify(503, None) == {"result": "http", "status": 503}
    assert TS.classify(404, {"predictions": HILO}) == {"result": "http", "status": 404}
    assert TS.classify(200, None) == {"result": "error", "message": "HTTP 200, not JSON"}


def test_mean_range_of_opposite_extremes():
    # 5.0 -> 0.5 is 4.5 ft, 0.5 -> 5.2 is 4.7 ft: mean 4.6
    assert TS.mean_range(HILO) == 4.6
    assert TS.mean_range(HILO[:1]) is None and TS.mean_range(None) is None
    # two highs in a row are not a range: 5.0 -> 4.0 (H, H) is skipped, 4.0 -> 1.0 is 3.0 ft
    assert TS.mean_range([{"v": "5.0", "type": "H"}, {"v": "4.0", "type": "H"},
                          {"v": "1.0", "type": "L"}]) == 3.0


def test_km_on_the_module_sphere():
    assert TS.km(32.80, -79.75, 32.80, -79.70) == pytest.approx(4.673, abs=0.002)
    assert TS.km(32.80, -79.75, 32.90, -79.75) == pytest.approx(11.12, abs=0.002)


SPOT = {"name": "Isle of Palms", "lat": 32.80, "lng": -79.75, "nearest_tide_station_id": "8665387",
        "nearest_tide_station_dist_km": 1.2}
LISTING = {
    "8665387": {"id": "8665387", "name": "Old gauge", "lat": 32.80, "lng": -79.751, "type": "S",
                "reference_id": "8665530"},
    "1000001": {"id": "1000001", "name": "Near east", "lat": 32.80, "lng": -79.70, "type": "S",
                "reference_id": "8665530"},
    "1000002": {"id": "1000002", "name": "North", "lat": 32.90, "lng": -79.75, "type": "R",
                "reference_id": None},
    "1000003": {"id": "1000003", "name": "Far", "lat": 33.20, "lng": -79.75, "type": "R",
                "reference_id": None},                          # 0.4 deg north, 44.5 km: out
}


def test_candidates_within_30_km_nearest_first_without_the_assigned_one():
    got = TS.candidates(SPOT, LISTING, "8665387", {"1000002": 3})
    assert [c["id"] for c in got] == ["1000001", "1000002"]
    assert got[0]["km"] == pytest.approx(4.67, abs=0.01) and got[0]["type"] == "S"
    assert got[1]["km"] == pytest.approx(11.12, abs=0.01) and got[1]["roster_spots"] == 3
    assert got[0]["roster_spots"] == 0 and got[0]["reference_id"] == "8665530"


class _Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


class _Session:
    """NOAA as a dict: the listing, one mdapi record, and predictions by station: 8665387 has
    none on any datum or interval; 1000001 answers hilo; 1000002 throttles."""

    def __init__(self):
        self.asked = []

    def get(self, url, timeout=None, params=None):
        self.asked.append((url, dict(params or {})))
        if url == TS.PREDICTION_LIST:
            return _Resp(200, {"stations": list(LISTING.values())})
        if url.endswith("/stations/8665387.json"):
            return _Resp(200, {"stations": [{"name": "Old gauge", "type": "S"}]})
        st = params["station"]
        if st == "8665387":
            return _Resp(200, NO_PRED)
        if st == "1000001":
            return _Resp(200, {"predictions": HILO})
        return _Resp(200, THROTTLE)


def test_run_asks_every_datum_for_the_assigned_station_and_hilo_for_the_candidates():
    from pipeline.config import NOAA_COOPS_DATUMS
    roster = [SPOT, dict(SPOT, name="Wild Dunes"), {"name": "Elsewhere", "lat": 0, "lng": 0,
                                                    "nearest_tide_station_id": "1000002"},
              dict(SPOT, name="Retired", is_valid_surf_spot=False)]      # not counted as a user
    s = _Session()
    rep = TS.run(s, roster, ["isle-of-palms", "wild-dunes", "nowhere"], begin="20261030",
                 pace_s=0)
    st = rep["stations"]["8665387"]
    assert st["in_prediction_list"] is True and st["roster_spots"] == 2
    assert st["list_entry"]["type"] == "S" and st["mdapi_record"]["stations"][0]["type"] == "S"
    assert set(st["answers"]) == {f"{iv} {d}" for iv in ("hilo", "h") for d in NOAA_COOPS_DATUMS}
    assert all(a["result"] == "no_predictions" for a in st["answers"].values())
    # the station is asked once though two spots share it; each candidate's hilo once
    preds = [p for u, p in s.asked if p.get("product") == "predictions"]
    assert sum(p["station"] == "8665387" for p in preds) == 2 * len(NOAA_COOPS_DATUMS)
    assert sum(p["station"] == "1000001" for p in preds) == 1
    assert all(p["begin_date"] == "20261030" and p["range"] == 72 for p in preds)
    c1, c2 = rep["spots"][0]["candidates"]
    assert c1["hilo"] == {"result": "rows", "rows": 3, "mean_range_ft": 4.6}
    assert c2["hilo"] == {"result": "error", "message": "Too many requests", "mean_range_ft": None}
    assert c2["roster_spots"] == 1
    assert rep["errors"] == {"spot nowhere": "not in the roster"}
    assert [sp["spot"] for sp in rep["spots"]] == ["Isle of Palms", "Wild Dunes"]


def test_the_report_names_the_station_and_its_neighbours(capsys):
    rep = TS.run(_Session(), [SPOT], ["isle-of-palms"], begin="20261030", pace_s=0)
    TS.print_report(rep)
    out = capsys.readouterr().out
    assert ("station 8665387: in the prediction list True (Old gauge, type S, reference 8665530); "
            "assigned to 1 roster spot(s)") in out
    assert "predictions hilo MLLW: {'result': 'no_predictions'" in out
    assert "1000001 Near east type S ref 8665530  4.67 km  in use by 0 spot(s)  3 hilo rows, " \
           "mean range 4.6 ft" in out


def test_the_default_spots_are_the_five_on_8665387():
    assert TS.DEFAULT_SPOTS == ("berts-bar", "dewees-island", "isle-of-palms", "wild-dunes",
                                "windjammer")
    import json
    import os
    from pipeline.db_import import _slugify
    root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    with open(os.path.join(root, "pipeline", "spots_enriched.json")) as f:
        roster = {_slugify(s["name"]): s for s in json.load(f)}
    assert {str(roster[s]["nearest_tide_station_id"]) for s in TS.DEFAULT_SPOTS} == {"8665387"}
