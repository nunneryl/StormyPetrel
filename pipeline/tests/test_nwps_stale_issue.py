"""One 'nwps-stale' issue while any single NWPS office's newest cycle is 24 h old (scripts/nwps_stale_issue.py).

THE CASE IT IS FOR. The 2026-11-19 01:45Z full run logged "okx newest listed cycle is 26 h old":
NOMADS listed nothing newer than OKX's 18 Nov 00Z. One office is under the 5 the gate fails at,
so nothing but a log line said so. By 08:15Z OKX read 19 Nov 00Z again. HFO in the same days
read cycles 7.8 to 14.3 h old: under 24 h, so it would never have opened the issue.

The rows below are cut to the fields the script reads, from those runs' NWPSFRESH lines.
Every expected value is written by hand; none comes from calling the function under test.
"""
from __future__ import annotations

import importlib.util
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from pipeline.forecast import nwps

ROOT = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("nwps_stale_issue", ROOT / "scripts" / "nwps_stale_issue.py")
NS = importlib.util.module_from_spec(spec)
spec.loader.exec_module(NS)

REPO = "o/r"
NOW = datetime(2026, 11, 19, 2, 10, tzinfo=timezone.utc)
CTX = {"repo": REPO, "run_number": "901", "run_url": "https://github.com/o/r/actions/runs/1",
       "time": "2026-11-19 02:10 UTC"}

# 19 Nov 01:45Z run: OKX 26 h old; HFO read 18Z at 7.8 h; BOX one cycle behind but 14 h old.
RUN_0145 = [
    {"wfo": "okx", "cycle_used": "2026-11-18T00:00Z", "newest_listed": "2026-11-18T00:00Z",
     "age_h": 26.0, "hours_behind": 0.0},
    {"wfo": "hfo", "cycle_used": "2026-11-18T18:00Z", "newest_listed": "2026-11-18T18:00Z",
     "age_h": 7.8, "hours_behind": 0.0},
    {"wfo": "box", "cycle_used": "2026-11-18T12:00Z", "newest_listed": "2026-11-18T18:00Z",
     "age_h": 14.0, "hours_behind": 6.0},
]
# 19 Nov 08:15Z run: OKX fresh again.
RUN_0815 = [
    {"wfo": "okx", "cycle_used": "2026-11-19T00:00Z", "newest_listed": "2026-11-19T00:00Z",
     "age_h": 8.3, "hours_behind": 0.0},
    {"wfo": "hfo", "cycle_used": "2026-11-19T00:00Z", "newest_listed": "2026-11-19T00:00Z",
     "age_h": 8.3, "hours_behind": 0.0},
]


class FakeGitHub:
    def __init__(self, issues=(), labels=()):
        self.issues = {i["number"]: dict(i, comments=[]) for i in issues}
        self.labels = set(labels)
        self.calls = []

    def call(self, method, path, query=None, body=None):
        self.calls.append((method, path))
        base = f"/repos/{REPO}"
        if method == "GET" and path == f"{base}/issues":
            assert query == {"state": "open", "labels": "nwps-stale", "per_page": 100}
            return [{k: v for k, v in i.items() if k != "comments"} for i in self.issues.values()
                    if i["state"] == "open" and "nwps-stale" in i["labels"]]
        if method == "GET" and path == f"{base}/labels/nwps-stale":
            if "nwps-stale" not in self.labels:
                raise NS.ApiError(404, "Not Found")
            return {}
        if method == "POST" and path == f"{base}/labels":
            self.labels.add(body["name"])
            return {}
        if method == "POST" and path == f"{base}/issues":
            assert set(body["labels"]) <= self.labels, "label must exist first"
            n = max(self.issues, default=300) + 1
            self.issues[n] = {"number": n, "title": body["title"], "body": body["body"],
                              "labels": body["labels"], "state": "open", "comments": []}
            return {"number": n}
        m = re.fullmatch(rf"{base}/issues/(\d+)/comments", path)
        if method == "POST" and m:
            self.issues[int(m.group(1))]["comments"].append(body["body"])
            return {}
        m = re.fullmatch(rf"{base}/issues/(\d+)", path)
        if method == "PATCH" and m:
            self.issues[int(m.group(1))].update(body)
            return {}
        raise AssertionError(f"no {method} {path}")

    def writes(self):
        return [c for c in self.calls if c[0] != "GET"]


def test_the_threshold_is_the_pipelines():
    assert NS.STALE_AGE_H == nwps.NWPS_STALE_AGE_H == 24.0


def test_old_offices_counts_24h_or_more_and_no_cycle_but_not_a_skipped_cycle():
    rows = RUN_0145 + [
        {"wfo": "mfl", "cycle_used": "2026-11-18T02:00Z", "age_h": 24.0},   # exactly 24: counts
        {"wfo": "sju", "cycle_used": "2026-11-18T02:06Z", "age_h": 23.9},   # under: does not
        {"wfo": "lox", "cycle_used": None, "newest_listed": "2026-11-18T18:00Z", "age_h": None},
    ]
    assert NS.old_offices(rows) == [
        {"wfo": "lox", "why": "no cycle read (newest listed 2026-11-18T18:00Z)"},
        {"wfo": "mfl", "why": "cycle 2026-11-18T02:00Z is 24 h old"},
        {"wfo": "okx", "why": "cycle 2026-11-18T00:00Z is 26 h old"},
    ]
    assert NS.old_offices(RUN_0815) == []


def test_the_okx_day_opens_one_issue_then_closes_it():
    gh = FakeGitHub()
    assert NS.report(gh, CTX, NS.old_offices(RUN_0145)) == "nwps-stale: opened #301 for ['okx']"
    issue = gh.issues[301]
    assert issue["title"] == "NWPS stale for OKX"
    assert "- **OKX**: cycle 2026-11-18T00:00Z is 26 h old" in issue["body"]
    assert "HFO" not in issue["body"] and "BOX" not in issue["body"]
    assert issue["body"].endswith('<!-- nwps-stale {"offices": ["okx"]} -->')
    assert gh.labels == {"nwps-stale"}

    # The same office stale again at the next run: no comment, no write.
    gh.calls.clear()
    assert NS.report(gh, CTX, NS.old_offices(RUN_0145)) == "nwps-stale: nothing to do (stale: ['okx'])"
    assert gh.writes() == []

    # 08:15Z: OKX fresh again: one comment, then closed.
    assert NS.report(gh, CTX, NS.old_offices(RUN_0815)) == "nwps-stale: closed #301"
    assert issue["state"] == "closed" and issue["state_reason"] == "completed"
    assert issue["comments"] == [
        "[run 901](https://github.com/o/r/actions/runs/1) · 2026-11-19 02:10 UTC: every office "
        "read a cycle under 24 h old (OKX fresh again). Closing; the next stale office opens a "
        "new issue."]

    # Nothing open and nothing old: nothing at all.
    gh.calls.clear()
    assert NS.report(gh, CTX, []) == "nwps-stale: nothing to do (stale: none)"
    assert gh.writes() == []


def test_a_change_in_which_offices_are_old_is_one_comment():
    open_issue = {"number": 7, "title": "NWPS stale for OKX", "labels": ["nwps-stale"],
                  "state": "open", "body": 'x\n\n<!-- nwps-stale {"offices": ["okx"]} -->'}
    gh = FakeGitHub([open_issue], labels=["nwps-stale"])
    old = [{"wfo": "hfo", "why": "cycle 2026-11-18T06:00Z is 31 h old"},
           {"wfo": "okx", "why": "cycle 2026-11-18T00:00Z is 32 h old"}]
    assert NS.report(gh, CTX, old) == "nwps-stale: commented on #7; stale now ['hfo', 'okx']"
    assert gh.issues[7]["comments"] == [
        "[run 901](https://github.com/o/r/actions/runs/1) · 2026-11-19 02:10 UTC\n"
        "\nNow stale:\n- **HFO**: cycle 2026-11-18T06:00Z is 31 h old\n"
        "\nStill stale:\n- **OKX**: cycle 2026-11-18T00:00Z is 32 h old"]
    assert gh.issues[7]["body"] == 'x\n\n<!-- nwps-stale {"offices": ["hfo", "okx"]} -->'
    assert gh.issues[7]["state"] == "open"

    # OKX recovers, HFO stays: "Fresh again: OKX."
    NS.report(gh, CTX, [old[0]])
    assert gh.issues[7]["comments"][1] == (
        "[run 901](https://github.com/o/r/actions/runs/1) · 2026-11-19 02:10 UTC\n"
        "\nStill stale:\n- **HFO**: cycle 2026-11-18T06:00Z is 31 h old\n"
        "\nFresh again: OKX.")


def test_summarise_writes_both_outputs_and_never_fails(tmp_path, capsys):
    f = tmp_path / "fresh.json"
    f.write_text(json.dumps(RUN_0145))
    out = tmp_path / "gh_output"
    assert NS.main(["summarise", str(f)], env={"GITHUB_OUTPUT": str(out)}) == 0
    assert out.read_text() == (
        "nwps-checked=true\n"
        'nwps-old=[{"wfo":"okx","why":"cycle 2026-11-18T00:00Z is 26 h old"}]\n')
    # A missing file: checked=false, so the issue job leaves the issue as it is.
    out.unlink()
    assert NS.main(["summarise", str(tmp_path / "none.json")], env={"GITHUB_OUTPUT": str(out)}) == 0
    assert out.read_text() == "nwps-checked=false\nnwps-old=[]\n"


def test_report_does_nothing_when_the_run_did_not_check(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("no API call may be made")
    monkeypatch.setattr(NS, "GitHub", boom)
    assert NS.main(["report"], env={"NWPS_CHECKED": "false", "NWPS_OLD": "[]"}) == 0
    assert NS.main(["report"], env={}) == 0


def test_report_fails_on_a_bad_output_or_an_api_error(monkeypatch, capsys):
    env = {"NWPS_CHECKED": "true", "NWPS_OLD": '"okx"', "GITHUB_REPOSITORY": REPO,
           "GITHUB_RUN_ID": "1", "GITHUB_RUN_NUMBER": "901"}
    assert NS.main(["report"], env=env) == 1
    assert "::error title=nwps-stale-issue::NWPS_OLD is not a list of offices" in capsys.readouterr().out

    class Refusing:
        def __init__(self, *a):
            pass

        def call(self, *a, **k):
            raise NS.ApiError(403, "Resource not accessible by integration")
    monkeypatch.setattr(NS, "GitHub", Refusing)
    env["NWPS_OLD"] = '[{"wfo":"okx","why":"x"}]'
    assert NS.main(["report"], env=env, now=NOW) == 1
    assert "HTTP 403" in capsys.readouterr().out


def test_an_issue_without_the_marker_records_no_offices():
    assert NS.recorded("no marker here") is None
    assert NS.recorded('<!-- nwps-stale {"offices": "okx"} -->') is None
    assert NS.plan([{"wfo": "okx", "why": "x"}], {"body": "no marker"}) == "comment"
