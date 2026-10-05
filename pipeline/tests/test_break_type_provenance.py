"""Every break_type carries where it came from, and every writer obeys the same ladder.

THE DEFECT. Three writers set break_type and none recorded itself. enrich's Algo 3 answered
'beach' for every spot (both curvature thresholds were infinity) and wrote
break_type_confidence = 0.5 beside it on every run; verify_spots and scrape_surf_forecast
overwrote the value on 83 spots without touching that number. So the confidence read 0.5 on
all 444 values and described none of them, and nothing could say which of the 444 a model
had checked, a page had named, or the algorithm had defaulted.

WHAT THESE TESTS HOLD (migration 020 holds the database side; see test_break_type_migration):

  1. the rank rules — lower never overwrites higher, equal may;
  2. the confidence follows the source, and nothing else;
  3. enrich no longer touches break_type or anything beside it;
  4. verify_spots stamps 'researched' only with a cited URL, otherwise 'model_recall';
  5. the scraper reads only the page's own sentence about its break, stamps 'scraped',
     obeys the ladder, and a dropped match takes back only what that page wrote;
  6. db_import carries the new columns and never sends the computed confidence;
  7. the committed roster holds the invariant, and every writer goes through one builder.

No expected value below is produced by calling the code under test: tables, values and
sentences are written out.
"""
from __future__ import annotations

import os

import pytest

from pipeline import config, db_import, enrich
from pipeline import scrape_surf_forecast as sc
from pipeline import verify_spots as vs
from pipeline.config import (
    break_type_confidence,
    break_type_fields,
    break_type_may_overwrite,
    break_type_quote,
    break_type_source_rank,
)
from pipeline.enrichment import tides as ET
from pipeline.tests.test_review_status_preserve import _db_row, _enriched, _run_import

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

# Written out from the plan the user signed off, not read back from config.
LADDER = {"reviewed": 6, "researched": 5, "scraped": 4, "model_recall": 3,
          "unattributed": 2, "algorithm_default": 1}
CONFIDENCE = {"reviewed": "high", "researched": "medium", "scraped": "medium",
              "model_recall": "low", "unattributed": "low", "algorithm_default": "low"}
VALUES = ("beach", "reef", "point", "jetty", "rivermouth", "unknown")
FIVE = ("break_type", "break_type_source", "break_type_confidence",
        "break_type_source_url", "break_type_evidence")

URL = "https://www.surf-forecast.com/breaks/Rincon"
QUOTE = "Rincon in Santa Barbara is an exposed point break"


def _spot(value=None, source=None, url=None, evidence=None, confidence=None, **extra):
    """A roster spot holding one break type, every key written out."""
    s = {"name": "Spot", "break_type": value, "break_type_source": source,
         "break_type_confidence": confidence, "break_type_source_url": url,
         "break_type_evidence": evidence}
    s.update(extra)
    return s


def _group(spot):
    return tuple(spot.get(k) for k in FIVE)


# --------------------------------------------------------------------------- #
# 1 — the ladder and the list                                                  #
# --------------------------------------------------------------------------- #

def test_the_ladder_and_the_list_are_these():
    assert config.BREAK_TYPE_SOURCE_RANK == LADDER
    assert config.BREAK_TYPE_VALUES == VALUES
    assert config.BREAK_TYPE_CITED_SOURCES == ("researched", "scraped")
    assert config.BREAK_TYPE_EVIDENCE_MAX_CHARS == 300
    assert config.BREAK_TYPE_FIELDS == FIVE


def test_the_ladder_is_a_strict_total_order():
    """No two sources share a rank, or the guard would allow a sideways write."""
    assert sorted(config.BREAK_TYPE_SOURCE_RANK.values()) == [1, 2, 3, 4, 5, 6]


def test_absent_and_unrecognised_sources_rank_zero():
    for source in (None, "", "manual", "unknown", "REVIEWED"):
        assert break_type_source_rank(source) == 0, source


@pytest.mark.parametrize("higher, lower", [
    ("reviewed", "researched"), ("reviewed", "model_recall"), ("researched", "scraped"),
    ("researched", "model_recall"), ("scraped", "model_recall"), ("scraped", "unattributed"),
    ("model_recall", "unattributed"), ("unattributed", "algorithm_default"),
    ("reviewed", "algorithm_default"),
])
def test_a_lower_source_never_overwrites_a_higher_one(higher, lower):
    assert break_type_may_overwrite(higher, lower) is False
    assert break_type_may_overwrite(lower, higher) is True


def test_an_equal_source_may_overwrite():
    """A second researched answer can still correct the first, as on the tide ladder."""
    for source in LADDER:
        assert break_type_may_overwrite(source, source) is True, source


def test_any_source_may_write_where_nothing_has_claimed_the_spot():
    for source in LADDER:
        assert break_type_may_overwrite(None, source) is True, source


def test_an_unrecognised_source_overwrites_nothing():
    for existing in LADDER:
        assert break_type_may_overwrite(existing, "manual") is False, existing


# --------------------------------------------------------------------------- #
# 2 — the confidence follows the source                                        #
# --------------------------------------------------------------------------- #

def test_confidence_is_high_for_reviewed_medium_for_cited_low_otherwise():
    for source, expected in CONFIDENCE.items():
        assert break_type_confidence(source) == expected, source
    assert break_type_confidence(None) is None


def test_every_write_carries_the_confidence_its_source_implies():
    for source, expected in CONFIDENCE.items():
        url = URL if source in ("researched", "scraped") else None
        out = break_type_fields("reef", source, url=url)
        assert out["break_type_confidence"] == expected, source


def test_a_write_is_all_five_keys_and_nothing_else():
    assert break_type_fields("reef", "researched", url=URL, evidence=QUOTE) == {
        "break_type": "reef", "break_type_source": "researched",
        "break_type_confidence": "medium", "break_type_source_url": URL,
        "break_type_evidence": QUOTE}
    assert break_type_fields(None, None) == dict.fromkeys(FIVE)


def test_unknown_is_a_value_someone_can_write():
    assert break_type_fields("unknown", "model_recall")["break_type"] == "unknown"


@pytest.mark.parametrize("args, kwargs", [
    (("sandbar", "unattributed"), {}),                       # not in the list
    (("reef", "guess"), {}),                                 # not on the ladder
    (("reef", None), {}),                                    # a value with no source
    ((None, "reviewed"), {}),                                # a source with no value
    ((None, None), {"url": URL}),                            # a citation with no value
    (("reef", "researched"), {}),                            # researched without its page
    (("reef", "scraped"), {}),                               # scraped without its page
    (("reef", "researched"), {"url": "surf-forecast.com/breaks/Rincon"}),   # not http(s)
    (("reef", "reviewed"), {"evidence": "x" * 301}),         # quote too long
    (("reef", "reviewed"), {"evidence": ""}),                # empty quote
])
def test_the_builder_refuses_what_migration_020_refuses(args, kwargs):
    """A bad write fails at the writer, not as a refused spots upsert in db_import."""
    with pytest.raises(ValueError):
        break_type_fields(*args, **kwargs)


def test_a_quote_of_exactly_300_characters_is_accepted():
    """The limit is inclusive, as migration 020's BETWEEN 1 AND 300 is."""
    out = break_type_fields("reef", "reviewed", evidence="x" * 300)
    assert out["break_type_evidence"] == "x" * 300


def test_a_quote_is_trimmed_to_300_characters_with_the_cut_marked():
    assert break_type_quote("  Rincon   is an\nexposed  point break ") == (
        "Rincon is an exposed point break")
    long = "word " * 100
    q = break_type_quote(long)
    assert len(q) == 300 and q.endswith("…")
    assert break_type_quote("   ") is None
    assert break_type_quote(None) is None
    assert break_type_quote(42) is None


# --------------------------------------------------------------------------- #
# 3 — enrich no longer touches break_type                                      #
# --------------------------------------------------------------------------- #

def _enrich(spot):
    """_enrich_one with every network/geodata algorithm stubbed, as the tide tests do."""
    saved = {k: getattr(enrich, k) for k in
             ("load_land_index", "compute_nearest_tide_station", "compute_nearest_buoy",
              "compute_orientation")}
    saved_load = ET.load_tide_stations
    try:
        enrich.load_land_index = lambda: None
        enrich.compute_nearest_tide_station = lambda s: {
            "nearest_tide_station_id": None, "nearest_tide_station_dist_km": None}
        enrich.compute_nearest_buoy = lambda s: {
            "nearest_buoy_id": None, "nearest_buoy_dist_km": None,
            "fallback_buoy_ids": [], "buoy_confidence": 0.0}
        enrich.compute_orientation = lambda s: {"orientation_deg": 270.0,
                                                "orientation_confidence": 0.5}
        ET.load_tide_stations = lambda: []
        return enrich._enrich_one(dict(spot), skip_raycast=True)
    finally:
        for k, v in saved.items():
            setattr(enrich, k, v)
        ET.load_tide_stations = saved_load


def test_enrich_leaves_a_sourced_break_type_exactly_as_it_found_it():
    spot = {"name": "Rincon", "lat": 34.3718, "lng": -119.4785,
            "break_type": "point", "break_type_source": "scraped",
            "break_type_confidence": "medium", "break_type_source_url": URL,
            "break_type_evidence": QUOTE}
    out = _enrich(spot)
    assert _group(out) == ("point", "scraped", "medium", URL, QUOTE)


def test_enrich_writes_no_break_type_where_nobody_has_looked():
    """Before: Algo 3 stamped 'beach' at 0.5 on any spot without a verification record."""
    out = _enrich({"name": "Nowhere", "lat": 30.0, "lng": -81.0})
    assert not any(k in out for k in FIVE), [k for k in FIVE if k in out]


def test_enrich_leaves_an_unverified_beach_alone_too():
    """The 271 unverified 'beach' values: enrich used to rewrite them every run."""
    out = _enrich({"name": "Somewhere", "lat": 30.0, "lng": -81.0, "break_type": "reef",
                   "break_type_source": "unattributed", "break_type_confidence": "low",
                   "break_type_source_url": None, "break_type_evidence": None})
    assert _group(out) == ("reef", "unattributed", "low", None, None)


def test_enrich_records_no_confidence_for_a_break_type():
    out = _enrich({"name": "Nowhere", "lat": 30.0, "lng": -81.0})
    assert "break_type" not in out["enrichment_confidence"]


def test_the_break_type_algorithm_is_gone():
    assert not hasattr(enrich, "compute_break_type")
    assert not os.path.exists(os.path.join(ROOT, "pipeline", "enrichment", "break_type.py"))


# --------------------------------------------------------------------------- #
# 4 — verify_spots: researched only with a cited URL                           #
# --------------------------------------------------------------------------- #

def _verified(break_type, url=None, evidence=None, confidence="high"):
    """A normalised verification record the merge will act on."""
    return {"name": "Spot", "is_valid_surf_spot": True, "invalid_reason": None,
            "facing_direction_deg": None, "offshore_wind_deg": None,
            "optimal_swell_dir": None, "break_type": break_type,
            "break_type_source_url": url, "break_type_evidence": evidence,
            "tide_preference": None, "crowd_factor": None, "hazards": [],
            "confidence": confidence, "notes": ""}


def test_verify_stamps_researched_with_a_cited_url():
    spot = _spot("beach", "unattributed", confidence="low")
    vs.merge_into_spots([spot], {"Spot": _verified("reef", URL, QUOTE)})
    assert _group(spot) == ("reef", "researched", "medium", URL, QUOTE)


def test_verify_stamps_model_recall_without_one():
    spot = _spot("beach", "unattributed", confidence="low")
    vs.merge_into_spots([spot], {"Spot": _verified("reef")})
    assert _group(spot) == ("reef", "model_recall", "low", None, None)


def test_verify_drops_a_quote_that_comes_without_its_page():
    spot = _spot("beach", "unattributed", confidence="low")
    vs.merge_into_spots([spot], {"Spot": _verified("reef", None, QUOTE)})
    assert _group(spot) == ("reef", "model_recall", "low", None, None)


@pytest.mark.parametrize("existing", ["researched", "reviewed"])
def test_an_uncited_answer_cannot_replace_a_cited_or_reviewed_one(existing):
    url = URL if existing == "researched" else None
    spot = _spot("point", existing, url=url, confidence=CONFIDENCE[existing])
    before = _group(spot)
    stats = vs.merge_into_spots([spot], {"Spot": _verified("beach")})
    assert _group(spot) == before
    assert stats["break_type_declined"] == 1
    assert stats["field_changes"]["break_type"] == 0


def test_a_second_cited_answer_can_correct_the_first():
    other = "https://www.surfline.com/surf-report/rincon/1"
    spot = _spot("beach", "researched", url=other, confidence="medium")
    vs.merge_into_spots([spot], {"Spot": _verified("point", URL, QUOTE)})
    assert _group(spot) == ("point", "researched", "medium", URL, QUOTE)


def test_the_same_value_from_a_better_known_source_is_restamped_not_counted_as_a_change():
    spot = _spot("beach", "unattributed", confidence="low")
    stats = vs.merge_into_spots([spot], {"Spot": _verified("beach")})
    assert _group(spot) == ("beach", "model_recall", "low", None, None)
    assert stats["field_changes"]["break_type"] == 0
    assert stats["break_type_restamped"] == 1


def test_a_low_confidence_record_writes_nothing():
    spot = _spot("beach", "unattributed", confidence="low")
    vs.merge_into_spots([spot], {"Spot": _verified("reef", URL, QUOTE, confidence="low")})
    assert _group(spot) == ("beach", "unattributed", "low", None, None)


def test_verify_never_leaves_the_value_and_its_source_apart():
    for start in (None, "algorithm_default", "unattributed", "model_recall", "scraped",
                  "researched", "reviewed"):
        url = URL if start in ("researched", "scraped") else None
        value = "beach" if start else None
        spot = _spot(value, start, url=url, confidence=CONFIDENCE.get(start))
        for rec in (_verified("reef"), _verified("reef", URL, QUOTE)):
            vs.merge_into_spots([spot], {"Spot": rec})
            assert (spot["break_type"] is None) == (spot["break_type_source"] is None), start
            assert spot["break_type_confidence"] == CONFIDENCE.get(spot["break_type_source"])


@pytest.mark.parametrize("raw, kept", [
    ("https://www.surf-forecast.com/breaks/Rincon", "https://www.surf-forecast.com/breaks/Rincon"),
    ("  https://www.surf-forecast.com/breaks/Rincon  ", "https://www.surf-forecast.com/breaks/Rincon"),
    ("http://example.org/a", "http://example.org/a"),
    ("surf-forecast.com/breaks/Rincon", None),
    ("https://a page with spaces", None),
    ("javascript:alert(1)", None),
    ("see surf-forecast.com", None),
    (17, None),
    (None, None),
])
def test_only_an_http_url_counts_as_a_citation(raw, kept):
    rec = vs._normalize_record({"name": "Spot", "break_type": "reef",
                                "break_type_source_url": raw,
                                "break_type_evidence": "Spot is an exposed reef break"})
    assert rec["break_type_source_url"] == kept
    assert (rec["break_type_evidence"] is None) == (kept is None)


def test_a_citation_without_a_valid_break_type_is_dropped_with_it():
    rec = vs._normalize_record({"name": "Spot", "break_type": "sandbar",
                                "break_type_source_url": URL, "break_type_evidence": QUOTE})
    assert rec["break_type"] is None
    assert rec["break_type_source_url"] is None
    assert rec["break_type_evidence"] is None


def test_the_model_may_answer_unknown():
    rec = vs._normalize_record({"name": "Spot", "break_type": "unknown"})
    assert rec["break_type"] == "unknown"


def test_a_long_quote_from_the_model_is_trimmed_to_fit():
    rec = vs._normalize_record({"name": "Spot", "break_type": "reef",
                                "break_type_source_url": URL,
                                "break_type_evidence": "quote " * 80})
    assert len(rec["break_type_evidence"]) == 300


def test_the_prompt_asks_for_the_citation_and_allows_unknown():
    assert "break_type_source_url" in vs._SYSTEM_PROMPT
    assert "break_type_evidence" in vs._SYSTEM_PROMPT
    assert "Never cite a page you did not read." in vs._SYSTEM_PROMPT
    assert '"jetty", "rivermouth", "unknown"' in vs._SYSTEM_PROMPT


# --------------------------------------------------------------------------- #
# 5 — the scraper: the page's own sentence, and nothing else                   #
# --------------------------------------------------------------------------- #

# Modelled on surf-forecast.com's spot page: a navigation line, the break's own templated
# sentence, then nearby breaks described in the same template. "beach break" appears BEFORE
# the own sentence, which is exactly the page the old first-phrase rule called 'beach'.
RINCON_PAGE = """
<html><head><title>Rincon Surf Forecast and Surf Reports (Cal - Santa Barbara, USA)</title></head>
<body>
<nav>Find the best beach breaks near you</nav>
<h1>Rincon Surf Forecast</h1>
<p>Rincon in Santa Barbara is an exposed point break that has quite consistent surf and can
work at any time of the year. Offshore winds blow from the north-northeast. Groundswells are
more frequent than windswells and the ideal swell direction is from the west.</p>
<section>Nearby breaks:
  <p>Rincon Point is an exposed beach break that has fairly consistent surf.</p>
  <p>Mussel Shoals is an exposed reef break that has inconsistent surf.</p>
</section>
</body></html>
"""


def _page(sentence, title="Spot Surf Forecast and Surf Reports", extra=""):
    return (f"<html><head><title>{title}</title></head><body><nav>Beach break, reef break and "
            f"point break guides</nav><p>{sentence}</p>{extra}</body></html>")


def test_the_page_used_here_would_have_read_beach_under_the_old_rule():
    """The fixture is only a test of the fix if 'beach break' comes first on it."""
    from bs4 import BeautifulSoup
    text = BeautifulSoup(RINCON_PAGE, "html.parser").get_text(" ", strip=True).lower()
    assert text.index("beach break") < text.index("is an exposed point break")


def test_the_break_type_comes_from_the_pages_own_sentence():
    fields = sc.parse_spot_page(RINCON_PAGE, "Rincon")
    assert fields["break_type"] == "point"
    assert fields["break_type_evidence"] == "Rincon in Santa Barbara is an exposed point break"


def test_the_rest_of_the_page_is_still_read():
    fields = sc.parse_spot_page(RINCON_PAGE, "Rincon")
    assert fields["offshore_wind_deg"] == 22.5
    assert fields["optimal_swell_dir"] == 270.0


def test_the_heading_alone_finds_the_sentence():
    """No name passed in: the page's own title says whose page it is."""
    assert sc.parse_spot_page(RINCON_PAGE)["break_type"] == "point"


@pytest.mark.parametrize("sentence, name, expected", [
    ("Pipeline in Oahu, Hawaii is an exposed reef break that has very consistent surf.",
     "Pipeline", "reef"),
    ("Huntington Beach Pier is an exposed beach break that has very consistent surf.",
     "Huntington Beach Pier", "beach"),
    ("Andrew Molera in Big Sur is a fairly exposed river mouth break that has reliable surf.",
     "Andrew Molera", "rivermouth"),
    ("Sebastian Inlet in Florida is an exposed jetty break that has consistent surf.",
     "Sebastian Inlet", "jetty"),
    ("Maria's in Rincon is a sheltered reef break that has reliable surf.", "Maria's", "reef"),
    ("Maria’s in Rincon is a sheltered reef break that has reliable surf.", "Maria's", "reef"),
    ("Pipeline - Backdoor in Oahu is an exposed reef break that has consistent surf.",
     "Pipeline Backdoor", "reef"),
])
def test_each_type_is_read_from_its_own_sentence(sentence, name, expected):
    fields = sc.parse_spot_page(_page(sentence, title=""), name)
    assert fields["break_type"] == expected
    assert fields["break_type_evidence"].endswith(" break")


def test_a_sentence_naming_two_types_yields_no_type_but_says_what_it_read():
    """surf-forecast lists types in a fixed order, so the first-named is not the dominant."""
    page = _page("Asilomar State Beach in Monterey is an exposed beach and reef break that "
                 "has consistent surf.", title="Asilomar State Beach Surf Forecast")
    fields = sc.parse_spot_page(page)
    assert fields["break_type"] is None
    assert fields["break_type_evidence"] == (
        "Asilomar State Beach in Monterey is an exposed beach and reef break")


@pytest.mark.parametrize("description", ["cobble", "beach and pier", "reef and sandbar"])
def test_a_word_the_parser_does_not_know_yields_no_type(description):
    """Even beside a type it knows: 'beach and pier' is not a sentence saying 'beach'."""
    page = _page(f"Seaside Cove in Oregon is an exposed {description} break that has "
                 "consistent surf.", title="Seaside Cove Surf Forecast")
    assert sc.parse_spot_page(page)["break_type"] is None


def test_another_breaks_sentence_is_never_read_as_this_ones():
    page = _page("Sewer Peak is an exposed reef break. First Peak is an exposed reef break.",
                 title="Pleasure Point Surf Forecast")
    fields = sc.parse_spot_page(page, "Pleasure Point")
    assert fields["break_type"] is None
    assert fields["break_type_evidence"] is None


def test_a_longer_name_that_starts_with_this_one_is_not_this_break():
    page = _page("Rincon Point is an exposed beach break.", title="Rincon Surf Forecast")
    assert sc.parse_spot_page(page, "Rincon")["break_type"] is None


def test_the_name_is_never_read_from_inside_another_word():
    page = _page("LaRincon is an exposed beach break. Rincon in Santa Barbara is an exposed "
                 "point break.", title="Rincon Surf Forecast")
    assert sc.parse_spot_page(page, "Rincon")["break_type"] == "point"


def test_a_place_never_reaches_across_a_full_stop():
    """'<Break> in <Place>.' then a later '... is an exposed beach break' is not one sentence."""
    page = _page("Rincon in Santa Barbara. The car park is an exposed beach break of sorts.",
                 title="Rincon Surf Forecast")
    assert sc.parse_spot_page(page, "Rincon")["break_type"] is None


# -- the merge ------------------------------------------------------------------

def _scraped(bt="point", evidence=QUOTE, url=URL):
    return {"source_url": url, "break_type": bt, "break_type_evidence": evidence}


def test_the_scrape_stamps_scraped_with_its_page_and_sentence():
    spot = _spot("beach", "unattributed", confidence="low")
    stats = sc.merge_into_spots([spot], {"Spot": _scraped()})
    assert _group(spot) == ("point", "scraped", "medium", URL, QUOTE)
    assert stats["field_changes"]["break_type"] == 1


@pytest.mark.parametrize("existing", ["model_recall", "unattributed", "algorithm_default", None])
def test_the_scrape_replaces_anything_below_it(existing):
    value = "beach" if existing else None
    spot = _spot(value, existing, confidence=CONFIDENCE.get(existing))
    sc.merge_into_spots([spot], {"Spot": _scraped()})
    assert _group(spot) == ("point", "scraped", "medium", URL, QUOTE)


@pytest.mark.parametrize("existing", ["researched", "reviewed"])
def test_the_scrape_never_replaces_a_researched_or_reviewed_type(existing):
    url = "https://www.surfline.com/surf-report/rincon/1" if existing == "researched" else None
    spot = _spot("reef", existing, url=url, confidence=CONFIDENCE[existing])
    before = _group(spot)
    stats = sc.merge_into_spots([spot], {"Spot": _scraped()})
    assert _group(spot) == before
    assert stats["break_type_declined"] == 1


def test_a_record_parsed_by_the_old_rule_is_not_applied():
    """A cached record with a type but no sentence came from the first-phrase rule."""
    spot = _spot("reef", "unattributed", confidence="low")
    stats = sc.merge_into_spots([spot], {"Spot": _scraped(bt="beach", evidence=None)})
    assert _group(spot) == ("reef", "unattributed", "low", None, None)
    assert stats["break_type_old_parse"] == 1


def test_a_page_naming_two_types_writes_nothing_and_is_counted():
    spot = _spot("reef", "unattributed", confidence="low")
    stats = sc.merge_into_spots([spot], {"Spot": _scraped(
        bt=None, evidence="Asilomar in Monterey is an exposed beach and reef break")})
    assert _group(spot) == ("reef", "unattributed", "low", None, None)
    assert stats["break_type_not_single"] == 1


def test_the_same_value_from_the_page_is_restamped():
    spot = _spot("point", "unattributed", confidence="low")
    stats = sc.merge_into_spots([spot], {"Spot": _scraped()})
    assert _group(spot) == ("point", "scraped", "medium", URL, QUOTE)
    assert stats["field_changes"]["break_type"] == 0
    assert stats["break_type_restamped"] == 1


# -- the unmerge ----------------------------------------------------------------

def _dropped(url=URL):
    return {"Spot": {"previously_matched_url": url, "source_url": None}}


def test_a_dropped_match_takes_back_the_type_its_page_wrote_all_five_keys_together():
    spot = _spot("point", "scraped", url=URL, evidence=QUOTE, confidence="medium",
                 surf_forecast_url=URL)
    stats = sc.unmerge_stale_matches([spot], _dropped())
    assert _group(spot) == (None, None, None, None, None)
    assert stats["cleared_fields"]["break_type"] == 1


@pytest.mark.parametrize("start", [
    ("reef", "researched", "https://www.surfline.com/surf-report/rincon/1", None, "medium"),
    ("reef", "reviewed", None, None, "high"),
    ("reef", "unattributed", None, None, "low"),
    ("reef", "model_recall", None, None, "low"),
    ("reef", "scraped", "https://www.surf-forecast.com/breaks/Other", QUOTE, "medium"),
])
def test_a_dropped_match_leaves_a_type_it_did_not_write(start):
    value, source, url, evidence, confidence = start
    spot = _spot(value, source, url=url, evidence=evidence, confidence=confidence,
                 surf_forecast_url=URL)
    stats = sc.unmerge_stale_matches([spot], _dropped())
    assert _group(spot) == (value, source, confidence, url, evidence)
    assert stats["cleared_fields"]["break_type"] == 0


# --------------------------------------------------------------------------- #
# 6 — db_import                                                                #
# --------------------------------------------------------------------------- #

def test_db_import_carries_the_type_its_source_and_its_citation():
    spot = {"name": "Spot", "lat": 1.0, "lng": 2.0, "break_type": "point",
            "break_type_source": "scraped", "break_type_confidence": "medium",
            "break_type_source_url": URL, "break_type_evidence": QUOTE}
    rec = db_import._spot_record(spot, {})
    assert {k: rec[k] for k in ("break_type", "break_type_source", "break_type_source_url",
                                "break_type_evidence")} == {
        "break_type": "point", "break_type_source": "scraped",
        "break_type_source_url": URL, "break_type_evidence": QUOTE}


def test_db_import_never_sends_the_confidence_the_database_computes():
    spot = {"name": "Spot", "lat": 1.0, "lng": 2.0, "break_type": "point",
            "break_type_source": "scraped", "break_type_confidence": "medium",
            "break_type_source_url": URL, "break_type_evidence": QUOTE}
    assert "break_type_confidence" not in db_import._spot_record(spot, {})
    assert "break_type_confidence" not in db_import.WRITTEN_COLUMNS["spots"]


def test_the_confidence_read_back_from_the_table_is_not_sent_back_either():
    """Postgres refuses any write to a generated column, so the preserve merge must skip it."""
    existing = [_db_row(break_type="reef", break_type_source="unattributed",
                        break_type_confidence="low")]
    up = _run_import(existing, [_enriched()])["steamer-lane"]
    assert "break_type_confidence" not in up
    assert up["break_type"] == "reef" and up["break_type_source"] == "unattributed"


def test_absent_provenance_stays_absent_for_the_preserve_merge_to_fill():
    rec = db_import._spot_record({"name": "Spot", "lat": 1.0, "lng": 2.0}, {})
    assert not any(k in rec for k in FIVE)


# --------------------------------------------------------------------------- #
# 7 — the committed roster, and the one builder                                #
# --------------------------------------------------------------------------- #

def _roster():
    import json
    with open(os.path.join(ROOT, "pipeline", "spots_enriched.json"), encoding="utf-8") as f:
        return json.load(f)


def test_every_roster_spot_holds_all_five_keys_or_none():
    for s in _roster():
        present = [k for k in FIVE if k in s]
        assert present in ([], list(FIVE)), (s["name"], present)


def test_every_roster_break_type_carries_a_source_and_the_confidence_it_implies():
    for s in _roster():
        if "break_type" not in s:
            continue
        name = s["name"]
        assert s["break_type"] in VALUES, name
        assert s["break_type_source"] in LADDER, name
        assert s["break_type_confidence"] == CONFIDENCE[s["break_type_source"]], name
        if s["break_type_source"] in ("researched", "scraped"):
            assert str(s["break_type_source_url"]).startswith(("http://", "https://")), name
        evidence = s["break_type_evidence"]
        assert evidence is None or 1 <= len(evidence) <= 300, name


def test_the_roster_keeps_no_algorithm_confidence_for_a_break_type():
    for s in _roster():
        assert "break_type" not in (s.get("enrichment_confidence") or {}), s["name"]


def _src(path):
    with open(os.path.join(ROOT, path), encoding="utf-8") as f:
        return f.read()


@pytest.mark.parametrize("path", ["pipeline/verify_spots.py", "pipeline/scrape_surf_forecast.py"])
def test_each_writer_goes_through_the_one_builder_and_the_one_guard(path):
    src = _src(path)
    assert "break_type_fields(" in src and "break_type_may_overwrite(" in src, path
    for key in FIVE:
        assert f'spot["{key}"] =' not in src, (path, key)


def test_enrich_writes_none_of_the_five_keys():
    src = _src("pipeline/enrich.py")
    for key in FIVE:
        assert f'_set("{key}"' not in src and f'enriched["{key}"]' not in src, key
    assert 'confidence["break_type"]' not in src


def test_the_writers_declare_their_sources_as_constants():
    assert vs._BREAK_TYPE_CITED == "researched"
    assert vs._BREAK_TYPE_UNCITED == "model_recall"
    assert sc._BREAK_TYPE_SOURCE == "scraped"
