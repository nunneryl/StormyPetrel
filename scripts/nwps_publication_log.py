#!/usr/bin/env python3
"""Log when each NWPS cycle is published on NOMADS. READ-ONLY; WRITES NOTHING.

WHY THIS EXISTS. The next design choice — how often to fetch, and what covers the hours
between the NWPS frontier and the claimed forecast length — turns on two numbers nothing
records: how long after its nominal time each office's cycle appears on NOMADS, and how often
an office skips a cycle it normally runs. The pipeline's own logs only bracket the first: they
see a cycle when it happens to be the newest one at a fetch, three times a day. NOMADS keeps
only the last few days, so the history has to be taken while it is still there.

WHAT IT DOES. For every office in WFO_TO_REGION and every date NOMADS lists for that office's
region, it lists {region}.{date}/{wfo}/ and, for each cycle folder listed, asks for the CG1 GRIB
file's Last-Modified with a HEAD request — the same file the pipeline downloads, built by the
pipeline's own nwps._direct_grib_url so the two cannot drift apart. Last-Modified is the
server's own timestamp, so it is exact however late we look: a check six hours after
publication reports the same time as a check six seconds after. That is why a 6-hourly
schedule is enough.

AS SEEN, NOT AS INTERPRETED. Every listing and every file request is printed with the HTTP
status it returned, or the error if it returned none. NOMADS answers 403, not 404, for a path
that does not exist — every afc folder returns 403 on every date while every other office lists
fine from the same machine — so a 403 is recorded as 403 and nothing more: not "blocked", not
"not published". The report below draws conclusions only from a 200, and puts everything else
in a table of statuses as seen.

WRITES NOTHING. No database, no repo, no file the pipeline reads. Its only output is stdout,
which is the job log. No secrets, no Supabase, no forecast rows: the pipeline is not run and
nothing it reads is touched, which is what makes it safe before the 2026-10-06 face-factor
measurement. pipeline.forecast.nwps is IMPORTED for its URL builder and listing regexes, never
called for its fetch path.

POLITE. One request at a time, each starting at least --min-interval seconds after the last
(default 1 s, never below 0.5 s). NOMADS blocks clients that hammer it. A full run is a few
hundred requests, so it takes minutes, which a 6-hourly job can afford.

HOW TO READ IT BACK (on 2026-10-06, or any day). Every observation is one line:
    NWPSPUB {"kind": "file", "wfo": "lox", "date": "20260922", "cycle": "06", "status": 200, ...}
so the tables come from the saved job logs, not from any store:

    mkdir -p nwps-logs
    gh run list --repo nunneryl/StormyPetrel --workflow nwps-publication-log.yml \\
        --limit 300 --json databaseId --jq '.[].databaseId' |
      while read id; do
        gh run view "$id" --repo nunneryl/StormyPetrel --log > "nwps-logs/$id.log"
      done
    python3 scripts/nwps_publication_log.py --report nwps-logs

--report also reads a directory of the "Download log archive" zips from the Actions page.
It prints (1) lag after nominal per office and cycle — n, min, median, max; (2) skipped
cycles: a cycle hour the office runs on at least half its settled dates that is absent from
every 200 listing of a settled date; (3) every date no 200 listing settled, with the statuses
seen; (4) every file whose Last-Modified changed between checks. A date is SETTLED once a 200
listing of it was taken at least --settle-hours after the date ended, so a cycle that is merely
late is never reported as skipped.

    python3 scripts/nwps_publication_log.py                       # log (the scheduled job)
    python3 scripts/nwps_publication_log.py --offices lox,afc     # a subset (the PR smoke run)
    python3 scripts/nwps_publication_log.py --report nwps-logs    # the tables, from saved logs
"""
from __future__ import annotations

import argparse
import email.utils
import io
import json
import os
import statistics
import sys
import time
import zipfile
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pipeline.config import NWPS_NOMADS_BASE, USER_AGENT, WFO_TO_REGION  # noqa: E402

MARKER = "NWPSPUB"
FORMAT = 1                  # bump if a record's meaning changes; the report reads it
MIN_INTERVAL_S = 1.0        # default spacing between request starts
MIN_INTERVAL_FLOOR_S = 0.5  # never faster than this, whatever the flag says
TIMEOUT_S = 30.0
SETTLE_HOURS = 12.0         # a date is settled once a 200 listing is this long past its end
NORMAL_FRACTION = 0.5       # a cycle hour is "normal" for an office on >= this share of dates


# --------------------------------------------------------------------------- #
# Parsing — pure, so the tests can hold them to hand-written input              #
# --------------------------------------------------------------------------- #

def _nwps():
    """The pipeline's NWPS module, imported only when a listing is parsed or a URL built,
    so --report runs on nothing but the standard library and pipeline.config."""
    from pipeline.forecast import nwps
    return nwps


def parse_root(html: str) -> dict[str, list[str]]:
    """{region: [YYYYMMDD oldest-first]} from the NOMADS nwps/prod index."""
    out: dict[str, list[str]] = {}
    for region, date in _nwps()._DATE_HREF_RE.findall(html):
        out.setdefault(region.lower(), []).append(date)
    return {r: sorted(set(ds)) for r, ds in out.items()}


def parse_cycles(html: str) -> list[str]:
    """[HH oldest-first] from a {region}.{date}/{wfo}/ index."""
    return sorted(set(_nwps()._HH_HREF_RE.findall(html)))


def parse_http_date(value: str | None) -> datetime | None:
    """A Last-Modified header as an aware UTC datetime, or None if absent or unreadable."""
    if not value:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, IndexError):
        return None
    if dt is None:
        return None
    if dt.tzinfo is None:              # HTTP dates are GMT by definition
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def nominal(date_ymd: str, hh: str) -> datetime:
    """The cycle's nominal time: its date at HH:00 UTC."""
    return datetime.strptime(date_ymd + hh, "%Y%m%d%H").replace(tzinfo=timezone.utc)


def lag_hours(date_ymd: str, hh: str, published: datetime | None) -> float | None:
    """Hours from nominal to publication, 3 decimals (3.6 s). None without a timestamp."""
    if published is None:
        return None
    return round((published - nominal(date_ymd, hh)).total_seconds() / 3600.0, 3)


def iso(dt: datetime | None) -> str | None:
    return None if dt is None else dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def from_iso(s: str | None) -> datetime | None:
    if not s:
        return None
    return datetime.strptime(s, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)


def record_line(rec: dict) -> str:
    """One observation as one log line. sort_keys so two runs' lines diff cleanly."""
    return f"{MARKER} {json.dumps(rec, sort_keys=True, separators=(',', ':'))}"


def parse_line(line: str) -> dict | None:
    """The record on a log line, or None. The marker may sit anywhere: `gh run view --log`
    prefixes job and step names and the Actions archive prefixes a timestamp."""
    i = line.find(MARKER + " {")
    if i < 0:
        return None
    try:
        rec = json.loads(line[i + len(MARKER) + 1:].strip())
    except ValueError:
        return None
    return rec if isinstance(rec, dict) else None


# --------------------------------------------------------------------------- #
# The logger                                                                   #
# --------------------------------------------------------------------------- #

@dataclass
class Reply:
    """What one request returned, as seen. status is None when there was no HTTP answer."""
    status: int | None
    headers: dict = field(default_factory=dict)
    text: str = ""
    error: str | None = None
    elapsed_s: float = 0.0


class Pacer:
    """Holds request STARTS at least min_interval_s apart. The clock and sleep are
    injectable so the spacing can be tested without waiting for it."""

    def __init__(self, min_interval_s: float, clock=time.monotonic, sleep=time.sleep):
        self.min_interval_s = max(float(min_interval_s), MIN_INTERVAL_FLOOR_S)
        self._clock = clock
        self._sleep = sleep
        self._last: float | None = None

    def wait(self) -> None:
        if self._last is not None:
            gap = self._clock() - self._last
            if gap < self.min_interval_s:
                self._sleep(self.min_interval_s - gap)
        self._last = self._clock()


def make_fetch(timeout_s: float = TIMEOUT_S):
    """A real fetch(method, url, headers=None) -> Reply on a session of our own. Every
    transport failure becomes Reply(status=None, error=...) — recorded, never raised."""
    import requests

    s = requests.Session()
    s.headers.update({"User-Agent": USER_AGENT})

    def fetch(method: str, url: str, headers: dict | None = None) -> Reply:
        t0 = time.monotonic()
        try:
            # stream=True, and a body is read only for a plain GET that answered 200 — a
            # directory listing. A server that ignored the one-byte Range would otherwise
            # hand us the whole GRIB.
            with s.request(method, url, headers=headers or {}, timeout=timeout_s,
                           allow_redirects=False, stream=True) as r:
                listing = method == "GET" and not headers and r.status_code == 200
                text = r.text if listing else ""
                return Reply(r.status_code, dict(r.headers), text,
                             elapsed_s=round(time.monotonic() - t0, 3))
        except requests.RequestException as e:
            return Reply(None, error=f"{type(e).__name__}: {e}"[:300],
                         elapsed_s=round(time.monotonic() - t0, 3))

    return fetch


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def observe(offices, fetch, pacer, emit, now=_utcnow) -> Counter:
    """List every office's cycles for the dates NOMADS holds and HEAD each CG1 file.

    Emits one record per request. Returns a Counter of statuses ("200", "403", ...,
    "error"). The only thing it ever does with a status is parse a 200 body; every other
    answer is passed through as seen.
    """
    statuses: Counter = Counter()
    base = NWPS_NOMADS_BASE

    def call(method, url, headers=None):
        pacer.wait()
        at = now()
        rep = fetch(method, url, headers)
        statuses["error" if rep.status is None else str(rep.status)] += 1
        return at, rep

    def common(at, rep, url, method):
        return {"v": FORMAT, "url": url, "method": method, "status": rep.status,
                "error": rep.error, "checked_at": iso(at), "elapsed_s": rep.elapsed_s}

    at, rep = call("GET", f"{base}/")
    dates = parse_root(rep.text) if rep.status == 200 else {}
    emit({**common(at, rep, f"{base}/", "GET"), "kind": "root",
          "dates": dates if rep.status == 200 else None})

    nwps = _nwps()
    for wfo in offices:
        region = WFO_TO_REGION[wfo]
        for date_ymd in dates.get(region, []):
            url = f"{base}/{region}.{date_ymd}/{wfo}/"
            at, rep = call("GET", url)
            cycles = parse_cycles(rep.text) if rep.status == 200 else None
            emit({**common(at, rep, url, "GET"), "kind": "listing", "wfo": wfo,
                  "region": region, "date": date_ymd, "cycles": cycles})
            for hh in cycles or []:
                furl = nwps._direct_grib_url(region, wfo, date_ymd, hh)
                attempts = [("HEAD", None)]
                # A server that refuses HEAD is asked for one byte instead. Both answers
                # are recorded; neither replaces the other.
                while attempts:
                    method, headers = attempts.pop(0)
                    at, rep = call(method, furl, headers)
                    published = parse_http_date(rep.headers.get("Last-Modified"))
                    length = rep.headers.get("Content-Length")
                    emit({**common(at, rep, furl, method), "kind": "file", "wfo": wfo,
                          "region": region, "date": date_ymd, "cycle": hh,
                          "last_modified": iso(published),
                          "lag_h": lag_hours(date_ymd, hh, published),
                          "bytes": int(length) if length and length.isdigit() else None})
                    if method == "HEAD" and rep.status in (405, 501):
                        attempts.append(("GET", {"Range": "bytes=0-0"}))
    return statuses


def run_summary(records: list[dict]) -> list[str]:
    """One human line per office and date, for reading a single run in the Actions UI."""
    files = {}
    for r in records:
        if r.get("kind") == "file" and r.get("lag_h") is not None:
            files[(r["wfo"], r["date"], r["cycle"])] = r["lag_h"]
    lines = []
    for r in records:
        if r.get("kind") != "listing":
            continue
        head = f"{r['wfo']:4} {r['region']}.{r['date']}  listing {r['status'] or r['error']}"
        cells = []
        for hh in r.get("cycles") or []:
            lag = files.get((r["wfo"], r["date"], hh))
            cells.append(f"{hh}Z {'+%.1fh' % lag if lag is not None else 'no Last-Modified'}")
        lines.append(head + ("  " + "  ".join(cells) if cells else ""))
    return lines


# --------------------------------------------------------------------------- #
# The report — from saved logs, no network                                     #
# --------------------------------------------------------------------------- #

def iter_log_lines(paths):
    """Every line of every log under `paths`: files, directories (recursive) and zips."""
    for p in paths:
        if os.path.isdir(p):
            for dirpath, _dirs, names in os.walk(p):
                for n in sorted(names):
                    yield from iter_log_lines([os.path.join(dirpath, n)])
        elif zipfile.is_zipfile(p):
            with zipfile.ZipFile(p) as z:
                for n in sorted(z.namelist()):
                    if not n.endswith("/"):
                        with z.open(n) as fh:
                            yield from io.TextIOWrapper(fh, encoding="utf-8", errors="replace")
        else:
            with open(p, encoding="utf-8", errors="replace") as fh:
                yield from fh


def build_report(records, settle_h: float = SETTLE_HOURS,
                 normal_fraction: float = NORMAL_FRACTION) -> dict:
    """Aggregate observations from any number of runs. See the module docstring for the
    definitions; the rule that matters is that only a 200 is ever read as evidence."""
    runs = {r["checked_at"] for r in records if r.get("kind") == "root"}
    listings: dict = {}
    files: dict = {}
    for r in records:
        if r.get("v") != FORMAT:
            continue
        if r.get("kind") == "listing":
            listings.setdefault((r["wfo"], r["date"]), []).append(r)
        elif r.get("kind") == "file":
            files.setdefault((r["wfo"], r["date"], r["cycle"]), []).append(r)

    # (1) lag per office and cycle. The EARLIEST Last-Modified a check saw is the
    # publication time; a later, different one is a rewrite and is listed in (4).
    changed = []
    lags: dict = {}
    for (wfo, date_ymd, hh), obs in sorted(files.items()):
        stamps = sorted({o["last_modified"] for o in obs if o.get("last_modified")})
        if not stamps:
            continue
        lag = lag_hours(date_ymd, hh, from_iso(stamps[0]))
        lags.setdefault((wfo, hh), []).append(lag)
        if len(stamps) > 1:
            changed.append({"wfo": wfo, "date": date_ymd, "cycle": hh, "last_modified": stamps})
    lag_rows = [{"wfo": w, "cycle": hh, "n": len(v), "min": min(v),
                 "median": round(statistics.median(v), 3), "max": max(v)}
                for (w, hh), v in sorted(lags.items())]

    # (2) and (3). Only a 200 listing is evidence of what a folder held.
    settled: dict = {}
    undetermined = []
    for (wfo, date_ymd), obs in sorted(listings.items()):
        ok = [o for o in obs if o.get("status") == 200]
        day_end = nominal(date_ymd, "00") + timedelta(days=1)
        late = [o for o in ok if from_iso(o["checked_at"]) >= day_end + timedelta(hours=settle_h)]
        if late:
            seen = set()
            for o in ok:
                seen.update(o.get("cycles") or [])
            settled.setdefault(wfo, {})[date_ymd] = seen
        else:
            seen_as = Counter("error" if o.get("status") is None else str(o["status"])
                              for o in obs)
            undetermined.append({
                "wfo": wfo, "date": date_ymd, "statuses": dict(sorted(seen_as.items())),
                "why": ("no 200 listing" if not ok else
                        f"last 200 listing at {max(o['checked_at'] for o in ok)}, "
                        f"before the date settled")})

    skipped = []
    normal = {}
    for wfo, by_date in sorted(settled.items()):
        if len(by_date) < 2:
            continue                       # one date cannot say what is normal
        counts = Counter(hh for seen in by_date.values() for hh in seen)
        normal[wfo] = sorted(hh for hh, n in counts.items()
                             if n / len(by_date) >= normal_fraction)
        for date_ymd, seen in sorted(by_date.items()):
            for hh in normal[wfo]:
                if hh not in seen:
                    skipped.append({"wfo": wfo, "date": date_ymd, "cycle": hh,
                                    "listed": sorted(seen)})

    return {"runs": len(runs),
            "first_run": min(runs) if runs else None,
            "last_run": max(runs) if runs else None,
            "records": len(records),
            "lags": lag_rows, "normal": normal, "skipped": skipped,
            "undetermined": undetermined, "changed": changed}


def render_report(rep: dict) -> str:
    out = [f"# NWPS publication on NOMADS — {rep['runs']} run(s), "
           f"{rep['first_run']} .. {rep['last_run']}, {rep['records']} observations", ""]
    out += ["## Lag after nominal, per office and cycle (hours)", "",
            "| office | cycle | n | min | median | max |", "|---|---|---|---|---|---|"]
    out += [f"| {r['wfo']} | {r['cycle']}Z | {r['n']} | {r['min']:.2f} | "
            f"{r['median']:.2f} | {r['max']:.2f} |" for r in rep["lags"]]
    out += ["", "## Skipped cycles (normally run, never listed on a settled date)", "",
            "| office | date | cycle | listed that date |", "|---|---|---|---|"]
    out += [f"| {s['wfo']} | {s['date']} | {s['cycle']}Z | {', '.join(s['listed']) or '—'} |"
            for s in rep["skipped"]] or ["| — | — | — | none |"]
    out += ["", "Normal cycles per office: "
            + "; ".join(f"{w} {'/'.join(h)}" for w, h in sorted(rep["normal"].items()))]
    out += ["", "## Not determinable from a settled 200 listing (statuses as seen)", "",
            "| office | date | statuses | why |", "|---|---|---|---|"]
    out += [f"| {u['wfo']} | {u['date']} | "
            f"{', '.join(f'{k}×{v}' for k, v in u['statuses'].items())} | {u['why']} |"
            for u in rep["undetermined"]] or ["| — | — | — | none |"]
    out += ["", "## Last-Modified changed between checks (lag above uses the earliest)", ""]
    out += [f"- {c['wfo']} {c['date']} {c['cycle']}Z: {' -> '.join(c['last_modified'])}"
            for c in rep["changed"]] or ["- none"]
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--offices", help="comma-separated subset (default: every office)")
    ap.add_argument("--min-interval", type=float, default=MIN_INTERVAL_S,
                    help=f"seconds between request starts (floor {MIN_INTERVAL_FLOOR_S})")
    ap.add_argument("--timeout", type=float, default=TIMEOUT_S)
    ap.add_argument("--report", nargs="+", metavar="LOG",
                    help="aggregate saved logs (files, directories, zips) instead of logging")
    ap.add_argument("--settle-hours", type=float, default=SETTLE_HOURS)
    ap.add_argument("--normal-fraction", type=float, default=NORMAL_FRACTION)
    args = ap.parse_args(argv)

    if args.report:
        records = [r for r in map(parse_line, iter_log_lines(args.report)) if r]
        print(render_report(build_report(records, args.settle_hours, args.normal_fraction)))
        return 0

    offices = sorted(WFO_TO_REGION)
    if args.offices:
        wanted = [o.strip().lower() for o in args.offices.split(",") if o.strip()]
        unknown = [o for o in wanted if o not in WFO_TO_REGION]
        if unknown:
            print(f"unknown office(s): {', '.join(unknown)}", file=sys.stderr)
            return 2
        offices = wanted

    records: list[dict] = []

    def emit(rec: dict) -> None:
        records.append(rec)
        print(record_line(rec), flush=True)

    pacer = Pacer(args.min_interval)
    started = _utcnow()
    print(f"# NWPS publication log: {len(offices)} office(s), one request per "
          f"{pacer.min_interval_s:g} s, started {iso(started)}", flush=True)
    statuses = observe(offices, make_fetch(args.timeout), pacer, emit)
    print("#", flush=True)
    for line in run_summary(records):
        print("# " + line, flush=True)
    stamped = sum(1 for r in records if r.get("kind") == "file" and r.get("last_modified"))
    print(f"# {sum(statuses.values())} requests in "
          f"{(_utcnow() - started).total_seconds():.0f} s; statuses as seen: "
          f"{dict(sorted(statuses.items()))}; {stamped} file timestamp(s) recorded", flush=True)
    if not stamped:
        # A run that recorded no publication time at all is a failed run, whatever the
        # reason — red, so it is noticed now rather than on the day the data is needed.
        print("# no file Last-Modified was recorded in this run", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
