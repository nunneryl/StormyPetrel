#!/usr/bin/env python3
"""Find new NWS service change notices that touch the data the pipeline reads. READ-ONLY.

WHY. NWPS v1.5 renamed every NWPS file from the 2026-11-09 12Z run, and the pipeline read
cycles up to 30 h old, or none, for a day before anyone looked. NWS had said so 34 days
earlier: SCN 26-91, "Upgrade Nearshore Wave Prediction System to v1.5", issued 2026-10-06 (and
PNS 26-10 asked for comments on it in February). This reads the same list every week.

WHAT IT DOES. Reads https://www.weather.gov/notification/ (one request), takes every dated
notice on it (SCN, PNS, TIN, SCB...), keeps those dated in the last --days days whose title
names a source the pipeline reads (TOPICS), and prints them; with GITHUB_OUTPUT set, it also
hands them to the workflow's issue step as JSON. The year of each "Oct 06" date is the year in
the heading it sits under ("Service Change Notices 2026").

FAILS LOUDLY. Fewer than MIN_NOTICES dated notices means the page did not load or its layout
changed: the run fails, so the workflow's failure issue opens, rather than reporting "nothing
new" forever.

    python3 scripts/noaa_notice_watch.py               # the last 14 days
    python3 scripts/noaa_notice_watch.py --days 400    # look further back (a dry check)
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.request
from datetime import date, datetime, timedelta, timezone

URL = "https://www.weather.gov/notification/"
USER_AGENT = "StormyPetrel-Pipeline/0.1 (+https://stormypetrel.surf)"
MIN_NOTICES = 50          # the page lists hundreds; fewer than this is a broken read
DAYS = 14                 # weekly runs, with a week of overlap for a late or missed run

# Title patterns for each source the pipeline reads, matched case-insensitively. "NWPS" alone
# is NOT one: the National Water Prediction Service (river gauges) is also NWPS.
TOPICS = {
    "NWPS (Nearshore Wave Prediction System)": r"nearshore wave",
    "WAVEWATCH III / GFS-Wave": r"wavewatch|gfs[- ]?wave|global wave|\bgfs\b",
    "NDBC buoys": r"\bndbc\b|data buoy|\bbuoys?\b",
    "CO-OPS tides": r"co-ops|\btides?\b|\btidal\b",
    "HRRR / RRFS": r"\bhrrr\b|high[- ]resolution rapid refresh|\brrfs\b|rapid refresh forecast",
    "NOMADS / data delivery": r"\bnomads\b|akamai|cipher",
}

MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_YEAR_HEADING = re.compile(r"<h2\b[^>]*>(.*?)</h2>", re.S | re.I)
_ITEM = re.compile(r"<li\b[^>]*>(.*?)</li>", re.S | re.I)
_LINK = re.compile(r'<a\b[^>]*href="(/media/notification/[^"]+)"[^>]*>(.*?)</a>', re.S | re.I)
_DATE = re.compile(r"^\s*([A-Za-z]{3})[a-z]*\.?\s+(\d{1,2})\s*:")
_ID = re.compile(r"\b((?:SCN|PNS|TIN|SCB))\s?(\d{2})[-_ ](\d{1,3})(?!\d)(\s+Updated)?", re.I)


def text(fragment: str) -> str:
    """Tags out, entities decoded, whitespace (non-breaking spaces too) collapsed to one."""
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ").split())


def parse(page: str) -> list[dict]:
    """Every dated notice on the page, in page order: {id, date, title, url}."""
    out = []
    heads = [(m.start(), re.search(r"\b(20\d\d)\b", text(m.group(1)))) for m in _YEAR_HEADING.finditer(page)]
    heads = [(pos, int(y.group(1))) for pos, y in heads if y]
    for m in _ITEM.finditer(page):
        year = next((y for pos, y in reversed(heads) if pos < m.start()), None)
        link = _LINK.search(m.group(1))
        when = _DATE.match(text(m.group(1)))
        if year is None or not link or not when or when.group(1).lower() not in MONTHS:
            continue
        label = text(link.group(2))
        ident = _ID.search(label) or _ID.search(link.group(1))
        if not ident:
            continue
        # The link text is "SCN26-91: Upgrade ..." (sometimes with the date in front); the
        # title is what follows the id. Some items link the id alone and write the title after
        # the link, so then the whole item is read from the id on.
        title = label[ident.end():].lstrip(" :") if _ID.search(label) else label
        if not title:
            item = text(m.group(1))
            again = _ID.search(item)
            title = item[again.end():].lstrip(" :") if again else ""
        try:
            day = date(year, MONTHS[when.group(1).lower()], int(when.group(2)))
        except ValueError:
            continue
        nid = f"{ident.group(1).upper()}{ident.group(2)}-{int(ident.group(3))}"
        if ident.group(4):
            nid += " Updated"
        out.append({"id": nid, "date": day.isoformat(), "title": title,
                    "url": "https://www.weather.gov" + html.unescape(link.group(1))})
    return out


def topics_of(title: str) -> list[str]:
    return [name for name, pat in TOPICS.items() if re.search(pat, title, re.I)]


def recent_matches(notices: list[dict], today: date, days: int = DAYS) -> list[dict]:
    """Notices dated within *days* before *today* (inclusive) that name a source we read, each
    with its topics and the key the issue step dedups on (id and date)."""
    since = today - timedelta(days=days)
    out = []
    for n in notices:
        hits = topics_of(n["title"])
        if hits and since <= date.fromisoformat(n["date"]) <= today:
            out.append({**n, "topics": hits, "key": f"{n['id']} {n['date']}"})
    return out


def fetch(url: str = URL, timeout: float = 60) -> str:
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", errors="replace")


def main(argv: list[str] | None = None, page: str | None = None, today: date | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--days", type=int, default=DAYS)
    args = ap.parse_args(argv)
    today = today or datetime.now(timezone.utc).date()
    try:
        page = page if page is not None else fetch()
    except Exception as e:  # noqa: BLE001  any failure to read the page fails the run
        print(f"::error::could not read {URL}: {type(e).__name__}: {e}")
        return 1
    notices = parse(page)
    if len(notices) < MIN_NOTICES:
        print(f"::error::{URL} gave {len(notices)} dated notices, fewer than {MIN_NOTICES}: the "
              f"page did not load or its layout changed, so new notices cannot be seen.")
        return 1
    found = recent_matches(notices, today, args.days)
    print(f"{len(notices)} dated notices on the page; {len(found)} in the last {args.days} days "
          f"name a source the pipeline reads.")
    for n in found:
        print(f"  {n['date']}  {n['id']}: {n['title']}  [{', '.join(n['topics'])}]  {n['url']}")
    out = os.environ.get("GITHUB_OUTPUT")
    if out:
        with open(out, "a", encoding="utf-8") as f:
            f.write(f"notices={json.dumps(found, separators=(',', ':'))}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
