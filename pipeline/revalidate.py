"""POST to the Next.js /api/revalidate webhook after a pipeline write.

Three scopes mirror the three cron jobs:

    --scope full   Diffs the just-written current-hour rating per spot
                   against a pre-write snapshot (see --snapshot below).
                   Posts /, /map, /regions, plus /region/<state> for
                   each state with at least one changed spot, plus
                   /spot/<slug> for each spot whose stars or face_ft
                   moved this cycle.
    --scope buoys  Posts /, /map only (3-hourly cron — spot pages can
                   wait for their normal ISR refresh).
    --scope daily  Posts /, /reports, /reports/<today>.

    --snapshot     Read current-hour stars + effective_size_ft per spot
                   from Supabase and write to the given JSON file.
                   Run this BEFORE the db_import step so the diff in
                   `--scope full` can compare against the previous
                   cycle's values.

ENV:
    REVALIDATE_URL      e.g. https://stormypetrel.surf/api/revalidate
    REVALIDATE_SECRET   shared secret, same value as the Vercel env var
    SUPABASE_URL        for listing spots/states and reading forecasts
    SUPABASE_SERVICE_KEY

A missing REVALIDATE_URL/SECRET is a no-op (logged, exit 0) so the
rest of the cron doesn't fail just because the webhook isn't wired
up yet in a particular environment.

A missing snapshot file in `--scope full` falls back to the old
"revalidate everything" behavior with a warning, so the very first
run after this script is deployed still works.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

log = logging.getLogger("revalidate")

# A spot is considered "changed" if its current-hour breaker face moves
# by more than FACE_FT_TOLERANCE feet OR if its star rating moves by at
# least STARS_TOLERANCE. Stars are emitted in 0.5 increments by the
# interpret stage, so anything below 0.5 is below the resolution of the
# rating system itself; bumping to 0.5 also guards against any future
# change that lets stars take finer values without us re-tuning here.
FACE_FT_TOLERANCE = 0.3
STARS_TOLERANCE = 0.5


# ---------------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------------

def get_client():
    """Lazy-import the supabase client; raise a clear error if env is wrong."""
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL") or os.environ.get(
        "NEXT_PUBLIC_SUPABASE_URL"
    )
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise SystemExit(
            "SUPABASE_URL / SUPABASE_SERVICE_KEY required for this command"
        )
    return create_client(url, key)


def fetch_spot_meta(client) -> dict[int, dict]:
    """spot_id -> {slug, state} for every spot."""
    out: dict[int, dict] = {}
    page = 1000
    start = 0
    while True:
        res = (
            client.table("spots")
            .select("id,slug,state")
            .order("id")
            .range(start, start + page - 1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        for r in rows:
            if r.get("id") is not None:
                out[r["id"]] = {
                    "slug": r.get("slug"),
                    "state": (r.get("state") or "").lower() or None,
                }
        if len(rows) < page:
            break
        start += page
    return out


# Forward window for "each spot's soonest row at or after now".
#
# CHOSEN FROM THE WRITE GRIDS, not from a guess. Two writers populate `forecasts`:
#   source='nwps'  — db_import.import_forecasts, one row per interpret ratings hour.
#                    The NWPS CG1 series is HOURLY over f000..f144, so consecutive
#                    rows for a spot are 1 h apart out to ~145 h.
#   source='ecmwf' — ecmwf_wam.upsert_forecasts on ecmwf_wam.WAVE_STEPS, which is
#                    3-HOURLY to 144 h then 6-HOURLY to the 240 h horizon.
# The widest gap between consecutive rows, for any spot that has future rows at
# all, is therefore 6 h (the ecmwf tail). This bound is 2x that worst gap, so a
# spot with any live forecast is guaranteed at least one row inside the window.
# The guarantee is structural — derived from the grids both writers emit — rather
# than sampled, so it does not depend on the state of one particular cycle.
#
# WHY THE BOUND EXISTS AT ALL: without an upper bound the query selects every
# future row (measured: 120,812 across ~121 pages) in order to use at most one per
# spot (648) — 99.5% is fetched and then discarded by the `sid in out` guard below.
# Deep offset paging cost 6.8 ms at offset 1000 and 7342 ms at offset 86,000, and
# the cumulative cost across the page sequence exceeded the server statement
# timeout. Bounded, the same query returns roughly 648 x (12 hourly + 4 three-
# hourly) = ~10k rows over ~11 pages, keeping every offset in the cheap regime.
#
# Widening is cheap and safe; narrowing below 6 h is not, because the failure mode
# of too-narrow is SILENT — a spot missing from the result never gets its page
# revalidated and quietly serves stale content.
CURRENT_HOUR_WINDOW_HOURS = 12


def fetch_current_hour_ratings(client) -> dict[int, dict]:
    """For each spot, the soonest forecast row in [now, now + CURRENT_HOUR_WINDOW_HOURS].

    Returns spot_id -> {stars, face_ft}. A spot with no row inside the window is
    ABSENT from the mapping — it is never present with null values — so the caller
    can tell "no forecast" from "a forecast that happens to be null".
    """
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()
    until_iso = (now + timedelta(hours=CURRENT_HOUR_WINDOW_HOURS)).isoformat()
    out: dict[int, dict] = {}
    # Ascending valid_time plus first-row-per-spot-wins gives each spot its soonest
    # row; every later row for that spot is dropped by the `sid in out` guard. With
    # the window bounded this is ~11 pages, so plain offset paging is no longer the
    # bottleneck and is kept as-is.
    page = 1000
    start = 0
    while True:
        res = (
            client.table("forecasts")
            .select("spot_id,valid_time,stars,effective_size_ft")
            .gte("valid_time", now_iso)
            .lte("valid_time", until_iso)
            .order("valid_time", desc=False)
            .range(start, start + page - 1)
            .execute()
        )
        rows = res.data or []
        if not rows:
            break
        for r in rows:
            sid = r.get("spot_id")
            if sid is None or sid in out:
                continue
            out[sid] = {
                "stars": r.get("stars"),
                "face_ft": r.get("effective_size_ft"),
            }
        if len(rows) < page:
            break
        start += page
    # Make a shortfall VISIBLE. A spot absent here never gets its page revalidated,
    # which is silent in the old code — the run just posts fewer paths and nobody
    # notices the stale page.
    if not out:
        log.warning(
            "No forecast rows in the next %dh — every spot page will be treated as "
            "unchanged. Check that db_import wrote this cycle.",
            CURRENT_HOUR_WINDOW_HOURS,
        )
    else:
        log.info("Current-hour ratings: %d spots inside the next %dh window",
                 len(out), CURRENT_HOUR_WINDOW_HOURS)
    return out


# ---------------------------------------------------------------------------
# Diff
# ---------------------------------------------------------------------------

def _rating_changed(old: dict | None, new: dict) -> bool:
    """True if stars moved by >= STARS_TOLERANCE OR face_ft moved by > FACE_FT_TOLERANCE.

    A null-to-value transition on either field counts as a change too —
    the spot card visibly looks different when stars or face_ft flip
    between "—" and a number.
    """
    if old is None:
        return True

    o_stars = old.get("stars")
    n_stars = new.get("stars")
    if (o_stars is None) != (n_stars is None):
        return True
    if o_stars is not None and n_stars is not None:
        try:
            if abs(float(n_stars) - float(o_stars)) >= STARS_TOLERANCE:
                return True
        except (TypeError, ValueError):
            if o_stars != n_stars:
                return True

    o_fft = old.get("face_ft")
    n_fft = new.get("face_ft")
    if (o_fft is None) != (n_fft is None):
        return True
    if o_fft is None and n_fft is None:
        return False
    try:
        return abs(float(o_fft) - float(n_fft)) > FACE_FT_TOLERANCE
    except (TypeError, ValueError):
        return o_fft != n_fft


def build_full_paths(client, snapshot_path: Path | None) -> tuple[list[str], dict]:
    """Diff snapshot against current DB. Return (paths, stats).

    If `snapshot_path` is missing or unreadable, falls back to "revalidate
    every spot" so the first run after deploy still works.
    """
    new_ratings = fetch_current_hour_ratings(client)
    spot_meta = fetch_spot_meta(client)

    fell_back = False
    if snapshot_path and snapshot_path.exists():
        try:
            raw = json.loads(snapshot_path.read_text())
            # JSON object keys are always strings — normalize back to int.
            old_ratings = {int(k): v for k, v in raw.items() if v is not None}
        except (OSError, ValueError, TypeError) as e:
            log.warning("Could not read snapshot %s (%s); falling back to revalidate-all", snapshot_path, e)
            old_ratings = {}
            fell_back = True
    else:
        log.warning(
            "Snapshot %s missing; falling back to revalidate-all (expected on first run only)",
            snapshot_path,
        )
        old_ratings = {}
        fell_back = True

    changed_spots: list[str] = []
    affected_states: set[str] = set()
    unchanged = 0
    for sid, new in new_ratings.items():
        meta = spot_meta.get(sid)
        if not meta or not meta.get("slug"):
            continue
        old = old_ratings.get(sid)
        if fell_back or _rating_changed(old, new):
            changed_spots.append(meta["slug"])
            if meta.get("state"):
                affected_states.add(meta["state"])
        else:
            unchanged += 1

    paths: list[str] = ["/", "/map", "/regions"]
    paths.extend(f"/region/{st}" for st in sorted(affected_states))
    paths.extend(f"/spot/{s}" for s in changed_spots)

    stats = {
        "total_spots": len(new_ratings),
        "changed_spots": len(changed_spots),
        "unchanged_spots": unchanged,
        "affected_states": len(affected_states),
        "fell_back_to_all": fell_back,
    }
    return paths, stats


# ---------------------------------------------------------------------------
# Webhook
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------

def cmd_snapshot(out_path: Path) -> int:
    """Snapshot current-hour ratings per spot before db_import runs."""
    client = get_client()
    snap = fetch_current_hour_ratings(client)
    # JSON object keys must be strings; readers normalize back.
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({str(k): v for k, v in snap.items()}))
    log.info("Snapshot wrote %d spot ratings to %s", len(snap), out_path)
    return 0


def cmd_revalidate(scope: str, snapshot_path: Path | None) -> int:
    url = os.environ.get("REVALIDATE_URL")
    secret = os.environ.get("REVALIDATE_SECRET")
    if not url or not secret:
        log.warning("REVALIDATE_URL / REVALIDATE_SECRET not set; skipping webhook")
        return 0

    if scope == "buoys":
        paths = ["/", "/map"]
        stats: dict = {}
    elif scope == "daily":
        today = datetime.now(timezone.utc).date().isoformat()
        paths = ["/", "/reports", f"/reports/{today}"]
        stats = {}
    elif scope == "full":
        client = get_client()
        paths, stats = build_full_paths(client, snapshot_path)
    else:
        log.error("unknown scope: %s", scope)
        return 2

    if stats:
        log.info(
            "Full diff: %d/%d spots changed (%d unchanged), %d states affected%s",
            stats["changed_spots"],
            stats["total_spots"],
            stats["unchanged_spots"],
            stats["affected_states"],
            " [fell back to revalidate-all]" if stats["fell_back_to_all"] else "",
        )
    log.info("Posting %d paths (scope=%s)", len(paths), scope)

    try:
        result = post_revalidate(url, secret, paths)
    except urllib.error.HTTPError as e:
        log.error("HTTP %s: %s", e.code, e.read().decode("utf-8", "replace")[:500])
        return 1
    except urllib.error.URLError as e:
        log.error("URL error: %s", e.reason)
        return 1

    log.info("Webhook response: %s", result)
    return 0 if result.get("ok") else 1


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verbose", action="store_true")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--scope",
        choices=["full", "buoys", "daily"],
        help="Build paths for one of the three cron scopes and POST.",
    )
    group.add_argument(
        "--snapshot",
        type=Path,
        metavar="OUT",
        help="Snapshot current-hour ratings per spot to a JSON file.",
    )
    parser.add_argument(
        "--diff-from",
        type=Path,
        metavar="SNAPSHOT",
        default=None,
        help="When --scope=full, JSON file written by an earlier --snapshot.",
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    if args.snapshot:
        return cmd_snapshot(args.snapshot)
    return cmd_revalidate(args.scope, args.diff_from)


if __name__ == "__main__":
    sys.exit(main())
