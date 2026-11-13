"""pipeline/apply_buoy_assign.py: run 74's buoy choices applied where the current buoy is silent,
past its cap, or missing, and nowhere else.

Expected values are written by hand from run 74's report (docs/face_validation/run-74) and the
README's tables, not from the module.
"""
from __future__ import annotations

import collections
import json

import pytest

from pipeline import apply_buoy_assign as A
from pipeline.db_import import _validate_coord_derived

ROSTER = A.ENRICHED


def _row(outcome, choice=None, km=None, fallbacks=None, wave=True, cap=True, los=True,
         current="46000", name="X", cap_km=150):
    cur = None if outcome in ("gain", "none") else {
        "id": current, "is_wave_station": wave, "within_cap": cap, "los": los}
    return {"name": name, "outcome": outcome, "current": cur, "choice": choice,
            "choice_km": km, "fallbacks": fallbacks or [], "cap_km": cap_km}


# --------------------------------------------------------------------------- #
# Which rows                                                                   #
# --------------------------------------------------------------------------- #

def test_a_gain_is_taken():
    assert A.why(_row("gain", "46025", 60.02)) == "gain"


def test_a_silent_buoy_is_replaced_or_cleared():
    assert A.why(_row("change", "46028", 56.88, wave=False)) == "silent"
    assert A.why(_row("loss", wave=False)) == "silent"


def test_a_buoy_past_its_cap_is_replaced_or_cleared():
    assert A.why(_row("change", "42098", 46.9, cap=False, los=False)) == "past cap"
    assert A.why(_row("loss", cap=False, los=False)) == "past cap"


def test_a_working_buoy_is_kept_however_the_rule_differs():
    assert A.why(_row("change", "46027", 79.5, los=False)) is None     # blocked by land
    assert A.why(_row("loss", los=False)) is None
    assert A.why(_row("change", "44065", 30.29)) is None                # a nearer one passes
    assert A.why(_row("same", "46266", 0.83)) is None
    assert A.why(_row("none")) is None


def test_fields_for_a_choice_and_for_none():
    assert A.fields(_row("change", "42033", 13.684, ["42034", "42092", "42032"])) == {
        "nearest_buoy_id": "42033", "nearest_buoy_dist_km": 13.68,
        "fallback_buoy_ids": ["42034", "42092", "42032"]}
    assert A.fields(_row("loss", wave=False)) == {
        "nearest_buoy_id": None, "nearest_buoy_dist_km": None, "fallback_buoy_ids": []}


def test_confidence_is_the_rules():
    # compute_nearest_buoy: 1.0 within half the cap, 0.75 beyond, 0.0 with no buoy.
    assert A.confidence(_row("gain", "46025", 60.02)) == 1.0          # 60.02 <= 75
    assert A.confidence(_row("gain", "46025", 75.0)) == 1.0           # the edge, kept
    assert A.confidence(_row("gain", "46014", 89.13)) == 0.75
    assert A.confidence(_row("gain", "41009", 147.03, cap_km=350)) == 1.0
    assert A.confidence(_row("loss", wave=False)) == 0.0


def test_apply_refuses_a_name_not_on_the_roster():
    with pytest.raises(KeyError):
        A.apply([{"name": "A"}], [("B", "gain", {"nearest_buoy_id": "1"}, 1.0)])


def test_apply_writes_the_fields_and_an_existing_confidence():
    cleared = {"nearest_buoy_id": None, "nearest_buoy_dist_km": None, "fallback_buoy_ids": []}
    roster = [{"name": "A", "nearest_buoy_id": "51205", "nearest_buoy_dist_km": 12.81,
               "enrichment_confidence": {"nearest_buoy": 1.0, "orientation": 0.9}},
              {"name": "B", "nearest_buoy_id": "51208", "nearest_buoy_dist_km": 10.68}]
    assert A.apply(roster, [("A", "silent", cleared, 0.0), ("B", "silent", cleared, 0.0)]) == 2
    assert roster == [{"name": "A", "nearest_buoy_id": None, "nearest_buoy_dist_km": None,
                       "fallback_buoy_ids": [],
                       "enrichment_confidence": {"nearest_buoy": 0.0, "orientation": 0.9}},
                      {"name": "B", "nearest_buoy_id": None, "nearest_buoy_dist_km": None,
                       "fallback_buoy_ids": []}]


# --------------------------------------------------------------------------- #
# Run 74's report, and the committed roster                                    #
# --------------------------------------------------------------------------- #

def test_run_74_gives_45_spots():
    rows = A.plan(A.load_report())
    # README: 15 gains; silent 16 changes + 5 losses; beyond the cap 8 changes + 1 loss.
    assert collections.Counter(r for _, r, *_ in rows) == {"gain": 15, "silent": 21, "past cap": 9}


def _roster():
    return {s["name"]: s for s in json.loads(ROSTER.read_text(encoding="utf-8"))}


@pytest.mark.parametrize("name, buoy, km, fallbacks", [
    # the five Hawaii spots on 51205 / 51208, cleared
    ("Kanaha Beach", None, None, []),
    ("Hoʻokipa", None, None, []),
    ("Peahi Jaws", None, None, []),
    ("Hanalei Bay", None, None, []),
    ("Tunnels Beach", None, None, []),
    # silent buoys replaced
    ("Andrew Molera State Park", "46028", 56.88, []),
    ("Davenport Landing", "46042", 31.0, ["46236", "46028"]),
    ("Sunset Beach NC", "41108", 48.0, ["41013"]),
    ("Pawleys Island", "41004", 102.45, ["41108", "41013"]),
    ("Port Aransas Jetty", "42033", 4.4, ["42034", "42092", "42032"]),
    # past the cap
    ("Coolidge", "42098", 46.9, []),
    ("Key West", "42095", 24.87, []),
    ("Ten Mile Beach", "46213", 117.85, []),
    ("Siesta Key", None, None, []),
    # gains
    ("Fiji", "41053", 1.11, []),
    ("Bathtub Beach", "41009", 147.03, []),
    ("Nantucket Surf", "44008", 109.92, []),
])
def test_the_roster_carries_run_74s_choice(name, buoy, km, fallbacks):
    s = _roster()[name]
    assert (s["nearest_buoy_id"], s["nearest_buoy_dist_km"], s["fallback_buoy_ids"]) == \
        (buoy, km, fallbacks)


@pytest.mark.parametrize("name, buoy", [
    ("Richardson Beach", "51206"),     # blocked by land in run 74: held for review
    ("Makaha", "51212"),               # the same
    ("Agate Beach", "46244"),          # blocked: held
    ("Asbury Park", "44091"),          # a nearer one passes: held
    ("Avila Beach", "46218"),          # a nearer one passes: held
])
def test_a_held_spot_keeps_its_buoy(name, buoy):
    assert _roster()[name]["nearest_buoy_id"] == buoy


def test_db_import_keeps_every_new_pairing():
    # db_import NULLs a pairing whose stored distance is more than 5 km off the great circle to
    # the buoy's snapshot position; none of the 45 may be dropped that way.
    names = {n for n, *_ in A.plan(A.load_report())}
    recs = [{k: s.get(k) for k in ("name", "lat", "lng", "nearest_buoy_id",
                                   "nearest_buoy_dist_km", "fallback_buoy_ids")}
            for n, s in _roster().items() if n in names]
    assert len(recs) == 45
    assert _validate_coord_derived(recs) == 0


@pytest.mark.parametrize("name, conf", [
    ("Kanaha Beach", 0.0),      # cleared
    ("Fiji", 1.0),              # 1.11 km, cap 250
    ("Bathtub Beach", 0.75),    # 147.03 km, cap 150
])
def test_the_roster_confidence(name, conf):
    assert _roster()[name]["enrichment_confidence"]["nearest_buoy"] == conf
