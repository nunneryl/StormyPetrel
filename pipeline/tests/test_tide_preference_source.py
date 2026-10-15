"""Every writer of tide_preference says where it came from — truthfully — through one helper.

WHAT WENT WRONG. Migration 018 made every tide writer stamp a source, and two of the three
stamped the wrong one: classify_tides (a model with no tools) and verify_spots (a model that
never cites anything for the tide) both wrote 'researched', the top of the ladder, which reads
as "a page was consulted for this break". break_type had the same defect and migration 020
fixed it with model_recall and a cited, quoted page. Migration 022 does the same for the tide,
and pipeline/config.py tide_preference_fields / tide_preference_may_write are the only way a
writer sets it, so the rule lives in one place.

TWO KINDS OF ASSERTION:

  * BEHAVIOURAL, wherever the merge can be driven. The three merges (classify_tides,
    verify_spots, scrape_surf_forecast) are pure functions over dicts — feed them a spot and a
    record and read the spot back.

  * STRUCTURAL, only for what no output shows: that no writer sets a tide key by hand beside
    the helper, and db_import's passthrough tuple.

No expected value below is produced by calling the code under test.
"""
import os
import re
import sys
import types
from collections import Counter

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, ROOT)

from pipeline.config import (  # noqa: E402
    TIDE_SOURCE_RANK,
    tide_preference_fields,
    tide_preference_may_write,
    tide_source_may_overwrite,
    tide_source_rank,
)

# Migration 022's CHECK list for tide_preference_source, written out: the ladder in rank
# order, then 'unknown', which is legal and makes no provenance claim.
MIGRATION_022_SOURCES = (
    "researched", "scraped", "model_recall", "derived_from_break_type", "unattributed",
    "unknown",
)
URL = "https://www.surf-forecast.com/breaks/Rincon"
QUOTE = "Best around low tide"
WRITERS = ("pipeline/classify_tides.py", "pipeline/verify_spots.py",
           "pipeline/scrape_surf_forecast.py")


def _src(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


def _all_four(value, source, url=None, evidence=None):
    """The four keys a spot carries after a write, written out by the test."""
    return {"tide_preference": value, "tide_preference_source": source,
            "tide_preference_source_url": url, "tide_preference_evidence": evidence}


def _tide(spot):
    return {k: spot.get(k) for k in ("tide_preference", "tide_preference_source",
                                     "tide_preference_source_url", "tide_preference_evidence")}


# --------------------------------------------------------------------------- #
# the ladder                                                                   #
# --------------------------------------------------------------------------- #
def test_the_rank_ladder_is_exactly_these_five_values():
    assert TIDE_SOURCE_RANK == {
        "researched": 5,
        "scraped": 4,
        "model_recall": 3,
        "derived_from_break_type": 2,
        "unattributed": 1,
    }


def test_the_ladder_is_a_strict_total_order():
    ranks = list(TIDE_SOURCE_RANK.values())
    assert len(set(ranks)) == len(ranks)
    assert sorted(ranks) == [1, 2, 3, 4, 5]


def test_absent_null_and_unrecognised_all_rank_zero():
    assert tide_source_rank(None) == 0
    assert tide_source_rank("") == 0
    assert tide_source_rank("nonsense") == 0
    assert tide_source_rank("unknown") == 0


def test_every_migration_022_value_is_either_ranked_or_deliberately_not():
    deliberately_unranked = {"unknown"}
    for value in MIGRATION_022_SOURCES:
        assert (value in TIDE_SOURCE_RANK) or (value in deliberately_unranked), value


def test_a_model_answer_cannot_overwrite_a_cited_one():
    """The point of model_recall: an uncited answer sits below every cited one."""
    assert tide_source_may_overwrite("researched", "model_recall") is False
    assert tide_source_may_overwrite("scraped", "model_recall") is False
    assert tide_source_may_overwrite("researched", "scraped") is False
    assert tide_source_may_overwrite("model_recall", "derived_from_break_type") is False
    assert tide_source_may_overwrite("model_recall", "unattributed") is False


def test_a_model_answer_replaces_what_no_one_attributed():
    assert tide_source_may_overwrite("unattributed", "model_recall") is True
    assert tide_source_may_overwrite("derived_from_break_type", "model_recall") is True
    assert tide_source_may_overwrite(None, "model_recall") is True
    assert tide_source_may_overwrite("model_recall", "scraped") is True
    assert tide_source_may_overwrite("model_recall", "researched") is True


def test_equal_rank_is_permitted():
    for value in TIDE_SOURCE_RANK:
        assert tide_source_may_overwrite(value, value) is True, value


# --------------------------------------------------------------------------- #
# config.tide_preference_fields — the one way to write the tide               #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("args, kwargs, expected", [
    (("low", "model_recall"), {}, _all_four("low", "model_recall")),
    (("mid", "unattributed"), {}, _all_four("mid", "unattributed")),
    (("unknown", "model_recall"), {}, _all_four("unknown", "model_recall")),
    (("high", "scraped"), {"url": URL}, _all_four("high", "scraped", URL)),
    (("high", "scraped"), {"url": URL, "evidence": QUOTE}, _all_four("high", "scraped", URL, QUOTE)),
    (("low", "researched"), {"url": URL, "evidence": QUOTE},
     _all_four("low", "researched", URL, QUOTE)),
    ((None, None), {}, _all_four(None, None)),
])
def test_the_builder_returns_all_four_keys(args, kwargs, expected):
    assert tide_preference_fields(*args, **kwargs) == expected


@pytest.mark.parametrize("why, args, kwargs", [
    ("researched without its page", ("low", "researched"), {"evidence": QUOTE}),
    ("researched without its quote", ("low", "researched"), {"url": URL}),
    ("researched with neither — the old classify_tides write", ("low", "researched"), {}),
    ("scraped without its page", ("low", "scraped"), {}),
    ("a quote with no page", ("low", "model_recall"), {"evidence": QUOTE}),
    ("a value outside the list", ("sometimes", "model_recall"), {}),
    ("a source outside the ladder", ("low", "guess"), {}),
    ("'unknown' is no writer's source", ("low", "unknown"), {}),
    ("a value with no source", ("low", None), {}),
    ("a source with no value", (None, "model_recall"), {}),
    ("a citation with no value", (None, None), {"url": URL}),
    ("a URL that is not http(s)", ("low", "scraped"), {"url": "ftp://example.org/x"}),
    ("a quote over 300 characters", ("low", "scraped"), {"url": URL, "evidence": "x" * 301}),
    ("an empty quote", ("low", "scraped"), {"url": URL, "evidence": ""}),
])
def test_the_builder_refuses_what_022_refuses(why, args, kwargs):
    with pytest.raises(ValueError):
        tide_preference_fields(*args, **kwargs)


def test_the_builders_value_list_is_every_writers():
    from pipeline import config, verify_spots
    assert config.TIDE_PREFERENCE_VALUES == (
        "low", "low_mid", "mid", "mid_high", "high", "all", "unknown")
    assert set(config.TIDE_PREFERENCE_VALUES) == verify_spots._VALID_TIDE_PREFS
    assert '{"low", "low_mid", "mid", "mid_high", "high", "all", "unknown"}' in _src(
        "pipeline/classify_tides.py")


@pytest.mark.parametrize("spot, incoming, expected", [
    ({}, "model_recall", True),                                              # nothing held
    ({"tide_preference_source": "unattributed"}, "model_recall", True),
    ({"tide_preference_source": "scraped"}, "model_recall", False),
    ({"tide_preference_source": "researched"}, "scraped", False),
    ({"is_valid_surf_spot": True, "tide_preference_source": None}, "unattributed", True),
    ({"is_valid_surf_spot": False}, "researched", False),                    # not a surf spot
    ({"is_valid_surf_spot": False, "tide_preference_source": "unattributed"}, "researched", False),
])
def test_may_write(spot, incoming, expected):
    assert tide_preference_may_write(spot, incoming) is expected


# --------------------------------------------------------------------------- #
# classify_tides — model_recall, and never an invalid spot                     #
# --------------------------------------------------------------------------- #
def test_classify_tides_declares_model_recall_and_says_why():
    from pipeline import classify_tides
    assert classify_tides._TIDE_SOURCE == "model_recall"
    src = _src("pipeline/classify_tides.py")
    assert "WHY 'model_recall'" in src
    assert "no `tools=` argument" in src


def _cached(tide, conf=0.8):
    return {"tide_preference": tide, "tide_preference_confidence": conf}


def test_classify_tides_stamps_model_recall_with_no_citation():
    from pipeline import classify_tides
    spot = {"name": "Spot", "tide_preference": "mid", "tide_preference_source": "unattributed"}
    assert classify_tides.merge_into_spots([spot], {"Spot": _cached("low", 0.7)}) == (1, 0)
    assert _tide(spot) == _all_four("low", "model_recall")
    assert spot["tide_preference_confidence"] == 0.7


def test_classify_tides_declines_a_cited_value():
    from pipeline import classify_tides
    spot = {"name": "Spot", "tide_preference": "high", "tide_preference_source": "scraped",
            "tide_preference_source_url": URL}
    assert classify_tides.merge_into_spots([spot], {"Spot": _cached("low")}) == (0, 1)
    assert _tide(spot) == _all_four("high", "scraped", URL)


def test_classify_tides_writes_nothing_on_an_invalid_spot():
    from pipeline import classify_tides
    spot = {"name": "Shop", "is_valid_surf_spot": False}
    assert classify_tides.merge_into_spots([spot], {"Shop": _cached("low")}) == (0, 0)
    assert spot == {"name": "Shop", "is_valid_surf_spot": False}


def test_classify_tides_never_sends_an_invalid_spot(monkeypatch):
    """classify_all, driven with the API call replaced: the batch the model would see."""
    from pipeline import classify_tides
    sent = []

    def fake_batch(client, batch):
        sent.extend(s["name"] for s in batch)
        return ([{"name": s["name"], "tide_preference": "mid", "confidence": 0.5}
                 for s in batch], types.SimpleNamespace(input_tokens=0, output_tokens=0))

    monkeypatch.setitem(sys.modules, "anthropic",
                        types.SimpleNamespace(Anthropic=lambda: object()))
    monkeypatch.setattr(classify_tides, "_classify_batch", fake_batch)
    monkeypatch.setattr(classify_tides, "_load_cache", lambda: {})
    monkeypatch.setattr(classify_tides, "_save_cache", lambda cache: None)
    spots = [{"name": "A"}, {"name": "Shop", "is_valid_surf_spot": False},
             {"name": "B", "is_valid_surf_spot": True}]
    cache, _ = classify_tides.classify_all(spots)
    assert sent == ["A", "B"]
    assert sorted(cache) == ["A", "B"]


# --------------------------------------------------------------------------- #
# verify_spots — researched only with a cited, quoted page                     #
# --------------------------------------------------------------------------- #
def _raw_entry(**tide):
    """A model's answer as it arrives, before _normalize_record."""
    entry = {"name": "Spot", "is_valid_surf_spot": True, "confidence": "high",
             "tide_preference": "low", "hazards": [], "notes": ""}
    entry.update(tide)
    return entry


def _verify(spot, **tide):
    from pipeline import verify_spots as vs
    rec = vs._normalize_record(_raw_entry(**tide))
    return vs.merge_into_spots([spot], {"Spot": rec})


def test_verify_spots_without_a_citation_stamps_model_recall():
    spot = {"name": "Spot", "tide_preference": "mid", "tide_preference_source": "unattributed"}
    stats = _verify(spot)
    assert _tide(spot) == _all_four("low", "model_recall")
    assert stats["field_changes"]["tide_preference"] == 1


def test_verify_spots_with_a_cited_quoted_page_stamps_researched():
    spot = {"name": "Spot", "tide_preference": "mid", "tide_preference_source": "unattributed"}
    _verify(spot, tide_preference_source_url=URL, tide_preference_evidence=QUOTE)
    assert _tide(spot) == _all_four("low", "researched", URL, QUOTE)


@pytest.mark.parametrize("why, cite", [
    ("a page but no quote", {"tide_preference_source_url": URL}),
    ("a quote but no page", {"tide_preference_evidence": QUOTE}),
    ("a page that is not a URL", {"tide_preference_source_url": "surf-forecast.com/Rincon",
                                  "tide_preference_evidence": QUOTE}),
    ("an empty quote", {"tide_preference_source_url": URL, "tide_preference_evidence": "  "}),
])
def test_verify_spots_with_half_a_citation_stamps_model_recall(why, cite):
    spot = {"name": "Spot"}
    _verify(spot, **cite)
    assert _tide(spot) == _all_four("low", "model_recall"), why


def test_verify_spots_restamps_an_unattributed_value_it_agrees_with():
    """Same value, better-known source: the stamp moves, the value change count does not."""
    spot = {"name": "Spot", "tide_preference": "low", "tide_preference_source": "unattributed"}
    stats = _verify(spot)
    assert _tide(spot) == _all_four("low", "model_recall")
    assert stats["field_changes"]["tide_preference"] == 0
    assert stats["tide_preference_restamped"] == 1


@pytest.mark.parametrize("held", [
    _all_four("high", "scraped", URL),
    _all_four("high", "researched", URL, "Best around high tide"),
])
def test_verify_spots_uncited_cannot_overwrite_a_cited_value(held):
    spot = dict({"name": "Spot"}, **held)
    stats = _verify(spot)
    assert _tide(spot) == held
    assert stats["tide_preference_declined"] == 1
    assert stats["field_changes"]["tide_preference"] == 0


def test_verify_spots_writes_no_tide_on_an_invalid_spot():
    """Skipped, not declined: a spot that is not a surf spot is no disagreement between
    sources, so it logs no warning and counts no declined write."""
    spot = {"name": "Spot", "is_valid_surf_spot": False}
    stats = _verify(spot, tide_preference_source_url=URL, tide_preference_evidence=QUOTE)
    assert _tide(spot) == _all_four(None, None)
    assert "tide_preference_declined" not in stats


def test_verify_spots_writes_no_tide_on_a_spot_it_flags_invalid():
    spot = {"name": "Spot"}
    _verify(spot, is_valid_surf_spot=False, invalid_reason="surf_shop")
    assert spot["is_valid_surf_spot"] is False
    assert _tide(spot) == _all_four(None, None)


def test_verify_spots_prompt_asks_for_the_tide_citation():
    src = _src("pipeline/verify_spots.py")
    assert "tide_preference_source_url — string or null." in src
    assert "tide_preference_evidence — string or null." in src


# --------------------------------------------------------------------------- #
# scrape_surf_forecast — scraped, cited to its page and quoting it             #
# --------------------------------------------------------------------------- #
def _scrape():
    from pipeline import scrape_surf_forecast
    return scrape_surf_forecast


@pytest.mark.parametrize("html, tide, quote", [
    ("<p>Rincon is an exposed point break. Best around low tide when the swell is up.</p>",
     "low", "Best around low tide"),
    ("<p>It works at all tides.</p>", "all", "works at all tides"),
    ("<p>Good surf at all stages of the tide.</p>", "all", "at all stages"),
    ("<p>Best at High  tide.</p>", "high", "Best at High tide"),
    ("<p>No tide sentence here.</p>", None, None),
])
def test_the_page_parse_keeps_the_phrase_it_read(html, tide, quote):
    fields = _scrape().parse_spot_page(html)
    assert (fields["tide_preference"], fields["tide_preference_evidence"]) == (tide, quote)


def _scrape_record(tide, evidence=QUOTE):
    return {"source_url": URL, "tide_preference": tide, "tide_preference_evidence": evidence}


def test_scrape_stamps_scraped_with_its_page_and_quote():
    spot = {"name": "Spot", "tide_preference": "mid", "tide_preference_source": "model_recall"}
    stats = _scrape().merge_into_spots([spot], {"Spot": _scrape_record("low")})
    assert _tide(spot) == _all_four("low", "scraped", URL, QUOTE)
    assert stats["field_changes"]["tide_preference"] == 1


def test_scrape_from_a_cache_written_before_the_quote_still_cites_its_page():
    spot = {"name": "Spot"}
    _scrape().merge_into_spots([spot], {"Spot": {"source_url": URL, "tide_preference": "high"}})
    assert _tide(spot) == _all_four("high", "scraped", URL)


def test_scrape_cannot_overwrite_a_researched_value():
    held = _all_four("mid", "researched", URL, "Best around mid tide")
    spot = dict({"name": "Spot"}, **held)
    stats = _scrape().merge_into_spots([spot], {"Spot": _scrape_record("low")})
    assert _tide(spot) == held
    assert stats["tide_preference_declined"] == 1
    assert stats["field_changes"]["tide_preference"] == 0


def test_scrape_writes_no_tide_on_an_invalid_spot():
    """Skipped, not declined, as in verify_spots."""
    spot = {"name": "Spot", "is_valid_surf_spot": False}
    stats = _scrape().merge_into_spots([spot], {"Spot": _scrape_record("low")})
    assert _tide(spot) == _all_four(None, None)
    assert "tide_preference_declined" not in stats


def _dropped(url=URL):
    return {"Spot": {"previously_matched_url": url, "source_url": None}}


def test_unmerge_clears_the_tide_its_page_wrote_all_four_keys_together():
    spot = dict({"name": "Spot", "offshore_wind_deg": 90, "surf_forecast_url": URL},
                **_all_four("low", "scraped", URL, QUOTE))
    stats = _scrape().unmerge_stale_matches([spot], _dropped())
    assert _tide(spot) == _all_four(None, None)
    assert spot["offshore_wind_deg"] is None
    assert stats["cleared_fields"]["tide_preference"] == 1


@pytest.mark.parametrize("held", [
    _all_four("low", "scraped", "https://www.surf-forecast.com/breaks/Other", QUOTE),
    _all_four("low", "researched", URL, QUOTE),
    _all_four("low", "model_recall"),
    _all_four("low", "unattributed"),
])
def test_unmerge_leaves_a_tide_another_writer_set(held):
    """The old unmerge cleared the tide whoever had written it; now only its own page's."""
    spot = dict({"name": "Spot", "offshore_wind_deg": 90}, **held)
    stats = _scrape().unmerge_stale_matches([spot], _dropped())
    assert _tide(spot) == held
    assert spot["offshore_wind_deg"] is None
    assert stats["cleared_fields"]["tide_preference"] == 0


# --------------------------------------------------------------------------- #
# structural — one helper, no hand-set tide key                                #
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("path", WRITERS)
def test_every_writer_goes_through_the_two_helpers(path):
    src = _src(path)
    assert "tide_preference_fields(" in src, path
    assert "tide_preference_may_write(" in src, path
    # the bare comparison is the helper's to make, not a writer's
    assert "tide_source_may_overwrite" not in src, path


@pytest.mark.parametrize("path, var", [("pipeline/classify_tides.py", "s"),
                                       ("pipeline/verify_spots.py", "spot"),
                                       ("pipeline/scrape_surf_forecast.py", "spot")])
def test_no_writer_sets_a_tide_key_on_a_spot_by_hand(path, var):
    """A key set beside the helper is how a source drifts from its value."""
    hand = re.findall(rf'\b{var}\["tide_preference(?:_source|_source_url|_evidence)?"\]\s*=',
                      _src(path))
    assert hand == [], path


@pytest.mark.parametrize("path", WRITERS)
def test_no_writer_hard_codes_researched_for_the_tide(path):
    """'researched' appears in a writer only as verify_spots' cited constant."""
    src = _src(path)
    assert '_TIDE_SOURCE = "researched"' not in src
    expected = 1 if path == "pipeline/verify_spots.py" else 0
    assert src.count('_TIDE_CITED = "researched"') == expected


# --------------------------------------------------------------------------- #
# db_import and the roster                                                     #
# --------------------------------------------------------------------------- #
def test_db_import_carries_the_tide_and_its_citation():
    from pipeline import db_import
    spot = dict({"name": "Spot", "lat": 1.0, "lng": 2.0},
                **_all_four("low_mid", "researched", URL, QUOTE))
    rec = db_import._spot_record(spot, {})
    assert _tide(rec) == _all_four("low_mid", "researched", URL, QUOTE)


def test_db_import_omits_tide_keys_the_roster_does_not_carry():
    from pipeline import db_import
    rec = db_import._spot_record({"name": "Spot", "lat": 1.0, "lng": 2.0}, {})
    for k in ("tide_preference", "tide_preference_source", "tide_preference_source_url",
              "tide_preference_evidence"):
        assert k not in rec, k


def test_the_roster_stamps_every_held_tide_unattributed():
    """018's header counted the live table: mid 365, low 62, high 9, all 8 = 444 values, 202
    NULL, 646 rated spots. The roster's two invalid spots both hold 'mid' (018: 367 vs 365)."""
    import json
    with open(os.path.join(ROOT, "pipeline", "spots_enriched.json"), encoding="utf-8") as f:
        spots = json.load(f)
    valid = Counter((s.get("tide_preference"), s.get("tide_preference_source"),
                     s.get("tide_preference_source_url"), s.get("tide_preference_evidence"))
                    for s in spots if s.get("is_valid_surf_spot") is not False)
    assert valid == {("mid", "unattributed", None, None): 365,
                     ("low", "unattributed", None, None): 62,
                     ("high", "unattributed", None, None): 9,
                     ("all", "unattributed", None, None): 8,
                     (None, None, None, None): 202}
    invalid = [(s["name"], s.get("tide_preference"), s.get("tide_preference_source"))
               for s in spots if s.get("is_valid_surf_spot") is False]
    assert sorted(invalid) == [("Lewes Street Surf Beach", "mid", "unattributed"),
                               ("Seal Beach, California", "mid", "unattributed")]
