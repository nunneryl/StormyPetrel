"""Keep at most one open failure issue per scheduled workflow.

WHAT HAPPENED. forecast-pipeline's full-pipeline job opened a new issue every time it failed,
titled with the run number, so no two titles were ever the same, and nothing looked for an
issue already open. On 2026-10-05 54 of them were open, every one stale, and a real failure
would have been the 55th. Nothing else reported at all: not the hourly buoy-update job, not
the seven other scheduled workflows, and not a job that hit its timeout-minutes. A timeout
ends a job "cancelled", and an `if: failure()` step does not run on a cancelled job — run
32295001092 (2026-08-19) spent its 60 minutes inside apt-get, and its issue step was skipped.

HOW IT RUNS. Every scheduled workflow ends with a failure-issue job that `needs` every other
job and runs `if: always()`, so it reports whatever happened, a timeout included. That job
runs .github/actions/failure-issue, which runs this file with the needs context as JSON. It
is the only job holding issues: write; the jobs that do the work hold no write scope on issues.

WHAT IT DOES. A workflow's issue is the open one carrying two labels, cron-failure and the
workflow's name.
  a job failed or was cancelled  comment on that issue (run link, time, failing step), or open
                                 it if there is none
  a job succeeded                when no job of the workflow is failing any more, comment and
                                 close the issue; while another job still fails, record that
                                 this one recovered
  a job was skipped              nothing. forecast-pipeline skips one of its two jobs on every
                                 run, and a skip says nothing about either.
Which jobs are failing is kept in the issue body, in a hidden marker, so the hourly buoy-update
succeeding cannot close an issue about a failed full-pipeline.

AN ISSUE IT DID NOT OPEN. An open issue without the marker (the old step's) is taken over by a
failure: the failure is commented there rather than opening another, and the marker is added.
A success never closes an issue without the marker, so the old ones are left for a person.

Only schedule and workflow_dispatch runs are reported. A pull_request run, such as
nwps-publication-log's smoke check of its own PRs, touches no issue.

Standard library only, so the job needs no setup-python: the runner image's python3 runs it.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

LABEL = "cron-failure"
REPORTED_EVENTS = ("schedule", "workflow_dispatch")
FAILED = ("failure", "cancelled")            # a job that hits timeout-minutes ends "cancelled"
SUCCEEDED = ("success",)
STEP_FAILED = ("failure", "cancelled", "timed_out")
NOTE_OUTPUT = "failure-note"                 # a job output quoted under the failed job
MARKER_RE = re.compile(r"<!-- failure-issue (\{.*?\}) -->")
EVENT_WORDS = {"schedule": "scheduled run", "workflow_dispatch": "manual run"}


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class GitHub:
    """The REST calls this needs, and nothing else. Tests hand report() a fake with the same
    call() instead, so every decision below is exercised without a network."""

    def __init__(self, api_url, token, timeout=30):
        self.api_url = api_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def call(self, method, path, query=None, body=None):
        url = self.api_url + path + ("?" + urllib.parse.urlencode(query) if query else "")
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(url, data=data, method=method, headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {self.token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "StormyPetrel-failure-issue",
            **({"Content-Type": "application/json"} if data is not None else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, e.read().decode("utf-8", "replace")[:300]) from None
        return json.loads(raw) if raw else None


# --------------------------------------------------------------------------- #
# Decisions. Pure: they read what they are given and return what to do.       #
# --------------------------------------------------------------------------- #
def job_results(needs):
    """(failed, succeeded) job ids from the needs context, each sorted. Skipped is neither."""
    if not isinstance(needs, dict) or not needs:
        raise ValueError("the needs context names no jobs: the failure-issue job must list "
                         "every other job of the workflow in its needs")
    failed = sorted(j for j, v in needs.items() if (v or {}).get("result") in FAILED)
    succeeded = sorted(j for j, v in needs.items() if (v or {}).get("result") in SUCCEEDED)
    return failed, succeeded


def recorded_failing(body):
    """The failing jobs an issue body records, sorted, or None when it carries no marker."""
    m = MARKER_RE.search(body or "")
    if not m:
        return None
    try:
        failing = json.loads(m.group(1)).get("failing")
    except (ValueError, AttributeError):
        return None
    if not isinstance(failing, list):
        return None
    return sorted(str(j) for j in failing)


def marker(failing):
    return "<!-- failure-issue %s -->" % json.dumps({"failing": sorted(failing)})


def with_marker(body, failing):
    """`body` recording `failing`: its marker replaced, or appended when it has none."""
    body = body or ""
    if MARKER_RE.search(body):
        return MARKER_RE.sub(lambda _m: marker(failing), body, count=1)
    return body.rstrip("\n") + "\n\n" + marker(failing)


def choose(issues):
    """(marked, unmarked): the newest open issue with a marker, and the newest without one.
    Pull requests come back from the issues endpoint too, and are not issues."""
    real = [i for i in issues if "pull_request" not in i]
    marked = [i for i in real if recorded_failing(i.get("body")) is not None]
    unmarked = [i for i in real if recorded_failing(i.get("body")) is None]
    newest = (lambda xs: max(xs, key=lambda i: i["number"]) if xs else None)
    return newest(marked), newest(unmarked)


def plan(marked, unmarked, failed, succeeded):
    """(action, issue, failing after this run).

      open       a job failed and no issue is open
      comment    a job failed: comment on the marked issue, else take over the unmarked one
      recovered  a job that was failing succeeded while another still fails
      close      no job is failing any more
      none       nothing to say: everything skipped, or a success with nothing open about it
    """
    if failed:
        issue = marked or unmarked
        before = set(recorded_failing(issue.get("body")) or []) if issue else set()
        after = sorted((before - set(succeeded)) | set(failed))
        return ("comment" if issue else "open"), issue, after
    if marked is None or not succeeded:
        return "none", None, None
    before = set(recorded_failing(marked.get("body")))
    after = sorted(before - set(succeeded))
    if not after:
        return "close", marked, after
    if before & set(succeeded):
        return "recovered", marked, after
    return "none", None, None


# --------------------------------------------------------------------------- #
# Words.                                                                       #
# --------------------------------------------------------------------------- #
def code_list(jobs):
    return ", ".join(f"`{j}`" for j in jobs)


def run_line(ctx, verdict):
    return (f"{verdict} — [run {ctx['run_number']}]({ctx['run_url']}) · {ctx['time']} · "
            f"{EVENT_WORDS.get(ctx['event'], ctx['event'])}")


def failure_lines(failed, needs, jobs):
    """Each failed job, any note it left, and the failing step(s) from the run's job list.
    `jobs` is that list, or the reason it could not be read."""
    lines = []
    for j in failed:
        how = ("was cancelled (it timed out, or was stopped by hand)"
               if needs[j].get("result") == "cancelled" else "failed")
        lines.append(f"- job `{j}` {how}")
        note = str((needs[j].get("outputs") or {}).get(NOTE_OUTPUT) or "").strip()
        if note:
            lines.append("  > " + " ".join(note.split()))
    if isinstance(jobs, str):
        lines.append(f"- failing step: not available ({jobs})")
        return lines
    found = False
    for job in jobs:
        if job.get("conclusion") not in STEP_FAILED:
            continue
        found = True
        name = job.get("name") or "?"
        where = f"[{name}]({job['html_url']})" if job.get("html_url") else name
        steps = [s.get("name") or "?" for s in job.get("steps") or []
                 if s.get("conclusion") in STEP_FAILED]
        if steps:
            then = f" (then {', '.join(steps[1:])})" if steps[1:] else ""
            lines.append(f"- failing step: **{steps[0]}** in {where}{then}")
        else:
            lines.append(f"- failing step: none recorded in {where}; see its log")
    if not found:
        lines.append("- failing step: no failed job in this run's job list; see the run")
    return lines


def new_issue_body(ctx, lines, failing):
    return "\n".join([
        f"**{ctx['workflow']}** failed. This is its one open failure issue: each further "
        "failure is added here as a comment instead of a new issue, and the issue closes "
        "itself, with a comment, once every failing job has succeeded again.",
        "",
        run_line(ctx, "❌ **Failed**"),
        *lines,
        "",
        "<sub>Kept by .github/actions/failure-issue.</sub>",
        "",
        marker(failing),
    ])


def failure_comment(ctx, lines, failing):
    return "\n".join([run_line(ctx, "❌ **Failed**"), *lines, "",
                      f"Failing now: {code_list(failing)}."])


def recovered_comment(ctx, recovered, failing):
    return (run_line(ctx, f"✅ **{code_list(recovered)} succeeded**")
            + f". Still failing: {code_list(failing)}.")


def close_comment(ctx, recovered):
    return (run_line(ctx, "✅ **Recovered**")
            + f": {code_list(recovered)} succeeded, and no job of **{ctx['workflow']}** is "
            "failing any more. Closing; the next failure opens a new issue.")


# --------------------------------------------------------------------------- #
# The run.                                                                     #
# --------------------------------------------------------------------------- #
def ensure_label(api, repo, name, color, description):
    try:
        api.call("GET", f"/repos/{repo}/labels/{urllib.parse.quote(name, safe='')}")
    except ApiError as e:
        if e.status != 404:
            raise
        api.call("POST", f"/repos/{repo}/labels",
                 body={"name": name, "color": color, "description": description})


def run_jobs(api, ctx):
    """This run attempt's jobs with their steps, or why they could not be read. Never raises:
    a missing step name must not cost the report itself."""
    try:
        got = api.call("GET", f"/repos/{ctx['repo']}/actions/runs/{ctx['run_id']}/attempts/"
                              f"{ctx['attempt']}/jobs", query={"per_page": 100})
        return list((got or {}).get("jobs") or [])
    except (ApiError, OSError, ValueError) as e:
        return f"the run's job list could not be read: {e}"


def report(api, ctx, needs):
    """Act on one run's job results. Returns one line saying what was done."""
    failed, succeeded = job_results(needs)
    wf, repo = ctx["workflow"], ctx["repo"]
    if ctx["event"] not in REPORTED_EVENTS:
        return f"{wf}: a {ctx['event']} run is not reported"
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 ._-]*", wf):
        raise ValueError(f"workflow name {wf!r} cannot be used as a label")
    labels = [LABEL, wf]
    issues = api.call("GET", f"/repos/{repo}/issues",
                      query={"state": "open", "labels": ",".join(labels), "per_page": 100}) or []
    marked, unmarked = choose(issues)
    action, issue, after = plan(marked, unmarked, failed, succeeded)
    if action == "none":
        return (f"{wf}: nothing to report (failed {failed or 'none'}, "
                f"succeeded {succeeded or 'none'})")

    if action in ("open", "comment"):
        lines = failure_lines(failed, needs, run_jobs(api, ctx))
    if action == "open":
        ensure_label(api, repo, LABEL, "b60205",
                     "A scheduled workflow is failing: one open issue per workflow")
        ensure_label(api, repo, wf, "ededed", f"The {wf} workflow")
        made = api.call("POST", f"/repos/{repo}/issues", body={
            "title": f"[cron] {wf} failing", "body": new_issue_body(ctx, lines, after),
            "labels": labels})
        return f"{wf}: failed {failed}; opened #{made['number']}"

    n = issue["number"]
    if action == "comment":
        api.call("POST", f"/repos/{repo}/issues/{n}/comments",
                 body={"body": failure_comment(ctx, lines, after)})
        if recorded_failing(issue.get("body")) != after:
            api.call("PATCH", f"/repos/{repo}/issues/{n}",
                     body={"body": with_marker(issue.get("body"), after)})
        return f"{wf}: failed {failed}; commented on #{n}; failing now {after}"
    if action == "recovered":
        recovered = sorted(set(recorded_failing(issue["body"])) & set(succeeded))
        api.call("POST", f"/repos/{repo}/issues/{n}/comments",
                 body={"body": recovered_comment(ctx, recovered, after)})
        api.call("PATCH", f"/repos/{repo}/issues/{n}",
                 body={"body": with_marker(issue["body"], after)})
        return f"{wf}: {recovered} recovered on #{n}; still failing {after}"
    api.call("POST", f"/repos/{repo}/issues/{n}/comments",
             body={"body": close_comment(ctx, succeeded)})
    api.call("PATCH", f"/repos/{repo}/issues/{n}", body={
        "state": "closed", "state_reason": "completed",
        "body": with_marker(issue["body"], after)})
    return f"{wf}: succeeded {succeeded}; closed #{n}"


def context(env, now):
    """What report() needs to know about this run, from the runner's environment."""
    missing = [k for k in ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_NUMBER",
                           "GITHUB_WORKFLOW", "GITHUB_EVENT_NAME") if not env.get(k)]
    if missing:
        raise ValueError(f"not set: {', '.join(missing)}")
    server = env.get("GITHUB_SERVER_URL") or "https://github.com"
    repo, run_id = env["GITHUB_REPOSITORY"], env["GITHUB_RUN_ID"]
    attempt = env.get("GITHUB_RUN_ATTEMPT") or "1"
    run_url = f"{server}/{repo}/actions/runs/{run_id}"
    return {"repo": repo, "workflow": env["GITHUB_WORKFLOW"], "event": env["GITHUB_EVENT_NAME"],
            "run_id": run_id, "attempt": attempt, "run_number": env["GITHUB_RUN_NUMBER"],
            "run_url": run_url if attempt == "1" else f"{run_url}/attempts/{attempt}",
            "time": now.strftime("%Y-%m-%d %H:%M UTC")}


def main(env=None, now=None):
    env = os.environ if env is None else env
    try:
        needs = json.loads(env.get("FAILURE_ISSUE_NEEDS") or "")
        ctx = context(env, now or datetime.now(timezone.utc))
        api = GitHub(env.get("GITHUB_API_URL") or "https://api.github.com",
                     env.get("GITHUB_TOKEN") or "")
        print(report(api, ctx, needs), flush=True)
    except (ValueError, ApiError, OSError) as e:
        print(f"::error title=failure-issue::{e}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
