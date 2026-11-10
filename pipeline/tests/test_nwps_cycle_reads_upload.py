"""pipeline/db_import.py: the NWPS cycle reads go to nwps_cycle_reads (migration 028), last in
the run, keyed on (wfo, nwps_cycle, read_at). Every expected value is written by hand."""
from __future__ import annotations

import json
from pathlib import Path

from pipeline import column_preflight as P
from pipeline import db_import as D

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "pipeline" / "migrations" / "028_nwps_cycle_reads.sql"

READ = {"wfo": "mfr", "nwps_cycle": "2026-11-08T00:00:00Z", "read_at": "2026-11-08T08:23:50Z",
        "run_id": "42667013127", "subset_bytes": 1000000, "subset_sha256": "cd" * 32,
        "filter_headers": {"Content-Type": "application/octet-stream"},
        "file_last_modified": "2026-11-08T07:05:08Z", "file_bytes": 43017415, "head_error": None}


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
    p = tmp_path / "nwps_reads.json"
    p.write_text(json.dumps(data))
    return p


def test_the_reads_are_upserted_on_their_key_in_column_order(tmp_path):
    extra = dict(READ, wfo="eka", nwps_cycle="2026-11-08T12:00:00Z", rating_debug="not a column")
    c = _Client()
    assert D.import_nwps_reads(c, reads_path=_file(tmp_path, [READ, extra])) == 2
    [(table, key, rows)] = c.sent
    assert (table, key) == ("nwps_cycle_reads", "wfo,nwps_cycle,read_at")
    assert rows[0] == READ and list(rows[0]) == ["wfo", "nwps_cycle", "read_at", "run_id",
                                                 "subset_bytes", "subset_sha256", "filter_headers",
                                                 "file_last_modified", "file_bytes", "head_error"]
    assert rows[1]["wfo"] == "eka" and "rating_debug" not in rows[1]


def test_a_read_without_its_key_is_not_sent(tmp_path):
    c = _Client()
    n = D.import_nwps_reads(c, reads_path=_file(tmp_path, [
        dict(READ, wfo=None), dict(READ, nwps_cycle=""), {k: v for k, v in READ.items() if k != "read_at"},
        READ]))
    assert n == 1 and len(c.sent[0][2]) == 1


def test_no_reads_and_no_file_send_nothing(tmp_path):
    c = _Client()
    assert D.import_nwps_reads(c, reads_path=_file(tmp_path, [])) == 0
    assert D.import_nwps_reads(c, reads_path=tmp_path / "absent.json") == 0
    assert c.sent == []


def test_a_column_the_preflight_found_missing_is_left_out(tmp_path):
    c = _Client()
    D.import_nwps_reads(c, reads_path=_file(tmp_path, [READ]),
                        leave_out={"nwps_cycle_reads": frozenset({"filter_headers"})})
    assert "filter_headers" not in c.sent[0][2][0] and c.sent[0][2][0]["wfo"] == "mfr"


def test_the_reads_go_with_the_forecasts_and_last():
    assert D.tables_written() == ("buoys", "spots", "forecasts", "buoy_observations",
                                  "tide_predictions", "nwps_cycle_reads",
                                  "nwps_office_freshness")
    assert D.tables_written(spots=False, forecasts=False, buoys=True, tides=False) == (
        "buoy_observations",)
    assert D.tables_written(spots=True, forecasts=False, buoys=False, tides=False) == (
        "buoys", "spots")


def test_migration_028_creates_every_column_db_import_writes_and_the_key():
    sql = MIGRATION.read_text()
    assert {c for t, c in P.columns_added(sql) if t == "nwps_cycle_reads"} == {
        "id", "wfo", "nwps_cycle", "read_at", "run_id", "subset_bytes", "subset_sha256",
        "filter_headers", "file_last_modified", "file_bytes", "head_error"}
    assert "CONSTRAINT nwps_cycle_reads_unique UNIQUE (wfo, nwps_cycle, read_at)" in sql
    assert "ALTER TABLE nwps_cycle_reads ENABLE ROW LEVEL SECURITY;" in sql
    assert "CREATE POLICY" not in sql.upper()
