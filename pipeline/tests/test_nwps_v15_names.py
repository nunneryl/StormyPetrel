"""NWPS v1.5 file names (NWS SCN 26-91, from the 2026-11-09 12Z run) and the per-office
freshness record that keeps a stale NWPS publish from being silent.

THE INCIDENT. NWPS v1.5 renamed every output file and NOMADS renamed the cycles it still
holds to match: box_nwps_CG1_20261110_0000.grib2 became nwps.t00z.cg1.box.grib2. The
pipeline kept asking grib_filter for the old name and got HTTP 500 ("Filename does not match
legal pattern"); from the 2026-11-09 23:23Z run every office fell back to older cycles, up to
30 h behind, or got none, and only per-attempt "returned 500" lines said so.

Every expected value is written by hand. The names are the ones NOMADS listed on 2026-11-10
(er.20261110/box/00/CG1/, pr.20261110/hfo/00/, wr.20261110/mfr/00/CG0/), and the error body
is the one grib_filter returned for box_nwps_CG1_20261110_0000.grib2.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from pipeline.forecast import nwps
from pipeline.forecast import nwps_nearshore as nn
from pipeline.forecast import nwps_trkng as nt

LEGAL_PATTERN_BODY = (
    '<!DOCTYPE html PUBLIC "-//W3C//DTD HTML 3.2 Final//EN"><html><head><title>Error</title>'
    '</head><body bgcolor="#ffffff"><font color=red><h2 align="center">Error</h2></font>\n'
    '<p align="center"> Filename does not match legal pattern: '
    'nwps.t\\d{2}z.cg\\d{1}.*(akq|box|car|chs|gyx|ilm|lwx|mhx|okx|phi).grib2$\n'
    '<p align=left><small>NCEP NOMADS Version 1.2<br></small></body></html>')
# The 1:00 run's 08:15:40Z start, 2026-11-10 — the run that read box's 11-09 00Z cycle.
NOW = datetime(2026, 11, 10, 8, 15, 40, tzinfo=timezone.utc)


# --------------------------------------------------------------------------- #
# 1 — the names the pipeline asks for                                          #
# --------------------------------------------------------------------------- #

def test_the_cg1_file_name_is_v15s():
    assert nwps._grib_filename("box", "20261110", "00") == "nwps.t00z.cg1.box.grib2"
    assert nwps._grib_filename("mfr", "20261109", "18") == "nwps.t18z.cg1.mfr.grib2"


def test_the_filter_asks_for_the_v15_name_in_the_unchanged_folder():
    params = nwps._filter_download_params("wr", "mfr", "20261110", "06")
    assert params["file"] == "nwps.t06z.cg1.mfr.grib2"
    assert params["dir"] == "/wr.20261110/mfr/06/CG1"
    assert (params["var_HTSGW"], params["lev_surface"]) == ("on", "on")


def test_the_full_file_url_is_v15s():
    assert nwps._direct_grib_url("er", "box", "20261110", "00") == (
        "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwps/prod/er.20261110/box/00/CG1/"
        "nwps.t00z.cg1.box.grib2")


# --------------------------------------------------------------------------- #
# 2 — the listings the diagnostics read                                        #
# --------------------------------------------------------------------------- #

def test_a_cg1_listing_finds_the_field_file_under_either_naming():
    assert nn.is_cg1_field_file("nwps.t00z.cg1.hfo.grib2") is True
    assert nn.is_cg1_field_file("box_nwps_CG1_20261108_0000.grib2") is True
    for other in ("nwps.t00z.5m_cg1_ripprob.hfo.txt", "nwps.t00z.spc2d_51003_cg1.hfo.txt",
                  "nwps.t00z.cg0_trkng.mfr.grib2", "nwps.t00z.cg0.trkng.mfr.grib2",
                  "mfr_nwps_CG0_Trkng_20261108_0000.grib2", "nwps.t00z.cg2.eka.grib2",
                  "nwps.t00z.cg1.box.grib2.idx", "../",
                  "nwps.t00z.cg10.box.grib2"):   # made up: a grid numbered 10 is not CG1
        assert nn.is_cg1_field_file(other) is False, other


def test_the_cg1_listing_returns_only_the_field_file(monkeypatch):
    listing = ["../", "nwps.t00z.5m_cg1_ripprob.hfo.txt", "nwps.t00z.cg1.hfo.grib2",
               "nwps.t00z.spc2d_51003_cg1.hfo.txt"]
    monkeypatch.setattr(nn, "_listdir", lambda url: listing)
    cg1, files = nn._cycle_files("hfo", "20261110", "00", "pr")
    assert cg1 == "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwps/prod/pr.20261110/hfo/00/CG1/"
    assert files == ["nwps.t00z.cg1.hfo.grib2"]


def test_the_tracking_file_under_either_naming(monkeypatch):
    assert nt.is_trkng_file("nwps.t00z.cg0_trkng.mfr.grib2") is True
    assert nt.is_trkng_file("nwps.t00z.cg0.trkng.mfr.grib2") is True
    assert nt.is_trkng_file("mfr_nwps_CG0_Trkng_20261108_0000.grib2") is True
    assert nt.is_trkng_file("nwps.t00z.cg1.mfr.grib2") is False
    assert nt.is_trkng_file("nwps.t00z.cg0_trkng.mfr.grib2.idx") is False
    base = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwps/prod/wr.20261110/mfr/00/CG0/"
    monkeypatch.setattr(nn, "_listdir", lambda url: ["../", "nwps.t00z.cg0_trkng.mfr.grib2"])
    assert nt._trkng_url("mfr", "20261110", "00", "wr") == base + "nwps.t00z.cg0_trkng.mfr.grib2"
    monkeypatch.setattr(nn, "_listdir", lambda url: [])      # listing unavailable
    assert nt._trkng_url("mfr", "20261110", "00", "wr") == base + "nwps.t00z.cg0_trkng.mfr.grib2"


# --------------------------------------------------------------------------- #
# 3 — one office's freshness                                                   #
# --------------------------------------------------------------------------- #

BOX_LISTED = [("20261110", "00"), ("20261109", "18"), ("20261109", "12"), ("20261109", "06"),
              ("20261109", "00")]
BOX_FAILED = [("20261110", "00", "HTTP 500"), ("20261109", "18", "HTTP 500"),
              ("20261109", "12", "HTTP 500"), ("20261109", "06", "HTTP 500")]


def test_the_0815_run_box_read_a_cycle_24_hours_behind():
    rec = nwps.freshness("box", BOX_LISTED, ("20261109", "00"), BOX_FAILED, NOW)
    # behind: 11-10 00Z - 11-09 00Z = 24 h. age: 08:15:40 on 11-10 - 00Z on 11-09
    # = 32 h 15 min 40 s = 32.261 h -> 32.3.
    assert rec == {"wfo": "box", "listing": "ok", "newest_listed": "2026-11-10T00:00:00Z",
                   "cycle_used": "2026-11-09T00:00:00Z", "source": "download",
                   "hours_behind": 24.0, "age_h": 32.3,
                   "failed": [{"cycle": "2026-11-10T00:00:00Z", "reason": "HTTP 500"},
                              {"cycle": "2026-11-09T18:00:00Z", "reason": "HTTP 500"},
                              {"cycle": "2026-11-09T12:00:00Z", "reason": "HTTP 500"},
                              {"cycle": "2026-11-09T06:00:00Z", "reason": "HTTP 500"}]}
    assert nwps.stale_reason(rec) == "24 h behind the newest listed (4 failed: HTTP 500)"


def test_only_failures_newer_than_the_cycle_read_are_listed():
    failed = [("20261110", "00", "HTTP 500"), ("20261109", "00", "HTTP 404")]
    rec = nwps.freshness("box", BOX_LISTED, ("20261109", "18"), failed, NOW)
    assert rec["failed"] == [{"cycle": "2026-11-10T00:00:00Z", "reason": "HTTP 500"}]
    assert rec["hours_behind"] == 6.0          # 11-10 00Z - 11-09 18Z
    assert rec["age_h"] == 14.3                # 14 h 15 min 40 s = 14.261 h


def test_the_newest_cycle_read_is_fresh():
    rec = nwps.freshness("mfr", [("20261110", "00"), ("20261109", "18")], ("20261110", "00"),
                         None, NOW)
    assert (rec["hours_behind"], rec["age_h"], rec["failed"]) == (0.0, 8.3, [])
    assert nwps.stale_reason(rec) is None


def test_a_cache_hit_says_so():
    rec = nwps.freshness("mfr", [("20261110", "00")], ("20261110", "00"), None, NOW, cached=True)
    assert rec["source"] == "cache"


def test_no_cycle_read():
    rec = nwps.freshness("ilm", BOX_LISTED, None, BOX_FAILED + [("20261109", "00", "HTTP 500")],
                         NOW)
    assert (rec["cycle_used"], rec["source"], rec["hours_behind"], rec["age_h"]) == (
        None, None, None, None)
    assert len(rec["failed"]) == 5             # every failure counts when none was read
    assert nwps.stale_reason(rec) == ("no cycle read, newest listed 2026-11-10T00:00:00Z "
                                      "(5 failed: HTTP 500)")


def test_mixed_reasons_are_named_once_each_in_order():
    failed = [("20261110", "00", "HTTP 500"), ("20261109", "18", "ReadTimeout"),
              ("20261109", "12", "HTTP 500")]
    rec = nwps.freshness("box", BOX_LISTED, ("20261109", "06"), failed, NOW)
    assert nwps.stale_reason(rec) == ("18 h behind the newest listed "
                                      "(3 failed: HTTP 500, ReadTimeout)")


def test_an_unanswered_or_empty_listing():
    unanswered = nwps.freshness("sju", None, None, None, NOW)
    assert (unanswered["listing"], unanswered["newest_listed"]) == ("unanswered", None)
    assert nwps.stale_reason(unanswered) == "no cycle read — NOMADS listing unanswered"
    empty = nwps.freshness("sju", [], None, None, NOW)
    assert empty["listing"] == "empty"
    assert nwps.stale_reason(empty) == "no cycle read — none listed"


def test_an_old_newest_cycle_warns_from_24_hours():
    listed = [("20261109", "08")]   # not a real NWPS hour; it puts the age on the threshold
    at = nwps.freshness("mfr", listed, ("20261109", "08"), None,
                        datetime(2026, 11, 10, 8, 0, 0, tzinfo=timezone.utc))
    assert (at["hours_behind"], at["age_h"]) == (0.0, 24.0)
    assert nwps.stale_reason(at) == "newest listed cycle is 24 h old"
    under = nwps.freshness("mfr", listed, ("20261109", "08"), None,
                           datetime(2026, 11, 10, 7, 54, 0, tzinfo=timezone.utc))
    assert under["age_h"] == 23.9             # 23 h 54 min
    assert nwps.stale_reason(under) is None
    assert nwps.NWPS_STALE_AGE_H == 24.0


# --------------------------------------------------------------------------- #
# 4 — the run's report                                                         #
# --------------------------------------------------------------------------- #

def test_the_report_logs_every_office_and_warns_once(caplog):
    caplog.set_level(logging.INFO, logger="pipeline.forecast.nwps")
    fresh = nwps.freshness("mfr", [("20261110", "00")], ("20261110", "00"), None, NOW)
    behind = nwps.freshness("box", BOX_LISTED, ("20261109", "00"), BOX_FAILED, NOW)
    none = nwps.freshness("sju", None, None, None, NOW)
    warning = nwps.report_freshness([fresh, behind, none])
    assert warning == ("::warning::NWPS stale this run for 2 of 3 offices (forecast rows keep "
                       "older cycles): box 24 h behind the newest listed (4 failed: HTTP 500); "
                       "sju no cycle read — NOMADS listing unanswered")
    msgs = [r.getMessage() for r in caplog.records]
    assert [m.split(" ")[0] for m in msgs if m.startswith("NWPSFRESH ")] == ["NWPSFRESH"] * 3
    assert ('NWPSFRESH {"age_h": 8.3, "cycle_used": "2026-11-10T00:00:00Z", "failed": [], '
            '"hours_behind": 0.0, "listing": "ok", "newest_listed": "2026-11-10T00:00:00Z", '
            '"source": "download", "wfo": "mfr"}') in msgs
    assert "nwps: box NWPS is stale this run: 24 h behind the newest listed (4 failed: HTTP 500)" \
        in msgs
    assert nwps.report_freshness([fresh]) is None


# --------------------------------------------------------------------------- #
# 5 — end to end through _locate_cycle and fetch                               #
# --------------------------------------------------------------------------- #

class _Resp:
    def __init__(self, status, text="", chunks=()):
        self.status_code, self.text, self.headers, self._chunks = status, text, {}, list(chunks)

    def iter_content(self, chunk_size=None):
        return iter(self._chunks)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Session:
    """grib_filter answers by file name: a 500 for every name in *refused*."""
    def __init__(self, refused):
        self.refused, self.asked = refused, []

    def get(self, url, params=None, stream=None, timeout=None):
        self.asked.append(params["file"])
        if params["file"] in self.refused:
            return _Resp(500, LEGAL_PATTERN_BODY)
        return _Resp(200, chunks=[b"g" * 2048])

    def head(self, url, timeout=None, allow_redirects=None):
        return _Resp(404)


def test_a_refused_newest_cycle_is_noted_and_the_next_is_read(monkeypatch, tmp_path, caplog):
    caplog.set_level(logging.INFO, logger="pipeline.forecast.nwps")
    sess = _Session({"nwps.t06z.cg1.mfr.grib2"})
    monkeypatch.setattr(nwps, "session", lambda: sess)
    monkeypatch.setattr(nwps, "NWPS_CACHE_DIR", tmp_path)
    monkeypatch.setattr(nwps, "candidate_cycles",
                        lambda wfo: [("20261110", "06"), ("20261110", "00")])
    for d in (nwps._CANDIDATES, nwps._FAILURES, nwps._READS):
        d.clear()
    path, date, hh = nwps._locate_cycle("mfr", use_cache=False)
    assert (path.name, date, hh) == ("mfr_20261110_00.grib2", "20261110", "00")
    assert sess.asked == ["nwps.t06z.cg1.mfr.grib2", "nwps.t00z.cg1.mfr.grib2"]
    assert nwps._CANDIDATES["mfr"] == [("20261110", "06"), ("20261110", "00")]
    assert nwps._FAILURES["mfr"] == [("20261110", "06", "HTTP 500")]
    assert ("nwps: grib_filter wr/mfr/2026111006Z returned 500: Error Error Filename does not match "
            "legal pattern: nwps.t\\d{2}z.cg\\d{1}.*(akq|box|car|chs|gyx|ilm|lwx|mhx|okx|phi)"
            ".grib2$ NCEP NOMADS Version 1.2") in [m.getMessage() for m in caplog.records]
    # ("Error Error": the page's <title> and its <h2>, each tag dropped to a space.)


def test_an_error_body_is_cut_to_200_characters_and_a_missing_one_is_empty():
    assert nwps._short_body(_Resp(500, "<p>" + "x" * 250 + "</p>")) == "x" * 200
    assert nwps._short_body(_Resp(500, "")) == ""
    assert nwps._short_body(object()) == ""          # no .text at all


def test_fetch_prints_one_warning_when_an_office_reads_nothing(monkeypatch, tmp_path, capsys):
    sess = _Session({"nwps.t00z.cg1.mfr.grib2", "nwps.t18z.cg1.mfr.grib2"})
    monkeypatch.setattr(nwps, "session", lambda: sess)
    monkeypatch.setattr(nwps, "NWPS_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(nwps, "NWPS_READS_FILE", tmp_path / "nwps_reads.json")
    monkeypatch.setattr(nwps, "NWPS_FORECAST_FILE", tmp_path / "nwps.json")
    monkeypatch.setattr(nwps, "apply_wfos", lambda spots: {"mfr": 1})
    monkeypatch.setattr(nwps, "candidate_cycles",
                        lambda wfo: [("20261110", "00"), ("20261109", "18")])
    nwps._FAILURES["stale"] = [("20261101", "00", "HTTP 500")]     # an earlier run's: cleared
    assert nwps.fetch([{"name": "Bandon Beach", "nwps_wfo": "mfr"}]) == {}
    printed = [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("::warning::")]
    assert printed == ["::warning::NWPS stale this run for 1 of 1 offices (forecast rows keep "
                       "older cycles): mfr no cycle read, newest listed 2026-11-10T00:00:00Z "
                       "(2 failed: HTTP 500)"]
    assert "stale" not in nwps._FAILURES


def test_fetch_prints_nothing_when_every_office_is_fresh(monkeypatch, tmp_path, capsys):
    """The cycle read is the newest listed; its age is under 24 h because it is made now."""
    now = datetime.now(timezone.utc)
    cyc = (now.strftime("%Y%m%d"), f"{now.hour:02d}")
    monkeypatch.setattr(nwps, "session", lambda: _Session(set()))
    monkeypatch.setattr(nwps, "NWPS_CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(nwps, "NWPS_READS_FILE", tmp_path / "nwps_reads.json")
    monkeypatch.setattr(nwps, "NWPS_FORECAST_FILE", tmp_path / "nwps.json")
    monkeypatch.setattr(nwps, "apply_wfos", lambda spots: {"mfr": 1})
    monkeypatch.setattr(nwps, "candidate_cycles", lambda wfo: [cyc])
    monkeypatch.setattr(nwps, "_open_grib_datasets", lambda path: [])   # nothing to extract
    nwps.fetch([{"name": "Bandon Beach", "nwps_wfo": "mfr"}])
    assert [ln for ln in capsys.readouterr().out.splitlines() if ln.startswith("::warning::")] == []
