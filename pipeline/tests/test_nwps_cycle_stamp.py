"""nwps_cycle travels from the fetcher to the database, and a skipped row is not an error.

The fetcher stamps every hour with its cycle (test_nwps_listing.py). These tests hold the
rest of the chain: interpret carries the stamp on every rated hour, db_import sends it on
every row, and when migration 019's trigger keeps a newer cycle's row, the rows PostgREST
did not apply are counted and logged — never raised, and never reported as written.

Every expected value is written by hand.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from pipeline import db_import
from pipeline import interpret as I

CYCLE = "2026-09-22T12:00:00Z"


# --------------------------------------------------------------------------- #
# 1 — interpret carries the stamp on every hour it rates                       #
# --------------------------------------------------------------------------- #

def _hour(vt, **kw):
    return {"valid_time": vt, "hs": 1.2, "swell_hs": 1.0, "tp": 12.0, "dp": 270.0,
            "nwps_cycle": CYCLE, **kw}


SPOT = {"name": "T", "lat": 36.0, "lng": -75.0, "orientation_deg": 270.0,
        "offshore_wind_deg": 90.0,
        "swell_window_arcs": [{"min": 200, "max": 340, "span": 144}],
        "optimal_swell_dir": 270}


def test_rate_spot_carries_the_cycle_on_every_hour():
    forecast = [_hour("2026-09-23T00:00:00Z"), _hour("2026-09-23T01:00:00Z")]
    rated = I.rate_spot(SPOT, forecast, None)
    assert [h["nwps_cycle"] for h in rated] == [CYCLE, CYCLE]


def test_compute_ratings_carries_it_through_the_whole_rating_pass():
    nwps = {"T": [_hour("2026-09-23T00:00:00Z")]}
    ratings = I.compute_ratings([SPOT], nwps, {}, {}, {})
    assert [h["nwps_cycle"] for h in ratings["T"]] == [CYCLE]


# --------------------------------------------------------------------------- #
# 2 — db_import sends it, and counts what the database applied                 #
# --------------------------------------------------------------------------- #

class _Result:
    def __init__(self, data):
        self.data = data


class _Table:
    """Returns only the rows whose spot_id is in `applied_ids` — what PostgREST does when
    the trigger skips the rest."""

    def __init__(self, client):
        self.client = client

    def upsert(self, chunk, **_kw):
        self.client.sent.extend(chunk)
        self._chunk = chunk
        return self

    def execute(self):
        if self.client.applied_ids is None:
            return object()                      # a response carrying no row list
        return _Result([r for r in self._chunk if r["spot_id"] in self.client.applied_ids])


class _Client:
    def __init__(self, applied_ids=None):
        self.sent = []
        self.applied_ids = applied_ids

    def table(self, _name):
        return _Table(self)


def _run(tmp_path, monkeypatch, client, caplog):
    ratings = {
        "A": [{"valid_time": "2026-09-23T00:00:00Z", "nwps_cycle": CYCLE, "stars": 2.0},
              {"valid_time": "2026-09-23T01:00:00Z", "nwps_cycle": CYCLE, "stars": 2.5}],
        "B": [{"valid_time": "2026-09-23T00:00:00Z", "nwps_cycle": "2026-09-21T18:00:00Z",
               "stars": 1.0}],
    }
    path = tmp_path / "ratings.json"
    path.write_text(json.dumps(ratings))
    monkeypatch.setattr(db_import, "_spot_id_map", lambda _c: {"A": 1, "B": 2})
    caplog.set_level(logging.INFO, logger=db_import.log.name)
    return db_import.import_forecasts(client, ratings_path=Path(path))


def test_every_row_sent_carries_its_hours_cycle(tmp_path, monkeypatch, caplog):
    client = _Client(applied_ids={1, 2})
    _run(tmp_path, monkeypatch, client, caplog)
    assert sorted((r["spot_id"], r["valid_time"], r["nwps_cycle"]) for r in client.sent) == [
        (1, "2026-09-23T00:00:00Z", CYCLE),
        (1, "2026-09-23T01:00:00Z", CYCLE),
        (2, "2026-09-23T00:00:00Z", "2026-09-21T18:00:00Z"),
    ]


def test_rows_the_trigger_kept_are_counted_logged_and_not_raised(tmp_path, monkeypatch, caplog):
    """Spot B came from an older cycle and the database kept its newer row: 3 sent, 2
    applied. The import returns 2, says why the third is missing, and does not fail."""
    client = _Client(applied_ids={1})
    applied = _run(tmp_path, monkeypatch, client, caplog)
    assert applied == 2
    assert len(client.sent) == 3
    assert "1 of 3 rows sent were NOT applied" in caplog.text
    assert not [r for r in caplog.records if r.levelno >= logging.ERROR]


def test_a_full_apply_says_nothing_about_kept_rows(tmp_path, monkeypatch, caplog):
    assert _run(tmp_path, monkeypatch, _Client(applied_ids={1, 2}), caplog) == 3
    assert "NOT applied" not in caplog.text


def test_a_response_without_rows_counts_as_applied_in_full(tmp_path, monkeypatch, caplog):
    assert _run(tmp_path, monkeypatch, _Client(applied_ids=None), caplog) == 3
    assert "NOT applied" not in caplog.text
