"""build_face_factors must describe the measurement it read, not the day it ran.

TWO BLOCKERS ON THE 2026-09-19 REGENERATION, both pinned here.

DATES. RUN_ON / WINDOW_T0 / WINDOW_T1 were module-level literals fixed at 2026-09-01.
build() took a `today=` parameter, main() never passed it, and there was no flag — so
every regeneration after that date wrote a file in which measurement.run_on,
measurement.window and every record's measured_on and window asserted a measurement that
had not happened, while generated_at quietly told the truth. The forecast-row stamp
(face_correction.face_correction_stamp) embeds that measured_on, so its human-readable
half would have carried a 09-01 date for 09-19 data while its fingerprint changed.

INPUT NAME. The script read scripts/mop_spread.json; the harness writes
scripts/mop_face_validation_out.json; nothing in the tree ever wrote the first name. Same
by_spot shape, so a leftover from an older run would have regenerated the OLD file in
silence.

WHAT IS DELIBERATELY NOT TESTED HERE: the factor arithmetic, FACE_FACTOR_MAX_IQR_RATIO,
the HELD_OUT list and classify()'s verdicts. This change does not touch them, and
pipeline/tests/test_face_correction.py already owns them.
"""
import datetime
import json
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

import build_face_factors as B                     # noqa: E402
from build_face_factors import (                   # noqa: E402
    GATE_CONSTANT_KEYS,
    KEPT_FACE_RATIO_FIELDS,
    SpreadError,
    assert_fresh,
    build,
    population_gates,
    resolve_window,
    validate_spread_records,
)

FILE_WIN = "the spread file's own window"
FLAG_WIN = "--window-t0/--window-t1"

# A complete record: every field the keep branch copies, present and numeric.
FULL_RATIO = {"median": 2.0, "n": 300, "p10": 1.2, "p25": 1.6, "p75": 2.5, "p90": 3.0}


def _artifact(**over):
    """A minimal well-formed harness artifact."""
    doc = {
        "generated_at": "2026-09-21T00:00:00+00:00",
        "window": {"t0": "2026-09-05", "t1": "2026-09-19", "days_back": 14},
        "constants": {
            "FACE_SHORE_NORMAL_MAX_DELTA": 90.0,
            "MATCH_SEPARATION_M": 2200.0,
            "is_valid_surf_spot_filter_applied": True,
        },
        "by_spot": [{"slug": "banded-spot", "face_ratio": dict(FULL_RATIO)}],
    }
    doc.update(over)
    return doc


def _written(doc):
    """(document, plan) from build() against a temp artifact + one-spot roster."""
    d = tempfile.mkdtemp()
    sp, ro = os.path.join(d, "spread.json"), os.path.join(d, "roster.json")
    json.dump(doc, open(sp, "w"))
    json.dump([{"name": "Banded Spot"}], open(ro, "w"))
    return build(spread_path=sp, roster_path=ro)


def _raises(fn, needle):
    try:
        fn()
    except SpreadError as e:
        return needle in str(e)
    return False


# --------------------------------------------------------------------------- #
# BLOCKER 1 — the dates.                                                       #
# --------------------------------------------------------------------------- #

def test_the_hardcoded_date_literals_are_gone():
    """The defect itself. Named explicitly so a reintroduction fails by name."""
    for gone in ("RUN_ON", "WINDOW_T0", "WINDOW_T1"):
        assert not hasattr(B, gone), f"{gone} is back; the dates would stop describing the data"


def test_no_measurement_date_survives_as_a_literal_in_build():
    """A stricter form of the same, aimed at where a date would be load-bearing.

    Parsed rather than grepped. Comments and docstrings in this module legitimately CITE
    2026-09-01 while explaining the defect, and the selftest uses real-looking dates as
    fixture data. What must not exist is an executable date CONSTANT in build(), which is
    what RUN_ON/WINDOW_T0/WINDOW_T1 became once they were interpolated.
    """
    import ast

    src = open(os.path.join(ROOT, "scripts", "build_face_factors.py")).read()
    tree = ast.parse(src)

    # No module-level constant may hold a date — that is where RUN_ON lived.
    for node in tree.body:
        if isinstance(node, ast.Assign):
            for lit in ast.walk(node):
                if isinstance(lit, ast.Constant) and isinstance(lit.value, str):
                    assert not _looks_like_a_date(lit.value), ast.unparse(node)[:80]

    fn = next(n for n in tree.body
              if isinstance(n, ast.FunctionDef) and n.name == "build")
    body = fn.body[1:] if ast.get_docstring(fn) else fn.body   # drop the docstring
    for node in body:
        for lit in ast.walk(node):
            if isinstance(lit, ast.Constant) and isinstance(lit.value, str):
                assert not _looks_like_a_date(lit.value), (
                    f"build() carries the date literal {lit.value!r}")


def _looks_like_a_date(s):
    import re

    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", s or ""))


def test_the_window_comes_from_the_artifact():
    t0, t1, prov = resolve_window({"window": {"t0": "2026-09-05", "t1": "2026-09-19"}})
    assert (t0, t1) == ("2026-09-05", "2026-09-19")
    assert prov == FILE_WIN


def test_a_full_iso_timestamp_is_reduced_to_its_date():
    """The harness writes datetimes; the factor file has always carried plain dates."""
    t0, t1, _ = resolve_window({"window": {"t0": "2026-09-05T00:00:00+00:00",
                                           "t1": "2026-09-19T11:22:33.456789+00:00"}})
    assert (t0, t1) == ("2026-09-05", "2026-09-19")


def test_no_window_anywhere_aborts_and_does_not_reach_for_the_clock():
    """The one behaviour the brief was most explicit about: FAIL, do not default.

    Asserted three ways — that it raises, that it names the remedy, and that it says why
    today's date is not the answer — because a future 'helpful' fallback would satisfy the
    first alone.
    """
    assert _raises(lambda: resolve_window({}), "records no window")
    assert _raises(lambda: resolve_window({}), "--window-t0")
    assert _raises(lambda: resolve_window({}), "not of when")

    try:
        got = resolve_window({})
    except SpreadError:
        got = None
    assert got is None, f"resolve_window returned {got} instead of raising"

    # and it must not be quietly reading the clock. The word "today" appears in the error
    # message explaining why it will NOT do this, so the check is for a CALL, not a word.
    src = open(os.path.join(ROOT, "scripts", "build_face_factors.py")).read()
    fn = src.split("def resolve_window")[1].split("\ndef ")[0]
    for clock in ("date.today(", "datetime.now(", "time.time(", "utcnow("):
        assert clock not in fn, f"resolve_window reads the clock via {clock}"


def test_the_flags_supply_a_window_when_the_artifact_has_none():
    t0, t1, prov = resolve_window({}, "2026-09-05", "2026-09-19")
    assert (t0, t1) == ("2026-09-05", "2026-09-19")
    assert prov == FLAG_WIN


def test_half_a_window_is_refused():
    assert _raises(lambda: resolve_window({}, "2026-09-05", None), "given together")
    assert _raises(lambda: resolve_window({}, None, "2026-09-19"), "given together")


def test_a_malformed_flag_date_is_refused():
    assert _raises(lambda: resolve_window({}, "05/09/2026", "2026-09-19"), "YYYY-MM-DD")
    assert _raises(lambda: resolve_window({}, "2026-09-05", "not-a-date"), "YYYY-MM-DD")


def test_artifact_and_flags_disagreeing_is_an_abort_not_a_precedence_rule():
    """Picking either would commit a date on a coin-flip, and a wrong date in a committed
    file is not visible by reading it."""
    doc = {"window": {"t0": "2026-09-05", "t1": "2026-09-19"}}
    assert _raises(lambda: resolve_window(doc, "2026-08-18", "2026-09-01"), "disagreement")
    # both candidate windows must be in the message so it can be settled at the source
    assert _raises(lambda: resolve_window(doc, "2026-08-18", "2026-09-01"), "2026-09-19")
    assert _raises(lambda: resolve_window(doc, "2026-08-18", "2026-09-01"), "2026-08-18")


def test_artifact_and_flags_agreeing_is_fine():
    doc = {"window": {"t0": "2026-09-05", "t1": "2026-09-19"}}
    assert resolve_window(doc, "2026-09-05", "2026-09-19")[:2] == ("2026-09-05", "2026-09-19")


def test_the_dates_reach_every_field_that_states_them():
    """Four places named the measurement and all four lied. All four are checked."""
    doc, _ = _written(_artifact())
    m = doc["measurement"]
    assert m["run_on"] == "2026-09-19"
    assert m["window"] == {"t0": "2026-09-05", "t1": "2026-09-19"}
    rec = doc["factors"]["banded-spot"]
    assert rec["measured_on"] == "2026-09-19"
    assert rec["window"] == {"t0": "2026-09-05", "t1": "2026-09-19"}
    # Nowhere in the whole document, including the prose — see the next test for why that
    # last one is not redundant.
    assert "2026-09-01" not in json.dumps(doc)
    assert "2026-08-18" not in json.dumps(doc)


def test_the_prose_carries_the_window_too():
    """A FIFTH PLACE THAT NAMED THE MEASUREMENT, and not one the brief listed.

    _comment hardcoded "14 days of SUMMER (2026-08-18 to 2026-09-01)" — inside the
    paragraph a reader is most likely to trust, since it is the file's stated main
    limitation. It would have gone on asserting a summer window over autumn data.
    """
    doc, _ = _written(_artifact())
    comment = doc["_comment"]
    assert "2026-09-05 to 2026-09-19" in comment
    assert "14-day window" in comment            # 09-05 -> 09-19 is 14 days
    assert "2026-08-18" not in comment
    assert "2026-09-01" not in comment


def test_the_prose_span_is_computed_not_assumed():
    """A 30-day window must not still read '14 days'."""
    doc, _ = _written(_artifact(window={"t0": "2026-08-20", "t1": "2026-09-19"}))
    assert "30-day window, 2026-08-20 to 2026-09-19" in doc["_comment"]


def test_the_prose_points_at_the_real_input_name():
    """It told the reader to regenerate from a file nothing writes."""
    doc, _ = _written(_artifact())
    assert "mop_face_validation_out.json" in doc["_comment"]
    assert "mop_spread.json" not in doc["_comment"]


def test_run_on_is_the_window_end_not_the_build_day():
    """The committed 09-01 file has run_on 2026-09-01 against generated_at 2026-09-03.
    Building days after the window closes is the normal case, which is exactly why
    'today' was never the right default."""
    doc, _ = _written(_artifact())
    m = doc["measurement"]
    assert m["run_on"] == m["window"]["t1"]
    assert m["generated_at"] != m["run_on"]
    assert m["generated_at"].startswith(datetime.datetime.now(datetime.timezone.utc)
                                        .date().isoformat())


def test_the_held_out_records_carry_the_same_dates():
    """Held-out records stamp a window too, and were equally wrong before."""
    doc, _ = _written(_artifact(by_spot=[
        {"slug": "banded-spot", "face_ratio": {**FULL_RATIO, "p25": 1.0, "p75": 9.0}}]))
    rec = doc["held_out"]["banded-spot"]
    assert rec["measured_on"] == "2026-09-19"
    assert rec["window"] == {"t0": "2026-09-05", "t1": "2026-09-19"}


def test_the_file_records_where_its_window_came_from():
    doc, _ = _written(_artifact())
    assert doc["measurement"]["window_from"] == FILE_WIN


# --------------------------------------------------------------------------- #
# BLOCKER 2 — the input name, and the staleness guards.                        #
# --------------------------------------------------------------------------- #

def test_the_default_input_is_what_the_harness_actually_writes():
    """The gap: this script read a name nothing in the tree ever wrote."""
    assert B.SPREAD_PATH.endswith("mop_face_validation_out.json")
    assert B.LEGACY_SPREAD_PATH.endswith("mop_spread.json")
    assert B.SPREAD_PATH != B.LEGACY_SPREAD_PATH


def test_the_default_input_matches_the_harness_output_constant():
    """Checked against the harness rather than against a copy of the string, so renaming
    the harness's OUT breaks this instead of silently re-opening the gap."""
    import mop_face_validation

    assert os.path.abspath(B.SPREAD_PATH) == os.path.abspath(mop_face_validation.OUT)


LEGACY_NAME = "mop_spread.json"

# The one module allowed to name the legacy path IN CODE. It defines LEGACY_SPREAD_PATH so
# a leftover can be reported as ignored, and names it in an error message — neither writes.
LEGACY_NAME_MAY_APPEAR_IN = {"scripts/build_face_factors.py"}


def _string_literals_naming(target, path):
    """[(lineno, value)] for string literals containing *target*, EXCLUDING docstrings.

    THIS REPLACES A `git grep`, AND THE REASON IS THE POINT. The grep matched any file
    that MENTIONED the name, so prose about the test satisfied the test's own search
    pattern. That fired twice: in #217 the whitelist omitted this file, and in #220 a
    docstring in test_python_version_floor.py explaining the #217 bug became a fresh
    instance of it. Both times the new file was still UNTRACKED when the suite was run —
    git grep skips untracked files — so both times it went green locally and red the
    moment it was committed.

    Reading string literals out of the AST removes the whole class rather than the
    instances. Comments are not in the AST at all, and a docstring is identifiable and
    skipped, so writing ABOUT the name can never again break the check. What is left is
    what the test actually cares about: code that references the path.
    """
    import ast

    try:
        tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
    except SyntaxError:
        return []
    docstrings = set()
    for node in ast.walk(tree):
        body = getattr(node, "body", None)
        if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)) and body:
            first = body[0]
            if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                    and isinstance(first.value.value, str)):
                docstrings.add(id(first.value))
    return [(n.lineno, n.value) for n in ast.walk(tree)
            if isinstance(n, ast.Constant) and isinstance(n.value, str)
            and target in n.value and id(n) not in docstrings]


def test_nothing_in_the_tree_writes_the_legacy_name():
    """The premise of the change: nothing writes mop_spread.json, so the input default
    must point at what the harness really produces.

    Test files are skipped because a test that searches for a literal must contain it, and
    a test is by definition not a writer.
    """
    offenders = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames
                       if d not in {".git", "node_modules", "__pycache__", ".venv",
                                    "venv", ".next", "dist", "build"}]
        for name in sorted(filenames):
            if not name.endswith(".py") or name.startswith("test_"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            if rel in LEGACY_NAME_MAY_APPEAR_IN:
                continue
            hits = _string_literals_naming(LEGACY_NAME, path)
            if hits:
                offenders[rel] = hits
    assert not offenders, (
        f"{LEGACY_NAME} is referenced in code outside "
        f"{sorted(LEGACY_NAME_MAY_APPEAR_IN)}: {offenders}")


def test_the_allowed_reference_does_not_write_the_legacy_name():
    """The stronger claim, checked directly rather than inferred from where the string is.

    build_face_factors may NAME the legacy path — that is how it reports a leftover as
    ignored — but it must never open it. Asserted against the source so `--apply` cannot
    start writing that name without this failing.
    """
    import ast

    path = os.path.join(ROOT, "scripts", "build_face_factors.py")
    tree = ast.parse(open(path, encoding="utf-8").read())
    for node in ast.walk(tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                and node.func.id == "open"):
            continue
        mode = None
        if len(node.args) > 1 and isinstance(node.args[1], ast.Constant):
            mode = node.args[1].value
        for kw in node.keywords:
            if kw.arg == "mode" and isinstance(kw.value, ast.Constant):
                mode = kw.value.value
        if mode and any(m in str(mode) for m in ("w", "a", "x", "+")):
            target = ast.unparse(node.args[0]) if node.args else ""
            assert "LEGACY" not in target.upper(), (
                f"build_face_factors opens the legacy path for writing at line "
                f"{node.lineno}: {target}")


def test_the_check_is_blind_to_comments_and_docstrings():
    """The property that ends the recurrence, demonstrated rather than asserted about.

    A file may now say the name in prose as often as it likes; only code counts.
    """
    import tempfile

    d = tempfile.mkdtemp()
    prose = os.path.join(d, "prose.py")
    with open(prose, "w") as fh:
        fh.write('"""A module docstring about mop_spread.json."""\n'
                 "# a comment about mop_spread.json\n"
                 "def f():\n"
                 '    """A function docstring about mop_spread.json."""\n'
                 "    return 1\n")
    assert _string_literals_naming(LEGACY_NAME, prose) == []

    code = os.path.join(d, "code.py")
    with open(code, "w") as fh:
        fh.write('PATH = "scripts/mop_spread.json"\n')
    assert _string_literals_naming(LEGACY_NAME, code) == [(1, "scripts/mop_spread.json")]


def test_the_file_that_broke_it_twice_is_now_clean():
    """#220's docstring in test_python_version_floor.py — prose explaining the #217 bug —
    was itself a fresh instance of it. Under the AST check it is invisible, which is the
    fix rather than a fourth whitelist entry."""
    floor_test = os.path.join(ROOT, "pipeline", "tests", "test_python_version_floor.py")
    assert os.path.exists(floor_test)
    assert LEGACY_NAME in open(floor_test, encoding="utf-8").read(), \
        "the prose that triggered this was removed; the demonstration is now vacuous"
    assert _string_literals_naming(LEGACY_NAME, floor_test) == []


def test_a_stale_artifact_is_refused():
    """generated_at before the window it claims to describe: it cannot hold those hours."""
    assert _raises(lambda: assert_fresh({"generated_at": "2026-09-03T00:00:00+00:00"},
                                        "2026-09-19", FILE_WIN), "stale spread file")


def test_the_staleness_message_names_both_dates():
    def go():
        assert_fresh({"generated_at": "2026-09-03T00:00:00+00:00"}, "2026-09-19", FILE_WIN)
    assert _raises(go, "2026-09-03")
    assert _raises(go, "2026-09-19")


def test_a_same_day_build_is_fresh():
    """The boundary is inclusive: building the moment the window closes is normal."""
    assert assert_fresh({"generated_at": "2026-09-19T23:59:00+00:00"},
                        "2026-09-19", FILE_WIN) == "2026-09-19"


def test_a_later_build_is_fresh():
    assert assert_fresh({"generated_at": "2026-09-21T00:00:00+00:00"},
                        "2026-09-19", FILE_WIN) == "2026-09-21"


def test_an_artifact_window_with_no_timestamp_cannot_be_checked_so_it_is_refused():
    assert _raises(lambda: assert_fresh({}, "2026-09-19", FILE_WIN), "no usable generated_at")


def test_an_operator_supplied_window_with_no_timestamp_is_allowed():
    """The operator has vouched for the dates; there is nothing to cross-check them
    against, and refusing would leave no route for an artifact predating the window
    field."""
    assert assert_fresh({}, "2026-09-19", FLAG_WIN) is None


def test_the_stale_guard_fires_through_build():
    """End to end, not just on the helper."""
    doc = _artifact(generated_at="2026-09-03T00:00:00+00:00")
    assert _raises(lambda: _written(doc), "stale spread file")


# --------------------------------------------------------------------------- #
# A missing source field stops the run; it does not become a null.             #
# --------------------------------------------------------------------------- #

def test_the_fields_checked_are_the_fields_copied():
    """The guard is only correct if its list matches what the keep branch writes."""
    assert set(KEPT_FACE_RATIO_FIELDS) == {"median", "n", "p10", "p25", "p75", "p90"}
    src = open(os.path.join(ROOT, "scripts", "build_face_factors.py")).read()
    keep = src.split('if verdict == "keep":')[1].split("else:")[0]
    for field in ("n", "p10", "p25", "p75", "p90"):
        assert f'fr.get("{field}")' in keep or f'fr["{field}"]' in keep, field


def test_a_complete_record_passes():
    assert validate_spread_records([{"slug": "s", "face_ratio": dict(FULL_RATIO)}],
                                   set(), 1.7) is None


def test_a_kept_record_missing_a_copied_field_aborts():
    """This is the "p25": null incident's shape. It shipped once, the band silently did
    not render, and it took four steps of tracing to find."""
    for field in ("n", "p10", "p90"):
        rec = {"slug": "s", "face_ratio": {k: v for k, v in FULL_RATIO.items()
                                           if k != field}}
        assert _raises(lambda r=rec: validate_spread_records([r], set(), 1.7),
                       "missing a face_ratio field"), field
        assert _raises(lambda r=rec: validate_spread_records([r], set(), 1.7),
                       field), field


def test_the_abort_names_the_spot_as_well_as_the_field():
    rec = {"slug": "half-moon-bay", "face_ratio": {k: v for k, v in FULL_RATIO.items()
                                                   if k != "p10"}}
    assert _raises(lambda: validate_spread_records([rec], set(), 1.7), "half-moon-bay")


def test_an_explicit_null_is_treated_as_missing():
    rec = {"slug": "s", "face_ratio": {**FULL_RATIO, "p90": None}}
    assert _raises(lambda: validate_spread_records([rec], set(), 1.7), "p90")


def test_a_record_that_would_not_be_kept_is_not_held_to_the_keep_fields():
    """An excluded spot never reaches `factors`, so its gaps cannot become nulls there.
    Holding it to the same standard would abort on files that are perfectly usable."""
    wide = {"slug": "s", "face_ratio": {"median": 2.0, "p25": 1.0, "p75": 9.0}}
    assert validate_spread_records([wide], set(), 1.7) is None


def test_a_file_with_no_quartiles_at_all_aborts_rather_than_writing_zero_factors():
    """Every spot would classify unusable and --apply would overwrite the committed
    factors with an empty map. Loud in the plan, silent in the outcome."""
    old = [{"slug": "s", "face_ratio": {"median": 2.0, "n": 300, "p10": 1.0, "p90": 3.0}}]
    assert _raises(lambda: validate_spread_records(old, set(), 1.7), "no record")
    assert _raises(lambda: validate_spread_records(old, set(), 1.7), "p25/p75")


def test_an_empty_artifact_is_not_a_quartile_abort():
    """Nothing to judge is a different condition from 'judged and found stale'."""
    assert validate_spread_records([], set(), 1.7) is None


def test_the_guard_fires_through_build_and_writes_nothing():
    doc = _artifact(by_spot=[{"slug": "banded-spot",
                              "face_ratio": {k: v for k, v in FULL_RATIO.items()
                                             if k != "p10"}}])
    assert _raises(lambda: _written(doc), "missing a face_ratio field")


# --------------------------------------------------------------------------- #
# The CLI contract. A refused build must FAIL, not just complain.              #
# --------------------------------------------------------------------------- #

def _cli(doc, *extra, apply_=True):
    """(returncode, stderr, output_exists) from main() against a temp artifact."""
    import contextlib
    import io

    d = tempfile.mkdtemp()
    sp = os.path.join(d, "mop_face_validation_out.json")
    out = os.path.join(d, "factors.json")
    json.dump(doc, open(sp, "w"))
    argv = ["--spread", sp, "--out", out, *extra]
    if apply_:
        argv.append("--apply")
    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = B.main(argv)
    return rc, err.getvalue(), os.path.exists(out)


# --------------------------------------------------------------------------- #
# The mixed-window gate. A banner on a terminal is not a guard.                #
# --------------------------------------------------------------------------- #

MIXED_INTEGRITY = {
    "verdict": "mixed", "raw_rows": 31000, "fallback_rows": 9000,
    "dropped_rows": 9000, "rows_kept": 31000, "rows_dropped": 9000,
    "safe_to_apply": False,
    "unsafe_reason": "MIXED WINDOW: the joined rows span both sides of migration 017.",
}
CLEAN_INTEGRITY = {
    "verdict": "clean", "raw_rows": 40000, "fallback_rows": 0, "dropped_rows": 0,
    "rows_kept": 40000, "rows_dropped": 0, "safe_to_apply": True, "unsafe_reason": None,
}


def test_apply_is_refused_when_the_window_is_not_safe():
    """THE DEFECT. A rehearsal against a contaminated artifact saw the harness print
    'MIXED WINDOW — NOT SAFE TO APPLY' twice, and then build_face_factors planned and
    wrote 139 factors from that same artifact without comment. The banner existed only
    where nothing downstream could read it."""
    rc, err, wrote = _cli(_artifact(window_integrity=MIXED_INTEGRITY))
    assert rc == 2, rc
    assert not wrote, "factors were written from an unsafe window"
    assert "REFUSING TO WRITE" in err
    assert "31000" in err and "9000" in err          # rows kept and dropped, both shown
    assert "--i-know-the-window-is-mixed" in err     # and the way out is named


def test_a_safe_window_still_writes():
    """The converse, so the gate is a gate and not a wall."""
    rc, _, wrote = _cli(_artifact(window_integrity=CLEAN_INTEGRITY))
    assert rc == 0 and wrote


def test_the_refusal_is_overridable_only_by_the_loudly_named_flag():
    """The flag NAME is part of the contract: it has to be impossible to pass by habit.

    SystemExit, not AssertionError, is what a renamed flag raises — argparse calls
    sys.exit(2) on an unrecognised argument. It is caught here so the failure reads as
    'the flag is gone' rather than as the whole run dying.
    """
    doc = _artifact(window_integrity=MIXED_INTEGRITY)
    try:
        rc, _, wrote = _cli(doc, "--i-know-the-window-is-mixed")
    except SystemExit as e:
        raise AssertionError(
            "--i-know-the-window-is-mixed was rejected by the parser (argparse exited "
            f"{e.code}); the override flag has been renamed or removed") from None
    assert rc == 0 and wrote


def test_the_override_is_recorded_in_the_OUTPUT_not_just_the_terminal():
    """The whole point. An operator who overrides leaves a mark in the artifact, because
    the next reader of that file is not the operator at the terminal."""
    import contextlib
    import io
    import tempfile

    d = tempfile.mkdtemp()
    sp = os.path.join(d, "mop_face_validation_out.json")
    out = os.path.join(d, "factors.json")
    json.dump(_artifact(window_integrity=MIXED_INTEGRITY), open(sp, "w"))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc = B.main(["--spread", sp, "--out", out, "--apply",
                     "--i-know-the-window-is-mixed"])
    assert rc == 0
    rec = json.load(open(out))["measurement"]["window_integrity_override"]
    assert rec["overridden"] is True
    assert rec["flag"] == "--i-know-the-window-is-mixed"
    assert rec["verdict"] == "mixed"
    assert rec["rows_kept"] == 31000 and rec["rows_dropped"] == 9000
    assert "NOT SAFE TO APPLY" in rec["note"]


def test_a_safe_build_carries_no_override_record():
    """Absence of the key is the positive assertion that no override happened."""
    import contextlib
    import io
    import tempfile

    d = tempfile.mkdtemp()
    sp = os.path.join(d, "mop_face_validation_out.json")
    out = os.path.join(d, "factors.json")
    json.dump(_artifact(window_integrity=CLEAN_INTEGRITY), open(sp, "w"))
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc = B.main(["--spread", sp, "--out", out, "--apply"])
    assert rc == 0
    assert "window_integrity_override" not in json.load(open(out))["measurement"]


def test_a_dry_run_against_an_unsafe_window_is_not_blocked():
    """The gate is on --apply. A plan is not a write, and refusing to even LOOK at a
    contaminated artifact would make it harder to diagnose."""
    rc, _, wrote = _cli(_artifact(window_integrity=MIXED_INTEGRITY), apply_=False)
    assert rc == 0 and not wrote


def test_an_artifact_with_no_integrity_record_is_reported_not_refused():
    """Unverifiable, not unsafe — the same posture as an unrecorded gate. An artifact
    predating this field is not evidence of contamination, and refusing it would block a
    legitimate rebuild; but silence would read as clean, so it is said out loud."""
    doc = _artifact()
    assert "window_integrity" not in doc
    rc, err, wrote = _cli(doc)
    assert rc == 0 and wrote
    assert "no window_integrity" in err
    assert "UNRECORDED, not clean" in err


def test_an_integrity_block_that_predates_safe_to_apply_is_not_refused():
    """The dict is there; the field is not, because the artifact was written before the
    field existed. The gate keys on `is False`, so this is allowed through — refusing an
    artifact merely for being old would block a legitimate rebuild, and the field being
    absent is not the same fact as its being False."""
    old_style = {"verdict": "clean", "raw_rows": 40000, "fallback_rows": 0,
                 "dropped_rows": 0}
    assert "safe_to_apply" not in old_style
    rc, _, wrote = _cli(_artifact(window_integrity=old_style))
    assert rc == 0 and wrote


def test_the_harness_records_kept_and_dropped_and_the_verdict():
    """The producing side of the contract, EXERCISED rather than grepped.

    An earlier version of this test only checked that the four field names appeared in the
    harness source. Mutation testing walked straight through it: `"rows_dropped": 0` and
    `"rows_kept": total_raw` both kept the names and lied about the numbers, and both
    survived. A consumer reads the value, not the key.
    """
    import mop_face_validation as MF

    b = MF.window_integrity_block(
        per_spot=[{"joined_hours": 300, "provenance": {"dropped": 90}},
                  {"joined_hours": 10, "provenance": {"dropped": 0}}],
        total_raw=31000, total_fallback=9000,
        window_verdicts={"steamer-lane": "mixed"}, stamps={"seam:abc": 300})
    assert b["rows_kept"] == 310            # 300 + 10, by hand
    assert b["rows_dropped"] == 90          # 90 + 0, by hand
    assert b["raw_rows"] == 31000 and b["fallback_rows"] == 9000
    assert b["verdict"] == "mixed"
    assert b["safe_to_apply"] is False
    assert "MIXED WINDOW" in b["unsafe_reason"]
    # The trap the docstring warns about: kept is NOT the raw row count.
    assert b["rows_kept"] != b["raw_rows"]


def test_rows_kept_is_the_retained_count_and_skips_errored_spots():
    """A spot that errored has no measurement, so its rows are in neither total. Counting
    them would inflate the kept figure that the override record publishes."""
    import mop_face_validation as MF

    b = MF.window_integrity_block(
        per_spot=[{"joined_hours": 40, "provenance": {"dropped": 5}},
                  {"error": "no orientation_deg", "joined_hours": 999,
                   "provenance": {"dropped": 999}}],
        total_raw=100, total_fallback=0, window_verdicts={}, stamps={})
    assert b["rows_kept"] == 40 and b["rows_dropped"] == 5


def test_under_legacy_kept_equals_fallback_and_nothing_is_dropped():
    """The case that makes raw_rows the wrong definition of kept. Under legacy every row is
    unstamped, every row is retained, and kept == fallback_rows — the opposite of mixed."""
    import mop_face_validation as MF

    b = MF.window_integrity_block(
        per_spot=[{"joined_hours": 800, "provenance": {"dropped": 0}}],
        total_raw=0, total_fallback=800, window_verdicts={}, stamps={})
    assert b["verdict"] == "legacy"
    assert b["rows_kept"] == 800 == b["fallback_rows"] and b["rows_dropped"] == 0
    assert b["safe_to_apply"] is True
    assert b["unsafe_reason"] is None


def test_unsafe_reason_is_present_under_mixed_and_absent_otherwise():
    """The refusal banner prints this string. If it went None the operator would be told
    'None' where the explanation belongs."""
    import mop_face_validation as MF

    def reason(raw, fallback):
        return MF.window_integrity_block([], raw, fallback, {}, {})["unsafe_reason"]

    assert reason(5, 5) and "migration 017" in reason(5, 5)
    assert reason(5, 0) is None
    assert reason(0, 5) is None
    assert reason(0, 0) is None


def test_safe_to_apply_is_false_exactly_when_the_verdict_is_mixed():
    """Derived from classify_window rather than asserted, so the two cannot drift."""
    import mop_face_validation as MF

    assert MF.classify_window(5, 5) == "mixed"        # -> unsafe
    assert MF.classify_window(5, 0) == "clean"        # -> safe
    assert MF.classify_window(0, 5) == "legacy"       # -> safe, deliberately: see below
    assert MF.classify_window(0, 0) == "empty"
    src = open(os.path.join(ROOT, "scripts", "mop_face_validation.py"),
               encoding="utf-8").read()
    assert '"safe_to_apply": overall != "mixed"' in src, (
        "the gate must key on 'mixed' alone. LEGACY is a different hazard — every row "
        "predates migration 017, so nothing was dropped and the window is not short — "
        "and extending the refusal to it is a separate decision, not a tidy-up.")


def test_a_refused_build_exits_nonzero_and_writes_nothing():
    """The regenerate command is `mop_face_validation.py && build_face_factors.py --apply`.
    An abort that exits 0 would let that `&&` report success on a file it never wrote —
    and the operator would go looking for a diff that does not exist.
    """
    for doc, why in (
        (_artifact(generated_at="2026-09-03T00:00:00+00:00"), "stale"),
        (_artifact(window=None), "no window"),
        (_artifact(by_spot=[{"slug": "banded-spot",
                             "face_ratio": {k: v for k, v in FULL_RATIO.items()
                                            if k != "p10"}}]), "missing field"),
    ):
        if doc.get("window") is None:
            doc.pop("window", None)
        rc, err, wrote = _cli(doc)
        assert rc == 2, f"{why}: exited {rc}"
        assert not wrote, f"{why}: wrote an output file anyway"
        assert err.strip(), f"{why}: failed silently"


def test_a_good_build_exits_zero_and_writes():
    rc, _, wrote = _cli(_artifact())
    assert rc == 0
    assert wrote


def test_a_dry_run_exits_zero_and_writes_nothing():
    """--apply must stay required; this change must not have made a dry run write."""
    rc, _, wrote = _cli(_artifact(), apply_=False)
    assert rc == 0
    assert not wrote


def test_the_flags_rescue_an_artifact_with_no_window():
    doc = _artifact()
    doc.pop("window")
    rc, _, wrote = _cli(doc, "--window-t0", "2026-09-05", "--window-t1", "2026-09-19")
    assert rc == 0
    assert wrote


def test_a_missing_input_still_names_the_harness():
    import contextlib
    import io

    err = io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
        rc = B.main(["--spread", os.path.join(tempfile.mkdtemp(), "nope.json")])
    assert rc == 2
    assert "mop_face_validation.py" in err.getvalue()


# --------------------------------------------------------------------------- #
# The population gates: copied, never invented.                                #
# --------------------------------------------------------------------------- #

def test_the_three_gates_are_the_ones_asked_for():
    assert set(GATE_CONSTANT_KEYS) == {
        "FACE_SHORE_NORMAL_MAX_DELTA",
        "MATCH_SEPARATION_M",
        "is_valid_surf_spot_filter_applied",
    }


def test_the_gates_are_copied_verbatim_into_the_measurement_block():
    doc, _ = _written(_artifact())
    assert doc["measurement"]["population_gates"] == {
        "FACE_SHORE_NORMAL_MAX_DELTA": 90.0,
        "MATCH_SEPARATION_M": 2200.0,
        "is_valid_surf_spot_filter_applied": True,
    }


def test_an_older_artifacts_gates_are_recorded_as_they_were():
    """The point of copying rather than importing. A 35-degree measurement must keep
    saying 35 even though this process can see a 90 — otherwise the record asserts that
    old factors came from a population that never produced them."""
    doc, _ = _written(_artifact(constants={"FACE_SHORE_NORMAL_MAX_DELTA": 35.0}))
    gates = doc["measurement"]["population_gates"]
    assert gates["FACE_SHORE_NORMAL_MAX_DELTA"] == 35.0
    assert gates["MATCH_SEPARATION_M"] is None


def test_a_gate_the_artifact_omits_is_null_and_not_invented():
    doc, _ = _written(_artifact(constants={}))
    assert doc["measurement"]["population_gates"] == {k: None for k in GATE_CONSTANT_KEYS}


def test_the_builder_never_imports_the_live_gate_values():
    """The mechanism behind the test above, asserted against the source.

    Importing MATCH_SEPARATION_M here would stamp today's gates onto yesterday's
    measurement. The names appear in GATE_CONSTANT_KEYS as strings — that is the lookup,
    not the value — so this checks for an IMPORT of the harness, which is the only way a
    live value could get in.
    """
    src = open(os.path.join(ROOT, "scripts", "build_face_factors.py")).read()
    for line in src.splitlines():
        stripped = line.strip()
        if stripped.startswith(("import ", "from ")):
            assert "mop_face_validation" not in stripped, stripped
            assert "mop_ca_rollout" not in stripped, stripped
            assert "mop_handful_slice" not in stripped, stripped
    assert not hasattr(B, "MATCH_SEPARATION_M")
    assert not hasattr(B, "FACE_SHORE_NORMAL_MAX_DELTA")


def test_null_gates_are_labelled_so_they_do_not_read_as_off():
    doc, _ = _written(_artifact(constants={}))
    note = doc["measurement"]["population_gates_note"]
    assert "null means" in note
    assert "NOT that the gate was off" in note


def test_the_harness_now_records_the_validity_filter():
    """The third gate did NOT exist in the run report and had to be added there — it is
    the only one of the three that is not a number, and nothing emitted it."""
    src = open(os.path.join(ROOT, "scripts", "mop_face_validation.py")).read()
    assert '"is_valid_surf_spot_filter_applied"' in src


def test_the_harness_probes_the_predicate_instead_of_asserting_a_literal():
    """A literal True would keep saying True after someone deleted the clause — the same
    failure mode as the RUN_ON literal this commit removes."""
    import mop_face_validation as MF

    probe = {"region_hint": "California", "swell_window_source": "nwps",
             "name": "probe", "is_valid_surf_spot": False}
    assert MF.is_population(probe) is False          # so the flag reads True
    src = open(os.path.join(ROOT, "scripts", "mop_face_validation.py")).read()
    block = src.split('"is_valid_surf_spot_filter_applied"')[1].split("},")[0]
    assert "is_population" in block, "the flag must be derived, not written as a literal"


def test_the_artifacts_own_timestamp_is_carried_through():
    """So a factor file can be tied back to the exact run that produced it, independently
    of the window."""
    doc, _ = _written(_artifact())
    assert doc["measurement"]["spread_artifact_generated_at"] == "2026-09-21T00:00:00+00:00"


# --------------------------------------------------------------------------- #
# The committed baseline must not move.                                        #
# --------------------------------------------------------------------------- #

def test_the_committed_09_01_file_is_untouched():
    """It is the regeneration's comparison baseline. This change is to the generator only.

    Pinned on the fields that identify the measurement rather than on a byte hash, so the
    test says what it is protecting.
    """
    d = json.load(open(os.path.join(ROOT, "pipeline", "data", "spot_face_factors.json")))
    assert len(d["factors"]) == 130
    assert len(d["held_out"]) == 7
    m = d["measurement"]
    assert m["run_on"] == "2026-09-01"
    assert m["window"] == {"t0": "2026-08-18", "t1": "2026-09-01"}
    assert m["generated_at"] == "2026-09-03T23:20:28+00:00"
    # The baseline predates every field this commit adds, and must not have acquired one.
    for added in ("window_from", "population_gates", "spread_artifact_generated_at"):
        assert added not in m, f"the 09-01 baseline was regenerated; {added} appeared"
