"""No file may use syntax newer than the Python floor this repo actually runs on.

THE DEFECT THIS EXISTS FOR. pipeline/config.py annotated `str | None` (PEP 604, 3.10+)
without `from __future__ import annotations`. Python 3.9 parses that fine and then raises
    TypeError: unsupported operand type(s) for |: 'type' and 'NoneType'
when the annotation is EVALUATED at def-time — taking down config.py at import and with it
interpret.py, mop_blacks_slice.py and scripts/mop_face_validation.py.

IT SHIPPED BECAUSE EVERY MACHINE THAT TESTED IT WAS NEWER THAN THE MACHINE THAT RUNS IT.
Three environments, nothing pinning any of them:

    the Mac that runs the MOP harness .......... 3.9
    the dev container that writes the code ..... 3.11
    the nine GitHub workflows .................. 3.12

A floor above what actually runs the code is not a floor, it is a wish — so it is set to
the oldest environment, and raising it is a deliberate one-line change here rather than
something that happens by accident on somebody's laptop.

3.9 IS END-OF-LIFE (October 2025) AND THIS SHOULD NOT BE PERMANENT. The floor is 3.9
because that is the truth today, not because 3.9 is the right target. The fix is to move
the Mac to 3.12 to match CI and then raise MIN_PYTHON in one commit; until that happens,
lowering the code to fit an EOL interpreter is the lesser cost of the two, because the
alternative is what just happened — a break nobody could see.

TWO CATEGORIES, AND THE DIFFERENCE MATTERS:

  SYNTAX     3.9 cannot even parse the file. match/case, except*, PEP 695 generics.
             The failure is immediate and total: SyntaxError at import.

  RUNTIME    3.9 parses it and raises when the expression is evaluated. PEP 604 `X | Y`
             is the whole of this category in practice. `from __future__ import
             annotations` rescues it in an ANNOTATION, because PEP 563 makes annotations
             strings that are never evaluated. It rescues NOTHING ELSE: a default value,
             a typing.cast argument, a TypeAlias right-hand side, an isinstance check or
             a plain assignment still evaluates the union and still raises.

NOT BREAKS, though they look like it — both landed IN 3.9 and are used freely here:
  PEP 585 builtin generics (list[int], dict[str, int])   3.9
  PEP 584 dict merge (a | b where both are dicts)        3.9
"""
import ast
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

# THE FLOOR. One place. Raise it here, and only after the Mac has moved.
MIN_PYTHON = (3, 9)

SKIP_DIRS = {".git", "node_modules", ".next", "__pycache__", ".venv", "venv",
             ".mypy_cache", ".pytest_cache", "dist", "build"}

# An operand that makes `|` a TYPE union rather than arithmetic, a set union or a dict
# merge. `None` is the decisive one in practice; the builtin type names cover the rest.
TYPE_OPERANDS = frozenset({
    "None", "int", "str", "float", "bool", "bytes", "bytearray", "complex",
    "list", "dict", "set", "frozenset", "tuple", "type", "object",
})

# Stdlib surface newer than the floor. Not exhaustive by construction — a name-based
# check cannot be — so it is a net for the common cases, not a proof.
NEWER_THAN_3_9 = {
    "TypeAlias": "3.10 typing.TypeAlias", "TypeGuard": "3.10 typing.TypeGuard",
    "ParamSpec": "3.10 typing.ParamSpec", "Concatenate": "3.10 typing.Concatenate",
    "pairwise": "3.10 itertools.pairwise",
    "Self": "3.11 typing.Self", "Never": "3.11 typing.Never",
    "StrEnum": "3.11 enum.StrEnum", "TaskGroup": "3.11 asyncio.TaskGroup",
    "ExceptionGroup": "3.11 builtins.ExceptionGroup", "tomllib": "3.11 tomllib",
    "override": "3.12 typing.override",
}


def python_files():
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if name.endswith(".py"):
                p = os.path.join(dirpath, name)
                out.append((p, os.path.relpath(p, ROOT).replace(os.sep, "/")))
    return sorted(out, key=lambda t: t[1])


def is_type_union(node):
    """True for a PEP 604 `X | Y` — as distinct from int|int, set|set or dict|dict.

    Conservative on purpose: it fires only when an operand is recognisably a type. A
    union of two user classes (Foo | Bar) is missed, and that is the accepted cost of
    never flagging `flags | MASK` or a dict merge as a version break.
    """
    if not (isinstance(node, ast.BinOp) and isinstance(node.op, ast.BitOr)):
        return False
    for side in (node.left, node.right):
        if isinstance(side, ast.Constant) and side.value is None:
            return True
        if isinstance(side, ast.Name) and side.id in TYPE_OPERANDS:
            return True
        if (isinstance(side, ast.Subscript) and isinstance(side.value, ast.Name)
                and side.value.id in TYPE_OPERANDS):
            return True
        if isinstance(side, ast.Attribute) and side.attr in {
                "Any", "Optional", "Union", "Callable", "Sequence", "Mapping", "Iterable"}:
            return True
        if isinstance(side, ast.BinOp) and is_type_union(side):
            return True
    return False


def _annotation_node_ids(tree):
    """Every node id that sits inside an annotation, which PEP 563 turns into a string."""
    ids = set()

    def mark(node):
        for sub in ast.walk(node):
            ids.add(id(sub))

    for n in ast.walk(tree):
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if n.returns is not None:
                mark(n.returns)
            args = n.args
            for a in (list(args.args) + list(getattr(args, "posonlyargs", []))
                      + list(args.kwonlyargs) + [args.vararg, args.kwarg]):
                if a is not None and a.annotation is not None:
                    mark(a.annotation)
        elif isinstance(n, ast.AnnAssign) and n.annotation is not None:
            mark(n.annotation)
    return ids


def scan(path, relpath):
    """[(category, line, message)] — SYNTAX or RUNTIME findings for this file.

    EVERY RULE IS TAGGED WITH THE VERSION IT NEEDS AND COMPARED AGAINST MIN_PYTHON, so
    raising the floor is genuinely the one-line edit the docstring claims. A scan that
    hardcoded "3.9" would leave MIN_PYTHON as decoration: the constant would move and the
    checks would not, which is the same shape as the RUN_ON literal this repo already
    fixed once.
    """
    def needed(version):
        return version > MIN_PYTHON

    src = open(path, encoding="utf-8", errors="replace").read()
    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        return [("SYNTAX", getattr(e, "lineno", 0) or 0, f"does not parse: {e}")]

    has_future = any(
        isinstance(n, ast.ImportFrom) and n.module == "__future__"
        and any(a.name == "annotations" for a in n.names)
        for n in ast.walk(tree))
    in_annotation = _annotation_node_ids(tree)

    out = []
    for n in ast.walk(tree):
        line = getattr(n, "lineno", 0)
        cls = n.__class__.__name__
        floor = ".".join(str(v) for v in MIN_PYTHON)
        if cls == "Match" and needed((3, 10)):
            out.append(("SYNTAX", line, f"match statement is 3.10+; {floor} cannot parse it"))
        elif cls == "TryStar" and needed((3, 11)):
            out.append(("SYNTAX", line, f"except* is 3.11+; {floor} cannot parse it"))
        elif cls == "TypeAlias" and needed((3, 12)):
            out.append(("SYNTAX", line, "PEP 695 `type X = ...` is 3.12+"))
        elif (isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
              and getattr(n, "type_params", None) and needed((3, 12))):
            out.append(("SYNTAX", line, "PEP 695 type parameters are 3.12+"))
        elif is_type_union(n) and needed((3, 10)):
            if id(n) in in_annotation:
                if not has_future:
                    out.append(("RUNTIME", line,
                                "PEP 604 `X | Y` in an annotation with no `from __future__ "
                                f"import annotations` — evaluated at def-time, TypeError "
                                f"on {floor}"))
            else:
                out.append(("RUNTIME", line,
                            "PEP 604 `X | Y` in a runtime position — the future import does "
                            f"NOT rescue this; TypeError on {floor}"))
        elif (isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
              and n.func.id == "zip" and needed((3, 10))):
            if any(k.arg == "strict" for k in n.keywords):
                out.append(("RUNTIME", line, "zip(strict=) is 3.10+"))
        elif isinstance(n, ast.Attribute) and n.attr in NEWER_THAN_3_9:
            ver = tuple(int(x) for x in NEWER_THAN_3_9[n.attr].split()[0].split("."))
            if needed(ver):
                out.append(("RUNTIME", line, f"{n.attr} is {NEWER_THAN_3_9[n.attr]}"))
    return out


def scan_repo():
    """(n_scanned, findings). THE COUNT IS RETURNED WITH THE VERDICT ON PURPOSE.

    A guard whose subject can be emptied passes while checking nothing — mutation testing
    stubbed the findings to {} and the test still went green, the same vacuous-guard shape
    found in the selftest wiring. Returning both forces the caller to assert that a real
    scan happened before believing that it found nothing.
    """
    findings = {}
    n = 0
    for path, rel in python_files():
        n += 1
        res = scan(path, rel)
        if res:
            findings[rel] = res
    return n, findings


# --------------------------------------------------------------------------- #
# The guard.                                                                   #
# --------------------------------------------------------------------------- #

def test_no_file_uses_syntax_newer_than_the_floor():
    """THE GUARD. config.py's `str | None` would have failed here on the day it landed."""
    n_scanned, findings = scan_repo()
    assert n_scanned >= 100, f"only {n_scanned} file(s) scanned — the guard is vacuous"
    assert not findings, "\n".join(
        f"{rel}:{line}  [{cat}]  {msg}"
        for rel, rows in sorted(findings.items()) for cat, line, msg in rows)


def test_the_floor_is_declared_in_exactly_one_place():
    """Raising the floor must be one edit, not a hunt.

    COUNTED FROM THE AST, not from the text — a text count matches the search string
    inside this very assertion and reports two. That self-reference has now bitten three
    times in this repo (the #217 grep for "mop_spread.json", the selftest-wiring scan, and
    here), so it is worth naming: a test that greps for a string its own source contains
    is checking itself as well as its subject.
    """
    assert MIN_PYTHON == (3, 9)
    tree = ast.parse(open(os.path.join(HERE, "test_python_version_floor.py"),
                          encoding="utf-8").read())
    assignments = [n for n in tree.body if isinstance(n, ast.Assign)
                   and any(isinstance(t, ast.Name) and t.id == "MIN_PYTHON"
                           for t in n.targets)]
    assert len(assignments) == 1, [n.lineno for n in assignments]


def test_the_running_interpreter_meets_the_floor():
    """Whatever machine runs the suite must itself be at or above the floor."""
    import sys

    assert sys.version_info[:2] >= MIN_PYTHON, sys.version


def test_the_scan_actually_covers_the_repo():
    files = python_files()
    assert len(files) >= 100, len(files)
    rels = {r for _, r in files}
    for required in ("pipeline/config.py", "pipeline/interpret.py",
                     "scripts/mop_face_validation.py"):
        assert required in rels, required


def test_config_carries_the_future_import():
    """The specific regression, pinned by name. config.py was the ONLY file using PEP 604
    without it, which is exactly why it was the only one that broke."""
    src = open(os.path.join(ROOT, "pipeline", "config.py"), encoding="utf-8").read()
    assert "from __future__ import annotations" in src


def test_every_file_using_pep_604_in_an_annotation_has_the_future_import():
    """The general form of the same rule, so the next file to adopt PEP 604 is covered
    without anyone remembering. 44 files rely on this rescue today."""
    offenders = []
    for path, rel in python_files():
        try:
            tree = ast.parse(open(path, encoding="utf-8", errors="replace").read())
        except SyntaxError:
            continue
        has_future = any(
            isinstance(n, ast.ImportFrom) and n.module == "__future__"
            and any(a.name == "annotations" for a in n.names) for n in ast.walk(tree))
        if has_future:
            continue
        ann = _annotation_node_ids(tree)
        if any(is_type_union(n) and id(n) in ann for n in ast.walk(tree)):
            offenders.append(rel)
    assert not offenders, offenders


# --------------------------------------------------------------------------- #
# The detector itself, on synthetic sources.                                   #
# --------------------------------------------------------------------------- #

def _scan_src(src, name="t.py"):
    import tempfile

    d = tempfile.mkdtemp()
    p = os.path.join(d, name)
    with open(p, "w") as fh:
        fh.write(src)
    try:
        return scan(p, name)
    finally:
        os.unlink(p)


def test_it_catches_the_exact_config_py_regression():
    src = "def f(source: str | None) -> int:\n    return 0\n"
    got = _scan_src(src)
    assert [c for c, _, _ in got] == ["RUNTIME"], got
    assert "def-time" in got[0][2]


def test_the_future_import_rescues_an_annotation():
    src = ("from __future__ import annotations\n"
           "def f(source: str | None) -> int:\n    return 0\n")
    assert _scan_src(src) == []


def test_the_future_import_does_NOT_rescue_a_runtime_position():
    """The distinction the brief called out, pinned in four shapes."""
    for src in (
        "from __future__ import annotations\nAlias = str | None\n",
        "from __future__ import annotations\nimport typing\n"
        "def f(v):\n    return typing.cast(str | None, v)\n",
        "from __future__ import annotations\ndef f(v):\n"
        "    return isinstance(v, int | str)\n",
        "from __future__ import annotations\ndef f(t=int | None):\n    return t\n",
    ):
        got = _scan_src(src)
        assert got, src
        assert got[0][0] == "RUNTIME", got
        assert "does NOT rescue" in got[0][2], got


def _parses_here(src):
    """Can the RUNNING interpreter parse this source?

    THE TESTS BELOW BRANCH ON CAPABILITY, NOT ON sys.version_info, and that distinction is
    the whole point of this helper. A version-floor test that only passes on interpreters
    ABOVE the floor is not testing the floor — and that is exactly what shipped: two tests
    fed `match` to the scanner and asserted the message "match statement is 3.10+", which
    only appears when the parser accepted the syntax in the first place. On 3.9, the floor
    itself, ast.parse raises and the scanner correctly reports `does not parse` instead, so
    the tests failed on the one interpreter they were written to protect.

    Asking the parser rather than the version number also means this keeps working when
    the floor moves: nothing here has to be updated to know what 3.13 can parse.
    """
    try:
        ast.parse(src)
        return True
    except SyntaxError:
        return False


MATCH_SRC = "def f(x):\n    match x:\n        case 1:\n            return 2\n"
EXCEPT_STAR_SRC = ("def f():\n    try:\n        pass\n"
                   "    except* ValueError:\n        pass\n")


def _assert_refused_as_syntax(src, label):
    """The construct is refused as SYNTAX, by whichever route this interpreter can take.

    A SEPARATE HELPER SO THE SIMULATION BELOW CAN RUN IT UNDER AN OLDER PARSER. Left
    inline, this logic would only ever execute on the branch the running interpreter
    happens to take, and the 3.9 branch would ship untested from a 3.11 container — which
    is how the original version of these tests reached a Mac it had never run on.
    """
    got = _scan_src(src)
    assert got, f"{label} produced no finding at all"
    assert got[0][0] == "SYNTAX", got
    if _parses_here(src):
        # This interpreter understands the syntax, so the NAMED rule must be what fired —
        # "does not parse" here would mean the version rule had gone missing.
        assert label in got[0][2], got
    else:
        # This interpreter is older than the construct. Refusing to parse is the correct
        # and stronger outcome; the named rule cannot fire and must not be demanded.
        assert "does not parse" in got[0][2], got


def test_match_and_except_star_are_syntax_findings():
    """Refused on every interpreter at or above the floor — by one of two routes."""
    _assert_refused_as_syntax(MATCH_SRC, "match")
    _assert_refused_as_syntax(EXCEPT_STAR_SRC, "except*")


def test_3_9_features_are_NOT_flagged():
    """PEP 585 generics and PEP 584 dict merge both landed IN 3.9. Flagging them would
    make the guard cry wolf on code that is fine, and the repo uses both freely."""
    assert _scan_src("def f(xs: list[int]) -> dict[str, int]:\n    return {}\n") == []
    assert _scan_src("def merge(a: dict, b: dict) -> dict:\n    return a | b\n") == []
    assert _scan_src("def f(a, b):\n    return a | b\n") == []
    assert _scan_src("FLAGS = 1\nMASK = 2\ndef f():\n    return FLAGS | MASK\n") == []


def test_a_set_union_is_not_flagged():
    assert _scan_src("def f(a: set, b: set):\n    return a | b\n") == []


def test_a_union_of_a_USER_class_with_None_is_caught():
    """`None` is the only signal here — neither operand is a builtin type name.

    This is the commonest real shape after `str | None`, and removing the None check
    survived every other test until this one existed.
    """
    got = _scan_src("class Spot: pass\ndef f(s: Spot | None):\n    return s\n")
    assert got, "a UserClass | None annotation was not detected"
    assert got[0][0] == "RUNTIME"
    got = _scan_src("class Spot: pass\nAlias = Spot | None\n")
    assert got and "does NOT rescue" in got[0][2], got


def test_newer_stdlib_surface_is_flagged():
    assert _scan_src("import typing\nX: typing.Self = None\n") != []
    assert _scan_src("def f(a, b):\n    return zip(a, b, strict=True)\n") != []


def test_an_unparsable_file_is_a_syntax_finding_not_silence():
    assert _scan_src("def (:\n")[0][0] == "SYNTAX"


def test_the_older_interpreter_path_is_exercised_even_on_a_newer_one():
    """Run the 3.9 branch HERE, so it is not a branch only one machine ever takes.

    The capability branching above is correct but self-fulfilling on a new interpreter:
    3.11 always takes the "parser accepted it" path, so the 3.9 path ships untested from
    the container and is first exercised on the Mac — which is precisely how the original
    defect reached a machine it had never run on. Patching ast.parse to refuse the syntax
    reproduces what 3.9 does, on any interpreter, so both branches are covered everywhere.
    """
    real_parse = ast.parse

    def parse_like_an_older_python(src, *a, **kw):
        if "match " in src or "except*" in src:
            raise SyntaxError("invalid syntax")
        return real_parse(src, *a, **kw)

    ast.parse = parse_like_an_older_python
    try:
        assert _parses_here(MATCH_SRC) is False
        for src in (MATCH_SRC, EXCEPT_STAR_SRC):
            got = _scan_src(src)
            assert got and got[0][0] == "SYNTAX", got
            assert "does not parse" in got[0][2], got

        # AND THE REAL ASSERTION HELPER, run under the older parser. Without this the
        # capability branch in _assert_refused_as_syntax is never taken on a new
        # interpreter: replacing `if _parses_here(src)` with `if True` — which is exactly
        # the bug that shipped — passes everything on 3.11 and fails on 3.9. Mutation
        # testing confirmed that survivor before this line existed.
        _assert_refused_as_syntax(MATCH_SRC, "match")
        _assert_refused_as_syntax(EXCEPT_STAR_SRC, "except*")

        # PEP 604 still parses on that older interpreter, so its rule still fires — which
        # is exactly why it, and not match, carries the floor-relaxation demonstration.
        pep604 = "def f(x: str | None):\n    return x\n"
        assert _parses_here(pep604) is True
        got = _scan_src(pep604)
        assert got and got[0][0] == "RUNTIME", got
    finally:
        ast.parse = real_parse

    # restored, and the real parser is back
    assert ast.parse is real_parse
    assert _parses_here("x = 1\n") is True


def test_the_rules_are_driven_by_min_python_not_hardcoded():
    """Raising the floor must actually relax the checks, or MIN_PYTHON is decoration.

    Demonstrated by temporarily moving the floor to 3.11, at which point PEP 604 stops
    being a finding. If this fails, someone has hardcoded a version back into scan() and
    the constant no longer governs anything.

    THE DEMONSTRATION USES PEP 604, NOT `match`, AND THAT IS DELIBERATE. `X | Y` is
    syntactically valid all the way back to 3.9 — it is an ordinary BinOp, and only its
    EVALUATION fails there, which is why config.py raised TypeError at import rather than
    SyntaxError. So every interpreter at or above the floor can parse it, and raising the
    floor genuinely changes the verdict on all of them. `match` cannot do this job: on 3.9
    it fails to parse whatever the floor says, so it would still be a finding at a 3.11
    floor and this test would fail on the one interpreter it exists to protect. That was
    the shipped bug. match's version-gating is covered above instead, by the route a 3.9
    interpreter can actually express.

    IT PATCHES globals(), NOT A RE-IMPORT OF THIS MODULE BY NAME. The first draft did
    `import pipeline.tests.test_python_version_floor as mod` and set mod.MIN_PYTHON —
    which works when the suite imports this file as that dotted name, and silently does
    nothing when the runner loads it via spec_from_file_location under a different name,
    because those are two distinct module objects. It passed standalone and failed in the
    suite. globals() is the namespace scan() actually reads, whatever the loader called
    this file.
    """
    ns = globals()
    pep604 = "def f(x: str | None):\n    return x\n"
    assert _parses_here(pep604), "PEP 604 must parse everywhere at or above the floor"

    assert _scan_src(pep604), "PEP 604 should be a finding at the 3.9 floor"

    original = ns["MIN_PYTHON"]
    try:
        ns["MIN_PYTHON"] = (3, 11)
        assert _scan_src(pep604) == [], "raising the floor did not relax the PEP 604 rule"
        # And the match rule too, but only where the parser can get that far.
        if _parses_here(MATCH_SRC):
            assert _scan_src(MATCH_SRC) == [], "raising the floor did not relax match"
    finally:
        ns["MIN_PYTHON"] = original
    assert ns["MIN_PYTHON"] == (3, 9)
    assert _scan_src(pep604), "the floor was not restored"
