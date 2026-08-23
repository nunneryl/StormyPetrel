"""The NWPS nearshore override keeps WW3 swell identity and only overrides HEIGHT.

THE DEFECT. rate_spot resolves swell direction/period from the WW3 partitions via
combine_ww3_partitions, and that works — PR #201's instrumentation measured 646/646 spots
resolving from ww3_partition pre-override, ww3_used at 97.9% of 93670 spot-hours, every WFO
between 96.6% and 100%. apply_nwps_overrides then ran over every swell_window_source=="nwps"
spot (598 of 646) and, at the e.update(...), replaced swell_dp with NWPS dirpw and swell_tp
with perpw, stamping swell_source="nwps". nwps_stars computed directional_gain from that
dirpw. So the partition decomposition was computed and then discarded for 92% of the roster.

WHY IT MATTERS. dirpw is a WHOLE-SPECTRUM mean direction and perpw its whole-spectrum
companion: on a mixed sea they describe no real wave train and track whichever component
carries most energy, usually local wind sea. Measured 2026-08-19 — Huntington Beach Pier
rated from 255°/7.8 s while the surfable swell was a 15 s south at 191°; Blacks Beach from
285°/6.0 s against a 14 s SW at 220°; Banzai Pipeline, Sunset and Waimea all from 95°/6.8 s,
the windward-side east trade sea, at spots facing 300–315°.

THE FRAME ARGUMENT, which is the load-bearing one and is pinned by
test_direction_is_compared_in_the_frame_the_window_was_defined_in: swell_window_arcs and
optimal_swell_dir are DEEP-WATER concepts, ray-cast from the coastline against distant swell
propagation, so they ALREADY encode sheltering. Comparing a nearshore-refracted direction
against them double-counts refraction. WW3 partition direction is deep-water — the frame the
window is defined in. This holds only while that is true; see the coupling comment at the
update site.

WHAT IS DELIBERATELY NOT CHANGED, and these tests pin it:
  * HEIGHT still comes from NWPS (swh/shts) — it is the nearshore-refracted, higher-resolution
    field, which is the whole reason the override exists;
  * hours with NO WW3 identity still get the override, still falling back to dirpw — dropping
    the override there would throw away the better height too;
  * wind_mult and tide_mult are carried through untouched;
  * directional_gain, combine_ww3_partitions, composite_stars, face_ft and every threshold
    are untouched.

Run: python -m pipeline.tests.test_nwps_override_ww3_direction   (or pytest)
"""
from __future__ import annotations

import math

from pipeline.forecast import nwps_nearshore as nn

BASE = 1767225600                      # 2026-01-01T00:00:00Z, epoch seconds
HR = BASE // 3600


def _spot(name="T", wfo="lox", orientation=210.0, source="nwps"):
    return {"name": name, "swell_window_source": source,
            "orientation_deg": orientation, "nwps_wfo": wfo}


def _entry(*, ww3_dp=None, ww3_tp=None, source=None, t=BASE,
           wind_mult=0.85, tide_mult=0.72, stars=1.0):
    """One pre-override rating entry as rate_spot leaves it. source="ww3" is what interpret
    sets exactly when combine_ww3_partitions returned a result."""
    e = {"valid_time": _iso(t), "stars": stars,
         "wind_mult": wind_mult, "tide_mult": tide_mult}
    if source is not None:
        e["swell_source"] = source
    if ww3_dp is not None:
        e["swell_dp"] = ww3_dp
    if ww3_tp is not None:
        e["swell_tp"] = ww3_tp
    return e


def _iso(epoch):
    import datetime
    return datetime.datetime.utcfromtimestamp(epoch).strftime("%Y-%m-%dT%H:00:00Z")


# The NWPS node series: (swh, perpw, dirpw, shts). dirpw here is the wind-sea-dragged
# whole-spectrum mean — 255°/7.8 s — against a real 15 s south swell at 191°, which is the
# Huntington case verbatim.
_SERIES = {HR: (1.6, 7.8, 255.0, 1.1)}
WW3_DP, WW3_TP = 191.0, 15.0


def _fetch(_s):
    return _SERIES


# --------------------------------------------------------------------------- #
# 1 — a WW3-derived direction survives the override; height comes from NWPS     #
# --------------------------------------------------------------------------- #
def test_ww3_direction_and_period_survive_while_height_comes_from_nwps():
    """THE FIX. swell_dp/swell_tp keep their WW3 values, and face_ft/stars are recomputed
    from the NWPS height — the two feeds are combined, not one discarded."""
    spot = _spot()
    e = _entry(ww3_dp=WW3_DP, ww3_tp=WW3_TP, source="ww3")
    ratings = {"T": [e]}
    stats = nn.apply_nwps_overrides(ratings, [spot], _fetch=_fetch)

    assert stats["fed"] == 1, stats
    assert e["swell_dp"] == WW3_DP, f"WW3 direction was overwritten: {e['swell_dp']}"
    assert e["swell_tp"] == WW3_TP, f"WW3 period was overwritten: {e['swell_tp']}"
    assert e["swell_dp"] != 255.0 and e["swell_tp"] != 7.8, "dirpw/perpw leaked back in"

    # HEIGHT is NWPS's: face_ft is face_ft(swh=1.6, WW3 tp) and swell_hs is shts.
    swh, _perpw, _dirpw, shts = _SERIES[HR]
    assert e["swell_hs"] == round(shts, 3), e["swell_hs"]
    assert e["face_ft"] == round(nn.face_ft(swh, WW3_TP, nn.RATING_SOURCE), 2), e["face_ft"]
    assert e["stars"] != 1.0, "the override must actually have rated the hour"
    # dir_gain is computed from the WW3 direction, not dirpw
    assert e["dir_gain"] == round(
        nn.directional_gain(WW3_DP, [], spot["orientation_deg"], spot["orientation_deg"]), 3)
    assert not e.get("nwps_dirpw_fallback"), "a WW3-driven hour is not a fallback"


def test_direction_is_compared_in_the_frame_the_window_was_defined_in():
    """The frame argument, made concrete. The spot faces 210°. The real swell is a 191°
    south — 19° off-axis, a good day. dirpw reads 255° — 45° off-axis, a mediocre one. The
    override must rate the day the swell actually delivers."""
    spot = _spot(orientation=210.0)
    ww3 = {"T": [_entry(ww3_dp=WW3_DP, ww3_tp=WW3_TP, source="ww3")]}
    dpw = {"T": [_entry(source="nwps_total", ww3_dp=None, ww3_tp=None)]}
    nn.apply_nwps_overrides(ww3, [spot], _fetch=_fetch)
    nn.apply_nwps_overrides(dpw, [spot], _fetch=_fetch)

    a, b = ww3["T"][0], dpw["T"][0]
    assert a["dir_gain"] > b["dir_gain"], (
        f"a 19°-off-axis swell must out-gain a 45°-off-axis whole-spectrum mean: "
        f"{a['dir_gain']} vs {b['dir_gain']}")
    assert a["period_quality"] > b["period_quality"], (
        f"15 s groundswell must out-score a 7.8 s mean period: "
        f"{a['period_quality']} vs {b['period_quality']}")
    assert a["stars"] > b["stars"], f"{a['stars']} vs {b['stars']}"


# --------------------------------------------------------------------------- #
# 2 — no WW3 identity → dirpw fallback, tagged and counted                      #
# --------------------------------------------------------------------------- #
def test_no_ww3_direction_falls_back_to_dirpw_and_is_tagged_and_counted():
    """The ~2% of spot-hours where combine_ww3_partitions returned None, plus any spot with
    no WW3 series. The override is KEPT (NWPS height is still the better height) but the
    direction falls back — visibly."""
    spot = _spot(wfo="box")
    e = _entry(source="nwps_total")          # rate_spot fell through to DIRPW
    ratings = {"T": [e]}
    stats = nn.apply_nwps_overrides(ratings, [spot], _fetch=_fetch)

    _swh, perpw, dirpw, _shts = _SERIES[HR]
    assert stats["fed"] == 1, "the override must NOT be dropped just because WW3 is absent"
    assert e["swell_dp"] == round(dirpw, 3), e["swell_dp"]
    assert e["swell_tp"] == round(perpw, 3), e["swell_tp"]
    assert e["nwps_dirpw_fallback"] is True, "the fallback must be tagged on the entry"

    assert stats["hours_dirpw_dir"] == 1 and stats["hours_ww3_dir"] == 0, stats
    w = stats["by_wfo"]["box"]
    assert w == {"spots": 1, "hours": 1, "ww3_dir": 0, "dirpw_dir": 1}, w


def test_a_half_resolved_entry_falls_back_rather_than_mixing_frames():
    """swell_source=="ww3" but a missing period is not a usable WW3 identity — take the
    whole fallback rather than pairing a WW3 direction with a whole-spectrum period."""
    for kw in ({"ww3_dp": WW3_DP}, {"ww3_tp": WW3_TP}, {}):
        e = _entry(source="ww3", **kw)
        nn.apply_nwps_overrides({"T": [e]}, [_spot()], _fetch=_fetch)
        assert e.get("nwps_dirpw_fallback") is True, f"{kw} should not count as WW3 identity"
        assert e["swell_dp"] == round(255.0, 3) and e["swell_tp"] == round(7.8, 3)


def test_only_a_ww3_swell_source_counts_as_a_deep_water_direction():
    """swell_dp is populated on the buoy and nwps_total paths too, and those are NOT
    deep-water partition directions. The discriminator is the source flag, not presence."""
    assert nn._ww3_swell_identity(
        {"swell_source": "ww3", "swell_dp": 191.0, "swell_tp": 15.0}) == (191.0, 15.0)
    for src in ("buoy", "nwps_total", "nwps_swell", "none", "", None):
        got = nn._ww3_swell_identity({"swell_source": src, "swell_dp": 191.0, "swell_tp": 15.0})
        assert got == (None, None), f"source {src!r} must not pass as a WW3 identity: {got}"
    assert nn._ww3_swell_identity({}) == (None, None)
    assert nn._ww3_swell_identity(
        {"swell_source": "ww3", "swell_dp": "bad", "swell_tp": 15.0}) == (None, None)


# --------------------------------------------------------------------------- #
# 3 — swell_source distinguishes the hybrid from the fallback                   #
# --------------------------------------------------------------------------- #
def test_swell_source_distinguishes_the_hybrid_case_from_the_fallback_case():
    """Height from NWPS + direction/period from WW3 is a HYBRID, and calling it "nwps" is
    what made this invisible for months. The two cases must not share a value."""
    hybrid = _entry(ww3_dp=WW3_DP, ww3_tp=WW3_TP, source="ww3")
    fallback = _entry(source="nwps_total")
    nn.apply_nwps_overrides({"T": [hybrid]}, [_spot()], _fetch=_fetch)
    nn.apply_nwps_overrides({"T": [fallback]}, [_spot()], _fetch=_fetch)

    assert hybrid["swell_source"] == nn.SWELL_SOURCE_NWPS_WW3 == "nwps_height_ww3_dir"
    assert fallback["swell_source"] == nn.SWELL_SOURCE_NWPS_DIRPW == "nwps"
    assert hybrid["swell_source"] != fallback["swell_source"], "the whole point"
    # and the hybrid value must not collide with any value interpret already writes
    assert nn.SWELL_SOURCE_NWPS_WW3 not in {
        "ww3", "nwps_swell", "buoy", "nwps_total", "none", "nwps", "cdip_mop"}, \
        "the hybrid value must be NEW — reusing one keeps the ambiguity it exists to remove"


def test_a_reoverridden_entry_drops_a_stale_fallback_tag():
    """The tag tracks THIS run. An hour that fell back once and resolves from WW3 next run
    must not keep claiming it was dirpw-driven."""
    e = _entry(source="nwps_total")
    nn.apply_nwps_overrides({"T": [e]}, [_spot()], _fetch=_fetch)
    assert e["nwps_dirpw_fallback"] is True
    e.update(swell_source="ww3", swell_dp=WW3_DP, swell_tp=WW3_TP)
    nn.apply_nwps_overrides({"T": [e]}, [_spot()], _fetch=_fetch)
    assert "nwps_dirpw_fallback" not in e, "a stale fallback tag must be cleared"
    assert e["swell_source"] == nn.SWELL_SOURCE_NWPS_WW3


# --------------------------------------------------------------------------- #
# 4 — the wind and tide multipliers are untouched by the override               #
# --------------------------------------------------------------------------- #
def test_wind_and_tide_multipliers_are_unchanged_by_the_override():
    """The override re-rates size and direction; the per-hour tide comes from the normal
    rater and passes through byte-identical on BOTH paths, and so does wind_mult FOR THIS
    FIXTURE.

    NARROWED, and deliberately so. wind_mult is no longer unconditionally passed through:
    when the entry carries wind_dir/wind_speed AND the spot carries offshore_wind_deg, the
    override re-judges wind_multiplier's offshore-bonus cap against the chop IT computed,
    because that cap used to be frozen in from rate_spot's chop and could contradict the
    chop_mult the override then wrote. This fixture's _spot() has no offshore_wind_deg and
    _entry() no wind fields, so the recompute cannot fire here and pass-through still
    holds. The recompute and the leave-alone cases are pinned in
    pipeline/tests/test_chop_consistency.py."""
    for src, dp, tp in (("ww3", WW3_DP, WW3_TP), ("nwps_total", None, None)):
        e = _entry(ww3_dp=dp, ww3_tp=tp, source=src, wind_mult=0.53, tide_mult=0.91)
        nn.apply_nwps_overrides({"T": [e]}, [_spot()], _fetch=_fetch)
        assert e["wind_mult"] == 0.53, f"{src}: wind_mult moved to {e['wind_mult']}"
        assert e["tide_mult"] == 0.91, f"{src}: tide_mult moved to {e['tide_mult']}"

        # ...and they must actually REACH the rating, not merely survive on the entry.
        # Strict inequality with well-separated multipliers: pinning >= would still pass
        # if the override hardcoded 1.0/1.0 and every hour collapsed to the same stars.
        foul = _entry(ww3_dp=dp, ww3_tp=tp, source=src, wind_mult=0.4, tide_mult=0.6)
        clean = _entry(ww3_dp=dp, ww3_tp=tp, source=src, wind_mult=1.2, tide_mult=1.0)
        nn.apply_nwps_overrides({"T": [foul]}, [_spot()], _fetch=_fetch)
        nn.apply_nwps_overrides({"T": [clean]}, [_spot()], _fetch=_fetch)
        assert clean["stars"] > foul["stars"], (
            f"{src}: wind/tide are NOT reaching the stars — a blown-out 0.4/0.6 hour rated "
            f"{foul['stars']}, a clean 1.2/1.0 hour rated {clean['stars']}")


# --------------------------------------------------------------------------- #
# Everything else the override does must survive                                #
# --------------------------------------------------------------------------- #
def test_dry_run_mutates_nothing_but_still_counts():
    e = _entry(ww3_dp=WW3_DP, ww3_tp=WW3_TP, source="ww3")
    before = dict(e)
    stats = nn.apply_nwps_overrides({"T": [e]}, [_spot()], dry_run=True, _fetch=_fetch)
    assert e == before, "dry_run must not touch the entry"
    assert stats["fed"] == 1 and stats["hours_ww3_dir"] == 1, stats


def test_only_slug_restriction_and_non_nwps_spots_are_still_respected():
    e = _entry(ww3_dp=WW3_DP, ww3_tp=WW3_TP, source="ww3")
    skipped = nn.apply_nwps_overrides({"T": [e]}, [_spot()], only={"other-slug"}, _fetch=_fetch)
    assert skipped["fed"] == 0 and e["swell_source"] == "ww3", "only= must skip the spot"
    e2 = _entry(ww3_dp=WW3_DP, ww3_tp=WW3_TP, source="ww3")
    plain = nn.apply_nwps_overrides({"T": [e2]}, [_spot(source="orientation_derived")],
                                    _fetch=_fetch)
    assert plain["fed"] == 0 and e2["swell_source"] == "ww3"


def test_wfo_outage_isolation_and_stats_shape_are_preserved():
    """_WfoUnavailable still isolates to its WFO, and every pre-existing stats key keeps its
    name and meaning — the three new keys are additive."""
    def boom(_s):
        raise nn._WfoUnavailable("lox", "IncompleteRead: truncated")
    stats = nn.apply_nwps_overrides(
        {"T": [_entry(ww3_dp=WW3_DP, ww3_tp=WW3_TP, source="ww3")]}, [_spot()], _fetch=boom)
    assert stats["errored"] == 1 and stats["wfo_unavailable"] == {"lox": "IncompleteRead: truncated"}
    for k in ("fed", "fell_back", "errored", "wfo_unavailable", "details"):
        assert k in stats, f"pre-existing stats key {k} disappeared"
    for k in ("by_wfo", "hours_ww3_dir", "hours_dirpw_dir"):
        assert k in stats, f"new stats key {k} missing"
    assert isinstance(stats["details"], list) and isinstance(stats["wfo_unavailable"], dict)


def test_full_horizon_is_still_fed_on_both_direction_paths():
    """NWPS covers f000..f144, so every overlapping hour is fed — not just near-now."""
    far = BASE + 100 * 3600
    series = {HR: (1.6, 7.8, 255.0, 1.1), far // 3600: (2.2, 8.1, 250.0, 1.5)}
    e0 = _entry(ww3_dp=WW3_DP, ww3_tp=WW3_TP, source="ww3", t=BASE)
    e1 = _entry(ww3_dp=200.0, ww3_tp=13.0, source="ww3", t=far)
    stats = nn.apply_nwps_overrides({"T": [e0, e1]}, [_spot()], _fetch=lambda _s: series)
    assert stats["by_wfo"]["lox"]["hours"] == 2, stats["by_wfo"]
    assert e1["swell_dp"] == 200.0 and e1["swell_tp"] == 13.0, "+100h hour kept WW3 identity"
    assert stats["hours_ww3_dir"] == 2


def test_untouched_functions_and_thresholds():
    """This change routes a different value into directional_gain; it does not change
    directional_gain, the curves, or any threshold."""
    assert nn.RATING_SOURCE == "ww3"
    # directional_gain's no-arcs branch: cos²(diff/2) floored at 0.25
    assert math.isclose(nn.directional_gain(210.0, [], 210.0, 210.0), 1.0, rel_tol=1e-9)
    assert math.isclose(nn.directional_gain(30.0, [], 210.0, 210.0), 0.25, rel_tol=1e-9)
    # nwps_stars still refuses an unusable hour
    assert nn.nwps_stars(None, 12, 160, 1.9, 160)[0] is None
    assert nn.nwps_stars(2.0, 12, None, 1.9, 160)[0] is None
    assert nn.nwps_stars(2.0, 12, 160, 1.9, None)[0] is None


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} NWPS-override WW3-direction checks passed")


if __name__ == "__main__":
    _run_all()
