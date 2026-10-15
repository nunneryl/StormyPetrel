"""The rating applies a tide preference only from a source it trusts.

DECIDED ON sw1-raycast run 15 (2026-10-15). Every one of the 444 preferences held was
'unattributed' — 365 of them 'mid' — and tide_mult only ever lowers a rating. Treated as
neutral, they raised 6,574 of 83,359 replayed spot-hours (7.9%) at 426 spots, by exactly half
a star, and lowered none. So interpret.rate_spot now reads its preference through
interpret.rated_tide_preference: the spot's own when tide_preference_source is reviewed,
researched or scraped, otherwise None, which tide_multiplier scores as the neutral 1.0.

Every expected value is worked by hand:
  the tide series has min 1.0 ft and max 5.0 ft, and one point at 11:00 local — 18:00Z at
  lng -123.8 — so the hour's tide_norm is (4.6 - 1.0) / (5.0 - 1.0) = 0.9.
  low  at 0.9: not < 0.3, not < 0.7          -> 0.6
  mid  at 0.9: outside [0.3, 0.7]            -> 0.7
  high at 0.9: > 0.7                         -> 1.0
  all, or no preference                      -> 1.0
"""
from __future__ import annotations

import json
import os
from datetime import datetime

import pytest

from pipeline import config, descriptions, interpret

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

SERIES = {"min": 1.0, "max": 5.0, "source": "hourly",
          "points": [(datetime(2026, 9, 9, 11, 0), 4.6)]}
FORECAST = [{"valid_time": "2026-09-09T18:00:00Z", "hs": 1.5, "tp": 12.0, "dp": 280.0,
             "wind_speed": 2.0, "wind_dir": 90.0, "swell_hs": 1.5,
             "swell_tp": 12.0, "swell_dp": 280.0}]
BASE = {"name": "Caspar", "lat": 39.3611, "lng": -123.8178, "orientation_deg": 270.0,
        "offshore_wind_deg": 90.0, "optimal_swell_dir": 270.0}

# (source, the tide_mult a 'low' preference gets at tide_norm 0.9)
BY_SOURCE = [
    ("reviewed", 0.6),
    ("researched", 0.6),
    ("scraped", 0.6),
    ("model_recall", 1.0),
    ("derived_from_break_type", 1.0),
    ("unattributed", 1.0),
    ("unknown", 1.0),
    (None, 1.0),
]


def _row(**spot):
    return interpret.rate_spot(dict(BASE, **spot), FORECAST, SERIES)[0]


@pytest.mark.parametrize("source, expected", BY_SOURCE)
def test_the_rating_applies_the_preference_only_from_a_trusted_source(source, expected):
    row = _row(tide_preference="low", tide_preference_source=source)
    assert row["tide_norm"] == 0.9
    assert row["tide_mult"] == expected


def test_a_preference_with_no_source_key_at_all_is_neutral():
    """The roster before this change carried no source key: absent is untrusted, not trusted."""
    assert _row(tide_preference="low")["tide_mult"] == 1.0


@pytest.mark.parametrize("pref, expected", [("low", 0.6), ("mid", 0.7), ("high", 1.0),
                                            ("all", 1.0), ("unknown", 1.0)])
def test_a_trusted_preference_still_scores_as_before(pref, expected):
    assert _row(tide_preference=pref, tide_preference_source="researched")["tide_mult"] == expected


@pytest.mark.parametrize("source, expected", BY_SOURCE)
def test_rated_tide_preference(source, expected):
    want = "low" if expected == 0.6 else None
    assert interpret.rated_tide_preference(
        {"tide_preference": "low", "tide_preference_source": source}) == want


def test_the_untrusted_hour_rates_as_the_neutral_one_and_the_trusted_hour_does_not():
    """Same hour, same spot: an unattributed 'low' publishes exactly what 'all' does."""
    unverified = _row(tide_preference="low", tide_preference_source="unattributed")
    neutral = _row(tide_preference="all", tide_preference_source="researched")
    trusted = _row(tide_preference="low", tide_preference_source="researched")
    assert unverified == neutral
    assert (trusted["tide_mult"], neutral["tide_mult"]) == (0.6, 1.0)


def test_the_rating_trusts_what_a_description_may_state():
    assert config.TIDE_RATED_SOURCES == ("reviewed", "researched", "scraped")
    assert config.TIDE_RATED_SOURCES == descriptions.STATED_SOURCES


def test_on_the_committed_roster_no_spot_gets_a_tide_penalty():
    """Run 15's neutral case is the published rating: the roster's 446 preferences (444 rated
    spots, 2 invalid) are all 'unattributed', so the rater applies none of them and every
    hour's tide_mult is 1.0 — the rule run 15 previewed as 'neutral'."""
    with open(os.path.join(ROOT, "pipeline", "spots_enriched.json"), encoding="utf-8") as f:
        spots = json.load(f)
    held = [s for s in spots if s.get("tide_preference") is not None]
    assert len(held) == 446
    assert {s.get("tide_preference_source") for s in held} == {"unattributed"}
    assert [s["name"] for s in spots if interpret.rated_tide_preference(s) is not None] == []


def test_the_methodology_post_states_the_rule_and_the_rosters_counts():
    """The post's Tide section names the trusted sources and the counts it quotes; the counts
    are the roster's rated spots (444 with a preference, 365 of them 'mid' — 018's header)."""
    with open(os.path.join(ROOT, "frontend", "content", "blog", "methodology.md"),
              encoding="utf-8") as f:
        post = f.read()
    tide = post[post.index("## Tide\n"):post.index("## Surface conditions")]
    assert "**A preferred tide only counts once it's verified.**" in tide
    assert "a person has reviewed it" in tide and "a source we researched" in tide
    assert "the spot's own surf-forecast.com page" in tide
    assert "every preferred tide we held, 444 of them" in tide
    assert "365 of the 444 said \"mid\"" in tide
    with open(os.path.join(ROOT, "pipeline", "spots_enriched.json"), encoding="utf-8") as f:
        rated = [s for s in json.load(f) if s.get("is_valid_surf_spot") is not False]
    held = [s["tide_preference"] for s in rated if s.get("tide_preference") is not None]
    assert (len(held), held.count("mid")) == (444, 365)
