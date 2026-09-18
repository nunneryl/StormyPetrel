"""Every script --selftest runs here, so they stop being checks nobody invokes.

THE DEFECT. Fifteen scripts carry a --selftest block, some of them hundreds of
assertions long, and until this file NOTHING RAN ANY OF THEM: no CI workflow, no test, no
pre-commit hook. Mutation testing found the hole twice — a mutant reverting a selftest's
roster count from 152 to 153 survived the whole suite, and another weakening a selftest
assertion to a tautology survived too. Both were caught only because the mutation runs
happened to shell out to --selftest by hand.

WHY IT MATTERS ON 2026-09-19. build_face_factors.py runs tomorrow to produce the file the
P0-1 stability question is read from. Its selftest is the only automated check on the
window-provenance and staleness guards added for exactly that run, and a check nobody
invokes will not catch anything tomorrow.

DISCOVERY, NOT A REGISTRY. The list is found by parsing each file and looking for an
argparse registration of --selftest, so a NEW script with a selftest is covered the moment
it lands and nobody has to remember to add it here. A hardcoded list is the same failure
shape as the RUN_ON literal and the eight-places "7-day" string: correct on the day it is
written and wrong from then on.

WHY AST AND NOT A TEXT SEARCH. A grep for the string matches any file that merely mentions
it — including this one, and including pipeline/tests/test_diff_face_factors.py, which
passes "--selftest" to a subprocess. That self-reference is precisely the bug shipped in
#217, where a test grepped the tree for a string that appeared in its own source and went
red the moment it was tracked. Matching the CALL rather than the text cannot make that
mistake.

IN-PROCESS, NOT SUBPROCESS. Every one of these modules imports cleanly and every one has
main(argv=None), so nothing here shells out: a module exposing run_selftest() is called
directly, and one whose selftest exists only as CLI behaviour inside main() is invoked as
main(["--selftest"]). Which mechanism each uses is recorded per test.
"""
import ast
import contextlib
import glob
import importlib
import io
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

# Where a runnable script can live. Both are scanned recursively, so a new subdirectory is
# covered without editing this.
SCAN_ROOTS = ("scripts", "pipeline")

SELFTEST_FLAGS = ("--selftest", "--self-test")

# Skipped by the repo-wide cross-check only. Not a coverage decision — these hold no
# runnable pipeline script, and walking them is slow and noisy.
_SKIP_DIRS = {".git", "node_modules", ".next", "__pycache__", ".venv", "venv",
              ".mypy_cache", ".pytest_cache", "dist", "build"}

# The scripts the 2026-09-19 regeneration and its reading depend on. Named so that losing
# a selftest on any of them fails loudly rather than silently reducing coverage; this is a
# FLOOR on what must be covered, not the registry — discovery is the registry.
P0_1_CHAIN = ("scripts/mop_face_validation.py",
              "scripts/build_face_factors.py",
              "scripts/diff_face_factors.py")


# --------------------------------------------------------------------------- #
# Discovery.                                                                   #
# --------------------------------------------------------------------------- #

def registers_a_selftest_flag(path):
    """True when this file calls add_argument("--selftest") — the CALL, not the string.

    A syntax error is reported as not-registering rather than raised, because a file this
    cannot parse is a separate problem and is caught by the cross-check below.
    """
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except (SyntaxError, UnicodeDecodeError):
        return False
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "add_argument"
                and node.args
                and isinstance(node.args[0], ast.Constant)
                and node.args[0].value in SELFTEST_FLAGS):
            return True
    return False


def module_name_for(relpath):
    """The import name for a discovered file.

    THIS DISTINCTION IS LOAD-BEARING, not cosmetic. pipeline/forecast/*.py are PACKAGE
    modules using relative imports; importing one as a top-level module leaves it with no
    parent package, and `from .buoys import ...` then fails. In
    pipeline/forecast/ndbc_spectral.py that import sits inside a try/except which swallows
    the error (:325-326), so parse_std_wind silently returns {} and one selftest check
    fails — a FALSE failure that looks like rot. scripts/*.py have no parent package and
    are imported by bare name with scripts/ on the path.
    """
    if relpath.startswith("pipeline/"):
        return relpath[:-3].replace("/", ".")
    return os.path.basename(relpath)[:-3]


def mentions_a_selftest_flag(path):
    """True when the flag appears as a string literal anywhere in the file — crude."""
    text = open(path, encoding="utf-8", errors="replace").read()
    return any(f'"{flag}"' in text or f"'{flag}'" in text for flag in SELFTEST_FLAGS)


def discover():
    """[(relpath, module_name)] for every file registering a --selftest flag, sorted.

    BOTH CHECKS MUST AGREE, and they fail in opposite directions, which is why running
    them together is not redundant. The AST check can over-match (a predicate bug that
    matched every add_argument call pulled in 50 modules including db_import and
    interpret); the text check cannot, because those files never name the flag. The text
    check can over-match (any file that merely mentions the string); the AST check cannot.

    THE OVER-MATCH IS A SAFETY PROBLEM, NOT A TIDINESS ONE. Every discovered module gets
    main(["--selftest"]) called on it. Invoking that on pipeline/db_import.py or
    pipeline/interpret.py means real work — network, database, a long run — and when this
    was tried the whole suite hung rather than failing. A hang is the worst failure mode
    available: it reads as no result rather than as a red.
    """
    found = []
    for root in SCAN_ROOTS:
        for path in glob.glob(os.path.join(ROOT, root, "**", "*.py"), recursive=True):
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            if registers_a_selftest_flag(path) and mentions_a_selftest_flag(path):
                found.append((rel, module_name_for(rel)))
    return sorted(set(found))


DISCOVERED = discover()


def _run_one(relpath, module_name):
    """(rc, mechanism, output). Never raises for a selftest failure — returns its rc."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
        mod = importlib.import_module(module_name)
        fn = getattr(mod, "run_selftest", None)
        if callable(fn):
            mechanism = "run_selftest()"
            rc = fn()
        else:
            # The selftest exists only as CLI behaviour inside main(). Calling main with
            # an explicit argv is still a direct call, not a shell-out.
            mechanism = 'main(["--selftest"])'
            rc = mod.main(["--selftest"])
    return rc, mechanism, buf.getvalue()


# --------------------------------------------------------------------------- #
# One generated test per discovered script, so a failure names the script.     #
# --------------------------------------------------------------------------- #

def _make_test(relpath, module_name):
    def test():
        rc, mechanism, output = _run_one(relpath, module_name)
        assert rc == 0, (
            f"\n{relpath} --selftest FAILED (rc={rc}, invoked via {mechanism})\n"
            + "\n".join(line for line in output.splitlines()
                        if "FAIL" in line or "Error" in line)[:4000]
            + f"\n\nfull output:\n{output[-4000:]}")
    test.__name__ = "test_selftest_" + relpath[:-3].replace("/", "_").replace("-", "_")
    test.__doc__ = f"{relpath} --selftest must exit 0."
    return test


for _rel, _mod in DISCOVERED:
    _t = _make_test(_rel, _mod)
    globals()[_t.__name__] = _t
del _rel, _mod, _t


# --------------------------------------------------------------------------- #
# Guards on the discovery itself.                                              #
# --------------------------------------------------------------------------- #

def test_discovery_found_something():
    """A discovery that silently returns nothing would make every test above vacuous —
    and vacuous coverage is exactly the state this file exists to end."""
    assert DISCOVERED, "no --selftest registrations discovered; the scan is broken"
    assert len(DISCOVERED) >= 10, [r for r, _ in DISCOVERED]


def test_the_p0_1_chain_is_covered():
    """The three scripts the 2026-09-19 regeneration and its reading depend on. A FLOOR,
    not a registry: discovery is the registry, and this fails if one of these loses its
    selftest rather than if a new script gains one."""
    found = {rel for rel, _ in DISCOVERED}
    for required in P0_1_CHAIN:
        assert required in found, f"{required} no longer registers a --selftest"


def test_every_file_that_mentions_the_flag_is_either_discovered_or_a_test():
    """THE ANTI-STALENESS CROSS-CHECK: a script that gains a selftest discovery misses
    would fail here.

    The textual scan is the crude, over-broad complement to the AST scan. Anything it
    finds must be either a real registration (discovered) or a test file that merely
    mentions the flag.

    IT SCANS THE WHOLE REPOSITORY, NOT SCAN_ROOTS, and that independence is the whole
    value. A first draft used SCAN_ROOTS on both sides, so narrowing that constant to
    ("scripts",) dropped the four pipeline/forecast modules from coverage AND from the
    check that would have noticed — a guard that its own subject can switch off guards
    nothing. Mutation testing found it.

    TEST FILES ARE EXEMPT BY NAME, and that exemption is the point rather than a fudge:
    this very file contains "--selftest" in SELFTEST_FLAGS, so a scan without the
    exemption would flag itself. That is the self-reference bug shipped in #217, where a
    test grepped for a string present in its own source and went red as soon as it was
    tracked — recognised here instead of repeated.
    """
    mentions = set()
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]
        for name in filenames:
            if not name.endswith(".py") or name.startswith("test_"):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
            if mentions_a_selftest_flag(path):
                mentions.add(rel)
    discovered = {rel for rel, _ in DISCOVERED}

    # THE SCAN MUST HAVE ACTUALLY SCANNED. Without this the guard is vacuous when the walk
    # finds nothing: an empty `mentions` makes `missed` empty and the test passes while
    # checking nothing at all. Every discovered file contains the flag as a literal by
    # construction — that is what the AST matched — so the textual scan must be a superset.
    # Mutation testing emptied the walk and this test still passed.
    assert discovered <= mentions, (
        "the repo-wide scan missed files that discovery found, so it is not scanning what "
        f"it claims: {sorted(discovered - mentions)}")

    missed = mentions - discovered
    assert not missed, (
        f"{len(missed)} file(s) mention a selftest flag but were not discovered, so their "
        f"selftest is not being run: {sorted(missed)}")


def test_discovery_does_not_pick_up_files_that_only_mention_the_flag():
    """The converse. pipeline/tests/test_diff_face_factors.py passes "--selftest" to a
    subprocess and registers nothing; a text-matching discovery would try to import it as
    a script."""
    discovered = {rel for rel, _ in DISCOVERED}
    assert "pipeline/tests/test_diff_face_factors.py" not in discovered
    assert not any(os.path.basename(r).startswith("test_") for r in discovered), discovered


def test_package_modules_are_imported_under_their_package():
    """Not cosmetic — see module_name_for. A pipeline/forecast module imported by bare
    name loses its parent package and its relative imports fail; in ndbc_spectral that
    failure is swallowed by a try/except and surfaces as a FALSE selftest failure.
    """
    assert module_name_for("pipeline/forecast/mop.py") == "pipeline.forecast.mop"
    assert module_name_for("scripts/build_face_factors.py") == "build_face_factors"
    for rel, mod in DISCOVERED:
        if rel.startswith("pipeline/"):
            assert mod == rel[:-3].replace("/", "."), (rel, mod)
            assert "." in mod, f"{rel} would import with no parent package"


def test_a_selftest_flag_is_matched_by_the_call_not_by_the_string():
    """The AST predicate, exercised directly on synthetic sources.

    Pinned because the difference between matching a CALL and matching TEXT is the whole
    reason discovery can be trusted, and it is invisible from the discovered list alone.
    """
    import tempfile

    d = tempfile.mkdtemp()
    real = os.path.join(d, "real.py")
    with open(real, "w") as fh:
        fh.write('import argparse\n'
                 'ap = argparse.ArgumentParser()\n'
                 'ap.add_argument("--selftest", action="store_true")\n')
    assert registers_a_selftest_flag(real) is True

    mention = os.path.join(d, "mention.py")
    with open(mention, "w") as fh:
        fh.write('CMD = ["python3", "x.py", "--selftest"]\n'
                 '# a comment about --selftest\n')
    assert registers_a_selftest_flag(mention) is False

    hyphen = os.path.join(d, "hyphen.py")
    with open(hyphen, "w") as fh:
        fh.write('import argparse\n'
                 'argparse.ArgumentParser().add_argument("--self-test")\n')
    assert registers_a_selftest_flag(hyphen) is True

    unrelated = os.path.join(d, "other.py")
    with open(unrelated, "w") as fh:
        fh.write('import argparse\n'
                 'argparse.ArgumentParser().add_argument("--verbose")\n')
    assert registers_a_selftest_flag(unrelated) is False


def test_a_file_that_cannot_be_parsed_is_not_silently_treated_as_covered():
    import tempfile

    broken = os.path.join(tempfile.mkdtemp(), "broken.py")
    with open(broken, "w") as fh:
        fh.write("def (:\n")
    assert registers_a_selftest_flag(broken) is False


def test_a_new_script_with_a_selftest_would_be_discovered_without_registration():
    """The property the brief asked for, demonstrated rather than asserted about.

    A temporary script is written into scripts/, discovery is re-run, and it appears — no
    edit to this file, no list to update.
    """
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".py", prefix="zz_discovery_probe_",
                                dir=os.path.join(ROOT, "scripts"))
    os.close(fd)
    try:
        with open(path, "w") as fh:
            fh.write('import argparse\n'
                     'def main(argv=None):\n'
                     '    ap = argparse.ArgumentParser()\n'
                     '    ap.add_argument("--selftest", action="store_true")\n'
                     '    return 0\n')
        rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
        assert rel in {r for r, _ in discover()}, "a new script was not discovered"
    finally:
        os.unlink(path)
    # and it is gone again once removed, so the probe cannot leak into a later run
    assert rel not in {r for r, _ in discover()}


# --------------------------------------------------------------------------- #
# The mechanism itself, proven against a selftest that is KNOWN to fail.       #
#                                                                              #
# WITHOUT THESE, THIS WHOLE FILE COULD BE INERT. Mutation testing weakened the  #
# generated assertion to a tautology, and separately made _run_one return 0     #
# without calling anything — all fifteen generated tests still passed, because  #
# nothing checked that they can detect a failure. Wiring that cannot fail is    #
# the same nothing as a selftest nobody invokes.                               #
# --------------------------------------------------------------------------- #

def _temp_script(body, prefix):
    """Write a throwaway module into scripts/ and return (relpath, module_name)."""
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".py", prefix=prefix,
                                dir=os.path.join(ROOT, "scripts"))
    os.close(fd)
    with open(path, "w") as fh:
        fh.write(body)
    rel = os.path.relpath(path, ROOT).replace(os.sep, "/")
    return path, rel, module_name_for(rel)


_FAILING_RUN_SELFTEST = (
    'import argparse\n'
    'def run_selftest():\n'
    '    print("  FAIL deliberately broken check")\n'
    '    return 1\n'
    'def main(argv=None):\n'
    '    ap = argparse.ArgumentParser()\n'
    '    ap.add_argument("--selftest", action="store_true")\n'
    '    return run_selftest()\n'
)

_FAILING_CLI_ONLY = (
    'import argparse\n'
    'def main(argv=None):\n'
    '    ap = argparse.ArgumentParser()\n'
    '    ap.add_argument("--selftest", action="store_true")\n'
    '    print("  FAIL deliberately broken check")\n'
    '    return 1\n'
)


def _expect_generated_test_to_fail(body, prefix):
    path, rel, mod = _temp_script(body, prefix)
    try:
        rc, mechanism, _ = _run_one(rel, mod)
        assert rc == 1, f"_run_one returned {rc}; it is not invoking the selftest"
        try:
            _make_test(rel, mod)()
        except AssertionError:
            return mechanism
        raise AssertionError("the generated test PASSED on a failing selftest")
    finally:
        os.unlink(path)
        sys.modules.pop(mod, None)


def test_the_wiring_detects_a_failing_run_selftest():
    mechanism = _expect_generated_test_to_fail(_FAILING_RUN_SELFTEST, "zz_fail_fn_")
    assert mechanism == "run_selftest()"


def test_the_wiring_detects_a_failing_cli_only_selftest():
    """The other half of the split — a module whose selftest lives only inside main()."""
    mechanism = _expect_generated_test_to_fail(_FAILING_CLI_ONLY, "zz_fail_cli_")
    assert mechanism == 'main(["--selftest"])'


def test_the_wiring_passes_a_selftest_that_succeeds():
    """The converse, so the two tests above pin detection rather than a blanket failure."""
    path, rel, mod = _temp_script(
        _FAILING_RUN_SELFTEST.replace("return 1", "return 0"), "zz_pass_")
    try:
        rc, _, _ = _run_one(rel, mod)
        assert rc == 0
        _make_test(rel, mod)()          # must not raise
    finally:
        os.unlink(path)
        sys.modules.pop(mod, None)


def test_the_failure_message_carries_the_script_and_the_output():
    """A red here must say which script and why without a rerun."""
    path, rel, mod = _temp_script(_FAILING_RUN_SELFTEST, "zz_msg_")
    try:
        try:
            _make_test(rel, mod)()
        except AssertionError as e:
            msg = str(e)
            assert rel in msg
            assert "deliberately broken check" in msg
            assert "rc=1" in msg
        else:
            raise AssertionError("no failure raised")
    finally:
        os.unlink(path)
        sys.modules.pop(mod, None)


# --------------------------------------------------------------------------- #
# The mechanism split, recorded so it is visible rather than inferred.         #
# --------------------------------------------------------------------------- #

def test_every_discovered_script_is_runnable_without_a_subprocess():
    """Direct calls throughout: run_selftest() where it exists, main(["--selftest"])
    where the selftest is CLI-only. If a future script offers neither, this fails here
    with a clear reason rather than in a generated test with an AttributeError."""
    for rel, mod in DISCOVERED:
        m = importlib.import_module(mod)
        has_fn = callable(getattr(m, "run_selftest", None))
        has_main = callable(getattr(m, "main", None))
        assert has_fn or has_main, f"{rel} exposes neither run_selftest nor main"


def test_this_file_shells_out_to_nothing():
    """Every selftest is a direct call, asserted so shelling out cannot creep back in.

    CHECKED BY IMPORT, NOT BY TEXT — and the first draft of this test got it wrong in the
    way this file spends two paragraphs warning about: it grepped its own source for the
    word, which appears in the prose explaining why it must not appear. Parsing the
    imports asks the real question.
    """
    tree = ast.parse(open(os.path.join(HERE, "test_script_selftests.py"),
                          encoding="utf-8").read())
    banned = {"subprocess", "os.system", "popen", "commands", "pty"}
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    assert not (imported & banned), sorted(imported & banned)
