"""The dispatch gate (scripts/dispatch_gate.py) and the schedule wiring in forecast-pipeline.yml.

WHAT IS PINNED HERE:
  1. the slot list, the anchor and the window, at and around every boundary;
  2. decide(): a manual run always goes ahead; a scheduled or external run is skipped only by
     an EARLIER run of the SAME job, created in its window, that is queued, running or
     succeeded — a failed or cancelled run covers nothing, and the lower run id breaks a tie;
  3. run() and main() FAIL OPEN: every error answers "run", and main always exits 0;
  4. the runs-list call over real HTTP: its path, query, headers and pagination;
  5. the workflow: the slots, the fallback crons 20 minutes after them, the run-name, the
     concurrency group keyed on the logical job (it used to be keyed on the trigger, so a
     scheduled and a dispatched full run could run at once), the gate job and its needs, and
     the Healthchecks ping after a successful full run;
  6. docs/scheduling.md agrees with the workflow, and its request bodies are valid JSON.

Times are 2026-10-13 UTC unless stated. Every expected value is written by hand; none is
obtained by calling the function under test.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import urllib.error
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

import dispatch_gate as G  # noqa: E402
from pipeline.tests.test_ci_workflow import (  # noqa: E402
    job_key, jobs_of, steps_of, top_level, yaml_code)

SCRIPT = ROOT / "scripts" / "dispatch_gate.py"
WORKFLOW = ROOT / ".github" / "workflows" / "forecast-pipeline.yml"
DOC = ROOT / "docs" / "scheduling.md"
UTC = timezone.utc
SLOTS = [(1, 45), (8, 15), (13, 45), (20, 15)]


def at(hh, mm, ss=0, day=13):
    return datetime(2026, 10, day, hh, mm, ss, tzinfo=UTC)


def gh_run(run_id, title, created, status="completed", conclusion="success"):
    """One run as the REST runs list returns it, cut to the fields the gate reads."""
    return {"id": run_id, "display_title": title, "created_at": created,
            "status": status, "conclusion": conclusion}


# --------------------------------------------------------------------------- #
# 1 — slots, anchor, window                                                    #
# --------------------------------------------------------------------------- #
def test_parse_slots_reads_the_workflows_form_and_sorts():
    assert G.parse_slots("01:45 08:15 13:45 20:15") == [(1, 45), (8, 15), (13, 45), (20, 15)]
    assert G.parse_slots("20:15  01:45\n08:15") == [(1, 45), (8, 15), (20, 15)]
    assert G.parse_slots("00:00 23:59") == [(0, 0), (23, 59)]


@pytest.mark.parametrize("bad", ["", "   ", None, "1:45", "24:00", "08:60", "08:15,13:45",
                                 "08:15 08:15", "8.15", "08:15Z"])
def test_parse_slots_refuses_anything_else(bad):
    with pytest.raises(ValueError):
        G.parse_slots(bad)


@pytest.mark.parametrize("now, expected", [
    (at(8, 15), at(8, 15)),                  # on the slot
    (at(8, 35), at(8, 15)),                  # the on-time fallback cron
    (at(13, 39, 59), at(8, 15)),             # still the 08:15 slot ...
    (at(13, 40), at(13, 45)),                # ... until 5 minutes before the next
    (at(8, 9, 59), at(1, 45)),               # 08:14:59 with the margin: not yet 08:15
    (at(8, 10), at(8, 15)),                  # an early dispatch belongs to its slot
    (at(1, 0), at(20, 15, day=12)),          # before the day's first slot: yesterday's last
    (at(0, 0), at(20, 15, day=12)),
    (at(23, 59), at(20, 15)),                # 00:04 tomorrow has no slot yet: today's last
    (at(1, 40), at(1, 45)),
])
def test_the_full_pipeline_anchor(now, expected):
    assert G.anchor("full-pipeline", SLOTS, now) == expected


@pytest.mark.parametrize("now, expected", [
    (at(13, 0), at(13, 0)),
    (at(13, 37), at(13, 0)),                 # a late hourly cron
    (at(13, 54, 59), at(13, 0)),
    (at(13, 55), at(14, 0)),                 # 5 minutes early belongs to the next hour
    (at(23, 58), at(0, 0, day=14)),
])
def test_the_buoy_anchor_is_the_top_of_the_hour(now, expected):
    assert G.anchor("buoy-update", SLOTS, now) == expected


def test_an_unknown_job_has_no_anchor():
    with pytest.raises(ValueError):
        G.anchor("tides", SLOTS, at(8, 15))


def test_job_of_title_reads_the_run_name_and_nothing_else():
    assert G.job_of_title("full-pipeline (external)") == "full-pipeline"
    assert G.job_of_title("buoy-update (schedule)") == "buoy-update"
    assert G.job_of_title("full-pipeline (manual)") == "full-pipeline"
    assert G.job_of_title("forecast-pipeline") is None          # a run from before run-name
    assert G.job_of_title("full-pipelines (external)") is None
    assert G.job_of_title(" full-pipeline") is None
    assert G.job_of_title("") is None and G.job_of_title(None) is None


@pytest.mark.parametrize("status, conclusion, expected", [
    ("queued", None, True), ("in_progress", None, True), ("waiting", None, True),
    ("pending", None, True), ("requested", None, True),
    ("completed", "success", True),
    ("completed", "failure", False), ("completed", "cancelled", False),
    ("completed", "skipped", False), ("completed", "timed_out", False),
    ("completed", "neutral", False), ("completed", None, False), (None, None, False),
])
def test_what_covers_a_slot(status, conclusion, expected):
    assert G.covers({"status": status, "conclusion": conclusion}) is expected


# --------------------------------------------------------------------------- #
# 2 — decide                                                                   #
# --------------------------------------------------------------------------- #
DISPATCHED_0815 = gh_run(100, "full-pipeline (external)", "2026-10-13T08:15:02Z")


def decide(runs, now, own_id=105, job="full-pipeline", event="schedule", trigger=""):
    return G.decide(job, event, trigger, now, own_id, SLOTS, runs)


def test_a_late_cron_run_after_a_successful_dispatch_is_skipped():
    assert decide([DISPATCHED_0815], at(13, 2)) == (
        False, "skipped: run 100 already covers the full-pipeline slot of 2026-10-13 08:15 UTC "
               "(created since 08:10)")


def test_the_on_time_fallback_cron_is_skipped_too():
    assert decide([DISPATCHED_0815], at(8, 35))[0] is False


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "timed_out"])
def test_a_dispatch_that_did_not_succeed_covers_nothing_so_the_cron_run_is_a_retry(conclusion):
    run = gh_run(100, "full-pipeline (external)", "2026-10-13T08:15:02Z", conclusion=conclusion)
    assert decide([run], at(13, 2)) == (
        True, "no earlier run covers the full-pipeline slot of 2026-10-13 08:15 UTC")


def test_a_dispatch_still_running_covers_the_slot():
    run = gh_run(110, "full-pipeline (external)", "2026-10-13T13:45:01Z",
                 status="in_progress", conclusion=None)
    assert decide([run], at(14, 5), own_id=111) == (
        False, "skipped: run 110 already covers the full-pipeline slot of 2026-10-13 13:45 UTC "
               "(created since 13:40)")


def test_a_cron_run_so_late_it_lands_after_the_next_slot_checks_the_next_slot():
    """The 08:15 run succeeded, but the 13:45 dispatch never came (the outside scheduler was
    down). A cron run at 13:50 belongs to 13:45, which nothing covers, so it goes ahead."""
    assert decide([DISPATCHED_0815], at(13, 50)) == (
        True, "no earlier run covers the full-pipeline slot of 2026-10-13 13:45 UTC")


def test_the_lower_run_id_breaks_a_tie_so_exactly_one_of_two_goes_ahead():
    a = gh_run(200, "full-pipeline (external)", "2026-10-13T20:15:01Z",
               status="in_progress", conclusion=None)
    b = gh_run(201, "full-pipeline (schedule)", "2026-10-13T20:15:03Z",
               status="queued", conclusion=None)
    assert decide([a, b], at(20, 16), own_id=200)[0] is True
    assert decide([a, b], at(20, 16), own_id=201) == (
        False, "skipped: run 200 already covers the full-pipeline slot of 2026-10-13 20:15 UTC "
               "(created since 20:10)")


def test_two_external_dispatches_for_one_slot_run_once():
    first = gh_run(299, "full-pipeline (external)", "2026-10-13T08:15:01Z")
    assert decide([first], at(8, 15, 5), own_id=300, event="workflow_dispatch",
                  trigger="external")[0] is False


def test_a_run_of_the_other_job_covers_nothing():
    buoy = gh_run(100, "buoy-update (external)", "2026-10-13T08:20:00Z")
    assert decide([buoy], at(8, 35))[0] is True
    full = gh_run(100, "full-pipeline (external)", "2026-10-13T08:15:02Z")
    assert decide([full], at(8, 37), job="buoy-update")[0] is True


def test_a_run_from_before_run_name_covers_nothing():
    old = gh_run(100, "forecast-pipeline", "2026-10-13T08:15:02Z")
    assert decide([old], at(8, 35))[0] is True


def test_the_window_opens_exactly_five_minutes_before_the_slot():
    on_edge = gh_run(100, "full-pipeline (external)", "2026-10-13T08:10:00Z")
    before = gh_run(100, "full-pipeline (external)", "2026-10-13T08:09:59Z")
    assert decide([on_edge], at(8, 35))[0] is False
    assert decide([before], at(8, 35))[0] is True


def test_a_later_run_never_covers_an_earlier_one():
    later = gh_run(106, "full-pipeline (external)", "2026-10-13T08:15:02Z")
    same = gh_run(105, "full-pipeline (external)", "2026-10-13T08:15:02Z")
    assert decide([later, same], at(8, 35), own_id=105)[0] is True


def test_the_earliest_covering_run_is_the_one_named():
    runs = [gh_run(103, "full-pipeline (manual)", "2026-10-13T09:00:00Z"),
            gh_run(101, "full-pipeline (external)", "2026-10-13T08:15:02Z"),
            gh_run(102, "full-pipeline (schedule)", "2026-10-13T08:40:00Z",
                   conclusion="failure")]
    assert decide(runs, at(9, 30)) == (
        False, "skipped: run 101 already covers the full-pipeline slot of 2026-10-13 08:15 UTC "
               "(created since 08:10)")


def test_the_buoy_gate_uses_the_hour():
    dispatched = gh_run(498, "buoy-update (external)", "2026-10-13T13:00:03Z")
    early = gh_run(498, "buoy-update (external)", "2026-10-13T12:59:58Z")
    last_hour = gh_run(498, "buoy-update (external)", "2026-10-13T12:00:03Z")
    assert decide([dispatched], at(13, 37), own_id=500, job="buoy-update") == (
        False, "skipped: run 498 already covers the buoy-update slot of 2026-10-13 13:00 UTC "
               "(created since 12:55)")
    assert decide([early], at(13, 37), own_id=500, job="buoy-update")[0] is False
    assert decide([last_hour], at(13, 37), own_id=500, job="buoy-update")[0] is True


@pytest.mark.parametrize("trigger", ["manual", "", None])
def test_a_manual_run_always_goes_ahead(trigger):
    assert decide([DISPATCHED_0815], at(8, 20), event="workflow_dispatch", trigger=trigger) == (
        True, "a manual run always goes ahead")


def test_an_event_the_gate_does_not_know_is_not_gated():
    assert decide([DISPATCHED_0815], at(8, 20), event="push") == (
        True, "a push run is not gated")


# --------------------------------------------------------------------------- #
# 3 — run() and main(): the environment, and failing open                       #
# --------------------------------------------------------------------------- #
ENV = {"GATE_JOB": "full-pipeline", "GITHUB_EVENT_NAME": "schedule", "GATE_TRIGGER": "",
       "DISPATCH_SLOTS": "01:45 08:15 13:45 20:15", "GITHUB_RUN_ID": "105",
       "GITHUB_REPOSITORY": "o/r", "GITHUB_API_URL": "https://api.example",
       "GITHUB_TOKEN": "t0ken",
       "GITHUB_WORKFLOW_REF": "o/r/.github/workflows/forecast-pipeline.yml@refs/heads/main"}


class Fetch:
    def __init__(self, runs=(), error=None):
        self.runs, self.error, self.calls = list(runs), error, []

    def __call__(self, api_url, token, repo, wf_file, since):
        self.calls.append((api_url, token, repo, wf_file, since))
        if self.error:
            raise self.error
        return self.runs


def test_run_asks_for_the_window_of_this_workflow_and_decides():
    fetch = Fetch([DISPATCHED_0815])
    assert G.run(ENV, at(13, 2), fetch) == (
        False, "skipped: run 100 already covers the full-pipeline slot of 2026-10-13 08:15 UTC "
               "(created since 08:10)")
    assert fetch.calls == [("https://api.example", "t0ken", "o/r", "forecast-pipeline.yml",
                            at(8, 10))]


def test_a_manual_run_does_not_call_the_api():
    fetch = Fetch([DISPATCHED_0815])
    env = dict(ENV, GITHUB_EVENT_NAME="workflow_dispatch", GATE_TRIGGER="manual")
    assert G.run(env, at(8, 20), fetch) == (True, "a manual run always goes ahead")
    assert fetch.calls == []


@pytest.mark.parametrize("change, fetch, why", [
    ({}, Fetch(error=urllib.error.URLError("down")), "URLError"),
    ({}, Fetch(error=urllib.error.HTTPError("u", 403, "Forbidden", None, None)), "HTTPError"),
    ({}, Fetch(error=TimeoutError("timed out")), "TimeoutError"),
    ({"DISPATCH_SLOTS": "8:15"}, Fetch(), "ValueError"),
    ({"DISPATCH_SLOTS": ""}, Fetch(), "ValueError"),
    ({"GATE_JOB": ""}, Fetch(), "ValueError"),
    ({"GITHUB_RUN_ID": None}, Fetch(), "KeyError"),
    ({"GITHUB_WORKFLOW_REF": "forecast-pipeline.yml"}, Fetch(), "ValueError"),
    ({}, Fetch([{"id": 1, "display_title": "full-pipeline (x)", "created_at": "yesterday",
                 "status": "completed", "conclusion": "success"}]), "ValueError"),
])
def test_every_failure_answers_run(change, fetch, why):
    env = {k: v for k, v in dict(ENV, **change).items() if v is not None}
    go, reason = G.run(env, at(13, 2), fetch)
    assert go is True
    assert reason.startswith(f"going ahead: the gate could not decide ({why}: "), reason


def test_a_job_the_gate_does_not_know_is_named_by_its_variable():
    go, reason = G.run(dict(ENV, GATE_JOB="tides"), at(13, 2), Fetch())
    assert (go, reason) == (True, "going ahead: the gate could not decide (ValueError: GATE_JOB "
                                  "'tides' is not one of full-pipeline, buoy-update)")


def test_workflow_file_reads_the_ref():
    assert G.workflow_file(
        "nunneryl/StormyPetrel/.github/workflows/forecast-pipeline.yml@refs/heads/main"
    ) == "forecast-pipeline.yml"
    assert G.workflow_file("o/r/.github/workflows/a.yaml@refs/pull/1/merge") == "a.yaml"
    for bad in ("", None, "o/r/.github/workflows/a.yml", "a.yml@refs/heads/main"):
        with pytest.raises(ValueError):
            G.workflow_file(bad)


def test_main_writes_the_answer_and_exits_zero(tmp_path, capsys, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setattr(G, "list_runs", Fetch([DISPATCHED_0815]))
    assert G.main(dict(ENV, GITHUB_OUTPUT=str(out)), at(13, 2)) == 0
    assert out.read_text() == (
        "run=false\nreason=skipped: run 100 already covers the full-pipeline slot of "
        "2026-10-13 08:15 UTC (created since 08:10)\n")
    assert capsys.readouterr().out == (
        "::notice title=dispatch gate::skipped: run 100 already covers the full-pipeline slot "
        "of 2026-10-13 08:15 UTC (created since 08:10)\n")


def test_main_on_an_error_answers_run_warns_and_exits_zero(tmp_path, capsys, monkeypatch):
    out = tmp_path / "out"
    monkeypatch.setattr(G, "list_runs", Fetch(error=urllib.error.URLError("down")))
    assert G.main(dict(ENV, GITHUB_OUTPUT=str(out)), at(13, 2)) == 0
    reason = "going ahead: the gate could not decide (URLError: <urlopen error down>)"
    assert out.read_text() == f"run=true\nreason={reason}\n"
    assert capsys.readouterr().out == f"{reason}\n::warning title=dispatch gate::{reason}\n"


def test_main_without_a_writable_output_still_exits_zero(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(G, "list_runs", Fetch([]))
    assert G.main(dict(ENV, GITHUB_OUTPUT=str(tmp_path / "no" / "such" / "dir")), at(13, 2)) == 0
    assert "::warning title=dispatch gate::could not write GITHUB_OUTPUT" in capsys.readouterr().out


# --------------------------------------------------------------------------- #
# 4 — the runs list over real HTTP                                             #
# --------------------------------------------------------------------------- #
class _Server:
    """Answers the runs list in pages of `page_size`, and records every request."""

    def __init__(self, runs, page_size=2, status=200):
        self.runs, self.page_size, self.status, self.seen = runs, page_size, status, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                u = urlsplit(self.path)
                q = dict(parse_qsl(u.query))
                outer.seen.append({"path": u.path, "query": q, "headers": dict(self.headers)})
                page = int(q.get("page", "1"))
                chunk = outer.runs[(page - 1) * outer.page_size: page * outer.page_size]
                body = json.dumps({"total_count": len(outer.runs),
                                   "workflow_runs": chunk}).encode()
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


@pytest.fixture
def no_proxy(monkeypatch):
    for k in list(os.environ):
        if "proxy" in k.lower():
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")


def test_list_runs_reads_every_page_with_the_right_query_and_headers(no_proxy):
    runs = [gh_run(i, "full-pipeline (external)", "2026-10-13T08:15:02Z") for i in (1, 2, 3, 4, 5)]
    s = _Server(runs, page_size=2)
    try:
        got = G.list_runs(s.url, "t0ken", "o/r", "forecast-pipeline.yml", at(8, 10))
    finally:
        s.close()
    assert [r["id"] for r in got] == [1, 2, 3, 4, 5]
    assert [r["path"] for r in s.seen] == ["/repos/o/r/actions/workflows/forecast-pipeline.yml/runs"] * 3
    assert [r["query"] for r in s.seen] == [
        {"created": ">=2026-10-13T08:10:00Z", "per_page": "100", "page": str(p)} for p in (1, 2, 3)]
    for r in s.seen:
        h = {k.lower(): v for k, v in r["headers"].items()}
        assert h["authorization"] == "Bearer t0ken"
        assert h["accept"] == "application/vnd.github+json"
        assert h["x-github-api-version"] == "2022-11-28"


def test_the_script_end_to_end_skips_a_covered_slot_and_fails_open_on_a_403(no_proxy, tmp_path):
    """Run as the gate step runs it: python3 scripts/dispatch_gate.py, environment only. The
    slot is today's latest, so the clock this test cannot set still lands in its window."""
    now = datetime.now(UTC)
    slot_h = now.hour
    created = now.replace(minute=0, second=1, microsecond=0)
    runs = [gh_run(41, "buoy-update (external)", created.strftime("%Y-%m-%dT%H:%M:%SZ"))]
    if now.minute >= 54:
        pytest.skip("within 6 minutes of the next hour the slot is about to move")
    for status, expected in [(200, "run=false\n"), (403, "run=true\n")]:
        s = _Server(runs, status=status)
        out = tmp_path / f"out{status}"
        env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
        env.update({k: v for k, v in ENV.items()})
        env.update({"NO_PROXY": "127.0.0.1,localhost", "GITHUB_API_URL": s.url,
                    "GATE_JOB": "buoy-update", "GITHUB_RUN_ID": "42",
                    "GITHUB_OUTPUT": str(out)})
        try:
            done = subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True,
                                  text=True, timeout=60)
        finally:
            s.close()
        assert done.returncode == 0, done.stdout + done.stderr
        assert out.read_text().startswith(expected), (status, out.read_text())
        assert f"{slot_h:02d}:00 UTC" in out.read_text() or status == 403


# --------------------------------------------------------------------------- #
# 5 — the workflow                                                             #
# --------------------------------------------------------------------------- #
JOB_EXPR = ("${{ (github.event.schedule == '0 * * * *' || inputs.job == 'buoy-update') "
            "&& 'buoy-update' || 'full-pipeline' }}")


@pytest.fixture(scope="module")
def wf():
    return yaml_code(WORKFLOW.read_text(encoding="utf-8"))


def _env_block(text):
    return dict(re.findall(r"^  ([A-Z_]+):[ \t]*(\S.*?)[ \t]*$", top_level(text, "env")[1], re.M))


def _crons(text):
    return re.findall(r"^    - cron: '([^']+)'[ \t]*$", top_level(text, "on")[1], re.M)


def test_the_slots_are_held_once_in_the_workflow(wf):
    assert _env_block(wf)["DISPATCH_SLOTS"] == "'01:45 08:15 13:45 20:15'"
    raw = WORKFLOW.read_text(encoding="utf-8")
    assert len(re.findall(r"^\s+DISPATCH_SLOTS:", raw, re.M)) == 1


def test_the_fallback_crons_sit_twenty_minutes_after_each_slot_and_buoys_stay_hourly(wf):
    assert _crons(wf) == ["5 2,14 * * *", "35 8,20 * * *", "0 * * * *"]
    # 01:45 + 20 min = 02:05, 08:15 -> 08:35, 13:45 -> 14:05, 20:15 -> 20:35, by hand:
    fallback = set()
    for cron in _crons(wf)[:2]:
        minute, hours = cron.split()[:2]
        fallback |= {(int(h), int(minute)) for h in hours.split(",")}
    assert fallback == {(2, 5), (8, 35), (14, 5), (20, 35)}
    slots = _env_block(wf)["DISPATCH_SLOTS"].strip("'").split()
    plus20 = set()
    for s in slots:
        h, m = map(int, s.split(":"))
        plus20.add(((h * 60 + m + 20) // 60 % 24, (m + 20) % 60))
    assert plus20 == fallback


def test_each_job_runs_on_its_own_crons_after_the_gate(wf):
    jobs = jobs_of(wf)
    gate_part = "!cancelled() && needs.gate.outputs.run != 'false' && ("
    full = jobs["full-pipeline"]
    buoy = jobs["buoy-update"]
    for text in (full, buoy):
        assert job_key(text, "needs")[0] == "gate"
        assert gate_part in text
    assert "github.event.schedule == '5 2,14 * * *'" in full
    assert "github.event.schedule == '35 8,20 * * *'" in full
    assert "'0 * * * *'" not in full and "inputs.job == 'full-pipeline'" in full
    assert "github.event.schedule == '0 * * * *'" in buoy and "inputs.job == 'buoy-update'" in buoy
    assert "2,14" not in buoy and "8,20" not in buoy


def test_the_concurrency_group_names_the_logical_job_not_the_trigger(wf):
    """The old group, `github.event.schedule || inputs.job`, put a scheduled full run and a
    dispatched one in different groups, so both could run at once. The run-name the gate reads
    and the job the gate is told must be the same expression, or the gate would compare one
    job's runs against another's."""
    group = dict(re.findall(r"^  ([\w-]+):[ \t]*(.*?)[ \t]*$", top_level(wf, "concurrency")[1], re.M))
    assert group == {"group": "${{ github.workflow }}-" + JOB_EXPR, "cancel-in-progress": "false"}
    assert "github.event.schedule ||" not in group["group"]
    assert top_level(wf, "run-name")[0] == (
        JOB_EXPR + " (${{ github.event_name == 'workflow_dispatch' && inputs.trigger "
        "|| github.event_name }})")
    gate_steps = steps_of(jobs_of(wf)["gate"])
    env = dict(re.findall(r"^ {10}([A-Z_]+):[ \t]*(\S.*?)[ \t]*$", gate_steps[1][1], re.M))
    assert env == {"GITHUB_TOKEN": "${{ github.token }}", "GATE_JOB": JOB_EXPR,
                   "GATE_TRIGGER": "${{ inputs.trigger }}"}


def test_the_trigger_input_defaults_to_manual(wf):
    on = top_level(wf, "on")[1]
    m = re.search(r"^      trigger:\n((?: {8}.*\n| {10}.*\n)+)", on, re.M)
    assert m, "no trigger input"
    assert re.findall(r"^ {8}([\w-]+):[ \t]*(.*?)[ \t]*$", m.group(1), re.M)[1:] == [
        ("required", "false"), ("default", "manual"), ("type", "choice"), ("options", "")]
    assert re.findall(r"^ {10}- (\S+)$", m.group(1), re.M) == ["manual", "external"]


def test_the_gate_job_reads_runs_and_fails_open(wf):
    gate = jobs_of(wf)["gate"]
    assert re.findall(r"^      (actions|contents): (\w+)", job_key(gate, "permissions")[1], re.M) == [
        ("actions", "read"), ("contents", "read")]
    assert job_key(gate, "outputs")[1].strip() == "run: ${{ steps.decide.outputs.run }}"
    assert job_key(gate, "timeout-minutes")[0] == "5"
    steps = [keys for keys, _ in steps_of(gate)]
    assert steps[0]["uses"].startswith("actions/checkout@")
    assert steps[1]["id"] == "decide" and steps[1]["continue-on-error"] == "true"
    assert steps[1]["run"] == "python3 scripts/dispatch_gate.py"
    assert len(steps) == 2


def test_the_failure_issue_job_needs_the_gate_and_both_jobs(wf):
    assert job_key(jobs_of(wf)["failure-issue"], "needs")[0] == "[gate, full-pipeline, buoy-update]"


PING = "Ping the dead-man's switch (healthchecks.io)"


def test_the_healthchecks_ping_follows_a_fully_successful_full_run_only(wf):
    full = [keys for keys, _ in steps_of(jobs_of(wf)["full-pipeline"])]
    names = [k.get("name") for k in full]
    finish = [k.get("run") for k in full].index("python -m pipeline.column_preflight --finish")
    assert names[finish + 1] == PING
    ping = full[finish + 1]
    assert ping["if"] == "success()" and ping["continue-on-error"] == "true"
    assert ping["timeout-minutes"] == "2"
    assert PING not in [k.get("name") for k, _ in steps_of(jobs_of(wf)["buoy-update"])]
    chunk = dict((k.get("name"), c) for k, c in steps_of(jobs_of(wf)["full-pipeline"]))[PING]
    assert "HEALTHCHECKS_URL: ${{ secrets.HEALTHCHECKS_FULL_PIPELINE_URL }}" in chunk


def _ping_script(wf):
    chunk = dict((k.get("name"), c) for k, c in steps_of(jobs_of(wf)["full-pipeline"]))[PING]
    body = re.search(r"^ {8}run: \|\n((?: {10}.*\n|\n)+)", chunk + "\n", re.M).group(1)
    return "\n".join(line[10:] for line in body.split("\n"))


def test_the_ping_does_nothing_without_the_secret_and_pings_with_it(wf, no_proxy):
    script = _ping_script(wf)
    env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
    env["NO_PROXY"] = "127.0.0.1,localhost"
    done = subprocess.run(["bash", "-e", "-c", script], env=dict(env, HEALTHCHECKS_URL=""),
                          capture_output=True, text=True, timeout=30)
    assert (done.returncode, done.stdout) == (
        0, "HEALTHCHECKS_FULL_PIPELINE_URL is not set: no ping sent.\n")
    s = _Server([])
    try:
        done = subprocess.run(["bash", "-e", "-c", script],
                              env=dict(env, HEALTHCHECKS_URL=s.url + "/ping/abc"),
                              capture_output=True, text=True, timeout=60)
    finally:
        s.close()
    assert (done.returncode, done.stdout) == (0, "Pinged healthchecks.io.\n"), done.stderr
    assert [r["path"] for r in s.seen] == ["/ping/abc"]


# --------------------------------------------------------------------------- #
# 6 — docs/scheduling.md                                                       #
# --------------------------------------------------------------------------- #
def test_the_doc_lists_the_workflows_slots_and_fallback_crons(wf):
    doc = DOC.read_text(encoding="utf-8")
    assert "DISPATCH_SLOTS: '01:45 08:15 13:45 20:15'" in doc
    for cron in _crons(wf):
        assert f"`{cron}`" in doc, cron
    for cron in ("45 1,13 * * *", "15 8,20 * * *", "0 * * * *"):
        assert f"`{cron}`" in doc, cron
    assert "Period: **7 hours**" in doc and "Grace: **1 hour**" in doc


def test_the_docs_request_bodies_are_the_dispatches_the_workflow_accepts():
    doc = DOC.read_text(encoding="utf-8")
    bodies = [json.loads(b) for b in re.findall(r"```json\n(.*?)\n```", doc, re.S)]
    assert bodies == [
        {"ref": "main", "inputs": {"job": "full-pipeline", "trigger": "external"}},
        {"ref": "main", "inputs": {"job": "full-pipeline", "trigger": "external"}},
        {"ref": "main", "inputs": {"job": "buoy-update", "trigger": "external"}},
    ]
    assert doc.count("https://api.github.com/repos/nunneryl/StormyPetrel/actions/workflows/"
                     "forecast-pipeline.yml/dispatches") >= 3


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
