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
import re
import sys
from pathlib import Path

from .config import (
    COORD_FIX_MAX_MOVE_KM,
    DEFAULT_ENRICHED_OUTPUT,
    EXCLUDED_SPOTS_FILE,
    SPOT_COORD_FIXES_FILE,
    SPOT_VERIFICATION_FILE,
)
from .geo import haversine_m

log = logging.getLogger("pipeline.cleanup_spots")

_RESERVED_KEYS = {"_comment", "_schema_version"}
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(name: str | None) -> str:
    """Lowercase, hyphen-join, drop non-[a-z0-9]. Mirrors db_import._slugify so slug-keyed coord
    fixes match the same slug the DB upserts on."""
    return _SLUG_RE.sub("-", (name or "").lower()).strip("-")


# Coastal-state bounding boxes (lat_min, lat_max, lng_min, lng_max) for the plausibility guard's
# "moved into a different state" check. Coarse on purpose — it only needs to catch gross cross-state
# teleports (a Florida spot landing in California), not adjacent-border nuance.
_STATE_BBOX = {
    "California": (32.3, 42.1, -124.6, -114.0), "Oregon": (41.9, 46.4, -124.7, -116.3),
    "Washington": (45.4, 49.1, -124.9, -116.8), "Hawaii": (18.8, 22.4, -160.4, -154.7),
    "Texas": (25.7, 36.6, -106.8, -93.4), "Louisiana": (28.8, 33.1, -94.2, -88.7),
    "Mississippi": (30.0, 35.1, -91.8, -88.0), "Alabama": (30.0, 35.1, -88.6, -84.8),
    "Florida": (24.3, 31.1, -87.8, -79.8), "Georgia": (30.2, 35.1, -85.8, -80.7),
    "South Carolina": (31.9, 35.4, -83.6, -78.4), "North Carolina": (33.7, 36.7, -84.5, -75.3),
    "Virginia": (36.4, 39.6, -83.8, -75.1), "Maryland": (37.8, 39.9, -79.6, -74.9),
    "Delaware": (38.3, 40.0, -75.9, -74.9), "New Jersey": (38.8, 41.5, -75.7, -73.8),
    "New York": (40.4, 45.1, -79.9, -71.7), "Connecticut": (40.8, 42.2, -73.8, -71.7),
    "Rhode Island": (41.0, 42.1, -72.0, -71.0), "Massachusetts": (41.1, 43.0, -73.6, -69.8),
    "New Hampshire": (42.6, 45.4, -72.7, -70.5), "Maine": (42.9, 47.6, -71.2, -66.8),
    "Puerto Rico": (17.8, 18.7, -67.4, -65.1),
}


def _state_of(lat, lng) -> str | None:
    """Coarse reverse-geocode a coordinate to a coastal state via bounding box, or None if outside all
    boxes / coords missing. Overlaps are possible near borders; used only for the guard's cross-state
    flag, which is one of two independent triggers (the other is raw distance)."""
    if lat is None or lng is None:
        return None
    for st, (a, b, c, d) in _STATE_BBOX.items():
        if a <= lat <= b and c <= lng <= d:
            return st
    return None


def normalize_name(name: str | None) -> str:
    """Canonicalize a spot name for exclusion matching.

    OSM / Wikipedia sources use a mix of Unicode punctuation that all
    render the same visually but collate differently:
      - ASCII apostrophe U+0027  '
      - RIGHT SINGLE QUOTATION MARK U+2019  ’   (iOS autocorrects to this)
      - LEFT SINGLE QUOTATION MARK U+2018  ‘
      - MODIFIER LETTER TURNED COMMA (ʻokina) U+02BB  ʻ  (Hawaiian names)
      - MODIFIER LETTER APOSTROPHE U+02BC  ʼ
    Without normalization, "Jack's Surfboards" in excluded_spots.json
    fails to match "Jack's Surfboards" in spots_enriched.json. Fold them
    all to ASCII apostrophe for comparison. Leaves the stored spot name
    untouched — only the comparison key is normalized.
    """
    if not name:
        return ""
    for variant in ("’", "‘", "ʻ", "ʼ"):
        name = name.replace(variant, "'")
    for variant in ("“", "”"):
        name = name.replace(variant, '"')
    return name


def load_excluded_names(path: Path = EXCLUDED_SPOTS_FILE) -> dict[str, str]:
    """Return {normalized_spot_name: reason} from the exclusion file.

    Reserved keys (``_comment``, ``_schema_version``) are ignored. Every
    other top-level key is treated as a reason whose value is a list of
    spot names. Keys are stored normalized (ASCII apostrophes) so the
    matcher is robust to curly-quote variants in the source data.
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
                out[normalize_name(name)] = reason
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
        # `force` bypasses the plausibility guard for a deliberately large/cross-state correction.
        out[name] = {"lat": lat, "lng": lng, "note": patch.get("note", ""),
                     "force": bool(patch.get("force", False))}
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
        "rejected_fixes": [],
    }

    # Slug index so a patch keyed by slug (the disambiguation-friendly key going forward) matches the
    # same spot db_import upserts on; bare-name keys still match via coord_fixes.get(name) below.
    by_slug = {_slugify(k): v for k, v in coord_fixes.items()}

    # Apply coord fixes first so a name that appears in both lists (shouldn't
    # happen, but be defensive) gets fixed and then removed rather than
    # ghost-removed.
    names_seen: set[str] = set()
    for spot in spots:
        name = spot.get("name")
        if not name:
            continue
        names_seen.add(name)
        patch = coord_fixes.get(name) or by_slug.get(_slugify(name))
        if patch is None:
            continue
        old_lat = spot.get("lat")
        old_lng = spot.get("lng")

        # PLAUSIBILITY GUARD (mode-a fix): the coord-fix map is name-keyed, and generic names collide
        # across states — a San-Diego "North Jetty" patch silently teleported the Florida one ~3500 km,
        # and every coordinate-derived field (orientation, tide, buoy, wfo) was then recomputed from the
        # wrong point. Reject a patch that moves a spot more than COORD_FIX_MAX_MOVE_KM, or into a
        # different state, unless it carries "force": true. Idempotent re-runs pass (current == patch →
        # 0 km); the guard bites only when a patch would actually relocate the spot.
        move_km = (haversine_m(old_lat, old_lng, patch["lat"], patch["lng"]) / 1000.0
                   if old_lat is not None and old_lng is not None else None)
        old_state, new_state = _state_of(old_lat, old_lng), _state_of(patch["lat"], patch["lng"])
        crossed_state = old_state is not None and new_state is not None and old_state != new_state
        too_far = move_km is not None and move_km > COORD_FIX_MAX_MOVE_KM
        if not patch.get("force") and (too_far or crossed_state):
            log.warning(
                "cleanup: REJECTED coord fix for %r — would move it %.0f km%s, from (%.4f,%.4f) to "
                "(%.4f,%.4f); exceeds the %.0f km plausibility guard. Likely a name collision (a patch "
                "authored for a different same-named spot). Add \"force\": true to the patch to override.",
                name, move_km if move_km is not None else -1.0,
                f" and CROSSES STATE {old_state}->{new_state}" if crossed_state else "",
                old_lat, old_lng, patch["lat"], patch["lng"], COORD_FIX_MAX_MOVE_KM,
            )
            stats["rejected_fixes"].append({
                "name": name, "slug": _slugify(name),
                "old_lat": old_lat, "old_lng": old_lng,
                "new_lat": patch["lat"], "new_lng": patch["lng"],
                "move_km": round(move_km, 1) if move_km is not None else None,
                "old_state": old_state, "new_state": new_state,
            })
            continue

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

    # Remove excluded spots. Normalize spot names before matching so
    # curly-apostrophe / ʻokina variants collide with their ASCII
    # equivalents in excluded_spots.json.
    cleaned: list[dict] = []
    excluded_seen: set[str] = set()
    for spot in spots:
        name = spot.get("name")
        normalized = normalize_name(name)
        if name and normalized in excluded:
            reason = excluded[normalized]
            stats["removed"] += 1
            stats["removed_by_reason"][reason] = stats["removed_by_reason"].get(reason, 0) + 1
            excluded_seen.add(normalized)
            # Also purge from verifications — the spot no longer exists.
            if verifications is not None and name in verifications:
                del verifications[name]
            continue
        cleaned.append(spot)

    for normalized_name in excluded:
        if normalized_name not in excluded_seen:
            stats["not_found_excluded"].append(normalized_name)

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
    if stats.get("rejected_fixes"):
        print(f"  coord-fixes REJECTED by plausibility guard: {len(stats['rejected_fixes'])}")
        for r in stats["rejected_fixes"]:
            xs = f" [{r['old_state']}→{r['new_state']}]" if r.get("old_state") != r.get("new_state") else ""
            print(f"    {r['name']} [{r['slug']}]: ({r['old_lat']}, {r['old_lng']}) → "
                  f"({r['new_lat']}, {r['new_lng']})  moved {r['move_km']} km{xs}  — add force:true to override")
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
