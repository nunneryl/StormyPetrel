"""A spot skipped by the forecasts name-join is NAMED, at WARNING level.

THE DEFECT. import_forecasts resolves each ratings.json key through _spot_id_map, a
name -> id map built from the spots table, and drops any spot the map does not answer for:

    spot_id = by_name.get(spot_name)
    if spot_id is None:
        skipped_unknown += 1
        continue

That drop was reported ONLY as an integer inside the existing summary log.info. A spot
that stops receiving forecasts entirely — its page renders empty — showed up as one digit
moving from 0 to 2 in an INFO line, with no name, no WARNING, and a green run.

HOW A SPOT FALLS OUT OF THE MAP. db_import upserts spots on the DERIVED slug
(_slugify(name)), while import_forecasts joins on the NAME. Rename a spot in
spots_enriched.json and the two disagree: the corrected record inserts as a NEW row under
its new slug, the old row is orphaned under the old one, and ratings.json — keyed by the
corrected name — no longer matches anything. That is exactly what happened to
"St Andews Park" and "Cape San B liss" (commit 9674d67), and it went unnoticed for three
weeks because nothing said their names out loud.

WHY THE ASSERTIONS ARE ON THE EMITTED RECORDS. The failure mode is silence, so the tests
capture the logger and assert on what was emitted. An assertion on the RETURN VALUE cannot
see it: import_forecasts returns the number of rows written, which is identical whether or
not the warning fires.

NO EXPECTED VALUE IS OBTAINED BY CALLING THE FUNCTION UNDER TEST. Every spot name, count
and substring asserted below is written literally into the fixture above the assertion.

Run: python -m pipeline.tests.test_forecast_skip_warning   (or pytest)
"""
from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path

from pipeline import db_import as D


# --------------------------------------------------------------------------- #
# A fake supabase client that records the query it was asked to build          #
# --------------------------------------------------------------------------- #
class RecordingQuery:
    """Records each builder call into `rec`; serves `spot_rows` from a select,
    and swallows upserts (this module asserts on logs, not on writes)."""

    def __init__(self, rec, spot_rows, upsert_raises=False):
        self.rec = rec
        self._spot_rows = spot_rows
        self._upsert_raises = upsert_raises
        self._served = False

    def select(self, cols):
        self.rec["select"].append(cols)
        return self

    def order(self, col, desc=False):
        self.rec["order"].append((col, desc))
        return self

    def range(self, start, end):
        self.rec["range"].append((start, end))
        return self

    def upsert(self, chunk, on_conflict=None):
        self.rec["upserts"].append((len(chunk), on_conflict))
        self.rec["upserted_spot_ids"].update(r["spot_id"] for r in chunk)
        if self._upsert_raises:
            raise RuntimeError("simulated PostgREST failure")
        return self

    def execute(self):
        # _spot_id_map pages until it gets a short page; serve the rows once, then empty.
        page = [] if self._served else list(self._spot_rows)
        self._served = True
        return type("Res", (), {"data": page})()


class RecordingClient:
    def __init__(self, spot_rows, upsert_raises=False):
        self.rec = {"tables": [], "select": [], "order": [], "range": [],
                    "upserts": [], "upserted_spot_ids": set()}
        self._spot_rows = spot_rows
        self._upsert_raises = upsert_raises

    def table(self, name):
        self.rec["tables"].append(name)
        return RecordingQuery(self.rec, self._spot_rows if name == "spots" else [],
                              upsert_raises=self._upsert_raises)


class CaptureLogs(logging.Handler):
    """Collects records off pipeline.db_import's logger for the duration of a call."""

    def __init__(self):
        super().__init__()
        self.records: list[logging.LogRecord] = []

    def emit(self, record):
        self.records.append(record)

    def at(self, level) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno == level]

    def at_or_above(self, level) -> list[str]:
        return [r.getMessage() for r in self.records if r.levelno >= level]


def _run(ratings: dict, spot_rows: list[dict], upsert_raises: bool = False):
    """Drive the real import_forecasts over a temp ratings.json and a recording client.

    Returns (written, capture, client); `written` is None when upsert_raises swallowed the
    return. The logger level is forced to INFO and restored, so the run does not depend on
    how the ambient logging config was left."""
    client = RecordingClient(spot_rows, upsert_raises=upsert_raises)
    cap = CaptureLogs()
    prev = D.log.level
    D.log.addHandler(cap)
    D.log.setLevel(logging.INFO)
    written = None
    try:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "ratings.json"
            p.write_text(json.dumps(ratings))
            try:
                written = D.import_forecasts(client, ratings_path=p)
            except RuntimeError:
                if not upsert_raises:
                    raise
    finally:
        D.log.setLevel(prev)
        D.log.removeHandler(cap)
    return written, cap, client


def _hours(valid_time: str) -> list[dict]:
    """One minimal ratings hour. Only valid_time is load-bearing for the join test."""
    return [{"valid_time": valid_time, "hs": 1.2, "stars": 3.0}]


# --------------------------------------------------------------------------- #
# 1 — a spot with no spots-table row is NAMED at WARNING level                 #
# --------------------------------------------------------------------------- #
def test_a_spot_absent_from_the_spots_table_is_named_in_a_warning():
    """ratings.json carries three spots; the spots table answers for only one of them.
    The two unmatched names are written literally here and must both appear in a WARNING.

    "Cape San B liss" and "St Andews Park" are the real orphans this warning exists for —
    both were in ratings.json under their CORRECTED spellings while the DB still held the
    misspelled rows, so the name join found nothing.
    """
    ratings = {
        "Ocean Beach SF":  _hours("2026-08-26T12:00:00Z"),
        "Cape San Blas":   _hours("2026-08-26T12:00:00Z"),
        "St Andrews Park": _hours("2026-08-26T12:00:00Z"),
    }
    spot_rows = [{"id": 11, "name": "Ocean Beach SF"}]   # the other two are absent

    written, cap, client = _run(ratings, spot_rows)

    warnings = cap.at(logging.WARNING)
    assert warnings, "a skipped spot must WARN — silence is the defect"
    joined = " | ".join(warnings)
    assert "Cape San Blas" in joined, joined
    assert "St Andrews Park" in joined, joined
    # the matched spot is NOT named — the warning lists what was dropped, not everything
    assert "Ocean Beach SF" not in joined, joined
    # and the surviving spot really did get its row, so the skip is selective
    assert client.rec["upserted_spot_ids"] == {11}, client.rec["upserted_spot_ids"]
    assert written == 1, written


def test_the_warning_states_the_count_and_the_consequence():
    """Two spots are skipped, so the count is the literal 2, and the message has to say
    what happens to them — a name with no consequence attached reads as informational."""
    ratings = {
        "Kept Spot":     _hours("2026-08-26T12:00:00Z"),
        "Missing One":   _hours("2026-08-26T12:00:00Z"),
        "Missing Two":   _hours("2026-08-26T12:00:00Z"),
    }
    _written, cap, _client = _run(ratings, [{"id": 7, "name": "Kept Spot"}])

    warnings = cap.at(logging.WARNING)
    assert len(warnings) == 1, warnings
    msg = warnings[0]
    assert msg.startswith("forecasts: "), msg
    assert "2 spot(s)" in msg, msg
    assert "no spots-table row" in msg, msg
    assert "NO forecast rows" in msg, msg
    assert "render empty" in msg, msg


def test_the_names_are_sorted_and_comma_joined():
    """Deterministic output: ratings.json insertion order is whatever interpret produced,
    so the names are sorted before joining. Fed deliberately out of order; the expected
    string is written literally, not produced by sorting the input here."""
    ratings = {
        "Zuma Beach":  _hours("2026-08-26T12:00:00Z"),
        "Aliso Creek": _hours("2026-08-26T12:00:00Z"),
        "Malibu Point": _hours("2026-08-26T12:00:00Z"),
    }
    _written, cap, _client = _run(ratings, [])   # spots table empty: all three skipped

    msg = cap.at(logging.WARNING)[0]
    assert msg.endswith("Aliso Creek, Malibu Point, Zuma Beach"), msg


# --------------------------------------------------------------------------- #
# 2 — nothing skipped means nothing warned                                     #
# --------------------------------------------------------------------------- #
def test_no_warning_at_all_when_every_spot_resolves():
    """The guard is a non-empty list, not a count that could be zero-but-truthy. Every
    ratings entry has a spots-table row, so the run must be WARNING-clean — a warning that
    fires on a healthy run is noise nobody reads on the run that matters."""
    ratings = {
        "Ocean Beach SF": _hours("2026-08-26T12:00:00Z"),
        "Steamer Lane":   _hours("2026-08-26T13:00:00Z"),
    }
    spot_rows = [{"id": 11, "name": "Ocean Beach SF"}, {"id": 12, "name": "Steamer Lane"}]

    written, cap, client = _run(ratings, spot_rows)

    assert cap.at_or_above(logging.WARNING) == [], cap.at_or_above(logging.WARNING)
    # both spots wrote a row, so the clean run is a real run and not an empty one
    assert client.rec["upserted_spot_ids"] == {11, 12}, client.rec["upserted_spot_ids"]
    assert written == 2, written


def test_an_empty_ratings_file_warns_about_nothing():
    """Zero ratings entries is a real state (interpret produced nothing). It must not warn
    about skipped spots — there were none to skip. Distinct from the case above, which has
    entries that all matched."""
    _written, cap, _client = _run({}, [{"id": 11, "name": "Ocean Beach SF"}])
    assert cap.at_or_above(logging.WARNING) == [], cap.at_or_above(logging.WARNING)


# --------------------------------------------------------------------------- #
# 3 — the warning is emitted BEFORE the upsert, so a failed write still names   #
#     what was dropped                                                          #
# --------------------------------------------------------------------------- #
def test_the_warning_survives_a_failing_upsert():
    """Placement, not just presence. The names are the diagnostic you most need on a run
    that ALSO failed to write — a PostgREST error, a dropped connection — because that run
    ends in a traceback and whatever was not yet logged is lost. Emitting after the upsert
    loop would pass every other test in this file and still lose the names exactly when
    they matter. The upsert here raises on its first chunk; the warning must already be out.
    """
    ratings = {
        "Kept Spot":   _hours("2026-08-26T12:00:00Z"),
        "Missing One": _hours("2026-08-26T12:00:00Z"),
    }
    _written, cap, client = _run(ratings, [{"id": 7, "name": "Kept Spot"}],
                                 upsert_raises=True)

    assert client.rec["upserts"], "the upsert must have been attempted, not skipped"
    warnings = cap.at(logging.WARNING)
    assert warnings, "the skip warning must be emitted BEFORE the upsert that failed"
    assert "Missing One" in " | ".join(warnings), warnings


# --------------------------------------------------------------------------- #
# 4 — the existing INFO summary is unchanged                                   #
# --------------------------------------------------------------------------- #
def test_the_existing_info_summary_still_reports_the_count():
    """The warning is ADDITIVE. The pre-existing summary line keeps its count so anything
    parsing the run log for it keeps working."""
    ratings = {
        "Kept Spot":   _hours("2026-08-26T12:00:00Z"),
        "Missing One": _hours("2026-08-26T12:00:00Z"),
    }
    _written, cap, _client = _run(ratings, [{"id": 7, "name": "Kept Spot"}])

    infos = [m for m in cap.at(logging.INFO) if m.startswith("forecasts: upserting")]
    assert len(infos) == 1, cap.at(logging.INFO)
    assert "1 spots in ratings.json had no spots-table row" in infos[0], infos[0]


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} forecast-skip-warning checks passed")


if __name__ == "__main__":
    _run_all()
