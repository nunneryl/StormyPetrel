"""Migration 019's trigger, run on a real Postgres: an older NWPS cycle never overwrites a newer one.

WHY A DATABASE. The rule lives in the database precisely so no code path can break it — a
failed listing, a download that falls back, a replayed artifact, a manual run, even a
legitimate fallback to yesterday. A test of Python could not show that; a trigger nothing
runs is a comment. So this file creates a throwaway database, gives it a `forecasts` table
with the real conflict key, applies pipeline/migrations/019_forecasts_nwps_cycle.sql exactly
as shipped, and drives it with the statement PostgREST sends for an upsert:

    INSERT ... ON CONFLICT (spot_id, valid_time, source) DO UPDATE SET ... RETURNING ...

RETURNING is what PostgREST hands back to db_import, so its row count is asserted too: a row
the trigger keeps must be absent from it, and the statement must not fail.

WHERE IT RUNS. CI provides a Postgres service and TEST_DATABASE_URL (see tests.yml), and on
CI this file REFUSES to skip. Anywhere else it skips, with that reason, when
TEST_DATABASE_URL is unset — point it at any Postgres you can create a database on:

    TEST_DATABASE_URL=postgresql://postgres@localhost:5432/postgres python -m pytest ...

It never touches an existing table: everything happens in a database it creates and drops.
"""
from __future__ import annotations

import os
import re
import uuid

import pytest

from pipeline.tests.test_ci_workflow import yaml_code

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MIGRATION = os.path.join(ROOT, "pipeline", "migrations", "019_forecasts_nwps_cycle.sql")
TESTS_YML = os.path.join(ROOT, ".github", "workflows", "tests.yml")

DB_URL = os.environ.get("TEST_DATABASE_URL")
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"

needs_db = pytest.mark.skipif(
    not DB_URL and not ON_CI,
    reason="TEST_DATABASE_URL is not set — the nwps_cycle trigger needs a Postgres "
           "(CI provides one; see this file's docstring to run it locally)")

# The forecasts columns this rule touches, with 001's conflict key. The rest of the real table
# is ~40 more measurement columns, none of which the trigger reads.
TABLE = """
CREATE TABLE forecasts (
  id BIGSERIAL PRIMARY KEY,
  spot_id INTEGER,
  valid_time TIMESTAMPTZ NOT NULL,
  stars DOUBLE PRECISION,
  source TEXT DEFAULT 'nwps',
  fetched_at TIMESTAMPTZ DEFAULT now(),
  UNIQUE(spot_id, valid_time, source)
);
"""

T12 = "2026-09-22 12:00+00"     # 09-22 12Z
T06 = "2026-09-22 06:00+00"     # 09-22 06Z
T00 = "2026-09-22 00:00+00"     # 09-22 00Z
Y18 = "2026-09-21 18:00+00"     # 09-21 18Z — yesterday's, the one sju fell back to


# --------------------------------------------------------------------------- #
# The throwaway database                                                       #
# --------------------------------------------------------------------------- #

@pytest.fixture(scope="module")
def db():
    if not DB_URL:
        pytest.fail("on CI, TEST_DATABASE_URL must be set by tests.yml — refusing to skip the "
                    "only test of migration 019's trigger")
    import psycopg2
    from psycopg2.extensions import make_dsn

    admin = psycopg2.connect(DB_URL)
    admin.autocommit = True
    name = f"nwps_cycle_trigger_{uuid.uuid4().hex[:12]}"
    with admin.cursor() as c:
        c.execute(f'CREATE DATABASE "{name}"')
    conn = psycopg2.connect(make_dsn(DB_URL, dbname=name))
    conn.autocommit = True
    try:
        with conn.cursor() as c:
            c.execute(TABLE)
            c.execute(open(MIGRATION, encoding="utf-8").read())
        yield conn
    finally:
        conn.close()
        with admin.cursor() as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def upsert(conn, rows):
    """The statement PostgREST sends for db_import's upsert. Returns the RETURNING rows."""
    from psycopg2.extras import execute_values
    with conn.cursor() as c:
        return execute_values(
            c,
            "INSERT INTO forecasts (spot_id, valid_time, source, stars, nwps_cycle) VALUES %s "
            "ON CONFLICT (spot_id, valid_time, source) DO UPDATE SET "
            "stars = EXCLUDED.stars, nwps_cycle = EXCLUDED.nwps_cycle "
            "RETURNING spot_id, valid_time, source",
            rows, fetch=True)


def row(conn, spot_id, vt, source="nwps"):
    with conn.cursor() as c:
        c.execute("SELECT stars, nwps_cycle::text FROM forecasts "
                  "WHERE spot_id = %s AND valid_time = %s AND source = %s", (spot_id, vt, source))
        return c.fetchone()


VT = "2026-09-23 00:00+00"


# --------------------------------------------------------------------------- #
# 1 — the rule, case by case                                                   #
# --------------------------------------------------------------------------- #

@needs_db
def test_the_migration_adds_the_column_and_the_trigger(db):
    with db.cursor() as c:
        c.execute("SELECT data_type FROM information_schema.columns "
                  "WHERE table_name = 'forecasts' AND column_name = 'nwps_cycle'")
        assert c.fetchone() == ("timestamp with time zone",)
        c.execute("SELECT count(*) FROM pg_trigger "
                  "WHERE tgname = 'forecasts_keep_newest_nwps_cycle' AND NOT tgisinternal")
        assert c.fetchone() == (1,)


@needs_db
def test_a_row_written_before_the_migration_takes_its_first_stamp(db):
    upsert(db, [(1, VT, "nwps", 1.0, None)])
    assert len(upsert(db, [(1, VT, "nwps", 2.0, T06)])) == 1
    assert row(db, 1, VT) == (2.0, "2026-09-22 06:00:00+00")


@needs_db
def test_an_older_cycle_never_overwrites_a_newer_one(db):
    upsert(db, [(2, VT, "nwps", 3.0, T12)])
    assert upsert(db, [(2, VT, "nwps", 1.0, Y18)]) == []
    assert row(db, 2, VT) == (3.0, "2026-09-22 12:00:00+00")


@needs_db
def test_an_unstamped_write_cannot_overwrite_a_stamped_row(db):
    upsert(db, [(3, VT, "nwps", 3.0, T12)])
    assert upsert(db, [(3, VT, "nwps", 1.0, None)]) == []
    assert row(db, 3, VT) == (3.0, "2026-09-22 12:00:00+00")


@needs_db
def test_the_same_cycle_may_rewrite_its_own_rows(db):
    """Re-rating one cycle — a newer WW3 or HRRR join, a new tide, a new face correction —
    is normal and must keep working."""
    upsert(db, [(4, VT, "nwps", 3.0, T12)])
    assert len(upsert(db, [(4, VT, "nwps", 3.5, T12)])) == 1
    assert row(db, 4, VT) == (3.5, "2026-09-22 12:00:00+00")


@needs_db
def test_a_newer_cycle_replaces_an_older_one(db):
    upsert(db, [(5, VT, "nwps", 1.0, T06)])
    assert len(upsert(db, [(5, VT, "nwps", 2.0, T12)])) == 1
    assert row(db, 5, VT) == (2.0, "2026-09-22 12:00:00+00")


@needs_db
def test_other_sources_are_not_governed_by_the_rule(db):
    upsert(db, [(6, VT, "ecmwf", 1.0, T12)])
    assert len(upsert(db, [(6, VT, "ecmwf", 2.0, None)])) == 1
    assert row(db, 6, VT, "ecmwf") == (2.0, None)


@needs_db
def test_an_update_that_leaves_the_cycle_alone_still_applies(db):
    upsert(db, [(7, VT, "nwps", 1.0, T12)])
    with db.cursor() as c:
        c.execute("UPDATE forecasts SET stars = 4.0 WHERE spot_id = 7")
        assert c.rowcount == 1
    assert row(db, 7, VT) == (4.0, "2026-09-22 12:00:00+00")


# --------------------------------------------------------------------------- #
# 2 — one statement, as db_import sends it                                     #
# --------------------------------------------------------------------------- #

@needs_db
def test_one_upsert_applies_what_it_may_skips_the_rest_and_does_not_fail(db):
    upsert(db, [(8, VT, "nwps", 3.0, T12), (9, VT, "nwps", 3.0, T12), (10, VT, "nwps", 1.0, None)])
    returned = upsert(db, [
        (8, VT, "nwps", 1.0, Y18),        # older than 12Z  -> kept
        (9, VT, "nwps", 3.9, T12),        # same cycle      -> applied
        (10, VT, "nwps", 2.0, T00),       # legacy row      -> applied
        (11, VT, "nwps", 5.0, Y18),       # new row         -> inserted
    ])
    assert sorted(r[0] for r in returned) == [9, 10, 11]
    assert [row(db, s, VT) for s in (8, 9, 10, 11)] == [
        (3.0, "2026-09-22 12:00:00+00"), (3.9, "2026-09-22 12:00:00+00"),
        (2.0, "2026-09-22 00:00:00+00"), (5.0, "2026-09-21 18:00:00+00")]


@needs_db
def test_the_sju_fallback_now_leaves_the_newer_cycle_in_place(db):
    """09-22 22:43: the listing timed out and the run wrote 09-21 18Z over sju. Replayed:
    three hours the 09-22 cycles already hold keep them; the one hour only the old cycle
    covers is written, because nothing newer holds it."""
    upsert(db, [(12, "2026-09-22 03:00+00", "nwps", 2.0, T00),
                (12, "2026-09-22 09:00+00", "nwps", 2.0, T06),
                (12, "2026-09-24 00:00+00", "nwps", 2.0, T06)])
    returned = upsert(db, [(12, "2026-09-21 20:00+00", "nwps", 1.0, Y18),
                           (12, "2026-09-22 03:00+00", "nwps", 1.0, Y18),
                           (12, "2026-09-22 09:00+00", "nwps", 1.0, Y18),
                           (12, "2026-09-24 00:00+00", "nwps", 1.0, Y18)])
    assert len(returned) == 1
    assert row(db, 12, "2026-09-21 20:00+00") == (1.0, "2026-09-21 18:00:00+00")
    assert row(db, 12, "2026-09-22 03:00+00") == (2.0, "2026-09-22 00:00:00+00")
    assert row(db, 12, "2026-09-22 09:00+00") == (2.0, "2026-09-22 06:00:00+00")
    assert row(db, 12, "2026-09-24 00:00+00") == (2.0, "2026-09-22 06:00:00+00")


@needs_db
def test_a_writer_that_does_not_send_the_column_still_writes(db):
    """THE DEPLOY WINDOW. The migration runs before the code that sends nwps_cycle is merged,
    so for a while the OLD db_import writes without the column. Every one of those writes
    must still land — on unstamped rows and, after a rollback, on stamped ones — or the
    migrate-first order would freeze the forecasts it was meant to protect."""
    from psycopg2.extras import execute_values
    upsert(db, [(13, VT, "nwps", 1.0, None), (14, VT, "nwps", 1.0, T12)])
    with db.cursor() as c:
        returned = execute_values(
            c,
            "INSERT INTO forecasts (spot_id, valid_time, source, stars) VALUES %s "
            "ON CONFLICT (spot_id, valid_time, source) DO UPDATE SET stars = EXCLUDED.stars "
            "RETURNING spot_id",
            [(13, VT, "nwps", 2.0), (14, VT, "nwps", 2.0)], fetch=True)
    assert sorted(r[0] for r in returned) == [13, 14]
    assert row(db, 13, VT) == (2.0, None)
    assert row(db, 14, VT) == (2.0, "2026-09-22 12:00:00+00")


@needs_db
def test_the_migration_can_be_run_again(db):
    with db.cursor() as c:
        c.execute(open(MIGRATION, encoding="utf-8").read())
        c.execute("SELECT count(*) FROM pg_trigger "
                  "WHERE tgname = 'forecasts_keep_newest_nwps_cycle' AND NOT tgisinternal")
        assert c.fetchone() == (1,)


# --------------------------------------------------------------------------- #
# 3 — CI really runs this file                                                 #
# --------------------------------------------------------------------------- #

def test_on_ci_without_a_database_this_file_fails_rather_than_skips():
    """The skip is for a laptop without Postgres. On CI the same condition is a broken
    workflow, and must read as red — run in a child process so this file can watch it."""
    import subprocess
    import sys
    env = {k: v for k, v in os.environ.items() if k != "TEST_DATABASE_URL"}
    env["GITHUB_ACTIONS"] = "true"
    p = subprocess.run(
        [sys.executable, "-m", "pytest", __file__, "-q", "-p", "no:cacheprovider",
         "-k", "test_the_migration_adds_the_column_and_the_trigger"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300)
    assert p.returncode != 0, p.stdout[-2000:]
    assert "refusing to skip" in p.stdout, p.stdout[-2000:]


def test_ci_gives_the_python_job_a_postgres_and_its_url():
    """Without the service, the tests above would skip — or, on CI, fail. Pinned here so the
    service cannot be dropped from tests.yml without a red check saying why it was there."""
    text = yaml_code(open(TESTS_YML, encoding="utf-8").read())
    python_job = text.split("\n  python:", 1)[1].split("\n  frontend:", 1)[0]
    assert re.search(r"^\s+services:\s*$", python_job, re.M), "no services block in the python job"
    assert re.search(r"^\s+image:\s*postgres:\d+", python_job, re.M), "no postgres service image"
    assert re.search(r"^\s+TEST_DATABASE_URL:\s*postgresql://", python_job, re.M), (
        "the python job does not hand TEST_DATABASE_URL to the suite")
