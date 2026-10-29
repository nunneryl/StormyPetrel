"""Migration 026 (spots.time_zone and its backfill) on a real Postgres, both check queries in its
header run exactly as the user will paste them, and the backfill's list held to the roster.

WHERE IT RUNS. Like test_bottom_migration.py: CI provides TEST_DATABASE_URL and the database
tests refuse to skip there; elsewhere they skip when it is unset. The test of the list against
the roster runs everywhere.

THE FIXTURE is the spots table as db_import fills it: one row per roster spot with
coordinates, slug from db_import._slugify(name), state from region_hint. No expected value
below is produced by calling the code under test (the migration): the counts are written out
from the roster.
"""
from __future__ import annotations

import json
import os
import re
import uuid

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MIGRATION = os.path.join(ROOT, "pipeline", "migrations", "026_spot_time_zone.sql")
ROSTER = os.path.join(ROOT, "pipeline", "spots_enriched.json")

DB_URL = os.environ.get("TEST_DATABASE_URL")
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"

needs_db = pytest.mark.skipif(
    not DB_URL and not ON_CI,
    reason="TEST_DATABASE_URL is not set — migration 026 needs a Postgres "
           "(CI provides one; see test_nwps_cycle_trigger.py's docstring to run it locally)")

# The spots columns the migration and its check queries read, as 001 created them.
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


def _roster_rows():
    """(slug, name, lng, state, zone) for every roster spot with coordinates."""
    from pipeline.db_import import _slugify
    with open(ROSTER, encoding="utf-8") as f:
        roster = json.load(f)
    return [(_slugify(s["name"]), s["name"], s["lng"], s.get("region_hint"), s["time_zone"])
            for s in roster if s.get("name") and s.get("lat") is not None and s.get("lng") is not None]


def test_the_backfill_is_every_roster_spot_s_zone():
    """The UPDATE's list is exactly the roster's (slug, zone) pairs: 648, one per slug, and
    it holds every spot db_import uploads (the 646 valid ones)."""
    listed = re.findall(r"^  \('([a-z0-9-]+)', '([A-Za-z_/]+)'\)", _sql(), re.M)
    assert len(listed) == 648 == len({s for s, _ in listed})
    assert sorted(listed) == sorted((r[0], r[4]) for r in _roster_rows())
    with open(ROSTER, encoding="utf-8") as f:
        roster = json.load(f)
    invalid = sorted(s["name"] for s in roster if s.get("is_valid_surf_spot") is False)
    assert invalid == ["Lewes Street Surf Beach", "Seal Beach, California"]


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


def _fill(conn, extra=()):
    with conn.cursor() as c:
        c.execute(TABLE)
        for slug, name, lng, state, _zone in list(_roster_rows()) + list(extra):
            c.execute("INSERT INTO spots (slug, name, lng, state) VALUES (%s, %s, %s, %s)",
                      (slug, name, lng, state))


@pytest.fixture()
def db():
    if not DB_URL:
        pytest.fail("on CI, TEST_DATABASE_URL must be set by tests.yml — refusing to skip the "
                    "only test of migration 026")
    admin, name, conn = _new_db()
    try:
        _fill(conn)
        with conn.cursor() as c:
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
      ("spots without a zone", "0", "0", True),
      ("spots with a zone", "648", "648", True),
      ("zones Postgres does not know", "0", "0", True),
      ("Florida spots in Central time", "7", "7", True),
      ("Texas spots in Central time", "12", "12", True),
      ("Hawaii spots in Hawaii time", "55", "55", True),
      ("Puerto Rico spots in Atlantic time", "24", "24", True)]


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
        _fill(conn)
        # It names the column, so before the migration it errors instead of reading ok.
        with pytest.raises(psycopg2.errors.UndefinedColumn):
            _rows(conn, check_query(CHECK_1))
    finally:
        _drop(admin, name, conn)


@needs_db
def test_a_row_the_roster_does_not_hold_is_reported_without_a_zone():
    admin, name, conn = _new_db()
    try:
        _fill(conn, extra=[("not-in-roster", "Nowhere", -80.0, "Florida", None)])
        with conn.cursor() as c:
            c.execute(_sql())
        rows = {r[0]: r[1:] for r in _rows(conn, check_query(CHECK_1))}
        assert rows["spots without a zone"] == ("0", "1", False)
        assert rows["spots with a zone"] == ("649", "648", False)
    finally:
        _drop(admin, name, conn)


@needs_db
def test_the_backfill_never_overwrites_a_zone_already_there():
    """Mexico Beach already holding America/New_York keeps it: Florida then has 6 Central
    spots, not 7."""
    admin, name, conn = _new_db()
    try:
        _fill(conn)
        with conn.cursor() as c:
            c.execute("ALTER TABLE spots ADD COLUMN time_zone TEXT")
            c.execute("UPDATE spots SET time_zone = 'America/New_York' WHERE slug = 'mexico-beach'")
            c.execute(_sql())
        assert _rows(conn, "SELECT time_zone FROM spots WHERE slug = 'mexico-beach'") == [("America/New_York",)]
        rows = {r[0]: r[1:] for r in _rows(conn, check_query(CHECK_1))}
        assert rows["Florida spots in Central time"] == ("7", "6", False)
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
    rows = {r[0]: r[1:] for r in _rows(db, check_query(CHECK_1))}
    assert rows["zones Postgres does not know"] == ("0", "1", False)


@needs_db
def test_check_query_2_lists_the_five_zones_with_the_panhandle_split(db):
    """One row per zone, most spots first: 315 New York, 235 Los Angeles, 55 Honolulu, 24
    Puerto Rico, 19 Chicago, the last naming the seven Florida panhandle spots west to east."""
    rows = [r[:3] for r in _rows(db, check_query(CHECK_2))]
    assert rows == [
        ("America/New_York", 315, None),
        ("America/Los_Angeles", 235, None),
        ("Pacific/Honolulu", 55, None),
        ("America/Puerto_Rico", 24, None),
        ("America/Chicago", 19, "Pensacola Beach, Navarre Beach, Okaloosa Island Pier, "
                                "Destin Jetty, Panama City Beach Pier, St Andrews Park, Mexico Beach"),
    ]
    with db.cursor() as c:
        c.execute("UPDATE spots SET time_zone = NULL WHERE slug = 'cape-san-blas'")
    assert _rows(db, check_query(CHECK_2))[-1][:3] == ("(none)", 1, None)
