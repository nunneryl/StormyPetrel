"""Guardrail tests for two silent-corruption fixes (both prevent corrupting manual work).

  FIX 1 — --validate roster default: `--validate --wfo X` with no --batch must validate the
          nwps_wfo==X spots from spots_enriched.json, NOT the okx_pilot.json set, and print the
          roster source. --batch still wins; okx_pilot is used only when --wfo is absent.
  FIX 2 — enrich preserve-guard: a full enrich must never SILENTLY demote a spot already on the
          nwps/cdip_mop swell-window tier back to raycast; --allow-tier-demotion opts out. The
          guard must NOT freeze nearest_buoy_id (the recomputable display buoy).

Run: python -m pipeline.tests.test_guardrails   (or pytest)
"""
from __future__ import annotations

from pipeline.forecast.nwps_nearshore import _validate_roster
from pipeline.enrich import _apply_tier_guard, _strip_preserve_markers


# --------------------------------------------------------------------------- #
# FIX 1 — --validate roster default                                            #
# --------------------------------------------------------------------------- #
def test_validate_wfo_selects_that_wfo_not_pilot():
    """(1) `--validate --wfo jax` with no --batch selects the 18 jax spots, NOT the 38-spot
    okx_pilot.json set, and labels the roster by wfo."""
    spots, label, grid = _validate_roster(None, "jax")
    assert grid == "jax"
    assert spots, "expected jax spots from spots_enriched.json"
    assert all(s.get("nwps_wfo") == "jax" for s in spots)
    assert len(spots) == 18                       # the committed jax roster
    assert label == f"nwps_wfo == 'jax' ({len(spots)} spots)"
    assert len(spots) != 38                       # not the okx pilot set


def test_validate_batch_overrides_wfo():
    """(2) --batch takes precedence over the --wfo roster default; --wfo still names the grid."""
    spots, label, grid = _validate_roster("waddell-creek,davenport-landing", "jax")
    assert len(spots) == 2
    assert label == "--batch (2 spots)"
    assert grid == "jax"


def test_validate_no_wfo_falls_back_to_pilot():
    """Only with --wfo absent entirely does the okx_pilot.json default apply."""
    spots, label, grid = _validate_roster(None, None)
    assert grid == "okx"
    assert label.startswith("scripts/okx_pilot.json")


# --------------------------------------------------------------------------- #
# FIX 2 — enrich preserve-guard                                                #
# --------------------------------------------------------------------------- #
def _nwps_spot():
    return {"swell_window_source": "nwps",
            "swell_window_arcs": [{"min": 90, "max": 230}],
            "optimal_swell_dir": 160.0,
            "enrichment_confidence": {"swell_window": 0.9},
            "nwps_buoy_id": "41113"}


def test_guard_blocks_tier_demotion_and_counts():
    """(3) A recompute that would demote an nwps spot to raycast is BLOCKED: the tier + its
    swell-window fields are restored, the guard reports the rescue, and the preserved count
    (via the marker _enrich_one stamps on a rescue) increments."""
    spot = _nwps_spot()
    enriched = {"swell_window_source": "raycast",              # recompute demoted it
                "swell_window_arcs": [{"min": 0, "max": 360}],
                "optimal_swell_dir": 200.0,
                "nwps_buoy_id": "41113"}
    confidence = {"swell_window": 0.4}
    assert _apply_tier_guard(spot, enriched, confidence, allow_tier_demotion=False) is True
    assert enriched["swell_window_source"] == "nwps"                    # tier preserved
    assert enriched["swell_window_arcs"] == [{"min": 90, "max": 230}]   # tier fields restored
    assert enriched["optimal_swell_dir"] == 160.0
    assert confidence["swell_window"] == 0.9

    # cdip_mop is guarded identically (demotion to orientation_derived also counts)
    spot_mop = _nwps_spot(); spot_mop["swell_window_source"] = "cdip_mop"
    enr_mop = {"swell_window_source": "orientation_derived"}
    assert _apply_tier_guard(spot_mop, enr_mop, {"swell_window": 0.0}, False) is True
    assert enr_mop["swell_window_source"] == "cdip_mop"

    # the preserved COUNT: _enrich_one stamps `_tier_preserved` on a rescue; main counts+strips.
    records = [{"_tier_preserved": True}, {"_tier_preserved": True}, {"name": "untouched"}]
    assert _strip_preserve_markers(records) == 2
    assert all("_tier_preserved" not in r for r in records)   # marker never leaks to the file


def test_allow_tier_demotion_lets_recompute_win():
    """(4) With --allow-tier-demotion the same recompute is NOT blocked — the spot demotes."""
    spot = _nwps_spot()
    enriched = {"swell_window_source": "raycast",
                "swell_window_arcs": [{"min": 0, "max": 360}],
                "optimal_swell_dir": 200.0}
    confidence = {"swell_window": 0.4}
    assert _apply_tier_guard(spot, enriched, confidence, allow_tier_demotion=True) is False
    assert enriched["swell_window_source"] == "raycast"       # demotion allowed to win
    assert enriched["optimal_swell_dir"] == 200.0
    assert confidence["swell_window"] == 0.4


def test_guard_does_not_freeze_nearest_buoy_id():
    """(5) A recompute that changes ONLY nearest_buoy_id (tier unchanged) is not blocked, and the
    guard never touches nearest_buoy_id — so a dead display buoy can still fall out on a
    targeted re-enrich (the planned fix that this guard must not block)."""
    # tier not demoted (nwps -> nwps): guard is a no-op, the buoy change stands
    spot = {"swell_window_source": "nwps", "nearest_buoy_id": "46240",
            "swell_window_arcs": [{"min": 90, "max": 230}], "optimal_swell_dir": 160.0}
    enriched = {"swell_window_source": "nwps", "nearest_buoy_id": "46237",   # dead buoy fell out
                "swell_window_arcs": [{"min": 90, "max": 230}], "optimal_swell_dir": 160.0}
    assert _apply_tier_guard(spot, enriched, {"swell_window": 0.0}, allow_tier_demotion=False) is False
    assert enriched["nearest_buoy_id"] == "46237"            # buoy change preserved, not frozen

    # even when the guard DOES fire (tier demoted), it must leave nearest_buoy_id alone
    spot2 = {"swell_window_source": "nwps", "nearest_buoy_id": "46240"}
    enriched2 = {"swell_window_source": "raycast", "nearest_buoy_id": "46237"}
    assert _apply_tier_guard(spot2, enriched2, {}, allow_tier_demotion=False) is True
    assert enriched2["swell_window_source"] == "nwps"        # tier restored
    assert enriched2["nearest_buoy_id"] == "46237"           # display buoy left recomputed


if __name__ == "__main__":
    import sys
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in tests:
        try:
            fn()
            print(f"  PASS  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL  {fn.__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
