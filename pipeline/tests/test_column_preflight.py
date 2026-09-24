"""A column missing from the live database cannot stop publishing, and cannot pass quietly.

THE INCIDENT. #227 merged before migration 019 was applied. db_import sent forecasts.nwps_cycle,
PostgREST refused the whole forecasts upsert with PGRST204, run_all stopped there (so buoy
observations and tides were not written either, and revalidation was skipped), and forecasts
stopped publishing until the migration was applied and the run repeated (run 2023).

WHAT IS PINNED HERE, through a fake of the PostgREST behind supabase-py:
  1. a missing column is named at the start with the migration that adds it, left out of the
     upload (everything else still goes), and the run ends FAILED with one line naming it;
  2. with every column present, a run sends exactly what it sends today and ends green;
  3. any other database error, including a PGRST204 the check did not predict, still raises;
  4. the lists the check works from match what the record builders send, and every column
     they send is added by a migration file, so the check can always name one;
  5. the workflow runs the check before the fetch and the failure after everything else.

THE FAKE refuses an unknown column exactly as PostgREST does: the same code and message
(measured on PostgREST 12.2.12 and 14.17). It also serves the OpenAPI description the preflight
reads, built from the same column lists, as PostgREST builds it from its schema cache.

Every expected value is written by hand. None is obtained by calling the function under test.
"""
from __future__ import annotations

import functools
import json
import logging
import re
from pathlib import Path
from types import SimpleNamespace

import pytest
from postgrest.exceptions import APIError

from pipeline import column_preflight as P
from pipeline import db_import as D
from pipeline.enrichment import geodata
from pipeline.tests.test_ci_workflow import yaml_code

ROOT = Path(__file__).resolve().parents[2]
WORKFLOW = ROOT / ".github" / "workflows" / "forecast-pipeline.yml"

# --------------------------------------------------------------------------- #
# What each upsert sends, written out by hand from the builders in db_import  #
# --------------------------------------------------------------------------- #

WRITTEN = {
    "buoys": ["id", "lat", "lng", "name"],
    "spots": [
        "slug", "name", "lat", "lng", "state", "region", "swell_window_arcs", "data_sources",
        "orientation_deg", "offshore_wind_deg", "optimal_swell_dir", "break_type",
        "break_type_confidence", "tide_preference", "tide_preference_source", "crowd_factor",
        "hazards", "nearest_buoy_id", "nearest_buoy_dist_km", "nearest_tide_station_id",
        "nearest_tide_station_dist_km", "nwps_wfo", "fallback_buoy_ids", "swell_window_source",
        "review_status", "description", "description_signature",
    ],
    "forecasts": [
        "spot_id", "valid_time", "hs", "tp", "dp", "wind_speed", "wind_dir", "swell_hs",
        "swell_tp", "swell_dp", "swell_1_hs", "swell_1_tp", "swell_1_dp", "swell_2_hs",
        "swell_2_tp", "swell_2_dp", "swell_3_hs", "swell_3_tp", "swell_3_dp", "wind_wave_hs",
        "wind_wave_tp", "wind_wave_dp", "swell_source", "tide_level_ft", "tide_norm", "face_ft",
        "face_lo_ft", "face_hi_ft", "face_ft_raw", "face_correction_version", "dir_gain",
        "wind_mult", "tide_mult", "chop_ratio", "chop_mult", "period_quality",
        "effective_size_ft", "stars", "nwps_cycle", "source",
    ],
    "buoy_observations": ["buoy_id", "observed_at", "hs", "tp", "dp", "swell_hs", "swell_tp",
                          "swell_dp", "wind_speed", "wind_dir", "water_temp"],
    "tide_predictions": ["station_id", "predicted_at", "level_ft", "type"],
}
KEYS = {"buoys": ("id",), "spots": ("slug",), "forecasts": ("spot_id", "valid_time", "source"),
        "buoy_observations": ("buoy_id", "observed_at"),
        "tide_predictions": ("station_id", "predicted_at")}
# Columns the live tables have that db_import never sends.
DB_ONLY = {"buoys": [], "spots": ["id", "geom", "aka_names", "created_at", "updated_at"],
           "forecasts": ["id", "fetched_at"], "buoy_observations": ["id"],
           "tide_predictions": ["id"]}
ALL_TABLES = ("buoys", "spots", "forecasts", "buoy_observations", "tide_predictions")


def live(*missing: str) -> dict[str, list[str]]:
    """The live tables' columns, less each 'table.column' or whole 'table' in `missing`."""
    out = {t: DB_ONLY[t] + WRITTEN[t] for t in ALL_TABLES}
    for m in missing:
        t, _, c = m.partition(".")
        if c:
            out[t] = [x for x in out[t] if x != c]
        else:
            del out[t]
    return out


# --------------------------------------------------------------------------- #
# The fake PostgREST                                                           #
# --------------------------------------------------------------------------- #

def pgrst204(table, column):
    """PostgREST's refusal of an unknown column, word for word (seen in run 2023's log)."""
    return APIError({"message": f"Could not find the '{column}' column of '{table}' in the schema cache",
                     "code": "PGRST204", "hint": None, "details": None})


class _Response:
    def __init__(self, status, body):
        self.status_code, self._body = status, body
        self.text = json.dumps(body)

    def json(self):
        return self._body


class _Session:
    def __init__(self, fake):
        self.fake, self.requests = fake, []

    def get(self, path, headers=None, timeout=None):
        self.requests.append((path, dict(headers or {}), timeout))
        answer = self.fake.describe.pop(0) if self.fake.describe else None
        if isinstance(answer, Exception):
            raise answer
        if answer is not None:
            return answer
        defs = {t: {"type": "object", "properties": {c: {"type": "string"} for c in cols}}
                for t, cols in self.fake.live.items()}
        return _Response(200, {"swagger": "2.0", "definitions": defs, "paths": {}})


class _Query:
    def __init__(self, fake, table):
        self.fake, self.table, self.op, self.cols, self.rng = fake, table, None, None, None

    def select(self, cols):
        self.op, self.cols = "select", cols
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def in_(self, *_a):
        return self

    def upsert(self, rows, on_conflict=None):
        self.op, self.rows, self.on_conflict = "upsert", rows, on_conflict
        return self

    def execute(self):
        if self.op == "upsert":
            err = self.fake.fail.get(self.table)
            if err is not None:
                raise err
            if self.table not in self.fake.live:
                raise APIError({"message": f"Could not find the table 'public.{self.table}' in the "
                                "schema cache", "code": "PGRST205", "hint": None, "details": None})
            for row in self.rows:
                for col in row:
                    if col not in self.fake.live[self.table]:
                        raise pgrst204(self.table, col)
            self.fake.sent.append((self.table, self.on_conflict, json.dumps(self.rows)))
            return SimpleNamespace(data=list(self.rows))
        has = self.fake.live.get(self.table, ())
        rows = [{k: v for k, v in r.items() if k in has}   # a missing column is not returned
                for r in self.fake.rows.get((self.table, self.cols), [])]
        a, b = self.rng or (0, len(rows))
        return SimpleNamespace(data=rows[a:b + 1])


class FakePostgrest:
    def __init__(self, live_columns, fail=None, describe=None):
        self.live = {t: list(c) for t, c in live_columns.items()}
        self.fail = dict(fail or {})
        self.describe = list(describe or [])
        self.sent = []   # (table, on_conflict, the JSON text of the rows) per upsert
        self.rows = {("spots", "*"): [EXISTING_SPOT], ("spots", "id,name"): SPOT_IDS}
        self.postgrest = SimpleNamespace(session=_Session(self))

    def table(self, name):
        return _Query(self, name)

    def rows_sent(self, table):
        return [r for t, _k, text in self.sent if t == table for r in json.loads(text)]

    def text_sent(self, table):
        return [text for t, _k, text in self.sent if t == table]


# --------------------------------------------------------------------------- #
# One small run's inputs, and what they must turn into                         #
# --------------------------------------------------------------------------- #

ENRICHED = [
    {"name": "Steamer Lane", "lat": 36.9513, "lng": -122.0266, "region_hint": "California",
     "is_valid_surf_spot": True, "swell_window_arcs": [{"min": 180, "max": 300}],
     "orientation_deg": 190.0, "offshore_wind_deg": 10.0, "optimal_swell_dir": 250.0,
     "break_type": "point", "break_type_confidence": 0.9, "tide_preference": "mid",
     "tide_preference_source": "manual", "crowd_factor": "high", "hazards": ["rocks"],
     "nearest_buoy_id": "46042", "nearest_buoy_dist_km": 30.1,
     "nearest_tide_station_id": "9413745", "nearest_tide_station_dist_km": 1.2,
     "nwps_wfo": "mtr", "fallback_buoy_ids": ["46236"], "swell_window_source": "cdip_mop"},
    {"name": "Mavericks", "lat": 37.4936, "lng": -122.4966, "region_hint": "California",
     "is_valid_surf_spot": True},
]
EXISTING_SPOT = {"id": 7, "slug": "steamer-lane", "name": "Steamer Lane", "aka_names": ["The Lane"],
                 "lat": 36.9513, "lng": -122.0266, "geom": "0101", "state": "California",
                 "region": "California", "review_status": "reviewed",
                 "description": "A right point.", "description_signature": "0000000000000000",
                 "created_at": "2026-01-01T00:00:00Z", "updated_at": "2026-09-01T00:00:00Z"}
SPOT_IDS = [{"id": 7, "name": "Steamer Lane"}, {"id": 8, "name": "Mavericks"}]
HOUR = {"valid_time": "2026-09-24T12:00:00Z", "hs": 1.2, "tp": 13.0, "dp": 285.0,
        "wind_speed": 3.1, "wind_dir": 45.0, "swell_hs": 1.1, "swell_tp": 13.0, "swell_dp": 280.0,
        "swell_1_hs": 1.0, "swell_1_tp": 14.0, "swell_1_dp": 282.0, "swell_2_hs": 0.4,
        "swell_2_tp": 9.0, "swell_2_dp": 200.0, "swell_3_hs": None, "swell_3_tp": None,
        "swell_3_dp": None, "wind_wave_hs": 0.2, "wind_wave_tp": 4.0, "wind_wave_dp": 40.0,
        "swell_source": "nwps", "tide_level_ft": 3.4, "tide_norm": 0.6, "face_ft": 4.5,
        "face_lo_ft": 4.0, "face_hi_ft": 5.0, "face_ft_raw": 4.2, "face_correction_version": "v3",
        "dir_gain": 0.95, "wind_mult": 1.0, "tide_mult": 0.98, "chop_ratio": 0.1, "chop_mult": 1.0,
        "period_quality": 0.9, "effective_size_ft": 4.3, "stars": 3.5,
        "nwps_cycle": "2026-09-24T00:00:00Z", "rating_debug": "not a column"}
BUOYS = {"46042": {"latest": {"time": "2026-09-24T11:00:00Z", "wave_height_m": 1.5,
                              "dominant_period_s": 12.0, "mean_wave_dir_deg": 290.0,
                              "wind_speed_ms": 4.0, "wind_dir_deg": 320.0, "water_temp_c": 14.2},
                   "spec_history_24h": [{"time": "2026-09-24T11:00:00Z", "swell_height_m": 1.3,
                                         "swell_period_s": 13.0, "swell_dir_deg": 285.0}]}}
TIDES = {"9413745": {"hilo": [{"t": "2026-09-24 03:12", "v": "4.1", "type": "H"}],
                     "hourly": [{"t": "2026-09-24 04:00", "v": "3.9"}]}}
SNAPSHOT = {"46042": {"lat": 36.785, "lng": -122.398, "name": "Monterey"}}

# The forecasts row the ratings hour above becomes: every column, in the builder's order.
FORECAST_ROW = {
    "spot_id": 7, "valid_time": "2026-09-24T12:00:00Z", "hs": 1.2, "tp": 13.0, "dp": 285.0,
    "wind_speed": 3.1, "wind_dir": 45.0, "swell_hs": 1.1, "swell_tp": 13.0, "swell_dp": 280.0,
    "swell_1_hs": 1.0, "swell_1_tp": 14.0, "swell_1_dp": 282.0, "swell_2_hs": 0.4,
    "swell_2_tp": 9.0, "swell_2_dp": 200.0, "swell_3_hs": None, "swell_3_tp": None,
    "swell_3_dp": None, "wind_wave_hs": 0.2, "wind_wave_tp": 4.0, "wind_wave_dp": 40.0,
    "swell_source": "nwps", "tide_level_ft": 3.4, "tide_norm": 0.6, "face_ft": 4.5,
    "face_lo_ft": 4.0, "face_hi_ft": 5.0, "face_ft_raw": 4.2, "face_correction_version": "v3",
    "dir_gain": 0.95, "wind_mult": 1.0, "tide_mult": 0.98, "chop_ratio": 0.1, "chop_mult": 1.0,
    "period_quality": 0.9, "effective_size_ft": 4.3, "stars": 3.5,
    "nwps_cycle": "2026-09-24T00:00:00Z", "source": "nwps",
}
BUOY_ROW = {"buoy_id": "46042", "observed_at": "2026-09-24T11:00:00Z", "hs": 1.5, "tp": 12.0,
            "dp": 290.0, "swell_hs": 1.3, "swell_tp": 13.0, "swell_dp": 285.0, "wind_speed": 4.0,
            "wind_dir": 320.0, "water_temp": 14.2}
TIDE_ROWS = [{"station_id": "9413745", "predicted_at": "2026-09-24T03:12:00+00:00",
              "level_ft": 4.1, "type": "H"},
             {"station_id": "9413745", "predicted_at": "2026-09-24T04:00:00+00:00",
              "level_ft": 3.9, "type": None}]
BUOYS_META_ROW = {"id": "46042", "lat": 36.785, "lng": -122.398, "name": "Monterey"}

# The line the run ends on when nwps_cycle is missing, exactly.
NWPS_CYCLE_LINE = ("Missing database columns: forecasts.nwps_cycle "
                   "(pipeline/migrations/019_forecasts_nwps_cycle.sql) — left out of this run's "
                   "upload; apply the migration to write them.")


@pytest.fixture
def run(tmp_path, monkeypatch):
    """A pipeline run's three db-facing steps against a fake PostgREST: the preflight at the
    start, db_import, and --finish at the end, through their real CLI entry points."""
    result = tmp_path / "column_preflight.json"
    monkeypatch.setenv(D.PREFLIGHT_RESULT_ENV, str(result))
    monkeypatch.delenv("GITHUB_ACTIONS", raising=False)
    waits = []
    monkeypatch.setattr(P, "_sleep", waits.append)
    for name, data in (("spots", ENRICHED), ("ratings", {"Steamer Lane": [HOUR]}),
                       ("buoys", BUOYS), ("tides", TIDES)):
        (tmp_path / f"{name}.json").write_text(json.dumps(data))
    for fn, kw in (("import_spots", "spots_path"), ("import_forecasts", "ratings_path"),
                   ("import_buoys", "buoys_path"), ("import_tides", "tides_path")):
        stem = {"spots_path": "spots", "ratings_path": "ratings", "buoys_path": "buoys",
                "tides_path": "tides"}[kw]
        monkeypatch.setattr(D, fn, functools.partial(getattr(D, fn), **{kw: tmp_path / f"{stem}.json"}))
    monkeypatch.setattr(D, "_excluded_slugs", lambda: set())
    monkeypatch.setattr(D, "_load_tide_freshness", lambda *a, **k: {})
    monkeypatch.setattr(D, "_validate_coord_derived", lambda records: 0)
    monkeypatch.setattr(geodata, "load_buoy_snapshot", lambda *a, **k: dict(SNAPSHOT))

    def go(fake, mode="--all", preflight=True):
        """preflight=False is a run as every run was before this change: no check, and no
        result file named for db_import."""
        monkeypatch.setattr(D, "get_client", lambda: fake)
        if preflight:
            assert P.main([mode]) == 0, "the preflight must never stop a run"
        else:
            monkeypatch.delenv(D.PREFLIGHT_RESULT_ENV, raising=False)
        return D.main([mode])

    return SimpleNamespace(go=go, result=result, waits=waits, monkeypatch=monkeypatch,
                           finish=lambda: P.main(["--finish"]))


# --------------------------------------------------------------------------- #
# 1 — the incident: a missing column                                          #
# --------------------------------------------------------------------------- #

def test_the_incident_nwps_cycle_is_named_left_out_and_the_run_fails_at_the_end(run, caplog, capsys):
    caplog.set_level(logging.INFO)
    fake = FakePostgrest(live("forecasts.nwps_cycle"))
    assert run.go(fake) == 0, "the upload must succeed without the column"
    # named at the start, by name and with the migration that adds it
    start = [r.getMessage() for r in caplog.records if r.name == "pipeline.column_preflight"]
    assert any("forecasts.nwps_cycle" in m and "pipeline/migrations/019_forecasts_nwps_cycle.sql" in m
               for m in start), start
    # forecasts flowed, with every column but that one
    without = {k: v for k, v in FORECAST_ROW.items() if k != "nwps_cycle"}
    assert fake.rows_sent("forecasts") == [without]
    assert json.loads(json.dumps(fake.rows_sent("forecasts")[0])) == without
    assert list(fake.rows_sent("forecasts")[0]) == [k for k in FORECAST_ROW if k != "nwps_cycle"]
    # the tables after it in run_all were written too, in full
    assert fake.rows_sent("buoy_observations") == [BUOY_ROW]
    assert fake.rows_sent("tide_predictions") == TIDE_ROWS
    # and the run ends FAILED, on one plain line naming it
    capsys.readouterr()
    assert run.finish() == 1
    assert capsys.readouterr().out.splitlines() == [NWPS_CYCLE_LINE]


@pytest.mark.parametrize("missing, migration", [
    ("forecasts.nwps_cycle", "019_forecasts_nwps_cycle.sql"),
    ("forecasts.face_lo_ft", "016_face_range.sql"),
    ("spots.tide_preference_source", "018_tide_preference_widening.sql"),
    ("spots.description_signature", "012_description_signature.sql"),
    ("buoy_observations.water_temp", "001_initial_schema.sql"),
    ("tide_predictions.type", "001_initial_schema.sql"),
    ("buoys.name", "011_buoys.sql"),
])
def test_a_missing_column_in_any_table_is_left_out_of_that_table_only(run, capsys, missing, migration):
    table, _, column = missing.partition(".")
    fake = FakePostgrest(live(missing))
    assert run.go(fake) == 0
    sent = fake.rows_sent(table)
    assert sent and all(column not in r for r in sent), f"{missing} must be left out"
    for other in ALL_TABLES:
        if other != table:
            assert fake.rows_sent(other), f"{other} must still be written"
    # everything else in that table is still sent
    if table == "forecasts":
        assert fake.rows_sent("forecasts") == [{k: v for k, v in FORECAST_ROW.items() if k != column}]
    capsys.readouterr()
    assert run.finish() == 1
    line, = capsys.readouterr().out.splitlines()
    assert line == (f"Missing database columns: {missing} (pipeline/migrations/{migration}) — "
                    "left out of this run's upload; apply the migration to write them.")


def test_two_missing_columns_are_both_named_on_the_one_line(run, capsys):
    fake = FakePostgrest(live("forecasts.nwps_cycle", "forecasts.face_hi_ft"))
    assert run.go(fake) == 0
    assert fake.rows_sent("forecasts") == [{k: v for k, v in FORECAST_ROW.items()
                                            if k not in ("nwps_cycle", "face_hi_ft")}]
    capsys.readouterr()
    assert run.finish() == 1
    assert capsys.readouterr().out.splitlines() == [
        "Missing database columns: forecasts.face_hi_ft (pipeline/migrations/016_face_range.sql), "
        "forecasts.nwps_cycle (pipeline/migrations/019_forecasts_nwps_cycle.sql) — left out of "
        "this run's upload; apply the migration to write them."]


def test_on_github_the_end_line_is_also_an_error_annotation(run, capsys, monkeypatch):
    run.go(FakePostgrest(live("forecasts.nwps_cycle")))
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    capsys.readouterr()
    assert run.finish() == 1
    assert capsys.readouterr().out.splitlines() == [
        NWPS_CYCLE_LINE, f"::error title=Missing database columns::{NWPS_CYCLE_LINE}"]


def test_an_annotation_stays_one_line_whatever_it_carries(capsys, monkeypatch):
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    P._annotate("error", "T", "50% of\r\nit")
    assert capsys.readouterr().out == "::error title=T::50%25 of%0D%0Ait\n"


# --------------------------------------------------------------------------- #
# 2 — a normal run sends exactly what it sends today                          #
# --------------------------------------------------------------------------- #

def test_with_every_column_present_a_run_sends_exactly_todays_rows_and_ends_green(run, capsys):
    fake = FakePostgrest(live())
    assert run.go(fake) == 0
    assert fake.text_sent("forecasts") == [json.dumps([FORECAST_ROW])]
    assert fake.text_sent("buoy_observations") == [json.dumps([BUOY_ROW])]
    assert fake.text_sent("tide_predictions") == [json.dumps(TIDE_ROWS)]
    assert fake.text_sent("buoys") == [json.dumps([BUOYS_META_ROW])]
    capsys.readouterr()
    assert run.finish() == 0
    assert capsys.readouterr().out == "", "a green run's last step says nothing"


def test_the_check_changes_nothing_about_what_is_sent(run):
    """The same run with the preflight and without it, which is what every run was before
    this change, sends byte-identical rows to every table."""
    checked, unchecked = FakePostgrest(live()), FakePostgrest(live())
    run.go(checked)
    run.go(unchecked, preflight=False)
    assert checked.sent == unchecked.sent
    assert [t for t, _k, _x in checked.sent] == ["buoys", "spots", "forecasts",
                                                 "buoy_observations", "tide_predictions"]


def test_the_preflight_asks_for_the_description_once_through_the_clients_own_session(run):
    fake = FakePostgrest(live())
    run.go(fake)
    assert fake.postgrest.session.requests == [("/", {"Accept": "application/openapi+json"}, 30.0)]
    assert run.waits == []


# --------------------------------------------------------------------------- #
# 3 — anything else still fails exactly as before                              #
# --------------------------------------------------------------------------- #

TIMEOUT = APIError({"message": "canceling statement due to statement timeout", "code": "57014",
                    "hint": None, "details": None})


@pytest.mark.parametrize("missing", [(), ("forecasts.nwps_cycle",)])
def test_an_unrelated_database_error_still_raises(run, missing):
    fake = FakePostgrest(live(*missing), fail={"forecasts": TIMEOUT})
    with pytest.raises(APIError) as e:
        run.go(fake)
    assert e.value.code == "57014"
    assert fake.rows_sent("buoy_observations") == [], "the run still stops where it always did"


def test_a_pgrst204_the_check_did_not_find_is_not_swallowed(run):
    """The check saw every column; by upload time `stars` is gone. That refusal is not the
    one the check predicted, so it raises exactly as it would have before."""
    fake = FakePostgrest(live())
    real_main = D.main

    def drop_stars_then_import(argv):
        fake.live["forecasts"].remove("stars")
        return real_main(argv)

    run.monkeypatch.setattr(D, "main", drop_stars_then_import)
    with pytest.raises(APIError) as e:
        run.go(fake)
    assert e.value.code == "PGRST204"
    assert e.value.message == "Could not find the 'stars' column of 'forecasts' in the schema cache"


def test_without_the_preflight_a_missing_column_fails_exactly_as_in_run_2023(run):
    fake = FakePostgrest(live("forecasts.nwps_cycle"))
    with pytest.raises(APIError) as e:
        run.go(fake, preflight=False)
    assert (e.value.code, e.value.message) == (
        "PGRST204", "Could not find the 'nwps_cycle' column of 'forecasts' in the schema cache")


def test_a_non_database_failure_still_raises(run):
    fake = FakePostgrest(live("forecasts.nwps_cycle"), fail={"forecasts": ConnectionError("TLS reset")})
    with pytest.raises(ConnectionError):
        run.go(fake)


# --------------------------------------------------------------------------- #
# What cannot be left out, and a check that cannot run                        #
# --------------------------------------------------------------------------- #

def test_a_missing_key_column_is_reported_but_not_left_out(run, capsys):
    fake = FakePostgrest(live("forecasts.valid_time"))
    with pytest.raises(APIError) as e:
        run.go(fake)
    assert e.value.code == "PGRST204", "without its key the upsert cannot run; it fails as before"
    result = json.loads(run.result.read_text())
    assert result["leave_out"] == {} and result["blocked"] == ["forecasts.valid_time"]
    capsys.readouterr()
    assert run.finish() == 1
    assert capsys.readouterr().out.splitlines() == [
        "Missing from the database and cannot be left out: forecasts.valid_time "
        "(pipeline/migrations/001_initial_schema.sql) — those uploads fail as before."]


def test_a_missing_table_is_reported_and_its_upload_fails_as_before(run, capsys):
    fake = FakePostgrest(live("tide_predictions"))
    with pytest.raises(APIError) as e:
        run.go(fake)
    assert e.value.code == "PGRST205"
    assert fake.rows_sent("forecasts") == [FORECAST_ROW], "tables before it are written in full"
    capsys.readouterr()
    assert run.finish() == 1
    assert capsys.readouterr().out.splitlines() == [
        "Missing from the database and cannot be left out: tide_predictions "
        "(pipeline/migrations/001_initial_schema.sql) — those uploads fail as before."]


FORBIDDEN = _Response(403, {"message": "Access to schema is forbidden",
                            "hint": "Accessing the schema via the Data API is only allowed using "
                                    "a secret API key."})


def test_an_unreadable_description_leaves_nothing_out_and_still_fails_the_run(run, capsys):
    fake = FakePostgrest(live(), describe=[FORBIDDEN, FORBIDDEN, FORBIDDEN])
    assert run.go(fake) == 0
    assert run.waits == [5.0, 15.0], "retried twice before giving up"
    assert fake.text_sent("forecasts") == [json.dumps([FORECAST_ROW])], "every column sent, as before"
    capsys.readouterr()
    assert run.finish() == 1
    line, = capsys.readouterr().out.splitlines()
    assert line.startswith("Column check did not run: could not read the live tables' columns "
                           "(HTTP 403: "), line
    assert line.endswith("Nothing was left out; this run sent every column, as before."), line


def test_a_transient_failure_is_retried_and_the_check_completes(run):
    fake = FakePostgrest(live("forecasts.nwps_cycle"), describe=[ConnectionError("reset")])
    assert run.go(fake) == 0
    assert run.waits == [5.0]
    assert "nwps_cycle" not in fake.rows_sent("forecasts")[0]


def test_no_database_client_is_recorded_not_raised(run, monkeypatch, capsys):
    def no_env():
        raise RuntimeError("SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in the environment")
    monkeypatch.setattr(D, "get_client", no_env)
    assert P.main(["--all"]) == 0
    assert run.waits == [], "nothing to retry"
    capsys.readouterr()
    assert run.finish() == 1
    assert capsys.readouterr().out.startswith("Column check did not run: no database client (RuntimeError")


def test_a_preflight_that_crashed_leaves_no_result_and_the_end_says_so(run, monkeypatch, capsys):
    run.result.write_text(json.dumps({"status": "checked", "leave_out": {}, "reason": None}))
    monkeypatch.setattr(D, "tables_for_args", lambda argv: 1 / 0)
    with pytest.raises(ZeroDivisionError):
        P.main(["--all"])
    assert not run.result.exists(), "an earlier run's result must never be read as this one's"
    capsys.readouterr()
    assert run.finish() == 1
    assert capsys.readouterr().out.startswith("Column check did not run: the preflight step left no result")


# --------------------------------------------------------------------------- #
# db_import reads only a completed check, and only what it may drop            #
# --------------------------------------------------------------------------- #

def _result(tmp_path, **fields):
    p = tmp_path / "r.json"
    p.write_text(json.dumps(dict({"status": "checked", "leave_out": {}}, **fields)))
    return {D.PREFLIGHT_RESULT_ENV: str(p)}


def test_leave_out_is_read_only_from_a_named_completed_check(tmp_path):
    env = _result(tmp_path, leave_out={"forecasts": ["nwps_cycle"]})
    assert D.preflight_leave_out(env) == {"forecasts": frozenset({"nwps_cycle"})}
    assert D.preflight_leave_out({}) == {}, "no result named: send every column"
    assert D.preflight_leave_out(_result(tmp_path, status="unchecked",
                                         leave_out={"forecasts": ["nwps_cycle"]})) == {}
    assert D.preflight_leave_out({D.PREFLIGHT_RESULT_ENV: str(tmp_path / "absent.json")}) == {}


def test_leave_out_never_names_a_key_or_a_column_db_import_does_not_write(tmp_path):
    env = _result(tmp_path, leave_out={"forecasts": ["spot_id", "made_up", "stars"],
                                       "not_a_table": ["x"]})
    assert D.preflight_leave_out(env) == {"forecasts": frozenset({"stars"})}


def test_with_nothing_to_leave_out_the_rows_built_are_the_rows_sent():
    chunk = [{"a": 1}]
    assert D._without(chunk, None, "forecasts") is chunk
    assert D._without(chunk, {}, "forecasts") is chunk
    assert D._without(chunk, {"spots": frozenset({"a"})}, "forecasts") is chunk
    assert D._without(chunk, {"forecasts": frozenset({"a"})}, "forecasts") == [{}]


# --------------------------------------------------------------------------- #
# 4 — the lists the check works from are the lists the builders send           #
# --------------------------------------------------------------------------- #

def test_the_declared_columns_and_keys_are_these():
    assert D.WRITTEN_COLUMNS == {t: tuple(c) for t, c in WRITTEN.items()}
    assert D.UPSERT_KEYS == KEYS


def test_every_builder_sends_exactly_its_declared_columns(run):
    fake = FakePostgrest(live())
    run.go(fake)
    for table in ("buoys", "forecasts", "buoy_observations", "tide_predictions"):
        for row in fake.rows_sent(table):
            assert list(row) == WRITTEN[table], table
    merged = set(EXISTING_SPOT) - {"id", "geom", "created_at", "updated_at"}
    sent = [set(r) for r in fake.rows_sent("spots")]
    assert all(keys <= set(WRITTEN["spots"]) | merged for keys in sent), "an undeclared spots column"
    assert set().union(*sent) >= set(WRITTEN["spots"]), "a declared spots column nothing sends"


def test_every_upsert_matches_on_its_declared_key(run):
    fake = FakePostgrest(live())
    run.go(fake)
    assert {t: k for t, k, _x in fake.sent} == {
        "buoys": "id", "spots": "slug", "forecasts": "spot_id,valid_time,source",
        "buoy_observations": "buoy_id,observed_at", "tide_predictions": "station_id,predicted_at"}


@pytest.mark.parametrize("flag, tables", [
    ("--all", ("buoys", "spots", "forecasts", "buoy_observations", "tide_predictions")),
    ("--buoys-only", ("buoy_observations",)),
    ("--spots-only", ("buoys", "spots")),
    ("--forecasts-only", ("forecasts", "buoy_observations", "tide_predictions")),
    ("--tides-only", ("tide_predictions",)),
])
def test_the_preflight_checks_exactly_the_tables_that_mode_writes(run, flag, tables):
    assert D.tables_for_args([flag]) == tables
    fake = FakePostgrest(live())
    run.go(fake, mode=flag)
    assert tuple(dict.fromkeys(t for t, _k, _x in fake.sent)) == tables
    assert json.loads(run.result.read_text())["tables"] == list(tables)


def test_every_column_db_import_writes_is_added_by_a_migration_file():
    index = P.migration_index()
    unowned = [f"{t}.{c}" for t, cols in WRITTEN.items() for c in cols if not index.get((t, c))]
    assert unowned == [], f"no file in pipeline/migrations/ adds {unowned}"


def test_the_migration_that_adds_a_column_is_found_by_name():
    index = P.migration_index()
    assert index[("forecasts", "nwps_cycle")] == ["019_forecasts_nwps_cycle.sql"]
    assert index[("forecasts", "stars")] == ["001_initial_schema.sql"]
    assert index[("spots", "fallback_buoy_ids")] == ["013_fallback_buoy_ids.sql"]
    assert index[("buoys", "lat")] == ["011_buoys.sql"]


def test_only_real_column_additions_count():
    sql = """
        -- ALTER TABLE forecasts ADD COLUMN in_a_comment TEXT;
        /* ALTER TABLE forecasts ADD COLUMN in_a_block_comment TEXT; */
        COMMENT ON COLUMN forecasts.x IS 'it''s like; ALTER TABLE forecasts ADD COLUMN in_a_string TEXT';
        CREATE OR REPLACE FUNCTION f() RETURNS void AS $fn$
          BEGIN PERFORM 1; ALTER TABLE forecasts ADD COLUMN in_a_function_body int; END;
        $fn$ LANGUAGE plpgsql;
        ALTER TABLE ONLY public."forecasts"
          ADD COLUMN IF NOT EXISTS a NUMERIC(10, 2),
          ADD b TEXT,
          ADD IF NOT EXISTS c INT,
          ADD CONSTRAINT not_a_column CHECK (a > 0),
          DROP COLUMN gone;
        CREATE TABLE IF NOT EXISTS t2 (
          id BIGSERIAL PRIMARY KEY,
          val NUMERIC(6, 1) DEFAULT 0,
          CONSTRAINT t2_val CHECK (val >= 0),
          UNIQUE (id, val),
          LIKE other_table INCLUDING ALL
        ) PARTITION BY RANGE (id);
    """
    assert P.columns_added(sql) == {("forecasts", "a"), ("forecasts", "b"), ("forecasts", "c"),
                                    ("t2", "id"), ("t2", "val")}


def test_a_do_block_runs_with_its_migration_so_its_column_counts():
    sql = """
        DO $$
        BEGIN
          IF NOT EXISTS (SELECT 1 FROM information_schema.columns
                         WHERE table_name = 'forecasts' AND column_name = 'in_a_do_block') THEN
            ALTER TABLE forecasts ADD COLUMN in_a_do_block TEXT;
          END IF;
        END $$;
        DO LANGUAGE plpgsql $body$ BEGIN ALTER TABLE spots ADD COLUMN also_in_a_do TEXT; END $body$;
    """
    assert P.columns_added(sql) == {("forecasts", "in_a_do_block"), ("spots", "also_in_a_do")}


# --------------------------------------------------------------------------- #
# 5 — the workflow: the check first, the failure last                          #
# --------------------------------------------------------------------------- #

def _job(text: str, job: str) -> str:
    body = re.split(r"^jobs:\n", text, maxsplit=1, flags=re.M)[1]
    m = re.search(rf"^  {re.escape(job)}:\n(.*?)(?=^  \S|\Z)", body, re.M | re.S)
    assert m, f"no job {job}"
    return m.group(1)


def steps(text: str, job: str) -> list[dict[str, str]]:
    """The steps of one job, in order, each {key: value} of its top-level single-line keys."""
    listed = re.split(r"^    steps:\n", _job(text, job), maxsplit=1, flags=re.M)[1]
    return [dict(re.findall(r"^(?:        )?([\w-]+):[ \t]*(.*\S)[ \t]*$", chunk, re.M))
            for chunk in re.split(r"^      - ", listed, flags=re.M)[1:]]


def job_env(text: str, job: str) -> dict[str, str]:
    """The job-level env block of one job."""
    head = re.split(r"^    steps:\n", _job(text, job), maxsplit=1, flags=re.M)[0]
    # yaml_code leaves a stripped comment as a blank line, so blank lines stay in the block
    m = re.search(r"^    env:\n((?:(?:      .*)?\n)*)", head + "\n", re.M)
    return dict(re.findall(r"^      ([A-Z_]+):\s*(\S.*)$", m.group(1), re.M)) if m else {}


def test_the_step_reader_reads_hand_written_yaml():
    text = ("on: push\njobs:\n  a:\n    permissions:\n      contents: read\n    env:\n      X_Y: z\n"
            "\n      A_B: c\n    steps:\n      - name: One\n        run: echo 1\n"
            "      - name: Two\n        if: failure()\n        uses: x/y@v1\n        with:\n"
            "          k: v\n  b:\n    steps:\n      - run: echo b\n")
    assert steps(text, "a") == [{"name": "One", "run": "echo 1"},
                                {"name": "Two", "if": "failure()", "uses": "x/y@v1"}]
    assert steps(text, "b") == [{"run": "echo b"}]
    assert job_env(text, "a") == {"X_Y": "z", "A_B": "c"}
    assert job_env(text, "b") == {}


@pytest.mark.parametrize("job, mode", [("full-pipeline", "--all"), ("buoy-update", "--buoys-only")])
def test_the_check_runs_first_can_never_stop_the_run_and_matches_db_imports_mode(job, mode):
    ss = steps(yaml_code(WORKFLOW.read_text(encoding="utf-8")), job)
    runs = [s.get("run", "") for s in ss]
    check = runs.index(f"python -m pipeline.column_preflight {mode}")
    db = runs.index(f"python -m pipeline.db_import {mode}")
    fetch = next(i for i, r in enumerate(runs) if "pipeline.forecast.fetch_all" in r)
    assert check < fetch < db, "the check runs before the fetch"
    assert ss[check].get("continue-on-error") == "true", "the check must never stop the run"
    assert "if" not in ss[check]


@pytest.mark.parametrize("job", ["full-pipeline", "buoy-update"])
def test_the_failure_comes_last_and_after_revalidation(job):
    text = yaml_code(WORKFLOW.read_text(encoding="utf-8"))
    ss = steps(text, job)
    runs = [s.get("run", "") for s in ss]
    end = runs.index("python -m pipeline.column_preflight --finish")
    work = [i for i, r in enumerate(runs) if "pipeline.db_import" in r or "pipeline.revalidate --scope" in r]
    assert work and max(work) < end, "every upload and revalidation runs before the failure"
    assert ss[end].get("if") == "${{ !cancelled() }}", "it reports even after an earlier failure"
    assert all(s.get("if") == "failure()" for s in ss[end + 1:]), \
        "only failure handlers may follow it"
    assert job_env(text, job).get("COLUMN_PREFLIGHT_RESULT") == "pipeline/forecast_data/column_preflight.json"


def test_the_failure_issue_carries_the_columns_line():
    ss = steps(yaml_code(WORKFLOW.read_text(encoding="utf-8")), "full-pipeline")
    issue = ss[-1]
    assert issue.get("name") == "Open issue on failure" and issue.get("if") == "failure()"
    script = WORKFLOW.read_text(encoding="utf-8").split("Open issue on failure", 1)[1]
    assert "process.env.COLUMN_PREFLIGHT_RESULT" in script and ".reason" in script
