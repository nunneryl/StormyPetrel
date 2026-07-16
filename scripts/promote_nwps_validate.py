#!/usr/bin/env python3
"""Promote --validate node coords into the curated NWPS assignments 'spots' list.

Reads a per-region diagnostic dump scripts/nwps_{wfo}_validate_out.json (written by
`python -m pipeline.forecast.nwps_nearshore --validate --wfo {wfo}` on the Mac) and merges each
PLACEABLE spot into scripts/nwps_okx_assignments.json's 'spots' list with its node fields and a
reference buoy. Placeable = outcome OK or OFFWIN — OFFWIN is a per-cycle direction condition, not a
placement failure (the node is valid; the sea just reads correctly-flat when off-window), so those
spots still get placed for HEIGHT. Genuine failures (FAR / DEAD / NO_WET_CELL — no valid node) are
skipped. Dedupes by slug (an existing entry is replaced).

This ONLY supplies the node coords the apply gate requires. It does NOT touch trust_by_buoy or
buoy_reference — placement of these spots relies on the pending / height-only path
(apply_nwps_assignments: buoy listed in buoy_reference.pending[] -> direction_status 'pending').
It sets NO trust PASS.

    python3 scripts/promote_nwps_validate.py --validate-out scripts/nwps_mtr_validate_out.json --buoy 46240
    python3 scripts/promote_nwps_validate.py --validate-out scripts/nwps_mtr_validate_out.json --buoy 46240 --apply
"""
from __future__ import annotations

import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ASSIGNMENTS = os.path.join(os.path.dirname(HERE), "scripts", "nwps_okx_assignments.json")

_PLACEABLE = ("OK", "OFFWIN")   # OFFWIN keeps a valid node — placed for height; off-window is per-cycle
_ENTRY_FIELDS = ("slug", "name", "nwps_wfo", "nwps_grid", "nwps_node_lat", "nwps_node_lng",
                 "nwps_node_distance_m", "nwps_buoy_id")


def build_promotions(validate_out, buoy, *, only_slugs=None):
    """[assignment spot entries] from a --validate output doc. Uses the diagnostic 'outcomes'
    (every spot: outcome + node lat/lng/dist) for coverage, preferring the richer 'spots' (OK-only,
    full fields) entry when present. Includes OK + OFFWIN with a valid node; sets nwps_buoy_id=*buoy*
    and nwps_grid='CG1'. Skips FAR / DEAD / NO_WET_CELL (no valid node) and anything not in
    *only_slugs* (when given). Pure/offline — invents no coordinates, only relays --validate's."""
    wfo = validate_out.get("grid_wfo")
    ok = {s.get("slug"): s for s in (validate_out.get("spots") or []) if s.get("slug")}
    out, seen, skipped = [], set(), []
    for o in (validate_out.get("outcomes") or []):
        slug = o.get("slug")
        if not slug or slug in seen:
            continue
        if only_slugs is not None and slug not in only_slugs:
            continue
        outcome = o.get("outcome")
        if outcome not in _PLACEABLE:
            skipped.append((slug, outcome))
            continue
        src = ok.get(slug, o)   # prefer the full OK entry; else the outcome entry (OFFWIN etc.)
        if src.get("nwps_node_lat") is None or src.get("nwps_node_lng") is None:
            skipped.append((slug, f"{outcome} but no node"))
            continue
        out.append({
            "slug": slug, "name": o.get("name") or src.get("name"),
            "nwps_wfo": wfo, "nwps_grid": src.get("nwps_grid") or "CG1",
            "nwps_node_lat": src.get("nwps_node_lat"), "nwps_node_lng": src.get("nwps_node_lng"),
            "nwps_node_distance_m": src.get("nwps_node_distance_m"),
            "nwps_buoy_id": str(buoy),
            "_outcome": outcome,   # informational for the dry-run; stripped before writing
        })
        seen.add(slug)
    return out, skipped


def merge_into_assignments(doc, promotions):
    """Merge promotion entries into doc['spots'] (replace by slug, else append; strip _* keys).
    Returns (added, replaced). Mutates doc."""
    spots = doc.setdefault("spots", [])
    idx = {s.get("slug"): i for i, s in enumerate(spots)}
    added = replaced = 0
    for p in promotions:
        entry = {k: p[k] for k in _ENTRY_FIELDS}
        if p["slug"] in idx:
            spots[idx[p["slug"]]] = entry
            replaced += 1
        else:
            idx[p["slug"]] = len(spots)
            spots.append(entry)
            added += 1
    return added, replaced


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--validate-out", required=True, help="scripts/nwps_{wfo}_validate_out.json")
    ap.add_argument("--buoy", required=True, help="reference buoy id (nwps_buoy_id) for these spots")
    ap.add_argument("--slugs", default=None,
                    help="comma-separated slugs to restrict this run to (so one --buoy batch never "
                         "picks up other spots that happen to be in the validate-out). Default: all placeable.")
    ap.add_argument("--apply", action="store_true", help="write the assignments JSON (default: dry run)")
    a = ap.parse_args(argv)
    if not os.path.exists(a.validate_out):
        print(f"error: missing {a.validate_out} — run `nwps_nearshore --validate --wfo <wfo>` on the "
              "Mac and commit the output first", file=sys.stderr)
        return 2
    only_slugs = {s.strip() for s in a.slugs.split(",") if s.strip()} if a.slugs else None
    validate_out = json.loads(open(a.validate_out).read())
    proms, skipped = build_promotions(validate_out, a.buoy, only_slugs=only_slugs)

    raw = open(ASSIGNMENTS).read()
    doc = json.loads(raw)
    existing = {s.get("slug") for s in doc.get("spots", [])}

    print(f"\n{'DRY RUN' if not a.apply else 'APPLY'} — promote {a.validate_out} → assignments 'spots' "
          f"(buoy {a.buoy}, wfo {validate_out.get('grid_wfo')})\n")
    print(f"  {'slug':30}{'outcome':9}{'node lat,lng':22}{'dist_m':>7}{'buoy':>8}  new/replace")
    for p in proms:
        node = f"{p['nwps_node_lat']},{p['nwps_node_lng']}"
        print(f"  {p['slug']:30}{p['_outcome']:9}{node:22}{(p['nwps_node_distance_m'] or 0):>7}"
              f"{p['nwps_buoy_id']:>8}  {'replace' if p['slug'] in existing else 'new'}")
    if skipped:
        print(f"\n  skipped {len(skipped)} (no valid node / not placeable): "
              + ", ".join(f"{s}({o})" for s, o in skipped))
    print(f"\n  {len(proms)} placeable spot(s). This sets NO trust PASS and does not touch "
          "trust_by_buoy / buoy_reference. Placement then relies on the buoy_reference.pending[] path.")

    if not a.apply:
        print("\ndry run only — nothing written. Re-run with --apply to merge into the assignments JSON.")
        return 0
    added, replaced = merge_into_assignments(doc, proms)
    text = json.dumps(doc, indent=2, ensure_ascii=False)
    if raw.endswith("\n"):
        text += "\n"
    open(ASSIGNMENTS, "w").write(text)
    print(f"\nMERGED → {ASSIGNMENTS}: {added} added, {replaced} replaced. trust_by_buoy untouched "
          "(no PASS set). Next: review, then `apply_nwps_assignments` (places them as 'pending').")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
