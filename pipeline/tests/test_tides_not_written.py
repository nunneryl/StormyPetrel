"""The pipeline no longer writes tide_predictions; it still fetches the tides and rates with them.

WHY. Nothing reads the table. The spot page's tide chart is drawn from each forecast row's
tide_level_ft (frontend/components/TideChart.tsx); no page, script or pipeline module selects
from tide_predictions; and on 2026-11-20 the database showed no view, rule or function on it
(pg_depend and pg_proc, both empty). It grew about 0.7 MB a day. Its rows from 21 Apr to
31 Oct 2026 are archived (retention-archive bucket, 679,186 rows in 13 parts). No row is
deleted by this change.

WHAT IS HELD HERE
  1. A scheduled run's upload (`db_import --all`, as forecast-pipeline runs it) and
     --forecasts-only write no tide_predictions; --tides-only, by hand, still does.
  2. fetch_all's library path (run_all with no tides argument) writes none either.
  3. The tides are still fetched every run and interpret still reads them: the tide level on
     each forecast row, the only tide the site shows, is untouched.
  4. The workflow's push step runs `db_import --all` and says tides are no longer written.
  5. No frontend file names tide_predictions or the removed TidePrediction type, other than
     TideChart's comment about what it used to read.
"""
from __future__ import annotations

import re
from pathlib import Path

from pipeline import db_import as D
from pipeline import interpret as I
from pipeline.config import TIDES_FORECAST_FILE
from pipeline.forecast import fetch_all as F

ROOT = Path(__file__).resolve().parents[2]


def _calls(monkeypatch):
    calls = []
    for fn in ("import_buoy_snapshot", "import_spots", "import_forecasts", "import_buoys",
               "import_tides", "import_nwps_reads", "import_nwps_freshness"):
        monkeypatch.setattr(D, fn, lambda client, leave_out=None, _fn=fn: calls.append(_fn) or 1)
    monkeypatch.setattr(D, "get_client", lambda: object())
    monkeypatch.setattr(D, "preflight_leave_out", lambda: {})
    return calls


def test_a_scheduled_run_writes_no_tide_predictions(monkeypatch):
    calls = _calls(monkeypatch)
    for argv in ([], ["--all"], ["--forecasts-only"]):
        calls.clear()
        assert D.main(argv) == 0
        assert "import_tides" not in calls, argv
        assert "tide_predictions" not in D.tables_for_args(argv), argv
    # The rest of a full run is still written, in the same order.
    calls.clear()
    D.main(["--all"])
    assert calls == ["import_buoy_snapshot", "import_spots", "import_forecasts", "import_buoys",
                     "import_nwps_reads", "import_nwps_freshness"]


def test_tides_only_by_hand_still_writes_it(monkeypatch):
    calls = _calls(monkeypatch)
    assert D.main(["--tides-only"]) == 0
    assert calls == ["import_tides"]
    assert D.tables_for_args(["--tides-only"]) == ("tide_predictions",)


def test_fetch_alls_library_path_writes_none(monkeypatch):
    calls = _calls(monkeypatch)
    D.run_all(spots=True, forecasts=True)               # fetch_all --push-to-db's call
    assert "import_tides" not in calls
    assert "db_import_mod.run_all(spots=True, forecasts=True)" in (
        ROOT / "pipeline" / "forecast" / "fetch_all.py").read_text()


def test_the_tides_are_still_fetched_and_rated():
    assert F.SOURCES["tides"].__module__ == "pipeline.forecast.tides", "still fetched every run"
    src = (ROOT / "pipeline" / "interpret.py").read_text()
    assert 'p.add_argument("--tides", type=Path, default=TIDES_FORECAST_FILE)' in src
    assert I.TIDES_FORECAST_FILE == TIDES_FORECAST_FILE
    # The forecast rows still carry the tide level the chart draws.
    assert "tide_level_ft" in D.WRITTEN_COLUMNS["forecasts"]


def test_the_workflow_pushes_with_all_and_says_tides_are_not_written():
    wf = (ROOT / ".github" / "workflows" / "forecast-pipeline.yml").read_text()
    assert "- name: Push to Supabase (spots + forecasts + buoys)\n" in wf
    assert "run: python -m pipeline.db_import --all" in wf
    assert "tide_predictions is no\n        # longer written" in wf
    assert "--tides-only" not in wf


def test_no_frontend_file_reads_tide_predictions():
    hits = []
    for p in (ROOT / "frontend").rglob("*"):
        if "node_modules" in p.parts or ".next" in p.parts or not p.is_file():
            continue
        if p.suffix not in (".ts", ".tsx", ".mts"):
            continue
        text = p.read_text(encoding="utf-8")
        if re.search(r"tide_predictions|TidePrediction\b", text):
            hits.append(p.relative_to(ROOT).as_posix())
    assert hits == ["frontend/components/TideChart.tsx"], hits
    chart = (ROOT / "frontend" / "components" / "TideChart.tsx").read_text()
    assert "tide_predictions H/L events used to be spliced in too" in chart
    assert "r.tide_level_ft" in chart
