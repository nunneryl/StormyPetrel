"""Migration 022 on a real Postgres, and its lists held to config's.

Like test_break_type_migration.py: a throwaway database with a spots table shaped as 018 left
it, filled the way 018's header records the live table (mid 365, low 62, high 9, all 8 —
each stamped 'unattributed' by 018's backfill — and 202 with no preference), then
pipeline/migrations/022_tide_preference_provenance.sql applied exactly as shipped and the
check query from its header run exactly as the user will.

WHERE IT RUNS. CI provides a Postgres service and TEST_DATABASE_URL (see tests.yml), and on
CI the database tests REFUSE to skip. Anywhere else they skip, with that reason, when
TEST_DATABASE_URL is unset. The tests that read the migration's text run everywhere.
"""
from __future__ import annotations

import os
import re
import uuid

import pytest

from pipeline import config
from pipeline.config import tide_preference_fields

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MIGRATION = os.path.join(ROOT, "pipeline", "migrations", "022_tide_preference_provenance.sql")

DB_URL = os.environ.get("TEST_DATABASE_URL")
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"

needs_db = pytest.mark.skipif(
    not DB_URL and not ON_CI,
    reason="TEST_DATABASE_URL is not set — migration 022 needs a Postgres "
           "(CI provides one; see test_nwps_cycle_trigger.py's docstring to run it locally)")

# The spots columns 022 touches, as 001 and 018 left them, with 018's two constraints.
TABLE = """
CREATE TABLE spots (
  id BIGSERIAL PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  name TEXT,
  tide_preference TEXT,
  tide_preference_source TEXT,
  CONSTRAINT spots_tide_preference_check CHECK (
    tide_preference IS NULL OR tide_preference IN (
      'low', 'low_mid', 'mid', 'mid_high', 'high', 'all', 'unknown')),
  CONSTRAINT spots_tide_preference_source_check CHECK (
    tide_preference_source IS NULL OR tide_preference_source IN (
      'researched', 'scraped', 'derived_from_break_type', 'unattributed', 'unknown'))
);
"""

# 018's header, live rows by stored value: 365 + 62 + 9 + 8 = 444 values, 444 + 202 = 646.
LIVE = (("mid", 365), ("low", 62), ("high", 9), ("all", 8), (None, 202))

# Written out, not read from config.
LADDER_IN_RANK_ORDER = ("researched", "scraped", "model_recall", "derived_from_break_type",
                        "unattributed")
RANKS = {"researched": 5, "scraped": 4, "model_recall": 3, "derived_from_break_type": 2,
         "unattributed": 1}
URL = "https://www.surf-forecast.com/breaks/Rincon"
QUOTE = "best around low tide"


def _sql():
    with open(MIGRATION, encoding="utf-8") as f:
        return f.read()


def check_query() -> str:
    """The CHECK QUERY block of 022's header, comment markers removed."""
    lines = _sql().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("--   SELECT check_name"))
    end = next(i for i in range(start, len(lines)) if lines[i].rstrip().endswith(";"))
    return "\n".join(ln[5:] for ln in lines[start:end + 1])


def _code(sql):
    return re.sub(r"--[^\n]*", "", sql)


def _constraint(name):
    m = re.search(rf"ADD CONSTRAINT {name} CHECK \((.*?)\);", _code(_sql()), re.S)
    assert m, name
    return m.group(1)


def _in_list(name):
    lst = re.search(r"IN \((.*?)\)", _constraint(name), re.S)
    return tuple(re.findall(r"'([^']*)'", lst.group(1)))


# --------------------------------------------------------------------------- #
# The migration's text, held to config                                         #
# --------------------------------------------------------------------------- #

def test_the_value_list_is_configs():
    assert _in_list("spots_tide_preference_check") == config.TIDE_PREFERENCE_VALUES == (
        "low", "low_mid", "mid", "mid_high", "high", "all", "unknown")


def test_the_source_list_is_the_ladder_in_rank_order_then_unknown():
    assert config.TIDE_SOURCE_RANK == RANKS
    assert _in_list("spots_tide_preference_source_check") == LADDER_IN_RANK_ORDER + ("unknown",)


def test_the_cited_and_quoted_sources_are_configs():
    lists = [tuple(re.findall(r"'([^']*)'", m))
             for m in re.findall(r"NOT IN \((.*?)\)", _constraint(
                 "spots_tide_preference_citation_check"), re.S)]
    assert lists == [("researched", "scraped"), ("researched",)]
    assert lists == [config.TIDE_CITED_SOURCES, config.TIDE_QUOTED_SOURCES]


def test_the_quote_limit_and_url_pattern_are_configs():
    assert re.search(r"char_length\(tide_preference_evidence\) BETWEEN 1 AND (\d+)",
                     _code(_sql())).group(1) == "300" == str(config.BREAK_TYPE_EVIDENCE_MAX_CHARS)
    assert "'^https?://[^[:space:]]+$'" in _constraint("spots_tide_preference_source_url_check")


def test_the_header_and_the_column_comment_state_the_ranks():
    header = dict((n, int(r)) for n, r in re.findall(
        r"^--\s+(researched|scraped|model_recall|derived_from_break_type|unattributed)"
        r"\s+(\d)\s", _sql(), re.M))
    assert header == RANKS
    comment = re.search(r"COMMENT ON COLUMN spots\.tide_preference_source IS(.*?);", _sql(), re.S)
    assert dict((n, int(r)) for n, r in re.findall(r"(\w+) (\d) [>(]", comment.group(1))) == RANKS


# --------------------------------------------------------------------------- #
# The throwaway database                                                       #
# --------------------------------------------------------------------------- #

def _new_db():
    import psycopg2
    from psycopg2.extensions import make_dsn
    admin = psycopg2.connect(DB_URL)
    admin.autocommit = True
    name = f"tide_migration_{uuid.uuid4().hex[:12]}"
    with admin.cursor() as c:
        c.execute(f'CREATE DATABASE "{name}"')
    conn = psycopg2.connect(make_dsn(DB_URL, dbname=name))
    conn.autocommit = True
    with conn.cursor() as c:
        c.execute(TABLE)
    return admin, conn, name


def _drop(admin, conn, name):
    conn.close()
    with admin.cursor() as c:
        c.execute(f'DROP DATABASE IF EXISTS "{name}"')
    admin.close()


def _seed(conn):
    with conn.cursor() as c:
        n = 0
        for value, count in LIVE:
            for _ in range(count):
                n += 1
                c.execute("INSERT INTO spots (slug, tide_preference, tide_preference_source) "
                          "VALUES (%s, %s, %s)",
                          (f"spot-{n}", value, "unattributed" if value else None))


@pytest.fixture(scope="module")
def db():
    if not DB_URL:
        pytest.fail("on CI, TEST_DATABASE_URL must be set by tests.yml — refusing to skip the "
                    "only test of migration 022")
    admin, conn, name = _new_db()
    try:
        _seed(conn)
        with conn.cursor() as c:
            c.execute(_sql())
        yield conn
    finally:
        _drop(admin, conn, name)


def _run_check(conn):
    with conn.cursor() as c:
        c.execute(check_query())
        return c.fetchall()


EXPECTED_CHECK = [
    ("spots", "646", "646", True),
    ("spots with a tide_preference", "444", "444", True),
    ("... stamped unattributed", "444", "444", True),
    ("spots with no tide_preference", "202", "202", True),
    ("rows with a URL or a quote", "0", "0", True),
    ("source check admits model_recall", "true", "true", True),
    ("tide_preference checks in place", "6", "6", True),
]


@needs_db
def test_the_check_query_reads_ok_on_every_row(db):
    assert _run_check(db) == EXPECTED_CHECK


@needs_db
def test_the_values_and_their_sources_are_unchanged(db):
    with db.cursor() as c:
        c.execute("SELECT tide_preference, tide_preference_source, count(*) FROM spots "
                  "GROUP BY 1, 2 ORDER BY 1 NULLS FIRST")
        assert c.fetchall() == [(None, None, 202), ("all", "unattributed", 8),
                                ("high", "unattributed", 9), ("low", "unattributed", 62),
                                ("mid", "unattributed", 365)]


@needs_db
def test_a_second_run_changes_nothing_and_keeps_a_real_source(db):
    with db.cursor() as c:
        c.execute("UPDATE spots SET tide_preference_source = 'researched', "
                  "tide_preference_source_url = %s, tide_preference_evidence = %s "
                  "WHERE slug = 'spot-1'", (URL, QUOTE))
        c.execute(_sql())
        c.execute("SELECT tide_preference_source, tide_preference_source_url, "
                  "tide_preference_evidence FROM spots WHERE slug = 'spot-1'")
        assert c.fetchone() == ("researched", URL, QUOTE)
        c.execute("UPDATE spots SET tide_preference_source = 'unattributed', "
                  "tide_preference_source_url = NULL, tide_preference_evidence = NULL "
                  "WHERE slug = 'spot-1'")
    assert _run_check(db) == EXPECTED_CHECK


@needs_db
def test_an_uncited_researched_row_becomes_model_recall_and_an_unsourced_value_unattributed():
    """The two updates, on a table 018 left with what the old writers could have put there:
    classify_tides' uncited 'researched', and a value someone wrote with no source at all."""
    admin, conn, name = _new_db()
    try:
        with conn.cursor() as c:
            c.execute("INSERT INTO spots (slug, tide_preference, tide_preference_source) VALUES "
                      "('a', 'low', 'researched'), ('b', 'mid', NULL), "
                      "('c', 'high', 'unattributed'), ('d', NULL, NULL)")
            c.execute(_sql())
            c.execute("SELECT slug, tide_preference, tide_preference_source FROM spots "
                      "ORDER BY slug")
            assert c.fetchall() == [("a", "low", "model_recall"), ("b", "mid", "unattributed"),
                                    ("c", "high", "unattributed"), ("d", None, None)]
    finally:
        _drop(admin, conn, name)


@needs_db
@pytest.mark.parametrize("row, constraint", [
    (("e", None, "unattributed"), "spots_tide_preference_source_paired_check"),
    (("f", "mid", "scraped"), "spots_tide_preference_citation_check"),
])
def test_a_live_row_that_breaks_a_rule_refuses_the_whole_file(row, constraint):
    """A source beside no value, or a scrape with no page: not guessed at — the file fails and
    applies nothing, so the new columns are not there afterwards."""
    import psycopg2
    admin, conn, name = _new_db()
    try:
        with conn.cursor() as c:
            c.execute("INSERT INTO spots (slug, tide_preference, tide_preference_source) "
                      "VALUES ('ok', 'low', 'researched'), (%s, %s, %s)", row)
            with pytest.raises(psycopg2.errors.CheckViolation) as err:
                c.execute(_sql())
            assert err.value.diag.constraint_name == constraint
            c.execute("SELECT column_name FROM information_schema.columns WHERE "
                      "table_name = 'spots' AND column_name LIKE 'tide_preference%' "
                      "ORDER BY column_name")
            assert c.fetchall() == [("tide_preference",), ("tide_preference_source",)]
            # nor was the uncited 'researched' row relabelled
            c.execute("SELECT tide_preference_source FROM spots WHERE slug = 'ok'")
            assert c.fetchone() == ("researched",)
    finally:
        _drop(admin, conn, name)


# Each row breaks exactly one rule, so the constraint Postgres names is that rule's; the same
# values make the Python builder refuse too, so a writer fails before db_import does.
VIOLATIONS = [
    ("a value outside the list", {"tide_preference": "sometimes",
                                  "tide_preference_source": "unattributed"},
     "spots_tide_preference_check", (("sometimes", "unattributed"), {})),
    ("a source outside the ladder", {"tide_preference": "low", "tide_preference_source": "guess"},
     "spots_tide_preference_source_check", (("low", "guess"), {})),
    ("a value with no source", {"tide_preference": "low"},
     "spots_tide_preference_source_paired_check", (("low", None), {})),
    ("a source with no value", {"tide_preference_source": "model_recall"},
     "spots_tide_preference_source_paired_check", ((None, "model_recall"), {})),
    ("a citation with no value", {"tide_preference_source_url": URL},
     "spots_tide_preference_citation_check", ((None, None), {"url": URL})),
    ("researched without its page", {"tide_preference": "low",
                                     "tide_preference_source": "researched",
                                     "tide_preference_evidence": QUOTE},
     "spots_tide_preference_citation_check", (("low", "researched"), {"evidence": QUOTE})),
    ("researched without its quote", {"tide_preference": "low",
                                      "tide_preference_source": "researched",
                                      "tide_preference_source_url": URL},
     "spots_tide_preference_citation_check", (("low", "researched"), {"url": URL})),
    ("scraped without its page", {"tide_preference": "low", "tide_preference_source": "scraped"},
     "spots_tide_preference_citation_check", (("low", "scraped"), {})),
    ("a quote with no page", {"tide_preference": "low", "tide_preference_source": "model_recall",
                              "tide_preference_evidence": QUOTE},
     "spots_tide_preference_citation_check", (("low", "model_recall"), {"evidence": QUOTE})),
    ("a URL that is not http(s)", {"tide_preference": "low", "tide_preference_source": "scraped",
                                   "tide_preference_source_url": "surf-forecast.com/x"},
     "spots_tide_preference_source_url_check",
     (("low", "scraped"), {"url": "surf-forecast.com/x"})),
    ("a quote over 300 characters", {"tide_preference": "low",
                                     "tide_preference_source": "scraped",
                                     "tide_preference_source_url": URL,
                                     "tide_preference_evidence": "x" * 301},
     "spots_tide_preference_evidence_check",
     (("low", "scraped"), {"url": URL, "evidence": "x" * 301})),
    ("an empty quote", {"tide_preference": "low", "tide_preference_source": "scraped",
                        "tide_preference_source_url": URL, "tide_preference_evidence": ""},
     "spots_tide_preference_evidence_check", (("low", "scraped"), {"url": URL, "evidence": ""})),
]


@needs_db
@pytest.mark.parametrize("why, row, constraint, builder", VIOLATIONS,
                         ids=[v[0] for v in VIOLATIONS])
def test_the_database_and_the_builder_refuse_the_same_rows(db, why, row, constraint, builder):
    import psycopg2
    cols = ", ".join(["slug"] + list(row))
    marks = ", ".join(["%s"] * (len(row) + 1))
    with db.cursor() as c, pytest.raises(psycopg2.errors.CheckViolation) as err:
        c.execute(f"INSERT INTO spots ({cols}) VALUES ({marks})",
                  [f"bad-{uuid.uuid4().hex[:8]}"] + list(row.values()))
    assert err.value.diag.constraint_name == constraint, why
    args, kwargs = builder
    with pytest.raises(ValueError):
        tide_preference_fields(*args, **kwargs)


@needs_db
@pytest.mark.parametrize("value, source, url, evidence", [
    (None, None, None, None),                          # nobody has looked
    ("unknown", "model_recall", None, None),           # a model looked and could not tell
    ("mid", "unattributed", None, None),               # the backfilled state
    ("low_mid", "derived_from_break_type", None, None),
    ("high", "scraped", URL, None),                    # a scrape cites its page; a quote is optional
    ("high", "scraped", URL, "x" * 300),               # the longest quote allowed
    ("low", "researched", URL, QUOTE),
])
def test_the_database_and_the_builder_accept_the_same_rows(db, value, source, url, evidence):
    slug = f"ok-{uuid.uuid4().hex[:8]}"
    with db.cursor() as c:
        c.execute("INSERT INTO spots (slug, tide_preference, tide_preference_source, "
                  "tide_preference_source_url, tide_preference_evidence) "
                  "VALUES (%s, %s, %s, %s, %s)", (slug, value, source, url, evidence))
        c.execute("DELETE FROM spots WHERE slug = %s", (slug,))
    assert tide_preference_fields(value, source, url=url, evidence=evidence) == {
        "tide_preference": value, "tide_preference_source": source,
        "tide_preference_source_url": url, "tide_preference_evidence": evidence}
