#!/usr/bin/env python3
"""Data hygiene — correct region_hint for 3 spots whose coordinates are right
but whose region label is wrong (tagged California; actually NJ / HI).

region_hint is CARRIED unchanged through enrich (enrich.py never recomputes it)
and db_import maps it to the spots-table `state` + `region` columns. So the
durable source of record for these spots is spots_enriched.json itself — none of
the 3 have a correct upstream entry to fix:
  * bombora / suicide-s : absent from pipeline/data/llm_spots.json.
  * 56th-street         : llm_spots.json has a *different* "56th Street"
                          (Newport Beach, CA) — a name collision; editing it
                          would mislabel that real CA spot. NOT touched.
So this is a surgical patch of spots_enriched.json (like apply_orientation_relook):
only region_hint of the 3 changes; nothing else. A coord guard refuses to patch
a spot whose lat/lng no longer matches the verified position.

    python3 scripts/fix_mislabeled_regions.py            # dry run (before/after)
    python3 scripts/fix_mislabeled_regions.py --apply    # write spots_enriched.json

Propagate: next `python -m pipeline.db_import --spots-only` (or the scheduled
pipeline) writes state/region from the corrected region_hint. No enrich step
needed. Read-only on prod until --apply.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ENRICHED = os.path.join(os.path.dirname(HERE), "pipeline", "spots_enriched.json")

# slug -> (verified_lat, verified_lng, correct_region_hint). Coords from the
# user's verification; region naming matches the roster (56 "New Jersey",
# 53 "Hawaii" spots already use these exact strings).
CORRECTIONS = {
    "56th-street": (39.14, -74.70, "New Jersey"),
    "bombora":     (21.28, -157.85, "Hawaii"),
    "suicide-s":   (19.95, -155.86, "Hawaii"),
}
COORD_TOL = 0.15  # degrees — guard against patching a spot that moved/renamed


def _slug(name):
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def build_plan(spots):
    by_slug = {}
    for s in spots:
        by_slug.setdefault(_slug(s.get("name")), s)
    rows, problems = [], []
    for slug, (lat, lng, region) in CORRECTIONS.items():
        s = by_slug.get(slug)
        if s is None:
            problems.append((slug, "not found in spots_enriched.json"))
            continue
        dlat, dlng = abs((s.get("lat") or 0) - lat), abs((s.get("lng") or 0) - lng)
        if dlat > COORD_TOL or dlng > COORD_TOL:
            problems.append((slug, f"coord mismatch (roster {s.get('lat')},{s.get('lng')} "
                                   f"vs verified {lat},{lng}) — refusing to patch"))
            continue
        old = s.get("region_hint")
        if old == region:
            problems.append((slug, f"already '{region}' — no change"))
            continue
        rows.append({"slug": slug, "name": s.get("name"), "lat": s.get("lat"), "lng": s.get("lng"),
                     "old": old, "new": region, "_spot": s})
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

    print(f"\n{'DRY RUN' if not a.apply else 'APPLY'} — region_hint fix in spots_enriched.json\n")
    print(f"  {'slug':14}{'coords':22}{'region_hint: before → after'}")
    print(f"  {'-'*14}{'-'*22}{'-'*34}")
    for r in rows:
        coords = f"{r['lat']:.2f}, {r['lng']:.2f}"
        print(f"  {r['slug']:14}{coords:22}{r['old']!r} → {r['new']!r}")
    if problems:
        print("\n  not changed:")
        for slug, why in problems:
            print(f"    {slug:14} {why}")
    # confirm nothing else changes
    changed_fields = {"region_hint"}
    print(f"\n  {len(rows)} spot(s) will change; only the field(s) {sorted(changed_fields)} are touched, "
          f"on those {len(rows)} spot(s) only. All other spots + fields untouched.")

    if not a.apply:
        print("\ndry run only — nothing written. Re-run with --apply to write spots_enriched.json.")
        return 0

    for r in rows:
        r["_spot"]["region_hint"] = r["new"]
    text = json.dumps(spots, indent=2, ensure_ascii=False)
    if raw.endswith("\n"):
        text += "\n"
    open(ENRICHED, "w").write(text)
    print(f"\nAPPLIED → {ENRICHED}: {len(rows)} region_hint corrected.")
    print("Propagate: run `python -m pipeline.db_import --spots-only` (or the scheduled pipeline) "
          "to write state/region. DB not touched here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
