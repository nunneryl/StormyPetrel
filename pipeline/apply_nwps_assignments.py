#!/usr/bin/env python3
"""Stage 2 (NWPS OKX pilot) Part A — bake validated NWPS assignments into
spots_enriched.json. Sibling of apply_mop_assignments.py.

For each placement-OK spot from the pilot run (scripts/nwps_okx_assignments.json,
written by `pipeline.forecast.nwps_nearshore --validate`) — and ONLY once the
WFO's buoy trust gate has PASSED — write, IN PLACE and only for those spots:
    swell_window_source   = "nwps"      (provenance tag — drives apply_nwps_overrides)
    nwps_wfo, nwps_grid="CG1"
    nwps_node_lat, nwps_node_lng        (the seaward node the rater samples)
    nwps_node_distance_m                (spot → node distance)
    nwps_buoy_id                        (the buoy that anchored the trust check)

Every other spot is left exactly as-is — a surgical patch like
apply_mop_assignments. DRY RUN by default; --apply to write. INERT until the
assignments file carries "trust":"PASS" (run --trustcheck first); --force overrides
for testing only.

    python -m pipeline.apply_nwps_assignments               # dry run (diff)
    python -m pipeline.apply_nwps_assignments --apply        # write (requires trust PASS)

Read-only on prod until --apply; does not touch the DB. Propagate after: next
`pipeline.interpret` picks up nwps spots automatically (additive), then db_import.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
ENRICHED = _ROOT / "pipeline" / "spots_enriched.json"
ASSIGNMENTS = _ROOT / "scripts" / "nwps_okx_assignments.json"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_FIELDS = ("nwps_wfo", "nwps_grid", "nwps_node_lat", "nwps_node_lng",
           "nwps_node_distance_m", "nwps_buoy_id")


def _slug(name):
    return _SLUG_RE.sub("-", (name or "").lower()).strip("-")


def build_plan():
    """(rows, problems, trust, enriched, by_slug). rows = per-spot field set to
    write (swell_window_source=nwps + the NWPS fields); problems = spots that
    can't be assigned."""
    doc = json.loads(ASSIGNMENTS.read_text())
    trust = doc.get("trust")
    enriched = json.loads(ENRICHED.read_text())
    by_slug = {}
    for s in enriched:
        by_slug.setdefault(_slug(s.get("name")), s)
    rows, problems = [], []
    for a in doc.get("spots", []):
        slug = a.get("slug") or _slug(a.get("name"))
        spot = by_slug.get(slug)
        if spot is None:
            problems.append((slug, "no spots_enriched.json match"))
            continue
        if a.get("nwps_node_lat") is None or a.get("nwps_node_lng") is None:
            problems.append((slug, "assignment missing node lat/lng"))
            continue
        fields = {"swell_window_source": "nwps"}
        for k in _FIELDS:
            fields[k] = a.get(k)
        fields["nwps_grid"] = fields.get("nwps_grid") or "CG1"
        rows.append({"slug": slug, "name": spot.get("name"),
                     "old_source": spot.get("swell_window_source"), "fields": fields})
    return rows, problems, trust, enriched, by_slug


def print_dry_run(rows, problems, trust):
    print(f"\nDRY RUN — NWPS assignments → spots_enriched.json ({len(rows)} placed spots)")
    print(f"  WFO trust gate: {trust or '(absent — run --trustcheck)'}\n")
    print(f"  {'slug':24}{'wfo':5}{'grid':5}{'node lat,lng':22}{'dist_m':>7}{'buoy':>7}  old_source → nwps")
    print(f"  {'-'*24} {'-'*4} {'-'*4} {'-'*20} {'-'*6} {'-'*6}")
    for r in rows:
        f = r["fields"]
        node = f"{f['nwps_node_lat']:.4f},{f['nwps_node_lng']:.4f}"
        print(f"  {r['slug']:24}{(f['nwps_wfo'] or '—'):5}{(f['nwps_grid'] or '—'):5}{node:22}"
              f"{(f['nwps_node_distance_m'] or 0):>7}{str(f['nwps_buoy_id'] or '—'):>7}  {r['old_source'] or '(none)'}")
    print(f"\n  fields written per spot: swell_window_source, {', '.join(_FIELDS)}" if rows else "  (nothing to write)")
    print("  Every non-listed spot: untouched.")
    if problems:
        print(f"\n  ⚠ {len(problems)} not assignable (skipped):")
        for slug, why in problems:
            print(f"      {slug:24} {why}")


def apply_plan(rows, enriched, by_slug, had_trailing_nl):
    n = 0
    for r in rows:
        spot = by_slug.get(r["slug"])
        if spot is None:
            continue
        spot.update(r["fields"])
        n += 1
    text = json.dumps(enriched, indent=2, ensure_ascii=False)
    if had_trailing_nl:
        text += "\n"
    ENRICHED.write_text(text)
    return n


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--apply", action="store_true", help="write spots_enriched.json (default: dry run)")
    ap.add_argument("--force", action="store_true", help="apply even if trust != PASS (testing only)")
    a = ap.parse_args(argv)
    for p in (ASSIGNMENTS, ENRICHED):
        if not p.exists():
            print(f"error: missing {p}"
                  + (" — run `nwps_nearshore --validate` on the Mac first" if p is ASSIGNMENTS else ""),
                  file=sys.stderr)
            return 2

    raw = ENRICHED.read_text()
    rows, problems, trust, enriched, by_slug = build_plan()
    print_dry_run(rows, problems, trust)

    if not a.apply:
        print("\ndry run only — nothing written. Re-run with --apply (after trust PASS) to patch spots_enriched.json.")
        return 0
    if trust != "PASS" and not a.force:
        print(f"\nREFUSING to apply: WFO trust gate is {trust!r}, not PASS. Run "
              "`nwps_nearshore --trustcheck` on a real swell; tag only on PASS (or --force for testing).",
              file=sys.stderr)
        return 1
    n = apply_plan(rows, enriched, by_slug, raw.endswith("\n"))
    print(f"\nAPPLIED → {ENRICHED}: {n} spots tagged nwps (+ node fields). {len(problems)} skipped. "
          f"All others untouched.{'  [FORCED — trust not PASS]' if trust != 'PASS' else ''}")
    print("Next: pipeline.interpret picks up nwps spots (additive); then db_import. DB not touched here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
