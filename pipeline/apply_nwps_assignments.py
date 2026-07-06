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
apply_mop_assignments. DRY RUN by default; --apply to write. The trust gate is
PER-BUOY: the assignments file carries trust_by_buoy {buoy_id: verdict}, and a spot
is tagged only if its nwps_buoy_id maps to "PASS" (else it is HELD and never tagged).
--force tags held spots anyway (testing only).

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


def build_plan(force=False):
    """(rows, problems, held, trust_by_buoy, enriched, by_slug). The trust gate is
    PER-BUOY: trust_by_buoy maps buoy_id -> verdict, and each spot is admitted only
    if trust_by_buoy[its nwps_buoy_id] == "PASS" (or *force*). rows = the admitted
    spots' field set (swell_window_source=nwps + the NWPS fields); held = spots whose
    buoy is absent from / not PASS in trust_by_buoy (skipped, never tagged); problems
    = spots that can't be matched. Raises ValueError on an old-format (global "trust")
    file — the per-buoy format is required."""
    doc = json.loads(ASSIGNMENTS.read_text())
    if "trust_by_buoy" not in doc:
        raise ValueError(
            f"{ASSIGNMENTS.name} is old-format: top-level 'trust'={doc.get('trust')!r} "
            "but no 'trust_by_buoy' map. The per-buoy format is required — migrate to "
            "trust_by_buoy {buoy_id: verdict}. Refusing to tag on the legacy global flag.")
    trust_by_buoy = doc.get("trust_by_buoy") or {}
    enriched = json.loads(ENRICHED.read_text())
    by_slug = {}
    for s in enriched:
        by_slug.setdefault(_slug(s.get("name")), s)
    rows, problems, held = [], [], []
    for a in doc.get("spots", []):
        slug = a.get("slug") or _slug(a.get("name"))
        spot = by_slug.get(slug)
        if spot is None:
            problems.append((slug, "no spots_enriched.json match"))
            continue
        if a.get("nwps_node_lat") is None or a.get("nwps_node_lng") is None:
            problems.append((slug, "assignment missing node lat/lng"))
            continue
        buoy = a.get("nwps_buoy_id")
        verdict = trust_by_buoy.get(str(buoy)) if buoy is not None else None
        if verdict != "PASS" and not force:
            held.append((slug, buoy, verdict))   # buoy not PASS -> never tag
            continue
        fields = {"swell_window_source": "nwps"}
        for k in _FIELDS:
            fields[k] = a.get(k)
        fields["nwps_grid"] = fields.get("nwps_grid") or "CG1"
        rows.append({"slug": slug, "name": spot.get("name"),
                     "old_source": spot.get("swell_window_source"), "fields": fields,
                     "buoy": buoy, "forced": verdict != "PASS"})
    return rows, problems, held, trust_by_buoy, enriched, by_slug


def print_dry_run(rows, problems, held, trust_by_buoy):
    print(f"\nDRY RUN — NWPS assignments → spots_enriched.json ({len(rows)} spots to tag)")
    passed = sorted(b for b, v in trust_by_buoy.items() if v == "PASS")
    print(f"  per-buoy trust gate: {json.dumps(trust_by_buoy)}")
    print(f"  PASS buoys: {', '.join(passed) or '(none)'}\n")
    print(f"  {'slug':24}{'wfo':5}{'grid':5}{'node lat,lng':22}{'dist_m':>7}{'buoy':>7}  old_source → nwps")
    print(f"  {'-'*24} {'-'*4} {'-'*4} {'-'*20} {'-'*6} {'-'*6}")
    for r in rows:
        f = r["fields"]
        node = f"{f['nwps_node_lat']:.4f},{f['nwps_node_lng']:.4f}"
        print(f"  {r['slug']:24}{(f['nwps_wfo'] or '—'):5}{(f['nwps_grid'] or '—'):5}{node:22}"
              f"{(f['nwps_node_distance_m'] or 0):>7}{str(f['nwps_buoy_id'] or '—'):>7}  {r['old_source'] or '(none)'}"
              f"{'  [FORCED — buoy not PASS]' if r.get('forced') else ''}")
    print(f"\n  fields written per spot: swell_window_source, {', '.join(_FIELDS)}" if rows else "  (nothing to write)")
    print("  Every non-listed spot: untouched.")
    if held:
        print(f"\n  ⊘ {len(held)} HELD — buoy not PASS in trust_by_buoy (NOT tagged):")
        for slug, buoy, verdict in held:
            why = f"verdict {verdict!r}" if verdict is not None else "absent from trust_by_buoy"
            print(f"      {slug:24} held (buoy {buoy or '—'} not PASS: {why})")
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
    ap.add_argument("--force", action="store_true",
                    help="tag spots even if their buoy is not PASS in trust_by_buoy (testing only)")
    a = ap.parse_args(argv)
    for p in (ASSIGNMENTS, ENRICHED):
        if not p.exists():
            print(f"error: missing {p}"
                  + (" — run `nwps_nearshore --validate` on the Mac first" if p is ASSIGNMENTS else ""),
                  file=sys.stderr)
            return 2

    raw = ENRICHED.read_text()
    try:
        rows, problems, held, trust_by_buoy, enriched, by_slug = build_plan(force=a.force)
    except ValueError as e:
        print(f"\nerror: {e}", file=sys.stderr)
        return 2
    print_dry_run(rows, problems, held, trust_by_buoy)

    if not a.apply:
        print("\ndry run only — nothing written. Re-run with --apply to patch spots_enriched.json "
              "(only spots whose buoy is PASS in trust_by_buoy).")
        return 0
    n = apply_plan(rows, enriched, by_slug, raw.endswith("\n"))
    print(f"\nAPPLIED → {ENRICHED}: {n} spots tagged nwps (+ node fields). "
          f"{len(held)} held (buoy not PASS), {len(problems)} skipped. All others untouched."
          f"{'  [FORCED]' if a.force else ''}")
    print("Next: pipeline.interpret picks up nwps spots (additive); then db_import. DB not touched here.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
