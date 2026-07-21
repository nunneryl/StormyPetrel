"""Synthetic tests for the cleanup_spots plausibility guard (mode-(a) coord-corruption fix).

A name-keyed coord fix authored for one "North Jetty" (San Diego) silently teleported the Florida
"North Jetty" ~3500 km, and every coordinate-derived field was then recomputed from the wrong point.
The guard rejects a fix that moves a spot > COORD_FIX_MAX_MOVE_KM or into a different state unless the
patch carries force:true.

Run: python -m pipeline.tests.test_cleanup_guard   (or pytest)
"""
from __future__ import annotations

from pipeline import cleanup_spots as cs


def _spot(name, lat, lng):
    return {"name": name, "lat": lat, "lng": lng, "is_valid_surf_spot": True}


def test_far_cross_state_fix_is_rejected():
    # Florida spot, patch to San Diego (~3500 km, FL->CA): REJECTED, coords untouched, not flagged fixed.
    spots = [_spot("North Jetty", 27.8623, -80.4465)]
    fixes = {"North Jetty": {"lat": 32.7545, "lng": -117.2550, "note": "", "force": False}}
    cleaned, stats = cs.apply_cleanup(spots, {}, fixes, {})
    s = cleaned[0]
    assert (s["lat"], s["lng"]) == (27.8623, -80.4465), "far cross-state fix must NOT be applied"
    assert not s.get("coord_fix_applied")
    assert stats["coord_fixed"] == 0 and len(stats["rejected_fixes"]) == 1
    rej = stats["rejected_fixes"][0]
    assert rej["old_state"] == "Florida" and rej["new_state"] == "California" and rej["move_km"] > 3000


def test_near_same_state_fix_is_applied():
    # ~0.5 km refinement within Florida: applied normally.
    spots = [_spot("Sebastian Inlet", 27.8630, -80.4465)]
    fixes = {"Sebastian Inlet": {"lat": 27.8676, "lng": -80.4474, "note": "", "force": False}}
    cleaned, stats = cs.apply_cleanup(spots, {}, fixes, {})
    assert (cleaned[0]["lat"], cleaned[0]["lng"]) == (27.8676, -80.4474)
    assert cleaned[0]["coord_fix_applied"] is True and stats["coord_fixed"] == 1 and not stats["rejected_fixes"]


def test_force_flag_overrides_the_guard():
    spots = [_spot("North Jetty", 27.8623, -80.4465)]
    fixes = {"North Jetty": {"lat": 32.7545, "lng": -117.2550, "note": "", "force": True}}
    cleaned, stats = cs.apply_cleanup(spots, {}, fixes, {})
    assert (cleaned[0]["lat"], cleaned[0]["lng"]) == (32.7545, -117.2550)
    assert stats["coord_fixed"] == 1 and not stats["rejected_fixes"]


def test_slug_keyed_patch_matches_by_slug():
    # A patch keyed by slug matches the spot db_import upserts on; small in-state move -> applied.
    spots = [_spot("Ponce Inlet", 29.1189, -80.9481)]
    fixes = {"ponce-inlet": {"lat": 29.1195, "lng": -80.9488, "note": "", "force": False}}
    cleaned, stats = cs.apply_cleanup(spots, {}, fixes, {})
    assert cleaned[0]["coord_fix_applied"] is True and stats["coord_fixed"] == 1


def test_idempotent_reapply_passes():
    # current == patch (already applied): 0 km move, not rejected.
    spots = [_spot("X", 34.0, -118.0)]
    fixes = {"X": {"lat": 34.0, "lng": -118.0, "note": "", "force": False}}
    cleaned, stats = cs.apply_cleanup(spots, {}, fixes, {})
    assert not stats["rejected_fixes"] and stats["coord_fixed"] == 1


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} cleanup-guard checks passed")


if __name__ == "__main__":
    _run_all()
