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

import numpy as np

from pipeline.forecast import nwps_nearshore as nn


def _sys(hs, direction, system=1, tp=10.0):
    return {"system": system, "hs": hs, "tp": tp, "dir": direction}


def _sample(t, systems, buoy_swd, hs_swell, frac, *, swh=None, wvht=None, ws=5.0, wdir=270.0):
    # default heights correlate (rising sea) so the height check is satisfied unless overridden;
    # a light 5 m/s wind + tp=10 s systems (c=15.6 m/s) classify as SWELL by wave-age.
    swh = 1.0 + 0.08 * t if swh is None else swh
    wvht = (swh * 1.05) if wvht is None else wvht
    return {"t": t, "model_systems": systems, "model_swh": swh, "buoy_wvht": wvht,
            "model_ws": ws, "model_wdir": wdir,
            "buoy_swell_dir": buoy_swd, "buoy_hs_swell": hs_swell, "buoy_frac": frac,
            "dirpw": None, "buoy_mwd": None}


def _batch(n, model_dir_fn, buoy_swd=90.0, hs_swell=1.1, frac=0.79, systems_fn=None):
    out = []
    for t in range(n):
        syss = systems_fn(t) if systems_fn else [_sys(1.3, model_dir_fn(t))]
        out.append(_sample(t, syss, buoy_swd, hs_swell, frac))
    return out


def test_matching_is_highest_energy_among_swell():
    # two long-period swell systems under a light 5 m/s wind (both swell): pick the dominant.
    systems = [_sys(0.4, 200.0, system=1, tp=11.0), _sys(1.4, 90.0, system=2, tp=12.0)]
    m = nn._match_swell_system(systems, 5.0, 270.0)
    assert m["system"] == 2 and m["dir"] == 90.0, "dominant swell (highest hs), not sys1"
    assert nn._match_swell_system([], 5.0, 270.0) is None
    # match doesn't depend on the buoy direction (no circular reasoning) — it isn't passed one.


def test_windsea_system_is_excluded_even_when_biggest():
    # THE fix: a BIG wind-sea (short 5 s period, aligned with a 12 m/s wind) + a smaller
    # long-period swell. Old rule took the biggest (wind-sea); new rule must pick the swell.
    windsea = _sys(1.8, 270.0, system=1, tp=5.0)    # c=7.8 m/s < 1.2·12·cos0=14.4 → wind-sea
    swell = _sys(0.7, 120.0, system=2, tp=12.0)     # c=18.7 m/s > 14.4 → swell
    m = nn._match_swell_system([windsea, swell], 12.0, 270.0)
    assert m is not None and m["system"] == 2 and m["dir"] == 120.0, "the SWELL, not the bigger wind-sea"
    assert nn._system_is_swell(windsea, 12.0, 270.0) is False
    assert nn._system_is_swell(swell, 12.0, 270.0) is True
    # a swell OPPOSING the wind stays swell regardless of period
    assert nn._system_is_swell(_sys(1.0, 90.0, tp=6.0), 12.0, 270.0) is True


def test_hour_with_only_windsea_is_not_comparable():
    # buoy has swell every hour, but the ONLY model system is a wind-sea → those hours are
    # excluded from the direction stat (validity), and n_model_no_swell counts them.
    windsea = lambda: [_sys(1.6, 270.0, system=1, tp=4.5)]   # short + aligned with a 12 m/s wind
    samples = [_sample(t, windsea(), 120.0, 1.1, 0.79, ws=12.0, wdir=270.0) for t in range(10)]
    res = nn.swell_trust_verdict(samples)
    assert res["verdict"] == "INCONCLUSIVE" and res["n_qualifying"] == 0
    assert res["n_model_no_swell"] == 10, "buoy had swell but the model had only wind-sea"


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


def test_depth_matched_node_selectors():
    # land to the NORTH (row 0); the plain-nearest wet cell (row 1) is just north of the buoy
    # = SHOREWARD/shallow; the seaward (open/deep) cells are to the south (rows 2-3).
    lat = np.array([[40.030], [40.008], [39.980], [39.960]])
    lon = np.array([[-73.0], [-73.0], [-73.0], [-73.0]])
    mask = np.array([[True], [False], [False], [False]])
    cyc = {"lats": lat, "lons": lon, "mask": mask}
    blat, blng = 40.000, -73.000
    # nearest = the shoreward shadow cell (row 1)
    assert nn._nearest_cell(cyc, blat, blng)[0] == 1
    # seaward = nearest cell in the seaward (south) half-plane (row 2, not the shoreward row 1)
    sc = nn._seaward_cell(cyc, blat, blng)
    assert sc is not None and sc[0] == 2, "seaward pick moves OFF the shoreward cell"
    # deepest w/o bathy = most-seaward (furthest offshore) within radius → row 3
    dc = nn._deepest_cell(cyc, blat, blng, radius_km=8.0)
    assert dc[0] == 3 and dc[5] is None
    # deepest WITH a bathymetry sampler favouring row 2 → row 2 (depth_fn overrides geometry)
    depth_fn = lambda la, lo: 100.0 if abs(la - 39.980) < 1e-6 else 20.0
    assert nn._deepest_cell(cyc, blat, blng, radius_km=8.0, depth_fn=depth_fn)[0] == 2
    # _pick_cell dispatch, and it falls back to nearest for an unknown / seaward-less grid
    assert nn._pick_cell(cyc, blat, blng, "nearest")[0] == 1
    assert nn._pick_cell(cyc, blat, blng, "seaward")[0] == 2
    assert nn._pick_cell(cyc, blat, blng, "deepest")[0] == 3
    # the sampled nearest node IS flagged shoreward by _node_diag (the refraction signal)
    nd = nn._node_diag(cyc, blat, blng, 1, 0, nn._haversine_km(blat, blng, 40.008, -73.0))
    assert nd["sampled_is_seaward"] is False and nd["seaward_differs"] is True


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} trust-gate checks passed")


if __name__ == "__main__":
    _run_all()
