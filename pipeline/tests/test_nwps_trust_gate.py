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
    windsea = _sys(1.8, 270.0, system=1, tp=5.0)    # c=7.8 m/s < 1.2·12·cos0=14.4 → wind-sea
    swell = _sys(0.7, 120.0, system=2, tp=12.0)     # c=18.7 m/s > 14.4 → swell
    m = nn._match_swell_system([windsea, swell], 12.0, 270.0)
    assert m is not None and m["system"] == 2 and m["dir"] == 120.0, "the SWELL, not the bigger wind-sea"
    assert nn._system_is_swell(windsea, 12.0, 270.0) is False
    assert nn._system_is_swell(swell, 12.0, 270.0) is True
    # a swell OPPOSING the wind stays swell regardless of period
    assert nn._system_is_swell(_sys(1.0, 90.0, tp=6.0), 12.0, 270.0) is True
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
