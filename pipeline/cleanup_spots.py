"""Apply manual cleanup decisions to spots_enriched.json.

Two data files drive this:

  - pipeline/data/excluded_spots.json    — names to permanently remove
  - pipeline/data/spot_coord_fixes.json  — coord patches for known spots

What the module does, in order:

  1. Load spots_enriched.json.
  2. Apply coord fixes. Each fixed spot also has its
     is_valid_surf_spot flag cleared (set to True) and its matching
     spot_verification.json entry reset so the next verify run can
     regenerate orientation / tide / crowd / hazards against the
     corrected coordinates.
  3. Remove every excluded spot.
  4. Write the cleaned file back.

The exclusion list is also consulted by seed_spots.py, so excluded
spots never re-enter the pipeline on subsequent source crawls.

CLI:
    python -m pipeline.cleanup_spots [--input ...] [--output ...]
                                     [--verification-file ...] [--dry-run] [-v]
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from .config import (
    DEFAULT_ENRICHED_OUTPUT,
    EXCLUDED_SPOTS_FILE,
    SPOT_COORD_FIXES_FILE,
    SPOT_VERIFICATION_FILE,
)

log = logging.getLogger("pipeline.cleanup_spots")

_RESERVED_KEYS = {"_comment", "_schema_version"}


def load_excluded_names(path: Path = EXCLUDED_SPOTS_FILE) -> dict[str, str]:
    """Return {spot_name: reason} from the exclusion file.

    Reserved keys (``_comment``, ``_schema_version``) are ignored. Every
    other top-level key is treated as a reason whose value is a list of
    spot names.
    """
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    out: dict[str, str] = {}
    for reason, names in data.items():
        if reason in _RESERVED_KEYS:
            continue
        if not isinstance(names, list):
            continue
        for name in names:
            if isinstance(name, str):
                out[name] = reason
    return out


def load_coord_fixes(path: Path = SPOT_COORD_FIXES_FILE) -> dict[str, dict]:
    """Return {spot_name: {lat, lng, note?}} from the coord-fixes file."""
    if not path.exists():
        return {}
    data = json.loads(path.read_text())
    fixes = data.get("fixes") or {}
    out: dict[str, dict] = {}
    for name, patch in fixes.items():
        if not isinstance(patch, dict):
            continue
        try:
            lat = float(patch["lat"])
            lng = float(patch["lng"])
        except (KeyError, TypeError, ValueError):
            log.warning("coord fix for %r missing or invalid lat/lng; skipping", name)
            continue
        out[name] = {"lat": lat, "lng": lng, "note": patch.get("note", "")}
    return out


def apply_cleanup(
    spots: list[dict],
    excluded: dict[str, str],
    coord_fixes: dict[str, dict],
    verifications: dict[str, dict] | None = None,
) -> tuple[list[dict], dict]:
    """Return (cleaned_spots, stats) and mutate *verifications* in place.

    Stats shape:
        {
          "before": int,
          "removed": int,
          "coord_fixed": int,
          "not_found_excluded": [names],
          "not_found_fixed": [names],
          "after": int,
          "removed_by_reason": {reason: count},
          "fixed_details": [{"name", "old_lat", "old_lng", "new_lat", "new_lng"}],
        }
    """
    stats: dict = {
        "before": len(spots),
        "removed": 0,
        "coord_fixed": 0,
        "not_found_excluded": [],
        "not_found_fixed": [],
        "removed_by_reason": {},
        "fixed_details": [],
    }

    # Apply coord fixes first so a name that appears in both lists (shouldn't
    # happen, but be defensive) gets fixed and then removed rather than
    # ghost-removed.
    names_seen: set[str] = set()
    for spot in spots:
        name = spot.get("name")
        if not name:
            continue
        names_seen.add(name)
        patch = coord_fixes.get(name)
        if patch is None:
            continue
        old_lat = spot.get("lat")
        old_lng = spot.get("lng")
        spot["lat"] = patch["lat"]
        spot["lng"] = patch["lng"]
        # Clear stale algorithmic outputs that depend on coordinates — next
        # enrich + verify pass regenerates them against the corrected coords.
        for k in (
            "coord_adjusted",
            "orientation_50m", "orientation_deg", "orientation_200m",
            "offshore_wind_deg", "orientation_flipped",
            "swell_window_arcs", "optimal_swell_dir", "swell_window_source",
            "nearest_buoy_id", "nearest_buoy_dist_km", "fallback_buoy_ids",
            "nearest_tide_station_id", "nearest_tide_station_dist_km",
        ):
            if k in spot:
                spot.pop(k, None)
        # Clear the invalid flag so downstream stops filtering it out.
        spot["is_valid_surf_spot"] = True
        spot.pop("invalid_reason", None)
        spot["coord_fix_applied"] = True
        spot["coord_fix_note"] = patch.get("note", "")

        # Reset the matching verification entry so verify_spots picks this
        # spot up as pending again.
        if verifications is not None and name in verifications:
            del verifications[name]

        stats["coord_fixed"] += 1
        stats["fixed_details"].append({
            "name": name,
            "old_lat": old_lat, "old_lng": old_lng,
            "new_lat": patch["lat"], "new_lng": patch["lng"],
            "note": patch.get("note", ""),
        })

    # Warn about coord-fix entries that didn't match anything in the file.
    for name in coord_fixes:
        if name not in names_seen:
            stats["not_found_fixed"].append(name)

    # Remove excluded spots.
    cleaned: list[dict] = []
    excluded_seen: set[str] = set()
    for spot in spots:
        name = spot.get("name")
        if name and name in excluded:
            reason = excluded[name]
            stats["removed"] += 1
            stats["removed_by_reason"][reason] = stats["removed_by_reason"].get(reason, 0) + 1
            excluded_seen.add(name)
            # Also purge from verifications — the spot no longer exists.
            if verifications is not None and name in verifications:
                del verifications[name]
            continue
        cleaned.append(spot)

    for name in excluded:
        if name not in excluded_seen:
            stats["not_found_excluded"].append(name)

    stats["after"] = len(cleaned)
    return cleaned, stats


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Apply manual cleanup to spots_enriched.json.")
    p.add_argument("--input", type=Path, default=DEFAULT_ENRICHED_OUTPUT,
                   help="Input/output spots_enriched.json (updated in place by default).")
    p.add_argument("--output", type=Path, default=None,
                   help="Output path (defaults to --input).")
    p.add_argument("--verification-file", type=Path, default=SPOT_VERIFICATION_FILE,
                   help="spot_verification.json to reset for coord-fixed / removed spots.")
    p.add_argument("--excluded-file", type=Path, default=EXCLUDED_SPOTS_FILE)
    p.add_argument("--coord-fixes-file", type=Path, default=SPOT_COORD_FIXES_FILE)
    p.add_argument("--dry-run", action="store_true",
                   help="Report what would change but don't write files.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def _summarize(stats: dict) -> None:
    print()
    print("=" * 60)
    print("Cleanup summary")
    print("=" * 60)
    print(f"  spots before:          {stats['before']}")
    print(f"  spots after:           {stats['after']}")
    print(f"  removed:               {stats['removed']}")
    for reason, n in sorted(stats["removed_by_reason"].items()):
        print(f"    {reason:<14} {n}")
    print(f"  coord-fixed:           {stats['coord_fixed']}")
    for f in stats["fixed_details"]:
        print(
            f"    {f['name']}: ({f['old_lat']}, {f['old_lng']}) → "
            f"({f['new_lat']}, {f['new_lng']})"
        )
        if f["note"]:
            print(f"      note: {f['note']}")
    if stats["not_found_excluded"]:
        print(f"  excluded names not present in input ({len(stats['not_found_excluded'])}):")
        for name in stats["not_found_excluded"]:
            print(f"    {name}")
    if stats["not_found_fixed"]:
        print(f"  coord-fix names not present in input ({len(stats['not_found_fixed'])}):")
        for name in stats["not_found_fixed"]:
            print(f"    {name}")
    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if not args.input.exists():
        log.error("Input file %s does not exist.", args.input)
        return 1

    excluded = load_excluded_names(args.excluded_file)
    coord_fixes = load_coord_fixes(args.coord_fixes_file)
    log.info(
        "cleanup: %d excluded names, %d coord fixes",
        len(excluded), len(coord_fixes),
    )

    spots = json.loads(args.input.read_text())
    log.info("Loaded %d spots from %s", len(spots), args.input)

    verifications: dict[str, dict] | None = None
    if args.verification_file.exists():
        try:
            verifications = json.loads(args.verification_file.read_text())
        except json.JSONDecodeError as e:
            log.warning("verification file %s corrupt (%s); not touching it",
                        args.verification_file, e)
            verifications = None

    cleaned, stats = apply_cleanup(spots, excluded, coord_fixes, verifications)

    _summarize(stats)

    if args.dry_run:
        print("  (dry-run: no files written)")
        return 0

    output_path = args.output or args.input
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(cleaned, indent=2, ensure_ascii=False))
    log.info("Wrote %d cleaned spots to %s", len(cleaned), output_path)

    if verifications is not None:
        args.verification_file.write_text(
            json.dumps(verifications, indent=2, ensure_ascii=False, sort_keys=True)
        )
        log.info("Updated verification file %s (purged removed + coord-fixed spots)",
                 args.verification_file)

    return 0


if __name__ == "__main__":
    sys.exit(main())
