"""Both rolling trust windows are reported, and only the FIRST one still gates.

TRUST_ROLLING_DAYS has declared (30, 90) with the comment "report both windows" since the
accumulator was written, but only element [0] was ever read: reverify_tagged called
rolling_trust_verdict(load_trust_history(..., days=TRUST_ROLLING_DAYS[0])) and the 90 appeared
nowhere else. One number cannot tell "not enough swell yet" from "the gating window is expiring
events faster than they arrive" — the second can NEVER settle, because with TRUST_MIN_EVENTS at 5
a zone must sustain ~1.17 independent events per week forever or the oldest expires before the
fifth lands. Measured 2026-08-17: box/44097 4 events at 90d vs 1 at 30d, okx/44025 4 vs 2,
phi/44091 2 vs 1, akq/44099 2 vs 1 (and akq/44099 ran at 1.3 events/week in July — a three-day
margin). So both counts are now reported everywhere the verdict is.

WHAT IS DELIBERATELY NOT CHANGED, and these tests pin it:
  * the PASS/FAIL gate still uses ONE window, TRUST_GATING_WINDOW_DAYS. Widening it is a separate
    decision with a real trade-off — a wider window mixes older model behaviour into a current
    verdict — so reporting the other windows must not quietly do it;
  * TRUST_MIN_EVENTS, TRUST_EVENT_GAP_HOURS, TRUST_RAYLEIGH_P and the tier thresholds are untouched;
  * the gating window is named, never taken by tuple position, so reordering TRUST_ROLLING_DAYS for
    display cannot move the gate.

A NOTE ON THE 30-DAY CUT, which these tests also pin (see the last two):
load_trust_history windows by day only when now_epoch_hour is passed TOO, and the gate passes
days= alone — so today the cut never fires and the gating verdict is computed over the ENTIRE
banked log, not 30 days of it. That is left exactly as it was: enforcing it would NARROW the gate
and could unsettle already-settled zones, which is a verdict change, not a reporting one. The
reported counts DO apply the cut, which is why they are the new information.

Run: python -m pipeline.tests.test_rolling_both_windows   (or pytest)
"""
from __future__ import annotations

import ast
import inspect
import json
import os
import tempfile
import textwrap
from pathlib import Path

from pipeline.forecast import nwps_nearshore as nn

NOW_H = 10_000          # an arbitrary "now" in epoch-hours; every fixture is relative to it


def _events(ages_days, residual=5.0, weight=1.0):
    """One record per age-in-days before NOW_H. Ages are spaced well past TRUST_EVENT_GAP_HOURS,
    so each record is its own INDEPENDENT event — the effective N the rolling verdict counts."""
    return [{"t": NOW_H - int(a * 24), "residual": residual, "weight": weight} for a in ages_days]


def _straddling_history():
    """Events either side of the NARROWER window's boundary: 3 inside it, 4 older but inside the
    wider one. The shape the whole change exists for — one count says 3, the other says 7."""
    narrow, wide = min(nn.TRUST_ROLLING_DAYS), max(nn.TRUST_ROLLING_DAYS)
    inside = [1, 5, 20]                                   # < narrow
    older = [narrow + 5, narrow + 15, narrow + 25, wide - 5]   # narrow < age < wide
    assert all(a < narrow for a in inside) and all(narrow < a < wide for a in older), \
        "fixture must straddle the narrower boundary"
    return _events(inside + older)


# --------------------------------------------------------------------------- #
# Change 1 — BOTH windows are computed                                         #
# --------------------------------------------------------------------------- #
def test_both_windows_are_computed_from_a_straddling_history():
    """THE FIX. Every declared window gets its own verdict and count, and the counts DIFFER on a
    history that straddles the narrower boundary — which is what proves the wider one is really
    being cut rather than reported twice from the same records."""
    recs = _straddling_history()
    out = nn.rolling_by_window(recs, tier="point", now_epoch_hour=NOW_H)

    assert [w["days"] for w in out] == list(nn.TRUST_ROLLING_DAYS), \
        f"one entry per declared window, in tuple order: {[w['days'] for w in out]}"
    narrow, wide = out[0], out[-1]
    assert narrow["n_events"] == 3, f"narrow window should hold 3 events, got {narrow['n_events']}"
    assert wide["n_events"] == 7, f"wide window should hold all 7, got {wide['n_events']}"
    assert wide["n_events"] > narrow["n_events"], \
        "the counts must differ — equal counts would mean the cut never ran"
    for w in out:
        assert w["n_records"] == w["n_events"], "one record per independent event in this fixture"
        assert w["verdict"]["verdict"] in ("ACCUMULATING", "PASS", "FAIL", "INCOHERENT")


def test_a_third_declared_window_is_reported_without_further_edits():
    """The tuple is iterated, not indexed twice, so declaring another window just works."""
    out = nn.rolling_by_window(_straddling_history(), now_epoch_hour=NOW_H, windows=(7, 30, 90, 365))
    assert [w["days"] for w in out] == [7, 30, 90, 365]
    counts = [w["n_events"] for w in out]
    assert counts == sorted(counts), f"a wider window can never hold fewer events: {counts}"
    assert counts[0] < counts[-1], "the fixture must actually separate the windows"


def test_window_cut_matches_load_trust_history_exactly():
    """window_records must not drift from the on-disk loader's rule, or the reported counts would
    describe a window nothing else uses. Same records, same days, same answer."""
    recs = _straddling_history()
    with tempfile.TemporaryDirectory() as tmp:
        saved = nn.TRUST_HISTORY_DIR
        nn.TRUST_HISTORY_DIR = Path(tmp) / "hist"
        try:
            nn.append_trust_history("zzz", "99999", recs)
            for d in list(nn.TRUST_ROLLING_DAYS) + [7, 365]:
                on_disk = nn.load_trust_history("zzz", "99999", days=d, now_epoch_hour=NOW_H)
                in_mem = nn.window_records(recs, d, NOW_H)
                assert sorted(r["t"] for r in on_disk) == sorted(r["t"] for r in in_mem), \
                    f"day {d}: in-memory cut disagrees with load_trust_history"
        finally:
            nn.TRUST_HISTORY_DIR = saved


# --------------------------------------------------------------------------- #
# Change 3 — the gate still uses ONE window, and it is named                   #
# --------------------------------------------------------------------------- #
def test_the_gate_uses_the_first_window_only():
    """The verdict comes from TRUST_GATING_WINDOW_DAYS, which IS the first declared window. A
    history that would settle at the wider window but not at the gating one must still read
    ACCUMULATING — that is precisely the case the wider count exists to explain, NOT to fix."""
    assert nn.TRUST_GATING_WINDOW_DAYS == nn.TRUST_ROLLING_DAYS[0], \
        "the gating window is the FIRST declared one"
    narrow, wide = min(nn.TRUST_ROLLING_DAYS), max(nn.TRUST_ROLLING_DAYS)
    # 2 events inside the narrow window, enough more just outside it to clear TRUST_MIN_EVENTS
    older = [narrow + 2 + 3 * i for i in range(nn.TRUST_MIN_EVENTS)]
    assert all(a < wide for a in older), "fixture's older events must still fall inside the wider window"
    recs = _events([1, 10] + older)

    out = nn.rolling_by_window(recs, tier="point", now_epoch_hour=NOW_H)
    gate = next(w for w in out if w["days"] == nn.TRUST_GATING_WINDOW_DAYS)
    widest = max(out, key=lambda w: w["days"])
    assert gate["n_events"] == 2, gate["n_events"]
    assert widest["n_events"] >= nn.TRUST_MIN_EVENTS, widest["n_events"]
    assert gate["verdict"]["verdict"] == "ACCUMULATING", \
        "the gating window is short of TRUST_MIN_EVENTS, so the gate must NOT settle"
    assert widest["verdict"]["verdict"] != "ACCUMULATING", \
        "fixture is wrong if the wider window did not settle — there is nothing to distinguish"


def test_reverify_reads_the_gating_window_by_name_not_by_position():
    """reverify_tagged must not index the tuple. Reading TRUST_ROLLING_DAYS[0] at the call site
    made 'the gating one' an accident of tuple order — reordering the tuple for display would
    then silently move the gate. Checked on the real source."""
    src = textwrap.dedent(inspect.getsource(nn.reverify_tagged))
    tree = ast.parse(src)
    for node in ast.walk(tree):
        if isinstance(node, ast.Subscript) and isinstance(node.value, ast.Name) \
                and node.value.id == "TRUST_ROLLING_DAYS":
            raise AssertionError(
                "reverify_tagged indexes TRUST_ROLLING_DAYS by position. Use "
                "TRUST_GATING_WINDOW_DAYS so reordering the tuple cannot move the gate.")
    assert "TRUST_GATING_WINDOW_DAYS" in src, \
        "reverify_tagged must name the gating window explicitly"
    assert "TRUST_ROLLING_DAYS" in src, "reverify_tagged must still iterate the declared windows"


def test_no_rolling_window_length_is_hardcoded():
    """Nothing anywhere reads a literal 30 or 90 as a window length — the tuple is the only
    source. A hardcoded copy is how a declared constant silently stops being the real one."""
    src = Path(inspect.getfile(nn)).read_text()
    tree = ast.parse(src)
    bad = []
    for node in ast.walk(tree):
        # days=30 / days=90 as a keyword argument anywhere
        if isinstance(node, ast.Call):
            for kw in node.keywords:
                if kw.arg in ("days", "gating_days", "now_epoch_hour") \
                        and isinstance(kw.value, ast.Constant) \
                        and kw.value.value in nn.TRUST_ROLLING_DAYS:
                    bad.append(f"line {kw.value.lineno}: {kw.arg}={kw.value.value}")
    assert not bad, ("a rolling-window length is hardcoded instead of read from "
                     f"TRUST_ROLLING_DAYS:\n  " + "\n  ".join(bad))
    # and the constants themselves are untouched
    assert nn.TRUST_ROLLING_DAYS == (30, 90)
    assert nn.TRUST_MIN_EVENTS == 5
    assert nn.TRUST_EVENT_GAP_HOURS == 12
    assert nn.TRUST_RAYLEIGH_P == 0.05


# --------------------------------------------------------------------------- #
# Change 4 — the expiry note, and what the settled/accumulating output carries  #
# --------------------------------------------------------------------------- #
def test_expiry_note_fires_only_when_the_wider_window_holds_more():
    """The note separates 'waiting on swell' from 'expiring events faster than they arrive'."""
    same = [{"days": 30, "n_records": 3, "n_events": 3, "verdict": {}},
            {"days": 90, "n_records": 3, "n_events": 3, "verdict": {}}]
    assert nn.window_expiry_note(same) is None, "equal counts are just a quiet spell — no note"

    more = [{"days": 30, "n_records": 1, "n_events": 1, "verdict": {}},
            {"days": 90, "n_records": 4, "n_events": 4, "verdict": {}}]
    note = nn.window_expiry_note(more)
    assert note and "\n" not in note, f"one line: {note!r}"
    assert "4 events at 90d" in note and "1 at 30d" in note, note
    assert "EXPIRING" in note, note
    assert "TRUST_MIN_EVENTS" not in note, \
        "4 < TRUST_MIN_EVENTS 5, so it would NOT already have settled — do not claim it would"

    settles = [{"days": 30, "n_records": 2, "n_events": 2, "verdict": {}},
               {"days": 90, "n_records": 6, "n_events": 6, "verdict": {}}]
    note2 = nn.window_expiry_note(settles)
    assert "ALREADY have settled" in note2 and str(nn.TRUST_MIN_EVENTS) in note2, note2

    single = [{"days": 30, "n_records": 1, "n_events": 1, "verdict": {}}]
    assert nn.window_expiry_note(single) is None, "one window has nothing to compare against"
    assert nn.window_expiry_note([]) is None, "no windows must not raise"


def test_per_zone_rolling_cell_shows_a_count_for_every_window():
    """Change 2 — the ROLLING column carries the verdict AND both counts, compactly enough that
    the table still fits. The `ev` column and the ⚑ flag are untouched, so the cell is the only
    thing that grew."""
    out = nn.rolling_by_window(_straddling_history(), now_epoch_hour=NOW_H)
    cell = nn._rolling_cell("ACCUMULATING", out)
    assert cell == "ACCUMULATING 3/7", cell
    assert len(cell) <= 17, f"the cell must fit the column: {len(cell)} chars"
    # every declared window contributes exactly one count, in tuple order
    assert cell.split(" ", 1)[1].split("/") == [str(w["n_events"]) for w in out]
    # a third declared window adds a third count, no edit needed at the call site
    # (fixture ages are 1, 5, 20 | 35, 45, 55, 85 days → 2 inside 7d, 3 inside 30d, all 7 inside 90d)
    three = nn._rolling_cell("PASS", nn.rolling_by_window(
        _straddling_history(), now_epoch_hour=NOW_H, windows=(7, 30, 90)))
    assert three == "PASS 2/3/7", three


def test_window_counts_render_marks_the_gating_window():
    rec = {"events_by_window": {"30": 1, "90": 4}, "gating_window_days": 30}
    s = nn._window_counts_str(rec)
    assert s == "30d 1 (gate) / 90d 4", s
    # a record from a different window set still renders, in ascending order
    other = {"events_by_window": {"7": 0, "365": 9}, "gating_window_days": 7}
    assert nn._window_counts_str(other) == "7d 0 (gate) / 365d 9", nn._window_counts_str(other)
    assert nn._window_counts_str({}) == "no window counts"


def test_emitted_output_carries_accumulating_zones_without_changing_the_settled_gate():
    """accumulating_json is ADDITIVE: any_settled still reflects settled zones only, so a run
    where nothing settled opens no issue however many zones are stuck accumulating."""
    with tempfile.TemporaryDirectory() as tmp:
        path = os.path.join(tmp, "gh_out")
        saved = os.environ.get("GITHUB_OUTPUT")
        os.environ["GITHUB_OUTPUT"] = path
        try:
            acc = [{"zone": "box/44097", "spots": 3, "verdict": "ACCUMULATING",
                    "events_by_window": {"30": 1, "90": 4}, "gating_window_days": 30,
                    "expiry_note": "4 events at 90d vs 1 at 30d"}]
            nn._emit_reverify_output([], acc)
            out = dict(l.split("=", 1) for l in
                       Path(path).read_text().splitlines() if "=" in l)
            assert out["any_settled"] == "false", \
                "accumulating zones must NOT make the workflow open an issue"
            assert json.loads(out["settled_json"]) == []
            got = json.loads(out["accumulating_json"])
            assert got[0]["zone"] == "box/44097" and got[0]["events_by_window"] == {"30": 1, "90": 4}
        finally:
            if saved is None:
                os.environ.pop("GITHUB_OUTPUT", None)
            else:
                os.environ["GITHUB_OUTPUT"] = saved


def test_emit_is_still_a_no_op_off_a_workflow():
    saved = os.environ.pop("GITHUB_OUTPUT", None)
    try:
        nn._emit_reverify_output([{"zone": "x"}], [{"zone": "y"}])   # must not raise
    finally:
        if saved is not None:
            os.environ["GITHUB_OUTPUT"] = saved


# --------------------------------------------------------------------------- #
# The 30-day cut is NOT enforced on the gate — pinned so the reported note      #
# stays true, and so nobody "fixes" it without noticing it moves verdicts.      #
# --------------------------------------------------------------------------- #
def test_load_trust_history_needs_both_days_and_now_to_cut():
    """The gate passes days= alone, so no cut happens and it reads the WHOLE log. Pinning the
    real behaviour rather than the believed one: if load_trust_history is ever changed to cut on
    days alone, this fails and points at the summary note AND at the fact that the change would
    narrow every zone's gating verdict."""
    recs = _straddling_history()
    with tempfile.TemporaryDirectory() as tmp:
        saved = nn.TRUST_HISTORY_DIR
        nn.TRUST_HISTORY_DIR = Path(tmp) / "hist"
        try:
            nn.append_trust_history("zzz", "99999", recs)
            full = nn.load_trust_history("zzz", "99999")
            gate_load = nn.load_trust_history("zzz", "99999", days=nn.TRUST_GATING_WINDOW_DAYS)
            assert len(gate_load) == len(full) == len(recs), (
                "load_trust_history cut on days alone — the gating verdict just NARROWED from the "
                "full log to a window, which moves verdicts. Update the reverify summary note and "
                "re-check every settled zone before accepting this.")
            windowed = nn.load_trust_history("zzz", "99999",
                                             days=nn.TRUST_GATING_WINDOW_DAYS, now_epoch_hour=NOW_H)
            assert len(windowed) < len(full), "with now_epoch_hour the cut DOES fire"
        finally:
            nn.TRUST_HISTORY_DIR = saved


def _reverify_calls(name):
    """Every Call node in reverify_tagged whose callee is `name` (plain or attribute)."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(nn.reverify_tagged)))
    out = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            f = node.func
            if (isinstance(f, ast.Name) and f.id == name) or \
                    (isinstance(f, ast.Attribute) and f.attr == name):
                out.append(node)
    return out


def test_reverify_reports_the_windows_but_gates_on_its_own_load():
    """Structural: reverify_tagged computes the gate from its own load_trust_history call and the
    reported windows from rolling_by_window. Keeping the gate's literal call is what guarantees no
    verdict moved, and keeps it coupled to that loader so a later fix reaches it."""
    src = textwrap.dedent(inspect.getsource(nn.reverify_tagged))
    assert "rolling_by_window(" in src, "reverify_tagged must report every declared window"
    assert "rolling_trust_verdict(gate_hist" in src, \
        "the gate must still be a direct rolling_trust_verdict over its own history load"
    assert "load_trust_history(wfo, buoy, days=TRUST_GATING_WINDOW_DAYS)" in src, \
        "the gate's history load must stay the same call it has always been"


def test_the_per_zone_line_actually_renders_the_counts():
    """_rolling_cell being correct is not enough — the zone line must USE it, or the table shows a
    bare verdict again and the whole change is invisible where it is read most."""
    assert _reverify_calls("_rolling_cell"), (
        "reverify_tagged does not call _rolling_cell — the per-zone ROLLING column has lost its "
        "per-window event counts.")


def test_accumulating_records_carry_both_counts():
    """Change 4's payload, checked where it is BUILT rather than on a hand-made fixture: the
    record reverify_tagged appends must carry the per-window counts and which window gates, or
    the summary and the workflow issue have nothing to render."""
    calls = _reverify_calls("append")
    acc = [c for c in calls
           if isinstance(c.func, ast.Attribute) and isinstance(c.func.value, ast.Name)
           and c.func.value.id == "accumulating"]
    assert acc, "reverify_tagged no longer collects ACCUMULATING zones"
    for call in acc:
        assert call.args and isinstance(call.args[0], ast.Dict), \
            "the accumulating record should be a dict literal"
        keys = {k.value for k in call.args[0].keys if isinstance(k, ast.Constant)}
        for required in ("events_by_window", "gating_window_days", "expiry_note", "zone"):
            assert required in keys, (
                f"the ACCUMULATING record is missing {required!r} — a reader cannot then tell "
                "'not enough swell yet' from 'the gating window is expiring events faster than "
                "they arrive'.")



def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} rolling-window checks passed")


if __name__ == "__main__":
    _run_all()
