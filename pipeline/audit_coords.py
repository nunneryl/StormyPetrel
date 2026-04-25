"""Reverse-geocode every spot's coords and flag region_hint mismatches.

Diagnostic tool — surfaces spots whose (lat, lng) places them in a
different state or country than their `region_hint` claims. The classic
bugs this catches:

  - V-Land at (54.09, 10.87)              region_hint=Hawaii        → DE
  - Westport Beach at (44.18, -73.43)     region_hint=Massachusetts → NY (Lake Champlain)
  - Antonio's Rincon at (10.14, -64.55)   region_hint=Puerto Rico   → VE
  - "Crash Boat" coord pasted into wrong spot, etc.

What it does NOT catch:
  - Within-state-but-wrong-location bugs (e.g. Avalon NJ at coords in
    inland NJ near Livingston rather than the actual barrier island).
    For those you need a coastline-distance check, which lives outside
    this module.

Output is informational only — does not modify spots_enriched.json.
Each surfaced mismatch is a candidate for `data/spot_coord_fixes.json`
or `data/excluded_spots.json` after manual review.

CLI:
    python -m pipeline.audit_coords
    python -m pipeline.audit_coords --include-invalid
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import DEFAULT_ENRICHED_OUTPUT

log = logging.getLogger("pipeline.audit_coords")


# region_hint → expected (cc, admin1). Most US states are (US, <state>);
# territories live under their own ISO codes per reverse_geocoder.
_TERRITORY_CC = {
    "Puerto Rico": "PR",
    "Guam": "GU",
    "American Samoa": "AS",
    "U.S. Virgin Islands": "VI",
}


def _expected_for_region(region_hint: str | None) -> tuple[str, str | None] | None:
    """Return (expected_cc, expected_admin1) for a region_hint, or None
    when we can't make a reasonable expectation (no hint set, or a
    region we don't have a mapping for).
    """
    if not region_hint:
        return None
    if region_hint in _TERRITORY_CC:
        return (_TERRITORY_CC[region_hint], None)
    return ("US", region_hint)


def audit(spots: list[dict], only_valid: bool = True) -> tuple[list[dict], list[tuple]]:
    """Reverse-geocode each spot; return (mismatches, no_region).

    mismatches is a list of dicts with name / coords / kind /
    expected / actual fields. no_region is a list of (spot, rev_dict)
    tuples for spots whose region_hint is missing — informational
    only, those don't trigger a flag.
    """
    import reverse_geocoder as rg

    targets = [
        s for s in spots
        if s.get("name")
        and s.get("lat") is not None
        and s.get("lng") is not None
        and (not only_valid or s.get("is_valid_surf_spot") is not False)
    ]
    if not targets:
        return [], []

    coords = [(float(s["lat"]), float(s["lng"])) for s in targets]
    log.info("audit_coords: reverse-geocoding %d spots", len(coords))
    # mode=1 is single-process, fine for our small set; mode=2 forks workers.
    results = rg.search(coords, mode=1)

    mismatches: list[dict] = []
    no_region: list[tuple] = []

    for spot, rev in zip(targets, results):
        region = spot.get("region_hint")
        expected = _expected_for_region(region)
        actual_cc = rev.get("cc")
        actual_admin1 = rev.get("admin1")

        if expected is None:
            no_region.append((spot, rev))
            continue

        ecc, eadmin1 = expected
        if actual_cc != ecc:
            mismatches.append({
                "name": spot["name"],
                "region_hint": region,
                "expected_cc": ecc,
                "actual_cc": actual_cc,
                "actual_admin1": actual_admin1,
                "lat": spot["lat"],
                "lng": spot["lng"],
                "kind": "country",
            })
        elif eadmin1 is not None and actual_admin1 != eadmin1:
            mismatches.append({
                "name": spot["name"],
                "region_hint": region,
                "expected_admin1": eadmin1,
                "actual_cc": actual_cc,
                "actual_admin1": actual_admin1,
                "lat": spot["lat"],
                "lng": spot["lng"],
                "kind": "state",
            })

    return mismatches, no_region


def _summarize(mismatches: list[dict], no_region: list[tuple]) -> None:
    print()
    print("=" * 60)
    print(f"Coord audit: {len(mismatches)} mismatches, {len(no_region)} with no region_hint")
    print("=" * 60)

    by_kind: dict[str, list[dict]] = {}
    for m in mismatches:
        by_kind.setdefault(m["kind"], []).append(m)

    for kind in ("country", "state"):
        items = by_kind.get(kind) or []
        if not items:
            continue
        label = "COUNTRY" if kind == "country" else "STATE"
        print(f"\n  {label} mismatches ({len(items)}):")
        for m in sorted(items, key=lambda x: x["name"]):
            print(f"    {m['name']:<45} ({m['lat']:.4f}, {m['lng']:.4f})")
            if kind == "country":
                print(
                    f"      region_hint={m['region_hint']!r}, "
                    f"expected cc={m['expected_cc']}, "
                    f"actual cc={m['actual_cc']} / admin1={m['actual_admin1']!r}"
                )
            else:
                print(
                    f"      region_hint={m['region_hint']!r}, "
                    f"expected admin1={m['expected_admin1']!r}, "
                    f"actual admin1={m['actual_admin1']!r} ({m['actual_cc']})"
                )

    if no_region:
        print(f"\n  Spots with no region_hint ({len(no_region)}):")
        for spot, rev in no_region[:10]:
            print(
                f"    {spot['name']:<45} ({spot['lat']:.4f}, {spot['lng']:.4f}) "
                f"→ {rev.get('admin1')!r} ({rev.get('cc')})"
            )
        if len(no_region) > 10:
            print(f"    ... ({len(no_region) - 10} more)")
    print("=" * 60)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    p.add_argument("--input", type=Path, default=DEFAULT_ENRICHED_OUTPUT,
                   help="spots_enriched.json to audit")
    p.add_argument("--include-invalid", action="store_true",
                   help="Include spots with is_valid_surf_spot=false (default skips them).")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if not args.input.exists():
        log.error("Input file %s does not exist.", args.input)
        return 1
    spots = json.loads(args.input.read_text())
    mismatches, no_region = audit(spots, only_valid=not args.include_invalid)
    _summarize(mismatches, no_region)
    return 0


if __name__ == "__main__":
    sys.exit(main())
