"""Migration 026 (spots.time_zone) on a real Postgres, and both check queries in its header
run exactly as the user will paste them.

WHERE IT RUNS. Like test_bottom_migration.py: CI provides TEST_DATABASE_URL and the database
tests refuse to skip there; elsewhere they skip when it is unset.

No expected value below is produced by calling the code under test.
"""
from __future__ import annotations

import os
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MIGRATION = os.path.join(ROOT, "pipeline", "migrations", "026_spot_time_zone.sql")

DB_URL = os.environ.get("TEST_DATABASE_URL")
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"

needs_db = pytest.mark.skipif(
    not DB_URL and not ON_CI,
    reason="TEST_DATABASE_URL is not set — migration 026 needs a Postgres "
           "(CI provides one; see test_nwps_cycle_trigger.py's docstring to run it locally)")

# The spots columns the check queries read, as 001 created them.
TABLE = """
CREATE TABLE spots (
  id BIGSERIAL PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  name TEXT,
  lng DOUBLE PRECISION,
  state TEXT
);
"""


def _sql():
    with open(MIGRATION, encoding="utf-8") as f:
        return f.read()


def check_query(starts: str) -> str:
    """A check query from the header, without its comment markers: the text the user pastes."""
    lines = _sql().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith(f"--   {starts}"))
    end = next(i for i in range(start, len(lines)) if lines[i].rstrip().endswith(";"))
    return "\n".join(ln[5:] for ln in lines[start:end + 1])


def _new_db():
    import psycopg2
    from psycopg2.extensions import make_dsn
    admin = psycopg2.connect(DB_URL)
    admin.autocommit = True
    name = f"time_zone_migration_{uuid.uuid4().hex[:12]}"
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


@pytest.fixture()
def db():
    if not DB_URL:
        pytest.fail("on CI, TEST_DATABASE_URL must be set by tests.yml — refusing to skip the "
                    "only test of migration 026")
    admin, name, conn = _new_db()
    try:
        with conn.cursor() as c:
            c.execute(TABLE)
            c.execute("INSERT INTO spots (slug, name, lng, state) VALUES "
                      "('pensacola-beach', 'Pensacola Beach', -87.14, 'Florida'), "
                      "('mexico-beach', 'Mexico Beach', -85.40, 'Florida'), "
                      "('cape-san-blas', 'Cape San Blas', -85.37, 'Florida'), "
                      "('banzai-pipeline', 'Banzai Pipeline', -158.05, 'Hawaii')")
            c.execute(_sql())
        yield conn
    finally:
        _drop(admin, name, conn)


def _rows(conn, sql):
    with conn.cursor() as c:
        c.execute(sql)
        return c.fetchall()


CHECK_1 = "SELECT check_name"
CHECK_2 = "SELECT coalesce(time_zone"
OK = [("time_zone column added", "text", "text", True),
      ("time_zone check in place", "1", "1", True),
      ("zones Postgres does not know", "0", "0", True)]


@needs_db
def test_check_query_1_reads_ok_on_every_row_and_again_after_a_rerun(db):
    assert _rows(db, check_query(CHECK_1)) == OK
    with db.cursor() as c:
        c.execute(_sql())                                   # idempotent
    assert _rows(db, check_query(CHECK_1)) == OK


@needs_db
def test_check_query_1_cannot_read_ok_before_the_migration():
    import psycopg2
    admin, name, conn = _new_db()
    try:
        with conn.cursor() as c:
            c.execute(TABLE)
        # It names the column, so before the migration it errors instead of reading ok.
        with pytest.raises(psycopg2.errors.UndefinedColumn):
            _rows(conn, check_query(CHECK_1))
    finally:
        _drop(admin, name, conn)


@needs_db
def test_the_shape_check_takes_iana_names_and_refuses_the_rest(db):
    import psycopg2
    with db.cursor() as c:
        for tz in ("America/Chicago", "America/Puerto_Rico", "America/Argentina/Buenos_Aires",
                   "Etc/GMT+5"):
            c.execute("UPDATE spots SET time_zone = %s WHERE slug = 'mexico-beach'", (tz,))
        for bad in ("EST", "PT", "America/", "/Chicago", "America/New York", ""):
            with pytest.raises(psycopg2.errors.CheckViolation):
                c.execute("UPDATE spots SET time_zone = %s WHERE slug = 'mexico-beach'", (bad,))


@needs_db
def test_check_query_1_flags_a_zone_postgres_does_not_know(db):
    with db.cursor() as c:
        c.execute("UPDATE spots SET time_zone = 'Mars/Olympus_Mons' WHERE slug = 'cape-san-blas'")
    assert _rows(db, check_query(CHECK_1))[2] == ("zones Postgres does not know", "0", "1", False)


@needs_db
def test_check_query_2_lists_each_zone_with_the_panhandle_split(db):
    """After an upload: one row per zone, the Florida Central spots named west to east; a row
    the upload missed shows as (none)."""
    with db.cursor() as c:
        c.execute("UPDATE spots SET time_zone = 'America/Chicago' "
                  "WHERE slug IN ('pensacola-beach', 'mexico-beach')")
        c.execute("UPDATE spots SET time_zone = 'America/New_York' WHERE slug = 'cape-san-blas'")
    rows = _rows(db, check_query(CHECK_2))
    assert rows == [
        ("America/Chicago", 2, "Pensacola Beach, Mexico Beach", ["Mexico Beach", "Pensacola Beach"]),
        ("(none)", 1, None, ["Banzai Pipeline"]),
        ("America/New_York", 1, None, ["Cape San Blas"]),
    ]
