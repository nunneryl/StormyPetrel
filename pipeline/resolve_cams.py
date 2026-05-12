"""Resolve YouTube-live cams to a current video ID.

For each unique channel_id in the cams table this script calls the
YouTube Data API v3 `search.list?eventType=live` endpoint, picks the
first live video, and writes its ID + embed URL back to every cams row
that uses that channel.

When no live video is found we mark the row `offline` but keep its
prior embed_url — the spot page renders an "offline" message rather
than a broken iframe.

Surfchex / explore rows are left alone (their embed_url is set at seed
time and doesn't need refreshing).

CLI:
    python -m pipeline.resolve_cams [--dry-run] [-v]

Env:
    SUPABASE_URL, SUPABASE_SERVICE_KEY — required.
    YOUTUBE_API_KEY                    — required.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timezone
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .db_import import get_client

log = logging.getLogger("pipeline.resolve_cams")

YT_SEARCH_URL = "https://www.googleapis.com/youtube/v3/search"


def fetch_live_video_id(channel_id: str, api_key: str) -> str | None:
    """Hit the YouTube Data API for the first live video on a channel.
    Returns the video ID, or None if the channel has no live stream now.
    """
    params = {
        "part": "id",
        "channelId": channel_id,
        "eventType": "live",
        "type": "video",
        "maxResults": "1",
        "key": api_key,
    }
    url = f"{YT_SEARCH_URL}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": "StormyPetrel/cams-resolver"})
    with urlopen(req, timeout=15) as resp:
        body = resp.read()
    import json
    payload = json.loads(body)
    items = payload.get("items") or []
    if not items:
        return None
    return items[0].get("id", {}).get("videoId")


def fetch_youtube_cams(client) -> list[dict]:
    resp = (
        client.table("cams")
        .select("id, spot_slug, cam_name, channel_id, embed_url, status")
        .eq("provider", "youtube")
        .execute()
    )
    return resp.data or []


def group_by_channel(rows: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        ch = r.get("channel_id")
        if not ch:
            continue
        out.setdefault(ch, []).append(r)
    return out


def update_rows(client, ids: list[int], updates: dict) -> None:
    if not ids:
        return
    client.table("cams").update(updates).in_("id", ids).execute()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Refresh YouTube-live cam embeds.")
    p.add_argument("--dry-run", action="store_true",
                   help="Call the YouTube API but skip the Supabase update.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    api_key = os.environ.get("YOUTUBE_API_KEY")
    if not api_key:
        log.error("YOUTUBE_API_KEY is required")
        return 1

    client = get_client()
    rows = fetch_youtube_cams(client)
    by_channel = group_by_channel(rows)
    if not by_channel:
        log.info("no youtube cams to resolve")
        return 0

    log.info("resolving %d youtube channel(s) covering %d cam row(s)",
             len(by_channel), sum(len(v) for v in by_channel.values()))

    now_iso = datetime.now(timezone.utc).isoformat()
    live_count = 0
    offline_count = 0
    failed_channels = 0

    for channel_id, channel_rows in by_channel.items():
        ids = [r["id"] for r in channel_rows]
        try:
            video_id = fetch_live_video_id(channel_id, api_key)
        except Exception:  # noqa: BLE001
            log.exception("youtube API call failed for channel %s", channel_id)
            failed_channels += 1
            if args.dry_run:
                continue
            update_rows(client, ids, {"last_checked_at": now_iso})
            continue

        if video_id:
            embed = f"https://www.youtube.com/embed/{video_id}"
            log.info("channel %s LIVE → %s (updates %d row(s))",
                     channel_id, video_id, len(ids))
            live_count += len(ids)
            if not args.dry_run:
                update_rows(client, ids, {
                    "resolved_video_id": video_id,
                    "embed_url": embed,
                    "status": "active",
                    "last_resolved_at": now_iso,
                    "last_checked_at": now_iso,
                })
        else:
            log.info("channel %s offline (no live video) — %d row(s) marked offline",
                     channel_id, len(ids))
            offline_count += len(ids)
            if not args.dry_run:
                # Keep embed_url so the frontend can still render a
                # "currently offline" preview against the last known
                # stream if it wants to.
                update_rows(client, ids, {
                    "status": "offline",
                    "last_checked_at": now_iso,
                })

    log.info("done. live=%d, offline=%d, failed_channels=%d",
             live_count, offline_count, failed_channels)
    return 0 if failed_channels == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
