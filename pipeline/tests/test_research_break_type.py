"""The break-type and bottom research pass: what it sends, what it believes, what it spends,
and what it may write.

WHAT THESE TESTS HOLD:

  1. the pilot is the twenty spots asked for, each matched in the roster exactly once;
  2. the model is never shown our break_type, its source or anything else of ours but
     the spot's name, state and coordinates;
  3. the requests go to MODEL with web search and web fetch called directly, fetched pages
     cut at 6,000 tokens, and the memory answer has no tools;
  4. the break type and the bottom each stand only on a URL a tool really returned and a
     quote really on that page; anything else is 'unknown', for that value alone;
  5. a page that puts the spot in another state, or far from our coordinates, is no
     evidence for the value that cites it, and a same-named spot elsewhere is recorded;
  6. the sand-bottom flag is derived from a researched bottom, and the researched and
     memory answers are compared on it;
  7. a failed fetch that leaves a value unknown gets one follow-up, which only fills
     values the first turn left unknown;
  8. cost is the API's usage at list price, the budget stops the run before a spot that
     could cross it, and it holds across re-runs;
  9. the run writes the results file and nothing else: never the roster, never the
     database, and the bottom has no column anywhere;
 10. the real SDK accepts the requests, the follow-up included, and its responses are
     read correctly;
 11. a quote is matched whatever its okina, accents or apostrophes, may stand on another
     page the run fetched, and when it is not found the reason says what the page was;
 12. the report ends with the gate for the full run, PASS or FAIL on each rule as set.

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
# A URL the model typed in itself, which web_fetch refuses with url_not_in_prior_context.
GUESSED_URL = "https://www.surf-forecast.com/breaks/Pipeline"
# 26 + 2 + 47 + 76 + 76 = 227 characters.
PAGE = ("Banzai Pipeline Surf Guide\n\n**Banzai Pipeline** in Oahu is an exposed reef "
        "break that has very consistent surf and works all around the year. The wave "
        "breaks over a shallow lava rock shelf. Location: North Shore, O‘ahu, Hawaii.")
QUOTE = "Banzai Pipeline in Oahu is an exposed reef break"
BOTTOM_QUOTE = "The wave breaks over a shallow lava rock shelf"
PAGE_ENTRY = {"url": URL, "place": "North Shore, Oahu", "state": "Hawaii", "lat": None,
              "lng": None, "location_quote": "Banzai Pipeline in Oahu"}
PIPELINE_PROMPT = ("Name: Banzai Pipeline\n"
                   "State or territory: Hawaii, United States\n"
                   "Coordinates: 21.66400, -158.05400 (latitude, longitude)")
UNKNOWN = {"value": "unknown", "all": [], "source_url": None, "quote": None}

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

FOLLOW_UP_TEXT = (
    "This fetch did not work:\n"
    "- https://www.surf-forecast.com/breaks/Pipeline: web_fetch opens only a URL that a "
    "search result or an earlier fetch returned, and this one came from neither\n"
    "\n"
    "Your answer for the kind of break and the bottom is still unknown. You may make one "
    "more tool call: one web search or one web fetch, not both. A URL written in this "
    "message may be fetched. Then give your final answer again, both values, as the same "
    "JSON object at the end of your reply.")


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


def _fetch_failed(call_id, url, code="url_not_in_prior_context"):
    return [{"type": "server_tool_use", "id": call_id, "name": "web_fetch",
             "input": {"url": url}},
            {"type": "web_fetch_tool_result", "tool_use_id": call_id,
             "content": {"type": "web_fetch_tool_result_error", "error_code": code}}]


def _answer(bt=None, bottom=None, pages=None, **top):
    """The research answer: reef on lava rock, both from the surf-forecast.com page, unless
    *bt* or *bottom* override fields of that value."""
    answer = {
        "break_type": dict({"value": "reef", "all": ["reef"], "source_url": URL,
                            "quote": QUOTE}, **(bt or {})),
        "bottom": dict({"value": "rock", "all": ["rock"], "source_url": URL,
                        "quote": BOTTOM_QUOTE}, **(bottom or {})),
        "pages": [dict(PAGE_ENTRY)] if pages is None else pages,
        "same_name_elsewhere": [], "note": None}
    answer.update(top)
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


def _recall(bt="reef", bottom="rock", confidence="high"):
    return _response([_text(json.dumps({
        "break_type": {"value": bt, "all": [bt], "confidence": confidence},
        "bottom": {"value": bottom, "all": [bottom], "confidence": confidence},
        "note": None}))])


def _judge(*answer_blocks, **kw):
    return rb.judge_research(_researched(*answer_blocks, **kw), IDENTITY)


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


def test_both_prompts_offer_the_six_break_types_and_the_six_bottoms():
    for prompt in (rb.RESEARCH_SYSTEM, rb.RECALL_SYSTEM):
        assert ('"value": "beach" | "reef" | "point" | "jetty" | "rivermouth" '
                '| "unknown"') in prompt
        assert '"value": "sand" | "rock" | "coral" | "cobble" | "mixed" | "unknown"' in prompt
    assert rb.BREAK_TYPE_VALUES == ("beach", "reef", "point", "jetty", "rivermouth",
                                    "unknown")
    assert rb.BOTTOM_VALUES == ("sand", "rock", "coral", "cobble", "mixed", "unknown")
    assert rb.FIELD_VALUES == {"break_type": rb.BREAK_TYPE_VALUES,
                               "bottom": rb.BOTTOM_VALUES}


def test_the_break_types_are_migration_020s_list_unchanged():
    with open(os.path.join(ROOT, "pipeline", "migrations", "020_break_type_provenance.sql"),
              encoding="utf-8") as fh:
        sql = fh.read()
    listed = re.search(r"break_type IS NULL OR break_type IN \(\s*([^)]*?)\s*\)", sql)
    assert listed.group(1) == "'beach', 'reef', 'point', 'jetty', 'rivermouth', 'unknown'"
    assert rb.FIELD_VALUES["break_type"] == ("beach", "reef", "point", "jetty",
                                             "rivermouth", "unknown")


def test_fetched_pages_are_cut_at_6000_tokens_and_pdfs_and_typed_urls_are_ruled_out():
    # The first pilot cut pages at 6,000 tokens and its answers held; the revision's 3,000
    # lost five of them, so the cap is back at 6,000. PDFs are outside the API's cut, and a
    # URL the model types in itself is refused (url_not_in_prior_context), so the prompt
    # rules out both.
    assert rb.FETCH_MAX_CONTENT_TOKENS == 6000
    assert rb.WEB_FETCH_TOOL == FETCH_TOOL
    assert "do not type one in yourself. Do not fetch PDFs." in rb.RESEARCH_SYSTEM.replace(
        "\n   ", " ")


# --- 4. what counts as researched ----------------------------------------------------------

def test_a_quote_on_the_fetched_page_with_its_url_is_researched():
    research = _judge(_answer())
    kind, bottom = research["break_type"], research["bottom"]
    assert (kind["status"], kind["value"], kind["values"], kind["mixed"]) == (
        "researched", "reef", ["reef"], False)
    assert (bottom["status"], bottom["value"], bottom["values"], bottom["mixed"]) == (
        "researched", "rock", ["rock"], False)
    assert kind["source_url"] == bottom["source_url"] == URL
    assert (kind["quote"], bottom["quote"]) == (QUOTE, BOTTOM_QUOTE)
    assert kind["quote_found_in"] == bottom["quote_found_in"] == "fetched page"
    assert kind["quote_names_value"] is True and bottom["quote_names_value"] is True
    assert kind["location"]["verdict"] == bottom["location"]["verdict"] == "ok"
    assert kind["reason"] is None and bottom["reason"] is None
    assert research["sand_bottom"] == "no"
    assert research["queries"] == ["Banzai Pipeline Hawaii surf"]
    assert research["fetches"] == [URL]
    assert research["follow_up"] is None


def test_each_value_stands_on_its_own_quote():
    # The bottom's quote is not on the page; the break type's is. Only the bottom is lost.
    research = _judge(_answer(bottom={"quote": "It breaks over a shallow coral reef"}))
    assert research["break_type"]["status"] == "researched"
    assert research["bottom"]["status"] == "unknown"
    # PAGE is 227 characters, and "it" is not one of its words.
    assert research["bottom"]["reason"] == (
        "the quote is not on the cited page (227 characters fetched, short of the "
        "6,000-token limit, so not cut): its first word, 'it', is not on the page")
    assert research["sand_bottom"] == "unknown"


def test_a_url_no_tool_returned_is_not_evidence():
    made_up = "https://www.surfline.com/surf-report/pipeline/5842041f4e65fad6a7708890"
    research = _judge(_answer(bt={"source_url": made_up}, bottom={"source_url": made_up}))
    for field in ("break_type", "bottom"):
        assert research[field]["status"] == "unknown"
        assert research[field]["value"] == "unknown"
        assert research[field]["reason"] == ("the cited URL was not returned by any search or "
                                             "fetch for this spot")


def test_a_quote_that_is_not_on_the_page_is_not_evidence():
    research = _judge(_answer(bt={"quote": "Pipeline is a shallow lava reef break"}))
    assert research["break_type"]["status"] == "unknown"
    # PAGE's words begin "banzai pipeline surf guide": "pipeline" is there, then "surf".
    assert research["break_type"]["reason"] == (
        "the quote is not on the cited page (227 characters fetched, short of the "
        "6,000-token limit, so not cut): its first word is on the page, then the quote has "
        "'is' where the page has 'surf'")


def test_a_quote_too_short_to_prove_anything_is_not_evidence():
    research = _judge(_answer(bt={"quote": "reef break"}, bottom={"quote": "lava rock"}))
    for field in ("break_type", "bottom"):
        assert research[field]["reason"] == "the quote is under 3 words, too short to check"


def test_no_quote_or_no_url_is_not_evidence():
    research = _judge(_answer(bt={"quote": None}, bottom={"source_url": None}))
    assert research["break_type"]["reason"] == "no quote"
    assert research["bottom"]["reason"] == "no source URL"


def test_a_search_citation_backs_a_quote_from_a_page_never_fetched():
    citation = {"type": "web_search_result_location", "url": URL, "title": "t",
                "encrypted_index": "i",
                "cited_text": "Banzai Pipeline in Oahu is an exposed reef break that has "
                              "very consistent surf and wor..."}
    research = _judge(_text("The guide says it is a reef break.", [citation]), _answer(),
                      fetch=False)
    assert research["break_type"]["status"] == "researched"
    assert research["break_type"]["quote_found_in"] == "search citation"
    # The citation does not hold the bottom's words, and nothing was fetched.
    assert research["bottom"]["reason"] == (
        "the quote is not on the cited page, which was never fetched, and no search "
        "citation from it holds the quote")


def test_a_search_citation_is_compared_on_its_words_too():
    citation = {"type": "web_search_result_location", "url": URL, "title": "t",
                "encrypted_index": "i",
                "cited_text": "The **Banzai Pipeline**, in [Oahu](https://x.org/Oahu), is a "
                              "reef break"}
    research = _judge(_text("x", [citation]),
                      _answer(bt={"quote": "The Banzai Pipeline, in Oahu, is a reef break"}),
                      fetch=False)
    assert research["break_type"]["quote_found_in"] == "search citation"


def test_a_citation_from_another_page_does_not_back_the_quote():
    citation = {"type": "web_search_result_location", "url": OTHER_URL, "title": "t",
                "encrypted_index": "i", "cited_text": QUOTE}
    research = _judge(_text("x", [citation]), _answer(), fetch=False)
    assert research["break_type"]["reason"] == (
        "the quote is not on the cited page, which was never fetched, and no search "
        "citation from it holds the quote")


def test_quote_marks_markdown_ellipses_and_spacing_do_not_hide_a_real_quote():
    quote = "“...Banzai Pipeline in O’ahu is   an exposed REEF break...”"
    page = PAGE.replace("in Oahu", "in O'ahu")
    content = (_search("s1", "q", [URL]) + _fetch("f1", URL, page)
               + [_answer(bt={"quote": quote})])
    research = rb.judge_research([_response(content)], IDENTITY)
    assert research["break_type"]["quote_found_in"] == "fetched page"


def test_a_redirected_fetch_still_backs_the_url_the_model_asked_for():
    landed = "https://www.surf-forecast.com/breaks/Banzai-Pipeline/surf-guide"
    content = _search("s1", "q", [URL]) + _fetch("f1", URL, PAGE, landed=landed)
    content.append(_answer())
    research = rb.judge_research([_response(content)], IDENTITY)
    assert research["break_type"]["status"] == research["bottom"]["status"] == "researched"
    assert research["break_type"]["quote_found_in"] == "fetched page"


# The revision's pilot lost Pipeline, Jaws, Tres Palmas, Suicide's and Bombora with "the
# quote is not on the cited page". The words were the same; the spelling was not: a page's
# okina (U+02BB), an accent or an apostrophe against the quote's plain letters, or the
# other way round.
@pytest.mark.parametrize("on_page, quoted", [
    ("Banzai Pipeline in Oʻahu is an exposed reef break",
     "Banzai Pipeline in Oahu is an exposed reef break"),
    ("Banzai Pipeline in Oahu is an exposed reef break",
     "Banzai Pipeline in Oʻahu is an exposed reef break"),
    ("Banzai Pipeline in O'ahu is an exposed reef break",
     "Banzai Pipeline in O‘ahu is an exposed reef break"),
    ("Peʻahi, also called Jaws, is a big-wave reef break",
     "Peahi, also called Jaws, is a big-wave reef break"),
    ("Tres Palmas off Rincón is a big-wave reef break",
     "Tres Palmas off Rincon is a big-wave reef break"),
    ("Tres Palmas off Rincon is a big-wave reef break",
     "Tres Palmas off Rincón is a big-wave reef break"),
    ("Suicides is a fast reef break", "Suicide's is a fast reef break"),
    ("Suicide’s is a fast reef break", "Suicides is a fast reef break"),
])
def test_okina_accents_and_apostrophes_do_not_hide_a_real_quote(on_page, quoted):
    content = (_search("s1", "q", [URL]) + _fetch("f1", URL, on_page)
               + [_answer(bt={"quote": quoted}, bottom=UNKNOWN)])
    research = rb.judge_research([_response(content)], IDENTITY)
    assert research["break_type"]["status"] == "researched"
    assert research["break_type"]["quote_found_in"] == "fetched page"


def test_a_different_word_is_still_not_the_quote():
    content = (_search("s1", "q", [URL]) + _fetch("f1", URL, PAGE)
               + [_answer(bt={"quote": "Banzai Pipeline in Maui is an exposed reef break"})])
    research = rb.judge_research([_response(content)], IDENTITY)
    # "banzai pipeline in" is on PAGE (its second "banzai pipeline"), then "oahu".
    assert research["break_type"]["reason"] == (
        "the quote is not on the cited page (227 characters fetched, short of the "
        "6,000-token limit, so not cut): its first 3 words are on the page, then the quote "
        "has 'maui' where the page has 'oahu'")


OTHER_PAGE = ("Pipeline, North Shore of Oahu. Type of wave: left reef. The wave breaks over a "
              "shallow lava rock shelf close to the beach.")


def test_a_quote_on_another_fetched_page_stands_on_that_page():
    # The model cites the surf-forecast page for words that are on the wannasurf page, which
    # it also fetched. The value stands on the wannasurf page, located by its own entry.
    quote = "breaks over a shallow lava rock shelf close to the beach"
    content = (_search("s1", "q", [URL, OTHER_URL]) + _fetch("f1", URL, PAGE)
               + _fetch("f2", OTHER_URL, OTHER_PAGE)
               + [_answer(bottom={"quote": quote},
                          pages=[dict(PAGE_ENTRY),
                                 dict(PAGE_ENTRY, url=OTHER_URL, place="North Shore of Oahu",
                                      location_quote="Pipeline, North Shore of Oahu")])])
    research = rb.judge_research([_response(content)], IDENTITY)
    bottom = research["bottom"]
    assert (bottom["status"], bottom["source_url"], bottom["cited_url"]) == (
        "researched", OTHER_URL, URL)
    assert bottom["quote_found_in"] == "fetched page"
    assert bottom["location"]["source_place"] == "North Shore of Oahu"
    assert bottom["location"]["verdict"] == "ok"
    assert (research["break_type"]["source_url"], research["break_type"]["cited_url"]) == (
        URL, None)


def test_a_quote_moved_to_a_page_with_no_location_entry_is_unverified_not_lost():
    quote = "breaks over a shallow lava rock shelf close to the beach"
    content = (_search("s1", "q", [URL, OTHER_URL]) + _fetch("f1", URL, PAGE)
               + _fetch("f2", OTHER_URL, OTHER_PAGE) + [_answer(bottom={"quote": quote})])
    bottom = rb.judge_research([_response(content)], IDENTITY)["bottom"]
    assert (bottom["status"], bottom["source_url"]) == ("researched", OTHER_URL)
    assert bottom["location"]["verdict"] == "unverified"


def test_a_search_result_page_never_fetched_does_not_lend_its_words():
    # Only fetched pages are searched for a moved quote: OTHER_URL came back from the
    # search but was never fetched, so its words are nowhere the code can read.
    quote = "breaks over a shallow lava rock shelf close to the beach"
    content = (_search("s1", "q", [URL, OTHER_URL]) + _fetch("f1", URL, PAGE)
               + [_answer(bottom={"quote": quote})])
    bottom = rb.judge_research([_response(content)], IDENTITY)["bottom"]
    # PAGE has "breaks over a shallow lava rock shelf. Location: ...".
    assert bottom["reason"] == (
        "the quote is not on the cited page (227 characters fetched, short of the "
        "6,000-token limit, so not cut): its first 7 words are on the page, then the quote "
        "has 'close' where the page has 'location'")


def test_a_page_long_enough_to_have_been_cut_is_named_in_the_reason():
    # 6,000 tokens x 4 characters x 0.9 = 21,600 characters. PAGE is 227 characters; one
    # space and 21,372 x's make 21,600, and one x fewer makes 21,599.
    # Either way the quote's "pipeline" is PAGE's second word, followed by "surf".
    differs = ("its first word is on the page, then the quote has 'is' where the page has "
               "'surf'")
    for xs, reason in ((21_372, "the quote is not on the cited page (21,600 characters "
                                "fetched, probably cut at the 6,000-token limit): " + differs),
                       (21_371, "the quote is not on the cited page (21,599 characters "
                                "fetched, short of the 6,000-token limit, so not cut): "
                                + differs)):
        page = PAGE + " " + "x" * xs
        content = (_search("s1", "q", [URL]) + _fetch("f1", URL, page)
                   + [_answer(bt={"quote": "Pipeline is described further down the page"})])
        research = rb.judge_research([_response(content)], IDENTITY)
        assert research["break_type"]["reason"] == reason, xs


def test_a_quote_cited_to_a_pdf_says_so():
    content = _search("s1", "q", [URL]) + [
        {"type": "server_tool_use", "id": "f1", "name": "web_fetch", "input": {"url": URL}},
        {"type": "web_fetch_tool_result", "tool_use_id": "f1",
         "content": {"type": "web_fetch_result", "url": URL,
                     "retrieved_at": "2026-10-06T12:00:00Z",
                     "content": {"type": "document",
                                 "source": {"type": "base64", "media_type": "application/pdf",
                                            "data": "JVBERi0xLjQK"}}}},
        _answer()]
    research = rb.judge_research([_response(content)], IDENTITY)
    assert research["break_type"]["reason"] == ("the quote is not on the cited page, a PDF, "
                                                "whose text cannot be checked")


# The gate run's Pipeline record: the break-type quote, exactly as the model gave it,
# cited to the Wikipedia article. It copies the page's markdown, a link and all, and stops
# inside the next link: "[Hawaii]" without its "(https://...)". The page around it is
# written here as web_fetch returns Wikipedia, as markdown, with the bold and footnote
# markers such a page carries.
PIPELINE_WIKI = "https://en.wikipedia.org/wiki/Banzai_Pipeline"
PIPELINE_QUOTE = ("The Banzai Pipeline, or simply Pipeline or Pipe, is a\n"
                  "[surf](https://en.wikipedia.org/wiki/Surfing) reef break located in [Hawaii]")
WIKI_PAGE = (
    "# Banzai Pipeline\n\nThe Banzai Pipeline, or simply Pipeline or Pipe, is a\n"
    "[surf](https://en.wikipedia.org/wiki/Surfing) reef break located in "
    "[Hawaii](https://en.wikipedia.org/wiki/Hawaii), off Ehukai Beach Park in "
    "[Pupukea](https://en.wikipedia.org/wiki/Pupukea,_Hawaii) on "
    "[O'ahu](https://en.wikipedia.org/wiki/O'ahu)'s "
    "[North Shore](https://en.wikipedia.org/wiki/North_Shore_(Oahu)).[[1]]"
    "(https://en.wikipedia.org/wiki/Banzai_Pipeline#cite_note-1) There are also several "
    "jagged, underwater lava spires that can injure fallen surfers.")
WIKI_ENTRY = {"url": PIPELINE_WIKI, "place": "off Ehukai Beach Park", "state": "Hawaii",
              "lat": None, "lng": None, "location_quote": "located in Hawaii"}


def _wiki(page, quote):
    content = (_search("s1", "q", [PIPELINE_WIKI]) + _fetch("f1", PIPELINE_WIKI, page)
               + [_answer(bt={"source_url": PIPELINE_WIKI, "quote": quote},
                          bottom=UNKNOWN, pages=[WIKI_ENTRY])])
    return rb.judge_research([_response(content)], IDENTITY)["break_type"]


def test_the_gate_runs_pipeline_quote_with_a_link_cut_off_is_on_the_page():
    kind = _wiki(WIKI_PAGE, PIPELINE_QUOTE)
    assert (kind["status"], kind["value"], kind["quote_found_in"]) == (
        "researched", "reef", "fetched page")


@pytest.mark.parametrize("page, quote", [
    # The page's bold markers, which the quote drops; before, they left " ," against ",".
    ("The **Banzai Pipeline**, or simply **Pipeline** or **Pipe**, is a surf reef break",
     "The Banzai Pipeline, or simply Pipeline or Pipe, is a surf reef break"),
    # A link whose URL holds parentheses, kept whole by the quote or dropped.
    ("on the [North Shore](https://en.wikipedia.org/wiki/North_Shore_(Oahu)). It breaks",
     "on the North Shore. It breaks"),
    ("on the [North Shore](https://en.wikipedia.org/wiki/North_Shore_(Oahu)). It breaks",
     "on the [North Shore](https://en.wikipedia.org/wiki/North_Shore_(Oahu)). It breaks"),
    # Text after the inner parentheses is still URL, not words on the page.
    ("a [reef](https://example.com/wiki/Reef_(surf)_break_guide) break over lava",
     "a reef break over lava"),
    # Footnote markers, which the quote leaves out.
    ("a reef break.[[1]](https://en.wikipedia.org/wiki/X#cite_note-1) It breaks left",
     "a reef break. It breaks left"),
    ("a reef break.[2] It breaks left[citation needed] over lava",
     "a reef break. It breaks left over lava"),
    # The quote's punctuation differs from the page's.
    ("Pipeline - a left-hand reef break - works in winter",
     "Pipeline, a left-hand reef break, works in winter"),
])
def test_markup_and_punctuation_between_the_words_do_not_hide_a_real_quote(page, quote):
    assert _wiki(page, quote)["quote_found_in"] == "fetched page"


@pytest.mark.parametrize("page, quote", [
    # A word left out, or two swapped: the words must be on the page together, in order.
    ("is a surf reef break located in Hawaii", "is a reef break located in Hawaii"),
    ("is a surf reef break located in Hawaii", "is a reef surf break located in Hawaii"),
    # An ellipsis inside the quote stands for words left out, unless the page has one.
    ("is a surf reef break located in Hawaii", "is a surf ... located in Hawaii"),
    ("is a surf reef break located in Hawaii", "is a surf ... reef break located"),
    # A word cut short is not the word.
    ("is a surf reef break located in Hawaii", "is a surf reef bre"),
    # A link's URL is not a word on the page: "wiki", "surfing" and the rest are not there.
    ("is a surf reef break", "is a https://en.wikipedia.org/wiki/Surfing reef break"),
])
def test_the_words_must_still_be_together_and_in_order(page, quote):
    assert _wiki(page, quote)["status"] == "unknown"


def test_an_ellipsis_the_page_has_is_part_of_the_quote():
    assert _wiki("Waves here... break hard on the reef", "Waves here... break hard on the reef"
                 )["quote_found_in"] == "fetched page"


def test_the_reason_says_where_the_quote_leaves_the_page():
    # WIKI_PAGE, folded to words, holds "... reef break located in hawaii off ehukai ...".
    # 15 of the quote's words are on it; then the quote has "maui".
    kind = _wiki(WIKI_PAGE, PIPELINE_QUOTE.replace("[Hawaii]", "Maui"))
    assert kind["reason"] == (
        f"the quote is not on the cited page ({len(WIKI_PAGE):,} characters fetched, short of "
        "the 6,000-token limit, so not cut): its first 15 words are on the page, then the "
        "quote has 'maui' where the page has 'hawaii'")
    # The page's text stops before the quote does. "The Banzai Pipeline, or simply
    # Pipeline or Pipe, is a surf reef break located in", word by word with the spaces:
    # 4+7+10+3+7+9+3+6+3+2+5+5+6+8+2 = 80 characters.
    page = "The Banzai Pipeline, or simply Pipeline or Pipe, is a surf reef break located in"
    assert _wiki(page, PIPELINE_QUOTE)["reason"] == (
        "the quote is not on the cited page (80 characters fetched, short of the 6,000-token "
        "limit, so not cut): its first 15 words are on the page, then the quote has 'hawaii' "
        "where the page ends")


def test_a_quote_of_ellipses_and_two_words_is_too_short():
    research = _judge(_answer(bt={"quote": "reef ... break"}))
    assert research["break_type"]["reason"] == ("the quote is under 3 words, too short to "
                                                "check")
    research = _judge(_answer(bt={"quote": "... reef break ..."}))
    assert research["break_type"]["reason"] == ("the quote is under 3 words, too short to "
                                                "check")


def test_the_prompt_says_to_quote_the_fetched_text_and_where_it_stops():
    assert ("4. Copy each quote from the text the fetch returned, not from a search result, "
            "and cite the URL of that fetched page. Your quote is checked against that text, "
            "which stops after about 6,000 tokens of the page.") in rb.RESEARCH_SYSTEM.replace(
        "\n   ", " ")


def test_www_scheme_and_trailing_slash_do_not_make_a_url_a_different_page():
    research = _judge(_answer(bt={"source_url": "http://surf-forecast.com/breaks/Banzai-Pipeline/"}))
    assert research["break_type"]["status"] == "researched"
    # ... and the answer's location entry, written the other way, is still that page's.
    assert research["break_type"]["location"]["verdict"] == "ok"


def test_a_value_outside_each_list_is_unknown():
    research = _judge(_answer(bt={"value": "pier"}, bottom={"value": "gravel"}))
    assert research["break_type"]["reason"] == ("it answered 'pier', which is not one of "
                                                "the 6 values")
    assert research["bottom"]["reason"] == "it answered 'gravel', which is not one of the 6 values"


def test_a_bare_value_or_a_missing_one_is_unknown():
    # The first pilot's flat answer: a bare break_type, no bottom.
    flat = _text(json.dumps({"break_type": "reef", "source_url": URL, "quote": QUOTE}))
    research = _judge(flat)
    assert research["break_type"]["reason"] == "no source URL"
    assert research["bottom"]["reason"] == "the answer has no bottom"


def test_the_models_own_unknown_is_kept_with_its_note():
    research = _judge(_answer(bt=UNKNOWN, bottom=UNKNOWN,
                              note="Every page found is about a different spot."))
    for field in ("break_type", "bottom"):
        assert research[field]["status"] == "unknown"
        assert research[field]["reason"] == ("no page it read says: Every page found is about "
                                             "a different spot.")


def test_no_answer_a_truncated_answer_and_a_refusal_are_each_unknown_and_said_so():
    none = _judge(_text("I could not decide."))
    assert none["break_type"]["reason"] == none["bottom"]["reason"] == "no JSON answer"
    cut = _judge(_text("{\"break_type\": {\"value\": \"re"), stop="max_tokens")
    assert cut["bottom"]["reason"] == "no JSON answer (it ran out of output tokens)"
    refused = rb.judge_research([_response([], stop="refusal")], IDENTITY)
    assert refused["reason"] == "the model declined to answer (stop_reason refusal)"
    assert refused["break_type"]["reason"] == refused["reason"]
    assert refused["sand_bottom"] == "unknown"


def test_an_answer_split_across_cited_text_blocks_is_read_whole():
    # With citations the API splits text into blocks; the JSON is read across them.
    raw = _answer()["text"][len("```json\n"):-len("\n```")]
    cut = raw.index(QUOTE)
    content = (_search("s1", "q", [URL]) + _fetch("f1", URL, PAGE)
               + [_text(raw[:cut]), _text(QUOTE, [{"type": "char_location",
                                                    "cited_text": QUOTE,
                                                    "document_index": 0,
                                                    "start_char_index": 0,
                                                    "end_char_index": 10}]),
                  _text(raw[cut + len(QUOTE):])])
    research = rb.judge_research([_response(content)], IDENTITY)
    assert research["break_type"]["status"] == research["bottom"]["status"] == "researched"


def test_the_answer_is_the_last_json_object_with_a_break_type_at_its_top_level():
    draft = json.dumps({"break_type": {"value": "beach"}, "bottom": {"value": "sand"}})
    final = _answer()["text"]
    trailer = json.dumps({"checked": True, "value": "beach"})
    research = _judge(_text("A first guess: " + draft + " - then I read the page. "),
                      _text(final + "\n" + trailer))
    assert (research["break_type"]["value"], research["bottom"]["value"]) == ("reef", "rock")


def test_a_long_quote_is_kept_cut_to_300_characters():
    sentence = "Banzai Pipeline in Oahu is an exposed reef break " + "and so on " * 40
    content = (_search("s1", "q", [URL]) + _fetch("f1", URL, sentence)
               + [_answer(bt={"quote": sentence})])
    research = rb.judge_research([_response(content)], IDENTITY)
    assert research["break_type"]["quote_found_in"] == "fetched page"
    # The first 299 characters are the 49-character opening and 25 "and so on " (250);
    # the last of those is a space, which goes, and an ellipsis marks the cut.
    assert research["break_type"]["quote"] == ("Banzai Pipeline in Oahu is an exposed reef "
                                               "break " + "and so on " * 24 + "and so on…")


def test_mixed_values_are_listed_in_the_pages_order():
    mixed = _judge(_answer(bt={"all": ["reef", "beach"]}))["break_type"]
    assert (mixed["value"], mixed["values"], mixed["mixed"]) == ("reef", ["reef", "beach"], True)
    # The value is put first when the list left it out; unknown and junk are dropped.
    odd = _judge(_answer(bt={"all": ["beach", "unknown", "pier"]}))["break_type"]
    assert odd["values"] == ["reef", "beach"]
    # A mixed bottom lists the materials it names, never the word "mixed".
    page = "Banzai Pipeline in Oahu is an exposed reef break. Its bottom is sand over rock."
    content = (_search("s1", "q", [URL]) + _fetch("f1", URL, page)
               + [_answer(bottom={"value": "mixed", "all": ["sand", "mixed", "rock"],
                                  "quote": "Its bottom is sand over rock"})])
    research = rb.judge_research([_response(content)], IDENTITY)
    bottom = research["bottom"]
    assert (bottom["status"], bottom["value"], bottom["values"], bottom["mixed"]) == (
        "researched", "mixed", ["sand", "rock"], True)
    assert research["sand_bottom"] == "unknown"


def test_the_quote_flag_looks_for_a_word_for_the_value_outside_the_spots_name():
    names = rb.quote_names_value
    assert names("Zuma Beach is an exposed beach break", "break_type", "beach",
                 "Zuma Beach") is True
    assert names("Zuma Beach is a popular spot with lifeguards", "break_type", "beach",
                 "Zuma Beach") is False
    assert names("it breaks over a shallow coral shelf", "break_type", "reef", "X") is True
    assert names("The wave breaks over a shallow lava rock shelf", "bottom", "rock", "X") is True
    assert names("It breaks over a shallow reef", "bottom", "coral", "X") is False
    assert names("It breaks over a shallow reef", "bottom", "rock", "X") is True
    assert names("a sandbar over rocks", "bottom", "mixed", "X") is True
    assert names("a long sandbar", "bottom", "mixed", "X") is False
    assert names("Sandspit breaks over boulders", "bottom", "sand", "Sandspit") is False


def test_the_record_keeps_each_fetched_pages_size_and_kind_and_each_failed_fetch():
    pdf_url = "https://www.example.gov/park-brochure.pdf"
    pdf = [{"type": "server_tool_use", "id": "f3", "name": "web_fetch",
            "input": {"url": pdf_url}},
           {"type": "web_fetch_tool_result", "tool_use_id": "f3",
            "content": {"type": "web_fetch_result", "url": pdf_url,
                        "retrieved_at": "2026-10-06T12:00:00Z",
                        "content": {"type": "document",
                                    "source": {"type": "base64",
                                               "media_type": "application/pdf",
                                               "data": "JVBERi0x"}}}}]
    content = (_search("s1", "q", [URL]) + _fetch("f1", URL, "x" * 4000)
               + _fetch_failed("f2", GUESSED_URL) + pdf
               + [_answer(bt=UNKNOWN, bottom=UNKNOWN)])
    research = rb.judge_research([_response(content)], IDENTITY)
    assert research["fetched_pages"] == [{"url": URL, "kind": "text", "chars": 4000},
                                         {"url": pdf_url, "kind": "pdf", "chars": None}]
    assert research["failed_fetches"] == [{"url": GUESSED_URL,
                                           "error": "url_not_in_prior_context"}]
    assert research["tool_errors"] == ["web_fetch: url_not_in_prior_context"]


# --- 5. the location check ------------------------------------------------------------------

def test_a_page_about_a_same_named_spot_in_another_state_is_no_evidence():
    elsewhere = [{"url": URL, "place": "Santa Cruz", "state": "California", "lat": None,
                  "lng": None, "location_quote": "Suicides in Santa Cruz"}]
    research = _judge(_answer(pages=elsewhere, same_name_elsewhere=["Santa Cruz, California"]))
    for field in ("break_type", "bottom"):
        assert research[field]["status"] == "unknown"
        assert research[field]["location"]["verdict"] == "wrong place"
        assert research[field]["reason"] == ("the page describes another place: the page "
                                             "puts it in California")
    assert research["same_name_elsewhere"] == ["Santa Cruz, California"]
    assert research["model_answer"]["break_type"]["value"] == "reef"
    assert research["sand_bottom"] == "unknown"


def test_each_value_is_located_by_the_page_it_cites():
    other_page = "Pipeline, Santa Cruz: the wave breaks over a rock ledge off the stairs."
    pages = [dict(PAGE_ENTRY),
             {"url": OTHER_URL, "place": "Santa Cruz", "state": "California", "lat": None,
              "lng": None, "location_quote": "Pipeline, Santa Cruz"}]
    content = (_search("s1", "q", [URL, OTHER_URL]) + _fetch("f1", URL, PAGE)
               + _fetch("f2", OTHER_URL, other_page)
               + [_answer(bottom={"source_url": OTHER_URL,
                                  "quote": "the wave breaks over a rock ledge"}, pages=pages)])
    research = rb.judge_research([_response(content)], IDENTITY)
    assert research["break_type"]["status"] == "researched"
    assert research["break_type"]["location"]["verdict"] == "ok"
    assert research["bottom"]["status"] == "unknown"
    assert research["bottom"]["location"]["verdict"] == "wrong place"


def test_the_pages_own_location_words_outrank_the_state_the_model_reports():
    pages = [dict(PAGE_ENTRY, state="Hawaii",
                  location_quote="Suicides, Santa Cruz, California")]
    research = _judge(_answer(pages=pages))
    assert research["break_type"]["location"]["verdict"] == "wrong place"
    assert research["break_type"]["location"]["reasons"] == [
        "the page's location words name California"]


def test_coordinates_more_than_25_km_from_ours_are_another_place():
    # A pure latitude offset of 0.36 degrees is 6371 km * 0.36 * pi/180 = 40.03 km;
    # 0.09 degrees is 10.01 km.
    far = rb.check_location(IDENTITY, {"state": "Hawaii", "lat": 22.024, "lng": -158.054})
    assert far["verdict"] == "wrong place"
    assert far["distance_km"] == 40.0
    assert far["reasons"] == ["the page's coordinates are 40 km from ours"]
    near = rb.check_location(IDENTITY, {"state": "Hawaii", "lat": 21.754, "lng": -158.054})
    assert (near["verdict"], near["distance_km"]) == ("ok", 10.0)


def test_a_page_that_says_nowhere_is_unverified_and_its_value_kept():
    research = _judge(_answer(pages=[]))
    for field in ("break_type", "bottom"):
        assert research[field]["status"] == "researched"
        assert research[field]["location"]["verdict"] == "unverified"
        assert research[field]["location"]["reasons"] == ["the page does not say which state"]


def test_state_spellings_read_the_same():
    for stated in ("Hawaii", "HI", "hawaii", "Hawaiʻi", "Hawaii (state)"):
        assert rb.check_location(IDENTITY, {"state": stated})["verdict"] == "ok", stated
    odd = rb.check_location(IDENTITY, {"state": "Oahu"})
    assert (odd["verdict"], odd["reasons"]) == ("unverified", ["cannot read the state 'Oahu'"])


def test_states_are_read_from_prose_by_full_name_only():
    assert rb.states_named_in("North Shore, O‘ahu, Hawaiʻi") == {"Hawaii"}
    assert rb.states_named_in("Pupukea, Hawai'i") == {"Hawaii"}
    assert rb.states_named_in("Morgantown, West Virginia") == {"West Virginia"}
    assert rb.states_named_in("the Jersey Shore in New Jersey") == {"New Jersey"}
    assert rb.states_named_in("works in or near me") == set()


# --- 6. the sand-bottom flag, the memory answer and agreement --------------------------------

def test_the_sand_bottom_flag_is_yes_for_sand_no_for_a_fixed_bottom_and_unknown_otherwise():
    assert rb.SAND_BOTTOM == {"sand": "yes", "rock": "no", "coral": "no", "cobble": "no",
                              "mixed": "unknown", "unknown": "unknown"}
    cases = (("sand", ["sand"], "The wave breaks over shifting sandbars", "yes"),
             ("coral", ["coral"], "The wave breaks over a shallow coral reef", "no"),
             ("cobble", ["cobble"], "The wave breaks over rounded cobblestones", "no"),
             ("mixed", ["sand", "rock"], "The wave breaks over sand and rock", "unknown"))
    for value, materials, sentence, flag in cases:
        content = (_search("s1", "q", [URL]) + _fetch("f1", URL, "Pipeline. " + sentence + ".")
                   + [_answer(bt=UNKNOWN, bottom={"value": value, "all": materials,
                                                  "quote": sentence})])
        research = rb.judge_research([_response(content)], IDENTITY)
        assert research["bottom"]["status"] == "researched", value
        assert research["sand_bottom"] == flag, value


def test_the_sand_bottom_flag_comes_only_from_a_researched_bottom():
    research = _judge(_answer(bottom={"value": "sand", "all": ["sand"],
                                      "quote": "The wave breaks over shifting sandbars"}))
    assert research["bottom"]["status"] == "unknown"
    assert research["sand_bottom"] == "unknown"


def test_the_memory_answer_is_read_as_given():
    recall = rb.judge_recall(_recall("point", "cobble", "medium"))
    assert recall["source"] == "model_recall"
    assert (recall["break_type"]["value"], recall["break_type"]["confidence"]) == (
        "point", "medium")
    assert (recall["bottom"]["value"], recall["sand_bottom"]) == ("cobble", "no")
    assert rb.judge_recall(_recall("beach", "sand"))["sand_bottom"] == "yes"
    assert rb.judge_recall(_recall("beach", "mixed"))["sand_bottom"] == "unknown"
    junk = rb.judge_recall(_response([_text(
        '{"break_type": {"value": "sandbar", "confidence": "sure"}, "bottom": "gravel"}')]))
    assert junk["break_type"]["reason"] == ("it answered 'sandbar', which is not one of "
                                            "the 6 values")
    assert junk["bottom"]["reason"] == "it answered 'gravel', which is not one of the 6 values"
    unsure = rb.judge_recall(_response([_text(
        '{"break_type": {"value": "reef", "confidence": "sure"}}')]))
    assert (unsure["break_type"]["value"], unsure["break_type"]["confidence"]) == ("reef", None)
    assert unsure["bottom"]["reason"] == "the answer has no bottom"
    refused = rb.judge_recall(_response([], stop="refusal"))
    assert refused["reason"] == "the model declined to answer (stop_reason refusal)"


def test_agreement_needs_both_answers_to_give_a_value():
    assert rb.agreement("reef", "reef") == "yes"
    assert rb.agreement("reef", "beach") == "no"
    assert rb.agreement("unknown", "reef") == "n/a"
    assert rb.agreement("reef", "unknown") == "n/a"


def test_the_answers_are_compared_on_each_value_and_on_the_sand_flag():
    research = _judge(_answer())  # reef, on rock: not a sand bottom
    # Rock against coral: the bottoms differ, but both are fixed, so the flag agrees.
    assert rb.agreements(research, rb.judge_recall(_recall("point", "coral"))) == {
        "break_type": "no", "bottom": "no", "sand_bottom": "yes"}
    assert rb.agreements(research, rb.judge_recall(_recall("reef", "sand"))) == {
        "break_type": "yes", "bottom": "no", "sand_bottom": "no"}
    assert rb.agreements(research, rb.judge_recall(_recall("unknown", "unknown"))) == {
        "break_type": "n/a", "bottom": "n/a", "sand_bottom": "n/a"}


# --- 7. one follow-up after a failed fetch ----------------------------------------------------

def test_the_follow_up_message_says_which_fetch_failed_why_and_what_is_allowed():
    assert rb.follow_up_message([{"url": GUESSED_URL, "error": "url_not_in_prior_context"}],
                                ["break_type", "bottom"]) == FOLLOW_UP_TEXT
    two = rb.follow_up_message([{"url": "https://a.example/x", "error": "url_not_accessible"},
                                {"url": None, "error": "brand_new_code"}], ["bottom"])
    assert two.splitlines()[:3] == ["These fetches did not work:",
                                    "- https://a.example/x: the site did not return the page",
                                    "- (no URL): error brand_new_code"]
    assert "Your answer for the bottom is still unknown." in two


def _failed_then_found(second_content, second_usage=None):
    """A client whose first research turn fails to fetch the URL the model typed in and
    answers unknown, and whose second turn is *second_content*."""
    first = ([{"type": "thinking", "thinking": "", "signature": "sig"}]
             + _search("s1", "Banzai Pipeline Hawaii surf", [OTHER_URL])
             + _fetch_failed("f1", GUESSED_URL)
             + [_answer(bt=UNKNOWN, bottom=UNKNOWN, pages=[], note="The fetch failed.")])

    def respond(request, n):
        if "tools" not in request:
            return _recall()
        if n == 1:
            return _response(first, usage=_usage(1000, 100, searches=1, fetches=1))
        return _response(second_content, usage=second_usage or _usage(2000, 100, fetches=1))
    return FakeClient(respond), first


def test_a_failed_fetch_that_leaves_values_unknown_gets_one_follow_up():
    found = (_fetch("f2", GUESSED_URL, PAGE)
             + [_answer(bt={"source_url": GUESSED_URL}, bottom={"source_url": GUESSED_URL},
                        pages=[dict(PAGE_ENTRY, url=GUESSED_URL)])])
    client, first = _failed_then_found(found)
    record, exc = rb.research_spot(client, PIPELINE_ENTRY, SPOT, "medium", rb.Budget(5.0),
                                   NO_SLEEP)
    assert exc is None
    asked = [call for call in client.calls if "tools" in call]
    assert len(asked) == 2
    # The same system and tools, and the conversation only appended to: the earlier turn
    # sent back unchanged, then the one follow-up message.
    assert asked[1]["system"] == asked[0]["system"]
    assert asked[1]["tools"] == asked[0]["tools"] == [SEARCH_TOOL, FETCH_TOOL]
    assert asked[1]["messages"] == [{"role": "user", "content": PIPELINE_PROMPT},
                                    {"role": "assistant", "content": first},
                                    {"role": "user", "content": FOLLOW_UP_TEXT}]
    research = record["research"]
    assert research["break_type"]["status"] == research["bottom"]["status"] == "researched"
    assert research["sand_bottom"] == "no"
    assert research["follow_up"] == {
        "after": [{"url": GUESSED_URL, "error": "url_not_in_prior_context"}],
        "needed": ["break_type", "bottom"], "searches": 0, "fetches": 1,
        "filled": ["break_type", "bottom"]}
    # 1,000 + 2,000 in x $2/M = $0.0060; 200 out x $10/M = $0.0020; 1 search = $0.0100.
    assert record["research_usage"]["cost_usd"] == pytest.approx(0.0180)
    assert record["research_usage"]["requests"] == 2
    assert record["done"] is True


def test_the_follow_up_only_fills_values_the_first_turn_left_unknown():
    later = "https://www.example.org/pipeline"
    later_page = ("Banzai Pipeline is a famous point break on Oahu, Hawaii. "
                  "It breaks over a shallow coral reef.")
    first = (_search("s1", "q", [URL, OTHER_URL]) + _fetch("f1", URL, PAGE)
             + _fetch_failed("f2", OTHER_URL, "url_not_accessible")
             + [_answer(bottom=UNKNOWN)])
    second = (_fetch("f3", later, later_page)
              + [_answer(bt={"value": "point", "all": ["point"], "source_url": later,
                             "quote": "Banzai Pipeline is a famous point break"},
                         bottom={"value": "coral", "all": ["coral"], "source_url": later,
                                 "quote": "It breaks over a shallow coral reef"},
                         pages=[dict(PAGE_ENTRY, url=later)])])

    def respond(request, n):
        if "tools" not in request:
            return _recall()
        return _response(first if n == 1 else second)
    record, exc = rb.research_spot(FakeClient(respond), PIPELINE_ENTRY, SPOT, "medium",
                                   rb.Budget(5.0), NO_SLEEP)
    assert exc is None
    research = record["research"]
    # The break type the first turn researched stays; the follow-up fills the bottom.
    assert (research["break_type"]["value"], research["break_type"]["source_url"]) == (
        "reef", URL)
    assert (research["bottom"]["value"], research["bottom"]["source_url"]) == ("coral", later)
    assert research["follow_up"]["needed"] == ["bottom"]
    assert research["follow_up"]["filled"] == ["bottom"]
    assert research["sand_bottom"] == "no"


def test_the_sand_flag_follows_the_bottom_that_is_kept():
    sand_page = "Banzai Pipeline, Oahu, Hawaii. The wave breaks over shifting sandbars."
    first = (_search("s1", "q", [URL]) + _fetch("f1", URL, sand_page)
             + _fetch_failed("f2", GUESSED_URL)
             + [_answer(bt=UNKNOWN, bottom={"value": "sand", "all": ["sand"],
                                             "quote": "The wave breaks over shifting sandbars"})])
    later = "https://www.example.org/pipeline"
    second = (_fetch("f3", later, PAGE)
              + [_answer(bt={"source_url": later}, bottom={"source_url": later},
                         pages=[dict(PAGE_ENTRY, url=later)])])

    def respond(request, n):
        if "tools" not in request:
            return _recall()
        return _response(first if n == 1 else second)
    record, exc = rb.research_spot(FakeClient(respond), PIPELINE_ENTRY, SPOT, "medium",
                                   rb.Budget(5.0), NO_SLEEP)
    assert exc is None
    research = record["research"]
    # The first turn's sand bottom stays, and the flag follows it, not the follow-up's rock.
    assert (research["bottom"]["value"], research["sand_bottom"]) == ("sand", "yes")
    assert research["break_type"]["value"] == "reef"
    assert research["follow_up"]["filled"] == ["break_type"]


def test_there_is_only_one_follow_up():
    # The follow-up fails too and the values stay unknown: no second follow-up.
    again = _fetch_failed("f2", GUESSED_URL, "url_not_accessible") + [
        _answer(bt=UNKNOWN, bottom=UNKNOWN, pages=[])]
    client, _ = _failed_then_found(again)
    record, exc = rb.research_spot(client, PIPELINE_ENTRY, SPOT, "medium", rb.Budget(5.0),
                                   NO_SLEEP)
    assert exc is None and record["done"] is True
    assert sum(1 for call in client.calls if "tools" in call) == 2
    assert record["research"]["follow_up"]["filled"] == []
    assert record["research"]["failed_fetches"] == [
        {"url": GUESSED_URL, "error": "url_not_in_prior_context"},
        {"url": GUESSED_URL, "error": "url_not_accessible"}]


def test_no_follow_up_without_a_failed_fetch_or_once_every_value_is_researched():
    def ask(content):
        client = FakeClient(lambda request, n: _response(content) if "tools" in request
                            else _recall())
        record, _ = rb.research_spot(client, PIPELINE_ENTRY, SPOT, "medium", rb.Budget(5.0),
                                     NO_SLEEP)
        return record, sum(1 for call in client.calls if "tools" in call)
    # Unknown, but nothing failed: the pages simply did not say.
    record, research_requests = ask(_search("s1", "q", [URL])
                                    + [_answer(bt=UNKNOWN, bottom=UNKNOWN)])
    assert research_requests == 1 and record["research"]["follow_up"] is None
    # A fetch failed, but both values were researched anyway.
    record, research_requests = ask(_search("s1", "q", [URL]) + _fetch_failed("f1", GUESSED_URL)
                                    + _fetch("f2", URL, PAGE) + [_answer()])
    assert research_requests == 1 and record["research"]["follow_up"] is None


def test_no_follow_up_after_a_turn_that_did_not_end_normally():
    for stop in ("max_tokens", "refusal"):
        content = (_search("s1", "q", [URL]) + _fetch_failed("f1", GUESSED_URL)
                   + [_text("{\"break_type\": {\"value\": \"re")])
        client = FakeClient(lambda request, n: _response(content, stop=stop)
                            if "tools" in request else _recall())
        record, _ = rb.research_spot(client, PIPELINE_ENTRY, SPOT, "medium", rb.Budget(5.0),
                                     NO_SLEEP)
        assert sum(1 for call in client.calls if "tools" in call) == 1, stop
        assert record["research"]["follow_up"] is None, stop


def test_a_follow_up_that_makes_more_than_one_call_is_flagged():
    found = (_search("s2", "Pipeline surf guide", [URL]) + _fetch("f2", URL, PAGE)
             + [_answer()])
    client, _ = _failed_then_found(found)
    record, exc = rb.research_spot(client, PIPELINE_ENTRY, SPOT, "medium", rb.Budget(5.0),
                                   NO_SLEEP)
    assert exc is None
    assert (record["research"]["follow_up"]["searches"],
            record["research"]["follow_up"]["fetches"]) == (1, 1)
    results = rb.new_results("medium", 5.0)
    results["spots"]["Banzai Pipeline|Hawaii"] = record
    row = [line for line in rb.render_report(results).splitlines()
           if line.startswith("| Banzai Pipeline | 1 |")]
    assert row and ("after url_not_in_prior_context: 1 search, 1 fetch; filled break_type "
                    "and bottom · more than the one call allowed") in row[0]


def test_the_follow_up_is_charged_to_the_budget_and_stops_with_it():
    # Cap $1.00: the spot may start (0 + 0.50 <= 1). Its first research turn costs
    # 500,000 in x $2/M = $1.00, so the follow-up would start with the cap spent.
    first = (_search("s1", "q", [URL]) + _fetch_failed("f1", GUESSED_URL)
             + [_answer(bt=UNKNOWN, bottom=UNKNOWN)])
    client = FakeClient(lambda request, n: _response(first, usage=_usage(500_000))
                        if "tools" in request else _recall())
    results = rb.new_results("medium", 1.0)
    stopped = rb.run(client, rb.resolve_pilot(ROSTER), results, rb.Budget(1.0), "medium",
                     pause_seconds=0, sleep=NO_SLEEP)
    assert stopped == "budget" and len(client.calls) == 1
    record = results["spots"]["Banzai Pipeline|Hawaii"]
    assert record["error"] == "BudgetExhausted: spent $1.0000 of the $1.00 budget"
    assert record["done"] is False and record["cost_usd"] == pytest.approx(1.00)


# --- 8. cost and budget ----------------------------------------------------------------------

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


def test_the_default_budget_is_four_dollars():
    assert rb.PILOT_BUDGET_USD == 4.00
    assert rb._parse_args([]).budget == 4.00


def _dollar_responses(research_usd, recall_usd, research_stop="end_turn"):
    """A client whose research request costs research_usd and memory request recall_usd,
    all of it input tokens at $2 per million."""
    def respond(request, n):
        if "tools" in request:
            return _response([_answer(bt=UNKNOWN, bottom=UNKNOWN)], stop=research_stop,
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
    assert record["research"]["break_type"]["status"] == "researched"
    assert record["research"]["bottom"]["status"] == "researched"
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


def test_the_settings_cover_the_prompts_and_the_follow_up_wording(monkeypatch):
    before = rb.run_settings("medium")
    assert before["tools"] == [SEARCH_TOOL, FETCH_TOOL]
    monkeypatch.setitem(rb.FETCH_ERRORS, "url_not_accessible", "the page did not load")
    assert rb.run_settings("medium")["prompts_sha256"] != before["prompts_sha256"]


def test_a_results_file_from_other_settings_is_not_resumed(tmp_path):
    path = tmp_path / "results.json"
    rb.save_results(path, rb.new_results("medium", 5.0))
    with pytest.raises(SystemExit, match="different settings"):
        rb.load_results(path, "high", 5.0, fresh=False)
    assert rb.load_results(path, "high", 5.0, fresh=True)["settings"]["effort"] == "high"
    # Nor is an earlier schema's file: the first gate runs' (schema 3) saved no pages.
    old = rb.new_results("medium", 5.0)
    old["schema"] = 3
    rb.save_results(path, old)
    with pytest.raises(SystemExit, match="has results schema 3; this version writes schema 4"):
        rb.load_results(path, "medium", 5.0, fresh=False)


def test_the_full_roster_estimate_is_the_pilots_mean_and_dearest_times_646():
    # mean 0.20 x 646 = 129.20; dearest 0.30 x 646 = 193.80
    figures = rb.estimate([0.10, 0.30, 0.20])
    assert figures["mean"] == pytest.approx(0.20)
    assert figures["median"] == pytest.approx(0.20)
    assert (figures["min"], figures["max"]) == (0.10, 0.30)
    assert figures["full_at_mean"] == pytest.approx(129.20)
    assert figures["full_at_max"] == pytest.approx(193.80)


# --- 9. what it may write ----------------------------------------------------------------------

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
    assert saved["schema"] == 4
    assert [r["done"] for r in saved["spots"].values()] == [True, True]
    assert [r["research"]["sand_bottom"] for r in saved["spots"].values()] == ["no", "no"]


def test_the_earlier_runs_results_are_left_where_they_are():
    assert rb.DEFAULT_OUTPUT == config.PIPELINE_DIR / "data" / "break_type_research_gate3.json"
    assert rb.DEFAULT_OUTPUT.name not in ("break_type_research_pilot.json",
                                          "break_type_research_pilot2.json",
                                          "break_type_research_gate.json",
                                          "break_type_research_gate2.json")


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
    assert "Your answer for the bottom is still unknown." in printed
    assert not output.exists()
    with pytest.raises(SystemExit, match="no results file"):
        rb.main(["--report", "--output", str(output)], client=never)
    # The first pilot's file has schema 1, which this report cannot read.
    output.write_text(json.dumps({"schema": 1, "spots": {}}))
    with pytest.raises(SystemExit, match="schema 1"):
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


def test_the_bottom_has_no_column_anywhere():
    # The results file only, for now: no migration adds a bottom column and the import
    # never sends one.
    migrations = os.path.join(ROOT, "pipeline", "migrations")
    for name in sorted(os.listdir(migrations)):
        with open(os.path.join(migrations, name), encoding="utf-8") as fh:
            assert not re.search(r"ADD\s+COLUMN[^;]*\bbottom", fh.read(), re.I), name
    with open(os.path.join(ROOT, "pipeline", "db_import.py"), encoding="utf-8") as fh:
        assert "bottom" not in fh.read()


# --- the report -----------------------------------------------------------------------------

def test_the_report_rows():
    record, exc = rb.research_spot(_good_client(), PIPELINE_ENTRY, SPOT, "medium",
                                   rb.Budget(5.0), NO_SLEEP)
    assert exc is None
    results = rb.new_results("medium", 5.0)
    results["spots"]["Banzai Pipeline|Hawaii"] = record
    results["spent_usd"] = record["cost_usd"]
    report = rb.render_report(results).splitlines()
    assert report[:5] == [
        "Break type:", "",
        "| Spot | Current value | Researched break type | Source URL | Location check "
        "| Memory answer | Agree |",
        "|---|---|---|---|---|---|---|",
        "| Banzai Pipeline | beach | reef | https://www.surf-forecast.com/breaks/Banzai-Pipeline "
        "| ok (Hawaii) | reef, high confidence | yes |"]
    assert report[5] == "| Waimea Bay | — | not run | — | — | — | — |"
    assert report[20] == "| Mavericks (control: reef) | — | not run | — | — | — | — |"
    bottom = report.index("Bottom (in this results file only):")
    assert report[bottom + 2:bottom + 5] == [
        "| Spot | Researched bottom | Source URL | Location check | Memory answer | Agree "
        "| Sand bottom: researched | Sand bottom: memory | Sand bottom: agree |",
        "|---|---|---|---|---|---|---|---|---|",
        "| Banzai Pipeline | rock | https://www.surf-forecast.com/breaks/Banzai-Pipeline "
        "| ok (Hawaii) | rock, high confidence | yes | no | no | yes |"]
    assert report[bottom + 5] == "| Waimea Bay | not run | — | — | — | — | — | — | — |"
    # Research: 12,000 in x $2/M + 900 out x $10/M + 1 search x $0.01
    #   = 0.0240 + 0.0090 + 0.0100 = $0.0430.
    # Memory: 300 in x $2/M + 50 out x $10/M = 0.0006 + 0.0005 = $0.0011.
    # Fetched text: PAGE is 227 characters, 227 // 4 = 56 tokens.
    cost = report.index("Cost:")
    assert report[cost + 4] == ("| Banzai Pipeline | 1 | 1 | 1 page, about 56 tokens | — "
                                "| 12,300 | 950 | $0.0430 | $0.0011 | $0.0441 |")
    # 0.0441 x 646 = 28.4886
    gate = report.index("The gate for the full run:")
    assert report[gate - 1] == "" and report[-1] == "GATE: FAIL"
    assert "PASS  Banzai Pipeline is not unknown; researched reef" in report[gate:]
    assert report[gate - 6:gate - 1] == [
        "Spent $0.0441 of the $5.00 budget, at list prices.",
        "Per spot, over 1 finished: mean $0.0441, median $0.0441, cheapest $0.0441, "
        "dearest $0.0441.",
        "The full 646 spots at the pilot's mean: $28.49; at its dearest spot's cost: $28.49.",
        "Sand bottom, researched: 0 yes, 1 no, 0 unknown; from memory: 0 yes, 1 no, "
        "0 unknown.",
        "Where both answers say yes or no, they agree on 1 of 1."]


def test_the_report_counts_follow_ups_and_the_values_they_gained():
    found = (_fetch("f2", GUESSED_URL, PAGE)
             + [_answer(bt={"source_url": GUESSED_URL}, bottom={"source_url": GUESSED_URL},
                        pages=[dict(PAGE_ENTRY, url=GUESSED_URL)])])
    client, _ = _failed_then_found(found)
    record, _ = rb.research_spot(client, PIPELINE_ENTRY, SPOT, "medium", rb.Budget(5.0),
                                 NO_SLEEP)
    results = rb.new_results("medium", 5.0)
    results["spots"]["Banzai Pipeline|Hawaii"] = record
    report = rb.render_report(results).splitlines()
    gate = report.index("The gate for the full run:")
    assert report[gate - 2] == ("Followed up a failed fetch at 1 of 1 spots; 1 of them gained "
                                "a value.")
    assert ("after url_not_in_prior_context: 0 searches, 1 fetch; filled break_type and "
            "bottom |") in [line for line in report if line.startswith("| Banzai Pipeline | 1 |")][0]


# --- the gate for the full run -------------------------------------------------------------

GATE_KEYS = {"Mavericks": "Mavericks, California|California",
             "Malibu": "Malibu Surfrider Beach|California", "Zuma": "Zuma Beach|California",
             "Steamer": "Steamer Lane|California", "Pipeline": "Banzai Pipeline|Hawaii",
             "Jaws": "Peahi Jaws|Hawaii", "Waimea": "Waimea Bay|Hawaii",
             "Tres": "Tres Palmas|Puerto Rico", "3 Mile": "3 Mile|California",
             "Refugio": "Refugio State Beach|California", "Tourmaline": "Tourmaline|California",
             "Lake Worth": "Lake Worth Pier|Florida", "Honolua": "Honolua Bay|Hawaii"}


def _gate_results(**changes):
    """Every pilot spot finished: the controls right and every other spot researched as a
    reef, except *changes*, by GATE_KEYS name: a value, 'unknown', 'unfinished' or None
    for not run."""
    values = {"Mavericks, California": "reef", "Malibu Surfrider Beach": "point",
              "Zuma Beach": "beach", "Steamer Lane": "point"}
    spots = {}
    for entry in rb.PILOT_SPOTS:
        spots[f"{entry[0]}|{entry[1]}"] = values.get(entry[0], "reef")
    for name, value in changes.items():
        spots[GATE_KEYS[name.replace("_", " ")]] = value
    results = {"spots": {}}
    for key, value in spots.items():
        if value is None:
            continue
        status = "unknown" if value in ("unknown", "unfinished") else "researched"
        results["spots"][key] = {
            "done": value != "unfinished",
            "research": {"break_type": {"status": status,
                                        "value": "unknown" if status == "unknown" else value}}}
    return results


GATE_PASSED = [
    "PASS  control Mavericks, California: reef; researched reef",
    "PASS  control Malibu Surfrider Beach: point; researched point",
    "PASS  control Zuma Beach: beach; researched beach",
    "PASS  control Steamer Lane: point or reef; researched point",
    "PASS  Banzai Pipeline is not unknown; researched reef",
    "PASS  Peahi Jaws is not unknown; researched reef",
    "PASS  Waimea Bay is not unknown; researched reef",
    "PASS  Tres Palmas is not unknown; researched reef",
]


def test_the_gate_passes_with_the_controls_right_and_four_unknown():
    passed, lines = rb.gate_verdict(_gate_results(**{"3_Mile": "unknown", "Refugio": "unknown",
                                                     "Tourmaline": "unknown",
                                                     "Lake_Worth": "unknown"}))
    assert passed is True
    assert lines == GATE_PASSED + [
        "PASS  no more than 4 of the 20 unknown for break type; 4 are: researched as unknown: "
        "3 Mile, Refugio State Beach, Tourmaline, Lake Worth Pier",
        "GATE: PASS"]


def test_five_unknown_fail_the_gate():
    passed, lines = rb.gate_verdict(_gate_results(**{"3_Mile": "unknown", "Refugio": "unknown",
                                                     "Tourmaline": "unknown",
                                                     "Lake_Worth": "unknown",
                                                     "Honolua": "unknown"}))
    assert passed is False
    assert lines == GATE_PASSED + [
        "FAIL  no more than 4 of the 20 unknown for break type; 5 are: researched as unknown: "
        "Honolua Bay, 3 Mile, Refugio State Beach, Tourmaline, Lake Worth Pier",
        "GATE: FAIL"]


def test_steamer_lane_may_be_a_point_or_a_reef_and_nothing_else():
    assert rb.gate_verdict(_gate_results(Steamer="reef"))[0] is True
    passed, lines = rb.gate_verdict(_gate_results(Steamer="beach"))
    assert passed is False
    assert lines[3] == "FAIL  control Steamer Lane: point or reef; researched beach"
    passed, lines = rb.gate_verdict(_gate_results(Steamer="unknown"))
    assert lines[3] == "FAIL  control Steamer Lane: point or reef; researched unknown"


@pytest.mark.parametrize("name, value, line", [
    ("Mavericks", "point", "FAIL  control Mavericks, California: reef; researched point"),
    ("Malibu", "beach", "FAIL  control Malibu Surfrider Beach: point; researched beach"),
    ("Zuma", "reef", "FAIL  control Zuma Beach: beach; researched reef"),
])
def test_each_other_control_has_one_right_answer(name, value, line):
    passed, lines = rb.gate_verdict(_gate_results(**{name: value}))
    assert passed is False and line in lines


@pytest.mark.parametrize("name, line", [
    ("Pipeline", "FAIL  Banzai Pipeline is not unknown; researched unknown"),
    ("Jaws", "FAIL  Peahi Jaws is not unknown; researched unknown"),
    ("Waimea", "FAIL  Waimea Bay is not unknown; researched unknown"),
    ("Tres", "FAIL  Tres Palmas is not unknown; researched unknown"),
])
def test_each_famous_break_left_unknown_fails_the_gate_alone(name, line):
    # One unknown is well inside the count of four, so this rule alone fails it.
    passed, lines = rb.gate_verdict(_gate_results(**{name: "unknown"}))
    assert passed is False
    assert [entry for entry in lines if entry.startswith("FAIL")] == [line]


def test_spots_not_run_or_not_finished_count_as_unknown():
    passed, lines = rb.gate_verdict(_gate_results(**{"3_Mile": "unknown", "Refugio": None,
                                                     "Tourmaline": "unfinished",
                                                     "Lake_Worth": None, "Honolua": None}))
    assert passed is False
    assert lines[-2] == (
        "FAIL  no more than 4 of the 20 unknown for break type; 5 are: researched as unknown: "
        "3 Mile; not run or not finished, so counted as unknown: Honolua Bay, Refugio State "
        "Beach, Tourmaline, Lake Worth Pier")
    passed, lines = rb.gate_verdict(_gate_results(Pipeline=None))
    assert passed is False
    assert "FAIL  Banzai Pipeline is not unknown; researched unknown" in lines
    assert lines[-2] == ("PASS  no more than 4 of the 20 unknown for break type; 1 is: not "
                         "run or not finished, so counted as unknown: Banzai Pipeline")


def test_a_spot_whose_research_ended_but_whose_run_did_not_counts_as_unknown():
    # Pipeline's research turn came back reef, then its memory request failed: the spot is
    # not finished, so the next run researches it again, and the gate reads it as unknown.
    results = _gate_results()
    results["spots"][GATE_KEYS["Pipeline"]]["done"] = False
    passed, lines = rb.gate_verdict(results)
    assert passed is False
    assert lines[4] == "FAIL  Banzai Pipeline is not unknown; researched unknown"
    results = _gate_results()
    results["spots"][GATE_KEYS["Mavericks"]]["done"] = False
    assert rb.gate_verdict(results)[1][0] == (
        "FAIL  control Mavericks, California: reef; researched unknown")


def test_an_empty_results_file_fails_every_rule():
    passed, lines = rb.gate_verdict({"spots": {}})
    assert passed is False
    assert [line[:4] for line in lines[:-1]] == ["FAIL"] * 9
    assert lines[-1] == "GATE: FAIL"


def test_the_report_ends_with_the_gate(tmp_path, capsys):
    # A results file with nothing run yet: every spot counts as unknown.
    output = tmp_path / "gate.json"
    rb.save_results(output, rb.new_results("medium", 4.0))
    never = FakeClient(lambda request, n: pytest.fail("no request may be made"))
    assert rb.main(["--report", "--output", str(output)], client=never) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[-12:] == [
        "The gate for the full run:", "",
        "FAIL  control Mavericks, California: reef; researched unknown",
        "FAIL  control Malibu Surfrider Beach: point; researched unknown",
        "FAIL  control Zuma Beach: beach; researched unknown",
        "FAIL  control Steamer Lane: point or reef; researched unknown",
        "FAIL  Banzai Pipeline is not unknown; researched unknown",
        "FAIL  Peahi Jaws is not unknown; researched unknown",
        "FAIL  Waimea Bay is not unknown; researched unknown",
        "FAIL  Tres Palmas is not unknown; researched unknown",
        "FAIL  no more than 4 of the 20 unknown for break type; 20 are: not run or not "
        "finished, so counted as unknown: Banzai Pipeline, Waimea Bay, Peahi Jaws, Honolua "
        "Bay, Rincon Domes, Tres Palmas, Suicide's, Bombora, 3 Mile, Refugio State Beach, "
        "Tourmaline, Venice Beach Breakwater, Lower Trestles, Jacksonville Beach Pier, Lake "
        "Worth Pier, Manasquan Inlet, Mavericks, California, Steamer Lane, Malibu Surfrider "
        "Beach, Zuma Beach",
        "GATE: FAIL"]


def test_a_report_cell_cannot_break_the_table():
    assert rb._cell("reef | beach\nbreak") == "reef / beach break"


def test_the_report_says_why_a_value_was_withheld():
    elsewhere = [dict(PAGE_ENTRY, place="Santa Cruz", state="California",
                      location_quote="Suicides in Santa Cruz")]

    def respond(request, n):
        if "tools" in request:
            return _researched(_answer(pages=elsewhere,
                                       same_name_elsewhere=["Santa Cruz, California"]))[0]
        return _recall()
    record, _ = rb.research_spot(FakeClient(respond), PIPELINE_ENTRY, SPOT, "medium",
                                 rb.Budget(5.0), NO_SLEEP)
    results = rb.new_results("medium", 5.0)
    results["spots"]["Banzai Pipeline|Hawaii"] = record
    report = rb.render_report(results).splitlines()
    assert report[4] == (
        "| Banzai Pipeline | beach | unknown: the page describes another place: the page "
        "puts it in California | https://www.surf-forecast.com/breaks/Banzai-Pipeline "
        "| wrong place: the page puts it in California · name also used: Santa Cruz, "
        "California | reef, high confidence | n/a |")
    bottom = report.index("Bottom (in this results file only):")
    assert report[bottom + 4] == (
        "| Banzai Pipeline | unknown: the page describes another place: the page puts it in "
        "California | https://www.surf-forecast.com/breaks/Banzai-Pipeline | wrong place: the "
        "page puts it in California | rock, high confidence | n/a | unknown | no | n/a |")


# --- 10. the real SDK ---------------------------------------------------------------------------

def _sdk_client(handler):
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx")
    return anthropic.Anthropic(api_key="test-key", max_retries=0,
                               http_client=httpx.Client(transport=httpx.MockTransport(handler)))


def _sdk_message(content, usage):
    return {"id": "msg_test", "type": "message", "role": "assistant",
            "model": "claude-sonnet-5-5", "content": content, "stop_reason": "end_turn",
            "stop_sequence": None, "usage": usage}


def test_the_real_sdk_sends_this_request_and_reads_the_answer_back():
    httpx = pytest.importorskip("httpx")
    bodies = []

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "tools" in body:
            return httpx.Response(200, json=_sdk_message(
                _researched(_answer())[0]["content"],
                {"input_tokens": 12000, "output_tokens": 900,
                 "server_tool_use": {"web_search_requests": 1, "web_fetch_requests": 1}}))
        return httpx.Response(200, json=_sdk_message(_recall()["content"],
                                                     {"input_tokens": 300, "output_tokens": 50}))

    record, exc = rb.research_spot(_sdk_client(handler), PIPELINE_ENTRY, SPOT, "medium",
                                   rb.Budget(5.0), NO_SLEEP)
    assert exc is None, record["error"]
    research_body, recall_body = bodies
    for field in ("model", "max_tokens", "system", "tools", "thinking", "output_config",
                  "messages"):
        assert research_body[field] == RESEARCH_REQUEST[field], field
    assert "tools" not in recall_body
    assert recall_body["messages"] == RECALL_REQUEST["messages"]
    for ours in ("unattributed", "ROSTER-ONLY", "break_type_source"):
        assert ours not in json.dumps(bodies)
    assert record["research"]["break_type"]["status"] == "researched"
    assert record["research"]["bottom"]["quote_found_in"] == "fetched page"
    assert record["model_recall"]["break_type"]["value"] == "reef"
    assert record["agree"] == {"break_type": "yes", "bottom": "yes", "sand_bottom": "yes"}
    assert record["cost_usd"] == pytest.approx(0.0441)


def test_the_real_sdk_sends_the_failed_fetch_back_and_the_follow_up_after_it():
    # The error code goes back to the API exactly as it came, which is what an SDK older
    # than 0.105 warns about (its list of codes predates url_not_in_prior_context) without
    # changing what it sends.
    httpx = pytest.importorskip("httpx")
    bodies = []
    first = (_search("s1", "Banzai Pipeline Hawaii surf", [OTHER_URL])
             + _fetch_failed("f1", GUESSED_URL)
             + [_answer(bt=UNKNOWN, bottom=UNKNOWN, pages=[])])
    found = (_fetch("f2", GUESSED_URL, PAGE)
             + [_answer(bt={"source_url": GUESSED_URL}, bottom={"source_url": GUESSED_URL},
                        pages=[dict(PAGE_ENTRY, url=GUESSED_URL)])])

    def handler(request):
        body = json.loads(request.content)
        bodies.append(body)
        if "tools" not in body:
            return httpx.Response(200, json=_sdk_message(
                _recall()["content"], {"input_tokens": 300, "output_tokens": 50}))
        content = first if len(body["messages"]) == 1 else found
        return httpx.Response(200, json=_sdk_message(
            content, {"input_tokens": 1000, "output_tokens": 100}))

    record, exc = rb.research_spot(_sdk_client(handler), PIPELINE_ENTRY, SPOT, "medium",
                                   rb.Budget(5.0), NO_SLEEP)
    assert exc is None, record["error"]
    follow_up = bodies[1]
    assert [message["role"] for message in follow_up["messages"]] == ["user", "assistant",
                                                                      "user"]
    returned = [block for block in follow_up["messages"][1]["content"]
                if block["type"] == "web_fetch_tool_result"]
    assert returned == [{"type": "web_fetch_tool_result", "tool_use_id": "f1",
                         "content": {"type": "web_fetch_tool_result_error",
                                     "error_code": "url_not_in_prior_context"}}]
    assert follow_up["messages"][2]["content"] == FOLLOW_UP_TEXT
    assert record["research"]["follow_up"]["filled"] == ["break_type", "bottom"]
