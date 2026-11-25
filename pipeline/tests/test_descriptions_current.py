"""The CI check: every roster spot's cached description is current with what the roster states.

A description's v2 signature hashes what it states: since template t4 its break type, bottom and
tide (from a stated source), what research says of the wave, its best months, buoy, tide station,
cam and nearby spots. db_import blanks a description whose signature no longer matches the roster,
so a PR that changes any of those (a break type, a buoy, a spot's position, which moves its
neighbours' lists too) must regenerate the affected descriptions in the same PR, with

    python -m pipeline.generate_descriptions --no-db

and this test fails until it does. Expected slugs are written by hand.
"""
from __future__ import annotations

import copy
import json
from pathlib import Path

from pipeline import generate_descriptions as G

ROOT = Path(__file__).resolve().parents[2]
ROSTER = json.loads((ROOT / "pipeline" / "spots_enriched.json").read_text(encoding="utf-8"))
CACHE = json.loads((ROOT / "pipeline" / "data" / "spot_descriptions.json").read_text(encoding="utf-8"))


def test_every_committed_description_is_current_with_the_committed_roster():
    stale = G.stale(ROSTER, CACHE)
    assert stale == [], (
        f"{len(stale)} description(s) no longer match what the roster states, and db_import will "
        f"blank them on the live site: {stale}. Regenerate them in this PR with "
        f"`python -m pipeline.generate_descriptions --no-db` and commit "
        f"pipeline/data/spot_descriptions.json.")


def _with(name, **changes):
    roster = copy.deepcopy(ROSTER)
    spot = next(s for s in roster if s.get("name") == name)
    spot.update(changes)
    return roster


def test_a_changed_break_type_is_caught():
    # the research import corrects a break type the description states
    spot = next(s for s in ROSTER if s.get("name") == "Hillsboro Inlet")
    new = "reef" if spot.get("break_type") != "reef" else "beach"
    roster = _with("Hillsboro Inlet", break_type=new, break_type_source="researched")
    assert G.stale(roster, CACHE) == [("hillsboro-inlet", "something it states has changed")]


def test_a_moved_spot_changes_its_neighbours_descriptions_too():
    # Topanga Beach moved 30 km out to sea: it is no longer anyone's neighbour, and its own
    # neighbours change, so every description that named it, and its own, is stale.
    spot = next(s for s in ROSTER if s.get("name") == "Topanga Beach")
    roster = _with("Topanga Beach", lat=spot["lat"] - 0.3)
    stale = dict(G.stale(roster, CACHE))
    assert stale.get("topanga-beach") == "something it states has changed"
    assert len(stale) > 1, stale


def test_a_change_the_description_does_not_state_is_not_caught():
    # Since t4 the facing, the optimal swell and the swell window are the Optimal conditions card's,
    # not the description's: changing them regenerates nothing.
    roster = _with("La Push First Beach", swell_window_arcs=[{"min": 200, "max": 280, "span": 84}],
                   orientation_deg=134.0, optimal_swell_dir=230)
    assert G.stale(roster, CACHE) == []


def test_a_spot_with_no_description_is_caught():
    cache = {k: v for k, v in CACHE.items() if k != "la-push-first-beach"}
    assert G.stale(ROSTER, cache) == [("la-push-first-beach", "no description yet")]


def test_an_entry_waiting_for_supabase_counts_as_current():
    cache = copy.deepcopy(CACHE)
    cache["hillsboro-inlet"]["pushed"] = False
    assert G.stale(ROSTER, cache) == []
