"""The CI workflow's versions must not drift from the versions this repo declares.

THE DEFECT THIS EXISTS FOR. A GitHub workflow cannot import Python, so the interpreter
versions the suite runs under have to be written into YAML by hand. That hand-written
copy is a second declaration of a fact that already has a home:

    MIN_PYTHON in test_python_version_floor.py ...... the floor the code must run on
    python-version: '3.12' in the nine workflows ..... what the fleet installs
    "@types/node" in frontend/package.json ........... the Node this code is typed against

A second copy that nothing checks is how the weekend of 2026-09-20 happened. config.py
shipped PEP 604 because the machine that wrote it was 3.11 and the machine that ran it was
3.9, and nothing in the tree connected those two numbers. Raising MIN_PYTHON to 3.12 while
tests.yml still said '3.9' would be the same mistake wearing a different hat: the floor
would move, CI would keep proving the old one, and the gap would not surface until
something broke on a laptop.

So every version in tests.yml is read back out of the YAML here and held against the
declaration it is a copy of. Raising the floor is still one edit to MIN_PYTHON — these
tests fail until tests.yml follows, which is the point.

IT PARSES THE YAML BY HAND, AND THAT IS DELIBERATE. PyYAML is not in requirements.txt and
importing it would make every machine that runs this suite install a parser to check a
comment's worth of text. The matrix lines are written as single-line inline lists in
tests.yml precisely so a regex is sufficient and honest. The extractors below are
themselves tested against hand-written input, because a parser nobody tested would fail
open — it would find nothing, assert nothing, and pass.

NONE OF THESE TESTS GREPS ITS OWN SOURCE. The floor test's docstring already names why
that matters: a test that searches for a string its own file contains is checking itself
as well as its subject, and this repo has been bitten by it three times. Everything here
reads a DIFFERENT file, and the scan of the other workflows excludes tests.yml by name.

Run: python -m pipeline.tests.test_ci_workflow   (or pytest)
"""
import ast
import json
import os
import re

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

WORKFLOW_DIR = os.path.join(ROOT, ".github", "workflows")
CI_WORKFLOW_NAME = "tests.yml"
CI_WORKFLOW = os.path.join(WORKFLOW_DIR, CI_WORKFLOW_NAME)
FLOOR_TEST = os.path.join(HERE, "test_python_version_floor.py")
PACKAGE_JSON = os.path.join(ROOT, "frontend", "package.json")
DEV_REQUIREMENTS = os.path.join(ROOT, "pipeline", "requirements-dev.txt")


# --------------------------------------------------------------------------- #
# Extractors. Each returns a value or raises — none of them returns a benign    #
# empty result, because "found nothing" must read as a failure, not a pass.     #
# --------------------------------------------------------------------------- #
def yaml_code(text):
    """The YAML with its comments removed.

    WRITING ABOUT A NAME MUST NOT BREAK THE CHECK FOR THAT NAME. tests.yml explains in a
    comment that nothing in it is continue-on-error, and the first draft of the test below
    grepped the raw file and failed on its own explanation. That is the fourth time this
    repo has tripped over a literal appearing in prose — the #217 grep for
    "mop_spread.json", the selftest-wiring scan, the floor test's MIN_PYTHON count, and
    now this — so the check reads code, and the prose is free to say anything.

    Strips from an unquoted `#` to end of line. Quote tracking is not decorative: a step
    named "the #1 case" would otherwise truncate the line and could hide a real setting
    behind it.
    """
    out = []
    for line in text.splitlines():
        quote = None
        cut = len(line)
        for i, ch in enumerate(line):
            if quote:
                if ch == quote:
                    quote = None
            elif ch in "'\"":
                quote = ch
            elif ch == "#" and (i == 0 or line[i - 1] in " \t"):
                cut = i
                break
        out.append(line[:cut].rstrip())
    return "\n".join(out)


def inline_list(text, key):
    """The values of a single-line `key: ['a', 'b']` inline YAML list.

    Matches ONLY the bracketed form. That is what makes it safe to run over a file that
    also contains `python-version: ${{ matrix.python-version }}` two lines further down:
    the templated reference has no brackets and is invisible here.

    Raises on anything other than exactly one such line, so a second matrix added later
    is a failure rather than a silently ignored one.
    """
    hits = re.findall(r"^\s*%s:\s*\[([^\]]*)\]\s*$" % re.escape(key), text, re.M)
    if len(hits) != 1:
        raise AssertionError(
            f"expected exactly one inline `{key}: [...]` list, found {len(hits)}: {hits}")
    items = [v.strip().strip("'\"") for v in hits[0].split(",") if v.strip()]
    if not items:
        raise AssertionError(f"`{key}` list is empty")
    return items


def scalar_values(text, key):
    """Every `key: value` scalar in the text, in order, quotes stripped."""
    return [m.strip().strip("'\"") for m in
            re.findall(r"^\s*%s:\s*(\S+)\s*$" % re.escape(key), text, re.M)]


def ci_text():
    """tests.yml with its comments stripped — what every check below reads."""
    return yaml_code(open(CI_WORKFLOW, encoding="utf-8").read())


def _min_python_from(src):
    """MIN_PYTHON as declared in the given source text.

    SPLIT FROM THE FILE READ SO IT CAN BE EXERCISED ON A LITERAL. A reader that ignored
    its input and returned (3, 9) would satisfy every assertion about today's repo and
    quietly stop governing anything the moment the floor moved — the reader would report
    3.9 for ever, the drift check would compare 3.9 against a matrix containing 3.9, and
    the guard would pass while the thing it guards was broken. Handing it a hand-written
    (3, 12) is the only way to show it actually reads.
    """
    tree = ast.parse(src)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
                isinstance(t, ast.Name) and t.id == "MIN_PYTHON" for t in node.targets):
            return ast.literal_eval(node.value)
    raise AssertionError("MIN_PYTHON is not assigned at module level in the floor test")


def min_python():
    """MIN_PYTHON, read out of the floor test's SOURCE rather than imported.

    An import would bind this test to how the runner happens to load that module. The
    floor test's own docstring records the bug: loaded by dotted name and loaded by
    spec_from_file_location are two distinct module objects, and a patch to one is
    invisible to the other. Reading the assignment from the AST has no such dependence,
    and it is the same route test_the_floor_is_declared_in_exactly_one_place takes.
    """
    return _min_python_from(open(FLOOR_TEST, encoding="utf-8").read())


def workflow_python_versions(directory, exclude=CI_WORKFLOW_NAME):
    """{version: [filenames]} over the workflows in `directory`, skipping `exclude`.

    TAKES THE DIRECTORY SO ITS TWO FILTERS CAN BE EXERCISED. Both of them — skipping
    tests.yml, and ignoring a `${{ matrix.* }}` reference — are unreachable against
    today's files: tests.yml writes its versions as an inline list that scalar_values
    does not match, and the fleet uses no matrices at all. A mutation run put that
    plainly by deleting each filter and watching 1104 tests pass. Unreachable today is
    not the same as wrong, because either file could be reformatted tomorrow, so the
    scan is pointed at a constructed directory in the tests below rather than left as
    two branches nobody has ever run.
    """
    found = {}
    for fn in sorted(os.listdir(directory)):
        if not fn.endswith((".yml", ".yaml")) or fn == exclude:
            continue
        text = yaml_code(open(os.path.join(directory, fn), encoding="utf-8").read())
        for v in scalar_values(text, "python-version"):
            if v.startswith("${{"):        # a matrix reference, not a version
                continue
            found.setdefault(v, []).append(fn)
    return found


def other_workflow_python_versions():
    """The distinct `python-version:` scalars across every workflow EXCEPT tests.yml."""
    return workflow_python_versions(WORKFLOW_DIR)


def requirement_lines(path):
    """A requirements file's actual requirements — comments and blanks removed.

    `# -r requirements.txt` still contains "-r requirements.txt", so a check that greps
    the raw text cannot tell a live include from a commented-out one. That is the same
    prose-versus-code confusion yaml_code exists for, and a mutation run proved it here
    too: commenting the include out changed nothing that any test could see.
    """
    out = []
    for line in open(path, encoding="utf-8").read().splitlines():
        line = line.split("#", 1)[0].strip()
        if line:
            out.append(line)
    return out


def _major_of(spec):
    """The major version out of an npm range like "^22.9.0". Split out for the same
    reason as _min_python_from: a function that returned "22" whatever it was given
    would pass today and stop meaning anything tomorrow."""
    m = re.match(r"[^\d]*(\d+)", spec)
    if not m:
        raise AssertionError(f"cannot read a major version out of @types/node {spec!r}")
    return m.group(1)


def types_node_major():
    """The major version of the frontend's declared @types/node."""
    pkg = json.load(open(PACKAGE_JSON, encoding="utf-8"))
    return _major_of(pkg["devDependencies"]["@types/node"])


# --------------------------------------------------------------------------- #
# 1 — the extractors work. A parser that silently finds nothing would make      #
#     every test below vacuous, so it is exercised on hand-written input.       #
# --------------------------------------------------------------------------- #
def test_yaml_code_strips_comments_without_hiding_a_real_setting():
    """Both halves matter. Stripping too little makes prose break the checks; stripping
    too much makes them fail OPEN, which is the worse of the two because it is silent."""
    prose = "      # Nothing here is continue-on-error; a red test is a red check.\n"
    assert "continue-on-error" not in yaml_code(prose)

    real = prose + "      continue-on-error: true\n"
    assert "continue-on-error: true" in yaml_code(real), (
        "a genuine continue-on-error must survive the strip, or the guard is theatre")

    trailing = "      run: npm test   # not continue-on-error\n"
    assert yaml_code(trailing) == "      run: npm test"

    quoted = "      name: \"the # 1 case\"\n"
    assert yaml_code(quoted) == quoted.rstrip(), "a # inside quotes is not a comment"

    # A # that is NOT preceded by whitespace does not start a YAML comment either, and
    # this is the case the quote branch above does not reach — dropping the whitespace
    # rule left every other assertion here green.
    assert yaml_code("      run: echo a#b\n") == "      run: echo a#b"

    assert yaml_code("a: 1\nb: 2\n") == "a: 1\nb: 2"


def test_inline_list_reads_a_matrix_line():
    text = ("jobs:\n"
            "  python:\n"
            "    strategy:\n"
            "      matrix:\n"
            "        python-version: ['3.9', '3.12']\n"
            "    steps:\n"
            "      - with:\n"
            "          python-version: ${{ matrix.python-version }}\n")
    assert inline_list(text, "python-version") == ["3.9", "3.12"]


def test_inline_list_refuses_to_guess_when_there_is_not_exactly_one():
    """Zero matches and two matches are both failures, and for the same reason: the test
    would otherwise assert something about a line it did not find, or about whichever of
    two lines came first."""
    for text in ("python-version: '3.9'\n",                      # scalar, not a list
                 "",                                             # nothing at all
                 "  python-version: ['3.9']\n  python-version: ['3.12']\n"):
        try:
            inline_list(text, "python-version")
        except AssertionError:
            continue
        raise AssertionError(f"should have refused: {text!r}")


def test_inline_list_rejects_an_empty_list():
    try:
        inline_list("  python-version: []\n", "python-version")
    except AssertionError as e:
        assert "empty" in str(e)
    else:
        raise AssertionError("an empty matrix must not read as 'nothing to check'")


def test_scalar_values_skips_the_bracketed_form():
    """The two extractors must not both claim the same line, or the scan of the other
    workflows would count this workflow's matrix as a fleet version."""
    assert scalar_values("  python-version: '3.12'\n", "python-version") == ["3.12"]
    assert scalar_values("  python-version: ['3.9', '3.12']\n", "python-version") == []


def test_min_python_is_read_from_source_not_imported():
    """The value read by AST is the tuple the floor test declares."""
    assert min_python() == (3, 9)
    src = open(FLOOR_TEST, encoding="utf-8").read()
    assert "MIN_PYTHON = (3, 9)" in src


def test_the_floor_reader_reads_rather_than_remembers():
    """Handed a different floor, it must report the different floor. Without this, every
    drift check below could be satisfied by a reader hardcoded to today's answer."""
    assert _min_python_from("MIN_PYTHON = (3, 12)\n") == (3, 12)
    assert _min_python_from("X = 1\nMIN_PYTHON = (3, 10)\nY = 2\n") == (3, 10)
    try:
        _min_python_from("NOT_THE_FLOOR = (3, 9)\n")
    except AssertionError:
        pass
    else:
        raise AssertionError("a source with no MIN_PYTHON must raise, not invent one")


def test_requirement_lines_tells_a_live_include_from_a_commented_one():
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "r.txt")
        open(p, "w").write("# a note about -r requirements.txt\n"
                           "\n"
                           "-r requirements.txt\n"
                           "pytest>=8.0,<9   # pinned\n")
        assert requirement_lines(p) == ["-r requirements.txt", "pytest>=8.0,<9"]

        open(p, "w").write("# -r requirements.txt\npytest>=8.0,<9\n")
        assert requirement_lines(p) == ["pytest>=8.0,<9"], (
            "a commented-out include must not read as present")


def test_types_node_major_reads_a_caret_range():
    assert types_node_major() == "22"
    assert _major_of("^22.9.0") == "22"
    assert _major_of("^24.0.0") == "24", "the reader must follow its input, not remember"
    assert _major_of("20.1.2") == "20"


# --------------------------------------------------------------------------- #
# 2 — the workflow exists and actually runs the suite                           #
# --------------------------------------------------------------------------- #
def test_a_workflow_runs_the_suite_on_push_and_on_pull_request():
    """The gap that let the three breaks through was that NOTHING ran the tests. Pinned
    by trigger, because a workflow that only runs on a schedule would not have caught any
    of them either."""
    text = ci_text()
    trigger = text.split("\non:", 1)[1].split("\nenv:", 1)[0]
    assert re.search(r"^\s*push:\s*$", trigger, re.M), trigger
    assert re.search(r"^\s*pull_request:\s*$", trigger, re.M), trigger
    assert re.search(r"pytest", text), "the workflow must invoke pytest"


def test_nothing_in_the_workflow_swallows_a_failure():
    """A failing test must fail the check. continue-on-error turns a red suite into a
    green tick, which is worse than having no CI at all: it looks like proof."""
    text = ci_text()
    assert "continue-on-error" not in text
    assert "|| true" not in text
    # EVERY strategy block, not "does the string appear anywhere". Both jobs declare
    # fail-fast, so `"fail-fast: false" in text` stayed true with the python job flipped
    # to true — the frontend job's copy answered for it.
    flags = scalar_values(text, "fail-fast")
    assert len(flags) >= 2 and set(flags) == {"false"}, (
        f"both matrix legs must report; cancelling the sibling hides whether a break is "
        f"version-specific. found fail-fast: {flags}")


def test_the_suite_is_installed_from_the_declared_dev_requirements():
    """Not an inline package list. An inline list is a third declaration of the
    environment and drifts from requirements.txt the moment either moves."""
    text = ci_text()
    assert "pip install -r pipeline/requirements-dev.txt" in text
    lines = requirement_lines(DEV_REQUIREMENTS)
    assert "-r requirements.txt" in lines, (
        "the dev list must build ON the production list, and the include must be live "
        f"rather than commented out. found: {lines}")
    assert any(re.match(r"pytest[><=]", l) for l in lines), (
        f"pytest must be declared, and pinned. found: {lines}")


# --------------------------------------------------------------------------- #
# 3 — the drift checks proper                                                   #
# --------------------------------------------------------------------------- #
def test_the_workflow_tests_the_declared_floor():
    """THE ONE THIS FILE IS FOR. If MIN_PYTHON moves and tests.yml does not, CI goes on
    proving the old floor and the new one is untested everywhere."""
    floor = ".".join(str(v) for v in min_python())
    matrix = inline_list(ci_text(), "python-version")
    assert floor in matrix, (
        f"MIN_PYTHON is {floor} but tests.yml runs {matrix}. Raising the floor is one "
        f"edit to MIN_PYTHON and one to the matrix in .github/workflows/tests.yml; this "
        f"test exists so the second one cannot be forgotten.")


def test_the_floor_is_tested_first_so_a_failure_names_the_older_interpreter():
    """Ordering is not cosmetic here. GitHub labels the jobs in matrix order, and the
    floor is the leg that catches the breaks this workflow was written for, so it reads
    first in the check list rather than being scrolled to."""
    matrix = inline_list(ci_text(), "python-version")
    floor = ".".join(str(v) for v in min_python())
    assert matrix[0] == floor, matrix


def test_the_workflow_also_tests_what_the_other_workflows_install():
    """The fleet's version is the second thing this code has to work on. If the nine
    move to 3.13 and tests.yml stays on 3.12, the suite stops covering what production
    actually runs."""
    fleet = other_workflow_python_versions()
    assert fleet, "no python-version found in any other workflow — has the scan broken?"
    assert len(fleet) == 1, (
        f"the other workflows no longer agree on one Python: {fleet}. Pick one, or this "
        f"test cannot say which version CI should mirror.")
    fleet_version = next(iter(fleet))
    matrix = inline_list(ci_text(), "python-version")
    assert fleet_version in matrix, (
        f"the other workflows install Python {fleet_version}; tests.yml runs {matrix}")


def test_the_fleet_scan_skips_the_workflow_it_is_checking():
    """A constructed directory, because against the real one this filter is unreachable:
    tests.yml declares its versions as an inline list, which the scalar reader does not
    see. Reformat that line one day and, without this filter, tests.yml would vote on the
    fleet version it is supposed to be held against — it would always agree with itself."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "a-real-workflow.yml"), "w").write(
            "jobs:\n  x:\n    steps:\n      - with:\n          python-version: '3.12'\n")
        open(os.path.join(d, CI_WORKFLOW_NAME), "w").write(
            "jobs:\n  x:\n    steps:\n      - with:\n          python-version: '3.99'\n")
        open(os.path.join(d, "notes.md"), "w").write("python-version: '3.42'\n")

        got = workflow_python_versions(d)
        assert got == {"3.12": ["a-real-workflow.yml"]}, got
        # and with nothing excluded, the very version it must not count comes back
        assert "3.99" in workflow_python_versions(d, exclude=None)


def test_the_fleet_scan_does_not_count_a_matrix_reference_as_a_version():
    """`python-version: ${{matrix.python-version}}` names no version. Unreachable today
    only because the fleet uses no matrices; the day one does, counting the template as
    a version would make the fleet look like it disagreed with itself."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "matrixed.yml"), "w").write(
            "jobs:\n  x:\n    steps:\n      - with:\n"
            "          python-version: ${{matrix.python-version}}\n"
            "      - with:\n          python-version: '3.12'\n")
        got = workflow_python_versions(d)
        assert got == {"3.12": ["matrixed.yml"]}, got


def test_the_scan_of_the_other_workflows_really_sees_them():
    """Guards the guard. If the directory listing or the regex breaks, the test above
    would find an empty fleet and pass on a technicality."""
    fleet = other_workflow_python_versions()
    files = {fn for names in fleet.values() for fn in names}
    assert len(files) >= 8, sorted(files)
    assert CI_WORKFLOW_NAME not in files, "tests.yml must not count as its own fleet"


def test_the_runner_image_is_pinned_while_the_floor_is_below_the_fleet():
    """ubuntu-latest WILL break the floor leg, silently and on someone else's schedule.

    actions/python-versions publishes per-image builds. 3.9's newest build targets ubuntu
    22.04 and 24.04; 24.04's successor already has 3.12 builds and no 3.9 ones. So the day
    ubuntu-latest moves up, `python-version: '3.9'` stops resolving and the job fails
    looking exactly like a code break — the 'why does CI suddenly hate me' failure.

    The condition is deliberately tied to the floor rather than written as a flat rule:
    once MIN_PYTHON reaches the fleet version there is no old interpreter to strand, and
    this stops demanding a pin.
    """
    text = ci_text()
    images = scalar_values(text, "runs-on")
    assert images, "no runs-on found"
    fleet = next(iter(other_workflow_python_versions()))
    floor = ".".join(str(v) for v in min_python())
    if floor != fleet:
        assert not any(i.endswith("-latest") for i in images), (
            f"floor {floor} is older than the fleet's {fleet}, so the images must be "
            f"pinned; found {images}")
    assert len(set(images)) == 1, (
        f"both jobs should run on one image so their results are comparable: {images}")


def test_the_frontend_is_tested_on_the_node_it_is_typed_against():
    """tsc checks this code against @types/node. Running the suites on a different major
    is the same gap as testing on 3.11 and shipping to 3.9, one language over."""
    matrix = inline_list(ci_text(), "node-version")
    assert len(matrix) == 1, matrix
    assert matrix[0] == types_node_major(), (
        f"tests.yml runs Node {matrix[0]}; frontend/package.json declares "
        f"@types/node ^{types_node_major()}.x")


def test_the_frontend_job_installs_typechecks_and_tests():
    """All three, and from the lockfile. npm install would resolve something the lockfile
    does not describe, which is how a green CI stops describing the deployed build."""
    text = ci_text()
    for command in ("npm ci", "npm run typecheck", "npm test"):
        assert re.search(r"run:\s*%s\s*$" % re.escape(command), text, re.M), command


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"{len(fns)} CI-workflow checks passed")
