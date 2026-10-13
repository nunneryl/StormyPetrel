"""Decide whether a forecast-pipeline run goes ahead, or another run already covers its slot.

WHY. GitHub's scheduler proved unreliable for this workflow over 30 days (2026-09-12 ..
2026-10-12): scheduled full runs started a median 4.4 h late (the 03:30 slot about 6 h), two
slots had no run, one got no runner, and the hourly buoy-update ran in 22% of hours. A
workflow_dispatch starts within seconds. So an outside scheduler (cron-job.org) now dispatches
the runs at fixed times, and GitHub's cron stays only as a fallback. Without a gate, a cron run
that fires hours late would repeat the work a dispatched run already did — and before this
change the two could even run at once, as the concurrency group was keyed on the trigger.

THE SLOTS. The full pipeline's slots are DISPATCH_SLOTS in forecast-pipeline.yml, held there
once: 01:45, 08:15, 13:45 and 20:15 UTC, chosen from the NWPS publication log
(docs/scheduling.md). The buoy-update's slots are the top of every hour.

THE RULE, for a run of job J (full-pipeline or buoy-update):
  a manual run (the Actions tab; trigger input "manual", the default)   always goes ahead
  anything else, a scheduled run or an external dispatch:
    the ANCHOR is the latest slot of J at or before now + MARGIN (a dispatch that fires a
    little early still belongs to its slot); the WINDOW opens at anchor - MARGIN.
    The run is skipped when another run of J, with a LOWER run id, was created inside the
    window and is queued, in progress, or completed successfully. Otherwise it goes ahead.
  A run that failed or was cancelled covers nothing, so a late cron run after a failed
  dispatch is a retry. The lower-id rule settles a tie: of two runs for one slot, the
  earlier one runs and the later one skips, never both skipping.

FAIL OPEN. When anything goes wrong — the API, the environment, a slot list that does not
parse — the run goes ahead. A duplicate run costs runner minutes; a missed run costs a cycle.
main() therefore always exits 0, and the workflow treats a missing answer as "run".

WHICH JOB A RUN IS. The runs list does not carry inputs or the cron string, so the workflow's
run-name begins with the logical job ("full-pipeline (external)"), and that is what is read
here. A run whose title names no job (a run from before run-name existed) covers nothing.

Standard library only, so the gate job needs no setup-python: the runner image's python3 runs
it. The decision functions are pure and tested in pipeline/tests/test_dispatch_gate.py.
"""
from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone

JOBS = ("full-pipeline", "buoy-update")
MARGIN = timedelta(minutes=5)
COVERING_STATUSES = ("queued", "in_progress", "waiting", "pending", "requested")
MAX_PAGES = 10
UTC = timezone.utc


# --------------------------------------------------------------------------- #
# Decisions. Pure: they read what they are given and return what to do.       #
# --------------------------------------------------------------------------- #
def parse_slots(text):
    """'01:45 08:15' -> [(1, 45), (8, 15)], sorted. Raises ValueError on anything else."""
    slots = []
    for word in (text or "").split():
        m = re.fullmatch(r"([01]\d|2[0-3]):([0-5]\d)", word)
        if not m:
            raise ValueError(f"slot {word!r} is not HH:MM in UTC")
        slots.append((int(m.group(1)), int(m.group(2))))
    if not slots:
        raise ValueError("no slots")
    if len(set(slots)) != len(slots):
        raise ValueError(f"a slot is listed twice: {text!r}")
    return sorted(slots)


def anchor(job, slots, now, margin=MARGIN):
    """The latest slot of `job` at or before now + margin, as an aware UTC datetime."""
    t = now + margin
    if job == "buoy-update":
        return t.replace(minute=0, second=0, microsecond=0)
    if job != "full-pipeline":
        raise ValueError(f"unknown job {job!r}")
    day = t.replace(hour=0, minute=0, second=0, microsecond=0)
    for back in (0, 1):
        d = day - timedelta(days=back)
        past = [d.replace(hour=h, minute=m) for h, m in slots if d.replace(hour=h, minute=m) <= t]
        if past:
            return max(past)
    raise ValueError("no slot at or before now")  # unreachable with a non-empty slot list


def job_of_title(title):
    """The logical job a run's title names, or None. The title is the workflow's run-name,
    which begins with the job: 'full-pipeline (external)', 'buoy-update (schedule)'."""
    first = (title or "").split(" ", 1)[0]
    return first if first in JOBS else None


def parse_time(s):
    """GitHub's '2026-10-13T08:15:03Z' as an aware UTC datetime."""
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def covers(run):
    """Whether a run counts as having done (or doing) its slot's work."""
    status = run.get("status")
    if status in COVERING_STATUSES:
        return True
    return status == "completed" and run.get("conclusion") == "success"


def decide(job, event, trigger, now, own_id, slots, runs, margin=MARGIN):
    """(go, reason). `runs` are the workflow's runs as the REST API lists them."""
    if event == "workflow_dispatch" and trigger != "external":
        return True, "a manual run always goes ahead"
    if event not in ("schedule", "workflow_dispatch"):
        return True, f"a {event} run is not gated"
    a = anchor(job, slots, now, margin)
    since = a - margin
    covering = sorted(
        r["id"] for r in runs
        if r.get("id") is not None and int(r["id"]) < int(own_id)
        and job_of_title(r.get("display_title")) == job
        and r.get("created_at") and parse_time(r["created_at"]) >= since
        and covers(r))
    slot = a.strftime("%Y-%m-%d %H:%M UTC")
    if covering:
        return False, (f"skipped: run {covering[0]} already covers the {job} slot of {slot} "
                       f"(created since {since.strftime('%H:%M')})")
    return True, f"no earlier run covers the {job} slot of {slot}"


# --------------------------------------------------------------------------- #
# The run.                                                                     #
# --------------------------------------------------------------------------- #
def workflow_file(ref):
    """'o/r/.github/workflows/forecast-pipeline.yml@refs/heads/main' -> 'forecast-pipeline.yml'."""
    m = re.fullmatch(r"[^/]+/[^/]+/\.github/workflows/([^/@]+)@.+", ref or "")
    if not m:
        raise ValueError(f"cannot read the workflow file from GITHUB_WORKFLOW_REF {ref!r}")
    return m.group(1)


def list_runs(api_url, token, repo, wf_file, since, timeout=20):
    """This workflow's runs created at or after `since`, every page."""
    runs, page = [], 1
    while page <= MAX_PAGES:
        query = urllib.parse.urlencode({
            "created": ">=" + since.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "per_page": 100, "page": page})
        req = urllib.request.Request(
            f"{api_url.rstrip('/')}/repos/{repo}/actions/workflows/{wf_file}/runs?{query}",
            headers={"Accept": "application/vnd.github+json",
                     "Authorization": f"Bearer {token}",
                     "X-GitHub-Api-Version": "2022-11-28",
                     "User-Agent": "StormyPetrel-dispatch-gate"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            got = json.loads(r.read() or b"{}")
        batch = list(got.get("workflow_runs") or [])
        runs += batch
        if not batch or len(runs) >= int(got.get("total_count") or 0):
            break
        page += 1
    return runs


def run(env, now, fetch=None):
    """(go, reason) for this run, from the runner's environment. Never raises. `fetch` stands
    in for list_runs in tests; it is looked up when called, not when this is defined."""
    fetch = fetch or list_runs
    try:
        job, event = env.get("GATE_JOB") or "", env.get("GITHUB_EVENT_NAME") or ""
        trigger = env.get("GATE_TRIGGER") or ""
        if job not in JOBS:
            raise ValueError(f"GATE_JOB {job!r} is not one of {', '.join(JOBS)}")
        if event == "workflow_dispatch" and trigger != "external":
            return decide(job, event, trigger, now, 0, [], [])
        slots = parse_slots(env.get("DISPATCH_SLOTS"))
        since = anchor(job, slots, now) - MARGIN
        runs = fetch(env.get("GITHUB_API_URL") or "https://api.github.com",
                     env.get("GITHUB_TOKEN") or "", env["GITHUB_REPOSITORY"],
                     workflow_file(env.get("GITHUB_WORKFLOW_REF")), since)
        return decide(job, event, trigger, now, int(env["GITHUB_RUN_ID"]), slots, runs)
    except (ValueError, KeyError, TypeError, OSError, urllib.error.URLError) as e:
        return True, f"going ahead: the gate could not decide ({type(e).__name__}: {e})"


def main(env=None, now=None):
    env = os.environ if env is None else env
    go, reason = run(env, now or datetime.now(UTC))
    reason = " ".join(reason.split())
    print(("::notice title=dispatch gate::" if not go else "") + reason, flush=True)
    if reason.startswith("going ahead: the gate could not decide"):
        print(f"::warning title=dispatch gate::{reason}", flush=True)
    out = env.get("GITHUB_OUTPUT")
    if out:
        try:
            with open(out, "a", encoding="utf-8") as f:
                f.write(f"run={'true' if go else 'false'}\nreason={reason}\n")
        except OSError as e:
            # No output means the jobs read "run" (they test != 'false'): still fail open.
            print(f"::warning title=dispatch gate::could not write GITHUB_OUTPUT: {e}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
