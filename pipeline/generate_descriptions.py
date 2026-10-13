"""Write short factual spot descriptions from a template.

Walks every spot in spots_enriched.json, renders the payload pipeline/descriptions.py builds for it
(only facts the roster holds from a source that can be stated plainly) through that module's template,
and records the result in pipeline/data/spot_descriptions.json (the cache) and in the spots table's
description and description_signature columns. Not part of the scheduled pipeline: run it by hand.
No API key is needed: nothing here calls a model.

THE SIGNATURE. Each description is stamped with description_signature(payload): 'v2:' plus a hash of
TEMPLATE_VERSION and the exact payload. A spot is rewritten when its cached signature differs from the
current one — no description yet, one written before v2, or anything it states has changed. This script
is the ONLY writer of v2 signatures; db_import blanks a description whose stored v2 signature no longer
matches the roster, and leaves anything older alone.

MODES
  --dry-run   No writes. One block per spot: what would happen and why, what the payload states and
              leaves out, whether the current text contradicts the break type, the pattern, the
              output check, and the exact final text. Then totals.
  --sample    The ten spots in SAMPLE_SPOTS, written to a separate file (--sample-output) only —
              never to the cache, never to Supabase. Prints each with its payload, check and text.
  (default)   The full run. Resumable: the cache is saved every SAVE_EVERY descriptions and a rerun
              skips every spot whose cached signature is current. Each description is written to
              Supabase as soon as it is rendered; one that could not be written is kept in the cache
              marked "pushed": false and written on the next run. A final reconcile reads the table
              and re-writes any row whose signature or text differs from the cache — which also repairs
              a row that a forecast-pipeline run, writing back the row it read before this one changed
              it, put back while this was running. Only the rows this run touched are written; nothing
              else in the cache is pushed. Cache entries for spots no longer in the roster are pruned.

THE OUTPUT CHECK. Every rendered text goes through check_description before anything is saved or
written: one paragraph, no self-talk, nothing about absent fields, at most MAX_WORDS words, the spot's
name, and the break type stated exactly when the payload gives one. A text that fails keeps the spot's
existing description and gets no signature, and the spot is listed in the end-of-run report.

CLI:
    python -m pipeline.generate_descriptions --dry-run > dry_run.txt
    python -m pipeline.generate_descriptions --sample
    python -m pipeline.generate_descriptions [--limit N] [--no-db] [--no-cache] [-v]

Env:
    SUPABASE_URL, SUPABASE_SERVICE_KEY — required for the full run unless --no-db.
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
import unicodedata
from pathlib import Path

from .config import DEFAULT_ENRICHED_OUTPUT
from .db_import import _excluded_slugs, _fetch_existing_spots, _slugify, get_client
from .descriptions import (
    GATED_FIELDS,
    STATED_SOURCES,
    description_payload,
    description_signature,
    pattern_for,
    render,
    spot_fields,
)

log = logging.getLogger("pipeline.generate_descriptions")

OUTPUT_FILE = Path(__file__).parent / "data" / "spot_descriptions.json"
SAMPLE_FILE = Path(__file__).parent / "data" / "spot_descriptions_sample.json"
SAVE_EVERY = 10

# --sample: one spot for each case worth reading before a full run.
SAMPLE_SPOTS = (
    "Banzai Pipeline",          # reef over rock, researched
    "Waimea Bay",               # reef over a mixed bottom
    "Steamer Lane",             # point over rock, tide 'all'
    "Malibu Surfrider Beach",   # point over cobble
    "Matanzas Inlet",           # jetty, reviewed — its old text said rivermouth
    "Asbury Park",              # a New Jersey beach; its bottom is model_recall, so left out
    "Pensacola Beach",          # break type model_recall: left out
    "Agate Beach",              # no break type at all
    "Aviones",                  # a Puerto Rico reef over coral
    "Seaside Reef",             # tide preference missing
)

GENERATE, PUSH, KEEP = "generate", "push", "keep"


# --------------------------------------------------------------------------- #
# Which spots, and what to do with each                                        #
# --------------------------------------------------------------------------- #
def roster_spots(spots):
    """{slug: spot} for the spots db_import would upsert: named, with coordinates, not marked invalid,
    not excluded, first occurrence of each slug. Only these are generated: a description of a spot
    db_import skips would never reach the site."""
    excluded = _excluded_slugs()
    out = {}
    for s in spots:
        if not s.get("name") or s.get("lat") is None or s.get("lng") is None:
            continue
        if s.get("is_valid_surf_spot") is False:
            continue
        slug = _slugify(s["name"])
        if slug and slug not in out and slug not in excluded:
            out[slug] = s
    return out


def plan(roster, cache, force=False):
    """[(slug, spot, payload, signature, action, reason)] in roster order."""
    items = []
    for slug, spot in roster.items():
        payload = description_payload(spot_fields(spot))
        sig = description_signature(payload)
        entry = cache.get(slug)
        cached_sig = entry.get("signature") if isinstance(entry, dict) else None
        if force:
            action, reason = GENERATE, "--no-cache"
        elif entry is None:
            action, reason = GENERATE, "no description yet"
        elif cached_sig == sig:
            if entry.get("pushed") is False:
                action, reason = PUSH, "generated but not yet written to Supabase"
            else:
                action, reason = KEEP, "current"
        elif not (cached_sig or "").startswith("v2:"):
            action, reason = GENERATE, "written before signature v2"
        else:
            action, reason = GENERATE, "something it states has changed"
        items.append((slug, spot, payload, sig, action, reason))
    return items


def orphans(spots, cache):
    """Cache entries whose spot is no longer in the roster file at all. A spot that is still listed
    but skipped (marked invalid, or excluded) keeps its entry: it is neither regenerated nor pruned."""
    named = {_slugify(s["name"]) for s in spots if s.get("name")}
    return sorted(k for k in cache if k not in named)


def left_out(spot):
    """What the payload leaves out of break_type, bottom and tide_preference, and why, one phrase
    each."""
    out = []
    for field in GATED_FIELDS:
        value, source = spot.get(field), spot.get(f"{field}_source")
        if value in (None, ""):
            out.append(f"{field} not set")
        elif value == "unknown":
            out.append(f"{field} 'unknown'")
        elif source not in STATED_SOURCES:
            out.append(f"{field} {value} ({source or 'no source'})")
    return out


# The break type a description NAMES. Matched after the spot's own name is removed, so "Chuns Reef"
# is not read as saying reef. "reef" alone counts; "point" counts only as "point break" or a
# right/left-hand point, since "point" is so often a place name.
_TYPE_PATTERNS = {
    "beach": r"\bbeach[- ]?breaks?\b",
    "reef": r"\breef[- ]?breaks?\b|\breef\b",
    "point": r"\bpoint[- ]?breaks?\b|\b(?:right|left)[- ]hand(?:ed)? point\b",
    "jetty": r"\bjetty[- ]?breaks?\b",
    "rivermouth": r"\briver[- ]?mouth\b",
}


def stated_break_types(text, name):
    body = text or ""
    for n in sorted({name or "", (name or "").split(",")[0]}, key=len, reverse=True):
        if n:
            body = re.sub(re.escape(n), " ", body, flags=re.I)
    return {k for k, rx in _TYPE_PATTERNS.items() if re.search(rx, body, re.I)}


def contradiction(text, spot):
    """'says beach; break_type is now reef (researched)', or None."""
    said = stated_break_types(text, spot.get("name"))
    now = spot.get("break_type")
    if not said or not now or now in said:
        return None
    return (f"says {'/'.join(sorted(said))}; break_type is now {now} "
            f"({spot.get('break_type_source') or 'no source'})")


# --------------------------------------------------------------------------- #
# The output check                                                             #
# --------------------------------------------------------------------------- #
# WHY. A sample run, when a model still wrote these, saved this as Pensacola Beach's description: a
# first paragraph that named the missing fields, then "---", then "Wait, I need to revise — I was
# instructed not to mention missing fields. Here is the corrected version:" and a second description.
# The template cannot do that, but the check stays as a guard on what it writes: a text that fails it
# is not saved, the spot keeps its existing description and gets no new signature, and the end-of-run
# report names it.
MAX_WORDS = 80
_APOSTROPHES = str.maketrans({"’": "'", "ʻ": "'", "‘": "'", "`": "'"})
_SELF_TALK = re.compile(
    r"\b(?:wait|revis(?:e|ed|es|ing|ion)|correct(?:ed|ion)|instruct(?:ed|ion|ions)|"
    r"here is|here's|metadata|payload)\b", re.I)
_FIRST_PERSON = re.compile(r"\bI(?:'m|'ve|'ll|'d)?\b")          # case-sensitive: the pronoun
_ABSENT = re.compile(
    r"\bnot (?:specified|provided|given|available|listed|known|included|stated)\b"
    r"|\b(?:unspecified|unknown|missing)\b"
    r"|\bno (?:data|information|details)\b"
    r"|\bno\b[^.;]{0,60}?\b(?:is|are|was|were|has been|have been)\s+"
    r"(?:specified|provided|given|available|listed|known|included|stated)\b", re.I)
# A break type NAMED as such: what must be absent when break_type is not in the payload.
_NAMED_TYPES = {
    "beach": r"\bbeach[- ]?breaks?\b",
    "reef": r"\breef[- ]?breaks?\b",
    "point": r"\bpoint[- ]?breaks?\b",
    "jetty": r"\bjett(?:y|ies)\b",
    "rivermouth": r"\briver[- ]?mouths?\b",
}
# How a description may state the break type it was given. Wider than _NAMED_TYPES for reef and
# point ("breaks over a shallow reef", "a right-hand point"), since the name is stripped first.
_STATES_TYPE = dict(_NAMED_TYPES, reef=r"\breef\b", point=r"\bpoint\b")


def _fold(text):
    """Lower case, no accents, no apostrophes, other punctuation as spaces: 'Hoʻokipa' matches
    "Ho'okipa", 'Surf City NJ' matches 'Surf City, NJ', 'St Simons' matches 'St. Simons'."""
    t = unicodedata.normalize("NFKD", (text or "").translate(_APOSTROPHES))
    t = "".join(c for c in t if not unicodedata.combining(c)).replace("'", "")
    return " ".join(re.sub(r"[^\w\s]", " ", t.lower()).split())


def _without_name(text, name):
    """*text* with the spot's name removed, so 'Avalon Jetty' or 'Ali'i' are not read as words."""
    body = (text or "").translate(_APOSTROPHES)
    for n in sorted({name or "", (name or "").split(",")[0]}, key=len, reverse=True):
        if n:
            # whole words only: removing "Pools" must not eat into "whirlpools"
            body = re.sub(r"(?<!\w)" + re.escape(n.translate(_APOSTROPHES)) + r"(?!\w)", " ",
                          body, flags=re.I)
    return body


def check_description(text, payload):
    """Every way *text* fails as the description of *payload*, one short phrase each; [] passes."""
    problems = []
    name = payload.get("name") or ""
    if "\n" in text or "\r" in text:
        problems.append("more than one paragraph (line break)")
    if "---" in text:
        problems.append("contains '---'")
    body = _without_name(text, name)
    talk = sorted({m.group(0).lower() for m in _SELF_TALK.finditer(body)}
                  | {m.group(0) for m in _FIRST_PERSON.finditer(body)})
    if talk:
        problems.append("self-talk or meta text: " + ", ".join(repr(t) for t in talk))
    absent = [m.group(0) for m in _ABSENT.finditer(body)]
    if absent:
        problems.append("mentions an absent field: " + ", ".join(repr(a) for a in absent))
    words = len(text.split())
    if words > MAX_WORDS:
        problems.append(f"{words} words (at most {MAX_WORDS})")
    # whole words: the name "A" is not found inside "Wait"
    folded = f" {_fold(text)} "
    if not any(_fold(n) and f" {_fold(n)} " in folded for n in (name, name.split(",")[0])):
        problems.append("does not include the spot's name")
    given = payload.get("break_type")
    named = sorted(k for k, rx in _NAMED_TYPES.items() if re.search(rx, body, re.I))
    if given:
        if not re.search(_STATES_TYPE.get(given, re.escape(given)), body, re.I):
            problems.append(f"does not state the break type {given!r}")
        others = [k for k in named if k != given]
        if others:
            problems.append("names another break type: " + ", ".join(others))
    elif named:
        problems.append("names a break type it was not given: " + ", ".join(named))
    return problems


def check_line(problems):
    return "pass" if not problems else "FAIL — " + "; ".join(problems)


def payload_line(payload):
    """The payload as compact JSON, in the builder's key order."""
    return json.dumps(payload, ensure_ascii=False)


def written(slug, payload):
    """(text, problems): the template's text for this spot and how it fares in the output check."""
    text = render(payload, slug)
    return text, check_description(text, payload)


def dry_run_report(items, cache, orphan_slugs):
    lines = []
    counts = {GENERATE: {}, PUSH: 0, KEEP: 0}
    contradicting = failing = 0
    patterns = {}
    left = {field: {} for field in GATED_FIELDS}
    for slug, spot, payload, _sig, action, reason in items:
        entry = cache.get(slug) if isinstance(cache.get(slug), dict) else {}
        current = entry.get("description")
        wrong = contradiction(current, spot) if current else None
        contradicting += bool(wrong)
        for field in GATED_FIELDS:
            if field not in payload:
                value = spot.get(field)
                key = ("not set" if value in (None, "") else "'unknown'" if value == "unknown"
                       else spot.get(f"{field}_source") or "no source")
                left[field][key] = left[field].get(key, 0) + 1
        if action != GENERATE:
            counts[action] += 1
            lines.append(f"[{action}] {slug} — {reason}")
            lines.append(f"    final:    {current}")
            continue
        counts[GENERATE][reason] = counts[GENERATE].get(reason, 0) + 1
        text, problems = written(slug, payload)
        failing += bool(problems)
        n = pattern_for(slug)
        patterns[n] = patterns.get(n, 0) + 1
        lines.append(f"[{action}] {slug} — {reason}")
        stated = [f"{f} {payload[f]} ({spot.get(f + '_source')})"
                  for f in GATED_FIELDS if f in payload]
        lines.append(f"    states:   {', '.join(stated) or '—'}")
        lines.append(f"    left out: {', '.join(left_out(spot)) or '—'}")
        if wrong:
            lines.append(f"    current text contradicts: {wrong}")
        lines.append(f"    payload:  {payload_line(payload)}")
        lines.append(f"    pattern:  {n}")
        lines.append(f"    check:    {check_line(problems)}")
        if problems:
            lines.append(f"    final:    (unchanged) {current if current else '—'}")
        else:
            lines.append(f"    final:    {text}")
    n_gen = sum(counts[GENERATE].values())
    lines += [
        "",
        "TOTALS",
        f"  spots in the roster:              {len(items)}",
        f"  to write:                         {n_gen}",
        *[f"      {r}: {c}" for r, c in sorted(counts[GENERATE].items())],
        f"  to write to Supabase only:        {counts[PUSH]}",
        f"  unchanged:                        {counts[KEEP]}",
        f"  orphan cache entries to prune:    {len(orphan_slugs)}"
        + (f" ({', '.join(orphan_slugs)})" if orphan_slugs else ""),
        f"  current texts contradicting the break type: {contradicting}",
        f"  new texts failing the output check (would be rejected): {failing}",
        "  patterns among the texts to write: "
        + (", ".join(f"{k} x{v}" for k, v in sorted(patterns.items())) or "none"),
        "  break_type left out:      " + _counts(left["break_type"]),
        "  bottom left out:          " + _counts(left["bottom"]),
        "  tide_preference left out: " + _counts(left["tide_preference"]),
    ]
    return lines


def _counts(d):
    return ", ".join(f"{k} {v}" for k, v in sorted(d.items())) or "none"


# --------------------------------------------------------------------------- #
# Files and the table                                                          #
# --------------------------------------------------------------------------- #
def load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} is not valid JSON ({e}); fix or move it before running") from None
    return data if isinstance(data, dict) else {}


def save_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {k: data[k] for k in sorted(data)}  # stable key order so diffs read cleanly
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    tmp.replace(path)


def push(db, slug, entry) -> bool:
    """Write one description and its signature. False when the write failed or matched no row (a
    spot db_import has not created yet)."""
    try:
        res = (db.table("spots")
               .update({"description": entry["description"],
                        "description_signature": entry["signature"]})
               .eq("slug", slug).execute())
    except Exception:  # noqa: BLE001
        log.exception("%s: Supabase update failed", slug)
        return False
    if not getattr(res, "data", None):
        log.warning("%s: no spots row with this slug yet; kept for the next run", slug)
        return False
    return True


def reconcile(db, cache, roster) -> tuple[int, int]:
    """Re-write every row whose stored signature differs from its current cache entry. Returns
    (rows re-written, rows that still differ)."""
    rows = _fetch_existing_spots(db)
    fixed = failed = 0
    for slug, entry in sorted(cache.items()):
        if slug not in roster or not isinstance(entry, dict) or entry.get("pushed") is False:
            continue
        row = rows.get(slug)
        if row is None:
            continue
        if (row.get("description_signature") == entry.get("signature")
                and row.get("description") == entry.get("description")):
            continue
        if push(db, slug, entry):
            fixed += 1
        else:
            failed += 1
    return fixed, failed


# --------------------------------------------------------------------------- #
# Modes                                                                        #
# --------------------------------------------------------------------------- #
def run_sample(roster, out_path: Path) -> int:
    by_name = {s["name"]: (slug, s) for slug, s in roster.items()}
    missing = [n for n in SAMPLE_SPOTS if n not in by_name]
    if missing:
        log.error("sample spots not in the roster: %s", ", ".join(missing))
        return 2
    results, rejected = {}, []
    for name in SAMPLE_SPOTS:
        slug, spot = by_name[name]
        payload = description_payload(spot_fields(spot))
        text, problems = written(slug, payload)
        results[slug] = {"name": name, "payload": payload, "pattern": pattern_for(slug),
                         "check": check_line(problems),
                         "signature": None if problems else description_signature(payload),
                         "description": None if problems else text}
        print(f"== {name} ({slug})")
        print(f"   left out: {', '.join(left_out(spot)) or '—'}")
        print(f"   payload:  {payload_line(payload)}")
        print(f"   pattern {pattern_for(slug)}, check: {check_line(problems)}")
        print(f"   {text}")
        if problems:
            rejected.append(slug)
            print("   REJECTED: a full run would keep the existing description")
        print(flush=True)
    save_json(out_path, results)
    print(f"{len(results)} sample description(s) written to {out_path}; nothing else was written.")
    if rejected:
        print(f"rejected by the output check: {', '.join(rejected)}")
    return 0 if not rejected else 1


def run_full(spots, roster, cache_path: Path, db, limit=None, force=False) -> int:
    cache = load_cache(cache_path)
    gone = orphans(spots, cache)
    for slug in gone:
        del cache[slug]
    if gone:
        log.info("pruned %d cache entr%s for spots no longer in the roster: %s",
                 len(gone), "y" if len(gone) == 1 else "ies", ", ".join(gone))
        save_json(cache_path, cache)

    items = plan(roster, cache, force=force)
    written_n = pushed = kept_unpushed = 0
    rejected = []
    for slug, _spot, payload, sig, action, reason in items:
        if action == KEEP:
            continue
        if action == PUSH:
            if db is not None and push(db, slug, cache[slug]):
                cache[slug].pop("pushed", None)
                pushed += 1
                save_json(cache_path, cache)
            continue
        if limit is not None and written_n >= limit:
            break
        text, problems = written(slug, payload)
        if problems:
            # Keep whatever the spot has: no cache change, no write, no signature.
            rejected.append((slug, (text, problems)))
            log.warning("%s: rejected (%s)", slug, check_line(problems))
            continue
        entry = {"description": text, "signature": sig}
        written_n += 1
        if db is not None and push(db, slug, entry):
            pushed += 1
        else:
            entry["pushed"] = False
            kept_unpushed += 1
        cache[slug] = entry
        log.info("%s (%s): %s", slug, reason, text[:80] + ("…" if len(text) > 80 else ""))
        if written_n % SAVE_EVERY == 0:
            save_json(cache_path, cache)
    save_json(cache_path, cache)

    fixed = still = 0
    if db is not None:
        fixed, still = reconcile(db, cache, roster)
    log.info("written=%d to-supabase=%d kept-unwritten=%d rejected=%d reconciled=%d "
             "still-differ=%d; cache → %s", written_n, pushed, kept_unpushed, len(rejected), fixed,
             still, cache_path)
    print("\n".join(rejected_report(rejected)), flush=True)
    # Unwritten entries are expected under --no-db; with Supabase they mean a rerun is needed.
    ok = not rejected and still == 0 and (db is None or kept_unpushed == 0)
    return 0 if ok else 1


def rejected_report(rejected):
    """The end-of-run list of spots whose text failed the output check."""
    if not rejected:
        return ["Output check: no spot rejected."]
    lines = [f"Output check: {len(rejected)} spot(s) rejected — existing description kept, no "
             "signature written:"]
    for slug, (text, problems) in rejected:
        lines.append(f"  {slug} — {'; '.join(problems)}")
        lines.append(f"    text: {' '.join(text.split())[:160]}")
    return lines


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="No writes: the per-spot report with each final text, then totals.")
    mode.add_argument("--sample", action="store_true",
                      help="Write the ten SAMPLE_SPOTS into --sample-output only.")
    p.add_argument("--input", type=Path, default=DEFAULT_ENRICHED_OUTPUT, help="spots_enriched.json")
    p.add_argument("--output", type=Path, default=OUTPUT_FILE, help="the description cache")
    p.add_argument("--sample-output", type=Path, default=SAMPLE_FILE)
    p.add_argument("--limit", type=int, default=None, help="Cap on descriptions written this run.")
    p.add_argument("--no-cache", action="store_true",
                   help="Rewrite every spot, whatever its cached signature.")
    p.add_argument("--no-db", action="store_true",
                   help="Full run without Supabase: entries are kept marked unwritten.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    spots = json.loads(args.input.read_text(encoding="utf-8"))
    if not isinstance(spots, list):
        log.error("expected a JSON array at %s", args.input)
        return 2
    roster = roster_spots(spots)

    if args.dry_run:
        cache = load_cache(args.output)
        items = plan(roster, cache, force=args.no_cache)
        print("\n".join(dry_run_report(items, cache, orphans(spots, cache))))
        return 0
    if args.sample:
        return run_sample(roster, args.sample_output)
    db = None if args.no_db else get_client()
    return run_full(spots, roster, args.output, db, limit=args.limit, force=args.no_cache)


if __name__ == "__main__":
    sys.exit(main())
