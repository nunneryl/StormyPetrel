"""pipeline/nwps_stale_gate.py: a full pipeline run ends FAILED, so the failure issue opens, when
5 or more NWPS offices are stale; fewer stay a warning. Every expected value is written by hand.

The rows are shaped like the 2026-11-10 08:15Z run's, the last before the NWPS v1.5 rename was
fixed: box read 11-09 00Z, 24 h behind the newest listed, after four HTTP 500s; ilm's listing
went unanswered; mfr was fresh.
"""
from __future__ import annotations

import json

from pipeline import nwps_stale_gate as G
from pipeline.tests.test_ci_workflow import yaml_code
from pipeline.tests.test_column_preflight import WORKFLOW, steps


def _row(wfo, reason=None):
    return {"wfo": wfo, "stale_reason": reason}


BOX = _row("box", "24 h behind the newest listed (4 failed: HTTP 500)")
ILM = _row("ilm", "no cycle read — NOMADS listing unanswered")
FRESH = _row("mfr")


def test_five_stale_offices_fail_with_one_line_naming_each():
    rows = [BOX, ILM, _row("gyx", "no cycle read — none listed"),
            _row("lox", "18 h behind the newest listed (3 failed: HTTP 500)"),
            _row("sju", "newest listed cycle is 25 h old"), FRESH]
    assert G.verdict(rows) == (
        "NWPS stale for 5 of 6 offices this run (forecast rows keep older cycles): "
        "box 24 h behind the newest listed (4 failed: HTTP 500); "
        "ilm no cycle read — NOMADS listing unanswered; gyx no cycle read — none listed; "
        "lox 18 h behind the newest listed (3 failed: HTTP 500); sju newest listed cycle is 25 h old")


def test_four_stale_offices_do_not():
    assert G.STALE_OFFICES_TO_FAIL == 5
    assert G.verdict([BOX, ILM, BOX, ILM, FRESH]) is None
    assert G.verdict([FRESH] * 25) is None


def test_main_fails_on_many_stale_and_passes_otherwise(tmp_path, capsys):
    p = tmp_path / "nwps_freshness.json"
    p.write_text(json.dumps([BOX, ILM, BOX, ILM, BOX] + [FRESH] * 20))
    assert G.main([], path=p) == 1
    out = capsys.readouterr().out
    assert out.startswith("::error::NWPS stale for 5 of 25 offices this run") and out.count("\n") == 1
    p.write_text(json.dumps([BOX] + [FRESH] * 24))
    assert G.main([], path=p) == 0
    assert capsys.readouterr().out == "NWPS: 25 offices, 1 stale (the run fails at 5).\n"


def test_a_missing_empty_or_unreadable_file_fails(tmp_path, capsys):
    p = tmp_path / "nwps_freshness.json"
    assert G.main([], path=p) == 1
    assert capsys.readouterr().out == (
        f"::error::NWPS freshness file missing ({p}): the NWPS fetch did not finish this run.\n")
    p.write_text("[]")
    assert G.main([], path=p) == 1
    assert capsys.readouterr().out == (
        f"::error::NWPS freshness file is empty ({p}): no office was fetched this run.\n")
    p.write_text("{not json")
    assert G.main([], path=p) == 1
    assert capsys.readouterr().out.startswith(f"::error::NWPS freshness file unreadable ({p}): ")


def test_it_reads_the_file_nwps_fetch_writes():
    from pipeline import config
    assert G.NWPS_FRESHNESS_FILE == config.NWPS_FRESHNESS_FILE


def test_the_step_runs_after_every_upload_and_before_the_column_check():
    text = yaml_code(WORKFLOW.read_text(encoding="utf-8"))
    ss = steps(text, "full-pipeline")
    runs = [s.get("run", "") for s in ss]
    gate = runs.index("python -m pipeline.nwps_stale_gate")
    work = [i for i, r in enumerate(runs) if "pipeline.db_import" in r or "pipeline.revalidate --scope" in r]
    assert work and max(work) < gate < runs.index("python -m pipeline.column_preflight --finish")
    assert ss[gate].get("if") == "${{ !cancelled() }}", "it reports even after an earlier failure"
    assert "continue-on-error" not in ss[gate], "it must be able to fail the run"
    assert "python -m pipeline.nwps_stale_gate" not in [
        s.get("run", "") for s in steps(text, "buoy-update")], "the buoy job reads no NWPS"
