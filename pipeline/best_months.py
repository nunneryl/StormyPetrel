"""Best months from our own forecast archive, for the spot descriptions. READ-ONLY.

SELECTs the published ratings (forecasts, source 'nwps', stars set) of every valid roster spot from
--since (default 2026-04-01, the first month the table holds) to the start of the current month,
and writes pipeline/data/spot_best_months.json, which pipeline/descriptions.py reads. Nothing is
written to Supabase. Run by sw1-raycast mode best-months; the artifact's file is committed.

HOW A MONTH IS SCORED (the same for every spot; the description says what it rests on):
  1. Each published hour is placed in the spot's own day by its time zone (spots_enriched.json
     time_zone). Only daylight hours count: 06:00 to 18:59 local.
  2. A DAY counts when it has at least MIN_DAY_HOURS such hours. A day is GOOD when its best
     daylight hour is rated GOOD_STARS (3) stars or more.
  3. A MONTH counts when it has at least MIN_MONTH_DAYS counted days. Its share is good days over
     counted days.
  4. The best months are the BEST_N (2) counted months with the highest share, only those with
     a share of MIN_SHARE (0.2) or more, highest first (ties: the earlier month). A spot with no
     such month gets none, and its description says nothing about months.
The covered span (first and last counted month across the spots) is recorded, so the
description can say which months it compared: one year of forecasts at most, and a month not in
the span (northern winter, until it is) is never called best.

    python -m pipeline.best_months [--since 2026-04-01] [--out PATH]
"""
from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .config import DEFAULT_ENRICHED_OUTPUT

OUT = Path(__file__).resolve().parent / "data" / "spot_best_months.json"
SINCE = "2026-04-01"
DAY_START_H, DAY_END_H = 6, 18
MIN_DAY_HOURS = 8
GOOD_STARS = 3.0
MIN_MONTH_DAYS = 20
MIN_SHARE = 0.2
BEST_N = 2


def _slug(name):
    from .db_import import _slugify
    return _slugify(name or "")


def month_table(rows, tz):
    """{'YYYY-MM': {"days": counted days, "good": good days}} from *rows* [(iso valid_time, stars)]."""
    zone = ZoneInfo(tz)
    by_day = defaultdict(list)
    for vt, stars in rows:
        if stars is None:
            continue
        t = datetime.fromisoformat(str(vt).replace("Z", "+00:00")).astimezone(zone)
        if DAY_START_H <= t.hour <= DAY_END_H:
            by_day[t.date()].append(float(stars))
    months = defaultdict(lambda: {"days": 0, "good": 0})
    for d, stars in by_day.items():
        if len(stars) < MIN_DAY_HOURS:
            continue
        m = months[f"{d.year:04d}-{d.month:02d}"]
        m["days"] += 1
        m["good"] += max(stars) >= GOOD_STARS
    return dict(sorted(months.items()))


def best_of(months):
    """The best months of *months* (month_table's result): [{"month", "good", "days"}], best first."""
    counted = [(m, v) for m, v in months.items() if v["days"] >= MIN_MONTH_DAYS]
    ranked = sorted(counted, key=lambda mv: (-mv[1]["good"] / mv[1]["days"], mv[0]))
    return [{"month": m, "good": v["good"], "days": v["days"]} for m, v in ranked
            if v["good"] / v["days"] >= MIN_SHARE][:BEST_N]


def _rows(client, spot_id, since, until):
    out, frm = [], 0
    while True:
        page = (client.table("forecasts").select("valid_time, stars").eq("spot_id", spot_id)
                .eq("source", "nwps").gte("valid_time", since).lt("valid_time", until)
                .order("valid_time").range(frm, frm + 999).execute().data or [])
        out.extend((r["valid_time"], r.get("stars")) for r in page)
        if len(page) < 1000:
            return out
        frm += 1000


def run(client, roster, since=SINCE, until=None, log=print):
    until = until or datetime.now(timezone.utc).strftime("%Y-%m-01T00:00:00+00:00")
    ids = {}
    frm = 0
    while True:
        page = client.table("spots").select("id, slug").order("id").range(frm, frm + 999).execute().data or []
        ids.update({r["slug"]: r["id"] for r in page})
        if len(page) < 1000:
            break
        frm += 1000
    spots, months_seen = {}, set()
    valid = [s for s in roster if s.get("is_valid_surf_spot") is not False and s.get("time_zone")]
    for i, s in enumerate(valid, 1):
        slug = _slug(s["name"])
        if slug not in ids:
            continue
        months = month_table(_rows(client, ids[slug], since, until), s["time_zone"])
        counted = [m for m, v in months.items() if v["days"] >= MIN_MONTH_DAYS]
        months_seen.update(counted)
        spots[slug] = {"months": months, "best": best_of(months)}
        if i % 50 == 0:
            log(f"best_months: {i}/{len(valid)} spots")
    return {"_comment": "Written by pipeline/best_months.py (read-only, sw1-raycast mode best-months). "
                        "Read by pipeline/descriptions.py. See best_months.py for how a month is scored.",
            "since": since, "until": until,
            "span": [min(months_seen), max(months_seen)] if months_seen else None,
            "rule": {"daylight_hours": [DAY_START_H, DAY_END_H], "min_day_hours": MIN_DAY_HOURS,
                     "good_stars": GOOD_STARS, "min_month_days": MIN_MONTH_DAYS,
                     "min_share": MIN_SHARE, "best_n": BEST_N},
            "spots": dict(sorted(spots.items()))}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--since", default=SINCE)
    p.add_argument("--out", default=str(OUT))
    args = p.parse_args(argv)
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    roster = json.loads(Path(DEFAULT_ENRICHED_OUTPUT).read_text())
    rep = run(client, roster, args.since)
    Path(args.out).write_text(json.dumps(rep, indent=1, ensure_ascii=False) + "\n")
    with_best = sum(1 for v in rep["spots"].values() if v["best"])
    print(f"best_months: {len(rep['spots'])} spots, span {rep['span']}, {with_best} with best months; "
          f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
