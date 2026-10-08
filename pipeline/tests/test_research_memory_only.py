"""The memory-only mode of the break-type research script: a memory answer (no web search)
for every rated spot, and the summary that decides what still needs research.

WHAT THESE TESTS HOLD:

  1. it asks about every rated spot, and the model sees only the name, state and
     coordinates, never our value;
  2. each answer is recorded as given: break type, bottom, the sand-bottom flag, confidence,
     the mixed note and the name-sharing flag;
  3. it writes its own results file and nothing else, resumes where it stopped, keeps to a
     $6 cap, and keeps the API's whole error message;
  4. the report's geography rule, reasons and lists are the ones written out here.

No expected value below is produced by calling the code under test.
"""
from __future__ import annotations

import json
import logging
import shutil
import types

import pytest

from pipeline import config
from pipeline import research_break_type as rb

with open(config.DEFAULT_ENRICHED_OUTPUT, encoding="utf-8") as _fh:
    ROSTER = json.load(_fh)

NO_SLEEP = lambda seconds: None  # noqa: E731
SPOT = {"name": "Banzai Pipeline", "region_hint": "Hawaii", "lat": 21.664, "lng": -158.054,
        "break_type": "beach", "break_type_source": "unattributed",
        "verification_notes": "ROSTER-ONLY NOTE", "verification_confidence": None}
PROMPT = ("Name: Banzai Pipeline\n"
          "State or territory: Hawaii, United States\n"
          "Coordinates: 21.66400, -158.05400 (latitude, longitude)")
MEMORY_REQUEST = {
    "model": "claude-sonnet-5-5",
    "max_tokens": 4000,
    "system": rb.MEMORY_SYSTEM,
    "thinking": {"type": "adaptive"},
    "output_config": {"effort": "medium"},
    "messages": [{"role": "user", "content": PROMPT}],
}


def _text(text):
    return {"type": "text", "text": text}


def _answer(bt="reef", bottom="rock", bt_conf="high", bottom_conf="high", shared=False,
            places=(), mixed_note=None, bottoms=None):
    return {"content": [_text(json.dumps({
        "break_type": {"value": bt, "all": [bt], "confidence": bt_conf},
        "bottom": {"value": bottom, "all": list(bottoms or [bottom]), "confidence": bottom_conf},
        "mixed_note": mixed_note, "name_shared_elsewhere": shared,
        "other_places": list(places), "note": None}))],
        "stop_reason": "end_turn", "usage": {"input_tokens": 0, "output_tokens": 0}}


class FakeClient:
    def __init__(self, respond):
        self.calls = []
        self.messages = self
        self._respond = respond

    def create(self, **request):
        self.calls.append(request)
        return self._respond(request, len(self.calls))


class FakeStatusError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status_code = status
        self.response = types.SimpleNamespace(headers={})


def _priced_answer(input_tokens, **kw):
    response = _answer(**kw)
    response["usage"] = {"input_tokens": input_tokens, "output_tokens": 0}
    return response


# --- 1. who is asked, and what the model is shown -----------------------------------------

def test_every_rated_spot_is_asked_once():
    spots = rb.memory_spots(ROSTER)
    assert len(spots) == 646
    assert all(spot.get("is_valid_surf_spot") is not False for spot in spots)
    assert len({rb.memory_key(spot) for spot in spots}) == 646


def test_two_rated_spots_with_one_name_and_state_stop_the_run():
    twin = dict(rb.memory_spots(ROSTER)[0])
    with pytest.raises(SystemExit, match="share a name and state"):
        rb.memory_spots(ROSTER + [twin])


def test_the_request_is_written_out_here_in_full_and_never_shows_our_value():
    assert rb.memory_request(rb.spot_identity(SPOT), "medium") == MEMORY_REQUEST
    other = dict(SPOT, break_type="jetty", verification_confidence="high",
                 verification_notes="different")
    assert rb.memory_request(rb.spot_identity(other), "medium") == MEMORY_REQUEST
    for ours in ("unattributed", "ROSTER-ONLY", "verification", "tools"):
        assert ours not in json.dumps(MEMORY_REQUEST)


def test_the_prompt_asks_for_both_values_confidence_mixing_and_a_shared_name():
    for line in ('"value": "beach" | "reef" | "point" | "jetty" | "rivermouth" | "unknown"',
                 '"value": "sand" | "rock" | "coral" | "cobble" | "mixed" | "unknown"',
                 '"confidence": "high" | "medium" | "low"',
                 '"mixed_note":',
                 '"name_shared_elsewhere": true | false',
                 '"other_places": ["other places you know with a surf spot of this name"]'):
        assert line in rb.MEMORY_SYSTEM, line
    # No rated spot's name is in the prompt: naming one would hand the model an answer.
    prompt = rb.MEMORY_SYSTEM.lower()
    assert [s["name"] for s in rb.memory_spots(ROSTER) if s["name"].lower() in prompt] == []


# --- 2. what is recorded ---------------------------------------------------------------------

def test_an_answer_is_recorded_as_given():
    memory = rb.judge_memory(_answer("point", "mixed", "high", "medium", shared=True,
                                     places=["Santa Cruz, California"],
                                     mixed_note="A cobble point with sand between the rocks.",
                                     bottoms=["cobble", "sand"]))
    assert (memory["break_type"]["value"], memory["break_type"]["confidence"]) == ("point", "high")
    assert (memory["bottom"]["value"], memory["bottom"]["values"],
            memory["bottom"]["confidence"]) == ("mixed", ["cobble", "sand"], "medium")
    assert memory["sand_bottom"] == "unknown"
    assert memory["mixed_note"] == "A cobble point with sand between the rocks."
    assert (memory["name_shared_elsewhere"], memory["other_places"]) == (
        True, ["Santa Cruz, California"])
    assert rb.shares_name(memory) is True


def test_the_sand_flag_and_the_shared_name_read_as_before():
    assert rb.judge_memory(_answer("beach", "sand"))["sand_bottom"] == "yes"
    assert rb.judge_memory(_answer("reef", "coral"))["sand_bottom"] == "no"
    plain = rb.judge_memory(_answer())
    assert (plain["name_shared_elsewhere"], plain["other_places"]) == (False, [])
    assert rb.shares_name(plain) is False
    # A place listed without the flag still counts; a flag that is not true/false is unread.
    listed = rb.judge_memory(_answer(shared="maybe", places=["Oahu, Hawaii"]))
    assert listed["name_shared_elsewhere"] is None and rb.shares_name(listed) is True


def test_a_refusal_or_no_answer_is_recorded_as_such():
    refused = rb.judge_memory({"content": [], "stop_reason": "refusal"})
    assert refused["reason"] == "the model declined to answer (stop_reason refusal)"
    assert (refused["break_type"]["value"], refused["sand_bottom"]) == ("unknown", "unknown")
    assert rb.judge_memory({"content": [_text("No idea.")],
                            "stop_reason": "end_turn"})["reason"] == "no JSON answer"


# --- 3. the run, its file, its budget and its errors ------------------------------------------

def test_the_memory_mode_has_its_own_file_budget_and_pause():
    args = rb._parse_args(["--memory-only"])
    assert args.output == config.PIPELINE_DIR / "data" / "break_type_memory_all.json"
    assert (args.budget, args.pause) == (6.00, 1.0)
    pilot = rb._parse_args([])
    assert pilot.output == config.PIPELINE_DIR / "data" / "break_type_research_pilot2.json"
    assert (pilot.budget, pilot.pause) == (3.00, 15.0)


def test_a_record_carries_the_current_label_for_the_report_only():
    client = FakeClient(lambda request, n: _priced_answer(500, bt="beach", bottom="sand"))
    spot = dict(SPOT, verification_confidence="high")
    record, exc = rb.memory_spot(client, spot, "medium", rb.Budget(6.0), NO_SLEEP)
    assert exc is None and record["done"] is True
    assert record["current"] == {"break_type": "beach", "verified": True,
                                 "verification_confidence": "high"}
    # 500 in x $2/M = $0.0010.
    assert record["cost_usd"] == pytest.approx(0.0010)
    assert client.calls == [MEMORY_REQUEST]


def test_a_failed_call_keeps_and_prints_the_apis_whole_message(caplog):
    message = ("Error code: 400 - {'type': 'error', 'error': {'type': 'invalid_request_error', "
               "'message': '" + "x" * 400 + "'}}")

    def fail(request, n):
        raise FakeStatusError(400, message)
    with caplog.at_level(logging.ERROR, logger="pipeline.research_break_type"):
        record, exc = rb.memory_spot(FakeClient(fail), SPOT, "medium", rb.Budget(6.0), NO_SLEEP)
    assert record["error"] == "FakeStatusError 400: " + message
    assert record["done"] is False and record["cost_usd"] == 0.0
    assert ("Banzai Pipeline (Hawaii): the call failed: FakeStatusError 400: " + message
            ) in caplog.text


def test_the_budget_floor_is_five_cents_so_small_spots_can_run_to_the_cap():
    # Each spot costs 31,250 in x $2/M = $0.0625, exact in binary. Cap $0.25: spot 1 needs
    # 0 + 0.05 <= 0.25; then twice the dearest, $0.125: spot 2 needs 0.0625 + 0.125 =
    # 0.1875; spot 3 needs 0.125 + 0.125 = 0.25, which fits; spot 4 needs 0.3125 > 0.25.
    client = FakeClient(lambda request, n: _priced_answer(31_250))
    results = rb.new_memory_results("medium", 0.25, 646)
    budget = rb.Budget(0.25, floor_usd=rb.MEMORY_RESERVE_FLOOR_USD)
    stopped = rb.memory_run(client, rb.memory_spots(ROSTER), results, budget, "medium",
                            pause_seconds=0, sleep=NO_SLEEP)
    assert (stopped, len(results["spots"]), results["spent_usd"]) == ("budget", 3, 0.1875)
    assert rb.MEMORY_RESERVE_FLOOR_USD == 0.05
    # The pilot's $0.50 floor would not have started one.
    assert rb.Budget(0.25).may_start_spot() is False


def test_a_rerun_resumes_retries_failures_and_stops_on_a_bad_request():
    spots = rb.memory_spots(ROSTER)[:4]

    def first(request, n):
        if n == 2:
            raise FakeStatusError(500, "overloaded")
        return _priced_answer(500)
    results = rb.new_memory_results("medium", 6.0, 4)
    stopped = rb.memory_run(FakeClient(first), spots, results, rb.Budget(6.0), "medium",
                            pause_seconds=0, sleep=NO_SLEEP)
    assert stopped is None
    assert [r["done"] for r in results["spots"].values()] == [True, False, True, True]
    again = FakeClient(lambda request, n: _priced_answer(500))
    rb.memory_run(again, spots, results, rb.Budget(6.0, results["spent_usd"]), "medium",
                  pause_seconds=0, sleep=NO_SLEEP)
    assert len(again.calls) == 1   # only the spot that failed
    assert again.calls[0]["messages"][0]["content"].startswith("Name: " + spots[1]["name"])
    assert all(r["done"] for r in results["spots"].values())

    def bad(request, n):
        raise FakeStatusError(400, "bad request")
    results = rb.new_memory_results("medium", 6.0, 4)
    stopped = rb.memory_run(FakeClient(bad), spots, results, rb.Budget(6.0), "medium",
                            pause_seconds=0, sleep=NO_SLEEP)
    assert stopped == "error: FakeStatusError 400: bad request"
    assert len(results["spots"]) == 1


def test_a_run_writes_its_results_file_and_nothing_else(tmp_path):
    roster = tmp_path / "spots_enriched_copy.json"
    shutil.copyfile(config.DEFAULT_ENRICHED_OUTPUT, roster)
    before = roster.read_bytes()
    output = tmp_path / "memory.json"
    client = FakeClient(lambda request, n: _priced_answer(500, bt="beach", bottom="sand"))
    code = rb.main(["--memory-only", "--roster", str(roster), "--output", str(output),
                    "--limit", "3"], client=client, sleep=NO_SLEEP)
    assert code == 0 and len(client.calls) == 3
    assert roster.read_bytes() == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["memory.json",
                                                         "spots_enriched_copy.json"]
    saved = json.loads(output.read_text())
    assert (saved["kind"], saved["schema"], saved["total_spots"]) == ("memory_only", 1, 646)
    assert len(saved["spots"]) == 3


def test_each_mode_refuses_the_others_results_file(tmp_path):
    memory = tmp_path / "memory.json"
    rb.save_results(memory, rb.new_memory_results("medium", 6.0, 646))
    with pytest.raises(SystemExit, match="schema 2 only"):
        rb.main(["--report", "--output", str(memory)])
    pilot = tmp_path / "pilot.json"
    rb.save_results(pilot, rb.new_results("medium", 3.0))
    with pytest.raises(SystemExit, match="not a memory-only results file"):
        rb.main(["--memory-only", "--report", "--output", str(pilot)])
    with pytest.raises(SystemExit, match="not a memory-only results file made with these"):
        rb.load_memory_results(pilot, "medium", 6.0, False, 646)
    with pytest.raises(SystemExit, match="made with these settings"):
        rb.load_memory_results(memory, "high", 6.0, False, 646)


def test_the_real_sdk_sends_the_memory_request_and_reads_the_answer_back():
    anthropic = pytest.importorskip("anthropic")
    httpx = pytest.importorskip("httpx")
    bodies = []

    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={
            "id": "msg_test", "type": "message", "role": "assistant",
            "model": "claude-sonnet-5-5", "content": _answer("reef", "rock")["content"],
            "stop_reason": "end_turn", "stop_sequence": None,
            "usage": {"input_tokens": 700, "output_tokens": 300}})
    client = anthropic.Anthropic(api_key="test-key", max_retries=0, http_client=httpx.Client(
        transport=httpx.MockTransport(handler)))
    record, exc = rb.memory_spot(client, SPOT, "medium", rb.Budget(6.0), NO_SLEEP)
    assert exc is None, record["error"]
    assert bodies == [MEMORY_REQUEST]
    assert (record["memory"]["break_type"]["value"], record["memory"]["sand_bottom"]) == (
        "reef", "no")
    # 700 in x $2/M + 300 out x $10/M = 0.0014 + 0.0030.
    assert record["cost_usd"] == pytest.approx(0.0044)


# --- 4. the report ------------------------------------------------------------------------------

GULF = ["Cape San Blas", "Captiva", "Coolidge", "Destin Jetty", "Englewood", "Gasparilla",
        "Indian Rocks Beach", "Madeira Beach", "Mexico Beach", "Naples Pier",
        "Navarre Beach", "Okaloosa Island Pier", "Panama City Beach Pier", "Pensacola Beach",
        "Redington Pier", "Siesta Key", "Spring Ave", "St Andrews Park", "St. Pete Beach",
        "Treasure Island FL", "Twin Piers", "Venice Beach FL", "Whitney Beach"]


def test_the_geography_lists_are_the_ones_proposed():
    assert rb.SAND_BARRIER_REGIONS == ("New Jersey", "Delaware", "Maryland", "Virginia",
                                       "North Carolina", "South Carolina", "Georgia", "Texas",
                                       "Florida (Gulf)", "Florida (Atlantic)")
    assert rb.FLORIDA_KNOWN_REEF == ("Monster Hole", "Bathtub Beach", "Ocean Reef Park",
                                     "Dania Beach Pier")
    assert rb.ROCKY_REGIONS == ("California", "Oregon", "Washington", "Hawaii",
                                "Puerto Rico", "Maine", "New Hampshire", "Massachusetts",
                                "Rhode Island", "Florida (Keys)")
    assert rb.HUMAN_LIST_SIZE == 30


def test_florida_is_split_by_coast():
    florida = [s for s in rb.memory_spots(ROSTER) if s["region_hint"] == "Florida"]
    by = {}
    for spot in florida:
        by.setdefault(rb.coast_region(spot), []).append(spot["name"])
    assert sorted(by["Florida (Gulf)"]) == GULF
    assert by["Florida (Keys)"] == ["Key West"]
    assert len(by["Florida (Atlantic)"]) == 82 - 23 - 1
    assert {"Jacksonville Beach Pier", "Fort Clinch", "South Beach Miami"} <= set(
        by["Florida (Atlantic)"])


def _spot(name, state, lat=0.0, lng=0.0):
    return {"name": name, "region_hint": state, "lat": lat, "lng": lng}


def test_which_spots_are_in_a_sand_barrier_or_a_rocky_region():
    cocoa = _spot("Cocoa Beach Pier", "Florida", 28.368, -80.601)
    monster = _spot("Monster Hole", "Florida", 27.868, -80.447)
    key_west = _spot("Key West", "Florida", 24.55, -81.78)
    assert [rb.in_sand_barrier_region(s) for s in (
        cocoa, monster, key_west, _spot("A", "Texas"), _spot("B", "New York"),
        _spot("C", "California"))] == [True, False, False, True, False, False]
    assert [rb.in_rocky_region(s) for s in (
        cocoa, monster, key_west, _spot("A", "Texas"), _spot("B", "New York"),
        _spot("C", "California"))] == [False, True, True, False, False, True]


def _record(name, state, lat=0.0, lng=0.0, label=None, verified=False, **answer):
    record = rb.new_memory_record(dict(_spot(name, state, lat, lng), break_type=label,
                                       verification_confidence="high" if verified else None))
    # 1/256 of a dollar, exact in binary, so no printed figure sits on a rounding tie.
    record.update(memory=rb.judge_memory(_answer(**answer)), done=True, cost_usd=0.00390625)
    return record


def test_settled_needs_a_sand_barrier_region_sand_with_high_confidence_and_no_shared_name():
    nj = dict(state="New Jersey", bt="beach", bottom="sand")
    assert rb.memory_verdict(_record("A", **nj)) == ("settled", [])
    # Settled is about the bottom: a disagreeing verified break type does not unsettle it.
    assert rb.memory_verdict(_record("B", label="jetty", verified=True, **nj)) == ("settled", [])
    assert rb.memory_verdict(_record("C", shared=True, **nj)) == (
        "candidate", ["possible name collision"])
    assert rb.memory_verdict(_record("D", bottom_conf="medium", **nj)) == (
        "candidate", ["low or medium confidence"])
    # A mixed bottom gives an unknown sand flag, so it is not settled either.
    assert rb.memory_verdict(_record("K", **dict(nj, bottom="mixed",
                                                 bottoms=["sand", "rock"]))) == (
        "candidate", ["not sand, or mixed"])
    assert rb.memory_verdict(_record("E", state="California", bt="beach", bottom="sand")) == (
        "candidate", ["rocky or reef region"])
    assert rb.memory_verdict(_record("F", state="New York", bt="beach", bottom="sand")) == (
        "candidate", ["sand with high confidence, outside the sand-barrier regions"])
    assert rb.memory_verdict(_record("Monster Hole", "Florida", 27.868, -80.447, bt="beach",
                                     bottom="sand")) == ("candidate", ["rocky or reef region"])
    assert rb.memory_verdict(_record("G", "California", label="reef", verified=True,
                                     bt="point", bottom="rock", bt_conf="low")) == (
        "candidate", ["low or medium confidence", "not sand, or mixed", "rocky or reef region",
                      "disagrees with a verified label"])


def test_the_confusion_score_and_its_reasons():
    # 3 (verified disagreement) + 2 (shared name) + 2 (low confidence) = 7.
    record = _record("G", "California", label="reef", verified=True, bt="point",
                     bottom="rock", bt_conf="low", shared=True,
                     places=["Oahu, Hawaii", "Bali", "Peru", "Chile"])
    assert rb.confusion(record) == (7, [
        "memory says point, the verified label says reef",
        "the name is also used at Oahu, Hawaii, Bali, Peru",
        "low or no confidence on its break type"])
    # 2 (rock in a sand-barrier region) + 1 (medium confidence) = 3.
    record = _record("H", "Texas", bt="beach", bottom="rock", bottom_conf="medium")
    assert rb.confusion(record) == (3, ["memory says a rock bottom in a sand-barrier region",
                                        "medium confidence on its bottom"])
    record = _record("I", "Maine", bt="beach", bottom="mixed", bottoms=["sand", "rock"],
                     mixed_note="Sand over ledge.")
    assert rb.confusion(record) == (1, ["mixed: Sand over ledge"])
    assert rb.confusion(_record("J", "New Jersey", bt="beach", bottom="sand")) == (0, [])


def _report(records):
    results = rb.new_memory_results("medium", 6.0, 646)
    results["spots"] = {f"{r['name']}|{r['state']}": r for r in records}
    results["spent_usd"] = round(0.00390625 * sum(1 for r in records if r["done"]), 6)
    return rb.render_memory_report(results).splitlines()


def test_the_report():
    records = [
        _record("Sandy", "New Jersey", label="beach", bt="beach", bottom="sand"),
        _record("Inlet", "New Jersey", label="jetty", verified=True, bt="beach", bottom="sand"),
        _record("Ledge", "Texas", label="beach", bt="reef", bottom="rock", bottom_conf="medium"),
        _record("Point", "California", label="point", verified=True, bt="point", bottom="cobble"),
        _record("Twin", "California", label="beach", bt="beach", bottom="sand", shared=True,
                places=["Oahu, Hawaii"]),
    ]
    failed = rb.new_memory_record(dict(_spot("Lost", "Maine"), break_type=None))
    failed["error"] = "FakeStatusError 500: overloaded"
    report = _report(records + [failed])
    # 5 answered at $0.00390625: spent $0.01953125 (stored as 0.019531); 646 - 5 = 641 left
    # x $0.00390625 = $2.50390625.
    assert report[:3] == [
        "Memory answers for 5 of 646 rated spots. Spent $0.0195 of the $6.00 budget, at list "
        "prices.",
        "Per spot: mean $0.0039, dearest $0.0039. The 641 spots left would cost about $2.50 "
        "at that mean.",
        "Not answered: Lost (Maine): FakeStatusError 500: overloaded"]
    assert "| California | 2 | 1 | 1 | 0 |" in report
    assert "| New Jersey | 2 | 2 | 0 | 0 |" in report
    assert "| Texas | 1 | 0 | 1 | 0 |" in report
    assert "| All | 5 | 3 | 2 | 0 |" in report
    assert "| high | 5 | 4 |" in report and "| medium | 0 | 1 |" in report
    assert "By sand-bottom flag: 3 yes, 2 no, 0 unknown." in report
    assert "Name possibly shared with a spot elsewhere: 1." in report
    assert ("Verified labels (2 spots): memory gives a break type for 2, agrees on 1 and "
            "disagrees on 1.") in report
    assert "| Inlet (New Jersey) | jetty | beach | high | sand |" in report
    assert ("Unverified 'beach' labels (3 spots): memory gives a break type for 3, agrees on 2 "
            "and disagrees on 1.") in report
    assert "Memory says the bottom is not sand at 1 of them." in report
    assert "| Ledge (Texas) | beach | reef | high | rock |" in report
    assert report[report.index("Settled by geography: 2 spots. The rule: the spot is in a "
                               "sand-barrier region, memory says a sand bottom with high "
                               "confidence, and memory knows of no same-named spot "
                               "elsewhere.") + 3] == (
        "Settled is about the bottom only: of these, 1 have a break type that disagrees with "
        "a verified label, and are in the table above.")
    groups = report[report.index("Candidates for research: 3 spots, grouped by reason. A "
                                 "spot can have more than one."):]
    assert groups[1:6] == ["", "low or medium confidence (1):", "Ledge (Texas)", "",
                           "not sand, or mixed (2):"]
    assert groups[6] == "Point (California); Ledge (Texas)"
    assert groups[groups.index("rocky or reef region (2):") + 1] == (
        "Point (California); Twin (California)")
    assert groups[groups.index("possible name collision (1):") + 1] == "Twin (California)"
    assert "disagrees with a verified label (0):" in groups
    human = report[report.index("For a human to look at (3 most confusing):"):]
    # Ledge 2 + 2 + 1 = 5; Inlet 3; Twin 2. Sandy and Point score 0 and are left out.
    assert human[1:] == [
        "1. Ledge (Texas): memory says reef with high confidence; the unverified label says "
        "beach; memory says a rock bottom in a sand-barrier region; medium confidence on its "
        "bottom.",
        "2. Inlet (New Jersey): memory says beach, the verified label says jetty.",
        "3. Twin (California): the name is also used at Oahu, Hawaii."]
