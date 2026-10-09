"""Saved pages and --recheck: each research turn's responses are saved in the results file,
fetched pages' text included, and --recheck judges the saved answers again with no API calls.

WHAT THESE TESTS HOLD:

  1. a response is saved with every field the checks read, the fetched page's text among
     them, and without thinking, encrypted search content or usage;
  2. the record keeps the first turn and the follow-up, with what the follow-up asked for,
     and the spot as the model was told it;
  3. --recheck judges the saved answers again against the saved pages, makes no client and
     no request, writes a new file and leaves the one it read as it was;
  4. it changes only the judging: costs, the memory answer and the run's history stay, the
     agreement with memory is worked out again, and the follow-up's rule still holds;
  5. it reports each value that changed and the gate before and after;
  6. it refuses a file made before pages were saved, a memory-only file, and any file it
     must not write over.

No expected value below is produced by calling the code under test.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from pipeline import config
from pipeline import research_break_type as rb

with open(config.DEFAULT_ENRICHED_OUTPUT, encoding="utf-8") as _fh:
    ROSTER = json.load(_fh)

NO_SLEEP = lambda seconds: None  # noqa: E731

WIKI = "https://en.wikipedia.org/wiki/Banzai_Pipeline"
GUIDE = "https://www.surfguide.com/spots/banzai-pipeline"
GUESSED = "https://www.surfguide.com/spots/pipeline"
PDF = "https://www.example.org/north-shore-breaks.pdf"
# The gate run's Pipeline quote, exactly as the model gave it: it stops inside a link.
PIPELINE_QUOTE = ("The Banzai Pipeline, or simply Pipeline or Pipe, is a\n"
                  "[surf](https://en.wikipedia.org/wiki/Surfing) reef break located in [Hawaii]")
LAVA_QUOTE = "There are also several jagged, underwater lava spires that can injure fallen surfers."
WIKI_PAGE = ("The Banzai Pipeline, or simply Pipeline or Pipe, is a\n"
             "[surf](https://en.wikipedia.org/wiki/Surfing) reef break located in "
             "[Hawaii](https://en.wikipedia.org/wiki/Hawaii), off Ehukai Beach Park. " + LAVA_QUOTE)
WIKI_ENTRY = {"url": WIKI, "place": "off Ehukai Beach Park", "state": "Hawaii", "lat": None,
              "lng": None, "location_quote": "located in Hawaii"}
SPOT = {"name": "Banzai Pipeline", "region_hint": "Hawaii", "lat": 21.664, "lng": -158.054,
        "break_type": "beach", "break_type_source": "unattributed"}
PIPELINE_ENTRY = rb.PILOT_SPOTS[0]
IDENTITY = {"name": "Banzai Pipeline", "region": "Hawaii", "lat": 21.664, "lng": -158.054}
# What the checks before word matching wrote for Pipeline's break type.
OLD_VERDICT = {"status": "unknown", "value": "unknown", "values": [], "mixed": False,
               "source_url": WIKI, "cited_url": None, "quote": None, "quote_found_in": None,
               "quote_names_value": None, "location": None,
               "reason": "the quote is not on the cited page"}


def _text(text, citations=None):
    block = {"type": "text", "text": text}
    if citations is not None:
        block["citations"] = citations
    return block


def _search(call_id, urls):
    return [{"type": "server_tool_use", "id": call_id, "name": "web_search",
             "input": {"query": "Banzai Pipeline surf"}},
            {"type": "web_search_tool_result", "tool_use_id": call_id,
             "content": [{"type": "web_search_result", "url": u, "title": "t",
                          "encrypted_content": "ENC", "page_age": None} for u in urls]}]


def _fetch(call_id, url, page):
    return [{"type": "server_tool_use", "id": call_id, "name": "web_fetch",
             "input": {"url": url}},
            {"type": "web_fetch_tool_result", "tool_use_id": call_id,
             "content": {"type": "web_fetch_result", "url": url,
                         "retrieved_at": "2026-10-09T12:00:00Z",
                         "content": {"type": "document", "title": "a page",
                                     "citations": {"enabled": True},
                                     "source": {"type": "text", "media_type": "text/plain",
                                                "data": page}}}}]


def _fetch_failed(call_id, url, code="url_not_in_prior_context"):
    return [{"type": "server_tool_use", "id": call_id, "name": "web_fetch",
             "input": {"url": url}},
            {"type": "web_fetch_tool_result", "tool_use_id": call_id,
             "content": {"type": "web_fetch_tool_result_error", "error_code": code}}]


def _answer(break_type, bottom, pages):
    return _text("```json\n" + json.dumps({
        "break_type": break_type, "bottom": bottom, "pages": pages,
        "same_name_elsewhere": [], "note": None}) + "\n```")


def _claim(value, url, quote):
    return {"value": value, "all": [value], "source_url": url, "quote": quote}


UNKNOWN = {"value": "unknown", "all": [], "source_url": None, "quote": None}


def _recall(kind="reef", bottom="rock"):
    return _text(json.dumps({"break_type": {"value": kind, "all": [kind], "confidence": "high"},
                             "bottom": {"value": bottom, "all": [bottom],
                                        "confidence": "high"}, "note": None}))


class FakeClient:
    def __init__(self, respond):
        self.calls = []
        self.messages = self
        self._respond = respond

    def create(self, **request):
        self.calls.append(request)
        return self._respond(request, len(self.calls))


def _never():
    return FakeClient(lambda request, n: pytest.fail("a recheck may make no request"))


# --- 1. what is saved -------------------------------------------------------------------------

def test_a_response_is_saved_with_what_the_checks_read_and_nothing_else():
    citation = {"type": "web_search_result_location", "url": GUIDE, "title": "guide",
                "cited_text": "Pipeline is a reef break", "encrypted_index": "IDX"}
    paused = {"id": "msg_1", "type": "message", "role": "assistant", "model": "m",
              "stop_reason": "pause_turn", "usage": {"input_tokens": 9, "output_tokens": 9},
              "content": [
                  {"type": "thinking", "thinking": "private", "signature": "sig"},
                  {"type": "server_tool_use", "id": "s1", "name": "web_search",
                   "input": {"query": "q"}, "caller": {"type": "direct"}},
                  {"type": "web_search_tool_result", "tool_use_id": "s1",
                   "content": {"type": "web_search_tool_result_error",
                               "error_code": "max_uses_exceeded"}}]}
    final = {"stop_reason": "end_turn", "usage": {"input_tokens": 9, "output_tokens": 9},
             "content": [
                 {"type": "redacted_thinking", "data": "xyz"},
                 {"type": "web_search_tool_result", "tool_use_id": "s2",
                  "content": [{"type": "web_search_result", "url": WIKI, "title": "Wiki",
                               "encrypted_content": "ENC", "page_age": "2 days"}]},
                 *_fetch("f1", WIKI, WIKI_PAGE),
                 {"type": "web_fetch_tool_result", "tool_use_id": "f2",
                  "content": {"type": "web_fetch_result", "url": PDF, "retrieved_at": "r",
                              "content": {"type": "document", "title": None,
                                          "source": {"type": "base64",
                                                     "media_type": "application/pdf",
                                                     "data": "JVBERi0xLjQK"}}}},
                 *_fetch_failed("f3", GUESSED, "url_not_accessible")[1:],
                 _text("The guide says reef.", [citation]),
                 {"type": "text", "text": "No citations here.", "citations": None}]}
    assert rb.saved_responses([paused, final]) == [
        {"stop_reason": "pause_turn", "content": [
            {"type": "server_tool_use", "id": "s1", "name": "web_search",
             "input": {"query": "q"}},
            {"type": "web_search_tool_result", "tool_use_id": "s1",
             "content": {"type": "web_search_tool_result_error",
                         "error_code": "max_uses_exceeded"}}]},
        {"stop_reason": "end_turn", "content": [
            {"type": "web_search_tool_result", "tool_use_id": "s2",
             "content": [{"type": "web_search_result", "url": WIKI, "title": "Wiki"}]},
            {"type": "server_tool_use", "id": "f1", "name": "web_fetch", "input": {"url": WIKI}},
            {"type": "web_fetch_tool_result", "tool_use_id": "f1",
             "content": {"type": "web_fetch_result", "url": WIKI,
                         "retrieved_at": "2026-10-09T12:00:00Z",
                         "content": {"type": "document", "title": "a page",
                                     "source": {"type": "text", "media_type": "text/plain",
                                                "data": WIKI_PAGE}}}},
            {"type": "web_fetch_tool_result", "tool_use_id": "f2",
             "content": {"type": "web_fetch_result", "url": PDF, "retrieved_at": "r",
                         "content": {"type": "document", "title": None,
                                     "source": {"type": "base64",
                                                "media_type": "application/pdf"}}}},
            {"type": "web_fetch_tool_result", "tool_use_id": "f3",
             "content": {"type": "web_fetch_tool_result_error",
                         "error_code": "url_not_accessible"}},
            {"type": "text", "text": "The guide says reef.",
             "citations": [{"type": "web_search_result_location", "url": GUIDE,
                            "title": "guide", "cited_text": "Pipeline is a reef break"}]},
            {"type": "text", "text": "No citations here."}]},
    ]


def test_the_saved_copy_holds_everything_the_checks_need():
    # The break type stands on a search citation from a page whose fetch failed; the bottom
    # on the fetched page's text; a PDF and the failed fetch are recorded. All read back
    # from the saved copy alone.
    citation = {"type": "web_search_result_location", "url": GUIDE, "title": "guide",
                "cited_text": "Pipeline is a reef break on the North Shore",
                "encrypted_index": "IDX"}
    response = {"stop_reason": "end_turn", "content": [
        *_search("s1", [WIKI, GUIDE]), *_fetch("f1", WIKI, WIKI_PAGE),
        *_fetch_failed("f2", GUIDE, "url_not_accessible"),
        _text("The guide says reef.", [citation]),
        _answer(_claim("reef", GUIDE, "Pipeline is a reef break"),
                _claim("rock", WIKI, LAVA_QUOTE),
                [WIKI_ENTRY, dict(WIKI_ENTRY, url=GUIDE)])]}
    research = rb.judge_research(rb.saved_responses([response]), IDENTITY)
    kind, bottom = research["break_type"], research["bottom"]
    assert (kind["status"], kind["value"], kind["source_url"], kind["quote_found_in"]) == (
        "researched", "reef", GUIDE, "search citation")
    assert (bottom["status"], bottom["value"], bottom["source_url"],
            bottom["quote_found_in"]) == ("researched", "rock", WIKI, "fetched page")
    # WIKI_PAGE, piece by piece: the opening sentence to "is a" and its newline, 54; the
    # surf link and " reef break located in ", 45 + 23 = 68; the Hawaii link and ", off
    # Ehukai Beach Park. ", 46 + 25 = 71; LAVA_QUOTE, 85. 54 + 68 + 71 + 85 = 278.
    assert research["fetched_pages"] == [{"url": WIKI, "kind": "text", "chars": 278}]
    assert research["failed_fetches"] == [{"url": GUIDE, "error": "url_not_accessible"}]
    assert research["search_result_urls"] == [WIKI, GUIDE]


# --- 2. what the record keeps --------------------------------------------------------------------

def _pipeline_client(research_usage=31_250, recall_usage=15_625):
    """Pipeline from Wikipedia: the cut-off-link quote for the break type, the lava quote for
    the bottom. Research costs 31,250 in x $2/M = $0.0625; memory 15,625 in = $0.03125."""
    def respond(request, n):
        if "tools" not in request:
            return {"content": [_recall()], "stop_reason": "end_turn",
                    "usage": {"input_tokens": recall_usage, "output_tokens": 0}}
        return {"content": [*_search("s1", [WIKI]), *_fetch("f1", WIKI, WIKI_PAGE),
                            _answer(_claim("reef", WIKI, PIPELINE_QUOTE),
                                    _claim("rock", WIKI, LAVA_QUOTE), [WIKI_ENTRY])],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": research_usage, "output_tokens": 0}}
    return FakeClient(respond)


def test_the_record_keeps_the_spot_as_told_and_each_turn_with_its_pages():
    first = {"content": [*_search("s1", [GUIDE]), *_fetch_failed("f1", GUESSED),
                         _answer(UNKNOWN, UNKNOWN, [])],
             "stop_reason": "end_turn", "usage": {"input_tokens": 0, "output_tokens": 0}}
    page = "Pipeline is a reef break over a shallow ledge of lava rock. Location: Hawaii."
    quote = "Pipeline is a reef break over a shallow ledge of lava rock"
    second = {"content": [*_fetch("f2", GUESSED, page),
                          _answer(_claim("reef", GUESSED, quote), _claim("rock", GUESSED, quote),
                                  [dict(WIKI_ENTRY, url=GUESSED)])],
              "stop_reason": "end_turn", "usage": {"input_tokens": 0, "output_tokens": 0}}

    def respond(request, n):
        if "tools" not in request:
            return {"content": [_recall()], "stop_reason": "end_turn",
                    "usage": {"input_tokens": 0, "output_tokens": 0}}
        return first if n == 1 else second
    record, exc = rb.research_spot(FakeClient(respond), PIPELINE_ENTRY, SPOT, "medium",
                                   rb.Budget(5.0), NO_SLEEP)
    assert exc is None
    assert record["identity"] == IDENTITY
    saved = record["research_responses"]
    assert [block["type"] for block in saved["first"][0]["content"]] == [
        "server_tool_use", "web_search_tool_result", "server_tool_use",
        "web_fetch_tool_result", "text"]
    assert saved["follow_up"]["asked_for"] == ["break_type", "bottom"]
    fetched = saved["follow_up"]["responses"][0]["content"][1]
    assert fetched["content"]["content"]["source"]["data"] == page
    assert record["research"]["follow_up"]["filled"] == ["break_type", "bottom"]


def test_a_run_saves_each_fetched_pages_text_in_the_results_file(tmp_path):
    output = tmp_path / "gate.json"
    assert rb.main(["--output", str(output), "--limit", "1", "--pause", "0"],
                   client=_pipeline_client(), sleep=NO_SLEEP) == 0
    saved = json.loads(output.read_text(encoding="utf-8"))
    assert saved["schema"] == 4
    record = saved["spots"]["Banzai Pipeline|Hawaii"]
    blocks = record["research_responses"]["first"][0]["content"]
    assert blocks[3]["content"]["content"]["source"]["data"] == WIKI_PAGE
    assert record["research_responses"]["follow_up"] is None


# --- 3-5. --recheck ------------------------------------------------------------------------

def _old_pipeline_file(path):
    """A results file holding Pipeline as the checks before word matching left it: the
    break type unknown, so no agreement with memory on it."""
    record, exc = rb.research_spot(_pipeline_client(), PIPELINE_ENTRY, SPOT, "medium",
                                   rb.Budget(5.0), NO_SLEEP)
    assert exc is None
    record["research"]["break_type"] = dict(OLD_VERDICT)
    record["agree"]["break_type"] = "n/a"
    results = rb.new_results("medium", 4.0)
    results["spots"]["Banzai Pipeline|Hawaii"] = record
    results["spent_usd"] = 0.09375
    rb.save_results(path, results)
    return path


def test_a_recheck_judges_the_saved_answer_again_and_writes_a_new_file(tmp_path, capsys,
                                                                     monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    source = _old_pipeline_file(tmp_path / "gate.json")
    before = source.read_bytes()
    assert rb.main(["--recheck", "--output", str(source)], client=_never()) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[:11] == [
        "Rechecked 1 spot from gate.json against their saved responses and pages, with no "
        "API calls. Wrote gate.rechecked.json.",
        "Values changed by the recheck: 1.",
        "",
        "| Spot | Value | Before | After |",
        "|---|---|---|---|",
        "| Banzai Pipeline (Hawaii) | break type | unknown: the quote is not on the cited page "
        "| reef |",
        "",
        "Values unchanged, with a different reason, URL, quote or location: 0.",
        "The gate: FAIL before the recheck, FAIL after.",
        "",
        "Break type:"]
    assert ("| Banzai Pipeline | beach | reef | https://en.wikipedia.org/wiki/Banzai_Pipeline "
            "| ok (Hawaii) | reef, high confidence | yes |") in printed
    assert source.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gate.json", "gate.rechecked.json"]
    rechecked = json.loads((tmp_path / "gate.rechecked.json").read_text(encoding="utf-8"))
    record = rechecked["spots"]["Banzai Pipeline|Hawaii"]
    kind = record["research"]["break_type"]
    assert (kind["status"], kind["value"], kind["source_url"], kind["quote_found_in"]) == (
        "researched", "reef", WIKI, "fetched page")
    assert kind["location"]["verdict"] == "ok"
    assert record["agree"] == {"break_type": "yes", "bottom": "yes", "sand_bottom": "yes"}
    # Only the judging changes: what the run cost, the memory answer and its history stay.
    assert (record["cost_usd"], rechecked["spent_usd"]) == (0.09375, 0.09375)
    assert record["model_recall"]["break_type"]["value"] == "reef"
    assert record["done"] is True and record["error"] is None
    assert rechecked["runs"] == [] and rechecked["schema"] == 4
    assert [(r["from"], r["values_changed"]) for r in rechecked["rechecks"]] == [
        ("gate.json", 1)]


def test_a_recheck_of_a_rechecked_file_changes_nothing(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    source = _old_pipeline_file(tmp_path / "gate.json")
    assert rb.main(["--recheck", "--output", str(source)], client=_never()) == 0
    again = tmp_path / "gate.rechecked.json"
    capsys.readouterr()
    assert rb.main(["--recheck", "--output", str(again), "--into",
                    str(tmp_path / "again.json")], client=_never()) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[1:3] == [
        "Values changed by the recheck: 0.",
        "Values unchanged, with a different reason, URL, quote or location: 0."]
    saved = json.loads((tmp_path / "again.json").read_text(encoding="utf-8"))
    assert [(r["from"], r["values_changed"]) for r in saved["rechecks"]] == [
        ("gate.json", 1), ("gate.rechecked.json", 0)]
    assert not (tmp_path / "gate.rechecked.rechecked.json").exists()


def test_a_recheck_reports_a_reworded_reason_without_counting_it_as_a_change(tmp_path, capsys,
                                                                             monkeypatch):
    # The saved bottom was unknown for a reason the checks no longer give; it is still
    # unknown, so the value has not changed, only its reason.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    source = _old_pipeline_file(tmp_path / "gate.json")
    results = json.loads(source.read_text(encoding="utf-8"))
    record = results["spots"]["Banzai Pipeline|Hawaii"]
    record["research_responses"]["first"][0]["content"][-1] = _answer(
        _claim("reef", WIKI, PIPELINE_QUOTE), _claim("rock", WIKI, "a shallow lava shelf here"),
        [WIKI_ENTRY])
    record["research"]["bottom"] = dict(OLD_VERDICT)
    rb.save_results(source, results)
    assert rb.main(["--recheck", "--output", str(source)], client=_never()) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[1] == "Values changed by the recheck: 1."
    assert printed[7] == "Values unchanged, with a different reason, URL, quote or location: 1."
    rechecked = json.loads((tmp_path / "gate.rechecked.json").read_text(encoding="utf-8"))
    # The only "a" on the page is in "is a surf reef break": the quote's first word is
    # there, then the quote has "shallow" where the page has "surf". The page is 278
    # characters (see test_the_saved_copy_holds_everything_the_checks_need).
    assert rechecked["spots"]["Banzai Pipeline|Hawaii"]["research"]["bottom"]["reason"] == (
        "the quote is not on the cited page (278 characters fetched, short of the 6,000-token "
        "limit, so not cut): its first word is on the page, then the quote has 'shallow' where "
        "the page has 'surf'")


def test_a_spot_with_nothing_saved_is_left_as_it_was(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    source = _old_pipeline_file(tmp_path / "gate.json")
    results = json.loads(source.read_text(encoding="utf-8"))
    stopped = rb.new_record(rb.PILOT_SPOTS[1], {"name": "Waimea Bay", "region_hint": "Hawaii",
                                                "lat": 21.642, "lng": -158.066})
    stopped["error"] = "FakeStatusError 500: overloaded"
    results["spots"]["Waimea Bay|Hawaii"] = stopped
    rb.save_results(source, results)
    assert rb.main(["--recheck", "--output", str(source)], client=_never()) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[:2] == [
        "Rechecked 1 spot from gate.json against their saved responses and pages, with no "
        "API calls. Wrote gate.rechecked.json.",
        "Nothing saved to recheck at 1 spot (not researched, or stopped before an answer); "
        "left as they were."]
    rechecked = json.loads((tmp_path / "gate.rechecked.json").read_text(encoding="utf-8"))
    assert rechecked["spots"]["Waimea Bay|Hawaii"] == stopped


def test_a_recheck_keeps_the_follow_ups_rule():
    # The first turn researched the break type from one page and failed a fetch; the
    # follow-up, asked for the bottom only, read another page that calls it a point break.
    # The break type stays the first turn's reef; the bottom is the follow-up's rock.
    page_a = "Banzai Pipeline is a reef break on the North Shore. Location: Hawaii."
    page_b = "Pipeline breaks over a shallow ledge of lava rock, a point break. Location: Hawaii."
    first = {"stop_reason": "end_turn", "content": [
        *_search("s1", [GUIDE]), *_fetch("f1", GUIDE, page_a), *_fetch_failed("f2", GUESSED),
        _answer(_claim("reef", GUIDE, "Banzai Pipeline is a reef break on the North Shore"),
                UNKNOWN, [dict(WIKI_ENTRY, url=GUIDE)])]}
    more = {"stop_reason": "end_turn", "content": [
        *_fetch("f3", GUESSED, page_b),
        _answer(_claim("point", GUESSED, "lava rock, a point break"),
                _claim("rock", GUESSED, "breaks over a shallow ledge of lava rock"),
                [dict(WIKI_ENTRY, url=GUESSED)])]}
    record = rb.new_record(PIPELINE_ENTRY, SPOT)
    record["research_responses"] = {"first": rb.saved_responses([first]),
                                    "follow_up": {"asked_for": ["bottom"],
                                                  "responses": rb.saved_responses([more])}}
    record["research"] = {"break_type": dict(OLD_VERDICT), "bottom": dict(OLD_VERDICT)}
    results = rb.new_results("medium", 4.0)
    results["spots"]["Banzai Pipeline|Hawaii"] = record
    rechecked, changed, details, unsaved = rb.recheck_results(results, "gate.json")
    research = rechecked["spots"]["Banzai Pipeline|Hawaii"]["research"]
    assert (research["break_type"]["value"], research["break_type"]["source_url"]) == (
        "reef", GUIDE)
    assert (research["bottom"]["value"], research["bottom"]["source_url"]) == ("rock", GUESSED)
    assert research["follow_up"] == {
        "after": [{"url": GUESSED, "error": "url_not_in_prior_context"}],
        "needed": ["bottom"], "searches": 0, "fetches": 1, "filled": ["bottom"]}
    assert [(field, old["value"], new["value"]) for _, field, old, new in changed] == [
        ("break_type", "unknown", "reef"), ("bottom", "unknown", "rock")]
    assert (details, unsaved) == (0, 0)
    # The results read are left as they were.
    assert results["spots"]["Banzai Pipeline|Hawaii"]["research"]["bottom"] == OLD_VERDICT
    assert "rechecks" not in results


def _gate_client():
    """All 20 pilot spots: the controls right, every other spot a reef over rock, and
    Pipeline from Wikipedia with the cut-off-link quote."""
    kinds = {"Mavericks, California": ("reef", "rock"), "Steamer Lane": ("point", "rock"),
             "Malibu Surfrider Beach": ("point", "rock"), "Zuma Beach": ("beach", "sand")}

    def respond(request, n):
        name = request["messages"][0]["content"].splitlines()[0][len("Name: "):]
        state = request["messages"][0]["content"].splitlines()[1].split(": ")[1].split(",")[0]
        kind, bottom = kinds.get(name, ("reef", "rock"))
        if "tools" not in request:
            return {"content": [_recall(kind, bottom)], "stop_reason": "end_turn",
                    "usage": {"input_tokens": 0, "output_tokens": 0}}
        if name == "Banzai Pipeline":
            content = [*_search("s1", [WIKI]), *_fetch("f1", WIKI, WIKI_PAGE),
                       _answer(_claim("reef", WIKI, PIPELINE_QUOTE),
                               _claim("rock", WIKI, LAVA_QUOTE), [WIKI_ENTRY])]
        else:
            url = "https://www.surfguide.com/" + re.sub(r"[^a-z0-9]+", "-", name.lower())
            quote = f"{name} is a {kind} break over a {bottom} bottom"
            content = [*_search("s1", [url]),
                       *_fetch("f1", url, f"{quote}. Location: {state}."),
                       _answer(_claim(kind, url, quote), _claim(bottom, url, quote),
                               [{"url": url, "place": name, "state": state, "lat": None,
                                 "lng": None, "location_quote": f"Location: {state}"}])]
        return {"content": content, "stop_reason": "end_turn",
                "usage": {"input_tokens": 0, "output_tokens": 0}}
    return FakeClient(respond)


def test_the_gate_failed_by_the_old_checks_passes_on_recheck(tmp_path, capsys, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    results = rb.new_results("medium", 4.0)
    assert rb.run(_gate_client(), rb.resolve_pilot(ROSTER), results, rb.Budget(4.0), "medium",
                  pause_seconds=0, sleep=NO_SLEEP) is None
    pipeline = results["spots"]["Banzai Pipeline|Hawaii"]
    pipeline["research"]["break_type"] = dict(OLD_VERDICT)
    pipeline["agree"]["break_type"] = "n/a"
    source = tmp_path / "gate.json"
    rb.save_results(source, results)
    assert rb.main(["--recheck", "--output", str(source)], client=_never()) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[:9] == [
        "Rechecked 20 spots from gate.json against their saved responses and pages, with no "
        "API calls. Wrote gate.rechecked.json.",
        "Values changed by the recheck: 1.",
        "",
        "| Spot | Value | Before | After |",
        "|---|---|---|---|",
        "| Banzai Pipeline (Hawaii) | break type | unknown: the quote is not on the cited page "
        "| reef |",
        "",
        "Values unchanged, with a different reason, URL, quote or location: 0.",
        "The gate: FAIL before the recheck, PASS after."]
    assert printed[-2:] == ["PASS  no more than 4 of the 20 unknown for break type; 0 are",
                            "GATE: PASS"]
    assert rb.main(["--report", "--output", str(source)], client=_never()) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "GATE: FAIL"


# --- 6. what it refuses -------------------------------------------------------------------------

def test_the_rechecked_file_is_named_after_the_one_read():
    assert rb.recheck_path(Path("pipeline/data/break_type_research_gate3.json")) == Path(
        "pipeline/data/break_type_research_gate3.rechecked.json")


def test_a_recheck_may_be_written_where_it_is_told(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    source = _old_pipeline_file(tmp_path / "gate.json")
    assert rb.main(["--recheck", "--output", str(source), "--into",
                    str(tmp_path / "mine.json")], client=_never()) == 0
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gate.json", "mine.json"]


def test_a_recheck_refuses_what_it_cannot_judge_or_must_not_write(tmp_path, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    never = _never()
    source = _old_pipeline_file(tmp_path / "gate.json")
    with pytest.raises(SystemExit, match="no results file"):
        rb.main(["--recheck", "--output", str(tmp_path / "missing.json")], client=never)
    # The first gate runs (schema 3) saved no pages.
    old = json.loads(source.read_text(encoding="utf-8"))
    old["schema"] = 3
    rb.save_results(tmp_path / "old.json", old)
    with pytest.raises(SystemExit, match="is not a gate results file of schema 4, the first "
                                         "to save each research turn's responses and pages"):
        rb.main(["--recheck", "--output", str(tmp_path / "old.json")], client=never)
    with pytest.raises(SystemExit, match="is not a full-run results file of schema 2"):
        rb.main(["--full", "--recheck", "--output", str(source)], client=never)
    with pytest.raises(SystemExit, match="memory-only answers cite no pages"):
        rb.main(["--memory-only", "--recheck", "--output", str(source)], client=never)
    with pytest.raises(SystemExit, match="refusing to write the recheck over"):
        rb.main(["--recheck", "--output", str(source), "--into", str(source)], client=never)
    with pytest.raises(SystemExit, match="that is the roster"):
        rb.main(["--recheck", "--output", str(source), "--into",
                 str(tmp_path / "spots_enriched.json")], client=never)
    memory = tmp_path / "memory.json"
    with pytest.raises(SystemExit, match="that is the memory-only results file"):
        rb.main(["--recheck", "--output", str(source), "--into", str(memory),
                 "--memory-results", str(memory)], client=never)
    with pytest.raises(SystemExit):
        rb._parse_args(["--into", str(tmp_path / "x.json")])
    with pytest.raises(SystemExit):
        rb._parse_args(["--recheck", "--report"])
    assert never.calls == []
    assert sorted(p.name for p in tmp_path.iterdir()) == ["gate.json", "old.json"]
