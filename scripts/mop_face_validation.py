#!/usr/bin/env python3
"""Validate our published face height against CDIP MOP as an independent nearshore reference.

WHAT THIS MEASURES, AND WHAT IT DOES NOT
========================================
THIS IS NOT A HEIGHT-VS-HEIGHT COMPARISON. Do not read it as one, and do not let a
summary of it be written as one.

MOP publishes NO breaking height. Its `waveHs` is a *significant wave height at a depth
contour* (~10 m in Southern California) — the full recorded variable list is in
docs/mop_prototype_report.md:88-99 and contains no breaker height and no breaker index.
Our `face_ft` is a *modelled breaking face*: interpret.face_ft = hs_m × period_factor(tp)
× M_TO_FT, where period_factor on the "ww3" curve is a scalar between 1.00 and 1.30. That
scalar plus the metre→foot conversion is the ENTIRE transform standing between the two
quantities — no depth term, no beach slope, no gamma, no local bathymetry.

So what this script actually measures is the RATIO

        face_ratio = face_ft / (MOP waveHs × M_TO_FT)

and asks whether it is a plausible 10 m-Hs → breaking-face amplification, or whether it
reproduces the 1.35× median / 2.43× p90 inflation already measured against a
surfable-only face. That is a direct measurement of the period_factor curve with an
INDEPENDENT nearshore height underneath it — the first time that curve has been checked
against anything but itself.

BOTH WAYS, AND THIS IS THE VALUABLE PART
========================================
MOP's Hs is already DIRECTIONALLY GATED: a swell that cannot reach the spot arrives at
the 10 m point with near-zero Hs (docs/mop_blacks_slice_report.md:68-71 — "MOP's Hs *is*
the directional gate"). Our face_ft is NOT gated; the gate lives in dir_gain, and
face_ft × dir_gain = effective_size_ft, which is computed, stored, selected, typed and
rendered nowhere. So this also computes

        eff_ratio = effective_size_ft / (MOP waveHs × M_TO_FT)

Comparing the two ratios separates two very different diagnoses:
  * both ratios inflated by a similar factor  → the inflation is in period_factor;
  * face_ratio inflated but eff_ratio near 1  → the inflation is the MISSING DIRECTIONAL
    GATE, and the fix is to publish effective_size_ft, which we already compute.

THE POPULATION, AND WHY THE OBVIOUS ONE IS WRONG
================================================
This deliberately EXCLUDES the 48 spots tagged swell_window_source == "cdip_mop". Their
published face_ft was computed FROM MOP by forecast/mop.py::apply_mop_overrides
(face = face_ft(mop_hs, mop_tp, "ww3") at mop.py:164), so validating them against MOP
would validate MOP against itself and would return period_factor × M_TO_FT by
construction. They are the WORST spots to validate on, not the best.

The valid population is California spots with swell_window_source == "nwps" and no mop_*
fields — 153 on the committed roster. Their face is NWPS-derived and genuinely
independent of MOP.

WHAT THE COMPARISON IS AGAINST ON OUR SIDE
==========================================
`forecasts` is UNIQUE(spot_id, valid_time, source) and every pipeline run upserts over
the hours it covers, so a PAST hour's face_ft has drifted to the shortest-lead forecast
made for it — effectively a nowcast. This script therefore measures our NOWCAST-LEAD
face, not the face a reader saw at the time. That is the right comparison for "is the
transform right", which is the question here — but it is not "was the published forecast
right", and the output labels it so.

RUNNING IT
==========
THREDDS is egress-blocked in the dev sandbox (403). Run this on a box with open CDIP
egress and scripts/mop_points.json present (the Mac).

    export SUPABASE_URL=... SUPABASE_SERVICE_KEY=...
    python3 scripts/mop_face_validation.py                    # 14 days back, all 153
    python3 scripts/mop_face_validation.py --days-back 30
    python3 scripts/mop_face_validation.py --limit 8          # smoke test, 8 spots
    python3 scripts/mop_face_validation.py --chunk-size 16    # probe for the planner cliff
    python3 scripts/mop_face_validation.py --selftest         # offline, no network, no DB

Writes scripts/mop_face_validation_out.json so a second run can be diffed against the
first. Reads Supabase; writes NOTHING to it.

ON pipeline.http: the MOP read is OPeNDAP, handled inside the netCDF C library by
netCDF4.Dataset(url) — it is a binary subsetting protocol, not an HTTP file GET, so it
CANNOT be routed through pipeline.http.request without changing what protocol is spoken.
mop.py is left exactly as it is. What this script adds instead is a local retry/backoff
around pull_mop_window (_pull_with_retry below), which recovers a transient THREDDS
failure but gives none of pipeline.http's other properties (shared session, User-Agent,
CA bundle). That mop.py bypasses the retry helper entirely is a real defect; it is a
separate branch's job, not this one's.
"""
from __future__ import annotations

import argparse
import datetime
import json
import math
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)   # mop_blacks_slice / mop_ca_rollout / mop_handful_slice
sys.path.insert(0, ROOT)   # pipeline.*

# REUSED, NOT REIMPLEMENTED — the match, the thresholds and the MOP pull are the ones
# already validated by the rollout; a second copy could drift from them silently.
from mop_blacks_slice import CACHE as MOP_CACHE_PATH            # noqa: E402
from mop_blacks_slice import circ_offset, load_cache            # noqa: E402
from mop_ca_rollout import MATCH_SANITY_M, _match, _slug, ca_zone  # noqa: E402
from mop_handful_slice import SHORE_NORMAL_MAX_DELTA, pearson   # noqa: E402
from pipeline.forecast.mop import (                             # noqa: E402
    _iso_to_epoch, _norm_epoch, nowcast_url, pull_mop_window,
)
from pipeline.interpret import M_TO_FT                          # noqa: E402

ROSTER = os.path.join(ROOT, "pipeline", "spots_enriched.json")
OUT = os.path.join(HERE, "mop_face_validation_out.json")

DEFAULT_DAYS_BACK = 14

# Below this the denominator is noise, not a measurement: a 0.05 m MOP Hs turns any face
# into a four-figure ratio. Hours below the floor are NOT silently dropped — they are
# counted and summarised separately (see "blocked_hours" in the output), because
# "we published N ft in an hour MOP says the swell never arrived" is itself the finding
# the gated-vs-ungated comparison exists to surface.
MOP_HS_FLOOR_M = 0.10

# Transient-failure retry around the OPeNDAP read. Mirrors the repo's 4-attempt
# exponential backoff (pipeline/http.py _RETRY) in shape only — see the module docstring
# for why the real helper cannot be used here.
_RETRY_ATTEMPTS = 4
_RETRY_BACKOFF_S = (2, 4, 8)

# PostgREST caps a select at 1000 rows, so paging inside a chunk is load-bearing.
FORECAST_PAGE_ROWS = 1000

# How many spot ids one forecasts statement may ask about.
#
# WHY THIS IS NEEDED. The un-chunked query timed out (PostgREST 57014, "canceling
# statement due to statement timeout") at 40, 80, 120 and 153 ids, and at every one of
# them it failed on the FIRST page — offset 0, before a single row came back. A row-count
# ceiling would have failed on a LATER page, so this is a plan problem, not a volume one.
#
# THE MECHANISM. The statement is
#     ... WHERE spot_id IN (...) AND source='nwps' AND valid_time BETWEEN t0 AND t1
#     ORDER BY id LIMIT 1000 OFFSET 0
# and `forecasts` carries a PK index on id plus idx_forecasts_spot_time(spot_id,
# valid_time) (001_initial_schema.sql:102). ORDER BY ... LIMIT gives the planner two
# shapes: (a) range-scan idx_forecasts_spot_time, sort the matches by id, take 1000; or
# (b) walk the PK index in id order, filter each row, stop after 1000 matches — no sort
# at all, and it can "stop early", so the planner likes it when it believes matches are
# common. Selectivity is what picks between them: 8 of ~648 spots looks like ~1% of the
# table, so (b) looks like a long walk and (a) wins; 40 looks like ~6%, so (b) looks
# cheap and wins. But the estimate is wrong in a way the planner cannot see: `id` is
# assigned in insertion order and the filter selects the LAST 14 days, so every matching
# row sits at the high end of `id`. Plan (b) walks the table from id=1 through months of
# older rows and reaches nothing before the timeout fires.
#
# The two committed readers of this table corroborate it: daily_report.py:206 and
# revalidate.py:172 both scan ALL 648 spots with no IN list at all and never time out —
# and both ORDER BY valid_time, which idx_forecasts_valid_time serves directly, so
# neither ever offers the planner the id-walk.
#
# WHY 8 AND NOT A ROUNDER NUMBER. 8 is the largest value with direct positive evidence:
# --limit 8 fetched 2,321 rows across three pages cleanly. 40 is the smallest with direct
# negative evidence. The crossover is somewhere in (8, 40] and has not been measured, and
# because it is a plan flip it is a CLIFF rather than a gradient — a value picked for
# tidiness could sit one id past it and fail exactly as before. So this takes the proven
# value rather than guessing at the boundary. The cost is small: 137 spots is 18 chunks,
# each roughly three pages. --chunk-size exists to probe for the real boundary later; if
# a larger value proves out, change the number here and record the evidence.
FORECAST_CHUNK_SPOTS = 8


# --------------------------------------------------------------------------- #
# Pure — no network, no database. Everything here is pinned by --selftest.     #
# --------------------------------------------------------------------------- #

def is_population(spot: dict) -> bool:
    """Is this spot in the valid validation population?

    California, fed by the NWPS path, and carrying NO mop_* field. The mop_* test is
    the load-bearing half: a spot with mop_point_id has its face computed from MOP by
    apply_mop_overrides, so including it would validate MOP against itself. Testing for
    the FIELDS rather than only for swell_window_source means a spot that was ever
    MOP-associated stays excluded even if its tier is later changed by hand.
    """
    if spot.get("region_hint") != "California":
        return False
    if spot.get("swell_window_source") != "nwps":
        return False
    return not any(k.startswith("mop_") for k in spot)


def chunk_ids(ids, size: int) -> list[list]:
    """Split *ids* into consecutive chunks of at most *size*.

    Order-preserving, and every id appears in exactly one chunk — the two properties the
    reassembly in fetch_forecasts depends on. An exact multiple yields no empty trailing
    chunk; an empty input yields no chunks at all (so the caller's loop simply does not
    run, rather than issuing a query with an empty IN list).
    """
    if size < 1:
        raise ValueError(f"chunk size must be >= 1, got {size}")
    seq = list(ids)
    return [seq[i:i + size] for i in range(0, len(seq), size)]


def ratio(numer_ft, mop_hs_m, floor_m: float = MOP_HS_FLOOR_M):
    """our_feet / (MOP waveHs in feet), or None when it is not a measurement.

    None means "no usable ratio here", and there are exactly two ways to get it: a
    missing input, or a MOP height at or below *floor_m* where the quotient is dominated
    by the denominator's noise. A zero numerator is a real measurement and returns 0.0.
    """
    if numer_ft is None or mop_hs_m is None:
        return None
    if not mop_hs_m > floor_m:
        return None
    return float(numer_ft) / (float(mop_hs_m) * M_TO_FT)


def hour_of(value) -> int | None:
    """UTC hour index — floor(epoch_seconds / 3600) — from an ISO string or epoch.

    The join key. forecasts.valid_time is an ISO timestamp on the hour and MOP's
    waveTime is epoch seconds, so both sides pass through here and neither side gets to
    define the bucket on its own.
    """
    if value is None:
        return None
    t = _norm_epoch(value) if isinstance(value, (int, float)) else _norm_epoch(_iso_to_epoch(value))
    if t is None:
        return None
    return int(t // 3600)


def join_on_hour(mop_by_hour: dict, rows: list) -> list[dict]:
    """Pairs for the hours BOTH sides cover. EXACT hour match, no slop.

    apply_mop_overrides uses a ±1 h fallback (mop.py:229) because it is trying to rate as
    many hours as it can. A validation wants the opposite: an exact match, so that a
    systematic one-hour offset shows up as a collapsed join rate instead of being
    silently absorbed into the numbers. The join rate is reported per spot.
    """
    out = []
    for r in rows:
        h = hour_of(r.get("valid_time"))
        if h is None or h not in mop_by_hour:
            continue
        mop_hs = mop_by_hour[h]
        if mop_hs is None:
            continue
        out.append({
            "hour": h,
            "valid_time": r.get("valid_time"),
            "mop_hs_m": float(mop_hs),
            "face_ft": r.get("face_ft"),
            "effective_size_ft": r.get("effective_size_ft"),
            "swell_source": r.get("swell_source"),
        })
    return out


def _stats(values: list) -> dict:
    """n / mean / median / p10 / p90 / min / max for a list of floats. Empty -> n 0."""
    vals = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "p10": None, "p90": None,
                "min": None, "max": None}
    s = sorted(vals)

    def pct(p: float) -> float:
        # Nearest-rank on the sorted sample. Stated rather than left to a library so the
        # selftest can assert an exact value against a hand-computed one.
        k = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
        return s[k]

    return {
        "n": len(s), "mean": statistics.fmean(s), "median": statistics.median(s),
        "p10": pct(0.10), "p90": pct(0.90), "min": s[0], "max": s[-1],
    }


def height_agreement(pairs: list[dict]) -> dict:
    """bias / MAE / Pearson r of our face_ft against MOP waveHs, both in FEET.

    Reported because it is what was asked for, and labelled in the output as what it is:
    the two are NOT the same quantity (see the module docstring), so a non-zero bias here
    is expected and is the amplification being measured — not an error. The CORRELATION
    is the part that carries information about tracking, independent of the scale factor.
    """
    ours, mop = [], []
    for p in pairs:
        if p["face_ft"] is None:
            continue
        ours.append(float(p["face_ft"]))
        mop.append(float(p["mop_hs_m"]) * M_TO_FT)
    if not ours:
        return {"n": 0, "bias_ft": None, "mae_ft": None, "r": None}
    diffs = [a - b for a, b in zip(ours, mop)]
    return {
        "n": len(ours),
        "bias_ft": statistics.fmean(diffs),
        "mae_ft": statistics.fmean([abs(d) for d in diffs]),
        "r": pearson(ours, mop) if len(ours) >= 3 else None,
    }


def shore_normal_delta(orientation_deg, shore_normal):
    """|orientation - metaShoreNormal| in [0,180], or None when either is absent."""
    if orientation_deg is None or shore_normal is None:
        return None
    return abs(circ_offset(float(orientation_deg), float(shore_normal)))


def match_verdict(dist_m, sn_delta):
    """(accepted, reason). The REFERENCING gate, not the adoption gate.

    Two checks only: MATCH_SANITY_M (is there a MOP point near this spot at all) and
    SHORE_NORMAL_MAX_DELTA (does that point face the same stretch of coast as the break).
    The buoy cross-check from mop_handful_slice.verdict is DELIBERATELY NOT APPLIED: it
    exists to license PUBLISHING from MOP, and every spot it rejects is one whose face is
    still NWPS-derived — i.e. exactly the population this study needs.

    MATCH_FALLBACK_M (1200 m) is likewise not applied. It is a publishing threshold; a
    point 1.5 km along the same contour is still a fair reference. The distance is
    recorded per spot instead, so it can be used as a filter at analysis time.
    """
    if dist_m > MATCH_SANITY_M:
        return False, f"nearest MOP point {dist_m / 1000:.1f} km away (> {MATCH_SANITY_M / 1000:.0f} km)"
    if sn_delta is None:
        return False, "no orientation_deg or no metaShoreNormal to compare"
    if sn_delta > SHORE_NORMAL_MAX_DELTA:
        return False, f"shore-normal delta {sn_delta:.0f} deg (> {SHORE_NORMAL_MAX_DELTA:.0f})"
    return True, "ok"


# --------------------------------------------------------------------------- #
# Impure — MOP over OPeNDAP, Supabase over PostgREST.                          #
# --------------------------------------------------------------------------- #

def _pull_with_retry(url, t0, t1, attempts=_RETRY_ATTEMPTS):
    """pull_mop_window with exponential backoff. See the module docstring for why this
    is not pipeline.http. Returns rows, or raises the final exception."""
    last = None
    for i in range(attempts):
        try:
            return pull_mop_window(url, t0, t1)
        except Exception as e:  # noqa: BLE001 — OPeNDAP surfaces OSError/RuntimeError/KeyError
            last = e
            if i < len(_RETRY_BACKOFF_S):
                time.sleep(_RETRY_BACKOFF_S[i])
    raise last


def fetch_mop_by_hour(url, t0, t1):
    """{hour_index: waveHs_m} for one MOP point over [t0, t1].

    Only waveHs is kept. Tp and Dp are read by pull_mop_window and discarded here on
    purpose: this study is about the HEIGHT transform, and carrying MOP's period would
    invite someone to feed it into face_ft and re-create the circularity the population
    filter exists to avoid.
    """
    rows = _pull_with_retry(url, t0, t1)
    out = {}
    for r in rows:
        h = hour_of(r.get("t"))
        if h is not None and r.get("hs") is not None:
            out[h] = float(r["hs"])
    return out


def _fetch_forecast_chunk(client, ids, t0_iso, t1_iso, page=FORECAST_PAGE_ROWS):
    """Every source='nwps' row in the window for ONE chunk of spot ids, paginated.

    The query is UNCHANGED from the un-chunked version: same select list, same filters,
    same `order("id")`, same 1000-row page. Chunking narrows only how many spots a single
    statement asks about — it cannot alter what is fetched.

    `order("id")` stays because it is what makes offset paging a TOTAL order: `id` is the
    primary key, so no row can be skipped or repeated across a page boundary. Ordering by
    `valid_time` instead would dodge the timeout (see FORECAST_CHUNK_SPOTS) but ties
    across spots at the same hour make its page boundaries non-deterministic.

    *page* is a parameter only so --selftest can exercise multi-page paging on a handful
    of rows; production always uses FORECAST_PAGE_ROWS.
    """
    out, frm = [], 0
    while True:
        resp = (
            client.table("forecasts")
            .select("spot_id, valid_time, face_ft, effective_size_ft, swell_source")
            .in_("spot_id", list(ids))
            .eq("source", "nwps")
            .gte("valid_time", t0_iso)
            .lte("valid_time", t1_iso)
            .order("id")
            .range(frm, frm + page - 1)
            .execute()
        )
        rows = resp.data or []
        if not rows:
            break
        out.extend(rows)
        if len(rows) < page:
            break
        frm += page
    return out


def fetch_forecasts(client, spot_ids, t0_iso, t1_iso,
                    chunk_size=FORECAST_CHUNK_SPOTS, page=FORECAST_PAGE_ROWS):
    """{spot_id: [rows]} of source='nwps' forecasts in the window.

    Chunked over spot ids — see FORECAST_CHUNK_SPOTS for why, and why 8. Each spot's ids
    land in exactly one chunk and each chunk is still ordered by `id`, so a spot's rows
    come back in the same order a single un-chunked fetch would have produced them; the
    reassembled dict is identical, which --selftest pins against a literal.
    """
    by_spot = {}
    chunks = chunk_ids(spot_ids, chunk_size)
    total = 0
    for i, chunk in enumerate(chunks, 1):
        rows = _fetch_forecast_chunk(client, chunk, t0_iso, t1_iso, page=page)
        for r in rows:
            by_spot.setdefault(r["spot_id"], []).append(r)
        total += len(rows)
        print(f"    chunk {i:3d}/{len(chunks)}  {len(chunk):2d} spots  "
              f"+{len(rows):5d} rows  ({total} total)", flush=True)
    return by_spot


# --------------------------------------------------------------------------- #
# Orchestration                                                                #
# --------------------------------------------------------------------------- #

def run(days_back=DEFAULT_DAYS_BACK, limit=None, out_path=OUT,
        chunk_size=FORECAST_CHUNK_SPOTS):
    # Checked here rather than left to load_cache, whose miss message interpolates
    # sys.argv[0] and so would tell you to run `mop_face_validation.py build-cache` —
    # a subcommand this script does not have. The cache belongs to mop_blacks_slice.
    if not os.path.exists(MOP_CACHE_PATH):
        print(f"no MOP point cache at {MOP_CACHE_PATH}\n"
              f"  build it once (needs open CDIP THREDDS egress, ~11.7k points, resumable):\n"
              f"      python3 scripts/mop_blacks_slice.py build-cache", file=sys.stderr)
        return 2
    cache = load_cache()
    if cache is None:
        return 2
    coord_pts = sum(1 for m in cache.values() if m.get("lat") is not None)
    print(f"MOP cache: {len(cache)} points, {coord_pts} with coordinates", flush=True)

    roster = json.load(open(ROSTER))
    pop = [s for s in roster if is_population(s)]
    print(f"population: {len(pop)} California spots on the NWPS tier with no mop_* fields "
          f"(of {sum(1 for s in roster if s.get('region_hint') == 'California')} CA spots)",
          flush=True)
    if limit:
        pop = pop[:limit]
        print(f"  --limit {limit}: using the first {len(pop)}", flush=True)
    if not pop:
        print("nothing to validate")
        return 2

    # --- match ---------------------------------------------------------------
    # _match scans every coord-resolved cache entry per spot, so this is
    # len(pop) x ~11.7k haversines in pure Python — tens of seconds, silent otherwise.
    print(f"matching {len(pop)} spots against {coord_pts} MOP points "
          f"(~{len(pop) * coord_pts / 1e6:.1f}M distance computations)…", flush=True)
    matched, rejected = [], []
    for n_done, s in enumerate(pop, 1):
        if n_done % 25 == 0:
            print(f"    …matched {n_done}/{len(pop)}", flush=True)
        pid, meta, dist = _match(cache, s["lat"], s["lng"])
        sn = meta.get("shore_normal")
        delta = shore_normal_delta(s.get("orientation_deg"), sn)
        ok, why = match_verdict(dist, delta)
        rec = {"name": s.get("name"), "slug": _slug(s.get("name")), "wfo": s.get("nwps_wfo"),
               "zone": ca_zone(s["lat"], s["lng"]), "mop_point": pid,
               "match_distance_m": round(dist, 1),
               "shore_normal": sn, "orientation_deg": s.get("orientation_deg"),
               "shore_normal_delta": round(delta, 1) if delta is not None else None}
        if ok:
            rec["url"] = nowcast_url(meta.get("url"))
            if not rec["url"]:
                rejected.append({**rec, "reason": "no MOP url in the cache entry"})
                continue
            matched.append((s, rec))
        else:
            rejected.append({**rec, "reason": why})
    print(f"matched: {len(matched)} accepted, {len(rejected)} rejected "
          f"(MATCH_SANITY_M={MATCH_SANITY_M / 1000:.0f} km, "
          f"SHORE_NORMAL_MAX_DELTA={SHORE_NORMAL_MAX_DELTA:.0f} deg; "
          f"buoy adoption gate NOT applied)", flush=True)
    for r in rejected:
        print(f"    reject  {r['slug']:28} {r['reason']}", flush=True)
    if not matched:
        return 2

    now = datetime.datetime.now(datetime.timezone.utc)
    t1 = now.timestamp()
    t0 = t1 - days_back * 86400
    t0_iso = datetime.datetime.fromtimestamp(t0, datetime.timezone.utc).isoformat()
    t1_iso = now.isoformat()
    print(f"window: {t0_iso} .. {t1_iso}  ({days_back} days back)", flush=True)

    # --- our forecasts -------------------------------------------------------
    print("reading our forecasts from Supabase "
          "(SUPABASE_URL + SUPABASE_SERVICE_KEY from the environment)…", flush=True)
    from pipeline.db_import import _spot_id_map, get_client
    client = get_client()
    name_to_id = _spot_id_map(client)
    ids, missing = [], []
    for s, _rec in matched:
        sid = name_to_id.get(s.get("name"))
        if sid is None:
            missing.append(s.get("name"))
        else:
            ids.append(sid)
    if missing:
        print(f"  {len(missing)} matched spot(s) have no spots-table row and are dropped: "
              f"{', '.join(missing[:6])}{' …' if len(missing) > 6 else ''}", flush=True)
    if not ids:
        print("no matched spot has a spots-table row — nothing to join against", file=sys.stderr)
        return 2
    print(f"  fetching in chunks of {chunk_size} spot ids "
          f"({len(chunk_ids(ids, chunk_size))} chunks) — see FORECAST_CHUNK_SPOTS", flush=True)
    fc = fetch_forecasts(client, ids, t0_iso, t1_iso, chunk_size=chunk_size)
    print(f"  forecasts: {sum(len(v) for v in fc.values())} rows across {len(fc)} spots",
          flush=True)

    # --- MOP, one OPeNDAP read per point -------------------------------------
    print(f"pulling MOP for {len(matched)} points — this is minutes, not seconds", flush=True)
    per_spot = []
    t_start = time.time()
    for i, (s, rec) in enumerate(matched, 1):
        sid = name_to_id.get(s.get("name"))
        label = f"[{i:3d}/{len(matched)}] {rec['slug']:28} {rec['mop_point']:>7}"
        if sid is None:
            print(f"{label}  skip (no spots-table row)", flush=True)
            continue
        try:
            mop = fetch_mop_by_hour(rec["url"], t0, t1)
        except Exception as e:  # noqa: BLE001 — one bad point must not end the sweep
            print(f"{label}  MOP ERROR {type(e).__name__}: {str(e)[:60]}", flush=True)
            per_spot.append({**rec, "error": f"{type(e).__name__}: {str(e)[:120]}"})
            continue
        rows = fc.get(sid) or []
        pairs = join_on_hour(mop, rows)

        face_ratios = [ratio(p["face_ft"], p["mop_hs_m"]) for p in pairs]
        eff_ratios = [ratio(p["effective_size_ft"], p["mop_hs_m"]) for p in pairs]
        face_ratios = [r for r in face_ratios if r is not None]
        eff_ratios = [r for r in eff_ratios if r is not None]

        # Hours MOP says the swell did not arrive, with what we published there.
        blocked = [p for p in pairs if p["mop_hs_m"] <= MOP_HS_FLOOR_M]
        blocked_face = _stats([p["face_ft"] for p in blocked])

        srcs = {}
        for p in pairs:
            srcs[p.get("swell_source") or "none"] = srcs.get(p.get("swell_source") or "none", 0) + 1

        entry = {
            **rec,
            "mop_hours": len(mop), "our_hours": len(rows), "joined_hours": len(pairs),
            "join_rate": round(len(pairs) / len(rows), 3) if rows else None,
            "face_ratio": _stats(face_ratios),
            "eff_ratio": _stats(eff_ratios),
            "height_agreement": height_agreement(pairs),
            "blocked_hours": {"n": len(blocked), "published_face_ft": blocked_face},
            "swell_source_counts": srcs,
        }
        per_spot.append(entry)
        fr = entry["face_ratio"]["median"]
        er = entry["eff_ratio"]["median"]
        joined = f"join {len(pairs):4d}/{len(rows):4d}"
        if fr is None or er is None:
            print(f"{label}  {joined}  (no usable ratio this window)", flush=True)
        else:
            print(f"{label}  {joined}  face x{fr:5.2f}  eff x{er:5.2f}  "
                  f"blocked {len(blocked):3d}", flush=True)
    print(f"  MOP sweep done in {time.time() - t_start:.0f}s", flush=True)

    # --- aggregate -----------------------------------------------------------
    good = [e for e in per_spot if "error" not in e and e["face_ratio"]["n"] > 0]

    def pooled(key):
        vals = []
        for e in good:
            st = e[key]
            if st["n"] and st["median"] is not None:
                vals.append(st["median"])
        return _stats(vals)

    by_wfo = {}
    for e in good:
        by_wfo.setdefault(e["wfo"] or "unknown", []).append(e)
    wfo_summary = {
        w: {"spots": len(es),
            "face_ratio_median_of_medians": _stats([x["face_ratio"]["median"] for x in es])["median"],
            "eff_ratio_median_of_medians": _stats([x["eff_ratio"]["median"] for x in es])["median"],
            "joined_hours": sum(x["joined_hours"] for x in es)}
        for w, es in sorted(by_wfo.items())
    }

    worst = sorted(good, key=lambda e: e["face_ratio"]["median"], reverse=True)[:12]
    blocked_worst = sorted(
        (e for e in good if e["blocked_hours"]["n"] > 0),
        key=lambda e: (e["blocked_hours"]["published_face_ft"]["median"] or 0), reverse=True)[:12]

    result = {
        "generated_at": now.isoformat(),
        "window": {"t0": t0_iso, "t1": t1_iso, "days_back": days_back},
        "what_this_measures": (
            "face_ft / (MOP waveHs x M_TO_FT). NOT height vs height: MOP publishes Hs at a "
            "~10 m depth contour and has no breaking height. The ratio measures the "
            "period_factor curve against an independent nearshore height. Our side is the "
            "NOWCAST-LEAD face: forecasts rows for past hours have drifted to the "
            "shortest-lead value, so this is not the face a reader saw at the time."
        ),
        "constants": {
            "M_TO_FT": M_TO_FT, "MOP_HS_FLOOR_M": MOP_HS_FLOOR_M,
            "MATCH_SANITY_M": MATCH_SANITY_M,
            "forecast_chunk_spots": chunk_size,
            "forecast_page_rows": FORECAST_PAGE_ROWS,
            "SHORE_NORMAL_MAX_DELTA": SHORE_NORMAL_MAX_DELTA,
            "buoy_adoption_gate_applied": False,
        },
        "population": {"selected": len(pop), "matched": len(matched),
                       "rejected": rejected, "with_results": len(good)},
        "roster": {
            "face_ratio_median_of_spot_medians": pooled("face_ratio"),
            "eff_ratio_median_of_spot_medians": pooled("eff_ratio"),
            "joined_hours_total": sum(e["joined_hours"] for e in good),
            "blocked_hours_total": sum(e["blocked_hours"]["n"] for e in good),
        },
        "by_wfo": wfo_summary,
        "worst_face_ratio": [{"name": e["name"], "wfo": e["wfo"],
                              "face_ratio_median": e["face_ratio"]["median"],
                              "eff_ratio_median": e["eff_ratio"]["median"],
                              "joined_hours": e["joined_hours"]} for e in worst],
        "worst_blocked_hours": [{"name": e["name"], "blocked_hours": e["blocked_hours"]["n"],
                                 "median_published_face_ft":
                                     e["blocked_hours"]["published_face_ft"]["median"]}
                                for e in blocked_worst],
        "by_spot": per_spot,
    }
    with open(out_path, "w") as f:
        json.dump(result, f, indent=2)

    # --- print ---------------------------------------------------------------
    fr, er = result["roster"]["face_ratio_median_of_spot_medians"], \
        result["roster"]["eff_ratio_median_of_spot_medians"]
    print("\n" + "=" * 78)
    print(f"ROSTER — {len(good)} spots, {result['roster']['joined_hours_total']} joined spot-hours")
    for label, st in (("face_ft", fr), ("effective_size_ft", er)):
        head = f"  {label} / (MOP Hs x {M_TO_FT})"
        if st["n"]:
            print(f"{head:44} median {st['median']:.2f}  "
                  f"p10 {st['p10']:.2f}  p90 {st['p90']:.2f}  (n={st['n']} spots)")
        else:
            print(f"{head:44} no data")
    print(f"  hours MOP says the swell did not arrive: {result['roster']['blocked_hours_total']}")
    print("\nBY WFO:")
    for w, v in wfo_summary.items():
        print(f"  {w:8} {v['spots']:3d} spots  face x{v['face_ratio_median_of_medians']:.2f}"
              f"  eff x{v['eff_ratio_median_of_medians']:.2f}  ({v['joined_hours']} hours)")
    print("\nWORST FACE RATIO (most inflated vs MOP):")
    for e in worst:
        print(f"  {e['name'][:34]:36} face x{e['face_ratio']['median']:5.2f}"
              f"  eff x{e['eff_ratio']['median']:5.2f}  ({e['joined_hours']} hrs, {e['wfo']})")
    if blocked_worst:
        print("\nPUBLISHED THE MOST SIZE IN HOURS MOP SAYS THE SWELL DID NOT ARRIVE:")
        for e in blocked_worst:
            print(f"  {e['name'][:34]:36} {e['blocked_hours']['n']:4d} hrs, "
                  f"median published face "
                  f"{e['blocked_hours']['published_face_ft']['median']:.2f} ft")
    print("=" * 78)
    print("READ THIS AS: a measurement of period_factor against an independent nearshore")
    print("height — NOT as face-vs-face. MOP has no breaking height. If face x and eff x")
    print("are both inflated, the inflation is in period_factor; if face x is inflated and")
    print("eff x is near 1, it is the missing directional gate and effective_size_ft is")
    print("already the fix.")
    print(f"\nwrote {out_path}")
    return 0


# --------------------------------------------------------------------------- #
# Selftest — pure logic only. No network, no database.                         #
# --------------------------------------------------------------------------- #

def run_selftest():
    ok = True

    def check(n, c):
        nonlocal ok
        ok = ok and c
        print(f"  {'PASS' if c else 'FAIL'}  {n}")

    # --- population filter -------------------------------------------------- #
    ca_nwps = {"region_hint": "California", "swell_window_source": "nwps", "name": "A"}
    check("CA + nwps + no mop_* -> in population", is_population(ca_nwps) is True)
    check("a mop_point_id excludes it (face computed FROM MOP)",
          is_population({**ca_nwps, "mop_point_id": "D0515"}) is False)
    check("any other mop_* field excludes it too",
          is_population({**ca_nwps, "mop_shore_normal": 270}) is False)
    check("cdip_mop tier excluded", is_population({**ca_nwps, "swell_window_source": "cdip_mop"}) is False)
    check("non-California excluded",
          is_population({**ca_nwps, "region_hint": "Hawaii"}) is False)
    check("orientation_derived CA spot excluded (face is not NWPS-derived)",
          is_population({**ca_nwps, "swell_window_source": "orientation_derived"}) is False)

    # The committed roster must give the number the brief expects.
    roster = json.load(open(ROSTER))
    n_pop = sum(1 for s in roster if is_population(s))
    n_ca = sum(1 for s in roster if s.get("region_hint") == "California")
    n_mop = sum(1 for s in roster if s.get("swell_window_source") == "cdip_mop")
    check(f"committed roster: 201 California spots ({n_ca})", n_ca == 201)
    check(f"committed roster: 48 on the cdip_mop tier ({n_mop})", n_mop == 48)
    check(f"committed roster: population is 153 ({n_pop})", n_pop == 153)

    # --- ratio arithmetic ---------------------------------------------------- #
    # M_TO_FT = 3.281. A 1.00 m MOP Hs is 3.281 ft, so a published 3.281 ft face is
    # exactly ratio 1.0 — hand-computed, not read back from the function.
    check("1.000 m MOP Hs, 3.281 ft face -> ratio 1.0",
          abs(ratio(3.281, 1.0) - 1.0) < 1e-12)
    # 2.00 m -> 6.562 ft. A published 9.843 ft face is 9.843 / 6.562 = 1.5 exactly.
    check("2.000 m MOP Hs, 9.843 ft face -> ratio 1.5",
          abs(ratio(9.843, 2.0) - 1.5) < 1e-12)
    # 0.50 m -> 1.6405 ft. Face 3.281 ft -> 3.281 / 1.6405 = 2.0.
    check("0.500 m MOP Hs, 3.281 ft face -> ratio 2.0",
          abs(ratio(3.281, 0.5) - 2.0) < 1e-12)
    check("a zero face is a measurement, not an absence", ratio(0.0, 1.0) == 0.0)
    check("missing face -> None", ratio(None, 1.0) is None)
    check("missing MOP height -> None", ratio(3.281, None) is None)
    check("MOP Hs at the floor is rejected (denominator is noise)",
          ratio(3.281, MOP_HS_FLOOR_M) is None)
    check("MOP Hs just above the floor is accepted",
          ratio(3.281, MOP_HS_FLOOR_M + 1e-9) is not None)
    check("MOP Hs of exactly 0 -> None, never a division by zero", ratio(3.281, 0.0) is None)

    # --- the hour join ------------------------------------------------------- #
    # 2026-08-31T14:00:00Z = epoch 1788184800; 1788184800 / 3600 = 496718 exactly.
    check("ISO on the hour -> hour index 496718",
          hour_of("2026-08-31T14:00:00Z") == 496718)
    check("mid-hour ISO floors to the same index",
          hour_of("2026-08-31T14:59:59Z") == 496718)
    check("epoch seconds land on the same index", hour_of(1788184800.0) == 496718)
    check("the next hour is the next index", hour_of("2026-08-31T15:00:00Z") == 496719)
    check("a fill value is rejected, not bucketed", hour_of(9.969e36) is None)
    check("unparseable -> None", hour_of("not a time") is None)

    mop = {496718: 1.0, 496719: 2.0}
    rows = [
        {"valid_time": "2026-08-31T14:00:00Z", "face_ft": 3.281, "effective_size_ft": 1.6405},
        {"valid_time": "2026-08-31T15:00:00Z", "face_ft": 9.843, "effective_size_ft": 6.562},
        {"valid_time": "2026-08-31T16:00:00Z", "face_ft": 5.0, "effective_size_ft": 4.0},  # no MOP
    ]
    pairs = join_on_hour(mop, rows)
    check(f"join keeps only the hours BOTH sides have ({len(pairs)})", len(pairs) == 2)
    check("the unmatched hour is dropped, not defaulted",
          all(p["hour"] in (496718, 496719) for p in pairs))
    check("the joined MOP height is the one for THAT hour",
          [p["mop_hs_m"] for p in pairs] == [1.0, 2.0])
    # 3.281/(1.0*3.281)=1.0 ; 9.843/(2.0*3.281)=1.5  — both hand-computed above.
    check("face ratios across the join are 1.0 and 1.5",
          [round(ratio(p["face_ft"], p["mop_hs_m"]), 6) for p in pairs] == [1.0, 1.5])
    # 1.6405/(1.0*3.281)=0.5 ; 6.562/(2.0*3.281)=1.0
    check("eff ratios across the join are 0.5 and 1.0",
          [round(ratio(p["effective_size_ft"], p["mop_hs_m"]), 6) for p in pairs] == [0.5, 1.0])
    check("an hour MOP covers but we do not is not invented",
          join_on_hour({496718: 1.0}, []) == [])

    # --- summary stats ------------------------------------------------------- #
    # Sorted [1,2,3,4,5]: median 3, p10 -> index round(0.1*4)=0 -> 1, p90 -> round(0.9*4)=4 -> 5.
    st = _stats([3.0, 1.0, 5.0, 2.0, 4.0])
    check(f"stats n ({st['n']})", st["n"] == 5)
    check(f"stats mean 3.0 ({st['mean']})", st["mean"] == 3.0)
    check(f"stats median 3.0 ({st['median']})", st["median"] == 3.0)
    check(f"stats p10 1.0 ({st['p10']})", st["p10"] == 1.0)
    check(f"stats p90 5.0 ({st['p90']})", st["p90"] == 5.0)
    check("stats on an empty list -> n 0, no crash", _stats([])["n"] == 0)
    check("None entries are dropped, not counted", _stats([1.0, None, 3.0])["n"] == 2)

    # --- height agreement ---------------------------------------------------- #
    # face 3.281 vs MOP 1.0 m = 3.281 ft -> diff 0 ; face 9.843 vs 2.0 m = 6.562 ft -> diff 3.281.
    # bias = (0 + 3.281)/2 = 1.6405 ; MAE identical because both diffs are >= 0.
    ha = height_agreement([
        {"face_ft": 3.281, "mop_hs_m": 1.0},
        {"face_ft": 9.843, "mop_hs_m": 2.0},
    ])
    check(f"height bias 1.6405 ft ({ha['bias_ft']})", abs(ha["bias_ft"] - 1.6405) < 1e-9)
    check(f"height MAE 1.6405 ft ({ha['mae_ft']})", abs(ha["mae_ft"] - 1.6405) < 1e-9)
    check("r is None below 3 samples (not a fake 1.0)", ha["r"] is None)

    # --- the referencing gate ------------------------------------------------ #
    check("close match, aligned shore normal -> accepted", match_verdict(600.0, 10.0)[0] is True)
    check("a 5 km match is still accepted (MATCH_FALLBACK_M is a PUBLISHING gate)",
          match_verdict(5000.0, 10.0)[0] is True)
    check("beyond MATCH_SANITY_M -> rejected",
          match_verdict(MATCH_SANITY_M + 1.0, 10.0)[0] is False)
    check("shore-normal delta beyond the threshold -> rejected",
          match_verdict(600.0, SHORE_NORMAL_MAX_DELTA + 0.1)[0] is False)
    check("exactly at the shore-normal threshold -> accepted (inclusive)",
          match_verdict(600.0, SHORE_NORMAL_MAX_DELTA)[0] is True)
    check("no shore normal to compare -> rejected, not assumed", match_verdict(600.0, None)[0] is False)
    # 350 vs 10 is 20 deg apart across 0/360, not 340.
    check("shore-normal delta wraps across 0/360", abs(shore_normal_delta(350, 10) - 20.0) < 1e-9)
    check("shore-normal delta is unsigned", abs(shore_normal_delta(10, 350) - 20.0) < 1e-9)
    check("absent orientation -> None", shore_normal_delta(None, 270) is None)

    # --- chunking ------------------------------------------------------------ #
    # The split itself. Expected chunks written out, not produced by the function.
    check("20 ids at size 8 split into exactly the listed chunks",
          chunk_ids(list(range(1, 21)), 8) == [[1, 2, 3, 4, 5, 6, 7, 8],
                                               [9, 10, 11, 12, 13, 14, 15, 16],
                                               [17, 18, 19, 20]])
    # 137 accepted spots at 8 per statement: 17 full chunks + a remainder of 1 = 18.
    check("137 ids at size 8 -> 18 chunks", len(chunk_ids(range(137), 8)) == 18)
    check("the last of those 18 holds the single remainder",
          len(chunk_ids(range(137), 8)[-1]) == 1)
    # An exact multiple must not produce a trailing empty chunk.
    check("16 ids at size 8 -> 2 chunks, none empty",
          [len(c) for c in chunk_ids(range(16), 8)] == [8, 8])
    check("fewer ids than the chunk size -> one chunk", chunk_ids([4, 7], 8) == [[4, 7]])
    check("no ids -> no chunks at all (never an empty IN list)", chunk_ids([], 8) == [])
    check("size 1 -> one chunk per id", chunk_ids([5, 6, 7], 1) == [[5], [6], [7]])
    try:
        chunk_ids([1, 2], 0)
        check("a zero chunk size raises rather than looping forever", False)
    except ValueError:
        check("a zero chunk size raises rather than looping forever", True)

    # Every id exactly once, across an awkward split. 137 ids, 18 chunks.
    flat = [i for c in chunk_ids(range(137), 8) for i in c]
    check("chunking loses no id", len(flat) == 137)
    check("chunking duplicates no id", len(set(flat)) == 137)
    check("chunking preserves order", flat == list(range(137)))

    # --- reassembly is identical to a single fetch ---------------------------- #
    # A stand-in PostgREST builder: honours in_() and range(), orders by id, and ignores
    # the filters this test does not vary. Rows are hand-written so the expected result
    # below can be too.
    class _Resp:
        def __init__(self, data):
            self.data = data

    class _FakeQuery:
        def __init__(self, rows):
            self._rows, self._ids, self._frm, self._to = rows, None, 0, None
            self.statements = 0

        def select(self, *a, **k):
            return self

        def eq(self, *a, **k):
            return self

        def gte(self, *a, **k):
            return self

        def lte(self, *a, **k):
            return self

        def order(self, *a, **k):
            return self

        def in_(self, _col, vals):
            self._ids = set(vals)
            return self

        def range(self, frm, to):
            self._frm, self._to = frm, to
            return self

        def execute(self):
            sel = sorted((r for r in self._rows
                          if self._ids is None or r["spot_id"] in self._ids),
                         key=lambda r: r["id"])
            return _Resp(sel[self._frm:self._to + 1])

    class _FakeClient:
        def __init__(self, rows):
            self._rows, self.statements = rows, 0

        def table(self, _name):
            self.statements += 1
            return _FakeQuery(self._rows)

    # Three spots, ids interleaved so an id-ordered fetch does NOT group by spot.
    rows = [
        {"id": 1, "spot_id": 10, "valid_time": "T1", "face_ft": 1.0,
         "effective_size_ft": 0.5, "swell_source": "ww3"},
        {"id": 2, "spot_id": 20, "valid_time": "T1", "face_ft": 2.0,
         "effective_size_ft": 1.0, "swell_source": "ww3"},
        {"id": 3, "spot_id": 10, "valid_time": "T2", "face_ft": 3.0,
         "effective_size_ft": 1.5, "swell_source": "ww3"},
        {"id": 4, "spot_id": 30, "valid_time": "T1", "face_ft": 4.0,
         "effective_size_ft": 2.0, "swell_source": "ww3"},
        {"id": 5, "spot_id": 20, "valid_time": "T2", "face_ft": 5.0,
         "effective_size_ft": 2.5, "swell_source": "ww3"},
    ]
    # What the fetch MUST return, whatever the chunk size: each spot's rows in id order.
    expected = {
        10: [rows[0], rows[2]],
        20: [rows[1], rows[4]],
        30: [rows[3]],
    }
    got_1 = fetch_forecasts(_FakeClient(rows), [10, 20, 30], "t0", "t1", chunk_size=1, page=1000)
    got_2 = fetch_forecasts(_FakeClient(rows), [10, 20, 30], "t0", "t1", chunk_size=2, page=1000)
    got_all = fetch_forecasts(_FakeClient(rows), [10, 20, 30], "t0", "t1", chunk_size=99, page=1000)
    check("chunk size 1 reassembles to the expected dict", got_1 == expected)
    check("chunk size 2 reassembles to the expected dict", got_2 == expected)
    check("one chunk (un-chunked) reassembles to the expected dict", got_all == expected)
    check("every chunk size agrees with every other", got_1 == got_2 == got_all)
    check("a spot absent from the data is absent from the result, not empty",
          30 in got_2 and 40 not in got_2)

    # Paging INSIDE a chunk still applies: 5 rows for one spot at page=2 is 3 statements.
    solo = [{"id": i, "spot_id": 10, "valid_time": f"T{i}", "face_ft": float(i),
             "effective_size_ft": float(i) / 2, "swell_source": "ww3"} for i in range(1, 6)]
    fc = _FakeClient(solo)
    paged = fetch_forecasts(fc, [10], "t0", "t1", chunk_size=8, page=2)
    check("paging inside a chunk returns every row", len(paged[10]) == 5)
    check("paged rows stay in id order", [r["id"] for r in paged[10]] == [1, 2, 3, 4, 5])
    # 5 rows at 2/page: pages of 2, 2, 1 — the short third page ends the loop.
    check(f"5 rows at page 2 took 3 statements ({fc.statements})", fc.statements == 3)
    # An exact multiple needs the extra empty page to learn it is done: 4 rows -> 2, 2, 0.
    fc4 = _FakeClient(solo[:4])
    fetch_forecasts(fc4, [10], "t0", "t1", chunk_size=8, page=2)
    check(f"4 rows at page 2 took 3 statements, the last empty ({fc4.statements})",
          fc4.statements == 3)

    check(f"the shipped chunk size is the proven 8 ({FORECAST_CHUNK_SPOTS})",
          FORECAST_CHUNK_SPOTS == 8)
    check(f"the shipped page size is PostgREST's cap ({FORECAST_PAGE_ROWS})",
          FORECAST_PAGE_ROWS == 1000)

    print("\nNOT COVERED HERE, deliberately: the MOP fetch. fetch_mop_by_hour and")
    print("_pull_with_retry speak OPeNDAP to CDIP THREDDS; there is no way to exercise them")
    print("without the network, and a mock of netCDF4.Dataset would prove only that the mock")
    print("was written to match the code. Their first real exercise is the run itself.")
    print("fetch_forecasts IS covered above, but only its CHUNKING AND REASSEMBLY, against a")
    print("stand-in PostgREST builder. What no offline test can reach is the thing that")
    print("actually broke — the planner\'s choice on the real table — so whether 8 is small")
    print("enough is settled by the run, not by these assertions.")
    print("\nself-test:", "ALL PASS" if ok else "FAILURES")
    return 0 if ok else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--days-back", type=int, default=DEFAULT_DAYS_BACK,
                    help=f"window length in days (default {DEFAULT_DAYS_BACK})")
    ap.add_argument("--limit", type=int, default=None, help="only the first N spots (smoke test)")
    ap.add_argument("--chunk-size", type=int, default=FORECAST_CHUNK_SPOTS,
                    help=f"spot ids per forecasts statement (default {FORECAST_CHUNK_SPOTS}; "
                         "raise it to probe for the real planner cliff, which is somewhere "
                         "in (8, 40] and unmeasured)")
    ap.add_argument("--out", default=OUT, help=f"results JSON (default {OUT})")
    ap.add_argument("--selftest", action="store_true", help="offline logic proof; no network, no DB")
    a = ap.parse_args(argv)
    if a.selftest:
        return run_selftest()
    return run(days_back=a.days_back, limit=a.limit, out_path=a.out,
               chunk_size=a.chunk_size)


if __name__ == "__main__":
    raise SystemExit(main())
