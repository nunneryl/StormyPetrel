"""How many spots the free tide pass would give: a measurement, not the pass itself.

READ-ONLY. No API calls, no network, and it writes nothing: it reads the break-type research
results file (pipeline/data/break_type_research_full.json, git-ignored, on the Mac only) and
prints counts. Run from the repo root:

    python3 scripts/tide_freepass_measure.py
    python3 scripts/tide_freepass_measure.py path/to/other_results.json

For every researched spot it reads each saved page — the text the fetch tool returned, read
with research_break_type.collect_evidence exactly as --recheck reads it — finds sentences that
state a best tide, maps each with map_sentence, and prints:
  - spots by outcome, in three location tiers:
      location ok         the page is one the break-type research cited for this spot and
                          whose location check read 'ok' (the only tier a pass would import)
      names the spot      another saved page whose sentence names the spot; location unchecked
      location unchecked  neither
  - the mapped values, by domain, with each domain's share of 'all';
  - how many mapped spots rest on mondo.surf alone;
  - unknown statements by reason, conflicts on one page and across pages;
  - pages that were probably cut at the 6,000-token fetch limit.

The rules are pipeline/tide_free_pass.py's: only clean prose (no link lists, menus or
forecast-widget text), stormypetrel.surf left out, and map_sentence's mapping. The free pass
itself — with its domain rules and the review file — is `python3 -m pipeline.tide_free_pass`.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pipeline.research_break_type import (  # noqa: E402
    CUT_PAGE_CHARS,
    DEFAULT_FULL_OUTPUT,
    _url_key,
    collect_evidence,
)
# One copy of the rules: the free pass's (pipeline/tide_free_pass.py), so this measurement and
# the review file it previews cannot disagree about what a sentence says.
from pipeline.tide_free_pass import excluded, map_sentence, prose_sentences  # noqa: E402

def domain(url):
    return _url_key(url).split("/")[0]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("results", nargs="?", default=str(DEFAULT_FULL_OUTPUT),
                        help="the research results file (default: %(default)s)")
    path = parser.parse_args(argv).results
    if not os.path.exists(path):
        raise SystemExit(f"no research results at {path}: it is git-ignored and exists only "
                         "where the break-type research ran (the Mac)")
    with open(path, encoding="utf-8") as f:
        results = json.load(f)
    spots = results["spots"]
    tiers, values, by_domain = Counter(), Counter(), defaultdict(Counter)
    unknown_why, conflicts_page, conflicts_spot, cut, pages_total = Counter(), 0, 0, 0, 0
    mondo_only = 0
    examples = defaultdict(list)
    for rec in spots.values():
        saved = rec.get("research_responses")
        if not saved:
            tiers["no saved pages"] += 1
            continue
        responses = list(saved["first"]) + list((saved.get("follow_up") or {}).get("responses") or [])
        ev = collect_evidence([b for r in responses for b in (r.get("content") or [])])
        research = rec.get("research") or {}
        ok_pages = {_url_key(p["source_url"]) for p in (research.get(f) for f in ("break_type", "bottom"))
                    if p and p.get("source_url") and (p.get("location") or {}).get("verdict") == "ok"}
        name = rec["identity"]["name"].lower()
        found = []   # (value, url, sentence, tier)
        seen_pages = set()
        for f in ev["fetched"]:
            k = _url_key(f["url"])
            if k in seen_pages or excluded(f["url"]):
                continue
            seen_pages.add(k)
            text = ev["pages"].get(k) or ""
            pages_total += 1
            if len(text) >= CUT_PAGE_CHARS:
                cut += 1
            page_vals = set()
            for s in prose_sentences(text):
                v, why = map_sentence(s)
                if v is None:
                    continue
                if v == "unknown":
                    unknown_why[why] += 1
                    continue
                tier = ("location ok" if k in ok_pages else
                        "names the spot" if name.split(",")[0] in s.lower() else "location unchecked")
                found.append((v, f["url"], s[:300], tier))
                page_vals.add(v)
            if len(page_vals) > 1:
                conflicts_page += 1
        if not found:
            tiers["no tide statement"] += 1
            continue
        best = [x for x in found if x[3] == "location ok"] or [x for x in found if x[3] == "names the spot"]
        if not best:
            tiers["statement, location unchecked only"] += 1
            continue
        vals = {x[0] for x in best}
        if len(vals) > 1:
            conflicts_spot += 1
            tiers["conflicting statements"] += 1
            continue
        v, url, s, tier = best[0]
        tiers["mapped: " + tier] += 1
        if all(domain(x[1]) == "mondo.surf" for x in best):
            mondo_only += 1
        values[v] += 1
        by_domain[domain(url)][v] += 1
        if len(examples[v]) < 3:
            examples[v].append(f"{rec['identity']['name']}: {s}  <{url}>")
    print(f"spots: {len(spots)}; pages read: {pages_total}; pages probably cut at the limit: {cut}")
    print("by outcome:", dict(tiers))
    print("mapped values:", dict(values))
    print(f"mapped spots resting on mondo.surf alone: {mondo_only}")
    print("unknown statements by reason:", dict(unknown_why))
    print(f"pages with two different values: {conflicts_page}; spots with conflicting pages: {conflicts_spot}")
    print("by domain (value counts; share 'all'):")
    for d, c in sorted(by_domain.items(), key=lambda kv: -sum(kv[1].values())):
        n = sum(c.values())
        print(f"  {d}: {n} {dict(c)}  all={c['all'] / n:.0%}")
    for v, ex in examples.items():
        print(f"examples, {v}:")
        for e in ex:
            print("   ", e)
    return 0


if __name__ == "__main__":
    sys.exit(main())
