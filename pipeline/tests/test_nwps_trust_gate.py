"""Checks for the rebuilt, partition-matched NWPS trust gate (swell_trust_verdict).

Synthetic per-hour samples (model tracked systems + buoy spectral swell) exercise the
four required behaviours plus the two subtle guards:
  * the swell-energy PRECONDITION excludes no-swell hours (validity, not outlier rejection);
  * a swell-dominated zone with good direction agreement PASSes;
  * a zone with genuinely bad direction FAILs — on SPREAD or on a constant BIAS;
  * too few qualifying (swell-present) hours → INCONCLUSIVE;
  * the system match is highest-ENERGY (independent of direction — no rigging);
  * height still gates (anti-correlated Hs FAILs).

Run: python -m pipeline.tests.test_nwps_trust_gate   (or pytest)
"""
from __future__ import annotations

from pipeline.forecast import nwps_nearshore as nn


def _sys(hs, direction, system=1, tp=10.0):
    return {"system": system, "hs": hs, "tp": tp, "dir": direction}


def _sample(t, systems, buoy_swd, hs_swell, frac, *, swh=None, wvht=None):
    # default heights correlate (rising sea) so the height check is satisfied unless overridden
    swh = 1.0 + 0.08 * t if swh is None else swh
    wvht = (swh * 1.05) if wvht is None else wvht
    return {"t": t, "model_systems": systems, "model_swh": swh, "buoy_wvht": wvht,
            "buoy_swell_dir": buoy_swd, "buoy_hs_swell": hs_swell, "buoy_frac": frac,
            "dirpw": None, "buoy_mwd": None}


def _batch(n, model_dir_fn, buoy_swd=90.0, hs_swell=1.1, frac=0.79, systems_fn=None):
    out = []
    for t in range(n):
        syss = systems_fn(t) if systems_fn else [_sys(1.3, model_dir_fn(t))]
        out.append(_sample(t, syss, buoy_swd, hs_swell, frac))
    return out


def test_matching_rule_is_highest_energy_not_sys1_not_direction():
    # two systems: sys1 is a small 200° swell, sys2 is the dominant 90° swell.
    systems = [_sys(0.4, 200.0, system=1), _sys(1.4, 90.0, system=2)]
    m = nn._match_swell_system(systems)
    assert m["system"] == 2 and m["dir"] == 90.0, "dominant (highest hs), not sys1"
    assert nn._match_swell_system([]) is None
    # match must NOT depend on the buoy direction (no circular reasoning) — same result
    # regardless of what the buoy says; the function doesn't even take the buoy dir.


def test_precondition_excludes_no_swell_hours():
    assert nn._swell_precondition(1.1, 0.79) is True
    assert nn._swell_precondition(0.3, 0.79) is False, "below Hs floor"
    assert nn._swell_precondition(1.1, 0.10) is False, "below fraction floor"
    assert nn._swell_precondition(None, 0.5) is False
    # 8 good hours + 6 no-swell hours (tiny swell): only the 8 qualify, and the no-swell
    # hours are marked qualifying=False (excluded by the QUANTITY, not by disagreement)
    good = _batch(8, lambda t: 90.0 + (3 if t % 2 else -3))
    noswell = [_sample(100 + t, [_sys(0.2, 90.0)], 90.0, 0.2, 0.15) for t in range(6)]
    res = nn.swell_trust_verdict(good + noswell)
    assert res["n_qualifying"] == 8
    assert sum(1 for p in res["per_hour"] if not p["qualifying"]) == 6


def test_swell_dominated_good_agreement_passes():
    res = nn.swell_trust_verdict(_batch(12, lambda t: 90.0 + (4 if t % 2 else -4)))
    assert res["verdict"] == "PASS", res.get("reason")
    assert res["dir_circ_std"] <= nn.SWELL_DIR_CIRC_MAX_DEG
    assert abs(res["dir_bias"]) <= nn.SWELL_DIR_BIAS_MAX_DEG
    assert res["comparison"] == "model swell-system dir vs buoy spectral swell_dir"


def test_bad_direction_spread_fails():
    # model swings ±40° about the buoy swell dir → circ_std far over the ceiling
    res = nn.swell_trust_verdict(_batch(12, lambda t: 90.0 + (40 if t % 2 else -40)))
    assert res["verdict"] == "FAIL" and res["dir_circ_std"] > nn.SWELL_DIR_CIRC_MAX_DEG


def test_constant_bias_fails_even_with_tight_spread():
    # a CONSTANT +40° offset: circ_std ≈ 0 (would pass on spread alone) but bias > ceiling.
    # This is the trap the old gate had — the bias guard must catch it.
    res = nn.swell_trust_verdict(_batch(12, lambda t: 130.0))   # buoy swd 90 → constant +40
    assert res["dir_circ_std"] < 2.0, "spread is tiny (constant offset)"
    assert res["verdict"] == "FAIL" and abs(res["dir_bias"]) > nn.SWELL_DIR_BIAS_MAX_DEG


def test_too_few_qualifying_hours_is_inconclusive():
    # 4 qualifying + 10 no-swell → below SWELL_MIN_QUALIFYING → INCONCLUSIVE (not FAIL/PASS)
    good = _batch(4, lambda t: 90.0)
    noswell = [_sample(50 + t, [_sys(0.2, 90.0)], 90.0, 0.1, 0.1) for t in range(10)]
    res = nn.swell_trust_verdict(good + noswell)
    assert res["verdict"] == "INCONCLUSIVE" and res["n_qualifying"] == 4


def test_height_still_gates_even_with_good_direction():
    # perfect direction, but model Hs ANTI-correlated with buoy WVHT → height r < 0.80 → FAIL
    samples = []
    for t in range(12):
        s = _sample(t, [_sys(1.3, 90.0)], 90.0, 1.1, 0.79, swh=1.0 + 0.1 * t, wvht=3.0 - 0.1 * t)
        samples.append(s)
    res = nn.swell_trust_verdict(samples)
    assert res["verdict"] == "FAIL" and res["height_r"] < nn.TRUST_R_MIN


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} trust-gate checks passed")


if __name__ == "__main__":
    _run_all()
