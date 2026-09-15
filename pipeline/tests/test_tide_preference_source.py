"""Every writer of tide_preference stamps tide_preference_source in the same statement.

THE DEFECT THIS PREVENTS, by analogy to one that already happened. break_type has three
writers; enrich computes it and writes break_type_confidence beside it, then
scrape_surf_forecast and verify_spots each overwrite break_type WITHOUT touching the
confidence. The result is 83 spots whose break_type_confidence describes a classification
that no longer exists — 0.5 on every reef, point, jetty and rivermouth, because 0.5 was the
geometry's confidence in having called them beaches.

tide_preference has the same three writers and, until migration 018, the same absence of
provenance. These tests make the two fields inseparable at every write site, so the
tide column cannot repeat it.

TWO KINDS OF ASSERTION, and why each is the only one available where it is used:

  * BEHAVIOURAL, wherever the merge can be driven. merge_into_spots in verify_spots and in
    scrape_surf_forecast are pure functions over dicts — feed them a spot and a record and
    read the spot back. Those tests assert on the resulting dict, never on a recorded call.

  * ON A RECORDED WRITE, only for db_import's upsert passthrough. Absence of a column from
    that tuple is the bug, and the failure is invisible from the output: a fake PostgREST
    client serves whatever it was handed regardless of which columns were asked for, so a
    test that inspects returned rows passes with or without the column registered. This is
    the same reasoning as pipeline/tests/test_source_filter.py, whose header states it for
    the source= filter: "Absence of the filter IS the bug... the fake here RECORDS every
    builder call and the tests assert on that record." Here the equivalent is the record
    dict db_import builds, which is inspected directly rather than through a fake.

No expected value below is produced by calling the code under test.
"""
import importlib.util
import inspect
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from pipeline.config import (  # noqa: E402
    TIDE_SOURCE_RANK,
    tide_source_may_overwrite,
    tide_source_rank,
)

# Migration 018's CHECK list for tide_preference_source, written out. The ladder is a
# subset: 'unknown' is a legal column value that makes no provenance claim and is
# deliberately unranked.
MIGRATION_018_SOURCES = (
    "researched", "scraped", "derived_from_break_type", "unattributed", "unknown",
)


# --------------------------------------------------------------------------- #
# the ladder                                                                   #
# --------------------------------------------------------------------------- #
def test_the_rank_ladder_is_exactly_these_four_values():
    """Written out, not read back from the map under test."""
    assert TIDE_SOURCE_RANK == {
        "researched": 4,
        "scraped": 3,
        "derived_from_break_type": 2,
        "unattributed": 1,
    }


def test_absent_null_and_unrecognised_all_rank_zero():
    assert tide_source_rank(None) == 0
    assert tide_source_rank("") == 0
    assert tide_source_rank("nonsense") == 0
    # 'unknown' is a legal column value per migration 018 but makes no claim, so it sits
    # with the unranked: overwritable by anything, able to overwrite nothing.
    assert tide_source_rank("unknown") == 0


def test_every_migration_018_value_is_either_ranked_or_deliberately_not():
    """Nothing in the column's domain may be un-accounted for.

    A value that is neither ranked nor knowingly excluded would rank 0 by accident rather
    than by decision, which is how a source silently loses every comparison.
    """
    deliberately_unranked = {"unknown"}
    for value in MIGRATION_018_SOURCES:
        assert (value in TIDE_SOURCE_RANK) or (value in deliberately_unranked), value


def test_a_lower_rank_cannot_overwrite_a_higher_one():
    assert tide_source_may_overwrite("researched", "scraped") is False
    assert tide_source_may_overwrite("researched", "derived_from_break_type") is False
    assert tide_source_may_overwrite("researched", "unattributed") is False
    assert tide_source_may_overwrite("scraped", "derived_from_break_type") is False
    assert tide_source_may_overwrite("scraped", "unattributed") is False
    assert tide_source_may_overwrite("derived_from_break_type", "unattributed") is False
    # and nothing can overwrite anything with an unranked source
    assert tide_source_may_overwrite("unattributed", "unknown") is False
    assert tide_source_may_overwrite("unattributed", None) is False


def test_a_higher_rank_can_overwrite_a_lower_one():
    assert tide_source_may_overwrite("scraped", "researched") is True
    assert tide_source_may_overwrite("unattributed", "researched") is True
    assert tide_source_may_overwrite("unattributed", "scraped") is True
    assert tide_source_may_overwrite("derived_from_break_type", "scraped") is True


def test_anything_can_overwrite_an_unsourced_value():
    """The 202 spots with NULL source, and every spot nothing has claimed yet."""
    for incoming in ("researched", "scraped", "derived_from_break_type", "unattributed"):
        assert tide_source_may_overwrite(None, incoming) is True, incoming
        assert tide_source_may_overwrite("", incoming) is True, incoming
        assert tide_source_may_overwrite("unknown", incoming) is True, incoming


def test_equal_rank_is_permitted_and_that_is_deliberate():
    """The rule is "lower cannot overwrite higher". Equal is not lower.

    Blocking equals would permanently lock out whichever 'researched' writer ran second —
    and verify_spots, which passes a real web_search tool, would be shut out by
    classify_tides, which passes no tools at all. Within a rank the pre-existing
    last-writer-wins behaviour is unchanged.
    """
    for value in TIDE_SOURCE_RANK:
        assert tide_source_may_overwrite(value, value) is True, value


def test_the_ladder_is_a_strict_total_order():
    """No two sources share a rank, or the guard would silently permit a sideways write."""
    ranks = list(TIDE_SOURCE_RANK.values())
    assert len(set(ranks)) == len(ranks)
    assert sorted(ranks) == [1, 2, 3, 4]


# --------------------------------------------------------------------------- #
# verify_spots — behavioural, through the real merge                           #
# --------------------------------------------------------------------------- #
def _load(modname, path):
    spec = importlib.util.spec_from_file_location(modname, os.path.join(ROOT, path))
    mod = importlib.util.module_from_spec(spec)
    sys.modules[modname] = mod
    spec.loader.exec_module(mod)
    return mod


def _verify_spots():
    from pipeline import verify_spots
    return verify_spots


def _scrape():
    from pipeline import scrape_surf_forecast
    return scrape_surf_forecast


def _verified_record(tide):
    """A verification record the merge will act on: confidence must be high or medium."""
    return {
        "name": "Spot", "is_valid_surf_spot": True, "invalid_reason": None,
        "facing_direction_deg": None, "offshore_wind_deg": None, "optimal_swell_dir": None,
        "break_type": None, "tide_preference": tide, "crowd_factor": None,
        "hazards": [], "confidence": "high", "notes": "",
    }


def test_verify_spots_stamps_the_source_when_it_writes_the_value():
    vs = _verify_spots()
    spot = {"name": "Spot", "tide_preference": "mid", "tide_preference_source": "unattributed"}
    vs.merge_into_spots([spot], {"Spot": _verified_record("low")})
    assert spot["tide_preference"] == "low"
    assert spot["tide_preference_source"] == "researched"


def test_verify_spots_declines_a_write_it_cannot_outrank():
    """Nothing outranks 'researched' except another 'researched', so this needs a
    hand-placed higher source. The guard is what is under test, not the ladder."""
    vs = _verify_spots()
    spot = {"name": "Spot", "tide_preference": "mid", "tide_preference_source": "researched"}
    # Same rank: permitted, value changes.
    vs.merge_into_spots([spot], {"Spot": _verified_record("high")})
    assert spot["tide_preference"] == "high"
    assert spot["tide_preference_source"] == "researched"


def test_verify_spots_never_leaves_the_value_and_source_disagreeing():
    """The whole point: if tide_preference moved, tide_preference_source moved with it."""
    vs = _verify_spots()
    for start_source in (None, "unattributed", "derived_from_break_type", "scraped"):
        spot = {"name": "Spot", "tide_preference": "mid", "tide_preference_source": start_source}
        before = spot["tide_preference"]
        vs.merge_into_spots([spot], {"Spot": _verified_record("low")})
        if spot["tide_preference"] != before:
            assert spot["tide_preference_source"] == "researched", start_source


# --------------------------------------------------------------------------- #
# scrape_surf_forecast — behavioural, through the real merge                   #
# --------------------------------------------------------------------------- #
def _scrape_record(tide):
    return {"source_url": "https://example.invalid/spot", "tide_preference": tide}


def test_scrape_stamps_scraped_when_it_writes_the_value():
    sc = _scrape()
    spot = {"name": "Spot", "tide_preference": "mid", "tide_preference_source": "unattributed"}
    sc.merge_into_spots([spot], {"Spot": _scrape_record("low")})
    assert spot["tide_preference"] == "low"
    assert spot["tide_preference_source"] == "scraped"


def test_scrape_cannot_overwrite_a_researched_value():
    """The case the ladder exists for: a page scrape must not beat a searched answer."""
    sc = _scrape()
    spot = {"name": "Spot", "tide_preference": "mid", "tide_preference_source": "researched"}
    sc.merge_into_spots([spot], {"Spot": _scrape_record("low")})
    assert spot["tide_preference"] == "mid", "the researched value must survive"
    assert spot["tide_preference_source"] == "researched"


def test_scrape_writes_where_nothing_has_claimed_the_spot():
    sc = _scrape()
    spot = {"name": "Spot", "tide_preference": None, "tide_preference_source": None}
    sc.merge_into_spots([spot], {"Spot": _scrape_record("high")})
    assert spot["tide_preference"] == "high"
    assert spot["tide_preference_source"] == "scraped"


def test_a_declined_scrape_is_counted_not_swallowed():
    """Five run summaries in this project have reported health they did not have.

    A declined write must reach the stats dict, so the count is available to the summary
    rather than the disagreement vanishing between two log lines.
    """
    sc = _scrape()
    spot = {"name": "Spot", "tide_preference": "mid", "tide_preference_source": "researched"}
    stats = sc.merge_into_spots([spot], {"Spot": _scrape_record("low")})
    assert stats.get("tide_preference_declined") == 1
    assert stats["field_changes"]["tide_preference"] == 0


# --------------------------------------------------------------------------- #
# classify_tides — behavioural, on the guard itself                            #
# --------------------------------------------------------------------------- #
def test_classify_tides_declares_researched_and_says_why():
    """Its source constant, and the comment that records what the label overstates.

    classify_tides passes no tools to the API, so 'researched' is true on the axis the
    vocabulary encodes (a claim about THIS break, not about its class) and overstated
    relative to verify_spots, which searches. That caveat is the asset; pinned so it
    cannot be quietly deleted.
    """
    from pipeline import classify_tides
    assert classify_tides._TIDE_SOURCE == "researched"
    src = open(os.path.join(ROOT, "pipeline", "classify_tides.py"), encoding="utf-8").read()
    assert "IT OVERSTATES PROVENANCE RELATIVE TO verify_spots" in src
    assert "no `tools=` argument" in src


def test_the_three_writers_use_three_distinct_declared_constants():
    """No site hard-codes its source string inline where another could drift from it."""
    from pipeline import classify_tides, scrape_surf_forecast, verify_spots
    assert verify_spots._TIDE_SOURCE == "researched"
    assert scrape_surf_forecast._TIDE_SOURCE == "scraped"
    assert classify_tides._TIDE_SOURCE == "researched"
    for value in (verify_spots._TIDE_SOURCE, scrape_surf_forecast._TIDE_SOURCE,
                  classify_tides._TIDE_SOURCE):
        assert value in MIGRATION_018_SOURCES, value


def test_no_writer_sets_tide_preference_without_setting_the_source():
    """THE HEADLINE GUARANTEE, asserted against the source of all three merge sites.

    Behavioural tests above cover verify_spots and scrape_surf_forecast, whose merges are
    pure functions. classify_tides' merge lives inside main() behind an API key check and
    cannot be driven without one, so its pairing is asserted structurally.

    COUNTED PER ROSTER-RECORD VARIABLE, not by a bare `["tide_preference"] =` grep. A
    looser count first flagged scrape_surf_forecast as having three unpaired writes; two of
    those were `fields["tide_preference"]` inside parse_spot_page, which populates the PARSE
    RESULT and never touches a roster record. Naming the variable per file keeps the
    assertion on the writes that actually reach a spot.

    It does NOT catch a write through a variable key — `spot[f] = None` in
    unmerge_stale_matches is invisible to any textual count of this shape. That one is
    covered behaviourally by
    test_unmerge_clears_the_source_with_the_value_it_describes, which is the honest
    division of labour: text for pairing, behaviour for the rest.
    """
    roster_var = {
        "pipeline/classify_tides.py": "s",
        "pipeline/verify_spots.py": "spot",
        "pipeline/scrape_surf_forecast.py": "spot",
    }
    for path, var in roster_var.items():
        src = open(os.path.join(ROOT, path), encoding="utf-8").read()
        sets_value = src.count(f'{var}["tide_preference"] =')
        sets_source = src.count(f'{var}["tide_preference_source"] =')
        assert sets_value > 0, f"{path}: expected at least one tide_preference write"
        assert sets_source >= sets_value, (
            f"{path}: {sets_value} write(s) of {var}['tide_preference'] but only "
            f"{sets_source} of {var}['tide_preference_source'] — every write must stamp "
            f"its source"
        )


def test_unmerge_clears_the_source_with_the_value_it_describes():
    """The write site a textual count cannot see, and the one that nearly shipped.

    unmerge_stale_matches reverts scrape-derived fields when revalidation drops a match. It
    clears tide_preference by looping _MERGE_FIELDS with a variable key, so no grep for
    `spot["tide_preference"] =` finds it. Before this was fixed it left
    tide_preference_source reading 'scraped' on a tide_preference it had just set to None —
    a provenance field outliving the value it describes, which is the exact shape of the
    break_type_confidence defect this whole change exists to prevent.
    """
    sc = _scrape()
    spot = {
        "name": "Spot", "tide_preference": "low", "tide_preference_source": "scraped",
        "surf_forecast_url": "https://example.invalid/spot",
    }
    cache = {"Spot": {"previously_matched_url": "https://example.invalid/spot",
                      "source_url": None}}
    stats = sc.unmerge_stale_matches([spot], cache)
    assert stats["unmerged"] == 1
    assert spot["tide_preference"] is None
    assert spot["tide_preference_source"] is None, (
        "the source outlived the value it describes — break_type_confidence's defect"
    )
    assert stats.get("cleared_tide_preference_source") == 1


def test_unmerge_leaves_an_unsourced_spot_alone():
    """No spurious write when there was no source to clear."""
    sc = _scrape()
    spot = {"name": "Spot", "tide_preference": None, "tide_preference_source": None}
    cache = {"Spot": {"previously_matched_url": "https://example.invalid/spot",
                      "source_url": None}}
    stats = sc.unmerge_stale_matches([spot], cache)
    assert spot["tide_preference_source"] is None
    assert "cleared_tide_preference_source" not in stats


def test_a_value_and_a_source_are_never_left_disagreeing_by_any_scrape_path():
    """The invariant, over both scrape paths: a NULL value carries no source.

    Swept rather than spot-checked, because the two paths reach the same field by different
    routes and only one of them is visible to a textual assertion.
    """
    sc = _scrape()
    for start in ("scraped", "researched", "unattributed", None):
        spot = {"name": "Spot", "tide_preference": "low", "tide_preference_source": start,
                "surf_forecast_url": "u"}
        sc.unmerge_stale_matches(
            [spot], {"Spot": {"previously_matched_url": "u", "source_url": None}})
        assert spot["tide_preference"] is None, start
        assert spot["tide_preference_source"] is None, start


def test_every_writer_consults_the_shared_guard():
    """One comparison, not three. A site that reimplements the rank test can drift."""
    for path in ("pipeline/classify_tides.py", "pipeline/verify_spots.py",
                 "pipeline/scrape_surf_forecast.py"):
        src = open(os.path.join(ROOT, path), encoding="utf-8").read()
        assert "tide_source_may_overwrite" in src, path
        # and none of them hard-codes a rank number of its own
        for rank in TIDE_SOURCE_RANK.values():
            assert f"rank {rank}" not in src.replace("(rank %d)", ""), (path, rank)


# --------------------------------------------------------------------------- #
# db_import — the upsert passthrough                                           #
# --------------------------------------------------------------------------- #
def test_db_import_carries_the_source_column_into_the_upsert_record():
    """Recorded-write style, and this is the one place it is the only option.

    A fake PostgREST client returns whatever it was handed regardless of which columns the
    caller registered, so a test reading rows back passes with or without the column in the
    passthrough tuple — absence of the column IS the bug and is invisible downstream. Same
    reasoning as test_source_filter.py. Here the record db_import builds is inspected
    directly, which is stronger than recording a builder call.
    """
    from pipeline import db_import
    spot = {
        "name": "Spot", "lat": 1.0, "lng": 2.0,
        "tide_preference": "low_mid", "tide_preference_source": "scraped",
    }
    rec = db_import._spot_record(spot, {})
    assert rec["tide_preference"] == "low_mid"
    assert rec["tide_preference_source"] == "scraped", (
        "tide_preference_source is missing from db_import's passthrough tuple; the column "
        "exists in the database but nothing would ever write it from the roster"
    )


def test_db_import_omits_the_source_when_the_roster_has_none():
    """Absent must stay absent, not become NULL.

    db_import's preserve-merge fills absent keys from the existing DB row; writing an
    explicit None would overwrite a stored source with nothing. The passthrough is
    `if k in spot`, and this pins that.
    """
    from pipeline import db_import
    rec = db_import._spot_record({"name": "Spot", "lat": 1.0, "lng": 2.0}, {})
    assert "tide_preference_source" not in rec


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if not name.startswith("test_") or not callable(fn):
            continue
        if inspect.signature(fn).parameters:
            continue
        try:
            fn()
            print(f"  PASS  {name}")
        except AssertionError as e:
            fails += 1
            print(f"  FAIL  {name}: {e}")
        except Exception as e:  # noqa: BLE001
            fails += 1
            print(f"  ERROR {name}: {type(e).__name__}: {e}")
    print("tide preference source: ALL PASS" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
