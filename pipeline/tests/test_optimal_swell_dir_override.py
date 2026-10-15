"""The durable override channel for optimal_swell_dir (SW-1 round one).

THE DEFECT. interpret.directional_gain targets `optimal_swell_dir` and falls back to
`orientation_deg` only when the optimal is null, so correcting an orientation by hand does
not move the rating. Nine spots carried an optimal more than 135 degrees from their own
facing — a swell arriving from behind the land — and six of them scored dir_gain 1.000 on
it. Honolua Bay rated 3.5 stars off an 84-degree east swell that would cross the whole of
Maui. Every one already had orientation_source == "manual": the orientation channel exists
and does nothing here, because it is the wrong field.

THE FIX. pipeline/data/spot_swell_windows.json, a slug-keyed file mirroring
spot_orientations.json, applied by enrich as Algo 2d — the LAST writer of optimal_swell_dir
in enrich, by direct assignment rather than _set, so it beats the raycast, the
orientation-derived fallback, the tier preserve-guard and LLM verification alike. It stamps
`swell_window_source_override = "manual"`, a field distinct from `swell_window_source`
(which carries the nwps/cdip_mop/raycast tier and decides which forecast reader runs).
Three downstream writers read that stamp and stand down.

NOT COVERED, DELIBERATELY: sw1_raycast.py:353 writes both fields outside enrich and is not
guarded in round one. test_the_unguarded_raycast_writer_is_recorded_as_a_known_limit pins
that the gap is documented rather than forgotten.

EVERY EXPECTED VALUE IS WRITTEN LITERALLY. The ten degrees are transcribed from the
researched sources, not read back from the loader.

Run: python -m pipeline.tests.test_optimal_swell_dir_override
"""
from __future__ import annotations

import json

from pipeline import enrich as E
from pipeline.config import SPOT_SWELL_WINDOWS_FILE
# Both modules export a function called merge_into_spots; alias them apart.
from pipeline.scrape_surf_forecast import merge_into_spots as scrape_merge
from pipeline.verify_spots import merge_into_spots as verify_merge

# The ten researched values, transcribed from the sources recorded in the file. NOT derived
# from _load_spot_swell_windows — that is the function under test.
EXPECTED = {
    "honolua-bay": 350.0,
    "spoils": 40.0,
    "hapuna-beach": 280.0,
    "suicide-s": 292.0,
    "thousand-peaks-maui": 200.0,
    "crash-boat-beach": 305.0,
    "gas-chambers-aguadilla": 315.0,
    "table-tops-aguadilla": 350.0,
    "lovers-point": 315.0,
    "monterey-beach": 320.0,
}

# The roster name each slug must resolve to. Four of the ten were proposed under a shorter
# slug that does not exist; these are the names the values were researched against.
EXPECTED_NAMES = {
    "honolua-bay": "Honolua Bay",
    "spoils": "Spoils",
    "hapuna-beach": "Hapuna Beach",
    "suicide-s": "Suicide's",
    "thousand-peaks-maui": "Thousand Peaks Maui",
    "crash-boat-beach": "Crash Boat Beach",
    "gas-chambers-aguadilla": "Gas Chambers Aguadilla",
    "table-tops-aguadilla": "Table Tops Aguadilla",
    "lovers-point": "Lovers Point",
    "monterey-beach": "Monterey Beach",
}


def _spot(name="Honolua Bay", **kw):
    """A spot shaped enough for _enrich_one's swell-window stretch."""
    s = {"name": name, "lat": 21.0159, "lng": -156.6391,
         "swell_window_arcs": [{"min": 341, "max": 47, "span": 70}],
         "optimal_swell_dir": 84, "orientation_deg": 278.0,
         "swell_window_source": "nwps"}
    s.update(kw)
    return s


# --------------------------------------------------------------------------- #
# 1 — the file loads, and every value in it is the researched one              #
# --------------------------------------------------------------------------- #

def test_the_override_file_loads():
    got = E._load_spot_swell_windows()
    assert got == EXPECTED, got


def test_the_module_global_is_loaded_at_import():
    """Mirrors _SPOT_ORIENTATIONS: read once at import, not per spot."""
    assert E._SPOT_SWELL_WINDOWS == EXPECTED, E._SPOT_SWELL_WINDOWS


def test_every_slug_in_the_file_resolves_to_exactly_one_roster_spot():
    """A slug that matches nothing is a silent no-op: the value is committed, reviewed and
    never applied. Four of the ten were originally proposed under a slug that does not
    exist (suicides / thousand-peaks / gas-chambers / table-tops)."""
    spots = json.loads((E.DEFAULT_ENRICHED_OUTPUT).read_text())
    counts = {}
    for s in spots:
        sl = E._slug_for(s.get("name"))
        counts[sl] = counts.get(sl, 0) + 1
    for slug, name in EXPECTED_NAMES.items():
        assert counts.get(slug) == 1, (slug, counts.get(slug))
        assert E._slug_for(name) == slug, (name, E._slug_for(name), slug)


def test_a_missing_file_degrades_to_no_overrides():
    saved = E.SPOT_SWELL_WINDOWS_FILE
    try:
        E.SPOT_SWELL_WINDOWS_FILE = saved.parent / "definitely_not_here.json"
        assert E._load_spot_swell_windows() == {}
    finally:
        E.SPOT_SWELL_WINDOWS_FILE = saved


def test_a_corrupt_file_degrades_to_no_overrides_instead_of_killing_the_run():
    saved = E.SPOT_SWELL_WINDOWS_FILE
    path = saved.parent / "_test_corrupt_windows.json"
    try:
        path.write_text("{not json")
        E.SPOT_SWELL_WINDOWS_FILE = path
        assert E._load_spot_swell_windows() == {}
    finally:
        E.SPOT_SWELL_WINDOWS_FILE = saved
        path.unlink(missing_ok=True)


def test_an_entry_with_no_degrees_is_skipped_not_fatal():
    saved = E.SPOT_SWELL_WINDOWS_FILE
    path = saved.parent / "_test_partial_windows.json"
    try:
        path.write_text(json.dumps({"windows": {
            "good": {"optimal_swell_dir": 200},
            "no-key": {"name": "x"},
            "not-a-number": {"optimal_swell_dir": "north"},
            "not-a-dict": 200,
        }}))
        E.SPOT_SWELL_WINDOWS_FILE = path
        assert E._load_spot_swell_windows() == {"good": 200.0}
    finally:
        E.SPOT_SWELL_WINDOWS_FILE = saved
        path.unlink(missing_ok=True)


def test_degrees_are_normalised_modulo_360():
    saved = E.SPOT_SWELL_WINDOWS_FILE
    path = saved.parent / "_test_mod_windows.json"
    try:
        # 380 % 360 = 20; -40 % 360 = 320 in Python.
        path.write_text(json.dumps({"windows": {
            "over": {"optimal_swell_dir": 380}, "under": {"optimal_swell_dir": -40}}}))
        E.SPOT_SWELL_WINDOWS_FILE = path
        assert E._load_spot_swell_windows() == {"over": 20.0, "under": 320.0}
    finally:
        E.SPOT_SWELL_WINDOWS_FILE = saved
        path.unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# 2 — each of the ten reaches the enriched record                              #
# --------------------------------------------------------------------------- #

def _enriched(name, **kw):
    """Run the Algo 2d stretch of _enrich_one with the raycast skipped."""
    return E._enrich_one(_spot(name, **kw), skip_raycast=True, prior_arcs=None)


def test_each_of_the_ten_values_reaches_the_enriched_record():
    for slug, deg in EXPECTED.items():
        out = _enriched(EXPECTED_NAMES[slug])
        assert out["optimal_swell_dir"] == deg, (slug, out["optimal_swell_dir"], deg)


def test_the_stamp_is_set_on_an_overridden_spot():
    out = _enriched("Honolua Bay")
    assert out["swell_window_source_override"] == "manual", out.get("swell_window_source_override")


def test_the_stamp_does_not_clobber_the_tier():
    """swell_window_source carries the nwps/cdip_mop tier and decides which forecast reader
    runs; the override must use its own field and leave that one alone."""
    out = _enriched("Honolua Bay")
    assert out["swell_window_source"] == "nwps", out["swell_window_source"]


def test_a_spot_not_in_the_file_is_untouched():
    out = _enriched("Steamer Lane", optimal_swell_dir=244, orientation_deg=128.0)
    assert out["optimal_swell_dir"] == 244, out["optimal_swell_dir"]
    assert "swell_window_source_override" not in out, out.get("swell_window_source_override")


def test_the_override_beats_llm_verification():
    """Three of the ten carry verification_confidence == "high", which makes _set refuse to
    write optimal_swell_dir. Algo 2d assigns directly, exactly as Algo 1b/1c do."""
    out = _enriched("Lovers Point", verification_confidence="high",
                    optimal_swell_dir=248, orientation_deg=29.0)
    assert out["optimal_swell_dir"] == 315.0, out["optimal_swell_dir"]


def test_the_override_beats_the_orientation_derived_fallback():
    """Algo 2b writes optimal_swell_dir from orientation whenever arcs are empty. Algo 2d
    runs after it, so the hand value wins rather than being overwritten by the fallback."""
    out = _enriched("Monterey Beach", swell_window_arcs=[], optimal_swell_dir=None,
                    orientation_deg=350.0, swell_window_source="orientation_derived")
    assert out["optimal_swell_dir"] == 320.0, out["optimal_swell_dir"]


def test_the_override_beats_the_tier_preserve_guard():
    """Algo 2c restores optimal_swell_dir from the PRIOR record when it would otherwise
    demote a better-tier spot. Algo 2d runs after it."""
    out = E._enrich_one(_spot("Crash Boat Beach", optimal_swell_dir=40,
                              orientation_deg=254.0, swell_window_source="nwps"),
                        skip_raycast=True, prior_arcs=None)
    assert out["optimal_swell_dir"] == 305.0, out["optimal_swell_dir"]


# --------------------------------------------------------------------------- #
# 3 — the three guarded writers                                                #
# --------------------------------------------------------------------------- #

def test_verify_spots_does_not_overwrite_an_overridden_optimal():
    spot = {"name": "Honolua Bay", "optimal_swell_dir": 350.0,
            "swell_window_source_override": "manual"}
    recs = {"Honolua Bay": {"confidence": "high", "optimal_swell_dir": 84,
                            "is_valid_surf_spot": True}}
    verify_merge([spot], recs)
    assert spot["optimal_swell_dir"] == 350.0, spot["optimal_swell_dir"]


def test_verify_spots_still_writes_an_unlocked_optimal():
    """The guard must be a lock on curated spots, not a blanket disable."""
    spot = {"name": "Some Spot", "optimal_swell_dir": 100}
    recs = {"Some Spot": {"confidence": "high", "optimal_swell_dir": 250,
                          "is_valid_surf_spot": True}}
    stats = verify_merge([spot], recs)
    assert spot["optimal_swell_dir"] == 250, spot["optimal_swell_dir"]
    assert stats["field_changes"]["optimal_swell_dir"] == 1, stats["field_changes"]


def test_verify_spots_does_not_null_an_overridden_optimal_on_an_orientation_change():
    """The second write site: an LLM orientation change on an orientation_derived spot
    clears the arcs AND nulls the optimal. Nulling a curated value hands the rating back to
    the orientation fallback."""
    spot = {"name": "Monterey Beach", "orientation_deg": 350, "optimal_swell_dir": 320.0,
            "swell_window_source": "orientation_derived",
            "swell_window_source_override": "manual"}
    recs = {"Monterey Beach": {"confidence": "high", "facing_direction_deg": 10,
                               "is_valid_surf_spot": True}}
    verify_merge([spot], recs)
    assert spot["optimal_swell_dir"] == 320.0, spot["optimal_swell_dir"]
    assert spot["swell_window_arcs"] == [], spot.get("swell_window_arcs")


def test_scrape_surf_forecast_does_not_overwrite_an_overridden_optimal():
    spot = {"name": "Spoils", "optimal_swell_dir": 40.0,
            "swell_window_source_override": "manual"}
    cache = {"Spoils": {"source_url": "https://example.invalid/x", "optimal_swell_dir": 292}}
    scrape_merge([spot], cache)
    assert spot["optimal_swell_dir"] == 40.0, spot["optimal_swell_dir"]


def test_scrape_surf_forecast_still_writes_an_unlocked_optimal():
    spot = {"name": "Some Spot", "optimal_swell_dir": 100}
    cache = {"Some Spot": {"source_url": "https://example.invalid/x", "optimal_swell_dir": 250}}
    stats = scrape_merge([spot], cache)
    assert spot["optimal_swell_dir"] == 250, spot["optimal_swell_dir"]
    assert stats["field_changes"]["optimal_swell_dir"] == 1, stats["field_changes"]


def test_apply_orientation_fixes_drops_the_optimal_for_an_overridden_slug():
    """THE WRITER THAT BYPASSES THE FILE. This script UPDATEs Supabase directly with
    optimal_swell_dir = orientation_deg, so an overridden value would be silently replaced
    in the live table with nothing disagreeing until the next db_import."""
    from pipeline import apply_orientation_fixes as A
    sent = {}

    class _Resp:
        data = [{"slug": "x"}]

    class _Tbl:
        def update(self, payload):
            sent.update(payload)
            return self

        def eq(self, *_a):
            return self

        def execute(self):
            return _Resp()

    class _Client:
        def table(self, _n):
            return _Tbl()

    assert A._update_supabase(_Client(), "honolua-bay", 278.0) is True
    assert "optimal_swell_dir" not in sent, sent
    assert sent["orientation_deg"] == 278.0, sent
    assert sent["offshore_wind_deg"] == 98.0, sent      # (278 + 180) % 360


def test_apply_orientation_fixes_still_writes_the_optimal_for_an_unlisted_slug():
    from pipeline import apply_orientation_fixes as A
    sent = {}

    class _Resp:
        data = [{"slug": "x"}]

    class _Tbl:
        def update(self, payload):
            sent.update(payload)
            return self

        def eq(self, *_a):
            return self

        def execute(self):
            return _Resp()

    class _Client:
        def table(self, _n):
            return _Tbl()

    assert A._update_supabase(_Client(), "steamer-lane", 128.0) is True
    assert sent["optimal_swell_dir"] == 128.0, sent


# --------------------------------------------------------------------------- #
# 4 — the recorded known limit                                                 #
# --------------------------------------------------------------------------- #

def test_the_unguarded_raycast_writer_is_recorded_as_a_known_limit():
    """sw1_raycast is NOT guarded in round one. The gap must be written down, naming the
    writer, or the next full raycast silently reverts all ten values."""
    doc = json.loads(SPOT_SWELL_WINDOWS_FILE.read_text())
    c = doc["_comment"]
    assert "sw1_raycast.py:353" in c, "the unguarded writer must be named by file and line"
    assert "KNOWN LIMIT" in c, c[:200]


def test_the_file_records_a_source_for_every_value():
    doc = json.loads(SPOT_SWELL_WINDOWS_FILE.read_text())
    for slug, rec in doc["windows"].items():
        assert rec.get("source"), slug
        assert rec.get("name"), slug


def test_the_weakest_value_is_flagged_as_such():
    """monterey-beach has no direct source and is carried on regional grounds."""
    doc = json.loads(SPOT_SWELL_WINDOWS_FILE.read_text())
    assert "WEAK" in doc["windows"]["monterey-beach"]["source"], \
        doc["windows"]["monterey-beach"]["source"]


def test_the_deep_water_versus_nearshore_frame_caveat_is_recorded():
    doc = json.loads(SPOT_SWELL_WINDOWS_FILE.read_text())
    c = doc["_comment"]
    assert "FRAME CAVEAT" in c, c[:200]
    assert "lovers-point" in c and "hapuna-beach" in c, "the caveat must name where it bites"



# --------------------------------------------------------------------------- #
# 5 — arc pruning (round two)                                                  #
# --------------------------------------------------------------------------- #
#
# THE RULE: drop any arc whose nearest PADDED sector edge is more than
# ARC_PRUNE_MAX_OFFSET_DEG (90.0) from the corrected optimal. The comparison is STRICTLY
# GREATER, so an arc at exactly 90.0 is KEPT. Prune only — nothing is authored, widened or
# re-centred, and an empty result is left empty.
#
# THE INPUT IS THE RAY-CAST, NOT THE ROSTER. Since enrich runs Algo 2d over the cast's arcs,
# the committed roster carries the arcs AFTER pruning; the arcs before pruning are the cast's
# own output, transcribed below from sw1-raycast run 13 (2026-10-15, the own-land rule checked
# before any re-entry; its raycast-only commit on the review branch sw1-raycast/full-13).
# monterey-beach is the exception: run 13 opened no window there at all, so sw1_raycast kept
# the stored one, which was already empty.
#
# Every expected list below is transcribed from those arcs, with the offsets computed by hand
# from the padded edges (every pad is 2: span - width = 4); none is read back from
# prune_arcs_to_optimal, which is the function under test.

RAYCAST_ARCS = {
    "honolua-bay": [{"min": 265, "max": 271, "span": 10}, {"min": 341, "max": 47, "span": 70}],
    "spoils": [{"min": 13, "max": 83, "span": 74}, {"min": 145, "max": 175, "span": 34}],
    "hapuna-beach": [{"min": 237, "max": 263, "span": 30}, {"min": 285, "max": 311, "span": 30}],
    "suicide-s": [{"min": 245, "max": 263, "span": 22}, {"min": 285, "max": 315, "span": 34}],
    "thousand-peaks-maui": [{"min": 149, "max": 159, "span": 14}, {"min": 205, "max": 251, "span": 50}],
    "crash-boat-beach": [{"min": 233, "max": 247, "span": 18}, {"min": 301, "max": 339, "span": 42}],
    "gas-chambers-aguadilla": [{"min": 233, "max": 247, "span": 18}, {"min": 301, "max": 351, "span": 54}],
    "table-tops-aguadilla": [{"min": 245, "max": 251, "span": 10}, {"min": 301, "max": 327, "span": 30}],
    "lovers-point": [{"min": 317, "max": 319, "span": 6}],
    "monterey-beach": [],
}

# {slug: (arcs kept, arcs dropped)} — written as [min, max] pairs.
EXPECTED_PRUNE = {
    # optimal 350. [265-271] padded [263-273]: |350-273| = 77 -> keep. [341-47] holds 350.
    # The east arc [77-91] that used to survive here is no longer cast at all.
    "honolua-bay": ([[265, 271], [341, 47]], []),
    # optimal 40 inside [11-85]. [145-175] padded [143-177]: 143-40 = 103 -> dropped. (The
    # cross-peninsula arcs [209-243], [261-279] and [297-315] of run 11 are no longer cast.)
    "spoils": ([[13, 83]], [[145, 175]]),
    # optimal 280 sits between the two arcs: 280-265 = 15, 283-280 = 3. Both kept.
    "hapuna-beach": ([[237, 263], [285, 311]], []),
    # optimal 292 inside [283-317]; [243-265]: 292-265 = 27. Both kept.
    "suicide-s": ([[245, 263], [285, 315]], []),
    # optimal 200: [147-161]: 200-161 = 39; [203-253]: 203-200 = 3. Both kept.
    "thousand-peaks-maui": ([[149, 159], [205, 251]], []),
    # optimal 305 inside [299-341]; [231-249]: 305-249 = 56. Both kept.
    "crash-boat-beach": ([[233, 247], [301, 339]], []),
    # optimal 315 inside [299-353]; [231-249]: 315-249 = 66. Both kept.
    "gas-chambers-aguadilla": ([[233, 247], [301, 351]], []),
    # optimal 350: [243-253]: 350-253 = 97 -> drop. [299-329]: 350-329 = 21 -> keep.
    "table-tops-aguadilla": ([[301, 327]], [[245, 251]]),
    # optimal 315 on the padded edge of [315-321]: inside, 0 -> keep.
    "lovers-point": ([[317, 319]], []),
    # no window to prune: run 13 opened none (88 of 90 rays blocked).
    "monterey-beach": ([], []),
}


def _pairs(arcs):
    return [[a["min"], a["max"]] for a in arcs]


def _roster():
    return {E._slug_for(s.get("name")): s
            for s in json.loads(E.DEFAULT_ENRICHED_OUTPUT.read_text())}


def test_the_pruning_rule_keeps_and_drops_exactly_these_arcs():
    for slug, (keep, drop) in EXPECTED_PRUNE.items():
        arcs = RAYCAST_ARCS[slug]
        got = E.prune_arcs_to_optimal(arcs, EXPECTED[slug])
        assert _pairs(got) == keep, (slug, _pairs(got), keep)
        assert _pairs([a for a in arcs if a not in got]) == drop, slug


def test_pruning_reaches_the_enriched_record():
    """The rule must run inside Algo 2d, not merely exist as a function."""
    roster = _roster()
    for slug, (keep, _drop) in EXPECTED_PRUNE.items():
        s = roster[slug]
        arcs = RAYCAST_ARCS[slug]
        out = E._enrich_one(
            _spot(s["name"], swell_window_arcs=arcs, optimal_swell_dir=s["optimal_swell_dir"],
                  orientation_deg=s["orientation_deg"], swell_window_source="nwps"),
            skip_raycast=True, prior_arcs={s["name"]: {
                "swell_window_arcs": arcs,
                "optimal_swell_dir": s["optimal_swell_dir"],
                "swell_window_source": "nwps"}})
        assert _pairs(out["swell_window_arcs"]) == keep, (slug, _pairs(out["swell_window_arcs"]))


def test_every_override_is_applied_in_the_committed_roster():
    """THE CHECK THAT WAS MISSING. From 2026-09-01 to the run-11 recompute none of the ten
    values reached spots_enriched.json: enrich, the only thing that applies them, had not been
    run and committed since, and every test above runs enrich in memory. This reads the
    committed roster itself and fails for any entry in the file that is not applied there:
    the researched degrees, the override stamp, and the arcs exactly as pruned."""
    roster = _roster()
    doc = json.loads(SPOT_SWELL_WINDOWS_FILE.read_text())
    assert set(doc["windows"]) == set(EXPECTED), sorted(set(doc["windows"]) ^ set(EXPECTED))
    for slug, rec in doc["windows"].items():
        s = roster[slug]
        assert s.get("optimal_swell_dir") == float(rec["optimal_swell_dir"]), \
            (slug, s.get("optimal_swell_dir"), rec["optimal_swell_dir"])
        assert s.get("optimal_swell_dir") == EXPECTED[slug], slug
        assert s.get("swell_window_source_override") == "manual", slug
        assert _pairs(s.get("swell_window_arcs") or []) == EXPECTED_PRUNE[slug][0], \
            (slug, _pairs(s.get("swell_window_arcs") or []))


def test_a_spot_with_no_override_keeps_every_arc():
    """Pruning is scoped to overridden spots. A spot absent from the file must come through
    with its window byte-identical, however far its arcs sit from its optimal."""
    far = [{"min": 100, "max": 140, "span": 44}, {"min": 200, "max": 240, "span": 44}]
    out = E._enrich_one(
        _spot("Steamer Lane", swell_window_arcs=far, optimal_swell_dir=244,
              orientation_deg=128.0),
        skip_raycast=True,
        prior_arcs={"Steamer Lane": {"swell_window_arcs": far, "optimal_swell_dir": 244,
                                     "swell_window_source": "nwps"}})
    assert out["swell_window_arcs"] == far, out["swell_window_arcs"]
    assert "swell_window_source_override" not in out


def test_the_ninety_degree_boundary_is_inclusive_of_keeping():
    """STRICTLY GREATER drops. An arc whose nearest padded edge is at exactly 90.0 is KEPT;
    one at 90.5 is dropped. Built with pad 0 (span == max - min) so the edge is the bound."""
    at_90 = {"min": 100, "max": 140, "span": 40}     # pad 0; |10 - 100| = 90 exactly
    past_90 = {"min": 101, "max": 140, "span": 39}   # pad 0; |10 - 101| = 91
    assert E.arc_offset_from(10, at_90) == 90.0, E.arc_offset_from(10, at_90)
    assert E.prune_arcs_to_optimal([at_90], 10) == [at_90]
    assert E.arc_offset_from(10, past_90) == 91.0, E.arc_offset_from(10, past_90)
    assert E.prune_arcs_to_optimal([past_90], 10) == []


def test_an_empty_result_is_left_empty_and_not_backfilled():
    """Algo 2b's orientation-derived fallback would rebuild a window centred on
    orientation_deg. It runs BEFORE Algo 2d, so a window pruned to empty must stay empty
    rather than being re-authored — the one thing this channel refuses to do."""
    far = [{"min": 100, "max": 140, "span": 44}]     # 175 deg from Honolua's optimal of 350
    assert E.prune_arcs_to_optimal(far, 350) == []
    out = E._enrich_one(
        _spot("Honolua Bay", swell_window_arcs=far, orientation_deg=278.0,
              swell_window_source="nwps"),
        skip_raycast=True,
        prior_arcs={"Honolua Bay": {"swell_window_arcs": far, "optimal_swell_dir": 84,
                                    "swell_window_source": "nwps"}})
    assert out["swell_window_arcs"] == [], out["swell_window_arcs"]
    assert out["optimal_swell_dir"] == 350.0, out["optimal_swell_dir"]


def test_empty_arcs_leave_directional_gain_on_the_optimal_branch():
    """Why an empty window is survivable at all: with no arcs, directional_gain takes its
    first branch — a plain cos²(Δ/2) peak about the optimal, floored at 0.25 — instead of
    returning 0. Hand-computed: at dp == optimal, cos²(0) = 1.0; at dp 80 against optimal
    350 the signed difference is ((80-350+540) mod 360) - 180 = 90, and cos²(45°) = 0.5.

    NOTE, because it bears on the risk: since the own-land recompute ONE spot runs this path
    in production — monterey-beach, whose cast opens no window (see the next test)."""
    from pipeline.interpret import directional_gain
    assert directional_gain(350.0, [], 350, 278.0) == 1.0
    # cos(radians(45))**2 is 0.5000000000000001, not 0.5 — round rather than loosen the value.
    assert round(directional_gain(80.0, [], 350, 278.0), 12) == 0.5
    assert directional_gain(170.0, [], 350, 278.0) == 0.25      # cos²(90°) = 0, floored


def test_monterey_beach_is_the_one_roster_spot_with_empty_arcs():
    """Pins the claim above, so a second empty window shows up loudly rather than quietly.
    The run-13 cast blocks 88 of monterey-beach's 90 rays and the two open ones shrink away."""
    spots = json.loads(E.DEFAULT_ENRICHED_OUTPUT.read_text())
    assert [s["name"] for s in spots if not (s.get("swell_window_arcs") or [])] == ["Monterey Beach"]


def test_pruning_never_changes_the_gain_at_the_corrected_optimal():
    """Removing arcs cannot bring a bearing INSIDE a window, so the gain at the corrected
    optimal is untouched for all ten — including the three that stay on the 0.40 rung
    (hapuna-beach, thousand-peaks-maui, table-tops-aguadilla: optimal outside every arc,
    within 45 degrees of an edge). monterey-beach has no arcs before or after, so both are
    the no-arcs branch's cos²(0) = 1.0. Pinned so that a future change claiming to lift the
    three has to face this test."""
    from pipeline.interpret import directional_gain
    roster = _roster()
    for slug, deg in EXPECTED.items():
        o = roster[slug]["orientation_deg"]
        before = RAYCAST_ARCS[slug]
        after = E.prune_arcs_to_optimal(before, deg)
        assert (directional_gain(float(deg), before, deg, o)
                == directional_gain(float(deg), after, deg, o)), slug
    for slug in ("hapuna-beach", "thousand-peaks-maui", "table-tops-aguadilla"):
        o = roster[slug]["orientation_deg"]
        deg = EXPECTED[slug]
        assert directional_gain(float(deg), RAYCAST_ARCS[slug], deg, o) == 0.40, slug
    o = roster["monterey-beach"]["orientation_deg"]
    assert directional_gain(320.0, [], 320, o) == 1.0


def test_pruning_can_raise_the_gain_at_some_bearings():
    """NOT a bug, and not monotonic protection: a bearing that sat inside a dropped arc
    scored the in-window floor of 0.25; once that arc is gone it is graded by the ladder
    against the KEPT arcs, which returns 0.40 within 45 degrees of a kept edge.

    Spoils, corrected optimal 40, on its RUN-11 arcs (the run-13 cast no longer opens the
    arcs this needs, and on run-13 arcs none of the ten has such a bearing — but the rule's
    behaviour is the point). A swell from 270 sits inside [261-279] (padded [259-281]),
    which is dropped. Before: in-window, signed difference ((270-40+540) mod 360) - 180 =
    -130, cos²(-65°) = 0.179, floored to 0.25. After: outside; the nearest kept edge is
    [297-315]'s padded 295, |295-270| = 25 < 45, so it lands on the 0.40 rung — HIGHER than
    the 0.25 it scored before. Hand-computed; pinned so the trade-off is visible."""
    from pipeline.interpret import directional_gain
    s = _roster()["spoils"]
    before = [{"min": 13, "max": 83, "span": 74}, {"min": 145, "max": 175, "span": 34},
              {"min": 209, "max": 243, "span": 38}, {"min": 261, "max": 279, "span": 22},
              {"min": 297, "max": 315, "span": 22}]
    after = E.prune_arcs_to_optimal(before, 40)
    assert directional_gain(270.0, before, 40, s["orientation_deg"]) == 0.25
    assert directional_gain(270.0, after, 40, s["orientation_deg"]) == 0.40


def test_honolua_east_swell_is_no_longer_in_a_window():
    """THE CASE PRUNING COULD NOT FIX, now fixed upstream. Honolua Bay's [77-91] arc let an
    80-degree east swell, blocked by the whole of West Maui, rate at all; at 85.0 degrees from
    350 it survived the 90-degree rule. The own-land ray-cast no longer casts it. The
    committed arcs are [265-271] and [341-47] (padded [263-273] and [339-49]); 80 is in
    neither, and its nearest padded edge is 49, |80-49| = 31 < 45: the 0.40 rung. It scored
    0.999 in production on the old arcs with the old optimal 84."""
    from pipeline.interpret import directional_gain, in_any_arc
    s = _roster()["honolua-bay"]
    arcs = s["swell_window_arcs"]
    assert _pairs(arcs) == [[265, 271], [341, 47]], _pairs(arcs)
    assert in_any_arc(80.0, arcs) is False
    assert directional_gain(80.0, arcs, 350, 278.0) == 0.40


def test_the_file_records_the_pruning_result_per_spot():
    """Every entry must show what the rule did — the rule text, the arcs before, and the
    kept/dropped split — so a reader can audit it without re-running enrich."""
    doc = json.loads(SPOT_SWELL_WINDOWS_FILE.read_text())
    for slug, rec in doc["windows"].items():
        p = rec.get("arcs_pruned")
        assert p, slug
        for key in ("rule", "before", "kept", "dropped"):
            assert key in p, (slug, key)
        assert "90.0" in p["rule"], (slug, p["rule"])
        assert len(p["kept"]) + len(p["dropped"]) == len(p["before"]), slug


def test_the_recorded_prune_record_matches_what_the_rule_actually_does():
    """THE POINT OF RECORDING IT: the file must describe the rule's real output, not a
    hand-written claim about it. Recomputes kept/dropped and every offset from the run-13
    arcs and compares against the text in the file."""
    doc = json.loads(SPOT_SWELL_WINDOWS_FILE.read_text())
    for slug, rec in doc["windows"].items():
        arcs, deg = RAYCAST_ARCS[slug], rec["optimal_swell_dir"]
        kept = E.prune_arcs_to_optimal(arcs, deg)
        kept_ids = [id(a) for a in kept]
        exp_keep = [f"[{a['min']}-{a['max']}] @ {E.arc_offset_from(deg, a):.1f}deg"
                    for a in arcs if id(a) in kept_ids]
        exp_drop = [f"[{a['min']}-{a['max']}] @ {E.arc_offset_from(deg, a):.1f}deg"
                    for a in arcs if id(a) not in kept_ids]
        assert rec["arcs_pruned"]["kept"] == exp_keep, (slug, rec["arcs_pruned"]["kept"], exp_keep)
        assert rec["arcs_pruned"]["dropped"] == exp_drop, (slug, rec["arcs_pruned"]["dropped"], exp_drop)
        assert rec["arcs_pruned"]["before"] == [f"[{a['min']}-{a['max']}]" for a in arcs], slug


def test_an_arc_containing_the_optimal_always_measures_zero():
    """Containment is short-circuited to 0 REGARDLESS of how far the edges are, and that is
    load-bearing for a wide arc. [0-300] with span 304 has pad 2, so its padded edges are 358
    and 302; the optimal 150 sits inside it but is 152 degrees from the nearer edge. Without
    the short-circuit the rule would drop the very arc the optimal lives in.

    The committed roster cannot expose this — its five containing arcs are all narrow enough
    that an edge stays inside 90 — so it is pinned directly on the primitive."""
    wide = {"min": 0, "max": 300, "span": 304}
    assert E.arc_offset_from(150, wide) == 0.0, E.arc_offset_from(150, wide)
    assert E.prune_arcs_to_optimal([wide], 150) == [wide]


def test_prune_returns_a_new_list_and_never_mutates_its_input():
    """enrich does `enriched = dict(spot)` — a SHALLOW copy, so enriched["swell_window_arcs"]
    is the same list object as spot["swell_window_arcs"]. An in-place prune would therefore
    reach back and rewrite the caller's own spot record, which in a real run is an element of
    the loaded roster. Asserted on identity, not just equality, because an aliased result
    compares equal to itself and hides the fault."""
    src = [{"min": 100, "max": 140, "span": 44}, {"min": 340, "max": 20, "span": 44}]
    snapshot = [dict(a) for a in src]
    out = E.prune_arcs_to_optimal(src, 350)
    assert out is not src, "must return a new list, not the input"
    assert src == snapshot, ("input was mutated", src, snapshot)
    assert len(src) == 2 and len(out) == 1, (len(src), len(out))


def test_algo_2d_does_not_rewrite_the_callers_spot_arcs():
    """The end-to-end form of the above: _enrich_one must not reach back through the shallow
    copy and prune the dict it was handed."""
    arcs = [{"min": 161, "max": 211, "span": 54}, {"min": 341, "max": 47, "span": 70}]
    spot = _spot("Honolua Bay", swell_window_arcs=arcs, swell_window_source="nwps")
    out = E._enrich_one(spot, skip_raycast=True, prior_arcs={"Honolua Bay": {
        "swell_window_arcs": arcs, "optimal_swell_dir": 84, "swell_window_source": "nwps"}})
    assert _pairs(out["swell_window_arcs"]) == [[341, 47]], _pairs(out["swell_window_arcs"])
    assert _pairs(spot["swell_window_arcs"]) == [[161, 211], [341, 47]], \
        ("the caller's spot was mutated", _pairs(spot["swell_window_arcs"]))


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\ntest_optimal_swell_dir_override: {len(fns)} PASS")


if __name__ == "__main__":
    _run_all()
