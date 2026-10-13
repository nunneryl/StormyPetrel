"""Generate short factual spot descriptions with Claude.

Walks every spot in spots_enriched.json, sends Claude the payload pipeline/descriptions.py builds for it
(only facts the roster holds from a source that can be stated plainly), and records the result in
pipeline/data/spot_descriptions.json (the cache) and in the spots table's description and
description_signature columns. Not part of the scheduled pipeline: run it by hand.

THE SIGNATURE. Each description is stamped with description_signature(payload): 'v2:' plus a hash of
PROMPT_VERSION and the exact payload. A spot is regenerated when its cached signature differs from the
current one — no description yet, one written before v2, or anything it states has changed. This script
is the ONLY writer of v2 signatures; db_import blanks a description whose stored v2 signature no longer
matches the roster, and leaves anything older alone.

MODES
  --dry-run   No API calls and no writes. One block per spot: what would happen and why, what the
              payload states and leaves out, and whether the current text contradicts the break type.
              Then totals and a cost estimate.
  --sample    Generates the ten spots in SAMPLE_SPOTS and writes them to a separate file
              (--sample-output) only — never to the cache, never to Supabase. Prints each with its
              payload.
  (default)   The full run. Resumable: the cache is saved every SAVE_EVERY generations and a rerun
              skips every spot whose cached signature is current. Each description is written to
              Supabase as soon as it is generated; one that could not be written is kept in the cache
              marked "pushed": false and written on the next run. A final reconcile reads the table
              and re-writes any row whose signature differs from the cache — which also repairs a row
              that a forecast-pipeline run, writing back the row it read before this one changed it,
              put back while this was running. Only the rows this run touched are written; nothing
              else in the cache is pushed. Cache entries for spots no longer in the roster are pruned.

CLI:
    python -m pipeline.generate_descriptions --dry-run > dry_run.txt
    python -m pipeline.generate_descriptions --sample
    python -m pipeline.generate_descriptions [--limit N] [--no-db] [--no-cache] [-v]

Env:
    ANTHROPIC_API_KEY  — required for --sample and the full run.
    SUPABASE_URL, SUPABASE_SERVICE_KEY — required for the full run unless --no-db.

Cost: claude-sonnet-4-6 at $3 / $15 per million input / output tokens. 646 spots at roughly 360 input
and 90 output tokens each is about $1.60 for a full regeneration; --dry-run prints the estimate for
what it would actually generate.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import re
import sys
from pathlib import Path

from .config import DEFAULT_ENRICHED_OUTPUT
from .db_import import _excluded_slugs, _fetch_existing_spots, _slugify, get_client
from .descriptions import (
    GATED_FIELDS,
    STATED_SOURCES,
    SYSTEM_PROMPT,
    description_payload,
    description_signature,
    spot_fields,
)

log = logging.getLogger("pipeline.generate_descriptions")

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 200  # 75-word cap is ~110 tokens; leaves headroom.

OUTPUT_FILE = Path(__file__).parent / "data" / "spot_descriptions.json"
SAMPLE_FILE = Path(__file__).parent / "data" / "spot_descriptions_sample.json"
SAVE_EVERY = 10

# claude-sonnet-4-6 list price, USD per million tokens (Anthropic's model table, as of 2026-10-06).
PRICE_PER_MTOK = {"input": 3.00, "output": 15.00}
# The dry run has no API to count tokens with, so it estimates from characters. Existing descriptions
# average 257 characters (about 43 words); 90 output tokens is a little above that.
CHARS_PER_TOKEN_EST = 3.5
OUTPUT_TOKENS_EST = 90

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


def estimate(payloads):
    """(calls, mean input tokens, output tokens per call, USD) — a character-count estimate."""
    n = len(payloads)
    if not n:
        return 0, 0, OUTPUT_TOKENS_EST, 0.0
    tokens_in = [math.ceil((len(SYSTEM_PROMPT) + len(user_prompt(p))) / CHARS_PER_TOKEN_EST)
                 for p in payloads]
    usd = (sum(tokens_in) * PRICE_PER_MTOK["input"]
           + n * OUTPUT_TOKENS_EST * PRICE_PER_MTOK["output"]) / 1_000_000
    return n, round(sum(tokens_in) / n), OUTPUT_TOKENS_EST, usd


def dry_run_report(items, cache, orphan_slugs):
    lines = []
    counts = {GENERATE: {}, PUSH: 0, KEEP: 0}
    contradicting = 0
    left = {field: {} for field in GATED_FIELDS}
    for slug, spot, payload, _sig, action, reason in items:
        text = (cache.get(slug) or {}).get("description") if isinstance(cache.get(slug), dict) else None
        wrong = contradiction(text, spot) if text else None
        contradicting += bool(wrong)
        for field in GATED_FIELDS:
            if field not in payload:
                value = spot.get(field)
                key = ("not set" if value in (None, "") else "'unknown'" if value == "unknown"
                       else spot.get(f"{field}_source") or "no source")
                left[field][key] = left[field].get(key, 0) + 1
        if action == GENERATE:
            counts[GENERATE][reason] = counts[GENERATE].get(reason, 0) + 1
        else:
            counts[action] += 1
        if action == KEEP and not wrong:
            lines.append(f"[keep] {slug} — {reason}")
            continue
        lines.append(f"[{action}] {slug} — {reason}")
        stated = [f"{f} {payload[f]} ({spot.get(f + '_source')})"
                  for f in GATED_FIELDS if f in payload]
        lines.append(f"    states:   {', '.join(stated) or '—'}")
        lines.append(f"    left out: {', '.join(left_out(spot)) or '—'}")
        if wrong:
            lines.append(f"    current text contradicts: {wrong}")
        lines.append(f"    payload:  {user_prompt(payload)}")
    n_gen = sum(counts[GENERATE].values())
    calls, tin, tout, usd = estimate([p for _s, _sp, p, _g, a, _r in items if a == GENERATE])
    lines += [
        "",
        "TOTALS",
        f"  spots in the roster:              {len(items)}",
        f"  to generate:                      {n_gen}"
        + "".join(f"\n      {r}: {c}" for r, c in sorted(counts[GENERATE].items())),
        f"  to write to Supabase only:        {counts[PUSH]}",
        f"  unchanged:                        {counts[KEEP]}",
        f"  orphan cache entries to prune:    {len(orphan_slugs)}"
        + (f" ({', '.join(orphan_slugs)})" if orphan_slugs else ""),
        f"  current texts contradicting the break type: {contradicting}",
        "  break_type left out:      " + _counts(left["break_type"]),
        "  bottom left out:          " + _counts(left["bottom"]),
        "  tide_preference left out: " + _counts(left["tide_preference"]),
        f"  estimate: {calls} call(s) x ~{tin} input + ~{tout} output tokens = about ${usd:.2f} on "
        f"{MODEL} at ${PRICE_PER_MTOK['input']:.2f} / ${PRICE_PER_MTOK['output']:.2f} per million "
        "tokens (from character counts, not a token count)",
    ]
    return lines


def _counts(d):
    return ", ".join(f"{k} {v}" for k, v in sorted(d.items())) or "none"


# --------------------------------------------------------------------------- #
# The model                                                                    #
# --------------------------------------------------------------------------- #
def user_prompt(payload):
    """The user message: the payload as compact JSON, in the builder's key order."""
    return json.dumps(payload, ensure_ascii=False)


def _strip_quotes(text: str) -> str:
    """Models sometimes wrap the answer in straight or smart quotes."""
    t = text.strip()
    if len(t) >= 2 and t[0] in "\"'“‘" and t[-1] in "\"'”’":
        return t[1:-1].strip()
    return t


def anthropic_client():
    import anthropic
    return anthropic.Anthropic()


def generate_one(client, payload) -> str:
    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_prompt(payload)}],
    )
    text = next((b.text for b in message.content if b.type == "text"), "")
    cleaned = _strip_quotes(text)
    if not cleaned:
        raise RuntimeError("empty response")
    return cleaned


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
def run_sample(roster, out_path: Path, client) -> int:
    by_name = {s["name"]: (slug, s) for slug, s in roster.items()}
    missing = [n for n in SAMPLE_SPOTS if n not in by_name]
    if missing:
        log.error("sample spots not in the roster: %s", ", ".join(missing))
        return 2
    results, failed = {}, 0
    for name in SAMPLE_SPOTS:
        slug, spot = by_name[name]
        payload = description_payload(spot_fields(spot))
        try:
            text = generate_one(client, payload)
        except Exception:  # noqa: BLE001
            log.exception("%s: generation failed", slug)
            failed += 1
            continue
        results[slug] = {"name": name, "payload": payload,
                         "signature": description_signature(payload), "description": text}
        print(f"== {name} ({slug})")
        print(f"   left out: {', '.join(left_out(spot)) or '—'}")
        print(f"   payload:  {user_prompt(payload)}")
        print(f"   {text}\n", flush=True)
    save_json(out_path, results)
    print(f"{len(results)} sample description(s) written to {out_path}; nothing else was written.")
    return 0 if failed == 0 else 1


def run_full(spots, roster, cache_path: Path, client, db, limit=None, force=False) -> int:
    cache = load_cache(cache_path)
    gone = orphans(spots, cache)
    for slug in gone:
        del cache[slug]
    if gone:
        log.info("pruned %d cache entr%s for spots no longer in the roster: %s",
                 len(gone), "y" if len(gone) == 1 else "ies", ", ".join(gone))
        save_json(cache_path, cache)

    items = plan(roster, cache, force=force)
    generated = pushed = kept_unpushed = failed = 0
    for slug, _spot, payload, sig, action, reason in items:
        if action == KEEP:
            continue
        if action == PUSH:
            if db is not None and push(db, slug, cache[slug]):
                cache[slug].pop("pushed", None)
                pushed += 1
                save_json(cache_path, cache)
            continue
        if limit is not None and generated >= limit:
            break
        try:
            text = generate_one(client, payload)
        except Exception:  # noqa: BLE001
            log.exception("%s: generation failed", slug)
            failed += 1
            continue
        entry = {"description": text, "signature": sig}
        generated += 1
        if db is not None and push(db, slug, entry):
            pushed += 1
        else:
            entry["pushed"] = False
            kept_unpushed += 1
        cache[slug] = entry
        log.info("%s (%s): %s", slug, reason, text[:80] + ("…" if len(text) > 80 else ""))
        if generated % SAVE_EVERY == 0:
            save_json(cache_path, cache)
    save_json(cache_path, cache)

    fixed = still = 0
    if db is not None:
        fixed, still = reconcile(db, cache, roster)
    log.info("generated=%d written=%d kept-unwritten=%d failed=%d reconciled=%d still-differ=%d; "
             "cache → %s", generated, pushed, kept_unpushed, failed, fixed, still, cache_path)
    # Unwritten entries are expected under --no-db; with Supabase they mean a rerun is needed.
    return 0 if failed == 0 and still == 0 and (db is None or kept_unpushed == 0) else 1


def _parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = p.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true",
                      help="No API calls, no writes: the per-spot report, totals and cost estimate.")
    mode.add_argument("--sample", action="store_true",
                      help="Generate the ten SAMPLE_SPOTS into --sample-output only.")
    p.add_argument("--input", type=Path, default=DEFAULT_ENRICHED_OUTPUT, help="spots_enriched.json")
    p.add_argument("--output", type=Path, default=OUTPUT_FILE, help="the description cache")
    p.add_argument("--sample-output", type=Path, default=SAMPLE_FILE)
    p.add_argument("--limit", type=int, default=None, help="Cap on generations this run.")
    p.add_argument("--no-cache", action="store_true",
                   help="Regenerate every spot, whatever its cached signature.")
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

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY is required")
        return 1
    if args.sample:
        return run_sample(roster, args.sample_output, anthropic_client())
    db = None if args.no_db else get_client()
    return run_full(spots, roster, args.output, anthropic_client(), db, limit=args.limit,
                    force=args.no_cache)


if __name__ == "__main__":
    sys.exit(main())
