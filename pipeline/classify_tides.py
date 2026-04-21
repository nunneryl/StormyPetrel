"""Phase 0C — LLM tide-preference classification.

Reads spots_enriched.json, batches spots in groups of TIDE_CLASSIFY_BATCH_SIZE,
and asks Claude to classify each spot's optimal tide for surfing. Writes
tide_preference + tide_preference_confidence back into the enriched JSON.

Responses are cached to pipeline/cache/tide_classification.json keyed by spot
name so re-runs only hit the API for newly-added spots.

CLI:
    python -m pipeline.classify_tides [--input ...] [--output ...]
                                      [--limit N] [--no-cache] [-v]

Env:
    ANTHROPIC_API_KEY — required.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

from .config import (
    DEFAULT_ENRICHED_OUTPUT,
    TIDE_CLASSIFY_BATCH_SIZE,
    TIDE_CLASSIFY_CACHE_FILE,
    TIDE_CLASSIFY_MODEL,
)

log = logging.getLogger("pipeline.classify_tides")

_SYSTEM_PROMPT = (
    "You are a surf forecasting expert. For each US surf spot the user gives "
    "you, determine the optimal tide state for surfing based on the spot's "
    "name, region, and coordinates. Use your knowledge of well-known spots "
    "where available; otherwise fall back to the general tide preference "
    "typical for that coastline and break style. Respond with JSON only — "
    "no prose, no markdown fences.\n\n"
    "Tide values:\n"
    '- "low": best on a low / dropping tide (many shallow reefs, some point '
    "breaks)\n"
    '- "mid": best around mid tide (most beach breaks, most common answer)\n'
    '- "high": best on a high / pushing tide (sandbar spots that need '
    "deeper water; some reef passes)\n"
    '- "all": works reasonably across most tides\n\n'
    "Confidence scale: 0.9+ if the spot is well-known with a clear documented "
    "preference, 0.6-0.8 for plausible inference from the region and name, "
    "0.3-0.5 when guessing from coastline type alone."
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Classify tide preference for every enriched spot.")
    p.add_argument("--input", type=Path, default=DEFAULT_ENRICHED_OUTPUT,
                   help="Input/output spots_enriched.json (updated in place by default)")
    p.add_argument("--output", type=Path, default=None,
                   help="Output path (defaults to --input, overwriting it)")
    p.add_argument("--limit", type=int, default=None,
                   help="Classify only the first N uncached spots (dev).")
    p.add_argument("--no-cache", action="store_true",
                   help="Ignore existing cache and reclassify every spot.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _load_cache() -> dict[str, dict]:
    if not TIDE_CLASSIFY_CACHE_FILE.exists():
        return {}
    try:
        return json.loads(TIDE_CLASSIFY_CACHE_FILE.read_text())
    except json.JSONDecodeError as e:
        log.warning("tide cache %s corrupt (%s); starting fresh", TIDE_CLASSIFY_CACHE_FILE, e)
        return {}


def _save_cache(cache: dict[str, dict]) -> None:
    TIDE_CLASSIFY_CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    TIDE_CLASSIFY_CACHE_FILE.write_text(json.dumps(cache, indent=2, ensure_ascii=False))


def _build_user_prompt(spots: list[dict]) -> str:
    lines = [
        'For each surf spot below, return the optimal tide for surfing. '
        'Reply with JSON only, no other text: '
        '[{"name": "...", "tide_preference": "low|mid|high|all", "confidence": 0.0-1.0}]',
        "",
        "Spots:",
    ]
    for s in spots:
        region = s.get("region_hint") or "Unknown"
        lines.append(f'- "{s["name"]}" ({region}) at ({s["lat"]:.4f}, {s["lng"]:.4f})')
    return "\n".join(lines)


def _parse_json_response(text: str) -> list[dict]:
    """Strip optional markdown fences and parse the JSON array."""
    t = text.strip()
    if t.startswith("```"):
        # Drop the opening fence line (e.g., ```json\n)
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1 :]
        # Drop the closing fence
        t = t.rsplit("```", 1)[0].strip()
    # Sometimes the model adds a leading "json\n" or trailing explanation; try a bracket scan as fallback.
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start = t.find("[")
        end = t.rfind("]")
        if start != -1 and end != -1 and end > start:
            return json.loads(t[start : end + 1])
        raise


def _classify_batch(client, spots: list[dict]) -> tuple[list[dict], object]:
    """Call the API for one batch. Returns (results, usage)."""
    prompt = _build_user_prompt(spots)
    message = client.messages.create(
        model=TIDE_CLASSIFY_MODEL,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    text = next((b.text for b in message.content if b.type == "text"), "")
    try:
        parsed = _parse_json_response(text)
    except json.JSONDecodeError as e:
        log.error("JSON parse failed for batch of %d spots: %s\nraw response:\n%s",
                  len(spots), e, text[:500])
        raise
    if not isinstance(parsed, list):
        raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")
    return parsed, message.usage


def _normalize_result(entry: dict) -> dict | None:
    """Validate and coerce one classification entry."""
    name = entry.get("name")
    tide = entry.get("tide_preference")
    conf = entry.get("confidence")
    if not name or tide not in {"low", "mid", "high", "all"}:
        return None
    try:
        conf = float(conf)
    except (TypeError, ValueError):
        conf = 0.5
    conf = max(0.0, min(1.0, conf))
    return {"tide_preference": tide, "tide_preference_confidence": round(conf, 2)}


def classify_all(
    spots: list[dict],
    use_cache: bool = True,
    limit: int | None = None,
) -> tuple[dict[str, dict], dict]:
    """Return (name -> {tide_preference, tide_preference_confidence}, stats).

    Reads and writes TIDE_CLASSIFY_CACHE_FILE. Only pending (uncached) spots
    hit the API; cached results are reused.
    """
    import anthropic

    cache = _load_cache() if use_cache else {}
    pending = [s for s in spots if s["name"] not in cache]
    if limit is not None:
        pending = pending[:limit]
    log.info(
        "tide classification: %d spots total, %d cached, %d to classify",
        len(spots), len(spots) - len(pending), len(pending),
    )

    stats = {
        "input_tokens": 0,
        "output_tokens": 0,
        "batches": 0,
        "parse_errors": 0,
        "missing_from_response": 0,
    }
    if not pending:
        return cache, stats

    client = anthropic.Anthropic()

    try:
        from tqdm import tqdm
        batches = [
            pending[i : i + TIDE_CLASSIFY_BATCH_SIZE]
            for i in range(0, len(pending), TIDE_CLASSIFY_BATCH_SIZE)
        ]
        iterator = tqdm(batches, desc="tide classify", unit="batch")
    except ImportError:
        iterator = [
            pending[i : i + TIDE_CLASSIFY_BATCH_SIZE]
            for i in range(0, len(pending), TIDE_CLASSIFY_BATCH_SIZE)
        ]

    for batch in iterator:
        try:
            parsed, usage = _classify_batch(client, batch)
        except Exception as e:  # noqa: BLE001
            log.warning("batch failed (%d spots): %s", len(batch), e)
            stats["parse_errors"] += 1
            continue

        stats["batches"] += 1
        stats["input_tokens"] += usage.input_tokens
        stats["output_tokens"] += usage.output_tokens

        # Index parsed results by spot name for O(1) matching.
        by_name = {e.get("name"): e for e in parsed if isinstance(e, dict)}
        for spot in batch:
            entry = by_name.get(spot["name"])
            if entry is None:
                stats["missing_from_response"] += 1
                log.warning("no classification returned for %r", spot["name"])
                continue
            rec = _normalize_result(entry)
            if rec is None:
                stats["missing_from_response"] += 1
                log.warning("invalid classification for %r: %s", spot["name"], entry)
                continue
            cache[spot["name"]] = rec

        # Persist cache every batch so a crash doesn't lose progress.
        _save_cache(cache)

    return cache, stats


def _summarize(spots: list[dict], cache: dict[str, dict], stats: dict) -> None:
    # Pricing snapshot for Sonnet 4: $3 / $15 per 1M tokens.
    cost_in = stats["input_tokens"] * 3 / 1_000_000
    cost_out = stats["output_tokens"] * 15 / 1_000_000
    total_cost = cost_in + cost_out
    classified = sum(1 for s in spots if s["name"] in cache)
    by_pref: dict[str, int] = {}
    for s in spots:
        rec = cache.get(s["name"])
        if rec:
            by_pref[rec["tide_preference"]] = by_pref.get(rec["tide_preference"], 0) + 1
    print()
    print("=" * 60)
    print("Tide classification summary")
    print("=" * 60)
    print(f"  spots total:          {len(spots)}")
    print(f"  spots classified:     {classified}")
    print(f"  batches this run:     {stats['batches']}")
    print(f"  input tokens:         {stats['input_tokens']:,}")
    print(f"  output tokens:        {stats['output_tokens']:,}")
    print(f"  estimated cost:       ${total_cost:.4f}  (in ${cost_in:.4f} + out ${cost_out:.4f})")
    if stats["parse_errors"]:
        print(f"  batch errors:         {stats['parse_errors']}")
    if stats["missing_from_response"]:
        print(f"  missing/invalid:      {stats['missing_from_response']}")
    print("  distribution:")
    for pref in ("low", "mid", "high", "all"):
        print(f"    {pref:<5} {by_pref.get(pref, 0)}")
    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not os.getenv("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY is not set. Export it before running.")
        return 1

    if not args.input.exists():
        log.error("Input file %s does not exist. Run `python -m pipeline.enrich` first.", args.input)
        return 1

    spots = json.loads(args.input.read_text())
    log.info("Loaded %d enriched spots from %s", len(spots), args.input)

    cache, stats = classify_all(spots, use_cache=not args.no_cache, limit=args.limit)

    # Merge results back into the enriched records.
    updated = 0
    for s in spots:
        rec = cache.get(s["name"])
        if rec:
            s["tide_preference"] = rec["tide_preference"]
            s["tide_preference_confidence"] = rec["tide_preference_confidence"]
            updated += 1

    output_path = args.output or args.input
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(spots, indent=2, ensure_ascii=False))
    log.info("Wrote %d spots (%d with tide preference) to %s", len(spots), updated, output_path)

    _summarize(spots, cache, stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
