"""Break-type and bottom research: what kind of break each spot is and what its waves break
over, each from a page that says so.

Step 2 of the break-type plan, piloted on the 20 spots in PILOT_SPOTS. It is separate
from verify_spots.py, whose prompt shows the model our current break_type and tells it
not to search for one.

WHAT THE MODEL IS SHOWN. Only the spot's name, its state or territory and its coordinates
(spot_identity). It is never shown our break_type, its source, the verification notes or
anything else in the roster, so it cannot hand our value back to us.

TWO THINGS, RESEARCHED SEPARATELY, each standing on its own cited URL and quote:
  break_type  the shape of the break, one of migration 020's values (BREAK_TYPE_VALUES)
  bottom      what the waves break over (BOTTOM_VALUES). The results file only: the spots
              table has no column for it yet.
A page that gives one and not the other gives that one only. Our one-word break_type mixed
the two ("reef" is a bottom, "point" a shape); the next use, a size ceiling for breaks that
close out, depends mainly on the bottom.

SAND BOTTOM. sand_bottom is 'yes', 'no' or 'unknown', derived from the bottom (SAND_BOTTOM):
shifting sand closes out and a fixed bottom holds. A 'mixed' bottom gives 'unknown', because
the page names sand and something fixed without saying which the wave breaks on.

TWO ANSWERS PER SPOT, from the same model (MODEL):
  researched    With web search and web fetch. Each value counts only with a URL that a
                search or fetch really returned for that spot, and a quote that is really on
                that page. Anything less is 'unknown'.
  model_recall  The same questions with no tools, from memory. Kept for comparison only.

LOCATION CHECK. A page about a same-named spot somewhere else is no evidence. The model
reports where each page it quotes puts the spot; check_location compares that with our
state and coordinates, and a page that puts it somewhere else makes the value 'unknown'.
Hawaii's Suicide's and Bombora were once researched as California spots.

ONE MORE TRY AFTER A FAILED FETCH. When a fetch failed and a value is still unknown at the
end of the turn, the conversation goes on once (follow_up_message): the model is told which
fetch failed and why, and may make one more search or one more fetch before answering again.

COST. Each request's usage is priced at list price (PRICES) and added up per spot. A fetched
page is cut at FETCH_MAX_CONTENT_TOKENS, and the model is told not to fetch PDFs, which that
cut does not cover. A spot starts only if the money left covers twice the dearest spot so
far, and at least SPOT_RESERVE_FLOOR_USD before any spot has been priced. No request starts
once the budget is spent. The budget ($4 by default) holds across re-runs: the results file
carries what has been spent.

WRITES ONE FILE: the results file (DEFAULT_OUTPUT), after every spot. Never the roster and
never the database. Nothing here imports db_import or a database client, and the results
path may not be the roster.

RUN on the Mac, from the repo root, with ANTHROPIC_API_KEY set:
    python3 -m pipeline.research_break_type --limit 1    # one spot, to check the setup
    python3 -m pipeline.research_break_type              # the rest of the pilot
    python3 -m pipeline.research_break_type --report     # the tables again, no API calls
    python3 -m pipeline.research_break_type --dry-run    # what the model is sent, no calls
The pilot's report ends with the gate for the full run (gate_verdict): PASS or FAIL.

THE FULL RUN (--full), only once the gate passes, after the memory-only run (--memory-only):
researches every rated spot that the memory answers do not settle by geography, into its own
results file, $70 cap, resumable. Its report lists what changed against memory and against
the current label, and the final sand-bottom flag for every spot.
"""
from __future__ import annotations

import argparse
import copy
import datetime
import hashlib
import json
import logging
import os
import re
import statistics
import sys
import time
import unicodedata
from pathlib import Path

from .config import (
    BREAK_TYPE_URL_RE,
    BREAK_TYPE_VALUES,
    DEFAULT_ENRICHED_OUTPUT,
    PIPELINE_DIR,
    break_type_quote,
)
from .geo import haversine_m, normalize_state

log = logging.getLogger("pipeline.research_break_type")

# The current Sonnet.
MODEL = "claude-sonnet-5-5"
EFFORTS = ("low", "medium", "high")
DEFAULT_EFFORT = "medium"

# List prices for MODEL in US dollars: per million tokens, and per search. $2 input, $10
# output, $2.50 for five-minute cache writes, $4 for one-hour cache writes and $0.20 for
# cache reads. Web search is $10 per 1,000 searches. Web fetch costs nothing beyond the
# tokens of the page it brings in.
PRICES = {
    "input_per_mtok": 2.00,
    "output_per_mtok": 10.00,
    "cache_write_5m_per_mtok": 2.50,
    "cache_write_1h_per_mtok": 4.00,
    "cache_read_per_mtok": 0.20,
    "per_web_search": 0.01,
    "per_web_fetch": 0.00,
}

# How much of a fetched page enters the model's context, in tokens. The first pilot used
# 6,000 and its answers held up; the revision's 3,000 cut surf-guide pages before their
# description, and five spots it had got right came back unknown. Accuracy over cost: back
# to 6,000. The API's limit is approximate and does not apply to PDFs (see the prompt).
FETCH_MAX_CONTENT_TOKENS = 6000
# The fetch tool's own estimate: an average 10 kB web page is about 2,500 tokens.
CHARS_PER_TOKEN = 4

# Both tools are called directly, not through dynamic filtering, so every search result
# and every fetched page comes back in the response. That is what lets the code check
# that the cited URL was really returned and that the quote is really on the page.
WEB_SEARCH_TOOL = {
    "type": "web_search_20260209",
    "name": "web_search",
    "max_uses": 3,
    "allowed_callers": ["direct"],
}
WEB_FETCH_TOOL = {
    "type": "web_fetch_20260209",
    "name": "web_fetch",
    "max_uses": 2,
    "max_content_tokens": FETCH_MAX_CONTENT_TOKENS,
    "citations": {"enabled": True},
    "allowed_callers": ["direct"],
}
RESEARCH_MAX_TOKENS = 8000
RECALL_MAX_TOKENS = 4000
# A paused server-side search turn is resumed at most this many times.
MAX_PAUSE_CONTINUATIONS = 2
# On top of the SDK's own retries: a 429 waits (retry-after, else this long) and retries.
RATE_LIMIT_RETRIES = 3
RATE_LIMIT_WAIT_SECONDS = 60.0
# Between spots, as verify_spots paces its batches, to stay under input-token rate limits.
SPOT_PAUSE_SECONDS = 15.0

PILOT_BUDGET_USD = 4.00
SPOT_RESERVE_FLOOR_USD = 0.50
# Rated spots in the database: the 2026-10-06 pipeline run upserted 646.
FULL_ROSTER_SPOTS = 646
# A page whose coordinates for the spot are further than this from ours describes
# somewhere else. The same scale as config.COORD_FIX_MAX_MOVE_KM.
LOCATION_MAX_KM = 25.0
# A shorter quote ("reef break") could be found on almost any page, so it proves nothing.
MIN_QUOTE_WORDS = 3

# Earlier runs keep their own files: the first pilot (schema 1) break_type_research_pilot.json,
# the revision (schema 2) break_type_research_pilot2.json. The gate run writes this one.
DEFAULT_OUTPUT = PIPELINE_DIR / "data" / "break_type_research_gate.json"
SCHEMA_VERSION = 3

# What the waves break over. Kept in the results file only: the spots table has no column.
BOTTOM_VALUES = ("sand", "rock", "coral", "cobble", "mixed", "unknown")
# The field a close-out size ceiling would read: shifting sand closes out, a fixed bottom
# holds. 'mixed' is 'unknown' because the page does not say which the wave breaks on.
SAND_BOTTOM = {"sand": "yes", "rock": "no", "coral": "no", "cobble": "no",
               "mixed": "unknown", "unknown": "unknown"}
FIELD_VALUES = {"break_type": BREAK_TYPE_VALUES, "bottom": BOTTOM_VALUES}
FIELDS = tuple(FIELD_VALUES)

# (roster name, region_hint, as the request named it, the known answer for a control).
# The known answer is used only in the report. The model is never shown it.
PILOT_SPOTS = (
    ("Banzai Pipeline", "Hawaii", "Banzai Pipeline", None),
    ("Waimea Bay", "Hawaii", "Waimea Bay", None),
    ("Peahi Jaws", "Hawaii", "Jaws", None),
    ("Honolua Bay", "Hawaii", "Honolua Bay", None),
    ("Rincon Domes", "Puerto Rico", "Rincon Domes", None),
    ("Tres Palmas", "Puerto Rico", "Tres Palmas", None),
    ("Suicide's", "Hawaii", "Suicide's (HI)", None),
    ("Bombora", "Hawaii", "Bombora (HI)", None),
    ("3 Mile", "California", "3 Mile", None),
    ("Refugio State Beach", "California", "Refugio State Beach", None),
    ("Tourmaline", "California", "Tourmaline", None),
    ("Venice Beach Breakwater", "California", "Venice Beach Breakwater", None),
    ("Lower Trestles", "California", "Lower Trestles", None),
    ("Jacksonville Beach Pier", "Florida", "Jacksonville Beach Pier", None),
    ("Lake Worth Pier", "Florida", "Lake Worth Pier", None),
    ("Manasquan Inlet", "New Jersey", "Manasquan Inlet", None),
    ("Mavericks, California", "California", "Mavericks", "reef"),
    ("Steamer Lane", "California", "Steamer Lane", "point"),
    ("Malibu Surfrider Beach", "California", "Malibu Surfrider Beach", "point"),
    ("Zuma Beach", "California", "Zuma Beach (plain sand beach)", "beach"),
)

_KINDS = (
    "  beach       waves break over a sand bottom: a beach break or sandbars, including\n"
    "              the sandbars beside a pier\n"
    "  reef        waves break over reef, coral, lava or rock that is not a point\n"
    "  point       waves wrap around and peel along a point or headland\n"
    "  jetty       waves are shaped by a jetty, groin, breakwater or inlet structure\n"
    "  rivermouth  waves break on the bar at a river or creek mouth\n"
)

# What a close-out ceiling needs is sand against a fixed bottom, so a reef that is not said to
# be coral counts as rock rather than as unknown.
_BOTTOMS = (
    "  sand    sand or sandbars\n"
    "  rock    rock, boulders, rock ledges or lava, and any reef not said to be coral\n"
    "  coral   coral\n"
    "  cobble  cobblestones or rounded stones\n"
    "  mixed   more than one of these, such as sand over rock\n"
)

RESEARCH_SYSTEM = (
    "You find out two things about one surf spot, each from a web page that says so, and\n"
    "report them in a fixed format:\n"
    "  break_type  what kind of break it is\n"
    "  bottom      what the waves break over\n"
    "\n"
    "The spot is given by its name, its US state or territory and its coordinates. Surf\n"
    "spots often share a name with spots in other states or countries, and some names are\n"
    "ordinary words. A page counts only if it describes the spot at these coordinates in\n"
    "this state. When the pages you find describe a same-named spot somewhere else, do\n"
    "not use them, and list those other places in same_name_elsewhere.\n"
    "\n"
    "How to work:\n"
    "1. Search for the spot, for example its name, its state and the word surf. Surf\n"
    "   guides such as surf-forecast.com and wannasurf.com usually say what kind of break\n"
    "   a spot is and what its bottom is.\n"
    "2. Fetch the page you will quote with web_fetch, so that its exact words can be\n"
    "   checked. web_fetch opens only a URL that a search result or an earlier fetch\n"
    "   returned, so do not type one in yourself. Do not fetch PDFs.\n"
    "3. For each of the two, find the words on the page that say it, and copy them\n"
    "   exactly: one continuous passage, no ellipses, at most 300 characters. The two may\n"
    "   come from the same page, and from the same passage.\n"
    "4. Copy each quote from the text the fetch returned, not from a search result, and\n"
    "   cite the URL of that fetched page. Your quote is checked against that text, which\n"
    "   stops after about 6,000 tokens of the page.\n"
    "\n"
    "break_type is exactly one of:\n"
    + _KINDS +
    "  unknown     no page you read says what kind of break this spot is, or the pages you\n"
    "              found describe a same-named spot somewhere else\n"
    "If the page names more than one kind (\"a reef and beach break\"), set the value to the\n"
    "one it says dominates, or else the first one it names, and list every kind it names,\n"
    "in its order, in \"all\".\n"
    "\n"
    "bottom is exactly one of:\n"
    + _BOTTOMS +
    "  unknown no page you read says what the bottom is\n"
    "List every material the page names, in its order, in \"all\".\n"
    "\n"
    "Answer each of the two from what a page you read says, and each on its own: a page\n"
    "that says the kind of break but not the bottom gives you the kind of break only.\n"
    "Without such a page the value is \"unknown\". Never quote words that are not on the\n"
    "page, and never cite a page you did not read.\n"
    "\n"
    "End your reply with this JSON object, and nothing after it:\n"
    "{\n"
    "  \"break_type\": {\n"
    "    \"value\": \"beach\" | \"reef\" | \"point\" | \"jetty\" | \"rivermouth\" | \"unknown\",\n"
    "    \"all\": [every kind the page names, in its order],\n"
    "    \"source_url\": \"the URL of the page you quote\" or null,\n"
    "    \"quote\": \"the exact words on that page that say what kind of break it is\" or null\n"
    "  },\n"
    "  \"bottom\": {\n"
    "    \"value\": \"sand\" | \"rock\" | \"coral\" | \"cobble\" | \"mixed\" | \"unknown\",\n"
    "    \"all\": [every material the page names, in its order],\n"
    "    \"source_url\": \"the URL of the page you quote\" or null,\n"
    "    \"quote\": \"the exact words on that page that say what the bottom is\" or null\n"
    "  },\n"
    "  \"pages\": [\n"
    "    {\n"
    "      \"url\": \"a page you quote above, each page once\",\n"
    "      \"place\": \"where that page says the spot is, in its words\" or null,\n"
    "      \"state\": \"the US state or territory that page puts the spot in\" or null,\n"
    "      \"lat\": the latitude that page gives for the spot, or null,\n"
    "      \"lng\": the longitude that page gives for the spot, or null,\n"
    "      \"location_quote\": \"the exact words on that page that say where the spot is\" or null\n"
    "    }\n"
    "  ],\n"
    "  \"same_name_elsewhere\": [\"other places you found with a surf spot of this name\"],\n"
    "  \"note\": \"one short sentence a reviewer should know\" or null\n"
    "}\n"
)

RECALL_SYSTEM = (
    "You say, from your own knowledge and without searching, two things about one surf\n"
    "spot: what kind of break it is (break_type) and what the waves break over (bottom).\n"
    "\n"
    "The spot is given by its name, its US state or territory and its coordinates. Surf\n"
    "spots share names, so answer for the spot at these coordinates in this state.\n"
    "\n"
    "break_type is exactly one of:\n"
    + _KINDS +
    "  unknown     you do not know this particular spot\n"
    "If the spot is more than one kind, set the value to the dominant one and list every\n"
    "kind in \"all\".\n"
    "\n"
    "bottom is exactly one of:\n"
    + _BOTTOMS +
    "  unknown you do not know this spot's bottom\n"
    "List every material in \"all\".\n"
    "\n"
    "End your reply with this JSON object, and nothing after it:\n"
    "{\n"
    "  \"break_type\": {\n"
    "    \"value\": \"beach\" | \"reef\" | \"point\" | \"jetty\" | \"rivermouth\" | \"unknown\",\n"
    "    \"all\": [every kind it is],\n"
    "    \"confidence\": \"high\" | \"medium\" | \"low\"\n"
    "  },\n"
    "  \"bottom\": {\n"
    "    \"value\": \"sand\" | \"rock\" | \"coral\" | \"cobble\" | \"mixed\" | \"unknown\",\n"
    "    \"all\": [every material it is],\n"
    "    \"confidence\": \"high\" | \"medium\" | \"low\"\n"
    "  },\n"
    "  \"note\": \"one short sentence\" or null\n"
    "}\n"
)

# Why a fetch failed, in words the model is given when the conversation goes on. The codes
# are the web fetch tool's.
FETCH_ERRORS = {
    "url_not_in_prior_context": "web_fetch opens only a URL that a search result or an "
                                "earlier fetch returned, and this one came from neither",
    "url_not_accessible": "the site did not return the page",
    "unsupported_content_type": "it is not a text, HTML or PDF page",
    "too_many_requests": "the fetch was rate-limited",
    "url_not_allowed": "fetching that URL is not allowed",
    "url_too_long": "the URL is longer than 250 characters",
    "invalid_tool_input": "the URL was not valid",
    "max_uses_exceeded": "the fetches allowed for that turn were used up",
    "unavailable": "the fetch service failed",
}
_FIELD_WORDS = {"break_type": "the kind of break", "bottom": "the bottom"}


def follow_up_message(failed: list, needed: list) -> str:
    """The one message that continues a turn after a failed fetch left a value unknown.
    A URL written in a user message may be fetched, so a URL the model typed in itself
    can be tried again."""
    lines = ["This fetch did not work:" if len(failed) == 1 else "These fetches did not work:"]
    for fetch in failed:
        why = FETCH_ERRORS.get(fetch["error"], f"error {fetch['error']}")
        lines.append(f"- {fetch['url'] or '(no URL)'}: {why}")
    lines += ["",
              "Your answer for " + " and ".join(_FIELD_WORDS[field] for field in needed)
              + " is still unknown. You may make one more tool call: one web search or one "
              "web fetch, not both. A URL written in this message may be fetched. Then give "
              "your final answer again, both values, as the same JSON object at the end of "
              "your reply."]
    return "\n".join(lines)


# Words that name each value, for the reviewer's "does the quote name the value" flag.
VALUE_WORDS = {
    "break_type": {
        "beach": ("beach", "sand"),
        "reef": ("reef", "coral", "lava", "rock", "ledge", "slab", "boulder"),
        "point": ("point",),
        "jetty": ("jetty", "jetties", "groin", "groyne", "breakwater", "inlet"),
        "rivermouth": ("river", "estuary", "creek"),
    },
    "bottom": {
        "sand": ("sand",),
        "rock": ("rock", "boulder", "ledge", "lava", "reef", "slab"),
        "coral": ("coral",),
        "cobble": ("cobble", "pebble", "stone"),
    },
}

_FATAL_STATUS = frozenset({400, 401, 403, 404, 413})


class BudgetExhausted(Exception):
    """The budget has no room for the next request."""


class Budget:
    """The pilot's spending against its cap, which holds across re-runs.

    A spot starts only if the money left covers spot_reserve_usd(): twice the dearest
    spot so far, and at least SPOT_RESERVE_FLOOR_USD before any spot has been priced. A
    request starts only while the cap has not been reached. What is spent is what the
    API's usage reports, at PRICES."""

    def __init__(self, cap_usd: float, spent_usd: float = 0.0, dearest_spot_usd: float = 0.0,
                 floor_usd: float = SPOT_RESERVE_FLOOR_USD):
        self.cap_usd = float(cap_usd)
        self.spent_usd = float(spent_usd)
        self.dearest_spot_usd = float(dearest_spot_usd)
        self.floor_usd = float(floor_usd)

    def spot_reserve_usd(self) -> float:
        return max(self.floor_usd, 2.0 * self.dearest_spot_usd)

    def may_start_spot(self) -> bool:
        return self.spent_usd + self.spot_reserve_usd() <= self.cap_usd

    def require_room(self) -> None:
        if self.spent_usd >= self.cap_usd:
            raise BudgetExhausted(
                f"spent ${self.spent_usd:.4f} of the ${self.cap_usd:.2f} budget")

    def charge(self, usd: float) -> None:
        self.spent_usd += usd

    def spot_priced(self, usd: float) -> None:
        self.dearest_spot_usd = max(self.dearest_spot_usd, usd)


# --- what the model is sent ---------------------------------------------------------

def spot_identity(spot: dict) -> dict:
    """The only fields of a roster entry that the model is shown."""
    return {
        "name": spot["name"],
        "region": spot["region_hint"],
        "lat": round(float(spot["lat"]), 5),
        "lng": round(float(spot["lng"]), 5),
    }


def spot_prompt(identity: dict) -> str:
    return (
        f"Name: {identity['name']}\n"
        f"State or territory: {identity['region']}, United States\n"
        f"Coordinates: {identity['lat']:.5f}, {identity['lng']:.5f} (latitude, longitude)"
    )


def research_request(identity: dict, effort: str) -> dict:
    """The researched answer's request: web search and web fetch, our value nowhere."""
    return {
        "model": MODEL,
        "max_tokens": RESEARCH_MAX_TOKENS,
        "system": [{"type": "text", "text": RESEARCH_SYSTEM,
                    "cache_control": {"type": "ephemeral"}}],
        "tools": [copy.deepcopy(WEB_SEARCH_TOOL), copy.deepcopy(WEB_FETCH_TOOL)],
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": spot_prompt(identity)}],
    }


def recall_request(identity: dict, effort: str) -> dict:
    """The memory answer's request: the same model and question, no tools."""
    return {
        "model": MODEL,
        "max_tokens": RECALL_MAX_TOKENS,
        "system": RECALL_SYSTEM,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": spot_prompt(identity)}],
    }


def _follow_up_sample() -> str:
    """The follow-up message with every error code in it: its wording, for the settings."""
    return follow_up_message([{"url": "https://example.com/", "error": code}
                              for code in sorted(FETCH_ERRORS)], list(FIELDS))


def run_settings(effort: str) -> dict:
    """Everything that shapes an answer. A results file is resumed only under the same."""
    prompts = "\x00".join((RESEARCH_SYSTEM, RECALL_SYSTEM, _follow_up_sample())).encode("utf-8")
    return {
        "model": MODEL,
        "thinking": "adaptive",
        "effort": effort,
        "tools": [copy.deepcopy(WEB_SEARCH_TOOL), copy.deepcopy(WEB_FETCH_TOOL)],
        "research_max_tokens": RESEARCH_MAX_TOKENS,
        "recall_max_tokens": RECALL_MAX_TOKENS,
        "prompts_sha256": hashlib.sha256(prompts).hexdigest()[:16],
    }


# --- what it cost -------------------------------------------------------------------

def usage_of(message: dict) -> dict:
    """One response's usage: tokens by price class, and the searches and fetches run."""
    usage = message.get("usage") or {}
    server = usage.get("server_tool_use") or {}
    created = int(usage.get("cache_creation_input_tokens") or 0)
    breakdown = usage.get("cache_creation") or {}
    if breakdown:
        write_5m = int(breakdown.get("ephemeral_5m_input_tokens") or 0)
        write_1h = int(breakdown.get("ephemeral_1h_input_tokens") or 0)
    else:
        # No breakdown: the default cache lifetime, five minutes, is the only one used here.
        write_5m, write_1h = created, 0
    return {
        "requests": 1,
        "input_tokens": int(usage.get("input_tokens") or 0),
        "output_tokens": int(usage.get("output_tokens") or 0),
        "cache_write_5m_tokens": write_5m,
        "cache_write_1h_tokens": write_1h,
        "cache_read_tokens": int(usage.get("cache_read_input_tokens") or 0),
        "web_search_requests": int(server.get("web_search_requests") or 0),
        "web_fetch_requests": int(server.get("web_fetch_requests") or 0),
    }


def zero_usage() -> dict:
    return {key: 0 for key in usage_of({})}


def add_usage(total: dict, more: dict) -> dict:
    return {key: total[key] + more[key] for key in total}


def cost_usd(usage: dict) -> float:
    """List price of *usage*, in US dollars."""
    tokens = (usage["input_tokens"] * PRICES["input_per_mtok"]
              + usage["output_tokens"] * PRICES["output_per_mtok"]
              + usage["cache_write_5m_tokens"] * PRICES["cache_write_5m_per_mtok"]
              + usage["cache_write_1h_tokens"] * PRICES["cache_write_1h_per_mtok"]
              + usage["cache_read_tokens"] * PRICES["cache_read_per_mtok"]) / 1_000_000
    return (tokens
            + usage["web_search_requests"] * PRICES["per_web_search"]
            + usage["web_fetch_requests"] * PRICES["per_web_fetch"])


def input_tokens_all(usage: dict) -> int:
    return (usage["input_tokens"] + usage["cache_write_5m_tokens"]
            + usage["cache_write_1h_tokens"] + usage["cache_read_tokens"])


# --- reading the answers ------------------------------------------------------------

_PUNCT = str.maketrans({"\u2018": "'", "\u2019": "'", "\u02bc": "'", "\u201c": '"',
                        "\u201d": '"', "\u2013": "-", "\u2014": "-", "\u00a0": " ",
                        "\u2026": "..."})
# A markdown link's URL part, which may hold one level of parentheses, as Wikipedia's do:
# (https://en.wikipedia.org/wiki/North_Shore_(Oahu)).
_URL_PART = r"\((?:[^()\s]|\([^()\s]*\))*\)"
_MD_LINK = re.compile(r"\[([^\]]*)\]" + _URL_PART)
# Footnote markers, which a quote may leave out: [1], [[1]](...#cite_note-1), and
# [citation needed].
_FOOTNOTE = re.compile(r"\[\[?\d+\]\]?(?:" + _URL_PART + r")?|\[\W*citation needed\W*\]", re.I)
# What a quote is compared on: its words, and any ellipsis, in order. Punctuation and
# markup between words are not compared, because a fetched page is markdown and a quote
# copies it unevenly: a link with or without its URL ("[Hawaii]" for
# "[Hawaii](https://...)"), bold markers kept or dropped, a comma moved by either.
_WORD = re.compile(r"[^\W_]+|\.\.\.")


# Apostrophes, and the Hawaiian okina, are dropped before comparing: a page's "Oʻahu",
# "Peʻahi" or "Suicides" is the quote's "O'ahu", "Pe'ahi" or "Suicide's".
_APOSTROPHES = re.compile("['`\u02bb]")


def _fold(text: str) -> str:
    """*text* for comparing a quote with a page: case, accents, apostrophes and the okina,
    quote marks, dashes, markdown and whitespace evened out."""
    folded = unicodedata.normalize("NFKD", text)
    folded = "".join(char for char in folded if not unicodedata.combining(char))
    folded = unicodedata.normalize("NFKC", folded).translate(_PUNCT)
    folded = _APOSTROPHES.sub("", folded)
    folded = _FOOTNOTE.sub(" ", folded)
    folded = _MD_LINK.sub(r"\1", folded)
    folded = re.sub(r"[*_`#>|]", " ", folded)
    return " ".join(folded.split()).lower()


def _clean_quote(text: str) -> str:
    """A quote folded, without the quote marks or ellipses around it."""
    quote = _fold(text).strip(" \"'")
    while quote.startswith("..."):
        quote = quote[3:].lstrip(" \"'")
    while quote.endswith("..."):
        quote = quote[:-3].rstrip(" \"'")
    return quote


def _words(text: str) -> list:
    """*text* folded, as the words (and ellipses) a quote is compared on."""
    return _WORD.findall(_fold(text))


def _holds(page_words: list, words: list) -> bool:
    """Whether *words* are on the page, together and in order."""
    return (" " + " ".join(words) + " ") in (" " + " ".join(page_words) + " ")


def where_it_differs(words: list, page_words: list) -> str:
    """Where the quote leaves the page: the longest run of its opening words on the page,
    and the word after it on each side."""
    best, at = 0, None
    for start in range(len(page_words)):
        run = 0
        while (run < len(words) and start + run < len(page_words)
               and page_words[start + run] == words[run]):
            run += 1
        if run > best:
            best, at = run, start
    if best == 0:
        return f"its first word, {words[0]!r}, is not on the page"
    following = at + best
    page_has = (f"the page has {page_words[following]!r}" if following < len(page_words)
                else "the page ends")
    opening = "its first word is" if best == 1 else f"its first {best} words are"
    return f"{opening} on the page, then the quote has {words[best]!r} where {page_has}"


def _url_key(url: str) -> str:
    """A URL for matching: no scheme, fragment, 'www.' or trailing slash; host lowercased."""
    url = url.strip().split("#", 1)[0]
    rest = url.partition("://")[2] or url
    host, _, path = rest.partition("/")
    host = host.lower()
    if host.startswith("www."):
        host = host[4:]
    return host + "/" + path.rstrip("/")


def _ascii_words(text: str) -> list:
    """Lowercase ASCII words; okina and apostrophes dropped, so Hawai'i reads hawaii."""
    text = re.sub("['`\u2018\u2019\u02bb\u02bc]", "", text)
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return re.findall(r"[a-z]+", text.lower())


def states_named_in(text: str) -> set:
    """The US states and territories named in full in *text*. Two-letter codes are not
    read: in prose they are ordinary words ("in", "or", "me")."""
    words = _ascii_words(text or "")
    found = set()
    i = 0
    while i < len(words):
        for size in (3, 2, 1):
            phrase = " ".join(words[i:i + size])
            if len(words[i:i + size]) == size and len(phrase) > 2:
                state = normalize_state(phrase)
                if state:
                    found.add(state)
                    i += size
                    break
        else:
            i += 1
    return found


def last_json_object(text: str):
    """The last JSON object in *text* that has a break_type at its top level, or None."""
    decoder = json.JSONDecoder()
    found = None
    for i, char in enumerate(text):
        if char != "{":
            continue
        try:
            obj, _ = decoder.raw_decode(text, i)
        except ValueError:
            continue
        if isinstance(obj, dict) and "break_type" in obj:
            found = obj
    return found


def collect_evidence(blocks: list) -> dict:
    """What the tools really returned in one research conversation, read from its blocks."""
    queries, fetches, search_urls, errors, texts = [], [], [], [], []
    pages = {}       # _url_key -> page text ('' when the page was a PDF)
    fetched = []     # {"url", "kind", "chars"} for each page a fetch returned
    failed = []      # {"url", "error"} for each fetch that returned an error
    citations = []   # {"url", "cited_text"} from web search citations
    asked = {}       # web_fetch call id -> the URL it asked for
    for block in blocks:
        kind = block.get("type")
        if kind == "server_tool_use":
            tool_input = block.get("input") or {}
            if block.get("name") == "web_search":
                queries.append(str(tool_input.get("query", "")))
            elif block.get("name") == "web_fetch":
                fetches.append(str(tool_input.get("url", "")))
                asked[block.get("id")] = str(tool_input.get("url", ""))
        elif kind == "web_search_tool_result":
            content = block.get("content")
            if isinstance(content, list):
                search_urls.extend(item["url"] for item in content
                                   if isinstance(item, dict) and item.get("url"))
            elif isinstance(content, dict):
                errors.append("web_search: " + str(content.get("error_code")))
        elif kind == "web_fetch_tool_result":
            content = block.get("content") or {}
            if content.get("type") == "web_fetch_result" and content.get("url"):
                source = (content.get("content") or {}).get("source") or {}
                text = (source.get("data") if source.get("type") == "text" else "") or ""
                # Under the URL it landed on and, after a redirect, the one it asked for.
                pages[_url_key(content["url"])] = text
                if asked.get(block.get("tool_use_id")):
                    pages[_url_key(asked[block["tool_use_id"]])] = text
                if source.get("type") == "text":
                    fetched.append({"url": content["url"], "kind": "text", "chars": len(text)})
                else:
                    media = str(source.get("media_type") or source.get("type") or "unknown")
                    fetched.append({"url": content["url"],
                                    "kind": "pdf" if media == "application/pdf" else media,
                                    "chars": None})
            else:
                errors.append("web_fetch: " + str(content.get("error_code")))
                failed.append({"url": asked.get(block.get("tool_use_id")),
                               "error": str(content.get("error_code"))})
        elif kind == "text":
            texts.append(block.get("text") or "")
            for citation in block.get("citations") or []:
                if citation.get("url"):
                    citations.append({"url": citation["url"],
                                      "cited_text": citation.get("cited_text") or ""})
    return {"queries": queries, "fetches": fetches, "search_urls": search_urls,
            "pages": pages, "fetched": fetched, "failed": failed, "citations": citations,
            "errors": errors, "text": "".join(texts)}


# A fetched page this long was probably cut at FETCH_MAX_CONTENT_TOKENS (the limit is
# approximate, so nine tenths of it).
CUT_PAGE_CHARS = int(FETCH_MAX_CONTENT_TOKENS * CHARS_PER_TOKEN * 0.9)


def find_quote(quote, url: str, evidence: dict) -> tuple:
    """Where *quote* is: ('fetched page' | 'search citation', None, the page's URL), or
    (None, why not, None). A quote that is not on the cited page but is on another page a
    fetch returned stands on that page, under its URL."""
    if not isinstance(quote, str) or not quote.strip():
        return None, "no quote", None
    words = _words(_clean_quote(quote))
    if sum(1 for word in words if word != "...") < MIN_QUOTE_WORDS:
        return None, f"the quote is under {MIN_QUOTE_WORDS} words, too short to check", None
    key = _url_key(url)
    page = evidence["pages"].get(key)
    if page and _holds(_words(page), words):
        return "fetched page", None, url
    for citation in evidence["citations"]:
        if _url_key(citation["url"]) == key and _holds(
                _words(_clean_quote(citation["cited_text"])), words):
            return "search citation", None, url
    for fetched in evidence["fetched"]:
        text = evidence["pages"].get(_url_key(fetched["url"])) or ""
        if _url_key(fetched["url"]) != key and text and _holds(_words(text), words):
            return "fetched page", None, fetched["url"]
    why = "the quote is not on the cited page"
    if page is None:
        why += ", which was never fetched, and no search citation from it holds the quote"
    elif not page:
        why += ", a PDF, whose text cannot be checked"
    else:
        cut = (f"probably cut at the {FETCH_MAX_CONTENT_TOKENS:,}-token limit"
               if len(page) >= CUT_PAGE_CHARS else
               f"short of the {FETCH_MAX_CONTENT_TOKENS:,}-token limit, so not cut")
        why += (f" ({len(page):,} characters fetched, {cut}): "
                + where_it_differs(words, _words(page)))
    return None, why, None


def quote_names_value(quote: str, field: str, value: str, spot_name: str) -> bool:
    """Whether the quote, outside the spot's own name, has a word for *value*. A mixed
    bottom needs words for two materials."""
    unnamed = re.sub(r"\b" + re.escape(_fold(spot_name)) + r"\b", " ", _clean_quote(quote))
    words = _ascii_words(unnamed)
    stems = VALUE_WORDS[field]

    def named(kind):
        return any(word.startswith(stem) for word in words for stem in stems[kind])
    if field == "bottom" and value == "mixed":
        return sum(1 for kind in stems if named(kind)) >= 2
    return named(value)


def _number_in(value, low: float, high: float) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and low <= value <= high)


def check_location(identity: dict, page: dict) -> dict:
    """Does the page describe OUR spot? Its state, the states its own location words name,
    and its coordinates when it gives them, against ours. *page* is the answer's entry
    for that page: state, place, lat, lng and location_quote.

    'wrong place'  it puts the spot in another state, or its coordinates are more than
                   LOCATION_MAX_KM from ours
    'ok'           it puts the spot in our state, or its coordinates are near ours
    'unverified'   it says neither"""
    ours = normalize_state(identity["region"])
    stated = page.get("state")
    theirs = None
    if isinstance(stated, str):
        # As written ("Hawaii (state)", "HI"), else with okina and accents folded away.
        theirs = normalize_state(stated) or normalize_state(" ".join(_ascii_words(stated)))
    reasons = []
    wrong = False
    if theirs is not None and theirs != ours:
        wrong = True
        reasons.append(f"the page puts it in {theirs}")
    named = states_named_in(" ".join(str(page.get(key) or "")
                                     for key in ("location_quote", "place")))
    others = sorted(named - {ours})
    if others and ours not in named:
        wrong = True
        reasons.append("the page's location words name " + ", ".join(others))
    distance_km = None
    lat, lng = page.get("lat"), page.get("lng")
    if _number_in(lat, -90, 90) and _number_in(lng, -180, 180):
        distance_km = round(haversine_m(identity["lat"], identity["lng"],
                                        float(lat), float(lng)) / 1000.0, 1)
        if distance_km > LOCATION_MAX_KM:
            wrong = True
            reasons.append(f"the page's coordinates are {distance_km:.0f} km from ours")
    if wrong:
        verdict = "wrong place"
    elif theirs == ours or ours in named or distance_km is not None:
        verdict = "ok"
    else:
        verdict = "unverified"
        reasons.append("the page does not say which state" if not stated
                       else f"cannot read the state {stated!r}")
    return {"verdict": verdict, "our_state": ours, "source_state": theirs or stated,
            "source_place": page.get("place"),
            "location_quote": page.get("location_quote"),
            "distance_km": distance_km, "reasons": reasons}


def _page_for(answer: dict, url: str) -> dict:
    """The answer's location entry for the page at *url*, or {} when it gave none."""
    pages = answer.get("pages")
    for page in pages if isinstance(pages, list) else []:
        if (isinstance(page, dict) and isinstance(page.get("url"), str)
                and _url_key(page["url"]) == _url_key(url)):
            return page
    return {}


def _claim(answer: dict, field: str):
    """The answer's object for *field*; a bare value is read as an object without evidence."""
    claim = answer.get(field)
    if isinstance(claim, str):
        return {"value": claim}
    return claim if isinstance(claim, dict) else None


def _listed(claim: dict, value: str, field: str) -> list:
    """Every kind or material the claim lists, each once, in its order; the value first if
    the list left it out. 'mixed' and 'unknown' name no kind or material, so they are
    never listed."""
    listed = claim.get("all")
    listed = listed if isinstance(listed, list) else []
    head = [] if value in listed or value in ("mixed", "unknown") else [value]
    kinds = []
    for kind in head + listed:
        if kind in FIELD_VALUES[field] and kind not in ("mixed", "unknown") \
                and kind not in kinds:
            kinds.append(kind)
    return kinds


def _note(answer: dict):
    note = answer.get("note")
    return " ".join(str(note).split())[:300] if note else None


def _unknown(reason) -> dict:
    return {"status": "unknown", "value": "unknown", "values": [], "mixed": False,
            "source_url": None, "cited_url": None, "quote": None, "quote_found_in": None,
            "quote_names_value": None, "location": None, "reason": reason}


def judge_claim(answer: dict, field: str, evidence: dict, identity: dict) -> dict:
    """One value of the researched answer, kept only if a returned page backs it."""
    values = FIELD_VALUES[field]
    claim = _claim(answer, field)
    if claim is None:
        return _unknown(f"the answer has no {field}")
    value = claim.get("value")
    if value not in values:
        return _unknown(f"it answered {value!r}, which is not one of the {len(values)} values")
    if value == "unknown":
        return _unknown("no page it read says" + (": " + _note(answer) if _note(answer) else ""))
    out = _unknown(None)
    url = claim.get("source_url")
    if not (isinstance(url, str) and BREAK_TYPE_URL_RE.match(url.strip())):
        out["reason"] = "no source URL"
        return out
    url = url.strip()
    returned = {_url_key(u) for u in evidence["search_urls"]} | set(evidence["pages"])
    if _url_key(url) not in returned:
        out["source_url"] = url
        out["reason"] = "the cited URL was not returned by any search or fetch for this spot"
        return out
    found_in, problem, found_url = find_quote(claim.get("quote"), url, evidence)
    if found_in is None:
        out["source_url"] = url
        out["reason"] = problem
        return out
    out.update(source_url=found_url, cited_url=None if found_url == url else url,
               quote=break_type_quote(claim["quote"]), quote_found_in=found_in,
               quote_names_value=quote_names_value(claim["quote"], field, value,
                                                   identity["name"]),
               location=check_location(identity, _page_for(answer, found_url)))
    if out["location"]["verdict"] == "wrong place":
        out["reason"] = "the page describes another place: " + "; ".join(
            out["location"]["reasons"])
        return out
    kinds = _listed(claim, value, field)
    out.update(status="researched", value=value, values=kinds,
               mixed=value == "mixed" or len(kinds) > 1)
    return out


def judge_research(responses: list, identity: dict) -> dict:
    """The researched answer: each value kept only if a returned page backs it, and the
    sand-bottom flag derived from the bottom."""
    evidence = collect_evidence([block for response in responses
                                 for block in (response.get("content") or [])])
    stops = [response.get("stop_reason") for response in responses]
    out = {"sand_bottom": "unknown", "same_name_elsewhere": [], "reason": None,
           "model_answer": None, "follow_up": None,
           "queries": evidence["queries"], "fetches": evidence["fetches"],
           "fetched_pages": evidence["fetched"], "failed_fetches": evidence["failed"],
           "search_result_urls": sorted(set(evidence["search_urls"])),
           "tool_errors": evidence["errors"], "stop_reasons": stops}
    answer = None
    if stops and stops[-1] == "refusal":
        out["reason"] = "the model declined to answer (stop_reason refusal)"
    else:
        answer = last_json_object(evidence["text"])
        if answer is None:
            out["reason"] = "no JSON answer" + (" (it ran out of output tokens)"
                                                if stops and stops[-1] == "max_tokens" else "")
    if answer is None:
        out.update({field: _unknown(out["reason"]) for field in FIELDS})
        return out
    out["model_answer"] = answer
    elsewhere = answer.get("same_name_elsewhere")
    if isinstance(elsewhere, list):
        out["same_name_elsewhere"] = [" ".join(str(p).split()) for p in elsewhere if p][:10]
    out.update({field: judge_claim(answer, field, evidence, identity) for field in FIELDS})
    out["sand_bottom"] = SAND_BOTTOM[out["bottom"]["value"]]
    return out


def judge_recall(response: dict) -> dict:
    """The memory answer, as the model gave it."""
    text = "".join(block.get("text") or "" for block in (response.get("content") or [])
                   if block.get("type") == "text")
    stop = response.get("stop_reason")
    out = {"source": "model_recall", "sand_bottom": "unknown", "note": None, "reason": None,
           "stop_reason": stop}
    out.update({field: {"value": "unknown", "values": [], "mixed": False, "confidence": None,
                        "reason": None} for field in FIELDS})
    if stop == "refusal":
        out["reason"] = "the model declined to answer (stop_reason refusal)"
        return out
    answer = last_json_object(text)
    if answer is None:
        out["reason"] = "no JSON answer"
        return out
    for field in FIELDS:
        claim, part = _claim(answer, field), out[field]
        value = (claim or {}).get("value")
        if claim is None:
            part["reason"] = f"the answer has no {field}"
        elif value not in FIELD_VALUES[field]:
            part["reason"] = (f"it answered {value!r}, which is not one of the "
                              f"{len(FIELD_VALUES[field])} values")
        else:
            kinds = _listed(claim, value, field) if value != "unknown" else []
            confidence = claim.get("confidence")
            part.update(value=value, values=kinds, mixed=value == "mixed" or len(kinds) > 1,
                        confidence=confidence if confidence in ("high", "medium", "low")
                        else None)
    out["sand_bottom"] = SAND_BOTTOM[out["bottom"]["value"]]
    out["note"] = _note(answer)
    return out


def agreement(researched: str, recalled: str) -> str:
    """'yes' or 'no' when both answers give a value, else 'n/a'."""
    if researched == "unknown" or recalled == "unknown":
        return "n/a"
    return "yes" if researched == recalled else "no"


def agreements(research: dict, recall: dict) -> dict:
    """Whether the researched and memory answers agree: on each value, and on the
    sand-bottom flag a close-out ceiling would read."""
    out = {field: agreement(research[field]["value"], recall[field]["value"])
           for field in FIELDS}
    out["sand_bottom"] = agreement(research["sand_bottom"], recall["sand_bottom"])
    return out


# --- calling the API ----------------------------------------------------------------

def _as_dict(message) -> dict:
    if isinstance(message, dict):
        return message
    for name in ("to_dict", "model_dump", "dict"):
        method = getattr(message, name, None)
        if callable(method):
            return method()
    raise TypeError(f"cannot read a {type(message).__name__} response")


def _replay_content(message):
    """A paused turn's content, to send back unchanged."""
    return message["content"] if isinstance(message, dict) else message.content


def _retry_after(exc):
    response = getattr(exc, "response", None)
    try:
        value = response.headers.get("retry-after") if response is not None else None
        return min(300.0, float(value)) if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def _create(client, request: dict, sleep):
    """One Messages API call. A 429 that outlasts the SDK's own retries waits and retries."""
    for attempt in range(RATE_LIMIT_RETRIES + 1):
        try:
            return client.messages.create(**request)
        except Exception as exc:
            if getattr(exc, "status_code", None) != 429 or attempt == RATE_LIMIT_RETRIES:
                raise
            wait = _retry_after(exc) or RATE_LIMIT_WAIT_SECONDS
            log.warning("rate-limited (attempt %d of %d); waiting %.0fs",
                        attempt + 1, RATE_LIMIT_RETRIES, wait)
            sleep(wait)
    raise RuntimeError("unreachable")


def _describe(exc: BaseException) -> str:
    """The error in full: its type, its HTTP status, and the API's whole message."""
    status = getattr(exc, "status_code", None)
    text = " ".join(str(exc).split())
    return f"{type(exc).__name__}" + (f" {status}" if status else "") + f": {text}"


def _is_fatal(exc: BaseException) -> bool:
    """An error every later spot would hit too: a bad request, a key or a model problem."""
    return getattr(exc, "status_code", None) in _FATAL_STATUS


def _priced(usage: dict) -> dict:
    return dict(usage, cost_usd=round(cost_usd(usage), 6))


# --- the run ------------------------------------------------------------------------

def spot_key(entry: tuple) -> str:
    return f"{entry[0]}|{entry[1]}"


def resolve_pilot(roster: list) -> list:
    """[(PILOT_SPOTS entry, roster spot)], each matched on name and region exactly once."""
    by_name = {}
    for spot in roster:
        by_name.setdefault((spot.get("name"), spot.get("region_hint")), []).append(spot)
    resolved = []
    for entry in PILOT_SPOTS:
        matches = by_name.get((entry[0], entry[1]), [])
        if len(matches) != 1:
            raise SystemExit(f"pilot spot {entry[0]!r} ({entry[1]}) matches "
                             f"{len(matches)} roster entries, not 1")
        resolved.append((entry, matches[0]))
    return resolved


def new_record(entry: tuple, spot: dict) -> dict:
    return {
        "name": entry[0], "region": entry[1], "asked_as": entry[2],
        "control_answer": entry[3], "lat": spot["lat"], "lng": spot["lng"],
        # Read for the report. Never sent: the requests are built from spot_identity.
        # The roster has no bottom, so there is no current bottom to show.
        "current": {"break_type": spot.get("break_type"),
                    "break_type_source": spot.get("break_type_source")},
        "research": None, "model_recall": None,
        "research_usage": None, "recall_usage": None,
        "cost_usd": 0.0, "agree": None, "done": False, "error": None,
    }


def _run_turn(client, request: dict, messages: list, budget: Budget, sleep,
              spent: dict) -> tuple:
    """One assistant turn: the request and its resumed pauses. Returns (its responses, the
    messages its last request was sent with, its last message as the client returned it).
    Each response is charged as it arrives, to *budget* and to spent['usage'], so what an
    interrupted turn cost is still counted."""
    responses = []
    for _ in range(MAX_PAUSE_CONTINUATIONS + 1):
        budget.require_room()
        message = _create(client, dict(request, messages=messages), sleep)
        data = _as_dict(message)
        responses.append(data)
        used = usage_of(data)
        spent["usage"] = add_usage(spent["usage"], used)
        budget.charge(cost_usd(used))
        if data.get("stop_reason") != "pause_turn":
            break
        messages = messages + [{"role": "assistant", "content": _replay_content(message)}]
    return responses, messages, message


def follow_up_needed(research: dict):
    """{'failed', 'needed'} when the conversation should go on once, else None: a fetch
    failed, the turn ended normally, and a value is still unknown."""
    needed = [field for field in FIELDS if research[field]["status"] != "researched"]
    stops = research["stop_reasons"]
    if not (research["failed_fetches"] and needed and stops and stops[-1] == "end_turn"):
        return None
    return {"failed": research["failed_fetches"], "needed": needed}


def merge_follow_up(first: dict, combined: dict, follow: dict, more: list) -> dict:
    """The answer after the follow-up. It only fills values the first turn left unknown: a
    value the first turn researched stays as it was, and each other value is the one the
    whole conversation's evidence backs."""
    merged = dict(combined)
    for field in FIELDS:
        if first[field]["status"] == "researched":
            merged[field] = first[field]
    merged["sand_bottom"] = SAND_BOTTOM[merged["bottom"]["value"]]
    calls = collect_evidence([block for response in more
                              for block in (response.get("content") or [])])
    merged["follow_up"] = {
        "after": follow["failed"], "needed": follow["needed"],
        "searches": len(calls["queries"]), "fetches": len(calls["fetches"]),
        "filled": [field for field in follow["needed"]
                   if merged[field]["status"] == "researched"],
    }
    return merged


def research_spot(client, entry: tuple, spot: dict, effort: str, budget: Budget,
                  sleep, recall: bool = True) -> tuple:
    """(record, the exception that stopped it or None). Both answers for one spot, or the
    researched answer only when *recall* is False (the full run, which has the memory
    answers from the memory-only run)."""
    identity = spot_identity(spot)
    record = new_record(entry, spot)
    spent = {"usage": zero_usage()}
    try:
        request = research_request(identity, effort)
        try:
            responses, messages, last = _run_turn(client, request, list(request["messages"]),
                                                  budget, sleep, spent)
            record["research"] = judge_research(responses, identity)
            follow = follow_up_needed(record["research"])
            if follow is not None:
                messages = messages + [
                    {"role": "assistant", "content": _replay_content(last)},
                    {"role": "user",
                     "content": follow_up_message(follow["failed"], follow["needed"])}]
                more, _, _ = _run_turn(client, request, messages, budget, sleep, spent)
                record["research"] = merge_follow_up(
                    record["research"], judge_research(responses + more, identity), follow,
                    more)
        finally:
            record["research_usage"] = _priced(spent["usage"])

        if not recall:
            record["done"] = True
            return record, None
        budget.require_room()
        data = _as_dict(_create(client, recall_request(identity, effort), sleep))
        used = usage_of(data)
        budget.charge(cost_usd(used))
        record["recall_usage"] = _priced(used)
        record["model_recall"] = judge_recall(data)
        record["agree"] = agreements(record["research"], record["model_recall"])
        record["done"] = True
        return record, None
    except Exception as exc:
        record["error"] = _describe(exc)
        return record, exc
    finally:
        record["cost_usd"] = round(sum(part["cost_usd"] for part in (
            record["research_usage"], record["recall_usage"]) if part), 6)
        record["finished_at"] = _now()


def run(client, resolved: list, results: dict, budget: Budget, effort: str, *,
        limit=None, pause_seconds: float = SPOT_PAUSE_SECONDS, sleep=time.sleep,
        save=None, memory=None):
    """Research the spots not yet done, in order. Returns why it stopped early: None,
    'budget', or 'error: ...'. With *memory* (the full run: spot key -> the memory-only
    run's answer, or None) no memory request is made; the record carries that answer."""
    started = 0
    for position, (entry, spot) in enumerate(resolved, 1):
        key = spot_key(entry)
        earlier = results["spots"].get(key)
        if earlier and earlier.get("done"):
            continue
        if limit is not None and started >= limit:
            return None
        if not budget.may_start_spot():
            log.info("budget: $%.4f spent of $%.2f; the next spot needs room for $%.2f, "
                     "so the run stops here", budget.spent_usd, budget.cap_usd,
                     budget.spot_reserve_usd())
            return "budget"
        if started and pause_seconds:
            sleep(pause_seconds)
        record, exc = research_spot(client, entry, spot, effort, budget, sleep,
                                    recall=memory is None)
        if memory is not None:
            record["memory"] = memory.get(key)
            record["current"]["verified"] = spot.get("verification_confidence") is not None
        if earlier:
            record["earlier_attempts_usd"] = round(
                earlier.get("cost_usd", 0.0) + earlier.get("earlier_attempts_usd", 0.0), 6)
        results["spots"][key] = record
        results["spent_usd"] = round(budget.spent_usd, 6)
        budget.spot_priced(record["cost_usd"])
        if save is not None:
            save(results)
        started += 1
        log.info("[%2d/%d] %s", position, len(resolved), _progress_line(record, budget))
        if isinstance(exc, BudgetExhausted):
            return "budget"
        if exc is not None and _is_fatal(exc):
            return "error: " + record["error"]
    return None


def _progress_line(record: dict, budget: Budget) -> str:
    research = record.get("research")
    recall = record.get("model_recall") or record.get("memory")
    usage = record.get("research_usage") or zero_usage()
    parts = [f"{record['asked_as']}:"]
    parts.append(f"researched {research['break_type']['value']} on {research['bottom']['value']}"
                 f" (sand bottom {research['sand_bottom']})" if research else "no research")
    parts.append(f"memory {recall['break_type']['value']} on {recall['bottom']['value']}"
                 if recall else "no memory answer")
    if research and research.get("follow_up"):
        parts.append("followed up a failed fetch")
    parts.append(f"{usage['web_search_requests']} searches, "
                 f"{usage['web_fetch_requests']} fetches")
    parts.append(f"${record['cost_usd']:.4f}")
    parts.append(f"spent ${budget.spent_usd:.4f} of ${budget.cap_usd:.2f}")
    if record.get("error"):
        parts.append("ERROR " + record["error"])
    return " · ".join(parts)


# --- the results file ---------------------------------------------------------------

def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def new_results(effort: str, budget_usd: float) -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "what": ("Break-type and bottom research pilot. A results file only: nothing in it "
                 "has been applied to the roster or the database, and the spots table has no "
                 "column for the bottom."),
        "settings": run_settings(effort),
        "prices_usd": dict(PRICES),
        "budget_usd": budget_usd,
        "spent_usd": 0.0,
        "runs": [],
        "spots": {},
    }


def load_results(path: Path, effort: str, budget_usd: float, fresh: bool) -> dict:
    """The results so far, to resume, or a new file. Refuses to mix settings."""
    if fresh or not path.exists():
        return new_results(effort, budget_usd)
    results = json.loads(path.read_text(encoding="utf-8"))
    if results.get("schema") != SCHEMA_VERSION or results.get("settings") != run_settings(effort):
        raise SystemExit(f"{path} was made with different settings (model, effort, tools "
                         "or prompts). Pass --fresh to start over, or --output for a new file.")
    results["budget_usd"] = budget_usd
    return results


def save_results(path: Path, results: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    temporary.write_text(json.dumps(results, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    os.replace(temporary, path)


def guard_output_path(output: Path, roster: Path) -> None:
    """The results file may not be the roster, and must be a .json file."""
    target = output.resolve()
    if target in (roster.resolve(), DEFAULT_ENRICHED_OUTPUT.resolve()) \
            or target.name == DEFAULT_ENRICHED_OUTPUT.name:
        raise SystemExit(f"refusing to write research results to {output}: that is the roster")
    if target.suffix != ".json":
        raise SystemExit(f"the results file must be a .json file, not {output}")


# --- the report ---------------------------------------------------------------------

def _cell(text) -> str:
    return " ".join(str(text).replace("|", "/").split())


def _row(*cells) -> str:
    return "| " + " | ".join(_cell(c) for c in cells) + " |"


def _value_cell(part: dict) -> str:
    """A value with the kinds or materials it lists: 'reef (mixed: reef and beach)',
    'mixed: sand and rock'."""
    if part["value"] == "mixed":
        return "mixed: " + " and ".join(part["values"]) if part["values"] else "mixed"
    if part["mixed"]:
        return part["value"] + " (mixed: " + " and ".join(part["values"]) + ")"
    return part["value"]


def _researched_cell(research, field: str) -> str:
    if research is None:
        return "not run"
    claim = research[field]
    if claim["status"] != "researched":
        return "unknown: " + (claim.get("reason") or "")
    cell = _value_cell(claim)
    if claim["quote_names_value"] is False:
        cell += " · the quote does not name it"
    return cell


def _url_cell(research, field: str) -> str:
    return ((research or {}).get(field) or {}).get("source_url") or "—"


def _location_cell(research, field: str, elsewhere: bool) -> str:
    if research is None:
        return "—"
    location = research[field].get("location")
    if location is None:
        cell = "—"
    elif location["verdict"] == "ok":
        cell = f"ok ({location['source_state'] or location['our_state']}"
        if location["distance_km"] is not None:
            cell += f", {location['distance_km']:.0f} km from ours"
        cell += ")"
    else:
        cell = location["verdict"] + ": " + "; ".join(location["reasons"])
    if elsewhere and research.get("same_name_elsewhere"):
        cell += " · name also used: " + ", ".join(research["same_name_elsewhere"])
    return cell


def _memory_cell(recall, field: str) -> str:
    if recall is None:
        return "not run"
    part = recall[field]
    cell = _value_cell(part)
    if part["confidence"]:
        cell += f", {part['confidence']} confidence"
    reason = part["reason"] or recall["reason"]
    if reason:
        cell += f" ({reason})"
    return cell


def _fetched_cell(research) -> str:
    """The pages the fetches brought in, and roughly how many tokens of text."""
    pages = (research or {}).get("fetched_pages") or []
    if not pages:
        return "none"
    chars = sum(page["chars"] for page in pages if page["kind"] == "text")
    cell = (f"{len(pages)} page" + ("s" if len(pages) != 1 else "")
            + f", about {chars // CHARS_PER_TOKEN:,} tokens")
    others = [page["kind"] for page in pages if page["kind"] != "text"]
    if others:
        cell += " + " + ", ".join(others)
    return cell


def _follow_up_cell(research) -> str:
    follow = (research or {}).get("follow_up")
    if not follow:
        return "—"
    errors = ", ".join(sorted({fetch["error"] for fetch in follow["after"]}))
    cell = (f"after {errors}: {follow['searches']} search"
            + ("es" if follow["searches"] != 1 else "")
            + f", {follow['fetches']} fetch" + ("es" if follow["fetches"] != 1 else ""))
    cell += ("; filled " + " and ".join(follow["filled"]) if follow["filled"]
             else "; filled nothing")
    if follow["searches"] + follow["fetches"] > 1:
        cell += " · more than the one call allowed"
    return cell


def per_spot_costs(results: dict) -> list:
    return [record["cost_usd"] for record in results["spots"].values() if record.get("done")]


def estimate(costs: list) -> dict:
    """Per-spot cost figures from the pilot, and the full roster at the mean and the max."""
    mean = statistics.mean(costs)
    return {"spots": len(costs), "mean": mean, "median": statistics.median(costs),
            "min": min(costs), "max": max(costs),
            "full_at_mean": mean * FULL_ROSTER_SPOTS,
            "full_at_max": max(costs) * FULL_ROSTER_SPOTS}


def _tally(flags: list) -> str:
    return ", ".join(f"{flags.count(flag)} {flag}" for flag in ("yes", "no", "unknown"))


# The gate for the full run, as it was set: the controls come out right; the famous breaks
# are not unknown; at most GATE_MAX_UNKNOWN of the 20 are unknown for break type. Every rule
# reads the researched break type, and a spot not run or not finished counts as unknown.
GATE_CONTROLS = (
    (("Mavericks, California", "California"), ("reef",)),
    (("Malibu Surfrider Beach", "California"), ("point",)),
    (("Zuma Beach", "California"), ("beach",)),
    (("Steamer Lane", "California"), ("point", "reef")),
)
GATE_NOT_UNKNOWN = (("Banzai Pipeline", "Hawaii"), ("Peahi Jaws", "Hawaii"),
                    ("Waimea Bay", "Hawaii"), ("Tres Palmas", "Puerto Rico"))
GATE_MAX_UNKNOWN = 4


def _gate_break_type(results: dict, name_region: tuple):
    """The researched break type of a pilot spot, or None if it is unknown, not run or
    not finished."""
    record = results["spots"].get(f"{name_region[0]}|{name_region[1]}")
    if not record or not record.get("done"):
        return None
    claim = record["research"]["break_type"]
    return claim["value"] if claim["status"] == "researched" else None


def gate_verdict(results: dict) -> tuple:
    """(passed, lines): each rule with PASS or FAIL, then the gate's own."""
    lines, passed = [], True

    def rule(ok: bool, text: str):
        nonlocal passed
        passed = passed and ok
        lines.append(("PASS" if ok else "FAIL") + "  " + text)

    for (name, region), allowed in GATE_CONTROLS:
        got = _gate_break_type(results, (name, region))
        rule(got in allowed, f"control {name}: {' or '.join(allowed)}; researched "
                             f"{got or 'unknown'}")
    for name, region in GATE_NOT_UNKNOWN:
        got = _gate_break_type(results, (name, region))
        rule(got is not None, f"{name} is not unknown; researched {got or 'unknown'}")
    unfinished = [entry[0] for entry in PILOT_SPOTS
                  if not (results["spots"].get(spot_key(entry)) or {}).get("done")]
    unknown = [entry[0] for entry in PILOT_SPOTS if entry[0] not in unfinished
               and _gate_break_type(results, entry[:2]) is None]
    count = len(unknown) + len(unfinished)
    text = (f"no more than {GATE_MAX_UNKNOWN} of the {len(PILOT_SPOTS)} unknown for break "
            f"type; {count} " + ("is" if count == 1 else "are"))
    if unknown:
        text += ": researched as unknown: " + ", ".join(unknown)
    if unfinished:
        text += ("; " if unknown else ": ") + "not run or not finished, so counted as " \
                "unknown: " + ", ".join(unfinished)
    rule(count <= GATE_MAX_UNKNOWN, text)
    lines.append("GATE: " + ("PASS" if passed else "FAIL"))
    return passed, lines


def render_report(results: dict) -> str:
    kinds = [_row("Spot", "Current value", "Researched break type", "Source URL",
                  "Location check", "Memory answer", "Agree"), "|---|---|---|---|---|---|---|"]
    bottoms = [_row("Spot", "Researched bottom", "Source URL", "Location check",
                    "Memory answer", "Agree", "Sand bottom: researched", "Sand bottom: memory",
                    "Sand bottom: agree"), "|---|---|---|---|---|---|---|---|---|"]
    costs = [_row("Spot", "Searches", "Fetches", "Fetched text", "Follow-up", "Input tokens",
                  "Output tokens", "Research", "Memory", "Total"),
             "|---|---|---|---|---|---|---|---|---|---|"]
    for entry in PILOT_SPOTS:
        record = results["spots"].get(spot_key(entry))
        label = entry[2] + (f" (control: {entry[3]})" if entry[3] else "")
        if record is None:
            kinds.append(_row(label, "—", "not run", "—", "—", "—", "—"))
            bottoms.append(_row(label, "not run", "—", "—", "—", "—", "—", "—", "—"))
            continue
        research, recall = record.get("research"), record.get("model_recall")
        agree = record.get("agree") or {}
        kinds.append(_row(label, record["current"]["break_type"] or "(none)",
                          _researched_cell(research, "break_type"),
                          _url_cell(research, "break_type"),
                          _location_cell(research, "break_type", elsewhere=True),
                          _memory_cell(recall, "break_type"), agree.get("break_type") or "—"))
        bottoms.append(_row(label, _researched_cell(research, "bottom"),
                            _url_cell(research, "bottom"),
                            _location_cell(research, "bottom", elsewhere=False),
                            _memory_cell(recall, "bottom"), agree.get("bottom") or "—",
                            (research or {}).get("sand_bottom") or "—",
                            (recall or {}).get("sand_bottom") or "—",
                            agree.get("sand_bottom") or "—"))
        r_use = record.get("research_usage") or _priced(zero_usage())
        m_use = record.get("recall_usage") or _priced(zero_usage())
        costs.append(_row(
            label, r_use["web_search_requests"], r_use["web_fetch_requests"],
            _fetched_cell(research), _follow_up_cell(research),
            f"{input_tokens_all(r_use) + input_tokens_all(m_use):,}",
            f"{r_use['output_tokens'] + m_use['output_tokens']:,}",
            f"${r_use['cost_usd']:.4f}", f"${m_use['cost_usd']:.4f}",
            f"${record['cost_usd']:.4f}" + ("" if record.get("done") else
                                           " (unfinished: " + (record.get("error") or "") + ")")))
    out = (["Break type:", ""] + kinds + ["", "Bottom (in this results file only):", ""]
           + bottoms + ["", "Cost:", ""] + costs + [""])
    out.append(f"Spent ${results['spent_usd']:.4f} of the ${results['budget_usd']:.2f} budget, "
               "at list prices.")
    done = per_spot_costs(results)
    if done:
        figures = estimate(done)
        out.append(f"Per spot, over {figures['spots']} finished: mean ${figures['mean']:.4f}, "
                   f"median ${figures['median']:.4f}, cheapest ${figures['min']:.4f}, "
                   f"dearest ${figures['max']:.4f}.")
        out.append(f"The full {FULL_ROSTER_SPOTS} spots at the pilot's mean: "
                   f"${figures['full_at_mean']:.2f}; at its dearest spot's cost: "
                   f"${figures['full_at_max']:.2f}.")
    finished = [record for record in results["spots"].values() if record.get("done")]
    if finished:
        out.append("Sand bottom, researched: "
                   + _tally([r["research"]["sand_bottom"] for r in finished])
                   + "; from memory: "
                   + _tally([r["model_recall"]["sand_bottom"] for r in finished]) + ".")
        both = [r["agree"]["sand_bottom"] for r in finished if r["agree"]["sand_bottom"] != "n/a"]
        out.append(f"Where both answers say yes or no, they agree on {both.count('yes')} "
                   f"of {len(both)}.")
        followed = [r for r in finished if r["research"].get("follow_up")]
        if followed:
            filled = sum(1 for r in followed if r["research"]["follow_up"]["filled"])
            out.append(f"Followed up a failed fetch at {len(followed)} of {len(finished)} "
                       f"spots; {filled} of them gained a value.")
    stops = [run_["stopped"] for run_ in results.get("runs", []) if run_.get("stopped")]
    if stops:
        out.append("Stopped early: " + stops[-1])
    out += ["", "The gate for the full run:", ""] + gate_verdict(results)[1]
    return "\n".join(out)


def render_dry_run(resolved: list, effort: str, recall: bool = True) -> str:
    """Exactly what the model is sent, without calling it. Without *recall* (the full run)
    there is no memory request to show."""
    sample = research_request(spot_identity(resolved[0][1]), effort)
    example = follow_up_message(
        [{"url": "https://www.example.com/breaks/a-spot", "error": "url_not_in_prior_context"}],
        ["bottom"])
    parts = [f"Model {MODEL}, effort {effort}. Every request is "
             + ("one of these two." if recall else "this one."), "",
             "RESEARCHED ANSWER: system prompt (the same for every spot)", "",
             RESEARCH_SYSTEM,
             "tools:", json.dumps(sample["tools"], indent=2), "",
             "IF A FETCH FAILED AND A VALUE IS STILL UNKNOWN at the end of that turn, the "
             "conversation goes on once, with a message like this one naming the real URLs "
             "and reasons:", "", example, ""]
    if recall:
        parts += ["MEMORY ANSWER: system prompt (the same for every spot; no tools)", "",
                  RECALL_SYSTEM,
                  "THE ONLY TEXT ABOUT EACH SPOT (the user message, the same in both "
                  "requests):"]
    else:
        parts.append("THE ONLY TEXT ABOUT EACH SPOT (the user message):")
    for _, spot in resolved:
        parts += ["", spot_prompt(spot_identity(spot))]
    return "\n".join(parts)


# --- memory-only mode: every rated spot, from the model's memory --------------------------
#
# A memory answer (no web search) for every rated spot first; how much research is worth
# doing is decided from it. The same model, and the same identity-only user message, as the
# pilot's memory answer: the model never sees our value. Writes its own results file.

MEMORY_BUDGET_USD = 6.00
# One small request per spot, so the reserve floor is far below the research pilot's: a spot
# starts while the money left covers twice the dearest spot so far, and at least this much.
MEMORY_RESERVE_FLOOR_USD = 0.05
MEMORY_PAUSE_SECONDS = 1.0
MEMORY_MAX_TOKENS = RECALL_MAX_TOKENS
DEFAULT_MEMORY_OUTPUT = PIPELINE_DIR / "data" / "break_type_memory_all.json"
MEMORY_SCHEMA_VERSION = 1

MEMORY_SYSTEM = (
    "You say, from your own knowledge and without searching, two things about one surf\n"
    "spot: what kind of break it is (break_type) and what the waves break over (bottom).\n"
    "\n"
    "The spot is given by its name, its US state or territory and its coordinates. Surf\n"
    "spots share names, so answer for the spot at these coordinates in this state, and say\n"
    "whether you know of a surf spot with the same name somewhere else.\n"
    "\n"
    "break_type is exactly one of:\n"
    + _KINDS +
    "  unknown     you do not know this particular spot\n"
    "If the spot is more than one kind, set the value to the dominant one and list every\n"
    "kind in \"all\".\n"
    "\n"
    "bottom is exactly one of:\n"
    + _BOTTOMS +
    "  unknown you do not know this spot's bottom\n"
    "List every material in \"all\".\n"
    "\n"
    "End your reply with this JSON object, and nothing after it:\n"
    "{\n"
    "  \"break_type\": {\n"
    "    \"value\": \"beach\" | \"reef\" | \"point\" | \"jetty\" | \"rivermouth\" | \"unknown\",\n"
    "    \"all\": [every kind it is],\n"
    "    \"confidence\": \"high\" | \"medium\" | \"low\"\n"
    "  },\n"
    "  \"bottom\": {\n"
    "    \"value\": \"sand\" | \"rock\" | \"coral\" | \"cobble\" | \"mixed\" | \"unknown\",\n"
    "    \"all\": [every material it is],\n"
    "    \"confidence\": \"high\" | \"medium\" | \"low\"\n"
    "  },\n"
    "  \"mixed_note\": \"if it is more than one kind of break or bottom, one short sentence on\n"
    "                 how they combine\" or null,\n"
    "  \"name_shared_elsewhere\": true | false,\n"
    "  \"other_places\": [\"other places you know with a surf spot of this name\"],\n"
    "  \"note\": \"one short sentence\" or null\n"
    "}\n"
)

# The settled-by-geography rule, for the report. Edit these lists to change it; the report
# prints them. Florida and New York are split by their coordinates (coast_region).
SAND_BARRIER_REGIONS = ("New Jersey", "Delaware", "Maryland", "Virginia", "North Carolina",
                        "South Carolina", "Georgia", "Texas", "Florida (Gulf)",
                        "Florida (Atlantic)", "New York (Long Island south shore)")
# Atlantic Florida's known reef spots in the roster, left out of the sand-barrier group.
FLORIDA_KNOWN_REEF = ("Monster Hole", "Bathtub Beach", "Ocean Reef Park", "Dania Beach Pier")
# Where rock or reef bottoms are common, so a memory answer settles nothing by geography.
ROCKY_REGIONS = ("California", "Oregon", "Washington", "Hawaii", "Puerto Rico", "Maine",
                 "New Hampshire", "Massachusetts", "Rhode Island", "Florida (Keys)",
                 "New York (Montauk)")
# Montauk: east of 72.05 W, which puts Hither Hills (72.026 W) in it.
MONTAUK_EAST_OF_LNG = -72.05
# Long Island's ocean shore runs from Breezy Point (40.54 N, 73.94 W) to East Hampton
# (40.95 N, 72.17 W). A New York spot counts as on it when it is west of Montauk, east of
# 74.05 W and south of the line through 40.55 N 74.0 W rising 0.23 degrees of latitude per
# degree of longitude eastward; the north shore is some 0.3 degrees north of that line.
LI_WEST_LNG = -74.05
LI_LINE_LAT, LI_LINE_LNG, LI_LINE_SLOPE = 40.55, -74.0, 0.23

REASON_CONFIDENCE = "low or medium confidence"
REASON_NOT_SAND = "not sand, or mixed"
REASON_ROCKY = "rocky or reef region"
REASON_NAME = "possible name collision"
REASON_VERIFIED = "disagrees with a verified label"
REASON_ELSEWHERE = "sand with high confidence, outside the sand-barrier regions"
REASONS = (REASON_CONFIDENCE, REASON_NOT_SAND, REASON_ROCKY, REASON_NAME, REASON_VERIFIED,
           REASON_ELSEWHERE)
HUMAN_LIST_SIZE = 30


def coast_region(spot: dict) -> str:
    """The spot's state, with Florida split by coast: the Keys south of 25.3 N; the Gulf
    west of 84 W (the panhandle) or west of 81.6 W south of 29 N (the west coast); the
    Atlantic everywhere else. New York is split into Montauk (east of 72.05 W), Long
    Island's south shore (see LI_LINE_*), and anywhere else, which stays 'New York'."""
    state = spot.get("region_hint") or spot.get("state")
    if state == "New York":
        lat, lng = float(spot["lat"]), float(spot["lng"])
        if lng > MONTAUK_EAST_OF_LNG:
            return "New York (Montauk)"
        if lng > LI_WEST_LNG and lat < LI_LINE_LAT + LI_LINE_SLOPE * (lng - LI_LINE_LNG):
            return "New York (Long Island south shore)"
        return state
    if state != "Florida":
        return state
    lat, lng = float(spot["lat"]), float(spot["lng"])
    if lat < 25.3:
        return "Florida (Keys)"
    if lng < -84.0 or (lng < -81.6 and lat < 29.0):
        return "Florida (Gulf)"
    return "Florida (Atlantic)"


def in_sand_barrier_region(spot: dict) -> bool:
    return coast_region(spot) in SAND_BARRIER_REGIONS and spot["name"] not in FLORIDA_KNOWN_REEF


def in_rocky_region(spot: dict) -> bool:
    region = coast_region(spot)
    return region in ROCKY_REGIONS or (region.startswith("Florida")
                                       and spot["name"] in FLORIDA_KNOWN_REEF)


def memory_request(identity: dict, effort: str) -> dict:
    """The memory-only request: no tools, our value nowhere."""
    return {
        "model": MODEL,
        "max_tokens": MEMORY_MAX_TOKENS,
        "system": MEMORY_SYSTEM,
        "thinking": {"type": "adaptive"},
        "output_config": {"effort": effort},
        "messages": [{"role": "user", "content": spot_prompt(identity)}],
    }


def memory_settings(effort: str) -> dict:
    return {"model": MODEL, "thinking": "adaptive", "effort": effort,
            "max_tokens": MEMORY_MAX_TOKENS,
            "prompt_sha256": hashlib.sha256(MEMORY_SYSTEM.encode("utf-8")).hexdigest()[:16]}


def judge_memory(response: dict) -> dict:
    """The memory answer as the model gave it: both values with their confidence, the sand
    flag, the mixed note, and whether it knows of a same-named spot elsewhere."""
    out = judge_recall(response)
    out.update(mixed_note=None, name_shared_elsewhere=None, other_places=[])
    if out["reason"]:
        return out
    text = "".join(block.get("text") or "" for block in (response.get("content") or [])
                   if block.get("type") == "text")
    answer = last_json_object(text) or {}
    note = answer.get("mixed_note")
    out["mixed_note"] = " ".join(str(note).split())[:300] if note else None
    shared = answer.get("name_shared_elsewhere")
    out["name_shared_elsewhere"] = shared if isinstance(shared, bool) else None
    places = answer.get("other_places")
    if isinstance(places, list):
        out["other_places"] = [" ".join(str(p).split()) for p in places if p][:10]
    return out


def shares_name(memory: dict) -> bool:
    return memory.get("name_shared_elsewhere") is True or bool(memory.get("other_places"))


def memory_key(spot: dict) -> str:
    return f"{spot['name']}|{spot['region_hint']}"


def memory_spots(roster: list) -> list:
    """Every rated spot in the roster, in its order, each key once."""
    spots = [spot for spot in roster if spot.get("is_valid_surf_spot") is not False]
    keys = [memory_key(spot) for spot in spots]
    if len(set(keys)) != len(keys):
        raise SystemExit("two rated spots share a name and state; the results cannot key them")
    return spots


def new_memory_record(spot: dict) -> dict:
    return {
        "name": spot["name"], "state": spot["region_hint"],
        "lat": spot["lat"], "lng": spot["lng"],
        # Read for the report. Never sent: the request is built from spot_identity.
        "current": {"break_type": spot.get("break_type"),
                    "verified": spot.get("verification_confidence") is not None,
                    "verification_confidence": spot.get("verification_confidence")},
        "memory": None, "usage": None, "cost_usd": 0.0, "done": False, "error": None,
    }


def memory_spot(client, spot: dict, effort: str, budget: Budget, sleep) -> tuple:
    """(record, the exception that stopped it or None). One memory answer."""
    record = new_memory_record(spot)
    try:
        budget.require_room()
        data = _as_dict(_create(client, memory_request(spot_identity(spot), effort), sleep))
        used = usage_of(data)
        budget.charge(cost_usd(used))
        record["usage"] = _priced(used)
        record["memory"] = judge_memory(data)
        record["done"] = True
        return record, None
    except Exception as exc:
        record["error"] = _describe(exc)
        log.error("%s (%s): the call failed: %s", spot["name"], spot["region_hint"],
                  record["error"])
        return record, exc
    finally:
        record["cost_usd"] = (record["usage"] or {}).get("cost_usd", 0.0)
        record["finished_at"] = _now()


def memory_run(client, spots: list, results: dict, budget: Budget, effort: str, *,
               limit=None, pause_seconds: float = MEMORY_PAUSE_SECONDS, sleep=time.sleep,
               save=None):
    """A memory answer for each spot not yet done, in order. Returns why it stopped early:
    None, 'budget', or 'error: ...'."""
    started = 0
    for position, spot in enumerate(spots, 1):
        key = memory_key(spot)
        earlier = results["spots"].get(key)
        if earlier and earlier.get("done"):
            continue
        if limit is not None and started >= limit:
            return None
        if not budget.may_start_spot():
            log.info("budget: $%.4f spent of $%.2f; the next spot needs room for $%.2f, "
                     "so the run stops here", budget.spent_usd, budget.cap_usd,
                     budget.spot_reserve_usd())
            return "budget"
        if started and pause_seconds:
            sleep(pause_seconds)
        record, exc = memory_spot(client, spot, effort, budget, sleep)
        results["spots"][key] = record
        results["spent_usd"] = round(budget.spent_usd, 6)
        budget.spot_priced(record["cost_usd"])
        if save is not None:
            save(results)
        started += 1
        memory = record["memory"]
        log.info("[%3d/%d] %s (%s): %s · $%.4f · spent $%.4f of $%.2f", position, len(spots),
                 spot["name"], spot["region_hint"],
                 (f"{memory['break_type']['value']} on {memory['bottom']['value']}, sand "
                  f"bottom {memory['sand_bottom']}") if memory else "no answer",
                 record["cost_usd"], budget.spent_usd, budget.cap_usd)
        if isinstance(exc, BudgetExhausted):
            return "budget"
        if exc is not None and _is_fatal(exc):
            return "error: " + record["error"]
    return None


def new_memory_results(effort: str, budget_usd: float, total_spots: int) -> dict:
    return {
        "schema": MEMORY_SCHEMA_VERSION,
        "kind": "memory_only",
        "what": ("Break type and bottom from the model's memory, no web search, for every "
                 "rated spot. A results file only: nothing in it has been applied to the "
                 "roster or the database."),
        "settings": memory_settings(effort),
        "prices_usd": dict(PRICES),
        "budget_usd": budget_usd,
        "spent_usd": 0.0,
        "total_spots": total_spots,
        "runs": [],
        "spots": {},
    }


def load_memory_results(path: Path, effort: str, budget_usd: float, fresh: bool,
                        total_spots: int) -> dict:
    """The memory results so far, to resume, or a new file. Refuses to mix settings."""
    if fresh or not path.exists():
        return new_memory_results(effort, budget_usd, total_spots)
    results = json.loads(path.read_text(encoding="utf-8"))
    if (results.get("kind") != "memory_only" or results.get("schema") != MEMORY_SCHEMA_VERSION
            or results.get("settings") != memory_settings(effort)):
        raise SystemExit(f"{path} is not a memory-only results file made with these settings "
                         "(model, effort or prompt). Pass --fresh to start over, or --output "
                         "for a new file.")
    results["budget_usd"] = budget_usd
    results["total_spots"] = total_spots
    return results


# --- the memory-only report ------------------------------------------------------------

def memory_verdict(record: dict) -> tuple:
    """('settled', []) when geography settles the bottom, else ('candidate', [reasons])."""
    memory = record["memory"]
    shared = shares_name(memory)
    if (in_sand_barrier_region(record) and memory["sand_bottom"] == "yes"
            and memory["bottom"]["confidence"] == "high" and not shared):
        return "settled", []
    reasons = []
    if any(memory[field]["confidence"] != "high" for field in FIELDS):
        reasons.append(REASON_CONFIDENCE)
    if memory["sand_bottom"] != "yes":
        reasons.append(REASON_NOT_SAND)
    if in_rocky_region(record):
        reasons.append(REASON_ROCKY)
    if shared:
        reasons.append(REASON_NAME)
    if _disagrees_with_verified(record):
        reasons.append(REASON_VERIFIED)
    if not reasons:
        reasons.append(REASON_ELSEWHERE)
    return "candidate", reasons


def _disagrees_with_verified(record: dict) -> bool:
    current, said = record["current"], record["memory"]["break_type"]["value"]
    return bool(current["verified"] and current["break_type"] and said != "unknown"
                and said != current["break_type"])


def confusion(record: dict) -> tuple:
    """(score, reasons): how much a spot needs a person's eye, and why in a few words."""
    memory, current = record["memory"], record["current"]
    kind, bottom = memory["break_type"], memory["bottom"]
    score, why = 0, []
    if _disagrees_with_verified(record):
        score += 3
        why.append(f"memory says {kind['value']}, the verified label says "
                   f"{current['break_type']}")
    if (not current["verified"] and current["break_type"] == "beach"
            and kind["value"] in ("reef", "point") and kind["confidence"] == "high"):
        score += 2
        why.append(f"memory says {kind['value']} with high confidence; the unverified "
                   "label says beach")
    if shares_name(memory):
        score += 2
        places = ", ".join(memory["other_places"][:3])
        why.append("the name is also used " + (f"at {places}" if places else "elsewhere"))
    if in_sand_barrier_region(record) and bottom["value"] in ("rock", "coral", "cobble"):
        score += 2
        why.append(f"memory says a {bottom['value']} bottom in a sand-barrier region")
    lows = [field for field in FIELDS if memory[field]["confidence"] in ("low", None)]
    mediums = [field for field in FIELDS if memory[field]["confidence"] == "medium"]
    if lows:
        score += 2
        why.append("low or no confidence on its " + " and ".join(_NAMES[f] for f in lows))
    elif mediums:
        score += 1
        why.append("medium confidence on its " + " and ".join(_NAMES[f] for f in mediums))
    unknown = [field for field in FIELDS if memory[field]["value"] == "unknown"]
    if unknown:
        score += 1
        why.append("memory does not know its " + " or ".join(_NAMES[f] for f in unknown))
    if kind["mixed"] or bottom["mixed"]:
        score += 1
        why.append("mixed: " + (memory["mixed_note"] or _value_cell(
            bottom if bottom["mixed"] else kind)).rstrip("."))
    return score, why


_NAMES = {"break_type": "break type", "bottom": "bottom"}


def _label(record: dict) -> str:
    return f"{record['name']} ({coast_region(record)})"


def _count_row(name, values: list, order: tuple) -> str:
    return _row(name, len(values), *(values.count(v) for v in order))


def render_memory_report(results: dict) -> str:
    records = list(results["spots"].values())
    done = [r for r in records if r.get("done")]
    total = results.get("total_spots") or len(records)
    out = [f"Memory answers for {len(done)} of {total} rated spots. Spent "
           f"${results['spent_usd']:.4f} of the ${results['budget_usd']:.2f} budget, at list "
           "prices."]
    if done:
        mean = statistics.mean(r["cost_usd"] for r in done)
        left = max(total - len(done), 0)
        out.append(f"Per spot: mean ${mean:.4f}, dearest ${max(r['cost_usd'] for r in done):.4f}."
                   f" The {left} spots left would cost about ${mean * left:.2f} at that mean.")
    failed = [r for r in records if not r.get("done")]
    for record in failed:
        out.append(f"Not answered: {_label(record)}: {record.get('error') or 'not finished'}")
    stops = [run_["stopped"] for run_ in results.get("runs", []) if run_.get("stopped")]
    if stops:
        out.append("Stopped early: " + stops[-1])
    if not done:
        return "\n".join(out)

    flags = ("yes", "no", "unknown")
    out += ["", "By region (Florida split by coast, New York into Montauk and the south shore):",
            "",
            _row("Region", "Spots", "Sand bottom: yes", "no", "unknown"), "|---|---|---|---|---|"]
    regions = sorted({coast_region(r) for r in done})
    for region in regions:
        out.append(_count_row(region, [r["memory"]["sand_bottom"] for r in done
                                       if coast_region(r) == region], flags))
    out.append(_count_row("All", [r["memory"]["sand_bottom"] for r in done], flags))
    levels = ("high", "medium", "low", None)
    out += ["", "By confidence:", "",
            _row("Confidence", "Break type", "Bottom"), "|---|---|---|"]
    for level in levels:
        out.append(_row(level or "none", *(sum(1 for r in done
                                               if r["memory"][field]["confidence"] == level)
                                           for field in FIELDS)))
    sand = [r["memory"]["sand_bottom"] for r in done]
    out += ["", "By sand-bottom flag: " + _tally(sand) + "."]
    out += ["", "Name possibly shared with a spot elsewhere: "
                f"{sum(1 for r in done if shares_name(r['memory']))}."]

    out += ["", "Where memory disagrees with the current label:"]
    for title, group, label in (
            ("Verified labels", [r for r in done if r["current"]["verified"]], None),
            ("Unverified 'beach' labels",
             [r for r in done if not r["current"]["verified"]
              and r["current"]["break_type"] == "beach"], "beach")):
        said = [r for r in group if r["memory"]["break_type"]["value"] != "unknown"]
        differ = [r for r in said if r["memory"]["break_type"]["value"]
                  != r["current"]["break_type"]]
        out += ["", f"{title} ({len(group)} spots): memory gives a break type for {len(said)}, "
                    f"agrees on {len(said) - len(differ)} and disagrees on {len(differ)}."]
        if label == "beach":
            out.append("Memory says the bottom is not sand at "
                       f"{sum(1 for r in group if r['memory']['sand_bottom'] == 'no')} of them.")
        if differ:
            out += ["", _row("Spot", "Label", "Memory", "Memory confidence", "Memory bottom"),
                    "|---|---|---|---|---|"]
            for r in sorted(differ, key=lambda r: (coast_region(r), r["name"])):
                kind = r["memory"]["break_type"]
                out.append(_row(_label(r), r["current"]["break_type"], _value_cell(kind),
                                kind["confidence"] or "none",
                                _value_cell(r["memory"]["bottom"])))

    verdicts = [(r, memory_verdict(r)) for r in done]
    settled = [r for r, (verdict, _) in verdicts if verdict == "settled"]
    out += ["", f"Settled by geography: {len(settled)} spots. The rule: the spot is in a "
                "sand-barrier region, memory says a sand bottom with high confidence, and "
                "memory knows of no same-named spot elsewhere.",
            "Sand-barrier regions: " + ", ".join(SAND_BARRIER_REGIONS) + ". Atlantic Florida "
            "leaves out " + ", ".join(FLORIDA_KNOWN_REEF) + ". Florida's coasts: the Keys "
            "south of 25.3 N; the Gulf west of 84 W, or west of 81.6 W south of 29 N; the "
            "Atlantic everywhere else. Long Island's south shore runs from Breezy Point and "
            "the Rockaways east to East Hampton; Montauk, east of 72.05 W and so including "
            "Hither Hills, is a rocky region and is never settled.",
            "By region: " + ", ".join(
                f"{region} {sum(1 for r in settled if coast_region(r) == region)}"
                for region in SAND_BARRIER_REGIONS) + ".",
            "Settled is about the bottom only: of these, "
            f"{sum(1 for r in settled if _disagrees_with_verified(r))} have a break type that "
            "disagrees with a verified label, and are in the table above."]

    candidates = [(r, reasons) for r, (verdict, reasons) in verdicts if verdict == "candidate"]
    out += ["", f"Candidates for research: {len(candidates)} spots, grouped by reason. A spot "
                "can have more than one."]
    for reason in REASONS:
        group = [r for r, reasons in candidates if reason in reasons]
        out += ["", f"{reason} ({len(group)}):"]
        if group:
            out.append("; ".join(_label(r) for r in sorted(
                group, key=lambda r: (coast_region(r), r["name"]))))

    ranked = sorted(((confusion(r), r) for r in done),
                    key=lambda item: (-item[0][0], coast_region(item[1]), item[1]["name"]))
    ranked = [(why, r) for (score, why), r in ranked if score > 0][:HUMAN_LIST_SIZE]
    out += ["", f"For a human to look at ({len(ranked)} most confusing):"]
    for n, (why, r) in enumerate(ranked, 1):
        out.append(f"{n}. {_label(r)}: " + "; ".join(why) + ".")
    return "\n".join(out)


def render_memory_dry_run(spots: list, effort: str) -> str:
    """Exactly what the model is sent in memory-only mode, without calling it."""
    parts = [f"Model {MODEL}, effort {effort}, no tools. One request per rated spot: "
             f"{len(spots)} spots.", "", "SYSTEM PROMPT (the same for every spot)", "",
             MEMORY_SYSTEM, "THE ONLY TEXT ABOUT EACH SPOT (the user message):"]
    for spot in spots:
        parts += ["", spot_prompt(spot_identity(spot))]
    return "\n".join(parts)


# --- full run: research every rated spot that geography does not settle -----------------
#
# Reads the memory-only run's results. A spot whose memory answer the settled-by-geography
# rule accepts (memory_verdict) is not researched; every other rated spot is, including any
# spot the memory run did not answer. The researched answer is the pilot's, with no memory
# request: the memory answer comes from the memory-only run. Resumable, like the others.

FULL_BUDGET_USD = 70.00
FULL_PAUSE_SECONDS = SPOT_PAUSE_SECONDS
DEFAULT_FULL_OUTPUT = PIPELINE_DIR / "data" / "break_type_research_full.json"
FULL_SCHEMA_VERSION = 1


def load_memory_answers(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"no memory-only results at {path}. Run --memory-only first, or pass "
                         "--memory-results.")
    results = json.loads(path.read_text(encoding="utf-8"))
    if results.get("kind") != "memory_only" or results.get("schema") != MEMORY_SCHEMA_VERSION:
        raise SystemExit(f"{path} is not a memory-only results file "
                         f"(schema {MEMORY_SCHEMA_VERSION})")
    return results


def full_plan(spots: list, memory_results: dict) -> tuple:
    """(settled, to_research), in roster order: settled is [(spot, its memory record)],
    to_research [(spot, its finished memory record or None)]."""
    settled, to_research = [], []
    for spot in spots:
        record = memory_results["spots"].get(memory_key(spot))
        if not (record and record.get("done")):
            to_research.append((spot, None))
        elif memory_verdict(record)[0] == "settled":
            settled.append((spot, record))
        else:
            to_research.append((spot, record))
    return settled, to_research


def full_entry(spot: dict) -> tuple:
    """The spot as a PILOT_SPOTS-shaped entry: asked as its own name, no control answer."""
    return (spot["name"], spot["region_hint"], spot["name"], None)


def new_full_results(effort: str, budget_usd: float) -> dict:
    return {
        "schema": FULL_SCHEMA_VERSION,
        "kind": "full",
        "what": ("Break-type and bottom research for every rated spot that geography does not "
                 "settle. A results file only: nothing in it has been applied to the roster "
                 "or the database, and the spots table has no column for the bottom."),
        "settings": run_settings(effort),
        "prices_usd": dict(PRICES),
        "budget_usd": budget_usd,
        "spent_usd": 0.0,
        "memory_results": None,
        "settled": {},
        "planned": [],
        "regions": {},
        "runs": [],
        "spots": {},
    }


def load_full_results(path: Path, effort: str, budget_usd: float, fresh: bool) -> dict:
    """The full run's results so far, to resume, or a new file. Refuses to mix settings."""
    if fresh or not path.exists():
        return new_full_results(effort, budget_usd)
    results = json.loads(path.read_text(encoding="utf-8"))
    if (results.get("kind") != "full" or results.get("schema") != FULL_SCHEMA_VERSION
            or results.get("settings") != run_settings(effort)):
        raise SystemExit(f"{path} is not a full-run results file made with these settings "
                         "(model, effort, tools or prompts). Pass --fresh to start over, or "
                         "--output for a new file.")
    results["budget_usd"] = budget_usd
    return results


def record_plan(results: dict, settled: list, to_research: list, memory_path: Path,
                memory_results: dict) -> None:
    """Writes this run's plan into the results: which spots are settled, with the memory
    answer that settled them, and which are to be researched, in order."""
    results["memory_results"] = {"file": memory_path.name,
                                 "answered": sum(1 for r in memory_results["spots"].values()
                                                 if r.get("done"))}
    results["settled"] = {memory_key(spot): {key: record[key] for key in (
        "name", "state", "lat", "lng", "current", "memory")} for spot, record in settled}
    results["planned"] = [memory_key(spot) for spot, _ in to_research]
    results["regions"] = {memory_key(spot): coast_region(spot)
                          for spot, _ in settled + to_research}


def _region_of(record: dict) -> str:
    return coast_region({"region_hint": record.get("region") or record.get("state"),
                         "lat": record["lat"], "lng": record["lng"]})


def _spot_label(record: dict) -> str:
    return f"{record['name']} ({_region_of(record)})"


def _memory_value(memory, field: str) -> str:
    return _value_cell(memory[field]) if memory else "(no memory answer)"


def _label_cell(current: dict) -> str:
    if not current.get("break_type"):
        return "(none)"
    return current["break_type"] + (" (verified)" if current.get("verified") else
                                     " (unverified)")


def final_sand_bottom(results: dict) -> list:
    """[(label, region, flag, bottom, from)] for every spot in the plan: settled spots from
    the memory answer that settled them, researched spots from the research alone."""
    rows = []
    for record in results["settled"].values():
        rows.append((_label(record), coast_region(record), record["memory"]["sand_bottom"],
                     _value_cell(record["memory"]["bottom"]),
                     "settled by geography (memory: sand, high confidence)"))
    for key in results["planned"]:
        record = results["spots"].get(key)
        if not (record and record.get("done")):
            region = results["regions"][key]
            rows.append((f"{key.split('|', 1)[0]} ({region})", region, "pending", "—",
                         "not researched yet"))
            continue
        claim = record["research"]["bottom"]
        source = ("research: " + (claim.get("source_url") or "")
                  if claim["status"] == "researched" else
                  "research found no bottom: " + (claim.get("reason") or ""))
        rows.append((_spot_label(record), _region_of(record), record["research"]["sand_bottom"],
                     _researched_cell(record["research"], "bottom"), source))
    return sorted(rows, key=lambda row: (row[1], row[0]))


def render_full_report(results: dict) -> str:
    planned = results["planned"]
    records = [results["spots"][key] for key in planned if key in results["spots"]]
    done = [r for r in records if r.get("done")]
    out = [f"Researched {len(done)} of the {len(planned)} rated spots that geography does not "
           f"settle; {len(results['settled'])} more are settled by geography. Spent "
           f"${results['spent_usd']:.4f} of the ${results['budget_usd']:.2f} budget, at list "
           "prices."]
    memory_file = results.get("memory_results") or {}
    if memory_file:
        out.append(f"Memory answers from {memory_file['file']} ({memory_file['answered']} "
                   "answered).")
    if done:
        costs = [r["cost_usd"] for r in done]
        left = len(planned) - len(done)
        out.append(f"Per spot: mean ${statistics.mean(costs):.4f}, dearest ${max(costs):.4f}. "
                   f"The {left} spots left would cost about ${statistics.mean(costs) * left:.2f} "
                   "at that mean.")
    for record in records:
        if not record.get("done"):
            out.append(f"Not finished: {_spot_label(record)}: "
                       f"{record.get('error') or 'not finished'}")
    stops = [run_["stopped"] for run_ in results.get("runs", []) if run_.get("stopped")]
    if stops:
        out.append("Stopped early: " + stops[-1])
    for field in FIELDS:
        unknown = sum(1 for r in done if r["research"][field]["status"] != "researched")
        out.append(f"Research left the {_NAMES[field]} unknown at {unknown} of {len(done)}.")

    for field in FIELDS:
        changed = [r for r in done if r["research"][field]["status"] == "researched"
                   and (r.get("memory") is None
                        or r["research"][field]["value"] != r["memory"][field]["value"])]
        out += ["", f"{_NAMES[field].capitalize()}: changed against memory ({len(changed)} "
                    "spots; memory 'unknown' or no memory answer counts as a change):", ""]
        header = ["Spot", "Memory", "Researched", "Source URL"]
        if field == "bottom":
            header[3:3] = ["Sand bottom: memory", "Sand bottom: researched"]
        out += [_row(*header), "|" + "---|" * len(header)]
        for r in sorted(changed, key=lambda r: (_region_of(r), r["name"])):
            cells = [_spot_label(r), _memory_value(r.get("memory"), field),
                     _researched_cell(r["research"], field), _url_cell(r["research"], field)]
            if field == "bottom":
                cells[3:3] = [(r.get("memory") or {}).get("sand_bottom") or "—",
                              r["research"]["sand_bottom"]]
            out.append(_row(*cells))

    claimed = [r for r in done if r["research"]["break_type"]["status"] == "researched"]
    changed = [r for r in claimed
               if r["research"]["break_type"]["value"] != r["current"]["break_type"]]
    groups = (("verified", lambda r: r["current"].get("verified")),
              ("unverified", lambda r: r["current"]["break_type"]
               and not r["current"].get("verified")),
              ("no label", lambda r: not r["current"]["break_type"]))
    out += ["", f"Break type: changed against the current label ({len(changed)} of the "
                f"{len(claimed)} researched; "
                + ", ".join(f"{sum(1 for r in changed if test(r))} {name}"
                            for name, test in groups) + "):", "",
            _row("Spot", "Current label", "Researched", "Source URL"), "|---|---|---|---|"]
    for r in sorted(changed, key=lambda r: (_region_of(r), r["name"])):
        out.append(_row(_spot_label(r), _label_cell(r["current"]),
                        _researched_cell(r["research"], "break_type"),
                        _url_cell(r["research"], "break_type")))

    rows = final_sand_bottom(results)
    flags = [row[2] for row in rows]
    out += ["", f"Final sand-bottom flag, every spot ({len(rows)}): "
                + ", ".join(f"{flags.count(flag)} {flag}"
                            for flag in ("yes", "no", "unknown", "pending")) + ".", "",
            _row("Spot", "Sand bottom", "Bottom", "From"), "|---|---|---|---|"]
    for label, _, flag, bottom, source in rows:
        out.append(_row(label, flag, bottom, source))
    return "\n".join(out)


def render_full_dry_run(settled: list, to_research: list, effort: str) -> str:
    """The plan, and exactly what the model is sent, without calling it."""
    regions = sorted({coast_region(spot) for spot, _ in settled + to_research})
    unanswered = sum(1 for _, record in to_research if record is None)
    parts = [f"Settled by geography: {len(settled)} spots. To research: {len(to_research)} "
             f"spots, {unanswered} of them with no memory answer. No memory request is made: "
             "each spot is one researched answer, with at most one follow-up.", "",
             _row("Region", "Settled", "To research"), "|---|---|---|"]
    for region in regions:
        parts.append(_row(region, sum(1 for s, _ in settled if coast_region(s) == region),
                          sum(1 for s, _ in to_research if coast_region(s) == region)))
    if to_research:
        parts += ["", render_dry_run([(full_entry(s), s) for s, _ in to_research], effort,
                                     recall=False)]
    return "\n".join(parts)


# --- entry point --------------------------------------------------------------------

def load_roster(path: Path) -> list:
    roster = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(roster, list):
        raise SystemExit(f"{path} is not a list of spots")
    return roster


def _make_client():
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise SystemExit("ANTHROPIC_API_KEY is not set in this shell. Export it and run again.")
    import inspect

    import anthropic

    client = anthropic.Anthropic()
    if "output_config" not in inspect.signature(client.messages.create).parameters:
        raise SystemExit(f"anthropic {anthropic.__version__} is too old for this script. "
                         "Upgrade it: python3 -m pip install -U 'anthropic<1'")
    return client


def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="python3 -m pipeline.research_break_type",
        description="Research the pilot spots' break types and bottoms into a results file, "
                    "or, with --memory-only, ask the model's memory about every rated spot. "
                    "Reads the roster; never writes it or the database.")
    which = parser.add_mutually_exclusive_group()
    which.add_argument("--memory-only", action="store_true",
                       help="a memory answer (no web search) for every rated spot, into "
                            f"its own results file (default {DEFAULT_MEMORY_OUTPUT.name}, "
                            f"${MEMORY_BUDGET_USD:.2f} cap, {MEMORY_PAUSE_SECONDS:.0f} s "
                            "between spots)")
    which.add_argument("--full", action="store_true",
                       help="research every rated spot that the memory-only run's answers "
                            "do not settle by geography, into its own results file (default "
                            f"{DEFAULT_FULL_OUTPUT.name}, ${FULL_BUDGET_USD:.2f} cap, "
                            f"{FULL_PAUSE_SECONDS:.0f} s between spots)")
    parser.add_argument("--memory-results", type=Path, default=DEFAULT_MEMORY_OUTPUT,
                        help="with --full: the memory-only results to plan from "
                             "(default: %(default)s)")
    parser.add_argument("--output", type=Path, default=None,
                        help=f"the results file (default: {DEFAULT_OUTPUT.name} in "
                             "pipeline/data)")
    parser.add_argument("--roster", type=Path, default=DEFAULT_ENRICHED_OUTPUT,
                        help="the roster to read (default: %(default)s)")
    parser.add_argument("--budget", type=float, default=None,
                        help="the cap in US dollars, across re-runs "
                             f"(default: {PILOT_BUDGET_USD:.2f})")
    parser.add_argument("--effort", choices=EFFORTS, default=DEFAULT_EFFORT)
    parser.add_argument("--limit", type=int, default=None,
                        help="research at most this many spots in this run")
    parser.add_argument("--pause", type=float, default=None,
                        help=f"seconds between spots (default: {SPOT_PAUSE_SECONDS:.0f})")
    parser.add_argument("--fresh", action="store_true",
                        help="start a new results file instead of resuming")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="print what the model is sent; call nothing, write nothing")
    mode.add_argument("--report", action="store_true",
                      help="print the report from the results file; call nothing")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args(argv)
    output, budget, pause = (
        (DEFAULT_MEMORY_OUTPUT, MEMORY_BUDGET_USD, MEMORY_PAUSE_SECONDS) if args.memory_only
        else (DEFAULT_FULL_OUTPUT, FULL_BUDGET_USD, FULL_PAUSE_SECONDS) if args.full
        else (DEFAULT_OUTPUT, PILOT_BUDGET_USD, SPOT_PAUSE_SECONDS))
    if args.output is None:
        args.output = output
    if args.budget is None:
        args.budget = budget
    if args.pause is None:
        args.pause = pause
    return args


def main(argv=None, client=None, sleep=time.sleep) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(message)s", stream=sys.stderr)
    if args.report:
        if not args.output.exists():
            raise SystemExit(f"no results file at {args.output}")
        results = json.loads(args.output.read_text(encoding="utf-8"))
        if args.memory_only:
            if (results.get("kind") != "memory_only"
                    or results.get("schema") != MEMORY_SCHEMA_VERSION):
                raise SystemExit(f"{args.output} is not a memory-only results file "
                                 f"(schema {MEMORY_SCHEMA_VERSION})")
            print(render_memory_report(results))
            return 0
        if args.full:
            if results.get("kind") != "full" or results.get("schema") != FULL_SCHEMA_VERSION:
                raise SystemExit(f"{args.output} is not a full-run results file "
                                 f"(schema {FULL_SCHEMA_VERSION})")
            print(render_full_report(results))
            return 0
        if results.get("kind") is not None or results.get("schema") != SCHEMA_VERSION:
            raise SystemExit(f"{args.output} has results schema {results.get('schema')}; this "
                             f"version of the script reports schema {SCHEMA_VERSION} only")
        print(render_report(results))
        return 0
    if args.memory_only:
        return _main_memory(args, client, sleep)
    if args.full:
        return _main_full(args, client, sleep)
    resolved = resolve_pilot(load_roster(args.roster))
    if args.dry_run:
        print(render_dry_run(resolved, args.effort))
        return 0
    guard_output_path(args.output, args.roster)
    if not args.budget > 0:
        raise SystemExit("--budget must be more than 0")
    results = load_results(args.output, args.effort, args.budget, args.fresh)
    if client is None:
        client = _make_client()
    budget = Budget(args.budget, results["spent_usd"],
                    max(per_spot_costs(results) or [0.0]))
    this_run = {"started_at": _now(), "finished_at": None, "stopped": None}
    results["runs"].append(this_run)
    stopped = run(client, resolved, results, budget, args.effort, limit=args.limit,
                  pause_seconds=args.pause, sleep=sleep,
                  save=lambda data: save_results(args.output, data))
    this_run.update(finished_at=_now(), stopped=stopped)
    results["spent_usd"] = round(budget.spent_usd, 6)
    save_results(args.output, results)
    print(render_report(results))
    log.info("results: %s", args.output)
    return 1 if stopped and stopped.startswith("error") else 0


def _main_memory(args, client, sleep) -> int:
    spots = memory_spots(load_roster(args.roster))
    if args.dry_run:
        print(render_memory_dry_run(spots, args.effort))
        return 0
    guard_output_path(args.output, args.roster)
    if not args.budget > 0:
        raise SystemExit("--budget must be more than 0")
    results = load_memory_results(args.output, args.effort, args.budget, args.fresh, len(spots))
    if client is None:
        client = _make_client()
    costs = [r["cost_usd"] for r in results["spots"].values() if r.get("done")]
    budget = Budget(args.budget, results["spent_usd"], max(costs or [0.0]),
                    floor_usd=MEMORY_RESERVE_FLOOR_USD)
    this_run = {"started_at": _now(), "finished_at": None, "stopped": None}
    results["runs"].append(this_run)
    stopped = memory_run(client, spots, results, budget, args.effort, limit=args.limit,
                         pause_seconds=args.pause, sleep=sleep,
                         save=lambda data: save_results(args.output, data))
    this_run.update(finished_at=_now(), stopped=stopped)
    results["spent_usd"] = round(budget.spent_usd, 6)
    save_results(args.output, results)
    print(render_memory_report(results))
    log.info("results: %s", args.output)
    return 1 if stopped and stopped.startswith("error") else 0


def _main_full(args, client, sleep) -> int:
    spots = memory_spots(load_roster(args.roster))
    memory_results = load_memory_answers(args.memory_results)
    settled, to_research = full_plan(spots, memory_results)
    if args.dry_run:
        print(render_full_dry_run(settled, to_research, args.effort))
        return 0
    guard_output_path(args.output, args.roster)
    if args.output.resolve() == args.memory_results.resolve():
        raise SystemExit(f"refusing to write the full run's results over {args.output}: that "
                         "is the memory-only results file")
    if not args.budget > 0:
        raise SystemExit("--budget must be more than 0")
    results = load_full_results(args.output, args.effort, args.budget, args.fresh)
    record_plan(results, settled, to_research, args.memory_results, memory_results)
    if client is None:
        client = _make_client()
    costs = [r["cost_usd"] for r in results["spots"].values() if r.get("done")]
    budget = Budget(args.budget, results["spent_usd"], max(costs or [0.0]))
    this_run = {"started_at": _now(), "finished_at": None, "stopped": None}
    results["runs"].append(this_run)
    memory = {memory_key(spot): (record or {}).get("memory") for spot, record in to_research}
    stopped = run(client, [(full_entry(spot), spot) for spot, _ in to_research], results,
                  budget, args.effort, limit=args.limit, pause_seconds=args.pause,
                  sleep=sleep, save=lambda data: save_results(args.output, data),
                  memory=memory)
    this_run.update(finished_at=_now(), stopped=stopped)
    results["spent_usd"] = round(budget.spent_usd, 6)
    save_results(args.output, results)
    print(render_full_report(results))
    log.info("results: %s", args.output)
    return 1 if stopped and stopped.startswith("error") else 0


if __name__ == "__main__":
    sys.exit(main())
