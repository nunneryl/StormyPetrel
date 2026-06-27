#!/usr/bin/env python3
"""Stage 2 Part A — bake the validated MOP assignments into spots_enriched.json.

For each buoy-verified CONSUME spot (scripts/mop_ca_verdicts.json, consume=true)
write, IN PLACE and ONLY for those spots:
    swell_window_source   = "cdip_mop"     (provenance tag — drives the MOP read
                                            in interpret.py + "Data courtesy of CDIP")
    mop_point_id          = matched MOP alongshore point
    mop_shore_normal      = that point's metaShoreNormal (the nearshore frame)
    mop_match_distance_m  = spot→point distance
    mop_nowcast_url       = the point's NOWCAST dataset url (so the live forecast
                            step needs no mop_points.json cache at runtime)
    mop_buoy_id           = the cross-check buoy (recovery mapping or roster)

FALL BACK spots are left EXACTLY as-is (orientation path, no MOP fields) — this is
a surgical patch like apply_orientation_relook: only the CONSUME spots change,
zero collateral on the rest. DRY RUN by default; --apply to write.

    python -m pipeline.apply_mop_assignments               # dry run (diff)
    python -m pipeline.apply_mop_assignments --apply       # write spots_enriched.json

Inputs (Mac artifacts from the rollout):
    scripts/mop_ca_verdicts.json        (48 CONSUME)
    scripts/mop_ca_buoy_recovery.json   (proposed buoy mapping; optional)
    scripts/mop_points.json             (point → url/shore_normal; for the url)
Read-only on prod until --apply; does not touch the DB.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = _ROOT / "scripts"
ENRICHED = _ROOT / "pipeline" / "spots_enriched.json"
VERDICTS = SCRIPTS / "mop_ca_verdicts.json"
RECOVERY = SCRIPTS / "mop_ca_buoy_recovery.json"
POINTS = SCRIPTS / "mop_points.json"

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_FLAVORS = ("_hindcast", "_forecast", "_ecmwf_fc")


def _slug(name):
    return _SLUG_RE.sub("-", (name or "").lower()).strip("-")


def _nowcast_url(url):
    if not url:
        return None
    for fl in _FLAVORS:
        if fl in url:
            return url.replace(fl, "_nowcast")
    return url


def build_plan():
    """Return (rows, problems): rows = per-CONSUME-spot field set to write;
    problems = spots that can't be assigned (no enriched match / no MOP url)."""
    verdicts = json.loads(VERDICTS.read_text()).get("spots", [])
    recovery = json.loads(RECOVERY.read_text()).get("assignments", {}) if RECOVERY.exists() else {}
    points = json.loads(POINTS.read_text()) if POINTS.exists() else {}
    enriched = json.loads(ENRICHED.read_text())
    by_slug = {}
    for s in enriched:
        by_slug.setdefault(_slug(s.get("name")), s)

    rows, problems = [], []
    for r in verdicts:
        if not r.get("consume"):
            continue
        slug = r["slug"]
        spot = by_slug.get(slug)
        if spot is None:
            problems.append((slug, "no spots_enriched.json match"))
            continue
        pid = r.get("mop_point")
        url = _nowcast_url((points.get(str(pid)) or {}).get("url"))
        if not url:
            problems.append((slug, f"no MOP url for point {pid} (need mop_points.json)"))
            continue
        buoy = (recovery.get(slug) or {}).get("buoy_id") or r.get("buoy_id")
        rows.append({
            "slug": slug, "name": spot.get("name"), "zone": r.get("zone"),
            "fields": {
                "swell_window_source": "cdip_mop",
                "mop_point_id": pid,
                "mop_shore_normal": r.get("shore_normal"),
                "mop_match_distance_m": r.get("dist_m"),
                "mop_nowcast_url": url,
                "mop_buoy_id": buoy,
            },
            "old_source": spot.get("swell_window_source"),
        })
    return rows, problems, enriched, by_slug


def print_dry_run(rows, problems):
    print(f"\nDRY RUN — MOP assignments → spots_enriched.json ({len(rows)} CONSUME spots)\n")
    print(f"  {'slug':26}{'zone':8}{'point':>7}{'sn':>5}{'dist_m':>7}{'buoy':>7}  old_source → cdip_mop")
    print(f"  {'-'*26} {'-'*7} {'-'*6} {'-'*4} {'-'*6} {'-'*6}")
    for r in rows:
        f = r["fields"]
        print(f"  {r['slug']:26}{(r['zone'] or '—'):8}{str(f['mop_point_id']):>7}"
              f"{(f['mop_shore_normal'] if f['mop_shore_normal'] is not None else 0):>5.0f}"
              f"{(f['mop_match_distance_m'] or 0):>7}{str(f['mop_buoy_id'] or '—'):>7}  "
              f"{r['old_source'] or '(none)'}")
    print(f"\n  fields written per spot: {', '.join(rows[0]['fields'].keys())}" if rows else "  (nothing to write)")
    print("  FALL BACK spots and every non-CONSUME spot: untouched.")
    if problems:
        print(f"\n  ⚠ {len(problems)} CONSUME spots NOT assignable (skipped):")
        for slug, why in problems:
            print(f"      {slug:26} {why}")


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
    a = ap.parse_args(argv)
    for p in (VERDICTS, ENRICHED):
        if not p.exists():
            print(f"error: missing {p}", file=sys.stderr)
            return 2

    raw = ENRICHED.read_text()
    rows, problems, enriched, by_slug = build_plan()
    print_dry_run(rows, problems)

    if not a.apply:
        print("\ndry run only — nothing written. Re-run with --apply to patch spots_enriched.json.")
        return 0
    n = apply_plan(rows, enriched, by_slug, raw.endswith("\n"))
    print(f"\nAPPLIED → {ENRICHED}: {n} spots tagged cdip_mop (+ MOP fields). "
          f"{len(problems)} skipped. FALL BACK + all others untouched.")
    print("Next: pipeline.interpret picks up cdip_mop spots automatically (additive); "
          "then db_import + revalidate. spots_enriched.json changed; DB not touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
