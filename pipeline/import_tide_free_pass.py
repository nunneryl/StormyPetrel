"""Write the reviewed free tide pass into the roster: each importable spot's tide preference as
'researched', with the page it was read from and the sentence that says it.

    python3 -m pipeline.import_tide_free_pass --dry-run   # what would change, by value; writes nothing
    python3 -m pipeline.import_tide_free_pass             # writes pipeline/spots_enriched.json

Run on the Mac, from the repo root, AFTER `python3 -m pipeline.tide_free_pass` has written the review
file and it has been reviewed. No API calls and no network.

WHAT IT WRITES. Exactly the rows pipeline/tide_free_pass.py's import_decision allows — a proposed
row, or one accepted on review (pipeline/data/tide_review_decisions.json), never a rejected one,
never a value no one page states — and only if THE REVIEW FILE AGREES: every row it marks
import=yes, with the same value, and no other. A review file written by older rules, or edited
since, stops the import, dry run included, with the differences named; re-run the review.

HOW. Each write goes through config.tide_preference_fields (the value, 'researched', the page's URL
and the sentence as its quote, refused there if migration 022 would refuse it) and
config.tide_preference_may_write (never a spot marked is_valid_surf_spot false; never over a
reviewed value; an equal-ranked researched value may be replaced). The model's self-scored
tide_preference_confidence from classify_tides describes the value being replaced, so it is cleared
beside a researched one. A spot whose roster coordinates have moved more than MOVED_KM from where
the research was done is left alone. Spots it does not write keep their exact text.

WHAT IT CANNOT CHANGE. The roster's five tide keys, nothing else: no database (db_import uploads the
roster on the next pipeline run, after migration 022), no description (run generate_descriptions),
no star rating until that run rates with the new values.
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path

from .config import (
    DEFAULT_ENRICHED_OUTPUT,
    TIDE_PREFERENCE_FIELDS,
    tide_preference_fields,
    tide_preference_may_write,
)
from .geo import haversine_m
from .import_break_type_research import MOVED_KM, _placed, load_full, roster_text
from .research_break_type import DEFAULT_FULL_OUTPUT, memory_key, memory_spots
from .tide_free_pass import (
    DECISIONS_FILE,
    REVIEW_OUTPUT,
    VALUES,
    import_decision,
    load_decisions,
    read_review,
    run,
    stating_page,
)

SOURCE = "researched"
CONFIDENCE = "tide_preference_confidence"
WRITE, SAME, KEPT, MISSING, MOVED = "write", "same", "kept", "missing", "moved"
ACTIONS = ((WRITE, "written"), (SAME, "already the same"),
           (KEPT, "kept: a higher-ranked source holds it"),
           (MISSING, "not a rated spot in the roster"),
           (MOVED, f"left: moved more than {MOVED_KM:g} km since the research"))
# Where a spot that has never had the tide keys gets them.
_AFTER = ("bottom_evidence", "break_type_evidence", "region_hint")


def importable(outcomes: list, decisions: dict) -> list:
    """The outcomes import_decision lets in, in the order the pass gave them."""
    return [o for o in outcomes
            if import_decision(o, decisions.get(f"{o['name']}|{o['state']}"))[0]]


def review_mismatch(rows: list, review: dict) -> list:
    """How the review file differs from what the import would write, one line each; [] agrees."""
    planned = {(o["name"], o["state"]): o["value"] for o in rows}
    marked = {key: r.get("value") for key, r in review.items() if r.get("import") == "yes"}
    out = []
    for key, value in planned.items():
        if key not in marked:
            out.append(f"{key[0]} ({key[1]}): importable as {value}, but not import=yes in the "
                       "review file")
        elif marked[key] != value:
            out.append(f"{key[0]} ({key[1]}): the review file says {marked[key]}, the pass now "
                       f"gives {value}")
    for key, value in marked.items():
        if key not in planned:
            out.append(f"{key[0]} ({key[1]}): import=yes as {value} in the review file, but not "
                       "importable now")
    return out


def plan(rows: list, roster: list) -> list:
    """[{'name', 'state', 'value', 'before', 'action', 'spot', 'fields'}] for every importable row:
    what the import does with it."""
    by_key = {memory_key(spot): spot for spot in memory_spots(roster)}
    out = []
    for o in rows:
        page = stating_page(o)
        quote = next(s for v, s in page["statements"] if v == o["value"])
        fields = tide_preference_fields(o["value"], SOURCE, url=page["url"], evidence=quote)
        spot = by_key.get(f"{o['name']}|{o['state']}")
        item = {"name": o["name"], "state": o["state"], "value": o["value"], "url": page["url"],
                "spot": spot, "fields": fields,
                "before": (spot.get("tide_preference"), spot.get("tide_preference_source"))
                if spot else (None, None)}
        if spot is None:
            item["action"] = MISSING
        elif (o.get("lat") is not None and o.get("lng") is not None
              and haversine_m(float(spot["lat"]), float(spot["lng"]),
                              float(o["lat"]), float(o["lng"])) / 1000.0 > MOVED_KM):
            item["action"] = MOVED
        elif not tide_preference_may_write(spot, SOURCE):
            item["action"] = KEPT
        elif all(spot.get(k) == fields[k] for k in TIDE_PREFERENCE_FIELDS) \
                and spot.get(CONFIDENCE) is None:
            item["action"] = SAME
        else:
            item["action"] = WRITE
        out.append(item)
    return out


def apply(roster: list, planned: list) -> list:
    """The roster with every WRITE made; every other spot returned as it was."""
    writes = {id(item["spot"]): item for item in planned if item["action"] == WRITE}
    out = []
    for spot in roster:
        item = writes.get(id(spot))
        if item is not None:
            fields = dict(item["fields"])
            if CONFIDENCE in spot:
                fields[CONFIDENCE] = None
            spot = _placed(spot, fields, _AFTER)
        out.append(spot)
    return out


def report(planned: list, review_rows: int, wrote: bool) -> str:
    lines = [f"{review_rows} rows the review file marks import=yes; the pass agrees with every one.",
             "", "What the import does, by value:", "",
             "| | " + " | ".join(VALUES) + " | total |",
             "|---|" + "---|" * (len(VALUES) + 1)]
    for action, label in ACTIONS:
        counts = Counter(item["value"] for item in planned if item["action"] == action)
        lines.append(f"| {label} | " + " | ".join(str(counts[v]) for v in VALUES)
                     + f" | {sum(counts.values())} |")
    changed = [i for i in planned if i["action"] == WRITE and i["before"][0] != i["value"]]
    restamped = [i for i in planned if i["action"] == WRITE and i["before"][0] == i["value"]]
    lines += ["", f"Spots whose tide preference changes: {len(changed)}. Spots that keep their "
                  f"value and now cite a page: {len(restamped)}."]
    moves = Counter(f"{i['before'][0] or '(none)'} -> {i['value']}" for i in changed)
    if moves:
        lines.append("Changes: " + ", ".join(f"{k} {n}" for k, n in sorted(moves.items())) + ".")
        lines += ["", "| Spot | Now | Becomes | Page |", "|---|---|---|---|"]
        lines += [f"| {i['name']} ({i['state']}) | {i['before'][0] or '(none)'} "
                  f"({i['before'][1] or 'no source'}) | {i['value']} | {i['url']} |" for i in changed]
    lines += ["", "Wrote the roster." if wrote else "Dry run: nothing written."]
    return "\n".join(lines)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python3 -m pipeline.import_tide_free_pass",
                                description=__doc__.splitlines()[0])
    p.add_argument("--results", type=Path, default=DEFAULT_FULL_OUTPUT)
    p.add_argument("--roster", type=Path, default=Path(DEFAULT_ENRICHED_OUTPUT))
    p.add_argument("--review", type=Path, default=REVIEW_OUTPUT,
                   help="the reviewed review file (default: %(default)s)")
    p.add_argument("--decisions", type=Path, default=DECISIONS_FILE)
    p.add_argument("--dry-run", action="store_true", help="report what would change; write nothing")
    args = p.parse_args(argv)
    if not args.review.exists():
        raise SystemExit(f"no review file at {args.review}: run `python3 -m pipeline.tide_free_pass` "
                         "and review it first")
    roster = json.loads(args.roster.read_text(encoding="utf-8"))
    decisions = load_decisions(args.decisions, roster)
    outcomes, _ = run(load_full(args.results))
    rows = importable(outcomes, decisions)
    review = read_review(args.review)
    mismatch = review_mismatch(rows, review)
    if mismatch:
        raise SystemExit(f"the review file {args.review} does not match what would be imported "
                         f"({len(mismatch)}); re-run `python3 -m pipeline.tide_free_pass`, review "
                         "it, and try again. Nothing was written:\n  " + "\n  ".join(mismatch))
    planned = plan(rows, roster)
    if not args.dry_run:
        args.roster.write_text(roster_text(apply(roster, planned)), encoding="utf-8")
    print(report(planned, len(rows), wrote=not args.dry_run))
    return 0


if __name__ == "__main__":
    sys.exit(main())
