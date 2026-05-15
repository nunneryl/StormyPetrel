"""Generate short factual spot descriptions with Claude.

Walks every spot in spots_enriched.json, sends its metadata to Claude
with a tight prompt that bans subjective wave-quality claims, and
writes the result into both pipeline/data/spot_descriptions.json
(idempotent cache) and the spots.description column in Supabase.

Re-runs are cheap: cached slugs are skipped unless --no-cache forces
a regeneration. The cache file itself doubles as the result file —
no second filesystem location to keep in sync.

CLI:
    python -m pipeline.generate_descriptions [--limit N] [--no-cache]
                                             [--no-db] [-v]

Env:
    ANTHROPIC_API_KEY  — required.
    SUPABASE_URL, SUPABASE_SERVICE_KEY — required unless --no-db.

Cost estimate: 485 spots × ~500 in + ~120 out tokens at
Sonnet 4.6 pricing ≈ ~$2 per full regeneration.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

import anthropic

from .config import DEFAULT_ENRICHED_OUTPUT
from .db_import import _slugify, get_client

log = logging.getLogger("pipeline.generate_descriptions")

MODEL = "claude-sonnet-4-6"
MAX_TOKENS = 200  # 75-word cap is ~110 tokens; leaves headroom.

OUTPUT_FILE = Path(__file__).parent / "data" / "spot_descriptions.json"

SYSTEM_PROMPT = (
    "Write a 2-3 sentence factual description of this surf spot using "
    "ONLY the provided metadata. State the type of break, what direction "
    "it faces, what swell window it picks up, what tide it prefers, and "
    "what wind direction is offshore. Do not make claims about wave "
    "quality, consistency, barrel potential, crowd levels, skill level, "
    "or how the wave breaks beyond what the data directly supports. Do "
    "not say 'one of the best' or 'most popular' or 'iconic'. Keep it "
    "factual and concise. No more than 75 words."
)

_CARDINALS_8 = ["N", "NE", "E", "SE", "S", "SW", "W", "NW"]


def _deg_to_cardinal(deg: float | None) -> str | None:
    if deg is None:
        return None
    idx = round((deg % 360) / 45) % 8
    return _CARDINALS_8[idx]


def _arcs_to_readable(arcs: list[dict] | None) -> list[str]:
    """[{min: 168, max: 328}] -> ['SSE..NW']. 8-point cardinals so the
    range reads as a directional sweep rather than a math problem."""
    out: list[str] = []
    for arc in arcs or []:
        a = _deg_to_cardinal(arc.get("min"))
        b = _deg_to_cardinal(arc.get("max"))
        if a and b:
            out.append(f"{a}..{b}")
    return out


def _build_user_prompt(spot: dict) -> str:
    """JSON-stringified metadata. Compact — the model doesn't need
    pretty-printed prose, and tokens are tight."""
    payload = {
        "name": spot.get("name"),
        "state": spot.get("region_hint"),
        "break_type": spot.get("break_type"),
        "faces": _deg_to_cardinal(spot.get("orientation_deg")),
        "orientation_deg": spot.get("orientation_deg"),
        "offshore_wind_from": _deg_to_cardinal(spot.get("offshore_wind_deg")),
        "offshore_wind_deg": spot.get("offshore_wind_deg"),
        "tide_preference": spot.get("tide_preference"),
        "swell_window": _arcs_to_readable(spot.get("swell_window_arcs")),
        "optimal_swell_from": _deg_to_cardinal(spot.get("optimal_swell_dir")),
        "nearest_buoy_id": spot.get("nearest_buoy_id"),
        "nearest_tide_station_id": spot.get("nearest_tide_station_id"),
    }
    # Strip null/empty entries so the model doesn't waste attention on
    # missing data fields ("offshore_wind_from": null).
    payload = {k: v for k, v in payload.items() if v not in (None, "", [])}
    return json.dumps(payload, ensure_ascii=False)


def _load_cache(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except json.JSONDecodeError as e:
        log.warning("cache %s corrupt (%s) — starting fresh", path, e)
        return {}


def _save_cache(path: Path, data: dict[str, str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Stable key order so diffs read cleanly.
    ordered = {k: data[k] for k in sorted(data)}
    path.write_text(json.dumps(ordered, indent=2, ensure_ascii=False) + "\n")


def _strip_quotes(text: str) -> str:
    """Models sometimes wrap the answer in straight or smart quotes."""
    t = text.strip()
    if len(t) >= 2 and t[0] in "\"'“‘" and t[-1] in "\"'”’":
        return t[1:-1].strip()
    return t


def _generate_one(client: anthropic.Anthropic, spot: dict) -> str:
    prompt = _build_user_prompt(spot)
    message = client.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": prompt}],
    )
    text = next((b.text for b in message.content if b.type == "text"), "")
    cleaned = _strip_quotes(text)
    if not cleaned:
        raise RuntimeError("empty response")
    return cleaned


def _upsert_descriptions(client, descriptions: dict[str, str]) -> int:
    """Push descriptions to Supabase keyed on slug. Returns count
    updated. We do one update per spot because Supabase REST upsert
    needs the full row schema; updating a single column on existing
    rows is simpler with .update().eq()."""
    written = 0
    for slug, desc in descriptions.items():
        try:
            client.table("spots").update({"description": desc}).eq("slug", slug).execute()
            written += 1
        except Exception:  # noqa: BLE001
            log.exception("DB update failed for %s", slug)
    return written


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input", type=Path, default=DEFAULT_ENRICHED_OUTPUT,
                   help="spots_enriched.json")
    p.add_argument("--output", type=Path, default=OUTPUT_FILE,
                   help="JSON file storing {slug: description}")
    p.add_argument("--limit", type=int, default=None,
                   help="Cap on spots to process this run (dev).")
    p.add_argument("--no-cache", action="store_true",
                   help="Ignore existing descriptions and regenerate.")
    p.add_argument("--no-db", action="store_true",
                   help="Write the JSON file but skip the Supabase upsert.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not os.environ.get("ANTHROPIC_API_KEY"):
        log.error("ANTHROPIC_API_KEY is required")
        return 1

    spots = json.loads(args.input.read_text())
    if not isinstance(spots, list):
        log.error("expected a JSON array at %s", args.input)
        return 2

    cache = {} if args.no_cache else _load_cache(args.output)
    log.info("loaded %d spots; %d already cached", len(spots), len(cache))

    anthropic_client = anthropic.Anthropic()

    generated = 0
    skipped = 0
    failed = 0
    new_entries: dict[str, str] = {}

    for spot in spots:
        name = spot.get("name")
        if not name:
            continue
        slug = _slugify(name)
        if not slug:
            continue
        if slug in cache and not args.no_cache:
            skipped += 1
            continue
        if args.limit is not None and generated >= args.limit:
            break

        try:
            desc = _generate_one(anthropic_client, spot)
        except Exception:  # noqa: BLE001
            log.exception("%s: generation failed", slug)
            failed += 1
            continue

        cache[slug] = desc
        new_entries[slug] = desc
        generated += 1
        log.info("%s: %s", slug, desc[:80] + ("…" if len(desc) > 80 else ""))

        # Snapshot the cache every 25 entries so a crash doesn't burn
        # all the in-flight work.
        if generated % 25 == 0:
            _save_cache(args.output, cache)

    _save_cache(args.output, cache)
    log.info("cache written → %s (generated=%d skipped=%d failed=%d)",
             args.output, generated, skipped, failed)

    if args.no_db:
        log.info("--no-db: skipping Supabase upsert")
        return 0 if failed == 0 else 1

    # Push the rows we touched this run. Skipping --no-db is the way
    # to do a JSON-only dry run; otherwise we always sync the new
    # generations even if --no-cache wasn't passed.
    payload = new_entries if new_entries else cache
    if not payload:
        log.info("nothing to push to Supabase")
        return 0
    log.info("pushing %d description(s) to Supabase", len(payload))
    db = get_client()
    written = _upsert_descriptions(db, payload)
    log.info("upserted %d row(s)", written)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
