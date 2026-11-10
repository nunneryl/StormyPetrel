"""Migration 029 (nwps_office_freshness) on a real Postgres: it applies twice, both check queries
in its header run exactly as the user will paste them, the key refuses a second row for one
office in one run, the value checks hold, and row level security is on with no policy.

WHERE IT RUNS. Like test_nwps_cycle_reads_migration.py: CI provides TEST_DATABASE_URL and the
database tests refuse to skip there; elsewhere they skip when it is unset. Every expected row is
written out by hand.
"""
from __future__ import annotations

import os
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MIGRATION = os.path.join(ROOT, "pipeline", "migrations", "029_nwps_office_freshness.sql")
DB_URL = os.environ.get("TEST_DATABASE_URL")
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"

needs_db = pytest.mark.skipif(
    not DB_URL and not ON_CI,
    reason="TEST_DATABASE_URL is not set — migration 029 needs a Postgres")


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
                    "only test of migration 029")
    import psycopg2
    from psycopg2.extensions import make_dsn
    admin = psycopg2.connect(DB_URL)
    admin.autocommit = True
    name = f"nwps_fresh_migration_{uuid.uuid4().hex[:12]}"
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


INSERT = ("INSERT INTO nwps_office_freshness (fetched_at, wfo, run_id, listing, newest_listed, "
          "cycle_used, source, hours_behind, age_h, failed, stale_reason, spots, spots_written) "
          "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s, %s, %s)")


def _row(fetched_at, wfo, cycle_used, hours_behind, age_h, stale, listing="ok",
         newest="2026-11-10T00:00:00Z", run_id="42781235066"):
    return (fetched_at, wfo, run_id, listing, newest, cycle_used,
            "download" if cycle_used else None, hours_behind, age_h, "[]", stale, 3, 3 if cycle_used else 0)


def test_the_check_query_text():
    assert check_query("SELECT column_name") == (
        "SELECT column_name, data_type FROM information_schema.columns\n"
        "WHERE table_schema = 'public' AND table_name = 'nwps_office_freshness'\n"
        "ORDER BY ordinal_position;")


@needs_db
def test_check_1_gives_the_fourteen_columns(db):
    assert _rows(db, check_query("SELECT column_name")) == [
        ("id", "bigint"), ("fetched_at", "timestamp with time zone"), ("wfo", "text"),
        ("run_id", "text"), ("listing", "text"),
        ("newest_listed", "timestamp with time zone"), ("cycle_used", "timestamp with time zone"),
        ("source", "text"), ("hours_behind", "real"), ("age_h", "real"), ("failed", "jsonb"),
        ("stale_reason", "text"), ("spots", "integer"), ("spots_written", "integer")]


@needs_db
def test_check_2_gives_one_row_per_run(db):
    # The 11-10 08:15Z run as three offices: box 24 h behind, ilm nothing, mfr fresh; and a
    # later run where all three are fresh.
    rows = [_row("2026-11-10T08:15:40Z", "box", "2026-11-09T00:00:00Z", 24.0, 32.3, "24 h behind"),
            _row("2026-11-10T08:15:40Z", "ilm", None, None, None, "no cycle read",
                 listing="unanswered", newest=None),
            _row("2026-11-10T08:15:40Z", "mfr", "2026-11-10T00:00:00Z", 0.0, 8.3, None),
            _row("2026-11-10T14:07:58Z", "box", "2026-11-10T00:00:00Z", 0.0, 14.1, None, run_id="42830902540"),
            _row("2026-11-10T14:07:58Z", "ilm", "2026-11-10T06:00:00Z", 0.0, 8.1, None, run_id="42830902540"),
            _row("2026-11-10T14:07:58Z", "mfr", "2026-11-10T00:00:00Z", 0.0, 14.1, None, run_id="42830902540")]
    with db.cursor() as c:
        for r in rows:
            c.execute(INSERT, r)
    got = _rows(db, "SET TIME ZONE 'UTC'; " + check_query("SELECT fetched_at, run_id"))
    as_text = [(f.isoformat(), r, n, nc, st, round(mb, 1), round(ma, 1)) for f, r, n, nc, st, mb, ma in got]
    assert as_text == [
        ("2026-11-10T14:07:58+00:00", "42830902540", 3, 0, 0, 0.0, 14.1),
        # stale: box (behind) and ilm (nothing read); no_cycle: ilm alone
        ("2026-11-10T08:15:40+00:00", "42781235066", 3, 1, 2, 24.0, 32.3)]


@needs_db
def test_the_key_refuses_a_second_row_for_one_office_in_one_run(db):
    import psycopg2
    row = _row("2026-11-10T08:15:40Z", "box", "2026-11-09T00:00:00Z", 24.0, 32.3, "24 h behind")
    with db.cursor() as c:
        c.execute(INSERT, row)
        with pytest.raises(psycopg2.errors.UniqueViolation):
            c.execute(INSERT, row)
        c.execute(INSERT, ("2026-11-10T14:07:58Z",) + row[1:])     # the next run: a new row
    assert _rows(db, "SELECT count(*) FROM nwps_office_freshness") == [(2,)]


@needs_db
def test_the_value_checks_hold(db):
    import psycopg2
    good = _row("2026-11-10T08:15:40Z", "box", "2026-11-09T00:00:00Z", 24.0, 32.3, None)
    for bad in (good[:3] + ("listed",) + good[4:],               # listing outside ok/empty/unanswered
                good[:6] + ("ftp",) + good[7:]):                  # source outside download/cache
        with db.cursor() as c, pytest.raises(psycopg2.errors.CheckViolation):
            c.execute(INSERT, bad)
    with db.cursor() as c:
        c.execute("INSERT INTO nwps_office_freshness (fetched_at, wfo, listing, spots, spots_written) "
                  "VALUES ('2026-11-10T08:15:40Z', 'sju', 'empty', 4, 0)")
    assert _rows(db, "SELECT failed::text, source FROM nwps_office_freshness WHERE wfo = 'sju'") == [
        ("[]", None)]


@needs_db
def test_row_level_security_is_on_with_no_policy(db):
    assert _rows(db, "SELECT relrowsecurity FROM pg_class "
                     "WHERE relname = 'nwps_office_freshness'") == [(True,)]
    assert _rows(db, "SELECT count(*) FROM pg_policies "
                     "WHERE tablename = 'nwps_office_freshness'") == [(0,)]
