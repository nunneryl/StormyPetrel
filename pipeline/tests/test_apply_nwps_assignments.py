"""Checks for the NWPS assignment apply gate — the pending / height-only placement path.

A spot is PLACED (swell_window_source=nwps) with direction_status:
  'unverifiable' if its slug is in buoy_reference.unverifiable[] (island-shadowed; checked
      BEFORE PASS so it can never be relabeled 'verified'; nwps_buoy_id nulled on the row), OR
  'verified'     if its ZONE ("wfo/buoy") is PASS in trust_by_zone — UNLESS that buoy is retired
      on the 'direction' axis, in which case 'unverifiable' (the 44098 fix: PASS verifies HEIGHT
      only). Keyed by ZONE, not buoy: a PASS must not verify another WFO's spots on the same buoy, OR
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
    # *trust* is keyed by ZONE — {"wfo/buoy": verdict} — matching the live file.
    return {"trust_by_zone": trust or {}, "spots": spots,
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
        trust={"okx/44025": "PASS"})
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
               trust={"mtr/44099": "PASS"})
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
        trust={"lox/44025": "PASS"},
        unverifiable=[{"zone": "lox/sbc", "wfo": "lox", "slugs": ["breezy-point"]}])
    rows, _, held, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED)
    assert rows[0]["direction_status"] == "unverifiable", "unverifiable[] is checked BEFORE PASS"
    assert rows[0]["fields"]["nwps_buoy_id"] is None, "B2 nulls the buoy even though it was PASS"
    assert rows[0]["fields"]["swell_window_source"] == "nwps" and held == []


def test_pass_but_retired_direction_axis_is_unverifiable_not_verified():
    # 44098 shape: PASS in trust (a HEIGHT verification) BUT retired on the 'direction' axis ->
    # direction_status must read 'unverifiable', never 'verified'; still PLACED, buoy kept, height intact.
    doc = _doc(spots=[dict(_node(), slug="salisbury", name="Salisbury", nwps_wfo="box", nwps_buoy_id="44098")],
               trust={"box/44098": "PASS"},
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
               trust={"box/44098": "PASS"},
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
    # LIVE consistency check: derive the expected verified/pending/unverifiable split from the
    # assignments doc itself, then assert build_plan agrees — no hardcoded totals, so it stays
    # correct as new pending zones are promoted into spots[]. The 44098 retired-direction spots
    # remain the 'unverifiable' set today (14).
    doc, enriched = _real_doc(), _real_enriched()
    ref, trust = doc["buoy_reference"], doc["trust_by_zone"]

    def keyset(records):
        return {(r["wfo"], str(r["buoy"])) for r in records if r.get("wfo") and r.get("buoy") is not None}

    pending_keys = keyset(ref.get("pending", []))
    retired_keys = keyset(ref.get("retired", []))
    retired_dir = keyset([r for r in ref.get("retired", []) if "direction" in (r.get("axes") or [])])
    unver_slugs = {s for u in ref.get("unverifiable", []) for s in (u.get("slugs") or [])}
    exp = {}
    for a in doc["spots"]:
        buoy = a.get("nwps_buoy_id")
        key = (a.get("nwps_wfo"), str(buoy))
        if a.get("slug") in unver_slugs:
            label = "unverifiable"
        elif trust.get(f"{a.get('nwps_wfo')}/{buoy}") == "PASS":
            label = "unverifiable" if key in retired_dir else "verified"
        elif key in pending_keys and key not in retired_keys:
            label = "pending"
        else:
            continue   # held — not placed
        exp[label] = exp.get(label, 0) + 1
    rows, problems, held, *_ = ap.build_plan(doc=doc, enriched=enriched)
    got = {}
    for r in rows:
        got[r["direction_status"]] = got.get(r["direction_status"], 0) + 1
    assert not problems, f"every assignment spot should match enriched + have a node (problems={problems})"
    assert got == exp, f"build_plan split {got} != doc-derived {exp}"
    assert got.get("unverifiable", 0) >= 14, f"at least the 14 retired-direction 44098 spots are unverifiable, got {got}"


def _assert_unverifiable_record(zone, n_slugs, wfo):
    """Apply-state-agnostic check for a buoy_reference.unverifiable[] record: every slug places as
    direction_status='unverifiable' with a null buoy (B2), verified/pending are untouched, and the
    unverifiable count grows by exactly the number of its slugs NOT already promoted into spots[].
    Works whether or not the zone has been applied yet (only the not-yet-applied slugs are appended),
    so it never goes stale as zones are promoted."""
    doc, enriched = _real_doc(), _real_enriched()
    rec = next(r for r in doc["buoy_reference"]["unverifiable"] if r["zone"] == zone)
    slugs = rec["slugs"]
    assert len(slugs) == n_slugs and rec["wfo"] == wfo, f"{zone}: {len(slugs)} slugs, wfo {rec['wfo']}"
    assert "buoy" not in rec, f"{zone}: no buoy on the record (B2, slug-keyed)"
    pend_slugs = {s for p in doc["buoy_reference"]["pending"] for s in p.get("slugs", [])}
    assert not (set(slugs) & pend_slugs), f"{zone}: unverifiable-only, never pending"
    assert not (set(slugs) & set(doc["trust_by_zone"])), f"{zone}: no slug collides with a trust zone"

    def split(d, e):
        rows, problems, *_ = ap.build_plan(doc=d, enriched=e)
        c = {}
        for r in rows:
            c[r["direction_status"]] = c.get(r["direction_status"], 0) + 1
        return c, rows, problems

    base, _, base_problems = split(doc, enriched)
    assert not base_problems
    applied = {s.get("slug") for s in doc["spots"]}
    to_add = [s for s in slugs if s not in applied]
    for sl in to_add:      # emulate `promote_nwps_validate --no-buoy` for the not-yet-placed slugs
        name = sl.replace("-", " ").title()
        doc["spots"].append({"slug": sl, "name": name, "nwps_wfo": wfo, "nwps_grid": "CG1",
                             "nwps_node_lat": 40.0, "nwps_node_lng": -124.0,
                             "nwps_node_distance_m": 1200, "nwps_buoy_id": None})
        enriched.append({"name": name})
    got, rows, problems = split(doc, enriched)
    by_slug = {r["slug"]: r for r in rows}
    for sl in slugs:       # every record slug — applied or just-appended — places unverifiable + null buoy
        r = by_slug[sl]
        assert r["direction_status"] == "unverifiable", f"{sl}: {r['direction_status']} (want unverifiable)"
        assert r["fields"]["nwps_buoy_id"] is None, f"{sl}: null buoy on the row (B2)"
        assert r["fields"]["nwps_direction_status"] == "unverifiable"
        assert r["fields"]["swell_window_source"] == "nwps", f"{sl}: height-live via NWPS"
    assert got.get("verified") == base.get("verified"), f"{zone}: verified unchanged"
    assert got.get("pending") == base.get("pending"), f"{zone}: pending unchanged"
    assert got.get("unverifiable") == base.get("unverifiable") + len(to_add), \
        f"{zone}: unverifiable grew by exactly the {len(to_add)} not-yet-applied slugs"
    assert not problems


def test_santa_barbara_channel_unverifiable_record():
    # lox/santa-barbara-channel: 24 island-shadowed spots (valid buoys, geometrically shadowed).
    _assert_unverifiable_record("lox/santa-barbara-channel", 24, "lox")


def test_california_closeout_eka_and_point_arena_unverifiable():
    # Closing out CA — the "no valid buoy exists at all" case (retire both axes), distinct from the
    # valid-but-shadowed Santa Barbara Channel. Both records place height-live, direction unverifiable,
    # null buoy; verified/pending untouched.
    _assert_unverifiable_record("eka/north-coast", 11, "eka")     # far NorCal — FIRST eka placement
    _assert_unverifiable_record("mtr/north-pocket", 1, "mtr")     # Point Arena, above the 46237 zone


def test_force_places_a_held_spot():
    doc = _doc(spots=[dict(_node(), slug="pleasure-point", name="Pleasure Point", nwps_wfo="mtr", nwps_buoy_id="46240")])
    rows, _, held, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED, force=True)
    assert rows and rows[0]["direction_status"] == "forced" and held == []


# --------------------------------------------------------------------------- #
# trust_by_zone — a PASS is scoped to ONE zone, never to a buoy across WFOs     #
# --------------------------------------------------------------------------- #
def _refusal(doc):
    """build_plan's ValueError message, or None if it did not raise."""
    try:
        ap.build_plan(doc=doc, enriched=_ENRICHED)
        return None
    except ValueError as e:
        return str(e)


def test_pass_on_one_zone_does_not_verify_another_zone_on_the_same_buoy():
    # THE DEFECT THIS FIX CLOSES. 44084 anchors phi/44084 (22 spots) and akq/44084 (3) — an
    # administrative WFO boundary, one physical buoy. The settled-zone issue names phi/44084, a
    # human pastes that PASS, and akq's spots must NOT become direction-verified on evidence
    # earned in another WFO: they stay HELD until akq settles on its own.
    doc = _doc(
        spots=[dict(_node(), slug="breezy-point", name="Breezy Point", nwps_wfo="phi", nwps_buoy_id="44084"),
               dict(_node(), slug="salisbury", name="Salisbury", nwps_wfo="akq", nwps_buoy_id="44084")],
        trust={"phi/44084": "PASS"})
    rows, problems, held, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED)
    assert [(r["slug"], r["direction_status"]) for r in rows] == [("breezy-point", "verified")], \
        "only the zone that earned the PASS places verified"
    assert [h[0] for h in held] == ["salisbury"], "the akq zone earned no PASS of its own — HELD"
    assert held[0][1] == "akq/44084", "held names the ZONE key a human would have to add"
    assert not problems


def test_both_zones_verify_only_when_both_are_tagged():
    # the other half of the same contract: two PASS entries on one buoy verify BOTH zones (this is
    # how box/44098 + gyx/44098 keep their 14 spots), so the gate is scoped, not merely stricter.
    doc = _doc(
        spots=[dict(_node(), slug="breezy-point", name="Breezy Point", nwps_wfo="phi", nwps_buoy_id="44084"),
               dict(_node(), slug="salisbury", name="Salisbury", nwps_wfo="akq", nwps_buoy_id="44084")],
        trust={"phi/44084": "PASS", "akq/44084": "PASS"})
    rows, _, held, *_ = ap.build_plan(doc=doc, enriched=_ENRICHED)
    assert held == [], f"both zones are tagged — neither is held ({[h[1] for h in held]})"
    assert sorted((r["slug"], r["direction_status"]) for r in rows) == \
        [("breezy-point", "verified"), ("salisbury", "verified")], \
        "a buoy spanning two WFOs is not a reason to withhold either zone's own PASS"


def test_buoy_keyed_trust_map_is_refused():
    # old format STOPS, naming the migration — it is never silently accepted (the line-83
    # precedent, which refused the legacy global 'trust' flag the same way).
    doc = {"trust_by_buoy": {"44025": "PASS"}, "spots": [], "buoy_reference": {}}
    msg = _refusal(doc)
    assert msg, "a buoy-keyed file must STOP, not be quietly read as if it were zone-keyed"
    assert "trust_by_buoy" in msg and "trust_by_zone" in msg, f"names both shapes: {msg}"
    assert "wfo/buoy" in msg, f"names the migration target: {msg}"
    assert "44025" in msg, f"echoes what it found so the reader can see it: {msg}"
    # the even older global-flag file still gets a useful diagnostic
    assert "True" in (_refusal({"trust": True, "spots": []}) or "")


def test_half_migrated_trust_map_is_refused():
    # a file where SOME keys are scoped and some are not is worse than one that stops: the
    # unscoped key would silently keep its buoy-wide reach. Refuse, and name the offending keys.
    doc = _doc(spots=[], trust={"okx/44025": "PASS", "44065": "PASS"})
    msg = _refusal(doc)
    assert msg, "a half-migrated map must STOP"
    assert "44065" in msg and "okx/44025" not in msg, f"names only the unscoped key: {msg}"
    assert "HALF-MIGRATED" in msg, msg


def test_single_zone_passes_place_verified_unchanged_live():
    # REGRESSION for the five single-WFO PASS zones (okx/44025, phi/44065, akq/44099, phi/44091,
    # box/44097): every spot they anchor still reads 'verified', exactly as under buoy keying.
    # The box/44098 + gyx/44098 pair is excluded here — retired on the direction axis, so their
    # PASS is a HEIGHT verification only and their 14 spots read 'unverifiable' (asserted above).
    doc, enriched = _real_doc(), _real_enriched()
    rows, problems, held, trust, *_ = ap.build_plan(doc=doc, enriched=enriched)
    by_slug = {r["slug"]: r for r in rows}
    retired_dir = {r["zone"] for r in doc["buoy_reference"]["retired"]
                   if "direction" in (r.get("axes") or [])}
    unver_slugs = {s for u in doc["buoy_reference"]["unverifiable"] for s in (u.get("slugs") or [])}
    single = sorted(z for z, v in trust.items() if v == "PASS" and z not in retired_dir)
    assert len(single) == 5, f"five single-WFO PASS zones today, got {single}"
    seen = 0
    for a in doc["spots"]:
        zone = f"{a.get('nwps_wfo')}/{a.get('nwps_buoy_id')}"
        if zone not in single or a.get("slug") in unver_slugs:
            continue
        assert a["slug"] in by_slug, f"{zone} {a['slug']}: PASS zone no longer places at all"
        r = by_slug[a["slug"]]
        assert r["direction_status"] == "verified", f"{zone} {a['slug']}: {r['direction_status']}"
        assert r["fields"]["nwps_buoy_id"] == str(a["nwps_buoy_id"])
        seen += 1
    assert seen >= 5 and not problems and not held


def test_live_trust_map_is_zone_keyed_and_covers_both_44098_wfos():
    doc = _real_doc()
    assert "trust_by_buoy" not in doc, "the buoy-keyed map is GONE, not left alongside the new one"
    zm = doc["trust_by_zone"]
    assert all("/" in k for k in zm), f"every key is wfo/buoy: {sorted(zm)}"
    # 44098's PASS could not migrate to a single key: it anchors box (3 spots) AND gyx (11), so one
    # entry would have dropped the other WFO's spots to held. Both zones are tagged.
    assert {"box/44098", "gyx/44098"} <= set(zm), f"both 44098 zones carry the PASS: {sorted(zm)}"


def test_zone_keying_moved_no_live_placement():
    # The migration re-SCOPES a future PASS; it must not move a single current placement. Collapse
    # the live zone map back to the buoy keying it replaced and assert the two lookups agree on
    # every assignment row — this fails loudly if a zone key were ever dropped or mis-derived.
    doc, enriched = _real_doc(), _real_enriched()
    zone_map = doc["trust_by_zone"]
    buoy_map = {}
    for z, v in zone_map.items():
        buoy = z.split("/", 1)[1]
        assert buoy_map.setdefault(buoy, v) == v, f"{buoy}: zones disagree — no buoy-keyed equivalent"
    for a in doc["spots"]:
        zone = f"{a.get('nwps_wfo')}/{a.get('nwps_buoy_id')}"
        assert zone_map.get(zone) == buoy_map.get(str(a.get("nwps_buoy_id"))), \
            f"{zone}: zone keying disagrees with the buoy keying it replaced — a placement moved"
    rows, problems, held, *_ = ap.build_plan(doc=doc, enriched=enriched)
    # derived, not hardcoded — the same discipline as test_full_dryrun_direction_status_breakdown_live,
    # so it stays correct as new zones are promoted into spots[] (it went stale once at 491 when hfo
    # added 55). The invariant is that EVERY assignment spot places, whatever the total is.
    assert len(rows) == len(doc["spots"]) and not held and not problems, \
        f"every assignment spot should place ({len(rows)} rows of {len(doc['spots'])}, " \
        f"{len(held)} held, {len(problems)} problems)"


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


# --------------------------------------------------------------------------- #
# Node overrides — pipeline/data/nwps_node_overrides.json                       #
# --------------------------------------------------------------------------- #
# Two spots on the equator and at 60 N. 0.01 deg of longitude on the equator, and 0.02 deg at
# 60 N, are both 6371 x pi/180 x 0.01 = 1.111949 km on the great circle -> 1112 m.
_GEO = [{"name": "Steamer Lane", "lat": 0.0, "lng": 0.0},
        {"name": "Pleasure Point", "lat": 60.0, "lng": 0.0}]
_OV_DOC = _doc(spots=[dict(_node(nwps_node_lat=0.0, nwps_node_lng=0.005, nwps_node_distance_m=556),
                           slug="steamer-lane", name="Steamer Lane", nwps_wfo="mtr",
                           nwps_buoy_id="46240"),
                      dict(_node(nwps_node_lat=60.0, nwps_node_lng=0.001, nwps_node_distance_m=56),
                           slug="pleasure-point", name="Pleasure Point", nwps_wfo="mtr",
                           nwps_buoy_id="46240")],
               pending=[{"zone": "mtr/46240", "wfo": "mtr", "buoy": "46240"}])


def _ov(**kw):
    base = {"nwps_wfo": "mtr", "nwps_node_lat": 0.0, "nwps_node_lng": 0.01,
            "replaces": [0.0, 0.005], "reason": "r", "evidence": "e"}
    base.update(kw)
    return base


def test_node_override_moves_the_node_and_measures_its_distance():
    rows, *_ = ap.build_plan(doc=_OV_DOC, enriched=_GEO, overrides={"steamer-lane": _ov()})
    f = {r["slug"]: r for r in rows}["steamer-lane"]
    assert f["node_override"] is True
    assert (f["fields"]["nwps_node_lat"], f["fields"]["nwps_node_lng"],
            f["fields"]["nwps_node_distance_m"]) == (0.0, 0.01, 1112)
    assert f["fields"]["nwps_wfo"] == "mtr" and f["fields"]["nwps_buoy_id"] == "46240"
    assert f["direction_status"] == "pending"


def test_node_override_distance_at_60_north():
    ov = _ov(nwps_node_lat=60.0, nwps_node_lng=0.02, replaces=[60.0, 0.001])
    rows, *_ = ap.build_plan(doc=_OV_DOC, enriched=_GEO, overrides={"pleasure-point": ov})
    f = {r["slug"]: r for r in rows}["pleasure-point"]["fields"]
    assert f["nwps_node_distance_m"] == 1112


def test_a_spot_without_an_override_keeps_its_assigned_node():
    rows, *_ = ap.build_plan(doc=_OV_DOC, enriched=_GEO, overrides={"steamer-lane": _ov()})
    r = {r["slug"]: r for r in rows}["pleasure-point"]
    assert r["node_override"] is False
    assert (r["fields"]["nwps_node_lat"], r["fields"]["nwps_node_lng"],
            r["fields"]["nwps_node_distance_m"]) == (60.0, 0.001, 56)


def _raises(**kw):
    try:
        ap.build_plan(doc=_OV_DOC, enriched=_GEO, **kw)
    except ValueError as e:
        return str(e)
    raise AssertionError("expected ValueError")


def test_node_override_on_another_wfo_stops():
    msg = _raises(overrides={"steamer-lane": _ov(nwps_wfo="lox")})
    assert "steamer-lane" in msg and "'lox'" in msg and "'mtr'" in msg


def test_node_override_of_a_node_the_assignment_no_longer_names_stops():
    msg = _raises(overrides={"steamer-lane": _ov(replaces=[0.0, 0.004])})
    assert "steamer-lane" in msg and "re-placed" in msg
    assert "[0.0, 0.005]" in msg
    msg = _raises(overrides={"steamer-lane": _ov(replaces=[0.001, 0.005])})
    assert "re-placed" in msg


def test_node_override_with_no_replaces_stops():
    ov = _ov()
    del ov["replaces"]
    assert "re-placed" in _raises(overrides={"steamer-lane": ov})


def test_node_override_for_a_spot_no_assignment_places_stops():
    msg = _raises(overrides={"waddell-creek": _ov()})
    assert "['waddell-creek']" in msg and "no assignment places" in msg
    held = _doc(spots=[dict(_node(), slug="steamer-lane", name="Steamer Lane", nwps_wfo="mtr",
                            nwps_buoy_id="46240")])
    try:
        ap.build_plan(doc=held, enriched=_GEO, overrides={"steamer-lane": _ov()})
    except ValueError as e:
        assert "['steamer-lane']" in str(e)
    else:
        raise AssertionError("a HELD spot's override must stop the plan")


def test_an_injected_doc_reads_no_override_file():
    # The live file moves duck-pier, which this doc does not place; with no override file read
    # the plan builds.
    rows, *_ = ap.build_plan(doc=_OV_DOC, enriched=_GEO)
    assert all(r["node_override"] is False for r in rows) and len(rows) == 2


def test_load_node_overrides_with_no_file_is_empty():
    from pathlib import Path
    assert ap.load_node_overrides(Path(_REPO) / "no-such-overrides.json") == {}


def test_the_dry_run_marks_an_overridden_node():
    import contextlib
    import io
    rows, problems, held, trust, *_ = ap.build_plan(doc=_OV_DOC, enriched=_GEO,
                                                    overrides={"steamer-lane": _ov()})
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        ap.print_dry_run(rows, problems, held, trust)
    lines = buf.getvalue().splitlines()
    assert [ln.split()[0] for ln in lines if ln.endswith("(node override)")] == ["steamer-lane"]


# Run 50's cells, copied by hand from docs/face_validation/run-50/face_seaward/deeper.csv
# (cell_lat, cell_lng, cell_km) and the current points they replace (node_lat, node_lng).
_RUN50 = {"duck-pier": ((36.19331, -75.72892), 2.07, (36.19317, -75.74849)),
          "spring-ave": ((27.51019, -82.80924), 7.26, (27.51007, -82.7375)),
          "st-pete-beach": ((27.7373, -82.86304), 9.94, (27.73716, -82.77337)),
          "treasure-island-fl": ((27.7373, -82.86304), 9.45, (27.75338, -82.77337)),
          "madeira-beach": ((27.7373, -82.86304), 9.16, (27.78582, -82.80924))}
# Run 58 (nwps-cells): the Hatteras beach column beside the stretch where MHX's grid has no
# island, moved one column seaward. km to 3 decimals: the great circle from the roster spot to
# the cell, worked separately from apply_nwps_assignments.
_RUN58 = {"rodanthe": ((35.5911, -75.43524), 2.465, (35.5911, -75.45482)),
          "salvo": ((35.52602, -75.43524), 2.996, (35.54229, -75.45482)),
          "s-turns": ((35.60738, -75.43524), 2.675, (35.60738, -75.45482))}
_MOVES = {**_RUN50, **_RUN58}


def test_live_overrides_are_run_50s_and_run_58s_cells():
    ov = ap.load_node_overrides()
    assert set(ov) == set(_MOVES)
    for slug, (cell, _km, was) in _MOVES.items():
        assert (ov[slug]["nwps_node_lat"], ov[slug]["nwps_node_lng"]) == cell, slug
        assert ov[slug]["replaces"] == list(was), slug
        assert ov[slug]["reason"] and ov[slug]["evidence"], slug


def test_live_roster_carries_the_moved_nodes_and_the_assignments_the_old_ones():
    by_slug = {ap._slug(s["name"]): s for s in _real_enriched()}
    assigned = {a["slug"]: a for a in _real_doc()["spots"]}
    for slug, (cell, km, was) in _MOVES.items():
        s = by_slug[slug]
        assert (s["nwps_node_lat"], s["nwps_node_lng"]) == cell, slug
        # run 50 printed the km to 2 decimals, so the metres agree to within 5
        assert abs(s["nwps_node_distance_m"] - km * 1000) <= 5, slug
        assert (assigned[slug]["nwps_node_lat"], assigned[slug]["nwps_node_lng"]) == was, slug


def test_live_rebake_writes_what_the_roster_already_holds():
    # A re-bake from the files on disk must be a no-op on spots_enriched.json: every placed
    # spot's node fields, the moved eight included, already read what apply would write.
    enriched = _real_enriched()
    by_slug = {ap._slug(s["name"]): s for s in enriched}
    rows, problems, held, *_ = ap.build_plan()
    assert not problems and not held
    assert sorted(r["slug"] for r in rows if r["node_override"]) == sorted(_MOVES)
    for r in rows:
        spot = by_slug[r["slug"]]
        for k, v in r["fields"].items():
            assert spot.get(k) == v, f"{r['slug']}.{k}: roster {spot.get(k)!r}, re-bake {v!r}"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"{len(fns)} apply-gate checks passed")


if __name__ == "__main__":
    _run_all()
