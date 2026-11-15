"""Migration 030 on a real Postgres: forecast_leads and model_at_buoys keep the newest issue in each
lead day's window, and the migration applies twice without error.

Driven with the statement PostgREST sends for accuracy_capture's upsert (INSERT ... ON CONFLICT DO
UPDATE ... RETURNING); a row the trigger keeps is absent from RETURNING and the statement does not
fail. Runs where TEST_DATABASE_URL is set (CI sets it, and there this file refuses to skip), in a
database it creates and drops. See test_nwps_cycle_trigger.py for the pattern.
"""
from __future__ import annotations

import os
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MIGRATION = os.path.join(ROOT, "pipeline", "migrations", "030_accuracy_capture.sql")

DB_URL = os.environ.get("TEST_DATABASE_URL")
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"

needs_db = pytest.mark.skipif(
    not DB_URL and not ON_CI,
    reason="TEST_DATABASE_URL is not set — migration 030 needs a Postgres "
           "(CI provides one; see test_nwps_cycle_trigger.py to run it locally)")

T12 = "2026-11-13 12:00+00"
T06 = "2026-11-13 06:00+00"
T18 = "2026-11-12 18:00+00"
VT = "2026-11-14 14:00+00"


@pytest.fixture(scope="module")
def db():
    if not DB_URL:
        pytest.fail("on CI, TEST_DATABASE_URL must be set by tests.yml — refusing to skip the "
                    "only test of migration 030")
    import psycopg2
    from psycopg2.extensions import make_dsn

    admin = psycopg2.connect(DB_URL)
    admin.autocommit = True
    name = f"accuracy_capture_{uuid.uuid4().hex[:12]}"
    with admin.cursor() as c:
        c.execute(f'CREATE DATABASE "{name}"')
    conn = psycopg2.connect(make_dsn(DB_URL, dbname=name))
    conn.autocommit = True
    try:
        with conn.cursor() as c:
            c.execute("CREATE TABLE spots (id SERIAL PRIMARY KEY, slug TEXT UNIQUE NOT NULL, "
                      "name TEXT NOT NULL)")
            c.execute("INSERT INTO spots (slug, name) SELECT 's' || g, 'S' || g FROM generate_series(1, 9) g")
            sql = open(MIGRATION, encoding="utf-8").read()
            c.execute(sql)
            c.execute(sql)          # idempotent
        yield conn
    finally:
        conn.close()
        with admin.cursor() as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def leads(conn, rows):
    from psycopg2.extras import execute_values
    with conn.cursor() as c:
        return execute_values(
            c,
            "INSERT INTO forecast_leads (spot_id, valid_time, lead_day, issued_at, lead_hours, "
            "face_ft, captured_at) VALUES %s ON CONFLICT (spot_id, valid_time, lead_day) DO UPDATE "
            "SET issued_at = EXCLUDED.issued_at, lead_hours = EXCLUDED.lead_hours, "
            "face_ft = EXCLUDED.face_ft, captured_at = EXCLUDED.captured_at RETURNING spot_id",
            [(*r, "2026-11-13 20:00+00") for r in rows], fetch=True)


def buoys(conn, rows):
    from psycopg2.extras import execute_values
    with conn.cursor() as c:
        return execute_values(
            c,
            "INSERT INTO model_at_buoys (buoy_id, valid_time, source, lead_day, issued_at, "
            "lead_hours, hs, captured_at) VALUES %s ON CONFLICT (buoy_id, valid_time, source, "
            "lead_day) DO UPDATE SET issued_at = EXCLUDED.issued_at, hs = EXCLUDED.hs "
            "RETURNING buoy_id",
            [(*r, "2026-11-13 20:00+00") for r in rows], fetch=True)


def one(conn, sql, args):
    with conn.cursor() as c:
        c.execute(sql, args)
        return c.fetchone()


@needs_db
def test_the_tables_have_the_columns_the_check_query_counts(db):
    with db.cursor() as c:
        c.execute("SELECT table_name, count(*) FROM information_schema.columns "
                  "WHERE table_schema = 'public' AND table_name IN ('forecast_leads', 'model_at_buoys') "
                  "GROUP BY table_name ORDER BY table_name")
        assert c.fetchall() == [("forecast_leads", 21), ("model_at_buoys", 30)]
        c.execute("SELECT relname FROM pg_class WHERE relrowsecurity AND relname IN "
                  "('forecast_leads', 'model_at_buoys') ORDER BY relname")
        assert c.fetchall() == [("forecast_leads",), ("model_at_buoys",)]


@needs_db
def test_an_older_issue_never_overwrites_a_newer_one(db):
    leads(db, [(1, VT, 1, T12, 26, 4.0)])
    assert leads(db, [(1, VT, 1, T06, 32, 9.0)]) == []
    assert one(db, "SELECT face_ft, issued_at::text FROM forecast_leads WHERE spot_id = 1", ()) == \
        (4.0, "2026-11-13 12:00:00+00")


@needs_db
def test_a_newer_issue_replaces_and_the_same_issue_rewrites(db):
    leads(db, [(2, VT, 1, T18, 44, 3.0)])
    assert len(leads(db, [(2, VT, 1, T12, 26, 4.0)])) == 1
    assert len(leads(db, [(2, VT, 1, T12, 26, 4.5)])) == 1
    assert one(db, "SELECT face_ft, lead_hours FROM forecast_leads WHERE spot_id = 2", ()) == (4.5, 26)


@needs_db
def test_one_statement_applies_what_it_may_and_does_not_fail(db):
    leads(db, [(3, VT, 1, T12, 26, 4.0)])
    returned = leads(db, [(3, VT, 1, T06, 32, 1.0),       # older: kept
                          (3, VT, 2, T18, 44, 2.0),       # another lead day: inserted
                          (4, VT, 1, T06, 32, 3.0)])      # another spot: inserted
    assert sorted(r[0] for r in returned) == [3, 4]


@needs_db
def test_lead_day_and_source_are_checked(db):
    import psycopg2
    with pytest.raises(psycopg2.errors.CheckViolation):
        leads(db, [(5, VT, 0, T12, 2, 1.0)])               # day 0 is forecasts' own
    with pytest.raises(psycopg2.errors.CheckViolation):
        buoys(db, [("46221", VT, "gfs", 0, T12, 2, 1.0)])
    with pytest.raises(psycopg2.errors.CheckViolation):
        buoys(db, [("46221", VT, "ww3", 4, T12, 98, 1.0)])


@needs_db
def test_model_at_buoys_keeps_the_newest_issue_per_source(db):
    buoys(db, [("46221", VT, "ww3", 1, T12, 26, 2.0), ("46221", VT, "nwps", 1, T12, 26, 1.5)])
    assert buoys(db, [("46221", VT, "ww3", 1, T06, 32, 9.0)]) == []
    assert len(buoys(db, [("46221", VT, "nwps", 1, T12, 26, 1.6)])) == 1
    assert one(db, "SELECT hs FROM model_at_buoys WHERE source = 'ww3'", ()) == (2.0,)
    assert one(db, "SELECT hs FROM model_at_buoys WHERE source = 'nwps'", ()) == (1.6,)


@needs_db
def test_a_lead_row_goes_with_its_spot(db):
    leads(db, [(9, VT, 1, T12, 26, 1.0)])
    with db.cursor() as c:
        c.execute("DELETE FROM spots WHERE id = 9")
    assert one(db, "SELECT count(*) FROM forecast_leads WHERE spot_id = 9", ()) == (0,)
