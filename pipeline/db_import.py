"""Push enriched spots + forecasts / buoys / tides to Supabase.

Reads the local pipeline output (spots_enriched.json, ratings.json,
buoys.json, tides.json) and upserts into the corresponding Supabase
tables defined in migrations/001_initial_schema.sql.

Authentication: reads SUPABASE_URL and SUPABASE_SERVICE_KEY from the
environment. Never log or echo the key. The service-role key bypasses
RLS so this script does NOT require row-level-security policies on the
target tables.

CLI:
    python -m pipeline.db_import                # everything
    python -m pipeline.db_import --spots-only
    python -m pipeline.db_import --forecasts-only
    python -m pipeline.db_import --all           # explicit (default)
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

from .cleanup_spots import load_excluded_names
from .config import (
    BUOYS_FORECAST_FILE,
    DEFAULT_ENRICHED_OUTPUT,
    RATINGS_FILE,
    TIDES_FORECAST_FILE,
)

log = logging.getLogger("pipeline.db_import")

_SLUG_RE = re.compile(r"[^a-z0-9]+")
_DEFAULT_BATCH = 200

# Safety cap on per-run spot deletions driven by excluded_spots.json. A run
# that would delete more than this many rows almost certainly means the
# exclusion file is truncated, swapped, or corrupted — so we refuse rather
# than wipe the roster. Routine deletes are 1-2 at a time; bump only with a
# deliberate reason.
SAFETY_DELETE_CAP = 10


def _slugify(name: str) -> str:
    """Lowercase, hyphen-join, drop everything that isn't [a-z0-9-]."""
    if not name:
        return ""
    s = _SLUG_RE.sub("-", name.lower())
    return s.strip("-")


def get_client():
    """Return a configured supabase client. Raises if env vars are missing.

    Lazy-imports supabase so the module stays importable in environments
    without the dependency installed.
    """
    from supabase import create_client

    url = os.environ.get("SUPABASE_URL")
    key = os.environ.get("SUPABASE_SERVICE_KEY")
    if not url or not key:
        raise RuntimeError(
            "SUPABASE_URL and SUPABASE_SERVICE_KEY must be set in the environment"
        )
    return create_client(url, key)


# ---------------------------------------------------------------------------
# Spots
# ---------------------------------------------------------------------------

def _spot_record(spot: dict) -> dict:
    """Map a spots_enriched.json entry to a spots-table row.

    Returns a *partial* record — only includes keys whose source-of-truth
    is present in *spot*. The caller (`import_spots`) merges these
    partials with the current DB row so an absent key leaves the
    existing DB value untouched.

    Two distinct concerns are blended in this function:

    1. Source → DB column mapping. The block below lists every
       enriched-JSON key whose name matches a spots-table column.
       Adding a new source key (e.g. a future ``swell_window_confidence``)
       requires adding it here so it actually gets written from source.
    2. Preserve safety net. Handled in import_spots via SELECT * and a
       per-record merge — completely schema-wide, no list to maintain.
       So a column we DON'T add to this list is still safe from
       silent NULL: it'll just keep its existing DB value.

    Always-written keys (the upsert key, geometric anchors, and fields
    derived fresh every pipeline run): slug, name, lat, lng, state,
    region, swell_window_arcs, data_sources, review_status.
    """
    rec = {
        "slug": _slugify(spot.get("name") or ""),
        "name": spot.get("name"),
        "lat": spot.get("lat"),
        "lng": spot.get("lng"),
        # state / region collapse to region_hint for now; aka_names left null
        # until we have a multi-name source.
        "state": spot.get("region_hint"),
        "region": spot.get("region_hint"),
        "swell_window_arcs": spot.get("swell_window_arcs") or [],
        # Provenance — what source produced each authoritative field. Lets
        # the frontend show "scrape from surf-forecast.com" or "manual".
        # Rebuilt fresh each pipeline run (always written).
        "data_sources": {
            "orientation_source": spot.get("orientation_source"),
            "verification_confidence": spot.get("verification_confidence"),
            "surf_forecast_url": spot.get("surf_forecast_url"),
            "swell_window_source": spot.get("swell_window_source"),
            "coord_fix_applied": spot.get("coord_fix_applied", False),
            "sources": spot.get("sources") or {},
        },
        "review_status": "auto",
    }
    # Source-to-DB column mapping: any enriched-JSON key in this list whose
    # name matches a spots-table column gets written through. Keys absent
    # from *spot* are not written; the preserve safety net in import_spots
    # fills them from the existing DB row at merge time. Concern (1)
    # from the docstring; concern (2) is import_spots' responsibility.
    for k in (
        "orientation_deg", "offshore_wind_deg", "optimal_swell_dir",
        "break_type", "break_type_confidence",
        "tide_preference", "crowd_factor", "hazards",
        "nearest_buoy_id", "nearest_buoy_dist_km",
        "nearest_tide_station_id", "nearest_tide_station_dist_km",
        "nwps_wfo",
    ):
        if k in spot:
            rec[k] = spot[k]
    # Swell-source provenance as a top-level flag for the frontend's CDIP
    # attribution. Only the non-default source is persisted; orientation-derived
    # (the default) stays NULL per migration 010. Full verbatim provenance for
    # every spot still lives in data_sources.swell_window_source above.
    _sws = spot.get("swell_window_source")
    rec["swell_window_source"] = _sws if _sws and _sws != "orientation_derived" else None
    return rec


# Columns that are DB-managed and must never be sent back through the
# upsert: the auto-incrementing PK, the trigger-derived PostGIS geometry,
# and the timestamp columns. Anything else in the spots table is treated
# as preserve-by-default: an absent key in the source dict gets filled
# from the current DB row at merge time. That's the schema-wide
# generic rule — adding a new column to the schema can't reopen the
# silent-NULL bug class because the preserve happens by SELECT *,
# not by name list.
_DB_MANAGED_COLUMNS = frozenset({"id", "geom", "created_at", "updated_at"})


def _fetch_existing_spots(client) -> dict[str, dict]:
    """Return ``{slug: {col: value}}`` for every existing spot, stripped of
    DB-managed columns.

    Pulled once per import_spots call. The per-row merge then fills any
    column absent from the partial source-derived record with the existing
    DB value, so a column the source doesn't carry is never NULLed by the
    upsert. Pages through Supabase's default 1000-row cap defensively
    (the roster is ~668 today but a future expansion shouldn't silently
    truncate).

    Uses ``select("*")`` deliberately: any column added to the spots
    schema later is automatically preserved here without touching this
    function or maintaining a separate at-risk list. The 13-column
    hardcoded list this replaces could go stale with every schema
    migration; ``*`` can't.
    """
    out: dict[str, dict] = {}
    page = 1000
    offset = 0
    while True:
        rows = (
            client.table("spots")
            .select("*")
            .range(offset, offset + page - 1)
            .execute()
        )
        data = rows.data or []
        for r in data:
            slug = r.pop("slug", None)
            if not slug:
                continue
            for k in _DB_MANAGED_COLUMNS:
                r.pop(k, None)
            out[slug] = r
        if len(data) < page:
            break
        offset += page
    return out


def _dedupe_by_slug(records: list[dict]) -> tuple[list[dict], list[tuple[str, str]]]:
    """Drop slug-duplicates from *records* (keeping the first occurrence) and
    return ``(unique, collisions)``. Two spot names can produce the same slug
    when they differ only in characters _slugify strips (Unicode apostrophes,
    diacritics, punctuation). Without this dedup, supabase-py upserts a batch
    with two rows that share the unique-key value and Postgres rejects the
    whole batch with ``21000 ON CONFLICT DO UPDATE command cannot affect row
    a second time``.
    """
    seen: dict[str, dict] = {}
    collisions: list[tuple[str, str]] = []
    for r in records:
        slug = r.get("slug")
        if not slug:
            continue
        if slug in seen:
            collisions.append((slug, r.get("name") or "(unnamed)"))
        else:
            seen[slug] = r
    return list(seen.values()), collisions


def _dedupe_by_keys(records: list[dict], keys: tuple[str, ...]) -> tuple[list[dict], int]:
    """Drop records whose composite key tuple repeats; keep the first.
    Returns ``(unique, dropped_count)``. Same Postgres "21000 ON CONFLICT
    can't affect a row twice" failure mode as _dedupe_by_slug, just for
    multi-column unique constraints (forecasts, buoys, tides).
    """
    seen: set[tuple] = set()
    out: list[dict] = []
    dropped = 0
    for r in records:
        k = tuple(r.get(k_) for k_ in keys)
        if k in seen:
            dropped += 1
            continue
        seen.add(k)
        out.append(r)
    return out, dropped


def _excluded_slugs() -> set[str]:
    """Slugs derived from `excluded_spots.json` names.

    The same `_slugify` rule that maps a record's name to its DB slug also
    maps an excluded entry to the slug we want to skip / delete. Curly
    quotes etc. are already folded by `load_excluded_names` via
    `normalize_name`. Empty results (file missing, empty file) are fine
    — the caller short-circuits to a no-op.
    """
    excluded = load_excluded_names()
    return {_slugify(name) for name in excluded if name}


def _find_excluded_in_db(client, excluded_slugs: set[str]) -> list[dict]:
    """Return rows in `spots` whose slug appears in the exclusion list.

    Used pre-flight by `import_spots` to (a) enforce the safety cap before
    we touch anything and (b) drive the deletion + log lines after the
    upsert.
    """
    if not excluded_slugs:
        return []
    result = (
        client.table("spots")
        .select("slug,name")
        .in_("slug", sorted(excluded_slugs))
        .execute()
    )
    return result.data or []


def import_spots(client, spots_path: Path = DEFAULT_ENRICHED_OUTPUT,
                 batch_size: int = _DEFAULT_BATCH) -> int:
    """Upsert valid spots from the enriched JSON, then delete any DB rows
    whose slug appears in `excluded_spots.json`.

    Excluded slugs are filtered out of the upsert pass too, so a stale
    `spots_enriched.json` that still contains an excluded entry can't
    resurrect a row that the deletion pass is about to remove. Skips
    invalid + unnamed records as before. Aborts before any write if the
    pre-flight delete count exceeds `SAFETY_DELETE_CAP`.
    """
    spots = json.loads(Path(spots_path).read_text())
    valid = [
        s for s in spots
        if s.get("name")
        and s.get("lat") is not None
        and s.get("lng") is not None
        and s.get("is_valid_surf_spot") is not False
    ]
    records = [_spot_record(s) for s in valid]
    records, collisions = _dedupe_by_slug(records)
    if collisions:
        log.warning(
            "spots: %d slug collision(s) — keeping the first occurrence in each:",
            len(collisions),
        )
        for slug, name in collisions:
            log.warning("  slug=%r duplicated by %r — dropped", slug, name)

    excluded_slugs = _excluded_slugs()

    # Pre-flight: check the deletion count BEFORE we upsert anything so a
    # corrupted exclusion file aborts the whole spot pass instead of
    # leaving a half-applied state.
    to_delete = _find_excluded_in_db(client, excluded_slugs)
    if len(to_delete) > SAFETY_DELETE_CAP:
        raise RuntimeError(
            f"spots: refusing to delete {len(to_delete)} rows in one run "
            f"(cap is {SAFETY_DELETE_CAP}). This usually means "
            f"excluded_spots.json is truncated, swapped, or corrupted. "
            f"Inspect the file and retry; raise SAFETY_DELETE_CAP only with a "
            f"deliberate reason. Excluded slugs targeted: "
            f"{[r['slug'] for r in to_delete]}"
        )

    # Drop excluded rows from the upsert so a stale `spots_enriched.json`
    # doesn't refill what we're about to delete.
    pre_excl = len(records)
    records = [r for r in records if r["slug"] not in excluded_slugs]
    skipped_excluded = pre_excl - len(records)

    # SELECT-then-merge: fill in any at-risk column absent from the partial
    # record with the current DB value. PostgREST bulk-upsert NULLs any key
    # missing from a row, so without this merge any column not carried in
    # every spots_enriched.json entry would silently NULL across the roster
    # — the exact bug that mass-NULLed nearest_tide_station_id (see
    # docs/tide_mapping_rebuild_report.md).
    existing = _fetch_existing_spots(client)
    filled_any = 0
    for rec in records:
        base = existing.get(rec["slug"])
        if not base:
            continue
        for k, v in base.items():
            if k not in rec:
                rec[k] = v
                filled_any += 1

    log.info(
        "spots: upserting %d records (skipped %d invalid/unnamed, %d slug collisions, %d excluded, %d cols filled from DB)",
        len(records), len(spots) - len(valid), len(collisions), skipped_excluded, filled_any,
    )

    written = 0
    for i in range(0, len(records), batch_size):
        chunk = records[i:i + batch_size]
        client.table("spots").upsert(chunk, on_conflict="slug").execute()
        written += len(chunk)

    # Deletion pass — DB rows matching an excluded entry. The cams FK is
    # `ON DELETE SET NULL` (per docs/spot_delete_workflow.sql) so any cam
    # that still references the slug just gets unassigned, and forecasts
    # CASCADE off automatically.
    if to_delete:
        for row in to_delete:
            log.warning("removing spot: %s (%s)", row["slug"], row.get("name") or "(unnamed)")
        client.table("spots").delete().in_(
            "slug", [row["slug"] for row in to_delete]
        ).execute()

    return written


def _spot_id_map(client) -> dict[str, int]:
    """Fetch name → id map for downstream foreign-key resolution.

    Paginated: Supabase caps select() at 1000 rows by default; for ~485
    spots one page is enough but we loop just in case.
    """
    by_name: dict[str, int] = {}
    page_size = 1000
    offset = 0
    while True:
        result = (
            client.table("spots")
            .select("id,name")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        rows = result.data or []
        if not rows:
            break
        for r in rows:
            if r.get("name"):
                by_name[r["name"]] = r["id"]
        if len(rows) < page_size:
            break
        offset += page_size
    return by_name


# ---------------------------------------------------------------------------
# Forecasts (NWPS-derived hourly ratings)
# ---------------------------------------------------------------------------

def import_forecasts(client, ratings_path: Path = RATINGS_FILE,
                     batch_size: int = _DEFAULT_BATCH * 5) -> int:
    """Upsert per-spot hourly forecasts from ratings.json."""
    ratings = json.loads(Path(ratings_path).read_text())
    by_name = _spot_id_map(client)

    records: list[dict] = []
    skipped_unknown = 0
    for spot_name, hours in ratings.items():
        spot_id = by_name.get(spot_name)
        if spot_id is None:
            skipped_unknown += 1
            continue
        for h in hours:
            vt = h.get("valid_time")
            if not vt:
                continue
            records.append({
                "spot_id": spot_id,
                "valid_time": vt,
                "hs": h.get("hs"),
                "tp": h.get("tp"),
                "dp": h.get("dp"),
                "wind_speed": h.get("wind_speed"),
                "wind_dir": h.get("wind_dir"),
                "swell_hs": h.get("swell_hs"),
                "swell_tp": h.get("swell_tp"),
                "swell_dp": h.get("swell_dp"),
                "swell_1_hs": h.get("swell_1_hs"),
                "swell_1_tp": h.get("swell_1_tp"),
                "swell_1_dp": h.get("swell_1_dp"),
                "swell_2_hs": h.get("swell_2_hs"),
                "swell_2_tp": h.get("swell_2_tp"),
                "swell_2_dp": h.get("swell_2_dp"),
                "swell_3_hs": h.get("swell_3_hs"),
                "swell_3_tp": h.get("swell_3_tp"),
                "swell_3_dp": h.get("swell_3_dp"),
                "wind_wave_hs": h.get("wind_wave_hs"),
                "wind_wave_tp": h.get("wind_wave_tp"),
                "wind_wave_dp": h.get("wind_wave_dp"),
                "swell_source": h.get("swell_source"),
                "tide_level_ft": h.get("tide_level_ft"),
                "tide_norm": h.get("tide_norm"),
                "face_ft": h.get("face_ft"),
                "dir_gain": h.get("dir_gain"),
                "wind_mult": h.get("wind_mult"),
                "tide_mult": h.get("tide_mult"),
                "chop_ratio": h.get("chop_ratio"),
                "chop_mult": h.get("chop_mult"),
                "period_quality": h.get("period_quality"),
                "effective_size_ft": h.get("effective_size_ft"),
                "stars": h.get("stars"),
                "source": "nwps",
            })

    records, deduped = _dedupe_by_keys(records, ("spot_id", "valid_time", "source"))
    log.info(
        "forecasts: upserting %d rows (%d spots in ratings.json had no spots-table row, %d intra-batch duplicates dropped)",
        len(records), skipped_unknown, deduped,
    )
    written = 0
    for i in range(0, len(records), batch_size):
        chunk = records[i:i + batch_size]
        client.table("forecasts").upsert(
            chunk, on_conflict="spot_id,valid_time,source"
        ).execute()
        written += len(chunk)
    return written


# ---------------------------------------------------------------------------
# Buoys
# ---------------------------------------------------------------------------

def _buoy_obs_record(buoy_id: str, obs: dict) -> dict | None:
    t = obs.get("time")
    if not t:
        return None
    # The fetcher merges std + spec fields into the `latest` dict and into
    # any observation we hand here, so a single record can carry both the
    # combined wave (wave_height_m / dominant_period_s / mean_wave_dir_deg)
    # AND the swell-only partition (swell_height_m / swell_period_s /
    # swell_dir_deg). Either side may be null on a given timestamp.
    return {
        "buoy_id": buoy_id,
        "observed_at": t,
        "hs": obs.get("wave_height_m"),
        "tp": obs.get("dominant_period_s"),
        "dp": obs.get("mean_wave_dir_deg"),
        "swell_hs": obs.get("swell_height_m"),
        "swell_tp": obs.get("swell_period_s"),
        "swell_dp": obs.get("swell_dir_deg"),
        "wind_speed": obs.get("wind_speed_ms"),
        "wind_dir": obs.get("wind_dir_deg"),
        "water_temp": obs.get("water_temp_c"),
    }


def import_buoys(client, buoys_path: Path = BUOYS_FORECAST_FILE,
                 batch_size: int = _DEFAULT_BATCH * 5) -> int:
    """Upsert NDBC buoy observations from buoys.json (latest + 24h history).

    Merges the .std and .spec histories by observed_at so the swell-only
    partition (swell_height_m / swell_period_s / swell_dir_deg) gets
    persisted alongside the combined-wave (.std) values when both are
    reported for the same timestamp.
    """
    buoys = json.loads(Path(buoys_path).read_text())
    records: list[dict] = []
    for buoy_id, data in buoys.items():
        merged: dict[str, dict] = {}

        # Latest is already a std + spec union from the fetcher; treat it
        # as the canonical entry for its timestamp.
        latest = data.get("latest") or {}
        if latest.get("time"):
            merged[latest["time"]] = dict(latest)

        for obs in data.get("history_24h") or []:
            t = obs.get("time")
            if not t:
                continue
            merged.setdefault(t, {}).update(obs)

        # Spec entries fill in swell_* fields without clobbering anything
        # the std side already populated.
        for obs in data.get("spec_history_24h") or []:
            t = obs.get("time")
            if not t:
                continue
            target = merged.setdefault(t, {"time": t})
            for k, v in obs.items():
                if v is None:
                    continue
                target.setdefault(k, v)

        for obs in merged.values():
            rec = _buoy_obs_record(buoy_id, obs)
            if rec is not None:
                records.append(rec)

    # NDBC's "latest" entry is often also the most recent row in
    # history_24h, so the same (buoy_id, observed_at) pair appears twice
    # within a single buoy. Postgres rejects that with code 21000 unless
    # we collapse them first.
    records, deduped = _dedupe_by_keys(records, ("buoy_id", "observed_at"))
    log.info(
        "buoy_observations: upserting %d rows across %d buoys (%d intra-batch duplicates dropped)",
        len(records), len(buoys), deduped,
    )
    written = 0
    for i in range(0, len(records), batch_size):
        chunk = records[i:i + batch_size]
        client.table("buoy_observations").upsert(
            chunk, on_conflict="buoy_id,observed_at"
        ).execute()
        written += len(chunk)
    return written


# ---------------------------------------------------------------------------
# Tides
# ---------------------------------------------------------------------------

def _parse_coops_time(t_str: str) -> str | None:
    """CO-OPS hilo/hourly times are 'YYYY-MM-DD HH:MM' in LST/LDT (no tz).

    We store them as TIMESTAMPTZ cast as UTC for schema simplicity — the
    interpret pipeline already treats CO-OPS timestamps as local, so this
    keeps the database consistent with how the data is consumed. Document
    in the schema comment.
    """
    try:
        dt = datetime.strptime(t_str, "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None
    return dt.isoformat()


def import_tides(client, tides_path: Path = TIDES_FORECAST_FILE,
                 batch_size: int = _DEFAULT_BATCH * 5) -> int:
    """Upsert CO-OPS tide predictions from tides.json (hilo + hourly)."""
    tides = json.loads(Path(tides_path).read_text())
    records: list[dict] = []
    seen: set[tuple[str, str]] = set()  # de-dupe within a station

    for station_id, data in tides.items():
        for series_key in ("hilo", "hourly"):
            for entry in data.get(series_key) or []:
                t_str = entry.get("t")
                if not t_str:
                    continue
                predicted_at = _parse_coops_time(t_str)
                if predicted_at is None:
                    continue
                try:
                    level = float(entry.get("v"))
                except (TypeError, ValueError):
                    continue
                key = (station_id, predicted_at)
                if key in seen:
                    continue  # hilo entry takes precedence over a duplicate hourly
                seen.add(key)
                etype = entry.get("type") or None
                records.append({
                    "station_id": station_id,
                    "predicted_at": predicted_at,
                    "level_ft": level,
                    "type": etype if etype else None,
                })

    log.info("tide_predictions: upserting %d rows across %d stations",
             len(records), len(tides))
    written = 0
    for i in range(0, len(records), batch_size):
        chunk = records[i:i + batch_size]
        client.table("tide_predictions").upsert(
            chunk, on_conflict="station_id,predicted_at"
        ).execute()
        written += len(chunk)
    return written


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

def run_all(
    spots: bool = True,
    forecasts: bool = True,
    buoys: bool = True,
    tides: bool = True,
) -> dict[str, int]:
    """Library entry point — used by fetch_all.py via --push-to-db.

    Each table is gated independently so the hourly buoy-only cron job
    can call this with just buoys=True, while the every-6h full pipeline
    leaves all four enabled.
    """
    client = get_client()
    stats: dict[str, int] = {}
    if spots:
        stats["spots"] = import_spots(client)
    if forecasts:
        stats["forecasts"] = import_forecasts(client)
    if buoys:
        stats["buoy_observations"] = import_buoys(client)
    if tides:
        stats["tide_predictions"] = import_tides(client)
    return stats


def _print_summary(stats: dict[str, int]) -> None:
    print()
    print("=" * 60)
    print("Supabase import summary")
    print("=" * 60)
    for table, n in stats.items():
        print(f"  {table:<22} {n:>8} rows upserted")
    print("=" * 60)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__ and __doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group()
    g.add_argument("--spots-only", action="store_true",
                   help="Push only the spots table.")
    g.add_argument("--forecasts-only", action="store_true",
                   help="Push forecasts + buoys + tides (skip spots).")
    g.add_argument("--buoys-only", action="store_true",
                   help="Push only the buoy_observations table — used by the "
                        "hourly cron that only refreshes buoy data.")
    g.add_argument("--tides-only", action="store_true",
                   help="Push only the tide_predictions table.")
    g.add_argument("--all", action="store_true", default=True,
                   help="Push every table (default).")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    if args.spots_only:
        kwargs = dict(spots=True, forecasts=False, buoys=False, tides=False)
    elif args.forecasts_only:
        kwargs = dict(spots=False, forecasts=True, buoys=True, tides=True)
    elif args.buoys_only:
        kwargs = dict(spots=False, forecasts=False, buoys=True, tides=False)
    elif args.tides_only:
        kwargs = dict(spots=False, forecasts=False, buoys=False, tides=True)
    else:  # --all (default)
        kwargs = dict(spots=True, forecasts=True, buoys=True, tides=True)

    try:
        stats = run_all(**kwargs)
    except RuntimeError as e:
        log.error("%s", e)
        return 1

    _print_summary(stats)
    return 0


if __name__ == "__main__":
    sys.exit(main())
