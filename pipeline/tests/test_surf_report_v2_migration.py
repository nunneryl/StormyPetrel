"""Migration 027 (surf reports, question version 2) on a real Postgres, both check queries in its
header run exactly as the user will paste them.

WHERE IT RUNS. Like test_time_zone_migration.py: CI provides TEST_DATABASE_URL and the database
tests refuse to skip there; elsewhere they skip when it is unset.

THE FIXTURE is surf_reports as migrations 014 and 015 left it, built by running those two files,
with three version 1 reports in it. Every expected value is written out by hand.
"""
from __future__ import annotations

import os
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MIGRATIONS = os.path.join(os.path.dirname(HERE), "migrations")

DB_URL = os.environ.get("TEST_DATABASE_URL")
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"

needs_db = pytest.mark.skipif(
    not DB_URL and not ON_CI,
    reason="TEST_DATABASE_URL is not set — migration 027 needs a Postgres (CI provides one)")


def _read(name):
    with open(os.path.join(MIGRATIONS, name), encoding="utf-8") as f:
        return f.read()


def check_query(starts):
    """A check query from 027's header, without its comment markers: the text the user pastes."""
    lines = _read("027_surf_report_v2.sql").splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"--   {starts}"))
    end = next(i for i in range(start, len(lines)) if lines[i].rstrip().endswith(";"))
    return "\n".join(ln[5:] for ln in lines[start:end + 1])


CHECK_1 = "SELECT check_name"
CHECK_2 = "SELECT question_version"
SPOTS = "CREATE TABLE spots (id SERIAL PRIMARY KEY, slug TEXT UNIQUE NOT NULL);"
V1_ROWS = [  # (spot, observed hour, bucket, hash)
    (1, "2026-09-01T14:00:00+00", "waist", "aaaaaaaaaaaaaaaa"),
    (1, "2026-09-01T15:00:00+00", "chest", "aaaaaaaaaaaaaaaa"),
    (2, "2026-09-02T09:00:00+00", "knee", "bbbbbbbbbbbbbbbb"),
]


def _new_db():
    import psycopg2
    from psycopg2.extensions import make_dsn
    admin = psycopg2.connect(DB_URL)
    admin.autocommit = True
    name = f"surf_report_v2_{uuid.uuid4().hex[:12]}"
    with admin.cursor() as c:
        c.execute(f'CREATE DATABASE "{name}"')
    conn = psycopg2.connect(make_dsn(DB_URL, dbname=name))
    conn.autocommit = True
    return admin, name, conn


def _drop(admin, name, conn):
    conn.close()
    with admin.cursor() as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}"')
    admin.close()


def _fill(conn, rows=V1_ROWS):
    with conn.cursor() as c:
        c.execute(SPOTS)
        c.execute("INSERT INTO spots (slug) VALUES ('a'), ('b')")
        c.execute(_read("014_surf_reports.sql"))
        c.execute(_read("015_surf_report_revisions.sql"))
        for spot, hour, bucket, h in rows:
            c.execute("INSERT INTO surf_reports (spot_id, observed_hour, size_bucket, reporter_hash) "
                      "VALUES (%s, %s, %s, %s)", (spot, hour, bucket, h))


def _rows(conn, sql):
    with conn.cursor() as c:
        c.execute(sql)
        return c.fetchall()


@pytest.fixture()
def db():
    if not DB_URL:
        pytest.fail("on CI, TEST_DATABASE_URL must be set by tests.yml — refusing to skip the "
                    "only test of migration 027")
    admin, name, conn = _new_db()
    try:
        _fill(conn)
        with conn.cursor() as c:
            c.execute(_read("027_surf_report_v2.sql"))
        yield conn
    finally:
        _drop(admin, name, conn)


OK = [("columns added", "5", "5", True),
      ("question_version is smallint, not null, default 1", "smallint NO 1", "smallint NO 1", True),
      ("checks in place", "4", "4", True),
      ("every report so far reads version 1", "3", "3", True),
      ("no version 1 row has a sets answer", "0", "0", True)]


@needs_db
def test_check_query_1_reads_ok_on_every_row_and_again_after_a_rerun(db):
    assert _rows(db, check_query(CHECK_1)) == OK
    with db.cursor() as c:
        c.execute(_read("027_surf_report_v2.sql"))              # idempotent
    assert _rows(db, check_query(CHECK_1)) == OK


@needs_db
def test_check_query_1_cannot_read_ok_before_the_migration():
    import psycopg2
    admin, name, conn = _new_db()
    try:
        _fill(conn)
        with pytest.raises(psycopg2.errors.UndefinedColumn):
            _rows(conn, check_query(CHECK_1))
    finally:
        _drop(admin, name, conn)


@needs_db
def test_on_an_empty_table_check_query_1_still_reads_ok():
    admin, name, conn = _new_db()
    try:
        _fill(conn, rows=[])
        with conn.cursor() as c:
            c.execute(_read("027_surf_report_v2.sql"))
        rows = {r[0]: r[1:] for r in _rows(conn, check_query(CHECK_1))}
        assert rows["every report so far reads version 1"] == ("0", "0", True)
    finally:
        _drop(admin, name, conn)


@needs_db
def test_an_old_route_s_insert_reads_version_1(db):
    # The route before this change sends no question_version: the default answers for it.
    with db.cursor() as c:
        c.execute("INSERT INTO surf_reports (spot_id, observed_hour, size_bucket, reporter_hash) "
                  "VALUES (2, '2026-10-30T13:00:00+00', 'head', 'cccccccccccccccc')")
    assert _rows(db, "SELECT question_version, sets_bucket FROM surf_reports "
                     "WHERE reporter_hash = 'cccccccccccccccc'") == [(1, None)]


@needs_db
def test_the_checks_take_version_2_rows_and_refuse_the_rest(db):
    import psycopg2
    ins = ("INSERT INTO surf_reports (spot_id, observed_hour, size_bucket, reporter_hash, "
           "question_version, sets_bucket, forecast_band, forecast_basis, forecast_nwps_cycle) "
           "VALUES (2, %s, 'waist', %s, %s, %s, %s, %s, %s)")
    with db.cursor() as c:
        c.execute(ins, ("2026-10-30T13:00:00+00", "d" * 16, 2, "chest", "3-4ft", "model",
                        "2026-10-30T00:00:00+00"))
        c.execute(ins, ("2026-10-30T14:00:00+00", "d" * 16, 2, None, None, None, None))
        for hour, version, sets, basis in (("2026-10-30T15:00:00+00", 1, "chest", None),
                                           ("2026-10-30T16:00:00+00", 3, None, None),
                                           ("2026-10-30T17:00:00+00", 2, "giant", None),
                                           ("2026-10-30T18:00:00+00", 2, None, "guess")):
            with pytest.raises(psycopg2.errors.CheckViolation):
                c.execute(ins, (hour, "d" * 16, version, sets, None, basis, None))
    assert _rows(db, "SELECT question_version, sets_bucket, forecast_band, forecast_basis "
                     "FROM surf_reports WHERE reporter_hash = 'dddddddddddddddd' "
                     "ORDER BY observed_hour") == [(2, "chest", "3-4ft", "model"),
                                                   (2, None, None, None)]


@needs_db
def test_check_query_2_one_row_per_version(db):
    with db.cursor() as c:
        c.execute("INSERT INTO surf_reports (spot_id, observed_hour, size_bucket, reporter_hash, "
                  "question_version, sets_bucket, forecast_band, first_reported_at) VALUES "
                  "(2, '2026-10-30T13:00:00+00', 'waist', 'eeeeeeeeeeeeeeee', 2, 'chest', '3-4ft', "
                  "'2026-10-30T13:30:00+00')")
    rows = [r[:4] for r in _rows(db, check_query(CHECK_2))]
    # version 1: the three fixture rows, none with sets or a band; version 2: the one above
    assert rows == [(1, 3, 0, 0), (2, 1, 1, 1)]
