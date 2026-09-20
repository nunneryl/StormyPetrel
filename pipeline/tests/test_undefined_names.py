"""No function may reference a name that is bound nowhere.

THE DEFECT THIS EXISTS FOR. 89f4e74 extracted summarise_spot out of
scripts/mop_face_validation.py's run(). The arithmetic moved, and with it the local
`blocked` — but the print statement in the caller kept referencing the name, which
compiled to a bare LOAD_GLOBAL for a name bound in no scope at all. Every real run died
there with NameError on the FIRST spot that produced a usable ratio, which is the ordinary
case, and it stayed live on main for thirteen days across five commits.

WHY NOTHING CAUGHT IT. run() needs Supabase and a CDIP OPeNDAP read, so no test and no
--selftest can execute that line; the repo has no linter configured; and none of the nine
GitHub workflows runs a test or lint pass. pyflakes would have flagged it the day it
landed. This is that check, written against symtable so it needs no dependency the repo
does not already have.

WHAT IT CANNOT SEE. A NameError raised through getattr, globals() or an eval string is
invisible here, and so is a name that exists but holds the wrong thing. This catches the
specific, common, mechanical failure — the extract-function leak — not every NameError.
"""
import builtins
import os
import symtable

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

SKIP_DIRS = {".git", "node_modules", ".next", "__pycache__", ".venv", "venv",
             ".mypy_cache", ".pytest_cache", "dist", "build"}

BUILTIN_NAMES = frozenset(dir(builtins)) | {"__file__", "__name__", "__doc__",
                                            "__package__", "__spec__", "__builtins__",
                                            "__debug__", "__loader__"}


def python_files():
    """Every .py file in the repo, as (abspath, relpath), sorted."""
    out = []
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in sorted(filenames):
            if name.endswith(".py"):
                p = os.path.join(dirpath, name)
                out.append((p, os.path.relpath(p, ROOT).replace(os.sep, "/")))
    return sorted(out, key=lambda t: t[1])


def _bound_in(table):
    """Names this scope binds: assignment, import, def/class, for, with-as, except-as."""
    return {s.get_name() for s in table.get_symbols()
            if s.is_assigned() or s.is_imported() or s.is_parameter()}


def undefined_names(source, filename):
    """[(scope_path, name, kind)] for names referenced but bound in no enclosing scope.

    THE RULE symtable actually gives us: a name used in a function and not assigned there
    is marked GLOBAL, not "undefined" — Python defers the lookup to run time. So the check
    is: for each function scope, take the names it treats as global, and subtract what the
    MODULE binds plus the builtins. What is left cannot resolve at run time.

    A name marked FREE is a closure reference to an enclosing function and is fine.
    A `global x` declaration is honoured: the module must still bind x somewhere.
    """
    table = symtable.symtable(source, filename, "exec")

    # A star-import can bind anything, so the module's binding set is unknowable and any
    # verdict would be a guess. Reported by the caller rather than silently trusted.
    star = "*" in source and any(
        line.strip().startswith("from ") and line.strip().endswith("import *")
        for line in source.splitlines())
    if star:
        return "STAR_IMPORT"

    module_bound = _bound_in(table) | BUILTIN_NAMES

    found = []

    def walk(tbl, path):
        # symtable names the module table "top"; that prefix is noise in a failure
        # message, so it is stripped from the reported scope on the way out.
        local = _bound_in(tbl)
        for sym in tbl.get_symbols():
            name = sym.get_name()
            if not sym.is_referenced():
                continue
            if sym.is_free() or sym.is_parameter() or name in local:
                continue
            if name in module_bound:
                continue
            # Anything still here is a global lookup with no module-level binding.
            if tbl.get_type() == "function":
                found.append((".".join(path + [tbl.get_name()]), name))
        for child in tbl.get_children():
            walk(child, path + [tbl.get_name()])

    walk(table, [])
    return [(scope.split("top.", 1)[-1] if scope.startswith("top.") else scope, name)
            for scope, name in found]


def scan_repo():
    """(n_scanned, hits, starred, unparsable).

    THE COUNT COMES BACK WITH THE VERDICT so a caller cannot believe "found nothing"
    without also asserting "looked at something". Mutation testing stubbed the hits to {}
    and the guard still passed; a vacuous guard is worth exactly as much as the check
    nobody ran, which is the defect this whole file exists to answer.
    """
    hits, starred, unparsable = {}, [], []
    n = 0
    for path, rel in python_files():
        n += 1
        src = open(path, encoding="utf-8", errors="replace").read()
        try:
            res = undefined_names(src, rel)
        except SyntaxError as e:
            unparsable.append((rel, str(e)))
            continue
        if res == "STAR_IMPORT":
            starred.append(rel)
        elif res:
            hits[rel] = res
    return n, hits, starred, unparsable


# --------------------------------------------------------------------------- #
# The guard.                                                                   #
# --------------------------------------------------------------------------- #

def test_no_function_references_an_unbound_name():
    """THE GUARD. A new extract-function leak fails here instead of at 3am on a Mac."""
    n_scanned, hits, _, _ = scan_repo()
    assert n_scanned >= 100, f"only {n_scanned} file(s) scanned — the guard is vacuous"
    assert not hits, "\n".join(
        f"{rel}: {scope}() references {name!r}, which is bound in no scope"
        for rel, rows in sorted(hits.items()) for scope, name in rows)


def test_every_file_in_the_repo_parses():
    """A file that will not parse cannot be checked, so silence from the guard above must
    mean 'clean', never 'unreadable'."""
    _, _, _, unparsable = scan_repo()
    assert not unparsable, unparsable


def test_star_imports_are_reported_rather_than_trusted():
    """A star import makes the module's binding set unknowable, so this checker cannot
    give a verdict on that file. It abstains loudly instead of passing quietly. If this
    list grows, those files are no longer covered."""
    _, _, starred, _ = scan_repo()
    assert starred == [], (
        f"{len(starred)} file(s) use `from x import *`, so undefined names in them cannot "
        f"be detected: {starred}")


def test_the_scan_actually_covers_the_repo():
    """Without this the guard is vacuous when the walk finds nothing."""
    files = python_files()
    assert len(files) >= 100, len(files)
    rels = {r for _, r in files}
    for required in ("pipeline/config.py", "scripts/mop_face_validation.py",
                     "pipeline/interpret.py"):
        assert required in rels, required


# --------------------------------------------------------------------------- #
# The checker itself, proven against synthetic sources.                        #
# --------------------------------------------------------------------------- #

def test_it_catches_the_exact_shape_of_the_blocked_bug():
    """The regression, reconstructed: a name bound only in a sibling function and read in
    the caller. This is what shipped in 89f4e74."""
    src = (
        "def summarise(pairs):\n"
        "    blocked = [p for p in pairs if p]\n"
        "    return {'n': len(blocked)}\n"
        "def run(rows):\n"
        "    entry = summarise(rows)\n"
        "    print(f'blocked {len(blocked)}')\n"
    )
    assert ("run", "blocked") in undefined_names(src, "t.py")


def test_the_fixed_form_is_clean():
    """The converse, so the test above pins detection rather than a blanket complaint."""
    src = (
        "def summarise(pairs):\n"
        "    blocked = [p for p in pairs if p]\n"
        "    return {'n': len(blocked)}\n"
        "def run(rows):\n"
        "    entry = summarise(rows)\n"
        "    print(f\"blocked {entry['n']}\")\n"
    )
    assert undefined_names(src, "t.py") == []


def test_a_module_level_binding_is_visible_to_functions():
    assert undefined_names("X = 1\ndef f():\n    return X\n", "t.py") == []


def test_a_builtin_is_not_flagged():
    assert undefined_names("def f(xs):\n    return len(xs) + int('1')\n", "t.py") == []


def test_an_import_binds():
    assert undefined_names("import os\ndef f():\n    return os.sep\n", "t.py") == []
    assert undefined_names("def f():\n    import os\n    return os.sep\n", "t.py") == []
    assert undefined_names("from os import sep\ndef f():\n    return sep\n", "t.py") == []


def test_a_closure_reference_is_not_flagged():
    """A free variable from an enclosing function is bound, just not locally."""
    src = ("def outer():\n"
           "    total = 0\n"
           "    def inner():\n"
           "        return total\n"
           "    return inner\n")
    assert undefined_names(src, "t.py") == []


def test_a_global_declaration_still_requires_a_module_binding():
    ok = "COUNT = 0\ndef f():\n    global COUNT\n    COUNT += 1\n"
    assert undefined_names(ok, "t.py") == []


def test_names_bound_by_for_with_except_and_walrus_are_not_flagged():
    src = ("def f(items, path):\n"
           "    for item in items:\n"
           "        pass\n"
           "    with open(path) as fh:\n"
           "        data = fh.read()\n"
           "    try:\n"
           "        pass\n"
           "    except ValueError as exc:\n"
           "        data = str(exc)\n"
           "    if (n := len(data)) > 0:\n"
           "        return n\n"
           "    return item\n")
    assert undefined_names(src, "t.py") == []


def test_a_comprehension_variable_does_not_leak_a_false_positive():
    src = ("def f(rows):\n"
           "    keep = [r for r in rows if r]\n"
           "    return {k: v for k, v in keep}\n")
    assert undefined_names(src, "t.py") == []


def test_a_function_defined_later_in_the_module_is_visible():
    """Call order does not matter at module level; the name is bound by def time."""
    src = ("def a():\n    return b()\ndef b():\n    return 1\n")
    assert undefined_names(src, "t.py") == []


def test_a_conditional_import_fallback_binds():
    """try/except ImportError is a normal binding, not an undefined name."""
    src = ("try:\n    import ujson as json\nexcept ImportError:\n    import json\n"
           "def f(s):\n    return json.loads(s)\n")
    assert undefined_names(src, "t.py") == []


def test_a_star_import_makes_the_file_unverifiable_rather_than_clean():
    src = "from os.path import *\ndef f():\n    return join('a', 'b')\n"
    assert undefined_names(src, "t.py") == "STAR_IMPORT"


def test_a_typo_in_a_module_constant_is_caught():
    """The other common shape: the name exists, spelled differently."""
    src = "THRESHOLD = 5\ndef f(x):\n    return x > THRESHOLDD\n"
    assert ("f", "THRESHOLDD") in undefined_names(src, "t.py")


def test_class_methods_are_checked_too():
    src = ("class C:\n"
           "    def m(self):\n"
           "        return missing_name\n")
    hits = undefined_names(src, "t.py")
    assert any(name == "missing_name" for _, name in hits), hits
