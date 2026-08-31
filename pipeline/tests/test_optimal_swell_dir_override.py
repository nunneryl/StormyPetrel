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


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\ntest_optimal_swell_dir_override: {len(fns)} PASS")


if __name__ == "__main__":
    _run_all()
