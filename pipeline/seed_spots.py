"""Seed surf spots from OSM, Wikidata, Wikipedia, and the gapfill list. CLI entrypoint."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from collections import Counter
from pathlib import Path

from .cleanup_spots import load_excluded_names
from .config import DEFAULT_OUTPUT
from .dedupe import DedupeStats, merge
from .geo import fill_region_hint
from .sources import gapfill, osm, wikidata, wikipedia

log = logging.getLogger("pipeline.seed_spots")

# Insertion order matters: gapfill runs last so dedupe can collapse anything
# it adds that OSM/Wikidata/Wikipedia already found.
SOURCES = {
    "osm": osm.fetch,
    "wikidata": wikidata.fetch,
    "wikipedia": wikipedia.fetch,
    "gapfill": gapfill.fetch,
}


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Seed surf spot candidates from OSM, Wikidata, and Wikipedia.")
    parser.add_argument(
        "--no-cache",
        action="store_true",
        help="Ignore cached API responses and refetch from live sources.",
    )
    parser.add_argument(
        "--skip",
        default="",
        help="Comma-separated list of sources to skip: osm, wikidata, wikipedia, gapfill.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Output JSON path (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable DEBUG logging.",
    )
    return parser.parse_args(argv)


def _run_sources(use_cache: bool, skip: set[str]) -> tuple[list[dict], dict[str, str]]:
    """Fetch each source; isolate failures so one bad source doesn't kill the run."""
    all_candidates: list[dict] = []
    errors: dict[str, str] = {}
    for name, fn in SOURCES.items():
        if name in skip:
            log.info("%s: skipped", name)
            continue
        try:
            results = fn(use_cache=use_cache)
            log.info("%s: %d candidates", name, len(results))
            all_candidates.extend(results)
        except Exception as e:  # noqa: BLE001
            log.exception("%s: fetch failed", name)
            errors[name] = str(e)
    return all_candidates, errors


def _print_summary(records: list[dict], stats: DedupeStats, errors: dict[str, str]) -> None:
    print()
    print("=" * 60)
    print("Stormy Petrel — surf spot seed summary")
    print("=" * 60)
    print(f"Candidates ingested: {stats.candidates_in}")
    for source, count in sorted(stats.per_source.items()):
        print(f"  {source:<10} {count}")
    print(f"Clusters after dedupe: {stats.clusters_out}")
    print(f"  merged by QID:       {stats.merges_by_qid}")
    print(f"  merged by proximity: {stats.merges_by_proximity}")
    if errors:
        print("Source errors:")
        for k, v in errors.items():
            print(f"  {k}: {v}")
    print()
    print("Spots by region:")
    by_region = Counter(r.get("region_hint") or "(unknown)" for r in records)
    width = max((len(k) for k in by_region), default=10)
    for region, count in sorted(by_region.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"  {region.ljust(width)}  {count}")
    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    skip = {s.strip() for s in args.skip.split(",") if s.strip()}
    candidates, errors = _run_sources(use_cache=not args.no_cache, skip=skip)

    if not candidates:
        log.error("No candidates from any source; aborting.")
        if errors:
            for k, v in errors.items():
                log.error("  %s: %s", k, v)
        return 1

    records, stats = merge(candidates)
    fill_region_hint(records)

    # Filter against the curated exclusion list so manually-removed spots
    # (surf shops, duplicates, mis-geocoded junk) don't creep back on
    # re-seed. Match by exact name.
    excluded = load_excluded_names()
    if excluded:
        before = len(records)
        dropped_by_reason: dict[str, int] = {}
        kept: list[dict] = []
        for rec in records:
            reason = excluded.get(rec.get("name") or "")
            if reason is not None:
                dropped_by_reason[reason] = dropped_by_reason.get(reason, 0) + 1
                continue
            kept.append(rec)
        records = kept
        if dropped_by_reason:
            log.info(
                "seed: dropped %d/%d records against exclusion list (%s)",
                before - len(records), before,
                ", ".join(f"{r}={n}" for r, n in sorted(dropped_by_reason.items())),
            )

    records.sort(key=lambda r: ((r.get("region_hint") or "zzz"), r.get("name") or ""))

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(records, indent=2, ensure_ascii=False))
    log.info("Wrote %d spots to %s", len(records), args.output)

    _print_summary(records, stats, errors)

    # Partial success is OK — exit non-zero only if every source failed.
    return 0 if len(errors) < len(SOURCES) - len(skip) else 2


if __name__ == "__main__":
    sys.exit(main())
