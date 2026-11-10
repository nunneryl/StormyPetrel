"""One nwps_office_freshness row per NWPS office per run, offices that read no cycle included
(migration 029): nwps.fetch builds and writes them, db_import uploads them last.

WHY. From the 2026-11-09 23:23Z run every full run read NWPS cycles up to 30 h behind, or
none, and only log lines said so; nwps_cycle_reads (migration 028) has a row only for a cycle
that was read, so an office that read nothing left no row at all.

Every expected value is written by hand. The box case is the 11-10 08:15Z run's: newest
listed 11-10 00Z, read 11-09 00Z after four HTTP 500s, 24 h behind and 32.3 h old.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from pipeline import column_preflight as P
from pipeline import db_import as D
from pipeline.forecast import nwps
from pipeline.tests.test_nwps_baked_node import BAKED_LAT, BAKED_LNG, _FakeDataset

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "pipeline" / "migrations" / "029_nwps_office_freshness.sql"
NOW = datetime(2026, 11, 10, 8, 15, 40, tzinfo=timezone.utc)
FETCHED_AT = "2026-11-10T08:15:40Z"

BOX_LISTED = [("20261110", "00"), ("20261109", "18"), ("20261109", "12"), ("20261109", "06"),
              ("20261109", "00")]
BOX_FAILED = [("20261110", "00", "HTTP 500"), ("20261109", "18", "HTTP 500"),
              ("20261109", "12", "HTTP 500"), ("20261109", "06", "HTTP 500")]

BOX_ROW = {"fetched_at": FETCHED_AT, "wfo": "box", "run_id": "42781235066", "listing": "ok",
           "newest_listed": "2026-11-10T00:00:00Z", "cycle_used": "2026-11-09T00:00:00Z",
           "source": "download", "hours_behind": 24.0, "age_h": 32.3,
           "failed": [{"cycle": "2026-11-10T00:00:00Z", "reason": "HTTP 500"},
                      {"cycle": "2026-11-09T18:00:00Z", "reason": "HTTP 500"},
                      {"cycle": "2026-11-09T12:00:00Z", "reason": "HTTP 500"},
                      {"cycle": "2026-11-09T06:00:00Z", "reason": "HTTP 500"}],
           "stale_reason": "24 h behind the newest listed (4 failed: HTTP 500)",
           "spots": 52, "spots_written": 52}
ILM_ROW = {"fetched_at": FETCHED_AT, "wfo": "ilm", "run_id": "42781235066", "listing": "unanswered",
           "newest_listed": None, "cycle_used": None, "source": None, "hours_behind": None,
           "age_h": None, "failed": [],
           "stale_reason": "no cycle read — NOMADS listing unanswered",
           "spots": 18, "spots_written": 0}
COLUMNS = ["fetched_at", "wfo", "run_id", "listing", "newest_listed", "cycle_used", "source",
           "hours_behind", "age_h", "failed", "stale_reason", "spots", "spots_written"]


# --------------------------------------------------------------------------- #
# 1 — the rows                                                                 #
# --------------------------------------------------------------------------- #

def test_a_behind_office_and_one_that_read_nothing_each_get_a_row():
    recs = [nwps.freshness("box", BOX_LISTED, ("20261109", "00"), BOX_FAILED, NOW),
            nwps.freshness("ilm", None, None, None, NOW)]
    rows = nwps.freshness_rows(recs, FETCHED_AT, "42781235066", {"box": 52, "ilm": 18},
                               {"box": 52})
    assert rows == [BOX_ROW, ILM_ROW]
    assert list(rows[0]) == COLUMNS


def test_a_fresh_office_has_no_stale_reason_and_an_unknown_office_serves_no_spots():
    rec = nwps.freshness("mfr", [("20261110", "00")], ("20261110", "00"), None, NOW)
    [row] = nwps.freshness_rows([rec], FETCHED_AT, None, {}, {})
    assert (row["stale_reason"], row["age_h"], row["hours_behind"]) == (None, 8.3, 0.0)
    assert (row["run_id"], row["spots"], row["spots_written"]) == (None, 0, 0)


def test_the_file_holds_the_rows(tmp_path):
    p = tmp_path / "nwps_freshness.json"
    assert nwps.write_freshness([BOX_ROW, ILM_ROW], p) == 2
    assert json.loads(p.read_text()) == [BOX_ROW, ILM_ROW]
    assert nwps.write_freshness([], p) == 0 and json.loads(p.read_text()) == []


def test_the_file_is_the_configured_one():
    from pipeline import config
    assert config.NWPS_FRESHNESS_FILE == config.FORECAST_DATA_DIR / "nwps_freshness.json"


# --------------------------------------------------------------------------- #
# 2 — fetch writes one row per office, the office that read nothing included   #
# --------------------------------------------------------------------------- #

class _Now(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


class _Resp:
    def __init__(self, status, chunks=()):
        self.status_code, self.text, self.headers, self._chunks = status, "", {}, list(chunks)

    def iter_content(self, chunk_size=None):
        return iter(self._chunks)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Session:
    """grib_filter refuses every mfr file and serves every other one."""
    def get(self, url, params=None, stream=None, timeout=None):
        if params["file"].endswith(".mfr.grib2"):
            return _Resp(500)
        return _Resp(200, [b"g" * 2048])

    def head(self, url, timeout=None, allow_redirects=None):
        return _Resp(404)


def test_fetch_writes_a_row_for_every_office_including_one_that_read_nothing(monkeypatch, tmp_path):
    out = tmp_path / "nwps_freshness.json"
    monkeypatch.setattr(nwps, "datetime", _Now)
    monkeypatch.setenv("GITHUB_RUN_ID", "42830902540")
    monkeypatch.setattr(nwps, "session", lambda: _Session())
    for name, value in (("NWPS_CACHE_DIR", tmp_path / "cache"),
                        ("NWPS_READS_FILE", tmp_path / "nwps_reads.json"),
                        ("NWPS_FORECAST_FILE", tmp_path / "nwps.json"),
                        ("NWPS_FRESHNESS_FILE", out)):
        monkeypatch.setattr(nwps, name, value)
    monkeypatch.setattr(nwps, "apply_wfos", lambda spots: {"mhx": 2, "mfr": 1})
    monkeypatch.setattr(nwps, "candidate_cycles", lambda wfo: {
        "mhx": [("20261110", "00")], "mfr": [("20261110", "00"), ("20261109", "18")]}[wfo])
    monkeypatch.setattr(nwps, "_open_grib_datasets", lambda p: [_FakeDataset()])
    # one of mhx's two spots gets a series, the other none: spots 2, spots_written 1
    monkeypatch.setattr(nwps, "_extract_time_series_from_datasets",
                        lambda ds, lat, lng: [{"valid_time": "2026-11-10T09:00:00Z", "hs": 1.0}]
                        if (round(lat, 2), round(lng, 2)) == (BAKED_LAT, BAKED_LNG) else [])
    spots = [{"name": "Kill Devil Hills", "nwps_wfo": "mhx", "lat": 36.35, "lng": -75.85,
              "orientation_deg": 90.0, "nwps_node_lat": BAKED_LAT, "nwps_node_lng": BAKED_LNG},
             {"name": "Nags Head", "nwps_wfo": "mhx", "lat": 36.35, "lng": -75.85,
              "orientation_deg": 90.0, "nwps_node_lat": 36.30, "nwps_node_lng": -75.90},
             {"name": "Bandon Beach", "nwps_wfo": "mfr", "lat": 43.1, "lng": -124.43}]
    nwps.fetch(spots)
    mfr_reason = "no cycle read, newest listed 2026-11-10T00:00:00Z (2 failed: HTTP 500)"
    assert json.loads(out.read_text()) == [
        {"age_h": None, "cycle_used": None,
         "failed": [{"cycle": "2026-11-10T00:00:00Z", "reason": "HTTP 500"},
                    {"cycle": "2026-11-09T18:00:00Z", "reason": "HTTP 500"}],
         "fetched_at": FETCHED_AT, "hours_behind": None, "listing": "ok",
         "newest_listed": "2026-11-10T00:00:00Z", "run_id": "42830902540", "source": None,
         "spots": 1, "spots_written": 0, "stale_reason": mfr_reason, "wfo": "mfr"},
        # 08:15:40 - 00:00 = 8 h 15 min 40 s = 8.261 h -> 8.3
        {"age_h": 8.3, "cycle_used": "2026-11-10T00:00:00Z", "failed": [],
         "fetched_at": FETCHED_AT, "hours_behind": 0.0, "listing": "ok",
         "newest_listed": "2026-11-10T00:00:00Z", "run_id": "42830902540", "source": "download",
         "spots": 2, "spots_written": 1, "stale_reason": None, "wfo": "mhx"}]


# --------------------------------------------------------------------------- #
# 3 — the upload                                                               #
# --------------------------------------------------------------------------- #

class _Table:
    def __init__(self, sink, name):
        self.sink, self.name = sink, name

    def upsert(self, rows, on_conflict=None):
        self.sink.append((self.name, on_conflict, rows))
        return self

    def execute(self):
        return self


class _Client:
    def __init__(self):
        self.sent = []

    def table(self, name):
        return _Table(self.sent, name)


def _file(tmp_path, data):
    p = tmp_path / "nwps_freshness.json"
    p.write_text(json.dumps(data))
    return p


def test_the_rows_are_upserted_on_their_key_in_column_order(tmp_path):
    c = _Client()
    extra = dict(ILM_ROW, debug="not a column")
    assert D.import_nwps_freshness(c, freshness_path=_file(tmp_path, [BOX_ROW, extra])) == 2
    [(table, key, rows)] = c.sent
    assert (table, key) == ("nwps_office_freshness", "wfo,fetched_at")
    assert rows == [BOX_ROW, ILM_ROW] and list(rows[1]) == COLUMNS


def test_a_row_without_its_key_is_not_sent(tmp_path):
    c = _Client()
    n = D.import_nwps_freshness(c, freshness_path=_file(tmp_path, [
        dict(BOX_ROW, wfo=None), dict(BOX_ROW, fetched_at=""),
        {k: v for k, v in BOX_ROW.items() if k != "wfo"}, ILM_ROW]))
    assert n == 1 and c.sent[0][2] == [ILM_ROW]


def test_no_rows_and_no_file_send_nothing(tmp_path):
    c = _Client()
    assert D.import_nwps_freshness(c, freshness_path=_file(tmp_path, [])) == 0
    assert D.import_nwps_freshness(c, freshness_path=tmp_path / "absent.json") == 0
    assert c.sent == []


def test_a_column_the_preflight_found_missing_is_left_out(tmp_path):
    c = _Client()
    D.import_nwps_freshness(c, freshness_path=_file(tmp_path, [BOX_ROW]),
                            leave_out={"nwps_office_freshness": frozenset({"failed"})})
    assert "failed" not in c.sent[0][2][0] and c.sent[0][2][0]["wfo"] == "box"


def test_the_freshness_goes_with_the_forecasts_and_last():
    assert D.tables_written() == ("buoys", "spots", "forecasts", "buoy_observations",
                                  "tide_predictions", "nwps_cycle_reads",
                                  "nwps_office_freshness")
    assert D.tables_written(spots=False, forecasts=False, buoys=True, tides=True) == (
        "buoy_observations", "tide_predictions")


def test_run_all_uploads_it_after_the_reads(monkeypatch):
    calls = []
    for fn in ("import_buoy_snapshot", "import_spots", "import_forecasts", "import_buoys",
               "import_tides", "import_nwps_reads", "import_nwps_freshness"):
        monkeypatch.setattr(D, fn, lambda client, leave_out=None, _fn=fn: calls.append(_fn) or 3)
    monkeypatch.setattr(D, "get_client", lambda: object())
    stats = D.run_all()
    assert calls[-2:] == ["import_nwps_reads", "import_nwps_freshness"]
    assert stats["nwps_office_freshness"] == 3
    calls.clear()
    assert "nwps_office_freshness" not in D.run_all(forecasts=False) and calls[-1] == "import_tides"


def test_migration_029_creates_every_column_db_import_writes_and_the_key():
    sql = MIGRATION.read_text()
    assert {c for t, c in P.columns_added(sql) if t == "nwps_office_freshness"} == {
        "id", *COLUMNS}
    assert "CONSTRAINT nwps_office_freshness_unique UNIQUE (wfo, fetched_at)" in sql
    assert "ALTER TABLE nwps_office_freshness ENABLE ROW LEVEL SECURITY;" in sql
    assert "CREATE POLICY" not in sql.upper()
