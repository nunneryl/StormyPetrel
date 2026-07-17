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
With --no-buoy the spots are promoted with nwps_buoy_id=null for the buoy_reference.unverifiable[]
path (island-shadowed spots whose direction is never buoy-verifiable; their slugs get listed under
buoy_reference.unverifiable[] separately, which is what makes apply place them 'unverifiable').
It sets NO trust PASS.

    python3 scripts/promote_nwps_validate.py --validate-out scripts/nwps_mtr_validate_out.json --buoy 46240
    python3 scripts/promote_nwps_validate.py --validate-out scripts/nwps_mtr_validate_out.json --buoy 46240 --apply
    python3 scripts/promote_nwps_validate.py --validate-out scripts/nwps_lox_validate_out.json --no-buoy --slugs jalama
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


def build_promotions(validate_out, buoy, *, only_slugs=None, wfo_override=None):
    """[assignment spot entries] from a --validate output doc. Uses the diagnostic 'outcomes'
    (every spot: outcome + node lat/lng/dist) for coverage, preferring the richer 'spots' (OK-only,
    full fields) entry when present. Includes OK + OFFWIN with a valid node; sets nwps_buoy_id=*buoy*
    (or null when *buoy* is None — the --no-buoy / unverifiable[] path) and nwps_grid='CG1'. Sets
    nwps_wfo to *wfo_override* when given, else the validate-out's grid_wfo — the override keeps a
    grid-CROSSING spot's region label when its HEIGHT is supplied by a different grid (e.g. the SLO
    Pismo spots whose nodes come from the lox grid but whose nwps_wfo must stay 'mtr'). Skips
    FAR / DEAD / NO_WET_CELL (no valid node) and anything not in *only_slugs* (when given).
    Pure/offline — invents no coordinates, only relays --validate's."""
    wfo = wfo_override or validate_out.get("grid_wfo")
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
            "nwps_buoy_id": None if buoy is None else str(buoy),
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
    ap.add_argument("--buoy", default=None,
                    help="reference buoy id (nwps_buoy_id) for these spots. Omit and pass --no-buoy "
                         "for buoy_reference.unverifiable[] (island-shadowed) spots.")
    ap.add_argument("--no-buoy", action="store_true",
                    help="promote with NO buoy id (nwps_buoy_id=null) — for unverifiable[] spots whose "
                         "direction is never buoy-verifiable. Mutually exclusive with --buoy.")
    ap.add_argument("--slugs", default=None,
                    help="comma-separated slugs to restrict this run to (so one --buoy batch never "
                         "picks up other spots that happen to be in the validate-out). Default: all placeable.")
    ap.add_argument("--wfo", default=None,
                    help="override nwps_wfo for the promoted spots (default: the validate-out's grid_wfo). "
                         "For grid-CROSSING spots whose HEIGHT comes from one grid but whose region label "
                         "must stay another — e.g. the SLO Pismo spots validated on the lox grid but kept "
                         "as nwps_wfo=mtr (`--buoy 46215 --wfo mtr` on the lox validate-out).")
    ap.add_argument("--apply", action="store_true", help="write the assignments JSON (default: dry run)")
    a = ap.parse_args(argv)
    if a.no_buoy and a.buoy:
        print("error: pass EITHER --buoy <id> OR --no-buoy, not both", file=sys.stderr)
        return 2
    if not a.no_buoy and not a.buoy:
        print("error: pass --buoy <id> (or --no-buoy for buoy_reference.unverifiable[] spots)", file=sys.stderr)
        return 2
    buoy = None if a.no_buoy else a.buoy
    if not os.path.exists(a.validate_out):
        print(f"error: missing {a.validate_out} — run `nwps_nearshore --validate --wfo <wfo>` on the "
              "Mac and commit the output first", file=sys.stderr)
        return 2
    only_slugs = {s.strip() for s in a.slugs.split(",") if s.strip()} if a.slugs else None
    validate_out = json.loads(open(a.validate_out).read())
    proms, skipped = build_promotions(validate_out, buoy, only_slugs=only_slugs, wfo_override=a.wfo)

    raw = open(ASSIGNMENTS).read()
    doc = json.loads(raw)
    existing = {s.get("slug") for s in doc.get("spots", [])}

    eff_wfo = a.wfo or validate_out.get("grid_wfo")
    wfo_note = f"{eff_wfo} (override; grid {validate_out.get('grid_wfo')})" if a.wfo else eff_wfo
    print(f"\n{'DRY RUN' if not a.apply else 'APPLY'} — promote {a.validate_out} → assignments 'spots' "
          f"(buoy {buoy or 'none (unverifiable[])'}, wfo {wfo_note})\n")
    print(f"  {'slug':30}{'outcome':9}{'node lat,lng':22}{'dist_m':>7}{'buoy':>8}  new/replace")
    for p in proms:
        node = f"{p['nwps_node_lat']},{p['nwps_node_lng']}"
        print(f"  {p['slug']:30}{p['_outcome']:9}{node:22}{(p['nwps_node_distance_m'] or 0):>7}"
              f"{str(p['nwps_buoy_id'] or 'none'):>8}  {'replace' if p['slug'] in existing else 'new'}")
    if skipped:
        print(f"\n  skipped {len(skipped)} (no valid node / not placeable): "
              + ", ".join(f"{s}({o})" for s, o in skipped))
    path = "buoy_reference.unverifiable[]" if buoy is None else "buoy_reference.pending[]"
    print(f"\n  {len(proms)} placeable spot(s). This sets NO trust PASS and does not touch "
          f"trust_by_buoy / buoy_reference. Placement then relies on the {path} path.")

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
