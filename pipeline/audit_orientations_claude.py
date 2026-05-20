"""Claude-based orientation audit.

For every spot in spots_enriched.json this script asks Claude what
the SEAWARD-FACING compass bearing should be, then compares against
our stored orientation_deg. Spots whose angular difference is ≥ 20°
get flagged; medium/high-confidence flags also land in a corrections
candidate file in the same shape as manual_orientations.json so the
maintainer can paste-and-review.

CLI:
    python -m pipeline.audit_orientations_claude [--limit N]
                                                 [--no-cache]
                                                 [--batch-size 10]
                                                 [-v]

Env:
    ANTHROPIC_API_KEY — required.

Cost: 489 spots batched 10-at-a-time → ~50 requests × (~1k input +
~500 output) at Sonnet 4.6 pricing ≈ ~$0.50 full regen. Incremental
runs cost almost nothing thanks to the on-disk cache.
"""
from __future__ import annotations

import argparse
import json
import logging
import math
import os
import sys
from pathlib import Path

import anthropic

from .config import CACHE_DIR, DEFAULT_ENRICHED_OUTPUT

log = logging.getLogger("pipeline.audit_orientations_claude")

MODEL = "claude-sonnet-4-6"
DEFAULT_BATCH_SIZE = 10
MAX_TOKENS = 800
FLAG_THRESHOLD_DEG = 20

DATA_DIR = Path(__file__).parent / "data"
OUTPUT_AUDIT = DATA_DIR / "audit_claude.json"
OUTPUT_CORRECTIONS = DATA_DIR / "orientation_corrections.json"
CACHE_FILE = CACHE_DIR / "audit_claude.json"

SYSTEM_PROMPT = (
    "You are a coastal geography expert. For each surf spot in the user's "
    "list, determine the SEAWARD-FACING compass bearing in degrees (0-359). "
    "This is the direction a surfer standing on the beach would look when "
    "facing the open ocean. 0=N, 90=E, 180=S, 270=W. "
    "Respond with ONLY a JSON array, one object per spot in the order given. "
    "Each object: "
    "{\"orientation_deg\": <0-359>, "
    "\"confidence\": \"high\"|\"medium\"|\"low\", "
    "\"reasoning\": \"<one short sentence>\"}. "
    "Use 'high' for famous spots whose geometry you know cold (Pipeline, "
    "Mavericks, etc.); 'medium' when the coastline at the given coordinate "
    "makes the bearing obvious; 'low' when you're inferring from a name + "
    "coordinate without specific knowledge. Output nothing but the JSON array."
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _circular_diff(a: float, b: float) -> float:
    d = abs(a - b) % 360
    return d if d <= 180 else 360 - d


def _normalize_deg(deg: float) -> float:
    return ((deg % 360) + 360) % 360


def _build_user_prompt(batch: list[dict]) -> str:
    lines = ["Determine the seaward-facing bearing for each surf spot:"]
    for i, spot in enumerate(batch, start=1):
        lines.append(
            f"{i}. {spot['name']}, "
            f"{spot.get('region_hint') or '(state unknown)'}, "
            f"{spot['lat']:.4f}, {spot['lng']:.4f}",
        )
    return "\n".join(lines)


def _parse_response(text: str) -> list | None:
    """Strip optional markdown fences and parse the JSON array. Returns
    None when the response is unsalvageable."""
    t = text.strip()
    if t.startswith("```"):
        nl = t.find("\n")
        if nl != -1:
            t = t[nl + 1:]
        t = t.rsplit("```", 1)[0].strip()
    try:
        data = json.loads(t)
    except json.JSONDecodeError:
        start = t.find("[")
        end = t.rfind("]")
        if start == -1 or end == -1:
            return None
        try:
            data = json.loads(t[start:end + 1])
        except json.JSONDecodeError:
            return None
    return data if isinstance(data, list) else None


def _normalize_entry(entry) -> dict | None:
    if not isinstance(entry, dict):
        return None
    try:
        deg = float(entry["orientation_deg"])
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(deg):
        return None
    conf = entry.get("confidence")
    if conf not in ("high", "medium", "low"):
        conf = "low"
    reasoning = (entry.get("reasoning") or "").strip()[:240]
    return {
        "orientation_deg": round(_normalize_deg(deg), 1),
        "confidence": conf,
        "reasoning": reasoning,
    }


def _classify_batch(client: anthropic.Anthropic, batch: list[dict]) -> list[dict | None]:
    """One Claude call for up to BATCH_SIZE spots. Returns one entry
    per input spot (None for entries we couldn't normalize)."""
    prompt = _build_user_prompt(batch)
    msg = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    text = next((b.text for b in msg.content if b.type == "text"), "")
    parsed = _parse_response(text)
    if parsed is None:
        raise RuntimeError(f"unparseable response: {text[:200]}")
    # Pad / truncate to match batch length so caller indexing stays
    # safe even if Claude dropped or duplicated an entry.
    if len(parsed) != len(batch):
        log.warning("batch returned %d entries, expected %d", len(parsed), len(batch))
    parsed = parsed[:len(batch)] + [None] * max(0, len(batch) - len(parsed))
    return [_normalize_entry(e) for e in parsed]


# ---------------------------------------------------------------------------
# cache
# ---------------------------------------------------------------------------

def _load_cache() -> dict[str, dict]:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text())
    except json.JSONDecodeError as e:
        log.warning("cache %s corrupt (%s) — starting fresh", CACHE_FILE, e)
        return {}


def _save_cache(cache: dict[str, dict]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2) + "\n")


# ---------------------------------------------------------------------------
# audit run
# ---------------------------------------------------------------------------

def _spot_key(spot: dict) -> str:
    """Cache key — spot name is unique within our dataset. (slug
    derivation is in db_import; the audit shouldn't need to depend on
    slug spelling, just identity, so the bare name is fine.)"""
    return spot.get("name") or ""


def _is_processable(spot: dict) -> bool:
    return bool(
        spot.get("name")
        and isinstance(spot.get("lat"), (int, float))
        and isinstance(spot.get("lng"), (int, float))
    )


def run_audit(
    spots: list[dict],
    batch_size: int,
    no_cache: bool,
    limit: int | None,
) -> tuple[dict, dict]:
    """Audit every (processable) spot. Returns (audit_payload,
    corrections_payload)."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise RuntimeError("ANTHROPIC_API_KEY is required")
    client = anthropic.Anthropic()

    cache = {} if no_cache else _load_cache()
    processable = [s for s in spots if _is_processable(s)]
    log.info("processable: %d / %d spots, %d cached",
             len(processable), len(spots), len(cache))

    # Build the "to process" list, respecting the limit.
    todo = [s for s in processable if _spot_key(s) not in cache or no_cache]
    if limit is not None:
        todo = todo[:limit]
    log.info("will query %d spots in batches of %d", len(todo), batch_size)

    batches = [todo[i:i + batch_size] for i in range(0, len(todo), batch_size)]
    errors = 0
    for bi, batch in enumerate(batches, start=1):
        log.info("batch %d/%d (%d spots)", bi, len(batches), len(batch))
        try:
            answers = _classify_batch(client, batch)
        except Exception:  # noqa: BLE001
            log.exception("batch %d: API call failed", bi)
            errors += 1
            continue
        for spot, answer in zip(batch, answers):
            cache[_spot_key(spot)] = answer or {"error": "unparseable_entry"}
        _save_cache(cache)

    # Compute audit + corrections rows from the cached answers.
    results: list[dict] = []
    summary = {"pass": 0, "flag_20": 0, "errors": 0, "no_orientation": 0}
    corrections: dict[str, dict] = {}

    for spot in processable:
        key = _spot_key(spot)
        cached = cache.get(key)
        if not cached or cached.get("error") or "orientation_deg" not in cached:
            summary["errors"] += 1
            results.append({
                "name": spot.get("name"),
                "our_orient": spot.get("orientation_deg"),
                "claude_orient": None,
                "diff": None,
                "confidence": None,
                "status": "ERROR",
            })
            continue

        claude_deg = float(cached["orientation_deg"])
        confidence = cached.get("confidence", "low")
        our_orient = spot.get("orientation_deg")

        if our_orient is None:
            summary["no_orientation"] += 1
            results.append({
                "name": spot.get("name"),
                "our_orient": None,
                "claude_orient": claude_deg,
                "diff": None,
                "confidence": confidence,
                "reasoning": cached.get("reasoning"),
                "status": "NO_LOCAL_ORIENT",
            })
            continue

        diff = round(_circular_diff(float(our_orient), claude_deg), 1)
        status = "FLAG_20" if diff >= FLAG_THRESHOLD_DEG else "PASS"
        summary["pass" if status == "PASS" else "flag_20"] += 1

        results.append({
            "name": spot.get("name"),
            "our_orient": float(our_orient),
            "claude_orient": claude_deg,
            "diff": diff,
            "confidence": confidence,
            "reasoning": cached.get("reasoning"),
            "status": status,
        })

        # Only feed medium/high-confidence claude flags into the
        # corrections file — low-confidence guesses with a 20° delta
        # are at least as likely to be Claude wrong as us wrong, so
        # the maintainer shouldn't paste those without manual review.
        if status == "FLAG_20" and confidence in ("high", "medium"):
            corrections[spot["name"]] = {
                "orientation_deg": int(round(claude_deg)),
                "source": f"Claude {MODEL} verification — {confidence} confidence",
                "notes": (
                    f"Was {our_orient:.0f}° (diff {diff:.0f}°). "
                    + (cached.get("reasoning") or "")
                ).strip(),
            }

    audit_payload = {"results": results, "summary": summary, "errors": errors}
    corrections_payload = {
        "_comment": (
            "Claude-flagged orientation candidates. Review each entry, then "
            "paste the ones you trust into pipeline/data/manual_orientations.json's "
            "`orientations` map. Includes only medium/high-confidence flags "
            "with >= 20° angular difference."
        ),
        "_schema_version": 1,
        "orientations": corrections,
    }
    return audit_payload, corrections_payload


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input", type=Path, default=DEFAULT_ENRICHED_OUTPUT,
                   help="spots_enriched.json")
    p.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE,
                   help="Spots per Claude request (default 10).")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap on UNCACHED spots to process (dev).")
    p.add_argument("--no-cache", action="store_true",
                   help="Re-query every spot even if already cached.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.input.exists():
        log.error("input %s not found", args.input)
        return 2
    spots = json.loads(args.input.read_text())
    if not isinstance(spots, list):
        log.error("expected a JSON array at %s", args.input)
        return 2

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    audit, corrections = run_audit(
        spots,
        batch_size=args.batch_size,
        no_cache=args.no_cache,
        limit=args.limit,
    )

    OUTPUT_AUDIT.write_text(json.dumps(audit, indent=2) + "\n")
    OUTPUT_CORRECTIONS.write_text(json.dumps(corrections, indent=2) + "\n")

    s = audit["summary"]
    log.info(
        "done. pass=%d flag_20=%d no_orientation=%d errors=%d → %s",
        s.get("pass", 0), s.get("flag_20", 0),
        s.get("no_orientation", 0), s.get("errors", 0),
        OUTPUT_AUDIT,
    )
    log.info("corrections candidates: %d → %s",
             len(corrections["orientations"]), OUTPUT_CORRECTIONS)
    return 0 if s.get("errors", 0) == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
