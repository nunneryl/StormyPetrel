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
from mop_handful_slice import (                                 # noqa: E402
    MATCH_FALLBACK_M, SHORE_NORMAL_MAX_DELTA, pearson,
)
from pipeline.forecast.mop import (                             # noqa: E402
    _iso_to_epoch, _norm_epoch, nowcast_url, pull_mop_window,
)
from pipeline.interpret import M_TO_FT                          # noqa: E402

ROSTER = os.path.join(ROOT, "pipeline", "spots_enriched.json")
OUT = os.path.join(HERE, "mop_face_validation_out.json")

# --------------------------------------------------------------------------- #
# THE SHORE-NORMAL GATE IS LOOSER HERE THAN ON THE ADOPTION PATH, AND THAT IS  #
# DELIBERATE. DO NOT REUNIFY THEM.                                            #
#                                                                             #
# THE DISTINCTION IS SCALAR VERSUS DIRECTIONAL, and it is visible in what each #
# path reads out of MOP:                                                       #
#                                                                             #
#   mop_ca_rollout (ADOPTION, keeps SHORE_NORMAL_MAX_DELTA = 35) reads         #
#   waveHs, waveTp, waveDp AND waveEnergyDensity — the directional spectrum —  #
#   and resolves waveDp against metaShoreNormal through interpret's            #
#   directional_gain. A normal wrong by d makes every directional term wrong   #
#   by d, first-order, in a PUBLISHED rating. 35 deg is a real tolerance there #
#   and must stay.                                                             #
#                                                                             #
#   this harness (REFERENCING) keeps waveHs and nothing else. fetch_mop_by_hour#
#   discards Tp and Dp on purpose, and face_ratio is one scalar divided by     #
#   another. Nothing here resolves a direction against a shore normal, so the  #
#   35-deg gate was protecting against a risk this path does not carry.        #
#                                                                             #
# WHY 90 AND NOT A TIGHTER NUMBER. A CONSTANT exposure difference between the  #
# break and the matched point is exactly what the face factor absorbs: the     #
# factor is a per-spot divisor fitted as the MEDIAN ratio over the window, so  #
# a point that systematically runs 20% smaller than the break puts that 20%    #
# into the factor and the correction still lands. What a bad pairing costs is  #
# not offset but INSTABILITY as the swell direction moves through the window — #
# and this harness already measures that, as the p25/p75 the factor file       #
# carries and face_range publishes as the band. The angle gate was a           #
# pre-filter for a defect the run quantifies afterwards anyway, which argues   #
# for a backstop rather than a tolerance.                                      #
#                                                                             #
# WHY 90 IS NOT AN ARBITRARY ROUND NUMBER. 90 deg is where the dot product of  #
# the two seaward unit vectors changes sign. Below it there is at least one    #
# swell bearing that is simultaneously onshore at the break and at the matched #
# point, so the two can be sampling one sea state; at 90 that set collapses to #
# a single grazing bearing; beyond it no bearing is onshore at both and the    #
# pairing is physically incoherent rather than merely imprecise. cos(90) = 0   #
# is a property of the geometry. Two alternatives were considered and dropped: #
# 80 deg (half-overlap of two +/-80 swell windows) makes the gate depend on the#
# orientation-derived fallback arc shape, which several of these spots do not  #
# use; and 60 deg, at the visible gap between 52 and 66 in the observed        #
# deltas, is gap-fitting on 16 points — move one spot and the gap moves.       #
#                                                                             #
# WHAT IS STILL GUARDED, because this is a backstop and not a removal: a point #
# facing the far side of a headland or the inside of a harbour arm, where no    #
# swell can reach both. DISTANCE IS THE BETTER GUARD FOR THE REST, and it is   #
# now enforced — see MATCH_SEPARATION_M below. Bolinas was the standing        #
# example for leaving it unenforced: 73 deg passes this angle gate, but its    #
# MOP point is reportedly 2.4 km away across a curving coast. That is the pair #
# the separation gate now rejects.                                             #
FACE_SHORE_NORMAL_MAX_DELTA = 90.0

# --------------------------------------------------------------------------- #
# THIS CONSTANT IS EMPIRICAL, NOT PHYSICAL. That is the first thing to know    #
# about it, and the reason every paragraph below is about a measurement        #
# rather than about geometry.                                                  #
#                                                                             #
# FACE_SHORE_NORMAL_MAX_DELTA has a derivation: 90 deg is where the dot        #
# product of two seaward unit vectors changes sign, which is a property of the #
# geometry and would be the same number on any coast in any dataset. THIS      #
# CONSTANT HAS NO SUCH ARGUMENT AND SHOULD NOT BE GIVEN ONE. There is no       #
# distance at which a MOP point stops sampling the break's sea state; exposure #
# decorrelates gradually and continuously, and nothing in the physics puts a   #
# step anywhere. 2200 m is a cut placed in the widest gap of one observed      #
# distribution. Argue it from that measurement, revise it when the measurement #
# changes, and do not round it to 2000 because 2000 looks tidier.              #
#                                                                             #
# THE MEASUREMENT, AND WHY NOTHING HERE CAN CHECK IT. Every figure in this     #
# block was measured off-repo, on the author's machine, against               #
# scripts/mop_points.json. That cache is gitignored and absent from any clone; #
# _match cannot run without it, so neither CI nor a reviewer can recompute a   #
# single one of these numbers. They are recorded as REPORTED MEASUREMENTS, not #
# as facts this repository can verify, and deliberately NO TEST ASSERTS THEM.  #
# The tests pin the gate's behaviour at given distances, which is checkable;   #
# they do not pin any spot's actual distance, which is not.                    #
#                                                                             #
#   Separation across the full population, before the is_valid_surf_spot       #
#   filter landed:  p50 648 m   p75 888 m   p90 1340 m   p95 1623 m            #
#                   p99 2753 m.  Ten spots exceeded 1500 m.                    #
#   The sorted tail of the current 152-spot population:                        #
#     2753, 2439, 2426, 2074, 1794, 1663, 1623, 1535, 1530, 1448               #
#                                                                             #
# WHY 2200. Adjacent gaps through that tail run 20-130 m, with one exception:  #
# 2074 -> 2426 is a 352 m jump, roughly three times any other gap in the       #
# region. Any cut inside (2074, 2426) separates exactly the same three spots,  #
# so what the data supports is a RANGE, and 2200 is a point inside it rather   #
# than a threshold the data singles out.                                       #
#                                                                             #
# THIS IS GAP-FITTING, WHICH THE ANGLE GATE ABOVE EXPLICITLY REJECTED, and the #
# difference is worth stating rather than hoping nobody notices. There, 60 deg #
# was declined for sitting in the gap between 52 and 66 — "move one spot and   #
# the gap moves" — because a NON-empirical alternative existed and was better. #
# Here no such alternative exists. Gap-fitting is not being preferred over a   #
# principled cut; it is the only method available, and it inherits the same    #
# fragility, which is why the provenance above is recorded in this much detail #
# and why the successor below matters.                                         #
#                                                                             #
# WHY THIS IS NOT MATCH_FALLBACK_M (1200 m) FROM mop_handful_slice. Two        #
# reasons. The first is in that constant's own comment, at                     #
# mop_handful_slice.py:73-75: "MOP points sit on the 10 m contour, legitimately#
# 0.5-1.5 km offshore, so we only HARD-disqualify beyond ~1.2 km." By its own  #
# account a standoff of up to 1.5 km is NORMAL, so 1200 m does not separate    #
# sound pairings from unsound ones — it clips the top off the healthy          #
# distribution.                                                                #
# The percentiles agree: 1200 m falls between p75 (888) and p90 (1340), so it  #
# would cut somewhere between a tenth and a quarter of the population, against #
# 3 of 152 here. Second, it is the wrong KIND of constant. MATCH_FALLBACK_M is #
# a PUBLISHING threshold on the adoption path, deciding whether MOP may become #
# a spot's source; this is a PAIRING test, deciding whether two measurements   #
# describe the same water. Borrowing it would repeat precisely the error the   #
# 35-deg shore-normal gate made on this path — importing a constant calibrated #
# for adoption into a harness that only references.                            #
#                                                                             #
# THE KNOWN WEAKNESS, WHICH IS NOT SMALL. Distance is a scalar and the defect  #
# is directional. 2.4 km straight out to sea and 2.4 km along the coast are    #
# different failures with different costs, and this gate cannot tell them      #
# apart: both survive together or are cut together, on a number that says      #
# nothing about which one occurred. Toes Over and New Brighton Reef make it    #
# concrete. They sit 1.27 km apart in a straight line on the same stretch of   #
# Santa Cruz coast — that one figure IS checkable, computed from their roster  #
# coordinates — yet their reported separations are 2074 m and 2753 m, so this  #
# gate keeps one and drops the other. Nothing about the coastline justifies    #
# splitting that pair; the cut falls between them because of where their       #
# nearest cache points happen to lie.                                          #
#                                                                             #
# A BEARING TEST IS THE INTENDED SUCCESSOR: comparing the spot-to-point        #
# bearing against the break's own shore normal separates offshore standoff     #
# from alongshore drift, which is the distinction this constant is blind to.   #
# WHEN IT LANDS, REVISIT THIS NUMBER. A gate that can tell those two apart may #
# want a larger distance allowance, or may not need one at all.                #
MATCH_SEPARATION_M = 2200.0

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

    California, fed by the NWPS path, judged a real surf spot, and carrying NO mop_*
    field. The mop_* test is the load-bearing half: a spot with mop_point_id has its
    face computed from MOP by apply_mop_overrides, so including it would validate MOP
    against itself. Testing for the FIELDS rather than only for swell_window_source
    means a spot that was ever MOP-associated stays excluded even if its tier is later
    changed by hand.

    WHY THIS HARNESS NEEDS AN is_valid_surf_spot FILTER WHEN db_import ALREADY HAS ONE.
    They are filtering two different populations, and that was never visible until it
    was measured. This harness reads pipeline/spots_enriched.json directly (ROSTER
    above) — the FULL enrichment record, including entries enrichment itself judged not
    to be surf spots. The `spots` table is the SUBSET that survived that judgement,
    because db_import.py:473 drops the invalid ones on the way in. So "every California
    NWPS spot" meant one thing to the importer and a larger thing here, and the harness
    was measuring MOP against a break the site does not publish and has no row for.

    Seal Beach, California is the one entry this removes, and its own record says why:
    sources.wikidata_id Q593039 is the article about the CITY, and verification_notes
    reads "town center on bay side ... the actual surf break is Seal Beach Pier. Marked
    duplicate." It is 3.34 km from seal-beach-pier by its own stored coordinates — back
    inside Anaheim Bay, not on the open-ocean beach. invalid_reason "duplicate" at
    verification_confidence "high". The real break is that separate roster entry,
    seal-beach-pier, which is valid and stays in.

    IDENTITY, NOT TRUTHINESS — `is False`, mirroring db_import.py:473's `is not False`
    exactly. The distinction is not pedantry: of the 153 spots this predicate used to
    return, 136 are explicitly true, 16 CARRY NO is_valid_surf_spot KEY AT ALL (verify
    never reached them — the roster has no explicit nulls, only absences) and 1 is
    explicitly false. An identity check drops the 1 and keeps the 16; `if not
    spot.get("is_valid_surf_spot")` would drop all 17 — and one of the 16 it would take
    with it is seal-beach-pier, the genuine break this filter exists to preserve. An
    unverified spot is not a rejected one, and only the importer's rule gets that right.
    """
    if spot.get("region_hint") != "California":
        return False
    if spot.get("swell_window_source") != "nwps":
        return False
    if spot.get("is_valid_surf_spot") is False:
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
        face_measured, face_source = measured_face(r)
        out.append({
            "hour": h,
            "valid_time": r.get("valid_time"),
            "mop_hs_m": float(mop_hs),
            # THE TWO FACES ARE DELIBERATELY BOTH HERE AND DELIBERATELY NAMED APART.
            #   face_ft        — what we PUBLISHED for that hour, verbatim. Post-correction
            #                    on a corrected row. This is the right number for "what did a
            #                    reader see", which is what the blocked-hours report asks.
            #   face_measured  — the PRE-correction face, which is the only thing the ratio
            #                    may be computed from. See measured_face.
            # Before migration 017 they were the same value and one key served both. Letting
            # one key keep serving both after the split is how the blocked-hours report would
            # have quietly started reporting a corrected number under a heading about what we
            # published — a second contamination of the same shape as the first.
            "face_ft": r.get("face_ft"),
            "face_measured": face_measured,
            "face_source": face_source,
            "face_correction_version": r.get("face_correction_version"),
            "effective_size_ft": r.get("effective_size_ft"),
            "swell_source": r.get("swell_source"),
        })
    return out


def measured_face(row: dict) -> tuple:
    """(face to measure, "raw" | "fallback") for one forecasts row.

    face_ft_raw (migration 017) is the face BEFORE the per-spot MOP correction divided it,
    and it is the only quantity this script may put over MOP Hs. face_ft is post-correction
    on a corrected row, so measuring it after 2026-09-02 03:30 UTC returns the RESIDUAL of
    the correction rather than the offset — and over a window straddling that instant, a
    blend of the two that looks like neither.

    THE FALLBACK IS NOT A SAFE DEFAULT AND IS NOT TREATED AS ONE. Every row written before
    017 has face_ft_raw NULL, and for those rows face_ft is the raw face if and only if the
    row also predates the correction. Nothing on the row says which: face_correction_version
    is NULL across BOTH the pre-correction era and the contaminated one, and no repair exists
    because the divisor was not uniform (steamer-lane spans un-corrected, /2.8702, absent
    again, /2.8084 inside one fortnight). So "fallback" means UNKNOWN CORRECTION STATE, and
    the caller's job is to decide what to do about that — see classify_window, which refuses
    to let unknown rows sit silently beside known ones.
    """
    raw = row.get("face_ft_raw")
    if raw is not None:
        return raw, "raw"
    return row.get("face_ft"), "fallback"


def is_stamped(row: dict) -> bool:
    """True when this row was written by a run that knew about migration 017.

    THE CLASSIFIER IS THE STAMP, NOT THE PRESENCE OF face_ft_raw, and the difference is not
    academic. face_ft_raw is NULL on an UNRATEABLE hour — one whose face_ft is itself NULL —
    because the seam copies face_ft verbatim, nulls included. Classifying on the raw column
    would therefore file every unrateable hour of a perfectly clean window as unknown, flag
    the window MIXED, and print the loud banner over nothing. A warning that fires on healthy
    data is a warning that gets turned off, and this one has one job.

    face_correction_version has no such hole: stamp_provenance writes it on every hour it
    sees, rateable or not, so it means exactly "a post-017 run produced this row". For a
    stamped row, raw is present if and only if the face is, so nothing measurable is lost.

    A row carrying a raw but no stamp is treated as UNSTAMPED — conservative, and reachable
    only by hand-editing, since both columns are written by the same pass.
    """
    return row.get("face_correction_version") is not None


def classify_window(n_stamped: int, n_unstamped: int) -> str:
    """"clean" | "legacy" | "mixed" | "empty" for a window's row provenance.

    Pure and total so the policy is a thing that can be tested against literals rather than
    inferred from a run's output.

      clean   every row was written by a post-017 run, so every rateable one carries its
              pre-correction face. The measurement needs no caveat.
      legacy  NO row was — the whole window predates migration 017. Measurable, but the
              correction state is unknown, so it is reported as unknown rather than assumed
              innocent.
      mixed   BOTH. This is the exact condition that produced the 2026-09-07 reading of
              steamer-lane at 2.188 against a factor of 2.81: two populations averaged into
              one median that belongs to neither. The caller drops the unstamped rows and
              says so loudly. It does NOT average them, and it does not go quiet about it —
              a window that silently shortens is how a sample-size artefact gets read as a
              spread problem.
      empty   nothing joined.
    """
    if n_stamped and n_unstamped:
        return "mixed"
    if n_stamped:
        return "clean"
    if n_unstamped:
        return "legacy"
    return "empty"


def retain_for_measurement(pairs: list) -> list:
    """The pairs whose face_measured may be pooled together.

    Under "mixed", only the rows a post-017 run wrote. Under "clean" and "legacy" every pair
    is kept, because in both of those every row is of the SAME provenance and the ratio is at
    least internally consistent — which is the property that was violated.
    """
    verdict = classify_window(sum(1 for p in pairs if is_stamped(p)),
                              sum(1 for p in pairs if not is_stamped(p)))
    if verdict == "mixed":
        return [p for p in pairs if is_stamped(p)]
    return list(pairs)


def window_integrity_block(per_spot: list, total_raw: int, total_fallback: int,
                           window_verdicts: dict, stamps: dict) -> dict:
    """The window's provenance, as a block that travels WITH the artifact.

    EXTRACTED FROM run FOR THE SAME REASON summarise_spot WAS. The rehearsal defect was that
    the harness printed "MIXED WINDOW — NOT SAFE TO APPLY" twice and build_face_factors then
    planned and wrote 139 factors from that same artifact without comment: the verdict lived
    only on a terminal, where nothing downstream could read it. A banner is not a guard. So
    the state is recorded here and build_face_factors refuses --apply on it — and this
    function is pure precisely so that what it records can be checked against literals
    rather than inferred from a run that needs Supabase and a CDIP read.

    KEPT AND DROPPED ARE STATED OUTRIGHT, because a consumer must not have to derive them.
    raw_rows is NOT the kept count in general: under "legacy" every row is unstamped and
    every row is kept, so kept == fallback_rows there and == raw_rows only under "mixed".
    joined_hours is the retained count per spot (see summarise_spot), so summing it is the
    one definition that holds in all four verdicts.
    """
    overall = classify_window(total_raw, total_fallback)
    dropped = sum(e.get("provenance", {}).get("dropped", 0)
                  for e in per_spot if "error" not in e)
    return {
        "verdict": overall,
        "raw_rows": total_raw,
        "fallback_rows": total_fallback,
        "dropped_rows": dropped,
        "rows_kept": sum(e.get("joined_hours", 0)
                         for e in per_spot if "error" not in e),
        # THIS IS THE FIELD build_face_factors REFUSES ON.
        "rows_dropped": dropped,
        "safe_to_apply": overall != "mixed",
        "unsafe_reason": (
            None if overall != "mixed" else
            "MIXED WINDOW: the joined rows span both sides of migration 017, so the "
            "unstamped ones were DROPPED rather than averaged in. The effective window "
            "is shorter than the one requested, which widens p75/p25 — and "
            "build_face_factors excludes on p75/p25, so a spot can be held out for "
            "sample size and not for instability. Re-run once the whole window "
            "post-dates the migration."
        ),
        "per_spot_verdicts": window_verdicts,
        "stamps": stamps,
        "seam_code_versions": sorted({v.split(":", 1)[0] for v in stamps}),
        "meaning": (
            "clean: every joined row carries face_ft_raw, so the ratio is of the "
            "PRE-correction face and needs no caveat. legacy: no row carries it — the whole "
            "window predates migration 017 and the correction state is UNKNOWN, not "
            "innocent. mixed: both, and the fallback rows have been DROPPED rather than "
            "averaged in; joined_hours per spot is the retained count. A differing "
            "fingerprint across stamps is benign once face_ft_raw is present (raw is "
            "pre-correction whichever divisor was applied); a differing seam CODE version "
            "is not, and is listed separately."
        ),
    }


def summarise_spot(pairs: list, mop_hours: int, our_hours: int) -> dict:
    """Every measured field for one spot, from its joined pairs. Pure, so it can be tested.

    EXTRACTED FROM main BECAUSE IT WAS THE ONE PART NOTHING COULD REACH. main needs Supabase
    and a CDIP OPeNDAP read, so the arithmetic that decides which rows reach a statistic sat
    behind a network boundary — and two mutations of it (computing the ratio over `pairs`
    instead of `kept`, and reporting the pre-drop count as joined_hours) survived the whole
    suite. Both are precisely the "silently average them" failure this change exists to stop,
    so the contamination-critical arithmetic does not get to live where it cannot be pinned.

    joined_hours IS THE RETAINED COUNT, not the joined one, and that is deliberate:
    build_face_factors reads it, and a p75/p25 exclusion judged against a sample size the
    spot never had would read a dropped window as an unstable spot.
    """
    n_stamped = sum(1 for p in pairs if is_stamped(p))
    n_unstamped = len(pairs) - n_stamped
    verdict = classify_window(n_stamped, n_unstamped)
    kept = retain_for_measurement(pairs)

    stamps: dict = {}
    for p in kept:
        v = p.get("face_correction_version")
        if v is not None:
            stamps[v] = stamps.get(v, 0) + 1

    face_ratios = [r for r in (ratio(p["face_measured"], p["mop_hs_m"]) for p in kept)
                   if r is not None]
    eff_ratios = [r for r in (ratio(p["effective_size_ft"], p["mop_hs_m"]) for p in kept)
                  if r is not None]

    # Hours MOP says the swell did not arrive, with what we PUBLISHED there — face_ft, not
    # face_measured. This one asks what a reader saw, so the corrected number is the right
    # number here and the raw one would answer a different question.
    blocked = [p for p in kept if p["mop_hs_m"] <= MOP_HS_FLOOR_M]

    srcs: dict = {}
    for p in kept:
        srcs[p.get("swell_source") or "none"] = srcs.get(p.get("swell_source") or "none", 0) + 1

    return {
        "mop_hours": mop_hours, "our_hours": our_hours, "joined_hours": len(kept),
        "join_rate": round(len(kept) / our_hours, 3) if our_hours else None,
        "provenance": {"verdict": verdict, "raw_rows": n_stamped,
                       "fallback_rows": n_unstamped,
                       "dropped": len(pairs) - len(kept), "stamps": stamps},
        "face_ratio": _stats(face_ratios),
        "eff_ratio": _stats(eff_ratios),
        "height_agreement": height_agreement(kept),
        "blocked_hours": {"n": len(blocked),
                          "published_face_ft": _stats([p["face_ft"] for p in blocked])},
        "swell_source_counts": srcs,
    }


def _print_window_integrity(wi: dict) -> None:
    """The banner. Loud for anything but "clean", and quiet for clean so it stays loud."""
    v = wi["verdict"]
    # A CODE-VERSION MISMATCH IS LOUD EVEN ON A CLEAN WINDOW, and getting that wrong is the
    # obvious shape of this function: `if clean: print one line; return` skips the one check
    # that face_ft_raw does NOT make redundant. raw is only pre-correction with respect to a
    # seam that behaves as this one does, so two seams in one window is its own contamination.
    code_mismatch = len(wi["seam_code_versions"]) > 1
    if v == "clean" and not code_mismatch:
        print(f"WINDOW PROVENANCE: clean — all {wi['raw_rows']} joined rows carry "
              f"face_ft_raw; the ratio is of the pre-correction face.")
        return
    bar = "!" * 78
    print(bar)
    if v == "clean":
        print("WINDOW PROVENANCE: every row carries face_ft_raw, BUT NOT FROM ONE SEAM.")
    elif v == "mixed":
        print("MIXED WINDOW — THE FACTORS FROM THIS RUN ARE NOT SAFE TO APPLY AS THEY STAND.")
        print(f"  {wi['raw_rows']} row(s) carry face_ft_raw (pre-correction, measurable)")
        print(f"  {wi['fallback_rows']} row(s) do NOT — written before migration 017, so their")
        print("    correction state is UNKNOWN and no repair exists: the applied divisor was")
        print("    not uniform (steamer-lane spans un-corrected, /2.8702, absent, /2.8084).")
        print(f"  {wi['dropped_rows']} row(s) DROPPED. They were not averaged in.")
        print("  The effective window is therefore SHORTER than the one requested. Fewer")
        print("  hours widens p75/p25, and build_face_factors excludes on p75/p25 — so a")
        print("  spot may be excluded here for sample size and not for instability.")
        print("  Re-run once the whole window post-dates the migration.")
    elif v == "legacy":
        print("LEGACY WINDOW — CORRECTION STATE UNKNOWN FOR EVERY ROW.")
        print(f"  none of the {wi['fallback_rows']} joined rows carries face_ft_raw, so this")
        print("  window predates migration 017. If any part of it post-dates the correction")
        print("  (2026-09-02 03:30 UTC) the ratio is a blend and belongs to neither pipeline.")
    else:
        print("EMPTY WINDOW — nothing joined; there is no measurement here.")
    if code_mismatch:
        print(f"  SEAM CODE VERSIONS DIFFER across retained rows: {wi['seam_code_versions']}.")
        print("  face_ft_raw is only pre-correction with respect to one seam's arithmetic.")
    print(bar)


def _stats(values: list) -> dict:
    """n / mean / median / p10 / p25 / p75 / p90 / min / max for floats. Empty -> n 0.

    p25 AND p75 ARE THE PUBLISHED BAND. p10/p90 stay because the exclusion rule in
    build_face_factors is written against p90/p10 and re-basing an exclusion threshold is a
    separate decision from choosing what to display. p25/p75 is what the site shows: at
    roughly +/-20% it rounds a 4 ft number to 3-5 ft with about half the measured hours
    inside, where p10/p90's +/-40% rounds it to 2-6 ft — a band wide enough always to be
    right and therefore worth nothing to a reader.

    NOTE THAT THESE CANNOT BE BACK-FILLED. The raw per-hour ratios are consumed here and
    never written, so mop_spread.json carries only what this returns. Adding a quantile
    means re-running this script; it cannot be recovered from an existing spread file.
    """
    vals = [float(v) for v in values if v is not None and not math.isnan(float(v))]
    if not vals:
        return {"n": 0, "mean": None, "median": None, "p10": None, "p25": None,
                "p75": None, "p90": None, "min": None, "max": None}
    s = sorted(vals)

    def pct(p: float) -> float:
        # Nearest-rank on the sorted sample. Stated rather than left to a library so the
        # selftest can assert an exact value against a hand-computed one.
        k = min(len(s) - 1, max(0, int(round(p * (len(s) - 1)))))
        return s[k]

    return {
        "n": len(s), "mean": statistics.fmean(s), "median": statistics.median(s),
        "p10": pct(0.10), "p25": pct(0.25), "p75": pct(0.75), "p90": pct(0.90),
        "min": s[0], "max": s[-1],
    }


def height_agreement(pairs: list[dict]) -> dict:
    """bias / MAE / Pearson r of our PRE-CORRECTION face against MOP waveHs, both in FEET.

    Reported because it is what was asked for, and labelled in the output as what it is:
    the two are NOT the same quantity (see the module docstring), so a non-zero bias here
    is expected and is the amplification being measured — not an error. The CORRELATION
    is the part that carries information about tracking, independent of the scale factor.

    Reads face_measured for the same reason face_ratio does: bias against a CORRECTED face
    measures how well the correction landed, not how big the transform's offset is, and the
    two would silently swap meaning on 2026-09-02. Pearson r is the exception that would
    have survived either — it is invariant under a positive scale factor, so a per-spot
    divisor cannot move it — but it is computed from the same list as the other two and
    splitting them across two quantities would be worse than the consistency is worth.
    """
    ours, mop = [], []
    for p in pairs:
        if p.get("face_measured") is None:
            continue
        ours.append(float(p["face_measured"]))
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
    """|orientation - metaShoreNormal| in [0,180], or None when either is absent.

    AN EXACT 0.0 SHORE NORMAL IS TREATED AS ABSENT, NOT AS DUE NORTH. The MOP cache
    builder (mop_blacks_slice.scalar) returns None when metaShoreNormal is missing
    altogether, so a literal 0.0 means the netCDF variable was present and read as zero —
    and np.asarray() on a masked array drops the mask, so a fill value arrives as a plain
    number. Across the 48 MOP-ADOPTED spots no normal is 0.0, the values span 170.02-343.54
    and carry full float precision (231.02000427246094); a bare 0.0 is not what real data
    from this source looks like.

    Four of the sixteen spots this harness rejected were rejected on a 0.0: seal-beach-pier,
    surfside-jetty, seal-beach-california and oceanside-harbor, whose deltas were 148, 147,
    135 and 129 — each EXACTLY 360 - orientation_deg, i.e. the circular distance from the
    spot's own orientation to the number zero rather than to any measurement. Oceanside
    Harbor's stored 231.0 is corroborated to 0.02 deg by Oceanside Pier, 1.6 km away and
    MOP-adopted, whose normal is 231.02. (That is the historical record of the run those
    numbers came from. seal-beach-california has since left the population entirely — see
    is_population, which now drops is_valid_surf_spot false — so a rerun rejects three
    spots on a 0.0, not four. The other three are unaffected.)

    THIS GUARD IS LOAD-BEARING BECAUSE OF THE RELAXATION ABOVE, not merely tidy. At the old
    35-deg gate a 0.0 slipped through for 5 of the 152 population spots (those oriented
    within 35 deg of north); at FACE_SHORE_NORMAL_MAX_DELTA = 90 that becomes 26. Widening
    the angle without this would have widened the blast radius of the zero five-fold.
    (5 and 26 are unchanged by the is_valid_surf_spot filter: the one spot it removes is
    oriented 225, which is 135 from north and so was in neither count.)

    Reported and declined rather than silently mapped, because a genuine due-north normal
    does exist on some coasts and a spot dropped for a repairable cache entry should be
    findable. Declining costs one spot's eligibility; accepting a fabricated bearing costs a
    published face factor measured against the wrong stretch of water.
    """
    if orientation_deg is None or shore_normal is None:
        return None
    sn = float(shore_normal)
    if not math.isfinite(sn) or sn == 0.0:
        # stderr, so it cannot interleave with the report this script prints to stdout.
        # This module has no logger; the rest of it prints. Pure apart from the warning.
        print(f"    WARNING metaShoreNormal is {shore_normal!r} — treating as ABSENT, not as "
              f"due north; this MOP cache entry needs repair", file=sys.stderr, flush=True)
        return None
    return abs(circ_offset(float(orientation_deg), sn))


def match_verdict(dist_m, sn_delta):
    """(accepted, reason). The REFERENCING gate, not the adoption gate.

    Three checks: MATCH_SANITY_M (is there a MOP point near this spot at all),
    MATCH_SEPARATION_M (is it near enough to be sampling the same water) and
    FACE_SHORE_NORMAL_MAX_DELTA (does that point face water the break's swell can reach).
    The buoy cross-check from mop_handful_slice.verdict is DELIBERATELY NOT APPLIED: it
    exists to license PUBLISHING from MOP, and every spot it rejects is one whose face is
    still NWPS-derived — i.e. exactly the population this study needs.

    THE ANGLE GATE HERE IS 90, NOT THE ADOPTION PATH'S 35 — see
    FACE_SHORE_NORMAL_MAX_DELTA for why the two differ and why they must keep differing.

    THE DISTANCE GATE HERE IS 2200 m, NOT THE ADOPTION PATH'S MATCH_FALLBACK_M (1200 m)
    — see MATCH_SEPARATION_M for the measurement it comes from, for why borrowing 1200
    would repeat the 35-deg mistake, and for the directional blindness it still carries.

    WHY BOTH DISTANCE CHECKS STAY, AND IN THIS ORDER. Every spot the 25 km sanity cap
    rejects would also fail the 2.2 km separation gate, so the cap no longer does
    independent GATING work. It does DIAGNOSTIC work, which is why it is still first:
    "nearest MOP point 31.4 km away" says the cache has no coverage for this stretch of
    coast, while "nearest MOP point 2753 m away" says there is coverage and this
    particular pairing is too loose. Those are different problems with different fixes,
    and collapsing them would report a coverage hole as a pairing complaint.
    """
    if dist_m > MATCH_SANITY_M:
        return False, f"nearest MOP point {dist_m / 1000:.1f} km away (> {MATCH_SANITY_M / 1000:.0f} km)"
    if dist_m > MATCH_SEPARATION_M:
        return False, (f"nearest MOP point {dist_m:.0f} m away "
                       f"(> {MATCH_SEPARATION_M:.0f} m separation gate)")
    if sn_delta is None:
        return False, "no orientation_deg or no metaShoreNormal to compare"
    if sn_delta > FACE_SHORE_NORMAL_MAX_DELTA:
        return False, (f"shore-normal delta {sn_delta:.0f} deg "
                       f"(> {FACE_SHORE_NORMAL_MAX_DELTA:.0f})")
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
            .select("spot_id, valid_time, face_ft, face_ft_raw, "
                    "face_correction_version, effective_size_ft, swell_source")
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
          f"MATCH_SEPARATION_M={MATCH_SEPARATION_M:.0f} m — empirical, see its comment; "
          f"the adoption path's MATCH_FALLBACK_M={MATCH_FALLBACK_M:.0f} m NOT applied; "
          f"FACE_SHORE_NORMAL_MAX_DELTA={FACE_SHORE_NORMAL_MAX_DELTA:.0f} deg — this path "
          f"reads scalar waveHs only; the adoption path keeps "
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
    # Row provenance, accumulated across the sweep — see classify_window.
    window_verdicts: dict = {}
    stamps: dict = {}
    total_raw = total_fallback = 0
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

        # PROVENANCE FIRST, ARITHMETIC SECOND — and both inside summarise_spot, so there is
        # no route to a statistic that bypasses the retention rule. This loop only folds the
        # per-spot verdict into the run-wide totals.
        entry = {**rec, **summarise_spot(pairs, len(mop), len(rows))}
        prov = entry["provenance"]
        window_verdicts[prov["verdict"]] = window_verdicts.get(prov["verdict"], 0) + 1
        total_raw += prov["raw_rows"]
        total_fallback += prov["fallback_rows"]
        for v, n in prov["stamps"].items():
            stamps[v] = stamps.get(v, 0) + n
        per_spot.append(entry)
        fr = entry["face_ratio"]["median"]
        er = entry["eff_ratio"]["median"]
        joined = f"join {len(pairs):4d}/{len(rows):4d}"
        if fr is None or er is None:
            print(f"{label}  {joined}  (no usable ratio this window)", flush=True)
        else:
            # READ IT OFF THE ENTRY, not from a local. `blocked` was a local of this loop
            # until 89f4e74 moved the arithmetic into summarise_spot; the binding went
            # with it and this reference did not, leaving a LOAD_GLOBAL for a name bound
            # nowhere. Every run that reached a spot with a usable ratio — the ordinary
            # case, i.e. the first one — died here with NameError, and nothing noticed
            # for thirteen days because run() needs Supabase and a CDIP read, so no test
            # and no selftest can reach it.
            print(f"{label}  {joined}  face x{fr:5.2f}  eff x{er:5.2f}  "
                  f"blocked {entry['blocked_hours']['n']:3d}", flush=True)
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

    window_integrity = window_integrity_block(
        per_spot, total_raw, total_fallback, window_verdicts, stamps)

    result = {
        "generated_at": now.isoformat(),
        "window": {"t0": t0_iso, "t1": t1_iso, "days_back": days_back},
        "window_integrity": window_integrity,
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
            # BOTH are recorded, and the names say which path each belongs to, so a run
            # report can never be read as if one number governed both. See
            # FACE_SHORE_NORMAL_MAX_DELTA for why they differ.
            "FACE_SHORE_NORMAL_MAX_DELTA": FACE_SHORE_NORMAL_MAX_DELTA,
            "adoption_path_SHORE_NORMAL_MAX_DELTA": SHORE_NORMAL_MAX_DELTA,
            # Same treatment for the distance pair, for the same reason. The run report
            # is the only artifact that survives the run, so a later reader must be able
            # to tell WHICH gate produced a rejection without rereading this file.
            "MATCH_SEPARATION_M": MATCH_SEPARATION_M,
            "MATCH_SEPARATION_M_basis": (
                "EMPIRICAL, not physical: placed in the 2074-2426 m gap of an "
                "externally measured separation distribution. Not reproducible from a "
                "clone — scripts/mop_points.json is gitignored. See the constant's "
                "comment for the measurement and for its directional blindness."
            ),
            "adoption_path_MATCH_FALLBACK_M": MATCH_FALLBACK_M,
            "adoption_path_MATCH_FALLBACK_M_applied": False,
            "buoy_adoption_gate_applied": False,
            # THE THIRD THING THAT DEFINES THE POPULATION, and the only one that is not a
            # number. is_population drops is_valid_surf_spot false (see there); without
            # this flag a downstream artifact can read the two gate DISTANCES out of this
            # report and still not know whether the spots were filtered for validity.
            # build_face_factors copies all three into the committed factor file and
            # records null for any this report omits, so an omission here becomes a
            # permanent gap in that file's provenance rather than a recoverable one.
            #
            # PROBED, NOT ASSERTED. A literal True here would keep saying True after
            # someone deleted the clause — the same failure as the RUN_ON literal that
            # kept stamping 2026-09-01 onto later measurements. This asks the predicate.
            "is_valid_surf_spot_filter_applied": not is_population(
                {"region_hint": "California", "swell_window_source": "nwps",
                 "name": "probe", "is_valid_surf_spot": False}),
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
    _print_window_integrity(window_integrity)
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
    # REPEATED AT THE BOTTOM ON PURPOSE. The run prints hundreds of lines and the reader
    # scrolls to the end; a caveat that appears only above the roster block is a caveat that
    # gets missed, which is how the 2026-09-07 reading was very nearly acted on.
    _print_window_integrity(window_integrity)
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
    check("is_valid_surf_spot false excludes it (db_import drops it too)",
          is_population({**ca_nwps, "is_valid_surf_spot": False}) is False)
    check("is_valid_surf_spot true is kept",
          is_population({**ca_nwps, "is_valid_surf_spot": True}) is True)
    check("is_valid_surf_spot null is kept — unverified is not rejected",
          is_population({**ca_nwps, "is_valid_surf_spot": None}) is True)
    # Identity, not truthiness: these three are all falsy but none of them IS False.
    for falsy in (None, 0, ""):
        check(f"falsy-but-not-False {falsy!r} is kept",
              is_population({**ca_nwps, "is_valid_surf_spot": falsy}) is True)

    # The committed roster must give the number the brief expects.
    roster = json.load(open(ROSTER))
    n_pop = sum(1 for s in roster if is_population(s))
    n_ca = sum(1 for s in roster if s.get("region_hint") == "California")
    n_mop = sum(1 for s in roster if s.get("swell_window_source") == "cdip_mop")
    check(f"committed roster: 201 California spots ({n_ca})", n_ca == 201)
    check(f"committed roster: 48 on the cdip_mop tier ({n_mop})", n_mop == 48)
    # 152, not the 153 this printed before the is_valid_surf_spot filter: Seal Beach,
    # California (invalid_reason "duplicate") is the single entry it removes.
    check(f"committed roster: population is 152 ({n_pop})", n_pop == 152)

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
          [round(ratio(p["face_measured"], p["mop_hs_m"]), 6) for p in pairs] == [1.0, 1.5])
    # These fixture rows carry no face_ft_raw, so face_measured falls back to face_ft and the
    # two agree — which is exactly the pre-migration case, pinned as such.
    check("with no raw column the join falls back and says so",
          [p["face_source"] for p in pairs] == ["fallback", "fallback"]
          and [p["face_measured"] for p in pairs] == [p["face_ft"] for p in pairs])
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
    # p25/p75 ARE THE PUBLISHED BAND — face_correction divides the corrected face by them.
    # Losing them here would make the regenerated factor file carry no range and the whole
    # feature would ship inert with nothing else failing, so they are pinned at the source.
    # Same nearest-rank rule on [1,2,3,4,5]: p25 -> round(0.25*4)=1 -> 2, p75 -> round(0.75*4)=3 -> 4.
    check(f"stats p25 2.0 ({st['p25']})", st["p25"] == 2.0)
    check(f"stats p75 4.0 ({st['p75']})", st["p75"] == 4.0)
    # And on [1..9], where len-1 = 8: p25 -> round(2.0)=2 -> 3, p75 -> round(6.0)=6 -> 7.
    st9 = _stats([float(v) for v in (9, 1, 8, 2, 7, 3, 6, 4, 5)])
    check(f"stats p25 on nine values is 3.0 ({st9['p25']})", st9["p25"] == 3.0)
    check(f"stats p75 on nine values is 7.0 ({st9['p75']})", st9["p75"] == 7.0)
    # The band must be INSIDE p10/p90, which is the whole reason for preferring it.
    check("p10 <= p25 <= p75 <= p90",
          st["p10"] <= st["p25"] <= st["p75"] <= st["p90"])
    check("stats on an empty list -> n 0, no crash", _stats([])["n"] == 0)
    check("an empty list still carries the p25/p75 keys, as None",
          _stats([])["p25"] is None and _stats([])["p75"] is None)
    check("None entries are dropped, not counted", _stats([1.0, None, 3.0])["n"] == 2)

    # --- height agreement ---------------------------------------------------- #
    # face 3.281 vs MOP 1.0 m = 3.281 ft -> diff 0 ; face 9.843 vs 2.0 m = 6.562 ft -> diff 3.281.
    # bias = (0 + 3.281)/2 = 1.6405 ; MAE identical because both diffs are >= 0.
    # face_measured, NOT face_ft — height_agreement measures the PRE-correction face for
    # the same reason face_ratio does. Feeding it face_ft would silently swap the quantity.
    ha = height_agreement([
        {"face_measured": 3.281, "mop_hs_m": 1.0},
        {"face_measured": 9.843, "mop_hs_m": 2.0},
    ])
    check(f"height bias 1.6405 ft ({ha['bias_ft']})", abs(ha["bias_ft"] - 1.6405) < 1e-9)
    check(f"height MAE 1.6405 ft ({ha['mae_ft']})", abs(ha["mae_ft"] - 1.6405) < 1e-9)
    check("r is None below 3 samples (not a fake 1.0)", ha["r"] is None)

    # --- row provenance (migration 017) -------------------------------------- #
    check("raw wins over the corrected face",
          measured_face({"face_ft": 4.0, "face_ft_raw": 8.0}) == (8.0, "raw"))
    check("a missing raw falls back and is LABELLED fallback",
          measured_face({"face_ft": 4.0}) == (4.0, "fallback"))
    check("a raw of 0.0 is a value, not an absence",
          measured_face({"face_ft": 4.0, "face_ft_raw": 0.0}) == (0.0, "raw"))
    check("all-raw -> clean", classify_window(10, 0) == "clean")
    check("all-fallback -> legacy", classify_window(0, 10) == "legacy")
    check("any of both -> mixed", classify_window(1, 999) == "mixed"
          and classify_window(999, 1) == "mixed")
    check("nothing joined -> empty", classify_window(0, 0) == "empty")
    # RETENTION IS CLASSIFIED BY THE STAMP, not by face_ft_raw — see is_stamped. An
    # unrateable hour has a NULL raw and is still stamped, and classifying on the raw column
    # would flag an entirely post-017 window as mixed and drop those hours for nothing.
    _v = "1:2026-09-01:aaaaaaaa"
    check("a stamped row is stamped", is_stamped({"face_correction_version": _v}) is True)
    check("an unstamped row is not", is_stamped({"face_correction_version": None}) is False)
    check("stamped with a NULL raw is still stamped (an unrateable post-017 hour)",
          is_stamped({"face_correction_version": _v, "face_ft_raw": None}) is True)
    _mixed = ([{"face_correction_version": _v}] * 3 + [{"face_correction_version": None}] * 2)
    check("a mixed window drops the unknown rows rather than averaging them",
          len(retain_for_measurement(_mixed)) == 3)
    check("a clean window keeps everything",
          len(retain_for_measurement([{"face_correction_version": _v}] * 4)) == 4)
    check("a legacy window keeps everything",
          len(retain_for_measurement([{"face_correction_version": None}] * 4)) == 4)

    # --- the referencing gate ------------------------------------------------ #
    check("close match, aligned shore normal -> accepted", match_verdict(600.0, 10.0)[0] is True)
    check("a 5 km match is now REJECTED on separation, however good its angle",
          match_verdict(5000.0, 10.0)[0] is False)
    check("beyond MATCH_SANITY_M -> rejected",
          match_verdict(MATCH_SANITY_M + 1.0, 10.0)[0] is False)
    # LITERALS, NOT THE CONSTANT, for the same reason as the angle checks below.
    check("2200 m exactly -> accepted (the gate is inclusive at its own boundary)",
          match_verdict(2200.0, 10.0)[0] is True)
    check("2200.1 m -> rejected", match_verdict(2200.1, 10.0)[0] is False)
    check("1300 m -> accepted; this is NOT the adoption path's 1200 m",
          match_verdict(1300.0, 10.0)[0] is True)
    check("the separation gate is 2200, and MATCH_FALLBACK_M is untouched at 1200",
          MATCH_SEPARATION_M == 2200.0 and MATCH_FALLBACK_M == 1200.0)
    check("a far match reports separation, not the 25 km sanity cap",
          "separation gate" in match_verdict(2753.0, 10.0)[1])
    check("a truly absurd match still reports the sanity cap, not separation",
          "km away" in match_verdict(31_400.0, 10.0)[1]
          and "separation gate" not in match_verdict(31_400.0, 10.0)[1])
    # LITERALS, NOT THE CONSTANT. Writing these as SHORE_NORMAL_MAX_DELTA +/- 0.1 made them
    # true for any value of the constant, so they could not catch the gate being moved.
    check("shore-normal delta beyond the threshold -> rejected",
          match_verdict(600.0, 90.1)[0] is False)
    check("exactly at the shore-normal threshold -> accepted (inclusive)",
          match_verdict(600.0, 90.0)[0] is True)
    check("the face gate is 90, NOT the adoption path's 35",
          match_verdict(600.0, 36.0)[0] is True and match_verdict(600.0, 73.0)[0] is True)
    check("the adoption path constant is untouched at 35", SHORE_NORMAL_MAX_DELTA == 35.0)
    check("the two gates are genuinely different numbers",
          FACE_SHORE_NORMAL_MAX_DELTA == 90.0 and FACE_SHORE_NORMAL_MAX_DELTA > SHORE_NORMAL_MAX_DELTA)
    check("no shore normal to compare -> rejected, not assumed", match_verdict(600.0, None)[0] is False)
    # A 0.0 normal is cache damage, not due north. 212 vs 0 would otherwise read 148 deg.
    check("an exactly-zero shore normal is ABSENT, not due north",
          shore_normal_delta(212.0, 0.0) is None)
    check("a non-finite shore normal is ABSENT too",
          shore_normal_delta(212.0, float("nan")) is None)
    check("a real normal near zero is still honoured",
          abs(shore_normal_delta(212.0, 0.5) - 148.5) < 1e-9)
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
