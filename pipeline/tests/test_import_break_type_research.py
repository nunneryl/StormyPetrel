"""The import of the break-type research into the roster: what it writes, from which source,
what it never overwrites, what its dry run says, and the summary it commits.

WHAT THESE TESTS HOLD:

  1. a checked research value is written as 'researched' with its page and quote, over
     anything ranked below it; otherwise a memory answer as 'model_recall' with no page, but
     only into an empty value or over the unverified default 'beach', never over a verified
     label or another held value, and never at low confidence; otherwise nothing;
  2. nothing is written from a source ranked below the one already there, a value already
     held exactly is not rewritten, and a spot that has moved is left alone;
  3. the dry run counts each source's writes, lists every break-type change and flags the
     verified labels research (or memory) changes, and writes nothing;
  4. the real run changes only the spots it writes, in the roster's own format, and writes
     a summary with both sand-bottom flags and no page text;
  5. it reads only the two results files and the roster, and writes only the roster and the
     summary: no database, nothing a rating or a forecast row reads.

No expected value below is produced by calling the code under test.
"""
from __future__ import annotations

import ast
import json
import os
import subprocess
import shutil

import pytest

from pipeline import config
from pipeline import import_break_type_research as imp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
MODULE = os.path.join(ROOT, "pipeline", "import_break_type_research.py")

URL_A = "https://en.wikipedia.org/wiki/Reefy"
URL_C = "https://www.surfguide.com/pointy"
URL_E = "https://www.surfguide.com/fresh"
URL_G = "https://www.surfguide.com/cited"
URL_H = "https://www.surfguide.com/same"
Q_A = "Reefy is a reef break over a shallow lava rock shelf"
Q_C = "The point breaks over cobblestones and boulders"
Q_E = "Fresh is a beach break over a sand bottom"
Q_G = "Cited breaks over a rock reef at the north end"
Q_H = "Same is a jetty break over a sand bottom"


def _bt(value, source, url=None, evidence=None):
    return {"break_type": value, "break_type_source": source,
            "break_type_confidence": {"reviewed": "high", "researched": "medium"}.get(
                source, "low"),
            "break_type_source_url": url, "break_type_evidence": evidence}


def _spot(name, state, lat, lng, verified=None, **fields):
    spot = {"name": name, "lat": lat, "lng": lng, "region_hint": state}
    spot.update(fields)
    spot.update({"is_valid_surf_spot": True, "verification_confidence": verified,
                 "tags": {"note": "kept as it is"}})
    return spot


def roster():
    """Eight rated spots, one per case, and one that is not rated."""
    return [
        # A: a verified 'beach' that research finds is a reef on rock.
        _spot("Reefy", "Hawaii", 21.6, -158.0, verified="high",
              **_bt("beach", "unattributed")),
        # B: settled by geography, no research; memory says beach on sand.
        _spot("Sandy", "New Jersey", 40.0, -74.0, **_bt("beach", "unattributed")),
        # C: a verified 'point': research found no break type but a cobble bottom; memory
        #    says reef.
        _spot("Pointy", "California", 36.95, -122.02, verified="medium",
              **_bt("point", "unattributed")),
        # D: no break type; neither research nor memory could tell.
        _spot("Nowhere", "Maine", 43.5, -70.3),
        # E: no break type; research finds a beach on sand.
        _spot("Fresh", "Texas", 27.6, -97.2),
        # F: memory answered for a spot 0.1 degrees of latitude (about 11 km) away from
        #    where the roster now has it.
        _spot("Moved", "Florida", 28.0, -80.5, **_bt("beach", "unattributed")),
        # G: a reviewed reef: research's 'point' may not replace it; its bottom is new.
        _spot("Cited", "Oregon", 45.0, -124.0, **_bt("reef", "reviewed")),
        # H: already researched, exactly as research now has it.
        _spot("Same", "Texas", 27.0, -97.0, **_bt("jetty", "researched", URL_H, Q_H)),
        # I: an unverified reef memory may not replace; memory's bottom is low confidence.
        _spot("Held", "Georgia", 31.0, -81.4, **_bt("reef", "unattributed")),
        # J: nothing held; memory's break type is low confidence, its bottom medium.
        _spot("Shaky", "South Carolina", 33.0, -79.0),
        # K: an earlier import already wrote exactly what memory says.
        _spot("Again", "North Carolina", 35.0, -75.5, **_bt("beach", "model_recall"),
              bottom="sand", bottom_source="model_recall", bottom_confidence="low",
              bottom_source_url=None, bottom_evidence=None),
        # Not rated: never touched.
        dict(_spot("Gone", "Texas", 26.0, -97.0, **_bt("beach", "unattributed")),
             is_valid_surf_spot=False),
    ]


def _claim(value, url, quote):
    return {"status": "researched", "value": value, "source_url": url, "quote": quote,
            "reason": None}


UNKNOWN = {"status": "unknown", "value": "unknown", "source_url": None, "quote": None,
           "reason": "no page it read says"}
FLAG = {"sand": "yes", "rock": "no", "coral": "no", "cobble": "no", "mixed": "unknown",
        "unknown": "unknown"}


def _researched(lat, lng, kind, bottom):
    return {"done": True, "lat": lat, "lng": lng,
            "research": {"break_type": kind, "bottom": bottom,
                         "sand_bottom": FLAG[bottom["value"]]}}


def full_results():
    return {"schema": 2, "kind": "full", "settled": {"Sandy|New Jersey": {}},
            "spots": {
                "Reefy|Hawaii": _researched(21.6, -158.0, _claim("reef", URL_A, Q_A),
                                            _claim("rock", URL_A, Q_A)),
                "Pointy|California": _researched(36.95, -122.02, dict(UNKNOWN),
                                                 _claim("cobble", URL_C, Q_C)),
                "Nowhere|Maine": _researched(43.5, -70.3, dict(UNKNOWN), dict(UNKNOWN)),
                "Fresh|Texas": _researched(27.6, -97.2, _claim("beach", URL_E, Q_E),
                                           _claim("sand", URL_E, Q_E)),
                "Cited|Oregon": _researched(45.0, -124.0, _claim("point", URL_G, Q_G),
                                            _claim("rock", URL_G, Q_G)),
                "Same|Texas": _researched(27.0, -97.0, _claim("jetty", URL_H, Q_H),
                                          _claim("sand", URL_H, Q_H)),
                # Researched once, no longer a rated spot in the roster.
                "Ghost|Hawaii": _researched(20.0, -156.0, _claim("reef", URL_A, Q_A),
                                            dict(UNKNOWN)),
            }}


def _remembered(lat, lng, kind, bottom, kind_conf="high", bottom_conf="high"):
    return {"done": True, "lat": lat, "lng": lng,
            "memory": {"break_type": {"value": kind, "confidence": kind_conf},
                       "bottom": {"value": bottom, "confidence": bottom_conf},
                       "sand_bottom": FLAG[bottom]}}


def memory_results():
    return {"schema": 1, "kind": "memory_only", "spots": {
        "Reefy|Hawaii": _remembered(21.6, -158.0, "reef", "rock"),
        "Sandy|New Jersey": _remembered(40.0, -74.0, "beach", "sand"),
        "Pointy|California": _remembered(36.95, -122.02, "reef", "rock", kind_conf="medium"),
        "Nowhere|Maine": _remembered(43.5, -70.3, "unknown", "unknown", "low", "low"),
        "Fresh|Texas": _remembered(27.6, -97.2, "beach", "sand"),
        "Moved|Florida": _remembered(28.1, -80.5, "point", "rock"),
        "Cited|Oregon": _remembered(45.0, -124.0, "reef", "rock"),
        "Same|Texas": _remembered(27.0, -97.0, "jetty", "sand"),
        "Held|Georgia": _remembered(31.0, -81.4, "point", "rock", bottom_conf="low"),
        "Shaky|South Carolina": _remembered(33.0, -79.0, "beach", "sand", kind_conf="low",
                                            bottom_conf="medium"),
        "Again|North Carolina": _remembered(35.0, -75.5, "beach", "sand"),
    }}


@pytest.fixture
def files(tmp_path):
    paths = {"roster": tmp_path / "roster.json", "full": tmp_path / "full.json",
             "memory": tmp_path / "memory.json", "summary": tmp_path / "summary.json"}
    paths["roster"].write_text(json.dumps(roster(), indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")
    paths["full"].write_text(json.dumps(full_results()), encoding="utf-8")
    paths["memory"].write_text(json.dumps(memory_results()), encoding="utf-8")
    return paths


def _run(files, *more):
    return imp.main(["--roster", str(files["roster"]), "--full", str(files["full"]),
                     "--memory", str(files["memory"]), "--summary", str(files["summary"]),
                     *more])


def _by_name(path):
    return {spot["name"]: spot for spot in json.loads(path.read_text(encoding="utf-8"))}


# --- 1-2. what each spot gets -------------------------------------------------------------

def test_each_spot_gets_what_its_sources_allow(files):
    before = _by_name(files["roster"])
    assert _run(files) == 0
    after = _by_name(files["roster"])
    pick = lambda spot, keys: {k: spot.get(k) for k in keys}  # noqa: E731
    bt, bottom = config.BREAK_TYPE_FIELDS, config.BOTTOM_FIELDS
    # A: research, both values, with the page and the quote.
    assert pick(after["Reefy"], bt) == _bt("reef", "researched", URL_A, Q_A)
    assert pick(after["Reefy"], bottom) == {
        "bottom": "rock", "bottom_source": "researched", "bottom_confidence": "medium",
        "bottom_source_url": URL_A, "bottom_evidence": Q_A}
    # B: no research; memory for both, with no page: over the unverified default 'beach',
    #    and into the empty bottom.
    assert pick(after["Sandy"], bt) == _bt("beach", "model_recall")
    assert pick(after["Sandy"], bottom) == {
        "bottom": "sand", "bottom_source": "model_recall", "bottom_confidence": "low",
        "bottom_source_url": None, "bottom_evidence": None}
    # C: research found no break type, and memory may not replace a verified label; the
    #    bottom is research's.
    assert pick(after["Pointy"], bt) == _bt("point", "unattributed")
    assert after["Pointy"]["bottom"] == "cobble"
    assert after["Pointy"]["bottom_source"] == "researched"
    # D: neither could tell: still no keys at all.
    assert after["Nowhere"] == before["Nowhere"]
    # E: research, and the keys where the other spots have them.
    assert pick(after["Fresh"], bt) == _bt("beach", "researched", URL_E, Q_E)
    assert list(after["Fresh"]) == [
        "name", "lat", "lng", "region_hint", *bt, *bottom, "is_valid_surf_spot",
        "verification_confidence", "tags"]
    # F: moved since memory answered: left as it is.
    assert after["Moved"] == before["Moved"]
    # G: the reviewed reef stays; the bottom is new, so research writes it.
    assert pick(after["Cited"], bt) == _bt("reef", "reviewed")
    assert pick(after["Cited"], bottom)["bottom"] == "rock"
    # H: the same researched jetty; its new bottom goes after its break type.
    assert pick(after["Same"], bt) == _bt("jetty", "researched", URL_H, Q_H)
    assert list(after["Same"])[4:14] == [*bt, *bottom]
    # I: memory may not replace an unverified reef, nor write a low-confidence bottom.
    assert after["Held"] == before["Held"]
    # J: no low-confidence break type; the medium-confidence bottom fills the empty one.
    assert "break_type" not in after["Shaky"]
    assert pick(after["Shaky"], bottom) == {
        "bottom": "sand", "bottom_source": "model_recall", "bottom_confidence": "low",
        "bottom_source_url": None, "bottom_evidence": None}
    assert list(after["Shaky"]) == ["name", "lat", "lng", "region_hint", *bottom,
                                    "is_valid_surf_spot", "verification_confidence", "tags"]
    # K: already exactly what memory says.
    assert after["Again"] == before["Again"]
    # Not rated: untouched.
    assert after["Gone"] == before["Gone"]
    assert list(after) == list(before)


def test_a_quote_research_kept_is_written_as_it_was_checked():
    # The research stores its quote cut to 300 characters; the import writes that quote.
    long_quote = "Reefy breaks " + "over lava " * 40
    full = full_results()
    full["spots"]["Reefy|Hawaii"]["research"]["break_type"]["quote"] = long_quote
    planned = imp.plan(roster(), full, memory_results())
    written = planned[0][4]["break_type"]["fields"]["break_type_evidence"]
    assert len(written) == 300 and written.endswith("…")
    assert written.startswith("Reefy breaks over lava over lava")


def test_research_never_overwrites_a_reviewed_value_and_memory_never_a_researched_one():
    spots = roster()
    spots[0].update(_bt("point", "reviewed"))        # A: a person set it
    spots[4].update(_bt("beach", "researched", URL_E, Q_E))   # E: researched already
    full = full_results()
    full["spots"]["Fresh|Texas"]["research"]["break_type"] = dict(UNKNOWN)
    planned = imp.plan(spots, full, memory_results())
    assert (planned[0][4]["break_type"]["action"], planned[0][4]["break_type"]["rule"]) == (
        "kept", "research_kept")
    assert (planned[4][4]["break_type"]["action"], planned[4][4]["break_type"]["rule"]) == (
        "skipped", "memory_skipped_held")
    assert planned[4][4]["break_type"]["source"] == "model_recall"


@pytest.mark.parametrize("held, source, verified, confidence, rule", [
    (None, None, None, "high", "memory_filled_empty"),
    (None, None, "high", "medium", "memory_filled_empty"),     # empty is not a label
    ("beach", "unattributed", None, "high", "memory_replaced_default"),
    ("beach", "unattributed", "high", "high", "memory_skipped_verified"),
    ("beach", "unattributed", "medium", "high", "memory_skipped_verified"),
    ("reef", "unattributed", None, "high", "memory_skipped_held"),
    ("beach", "researched", None, "high", "memory_skipped_held"),
    ("beach", "algorithm_default", None, "high", "memory_skipped_held"),
    (None, None, None, "low", "memory_skipped_low"),
    ("beach", "unattributed", None, "low", "memory_skipped_low"),
    ("reef", "unattributed", "high", "low", "memory_skipped_verified"),   # the first rule
    (None, None, None, None, "memory_filled_empty"),            # no confidence is not low
])
def test_the_memory_rules_for_the_break_type(held, source, verified, confidence, rule):
    spot = {"name": "X", "region_hint": "Texas", "break_type": held,
            "break_type_source": source, "verification_confidence": verified}
    assert imp.memory_rule(spot, "break_type", confidence) == rule


@pytest.mark.parametrize("held, source, verified, confidence, rule", [
    (None, None, None, "high", "memory_filled_empty"),
    (None, None, "high", "high", "memory_filled_empty"),
    # The bottom has no default to replace: any held bottom stays.
    ("sand", "unattributed", None, "high", "memory_skipped_held"),
    ("rock", "model_recall", None, "high", "memory_skipped_held"),
    ("rock", "unattributed", "high", "high", "memory_skipped_verified"),
    (None, None, None, "low", "memory_skipped_low"),
])
def test_the_memory_rules_for_the_bottom(held, source, verified, confidence, rule):
    spot = {"name": "X", "region_hint": "Texas", "bottom": held, "bottom_source": source,
            "verification_confidence": verified}
    assert imp.memory_rule(spot, "bottom", confidence) == rule


def test_an_answer_that_did_not_finish_is_not_used():
    # Fresh's research stopped before it finished, so memory answers instead; Sandy's
    # memory request stopped too, so Sandy is left as it is.
    full, memory = full_results(), memory_results()
    full["spots"]["Fresh|Texas"]["done"] = False
    memory["spots"]["Sandy|New Jersey"]["done"] = False
    planned = {spot["name"]: changes for spot, _, _, _, changes in
               imp.plan(roster(), full, memory)}
    assert [(planned["Fresh"][f]["action"], planned["Fresh"][f]["source"],
             planned["Fresh"][f]["after"]) for f in ("break_type", "bottom")] == [
        ("write", "model_recall", "beach"), ("write", "model_recall", "sand")]
    assert [planned["Sandy"][f]["action"] for f in ("break_type", "bottom")] == ["left", "left"]


# --- 3. the dry run ---------------------------------------------------------------------------

DRY_RUN = [
    "11 rated spots in the roster. The full run researched 6 of them (1 more settled by "
    "geography); the memory run answered 11.",
    "In the full run but not a rated spot in the roster, so not used: Ghost|Hawaii.",
    "",
    "What each rule wrote and skipped (a memory answer stopped by more than one rule is "
    "counted under the first):",
    "",
    "| Rule | break type | bottom |",
    "|---|---|---|",
    "| research (cited): written | 2 | 5 |",            # A E | A C E G H
    "| research: already the same | 1 | 0 |",           # H
    "| research: skipped, a higher-ranked source holds it | 1 | 0 |",   # G
    "| memory: written where the value was empty | 0 | 2 |",           # - | B J
    "| memory: written over the unverified default 'beach' | 1 | 0 |",  # B
    "| memory: already the same | 1 | 1 |",             # K | K
    "| memory: skipped, a verified label | 1 | 0 |",     # C
    "| memory: skipped, the spot holds another value | 1 | 0 |",       # I
    "| memory: skipped, low confidence | 1 | 1 |",       # J | I
    "| left: neither research nor memory gave a value | 1 | 1 |",      # D | D
    "| left: the spot has moved since the answers | 1 | 1 |",          # F | F
    "| total | 11 | 11 |",
    "Memory answers written for the break type, by their confidence: 1 high, 0 medium, 0 low, "
    "0 none.",
    "Memory answers written for the bottom, by their confidence: 1 high, 1 medium, 0 low, "
    "0 none.",
    "",
    "Spots whose break type changes (2):",
    "",
    "| Spot | Now | Becomes | From | Flag |",
    "|---|---|---|---|---|",
    "| Reefy (Hawaii) | beach (unattributed, verified) | reef | researched | VERIFIED LABEL "
    "CHANGED BY RESEARCH |",
    "| Fresh (Texas) | (none) | beach | researched |  |",
    "",
    "Verified labels that research changes: 1.",
    "Verified labels that memory changes (the memory rules allow none): 0.",
    "Spots that had no break type and get one: 1.",
    "",
    # A rock, C cobble, E sand, G rock, H sand (research); B sand, J sand (memory).
    "Bottoms written, by value: sand 4, rock 2, coral 0, cobble 1, mixed 0, unknown 0.",
    "Research and memory both say sand at 2 spots.",
    "Left as they are, moved more than 1 km since the answers: Moved (Florida).",
    "",
]


def test_the_dry_run_reports_and_writes_nothing(files, capsys):
    before = {name: path.read_bytes() for name, path in files.items() if path.exists()}
    assert _run(files, "--dry-run") == 0
    assert capsys.readouterr().out.splitlines() == DRY_RUN + ["Dry run: nothing written."]
    assert {name: path.read_bytes() for name, path in files.items()
            if path.exists()} == before
    assert not files["summary"].exists()


def test_the_real_run_reports_the_same_and_says_it_wrote(files, capsys):
    assert _run(files) == 0
    assert capsys.readouterr().out.splitlines() == DRY_RUN + [
        "Wrote the roster and the summary."]


# --- 4. what the real run writes ----------------------------------------------------------------

def test_a_spot_the_import_does_not_write_keeps_its_exact_text(files):
    before = files["roster"].read_text(encoding="utf-8")
    assert _run(files) == 0
    after = files["roster"].read_text(encoding="utf-8")
    for name in ("Nowhere", "Moved", "Gone"):
        block = json.dumps(next(s for s in roster() if s["name"] == name), indent=2,
                           ensure_ascii=False).replace("\n", "\n  ")
        assert block in before and block in after, name
    assert after.endswith("}\n]\n")


def test_the_committed_roster_is_written_back_byte_for_byte_when_nothing_changes():
    path = config.DEFAULT_ENRICHED_OUTPUT
    raw = path.read_text(encoding="utf-8")
    assert imp.roster_text(json.loads(raw)) == raw


def test_the_summary_has_both_sand_flags_and_no_page_text(files):
    assert _run(files) == 0
    text = files["summary"].read_text(encoding="utf-8")
    data = json.loads(text)
    assert (data["schema"], data["from"]) == (1, {"full_run": "full.json",
                                                  "memory": "memory.json"})
    spots = {s["name"]: s for s in data["spots"]}
    assert list(spots) == ["Reefy", "Sandy", "Pointy", "Nowhere", "Fresh", "Moved", "Cited",
                           "Same", "Held", "Shaky", "Again"]
    assert spots["Fresh"] == {
        "name": "Fresh", "state": "Texas", "settled_by_geography": False,
        "research": {"break_type": {"value": "beach", "status": "researched",
                                    "source_url": URL_E, "quote": Q_E, "reason": None},
                     "bottom": {"value": "sand", "status": "researched",
                                "source_url": URL_E, "quote": Q_E, "reason": None},
                     "sand_bottom": "yes"},
        "memory": {"break_type": {"value": "beach", "confidence": "high"},
                   "bottom": {"value": "sand", "confidence": "high"}, "sand_bottom": "yes"},
        "roster": {"break_type": {"value": "beach", "source": "researched"},
                   "bottom": {"value": "sand", "source": "researched"}},
        "sand_bottom": {"research": "yes", "memory": "yes", "both_say_sand": True}}
    assert spots["Sandy"]["settled_by_geography"] is True
    assert spots["Sandy"]["research"] is None
    assert spots["Sandy"]["sand_bottom"] == {"research": None, "memory": "yes",
                                             "both_say_sand": False}
    assert spots["Nowhere"]["roster"] == {"break_type": {"value": None, "source": None},
                                          "bottom": {"value": None, "source": None}}
    assert spots["Moved"]["roster"]["break_type"] == {"value": "beach",
                                                      "source": "unattributed"}
    assert [name for name, s in spots.items() if s["sand_bottom"]["both_say_sand"]] == [
        "Fresh", "Same"]
    # One spot per line, and nothing of the saved responses or the pages' text.
    # "{", schema, what, from and '"spots": [' (5), a line per spot (11), "  ]" and "}" (2).
    assert len(text.splitlines()) == 5 + 11 + 2
    for word in ("research_responses", "web_fetch", "\"data\""):
        assert word not in text


def test_the_summary_is_committed_and_the_results_are_not():
    if shutil.which("git") is None or not os.path.isdir(os.path.join(ROOT, ".git")):
        pytest.skip("not a git checkout")

    def ignored(path):
        return subprocess.run(["git", "check-ignore", "-q", os.path.relpath(path, ROOT)],
                              cwd=ROOT).returncode == 0
    assert imp.SUMMARY_OUTPUT.name == "spot_break_research_summary.json"
    assert not ignored(imp.SUMMARY_OUTPUT)
    from pipeline import research_break_type as rb
    assert ignored(rb.DEFAULT_FULL_OUTPUT) and ignored(rb.DEFAULT_MEMORY_OUTPUT)


# --- refusals ---------------------------------------------------------------------------------

def test_it_reads_only_results_files_of_the_kind_and_schema_it_knows(files):
    other = files["full"].parent / "other.json"
    other.write_text(json.dumps(dict(full_results(), schema=1)), encoding="utf-8")
    with pytest.raises(SystemExit, match="is not a full-run results file of schema 2"):
        imp.main(["--roster", str(files["roster"]), "--full", str(other),
                  "--memory", str(files["memory"]), "--dry-run"])
    with pytest.raises(SystemExit, match="is not a memory-only results file of schema 1"):
        imp.main(["--roster", str(files["roster"]), "--full", str(files["full"]),
                  "--memory", str(files["full"]), "--dry-run"])
    with pytest.raises(SystemExit, match="no full-run results file at "):
        imp.main(["--roster", str(files["roster"]), "--full", str(other) + ".missing",
                  "--memory", str(files["memory"]), "--dry-run"])
    with pytest.raises(SystemExit, match="refusing to write the summary over"):
        imp.main(["--roster", str(files["roster"]), "--full", str(files["full"]),
                  "--memory", str(files["memory"]), "--summary", str(files["roster"])])


# --- 5. what it can reach -------------------------------------------------------------------

def test_it_imports_no_database_client_and_nothing_that_rates():
    with open(MODULE, encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(("." * node.level) + (node.module or ""))
    assert imported == {"__future__", "argparse", "json", "sys", "pathlib", ".", ".config",
                        ".geo"}


def test_neither_value_is_read_by_the_ratings_or_the_forecast_rows():
    # Where break_type is read at all, outside its writers: descriptions, the review queue,
    # and the NWPS trust diagnostic's tier — none of them in the forecast run's rating or
    # row code. The bottom is read nowhere yet.
    readers = []
    for folder in ("pipeline", os.path.join("pipeline", "forecast")):
        for name in sorted(os.listdir(os.path.join(ROOT, folder))):
            if name.endswith(".py"):
                with open(os.path.join(ROOT, folder, name), encoding="utf-8") as fh:
                    text = fh.read()
                if '"break_type"' in text or "'break_type'" in text or '"bottom"' in text:
                    readers.append(os.path.join(folder, name).replace(os.sep, "/"))
    assert readers == [
        "pipeline/audit_review_queue.py", "pipeline/config.py",
        "pipeline/db_import.py", "pipeline/generate_descriptions.py",
        "pipeline/import_break_type_research.py", "pipeline/research_break_type.py",
        "pipeline/scrape_surf_forecast.py", "pipeline/verify_spots.py",
        "pipeline/forecast/nwps_nearshore.py"]
    for name in ("interpret.py", "run_all.py"):
        path = os.path.join(ROOT, "pipeline", name)
        if os.path.exists(path):
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            assert "break_type" not in text and "bottom\"" not in text, name
