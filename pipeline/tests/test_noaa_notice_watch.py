"""scripts/noaa_notice_watch.py and .github/workflows/noaa-notices.yml: a weekly read of the NWS
service change notices, opening one issue per new notice that names a source the pipeline reads.

THE FIXTURE IS THE REAL PAGE'S OWN HTML: headings and items copied byte for byte from
https://www.weather.gov/notification/ as read on 2026-11-10, in the page's order, the layouts it
mixes included (the date inside the link, the id in its own link, "Jul &nbsp;6"). Every expected
value is written by hand from that HTML.
"""
from __future__ import annotations

import json
import os
import re
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import noaa_notice_watch as W  # noqa: E402

from pipeline.tests.test_ci_workflow import (  # noqa: E402
    job_key, jobs_of, permission_map, steps_of, yaml_code)

WORKFLOW = ROOT / ".github" / "workflows" / "noaa-notices.yml"

PAGE = """<h1 class='location-pagetitle'>National Service Change Notices and Public Statements</h1>
<h2><strong><a name="pns"></a>Public Information Statements 2026&nbsp;</strong></h2>
<ul>
\t<li>Sep 30:&nbsp;<a href="/media/notification/pdf_2026/pns26-70_HRRR_HRRRDAS_NOMADS_Product_Changes.pdf"><strong>PNS26-70</strong>: Soliciting Comments through <strong>October 30, 2026</strong> on Proposed Changes to NOMADS/FTPPRD-distributed HRRR/HRRRDAS CONUS Products</a></li>
\t<li>May 28:<strong>&nbsp;<a href="/media/notification/pdf_2026/pns26-44_experimental_alaska_heatrisk.pdf">PNS26-44</a></strong><a href="/media/notification/pdf_2026/pns26-44_experimental_alaska_heatrisk.pdf">: Soliciting Comments on the Experimental Alaska HeatRisk Product through <strong>September 30, 2026</strong></a></li>
\t<li>Feb 10:&nbsp;<a href="/media/notification/pdf_2026/pns26-11_AR_Datum_Change.pdf"><strong>PNS26-11</strong>: Soliciting Comments through<strong> March 13, 2026</strong> on Proposed Change to Water Level Datum Displayed on the National Water Prediction Service (NWPS) Webpage</a></li>
\t<li>Feb 06:&nbsp;<a href="/media/notification/pdf_2026/pns26-10_NWPSv1.5.0.pdf"><strong>PNS26-10</strong>: Soliciting Comments on the Upgrade of the Nearshore Wave Prediction System (NWPS) to Version 1.5.0 through <strong>March 16, 2026</strong></a></li>
</ul>
<h2><strong><a id="scn" name="scn"></a>Service Change Notices 2026&nbsp;</strong></h2>
<ul>
\t<li><a href="/media/notification/pdf_2026/scn26-98_STOFS_SHEF__bug_fix.pdf">Nov 02:&nbsp;<strong>SCN26-98</strong>: Surge and Tide Operational Forecast System (STOFS) Version 3.1 Bug Fix: Effective <strong>November 2, 2026</strong></a></li>
\t<li>Oct 27:&nbsp;<a href="/media/notification/pdf_2026/scn26-94_Obsproc.v1.3.0.pdf"><strong>SCN26-94</strong>: Disable the generation of zero-size BUFR dump files for GFS &amp; GDAS Effective <strong>October 26, 2026</strong></a></li>
\t<li>Oct 15:&nbsp;<a href="/media/notification/pdf_2026/scn26-048_Updated_RRFS_and_REFS_Implementation.aaf.pdf"><strong>SCN26-48 Updated</strong>: Implementation of the Rapid Refresh Forecast System (RRFS) and RRFS Ensemble Forecast System (REFS): Effective <strong>November 3, 2026</strong></a>&nbsp;</li>
\t<li>Oct 06:&nbsp;<a href="/media/notification/pdf_2026/scn26-91_nwps_1.5.0.pdf"><strong>SCN26-91</strong>: Upgrade Nearshore Wave Prediction System to v1.5: Effective on or about <strong>November 09, 2026</strong></a></li>
\t<li>Oct 06:&nbsp;<a href="/media/notification/pdf_2026/scn26-90_Station_reporting_changes_GOES-R%20ASOS_Satellite_Cloud_Products.pdf"><strong>SCN26-90</strong>: Station reporting changes in GOES-R ASOS Satellite Cloud Products on <strong>September 3, 2026</strong></a>&nbsp;</li>
\t<li>Jul &nbsp;6:&nbsp;<a href="/media/notification/pdf_2026/scn26-048_Updated_RRFS_and_REFS_Implementation.aaf.pdf"><strong>SCN26-48 Updated</strong>: Implementation of the Rapid Refresh Forecast System (RRFS) and RRFS Ensemble Forecast System (REFS): Effective <strong>November 3, 2026</strong></a>&nbsp;</li>
</ul>
<h2><strong><a id="scn" name="scn"></a>Service Change Notices 2025&nbsp;</strong></h2>
<ul>
\t<li>Sep 24:&nbsp;<a href="/media/notification/pdf_2025/scn25-66_Updated_nwps_1.4.20.aaa.pdf"><strong>SCN25-66&nbsp;Updated</strong>: Probabilistic Rip Risk, Wave Runup data, and Spectral point outputs moving to NOMADS: Effective <strong>October 15, 2025</strong></a></li>
</ul>
"""
BASE = "https://www.weather.gov/media/notification/"
RRFS = ("Implementation of the Rapid Refresh Forecast System (RRFS) and RRFS Ensemble Forecast "
        "System (REFS): Effective November 3, 2026")


# --------------------------------------------------------------------------- #
# 1 — reading the page                                                         #
# --------------------------------------------------------------------------- #

def test_every_item_is_read_with_its_headings_year_and_its_title():
    assert W.parse(PAGE) == [
        {"id": "PNS26-70", "date": "2026-09-30", "url": BASE + "pdf_2026/pns26-70_HRRR_HRRRDAS_NOMADS_Product_Changes.pdf",
         "title": "Soliciting Comments through October 30, 2026 on Proposed Changes to "
                  "NOMADS/FTPPRD-distributed HRRR/HRRRDAS CONUS Products"},
        {"id": "PNS26-44", "date": "2026-05-28", "url": BASE + "pdf_2026/pns26-44_experimental_alaska_heatrisk.pdf",
         "title": "Soliciting Comments on the Experimental Alaska HeatRisk Product through "
                  "September 30, 2026"},
        {"id": "PNS26-11", "date": "2026-02-10", "url": BASE + "pdf_2026/pns26-11_AR_Datum_Change.pdf",
         "title": "Soliciting Comments through March 13, 2026 on Proposed Change to Water Level "
                  "Datum Displayed on the National Water Prediction Service (NWPS) Webpage"},
        {"id": "PNS26-10", "date": "2026-02-06", "url": BASE + "pdf_2026/pns26-10_NWPSv1.5.0.pdf",
         "title": "Soliciting Comments on the Upgrade of the Nearshore Wave Prediction System "
                  "(NWPS) to Version 1.5.0 through March 16, 2026"},
        {"id": "SCN26-98", "date": "2026-11-02", "url": BASE + "pdf_2026/scn26-98_STOFS_SHEF__bug_fix.pdf",
         "title": "Surge and Tide Operational Forecast System (STOFS) Version 3.1 Bug Fix: "
                  "Effective November 2, 2026"},
        {"id": "SCN26-94", "date": "2026-10-27", "url": BASE + "pdf_2026/scn26-94_Obsproc.v1.3.0.pdf",
         "title": "Disable the generation of zero-size BUFR dump files for GFS & GDAS Effective "
                  "October 26, 2026"},
        {"id": "SCN26-48 Updated", "date": "2026-10-15",
         "url": BASE + "pdf_2026/scn26-048_Updated_RRFS_and_REFS_Implementation.aaf.pdf", "title": RRFS},
        {"id": "SCN26-91", "date": "2026-10-06", "url": BASE + "pdf_2026/scn26-91_nwps_1.5.0.pdf",
         "title": "Upgrade Nearshore Wave Prediction System to v1.5: Effective on or about "
                  "November 09, 2026"},
        {"id": "SCN26-90", "date": "2026-10-06",
         "url": BASE + "pdf_2026/scn26-90_Station_reporting_changes_GOES-R%20ASOS_Satellite_Cloud_Products.pdf",
         "title": "Station reporting changes in GOES-R ASOS Satellite Cloud Products on "
                  "September 3, 2026"},
        {"id": "SCN26-48 Updated", "date": "2026-07-06",
         "url": BASE + "pdf_2026/scn26-048_Updated_RRFS_and_REFS_Implementation.aaf.pdf", "title": RRFS},
        {"id": "SCN25-66 Updated", "date": "2025-09-24",
         "url": BASE + "pdf_2025/scn25-66_Updated_nwps_1.4.20.aaa.pdf",
         "title": "Probabilistic Rip Risk, Wave Runup data, and Spectral point outputs moving to "
                  "NOMADS: Effective October 15, 2025"},
    ]


def test_an_item_before_any_year_heading_or_without_a_date_or_id_is_skipped():
    assert W.parse('<li>Oct 06: <a href="/media/notification/pdf_2026/scn26-91.pdf">SCN26-91: x</a></li>') == []
    page = ('<h2>Service Change Notices 2026</h2>'
            '<li><a href="/media/notification/pdf_2026/scn26-91.pdf">SCN26-91: no date</a></li>'
            '<li>Oct 06: <a href="/media/notification/pdf_2026/notice.pdf">no id anywhere</a></li>'
            '<li>Feb 30: <a href="/media/notification/pdf_2026/scn26-1.pdf">SCN26-1: no such day</a></li>'
            '<li>Oct 06: <a href="/elsewhere/scn26-91.pdf">SCN26-91: not a notice link</a></li>')
    assert W.parse(page) == []


# --------------------------------------------------------------------------- #
# 2 — which notices count                                                      #
# --------------------------------------------------------------------------- #

def test_a_title_names_the_sources_it_touches_and_nwps_alone_is_not_one():
    assert W.topics_of("Upgrade Nearshore Wave Prediction System to v1.5") == [
        "NWPS (Nearshore Wave Prediction System)"]
    assert W.topics_of("Proposed Change to Water Level Datum Displayed on the National Water "
                       "Prediction Service (NWPS) Webpage") == []
    assert W.topics_of("Proposed Changes to NOMADS/FTPPRD-distributed HRRR/HRRRDAS CONUS Products") == [
        "HRRR / RRFS", "NOMADS / data delivery"]
    assert W.topics_of("Disable the generation of zero-size BUFR dump files for GFS & GDAS") == [
        "WAVEWATCH III / GFS-Wave"]
    assert W.topics_of("Surge and Tide Operational Forecast System (STOFS)") == ["CO-OPS tides"]
    assert W.topics_of("Changes to the NDBC realtime2 directory") == ["NDBC buoys"]
    assert W.topics_of("Enforcement of new cipher suite on some National Weather Service Sites") == [
        "NOMADS / data delivery"]
    assert W.topics_of("Experimental Alaska HeatRisk Product") == []


def test_the_last_fourteen_days_on_2026_11_10():
    got = W.recent_matches(W.parse(PAGE), date(2026, 11, 10))
    # 11-10 - 14 days = 10-27: SCN26-98 (11-02) and SCN26-94 (10-27, the edge) are in; the
    # RRFS update of 10-15 and SCN26-91 of 10-06 are older.
    assert [(n["key"], n["topics"]) for n in got] == [
        ("SCN26-98 2026-11-02", ["CO-OPS tides"]),
        ("SCN26-94 2026-10-27", ["WAVEWATCH III / GFS-Wave"])]
    assert W.DAYS == 14


def test_a_week_after_scn_26_91_was_posted_it_would_have_been_reported():
    got = W.recent_matches(W.parse(PAGE), date(2026, 10, 12))
    # 10-12 - 14 = 09-28: PNS26-70 (09-30), SCN26-91 (10-06); SCN26-90 names nothing we read
    assert [n["key"] for n in got] == ["PNS26-70 2026-09-30", "SCN26-91 2026-10-06"]
    # 10-12 - 6 days = 10-06, the edge: SCN26-91 alone
    assert W.recent_matches(W.parse(PAGE), date(2026, 10, 12), days=6) == [
        {**W.parse(PAGE)[7], "topics": ["NWPS (Nearshore Wave Prediction System)"],
         "key": "SCN26-91 2026-10-06"}]


def test_a_notice_dated_after_today_is_not_counted():
    assert W.recent_matches(W.parse(PAGE), date(2026, 10, 30), days=2) == []   # 10-28..10-30: none
    assert [n["key"] for n in W.recent_matches(W.parse(PAGE), date(2026, 10, 27), days=0)] == [
        "SCN26-94 2026-10-27"]


# --------------------------------------------------------------------------- #
# 3 — the run                                                                  #
# --------------------------------------------------------------------------- #

def _page_of(n_filler):
    """The fixture plus n_filler dated notices that name nothing we read (to pass the floor)."""
    filler = "".join(f'<li>Jan 05: <a href="/media/notification/pdf_2024/scn24-{i}.pdf">SCN24-{i}: '
                     f'Experimental product {i}</a></li>' for i in range(1, n_filler + 1))
    return PAGE + "<h2>Service Change Notices 2024</h2><ul>" + filler + "</ul>"


def test_a_run_prints_and_hands_the_notices_to_the_issue_step(tmp_path, capsys, monkeypatch):
    out = tmp_path / "github_output"
    monkeypatch.setenv("GITHUB_OUTPUT", str(out))
    assert W.main([], page=_page_of(39), today=date(2026, 11, 10)) == 0     # 11 + 39 = 50
    printed = capsys.readouterr().out.splitlines()
    assert printed[0] == ("50 dated notices on the page; 2 in the last 14 days name a source the "
                          "pipeline reads.")
    assert printed[1] == ("  2026-11-02  SCN26-98: Surge and Tide Operational Forecast System (STOFS) "
                          "Version 3.1 Bug Fix: Effective November 2, 2026  [CO-OPS tides]  "
                          + BASE + "pdf_2026/scn26-98_STOFS_SHEF__bug_fix.pdf")
    line = out.read_text()
    assert line.startswith("notices=") and line.endswith("\n") and line.count("\n") == 1
    assert [n["key"] for n in json.loads(line[len("notices="):])] == [
        "SCN26-98 2026-11-02", "SCN26-94 2026-10-27"]


def test_too_few_notices_fails_loudly(capsys, monkeypatch):
    monkeypatch.delenv("GITHUB_OUTPUT", raising=False)
    assert W.MIN_NOTICES == 50
    assert W.main([], page=_page_of(38), today=date(2026, 11, 10)) == 1        # 49
    assert capsys.readouterr().out == (
        "::error::https://www.weather.gov/notification/ gave 49 dated notices, fewer than 50: the "
        "page did not load or its layout changed, so new notices cannot be seen.\n")


def test_a_page_that_cannot_be_read_fails(capsys, monkeypatch):
    def boom(*a, **k):
        raise OSError("connection reset")
    monkeypatch.setattr(W, "fetch", boom)
    assert W.main([], today=date(2026, 11, 10)) == 1
    assert capsys.readouterr().out == (
        "::error::could not read https://www.weather.gov/notification/: OSError: connection reset\n")


def test_it_asks_politely_and_names_itself():
    assert W.URL == "https://www.weather.gov/notification/"
    assert W.USER_AGENT == "StormyPetrel-Pipeline/0.1 (+https://stormypetrel.surf)"


# --------------------------------------------------------------------------- #
# 4 — the workflow                                                             #
# --------------------------------------------------------------------------- #

def _watch_steps():
    code = yaml_code(WORKFLOW.read_text(encoding="utf-8"))
    return code, {keys.get("name"): (keys, chunk) for keys, chunk in steps_of(jobs_of(code)["watch"])}


def test_weekly_and_on_its_own_prs_with_no_issue_written_on_a_pr():
    code, steps = _watch_steps()
    assert re.search(r"^    - cron: '23 14 \* \* 1'$", code, re.M)
    assert steps["Read the notices (standard library only)"][0]["run"] == "python scripts/noaa_notice_watch.py"
    keys, chunk = steps["Open an issue for each new notice"]
    assert keys["if"] == "github.event_name != 'pull_request' && steps.watch.outcome == 'success'"
    assert "NOTICES: ${{ steps.watch.outputs.notices }}" in chunk, "the notices reach it through env"
    assert "${{" not in chunk.split("script: |", 1)[1], "nothing is spliced into the script"


def test_the_watch_job_may_read_the_repo_and_write_issues_and_nothing_else():
    code = yaml_code(WORKFLOW.read_text(encoding="utf-8"))
    assert permission_map(*job_key(jobs_of(code)["watch"], "permissions"), 6) == {
        "contents": "read", "issues": "write"}


def test_an_id_written_with_a_leading_zero_reads_as_the_notice_number():
    page = ('<h2>Service Change Notices 2026</h2>'
            '<li>Oct 15: <a href="/media/notification/pdf_2026/scn26-048_Updated_RRFS.pdf">Implementation '
            'of RRFS</a></li>')
    assert [(n["id"], n["title"]) for n in W.parse(page)] == [("SCN26-48", "Implementation of RRFS")]


def test_the_issue_step_skips_a_notice_already_in_an_open_or_closed_issue():
    _code, steps = _watch_steps()
    chunk = steps["Open an issue for each new notice"][1]
    assert "state: 'open', labels: LABEL" in chunk and "state: 'closed', labels: LABEL" in chunk
    assert "const markerRe = /<!-- noaa-notice:([^>]+?) -->/;" in chunk
    assert "`<!-- noaa-notice:${n.key} -->`," in chunk
    marker = re.compile(r"<!-- noaa-notice:([^>]+?) -->")
    assert marker.search("x\n<!-- noaa-notice:SCN26-48 Updated 2026-10-15 -->").group(1) == \
        "SCN26-48 Updated 2026-10-15"


def test_the_script_reads_one_page_and_writes_only_the_step_output():
    src = (ROOT / "scripts" / "noaa_notice_watch.py").read_text(encoding="utf-8")
    assert re.findall(r"urlopen\(", src) == ["urlopen("]
    assert re.findall(r"open\((\w+), \"a\"", src) == ["out"], "it appends to GITHUB_OUTPUT only"
    assert not re.search(r"^\s*(?:import|from)\s+(?:requests|pipeline)\b", src, re.M)
    assert os.path.exists(WORKFLOW)
