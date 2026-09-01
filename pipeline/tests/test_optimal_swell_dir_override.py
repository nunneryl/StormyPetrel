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
# Every expected list below is transcribed from the arcs committed in spots_enriched.json
# and the offsets computed by hand from the padded edges; none is read back from
# prune_arcs_to_optimal, which is the function under test.

# {slug: (arcs kept, arcs dropped)} — written as [min, max] pairs.
EXPECTED_PRUNE = {
    # optimal 350. [161-211] padded [159-213]: |350-213| = 137 -> drop. [77-91] padded
    # [75-93]: |350-75| = 85 -> KEPT, which is why the east-swell arc survives.
    "honolua-bay": ([[77, 91], [265, 271], [341, 47]], [[161, 211]]),
    # optimal 40. [297-315] padded [295-317]: |40-317| = 83 -> keep.
    "spoils": ([[13, 83], [297, 315]], [[145, 175], [209, 243], [261, 279]]),
    # optimal 280. [9-99] padded [7-101]: |280-7| = 87 -> keep. Nothing is dropped.
    "hapuna-beach": ([[9, 99], [117, 203], [237, 263], [285, 311]], []),
    "suicide-s": ([[237, 265], [283, 315]], [[65, 99], [113, 175]]),
    "thousand-peaks-maui": ([[149, 159], [205, 251]], [[33, 83], [349, 15]]),
    "crash-boat-beach": ([[233, 247], [317, 47]], [[65, 75], [97, 131], [153, 159]]),
    "gas-chambers-aguadilla": ([[233, 247], [317, 79]], [[97, 123]]),
    # [245-251] padded [243-253]: |350-253| = 97 -> drop, the narrowest drop in the set.
    "table-tops-aguadilla": ([[69, 75], [301, 327], [345, 51]], [[93, 115], [245, 251]]),
    # [213-219] padded [211-221]: |315-221| = 94 -> drop, the narrowest margin over the limit.
    "lovers-point": ([[253, 319]], [[165, 175], [213, 219]]),
    "monterey-beach": ([[225, 247], [265, 303]], [[105, 111], [129, 195]]),
}


def _pairs(arcs):
    return [[a["min"], a["max"]] for a in arcs]


def _roster():
    return {E._slug_for(s.get("name")): s
            for s in json.loads(E.DEFAULT_ENRICHED_OUTPUT.read_text())}


def test_the_pruning_rule_keeps_and_drops_exactly_these_arcs():
    roster = _roster()
    for slug, (keep, drop) in EXPECTED_PRUNE.items():
        arcs = roster[slug]["swell_window_arcs"]
        got = E.prune_arcs_to_optimal(arcs, EXPECTED[slug])
        assert _pairs(got) == keep, (slug, _pairs(got), keep)
        assert _pairs([a for a in arcs if a not in got]) == drop, slug


def test_pruning_reaches_the_enriched_record():
    """The rule must run inside Algo 2d, not merely exist as a function."""
    roster = _roster()
    for slug, (keep, _drop) in EXPECTED_PRUNE.items():
        s = roster[slug]
        out = E._enrich_one(
            _spot(s["name"], swell_window_arcs=s["swell_window_arcs"],
                  optimal_swell_dir=s["optimal_swell_dir"],
                  orientation_deg=s["orientation_deg"], swell_window_source="nwps"),
            skip_raycast=True, prior_arcs={s["name"]: {
                "swell_window_arcs": s["swell_window_arcs"],
                "optimal_swell_dir": s["optimal_swell_dir"],
                "swell_window_source": "nwps"}})
        assert _pairs(out["swell_window_arcs"]) == keep, (slug, _pairs(out["swell_window_arcs"]))


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

    NOTE, because it bears on the risk: NO spot in the committed roster currently has empty
    arcs, so a spot pruned to empty would be the first to run this path in production. None
    of the ten prunes to empty today."""
    from pipeline.interpret import directional_gain
    assert directional_gain(350.0, [], 350, 278.0) == 1.0
    # cos(radians(45))**2 is 0.5000000000000001, not 0.5 — round rather than loosen the value.
    assert round(directional_gain(80.0, [], 350, 278.0), 12) == 0.5
    assert directional_gain(170.0, [], 350, 278.0) == 0.25      # cos²(90°) = 0, floored


def test_no_roster_spot_currently_runs_with_empty_arcs():
    """Pins the claim above, so it stops being true loudly rather than quietly."""
    spots = json.loads(E.DEFAULT_ENRICHED_OUTPUT.read_text())
    assert [s["name"] for s in spots if not (s.get("swell_window_arcs") or [])] == []


def test_pruning_never_changes_the_gain_at_the_corrected_optimal():
    """Removing arcs cannot bring a bearing INSIDE a window, so the headline number is
    untouched for all ten — including the four that stay on the 0.40 rung. Pinned so that a
    future change claiming to lift them has to face this test."""
    from pipeline.interpret import directional_gain
    roster = _roster()
    for slug, deg in EXPECTED.items():
        s = roster[slug]
        before = s["swell_window_arcs"]
        after = E.prune_arcs_to_optimal(before, deg)
        o = s["orientation_deg"]
        assert (directional_gain(float(deg), before, deg, o)
                == directional_gain(float(deg), after, deg, o)), slug


def test_pruning_can_raise_the_gain_at_some_bearings():
    """NOT a bug, and not monotonic protection: a bearing that sat inside a dropped arc
    scored the in-window floor of 0.25; once that arc is gone it is graded by the ladder
    against the KEPT arcs, which returns 0.40 within 45 degrees of a kept edge.

    Crash Boat Beach, corrected optimal 305. A swell from 70 sits inside [65-75], which is
    dropped at 118 degrees. Afterwards its nearest kept edge is [317-47]'s padded 49, and
    |70-49| = 21 < 45, so it lands on the 0.40 rung — HIGHER than the 0.25 it scored before.
    Hand-computed; pinned so the trade-off is visible rather than discovered later."""
    from pipeline.interpret import directional_gain
    s = _roster()["crash-boat-beach"]
    before = s["swell_window_arcs"]
    after = E.prune_arcs_to_optimal(before, 305)
    assert directional_gain(70.0, before, 305, s["orientation_deg"]) == 0.25
    assert directional_gain(70.0, after, 305, s["orientation_deg"]) == 0.40


def test_honolua_east_swell_arc_survives_the_ninety_degree_rule():
    """THE CASE THE RULE DOES NOT FIX, pinned so nobody assumes it does. [77-91] is the arc
    that lets an 80-degree east swell — blocked by the whole of Maui — rate at all. Its
    nearest padded edge is 75, and |350-75| = 85, inside a 90-degree limit, so it is KEPT.
    An 80-degree swell scores 0.999 in production, 0.500 after the optimal moves to 350, and
    0.500 after pruning: the improvement is entirely the optimal's. Any limit below 85 would
    drop it."""
    from pipeline.interpret import directional_gain, in_any_arc
    s = _roster()["honolua-bay"]
    before = s["swell_window_arcs"]
    after = E.prune_arcs_to_optimal(before, 350)
    east = [a for a in after if [a["min"], a["max"]] == [77, 91]]
    assert len(east) == 1, "the east arc must still be present under a 90-degree limit"
    assert E.arc_offset_from(350, east[0]) == 85.0, E.arc_offset_from(350, east[0])
    assert in_any_arc(80.0, after) is True
    # cos²(2°) = 0.9987820251299122, cos²(45°) = 0.5000000000000001 — rounded, not loosened.
    assert round(directional_gain(80.0, before, 84, 278.0), 4) == 0.9988   # production today
    assert round(directional_gain(80.0, before, 350, 278.0), 12) == 0.5    # override only
    assert round(directional_gain(80.0, after, 350, 278.0), 12) == 0.5     # + pruning


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
    hand-written claim about it. Recomputes kept/dropped and every offset from the committed
    arcs and compares against the text in the file."""
    doc = json.loads(SPOT_SWELL_WINDOWS_FILE.read_text())
    roster = _roster()
    for slug, rec in doc["windows"].items():
        s = roster[slug]
        arcs, deg = s["swell_window_arcs"], rec["optimal_swell_dir"]
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
