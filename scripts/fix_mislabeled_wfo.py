#!/usr/bin/env python3
"""Data hygiene — correct nwps_wfo for 5 spots whose coordinates are right but whose
NWPS WFO grid label is wrong (they point at the wrong Weather Forecast Office region).

nwps_wfo selects which NWPS WFO SWAN grid a spot's nearshore forecast is sampled from
(read in pipeline.forecast.nwps_nearshore / nwps / mop, and consumed by db_import). It is
CARRIED unchanged through enrich (enrich never recomputes it), so the durable source of
record is spots_enriched.json itself. This is a surgical patch of that file — like
fix_mislabeled_regions / apply_orientation_relook: only nwps_wfo of the 5 changes, nothing
else. A coord guard refuses to patch a spot whose lat/lng no longer matches the verified
position, so a moved/renamed spot is never silently mislabeled.

    python3 scripts/fix_mislabeled_wfo.py            # dry run (before/after)
    python3 scripts/fix_mislabeled_wfo.py --apply    # write spots_enriched.json

Propagate: next `python -m pipeline.db_import --spots-only` (or the scheduled pipeline)
picks up the corrected nwps_wfo. No enrich step needed. Read-only on prod until --apply;
touches no other file.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENRICHED = os.path.join(os.path.dirname(HERE), "pipeline", "spots_enriched.json")

# slug -> (verified_lat, verified_lng, correct_nwps_wfo). Coords from the user's
# verification; the label is the WFO whose grid actually covers that coordinate.
CORRECTIONS = {
    "56th-street":  (39.14, -74.70, "phi"),    # NJ — was mislabeled lox (Los Angeles/Oxnard)
    "bombora":      (21.28, -157.85, "hfo"),   # Oahu, HI — was mtr (San Francisco Bay)
    "suicide-s":    (19.95, -155.86, "hfo"),   # Hawaii Island — was mtr (San Francisco Bay)
    "hammonds":     (34.41, -119.64, "lox"),   # Santa Barbara CA — was mtr (San Francisco Bay)
    "scotts-creek": (37.04, -122.23, "mtr"),   # Santa Cruz CA — was eka (Eureka)
}
COORD_TOL = 0.05  # degrees — guard: refuse to patch a spot whose lat/lng drifted from verified


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def build_plan(spots):
    by_slug = {}
    for s in spots:
        by_slug.setdefault(_slug(s.get("name")), s)
    rows, problems = [], []
    for slug, (lat, lng, wfo) in CORRECTIONS.items():
        s = by_slug.get(slug)
        if s is None:
            problems.append((slug, "not found in spots_enriched.json"))
            continue
        dlat, dlng = abs((s.get("lat") or 0) - lat), abs((s.get("lng") or 0) - lng)
        if dlat > COORD_TOL or dlng > COORD_TOL:
            problems.append((slug, f"coord mismatch (roster {s.get('lat')},{s.get('lng')} "
                                   f"vs verified {lat},{lng}, tol {COORD_TOL}) — refusing to patch"))
            continue
        old = s.get("nwps_wfo")
        if old == wfo:
            problems.append((slug, f"already '{wfo}' — no change"))
            continue
        rows.append({"slug": slug, "name": s.get("name"), "lat": s.get("lat"), "lng": s.get("lng"),
                     "old": old, "new": wfo, "_spot": s})
    return rows, problems


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write spots_enriched.json (default: dry run)")
    a = ap.parse_args(argv)
    if not os.path.exists(ENRICHED):
        print(f"error: missing {ENRICHED}", file=sys.stderr)
        return 2

    raw = open(ENRICHED).read()
    spots = json.loads(raw)
    rows, problems = build_plan(spots)

    print(f"\n{'DRY RUN' if not a.apply else 'APPLY'} — nwps_wfo fix in spots_enriched.json\n")
    print(f"  {'slug':14}{'coords':22}{'nwps_wfo: before → after'}")
    print(f"  {'-'*14}{'-'*22}{'-'*30}")
    for r in rows:
        coords = f"{r['lat']:.2f}, {r['lng']:.2f}"
        print(f"  {r['slug']:14}{coords:22}{r['old']!r} → {r['new']!r}")
    if problems:
        print("\n  not changed:")
        for slug, why in problems:
            print(f"    {slug:14} {why}")
    # confirm nothing else changes
    changed_fields = {"nwps_wfo"}
    print(f"\n  {len(rows)} spot(s) will change; only the field(s) {sorted(changed_fields)} are touched, "
          f"on those {len(rows)} spot(s) only. All other spots + fields untouched.")

    if not a.apply:
        print(f"\nsummary: {len(rows)} to patch, {len(problems)} skipped/aborted.")
        print("dry run only — nothing written. Re-run with --apply to write spots_enriched.json.")
        return 0

    for r in rows:
        r["_spot"]["nwps_wfo"] = r["new"]
    text = json.dumps(spots, indent=2, ensure_ascii=False)
    if raw.endswith("\n"):
        text += "\n"
    open(ENRICHED, "w").write(text)
    print(f"\nAPPLIED → {ENRICHED}: {len(rows)} nwps_wfo corrected.")
    print(f"summary: {len(rows)} patched, {len(problems)} skipped/aborted.")
    print("Propagate: run `python -m pipeline.db_import --spots-only` (or the scheduled pipeline) "
          "to pick up the corrected nwps_wfo. DB not touched here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
