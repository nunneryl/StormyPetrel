"""pipeline/forecast/nwps.py: every NWPS cycle read is recorded, so a published forecast can be
traced to the copy of its cycle that was read (NOMADS can serve two; migration 028).

Every expected value is written by hand. The digest is FIPS 180-2's own test vector: SHA-256
of one million "a" bytes is cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from pipeline.forecast import nwps

MILLION_A_SHA256 = "cdc76e5c9914fb9281a1c7e284d73e67f1809a48a497200e046d39ccc7112cd0"
READ_AT = datetime(2026, 11, 8, 8, 23, 50, 400000, tzinfo=timezone.utc)
FULL_URL = ("https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwps/prod/wr.20261108/mfr/00/CG1/"
            "mfr_nwps_CG1_20261108_0000.grib2")


class _Resp:
    def __init__(self, status=200, headers=None, chunks=()):
        self.status_code, self.headers, self._chunks = status, dict(headers or {}), list(chunks)

    def iter_content(self, chunk_size=None):
        return iter(self._chunks)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _Session:
    def __init__(self, get, head=None):
        self._get, self._head, self.heads = get, head, []

    def get(self, url, params=None, stream=None, timeout=None):
        return self._get

    def head(self, url, timeout=None, allow_redirects=None):
        self.heads.append((url, timeout, allow_redirects))
        if isinstance(self._head, Exception):
            raise self._head
        return self._head


class _Now(datetime):
    @classmethod
    def now(cls, tz=None):
        return READ_AT


FILTER_HEADERS = {"Content-Type": "application/octet-stream",
                  "Content-Disposition": "attachment; filename=mfr_nwps_CG1_20261108_0000.grib2",
                  "Set-Cookie": "session=xyz"}
BODY = [b"a" * 400000, b"", b"a" * 600000]          # 1,000,000 bytes in two chunks


def _setup(monkeypatch, sess):
    monkeypatch.setattr(nwps, "session", lambda: sess)
    monkeypatch.setattr(nwps, "datetime", _Now)
    monkeypatch.setenv("GITHUB_RUN_ID", "42667013127")
    nwps._READS.clear()


def test_a_read_is_recorded_with_the_subset_and_the_full_files_head(monkeypatch, tmp_path, caplog):
    head = _Resp(200, {"Last-Modified": "Sun, 08 Nov 2026 07:05:08 GMT", "Content-Length": "43017415"})
    sess = _Session(_Resp(200, FILTER_HEADERS, BODY), head)
    _setup(monkeypatch, sess)
    caplog.set_level(logging.INFO, logger="pipeline.forecast.nwps")
    dest = tmp_path / "mfr_20261108_00.grib2"
    assert nwps._download_filtered("wr", "mfr", "20261108", "00", dest) is True
    assert dest.stat().st_size == 1000000
    want = {"wfo": "mfr", "nwps_cycle": "2026-11-08T00:00:00Z", "read_at": "2026-11-08T08:23:50Z",
            "run_id": "42667013127", "subset_bytes": 1000000, "subset_sha256": MILLION_A_SHA256,
            "filter_headers": {"Content-Type": "application/octet-stream",
                               "Content-Disposition": "attachment; filename=mfr_nwps_CG1_20261108_0000.grib2"},
            "file_last_modified": "2026-11-08T07:05:08Z", "file_bytes": 43017415, "head_error": None}
    assert nwps._READS == [want]
    assert sess.heads == [(FULL_URL, 60, True)]
    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("NWPSREAD ")]
    assert lines == ["NWPSREAD " + json.dumps(want, sort_keys=True)]


def test_a_failed_head_is_recorded_and_the_read_stands(monkeypatch, tmp_path):
    for head, err in ((ConnectionError("reset"), "ConnectionError: reset"),
                      (_Resp(403), "HTTP 403")):
        sess = _Session(_Resp(200, {}, BODY), head)
        _setup(monkeypatch, sess)
        assert nwps._download_filtered("wr", "mfr", "20261108", "00", tmp_path / "f.grib2") is True
        rec = nwps._READS[0]
        assert (rec["file_last_modified"], rec["file_bytes"], rec["head_error"]) == (None, None, err)
        assert rec["subset_sha256"] == MILLION_A_SHA256 and rec["filter_headers"] == {}


def test_a_head_without_the_headers_gives_none(monkeypatch, tmp_path):
    sess = _Session(_Resp(200, {}, BODY), _Resp(200, {"Content-Length": "4x"}))
    _setup(monkeypatch, sess)
    nwps._download_filtered("wr", "mfr", "20261108", "00", tmp_path / "f.grib2")
    rec = nwps._READS[0]
    assert (rec["file_last_modified"], rec["file_bytes"], rec["head_error"]) == (None, None, None)


def test_no_run_id_outside_actions(monkeypatch, tmp_path):
    sess = _Session(_Resp(200, {}, BODY), _Resp(200, {}))
    _setup(monkeypatch, sess)
    monkeypatch.delenv("GITHUB_RUN_ID")
    nwps._download_filtered("wr", "mfr", "20261108", "00", tmp_path / "f.grib2")
    assert nwps._READS[0]["run_id"] is None


def test_a_refused_or_tiny_download_is_not_a_read(monkeypatch, tmp_path):
    for resp in (_Resp(404, {}, BODY), _Resp(200, {}, [b"a" * 1023])):
        sess = _Session(resp, _Resp(200, {}))
        _setup(monkeypatch, sess)
        assert nwps._download_filtered("wr", "mfr", "20261108", "00", tmp_path / "f.grib2") is False
        assert nwps._READS == [] and sess.heads == []


def test_a_record_that_fails_never_costs_the_read(monkeypatch, tmp_path, caplog):
    sess = _Session(_Resp(200, {}, BODY), _Resp(200, {}))
    _setup(monkeypatch, sess)

    def boom(*a, **k):
        raise TypeError("bad header")
    monkeypatch.setattr(nwps, "_record_read", boom)
    dest = tmp_path / "f.grib2"
    assert nwps._download_filtered("wr", "mfr", "20261108", "00", dest) is True
    assert dest.stat().st_size == 1000000
    assert any("could not record the read of mfr/20261108 00Z: bad header" in r.getMessage()
               for r in caplog.records)


def test_http_dates():
    assert nwps._http_date("Sun, 08 Nov 2026 07:05:08 GMT") == "2026-11-08T07:05:08Z"
    assert nwps._http_date("Sun, 08 Nov 2026 08:05:08 +0100") == "2026-11-08T07:05:08Z"
    assert nwps._http_date(None) is None
    assert nwps._http_date("") is None
    assert nwps._http_date("not a date") is None


def test_the_reads_file_and_fetch_starts_it_afresh(monkeypatch, tmp_path):
    reads, out = tmp_path / "nwps_reads.json", tmp_path / "nwps.json"
    monkeypatch.setattr(nwps, "NWPS_READS_FILE", reads)
    monkeypatch.setattr(nwps, "NWPS_FORECAST_FILE", out)
    monkeypatch.setattr(nwps, "NWPS_CACHE_DIR", tmp_path / "cache")
    nwps._READS[:] = [{"wfo": "a"}, {"wfo": "b"}]
    assert nwps.write_reads() == 2
    assert json.loads(reads.read_text()) == [{"wfo": "a"}, {"wfo": "b"}]
    # a run's reads file holds only that run's reads: an earlier record is not carried over
    monkeypatch.setattr(nwps, "apply_wfos", lambda spots: {"mfr": 1})
    monkeypatch.setattr(nwps, "_locate_cycle", lambda wfo, use_cache: None)
    nwps.fetch([{"name": "Bandon Beach", "nwps_wfo": "mfr"}])
    assert json.loads(reads.read_text()) == []
    assert json.loads(out.read_text()) == {}


def test_the_reads_file_is_the_configured_one():
    from pipeline import config
    assert config.NWPS_READS_FILE == config.FORECAST_DATA_DIR / "nwps_reads.json"
