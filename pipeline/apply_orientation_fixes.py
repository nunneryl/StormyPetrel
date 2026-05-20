"""Apply manually-verified orientation fixes to Supabase + the
manual_orientations.json source file.

Input shape (matches the corrections file emitted by
audit_orientations_claude, after a human review pass):

    {
      "_comment": "...",
      "_schema_version": 1,
      "orientations": {
        "Spot Name": {
          "orientation_deg": 225,
          "source": "...",
          "notes": "..."
        },
        ...
      }
    }

For each verified spot we:
  1. Look up the spot in the live spots table by slug
     (db_import._slugify of the name — same rule as ingestion).
  2. UPDATE orientation_deg + offshore_wind_deg ((deg+180)%360)
     + optimal_swell_dir (set to the same direction the spot faces
     by default; reviewer's notes are kept verbatim).
  3. Merge the entry into manual_orientations.json so the next
     enrich.py run carries the same value forward without needing
     this script again.

CLI:
    python -m pipeline.apply_orientation_fixes [--input PATH] [--dry-run] [-v]

Env:
    SUPABASE_URL, SUPABASE_SERVICE_KEY — required (unless --dry-run).
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .db_import import _slugify, get_client

log = logging.getLogger("pipeline.apply_orientation_fixes")

DATA_DIR = Path(__file__).parent / "data"
DEFAULT_INPUT = DATA_DIR / "orientation_fixes_verified.json"
MANUAL_ORIENTATIONS_PATH = DATA_DIR / "manual_orientations.json"


def _load_orientations(path: Path) -> dict[str, dict]:
    """Return the {name: {orientation_deg, source?, notes?}} map.
    Tolerates either a top-level `orientations` envelope or a bare
    map at the root."""
    if not path.exists():
        raise FileNotFoundError(path)
    payload = json.loads(path.read_text())
    if isinstance(payload, dict) and "orientations" in payload:
        inner = payload["orientations"]
    else:
        inner = payload
    if not isinstance(inner, dict):
        raise ValueError(f"expected a mapping in {path}; got {type(inner).__name__}")
    return inner


def _validate_entry(name: str, entry: Any) -> dict | None:
    """One row sanity check. Returns the normalized entry or None on bad input."""
    if not isinstance(entry, dict):
        log.error("%s: not an object — skipping", name)
        return None
    deg = entry.get("orientation_deg")
    if not isinstance(deg, (int, float)):
        log.error("%s: missing/invalid orientation_deg — skipping", name)
        return None
    deg = float(deg) % 360
    return {
        "orientation_deg": round(deg, 1),
        "source": (entry.get("source") or "").strip() or None,
        "notes": (entry.get("notes") or "").strip() or None,
    }


def _update_supabase(client, slug: str, orientation_deg: float) -> bool:
    """One UPDATE per verified spot. Returns True on success."""
    payload = {
        "orientation_deg": orientation_deg,
        "offshore_wind_deg": (orientation_deg + 180.0) % 360.0,
        "optimal_swell_dir": orientation_deg,
    }
    try:
        resp = client.table("spots").update(payload).eq("slug", slug).execute()
    except Exception:  # noqa: BLE001
        log.exception("supabase update failed for slug=%s", slug)
        return False
    # supabase-py returns the updated rows in resp.data; an empty list
    # means the slug didn't match anything — flag that to the user
    # because the fix never landed.
    if not resp.data:
        log.warning("slug %s not found in spots table — UPDATE matched 0 rows", slug)
        return False
    return True


def _merge_manual_orientations(updates: dict[str, dict]) -> tuple[int, int]:
    """Merge each verified entry into manual_orientations.json,
    preserving the file's _comment + _schema_version. Returns
    (added, replaced)."""
    if MANUAL_ORIENTATIONS_PATH.exists():
        doc = json.loads(MANUAL_ORIENTATIONS_PATH.read_text())
    else:
        doc = {
            "_schema_version": 1,
            "orientations": {},
        }
    existing = doc.setdefault("orientations", {})
    added = replaced = 0
    for name, entry in updates.items():
        record = {"orientation_deg": int(round(entry["orientation_deg"]))}
        if entry.get("source"):
            record["source"] = entry["source"]
        if entry.get("notes"):
            record["notes"] = entry["notes"]
        if name in existing:
            replaced += 1
        else:
            added += 1
        existing[name] = record
    MANUAL_ORIENTATIONS_PATH.write_text(json.dumps(doc, indent=2) + "\n")
    return added, replaced


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--input", type=Path, default=DEFAULT_INPUT,
                   help="orientation_fixes_verified.json")
    p.add_argument("--dry-run", action="store_true",
                   help="Parse + validate but skip the Supabase update and the manual_orientations.json write.")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        raw = _load_orientations(args.input)
    except FileNotFoundError:
        log.error("input file not found: %s", args.input)
        return 2
    except (ValueError, json.JSONDecodeError) as e:
        log.error("could not parse %s: %s", args.input, e)
        return 2

    # Normalise + drop malformed rows up front so we don't half-apply
    # if one entry is bad.
    verified: dict[str, dict] = {}
    for name, entry in raw.items():
        normalised = _validate_entry(name, entry)
        if normalised is not None:
            verified[name] = normalised

    log.info("loaded %d verified orientation(s) from %s",
             len(verified), args.input)
    if not verified:
        log.warning("nothing to apply")
        return 0

    # Supabase pass first — we want the live row corrected even if
    # the manual_orientations.json write later fails for some reason.
    db_ok = db_failed = 0
    if args.dry_run:
        log.info("--dry-run: skipping supabase updates")
    else:
        client = get_client()
        for name, entry in verified.items():
            slug = _slugify(name)
            if not slug:
                log.warning("%s: empty slug — skipping", name)
                db_failed += 1
                continue
            ok = _update_supabase(client, slug, entry["orientation_deg"])
            if ok:
                db_ok += 1
                log.info("  ✓ %-40s orient=%g° offshore=%g°",
                         name, entry["orientation_deg"],
                         (entry["orientation_deg"] + 180) % 360)
            else:
                db_failed += 1

    if args.dry_run:
        log.info("--dry-run: skipping manual_orientations.json merge")
        added = replaced = 0
    else:
        added, replaced = _merge_manual_orientations(verified)
        log.info("manual_orientations.json: +%d new, %d replaced → %s",
                 added, replaced, MANUAL_ORIENTATIONS_PATH)

    log.info("--- SUMMARY ---")
    log.info("verified entries: %d", len(verified))
    log.info("supabase: ok=%d failed=%d", db_ok, db_failed)
    log.info("manual_orientations.json: added=%d replaced=%d", added, replaced)
    return 0 if db_failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
