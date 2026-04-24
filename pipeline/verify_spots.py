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
    SPOT_VERIFY_INTER_BATCH_SECONDS,
    SPOT_VERIFY_MAX_RETRIES,
    SPOT_VERIFY_MODEL,
    SPOT_VERIFY_RETRY_BACKOFF_SECONDS,
)

log = logging.getLogger("pipeline.verify_spots")


# Long, stable system prompt — designed to (a) elicit consistent JSON,
# (b) direct the model to search surf-forecast.com / Surfline for exact
# facing-direction / optimal-swell values rather than guessing from
# training data, and (c) clear the 2048-token Sonnet 4.6 prompt-cache
# threshold so every batch after the first reads instead of writes.
_SYSTEM_PROMPT = """You are a surf forecasting expert with deep knowledge of US surf spots,
including those documented on Surfline, surf-forecast.com, MagicSeaweed
(historical), Wannasurf, regional guidebooks, and local surf-club pages.

SEARCH FIRST, ANSWER FROM SOURCES
=================================

You have access to the web_search tool. For each spot in the user's
batch, search surf-forecast.com first to find the exact offshore wind
direction and ideal swell direction published for that break. Use the
precise degree values from the source — do NOT round to the nearest
cardinal, and do NOT estimate from memory when a documented value
exists. A typical query is:

    "<spot name>" <state> surf-forecast.com

If you cannot find the spot on surf-forecast.com, try Surfline, then
Wannasurf or regional surf-guide sites. Record the sourced values in
your JSON output at full precision (integer degrees). If no source
returns a usable match after reasonable searching, fall back to your
training-data knowledge and lower confidence to "medium" or "low".

Search efficiently — a spot often needs one or two searches, not a
deep dive. Prioritize the five fields below; don't search for peripheral
details you can reasonably infer (break_type, hazards, crowd_factor).


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

REGIONAL REFERENCE
==================

These rules of thumb help sanity-check values from surf-forecast.com
and catch obvious transcription errors:

  - Outer Banks NC (MHX / ILM): facing 90-120°, offshore winds from
    W/SW (~270-240°). Most beach breaks; Hatteras peaks are the
    named reef-like sections.
  - Virginia Beach / OBX north (AKQ): facing ~90°, classic
    Mid-Atlantic beach-break profile; light-wind summer, heavier
    winter swell from hurricanes and nor'easters.
  - Jersey Shore (PHI): facing 90-110° depending on inlet proximity;
    Manasquan Inlet, Belmar, and Ocean City (NJ) are the named
    reference breaks.
  - Long Island (OKX): south shore faces ~180°; Montauk faces closer
    to 135-170° around its points.
  - Southern California (SGX / LOX): mostly W-to-SW facing
    (225-270°); Trestles, Swamis, Malibu, and Rincon are canonical.
  - Central California (MTR): heavy W/NW swell exposure, facing
    250-290°; Steamer Lane, Mavericks, Ocean Beach SF, Pleasure Pt.
  - Pacific Northwest (PQR / SEW): facing 270°, cold-water beach and
    cobble reef breaks; Westport, Seaside Cove.
  - Hawaii — north shore Oahu: facing 310-20° (winter); south shore
    Oahu: ~170-200° (summer); east shore / windward: 60-90° (trade
    swell); west shore: 240-270° (Makaha).
  - Great Lakes: open-lake facing varies; Sheboygan/Port Washington
    face NE (~45°), Milwaukee faces ENE (~60°).

COMMON SURF-FORECAST.COM QUIRKS
===============================

  - The site sometimes uses regional alias names (e.g. "Rincon"
    redirects to the PR vs CA page depending on the search context).
    Always verify by cross-referencing the coordinates in your query.
  - surf-forecast.com lists "ideal swell direction" and "ideal wind
    direction" on each spot's detail page. The ideal wind direction
    IS the offshore_wind_deg. The ideal swell direction IS the
    optimal_swell_dir. Do not confuse the two.
  - Some spots list a range (e.g. "270-295°"). Use the midpoint.
  - If the site only shows a cardinal (e.g. "W", "SW"), convert:
    N=0, NNE=22, NE=45, ENE=67, E=90, ESE=112, SE=135, SSE=157,
    S=180, SSW=202, SW=225, WSW=247, W=270, WNW=292, NW=315, NNW=337.

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
    p.add_argument("--batch-size", type=int, default=SPOT_VERIFY_BATCH_SIZE,
                   help=f"Spots per API call (default {SPOT_VERIFY_BATCH_SIZE}). "
                        "Smaller batch = smaller per-request prompt, safer under "
                        "TPM rate limits.")
    p.add_argument("--inter-batch-seconds", type=float,
                   default=SPOT_VERIFY_INTER_BATCH_SECONDS,
                   help=f"Seconds to sleep between batches (default "
                        f"{SPOT_VERIFY_INTER_BATCH_SECONDS}). Raise to 30+ if you "
                        "see 429s. Ignored on the final batch.")
    p.add_argument("--no-cache", action="store_true",
                   help="Ignore existing verification records in memory and "
                        "reverify every spot. The on-disk file is overwritten "
                        "progressively as batches complete (crash = partial "
                        "overwrite). Use --force for a safer rename-then-rerun.")
    p.add_argument("--force", action="store_true",
                   help="Archive the existing verification file to "
                        "<name>.<timestamp>.bak and re-verify every spot "
                        "from scratch. Use this after a prompt or tooling "
                        "change invalidates prior results (e.g. switching "
                        "to a web-search-enabled flow).")
    p.add_argument("--no-merge", action="store_true",
                   help="Skip the merge step; only write the verification file.")
    p.add_argument("--merge-only", action="store_true",
                   help="Skip the API-verification step and only apply the "
                        "existing verification file to spots_enriched.json. "
                        "Does not require ANTHROPIC_API_KEY.")
    p.add_argument("--show-low-confidence", action="store_true",
                   help="Print every low-confidence verification and exit (no API calls).")
    p.add_argument("--show-invalid", action="store_true",
                   help="Print every spot flagged is_valid_surf_spot=false and exit (no API calls).")
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


class _UsageTotal:
    """Accumulator matching the subset of usage fields we record in stats.

    Mirrors the `message.usage` attributes we consume downstream so the
    caller can treat this like a single-request usage object regardless of
    how many pause_turn resumes it took to complete.
    """

    __slots__ = (
        "input_tokens", "output_tokens",
        "cache_creation_input_tokens", "cache_read_input_tokens",
    )

    def __init__(self) -> None:
        self.input_tokens = 0
        self.output_tokens = 0
        self.cache_creation_input_tokens = 0
        self.cache_read_input_tokens = 0

    def add(self, usage) -> None:
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_creation_input_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0
        self.cache_read_input_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0


# Server-side web_search runs up to ~10 tool calls per response and then
# returns `stop_reason: "pause_turn"` if the model wants more. We resume
# by echoing the assistant turn back in messages and calling again. Cap
# the total resumes so a runaway search loop can't burn the whole budget.
_MAX_PAUSE_RESUMES = 5


def _verify_batch(client, spots: list[dict]) -> tuple[list[dict], _UsageTotal]:
    prompt = _build_user_prompt(spots)
    # System prompt as a list-of-blocks with cache_control so every batch
    # after the first reads the cache (~0.1× input cost) instead of
    # paying full price for the long instructions. Tools render before
    # system in the prefix, so the tool definition is part of the cache.
    system = [
        {
            "type": "text",
            "text": _SYSTEM_PROMPT,
            "cache_control": {"type": "ephemeral"},
        }
    ]
    tools = [{"type": "web_search_20250305", "name": "web_search"}]
    messages: list[dict] = [{"role": "user", "content": prompt}]

    usage_total = _UsageTotal()
    message = None
    for resume in range(_MAX_PAUSE_RESUMES + 1):
        message = client.messages.create(
            model=SPOT_VERIFY_MODEL,
            max_tokens=8192,
            system=system,
            tools=tools,
            messages=messages,
        )
        usage_total.add(message.usage)
        if message.stop_reason != "pause_turn":
            break
        # Resume: keep the user prompt + append the assistant turn (including
        # server_tool_use blocks) and let the model continue its search loop.
        messages.append({"role": "assistant", "content": message.content})
    else:
        log.warning(
            "verify: batch of %d spots exhausted %d pause_turn resumes — "
            "using whatever the last response produced",
            len(spots), _MAX_PAUSE_RESUMES,
        )

    # The model's JSON answer lives in the final text block of the last
    # response. Prior text blocks may describe its search reasoning — skip
    # those by taking the last text block only.
    text_blocks = [b.text for b in message.content if b.type == "text"]
    text = text_blocks[-1] if text_blocks else ""
    if not text:
        raise ValueError("no text block in final response")

    try:
        parsed = _parse_json_response(text)
    except json.JSONDecodeError as e:
        log.error("JSON parse failed for batch of %d spots: %s\nraw response:\n%s",
                  len(spots), e, text[:500])
        raise
    if not isinstance(parsed, list):
        raise ValueError(f"Expected JSON array, got {type(parsed).__name__}")
    return parsed, usage_total


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


def _verify_batch_with_retry(
    client,
    spots: list[dict],
    max_retries: int,
    backoff_seconds: float,
) -> tuple[list[dict], _UsageTotal]:
    """Call _verify_batch, retrying on 429 rate-limit errors.

    Uses the Anthropic SDK's typed RateLimitError and honors the server's
    ``retry-after`` header when present; otherwise falls back to the
    configured backoff. Non-rate-limit exceptions are raised to the caller
    so the existing batch-failed log path handles them.
    """
    import anthropic
    import time

    for attempt in range(max_retries + 1):
        try:
            return _verify_batch(client, spots)
        except anthropic.RateLimitError as e:
            if attempt >= max_retries:
                raise
            wait_s = backoff_seconds
            # Server-provided retry-after wins when present, capped at 120s
            # so a pathological header can't park us forever.
            try:
                ra = e.response.headers.get("retry-after") if e.response is not None else None
                if ra is not None:
                    wait_s = min(120.0, max(wait_s, float(ra)))
            except (ValueError, AttributeError):
                pass
            log.warning(
                "verify: 429 rate-limited on %d-spot batch (attempt %d/%d); "
                "sleeping %.1fs before retry",
                len(spots), attempt + 1, max_retries, wait_s,
            )
            time.sleep(wait_s)
    # Unreachable — the final iteration either returns or raises.
    raise RuntimeError("unreachable")


def verify_all(
    spots: list[dict],
    verification_path: Path,
    use_cache: bool = True,
    limit: int | None = None,
    batch_size: int = SPOT_VERIFY_BATCH_SIZE,
    inter_batch_seconds: float = SPOT_VERIFY_INTER_BATCH_SECONDS,
    max_retries: int = SPOT_VERIFY_MAX_RETRIES,
    retry_backoff_seconds: float = SPOT_VERIFY_RETRY_BACKOFF_SECONDS,
) -> tuple[dict[str, dict], dict]:
    """Return (name -> verification record, stats). Persists to *verification_path*.

    Paces itself for the 30K input-TPM rate limit: small batches + a sleep
    between calls + bounded retry on 429.
    """
    import anthropic
    import time

    cache = _load_verifications(verification_path) if use_cache else {}
    pending = [s for s in spots if s["name"] not in cache]
    if limit is not None:
        pending = pending[:limit]
    log.info(
        "verify: %d spots total, %d cached, %d to verify "
        "(batch_size=%d, inter_batch=%.1fs, retries=%d)",
        len(spots), len(spots) - len(pending), len(pending),
        batch_size, inter_batch_seconds, max_retries,
    )

    stats = {
        "input_tokens": 0,
        "output_tokens": 0,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 0,
        "batches": 0,
        "parse_errors": 0,
        "rate_limit_retries": 0,
        "missing_from_response": 0,
    }
    if not pending:
        return cache, stats

    client = anthropic.Anthropic()

    batches = [
        pending[i : i + batch_size]
        for i in range(0, len(pending), batch_size)
    ]
    try:
        from tqdm import tqdm
        iterator = tqdm(batches, desc="verify spots", unit="batch")
    except ImportError:
        iterator = batches

    for batch_idx, batch in enumerate(iterator):
        try:
            parsed, usage = _verify_batch_with_retry(
                client, batch, max_retries, retry_backoff_seconds,
            )
        except anthropic.RateLimitError as e:
            log.error(
                "verify: batch of %d spots exhausted %d rate-limit retries: %s",
                len(batch), max_retries, e,
            )
            stats["parse_errors"] += 1
            stats["rate_limit_retries"] += max_retries
            continue
        except Exception as e:  # noqa: BLE001
            log.warning("batch failed (%d spots): %s", len(batch), e)
            stats["parse_errors"] += 1
            continue

        stats["batches"] += 1
        stats["input_tokens"] += usage.input_tokens
        stats["output_tokens"] += usage.output_tokens
        stats["cache_creation_input_tokens"] += usage.cache_creation_input_tokens
        stats["cache_read_input_tokens"] += usage.cache_read_input_tokens

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

        # Pace under the input-TPM rate limit. Skip the sleep on the last
        # batch so the run ends promptly.
        if inter_batch_seconds > 0 and batch_idx < len(batches) - 1:
            time.sleep(inter_batch_seconds)

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
        # Manual-orientation spots (orientation_source=="manual") keep their
        # hand-curated bearing — the LLM doesn't beat human review.
        # If orientation changes, also clear orientation-derived swell-window
        # arcs so a subsequent `enrich --skip-raycast` rebuilds them from
        # the corrected orientation.
        orient_locked = spot.get("orientation_source") == "manual"

        new_orient = rec.get("facing_direction_deg")
        if new_orient is not None and not orient_locked and new_orient != spot.get("orientation_deg"):
            spot["orientation_deg"] = new_orient
            stats["field_changes"]["orientation_deg"] += 1
            if spot.get("swell_window_source") == "orientation_derived":
                spot["swell_window_arcs"] = []
                spot["optimal_swell_dir"] = None
                spot.pop("swell_window_source", None)

        new_offshore = rec.get("offshore_wind_deg")
        if new_offshore is not None and not orient_locked and new_offshore != spot.get("offshore_wind_deg"):
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
    if api_stats.get("rate_limit_retries"):
        print(f"  rate-limit retries:   {api_stats['rate_limit_retries']}")
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


def _print_low_confidence(verifications: dict[str, dict]) -> None:
    low = [rec for rec in verifications.values() if rec.get("confidence") == "low"]
    low.sort(key=lambda r: r.get("name") or "")
    print(f"Low-confidence spots ({len(low)}):")
    print("-" * 60)
    for rec in low:
        name = rec.get("name") or "?"
        notes = (rec.get("notes") or "").strip() or "(no notes)"
        print(f"  {name}")
        print(f"      notes: {notes}")


def _print_invalid(verifications: dict[str, dict]) -> None:
    invalid = [rec for rec in verifications.values() if not rec.get("is_valid_surf_spot", True)]
    invalid.sort(key=lambda r: (r.get("invalid_reason") or "", r.get("name") or ""))
    print(f"Invalid spots ({len(invalid)}):")
    print("-" * 60)
    current_reason = None
    for rec in invalid:
        reason = rec.get("invalid_reason") or "?"
        if reason != current_reason:
            print(f"\n[{reason}]")
            current_reason = reason
        name = rec.get("name") or "?"
        notes = (rec.get("notes") or "").strip()
        if notes:
            print(f"  {name}  —  {notes}")
        else:
            print(f"  {name}")


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Display-only flags — read the verification file and exit. No API
    # calls, no merge, no ANTHROPIC_API_KEY requirement.
    if args.show_low_confidence or args.show_invalid:
        if not args.verification_file.exists():
            log.error("Verification file %s does not exist. Run verify_spots without "
                      "--show-* flags first.", args.verification_file)
            return 1
        verifications = _load_verifications(args.verification_file)
        if args.show_low_confidence:
            _print_low_confidence(verifications)
        if args.show_invalid:
            if args.show_low_confidence:
                print()
            _print_invalid(verifications)
        return 0

    if not args.input.exists():
        log.error("Input file %s does not exist. Run `python -m pipeline.enrich` first.",
                  args.input)
        return 1

    spots = json.loads(args.input.read_text())
    log.info("Loaded %d enriched spots from %s", len(spots), args.input)

    # --merge-only: skip the API step and go straight to merging the
    # existing verification file into spots_enriched.json. No API key
    # required.
    if args.merge_only:
        verifications = _load_verifications(args.verification_file)
        log.info("--merge-only: %d verification records from %s",
                 len(verifications), args.verification_file)
        api_stats = {
            "input_tokens": 0, "output_tokens": 0,
            "cache_creation_input_tokens": 0, "cache_read_input_tokens": 0,
            "batches": 0, "parse_errors": 0, "rate_limit_retries": 0,
            "missing_from_response": 0,
        }
    else:
        if not os.getenv("ANTHROPIC_API_KEY"):
            log.error("ANTHROPIC_API_KEY is not set. Export it before running.")
            return 1

        # --force: move the existing verification file aside so (a) the next
        # verify_all starts from an empty cache, and (b) if the re-run crashes
        # mid-way the user can recover the prior results from the .bak file.
        if args.force and args.verification_file.exists():
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            backup = args.verification_file.with_suffix(
                args.verification_file.suffix + f".{timestamp}.bak"
            )
            args.verification_file.rename(backup)
            log.info("--force: archived previous verification file to %s", backup)

        verifications, api_stats = verify_all(
            spots,
            verification_path=args.verification_file,
            use_cache=not (args.no_cache or args.force),
            limit=args.limit,
            batch_size=args.batch_size,
            inter_batch_seconds=args.inter_batch_seconds,
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
