"""pipeline/retention.py: archive a month to Storage and restore it, by keyed REST reads. No deletion.

Against a stand-in for PostgREST that behaves as it does where it matters here: filters, ORDER BY
the listed columns, a page from the requested range, cut further by a server page cap when one
is set, count=exact with head, and upsert with on_conflict and ignore-duplicates returning only
the rows it inserted. Storage is a stand-in too, except one test of the real Storage class
against a local HTTP server.

THE FIXTURE, forecasts, worked by hand. PAGE is 4 and PART_ROWS 5 in these tests.
  spot 1, August: 00Z ecmwf, 00Z nwps, 01Z nwps, 02Z nwps, 03Z ecmwf, 03Z nwps (6 rows); plus
          31 Jul 23Z and 1 Sep 00Z, outside the month.
          Ordered (valid_time, source), the first page is 00e 00n 01n 02n: full, so the rows
          before its last time (02Z) are kept, 00e 00n 01n, and the next request starts at 02Z:
          02n 03e 03n, 3 rows, short, done. Two requests, both from offset 0.
  spot 2, August: 05Z nwps, 06Z nwps (2 rows).
  spot 3: only a July row: no August rows.
  August: 6 + 2 = 8 rows, in two parts: 5 rows, then 3.
Every expected value is written by hand; none comes from calling the function under test.
"""
from __future__ import annotations

import ast
import gzip
import hashlib
import json
import re
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from pipeline import retention as R

ROOT = Path(__file__).resolve().parents[2]


def _ts(s):
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


class _Res:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


class FakeQuery:
    def __init__(self, db, table):
        self.db, self.table = db, table
        self.filters, self.orders, self.rng = [], [], None
        self.cols, self.head, self.counting, self.ups = "*", False, None, None

    def select(self, cols, count=None, head=None):
        self.cols, self.counting, self.head = cols, count, bool(head)
        return self

    def _f(self, col, op, v):
        self.filters.append((col, op, v))
        return self

    def eq(self, c, v): return self._f(c, "=", v)
    def gt(self, c, v): return self._f(c, ">", v)
    def gte(self, c, v): return self._f(c, ">=", v)
    def lt(self, c, v): return self._f(c, "<", v)

    def is_(self, c, v):
        assert v == "null"
        return self._f(c, "is null", None)

    def order(self, col):
        self.orders.append(col)
        return self

    def range(self, a, b):
        self.rng = (a, b)
        return self

    def upsert(self, rows, on_conflict="", ignore_duplicates=False):
        self.ups = (rows, on_conflict, ignore_duplicates)
        return self

    def _val(self, col, v):
        return _ts(v) if col in self.db.time_cols and isinstance(v, str) else v

    def _ok(self, r, col, op, v):
        if op == "is null":
            return r.get(col) is None
        a, b = self._val(col, r.get(col)), self._val(col, v)
        if a is None:
            return False                                   # SQL: NULL compares to nothing
        return {"=": a == b, ">": a > b, ">=": a >= b, "<": a < b}[op]

    def execute(self):
        self.db.calls.append(self)
        rows = self.db.tables.setdefault(self.table, [])
        if self.ups is not None:
            new, conflict, ignore = self.ups
            assert ignore is True
            keys = conflict.split(",")
            # The database casts JSON text to each column's type, as PostgREST's insert does.
            types = self.db.types.get(self.table, {})
            new = [{c: (types[c](v) if isinstance(v, str) and types.get(c) in (int, float) else v)
                    for c, v in r.items()} for r in new]
            have = {tuple(str(r[k]) for k in keys) for r in rows}
            out = []
            for r in new:
                k = tuple(str(r[c]) for c in keys)
                if k not in have:
                    have.add(k)
                    rows.append(dict(r))
                    out.append(dict(r))
            return _Res(out)
        data = [r for r in rows if all(self._ok(r, *f) for f in self.filters)]
        if self.counting in ("exact", "planned") and self.head:
            # planned: the stand-in's "estimate" is the true count; a real planner's is not.
            n = len(data) + (self.db.count_bump() if self.counting == "exact" else 0)
            return _Res([], count=n)
        for col in reversed(self.orders):
            data.sort(key=lambda r, c=col: (r.get(c) is None, self._val(c, r.get(c))))
        a, b = self.rng
        page = data[a:b + 1][: self.db.cap] if self.db.cap else data[a:b + 1]
        if self.cols == "*":
            return _Res([dict(r) for r in page])
        cols = self.cols.split(",")
        return _Res([{c: r.get(c) for c in cols} for r in page])


class FakeDB:
    def __init__(self, tables, cap=None, time_cols=("valid_time", "observed_at", "predicted_at")):
        self.tables = {k: [dict(r) for r in v] for k, v in tables.items()}
        # Each column's type, from the rows given (the stand-in's schema).
        self.types = {k: {c: type(x) for r in v for c, x in r.items() if x is not None}
                      for k, v in tables.items()}
        self.cap, self.time_cols, self.calls = cap, set(time_cols), []
        self.bumps = []                                    # extra rows to report per count call

    def count_bump(self):
        return self.bumps.pop(0) if self.bumps else 0

    def table(self, name):
        return FakeQuery(self, name)


class FakeStorage:
    def __init__(self):
        self.files, self.uploads, self.tamper = {}, [], None

    def download(self, path):
        if path not in self.files:
            raise R.NotFound(path)
        data = self.files[path]
        return self.tamper(path, data) if self.tamper else data

    def exists(self, path):
        return path in self.files

    def upload(self, path, data, content_type):
        if path in self.files:
            raise R.RetentionError(f"upload {path}: exists")
        self.files[path] = data
        self.uploads.append((path, content_type))


def F(i, spot, t, src, hs, face=1.0):
    return {"id": i, "spot_id": spot, "valid_time": t, "source": src, "hs": hs, "face_ft": face}


AUG = [
    F(10, 1, "2026-08-01T00:00:00+00:00", "nwps", 1.5),
    F(11, 1, "2026-08-01T00:00:00+00:00", "ecmwf", 1.4, None),
    F(12, 1, "2026-08-01T01:00:00+00:00", "nwps", 1.6),
    F(13, 1, "2026-08-01T02:00:00+00:00", "nwps", 1.7),
    F(14, 1, "2026-08-01T03:00:00+00:00", "nwps", 1.8),
    F(15, 1, "2026-08-01T03:00:00+00:00", "ecmwf", 1.75, None),
    F(20, 2, "2026-08-14T05:00:00+00:00", "nwps", 0.9),
    F(21, 2, "2026-08-14T06:00:00+00:00", "nwps", 1.0),
]
OUTSIDE = [
    F(9, 1, "2026-07-31T23:00:00+00:00", "nwps", 1.2),
    F(16, 1, "2026-09-01T00:00:00+00:00", "nwps", 2.0),
    F(30, 3, "2026-07-15T12:00:00+00:00", "nwps", 0.5),
]
NOW = datetime(2026, 11, 19, 16, 0, tzinfo=timezone.utc)
SPEC = R.TABLES["forecasts"]


@pytest.fixture(autouse=True)
def small(monkeypatch):
    monkeypatch.setattr(R, "PAGE", 4)
    monkeypatch.setattr(R, "PART_ROWS", 5)


def _db(**kw):
    return FakeDB({"forecasts": AUG + OUTSIDE}, **kw)


def _parts(storage):
    return {p: storage.files[p] for p in storage.files if p.endswith(".csv.gz")}


# ---- months ------------------------------------------------------------------------------- #
def test_month_bounds_and_completeness():
    assert R.month_bounds("2026-08") == (datetime(2026, 8, 1, tzinfo=timezone.utc),
                                         datetime(2026, 9, 1, tzinfo=timezone.utc))
    assert R.month_bounds("2026-12")[1] == datetime(2027, 1, 1, tzinfo=timezone.utc)
    assert R.next_month("2026-12") == "2027-01"
    for bad in ("2026-8", "2026-13", "26-08", "2026/08"):
        with pytest.raises(R.RetentionError):
            R.month_bounds(bad)
    # August ended 1 Sep 00:00; complete once 2 days have passed: 3 Sep 00:00.
    assert R.complete("2026-08", datetime(2026, 9, 3, 0, 0, tzinfo=timezone.utc))
    assert not R.complete("2026-08", datetime(2026, 9, 2, 23, 59, tzinfo=timezone.utc))
    assert R.q(datetime(2026, 8, 1, tzinfo=timezone.utc)) == "2026-08-01T00:00:00.000000Z"
    # Fractions of any length, with the offset's digits left alone (Python 3.9's fromisoformat
    # takes only 3 or 6 fraction digits; a padding that swallowed "+00:00" broke it there).
    assert R.parse_ts("2026-08-01T00:10:00.12+00:00") == datetime(
        2026, 8, 1, 0, 10, 0, 120000, tzinfo=timezone.utc)
    assert R.parse_ts("2026-08-01T00:10:00.123456789+00:00") == datetime(
        2026, 8, 1, 0, 10, 0, 123456, tzinfo=timezone.utc)
    assert R.parse_ts("2026-08-01T00:10:00Z") == datetime(2026, 8, 1, 0, 10, tzinfo=timezone.utc)
    assert R.parse_ts("2026-08-01T10:10:00.5-07:00") == datetime(
        2026, 8, 1, 17, 10, 0, 500000, tzinfo=timezone.utc)


# ---- reading ------------------------------------------------------------------------------ #
def test_units_are_walked_one_keyed_request_each():
    db = _db()
    assert R.units(db, SPEC) == [1, 2, 3]
    # Four requests: none, >1, >2, >3 (empty). Each one row from offset 0, ordered by spot_id.
    assert [q.filters for q in db.calls] == [[], [("spot_id", ">", 1)], [("spot_id", ">", 2)],
                                             [("spot_id", ">", 3)]]
    assert all(q.rng == (0, 0) and q.orders == ["spot_id"] for q in db.calls)


def test_a_full_page_is_split_at_its_last_time_and_never_deep_paged():
    db = _db()
    got = list(R.read_unit(db, SPEC, 1, *R.month_bounds("2026-08")))
    assert [r["id"] for r in got] == [11, 10, 12, 13, 15, 14]     # (time, source): 00e 00n 01n 02n 03e 03n
    assert len(db.calls) == 2
    assert [q.rng for q in db.calls] == [(0, 3), (0, 3)]
    assert db.calls[0].filters == [("spot_id", "=", 1), ("valid_time", ">=", "2026-08-01T00:00:00.000000Z"),
                                   ("valid_time", "<", "2026-09-01T00:00:00.000000Z")]
    assert db.calls[1].filters[1] == ("valid_time", ">=", "2026-08-01T02:00:00.000000Z")
    assert all(q.orders == ["valid_time", "source"] for q in db.calls)


def test_a_page_of_rows_at_one_time_cannot_be_split():
    rows = [{"buoy_id": "46050", "valid_time": "2026-08-01T00:00:00+00:00", "source": s, "lead_day": d}
            for s in ("nwps", "ww3") for d in (0, 1, 2, 3)]
    db = FakeDB({"model_at_buoys": rows})
    with pytest.raises(R.RetentionError, match="4 rows share valid_time"):
        list(R.read_unit(db, R.TABLES["model_at_buoys"], "46050", *R.month_bounds("2026-08")))


# ---- archive ------------------------------------------------------------------------------ #
def test_a_dry_run_reads_the_month_and_uploads_nothing(tmp_path):
    db, st = _db(), FakeStorage()
    got = R.archive(db, st, SPEC, "2026-08", apply=False, now=NOW, workdir=tmp_path)
    assert (got["status"], got["rows"], got["parts"]) == ("dry run", 8, 2)
    assert st.uploads == []


def test_apply_uploads_checked_parts_then_the_manifest(tmp_path):
    db, st = _db(), FakeStorage()
    got = R.archive(db, st, SPEC, "2026-08", apply=True, now=NOW, workdir=tmp_path)
    assert got["status"] == "archived" and got["rows"] == 8
    assert st.uploads == [("forecasts/2026-08/part-0001.csv.gz", "application/gzip"),
                          ("forecasts/2026-08/part-0002.csv.gz", "application/gzip"),
                          ("forecasts/2026-08/manifest.json", "application/json")]
    p1 = gzip.decompress(st.files["forecasts/2026-08/part-0001.csv.gz"]).decode()
    assert p1 == ("id,spot_id,valid_time,source,hs,face_ft\n"
                  "11,1,2026-08-01T00:00:00+00:00,ecmwf,1.4,\\N\n"
                  "10,1,2026-08-01T00:00:00+00:00,nwps,1.5,1.0\n"
                  "12,1,2026-08-01T01:00:00+00:00,nwps,1.6,1.0\n"
                  "13,1,2026-08-01T02:00:00+00:00,nwps,1.7,1.0\n"
                  "15,1,2026-08-01T03:00:00+00:00,ecmwf,1.75,\\N\n")
    p2 = gzip.decompress(st.files["forecasts/2026-08/part-0002.csv.gz"]).decode()
    assert p2.splitlines()[1:] == ["14,1,2026-08-01T03:00:00+00:00,nwps,1.8,1.0",
                                   "20,2,2026-08-14T05:00:00+00:00,nwps,0.9,1.0",
                                   "21,2,2026-08-14T06:00:00+00:00,nwps,1.0,1.0"]
    m = json.loads(st.files["forecasts/2026-08/manifest.json"])
    assert (m["rows"], m["db_count_before"], m["db_count_after"], m["units"]) == (8, 8, 8, 3)
    assert [(p["name"], p["rows"]) for p in m["parts"]] == [("part-0001.csv.gz", 5), ("part-0002.csv.gz", 3)]
    for p in m["parts"]:
        data = st.files[f"forecasts/2026-08/{p['name']}"]
        assert p["sha256"] == hashlib.sha256(data).hexdigest() and p["bytes"] == len(data)
    assert (m["from"], m["to"]) == ("2026-08-01T00:00:00.000000Z", "2026-09-01T00:00:00.000000Z")
    assert m["columns"] == ["id", "spot_id", "valid_time", "source", "hs", "face_ft"]
    assert m["unique_key"] == "spot_id,valid_time,source" and m["null"] == "\\N"
    # Every read was one page from offset 0.
    assert all(q.rng is None or q.rng[0] == 0 for q in db.calls)


def test_the_same_rows_give_the_same_bytes(tmp_path):
    a, b = FakeStorage(), FakeStorage()
    R.archive(_db(), a, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    R.archive(_db(), b, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    assert _parts(a) == _parts(b)
    # gzip's header: bytes 0-1 the magic 1f 8b, byte 2 the method (8, deflate), byte 3 the flags
    # (0: no file name), bytes 4-7 the modification time: 0, so a later run gives the same bytes.
    for data in _parts(a).values():
        assert data[:8] == bytes([0x1F, 0x8B, 8, 0, 0, 0, 0, 0])


def test_an_archived_month_is_skipped_and_never_overwritten(tmp_path):
    db, st = _db(), FakeStorage()
    R.archive(db, st, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    n = len(st.uploads)
    got = R.archive(db, st, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    assert got["status"] == "already archived" and len(st.uploads) == n


def test_a_server_page_cap_below_page_is_caught_by_the_count(tmp_path):
    # The server returns at most 3 rows; the tool asked for 4 and sees a short page, so it stops
    # early on spot 1: 3 rows read against its 6 counted. Nothing is uploaded.
    db, st = _db(cap=3), FakeStorage()
    with pytest.raises(R.RetentionError,
                       match="2026-08 spot_id=1: counted 6 rows before reading, read 3, counted 6 after"):
        R.archive(db, st, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    assert st.uploads == []


def test_rows_written_during_the_read_stop_the_archive(tmp_path):
    db, st = _db(), FakeStorage()
    # Exact counts in order: the NULL-unit count, then spot 1 before and after its read. One more
    # row is counted after spot 1's read.
    db.bumps = [0, 0, 1]
    with pytest.raises(R.RetentionError,
                       match="spot_id=1: counted 6 rows before reading, read 6, counted 7 after"):
        R.archive(db, st, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    assert st.uploads == []


def test_rows_counted_before_but_gone_by_the_read_stop_the_archive(tmp_path):
    db, st = _db(), FakeStorage()
    db.bumps = [0, 1]                                     # spot 1: 7 counted before, 6 read, 6 after
    with pytest.raises(R.RetentionError,
                       match="spot_id=1: counted 7 rows before reading, read 6, counted 6 after"):
        R.archive(db, st, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    assert st.uploads == []


def test_a_month_not_yet_two_days_over_is_refused(tmp_path):
    with pytest.raises(R.RetentionError, match="must have ended 2 days ago"):
        R.archive(_db(), FakeStorage(), SPEC, "2026-11", True, NOW, workdir=tmp_path)


def test_a_corrupted_upload_writes_no_manifest(tmp_path):
    db, st = _db(), FakeStorage()
    st.tamper = lambda path, data: data[:-1] + b"x" if path.endswith("part-0002.csv.gz") else data
    with pytest.raises(R.RetentionError, match="part-0002.csv.gz: the downloaded copy's SHA-256 differs"):
        R.archive(db, st, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    assert "forecasts/2026-08/manifest.json" not in st.files


# ---- restore ------------------------------------------------------------------------------ #
def _archived(tmp_path):
    st = FakeStorage()
    R.archive(_db(), st, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    return st


def test_restore_inserts_the_missing_rows_and_skips_the_rest(tmp_path):
    st = _archived(tmp_path)
    # The table now holds 3 of August's 8 rows (spot 2's two, and spot 1's 00Z nwps).
    db = FakeDB({"forecasts": [AUG[0], AUG[6], AUG[7]] + OUTSIDE})
    got = R.restore(db, st, SPEC, "2026-08", apply=True)
    assert (got["rows"], got["inserted"], got["skipped"]) == (8, 5, 3)
    assert (got["in_db_before"], got["in_db_after"]) == (3, 8)
    new = [r for r in db.tables["forecasts"] if "id" not in r]
    # The serial id is left out; NULL comes back as None; values are sent as the CSV text, which
    # the database casts to the column's type.
    assert sorted((r["spot_id"], r["valid_time"], r["source"], r["hs"], r["face_ft"]) for r in new) == [
        (1, "2026-08-01T00:00:00+00:00", "ecmwf", 1.4, None),
        (1, "2026-08-01T01:00:00+00:00", "nwps", 1.6, 1.0),
        (1, "2026-08-01T02:00:00+00:00", "nwps", 1.7, 1.0),
        (1, "2026-08-01T03:00:00+00:00", "ecmwf", 1.75, None),
        (1, "2026-08-01T03:00:00+00:00", "nwps", 1.8, 1.0)]
    # All 8 archived rows are sent, as text; the database keeps the 3 it has.
    sent = [r for q in db.calls if q.ups for r in q.ups[0]]
    assert sorted(r["hs"] for r in sent) == ["0.9", "1.0", "1.4", "1.5", "1.6", "1.7", "1.75", "1.8"]
    ups = [q.ups for q in db.calls if q.ups]
    assert all(u[1] == "spot_id,valid_time,source" and u[2] is True for u in ups)


def test_a_restore_dry_run_checks_and_writes_nothing(tmp_path):
    st = _archived(tmp_path)
    db = FakeDB({"forecasts": []})
    got = R.restore(db, st, SPEC, "2026-08", apply=False)
    assert (got["status"], got["rows"], got["in_db"]) == ("dry run", 8, 0)
    assert not any(q.ups for q in db.calls)


def test_restore_refuses_a_part_that_differs_before_writing_anything(tmp_path):
    st = _archived(tmp_path)
    st.tamper = lambda path, data: data + b"\0" if path.endswith("part-0001.csv.gz") else data
    db = FakeDB({"forecasts": []})
    with pytest.raises(R.RetentionError, match="part-0001.csv.gz: SHA-256 differs from the manifest"):
        R.restore(db, st, SPEC, "2026-08", apply=True)
    assert not any(q.ups for q in db.calls)


def test_restore_of_a_month_never_archived_is_refused():
    with pytest.raises(R.RetentionError, match="no manifest.json"):
        R.restore(FakeDB({"forecasts": []}), FakeStorage(), SPEC, "2026-07", apply=True)


def test_cells_null_json_and_the_null_marker():
    js = set()
    assert R.cell(None, "x", js) == "\\N"
    assert R.cell(True, "x", js) == "true" and R.cell(0, "x", js) == "0"
    assert R.cell(1.75, "x", js) == "1.75"
    assert R.cell({"a": [1, 2]}, "j", js) == '{"a":[1,2]}' and js == {"j"}
    with pytest.raises(R.RetentionError, match="NULL marker"):
        R.cell("\\N", "x", set())
    assert R.decode('{"a":[1,2]}', "j", {"j"}) == {"a": [1, 2]}
    assert R.decode("\\N", "j", {"j"}) is None
    assert R.decode("plain", "j", {"j"}) == "plain"


# ---- the command line -------------------------------------------------------------------- #
def test_main_archives_every_complete_month_without_a_manifest():
    db, st = _db(), FakeStorage()
    # Earliest row 15 Jul; on 19 Nov the complete months are Jul, Aug, Sep, Oct.
    assert R.main(["archive", "--table", "forecasts", "--month", "all", "--apply"],
                  client=db, storage=st, now=NOW) == 0
    assert sorted(p for p in st.files if p.endswith("manifest.json")) == [
        "forecasts/2026-07/manifest.json", "forecasts/2026-08/manifest.json",
        "forecasts/2026-09/manifest.json", "forecasts/2026-10/manifest.json"]
    rows = {p.split("/")[1]: json.loads(st.files[p])["rows"] for p in st.files if p.endswith(".json")}
    assert rows == {"2026-07": 2, "2026-08": 8, "2026-09": 1, "2026-10": 0}
    # A second run finds them all archived and uploads nothing.
    n = len(st.uploads)
    assert R.main(["archive", "--table", "forecasts", "--month", "all", "--apply"],
                  client=db, storage=st, now=NOW) == 0
    assert len(st.uploads) == n


def test_main_restore_needs_one_table_and_one_month(capsys):
    assert R.main(["restore", "--table", "all", "--month", "2026-08"], client=_db(),
                  storage=FakeStorage(), now=NOW) == 1
    assert "restore takes one --table and one --month" in capsys.readouterr().out


def test_status_lists_months_counts_and_archived(tmp_path):
    db, st = _db(), FakeStorage()
    R.archive(db, st, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    got = R.status(db, st, SPEC, NOW)
    assert [(r["month"], r["rows"], r["exact"], r["archived"]) for r in got] == [
        ("2026-07", 2, False, False), ("2026-08", 8, False, True), ("2026-09", 1, False, False),
        ("2026-10", 0, False, False)]
    # Without --exact every month count is the planner's estimate: count=planned, one request
    # a month; no exact count is made at all.
    counts = [q for q in db.calls if q.head]
    assert all(q.counting in ("planned", "exact") for q in counts)
    status_counts = [q for q in counts if q.counting == "planned"]
    assert len(status_counts) == 4
    # The earliest time is found per spot: no request orders the table by time without naming a
    # spot (buoy_observations and tide_predictions have no index on time alone).
    by_time = [q for q in db.calls if q.orders == ["valid_time"]]
    assert by_time and all(("spot_id", "=") in [(f[0], f[1]) for f in q.filters] for q in by_time)


def test_status_exact_sums_per_unit_counts_and_never_counts_a_whole_month():
    db, st = _db(), FakeStorage()
    got = R.status(db, st, SPEC, NOW, exact=True)
    assert [(r["month"], r["rows"], r["exact"]) for r in got] == [
        ("2026-07", 2, True), ("2026-08", 8, True), ("2026-09", 1, True), ("2026-10", 0, True)]
    # Every count names one spot or asks for NULL spots: 4 months x (3 spots + 1 NULL check).
    counts = [q for q in db.calls if q.head]
    assert len(counts) == 16 and all(q.counting == "exact" for q in counts)
    assert all(any(f[0] == "spot_id" and f[1] in ("=", "is null") for f in q.filters) for q in counts)


def test_rows_with_a_null_unit_stop_the_archive(tmp_path):
    db, st = FakeDB({"forecasts": AUG + [F(40, None, "2026-08-02T00:00:00+00:00", "nwps", 1.0)]}), FakeStorage()
    with pytest.raises(R.RetentionError, match="1 rows with spot_id NULL"):
        R.archive(db, st, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    assert st.uploads == []


def test_no_count_in_an_archive_spans_more_than_one_unit(tmp_path):
    db = _db()
    R.archive(db, FakeStorage(), SPEC, "2026-08", True, NOW, workdir=tmp_path)
    counts = [q for q in db.calls if q.head]
    # The NULL check, then before and after for each of the 3 spots: 7 exact counts.
    assert len(counts) == 7 and all(q.counting == "exact" for q in counts)
    assert all(any(f[0] == "spot_id" and f[1] in ("=", "is null") for f in q.filters) for q in counts)
    # The earliest time is found per spot too: no request orders the whole table by time alone.
    assert not any(q.orders == ["valid_time"] and not any(f[0] == "spot_id" for f in q.filters)
                   for q in db.calls)


def test_an_api_error_names_the_table_month_and_unit(tmp_path):
    class Boom(FakeDB):
        def table(self, name):
            q = super().table(name)
            real = q.execute
            def execute():
                if q.head and any(f == ("spot_id", "=", 2) for f in q.filters):
                    raise RuntimeError("{'message': 'Internal Server Error', 'code': '57014'}")
                return real()
            q.execute = execute
            return q
    db = Boom({"forecasts": AUG})
    with pytest.raises(R.RetentionError,
                       match=r"forecasts 2026-08 spot_id=2: exact count: RuntimeError: .*57014"):
        R.archive(db, FakeStorage(), SPEC, "2026-08", False, NOW, workdir=tmp_path)


def test_every_table_reads_by_its_unique_keys_order():
    assert {t: (s.unit, s.order, s.conflict) for t, s in R.TABLES.items()} == {
        "forecasts": ("spot_id", ("valid_time", "source"), "spot_id,valid_time,source"),
        "buoy_observations": ("buoy_id", ("observed_at",), "buoy_id,observed_at"),
        "forecast_leads": ("spot_id", ("valid_time", "lead_day"), "spot_id,valid_time,lead_day"),
        "model_at_buoys": ("buoy_id", ("valid_time", "source", "lead_day"),
                           "buoy_id,valid_time,source,lead_day"),
        "tide_predictions": ("station_id", ("predicted_at",), "station_id,predicted_at"),
    }
    assert all(s.order[0] == s.time for s in R.TABLES.values())
    assert {t for t, s in R.TABLES.items() if s.surrogate == "id"} == {
        "forecasts", "buoy_observations", "tide_predictions"}


# ---- deletion is off --------------------------------------------------------------------- #
def test_nothing_in_the_tool_or_its_workflow_can_delete():
    src = (ROOT / "pipeline" / "retention.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
    assert "delete" not in attrs and "remove" not in attrs
    strings = [n.value for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str)]
    assert not any(re.search(r"\bDELETE\b", s) for s in strings)
    storage_methods = {n.name for n in ast.walk(tree) if isinstance(n, ast.ClassDef) and n.name == "Storage"
                       for n in n.body if isinstance(n, ast.FunctionDef)}
    assert storage_methods == {"__init__", "_headers", "download", "exists", "upload"}
    wf = (ROOT / ".github" / "workflows" / "retention.yml").read_text(encoding="utf-8")
    code = "\n".join(l.split("#")[0] for l in wf.splitlines())
    assert "delete" not in code.lower()
    assert re.search(r"^on:\n  workflow_dispatch:\n", code, re.M) and "schedule" not in code
    assert "--apply" in code and 'if [ "$RETENTION_APPLY" = "true" ]' in code


def test_face_calibration_reads_from_7_september_by_default():
    from pipeline import face_calibration as FC
    assert FC.DEFAULT_SINCE == "2026-09-07T00:00:00+00:00"
    src = (ROOT / "pipeline" / "face_calibration.py").read_text(encoding="utf-8")
    assert 'ap.add_argument("--since", default=DEFAULT_SINCE)' in src and "2025-06-01T" not in src


# ---- the real Storage class, against a local server -------------------------------------- #
class _Store(BaseHTTPRequestHandler):
    seen: list = []
    files: dict = {}

    def _send(self, code, body=b""):
        self.send_response(code)
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        _Store.seen.append(("GET", self.path, dict(self.headers)))
        p = self.path.split("/storage/v1/object/retention-archive/", 1)[-1]
        if p == "boom":
            return self._send(500, b"oops")
        if p == "gone400":
            return self._send(400, b'{"statusCode":"404","error":"not_found","message":"Object not found"}')
        if p in _Store.files:
            return self._send(200, _Store.files[p])
        self._send(404, b"{}")

    def do_POST(self):
        body = self.rfile.read(int(self.headers["Content-Length"]))
        _Store.seen.append(("POST", self.path, dict(self.headers)))
        p = self.path.split("/storage/v1/object/retention-archive/", 1)[-1]
        if p in _Store.files:
            return self._send(400, b'{"statusCode":"409","error":"Duplicate"}')
        _Store.files[p] = body
        self._send(200, b'{"Key":"x"}')

    def log_message(self, *a):
        pass


def test_storage_sends_the_service_key_never_overwrites_and_reads_not_found():
    _Store.seen, _Store.files = [], {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _Store)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        import requests
        s = R.Storage(f"http://127.0.0.1:{srv.server_address[1]}", "KEY", session=requests.Session())
        s.upload("forecasts/2026-08/part-0001.csv.gz", b"abc", "application/gzip")
        method, path, headers = _Store.seen[-1]
        assert (method, path) == ("POST", "/storage/v1/object/retention-archive/forecasts/2026-08/part-0001.csv.gz")
        assert headers["Authorization"] == "Bearer KEY" and headers["apikey"] == "KEY"
        assert headers["x-upsert"] == "false" and headers["Content-Type"] == "application/gzip"
        assert s.download("forecasts/2026-08/part-0001.csv.gz") == b"abc"
        with pytest.raises(R.RetentionError, match="HTTP 400 .*Duplicate.*never overwritten"):
            s.upload("forecasts/2026-08/part-0001.csv.gz", b"new", "application/gzip")
        assert _Store.files["forecasts/2026-08/part-0001.csv.gz"] == b"abc"
        assert s.exists("forecasts/2026-08/part-0001.csv.gz") and not s.exists("nope")
        assert not s.exists("gone400")                  # Storage's 400 "not_found" is not found
        with pytest.raises(R.RetentionError, match="HTTP 500"):
            s.download("boom")
    finally:
        srv.shutdown()
        srv.server_close()


# ---- retry-safe archive, part logging, restore per unit ------------------------------------ #
class StopsAt(FakeStorage):
    """Fails the upload of one named part, as a run stopped partway would."""

    def __init__(self, stop_at):
        super().__init__()
        self.stop_at = stop_at

    def upload(self, path, data, content_type):
        if self.stop_at and path.endswith(self.stop_at):
            raise R.RetentionError(f"upload {path}: HTTP 502 Bad Gateway")
        super().upload(path, data, content_type)


def test_a_run_stopped_partway_is_finished_by_a_retry_that_keeps_the_matching_part(tmp_path):
    st = StopsAt("part-0002.csv.gz")
    with pytest.raises(R.RetentionError, match="502"):
        R.archive(_db(), st, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    assert sorted(st.files) == ["forecasts/2026-08/part-0001.csv.gz"]       # no manifest
    first = st.files["forecasts/2026-08/part-0001.csv.gz"]
    st.stop_at, st.uploads = None, []
    got = R.archive(_db(), st, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    assert (got["status"], got["reused"]) == ("archived", 1)
    # Only part 2 and the manifest go up; part 1 is kept as it was.
    assert [u[0] for u in st.uploads] == ["forecasts/2026-08/part-0002.csv.gz",
                                          "forecasts/2026-08/manifest.json"]
    assert st.files["forecasts/2026-08/part-0001.csv.gz"] == first
    m = json.loads(st.files["forecasts/2026-08/manifest.json"])
    assert m["rows"] == 8 and [p["rows"] for p in m["parts"]] == [5, 3]
    assert m["parts"][0]["sha256"] == hashlib.sha256(first).hexdigest()


def test_a_part_in_the_bucket_that_differs_stops_the_run_and_is_never_overwritten(tmp_path):
    st = FakeStorage()
    st.files["forecasts/2026-08/part-0001.csv.gz"] = b"not this run's part"
    dry = R.archive(_db(), st, SPEC, "2026-08", False, NOW, workdir=tmp_path)
    assert dry["reusable"] == []
    with pytest.raises(R.RetentionError, match=r"part-0001.csv.gz is already in the bucket and "
                                               r"differs .*never overwritten"):
        R.archive(_db(), st, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    assert st.uploads == [] and st.files["forecasts/2026-08/part-0001.csv.gz"] == b"not this run's part"


def test_a_leftover_part_this_run_does_not_make_stops_the_run(tmp_path):
    st = FakeStorage()
    st.files["forecasts/2026-08/part-0003.csv.gz"] = b"left over"      # this month makes 2 parts
    with pytest.raises(R.RetentionError, match="part-0003.csv.gz is in the bucket but this run "
                                               "makes only 2 part"):
        R.archive(_db(), st, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    assert st.uploads == []


def test_a_dry_run_names_the_parts_a_retry_would_keep(tmp_path):
    st = StopsAt("part-0002.csv.gz")
    with pytest.raises(R.RetentionError):
        R.archive(_db(), st, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    dry = R.archive(_db(), st, SPEC, "2026-08", False, NOW, workdir=tmp_path)
    assert dry["reusable"] == ["part-0001.csv.gz"]


def test_each_part_is_logged_with_its_rows_and_size(tmp_path, caplog):
    caplog.set_level("INFO")
    st = FakeStorage()
    R.archive(_db(), st, SPEC, "2026-08", True, NOW, workdir=tmp_path)
    sizes = {p: len(d) for p, d in st.files.items()}
    for name, rows in (("part-0001.csv.gz", 5), ("part-0002.csv.gz", 3)):
        mb = sizes[f"forecasts/2026-08/{name}"] / 1e6
        assert f"forecasts 2026-08 {name}: {rows} rows, {mb:.2f} MB" in caplog.text
        assert f"forecasts/2026-08/{name}: uploaded, downloaded again and checked" in caplog.text
    assert "(2 uploaded, 0 kept from an earlier run)" in caplog.text
    caplog.clear()
    R.restore(FakeDB({"forecasts": AUG}), st, SPEC, "2026-08", apply=False)
    assert "forecasts/2026-08/part-0001.csv.gz: 5 rows" in caplog.text
    assert "forecasts/2026-08/part-0002.csv.gz: 3 rows" in caplog.text


def test_a_restore_dry_run_compares_each_spot_with_the_archive(tmp_path):
    st = _archived(tmp_path)
    full = R.restore(FakeDB({"forecasts": AUG + OUTSIDE}), st, SPEC, "2026-08", apply=False)
    assert (full["in_db"], full["short_units"], full["short_rows"]) == (8, 0, 0)
    # The table holds 3 of August's 8 rows: spot 1 has 1 of its 6 (5 short), spot 2 both of its 2.
    part = R.restore(FakeDB({"forecasts": [AUG[0], AUG[6], AUG[7]] + OUTSIDE}), st, SPEC, "2026-08",
                     apply=False)
    assert (part["in_db"], part["short_units"], part["short_rows"]) == (3, 1, 5)


def test_month_all_stops_at_a_failed_month_and_a_rerun_finishes_the_rest():
    # Complete months on 19 Nov: Jul (2 rows), Aug (8), Sep (1), Oct (0). August's second part
    # fails to upload: July is archived, August has one part and no manifest, September and
    # October are not touched.
    db, st = _db(), StopsAt("2026-08/part-0002.csv.gz")
    assert R.main(["archive", "--table", "forecasts", "--month", "all", "--apply"],
                  client=db, storage=st, now=NOW) == 1
    assert sorted(st.files) == ["forecasts/2026-07/manifest.json", "forecasts/2026-07/part-0001.csv.gz",
                                "forecasts/2026-08/part-0001.csv.gz"]
    st.stop_at = None
    assert R.main(["archive", "--table", "forecasts", "--month", "all", "--apply"],
                  client=db, storage=st, now=NOW) == 0
    assert sorted(p for p in st.files if p.endswith("manifest.json")) == [
        "forecasts/2026-07/manifest.json", "forecasts/2026-08/manifest.json",
        "forecasts/2026-09/manifest.json", "forecasts/2026-10/manifest.json"]
