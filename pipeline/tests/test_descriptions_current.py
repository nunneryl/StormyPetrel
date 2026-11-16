"""The CI check: every roster spot's cached description is current with what the roster states.

A description's v2 signature hashes what it states, including the facing, the offshore bearing and
the optimal swell direction. db_import blanks a description whose signature no longer matches the
roster, so a PR that changes any of those (as #328 changed three facings, and #263 the optimal
directions of 94 spots) must regenerate the affected descriptions in the same PR, with

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


def test_a_changed_facing_is_caught():
    # #328's own case: Hillsboro Inlet back at 134 (offshore 314) no longer matches its description.
    roster = _with("Hillsboro Inlet", orientation_deg=134.0, offshore_wind_deg=314.0)
    assert G.stale(roster, CACHE) == [("hillsboro-inlet", "something it states has changed")]


def test_a_changed_optimal_swell_direction_is_caught():
    # #263's case: the optimal moves (Topanga Beach 204 -> 230).
    roster = _with("Topanga Beach", optimal_swell_dir=230)
    assert G.stale(roster, CACHE) == [("topanga-beach", "something it states has changed")]


def test_a_change_the_description_does_not_state_is_not_caught():
    # The swell window's arcs are not in the payload: no description to regenerate.
    roster = _with("La Push First Beach",
                   swell_window_arcs=[{"min": 200, "max": 280, "span": 84}])
    assert G.stale(roster, CACHE) == []


def test_a_spot_with_no_description_is_caught():
    cache = {k: v for k, v in CACHE.items() if k != "la-push-first-beach"}
    assert G.stale(ROSTER, cache) == [("la-push-first-beach", "no description yet")]


def test_an_entry_waiting_for_supabase_counts_as_current():
    cache = copy.deepcopy(CACHE)
    cache["hillsboro-inlet"]["pushed"] = False
    assert G.stale(ROSTER, cache) == []
