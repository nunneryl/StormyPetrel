"""POST to the Next.js /api/revalidate webhook after a pipeline write.

Reads spot slugs + distinct states from Supabase and builds the path
list the frontend needs to invalidate, then sends a single POST with
the shared bearer token.

ENV:
    REVALIDATE_URL      e.g. https://stormypetrel.surf/api/revalidate
    REVALIDATE_SECRET   shared secret, same value as the Vercel env var
    SUPABASE_URL        for listing spots/states
    SUPABASE_SERVICE_KEY

CLI:
    python -m pipeline.revalidate --scope full     # spots + regions + home + map
    python -m pipeline.revalidate --scope buoys    # home + map only (3-hourly cron)
    python -m pipeline.revalidate --scope daily    # home + /reports paths

A missing REVALIDATE_URL is a no-op (logged, exit 0) so the rest of
the cron doesn't fail just because the webhook isn't wired up yet in
a particular environment.
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
import json

log = logging.getLogger("revalidate")


def list_slugs_and_states(client) -> tuple[list[str], list[str]]:
    """Return (spot_slugs, distinct_state_paths). States are lowercased."""
    slugs: list[str] = []
    states: set[str] = set()
    page = 1000
    start = 0
    while True:
        res = (
            client.table("spots")
            .select("slug,state")
            .order("id")
            .range(start, start + page - 1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        for r in rows:
            slug = r.get("slug")
            if slug:
                slugs.append(slug)
            st = r.get("state")
            if st:
                states.add(st.lower())
        if len(rows) < page:
            break
        start += page
    return slugs, sorted(states)


def build_paths(scope: str, client) -> list[str]:
    if scope == "buoys":
        # Buoy refresh moves the "now" rail on the home + map only.
        # Spot-page buoy readings catch up on the next ISR cycle.
        return ["/", "/map"]

    if scope == "daily":
        today = datetime.now(timezone.utc).date().isoformat()
        # AI reports show on home + reports index + the per-date and
        # per-date/region detail. We don't know which regions will
        # land for today's run, so revalidate the date page (parent
        # list) and let the per-region page rebuild on first hit.
        return ["/", "/reports", f"/reports/{today}"]

    # Full pipeline: full freshness sweep.
    slugs, states = list_slugs_and_states(client)
    paths = ["/", "/map", "/regions"]
    paths.extend(f"/region/{st}" for st in states)
    paths.extend(f"/spot/{s}" for s in slugs)
    return paths


def post_revalidate(url: str, secret: str, paths: list[str]) -> dict:
    body = json.dumps({"paths": paths}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {secret}",
        },
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        raw = resp.read().decode("utf-8", "replace")
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return {"ok": False, "error": "non-json response", "raw": raw[:500]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True, choices=["full", "buoys", "daily"])
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    url = os.environ.get("REVALIDATE_URL")
    secret = os.environ.get("REVALIDATE_SECRET")
    if not url or not secret:
        log.warning(
            "REVALIDATE_URL / REVALIDATE_SECRET not set; skipping webhook"
        )
        return 0

    if args.scope in ("full",):
        # Lazy supabase import so the script can run with just stdlib
        # in test/CI scopes that don't need DB access.
        from supabase import create_client

        sb_url = os.environ.get("SUPABASE_URL") or os.environ.get(
            "NEXT_PUBLIC_SUPABASE_URL"
        )
        sb_key = os.environ.get("SUPABASE_SERVICE_KEY")
        if not sb_url or not sb_key:
            log.error("SUPABASE_URL / SUPABASE_SERVICE_KEY required for --scope full")
            return 2
        client = create_client(sb_url, sb_key)
    else:
        client = None

    paths = build_paths(args.scope, client)
    log.info("Revalidating %d paths (scope=%s)", len(paths), args.scope)

    try:
        result = post_revalidate(url, secret, paths)
    except urllib.error.HTTPError as e:
        log.error("HTTP %s: %s", e.code, e.read().decode("utf-8", "replace")[:500])
        return 1
    except urllib.error.URLError as e:
        log.error("URL error: %s", e.reason)
        return 1

    log.info("Webhook response: %s", result)
    if not result.get("ok"):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
