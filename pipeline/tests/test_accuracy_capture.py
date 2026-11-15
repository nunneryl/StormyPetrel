"""pipeline/accuracy_capture.py: the scoreboard's forecasts at lead days 1-3 and the models at the
buoys (migration 030), captured after the database push, and never able to stop the upload.

Every expected value is worked by hand in the comments: times in UTC and in each spot's zone
(Los Angeles is UTC-8 in November, Honolulu UTC-10, New York UTC-4 in July), lead hours as the
difference, lead day as its floor over 24, and distances as degrees x 111.195 km (pi/180 x 6371).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from pipeline import accuracy_capture as AC
from pipeline.tests.test_ci_workflow import yaml_code
from pipeline.tests.test_column_preflight import WORKFLOW, steps

ROOT = Path(__file__).resolve().parents[2]


def _t(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


C = _t("2026-11-13T12:00:00Z")      # the cycle most cases issue from


# --------------------------------------------------------------------------- #
# Lead day                                                                     #
# --------------------------------------------------------------------------- #

@pytest.mark.parametrize("valid, day", [
    ("2026-11-13T11:00:00Z", None),   # an hour before the issue
    ("2026-11-14T11:00:00Z", 0),      # 23 h
    ("2026-11-14T12:00:00Z", 1),      # 24 h
    ("2026-11-16T11:00:00Z", 2),      # 71 h
    ("2026-11-16T12:00:00Z", 3),      # 72 h
    ("2026-11-17T11:00:00Z", 3),      # 95 h
    ("2026-11-17T12:00:00Z", 4),      # 96 h
])
def test_lead_day(valid, day):
    assert AC.lead_day(C, _t(valid)) == day


# --------------------------------------------------------------------------- #
# forecast_leads rows                                                          #
# --------------------------------------------------------------------------- #

def _h(vt, cyc="2026-11-13T12:00:00Z", **kw):
    return {"valid_time": vt, "nwps_cycle": cyc, **kw}


RATINGS = {
    "LA": [
        _h("2026-11-14T14:00:00Z", hs=1.5, face_ft=4.2, face_ft_raw=6.1, stars=3.0),  # 06 PST, 26 h: day 1
        _h("2026-11-14T15:00:00Z"),          # 07 PST: not a kept hour
        _h("2026-11-14T02:00:00Z"),          # 18 PST on the 13th, 14 h: day 0, forecasts has it
        _h("2026-11-16T23:00:00Z"),          # 15 PST, 83 h: day 3
        _h("2026-11-17T02:00:00Z"),          # 18 PST on the 16th, 86 h: day 3
        _h("2026-11-17T14:00:00Z"),          # 06 PST, 98 h: day 4, not kept
        _h("2026-11-15T20:00:00Z", None),    # 12 PST, no cycle: lead unknown
        _h("2026-11-15T20:30:00Z"),          # not on the hour
    ],
    "HI": [
        _h("2026-11-14T16:00:00Z"),          # 06 HST, 28 h: day 1
        _h("2026-11-14T14:00:00Z"),          # 04 HST: not a kept hour
    ],
    "NY": [
        _h("2026-07-01T10:00:00Z", "2026-06-30T06:00:00Z"),   # 06 EDT, 28 h: day 1 (05 if EST)
        _h("2026-07-01T11:00:00Z", "2026-06-30T06:00:00Z"),   # 07 EDT
    ],
    "Ghost": [_h("2026-11-14T14:00:00Z")],                    # not in the spots table
    "NoZone": [_h("2026-11-14T14:00:00Z")],
}
IDS = {"LA": 1, "HI": 2, "NY": 3, "NoZone": 4}
ZONES = {"LA": "America/Los_Angeles", "HI": "Pacific/Honolulu", "NY": "America/New_York",
         "Ghost": "America/New_York", "NoZone": None}


def test_spot_lead_rows_keep_the_five_local_hours_at_lead_days_1_to_3():
    rows, skipped = AC.spot_lead_rows(RATINGS, IDS, ZONES, "2026-11-13T20:00:00Z", "77")
    assert [(r["spot_id"], r["valid_time"], r["lead_day"], r["lead_hours"]) for r in rows] == [
        (1, "2026-11-14T14:00:00Z", 1, 26),
        (1, "2026-11-16T23:00:00Z", 3, 83),
        (1, "2026-11-17T02:00:00Z", 3, 86),
        (2, "2026-11-14T16:00:00Z", 1, 28),
        (3, "2026-07-01T10:00:00Z", 1, 28),
    ]
    assert skipped == {"no spot id": 1, "no time zone": 1, "no cycle": 1}


def test_a_spot_lead_row_carries_the_forecast_as_issued():
    rows, _ = AC.spot_lead_rows({"LA": RATINGS["LA"][:1]}, IDS, ZONES, "2026-11-13T20:00:00Z", "77")
    assert rows == [{
        "spot_id": 1, "valid_time": "2026-11-14T14:00:00Z", "lead_day": 1,
        "issued_at": "2026-11-13T12:00:00Z", "lead_hours": 26,
        "hs": 1.5, "tp": None, "dp": None, "swell_hs": None, "swell_tp": None, "swell_dp": None,
        "wind_speed": None, "wind_dir": None, "swell_source": None, "chop_ratio": None,
        "face_ft": 4.2, "face_ft_raw": 6.1, "face_correction_version": None, "stars": 3.0,
        "captured_at": "2026-11-13T20:00:00Z", "run_id": "77"}]


# --------------------------------------------------------------------------- #
# model_at_buoys rows                                                          #
# --------------------------------------------------------------------------- #

WW3_ENTRIES = [
    {"valid_time": "2026-11-13T12:00:00Z", "total_hs": 2.1, "total_tp": 12.0, "total_dp": 280.0,
     "swell_1_hs": 1.8, "swell_2_dp": 200.0, "wind_wave_tp": 4.0},      # 0 h: day 0
    {"valid_time": "2026-11-13T13:00:00Z", "total_hs": 2.0},            # not 3-hourly
    {"valid_time": "2026-11-13T15:00:00Z", "total_hs": 1.9},            # 3 h: day 0
    {"valid_time": "2026-11-17T09:00:00Z", "total_hs": 1.0},            # 93 h: day 3
    {"valid_time": "2026-11-17T12:00:00Z", "total_hs": 0.9},            # 96 h: day 4
    {"valid_time": "2026-11-14T00:00:00Z"},                             # no model value
]


def test_buoy_lead_rows_every_3_hours_at_lead_days_0_to_3():
    rows = AC.buoy_lead_rows("46221", "ww3", C, WW3_ENTRIES, {"lat": 33.75, "lng": -118.5, "km": 9.4},
                             None, "2026-11-13T20:00:00Z", None)
    assert [(r["valid_time"], r["lead_day"], r["lead_hours"], r["hs"]) for r in rows] == [
        ("2026-11-13T12:00:00Z", 0, 0, 2.1),
        ("2026-11-13T15:00:00Z", 0, 3, 1.9),
        ("2026-11-17T09:00:00Z", 3, 93, 1.0),
    ]
    first = rows[0]
    assert (first["tp"], first["dp"], first["swell_1_hs"], first["swell_2_dp"],
            first["wind_wave_tp"], first["swell_hs"], first["wind_speed"]) == \
        (12.0, 280.0, 1.8, 200.0, 4.0, None, None)
    assert (first["source"], first["cell_lat"], first["cell_lng"], first["cell_km"], first["wfo"]) == \
        ("ww3", 33.75, -118.5, 9.4, None)


def test_nwps_fields_map_to_the_columns():
    rows = AC.buoy_lead_rows("46221", "nwps", C, [{
        "valid_time": "2026-11-13T18:00:00Z", "hs": 1.2, "tp": 13.0, "dp": 270.0, "swell_hs": 1.1,
        "windsea_hs": 0.3, "wind_speed": 5.0, "wind_dir": 310.0}], None, "lox",
        "2026-11-13T20:00:00Z", "77")
    r = rows[0]
    assert (r["hs"], r["tp"], r["dp"], r["swell_hs"], r["wind_wave_hs"], r["wind_speed"],
            r["wind_dir"], r["wfo"], r["lead_day"], r["lead_hours"]) == \
        (1.2, 13.0, 270.0, 1.1, 0.3, 5.0, 310.0, "lox", 0, 6)
    assert set(r) == {"buoy_id", "valid_time", "source", "lead_day", "issued_at", "lead_hours",
                      "wfo", "cell_lat", "cell_lng", "cell_km", *AC.MODEL_COLUMNS,
                      "captured_at", "run_id"}


def test_every_column_the_rows_write_is_in_the_migration():
    sql = (ROOT / "pipeline" / "migrations" / "030_accuracy_capture.sql").read_text()
    spot_row, _ = AC.spot_lead_rows({"LA": RATINGS["LA"][:1]}, IDS, ZONES, "x", None)
    buoy_row = AC.buoy_lead_rows("b", "nwps", C, [{"valid_time": "2026-11-13T18:00:00Z", "hs": 1}],
                                 None, None, "x", None)
    for table, row in (("forecast_leads", spot_row[0]), ("model_at_buoys", buoy_row[0])):
        body = sql.split(f"CREATE TABLE IF NOT EXISTS {table} (")[1].split(");")[0]
        cols = [ln.split()[0] for ln in body.splitlines() if ln.strip() and not ln.strip().startswith("PRIMARY")]
        assert sorted(row) == sorted(cols), table


def test_the_ww3_cycle_is_the_earliest_hour_in_ww3_json():
    assert AC.ww3_cycle({"a": [{"valid_time": "2026-11-13T15:00:00Z"}, {"valid_time": "2026-11-13T12:00:00Z"}],
                         "b": [{"valid_time": "2026-11-13T18:00:00Z"}]}) == C
    assert AC.ww3_cycle({}) is None


def test_newest_nwps_file_per_office(tmp_path):
    for name, body in [("mfl_20261113_06.grib2", b"x"), ("mfl_20261113_12.grib2", b"x"),
                       ("lox_20261112_18.grib2", b"x"), ("lox_20261113_00.grib2", b""),
                       ("notes.txt", b"x"), ("MFL_20261114_00.grib2", b"x")]:
        (tmp_path / name).write_bytes(body)
    got = {w: (p.name, c) for w, (p, c) in AC.newest_nwps_files(tmp_path).items()}
    assert got == {"mfl": ("mfl_20261113_12.grib2", C),
                   "lox": ("lox_20261112_18.grib2", _t("2026-11-12T18:00:00Z"))}


def test_roster_buoys_are_the_fetched_ones_with_a_snapshot_position():
    spots = [{"nearest_buoy_id": "46221", "fallback_buoy_ids": ["46222", "42033"]},
             {"nearest_buoy_id": "46222", "fallback_buoy_ids": []}]
    snap = {"46221": {"lat": 33.86, "lng": -118.63}, "46222": {"lat": 33.62, "lng": -118.32}}
    assert AC.roster_buoys(spots, snap) == (
        [("46221", 33.86, -118.63), ("46222", 33.62, -118.32)], ["42033"])


def test_dedupe_keeps_the_newest_issue_whatever_the_order():
    old = {"k": 1, "issued_at": "2026-11-13T06:00:00Z", "v": "old"}
    new = {"k": 1, "issued_at": "2026-11-13T12:00:00Z", "v": "new"}
    assert AC.dedupe([old, new], ("k",)) == [new]
    assert AC.dedupe([new, old], ("k",)) == [new]


# --------------------------------------------------------------------------- #
# Sampling, on synthetic grids                                                 #
# --------------------------------------------------------------------------- #

def _nwps_ds(lats, lngs, hs, start="2026-11-13T12:00:00", steps=4):
    """An NWPS-like dataset: (step, latitude, longitude) hourly, 0-360 longitudes."""
    import numpy as np
    import pandas as pd
    import xarray as xr
    vt = pd.date_range(start, periods=steps, freq="h")
    arr = np.broadcast_to(np.asarray(hs, dtype=float), (steps, len(lats), len(lngs))).copy()
    return xr.Dataset({"swh": (("step", "latitude", "longitude"), arr)},
                      coords={"step": np.arange(steps), "latitude": lats, "longitude": lngs,
                              "valid_time": ("step", vt.values)})


def test_sample_nwps_takes_the_wet_cell_inside_the_grid_and_the_nearer_office(monkeypatch, tmp_path):
    from pipeline.forecast import nwps as N
    nan = float("nan")
    # Office A: cells at 33.0/33.1/33.2 N by 241.0/241.1/241.2 E (118.9-119.0 W); 33.2 N 241.2 E dry.
    a = _nwps_ds([33.0, 33.1, 33.2], [241.0, 241.1, 241.2],
                 [[1.0, 1.0, 1.0], [1.0, 1.5, 1.0], [1.0, 1.0, nan]])
    # Office B: a finer grid whose cell 33.12 N 241.1 E sits exactly on buoy "near".
    b = _nwps_ds([33.10, 33.12, 33.14], [241.08, 241.10, 241.12], 2.0)
    files = {"aaa": (tmp_path / "aaa", C), "bbb": (tmp_path / "bbb", _t("2026-11-13T06:00:00Z"))}
    monkeypatch.setattr(N, "_open_grib_datasets", lambda p: [{"aaa": a, "bbb": b}[Path(p).name]])
    buoys = [("centre", 33.1, -118.9),     # A's cell exactly: 0 km, hs 1.5
             ("near", 33.12, -118.9),      # A: 33.1 N, 0.02 deg = 2.22 km; B: exact, 0 km
             ("dry", 33.2, -118.8),        # A's dry cell: no value anywhere
             ("outside", 34.0, -118.9)]    # in neither grid
    found, not_read = AC.sample_nwps(buoys, files, time.monotonic() + 60)
    assert not_read == []
    assert sorted(found) == ["centre", "near"]
    wfo, cyc, cell, entries = found["centre"]
    assert (wfo, cyc, cell) == ("aaa", C, {"lat": 33.1, "lng": -118.9, "km": 0.0})
    assert [e["hs"] for e in entries] == [1.5, 1.5, 1.5, 1.5]
    wfo, cyc, cell, entries = found["near"]
    assert (wfo, cell["km"], entries[0]["hs"]) == ("bbb", 0.0, 2.0)


def test_sample_nwps_stops_at_the_time_budget(monkeypatch, tmp_path):
    from pipeline.forecast import nwps as N
    monkeypatch.setattr(N, "_open_grib_datasets", lambda p: pytest.fail("opened past the budget"))
    files = {"aaa": (tmp_path / "aaa", C), "bbb": (tmp_path / "bbb", C)}
    assert AC.sample_nwps([("x", 33.1, -118.9)], files, time.monotonic() - 1) == ({}, ["aaa", "bbb"])


def _ww3_ds(vt, hs):
    """A gfswave-like step: swh/perpw on (latitude, longitude), 0-360 longitudes, one valid time."""
    import numpy as np
    import xarray as xr
    lats, lngs = [33.0, 33.25, 33.5], [240.75, 241.0, 241.25]
    arr = np.full((3, 3), hs, dtype=float)
    arr[1, 1] = np.nan                       # 33.25 N 241.0 E is land
    return xr.Dataset({"swh": (("latitude", "longitude"), arr),
                       "perpw": (("latitude", "longitude"), np.full((3, 3), 11.0))},
                      coords={"latitude": lats, "longitude": lngs,
                              "valid_time": np.datetime64(vt.replace("Z", ""))})


def test_sample_ww3_reads_the_cycles_steps_to_lead_day_3(monkeypatch, tmp_path):
    from pipeline.forecast import ww3 as W
    have = {0: _ww3_ds("2026-11-13T12:00:00Z", 2.0), 3: _ww3_ds("2026-11-13T15:00:00Z", 2.4)}
    for fh in have:
        (tmp_path / f"f{fh:03d}").write_bytes(b"x")
    monkeypatch.setattr(W, "_step_cache_path", lambda d, h, fh: tmp_path / f"f{fh:03d}")
    monkeypatch.setattr(W, "_open_grib_datasets", lambda p: [have[int(Path(p).name[1:])]])
    # "sea" sits on 33.5 N 241.25 E; "coast" on the land cell, so it takes a wet neighbour.
    series, cells, not_read = AC.sample_ww3([("sea", 33.5, -118.75), ("coast", 33.25, -119.0)],
                                            C, time.monotonic() + 60)
    assert not_read == list(range(6, 96, 3))           # f006..f093 absent; f096 is lead day 4
    assert [(e["valid_time"], e["total_hs"], e["total_tp"]) for e in series["sea"]] == [
        ("2026-11-13T12:00:00Z", 2.0, 11.0), ("2026-11-13T15:00:00Z", 2.4, 11.0)]
    assert cells["sea"] == {"lat": 33.5, "lng": -118.75, "km": 0.0}
    assert cells["coast"]["km"] > 0 and series["coast"][0]["total_hs"] == 2.0


# --------------------------------------------------------------------------- #
# Never stops the upload                                                       #
# --------------------------------------------------------------------------- #

class _Res:
    def __init__(self, data):
        self.data = data


class FakeClient:
    """Records upserts; raises for the tables in *broken*; _spot_id_map reads 'spots'."""

    def __init__(self, broken=()):
        self.broken, self.written, self._t = set(broken), {}, None

    def table(self, name):
        self._t = name
        return self

    def select(self, *_a):
        return self

    def range(self, *_a):
        return self

    def upsert(self, rows, on_conflict=None):
        if self._t in self.broken:
            raise RuntimeError(f"relation {self._t} does not exist")
        self.written.setdefault(self._t, []).extend(rows)
        self._rows = rows
        return self

    def execute(self):
        if self._t == "spots":
            return _Res([{"id": 1, "name": "LA"}])
        return _Res(list(self._rows))


@pytest.fixture()
def world(monkeypatch, tmp_path):
    ratings = tmp_path / "ratings.json"
    ratings.write_text(json.dumps({"LA": RATINGS["LA"]}))
    spots = tmp_path / "spots.json"
    spots.write_text(json.dumps([{"name": "LA", "time_zone": "America/Los_Angeles",
                                  "nearest_buoy_id": "46221"}]))
    ww3 = tmp_path / "ww3.json"
    ww3.write_text(json.dumps({"LA": [{"valid_time": "2026-11-13T12:00:00Z"}]}))
    monkeypatch.setattr("pipeline.enrichment.geodata.load_buoy_snapshot",
                        lambda: {"46221": {"lat": 33.86, "lng": -118.63}})
    monkeypatch.setattr(AC, "sample_ww3", lambda b, c, d: ({"46221": WW3_ENTRIES}, {}, []))
    monkeypatch.setattr(AC, "sample_nwps", lambda b, f, d: ({"46221": (
        "lox", C, {"lat": 33.86, "lng": -118.63, "km": 0.4},
        [{"valid_time": "2026-11-13T18:00:00Z", "hs": 1.2}])}, []))

    def run(client):
        return AC.capture(client, ratings_path=ratings, spots_path=spots, ww3_path=ww3,
                          nwps_cache=tmp_path, now=_t("2026-11-13T20:00:00Z"))
    return run


def test_both_tables_written(world):
    client = FakeClient()
    stats = world(client)
    assert len(client.written["forecast_leads"]) == 3        # the three kept LA hours above
    assert [(r["source"], r["valid_time"]) for r in client.written["model_at_buoys"]] == [
        ("ww3", "2026-11-13T12:00:00Z"), ("ww3", "2026-11-13T15:00:00Z"),
        ("ww3", "2026-11-17T09:00:00Z"), ("nwps", "2026-11-13T18:00:00Z")]
    assert stats["forecast_leads"]["by_lead_day"] == {1: 1, 3: 2}


def test_a_broken_forecast_leads_does_not_stop_the_buoys(world, capsys):
    client = FakeClient(broken={"forecast_leads"})
    stats = world(client)
    assert stats["forecast_leads"]["failed"] == "RuntimeError: relation forecast_leads does not exist"
    assert len(client.written["model_at_buoys"]) == 4
    assert "::warning title=accuracy capture::forecast_leads not written" in capsys.readouterr().out


def test_a_broken_model_sampler_does_not_stop_the_rest(world, monkeypatch):
    def boom(*_a):
        raise MemoryError("cfgrib")
    monkeypatch.setattr(AC, "sample_ww3", boom)
    client = FakeClient()
    stats = world(client)
    assert stats["model_at_buoys"]["ww3"] == {"failed": "MemoryError: cfgrib"}
    assert len(client.written["forecast_leads"]) == 3
    assert [r["source"] for r in client.written["model_at_buoys"]] == ["nwps"]


def test_main_exits_0_whatever_happens(monkeypatch, capsys):
    def boom(*_a, **_k):
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in the environment")
    monkeypatch.setattr(AC, "capture", boom)
    assert AC.main() == 0
    assert capsys.readouterr().out.startswith(
        "::warning title=accuracy capture::accuracy capture did not run: RuntimeError: SUPABASE_URL")


def test_the_module_run_as_the_workflow_runs_it_exits_0_without_a_database():
    env = {k: v for k, v in os.environ.items() if not k.startswith("SUPABASE")}
    p = subprocess.run([sys.executable, "-m", "pipeline.accuracy_capture"], cwd=ROOT, env=env,
                       capture_output=True, text=True, timeout=120)
    assert p.returncode == 0, p.stderr
    assert "::warning title=accuracy capture::accuracy capture did not run" in p.stdout


def test_the_step_runs_last_after_the_upload_and_can_never_fail_the_run():
    text = yaml_code(WORKFLOW.read_text(encoding="utf-8"))
    ss = steps(text, "full-pipeline")
    runs = [s.get("run", "") for s in ss]
    cap = runs.index("python -m pipeline.accuracy_capture")
    push = runs.index("python -m pipeline.db_import --all")
    names = [s.get("name", "") for s in ss]
    ping = names.index("Ping the dead-man's switch (healthchecks.io)")
    later = [runs.index("python -m pipeline.revalidate --scope full --diff-from "
                        "pipeline/forecast_data/ratings_snapshot.json"),
             runs.index("python -m pipeline.nwps_stale_gate"),
             runs.index("python -m pipeline.column_preflight --finish"), ping]
    assert push < min(later) and max(later) < cap, "after the upload, the gates and the ping"
    assert ss[push].get("id") == "push" and ss[push].get("if") == "${{ !cancelled() }}"
    assert ss[cap].get("if") == "${{ !cancelled() && steps.push.outcome == 'success' }}"
    assert ss[cap].get("continue-on-error") == "true"
    assert int(ss[cap].get("timeout-minutes")) == 10
    assert all(s.get("if") == "failure()" for s in ss[cap + 1:])
    assert runs.count("python -m pipeline.accuracy_capture") == 1
    assert "python -m pipeline.accuracy_capture" not in [s.get("run", "") for s in steps(text, "buoy-update")]


def test_the_sampling_budget_leaves_room_inside_the_steps_timeout():
    assert AC.SAMPLE_BUDGET_S <= 6 * 60
