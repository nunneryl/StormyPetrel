"""Break-type research: what kind of break each spot is, from a page that says so.

Step 2 of the break-type plan, piloted on the 20 spots in PILOT_SPOTS. It is separate
from verify_spots.py, whose prompt shows the model our current break_type and tells it
not to search for one.

WHAT THE MODEL IS SHOWN. Only the spot's name, its state or territory and its coordinates
(spot_identity). It is never shown our break_type, its source, the verification notes or
anything else in the roster, so it cannot hand our value back to us.

TWO ANSWERS PER SPOT, from the same model (MODEL):
  researched    With web search and web fetch. It counts only with a URL that a search or
                fetch really returned in that request, and a quote that is really on that
                page. Anything less is 'unknown'.
  model_recall  The same question with no tools, from memory. Kept for comparison only.

LOCATION CHECK. A page about a same-named spot somewhere else is no evidence. The model
reports where the page puts the spot; check_location compares that with our state and
coordinates, and a page that puts it somewhere else makes the answer 'unknown'. Hawaii's
Suicide's and Bombora were once researched as California spots.

COST. Each request's usage is priced at list price (PRICES) and added up per spot. A spot
starts only if the money left covers twice the dearest spot so far, and at least
SPOT_RESERVE_FLOOR_USD before any spot has been priced. No request starts once the budget
is spent. The budget ($5 by default) holds across re-runs: the results file carries what
has been spent.

WRITES ONE FILE: the results file (DEFAULT_OUTPUT), after every spot. Never the roster and
never the database. Nothing here imports db_import or a database client, and the results
path may not be the roster.

RUN on the Mac, from the repo root, with ANTHROPIC_API_KEY set:
    python3 -m pipeline.research_break_type --limit 1    # one spot, to check the setup
    python3 -m pipeline.research_break_type              # the rest of the pilot
    python3 -m pipeline.research_break_type --report     # the table again, no API calls
    python3 -m pipeline.research_break_type --dry-run    # what the model is sent, no calls
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
    "max_content_tokens": 6000,
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

PILOT_BUDGET_USD = 5.00
SPOT_RESERVE_FLOOR_USD = 0.50
# Rated spots in the database: the 2026-10-06 pipeline run upserted 646.
FULL_ROSTER_SPOTS = 646
# A page whose coordinates for the spot are further than this from ours describes
# somewhere else. The same scale as config.COORD_FIX_MAX_MOVE_KM.
LOCATION_MAX_KM = 25.0
# A shorter quote ("reef break") could be found on almost any page, so it proves nothing.
MIN_QUOTE_WORDS = 3

DEFAULT_OUTPUT = PIPELINE_DIR / "data" / "break_type_research_pilot.json"
SCHEMA_VERSION = 1

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

RESEARCH_SYSTEM = (
    "You find out what kind of surf break one surf spot is, from a web page that says so,\n"
    "and report it in a fixed format.\n"
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
    "   a spot is.\n"
    "2. Fetch the page you will quote with web_fetch, so that its exact words can be\n"
    "   checked.\n"
    "3. Find the words on that page that say what the waves break over or along, and copy\n"
    "   them exactly: one continuous passage, no ellipses, at most 300 characters.\n"
    "\n"
    "break_type is exactly one of:\n"
    + _KINDS +
    "  unknown     no page you read says what kind of break this spot is, or the pages you\n"
    "              found describe a same-named spot somewhere else\n"
    "If the page names more than one kind (\"a reef and beach break\"), set break_type to\n"
    "the one it says dominates, or else the first one it names, and list every kind it\n"
    "names, in its order, in break_types.\n"
    "\n"
    "Answer only from what a page you read says. Without such a page, break_type is\n"
    "\"unknown\". Never quote words that are not on the page, and never cite a page you did\n"
    "not read.\n"
    "\n"
    "End your reply with this JSON object, and nothing after it:\n"
    "{\n"
    "  \"break_type\": \"beach\" | \"reef\" | \"point\" | \"jetty\" | \"rivermouth\" | \"unknown\",\n"
    "  \"break_types\": [every kind the page names, in its order],\n"
    "  \"source_url\": \"the URL of the page you quote\" or null,\n"
    "  \"quote\": \"the exact words on that page that say what kind of break it is\" or null,\n"
    "  \"source_place\": \"where that page says the spot is, in its words\" or null,\n"
    "  \"source_state\": \"the US state or territory that page puts the spot in\" or null,\n"
    "  \"source_lat\": the latitude that page gives for the spot, or null,\n"
    "  \"source_lng\": the longitude that page gives for the spot, or null,\n"
    "  \"location_quote\": \"the exact words on that page that say where the spot is\" or null,\n"
    "  \"same_name_elsewhere\": [\"other places you found with a surf spot of this name\"],\n"
    "  \"note\": \"one short sentence a reviewer should know\" or null\n"
    "}\n"
)

RECALL_SYSTEM = (
    "You say, from your own knowledge and without searching, what kind of surf break one\n"
    "surf spot is.\n"
    "\n"
    "The spot is given by its name, its US state or territory and its coordinates. Surf\n"
    "spots share names, so answer for the spot at these coordinates in this state.\n"
    "\n"
    "break_type is exactly one of:\n"
    + _KINDS +
    "  unknown     you do not know this particular spot\n"
    "If the spot is more than one kind, set break_type to the dominant one and list every\n"
    "kind in break_types.\n"
    "\n"
    "End your reply with this JSON object, and nothing after it:\n"
    "{\n"
    "  \"break_type\": \"beach\" | \"reef\" | \"point\" | \"jetty\" | \"rivermouth\" | \"unknown\",\n"
    "  \"break_types\": [every kind it is],\n"
    "  \"confidence\": \"high\" | \"medium\" | \"low\",\n"
    "  \"note\": \"one short sentence\" or null\n"
    "}\n"
)

# Words that name each kind, for the reviewer's "does the quote name the type" flag.
TYPE_WORDS = {
    "beach": ("beach", "sand"),
    "reef": ("reef", "coral", "lava", "rock", "ledge", "slab", "boulder"),
    "point": ("point",),
    "jetty": ("jetty", "jetties", "groin", "groyne", "breakwater", "inlet"),
    "rivermouth": ("river", "estuary", "creek"),
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

    def __init__(self, cap_usd: float, spent_usd: float = 0.0, dearest_spot_usd: float = 0.0):
        self.cap_usd = float(cap_usd)
        self.spent_usd = float(spent_usd)
        self.dearest_spot_usd = float(dearest_spot_usd)

    def spot_reserve_usd(self) -> float:
        return max(SPOT_RESERVE_FLOOR_USD, 2.0 * self.dearest_spot_usd)

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


def run_settings(effort: str) -> dict:
    """Everything that shapes an answer. A results file is resumed only under the same."""
    prompts = (RESEARCH_SYSTEM + "\x00" + RECALL_SYSTEM).encode("utf-8")
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
_MD_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")


def _fold(text: str) -> str:
    """*text* for comparing a quote with a page: case, quote marks, dashes, markdown
    and whitespace evened out."""
    folded = unicodedata.normalize("NFKC", text).translate(_PUNCT)
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
    """The last JSON object in *text* that has a break_type, or None."""
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
    """What the tools really returned in one research turn, read from its blocks."""
    queries, fetches, search_urls, errors, texts = [], [], [], [], []
    pages = {}       # _url_key -> page text ('' when the page was a PDF)
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
            else:
                errors.append("web_fetch: " + str(content.get("error_code")))
        elif kind == "text":
            texts.append(block.get("text") or "")
            for citation in block.get("citations") or []:
                if citation.get("url"):
                    citations.append({"url": citation["url"],
                                      "cited_text": citation.get("cited_text") or ""})
    return {"queries": queries, "fetches": fetches, "search_urls": search_urls,
            "pages": pages, "citations": citations, "errors": errors,
            "text": "".join(texts)}


def find_quote(quote, url: str, evidence: dict) -> tuple:
    """Where *quote* is on the page at *url*: ('fetched page' | 'search citation', None),
    or (None, why not)."""
    if not isinstance(quote, str) or not quote.strip():
        return None, "no quote"
    wanted = _clean_quote(quote)
    if len(wanted.split()) < MIN_QUOTE_WORDS:
        return None, f"the quote is under {MIN_QUOTE_WORDS} words, too short to check"
    key = _url_key(url)
    page = evidence["pages"].get(key)
    if page and wanted in _fold(page):
        return "fetched page", None
    for citation in evidence["citations"]:
        if _url_key(citation["url"]) == key and wanted in _clean_quote(citation["cited_text"]):
            return "search citation", None
    return None, "the quote is not on the cited page"


def quote_names_type(quote: str, value: str, spot_name: str) -> bool:
    """Whether the quote, outside the spot's own name, has a word for *value*."""
    unnamed = re.sub(r"\b" + re.escape(_fold(spot_name)) + r"\b", " ", _clean_quote(quote))
    words = _ascii_words(unnamed)
    return any(word.startswith(stem) for word in words for stem in TYPE_WORDS[value])


def _number_in(value, low: float, high: float) -> bool:
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and low <= value <= high)


def check_location(identity: dict, answer: dict) -> dict:
    """Does the page describe OUR spot? Its state, the states its own location words name,
    and its coordinates when it gives them, against ours.

    'wrong place'  it puts the spot in another state, or its coordinates are more than
                   LOCATION_MAX_KM from ours
    'ok'           it puts the spot in our state, or its coordinates are near ours
    'unverified'   it says neither"""
    ours = normalize_state(identity["region"])
    stated = answer.get("source_state")
    theirs = None
    if isinstance(stated, str):
        # As written ("Hawaii (state)", "HI"), else with okina and accents folded away.
        theirs = normalize_state(stated) or normalize_state(" ".join(_ascii_words(stated)))
    reasons = []
    wrong = False
    if theirs is not None and theirs != ours:
        wrong = True
        reasons.append(f"the page puts it in {theirs}")
    named = states_named_in(" ".join(str(answer.get(key) or "")
                                     for key in ("location_quote", "source_place")))
    others = sorted(named - {ours})
    if others and ours not in named:
        wrong = True
        reasons.append("the page's location words name " + ", ".join(others))
    distance_km = None
    lat, lng = answer.get("source_lat"), answer.get("source_lng")
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
            "source_place": answer.get("source_place"),
            "location_quote": answer.get("location_quote"),
            "distance_km": distance_km, "reasons": reasons}


def _kinds(answer: dict, value: str) -> list:
    """Every kind the answer names, each once, in its order; the value first if the list
    left it out."""
    listed = answer.get("break_types")
    listed = listed if isinstance(listed, list) else []
    kinds = []
    for kind in (listed if value in listed else [value] + listed):
        if kind in BREAK_TYPE_VALUES and kind != "unknown" and kind not in kinds:
            kinds.append(kind)
    return kinds


def _note(answer: dict):
    note = answer.get("note")
    return " ".join(str(note).split())[:300] if note else None


def judge_research(responses: list, identity: dict) -> dict:
    """The researched answer: the model's JSON, kept only if a returned page backs it."""
    evidence = collect_evidence([block for response in responses
                                 for block in (response.get("content") or [])])
    stops = [response.get("stop_reason") for response in responses]
    out = {"status": "unknown", "break_type": "unknown", "break_types": [], "mixed": False,
           "source_url": None, "quote": None, "quote_found_in": None,
           "quote_names_type": None, "location": None, "same_name_elsewhere": [],
           "reason": None, "model_answer": None,
           "queries": evidence["queries"], "fetches": evidence["fetches"],
           "search_result_urls": sorted(set(evidence["search_urls"])),
           "tool_errors": evidence["errors"], "stop_reasons": stops}
    if stops and stops[-1] == "refusal":
        out["reason"] = "the model declined to answer (stop_reason refusal)"
        return out
    answer = last_json_object(evidence["text"])
    if answer is None:
        out["reason"] = "no JSON answer" + (" (it ran out of output tokens)"
                                            if stops and stops[-1] == "max_tokens" else "")
        return out
    out["model_answer"] = answer
    elsewhere = answer.get("same_name_elsewhere")
    if isinstance(elsewhere, list):
        out["same_name_elsewhere"] = [" ".join(str(p).split()) for p in elsewhere if p][:10]
    value = answer.get("break_type")
    if value not in BREAK_TYPE_VALUES:
        out["reason"] = f"it answered {value!r}, which is not one of the six values"
        return out
    if value == "unknown":
        out["reason"] = "no page it read says" + (": " + _note(answer) if _note(answer) else "")
        return out
    url = answer.get("source_url")
    if not (isinstance(url, str) and BREAK_TYPE_URL_RE.match(url.strip())):
        out["reason"] = "no source URL"
        return out
    url = url.strip()
    returned = {_url_key(u) for u in evidence["search_urls"]} | set(evidence["pages"])
    if _url_key(url) not in returned:
        out["source_url"] = url
        out["reason"] = "the cited URL was not returned by any search or fetch in this request"
        return out
    found_in, problem = find_quote(answer.get("quote"), url, evidence)
    if found_in is None:
        out["source_url"] = url
        out["reason"] = problem
        return out
    out.update(source_url=url, quote=break_type_quote(answer["quote"]),
               quote_found_in=found_in,
               quote_names_type=quote_names_type(answer["quote"], value, identity["name"]),
               location=check_location(identity, answer))
    if out["location"]["verdict"] == "wrong place":
        out["reason"] = "the page describes another place: " + "; ".join(
            out["location"]["reasons"])
        return out
    kinds = _kinds(answer, value)
    out.update(status="researched", break_type=value, break_types=kinds,
               mixed=len(kinds) > 1)
    return out


def judge_recall(response: dict) -> dict:
    """The memory answer, as the model gave it."""
    text = "".join(block.get("text") or "" for block in (response.get("content") or [])
                   if block.get("type") == "text")
    stop = response.get("stop_reason")
    out = {"source": "model_recall", "break_type": "unknown", "break_types": [],
           "mixed": False, "confidence": None, "note": None, "reason": None,
           "stop_reason": stop}
    if stop == "refusal":
        out["reason"] = "the model declined to answer (stop_reason refusal)"
        return out
    answer = last_json_object(text)
    if answer is None:
        out["reason"] = "no JSON answer"
        return out
    value = answer.get("break_type")
    if value not in BREAK_TYPE_VALUES:
        out["reason"] = f"it answered {value!r}, which is not one of the six values"
        return out
    kinds = _kinds(answer, value) if value != "unknown" else []
    confidence = answer.get("confidence")
    out.update(break_type=value, break_types=kinds, mixed=len(kinds) > 1,
               confidence=confidence if confidence in ("high", "medium", "low") else None,
               note=_note(answer))
    return out


def agreement(researched: str, recalled: str) -> str:
    """'yes' or 'no' when both answers name a type, else 'n/a'."""
    if researched == "unknown" or recalled == "unknown":
        return "n/a"
    return "yes" if researched == recalled else "no"


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
    status = getattr(exc, "status_code", None)
    text = " ".join(str(exc).split())[:300]
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
        "current": {"break_type": spot.get("break_type"),
                    "break_type_source": spot.get("break_type_source")},
        "research": None, "model_recall": None,
        "research_usage": None, "recall_usage": None,
        "cost_usd": 0.0, "agree": None, "done": False, "error": None,
    }


def research_spot(client, entry: tuple, spot: dict, effort: str, budget: Budget,
                  sleep) -> tuple:
    """(record, the exception that stopped it or None). Both answers for one spot."""
    identity = spot_identity(spot)
    record = new_record(entry, spot)
    try:
        request = research_request(identity, effort)
        messages = list(request["messages"])
        responses, usage = [], zero_usage()
        try:
            for _ in range(MAX_PAUSE_CONTINUATIONS + 1):
                budget.require_room()
                message = _create(client, dict(request, messages=messages), sleep)
                data = _as_dict(message)
                responses.append(data)
                used = usage_of(data)
                usage = add_usage(usage, used)
                budget.charge(cost_usd(used))
                if data.get("stop_reason") != "pause_turn":
                    break
                messages = messages + [{"role": "assistant",
                                        "content": _replay_content(message)}]
        finally:
            record["research_usage"] = _priced(usage)
        record["research"] = judge_research(responses, identity)

        budget.require_room()
        data = _as_dict(_create(client, recall_request(identity, effort), sleep))
        used = usage_of(data)
        budget.charge(cost_usd(used))
        record["recall_usage"] = _priced(used)
        record["model_recall"] = judge_recall(data)
        record["agree"] = agreement(record["research"]["break_type"],
                                    record["model_recall"]["break_type"])
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
        save=None):
    """Research the pilot spots not yet done, in order. Returns why it stopped early:
    None, 'budget', or 'error: ...'."""
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
        record, exc = research_spot(client, entry, spot, effort, budget, sleep)
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
    research, recall = record.get("research"), record.get("model_recall")
    usage = record.get("research_usage") or zero_usage()
    parts = [f"{record['asked_as']}:"]
    parts.append(f"researched {research['break_type']}" if research else "no research")
    parts.append(f"memory {recall['break_type']}" if recall else "no memory answer")
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
        "what": ("Break-type research pilot. A results file only: nothing in it has been "
                 "applied to the roster or the database."),
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


def _researched_cell(research) -> str:
    if research is None:
        return "not run"
    if research["status"] == "researched":
        cell = research["break_type"]
        if research["mixed"]:
            cell += " (mixed: " + " and ".join(research["break_types"]) + ")"
        if research["quote_names_type"] is False:
            cell += " · the quote does not name the type"
        return cell
    return "unknown: " + (research.get("reason") or "")


def _location_cell(research) -> str:
    if research is None:
        return "—"
    location = research.get("location")
    if location is None:
        cell = "—"
    elif location["verdict"] == "ok":
        cell = f"ok ({location['source_state'] or location['our_state']}"
        if location["distance_km"] is not None:
            cell += f", {location['distance_km']:.0f} km from ours"
        cell += ")"
    else:
        cell = location["verdict"] + ": " + "; ".join(location["reasons"])
    if research.get("same_name_elsewhere"):
        cell += " · name also used: " + ", ".join(research["same_name_elsewhere"])
    return cell


def _memory_cell(recall) -> str:
    if recall is None:
        return "not run"
    cell = recall["break_type"]
    if recall["mixed"]:
        cell += " (mixed: " + " and ".join(recall["break_types"]) + ")"
    if recall["confidence"]:
        cell += f", {recall['confidence']} confidence"
    if recall["reason"]:
        cell += f" ({recall['reason']})"
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


def render_report(results: dict) -> str:
    lines = ["| Spot | Current value | Researched value | Source URL | Location check "
             "| Memory answer | Agree |",
             "|---|---|---|---|---|---|---|"]
    costs = ["| Spot | Searches | Fetches | Input tokens | Output tokens | Research | Memory "
             "| Total |",
             "|---|---|---|---|---|---|---|---|"]
    for entry in PILOT_SPOTS:
        record = results["spots"].get(spot_key(entry))
        label = entry[2] + (f" (control: {entry[3]})" if entry[3] else "")
        if record is None:
            lines.append(f"| {_cell(label)} | — | not run | — | — | — | — |")
            continue
        research, recall = record.get("research"), record.get("model_recall")
        lines.append("| " + " | ".join(_cell(c) for c in (
            label,
            record["current"]["break_type"] or "(none)",
            _researched_cell(research),
            (research or {}).get("source_url") or "—",
            _location_cell(research),
            _memory_cell(recall),
            record.get("agree") or "—",
        )) + " |")
        r_use = record.get("research_usage") or _priced(zero_usage())
        m_use = record.get("recall_usage") or _priced(zero_usage())
        costs.append("| " + " | ".join(_cell(c) for c in (
            label,
            r_use["web_search_requests"],
            r_use["web_fetch_requests"],
            f"{input_tokens_all(r_use) + input_tokens_all(m_use):,}",
            f"{r_use['output_tokens'] + m_use['output_tokens']:,}",
            f"${r_use['cost_usd']:.4f}",
            f"${m_use['cost_usd']:.4f}",
            f"${record['cost_usd']:.4f}" + ("" if record.get("done")
                                           else " (unfinished: " + (record.get("error") or "")
                                           + ")"),
        )) + " |")
    out = lines + [""] + costs + [""]
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
    stops = [run_["stopped"] for run_ in results.get("runs", []) if run_.get("stopped")]
    if stops:
        out.append("Stopped early: " + stops[-1])
    return "\n".join(out)


def render_dry_run(resolved: list, effort: str) -> str:
    """Exactly what the model is sent, without calling it."""
    sample = research_request(spot_identity(resolved[0][1]), effort)
    parts = [f"Model {MODEL}, effort {effort}. Every request is one of these two.", "",
             "RESEARCHED ANSWER: system prompt (the same for every spot)", "",
             RESEARCH_SYSTEM,
             "tools:", json.dumps(sample["tools"], indent=2), "",
             "MEMORY ANSWER: system prompt (the same for every spot; no tools)", "",
             RECALL_SYSTEM,
             "THE ONLY TEXT ABOUT EACH SPOT (the user message, the same in both requests):"]
    for _, spot in resolved:
        parts += ["", spot_prompt(spot_identity(spot))]
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
        description="Research the pilot spots' break types into a results file. Reads the "
                    "roster; never writes it or the database.")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                        help="the results file (default: %(default)s)")
    parser.add_argument("--roster", type=Path, default=DEFAULT_ENRICHED_OUTPUT,
                        help="the roster to read (default: %(default)s)")
    parser.add_argument("--budget", type=float, default=PILOT_BUDGET_USD,
                        help="the cap in US dollars, across re-runs (default: %(default).2f)")
    parser.add_argument("--effort", choices=EFFORTS, default=DEFAULT_EFFORT)
    parser.add_argument("--limit", type=int, default=None,
                        help="research at most this many spots in this run")
    parser.add_argument("--pause", type=float, default=SPOT_PAUSE_SECONDS,
                        help="seconds between spots (default: %(default).0f)")
    parser.add_argument("--fresh", action="store_true",
                        help="start a new results file instead of resuming")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="print what the model is sent; call nothing, write nothing")
    mode.add_argument("--report", action="store_true",
                      help="print the report from the results file; call nothing")
    parser.add_argument("-v", "--verbose", action="store_true")
    return parser.parse_args(argv)


def main(argv=None, client=None, sleep=time.sleep) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(message)s", stream=sys.stderr)
    if args.report:
        if not args.output.exists():
            raise SystemExit(f"no results file at {args.output}")
        print(render_report(json.loads(args.output.read_text(encoding="utf-8"))))
        return 0
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


if __name__ == "__main__":
    sys.exit(main())
