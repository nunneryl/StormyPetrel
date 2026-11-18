"""ecmwf-wam gives up on an unavailable ECMWF mirror after DOWNLOAD_BUDGET_S and ends green (#337).

On 2026-11-17 23:41Z the mirror answered 503 fifteen times; the client's defaults (500 retries,
120 s apart) ran the job into its 30-minute timeout, which cancelled it and opened a failure
issue. Expected values are written by hand.
"""
from __future__ import annotations

import sys
import time
import types

import pytest

from pipeline import ecmwf_wam as E


def test_the_limits():
    # 8 retries 60 s apart is at most 8 minutes of waiting, inside the 10-minute budget, which is
    # inside the job's 30-minute timeout.
    assert (E.MAX_RETRIES, E.RETRY_AFTER_S, E.DOWNLOAD_BUDGET_S) == (8, 60, 600)
    assert E.MAX_RETRIES * E.RETRY_AFTER_S < E.DOWNLOAD_BUDGET_S < 30 * 60


def test_the_client_is_built_with_the_capped_retries(monkeypatch, tmp_path):
    made = {}

    class FakeClient:
        def __init__(self, **kw):
            made.update(kw)

        def latest(self, **kw):
            raise ValueError("no cycle")
    monkeypatch.setitem(sys.modules, "ecmwf", types.ModuleType("ecmwf"))
    monkeypatch.setitem(sys.modules, "ecmwf.opendata", types.SimpleNamespace(Client=FakeClient))
    with pytest.raises(ValueError):
        E.download_latest_wave(tmp_path / "x.grib2")
    assert made == {"source": "aws", "maximum_retries": 8, "retry_after": 60,
                    "use_server_retry_after": False}


def test_deadline_raises_after_the_budget():
    t0 = time.monotonic()
    with pytest.raises(E.DownloadTimeout):
        with E.deadline(1):
            time.sleep(5)
    assert time.monotonic() - t0 < 3


def test_deadline_is_cleared_when_the_download_finishes():
    with E.deadline(1):
        pass
    time.sleep(1.5)                     # no stray alarm fires after the block


def _main_with(monkeypatch, download):
    written = []
    monkeypatch.setitem(sys.modules, "pipeline.db_import",
                        types.SimpleNamespace(get_client=lambda: object()))
    monkeypatch.setattr(E, "fetch_spots", lambda client: [{"id": 1}])
    monkeypatch.setattr(E, "download_latest_wave", download)
    monkeypatch.setattr(E, "upsert_forecasts", lambda client, rows: written.append(rows) or len(rows))
    return E.main([]), written


@pytest.mark.parametrize("why", [E.DownloadTimeout("download still running after 600 s"),
                                 OSError("503 Server Error: Service Unavailable"),
                                 ValueError("Cannot find latest")])
def test_an_unavailable_mirror_skips_the_cycle_green(monkeypatch, capsys, why):
    def fail(target):
        raise why
    code, written = _main_with(monkeypatch, fail)
    assert code == 0 and written == []
    out = capsys.readouterr().out
    assert out.startswith("::warning title=ecmwf-wam::ECMWF open data unavailable (")
    assert "skipped this cycle, nothing written" in out


def test_any_other_error_still_fails(monkeypatch):
    def broken(target):
        raise KeyError("a bug, not an outage")
    with pytest.raises(KeyError):
        _main_with(monkeypatch, broken)


def test_the_deadline_wraps_the_download_in_main(monkeypatch):
    monkeypatch.setattr(E, "DOWNLOAD_BUDGET_S", 1)
    code, written = _main_with(monkeypatch, lambda target: time.sleep(5))
    assert code == 0 and written == []
