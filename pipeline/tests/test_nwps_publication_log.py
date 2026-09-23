"""scripts/nwps_publication_log.py records NWPS publication times AS SEEN and writes nothing.

WHAT MUST HOLD, AND WHY EACH IS PINNED HERE RATHER THAN TRUSTED.

  AS SEEN. NOMADS answers 403, not 404, for a path that does not exist (every afc folder, on
  every date). A logger that read 403 as "not published" or "blocked" would put a conclusion
  into the record that the record cannot support. So a 403 — even one carrying a body that
  looks like a listing — is stored as the number 403 and nothing is parsed out of it.

  WRITES NOTHING, TOUCHES NO FORECAST ROW. The workflow must be safe before the 2026-10-06
  face-factor measurement. Checked two ways: the script's syntax tree may not open a file for
  writing or import anything that reaches a database or the pipeline's run path, and the
  workflow may hold no permission beyond reading the checkout and may name no secret.

  POLITE. NOMADS blocks clients that hammer it. The pacer's spacing is tested on a fake clock.

  THE REPORT. The 10-06 tables come from saved logs. The skipped-cycle rule is the delicate
  part: a cycle that is merely LATE must never be reported as skipped, and a 403 must never
  be read as evidence of anything. Both are exercised on hand-built runs.

Every expected value below is written out by hand, with the arithmetic in a comment; none is
obtained by calling the function under test.
"""
from __future__ import annotations

import ast
import io
import json
import os
import re
import sys
import zipfile
from collections import Counter
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))

import nwps_publication_log as P  # noqa: E402
from pipeline.tests.test_ci_workflow import yaml_code  # noqa: E402

SCRIPT = os.path.join(ROOT, "scripts", "nwps_publication_log.py")
WORKFLOW = os.path.join(ROOT, ".github", "workflows", "nwps-publication-log.yml")
BASE = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwps/prod"


def utc(*a):
    return datetime(*a, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 1 — parsing                                                                  #
# --------------------------------------------------------------------------- #

ROOT_HTML = """<html><body><h1>Index of /pub/data/nccf/com/nwps/prod</h1>
<a href="../">Parent Directory</a>
<a href="er.20260922/">er.20260922/</a> 22-Sep-2026 00:31 -
<a href="er.20260921/">er.20260921/</a> 21-Sep-2026 00:29 -
<a href="wr.20260922/">wr.20260922/</a> 22-Sep-2026 01:02 -
<a href="ar.20260922/">ar.20260922/</a> 22-Sep-2026 03:10 -
<a href="README">README</a>
</body></html>"""

LOX_HTML = """<a href="../">Parent Directory</a>
<a href="00/">00/</a> 22-Sep-2026 07:05 -
<a href="18/">18/</a> 23-Sep-2026 01:17 -
<a href="06/">06/</a> 22-Sep-2026 12:53 -
<a href="notes.txt">notes.txt</a>"""


def test_the_root_index_gives_each_regions_dates_oldest_first():
    assert P.parse_root(ROOT_HTML) == {"ar": ["20260922"],
                                       "er": ["20260921", "20260922"],
                                       "wr": ["20260922"]}


def test_an_office_index_gives_its_cycle_folders_oldest_first_and_nothing_else():
    assert P.parse_cycles(LOX_HTML) == ["00", "06", "18"]


def test_last_modified_is_read_as_utc_and_garbage_is_none():
    assert P.parse_http_date("Tue, 22 Sep 2026 12:51:07 GMT") == utc(2026, 9, 22, 12, 51, 7)
    assert P.parse_http_date("not a date") is None
    assert P.parse_http_date(None) is None
    assert P.parse_http_date("") is None


def test_lag_is_hours_after_the_nominal_cycle_time():
    # 06Z published 12:51:07 -> 6 h 51 m 07 s = 6 + 51/60 + 7/3600 = 6.85194 -> 6.852
    assert P.lag_hours("20260922", "06", utc(2026, 9, 22, 12, 51, 7)) == 6.852
    # 18Z published 01:30 the NEXT day -> 7.5 h: the date rolls over, the lag does not
    assert P.lag_hours("20260922", "18", utc(2026, 9, 23, 1, 30, 0)) == 7.5
    assert P.lag_hours("20260922", "18", None) is None


def test_a_record_survives_the_log_whatever_prefixes_it():
    rec = {"kind": "file", "wfo": "lox", "status": 403, "last_modified": None}
    line = P.record_line(rec)
    assert line.startswith("NWPSPUB {")
    # `gh run view --log` prefixes job and step; the Actions archive prefixes a timestamp
    gh = "log NWPS publication times (read-only)\tLog\t2026-09-24T00:41:12.1234567Z " + line
    archive = "2026-09-24T00:41:12.1234567Z " + line
    assert P.parse_line(gh) == rec
    assert P.parse_line(archive) == rec
    assert P.parse_line("# a human summary line") is None
    assert P.parse_line("NWPSPUB {not json") is None


# --------------------------------------------------------------------------- #
# 2 — the logger, against a fake NOMADS                                        #
# --------------------------------------------------------------------------- #

class FakeNomads:
    """Answers from a table of url -> Reply, recording every call in order."""

    def __init__(self, table, head_refused=False):
        self.table = table
        self.calls = []
        self.head_refused = head_refused

    def __call__(self, method, url, headers=None):
        self.calls.append((method, url, headers))
        if method == "HEAD" and self.head_refused:
            return P.Reply(405)
        return self.table.get(url, P.Reply(404))


class NoWait:
    def __init__(self):
        self.waits = 0

    def wait(self):
        self.waits += 1


def _clock():
    t = [utc(2026, 9, 24, 0, 41, 0)]

    def now():
        return t[0]
    return now


def _run(fake, offices):
    got = []
    pacer = NoWait()
    statuses = P.observe(offices, fake, pacer, got.append, now=_clock())
    return got, statuses, pacer


LOX_00 = f"{BASE}/wr.20260922/lox/00/CG1/lox_nwps_CG1_20260922_0000.grib2"
LOX_06 = f"{BASE}/wr.20260922/lox/06/CG1/lox_nwps_CG1_20260922_0600.grib2"
LOX_18 = f"{BASE}/wr.20260922/lox/18/CG1/lox_nwps_CG1_20260922_1800.grib2"


def _table():
    return {
        f"{BASE}/": P.Reply(200, text=ROOT_HTML),
        # afc: a 403 whose body LOOKS like a listing. Nothing may be parsed out of it.
        f"{BASE}/ar.20260922/afc/": P.Reply(403, text='<a href="12/">12/</a>'),
        f"{BASE}/wr.20260922/lox/": P.Reply(200, text=LOX_HTML),
        LOX_00: P.Reply(200, {"Last-Modified": "Tue, 22 Sep 2026 07:06:00 GMT",
                              "Content-Length": "4123456"}),
        LOX_06: P.Reply(403),
        LOX_18: P.Reply(None, error="ReadTimeout: read timed out"),
    }


def test_every_answer_is_recorded_as_seen_and_a_403_is_only_a_number():
    fake = FakeNomads(_table())
    got, statuses, _ = _run(fake, ["afc", "lox"])
    kinds = [(r["kind"], r.get("wfo"), r.get("cycle"), r["status"]) for r in got]
    assert kinds == [("root", None, None, 200),
                     ("listing", "afc", None, 403),
                     ("listing", "lox", None, 200),
                     ("file", "lox", "00", 200),
                     ("file", "lox", "06", 403),
                     ("file", "lox", "18", None)]
    afc = got[1]
    assert afc["cycles"] is None, "a 403 body was parsed as if it were a listing"
    assert got[2]["cycles"] == ["00", "06", "18"]
    ok, forbidden, silent = got[3], got[4], got[5]
    assert ok["last_modified"] == "2026-09-22T07:06:00Z"
    assert ok["lag_h"] == 7.1                  # 07:06 - 00:00 = 7 h 6 m = 7.1 h
    assert ok["bytes"] == 4123456
    assert (forbidden["last_modified"], forbidden["lag_h"]) == (None, None)
    assert silent["error"] == "ReadTimeout: read timed out"
    # AS SEEN: no record carries a verdict about what a status means
    for r in got:
        assert not {"published", "exists", "blocked", "missing", "available"} & set(r), r
    # 200: root + lox listing + one file; 403: afc listing + one file; one transport error
    assert dict(statuses) == {"200": 3, "403": 2, "error": 1}


def test_it_asks_for_the_file_the_pipeline_downloads_and_only_reads():
    fake = FakeNomads(_table())
    _run(fake, ["lox"])
    assert fake.calls[0] == ("GET", f"{BASE}/", None)
    assert ("HEAD", LOX_00, None) in fake.calls
    assert {m for m, _u, _h in fake.calls} <= {"GET", "HEAD"}


def test_a_server_that_refuses_head_is_asked_for_one_byte_and_both_answers_are_kept():
    table = _table()
    table[LOX_00] = P.Reply(206, {"Last-Modified": "Tue, 22 Sep 2026 07:06:00 GMT"})
    fake = FakeNomads(table, head_refused=True)
    got, _, _ = _run(fake, ["lox"])
    lox00 = [(r["method"], r["status"], r["last_modified"])
             for r in got if r["kind"] == "file" and r["cycle"] == "00"]
    assert lox00 == [("HEAD", 405, None), ("GET", 206, "2026-09-22T07:06:00Z")]
    assert ("GET", LOX_00, {"Range": "bytes=0-0"}) in fake.calls


def test_every_request_goes_through_the_pacer():
    fake = FakeNomads(_table())
    _, _, pacer = _run(fake, ["afc", "lox"])
    assert pacer.waits == len(fake.calls) == 6


def test_every_date_nomads_holds_is_listed_not_only_the_newest():
    root = ROOT_HTML.replace('<a href="wr.20260922/">',
                             '<a href="wr.20260921/">wr.20260921/</a>\n<a href="wr.20260922/">')
    fake = FakeNomads({f"{BASE}/": P.Reply(200, text=root)})
    _run(fake, ["lox"])
    listed = [u for _m, u, _h in fake.calls if u.endswith("/lox/")]
    assert listed == [f"{BASE}/wr.20260921/lox/", f"{BASE}/wr.20260922/lox/"]


def test_a_failed_root_listing_lists_nothing_else():
    fake = FakeNomads({f"{BASE}/": P.Reply(503)})
    got, statuses, _ = _run(fake, ["afc", "lox"])
    assert [(r["kind"], r["status"], r["dates"]) for r in got] == [("root", 503, None)]
    assert dict(statuses) == {"503": 1}


def test_a_403_root_is_not_parsed_even_when_its_body_looks_like_an_index():
    fake = FakeNomads({f"{BASE}/": P.Reply(403, text=ROOT_HTML)})
    got, _, _ = _run(fake, ["lox"])
    assert [(r["kind"], r["status"], r["dates"]) for r in got] == [("root", 403, None)]
    assert len(fake.calls) == 1


# --------------------------------------------------------------------------- #
# 3 — spacing                                                                  #
# --------------------------------------------------------------------------- #

def test_the_pacer_holds_request_starts_apart():
    now = [0.0]
    slept = []

    def clock():
        return now[0]

    def sleep(s):
        slept.append(round(s, 6))
        now[0] += s

    p = P.Pacer(1.0, clock=clock, sleep=sleep)
    p.wait()              # first request: no wait
    now[0] += 0.2         # it took 0.2 s
    p.wait()              # started 0.2 s after the last start -> sleep 0.8
    now[0] += 1.5         # a slow one: 1.5 s
    p.wait()              # already 1.5 s apart -> no sleep
    assert slept == [0.8]


def test_the_spacing_has_a_floor_whatever_the_flag_says():
    assert P.Pacer(0.1).min_interval_s == 0.5
    assert P.Pacer(2.0).min_interval_s == 2.0


# --------------------------------------------------------------------------- #
# 4 — writes nothing, reaches no database, runs no pipeline                    #
# --------------------------------------------------------------------------- #

def _script_tree():
    return ast.parse(open(SCRIPT, encoding="utf-8").read())


def test_the_script_opens_nothing_for_writing():
    for node in ast.walk(_script_tree()):
        if not isinstance(node, ast.Call):
            continue
        f = node.func
        name = f.attr if isinstance(f, ast.Attribute) else getattr(f, "id", None)
        assert name not in {"write_text", "write_bytes", "mkdir", "makedirs", "unlink",
                            "rmdir", "rmtree", "touch", "remove", "rename"}, name
        if name in {"open", "ZipFile"}:
            modes = [a.value for a in node.args[1:2] if isinstance(a, ast.Constant)]
            modes += [k.value.value for k in node.keywords
                      if k.arg == "mode" and isinstance(k.value, ast.Constant)]
            assert all(m in ("r", "rb") for m in modes), (name, modes)


def test_the_script_imports_nothing_that_reaches_a_database_or_runs_the_pipeline():
    imported = set()
    for node in ast.walk(_script_tree()):
        if isinstance(node, ast.Import):
            imported.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom):
            mod = node.module or ""
            imported.add(mod)
            imported.update(f"{mod}.{a.name}" for a in node.names)
    forbidden = {"supabase", "psycopg2", "subprocess", "pipeline.db_import",
                 "pipeline.interpret", "pipeline.revalidate", "pipeline.forecast.fetch_all"}
    assert not {m for m in imported if any(m == f or m.startswith(f + ".") for f in forbidden)}
    # the NWPS module is imported for its URL builder and regexes only
    assert "pipeline.forecast.nwps" in imported


def _workflow():
    return yaml_code(open(WORKFLOW, encoding="utf-8").read())


def test_the_workflow_can_only_read():
    text = _workflow()
    perms = text.split("\npermissions:", 1)[1].split("\n\n", 1)[0]
    assert re.findall(r"^\s+(\w[\w-]*):\s*(\w+)", perms, re.M) == [("contents", "read")]
    assert "secrets." not in text
    assert "write" not in perms


def test_the_workflow_runs_the_logger_and_nothing_of_the_pipeline():
    text = _workflow()
    runs = re.findall(r"^\s+run:\s*(.+)$", text, re.M)
    assert any("scripts/nwps_publication_log.py" in r for r in runs), runs
    for r in runs:
        for word in ("db_import", "interpret", "fetch_all", "revalidate", "supabase"):
            assert word not in r, r
    assert re.search(r"^\s+PYTHONDONTWRITEBYTECODE:\s*'1'", text, re.M)


def test_the_schedule_is_every_six_hours_and_the_pr_run_is_a_small_subset():
    text = _workflow()
    crons = re.findall(r"cron:\s*'([^']+)'", text)
    assert len(crons) == 1
    minute, hour, *_ = crons[0].split()
    assert hour == "*/6" and minute.isdigit() and minute != "0", crons
    assert re.search(r"^\s+paths:\s*$", text, re.M), "the PR trigger must be path-filtered"
    assert re.search(r"nwps_publication_log\.py --offices \w+,\w+$", text, re.M)


# --------------------------------------------------------------------------- #
# 5 — the report                                                               #
# --------------------------------------------------------------------------- #

RUN_A = "2026-09-23T00:41:00Z"   # 09-22 not settled yet (settles at 09-23 12:00)
RUN_B = "2026-09-23T18:41:00Z"   # everything settled


def _listing(at, wfo, date, status, cycles):
    return {"v": 1, "kind": "listing", "wfo": wfo, "region": "wr", "date": date,
            "status": status, "checked_at": at, "cycles": cycles}


def _file(at, wfo, date, hh, lm):
    return {"v": 1, "kind": "file", "wfo": wfo, "region": "wr", "date": date, "cycle": hh,
            "status": 200 if lm else 403, "checked_at": at, "last_modified": lm}


def _runs(include_b=True):
    recs = [{"v": 1, "kind": "root", "status": 200, "checked_at": RUN_A}]
    runs = [RUN_A] + ([RUN_B] if include_b else [])
    for at in runs:
        if at == RUN_B:
            recs.append({"v": 1, "kind": "root", "status": 200, "checked_at": RUN_B})
        # lox runs 00/06/12 nearly always; 18Z once in four dates, so 18Z is not "normal"
        recs.append(_listing(at, "lox", "20260919", 200, ["00", "06", "12"]))
        recs.append(_listing(at, "lox", "20260920", 200, ["00", "06", "12"]))
        recs.append(_listing(at, "lox", "20260921", 200, ["00", "06", "12"]))
        # the user's own case: lox's 09-22 folder lists 00, 06 and 18 only
        recs.append(_listing(at, "lox", "20260922", 200, ["00", "06", "18"]))
        recs.append(_listing(at, "afc", "20260922", 403, None))
        recs.append(_file(at, "lox", "20260921", "00", "2026-09-21T07:30:00Z"))
        recs.append(_file(at, "lox", "20260922", "00", "2026-09-22T07:06:00Z"))
        recs.append(_file(at, "lox", "20260922", "18", "2026-09-23T01:18:00Z"))
    # 06Z seen twice with DIFFERENT Last-Modified: a rewrite. The earliest is publication.
    recs.append(_file(RUN_A, "lox", "20260922", "06", "2026-09-22T12:54:00Z"))
    if include_b:
        recs.append(_file(RUN_B, "lox", "20260922", "06", "2026-09-22T13:30:00Z"))
    # a record in a format this reader does not know must not be counted
    recs.append({"v": 99, "kind": "file", "wfo": "lox", "date": "20260922", "cycle": "12",
                 "status": 200, "checked_at": RUN_A, "last_modified": "2026-09-22T23:59:00Z"})
    return recs


def test_the_lag_table_per_office_and_cycle():
    rep = P.build_report(_runs())
    rows = {(r["wfo"], r["cycle"]): r for r in rep["lags"]}
    # 00Z: 09-21 07:30 -> 7.5 h, 09-22 07:06 -> 7.1 h; median of (7.1, 7.5) = 7.3
    assert (rows["lox", "00"]["n"], rows["lox", "00"]["min"], rows["lox", "00"]["median"],
            rows["lox", "00"]["max"]) == (2, 7.1, 7.3, 7.5)
    # 06Z: earliest of 12:54 and 13:30 -> 6 h 54 m = 6.9 h
    assert (rows["lox", "06"]["n"], rows["lox", "06"]["median"]) == (1, 6.9)
    # 18Z published 01:18 the next day -> 7 h 18 m = 7.3 h
    assert rows["lox", "18"]["median"] == 7.3
    assert ("lox", "12") not in rows, "the unknown-format record was counted"
    assert rep["changed"] == [{"wfo": "lox", "date": "20260922", "cycle": "06",
                               "last_modified": ["2026-09-22T12:54:00Z",
                                                 "2026-09-22T13:30:00Z"]}]


def test_a_skipped_cycle_is_one_the_office_normally_runs_that_never_appeared():
    rep = P.build_report(_runs())
    # 00 and 06 on 4/4 settled dates, 12 on 3/4 = 0.75, 18 on 1/4 = 0.25 < 0.5
    assert rep["normal"] == {"lox": ["00", "06", "12"]}
    assert rep["skipped"] == [{"wfo": "lox", "date": "20260922", "cycle": "12",
                               "listed": ["00", "06", "18"]}]


def test_a_cycle_run_on_exactly_half_the_dates_counts_as_normal():
    recs = [{"v": 1, "kind": "root", "status": 200, "checked_at": RUN_B}]
    # four settled dates, 18Z on two of them: 2/4 = 0.5, which is "at least half"
    for date, cycles in (("20260919", ["00", "18"]), ("20260920", ["00", "18"]),
                         ("20260921", ["00"]), ("20260922", ["00"])):
        recs.append(_listing(RUN_B, "sew", date, 200, cycles))
    rep = P.build_report(recs)
    assert rep["normal"] == {"sew": ["00", "18"]}
    assert [(s["date"], s["cycle"]) for s in rep["skipped"]] == [("20260921", "18"),
                                                                 ("20260922", "18")]


def test_a_cycle_seen_in_any_200_listing_appeared_even_if_a_later_one_lacks_it():
    """"Never appeared" means no 200 listing of the date ever showed it. A folder seen at
    one check and gone at the next (purged, or the listing was partial) did appear."""
    recs = [{"v": 1, "kind": "root", "status": 200, "checked_at": RUN_B}]
    for date in ("20260919", "20260920", "20260921"):
        recs.append(_listing(RUN_B, "lox", date, 200, ["00", "12"]))
    recs.append(_listing(RUN_A, "lox", "20260922", 200, ["00", "12"]))  # early, unsettled
    recs.append(_listing(RUN_B, "lox", "20260922", 200, ["00"]))        # late, settled
    rep = P.build_report(recs)
    assert rep["normal"] == {"lox": ["00", "12"]}
    assert rep["skipped"] == []


def test_one_settled_date_cannot_say_what_is_normal():
    recs = [{"v": 1, "kind": "root", "status": 200, "checked_at": RUN_B},
            _listing(RUN_B, "sew", "20260921", 200, ["00", "12"])]
    rep = P.build_report(recs)
    assert rep["normal"] == {} and rep["skipped"] == []


def test_a_late_cycle_is_never_called_skipped():
    """Run A alone saw 09-22 at 00:41 the next day — before it settled at 12:00. The 12Z
    folder could still have appeared, so 09-22 must be undetermined, not skipped."""
    rep = P.build_report(_runs(include_b=False))
    assert rep["skipped"] == []
    lox22 = [u for u in rep["undetermined"] if (u["wfo"], u["date"]) == ("lox", "20260922")]
    assert lox22 == [{"wfo": "lox", "date": "20260922", "statuses": {"200": 1},
                      "why": f"last 200 listing at {RUN_A}, before the date settled"}]


def test_a_403_is_never_read_as_evidence():
    rep = P.build_report(_runs())
    afc = [u for u in rep["undetermined"] if u["wfo"] == "afc"]
    assert afc == [{"wfo": "afc", "date": "20260922", "statuses": {"403": 2},
                    "why": "no 200 listing"}]
    assert "afc" not in rep["normal"]
    assert not [s for s in rep["skipped"] if s["wfo"] == "afc"]


def test_the_report_reads_gh_logs_and_actions_archives_from_a_directory(tmp_path, capsys):
    lines = [P.record_line(r) for r in _runs()]
    half = len(lines) // 2
    (tmp_path / "logs").mkdir()
    gh_log = "\n".join("log\tLog publication times\t2026-09-23T18:41:01.0000000Z " + l
                       for l in lines[:half])
    (tmp_path / "logs" / "1.log").write_text("# a summary line\n" + gh_log + "\n")
    buf = io.BytesIO()
    # DEFLATED, as GitHub's archives are: a stored zip holds its text verbatim, and reading
    # it as a plain file would then "work" and hide a reader that cannot open a zip.
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("log/2_Log.txt",
                   "\n".join("2026-09-23T18:41:01.0000000Z " + l for l in lines[half:]))
    (tmp_path / "logs" / "logs_2.zip").write_bytes(buf.getvalue())

    assert P.main(["--report", str(tmp_path / "logs")]) == 0
    out = capsys.readouterr().out
    assert "| lox | 20260922 | 12Z | 00, 06, 18 |" in out
    assert "| lox | 00Z | 2 | 7.10 | 7.30 | 7.50 |" in out
    assert "| afc | 20260922 | 403×2 | no 200 listing |" in out
    assert "lox 20260922 06Z: 2026-09-22T12:54:00Z -> 2026-09-22T13:30:00Z" in out


# --------------------------------------------------------------------------- #
# 6 — the command line                                                         #
# --------------------------------------------------------------------------- #

class _InstantPacer(P.Pacer):
    def __init__(self, min_interval_s):
        super().__init__(min_interval_s, sleep=lambda s: None)


def _main_with(monkeypatch, table, argv):
    monkeypatch.setattr(P, "make_fetch", lambda timeout_s: FakeNomads(table))
    monkeypatch.setattr(P, "Pacer", _InstantPacer)
    return P.main(argv)


def test_a_run_that_recorded_a_publication_time_succeeds(monkeypatch, capsys):
    assert _main_with(monkeypatch, _table(), ["--offices", "lox"]) == 0
    out = capsys.readouterr().out
    records = [r for r in map(P.parse_line, out.splitlines()) if r]
    assert Counter(r["kind"] for r in records) == {"root": 1, "listing": 1, "file": 3}
    assert "# lox  wr.20260922  listing 200  00Z +7.1h  06Z no Last-Modified" in out


def test_a_run_that_recorded_no_publication_time_fails_loudly(monkeypatch, capsys):
    assert _main_with(monkeypatch, {f"{BASE}/": P.Reply(503)}, ["--offices", "lox"]) == 1
    assert "no file Last-Modified was recorded" in capsys.readouterr().err


def test_an_unknown_office_is_refused_rather_than_guessed(capsys):
    assert P.main(["--offices", "lox,zzz"]) == 2
    assert "zzz" in capsys.readouterr().err
