"""Check, before a run fetches anything, that the live tables have every column db_import writes.

WHAT HAPPENED. #227 merged before migration 019 had been applied. db_import sent the new
forecasts.nwps_cycle, and PostgREST refused the whole forecasts upsert:

    PGRST204  Could not find the 'nwps_cycle' column of 'forecasts' in the schema cache

run_all stopped at that exception, so buoy observations and tides were not written either
and revalidation was skipped. Forecasts stopped publishing until the migration was applied
and the run repeated (run 2023, 2026-09-23). Nothing records which migrations are applied,
so nothing could notice in advance.

WHAT THIS DOES, in three steps of forecast-pipeline.yml:

  1. At the start of a run, before the fetch, `python -m pipeline.column_preflight --all`
     reads the live tables' columns and compares every column db_import.WRITTEN_COLUMNS says
     an upsert can send, for every table the run's db_import mode writes. Each missing one is
     logged by name with the migration file in pipeline/migrations/ that adds it. The finding
     goes to the file named by COLUMN_PREFLIGHT_RESULT.
  2. db_import reads that file and leaves exactly those columns out of every row it sends.
     Everything else is uploaded, so forecasts keep flowing.
  3. After the uploads and revalidation, `--finish` ends the run FAILED with one line naming
     the missing columns. It is the last step that does work, so it never stops one.

HOW IT LEARNS THE COLUMNS: PostgREST's own description of the tables, GET /rest/v1/ with
the service key db_import already holds. That description is built from the same schema
cache PostgREST checks an upsert's columns against (PGRST204 is "not in the schema cache"),
so a column is missing from it exactly when an upsert carrying that column is refused. A
zero-row select asks Postgres instead, and the two disagree while the cache is stale.
Measured on PostgREST 12.2.12 and 14.17 (the version Supabase's own stack ships): a column
added but not yet reloaded selects fine, yet the upsert still fails. A select also names
only the first missing column. Supabase serves the description to service-role and secret
keys only (anon-key access was removed in 2026), and SUPABASE_SERVICE_KEY is the
service-role key.

WHAT IT NEVER DOES. It drops nothing it did not find missing and catches no database error.
Any other failure, including a PGRST204 for a column the check did not flag, fails exactly
as before. Two things cannot be left out: a missing TABLE, and a missing column the upsert
matches rows on (its ON CONFLICT key). Those are reported, and that table's upload fails
as before. If the description cannot be read at all, nothing is left out, and --finish
still fails the run to say the check did not run. A safety net that silently stopped
working would be the quiet failure this exists to end.

CLI:
    python -m pipeline.column_preflight --all          # same mode flags as db_import
    python -m pipeline.column_preflight --buoys-only
    python -m pipeline.column_preflight --finish       # last step: exit 1 if anything was found
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from . import db_import

log = logging.getLogger("pipeline.column_preflight")

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"
DEFAULT_RESULT = Path(__file__).resolve().parent / "forecast_data" / "column_preflight.json"

ATTEMPTS = 3
RETRY_WAITS_S = (5.0, 15.0)
TIMEOUT_S = 30.0
_sleep = time.sleep

TITLE_MISSING = "Missing database columns"
TITLE_UNCHECKED = "Column check did not run"


class ColumnCheckError(Exception):
    """The live tables' columns could not be read, so nothing can be said about them."""


# --------------------------------------------------------------------------- #
# The live tables, as PostgREST describes them                                 #
# --------------------------------------------------------------------------- #

def parse_description(spec) -> dict[str, frozenset[str]]:
    """{table: its columns} from PostgREST's OpenAPI description (its `definitions` map)."""
    defs = spec.get("definitions") if isinstance(spec, dict) else None
    if not isinstance(defs, dict):
        raise ColumnCheckError("the description has no table definitions")
    out = {}
    for name, d in defs.items():
        props = d.get("properties") if isinstance(d, dict) else None
        if isinstance(props, dict):
            out[name] = frozenset(props)
    return out


def read_live_columns(client) -> dict[str, frozenset[str]]:
    """Read the description through the client's own PostgREST session, so the request
    carries exactly the credentials the upserts use. Retried; then ColumnCheckError."""
    detail = "no attempt made"
    for attempt in range(ATTEMPTS):
        if attempt:
            _sleep(RETRY_WAITS_S[min(attempt - 1, len(RETRY_WAITS_S) - 1)])
        try:
            session = client.postgrest.session
            r = session.get("/", headers={"Accept": "application/openapi+json"},
                            timeout=TIMEOUT_S)
        except ColumnCheckError:
            raise
        except Exception as e:  # noqa: BLE001 — any transport failure means "could not read"
            detail = f"{type(e).__name__}: {e}"
            continue
        if r.status_code != 200:
            detail = f"HTTP {r.status_code}: {r.text[:200].strip()}"
            continue
        try:
            return parse_description(r.json())
        except (ValueError, ColumnCheckError) as e:
            detail = str(e) or type(e).__name__
    raise ColumnCheckError(f"could not read the live tables' columns ({detail})")


# --------------------------------------------------------------------------- #
# Which migration adds a column                                                 #
# --------------------------------------------------------------------------- #

_DOLLAR_TAG = re.compile(r"\$[A-Za-z_]*\$")
_DO_BEFORE = re.compile(r"\bDO(?:\s+LANGUAGE\s+\w+)?\s*$", re.I)
_CONSTRAINT_WORDS = frozenset({"constraint", "primary", "unique", "check", "foreign",
                               "exclude", "like"})


def _code_only(sql: str) -> str:
    """The SQL with comments removed and quoted text blanked, so a column named in a comment,
    a COMMENT ON string or a function body is never read as added. The body of a DO block is
    kept: it runs when the migration runs, so a column it adds is added by that file."""
    out, i, n = [], 0, len(sql)
    while i < n:
        if sql.startswith("--", i):
            j = sql.find("\n", i)
            i = n if j < 0 else j
        elif sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            i = n if j < 0 else j + 2
            out.append(" ")
        elif sql[i] == "'":
            j = i + 1
            while j < n and not (sql[j] == "'" and not sql.startswith("''", j)):
                j += 2 if sql.startswith("''", j) else 1
            out.append("''")
            i = j + 1
        elif _DOLLAR_TAG.match(sql, i):
            tag = _DOLLAR_TAG.match(sql, i).group(0)
            j = sql.find(tag, i + len(tag))
            body = sql[i + len(tag):n if j < 0 else j]
            out.append(f" {_code_only(body)} " if _DO_BEFORE.search("".join(out)) else "''")
            i = n if j < 0 else j + len(tag)
        else:
            out.append(sql[i])
            i += 1
    return "".join(out)


def _split_top(s: str, sep: str) -> list[str]:
    """Split on `sep` wherever it is not inside parentheses."""
    parts, depth, cur = [], 0, []
    for ch in s:
        depth += (ch == "(") - (ch == ")")
        if ch == sep and depth == 0:
            parts.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    parts.append("".join(cur))
    return parts


def _paren_body(s: str, start: int) -> str | None:
    """The text inside the parenthesis opening at s[start], or None if it never closes."""
    depth = 0
    for i in range(start, len(s)):
        depth += (s[i] == "(") - (s[i] == ")")
        if depth == 0:
            return s[start + 1:i]
    return None


def _ident(name: str) -> str:
    """Unqualified, unquoted identifier: public."forecasts" -> forecasts."""
    last = name.split(".")[-1]
    return last[1:-1] if last.startswith('"') and last.endswith('"') else last.lower()


_ALTER = re.compile(r"\bALTER\s+TABLE\s+(?:IF\s+EXISTS\s+)?(?:ONLY\s+)?([\w.\"]+)\s+(.*)",
                    re.I | re.S)
_CREATE = re.compile(r"\bCREATE\s+(?:UNLOGGED\s+)?TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([\w.\"]+)"
                     r"\s*(?=\()", re.I)
_ADD = re.compile(r"\s*ADD\s+(COLUMN\s+)?(?:IF\s+NOT\s+EXISTS\s+)?(\"[^\"]+\"|\w+)", re.I)


def columns_added(sql: str) -> set[tuple[str, str]]:
    """Every (table, column) one migration file adds: ALTER TABLE ... ADD [COLUMN], and the
    column definitions of CREATE TABLE, including inside a DO block (an IF ... THEN around
    the ALTER, say). Constraints are not columns."""
    added = set()
    for stmt in _split_top(_code_only(sql), ";"):
        m = _ALTER.search(stmt)
        if m:
            for action in _split_top(m.group(2), ","):
                a = _ADD.match(action)
                if a and (a.group(1) or a.group(2).lower() not in _CONSTRAINT_WORDS):
                    added.add((_ident(m.group(1)), _ident(a.group(2))))
            continue
        m = _CREATE.search(stmt)
        body = _paren_body(stmt, m.end()) if m else None
        if body is not None:
            for element in _split_top(body, ","):
                words = element.split()
                if words and words[0].lower() not in _CONSTRAINT_WORDS:
                    added.add((_ident(m.group(1)), _ident(words[0])))
    return added


def migration_index(migrations_dir: Path = MIGRATIONS_DIR) -> dict[tuple[str, str], list[str]]:
    """{(table, column): [the migration files that add it, in order]}."""
    index: dict[tuple[str, str], list[str]] = {}
    for path in sorted(Path(migrations_dir).glob("*.sql")):
        for key in sorted(columns_added(path.read_text(encoding="utf-8"))):
            index.setdefault(key, []).append(path.name)
    return index


def _where(files) -> str:
    files = list(files)
    if not files:
        return "no file in pipeline/migrations/ adds it"
    return " and ".join(f"pipeline/migrations/{f}" for f in files)


# --------------------------------------------------------------------------- #
# The check                                                                    #
# --------------------------------------------------------------------------- #

def check(tables, live: dict[str, frozenset[str]], index: dict[tuple[str, str], list[str]],
          written: dict[str, tuple[str, ...]] = db_import.WRITTEN_COLUMNS,
          keys: dict[str, tuple[str, ...]] = db_import.UPSERT_KEYS) -> dict:
    """Compare what each table's upsert can send with what the live table has.

    leave_out  {table: [columns]} db_import drops: missing from a table that exists, and not
               part of the upsert's key
    blocked    a missing table, or a missing key column. Neither can be left out, so that
               table's upload fails as before; both are reported all the same
    where      what adds each one, by name
    reason     the one line --finish ends the run with, or None when nothing is missing"""
    leave_out: dict[str, list[str]] = {}
    blocked: list[str] = []
    where: dict[str, str] = {}
    for table in tables:
        if table not in live:
            creators = sorted({f for (t, _c), fs in index.items() if t == table for f in fs})
            where[table] = _where(creators[:1])
            blocked.append(table)
            continue
        for c in written[table]:
            if c in live[table]:
                continue
            where[f"{table}.{c}"] = _where(index.get((table, c), []))
            if c in keys[table]:
                blocked.append(f"{table}.{c}")
            else:
                leave_out.setdefault(table, []).append(c)
    parts = []
    if leave_out:
        named = ", ".join(f"{t}.{c} ({where[f'{t}.{c}']})" for t, cs in leave_out.items() for c in cs)
        parts.append(f"{TITLE_MISSING}: {named} — left out of this run's upload; apply the "
                     "migration to write them.")
    if blocked:
        named = ", ".join(f"{b} ({where[b]})" for b in blocked)
        parts.append(f"Missing from the database and cannot be left out: {named} — those "
                     "uploads fail as before.")
    return {"leave_out": leave_out, "blocked": blocked, "where": where,
            "reason": " ".join(parts) or None}


def _annotate(kind: str, title: str, message: str) -> None:
    """A GitHub Actions annotation, so the line shows on the run's summary page."""
    if os.environ.get("GITHUB_ACTIONS") == "true":
        esc = message.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
        print(f"::{kind} title={title}::{esc}", flush=True)


def _write_result(path: Path, result: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)   # all or nothing: --finish never reads half a result


def run_preflight(client, tables, result_path: Path, index=None) -> dict:
    """Check `tables`, log what is missing, and write the result for db_import and --finish.
    Nothing the database answers makes this raise, and nothing here stops a run."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    tables = list(tables)
    base = {"format": 1, "checked_at": now, "tables": tables, "leave_out": {}, "blocked": [],
            "where": {}}
    try:
        live = read_live_columns(client)
    except ColumnCheckError as e:
        reason = (f"{TITLE_UNCHECKED}: {e}. Nothing was left out; this run sent every column, "
                  "as before.")
        log.error("column preflight: %s", reason)
        _annotate("error", TITLE_UNCHECKED, reason)
        result = dict(base, status="unchecked", reason=reason, title=TITLE_UNCHECKED)
        _write_result(result_path, result)
        return result
    found = check(tables, live, migration_index() if index is None else index)
    if not found["reason"]:
        log.info("column preflight: every column db_import writes is present in the live tables "
                 "(%d tables: %s; %d columns)", len(tables), ", ".join(tables),
                 sum(len(db_import.WRITTEN_COLUMNS[t]) for t in tables))
    for table, cols in found["leave_out"].items():
        for c in cols:
            msg = (f"{table}.{c} is missing from the live table. It is added by "
                   f"{found['where'][f'{table}.{c}']}, which has not been applied. This run "
                   f"leaves it out of every {table} row, uploads everything else, and ends FAILED.")
            log.error("MISSING COLUMN %s", msg)
            _annotate("warning", "Missing database column", msg)
    for b in found["blocked"]:
        msg = (f"{b} is missing from the live database ({found['where'][b]}). It cannot be left "
               "out, so that upload fails as before.")
        log.error("MISSING %s %s", "KEY COLUMN" if "." in b else "TABLE", msg)
        _annotate("warning", "Missing database table or key column", msg)
    result = dict(base, status="checked", leave_out=found["leave_out"], blocked=found["blocked"],
                  where=found["where"], reason=found["reason"],
                  title=TITLE_MISSING if found["reason"] else None)
    _write_result(result_path, result)
    return result


def finish(result_path: Path) -> int:
    """The job's last working step: 1 with one plain line if the preflight found anything or
    did not complete; 0 otherwise. The line is printed and posted as an error annotation."""
    try:
        result = json.loads(Path(result_path).read_text(encoding="utf-8"))
        reason, title = result.get("reason"), result.get("title") or TITLE_MISSING
    except (OSError, ValueError, AttributeError) as e:
        reason = (f"{TITLE_UNCHECKED}: the preflight step left no result ({type(e).__name__}); "
                  "see its log. Nothing was left out; this run sent every column, as before.")
        title = TITLE_UNCHECKED
    if not reason:
        return 0
    print(reason, flush=True)
    _annotate("error", title, reason)
    return 1


def result_path() -> Path:
    return Path(os.environ.get(db_import.PREFLIGHT_RESULT_ENV) or DEFAULT_RESULT)


class _NoClient:
    """Stands in for a client that could not be built, so that failure is recorded like any
    other unreadable description instead of stopping the run."""

    def __init__(self, err: Exception):
        self.err = err

    @property
    def postgrest(self):
        raise ColumnCheckError(f"no database client ({type(self.err).__name__}: {self.err})")


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    path = result_path()
    if argv == ["--finish"]:
        return finish(path)
    # A result left by an earlier run must never be read as this one's.
    path.unlink(missing_ok=True)
    tables = db_import.tables_for_args(argv)
    try:
        client = db_import.get_client()
    except Exception as e:  # noqa: BLE001 — recorded as "could not check"; the run goes on
        client = _NoClient(e)
    run_preflight(client, tables, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
