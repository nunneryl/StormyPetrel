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

SECTION 5 holds the fleet to one failure issue per scheduled workflow: every scheduled
workflow reports through the shared .github/actions/failure-issue step, nothing else in the
repository opens an issue except two named alert managers, and no job can write issues
unless it does. The scan for issue-openers skips pipeline/tests for the same reason as above:
the tests hold those patterns as data.

Run: python -m pipeline.tests.test_ci_workflow   (or pytest)
"""
import ast
import io
import json
import os
import re
import tokenize

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


FLOATING_LABEL = "ubuntu-latest"


def workflow_files(directory):
    """The files GitHub runs from `directory`: every .yml and .yaml, nothing else."""
    return sorted(fn for fn in os.listdir(directory) if fn.endswith((".yml", ".yaml")))


def floating_label_lines(directory):
    """[(filename, line number, code)] for every line of workflow CODE naming ubuntu-latest.

    EVERY LINE, NOT JUST runs-on. A job can reach the label without its runs-on line ever
    spelling it: `runs-on: ${{ matrix.os }}` with `os: [ubuntu-latest]`, or an expression
    default like `${{ inputs.runner || 'ubuntu-latest' }}`. A check that read runs-on
    values would pass both. Comments are stripped first, because tests.yml explains in
    prose why it is not on ubuntu-latest, and that explanation must not trip the check it
    motivates. Case-insensitive, so `Ubuntu-Latest` cannot slip past either.

    Includes tests.yml. The fleet scan above skips it so it cannot vote on its own
    matrix; there is nothing to vote on here, and it floats like any other workflow.
    """
    hits = []
    for fn in workflow_files(directory):
        code = yaml_code(open(os.path.join(directory, fn), encoding="utf-8").read())
        for n, line in enumerate(code.splitlines(), 1):
            if FLOATING_LABEL in line.lower():
                hits.append((fn, n, line.strip()))
    return hits


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


# --------------------------------------------------------------------------- #
# 4 — no workflow floats on ubuntu-latest                                       #
# --------------------------------------------------------------------------- #
def test_no_workflow_floats_on_ubuntu_latest():
    """GitHub moves ubuntu-latest to Ubuntu 26.04 over the weeks from 2026-10-19
    (actions/runner-images#14748). The forecast pipeline installs libeccodes0 from apt and
    a geospatial stack from pip wheels on whatever image it lands on, so a floating label
    would swap the platform under the forecasts on GitHub's schedule, a run at a time,
    with nothing in the diff to say so. Pinned, the move happens when someone makes it,
    in one PR that changes every runs-on and has been tried on the new image first.

    This is the fleet-wide rule. The floor test above is the reason tests.yml in
    particular cannot move until MIN_PYTHON does; this one holds whatever the floor."""
    hits = floating_label_lines(WORKFLOW_DIR)
    assert not hits, (
        "ubuntu-latest moves to a new Ubuntu release on GitHub's schedule. Pin ubuntu-24.04, "
        "or move every workflow to a newer image deliberately:\n"
        + "\n".join(f"  .github/workflows/{fn}:{n}: {code}" for fn, n, code in hits))


def test_the_floating_label_scan_catches_every_way_a_job_can_name_it():
    """Line numbers are written out by hand from the fixtures, so the scan is held to
    where the label really is rather than to whatever it happens to report."""
    import tempfile

    fixtures = {
        "scalar.yml": "jobs:\n  a:\n    runs-on: ubuntu-latest\n",
        "quoted.yml": "jobs:\n  a:\n    runs-on: 'ubuntu-latest'\n",
        "inline-list.yml": "jobs:\n  a:\n    runs-on: [self-hosted, ubuntu-latest]\n",
        "block-list.yml": "jobs:\n  a:\n    runs-on:\n      - ubuntu-latest\n",
        "matrix.yml": ("jobs:\n  a:\n    strategy:\n      matrix:\n        os: [ubuntu-latest]\n"
                       "    runs-on: ${{ matrix.os }}\n"),
        "expression.yml": "jobs:\n  a:\n    runs-on: ${{ inputs.runner || 'ubuntu-latest' }}\n",
        "other-case.yaml": "jobs:\n  a:\n    runs-on: Ubuntu-Latest\n",
    }
    with tempfile.TemporaryDirectory() as d:
        for fn, text in fixtures.items():
            open(os.path.join(d, fn), "w").write(text)
        got = {(fn, n) for fn, n, _ in floating_label_lines(d)}
    assert got == {("scalar.yml", 3), ("quoted.yml", 3), ("inline-list.yml", 3),
                   ("block-list.yml", 4), ("matrix.yml", 5), ("expression.yml", 3),
                   ("other-case.yaml", 3)}, sorted(got)


def test_the_floating_label_scan_ignores_prose_and_files_github_does_not_run():
    """The other half. Failing on prose would make the explanation in tests.yml impossible
    to write, and docs/archive keeps an old workflow as .yml.txt that GitHub never runs."""
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        open(os.path.join(d, "pinned.yml"), "w").write(
            "# Not ubuntu-latest: that label moves on GitHub's schedule.\n"
            "jobs:\n"
            "  a:\n"
            "    runs-on: ubuntu-24.04   # was ubuntu-latest\n")
        open(os.path.join(d, "notes.md"), "w").write("runs-on: ubuntu-latest\n")
        open(os.path.join(d, "prototype.yml.txt"), "w").write("runs-on: ubuntu-latest\n")
        assert floating_label_lines(d) == []


def test_the_floating_label_scan_sees_every_job_in_the_real_workflows():
    """Guards the guard, on the real files rather than toy ones. A copy of the workflow
    directory has every runs-on line flipped to ubuntu-latest, and the scan must report
    exactly those lines. That fails if the listing misses a file, if the comment stripper
    eats a real line, or if the reader stops reading, and each of those would otherwise
    leave the check above passing on an empty result."""
    import tempfile

    flipped = set()
    with tempfile.TemporaryDirectory() as d:
        for fn in os.listdir(WORKFLOW_DIR):
            path = os.path.join(WORKFLOW_DIR, fn)
            if not os.path.isfile(path):
                continue
            lines = open(path, encoding="utf-8").read().split("\n")
            if fn.endswith((".yml", ".yaml")):
                for i, line in enumerate(lines):
                    m = re.match(r"(\s*runs-on:\s*)\S", line)
                    if m:
                        lines[i] = m.group(1) + "ubuntu-latest"
                        flipped.add((fn, i + 1))
            open(os.path.join(d, fn), "w", encoding="utf-8").write("\n".join(lines))
        got = {(fn, n) for fn, n, _ in floating_label_lines(d)}
    files = {fn for fn, _ in flipped}
    assert len(files) >= 8 and CI_WORKFLOW_NAME in files, (
        f"the runs-on lines were found in only {sorted(files)} — has the regex broken?")
    assert got == flipped, {"missed": sorted(flipped - got), "extra": sorted(got - flipped)}


# --------------------------------------------------------------------------- #
# 5 — one failure issue per scheduled workflow                                  #
# --------------------------------------------------------------------------- #
# forecast-pipeline used to open a new issue for every failed run, and 54 piled up. The
# shared step replaced it in every scheduled workflow; these hold the fleet to it.
SHARED_STEP = "./.github/actions/failure-issue"
SHARED_ACTION = os.path.join(ROOT, ".github", "actions", "failure-issue", "action.yml")
SHARED_SCRIPT = ".github/actions/failure-issue/failure_issue.py"
REPORTER_PERMISSIONS = {"actions": "read", "contents": "read", "issues": "write"}
REPORTER_CONCURRENCY = {"group": "failure-issue-${{ github.workflow }}",
                        "cancel-in-progress": "false"}

# EVERYTHING ALLOWED TO OPEN AN ISSUE, and why. The shared step is the only way a failure
# becomes an issue. The two workflow steps are alert managers, not failure reports: each keeps
# its own issues under its own label, looks for an open one before opening another, and holds
# issues: write in its own job only. Adding a fourth means adding it here, on purpose.
ISSUE_OPENERS = {
    (SHARED_SCRIPT, None, None),
    (".github/workflows/buoy-ready-monitor.yml", "buoy-liveness",
     "Manage 'buoy-liveness' issues (per buoy — detect & report only)"),
    (".github/workflows/reverify-trust-accumulate.yml", "reverify",
     "Open/update issue for SETTLED zones (manual tagging — never auto-applied)"),
}
ALERT_LABELS = {"buoy-liveness", "nwps-trust-settled"}

# How code opens an issue: the octokit call github-script uses, the GitHub CLI, the GraphQL
# mutation and PyGithub's method, and the REST path itself. A comment naming one is prose.
ISSUE_WRITE = re.compile(
    r"\bissues\s*\.\s*create\s*\("
    r"|\bgh\s+issue\s+(?:create|new)\b"
    r"|\bcreate_?issue\b"
    r"|/issues(?![\w/-])",
    re.I | re.M)
SCAN_EXTENSIONS = (".py", ".sh", ".js", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".yml", ".yaml")
SCAN_SKIP_DIRS = {".git", "node_modules", ".next", "__pycache__", ".venv", "venv",
                  ".mypy_cache", ".pytest_cache", "dist", "build"}


def top_level(code, key):
    """(inline value, indented block) of a column-0 `key:`, or (None, None) without one. The
    block runs to the next column-0 line, so a stripped comment's blank line stays inside."""
    m = re.search(r"^%s:[ \t]*(.*)$" % re.escape(key), code, re.M)
    if not m:
        return None, None
    rest = code[m.end():]
    end = re.search(r"^\S", rest, re.M)
    return (m.group(1).strip() or None), (rest[:end.start()] if end else rest)


def unquote(value):
    """A scalar without the one pair of quotes YAML allows around it: 'false' is false."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
        return value[1:-1]
    return value


def keys_at(block, indent):
    """[(key, value)] of the `key: value` lines indented exactly `indent` spaces."""
    return [(k, unquote(v)) for k, v in
            re.findall(r"^ {%d}([\w-]+):[ \t]*(.*?)[ \t]*$" % indent, block or "", re.M)]


def triggers(code):
    """The events under `on:`, in order, in any of the three ways YAML can write them."""
    inline, block = top_level(code, "on")
    if inline:
        return [t.strip() for t in inline.strip("[]").split(",") if t.strip()]
    return [k for k, _ in keys_at(block, 2)]


def jobs_of(code):
    """{job id: the job's text}, in file order."""
    _inline, block = top_level(code, "jobs")
    if block is None:
        raise AssertionError("no jobs: block")
    parts = re.split(r"^  ([\w-]+):[ \t]*$", block, flags=re.M)
    return dict(zip(parts[1::2], parts[2::2]))


def job_key(job, key):
    """(inline value, block) of one of a job's own keys, or (None, None) without it."""
    m = re.search(r"^    %s:[ \t]*(.*)$" % re.escape(key), job, re.M)
    if not m:
        return None, None
    rest = job[m.end():]
    end = re.search(r"^ {0,4}\S", rest, re.M)
    return (unquote(m.group(1).strip()) or None), (rest[:end.start()] if end else rest)


def permission_map(inline, block, indent):
    """{scope: level} of a permissions key, or None when it is not declared at all. The
    shorthands expand, so `write-all` reads as the write it is, not as no permissions."""
    if inline is None and block is None:
        return None
    if inline is None:
        return dict(keys_at(block, indent))
    if inline in ("read-all", "write-all"):
        return {"*": inline[:-4]}
    if inline == "{}":
        return {}
    raise AssertionError(f"unrecognised permissions: {inline!r}")


def can_write_issues(perms):
    return bool(perms) and "write" in (perms.get("issues"), perms.get("*"))


def needs_of(job):
    """A job's needs, written inline or as a block list."""
    inline, block = job_key(job, "needs")
    if inline:
        return [n.strip() for n in inline.strip("[]").split(",") if n.strip()]
    return re.findall(r"^ {6}- ([\w-]+)[ \t]*$", block or "", re.M)


def steps_of(job):
    """[(the step's single-line keys, the step's text)], in order."""
    _inline, block = job_key(job, "steps")
    return [({k: unquote(v) for k, v in
              re.findall(r"^(?: {8})?([\w-]+):[ \t]*(.*?)[ \t]*$", chunk, re.M)}, chunk)
            for chunk in re.split(r"^      - ", block or "", flags=re.M)[1:]]


def step_with(chunk):
    """A step's `with:` inputs."""
    m = re.search(r"^ {8}with:[ \t]*$", chunk, re.M)
    if not m:
        return {}
    rest = chunk[m.end():]
    end = re.search(r"^ {0,8}\S", rest, re.M)
    return dict(keys_at(rest[:end.start()] if end else rest, 10))


def workflow_code(directory, fn):
    return yaml_code(open(os.path.join(directory, fn), encoding="utf-8").read())


def scheduled_workflows(directory):
    return [fn for fn in workflow_files(directory)
            if "schedule" in triggers(workflow_code(directory, fn))]


def is_reporter(job):
    return any(keys.get("uses") == SHARED_STEP for keys, _ in steps_of(job))


def failure_issue_problems(directory):
    """Every way a scheduled workflow in `directory` falls short of reporting its failures
    through the shared step, one line each. Empty means every one of them is wired."""
    problems = []
    for fn in scheduled_workflows(directory):
        code = workflow_code(directory, fn)
        name = top_level(code, "name")[0] or ""
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) or name in ALERT_LABELS:
            problems.append(f"{fn}: name {name!r} cannot be its failure label")
        jobs = jobs_of(code)
        reporters = [j for j, text in jobs.items() if is_reporter(text)]
        if len(reporters) != 1:
            problems.append(f"{fn}: {len(reporters)} jobs use {SHARED_STEP}, not one")
            continue
        r = reporters[0]
        job = jobs[r]
        others = sorted(j for j in jobs if j != r)
        if sorted(needs_of(job)) != others:
            problems.append(f"{fn}: {r} needs {needs_of(job)}, not every other job {others}")
        if job_key(job, "if")[0] != "always()":
            problems.append(f"{fn}: {r} runs if {job_key(job, 'if')[0]!r}, not always(), so a "
                            "failure or a timeout would skip it")
        perms = permission_map(*job_key(job, "permissions"), 6)
        if perms != REPORTER_PERMISSIONS:
            problems.append(f"{fn}: {r} permissions {perms}, not {REPORTER_PERMISSIONS}")
        concurrency = dict(keys_at(job_key(job, "concurrency")[1], 6))
        if concurrency != REPORTER_CONCURRENCY:
            problems.append(f"{fn}: {r} concurrency {concurrency}, not {REPORTER_CONCURRENCY}")
        if not (job_key(job, "timeout-minutes")[0] or "").isdigit():
            problems.append(f"{fn}: {r} has no timeout-minutes")
        steps = steps_of(job)
        uses = [keys.get("uses") for keys, _ in steps]
        if len(uses) != 2 or not (uses[0] or "").startswith("actions/checkout@") \
                or uses[1] != SHARED_STEP:
            problems.append(f"{fn}: {r} steps use {uses}, not a checkout then {SHARED_STEP}")
            continue
        if step_with(steps[0][1]) != {"sparse-checkout": ".github/actions/failure-issue",
                                      "persist-credentials": "false"}:
            problems.append(f"{fn}: {r} checkout with {step_with(steps[0][1])}")
        if step_with(steps[1][1]) != {"needs": "${{ toJSON(needs) }}"}:
            problems.append(f"{fn}: {r} hands the step {step_with(steps[1][1])}, "
                            "not needs: ${{ toJSON(needs) }}")
    return problems


def permission_problems(directory):
    """Every job in `directory` that could write issues without having a step that does, and
    every job whose permissions are not stated anywhere: that job holds whatever the
    repository's default grants, which can include issues: write."""
    problems = []
    for fn in workflow_files(directory):
        code = workflow_code(directory, fn)
        top = permission_map(*top_level(code, "permissions"), 2)
        if can_write_issues(top):
            problems.append(f"{fn}: issues: write at the top level reaches every job")
        for j, text in jobs_of(code).items():
            own = permission_map(*job_key(text, "permissions"), 6)
            perms = own if own is not None else top
            if perms is None:
                problems.append(f"{fn}: {j} states no permissions, so it gets the "
                                "repository's default token")
                continue
            if not can_write_issues(perms) or is_reporter(text):
                continue
            if not any((f".github/workflows/{fn}", j, keys.get("name")) in ISSUE_OPENERS
                       for keys, _ in steps_of(text)):
                problems.append(f"{fn}: {j} can write issues and no step of it does")
    return problems


def python_code(src):
    """Python source with its comments blanked. Strings stay: a URL in one is code."""
    lines = src.splitlines(True)
    try:
        for tok in tokenize.generate_tokens(io.StringIO(src).readline):
            if tok.type == tokenize.COMMENT:
                row, col = tok.start
                lines[row - 1] = lines[row - 1][:col] + "\n"
    except (tokenize.TokenError, SyntaxError):
        pass
    return "".join(lines)


def _issue_action(uses):
    """A third-party action whose name says it handles issues. The shared step is not one."""
    return bool(uses) and "issue" in uses.lower() and uses != SHARED_STEP


def issue_openers(root):
    """{(path, job, step)} for every place under `root` that can open an issue: a workflow
    step as (path, job id, step name), anything else as (path, None, None)."""
    found = set()
    for dirpath, dirnames, filenames in os.walk(root):
        rel_dir = os.path.relpath(dirpath, root)
        dirnames[:] = sorted(
            d for d in dirnames if d not in SCAN_SKIP_DIRS and
            os.path.normpath(os.path.join(rel_dir, d)).replace(os.sep, "/") != "pipeline/tests")
        for name in sorted(filenames):
            if not name.endswith(SCAN_EXTENSIONS):
                continue
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            src = open(path, encoding="utf-8", errors="replace").read()
            if rel.startswith(".github/workflows/"):
                for j, text in jobs_of(yaml_code(src)).items():
                    for keys, chunk in steps_of(text):
                        if ISSUE_WRITE.search(chunk) or _issue_action(keys.get("uses")):
                            found.add((rel, j, keys.get("name")))
                continue
            code = (python_code(src) if name.endswith(".py")
                    else yaml_code(src) if name.endswith((".yml", ".yaml", ".sh")) else src)
            uses = re.findall(r"^\s*-?\s*uses:\s*['\"]?([^'\"\s]+)", code, re.M)
            if ISSUE_WRITE.search(code) or any(_issue_action(u) for u in uses):
                found.add((rel, None, None))
    return found


def test_the_workflow_readers_read_hand_written_yaml():
    code = yaml_code(
        "name: demo\n"
        "on:\n"
        "  schedule:\n"
        "    - cron: '0 * * * *'   # hourly\n"
        "  workflow_dispatch: {}\n"
        "permissions:\n"
        "  contents: read\n"
        "jobs:\n"
        "  work:\n"
        "    runs-on: ubuntu-24.04\n"
        "    permissions:\n"
        "      # the alert step below writes issues\n"
        "      contents: read\n"
        "      issues: write   # a comment\n"
        "    steps:\n"
        "      - name: One\n"
        "        run: echo 1\n"
        "      - name: Two\n"
        "        uses: 'x/y@v1'\n"
        "        with:\n"
        "          a: \"b\"\n"
        "          c: ${{ d }}\n"
        "        env:\n"
        "          E: f\n"
        "  report:\n"
        "    needs: [work, other]\n"
        "    if: always()\n"
        "    concurrency:\n"
        "      group: g\n"
        "      cancel-in-progress: false\n"
        "    steps:\n"
        "      - uses: ./z\n"
        "  other:\n"
        "    needs:\n"
        "      - work\n"
        "    permissions: write-all\n")
    assert triggers(code) == ["schedule", "workflow_dispatch"]
    assert top_level(code, "name") == ("demo", "\n")
    assert top_level(code, "concurrency") == (None, None)
    jobs = jobs_of(code)
    assert list(jobs) == ["work", "report", "other"]
    assert permission_map(*top_level(code, "permissions"), 2) == {"contents": "read"}
    assert permission_map(*job_key(jobs["work"], "permissions"), 6) == {
        "contents": "read", "issues": "write"}
    assert permission_map(*job_key(jobs["report"], "permissions"), 6) is None
    assert permission_map(*job_key(jobs["other"], "permissions"), 6) == {"*": "write"}
    assert permission_map("read-all", None, 2) == {"*": "read"}
    assert permission_map("{}", None, 2) == {}
    assert needs_of(jobs["report"]) == ["work", "other"]
    assert needs_of(jobs["other"]) == ["work"]
    assert needs_of(jobs["work"]) == []
    assert job_key(jobs["report"], "if") == ("always()", "\n")
    assert dict(keys_at(job_key(jobs["report"], "concurrency")[1], 6)) == {
        "group": "g", "cancel-in-progress": "false"}
    steps = steps_of(jobs["work"])
    assert [keys for keys, _ in steps] == [
        {"name": "One", "run": "echo 1"},
        {"name": "Two", "uses": "x/y@v1", "with": "", "env": ""}]
    assert step_with(steps[1][1]) == {"a": "b", "c": "${{ d }}"}
    assert step_with(steps[0][1]) == {}
    assert [keys for keys, _ in steps_of(jobs["report"])] == [{"uses": "./z"}]
    assert steps_of(jobs["other"]) == []
    assert triggers(yaml_code("on: [push, pull_request]\njobs:\n  a:\n")) == ["push", "pull_request"]
    assert triggers(yaml_code("on: push\njobs:\n  a:\n")) == ["push"]


def test_keys_at_removes_one_pair_of_quotes_and_only_a_matching_pair():
    """'false' and false are the same YAML scalar, so a quoted value must not read as a
    different setting; a lone or mismatched quote is part of the value."""
    block = "  a: 'x'\n  b: \"y\"\n  c: 'z\n  d: 'e\"\n  f: ''\n    g: deeper\n"
    assert keys_at(block, 2) == [("a", "x"), ("b", "y"), ("c", "'z"), ("d", "'e\""), ("f", "")]


def test_permission_map_refuses_a_form_it_cannot_read():
    """An unreadable permissions value must not read as 'grants nothing'."""
    try:
        permission_map("${{ fromJSON(x) }}", None, 6)
    except AssertionError:
        pass
    else:
        raise AssertionError("an unrecognised permissions value must raise")


def test_python_code_blanks_comments_and_keeps_strings():
    src = ('x = "/repos/o/r/issues"   # gh issue create\n'
           '# repo.create_issue(\n'
           'y = 1\n')
    assert python_code(src) == 'x = "/repos/o/r/issues"   \n\ny = 1\n'


def test_every_scheduled_workflow_reports_failures_through_the_shared_step():
    """One job per scheduled workflow, after every other job, whatever their result, with
    only the permissions it needs, one at a time per workflow, running the shared step and
    nothing else. A workflow added on a schedule without it fails here."""
    problems = failure_issue_problems(WORKFLOW_DIR)
    assert not problems, "\n".join(problems)


def test_the_scheduled_workflows_are_all_seen():
    """Guards the guard: a broken trigger reader would find no scheduled workflow, and the
    test above would pass on an empty fleet."""
    scheduled = scheduled_workflows(WORKFLOW_DIR)
    assert len(scheduled) >= 8, scheduled
    assert "forecast-pipeline.yml" in scheduled and CI_WORKFLOW_NAME not in scheduled


def test_nothing_else_opens_an_issue():
    """No step anywhere opens an issue except the shared one and the two named alert
    managers. The old per-run step in forecast-pipeline would fail this, and so would a new
    github-script, gh, GraphQL, PyGithub or REST call, or an issue-creating action."""
    found = issue_openers(ROOT)
    assert found == ISSUE_OPENERS, {"unexpected": sorted(found - ISSUE_OPENERS, key=str),
                                    "missing": sorted(ISSUE_OPENERS - found, key=str)}


def test_the_alert_managers_look_for_an_open_issue_before_opening_one():
    """What earns them their place in ISSUE_OPENERS: each lists the open issues under its
    own label first, and opens one only when none is there to update."""
    for path, job, step in sorted(ISSUE_OPENERS - {(SHARED_SCRIPT, None, None)}):
        code = yaml_code(open(os.path.join(ROOT, path), encoding="utf-8").read())
        chunk = {keys.get("name"): c for keys, c in steps_of(jobs_of(code)[job])}[step]
        assert re.search(r"listForRepo\(\{\s*owner, repo, state: 'open', labels: LABEL", chunk), path
        assert re.search(r"const LABEL = '([\w-]+)'", chunk).group(1) in ALERT_LABELS, path


def test_issues_write_is_held_only_by_jobs_that_write_issues():
    """Least privilege, fleet-wide: no top-level issues: write, no job without stated
    permissions, and issues: write only in a failure-issue job or an alert manager's job.
    The jobs that run the pipeline hold secrets and run third-party code; they cannot touch
    an issue."""
    problems = permission_problems(WORKFLOW_DIR)
    assert not problems, "\n".join(problems)


def test_the_shared_action_hands_the_script_its_input_through_the_environment():
    """No expression is spliced into the command, so no job output or name can become source;
    and the token is the job's own, so nothing in a workflow names a secret for it."""
    text = yaml_code(open(SHARED_ACTION, encoding="utf-8").read())
    assert re.search(r"^  using: composite$", text, re.M)
    runs = re.findall(r"^ +run: (.+)$", text, re.M)
    assert runs == ['python3 "$GITHUB_ACTION_PATH/failure_issue.py"'], runs
    env = dict(re.findall(r"^ {8}([A-Z_]+): (.+)$", text, re.M))
    assert env == {"FAILURE_ISSUE_NEEDS": "${{ inputs.needs }}",
                   "GITHUB_TOKEN": "${{ inputs.token }}"}, env
    assert re.search(r"^    default: \$\{\{ github\.token \}\}$", text, re.M)
    assert os.path.isfile(os.path.join(ROOT, SHARED_SCRIPT))


def test_the_shared_action_holds_no_expression_the_runner_cannot_evaluate():
    """The runner evaluates `${{ }}` everywhere in action.yml, input descriptions included,
    and inside an action only github, inputs and the like exist. The first push of this
    action described its input as `${{ toJSON(needs) }}`, and every failure-issue job died
    loading the manifest with "Unrecognized named-value: 'needs'"; actionlint and a YAML
    parser both passed it. So the expressions are listed, and only these three may appear."""
    text = yaml_code(open(SHARED_ACTION, encoding="utf-8").read())
    found = re.findall(r"\$\{\{\s*(.*?)\s*\}\}", text)
    assert sorted(found) == ["github.token", "inputs.needs", "inputs.token"], found


def _copy_workflows(d):
    for fn in workflow_files(WORKFLOW_DIR):
        open(os.path.join(d, fn), "w", encoding="utf-8").write(
            open(os.path.join(WORKFLOW_DIR, fn), encoding="utf-8").read())


def _edit(d, fn, old, new):
    path = os.path.join(d, fn)
    text = open(path, encoding="utf-8").read()
    assert text.count(old) == 1, (fn, old)
    open(path, "w", encoding="utf-8").write(text.replace(old, new))


def test_the_wiring_check_catches_each_way_a_real_workflow_can_drift():
    """Guards the guard, on copies of the real files: each break is made once, in one
    workflow, and the check must name that workflow and that fault, and nothing else."""
    import tempfile

    cut_job = ("daily-report.yml", None, None, "daily-report.yml: 0 jobs use")
    cases = [
        cut_job,
        ("forecast-pipeline.yml", "needs: [full-pipeline, buoy-update]",
         "needs: [full-pipeline]", "forecast-pipeline.yml: failure-issue needs ['full-pipeline']"),
        ("resolve-cams.yml", "    if: always()\n", "    if: failure()\n",
         "resolve-cams.yml: failure-issue runs if 'failure()'"),
        ("ecmwf-wam.yml",
         "      issues: write               # open, comment on and close the failure issue\n",
         "", "ecmwf-wam.yml: failure-issue permissions"),
        ("archive-partitions.yml", "group: failure-issue-${{ github.workflow }}",
         "group: ${{ github.workflow }}", "archive-partitions.yml: failure-issue concurrency"),
        ("nwps-publication-log.yml", "needs: ${{ toJSON(needs) }}", "needs: '{}'",
         "nwps-publication-log.yml: failure-issue hands the step"),
        ("buoy-ready-monitor.yml", "          persist-credentials: false\n", "",
         "buoy-ready-monitor.yml: failure-issue checkout with"),
        ("reverify-trust-accumulate.yml", "    timeout-minutes: 5\n", "",
         "reverify-trust-accumulate.yml: failure-issue has no timeout-minutes"),
    ]
    for fn, old, new, expected in cases:
        with tempfile.TemporaryDirectory() as d:
            _copy_workflows(d)
            if old is None:
                path = os.path.join(d, fn)
                text = open(path, encoding="utf-8").read()
                open(path, "w", encoding="utf-8").write(text.split("\n  failure-issue:\n")[0])
            else:
                _edit(d, fn, old, new)
            problems = failure_issue_problems(d)
        assert len(problems) == 1 and problems[0].startswith(expected), (fn, problems)


def test_the_permission_check_catches_each_way_a_real_job_can_overreach():
    """The same, for least privilege: issues: write handed to a job that writes none, to the
    whole workflow, or left to the repository's default by stating nothing."""
    import tempfile

    cases = [
        ("forecast-pipeline.yml", "    permissions:\n      contents: read\n    # The column",
         "    permissions:\n      contents: read\n      issues: write\n    # The column",
         "forecast-pipeline.yml: full-pipeline can write issues and no step of it does"),
        ("forecast-pipeline.yml", "permissions:\n  contents: read\n\njobs:",
         "jobs:", "forecast-pipeline.yml: buoy-update states no permissions"),
        ("buoy-ready-monitor.yml", "permissions:\n  contents: read ",
         "permissions:\n  issues: write\n  contents: read ",
         "buoy-ready-monitor.yml: issues: write at the top level"),
        (CI_WORKFLOW_NAME, "permissions:\n  contents: read ", "permissions: write-all\n  #",
         f"{CI_WORKFLOW_NAME}: issues: write at the top level"),
    ]
    for fn, old, new, expected in cases:
        with tempfile.TemporaryDirectory() as d:
            _copy_workflows(d)
            _edit(d, fn, old, new)
            problems = permission_problems(d)
        assert problems and all(p.startswith(fn) for p in problems), (fn, problems)
        assert any(p.startswith(expected) for p in problems), (fn, problems)


def test_the_issue_scan_finds_every_way_to_open_one_and_no_prose():
    """Constructed files, with the hits written out by hand."""
    import tempfile

    files = {
        ".github/workflows/x.yml": (
            "on: push\n"
            "jobs:\n"
            "  a:\n"
            "    steps:\n"
            "      - name: script\n"
            "        uses: actions/github-script@v9\n"
            "        with:\n"
            "          script: await github.rest.issues.create({owner, repo, title: 't'})\n"
            "      - name: cli\n"
            "        run: gh issue create --title t\n"
            "      - name: action\n"
            "        uses: someone/create-an-issue@v2\n"
            "      - name: rest\n"
            "        run: curl -X POST https://api.github.com/repos/o/r/issues -d @b.json\n"
            "      - name: prose\n"
            "        # never calls github.rest.issues.create() or gh issue create\n"
            "        run: echo fine\n"
            "      - name: shared\n"
            "        uses: ./.github/actions/failure-issue\n"),
        "scripts/a.py": 'requests.post(f"{API}/repos/{REPO}/issues", json=body)\n',
        "scripts/b.py": "# gh issue create, issues.create(, repo.create_issue(\nx = 1\n",
        "scripts/c.sh": "gh  issue  new --title t\n",
        "frontend/lib/d.ts": "await octokit.rest.issues.create({ owner, repo, title })\n",
        "frontend/lib/e.ts": "fetch(`${api}/repos/${repo}/issues/${n}/comments`)\n",
        "pipeline/f.py": "g.get_repo(r).create_issue(title='t')\n",
        "pipeline/tests/test_g.py": "github.rest.issues.create(\n",
        "frontend/node_modules/h.js": "issues.create(\n",
        "docs/i.md": "gh issue create\n",
    }
    with tempfile.TemporaryDirectory() as d:
        for rel, text in files.items():
            os.makedirs(os.path.dirname(os.path.join(d, rel)), exist_ok=True)
            open(os.path.join(d, rel), "w", encoding="utf-8").write(text)
        got = issue_openers(d)
    assert got == {(".github/workflows/x.yml", "a", "script"),
                   (".github/workflows/x.yml", "a", "cli"),
                   (".github/workflows/x.yml", "a", "action"),
                   (".github/workflows/x.yml", "a", "rest"),
                   ("scripts/a.py", None, None),
                   ("scripts/c.sh", None, None),
                   ("frontend/lib/d.ts", None, None),
                   ("pipeline/f.py", None, None)}, sorted(got, key=str)


def test_the_issue_scan_sees_a_step_added_to_a_real_workflow():
    """Guards the guard on the real files: the old per-run step, put back into a copy of
    forecast-pipeline.yml, is found, by job and step."""
    import shutil
    import tempfile

    with tempfile.TemporaryDirectory() as d:
        shutil.copytree(os.path.join(ROOT, ".github"), os.path.join(d, ".github"))
        _edit(os.path.join(d, ".github", "workflows"), "forecast-pipeline.yml",
              "      - name: Hand the column check's line to the failure issue\n"
              "        # Only when the job failed.",
              "      - name: Open issue on failure\n"
              "        if: failure()\n"
              "        uses: actions/github-script@v9\n"
              "        with:\n"
              "          script: |\n"
              "            await github.rest.issues.create({ owner: context.repo.owner,\n"
              "              repo: context.repo.repo, title: 'failed' });\n\n"
              "      - name: Hand the column check's line to the failure issue\n"
              "        # Only when the job failed.")
        got = issue_openers(d)
    assert got - ISSUE_OPENERS == {(".github/workflows/forecast-pipeline.yml", "full-pipeline",
                                    "Open issue on failure")}, sorted(got, key=str)


if __name__ == "__main__":
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
    print(f"{len(fns)} CI-workflow checks passed")
