"""fetch_current_hour_ratings queries a BOUNDED forward window.

THE DEFECT. The query filtered `valid_time >= now()` and nothing else, so it selected every
future row — measured 120,812 across ~121 pages of 1000 — in order to use at most one per spot
(648). The other 99.5% was fetched, transferred, decoded and then dropped client-side by the
`sid in out` guard. Deep offset paging cost 6.8 ms at offset 1000 and 7342 ms at offset 86,000,
and the cumulative cost across the page sequence exceeded the server statement timeout. Two
callers run the function independently, so a full pipeline run paid it twice. A timeout failed
the snapshot step; db_import still wrote (its `!cancelled()` guard), but the revalidate step was
skipped and the live site served stale pages.

THE BOUND IS DERIVED FROM THE WRITE GRID, not guessed. Two writers populate `forecasts`,
but the query now reads only one of them:
    source='nwps'  db_import.import_forecasts — HOURLY, f000..f144         -> 1 h gaps
    source='ecmwf' ecmwf_wam.WAVE_STEPS       — 3-hourly to 144 h,          (FILTERED OUT
                                                6-hourly 150..240 h         by .eq source)
Worst consecutive-row gap among in-scope rows: 1 h. CURRENT_HOUR_WINDOW_HOURS is 12, i.e. 12x
that, so a spot with any live nwps forecast necessarily has rows inside the window. The 12 h
value was originally derived as 2x the 6 h ecmwf tail, back when this query returned both
writers; the source filter retired that derivation, and 12 is kept because the cost figures
below already justify it and narrowing is the dangerous direction.
    rows in window ~= 648 spots x 12 hourly = 7,776 -> 8 pages, max offset 7,000.
    Before the bound (and before the filter): 120,812 -> 121 pages, max offset 120,000.

WHY THE FILTER ASSERTIONS MATTER MOST. The absence of an upper bound IS the bug. A test that
exercises the function through a fake returning a handful of rows would pass with or without
`.lte(...)`, because the fake does not care. So the fake here RECORDS every filter it receives
and the tests assert on that record — removing the upper bound must fail a test.

Every expected value is hand-computed with the arithmetic in a comment; none is obtained by
calling the function under test.

Run: python -m pipeline.tests.test_revalidate_window   (or pytest)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pipeline import revalidate as R


# --------------------------------------------------------------------------- #
# A fake supabase client that records the query it was asked to build          #
# --------------------------------------------------------------------------- #
class FakeQuery:
    """Records each builder call, then serves `pages` in order from .execute()."""

    def __init__(self, recorder, pages):
        self.rec = recorder
        self._pages = pages

    def select(self, cols):
        self.rec["select"] = cols
        return self

    def gte(self, col, val):
        self.rec.setdefault("gte", []).append((col, val))
        return self

    def lte(self, col, val):
        self.rec.setdefault("lte", []).append((col, val))
        return self

    def lt(self, col, val):
        self.rec.setdefault("lt", []).append((col, val))
        return self

    def eq(self, col, val):
        self.rec.setdefault("eq", []).append((col, val))
        return self

    def order(self, col, desc=False):
        self.rec.setdefault("order", []).append((col, desc))
        return self

    def range(self, start, end):
        self.rec.setdefault("range", []).append((start, end))
        self._pending = (start, end)
        return self

    def execute(self):
        start, end = self._pending
        page = self._pages.pop(0) if self._pages else []
        self.rec.setdefault("pages_served", []).append((start, end, len(page)))
        return type("Res", (), {"data": page})()


class FakeClient:
    def __init__(self, pages=None):
        self.rec: dict = {}
        self._pages = list(pages or [])

    def table(self, name):
        self.rec.setdefault("tables", []).append(name)
        return FakeQuery(self.rec, self._pages)


def _row(spot_id, valid_time, stars, face):
    return {"spot_id": spot_id, "valid_time": valid_time,
            "stars": stars, "effective_size_ft": face}


def _iso(dt):
    return dt.isoformat()


# --------------------------------------------------------------------------- #
# 6 — BOTH bounds are applied. This is the regression that matters.            #
# --------------------------------------------------------------------------- #
def test_query_applies_both_a_lower_and_an_upper_time_bound():
    """The lower bound alone was the bug. Assert the recorded filters, not the output —
    output-only tests pass whether or not `.lte(...)` is there."""
    c = FakeClient(pages=[[]])
    before = datetime.now(timezone.utc)
    R.fetch_current_hour_ratings(c)
    after = datetime.now(timezone.utc)

    assert c.rec["tables"] == ["forecasts"], c.rec["tables"]
    assert c.rec["select"] == "spot_id,valid_time,stars,effective_size_ft"

    gte = c.rec.get("gte") or []
    lte = c.rec.get("lte") or []
    assert len(gte) == 1 and gte[0][0] == "valid_time", f"expected one gte on valid_time: {gte}"
    assert len(lte) == 1 and lte[0][0] == "valid_time", (
        "THE UPPER BOUND IS MISSING — this is the defect. Without it the query walks every "
        f"future row (120,812) to use 648. recorded lte filters: {lte}")

    lo = datetime.fromisoformat(gte[0][1])
    hi = datetime.fromisoformat(lte[0][1])
    # The lower bound is "now", taken inside the call, so it must land in [before, after].
    assert before <= lo <= after, f"lower bound {lo} outside the call window"
    # The upper bound is exactly CURRENT_HOUR_WINDOW_HOURS later. Hand arithmetic:
    #   hi - lo == timedelta(hours=12) == 43200 s exactly (no rounding anywhere in the code).
    assert (hi - lo) == timedelta(hours=12), f"span was {hi - lo}, want 12:00:00"
    assert (hi - lo).total_seconds() == 43200.0
    assert hi > lo, "the upper bound must be in the future, not the past"


def test_the_window_constant_is_named_and_at_least_twice_the_worst_grid_gap():
    """Pinned by construction, not preference. The query filters to source='nwps', whose grid
    is hourly over f000..f144, so the worst consecutive-row gap among in-scope rows is 1 h and
    12 h clears it 12x over. The 12 was first derived as 2x the 6 h ecmwf tail, which the
    source filter took out of scope; the FLOOR asserted here is kept at 12 anyway, because
    narrowing is the direction that fails silently — a spot that falls between rows never gets
    its page revalidated and quietly serves stale content."""
    assert R.CURRENT_HOUR_WINDOW_HOURS == 12
    assert R.CURRENT_HOUR_WINDOW_HOURS >= 12, (
        "the bound must not be narrowed below 12 h; too-narrow fails silently")
    # and the constant is what the query actually uses
    c = FakeClient(pages=[[]])
    R.fetch_current_hour_ratings(c)
    lo = datetime.fromisoformat(c.rec["gte"][0][1])
    hi = datetime.fromisoformat(c.rec["lte"][0][1])
    assert (hi - lo).total_seconds() == R.CURRENT_HOUR_WINDOW_HOURS * 3600.0


def test_ordering_and_paging_are_unchanged():
    """Ascending valid_time is what makes first-row-per-spot mean SOONEST. Page size stays
    1000; with the bound in place offset paging is deliberately kept (not keyset)."""
    c = FakeClient(pages=[[]])
    R.fetch_current_hour_ratings(c)
    assert c.rec["order"] == [("valid_time", False)], c.rec["order"]
    # first page is rows 0..999 inclusive -> range(0, 0 + 1000 - 1) == (0, 999)
    assert c.rec["range"][0] == (0, 999), c.rec["range"]


def test_paging_advances_by_the_page_size_and_stops_on_a_short_page():
    """Two full pages then a short one. Hand arithmetic for the offsets:
        page 0 -> range(0,   0 + 1000 - 1) = (0, 999)
        page 1 -> range(1000, 1000 + 999)  = (1000, 1999)
        page 2 -> range(2000, 2000 + 999)  = (2000, 2999)   short -> loop ends
    Distinct spot ids so nothing is deduped away; 1000 + 1000 + 3 = 2003 spots seen."""
    base = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    p0 = [_row(i, _iso(base), 3.0, 4.0) for i in range(1000)]
    p1 = [_row(1000 + i, _iso(base), 3.0, 4.0) for i in range(1000)]
    p2 = [_row(2000 + i, _iso(base), 3.0, 4.0) for i in range(3)]
    c = FakeClient(pages=[p0, p1, p2])
    out = R.fetch_current_hour_ratings(c)
    assert c.rec["range"] == [(0, 999), (1000, 1999), (2000, 2999)], c.rec["range"]
    assert len(out) == 2003, len(out)


# --------------------------------------------------------------------------- #
# 7 — first row per spot wins, and it is the EARLIEST                          #
# --------------------------------------------------------------------------- #
def test_earliest_row_per_spot_is_kept_and_later_rows_are_discarded():
    """Rows arrive ascending by valid_time, as the query orders them. For spot 1 the rows are
    12:00 stars 1.0, 13:00 stars 2.0, 14:00 stars 3.0 — the 12:00 row must win.
    For spot 2: 12:30 face 7.5 first, then 15:00 face 9.9 — the 12:30 row must win.
    Expected, by hand:
        {1: {"stars": 1.0, "face_ft": 4.4},
         2: {"stars": 5.0, "face_ft": 7.5}}
    """
    b = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    rows = [
        _row(1, _iso(b),                       1.0, 4.4),   # spot 1, earliest -> KEPT
        _row(2, _iso(b + timedelta(minutes=30)), 5.0, 7.5),  # spot 2, earliest -> KEPT
        _row(1, _iso(b + timedelta(hours=1)),  2.0, 5.5),   # spot 1, later -> discarded
        _row(1, _iso(b + timedelta(hours=2)),  3.0, 6.6),   # spot 1, later -> discarded
        _row(2, _iso(b + timedelta(hours=3)),  9.0, 9.9),   # spot 2, later -> discarded
    ]
    out = R.fetch_current_hour_ratings(FakeClient(pages=[rows]))
    assert out == {1: {"stars": 1.0, "face_ft": 4.4},
                   2: {"stars": 5.0, "face_ft": 7.5}}, out


def test_face_ft_is_read_from_effective_size_ft():
    """The returned key is face_ft; the column is effective_size_ft. Pin the mapping — they
    are different names and a silent swap would snapshot the wrong number."""
    b = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    rows = [{"spot_id": 7, "valid_time": _iso(b), "stars": 2.5,
             "effective_size_ft": 3.25, "face_ft": 999.0}]   # decoy face_ft column
    out = R.fetch_current_hour_ratings(FakeClient(pages=[rows]))
    assert out == {7: {"stars": 2.5, "face_ft": 3.25}}, out


def test_dedup_survives_a_page_boundary():
    """A spot's earliest row on page 0 must still beat a later row on page 1 — the guard is
    keyed on the accumulated dict, not per page. Spot 1: 12:00 (page 0) beats 14:00 (page 1)."""
    b = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    p0 = [_row(1, _iso(b), 1.0, 4.0)] + [_row(100 + i, _iso(b), 3.0, 4.0) for i in range(999)]
    p1 = [_row(1, _iso(b + timedelta(hours=2)), 8.0, 8.0)]
    out = R.fetch_current_hour_ratings(FakeClient(pages=[p0, p1]))
    assert len(p0) == 1000, "page 0 must be full so the loop continues to page 1"
    assert out[1] == {"stars": 1.0, "face_ft": 4.0}, out[1]


# --------------------------------------------------------------------------- #
# 8 — a spot with no row in the window is ABSENT, not present-with-nulls       #
# --------------------------------------------------------------------------- #
def test_a_spot_with_no_row_in_the_window_is_absent_not_null_valued():
    """Only spot 1 has a row. Spot 2 must not appear at all — the caller distinguishes
    "no forecast" (absent) from "a forecast whose value is null" (present, None), and
    _rating_changed treats old=None as a change, so a phantom null entry would revalidate
    a page that did not move."""
    b = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    out = R.fetch_current_hour_ratings(FakeClient(pages=[[_row(1, _iso(b), 2.0, 3.0)]]))
    assert out == {1: {"stars": 2.0, "face_ft": 3.0}}, out
    assert 2 not in out
    assert out.get(2) is None and len(out) == 1


def test_an_empty_window_yields_an_empty_mapping_not_an_error():
    """Zero rows in the window is a real state (nothing written this cycle). It returns {} —
    it does not raise, and does not fabricate entries."""
    out = R.fetch_current_hour_ratings(FakeClient(pages=[[]]))
    assert out == {}, out


def test_rows_without_a_spot_id_are_skipped():
    """A null spot_id cannot key the mapping. Hand-expected: only spot 5 survives."""
    b = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    rows = [{"spot_id": None, "valid_time": _iso(b), "stars": 1.0, "effective_size_ft": 1.0},
            {"valid_time": _iso(b), "stars": 2.0, "effective_size_ft": 2.0},
            _row(5, _iso(b), 3.0, 3.0)]
    out = R.fetch_current_hour_ratings(FakeClient(pages=[rows]))
    assert out == {5: {"stars": 3.0, "face_ft": 3.0}}, out


def test_a_row_present_with_null_columns_is_kept_as_null():
    """Distinct from the absent case above: the row EXISTS, its columns are null. The function
    reports it faithfully rather than dropping the spot.

    The row that used to reach this path was an ecmwf-source one (hs/tp/dp, no stars or
    effective_size_ft); the source filter now keeps those out of the result entirely. The
    behaviour is still pinned because nothing guarantees a source='nwps' row is fully
    populated — db_import passes h.get(...) straight through, so an interpret hour missing
    effective_size_ft writes an nwps row with a null in it."""
    b = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
    rows = [{"spot_id": 9, "valid_time": _iso(b), "stars": None, "effective_size_ft": None}]
    out = R.fetch_current_hour_ratings(FakeClient(pages=[rows]))
    assert out == {9: {"stars": None, "face_ft": None}}, out
    assert 9 in out, "present-with-nulls is NOT the same as absent"


def test_an_empty_window_is_logged_loudly_not_silently():
    """Requirement 2's handling. A spot absent from the result never gets its page
    revalidated, and in the old code that was completely silent — the run just posted fewer
    paths. An empty window (nothing written this cycle) must WARN, and a populated one must
    report its count, so a shortfall shows up in the run log instead of being inferred later
    from a stale page."""
    import logging

    class _Capture(logging.Handler):
        def __init__(self):
            super().__init__()
            self.records = []

        def emit(self, record):
            self.records.append(record)

    cap = _Capture()
    R.log.addHandler(cap)
    prev = R.log.level
    R.log.setLevel(logging.INFO)
    try:
        cap.records.clear()
        R.fetch_current_hour_ratings(FakeClient(pages=[[]]))
        warns = [r for r in cap.records if r.levelno >= logging.WARNING]
        assert warns, "an empty window must WARN — silence is the defect"
        assert "12h" in warns[0].getMessage(), warns[0].getMessage()

        cap.records.clear()
        b = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)
        R.fetch_current_hour_ratings(FakeClient(pages=[[_row(1, _iso(b), 2.0, 3.0)]]))
        infos = [r for r in cap.records if r.levelno == logging.INFO]
        assert infos, "a populated window must report its spot count"
        # exactly one spot in that fixture, so the message states 1
        assert "1 spots" in infos[0].getMessage(), infos[0].getMessage()
        assert not [r for r in cap.records if r.levelno >= logging.WARNING], \
            "a populated window must NOT warn"
    finally:
        R.log.setLevel(prev)
        R.log.removeHandler(cap)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} revalidate-window checks passed")


if __name__ == "__main__":
    _run_all()
