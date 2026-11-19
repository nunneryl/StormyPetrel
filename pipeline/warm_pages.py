"""Load each page the full run just refreshed, once, so no visitor is served the old copy.

WHY. After a full run, pipeline/revalidate.py marks every spot and region page stale. A stale
page is not rebuilt there and then: the next request is served the OLD copy and starts the
rebuild. On 2026-11-19 Kanaha Beach and Hoʻokipa showed "Model run 31 h ago" (the 18 Nov 06Z
cycle) to a visitor at about 13:00Z, while the runs at 01:45Z and 08:15Z had written 18Z and
19 Nov 00Z; the page was rebuilt at 13:08Z, after that visit. Loading every page once right
after the refresh means this script gets the old copy and starts the rebuild, not a visitor.

HOW. One GET per path, at most WORKERS at a time, each with its own timeout. The paths are the
ones revalidate.py posted, written by its --paths-out once the webhook accepted them. The site
is REVALIDATE_URL's scheme and host (https://stormypetrel.surf), or WARM_BASE_URL if set.

NEVER FAILS THE RUN. A page that errors or times out is counted and named in the log, and the
script still exits 0: the data is already written and the page rebuilds on its next visit
anyway. Only a missing or unreadable paths file is reported as a ::warning::.

COST. About 680 requests per full run (646 spot pages, the region pages, /, /map, /regions),
four runs a day: ~2,700 a day. Each serves a cached copy and starts the rebuild the first
visitor would otherwise have started.

    python -m pipeline.warm_pages pipeline/forecast_data/revalidated_paths.json
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

log = logging.getLogger("warm_pages")

WORKERS = 4
TIMEOUT_S = 30.0
USER_AGENT = "StormyPetrel-Pipeline/0.1 (+https://stormypetrel.surf)"
NAMED_FAILURES = 10          # how many failed paths the log names


def base_url(env: dict) -> str | None:
    """The site to load: WARM_BASE_URL, else REVALIDATE_URL's scheme and host. None if neither."""
    explicit = (env.get("WARM_BASE_URL") or "").strip().rstrip("/")
    if explicit:
        return explicit
    hook = (env.get("REVALIDATE_URL") or "").strip()
    parts = urllib.parse.urlsplit(hook)
    if not parts.scheme or not parts.netloc:
        return None
    return f"{parts.scheme}://{parts.netloc}"


def fetch(url: str, timeout: float = TIMEOUT_S) -> str | None:
    """GET *url* and read the body; None when it answered 2xx, else a few words on what went wrong."""
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            r.read()
            return None
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}"
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        return f"{type(reason).__name__}: {reason}"


def warm(base: str, paths: list[str], workers: int = WORKERS, get=fetch) -> dict:
    """Load each path once, *workers* at a time. Returns {"ok": n, "failed": [(path, why)]}."""
    def one(path: str):
        return path, get(base + urllib.parse.quote(path, safe="/"))

    failed: list[tuple[str, str]] = []
    ok = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for path, why in pool.map(one, paths):
            if why is None:
                ok += 1
            else:
                failed.append((path, why))
    return {"ok": ok, "failed": failed}


def main(argv: list[str] | None = None, env: dict | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("paths_file", type=Path)
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    env = os.environ if env is None else env

    base = base_url(env)
    if base is None:
        print("::warning::warm_pages: neither WARM_BASE_URL nor REVALIDATE_URL is set; no page "
              "loaded.")
        return 0
    try:
        paths = json.loads(args.paths_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        print(f"::warning::warm_pages: could not read {args.paths_file} ({e}); no page loaded.")
        return 0
    paths = [p for p in paths if isinstance(p, str) and p.startswith("/")]

    t0 = time.monotonic()
    got = warm(base, paths, workers=max(1, args.workers))
    took = time.monotonic() - t0
    log.info("Loaded %d of %d pages from %s in %.0f s (%d at a time)",
             got["ok"], len(paths), base, took, max(1, args.workers))
    if got["failed"]:
        named = "; ".join(f"{p} {why}" for p, why in got["failed"][:NAMED_FAILURES])
        more = len(got["failed"]) - NAMED_FAILURES
        print(f"::warning::warm_pages: {len(got['failed'])} of {len(paths)} pages did not load "
              f"(each rebuilds on its next visit instead): {named}"
              + (f"; and {more} more" if more > 0 else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
