"""Migration 021 (the bottom) on a real Postgres, its lists held to config's, and the one
builder that writes it.

WHY A DATABASE. As with 020: the fixed list, the ranked sources, value-and-source together,
the citation a researched or scraped bottom must carry, and a confidence no writer can set
all live in the database. This file creates a throwaway database, gives it the spots table as
it stands after 020 (001's columns plus 020 applied exactly as shipped), applies
pipeline/migrations/021_bottom_provenance.sql exactly as shipped, and runs the check query
from its header exactly as the user will.

WHERE IT RUNS. Like test_break_type_migration.py: CI provides TEST_DATABASE_URL and the
database tests refuse to skip there; elsewhere they skip when it is unset. The tests that
read the migration's text, and the builder's, run everywhere.

No expected value below is produced by calling the code under test.
"""
from __future__ import annotations

import os
import re
import uuid

import pytest

from pipeline import config
from pipeline.config import bottom_fields, bottom_may_overwrite
from pipeline.tests.test_break_type_provenance import _enrich

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MIGRATION = os.path.join(ROOT, "pipeline", "migrations", "021_bottom_provenance.sql")
MIGRATION_020 = os.path.join(ROOT, "pipeline", "migrations", "020_break_type_provenance.sql")

DB_URL = os.environ.get("TEST_DATABASE_URL")
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"

needs_db = pytest.mark.skipif(
    not DB_URL and not ON_CI,
    reason="TEST_DATABASE_URL is not set — migration 021 needs a Postgres "
           "(CI provides one; see test_nwps_cycle_trigger.py's docstring to run it locally)")

# The spots columns 020 and 021 touch, as 001 created them; 020 is applied on top.
TABLE = """
CREATE TABLE spots (
  id BIGSERIAL PRIMARY KEY,
  slug TEXT UNIQUE NOT NULL,
  name TEXT,
  break_type TEXT,
  break_type_confidence DOUBLE PRECISION,
  created_at TIMESTAMPTZ DEFAULT now(),
  updated_at TIMESTAMPTZ DEFAULT now()
);
"""

# Written out, not read from config: the list, the ladder in rank order, the mapping.
VALUES = ("sand", "rock", "coral", "cobble", "mixed", "unknown")
LADDER = ("reviewed", "researched", "scraped", "model_recall", "unattributed",
          "algorithm_default")
CONFIDENCE = {"reviewed": "high", "researched": "medium", "scraped": "medium",
              "model_recall": "low", "unattributed": "low", "algorithm_default": "low"}
URL = "https://www.surf-forecast.com/breaks/Banzai-Pipeline"
QUOTE = "The wave breaks over a shallow lava rock shelf"


def _sql(path=MIGRATION):
    with open(path, encoding="utf-8") as f:
        return f.read()


def check_query() -> str:
    """The CHECK QUERY block of 021's header without its comment markers: the text the user
    is told to paste."""
    lines = _sql().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("--   SELECT check_name"))
    end = next(i for i in range(start, len(lines)) if lines[i].rstrip().endswith(";"))
    return "\n".join(ln[5:] for ln in lines[start:end + 1])


def _code(sql):
    return re.sub(r"--[^\n]*", "", sql)


def _in_list(constraint: str, negated=False) -> tuple:
    m = re.search(rf"ADD CONSTRAINT {constraint} CHECK \((.*?)\);", _code(_sql()), re.S)
    assert m, constraint
    lst = re.search(r"NOT IN \((.*?)\)" if negated else r" IN \((.*?)\)", m.group(1), re.S)
    return tuple(re.findall(r"'([^']*)'", lst.group(1)))


# --------------------------------------------------------------------------- #
# The migration's text, held to config and to 020                             #
# --------------------------------------------------------------------------- #

def test_the_value_list_is_these_six_and_configs():
    assert _in_list("spots_bottom_check") == VALUES == config.BOTTOM_VALUES


def test_the_source_list_is_020s_ladder_in_rank_order():
    assert _in_list("spots_bottom_source_check") == LADDER
    assert tuple(sorted(config.BREAK_TYPE_SOURCE_RANK, key=config.BREAK_TYPE_SOURCE_RANK.get,
                        reverse=True)) == LADDER


def test_the_cited_sources_and_the_quote_limit_are_020s():
    assert _in_list("spots_bottom_citation_check", negated=True) == ("researched", "scraped")
    assert re.search(r"char_length\(bottom_evidence\) BETWEEN 1 AND (\d+)",
                     _code(_sql())).group(1) == "300"


def test_the_header_and_the_column_comment_state_020s_ranks():
    ranks = {"reviewed": 6, "researched": 5, "scraped": 4, "model_recall": 3,
             "unattributed": 2, "algorithm_default": 1}
    header = dict((name, int(rank)) for name, rank in re.findall(
        r"^--\s+(reviewed|researched|scraped|model_recall|unattributed|algorithm_default)"
        r"\s+(\d)\s", _sql(), re.M))
    assert header == ranks == config.BREAK_TYPE_SOURCE_RANK
    comment = re.search(r"COMMENT ON COLUMN spots\.bottom_source IS(.*?);", _sql(), re.S)
    assert dict((n, int(r)) for n, r in re.findall(r"(\w+) (\d) [>(]", comment.group(1))) == ranks


def test_the_rules_are_020s_with_bottom_for_break_type():
    """Every constraint and the generated column, text for text, are 020's with the column
    renamed and the value list swapped: the two cannot drift apart."""
    def constraints(sql, prefix):
        return {name.replace(prefix, "X"): re.sub(r"\s+", " ", body.replace(prefix, "X"))
                for name, body in re.findall(r"ADD CONSTRAINT (\w+) CHECK \((.*?)\);",
                                             _code(sql), re.S)}
    ours = constraints(_sql(), "bottom")
    theirs = constraints(_sql(MIGRATION_020), "break_type")
    assert set(ours) == set(theirs) == {
        "spots_X_check", "spots_X_source_check", "spots_X_source_paired_check",
        "spots_X_citation_check", "spots_X_source_url_check", "spots_X_evidence_check"}
    for name in ours:
        if name != "spots_X_check":
            assert ours[name] == theirs[name], name
    case = re.compile(r"GENERATED ALWAYS AS \(\s*(CASE.*?END)", re.S)
    assert (re.sub(r"\s+", " ", case.search(_code(_sql())).group(1)).replace("bottom", "X")
            == re.sub(r"\s+", " ", case.search(_code(_sql(MIGRATION_020))).group(1))
            .replace("break_type", "X"))


# --------------------------------------------------------------------------- #
# The builder and the overwrite rule                                            #
# --------------------------------------------------------------------------- #

def test_a_bottom_write_is_all_five_keys_together():
    assert bottom_fields("rock", "researched", url=URL, evidence=QUOTE) == {
        "bottom": "rock", "bottom_source": "researched", "bottom_confidence": "medium",
        "bottom_source_url": URL, "bottom_evidence": QUOTE}
    assert bottom_fields("sand", "model_recall") == {
        "bottom": "sand", "bottom_source": "model_recall", "bottom_confidence": "low",
        "bottom_source_url": None, "bottom_evidence": None}
    assert bottom_fields(None, None) == dict.fromkeys(
        ("bottom", "bottom_source", "bottom_confidence", "bottom_source_url",
         "bottom_evidence"))
    assert config.BOTTOM_FIELDS == ("bottom", "bottom_source", "bottom_confidence",
                                    "bottom_source_url", "bottom_evidence")


@pytest.mark.parametrize("args, kwargs, message", [
    (("reef", "researched"), {"url": URL}, "bottom 'reef' is not one of"),
    (("rock", "guess"), {}, "bottom_source 'guess' is not one of"),
    ((None, "reviewed"), {}, "a bottom source or citation needs a bottom"),
    (("rock", "researched"), {}, "a researched bottom must cite the page it came from"),
    (("rock", "scraped"), {}, "a scraped bottom must cite the page it came from"),
    (("rock", "reviewed"), {"url": "surf-forecast.com/x"}, "is not an http(s) URL"),
    (("rock", "reviewed"), {"evidence": "x" * 301}, "must be 1-300 characters, got 301"),
    (("rock", "reviewed"), {"evidence": ""}, "must be 1-300 characters, got 0"),
])
def test_the_builder_refuses_what_021_refuses(args, kwargs, message):
    with pytest.raises(ValueError, match=re.escape(message)):
        bottom_fields(*args, **kwargs)


def test_the_bottom_overwrite_rule_is_020s_ladder():
    assert bottom_may_overwrite("unattributed", "model_recall") is True
    assert bottom_may_overwrite("model_recall", "researched") is True
    assert bottom_may_overwrite("researched", "researched") is True
    assert bottom_may_overwrite("researched", "model_recall") is False
    assert bottom_may_overwrite("reviewed", "researched") is False
    assert bottom_may_overwrite(None, "algorithm_default") is True
    assert bottom_may_overwrite("researched", "guess") is False


def test_enrich_leaves_a_sourced_bottom_exactly_as_it_found_it():
    # enrich reads spots_enriched.json and copies each spot: a bottom the import wrote must
    # survive the next enrich run, keys, order of values and all.
    spot = {"name": "Pipe", "lat": 21.664, "lng": -158.054, "bottom": "rock",
            "bottom_source": "researched", "bottom_confidence": "medium",
            "bottom_source_url": URL, "bottom_evidence": QUOTE}
    out = _enrich(spot)
    assert {k: out.get(k) for k in ("bottom", "bottom_source", "bottom_confidence",
                                    "bottom_source_url", "bottom_evidence")} == {
        "bottom": "rock", "bottom_source": "researched", "bottom_confidence": "medium",
        "bottom_source_url": URL, "bottom_evidence": QUOTE}


# --------------------------------------------------------------------------- #
# The throwaway database                                                       #
# --------------------------------------------------------------------------- #

def _new_db():
    import psycopg2
    from psycopg2.extensions import make_dsn

    admin = psycopg2.connect(DB_URL)
    admin.autocommit = True
    name = f"bottom_migration_{uuid.uuid4().hex[:12]}"
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


@pytest.fixture(scope="module")
def db():
    if not DB_URL:
        pytest.fail("on CI, TEST_DATABASE_URL must be set by tests.yml — refusing to skip the "
                    "only test of migration 021")
    admin, name, conn = _new_db()
    try:
        with conn.cursor() as c:
            c.execute(TABLE)
            # Four spots as the live table holds them before 021: three with a break type,
            # one nobody has looked at.
            c.execute("INSERT INTO spots (slug, break_type, break_type_confidence) VALUES "
                      "('a', 'beach', 0.5), ('b', 'reef', 0.5), ('c', 'point', 0.5), "
                      "('d', NULL, NULL)")
            c.execute(_sql(MIGRATION_020))
            c.execute(_sql())
        yield conn
    finally:
        _drop(admin, name, conn)


def _run_check(conn):
    with conn.cursor() as c:
        c.execute(check_query())
        return c.fetchall()


EXPECTED_CHECK = [
    ("bottom columns added", "5", "5", True),
    ("bottom_confidence generated", "ALWAYS", "ALWAYS", True),
    ("bottom checks in place", "6", "6", True),
    ("rows with a bottom yet", "0", "0", True),
    ("break_type checks still in place", "6", "6", True),
]


@needs_db
def test_the_check_query_reads_ok_on_every_row(db):
    assert _run_check(db) == EXPECTED_CHECK


@needs_db
def test_the_check_query_cannot_read_ok_before_the_migration():
    # It reads the columns 021 adds, so before 021 it stops with an error: it can never
    # read ok on a database the migration has not reached.
    import psycopg2
    admin, name, conn = _new_db()
    try:
        with conn.cursor() as c:
            c.execute(TABLE)
            c.execute(_sql(MIGRATION_020))
        with pytest.raises(psycopg2.errors.UndefinedColumn):
            _run_check(conn)
    finally:
        _drop(admin, name, conn)


@needs_db
def test_a_second_run_changes_nothing_and_keeps_what_was_written(db):
    with db.cursor() as c:
        c.execute("UPDATE spots SET bottom = 'rock', bottom_source = 'researched', "
                  "bottom_source_url = %s, bottom_evidence = %s WHERE slug = 'b'", (URL, QUOTE))
        c.execute(_sql())
        c.execute("SELECT bottom, bottom_source, bottom_confidence, bottom_source_url, "
                  "bottom_evidence FROM spots WHERE slug = 'b'")
        assert c.fetchone() == ("rock", "researched", "medium", URL, QUOTE)
        c.execute("UPDATE spots SET bottom = NULL, bottom_source = NULL, "
                  "bottom_source_url = NULL, bottom_evidence = NULL WHERE slug = 'b'")
    assert _run_check(db) == EXPECTED_CHECK


@needs_db
@pytest.mark.parametrize("source, expected", sorted(CONFIDENCE.items()))
def test_the_database_computes_the_confidence_from_the_source(db, source, expected):
    url = URL if source in ("researched", "scraped") else None
    slug = f"conf-{source}"
    with db.cursor() as c:
        c.execute("INSERT INTO spots (slug, bottom, bottom_source, bottom_source_url) "
                  "VALUES (%s, 'sand', %s, %s) RETURNING bottom_confidence", (slug, source, url))
        assert c.fetchone() == (expected,)
        c.execute("DELETE FROM spots WHERE slug = %s", (slug,))


@needs_db
def test_no_writer_can_set_the_confidence(db):
    import psycopg2
    with db.cursor() as c, pytest.raises(psycopg2.Error) as err:
        c.execute("UPDATE spots SET bottom_confidence = 'high' WHERE slug = 'a'")
    assert err.value.pgcode == "428C9"


@needs_db
def test_the_upsert_db_import_sends_lands_and_the_confidence_follows(db):
    with db.cursor() as c:
        c.execute(
            "INSERT INTO spots (slug, bottom, bottom_source, bottom_source_url, bottom_evidence) "
            "VALUES ('c', 'cobble', 'researched', %s, %s) "
            "ON CONFLICT (slug) DO UPDATE SET bottom = EXCLUDED.bottom, "
            "bottom_source = EXCLUDED.bottom_source, "
            "bottom_source_url = EXCLUDED.bottom_source_url, "
            "bottom_evidence = EXCLUDED.bottom_evidence "
            "RETURNING bottom, bottom_source, bottom_confidence, break_type",
            (URL, "The point breaks over cobblestones and boulders"))
        assert c.fetchone() == ("cobble", "researched", "medium", "point")
        c.execute("UPDATE spots SET bottom = NULL, bottom_source = NULL, "
                  "bottom_source_url = NULL, bottom_evidence = NULL WHERE slug = 'c'")


# Each row breaks exactly one rule; the builder must refuse the same values.
VIOLATIONS = [
    ("a value outside the list", {"bottom": "reef", "bottom_source": "unattributed"},
     "spots_bottom_check", (("reef", "unattributed"), {})),
    ("a source outside the ladder", {"bottom": "rock", "bottom_source": "guess"},
     "spots_bottom_source_check", (("rock", "guess"), {})),
    ("a value with no source", {"bottom": "rock"},
     "spots_bottom_source_paired_check", (("rock", None), {})),
    ("a source with no value", {"bottom_source": "reviewed"},
     "spots_bottom_source_paired_check", ((None, "reviewed"), {})),
    ("a citation with no value", {"bottom_source_url": URL},
     "spots_bottom_citation_check", ((None, None), {"url": URL})),
    ("researched without its page", {"bottom": "rock", "bottom_source": "researched"},
     "spots_bottom_citation_check", (("rock", "researched"), {})),
    ("scraped without its page", {"bottom": "rock", "bottom_source": "scraped"},
     "spots_bottom_citation_check", (("rock", "scraped"), {})),
    ("a URL that is not http(s)", {"bottom": "rock", "bottom_source": "researched",
                                   "bottom_source_url": "surf-forecast.com/breaks/Rincon"},
     "spots_bottom_source_url_check",
     (("rock", "researched"), {"url": "surf-forecast.com/breaks/Rincon"})),
    ("a quote over 300 characters", {"bottom": "rock", "bottom_source": "reviewed",
                                     "bottom_evidence": "x" * 301},
     "spots_bottom_evidence_check", (("rock", "reviewed"), {"evidence": "x" * 301})),
    ("an empty quote", {"bottom": "rock", "bottom_source": "reviewed", "bottom_evidence": ""},
     "spots_bottom_evidence_check", (("rock", "reviewed"), {"evidence": ""})),
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
        bottom_fields(*args, **kwargs)


@needs_db
@pytest.mark.parametrize("value, source, url, evidence", [
    (None, None, None, None),
    ("unknown", "model_recall", None, None),
    ("mixed", "model_recall", None, None),
    ("coral", "reviewed", None, "x" * 300),
    ("rock", "researched", URL, QUOTE),
    ("sand", "algorithm_default", None, None),
])
def test_the_database_and_the_builder_accept_the_same_rows(db, value, source, url, evidence):
    slug = f"ok-{uuid.uuid4().hex[:8]}"
    with db.cursor() as c:
        c.execute("INSERT INTO spots (slug, bottom, bottom_source, bottom_source_url, "
                  "bottom_evidence) VALUES (%s, %s, %s, %s, %s) RETURNING bottom_confidence",
                  (slug, value, source, url, evidence))
        assert c.fetchone() == (CONFIDENCE.get(source),)
        c.execute("DELETE FROM spots WHERE slug = %s", (slug,))
    assert bottom_fields(value, source, url=url, evidence=evidence)[
        "bottom_confidence"] == CONFIDENCE.get(source)
