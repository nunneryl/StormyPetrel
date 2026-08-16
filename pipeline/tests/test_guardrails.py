"""Guardrail tests for three silent-corruption fixes (all prevent losing work with no error).

  FIX 1 — --validate roster default: `--validate --wfo X` with no --batch must validate the
          nwps_wfo==X spots from spots_enriched.json, NOT the okx_pilot.json set, and print the
          roster source. --batch still wins; okx_pilot is used only when --wfo is absent.
  FIX 2 — enrich preserve-guard: a full enrich must never SILENTLY demote a spot already on the
          nwps/cdip_mop swell-window tier back to raycast; --allow-tier-demotion opts out. The
          guard must NOT freeze nearest_buoy_id (the recomputable display buoy).
  FIX 3 — --validate batch output path: a --batch run must NOT write to the full-region
          scripts/nwps_{wfo}_validate_out.json. That file is the only record of a region's
          FAR / NO_WET_CELL rows, and a one- or two-spot batch dump silently replaced it three
          times (box + phi in PR #175, sgx in PR #183) with nothing failing or warning.

Run: python -m pipeline.tests.test_guardrails   (or pytest)
"""
from __future__ import annotations

from pipeline.forecast.nwps_nearshore import (
    _validate_roster, validate_out_path, write_validate_out)
import pipeline.forecast.nwps_nearshore as nn
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


# --------------------------------------------------------------------------- #
# FIX 3 — --validate batch output path                                         #
# --------------------------------------------------------------------------- #
import contextlib, json as _json, tempfile                                    # noqa: E402
from pathlib import Path as _Path                                             # noqa: E402


@contextlib.contextmanager
def _scripts_dir(tmp):
    """Point the module's SCRIPTS_DIR at a scratch dir so a test write touches no real file."""
    saved = nn.SCRIPTS_DIR
    nn.SCRIPTS_DIR = _Path(tmp)
    try:
        yield _Path(tmp)
    finally:
        nn.SCRIPTS_DIR = saved


def test_batch_run_does_not_write_the_full_region_path():
    """(6) THE FIX. A --batch run must land on its own path and leave the full-region file
    untouched — asserted on the real write, not just on the path helper."""
    with tempfile.TemporaryDirectory() as tmp, _scripts_dir(tmp) as d:
        region = validate_out_path("sgx")
        path, kind = write_validate_out("sgx", "rockpile-laguna", [{"slug": "rockpile-laguna"}],
                                        [{"slug": "rockpile-laguna", "outcome": "OK"}])
        assert kind == "BATCH", f"a --batch run must label itself BATCH, got {kind!r}"
        assert path != region, "batch dump must not target the full-region path"
        assert path.exists(), "the batch dump was not written"
        assert not region.exists(), "a --batch run WROTE the full-region file"
        assert set(p.name for p in d.iterdir()) == {path.name}, "only the batch file appeared"
        assert "batch" in path.name, f"the name must show it at a glance: {path.name}"
        assert _json.loads(path.read_text())["batch"] is True, \
            "the dump must mark itself a batch so a reader of the FILE can tell too"


def test_batch_run_cannot_clobber_an_existing_region_dump():
    """(7) The incident itself, replayed: write a full-region dump, then a batch dump on the SAME
    wfo, and the region dump must come back byte-identical. This is what failed three times."""
    with tempfile.TemporaryDirectory() as tmp, _scripts_dir(tmp):
        region_placed = [{"slug": f"spot-{i}"} for i in range(9)]
        region_outcomes = ([{"slug": f"spot-{i}", "outcome": "OK"} for i in range(9)]
                           + [{"slug": "far-one", "outcome": "FAR"},
                              {"slug": "dry-one", "outcome": "NO_WET_CELL"}])
        region, rkind = write_validate_out("sgx", None, region_placed, region_outcomes)
        assert rkind == "full-region", f"a no-batch run is full-region, got {rkind!r}"
        before = region.read_text()
        batch, _ = write_validate_out("sgx", "rockpile-laguna", [{"slug": "rockpile-laguna"}],
                                      [{"slug": "rockpile-laguna", "outcome": "OK"}])
        assert region.read_text() == before, "the batch run overwrote the full-region dump"
        doc = _json.loads(region.read_text())
        assert len(doc["outcomes"]) == 11 and doc["batch"] is False, \
            f'region dump altered: {len(doc["outcomes"])} outcomes, batch={doc["batch"]}'
        assert {o["outcome"] for o in doc["outcomes"]} == {"OK", "FAR", "NO_WET_CELL"}, \
            "the non-OK rows survive — they exist in no other file"
        assert len(_json.loads(batch.read_text())["outcomes"]) == 1, "batch dump holds its own row"


def test_full_region_path_is_unchanged():
    """(8) The full-region name must NOT move: promote_nwps_validate invocations, every zone
    record's to_place text and the committed eka/lox/mtr dumps all reference it by name."""
    for wfo in ("sgx", "box", "phi", "lox", "hfo"):
        want = f"nwps_{wfo}_validate_out.json"
        for arg in (None, ""):          # no --batch, and an empty --batch, are both full-region
            got = validate_out_path(wfo, arg).name
            assert got == want, f"full-region path moved for batch={arg!r}: {got} != {want}"
        assert validate_out_path(wfo).name == want, f"full-region path moved: {validate_out_path(wfo).name}"
        assert validate_out_path(wfo, "a,b") != validate_out_path(wfo), \
            f"{wfo}: batch and full-region resolve to the SAME path"
    assert validate_out_path("sgx").parent == validate_out_path("sgx", "a").parent, \
        "both dumps belong in SCRIPTS_DIR"


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
