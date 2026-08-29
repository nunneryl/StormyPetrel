"""WW3 cycle selection, step-download retries, and the series-horizon log.

THE DEFECT. `candidate_cycles()` returns cycles newest-first and `_locate_cycle` took the
first one yielding any 8 of the 57 requested steps. NCEP publishes a cycle's step files
progressively over ~2-3 h, so the newest cycle is by construction the LEAST complete: at
19:30 UTC that is the 18Z cycle, 1.5 h old. Measured in production 2026-08-28, the
partition series stopped at 2026-08-31 15:00 — 26-32 of 57 steps under every candidate
cycle — and every NWPS hour past it joined to nothing. 50.2% of nwps spot-hours carried no
partitions; 82-88% of that was the tail.

Three separable changes, pinned here:
  1. prefer a cycle that PUBLISHES the horizon, measured contiguously from f000;
  2. route step downloads through http.request so they get the module's retries — one
     transient 502 used to lose a step for the whole run, at log.debug;
  3. log the SPAN, not just the count. "30/57 step files available" was the identical line
     for a contiguous f000..f087 series and 30 scattered steps.

EVERY EXPECTED VALUE IS WRITTEN LITERALLY. None is produced by calling the function under
test — the cover figures, step sets, log substrings and valid_times below are all typed out.

NO NETWORK. Every fixture is local: directory indexes are strings, downloads are stubs, and
the retry test drives the REAL tenacity decorator against a fake session.

Run: python -m pipeline.tests.test_ww3_cycle_horizon   (or pytest)
"""
from __future__ import annotations

import logging
from pathlib import Path

import requests

from pipeline import http
from pipeline.forecast import ww3


# --------------------------------------------------------------------------- #
# helpers                                                                      #
# --------------------------------------------------------------------------- #
class _Capture(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)

    def text(self):
        return "\n".join(r.getMessage() for r in self.records)

    def warnings(self):
        return "\n".join(r.getMessage() for r in self.records if r.levelno >= logging.WARNING)


def _index_html(hh: str, fhours) -> str:
    """A cycle directory index carrying exactly *fhours*, in the real href shape:
       <a href="gfswave.t18z.global.0p25.f087.grib2">"""
    rows = "".join(
        f'<a href="gfswave.t{hh}z.global.0p25.f{fh:03d}.grib2">x</a>\n' for fh in fhours
    )
    return "<html><body>\n" + rows + "</body></html>"


def _steps(last_fh: int):
    """Every 3-hourly step from f000 through *last_fh* inclusive."""
    return list(range(0, last_fh + 1, 3))


# --------------------------------------------------------------------------- #
# 1 — the index parser                                                         #
# --------------------------------------------------------------------------- #
def test_published_steps_reads_the_cycle_index():
    """Parses fhours out of a real-shaped index and ignores other cycles' files."""
    html = _index_html("18", [0, 3, 6]) + '<a href="gfswave.t12z.global.0p25.f009.grib2">x</a>'
    saved = ww3._get_text
    try:
        ww3._get_text = lambda url: html
        got = ww3._published_steps("20260828", "18")
    finally:
        ww3._get_text = saved
    assert got == {0, 3, 6}, got          # f009 belongs to 12z and must not be counted


def test_published_steps_returns_None_when_the_index_is_unreadable():
    """Unreadable is NOT empty — the ranking treats the two differently."""
    saved = ww3._get_text
    try:
        ww3._get_text = lambda url: None
        assert ww3._published_steps("20260828", "18") is None
    finally:
        ww3._get_text = saved


# --------------------------------------------------------------------------- #
# 2 — contiguous cover                                                         #
# --------------------------------------------------------------------------- #
def test_contiguous_cover_is_the_unbroken_run_from_f000():
    """Contiguity, not count. 30 steps running f000..f087 give a clean 87 h series;
    30 steps scattered to f168 give a series full of holes. The two must not score alike.

        {0,3,6,9}          -> 9    (unbroken through f009)
        {0,3,9,12}         -> 3    (f006 missing; the run stops there, count is 4 either way)
        {0}                -> 0
        {3,6,9}            -> 0    (f000 absent — nothing is covered from the start)
        every 3 h to f168  -> 168
    """
    assert ww3._contiguous_cover_h({0, 3, 6, 9}) == 9
    assert ww3._contiguous_cover_h({0, 3, 9, 12}) == 3
    assert ww3._contiguous_cover_h({0}) == 0
    assert ww3._contiguous_cover_h({3, 6, 9}) == 0
    assert ww3._contiguous_cover_h(set(range(0, 169, 3))) == 168
    # THE PAIR THAT SEPARATES COVER FROM COUNT: same number of steps, different cover.
    dense = set(_steps(87))                      # 30 steps, unbroken
    sparse = {0} | set(range(6, 175, 6))         # also 30 steps, but f003 missing
    assert len(dense) == 30 and len(sparse) == 30, (len(dense), len(sparse))
    assert ww3._contiguous_cover_h(dense) == 87
    assert ww3._contiguous_cover_h(sparse) == 0


def test_contiguous_cover_passes_unknown_through_as_None():
    assert ww3._contiguous_cover_h(None) is None
    assert ww3._contiguous_cover_h(set()) == 0


# --------------------------------------------------------------------------- #
# 3 — ranking: a fresh-but-partial cycle must NOT win                          #
# --------------------------------------------------------------------------- #
def _rank_with(published_by_cycle):
    saved = ww3._published_steps
    try:
        ww3._published_steps = lambda d, h: published_by_cycle.get((d, h))
        return ww3._rank_candidates(list(published_by_cycle.keys()))
    finally:
        ww3._published_steps = saved


def test_a_fresh_partial_cycle_is_NOT_preferred_over_an_older_complete_one():
    """THE 19:30 CASE. The 18Z cycle is 1.5 h old and has published f000..f087; the 12Z
    cycle is 7.5 h old and complete. Newest-first put 18Z first and the >= 8 floor accepted
    it, producing the measured cliff. It must now rank second.

        18Z: cover  87  -> short of WW3_MIN_COVER_HOURS 144
        12Z: cover 168  -> complete
    """
    ranked = _rank_with({
        ("20260828", "18"): set(_steps(87)),
        ("20260828", "12"): set(_steps(168)),
    })
    assert ranked[0][:2] == ("20260828", "12"), ranked
    assert ranked[0][2] == 168, ranked
    assert ranked[1][:2] == ("20260828", "18"), ranked
    assert ranked[1][2] == 87, ranked


def test_among_complete_cycles_the_NEWEST_wins():
    """Newest-that-is-complete, not most-complete-overall. The oldest cycle is always the
    most complete, so ranking by cover alone would always pick the oldest and throw away
    freshness for nothing. Both cover 168 here; the newer must lead."""
    ranked = _rank_with({
        ("20260828", "06"): set(_steps(168)),
        ("20260828", "00"): set(_steps(168)),
        ("20260827", "18"): set(_steps(168)),
    })
    assert [c[:2] for c in ranked] == [
        ("20260828", "06"), ("20260828", "00"), ("20260827", "18")], ranked


def test_a_cycle_at_exactly_the_threshold_counts_as_complete():
    """The boundary is inclusive: WW3_MIN_COVER_HOURS is what NWPS's f000..f144 needs, so a
    cycle covering exactly 144 h covers every NWPS hour there is.
        144 -> complete;  141 -> short."""
    ranked = _rank_with({
        ("20260828", "06"): set(_steps(144)),
        ("20260828", "00"): set(_steps(168)),
    })
    assert ranked[0][:2] == ("20260828", "06") and ranked[0][2] == 144, ranked
    ranked2 = _rank_with({
        ("20260828", "06"): set(_steps(141)),
        ("20260828", "00"): set(_steps(168)),
    })
    assert ranked2[0][:2] == ("20260828", "00"), ranked2


def test_when_none_is_complete_the_longest_cover_leads_and_unknowns_go_last():
    """No candidate reaches 144 h. Order by cover; an unreadable index ranks last because
    'unknown' is not evidence of coverage, but it is still a candidate to try."""
    ranked = _rank_with({
        ("20260828", "18"): set(_steps(24)),     # cover 24
        ("20260828", "12"): None,                # index unreadable
        ("20260828", "06"): set(_steps(96)),     # cover 96
    })
    assert [c[:2] for c in ranked] == [
        ("20260828", "06"), ("20260828", "18"), ("20260828", "12")], ranked
    assert [c[2] for c in ranked] == [96, 24, None], ranked


# --------------------------------------------------------------------------- #
# 4 — _locate_cycle: the >= 8 floor survives                                   #
# --------------------------------------------------------------------------- #
def _locate_with(published_by_cycle, downloadable, use_cache=False):
    """Drive _locate_cycle with the network stubbed. *downloadable* maps (date, hh) to the
    set of fhours whose download succeeds. Returns (result, captured log)."""
    saved = (ww3.candidate_cycles, ww3._published_steps, ww3._download_step)
    cap = _Capture()
    ww3.log.addHandler(cap)
    prev = ww3.log.level
    ww3.log.setLevel(logging.INFO)
    try:
        ww3.candidate_cycles = lambda: list(published_by_cycle.keys())
        ww3._published_steps = lambda d, h: published_by_cycle.get((d, h))
        ww3._download_step = (
            lambda d, h, fh, dest: fh in downloadable.get((d, h), set()))
        return ww3._locate_cycle(use_cache), cap
    finally:
        (ww3.candidate_cycles, ww3._published_steps, ww3._download_step) = saved
        ww3.log.removeHandler(cap)
        ww3.log.setLevel(prev)


def test_the_complete_cycle_is_the_one_actually_downloaded():
    """End to end: ranking picks 12Z, and the paths returned are 12Z's."""
    res, _cap = _locate_with(
        {("20260828", "18"): set(_steps(87)), ("20260828", "12"): set(_steps(168))},
        {("20260828", "18"): set(_steps(87)), ("20260828", "12"): set(_steps(168))},
    )
    assert res is not None
    date_ymd, hh, paths = res
    assert (date_ymd, hh) == ("20260828", "12"), (date_ymd, hh)
    assert len(paths) == 57, len(paths)


def test_when_no_candidate_is_complete_something_is_still_returned():
    """A SHORT SERIES BEATS NO SERIES. Returning None drops WW3 for the whole roster and
    every spot falls to whole-spectrum DIRPW, so the best-available cycle is still used —
    and the shortfall is WARNED, not swallowed.
        best cover 96 h < 144 -> warn, and still return its 33 steps
    """
    res, cap = _locate_with(
        {("20260828", "18"): set(_steps(24)), ("20260828", "06"): set(_steps(96))},
        {("20260828", "18"): set(_steps(24)), ("20260828", "06"): set(_steps(96))},
    )
    assert res is not None
    date_ymd, hh, paths = res
    assert (date_ymd, hh) == ("20260828", "06"), (date_ymd, hh)
    assert len(paths) == 33, len(paths)            # f000..f096 inclusive at 3 h = 33
    w = cap.warnings()
    assert "NO candidate cycle covers 144 h" in w, w
    assert "96 h" in w, w


def test_the_eight_step_floor_still_rejects_a_cycle_and_falls_through():
    """The floor is unchanged and independent of the ranking. The best-ranked cycle offers
    only 5 downloadable steps — under the floor — so it is skipped and the next is used.
        18Z: 5 steps  -> below the floor
        06Z: 9 steps  -> at/above it
    """
    res, cap = _locate_with(
        {("20260828", "18"): set(_steps(96)), ("20260828", "06"): set(_steps(24))},
        {("20260828", "18"): {0, 3, 6, 9, 12}, ("20260828", "06"): set(_steps(24))},
    )
    assert res is not None
    date_ymd, hh, paths = res
    assert (date_ymd, hh) == ("20260828", "06"), (date_ymd, hh)
    assert len(paths) == 9, len(paths)             # f000..f024 inclusive at 3 h = 9
    assert "only 5 step files; trying next" in cap.text(), cap.text()


def test_every_candidate_below_the_floor_returns_None():
    """Unchanged terminal behaviour: nothing usable anywhere is still None."""
    res, _cap = _locate_with(
        {("20260828", "18"): set(_steps(96)), ("20260828", "06"): set(_steps(96))},
        {("20260828", "18"): {0, 3}, ("20260828", "06"): {0}},
    )
    assert res is None, res


# --------------------------------------------------------------------------- #
# 5 — step downloads get the module's retries                                  #
# --------------------------------------------------------------------------- #
class _FakeResp:
    def __init__(self, status=200, body=b"x" * 200_000):
        self.status_code = status
        self._body = body
        self.text = ""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def iter_content(self, chunk_size=1):
        yield self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


class _FakeSession:
    """Fails the first *fail_times* calls, then succeeds. Counts attempts."""

    def __init__(self, fail_times, exc=None, status=200):
        self.fail_times = fail_times
        self.calls = 0
        self._exc = exc or requests.ConnectionError("boom")
        self._status = status

    def request(self, method, url, **kw):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise self._exc
        return _FakeResp(status=self._status)


def _download_with(session_obj, tmp_name="ww3_retry.grib2"):
    """Drive the REAL http.request (and its real tenacity decorator) against a fake
    session, with the backoff sleep neutered so the test is instant."""
    tmp = Path("/tmp/claude-0/-home-user-StormyPetrel/"
               "c0d14651-f350-5987-84e1-cf8ad55860d6/scratchpad")
    tmp.mkdir(parents=True, exist_ok=True)
    dest = tmp / tmp_name
    dest.unlink(missing_ok=True)
    saved_sess = http._session
    saved_sleep = http.request.retry.sleep
    try:
        http._session = session_obj
        http.request.retry.sleep = lambda _s: None
        ok = ww3._download_step("20260828", "12", 87, dest)
    finally:
        http._session = saved_sess
        http.request.retry.sleep = saved_sleep
    return ok, dest


def test_a_step_that_fails_once_and_succeeds_on_retry_is_obtained():
    """THE INTERIOR-BLOCK FIX. One transient ConnectionError used to lose the step for the
    whole run — session().get bypassed _RETRY entirely. Now attempt 2 succeeds.
        fail_times 1 -> 2 total calls, download OK, file on disk"""
    sess = _FakeSession(fail_times=1)
    ok, dest = _download_with(sess, "ww3_retry_once.grib2")
    assert ok is True, ok
    assert sess.calls == 2, sess.calls
    assert dest.exists() and dest.stat().st_size == 200_000, dest.stat().st_size
    dest.unlink(missing_ok=True)


def test_a_step_that_fails_every_attempt_is_counted_and_does_not_abort():
    """_RETRY stops after 4 attempts and _download_step swallows the result as False, so
    the run continues and the step is counted missing rather than raising.
        stop_after_attempt(4) -> exactly 4 calls, then False"""
    sess = _FakeSession(fail_times=99)
    ok, dest = _download_with(sess, "ww3_retry_never.grib2")
    assert ok is False, ok
    assert sess.calls == 4, sess.calls
    assert not dest.exists()


def test_a_404_is_not_retried():
    """A not-yet-published step must fast-fail: four backed-off attempts on a 404 would
    slow every run for a file that cannot appear inside the retry window. Cycle selection
    is what keeps us off unpublished cycles, not the retry.
        status 404 -> exactly 1 call, then False"""
    sess = _FakeSession(fail_times=0, status=404)
    ok, dest = _download_with(sess, "ww3_retry_404.grib2")
    assert ok is False, ok
    assert sess.calls == 1, sess.calls
    assert not dest.exists()


def test_a_persistent_5xx_says_GAVE_UP_not_merely_FAILED():
    """The two failure kinds must not read alike. A 429/5xx that survived four attempts is
    an UPSTREAM OUTAGE; a 404 is a file that is not published. Both return False and both
    count the step missing, so the log text is the only thing that separates them — and
    `except Exception` would catch RetryableHTTPError perfectly well, which is exactly why
    the narrower clause has to be pinned or it reads as redundant and gets deleted.
    """
    class _Boom:
        def __init__(self):
            self.calls = 0

        def request(self, method, url, **kw):
            self.calls += 1
            raise http.RetryableHTTPError("503 on " + url)

    cap = _Capture()
    ww3.log.addHandler(cap)
    prev = ww3.log.level
    ww3.log.setLevel(logging.DEBUG)
    saved_sess, saved_sleep = http._session, http.request.retry.sleep
    dest = Path("/tmp/claude-0/-home-user-StormyPetrel/"
                "c0d14651-f350-5987-84e1-cf8ad55860d6/scratchpad/ww3_5xx.grib2")
    dest.unlink(missing_ok=True)
    try:
        http._session = _Boom()
        http.request.retry.sleep = lambda _s: None
        ok = ww3._download_step("20260828", "12", 87, dest)
    finally:
        http._session, http.request.retry.sleep = saved_sess, saved_sleep
        ww3.log.removeHandler(cap)
        ww3.log.setLevel(prev)
    assert ok is False, ok
    t = cap.text()
    assert "gave up after retries" in t, t
    assert "GET %s failed" not in t and " failed: " not in t, t


def test_download_step_does_not_call_session_get_directly():
    """STRUCTURAL PIN. The bug was the call SHAPE — session().get(...) reaches the shared
    session but not the retry that the module's own title advertises. If someone reverts to
    it, the retry tests above would still pass against a fake session, so this asserts the
    seam itself."""
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(ww3._download_step)))
    calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
    # session().get(...) — an Attribute 'get' whose value is a Call to the name `session`
    bypass = [
        c for c in calls
        if isinstance(c.func, ast.Attribute) and c.func.attr == "get"
        and isinstance(c.func.value, ast.Call)
        and isinstance(c.func.value.func, ast.Name) and c.func.value.func.id == "session"
    ]
    assert not bypass, "step download calls session().get() again, bypassing http.request"
    # ...and it really does go through the retrying helper
    routed = [c for c in calls if isinstance(c.func, ast.Name) and c.func.id == "request"]
    assert routed, "step download no longer calls http.request"


# --------------------------------------------------------------------------- #
# 6 — the horizon log                                                          #
# --------------------------------------------------------------------------- #
class _FakeDataset:
    """Only what fetch()'s first-step diagnostic line touches."""

    data_vars = ("shts", "mpts", "swdir")


def _run_fetch(fhours, valid_times):
    """Drive fetch() with the cycle, GRIB open and extraction stubbed. *fhours* are the
    steps whose files 'arrived'; *valid_times* the ISO string each one parses to."""
    tmp = Path("/tmp/claude-0/-home-user-StormyPetrel/"
               "c0d14651-f350-5987-84e1-cf8ad55860d6/scratchpad")
    tmp.mkdir(parents=True, exist_ok=True)
    paths = [ww3._step_cache_path("20260828", "12", fh) for fh in fhours]
    by_path = dict(zip([p.name for p in paths], valid_times))

    saved = (ww3._locate_cycle, ww3._open_grib_datasets, ww3._extract_step_vectorized,
             ww3._close_datasets, ww3.WW3_FORECAST_FILE)
    cap = _Capture()
    ww3.log.addHandler(cap)
    prev = ww3.log.level
    ww3.log.setLevel(logging.INFO)
    try:
        ww3._locate_cycle = lambda use_cache: ("20260828", "12", paths)
        ww3._open_grib_datasets = lambda p: [_FakeDataset()]
        ww3._close_datasets = lambda ds: None
        ww3._extract_step_vectorized = (
            lambda datasets, names, la, lo, debug_first=False: (
                by_path[_CUR["name"]], {"Steamer Lane": {"swell_1_hs": 1.0}}))
        # _extract_step_vectorized gets no path, so thread it through a cell the stubbed
        # opener sets — keeps the stub honest about which step it is answering for.
        orig_open = ww3._open_grib_datasets

        def _open(p):
            _CUR["name"] = p.name
            return orig_open(p)
        ww3._open_grib_datasets = _open
        ww3.WW3_FORECAST_FILE = tmp / "ww3_horizon_test.json"
        ww3.fetch([{"name": "Steamer Lane", "lat": 36.95, "lng": -122.03}], use_cache=True)
    finally:
        (ww3._locate_cycle, ww3._open_grib_datasets, ww3._extract_step_vectorized,
         ww3._close_datasets, ww3.WW3_FORECAST_FILE) = saved
        ww3.log.removeHandler(cap)
        ww3.log.setLevel(prev)
    return cap


_CUR: dict = {}


def test_the_summary_reports_the_span_and_the_max_fhour():
    """The two numbers that were one arithmetic step away and never printed.
        steps f000/f003/f006 -> span 2026-08-28T12:00:00Z .. 2026-08-28T18:00:00Z,
                                max f006 of f168, 3/57 steps
    """
    cap = _run_fetch([0, 3, 6], ["2026-08-28T12:00:00Z",
                                 "2026-08-28T15:00:00Z",
                                 "2026-08-28T18:00:00Z"])
    t = cap.text()
    assert "series spans 2026-08-28T12:00:00Z .. 2026-08-28T18:00:00Z" in t, t
    assert "max f006 of f168" in t, t
    assert "3/57 steps" in t, t


def test_contiguous_and_scattered_step_sets_produce_DIFFERENT_output():
    """THE PIN THIS CHANGE EXISTS FOR. Both runs obtain the same NUMBER of steps and used
    to log the identical line. One is a clean short series; the other has a hole at f003
    that the ±90 min join cannot bridge.
        [0,3,6] -> CONTIGUOUS, no gap warning
        [0,6,9] -> WITH GAPS, names f003
    """
    good = _run_fetch([0, 3, 6], ["2026-08-28T12:00:00Z",
                                  "2026-08-28T15:00:00Z",
                                  "2026-08-28T18:00:00Z"])
    bad = _run_fetch([0, 6, 9], ["2026-08-28T12:00:00Z",
                                 "2026-08-28T18:00:00Z",
                                 "2026-08-28T21:00:00Z"])
    gt, bt = good.text(), bad.text()
    assert "CONTIGUOUS" in gt and "WITH GAPS" not in gt, gt
    assert "WITH GAPS" in bt, bt
    assert gt != bt, "the two runs must not log the same line"
    assert "NOT contiguous from f000" not in good.warnings(), good.warnings()
    bw = bad.warnings()
    assert "NOT contiguous from f000" in bw, bw
    assert "f003" in bw, bw
    # both are short of f168, so both must say so — that is the tail warning, not the gap one
    assert "short of the requested f168" in good.warnings(), good.warnings()


def test_a_full_length_series_warns_about_neither():
    """No cliff, no holes, no warnings — so a clean run is distinguishable from both
    failure modes rather than merely quieter."""
    fhours = list(range(0, 169, 3))
    vts = [f"2026-08-{28 + (12 + fh) // 24:02d}T{(12 + fh) % 24:02d}:00:00Z" for fh in fhours]
    cap = _run_fetch(fhours, vts)
    assert "max f168 of f168" in cap.text(), cap.text()
    assert "CONTIGUOUS" in cap.text()
    assert cap.warnings() == "", cap.warnings()


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} ww3 cycle/horizon checks passed")


if __name__ == "__main__":
    _run_all()
