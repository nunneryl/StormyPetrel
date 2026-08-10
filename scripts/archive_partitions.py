#!/usr/bin/env python3
"""Archive partitioned wave data to Supabase — PURE DATA CAPTURE for the P3-2 test.

WHY THIS EXISTS, AND WHY IT IS URGENT. NOMADS retains only ~5 days of cycles (confirmed by
directory listing: 20260806-20260810 populated, everything older empty). The P3-2 design
question — which partition-selection rule actually tracks the buoy — can only be settled
against a year of paired model/buoy partitions, and every hour not captured inside that
5-day window is PERMANENTLY UNRECOVERABLE. This job exists to make that dataset exist.

HARD BOUNDARY: this is CAPTURE ONLY. It never modifies a rating, a trust verdict, an
assignment, spots_enriched.json, or any other table. It reads scripts/nwps_okx_assignments.json,
reads NOMADS + NDBC, and writes exactly one table: `partition_archive`.

WHAT IS CAPTURED — two rows per valid hour per pending zone:
  source='model'  CG1 at the buoy's node: swh, perpw, dirpw, shts, wind_speed, wind_dir;
                  CG0_Trkng at the reconciled node: the FULL RAW tracked-system list as
                  jsonb (each system carrying at least hs/tp/dir). The raw list is stored
                  UNMODIFIED — not a selected system — because the analysis has to compare
                  selection RULES against each other, and after five days re-fetching is
                  impossible. Storing a selection would bake today's rule into the record
                  and destroy the very comparison the archive is for.
                  Plus cycle_date, cycle_hour, lead_hours so lead time is distinguishable.
  source='buoy'   ndbc_spectral.by_hour: hs_total, hs_swell, hs_windsea, swell_dir,
                  windsea_dir, total_mean_dir, swell_frac, split_method; plus the station's
                  .spec product: SwH, SwP, SwD, WVHT, MWD. The .spec fields are the
                  INDEPENDENT reference the research report identifies and are not derivable
                  from anything else stored here, so they are captured verbatim.

THE UNIQUE KEY IS (valid_hour, wfo, buoy_id, source) — see --print-ddl for the exact DDL
this writes against. One consequence a later reader must not be surprised by: because
lead_hours is NOT in the key, only ONE lead survives per valid hour. This job keeps the
SHORTEST lead deterministically (the best estimate, and the same "shortest lead per valid
hour" rule trust_check assembles with), so a re-run cannot flip which lead is stored. If
lead-resolved analysis is ever wanted, lead_hours has to JOIN the unique key — that is a
schema change, not a code change here.

  python3 scripts/archive_partitions.py --dry-run              # print, write nothing
  python3 scripts/archive_partitions.py --backfill 5           # first production run
  python3 scripts/archive_partitions.py --print-ddl            # the table this expects
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys
import traceback
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

ASSIGNMENTS = REPO / "scripts" / "nwps_okx_assignments.json"
TABLE = "partition_archive"
UPSERT_KEY = "valid_hour,wfo,buoy_id,source"
# Upper bound on NWPS cycles published per day; only used to size the recent_cycles request
# for a backfill window, never as a truth about the schedule.
_MAX_CYCLES_PER_DAY = 8

DDL = f"""-- Table {TABLE} expects this shape. The UNIQUE constraint is load-bearing:
-- archive_partitions.py upserts ON CONFLICT ({UPSERT_KEY}) and will FAIL LOUDLY if it
-- is absent, rather than silently inserting duplicates on every run.
create table if not exists {TABLE} (
  id             bigint generated always as identity primary key,
  valid_hour     timestamptz not null,      -- UTC, truncated to the hour
  wfo            text        not null,
  buoy_id        text        not null,
  source         text        not null,      -- 'model' | 'buoy'
  -- model side (null on buoy rows)
  cycle_date     text,                      -- 'YYYYMMDD' of the NWPS cycle
  cycle_hour     text,                      -- '00'..'23'
  lead_hours     integer,                   -- forecast hour; shortest lead wins per valid_hour
  swh            double precision,
  perpw          double precision,
  dirpw          double precision,
  shts           double precision,
  wind_speed     double precision,
  wind_dir       double precision,
  systems        jsonb,                     -- RAW CG0_Trkng system list, unmodified
  -- buoy side (null on model rows)
  hs_total       double precision,
  hs_swell       double precision,
  hs_windsea     double precision,
  swell_dir      double precision,
  windsea_dir    double precision,
  total_mean_dir double precision,
  swell_frac     double precision,
  split_method   text,
  spec_swh       double precision,          -- .spec SwH
  spec_swp       double precision,          -- .spec SwP
  spec_swd       double precision,          -- .spec SwD
  spec_wvht      double precision,          -- .txt WVHT
  spec_mwd       double precision,          -- .txt MWD
  archived_at    timestamptz not null default now(),
  constraint {TABLE}_key unique (valid_hour, wfo, buoy_id, source)
);
create index if not exists {TABLE}_zone_idx on {TABLE} (wfo, buoy_id, valid_hour);
"""

# Every column a row may carry, in a fixed order — the row builders emit exactly this key
# set (missing values as None) so the shape cannot drift between the two sources.
MODEL_FIELDS = ("cycle_date", "cycle_hour", "lead_hours", "swh", "perpw", "dirpw", "shts",
                "wind_speed", "wind_dir", "systems")
BUOY_FIELDS = ("hs_total", "hs_swell", "hs_windsea", "swell_dir", "windsea_dir",
               "total_mean_dir", "swell_frac", "split_method", "spec_swh", "spec_swp",
               "spec_swd", "spec_wvht", "spec_mwd")
KEY_FIELDS = ("valid_hour", "wfo", "buoy_id", "source")
ALL_FIELDS = KEY_FIELDS + MODEL_FIELDS + BUOY_FIELDS


# --------------------------------------------------------------------------- #
# Pure helpers (unit-tested; no network, no DB)                                #
# --------------------------------------------------------------------------- #
def valid_hour_iso(epoch_hour):
    """Epoch-hour bucket (the int the whole codebase keys valid hours by) → an ISO-8601 UTC
    timestamp, which is what a timestamptz column wants. Hour-truncated by construction."""
    return datetime.datetime.fromtimestamp(int(epoch_hour) * 3600,
                                           tz=datetime.timezone.utc).isoformat()


def _num(v):
    """Float, or None for anything not a finite real number — NaN included. NaN is not valid
    JSON and PostgREST rejects it, so it must never reach a row."""
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f == f and abs(f) != float("inf") else None


def sanitize_systems(systems):
    """The RAW tracked-system list, made JSON-safe WITHOUT selecting, reordering or dropping
    a system. Each entry keeps every key it arrived with; only non-finite numbers become
    null. A system missing hs/tp/dir still round-trips — the archive records what the model
    emitted, and judging completeness is the analysis's job, not the capture's."""
    out = []
    for s in (systems or []):
        if not isinstance(s, dict):
            continue
        out.append({k: (_num(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
                    for k, v in s.items()})
    return out


def pending_zones(doc):
    """[(wfo, buoy_id, zone)] for every buoy_reference.pending record that HAS a buoy —
    unverifiable and retired zones have no reference to archive against, and a pending
    record without a buoy id cannot be paired with a station."""
    out = []
    for r in ((doc.get("buoy_reference") or {}).get("pending") or []):
        wfo, buoy = r.get("wfo"), r.get("buoy")
        if wfo and buoy is not None:
            out.append((wfo, str(buoy), r.get("zone") or f"{wfo}/{buoy}"))
    return out


def build_model_row(wfo, buoy_id, epoch_hour, *, cycle_date, cycle_hour, lead_hours,
                    swh=None, perpw=None, dirpw=None, shts=None,
                    wind_speed=None, wind_dir=None, systems=None):
    """One source='model' row. Every ALL_FIELDS key is present; buoy-side columns are None."""
    row = {k: None for k in ALL_FIELDS}
    row.update(valid_hour=valid_hour_iso(epoch_hour), wfo=wfo, buoy_id=str(buoy_id),
               source="model", cycle_date=cycle_date, cycle_hour=cycle_hour,
               lead_hours=(None if lead_hours is None else int(lead_hours)),
               swh=_num(swh), perpw=_num(perpw), dirpw=_num(dirpw), shts=_num(shts),
               wind_speed=_num(wind_speed), wind_dir=_num(wind_dir),
               systems=sanitize_systems(systems))
    return row


def build_buoy_row(wfo, buoy_id, epoch_hour, *, spectral=None, spec=None, std=None):
    """One source='buoy' row. *spectral* is an ndbc_spectral.by_hour entry, *spec* a
    {'swh','swp','swd'} .spec entry, *std* a {'hs','mwd'} .txt entry (WVHT / MWD). Any of
    the three may be absent — the row still carries the full key set with Nones, so a
    partial hour is recorded rather than dropped."""
    sp, sc, st = spectral or {}, spec or {}, std or {}
    row = {k: None for k in ALL_FIELDS}
    row.update(valid_hour=valid_hour_iso(epoch_hour), wfo=wfo, buoy_id=str(buoy_id),
               source="buoy",
               hs_total=_num(sp.get("hs_total")), hs_swell=_num(sp.get("hs_swell")),
               hs_windsea=_num(sp.get("hs_windsea")), swell_dir=_num(sp.get("swell_dir")),
               windsea_dir=_num(sp.get("windsea_dir")),
               total_mean_dir=_num(sp.get("total_mean_dir")),
               swell_frac=_num(sp.get("swell_frac")),
               split_method=sp.get("split_method"),
               spec_swh=_num(sc.get("swh")), spec_swp=_num(sc.get("swp")),
               spec_swd=_num(sc.get("swd")),
               spec_wvht=_num(st.get("hs")), spec_mwd=_num(st.get("mwd")))
    return row


def dedupe_shortest_lead(rows):
    """Collapse rows to ONE per (valid_hour, wfo, buoy_id, source) — the unique key. Model
    rows from several cycles cover the same valid hour at different leads; the SHORTEST lead
    wins, which is both the best estimate and DETERMINISTIC, so a re-run in any cycle order
    stores the same row (that determinism is what makes the whole job idempotent, not just
    the upsert). Ties and rows without a lead keep the first seen. Order is preserved."""
    best = {}
    for r in rows:
        k = tuple(r.get(f) for f in KEY_FIELDS)
        cur = best.get(k)
        if cur is None:
            best[k] = r
            continue
        new_lead, cur_lead = r.get("lead_hours"), cur.get("lead_hours")
        if new_lead is not None and (cur_lead is None or new_lead < cur_lead):
            best[k] = r
    return list(best.values())


# --------------------------------------------------------------------------- #
# Supabase I/O                                                                 #
# --------------------------------------------------------------------------- #
def ensure_table(client):
    """Fail LOUDLY and specifically if `partition_archive` is absent. The table is created by
    hand before the first run, so a missing table is a setup error, not a transient one —
    the run must stop with an actionable message rather than log a vague per-zone SKIP for
    every zone and exit 0 as if it had merely degraded."""
    try:
        client.table(TABLE).select("valid_hour").limit(1).execute()
    except Exception as e:                                   # noqa: BLE001 — any client error
        raise RuntimeError(
            f"table {TABLE!r} is not readable ({type(e).__name__}: {e}).\n"
            f"This job does NOT create it. Create it first — run\n"
            f"    python3 scripts/archive_partitions.py --print-ddl\n"
            f"and apply that DDL, then re-run. The UNIQUE ({UPSERT_KEY}) constraint in it is\n"
            f"required: the upsert targets it by name and will fail without it."
        ) from e


def upsert_rows(client, rows, *, dry_run=False, batch_size=200):
    """Upsert on the (valid_hour, wfo, buoy_id, source) unique key → number of rows written.
    Idempotent BY CONSTRUCTION: same key → same row replaced, never a duplicate insert.
    A missing constraint surfaces as PostgREST 42P10 ("no unique or exclusion constraint
    matching the ON CONFLICT specification"); that is re-raised with the actionable message
    rather than left as a raw driver error — VERIFYING the key exists rather than assuming it."""
    if dry_run or not rows:
        return 0
    written = 0
    for i in range(0, len(rows), batch_size):
        chunk = rows[i:i + batch_size]
        try:
            client.table(TABLE).upsert(chunk, on_conflict=UPSERT_KEY).execute()
        except Exception as e:                               # noqa: BLE001
            if "42P10" in str(e) or "ON CONFLICT" in str(e):
                raise RuntimeError(
                    f"{TABLE} has no UNIQUE ({UPSERT_KEY}) constraint, so the upsert cannot be "
                    f"idempotent and every run would duplicate rows. Apply the constraint from "
                    f"--print-ddl before running again. Underlying error: {e}") from e
            raise
        written += len(chunk)
    return written


# --------------------------------------------------------------------------- #
# Per-zone capture (single-attempt fetches; one zone's failure never aborts)   #
# --------------------------------------------------------------------------- #
def collect_zone(wfo, buoy_id, *, backfill_days=0, max_rows_per_zone=None):
    """(rows, note) for one zone. Fetches are SINGLE-ATTEMPT with the module's own timeouts
    — no retry loop, so an outage degrades fast (the reverify job's discipline). Needs
    NOMADS + eccodes + NDBC; raises on failure, and the caller turns that into a SKIP."""
    from pipeline.forecast import nwps_nearshore as nn
    from pipeline.forecast import nwps_trkng as trk
    from pipeline.forecast import ndbc_spectral as ndbc_spec

    region = nn._region_for(wfo)
    blat, blng = nn._buoy_latlng(buoy_id)

    # Cycle window. backfill 0 → the latest cycle only. backfill N → every cycle whose date
    # falls inside the last N days; recent_cycles is asked for a generous count and filtered
    # by date, so the schedule's real cycles-per-day never has to be guessed.
    if backfill_days and backfill_days > 0:
        want = max(1, (backfill_days + 1) * _MAX_CYCLES_PER_DAY)
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(days=backfill_days)).strftime("%Y%m%d")
        cycles = [c for c in nn.recent_cycles(wfo, want, region) if c[0] >= cutoff]
    else:
        latest = nn.find_latest_cycle(wfo, region)
        cycles = [latest] if latest else []
    if not cycles:
        return [], "no NOMADS cycles in range"

    model_rows, seen_hours = [], set()
    model_wind, trkng_why = {}, None
    # Oldest cycle first so the newest (shortest-lead) row is the one dedupe_shortest_lead
    # keeps on a tie; the lead comparison makes this ordering-independent anyway.
    for date, cc, url in sorted(cycles):
        cg1 = nn.load_cycle(wfo, (date, cc, url))
        pcell = nn._nearest_cell(cg1, blat, blng)
        if pcell is None:
            continue
        ci, cj = pcell[0], pcell[1]
        node_lat = float(cg1["lats"][ci, cj])
        node_lng = float(cg1["lons"][ci, cj])
        # CG0_Trkng is OPTIONAL: a cycle can publish CG1 without it. Model rows are still
        # captured (fields + wind), just with an empty system list — losing the whole hour
        # because the partition file is missing would be the worse trade under a 5-day clock.
        try:
            trkc = trk.load_trkng_cycle(wfo, (date, cc, url))
            ti, tj, trkng_why = trk.trkng_node(trkc, cg1, node_lat, node_lng)
        except Exception:                                     # noqa: BLE001
            trkc, ti, tj = None, None, None
            trkng_why = "Trkng unavailable for this cycle"
        for fh in cg1["steps"]:
            valid = int((cg1["cycle_dt"] + datetime.timedelta(hours=fh)).timestamp() // 3600)
            ws = nn._node_value(cg1, "ws", fh, ci, cj)
            wd = nn._node_value(cg1, "wdir", fh, ci, cj)
            if ws is not None and wd is not None:
                model_wind.setdefault(valid, (ws, wd))
            systems = (trk.trkng_systems_at(trkc, ti, tj, fh)
                       if trkc is not None and ti is not None else [])
            model_rows.append(build_model_row(
                wfo, buoy_id, valid, cycle_date=date, cycle_hour=cc, lead_hours=fh,
                swh=nn._node_value(cg1, "swh", fh, ci, cj),
                perpw=nn._node_value(cg1, "perpw", fh, ci, cj),
                dirpw=nn._node_value(cg1, "dirpw", fh, ci, cj),
                shts=nn._node_value(cg1, "shts", fh, ci, cj),
                wind_speed=ws, wind_dir=wd, systems=systems))
            seen_hours.add(valid)

    # Buoy side — one read each, covering every hour the model rows span.
    spectral = ndbc_spec.by_hour(buoy_id, model_wind=model_wind) or {}
    spec = trk._spec_by_hour(buoy_id) or {}
    std = nn._buoy_hourly(buoy_id) or {}
    buoy_rows = [build_buoy_row(wfo, buoy_id, h, spectral=spectral.get(h),
                                spec=spec.get(h), std=std.get(h))
                 for h in sorted(set(spectral) | set(spec) | set(std))
                 if h in seen_hours or not seen_hours]

    rows = dedupe_shortest_lead(model_rows) + buoy_rows
    if max_rows_per_zone:
        rows = rows[:max_rows_per_zone]
    note = (f"{len(cycles)} cycle(s), {len(seen_hours)} model hour(s), "
            f"{len(buoy_rows)} buoy hour(s)"
            + (f"; {trkng_why}" if trkng_why and "Trkng unavailable" in str(trkng_why) else ""))
    return rows, note


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", type=int, default=0, metavar="N",
                    help="also walk back N days of NOMADS cycles (default 0 = latest only). "
                         "Use 5 on the first run to capture everything still retained.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be written and write NOTHING")
    ap.add_argument("--print-ddl", action="store_true",
                    help="print the table DDL this script expects, then exit")
    ap.add_argument("--assignments", type=Path, default=ASSIGNMENTS)
    a = ap.parse_args(argv)

    if a.print_ddl:
        print(DDL)
        return 0

    doc = json.loads(Path(a.assignments).read_text(encoding="utf-8"))
    zones = pending_zones(doc)
    print(f"=== archive_partitions — {len(zones)} pending zone(s) with a buoy "
          f"| backfill {a.backfill}d | {'DRY RUN (writes nothing)' if a.dry_run else 'WRITING'} ===")
    if not zones:
        print("nothing to archive.")
        return 0

    client = None
    if not a.dry_run:
        from pipeline.db_import import get_client
        client = get_client()
        ensure_table(client)          # loud, specific failure if the table is missing

    total_written = total_skipped = 0
    for wfo, buoy_id, zone in zones:
        try:
            rows, note = collect_zone(wfo, buoy_id, backfill_days=a.backfill)
        except Exception as e:        # noqa: BLE001 — one zone must never abort the run
            total_skipped += 1
            print(f"  SKIP  {zone:<20} {type(e).__name__}: {e}")
            if os.environ.get("ARCHIVE_PARTITIONS_TRACE"):
                traceback.print_exc()
            continue
        if not rows:
            total_skipped += 1
            print(f"  SKIP  {zone:<20} no rows — {note}")
            continue
        n_model = sum(1 for r in rows if r["source"] == "model")
        n_buoy = len(rows) - n_model
        try:
            written = upsert_rows(client, rows, dry_run=a.dry_run)
        except RuntimeError:
            raise                     # a missing table/constraint is fatal, not a per-zone skip
        except Exception as e:        # noqa: BLE001
            total_skipped += 1
            print(f"  SKIP  {zone:<20} write failed — {type(e).__name__}: {e}")
            continue
        total_written += written
        verb = "would write" if a.dry_run else "wrote"
        print(f"  OK    {zone:<20} {verb} {len(rows):>4} row(s) "
              f"({n_model} model, {n_buoy} buoy) — {note}")
        if a.dry_run and rows:
            print(f"        sample: {json.dumps(rows[0], default=str)[:200]}…")

    print(f"\n{'would write' if a.dry_run else 'wrote'} {total_written} row(s) "
          f"across {len(zones) - total_skipped} zone(s); {total_skipped} skipped.")
    print("(capture only: no rating, trust verdict, assignment or spots_enriched.json touched.)")
    return 0                          # partial failure is not a run failure


if __name__ == "__main__":
    raise SystemExit(main())
