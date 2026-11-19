"""Keep one open 'nwps-stale' issue while any single NWPS office's newest cycle is 24 h old.

WHY. pipeline/nwps_stale_gate.py fails a full run only when 5 or more offices are stale, so one
office going quiet is a log line and nothing more. On 2026-11-19 at 01:45Z the newest cycle
NOMADS listed for OKX (New York) was 18 Nov 00Z, 26 h old; spot pages for that office showed
26 h old data and nobody was told. It recovered by the 08:15Z run.

THE RULE. An office counts when the cycle the run read is NWPS_STALE_AGE_H (24 h) old or more,
the same line as nwps.stale_reason and the spot page's "Model run N h ago" note, or when the
run read no cycle at all for it. An office one cycle behind but under 24 h old does not count:
offices skip a cycle now and then without anything being wrong.

WHAT IT DOES, after each full run (never failing the run):
  any office old, no issue open      open one issue naming each office and why
  the set of old offices changed     comment on the open issue: which went stale, which are
                                     still stale, which are fresh again
  same offices as last time          nothing: four runs a day of the same news would be noise
  no office old, an issue open       comment and close it
  no office old, nothing open        nothing
The offices the issue is about are kept in a hidden marker in its body.

TWO HALVES, because the job that runs the pipeline holds no write scope on issues:
  summarise FILE   in full-pipeline: read nwps_freshness.json and write the old offices to
                   $GITHUB_OUTPUT (nwps-old, and nwps-checked=true when the file had rows).
                   Always exits 0.
  report           in the nwps-stale-issue job (issues: write): read them back from NWPS_OLD /
                   NWPS_CHECKED and open, comment on or close the issue. Exits 1 on an API
                   error, so the workflow's failure issue reports it.

Standard library only, so the issue job needs no setup-python.
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

LABEL = "nwps-stale"
# The same as pipeline/forecast/nwps.py NWPS_STALE_AGE_H (a test holds them equal); copied, not
# imported, so the issue job can run this file without the pipeline's dependencies.
STALE_AGE_H = 24.0
MARKER_RE = re.compile(r"<!-- nwps-stale (\{.*?\}) -->")


# --------------------------------------------------------------------------- #
# Decisions. Pure.                                                             #
# --------------------------------------------------------------------------- #
def old_offices(rows: list[dict]) -> list[dict]:
    """[{wfo, why}] for each office whose cycle is STALE_AGE_H old or more, or that read none,
    sorted by office."""
    out = []
    for r in rows or []:
        wfo = r.get("wfo")
        if not wfo:
            continue
        age = r.get("age_h")
        if r.get("cycle_used") is None:
            out.append({"wfo": wfo, "why": f"no cycle read (newest listed "
                                           f"{r.get('newest_listed') or 'none'})"})
        elif age is not None and age >= STALE_AGE_H:
            out.append({"wfo": wfo, "why": f"cycle {r['cycle_used']} is {age:g} h old"})
    return sorted(out, key=lambda o: o["wfo"])


def recorded(body: str | None) -> list[str] | None:
    """The offices an issue body records, sorted, or None when it has no marker."""
    m = MARKER_RE.search(body or "")
    if not m:
        return None
    try:
        offices = json.loads(m.group(1)).get("offices")
    except (ValueError, AttributeError):
        return None
    return sorted(str(o) for o in offices) if isinstance(offices, list) else None


def marker(offices: list[str]) -> str:
    return "<!-- nwps-stale %s -->" % json.dumps({"offices": sorted(offices)})


def with_marker(body: str | None, offices: list[str]) -> str:
    body = body or ""
    if MARKER_RE.search(body):
        return MARKER_RE.sub(lambda _m: marker(offices), body, count=1)
    return body.rstrip("\n") + "\n\n" + marker(offices)


def plan(old: list[dict], issue: dict | None) -> str:
    """open | comment | close | none."""
    now = sorted(o["wfo"] for o in old)
    if now:
        if issue is None:
            return "open"
        return "none" if recorded(issue.get("body")) == now else "comment"
    return "close" if issue is not None else "none"


# --------------------------------------------------------------------------- #
# Words.                                                                       #
# --------------------------------------------------------------------------- #
def office_lines(old: list[dict]) -> list[str]:
    return [f"- **{o['wfo'].upper()}**: {o['why']}" for o in old]


def run_line(ctx: dict) -> str:
    return f"[run {ctx['run_number']}]({ctx['run_url']}) · {ctx['time']}"


def new_body(ctx: dict, old: list[dict]) -> str:
    return "\n".join([
        f"NWPS is {STALE_AGE_H:g} h old or more for {len(old)} office(s) after "
        f"{run_line(ctx)}. Spot pages served by these offices show the older cycle, with a "
        "\"Model run N h ago\" note.",
        "",
        *office_lines(old),
        "",
        "This issue gets a comment when the set of stale offices changes, and closes itself "
        "once every office is fresh again. The run itself does not fail for this (it fails at "
        "5 stale offices, pipeline/nwps_stale_gate.py).",
        "",
        "<sub>Kept by scripts/nwps_stale_issue.py.</sub>",
        "",
        marker([o["wfo"] for o in old]),
    ])


def change_comment(ctx: dict, old: list[dict], before: list[str]) -> str:
    now = {o["wfo"] for o in old}
    went = [o for o in old if o["wfo"] not in before]
    still = [o for o in old if o["wfo"] in before]
    fresh = sorted(set(before) - now)
    lines = [run_line(ctx)]
    if went:
        lines += ["", "Now stale:", *office_lines(went)]
    if still:
        lines += ["", "Still stale:", *office_lines(still)]
    if fresh:
        lines += ["", "Fresh again: " + ", ".join(w.upper() for w in fresh) + "."]
    return "\n".join(lines)


def close_comment(ctx: dict, before: list[str] | None) -> str:
    names = ", ".join(w.upper() for w in before or []) or "every office"
    return (f"{run_line(ctx)}: every office read a cycle under {STALE_AGE_H:g} h old "
            f"({names} fresh again). Closing; the next stale office opens a new issue.")


# --------------------------------------------------------------------------- #
# The run.                                                                     #
# --------------------------------------------------------------------------- #
class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(f"HTTP {status}: {message}")
        self.status = status


class GitHub:
    def __init__(self, api_url: str, token: str, timeout: float = 30):
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
            "User-Agent": "StormyPetrel-nwps-stale-issue",
            **({"Content-Type": "application/json"} if data is not None else {}),
        })
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                raw = r.read()
        except urllib.error.HTTPError as e:
            raise ApiError(e.code, e.read().decode("utf-8", "replace")[:300]) from None
        return json.loads(raw) if raw else None


def report(api, ctx: dict, old: list[dict]) -> str:
    """Act on this run's old offices. Returns one line saying what was done."""
    repo = ctx["repo"]
    # Look for the open issue first: there is never more than one to update.
    issues = api.call("GET", f"/repos/{repo}/issues",
                      query={"state": "open", "labels": LABEL, "per_page": 100}) or []
    issues = [i for i in issues if "pull_request" not in i]
    issue = max(issues, key=lambda i: i["number"]) if issues else None
    action = plan(old, issue)
    now = [o["wfo"] for o in old]
    if action == "none":
        return f"nwps-stale: nothing to do (stale: {now or 'none'})"
    if action == "open":
        try:
            api.call("GET", f"/repos/{repo}/labels/{LABEL}")
        except ApiError as e:
            if e.status != 404:
                raise
            api.call("POST", f"/repos/{repo}/labels", body={
                "name": LABEL, "color": "fbca04",
                "description": "An NWPS office's newest cycle is 24 h old or more"})
        made = api.call("POST", f"/repos/{repo}/issues", body={
            "title": "NWPS stale for " + ", ".join(w.upper() for w in now),
            "body": new_body(ctx, old), "labels": [LABEL]})
        return f"nwps-stale: opened #{made['number']} for {now}"
    n = issue["number"]
    before = recorded(issue.get("body"))
    if action == "comment":
        api.call("POST", f"/repos/{repo}/issues/{n}/comments",
                 body={"body": change_comment(ctx, old, before or [])})
        api.call("PATCH", f"/repos/{repo}/issues/{n}",
                 body={"body": with_marker(issue.get("body"), now)})
        return f"nwps-stale: commented on #{n}; stale now {now}"
    api.call("POST", f"/repos/{repo}/issues/{n}/comments", body={"body": close_comment(ctx, before)})
    api.call("PATCH", f"/repos/{repo}/issues/{n}", body={
        "state": "closed", "state_reason": "completed", "body": with_marker(issue.get("body"), [])})
    return f"nwps-stale: closed #{n}"


def context(env: dict, now: datetime) -> dict:
    missing = [k for k in ("GITHUB_REPOSITORY", "GITHUB_RUN_ID", "GITHUB_RUN_NUMBER")
               if not env.get(k)]
    if missing:
        raise ValueError(f"not set: {', '.join(missing)}")
    server = env.get("GITHUB_SERVER_URL") or "https://github.com"
    repo, run_id = env["GITHUB_REPOSITORY"], env["GITHUB_RUN_ID"]
    return {"repo": repo, "run_number": env["GITHUB_RUN_NUMBER"],
            "run_url": f"{server}/{repo}/actions/runs/{run_id}",
            "time": now.strftime("%Y-%m-%d %H:%M UTC")}


def summarise(path: str, env: dict) -> int:
    """full-pipeline's half: write nwps-checked and nwps-old to $GITHUB_OUTPUT. Never fails."""
    try:
        with open(path, encoding="utf-8") as f:
            rows = json.load(f)
    except (OSError, ValueError) as e:
        rows = None
        print(f"nwps-stale: could not read {path} ({e}); the issue is left as it is.")
    checked = bool(rows) and isinstance(rows, list)
    old = old_offices(rows) if checked else []
    if checked:
        print(f"nwps-stale: {len(rows)} offices, {len(old)} with a cycle {STALE_AGE_H:g} h old "
              f"or none: {', '.join(o['wfo'] + ' ' + o['why'] for o in old) or 'none'}")
    out = env.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"nwps-checked={'true' if checked else 'false'}\n")
            f.write(f"nwps-old={json.dumps(old, separators=(',', ':'))}\n")
    return 0


def main(argv: list[str] | None = None, env: dict | None = None, now: datetime | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    env = os.environ if env is None else env
    if argv[:1] == ["summarise"] and len(argv) == 2:
        return summarise(argv[1], env)
    if argv != ["report"]:
        print("usage: nwps_stale_issue.py summarise FILE | report")
        return 2
    if env.get("NWPS_CHECKED") != "true":
        print("nwps-stale: this run did not check the offices; the issue is left as it is.")
        return 0
    try:
        old = json.loads(env.get("NWPS_OLD") or "[]")
        if not isinstance(old, list) or not all(isinstance(o, dict) and o.get("wfo") for o in old):
            raise ValueError(f"NWPS_OLD is not a list of offices: {env.get('NWPS_OLD')!r}")
        ctx = context(env, now or datetime.now(timezone.utc))
        api = GitHub(env.get("GITHUB_API_URL") or "https://api.github.com",
                     env.get("GITHUB_TOKEN") or "")
        print(report(api, ctx, old), flush=True)
    except (ValueError, ApiError, OSError) as e:
        print(f"::error title=nwps-stale-issue::{e}", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
