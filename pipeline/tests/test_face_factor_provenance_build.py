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


def test_nothing_in_the_tree_writes_the_legacy_name():
    """The premise of the change. If some script starts writing mop_spread.json, the
    default should be reconsidered rather than left pointing elsewhere.

    THE WHITELIST INCLUDES THIS FILE, and that is not a fudge — it is the self-reference
    every grep-for-a-string test has. The name appears here in the search term and in the
    whitelist itself, so `git grep` finds this file the moment it is tracked. It was
    written with the whitelist omitting itself, passed while the file was still untracked,
    and went red on main as soon as it was committed. Recorded rather than quietly
    patched: an assertion whose own source satisfies its search pattern is a shape to
    recognise, not a one-off slip.
    """
    import subprocess

    out = subprocess.run(["git", "grep", "-l", "mop_spread.json", "--",
                          "scripts/", "pipeline/"],
                         cwd=ROOT, capture_output=True, text=True).stdout.split()
    # Mentioned in prose or as a search term by these; WRITTEN by none of them.
    assert set(out) <= {"scripts/build_face_factors.py",
                        "scripts/mop_face_validation.py",
                        "pipeline/data/spot_face_factors.json",
                        "pipeline/tests/test_face_factor_provenance_build.py"}, out


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
