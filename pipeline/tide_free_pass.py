"""The free tide pass: best-tide statements read from the pages the break-type research already
fetched, for review. A DRY RUN ONLY: it writes a review file and a conflicts file, never the roster
and never the database. Nothing is imported until a person has reviewed the rows.

    python3 -m pipeline.tide_free_pass
    python3 -m pipeline.tide_free_pass --results path/to/break_type_research_full.json

No API calls and no network. It reads the full run's results (research_break_type --full,
pipeline/data/break_type_research_full.json, git-ignored, on the Mac only), the saved page text in
it read exactly as research_break_type --recheck reads it (collect_evidence).

WHAT COUNTS AS A STATEMENT, all of these:
  1. The page is one the break-type research cited for THIS spot and whose location check read
     'ok' (check_location): a page about a same-named spot elsewhere says nothing about ours.
  2. The page is not ours (EXCLUDED_DOMAINS): stormypetrel.surf repeats what we hold.
  3. The sentence is clean prose (prose_sentences): not a heading, a table row, link markup, a
     navigation menu or forecast-widget text. A quote must read as a sentence on the page.
  4. The sentence names the spot, or the page is about the spot alone (its URL names it): a
     guide covering a whole coast says things about other breaks too.
  5. map_sentence gives it a value (the rules are in its docstring), and the sentence is on the
     page word for word (research_break_type._holds), so the quote is the page's own.

ONE PAGE: one value. A page that says "works at all tides" and also "best at low tide" is 'all';
any other pair on one page is a conflict on that page.

ONE SPOT, several pages (combine): equal values agree; adjacent values combine into the compound
that covers both (low + mid -> low_mid); anything else — 'all' against a stage, low against high —
is a conflict. NEVER_ALONE_DOMAINS (mondo.surf, swell-intel.com) never decide on their own: they
count only beside another domain, and a value from them that disagrees with the other domain is
noted, not counted. Any other domain that says one value for most of its spots (DOMINANT_*) is
flagged in the review file, not excluded: a person decides.

THE REVIEW FILE (REVIEW_OUTPUT), one row per spot with a value: status, value, whether the import
would write it, the reviewed decision and its note, the domains, the quote, its URL and domain,
flags. Single-source rows and unusual values come first. Uncorroborated rows (a never-alone domain
only) follow. THE CONFLICTS FILE (CONFLICTS_OUTPUT) lists every spot whose statements disagree,
each page with its value, quote and URL.

THE DECISIONS (DECISIONS_FILE, committed): a person's 'reject' keeps a spot's row out of any import;
an 'accept' names the value the person checked, and the row is written only while the pass still
proposes exactly that value. A proposed row with no decision may be imported once reviewed; an
uncorroborated row only with an 'accept'. See import_decision.

EACH RUN SAYS WHAT CHANGED: before the review file is replaced it is copied beside itself
(*.previous.csv) and compared with the new one, row by row; anything typed into the old file's
decision column that is not in the decisions file is listed, so it is not lost.
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

from .config import DEFAULT_ENRICHED_OUTPUT, PIPELINE_DIR, break_type_quote
from .research_break_type import (
    DEFAULT_FULL_OUTPUT,
    _holds,
    _url_key,
    _words,
    collect_evidence,
)

REVIEW_OUTPUT = PIPELINE_DIR / "data" / "tide_free_pass_review.csv"
CONFLICTS_OUTPUT = PIPELINE_DIR / "data" / "tide_free_pass_conflicts.csv"
# A person's decisions on the review rows, committed. The free pass shows them in the review file,
# and the import, when there is one, writes only what import_decision allows (see there).
DECISIONS_FILE = PIPELINE_DIR / "data" / "tide_review_decisions.json"
DECISIONS = ("accept", "reject")
DECISION_KEYS = frozenset({"name", "state", "decision", "value", "note"})
NOTE_MAX_CHARS = 300

# Our own site: a page there repeats what we hold, so it is evidence of nothing.
EXCLUDED_DOMAINS = ("stormypetrel.surf",)
# Domains whose tide statements look templated: mondo.surf said 'all' for half its spots and
# called reefs in California, Oregon and Rhode Island coral; swell-intel.com said 'mid' for 22 of
# 24 spots. Their statements count only beside another domain.
NEVER_ALONE_DOMAINS = ("mondo.surf", "swell-intel.com")
# Any other domain that gives one value for at least this share of at least this many spots is
# flagged in the review file for a person to judge.
DOMINANT_MIN_SPOTS = 10
DOMINANT_SHARE = 0.75

VALUES = ("low", "low_mid", "mid", "mid_high", "high", "all")
STAGES = {"low": "L", "low_mid": "LM", "mid": "M", "mid_high": "MH", "high": "H", "all": "LMH"}
COMPOUND = {frozenset("LM"): "low_mid", frozenset("MH"): "mid_high"}


def domain(url: str) -> str:
    """The URL's host without 'www.', lowercased."""
    return _url_key(url).split("/")[0]


def _on(host: str, domains: tuple) -> bool:
    return any(host == d or host.endswith("." + d) for d in domains)


def excluded(url: str) -> bool:
    return _on(domain(url), EXCLUDED_DOMAINS)


def never_alone(url: str) -> bool:
    return _on(domain(url), NEVER_ALONE_DOMAINS)


# --- 1. clean prose ---------------------------------------------------------------------------
#
# A page from the fetch tool is markdown: headings, tables, link lists, menus and widgets sit
# among the prose. The two cases that set these rules, found by the measurement run:
#   15th Street Del Mar mapped 'low' from a forecast widget ("Timezone … Forecast updated … Closes")
#   Carpinteria mapped 'mid' from swell-intel.com's navigation links
# A sentence is prose when it has no bare URL or table bar, at most one link (read as its text,
# as "[Hermosa Beach](...) in LA County is ..." is a sentence about the spot) and that link no
# more than a third of its words, no widget vocabulary or clock time, at most MAX_NUMBERS
# numbers, 6 to 60 words, a capital (or digit) first and a full stop, question or exclamation
# mark last, at least one ordinary function word, and no more than half its later words
# capitalised (a menu is Title Case). "Sunset" and "closes out" are surf words, so the widget
# vocabulary leaves them alone.

_MD_LINK = re.compile(r"\[([^\]]*)\]\((?:[^()\s]|\([^()\s]*\))*\)")
_BARE = re.compile(r"https?://|\bwww\.|\||\]\(")
_WIDGET = re.compile(r"\b(time\s?zone|forecast updated|last updated|updated \d|opens at"
                     r"|closes(?!\s+out)|kts|km/h)\b|°", re.I)
_CLOCK = re.compile(r"\b\d{1,2}:\d{2}\b")
_EMPHASIS = re.compile(r"\*\*|__|`")
_BULLET = re.compile(r"^(?:[*+-]|\d+[.)])\s+")
_RUN_ON = re.compile(r"([a-z][.!?])([A-Z])")
_SENTENCE_END = re.compile(r"[.!?][\"'”’)\]]*$")
_TOKEN = re.compile(r"[A-Za-z][A-Za-z'’-]*|\d[\d.,/-]*")
FUNCTION_WORDS = frozenset({"the", "a", "an", "is", "are", "was", "it", "its", "at", "on", "in",
                            "and", "or", "with", "to", "of", "for", "from", "but", "this",
                            "can", "best", "works", "when", "during"})
MIN_WORDS, MAX_WORDS = 6, 60
MAX_NUMBERS = 4
MAX_LINKS = 1


def unlinked(sentence: str):
    """*sentence* with its one link read as its text, or None when it has more than MAX_LINKS
    links, a link that is more than a third of its words, or a bare URL or table bar."""
    links = _MD_LINK.findall(sentence)
    plain = _MD_LINK.sub(lambda m: m.group(1), sentence)
    if len(links) > MAX_LINKS or _BARE.search(plain):
        return None
    words = len(_TOKEN.findall(plain))
    if links and len(_TOKEN.findall(links[0])) * 3 > words:
        return None
    return plain


def is_prose(sentence: str) -> bool:
    """Whether *sentence* reads as a sentence of prose (see the rules above)."""
    s = unlinked(sentence.strip())
    if not s or _WIDGET.search(s) or _CLOCK.search(s):
        return False
    tokens = _TOKEN.findall(s)
    words = [t for t in tokens if t[0].isalpha()]
    numbers = len(tokens) - len(words)
    if not MIN_WORDS <= len(words) <= MAX_WORDS or numbers > MAX_NUMBERS:
        return False
    first = next((c for c in s if c.isalnum()), "")
    if not (first.isupper() or first.isdigit()) or not _SENTENCE_END.search(s):
        return False
    if not any(w.lower() in FUNCTION_WORDS for w in words):
        return False
    later = words[1:]
    return sum(1 for w in later if w[0].isupper()) <= len(later) / 2


def prose_sentences(text: str) -> list:
    """The page's prose sentences, in order, each with its one link (if any) read as its text.
    Headings, table rows and block quotes are dropped; a list item is read on its own, never run
    into the next; emphasis markers are removed."""
    paragraphs, current = [], []

    def flush():
        if current:
            paragraphs.append(" ".join(current))
            current.clear()
    for line in (text or "").splitlines():
        t = line.strip()
        if not t or t.startswith(("#", "|", ">")):
            flush()
            continue
        bullet = _BULLET.match(t)
        if bullet:
            flush()
            paragraphs.append(t[bullet.end():])
            continue
        current.append(t)
    flush()
    out = []
    for paragraph in paragraphs:
        paragraph = _EMPHASIS.sub("", re.sub(r"\s+", " ", paragraph))
        # A full stop with no space after it still ends a sentence: "higher tide.Best at 3-8 ft."
        # Only after a lower-case letter and before a capital, so "0.4 ft" and "U.S." stay whole.
        paragraph = _RUN_ON.sub(r"\1 \2", paragraph)
        for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'“\[])", paragraph):
            if is_prose(s):
                out.append(unlinked(s.strip()))
    return out


# --- 2. what a sentence says -----------------------------------------------------------------

STAGE = r"(low|mid|medium|middle|half|high)"
UNIT = r"(?:tides?|tide (?:levels?|conditions|stages?)|water)"
POSITIVE = (r"(best|works?|working|work best|favou?rable|ideal|good|great|excellent|prefers?"
            r"|needs?|requires?|only|breaks? best|turns on|comes alive|fires|fish for it"
            r"|suitable|surf(?:ed|able)|rideable|performs best)")
# About something other than the surf: never a preference, whatever else the sentence says.
NOT_ABOUT_SURF = (r"(tidepools?|tide pools?|accessib\w* only|only accessib\w*|access\w* only during"
                  r"|walk(ing)? (around|out|across)|parking|sea glass|dog|swimming|beachcomb)")
# About the surf, but not a preference unless "best", "only" or "needs" says so.
NOT_A_PREFERENCE = (r"(exposed|dangerous|shallow|rocks? show|hollower|mellower|forgiving"
                    r"|challenging|depend|dependent|sensitive|check the tide)")
DIRECTION = r"\b(rising|incoming|pushing|push|dropping|falling|outgoing|ebb|flood)(ing)? tide\b"
# Between the two stages of a range: "to", "and", "through", "or", a hyphen, slash or dash, also
# written with hyphens ("low-to-mid"); and a direction word may sit before either stage ("a low
# to rising mid tide").
_MOVING = r"(?:(?:rising|incoming|pushing|dropping|falling|outgoing)\s+)?"
_JOIN = r"\s*(?:-\s*)?(to|and|through|or|-|/)(?:\s*-)?\s*"
# A list of all three stages: "low, mid, and high tide", "low, medium or high tides".
_LIST_SEP = r"\s*(?:,\s*(?:and\s+|or\s+)?|\s(?:and|or)\s+|/)\s*"
STAGE_OF = {"low": "low", "mid": "mid", "medium": "mid", "middle": "mid", "half": "mid",
            "high": "high"}
PAIR = {frozenset({"low", "mid"}): "low_mid", frozenset({"mid", "high"}): "mid_high",
        frozenset({"low", "high"}): "all"}


def map_sentence(s: str) -> tuple:
    """(value, rule) for one sentence, or (None, why) when it is not a statement about the
    surf's tide at all. value is one of VALUES, or 'unknown' for a tide statement that gives no
    usable stage: a direction ("pushing tide"), "tide dependent", a comparison ("hollower at
    lower tides"), a negative ("avoid high tide"), two stages that are not adjacent ("low
    and high"), or two stage words run together ("medium high to low"). "low to high" is a
    range, so 'all', and so is a list of all three ("low, mid, and high tide"). A range may be
    hyphenated ("low-to-mid") and carry a direction word ("a low to rising mid tide"). A stage
    needs a positive word beside it ("best", "works", "needs", "only", "good" ...)."""
    t = s.lower().replace("’", "'").replace("–", "-").replace("—", "-")
    if not re.search(r"\btides?\b|\blow water\b|\bhigh water\b", t):
        return None, "no tide"
    if re.search(NOT_ABOUT_SURF, t):
        return None, "not about the surf"
    if re.search(r"\b(all|any|every) (stages? of the )?tides?\b|\ball tide (levels|conditions|stages)"
                 r"|\b(at|on|in) all stages\b|\bwhatever the tide\b|\btide (doesn't|does not) matter",
                 t):
        return "all", "all tides"
    # Two stage words side by side, with nothing joining them ("medium high to low"): which
    # stages it means cannot be read.
    if re.search(rf"\b{STAGE}\s+{STAGE}\b", t):
        return "unknown", "stages run together"
    m = re.search(rf"\b{STAGE}{_LIST_SEP}{STAGE}{_LIST_SEP}{STAGE}\b", t)
    if m and re.search(POSITIVE, t):
        listed = frozenset(STAGE_OF[g] for g in m.groups())
        if listed == {"low", "mid", "high"}:
            return "all", "all three stages listed"
        # Three words for two stages ("low, mid, or medium tide"): the pair they name, if any.
        if PAIR.get(listed) in ("low_mid", "mid_high"):
            return PAIR[listed], "list of two stages"
        return "unknown", "listed stages that are not adjacent"
    m = re.search(rf"\b{_MOVING}{STAGE}{_JOIN}{_MOVING}{STAGE}\b(?:\s+{UNIT})?", t)
    if m and re.search(POSITIVE, t) and STAGE_OF[m.group(1)] != STAGE_OF[m.group(3)]:
        if re.search(NOT_A_PREFERENCE, t) and not re.search(r"\bbest\b", t):
            return "unknown", "pair, not a preference"
        pair = frozenset({STAGE_OF[m.group(1)], STAGE_OF[m.group(3)]})
        if pair == frozenset({"low", "high"}) and m.group(2) in ("and", "or", "/"):
            return "unknown", "two stages that are not adjacent"
        return PAIR[pair], "pair"
    m = re.search(r"\b(minus|negative|spring) (low )?tide\b", t)
    if m and re.search(POSITIVE, t):
        return "low", "minus tide"
    hits = {STAGE_OF[x] for x in re.findall(rf"\b{STAGE}(?:er)? {UNIT}", t)}
    if len(hits) == 1:
        if re.search(NOT_A_PREFERENCE, t) and not re.search(r"\b(best|only|needs?|requires?)\b", t):
            return "unknown", "stage named, not a preference"
        if re.search(r"\b(lower|higher) tides?\b", t) and not re.search(r"\b(best|only|needs?)\b", t):
            return "unknown", "comparative"
        if re.search(r"\b(not|never|avoid|doesn't|does not|won't|kills?|swamp)", t):
            return "unknown", "negative"
        if re.search(POSITIVE, t):
            return hits.pop(), "single stage"
        return "unknown", "stage named, no positive cue"
    if len(hits) > 1:
        return "unknown", "several stages"
    if re.search(DIRECTION, t):
        return "unknown", "direction, not a stage"
    return "unknown", "tide named, no stage"


# --- 3. one page, one spot ---------------------------------------------------------------------

def _squash(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


# Words a guide's URL or sentence often leaves out of a spot's name: mondo.surf files Jalama Beach
# under /surf-spot/jalama/.
GENERIC_NAME_WORDS = frozenset({"beach", "beaches", "state", "park", "pier", "county", "the"})
MIN_NAME_CHARS = 4


def spot_names(name: str) -> list:
    """The spot's name as a page might write it: whole, before a comma ("Redondo Beach,
    California" -> "Redondo Beach"), and without GENERIC_NAME_WORDS ("Jalama Beach" -> "Jalama")
    when at least MIN_NAME_CHARS letters are left."""
    head = name.split(",")[0]
    core = _squash(" ".join(w for w in re.findall(r"[A-Za-z0-9']+", head)
                            if w.lower() not in GENERIC_NAME_WORDS))
    names = {_squash(name), _squash(head)}
    if len(core) >= MIN_NAME_CHARS:
        names.add(core)
    return sorted((n for n in names if n), key=len)


def page_is_about(url: str, name: str) -> bool:
    """Whether the page's URL path names the spot, so the page is about it alone."""
    path = _squash(_url_key(url).partition("/")[2])
    return any(n in path for n in spot_names(name))


def names_spot(sentence: str, name: str) -> bool:
    return any(n in _squash(sentence) for n in spot_names(name))


def page_value(values: list):
    """(value, conflict?) for one page's mapped values: one value, or 'all' when the page says
    it works at all tides beside a 'best at' stage; any other mix is a conflict."""
    distinct = set(values)
    if not distinct:
        return None, False
    if len(distinct) == 1:
        return distinct.pop(), False
    if "all" in distinct:
        return "all", False
    return None, True


def combine(values) -> str | None:
    """The one value several pages agree on, or None for a conflict. Equal values agree;
    adjacent ones give the compound covering both; 'all' against a stage, and stages that do
    not form low_mid or mid_high, conflict."""
    distinct = set(values)
    if len(distinct) == 1:
        return next(iter(distinct))
    if not distinct or "all" in distinct:
        return None
    stages = frozenset("".join(STAGES[v] for v in distinct))
    return COMPOUND.get(stages)


def location_ok_pages(record: dict) -> set:
    """URL keys of the pages the break-type research cited for this spot with location 'ok'."""
    out = set()
    for field in ("break_type", "bottom"):
        claim = (record.get("research") or {}).get(field) or {}
        if claim.get("source_url") and (claim.get("location") or {}).get("verdict") == "ok":
            out.add(_url_key(claim["source_url"]))
    return out


def spot_pages(record: dict, stats: Counter = None) -> list:
    """Each location-checked page of one spot with what it says about the tide:
    {'url', 'domain', 'value', 'conflict', 'statements': [(value, sentence)]}."""
    stats = stats if stats is not None else Counter()
    saved = record.get("research_responses")
    if not saved:
        return []
    responses = list(saved.get("first") or []) + list(
        (saved.get("follow_up") or {}).get("responses") or [])
    ev = collect_evidence([b for r in responses for b in (r.get("content") or [])])
    ok = location_ok_pages(record)
    name = record["identity"]["name"]
    out, seen = [], set()
    for fetched in ev["fetched"]:
        url, key = fetched["url"], _url_key(fetched["url"])
        if key in seen:
            continue
        seen.add(key)
        if excluded(url):
            stats["pages on an excluded domain"] += 1
            continue
        if key not in ok:
            continue
        text = ev["pages"].get(key) or ""
        page_words = _words(text)
        whole_page = page_is_about(url, name)
        statements = []
        for s in prose_sentences(text):
            value, _ = map_sentence(s)
            if value in (None, "unknown"):
                continue
            if not (whole_page or names_spot(s, name)):
                stats["statements about another break on a shared page"] += 1
                continue
            if not _holds(page_words, _words(s)):
                stats["statements not found on the page word for word"] += 1
                continue
            statements.append((value, break_type_quote(s)))
        if not statements:
            continue
        value, conflict = page_value([v for v, _ in statements])
        out.append({"url": url, "domain": domain(url), "value": value, "conflict": conflict,
                    "statements": statements})
    return out


def decide(pages: list) -> dict:
    """One spot's outcome from its pages: status 'proposed', 'uncorroborated', 'conflict' or
    'none'; the value; the pages that decided it; the never-alone pages that disagreed."""
    valued = [p for p in pages if p["value"] is not None]
    counting = [p for p in valued if not never_alone(p["url"])]
    alone = [p for p in valued if never_alone(p["url"])]
    page_conflicts = [p for p in pages if p["conflict"]]
    base = {"value": None, "pages": [], "ignored": [], "conflict_pages": page_conflicts}
    if counting:
        value = combine(p["value"] for p in counting)
        if value is None:
            return {**base, "status": "conflict", "pages": counting + alone}
        agree = [p for p in alone if combine([value, p["value"]]) is not None]
        return {**base, "status": "proposed", "value": combine([value] + [p["value"] for p in agree]),
                "pages": counting + agree, "ignored": [p for p in alone if p not in agree]}
    if alone:
        value = combine(p["value"] for p in alone)
        if value is None:
            return {**base, "status": "conflict", "pages": alone}
        return {**base, "status": "uncorroborated", "value": value, "pages": alone}
    if page_conflicts:
        return {**base, "status": "conflict", "pages": page_conflicts}
    return {**base, "status": "none"}


def dominant_domains(outcomes: list) -> dict:
    """{domain: 'mostly <value> (n of m)'} for domains outside NEVER_ALONE_DOMAINS that give
    one value for at least DOMINANT_SHARE of at least DOMINANT_MIN_SPOTS spots."""
    by_domain = defaultdict(Counter)
    for outcome in outcomes:
        for page in outcome["all_pages"]:
            if page["value"] is not None:
                by_domain[page["domain"]][page["value"]] += 1
    flags = {}
    for host, counts in by_domain.items():
        n = sum(counts.values())
        value, top = counts.most_common(1)[0]
        if not _on(host, NEVER_ALONE_DOMAINS) and n >= DOMINANT_MIN_SPOTS \
                and top >= DOMINANT_SHARE * n:
            flags[host] = f"mostly {value} ({top} of {n})"
    return flags


# --- 4. a person's decisions -------------------------------------------------------------------

def load_decisions(path: Path, roster: list) -> dict:
    """{'name|state': entry} from the decisions file. Fails loudly, naming every entry, if any is
    not exactly one rated spot in the roster, is listed twice, has a key the file does not use,
    a decision other than accept or reject, an accept without one of VALUES or a reject with a
    value, or no note of 1-NOTE_MAX_CHARS characters. A missing file is no decisions."""
    from .research_break_type import memory_key, memory_spots
    if not path.exists():
        return {}
    import json
    doc = json.loads(path.read_text(encoding="utf-8"))
    entries = doc.get("spots") if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        raise SystemExit(f"{path} has no list of spots")
    rated = {memory_key(spot) for spot in memory_spots(roster)}
    out, problems = {}, []
    for entry in entries:
        entry = entry if isinstance(entry, dict) else {}
        key = f"{entry.get('name')}|{entry.get('state')}"
        label = f"{entry.get('name')} ({entry.get('state')})"
        extra = sorted(set(entry) - DECISION_KEYS)
        decision, value, note = entry.get("decision"), entry.get("value"), entry.get("note")
        if key in out:
            problems.append(f"{label}: listed twice")
        elif key not in rated:
            problems.append(f"{label}: not a rated spot in {DEFAULT_ENRICHED_OUTPUT.name} "
                            "(the name and state must match exactly)")
        elif extra:
            problems.append(f"{label}: unknown key{'s' if len(extra) > 1 else ''} "
                            + ", ".join(repr(k) for k in extra))
        elif decision not in DECISIONS:
            problems.append(f"{label}: decision {decision!r} is not one of {DECISIONS}")
        elif decision == "accept" and value not in VALUES:
            problems.append(f"{label}: an accept names the value checked, one of {VALUES}")
        elif decision == "reject" and "value" in entry:
            problems.append(f"{label}: a reject takes no value")
        elif not (isinstance(note, str) and 1 <= len(note.strip()) <= NOTE_MAX_CHARS):
            problems.append(f"{label}: needs a note of 1-{NOTE_MAX_CHARS} characters")
        else:
            out[key] = entry
    if problems:
        raise SystemExit(f"{path}: {len(problems)} of its {len(entries)} entries cannot be used:\n  "
                         + "\n  ".join(problems))
    return out


def stating_page(outcome: dict):
    """The first of the outcome's pages that states its value itself, or None. A value combined
    from pages that each say less ("low" here, "mid" there: low_mid) has none."""
    return next((p for p in outcome.get("pages") or [] if p["value"] == outcome["value"]), None)


def import_decision(outcome: dict, entry) -> tuple:
    """(import?, why) for one spot's outcome and its decision entry (or None). The rule the import
    applies (pipeline/import_tide_free_pass.py):
      reject                       never
      combined                     never: no one page states the value, and a 'researched' value
                                   is a cited page and a quote that say it (migration 022)
      accept                       only while the pass proposes (or, uncorroborated, gives)
                                   exactly the value accepted
      no decision                  a proposed row, once reviewed; never an uncorroborated one,
                                   a conflict or nothing"""
    status, value = outcome["status"], outcome["value"]
    if entry is not None and entry["decision"] == "reject":
        return False, "rejected on review"
    if value is not None and stating_page(outcome) is None:
        return False, f"combined: no one page says {value}"
    if entry is not None:
        if status in ("proposed", "uncorroborated") and value == entry["value"]:
            return True, f"accepted on review as {entry['value']}"
        return False, (f"accepted on review as {entry['value']}, but the pass now gives "
                       f"{value or 'no value'} ({status})")
    if status == "proposed":
        return True, "proposed"
    return False, {"uncorroborated": "uncorroborated: needs an accept",
                   "conflict": "conflict", "none": "no statement"}[status]


# --- 5. the review file ------------------------------------------------------------------------

REVIEW_COLUMNS = ("name", "state", "status", "value", "import", "decision", "decision_note",
                  "domains", "quote", "url", "domain", "flags", "other_quotes")
CONFLICT_COLUMNS = ("name", "state", "domain", "value", "quote", "url", "note")


def _flags(pages: list, dominant: dict, ignored: list) -> str:
    out = []
    for host in sorted({p["domain"] for p in pages}):
        if _on(host, NEVER_ALONE_DOMAINS):
            out.append(f"{host}: never counts alone")
        elif host in dominant:
            out.append(f"{host}: {dominant[host]}")
    for p in ignored:
        out.append(f"{p['domain']} says {p['value']}, not counted")
    return "; ".join(out)


def review_rows(outcomes: list, dominant: dict, decisions: dict = None) -> list:
    """The review file's rows: proposed spots, single-source and unusual values first, then the
    uncorroborated ones; each with what import_decision says of it."""
    decisions = decisions or {}
    proposed = [o for o in outcomes if o["status"] == "proposed"]
    frequency = Counter(o["value"] for o in proposed)
    rows = []
    for o in outcomes:
        if o["status"] not in ("proposed", "uncorroborated"):
            continue
        pages = o["pages"]
        first = stating_page(o) or pages[0]
        quote = next(s for v, s in first["statements"] if v == first["value"])
        others = [f"{p['domain']}: {s}" for p in pages if p is not first
                  for v, s in p["statements"] if v == p["value"]][:3]
        domains = sorted({p["domain"] for p in pages})
        entry = decisions.get(f"{o['name']}|{o['state']}")
        ok, why = import_decision(o, entry)
        flags = _flags(pages, dominant, o["ignored"])
        if not ok and why.startswith("combined") or (
                entry is not None and not ok and entry["decision"] == "accept"):
            flags = "; ".join(f for f in (flags, why) if f)
        rows.append({"name": o["name"], "state": o["state"], "status": o["status"],
                     "value": o["value"], "domains": " ".join(domains), "quote": quote,
                     "url": first["url"], "domain": first["domain"],
                     "flags": flags, "other_quotes": " || ".join(others),
                     "import": "yes" if ok else "no",
                     "decision": (f"{entry['decision']} {entry.get('value', '')}".strip()
                                  if entry else ""),
                     "decision_note": entry["note"] if entry else "",
                     "_key": (o["status"] != "proposed", len(domains) > 1,
                              not any(p["domain"] in dominant for p in pages),
                              frequency[o["value"]], o["state"], o["name"])})
    rows.sort(key=lambda r: r["_key"])
    for row in rows:
        del row["_key"]
    return rows


def conflict_rows(outcomes: list) -> list:
    rows = []
    for o in sorted((o for o in outcomes if o["status"] == "conflict"),
                    key=lambda o: (o["state"], o["name"])):
        for p in o["pages"]:
            note = ("this page says more than one stage" if p["conflict"]
                    else "never counts alone" if never_alone(p["url"]) else "")
            for v, s in p["statements"]:
                rows.append({"name": o["name"], "state": o["state"], "domain": p["domain"],
                             "value": v, "quote": s, "url": p["url"], "note": note})
    return rows


def write_csv(path: Path, columns: tuple, rows: list) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def run(results: dict) -> tuple:
    """(outcomes, stats) for every spot in the full run's results."""
    stats = Counter()
    outcomes = []
    for record in results["spots"].values():
        if not record.get("research_responses"):
            stats["spots with no saved pages"] += 1
            continue
        pages = spot_pages(record, stats)
        outcome = decide(pages)
        outcome.update(name=record["identity"]["name"], state=record["identity"]["region"],
                       lat=record.get("lat"), lng=record.get("lng"), all_pages=pages)
        outcomes.append(outcome)
    return outcomes, stats


def read_review(path: Path) -> dict:
    """{(name, state): row} from an earlier review file, any column layout; {} if there is none."""
    if not path.exists():
        return {}
    with open(path, encoding="utf-8", newline="") as fh:
        return {(r.get("name"), r.get("state")): r for r in csv.DictReader(fh)}


def review_changes(before: dict, rows: list) -> list:
    """What changed between an earlier review file and the new rows, one line per spot, in the
    new file's order and then the rows that are gone."""
    after = {(r["name"], r["state"]): r for r in rows}
    out = []
    for key, row in after.items():
        old = before.get(key)
        label = f"{key[0]} ({key[1]})"
        if old is None:
            out.append(f"{label}: new row, {row['status']} {row['value']}")
            continue
        was, now = f"{old.get('status')} {old.get('value')}", f"{row['status']} {row['value']}"
        if was != now:
            out.append(f"{label}: {was} -> {now}")
        elif (old.get("quote"), old.get("url")) != (row["quote"], row["url"]):
            out.append(f"{label}: {now}, same value from a different quote")
    for key, old in before.items():
        if key not in after:
            out.append(f"{key[0]} ({key[1]}): row gone (was {old.get('status')} {old.get('value')})")
    return out


def report(outcomes: list, stats: Counter, dominant: dict, review: Path, conflicts: Path) -> str:
    status = Counter(o["status"] for o in outcomes)
    proposed = Counter(o["value"] for o in outcomes if o["status"] == "proposed")
    single = sum(1 for o in outcomes if o["status"] == "proposed"
                 and len({p["domain"] for p in o["pages"]}) == 1)
    lines = [f"spots with saved pages: {len(outcomes)}",
             "by outcome: " + ", ".join(f"{k} {status[k]}" for k in
                                        ("proposed", "uncorroborated", "conflict", "none")),
             "proposed values: " + ", ".join(f"{v} {proposed[v]}" for v in VALUES),
             f"proposed from a single domain: {single}",
             "flagged domains (mostly one value): "
             + ("; ".join(f"{d} {f}" for d, f in sorted(dominant.items())) or "none")]
    lines += [f"{k}: {v}" for k, v in sorted(stats.items())]
    lines += [f"review file: {review}", f"conflicts file: {conflicts}"]
    return "\n".join(lines)


def main(argv=None) -> int:
    from .import_break_type_research import load_full
    p = argparse.ArgumentParser(prog="python3 -m pipeline.tide_free_pass",
                                description=__doc__.splitlines()[0])
    p.add_argument("--results", type=Path, default=DEFAULT_FULL_OUTPUT,
                   help="the full run's results (default: %(default)s)")
    p.add_argument("--review", type=Path, default=REVIEW_OUTPUT)
    p.add_argument("--conflicts", type=Path, default=CONFLICTS_OUTPUT)
    p.add_argument("--decisions", type=Path, default=DECISIONS_FILE,
                   help="a person's decisions on the review rows (default: %(default)s)")
    p.add_argument("--roster", type=Path, default=None,
                   help="the roster the decisions name spots in (default: the committed one)")
    args = p.parse_args(argv)
    protected = {Path(DEFAULT_ENRICHED_OUTPUT).resolve(), args.results.resolve()}
    for out in (args.review, args.conflicts):
        if out.resolve() in protected or out.suffix != ".csv":
            raise SystemExit(f"refusing to write {out}: the outputs are .csv review files")
    if args.review.resolve() == args.conflicts.resolve():
        raise SystemExit("--review and --conflicts must be different files")
    import json
    roster = json.loads(Path(DEFAULT_ENRICHED_OUTPUT if args.roster is None else args.roster)
                        .read_text(encoding="utf-8"))
    decisions = load_decisions(args.decisions, roster)
    outcomes, stats = run(load_full(args.results))
    dominant = dominant_domains(outcomes)
    rows = review_rows(outcomes, dominant, decisions)
    before = read_review(args.review)
    if args.review.exists():
        backup = args.review.with_name(args.review.stem + ".previous.csv")
        backup.write_bytes(args.review.read_bytes())
    write_csv(args.review, REVIEW_COLUMNS, rows)
    write_csv(args.conflicts, CONFLICT_COLUMNS, conflict_rows(outcomes))
    print(report(outcomes, stats, dominant, args.review, args.conflicts))
    print(decisions_report(outcomes, rows, decisions, before))
    return 0


def decisions_report(outcomes: list, rows: list, decisions: dict, before: dict) -> str:
    imports = Counter(r["import"] for r in rows)
    lines = ["", f"decisions: {len(decisions)} ("
             + ", ".join(f"{d} {sum(1 for e in decisions.values() if e['decision'] == d)}"
                         for d in DECISIONS) + f"); rows the import would write: {imports['yes']}"]
    seen = {f"{r['name']}|{r['state']}" for r in rows}
    unmatched = sorted(k for k in decisions if k not in seen)
    if unmatched:
        lines.append("decisions with no row in the review file: " + "; ".join(unmatched))
    held = [r for r in rows if r["decision"].startswith("accept") and r["import"] == "no"]
    for r in held:
        lines.append(f"accept no longer matches: {r['name']} ({r['state']}): {r['flags']}")
    typed = sorted(f"{k[0]} ({k[1]}): {r.get('decision')!r}" for k, r in before.items()
                   if (r.get("decision") or "").strip()
                   and f"{k[0]}|{k[1]}" not in decisions)
    if typed:
        lines.append("typed into the old review file's decision column but not in the decisions "
                     "file (add them there, or they are lost): " + "; ".join(typed))
    if before:
        changes = review_changes(before, rows)
        lines += ["", f"rows changed since the last review file: {len(changes)}"]
        lines += [f"  {line}" for line in changes]
    else:
        lines += ["", "rows changed since the last review file: no earlier review file"]
    lines += ["", "Dry run: nothing imported. Review the rows before anything is written."]
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
