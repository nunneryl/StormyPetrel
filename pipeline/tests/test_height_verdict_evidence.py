"""The height verdict says WHAT IT WAS COMPUTED OVER, so a wind-sea-only FAIL is not read as
a groundswell failure.

THE CASE. On 2026-08-17 phi/44091 returned FAIL with r=0.746 over 25 overlapping hours. The
model carried no swell partition in any hour, the buoy's swell ran 0.12-0.25 m against
SWELL_HS_FLOOR_M of 0.5, and n_qualifying was 0 — a pure wind-sea window. The buoy's TOTAL Hs
still varied enough to clear TRUST_BUOY_RANGE_MIN_M because the chop built and died, so the
range guard passed and the correlation ran. The verdict is a TRUE statement about that window
and is unchanged here; what changed is that it now says so.

WHAT IS DELIBERATELY NOT CHANGED, and these tests pin it:
  * the range guard still decides when the correlation runs — a flat window is still
    INCONCLUSIVE with height_r nan, and height_hs_span is None because no comparison happened;
  * NO swell precondition is added to the height axis. Height is the buoy-INDEPENDENT primary
    gate; disabling it on swell-free windows would remove it exactly where it still has
    something to say. windsea_only_fail only MARKS, it never suppresses or filters.

Run: python -m pipeline.tests.test_height_verdict_evidence   (or pytest)
"""
from __future__ import annotations

import ast
import inspect
import math
import textwrap

from pipeline.forecast import nwps_nearshore as nn


def _sys(hs, direction, system=1, tp=13.0):
    """A model tracked partition, same shape the gate reads (per_hour needs 'system')."""
    return {"system": system, "hs": hs, "tp": tp, "dir": direction}


def _sample(t, *, swh, wvht, hs_swell, frac, systems=None, ws=5.0, wdir=270.0, swd=90.0):
    """One hour of model×buoy. systems=None means the model carried NO partition that hour,
    which is the wind-sea-only shape: nothing for _match_swell_system to match."""
    return {"t": t, "model_systems": systems, "model_swh": swh, "buoy_wvht": wvht,
            "model_ws": ws, "model_wdir": wdir, "buoy_swell_dir": swd,
            "buoy_hs_swell": hs_swell, "buoy_frac": frac,
            "dirpw": None, "buoy_mwd": None}


def _flat_window(n=25):
    """Buoy total Hs barely moves — span well under TRUST_BUOY_RANGE_MIN_M."""
    return [_sample(i, swh=1.00 + 0.002 * i, wvht=1.00 + 0.002 * i,
                    hs_swell=0.15, frac=0.10) for i in range(n)]


def _windsea_only_window(n=25):
    """The phi/44091 shape: chop builds and dies so the TOTAL-Hs span clears the range guard,
    but every hour's swell is below SWELL_HS_FLOOR_M and the model carries no partition, so
    NO hour is comparable. Model and buoy are deliberately decorrelated to land on FAIL."""
    out = []
    for i in range(n):
        # buoy total Hs sweeps ~1.0 m (clears the 0.75 m guard); the model tracks it poorly
        wvht = 0.6 + 1.0 * (i % 5) / 4.0
        swh = 0.6 + 1.0 * ((i * 3) % 5) / 4.0        # different phase → weak r
        out.append(_sample(i, swh=swh, wvht=wvht, hs_swell=0.12 + 0.005 * (i % 3),
                           frac=0.10, systems=None))
    return out


def _swell_window(n=25):
    """Real groundswell: every hour clears SWELL_HS_FLOOR_M / SWELL_FRAC_FLOOR and the model
    carries a long-period system, so hours ARE comparable. Heights decorrelated → FAIL, but a
    FAIL with real swell hours behind it."""
    out = []
    for i in range(n):
        wvht = 1.2 + 1.0 * (i % 5) / 4.0
        swh = 1.2 + 1.0 * ((i * 3) % 5) / 4.0
        out.append(_sample(i, swh=swh, wvht=wvht, hs_swell=1.4, frac=0.85,
                           systems=[_sys(1.4, 92.0)]))
    return out


def test_flat_window_is_inconclusive_with_nan_r_and_no_span():
    """The range guard is untouched: below TRUST_BUOY_RANGE_MIN_M the correlation never runs,
    height_r stays nan, the verdict is INCONCLUSIVE, and height_hs_span is None because there
    was no comparison to report a span for."""
    s = _flat_window()
    bwv = [x["buoy_wvht"] for x in s]
    assert max(bwv) - min(bwv) < nn.TRUST_BUOY_RANGE_MIN_M, "fixture must be flat"
    assert len(s) >= nn.TRUST_MIN_PAIRS, "fixture must clear the pair count, so only span decides"

    res = nn.swell_trust_verdict(s)
    assert res["verdict"] == "INCONCLUSIVE", res["verdict"]
    assert math.isnan(res["height_r"]), f"correlation must not run on a flat window: {res['height_r']}"
    assert res["height_hs_span"] is None, \
        f"span must be None when no correlation was computed, got {res['height_hs_span']}"
    assert not nn.windsea_only_fail(res), "an INCONCLUSIVE is not a FAIL and must not be marked"


def test_windsea_only_fail_reports_zero_swell_hours_and_carries_the_evidence():
    """THE phi/44091 CASE. Range guard passes, correlation runs, r fails — and the reason now
    states the hours, the span and the zero comparable swell hours."""
    s = _windsea_only_window()
    bwv = [x["buoy_wvht"] for x in s]
    assert max(bwv) - min(bwv) >= nn.TRUST_BUOY_RANGE_MIN_M, \
        "fixture must CLEAR the range guard — that is the whole point of this case"

    res = nn.swell_trust_verdict(s)
    assert res["verdict"] == "FAIL", f"expected a height FAIL, got {res['verdict']} r={res['height_r']}"
    assert res["n_qualifying"] == 0, f"no hour should be comparable, got {res['n_qualifying']}"
    assert res["height_r"] < nn.TRUST_R_MIN and res["height_r"] == res["height_r"]
    assert res["height_hs_span"] is not None, "the correlation ran, so the span must be reported"
    assert res["height_hs_span"] >= nn.TRUST_BUOY_RANGE_MIN_M

    reason = res["reason"]
    assert reason and "\n" not in reason, f"the reason stays one line: {reason!r}"
    assert f"{res['height_n']} overlapping hr" in reason, f"hour count missing: {reason}"
    assert f"{res['height_hs_span']:.2f} m" in reason, f"buoy Hs span missing: {reason}"
    assert "0 comparable swell hr" in reason, f"comparable-hour count missing: {reason}"
    assert "WIND-SEA ONLY" in reason, f"a reader must be able to tell this from groundswell: {reason}"
    assert nn.windsea_only_fail(res) is True


def test_a_fail_with_real_swell_hours_is_not_marked_wind_sea_only():
    """A FAIL backed by comparable swell hours keeps its current presentation — no marking,
    and the reason reports a non-zero comparable-hour count."""
    s = _swell_window()
    res = nn.swell_trust_verdict(s)
    assert res["verdict"] == "FAIL", f"fixture should fail on r, got {res['verdict']}"
    assert res["n_qualifying"] > 0, "fixture must produce comparable swell hours"
    assert nn.windsea_only_fail(res) is False, "a FAIL with real swell hours must NOT be marked"
    reason = res["reason"]
    assert "WIND-SEA ONLY" not in reason, f"must not claim wind-sea only: {reason}"
    assert f"{res['n_qualifying']} comparable swell hr" in reason, reason
    assert f"{res['height_n']} overlapping hr" in reason, reason


def test_pass_also_states_what_it_was_computed_over():
    """A PASS carries the same evidence, so 'r 0.9' can never stand alone either."""
    s = [_sample(i, swh=1.0 + 0.06 * i, wvht=1.02 + 0.06 * i, hs_swell=1.4, frac=0.85,
                 systems=[_sys(1.4, 92.0)]) for i in range(25)]
    res = nn.swell_trust_verdict(s)
    assert res["verdict"] == "PASS", f"{res['verdict']} r={res['height_r']}"
    assert res["reason"], "PASS must now carry a reason rather than None"
    assert f"{res['height_n']} overlapping hr" in res["reason"], res["reason"]
    assert res["height_hs_span"] is not None
    assert not nn.windsea_only_fail(res), "a PASS is never marked"


def test_marking_never_suppresses_or_changes_a_verdict():
    """windsea_only_fail is REPORTING ONLY: it reads a result and returns a bool. The verdict,
    r, span and hour counts are identical whether or not it is consulted."""
    res = nn.swell_trust_verdict(_windsea_only_window())
    before = (res["verdict"], res["height_r"], res["height_n"],
              res["height_hs_span"], res["n_qualifying"], res["reason"])
    assert nn.windsea_only_fail(res) is True
    after = (res["verdict"], res["height_r"], res["height_n"],
             res["height_hs_span"], res["n_qualifying"], res["reason"])
    assert before == after, "consulting the marker must not mutate the result"
    # and it is False for every non-FAIL verdict, whatever n_qualifying says
    assert not nn.windsea_only_fail({"verdict": "PASS", "n_qualifying": 0})
    assert not nn.windsea_only_fail({"verdict": "INCONCLUSIVE", "n_qualifying": 0})
    assert nn.windsea_only_fail({"verdict": "FAIL", "n_qualifying": 0})
    assert not nn.windsea_only_fail({"verdict": "FAIL", "n_qualifying": 3})
    assert nn.windsea_only_fail({"verdict": "FAIL"}), "a missing count reads as zero"


def test_thresholds_are_untouched():
    """This was a reporting fix. Pin the constants it must not have moved."""
    assert nn.TRUST_R_MIN == 0.8
    assert nn.TRUST_BUOY_RANGE_MIN_M == 0.75
    assert nn.TRUST_MIN_PAIRS == 6
    assert nn.SWELL_HS_FLOOR_M == 0.5


def _mentions_marker(node):
    return any(isinstance(n, ast.Name) and n.id == "windsea_only_fail" for n in ast.walk(node))


def test_reverify_marks_a_windsea_only_fail_without_filtering_it_out():
    """Change 3's load-bearing half, checked on reverify_tagged's ACTUAL source.

    The marking is presentation: it appends a NOTE to a line that gets appended either way. Had
    it become `if not windsea_only_fail(res): flagged.append(...)` — or a `continue` before the
    append — the zone would silently vanish from the 'zones failing HEIGHT or the ROLLING
    direction gate' summary. A wind-sea FAIL is still a FAIL and must still be LISTED.

    reverify_tagged fetches GRIBs, so this reads its source rather than running it. The rule is
    stricter than 'the append is not nested in a windsea branch', because a sibling `continue`
    skips the append without containing it: the marker must never gate a STATEMENT at all — it
    only ever selects a VALUE."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(nn.reverify_tagged)))

    marks = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "windsea_only_fail"]
    assert marks, "reverify_tagged no longer consults windsea_only_fail — the marking is gone"

    appends = [n for n in ast.walk(tree)
               if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
               and n.func.attr == "append" and isinstance(n.func.value, ast.Name)
               and n.func.value.id == "flagged"]
    assert appends, "reverify_tagged no longer appends to flagged — the summary is gone"

    # The marker gates no statement: not the append, and not a continue/return that skips it.
    for node in ast.walk(tree):
        assert not (isinstance(node, ast.If) and _mentions_marker(node.test)), (
            "windsea_only_fail gates a STATEMENT in reverify_tagged (line "
            f"{node.test.lineno} of the function). It must only choose the note text — any "
            "branch on it can filter a wind-sea-only FAIL out of the summary, and it must "
            "still be listed.")

    # ...and where it does select a value, that value is the note, never the listing itself.
    for node in ast.walk(tree):
        if isinstance(node, ast.IfExp) and _mentions_marker(node.test):
            assert not any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "append" and isinstance(n.func.value, ast.Name)
                and n.func.value.id == "flagged"
                for n in ast.walk(node)), (
                "flagged.append sits inside a windsea_only_fail conditional expression — "
                "the marking must decide the NOTE, never whether the zone is listed.")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} height-verdict-evidence checks passed")


if __name__ == "__main__":
    _run_all()
