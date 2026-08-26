"""Every Python reader of `forecasts` filters to source='nwps'.

THE DEFECT. `forecasts` is keyed UNIQUE(spot_id, valid_time, source) and has two writers:

    source='nwps'   db_import.import_forecasts  — the rated feed. Every column: stars,
                    effective_size_ft, face_ft, wind, tide, the lot.
    source='ecmwf'  ecmwf_wam.upsert_forecasts  — a raw second-opinion wave feed. Writes
                    spot_id, valid_time, hs, tp, dp, source, fetched_at and NOTHING else,
                    on ecmwf_wam.WAVE_STEPS: 3-hourly to 144 h, 6-hourly to 240 h.

No reader filtered on `source`, so both feeds came back from every query. ecmwf_wam's step
ladder is a multiple of 3 for its entire length, so whenever a spot's soonest future hour
landed on a multiple of 3 the two writers produced rows with the SAME valid_time, and the
readers here — both of which mean "the soonest row per spot" — could pick the unrated one.
Nothing in either function breaks that tie; see the two characterisation tests at the end,
which pin that the query filter is the ONLY defence.

WHY THE ASSERTIONS ARE ON THE RECORDED QUERY, NOT ON THE OUTPUT. Absence of the filter IS
the bug. A test that drives these functions through a fake returning a handful of rows
passes with or without `.eq("source", "nwps")`, because the fake serves whatever it was
given regardless of what was asked for. So the fake here RECORDS every builder call and the
tests assert on that record: deleting the filter from either reader must fail a test.

WHY NOT A stars-IS-NOT-NULL TEST. It would work today and for the wrong reason. interpret
writes `stars = composite_stars(...) if fft is not None else 0.0` — 0.0, never None — so a
nwps row's stars is never null, and a null test separates the two feeds only as a side
effect of which columns ecmwf_wam happens to populate. Give that module a stars column and
the proxy silently stops discriminating. test_the_filter_is_on_source_not_on_a_null_column
pins the mechanism, not just the outcome.

Run: python -m pipeline.tests.test_source_filter   (or pytest)
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from pipeline import daily_report as D
from pipeline import revalidate as R


# --------------------------------------------------------------------------- #
# A fake supabase client that records the query it was asked to build          #
# --------------------------------------------------------------------------- #
class _Not:
    """Stands in for supabase-py's `.not_` negation namespace, so a reader rewritten
    as `.not_.is_("stars", "null")` is RECORDED rather than raising AttributeError."""

    def __init__(self, query):
        self._q = query

    def is_(self, col, val):
        self._q.rec["not_is"].append((col, val))
        return self._q

    def eq(self, col, val):
        self._q.rec["not_eq"].append((col, val))
        return self._q


class RecordingQuery:
    """Records each builder call into `rec`, then serves `pages` in order from execute()."""

    def __init__(self, rec, pages):
        self.rec = rec
        self._pages = pages
        self._pending = (None, None)

    def select(self, cols):
        self.rec["select"] = cols
        return self

    def eq(self, col, val):
        self.rec["eq"].append((col, val))
        return self

    def neq(self, col, val):
        self.rec["neq"].append((col, val))
        return self

    def gte(self, col, val):
        self.rec["gte"].append((col, val))
        return self

    def lte(self, col, val):
        self.rec["lte"].append((col, val))
        return self

    def is_(self, col, val):
        self.rec["is_"].append((col, val))
        return self

    @property
    def not_(self):
        return _Not(self)

    def order(self, col, desc=False):
        self.rec["order"].append((col, desc))
        return self

    def limit(self, n):
        self.rec["limit"].append(n)
        return self

    def range(self, start, end):
        self.rec["range"].append((start, end))
        self._pending = (start, end)
        return self

    def execute(self):
        page = self._pages.pop(0) if self._pages else []
        self.rec["pages_served"].append((self._pending, len(page)))
        return type("Res", (), {"data": page})()


def _blank_rec(name):
    return {"table": name, "select": None, "eq": [], "neq": [], "gte": [], "lte": [],
            "is_": [], "not_is": [], "not_eq": [], "order": [], "limit": [],
            "range": [], "pages_served": []}


class RecordingClient:
    """One record per .table() call, in call order. `pages` is a per-table queue, so a
    reader that pages three times gets three RecordingQuery objects sharing that queue —
    which is exactly how the real readers rebuild the chain on each loop iteration."""

    def __init__(self, pages=None, table="forecasts"):
        self.queries: list[dict] = []
        self._queues: dict[str, list] = {table: list(pages or [])}

    def table(self, name):
        rec = _blank_rec(name)
        self.queries.append(rec)
        return RecordingQuery(rec, self._queues.setdefault(name, []))

    def forecast_queries(self) -> list[dict]:
        return [q for q in self.queries if q["table"] == "forecasts"]


# --------------------------------------------------------------------------- #
# Fixtures                                                                     #
# --------------------------------------------------------------------------- #
BASE = datetime(2026, 8, 20, 12, 0, tzinfo=timezone.utc)

# The exact projections the two readers ask for. NEITHER INCLUDES `source`: these rows
# arrive at the calling code with no discriminator on them at all, which is precisely why
# the filter has to be in the query and cannot be recovered downstream.
R_SELECT = "spot_id,valid_time,stars,effective_size_ft"
D_SELECT = D._FCAST_COLS


def _iso(dt):
    return dt.isoformat()


def _project(row: dict, select: str) -> dict:
    """What PostgREST actually hands back: exactly the named columns, no more. A column
    the writer never populated comes back as null rather than as a missing key, so this
    fills absent columns with None instead of dropping them."""
    return {c.strip(): row.get(c.strip()) for c in select.split(",")}


def _nwps_row(spot_id, dt):
    """The whole-table shape db_import writes: every rated column populated."""
    return {"spot_id": spot_id, "valid_time": _iso(dt), "source": "nwps",
            "hs": 1.5, "swell_hs": 1.4, "tp": 12.0, "dp": 200.0,
            "swell_tp": 12.0, "swell_dp": 200.0, "wind_speed": 3.0, "wind_dir": 250.0,
            "face_ft": 4.0, "stars": 3.5, "effective_size_ft": 4.0, "tide_level_ft": 2.0}


def _ecmwf_row(spot_id, dt):
    """The whole-table shape ecmwf_wam writes: hs/tp/dp and nothing else. Every rated
    column is left NULL by that writer."""
    return {"spot_id": spot_id, "valid_time": _iso(dt), "source": "ecmwf",
            "hs": 1.9, "swell_hs": None, "tp": 9.0, "dp": 210.0,
            "swell_tp": None, "swell_dp": None, "wind_speed": None, "wind_dir": None,
            "face_ft": None, "stars": None, "effective_size_ft": None,
            "tide_level_ft": None}


# --------------------------------------------------------------------------- #
# 1 — the filter is present, in both readers. This is the regression.          #
# --------------------------------------------------------------------------- #
def test_revalidate_fetch_current_hour_ratings_filters_source_to_nwps():
    """Deleting `.eq("source", "nwps")` from revalidate.fetch_current_hour_ratings must
    fail HERE. Assert the recorded filter, not the returned mapping — the fake serves the
    rows it was handed either way."""
    c = RecordingClient(pages=[[]])
    R.fetch_current_hour_ratings(c)

    qs = c.forecast_queries()
    assert len(qs) == 1, f"expected one forecasts query for an empty first page: {len(qs)}"
    assert ("source", "nwps") in qs[0]["eq"], (
        "THE SOURCE FILTER IS MISSING — this is the defect. Without it the query returns "
        "ecmwf rows too, and 'first row per spot wins' can hand the caller an unrated row. "
        f"recorded eq filters: {qs[0]['eq']}")


def test_daily_report_fetch_forecasts_window_filters_source_to_nwps():
    """Same regression, other reader. Deleting `.eq("source", "nwps")` from
    daily_report.fetch_forecasts_window must fail HERE."""
    c = RecordingClient(pages=[[]])
    D.fetch_forecasts_window(c)

    qs = c.forecast_queries()
    assert len(qs) == 1, f"expected one forecasts query for an empty first page: {len(qs)}"
    assert ("source", "nwps") in qs[0]["eq"], (
        "THE SOURCE FILTER IS MISSING — this is the defect. Without it `latest` and "
        "`plus24` can each land on an ecmwf row, which _build_user_prompt reads as "
        f"★0.0 with a '—' face. recorded eq filters: {qs[0]['eq']}")


# --------------------------------------------------------------------------- #
# 2 — the filter value is exact. Postgres text equality is case-sensitive.     #
# --------------------------------------------------------------------------- #
def test_the_filter_value_is_the_exact_lowercase_literal_both_writers_use():
    """db_import.py writes the literal "nwps" and ecmwf_wam.py the literal "ecmwf" —
    lowercase, no whitespace. `source = 'NWPS'` matches nothing in Postgres and would
    empty every result set silently, so pin the byte-for-byte value rather than a
    case-folded comparison."""
    for reader, name in ((R.fetch_current_hour_ratings, "revalidate"),
                         (D.fetch_forecasts_window, "daily_report")):
        c = RecordingClient(pages=[[]])
        reader(c)
        src = [v for (col, v) in c.forecast_queries()[0]["eq"] if col == "source"]
        assert src == ["nwps"], f"{name}: expected exactly one source filter =='nwps', got {src}"
        assert src[0] == src[0].lower() and src[0].strip() == src[0], name


def test_the_filter_column_is_source_not_swell_source():
    """`forecasts` has TWO columns whose name ends in 'source': `source` (the writer
    discriminator, this filter) and `swell_source` (which feed drove the rating —
    'ww3'/'nwps'/'cdip_mop', written by interpret on nwps rows only). Filtering the wrong
    one would compile, run, and quietly return a subset of the nwps rows."""
    for reader, name in ((R.fetch_current_hour_ratings, "revalidate"),
                         (D.fetch_forecasts_window, "daily_report")):
        c = RecordingClient(pages=[[]])
        reader(c)
        cols = [col for (col, _v) in c.forecast_queries()[0]["eq"]]
        assert "source" in cols, f"{name}: {cols}"
        assert "swell_source" not in cols, (
            f"{name}: filtered swell_source, which is a different column: {cols}")


# --------------------------------------------------------------------------- #
# 3 — the mechanism is the discriminator column, not a null proxy              #
# --------------------------------------------------------------------------- #
def test_the_filter_is_on_source_not_on_a_null_column():
    """A `stars is not null` test would pass a behavioural check today and be wrong for
    the wrong reason: interpret.py writes `stars = ... if fft is not None else 0.0`, so a
    nwps row's stars is NEVER null, and the proxy only works because ecmwf_wam happens not
    to write that column. Pin that no reader substitutes one for the other."""
    for reader, name in ((R.fetch_current_hour_ratings, "revalidate"),
                         (D.fetch_forecasts_window, "daily_report")):
        c = RecordingClient(pages=[[]])
        reader(c)
        q = c.forecast_queries()[0]
        assert q["is_"] == [], f"{name}: unexpected IS filter {q['is_']}"
        assert q["not_is"] == [], (
            f"{name}: a NOT-IS-NULL filter is standing in for the source filter: "
            f"{q['not_is']}")
        assert q["neq"] == [], (
            f"{name}: an exclusion filter (neq) enumerates the sources to REJECT, which "
            f"silently admits the next writer added: {q['neq']}")


# --------------------------------------------------------------------------- #
# 4 — the filter is on every page, not just the first                          #
# --------------------------------------------------------------------------- #
def test_the_filter_is_reapplied_on_every_page_of_a_paged_read():
    """Both readers rebuild the whole builder chain inside the paging loop, so the filter
    has to appear once per page. A refactor that hoists the builder out of the loop and
    only re-calls .range() would still page correctly while applying the filter once —
    against a client that caches, that is a real hazard. Two full pages then a short one.

    Hand arithmetic for the offsets, page size 1000:
        page 0 -> range(0,    0 + 1000 - 1) = (0, 999)
        page 1 -> range(1000, 1000 + 999)   = (1000, 1999)
        page 2 -> range(2000, 2000 + 999)   = (2000, 2999)   short -> loop ends
    """
    for reader, name, sel in ((R.fetch_current_hour_ratings, "revalidate", R_SELECT),
                              (D.fetch_forecasts_window, "daily_report", D_SELECT)):
        p0 = [_project(_nwps_row(i, BASE), sel) for i in range(1000)]
        p1 = [_project(_nwps_row(1000 + i, BASE), sel) for i in range(1000)]
        p2 = [_project(_nwps_row(2000 + i, BASE), sel) for i in range(3)]
        c = RecordingClient(pages=[p0, p1, p2])
        reader(c)

        qs = c.forecast_queries()
        assert len(qs) == 3, f"{name}: expected 3 paged queries, got {len(qs)}"
        assert [q["range"][0] for q in qs] == [(0, 999), (1000, 1999), (2000, 2999)], name
        for i, q in enumerate(qs):
            assert ("source", "nwps") in q["eq"], (
                f"{name}: page {i} was fetched without the source filter: {q['eq']}")


# --------------------------------------------------------------------------- #
# 5 — the readers still ask for what they consume                              #
# --------------------------------------------------------------------------- #
def test_the_select_lists_are_unchanged_by_the_filter():
    """Adding a WHERE clause must not have quietly changed the projection. revalidate's
    list is inline; daily_report's is the shared _FCAST_COLS constant."""
    c = RecordingClient(pages=[[]])
    R.fetch_current_hour_ratings(c)
    assert c.forecast_queries()[0]["select"] == "spot_id,valid_time,stars,effective_size_ft"

    c = RecordingClient(pages=[[]])
    D.fetch_forecasts_window(c)
    assert c.forecast_queries()[0]["select"] == D._FCAST_COLS
    # and the columns both readers key their output on are actually requested
    assert "spot_id" in D._FCAST_COLS and "valid_time" in D._FCAST_COLS
    assert "face_ft" in D._FCAST_COLS and "stars" in D._FCAST_COLS


# --------------------------------------------------------------------------- #
# 6 — CHARACTERISATION: nothing downstream re-filters. The query is the        #
#     only defence, which is why deleting it is not a cosmetic change.         #
# --------------------------------------------------------------------------- #
def test_revalidate_has_no_second_line_of_defence_against_an_ecmwf_row():
    """Feed the two writers' rows for the SAME spot at the SAME instant, ecmwf first —
    the exact shape the DB can return once every 3 h when the filter is absent, since
    the tie is broken by whatever order Postgres hands back.

    `sid in out` keeps the FIRST row seen, and nothing in the function looks at whether
    the rated columns are populated, so it returns the ecmwf nulls:
        {1: {"stars": None, "face_ft": None}}
    It could not look at `source` even if it wanted to: R_SELECT does not include that
    column, so the discriminator is not on the rows by the time they reach this code.
    That is not a bug in this function — it is why the filter has to be in the query.
    """
    rows = [_project(_ecmwf_row(1, BASE), R_SELECT), _project(_nwps_row(1, BASE), R_SELECT)]
    assert "source" not in rows[0], "the projection must not carry the discriminator"
    out = R.fetch_current_hour_ratings(RecordingClient(pages=[rows]))
    assert out == {1: {"stars": None, "face_ft": None}}, out

    # Reverse the arrival order and the SAME function returns the rated row. The result
    # depends on row order alone — i.e. on nothing the code controls.
    rows = [_project(_nwps_row(1, BASE), R_SELECT), _project(_ecmwf_row(1, BASE), R_SELECT)]
    out = R.fetch_current_hour_ratings(RecordingClient(pages=[rows]))
    assert out == {1: {"stars": 3.5, "face_ft": 4.0}}, out


def test_daily_report_has_no_second_line_of_defence_against_an_ecmwf_row():
    """Same characterisation for the other reader. `rows.sort(key=valid_time)` is STABLE,
    so a tie at the same valid_time preserves arrival order and `rows[0]` is whichever
    the DB returned first. Feed ecmwf first at now, plus a nwps row at +24h so the
    `plus24` pick is unambiguous.

    The rows are projected through _FCAST_COLS, which — like revalidate's list — does NOT
    include `source`, so the winner is identified here the only way the calling code could:
    by its null rated columns. Downstream _build_user_prompt reads `latest.get("stars") or
    0` and would render that spot as '★0.0 · — @ —', ranking it out of the top 10.
    """
    ecmwf_now = _project(_ecmwf_row(1, BASE), D_SELECT)
    nwps_now = _project(_nwps_row(1, BASE), D_SELECT)
    nwps_24 = _project(_nwps_row(1, BASE + timedelta(hours=24)), D_SELECT)
    assert "source" not in ecmwf_now, "the projection must not carry the discriminator"

    out = D.fetch_forecasts_window(RecordingClient(pages=[[ecmwf_now, nwps_now, nwps_24]]))
    latest = out[1]["latest"]
    assert latest["stars"] is None and latest["face_ft"] is None, latest
    assert latest["wind_speed"] is None and latest["hs"] == 1.9, latest
    assert out[1]["plus24"]["valid_time"] == _iso(BASE + timedelta(hours=24))

    # Reverse the tie and the rated row wins. Order alone decides.
    out = D.fetch_forecasts_window(RecordingClient(pages=[[nwps_now, ecmwf_now, nwps_24]]))
    latest = out[1]["latest"]
    assert latest["stars"] == 3.5 and latest["face_ft"] == 4.0, latest


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} source-filter checks passed")


if __name__ == "__main__":
    _run_all()
