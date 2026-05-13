"""One-shot health check for every cam's attribution_url.

Walks every row in `cams`, sends a HEAD (falling back to GET on 405)
to attribution_url with a 10s timeout, and buckets each result:

  ok        — 200..299
  redirect  — 300..399 (final URL printed for review)
  dead      — 400+, timeout, DNS / SSL / connection error

Results get written to pipeline/data/cam_health.json alongside the
existing cam_seed / cam_discovery files.

CLI:
    python -m pipeline.check_cams [--fix] [--delay S] [-v]

Env:
    SUPABASE_URL, SUPABASE_SERVICE_KEY — required.

The `--fix` flag flips status='offline' for every cam in the `dead`
bucket. Redirects are NOT auto-flipped — providers often add a vanity
host or move pages around without taking the cam down, so a human
should look at those before disabling.
"""
from __future__ import annotations

import argparse
import json
import logging
import socket
import ssl
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

from .db_import import get_client

log = logging.getLogger("pipeline.check_cams")

DEFAULT_OUTPUT = Path(__file__).parent / "data" / "cam_health.json"
REQUEST_TIMEOUT = 10
UA = "StormyPetrel/check-cams (+https://stormypetrel.surf)"


def _request(url: str, method: str) -> tuple[int, str]:
    """Return (status_code, final_url). Raises on connection failures."""
    req = Request(url, method=method, headers={
        "User-Agent": UA,
        "Accept": "*/*",
    })
    with urlopen(req, timeout=REQUEST_TIMEOUT) as resp:
        return resp.status, resp.geturl()


def check_url(url: str) -> dict:
    """Probe one URL, return a result row (no DB side effects)."""
    out = {
        "url": url,
        "bucket": "dead",
        "status_code": None,
        "final_url": None,
        "error": None,
    }
    if not url:
        out["error"] = "missing attribution_url"
        return out

    try:
        try:
            code, final = _request(url, "HEAD")
        except HTTPError as e:
            # Some servers reject HEAD (405) or 501. Retry with GET; we
            # still don't pull the body — urlopen lets us read 0 bytes.
            if e.code in (405, 501):
                code, final = _request(url, "GET")
            else:
                # Treat 3xx as a redirect bucket. urlopen follows by
                # default so we usually land here only on a 4xx/5xx;
                # 3xx hitting this branch is rare but valid.
                out["status_code"] = e.code
                out["final_url"] = e.url
                if 300 <= e.code < 400:
                    out["bucket"] = "redirect"
                return out
    except (URLError, socket.timeout, ssl.SSLError, TimeoutError, ConnectionError, OSError) as e:
        out["error"] = repr(e)
        return out

    out["status_code"] = code
    out["final_url"] = final
    if 200 <= code < 300:
        # urlopen followed redirects silently — flag if the final URL
        # differs from what we asked for so a human can decide to
        # update attribution_url.
        out["bucket"] = "redirect" if final and final.rstrip("/") != url.rstrip("/") else "ok"
    elif 300 <= code < 400:
        out["bucket"] = "redirect"
    else:
        out["bucket"] = "dead"
    return out


def fetch_cams(client) -> list[dict]:
    """Pull every cam — we don't filter by status because we want to
    catch already-offline rows whose URL has since come back up."""
    out: list[dict] = []
    page = 1000
    frm = 0
    while True:
        resp = (
            client.table("cams")
            .select("id, spot_slug, cam_name, provider, attribution, attribution_url, status")
            .order("id")
            .range(frm, frm + page - 1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break
        out.extend(rows)
        if len(rows) < page:
            break
        frm += page
    return out


def mark_offline(client, cam_ids: list[int]) -> None:
    if not cam_ids:
        return
    now_iso = datetime.now(timezone.utc).isoformat()
    client.table("cams").update(
        {"status": "offline", "last_checked_at": now_iso}
    ).in_("id", cam_ids).execute()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--output", type=Path, default=DEFAULT_OUTPUT,
                   help="Where to write cam_health.json")
    p.add_argument("--fix", action="store_true",
                   help="Flip status='offline' in Supabase for dead-bucket cams.")
    p.add_argument("--delay", type=float, default=0.2,
                   help="Sleep between requests in seconds (default 0.2).")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    client = get_client()
    cams = fetch_cams(client)
    log.info("checking %d cams", len(cams))

    results: list[dict] = []
    counters = {"checked": 0, "ok": 0, "redirect": 0, "dead": 0, "skipped": 0}
    for i, cam in enumerate(cams):
        if i > 0 and args.delay > 0:
            time.sleep(args.delay)
        url = cam.get("attribution_url")
        if not url:
            counters["skipped"] += 1
            results.append({
                "cam_id": cam["id"],
                "spot_slug": cam.get("spot_slug"),
                "cam_name": cam.get("cam_name"),
                "provider": cam.get("provider"),
                "url": None,
                "bucket": "skipped",
                "status_code": None,
                "final_url": None,
                "error": "no attribution_url",
            })
            continue

        r = check_url(url)
        counters["checked"] += 1
        counters[r["bucket"]] = counters.get(r["bucket"], 0) + 1

        results.append({
            "cam_id": cam["id"],
            "spot_slug": cam.get("spot_slug"),
            "cam_name": cam.get("cam_name"),
            "provider": cam.get("provider"),
            **r,
        })

        tag = r["bucket"].upper()
        if r["bucket"] == "redirect":
            log.info("%-8s %s/%-30s %s -> %s",
                     tag, cam.get("spot_slug"), cam.get("cam_name"),
                     url, r.get("final_url"))
        elif r["bucket"] == "dead":
            log.warning("%-8s %s/%-30s %s (%s)",
                        tag, cam.get("spot_slug"), cam.get("cam_name"),
                        url, r.get("status_code") or r.get("error"))
        else:
            log.debug("%-8s %s/%s", tag, cam.get("spot_slug"), cam.get("cam_name"))

    # --- write report --------------------------------------------------
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps({
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "totals": counters,
        "results": results,
    }, indent=2))

    log.info(
        "done. ok=%d redirect=%d dead=%d skipped=%d → wrote %s",
        counters.get("ok", 0), counters.get("redirect", 0),
        counters.get("dead", 0), counters.get("skipped", 0),
        args.output,
    )

    # --- optional fixup ------------------------------------------------
    if args.fix:
        dead_ids = [r["cam_id"] for r in results if r["bucket"] == "dead"]
        if dead_ids:
            mark_offline(client, dead_ids)
            log.info("--fix: marked %d cam(s) offline", len(dead_ids))
        else:
            log.info("--fix: no dead cams to update")

    return 0


if __name__ == "__main__":
    sys.exit(main())
