"""Checks for the NWPS assignment apply gate — the pending / height-only placement path.

A spot is PLACED (swell_window_source=nwps) if its buoy is PASS in trust_by_buoy
(direction_status 'verified') OR its (wfo, buoy) is listed in buoy_reference.pending[]
(direction_status 'pending'); neither -> HELD. A buoy in buoy_reference.retired[] never
auto-places via the pending path. Node coords are required either way. Plus the
--validate -> assignments promotion helper (OK + OFFWIN -> placeable node entries).

Run: python -m pipeline.tests.test_apply_nwps_assignments   (or pytest)
"""
from __future__ import annotations

import importlib.util
import os

from pipeline import apply_nwps_assignments as ap

# Injected spots_enriched (build_plan matches assignment slugs against slugified names).
_ENRICHED = [{"name": "Steamer Lane"}, {"name": "Pleasure Point"}, {"name": "Waddell Creek"},
             {"name": "Moss Landing"}, {"name": "Salisbury"}, {"name": "Breezy Point"}]


def _node(**kw):
    base = {"nwps_grid": "CG1", "nwps_node_lat": 36.95, "nwps_node_lng": -122.02,
            "nwps_node_distance_m": 800}
    base.update(kw)
    return base


def _doc(spots, trust=None, pending=None, retired=None):
    return {"trust_by_buoy": trust or {}, "spots": spots,
            "buoy_reference": {"pending": pending or [], "retired": retired or []}}


def test_pending_path_places_as_pending():
    doc = _doc(
        spots=[dict(_node(), slug="steamer-lane", name="Steamer Lane", nwps_wfo="mtr", nwps_buoy_id="46240")],
        pending=[{"zone": "mtr/46240", "wfo": "mtr", "buoy": "46240"}])
    rows, problems, held, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED)
    assert [r["slug"] for r in rows] == ["steamer-lane"] and held == [] and problems == []
    f = rows[0]["fields"]
    assert rows[0]["direction_status"] == "pending"
    assert f["swell_window_source"] == "nwps" and f["nwps_direction_status"] == "pending"
    assert f["nwps_buoy_id"] == "46240" and f["nwps_wfo"] == "mtr"


def test_pass_path_places_as_verified():
    doc = _doc(
        spots=[dict(_node(), slug="breezy-point", name="Breezy Point", nwps_wfo="okx", nwps_buoy_id="44025")],
        trust={"44025": "PASS"})
    rows, _, held, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED)
    assert rows[0]["direction_status"] == "verified"
    assert rows[0]["fields"]["nwps_direction_status"] == "verified" and held == []


def test_neither_pass_nor_pending_is_held():
    doc = _doc(
        spots=[dict(_node(), slug="pleasure-point", name="Pleasure Point", nwps_wfo="mtr", nwps_buoy_id="46240")],
        trust={}, pending=[])   # 46240 is neither PASS nor pending
    rows, _, held, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED)
    assert rows == [] and [h[0] for h in held] == ["pleasure-point"]


def test_retired_buoy_never_auto_places_via_pending():
    # even if a buoy is (mistakenly) in BOTH pending[] and retired[], retired wins -> not placed here
    doc = _doc(
        spots=[dict(_node(), slug="steamer-lane", name="Steamer Lane", nwps_wfo="mtr", nwps_buoy_id="46240")],
        pending=[{"wfo": "mtr", "buoy": "46240"}],
        retired=[{"wfo": "mtr", "buoy": "46240"}])
    rows, _, held, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED)
    assert rows == [] and [h[0] for h in held] == ["steamer-lane"], "retired excluded from the pending path"


def test_missing_node_coords_is_a_problem_not_placed():
    doc = _doc(
        spots=[{"slug": "waddell-creek", "name": "Waddell Creek", "nwps_wfo": "mtr", "nwps_buoy_id": "46240"}],
        pending=[{"wfo": "mtr", "buoy": "46240"}])   # pending, but no node lat/lng
    rows, problems, held, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED)
    assert rows == [] and any("missing node" in p[1] for p in problems)


def test_pass_path_regression_unchanged():
    # a PASS spot places exactly as before (verified) with the same node fields; direction_status added
    doc = _doc(spots=[dict(_node(), slug="salisbury", name="Salisbury", nwps_wfo="box", nwps_buoy_id="44098")],
               trust={"44098": "PASS"})
    rows, _, held, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED)
    f = rows[0]["fields"]
    assert rows[0]["direction_status"] == "verified" and held == []
    assert f["swell_window_source"] == "nwps" and f["nwps_buoy_id"] == "44098" and f["nwps_wfo"] == "box"
    assert f["nwps_node_lat"] == 36.95 and f["nwps_grid"] == "CG1"


def test_force_places_a_held_spot():
    doc = _doc(spots=[dict(_node(), slug="pleasure-point", name="Pleasure Point", nwps_wfo="mtr", nwps_buoy_id="46240")])
    rows, _, held, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED, force=True)
    assert rows and rows[0]["direction_status"] == "forced" and held == []


# --------------------------------------------------------------------------- #
# Promotion helper — --validate output -> assignments 'spots'                   #
# --------------------------------------------------------------------------- #
def _load_promote():
    path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                        "scripts", "promote_nwps_validate.py")
    spec = importlib.util.spec_from_file_location("promote_nwps_validate", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_promotion_includes_ok_and_offwin_and_end_to_end_pending():
    pv = _load_promote()
    validate_out = {
        "grid_wfo": "mtr",
        "spots": [   # OK-only, full fields (the diagnostic's placed list)
            {"slug": "steamer-lane", "name": "Steamer Lane", "nwps_wfo": "mtr", "nwps_grid": "CG1",
             "nwps_node_lat": 36.951, "nwps_node_lng": -122.02, "nwps_node_distance_m": 800, "nwps_buoy_id": None}],
        "outcomes": [   # EVERY spot: OK + OFFWIN (valid node) + a genuine failure (no node)
            {"slug": "steamer-lane", "name": "Steamer Lane", "outcome": "OK",
             "nwps_node_lat": 36.951, "nwps_node_lng": -122.02, "nwps_node_distance_m": 800},
            {"slug": "moss-landing", "name": "Moss Landing", "outcome": "OFFWIN",
             "nwps_node_lat": 36.80, "nwps_node_lng": -121.79, "nwps_node_distance_m": 1200},
            {"slug": "deep-fail", "name": "Deep Fail", "outcome": "FAR",
             "nwps_node_lat": None, "nwps_node_lng": None, "nwps_node_distance_m": None}]}
    proms, skipped = pv.build_promotions(validate_out, "46240")
    got = {p["slug"]: p for p in proms}
    assert set(got) == {"steamer-lane", "moss-landing"}, "OK + OFFWIN placed; FAR (no node) skipped"
    assert got["moss-landing"]["_outcome"] == "OFFWIN", "OFFWIN is placed for height (not a failure)"
    assert any(s == "deep-fail" for s, _ in skipped)
    for p in proms:
        assert p["nwps_buoy_id"] == "46240" and p["nwps_wfo"] == "mtr" and p["nwps_grid"] == "CG1"
        assert p["nwps_node_lat"] is not None
    # end-to-end: merge into a doc with 46240 pending, then build_plan places both as PENDING
    doc = _doc(spots=[], pending=[{"wfo": "mtr", "buoy": "46240"}])
    added, replaced = pv.merge_into_assignments(doc, proms)
    assert added == 2 and replaced == 0
    assert all("_outcome" not in s for s in doc["spots"]), "informational keys stripped before write"
    rows, _, held, *_ = ap.build_plan(doc=doc, enriched=[{"name": "Steamer Lane"}, {"name": "Moss Landing"}])
    assert {r["slug"] for r in rows} == {"steamer-lane", "moss-landing"} and held == []
    assert all(r["direction_status"] == "pending" for r in rows), "promoted spots place via the pending path"


def test_promotion_two_pass_merge_does_not_clobber():
    # deliverable (b): two sequential promote runs (46284 batch, then 46237 batch) MERGE — the
    # second run adds its slugs and never overwrites the first run's, and --slugs restricts a run
    # to its own batch even if the validate-out contains extras.
    pv = _load_promote()
    vout1 = {"grid_wfo": "mtr", "spots": [], "outcomes": [
        {"slug": "pigeon-point", "name": "Pigeon Point", "outcome": "OK",
         "nwps_node_lat": 37.18, "nwps_node_lng": -122.39, "nwps_node_distance_m": 900},
        {"slug": "scotts-creek", "name": "Scotts Creek", "outcome": "OFFWIN",
         "nwps_node_lat": 37.04, "nwps_node_lng": -122.23, "nwps_node_distance_m": 1000},
        {"slug": "some-other", "name": "Other", "outcome": "OK",   # extra in the file → excluded by --slugs
         "nwps_node_lat": 36.0, "nwps_node_lng": -121.0, "nwps_node_distance_m": 500}]}
    doc = _doc(spots=[], pending=[{"wfo": "mtr", "buoy": "46284"}, {"wfo": "mtr", "buoy": "46237"}])
    p1, _ = pv.build_promotions(vout1, "46284", only_slugs={"pigeon-point", "scotts-creek"})
    a1, r1 = pv.merge_into_assignments(doc, p1)
    assert a1 == 2 and r1 == 0 and {s["slug"] for s in doc["spots"]} == {"pigeon-point", "scotts-creek"}
    assert all(s["nwps_buoy_id"] == "46284" for s in doc["spots"]), "--slugs kept only the 46284 batch"
    # run 2: file re-validated for the 46237 batch (different slugs) → merge ADDS, no clobber
    vout2 = {"grid_wfo": "mtr", "spots": [], "outcomes": [
        {"slug": "jenner-beach", "name": "Jenner Beach", "outcome": "OK",
         "nwps_node_lat": 38.45, "nwps_node_lng": -123.13, "nwps_node_distance_m": 2090},
        {"slug": "half-moon-bay", "name": "Half Moon Bay", "outcome": "OFFWIN",
         "nwps_node_lat": 37.50, "nwps_node_lng": -122.48, "nwps_node_distance_m": 1920}]}
    p2, _ = pv.build_promotions(vout2, "46237")
    a2, r2 = pv.merge_into_assignments(doc, p2)
    assert a2 == 2 and r2 == 0, "run 2 MERGES (adds) — does not overwrite run 1"
    buoy = {s["slug"]: s["nwps_buoy_id"] for s in doc["spots"]}
    assert set(buoy) == {"pigeon-point", "scotts-creek", "jenner-beach", "half-moon-bay"}
    assert buoy["pigeon-point"] == "46284" and buoy["jenner-beach"] == "46237", "each batch kept its buoy"
    # idempotent: re-promoting an existing slug REPLACES, never duplicates
    a3, r3 = pv.merge_into_assignments(doc, p1)
    assert a3 == 0 and r3 == 2 and len(doc["spots"]) == 4


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} apply-gate checks passed")


if __name__ == "__main__":
    _run_all()
