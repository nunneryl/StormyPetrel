"""The break-type research pass: what it sends, what it believes, what it spends, and
what it may write.

WHAT THESE TESTS HOLD:

  1. the pilot is the twenty spots asked for, each matched in the roster exactly once;
  2. the model is never shown our break_type, its source or anything else of ours but
     the spot's name, state and coordinates;
  3. the requests go to MODEL with web search and web fetch called directly, and the
     memory answer has no tools;
  4. a researched answer stands only on a URL a tool really returned and a quote really
     on that page; anything else is 'unknown';
  5. a page that puts the spot in another state, or far from our coordinates, is no
     evidence, and a same-named spot elsewhere is recorded;
  6. cost is the API's usage at list price, the budget stops the run before a spot that
     could cross it, and it holds across re-runs;
  7. the run writes the results file and nothing else: never the roster, never the
     database;
  8. the real SDK accepts the request and its response is read correctly.

No expected value below is produced by calling the code under test: prices, costs,
distances, sentences and table rows are written out or worked by hand in the comments.
"""
from __future__ import annotations

import ast
import copy
import json
import os
import re
import shutil
import types

import pytest

from pipeline import config
from pipeline import research_break_type as rb

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MODULE = os.path.join(ROOT, "pipeline", "research_break_type.py")

with open(config.DEFAULT_ENRICHED_OUTPUT, encoding="utf-8") as _fh:
    ROSTER = json.load(_fh)

NO_SLEEP = lambda seconds: None  # noqa: E731

URL = "https://www.surf-forecast.com/breaks/Banzai-Pipeline"
OTHER_URL = "https://www.wannasurf.com/spot/North_America/USA/Hawaii/Oahu/pipeline/"
PAGE = ("Banzai Pipeline Surf Guide\n\n**Banzai Pipeline** in Oahu is an exposed reef "
        "break that has very consistent surf and works all around the year. Location: "
        "North Shore, O\u2018ahu, Hawaii.")
QUOTE = "Banzai Pipeline in Oahu is an exposed reef break"
PIPELINE_PROMPT = ("Name: Banzai Pipeline\n"
                   "State or territory: Hawaii, United States\n"
                   "Coordinates: 21.66400, -158.05400 (latitude, longitude)")

# A roster entry carrying everything we know about the spot, our value included.
SPOT = {
    "name": "Banzai Pipeline", "region_hint": "Hawaii", "lat": 21.664, "lng": -158.054,
    "break_type": "beach", "break_type_source": "unattributed",
    "break_type_confidence": "low", "break_type_source_url": None,
    "break_type_evidence": None,
    "verification_notes": "ROSTER-ONLY NOTE: a reef break per our records",
    "tags": {"nominatim_display_name": "ROSTER-ONLY ADDRESS"},
}
PIPELINE_ENTRY = rb.PILOT_SPOTS[0]
IDENTITY = {"name": "Banzai Pipeline", "region": "Hawaii", "lat": 21.664, "lng": -158.054}

SEARCH_TOOL = {"type": "web_search_20260209", "name": "web_search", "max_uses": 3,
               "allowed_callers": ["direct"]}
FETCH_TOOL = {"type": "web_fetch_20260209", "name": "web_fetch", "max_uses": 2,
              "max_content_tokens": 6000, "citations": {"enabled": True},
              "allowed_callers": ["direct"]}


# --- response builders: the shapes the Messages API returns ---------------------------

def _text(text, citations=None):
    block = {"type": "text", "text": text}
    if citations:
        block["citations"] = citations
    return block


def _search(call_id, query, urls):
    return [{"type": "server_tool_use", "id": call_id, "name": "web_search",
             "input": {"query": query}},
            {"type": "web_search_tool_result", "tool_use_id": call_id,
             "content": [{"type": "web_search_result", "url": u, "title": "t",
                          "encrypted_content": "enc", "page_age": None} for u in urls]}]


def _fetch(call_id, url, page, landed=None):
    return [{"type": "server_tool_use", "id": call_id, "name": "web_fetch",
             "input": {"url": url}},
            {"type": "web_fetch_tool_result", "tool_use_id": call_id,
             "content": {"type": "web_fetch_result", "url": landed or url,
                         "retrieved_at": "2026-10-06T12:00:00Z",
                         "content": {"type": "document",
                                     "source": {"type": "text", "media_type": "text/plain",
                                                "data": page},
                                     "title": "Banzai Pipeline Surf Guide",
                                     "citations": {"enabled": True}}}}]


def _answer(**overrides):
    answer = {"break_type": "reef", "break_types": ["reef"], "source_url": URL,
              "quote": QUOTE, "source_place": "North Shore, Oahu",
              "source_state": "Hawaii", "source_lat": None, "source_lng": None,
              "location_quote": "Banzai Pipeline in Oahu", "same_name_elsewhere": [],
              "note": None}
    answer.update(overrides)
    return _text("```json\n" + json.dumps(answer) + "\n```")


def _usage(input_tokens=0, output_tokens=0, searches=0, fetches=0):
    return {"input_tokens": input_tokens, "output_tokens": output_tokens,
            "server_tool_use": {"web_search_requests": searches,
                                "web_fetch_requests": fetches}}


def _response(content, stop="end_turn", usage=None):
    return {"content": content, "stop_reason": stop, "usage": usage or _usage()}


def _researched(*answer_blocks, fetch=True, stop="end_turn"):
    content = [{"type": "thinking", "thinking": "", "signature": "sig"}]
    content += _search("srvtoolu_1", "Banzai Pipeline Hawaii surf", [URL, OTHER_URL])
    if fetch:
        content += _fetch("srvtoolu_2", URL, PAGE)
    content += [_text("I read the surf-forecast.com guide. ")] + list(answer_blocks)
    return [_response(content, stop)]


def _recall(value="reef", confidence="high"):
    return _response([_text(json.dumps({"break_type": value, "break_types": [value],
                                        "confidence": confidence, "note": None}))])


class FakeClient:
    """Stands in for anthropic.Anthropic: records each request, answers from a script."""

    def __init__(self, respond):
        self.calls = []
        self.messages = self
        self._respond = respond

    def create(self, **request):
        self.calls.append(copy.deepcopy(request))
        return self._respond(request, len(self.calls))


class FakeStatusError(Exception):
    def __init__(self, status, retry_after=None):
        super().__init__(f"status {status}")
        self.status_code = status
        headers = {"retry-after": retry_after} if retry_after else {}
        self.response = types.SimpleNamespace(headers=headers)


# --- 1. the pilot ----------------------------------------------------------------------

ASKED = ("Banzai Pipeline", "Waimea Bay", "Jaws", "Honolua Bay", "Rincon Domes",
         "Tres Palmas", "Suicide's (HI)", "Bombora (HI)", "3 Mile", "Refugio State Beach",
         "Tourmaline", "Venice Beach Breakwater", "Lower Trestles",
         "Jacksonville Beach Pier", "Lake Worth Pier", "Manasquan Inlet", "Mavericks",
         "Steamer Lane", "Malibu Surfrider Beach", "Zuma Beach (plain sand beach)")


def test_the_pilot_is_the_twenty_spots_asked_for_each_matched_once():
    resolved = rb.resolve_pilot(ROSTER)
    assert tuple(entry[2] for entry, _ in resolved) == ASKED
    assert len({id(spot) for _, spot in resolved}) == 20
    assert [(e[0], e[1]) for e, s in resolved] == [(s["name"], s["region_hint"])
                                                   for e, s in resolved]


def test_the_controls_carry_the_answers_we_know_and_nothing_else_does():
    controls = {entry[0]: entry[3] for entry in rb.PILOT_SPOTS if entry[3]}
    assert controls == {"Mavericks, California": "reef", "Steamer Lane": "point",
                        "Malibu Surfrider Beach": "point", "Zuma Beach": "beach"}


def test_two_pilot_names_are_spelled_differently_in_the_roster():
    names = {spot["name"] for spot in ROSTER}
    assert "Jaws" not in names and "Peahi Jaws" in names
    assert "Mavericks" not in names and "Mavericks, California" in names
    roster_name = {entry[2]: entry[0] for entry in rb.PILOT_SPOTS}
    assert roster_name["Jaws"] == "Peahi Jaws"
    assert roster_name["Mavericks"] == "Mavericks, California"


def test_a_pilot_spot_missing_from_the_roster_stops_the_run():
    roster = [s for s in ROSTER if s["name"] != "Tourmaline"]
    with pytest.raises(SystemExit, match="Tourmaline"):
        rb.resolve_pilot(roster)


# --- 2-3. what the model is sent --------------------------------------------------------

RESEARCH_REQUEST = {
    "model": "claude-sonnet-5-5",
    "max_tokens": 8000,
    "system": [{"type": "text", "text": rb.RESEARCH_SYSTEM,
                "cache_control": {"type": "ephemeral"}}],
    "tools": [SEARCH_TOOL, FETCH_TOOL],
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "medium"},
    "messages": [{"role": "user", "content": PIPELINE_PROMPT}],
}
RECALL_REQUEST = {
    "model": "claude-sonnet-5-5",
    "max_tokens": 4000,
    "system": rb.RECALL_SYSTEM,
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "medium"},
    "messages": [{"role": "user", "content": PIPELINE_PROMPT}],
}


def test_the_requests_are_written_out_here_in_full():
    identity = rb.spot_identity(SPOT)
    assert identity == IDENTITY
    assert rb.research_request(identity, "medium") == RESEARCH_REQUEST
    assert rb.recall_request(identity, "medium") == RECALL_REQUEST


def test_the_model_is_never_shown_our_value_or_anything_else_of_ours():
    # The same spot, holding something different in every field but the identity: the
    # requests stay exactly the ones written out above.
    other = dict(SPOT, break_type="jetty", break_type_source="reviewed",
                 break_type_source_url="https://example.org/x",
                 break_type_evidence="a jetty break", verification_notes="different",
                 tags={"nominatim_display_name": "different"})
    identity = rb.spot_identity(other)
    assert rb.research_request(identity, "medium") == RESEARCH_REQUEST
    assert rb.recall_request(identity, "medium") == RECALL_REQUEST
    sent = json.dumps([RESEARCH_REQUEST, RECALL_REQUEST])
    for ours in ("unattributed", "ROSTER-ONLY", "break_type_source", "verification"):
        assert ours not in sent


def test_the_prompts_do_not_name_any_pilot_spot():
    # Naming one would hand the model the answer the pilot is testing for. Ordinary words
    # that are also in a spot's name ("beach", "breakwater", "pier") are not names.
    prompt_words = set(re.findall(r"[a-z']+", (rb.RESEARCH_SYSTEM + rb.RECALL_SYSTEM).lower()))
    ordinary = {"beach", "bay", "state", "point", "lane", "pier", "inlet", "lower", "lake",
                "worth", "breakwater"}
    names = {word for entry in rb.PILOT_SPOTS
             for word in re.findall(r"[a-z']+", entry[0].lower())} - ordinary
    assert names & prompt_words == set()
    assert {"suicide's", "bombora", "pipeline", "mavericks", "zuma"} <= names


def test_both_prompts_offer_exactly_the_six_values():
    for prompt in (rb.RESEARCH_SYSTEM, rb.RECALL_SYSTEM):
        assert ('"break_type": "beach" | "reef" | "point" | "jetty" | "rivermouth" '
                '| "unknown"') in prompt
    assert rb.BREAK_TYPE_VALUES == ("beach", "reef", "point", "jetty", "rivermouth",
                                    "unknown")


# --- 4. what counts as researched --------------------------------------------------------

def test_a_quote_on_the_fetched_page_with_its_url_is_researched():
    research = rb.judge_research(_researched(_answer()), IDENTITY)
    assert research["status"] == "researched"
    assert research["break_type"] == "reef"
    assert research["break_types"] == ["reef"]
    assert research["mixed"] is False
    assert research["source_url"] == URL
    assert research["quote"] == QUOTE
    assert research["quote_found_in"] == "fetched page"
    assert research["quote_names_type"] is True
    assert research["location"]["verdict"] == "ok"
    assert research["queries"] == ["Banzai Pipeline Hawaii surf"]
    assert research["fetches"] == [URL]
    assert research["reason"] is None


def test_a_url_no_tool_returned_is_not_evidence():
    made_up = "https://www.surfline.com/surf-report/pipeline/5842041f4e65fad6a7708890"
    research = rb.judge_research(_researched(_answer(source_url=made_up)), IDENTITY)
    assert research["status"] == "unknown"
    assert research["break_type"] == "unknown"
    assert research["reason"] == ("the cited URL was not returned by any search or fetch "
                                  "in this request")


def test_a_quote_that_is_not_on_the_page_is_not_evidence():
    research = rb.judge_research(
        _researched(_answer(quote="Pipeline is a shallow lava reef break")), IDENTITY)
    assert research["status"] == "unknown"
    assert research["reason"] == "the quote is not on the cited page"


def test_a_quote_too_short_to_prove_anything_is_not_evidence():
    research = rb.judge_research(_researched(_answer(quote="reef break")), IDENTITY)
    assert research["status"] == "unknown"
    assert research["reason"] == "the quote is under 3 words, too short to check"


def test_no_quote_is_not_evidence():
    research = rb.judge_research(_researched(_answer(quote=None)), IDENTITY)
    assert research["reason"] == "no quote"
    research = rb.judge_research(_researched(_answer(source_url=None)), IDENTITY)
    assert research["reason"] == "no source URL"


def test_a_search_citation_backs_a_quote_from_a_page_never_fetched():
    citation = {"type": "web_search_result_location", "url": URL, "title": "t",
                "encrypted_index": "i",
                "cited_text": "Banzai Pipeline in Oahu is an exposed reef break that has "
                              "very consistent surf and wor..."}
    blocks = _researched(_text("The guide says it is a reef break.", [citation]),
                         _answer(), fetch=False)
    research = rb.judge_research(blocks, IDENTITY)
    assert research["status"] == "researched"
    assert research["quote_found_in"] == "search citation"


def test_a_citation_from_another_page_does_not_back_the_quote():
    citation = {"type": "web_search_result_location", "url": OTHER_URL, "title": "t",
                "encrypted_index": "i", "cited_text": QUOTE}
    blocks = _researched(_text("x", [citation]), _answer(), fetch=False)
    assert rb.judge_research(blocks, IDENTITY)["reason"] == "the quote is not on the cited page"


def test_quote_marks_markdown_ellipses_and_spacing_do_not_hide_a_real_quote():
    quote = "\u201c...Banzai Pipeline in O\u2019ahu is   an exposed REEF break...\u201d"
    page = PAGE.replace("in Oahu", "in O'ahu")
    content = (_search("s1", "q", [URL]) + _fetch("f1", URL, page)
               + [_answer(quote=quote)])
    research = rb.judge_research([_response(content)], IDENTITY)
    assert research["quote_found_in"] == "fetched page"


def test_a_redirected_fetch_still_backs_the_url_the_model_asked_for():
    landed = "https://www.surf-forecast.com/breaks/Banzai-Pipeline/surf-guide"
    content = _search("s1", "q", [URL]) + _fetch("f1", URL, PAGE, landed=landed)
    content.append(_answer())
    research = rb.judge_research([_response(content)], IDENTITY)
    assert research["status"] == "researched"
    assert research["quote_found_in"] == "fetched page"


def test_www_scheme_and_trailing_slash_do_not_make_a_url_a_different_page():
    research = rb.judge_research(
        _researched(_answer(source_url="http://surf-forecast.com/breaks/Banzai-Pipeline/")),
        IDENTITY)
    assert research["status"] == "researched"


def test_a_value_outside_the_six_is_unknown():
    research = rb.judge_research(_researched(_answer(break_type="pier")), IDENTITY)
    assert research["status"] == "unknown"
    assert research["reason"] == "it answered 'pier', which is not one of the six values"


def test_the_models_own_unknown_is_kept_with_its_note():
    research = rb.judge_research(_researched(_answer(
        break_type="unknown", source_url=None, quote=None,
        note="Every page found is about a different spot.")), IDENTITY)
    assert research["status"] == "unknown"
    assert research["reason"] == ("no page it read says: Every page found is about a "
                                  "different spot.")


def test_no_answer_a_truncated_answer_and_a_refusal_are_each_unknown_and_said_so():
    assert rb.judge_research(_researched(_text("I could not decide.")),
                             IDENTITY)["reason"] == "no JSON answer"
    assert rb.judge_research(_researched(_text("{\"break_type\": \"re"), stop="max_tokens"),
                             IDENTITY)["reason"] == ("no JSON answer (it ran out of output "
                                                     "tokens)")
    refused = rb.judge_research([_response([], stop="refusal")], IDENTITY)
    assert refused["reason"] == "the model declined to answer (stop_reason refusal)"


def test_an_answer_split_across_cited_text_blocks_is_read_whole():
    # With citations the API splits text into blocks; the JSON is read across them.
    whole = json.loads(_answer()["text"][len("```json\n"):-len("\n```")])
    raw = json.dumps(whole)
    cut = raw.index(QUOTE)
    content = (_search("s1", "q", [URL]) + _fetch("f1", URL, PAGE)
               + [_text(raw[:cut]), _text(QUOTE, [{"type": "char_location",
                                                    "cited_text": QUOTE,
                                                    "document_index": 0,
                                                    "start_char_index": 0,
                                                    "end_char_index": 10}]),
                  _text(raw[cut + len(QUOTE):])])
    assert rb.judge_research([_response(content)], IDENTITY)["status"] == "researched"


def test_the_answer_is_the_last_json_object_that_has_a_break_type():
    draft = json.dumps({"break_type": "beach", "source_url": None})
    final = _answer()["text"]
    trailer = json.dumps({"checked": True})
    blocks = _researched(_text("A first guess: " + draft + " - then I read the page. "),
                         _text(final + "\n" + trailer))
    assert rb.judge_research(blocks, IDENTITY)["break_type"] == "reef"


def test_a_long_quote_is_kept_cut_to_300_characters():
    sentence = "Banzai Pipeline in Oahu is an exposed reef break " + "and so on " * 40
    content = (_search("s1", "q", [URL]) + _fetch("f1", URL, sentence)
               + [_answer(quote=sentence)])
    research = rb.judge_research([_response(content)], IDENTITY)
    assert research["quote_found_in"] == "fetched page"
    # The first 299 characters are the 49-character opening and 25 "and so on " (250);
    # the last of those is a space, which goes, and an ellipsis marks the cut.
    assert research["quote"] == ("Banzai Pipeline in Oahu is an exposed reef break "
                                 + "and so on " * 24 + "and so on\u2026")


def test_mixed_breaks_are_noted_in_the_pages_order():
    mixed = rb.judge_research(_researched(_answer(break_types=["reef", "beach"])), IDENTITY)
    assert (mixed["break_type"], mixed["break_types"], mixed["mixed"]) == (
        "reef", ["reef", "beach"], True)
    # The value is put first when the list left it out; unknown and junk are dropped.
    odd = rb.judge_research(_researched(_answer(break_types=["beach", "unknown", "pier"])),
                            IDENTITY)
    assert odd["break_types"] == ["reef", "beach"]


def test_the_quote_type_flag_ignores_the_spots_own_name():
    assert rb.quote_names_type("Zuma Beach is an exposed beach break", "beach",
                               "Zuma Beach") is True
    assert rb.quote_names_type("Zuma Beach is a popular spot with lifeguards", "beach",
                               "Zuma Beach") is False
    assert rb.quote_names_type("it breaks over a shallow coral shelf", "reef", "X") is True


# --- 5. the location check ----------------------------------------------------------------

def test_a_page_about_a_same_named_spot_in_another_state_is_no_evidence():
    answer = _answer(source_state="California", source_place="Santa Cruz",
                     location_quote="Suicides in Santa Cruz",
                     same_name_elsewhere=["Santa Cruz, California"])
    research = rb.judge_research(_researched(answer), IDENTITY)
    assert research["status"] == "unknown"
    assert research["break_type"] == "unknown"
    assert research["location"]["verdict"] == "wrong place"
    assert research["reason"] == ("the page describes another place: the page puts it in "
                                  "California")
    assert research["same_name_elsewhere"] == ["Santa Cruz, California"]
    assert research["model_answer"]["break_type"] == "reef"


def test_the_pages_own_location_words_outrank_the_state_the_model_reports():
    answer = _answer(source_state="Hawaii",
                     location_quote="Suicides, Santa Cruz, California")
    research = rb.judge_research(_researched(answer), IDENTITY)
    assert research["location"]["verdict"] == "wrong place"
    assert research["location"]["reasons"] == ["the page's location words name California"]


def test_coordinates_more_than_25_km_from_ours_are_another_place():
    # A pure latitude offset of 0.36 degrees is 6371 km * 0.36 * pi/180 = 40.03 km;
    # 0.09 degrees is 10.01 km.
    far = rb.check_location(IDENTITY, {"source_state": "Hawaii", "source_lat": 22.024,
                                       "source_lng": -158.054})
    assert far["verdict"] == "wrong place"
    assert far["distance_km"] == 40.0
    assert far["reasons"] == ["the page's coordinates are 40 km from ours"]
    near = rb.check_location(IDENTITY, {"source_state": "Hawaii", "source_lat": 21.754,
                                        "source_lng": -158.054})
    assert (near["verdict"], near["distance_km"]) == ("ok", 10.0)


def test_a_page_that_says_nowhere_is_unverified_and_its_value_kept():
    research = rb.judge_research(_researched(_answer(
        source_state=None, source_place=None, location_quote=None)), IDENTITY)
    assert research["status"] == "researched"
    assert research["location"]["verdict"] == "unverified"
    assert research["location"]["reasons"] == ["the page does not say which state"]


def test_state_spellings_read_the_same():
    for stated in ("Hawaii", "HI", "hawaii", "Hawai\u02bbi", "Hawaii (state)"):
        assert rb.check_location(IDENTITY, {"source_state": stated})["verdict"] == "ok", stated
    odd = rb.check_location(IDENTITY, {"source_state": "Oahu"})
    assert (odd["verdict"], odd["reasons"]) == ("unverified", ["cannot read the state 'Oahu'"])


def test_states_are_read_from_prose_by_full_name_only():
    assert rb.states_named_in("North Shore, O\u2018ahu, Hawai\u02bbi") == {"Hawaii"}
    assert rb.states_named_in("Pupukea, Hawai'i") == {"Hawaii"}
    assert rb.states_named_in("Morgantown, West Virginia") == {"West Virginia"}
    assert rb.states_named_in("the Jersey Shore in New Jersey") == {"New Jersey"}
    assert rb.states_named_in("works in or near me") == set()


# --- the memory answer and agreement ------------------------------------------------------

def test_the_memory_answer_is_read_as_given():
    recall = rb.judge_recall(_recall("point", "medium"))
    assert (recall["source"], recall["break_type"], recall["confidence"]) == (
        "model_recall", "point", "medium")
    junk = rb.judge_recall(_response([_text('{"break_type": "sandbar", "confidence": "sure"}')]))
    assert junk["break_type"] == "unknown"
    assert junk["reason"] == "it answered 'sandbar', which is not one of the six values"
    unsure = rb.judge_recall(_response([_text('{"break_type": "reef", "confidence": "sure"}')]))
    assert (unsure["break_type"], unsure["confidence"]) == ("reef", None)


def test_agreement_needs_both_answers_to_name_a_type():
    assert rb.agreement("reef", "reef") == "yes"
    assert rb.agreement("reef", "beach") == "no"
    assert rb.agreement("unknown", "reef") == "n/a"
    assert rb.agreement("reef", "unknown") == "n/a"


# --- 6. cost and budget ----------------------------------------------------------------------

def test_the_model_and_its_list_prices():
    assert rb.MODEL == "claude-sonnet-5-5"
    assert rb.PRICES == {"input_per_mtok": 2.00, "output_per_mtok": 10.00,
                         "cache_write_5m_per_mtok": 2.50, "cache_write_1h_per_mtok": 4.00,
                         "cache_read_per_mtok": 0.20, "per_web_search": 0.01,
                         "per_web_fetch": 0.00}


def test_a_requests_cost_is_its_usage_at_list_price():
    # 10,000 in x $2/M = $0.0200; 2,000 out x $10/M = $0.0200; 1,000 cache writes x $2.50/M
    # = $0.0025; 5,000 cache reads x $0.20/M = $0.0010; 2 searches x $0.01 = $0.0200; one
    # fetch costs nothing. Total $0.0635.
    usage = rb.usage_of({"usage": {"input_tokens": 10000, "output_tokens": 2000,
                                   "cache_creation_input_tokens": 1000,
                                   "cache_read_input_tokens": 5000,
                                   "server_tool_use": {"web_search_requests": 2,
                                                       "web_fetch_requests": 1}}})
    assert rb.cost_usd(usage) == pytest.approx(0.0635)
    assert (usage["web_search_requests"], usage["web_fetch_requests"]) == (2, 1)


def test_one_hour_cache_writes_are_priced_as_such():
    # 1,000 one-hour cache writes x $4/M = $0.0040.
    usage = rb.usage_of({"usage": {"input_tokens": 0, "output_tokens": 0,
                                   "cache_creation_input_tokens": 1000,
                                   "cache_creation": {"ephemeral_5m_input_tokens": 0,
                                                      "ephemeral_1h_input_tokens": 1000}}})
    assert rb.cost_usd(usage) == pytest.approx(0.0040)


def _dollar_responses(research_usd, recall_usd, research_stop="end_turn"):
    """A client whose research request costs research_usd and memory request recall_usd,
    all of it input tokens at $2 per million."""
    def respond(request, n):
        if "tools" in request:
            return _response([_answer(break_type="unknown", source_url=None, quote=None)],
                             stop=research_stop,
                             usage=_usage(input_tokens=round(research_usd * 500_000)))
        return _response(_recall()["content"],
                         usage=_usage(input_tokens=round(recall_usd * 500_000)))
    return FakeClient(respond)


def test_the_budget_stops_the_run_before_a_spot_that_could_cross_it():
    # Each spot costs $1.00 + $0.10 = $1.10. Spot 1 needs room for the $0.50 floor:
    # 0 + 0.50 <= 5. After it, spot n needs room for twice the dearest, $2.20:
    # spot 2: 1.10 + 2.20 = 3.30 <= 5; spot 3: 2.20 + 2.20 = 4.40 <= 5;
    # spot 4: 3.30 + 2.20 = 5.50 > 5, so the run stops with $3.30 spent.
    client = _dollar_responses(1.00, 0.10)
    results = rb.new_results("medium", 5.0)
    budget = rb.Budget(5.0)
    stopped = rb.run(client, rb.resolve_pilot(ROSTER), results, budget, "medium",
                     pause_seconds=0, sleep=NO_SLEEP)
    assert stopped == "budget"
    assert len(results["spots"]) == 3 and len(client.calls) == 6
    assert budget.spent_usd == pytest.approx(3.30)
    assert results["spent_usd"] == pytest.approx(3.30)


def test_the_budget_takes_a_spot_that_exactly_fits():
    # Spots of $1.00 + $0.25 = $1.25, all exact in binary. Cap $3.75: spot 1 needs
    # 0 + 0.50 <= 3.75; spot 2 needs 1.25 + 2.50 = 3.75 <= 3.75, which exactly fits;
    # spot 3 needs 2.50 + 2.50 = 5.00 > 3.75.
    client = _dollar_responses(1.00, 0.25)
    results = rb.new_results("medium", 3.75)
    stopped = rb.run(client, rb.resolve_pilot(ROSTER), results, rb.Budget(3.75), "medium",
                     pause_seconds=0, sleep=NO_SLEEP)
    assert (stopped, len(results["spots"]), results["spent_usd"]) == ("budget", 2, 2.50)


def test_no_spot_starts_without_room_for_the_floor():
    for cap, spots in ((0.49, 0), (0.50, 1)):
        client = _dollar_responses(0.10, 0.01)
        results = rb.new_results("medium", cap)
        rb.run(client, rb.resolve_pilot(ROSTER), results, rb.Budget(cap), "medium",
               limit=1, pause_seconds=0, sleep=NO_SLEEP)
        assert len(results["spots"]) == spots, cap


def test_the_run_pauses_between_spots_and_not_before_the_first():
    waits = []
    rb.run(_dollar_responses(0.10, 0.01), rb.resolve_pilot(ROSTER),
           rb.new_results("medium", 5.0), rb.Budget(5.0), "medium", limit=2,
           pause_seconds=15.0, sleep=waits.append)
    assert waits == [15.0]


def test_no_request_starts_once_the_budget_is_spent():
    # Cap $1.00: spot 1 may start (0 + 0.50 <= 1). Its research request costs $1.00 and
    # pauses; the continuation would start with $1.00 of $1.00 spent, so it never does.
    client = _dollar_responses(1.00, 0.10, research_stop="pause_turn")
    results = rb.new_results("medium", 1.0)
    budget = rb.Budget(1.0)
    stopped = rb.run(client, rb.resolve_pilot(ROSTER), results, budget, "medium",
                     pause_seconds=0, sleep=NO_SLEEP)
    assert stopped == "budget"
    assert len(client.calls) == 1
    record = results["spots"]["Banzai Pipeline|Hawaii"]
    assert record["error"] == "BudgetExhausted: spent $1.0000 of the $1.00 budget"
    assert record["done"] is False and record["cost_usd"] == pytest.approx(1.00)


def test_a_paused_turn_is_resumed_with_its_content_sent_back_unchanged():
    paused = _search("s1", "Banzai Pipeline surf", [URL])
    finished = _fetch("f1", URL, PAGE) + [_answer()]

    def respond(request, n):
        if "tools" not in request:
            return _recall()
        if n == 1:
            return _response(paused, stop="pause_turn", usage=_usage(1000, 10, searches=1))
        return _response(finished, usage=_usage(2000, 20, fetches=1))
    client = FakeClient(respond)
    record, exc = rb.research_spot(client, PIPELINE_ENTRY, SPOT, "medium", rb.Budget(5.0),
                                   NO_SLEEP)
    assert exc is None
    assert client.calls[1]["messages"] == [
        {"role": "user", "content": PIPELINE_PROMPT},
        {"role": "assistant", "content": paused}]
    assert record["research"]["status"] == "researched"
    # 3,000 in x $2/M + 30 out x $10/M + 1 search x $0.01 = 0.006 + 0.0003 + 0.01
    assert record["research_usage"]["cost_usd"] == pytest.approx(0.0163)
    assert record["research_usage"]["requests"] == 2


def test_a_rerun_resumes_where_the_last_one_stopped_and_counts_what_it_spent():
    resolved = rb.resolve_pilot(ROSTER)
    first = _dollar_responses(0.10, 0.01)
    results = rb.new_results("medium", 5.0)
    rb.run(first, resolved, results, rb.Budget(5.0), "medium", limit=2, pause_seconds=0,
           sleep=NO_SLEEP)
    assert list(results["spots"]) == ["Banzai Pipeline|Hawaii", "Waimea Bay|Hawaii"]
    second = _dollar_responses(0.10, 0.01)
    budget = rb.Budget(5.0, results["spent_usd"], 0.11)
    rb.run(second, resolved, results, budget, "medium", limit=1, pause_seconds=0,
           sleep=NO_SLEEP)
    assert second.calls[0]["messages"][0]["content"] == (
        "Name: Peahi Jaws\nState or territory: Hawaii, United States\n"
        "Coordinates: 20.95182, -156.28223 (latitude, longitude)")
    # Three spots at $0.11 each.
    assert results["spent_usd"] == pytest.approx(0.33)


def test_a_rate_limit_waits_as_told_and_retries():
    waits = []

    def respond(request, n):
        if n == 1:
            raise FakeStatusError(429, retry_after="7")
        return _researched(_answer())[0] if "tools" in request else _recall()
    record, exc = rb.research_spot(FakeClient(respond), PIPELINE_ENTRY, SPOT, "medium",
                                   rb.Budget(5.0), waits.append)
    assert exc is None and waits == [7.0]
    assert record["done"] is True


def test_a_bad_request_stops_the_run_and_a_server_error_skips_one_spot():
    def bad(request, n):
        raise FakeStatusError(400)
    results = rb.new_results("medium", 5.0)
    stopped = rb.run(FakeClient(bad), rb.resolve_pilot(ROSTER), results, rb.Budget(5.0),
                     "medium", pause_seconds=0, sleep=NO_SLEEP)
    assert stopped == "error: FakeStatusError 400: status 400"
    assert len(results["spots"]) == 1

    def flaky(request, n):
        if n == 1:
            raise FakeStatusError(500)
        return _researched(_answer())[0] if "tools" in request else _recall()
    results = rb.new_results("medium", 5.0)
    stopped = rb.run(FakeClient(flaky), rb.resolve_pilot(ROSTER), results, rb.Budget(5.0),
                     "medium", limit=2, pause_seconds=0, sleep=NO_SLEEP)
    assert stopped is None
    first, second = list(results["spots"].values())
    assert first["error"] == "FakeStatusError 500: status 500" and first["done"] is False
    assert second["done"] is True


def test_a_results_file_from_other_settings_is_not_resumed(tmp_path):
    path = tmp_path / "results.json"
    rb.save_results(path, rb.new_results("medium", 5.0))
    with pytest.raises(SystemExit, match="different settings"):
        rb.load_results(path, "high", 5.0, fresh=False)
    assert rb.load_results(path, "high", 5.0, fresh=True)["settings"]["effort"] == "high"


def test_the_full_roster_estimate_is_the_pilots_mean_and_dearest_times_646():
    # mean 0.20 x 646 = 129.20; dearest 0.30 x 646 = 193.80
    figures = rb.estimate([0.10, 0.30, 0.20])
    assert figures["mean"] == pytest.approx(0.20)
    assert figures["median"] == pytest.approx(0.20)
    assert (figures["min"], figures["max"]) == (0.10, 0.30)
    assert figures["full_at_mean"] == pytest.approx(129.20)
    assert figures["full_at_max"] == pytest.approx(193.80)


# --- 7. what it may write ----------------------------------------------------------------------

def _good_client():
    def respond(request, n):
        if "tools" in request:
            return _response(_researched(_answer())[0]["content"],
                             usage=_usage(12000, 900, searches=1, fetches=1))
        return _response(_recall()["content"], usage=_usage(300, 50))
    return FakeClient(respond)


def test_a_run_writes_its_results_file_and_nothing_else(tmp_path):
    roster = tmp_path / "spots_enriched_copy.json"
    shutil.copyfile(config.DEFAULT_ENRICHED_OUTPUT, roster)
    before = roster.read_bytes()
    output = tmp_path / "results.json"
    code = rb.main(["--roster", str(roster), "--output", str(output), "--limit", "2",
                    "--pause", "0"], client=_good_client(), sleep=NO_SLEEP)
    assert code == 0
    assert roster.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["results.json",
                                                         "spots_enriched_copy.json"]
    saved = json.loads(output.read_text())
    assert [r["done"] for r in saved["spots"].values()] == [True, True]


def test_the_results_file_may_not_be_the_roster(tmp_path):
    with pytest.raises(SystemExit, match="that is the roster"):
        rb.guard_output_path(config.DEFAULT_ENRICHED_OUTPUT, config.DEFAULT_ENRICHED_OUTPUT)
    with pytest.raises(SystemExit, match="that is the roster"):
        rb.guard_output_path(tmp_path / "spots_enriched.json", tmp_path / "copy.json")
    with pytest.raises(SystemExit, match="that is the roster"):
        rb.guard_output_path(tmp_path / "copy.json", tmp_path / "copy.json")
    with pytest.raises(SystemExit, match=".json file"):
        rb.guard_output_path(tmp_path / "results.csv", config.DEFAULT_ENRICHED_OUTPUT)
    rb.guard_output_path(tmp_path / "results.json", config.DEFAULT_ENRICHED_OUTPUT)


def test_a_dry_run_and_a_report_call_nothing_and_write_nothing(tmp_path, capsys):
    output = tmp_path / "results.json"
    never = FakeClient(lambda request, n: pytest.fail("no request may be made"))
    assert rb.main(["--dry-run", "--output", str(output)], client=never) == 0
    printed = capsys.readouterr().out
    assert PIPELINE_PROMPT in printed and rb.RESEARCH_SYSTEM in printed
    assert not output.exists()
    with pytest.raises(SystemExit, match="no results file"):
        rb.main(["--report", "--output", str(output)], client=never)


def test_nothing_here_can_reach_the_database():
    with open(MODULE, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
    assert imported == {"__future__", "argparse", "copy", "datetime", "hashlib", "json",
                        "logging", "os", "re", "statistics", "sys", "time", "unicodedata",
                        "pathlib", ".config", ".geo", "inspect", "anthropic"}


# --- the report -----------------------------------------------------------------------------

def test_the_report_rows():
    record, exc = rb.research_spot(_good_client(), PIPELINE_ENTRY, SPOT, "medium",
                                   rb.Budget(5.0), NO_SLEEP)
    assert exc is None
    results = rb.new_results("medium", 5.0)
    results["spots"]["Banzai Pipeline|Hawaii"] = record
    results["spent_usd"] = record["cost_usd"]
    report = rb.render_report(results).splitlines()
    assert report[0] == ("| Spot | Current value | Researched value | Source URL "
                         "| Location check | Memory answer | Agree |")
    assert report[2] == ("| Banzai Pipeline | beach | reef "
                         "| https://www.surf-forecast.com/breaks/Banzai-Pipeline "
                         "| ok (Hawaii) | reef, high confidence | yes |")
    assert report[3] == "| Waimea Bay | — | not run | — | — | — | — |"
    assert report[18] == "| Mavericks (control: reef) | — | not run | — | — | — | — |"
    # Research: 12,000 in x $2/M + 900 out x $10/M + 1 search x $0.01
    #   = 0.0240 + 0.0090 + 0.0100 = $0.0430.
    # Memory: 300 in x $2/M + 50 out x $10/M = 0.0006 + 0.0005 = $0.0011.
    costs = report[report.index("| Spot | Searches | Fetches | Input tokens | Output tokens "
                                "| Research | Memory | Total |") + 2]
    assert costs == ("| Banzai Pipeline | 1 | 1 | 12,300 | 950 | $0.0430 | $0.0011 "
                     "| $0.0441 |")
    # 0.0441 x 646 = 28.4886
    assert report[-3:] == [
        "Spent $0.0441 of the $5.00 budget, at list prices.",
        "Per spot, over 1 finished: mean $0.0441, median $0.0441, cheapest $0.0441, "
        "dearest $0.0441.",
        "The full 646 spots at the pilot's mean: $28.49; at its dearest spot's cost: $28.49."]


def test_a_report_cell_cannot_break_the_table():
    assert rb._cell("reef | beach\nbreak") == "reef / beach break"


def test_the_report_says_why_a_value_was_withheld():
    def respond(request, n):
        if "tools" in request:
            return _researched(_answer(source_state="California",
                                       same_name_elsewhere=["Santa Cruz, California"]))[0]
        return _recall()
    record, _ = rb.research_spot(FakeClient(respond), PIPELINE_ENTRY, SPOT, "medium",
                                 rb.Budget(5.0), NO_SLEEP)
    results = rb.new_results("medium", 5.0)
    results["spots"]["Banzai Pipeline|Hawaii"] = record
    assert rb.render_report(results).splitlines()[2] == (
        "| Banzai Pipeline | beach | unknown: the page describes another place: the page "
        "puts it in California | https://www.surf-forecast.com/breaks/Banzai-Pipeline "
        "| wrong place: the page puts it in California · name also used: Santa Cruz, "
        "California | reef, high confidence | n/a |")


# --- 8. the real SDK ---------------------------------------------------------------------------

def test_the_real_sdk_sends_this_request_and_reads_the_answer_back():
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx")
    bodies = []

    def message(content, usage):
        return {"id": "msg_test", "type": "message", "role": "assistant",
                "model": "claude-sonnet-5-5", "content": content, "stop_reason": "end_turn",
                "stop_sequence": None, "usage": usage}

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "tools" in body:
            return httpx.Response(200, json=message(
                _researched(_answer())[0]["content"],
                {"input_tokens": 12000, "output_tokens": 900,
                 "server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 1}}))
        return httpx.Response(200, json=message(_recall()["content"],
                                                {"input_tokens": 300, "output_tokens": 50}))

    client = anthropic.Anthropic(api_key="test-key", max_retries=0,
                                 http_client=httpx.Client(
                                     transport=httpx.MockTransport(handler)))
    record, exc = rb.research_spot(client, PIPELINE_ENTRY, SPOT, "medium", rb.Budget(5.0),
                                   NO_SLEEP)
    assert exc is None, record["error"]
    research_body, recall_body = bodies
    for field in ("model", "max_tokens", "system", "tools", "thinking", "output_config",
                  "messages"):
        assert research_body[field] == RESEARCH_REQUEST[field], field
    assert "tools" not in recall_body
    assert recall_body["messages"] == RECALL_REQUEST["messages"]
    for ours in ("unattributed", "ROSTER-ONLY", "break_type_source"):
        assert ours not in json.dumps(bodies)
    assert record["research"]["status"] == "researched"
    assert record["research"]["quote_found_in"] == "fetched page"
    assert record["model_recall"]["break_type"] == "reef"
    assert record["agree"] == "yes"
    assert record["cost_usd"] == pytest.approx(0.0441)
