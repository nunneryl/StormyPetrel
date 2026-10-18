"""The free tide pass's import (pipeline/import_tide_free_pass.py): what it writes, from which rows,
what it refuses, and the report its dry run gives.

Nine spots, one per case, worked by hand:
  San Onofre     the pass says low; the roster holds mid (unattributed, model confidence 0.7):
                 written, mid -> low, the confidence cleared
  The Wedge      two pages say mid; the roster holds mid (unattributed): written, same value,
                 now citing the first page
  Jalama Beach   mondo.surf alone says all, accepted on review; no tide keys yet: written, the keys
                 placed after bottom_evidence
  Steamer Lane   the pass says low; a person set high (reviewed): kept
  Rejected Spot  the pass says low; rejected on review: not imported at all
  Combined       one page low, another mid: low_mid, which no page states: not imported
  Moved Spot     the pass says low; the roster spot is 0.1 degrees (about 11 km) north: left
  Shop           the pass says low; not a surf spot: not a rated spot in the roster
  Already        the roster already holds exactly what the pass would write: the same

No expected value below is produced by calling the code under test.
"""
from __future__ import annotations

import csv
import json
import os

import pytest

from pipeline import config
from pipeline import import_tide_free_pass as imp

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))

SO = "https://www.surf-forecast.com/breaks/San-Onofre"
SO_Q = "San Onofre is a reef that works best around low tide on a south swell."
WEDGE_A = "https://deepswell.com/surf-guide/US/x/The-Wedge/1"
WEDGE_A_Q = "The Wedge works best at mid tide on a south swell."
WEDGE_B = "https://www.surfline.com/surf-report/the-wedge/1"
JALAMA = "https://www.mondo.surf/surf-spot/jalama/guide/10918"
JALAMA_Q = "The bottom is sandy, and the spot works on all tides."
ALREADY = "https://www.surf-forecast.com/breaks/Already"
ALREADY_Q = "Already is a beach that works best around low tide all year."


def _fetch(url, text, call_id):
    return [{"type": "server_tool_use", "id": call_id, "name": "web_fetch", "input": {"url": url}},
            {"type": "web_fetch_tool_result", "tool_use_id": call_id,
             "content": {"type": "web_fetch_result", "url": url,
                         "content": {"type": "document",
                                     "source": {"type": "text", "data": text}}}}]


def _record(name, pages, lat=33.0):
    blocks = [b for i, (url, text) in enumerate(pages) for b in _fetch(url, text, f"c{i}")]
    cited = [url for url, _ in pages][:2] + [None, None]
    return {"done": True, "lat": lat, "lng": -117.0,
            "identity": {"name": name, "region": "California", "lat": lat, "lng": -117.0},
            "research_responses": {"first": [{"stop_reason": "end_turn",
                                              "content": blocks + [{"type": "text", "text": "{}"}]}],
                                   "follow_up": None},
            "research": {f: {"source_url": u, "location": {"verdict": "ok"} if u else None}
                         for f, u in zip(("break_type", "bottom"), cited)}}


def _low(name):
    url = f"https://www.surf-forecast.com/breaks/{name.replace(' ', '-')}"
    return _record(name, [(url, f"{name} is a reef that works best around low tide on a south swell.")])


def results():
    spots = {
        "San Onofre": _record("San Onofre", [(SO, SO_Q)]),
        "The Wedge": _record("The Wedge", [(WEDGE_A, WEDGE_A_Q), (WEDGE_B,
                             "The Wedge is a famous shorebreak that is best around mid tide.")]),
        "Jalama Beach": _record("Jalama Beach", [(JALAMA, JALAMA_Q)]),
        "Steamer Lane": _low("Steamer Lane"),
        "Rejected Spot": _low("Rejected Spot"),
        "Combined": _record("Combined", [
            ("https://www.surf-forecast.com/breaks/Combined", "Combined works best around low tide on a big day."),
            ("https://deepswell.com/surf-guide/US/x/Combined/2", "Combined works best around mid tide in summer.")]),
        "Moved Spot": _low("Moved Spot"),
        "Shop": _low("Shop"),
        "Already": _record("Already", [(ALREADY, ALREADY_Q)]),
    }
    return {"schema": 2, "kind": "full", "spots": {f"{k}|California": v for k, v in spots.items()}}


def _tide(value, source, url=None, evidence=None, confidence=None):
    return {"tide_preference": value, "tide_preference_source": source,
            "tide_preference_source_url": url, "tide_preference_evidence": evidence,
            "tide_preference_confidence": confidence}


def _spot(name, lat=33.0, **more):
    spot = {"name": name, "lat": lat, "lng": -117.0, "region_hint": "California",
            "break_type": "reef", "break_type_evidence": None, "bottom": "rock",
            "bottom_evidence": None, "is_valid_surf_spot": True}
    spot.update(more)
    return spot


def roster():
    return [
        _spot("San Onofre", **_tide("mid", "unattributed", confidence=0.7)),
        _spot("The Wedge", **_tide("mid", "unattributed", confidence=0.65)),
        _spot("Jalama Beach"),
        _spot("Steamer Lane", **_tide("high", "reviewed")),
        _spot("Rejected Spot", **_tide("mid", "unattributed")),
        _spot("Combined", **_tide("mid", "unattributed")),
        _spot("Moved Spot", lat=33.1, **_tide("mid", "unattributed")),
        _spot("Shop", is_valid_surf_spot=False, **_tide("mid", "unattributed")),
        _spot("Already", **_tide("low", "researched", ALREADY, ALREADY_Q)),
    ]


DECISIONS = {"spots": [
    {"name": "Rejected Spot", "state": "California", "decision": "reject", "note": "Not surf."},
    {"name": "Jalama Beach", "state": "California", "decision": "accept", "value": "all",
     "note": "All tides, checked."},
]}
# The review file as the pass writes it after these decisions: import=yes on the seven rows the
# import may write, no on the rejected and the combined ones.
REVIEW = [("San Onofre", "low", "yes"), ("The Wedge", "mid", "yes"), ("Jalama Beach", "all", "yes"),
          ("Steamer Lane", "low", "yes"), ("Rejected Spot", "low", "no"),
          ("Combined", "low_mid", "no"), ("Moved Spot", "low", "yes"), ("Shop", "low", "yes"),
          ("Already", "low", "yes")]


def _write_review(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=["name", "state", "status", "value", "import"])
        writer.writeheader()
        for name, value, yes in rows:
            writer.writerow({"name": name, "state": "California", "status": "proposed",
                             "value": value, "import": yes})


@pytest.fixture
def files(tmp_path):
    paths = {k: tmp_path / f"{k}.{ext}" for k, ext in (
        ("results", "json"), ("roster", "json"), ("decisions", "json"), ("review", "csv"))}
    paths["results"].write_text(json.dumps(results()), encoding="utf-8")
    paths["roster"].write_text(json.dumps(roster(), indent=2, ensure_ascii=False) + "\n",
                               encoding="utf-8")
    paths["decisions"].write_text(json.dumps(DECISIONS), encoding="utf-8")
    _write_review(paths["review"], REVIEW)
    return paths


def _run(files, *more):
    return imp.main(["--results", str(files["results"]), "--roster", str(files["roster"]),
                     "--review", str(files["review"]), "--decisions", str(files["decisions"]),
                     *more])


def _by_name(path):
    return {s["name"]: s for s in json.loads(path.read_text(encoding="utf-8"))}


DRY_RUN = [
    "7 rows the review file marks import=yes; the pass agrees with every one.",
    "",
    "What the import does, by value:",
    "",
    "| | low | low_mid | mid | mid_high | high | all | total |",
    "|---|---|---|---|---|---|---|---|",
    "| written | 1 | 0 | 1 | 0 | 0 | 1 | 3 |",                  # San Onofre, The Wedge, Jalama
    "| already the same | 1 | 0 | 0 | 0 | 0 | 0 | 1 |",         # Already
    "| kept: a higher-ranked source holds it | 1 | 0 | 0 | 0 | 0 | 0 | 1 |",   # Steamer Lane
    "| not a rated spot in the roster | 1 | 0 | 0 | 0 | 0 | 0 | 1 |",          # Shop
    "| left: moved more than 1 km since the research | 1 | 0 | 0 | 0 | 0 | 0 | 1 |",  # Moved
    "",
    "Spots whose tide preference changes: 2. Spots that keep their value and now cite a page: 1.",
    "Changes: (none) -> all 1, mid -> low 1.",
    "",
    "| Spot | Now | Becomes | Page |",
    "|---|---|---|---|",
    f"| San Onofre (California) | mid (unattributed) | low | {SO} |",
    f"| Jalama Beach (California) | (none) (no source) | all | {JALAMA} |",
    "",
    "Dry run: nothing written.",
]


def test_the_dry_run_reports_by_value_and_writes_nothing(files, capsys):
    before = {k: p.read_bytes() for k, p in files.items()}
    assert _run(files, "--dry-run") == 0
    assert capsys.readouterr().out.splitlines() == DRY_RUN
    assert {k: p.read_bytes() for k, p in files.items()} == before


def test_the_import_writes_each_spot_through_the_shared_builder(files, capsys):
    before = _by_name(files["roster"])
    assert _run(files) == 0
    assert capsys.readouterr().out.splitlines()[-1] == "Wrote the roster."
    after = _by_name(files["roster"])
    assert {k: after["San Onofre"][k] for k in _tide(None, None)} == _tide(
        "low", "researched", SO, SO_Q, confidence=None)
    assert {k: after["The Wedge"][k] for k in _tide(None, None)} == _tide(
        "mid", "researched", WEDGE_A, WEDGE_A_Q, confidence=None)
    # Jalama had no tide keys: four placed after bottom_evidence, and no confidence key added.
    assert list(after["Jalama Beach"]) == [
        "name", "lat", "lng", "region_hint", "break_type", "break_type_evidence", "bottom",
        "bottom_evidence", "tide_preference", "tide_preference_source",
        "tide_preference_source_url", "tide_preference_evidence", "is_valid_surf_spot"]
    assert (after["Jalama Beach"]["tide_preference"], after["Jalama Beach"]["tide_preference_evidence"]) \
        == ("all", JALAMA_Q)
    for name in ("Steamer Lane", "Rejected Spot", "Combined", "Moved Spot", "Shop", "Already"):
        assert after[name] == before[name], name


def test_spots_it_does_not_write_keep_their_exact_text(files, capsys):
    raw = files["roster"].read_text(encoding="utf-8")
    assert _run(files) == 0
    new = files["roster"].read_text(encoding="utf-8")
    old_lines, new_lines = raw.splitlines(), new.splitlines()
    # Only lines inside the three written spots differ.
    changed = {line for line in set(new_lines) ^ set(old_lines)}
    assert all(("tide_preference" in line) for line in changed), sorted(changed)


def test_a_second_run_has_nothing_left_to_write(files, capsys):
    assert _run(files) == 0
    capsys.readouterr()
    assert _run(files, "--dry-run") == 0
    out = capsys.readouterr().out.splitlines()
    assert "| written | 0 | 0 | 0 | 0 | 0 | 0 | 0 |" in out
    assert "| already the same | 2 | 0 | 1 | 0 | 0 | 1 | 4 |" in out


@pytest.mark.parametrize("rows, why", [
    ([r for r in REVIEW if r[0] != "Shop"],
     "Shop (California): importable as low, but not import=yes in the review file"),
    ([("San Onofre", "mid", "yes")] + REVIEW[1:],
     "San Onofre (California): the review file says mid, the pass now gives low"),
    ([r if r[0] != "Combined" else ("Combined", "low_mid", "yes") for r in REVIEW],
     "Combined (California): import=yes as low_mid in the review file, but not importable now"),
])
def test_a_review_file_that_does_not_match_stops_the_import(files, rows, why):
    _write_review(files["review"], rows)
    before = {k: p.read_bytes() for k, p in files.items()}
    for more in (["--dry-run"], []):
        with pytest.raises(SystemExit) as err:
            _run(files, *more)
        lines = str(err.value).splitlines()
        assert lines[0].endswith("(1); re-run `python3 -m pipeline.tide_free_pass`, review it, "
                                 "and try again. Nothing was written:")
        assert lines[1:] == ["  " + why]
    assert {k: p.read_bytes() for k, p in files.items()} == before


def test_a_leftover_model_confidence_is_cleared_even_when_the_value_is_the_same(files, capsys):
    spots = roster()
    spots[-1]["tide_preference_confidence"] = 0.5     # Already: the same researched value
    files["roster"].write_text(json.dumps(spots, indent=2) + "\n", encoding="utf-8")
    assert _run(files) == 0
    out = capsys.readouterr().out.splitlines()
    assert "| written | 2 | 0 | 1 | 0 | 0 | 1 | 4 |" in out
    assert _by_name(files["roster"])["Already"]["tide_preference_confidence"] is None


def test_no_review_file_no_import(files):
    files["review"].unlink()
    with pytest.raises(SystemExit) as err:
        _run(files, "--dry-run")
    assert str(err.value).startswith("no review file at ")


def test_it_writes_researched_only_through_the_shared_helpers():
    with open(os.path.join(ROOT, "pipeline", "import_tide_free_pass.py"), encoding="utf-8") as fh:
        src = fh.read()
    assert "tide_preference_fields(o[\"value\"], SOURCE, url=page[\"url\"], evidence=quote)" in src
    assert "tide_preference_may_write(spot, SOURCE)" in src
    assert imp.SOURCE == "researched"
    imports = [line for line in src.splitlines() if line.startswith(("import ", "from "))
               or line.startswith("    ") and line.strip().startswith(("import ", "from ."))]
    for name in ("db_import", "supabase", "psycopg2"):
        assert not any(name in line for line in imports), name
