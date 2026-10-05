"""One open failure issue per scheduled workflow, kept by .github/actions/failure-issue.

THE INCIDENT. forecast-pipeline opened a fresh issue for every failed run, titled with the run
number, and never looked for one already open. 54 were open on 2026-10-05, all stale. Nothing
else reported failures at all, and a job that hit its timeout was not reported either: it ends
"cancelled", and the old `if: failure()` step was skipped (run 32295001092, 2026-08-19).

WHAT IS PINNED HERE, against a fake of the GitHub REST API:
  1. the first failure opens one issue; every later failure is a comment on it, with the run
     link, the time and the failing step; the next success comments and closes it;
  2. a timed-out job counts as a failure, and its hung step is the one named;
  3. in a workflow of two jobs, one job succeeding does not close an issue about the other;
  4. an issue the old step opened is taken over by a failure and never closed by a success;
  5. a pull_request run, or a run where every job was skipped, writes nothing;
  6. the script itself, run as the action runs it, against a local HTTP server: what it sends,
     with which headers, and that a failed write fails the step.

THE FAKE answers as GitHub does where it matters here: the issues list returns pull requests
too, filters on every label given and lists newest first, and a missing label is a 404. It is
stricter in one place: creating an issue with a label that does not exist is refused, where
GitHub may create the label itself, so the step's own label check is what gets exercised.

THE JOB LIST in the timeout tests is the real one GitHub returned for run 32295001092, read
through the GitHub API on 2026-10-05, cut to the fields the step reads.

Every expected value is written by hand. None is obtained by calling the function under test.
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qsl, unquote, urlsplit

import pytest

ROOT = Path(__file__).resolve().parents[2]
ACTION_DIR = ROOT / ".github" / "actions" / "failure-issue"
SCRIPT = ACTION_DIR / "failure_issue.py"


def _load():
    spec = importlib.util.spec_from_file_location("failure_issue", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


FI = _load()

REPO = "o/r"
SERVER = "https://github.com"


# --------------------------------------------------------------------------- #
# The fake GitHub                                                              #
# --------------------------------------------------------------------------- #
class FakeGitHub:
    """Issues, comments and labels in memory, behind the one call() the step uses."""

    def __init__(self, issues=(), labels=("cron-failure",), jobs=(), jobs_error=None,
                 fail=None):
        self.issues = {i["number"]: dict(i, comments=[]) for i in issues}
        self.labels = set(labels)
        self.jobs = list(jobs)
        self.jobs_error = jobs_error        # (status, message) to answer the job list with
        self.fail = dict(fail or {})        # (method, path regex) -> status
        self.calls = []

    def writes(self):
        return [(m, p) for m, p, _q, _b in self.calls if m != "GET"]

    def open_issues(self):
        return sorted(n for n, i in self.issues.items()
                      if i["state"] == "open" and "pull_request" not in i)

    def call(self, method, path, query=None, body=None):
        self.calls.append((method, path, dict(query or {}), body))
        for (m, rx), status in self.fail.items():
            if m == method and re.fullmatch(rx, path):
                raise FI.ApiError(status, "injected")
        base = f"/repos/{REPO}"
        if method == "GET" and path == f"{base}/issues":
            want = set(query["labels"].split(","))
            hits = [i for i in self.issues.values()
                    if i["state"] == query["state"] and want <= set(i["labels"])]
            hits.sort(key=lambda i: -i["number"])
            return [self._public(i) for i in hits[: int(query["per_page"])]]
        if method == "POST" and path == f"{base}/issues":
            missing = [l for l in body["labels"] if l not in self.labels]
            if missing:
                raise FI.ApiError(422, f"no such label: {missing}")
            n = max(self.issues, default=200) + 1
            self.issues[n] = {"number": n, "title": body["title"], "body": body["body"],
                              "labels": list(body["labels"]), "state": "open", "comments": []}
            return self._public(self.issues[n])
        m = re.fullmatch(rf"{base}/issues/(\d+)", path)
        if method == "PATCH" and m:
            self.issues[int(m.group(1))].update(body)
            return self._public(self.issues[int(m.group(1))])
        m = re.fullmatch(rf"{base}/issues/(\d+)/comments", path)
        if method == "POST" and m:
            self.issues[int(m.group(1))]["comments"].append(body["body"])
            return {"id": 1}
        m = re.fullmatch(rf"{base}/labels/(.+)", path)
        if method == "GET" and m:
            name = unquote(m.group(1))
            if name not in self.labels:
                raise FI.ApiError(404, "Not Found")
            return {"name": name}
        if method == "POST" and path == f"{base}/labels":
            self.labels.add(body["name"])
            return {"name": body["name"]}
        if method == "GET" and re.fullmatch(rf"{base}/actions/runs/\d+/attempts/\d+/jobs", path):
            if self.jobs_error:
                raise FI.ApiError(*self.jobs_error)
            return {"total_count": len(self.jobs), "jobs": self.jobs}
        raise AssertionError(f"the fake has no {method} {path}")

    @staticmethod
    def _public(i):
        out = {k: v for k, v in i.items() if k != "comments"}
        out["labels"] = [{"name": l} for l in i["labels"]]
        return out


def ctx(run_number="2121", workflow="daily-report", event="schedule", time="2026-10-05 10:43"):
    run_id = "3729837" + run_number
    return {"repo": REPO, "workflow": workflow, "event": event, "run_id": run_id,
            "attempt": "1", "run_number": run_number,
            "run_url": f"{SERVER}/{REPO}/actions/runs/{run_id}", "time": time + " UTC"}


def daily_job(conclusion, failing_step=None):
    steps = [{"name": "Checkout", "conclusion": "success"},
             {"name": "Generate daily reports",
              "conclusion": "failure" if failing_step else "success"},
             {"name": "Revalidate ISR cache (home + /reports + today's date page)",
              "conclusion": "skipped" if failing_step else "success"}]
    return [{"name": "daily-report (Claude regional summaries)", "conclusion": conclusion,
             "html_url": f"{SERVER}/{REPO}/actions/runs/1/job/11", "steps": steps},
            {"name": "failure-issue", "conclusion": None, "steps": []}]


FAILED_DAILY = daily_job("failure", failing_step=True)

# The job list GitHub returned for run 32295001092: full-pipeline hit its 60-minute
# timeout inside apt-get and ended "cancelled"; buoy-update was skipped by its `if:`.
TIMEOUT_RUN_JOBS = [
    {"name": "full-pipeline (NWPS + WW3 + HRRR + buoys + tides)", "conclusion": "cancelled",
     "html_url": "https://github.com/nunneryl/StormyPetrel/actions/runs/32295001092/job/96204155381",
     "steps": [{"name": n, "conclusion": c} for n, c in [
         ("Set up job", "success"), ("Checkout", "success"),
         ("Set up Python 3.12", "success"),
         ("Install eccodes (cfgrib's GRIB-2 backend)", "cancelled"),
         ("Install Python dependencies", "skipped"), ("Restore geodata cache", "skipped"),
         ("Download geodata (only if cache missed)", "skipped"),
         ("Create runtime directories", "skipped"),
         ("Compute tide cache key (per UTC date)", "skipped"),
         ("Restore tide prediction cache", "skipped"),
         ("Fetch all forecast / observation sources", "skipped"),
         ("Save tide prediction cache", "success"), ("Interpret (compute ratings)", "skipped"),
         ("Snapshot pre-write spot ratings (for diff in revalidate step)", "skipped"),
         ("Push to Supabase (spots + forecasts + buoys + tides)", "skipped"),
         ("Revalidate ISR cache (home + map + regions + CHANGED spot pages only)", "skipped"),
         ("Open issue on failure", "skipped"), ("Post Set up Python 3.12", "skipped"),
         ("Post Checkout", "success"), ("Complete job", "success")]]},
    {"name": "buoy-update (hourly NDBC refresh)", "conclusion": "skipped",
     "html_url": "https://github.com/nunneryl/StormyPetrel/actions/runs/32295001092/job/96204156674"},
]

# The body issue #228 was opened with, by the old step.
LEGACY_BODY = ("The scheduled forecast pipeline failed at `2026-09-23T19:11:06.890Z`.\n\n"
               "**Run logs:** https://github.com/nunneryl/StormyPetrel/actions/runs/35904511294\n\n"
               "Common causes:\n"
               "- NOMADS rate-limit / missing cycle (will self-resolve next run)\n"
               "- Supabase TLS hiccup mid-upsert (re-run is idempotent)\n"
               "- New cfgrib variable name in a GRIB (check the first-step diagnostic in the log)")


def legacy(n, run):
    return {"number": n, "title": f"[cron] full-pipeline failed (run {run})", "body": LEGACY_BODY,
            "labels": ["cron-failure", "forecast-pipeline"], "state": "open"}


def needs(**results):
    return {job.replace("_", "-"): {"result": r, "outputs": {}} for job, r in results.items()}


# --------------------------------------------------------------------------- #
# 1 — one issue: open, comment, close                                          #
# --------------------------------------------------------------------------- #
def test_the_first_failure_opens_one_issue_with_the_run_time_and_failing_step():
    gh = FakeGitHub(jobs=FAILED_DAILY)
    said = FI.report(gh, ctx(), needs(daily_report="failure"))
    assert said == "daily-report: failed ['daily-report']; opened #201"
    assert gh.open_issues() == [201]
    issue = gh.issues[201]
    assert issue["title"] == "[cron] daily-report failing"
    assert issue["labels"] == ["cron-failure", "daily-report"]
    assert issue["body"] == (
        "**daily-report** failed. This is its one open failure issue: each further failure is "
        "added here as a comment instead of a new issue, and the issue closes itself, with a "
        "comment, once every failing job has succeeded again.\n"
        "\n"
        "❌ **Failed** — [run 2121](https://github.com/o/r/actions/runs/37298372121) · "
        "2026-10-05 10:43 UTC · scheduled run\n"
        "- job `daily-report` failed\n"
        "- failing step: **Generate daily reports** in "
        "[daily-report (Claude regional summaries)](https://github.com/o/r/actions/runs/1/job/11)\n"
        "\n"
        "<sub>Kept by .github/actions/failure-issue.</sub>\n"
        "\n"
        '<!-- failure-issue {"failing": ["daily-report"]} -->')
    # it looked for an open issue before opening one, and made the workflow's label
    assert gh.calls[0] == ("GET", "/repos/o/r/issues",
                           {"state": "open", "labels": "cron-failure,daily-report",
                            "per_page": 100}, None)
    assert gh.writes() == [("POST", "/repos/o/r/labels"), ("POST", "/repos/o/r/issues")]
    assert gh.labels == {"cron-failure", "daily-report"}


def test_a_later_failure_is_a_comment_on_that_issue_not_a_second_issue():
    gh = FakeGitHub(jobs=FAILED_DAILY)
    FI.report(gh, ctx("2121"), needs(daily_report="failure"))
    gh.calls.clear()
    said = FI.report(gh, ctx("2122", time="2026-10-06 11:02"), needs(daily_report="failure"))
    assert said == "daily-report: failed ['daily-report']; commented on #201; failing now ['daily-report']"
    assert gh.open_issues() == [201]
    assert gh.issues[201]["comments"] == [
        "❌ **Failed** — [run 2122](https://github.com/o/r/actions/runs/37298372122) · "
        "2026-10-06 11:02 UTC · scheduled run\n"
        "- job `daily-report` failed\n"
        "- failing step: **Generate daily reports** in "
        "[daily-report (Claude regional summaries)](https://github.com/o/r/actions/runs/1/job/11)\n"
        "\n"
        "Failing now: `daily-report`."]
    # nothing changed in what is failing, so the body is left alone
    assert gh.writes() == [("POST", "/repos/o/r/issues/201/comments")]


def test_the_next_success_comments_and_closes_it_and_the_one_after_says_nothing():
    gh = FakeGitHub(jobs=FAILED_DAILY)
    FI.report(gh, ctx("2121"), needs(daily_report="failure"))
    FI.report(gh, ctx("2122"), needs(daily_report="failure"))
    gh.calls.clear()
    said = FI.report(gh, ctx("2123", time="2026-10-07 11:05"), needs(daily_report="success"))
    assert said == "daily-report: succeeded ['daily-report']; closed #201"
    assert gh.open_issues() == []
    issue = gh.issues[201]
    assert issue["state"] == "closed" and issue["state_reason"] == "completed"
    assert issue["comments"][-1] == (
        "✅ **Recovered** — [run 2123](https://github.com/o/r/actions/runs/37298372123) · "
        "2026-10-07 11:05 UTC · scheduled run: `daily-report` succeeded, and no job of "
        "**daily-report** is failing any more. Closing; the next failure opens a new issue.")
    assert issue["body"].endswith('<!-- failure-issue {"failing": []} -->')
    assert gh.writes() == [("POST", "/repos/o/r/issues/201/comments"),
                           ("PATCH", "/repos/o/r/issues/201")]

    gh.calls.clear()
    said = FI.report(gh, ctx("2124"), needs(daily_report="success"))
    assert said == "daily-report: nothing to report (failed none, succeeded ['daily-report'])"
    assert gh.writes() == []


def test_after_a_close_the_next_failure_opens_a_new_issue_and_only_one_is_ever_open():
    gh = FakeGitHub(jobs=FAILED_DAILY)
    for run, result in [("1", "failure"), ("2", "failure"), ("3", "success"), ("4", "failure"),
                        ("5", "failure"), ("6", "failure")]:
        FI.report(gh, ctx(run), needs(daily_report=result))
        assert len(gh.open_issues()) <= 1
    assert gh.open_issues() == [202]
    assert gh.issues[201]["state"] == "closed"
    assert len(gh.issues[201]["comments"]) == 2      # run 2's failure, run 3's recovery
    assert len(gh.issues[202]["comments"]) == 2      # runs 5 and 6


def test_a_label_that_already_exists_is_not_created_again():
    gh = FakeGitHub(labels=("cron-failure", "daily-report"), jobs=FAILED_DAILY)
    FI.report(gh, ctx(), needs(daily_report="failure"))
    assert gh.writes() == [("POST", "/repos/o/r/issues")]


# --------------------------------------------------------------------------- #
# 2 — a timeout is a failure, and its hung step is named                       #
# --------------------------------------------------------------------------- #
def test_a_timed_out_job_opens_the_issue_and_names_the_step_that_hung():
    gh = FakeGitHub(jobs=TIMEOUT_RUN_JOBS)
    c = ctx("1565", workflow="forecast-pipeline", time="2026-08-19 20:48")
    FI.report(gh, c, needs(full_pipeline="cancelled", buoy_update="skipped"))
    body = gh.issues[201]["body"]
    assert ("- job `full-pipeline` was cancelled (it timed out, or was stopped by hand)\n"
            "- failing step: **Install eccodes (cfgrib's GRIB-2 backend)** in "
            "[full-pipeline (NWPS + WW3 + HRRR + buoys + tides)]"
            "(https://github.com/nunneryl/StormyPetrel/actions/runs/32295001092/job/96204155381)\n"
            ) in body
    assert "buoy-update" not in body, "a skipped job is not a failure"
    assert body.endswith('<!-- failure-issue {"failing": ["full-pipeline"]} -->')


def test_every_failing_step_is_listed_the_first_named_as_the_failure():
    jobs = [{"name": "log NWPS publication times (read-only)", "conclusion": "failure",
             "html_url": "u", "steps": [
                 {"name": "Checkout", "conclusion": "success"},
                 {"name": "Log publication times, every office (writes nothing)",
                  "conclusion": "failure"},
                 {"name": "Upload", "conclusion": "cancelled"},
                 {"name": "Tidy", "conclusion": "timed_out"}]}]
    lines = FI.failure_lines(["log"], needs(log="failure"), jobs)
    assert lines == ["- job `log` failed",
                     "- failing step: **Log publication times, every office (writes nothing)** "
                     "in [log NWPS publication times (read-only)](u) (then Upload, Tidy)"]


def test_a_failed_job_with_no_failed_step_says_so_and_points_at_the_log():
    jobs = [{"name": "resolve-cams (YouTube live IDs)", "conclusion": "failure", "steps": [
        {"name": "Set up job", "conclusion": "success"}]}]
    assert FI.failure_lines(["resolve-cams"], needs(resolve_cams="failure"), jobs) == [
        "- job `resolve-cams` failed",
        "- failing step: none recorded in resolve-cams (YouTube live IDs); see its log"]
    assert FI.failure_lines(["resolve-cams"], needs(resolve_cams="failure"), []) == [
        "- job `resolve-cams` failed",
        "- failing step: no failed job in this run's job list; see the run"]


def test_an_unreadable_job_list_costs_the_step_name_and_nothing_else():
    gh = FakeGitHub(jobs_error=(403, "Resource not accessible by integration"))
    FI.report(gh, ctx(), needs(daily_report="failure"))
    assert gh.open_issues() == [201]
    assert ("- failing step: not available (the run's job list could not be read: "
            "HTTP 403: Resource not accessible by integration)") in gh.issues[201]["body"]


def test_a_failure_note_is_quoted_under_its_job():
    """forecast-pipeline's jobs hand the column check's line over as their failure-note
    output; the old step put that line in the issue, and so does this one."""
    n = {"full-pipeline": {"result": "failure", "outputs": {
            "failure-note": "Missing database column: forecasts.nwps_cycle "
                            "(pipeline/migrations/019_nwps_cycle.sql)\n  left out"}},
         "buoy-update": {"result": "skipped", "outputs": {}}}
    assert FI.failure_lines(["full-pipeline"], n, [])[:2] == [
        "- job `full-pipeline` failed",
        "  > Missing database column: forecasts.nwps_cycle "
        "(pipeline/migrations/019_nwps_cycle.sql) left out"]


# --------------------------------------------------------------------------- #
# 3 — two jobs in one workflow                                                 #
# --------------------------------------------------------------------------- #
def test_the_hourly_job_succeeding_does_not_close_an_issue_about_the_other():
    """forecast-pipeline runs full-pipeline three times a day and buoy-update every hour, one
    job per run with the other skipped. A buoy-update success says nothing about a failed
    full-pipeline, and must not close its issue an hour later."""
    gh = FakeGitHub(jobs=TIMEOUT_RUN_JOBS)
    fp = "forecast-pipeline"
    FI.report(gh, ctx("1", workflow=fp), needs(full_pipeline="failure", buoy_update="skipped"))
    gh.calls.clear()
    said = FI.report(gh, ctx("2", workflow=fp), needs(full_pipeline="skipped", buoy_update="success"))
    assert said == "forecast-pipeline: nothing to report (failed none, succeeded ['buoy-update'])"
    assert gh.writes() == [] and gh.open_issues() == [201]

    FI.report(gh, ctx("3", workflow=fp), needs(full_pipeline="skipped", buoy_update="failure"))
    assert gh.issues[201]["comments"][-1].endswith("Failing now: `buoy-update`, `full-pipeline`.")
    assert gh.issues[201]["body"].endswith(
        '<!-- failure-issue {"failing": ["buoy-update", "full-pipeline"]} -->')

    gh.calls.clear()
    said = FI.report(gh, ctx("4", workflow=fp, time="2026-10-05 12:00"),
                     needs(full_pipeline="skipped", buoy_update="success"))
    assert said == "forecast-pipeline: ['buoy-update'] recovered on #201; still failing ['full-pipeline']"
    assert gh.issues[201]["comments"][-1] == (
        "✅ **`buoy-update` succeeded** — [run 4](https://github.com/o/r/actions/runs/37298374) "
        "· 2026-10-05 12:00 UTC · scheduled run. Still failing: `full-pipeline`.")
    assert gh.issues[201]["body"].endswith('<!-- failure-issue {"failing": ["full-pipeline"]} -->')
    assert gh.open_issues() == [201]

    FI.report(gh, ctx("5", workflow=fp), needs(full_pipeline="skipped", buoy_update="success"))
    assert len(gh.issues[201]["comments"]) == 2, "an already-recovered job is not news"

    said = FI.report(gh, ctx("6", workflow=fp), needs(full_pipeline="success", buoy_update="skipped"))
    assert said == "forecast-pipeline: succeeded ['full-pipeline']; closed #201"
    assert gh.open_issues() == []


def test_a_run_whose_jobs_were_all_skipped_writes_nothing():
    gh = FakeGitHub(issues=[{"number": 230, "title": "t", "labels": ["cron-failure", "w"],
                             "state": "open", "body": '<!-- failure-issue {"failing": ["a"]} -->'}])
    said = FI.report(gh, ctx(workflow="w"), needs(a="skipped", b="skipped"))
    assert said == "w: nothing to report (failed none, succeeded none)"
    assert gh.writes() == []


# --------------------------------------------------------------------------- #
# 4 — the old step's issues                                                    #
# --------------------------------------------------------------------------- #
def test_a_success_never_closes_an_issue_the_old_step_opened():
    """Those are left for a person to close after reading them, with the command in the PR."""
    gh = FakeGitHub(issues=[legacy(112, 1113), legacy(205, 1587), legacy(228, 2023)])
    said = FI.report(gh, ctx("2121", workflow="forecast-pipeline"),
                     needs(full_pipeline="success", buoy_update="skipped"))
    assert said == "forecast-pipeline: nothing to report (failed none, succeeded ['full-pipeline'])"
    assert gh.writes() == [] and gh.open_issues() == [112, 205, 228]


def test_a_failure_comments_on_the_newest_old_issue_instead_of_opening_another():
    gh = FakeGitHub(issues=[legacy(112, 1113), legacy(205, 1587), legacy(228, 2023)],
                    jobs=TIMEOUT_RUN_JOBS)
    said = FI.report(gh, ctx("2122", workflow="forecast-pipeline"),
                     needs(full_pipeline="failure", buoy_update="skipped"))
    assert said == ("forecast-pipeline: failed ['full-pipeline']; commented on #228; "
                    "failing now ['full-pipeline']")
    assert gh.open_issues() == [112, 205, 228], "no new issue"
    assert gh.issues[228]["body"] == (LEGACY_BODY + "\n\n"
                                      '<!-- failure-issue {"failing": ["full-pipeline"]} -->')
    assert gh.issues[205]["body"] == LEGACY_BODY and gh.issues[205]["comments"] == []
    # from then on it is the workflow's issue, and the next success closes it — that one only
    FI.report(gh, ctx("2123", workflow="forecast-pipeline"),
              needs(full_pipeline="success", buoy_update="skipped"))
    assert gh.open_issues() == [112, 205]


def test_an_issue_with_the_marker_is_preferred_to_a_newer_one_without():
    gh = FakeGitHub(issues=[
        {"number": 230, "title": "[cron] daily-report failing", "state": "open",
         "labels": ["cron-failure", "daily-report"],
         "body": 'x\n\n<!-- failure-issue {"failing": ["daily-report"]} -->'},
        {"number": 231, "title": "opened by hand", "state": "open",
         "labels": ["cron-failure", "daily-report"], "body": "no marker"}], jobs=FAILED_DAILY)
    FI.report(gh, ctx(), needs(daily_report="failure"))
    assert len(gh.issues[230]["comments"]) == 1 and gh.issues[231]["comments"] == []


def test_a_pull_request_is_never_taken_for_the_issue():
    """The issues endpoint lists pull requests too."""
    gh = FakeGitHub(issues=[{"number": 240, "title": "a PR", "state": "open",
                             "labels": ["cron-failure", "daily-report"], "pull_request": {},
                             "body": '<!-- failure-issue {"failing": ["daily-report"]} -->'}],
                    jobs=FAILED_DAILY)
    FI.report(gh, ctx(), needs(daily_report="failure"))
    assert gh.open_issues() == [241] and gh.issues[240]["comments"] == []


def test_issues_of_another_workflow_are_not_touched():
    gh = FakeGitHub(issues=[{"number": 230, "title": "t", "state": "open",
                             "labels": ["cron-failure", "resolve-cams"],
                             "body": '<!-- failure-issue {"failing": ["resolve-cams"]} -->'}],
                    jobs=FAILED_DAILY)
    FI.report(gh, ctx(), needs(daily_report="failure"))
    assert gh.open_issues() == [230, 231] and gh.issues[230]["comments"] == []


# --------------------------------------------------------------------------- #
# 5 — what is not reported, and what is refused                                #
# --------------------------------------------------------------------------- #
def test_a_pull_request_run_touches_no_issue():
    gh = FakeGitHub(jobs=FAILED_DAILY)
    said = FI.report(gh, ctx(workflow="nwps-publication-log", event="pull_request"),
                     needs(log="failure"))
    assert said == "nwps-publication-log: a pull_request run is not reported"
    assert gh.calls == []


def test_a_manual_run_is_reported_and_says_so():
    gh = FakeGitHub(jobs=FAILED_DAILY)
    FI.report(gh, ctx(event="workflow_dispatch"), needs(daily_report="failure"))
    assert "· 2026-10-05 10:43 UTC · manual run\n" in gh.issues[201]["body"]


def test_a_reporting_job_that_needs_nothing_is_refused():
    for bad in ({}, [], None):
        with pytest.raises(ValueError, match="must list every other job"):
            FI.report(FakeGitHub(), ctx(), bad)


def test_a_workflow_name_that_cannot_be_a_label_is_refused():
    """The issues list filters on labels joined with commas."""
    with pytest.raises(ValueError, match="cannot be used as a label"):
        FI.report(FakeGitHub(), ctx(workflow="a,b"), needs(x="failure"))


def test_a_failed_write_is_an_error_not_a_quiet_pass():
    gh = FakeGitHub(jobs=FAILED_DAILY, fail={("POST", r"/repos/o/r/issues"): 500})
    with pytest.raises(FI.ApiError, match="HTTP 500"):
        FI.report(gh, ctx(), needs(daily_report="failure"))


# --------------------------------------------------------------------------- #
# The pieces, on hand-written input                                            #
# --------------------------------------------------------------------------- #
def test_job_results_counts_cancelled_as_failed_and_skipped_as_neither():
    n = {"a": {"result": "failure"}, "b": {"result": "cancelled"}, "c": {"result": "success"},
         "d": {"result": "skipped"}, "e": {"result": "something new"}, "f": None}
    assert FI.job_results(n) == (["a", "b"], ["c"])


def test_the_marker_is_read_written_and_replaced_in_place():
    assert FI.recorded_failing("text\n<!-- failure-issue {\"failing\": [\"b\", \"a\"]} -->") == ["a", "b"]
    assert FI.recorded_failing('<!-- failure-issue {"failing": []} -->') == []
    for no in (None, "", "no marker", "<!-- failure-issue {broken} -->",
               '<!-- failure-issue {"failing": "a"} -->', "<!-- failure-issue [1] -->"):
        assert FI.recorded_failing(no) is None, no
    assert FI.with_marker("text", ["b", "a"]) == 'text\n\n<!-- failure-issue {"failing": ["a", "b"]} -->'
    assert FI.with_marker("text\n\n", []) == 'text\n\n<!-- failure-issue {"failing": []} -->'
    assert FI.with_marker('a\n<!-- failure-issue {"failing": ["x"]} -->\nb', ["y"]) == (
        'a\n<!-- failure-issue {"failing": ["y"]} -->\nb')


def test_choose_takes_the_newest_with_a_marker_and_the_newest_without():
    m = '<!-- failure-issue {"failing": []} -->'
    issues = [{"number": 5, "body": m}, {"number": 9, "body": m}, {"number": 7, "body": "x"},
              {"number": 3, "body": None}, {"number": 11, "body": m, "pull_request": {}}]
    marked, unmarked = FI.choose(issues)
    assert (marked["number"], unmarked["number"]) == (9, 7)
    assert FI.choose([]) == (None, None)


@pytest.mark.parametrize("marked, unmarked, failed, succeeded, expected", [
    (None, None, ["a"], [], ("open", None, ["a"])),
    ("M:a", None, ["a"], [], ("comment", "M:a", ["a"])),
    ("M:", None, ["b"], [], ("comment", "M:", ["b"])),
    ("M:a", "U", ["b"], [], ("comment", "M:a", ["a", "b"])),
    (None, "U", ["b"], [], ("comment", "U", ["b"])),
    ("M:a,b", None, ["b"], ["a"], ("comment", "M:a,b", ["b"])),
    ("M:a,b", None, [], ["a"], ("recovered", "M:a,b", ["b"])),
    ("M:a", None, [], ["a"], ("close", "M:a", [])),
    ("M:", None, [], ["a"], ("close", "M:", [])),
    ("M:a", None, [], ["b"], ("none", None, None)),
    (None, "U", [], ["a"], ("none", None, None)),
    ("M:a", None, [], [], ("none", None, None)),
])
def test_plan(marked, unmarked, failed, succeeded, expected):
    def issue(spec):
        if spec is None:
            return None
        if spec == "U":
            return {"number": 1, "body": "old", "tag": spec}
        jobs = [j for j in spec[2:].split(",") if j]
        return {"number": 2, "body": '<!-- failure-issue {"failing": %s} -->' % json.dumps(jobs),
                "tag": spec}
    action, chosen, after = FI.plan(issue(marked), issue(unmarked), failed, succeeded)
    assert (action, chosen and chosen["tag"], after) == expected


def test_context_from_the_runner_environment():
    env = {"GITHUB_REPOSITORY": "nunneryl/StormyPetrel", "GITHUB_RUN_ID": "32295001092",
           "GITHUB_RUN_NUMBER": "1565", "GITHUB_RUN_ATTEMPT": "1",
           "GITHUB_WORKFLOW": "forecast-pipeline", "GITHUB_EVENT_NAME": "schedule",
           "GITHUB_SERVER_URL": "https://github.com"}
    now = datetime(2026, 8, 19, 20, 48, 17, tzinfo=timezone.utc)
    assert FI.context(env, now) == {
        "repo": "nunneryl/StormyPetrel", "workflow": "forecast-pipeline", "event": "schedule",
        "run_id": "32295001092", "attempt": "1", "run_number": "1565",
        "run_url": "https://github.com/nunneryl/StormyPetrel/actions/runs/32295001092",
        "time": "2026-08-19 20:48 UTC"}
    again = FI.context(dict(env, GITHUB_RUN_ATTEMPT="2"), now)
    assert again["run_url"] == ("https://github.com/nunneryl/StormyPetrel/actions/runs/"
                                "32295001092/attempts/2")
    with pytest.raises(ValueError, match="GITHUB_RUN_ID"):
        FI.context({k: v for k, v in env.items() if k != "GITHUB_RUN_ID"}, now)


# --------------------------------------------------------------------------- #
# 6 — the script, run as the action runs it, against a local HTTP server      #
# --------------------------------------------------------------------------- #
class _Server:
    """FakeGitHub behind real HTTP on 127.0.0.1, recording each request as it arrived."""

    def __init__(self, fake):
        self.fake, self.seen = fake, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def _serve(self):
                parts = urlsplit(self.path)
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n)) if n else None
                query = dict(parse_qsl(parts.query))
                outer.seen.append({"method": self.command, "path": parts.path,
                                   "query": dict(query), "body": body,
                                   "headers": dict(self.headers)})
                if "per_page" in query:
                    query["per_page"] = int(query["per_page"])
                try:
                    status, out = 200, outer.fake.call(self.command, parts.path, query, body)
                except FI.ApiError as e:
                    status, out = e.status, {"message": str(e)}
                raw = json.dumps(out).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            do_GET = do_POST = do_PATCH = _serve

            def log_message(self, *a):
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d" % self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _action_env():
    """The variables action.yml hands the script, by name, read out of action.yml."""
    text = (ACTION_DIR / "action.yml").read_text(encoding="utf-8")
    return dict(re.findall(r"^ {8}([A-Z_]+):\s*(\S.*?)\s*$", text, re.M))


def _run(server, event="schedule", results=None, run="2121", raw_needs=None):
    env = {k: v for k, v in os.environ.items() if "proxy" not in k.lower()}
    env.update({
        "NO_PROXY": "127.0.0.1,localhost",
        "GITHUB_API_URL": server.url, "GITHUB_SERVER_URL": "https://github.com",
        "GITHUB_REPOSITORY": REPO, "GITHUB_RUN_ID": "3729837" + run, "GITHUB_RUN_NUMBER": run,
        "GITHUB_RUN_ATTEMPT": "1", "GITHUB_WORKFLOW": "resolve-cams", "GITHUB_EVENT_NAME": event,
    })
    names = _action_env()
    assert names == {"FAILURE_ISSUE_NEEDS": "${{ inputs.needs }}",
                     "GITHUB_TOKEN": "${{ inputs.token }}"}, names
    env["FAILURE_ISSUE_NEEDS"] = raw_needs if raw_needs is not None else json.dumps(
        {"resolve-cams": {"result": results or "failure", "outputs": {}}})
    env["GITHUB_TOKEN"] = "t0ken"
    return subprocess.run([sys.executable, str(SCRIPT)], env=env, capture_output=True,
                          text=True, timeout=60)


@pytest.fixture
def server():
    jobs = [{"name": "resolve-cams (YouTube live IDs)", "conclusion": "failure", "html_url": "h",
             "steps": [{"name": "Resolve cam embeds", "conclusion": "failure"}]}]
    s = _Server(FakeGitHub(jobs=jobs))
    yield s
    s.close()


def test_the_script_opens_then_closes_over_real_http(server):
    done = _run(server)
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout == "resolve-cams: failed ['resolve-cams']; opened #201\n"
    first = server.seen[0]
    assert (first["method"], first["path"], first["query"]) == (
        "GET", "/repos/o/r/issues",
        {"state": "open", "labels": "cron-failure,resolve-cams", "per_page": "100"})
    for r in server.seen:
        h = {k.lower(): v for k, v in r["headers"].items()}
        assert h["authorization"] == "Bearer t0ken"
        assert h["accept"] == "application/vnd.github+json"
        assert h["x-github-api-version"] == "2022-11-28"
    assert [(r["method"], r["path"]) for r in server.seen] == [
        ("GET", "/repos/o/r/issues"),
        ("GET", "/repos/o/r/actions/runs/37298372121/attempts/1/jobs"),
        ("GET", "/repos/o/r/labels/cron-failure"),
        ("GET", "/repos/o/r/labels/resolve-cams"),
        ("POST", "/repos/o/r/labels"),
        ("POST", "/repos/o/r/issues")]
    assert server.seen[-1]["body"]["title"] == "[cron] resolve-cams failing"
    assert "- failing step: **Resolve cam embeds** in [resolve-cams (YouTube live IDs)](h)" in (
        server.seen[-1]["body"]["body"])

    done = _run(server, results="success", run="2122")
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout == "resolve-cams: succeeded ['resolve-cams']; closed #201\n"
    assert server.seen[-1]["method"] == "PATCH" and server.seen[-1]["body"]["state"] == "closed"


def test_the_script_sends_nothing_for_a_pull_request_run(server):
    done = _run(server, event="pull_request")
    assert done.returncode == 0, done.stdout + done.stderr
    assert done.stdout == "resolve-cams: a pull_request run is not reported\n"
    assert server.seen == []


def test_the_script_fails_the_step_when_a_write_fails(server):
    server.fake.fail[("POST", r"/repos/o/r/issues")] = 500
    done = _run(server)
    assert done.returncode == 1
    assert done.stdout.startswith("::error title=failure-issue::HTTP 500:"), done.stdout


def test_the_script_fails_the_step_on_needs_it_cannot_read(server):
    done = _run(server, raw_needs="not json")
    assert done.returncode == 1 and done.stdout.startswith("::error title=failure-issue::")
    assert server.seen == []


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
