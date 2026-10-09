"""The full run of the break-type research script (--full): every rated spot that the
memory-only run's answers do not settle by geography is researched, and the report says
what changed and the final sand-bottom flag for every spot.

WHAT THESE TESTS HOLD:

  1. the plan settles exactly the spots the settled-by-geography rule settles, and
     researches every other rated spot, including any the memory run did not answer;
  2. each planned spot gets the researched answer only: no memory request, the memory-only
     run's answer carried in the record;
  3. it writes its own results file and nothing else, resumes where it stopped, keeps to
     its cap ($70 by default), and will not write over the memory-only results;
  4. the report's tables are the ones written out here;
  5. its file can be rechecked with no API calls, like the gate's.

No expected value below is produced by calling the code under test.
"""
from __future__ import annotations

import json

import pytest

from pipeline import config
from pipeline import research_break_type as rb

NO_SLEEP = lambda seconds: None  # noqa: E731


def _spot(name, state, lat, lng, label=None, verified=False):
    return {"name": name, "region_hint": state, "lat": lat, "lng": lng,
            "is_valid_surf_spot": True, "break_type": label,
            "verification_confidence": "high" if verified else None,
            "verification_notes": "ROSTER-ONLY NOTE"}


# In roster order. Sandy and Rockaway Beach are settled; the rest are researched.
ROSTER = [
    _spot("Sandy", "New Jersey", 40.0, -74.05, label="beach"),
    # South of the south-shore line at 73.8069 W: 40.55 + 0.23 x 0.1931 = 40.594 N.
    _spot("Rockaway Beach", "New York", 40.5833, -73.8069, label="beach"),
    # East of 72.05 W: Montauk, never settled.
    _spot("Ditch Plains", "New York", 41.0387, -71.9145, label="beach"),
    _spot("Twin", "New Jersey", 39.5, -74.3, label="beach"),
    _spot("Cove", "California", 36.95, -122.02, label="point", verified=True),
    _spot("Lost", "Maine", 43.5, -70.3),
    _spot("Unasked", "Texas", 27.6, -97.2, label="jetty", verified=True),
    # Not a rated spot: never planned.
    dict(_spot("Gone", "Texas", 27.0, -97.0), is_valid_surf_spot=False),
]


def _memory_answer(bt, bottom, shared=False, places=()):
    return {"content": [{"type": "text", "text": json.dumps({
        "break_type": {"value": bt, "all": [bt], "confidence": "high"},
        "bottom": {"value": bottom, "all": [bottom], "confidence": "high"},
        "mixed_note": None, "name_shared_elsewhere": shared, "other_places": list(places),
        "note": None})}], "stop_reason": "end_turn"}


def _memory_results():
    """The memory-only run's file: five answered, Lost failed, Unasked never asked."""
    answers = {"Sandy": ("beach", "sand"), "Rockaway Beach": ("beach", "sand"),
               "Ditch Plains": ("beach", "sand"), "Cove": ("point", "rock")}
    results = rb.new_memory_results("medium", 6.0, 7)
    for spot in ROSTER[:5]:
        record = rb.new_memory_record(spot)
        if spot["name"] == "Twin":
            memory = _memory_answer("beach", "sand", shared=True, places=["Twin, Oregon"])
        else:
            memory = _memory_answer(*answers[spot["name"]])
        record.update(memory=rb.judge_memory(memory), done=True)
        results["spots"][f"{spot['name']}|{spot['region_hint']}"] = record
    lost = rb.new_memory_record(ROSTER[5])
    lost["error"] = "FakeStatusError 500: overloaded"
    results["spots"]["Lost|Maine"] = lost
    return results


def _url(name):
    return "https://www.surfguide.com/spots/" + name.lower().replace(" ", "-")


# What research finds at each planned spot: (break type, bottom or None, the quote).
FOUND = {
    "Ditch Plains": ("reef", "rock", "Ditch Plains is a reef break over a rock bottom"),
    "Twin": ("beach", "sand", "Twin is a beach break over a sand bottom"),
    "Cove": ("point", None, "Cove is a long point break"),
    "Lost": ("beach", "sand", "Lost is a beach break over a sand bottom"),
    "Unasked": ("jetty", "sand", "Unasked is a jetty break over a sand bottom"),
}
STATES = {spot["name"]: spot["region_hint"] for spot in ROSTER}


def _researched(name):
    kind, bottom, quote = FOUND[name]
    url, state = _url(name), STATES[name]
    answer = {
        "break_type": {"value": kind, "all": [kind], "source_url": url, "quote": quote},
        "bottom": ({"value": bottom, "all": [bottom], "source_url": url, "quote": quote}
                   if bottom else {"value": "unknown", "all": [], "source_url": None,
                                   "quote": None}),
        "pages": [{"url": url, "place": name, "state": state, "lat": None, "lng": None,
                   "location_quote": f"Location: {state}"}],
        "same_name_elsewhere": [], "note": None}
    return [
        {"type": "server_tool_use", "id": "s1", "name": "web_search",
         "input": {"query": f"{name} {state} surf"}},
        {"type": "web_search_tool_result", "tool_use_id": "s1",
         "content": [{"type": "web_search_result", "url": url, "title": name,
                      "encrypted_content": "enc", "page_age": None}]},
        {"type": "server_tool_use", "id": "f1", "name": "web_fetch", "input": {"url": url}},
        {"type": "web_fetch_tool_result", "tool_use_id": "f1",
         "content": {"type": "web_fetch_result", "url": url,
                     "retrieved_at": "2026-10-09T12:00:00Z",
                     "content": {"type": "document",
                                 "source": {"type": "text", "media_type": "text/plain",
                                            "data": f"{quote}. Location: {state}."},
                                 "citations": {"enabled": True}}}},
        {"type": "text", "text": "```json\n" + json.dumps(answer) + "\n```"}]


class FakeClient:
    """Answers research requests from FOUND, each at 31,250 input tokens x $2/M = $0.0625,
    exact in binary; the usage counts no search ($0.01 each) and one fetch (free). Any
    request without tools fails the test: there is no memory request."""

    def __init__(self):
        self.calls = []
        self.messages = self

    def create(self, **request):
        assert "tools" in request, "the full run makes no memory request"
        self.calls.append(request)
        name = request["messages"][0]["content"].splitlines()[0][len("Name: "):]
        return {"content": _researched(name), "stop_reason": "end_turn",
                "usage": {"input_tokens": 31_250, "output_tokens": 0,
                          "server_tool_use": {"web_search_requests": 0,
                                              "web_fetch_requests": 1}}}


def _asked(client):
    return [call["messages"][0]["content"].splitlines()[0] for call in client.calls]


@pytest.fixture
def files(tmp_path):
    roster = tmp_path / "roster_copy.json"
    roster.write_text(json.dumps(ROSTER))
    memory = tmp_path / "memory.json"
    rb.save_results(memory, _memory_results())
    return {"dir": tmp_path, "roster": roster, "memory": memory,
            "output": tmp_path / "full.json"}


def _main(files, *more, client=None):
    return rb.main(["--full", "--roster", str(files["roster"]), "--memory-results",
                    str(files["memory"]), "--output", str(files["output"]), "--pause", "0",
                    *more], client=client, sleep=NO_SLEEP)


# --- 1. the plan ---------------------------------------------------------------------------

def test_the_plan_settles_what_the_rule_settles_and_researches_the_rest():
    settled, to_research = rb.full_plan(rb.memory_spots(ROSTER), _memory_results())
    assert [spot["name"] for spot, _ in settled] == ["Sandy", "Rockaway Beach"]
    assert [(spot["name"], record is not None) for spot, record in to_research] == [
        ("Ditch Plains", True), ("Twin", True), ("Cove", True), ("Lost", False),
        ("Unasked", False)]


def test_the_full_runs_defaults():
    assert rb.FULL_BUDGET_USD == 70.00
    assert rb.DEFAULT_FULL_OUTPUT == config.PIPELINE_DIR / "data" / "break_type_research_full.json"
    assert rb.FULL_SCHEMA_VERSION == 2


# --- 2-3. the run ------------------------------------------------------------------------------

def test_the_run_researches_each_planned_spot_once_and_writes_only_its_file(files):
    roster_before = files["roster"].read_bytes()
    memory_before = files["memory"].read_bytes()
    client = FakeClient()
    assert _main(files, client=client) == 0
    assert _asked(client) == ["Name: Ditch Plains", "Name: Twin", "Name: Cove", "Name: Lost",
                              "Name: Unasked"]
    assert client.calls[0]["messages"][0]["content"] == (
        "Name: Ditch Plains\nState or territory: New York, United States\n"
        "Coordinates: 41.03870, -71.91450 (latitude, longitude)")
    assert client.calls[0]["system"][0]["text"] == rb.RESEARCH_SYSTEM
    assert "ROSTER-ONLY" not in json.dumps(client.calls)
    assert files["roster"].read_bytes() == roster_before
    assert files["memory"].read_bytes() == memory_before
    assert sorted(p.name for p in files["dir"].iterdir()) == ["full.json", "memory.json",
                                                             "roster_copy.json"]
    saved = json.loads(files["output"].read_text())
    assert (saved["kind"], saved["schema"], saved["budget_usd"]) == ("full", 2, 70.0)
    assert sorted(saved["settled"]) == ["Rockaway Beach|New York", "Sandy|New Jersey"]
    assert saved["planned"] == ["Ditch Plains|New York", "Twin|New Jersey", "Cove|California",
                                "Lost|Maine", "Unasked|Texas"]
    assert saved["regions"]["Rockaway Beach|New York"] == "New York (Long Island south shore)"
    assert saved["regions"]["Ditch Plains|New York"] == "New York (Montauk)"
    ditch = saved["spots"]["Ditch Plains|New York"]
    assert ditch["done"] is True and ditch["model_recall"] is None
    assert ditch["recall_usage"] is None and ditch["agree"] is None
    assert (ditch["memory"]["break_type"]["value"], ditch["memory"]["bottom"]["value"]) == (
        "beach", "sand")
    assert (ditch["research"]["break_type"]["value"], ditch["research"]["sand_bottom"]) == (
        "reef", "no")
    assert ditch["current"]["verified"] is False
    assert saved["spots"]["Lost|Maine"]["memory"] is None
    assert saved["spots"]["Unasked|Texas"]["current"]["verified"] is True
    # Five spots at $0.0625.
    assert saved["spent_usd"] == 0.3125
    assert ditch["cost_usd"] == 0.0625


def test_a_rerun_resumes_where_the_last_one_stopped(files):
    first = FakeClient()
    assert _main(files, "--limit", "2", client=first) == 0
    assert _asked(first) == ["Name: Ditch Plains", "Name: Twin"]
    second = FakeClient()
    assert _main(files, client=second) == 0
    assert _asked(second) == ["Name: Cove", "Name: Lost", "Name: Unasked"]
    saved = json.loads(files["output"].read_text())
    assert saved["spent_usd"] == 0.3125 and len(saved["runs"]) == 2
    assert all(record["done"] for record in saved["spots"].values())


def test_the_budget_stops_the_run_before_a_spot_that_could_cross_it(files):
    # Spots of $0.0625; the reserve is the $0.50 floor, as twice the dearest is $0.125.
    # Cap $0.6875: spot 4 needs 0.1875 + 0.50 = 0.6875, which fits; spot 5 needs
    # 0.25 + 0.50 = 0.75, which does not.
    client = FakeClient()
    assert _main(files, "--budget", "0.6875", client=client) == 0
    assert len(client.calls) == 4
    saved = json.loads(files["output"].read_text())
    assert (saved["spent_usd"], saved["runs"][-1]["stopped"]) == (0.25, "budget")


def test_a_results_file_from_other_settings_is_not_resumed(files):
    assert _main(files, "--limit", "1", client=FakeClient()) == 0
    with pytest.raises(SystemExit, match="not a full-run results file made with these"):
        _main(files, "--effort", "high", client=FakeClient())


def test_it_will_not_write_over_the_memory_results_or_plan_from_another_file(files):
    never = FakeClient()
    with pytest.raises(SystemExit, match="that is the memory-only results file"):
        rb.main(["--full", "--roster", str(files["roster"]), "--memory-results",
                 str(files["memory"]), "--output", str(files["memory"])], client=never)
    pilot = files["dir"] / "pilot.json"
    rb.save_results(pilot, rb.new_results("medium", 4.0))
    with pytest.raises(SystemExit, match="not a memory-only results file"):
        rb.main(["--full", "--roster", str(files["roster"]), "--memory-results", str(pilot),
                 "--output", str(files["output"])], client=never)
    with pytest.raises(SystemExit, match="Run --memory-only first"):
        rb.main(["--full", "--roster", str(files["roster"]), "--memory-results",
                 str(files["dir"] / "missing.json"), "--output", str(files["output"])],
                client=never)
    assert never.calls == [] and not files["output"].exists()


def test_each_report_refuses_the_others_results_file(files):
    pilot = files["dir"] / "pilot.json"
    rb.save_results(pilot, rb.new_results("medium", 4.0))
    with pytest.raises(SystemExit, match="not a full-run results file"):
        rb.main(["--full", "--report", "--output", str(pilot)])
    assert _main(files, "--limit", "1", client=FakeClient()) == 0
    with pytest.raises(SystemExit, match="schema 4 only"):
        rb.main(["--report", "--output", str(files["output"])])


def test_a_dry_run_shows_the_plan_and_the_request_and_calls_nothing(files, capsys):
    never = FakeClient()
    assert _main(files, "--dry-run", client=never) == 0
    printed = capsys.readouterr().out
    lines = printed.splitlines()
    assert lines[:10] == [
        "Settled by geography: 2 spots. To research: 5 spots, 2 of them with no memory "
        "answer. No memory request is made: each spot is one researched answer, with at most "
        "one follow-up.", "",
        "| Region | Settled | To research |", "|---|---|---|",
        "| California | 0 | 1 |", "| Maine | 0 | 1 |", "| New Jersey | 1 | 1 |",
        "| New York (Long Island south shore) | 1 | 0 |", "| New York (Montauk) | 0 | 1 |",
        "| Texas | 0 | 1 |"]
    assert "Every request is this one." in printed
    assert rb.RESEARCH_SYSTEM in printed and rb.RECALL_SYSTEM not in printed
    assert ("Name: Unasked\nState or territory: Texas, United States\n"
            "Coordinates: 27.60000, -97.20000 (latitude, longitude)") in printed
    assert "Name: Sandy" not in printed
    assert never.calls == [] and not files["output"].exists()


# --- 4. the report --------------------------------------------------------------------------

DITCH, TWIN, LOST, UNASKED = (_url(n) for n in ("Ditch Plains", "Twin", "Lost", "Unasked"))
SETTLED = "settled by geography (memory: sand, high confidence)"


def test_the_report(files, capsys):
    assert _main(files, client=FakeClient()) == 0
    capsys.readouterr()
    assert rb.main(["--full", "--report", "--output", str(files["output"])]) == 0
    report = capsys.readouterr().out.splitlines()
    assert report == [
        "Researched 5 of the 5 rated spots that geography does not settle; 2 more are settled "
        "by geography. Spent $0.3125 of the $70.00 budget, at list prices.",
        "Memory answers from memory.json (5 answered).",
        "Per spot: mean $0.0625, dearest $0.0625. The 0 spots left would cost about $0.00 at "
        "that mean.",
        "Research left the break type unknown at 0 of 5.",
        "Research left the bottom unknown at 1 of 5.",
        "",
        "Break type: changed against memory (3 spots; memory 'unknown' or no memory answer "
        "counts as a change):",
        "",
        "| Spot | Memory | Researched | Source URL |",
        "|---|---|---|---|",
        f"| Lost (Maine) | (no memory answer) | beach | {LOST} |",
        f"| Ditch Plains (New York (Montauk)) | beach | reef | {DITCH} |",
        f"| Unasked (Texas) | (no memory answer) | jetty | {UNASKED} |",
        "",
        "Bottom: changed against memory (3 spots; memory 'unknown' or no memory answer counts "
        "as a change):",
        "",
        "| Spot | Memory | Researched | Sand bottom: memory | Sand bottom: researched "
        "| Source URL |",
        "|---|---|---|---|---|---|",
        f"| Lost (Maine) | (no memory answer) | sand | — | yes | {LOST} |",
        f"| Ditch Plains (New York (Montauk)) | sand | rock | yes | no | {DITCH} |",
        f"| Unasked (Texas) | (no memory answer) | sand | — | yes | {UNASKED} |",
        "",
        "Break type: changed against the current label (2 of the 5 researched; 0 verified, "
        "1 unverified, 1 no label):",
        "",
        "| Spot | Current label | Researched | Source URL |",
        "|---|---|---|---|",
        f"| Lost (Maine) | (none) | beach | {LOST} |",
        f"| Ditch Plains (New York (Montauk)) | beach (unverified) | reef | {DITCH} |",
        "",
        "Final sand-bottom flag, every spot (7): 5 yes, 1 no, 1 unknown, 0 pending.",
        "",
        "| Spot | Sand bottom | Bottom | From |",
        "|---|---|---|---|",
        "| Cove (California) | unknown | unknown: no page it read says | research found no "
        "bottom: no page it read says |",
        f"| Lost (Maine) | yes | sand | research: {LOST} |",
        f"| Sandy (New Jersey) | yes | sand | {SETTLED} |",
        f"| Twin (New Jersey) | yes | sand | research: {TWIN} |",
        f"| Rockaway Beach (New York (Long Island south shore)) | yes | sand | {SETTLED} |",
        f"| Ditch Plains (New York (Montauk)) | no | rock | research: {DITCH} |",
        f"| Unasked (Texas) | yes | sand | research: {UNASKED} |",
    ]


def test_the_report_of_an_unfinished_run_marks_the_rest_pending(files, capsys):
    # As in the budget test: four spots researched, Unasked not.
    assert _main(files, "--budget", "0.6875", client=FakeClient()) == 0
    report = capsys.readouterr().out.splitlines()
    assert report[0] == ("Researched 4 of the 5 rated spots that geography does not settle; "
                         "2 more are settled by geography. Spent $0.2500 of the $0.69 budget, "
                         "at list prices.")
    assert report[2] == ("Per spot: mean $0.0625, dearest $0.0625. The 1 spots left would "
                         "cost about $0.06 at that mean.")
    assert report[3] == "Stopped early: budget"
    assert "Final sand-bottom flag, every spot (7): 4 yes, 1 no, 1 unknown, 1 pending." in report
    assert report[-1] == "| Unasked (Texas) | pending | — | not researched yet |"


def test_a_full_run_file_is_rechecked_with_no_calls(files, capsys, monkeypatch):
    # No key and no client: a recheck that tried to make one would stop with an error.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    assert _main(files, client=FakeClient()) == 0
    saved = json.loads(files["output"].read_text())
    # Ditch Plains' bottom as checks that missed its quote would have left it.
    ditch = saved["spots"]["Ditch Plains|New York"]["research"]
    ditch["bottom"] = {"status": "unknown", "value": "unknown", "values": [], "mixed": False,
                       "source_url": DITCH, "cited_url": None, "quote": None,
                       "quote_found_in": None, "quote_names_value": None, "location": None,
                       "reason": "the quote is not on the cited page"}
    ditch["sand_bottom"] = "unknown"
    rb.save_results(files["output"], saved)
    before = files["output"].read_bytes()
    capsys.readouterr()
    assert rb.main(["--full", "--recheck", "--output", str(files["output"]), "--roster",
                    str(files["roster"]), "--memory-results", str(files["memory"])]) == 0
    printed = capsys.readouterr().out.splitlines()
    assert printed[:10] == [
        "Rechecked 5 spots from full.json against their saved responses and pages, with no "
        "API calls. Wrote full.rechecked.json.",
        "Values changed by the recheck: 1.",
        "",
        "| Spot | Value | Before | After |",
        "|---|---|---|---|",
        "| Ditch Plains (New York) | bottom | unknown: the quote is not on the cited page "
        "| rock |",
        "",
        "Values unchanged, with a different reason, URL, quote or location: 0.",
        "",
        "Researched 5 of the 5 rated spots that geography does not settle; 2 more are settled "
        "by geography. Spent $0.3125 of the $70.00 budget, at list prices."]
    assert files["output"].read_bytes() == before
    rechecked = json.loads((files["dir"] / "full.rechecked.json").read_text())
    assert (rechecked["kind"], rechecked["schema"]) == ("full", 2)
    research = rechecked["spots"]["Ditch Plains|New York"]["research"]
    assert (research["bottom"]["status"], research["bottom"]["value"],
            research["bottom"]["source_url"], research["sand_bottom"]) == (
        "researched", "rock", DITCH, "no")
    assert rechecked["settled"] == saved["settled"]
    assert rechecked["planned"] == saved["planned"]
