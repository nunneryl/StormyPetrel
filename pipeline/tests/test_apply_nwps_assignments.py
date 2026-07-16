"""Checks for the NWPS assignment apply gate — the pending / height-only placement path.

A spot is PLACED (swell_window_source=nwps) with direction_status:
  'unverifiable' if its slug is in buoy_reference.unverifiable[] (island-shadowed; checked
      BEFORE PASS so it can never be relabeled 'verified'; nwps_buoy_id nulled on the row), OR
  'verified'     if its buoy is PASS in trust_by_buoy — UNLESS that buoy is retired on the
      'direction' axis, in which case 'unverifiable' (the 44098 fix: PASS verifies HEIGHT only), OR
  'pending'      if its (wfo, buoy) is in buoy_reference.pending[];
  else HELD. A buoy in buoy_reference.retired[] never auto-places via the pending path. Node
coords are required. Plus the --validate -> assignments promotion helper (OK + OFFWIN -> placeable
node entries; --no-buoy -> nwps_buoy_id null for the unverifiable[] path).

Run: python -m pipeline.tests.test_apply_nwps_assignments   (or pytest)
"""
from __future__ import annotations

import importlib.util
import json
import os

from pipeline import apply_nwps_assignments as ap

_REPO = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))


def _real_doc():
    return json.loads(open(os.path.join(_REPO, "scripts", "nwps_okx_assignments.json")).read())


def _real_enriched():
    return json.loads(open(os.path.join(_REPO, "pipeline", "spots_enriched.json")).read())


# The 14 spots anchored on buoy 44098 (retired both axes) — must read 'unverifiable', never 'verified'.
_44098_SLUGS = {"salisbury", "plum-island", "good-harbor-beach", "pirates-cove", "straws-point",
                "jenness-beach", "north-beach-rye", "fox-hill", "costellos", "the-wall-hampton",
                "hampton-beach", "harbor-beach", "long-sands-beach", "ogunquit"}

# Injected spots_enriched (build_plan matches assignment slugs against slugified names).
_ENRICHED = [{"name": "Steamer Lane"}, {"name": "Pleasure Point"}, {"name": "Waddell Creek"},
             {"name": "Moss Landing"}, {"name": "Salisbury"}, {"name": "Breezy Point"}]


def _node(**kw):
    base = {"nwps_grid": "CG1", "nwps_node_lat": 36.95, "nwps_node_lng": -122.02,
            "nwps_node_distance_m": 800}
    base.update(kw)
    return base


def _doc(spots, trust=None, pending=None, retired=None, unverifiable=None):
    return {"trust_by_buoy": trust or {}, "spots": spots,
            "buoy_reference": {"pending": pending or [], "retired": retired or [],
                               "unverifiable": unverifiable or []}}


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
    # a PASS spot whose buoy is NOT retired places exactly as before (verified), same node fields
    doc = _doc(spots=[dict(_node(), slug="moss-landing", name="Moss Landing", nwps_wfo="mtr", nwps_buoy_id="44099")],
               trust={"44099": "PASS"})
    rows, _, held, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED)
    f = rows[0]["fields"]
    assert rows[0]["direction_status"] == "verified" and held == []
    assert f["swell_window_source"] == "nwps" and f["nwps_buoy_id"] == "44099" and f["nwps_wfo"] == "mtr"
    assert f["nwps_node_lat"] == 36.95 and f["nwps_grid"] == "CG1"


# --------------------------------------------------------------------------- #
# unverifiable[] (B2) + the 44098 retired-direction relabel                     #
# --------------------------------------------------------------------------- #
def test_unverifiable_slug_places_height_no_buoy():
    # a slug in unverifiable[] is PLACED for HEIGHT with direction_status 'unverifiable' and NO buoy id
    doc = _doc(
        spots=[dict(_node(), slug="steamer-lane", name="Steamer Lane", nwps_wfo="lox", nwps_buoy_id=None)],
        unverifiable=[{"zone": "lox/sbc", "wfo": "lox", "nearest_candidate": "46053", "slugs": ["steamer-lane"]}])
    rows, problems, held, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED)
    assert [r["slug"] for r in rows] == ["steamer-lane"] and held == [] and problems == []
    f = rows[0]["fields"]
    assert rows[0]["direction_status"] == "unverifiable"
    assert f["swell_window_source"] == "nwps" and f["nwps_direction_status"] == "unverifiable"
    assert f["nwps_buoy_id"] is None, "B2: no buoy id on the row"
    assert f["nwps_node_lat"] == 36.95 and f["nwps_grid"] == "CG1", "height node intact"


def test_unverifiable_slug_beats_pass_ordering():
    # load-bearing ORDERING: a spot whose buoy is PASS but whose slug is in unverifiable[] must read
    # 'unverifiable' (checked BEFORE the PASS branch), never 'verified', and its buoy id is nulled.
    doc = _doc(
        spots=[dict(_node(), slug="breezy-point", name="Breezy Point", nwps_wfo="lox", nwps_buoy_id="44025")],
        trust={"44025": "PASS"},
        unverifiable=[{"zone": "lox/sbc", "wfo": "lox", "slugs": ["breezy-point"]}])
    rows, _, held, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED)
    assert rows[0]["direction_status"] == "unverifiable", "unverifiable[] is checked BEFORE PASS"
    assert rows[0]["fields"]["nwps_buoy_id"] is None, "B2 nulls the buoy even though it was PASS"
    assert rows[0]["fields"]["swell_window_source"] == "nwps" and held == []


def test_pass_but_retired_direction_axis_is_unverifiable_not_verified():
    # 44098 shape: PASS in trust (a HEIGHT verification) BUT retired on the 'direction' axis ->
    # direction_status must read 'unverifiable', never 'verified'; still PLACED, buoy kept, height intact.
    doc = _doc(spots=[dict(_node(), slug="salisbury", name="Salisbury", nwps_wfo="box", nwps_buoy_id="44098")],
               trust={"44098": "PASS"},
               retired=[{"zone": "box/44098", "wfo": "box", "buoy": "44098", "axes": ["height", "direction"]}])
    rows, _, held, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED)
    f = rows[0]["fields"]
    assert rows[0]["direction_status"] == "unverifiable" and held == []
    assert f["nwps_direction_status"] == "unverifiable"
    assert f["swell_window_source"] == "nwps", "still height-live"
    assert f["nwps_buoy_id"] == "44098", "the (invalid) buoy id is kept — PASS placed it for height"
    assert f["nwps_node_lat"] == 36.95, "height node intact"


def test_pass_retired_height_only_axis_stays_verified():
    # 'axes' granularity: retiring ONLY the height axis must NOT relabel direction — stays 'verified'
    doc = _doc(spots=[dict(_node(), slug="salisbury", name="Salisbury", nwps_wfo="box", nwps_buoy_id="44098")],
               trust={"44098": "PASS"},
               retired=[{"zone": "box/44098", "wfo": "box", "buoy": "44098", "axes": ["height"]}])
    rows, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED)
    assert rows[0]["direction_status"] == "verified", "only a 'direction'-axis retirement flips to unverifiable"


def test_44098_retired_direction_flips_verified_to_unverifiable_live():
    # LIVE: against the real assignments + enriched, all 14 44098 spots read 'unverifiable' (not
    # 'verified') and remain PLACED with height intact and the 44098 buoy id kept.
    doc, enriched = _real_doc(), _real_enriched()
    rows, problems, held, *_ = ap.build_plan(doc=doc, enriched=enriched)
    by_slug = {r["slug"]: r for r in rows}
    missing = _44098_SLUGS - set(by_slug)
    assert not missing, f"44098 spots not placed: {sorted(missing)}"
    for s in sorted(_44098_SLUGS):
        r = by_slug[s]
        assert r["direction_status"] == "unverifiable", f"{s}: {r['direction_status']} (want unverifiable)"
        assert r["fields"]["nwps_direction_status"] == "unverifiable"
        assert r["fields"]["swell_window_source"] == "nwps", f"{s} must stay height-live"
        assert r["fields"]["nwps_buoy_id"] == "44098", f"{s} keeps its buoy id"
        assert r["fields"]["nwps_node_lat"] is not None, f"{s} height node intact"
    assert not (_44098_SLUGS & {r["slug"] for r in rows if r["direction_status"] == "verified"}), \
        "no 44098 spot may read 'verified'"


def test_full_dryrun_direction_status_breakdown_live():
    # LIVE dry-run math: 91 verified + 39 pending + 14 unverifiable = 144 placed (see task).
    doc, enriched = _real_doc(), _real_enriched()
    rows, problems, held, *_ = ap.build_plan(doc=doc, enriched=enriched)
    counts = {}
    for r in rows:
        counts[r["direction_status"]] = counts.get(r["direction_status"], 0) + 1
    assert counts.get("unverifiable") == 14, f"want 14 unverifiable (the 44098 spots), got {counts}"
    assert counts.get("pending") == 39, f"want 39 pending (46240+46237+46284), got {counts}"
    assert counts.get("verified") == 91, f"want 91 verified (105 − 14 flipped), got {counts}"
    assert len(rows) == 144 and not problems, f"144 placed, no problems (problems={problems})"


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


def test_promotion_no_buoy_sets_null_buoy_id_and_places_unverifiable():
    # --no-buoy path: promote with buoy=None -> nwps_buoy_id null; merges by slug; then, once the slug
    # is listed in buoy_reference.unverifiable[], build_plan places it 'unverifiable' (height, null buoy).
    pv = _load_promote()
    validate_out = {"grid_wfo": "lox", "spots": [], "outcomes": [
        {"slug": "jalama", "name": "Jalama", "outcome": "OK",
         "nwps_node_lat": 34.51, "nwps_node_lng": -120.50, "nwps_node_distance_m": 700}]}
    proms, skipped = pv.build_promotions(validate_out, None)   # None buoy -> null on the row
    assert len(proms) == 1 and proms[0]["nwps_buoy_id"] is None, "no-buoy promote -> null buoy id"
    assert proms[0]["nwps_node_lat"] == 34.51 and proms[0]["nwps_wfo"] == "lox"
    doc = _doc(spots=[], unverifiable=[{"zone": "lox/sbc", "wfo": "lox", "slugs": ["jalama"]}])
    added, replaced = pv.merge_into_assignments(doc, proms)
    assert added == 1 and doc["spots"][0]["nwps_buoy_id"] is None and doc["spots"][0]["slug"] == "jalama"
    rows, _, held, *_ = ap.build_plan(doc=doc, enriched=[{"name": "Jalama"}])
    assert [r["slug"] for r in rows] == ["jalama"] and held == []
    assert rows[0]["direction_status"] == "unverifiable" and rows[0]["fields"]["nwps_buoy_id"] is None


def test_promotion_wfo_override_keeps_grid_crossing_label():
    # --wfo override: a spot validated on the lox grid (grid_wfo=lox) but promoted with wfo_override=
    # "mtr" keeps nwps_wfo="mtr" (the grid-crossing SLO/Pismo case) while taking the lox-grid node.
    pv = _load_promote()
    vout_lox = {"grid_wfo": "lox", "spots": [], "outcomes": [
        {"slug": "pismo-beach-pier", "name": "Pismo Beach Pier", "outcome": "OK",
         "nwps_node_lat": 35.14, "nwps_node_lng": -120.64, "nwps_node_distance_m": 900}]}
    proms, _ = pv.build_promotions(vout_lox, "46215", wfo_override="mtr")
    assert proms[0]["nwps_wfo"] == "mtr", "wfo override keeps the mtr label"
    assert proms[0]["nwps_buoy_id"] == "46215" and proms[0]["nwps_node_lat"] == 35.14, "lox-grid node kept"
    # default (no override) still uses grid_wfo
    proms2, _ = pv.build_promotions(vout_lox, "46215")
    assert proms2[0]["nwps_wfo"] == "lox", "without override, nwps_wfo = grid_wfo (unchanged default)"


def test_promotion_two_source_files_one_buoy_46215_grid_crossing():
    # deliverable (b): the 12 SLO spots split across TWO validate-out files but land under ONE buoy
    # (46215) via merge-by-slug — 8 from mtr validate-out, 4 from lox validate-out (--wfo mtr). All 12
    # keep nwps_wfo="mtr", so the single (mtr, 46215) pending key places them all as 'pending'.
    pv = _load_promote()
    vout_mtr = {"grid_wfo": "mtr", "spots": [], "outcomes": [
        {"slug": "avila-beach", "name": "Avila Beach", "outcome": "OK",
         "nwps_node_lat": 35.18, "nwps_node_lng": -120.73, "nwps_node_distance_m": 800},
        {"slug": "morro-rock", "name": "Morro Rock", "outcome": "OFFWIN",
         "nwps_node_lat": 35.37, "nwps_node_lng": -120.87, "nwps_node_distance_m": 1100}]}
    vout_lox = {"grid_wfo": "lox", "spots": [], "outcomes": [
        {"slug": "shell-beach", "name": "Shell Beach", "outcome": "OK",
         "nwps_node_lat": 35.15, "nwps_node_lng": -120.67, "nwps_node_distance_m": 950},
        {"slug": "grover-beach", "name": "Grover Beach", "outcome": "OK",
         "nwps_node_lat": 35.12, "nwps_node_lng": -120.63, "nwps_node_distance_m": 700}]}
    doc = _doc(spots=[], pending=[{"zone": "mtr/46215", "wfo": "mtr", "buoy": "46215"}])
    # pass 1: the mtr-grid batch (grid_wfo=mtr, no override)
    p1, _ = pv.build_promotions(vout_mtr, "46215")
    a1, r1 = pv.merge_into_assignments(doc, p1)
    # pass 2: the lox-grid batch, SAME buoy 46215, --wfo mtr → keeps the mtr label
    p2, _ = pv.build_promotions(vout_lox, "46215", wfo_override="mtr")
    a2, r2 = pv.merge_into_assignments(doc, p2)
    assert (a1, r1, a2, r2) == (2, 0, 2, 0), "two source files MERGE (add), never clobber"
    assert {s["slug"] for s in doc["spots"]} == {"avila-beach", "morro-rock", "shell-beach", "grover-beach"}
    assert all(s["nwps_buoy_id"] == "46215" for s in doc["spots"]), "all four land under buoy 46215"
    assert all(s["nwps_wfo"] == "mtr" for s in doc["spots"]), "all keep nwps_wfo=mtr (label not relabeled to lox)"
    # the single (mtr, 46215) pending key places all four as 'pending'
    enr = [{"name": "Avila Beach"}, {"name": "Morro Rock"}, {"name": "Shell Beach"}, {"name": "Grover Beach"}]
    rows, _, held, *_ = ap.build_plan(doc=doc, enriched=enr)
    assert len(rows) == 4 and held == [] and all(r["direction_status"] == "pending" for r in rows)


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} apply-gate checks passed")


if __name__ == "__main__":
    _run_all()
