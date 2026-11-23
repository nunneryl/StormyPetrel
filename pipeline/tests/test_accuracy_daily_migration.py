"""Migrations 031 and 032 on a real Postgres: accuracy_daily's shape, checks (032 adds kind
'spot_model') and the upsert re-scoring a day.

Runs where TEST_DATABASE_URL is set (CI sets it, and there this file refuses to skip), in a database
it creates and drops. See test_nwps_cycle_trigger.py for the pattern.
"""
from __future__ import annotations

import os
import uuid

import pytest

from pipeline.accuracy_score import SUMS, Totals

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MIGRATION = os.path.join(ROOT, "pipeline", "migrations", "031_accuracy_daily.sql")
MIGRATION_032 = os.path.join(ROOT, "pipeline", "migrations", "032_accuracy_daily_spot_model.sql")

DB_URL = os.environ.get("TEST_DATABASE_URL")
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"

needs_db = pytest.mark.skipif(
    not DB_URL and not ON_CI,
    reason="TEST_DATABASE_URL is not set — migration 031 needs a Postgres "
           "(CI provides one; see test_nwps_cycle_trigger.py to run it locally)")


@pytest.fixture(scope="module")
def db():
    if not DB_URL:
        pytest.fail("on CI, TEST_DATABASE_URL must be set by tests.yml — refusing to skip the "
                    "only test of migration 031")
    import psycopg2
    from psycopg2.extensions import make_dsn

    admin = psycopg2.connect(DB_URL)
    admin.autocommit = True
    name = f"accuracy_daily_{uuid.uuid4().hex[:12]}"
    with admin.cursor() as c:
        c.execute(f'CREATE DATABASE "{name}"')
    conn = psycopg2.connect(make_dsn(DB_URL, dbname=name))
    conn.autocommit = True
    try:
        with conn.cursor() as c:
            for path in (MIGRATION, MIGRATION_032):
                sql = open(path, encoding="utf-8").read()
                c.execute(sql)
                c.execute(sql)      # idempotent
        yield conn
    finally:
        conn.close()
        with admin.cursor() as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def _rows(e):
    t = Totals()
    t.add(("buoy", "ww3", "Oregon", "", 1, "hs"), "46050", 1.0 + e, 1.0, e)
    return t.rows(__import__("datetime").date(2026, 11, 14), "2026-11-15T04:47:00Z")


def upsert(conn, rows):
    """The statement PostgREST sends for accuracy_score.write."""
    from psycopg2.extras import execute_values
    cols = list(rows[0])
    with conn.cursor() as c:
        execute_values(
            c, f"INSERT INTO accuracy_daily ({', '.join(cols)}) VALUES %s "
               "ON CONFLICT (day, kind, source, region, point, lead_day, measure) DO UPDATE SET "
               + ", ".join(f"{k} = EXCLUDED.{k}" for k in cols if k not in
                           ("day", "kind", "source", "region", "point", "lead_day", "measure")),
            [tuple(r[k] for k in cols) for r in rows])


@needs_db
def test_the_columns_the_check_counts_and_rls(db):
    with db.cursor() as c:
        c.execute("SELECT count(*) FROM information_schema.columns "
                  "WHERE table_schema = 'public' AND table_name = 'accuracy_daily'")
        assert c.fetchone() == (18,)
        c.execute("SELECT relrowsecurity FROM pg_class WHERE relname = 'accuracy_daily'")
        assert c.fetchone() == (True,)


@needs_db
def test_every_field_the_scorer_writes_is_a_column(db):
    with db.cursor() as c:
        c.execute("SELECT column_name FROM information_schema.columns WHERE table_name = 'accuracy_daily'")
        assert {r[0] for r in c.fetchall()} == set(_rows(0.1)[0])


@needs_db
def test_rescoring_a_day_overwrites_it(db):
    upsert(db, _rows(0.1))
    upsert(db, _rows(0.3))
    with db.cursor() as c:
        c.execute("SELECT count(*), max(sum_e) FROM accuracy_daily")
        n, e = c.fetchone()
    assert n == 1 and abs(e - 0.3) < 1e-9


@needs_db
@pytest.mark.parametrize("field, bad", [("kind", "model"), ("source", "gfs"), ("lead_day", 4),
                                        ("measure", "wind"), ("n", 0)])
def test_the_checks(db, field, bad):
    import psycopg2
    row = {**_rows(0.1)[0], field: bad, "region": f"check-{field}"}
    with pytest.raises(psycopg2.errors.CheckViolation):
        upsert(db, [row])


@needs_db
def test_032_takes_the_scorers_per_spot_and_spot_model_rows(db):
    t = Totals()
    t.add(("spot_model", "nwps", "Florida", "", 1, "hs"), "dania-beach-pier", 1.0, 1.4, -0.4)
    t.add(("spot_model", "nwps", "Florida", "dania-beach-pier", 1, "hs"), "dania-beach-pier", 1.0, 1.4, -0.4)
    t.add(("spot", "spot", "Florida", "dania-beach-pier", 1, "hs"), "dania-beach-pier", 1.0, 1.2, -0.2)
    upsert(db, t.rows(__import__("datetime").date(2026, 11, 22), "2026-11-23T04:47:00Z"))
    with db.cursor() as c:
        c.execute("SELECT kind, point FROM accuracy_daily WHERE region = 'Florida' ORDER BY kind, point")
        assert c.fetchall() == [("spot", "dania-beach-pier"), ("spot_model", ""),
                                ("spot_model", "dania-beach-pier")]
        c.execute("SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                  "WHERE conname = 'accuracy_daily_kind_check'")
        assert c.fetchone() == ("CHECK ((kind = ANY (ARRAY['buoy'::text, 'spot'::text, 'spot_model'::text])))",)


def test_the_sums_are_the_migrations():
    sql = open(MIGRATION, encoding="utf-8").read()
    for s in SUMS:
        assert f"  {s} " in sql, s
