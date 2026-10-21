"""Indiatlantic -> Indialantic and Ventor Pier -> Ventnor Pier, across the roster, the data files
keyed by slug, the site's redirects, and migration 025 on a real Postgres.

Every expected value is written out: the names, the slugs, the texts. The one value taken from
code is the import's own verdict on the migration's description and signature
(description_blank_reason), which is the thing the migration has to satisfy.

WHERE THE DATABASE TESTS RUN. As test_tide_provenance_migration.py: CI provides a Postgres and
TEST_DATABASE_URL and the tests refuse to skip there; elsewhere they skip without it.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "pipeline" / "migrations" / "025_rename_indialantic_ventnor.sql"
DB_URL = os.environ.get("TEST_DATABASE_URL")
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"
needs_db = pytest.mark.skipif(
    not DB_URL and not ON_CI,
    reason="TEST_DATABASE_URL is not set — migration 025 needs a Postgres")

OLD = {"indiatlantic": "Indiatlantic", "ventor-pier": "Ventor Pier"}
NEW = {"indialantic": "Indialantic", "ventnor-pier": "Ventnor Pier"}
TEXT = {
    "indialantic": "Indialantic is a surf spot in Florida. It faces east, and its optimal swell "
                   "direction is east. Offshore winds blow from the west.",
    "ventnor-pier": "Facing southeast, Ventnor Pier is a beach break in New Jersey. The optimal "
                    "swell comes from the southeast, and the offshore wind is from the northwest.",
}
# The files the pipeline reads by name or slug. Historical reports and run outputs (docs/*.md,
# scripts/*_out.json) record what a past run saw and keep the old names.
RUNTIME_FILES = ("pipeline/data/llm_spots.json", "pipeline/spots_enriched.json",
                 "pipeline/data/spot_orientations.json", "pipeline/data/spot_descriptions.json",
                 "pipeline/data/spot_break_research_summary.json",
                 "scripts/nwps_okx_assignments.json", "scripts/find_missing_roster_spots.sql",
                 ".github/workflows/sw1-raycast.yml")


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8")


@pytest.mark.parametrize("rel", RUNTIME_FILES)
def test_no_runtime_file_keeps_an_old_name_or_slug(rel):
    text = _read(rel)
    for word in ("Indiatlantic", "indiatlantic", "Ventor Pier", "ventor-pier"):
        assert word not in text, (rel, word)


def test_the_roster_has_each_new_name_once():
    names = [s["name"] for s in json.loads(_read("pipeline/spots_enriched.json"))]
    assert names.count("Indialantic") == 1 and names.count("Ventnor Pier") == 1
    llm = _read("pipeline/data/llm_spots.json")
    assert llm.count('"name": "Indialantic"') == 1 and llm.count('"name": "Ventnor Pier"') == 1


def test_the_slugs_are_the_new_ones():
    from pipeline.enrich import _slug_for
    assert {_slug_for(n): n for n in NEW.values()} == NEW
    orient = json.loads(_read("pipeline/data/spot_orientations.json"))
    for slug, name in NEW.items():
        assert orient["orientations"][slug]["name"] == name


def test_the_description_cache_and_the_migration_carry_the_same_texts():
    cache = json.loads(_read("pipeline/data/spot_descriptions.json"))
    rows = {slug: (text, sig) for slug, text, sig in re.findall(
        r"\('(?:indiatlantic|ventor-pier)', '([a-z-]+)', '[^']+',\s*'([^']+)',\s*"
        r"'(v2:[0-9a-f]{16})'\)", MIGRATION.read_text())}
    assert set(rows) == set(NEW)
    for slug, text in TEXT.items():
        assert cache[slug]["description"] == text == rows[slug][0], slug
        assert cache[slug]["signature"] == rows[slug][1], slug


def test_the_import_keeps_the_migrations_description():
    """db_import blanks a description whose signature no longer matches the roster. The pair the
    migration writes must pass that check for the renamed spot, or the next run blanks it."""
    from pipeline import db_import
    cache = json.loads(_read("pipeline/data/spot_descriptions.json"))
    roster = {s["name"]: s for s in json.loads(_read("pipeline/spots_enriched.json"))}
    for slug, name in NEW.items():
        rec = db_import._spot_record(roster[name])
        assert rec["slug"] == slug
        base = dict(rec, description=cache[slug]["description"],
                    description_signature=cache[slug]["signature"])
        assert db_import.description_blank_reason(rec, base) is None, slug


def test_the_old_urls_redirect():
    cfg = _read("frontend/next.config.js")
    assert "{ source: '/spot/indiatlantic', destination: '/spot/indialantic', permanent: true }" in cfg
    assert "{ source: '/spot/ventor-pier', destination: '/spot/ventnor-pier', permanent: true }" in cfg


# --------------------------------------------------------------------------- #
# Migration 025 on a throwaway database                                         #
# --------------------------------------------------------------------------- #
# The four tables it touches, with the live foreign keys: forecasts and surf_reports cascade on
# spots(id) (001, 014); cams.spot_slug references spots(slug), ON DELETE SET NULL as
# docs/spot_delete_workflow.sql left it, and no ON UPDATE.
TABLES = """
CREATE TABLE spots (id SERIAL PRIMARY KEY, slug TEXT UNIQUE NOT NULL, name TEXT,
                    description TEXT, description_signature TEXT);
CREATE TABLE forecasts (id SERIAL PRIMARY KEY,
                        spot_id INTEGER REFERENCES spots(id) ON DELETE CASCADE,
                        valid_time TIMESTAMPTZ NOT NULL);
CREATE TABLE surf_reports (id BIGSERIAL PRIMARY KEY,
                           spot_id INTEGER NOT NULL REFERENCES spots(id) ON DELETE CASCADE,
                           observed_hour TIMESTAMPTZ NOT NULL, reporter_hash TEXT NOT NULL,
                           CONSTRAINT surf_reports_unique_report
                             UNIQUE (spot_id, observed_hour, reporter_hash));
CREATE TABLE cams (id SERIAL PRIMARY KEY,
                   spot_slug TEXT REFERENCES spots(slug) ON DELETE SET NULL,
                   cam_name TEXT NOT NULL, UNIQUE (spot_slug, cam_name));
"""
H1, H2 = "2026-10-21T14:00:00+00:00", "2026-10-21T15:00:00+00:00"


@pytest.fixture
def conn():
    if not DB_URL:
        pytest.fail("on CI, TEST_DATABASE_URL must be set — refusing to skip migration 025")
    import psycopg2
    from psycopg2.extensions import make_dsn
    admin = psycopg2.connect(DB_URL)
    admin.autocommit = True
    name = f"rename_migration_{uuid.uuid4().hex[:12]}"
    with admin.cursor() as c:
        c.execute(f'CREATE DATABASE "{name}"')
    db = psycopg2.connect(make_dsn(DB_URL, dbname=name))
    db.autocommit = True
    with db.cursor() as c:
        c.execute(TABLES)
    try:
        yield db
    finally:
        db.close()
        with admin.cursor() as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def _q(conn, sql, args=()):
    with conn.cursor() as c:
        c.execute(sql, args)
        return c.fetchall() if c.description else None


def _spot(conn, slug, name):
    return _q(conn, "INSERT INTO spots (slug, name, description, description_signature) "
                    "VALUES (%s, %s, 'old text', 'v2:0000000000000000') RETURNING id",
              (slug, name))[0][0]


def _migrate(conn):
    _q(conn, MIGRATION.read_text())


def _state(conn):
    return _q(conn, "SELECT id, slug, name, description, description_signature FROM spots "
                    "ORDER BY slug")


@needs_db
def test_the_rename_keeps_the_id_forecasts_reports_and_cam(conn):
    ids = {slug: _spot(conn, slug, name) for slug, name in OLD.items()}
    other = _spot(conn, "vero-pier", "Vero Pier")
    for i in ids.values():
        _q(conn, "INSERT INTO forecasts (spot_id, valid_time) VALUES (%s, %s)", (i, H1))
        _q(conn, "INSERT INTO surf_reports (spot_id, observed_hour, reporter_hash) "
                 "VALUES (%s, %s, 'r1')", (i, H1))
    _q(conn, "INSERT INTO cams (spot_slug, cam_name) VALUES ('ventor-pier', 'Pier cam')")
    _migrate(conn)
    assert _state(conn) == [
        (ids["indiatlantic"], "indialantic", "Indialantic", TEXT["indialantic"],
         "v2:f6bfe509bd2e9a55"),
        (ids["ventor-pier"], "ventnor-pier", "Ventnor Pier", TEXT["ventnor-pier"],
         "v2:d3366e883d461d37"),
        (other, "vero-pier", "Vero Pier", "old text", "v2:0000000000000000"),
    ]
    assert _q(conn, "SELECT spot_id FROM forecasts ORDER BY spot_id") == \
        sorted([(i,) for i in ids.values()])
    assert _q(conn, "SELECT spot_id FROM surf_reports ORDER BY spot_id") == \
        sorted([(i,) for i in ids.values()])
    assert _q(conn, "SELECT spot_slug, cam_name FROM cams") == [("ventnor-pier", "Pier cam")]


@needs_db
def test_a_row_the_pipeline_inserted_first_gives_its_reports_to_the_original(conn):
    old = _spot(conn, "indiatlantic", "Indiatlantic")
    new = _spot(conn, "indialantic", "Indialantic")
    _q(conn, "INSERT INTO surf_reports (spot_id, observed_hour, reporter_hash) VALUES "
             "(%s, %s, 'r1'), (%s, %s, 'r1'), (%s, %s, 'r2')", (old, H1, new, H1, new, H2))
    _q(conn, "INSERT INTO forecasts (spot_id, valid_time) VALUES (%s, %s)", (new, H2))
    _migrate(conn)
    assert [r[:3] for r in _state(conn)] == [(old, "indialantic", "Indialantic")]
    # r1 at H1 was already on the original; r2 at H2 moved; the repeat went with the row.
    assert _q(conn, "SELECT spot_id, observed_hour, reporter_hash FROM surf_reports "
                    "ORDER BY observed_hour") == [
        (old, _q(conn, "SELECT %s::timestamptz", (H1,))[0][0], "r1"),
        (old, _q(conn, "SELECT %s::timestamptz", (H2,))[0][0], "r2")]
    assert _q(conn, "SELECT count(*) FROM forecasts") == [(0,)]


@needs_db
def test_running_it_twice_changes_nothing_the_second_time(conn):
    for slug, name in OLD.items():
        _spot(conn, slug, name)
    _migrate(conn)
    once = _state(conn)
    _migrate(conn)
    assert _state(conn) == once
    assert [r[1] for r in once] == ["indialantic", "ventnor-pier"]


@needs_db
def test_it_is_a_no_op_where_neither_spot_exists(conn):
    _spot(conn, "vero-pier", "Vero Pier")
    _migrate(conn)
    assert [r[1:3] for r in _state(conn)] == [("vero-pier", "Vero Pier")]
