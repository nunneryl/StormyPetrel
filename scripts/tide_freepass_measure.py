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

The mapping rules are the design's (directions such as "pushing tide", tide-dependent and
comparative sentences, negatives and non-adjacent stages stay unknown; access, parking and
tidepool sentences are not statements at all). On the 50 sentences in the committed research
summary that mention the tide, it agreed with a hand reading on 49; the 50th, a low-to-medium
statement next to the word "shallow", it leaves unknown.
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

STAGE = r"(low|mid|medium|middle|half|high)"
UNIT = r"(?:tides?|tide (?:levels?|conditions|stages?)|water)"
POSITIVE = (r"(best|works?|working|work best|favou?rable|ideal|good|great|excellent|prefers?"
            r"|needs?|requires?|only|breaks? best|turns on|comes alive|fires|fish for it"
            r"|suitable|surf(?:ed|able)|rideable|performs best)")
# About something other than the surf: never a preference, whatever else the sentence says.
NOT_ABOUT_SURF = (r"(tidepools?|tide pools?|accessib\w* only|only accessib\w*|access\w* only during"
                  r"|walk(ing)? (around|out|across)|parking|sea glass|dog|swimming|beachcomb)")
# About the surf, but not a preference unless "best", "only" or "needs" says so.
NOT_A_PREFERENCE = (r"(exposed|dangerous|shallow|rocks? show|hollower|mellower|forgiving"
                    r"|challenging|depend|dependent|sensitive|check the tide)")
DIRECTION = r"\b(rising|incoming|pushing|push|dropping|falling|outgoing|ebb|flood)(ing)? tide\b"
STAGE_OF = {"low": "low", "mid": "mid", "medium": "mid", "middle": "mid", "half": "mid",
            "high": "high"}
PAIR = {frozenset({"low", "mid"}): "low_mid", frozenset({"mid", "high"}): "mid_high",
        frozenset({"low", "high"}): "all"}


def sentences(text):
    text = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9\"'*])", text) if s.strip()]


def map_sentence(s):
    """(value, rule) for one sentence, or (None, why). value is one of low, low_mid, mid,
    mid_high, high, all, or 'unknown' for a tide statement that names no usable stage."""
    t = s.lower().replace("’", "'")
    if not re.search(r"\btides?\b|\blow water\b|\bhigh water\b", t):
        return None, "no tide"
    if re.search(NOT_ABOUT_SURF, t):
        return None, "not about the surf"
    if re.search(r"\b(all|any|every) (stages? of the )?tides?\b|\ball tide (levels|conditions|stages)"
                 r"|\b(at|on|in) all stages\b|\bwhatever the tide\b|\btide (doesn't|does not) matter",
                 t):
        return "all", "all tides"
    # "low to mid", "low and medium", "medium to high", "low-mid"
    m = re.search(rf"\b{STAGE}\s*(to|and|-|through|or)\s*{STAGE}\b(?:\s+{UNIT})?", t)
    if m and re.search(POSITIVE, t) and STAGE_OF[m.group(1)] != STAGE_OF[m.group(3)]:
        if re.search(NOT_A_PREFERENCE, t) and not re.search(r"\bbest\b", t):
            return "unknown", "pair, not a preference"
        pair = frozenset({STAGE_OF[m.group(1)], STAGE_OF[m.group(3)]})
        if pair == frozenset({"low", "high"}) and m.group(2) in ("and", "or"):
            return "unknown", "two stages that are not adjacent"
        return PAIR[pair], "pair"
    m = re.search(rf"\b(minus|negative|spring) (low )?tide\b", t)
    if m and re.search(POSITIVE, t):
        return "low", "minus tide"
    hits = {STAGE_OF[x] for x in re.findall(rf"\b{STAGE}(?:er)? {UNIT}", t)}
    if len(hits) == 1:
        if re.search(NOT_A_PREFERENCE, t) and not re.search(r"\b(best|only|needs?|requires?)\b", t):
            return "unknown", "stage named, not a preference"
        if re.search(r"\b(lower|higher) tides?\b", t) and not re.search(r"\b(best|only|needs?)\b", t):
            return "unknown", "comparative"
        if re.search(rf"\b(not|never|avoid|doesn't|does not|won't|kills?|swamp)", t):
            return "unknown", "negative"
        if re.search(POSITIVE, t):
            return hits.pop(), "single stage"
        return "unknown", "stage named, no positive cue"
    if len(hits) > 1:
        return "unknown", "several stages"
    if re.search(DIRECTION, t):
        return "unknown", "direction, not a stage"
    return "unknown", "tide named, no stage"


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
            if k in seen_pages:
                continue
            seen_pages.add(k)
            text = ev["pages"].get(k) or ""
            pages_total += 1
            if len(text) >= CUT_PAGE_CHARS:
                cut += 1
            page_vals = set()
            for s in sentences(text):
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
