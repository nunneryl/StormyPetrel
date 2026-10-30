"""THE DESIGN RULE: surf reports never feed a displayed rating, height or forecast.

Written out in docs/face_validation/README.md. Reports (surf_reports: the size answers, the
optional sets answer, the star verdict) are read only by validation code, face_compare.py and
the merge gate that calls it, surf_preview.py. This file fails if anything else comes to read
them:

  1. A SCAN of frontend/, pipeline/, scripts/ and .github/workflows/. A file that names the
     table or the star verdict, or the report modules, must be on an allowlist below, each entry
     with its reason. Two tiers: naming the TABLE (surf_reports, rating_verdict) is allowed only
     to the code that writes a report and the code that validates against them; naming the
     report MODULES (the card, the route) is allowed a little wider, to the page that mounts the
     card.
  2. THE PIPELINE'S IMPORT GRAPH. Every module the publishing workflows run (forecast-pipeline,
     daily-report, ecmwf-wam), and everything those import, transitively, must not reach
     face_compare or surf_preview and must not name the table.
  3. THE WORKFLOWS THEMSELVES name neither.

Changing the rule means writing the decision in docs/face_validation/README.md first. Do not
just widen an allowlist here.

Run: python -m pytest pipeline/tests/test_reports_never_feed_forecasts.py
"""
from __future__ import annotations

import ast
import os
import re
import subprocess

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Naming the table or the star verdict: a read or a write of surf reports.
TABLE = re.compile(r"surf_reports|rating_verdict")
# Naming the report modules or the route, in any spelling: SurfReport, surfReport, surf_report,
# reportSnapshot, /api/reports. Includes everything TABLE matches.
NAMES = re.compile(r"surf_?reports?|rating_verdict|reportsnapshot|/api/reports", re.IGNORECASE)

SCANNED = ("frontend/", "pipeline/", "scripts/", ".github/workflows/")
SKIPPED_DIRS = ("frontend/node_modules/", "frontend/.next/")

# --- who may name the TABLE --------------------------------------------------------------- #
TABLE_ALLOWED = {
    # writing a report: the route, its logic, and the card's POST body
    "frontend/app/api/reports/route.ts",
    "frontend/lib/surfReport.ts",
    "frontend/components/SurfReport.tsx",
    # validation, the only readers
    "pipeline/face_compare.py",
    "pipeline/surf_preview.py",
}

# --- who may name the report MODULES (everything in TABLE_ALLOWED, plus) ------------------- #
NAMES_ALLOWED = TABLE_ALLOWED | {
    # the forecast snapshot a report stores: reads FORECASTS for the reported hour
    "frontend/lib/reportSnapshot.ts",
    # the card's labels and colours
    "frontend/lib/reportUi.ts",
    # mounts the card and reads HOUR_PICKER_SPAN; checked separately below to name no table
    "frontend/app/spot/[slug]/page.tsx",
    # the test scripts list
    "frontend/package.json",
}


def _is_test_or_schema(rel):
    return (rel.startswith("pipeline/tests/")
            or rel.startswith("frontend/tests/")
            or rel.endswith(".test.mts")
            or (rel.startswith("pipeline/migrations/") and rel.endswith(".sql")))


def _files():
    """Tracked files under SCANNED; falls back to walking the tree outside a git checkout."""
    try:
        out = subprocess.run(["git", "ls-files", "-z", "--", *SCANNED], cwd=ROOT,
                             capture_output=True, check=True).stdout.decode("utf-8")
        rels = [r for r in out.split("\0") if r]
    except (OSError, subprocess.CalledProcessError):
        rels = []
        for top in SCANNED:
            for d, dirs, files in os.walk(os.path.join(ROOT, top)):
                dirs[:] = [x for x in dirs if x not in ("node_modules", ".next", "__pycache__")]
                rels += [os.path.relpath(os.path.join(d, f), ROOT).replace(os.sep, "/")
                         for f in files]
    return [r for r in rels if not r.startswith(SKIPPED_DIRS)]


def _read(rel):
    try:
        with open(os.path.join(ROOT, rel), encoding="utf-8") as f:
            return f.read()
    except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
        return ""


def offenders(files, read=_read):
    """{rel: 'table' | 'names'} for every file that names reports without being allowed to."""
    bad = {}
    for rel in files:
        if _is_test_or_schema(rel):
            continue
        text = read(rel)
        if TABLE.search(text) and rel not in TABLE_ALLOWED:
            bad[rel] = "table"
        elif NAMES.search(text) and rel not in NAMES_ALLOWED:
            bad[rel] = "names"
    return bad


def test_no_display_or_pipeline_code_names_surf_reports():
    files = _files()
    assert len(files) > 100, f"the scan found only {len(files)} files: is ROOT right? {ROOT}"
    assert offenders(files) == {}, (
        "These files name surf reports but are not validation or report-collection code. "
        "Reports must never feed a displayed rating, height or forecast "
        "(docs/face_validation/README.md):\n" + "\n".join(f"  {k} ({v})"
                                                        for k, v in sorted(offenders(files).items())))


def test_every_allowlisted_file_exists():
    # An entry for a file that moved would quietly allow its replacement nothing, and the
    # renamed file would then fail the scan; this says why.
    missing = sorted(r for r in NAMES_ALLOWED if not os.path.exists(os.path.join(ROOT, r)))
    assert missing == [], missing


def test_the_spot_page_mounts_the_card_but_names_no_table():
    page = _read("frontend/app/spot/[slug]/page.tsx")
    assert "SurfReportPanel" in page          # it does mount the card
    assert not TABLE.search(page), TABLE.search(page).group(0)
    assert "/api/reports" not in page and "reportSnapshot" not in page


def test_the_scan_catches_a_rating_module_that_reads_reports():
    """The scan's own check, on stand-in files: a frontend rating module, an interpret-like
    pipeline step and a workflow each naming the table, and one importing the card."""
    fake = {
        "frontend/lib/ratings.ts": "const r = await supabase.from('surf_reports').select('*');",
        "pipeline/interpret.py": "rows = fetch('surf_reports')",
        ".github/workflows/forecast-pipeline.yml": "run: python -m pipeline.face_compare  # surf_reports",
        "frontend/components/CurrentConditions.tsx": "import { SurfReportPanel } from './SurfReport';",
        "frontend/lib/heightBasis.ts": "// rating_verdict tells us",
        "pipeline/tests/test_x.py": "surf_reports",                       # tests are exempt
        "pipeline/migrations/099_x.sql": "ALTER TABLE surf_reports ...",  # schema is exempt
        "frontend/lib/formatting.ts": "export const fmt = 1;",            # clean
        "pipeline/face_compare.py": "SELECT * FROM surf_reports",         # allowed reader
        "frontend/lib/reportSnapshot.ts": "// see lib/surfReport.ts",     # allowed module name
        "frontend/lib/reportSnapshot.tsx": "from('surf_reports')",        # names allowed, table not
    }
    assert offenders(list(fake), read=fake.get) == {
        "frontend/lib/ratings.ts": "table",
        "pipeline/interpret.py": "table",
        ".github/workflows/forecast-pipeline.yml": "table",
        "frontend/components/CurrentConditions.tsx": "names",
        "frontend/lib/heightBasis.ts": "table",
        "frontend/lib/reportSnapshot.tsx": "table",
    }


# --------------------------------------------------------------------------- #
# The publishing workflows and everything they import                          #
# --------------------------------------------------------------------------- #

WORKFLOWS = {
    ".github/workflows/forecast-pipeline.yml": {
        "pipeline.column_preflight", "pipeline.forecast.fetch_all", "pipeline.interpret",
        "pipeline.revalidate", "pipeline.db_import"},
    ".github/workflows/daily-report.yml": {"pipeline.daily_report", "pipeline.revalidate"},
    ".github/workflows/ecmwf-wam.yml": {"pipeline.ecmwf_wam"},
}
VALIDATION_MODULES = {"pipeline.face_compare", "pipeline.surf_preview"}


def entry_modules(workflow_text):
    return set(re.findall(r"python -m (pipeline(?:\.\w+)+)", workflow_text))


def _module_path(mod):
    base = os.path.join(ROOT, *mod.split("."))
    for p in (base + ".py", os.path.join(base, "__init__.py")):
        if os.path.exists(p):
            return p
    return None


def _imports(mod, path):
    """pipeline.* modules *mod* imports anywhere in its body (function-level imports too)."""
    tree = ast.parse(_read(os.path.relpath(path, ROOT)))
    pkg = mod if path.endswith("__init__.py") else mod.rsplit(".", 1)[0]
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names if a.name.startswith("pipeline")}
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                parts = pkg.split(".")
                base = ".".join(parts[:len(parts) - (node.level - 1)])
                target = f"{base}.{node.module}" if node.module else base
            elif node.module and node.module.startswith("pipeline"):
                target = node.module
            else:
                continue
            out.add(target)
            # `from pkg import name` may import a submodule
            out |= {f"{target}.{a.name}" for a in node.names if _module_path(f"{target}.{a.name}")}
    return {m for m in out if _module_path(m)}


def import_closure(roots):
    seen, todo = set(), list(roots)
    while todo:
        mod = todo.pop()
        if mod in seen:
            continue
        seen.add(mod)
        path = _module_path(mod)
        if path:
            todo.extend(_imports(mod, path) - seen)
    return seen


def test_the_workflows_run_the_modules_this_test_follows():
    # Written out, so a new step is a failing test until someone checks it against the rule.
    for wf, expected in WORKFLOWS.items():
        assert entry_modules(_read(wf)) == expected, (wf, entry_modules(_read(wf)))


def test_nothing_the_publishing_workflows_run_reaches_reports():
    roots = set().union(*WORKFLOWS.values())
    closure = import_closure(roots)
    assert {"pipeline.interpret", "pipeline.db_import", "pipeline.forecast.tides"} <= closure, \
        "the import walk is not following imports"
    assert closure & VALIDATION_MODULES == set(), closure & VALIDATION_MODULES
    naming = sorted(m for m in closure
                    if TABLE.search(_read(os.path.relpath(_module_path(m), ROOT))))
    assert naming == [], naming


def test_the_workflows_name_no_report_and_no_validation_module():
    for wf in WORKFLOWS:
        text = _read(wf)
        assert not NAMES.search(text), (wf, NAMES.search(text).group(0))
        assert "face_compare" not in text and "surf_preview" not in text, wf


def test_the_import_walk_would_see_a_validation_import():
    """The walk's own check: interpret importing face_compare inside a function is reached."""
    saved = _read
    fake_src = "def f():\n    from . import face_compare\n"
    path = _module_path("pipeline.interpret")
    globals()["_read"] = lambda rel: fake_src if rel == os.path.relpath(path, ROOT) else saved(rel)
    try:
        assert "pipeline.face_compare" in import_closure({"pipeline.interpret"})
    finally:
        globals()["_read"] = saved
