"""Phase 0B enrichment orchestrator.

Reads spots_seed.json, runs the five enrichment algorithms on each spot,
and writes spots_enriched.json with the full schema.

CLI:
    python -m pipeline.enrich [--input ...] [--output ...] [--skip-raycast]
"""
from __future__ import annotations

import argparse
import json
import logging
import re
import sys
from pathlib import Path

from .config import (
    DEFAULT_ENRICHED_OUTPUT,
    DEFAULT_OUTPUT,
    MANUAL_ORIENTATIONS_FILE,
    SPOT_ORIENTATIONS_FILE,
)
from .enrichment.adjust import seaward_adjust
from .enrichment.break_type import compute_break_type
from .enrichment.buoys import compute_nearest_buoy
from .enrichment.geodata import load_land_index
from .enrichment.orientation import compute_orientation
from .enrichment.swell_window import compute_swell_window
from .enrichment.swell_window_fallback import compute_swell_window_fallback
from .enrichment.tides import compute_nearest_tide_station

log = logging.getLogger("pipeline.enrich")


def _load_manual_orientations() -> dict[str, dict]:
    """Return {spot_name: {orientation_deg, source?, notes?}} from the
    curated data file. Entries beat the algorithm AND LLM verification —
    they're the escape hatch for spots where both fail (Great Lakes,
    complex harbors, jetties, bay-side geocodes).
    """
    path = MANUAL_ORIENTATIONS_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        log.warning("manual orientations file %s corrupt (%s); ignoring", path, e)
        return {}
    entries = data.get("orientations") or {}
    out: dict[str, dict] = {}
    for name, rec in entries.items():
        if not isinstance(rec, dict):
            continue
        try:
            deg = float(rec["orientation_deg"]) % 360.0
        except (KeyError, TypeError, ValueError):
            log.warning("manual orientation for %r missing or invalid; skipping", name)
            continue
        out[name] = {
            "orientation_deg": deg,
            "source": rec.get("source", ""),
            "notes": rec.get("notes", ""),
        }
    return out


_MANUAL_ORIENTATIONS = _load_manual_orientations()


# Mirror db_import._slugify so override lookups use the same key as the row
# that lands in the spots table. Inlined (rather than imported) so enrich
# stays usable in environments without the supabase dependency.
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug_for(name: str | None) -> str:
    if not name:
        return ""
    s = _SLUG_RE.sub("-", name.lower())
    return s.strip("-")


def _load_spot_orientations() -> dict[str, float]:
    """Return {slug: orientation_deg} from the slug-keyed override file.

    This is the comprehensive human-review override — same role for
    orientation that ``spot_coord_fixes.json`` plays for lat/lng. Applied
    by ``_enrich_one`` AFTER the geometric Algorithm 1 *and* AFTER the
    name-keyed manual_orientations.json fallback, so a slug match here
    beats every other source.
    """
    path = SPOT_ORIENTATIONS_FILE
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        log.warning("spot orientations file %s corrupt (%s); ignoring", path, e)
        return {}
    entries = data.get("orientations") or {}
    out: dict[str, float] = {}
    for slug, rec in entries.items():
        if not isinstance(rec, dict):
            continue
        try:
            out[slug] = float(rec["orientation_deg"]) % 360.0
        except (KeyError, TypeError, ValueError):
            log.warning("spot orientation for slug %r missing or invalid; skipping", slug)
    return out


_SPOT_ORIENTATIONS = _load_spot_orientations()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Enrich seed spots with forecast-grade metadata.")
    p.add_argument(
        "--input", type=Path, default=None,
        help="Input file. Defaults to spots_enriched.json when it exists (so "
             "re-runs respect cleanup and verification), otherwise spots_seed.json.",
    )
    p.add_argument("--output", type=Path, default=DEFAULT_ENRICHED_OUTPUT, help="Output spots_enriched.json")
    p.add_argument(
        "--skip-raycast",
        action="store_true",
        help="Skip Algorithm 2 (swell window ray-casting) — much faster for dev iteration.",
    )
    p.add_argument("--limit", type=int, default=None, help="Only enrich the first N spots (dev).")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


# Fields the LLM verification pass claims authority over. For any spot with a
# high/medium-confidence verification record, the enrichment algorithms must
# NOT overwrite these — the LLM's answer is the source of truth.
_VERIFIED_FIELDS = frozenset({
    "orientation_deg", "offshore_wind_deg", "optimal_swell_dir",
    "break_type", "tide_preference",
})


def _is_verified(spot: dict) -> bool:
    """True when the LLM verification pass has claimed this spot's derived
    metadata (high or medium confidence).
    """
    return spot.get("verification_confidence") in ("high", "medium")


def _enrich_one(spot: dict, skip_raycast: bool, prior_arcs: dict | None = None) -> dict:
    """Run all algorithms on one spot; return the enriched record.

    If *skip_raycast* is True and *prior_arcs* contains a matching entry for
    this spot, carry its raycast arcs forward instead of blanking them — this
    lets dev iterations (`--skip-raycast`) reuse a prior full run's swell
    windows without re-running the 30+ min ray-cast.

    Spots already flagged `is_valid_surf_spot: false` by the verification
    pass (surf shops, duplicates, non-surfable rivers/lakes) are passed
    through unchanged — there's no point running orientation / raycast /
    buoy / tide algorithms on them.
    """
    if spot.get("is_valid_surf_spot") is False:
        log.debug(
            "%s: is_valid_surf_spot=false (%s) — skipping enrichment",
            spot.get("name"), spot.get("invalid_reason"),
        )
        return dict(spot)
    # Seaward-adjust spots that sit inside a GSHHG polygon. The output record
    # keeps the original lat/lng; algorithms receive a copy with the adjusted
    # coordinates so their LOS / perpendicular / curvature checks run from a
    # point that's actually in the water.
    land = load_land_index()
    adj_lat, adj_lng, was_adjusted = (spot["lat"], spot["lng"], False)
    if land is not None:
        adj_lat, adj_lng, was_adjusted = seaward_adjust(spot["lat"], spot["lng"], land)
        if was_adjusted:
            log.info(
                "%s @ (%.4f, %.4f): coord inside land, adjusted seaward to (%.4f, %.4f)",
                spot.get("name") or "(unnamed)", spot["lat"], spot["lng"], adj_lat, adj_lng,
            )
    spot_for_algo = {**spot, "_algo_lat": adj_lat, "_algo_lng": adj_lng}

    # Start from a copy of the input — this carries through any fields the
    # upstream passes already set (verification_confidence, crowd_factor,
    # hazards, tide_preference, tags, sources, ...). Algos overlay their
    # computed fields on top, skipping _VERIFIED_FIELDS for verified spots.
    enriched = dict(spot)
    enriched["coord_adjusted"] = was_adjusted

    verified = _is_verified(spot)
    if verified:
        log.debug(
            "%s: verification_confidence=%s — preserving LLM-set fields %s",
            spot.get("name"), spot.get("verification_confidence"),
            sorted(_VERIFIED_FIELDS),
        )

    def _set(key: str, value) -> None:
        """Write to enriched unless the LLM verification owns that field."""
        if verified and key in _VERIFIED_FIELDS:
            return
        enriched[key] = value

    confidence: dict = dict(enriched.get("enrichment_confidence") or {})

    # Algo 1 — orientation
    try:
        r = compute_orientation(spot_for_algo)
        for k, v in r.items():
            if k == "orientation_confidence":
                continue
            _set(k, v)
        confidence["orientation"] = r.get("orientation_confidence", 0.0)
    except Exception as e:  # noqa: BLE001
        log.warning("%s: orientation failed: %s", spot.get("name"), e)
        for k in ("orientation_deg", "orientation_50m", "orientation_200m", "offshore_wind_deg"):
            _set(k, None)
        confidence["orientation"] = 0.0

    # Algo 1b — manual orientation override. The curated data file is the
    # escape hatch for spots where the geometric algorithm can't find a
    # coastline (Great Lakes), or the nearest-edge perpendicular lies 180°
    # from the actual break (barrier islands whose Nominatim fallback
    # landed on the bay side). Overrides algorithm AND verification —
    # a hand-reviewed value wins over both. Stamped with
    # orientation_source="manual" so downstream scrape/verify merges can
    # recognize and preserve it (see merge_into_spots rules).
    manual = _MANUAL_ORIENTATIONS.get(spot.get("name"))
    if manual is not None:
        deg = manual["orientation_deg"]
        enriched["orientation_deg"] = deg
        enriched["offshore_wind_deg"] = (deg + 180.0) % 360.0
        enriched["orientation_source"] = "manual"
        if manual.get("notes"):
            enriched["orientation_note"] = manual["notes"]
        confidence["orientation"] = 1.0
        log.debug("%s: manual orientation %.0f° applied", spot.get("name"), deg)

    # Algo 1c — slug-keyed orientation override. This is the comprehensive
    # human-review file (spot_orientations.json) — same durable-override
    # role for orientation that spot_coord_fixes.json plays for lat/lng.
    # Runs AFTER the name-keyed Algo 1b so a slug match here is the final
    # word. db_import upserts orientation_deg into the spots table, and
    # interpret.directional_gain reads it as the "target" for inside-window
    # cos² gain (and offshore_wind_deg drives wind_multiplier), so writing
    # it here is what makes the human value reach the live rating.
    slug = _slug_for(spot.get("name"))
    override_deg = _SPOT_ORIENTATIONS.get(slug)
    if override_deg is not None:
        enriched["orientation_deg"] = override_deg
        enriched["offshore_wind_deg"] = (override_deg + 180.0) % 360.0
        enriched["orientation_source"] = "manual"
        confidence["orientation"] = 1.0
        log.debug(
            "%s (%s): spot_orientations override %.0f° applied",
            spot.get("name"), slug, override_deg,
        )

    # Algo 2 — swell window (optional). Skip the expensive ray-cast entirely
    # for verified spots whose optimal_swell_dir is set by the LLM — we still
    # need arcs, though, so the fallback runs after.
    if skip_raycast:
        prior = (prior_arcs or {}).get(spot.get("name")) if prior_arcs else None
        if prior and prior.get("swell_window_arcs"):
            enriched["swell_window_arcs"] = prior["swell_window_arcs"]
            _set("optimal_swell_dir", prior.get("optimal_swell_dir"))
            if prior.get("swell_window_source"):
                enriched["swell_window_source"] = prior["swell_window_source"]
            confidence["swell_window"] = prior.get("swell_window_confidence", 0.0)
        else:
            # Blank arcs so the orientation-derived fallback below actually
            # rebuilds them against the current orientation. Preserving the
            # stale arcs from a prior run would short-circuit the fallback
            # (it no-ops when arcs already exist) and leave optimal_swell_dir
            # permanently None for unverified spots — the exact regression
            # we saw post-unmerge.
            enriched["swell_window_arcs"] = []
            if not verified:
                enriched["optimal_swell_dir"] = None
            confidence["swell_window"] = 0.0
    else:
        try:
            r = compute_swell_window(spot_for_algo)
            enriched["swell_window_arcs"] = r["swell_window_arcs"]
            _set("optimal_swell_dir", r["optimal_swell_dir"])
            confidence["swell_window"] = r["swell_window_confidence"]
        except Exception as e:  # noqa: BLE001
            log.warning("%s: swell window failed: %s", spot.get("name"), e)
            enriched["swell_window_arcs"] = []
            _set("optimal_swell_dir", None)
            confidence["swell_window"] = 0.0

    # Algo 2b — orientation-derived fallback for spots whose arcs are still
    # empty. Already-populated arcs (raycast or carried-forward) are a no-op.
    fallback = compute_swell_window_fallback(enriched)
    if fallback:
        # The fallback produces arcs + optimal_swell_dir + swell_window_source.
        # For verified spots, keep the LLM's optimal_swell_dir; adopt the arcs.
        if "swell_window_arcs" in fallback:
            enriched["swell_window_arcs"] = fallback["swell_window_arcs"]
        if "swell_window_source" in fallback:
            enriched["swell_window_source"] = fallback["swell_window_source"]
        if "optimal_swell_dir" in fallback:
            _set("optimal_swell_dir", fallback["optimal_swell_dir"])

    # Algo 3 — break type
    try:
        r = compute_break_type(spot_for_algo)
        _set("break_type", r["break_type"])
        enriched["break_type_confidence"] = r["break_type_confidence"]
        confidence["break_type"] = r["break_type_confidence"]
    except Exception as e:  # noqa: BLE001
        log.warning("%s: break_type failed: %s", spot.get("name"), e)
        _set("break_type", "beach")
        enriched["break_type_confidence"] = 0.5
        confidence["break_type"] = 0.5

    # Algo 4 — nearest buoy
    try:
        r = compute_nearest_buoy(spot_for_algo)
        enriched["nearest_buoy_id"] = r["nearest_buoy_id"]
        enriched["nearest_buoy_dist_km"] = r["nearest_buoy_dist_km"]
        enriched["fallback_buoy_ids"] = r["fallback_buoy_ids"]
        confidence["nearest_buoy"] = r["buoy_confidence"]
    except Exception as e:  # noqa: BLE001
        log.warning("%s: buoy failed: %s", spot.get("name"), e)
        enriched.update(nearest_buoy_id=None, nearest_buoy_dist_km=None, fallback_buoy_ids=[])
        confidence["nearest_buoy"] = 0.0

    # Algo 5 — tide station
    try:
        r = compute_nearest_tide_station(spot)
        enriched["nearest_tide_station_id"] = r["nearest_tide_station_id"]
        enriched["nearest_tide_station_dist_km"] = r["nearest_tide_station_dist_km"]
        confidence["nearest_tide_station"] = 1.0 if r["nearest_tide_station_id"] else 0.0
    except Exception as e:  # noqa: BLE001
        log.warning("%s: tide failed: %s", spot.get("name"), e)
        enriched.update(nearest_tide_station_id=None, nearest_tide_station_dist_km=None)
        confidence["nearest_tide_station"] = 0.0

    enriched["sources"] = spot.get("sources", {})
    enriched["tags"] = spot.get("tags", {})
    enriched["enrichment_confidence"] = confidence
    return enriched


def _summarize(records: list[dict]) -> None:
    n = len(records)
    invalid = sum(1 for r in records if r.get("is_valid_surf_spot") is False)
    valid_n = n - invalid
    print()
    print("=" * 60)
    print(f"Enrichment summary ({n} spots; {valid_n} valid, {invalid} flagged invalid)")
    print("=" * 60)
    def pct(f):
        # Percentages are computed over valid spots only — counting invalid
        # spots as "unresolved" would distort the numbers.
        valid = [r for r in records if r.get("is_valid_surf_spot") is not False]
        return f"{100 * sum(1 for r in valid if f(r)) / max(len(valid), 1):.0f}%"
    print(f"  orientation resolved:       {pct(lambda r: r.get('orientation_deg') is not None)}")
    manual_n = sum(1 for r in records
                   if r.get("is_valid_surf_spot") is not False
                   and r.get("orientation_source") == "manual")
    if manual_n:
        print(f"    manual overrides:         {manual_n}")
    print(f"  swell window resolved:      {pct(lambda r: r.get('optimal_swell_dir') is not None)}")
    print(f"    raycast-resolved:         {pct(lambda r: r.get('optimal_swell_dir') is not None and r.get('swell_window_source') != 'orientation_derived')}")
    print(f"    orientation-derived:      {pct(lambda r: r.get('swell_window_source') == 'orientation_derived')}")
    print(f"  break type = point:         {pct(lambda r: r.get('break_type') == 'point')}")
    print(f"  nearest buoy assigned:      {pct(lambda r: r.get('nearest_buoy_id'))}")
    print(f"  nearest tide station ≤50km: {pct(lambda r: r.get('nearest_tide_station_id'))}")
    print("=" * 60)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    # Default: re-enrich the existing spots_enriched.json when it exists so
    # that cleanup_spots and verify_spots results carry through. Otherwise
    # bootstrap from spots_seed.json.
    if args.input is None:
        if DEFAULT_ENRICHED_OUTPUT.exists():
            args.input = DEFAULT_ENRICHED_OUTPUT
            log.info("enrich: resuming from existing enriched file %s", args.input)
        elif DEFAULT_OUTPUT.exists():
            args.input = DEFAULT_OUTPUT
            log.info("enrich: bootstrapping from seed file %s", args.input)
        else:
            log.error("Neither %s nor %s exists. Run `python -m pipeline.seed_spots` first.",
                      DEFAULT_ENRICHED_OUTPUT, DEFAULT_OUTPUT)
            return 1

    if not args.input.exists():
        log.error("Input file %s does not exist.", args.input)
        return 1

    spots = json.loads(args.input.read_text())
    if args.limit:
        spots = spots[: args.limit]
    verified_n = sum(1 for s in spots if _is_verified(s))
    log.info("Enriching %d spots from %s (%d LLM-verified, will preserve %s)",
             len(spots), args.input, verified_n, sorted(_VERIFIED_FIELDS))

    # When --skip-raycast is set, carry forward arcs from a prior full run
    # so dev iteration doesn't wipe 30+ min of ray-cast work. We explicitly
    # *skip* orientation-derived arcs — those are cheap to recompute from
    # the fallback, and carrying them forward would persist stale arcs
    # whenever orientation changes (e.g. the hemisphere flip correcting a
    # barrier-island spot that geocoded to the bay side).
    prior_arcs: dict[str, dict] = {}
    if args.skip_raycast and args.output.exists():
        try:
            prior = json.loads(args.output.read_text())
            for rec in prior:
                name = rec.get("name")
                if not name:
                    continue
                if rec.get("swell_window_source") == "orientation_derived":
                    continue  # recompute from (possibly-corrected) orientation
                prior_arcs[name] = {
                    "swell_window_arcs": rec.get("swell_window_arcs") or [],
                    "optimal_swell_dir": rec.get("optimal_swell_dir"),
                    "swell_window_source": rec.get("swell_window_source"),
                    "swell_window_confidence": (
                        (rec.get("enrichment_confidence") or {}).get("swell_window", 0.0)
                    ),
                }
            carried = sum(1 for v in prior_arcs.values() if v["swell_window_arcs"])
            log.info(
                "enrich: --skip-raycast; carrying %d ray-cast arc sets from %s",
                carried, args.output,
            )
        except (json.JSONDecodeError, OSError) as e:
            log.warning("enrich: could not load prior arcs from %s: %s", args.output, e)

    try:
        from tqdm import tqdm
        iterator = tqdm(spots, desc="enrich", unit="spot")
    except ImportError:
        iterator = spots

    enriched = [_enrich_one(spot, args.skip_raycast, prior_arcs) for spot in iterator]

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(enriched, indent=2, ensure_ascii=False))
    log.info("Wrote %d enriched spots to %s", len(enriched), args.output)
    _summarize(enriched)
    return 0


if __name__ == "__main__":
    sys.exit(main())
