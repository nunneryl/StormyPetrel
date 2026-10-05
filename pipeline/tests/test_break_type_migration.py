"""Migration 020 on a real Postgres, and its lists held to config's.

WHY A DATABASE. The rules that matter most here live in the database so no writer can break
them: the fixed list, the ranked sources, value-and-source-together, the citation a
researched or scraped type must carry, and a confidence no writer can set because the
database computes it. A test of Python could not show any of that. So, like
test_nwps_cycle_trigger.py, this file creates a throwaway database, gives it a spots table
with 001's break_type columns, fills it the way the live table is filled today (the
committed roster's 646 rated spots: 361 beach, 36 reef, 23 point, 22 jetty, 2 rivermouth,
202 with none, 0.5 beside every value), applies
pipeline/migrations/020_break_type_provenance.sql exactly as shipped, and runs the check
query from its header exactly as the user will.

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
from pipeline.config import break_type_confidence, break_type_fields

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MIGRATION = os.path.join(ROOT, "pipeline", "migrations", "020_break_type_provenance.sql")

DB_URL = os.environ.get("TEST_DATABASE_URL")
ON_CI = os.environ.get("GITHUB_ACTIONS") == "true"

needs_db = pytest.mark.skipif(
    not DB_URL and not ON_CI,
    reason="TEST_DATABASE_URL is not set — migration 020 needs a Postgres "
           "(CI provides one; see test_nwps_cycle_trigger.py's docstring to run it locally)")

# The spots columns 020 touches, as 001 created them. The rest of the real table is ~25
# more columns none of which 020 reads.
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

# The committed roster's rated spots at the time 020 was written, by stored value.
ROSTER = (("beach", 361), ("reef", 36), ("point", 23), ("jetty", 22), ("rivermouth", 2),
          (None, 202))

# Written out, not read from config: the mapping the generated column must compute.
CONFIDENCE = {"reviewed": "high", "researched": "medium", "scraped": "medium",
              "model_recall": "low", "unattributed": "low", "algorithm_default": "low"}
URL = "https://www.surf-forecast.com/breaks/Rincon"


def _sql():
    with open(MIGRATION, encoding="utf-8") as f:
        return f.read()


def check_query() -> str:
    """The CHECK QUERY block of 020's header, with its comment markers removed — the text the
    user is told to paste, byte for byte apart from the leading '--   '."""
    lines = _sql().splitlines()
    start = next(i for i, ln in enumerate(lines) if ln.startswith("--   SELECT check_name"))
    end = next(i for i in range(start, len(lines)) if lines[i].rstrip().endswith(";"))
    return "\n".join(ln[5:] for ln in lines[start:end + 1])


def _code(sql):
    """The SQL with comments removed."""
    return re.sub(r"--[^\n]*", "", sql)


def _in_list(constraint: str) -> tuple[str, ...]:
    """The quoted values of the first IN (...) list in *constraint*'s ADD CONSTRAINT."""
    code = _code(_sql())
    m = re.search(rf"ADD CONSTRAINT {constraint} CHECK \((.*?)\);", code, re.S)
    assert m, constraint
    lst = re.search(r"IN \((.*?)\)", m.group(1), re.S)
    return tuple(re.findall(r"'([^']*)'", lst.group(1)))


# --------------------------------------------------------------------------- #
# The migration's text, held to config                                         #
# --------------------------------------------------------------------------- #

def test_the_value_list_is_configs():
    assert _in_list("spots_break_type_check") == config.BREAK_TYPE_VALUES


def test_the_source_list_is_configs_ladder_in_rank_order():
    assert _in_list("spots_break_type_source_check") == tuple(
        sorted(config.BREAK_TYPE_SOURCE_RANK, key=config.BREAK_TYPE_SOURCE_RANK.get,
               reverse=True))


def test_the_cited_sources_are_configs():
    code = _code(_sql())
    m = re.search(r"ADD CONSTRAINT spots_break_type_citation_check CHECK \((.*?)\);", code, re.S)
    lst = re.search(r"NOT IN \((.*?)\)", m.group(1), re.S)
    assert tuple(re.findall(r"'([^']*)'", lst.group(1))) == config.BREAK_TYPE_CITED_SOURCES


def test_the_quote_limit_is_configs():
    assert re.search(r"char_length\(break_type_evidence\) BETWEEN 1 AND (\d+)",
                     _code(_sql())).group(1) == str(config.BREAK_TYPE_EVIDENCE_MAX_CHARS)


def test_the_header_and_the_column_comment_state_configs_ranks():
    header = dict((name, int(rank)) for name, rank in re.findall(
        r"^--\s+(reviewed|researched|scraped|model_recall|unattributed|algorithm_default)"
        r"\s+(\d)\s", _sql(), re.M))
    assert header == config.BREAK_TYPE_SOURCE_RANK
    comment = re.search(r"COMMENT ON COLUMN spots\.break_type_source IS(.*?);", _sql(), re.S)
    stated = dict((n, int(r)) for n, r in re.findall(r"(\w+) (\d) [>(]", comment.group(1)))
    assert stated == config.BREAK_TYPE_SOURCE_RANK


def test_the_generated_column_is_configs_mapping():
    """Read the CASE out of the migration and evaluate it in Python, source by source."""
    case = re.search(r"GENERATED ALWAYS AS \(\s*CASE(.*?)END", _code(_sql()), re.S).group(1)
    arms = re.findall(r"WHEN (.*?) THEN (NULL|'\w+')", case)
    otherwise = re.search(r"ELSE '(\w+)'", case).group(1)

    def generated(source):
        for cond, out in arms:
            if (cond.strip() == "break_type_source IS NULL" and source is None) or (
                    source is not None and re.search(rf"'{source}'", cond)):
                return None if out == "NULL" else out.strip("'")
        return otherwise

    for source, expected in CONFIDENCE.items():
        assert generated(source) == expected == break_type_confidence(source), source
    assert generated(None) is None and break_type_confidence(None) is None


# --------------------------------------------------------------------------- #
# The throwaway database                                                       #
# --------------------------------------------------------------------------- #

def _seed(conn):
    with conn.cursor() as c:
        n = 0
        for value, count in ROSTER:
            for _ in range(count):
                n += 1
                c.execute("INSERT INTO spots (slug, name, break_type, break_type_confidence) "
                          "VALUES (%s, %s, %s, %s)",
                          (f"spot-{n}", f"Spot {n}", value, 0.5 if value else None))


@pytest.fixture(scope="module")
def db():
    if not DB_URL:
        pytest.fail("on CI, TEST_DATABASE_URL must be set by tests.yml — refusing to skip the "
                    "only test of migration 020")
    import psycopg2
    from psycopg2.extensions import make_dsn

    admin = psycopg2.connect(DB_URL)
    admin.autocommit = True
    name = f"break_type_migration_{uuid.uuid4().hex[:12]}"
    with admin.cursor() as c:
        c.execute(f'CREATE DATABASE "{name}"')
    conn = psycopg2.connect(make_dsn(DB_URL, dbname=name))
    conn.autocommit = True
    try:
        with conn.cursor() as c:
            c.execute(TABLE)
        _seed(conn)
        with conn.cursor() as c:
            c.execute(_sql())
        yield conn
    finally:
        conn.close()
        with admin.cursor() as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


@needs_db
def test_a_value_outside_the_list_refuses_the_whole_file_and_applies_nothing():
    """The header's promise: run as one query, a rejected value leaves no part of 020 behind —
    no new column, and the old 0.5 confidences still in place."""
    import psycopg2
    from psycopg2.extensions import make_dsn

    admin = psycopg2.connect(DB_URL)
    admin.autocommit = True
    name = f"break_type_refused_{uuid.uuid4().hex[:12]}"
    with admin.cursor() as c:
        c.execute(f'CREATE DATABASE "{name}"')
    conn = psycopg2.connect(make_dsn(DB_URL, dbname=name))
    conn.autocommit = True
    try:
        with conn.cursor() as c:
            c.execute(TABLE)
            c.execute("INSERT INTO spots (slug, break_type, break_type_confidence) "
                      "VALUES ('a', 'beach', 0.5), ('b', 'sandbar', 0.5)")
            with pytest.raises(psycopg2.errors.CheckViolation) as err:
                c.execute(_sql())
            assert err.value.diag.constraint_name == "spots_break_type_check"
            c.execute("SELECT column_name, data_type FROM information_schema.columns "
                      "WHERE table_name = 'spots' AND column_name LIKE 'break_type%' "
                      "ORDER BY column_name")
            assert c.fetchall() == [("break_type", "text"),
                                    ("break_type_confidence", "double precision")]
            c.execute("SELECT break_type_confidence FROM spots ORDER BY slug")
            assert c.fetchall() == [(0.5,), (0.5,)]
    finally:
        conn.close()
        with admin.cursor() as c:
            c.execute(f'DROP DATABASE IF EXISTS "{name}"')
        admin.close()


def _run_check(conn):
    with conn.cursor() as c:
        c.execute(check_query())
        return c.fetchall()


EXPECTED_CHECK = [
    ("spots", "646", "646", True),
    ("spots with a break_type", "444", "444", True),
    ("... stamped unattributed and low", "444", "444", True),
    ("spots with no break_type", "202", "202", True),
    ("rows with a URL or a quote", "0", "0", True),
    ("break_type_confidence generated", "ALWAYS", "ALWAYS", True),
    ("break_type checks in place", "6", "6", True),
]


# --------------------------------------------------------------------------- #
# Applied once, then again                                                     #
# --------------------------------------------------------------------------- #

@needs_db
def test_the_check_query_reads_ok_on_every_row(db):
    assert _run_check(db) == EXPECTED_CHECK


@needs_db
def test_the_backfill_stamps_each_value_and_leaves_it_alone(db):
    with db.cursor() as c:
        c.execute("SELECT break_type, break_type_source, break_type_confidence, count(*) "
                  "FROM spots GROUP BY 1, 2, 3 ORDER BY 1 NULLS FIRST")
        assert c.fetchall() == [
            (None, None, None, 202),
            ("beach", "unattributed", "low", 361),
            ("jetty", "unattributed", "low", 22),
            ("point", "unattributed", "low", 23),
            ("reef", "unattributed", "low", 36),
            ("rivermouth", "unattributed", "low", 2),
        ]


def _confidence_attnum(conn):
    with conn.cursor() as c:
        c.execute("SELECT attnum FROM pg_attribute WHERE attrelid = 'spots'::regclass "
                  "AND attname = 'break_type_confidence' AND NOT attisdropped")
        return c.fetchone()[0]


@needs_db
def test_a_second_run_changes_nothing_and_keeps_a_real_source(db):
    attnum = _confidence_attnum(db)
    with db.cursor() as c:
        c.execute("UPDATE spots SET break_type_source = 'researched', "
                  "break_type_source_url = %s WHERE slug = 'spot-1'", (URL,))
        c.execute(_sql())
    # The generated column is the same column, not dropped and re-added: a re-run rewrites
    # nothing that is already right.
    assert _confidence_attnum(db) == attnum
    with db.cursor() as c:
        c.execute("SELECT break_type_source, break_type_confidence, break_type_source_url "
                  "FROM spots WHERE slug = 'spot-1'")
        assert c.fetchone() == ("researched", "medium", URL)
        c.execute("UPDATE spots SET break_type_source = 'unattributed', "
                  "break_type_source_url = NULL WHERE slug = 'spot-1'")
    assert _run_check(db) == EXPECTED_CHECK


@needs_db
@pytest.mark.parametrize("source, expected", sorted(CONFIDENCE.items()))
def test_the_database_computes_the_confidence_from_the_source(db, source, expected):
    url = URL if source in ("researched", "scraped") else None
    slug = f"conf-{source}"
    with db.cursor() as c:
        c.execute("INSERT INTO spots (slug, break_type, break_type_source, break_type_source_url) "
                  "VALUES (%s, 'reef', %s, %s) RETURNING break_type_confidence",
                  (slug, source, url))
        assert c.fetchone() == (expected,)
        c.execute("DELETE FROM spots WHERE slug = %s", (slug,))


@needs_db
def test_no_writer_can_set_the_confidence(db):
    import psycopg2
    with db.cursor() as c, pytest.raises(psycopg2.Error) as err:
        c.execute("UPDATE spots SET break_type_confidence = 'high' WHERE slug = 'spot-1'")
    assert err.value.pgcode == "428C9"   # generated_always: what db_import would hit


@needs_db
def test_the_upsert_db_import_sends_lands_and_the_confidence_follows(db):
    """INSERT ... ON CONFLICT DO UPDATE with the columns db_import sends, as PostgREST runs it."""
    with db.cursor() as c:
        c.execute(
            "INSERT INTO spots (slug, break_type, break_type_source, break_type_source_url, "
            "break_type_evidence) VALUES ('spot-2', 'point', 'scraped', %s, %s) "
            "ON CONFLICT (slug) DO UPDATE SET break_type = EXCLUDED.break_type, "
            "break_type_source = EXCLUDED.break_type_source, "
            "break_type_source_url = EXCLUDED.break_type_source_url, "
            "break_type_evidence = EXCLUDED.break_type_evidence "
            "RETURNING break_type, break_type_source, break_type_confidence",
            (URL, "Rincon in Santa Barbara is an exposed point break"))
        assert c.fetchone() == ("point", "scraped", "medium")
        c.execute("UPDATE spots SET break_type = 'beach', break_type_source = 'unattributed', "
                  "break_type_source_url = NULL, break_type_evidence = NULL "
                  "WHERE slug = 'spot-2'")


# Each row breaks exactly one rule, so the constraint Postgres names is that rule's; the
# same values must make the Python builder refuse too, so a writer fails before db_import.
VIOLATIONS = [
    ("a value outside the list", {"break_type": "sandbar", "break_type_source": "unattributed"},
     "spots_break_type_check", (("sandbar", "unattributed"), {})),
    ("a source outside the ladder", {"break_type": "reef", "break_type_source": "guess"},
     "spots_break_type_source_check", (("reef", "guess"), {})),
    ("a value with no source", {"break_type": "reef"},
     "spots_break_type_source_paired_check", (("reef", None), {})),
    ("a source with no value", {"break_type_source": "reviewed"},
     "spots_break_type_source_paired_check", ((None, "reviewed"), {})),
    ("a citation with no value", {"break_type_source_url": URL},
     "spots_break_type_citation_check", ((None, None), {"url": URL})),
    ("researched without its page", {"break_type": "reef", "break_type_source": "researched"},
     "spots_break_type_citation_check", (("reef", "researched"), {})),
    ("scraped without its page", {"break_type": "reef", "break_type_source": "scraped"},
     "spots_break_type_citation_check", (("reef", "scraped"), {})),
    ("a URL that is not http(s)", {"break_type": "reef", "break_type_source": "researched",
                                   "break_type_source_url": "surf-forecast.com/breaks/Rincon"},
     "spots_break_type_source_url_check",
     (("reef", "researched"), {"url": "surf-forecast.com/breaks/Rincon"})),
    ("a quote over 300 characters", {"break_type": "reef", "break_type_source": "reviewed",
                                     "break_type_evidence": "x" * 301},
     "spots_break_type_evidence_check", (("reef", "reviewed"), {"evidence": "x" * 301})),
    ("an empty quote", {"break_type": "reef", "break_type_source": "reviewed",
                        "break_type_evidence": ""},
     "spots_break_type_evidence_check", (("reef", "reviewed"), {"evidence": ""})),
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
        break_type_fields(*args, **kwargs)


@needs_db
@pytest.mark.parametrize("value, source, url, evidence", [
    (None, None, None, None),                                   # nobody has looked
    ("unknown", "model_recall", None, None),                    # someone looked, could not tell
    ("reef", "reviewed", None, None),                           # a person needs no page
    ("reef", "reviewed", None, "x" * 300),                      # the longest quote allowed
    ("point", "researched", URL, "Rincon in Santa Barbara is an exposed point break"),
    ("beach", "algorithm_default", None, None),
])
def test_the_database_and_the_builder_accept_the_same_rows(db, value, source, url, evidence):
    slug = f"ok-{uuid.uuid4().hex[:8]}"
    with db.cursor() as c:
        c.execute("INSERT INTO spots (slug, break_type, break_type_source, break_type_source_url, "
                  "break_type_evidence) VALUES (%s, %s, %s, %s, %s) "
                  "RETURNING break_type_confidence", (slug, value, source, url, evidence))
        assert c.fetchone() == (CONFIDENCE.get(source),)
        c.execute("DELETE FROM spots WHERE slug = %s", (slug,))
    assert break_type_fields(value, source, url=url, evidence=evidence)[
        "break_type_confidence"] == CONFIDENCE.get(source)


@needs_db
@pytest.mark.parametrize("url", [
    "https://www.surf-forecast.com/breaks/Rincon", "http://example.org/a?b=c#d",
    "https://", "surf-forecast.com/breaks/Rincon", "https://a b", "ftp://example.org/a",
    "javascript:alert(1)",
])
def test_the_url_rule_is_the_same_in_both_places(db, url):
    import psycopg2
    slug = f"url-{uuid.uuid4().hex[:8]}"
    python_ok = bool(config.BREAK_TYPE_URL_RE.match(url))
    with db.cursor() as c:
        try:
            c.execute("INSERT INTO spots (slug, break_type, break_type_source, "
                      "break_type_source_url) VALUES (%s, 'reef', 'researched', %s)", (slug, url))
            database_ok = True
            c.execute("DELETE FROM spots WHERE slug = %s", (slug,))
        except psycopg2.errors.CheckViolation:
            database_ok = False
    assert database_ok == python_ok, url
