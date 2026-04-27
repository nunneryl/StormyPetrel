"""Persistent review queue for spots whose orientation/break/tide values
deserve a manual eyeball at some point.

The pipeline computes orientation through several layers (manual override
→ surf-forecast.com scrape → LLM verification → geometric algorithm), and
each has different confidence. This module ranks spots by how badly they
need human review and writes them to data/review_queue.json so the queue
can be worked through over time — perhaps after launch, when a slow week
gives time for spot-checking.

Priority assignment (skipping spots already reviewed):

  HIGH    — orientation 50m/100m/200m windows disagree by >30°, OR the
            spot is algorithm-only AND has window disagreement, OR it
            has only low-confidence LLM verification.
  MEDIUM  — algorithm-only spots with consistent windows but no scrape /
            high-confidence verification to corroborate, OR scraped
            spots with significant window disagreement.
  LOW     — well-validated spots with minor window disagreement (15-30°),
            or LLM-medium with no other signal.

Spots in any of these states are NOT included:
  - orientation_source == "manual"  (gold standard, can't be wrong)
  - is_valid_surf_spot == False     (already filtered out of the database)

Re-running this command preserves `reviewed: true` and any reviewer_notes
on existing entries; new spots get added with `reviewed: false`.

CLI:
    python -m pipeline.audit_review_queue              # regenerate the queue
    python -m pipeline.audit_review_queue --show       # list pending items
    python -m pipeline.audit_review_queue --markdown   # print as markdown checklist
"""
from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

from .config import DEFAULT_ENRICHED_OUTPUT, REVIEW_QUEUE_FILE

log = logging.getLogger("pipeline.audit_review_queue")


def _angular_distance(a: float, b: float) -> float:
    """Smallest absolute angular difference, 0-180."""
    d = abs(a - b) % 360.0
    return min(d, 360.0 - d)


def _max_window_disagreement(spot: dict) -> float | None:
    """Pairwise max disagreement across the 50m / 100m / 200m orientation
    windows. Returns None if any window is missing — can't compare three
    when one is null.
    """
    windows = [spot.get("orientation_50m"), spot.get("orientation_deg"),
               spot.get("orientation_200m")]
    if any(w is None for w in windows):
        return None
    a, b, c = windows
    return max(_angular_distance(a, b), _angular_distance(b, c), _angular_distance(a, c))


def classify_spot(spot: dict) -> tuple[str, list[str], float | None] | None:
    """Return (priority, concerns, max_disagree_deg) or None to skip.

    Spots returning None are either gold-standard (manual override) or
    sufficiently validated that no review is needed.
    """
    if spot.get("is_valid_surf_spot") is False:
        return None
    if spot.get("orientation_source") == "manual":
        return None

    has_scrape = bool(spot.get("surf_forecast_url"))
    verif = spot.get("verification_confidence")
    max_dis = _max_window_disagreement(spot)

    concerns: list[str] = []
    if max_dis is not None and max_dis > 30:
        concerns.append(f"window_disagreement_{int(round(max_dis))}deg")
    elif max_dis is not None and max_dis > 15:
        concerns.append(f"window_disagreement_{int(round(max_dis))}deg")

    # Spot is "validated" if it has a coord-checked scrape OR high-confidence LLM.
    # Validated spots only get into the queue when their windows disagree badly.
    validated = has_scrape or verif == "high"

    if validated:
        if max_dis is not None and max_dis > 30:
            return ("medium", concerns, max_dis)
        if max_dis is not None and max_dis > 15:
            return ("low", concerns, max_dis)
        return None

    # Unvalidated → must review at some point.
    if verif == "low":
        concerns.append("llm_low_confidence")
        priority = "high"
    elif verif == "medium":
        concerns.append("llm_medium_confidence")
        priority = "medium"
    else:
        concerns.append("algorithm_only")
        priority = "medium"

    if max_dis is not None and max_dis > 30:
        priority = "high"

    return (priority, concerns, max_dis)


def _load_existing_queue(path: Path) -> dict[str, dict]:
    """Return {name: full_record} from the existing queue file, so
    `reviewed: true` and reviewer_notes survive regeneration.
    """
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        log.warning("review queue %s corrupt (%s); regenerating from scratch", path, e)
        return {}
    return {item["name"]: item for item in (data.get("items") or []) if item.get("name")}


def build_queue(spots: list[dict], existing: dict[str, dict]) -> dict:
    """Walk spots, classify each, merge with existing review state."""
    items: list[dict] = []
    for spot in spots:
        name = spot.get("name")
        if not name:
            continue
        result = classify_spot(spot)
        if result is None:
            continue
        priority, concerns, max_dis = result
        prior = existing.get(name) or {}
        items.append({
            "name": name,
            "priority": priority,
            "concerns": concerns,
            "lat": spot.get("lat"),
            "lng": spot.get("lng"),
            "region_hint": spot.get("region_hint"),
            "orientation_deg": spot.get("orientation_deg"),
            "orientation_50m": spot.get("orientation_50m"),
            "orientation_200m": spot.get("orientation_200m"),
            "offshore_wind_deg": spot.get("offshore_wind_deg"),
            "break_type": spot.get("break_type"),
            "max_window_disagreement_deg": (
                round(max_dis, 1) if max_dis is not None else None
            ),
            "verification_confidence": spot.get("verification_confidence"),
            "surf_forecast_url": spot.get("surf_forecast_url"),
            # Preserve review state across regenerations.
            "reviewed": prior.get("reviewed", False),
            "reviewer_notes": prior.get("reviewer_notes", ""),
        })

    # Sort: pending high > pending medium > pending low > reviewed at end.
    _ORDER = {"high": 0, "medium": 1, "low": 2}
    items.sort(key=lambda i: (i["reviewed"], _ORDER[i["priority"]], i["name"]))

    return {
        "_comment": (
            "Pending spots ranked for manual orientation review. Mark items "
            "as reviewed by setting reviewed: true and (optionally) writing "
            "in reviewer_notes. Re-run audit_review_queue to refresh and "
            "preserve your annotations."
        ),
        "_generated_at": datetime.now(tz=timezone.utc).isoformat(),
        "summary": _summary(items),
        "items": items,
    }


def _summary(items: list[dict]) -> dict:
    out = {"total": len(items), "reviewed": 0, "pending": {"high": 0, "medium": 0, "low": 0}}
    for it in items:
        if it["reviewed"]:
            out["reviewed"] += 1
        else:
            out["pending"][it["priority"]] += 1
    return out


def _print_summary(queue: dict) -> None:
    s = queue["summary"]
    print()
    print("=" * 60)
    print("Spot review queue")
    print("=" * 60)
    print(f"  total tracked:    {s['total']}")
    print(f"  reviewed:         {s['reviewed']}")
    print(f"  pending high:     {s['pending']['high']}")
    print(f"  pending medium:   {s['pending']['medium']}")
    print(f"  pending low:      {s['pending']['low']}")
    print("=" * 60)


def _print_pending(queue: dict, limit: int = 999) -> None:
    print()
    print("=" * 60)
    print("Pending review (highest priority first)")
    print("=" * 60)
    shown = 0
    for it in queue["items"]:
        if it["reviewed"]:
            continue
        if shown >= limit:
            break
        shown += 1
        coord = f"({it['lat']:.4f}, {it['lng']:.4f})" if it["lat"] is not None else "(?, ?)"
        orient = it.get("orientation_deg")
        orient_str = f"{int(orient)}°" if orient is not None else "?"
        max_dis = it.get("max_window_disagreement_deg")
        dis_str = f"  Δwindow={max_dis}°" if max_dis else ""
        print(f"  [{it['priority']:>6}] {it['name']:<42} {coord:<24}"
              f"orient={orient_str}{dis_str}")
        print(f"            region={it.get('region_hint')!r}  "
              f"concerns={it['concerns']}")
    print("=" * 60)


def _print_markdown(queue: dict) -> None:
    """Render the queue as a markdown checklist for tracking outside the JSON file."""
    print(f"# Spot review queue\n")
    print(f"_Generated {queue['_generated_at']}_\n")
    s = queue["summary"]
    print(f"**Pending: {s['pending']['high']} high, {s['pending']['medium']} medium, "
          f"{s['pending']['low']} low. Reviewed: {s['reviewed']}.**\n")
    for prio in ("high", "medium", "low"):
        items = [i for i in queue["items"] if not i["reviewed"] and i["priority"] == prio]
        if not items:
            continue
        print(f"\n## {prio.title()} priority ({len(items)})\n")
        for it in items:
            box = "[x]" if it["reviewed"] else "[ ]"
            coord = f"({it['lat']:.4f}, {it['lng']:.4f})" if it["lat"] is not None else "(?, ?)"
            concerns = ", ".join(it["concerns"])
            print(f"- {box} **{it['name']}** ({it.get('region_hint') or '?'}) {coord} — {concerns}")


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    p.add_argument("--input", type=Path, default=DEFAULT_ENRICHED_OUTPUT,
                   help="spots_enriched.json to read")
    p.add_argument("--queue-file", type=Path, default=REVIEW_QUEUE_FILE,
                   help="Where to write the persistent review queue")
    p.add_argument("--show", action="store_true",
                   help="Print pending items after regenerating")
    p.add_argument("--markdown", action="store_true",
                   help="Print queue as markdown checklist (also still writes JSON)")
    p.add_argument("--no-write", action="store_true",
                   help="Don't update the queue file (use with --show / --markdown)")
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
    existing = _load_existing_queue(args.queue_file)
    queue = build_queue(spots, existing)

    if not args.no_write:
        args.queue_file.parent.mkdir(parents=True, exist_ok=True)
        args.queue_file.write_text(
            json.dumps(queue, indent=2, ensure_ascii=False, sort_keys=False)
        )
        log.info("Wrote %d items to %s", len(queue["items"]), args.queue_file)

    _print_summary(queue)
    if args.show:
        _print_pending(queue)
    if args.markdown:
        print()
        _print_markdown(queue)
    return 0


if __name__ == "__main__":
    sys.exit(main())
