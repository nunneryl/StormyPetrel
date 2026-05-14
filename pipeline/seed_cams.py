"""Seed the cams table from pipeline/data/cam_seed.json.

Idempotent: re-running upserts on (spot_slug, cam_name) so re-seeding
after editing the JSON updates the existing row instead of inserting a
duplicate.

For surfchex/explore rows we set `embed_url = iframe_url` and mark the
row `active` at seed time, since those URLs don't need any external
resolution. YouTube rows leave `embed_url` null until
pipeline.resolve_cams fills it from the YouTube Data API.

CLI:
    python -m pipeline.seed_cams [--input pipeline/data/cam_seed.json] [-v]

Env:
    SUPABASE_URL, SUPABASE_SERVICE_KEY — required.
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .db_import import get_client

log = logging.getLogger("pipeline.seed_cams")

DEFAULT_INPUT = Path(__file__).parent / "data" / "cam_seed.json"

REQUIRED_FIELDS = ("spot_slug", "cam_name", "provider")


def _validate(entry: dict) -> str | None:
    """Return None if the entry is well-formed; an error string otherwise."""
    for f in REQUIRED_FIELDS:
        if not entry.get(f):
            return f"missing required field {f!r}"
    provider = entry["provider"]
    if provider == "youtube" and not entry.get("channel_id"):
        return "youtube provider requires channel_id"
    display_mode = entry.get("display_mode") or _default_display_mode(provider)
    if display_mode == "embed":
        # Embed-mode entries need something to render — either an
        # iframe_url at seed time (surfchex / explore) or a
        # channel_id the resolver can chase (youtube).
        if not entry.get("iframe_url") and not entry.get("channel_id"):
            return "embed display_mode requires iframe_url or channel_id"
    elif display_mode == "link":
        # Link-mode entries need somewhere to point the user.
        if not entry.get("attribution_url"):
            return "link display_mode requires attribution_url"
    else:
        return f"unknown display_mode {display_mode!r}"
    return None


def _default_display_mode(provider: str) -> str:
    """Providers that render well in an iframe get embed mode; everyone
    else defaults to a link-out banner. Overridable per-entry."""
    return "embed" if provider in {"youtube", "explore"} else "link"


def _build_row(entry: dict) -> dict:
    """Translate one seed entry into the DB row shape."""
    provider = entry["provider"]
    iframe_url = entry.get("iframe_url")
    display_mode = entry.get("display_mode") or _default_display_mode(provider)
    # Static providers ship with embed_url ready from seed time.
    # YouTube rows wait for the resolver to fill embed_url with the
    # live video ID; the frontend renders an "offline" pane in the
    # meantime so the cam stays visible in /cams + the badge query.
    embed_url = iframe_url if iframe_url else None
    # Every cam seeds as 'active' now. The resolver and the
    # check_cams health script flip individual rows to 'offline'
    # when a stream actually goes dark — there's no need for a
    # separate 'pending' state because the spot page already
    # degrades gracefully when status='active' but embed_url is
    # missing (renders the offline pane until the resolver lands a
    # live video). Keeping rows visible on /cams + the cam badge
    # at all times matches what visitors expect.
    status = "active"
    return {
        "spot_slug": entry["spot_slug"],
        "cam_name": entry["cam_name"],
        "provider": provider,
        "channel_id": entry.get("channel_id"),
        "iframe_url": iframe_url,
        "embed_url": embed_url,
        "attribution": entry.get("attribution"),
        "attribution_url": entry.get("attribution_url"),
        "display_mode": display_mode,
        "status": status,
        "last_checked_at": datetime.now(timezone.utc).isoformat(),
    }


def seed(client, entries: list[dict]) -> tuple[int, int]:
    """Upsert each entry. Returns (written, failed)."""
    written = 0
    failed = 0
    for entry in entries:
        problem = _validate(entry)
        if problem:
            log.error("skipping %s/%s: %s",
                      entry.get("spot_slug"), entry.get("cam_name"), problem)
            failed += 1
            continue
        row = _build_row(entry)
        try:
            client.table("cams").upsert(
                row, on_conflict="spot_slug,cam_name"
            ).execute()
            written += 1
        except Exception:  # noqa: BLE001
            log.exception(
                "upsert failed for %s/%s",
                row["spot_slug"], row["cam_name"],
            )
            failed += 1
    return written, failed


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Seed the cams table from JSON.")
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                   help="Path to cam_seed.json")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.input.exists():
        log.error("seed file not found: %s", args.input)
        return 2

    payload = json.loads(args.input.read_text())
    entries = payload.get("cams") if isinstance(payload, dict) else payload
    if not isinstance(entries, list):
        log.error("expected a JSON array (or {cams: [...]})")
        return 2

    client = get_client()
    written, failed = seed(client, entries)
    log.info("done. wrote=%d, failed=%d", written, failed)
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
