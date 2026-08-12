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

INTENDED LIFESPAN — THIS IS NOT PERMANENT INFRASTRUCTURE. The table exists to settle the
P3-2 rating design question and to serve as a validation corpus for the rating rewrite,
expected to take TWELVE TO EIGHTEEN MONTHS. Once P3-2 is decided the intent is to THIN it to
shortest-lead-per-valid-hour and drop the rest — roughly a 90% reduction — keeping the
comparison corpus and discarding the lead sweep that only existed to answer the forecast-
skill question. That is the PLAN, deliberately not implemented here: no deletion path ships
with the capture, so nothing can prune the archive while it is still being filled.

LEAD CAP: model rows are captured only out to MAX_LEAD_HOURS (72). NWPS publishes f000-f144,
so this keeps about half the horizon and halves the storage. 72 h is chosen, not 48, because
P3-2 is about near-term rating quality — it covers the check-on-Thursday-for-Saturday case —
and because the peak-hopping failure mode being measured should if anything be MORE visible
at longer leads, so a 48 h cap would risk measuring only the easy end. RAISING THE CAP LATER
CANNOT BACKFILL: NOMADS retains ~5 days, so leads dropped today are gone for good. It is one
named constant to change if that trade ever needs revisiting.

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

THE UNIQUE KEY IS (valid_hour, wfo, buoy_id, source, lead_hours) — see --print-ddl for the
exact DDL this writes against. lead_hours is IN the key on purpose: P3-2 is about forecast
quality, not only nowcast agreement, so skill across lead times is a question we are likely
to want. Keeping every lead costs nothing at this volume, whereas excluding it and later
wanting it would mean restarting a year-long capture with no backfill available past
NOMADS' five-day retention.

WHAT A REPEATED valid_hour MEANS, since the archive now contains several rows per hour: the
same valid hour appears once per forecast lead that covered it — the 12Z cycle's f000, the
06Z cycle's f006, the 00Z cycle's f012 and so on all describe 12Z at different lead times,
and all are kept. To collapse the archive to ONE best estimate per hour, take the SHORTEST
lead; that is the same "shortest lead per valid hour" rule trust_check assembles with, and
it is deterministic. That rule is a documented property OF THE DATA for a reader to apply,
NOT a property of the key — this job no longer discards the longer leads.

BUOY ROWS CARRY lead_hours = 0 AS A SENTINEL, never NULL. An observation has no forecast
lead, but NULL cannot be used: in Postgres a NULL in a unique key does not compare equal to
another NULL, so two buoy rows for the same hour would BOTH insert and the constraint would
silently stop deduplicating. 0 is the sentinel precisely because it also reads as "zero lead
time", which an observation is.

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
UPSERT_KEY = "valid_hour,wfo,buoy_id,source,lead_hours"
# CG0_Trkng availability for a model row, so the analysis never has to read meaning into an
# empty systems array. 'ok' = the file was read and its system list (possibly legitimately
# empty) is what the model emitted; 'absent' = the cycle published CG1 but no CG0_Trkng at
# all; 'error' = the file existed but could not be read, parsed or node-reconciled.
TRKNG_OK, TRKNG_ABSENT, TRKNG_ERROR = "ok", "absent", "error"
# Buoy rows have no forecast lead. NULL is unusable here: a NULL in a Postgres unique key
# never compares equal to another NULL, so two buoy rows for one hour would both insert.
BUOY_LEAD_SENTINEL = 0
# Upper bound on NWPS cycles published per day; only used to size the recent_cycles request
# for a backfill window, never as a truth about the schedule.
_MAX_CYCLES_PER_DAY = 8
# FORECAST LEAD CAP. Model rows beyond this lead are not captured. NWPS publishes f000-f144;
# 72 keeps about half the horizon and halves the storage. Chosen over 48 because the
# peak-hopping failure mode P3-2 measures should be MORE visible at longer leads, so a
# tighter cap would risk measuring only the easy end, and because 72 h covers the
# check-on-Thursday-for-Saturday case the rating actually has to be good at.
# ONE EDIT TO CHANGE — but raising it later CANNOT BACKFILL: NOMADS retains ~5 days, so any
# lead not captured today is permanently gone.
MAX_LEAD_HOURS = 72
# Volume estimate, printed in the run header so the storage trade stays visible. Both
# figures are ASSUMPTIONS about the NWPS schedule, not measurements: hourly steps (f000-f144
# is 145 steps) and 4 cycles/day (the 6-hourly cadence reverify's n_cycles=4 ≈ 24 h implies).
_ASSUMED_CYCLES_PER_DAY = 4
_ASSUMED_STEP_HOURS = 1


def within_lead_cap(lead_hours, cap=None):
    """True iff a model step is inside the capture window. Pure, so the cap is testable
    without NOMADS. Inclusive at the boundary: a lead of exactly MAX_LEAD_HOURS is KEPT —
    "72 or less" — and a missing lead is not capturable at all."""
    if lead_hours is None:
        return False
    return lead_hours <= (MAX_LEAD_HOURS if cap is None else cap)


def expected_rows_per_zone_per_day(max_lead=None, *, cycles_per_day=_ASSUMED_CYCLES_PER_DAY,
                                   step_hours=_ASSUMED_STEP_HOURS):
    """(model, buoy, total) rows a single zone is expected to add per day under the lead cap.
    Every cycle contributes one model row per step out to the cap, and a valid hour is covered
    by every cycle within the cap — which is exactly why lead_hours is in the unique key.
    Buoy rows are one per valid hour. Pure, so the header figure cannot drift from the cap."""
    cap = MAX_LEAD_HOURS if max_lead is None else max_lead
    steps_per_cycle = int(cap // step_hours) + 1              # f000 inclusive
    model = steps_per_cycle * cycles_per_day
    buoy = 24 // step_hours
    return model, buoy, model + buoy

DDL = f"""-- Table {TABLE}.
--
-- INTENDED LIFESPAN: NOT permanent infrastructure. This table exists to settle the P3-2
-- rating design question and to serve as a validation corpus for the rating rewrite,
-- expected to run TWELVE TO EIGHTEEN MONTHS. Once P3-2 is decided the intent is to THIN it
-- to shortest-lead-per-valid-hour and drop the rest — roughly a 90% reduction — keeping the
-- comparison corpus and discarding the lead sweep that only existed to answer the
-- forecast-skill question. That is the plan, NOT implemented: no deletion path ships with
-- the capture, so nothing can prune the archive while it is still being filled.
--
-- Model rows are captured only out to a {MAX_LEAD_HOURS} h forecast lead (see MAX_LEAD_HOURS).
--
-- The UNIQUE constraint is load-bearing: archive_partitions.py upserts ON CONFLICT
-- ({UPSERT_KEY}) and will FAIL LOUDLY if it is absent, rather than
-- silently inserting duplicates on every run.
--
-- lead_hours IS PART OF THE KEY, so every forecast lead that covered a valid hour is kept
-- and skill can be compared ACROSS lead times. A repeated valid_hour therefore means "the
-- same hour, forecast from several cycles at different leads" — it is not a duplicate. To
-- collapse to one best estimate per hour, take the SHORTEST lead (the rule trust_check
-- assembles with). That is a property of the DATA for the reader to apply; the writer keeps
-- every lead.
create table if not exists {TABLE} (
  id             bigint generated always as identity primary key,
  valid_hour     timestamptz not null,      -- UTC, truncated to the hour
  wfo            text        not null,
  buoy_id        text        not null,
  source         text        not null,      -- 'model' | 'buoy'
  -- lead_hours is NOT NULL and part of the unique key. Buoy rows carry the sentinel 0
  -- rather than NULL: an observation has no lead, but a NULL in a unique key does not
  -- compare equal to another NULL in Postgres, so NULL would silently stop deduplicating
  -- buoy rows entirely. 0 also reads correctly as "zero lead time" for an observation.
  lead_hours     integer     not null,      -- model: NWPS forecast hour. buoy: 0 (sentinel).
  -- model side (null on buoy rows)
  cycle_date     text,                      -- 'YYYYMMDD' of the NWPS cycle
  cycle_hour     text,                      -- '00'..'23'
  -- Was CG0_Trkng actually read for this row? Explicit, so an empty `systems` array never
  -- has to carry the meaning: 'ok' = read (an empty list is then genuinely no systems),
  -- 'absent' = the cycle published CG1 but no CG0_Trkng, 'error' = present but unreadable
  -- or un-reconcilable. Filter on this, not on jsonb_array_length(systems) = 0.
  trkng_status   text,                      -- 'ok' | 'absent' | 'error'  (null on buoy rows)
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
  constraint {TABLE}_key unique (valid_hour, wfo, buoy_id, source, lead_hours)
);
create index if not exists {TABLE}_zone_idx on {TABLE} (wfo, buoy_id, valid_hour);
-- Shortest-lead-per-hour is the common read, so make it cheap:
create index if not exists {TABLE}_lead_idx on {TABLE} (wfo, buoy_id, source, valid_hour, lead_hours);
"""

# Every column a row may carry, in a fixed order — the row builders emit exactly this key
# set (missing values as None) so the shape cannot drift between the two sources.
KEY_FIELDS = ("valid_hour", "wfo", "buoy_id", "source", "lead_hours")
MODEL_FIELDS = ("cycle_date", "cycle_hour", "trkng_status", "swh", "perpw", "dirpw", "shts",
                "wind_speed", "wind_dir", "systems")
BUOY_FIELDS = ("hs_total", "hs_swell", "hs_windsea", "swell_dir", "windsea_dir",
               "total_mean_dir", "swell_frac", "split_method", "spec_swh", "spec_swp",
               "spec_swd", "spec_wvht", "spec_mwd")
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
                    trkng_status=TRKNG_OK, swh=None, perpw=None, dirpw=None, shts=None,
                    wind_speed=None, wind_dir=None, systems=None):
    """One source='model' row. Every ALL_FIELDS key is present; buoy-side columns are None.

    *lead_hours* is REQUIRED and part of the unique key, so it may not be None — a NULL in a
    Postgres unique key never compares equal, which would silently stop the row deduplicating.
    *trkng_status* records whether CG0_Trkng was actually read, so an empty *systems* list
    never has to carry that meaning by implication."""
    if lead_hours is None:
        raise ValueError("lead_hours is part of the unique key and cannot be None on a model "
                         "row; a NULL there would silently defeat deduplication")
    if trkng_status not in (TRKNG_OK, TRKNG_ABSENT, TRKNG_ERROR):
        raise ValueError(f"trkng_status must be one of "
                         f"{(TRKNG_OK, TRKNG_ABSENT, TRKNG_ERROR)}, got {trkng_status!r}")
    row = {k: None for k in ALL_FIELDS}
    row.update(valid_hour=valid_hour_iso(epoch_hour), wfo=wfo, buoy_id=str(buoy_id),
               source="model", cycle_date=cycle_date, cycle_hour=cycle_hour,
               lead_hours=int(lead_hours), trkng_status=trkng_status,
               swh=_num(swh), perpw=_num(perpw), dirpw=_num(dirpw), shts=_num(shts),
               wind_speed=_num(wind_speed), wind_dir=_num(wind_dir),
               systems=sanitize_systems(systems))
    return row


def build_buoy_row(wfo, buoy_id, epoch_hour, *, spectral=None, spec=None, std=None):
    """One source='buoy' row. *spectral* is an ndbc_spectral.by_hour entry, *spec* a
    {'swh','swp','swd'} .spec entry, *std* a {'hs','mwd'} .txt entry (WVHT / MWD). Any of
    the three may be absent — the row still carries the full key set with Nones, so a
    partial hour is recorded rather than dropped. lead_hours is the BUOY_LEAD_SENTINEL (0),
    never NULL — see the constant."""
    sp, sc, st = spectral or {}, spec or {}, std or {}
    row = {k: None for k in ALL_FIELDS}
    row.update(valid_hour=valid_hour_iso(epoch_hour), wfo=wfo, buoy_id=str(buoy_id),
               source="buoy", lead_hours=BUOY_LEAD_SENTINEL,
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


def dedupe_by_key(rows):
    """Collapse EXACT-key duplicates — one row per (valid_hour, wfo, buoy_id, source,
    lead_hours). Different leads for the same valid hour are DIFFERENT keys and all survive:
    keeping every lead is the point of putting lead_hours in the key, so this must never
    collapse across leads. First occurrence wins (deterministic), order preserved.

    This is not merely tidiness. PostgREST rejects a batch that contains two rows matching
    the same ON CONFLICT target — "cannot affect row a second time" — so an exact-key
    duplicate inside one upsert chunk would fail the whole chunk rather than dedupe itself.

    NOTE the shortest-lead-per-hour rule now lives in the DATA, not here: to reduce the
    archive to one best estimate per valid hour, a reader selects the minimum lead_hours per
    (valid_hour, wfo, buoy_id, source). The writer keeps them all."""
    best = {}
    for r in rows:
        best.setdefault(tuple(r.get(f) for f in KEY_FIELDS), r)
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


def render_summary(rows_per_zone, n_skipped, *, dry_run):
    """(total_rows, n_zones, line) for the final run summary. Extracted and pure BECAUSE IT
    WAS WRONG: the totals were accumulated from upsert_rows' return value, which was 0 under
    --dry-run, so a run whose per-zone lines each reported hundreds of rows finished with
    "would write 0 row(s) across 26 zone(s)". A scheduled run is judged on that line, and a
    real capture reporting zero reads as a silent no-op — the one failure mode this job must
    not have. Counting the rows actually submitted per zone makes it true in both modes."""
    total = sum(rows_per_zone)
    n_zones = len(rows_per_zone)
    verb = "would write" if dry_run else "wrote"
    return total, n_zones, (f"{verb} {total} row(s) across {n_zones} zone(s); "
                            f"{n_skipped} skipped.")


def upsert_rows(client, rows, *, dry_run=False, batch_size=200):
    """Upsert on the (valid_hour, wfo, buoy_id, source, lead_hours) unique key → the number of
    rows written, or under *dry_run* the number that WOULD be written. Returning the count in
    both modes is deliberate: returning 0 for a dry run is what made the run summary read
    "would write 0 row(s)" while every per-zone line reported hundreds.
    Idempotent BY CONSTRUCTION: same key → same row replaced, never a duplicate insert.
    A missing constraint surfaces as PostgREST 42P10 ("no unique or exclusion constraint
    matching the ON CONFLICT specification"); that is re-raised with the actionable message
    rather than left as a raw driver error — VERIFYING the key exists rather than assuming it."""
    if dry_run:
        return len(rows)              # what WOULD be written — see the docstring
    if not rows:
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
def load_station_roster():
    """(active, reporting) NDBC station lists, loaded ONCE per run, plus a single staleness
    warning. nwps_nearshore._buoy_latlng calls _warn_if_roster_stale() on every invocation
    when the lists are not injected — its docstring says "warn once", but the caller is
    per-zone, so a 26-zone run printed the same warning 26 times with the age drifting as the
    clock moved (2.3 → 2.4 days). Loading here and INJECTING the lists takes that path out
    entirely: the warning is emitted once, up front, and the XML is parsed once instead of
    once per zone. Returns (None, None) if the roster is unavailable, which lets every zone
    SKIP with its own specific message exactly as before."""
    from pipeline.forecast import nwps_nearshore as nn
    from pipeline.enrichment.geodata import (load_ndbc_active_stations,
                                             load_ndbc_wave_stations)
    nn._warn_if_roster_stale()                    # ONCE per run, not once per zone
    try:
        return list(load_ndbc_active_stations()), list(load_ndbc_wave_stations())
    except Exception:                             # noqa: BLE001 — zones will SKIP individually
        return None, None


def group_zones_by_wfo(zones):
    """[(wfo, [(buoy_id, zone), ...])] preserving first-seen order, both of the WFOs and of
    the zones inside each. Grouping FIRST is the whole optimisation: zones on one WFO share
    the CG1 and CG0_Trkng grids EXACTLY — only their node coordinates differ — so fetching
    inside a per-zone loop re-downloaded and re-parsed the same GRIBs once per zone. Across
    26 zones on ~15 distinct WFOs that is ~1.7x the necessary work on average, and worse in
    the multi-zone regions (mhx 4, sgx 4, then pqr/jax/ilm/lox at 2 each)."""
    groups, order = {}, []
    for wfo, buoy_id, zone in zones:
        if wfo not in groups:
            groups[wfo] = []
            order.append(wfo)
        groups[wfo].append((buoy_id, zone))
    return [(w, groups[w]) for w in order]


def resolve_cycles(wfo, region, backfill_days, *, nn):
    """[(date, cc, url)] for a WFO's capture window, listed ONCE per WFO rather than once per
    zone. backfill 0 → the latest cycle only. backfill N → every cycle whose date falls inside
    the last N days; recent_cycles is asked for a generous count and filtered by date, so the
    schedule's real cycles-per-day never has to be guessed."""
    if backfill_days and backfill_days > 0:
        want = max(1, (backfill_days + 1) * _MAX_CYCLES_PER_DAY)
        cutoff = (datetime.datetime.now(datetime.timezone.utc)
                  - datetime.timedelta(days=backfill_days)).strftime("%Y%m%d")
        return [c for c in nn.recent_cycles(wfo, want, region) if c[0] >= cutoff]
    latest = nn.find_latest_cycle(wfo, region)
    return [latest] if latest else []


def _classify_trkng_error(e):
    """(status, why) for a CG0_Trkng fetch failure. ABSENT vs ERROR: a 404 on the CG0
    listing/file, or a body that is not GRIB, means the cycle simply did not publish
    CG0_Trkng. Anything else (eccodes failure, truncated read, parse error) is a real error
    and must not be laundered into "the file wasn't there"."""
    code = getattr(e, "code", None)
    absent = code == 404 or "not GRIB" in str(e) or "no swdir/shts/mpts" in str(e)
    status = TRKNG_ABSENT if absent else TRKNG_ERROR
    return status, f"Trkng {status}: {type(e).__name__}: {e}"


def load_wfo_cycles(wfo, cycles, *, load_cycle, load_trkng):
    """[(date, cc, cg1, trkc, fetch_status, fetch_why)] — each (wfo, cycle) pair fetched and
    parsed EXACTLY ONCE, for every zone on that WFO to sample from. This is the function the
    optimisation lives in; *load_cycle* and *load_trkng* are injected so the fetch COUNT is
    testable offline.

    A CG1 that will not load drops that CYCLE (not the WFO): the remaining cycles still
    produce rows. CG0_Trkng stays OPTIONAL per cycle — a cycle can publish CG1 without it, and
    losing a whole hour to a missing partition file would be the worse trade under a 5-day
    clock — so its failure is recorded as a status and carried onto every row instead."""
    bundles = []
    for date, cc, url in sorted(cycles):
        try:
            cg1 = load_cycle(wfo, (date, cc, url))
        except Exception as e:                              # noqa: BLE001
            bundles.append((date, cc, None, None, TRKNG_ERROR,
                            f"CG1 unreadable: {type(e).__name__}: {e}"))
            continue
        try:
            trkc = load_trkng(wfo, (date, cc, url))
            bundles.append((date, cc, cg1, trkc, TRKNG_OK, None))
        except Exception as e:                              # noqa: BLE001
            status, why = _classify_trkng_error(e)
            bundles.append((date, cc, cg1, None, status, why))
    return bundles


def rows_for_zone(wfo, buoy_id, blat, blng, bundles, cap, *, nn, trk):
    """(model_rows, model_wind, seen_hours, n_over_cap, trkng_why) for ONE zone, sampled from
    ALREADY-PARSED cycles. Only the NODE differs between zones on a WFO, so this is the part
    that genuinely has to run per zone — everything upstream of it is now shared.

    trkng_status is resolved per zone even though the FETCH is shared: a cycle's Trkng file
    can load fine and still fail to node-reconcile for one particular buoy, which is that
    zone's 'error', not the WFO's."""
    model_rows, seen_hours, model_wind, n_over_cap, trkng_why = [], set(), {}, 0, None
    for date, cc, cg1, trkc, fetch_status, fetch_why in bundles:
        if cg1 is None:                       # this cycle's CG1 never parsed — shared failure
            trkng_why = fetch_why
            continue
        pcell = nn._nearest_cell(cg1, blat, blng)
        if pcell is None:
            continue
        ci, cj = pcell[0], pcell[1]
        if trkc is None:
            status, ti, tj = fetch_status, None, None
            if fetch_why:
                trkng_why = fetch_why
        else:
            node_lat = float(cg1["lats"][ci, cj])
            node_lng = float(cg1["lons"][ci, cj])
            ti, tj, why = trk.trkng_node(trkc, cg1, node_lat, node_lng)
            status = TRKNG_OK if ti is not None else TRKNG_ERROR
            if ti is None:
                trkng_why = f"Trkng read but not node-reconciled: {why}"
        for fh in cg1["steps"]:
            if not within_lead_cap(fh, cap):
                n_over_cap += 1        # beyond MAX_LEAD_HOURS — not captured, and reported
                continue
            valid = int((cg1["cycle_dt"] + datetime.timedelta(hours=fh)).timestamp() // 3600)
            ws = nn._node_value(cg1, "ws", fh, ci, cj)
            wd = nn._node_value(cg1, "wdir", fh, ci, cj)
            if ws is not None and wd is not None:
                model_wind.setdefault(valid, (ws, wd))
            systems = (trk.trkng_systems_at(trkc, ti, tj, fh)
                       if trkc is not None and ti is not None else [])
            model_rows.append(build_model_row(
                wfo, buoy_id, valid, cycle_date=date, cycle_hour=cc, lead_hours=fh,
                trkng_status=status,
                swh=nn._node_value(cg1, "swh", fh, ci, cj),
                perpw=nn._node_value(cg1, "perpw", fh, ci, cj),
                dirpw=nn._node_value(cg1, "dirpw", fh, ci, cj),
                shts=nn._node_value(cg1, "shts", fh, ci, cj),
                wind_speed=ws, wind_dir=wd, systems=systems))
            seen_hours.add(valid)
    return model_rows, model_wind, seen_hours, n_over_cap, trkng_why


def collect_wfo(wfo, members, *, backfill_days=0, max_lead_hours=None, roster=None,
                load_cycle=None, load_trkng=None):
    """{buoy_id: (rows, note)} for EVERY zone on one WFO, fetching each (wfo, cycle) pair
    exactly ONCE. Fetches are SINGLE-ATTEMPT with the modules' own timeouts — no retry loop,
    so an outage degrades fast (the reverify job's discipline).

    ERROR SCOPE, which changed with the grouping: a cycle-listing or GRIB failure now affects
    EVERY zone on this WFO rather than one, so it raises out of here and the caller reports it
    against each affected zone as a shared cause. Failures that are genuinely per zone — an
    unresolvable buoy id, a dead buoy feed — stay per zone and leave the others intact.

    *load_cycle* / *load_trkng* are injected so the fetch COUNT is testable offline; they
    default to the real NOMADS loaders."""
    from pipeline.forecast import nwps_nearshore as nn
    from pipeline.forecast import nwps_trkng as trk
    from pipeline.forecast import ndbc_spectral as ndbc_spec

    cap = MAX_LEAD_HOURS if max_lead_hours is None else max_lead_hours
    active, reporting = roster if roster else (None, None)
    region = nn._region_for(wfo)

    # ── ONCE PER WFO ────────────────────────────────────────────────────────────────────
    cycles = resolve_cycles(wfo, region, backfill_days, nn=nn)
    if not cycles:
        return {b: ([], "no NOMADS cycles in range") for b, _z in members}
    bundles = load_wfo_cycles(wfo, cycles, load_cycle=load_cycle or nn.load_cycle,
                              load_trkng=load_trkng or trk.load_trkng_cycle)

    # ── ONCE PER ZONE (only the node differs) ───────────────────────────────────────────
    out = {}
    for buoy_id, _zone in members:
        try:
            blat, blng = nn._buoy_latlng(buoy_id, _active=active, _reporting=reporting)
        except Exception as e:                              # noqa: BLE001 — per-zone only
            out[buoy_id] = ([], f"{type(e).__name__}: {e}")
            continue
        model_rows, model_wind, seen_hours, n_over_cap, trkng_why = rows_for_zone(
            wfo, buoy_id, blat, blng, bundles, cap, nn=nn, trk=trk)

        # Buoy side — one read each, per zone (a different station per zone, so not shared).
        spectral = ndbc_spec.by_hour(buoy_id, model_wind=model_wind) or {}
        spec = trk._spec_by_hour(buoy_id) or {}
        std = nn._buoy_hourly(buoy_id) or {}
        buoy_rows = [build_buoy_row(wfo, buoy_id, h, spectral=spectral.get(h),
                                    spec=spec.get(h), std=std.get(h))
                     for h in sorted(set(spectral) | set(spec) | set(std))
                     if h in seen_hours or not seen_hours]

        rows = dedupe_by_key(model_rows + buoy_rows)
        from collections import Counter
        st = Counter(r["trkng_status"] for r in rows if r["source"] == "model")
        note = (f"{len(cycles)} cycle(s), {len(seen_hours)} model hour(s) "
                f"[trkng {'/'.join(f'{k}:{v}' for k, v in sorted(st.items())) or 'none'}], "
                f"{len(buoy_rows)} buoy hour(s)"
                + (f", {n_over_cap} step(s) past the {cap} h lead cap" if n_over_cap else "")
                + (f"; {trkng_why}" if trkng_why and str(trkng_why).startswith("Trkng ") else ""))
        out[buoy_id] = (rows, note)
    return out


def collect_zone(wfo, buoy_id, *, backfill_days=0, max_rows_per_zone=None,
                 max_lead_hours=None, roster=None):
    """(rows, note) for a SINGLE zone — a thin wrapper over collect_wfo, kept so the one-zone
    path stays callable and exercised. The batch path groups by WFO first; see collect_wfo."""
    res = collect_wfo(wfo, [(buoy_id, f"{wfo}/{buoy_id}")], backfill_days=backfill_days,
                      max_lead_hours=max_lead_hours, roster=roster)
    rows, note = res.get(buoy_id, ([], "no result"))
    if max_rows_per_zone:
        rows = rows[:max_rows_per_zone]
    return rows, note


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", type=int, default=0, metavar="N",
                    help="also walk back N days of NOMADS cycles (default 0 = latest only). "
                         "Use 5 on the first run to capture everything still retained.")
    ap.add_argument("--dry-run", action="store_true",
                    help="print what would be written and write NOTHING")
    ap.add_argument("--max-lead", type=int, default=MAX_LEAD_HOURS, metavar="H",
                    help=f"drop model rows past this forecast lead (default {MAX_LEAD_HOURS}). "
                         "Raising it cannot backfill — NOMADS retains only ~5 days.")
    ap.add_argument("--print-ddl", action="store_true",
                    help="print the table DDL this script expects, then exit")
    ap.add_argument("--assignments", type=Path, default=ASSIGNMENTS)
    a = ap.parse_args(argv)

    if a.print_ddl:
        print(DDL)
        return 0

    doc = json.loads(Path(a.assignments).read_text(encoding="utf-8"))
    zones = pending_zones(doc)
    m, b, t = expected_rows_per_zone_per_day(a.max_lead)
    print(f"=== archive_partitions — {len(zones)} pending zone(s) with a buoy "
          f"| backfill {a.backfill}d | lead cap {a.max_lead} h "
          f"| {'DRY RUN (writes nothing)' if a.dry_run else 'WRITING'} ===")
    print(f"expected steady-state volume: ~{t} row(s)/zone/day ({m} model + {b} buoy), "
          f"~{t * len(zones):,}/day over {len(zones)} zones, ~{t * len(zones) * 365:,}/year "
          f"(assumes hourly steps and {_ASSUMED_CYCLES_PER_DAY} cycles/day)")
    if not zones:
        print("nothing to archive.")
        return 0

    client = None
    if not a.dry_run:
        from pipeline.db_import import get_client
        client = get_client()
        ensure_table(client)          # loud, specific failure if the table is missing

    # Roster loaded ONCE: this is what stops _buoy_latlng's staleness warning firing per zone.
    roster = load_station_roster()
    # GROUPED BY WFO so each cycle's GRIBs are fetched and parsed once, not once per zone.
    groups = group_zones_by_wfo(zones)
    print(f"grouped into {len(groups)} WFO(s): "
          + ", ".join(f"{w}({len(m)})" for w, m in groups)
          + f" — {len(zones)} zones / {len(groups)} WFOs = "
            f"{len(zones)/max(1,len(groups)):.2f}x fewer cycle fetches than one-per-zone")
    rows_per_zone, total_skipped = [], 0
    for wfo, members in groups:
        try:
            results = collect_wfo(wfo, members, backfill_days=a.backfill,
                                  max_lead_hours=a.max_lead, roster=roster)
        except Exception as e:        # noqa: BLE001 — one WFO must never abort the others
            # SCOPED REPORTING: the fetch is shared now, so a cycle/GRIB failure hits every
            # zone on this WFO at once. Say so, instead of printing what looks like the same
            # unexplained error N times.
            total_skipped += len(members)
            for _b, zone in members:
                print(f"  SKIP  {zone:<20} {wfo} cycle fetch failed — {type(e).__name__}: {e} "
                      f"(shared cause: all {len(members)} zone(s) on {wfo} affected)")
            if os.environ.get("ARCHIVE_PARTITIONS_TRACE"):
                traceback.print_exc()
            continue
        for buoy_id, zone in members:
            rows, note = results.get(buoy_id, ([], "no result"))
            if not rows:
                total_skipped += 1
                print(f"  SKIP  {zone:<20} no rows — {note}")
                continue
            n_model = sum(1 for r in rows if r["source"] == "model")
            n_buoy = len(rows) - n_model
            try:
                written = upsert_rows(client, rows, dry_run=a.dry_run)
            except RuntimeError:
                raise                 # a missing table/constraint is fatal, not a per-zone skip
            except Exception as e:    # noqa: BLE001
                total_skipped += 1
                print(f"  SKIP  {zone:<20} write failed — {type(e).__name__}: {e}")
                continue
            rows_per_zone.append(written)     # what this zone contributed, in BOTH modes
            verb = "would write" if a.dry_run else "wrote"
            print(f"  OK    {zone:<20} {verb} {len(rows):>4} row(s) "
                  f"({n_model} model, {n_buoy} buoy) — {note}")
            if a.dry_run and rows:
                print(f"        sample: {json.dumps(rows[0], default=str)[:200]}…")

    total, n_ok, line = render_summary(rows_per_zone, total_skipped, dry_run=a.dry_run)
    print(f"\n{line}")
    print("(capture only: no rating, trust verdict, assignment or spots_enriched.json touched.)")
    return 0                          # partial failure is not a run failure


if __name__ == "__main__":
    raise SystemExit(main())
