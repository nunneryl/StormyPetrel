"""Checks for the Stage-1 NWPS trust gate — height-primary, energy-weighted, spot-tiered, rolling.

Synthetic per-hour samples (model tracked systems + buoy spectral swell) exercise the rebuild:
  * the swell-energy PRECONDITION excludes no-swell hours (validity, not outlier rejection);
  * the system match is highest-ENERGY among SWELL systems (wind-sea excluded by wave-age);
  * HEIGHT is the PRIMARY window verdict (anti-correlated Hs FAILs; direction never blocks a window);
  * DIRECTION is an ENERGY-WEIGHTED residual — tiny slivers stop dominating the spread (THE fix);
  * a spot's TIER comes from the raycast window width refined by break_type;
  * bad direction (spread OR constant bias) drops the dir_flag but does not block the window;
  * independent-EVENT counting + the Rayleigh coherence guard behave;
  * the ROLLING accumulator stays ACCUMULATING until enough events, then PASS / FAIL / INCOHERENT.

Run: python -m pipeline.tests.test_nwps_trust_gate   (or pytest)
"""
from __future__ import annotations

import json

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


def _records(residuals, weights=None, *, gap=24):
    # one record per residual, spaced `gap` (≥ TRUST_EVENT_GAP_HOURS) apart so each residual is its
    # own INDEPENDENT swell event — the effective N the rolling verdict counts.
    weights = [1.0] * len(residuals) if weights is None else weights
    return [{"t": i * gap, "residual": r, "weight": w}
            for i, (r, w) in enumerate(zip(residuals, weights))]


# --------------------------------------------------------------------------- #
# System matching — highest-energy SWELL, wind-sea excluded (unchanged rule)   #
# --------------------------------------------------------------------------- #
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
    windsea = _sys(1.8, 270.0, system=1, tp=5.0)    # tp 5 s < the 8 s cutoff → wind-sea
    swell = _sys(0.7, 120.0, system=2, tp=12.0)     # tp 12 s ≥ 8 s → swell
    m = nn._match_swell_system([windsea, swell], 12.0, 270.0)
    assert m is not None and m["system"] == 2 and m["dir"] == 120.0, "the SWELL, not the bigger wind-sea"
    assert nn._system_is_swell(windsea, 12.0, 270.0) is False
    assert nn._system_is_swell(swell, 12.0, 270.0) is True
    # RULE CHANGE: under the fixed cutoff a 6 s system is chop whatever its direction. Wave age
    # called it swell purely for OPPOSING the wind (cosδ ≤ 0), which is how sub-8 s chop got
    # matched against buoy swell; the opt-in still reproduces the old answer.
    short_opposing = _sys(1.0, 90.0, tp=6.0)
    assert nn._system_is_swell(short_opposing, 12.0, 270.0) is False, "6 s is chop by period"
    assert nn._system_is_swell(short_opposing, 12.0, 270.0, prefer_wave_age=True) is True
    # the diag also surfaces the dominant WIND-SEA partition (chop that rotates with the wind)
    w = nn._match_windsea_system([windsea, swell], 12.0, 270.0)
    assert w is not None and w["system"] == 1 and w["dir"] == 270.0


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


# --------------------------------------------------------------------------- #
# HEIGHT is the primary window verdict; DIRECTION is a flag, not a block       #
# --------------------------------------------------------------------------- #
def test_swell_dominated_good_agreement_is_flagged_ok_and_passes_height():
    res = nn.swell_trust_verdict(_batch(12, lambda t: 90.0 + (4 if t % 2 else -4)), tier="point")
    assert res["verdict"] == "PASS", res.get("reason")          # HEIGHT (primary) — co-moving Hs
    assert res["dir_circ_std_w"] <= nn.SWELL_DIR_TIERS["point"]["circ_std"]
    assert abs(res["dir_bias_w"]) <= nn.SWELL_DIR_TIERS["point"]["bias"]
    assert res["dir_flag"] is True, "energy-weighted residual clears the point-tier bar"
    assert res["comparison"] == "energy-weighted model-swell-dir vs buoy-spectral-swell-dir"


def test_height_still_gates_even_with_good_direction():
    # perfect direction, but model Hs ANTI-correlated with buoy WVHT → height r < 0.80 → FAIL
    samples = []
    for t in range(12):
        s = _sample(t, [_sys(1.3, 90.0)], 90.0, 1.1, 0.79, swh=1.0 + 0.1 * t, wvht=3.0 - 0.1 * t)
        samples.append(s)
    res = nn.swell_trust_verdict(samples)
    assert res["verdict"] == "FAIL" and res["height_r"] < nn.TRUST_R_MIN
    assert res["gate"] == "height (primary)"


def test_bad_direction_spread_is_flagged_not_blocked():
    # model swings ±40° about the buoy swell dir → circ_std far over the point bar → dir_flag OFF,
    # but the WINDOW verdict is height-driven (PASS): a bad-direction window FLAGS, it doesn't block.
    res = nn.swell_trust_verdict(_batch(12, lambda t: 90.0 + (40 if t % 2 else -40)), tier="point")
    assert res["dir_circ_std_w"] > nn.SWELL_DIR_TIERS["point"]["circ_std"]
    assert res["dir_flag"] is False
    assert res["verdict"] == "PASS", "height gates the window; bad direction is a rolling flag"


def test_constant_bias_is_flagged_even_with_tight_spread():
    # a CONSTANT +40° offset: circ_std ≈ 0 (would pass on spread alone) but bias > the bar.
    # The bias guard must still drop the flag — the trap the old spread-only gate had.
    res = nn.swell_trust_verdict(_batch(12, lambda t: 130.0), tier="point")   # buoy 90 → +40
    assert res["dir_circ_std_w"] < 2.0, "spread is tiny (constant offset)"
    assert abs(res["dir_bias_w"]) > nn.SWELL_DIR_TIERS["point"]["bias"]
    assert res["dir_flag"] is False


def test_hour_with_only_windsea_is_not_comparable():
    # buoy has swell every hour, but the ONLY model system is a wind-sea → those hours are
    # excluded from the DIRECTION stat (validity) and counted in n_model_no_swell. Height still
    # gates the window; direction simply has nothing comparable, so it can't be flagged on.
    windsea = lambda: [_sys(1.6, 270.0, system=1, tp=4.5)]   # short + aligned with a 12 m/s wind
    samples = [_sample(t, windsea(), 120.0, 1.1, 0.79, ws=12.0, wdir=270.0) for t in range(10)]
    res = nn.swell_trust_verdict(samples)
    assert res["n_qualifying"] == 0, "buoy swell present, but no model SWELL system to compare"
    assert res["n_model_no_swell"] == 10, "buoy had swell but the model had only wind-sea"
    assert res["dir_flag"] is False and res["dir_circ_std_w"] != res["dir_circ_std_w"]  # NaN
    assert res["verdict"] == "PASS", "height still gates the window (co-moving Hs)"


# --------------------------------------------------------------------------- #
# ENERGY-WEIGHTING — the highest-impact fix (slivers stop dominating)          #
# --------------------------------------------------------------------------- #
def test_energy_weighting_downweights_slivers():
    # 12 hours of energetic swell in tight agreement + 3 sliver hours (0.5 m) pointing 120° wrong.
    # UNWEIGHTED the slivers explode the spread (a "spread-explosion" zone); energy-weighting
    # (w=min(Hs)² shrinks a 0.5 m sliver ~1/64 vs a 2 m swell) recovers it under the point bar.
    energetic = [_sample(t, [_sys(2.0, 90.0 + (3 if t % 2 else -3))], 90.0, 2.0, 0.8)
                 for t in range(12)]
    slivers = [_sample(50 + t, [_sys(0.5, 210.0)], 90.0, 0.5, 0.5) for t in range(3)]
    res = nn.swell_trust_verdict(energetic + slivers, tier="point")
    assert res["n_qualifying"] == 15, "the slivers DO qualify — they are just down-weighted"
    assert res["dir_circ_std_u"] > 30.0, "unweighted: the slivers blow the spread up"
    assert res["dir_circ_std_w"] < res["dir_circ_std_u"]
    assert res["dir_circ_std_w"] <= nn.SWELL_DIR_TIERS["point"]["circ_std"], "weighted recovers"
    assert abs(res["dir_bias_w"]) < abs(res["dir_bias_u"]), "the sliver bias is down-weighted too"
    assert res["dir_flag"] is True, "the energy-weighted residual clears the point-tier bar"


def test_hour_weight_uses_the_smaller_side_squared():
    assert nn._hour_weight(2.0, 0.5) == 0.25, "min(2.0,0.5)² — the buoy sliver caps it"
    assert nn._hour_weight(0.5, 2.0) == 0.25, "symmetric — the model sliver caps it too"
    assert nn._hour_weight(2.0, 2.0) == 4.0
    assert nn._hour_weight(None, 2.0) == 0.0 and nn._hour_weight(2.0, None) == 0.0


def test_weighted_circ_stats_basic_and_degenerate_guard():
    # equal weights, symmetric ±10 → bias ~0, finite std, weight/n reported
    bias, cs, rbar, sw, n = nn._weighted_circ_stats([10.0, -10.0], [1.0, 1.0])
    assert abs(bias) < 1e-6 and 0 < cs < 20 and n == 2 and abs(sw - 2.0) < 1e-9
    # weighting (not arithmetic): a 9× heavier 0° and a light 80° → bias near 0, not the 40° midpoint
    b2, _, _, _, _ = nn._weighted_circ_stats([0.0, 80.0], [9.0, 1.0])
    assert 0.0 <= b2 < 15.0, "heavy 0° dominates; light 80° barely tugs it"
    # DEGENERATE guard the research flags: fully opposed, equal weight → Rbar≈0 → circ_std = inf
    _, cs3, rbar3, _, _ = nn._weighted_circ_stats([0.0, 180.0], [1.0, 1.0])
    assert rbar3 < 1e-6 and cs3 == float("inf"), "no resultant → std diverges, never a bogus finite"
    # no usable data → NaN bias/std, zero weight/n (never a spurious value)
    bn, csn, _, sw0, n0 = nn._weighted_circ_stats([None, 10.0], [None, 0.0])
    assert bn != bn and csn != csn and sw0 == 0.0 and n0 == 0


# --------------------------------------------------------------------------- #
# SPOT TIERS — from raycast window width, refined by break_type                #
# --------------------------------------------------------------------------- #
def test_spot_tier_from_window_width_and_break_type():
    wide = {"swell_window_arcs": [{"min": 0, "max": 200, "span": 200}], "break_type": "beach break"}
    narrow = {"swell_window_arcs": [{"min": 100, "max": 160, "span": 60}], "break_type": "reef"}
    mid = {"swell_window_arcs": [{"min": 90, "max": 210, "span": 120}], "break_type": "sandbar"}
    assert nn._spot_tier(wide) == "exposed"
    assert nn._spot_tier(narrow) == "sheltered"
    assert nn._spot_tier(mid) == "point"
    # break_type refines: a wide window at a named POINT is not treated as a fully exposed beach
    pt = {"swell_window_arcs": [{"min": 0, "max": 220, "span": 220}], "break_type": "point break"}
    assert nn._spot_tier(pt) == "point", "a named point never counts as fully exposed"
    # width sums across multiple arcs; no arcs → width 0 → sheltered (conservative — the tight bar)
    two = {"swell_window_arcs": [{"span": 100}, {"span": 100}], "break_type": ""}
    assert nn._spot_tier(two) == "exposed"
    assert nn._spot_tier({"swell_window_arcs": []}) == "sheltered"
    # _arc_total_width falls back to (max−min) when an arc carries only min/max (pilot fixtures)
    assert nn._arc_total_width([{"min": 90, "max": 230}]) == 140
    assert nn._arc_total_width([{"min": 350, "max": 30}]) == 40, "wrap-aware"


# --------------------------------------------------------------------------- #
# Independent EVENTS + Rayleigh coherence — the rolling accumulator's guards    #
# --------------------------------------------------------------------------- #
def test_independent_event_counting():
    # hours within TRUST_EVENT_GAP_HOURS are ONE episode; a ≥gap jump starts a new event
    assert nn._count_independent_events([]) == 0
    assert nn._count_independent_events([100]) == 1
    assert nn._count_independent_events([100, 101, 102, 103]) == 1, "one continuous episode"
    # two episodes 48 h apart, each a few hours long → 2 independent events
    assert nn._count_independent_events([100, 101, 102, 148, 149, 150]) == 2
    # exactly at the gap boundary counts as a NEW event (the ≥ boundary)
    g = nn.TRUST_EVENT_GAP_HOURS
    assert nn._count_independent_events([0, g]) == 2
    assert nn._count_independent_events([0, g - 1]) == 1


def test_rayleigh_coherence():
    # tightly clustered residuals (Rbar≈1) over many samples → tiny p (coherent, bias meaningful)
    assert nn._rayleigh_p(0.98, 20) < 0.01
    # Rbar≈0 (scattered) → p≈1 (incoherent; the "bias" is noise, circ_std diverges)
    assert nn._rayleigh_p(0.02, 20) > 0.9
    assert nn._rayleigh_p(0.0, 0) == 1.0, "no data → not coherent"
    # p falls with more independent samples at the same Rbar
    assert nn._rayleigh_p(0.5, 40) < nn._rayleigh_p(0.5, 5)


def test_short_window_reports_one_event_for_the_accumulator():
    # 4 qualifying swell hours in ONE consecutive episode + 10 no-swell hours: the gate reports
    # n_qualifying=4 but only 1 independent EVENT — a single flat window can never mint a
    # direction PASS/FAIL; the rolling accumulator (not this one window) decides trust.
    good = _batch(4, lambda t: 90.0)                        # t = 0,1,2,3 → one episode
    noswell = [_sample(50 + t, [_sys(0.2, 90.0)], 90.0, 0.2, 0.1) for t in range(10)]
    res = nn.swell_trust_verdict(good + noswell)
    assert res["n_qualifying"] == 4 and res["n_events"] == 1
    assert res["n_model_no_swell"] == 0, "the no-swell hours fail the buoy precondition, not the match"


# --------------------------------------------------------------------------- #
# ROLLING accumulator — ACCUMULATING → PASS / FAIL / INCOHERENT                #
# --------------------------------------------------------------------------- #
def test_rolling_accumulates_until_enough_events():
    # 4 clean events (bias ~0) — below TRUST_MIN_EVENTS → ACCUMULATING, not a premature PASS,
    # even though the numbers look great. Hours are autocorrelated; events are the effective N.
    v = nn.rolling_trust_verdict(_records([2.0, -2.0, 1.0, -1.0]), tier="point")
    assert v["verdict"] == "ACCUMULATING" and v["n_events"] == 4
    assert v["dir_circ_std_w"] < 5.0, "the fit is tight — but we still wait for enough events"


def test_rolling_pass_on_enough_coherent_low_error_events():
    v = nn.rolling_trust_verdict(_records([3.0, -3.0, 2.0, -2.0, 1.0, -1.0]), tier="point")
    assert v["n_events"] == 6 and v["verdict"] == "PASS", v.get("reason")
    assert v["dir_circ_std_w"] <= nn.SWELL_DIR_TIERS["point"]["circ_std"]
    assert v["dir_rayleigh_p"] <= nn.TRUST_RAYLEIGH_P and v["ci_lo"] < v["ci_hi"]


def test_rolling_fail_on_coherent_but_biased_events():
    # 6 events, a COHERENT ~+30° offset (tight spread) → over the point ±15° bias bar → FAIL,
    # not INCOHERENT — the bias is real and stable, it is just too large.
    v = nn.rolling_trust_verdict(_records([28.0, 30.0, 32.0, 29.0, 31.0, 30.0]), tier="point")
    assert v["n_events"] == 6 and v["verdict"] == "FAIL"
    assert abs(v["dir_bias_w"]) > nn.SWELL_DIR_TIERS["point"]["bias"]
    assert v["dir_rayleigh_p"] <= nn.TRUST_RAYLEIGH_P, "a stable bias is coherent, just too big"


def test_rolling_incoherent_when_residuals_scatter():
    # 6 events spread around the whole circle → Rbar≈0, Rayleigh p high → INCOHERENT (no bias),
    # NOT a FAIL: there is no stable direction to fail against, and circ_std would diverge.
    v = nn.rolling_trust_verdict(_records([0.0, 60.0, 120.0, 180.0, 240.0, 300.0]), tier="point")
    assert v["n_events"] == 6 and v["verdict"] == "INCOHERENT"
    assert v["dir_rayleigh_p"] > nn.TRUST_RAYLEIGH_P


def test_rolling_tier_makes_the_bar():
    # the SAME ~22° coherent bias PASSes an exposed beach (±25°) but FAILs a point (±15°): the
    # tier — not a tuned global constant — sets the bar (tiers come from directional sensitivity).
    recs = _records([20.0, 22.0, 24.0, 21.0, 23.0, 22.0])
    assert nn.rolling_trust_verdict(recs, tier="exposed")["verdict"] == "PASS"
    assert nn.rolling_trust_verdict(recs, tier="point")["verdict"] == "FAIL"


# --------------------------------------------------------------------------- #
# History log round-trip — append-only, de-duped, never touches prod data      #
# --------------------------------------------------------------------------- #
def test_history_append_is_deduped_and_windowed(tmp_path, monkeypatch):
    monkeypatch.setattr(nn, "TRUST_HISTORY_DIR", tmp_path / "hist")
    recs = _records([1.0, 2.0, 3.0])                        # t = 0, 24, 48
    assert nn.append_trust_history("okx", "44025", recs) == 3
    assert nn.append_trust_history("okx", "44025", recs) == 0, "same timestamps de-duped"
    assert nn.append_trust_history("okx", "44025", _records([9.0], gap=1)[:0]) == 0  # empty is a no-op
    loaded = nn.load_trust_history("okx", "44025")
    assert len(loaded) == 3 and [r["t"] for r in loaded] == [0, 24, 48]
    # day-windowing keeps only recent records relative to a supplied "now"
    recent = nn.load_trust_history("okx", "44025", days=1, now_epoch_hour=48)
    assert [r["t"] for r in recent] == [24, 48], "last 24 h (t ≥ 48−24) only"


# --------------------------------------------------------------------------- #
# Node selectors (unchanged) — the depth-experiment geometry                   #
# --------------------------------------------------------------------------- #
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


# --------------------------------------------------------------------------- #
# Pairing audit — structural validity of the buoy as a directional reference   #
# --------------------------------------------------------------------------- #
def test_pairing_audit_scoring_offline():
    # a DEEP bank/ledge buoy paired to a shallow nearshore node is STRUCTURALLY INVALID
    inv, reasons = nn._score_pairing({"payload": None, "depth_m": 76.0, "note": "deep ledge"})
    assert inv == "STRUCTURALLY INVALID" and any("DEEP" in r for r in reasons)
    # a foam SCOOP discus is MARGINAL (noisier direction for low-energy swell)
    marg, _ = nn._score_pairing({"payload": "3-m foam SCOOP discus", "depth_m": None, "note": None})
    assert marg == "MARGINAL"
    # a Datawell Waverider with no red flags is a VALID REFERENCE
    val, vr = nn._score_pairing({"payload": "Datawell Waverider", "depth_m": None, "note": None})
    assert val == "VALID REFERENCE" and any("high-quality" in r for r in vr)
    # MODALITY: a complex / multi-directional approach is a MARGINAL reference (ambiguous mean dir)
    cx, _ = nn._score_pairing({"payload": None, "depth_m": None,
                               "note": "Chesapeake mouth — complex, multi-directional approaches"})
    assert cx == "MARGINAL"
    # known metadata drives it offline — 44098 deep, 44091 Waverider, 44025 discus, 44099 complex
    assert nn._score_pairing(nn._ndbc_station_meta("44098"))[0] == "STRUCTURALLY INVALID"
    assert nn._score_pairing(nn._ndbc_station_meta("44091"))[0] == "VALID REFERENCE"
    assert nn._score_pairing(nn._ndbc_station_meta("44025"))[0] == "MARGINAL"
    assert nn._score_pairing(nn._ndbc_station_meta("44099"))[0] == "MARGINAL"


# --------------------------------------------------------------------------- #
# --find-buoy — search for the best VALID directional reference (read-only)     #
# --------------------------------------------------------------------------- #
def test_score_pairing_structural_vs_soft():
    # a DEEP-water depth mismatch is STRUCTURAL — invalid no matter how good everything else is
    v, _ = nn._score_pairing({"payload": "Datawell Waverider", "depth_m": 76.0, "distance_km": 5.0})
    assert v == "STRUCTURALLY INVALID", "a good Waverider on a deep bank is still the wrong regime"
    # SOFT concerns never SUM into structural: a foam discus + a far distance = MARGINAL, not invalid
    v2, r2 = nn._score_pairing({"payload": "3-m foam discus", "depth_m": 20.0, "distance_km": 130.0})
    assert v2 == "MARGINAL"
    assert any("far" in x for x in r2) and any("noisier" in x for x in r2)
    # a shallow Waverider, close, open exposure → VALID REFERENCE
    v3, _ = nn._score_pairing({"payload": "Datawell Waverider", "depth_m": 18.0, "distance_km": 15.0})
    assert v3 == "VALID REFERENCE"
    # distance is scored ONLY when supplied → --pairing-audit (no distance_km) is byte-unchanged
    base, rb = nn._score_pairing({"payload": "Datawell Waverider", "depth_m": 18.0})
    assert base == "VALID REFERENCE" and not any("km from target" in x for x in rb)
    # a sheltered / bay exposure note is a soft MARGINAL (a poor single-direction proxy)
    v4, _ = nn._score_pairing({"payload": "Datawell Waverider", "depth_m": 20.0,
                               "note": "Cape Cod Bay — SHELTERED, not the open coast"})
    assert v4 == "MARGINAL"


def test_find_buoy_ranking_best_first():
    target = (43.0, -70.7)
    stations = [
        {"id": "deep1", "lat": 43.00, "lng": -70.6, "name": "deep bank"},     # ~8 km, deep → invalid
        {"id": "good",  "lat": 43.05, "lng": -70.7, "name": "nearshore WR"},  # ~6 km, shallow → valid
        {"id": "marg",  "lat": 43.20, "lng": -70.7, "name": "discus"},        # ~22 km → marginal
        {"id": "far",   "lat": 45.00, "lng": -70.7, "name": "too far"},       # >150 km → excluded
    ]
    meta = {"deep1": {"payload": "Datawell Waverider", "depth_m": 70.0},
            "good":  {"payload": "Datawell Waverider", "depth_m": 18.0},
            "marg":  {"payload": "3-m foam discus", "depth_m": 22.0},
            "far":   {"payload": "Datawell Waverider", "depth_m": 15.0}}
    rows = nn._rank_candidates(*target, stations, 150.0, meta_fn=lambda i: meta[i])
    ids = [r["id"] for r in rows]
    assert "far" not in ids, "beyond radius is excluded"
    assert ids[0] == "good", "the VALID nearshore Waverider ranks first"
    assert ids.index("marg") < ids.index("deep1"), "MARGINAL outranks STRUCTURALLY INVALID"
    best = nn._best_valid(rows)
    assert best is not None and best["id"] == "good"


def test_find_buoy_none_qualifies_is_honest():
    # a Gulf-of-Maine-like set: every candidate is deep or sheltered → NO valid reference. The
    # search must return None, not dress up a least-bad option as valid (the whole point of task 4).
    target = (43.0, -70.7)
    stations = [
        {"id": "d62", "lat": 43.1, "lng": -70.7, "name": "shelf 62 m"},
        {"id": "d76", "lat": 42.8, "lng": -70.2, "name": "bank 76 m"},
        {"id": "bay", "lat": 41.9, "lng": -70.3, "name": "sheltered bay WR"},
    ]
    meta = {"d62": {"payload": None, "depth_m": 62.0},
            "d76": {"payload": "Datawell Waverider", "depth_m": 76.0},
            "bay": {"payload": "Datawell Waverider", "depth_m": 25.0,
                    "note": "SHELTERED bay, not the open coast"}}
    rows = nn._rank_candidates(*target, stations, 150.0, meta_fn=lambda i: meta[i])
    assert nn._best_valid(rows) is None, "no VALID reference — must not return a least-bad option"
    by = {r["id"]: r["verdict"] for r in rows}
    assert by["bay"] == "MARGINAL", "shallow WR but sheltered exposure → soft, not valid"
    assert by["d62"] == "STRUCTURALLY INVALID" and by["d76"] == "STRUCTURALLY INVALID"


def test_find_buoy_demotes_no_spectral_and_unknown_metadata():
    target = (43.0, -70.7)
    stations = [
        {"id": "wr",  "lat": 43.02, "lng": -70.7, "name": "waverider, no spec files"},
        {"id": "wr2", "lat": 43.04, "lng": -70.7, "name": "waverider, spec ok"},
        {"id": "unk", "lat": 43.01, "lng": -70.7, "name": "unknown depth"},
    ]
    meta = {"wr":  {"payload": "Datawell Waverider", "depth_m": 18.0},
            "wr2": {"payload": "Datawell Waverider", "depth_m": 18.0},
            "unk": {"payload": None, "depth_m": None}}
    spec = {"wr": False, "wr2": True, "unk": None}   # wr publishes no .data_spec/.swdir → unusable
    rows = nn._rank_candidates(*target, stations, 150.0, meta_fn=lambda i: meta[i],
                               spectral_fn=lambda i: spec[i])
    ids = [r["id"] for r in rows]
    assert ids[0] == "wr2", "the usable Waverider (spectral present) ranks first"
    assert ids[-1] == "wr", "a Waverider with NO spectral files is unusable → ranks last"
    best = nn._best_valid(rows)
    assert best["id"] == "wr2", "unknown-depth / no-spectral candidates are never the recommendation"


def test_water_depth_parse_tolerates_html_tags():
    import re
    # NDBC renders depth as '<b>Water depth:</b> 20.45 m<br>' — the parser must skip the </b> tag.
    assert nn._parse_water_depth("<b>Water depth:</b> 20.45 m<br>") == 20.45
    assert nn._parse_water_depth("<b>Water depth:</b> 60 m<br>") == 60.0
    # the OLD pattern (no tag tolerance) FAILED these — the exact bug this fixes (regression guard)
    old = re.compile(r"[Ww]ater depth:\s*([\d.]+)\s*m")
    assert old.search("<b>Water depth:</b> 20.45 m<br>") is None
    assert old.search("<b>Water depth:</b> 60 m<br>") is None
    # plain text (no tags) still works; absent / implausible → None (sanity-bounded, never poisons)
    assert nn._parse_water_depth("Water depth: 12.5 m") == 12.5
    assert nn._parse_water_depth("no depth listed here") is None
    assert nn._parse_water_depth("<b>Water depth:</b> 999999 m") is None   # > 12000 m sanity bound
    assert nn._parse_water_depth("") is None


def test_find_buoy_probes_the_nearest_candidates_not_roster_order():
    """DEFECT 1 — the probe cap must be spent NEAREST-FIRST. _rank_candidates calls spectral_fn
    inside the station loop, so whatever order that loop runs in decides which stations get probed.
    Iterating the roster directly spent the cap in activestations.xml order: ljpc1 (La Jolla, 1 km,
    both endpoints 404) went unprobed while stations 130 km away were probed, and the "probed the N
    nearest" message was untrue."""
    target = (32.87, -117.25)
    # roster order puts the FAR station first, exactly as activestations.xml did for ljpc1
    stations = [{"id": "far", "lat": 34.05, "lng": -117.25, "name": "Far"},          # ~131 km
                {"id": "near", "lat": 32.879, "lng": -117.25, "name": "Near"}]       # ~1 km
    meta = {"far": {"depth_m": 20.0, "payload": "Datawell Waverider"},
            "near": {"depth_m": 18.0, "payload": "Datawell Waverider"}}
    CAP = 1
    probed, order = [], []

    def _spec(bid):                       # mirrors find_buoy's _spec closure: capped, single-shot
        order.append(bid)
        if len(probed) >= CAP:
            return None                   # not probed (cap) → UNKNOWN
        probed.append(bid)
        return True
    rows = nn._rank_candidates(*target, stations, 150.0, meta_fn=lambda i: meta[i], spectral_fn=_spec)
    assert probed == ["near"], f"cap spent on the wrong station: probed={probed} order={order}"
    assert order[0] == "near", f"spectral_fn called in roster order, not distance order: {order}"
    # and the probed near station is the recommendation, not the unprobed far one
    assert rows[0]["id"] == "near" and rows[0]["spectral"] is True
    assert nn._best_valid(rows)["id"] == "near"


def test_find_buoy_unknown_spectral_ranks_below_confirmed():
    """DEFECT 2 — `is False` let None (not probed → UNKNOWN) escape demotion and tie with a
    confirmed live directional buoy on distance alone. That is how ljpc1, which publishes nothing,
    outranked 46254 SCRIPPS Nearshore at the same distance. Unknown must sort BELOW confirmed and
    ABOVE confirmed-absent — never discarded, since one transient probe failure must not bin a
    good buoy."""
    target = (32.87, -117.25)
    stations = [{"id": "unknown", "lat": 32.879, "lng": -117.25, "name": "Unknown"},
                {"id": "confirmed", "lat": 32.879, "lng": -117.25, "name": "Confirmed"},
                {"id": "absent", "lat": 32.879, "lng": -117.25, "name": "Absent"}]
    m = {"depth_m": 18.0, "payload": "Datawell Waverider"}
    spec = {"unknown": None, "confirmed": True, "absent": False}
    rows = nn._rank_candidates(*target, stations, 150.0,
                               meta_fn=lambda i: dict(m), spectral_fn=lambda i: spec[i])
    # identical verdict and identical distance — only the spectral state separates them
    assert [r["id"] for r in rows] == ["confirmed", "unknown", "absent"], [r["id"] for r in rows]
    assert len({r["d"] for r in rows}) == 1 and len({r["verdict"] for r in rows}) == 1
    assert nn._best_valid(rows)["id"] == "confirmed", "a confirmed axis must win the recommendation"


def test_find_buoy_unknown_spectral_does_not_fire_depth_fallback():
    """DEFECT 2 (second half) — _depth_unconfirmed_valid had the same `is False` test. It already
    accepts an unresolved DEPTH; accepting an unprobed AXIS too would stack two unknowns into a
    VALID REFERENCE, which is what recommended ljpc1. Confirmed spectra required here."""
    def _row(**kw):
        base = {"d": 10.0, "id": "x", "name": "Nearshore", "depth": None,
                "payload": "Datawell Waverider", "spectral": True,
                "verdict": "VALID REFERENCE", "complete": False, "reasons": []}
        base.update(kw)
        return base
    assert nn._depth_unconfirmed_valid(_row(spectral=True)) is True      # confirmed → fires
    assert nn._depth_unconfirmed_valid(_row(spectral=None)) is False     # UNKNOWN → declines
    assert nn._depth_unconfirmed_valid(_row(spectral=False)) is False    # absent → declines
    # and it does not leak through _best_valid either
    assert nn._best_valid([_row(spectral=None)]) is None
    assert nn._best_valid([_row(spectral=True)])["id"] == "x"
    # a DEPTH-RESOLVED row is unaffected — this tightening touches only the no-depth fallback
    assert nn._best_valid([_row(spectral=None, depth=18.0, complete=True)])["id"] == "x"


def test_find_buoy_depth_unconfirmed_waverider_fallback():
    # spectral defaults to True (CONFIRMED): this fallback already accepts an unresolved DEPTH, so
    # it requires the directional axis to be confirmed rather than merely not-known-absent — see
    # test_find_buoy_unknown_spectral_does_not_fire_depth_fallback for the None case.
    def _row(**kw):
        base = {"d": 10.0, "id": "x", "name": "", "depth": None, "payload": None,
                "spectral": True, "verdict": "VALID REFERENCE", "complete": False, "reasons": []}
        base.update(kw)
        return base
    # a CLOSE Waverider whose depth didn't resolve (None) IS accepted VALID on payload+distance
    wr = _row(id="46268", name="Topanga Nearshore", payload="Datawell Waverider", d=10.0)
    assert nn._depth_unconfirmed_valid(wr) is True
    assert nn._best_valid([wr])["id"] == "46268"
    # a CDIP 'Nearshore' station with no payload string still qualifies on the name signal
    assert nn._depth_unconfirmed_valid(_row(name="Del Mar Nearshore", d=12.0)) is True
    # too far (> FIND_BUOY_FALLBACK_KM) → NOT accepted on the fallback
    assert nn._depth_unconfirmed_valid(_row(payload="Datawell Waverider", d=55.0)) is False
    assert nn._best_valid([_row(payload="Datawell Waverider", d=55.0)]) is None
    # a known-DEEP buoy (resolved depth ≥ threshold) scores INVALID → fallback can NEVER touch it
    deep = _row(id="44098", depth=76.0, payload="Datawell Waverider", d=5.0,
                verdict="STRUCTURALLY INVALID", complete=True)
    assert nn._depth_unconfirmed_valid(deep) is False and nn._best_valid([deep]) is None
    # depth-None but NOT a Waverider / not a Nearshore name (e.g. a discus) → not fallback-eligible
    assert nn._depth_unconfirmed_valid(_row(name="Offshore", payload="3-m discus",
                                            verdict="MARGINAL")) is False
    # a no-spectral Waverider is unusable even close-in
    assert nn._depth_unconfirmed_valid(_row(payload="Datawell Waverider", spectral=False)) is False
    # a depth-RESOLVED VALID Waverider is still recommended the normal (confirmed) way, and PREFERRED
    ok = _row(id="y", name="Nearshore", depth=18.0, payload="Datawell Waverider", complete=True)
    assert nn._best_valid([ok])["id"] == "y" and nn._best_valid([ok])["complete"] is True
    # ranking preference: a confirmed VALID outranks an unconfirmed fallback (rows already sorted)
    assert nn._best_valid([ok, wr])["id"] == "y", "confirmed depth beats depth-unconfirmed fallback"


def test_resolve_find_target():
    (la, ln), lab = nn._resolve_find_target(None, "42.8,-70.17", None)
    assert abs(la - 42.8) < 1e-9 and abs(ln + 70.17) < 1e-9 and "42.8" in lab
    # --near-buoy resolves via the live active list, or the cited seed offline — either gives 44098's
    # coordinates (tolerance covers both paths); no hardcoded guess for an unknown id.
    (bla, bln), blab = nn._resolve_find_target(None, None, "44098")
    assert abs(bla - 42.800) < 0.05 and abs(bln + 70.169) < 0.05 and "44098" in blab
    for args in [(None, None, "99999"), (None, None, None)]:
        try:
            nn._resolve_find_target(*args)
            raised = False
        except ValueError:
            raised = True
        assert raised, f"expected ValueError (honest, not a guess) for {args}"


def test_retired_reference_zones_parsing(tmp_path, monkeypatch):
    f = tmp_path / "assign.json"
    f.write_text(json.dumps({
        # HEIGHT-tagging gate stays PASS on BOTH zones — only the CHECK is retired
        "trust_by_zone": {"box/44098": "PASS", "gyx/44098": "PASS"},
        "spots": [],
        "buoy_reference": {"retired": [
            {"zone": "box/44098", "wfo": "box", "buoy": "44098", "spots": 3,
             "axes": ["height", "direction"], "reason": "deep bank; no valid nearshore buoy on either axis"},
            {"zone": "gyx/44098", "wfo": "gyx", "buoy": "44098", "spots": 11,
             "axes": ["height", "direction"], "reason": "same geography"},
        ]},
    }))
    monkeypatch.setattr(nn, "NWPS_ASSIGNMENTS", f)
    r = nn._retired_reference_zones()
    assert set(r.keys()) == {("box", "44098"), ("gyx", "44098")}
    assert r[("box", "44098")]["spots"] == 3 and r[("box", "44098")]["axes"] == ["height", "direction"]
    assert "deep bank" in r[("box", "44098")]["reason"]
    # a missing file / missing section → {} (never raises; a zone is simply "not retired")
    monkeypatch.setattr(nn, "NWPS_ASSIGNMENTS", tmp_path / "does_not_exist.json")
    assert nn._retired_reference_zones() == {}
    f.write_text(json.dumps({"trust_by_zone": {}, "spots": []}))   # no buoy_reference section
    monkeypatch.setattr(nn, "NWPS_ASSIGNMENTS", f)
    assert nn._retired_reference_zones() == {}


def test_banking_guard_skips_inconclusive_but_banks_pass_and_fail():
    # option (b): _bank_records must NOT call append_trust_history on an INCONCLUSIVE (height not
    # assessable) window; PASS and FAIL still bank (height WAS assessable). Verified via a spy so no
    # disk is touched.
    calls = []
    orig = nn.append_trust_history
    nn.append_trust_history = lambda wfo, buoy, records: (calls.append((wfo, buoy, list(records))), len(records))[1]
    try:
        recs = [{"t": 0, "residual": 5.0, "weight": 1.0}]
        added, skip = nn._bank_records("mtr", "46237", {"verdict": "INCONCLUSIVE", "reason": "flat", "records": recs})
        assert added == 0 and skip and not calls, "INCONCLUSIVE banks nothing; append_trust_history NOT called"
        added, skip = nn._bank_records("box", "44097", {"verdict": "PASS", "records": recs})
        assert added == 1 and skip is None and len(calls) == 1, "PASS banks"
        added, skip = nn._bank_records("phi", "44025", {"verdict": "FAIL", "records": recs})
        assert added == 1 and skip is None and len(calls) == 2, "FAIL still banks (height was assessable)"
    finally:
        nn.append_trust_history = orig


def test_variance_floor_075_makes_marginal_range_inconclusive():
    # option (a): TRUST_BUOY_RANGE_MIN_M raised 0.5→0.75 — a buoy total-Hs span of 0.6 m now returns
    # INCONCLUSIVE (was assessable at the old 0.5 floor); a 0.9 m span still assesses (r computed).
    assert nn.TRUST_BUOY_RANGE_MIN_M == 0.75, "variance floor is 0.75 m"

    def samples(span, n=8):   # model_swh tracks buoy_wvht (r≈1 when assessable); buoy WVHT spans `span`
        return [{"t": i, "model_swh": 1.0 + span * i / (n - 1), "buoy_wvht": 1.0 + span * i / (n - 1)}
                for i in range(n)]

    r06 = nn.swell_trust_verdict(samples(0.6))
    assert r06["verdict"] == "INCONCLUSIVE" and "flat" in (r06["reason"] or ""), \
        "0.6 m total-Hs span < 0.75 floor → height not assessable (was assessable at 0.5)"
    r09 = nn.swell_trust_verdict(samples(0.9))
    assert r09["verdict"] == "PASS" and r09["height_r"] == r09["height_r"] and r09["height_r"] >= 0.80, \
        "0.9 m span ≥ 0.75 → assessable; a tracking model → r≈1 PASS"


def _expected_pending_zones(doc, enriched):
    """(all_pending_zone_keys, the_ones_reverify_must_cover) as (wfo, buoy) string pairs.

    A pending zone belongs in the EXPECTED set only when at least one spot carries that SAME
    nwps_wfo AND that SAME nwps_buoy_id and has swell_window_source == 'nwps'. Scoping by the full
    zone key rather than by wfo is the point: a wfo can have placed spots from an earlier batch
    while a newly registered zone on a different buoy in the same wfo has none of its own yet.
    Shared by the real-data guard and the synthetic regression below, so the synthetic case
    exercises the actual rule instead of a re-implementation of it."""
    pending = {(r["wfo"], str(r["buoy"]))
               for r in (doc.get("buoy_reference") or {}).get("pending") or []
               if r.get("wfo") and r.get("buoy") is not None}
    placed = {(s.get("nwps_wfo"), str(s.get("nwps_buoy_id"))) for s in enriched
              if s.get("swell_window_source") == "nwps" and s.get("nwps_buoy_id") is not None}
    return pending, pending & placed


def test_reverify_guard_scopes_by_zone_not_by_wfo():
    """A wfo with placed spots on ONE buoy and a newly registered zone on a DIFFERENT buoy must not
    be demanded coverage it cannot have. This is the phi shape: 29 phi spots placed on 44065/44091,
    then phi/44084 registered with nothing of its own placed yet. Under the old wfo-only scoping the
    guard failed on ('phi','44084') purely because some OTHER phi buoy had placements."""
    doc = {"buoy_reference": {"pending": [
        {"wfo": "phi", "buoy": "44091", "slugs": []},     # placed: 15 spots of its own
        {"wfo": "phi", "buoy": "44084", "slugs": []},     # registered, NOTHING placed yet
        {"wfo": "mtr", "buoy": "46237", "slugs": []},     # placed, unrelated wfo
    ]}}
    enriched = ([{"nwps_wfo": "phi", "nwps_buoy_id": "44091", "swell_window_source": "nwps"}] * 15
                + [{"nwps_wfo": "phi", "nwps_buoy_id": "44065", "swell_window_source": "nwps"}] * 14
                + [{"nwps_wfo": "mtr", "nwps_buoy_id": "46237", "swell_window_source": "nwps"}]
                # 44084's own spots exist but are NOT yet placed
                + [{"nwps_wfo": "phi", "nwps_buoy_id": None, "swell_window_source": "raycast"}] * 22)
    pending, expected = _expected_pending_zones(doc, enriched)
    assert pending == {("phi", "44091"), ("phi", "44084"), ("mtr", "46237")}
    # the newly registered zone is EXCLUDED; the two with their own placed spots are NOT
    assert expected == {("phi", "44091"), ("mtr", "46237")}, expected
    assert ("phi", "44084") not in expected, "unplaced zone demanded because its WFO has placements"

    # the guard then passes against a roster that legitimately lacks phi/44084 ...
    zones = {("phi", "44091"), ("phi", "44065"), ("mtr", "46237")}
    assert not (expected - zones), "guard must not fail on a registered-but-unplaced zone"
    # ... and is NOT weakened: a zone that DOES have placed spots is still demanded, so dropping
    # phi/44091 from the reverify roster still fails.
    assert expected - {("phi", "44065"), ("mtr", "46237")} == {("phi", "44091")}

    # wfo-only scoping (what PR #114 did) would have pulled the unplaced zone in — the regression
    placed_wfos = {s["nwps_wfo"] for s in enriched if s["swell_window_source"] == "nwps"}
    wfo_only = {(w, b) for w, b in pending if w in placed_wfos}
    assert ("phi", "44084") in wfo_only, "fixture no longer reproduces the wfo-only failure"
    assert wfo_only - expected == {("phi", "44084")}

    # once 44084's spots ARE placed it is demanded again, so the fix defers rather than exempts
    enriched2 = enriched + [{"nwps_wfo": "phi", "nwps_buoy_id": "44084", "swell_window_source": "nwps"}]
    _, expected2 = _expected_pending_zones(doc, enriched2)
    assert ("phi", "44084") in expected2


def test_reverify_covers_pending_zones_not_just_pass():
    # Part 1(d): _tagged_nwps_zones keys on swell_window_source=='nwps', so it returns EVERY placed
    # zone — the PASS/verified ones AND the PENDING ones — so scheduling reverify accumulates for the
    # pending zones we care about (the stale docstring's "PASS from the OLD gate" notwithstanding).
    # The expected pending set is READ from the assignments file so it can't go stale: 46240/46284
    # were moved to unverifiable[] in the Monterey-Bay reference-offline change and are correctly NO
    # LONGER pending, and any future pending edits are picked up automatically.
    #
    # REGISTERING a pending zone and PLACING its spots are separate steps, so a zone can legitimately
    # sit in pending[] with nothing placed behind it yet. _tagged_nwps_zones keys on PLACED spots, so
    # such a zone is correctly absent from it and must not be demanded here.
    #
    # SCOPED BY ZONE KEY (wfo AND buoy), not by wfo alone. A wfo can carry placed spots from an
    # EARLIER batch while a newly registered zone for a DIFFERENT buoy in that same wfo has none yet:
    # phi is exactly this shape — 29 phi spots are placed on 44065/44091, so wfo-only scoping demanded
    # coverage for the freshly registered phi/44084 that it cannot have until apply runs. A registered
    # zone with no placed spots OF ITS OWN is legitimately absent from _tagged_nwps_zones regardless
    # of whether its wfo has other placements. For zones that DO have their own placed spots the
    # assertion is unchanged and undiluted.
    import json
    from pipeline.forecast.nwps_nearshore import NWPS_ASSIGNMENTS, ENRICHED
    zones = {(w, str(b)) for w, b, _ in nn._tagged_nwps_zones() if b is not None}
    doc = json.loads(NWPS_ASSIGNMENTS.read_text())
    pending, expected = _expected_pending_zones(doc, json.loads(ENRICHED.read_text()))
    assert pending, "expected at least one pending zone in the assignments file"
    assert expected, "expected at least one PLACED pending zone (all pending zones are unplaced?)"
    missing = expected - zones
    assert not missing, f"reverify must cover the PLACED pending zones; missing {missing}"


def test_reverify_emit_settled_to_github_output():
    # the accumulator emits SETTLED zones to $GITHUB_OUTPUT (for the workflow's manual-tagging issue);
    # a no-op when the env var is unset (Mac runs). Never tags anything — this is report-only.
    import os
    import tempfile
    settled = [{"zone": "mtr/46237", "wfo": "mtr", "buoy": "46237", "spots": 13,
                "verdict": "PASS", "n_events": 5, "reason": None}]
    orig = os.environ.get("GITHUB_OUTPUT")
    fd, path = tempfile.mkstemp()
    os.close(fd)
    try:
        os.environ["GITHUB_OUTPUT"] = path
        nn._emit_reverify_output(settled)
        nn._emit_reverify_output([])          # second call: nothing settled
        text = open(path).read()
        assert "any_settled=true" in text and '"zone":"mtr/46237"' in text and '"verdict":"PASS"' in text
        assert "any_settled=false" in text     # the empty call
        os.environ.pop("GITHUB_OUTPUT", None)
        nn._emit_reverify_output(settled)       # no env → silent no-op (must not raise, writes nothing)
        assert open(path).read() == text
    finally:
        if orig is not None:
            os.environ["GITHUB_OUTPUT"] = orig
        else:
            os.environ.pop("GITHUB_OUTPUT", None)
        os.unlink(path)


# --------------------------------------------------------------------------- #
# --find-buoy validity gaps: LIVENESS + directional-axis availability          #
# (the gate must not accept "not confirmed broken" as "confirmed good")        #
# --------------------------------------------------------------------------- #
class _FakeResp:
    """Minimal stand-in for a requests.Response (status_code + text) for probe tests."""
    def __init__(self, status_code, text="data"):
        self.status_code = status_code
        self.text = text


def _fb_row(**kw):
    base = {"d": 6.0, "id": "x", "name": "", "depth": 18.0, "payload": "Datawell Waverider",
            "spectral": True, "verdict": "VALID REFERENCE", "complete": True, "reasons": [],
            "age_h": None}
    base.update(kw)
    return base


def test_find_buoy_dead_buoy_not_recommended():
    # (1) a buoy with valid metadata but no recent observation is NOT recommended.
    live = _fb_row(id="live", age_h=2)
    dead = _fb_row(id="dead", age_h=43 * 24)            # 43 days silent (the 46284 case)
    assert nn._best_valid([live])["id"] == "live"       # a live VALID reference IS recommended
    assert nn._best_valid([dead]) is None               # DEAD (no recent obs) is NOT, despite metadata
    assert nn._best_valid([dead, live])["id"] == "live"  # dead skipped, live chosen
    # the verdict states WHY, with the number
    assert nn._liveness_verdict(43 * 24)[0] == "dead" and "43 days" in nn._liveness_verdict(43 * 24)[1]
    assert nn._liveness_verdict(26)[0] == "dead" and "26 h" in nn._liveness_verdict(26)[1]
    assert nn._liveness_verdict(2)[0] == "alive"


def test_find_buoy_404_makes_direction_unavailable():
    # (2) a 404 on .data_spec makes a buoy unselectable as a directional reference.
    resp404 = lambda url, timeout=None: _FakeResp(404, "<html>404</html>")
    assert nn._probe_direction_axis("sipf1", _get=resp404) == ("unavailable", ".data_spec 404")
    # 404 -> spectral False -> _best_valid rejects it even with otherwise-perfect metadata
    row = _fb_row(id="sipf1", spectral=False)
    assert nn._best_valid([row]) is None
    # 410 (Gone) is also permanent
    assert nn._probe_direction_axis("x", _get=lambda url, timeout=None: _FakeResp(410, ""))[0] == "unavailable"


def _by_url(mapping, default=None):
    """A fetcher fake that answers PER ENDPOINT, so the two probed files can differ. Values are a
    _FakeResp or a callable raising a transport error. Records the URLs probed, in order."""
    seen = []

    def _get(url, timeout=None):
        seen.append(url)
        for suffix, resp in mapping.items():
            if url.endswith(suffix):
                if callable(resp):
                    return resp()
                return resp
        if default is None:
            raise AssertionError(f"unexpected probe URL: {url}")
        return default
    return _get, seen


def test_find_buoy_data_spec_without_swdir_is_unavailable():
    """LJPC1 (La Jolla, CA 073): 200 on .data_spec, 404 on .swdir and .swr1. .data_spec carries
    energy density by frequency ONLY — no directional moments — so a 200 there is not proof of a
    direction axis. Probing .data_spec alone rated it as having one and it was recommended as a
    VALID directional reference for seven San Diego spots."""
    SPEC = "2026 07 24 12 00  0.100  8.0 (0.08)"
    get, seen = _by_url({".data_spec": _FakeResp(200, SPEC), ".swdir": _FakeResp(404, "<html>404</html>")})
    assert nn._probe_direction_axis("ljpc1", _get=get) == ("unavailable", ".swdir 404")
    assert any(u.endswith(".swdir") for u in seen), f".swdir was never probed: {seen}"
    # unavailable -> spectral False -> not selectable, and it cannot ride the depth fallback either
    assert nn._best_valid([_fb_row(id="ljpc1", spectral=False)]) is None
    assert nn._depth_unconfirmed_valid(_fb_row(id="ljpc1", spectral=False, depth=None,
                                               complete=False)) is False
    # 410 on .swdir is equally permanent, and an empty 200 body is still treated as absent
    g410, _ = _by_url({".data_spec": _FakeResp(200, SPEC), ".swdir": _FakeResp(410, "")})
    assert nn._probe_direction_axis("x", _get=g410) == ("unavailable", ".swdir 410")
    gempty, _ = _by_url({".data_spec": _FakeResp(200, SPEC), ".swdir": _FakeResp(200, "")})
    assert nn._probe_direction_axis("x", _get=gempty) == ("unavailable", ".swdir empty")
    # a .data_spec 404 short-circuits: no point probing the moments, and it stays the reported file
    g404, seen404 = _by_url({".data_spec": _FakeResp(404, "<html>404</html>"),
                             ".swdir": _FakeResp(200, SPEC)})
    assert nn._probe_direction_axis("x", _get=g404) == ("unavailable", ".data_spec 404")
    assert not any(u.endswith(".swdir") for u in seen404), "short-circuit failed, .swdir probed anyway"


def test_find_buoy_both_files_present_is_available():
    """46254 / 44095 / 41120 shape: energy spectrum AND directional moments both published."""
    SPEC = "2026 07 24 12 00  0.100  8.0 (0.08)"
    SWDIR = "2026 07 24 12 00  180.0 175.0 170.0"
    get, seen = _by_url({".data_spec": _FakeResp(200, SPEC), ".swdir": _FakeResp(200, SWDIR)})
    assert nn._probe_direction_axis("46254", _get=get) == ("available", None)
    assert [u.rsplit(".", 1)[-1] for u in seen] == ["data_spec", "swdir"], seen
    # available -> spectral True -> selectable, exactly as before this change
    assert nn._best_valid([_fb_row(id="46254", spectral=True, age_h=2)])["id"] == "46254"


def test_find_buoy_swdir_timeout_is_unknown_not_unavailable():
    """A transient failure on the SECOND file must not read as permanent absence — otherwise a blip
    on .swdir would blacklist a real directional buoy. The single retry applies to it too."""
    import requests
    SPEC = "2026 07 24 12 00  0.100  8.0 (0.08)"

    def _timeout():
        raise requests.Timeout("simulated")
    get, seen = _by_url({".data_spec": _FakeResp(200, SPEC), ".swdir": _timeout})
    state, detail = nn._probe_direction_axis("x", _get=get)
    assert state == "unknown", f"transient .swdir failure read as {state}"
    assert ".swdir" in detail and "Timeout" in detail, detail
    assert sum(1 for u in seen if u.endswith(".swdir")) == 2, f"retry not applied to .swdir: {seen}"
    # unknown -> spectral None -> not selectable on faith, but NOT blacklisted (ranks mid, not last)
    spectral = {"available": True, "unavailable": False, "unknown": None}[state]
    assert spectral is None
    # a 5xx on .swdir is likewise transient, and a blip that recovers on the retry still succeeds
    g5, _ = _by_url({".data_spec": _FakeResp(200, SPEC), ".swdir": _FakeResp(503, "")})
    assert nn._probe_direction_axis("x", _get=g5) == ("unknown", ".swdir 503")
    calls = {"n": 0}

    def _flaky():
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("blip")
        return _FakeResp(200, "2026 07 24 12 00  180.0")
    gflaky, _ = _by_url({".data_spec": _FakeResp(200, SPEC), ".swdir": _flaky})
    assert nn._probe_direction_axis("x", _get=gflaky) == ("available", None)


def test_find_buoy_timeout_is_unknown_not_permanent():
    # (3) a simulated timeout yields UNKNOWN — not VALID and not permanently invalid.
    import requests

    def persistent(url, timeout=None):
        raise requests.Timeout("simulated")

    state, detail = nn._probe_direction_axis("x", _get=persistent)
    assert state == "unknown" and "Timeout" in detail             # surfaced with the reason
    spectral = {"available": True, "unavailable": False, "unknown": None}[state]
    assert spectral is None                                        # None: not VALID (True), not blacklisted (False)
    # a network blip that recovers on the retry must NOT blacklist a good buoy
    calls = {"n": 0}

    def flaky(url, timeout=None):
        calls["n"] += 1
        if calls["n"] == 1:
            raise requests.ConnectionError("blip")
        return _FakeResp(200, "2026 07 24 12 00  0.100  8.0 (0.08)")

    assert nn._probe_direction_axis("x", _get=flaky) == ("available", None)


def test_find_buoy_stale_roster_does_not_report_alive():
    # (4) a stale cached roster does not produce a false 'alive' verdict.
    age, refreshed, stale = nn._find_buoy_roster_freshness(_age_fn=lambda: 72.0, _refresh_fn=lambda: False)
    assert (age, refreshed, stale) == (72.0, False, True)          # stale + unrefreshable is flagged
    # that flag forces liveness UNKNOWN even for an age that would otherwise read 'alive'
    assert nn._liveness_verdict(3, roster_stale=True)[0] == "unknown"
    assert nn._liveness_verdict(3, roster_stale=False)[0] == "alive"   # same age, fresh roster -> alive
    # a young roster is not refetched; a stale-but-refreshable roster refreshes (not stale)
    assert nn._find_buoy_roster_freshness(_age_fn=lambda: 2.0) == (2.0, False, False)
    assert nn._find_buoy_roster_freshness(_age_fn=lambda: 72.0, _refresh_fn=lambda: True) == (0.0, True, False)


def test_find_buoy_live_good_buoy_still_recommended():
    # (5) a live buoy with good spectra is still recommended exactly as before — no regression.
    live = _fb_row(id="46268", name="Topanga Nearshore", depth=20.0, spectral=True, age_h=2)
    assert nn._best_valid([live])["id"] == "46268"
    # and the offline / not-probed path (spectral None, age None) still recommends on metadata
    assert nn._best_valid([_fb_row(id="46268", spectral=None, age_h=None)])["id"] == "46268"


# --------------------------------------------------------------------------- #
# --find-buoy WRONG-SIDE-OF-HEADLAND gate                                       #
# --------------------------------------------------------------------------- #
def _fake_land(polys):
    """A minimal geodata.LandIndex stand-in (.polygons + .polygon_tree) over synthetic polygons,
    so the headland geometry is testable offline without the real GSHHG shapefile."""
    from shapely.strtree import STRtree
    class _L:
        pass
    L = _L(); L.polygons = list(polys); L.polygon_tree = STRtree(L.polygons)
    return L


def _synthetic_cape():
    from shapely.geometry import box
    # ~34 x 39 km east-jutting block ~ 1300 km^2 (well over the 500 km^2 headland floor)
    return box(-80.75, 28.35, -80.40, 28.70)


def test_headland_flags_wrong_side_crossing():
    # a spot NORTH of a cape paired with a buoy SOUTH of it (the Canaveral/41113 shape) is rejected,
    # with the crossed-land chord as its evidence.
    land = _fake_land([_synthetic_cape()])
    v = nn._headland_verdict((28.75, -80.70), (28.30, -80.45), land)
    assert v["reject"] is True
    assert v["chord_km"] > 5.0
    assert v["dist_km"] < nn.FIND_BUOY_HEADLAND_MAX_KM
    # and _best_valid drops any row carrying headland_reject (the gate is wired in)
    assert nn._best_valid([_fb_row(id="X", headland_reject=True)]) is None
    assert nn._best_valid([_fb_row(id="X")])["id"] == "X"     # same row, no headland flag -> selected


def test_headland_clears_open_coast_and_trims_own_shore():
    from shapely.geometry import box
    land = _fake_land([_synthetic_cape()])
    # buoy due east offshore, line stays north of the cape -> no crossing (no-regression)
    v = nn._headland_verdict((28.75, -80.70), (28.75, -80.40), land)
    assert v["reject"] is False and v["chord_km"] == 0.0
    # a spot ~0.5 km inside a big landmass edge with an offshore buoy must NOT flag on its OWN shore
    # (the first MID_START_KM is trimmed) — this is what keeps barrier-coast pairings clean.
    mainland = box(-82.0, 28.0, -80.71, 29.5)
    v2 = nn._headland_verdict((28.75, -80.715), (28.75, -80.40), _fake_land([mainland]))
    assert v2["reject"] is False and v2["chord_km"] == 0.0


def test_headland_distance_ceiling_downgrades_to_note():
    land = _fake_land([_synthetic_cape()])
    near = nn._headland_verdict((28.75, -80.70), (28.30, -80.45), land)   # crosses, ~55 km
    far = nn._headland_verdict((28.75, -80.70), (27.00, -80.45), land)    # crosses, ~195 km
    assert near["reject"] is True
    assert far["reject"] is False and far["note"] is True                 # beyond ceiling -> kept, noted
    assert far["chord_km"] > 0.0 and far["dist_km"] > nn.FIND_BUOY_HEADLAND_MAX_KM


def test_headland_area_floor_ignores_small_land():
    from shapely.geometry import box
    # a ~2x2 km island (< 500 km^2) sitting ON the path is a barrier/inlet bank, not a headland
    small = box(-80.57, 28.50, -80.55, 28.52)
    v = nn._headland_verdict((28.75, -80.70), (28.30, -80.45), _fake_land([small]))
    assert v["reject"] is False and v["chord_km"] == 0.0
    # confirm the fixture really is on the path AND really is sub-floor (guards the test itself)
    from pyproj import Geod
    assert abs(Geod(ellps="WGS84").geometry_area_perimeter(small)[0]) / 1e6 < nn.FIND_BUOY_HEADLAND_AREA_KM2


def test_headland_offline_never_rejects():
    # land None (offline / no GSHHG) must never reject — the gate is a no-op without coastline data
    v = nn._headland_verdict((28.75, -80.70), (28.30, -80.45), None)
    assert v["reject"] is False and v["note"] is False and v["chord_km"] == 0.0


# --------------------------------------------------------------------------- #
# Placement is GEOMETRY — DEAD/OFFWIN removed, land-crossing guard added        #
# --------------------------------------------------------------------------- #
def test_sub_floor_period_no_longer_returns_dead():
    # DEAD was "peak period below PER_FLOOR_S" — a sea-state condition being used to make a
    # permanent geographic decision. A flat hour must not un-place a spot.
    assert nn.placement_verdict(1.0, 2.0, 150, []) == "OK"
    assert nn.placement_verdict(1.0, 0.1, 150, []) == "OK", "no period is low enough to disqualify"
    assert "DEAD" not in {nn.placement_verdict(1.0, p, 150, []) for p in (0.1, 2.0, 2.99, 3.0, 20.0)}


def test_off_window_direction_no_longer_returns_offwin():
    # OFFWIN was "peak direction outside the spot's swell window". Pipeline faces NW and is
    # correctly off-window in August; that is a season, not a geography, and must not decide
    # whether the spot gets NWPS at all. A due-south swell at a north-facing spot now places.
    north_facing = [{"min": 300, "max": 30}]        # NW through N to NNE, wrapping 0/360
    assert nn.placement_verdict(1.0, 12, 170, north_facing) == "OK", "SE trade sea in August"
    assert nn.placement_verdict(1.0, 12, 15, north_facing) == "OK", "December NNE groundswell"
    assert "OFFWIN" not in {nn.placement_verdict(1.0, 12, d, north_facing) for d in range(0, 360, 15)}


def test_placement_needs_no_spectrum_and_ignores_the_condition_args():
    # after the change placement does not require a live wave spectrum at all: the retained
    # per/dirpw/arcs arguments must not move the verdict for ANY value, including None/NaN.
    got = {nn.placement_verdict(1.0, p, d, a)
           for p in (None, float("nan"), 0.0, 2.0, 18.0)
           for d in (None, float("nan"), 0.0, 170.0, 359.0)
           for a in ([], [{"min": 90, "max": 230}], [{"min": 300, "max": 30}])}
    assert got == {"OK"}, f"the ignored args still change the verdict: {got}"


def test_far_and_no_wet_cell_are_unchanged():
    # the two PERMANENT geometric facts survive exactly as they were.
    assert nn.placement_verdict(5.0, 10, 150, []) == "FAR"
    assert nn.placement_verdict(None, 10, 150, []) == "FAR", "no seaward wet cell at all"
    assert nn.placement_verdict(3.35, 10, 150, []) == "FAR", "default cap is still the legacy 3.0"
    assert nn.placement_verdict(3.35, 10, 150, [], far_cap_km=5.0) == "OK", "grid-aware cap intact"
    assert nn.placement_verdict(1.71, 10, 150, [], far_cap_km=3.0) == "OK"
    # NO_WET_CELL is raised by the caller (select_node returning None), not by placement_verdict;
    # both it and FAR still roll up as domain misses, and OK still does not.
    assert nn._is_domain_miss("FAR") and nn._is_domain_miss("NO_WET_CELL")
    assert not nn._is_domain_miss("OK")


def _node_grid(lats1d, lngs1d, wet_ij):
    """A minimal select_node cycle: a lat/lng meshgrid with only *wet_ij* cells unmasked."""
    la, lo = np.meshgrid(np.array(lats1d), np.array(lngs1d), indexing="ij")
    mask = np.ones(la.shape, dtype=bool)
    for i, j in wet_ij:
        mask[i, j] = False
    return {"lats": la, "lons": lo, "mask": mask}


# The land-crossing guard walks the MODEL'S OWN MASK — no GSHHG, no tuned constant. See the
# guard block in nwps_nearshore for the measured reason the GSHHG polygon check was dropped at
# this scale (36+ false positives; the continent polygon defeats the area floor; must-clear
# penetration reaches 396 m while must-flag only clears it once a barrier exceeds 0.79 km, by
# which point the model's own mask already marks it land).
def _mask_grid(nr, nc, lat0, dlat, lng0, dlng, wet):
    la, lo = np.meshgrid(np.array([lat0 + k * dlat for k in range(nr)]),
                         np.array([lng0 + k * dlng for k in range(nc)]), indexing="ij")
    m = np.ones(la.shape, dtype=bool)
    for i, j in wet:
        m[i, j] = False
    return {"lats": la, "lons": lo, "mask": m}


def _barrier_cycle():
    """Deliberately ANISOTROPIC — rows 2.22 km apart (offshore steps), columns 0.49 km apart
    (along the barrier) — so the BAY cell two columns east is NEARER (0.98 km) than the open-ocean
    cell one row north (2.22 km). Spot cell (2,2) is LAND, as a beach is; (2,3) is the barrier.
    This is the only geometry that exhibits the case: a wet cell ADJACENT to the spot's cell is
    always both nearest and unblockable, so the blocked cell has to be two cells out and the clear
    fallback further still."""
    return _mask_grid(4, 6, 28.470, 0.020, -80.620, 0.005,
                      wet=[(2, 4), (1, 2), (0, 2), (1, 3), (0, 3)])


def _barrier_spot(cyc):
    return float(cyc["lats"][2, 2]), float(cyc["lons"][2, 2])


def test_select_node_rejects_a_candidate_behind_land():
    cyc = _barrier_cycle()
    slat, slng = _barrier_spot(cyc)
    bay, ocean = (2, 4), (1, 2)
    d = lambda ij: nn._haversine_km(slat, slng, float(cyc["lats"][ij]), float(cyc["lons"][ij]))
    assert d(bay) < d(ocean), "the blocked cell really is the nearer of the two"
    assert nn._mask_blocked(cyc, slat, slng, *bay), "mask puts land between the spot and the bay"
    assert not nn._mask_blocked(cyc, slat, slng, *ocean), "the fallback is genuinely clear"
    plain = nn.select_node(cyc, slat, slng, None)
    guarded = nn.select_node(cyc, slat, slng, None, avoid_land=True)
    assert (plain[0], plain[1]) == bay, "unguarded: nearest wins, land or not"
    assert (guarded[0], guarded[1]) == ocean, "guarded: next-nearest cell with clear water"
    assert not nn._mask_blocked(cyc, slat, slng, guarded[0], guarded[1])
    assert guarded[4] > plain[4], "the guard trades distance for an unobstructed path"


def test_select_node_land_guard_is_off_by_default():
    # every existing caller (nwps_series_by_hour, trkng_systems_by_hour) omits avoid_land and
    # must pick exactly as before — the guard is placement-only.
    cyc = _barrier_cycle()
    slat, slng = _barrier_spot(cyc)
    assert nn.select_node(cyc, slat, slng, None) \
        == nn.select_node(cyc, slat, slng, None, avoid_land=False)
    # and with no land in the grid at all it changes nothing either
    clear = _mask_grid(3, 3, 28.49, 0.009, -80.61, 0.010,
                       [(r, c) for r in range(3) for c in range(3)])
    assert nn.select_node(clear, slat, slng, None) \
        == nn.select_node(clear, slat, slng, None, avoid_land=True)


def test_select_node_falls_back_when_every_candidate_is_blocked():
    # insurance, not a new rejection path: if nothing clears, the nearest cell is still returned,
    # so the guard can move a placement but never drop a spot to NO_WET_CELL.
    cyc = _mask_grid(4, 3, 28.470, 0.020, -80.620, 0.005, wet=[(0, 0), (0, 1), (0, 2)])
    slat, slng = float(cyc["lats"][2, 1]), float(cyc["lons"][2, 1])
    for j in range(3):
        assert nn._mask_blocked(cyc, slat, slng, 0, j), f"(0,{j}) is behind the land row"
    assert nn.select_node(cyc, slat, slng, None, avoid_land=True) \
        == nn.select_node(cyc, slat, slng, None), "falls back to today's nearest pick"


def test_mask_walk_cannot_be_slipped_diagonally():
    # sampling at ~1/3 of a cell must stop a path threading the corner between land cells.
    cyc = _mask_grid(3, 3, 28.480, 0.009, -80.620, 0.010, wet=[(0, 0), (2, 2)])
    slat, slng = float(cyc["lats"][0, 0]), float(cyc["lons"][0, 0])
    assert nn._mask_blocked(cyc, slat, slng, 2, 2), "the diagonal crosses land cell (1,1)"


def test_guard_needs_no_gshhg_and_no_tuned_constant():
    # the point of the rewrite: the guard depends on the cycle alone. No coastline index, and no
    # NODE_HEADLAND_* threshold left to drift out of scale or to be set in an overlapping band.
    assert not [k for k in dir(nn) if k.startswith("NODE_HEADLAND")], \
        "the unsettable node-scale constants are gone, not merely retuned"
    cyc = _barrier_cycle()
    assert nn._mask_blocked(cyc, *_barrier_spot(cyc), 2, 4)     # no land index in sight


def test_adjacent_cell_is_never_blocked_which_is_correct_at_model_resolution():
    # a node one cell from the spot cannot have land "in between" at the model's resolution, and
    # this is what replaces the old NEAR_TRIM/END_TRIM distances — a resolution-matched skip
    # rather than a tuned 100 m that was never measured.
    cyc = _barrier_cycle()
    slat, slng = _barrier_spot(cyc)
    assert not nn._mask_blocked(cyc, slat, slng, 1, 2), "one row north — adjacent, never blocked"
    assert not nn._mask_blocked(cyc, slat, slng, 1, 3), "diagonally adjacent — likewise"


def test_grid_area_floor_is_one_cell_and_per_grid():
    # per-grid land-area floor, derived from spacing the way grid_far_cap_km derives the far cap.
    def mk(dlat, dlng, lat0=40.55, lng0=-73.95, n=6):
        la, lo = np.meshgrid(np.array([lat0 + k * dlat for k in range(n)]),
                             np.array([lng0 + k * dlng for k in range(n)]), indexing="ij")
        return {"lats": la, "lons": lo}
    fine = mk(0.01631, 0.02112)                                # okx-like ~1.8 km -> ~3.24 km2
    coarse = mk(0.02256, 0.02750, lat0=37.0, lng0=-122.5)      # mtr-like ~2.48 km -> ~6.15 km2
    assert 3.0 <= nn.grid_area_floor_km2(fine) <= 3.5
    assert 5.5 <= nn.grid_area_floor_km2(coarse) <= 6.8
    assert nn.grid_area_floor_km2(coarse) > nn.grid_area_floor_km2(fine), "coarser grid, higher floor"
    assert abs(nn.grid_area_floor_km2(fine) - nn.grid_spacing_km(fine) ** 2) < 1e-9, "= one cell"


def test_buoy_scale_headland_defaults_are_untouched_by_the_parameterisation():
    # the four scale keywords must default to the FIND_BUOY_HEADLAND_* constants, so --find-buoy
    # and both real-GSHHG acceptance bars still run the numbers they were validated against.
    land = _fake_land([_synthetic_cape()])
    spot, buoy = (28.75, -80.70), (28.30, -80.45)
    explicit = nn._headland_verdict(
        spot, buoy, land, max_km=nn.FIND_BUOY_HEADLAND_MAX_KM,
        near_trim_km=nn.FIND_BUOY_HEADLAND_MID_START_KM,
        end_trim_km=nn.FIND_BUOY_HEADLAND_END_TRIM_KM,
        area_km2=nn.FIND_BUOY_HEADLAND_AREA_KM2,
        own_graze_km=nn.FIND_BUOY_HEADLAND_OWN_GRAZE_KM)
    assert nn._headland_verdict(spot, buoy, land) == explicit, "unqualified call == buoy-scale call"
    assert explicit["reject"] is True, "and it is still the validated wrong-side rejection"
    # The cape sits 20+ km along a 55 km path, so it cannot detect a drifting NEAR TRIM. This case
    # can: a spot 0.5 km inside a big landmass edge with an offshore buoy must stay CLEAR, which is
    # the only thing the 3.0 km near trim does (cf. test_headland_clears_open_coast_and_trims_own_shore).
    from shapely.geometry import box
    own = _fake_land([box(-82.0, 28.0, -80.71, 29.5)])
    ospot, obuoy = (28.75, -80.715), (28.75, -80.40)
    assert nn._headland_verdict(ospot, obuoy, own)["reject"] is False, \
        "buoy-scale near trim must still clear the spot's own shore"
    assert nn._headland_verdict(ospot, obuoy, own)["chord_km"] == 0.0
    # and prove the trim is what does it — a node-scale trim sees that same own shore
    assert nn._headland_land_chord_km(ospot, obuoy, own, near_trim_km=0.10, end_trim_km=0.10,
                                      area_km2=0.5, own_graze_km=0.0) > 0.0
    # Behaviour alone cannot pin every default: the two real-GSHHG acceptance bars are what
    # validate these four numbers, and they need the Mac. Offline, assert the defaults ARE those
    # validated constants, so a node-scale value can never drift into the buoy-scale path unseen.
    import inspect
    got = {k: v.default for k, v in inspect.signature(nn._headland_land_chord_km).parameters.items()
           if k.endswith(("_km", "_km2"))}
    assert got == {"near_trim_km": nn.FIND_BUOY_HEADLAND_MID_START_KM,
                   "end_trim_km": nn.FIND_BUOY_HEADLAND_END_TRIM_KM,
                   "area_km2": nn.FIND_BUOY_HEADLAND_AREA_KM2,
                   "own_graze_km": nn.FIND_BUOY_HEADLAND_OWN_GRAZE_KM}, got
    assert inspect.signature(nn._headland_verdict).parameters["max_km"].default \
        == nn.FIND_BUOY_HEADLAND_MAX_KM


def test_no_current_placement_can_move_live():
    # The guard is insurance for the ~25 spots about to place, not a fix for the placed ones.
    # It is unreachable for them BY CONSTRUCTION: every placed spot carries a baked node, and the
    # read path (nwps_series_by_hour / trkng_systems_by_hour) resolves that node with _nearest_cell,
    # consulting select_node only when the baked node is ABSENT. select_node is passed a land index
    # from exactly one place — validate_batch — which does not touch spots_enriched.json.
    import os
    enriched = json.loads(open(os.path.join(
        os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
        "pipeline", "spots_enriched.json")).read())
    placed = [s for s in enriched if s.get("swell_window_source") == "nwps"]
    assert len(placed) == 491, f"expected the 491 placed spots, got {len(placed)}"
    missing = [s.get("name") for s in placed
               if s.get("nwps_node_lat") is None or s.get("nwps_node_lng") is None]
    assert not missing, f"these placed spots would re-enter select_node on read: {missing}"


def _synthetic_convex_coast(west_lon=-80.85):
    """A ~12,000 km² mainland whose east coast bulges ~5 km seaward at mid-span — the SE-Florida
    shape that produced the false positive. *west_lon* sets the peninsula width so a far-side
    station can be placed inside the distance ceiling."""
    import math
    from shapely.geometry import Polygon
    lon = lambda y: -80.10 + 0.05 * math.sin(math.pi * (y - 25.8) / 1.4)
    ys = [25.8 + i * 0.01 for i in range(141)]
    return Polygon([(lon(y), y) for y in ys] + [(west_lon, 27.2), (west_lon, 25.8)]), lon


def test_headland_clears_along_coast_graze_of_buoys_own_landmass():
    """THE BUG (mfl 2026-07-27): a nearshore buoy sits AGAINST land, so a spot->buoy geodesic running
    parallel to a straight coast clips the landmass BOTH ends hang off. That must NOT be a headland."""
    mainland, lon = _synthetic_convex_coast()
    land = _fake_land([mainland])
    buoy = (26.001, lon(26.001) + 0.003)                       # ~300 m off the beach (the 41122 shape)
    seen = []
    for lat in (26.30, 26.70, 26.88):                          # hillsboro / palm-beach / juno analogues
        spot = (lat, lon(lat) + 0.001)
        v = nn._headland_verdict(spot, buoy, land)
        assert v["reject"] is False and v["chord_km"] == 0.0, (lat, v)
        assert v["own_excluded"] is True                       # excluded as a graze, and said so
        seen.append((v["dist_km"], v["own_chord_km"], v["own_penetration_km"]))
    # the BUG SIGNATURE: clipped chord grows with along-coast distance (9 -> 90 km) while inland
    # penetration does NOT (stays ~0-2 km). Penetration is what the fix keys on, which is why the
    # reject no longer scales with distance.
    assert seen[-1][1] > 8 * seen[0][1], seen                  # chord grew ~10x
    assert all(p <= nn.FIND_BUOY_HEADLAND_OWN_GRAZE_KM for _, _, p in seen), seen
    assert seen[-1][2] < 4.0, seen                             # deepest graze still only a few km inland


def test_headland_still_flags_traverse_of_buoys_own_landmass():
    """DO NOT OVERCORRECT: the Everglades / Florida-Bay f1 cases must keep rejecting. Both ways a
    buoy can be behind its own nearest landmass — on the far side of it, or inside it."""
    mainland, lon = _synthetic_convex_coast()
    land = _fake_land([mainland])
    spot = (26.30, lon(26.30) + 0.001)
    far = nn._headland_verdict(spot, (26.30, -80.90), land)     # station off the OPPOSITE coast
    assert far["reject"] is True and far["chord_km"] > 50.0
    assert far["own_excluded"] is False                         # a traverse, not a graze
    assert far["own_penetration_km"] > nn.FIND_BUOY_HEADLAND_OWN_GRAZE_KM
    inside = nn._headland_verdict(spot, (26.40, -80.60), land)   # station INSIDE the landmass (marsh)
    assert inside["reject"] is True and inside["chord_km"] > 20.0
    assert inside["own_excluded"] is False                       # containment is never excluded
    idx, contains = nn._headland_own_landmass((26.40, -80.60), land)
    assert (idx, contains) == (0, True)


def test_headland_own_exclusion_is_per_polygon_not_global():
    """The exclusion applies to the buoy's OWN polygon only — a different landmass between spot and
    buoy still rejects even while the buoy's own shore is being grazed on the same path."""
    from shapely.geometry import box
    mainland, lon = _synthetic_convex_coast()
    cape = box(-80.30, 26.10, -79.95, 26.45)                   # a separate cape/island east of the coast
    buoy = (26.001, lon(26.001) + 0.003)
    spot = (26.60, lon(26.60) + 0.001)
    v = nn._headland_verdict(spot, buoy, _fake_land([mainland, cape]))
    assert v["own_excluded"] is True                            # mainland graze still excluded ...
    assert v["reject"] is True and v["chord_km"] > 10.0         # ... but the cape still flags
    # and with the cape removed the same pairing is clean — the cape is doing the rejecting
    assert nn._headland_verdict(spot, buoy, _fake_land([mainland]))["reject"] is False


def test_headland_own_landmass_resolution_and_fallback():
    # a buoy offshore resolves to the nearest polygon (not contained); an unresolvable index falls
    # back to (None, False) so every crossing counts — i.e. the OLD behaviour, never a silent clear
    mainland, lon = _synthetic_convex_coast()
    land = _fake_land([mainland])
    assert nn._headland_own_landmass((26.001, lon(26.001) + 0.003), land) == (0, False)
    class _Broken:
        polygons = []
        class polygon_tree:
            @staticmethod
            def query(_):  raise RuntimeError("no tree")
            @staticmethod
            def nearest(_): raise RuntimeError("no tree")
    assert nn._headland_own_landmass((26.0, -80.0), _Broken()) == (None, False)


def test_headland_graze_ceiling_sits_in_the_measured_gap():
    """The graze ceiling is a MEASURED boundary, not a tuned one, and its margin is only ~2 km wide.
    At 8.0 it swallowed real headlands: on full-res GSHHG the whole North American mainland is ONE
    polygon, so every East Coast buoy routes its mainland crossings through the graze test, and the
    Canaveral crossings (33-78 km of land) reach only 4.1-5.4 km inland — under the old bar, so all
    six wrong-side pairings cleared and the detector was a no-op for the entire seaboard. These are
    the real-GSHHG margins that set the value; runs offline so the gap can't be silently invalidated."""
    MUST_CLEAR_DEEPEST_KM = 2.15      # deepest inland reach of an along-coast pairing that must CLEAR
    MUST_FLAG_SHALLOWEST_KM = 4.14    # shallowest inland reach of the six Canaveral crossings (must FLAG)
    assert MUST_CLEAR_DEEPEST_KM < nn.FIND_BUOY_HEADLAND_OWN_GRAZE_KM < MUST_FLAG_SHALLOWEST_KM, (
        f"FIND_BUOY_HEADLAND_OWN_GRAZE_KM={nn.FIND_BUOY_HEADLAND_OWN_GRAZE_KM} is outside the measured "
        f"{MUST_CLEAR_DEEPEST_KM}-{MUST_FLAG_SHALLOWEST_KM} km gap; re-run BOTH real-GSHHG acceptance "
        f"bars (test_headland_27_pair_regression 6 FLAG/21 clear, test_headland_mfl_regression 4 clear/"
        f"2 FLAG) before changing it")


def test_headland_mfl_regression():
    """ACCEPTANCE BAR for the fix — real GSHHG, real coords. The four along-coast 41122 pairings must
    NO LONGER be rejected on headland grounds; the two Everglades crossings must still be. Skips when
    the GSHHG shapefile is absent (this sandbox); run on the Mac."""
    from pipeline.enrichment.geodata import load_land_index
    land = load_land_index()
    if land is None:
        print("    SKIP test_headland_mfl_regression — GSHHG shapefile absent (run on the Mac).")
        return
    import json, re
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    E = json.loads((repo / "pipeline/spots_enriched.json").read_text())
    slug = lambda n: re.sub(r"[^a-z0-9]+", "-", (n or "").lower()).strip("-")
    coord = {(s.get("slug") or slug(s.get("name"))): (s.get("lat"), s.get("lng"))
             for s in E if s.get("lat") is not None}
    snap = json.loads((repo / "pipeline/data/ndbc_buoy_snapshot.json").read_text())
    bll = {b: (snap[b]["lat"], snap[b]["lng"]) for b in ("41122", "trrf1", "wiwf1")}
    # 41122 (Hollywood Beach, ~200 m off the beach) vs spots north on the SAME unbroken Atlantic
    # beach — no headland exists between any of them.
    MUST_CLEAR = {s: "41122" for s in ("hillsboro-beach", "palm-beach", "ocean-reef-park",
                                       "juno-beach-pier")}
    # real peninsula crossings — a DIFFERENT landmass relationship (station behind the Everglades)
    MUST_FLAG = {"south-beach-miami": "trrf1", "haulover-inlet": "wiwf1"}
    print("    mfl headland regression (real GSHHG):")
    flagged = set()
    for sl, b in {**MUST_CLEAR, **MUST_FLAG}.items():
        v = nn._headland_verdict(coord[sl], bll[b], land)
        pen = v["own_penetration_km"]
        print(f"      {'FLAG ' if v['reject'] else 'clear'} {sl:20} {b:6} chord={v['chord_km']:5.1f} km  "
              f"dist={v['dist_km']:5.0f} km  own_clip={v['own_chord_km']:5.1f} km  "
              f"inland={'n/a' if pen is None else f'{pen:5.1f} km'}  excluded={v['own_excluded']}")
        if v["reject"]:
            flagged.add(sl)
    assert flagged == set(MUST_FLAG), f"expected exactly the 2 Everglades crossings; got {sorted(flagged)}"


def test_headland_27_pair_regression():
    """ACCEPTANCE BAR — real GSHHG. Must FLAG exactly the 6 wrong-side pairings and CLEAR the 21
    correct ones. Skips when the GSHHG shapefile is absent (this sandbox); run on the Mac."""
    from pipeline.enrichment.geodata import load_land_index
    land = load_land_index()
    if land is None:
        print("    SKIP test_headland_27_pair_regression — GSHHG shapefile absent (run on the Mac).")
        return
    import json, re
    from pathlib import Path
    repo = Path(__file__).resolve().parents[2]
    E = json.loads((repo / "pipeline/spots_enriched.json").read_text())
    slug = lambda n: re.sub(r"[^a-z0-9]+", "-", (n or "").lower()).strip("-")
    coord = {slug(s.get("name")): (s.get("lat"), s.get("lng")) for s in E if s.get("lat") is not None}
    snap = json.loads((repo / "pipeline/data/ndbc_buoy_snapshot.json").read_text())
    bll = {b: (snap[b]["lat"], snap[b]["lng"]) for b in ("41113", "41112", "41117")}
    MUST_FLAG = {s: "41113" for s in ("playalinda-beach", "daytona-beach", "wilbur-by-the-sea",
                 "ponce-inlet", "new-smyrna-beach-inlet", "flagler-ave")}
    MUST_CLEAR = {**{s: "41113" for s in ("jetty-park-cocoa-beach", "cocoa-beach-pier",
                  "lori-wilson-park", "16th-street-south", "patrick-air-force-base-beach",
                  "satellite-beach", "pelican-beach", "canova-beach-park")},
                  **{s: "41112" for s in ("fort-clinch", "amelia-island", "mayport-poles", "atlantic-beach")},
                  **{s: "41117" for s in ("vilano-beach", "st-augustine-beach-pier", "a-street",
                     "matanzas-inlet", "marineland", "ponte-vedra-beach", "jacksonville-beach-pier",
                     "atlantic-blvd", "flagler-beach-pier")}}
    print("    27-pair headland regression (real GSHHG):")
    flagged = set()
    for sl, b in {**MUST_FLAG, **MUST_CLEAR}.items():
        v = nn._headland_verdict(coord[sl], bll[b], land)
        print(f"      {'FLAG ' if v['reject'] else 'clear'} {sl:30} {b}  "
              f"chord={v['chord_km']:5.1f} km  dist={v['dist_km']:5.0f} km")
        if v["reject"]:
            flagged.add(sl)
    assert flagged == set(MUST_FLAG), f"expected exactly the 6 wrong flagged; got {sorted(flagged)}"


def _run_all():
    import inspect
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    passed = 0
    for fn in fns:
        params = inspect.signature(fn).parameters
        if params:                       # pytest-fixture tests (tmp_path/monkeypatch) — pytest only
            print(f"  SKIP  {fn.__name__} (needs pytest fixtures)")
            continue
        fn()
        passed += 1
        print(f"  PASS  {fn.__name__}")
    print(f"{passed} trust-gate checks passed")


if __name__ == "__main__":
    _run_all()
