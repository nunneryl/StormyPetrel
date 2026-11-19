"""pipeline/retention.py on a real Postgres with a short statement_timeout: no count can time out.

RETENTION RUN 1 (2026-11-19) failed in `status`: one exact count over a whole month of forecasts
took longer each month (April 0.8 s ... August 7.6 s) and September hit the API's statement
timeout, 57014, at 8.3 s, although forecasts has an index on valid_time. Counting the rows is
the cost. Here the same shape is built at a scale the test database can hold: forecasts with
1,000 spots x 500 hours x 2 sources = 1,000,000 rows in September 2026, the production table's
indexes, and statement_timeout 10 ms on every request. On this database a whole-month count of
those rows takes tens of milliseconds (16 ms for 432k measured while writing this), one spot's
count about half a millisecond.

  1. THE FIXTURE BITES: the old whole-month exact count times out with 57014.
  2. The tool's counts do not: the month's exact count as the sum of per-spot counts is
     1,000 x 500 x 2 = 1,000,000; `status` (the planner's estimate) and the earliest-time walk
     finish too.
  3. End to end on real SQL (a small table, the same 10 ms limit): a dry run, an archive with
     its per-spot before/after counts, and a restore into an empty table and again into the
     full one, through INSERT ... SELECT FROM json_populate_recordset ... ON CONFLICT DO
     NOTHING, which is how PostgREST inserts: text values cast to each column's type.

THE ADAPTER below turns the tool's query-builder calls into the SQL PostgREST would run for them
(filters, ORDER BY, OFFSET/LIMIT, count=exact as count(*), count=planned as EXPLAIN's row
estimate) and raises an error carrying code 57014 when Postgres cancels a statement, as
PostgREST's 500 does.

Runs where TEST_DATABASE_URL is set; on CI it must be (tests.yml sets it), and there this file
refuses to skip.
"""
from __future__ import annotations

import json
import os
import uuid
from datetime import datetime, timezone

import pytest

from pipeline import retention as R

DB_URL = os.environ.get("TEST_DATABASE_URL")
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"
TIMEOUT_MS = 10
NOW = datetime(2026, 11, 19, 16, 0, tzinfo=timezone.utc)
SPEC = R.TABLES["forecasts"]

needs_db = pytest.mark.skipif(
    not DB_URL and not ON_CI,
    reason="TEST_DATABASE_URL is not set: these tests need a Postgres (CI provides one)")


class TimedOut(Exception):
    """What PostgREST answers when Postgres cancels the statement: code 57014."""

    def __init__(self, message):
        super().__init__(json.dumps({"message": message, "code": "57014"}))
        self.code = "57014"


class _Res:
    def __init__(self, data, count=None):
        self.data, self.count = data, count


class PgQuery:
    OPS = {"=": "=", ">": ">", ">=": ">=", "<": "<"}

    def __init__(self, client, table):
        self.c, self.table = client, table
        self.cols, self.counting, self.head = "*", None, False
        self.where, self.orders, self.rng, self.ups = [], [], None, None

    def select(self, cols, count=None, head=None):
        self.cols, self.counting, self.head = cols, count, bool(head)
        return self

    def _f(self, col, op, v):
        self.where.append((col, op, v))
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
        assert ignore_duplicates is True
        self.ups = (rows, on_conflict)
        return self

    def _sql(self):
        from psycopg2 import sql
        parts, params = [], []
        for col, op, v in self.where:
            if op == "is null":
                parts.append(sql.SQL("{} IS NULL").format(sql.Identifier(col)))
            else:
                parts.append(sql.SQL("{} " + self.OPS[op] + " %s").format(sql.Identifier(col)))
                params.append(v)
        where = sql.SQL(" WHERE ") + sql.SQL(" AND ").join(parts) if parts else sql.SQL("")
        return where, params

    def execute(self):
        import psycopg2
        from psycopg2 import errors, sql
        self.c.calls.append(self)
        t = sql.Identifier(self.c.schema, self.table)
        conn = self.c.conn
        try:
            with conn:
                with conn.cursor() as cur:
                    cur.execute(f"SET LOCAL statement_timeout = '{self.c.timeout_ms}ms'")
                    if self.ups is not None:
                        rows, conflict = self.ups
                        cols = list(rows[0].keys())
                        ids = sql.SQL(", ").join(map(sql.Identifier, cols))
                        cur.execute(sql.SQL(
                            "INSERT INTO {t} ({ids}) SELECT {ids} FROM json_populate_recordset("
                            "NULL::{t}, %s::json) ON CONFLICT ({key}) DO NOTHING RETURNING *").format(
                            t=t, ids=ids, key=sql.SQL(", ").join(
                                map(sql.Identifier, conflict.split(",")))), [json.dumps(rows)])
                        return _Res(self._dicts(cur))
                    where, params = self._sql()
                    if self.head and self.counting == "exact":
                        cur.execute(sql.SQL("SELECT count(*) FROM {}").format(t) + where, params)
                        return _Res([], count=cur.fetchone()[0])
                    if self.head and self.counting == "planned":
                        cur.execute(sql.SQL("EXPLAIN (FORMAT JSON) SELECT 1 FROM {}").format(t)
                                    + where, params)
                        plan = cur.fetchone()[0]
                        plan = json.loads(plan) if isinstance(plan, str) else plan
                        return _Res([], count=int(plan[0]["Plan"]["Plan Rows"]))
                    cols = (sql.SQL("*") if self.cols == "*" else
                            sql.SQL(", ").join(map(sql.Identifier, self.cols.split(","))))
                    order = (sql.SQL(" ORDER BY ") + sql.SQL(", ").join(
                        sql.SQL("{} ASC").format(sql.Identifier(o)) for o in self.orders)
                        if self.orders else sql.SQL(""))
                    a, b = self.rng
                    cur.execute(sql.SQL("SELECT {} FROM {}").format(cols, t) + where + order
                                + sql.SQL(" OFFSET %s LIMIT %s"), params + [a, b - a + 1])
                    return _Res(self._dicts(cur))
        except errors.QueryCanceled as e:
            raise TimedOut(str(e).strip()) from None
        except psycopg2.Error:
            raise

    @staticmethod
    def _dicts(cur):
        names = [d[0] for d in cur.description]
        out = []
        for row in cur.fetchall():
            out.append({n: (v.isoformat() if isinstance(v, datetime) else v)
                        for n, v in zip(names, row)})
        return out


class PgClient:
    def __init__(self, conn, schema, timeout_ms=TIMEOUT_MS):
        self.conn, self.schema, self.timeout_ms, self.calls = conn, schema, timeout_ms, []

    def table(self, name):
        return PgQuery(self, name)


class FakeStorage:
    def __init__(self):
        self.files = {}

    def download(self, path):
        if path not in self.files:
            raise R.NotFound(path)
        return self.files[path]

    def exists(self, path):
        return path in self.files

    def upload(self, path, data, content_type):
        assert path not in self.files
        self.files[path] = data


FORECASTS_DDL = """
CREATE TABLE {s}.forecasts (
  id BIGSERIAL PRIMARY KEY,
  spot_id INTEGER,
  valid_time TIMESTAMPTZ NOT NULL,
  source TEXT DEFAULT 'nwps',
  hs DOUBLE PRECISION,
  face_ft DOUBLE PRECISION,
  UNIQUE (spot_id, valid_time, source));
CREATE INDEX ON {s}.forecasts (spot_id, valid_time);
CREATE INDEX ON {s}.forecasts (valid_time);
"""


@pytest.fixture(scope="module")
def conn():
    if not DB_URL:
        if ON_CI:
            pytest.fail("on CI, TEST_DATABASE_URL must be set by tests.yml: refusing to skip")
        pytest.skip("TEST_DATABASE_URL is not set")
    import psycopg2
    c = psycopg2.connect(DB_URL)
    yield c
    c.close()


def _schema(conn, rows_sql):
    name = "rt_" + uuid.uuid4().hex[:10]
    old = conn.autocommit
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"CREATE SCHEMA {name}")
        cur.execute(FORECASTS_DDL.format(s=name))
        if rows_sql:
            cur.execute(rows_sql.format(s=name))
        cur.execute(f"VACUUM ANALYZE {name}.forecasts")
    conn.autocommit = old
    return name


def _drop(conn, name):
    old = conn.autocommit
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"DROP SCHEMA {name} CASCADE")
    conn.autocommit = old


@pytest.fixture(scope="module")
def big(conn):
    # 1,000 spots x 500 hours (1 Sep 00Z to 21 Sep 19Z) x 2 sources = 1,000,000 rows.
    name = _schema(conn, """
        INSERT INTO {s}.forecasts (spot_id, valid_time, source, hs, face_ft)
        SELECT sp, '2026-09-01T00:00:00Z'::timestamptz + h * interval '1 hour', src, 1.0, 2.0
        FROM generate_series(1, 1000) sp, generate_series(0, 499) h, unnest(ARRAY['ecmwf', 'nwps']) src""")
    yield name
    _drop(conn, name)


@needs_db
def test_the_fixture_reproduces_run_1_a_whole_month_exact_count_times_out(conn, big):
    db = PgClient(conn, big)
    start, end = R.month_bounds("2026-09")
    with pytest.raises(TimedOut) as e:
        (db.table("forecasts").select("spot_id", count="exact", head=True)
         .gte("valid_time", R.q(start)).lt("valid_time", R.q(end)).execute())
    assert e.value.code == "57014"


@needs_db
def test_the_exact_count_per_spot_summed_does_not_time_out(conn, big):
    db = PgClient(conn, big)
    unit_list = R.units(db, SPEC)
    assert unit_list == list(range(1, 1001))
    start, end = R.month_bounds("2026-09")
    assert R.count_keyed(db, SPEC, unit_list, start, end, "2026-09") == 1_000_000
    # Every count named one spot, or asked for NULL spots.
    counts = [q for q in db.calls if q.head]
    assert len(counts) == 1001
    assert all(any(w[0] == "spot_id" and w[1] in ("=", "is null") for w in q.where) for q in counts)


@needs_db
def test_status_and_the_earliest_time_do_not_time_out(conn, big):
    db = PgClient(conn, big)
    unit_list = R.units(db, SPEC)
    assert R.earliest(db, SPEC, unit_list) == datetime(2026, 9, 1, tzinfo=timezone.utc)
    got = R.status(db, FakeStorage(), SPEC, NOW)
    assert [r["month"] for r in got] == ["2026-09", "2026-10"]
    sept = got[0]["rows"]
    # The planner's estimate: approximate, so within 10% of the 1,000,000 rows.
    assert got[0]["exact"] is False and 900_000 <= sept <= 1_100_000, sept


@pytest.fixture()
def small(conn):
    # 30 spots x 40 hours (1 Aug 00Z to 2 Aug 15Z) x 2 sources = 2,400 August rows, plus spot 1
    # at 31 Jul 23Z and 1 Sep 00Z, outside the month.
    name = _schema(conn, """
        INSERT INTO {s}.forecasts (spot_id, valid_time, source, hs, face_ft)
        SELECT sp, '2026-08-01T00:00:00Z'::timestamptz + h * interval '1 hour', src,
               sp + h / 100.0, CASE WHEN src = 'ecmwf' THEN NULL ELSE 2.5 END
        FROM generate_series(1, 30) sp, generate_series(0, 39) h, unnest(ARRAY['ecmwf', 'nwps']) src;
        INSERT INTO {s}.forecasts (spot_id, valid_time, source, hs)
        VALUES (1, '2026-07-31T23:00:00Z', 'nwps', 9.0), (1, '2026-09-01T00:00:00Z', 'nwps', 9.0)""")
    yield name
    _drop(conn, name)


@needs_db
def test_archive_and_restore_end_to_end_on_real_sql(conn, small, tmp_path, monkeypatch):
    monkeypatch.setattr(R, "PAGE", 50)            # 80 rows a spot: one split per spot
    monkeypatch.setattr(R, "PART_ROWS", 1000)     # 2,400 rows: parts of 1,000, 1,000 and 400
    db, st = PgClient(conn, small), FakeStorage()
    dry = R.archive(db, st, SPEC, "2026-08", apply=False, now=NOW, workdir=tmp_path)
    assert (dry["rows"], dry["parts"]) == (2400, 3) and st.files == {}
    got = R.archive(db, st, SPEC, "2026-08", apply=True, now=NOW, workdir=tmp_path)
    assert got["status"] == "archived"
    m = json.loads(st.files["forecasts/2026-08/manifest.json"])
    assert (m["rows"], m["db_count_before"], m["db_count_after"]) == (2400, 2400, 2400)
    assert [p["rows"] for p in m["parts"]] == [1000, 1000, 400]
    # Every read started at offset 0.
    assert all(q.rng[0] == 0 for q in db.calls if q.rng)

    # Restore into an empty table: all 2,400 inserted, cast back to their types.
    empty = _schema(conn, None)
    try:
        db2 = PgClient(conn, empty)
        r1 = R.restore(db2, st, SPEC, "2026-08", apply=True)
        assert (r1["inserted"], r1["skipped"], r1["in_db_before"], r1["in_db_after"]) == (2400, 0, 0, 2400)
        with conn.cursor() as cur:
            cur.execute(f"SELECT hs, face_ft FROM {empty}.forecasts WHERE spot_id = 7 "
                        "AND valid_time = '2026-08-01T12:00:00Z' ORDER BY source")
            # spot 7, hour 12: hs = 7 + 12/100 = 7.12; ecmwf's face_ft NULL, nwps's 2.5.
            assert cur.fetchall() == [(7.12, None), (7.12, 2.5)]
        conn.rollback()
        # Again: every row is there, so none is inserted.
        r2 = R.restore(db2, st, SPEC, "2026-08", apply=True)
        assert (r2["inserted"], r2["skipped"]) == (0, 2400)
    finally:
        _drop(conn, empty)
