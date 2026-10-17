"""Put the break-type research into the roster: each spot's break_type and bottom, with
where each came from.

Reads the full run's results (pipeline/research_break_type.py --full) and the memory-only
answers (--memory-only), and writes break_type and bottom into pipeline/spots_enriched.json.
The roster, not the database: db_import uploads the roster on every pipeline run, so a value
written straight into the database would be overwritten by the next run.

FOR EACH RATED SPOT AND EACH OF THE TWO VALUES, the first of these that applies:
  1. research gave a checked value (status 'researched': a URL a tool returned, a quote on
     that page, a location check): it is written with source 'researched', the page it
     cites and its quote, over any value config's ladder ranks below it
     (config.break_type_may_overwrite / config.bottom_may_overwrite);
  2. otherwise the memory answer, as 'model_recall' with no page, but only under the memory
     rules (MEMORY_MAY_REPLACE, memory_rule): into an empty value, or over the break type's
     unverified default 'beach' (stamped 'unattributed', no verification_confidence);
     never over a verified label or any other held value, and never an answer marked low
     confidence. The bottom has no default, so memory only fills an empty one;
  3. otherwise nothing is written: a value research and memory both left unknown stays as
     it is.
THEN THE REVIEWED VALUES (REVIEWED_FILE, committed): each listed spot's break type, bottom,
or both, whichever the entry names, is written as 'reviewed' (rank 6) over whatever research
or memory would leave, through the same may-overwrite rule as the value's other writes.
Nothing ranked below it overwrites it, in this run or a later one. A value the entry does not
name is left as research and memory set it. A listed spot that is not exactly one rated spot
in the roster, or an entry that names neither value, stops the import, dry run included,
before anything is written.
Every write goes through config.break_type_fields / config.bottom_fields: the value, its
source, its confidence and its citation in one statement, refused there if migration 020
or 021 would refuse it. A spot whose coordinates have moved more than MOVED_KM since the
answers were given is left as it is: the answers were about the spot where it was. The dry
run counts what each rule wrote and skipped (RULES).

IT ALSO WRITES A SUMMARY (SUMMARY_OUTPUT), committed beside the roster, with no page text:
each rated spot's research and memory answers, their sources and quotes, what the roster
now holds, any reviewed value with its note, and both sand-bottom flags. The beach-break
ceiling to come needs to know where memory and research both say sand. (The flags are
research's and memory's; a reviewed bottom changes the roster's bottom, not them.)

WHAT IT CANNOT CHANGE. It writes the roster's break_type and bottom keys and the summary,
nothing else: no star rating, no forecast row, no database. Neither value is read by the
ratings (pipeline/interpret.py) or by anything the forecast run computes; see the pull
request for where break_type is read.

RUN on the Mac, from the repo root, after migration 021 is applied:
    python3 -m pipeline.import_break_type_research --dry-run   # what would change; writes nothing
    python3 -m pipeline.import_break_type_research             # writes the roster and the summary
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import research_break_type as rb
from .config import (
    BOTTOM_FIELDS,
    BOTTOM_VALUES,
    BREAK_TYPE_FIELDS,
    BREAK_TYPE_VALUES,
    DEFAULT_ENRICHED_OUTPUT,
    PIPELINE_DIR,
    bottom_fields,
    bottom_may_overwrite,
    break_type_fields,
    break_type_may_overwrite,
    break_type_quote,
)
from .geo import haversine_m

# Named so .gitignore's pipeline/data/break_type_*.json, which keeps the results files (and
# their page text) out of git, does not catch it.
SUMMARY_OUTPUT = PIPELINE_DIR / "data" / "spot_break_research_summary.json"
SUMMARY_SCHEMA = 1

RESEARCHED, MEMORY, REVIEWED = "researched", "model_recall", "reviewed"

# Values a person reviewed — a break type, a bottom, or both — by exact roster name and state,
# with a short note. Applied after research and memory, as 'reviewed' (rank 6): nothing ranked
# below it overwrites it, here or in any later run. The note stays in this file and the
# summary; *_evidence holds quotes from a cited page, and a reviewer's note is not one.
REVIEWED_FILE = PIPELINE_DIR / "data" / "break_type_reviewed.json"
NOTE_MAX_CHARS = 300
# The values a reviewed entry may name, each against its own list, and the keys an entry may
# have at all: a misspelt key ("botom") would otherwise be ignored without a word.
REVIEWED_VALUES = {"break_type": BREAK_TYPE_VALUES, "bottom": BOTTOM_VALUES}
REVIEWED_KEYS = frozenset({"name", "state", "note", *REVIEWED_VALUES})
# The two values, each with its one builder, its one overwrite rule and its five keys.
VALUES = {
    "break_type": (break_type_fields, break_type_may_overwrite, BREAK_TYPE_FIELDS),
    "bottom": (bottom_fields, bottom_may_overwrite, BOTTOM_FIELDS),
}
_NAMES = {"break_type": "break type", "bottom": "bottom"}

# What happened to one value at one spot.
WRITE = "write"            # written: a new value, or the same value from a better-known source
SAME = "same"              # the roster already holds exactly this
KEPT = "kept"              # a source ranked above the incoming one holds it
SKIPPED = "skipped"        # a memory answer the memory rules do not let in
LEFT = "left"              # research and memory gave no value: left as it is
MOVED = "moved"            # the spot has moved since the answers were given: left as it is

# The rule behind each outcome, in the order the dry run reports them. A memory answer that
# more than one rule stops is counted under the first of them here.
RULES = (
    ("reviewed_written", "reviewed: written"),
    ("reviewed_same", "reviewed: already the same"),
    ("research_written", "research (cited): written"),
    ("research_same", "research: already the same"),
    ("research_kept", "research: skipped, a higher-ranked source holds it"),
    ("memory_filled_empty", "memory: written where the value was empty"),
    ("memory_replaced_default", "memory: written over the unverified default 'beach'"),
    ("memory_same", "memory: already the same"),
    ("memory_skipped_verified", "memory: skipped, a verified label"),
    ("memory_skipped_held", "memory: skipped, the spot holds another value"),
    ("memory_skipped_low", "memory: skipped, low confidence"),
    ("left", "left: neither research nor memory gave a value"),
    ("moved", "left: the spot has moved since the answers"),
)

# THE MEMORY RULES. A memory answer ('model_recall', no citation) may only fill a value that
# is empty, or the break type's unverified default: 'beach' stamped 'unattributed' on a spot
# with no verification_confidence (what enrich's removed algorithm wrote everywhere). Never a
# verified label, never any other held value, and never an answer marked low confidence. The
# bottom has no default: memory only fills an empty one. Research, which cites its page, may
# overwrite anything ranked below it (config's ladder).
MEMORY_MAY_REPLACE = {"break_type": "beach", "bottom": None}

# A spot whose roster coordinates are further than this from the ones the answers were
# given for is left alone.
MOVED_KM = 1.0


# --- reading the results ------------------------------------------------------------------

def _read(path: Path, kind, schema: int, what: str) -> dict:
    if not path.exists():
        raise SystemExit(f"no {what} at {path}")
    results = json.loads(path.read_text(encoding="utf-8"))
    if results.get("kind") != kind or results.get("schema") != schema:
        raise SystemExit(f"{path} is not a {what} of schema {schema}")
    return results


def load_full(path: Path) -> dict:
    return _read(path, "full", rb.FULL_SCHEMA_VERSION, "full-run results file")


def load_memory(path: Path) -> dict:
    return _read(path, "memory_only", rb.MEMORY_SCHEMA_VERSION, "memory-only results file")


def research_claim(record, field: str):
    """(value, url, quote) where the research checked a value, else None."""
    if not record or not record.get("done") or not record.get("research"):
        return None
    claim = record["research"][field]
    if claim.get("status") != "researched":
        return None
    return claim["value"], claim["source_url"], break_type_quote(claim.get("quote"))


def memory_claim(record, field: str):
    """(value, confidence) of the memory answer, or None where it gave none or 'unknown'."""
    if not record or not record.get("done") or not record.get("memory"):
        return None
    answer = record["memory"][field]
    value = answer["value"]
    return None if value in (None, "unknown") else (value, answer.get("confidence"))


def _verified(spot: dict) -> bool:
    return spot.get("verification_confidence") is not None


def memory_rule(spot: dict, field: str, confidence) -> str:
    """Which memory rule decides a memory answer for *field* at *spot*: the rule that
    stops it, or the one that lets it in."""
    held = spot.get(field)
    # Checked first, so the default below is always an unverified one.
    if held is not None and _verified(spot):
        return "memory_skipped_verified"
    default = (held is not None and held == MEMORY_MAY_REPLACE[field]
               and spot.get(f"{field}_source") == "unattributed")
    if held is not None and not default:
        return "memory_skipped_held"
    if confidence == "low":
        return "memory_skipped_low"
    return "memory_replaced_default" if default else "memory_filled_empty"


def _moved_km(spot: dict, record) -> float:
    """How far the roster spot is from where the answer was given, in km (0 with no answer)."""
    if not record:
        return 0.0
    return haversine_m(float(spot["lat"]), float(spot["lng"]),
                       float(record["lat"]), float(record["lng"])) / 1000.0


# --- the plan -------------------------------------------------------------------------------

def plan_value(spot: dict, field: str, researched, memory) -> dict:
    """What happens to one value at one spot: {'action', 'rule', 'source', 'before',
    'after', 'fields'} — 'fields' the five keys to write, for WRITE only."""
    build, may_overwrite, keys = VALUES[field]
    before = spot.get(field)

    def outcome(action, rule, source=None, fields=None):
        return {"action": action, "rule": rule, "source": source, "before": before,
                "after": fields[field] if action == WRITE else before, "fields": fields}

    if max(_moved_km(spot, researched), _moved_km(spot, memory)) > MOVED_KM:
        return outcome(MOVED, "moved")
    found = research_claim(researched, field)
    if found is not None:
        value, url, quote = found
        fields = build(value, RESEARCHED, url=url, evidence=quote)
        if not may_overwrite(spot.get(f"{field}_source"), RESEARCHED):
            return outcome(KEPT, "research_kept", RESEARCHED)
        if all(spot.get(key) == fields[key] for key in keys):
            return outcome(SAME, "research_same", RESEARCHED)
        return outcome(WRITE, "research_written", RESEARCHED, fields)
    answer = memory_claim(memory, field)
    if answer is None:
        return outcome(LEFT, "left")
    value, confidence = answer
    fields = build(value, MEMORY)
    if all(spot.get(key) == fields[key] for key in keys):
        return outcome(SAME, "memory_same", MEMORY)
    rule = memory_rule(spot, field, confidence)
    if rule.startswith("memory_skipped"):
        return outcome(SKIPPED, rule, MEMORY)
    # The memory rules only let memory replace an empty value or an 'unattributed' one,
    # both ranked below model_recall, so the ladder always agrees; it is asked all the same.
    if not may_overwrite(spot.get(f"{field}_source"), MEMORY):
        return outcome(SKIPPED, "memory_skipped_held", MEMORY)
    return outcome(WRITE, rule, MEMORY, fields)


def load_reviewed(path: Path, roster: list) -> dict:
    """{spot key: entry} from the reviewed file. Fails loudly, naming every entry, if any is
    not exactly one rated spot in the roster, is listed twice, names neither a break type nor
    a bottom, has a value outside its list or a key the file does not use, or has no note."""
    if not path.exists():
        raise SystemExit(f"no reviewed values at {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    entries = doc.get("spots") if isinstance(doc, dict) else None
    if not isinstance(entries, list):
        raise SystemExit(f"{path} has no list of spots")
    rated = {rb.memory_key(spot) for spot in rb.memory_spots(roster)}
    out, problems = {}, []
    for entry in entries:
        entry = entry if isinstance(entry, dict) else {}
        key = f"{entry.get('name')}|{entry.get('state')}"
        label = f"{entry.get('name')} ({entry.get('state')})"
        note = entry.get("note")
        named = [field for field in REVIEWED_VALUES if field in entry]
        bad = [f"{field} {entry[field]!r} is not one of {REVIEWED_VALUES[field]}"
               for field in named if entry[field] not in REVIEWED_VALUES[field]]
        extra = sorted(set(entry) - REVIEWED_KEYS)
        if key in out:
            problems.append(f"{label}: listed twice")
        elif key not in rated:
            problems.append(f"{label}: not a rated spot in {DEFAULT_ENRICHED_OUTPUT.name} "
                            "(the name and state must match exactly)")
        elif extra:
            problems.append(f"{label}: unknown key{'s' if len(extra) > 1 else ''} "
                            + ", ".join(repr(k) for k in extra))
        elif not named:
            problems.append(f"{label}: names neither a break_type nor a bottom")
        elif bad:
            problems.append(f"{label}: " + "; ".join(bad))
        elif not (isinstance(note, str) and 1 <= len(note.strip()) <= NOTE_MAX_CHARS):
            problems.append(f"{label}: needs a note of 1-{NOTE_MAX_CHARS} characters")
        else:
            out[key] = entry
    if problems:
        raise SystemExit(f"{path}: {len(problems)} of its {len(entries)} entries cannot be "
                         "applied; nothing was written:\n  " + "\n  ".join(problems))
    return out


def plan_reviewed(spot: dict, entry: dict, field: str, under: dict) -> dict:
    """A reviewed *field* (break_type or bottom), applied after research and memory (*under*:
    what they would do), through that value's own builder and may-overwrite rule. Keeps
    *under* for the report."""
    build, may_overwrite, keys = VALUES[field]
    fields = build(entry[field], REVIEWED)
    held = (under["fields"][f"{field}_source"] if under["action"] == WRITE
            else spot.get(f"{field}_source"))
    before = spot.get(field)
    if not may_overwrite(held, REVIEWED):
        return under   # cannot happen: reviewed tops the ladder; asked all the same
    common = {"source": REVIEWED, "before": before, "under": under, "note": entry["note"]}
    if all(spot.get(key) == fields[key] for key in keys):
        return {**common, "action": SAME, "rule": "reviewed_same", "after": before,
                "fields": None}
    return {**common, "action": WRITE, "rule": "reviewed_written",
            "after": entry[field], "fields": fields}


def plan(roster: list, full: dict, memory: dict, reviewed: dict = None) -> list:
    """[(roster spot, its full-run record or None, its memory record or None, settled?,
    {field: plan_value})] for every rated spot, in roster order; a reviewed value
    (load_reviewed) replaces what research and memory would do with that value."""
    out = []
    for spot in rb.memory_spots(roster):
        key = rb.memory_key(spot)
        researched = full["spots"].get(key)
        remembered = memory["spots"].get(key)
        changes = {field: plan_value(spot, field, researched, remembered) for field in VALUES}
        entry = (reviewed or {}).get(key)
        for field in VALUES:
            if entry is not None and field in entry:
                changes[field] = plan_reviewed(spot, entry, field, changes[field])
        out.append((spot, researched, remembered, key in full.get("settled", {}), changes))
    return out


# --- writing ----------------------------------------------------------------------------------

def _placed(spot: dict, fields: dict, after: tuple) -> dict:
    """*spot* with *fields* set: in place where its keys are already there, otherwise
    inserted after the first key of *after* that the spot has (else at the end)."""
    if all(key in spot for key in fields):
        return {**spot, **fields}
    anchor = next((key for key in after if key in spot), None)
    out = {}
    for key, value in spot.items():
        if key not in fields:
            out[key] = value
        if key == anchor:
            out.update(fields)
    if anchor is None:
        out.update(fields)
    return out


# Where a spot that has never had the keys gets them: break_type after region_hint, as on
# the other spots; bottom after break_type's keys.
_AFTER = {"break_type": ("region_hint",),
          "bottom": ("break_type_evidence", "region_hint")}


def apply(roster: list, planned: list) -> list:
    """The roster with every WRITE made. Spots without one are returned unchanged."""
    writes = {id(spot): changes for spot, _, _, _, changes in planned}
    out = []
    for spot in roster:
        for field, change in (writes.get(id(spot)) or {}).items():
            if change["action"] == WRITE:
                spot = _placed(spot, change["fields"], _AFTER[field])
        out.append(spot)
    return out


def roster_text(roster: list) -> str:
    """The roster as the other writers write it (enrich, verify_spots, apply_nwps)."""
    return json.dumps(roster, indent=2, ensure_ascii=False) + "\n"


# --- the summary --------------------------------------------------------------------------------

def _research_part(record):
    if not record or not record.get("done") or not record.get("research"):
        return None
    research = record["research"]
    part = {}
    for field in VALUES:
        claim = research[field]
        part[field] = {"value": claim["value"], "status": claim["status"],
                       "source_url": claim.get("source_url"), "quote": claim.get("quote"),
                       "reason": claim.get("reason")}
    part["sand_bottom"] = research["sand_bottom"]
    return part


def _memory_part(record):
    if not record or not record.get("done") or not record.get("memory"):
        return None
    memory = record["memory"]
    part = {field: {"value": memory[field]["value"],
                    "confidence": memory[field].get("confidence")} for field in VALUES}
    part["sand_bottom"] = memory["sand_bottom"]
    return part


def _reviewed_part(entry):
    """The values a reviewed entry names, in VALUES order, then its note; None with no entry."""
    if entry is None:
        return None
    return {**{field: entry[field] for field in VALUES if field in entry}, "note": entry["note"]}


def summary(planned: list, roster_after: list, full_name: str, memory_name: str,
            reviewed: dict = None) -> dict:
    """Each rated spot's answers and what the roster now holds; no page text."""
    by_key = {rb.memory_key(spot): spot for spot in rb.memory_spots(roster_after)}
    spots = []
    for spot, researched, remembered, settled, _ in planned:
        key = rb.memory_key(spot)
        now = by_key[key]
        research, memory = _research_part(researched), _memory_part(remembered)
        research_sand = research["sand_bottom"] if research else None
        memory_sand = memory["sand_bottom"] if memory else None
        spots.append({
            "name": spot["name"], "state": spot["region_hint"],
            "settled_by_geography": settled,
            "research": research, "memory": memory,
            "reviewed": _reviewed_part((reviewed or {}).get(key)),
            "roster": {field: {"value": now.get(field), "source": now.get(f"{field}_source")}
                       for field in VALUES},
            "sand_bottom": {"research": research_sand, "memory": memory_sand,
                            "both_say_sand": research_sand == "yes" and memory_sand == "yes"},
        })
    return {"schema": SUMMARY_SCHEMA,
            "what": ("Each rated spot's break type and bottom: the research answer (checked "
                     "against the page it cites), the memory answer, a reviewed break type "
                     "or bottom where a person set one, what the roster holds, and both "
                     "sand-bottom flags. No page text."),
            "from": {"full_run": full_name, "memory": memory_name},
            "spots": spots}


def summary_text(data: dict) -> str:
    """The summary with one spot per line, so a change to one spot is one line of diff."""
    head = {key: value for key, value in data.items() if key != "spots"}
    lines = [json.dumps(spot, ensure_ascii=False, separators=(",", ":"))
             for spot in data["spots"]]
    return ("{\n" + "".join(f"  {json.dumps(key)}: {json.dumps(value, ensure_ascii=False)},\n"
                            for key, value in head.items())
            + '  "spots": [\n' + ",\n".join("    " + line for line in lines) + "\n  ]\n}\n")


# --- the report ---------------------------------------------------------------------------------

def _row(*cells) -> str:
    return "| " + " | ".join(" ".join(str(c).replace("|", "/").split()) for c in cells) + " |"


def _label(spot: dict) -> str:
    return f"{spot['name']} ({spot['region_hint']})"


def _review_table(what: str, reviews: list) -> list:
    """The report's table of one value's reviews, or nothing when there are none."""
    if not reviews:
        return []
    out = ["", f"Reviewed {what} ({len(reviews)}), applied after research and memory:", "",
           _row("Spot", "Now", "Research or memory would write", "Reviewed", "Note"),
           "|---|---|---|---|---|"]
    for spot, c in reviews:
        under = c["under"]
        would = (f"{under['after']} ({under['source']})" if under["action"] == WRITE
                 else "nothing new")
        out.append(_row(_label(spot), c["before"] or "(none)", would, c["after"]
                        if c["action"] == WRITE else f"{c['after']} (already)", c["note"]))
    return out


def render_report(planned: list, full: dict, memory: dict, wrote: bool) -> str:
    researched = sum(1 for _, record, _, _, _ in planned if record and record.get("done"))
    answered = sum(1 for _, _, record, _, _ in planned if record and record.get("done"))
    settled = sum(1 for *_, s, _ in planned if s)
    out = [f"{len(planned)} rated spots in the roster. The full run researched {researched} "
           f"of them ({settled} more settled by geography); the memory run answered "
           f"{answered}."]
    outside = sorted(set(full["spots"]) - {rb.memory_key(s) for s, *_ in planned})
    if outside:
        out.append(f"In the full run but not a rated spot in the roster, so not used: "
                   f"{', '.join(outside)}.")
    out += ["", "What each rule wrote and skipped (a memory answer stopped by more than one "
                "rule is counted under the first):", "",
            _row("Rule", "break type", "bottom"), "|---|---|---|"]
    for rule, label in RULES:
        out.append(_row(label, *(sum(1 for *_, c in planned if c[field]["rule"] == rule)
                                 for field in VALUES)))
    out.append(_row("total", *(len(planned) for _ in VALUES)))
    for field in VALUES:
        levels = [(record["memory"][field].get("confidence") or "none")
                  for _, _, record, _, changes in planned
                  if changes[field]["action"] == WRITE and changes[field]["source"] == MEMORY]
        if levels:
            out.append(f"Memory answers written for the {_NAMES[field]}, by their confidence: "
                       + ", ".join(f"{levels.count(level)} {level}"
                                   for level in ("high", "medium", "low", "none")) + ".")

    reviews = [(spot, changes["break_type"]) for spot, *_, changes in planned
               if changes["break_type"]["source"] == REVIEWED]
    out += _review_table("break types", reviews)
    out += _review_table("bottoms", [(spot, changes["bottom"]) for spot, *_, changes in planned
                                     if changes["bottom"]["source"] == REVIEWED])

    changed = [(spot, changes["break_type"]) for spot, _, _, _, changes in planned
               if changes["break_type"]["action"] == WRITE
               and changes["break_type"]["before"] != changes["break_type"]["after"]]
    # A verified label is a break type a verified spot holds: filling an empty one changes
    # no label.
    relabelled = [(spot, c) for spot, c in changed if _verified(spot) and c["before"] is not None]
    by_research = [spot for spot, c in relabelled if c["source"] == RESEARCHED]
    by_memory = [spot for spot, c in relabelled if c["source"] == MEMORY]
    by_review = [spot for spot, c in relabelled if c["source"] == REVIEWED]
    instead = [spot for spot, c in reviews if _verified(spot)
               and c["under"]["action"] == WRITE and c["under"]["source"] == RESEARCHED
               and c["under"]["before"] != c["under"]["after"]]
    out += ["", f"Spots whose break type changes ({len(changed)}):", "",
            _row("Spot", "Now", "Becomes", "From", "Flag"), "|---|---|---|---|---|"]
    for spot, c in changed:
        now = (f"{c['before']} ({spot.get('break_type_source')}"
               + (", verified" if _verified(spot) else "") + ")" if c["before"] else "(none)")
        flag = ""
        if _verified(spot) and c["before"] is not None:
            flag = {RESEARCHED: "VERIFIED LABEL CHANGED BY RESEARCH",
                    MEMORY: "verified label changed by memory",
                    REVIEWED: "verified label changed by review"}[c["source"]]
        out.append(_row(_label(spot), now, c["after"], c["source"], flag))
    out += ["", f"Verified labels that research changes: {len(by_research)}.",
            f"Verified labels research would change, where a review decides instead: "
            f"{len(instead)}.",
            f"Verified labels that a review changes: {len(by_review)}.",
            f"Verified labels that memory changes (the memory rules allow none): "
            f"{len(by_memory)}.",
            f"Spots that had no break type and get one: "
            f"{sum(1 for _, c in changed if c['before'] is None)}."]

    bottoms = [changes["bottom"] for *_, changes in planned if changes["bottom"]["action"] == WRITE]
    out += ["", "Bottoms written, by value: " + ", ".join(
        f"{value} {sum(1 for c in bottoms if c['after'] == value)}"
        for value in ("sand", "rock", "coral", "cobble", "mixed", "unknown")) + "."]
    both = sum(1 for _, r, m, _, _ in planned
               if (_research_part(r) or {}).get("sand_bottom") == "yes"
               and (_memory_part(m) or {}).get("sand_bottom") == "yes")
    out.append(f"Research and memory both say sand at {both} spots.")
    moved = [spot for spot, *_, changes in planned if changes["break_type"]["action"] == MOVED]
    if moved:
        out.append(f"Left as they are, moved more than {MOVED_KM:g} km since the answers: "
                   + "; ".join(_label(spot) for spot in moved) + ".")
    out += ["", "Wrote the roster and the summary." if wrote
            else "Dry run: nothing written."]
    return "\n".join(out)


# --- entry point ------------------------------------------------------------------------------

def _parse_args(argv):
    parser = argparse.ArgumentParser(
        prog="python3 -m pipeline.import_break_type_research",
        description="Write the break-type research into the roster (spots_enriched.json) and "
                    "a committed summary. Never touches the database.")
    parser.add_argument("--full", type=Path, default=rb.DEFAULT_FULL_OUTPUT,
                        help="the full run's results (default: %(default)s)")
    parser.add_argument("--memory", type=Path, default=rb.DEFAULT_MEMORY_OUTPUT,
                        help="the memory-only results (default: %(default)s)")
    parser.add_argument("--roster", type=Path, default=DEFAULT_ENRICHED_OUTPUT,
                        help="the roster to update (default: %(default)s)")
    parser.add_argument("--reviewed", type=Path, default=REVIEWED_FILE,
                        help="the reviewed break types and bottoms (default: %(default)s)")
    parser.add_argument("--summary", type=Path, default=SUMMARY_OUTPUT,
                        help="the summary to write (default: %(default)s)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change; write nothing")
    return parser.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    full, memory = load_full(args.full), load_memory(args.memory)
    roster = json.loads(args.roster.read_text(encoding="utf-8"))
    if args.summary.resolve() in (args.roster.resolve(), args.full.resolve(),
                                  args.memory.resolve(), args.reviewed.resolve()):
        raise SystemExit(f"refusing to write the summary over {args.summary}")
    reviewed = load_reviewed(args.reviewed, roster)
    planned = plan(roster, full, memory, reviewed)
    if not args.dry_run:
        after = apply(roster, planned)
        args.roster.write_text(roster_text(after), encoding="utf-8")
        args.summary.write_text(summary_text(summary(planned, after, args.full.name,
                                                     args.memory.name, reviewed)),
                                encoding="utf-8")
    print(render_report(planned, full, memory, wrote=not args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
