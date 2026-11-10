"""Migration 028 (nwps_cycle_reads) on a real Postgres: it applies twice, both check queries in
its header run exactly as the user will paste them, the key refuses a second row for the same
read, and row level security is on with no policy.

WHERE IT RUNS. Like test_time_zone_migration.py: CI provides TEST_DATABASE_URL and the database
tests refuse to skip there; elsewhere they skip when it is unset. Every expected row is written
out by hand.
"""
from __future__ import annotations

import os
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MIGRATION = os.path.join(ROOT, "pipeline", "migrations", "028_nwps_cycle_reads.sql")
DB_URL = os.environ.get("TEST_DATABASE_URL")
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"

needs_db = pytest.mark.skipif(
    not DB_URL and not ON_CI,
    reason="TEST_DATABASE_URL is not set — migration 028 needs a Postgres")


def _sql():
    with open(MIGRATION, encoding="utf-8") as f:
        return f.read()


def check_query(starts: str) -> str:
    """A check query from the header, without its comment markers: the text the user pastes."""
    lines = _sql().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"--   {starts}"))
    end = next(i for i in range(start, len(lines)) if lines[i].rstrip().endswith(";"))
    return "\n".join(ln[5:] for ln in lines[start:end + 1])


@pytest.fixture()
def db():
    if not DB_URL:
        pytest.fail("on CI, TEST_DATABASE_URL must be set by tests.yml — refusing to skip the "
                    "only test of migration 028")
    import psycopg2
    from psycopg2.extensions import make_dsn
    admin = psycopg2.connect(DB_URL)
    admin.autocommit = True
    name = f"nwps_reads_migration_{uuid.uuid4().hex[:12]}"
    with admin.cursor() as c:
        c.execute(f'CREATE DATABASE "{name}"')
    conn = psycopg2.connect(make_dsn(DB_URL, dbname=name))
    conn.autocommit = True
    try:
        with conn.cursor() as c:
            c.execute(_sql())
            c.execute(_sql())            # idempotent: a second run changes nothing
        yield conn
    finally:
        conn.close()
        with admin.cursor() as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def _rows(conn, sql, args=None):
    with conn.cursor() as c:
        c.execute(sql, args)
        return c.fetchall()


INSERT = ("INSERT INTO nwps_cycle_reads (wfo, nwps_cycle, read_at, run_id, subset_bytes, "
          "subset_sha256, filter_headers, file_last_modified, file_bytes, head_error) "
          "VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)")


def test_the_check_query_text():
    assert check_query("SELECT column_name") == (
        "SELECT column_name, data_type FROM information_schema.columns\n"
        "WHERE table_schema = 'public' AND table_name = 'nwps_cycle_reads'\n"
        "ORDER BY ordinal_position;")


@needs_db
def test_check_1_gives_the_eleven_columns(db):
    assert _rows(db, check_query("SELECT column_name")) == [
        ("id", "bigint"), ("wfo", "text"), ("nwps_cycle", "timestamp with time zone"),
        ("read_at", "timestamp with time zone"), ("run_id", "text"),
        ("subset_bytes", "bigint"), ("subset_sha256", "text"), ("filter_headers", "jsonb"),
        ("file_last_modified", "timestamp with time zone"), ("file_bytes", "bigint"),
        ("head_error", "text")]


@needs_db
def test_check_2_gives_one_row_per_office_and_cycle(db):
    reads = [("mfr", "2026-11-07T12:00:00Z", "2026-11-07T20:22:24Z", "55879399", "aa"),
             ("mfr", "2026-11-07T12:00:00Z", "2026-11-08T01:52:27Z", "43017313", "bb"),
             ("eka", "2026-11-07T12:00:00Z", "2026-11-07T20:18:10Z", "12492532", "cc"),
             ("eka", "2026-11-07T12:00:00Z", "2026-11-08T01:48:21Z", "12492532", "cc")]
    with db.cursor() as c:
        for wfo, cyc, at, size, sha in reads:
            c.execute(INSERT, (wfo, cyc, at, "1", int(size), sha, "{}", None, None, None))
    got = _rows(db, "SET TIME ZONE 'UTC'; " + check_query("SELECT wfo, nwps_cycle"))
    as_text = [(w, c.isoformat(), n, k, f.isoformat(), l.isoformat()) for w, c, n, k, f, l in got]
    assert as_text == [
        ("mfr", "2026-11-07T12:00:00+00:00", 2, 2, "2026-11-07T20:22:24+00:00", "2026-11-08T01:52:27+00:00"),
        ("eka", "2026-11-07T12:00:00+00:00", 2, 1, "2026-11-07T20:18:10+00:00", "2026-11-08T01:48:21+00:00")]


@needs_db
def test_the_key_refuses_a_second_row_for_the_same_read(db):
    import psycopg2
    row = ("mfr", "2026-11-08T00:00:00Z", "2026-11-08T08:23:50Z", "1", 1000000, "cd" * 32,
           '{"Content-Type": "application/octet-stream"}', "2026-11-08T07:05:08Z", 43017415, None)
    with db.cursor() as c:
        c.execute(INSERT, row)
        with pytest.raises(psycopg2.errors.UniqueViolation):
            c.execute(INSERT, row)
        # the same cycle read again by a later run is a new row
        c.execute(INSERT, row[:2] + ("2026-11-08T13:50:14Z",) + row[3:])
    assert _rows(db, "SELECT count(*) FROM nwps_cycle_reads") == [(2,)]


@needs_db
def test_row_level_security_is_on_with_no_policy(db):
    assert _rows(db, "SELECT relrowsecurity FROM pg_class WHERE relname = 'nwps_cycle_reads'") == [(True,)]
    assert _rows(db, "SELECT count(*) FROM pg_policies WHERE tablename = 'nwps_cycle_reads'") == [(0,)]
