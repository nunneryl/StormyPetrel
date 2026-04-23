"""Phase 2B — LLM verification of spot metadata.

Cross-checks every enriched spot against Claude's surf-domain knowledge
(Surfline, surf-forecast, local guides), corrects orientation /
swell-window / break-type / tide-preference where the geometric
algorithms got it wrong, surfaces newly-derived fields (crowd_factor,
hazards), and flags non-surf entries (surf shops, river-mouths beyond
the surf zone, lakes mistakenly tagged as breaks, duplicates).

Mirrors classify_tides.py: batched Claude Sonnet calls with structured
JSON output, on-disk cache so re-runs only hit the API for newly-added
spots, prompt caching on the system prompt.

After verification, a merge step applies high/medium-confidence
corrections back into spots_enriched.json. Low-confidence entries are
left untouched but flagged in the verification file so they can be
reviewed manually.

CLI:
    python -m pipeline.verify_spots [--input ...] [--output ...]
                                    [--limit N] [--no-cache] [--no-merge] [-v]

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
    SPOT_VERIFICATION_FILE,
    SPOT_VERIFY_BATCH_SIZE,
    SPOT_VERIFY_MODEL,
)

log = logging.getLogger("pipeline.verify_spots")


# Long, stable system prompt — designed to (a) elicit consistent JSON
# and (b) clear the 2048-token Sonnet 4.6 prompt-cache threshold so
# every batch after the first reads instead of writes.
_SYSTEM_PROMPT = """You are a surf forecasting expert with deep knowledge of US surf spots,
including those documented on Surfline, surf-forecast.com, MagicSeaweed
(historical), Wannasurf, regional guidebooks, and local surf-club pages.

For each candidate surf spot the user describes, return a structured
verification record. The user is building an open-source surf forecast
covering ~500 US spots, and the upstream pipeline derived geometric
metadata (orientation, offshore wind direction, optimal swell direction,
break type, tide preference) from coastline polygons. Those geometric
estimates are often wrong for spots that:

  - sit on a barrier island whose town center geocoded to the bay side
  - sit at a river-mouth or jetty where the coastline normal misrepresents
    the actual wave-facing geometry
  - have a well-known surfer-facing direction that differs from the
    nearest-coast tangent (point breaks are the classic case)

Your job is to verify or correct these values using your domain knowledge
of the spot, not your knowledge of the underlying pipeline.

OUTPUT FORMAT
=============

Respond with JSON only — no prose, no markdown fences, no commentary.
Return a JSON array containing one object per spot, in the same order
the spots appeared in the user message. Each object must include the
spot's name verbatim and every field below.

For each object:

  name                  — string, copied verbatim from the prompt
  is_valid_surf_spot    — boolean. False if this is a surf shop, a river
                          mouth not actually surfed, an inland lake
                          (except recognized Great-Lakes surf spots), a
                          duplicate of another well-known spot, a beach
                          with no rideable waves, or anything else that
                          doesn't belong in a surf-spot database.
  invalid_reason        — string or null. One of: "surf_shop", "river",
                          "lake", "duplicate", "non_surfable", "unknown".
                          Null when is_valid_surf_spot is true.
  facing_direction_deg  — integer 0-359. The bearing the wave-rider faces
                          when looking out at the incoming swell (the
                          seaward bearing of the break). 0=N, 90=E,
                          180=S, 270=W. For an east-coast Atlantic spot
                          this is roughly 90; for a California Pacific
                          spot roughly 270; for a Gulf-coast spot roughly
                          180. Point breaks may differ substantially
                          from the local coastline normal.
  offshore_wind_deg     — integer 0-359. The bearing FROM which the wind
                          blows when it is offshore (clean) at this spot.
                          Always (facing_direction_deg + 180) % 360.
  optimal_swell_dir     — integer 0-359. The swell bearing that produces
                          the cleanest, best-formed waves at this spot.
                          Often equal to facing_direction_deg, but for
                          point breaks and reef passes it can be
                          significantly off-axis.
  break_type            — string. One of: "beach", "reef", "point",
                          "jetty", "rivermouth". Pick the dominant type;
                          if the spot is mixed (e.g. beach with a reef
                          section), pick the one most surfers associate
                          with the name.
  tide_preference       — string. One of: "low", "mid", "high", "all".
                          The tide stage at which the spot works best.
                          "all" only when the spot is genuinely
                          tide-tolerant; default to "mid" if uncertain.
  crowd_factor          — string. One of: "heavy", "moderate", "light",
                          "empty". Your best estimate of typical crowd
                          density on a good day. Heavy = packed lineups
                          (Malibu, Trestles, Pipeline, Steamer Lane);
                          moderate = recognizable spot with locals plus
                          travelers; light = mostly known to locals;
                          empty = obscure or hard-to-reach.
  hazards               — array of short strings. Common values: "rips",
                          "rocks", "reef", "sharks", "sea_urchins",
                          "shallow", "crowds", "localism", "pollution",
                          "wood_pilings", "jetty", "boat_traffic",
                          "current", "cold_water". Empty array if none
                          stand out.
  confidence            — string. One of: "high", "medium", "low".
                          "high"   — well-known spot, you can cite
                                     specific characteristics from
                                     memory.
                          "medium" — recognized spot or you can
                                     reasonably infer most fields from
                                     the name + region + coordinates.
                          "low"    — you don't recognize the spot;
                                     fields are best-guess from the
                                     coastline geometry only.
  notes                 — string. One or two short sentences. Mention
                          any specific corrections you applied, anything
                          unusual about the spot, or "Spot not
                          recognized; defaults applied."

GUIDELINES
==========

  - When the user-provided computed values look reasonable and you have
    no specific reason to disagree, return them unchanged. Don't churn
    fields just because you can.
  - Do NOT invent precision. If you only know a spot vaguely, set
    confidence to "low" and use the user's provided values as your
    estimates rather than fabricating new ones.
  - Atlantic-facing US spots have facing_direction_deg roughly in
    [0, 180]; Pacific-facing roughly in [180, 360]; Gulf roughly in
    [90, 270]; Great-Lakes spots can face any direction depending on
    which lake and which shore.
  - Florida east-coast spots face roughly east (~90); west-coast Gulf
    spots face roughly south or southwest (~180-225); Keys spots face
    roughly south (~180).
  - For Hawaiian spots use your specific knowledge of the island and
    swell exposure (north-shore Oahu winter spots face ~330-30; south-
    shore summer spots ~180-200).
  - If a spot is marked in the prompt with a non-surfing name pattern
    ("surf shop", "surfboards", "surf school", "rentals"), it is almost
    certainly invalid_reason="surf_shop" — but verify by name, don't
    rely solely on keyword matching.
  - If two spots in the same batch have the same name and very similar
    coordinates (< 1 km apart), mark the second one invalid_reason=
    "duplicate" with confidence="high".

Return JSON only.
"""


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="LLM-verify and correct spot metadata.")
    p.add_argument("--input", type=Path, default=DEFAULT_ENRICHED_OUTPUT,
                   help="Input/output spots_enriched.json (updated in place by default).")
    p.add_argument("--output", type=Path, default=None,
                   help="Output path for enriched spots after merge (defaults to --input).")
    p.add_argument("--verification-file", type=Path, default=SPOT_VERIFICATION_FILE,
                   help="Where to read/write per-spot verification records.")
    p.add_argument("--limit", type=int, default=None,
                   help="Verify only the first N pending spots (dev).")
    p.add_argument("--no-cache", action="store_true",
                   help="Ignore existing verification file and reverify every spot.")
    p.add_argument("--no-merge", action="store_true",
                   help="Skip the merge step; only write the verification file.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _load_verifications(path: Path) -> dict[str, dict]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        log.warning("verification file %s corrupt (%s); starting fresh", path, e)
        return {}


def _save_verifications(path: Path, data: dict[str, dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True))


def _build_user_prompt(spots: list[dict]) -> str:
    lines = [
        "Verify the surf-spot metadata below. Reply with a JSON array, "
        "one object per spot, in the same order. JSON only — no prose, "
        "no markdown fences.",
        "",
        "Spots:",
    ]
    for s in spots:
        region = s.get("region_hint") or "Unknown"
        computed = (
            f"orientation_deg={s.get('orientation_deg')!r}, "
            f"offshore_wind_deg={s.get('offshore_wind_deg')!r}, "
            f"optimal_swell_dir={s.get('optimal_swell_dir')!r}, "
            f"break_type={s.get('break_type')!r}, "
            f"tide_preference={s.get('tide_preference')!r}"
        )
        lines.append(
            f'- name="{s["name"]}" region="{region}" '
            f'coords=({s["lat"]:.4f}, {s["lng"]:.4f}) computed=({computed})'
        )
    return "\n".join(lines)


def _parse_json_response(text: str) -> list[dict]:
    """Strip optional markdown fences and parse the JSON array."""
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1 :]
        t = t.rsplit("```", 1)[0].strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        start = t.find("[")
        end = t.rfind("]")
        if start != -1 and end != -1 and end > start:
            return json.loads(t[start : end + 1])
        raise


def _verify_batch(client, spots: list[dict]) -> tuple[list[dict], object]:
    prompt = _build_user_prompt(spots)
    # System prompt as a list-of-blocks with cache_control so every batch
    # after the first reads the cache (~0.1× input cost) instead of
    # paying full price for the long instructions.
    message = client.messages.create(
        model=SPOT_VERIFY_MODEL,
        max_tokens=4096,
        system=[
            {
                "type": "text",
                "text": _SYSTEM_PROMPT,
                "cache_control": {"type": "ephemeral"},
            }
        ],
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


_VALID_INVALID_REASONS = {"surf_shop", "river", "lake", "duplicate", "non_surfable", "unknown"}
_VALID_BREAK_TYPES = {"beach", "reef", "point", "jetty", "rivermouth"}
_VALID_TIDE_PREFS = {"low", "mid", "high", "all"}
_VALID_CROWD = {"heavy", "moderate", "light", "empty"}
_VALID_CONFIDENCE = {"high", "medium", "low"}


def _coerce_int(v, *, mod: int | None = None) -> int | None:
    try:
        n = int(round(float(v)))
    except (TypeError, ValueError):
        return None
    if mod is not None:
        n = n % mod
    return n


def _normalize_record(entry: dict) -> dict | None:
    name = entry.get("name")
    if not name:
        return None

    is_valid = bool(entry.get("is_valid_surf_spot", True))
    invalid_reason = entry.get("invalid_reason")
    if invalid_reason not in _VALID_INVALID_REASONS:
        invalid_reason = None
    if not is_valid and invalid_reason is None:
        invalid_reason = "unknown"

    confidence = entry.get("confidence")
    if confidence not in _VALID_CONFIDENCE:
        confidence = "low"

    break_type = entry.get("break_type") if entry.get("break_type") in _VALID_BREAK_TYPES else None
    tide_pref = entry.get("tide_preference") if entry.get("tide_preference") in _VALID_TIDE_PREFS else None
    crowd = entry.get("crowd_factor") if entry.get("crowd_factor") in _VALID_CROWD else None

    hazards = entry.get("hazards") or []
    if not isinstance(hazards, list):
        hazards = []
    hazards = [str(h).lower().strip() for h in hazards if isinstance(h, (str, int, float))]
    hazards = [h for h in hazards if h]

    return {
        "name": name,
        "is_valid_surf_spot": is_valid,
        "invalid_reason": invalid_reason,
        "facing_direction_deg": _coerce_int(entry.get("facing_direction_deg"), mod=360),
        "offshore_wind_deg": _coerce_int(entry.get("offshore_wind_deg"), mod=360),
        "optimal_swell_dir": _coerce_int(entry.get("optimal_swell_dir"), mod=360),
        "break_type": break_type,
        "tide_preference": tide_pref,
        "crowd_factor": crowd,
        "hazards": hazards,
        "confidence": confidence,
        "notes": (entry.get("notes") or "").strip()[:500],
    }


def verify_all(
    spots: list[dict],
    verification_path: Path,
    use_cache: bool = True,
    limit: int | None = None,
) -> tuple[dict[str, dict], dict]:
    """Return (name -> verification record, stats). Persists to *verification_path*."""
    import anthropic

    cache = _load_verifications(verification_path) if use_cache else {}
    pending = [s for s in spots if s["name"] not in cache]
    if limit is not None:
        pending = pending[:limit]
    log.info(
        "verify: %d spots total, %d cached, %d to verify",
        len(spots), len(spots) - len(pending), len(pending),
    )

    stats = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "batches": 0,
        "parse_errors": 0,
        "missing_from_response": 0,
    }
    if not pending:
        return cache, stats

    client = anthropic.Anthropic()

    batches = [
        pending[i : i + SPOT_VERIFY_BATCH_SIZE]
        for i in range(0, len(pending), SPOT_VERIFY_BATCH_SIZE)
    ]
    try:
        from tqdm import tqdm
        iterator = tqdm(batches, desc="verify spots", unit="batch")
    except ImportError:
        iterator = batches

    for batch in iterator:
        try:
            parsed, usage = _verify_batch(client, batch)
        except Exception as e:  # noqa: BLE001
            log.warning("batch failed (%d spots): %s", len(batch), e)
            stats["parse_errors"] += 1
            continue

        stats["batches"] += 1
        stats["input_tokens"] += usage.input_tokens
        stats["output_tokens"] += usage.output_tokens
        stats["cache_creation_input_tokens"] += getattr(usage, "cache_creation_input_tokens", 0) or 0
        stats["cache_read_input_tokens"] += getattr(usage, "cache_read_input_tokens", 0) or 0

        by_name = {e.get("name"): e for e in parsed if isinstance(e, dict)}
        for spot in batch:
            entry = by_name.get(spot["name"])
            if entry is None:
                stats["missing_from_response"] += 1
                log.warning("no verification returned for %r", spot["name"])
                continue
            rec = _normalize_record(entry)
            if rec is None:
                stats["missing_from_response"] += 1
                log.warning("invalid verification for %r: %s", spot["name"], entry)
                continue
            cache[spot["name"]] = rec

        # Persist after every batch so a crash doesn't lose progress.
        _save_verifications(verification_path, cache)

    return cache, stats


# ---------------------------------------------------------------------------
# Merge step — apply high/medium-confidence corrections back into enriched
# ---------------------------------------------------------------------------

def merge_into_spots(
    spots: list[dict],
    verifications: dict[str, dict],
) -> dict:
    """Apply high/medium-confidence verifications to *spots* in place.

    Low-confidence records are not applied — the spot keeps its computed
    values, but the verification record stays in spot_verification.json
    for manual review.

    Returns stats: counts of changed fields by category and by confidence.
    """
    stats = {
        "high_applied": 0,
        "medium_applied": 0,
        "low_skipped": 0,
        "no_verification": 0,
        "invalid_flagged": 0,
        "field_changes": {
            "orientation_deg": 0,
            "offshore_wind_deg": 0,
            "optimal_swell_dir": 0,
            "break_type": 0,
            "tide_preference": 0,
            "crowd_factor": 0,
            "hazards": 0,
        },
    }

    for spot in spots:
        rec = verifications.get(spot["name"])
        if rec is None:
            stats["no_verification"] += 1
            continue

        # Stamp verification metadata regardless of confidence — useful for
        # downstream consumers and manual review.
        spot["verification_confidence"] = rec.get("confidence")
        spot["verification_notes"] = rec.get("notes")
        if not rec.get("is_valid_surf_spot", True):
            spot["is_valid_surf_spot"] = False
            spot["invalid_reason"] = rec.get("invalid_reason")
            stats["invalid_flagged"] += 1

        confidence = rec.get("confidence")
        if confidence not in ("high", "medium"):
            stats["low_skipped"] += 1
            continue
        if confidence == "high":
            stats["high_applied"] += 1
        else:
            stats["medium_applied"] += 1

        # Apply field-level overrides where the LLM returned a usable value.
        # If orientation changes, also clear orientation-derived swell-window
        # arcs so a subsequent `enrich --skip-raycast` rebuilds them from
        # the corrected orientation.
        new_orient = rec.get("facing_direction_deg")
        if new_orient is not None and new_orient != spot.get("orientation_deg"):
            spot["orientation_deg"] = new_orient
            stats["field_changes"]["orientation_deg"] += 1
            if spot.get("swell_window_source") == "orientation_derived":
                spot["swell_window_arcs"] = []
                spot["optimal_swell_dir"] = None
                spot.pop("swell_window_source", None)

        new_offshore = rec.get("offshore_wind_deg")
        if new_offshore is not None and new_offshore != spot.get("offshore_wind_deg"):
            spot["offshore_wind_deg"] = new_offshore
            stats["field_changes"]["offshore_wind_deg"] += 1

        new_optimal = rec.get("optimal_swell_dir")
        if new_optimal is not None and new_optimal != spot.get("optimal_swell_dir"):
            spot["optimal_swell_dir"] = new_optimal
            stats["field_changes"]["optimal_swell_dir"] += 1

        new_break = rec.get("break_type")
        if new_break is not None and new_break != spot.get("break_type"):
            spot["break_type"] = new_break
            stats["field_changes"]["break_type"] += 1

        new_tide = rec.get("tide_preference")
        if new_tide is not None and new_tide != spot.get("tide_preference"):
            spot["tide_preference"] = new_tide
            stats["field_changes"]["tide_preference"] += 1

        new_crowd = rec.get("crowd_factor")
        if new_crowd is not None and new_crowd != spot.get("crowd_factor"):
            spot["crowd_factor"] = new_crowd
            stats["field_changes"]["crowd_factor"] += 1

        new_hazards = rec.get("hazards") or []
        if new_hazards and new_hazards != (spot.get("hazards") or []):
            spot["hazards"] = new_hazards
            stats["field_changes"]["hazards"] += 1

    return stats


# ---------------------------------------------------------------------------
# Summary + CLI
# ---------------------------------------------------------------------------

def _summarize(
    spots: list[dict],
    verifications: dict[str, dict],
    api_stats: dict,
    merge_stats: dict | None,
) -> None:
    # Sonnet 4.6: $3 in / $15 out per 1M tokens; cache reads ~0.1× input.
    cost_in = api_stats["input_tokens"] * 3 / 1_000_000
    cost_out = api_stats["output_tokens"] * 15 / 1_000_000
    cost_cache_write = api_stats["cache_creation_input_tokens"] * 3 * 1.25 / 1_000_000
    cost_cache_read = api_stats["cache_read_input_tokens"] * 3 * 0.1 / 1_000_000
    total_cost = cost_in + cost_out + cost_cache_write + cost_cache_read

    by_conf = {"high": 0, "medium": 0, "low": 0}
    invalid_spots: list[tuple[str, str | None]] = []
    for rec in verifications.values():
        c = rec.get("confidence", "low")
        if c in by_conf:
            by_conf[c] += 1
        if not rec.get("is_valid_surf_spot", True):
            invalid_spots.append((rec.get("name", "?"), rec.get("invalid_reason")))

    print()
    print("=" * 60)
    print("Spot verification summary")
    print("=" * 60)
    print(f"  spots total:          {len(spots)}")
    print(f"  spots verified:       {len(verifications)}")
    print(f"  batches this run:     {api_stats['batches']}")
    print(f"  input tokens:         {api_stats['input_tokens']:,}")
    print(f"  output tokens:        {api_stats['output_tokens']:,}")
    print(f"  cache writes:         {api_stats['cache_creation_input_tokens']:,}")
    print(f"  cache reads:          {api_stats['cache_read_input_tokens']:,}")
    print(f"  estimated cost:       ${total_cost:.4f}  "
          f"(in ${cost_in:.4f} + out ${cost_out:.4f} + "
          f"cache_w ${cost_cache_write:.4f} + cache_r ${cost_cache_read:.4f})")
    if api_stats["parse_errors"]:
        print(f"  batch errors:         {api_stats['parse_errors']}")
    if api_stats["missing_from_response"]:
        print(f"  missing/invalid:      {api_stats['missing_from_response']}")

    print("  confidence:")
    for conf in ("high", "medium", "low"):
        print(f"    {conf:<6} {by_conf[conf]}")

    if invalid_spots:
        print(f"  flagged invalid: {len(invalid_spots)}")
        for name, reason in invalid_spots[:10]:
            print(f"    {reason or '?':<14} {name}")
        if len(invalid_spots) > 10:
            print(f"    ... ({len(invalid_spots) - 10} more)")

    if merge_stats is not None:
        print()
        print("  merge into spots_enriched.json:")
        print(f"    high-confidence applied:    {merge_stats['high_applied']}")
        print(f"    medium-confidence applied:  {merge_stats['medium_applied']}")
        print(f"    low-confidence skipped:     {merge_stats['low_skipped']}")
        print(f"    no verification record:     {merge_stats['no_verification']}")
        print(f"    invalid spots flagged:      {merge_stats['invalid_flagged']}")
        print("    field changes:")
        for field, n in merge_stats["field_changes"].items():
            print(f"      {field:<22} {n}")
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
        log.error("Input file %s does not exist. Run `python -m pipeline.enrich` first.",
                  args.input)
        return 1

    spots = json.loads(args.input.read_text())
    log.info("Loaded %d enriched spots from %s", len(spots), args.input)

    verifications, api_stats = verify_all(
        spots,
        verification_path=args.verification_file,
        use_cache=not args.no_cache,
        limit=args.limit,
    )

    merge_stats: dict | None = None
    if not args.no_merge:
        merge_stats = merge_into_spots(spots, verifications)
        output_path = args.output or args.input
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(spots, indent=2, ensure_ascii=False))
        log.info("Wrote %d spots back to %s after merge", len(spots), output_path)

    _summarize(spots, verifications, api_stats, merge_stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
