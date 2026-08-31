"""NOAA NWPS nearshore forecast integration (Stage 2 — OKX pilot).

Sibling of pipeline/forecast/mop.py. For spots tagged
``swell_window_source == "nwps"`` in spots_enriched.json (set by
``pipeline.apply_nwps_assignments`` once placement + the buoy trust check pass),
override the spot's swell rating with its NWPS CG1 nearshore-field node, run
through the SAME break-response chain as the normal path (interpret.py: face_ft ×
directional_gain in the nearshore frame, period quality, chop), while KEEPING the
per-hour wind and tide multipliers the normal rater already computed. Spots
without a fresh NWPS read this cycle are left exactly as the orientation path
produced them.

This is the productionised form of the validated prototypes
(scripts/nwps_okx_probe_v3.py = discovery + seaward node placement + plausibility;
scripts/nwps_okx_buoycheck.py = NWPS-vs-NDBC trust gate). The logic is ported
here so the pipeline has no dependency on scripts/; interpret.py is reused for the
rating primitives. NWPS gives no shore normal (unlike MOP's metaShoreNormal), so
the "representative cell" check is replaced by: seaward-half-plane node selection
(±90° of orientation_deg) + a period floor + an in-swell-window direction gate.

Design guarantees (additive + reversible), mirroring apply_mop_overrides:
  * No-op until a spot carries swell_window_source == "nwps".
  * Any failure (no cycle, NOMADS hiccup, missing hour, bad node) → that spot
    keeps its orientation-path rating for the cycle. Never errors, never blanks.
  * HORIZON departure from MOP: NWPS carries the full 145-hr horizon (f000..f144),
    so we feed NWPS for EVERY valid hour it covers, not just near-now; fall back
    to WW3/orientation only beyond f144 or where a cycle lacks that hour.

  python -m pipeline.forecast.nwps_nearshore --selftest
  python -m pipeline.forecast.nwps_nearshore --validate            # Mac: fetch one OKX cycle, place + sample vs fallback
  python -m pipeline.forecast.nwps_nearshore --trustcheck          # Mac: NWPS-vs-buoy 44025 trust gate
"""
from __future__ import annotations

import argparse
import datetime
import json
import logging
import math
import os
import re
import time
from pathlib import Path

import numpy as np

from ..interpret import (
    chop_factors, composite_stars, directional_gain, face_ft,
    in_any_arc, period_quality, wind_multiplier,
)
from ..config import NWPS_FORECAST_FILE, WFO_TO_REGION
# ONE cutoff, both sides of the comparison. Importing the constant (rather than restating 8.0)
# is what stops the buoy band split and the model system veto drifting apart. ndbc_spectral is
# stdlib-only and imports nothing from here, so this is not a cycle.
from .ndbc_spectral import SWELL_WINDSEA_CUTOFF_HZ as _BUOY_SWELL_CUTOFF_HZ
from http.client import HTTPException      # base of IncompleteRead — a truncated NOMADS read raises
from urllib.error import HTTPError, URLError   #   http.client.IncompleteRead, which is NOT an OSError

log = logging.getLogger("pipeline.forecast.nwps_nearshore")

RATING_SOURCE = "ww3"          # face_ft shoaling factor — same as the validated chain
NOMADS = "https://nomads.ncep.noaa.gov/pub/data/nccf/com/nwps/prod/"

# swell_source values this module writes. The override takes HEIGHT from NWPS but
# SWELL IDENTITY (direction + period) from the WW3 partitions rate_spot already
# resolved, so the result is a HYBRID of two feeds and gets its own value. Calling
# that "nwps" is exactly what hid the dirpw substitution for months: the persisted
# provenance said NWPS drove the rating, which was true of the height and false of
# the direction the stars actually turned on. A distinct value makes an audit of
# "what decided this rating" answerable from the column alone.
SWELL_SOURCE_NWPS_WW3 = "nwps_height_ww3_dir"   # NWPS swh/shts + WW3 swell dp/tp
SWELL_SOURCE_NWPS_DIRPW = "nwps"                # no WW3 identity for this hour → dirpw/perpw
# --------------------------------------------------------------------------- #
# PLACEMENT_IS_GEOMETRY — why placement_verdict returns only OK / FAR.          #
# Assigning a spot to a model node is a statement about where the spot is, not  #
# about what the sea is doing while the check runs. placement_verdict used to   #
# return five outcomes; two of them (DEAD, period below a floor; OFFWIN, peak   #
# direction outside the spot's swell window) were sea-state conditions, so a    #
# spot's data source depended on the MONTH it was validated.                    #
#                                                                              #
# State of the practice: no operational surf service documented — Surfline, the #
# former Magicseaweed, surf-forecast.com, Windy, Stormglass — makes a spot's    #
# data-source assignment conditional on current conditions. CDIP's MOP model,   #
# the reference implementation for this exact problem, precomputes a continuous #
# transfer coefficient per direction and frequency from BATHYMETRY ALONE and    #
# applies it to whatever spectrum arrives. WAVEWATCH III represents unresolved- #
# island blocking as a partial transmission coefficient, not a binary (Tolman   #
# 2003).                                                                       #
#                                                                              #
# A hard directional gate is also physically wrong: diffraction puts wave       #
# height at roughly HALF the open-ocean height on the geometric shadow line,    #
# and Pawka, Inman & Guza (1984) found refraction supplies up to ~10% of        #
# otherwise-blocked energy, negligible only above ~0.11 Hz — so it matters MOST #
# for the long-period groundswell that makes these spots work.                  #
#                                                                              #
# NOTHING replaced them, and that is the evidenced answer rather than an        #
# omission. Two land-crossing guards were built for select_node and both were   #
# dropped after testing against real coastline across seven WFOs: its existing  #
# SEAWARD HALF-PLANE rule already covers the case (see the note above           #
# select_node, and scripts/node_headland_calibrate.py for the full result).     #
# NOT added: a depth check. The CG1 GRIB carries dirc, dirpw, perpw, shts, spc, #
# swh, wdir, ws and zos; zos is sea surface HEIGHT, not bathymetry, so no depth #
# field is available and an external source is out of scope.                    #
# --------------------------------------------------------------------------- #
PER_FLOOR_S = 3.0              # RETIRED from placement (the removed DEAD floor) — kept as the
                               # record of the threshold that was applied, referenced by nothing.
# FAR placement cap — the max spot→nearest-seaward-wet-cell distance that still counts as
# in-domain. GRID-AWARE (was a hardcoded 3.0 tuned to okx's 1.82 km grid): per-grid it is
# max(FAR_CAP_FLOOR_KM, FAR_CAP_MULT × grid node spacing) — see grid_far_cap_km(). The 3.0 floor
# is the legacy okx cap, so every grid at/finer than ~2 km spacing (okx 1.8, box 2.0, phi, akq)
# keeps EXACTLY 3.0 (no fine-grid zone changes). Coarser grids (mtr/gyx ≈2.5 km, lox ≈3+ km) get a
# proportionally wider cap so a legitimate coarse-grid nearshore node isn't rejected as a domain
# miss. 1.5× is the multiplier the legacy 3.0 already encodes for a ~2 km grid AND the largest that
# still floors every current fine grid at 3.0 (a literal 2× would widen even okx's 1.8 km grid to
# 3.6 km — a fine-grid change). A real domain gap (tens of km) stays FAR on every grid.
FAR_CAP_FLOOR_KM = 3.0        # legacy okx cap = the floor of the grid-aware cap
FAR_CAP_MULT = 1.5            # cap = max(floor, MULT × grid node spacing)
FAR_CAP_KM = FAR_CAP_FLOOR_KM  # back-compat: the default cap (fine-grid / no-grid callers + selftest)
HORIZON_MAX_FH = 144          # CG1 carries f000..f144 hourly (145 steps)
# Trust-gate thresholds.
# HEIGHT (unchanged — total-Hs skill is already excellent, r≈0.984 measured): model swh
# vs buoy WVHT, Pearson r ≥ TRUST_R_MIN over all overlapping hours.
TRUST_R_MIN = 0.80
TRUST_BUOY_RANGE_MIN_M = 0.75 # buoy TOTAL-Hs span below this ⇒ the window is flat ⇒ height not
                              #   assessable (Pearson r would be computed on noise, not signal).
                              #   Raised 0.5→0.75: 46268 (r=-0.545) and 46256 (r=-0.724) produced bogus
                              #   NEGATIVE correlations from ~0.3 m of noise that barely cleared the old
                              #   0.5 floor; 0.75 makes those windows INCONCLUSIVE (and, via the banking
                              #   guard in reverify_tagged, they then bank nothing). The monitor mirrors
                              #   this same constant (buoy_ready_monitor imports it — one source of truth).
TRUST_MIN_PAIRS = 6
TRUST_CIRC_MAX = 25.0         # LEGACY: the old dirpw-vs-MWD ceiling (superseded; see below)
# DIRECTION (rebuilt — partition-matched, energy-preconditioned). The old dirpw-vs-MWD
# comparison was a category error (whole-spectrum peak vs single-dominant-bin mean) and
# its 25° ceiling is meaningless for the new comparison. We now compare the model's
# tracked SWELL SYSTEM direction (CG0_Trkng) against the buoy's SPECTRAL swell-band mean
# direction (degree-valued alpha1/C11) — both swell, both "from". Measured circ_std on a
# good day is 4.5°, so:
SWELL_DIR_CIRC_MAX_DEG = 12.0   # residual SPREAD ceiling (~2.7× the 4.5° good day — margin
                                #   for sea-state/cycle variation, well under the model's real error)
SWELL_DIR_BIAS_MAX_DEG = 20.0   # residual MEAN (bias) ceiling — REQUIRED because circ_std of the
                                #   residual ignores a constant offset: a model 90° off has circ_std≈0
                                #   and would pass on spread alone. A large systematic offset must FAIL.
# PRECONDITION (validity, NOT outlier rejection): only judge direction where swell actually
# exists to judge. Excludes no-swell hours by the QUANTITY BEING COMPARED, never by whether
# it agrees with the model.
SWELL_HS_FLOOR_M = 0.5          # buoy spectral Hs_swell floor — below this the swell-band mean
                                #   direction is energy-starved/noisy and not surf-relevant
SWELL_FRAC_FLOOR = 0.3          # buoy swell-fraction floor — below this the sea is chop-dominated
                                #   and "swell direction" is a minor, low-signal component
SWELL_MIN_QUALIFYING = 6        # < this many qualifying (swell-present) hours → INCONCLUSIVE
# Model-system wind-sea/swell split. NWPS's watershed partitioning (Hanson & Phillips 2001,
# as in SWAN/WW3) partitions the WHOLE 2-D spectrum, so the tracked "systems" INCLUDE the
# local WIND-SEA — the biggest system on a windy day. We classify each model system and
# exclude wind-sea before matching, so we never compare the model's wind-sea against the
# buoy's swell.
# THE RULE IS A FIXED PERIOD CUTOFF, the SAME 8 s the buoy side uses — the value is derived
# from ndbc_spectral.SWELL_WINDSEA_CUTOFF_HZ (0.125 Hz) rather than restated, so the two
# sides of the comparison CANNOT drift apart. A previous note here said the buoy-side
# asymmetry was deliberate and not to "restore symmetry without measuring first"; it has now
# been measured, and wave age fails on this side too, for a different reason than it failed
# on the buoy side (see _system_is_swell). Wave age is retained as an explicit opt-in,
# exactly as PR #134 retained it for the band split.
_SWELL_MIN_PERIOD_S = 1.0 / _BUOY_SWELL_CUTOFF_HZ   # 8.0 s — tp below this is WIND SEA
_WAVE_AGE_FACTOR = 1.2          # OPT-IN ONLY; same value as ndbc_spectral.WAVE_AGE_FACTOR
_G_MS2 = 9.80665               # gravity; deep-water phase speed c = g·tp/(2π)

# ── Stage-1 rebuild (energy-weighted, spot-tiered, rolling) ──────────────────
# ENERGY-WEIGHTING: weight each hour's directional residual by the matched swell energy,
# w = min(model_swell_Hs, buoy_Hs_swell)² (energy ∝ Hs²; the min stops either side inflating
# it). A 100° miss on a 0.1 m sliver then counts ~1/400 of a 20° miss on a 2 m swell. This is
# the ECMWF/Bidlot & NDBC WavEval convention and is the single highest-impact fix.

# SPOT DIRECTIONAL-SENSITIVITY TIERS — from the product's cos²-gain analysis (~15° error is
# invisible on an open beach, decisive at a window edge), NOT fitted to make zones pass.
SWELL_DIR_TIERS = {
    "exposed":   {"circ_std": 30.0, "bias": 25.0},   # wide window / beach break
    "point":     {"circ_std": 15.0, "bias": 15.0},   # points, groins, partial windows
    "sheltered": {"circ_std": 10.0, "bias": 10.0},   # narrow window / sheltered / window-edge
}
# A spot's tier is read from the raycast window width (sum of swell_window_arcs spans — the
# DIRECT measure of directional exposure) refined by break_type.
SWELL_TIER_EXPOSED_ARC_DEG = 180.0    # ≥ this total open window ⇒ exposed
SWELL_TIER_SHELTERED_ARC_DEG = 90.0   # ≤ this ⇒ sheltered; between ⇒ point

# ROLLING ACCUMULATION — continuous skill monitoring, never a one-shot verdict on 40
# autocorrelated hours.
TRUST_ROLLING_DAYS = (30, 90)         # report both windows — reverify_tagged reports EVERY entry
# WHICH window decides PASS/FAIL, named rather than taken by position: reading TRUST_ROLLING_DAYS[0]
# at the call site made "the gating one" an accident of tuple order, so reordering the tuple for
# display would silently have moved the gate. Nothing else may assume a position. WIDENING this is a
# deliberate separate decision with a real trade-off — a wider window mixes older model behaviour
# into a current verdict — so it is not changed by anything that merely reports the other windows.
TRUST_GATING_WINDOW_DAYS = TRUST_ROLLING_DAYS[0]
TRUST_EVENT_GAP_HOURS = 12            # qualifying hours split into distinct swell EVENTS at ≥ this gap
TRUST_MIN_EVENTS = 5                  # < this many independent events ⇒ ACCUMULATING (not PASS/FAIL)
TRUST_RAYLEIGH_P = 0.05               # Rayleigh test: p > this ⇒ residuals directionally incoherent
                                      #   (R̄≈0) ⇒ the "bias" is meaningless and circ_std diverges — guard it
TRUST_HISTORY_DIR = Path(__file__).resolve().parents[2] / "pipeline" / "forecast_data" / "nwps_trust_history"

# eccodes short names (NOT NCEP abbreviations):
#   swh   = sig height of combined wind waves + swell (headline Hs)
#   shts  = sig height of total swell (swell only) — the windsea split for chop
#   perpw = primary wave period   dirpw = primary wave direction (deg, FROM)
#   ws / wdir = 10 m wind speed (m/s) / direction (deg, FROM) — additive, for the NDBC
#     spectral wave-age split at wave-only buoys; existing consumers read fields BY NAME
#     (swh/shts/perpw/dirpw), so carrying wind is inert for the trust gate and the rating.
_SHORTS = ("swh", "shts", "perpw", "dirpw", "ws", "wdir")
_SLUG_RE = re.compile(r"[^a-z0-9]+")

_HERE = Path(__file__).resolve()
_ROOT = _HERE.parents[2]
SCRIPTS_DIR = _ROOT / "scripts"
ENRICHED = _ROOT / "pipeline" / "spots_enriched.json"
# The NWPS assignment records apply_nwps_assignments reads: trust_by_zone (the HEIGHT-tagging gate
# that controls whether a spot consumes NWPS) + spots + buoy_reference (the BUOY trust-CHECK
# disposition, incl. zones whose buoy has been RETIRED as a reference — for 44098, on BOTH axes,
# because it is structurally invalid). The gate reads buoy_reference to report a retired zone as
# retired-by-design and to run NO buoy comparison against an invalid reference. Read-only here.
NWPS_ASSIGNMENTS = SCRIPTS_DIR / "nwps_okx_assignments.json"
# --validate writes a diagnostic dump — NOT the apply input. A FULL-REGION run writes
# scripts/nwps_{wfo}_validate_out.json; a --batch run writes
# scripts/nwps_{wfo}_batch_validate_out.json instead, so a two-spot batch cannot destroy the
# region file's FAR / NO_WET_CELL rows (see validate_out_path).


def _slug(name):
    return _SLUG_RE.sub("-", (name or "").lower()).strip("-")


# --------------------------------------------------------------------------- #
# Geometry + window helpers (ported verbatim from nwps_okx_probe_v3.py)        #
# --------------------------------------------------------------------------- #
def _haversine_km(a, b, c, d):
    R = 6371.0
    p1, p2 = math.radians(a), math.radians(c)
    dphi = math.radians(c - a); dl = math.radians(d - b)
    x = math.sin(dphi / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(min(1, math.sqrt(x)))


def _bearing(lat1, lon1, lat2, lon2):
    dl = math.radians(lon2 - lon1)
    y = math.sin(dl) * math.cos(math.radians(lat2))
    x = (math.cos(math.radians(lat1)) * math.sin(math.radians(lat2))
         - math.sin(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.cos(dl))
    return (math.degrees(math.atan2(y, x)) + 360) % 360


def _ang_within(deg, center, half):
    return abs(((deg - center + 180) % 360) - 180) <= half


# _in_arcs was DELETED here. It was a third copy of the membership test, comparing min/max as
# inclusive sector bounds, and its only caller was placement_verdict — whose own docstring
# states "per, dirpw and arcs ARE IGNORED and do not affect the verdict", which the code
# confirms. So nothing depended on it, including its empty-arcs-returns-True branch, which
# differed from interpret's False and was itself a latent trap. Use interpret.in_any_arc.


def placement_verdict(dist_km, per, dirpw, arcs, far_cap_km=FAR_CAP_FLOOR_KM):
    """OK / FAR for a placed node — PURELY GEOMETRIC.
    FAR = no seaward wet cell within *far_cap_km* (the grid-aware cap from grid_far_cap_km();
    defaults to the legacy FAR_CAP_FLOOR_KM for fine-grid / no-grid callers + tests).

    *per*, *dirpw* and *arcs* ARE IGNORED and do not affect the verdict. They are retained only
    so the existing call sites keep working; placement no longer needs a live wave spectrum.

    DEAD (peak period below PER_FLOOR_S) and OFFWIN (peak direction outside the spot's swell
    window) WERE RETURNED HERE AND WERE REMOVED (2026-08-13): they describe the sea state in the
    hour the check happened to run, and were being used to make a permanent geographic decision,
    so a spot's data source depended on the month it was validated. Twenty of Hawaii's 55 spots
    read OFFWIN in August because they face N/NW and the only swell running was a SE trade sea —
    Pipeline, Sunset, Waimea and Jaws are correctly off-window in August and in-window in
    December. Five mainland spots failed the same way and two of those passed a month later.
    See PLACEMENT_IS_GEOMETRY below for the state-of-practice and physics reasoning."""
    if dist_km is None or dist_km > far_cap_km:
        return "FAR"
    return "OK"


def _is_domain_miss(outcome):
    """Explicit rollup of the placement outcome: True when the spot fell OUTSIDE
    this WFO's grid domain — FAR (nearest wet cell beyond the grid's far cap) or NO_WET_CELL
    (no water in the grid at all) — so the grid-edge mop-up should retry it on
    another WFO. False for OK. Purely derived — it does NOT change how the outcomes
    themselves are computed.

    Formerly also False for the in-domain disqualifiers DEAD / OFFWIN; both were removed from
    placement_verdict (conditions, not geography — see its docstring), so the outcome set is now
    OK / FAR / NO_WET_CELL. Passing either name still returns False, which is what it always
    returned, so a stale caller or an archived validate-out is read the same way as before."""
    return outcome in ("FAR", "NO_WET_CELL")


def grid_spacing_km(cycle):
    """Nominal node spacing (km) of THIS grid — the mean of the per-axis MEDIAN adjacent-node
    great-circle distances, taken from the grid's own lat/lon coordinate vectors. NWPS CG1 grids
    are regular lat/lon, so this is the grid's real resolution (measured: okx≈1.80, box≈1.99,
    phi≈1.0, akq≈1.80, mtr≈2.48, gyx≈2.49 km; lox is coarser still). Returns 0.0 for a degenerate
    grid. Pure — reads only cycle['lats']/'lons' (the meshgrid from load_cycle)."""
    lats = np.asarray(cycle["lats"]); lons = np.asarray(cycle["lons"])
    if lats.ndim != 2 or lons.ndim != 2:
        return 0.0
    lat1d = lats[:, 0]      # meshgrid(indexing="ij"): column 0 varies with i → the latitudes
    lng1d = lons[0, :]      # row 0 varies with j → the longitudes
    mid_lat = float(np.median(lat1d)) if lat1d.size else 0.0
    steps = []
    if lat1d.size > 1:
        dlat = float(np.median(np.abs(np.diff(lat1d))))
        if dlat > 0:
            steps.append(_haversine_km(mid_lat, 0.0, mid_lat + dlat, 0.0))
    if lng1d.size > 1:
        dlng = float(np.median(np.abs(np.diff(lng1d))))
        if dlng > 0:
            steps.append(_haversine_km(mid_lat, 0.0, mid_lat, dlng))
    return sum(steps) / len(steps) if steps else 0.0


def grid_far_cap_km(cycle):
    """Per-grid FAR placement cap = max(FAR_CAP_FLOOR_KM, FAR_CAP_MULT × grid_spacing_km(cycle)).
    Fine grids (spacing ≤ FAR_CAP_FLOOR_KM/FAR_CAP_MULT = 2.0 km: okx, box, phi, akq) floor to the
    legacy 3.0 km; coarser grids widen proportionally (mtr/gyx ≈2.5 km → ≈3.7; lox ≈3+ km → ≈4.5+)
    so a valid coarse-grid nearshore node is not mis-flagged FAR. A real domain gap (tens of km)
    stays FAR on every grid. Falls back to the floor for a degenerate grid."""
    sp = grid_spacing_km(cycle)
    return max(FAR_CAP_FLOOR_KM, FAR_CAP_MULT * sp) if sp > 0 else FAR_CAP_FLOOR_KM


# --------------------------------------------------------------------------- #
# NOMADS discovery (ported from the probe / buoycheck)                         #
# --------------------------------------------------------------------------- #
def _http_get(url, timeout=180, retries=1, retry_delay=1.0):
    """GET *url*. Retries ONCE on a transient truncation/network error — http.client.IncompleteRead
    (a partial body, e.g. 10 MB of a 35 MB GRIB: a dropped connection, NOT a corrupt file), or a
    URLError/OSError — after a short delay, before giving up. A definitive HTTP 4xx is not retried.
    Only when the retry ALSO fails does the caller treat it as fatal (load_cycle → cached
    _WfoUnavailable; _listdir → []). One retry keeps a single flaky download from downing a whole WFO."""
    import urllib.request
    req = urllib.request.Request(url, headers={"User-Agent": "stormy-petrel-nwps"})
    for attempt in range(retries + 1):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read()
        except HTTPError as e:
            # A definitive 4xx won't be fixed by retrying; give up immediately. 5xx falls through.
            if 400 <= e.code < 500 or attempt >= retries:
                raise
            log.warning("nwps: %s → HTTP %s — retrying once in %.1fs", url, e.code, retry_delay)
            time.sleep(retry_delay)
        except (HTTPException, URLError, OSError) as e:
            if attempt >= retries:
                raise
            log.warning("nwps: download of %s interrupted (%s) — retrying once in %.1fs",
                        url, type(e).__name__, retry_delay)
            time.sleep(retry_delay)


def _listdir(url):
    try:
        html = _http_get(url, 60).decode("utf-8", "replace")
    except (HTTPError, URLError, OSError, HTTPException):
        # HTTPException covers http.client.IncompleteRead — a truncated directory listing must yield
        # [] (so find_latest_cycle falls back to an older cycle), never escape.
        return []
    return re.findall(r'href="([^"?][^"]*)"', html)


def _region_for(wfo):
    """NWS region root (er/sr/wr/pr/ar) for *wfo*'s NWPS tree, from
    pipeline.config.WFO_TO_REGION — so cycle discovery scrapes the WFO's real
    region dir (sgx → wr.<date>/…) instead of the hardcoded Eastern 'er.' default.
    An unmapped WFO falls back to 'er' with a one-line warning (never crashes)."""
    region = WFO_TO_REGION.get((wfo or "").lower())
    if region is None:
        print(f"⚠ nwps_nearshore: WFO {wfo!r} not in WFO_TO_REGION — defaulting region to 'er'")
        return "er"
    return region


def _cycle_files(wfo, date, cc, region="er"):
    cg1 = f"{NOMADS}{region}.{date}/{wfo}/{cc}/CG1/"
    files = [n for n in _listdir(cg1)
             if n.endswith(".grib2") and "Trkng" not in n and "CG1" in n]   # field file, NOT CG0 tracking
    return cg1, sorted(files)


def find_latest_cycle(wfo, region="er", lookback_days=4):
    """(date, cc, url) of the latest existing CG1 field file for *wfo*, or None."""
    dates = sorted({m for n in _listdir(NOMADS)
                    for m in re.findall(rf'^{region}\.(\d{{8}})/$', n)}, reverse=True)
    for date in dates[:lookback_days]:
        wfo_url = f"{NOMADS}{region}.{date}/{wfo}/"
        for cc in sorted({c for n in _listdir(wfo_url) for c in re.findall(r'^(\d\d)/$', n)},
                         reverse=True):
            cg1, files = _cycle_files(wfo, date, cc, region)
            if files:
                return date, cc, cg1 + files[-1]
    return None


def recent_cycles(wfo, n, region="er"):
    """Up to *n* most recent (date, cc, url) CG1 cycles — for the trust gate's
    elapsed-forecast-hour assembly."""
    out = []
    dates = sorted({m for x in _listdir(NOMADS)
                    for m in re.findall(rf'^{region}\.(\d{{8}})/$', x)}, reverse=True)
    for date in dates:
        wfo_url = f"{NOMADS}{region}.{date}/{wfo}/"
        for cc in sorted({c for x in _listdir(wfo_url) for c in re.findall(r'^(\d\d)/$', x)},
                         reverse=True):
            cg1, files = _cycle_files(wfo, date, cc, region)
            if files:
                out.append((date, cc, cg1 + files[-1]))
            if len(out) >= n:
                return out
    return out


# --------------------------------------------------------------------------- #
# GRIB load + node sampling (cfgrib; lazy so --selftest needs no eccodes)      #
# --------------------------------------------------------------------------- #
def _cycle_dt(date, cc):
    return datetime.datetime(int(date[:4]), int(date[4:6]), int(date[6:]), int(cc),
                             tzinfo=datetime.timezone.utc)


def _step_hours(da):
    """Forecast hours aligned to a DataArray's step axis (list), or [0] when the
    run carries a single scalar step. NWPS steps are timedelta64 offsets."""
    if "step" not in da.coords:
        return [0]
    steps = np.atleast_1d(np.asarray(da["step"].values))
    return [int(round(float(s / np.timedelta64(1, "h")))) for s in steps]


# --------------------------------------------------------------------------- #
# PER-PROCESS CYCLE CACHE — the fix for reverify's redundant refetching.        #
#                                                                              #
# trust_check loads n_cycles CG1 cycles per ZONE, and zones share a WFO (mhx 4, #
# sgx 4, lox 3), so a reverify pass fetched and REWROTE the same GRIB once per   #
# zone. Rewriting is what hurt most: it made cfgrib's sidecar .idx older than    #
# the GRIB, so every zone paid a full re-scan — the hundreds of "Ignoring index  #
# file ... older than GRIB file" lines. A cycle costs 12.9 s parsed once with no #
# index rebuild, against ~6.5 min per zone in CI.                               #
#                                                                              #
# Keyed on (wfo, date, cycle). Two levels, both PROCESS-LOCAL — nothing persists #
# across runs, the path format and the /tmp location are unchanged:             #
#   _CYCLE_FILES   keys whose GRIB this process wrote. A hit skips the download  #
#                  AND the rewrite, so the sidecar index stays valid.           #
#   _CYCLE_PARSED  the parsed dicts, bounded. Safe to share: load_cycle copies   #
#                  every slab out with np.asarray before its cfgrib datasets go  #
#                  out of scope, so the returned dict holds NO open GRIB handle  #
#                  and no cfgrib lifetime is extended by keeping it.            #
# Bounded because a full 57-zone pass touches ~60 cycles at tens of MB each. The #
# zone list is wfo-sorted, so same-WFO zones are adjacent and a few entries hold #
# every reuse; the cap trades a far-back re-parse for a bounded footprint.      #
# --------------------------------------------------------------------------- #
_CYCLE_CACHE_MAX = 8          # parsed cycles retained; ≈2 WFOs' worth at n_cycles=4
_CYCLE_FILES: set = set()
_CYCLE_PARSED: "collections.OrderedDict" = __import__("collections").OrderedDict()
_CYCLE_STATS = {"requested": 0, "downloaded": 0, "file_reused": 0, "parse_reused": 0}


def reset_cycle_cache():
    """Drop the per-process cycle cache and its counters (tests; also lets a long
    process reclaim memory). Does NOT delete the /tmp GRIB files."""
    _CYCLE_FILES.clear()
    _CYCLE_PARSED.clear()
    for k in _CYCLE_STATS:
        _CYCLE_STATS[k] = 0


def cycle_cache_stats():
    """{requested, downloaded, file_reused, parse_reused, saved} — 'saved' is the
    number of requests served without a download. Pure read of the counters."""
    s = dict(_CYCLE_STATS)
    s["saved"] = s["file_reused"] + s["parse_reused"]
    return s


def _parse_cycle_file(path, url, date, cc):
    """Parse an on-disk CG1 GRIB into the cycle dict. Split out of load_cycle so the
    fetch/cache path is testable without cfgrib or a network (see test_reverify_cycle_cache)."""
    import cfgrib    # lazy: --selftest never calls load_cycle, so needs no eccodes
    import warnings  # to scope the cfgrib/xarray merge FutureWarning below
    with warnings.catch_warnings():
        # cfgrib.open_datasets merges each param group internally with xarray's
        # default `compat`, emitting one FutureWarning per group ("the default
        # value for compat will change ... set compat explicitly"). We can't thread
        # compat into cfgrib's internal merge, and these groups carry no conflicting
        # duplicate variables (the current default and the future "override" give
        # identical values), so silence just that warning — the merged
        # swh/shts/perpw/dirpw values are unchanged.
        warnings.filterwarnings("ignore", category=FutureWarning, message=".*compat.*")
        datasets = cfgrib.open_datasets(path)
    if not datasets:
        raise OSError(f"cfgrib produced no datasets: {url}")

    # NWPS CG1 is a regular lat/lon nest — take the 1-D axes from the first
    # wave-bearing dataset and mesh them into the 2-D (lat, lng) frame the
    # seaward-node selector expects. Longitude → the app's -180/180 convention.
    lat1d = lng1d = None
    fields, steps = {}, set()
    for ds in datasets:
        if lat1d is None and "latitude" in ds.coords and "longitude" in ds.coords:
            lat1d = np.asarray(ds["latitude"].values, dtype="float64").ravel()
            raw = np.asarray(ds["longitude"].values, dtype="float64").ravel()
            lng1d = ((raw + 180.0) % 360.0) - 180.0
        for var in ds.data_vars:
            short = str(var).lower()
            if short not in _SHORTS:
                continue
            da = ds[var]
            for si, fh in enumerate(_step_hours(da)):
                if fh > HORIZON_MAX_FH:
                    continue
                slab = da.isel(step=si) if "step" in da.dims else da
                try:
                    slab = slab.transpose("latitude", "longitude")
                except ValueError:
                    continue   # not a plain lat/lon slab (e.g. wave partitions) — skip
                fields[(short, fh)] = np.asarray(slab.values, dtype="float32")
                steps.add(fh)
    if lat1d is None:
        raise OSError(f"cfgrib datasets carry no lat/lon grid: {url}")
    swh0 = fields.get(("swh", 0))
    if swh0 is None:   # mirror pygrib's swh@f000 anchor; else the earliest swh step
        swh_fhs = sorted(fh for (s, fh) in fields if s == "swh")
        if not swh_fhs:
            raise OSError(f"no swh field in cycle: {url}")
        swh0 = fields[("swh", swh_fhs[0])]
    lats, lons = np.meshgrid(lat1d, lng1d, indexing="ij")
    mask = np.isnan(swh0)   # land = NaN wave cell (was a masked array under pygrib)
    return {"lats": lats, "lons": lons, "mask": mask, "cycle_dt": _cycle_dt(date, cc),
            "steps": sorted(steps), "fields": fields}


def load_cycle(wfo, cycle=None):
    """Fetch + parse the latest (or given) CG1 cycle. Returns a dict:
      {lats, lons, mask, cycle_dt, steps, fields:{(short,fh): float32 grid}}
    Read with xarray + cfgrib, mirroring the sibling fetcher pipeline/forecast/
    nwps.py — the pipeline runner ships cfgrib/eccodes, not pygrib. cfgrib opens
    an NWPS GRIB as MULTIPLE datasets (one per param group), so the 4 wave fields
    can span datasets; we union them keyed by (shortName, forecast hour), the way
    nwps._extract_time_series_from_datasets unions by valid_time. Land cells read
    back NaN under cfgrib (they were masked arrays under pygrib), so `mask` is the
    NaN footprint of swh@f000 and `_wet_nodes` still selects on ``not mask``.
    Holds only the 4 wave fields × 145 steps in memory (≈tens of MB per WFO nest).
    Raises on fetch/parse failure (callers catch).

    CACHED PER PROCESS on (wfo, date, cycle) — see the cache block above. A parsed
    hit returns the same dict; a file hit re-parses but neither re-downloads nor
    REWRITES the GRIB, which is what keeps cfgrib's sidecar index valid. Nothing is
    cached until the parse succeeds, so a truncated body or a bad parse is retried
    rather than remembered."""
    if cycle is None:
        cycle = find_latest_cycle(wfo, _region_for(wfo))
        if not cycle:
            raise OSError(f"no recent CG1 cycle for {wfo}")
    date, cc, url = cycle
    key = (wfo, date, cc)
    path = os.path.join("/tmp", f"nwps_{wfo}_{date}_{cc}_CG1.grib2")
    _CYCLE_STATS["requested"] += 1

    hit = _CYCLE_PARSED.get(key)
    if hit is not None:
        _CYCLE_PARSED.move_to_end(key)
        _CYCLE_STATS["parse_reused"] += 1
        return hit

    if key in _CYCLE_FILES and os.path.exists(path):
        _CYCLE_STATS["file_reused"] += 1        # no download, no rewrite → sidecar index stays valid
    else:
        body = _http_get(url)
        _CYCLE_STATS["downloaded"] += 1      # counted on the FETCH, not on success: a body that
        if body[:4] != b"GRIB":              # turns out not to be GRIB still cost the network time
            raise OSError(f"not GRIB: {url}")
        with open(path, "wb") as f:
            f.write(body)

    out = _parse_cycle_file(path, url, date, cc)
    _CYCLE_FILES.add(key)                        # only after a clean parse
    _CYCLE_PARSED[key] = out
    while len(_CYCLE_PARSED) > _CYCLE_CACHE_MAX:
        _CYCLE_PARSED.popitem(last=False)
    return out


def _wet_nodes(lats, lons, mask):
    return [(float(lats[i, j]), float(lons[i, j]), i, j)
            for i in range(lats.shape[0]) for j in range(lats.shape[1]) if not mask[i, j]]


# --------------------------------------------------------------------------- #
# THE SEAWARD HALF-PLANE RULE IS LOAD-BEARING — DO NOT REMOVE IT AS A            #
# REDUNDANT HEURISTIC. It is what stops a node being chosen on the far side of   #
# land, and placement carries NO separate land-crossing check because of it.     #
#                                                                              #
# A cell behind a barrier is LANDWARD of the shore normal, so the ±90° filter    #
# drops it BEFORE distance is ever considered. Two land-crossing guards were     #
# built to back this up and BOTH were tested against real coastline across seven #
# WFOs (mfl, mhx, okx, phi, hgx, bro, box) before being dropped as unnecessary:  #
#                                                                              #
#   * a GSHHG polygon check re-scaled from --find-buoy: 23 FALSE POSITIVES over  #
#     those seven, penetrations 24–133 m, mostly against the single North        #
#     America polygon, plus Martha's Vineyard (243.2 km²) and Nantucket (132.0   #
#     km²) clipping their own shorelines. box alone would have lost 17 correctly #
#     placed spots. No setting of its constants was both safe and useful: a      #
#     per-grid area floor of one cell removed only 2 of the 9 polygon areas      #
#     actually crossed, and penetration only separates must-clear from must-flag #
#     once a barrier exceeds ~0.79 km — by which point the model's own mask      #
#     already marks it land.                                                     #
#   * a walk of the NWPS land mask itself, which needed no tuned constant:       #
#     moved ZERO nodes on all seven, INCLUDING mhx, where Hatteras is a resolved #
#     ~1 km barrier with open Pamlico Sound behind it — the strongest candidate  #
#     geometry in the set, and it stayed silent.                                 #
#                                                                              #
# The half-plane rule had already covered every case. scripts/                   #
# node_headland_calibrate.py holds the full result; it is kept as the record of  #
# why placement does no land-crossing check at node scale. If this filter is     #
# ever weakened, that conclusion no longer holds and the question reopens.       #
# --------------------------------------------------------------------------- #
def select_node(cycle, lat, lng, orientation):
    """Seaward-aware nearest WET cell (replaces MOP's metaShoreNormal clause):
    prefer wet cells whose bearing from the spot is within ±90° of orientation_deg
    (the open-ocean half-plane), else nearest wet. Returns
    (i, j, node_lat, node_lng, dist_km, moved) or None if no wet cell.

    The ±90° filter is LOAD-BEARING, not a nicety — see the block above."""
    wet = _wet_nodes(cycle["lats"], cycle["lons"], cycle["mask"])
    if not wet:
        return None
    def dist(n):
        return _haversine_km(lat, lng, n[0], n[1])
    naive = min(wet, key=dist)
    if orientation is not None:
        sea = [n for n in wet if _ang_within(_bearing(lat, lng, n[0], n[1]), orientation, 90)]
    else:
        sea = wet
    best = min(sea, key=dist) if sea else naive
    return best[2], best[3], best[0], best[1], dist(best), best is not naive


def _nearest_cell(cycle, lat, lng):
    """(i, j, dist_km) of the nearest WET cell to a baked node lat/lng."""
    wet = _wet_nodes(cycle["lats"], cycle["lons"], cycle["mask"])
    if not wet:
        return None
    best = min(wet, key=lambda n: _haversine_km(lat, lng, n[0], n[1]))
    return best[2], best[3], _haversine_km(lat, lng, best[0], best[1])   # (i, j, dist_km)


# --------------------------------------------------------------------------- #
# Depth-diagnostic node selectors (READ-ONLY — for the refraction/node experiment).       #
# The gate's default sampling is _nearest_cell; these are alternates the depth experiment  #
# re-runs the gate at, to test whether a shoreward/shallow node explains a direction bias. #
# --------------------------------------------------------------------------- #
def _shore_seaward_bearings(cyc, blat, blng):
    """(shore_brg, sea_brg, land_dist_km) inferred from the grid MASK alone: the shore
    bearing points at the nearest LAND (masked) cell, seaward is the opposite. None if the
    grid carries no land. Mirrors _node_diag so 'seaward' means the same everywhere."""
    lats, lons, mask = cyc["lats"], cyc["lons"], cyc["mask"]
    land = [(float(lats[a, b]), float(lons[a, b]))
            for a in range(lats.shape[0]) for b in range(lats.shape[1]) if mask[a, b]]
    if not land:
        return None
    ld = min(land, key=lambda p: _haversine_km(blat, blng, p[0], p[1]))
    shore = _bearing(blat, blng, ld[0], ld[1])
    return shore, (shore + 180.0) % 360.0, _haversine_km(blat, blng, ld[0], ld[1])


def _seaward_cell(cyc, blat, blng):
    """(i, j, lat, lng, dist_km) of the nearest wet cell in the SEAWARD half-plane (±90° of
    the mask-inferred seaward bearing) — the depth-matched pick when the plain-nearest cell
    is a shoreward/shallow shadow. None if no land (seaward undefined) or no seaward wet
    cell. Pure."""
    wet = _wet_nodes(cyc["lats"], cyc["lons"], cyc["mask"])
    sb = _shore_seaward_bearings(cyc, blat, blng)
    if not wet or sb is None:
        return None
    sea_brg = sb[1]
    sea = [w for w in wet if _ang_within(_bearing(blat, blng, w[0], w[1]), sea_brg, 90)]
    if not sea:
        return None
    b = min(sea, key=lambda w: _haversine_km(blat, blng, w[0], w[1]))
    return b[2], b[3], b[0], b[1], _haversine_km(blat, blng, b[0], b[1])


def _deepest_cell(cyc, blat, blng, radius_km=6.0, depth_fn=None):
    """(i, j, lat, lng, dist_km, depth_m|None) of the DEEPEST wet cell within *radius_km* of
    the buoy — depth-matched to the buoy's open-water regime. With *depth_fn* (a bathymetry
    sampler) it is the deepest by true depth; without it, the MOST-SEAWARD cell in the
    neighbourhood (largest projection onto the seaward bearing = furthest offshore), a
    landmask proxy for 'deepest'. None if no wet cell in radius / seaward undefined. Pure
    (depth_fn injectable for tests)."""
    wet = [w for w in _wet_nodes(cyc["lats"], cyc["lons"], cyc["mask"])
           if _haversine_km(blat, blng, w[0], w[1]) <= radius_km]
    if not wet:
        return None
    if depth_fn is not None:
        scored = [(depth_fn(w[0], w[1]), w) for w in wet]
        scored = [(d, w) for d, w in scored if d is not None]
        if scored:
            d, b = max(scored, key=lambda dw: dw[0])
            return b[2], b[3], b[0], b[1], _haversine_km(blat, blng, b[0], b[1]), d
    sb = _shore_seaward_bearings(cyc, blat, blng)
    if sb is None:
        return None
    sea_brg = sb[1]

    def seaward_projection(w):
        dist = _haversine_km(blat, blng, w[0], w[1])
        ang = math.radians(((_bearing(blat, blng, w[0], w[1]) - sea_brg + 180) % 360) - 180)
        return dist * math.cos(ang)   # + offshore, − shoreward
    b = max(wet, key=seaward_projection)
    return b[2], b[3], b[0], b[1], _haversine_km(blat, blng, b[0], b[1]), None


def _pick_cell(cyc, blat, blng, node_select="nearest", radius_km=6.0, depth_fn=None):
    """(i, j, lat, lng, dist_km) for the requested node-selection mode, falling back to the
    production NEAREST cell when a seaward/deep alternate can't be found. Read-only."""
    if node_select == "seaward":
        c = _seaward_cell(cyc, blat, blng)
        if c:
            return c
    elif node_select == "deepest":
        c = _deepest_cell(cyc, blat, blng, radius_km, depth_fn)
        if c:
            return c[:5]
    c = _nearest_cell(cyc, blat, blng)
    if c is None:
        return None
    i, j = c[0], c[1]
    return i, j, float(cyc["lats"][i, j]), float(cyc["lons"][i, j]), c[2]


def _node_value(cycle, short, fh, i, j):
    arr = cycle["fields"].get((short, fh))
    if arr is None:
        return None
    v = float(arr[i, j])
    return None if v != v else v   # NaN (land/missing) → None


def nwps_series_by_hour(spot, cycle):
    """{valid_hour_bucket: (swh, perpw, dirpw, shts)} for the spot's node across
    the full f000..f144 horizon, or None. hour_bucket = floor(valid_epoch/3600).
    Node = nearest wet cell to the baked nwps_node_lat/lng, else seaward-selected."""
    nlat, nlng = spot.get("nwps_node_lat"), spot.get("nwps_node_lng")
    if nlat is not None and nlng is not None:
        cell = _nearest_cell(cycle, nlat, nlng)
        ij = (cell[0], cell[1]) if cell else None
    else:
        sel = select_node(cycle, spot["lat"], spot["lng"], spot.get("orientation_deg"))
        ij = (sel[0], sel[1]) if sel else None
    if ij is None:
        return None
    i, j = ij
    cdt = cycle["cycle_dt"]
    out = {}
    for fh in cycle["steps"]:
        swh = _node_value(cycle, "swh", fh, i, j)
        per = _node_value(cycle, "perpw", fh, i, j)
        dpw = _node_value(cycle, "dirpw", fh, i, j)
        if swh is None or per is None or dpw is None:
            continue
        shts = _node_value(cycle, "shts", fh, i, j)
        valid = cdt + datetime.timedelta(hours=fh)
        out[int(valid.timestamp() // 3600)] = (swh, per, dpw, shts)
    return out or None


# --------------------------------------------------------------------------- #
# Rating + override (mirror mop_stars / apply_mop_overrides)                   #
# --------------------------------------------------------------------------- #
def nwps_stars(hs, per, swell_dir, swell_hs, orientation, wind_mult=1.0, tide_mult=1.0,
               arcs=None, optimal=None, chop=None):
    """Nearshore-frame star rating for one NWPS hour, reusing interpret.py exactly
    (face_ft from swh × directional_gain(swell_dir vs orientation), period quality
    from the swell period), with per-hour wind/tide injected from the normal rater.
    Chop is derived from the windsea split (swh vs shts); if shts is missing, chop
    falls to neutral (the entry's wind-based texture still applies via wind_mult).
    Returns (stars, face_ft, dir_gain, chop_mult, period_quality) or (None, …).

    *swell_dir* and *per* describe the SWELL TRAIN, not the whole spectrum. The
    caller passes the WW3 partition direction/period whenever rate_spot resolved
    one, and only falls back to NWPS dirpw/perpw when it did not — these two
    parameters were named dirpw/perpw when dirpw was the only thing ever passed,
    and that naming is why the substitution read as intentional for so long."""
    if hs is None or per is None or swell_dir is None or orientation is None:
        return None, None, None, None, None
    # SAME GEOMETRIC FRAME the partition was SELECTED in. combine_ww3_partitions picks the
    # winning partition with the spot's real arcs and optimal_swell_dir; this used to rescore
    # it with `[], orientation, orientation` — empty arcs, orientation in the optimal slot —
    # and overwrite the selection value, so the partition was chosen in one frame and scored
    # in another, and directional_gain's arc branch was unreachable for every nwps spot.
    # arcs/optimal default to the old behaviour so a caller with no spot geometry (the
    # selftest, the placement harness) is unchanged.
    dg = directional_gain(swell_dir, arcs or [],
                          optimal if optimal is not None else orientation,
                          orientation)
    face = face_ft(hs, per, RATING_SOURCE)
    eff = face * dg
    # Windsea-derived, from ONE computation that yields both the ratio and the
    # multiplier. *chop* lets the caller pass the pair it already computed (it needs the
    # ratio to persist alongside the multiplier and to re-judge the wind cap); when it is
    # None this computes the same pair from the same inputs via the same function, so the
    # two entry points cannot diverge.
    #
    # THE OLD IDIOM WAS `swell_hs if swell_hs else hs`, which could not tell None from
    # 0.0: a present-but-zero shts substituted hs for itself and scored chop 0 -> mult
    # 1.00, while rate_spot read the identical input as ratio 1.0 -> mult 0.30. Same
    # data, opposite ends of the curve, and the override wrote last. A missing shts still
    # falls to NEUTRAL, as the docstring above promises — but now via an explicit
    # unknown (ratio None -> mult 1.0) rather than by pretending the swell equals total.
    cr, cm = chop if chop is not None else chop_factors(hs, swell_hs)
    pq = period_quality(per)
    stars = composite_stars(eff, wind_mult, tide_mult, cm, pq)
    return stars, face, dg, cm, pq


def _ww3_swell_identity(entry):
    """(swell_dp, swell_tp) from a rating entry IFF rate_spot resolved them from the
    WW3 partitions, else (None, None).

    interpret sets swell_source="ww3" exactly when combine_ww3_partitions returned a
    result, so that flag — not the mere presence of swell_dp — is the discriminator:
    swell_dp is also populated on the buoy and nwps_total fallback paths, and those
    are NOT deep-water partition directions. Both values must be present and numeric;
    a half-resolved entry falls back rather than mixing frames. Pure."""
    if (entry.get("swell_source") or "") != "ww3":
        return None, None
    dp, tp = entry.get("swell_dp"), entry.get("swell_tp")
    if dp is None or tp is None:
        return None, None
    try:
        return float(dp), float(tp)
    except (TypeError, ValueError):
        return None, None


class _WfoUnavailable(Exception):
    """A WFO's CG1 cycle could not be downloaded/parsed this run (download truncation, HTTP error,
    missing cycle, bad GRIB). Raised per-spot but attributable to the WHOLE WFO, so apply_nwps_overrides
    can isolate the failure to that WFO's spots and count it distinctly from an ordinary per-hour
    fallback. Carries the WFO id and a short reason for the run summary."""
    def __init__(self, wfo, reason):
        super().__init__(f"{wfo}: {reason}")
        self.wfo = wfo
        self.reason = reason


class _SpotNotInArtifact(Exception):
    """The fetch stage produced no NWPS series for this spot, so the override has nothing to
    read. Distinct from a per-hour fallback and from a WFO outage: it means the spot was
    ABSENT from forecast_data/nwps.json, which nwps.fetch writes only when it extracted a
    non-empty series (nwps.py:1272 `if series: out[spot["name"]] = series`). Carries the name
    so the run summary can list who was lost rather than only how many."""
    def __init__(self, name):
        super().__init__(f"{name}: absent from the NWPS fetch artifact")
        self.name = name


def _make_artifact_fetch(nwps_by_spot):
    """fetch(spot) → {hour_bucket: (swh, perpw, dirpw, shts)} from READ A's artifact.

    THE SAME FOUR VALUES nwps_series_by_hour returns, taken from the fetch stage's own read
    instead of a second download of the same cycle.

    WHY THIS REPLACED THE SECOND DOWNLOAD. The pipeline ran `fetch_all` and `interpret` as
    two processes, and each resolved its own NWPS cycle by its own rule — nwps._locate_cycle
    (candidate DIRECTORIES, one constructed filename, grib_filter subset) versus
    find_latest_cycle (any listed CG1 file, whole file). Measured over 14 days of California
    rows: the two reads disagreed on the period they published for the same spot-hour on
    ~5.5% of rows, up to 13.7 s apart, and on the face by up to 8.6 ft — with the row then
    carrying `hs`/`tp`/`dp` from one read beside `face_ft`/`swell_*`/`chop_*` from the other.
    Four other explanations were tested and refuted (publish-window timing, the ±1 hour
    bucket slop, a perpw/mwp variable collision, and a much larger apparent rate that turned
    out to be sub-0.05 s rounding noise); a plain cycle race between the two stages is the
    one that survived. One read makes the disagreement impossible rather than smaller.

    The hour bucket, the four values and their order are unchanged, so every consumer below
    reads exactly what it read before. The KEY DIFFERENCE from nwps_series_by_hour is what
    happens to a spot with no data: that function returns None (an ordinary fallback), while
    this raises _SpotNotInArtifact so the loss is counted and NAMED — a spot the fetcher
    skipped used to get a second chance from the second download and no longer does.

    An hour is dropped on the same condition nwps_series_by_hour uses — swh, perpw or dirpw
    missing — so the two paths admit and reject the same hours. shts may be None and is
    passed through as None, exactly as before.
    """
    def fetch(spot):
        entries = nwps_by_spot.get(spot.get("name"))
        if not entries:
            raise _SpotNotInArtifact(spot.get("name"))
        out = {}
        for e in entries:
            t = _iso_to_epoch(e.get("valid_time"))
            if t is None:
                continue
            swh, per, dpw = e.get("hs"), e.get("tp"), e.get("dp")
            if swh is None or per is None or dpw is None:
                continue
            out[int(t // 3600)] = (swh, per, dpw, e.get("swell_hs"))
        return out or None
    return fetch


def _load_nwps_artifact():
    """READ A's artifact, {spot_name: [hourly entry, …]}. Raises when it is absent.

    Raising rather than returning {} is deliberate: an empty dict would send all ~600 spots
    down the not-in-artifact path and bury the real cause (the fetch stage never ran) under
    600 individual warnings. interpret wraps the override in a try/except that logs the
    exception and keeps the orientation-path ratings, so a loud failure here is both visible
    and non-fatal."""
    if not NWPS_FORECAST_FILE.exists():
        raise OSError(
            f"NWPS override needs {NWPS_FORECAST_FILE}, which does not exist — "
            "run `python -m pipeline.forecast.fetch_all` first. The override reads the "
            "fetch stage's series rather than downloading a second copy of the cycle."
        )
    return json.loads(NWPS_FORECAST_FILE.read_text())


def _make_default_fetch():
    """Per-WFO cycle cache so all spots of a WFO share one fetch+parse. A WFO whose cycle fails to
    download/parse is cached as a FAILURE (a _WfoUnavailable) — it is NOT re-attempted for every one of
    its spots, and the failure is isolated to that WFO. Returns a fetch(spot) → series-by-hour closure.
    Broad on purpose: load_cycle reaches NOMADS over urllib, whose read() can raise http.client
    IncompleteRead (HTTPException) — NOT an OSError — so a narrow (HTTPError, URLError, OSError) catch
    would let a truncated GRIB escape and abort the entire override step (all WFOs)."""
    cache = {}   # wfo -> loaded cycle dict, OR a _WfoUnavailable (cached failure)

    def fetch(spot):
        wfo = spot.get("nwps_wfo")
        if not wfo:
            return None
        if wfo not in cache:
            try:
                cache[wfo] = load_cycle(wfo)
            except (HTTPError, URLError, OSError, HTTPException, KeyError, ValueError, ImportError) as e:
                cache[wfo] = _WfoUnavailable(wfo, f"{type(e).__name__}: {e}")
                log.warning("nwps: WFO %s cycle unavailable (%s) — its spots fall back to the "
                            "orientation path this run; other WFOs unaffected", wfo, type(e).__name__)
        cached = cache[wfo]
        if isinstance(cached, _WfoUnavailable):
            raise cached
        return nwps_series_by_hour(spot, cached)
    return fetch


def apply_nwps_overrides(ratings, spots, *, dry_run=False, only=None, nwps=None, _fetch=None):
    """Override the swell rating of every swell_window_source=="nwps" spot with its
    NWPS node HEIGHT, keeping each hour's wind/tide AND the WW3-derived swell
    direction/period that rate_spot already resolved. Mutates *ratings* in place
    unless dry_run. *only* = slugs to restrict to. *_fetch* injectable for tests.

    SWELL IDENTITY IS NOT OVERWRITTEN. This used to replace swell_dp/swell_tp with
    NWPS dirpw/perpw for every overridden hour, which discarded the WW3 partition
    decomposition for 92% of the roster and rated those spots off a whole-spectrum
    mean direction — see the coupling note at the update site. Hours with no WW3
    identity still fall back to dirpw, tagged SWELL_SOURCE_NWPS_DIRPW and counted
    per WFO, so the fallback is visible rather than silent.

    Returns stats {fed, fell_back, errored, wfo_unavailable, details, by_wfo,
    hours_ww3_dir, hours_dirpw_dir} — the first five unchanged in name and
    meaning, the last three added — where
    wfo_unavailable maps each WFO whose whole cycle failed to download/parse to a
    reason — a distinct, VISIBLE signal so a mass fall-back to the orientation path
    can't ship silently (one WFO's GRIB failure is isolated to its spots; every
    other WFO still applies). Mirrors apply_mop_overrides; the one difference is
    HORIZON — NWPS covers the full f000..f144, so every overlapping valid hour is
    fed (not just near-now), and only hours beyond coverage fall back."""
    # ONE READ. The override now reads the FETCH STAGE's series (read A) instead of
    # downloading the cycle a second time — see _make_artifact_fetch for the measurement that
    # forced it. _fetch still wins when injected, so the selftest, the validate harness and
    # the trust gate keep their seam; *nwps* is the in-memory artifact interpret already
    # loaded, which is what makes the override's inputs byte-identical to rate_spot's.
    # load_cycle is NOT called on this path; it remains for the standalone grid tooling.
    if _fetch is not None:
        fetch = _fetch
    else:
        fetch = _make_artifact_fetch(nwps if nwps is not None else _load_nwps_artifact())
    fed = fell_back = errored = 0
    not_in_artifact = 0
    not_in_artifact_names = []
    wfo_unavailable = {}   # wfo -> reason: a whole-WFO download/parse outage (NOT a per-hour fallback)
    by_wfo = {}            # wfo -> {spots, hours, ww3_dir, dirpw_dir} over the OVERRIDDEN hours
    details = []
    for s in spots:
        if s.get("swell_window_source") != "nwps":
            continue
        name = s.get("name")
        slug = _slug(name)
        if only is not None and slug not in only:
            continue
        entries = ratings.get(name)
        if not entries:
            details.append((slug, "no base ratings (skip)", 0))
            continue
        orient = s.get("orientation_deg")
        try:
            series = fetch(s)
        except _SpotNotInArtifact as e:
            # THE BEHAVIOUR CHANGE, MADE VISIBLE. This spot produced no series in the fetch
            # stage (its WFO had no cycle, or the node search found no water). It used to get
            # a SECOND CHANCE from the override's own download, which could succeed where the
            # fetcher failed; with one read that chance is gone. Counted and NAMED in its own
            # bucket rather than folded into fell_back, so the loss is legible in the run
            # summary instead of looking like an ordinary missing hour.
            not_in_artifact += 1
            not_in_artifact_names.append(e.name)
            details.append((slug, "absent from the NWPS fetch artifact → fallback", 0))
            continue
        except _WfoUnavailable as e:
            # WHOLE WFO down — isolate to its spots and record it as a distinct, visible outage so a
            # mass fall-back to the orientation path can never ship silently. Other WFOs keep going.
            errored += 1
            wfo_unavailable[e.wfo] = e.reason
            details.append((slug, f"WFO {e.wfo} unavailable → fallback", 0))
            continue
        except (HTTPError, URLError, OSError, HTTPException, KeyError, ValueError, ImportError) as e:
            errored += 1
            details.append((slug, f"error: {type(e).__name__} → fallback", 0))
            continue
        if not series:
            fell_back += 1
            details.append((slug, "no fresh NWPS data → fallback", 0))
            continue
        n_over = n_ww3 = n_dirpw = 0
        for e in entries:
            t = _iso_to_epoch(e.get("valid_time"))
            if t is None:
                continue
            k = int(t // 3600)
            m = series.get(k) or series.get(k - 1) or series.get(k + 1)
            if not m:
                continue   # hour beyond f144 or missing → keep orientation path for it
            swh, per, dpw, shts = m
            # HEIGHT from NWPS (swh/shts — nearshore-refracted, higher resolution than
            # gfswave's 0.25° grid, which is why the override exists at all). SWELL
            # IDENTITY from WW3 — the direction and period rate_spot already resolved
            # by partition decomposition — because dirpw/perpw are WHOLE-SPECTRUM mean
            # quantities: on a mixed sea they describe no real wave train and track
            # whichever component carries most energy, usually local wind sea. That is
            # how Huntington rated off a 255°/7.8 s mean while the surfable swell was a
            # 15 s south at 191°, and how Pipeline/Sunset/Waimea rated off the 95°
            # windward trade sea at spots facing 300–315°.
            #
            # COUPLING — READ BEFORE CHANGING THE DIRECTION INPUT.
            # This is correct only while swell_window_arcs and optimal_swell_dir remain
            # DEEP-WATER concepts: they are ray-cast from the coastline against distant
            # swell propagation, so they ALREADY encode sheltering. Comparing a
            # nearshore-refracted direction against them double-counts refraction. WW3
            # partition direction is deep-water, i.e. the frame the window is defined
            # in. If those fields are ever replaced by nearshore transfer coefficients
            # (the P3-4 direction), the frame flips and a nearshore refracted direction
            # — dirpw, or an NWPS partition — becomes the RIGHT input instead. Revisit
            # this decision then; do not treat the WW3 preference as unconditional.
            ww3_dir, ww3_per = _ww3_swell_identity(e)
            if ww3_dir is not None:
                dir_eff, per_eff, src = ww3_dir, ww3_per, SWELL_SOURCE_NWPS_WW3
            else:
                # No WW3 identity for this hour (combine_ww3_partitions returned None,
                # or the spot has no WW3 series). Keep the override rather than dropping
                # it — NWPS height is still the better height — but fall back to dirpw
                # and TAG it, so a whole-spectrum direction can never again pass for a
                # swell direction unnoticed.
                dir_eff, per_eff, src = dpw, per, SWELL_SOURCE_NWPS_DIRPW
                n_dirpw += 1
            # CHOP IS COMPUTED ONCE, HERE, and both halves of it are persisted below.
            # This override replaces the height (swh/shts) that rate_spot's chop was
            # derived from, so rate_spot's chop_ratio no longer describes this row —
            # it must be recomputed and rewritten, not left behind while only the
            # multiplier moves. That mismatch is the 19.5% disagreement.
            cr, cm_pair = chop_factors(swh, shts)
            # RE-JUDGE THE WIND CAP against the chop this row actually has.
            # wind_multiplier caps an offshore bonus at 0.8 when chop_ratio > 0.4, and
            # the stored wind_mult had that decision frozen in from rate_spot's chop.
            # Recomputing it here is possible because the entry still carries the raw
            # NWPS wind and the spot carries offshore_wind_deg; where either is absent
            # the entry's existing wind_mult is kept untouched rather than guessed at.
            wdir, wspd = e.get("wind_dir"), e.get("wind_speed")
            offshore = s.get("offshore_wind_deg")
            wm_eff = e.get("wind_mult", 1.0)
            if wdir is not None and wspd is not None and offshore is not None:
                wm_eff = wind_multiplier(float(wdir), float(wspd), offshore, cr)
            st, face, dg, cm, pq = nwps_stars(swh, per_eff, dir_eff, shts, orient,
                                              wm_eff, e.get("tide_mult", 1.0),
                                              arcs=s.get("swell_window_arcs"),
                                              optimal=s.get("optimal_swell_dir"),
                                              chop=(cr, cm_pair))
            if st is None:
                continue
            if not dry_run:
                e.update(
                    face_ft=round(face, 2), dir_gain=round(dg, 3),
                    chop_ratio=cr, chop_mult=round(cm, 3),
                    wind_mult=round(wm_eff, 3),
                    period_quality=round(pq, 3), effective_size_ft=round(face * dg, 2),
                    stars=st, swell_dp=round(dir_eff, 3), swell_tp=round(per_eff, 3),
                    swell_hs=round(shts, 3) if shts is not None else None,
                    swell_source=src,
                )
                # In-memory / ratings.json tag only — deliberately NOT a DB column, so
                # this adds no schema. swell_source is the persisted discriminator.
                if src == SWELL_SOURCE_NWPS_DIRPW:
                    e["nwps_dirpw_fallback"] = True
                else:
                    e.pop("nwps_dirpw_fallback", None)
            n_over += 1
            n_ww3 += 1 if src == SWELL_SOURCE_NWPS_WW3 else 0
        if n_over:
            fed += 1
            # Per-WFO split of WHAT drove direction on the overridden hours. The dirpw
            # count is the visible half: a WFO drifting toward it is losing the swell
            # decomposition, and that must be readable per run, not inferred later from
            # the DB.
            w = by_wfo.setdefault(s.get("nwps_wfo") or "?",
                                  {"spots": 0, "hours": 0, "ww3_dir": 0, "dirpw_dir": 0})
            w["spots"] += 1
            w["hours"] += n_over
            w["ww3_dir"] += n_ww3
            w["dirpw_dir"] += n_dirpw
            details.append((slug, f"{n_over} hrs NWPS-fed "
                                  f"({n_ww3} ww3-dir, {n_dirpw} dirpw-dir)", n_over))
        else:
            fell_back += 1
            details.append((slug, "NWPS had no overlapping hour → fallback", 0))
    # ADDITIVE only: every pre-existing key keeps its name and meaning, so the existing
    # consumers (interpret's summary log, the selftest) are untouched.
    if not_in_artifact:
        log.warning(
            "nwps: %d spot(s) absent from the fetch artifact — they get NO override this run "
            "(they used to get a second chance from the override's own download, which is "
            "gone now that both stages share one read): %s",
            not_in_artifact, ", ".join(sorted(not_in_artifact_names)),
        )
    return {"fed": fed, "fell_back": fell_back, "errored": errored,
            "not_in_artifact": not_in_artifact,
            "not_in_artifact_names": sorted(not_in_artifact_names),
            "wfo_unavailable": wfo_unavailable, "details": details,
            "by_wfo": by_wfo,
            "hours_ww3_dir": sum(w["ww3_dir"] for w in by_wfo.values()),
            "hours_dirpw_dir": sum(w["dirpw_dir"] for w in by_wfo.values())}


def _iso_to_epoch(iso):
    try:
        return datetime.datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp()
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- #
# Trust gate (ported from nwps_okx_buoycheck.py) — NWPS vs nearest NDBC buoy    #
# --------------------------------------------------------------------------- #
def _pearson(xs, ys):
    n = len(xs)
    if n < 2:
        return float("nan")
    mx, my = sum(xs) / n, sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs); vy = sum((y - my) ** 2 for y in ys)
    return cov / math.sqrt(vx * vy) if vx > 0 and vy > 0 else float("nan")


def _circ_std(diffs):
    n = len(diffs)
    if n == 0:
        return float("nan")
    s = sum(math.sin(math.radians(d)) for d in diffs) / n
    c = sum(math.cos(math.radians(d)) for d in diffs) / n
    rbar = min(1.0, math.hypot(s, c))   # clamp: a constant offset gives rbar≈1 (float can overshoot)
    if rbar <= 1e-9:
        return float("inf")
    return math.degrees(math.sqrt(max(0.0, -2 * math.log(rbar))))


def _circ_mean(diffs):
    """Circular mean of angular residuals (degrees, −180..180). NaN if empty."""
    n = len(diffs)
    if n == 0:
        return float("nan")
    s = sum(math.sin(math.radians(d)) for d in diffs) / n
    c = sum(math.cos(math.radians(d)) for d in diffs) / n
    return math.degrees(math.atan2(s, c))


def swell_match_rule(*, prefer_wave_age=False):
    """Human-readable label for the rule _system_is_swell is applying, so the --diag can PRINT
    the selection rule instead of leaving a reader to infer it from the numbers. Derived from
    the same constants the rule uses, so the label cannot drift from the behaviour."""
    if prefer_wave_age:
        return f"wave_age (wind sea while c < {_WAVE_AGE_FACTOR}·U·cosδ) [OPT-IN]"
    return f"fixed_period_cutoff (wind sea while tp < {_SWELL_MIN_PERIOD_S:.1f} s)"


def _system_is_swell(system, wind_speed, wind_dir, *, prefer_wave_age=False):
    """True iff a tracked model system is SWELL (not wind-sea). DEFAULT RULE: a FIXED PERIOD
    CUTOFF — wind sea while tp < _SWELL_MIN_PERIOD_S (8 s), swell at or above it. That is the
    SAME threshold the buoy band split uses, derived from the same constant, so the two sides
    of the comparison cannot drift apart.

    WHY NOT WAVE AGE (the previous default): it DEGENERATES AT LOW WIND SPEED. The criterion
    calls a component wind sea only while its phase speed c = g·tp/(2π) is below
    1.2·U·cos(δ), so as U falls the threshold collapses and eventually sits below the phase
    speed of even very short waves — the veto then never fires and NOTHING is classified as
    wind sea. _match_swell_system's highest-energy rule is left picking whatever is biggest,
    which in light wind is the local chop. Worked case, pqr/46243 fh0 at 4 m/s from 340
    degrees: the real 12.2 s swell has c = 19.0 m/s against a 1.9 threshold, and the 6.6 s
    WIND SEA has c = 10.3 m/s against 3.6 — both pass as swell, so height decides and the
    1.10 m wind sea beats the 0.25 m swell. A 3 s wave at 4.7 m/s would pass too.
    MEASURED, sgx/46254 on the 2026-08-08 cycle: over twelve consecutive hours of 2-4 m/s
    wind the matched system was 0.44 m at 4.3 s, while an 18.2 s system at 0.13 m sat
    alongside it holding a steady 273 degrees and NDBC's own .spec reported swell at 16.7 s —
    the model's 4 s chop was being compared against the buoy's 18 s swell. At pqr/46243 the
    same failure produced a match that swung 227, 37 and 305 degrees across consecutive hours
    while the buoy held 267. Against the fixed cutoff over 48-hour windows the two rules
    disagree on 48 of 48 hours at sgx/46254, 35 of 41 at phi/44091, 16 of 27 at box/44085,
    9 of 48 at pqr/46243 and 4 of 37 at mhx/44095 — and those counts inspect only the first
    two tracked systems, so they are LOWER BOUNDS where more systems exist.

    *prefer_wave_age* restores the old criterion explicitly (kept, not deleted, exactly as
    PR #134 kept it for the buoy band split).

    UNCLASSIFIABLE → False, never mistaken for swell: missing/non-positive PERIOD, missing
    DIRECTION, or missing/non-positive WIND. The wind guard is retained DELIBERATELY even
    though the default rule does not consult the wind: `_match_swell_system(systems, None,
    None) is None` is the contract behind "an hour with no model wind at the node contributes
    no sample" in the trust gate and in nwps_trkng's --diag (its n_no_wind counter). Dropping
    it here would silently widen the gate's comparable-hour set, which is a separate change
    with its own measurement to do first."""
    tp, d = system.get("tp"), system.get("dir")
    if (tp is None or tp <= 0 or d is None
            or wind_speed is None or wind_dir is None or wind_speed <= 0):
        return False
    if not prefer_wave_age:
        return tp >= _SWELL_MIN_PERIOD_S
    c = _G_MS2 * tp / (2.0 * math.pi)                       # phase speed from the system period
    cosd = math.cos(math.radians(((d - wind_dir + 180) % 360) - 180))
    windsea = cosd > 0.0 and c < _WAVE_AGE_FACTOR * wind_speed * cosd
    return not windsea


def _match_swell_system(systems, wind_speed, wind_dir, *, prefer_wave_age=False):
    """The model tracked SWELL system to compare against the buoy's swell. NWPS's watershed
    partitioning (Hanson & Phillips 2001, as in SWAN/WW3) partitions the WHOLE spectrum, so
    the tracked systems INCLUDE the local WIND-SEA — on a windy day it is the biggest system,
    and matching it against the buoy's swell is the dirpw-vs-MWD category error one level
    deeper. So we FIRST drop wind-sea systems (_system_is_swell: a fixed 8 s period cutoff by
    default), THEN take the highest-energy remaining SWELL system. Highest-energy (not sys1,
    not a direction match): the buoy's swell-band mean is energy-weighted → its counterpart is
    the dominant swell, and it is independent of direction so it can't rig the residual.
    NOTE the two steps are not interchangeable — the highest-energy step is only safe because
    the veto ran first, which is precisely what stopped working when the veto degenerated at
    low wind. None when no system qualifies as swell → that hour is not comparable (validity,
    same as the buoy precondition: we exclude hours where the quantity — a model swell — does
    not exist), and dropping such an hour is always preferable to matching chop into it."""
    swell = [s for s in (systems or [])
             if s.get("hs") is not None
             and _system_is_swell(s, wind_speed, wind_dir, prefer_wave_age=prefer_wave_age)]
    return max(swell, key=lambda s: s["hs"]) if swell else None


def _match_windsea_system(systems, wind_speed, wind_dir, *, prefer_wave_age=False):
    """The model's dominant WIND-SEA partition (highest-energy system classified wind-sea),
    for the side-by-side diagnostic — so the chop rotating with the wind is visible next to
    the steady swell. None if no wind-sea system. Uses the SAME rule as the swell side, so
    the two partitions stay complementary under either setting."""
    ws = [s for s in (systems or [])
          if s.get("hs") is not None
          and not _system_is_swell(s, wind_speed, wind_dir, prefer_wave_age=prefer_wave_age)
          and s.get("tp") is not None and s.get("dir") is not None]
    return max(ws, key=lambda s: s["hs"]) if ws else None


def _weighted_circ_stats(deltas_deg, weights):
    """ENERGY-WEIGHTED circular residual stats → (bias_deg, circ_std_deg, Rbar, sum_w, n).
    bias = atan2(Σ w·sinΔ, Σ w·cosΔ); Rbar = |Σ w·e^{iΔ}| / Σw; circ_std = sqrt(−2·ln Rbar).
    Rbar guards the degenerate case the research flags: Rbar≈0 (directionally incoherent) →
    circ_std = inf and the bias is meaningless (see the Rayleigh test in rolling stats)."""
    pairs = [(d, w) for d, w in zip(deltas_deg, weights)
             if d is not None and w is not None and w > 0]
    sw = sum(w for _, w in pairs)
    if sw <= 0:
        return float("nan"), float("nan"), 0.0, 0.0, 0
    sx = sum(w * math.cos(math.radians(d)) for d, w in pairs)
    sy = sum(w * math.sin(math.radians(d)) for d, w in pairs)
    rbar = min(1.0, math.hypot(sx / sw, sy / sw))
    bias = math.degrees(math.atan2(sy, sx))
    circ_std = math.degrees(math.sqrt(-2.0 * math.log(rbar))) if rbar > 1e-9 else float("inf")
    return bias, circ_std, rbar, sw, len(pairs)


def _hour_weight(model_hs, buoy_hs):
    """w = min(model_swell_Hs, buoy_Hs_swell)² — energy of the SMALLER side, so neither a
    model sliver nor a buoy sliver can inflate a noisy hour's weight."""
    if model_hs is None or buoy_hs is None:
        return 0.0
    return min(model_hs, buoy_hs) ** 2


def _arc_total_width(arcs):
    """Total open swell-window width (°) — sum of the raycast arc spans; 0 if none. Falls back
    to a wrap-aware (max − min) when an arc carries only min/max (e.g. the pilot fixtures);
    live spots_enriched.json arcs always carry an explicit span, so they are byte-identical."""
    total = 0.0
    for a in (arcs or []):
        if not isinstance(a, dict):
            continue
        span = a.get("span")
        if span is not None:
            total += span
        elif a.get("min") is not None and a.get("max") is not None:
            total += (a["max"] - a["min"]) % 360.0
    return total


def _spot_tier(spot):
    """'exposed' | 'point' | 'sheltered' — directional-sensitivity tier from the raycast
    window WIDTH (the direct measure: a wide open window means many directions reach the
    spot, so a directional error stays inside a broad acceptance and is invisible; a narrow
    window means a small directional error pushes you off it and is decisive), refined by
    break_type. Width is primary because it is measured, not labelled."""
    width = _arc_total_width(spot.get("swell_window_arcs"))
    bt = (spot.get("break_type") or "").lower()
    if width >= SWELL_TIER_EXPOSED_ARC_DEG:
        tier = "exposed"
    elif width <= SWELL_TIER_SHELTERED_ARC_DEG:
        tier = "sheltered"
    else:
        tier = "point"
    # a named point/reef/jetty/sheltered break is never treated as a fully exposed beach
    if tier == "exposed" and any(k in bt for k in ("point", "reef", "jetty", "groin", "cove",
                                                   "harbor", "harbour", "sheltered", "sound")):
        tier = "point"
    return tier


def _count_independent_events(hours_sorted, gap_hours=TRUST_EVENT_GAP_HOURS):
    """Number of INDEPENDENT swell events among qualifying-hour timestamps (epoch-hours,
    sorted): a new event starts whenever the gap to the previous qualifying hour is ≥
    *gap_hours*. Hourly residuals within one swell episode are autocorrelated (the same
    swell), so one episode ≈ one independent sample — the effective N the verdict counts."""
    if not hours_sorted:
        return 0
    events = 1
    for a, b in zip(hours_sorted, hours_sorted[1:]):
        if b - a >= gap_hours:
            events += 1
    return events


def _rayleigh_p(rbar, n):
    """Rayleigh test p-value for directional coherence of the residuals. Small p ⇒ the
    residuals cluster around a real mean direction (bias meaningful). Large p ⇒ R̄≈0,
    incoherent — the bias is noise and circ_std diverges. Approx p = exp(−n·R̄²)."""
    if n <= 0:
        return 1.0
    return math.exp(-n * rbar * rbar)


def _swell_precondition(hs_swell, frac):
    """True when there is enough swell to MEANINGFULLY judge its direction. This is a
    VALIDITY precondition, NOT outlier rejection: it excludes hours by the QUANTITY BEING
    COMPARED (is there swell at all?), never by whether the model agrees. You cannot
    validate swell direction in a sea with no swell."""
    return (hs_swell is not None and frac is not None
            and hs_swell >= SWELL_HS_FLOOR_M and frac >= SWELL_FRAC_FLOOR)


def windsea_only_fail(res):
    """True when THIS window's height FAIL was computed over a sea with NO comparable swell
    hours (n_qualifying == 0), so the FAIL says nothing about groundswell skill.

    Reporting only — it never suppresses a FAIL, changes a verdict, or filters a zone out of
    the flagged list. Height is deliberately the buoy-independent primary gate, so it keeps
    running on swell-free windows where it still has something to say; this just stops the
    result being read as evidence it is not. Pure."""
    return bool(res.get("verdict") == "FAIL" and (res.get("n_qualifying") or 0) == 0)


def swell_trust_verdict(samples, tier="point"):
    """The rebuilt gate (Stage 1). HEIGHT is the PRIMARY gate — the skill the field actually
    verifies (JCOMM/ECMWF/NCEP score SWH & period, not buoy direction). DIRECTION is an
    ENERGY-WEIGHTED, spot-TIERED FLAG (not a hard block on one window): each hour's residual
    is weighted by min(model_swell_Hs, buoy_Hs_swell)² so a 100° miss on a 0.1 m sliver counts
    ~1/400 of a 20° miss on a 2 m swell. Model wind-sea systems are excluded (wave-age); the
    matched model SWELL is compared to the buoy spectral swell_dir over comparable hours only.
    Returns weighted AND unweighted stats, per-hour records (incl. the wind-sea partition, for
    the side-by-side diag), independent-event count + Rayleigh coherence, and 'records' for the
    rolling accumulator. Pure/offline."""
    th = SWELL_DIR_TIERS.get(tier, SWELL_DIR_TIERS["point"])
    # HEIGHT — the PRIMARY gate: model swh vs buoy WVHT over all overlapping hours.
    h = [(s["model_swh"], s["buoy_wvht"]) for s in samples
         if s.get("model_swh") is not None and s.get("buoy_wvht") is not None]
    height_r, height_n = float("nan"), len(h)
    # The range guard already computes this span; keep it so the verdict can SAY what it was
    # computed over. None whenever the correlation was NOT computed, so it never implies a
    # comparison that did not happen. WHEN the correlation runs is unchanged.
    height_hs_span = None
    if height_n >= TRUST_MIN_PAIRS:
        bwv = [b for _, b in h]
        span = max(bwv) - min(bwv)
        if span >= TRUST_BUOY_RANGE_MIN_M:
            height_hs_span = span
            height_r = _pearson([m for m, _ in h], bwv)

    # DIRECTION — comparable hours only (buoy swell present AND a model SWELL system exists).
    per_hour, resid, weights, comp_hours, records, n_model_no_swell = [], [], [], [], [], 0
    for s in samples:
        qual = _swell_precondition(s.get("buoy_hs_swell"), s.get("buoy_frac"))
        mws, mwd = s.get("model_ws"), s.get("model_wdir")
        matched = _match_swell_system(s.get("model_systems"), mws, mwd) if qual else None
        wsea = _match_windsea_system(s.get("model_systems"), mws, mwd)   # for the side-by-side diag
        # THE LIGHT-WIND CONTAMINATION NOTED HERE IS NOW FIXED AT SOURCE. It read: in light wind
        # the model emits no wind-sea system (wsea is None), local chop folds into the single
        # tracked system, `matched` classifies it as swell BY WAVE-AGE, and its direction rotates
        # WITH the wind (observed +23° at 46237, +31° at 46215 while the buoy swell held steady).
        # The cause was the wave-age veto degenerating as U falls, so nothing was ever classified
        # wind sea. _system_is_swell now applies a FIXED 8 s period cutoff, which vetoes chop
        # independently of wind speed, so the deferred per-hour "option-c" guard is no longer the
        # pointer for this — chop at tp < 8 s can no longer be matched at any wind speed. What the
        # cutoff does NOT catch is a genuine >= 8 s system that is still locally wind-driven; if
        # that ever shows up, calibrate against a known-good mixed-sea event before adding a guard.
        bdir, bhs = s.get("buoy_swell_dir"), s.get("buoy_hs_swell")
        d = w = None
        if qual and matched is None and s.get("model_systems"):
            n_model_no_swell += 1
        if matched and bdir is not None:
            d = ((matched["dir"] - bdir + 180.0) % 360.0) - 180.0
            w = _hour_weight(matched.get("hs"), bhs)
            resid.append(d); weights.append(w); comp_hours.append(s.get("t"))
            records.append({"t": s.get("t"), "model_hs": matched.get("hs"), "model_tp": matched.get("tp"),
                            "model_dir": matched.get("dir"), "buoy_hs": bhs, "buoy_dir": bdir,
                            "residual": d, "weight": w})
        per_hour.append({"t": s.get("t"), "qualifying": qual,
                         "matched_system": (matched["system"] if matched else None),
                         "model_dir": (matched["dir"] if matched else None),
                         "matched_tp": (matched.get("tp") if matched else None),
                         "matched_hs": (matched.get("hs") if matched else None),
                         "windsea": wsea, "delta": d, "weight": w,
                         "buoy_swell_dir": bdir, "buoy_hs_swell": bhs, "buoy_frac": s.get("buoy_frac"),
                         "buoy_windsea_dir": s.get("buoy_windsea_dir"), "buoy_hs_windsea": s.get("buoy_hs_windsea"),
                         "all_systems": s.get("model_systems") or [], "model_ws": mws, "model_wdir": mwd})

    dir_n = len(resid)
    w_bias, w_cs, rbar, _sumw, _ = _weighted_circ_stats(resid, weights)
    u_cs = _circ_std(resid) if resid else float("nan")
    u_bias = _circ_mean(resid) if resid else float("nan")
    n_events = _count_independent_events(sorted(comp_hours))
    rayleigh = _rayleigh_p(rbar, dir_n)
    coherent = rayleigh <= TRUST_RAYLEIGH_P
    dir_flag = bool(coherent and w_cs == w_cs and w_cs <= th["circ_std"] and abs(w_bias) <= th["bias"])

    # HEIGHT-PRIMARY verdict for THIS window (direction is a flag, not a one-window block).
    # The PASS/FAIL reasons state WHAT THE CORRELATION WAS COMPUTED OVER — overlapping hours,
    # the buoy total-Hs span, and the comparable swell-hour count — because r alone cannot tell
    # a groundswell divergence from a flat afternoon. phi/44091 on 2026-08-17 read FAIL r=0.746
    # over 25 hr with n_qualifying 0: a true statement about a pure wind-sea window, and easy to
    # misread as a statement about groundswell skill. The verdict is unchanged; only what it
    # says about itself is.
    _ev = (f"over {height_n} overlapping hr"
           + (f", buoy Hs span {height_hs_span:.2f} m" if height_hs_span is not None else "")
           + f", {dir_n} comparable swell hr"
           + (" — WIND-SEA ONLY, no swell to compare" if dir_n == 0 else ""))
    if height_n < TRUST_MIN_PAIRS or height_r != height_r:
        verdict, hreason = "INCONCLUSIVE", "height not assessable this window (flat / too short)"
    elif height_r >= TRUST_R_MIN:
        verdict, hreason = "PASS", f"height r {height_r:.3f} >= {TRUST_R_MIN} {_ev}"
    else:
        verdict, hreason = "FAIL", f"height r {height_r:.3f} < {TRUST_R_MIN} {_ev}"

    return {
        "verdict": verdict, "reason": hreason, "gate": "height (primary)",
        "height_r": height_r, "height_n": height_n, "height_hs_span": height_hs_span,
        "dir_bias_w": w_bias, "dir_circ_std_w": w_cs, "dir_bias_u": u_bias, "dir_circ_std_u": u_cs,
        "dir_rbar": rbar, "dir_rayleigh_p": rayleigh, "dir_coherent": coherent,
        "n_qualifying": dir_n, "n_events": n_events, "n_model_no_swell": n_model_no_swell,
        "tier": tier, "tier_thresholds": th, "dir_flag": dir_flag,
        "per_hour": per_hour, "records": records,
        "comparison": "energy-weighted model-swell-dir vs buoy-spectral-swell-dir",
    }


# --------------------------------------------------------------------------- #
# Rolling accumulation — continuous skill monitoring (not a one-shot verdict)  #
# --------------------------------------------------------------------------- #
def _history_path(wfo, buoy):
    return TRUST_HISTORY_DIR / f"{wfo}_{buoy}.jsonl"


def append_trust_history(wfo, buoy, records):
    """Append per-hour qualifying records (swell_trust_verdict['records']) to an append-only
    JSONL monitoring log, de-duped by timestamp. This is a NEW diagnostic artifact under
    forecast_data/ — never spots_enriched.json / the rating. Returns #records added."""
    if not records:
        return 0
    TRUST_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    path = _history_path(wfo, buoy)
    seen = set()
    if path.exists():
        for line in path.read_text().splitlines():
            try:
                seen.add(json.loads(line).get("t"))
            except (ValueError, TypeError):
                pass
    added = 0
    with path.open("a") as f:
        for r in records:
            if r.get("t") in seen:
                continue
            f.write(json.dumps(r) + "\n")
            seen.add(r.get("t"))
            added += 1
    return added


def _bank_records(wfo, buoy, res):
    """Bank this window's direction records toward the rolling verdict — UNLESS the height gate could
    not assess the window (verdict INCONCLUSIVE = 'height not assessable this window (flat / too
    short)'). Option (b): banking a direction event on a window the height gate can't verify lets
    low-energy, partition-contaminated seas accumulate toward a settled verdict on noise (the
    monitor/gate mismatch this fixes). Returns (n_added, skip_reason): skip_reason is None when the
    records were banked, else the height reason string (nothing written). append_trust_history does
    the de-duped disk write; this wrapper is the single guarded banking seam (and is unit-testable)."""
    if res.get("verdict") == "INCONCLUSIVE":
        return 0, res.get("reason") or "height not assessable this window (flat / too short)"
    return append_trust_history(wfo, buoy, res.get("records") or []), None


def load_trust_history(wfo, buoy, days=None, now_epoch_hour=None):
    """[records] from the history log, optionally the last *days* (needs now_epoch_hour)."""
    path = _history_path(wfo, buoy)
    if not path.exists():
        return []
    out = []
    for line in path.read_text().splitlines():
        try:
            out.append(json.loads(line))
        except ValueError:
            pass
    if days is not None and now_epoch_hour is not None:
        cut = now_epoch_hour - days * 24
        out = [r for r in out if r.get("t") is not None and r["t"] >= cut]
    return out


def _bootstrap_ci_circular(deltas, weights, *, n_boot=1000, alpha=0.05, seed=12345):
    """Percentile CI (lo, hi °) on the WEIGHTED circular mean bias, resampling comparable
    hours with replacement. Deterministic with *seed*; NaN if < 3 points. (Hours within an
    episode are autocorrelated, so this CI is optimistic — read it alongside n_events.)"""
    import random
    pts = [(d, w) for d, w in zip(deltas, weights) if d is not None and w and w > 0]
    if len(pts) < 3:
        return float("nan"), float("nan")
    center = _weighted_circ_stats([d for d, _ in pts], [w for _, w in pts])[0]
    rng = random.Random(seed)
    n = len(pts)
    unwrapped = []
    for _ in range(n_boot):
        samp = [pts[rng.randrange(n)] for _ in range(n)]
        b = _weighted_circ_stats([d for d, _ in samp], [w for _, w in samp])[0]
        unwrapped.append(((b - center + 180) % 360) - 180)
    unwrapped.sort()
    lo = unwrapped[int(alpha / 2 * n_boot)]
    hi = unwrapped[min(n_boot - 1, int((1 - alpha / 2) * n_boot))]
    return center + lo, center + hi


def rolling_trust_verdict(records, tier="point"):
    """DIRECTION trust from ACCUMULATED history: energy-weighted circular bias + circ_std over
    the records, independent-EVENT count, Rayleigh coherence, and a bootstrap CI on the bias.
    Verdict: ACCUMULATING if < TRUST_MIN_EVENTS independent events (hours are autocorrelated —
    events are the effective N); INCOHERENT if the residuals have no stable direction (Rayleigh
    p > TRUST_RAYLEIGH_P → R̄≈0, bias meaningless); else a spot-tier threshold on the weighted
    circ_std + |bias|. Pure."""
    th = SWELL_DIR_TIERS.get(tier, SWELL_DIR_TIERS["point"])
    deltas = [r.get("residual") for r in records]
    weights = [r.get("weight") for r in records]
    hours = sorted(r["t"] for r in records if r.get("t") is not None)
    events = _count_independent_events(hours)
    bias, cs, rbar, _sumw, npts = _weighted_circ_stats(deltas, weights)
    rp = _rayleigh_p(rbar, npts)
    ci_lo, ci_hi = _bootstrap_ci_circular(deltas, weights)
    base = {"n_hours": npts, "n_events": events, "dir_bias_w": bias, "dir_circ_std_w": cs,
            "dir_rbar": rbar, "dir_rayleigh_p": rp, "ci_lo": ci_lo, "ci_hi": ci_hi,
            "tier": tier, "tier_thresholds": th}
    if events < TRUST_MIN_EVENTS:
        return {**base, "verdict": "ACCUMULATING",
                "reason": f"{events} independent swell event(s) — need {TRUST_MIN_EVENTS}"}
    if rp > TRUST_RAYLEIGH_P:
        return {**base, "verdict": "INCOHERENT",
                "reason": f"residuals directionally incoherent (Rayleigh p={rp:.2f}); no stable bias"}
    ok = cs <= th["circ_std"] and abs(bias) <= th["bias"]
    return {**base, "verdict": ("PASS" if ok else "FAIL"),
            "reason": None if ok else (f"weighted circ_std {cs:.1f}° / bias {bias:+.1f}° vs tier "
                                       f"{tier} (≤{th['circ_std']}° / ±{th['bias']}°)")}


def window_records(records, days, now_epoch_hour):
    """The subset of *records* inside the last *days*, by the SAME rule load_trust_history uses
    (t >= now_epoch_hour - days*24) — and, like it, everything when either argument is None.

    Split out so the windows can be cut in memory from ONE load instead of re-reading the log per
    window, and so the cut is testable without touching disk. Pure."""
    if days is None or now_epoch_hour is None:
        return list(records)
    cut = now_epoch_hour - days * 24
    return [r for r in records if r.get("t") is not None and r["t"] >= cut]


def rolling_by_window(records, tier="point", now_epoch_hour=None, windows=None):
    """The rolling DIRECTION verdict for EVERY window in TRUST_ROLLING_DAYS, in tuple order —
    [{days, n_records, n_events, verdict}] — cut from one already-loaded record list.

    Iterates the tuple, so declaring a third window reports it with no further edit here. This is
    REPORTING: which window gates PASS/FAIL is TRUST_GATING_WINDOW_DAYS, decided by the caller.
    Pure."""
    out = []
    for d in (TRUST_ROLLING_DAYS if windows is None else windows):
        sub = window_records(records, d, now_epoch_hour)
        v = rolling_trust_verdict(sub, tier=tier)
        out.append({"days": d, "n_records": len(sub), "n_events": v.get("n_events", 0), "verdict": v})
    return out


def window_expiry_note(windows, gating_days=None):
    """One line when the WIDEST rolling window holds more independent events than the GATING one,
    else None.

    This is the distinction the counts exist to make: a zone stuck on ACCUMULATING because the sea
    has been quiet looks identical, in a single number, to one whose gating window is EXPIRING
    events faster than they arrive — the second can never settle however much swell it sees, and
    only the wider count reveals it. With TRUST_MIN_EVENTS at 5, ONE expired event is the
    difference between settling and not, so "materially more" is taken as strictly more; the note
    prints the actual counts rather than a verdict on them. Pure."""
    gating_days = TRUST_GATING_WINDOW_DAYS if gating_days is None else gating_days
    gate = next((w for w in windows if w["days"] == gating_days), None)
    wide = max(windows, key=lambda w: w["days"]) if windows else None
    if not gate or not wide or wide["days"] == gate["days"] or wide["n_events"] <= gate["n_events"]:
        return None
    note = (f"{wide['n_events']} events at {wide['days']}d vs {gate['n_events']} at {gate['days']}d "
            f"(+{wide['n_events'] - gate['n_events']}) — the {gate['days']}d gating window is "
            f"EXPIRING events faster than they arrive, not merely waiting on swell")
    if wide["n_events"] >= TRUST_MIN_EVENTS > gate["n_events"]:
        note += (f"; it would ALREADY have settled at {wide['days']}d "
                 f"({wide['n_events']} >= TRUST_MIN_EVENTS {TRUST_MIN_EVENTS})")
    return note


def trust_verdict(samples):
    """SUPERSEDED by swell_trust_verdict (the live gate is now partition-matched). Retained
    for the offline selftest + reference only — trust_check no longer calls it.

    samples = _pair_samples(...) output. Returns (verdict, r, circ_std, n, reason).
    HEIGHT correlation r over all overlapping hours; DIRECTION circ_std over model dirpw
    vs buoy MWD for every hour that carries both. INCONCLUSIVE (never PASS/FAIL) on too
    few overlapping hours or a flat buoy Hs spell (reason says which). Thresholds
    unchanged (r≥0.80, circ_std≤25). Pure — selftest-able offline."""
    n = len(samples)
    if n < TRUST_MIN_PAIRS:
        return "INCONCLUSIVE", float("nan"), float("nan"), n, "too few overlapping hours"
    bhs = [s["buoy_hs"] for s in samples]
    if max(bhs) - min(bhs) < TRUST_BUOY_RANGE_MIN_M:
        return ("INCONCLUSIVE", float("nan"), float("nan"), n,
                f"buoy Hs flat — 24h range < {TRUST_BUOY_RANGE_MIN_M} m")
    nhs = [s["nwps_hs"] for s in samples]
    r = _pearson(nhs, bhs)
    # DIRECTION — compare LIKE-FOR-LIKE. Model dirpw is "primary wave direction": the
    # PEAK direction of the TOTAL spectrum. The NWPS box CG1 GRIB carries NO swell
    # direction (its wave fields are ws/wdir/swh/shts/dirpw/perpw only — there is no
    # swdir), so dirpw is the only model direction we have and it describes the whole
    # sea. Its correct buoy counterpart is therefore MWD — the mean direction of the
    # buoy's TOTAL spectrum — NOT the buoy's swell-partition direction. Comparing dirpw
    # to buoy swell_dir (PR #47) mixed peak-of-total with a swell partition and produced
    # spurious FAILs (and the PR #54 swell-dominance filter that "fixed" it excluded
    # ~100% of hours). Do NOT re-introduce swell_dir here. (buoy swell_dir/swell_hs and
    # model shts are still fetched + shown in the diagnostic — for context, never verdict.)
    diffs = [s["nwps_dir"] - s["buoy_mwd"] for s in samples
             if s["buoy_mwd"] is not None and s["nwps_dir"] is not None
             and s["nwps_dir"] == s["nwps_dir"]]
    cs = _circ_std(diffs)
    if r >= TRUST_R_MIN and cs <= TRUST_CIRC_MAX:
        return "PASS", r, cs, n, None
    return "FAIL", r, cs, n, None


def _buoy_hourly(buoy_id):
    """{hour_bucket: {"hs", "mwd", "swell_dir", "swell_hs"}} from the buoy's NDBC
    realtime2 feeds, reusing the pipeline's fetcher + parser (lazy import; needs
    requests). The std .txt table gives hs (WVHT) + MWD; the .spec spectral summary
    supplies the swell PARTITION — swell_dir (SwD → swell_dir_deg) and swell_hs (SwH →
    swell_height_m). The swell fields are carried for the DIAGNOSTIC context only: the
    verdict compares model dirpw to the buoy MWD, never to swell_dir (see trust_verdict).
    The .spec feed is SUPPLEMENTARY: if it is missing / unpublished / unparseable,
    swell_dir and swell_hs are None and nothing breaks. Returns None only when the std
    feed itself is unavailable."""
    try:
        from .buoys import _fetch_text, _parse_realtime2, _STD_FIELDS, _SPEC_FIELDS
    except Exception:  # noqa: BLE001
        return None
    txt = _fetch_text(f"https://www.ndbc.noaa.gov/data/realtime2/{buoy_id.upper()}.txt",
                      buoy_id, "std", use_cache=False)
    if not txt:
        return None
    # Supplementary spectral swell partition (dir + height), keyed by hour bucket — for
    # the diagnostic only. Any failure leaves spec_by_hour empty → swell fields None.
    spec_by_hour = {}
    try:
        spec = _fetch_text(f"https://www.ndbc.noaa.gov/data/realtime2/{buoy_id.upper()}.spec",
                           buoy_id, "spec", use_cache=False)
        if spec:
            for o in _parse_realtime2(spec, _SPEC_FIELDS):
                t = _iso_to_epoch(o.get("time"))
                if t is None:
                    continue
                swd, swh = o.get("swell_dir_deg"), o.get("swell_height_m")
                if swd is not None or swh is not None:
                    spec_by_hour[int(t // 3600)] = (float(swd) if swd is not None else None,
                                                    float(swh) if swh is not None else None)
    except Exception:  # noqa: BLE001
        spec_by_hour = {}
    out = {}
    for o in _parse_realtime2(txt, _STD_FIELDS):
        hs, mwd, t = o.get("wave_height_m"), o.get("mean_wave_dir_deg"), _iso_to_epoch(o.get("time"))
        if hs is not None and t is not None and hs < 90:
            hb = int(t // 3600)
            swd, swh = spec_by_hour.get(hb, (None, None))
            out[hb] = {"hs": float(hs), "mwd": float(mwd) if mwd is not None else None,
                       "swell_dir": swd, "swell_hs": swh}
    return out or None


def _node_diag(cyc, blat, blng, i, j, dist_km, radius_km=5.0):
    """DIAGNOSTIC ONLY — surface WHERE trust_check sampled; does NOT change what it
    samples, the correlated variables, or any verdict math. Given the plain-nearest
    wet cell (i, j) trust_check picked and its distance from the buoy, report that
    node's lat/lng and compare it to a SEAWARD-aware pick from the SAME wet-node set.
    'Seaward' is inferred from the grid mask alone (no coastline needed): the shoreward
    bearing points at the nearest LAND (masked) cell, so the seaward half-plane is the
    opposite ±90° — reusing select_node's half-plane idea against the buoy point. Also
    counts wet cells within *radius_km* of the buoy (a cluttered / landmask-adjacent
    indicator). Pure and read-only; returns a plain dict for the CLI to print."""
    lats, lons, mask = cyc["lats"], cyc["lons"], cyc["mask"]
    node_lat, node_lng = float(lats[i, j]), float(lons[i, j])
    wet = _wet_nodes(lats, lons, mask)

    def _d(lat, lng):
        return _haversine_km(blat, blng, lat, lng)

    diag = {"lat": node_lat, "lng": node_lng, "dist_km": dist_km, "radius_km": radius_km,
            "n_within_radius": sum(1 for w in wet if _d(w[0], w[1]) <= radius_km),
            "sampled_is_seaward": None, "seaward_differs": None,
            "seaward_nearest_lat": None, "seaward_nearest_lng": None,
            "seaward_nearest_dist_km": None, "shore_bearing": None,
            "seaward_bearing": None, "land_dist_km": None, "reason": None}
    land = [(float(lats[a, b]), float(lons[a, b]))
            for a in range(lats.shape[0]) for b in range(lats.shape[1]) if mask[a, b]]
    if not land:
        diag["reason"] = "no land/masked cells in grid — seaward direction undefined"
        return diag
    lnd = min(land, key=lambda p: _d(p[0], p[1]))
    shore_brg = _bearing(blat, blng, lnd[0], lnd[1])
    sea_brg = (shore_brg + 180.0) % 360.0
    sea = [w for w in wet if _ang_within(_bearing(blat, blng, w[0], w[1]), sea_brg, 90)]
    sea_nearest = min(sea, key=lambda w: _d(w[0], w[1])) if sea else None
    diag.update({
        "shore_bearing": shore_brg, "seaward_bearing": sea_brg, "land_dist_km": _d(lnd[0], lnd[1]),
        "sampled_is_seaward": _ang_within(_bearing(blat, blng, node_lat, node_lng), sea_brg, 90),
        "seaward_nearest_lat": (sea_nearest[0] if sea_nearest else None),
        "seaward_nearest_lng": (sea_nearest[1] if sea_nearest else None),
        "seaward_nearest_dist_km": (_d(sea_nearest[0], sea_nearest[1]) if sea_nearest else None),
        "seaward_differs": (bool(sea_nearest) and (sea_nearest[2], sea_nearest[3]) != (i, j)),
    })
    return diag


def _pair_samples(series, buoy):
    """Join the model series (valid_hour → {"hs","dir","shts","lead"}) to the buoy obs
    (valid_hour → {"hs","mwd","swell_dir","swell_hs"}) on shared hour buckets — one dict
    per shared hour. The DIRECTION used for the verdict is the buoy MWD (see
    trust_verdict for WHY model dirpw pairs with MWD, not swell_dir). The buoy
    swell_dir/swell_hs and the model swell height (shts) are carried for the DIAGNOSTIC
    table only, never for the verdict. Pure/offline."""
    samples = []
    for t in sorted(series):
        if t not in buoy:
            continue
        m, b = series[t], buoy[t]
        samples.append({"t": t, "nwps_hs": m["hs"], "nwps_dir": m["dir"], "model_shts": m.get("shts"),
                        "buoy_hs": b["hs"], "buoy_mwd": b.get("mwd"),
                        "buoy_swell_dir": b.get("swell_dir"), "buoy_swell_hs": b.get("swell_hs")})
    return samples


def trust_check(wfo, buoy_id, blat, blng, n_cycles=4, **kw):
    """Live NWPS-vs-buoy trust gate (Mac), rebuilt PARTITION-MATCHED + energy-preconditioned.
    kw: node_select ('nearest' default | 'seaward' | 'deepest') + node_radius_km — READ-ONLY
    node variants for the depth experiment; the production gate always uses 'nearest'.
    Assembles at the buoy's node across recent cycles (shortest lead per valid hour): the
    CG1 total height (swh) + 10 m wind (ws/wdir), and the CG0_Trkng per-system swell
    (hs/tp/dir). Fetches the buoy's SPECTRAL swell (degree-valued swell_dir + Hs_swell +
    swell fraction; the band split is ndbc_spectral's default — a published Sep_Freq when one
    is valid, else the fixed 0.125 Hz cutoff, NOT wave age, which over-assigned to swell)
    and its WVHT, then judges via swell_trust_verdict: DIRECTION = the model's dominant
    swell system vs the buoy spectral swell_dir over swell-present hours; HEIGHT = model swh
    vs buoy WVHT. Needs NOMADS+NDBC+eccodes. Returns swell_trust_verdict(...) plus read-only
    node diagnostics ('node', 'trkng_why', 'per_hour', 'samples')."""
    from . import nwps_trkng as _trk     # lazy: avoids an import cycle; keeps --selftest light
    from . import ndbc_spectral as _spec
    node_select = kw.get("node_select", "nearest")   # READ-ONLY variant for the depth experiment
    node_radius_km = kw.get("node_radius_km", 6.0)
    tier = kw.get("tier", "point")                    # spot directional-sensitivity tier
    buoy = _buoy_hourly(buoy_id)
    if not buoy:
        return {"verdict": "INCONCLUSIVE", "reason": "buoy feed unavailable", "n_qualifying": 0,
                "dir_circ_std": float("nan"), "dir_bias": float("nan"), "height_r": float("nan"),
                "node": None, "trkng_why": None, "per_hour": [], "samples": []}
    now = datetime.datetime.now(datetime.timezone.utc)
    series = {}   # valid_hour -> {swh, dirpw, shts, ws, wdir, systems, lead}
    node = None   # DIAGNOSTIC: captured once — static grid/mask → same node every cycle
    trkng_why = None
    for date, cc, url in recent_cycles(wfo, n_cycles, _region_for(wfo)):
        cyc = load_cycle(wfo, (date, cc, url))
        elapsed = int((now - cyc["cycle_dt"]).total_seconds() // 3600)
        if elapsed < 0:
            continue
        picked = _pick_cell(cyc, blat, blng, node_select, node_radius_km)
        if picked is None:
            continue
        i, j, nlat, nlng, pdist = picked
        if node is None:
            node = _node_diag(cyc, blat, blng, i, j, pdist)   # read-only; reflects the sampled node
        # CG0_Trkng per-system swell at the SAME node (remapped into the coarser grid). If the
        # Trkng file is missing for a cycle, height still works and those hours just carry no
        # systems (they won't qualify for the direction stat).
        trk = ti = tj = None
        try:
            trk = _trk.load_trkng_cycle(wfo, (date, cc, url))
            ti, tj, why = _trk.trkng_node(trk, cyc, nlat, nlng)
            trkng_why = trkng_why or why
        except Exception as e:  # noqa: BLE001
            trkng_why = trkng_why or f"Trkng unavailable ({type(e).__name__})"
        for fh in cyc["steps"]:
            if fh > elapsed:
                continue
            swh = _node_value(cyc, "swh", fh, i, j)
            if swh is None:
                continue
            valid = int((cyc["cycle_dt"] + datetime.timedelta(hours=fh)).timestamp() // 3600)
            if valid in series and series[valid]["lead"] <= fh:
                continue
            systems = _trk.trkng_systems_at(trk, ti, tj, fh) if (trk is not None and ti is not None) else []
            series[valid] = {
                "swh": swh, "dirpw": _node_value(cyc, "dirpw", fh, i, j),
                "shts": _node_value(cyc, "shts", fh, i, j),
                "ws": _node_value(cyc, "ws", fh, i, j), "wdir": _node_value(cyc, "wdir", fh, i, j),
                "systems": systems, "lead": fh}
    # Buoy spectral swell: wave-age split on the buoy's own wind, else the MODEL wind at the
    # node just assembled (wave-only buoys like 44095 report MM wind).
    model_wind = {v: (s["ws"], s["wdir"]) for v, s in series.items()
                  if s["ws"] is not None and s["wdir"] is not None}
    spectral = _spec.by_hour(buoy_id, model_wind=model_wind)
    samples = []
    for v, s in series.items():
        if v not in buoy:
            continue
        b, spx = buoy[v], spectral.get(v)
        samples.append({
            "t": v, "model_systems": s["systems"], "model_swh": s["swh"], "model_shts": s["shts"],
            "model_ws": s["ws"], "model_wdir": s["wdir"],   # for the model-system wave-age split
            "buoy_wvht": b["hs"],
            "buoy_swell_dir": (spx["swell_dir"] if spx else None),
            "buoy_hs_swell": (spx["hs_swell"] if spx else None),
            "buoy_frac": (spx["swell_frac"] if spx else None),
            "buoy_windsea_dir": (spx["windsea_dir"] if spx else None),   # side-by-side diag
            "buoy_hs_windsea": (spx["hs_windsea"] if spx else None),
            "wind_used": (spx.get("wind_used") if spx else None),
            "dirpw": s["dirpw"], "buoy_mwd": b.get("mwd"),   # OLD metric, kept for the reverify side-by-side
            "buoy_coarse_swd": b.get("swell_dir")})           # coarse .spec SwD — for the buoy-side sanity check
    res = swell_trust_verdict(samples, tier=tier)
    res.update({"node": node, "trkng_why": trkng_why, "samples": samples})
    return res


# --------------------------------------------------------------------------- #
# --validate / --trustcheck (Mac; offline-degrading like mop.validate_batch)   #
# --------------------------------------------------------------------------- #
def _load_pilot_spots():
    """Pilot spots from scripts/okx_pilot.json (the probe's input) joined to the
    roster for full fields, or a 3-spot fallback. (spot dicts, note)."""
    pj = SCRIPTS_DIR / "okx_pilot.json"
    roster = {_slug(s["name"]): s for s in json.loads(ENRICHED.read_text())} if ENRICHED.exists() else {}
    if pj.exists():
        pilots = json.loads(pj.read_text())
        out = []
        for p in pilots:
            base = roster.get(p.get("slug"), {})
            s = dict(base); s.update(p); s.setdefault("nwps_wfo", "okx")
            out.append(s)
        return out, None
    return [{"slug": "rockaway-beach", "name": "Rockaway Beach", "lat": 40.58329, "lng": -73.806882,
             "nwps_wfo": "okx", "orientation_deg": 160.0, "swell_window_arcs": [{"min": 90, "max": 230}]},
            {"slug": "lido-beach", "name": "Lido Beach", "lat": 40.583714, "lng": -73.606746,
             "nwps_wfo": "okx", "orientation_deg": 170.0, "swell_window_arcs": [{"min": 100, "max": 240}]},
            {"slug": "montauk-point", "name": "Montauk Point", "lat": 41.071004, "lng": -71.855135,
             "nwps_wfo": "okx", "orientation_deg": 130.0, "swell_window_arcs": [{"min": 40, "max": 220}]}], \
        "okx_pilot.json not found — 3-spot fallback"


def _load_roster_spots(slugs):
    """Load specific spots straight from spots_enriched.json by slug (read-only),
    independent of the pilot file. Each record keeps its real fields — lat/lng,
    orientation_deg, swell_window_arcs and its OWN nwps_wfo tag (nothing is
    force-stamped). Raises ValueError naming any slug not in the roster, so a
    typo / absent spot is loud instead of silently dropped."""
    if not ENRICHED.exists():
        raise FileNotFoundError(f"--batch needs {ENRICHED}; not found")
    roster = {}
    for s in json.loads(ENRICHED.read_text()):
        roster.setdefault(_slug(s.get("name")), s)
    out, missing = [], []
    for raw in slugs:
        sl = raw.strip()
        if not sl:
            continue
        s = roster.get(sl)
        if s is None:
            missing.append(sl)
        else:
            out.append(s)
    if missing:
        raise ValueError(f"--batch: slug(s) not found in spots_enriched.json: {', '.join(missing)}")
    return out


def _load_wfo_spots(wfo):
    """Every spot in spots_enriched.json tagged nwps_wfo == *wfo* (read-only), each keeping its
    real fields. This is the --validate roster when --wfo is given WITHOUT --batch, so a run can
    never silently validate a different region's spots against this grid (the old default was the
    okx_pilot.json 38-spot set regardless of --wfo). Empty list if no spot carries the tag."""
    if not ENRICHED.exists():
        raise FileNotFoundError(f"--validate --wfo {wfo} needs {ENRICHED}; not found")
    return [s for s in json.loads(ENRICHED.read_text()) if s.get("nwps_wfo") == wfo]


def _validate_roster(batch, wfo):
    """Resolve the --validate roster AND the grid to fetch, returning
    (spots, roster_source_label, grid_wfo). Precedence: --batch wins (slugs from
    spots_enriched.json); else --wfo selects every nwps_wfo==wfo spot; else (no --wfo at all)
    the okx_pilot.json set. The label is printed at the top of the run so it is impossible to
    validate and not know which spots were in it. Pure/offline (reads local files only)."""
    if batch:
        want = batch.split(",") if isinstance(batch, str) else list(batch)
        spots = _load_roster_spots(want)
        return spots, f"--batch ({len(spots)} spots)", (wfo or "okx")
    if wfo:
        spots = _load_wfo_spots(wfo)
        return spots, f"nwps_wfo == '{wfo}' ({len(spots)} spots)", wfo
    spots, note = _load_pilot_spots()
    if note:
        print(note)
    return spots, f"scripts/okx_pilot.json ({len(spots)} spots)", "okx"


def _warn_if_roster_stale(max_age_days=2):
    """Non-blocking: warn once if the local NDBC roster files are older than
    *max_age_days*. Never fails — a missing / unstat-able file is just skipped."""
    import time
    from ..config import NDBC_STATIONS_XML, NDBC_LATEST_OBS_TXT
    now = time.time()
    stale = []
    for p in (NDBC_STATIONS_XML, NDBC_LATEST_OBS_TXT):
        try:
            age = (now - p.stat().st_mtime) / 86400.0
        except OSError:
            continue
        if age > max_age_days:
            stale.append((p.name, age))
    if stale:
        name, age = max(stale, key=lambda x: x[1])
        print(f"warning: NDBC roster is stale — {name} is {age:.1f} days old; refresh "
              "activestations.xml / latest_obs.txt for current station metadata.")


def _buoy_latlng(buoy_id, *, _active=None, _reporting=None):
    """(lat, lng) for an NDBC buoy id, resolved from the FULL active-station metadata
    (enrichment.geodata.load_ndbc_active_stations — every active station with
    coordinates), NOT the wave-reporting-only subset. Coordinates are static metadata
    and must not depend on a momentary WVHT reading, so a real buoy that isn't
    transmitting a wave height right now still resolves and the trust check can
    proceed. Raises KeyError only if the id is absent from the full active list
    (genuinely unknown) — never falls back to typed coordinates. Prints a
    non-blocking note if the buoy resolves but isn't in the wave-reporting subset.
    *_active* / *_reporting* are injectable station lists for offline tests."""
    from ..enrichment.geodata import load_ndbc_active_stations, load_ndbc_wave_stations
    if _active is None:
        _warn_if_roster_stale()
    bid = str(buoy_id).lower()
    active = {s["id"]: s for s in (_active if _active is not None else load_ndbc_active_stations())}
    st = active.get(bid)
    if st is None:
        raise KeyError(f"buoy {buoy_id!r} not in the NDBC active-station list "
                       "(activestations.xml) — genuinely unknown; cannot resolve its lat/lng")
    reporting = {s["id"] for s in (_reporting if _reporting is not None else load_ndbc_wave_stations())}
    if reporting and bid not in reporting:
        print(f"note: {buoy_id} resolved from station metadata but isn't currently reporting "
              "waves; trust check may return INCONCLUSIVE.")
    return st["lat"], st["lng"]


def validate_out_path(wfo, batch=None):
    """Where --validate writes its diagnostic dump. Pure — returns a path, touches nothing.

    FULL-REGION runs (no --batch) keep scripts/nwps_{wfo}_validate_out.json, unchanged. That
    file is the ONLY record of a region's non-OK outcomes — the FAR and NO_WET_CELL rows never
    appear anywhere else, since only placed-OK spots get promoted into the assignments file.

    --batch runs write scripts/nwps_{wfo}_BATCH_validate_out.json instead. A one- or two-spot
    batch dump landing on the region path silently destroyed that record THREE TIMES before
    this split: box and phi in PR #175, sgx in PR #183. Nothing failed and nothing warned —
    the file simply came back with two entries where it used to have a region.

    Batch dumps still overwrite EACH OTHER per WFO, deliberately: a batch dump is scratch and
    the thing worth protecting is the region file."""
    stem = f"nwps_{wfo}_batch_validate_out" if batch else f"nwps_{wfo}_validate_out"
    return SCRIPTS_DIR / f"{stem}.json"


def write_validate_out(wfo, batch, placed, outcomes):
    """Write the --validate diagnostic dump; return (path, kind). Split out of validate_batch so
    the batch/full-region routing is testable without a live cycle (see test_guardrails)."""
    path = validate_out_path(wfo, batch)
    kind = "BATCH" if batch else "full-region"
    path.write_text(json.dumps(
        {"_comment": f"{wfo} --validate {kind} diagnostic. 'spots' = placed-OK (node fields); "
         "'outcomes' = every spot's category (OK / FAR / NO_WET_CELL — placement is purely "
         "geometric; the former DEAD/OFFWIN condition checks were removed). NOT the apply input — "
         "review, then promote OK spots into scripts/nwps_okx_assignments.json by hand."
         + (" THIS IS A BATCH DUMP covering only the --batch slugs, NOT the whole region; the "
            f"full-region file nwps_{wfo}_validate_out.json is a separate path and was not "
            "touched." if batch else ""),
         "grid_wfo": wfo, "batch": bool(batch), "spots": placed, "outcomes": outcomes}, indent=2))
    return path, kind


def validate_batch(batch=None, wfo=None):
    """Part C — fetch one *wfo* cycle, place each spot's seaward node, sample its f000
    swh/perpw/dirpw, print placement verdict + NWPS★ vs the orientation fallback★, plus a
    forced-empty test. The ROSTER of spots (see _validate_roster) is: --batch slugs if given;
    else, if *wfo* is given, every nwps_wfo==wfo spot in spots_enriched.json; else the
    okx_pilot.json set (only when --wfo is absent entirely). The roster source + count is
    printed at the top so a run can never silently validate the wrong region's spots against a
    grid. Writes a DIAGNOSTIC dump only, routed by validate_out_path — full-region runs to
    scripts/nwps_{wfo}_validate_out.json, --batch runs to nwps_{wfo}_batch_validate_out.json so a
    batch cannot clobber the region file (records every
    spot's outcome: OK / FAR / NO_WET_CELL); it does NOT touch the curated apply
    input scripts/nwps_okx_assignments.json (promote by hand after review). Mac-only (NOMADS);
    degrades to a clear message offline."""
    spots, roster_src, wfo = _validate_roster(batch, wfo)
    print(f"roster: {roster_src}")
    print(f"NWPS {wfo.upper()} validate — {len(spots)} spots\n")
    try:
        cycle = load_cycle(wfo)
    except Exception as e:  # noqa: BLE001  NOMADS/cfgrib unavailable here
        print(f"⚠ could not load a {wfo.upper()} cycle ({type(e).__name__}: {e}). "
              "Live NOMADS + cfgrib/eccodes needed — run on the Mac. Offline logic is covered by --selftest.")
        return 0
    print(f"cycle {cycle['cycle_dt']:%Y-%m-%d %HZ}  ·  {len(cycle['steps'])} steps  ·  grid {cycle['lats'].shape}")
    spacing = grid_spacing_km(cycle)
    far_cap = grid_far_cap_km(cycle)
    floored = far_cap <= FAR_CAP_FLOOR_KM + 1e-9
    print(f"grid node spacing ≈ {spacing:.2f} km  →  FAR placement cap = {far_cap:.2f} km  "
          f"({'legacy 3.0 floor (fine grid — unchanged)' if floored else f'grid-aware = {FAR_CAP_MULT}× spacing'})\n")

    # Real fallback baseline = the orientation path via the EXISTING NWPS fetcher
    # (interpret.compute_ratings), so NWPS★ (nearshore node) is asserted against
    # what the spot gets today. Lazy + degrades to NWPS-only sanity if unavailable.
    fb = None
    try:
        from ..interpret import compute_ratings
        from . import nwps as nwps_mod, tides as tides_mod
        # Redirect the fetchers' diagnostic writes to scratch files so a Mac
        # --validate can never clobber the real forecast_data/{nwps,tides}.json.
        _saved = (nwps_mod.NWPS_FORECAST_FILE, tides_mod.TIDES_FORECAST_FILE)
        nwps_mod.NWPS_FORECAST_FILE = _saved[0].parent / "nwps_validate_scratch.json"
        tides_mod.TIDES_FORECAST_FILE = _saved[1].parent / "tides_validate_scratch.json"
        try:
            fb = compute_ratings(spots, nwps_mod.fetch(spots), tides_mod.fetch(spots), {}, {})
        finally:
            nwps_mod.NWPS_FORECAST_FILE, tides_mod.TIDES_FORECAST_FILE = _saved
    except Exception as e:  # noqa: BLE001
        print(f"(fallback baseline unavailable — {type(e).__name__}; showing NWPS sanity only)\n")

    print(f"  {'slug':22}{'node_km':>8}{'swh':>6}{'per':>6}{'dir':>6}  verdict   NWPS★   fb★")
    placed, outcomes = [], []
    for s in spots:
        slug = _slug(s["name"]); wfo_tag = s.get("nwps_wfo", wfo)
        sel = select_node(cycle, s["lat"], s["lng"], s.get("orientation_deg"))
        if sel is None:
            # No water anywhere in the fetched grid near this spot: the spot is
            # OUTSIDE this WFO's marine domain. A DISTINCT outcome from FAR (which
            # found a cell but beyond the grid's far cap) — NO_WET_CELL means
            # "retry against another WFO grid", not "no usable cell nearby".
            print(f"  {slug:22}{'—':>8}  NO_WET_CELL  (no water in {wfo} grid — outside its marine domain)")
            outcomes.append({"slug": slug, "name": s["name"], "nwps_wfo": wfo_tag,
                             "grid_wfo": wfo, "outcome": "NO_WET_CELL",
                             "domain_miss": _is_domain_miss("NO_WET_CELL")})
            continue
        i, j, nlat, nlng, dkm, moved = sel
        swh = _node_value(cycle, "swh", 0, i, j); per = _node_value(cycle, "perpw", 0, i, j)
        dpw = _node_value(cycle, "dirpw", 0, i, j); shts = _node_value(cycle, "shts", 0, i, j)
        v = placement_verdict(dkm, per, dpw, s.get("swell_window_arcs", []), far_cap_km=far_cap)
        # match the fallback's f000 valid hour for a same-hour comparison
        wm = tm = 1.0; fbstar = None
        ents = (fb or {}).get(s["name"]) or []
        k0 = int(cycle["cycle_dt"].timestamp() // 3600)
        for e in ents:
            t = _iso_to_epoch(e.get("valid_time"))
            if t is not None and int(t // 3600) in (k0, k0 - 1, k0 + 1):
                wm, tm, fbstar = e.get("wind_mult", 1.0), e.get("tide_mult", 1.0), e.get("stars")
                break
        st, *_ = nwps_stars(swh, per, dpw, shts, s.get("orientation_deg"), wm, tm,
                            arcs=s.get("swell_window_arcs"),
                            optimal=s.get("optimal_swell_dir"))
        sval = f"{st:.1f}" if st is not None else "—"
        fval = f"{fbstar:.1f}" if fbstar is not None else "—"
        dm = _is_domain_miss(v)
        print(f"  {slug:22}{dkm:8.2f}{(swh or 0):6.1f}{(per or 0):6.1f}{(dpw or 0):6.0f}"
              f"  {v:8}{sval:>6}{fval:>6}{'  *' if moved else ''}{'  domain-miss' if dm else ''}")
        # FAR = nearest seaward wet cell beyond the grid's far cap (spot outside this WFO's
        # nearshore nest — a domain miss, like NO_WET_CELL). The outcome set is now OK / FAR /
        # NO_WET_CELL: DEAD (period floor) and OFFWIN (direction outside the window) were removed
        # because they are sea-state conditions, not geography — see PLACEMENT_IS_GEOMETRY.
        # domain_miss is the explicit rollup the grid-edge mop-up filters on.
        outcomes.append({"slug": slug, "name": s["name"], "nwps_wfo": wfo_tag, "grid_wfo": wfo,
                         "outcome": v, "domain_miss": dm, "nwps_node_distance_m": round(dkm * 1000),
                         "nwps_node_lat": round(nlat, 5), "nwps_node_lng": round(nlng, 5)})
        if v == "OK":
            placed.append({"slug": slug, "name": s["name"], "nwps_wfo": wfo_tag,
                           "nwps_grid": "CG1", "nwps_node_lat": round(nlat, 5), "nwps_node_lng": round(nlng, 5),
                           "nwps_node_distance_m": round(dkm * 1000), "nwps_buoy_id": s.get("nwps_buoy_id")})
    # forced-empty test — fallback must engage cleanly
    if spots:
        tname = spots[0]["name"]
        test = {tname: [dict(valid_time="2026-06-27T12:00:00Z", stars=2.5, wind_mult=1.0, tide_mult=1.0)]}
        st = apply_nwps_overrides(test, [dict(spots[0], swell_window_source="nwps")], _fetch=lambda _s: None)
        print(f"\nforced-empty test: fed={st['fed']} fell_back={st['fell_back']} errored={st['errored']}; "
              f"base preserved: {'YES' if test[tname][0]['stars'] == 2.5 else 'NO'}")
    if outcomes:
        validate_out, kind = write_validate_out(wfo, batch, placed, outcomes)
        n_ok = len(placed); n_other = len(outcomes) - n_ok
        print(f"\nwrote {validate_out} ({n_ok} placed-OK, {n_other} other outcomes on the {wfo} grid) — "
              f"{kind} diagnostic only. The apply input scripts/nwps_okx_assignments.json is left untouched; "
              "review + promote OK spots into it, then --trustcheck and apply_nwps_assignments --apply once "
              "the gate PASSES.")
        if batch:
            # say it in the run output rather than leaving it to be discovered later
            print(f"  ^ BATCH dump — covers only the {len(outcomes)} --batch spot(s). The full-region file "
                  f"{validate_out_path(wfo)} was NOT written and still holds the last whole-{wfo} run, "
                  "including its FAR / NO_WET_CELL rows.")
    print("\nfb★ = the orientation-path baseline (interpret.compute_ratings via the existing NWPS "
          "fetcher) at the same f000 hour, when NWPS+tides fetch succeeds — NWPS★ should be sane "
          "next to it. Trust the WFO (--trustcheck) before apply_nwps_assignments --apply.")
    return 0


def _selftest():
    ok = True

    def check(n, c):
        nonlocal ok; ok = ok and c; print(f"  {'PASS' if c else 'FAIL'}  {n}")

    # window + geometry helpers
    # _in_arcs is gone; the shared interpret.in_any_arc replaces it. These fixtures carry no
    # span, so arc_pad_deg returns 0 and the bounds read as sector edges — same answers as the
    # deleted copy gave, which is why these two lines are unchanged in meaning.
    check("in_any_arc simple", in_any_arc(120, [{"min": 90, "max": 230}]) and not in_any_arc(300, [{"min": 90, "max": 230}]))
    check("in_any_arc wrap 0/360", in_any_arc(10, [{"min": 340, "max": 30}]) and not in_any_arc(180, [{"min": 340, "max": 30}]))
    check("ang_within ±90", _ang_within(200, 180, 90) and not _ang_within(350, 180, 90))
    check("bearing east ≈90", abs(_bearing(40, -74, 40, -73) - 90) < 1)

    # placement verdict — PURELY GEOMETRIC (OK / FAR only; see PLACEMENT_IS_GEOMETRY)
    check("verdict FAR", placement_verdict(5.0, 10, 150, []) == "FAR")
    check("verdict OK", placement_verdict(1.0, 10, 150, [{"min": 90, "max": 230}]) == "OK")
    check("sub-floor period no longer DEAD (a condition, not geography)",
          placement_verdict(1.0, 2.0, 150, []) == "OK")
    check("off-window direction no longer OFFWIN (Pipeline in August)",
          placement_verdict(1.0, 10, 300, [{"min": 90, "max": 230}]) == "OK")
    check("placement needs no spectrum at all (per/dirpw absent)",
          placement_verdict(1.0, None, None, [{"min": 90, "max": 230}]) == "OK")
    check("domain_miss rollup", _is_domain_miss("FAR") and _is_domain_miss("NO_WET_CELL")
          and not _is_domain_miss("OK")
          and not _is_domain_miss("OFFWIN") and not _is_domain_miss("DEAD"))   # retired names still False

    # grid-aware FAR cap — placement_verdict takes a per-grid cap (default = legacy 3.0 floor).
    check("default cap = legacy 3.0 (back-compat, fine grid)",
          placement_verdict(3.35, 10, 150, []) == "FAR" and placement_verdict(5.0, 10, 150, []) == "FAR")
    check("fine grid (cap 3.0): 3.35 km still FAR", placement_verdict(3.35, 10, 150, [], far_cap_km=3.0) == "FAR")
    check("coarse grid (cap 5.0): 3.35 km now OK", placement_verdict(3.35, 10, 150, [], far_cap_km=5.0) == "OK")
    check("coarse grid (cap 5.0): 4.08 km now OK (santa-monica-pier case)",
          placement_verdict(4.08, 10, 150, [], far_cap_km=5.0) == "OK")
    check("real gap 37–59 km FAR on any grid",
          placement_verdict(37.0, 10, 150, [], far_cap_km=6.0) == "FAR"
          and placement_verdict(59.0, 10, 150, [], far_cap_km=5.0) == "FAR")
    check("widening the cap never flips OK→FAR (monotone): 1.71 km OK on every grid",
          placement_verdict(1.71, 10, 150, [], far_cap_km=3.0) == "OK"
          and placement_verdict(1.71, 10, 150, [], far_cap_km=5.0) == "OK")

    # grid_spacing_km + grid_far_cap_km from a grid's own coordinate vectors (regular lat/lon).
    def _mkgrid(lat0, dlat, lng0, dlng, n=6):
        lat1d = np.array([lat0 + k * dlat for k in range(n)])
        lng1d = np.array([lng0 + k * dlng for k in range(n)])
        la, lo = np.meshgrid(lat1d, lng1d, indexing="ij")
        return {"lats": la, "lons": lo}

    fine = _mkgrid(40.55, 0.01631, -73.95, 0.02112)     # okx-like ≈1.8 km
    check("grid_spacing okx-like ≈1.8 km", 1.7 <= grid_spacing_km(fine) <= 1.9)
    check("far cap fine grid → floored 3.0", abs(grid_far_cap_km(fine) - 3.0) < 1e-6)
    box = _mkgrid(41.4, 0.01806, -70.6, 0.02370)        # box-like ≈2.0 km (still floors)
    check("far cap box-like (≈2.0 km) → floored 3.0", abs(grid_far_cap_km(box) - 3.0) < 1e-6)
    coarse = _mkgrid(37.0, 0.02256, -122.5, 0.02750)    # mtr-like ≈2.48 km
    check("grid_spacing mtr-like ≈2.5 km", 2.35 <= grid_spacing_km(coarse) <= 2.6)
    check("far cap mtr-like → widened ≈3.7", 3.5 <= grid_far_cap_km(coarse) <= 3.9)
    lox = _mkgrid(34.0, 0.02710, -119.5, 0.03250)       # lox-like ≈3.0 km
    cap_lox = grid_far_cap_km(lox)
    check("grid_spacing lox-like ≈3.0 km", 2.8 <= grid_spacing_km(lox) <= 3.3)
    check("far cap lox → ≈4–6 km and places the 4.08 km FAR", 4.0 <= cap_lox <= 6.0 and cap_lox > 4.08)
    check("degenerate grid → floor 3.0", grid_far_cap_km({"lats": np.array([[40.0]]), "lons": np.array([[-73.0]])}) == 3.0)

    # cfgrib land semantics: mask = np.isnan(swh) must drive wet-cell selection
    # (was a masked-array test under pygrib). Load-bearing for the seaward snap.
    la = np.array([[40.0, 40.0], [41.0, 41.0]])
    lo = np.array([[-74.0, -73.0], [-74.0, -73.0]])
    swh_grid = np.array([[1.2, np.nan], [np.nan, 0.8]])   # only (0,0) and (1,1) wet
    wet = _wet_nodes(la, lo, np.isnan(swh_grid))
    check("NaN land mask → only wet cells", sorted((i, j) for _, _, i, j in wet) == [(0, 0), (1, 1)])
    row_lat = np.array([[40.0, 40.0, 40.0]]); row_lng = np.array([[-73.02, -73.01, -73.00]])
    only_east = {"lats": row_lat, "lons": row_lng, "mask": np.isnan(np.array([[np.nan, np.nan, 1.5]]))}
    sel = select_node(only_east, 40.0, -73.0, None)
    check("select_node snaps past NaN land to the wet cell",
          sel is not None and (sel[0], sel[1]) == (0, 2))

    # nwps_stars mirrors mop_stars
    st, face, dg, cm, pq = nwps_stars(2.0, 12, 160, 1.9, 160, 1.0, 1.0)
    st_off, *_ = nwps_stars(2.0, 12, 70, 1.9, 160, 1.0, 1.0)   # 90° off-axis
    check(f"nwps_stars on-axis > off-axis ({st} > {st_off})", st > st_off)
    check("nwps_stars unusable -> None", nwps_stars(None, 12, 160, 1.9, 160)[0] is None)
    st_neutral, *_ = nwps_stars(2.0, 12, 160, 1.9, 160, 1.0, 1.0)
    st_windy, *_ = nwps_stars(2.0, 12, 160, 1.9, 160, 0.5, 1.0)
    check(f"wind_mult injected ({st_windy} < {st_neutral})", st_windy < st_neutral)

    # apply_nwps_overrides — fed / fallback / reversible / error / non-nwps ignored / FULL HORIZON
    base = 1767225600
    spots = [{"name": "T", "swell_window_source": "nwps", "orientation_deg": 160, "nwps_wfo": "okx"}]
    far = base + 100 * 3600    # +100h — within NWPS's 145-hr horizon (unlike MOP near-now)
    ratings = {"T": [
        {"valid_time": "2026-01-01T00:00:00Z", "stars": 1.0, "wind_mult": 1.0, "tide_mult": 1.0},
        {"valid_time": datetime.datetime.utcfromtimestamp(far).strftime("%Y-%m-%dT%H:00:00Z"),
         "stars": 1.0, "wind_mult": 1.0, "tide_mult": 1.0},
    ]}
    series = {int(base // 3600): (2.0, 14, 160, 1.9), int(far // 3600): (2.5, 15, 160, 2.4)}
    stats = apply_nwps_overrides(ratings, spots, _fetch=lambda _s: series)
    e0, e1 = ratings["T"]
    check(f"override fed 1 spot ({stats['fed']})", stats["fed"] == 1)
    check("near hour fed", e0["swell_source"] == "nwps" and e0["stars"] != 1.0)
    check("FULL-HORIZON: +100h hour also fed", e1["swell_source"] == "nwps" and e1["stars"] != 1.0)
    nstats = apply_nwps_overrides({"T": [dict(e0)]}, spots, _fetch=lambda _s: None)
    check("no NWPS -> fell_back, no error", nstats["fell_back"] == 1 and nstats["errored"] == 0)
    estats = apply_nwps_overrides({"T": [dict(e0)]}, spots,
                                  _fetch=lambda _s: (_ for _ in ()).throw(OSError("nomads")))
    check("error -> errored, never raises", estats["errored"] == 1)
    plain = apply_nwps_overrides({"P": [{"valid_time": "2026-01-01T00:00:00Z", "stars": 3.0}]},
                                 [{"name": "P", "swell_window_source": "orientation_derived"}],
                                 _fetch=lambda _s: series)
    check("non-nwps spot ignored", plain["fed"] == 0 and plain["fell_back"] == 0)

    # WFO-LEVEL ISOLATION — one WFO's truncated GRIB download (http.client.IncompleteRead, which is NOT
    # an OSError/URLError and used to escape the per-spot except and abort ALL 232 spots) must cost only
    # that WFO's spots. Every other WFO still applies; the failed cycle is fetched ONCE (failure cached);
    # the outage is reported distinctly in wfo_unavailable. Drives the REAL _make_default_fetch by
    # patching the module-global load_cycle / nwps_series_by_hour.
    import http.client as _httpclient
    _saved_load, _saved_sbh = load_cycle, nwps_series_by_hour
    _load_calls = {}
    def _fake_load(wfo, cycle=None):
        _load_calls[wfo] = _load_calls.get(wfo, 0) + 1
        if wfo == "gyx":
            raise _httpclient.IncompleteRead(b"partial", 500)   # truncated NOMADS read
        return {"wfo": wfo}
    globals()["load_cycle"] = _fake_load
    globals()["nwps_series_by_hour"] = lambda _spot, _cyc: {int(base // 3600): (2.0, 14, 160, 1.9)}
    try:
        wspots = [
            {"name": "OK", "swell_window_source": "nwps", "orientation_deg": 160, "nwps_wfo": "okx"},
            {"name": "B1", "swell_window_source": "nwps", "orientation_deg": 160, "nwps_wfo": "gyx"},
            {"name": "B2", "swell_window_source": "nwps", "orientation_deg": 160, "nwps_wfo": "gyx"},
        ]
        wr = {n: [{"valid_time": "2026-01-01T00:00:00Z", "stars": 1.0,
                   "wind_mult": 1.0, "tide_mult": 1.0}] for n in ("OK", "B1", "B2")}
        # EXPLICIT now: the default is the artifact fetch, but _make_default_fetch's WFO
        # isolation still guards the standalone grid tooling and is still worth pinning.
        wst = apply_nwps_overrides(wr, wspots, _fetch=_make_default_fetch())
        check("WFO isolation: healthy WFO fed while another WFO is down", wst["fed"] == 1)
        check("WFO isolation: only the down WFO's 2 spots errored", wst["errored"] == 2)
        check("WFO isolation: IncompleteRead reported in wfo_unavailable", "gyx" in wst["wfo_unavailable"])
        check("WFO isolation: down cycle fetched ONCE (failure cached)", _load_calls.get("gyx") == 1)
        check("WFO isolation: healthy spot kept its NWPS override", wr["OK"][0]["swell_source"] == "nwps")
    finally:
        globals()["load_cycle"], globals()["nwps_series_by_hour"] = _saved_load, _saved_sbh

    # DOWNLOAD RETRY — a truncated read (IncompleteRead) is a transient network drop, not a corrupt
    # file, so _http_get retries once before giving up: a single truncation must NOT down the WFO.
    import urllib.request as _urlreq
    _saved_urlopen = _urlreq.urlopen
    _tries = {"n": 0}
    class _FakeResp:
        def __init__(self, body): self._body = body
        def read(self): return self._body
        def __enter__(self): return self
        def __exit__(self, *a): return False
    def _flaky(req, timeout=None):
        _tries["n"] += 1
        if _tries["n"] == 1:
            raise _httpclient.IncompleteRead(b"partial", 999)   # first attempt truncates
        return _FakeResp(b"GRIB....ok")
    _urlreq.urlopen = _flaky
    try:
        body = _http_get("http://x/cg1.grib2", timeout=5, retry_delay=0)
        check("download retry: one truncation then success", body == b"GRIB....ok" and _tries["n"] == 2)
        _tries["n"] = 0
        def _always_truncate(req, timeout=None):
            _tries["n"] += 1
            raise _httpclient.IncompleteRead(b"partial", 999)
        _urlreq.urlopen = _always_truncate
        gave_up = False
        try:
            _http_get("http://x/cg1.grib2", timeout=5, retry_delay=0)
        except _httpclient.IncompleteRead:
            gave_up = True
        check("download retry: gives up after the retry (2 attempts) → WFO cached unavailable",
              gave_up and _tries["n"] == 2)
    finally:
        _urlreq.urlopen = _saved_urlopen

    # trust gate — DIRECTION is model dirpw vs buoy MWD (both total-spectrum), NOT the
    # buoy swell partition (see trust_verdict). _sb builds (series, buoy) dicts from
    # compact rows so we drive the REAL _pair_samples + trust_verdict. Row is:
    #   (nwps_hs, nwps_dir(dirpw), model_shts, buoy_hs, buoy_mwd, buoy_swell_dir, buoy_swell_hs)
    def _sb(rows):
        series, buoy = {}, {}
        for i, (nh, nd, msh, bh, bm, bsd, bsh) in enumerate(rows):
            series[i] = {"hs": nh, "dir": nd, "shts": msh, "lead": 0}
            buoy[i] = {"hs": bh, "mwd": bm, "swell_dir": bsd, "swell_hs": bsh}
        return series, buoy

    # PASS: dirpw TRACKS buoy MWD (varying dirs, tight offset) and heights co-move.
    _dpw = [150.0, 160.0, 140.0, 155.0, 145.0, 165.0]
    _swd = [210.0, 30.0, 300.0, 90.0, 180.0, 260.0]      # buoy swell_dir: SCATTERED vs dirpw
    _h6 = [0.8, 1.4, 2.1, 1.7, 1.0, 2.4]
    csamp = _pair_samples(*_sb([(_h6[k], _dpw[k], _h6[k] * 0.9, _h6[k] * 1.02 + 0.05,
                                 _dpw[k] - 2.0, _swd[k], _h6[k] * 0.5) for k in range(6)]))
    v, r, cs, n, reason = trust_verdict(csamp)
    check(f"trust PASS when dirpw tracks buoy MWD (cs={cs:.0f}, r={r:.2f})",
          v == "PASS" and r >= 0.80 and cs <= 25)
    # PROOF the verdict uses MWD, not swell_dir: on the SAME hours, dirpw-vs-swell_dir is scattered
    _swd_cs = _circ_std([s["nwps_dir"] - s["buoy_swell_dir"] for s in csamp])
    check(f"verdict uses buoy MWD, not swell_dir (dirpw-vs-swell_dir cs would be {_swd_cs:.0f} > 25 → FAIL)",
          v == "PASS" and _swd_cs > TRUST_CIRC_MAX)
    # INCONCLUSIVE on flat buoy Hs (reason names it) and on too few hours
    fv, _, _, _, freason = trust_verdict(_pair_samples(*_sb(
        [(1.0, 150.0, 0.9, 1.0 + 0.01 * i, 148.0, 210.0, 0.5) for i in range(8)])))
    check("trust INCONCLUSIVE on flat buoy Hs (range < 0.5 m), reason names it",
          fv == "INCONCLUSIVE" and "flat" in freason)
    check("trust INCONCLUSIVE on few overlapping hours (<6)",
          trust_verdict(_pair_samples(*_sb([(1.0, 150.0, 0.9, 1.2, 148.0, 210.0, 0.5)] * 3)))[0]
          == "INCONCLUSIVE")
    # FAIL: dirpw scatters vs MWD -> circ_std > 25 (heights still correlate)
    _mwd = [10.0, 200.0, 95.0, 300.0, 20.0, 170.0, 250.0, 60.0]
    sv, _, scs, _, _ = trust_verdict(_pair_samples(*_sb(
        [(h, 150.0, h * 0.9, h, _mwd[k], 210.0, h * 0.5)
         for k, h in enumerate([0.8, 1.1, 1.5, 1.9, 2.3, 1.2, 1.7, 2.1])])))
    check(f"trust FAIL when dirpw scatters vs MWD (cs={scs:.0f} > 25)", sv == "FAIL" and scs > 25)
    # height r is over ALL pairs — equals _pearson(all nwps_hs, all buoy_hs)
    hsamp = _pair_samples(*_sb([(h, 150.0, h * 0.9, h * 1.01 + 0.03, 148.0, 210.0, h * 0.5)
                                for h in (0.8, 1.1, 1.5, 1.9, 2.3, 1.3)]))
    check("height r over ALL pairs == _pearson(all) — unchanged",
          trust_verdict(hsamp)[1] == _pearson([s["nwps_hs"] for s in hsamp],
                                              [s["buoy_hs"] for s in hsamp]))
    # NO swell-dominance gate: a WINDSEA-dominated day (tiny shts) still assessed → PASS when dirpw≈MWD
    wv, _, _, wn, _ = trust_verdict(_pair_samples(*_sb(
        [(1.0 + 0.05 * k, 150.0 + (k % 3), (1.0 + 0.05 * k) * 0.12,   # shts ~12% of swh = windsea
          1.0 + 0.05 * k, 149.0 + (k % 3), 210.0, (1.0 + 0.05 * k) * 0.12) for k in range(20)])))
    check(f"windsea day (tiny shts) still assessed — no swell-dominance gate ({wv} n={wn})",
          wv == "PASS" and wn >= 20)
    # REGRESSION: clean groundswell (dirpw≈MWD within ~10°, r>0.85, ≥20 pairs) → PASS
    gv, gr, gcs, gn, _ = trust_verdict(_pair_samples(*_sb(
        [(1.0 + 0.05 * k, 150.0 + (k % 3), (1.0 + 0.05 * k) * 0.95,
          1.0 + 0.05 * k, 148.0 + (k % 3), 152.0, (1.0 + 0.05 * k) * 0.95) for k in range(24)])))
    check(f"REGRESSION clean groundswell still PASS ({gv} r={gr:.2f} cs={gcs:.0f} n={gn})",
          gv == "PASS" and gr > 0.85 and gn >= 20)
    # swell_dir + shts are carried for the diagnostic, NOT used for the verdict
    ctx = _pair_samples(*_sb([(1.5, 150.0, 0.9, 1.5, 148.0, 210.0, 0.7)]))
    check("samples carry buoy swell_dir + model shts for context (verdict uses MWD)",
          ctx[0]["buoy_swell_dir"] == 210.0 and ctx[0]["model_shts"] == 0.9 and ctx[0]["buoy_mwd"] == 148.0)

    # DIAGNOSTIC visibility (added; does NOT touch trust math): _node_diag surfaces
    # WHERE trust_check sampled. Synthetic grid — land to the NORTH, a shadowed wet
    # cell just north of the buoy (the plain-nearest → exactly what trust_check
    # samples), seaward wet cells to the south. Proves the new fields populate and that
    # a shoreward / landmask-shadowed sample is flagged with a differing seaward pick.
    dlat = np.array([[40.030], [40.008], [39.980], [39.960]])
    dlng = np.array([[-73.000], [-73.000], [-73.000], [-73.000]])
    dmask = np.array([[True], [False], [False], [False]])       # north row is land
    dcyc = {"lats": dlat, "lons": dlng, "mask": dmask}
    dblat, dblng = 40.000, -73.000
    dcell = _nearest_cell(dcyc, dblat, dblng)                   # exactly what trust_check picks
    dnode = _node_diag(dcyc, dblat, dblng, dcell[0], dcell[1], dcell[2])
    check("node_diag: sampled node lat/lng = the plain-nearest wet cell",
          abs(dnode["lat"] - 40.008) < 1e-9 and abs(dnode["lng"] + 73.000) < 1e-9)
    check(f"node_diag: distance-from-buoy populated ({dnode['dist_km']:.2f} km)",
          0.5 < dnode["dist_km"] < 1.5)
    check("node_diag: counts wet cells within radius (3 within 5 km)",
          dnode["n_within_radius"] == 3)
    check("node_diag: flags sampled node SHOREWARD (landmask-shadow signal)",
          dnode["sampled_is_seaward"] is False)
    check("node_diag: seaward-aware pick DIFFERS and is farther than the sampled cell",
          dnode["seaward_differs"] is True
          and dnode["seaward_nearest_dist_km"] > dnode["dist_km"])

    # _buoy_latlng: coordinates resolve from the FULL active-station metadata, not the
    # wave-reporting subset; an unknown id raises; there is never a hardcoded fallback.
    active_fx = [{"id": "44065", "lat": 40.369, "lng": -73.703, "name": "Long Island Sound"}]
    reporting_fx = [{"id": "44025", "lat": 0.0, "lng": 0.0, "name": ""}]   # 44065 present but NOT reporting waves
    check("buoy resolves from metadata even when NOT wave-reporting",
          _buoy_latlng("44065", _active=active_fx, _reporting=reporting_fx) == (40.369, -73.703))

    def _raises(bid, act):
        try:
            _buoy_latlng(bid, _active=act, _reporting=[]); return False
        except KeyError:
            return True
    check("unknown buoy (absent from metadata) raises", _raises("99999", active_fx))
    check("empty metadata raises — never returns typed/hardcoded coords", _raises("44065", []))

    # region-root resolution — the --validate/--trustcheck cycle path must build the
    # NOMADS URL under each WFO's NWS region (er/sr/wr/pr/ar) via WFO_TO_REGION, NOT a
    # hardcoded 'er'. Pure: no network (proves the sgx/West-Coast fix by string alone).
    # NB: chs (Charleston SC) is NWS Eastern Region — the Carolina coast (mhx/ilm/chs)
    # is 'er'; Southern starts at Florida/Gulf (jax/mfl/tbw…). Assert the real map.
    for _w, _exp in [("box", "er"), ("okx", "er"), ("phi", "er"), ("chs", "er"),
                     ("sgx", "wr"), ("lox", "wr"), ("mtr", "wr"), ("eka", "wr"),
                     ("jax", "sr"), ("mfl", "sr"), ("tbw", "sr"), ("hfo", "pr")]:
        check(f"region {_w} -> {_exp}", _region_for(_w) == _exp)
    check("region resolution is case-insensitive (SGX -> wr)", _region_for("SGX") == "wr")
    check("unmapped WFO falls back to 'er' (no crash)", _region_for("zzq") == "er")
    # constructed CG1 cycle-directory URL carries the WFO's region root — sgx under
    # wr., not er. (mirrors _cycle_files: f"{NOMADS}{region}.{date}/{wfo}/{cc}/CG1/").
    _sgx_dir = f"{NOMADS}{_region_for('sgx')}.20260709/sgx/12/CG1/"
    _okx_dir = f"{NOMADS}{_region_for('okx')}.20260709/okx/12/CG1/"
    check(f"sgx cycle dir under wr., not er. ({_sgx_dir})",
          "/wr.20260709/sgx/12/CG1/" in _sgx_dir and "/er." not in _sgx_dir)
    check("okx cycle dir still under er. — Eastern byte-identical",
          "/er.20260709/okx/12/CG1/" in _okx_dir)

    print("\nself-test:", "ALL PASS — NWPS placement, rating, override (full horizon), trust gate sound."
          if ok else "FAILURES")
    return 0 if ok else 1


def _print_trust_diag(buoy_id, blat, blng, res):
    """DIAGNOSTIC ONLY — print the node + the per-hour DIRECTION breakdown behind the
    verdict (matched system, precondition pass/fail, Δ), so every PASS/FAIL/INCONCLUSIVE is
    auditable, plus the OLD dirpw-vs-MWD metric for reference. Reads only what trust_check
    returned; computes nothing new about the verdict."""
    nd = res.get("node")
    print(f"  buoy point: {blat:.4f},{blng:.4f}")
    if nd:
        print(f"  ↳ [diag] CG1 node {nd['lat']:.4f},{nd['lng']:.4f}  dist_from_buoy={nd['dist_km']:.2f} km"
              f"  ({nd['n_within_radius']} wet cells ≤{nd['radius_km']:.0f} km)")
    else:
        print("  ↳ [diag] no node captured (no usable cycle / no wet cell this run)")
    if res.get("trkng_why"):
        print(f"  ↳ [diag] Trkng node: {res['trkng_why']}")
    if res.get("reason"):
        print(f"  ↳ [diag] verdict reason: {res['reason']}")
    ph = res.get("per_hour") or []
    if not ph:
        print("  ↳ [diag] no paired model/buoy hours")
        return
    _g = lambda x, s: (s % x) if isinstance(x, (int, float)) and x == x else "—"
    print(f"  ↳ [diag] HEIGHT (primary gate): total-Hs r={_g(res.get('height_r'), '%.3f')}")
    nmns = res.get("n_model_no_swell", 0)
    print(f"  ↳ [diag] DIRECTION (energy-weighted, tier={res.get('tier')}): "
          f"circ_std {_g(res.get('dir_circ_std_w'), '%.0f')}° / bias {_g(res.get('dir_bias_w'), '%+.0f')}° "
          f"[unweighted {_g(res.get('dir_circ_std_u'), '%.0f')}°/{_g(res.get('dir_bias_u'), '%+.0f')}°]; "
          f"{res.get('n_qualifying', 0)} comparable hr / {res.get('n_events', 0)} events; "
          f"Rayleigh p={_g(res.get('dir_rayleigh_p'), '%.2f')}"
          + (f"; {nmns} hr had swell but only wind-sea in the model" if nmns else ""))
    print("  ↳ [diag] per hour — model SWELL vs WIND-SEA vs buoy (watch the chop rotate with the wind "
          "while the groundswell holds):")
    print(f"      {'hour':>9} {'q':>1} {'M-swell hs/tp/dir':>18} {'M-windsea hs/tp/dir':>19} "
          f"{'wind':>7} | {'B-swell hs/dir':>14} {'B-wsea dir':>10} {'Δ':>5} {'wt':>5}")
    for p in ph[:16]:
        ts = datetime.datetime.fromtimestamp(p["t"] * 3600, datetime.timezone.utc).strftime("%m-%d %HZ")

        def _sys(hs, tp, d):
            if hs is None or d is None:
                return "—"
            return f"{hs:.2f}/{('%.0fs' % tp) if tp is not None else '—'}/{d:.0f}°"
        msw = _sys(p.get("matched_hs"), p.get("matched_tp"), p.get("model_dir"))
        ws = p.get("windsea") or {}
        mws = _sys(ws.get("hs"), ws.get("tp"), ws.get("dir"))
        wind = (f"{p['model_ws']:.0f}/{p['model_wdir']:.0f}"
                if p.get("model_ws") is not None and p.get("model_wdir") is not None else "—")
        bsw = (f"{p['buoy_hs_swell']:.2f}/{p['buoy_swell_dir']:.0f}°"
               if p.get("buoy_hs_swell") is not None and p.get("buoy_swell_dir") is not None else "—")
        bws = f"{p['buoy_windsea_dir']:.0f}°" if p.get("buoy_windsea_dir") is not None else "—"
        dl = f"{p['delta']:+.0f}°" if p.get("delta") is not None else "—"
        wt = f"{p['weight']:.2f}" if p.get("weight") is not None else "—"
        print(f"      {ts:>9} {('Y' if p['qualifying'] else 'n'):>1} {msw:>18} {mws:>19} {wind:>7} | "
              f"{bsw:>14} {bws:>10} {dl:>5} {wt:>5}")
    samples = res.get("samples") or []
    old = [(((s["dirpw"] - s["buoy_mwd"] + 180) % 360) - 180) for s in samples
           if s.get("dirpw") is not None and s["dirpw"] == s["dirpw"] and s.get("buoy_mwd") is not None]
    if old:
        print(f"  ↳ [diag] OLD dirpw-vs-MWD for reference: circ_std {_circ_std(old):.1f}° "
              f"bias {_circ_mean(old):+.1f}° over {len(old)} hr — the category error this gate replaced")
    # BUOY-SIDE SANITY (task 5): our degree-valued spectral swell_dir must agree with the buoy's
    # own coarse .spec SwD (22.5°-quantized, but not 50° off). A big gap ⇒ OUR reader is wrong.
    sw = [(((s["buoy_swell_dir"] - s["buoy_coarse_swd"] + 180) % 360) - 180) for s in samples
          if s.get("buoy_swell_dir") is not None and s.get("buoy_coarse_swd") is not None]
    if sw:
        print(f"  ↳ [diag] buoy-side sanity: spectral swell_dir vs coarse .spec SwD — mean|Δ| "
              f"{sum(abs(x) for x in sw)/len(sw):.1f}° over {len(sw)} hr (expect ≲15°; a big gap "
              "means the SPECTRAL READER is wrong, not the model)")


def _retired_reference_zones():
    """{(wfo, buoy): record} for zones whose BUOY trust-CHECK has been RETIRED by design — read
    from the assignment file's buoy_reference.retired (NOT spots_enriched.json). The record's
    'axes' lists which axes are retired (['height', 'direction'] for a structurally-invalid
    reference like 44098). These zones KEEP consuming NWPS height + raycast direction; the gate
    reports them as retired-by-design and runs NO buoy comparison against an invalid reference.
    Read-only; {} if the file / section is absent or unparseable."""
    try:
        doc = json.loads(NWPS_ASSIGNMENTS.read_text())
    except (OSError, ValueError):
        return {}
    out = {}
    for r in ((doc.get("buoy_reference") or {}).get("retired") or []):
        if r.get("wfo") and r.get("buoy") is not None:
            out[(r["wfo"], str(r["buoy"]))] = r
    return out


def _tagged_nwps_zones():
    """[(wfo, buoy, spot_count)] — the distinct nwps (wfo, buoy) pairs currently TAGGED LIVE
    in spots_enriched.json (the zones whose PASS came from the OLD gate)."""
    if not ENRICHED.exists():
        return []
    import collections
    counts = collections.Counter()
    for s in json.loads(ENRICHED.read_text()):
        if s.get("swell_window_source") == "nwps":
            counts[(s.get("nwps_wfo"), s.get("nwps_buoy_id"))] += 1
    return [(w, b, n) for (w, b), n in
            sorted(counts.items(), key=lambda kv: (kv[0][0] or "", kv[0][1] or ""))]


def _zone_tiers(wfo, buoy):
    """({tier: count}, strictest_tier) for a (wfo, buoy) zone's spots. Strictest = the tightest
    tier present, so a zone feeding any sheltered/window-edge spot is held to that spot's bar."""
    counts = {}
    if ENRICHED.exists():
        for s in json.loads(ENRICHED.read_text()):
            if (s.get("swell_window_source") == "nwps" and s.get("nwps_wfo") == wfo
                    and s.get("nwps_buoy_id") == buoy):
                t = _spot_tier(s)
                counts[t] = counts.get(t, 0) + 1
    strictest = next((t for t in ("sheltered", "point", "exposed") if counts.get(t)), "point")
    return counts, strictest


def rotate_zones(zones, start_offset=0):
    """Rotate a SORTED zone list so a time-capped run does not always start at 'akq'.

    reverify iterates 57 zones at ~6.5 min each in CI against a 40-minute cap, so it is
    cancelled around zone 15 EVERY run and everything from mfl onward — okx/44025, phi/44091,
    all four sgx zones, both sju zones — had never banked a single event. The list stays
    sorted for determinism; only the START INDEX moves, and iteration wraps, so each run is a
    contiguous window over the same fixed order and consecutive runs cover different zones.
    Pure. offset 0 is the identity, so a Mac run is unchanged."""
    if not zones:
        return []
    off = int(start_offset or 0) % len(zones)
    return list(zones[off:]) + list(zones[:off])


def reverify_tagged(n_cycles=4, start_offset=0):
    """READ-ONLY (deliverable) — re-run the Stage-1 gate against every tagged nwps zone:
    HEIGHT verdict (the PRIMARY gate), the ENERGY-WEIGHTED direction stats (unweighted shown
    alongside), the zone's strictest spot TIER + whether this window's direction clears it,
    and the ROLLING accumulated direction verdict (this window's height-ASSESSABLE records are
    appended to the history log first; INCONCLUSIVE windows bank nothing). Covers every NWPS-placed
    zone — PASS/verified AND pending (46240/46237/46284/46268/46215/46256) — since it keys on
    swell_window_source, not on trust_by_zone. Tags/writes NO prod data (only the append-only
    monitoring log). Runs on the Mac OR on a schedule (buoy events accumulate automatically); when
    GITHUB_OUTPUT is set it emits the zones whose rolling verdict has SETTLED, for a workflow to
    surface for MANUAL tagging — it never tags anything itself."""
    all_zones = _tagged_nwps_zones()
    if not all_zones:
        print("no tagged nwps zones found in spots_enriched.json.")
        return 0
    zones = rotate_zones(all_zones, start_offset)
    n_zones = len(zones)
    print("=== RE-VERIFY tagged nwps zones — Stage 1: height-primary, energy-weighted, tiered, rolling ===")
    print("  HEIGHT is the gate; DIRECTION is an energy-weighted, spot-tiered, ROLLING flag.")
    first = f"{zones[0][0]}/{zones[0][1]}"
    print(f"  {n_zones} zones, starting at index {int(start_offset or 0) % n_zones} ({first}) and "
          f"wrapping — the order is fixed, only the start rotates so a capped run covers a "
          f"different window each time.\n")
    # ROLLING reports EVERY window in TRUST_ROLLING_DAYS (counts in tuple order), so the header is
    # derived from the tuple and a third window needs no edit here. Only TRUST_GATING_WINDOW_DAYS
    # decides the verdict shown beside them.
    win_lbl = "/".join(f"{d}d" for d in TRUST_ROLLING_DAYS)
    now_h = int(datetime.datetime.now(datetime.timezone.utc).timestamp() // 3600)
    print(f"  ROLLING shows the verdict then independent-event counts for {win_lbl}; the PASS/FAIL "
          f"gate uses {TRUST_GATING_WINDOW_DAYS}d only.\n")
    print(f"  {'#':>6} {'wfo/buoy':<11} {'sp':>3} {'tier':<9} {'HEIGHT':<11} {'dirW cs/bias':>14} "
          f"{'(unwtd)':>12} {'ev':>3} {('ROLLING ' + win_lbl):<17}")
    flagged, settled, accumulating, gate_unwindowed = [], [], [], []
    retired = _retired_reference_zones()
    # coverage bookkeeping — a cancelled run prints no summary at all, which is why every zone
    # line carries a [k/n] counter: the LOG still shows how far the run reached.
    visited, skipped_retired, skipped_unverifiable, skipped_error, checked = [], [], [], [], []
    for zi, (wfo, buoy, nspots) in enumerate(zones, 1):
        tag = f"{wfo}/{buoy}"
        pos = f"[{zi}/{n_zones}]"
        visited.append(tag)
        counts, tier = _zone_tiers(wfo, buoy)
        tier_s = tier + (f"×{counts.get(tier)}" if len(counts) > 1 else "")
        if (wfo, str(buoy)) in retired:
            # BUOY retired on both axes — do NOT run ANY comparison against an invalid reference
            # (no height r, no direction verdict); these spots ride NWPS height + raycast, unverified.
            print(f"  {pos:>6} {tag:<11} {nspots:>3} {tier_s:<9} RETIRED BOTH AXES — no valid buoy; "
                  "rides NWPS height + raycast direction (unverified)")
            skipped_retired.append(tag)
            continue
        if buoy is None:
            # UNVERIFIABLE zone (buoy_reference.unverifiable[]: island-shadowed / no valid buoy) —
            # nwps_buoy_id is null by design, so there is nothing to trust-check. Rides NWPS height +
            # raycast direction, unverified. Skip cleanly (not an error, not a "run on the Mac" skip).
            print(f"  {pos:>6} {tag:<11} {nspots:>3} {tier_s:<9} UNVERIFIABLE — no buoy on the row "
                  "(buoy_reference.unverifiable[]); rides NWPS height + raycast direction")
            skipped_unverifiable.append(tag)
            continue
        try:
            blat, blng = _buoy_latlng(buoy)
            res = trust_check(wfo, buoy, blat, blng, n_cycles=n_cycles, tier=tier)
        except Exception as e:  # noqa: BLE001
            print(f"  {pos:>6} {tag:<11} {nspots:>3} {tier_s:<9} {'SKIP':<11} — run on the Mac ({type(e).__name__})")
            skipped_error.append(tag)
            continue

        def _f(x, s):
            return (s % x) if isinstance(x, (int, float)) and x == x else "—"
        hv = res["verdict"] + (f"(r={res['height_r']:.2f})" if res.get("height_r") == res.get("height_r") else "")
        banked, skip_reason = _bank_records(wfo, buoy, res)   # option (b): bank nothing on INCONCLUSIVE
        # THE GATE — deliberately the same call as before, argument for argument, so no verdict can
        # move: this change reports the other windows, it does not re-decide anything. Keeping the
        # literal load_trust_history call (rather than reusing the full history below) also keeps
        # the gate COUPLED to that function, so a later fix there reaches the gate on its own.
        gate_hist = load_trust_history(wfo, buoy, days=TRUST_GATING_WINDOW_DAYS)
        roll = rolling_trust_verdict(gate_hist, tier=tier)
        # ...and REPORTING — every declared window, cut in memory from one full load.
        windows = rolling_by_window(load_trust_history(wfo, buoy), tier=tier, now_epoch_hour=now_h)
        gate_win = next((w for w in windows if w["days"] == TRUST_GATING_WINDOW_DAYS), None)
        # MEASURED, not assumed: load_trust_history applies its day cut only when now_epoch_hour is
        # given too, and the gate call above passes days= alone — so today the gate reads the WHOLE
        # log, not TRUST_GATING_WINDOW_DAYS of it. Detect it from this zone's real records instead of
        # restating what the source is believed to do, so the summary reports a later fix rather than
        # going stale, and stays silent on zones with nothing old enough to discard.
        if gate_win is not None and len(gate_hist) > gate_win["n_records"]:
            gate_unwindowed.append(tag)
        dw = f"{_f(res.get('dir_circ_std_w'), '%.0f')}/{_f(res.get('dir_bias_w'), '%+.0f')}"
        du = f"{_f(res.get('dir_circ_std_u'), '%.0f')}/{_f(res.get('dir_bias_u'), '%+.0f')}"
        flag = "" if res.get("dir_flag") else " ⚑"   # this window fails the tier's direction bar
        checked.append(tag)
        print(f"  {pos:>6} {tag:<11} {nspots:>3} {tier_s:<9} {hv:<11} {dw:>14} {du:>12} "
              f"{res.get('n_events', 0):>3} {_rolling_cell(roll['verdict'], windows):<17}{flag}")
        if skip_reason:
            print(f"      ↳ banked 0 events — {skip_reason}; this window does NOT accumulate "
                  "(height not assessable → its direction residual is not trusted)")
        if res["verdict"] == "FAIL" or roll["verdict"] == "FAIL":
            # Still listed — never filtered — but a height FAIL from a swell-free window is
            # marked, so it is not read as evidence about groundswell skill.
            ws_note = ("  [WIND-SEA ONLY — 0 comparable swell hours this window; the height FAIL "
                       "is about wind chop, NOT groundswell skill]" if windsea_only_fail(res) else "")
            flagged.append(f"{tag} ({nspots} sp): height {res['verdict']}, dir(rolling) "
                           f"{roll['verdict']} — {roll.get('reason') or res.get('reason') or ''}{ws_note}")
        by_window = {str(w["days"]): w["n_events"] for w in windows}
        if roll["verdict"] != "ACCUMULATING":   # reached TRUST_MIN_EVENTS → SETTLED, ready for MANUAL review
            settled.append({"zone": tag, "wfo": wfo, "buoy": str(buoy), "spots": nspots,
                            "verdict": roll["verdict"], "n_events": roll.get("n_events", 0),
                            "reason": roll.get("reason"),
                            "events_by_window": by_window,
                            "gating_window_days": TRUST_GATING_WINDOW_DAYS})
        else:
            # ACCUMULATING carries BOTH counts so a reader can separate "not enough swell yet" from
            # "the gating window is expiring events faster than they arrive" — one number cannot.
            accumulating.append({"zone": tag, "wfo": wfo, "buoy": str(buoy), "spots": nspots,
                                 "verdict": roll["verdict"], "n_events": roll.get("n_events", 0),
                                 "reason": roll.get("reason"),
                                 "events_by_window": by_window,
                                 "gating_window_days": TRUST_GATING_WINDOW_DAYS,
                                 "expiry_note": window_expiry_note(windows)})
    print("\n==== coverage ====")
    not_reached = [f"{w}/{b}" for w, b, _ in zones if f"{w}/{b}" not in visited]
    print(f"  zones in the roster: {n_zones}   reached: {len(visited)}   not reached: {len(not_reached)}")
    print(f"  of those reached — trust-checked: {len(checked)}   skipped UNVERIFIABLE (no buoy on the "
          f"row): {len(skipped_unverifiable)}   skipped RETIRED (both axes): {len(skipped_retired)}"
          + (f"   skipped on error: {len(skipped_error)}" if skipped_error else ""))
    if not_reached:
        print(f"  NOT REACHED this run ({len(not_reached)}) — the next run's rotated offset should pick "
              "them up; this is reported, NOT a failure:")
        for i in range(0, len(not_reached), 6):
            print("      " + ", ".join(not_reached[i:i + 6]))
    else:
        print("  full pass — every zone in the roster was reached.")
    cs = cycle_cache_stats()
    print(f"  CG1 cycle fetches: {cs['requested']} requested → {cs['downloaded']} downloaded "
          f"({cs['saved']} served from the per-process cache: {cs['parse_reused']} parsed-reuse, "
          f"{cs['file_reused']} file-reuse with no rewrite so cfgrib's index stayed valid)")
    if not_reached:
        print("  NOTE: a run CANCELLED by the workflow timeout never reaches this summary at all — "
              "read the [k/n] counter on the last zone line to see how far it got.")

    print("\n==== summary ====")
    print("  HEIGHT is the primary gate (the skill the field verifies). Direction ⚑ = this window's")
    print("  energy-weighted residual exceeds the zone's strictest-spot tier; ROLLING is the verdict")
    print("  that matters — it needs TRUST_MIN_EVENTS independent swell events before PASS/FAIL.")
    if retired:
        print("  RETIRED-BY-DESIGN zones (buoy invalid on BOTH axes → no buoy check; ride NWPS height +")
        print("  raycast direction, unverified — NOT a failure, and NOT in the failing-zones list below):")
        for (w, b), r in sorted(retired.items()):
            print(f"    • {w}/{b} ({r.get('spots','?')} sp): {r.get('reason', '')}")
    if flagged:
        print("  zones failing HEIGHT or the ROLLING direction gate (re-verify / untag candidates — YOUR call):")
        for c in flagged:
            print(f"    • {c}")
    else:
        print("  no zone fails the height gate or the rolling direction gate (most will read ACCUMULATING")
        print("  until enough events log — that is expected, not a pass).")
    print("  Also run --pairing-audit: a STRUCTURALLY INVALID buoy can't be fixed by any metric.")
    if settled:
        print("\n  SETTLED zones (rolling verdict reached TRUST_MIN_EVENTS — ready for MANUAL review/tagging;")
        print("  this job NEVER tags them itself):")
        for s in settled:
            print(f"    • {s['zone']} ({s['spots']} sp): rolling {s['verdict']} on {s['n_events']} events"
                  + f" [{_window_counts_str(s)}]"
                  + (f" — {s['reason']}" if s.get("reason") else ""))
    if accumulating:
        print(f"\n  ACCUMULATING zones — event counts at {win_lbl}. A zone short at "
              f"{TRUST_GATING_WINDOW_DAYS}d but well supplied at a wider window is not waiting on")
        print("  swell: the gating window is expiring events faster than they arrive, and it can never")
        print("  settle. Widening the gate is a SEPARATE decision — this only reports the evidence:")
        for a in accumulating:
            print(f"    • {a['zone']} ({a['spots']} sp): {_window_counts_str(a)}"
                  + (f" — {a['reason']}" if a.get("reason") else ""))
            if a.get("expiry_note"):
                print(f"        ↳ {a['expiry_note']}")
    if gate_unwindowed:
        print(f"\n  NOTE — the {TRUST_GATING_WINDOW_DAYS}d gating window is NOT being applied. "
              "load_trust_history cuts by day only when")
        print("  now_epoch_hour is passed as well, and the gate passes days= alone, so the verdict is "
              "computed over")
        print(f"  the ENTIRE banked log. Measured this run on {len(gate_unwindowed)} zone(s) holding "
              "records older than the")
        print(f"  window: {', '.join(gate_unwindowed[:8])}"
              + (f", +{len(gate_unwindowed) - 8} more" if len(gate_unwindowed) > 8 else ""))
        print(f"  So the {win_lbl} counts above are what the DECLARED windows would hold; the verdict "
              "is over everything.")
        print("  Left as-is deliberately: enforcing the cut would NARROW the gate and could unsettle "
              "already-settled")
        print("  zones — a verdict change, not a reporting one, and YOUR call.")
    print("  (Read-only: nothing tagged/untagged; spots_enriched.json untouched; only the monitoring log grows.)")
    _emit_reverify_output(settled, accumulating)
    return 0


def _rolling_cell(verdict, windows):
    """The ROLLING table cell — e.g. `ACCUMULATING 1/4`: the GATING verdict followed by the
    independent-event count for EVERY declared window, in tuple order. One number could not
    separate a quiet spell from a window expiring events faster than they arrive. Pure."""
    return verdict + " " + "/".join(str(w["n_events"]) for w in windows)


def _window_counts_str(rec):
    """Render a record's events_by_window as e.g. `30d 1 (gate) / 90d 4`, in TRUST_ROLLING_DAYS
    order with the GATING window marked. Reads the record's own map rather than the tuple, so a
    record emitted under a different window set still renders. Pure."""
    ev = rec.get("events_by_window") or {}
    gating = rec.get("gating_window_days")
    order = [str(d) for d in TRUST_ROLLING_DAYS if str(d) in ev]
    order += [k for k in sorted(ev, key=lambda x: int(x)) if k not in order]
    return " / ".join(f"{k}d {ev[k]}" + (" (gate)" if gating is not None and k == str(gating) else "")
                      for k in order) or "no window counts"


def _emit_reverify_output(settled, accumulating=None):
    """Write any_settled / settled_json / accumulating_json to $GITHUB_OUTPUT (only when the env var
    is set — i.e. under a scheduling workflow; a no-op on the Mac). Lets the workflow open/update an
    issue for zones whose ROLLING verdict has SETTLED (reached TRUST_MIN_EVENTS → PASS / FAIL /
    INCOHERENT) and are ready for MANUAL review/tagging. This function NEVER tags anything — it only
    reports. Mirrors the buoy-ready monitor's _emit_github_output pattern.

    accumulating_json carries the not-yet-settled zones with BOTH windows' event counts, so the issue
    can distinguish a zone waiting on swell from one whose gating window expires events faster than
    they arrive. It is ADDITIVE — any_settled still gates the issue exactly as before, so a run with
    no settled zone opens nothing however many zones are accumulating."""
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"any_settled={'true' if settled else 'false'}\n")
        f.write("settled_json=" + json.dumps(settled, separators=(",", ":"), ensure_ascii=False) + "\n")
        f.write("accumulating_json="
                + json.dumps(accumulating or [], separators=(",", ":"), ensure_ascii=False) + "\n")


def depth_experiment(n_cycles=4, radius_km=6.0):
    """READ-ONLY (tasks 1-3) — re-run the rebuilt gate at the production NEAREST node and at
    the depth-matched SEAWARD node for every tagged nwps zone, reporting before/after
    circ_std + bias plus the node geometry (dist / bearing / shoreward|seaward) the
    refraction hypothesis predicts. If moving the sample SEAWARD (deeper/open) collapses the
    bias toward 0, the failures are node selection / refraction — fix the node, NOT the tags.
    buoy 44098 appears twice (box + gyx): same buoy, two model nodes = the controlled test.
    Tags/writes NOTHING. Mac-only (NOMADS+NDBC+eccodes)."""
    zones = _tagged_nwps_zones()
    if not zones:
        print("no tagged nwps zones found in spots_enriched.json.")
        return 0
    print("=== DEPTH / NODE experiment — gate at NEAREST vs SEAWARD node (READ-ONLY) ===")
    print(f"  seaward re-pick radius {radius_km} km · buoy 44098 (box+gyx) = the same-buoy control\n")
    hdr = (f"  {'wfo/buoy':<11} {'node':<8} {'node lat,lng':<18} {'d_km':>5} {'brg':>4} {'side':>9} "
           f"{'circ_std':>8} {'bias':>7} {'qual':>4}  verdict")
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    def _f(x, s):
        return (s % x) if isinstance(x, (int, float)) and x == x else "—"

    for wfo, buoy, nspots in zones:
        try:
            blat, blng = _buoy_latlng(buoy)
        except Exception as e:  # noqa: BLE001
            print(f"  {wfo}/{buoy:<6} SKIP ({type(e).__name__}) — run on the Mac")
            continue
        for sel in ("nearest", "seaward"):
            try:
                res = trust_check(wfo, buoy, blat, blng, n_cycles=n_cycles,
                                  node_select=sel, node_radius_km=radius_km)
            except Exception as e:  # noqa: BLE001
                print(f"  {wfo}/{buoy:<6} {sel:<8} SKIP ({type(e).__name__})")
                continue
            nd = res.get("node") or {}
            if nd:
                latlng = f"{nd['lat']:.3f},{nd['lng']:.3f}"
                dkm, brg = f"{nd['dist_km']:.2f}", f"{_bearing(blat, blng, nd['lat'], nd['lng']):.0f}"
                side = "seaward" if nd.get("sampled_is_seaward") else "SHOREWARD"
            else:
                latlng, dkm, brg, side = "—", "—", "—", "—"
            print(f"  {wfo}/{buoy:<6} {sel:<8} {latlng:<18} {dkm:>5} {brg:>4} {side:>9} "
                  f"{_f(res.get('dir_circ_std'), '%.1f°'):>8} {_f(res.get('dir_bias'), '%+.1f°'):>7} "
                  f"{res.get('n_qualifying', 0):>4}  {res['verdict']}")
    print("\n  → per zone, compare its two rows: if bias shrinks toward 0 (and the node flips")
    print("    SHOREWARD→seaward) from nearest→seaward, refraction/node-selection is the cause —")
    print("    the fix is depth-matched node selection, NOT untagging. If the bias persists at the")
    print("    seaward node, the pairing may genuinely be bad (untag is then on the table).")
    print("  (Read-only: nothing tagged/untagged; spots_enriched.json untouched.)")
    return 0


# ── Buoy pairing audit (task 3): is this buoy a VALID directional reference? ──
# Structural facts from the research report (authoritative); the live NDBC station-page fetch
# augments these on the Mac. Geometry, not season, separates the passing from the failing zones.
# Buoy-as-directional-reference scoring thresholds (the report's checklist, shared by
# --pairing-audit and --find-buoy).
PAIRING_DEEP_DEPTH_M = 50.0   # buoy depth ≥ this vs a shallow nearshore SWAN node = a different wave
                              #   regime (offshore/bank vs coastal shoaling) → a STRUCTURAL disqualifier
PAIRING_FAR_KM = 100.0        # beyond this the exposure/refraction differ enough to weaken the proxy (soft)

# depth_m: a verified metres value where known; None means "resolve live" — _ndbc_station_meta now
# scrapes it from the station page (<b>Water depth:</b> N m<br>) on the Mac. Do NOT invent depths;
# leave None rather than guess, and the live scrape fills it in at runtime.
_KNOWN_BUOY_META = {
    "44025": {"payload": "3-m foam SCOOP discus", "depth_m": None,
              "note": "foam discus — noisier direction for LOW-energy swell"},
    "44065": {"payload": "3-m foam SCOOP discus", "depth_m": None,
              "note": "foam discus — noisier direction for LOW-energy swell"},
    "44091": {"payload": "Datawell Waverider", "depth_m": None,
              "note": "Waverider — high-quality directional reference (prefer)"},
    "44095": {"payload": "Datawell Waverider", "depth_m": None,
              "note": "Waverider — high-quality directional reference (prefer)"},
    "44097": {"payload": None, "depth_m": None, "note": None},
    "44098": {"payload": None, "depth_m": 76.0,
              "note": "DEEP bank/ledge (~76 m) paired to a shallow nearshore SWAN node — "
                      "different wave regime (refraction/shoaling): structural depth mismatch"},
    "44099": {"payload": None, "depth_m": None,
              "note": "Chesapeake mouth — complex, multi-directional approaches"},
    # Gulf-of-Maine 44098 re-pairing candidates — real NDBC/NERACOOS station metadata (the OFFLINE
    # floor; the Mac's live station-page fetch confirms/supersedes). Depths in metres, from the NDBC
    # station pages / NERACOOS. These ids are NOT tagged zones, so --pairing-audit is unaffected.
    "44005": {"payload": "3-m discus", "depth_m": None,
              "note": "central Gulf of Maine — deep, far offshore (depth via Mac fetch)"},
    "44007": {"payload": "3-m discus", "depth_m": 49.0,
              "note": "Portland ME, ~12 NM offshore — 3-m discus"},
    "44013": {"payload": "2.1-m ionomer foam", "depth_m": 64.6,
              "note": "Boston, 16 NM E — deep shelf, foam hull"},
    "44018": {"payload": None, "depth_m": None,
              "note": "SE of Cape Cod — different (Cape/Georges Bank) exposure; depth via Mac fetch"},
    "44030": {"payload": None, "depth_m": 62.0,
              "note": "Western Maine Shelf (NERACOOS B01) — 62 m open-shelf platform"},
    "44090": {"payload": "Datawell Waverider", "depth_m": 25.9,
              "note": "Cape Cod Bay (CDIP/NERACOOS Waverider) — shallow + good payload, but a SHELTERED "
                      "Cape Cod Bay exposure, not the open Gulf-of-Maine coast these spots face"},
}

# Cited NDBC/NERACOOS coordinates for the Gulf-of-Maine re-pairing search — the OFFLINE floor for
# --find-buoy when activestations.xml is unavailable (sandbox). id -> (lat, lng, short name). The
# Mac's live active-station list enumerates ALL stations and supersedes this short seed.
_FIND_BUOY_COORD_SEED = {
    "44098": (42.800, -70.169, "Jeffreys Ledge, NH"),
    "44030": (43.179, -70.426, "Western Maine Shelf B01"),
    "44007": (43.525, -70.140, "Portland, ME"),
    "44013": (42.346, -70.651, "Boston, MA"),
    "44090": (41.840, -70.329, "Cape Cod Bay"),
    "44018": (42.203, -70.154, "SE of Cape Cod"),
}


# NDBC station pages render depth as: <b>Water depth:</b> 20.45 m<br> — an HTML tag (and any
# whitespace) sits between the label and the number, so after the colon we skip zero-or-more tags
# before the float. The old `Water depth:\s*([\d.]+)` missed the </b> and returned None for EVERY
# buoy, which made --find-buoy's validity gate unsatisfiable. Verified live: 46268 = 20.45 m,
# 46027 = 60 m. Keep this pattern tag-tolerant so it is not "fixed" back later.
_WATER_DEPTH_RE = re.compile(r"[Ww]ater depth:\s*(?:</?[a-zA-Z][^>]*>\s*)*([\d.]+)\s*m")


def _parse_water_depth(html):
    """Water depth in metres from an NDBC station-page HTML string, tolerant of the
    <b>Water depth:</b> N m<br> tag layout; None when absent or implausible. Sanity-bounded to a
    positive float below 12000 m (deepest ocean ≈ 11 km) so a stray capture never poisons depth."""
    m = _WATER_DEPTH_RE.search(html or "")
    if not m:
        return None
    try:
        d = float(m.group(1))
    except ValueError:
        return None
    return d if 0.0 < d < 12000.0 else None


def _ndbc_station_meta(buoy):
    """{payload, depth_m, note} — the report's KNOWN facts, augmented on the Mac by scraping the
    NDBC station page (depth via _parse_water_depth + payload). Fetch failures are silent; the known
    table is the reliable floor."""
    meta = dict(_KNOWN_BUOY_META.get(str(buoy).lower(), {"payload": None, "depth_m": None, "note": None}))
    if meta.get("depth_m") is not None and meta.get("payload") is not None:
        return meta   # known metadata is complete — no fetch needed
    try:  # single-attempt fetch (no retry/backoff) — fast-fails offline, augments on the Mac
        html = _http_get(f"https://www.ndbc.noaa.gov/station_page.php?station={buoy}",
                         timeout=15).decode("utf-8", "replace")
        if meta.get("depth_m") is None:   # <b>Water depth:</b> N m<br> — tag-tolerant parse + sanity bound
            meta["depth_m"] = _parse_water_depth(html)
        pm = re.search(r"(Waverider|SCOOP|3-m foam|[Dd]iscus)", html)
        if pm and not meta.get("payload"):
            meta["payload"] = pm.group(1)
    except Exception:  # noqa: BLE001
        pass
    return meta


def _score_pairing(meta):
    """(verdict, reasons) for a buoy as a nearshore directional REFERENCE — the SINGLE source of
    truth shared by --pairing-audit and --find-buoy. One STRUCTURAL disqualifier → STRUCTURALLY
    INVALID no matter what else: a deep-water DEPTH MISMATCH (the buoy sits in a different wave
    regime than a shallow nearshore SWAN node). SOFT concerns each make it MARGINAL but never sum
    into 'invalid': a foam/discus payload, a far distance, or a sheltered/complex exposure. No
    concern → VALID REFERENCE. Distance + exposure are scored only when the caller supplies them
    (find-buoy sets meta['distance_km']), so --pairing-audit's output is unchanged."""
    reasons, soft, structural = [], 0, False
    payload = meta.get("payload")
    if payload and "waverider" in payload.lower():
        reasons.append(f"payload {payload} — high-quality directional ✓")
    elif payload and any(k in payload.lower() for k in ("scoop", "discus", "foam")):
        reasons.append(f"payload {payload} — noisier direction for low-energy swell")
        soft += 1
    elif payload:
        reasons.append(f"payload {payload}")
    depth = meta.get("depth_m")
    if depth is not None:
        if depth >= PAIRING_DEEP_DEPTH_M:
            reasons.append(f"buoy depth {depth:.0f} m = DEEP water; a nearshore SWAN node is a "
                           "different wave regime → structural DEPTH MISMATCH")
            structural = True
        else:
            reasons.append(f"buoy depth {depth:.0f} m (shallow — closer to a nearshore node)")
    dist = meta.get("distance_km")
    if dist is not None:
        if dist > PAIRING_FAR_KM:
            reasons.append(f"{dist:.0f} km from target — far enough that exposure/refraction differ "
                           "(weaker directional proxy)")
            soft += 1
        else:
            reasons.append(f"{dist:.0f} km from target")
    note = meta.get("note") or ""
    # MODALITY / EXPOSURE (checklist item): a complex, multi-directional, or sheltered/bay approach
    # makes the buoy's mean swell direction a poor single nearshore proxy → MARGINAL (not a depth veto).
    if any(k in note.lower() for k in ("complex", "multi-directional", "multidirectional",
                                       "sheltered", "shadowed", " bay", "estuary")):
        soft += 1
    if note and note not in " ".join(reasons):
        reasons.append(note)
    verdict = ("STRUCTURALLY INVALID" if structural else
               ("MARGINAL" if soft > 0 else "VALID REFERENCE"))
    return verdict, reasons


def pairing_audit():
    """READ-ONLY (task 3) — score each tagged zone's buoy as a directional REFERENCE against
    the report's checklist (payload, depth match, complexity) and print VALID / MARGINAL /
    STRUCTURALLY INVALID with reasons. Uses the report's known metadata + (Mac) the NDBC
    station page. Tags/writes NOTHING; the human decides what to re-verify or untag."""
    zones = _tagged_nwps_zones()
    if not zones:
        print("no tagged nwps zones found in spots_enriched.json.")
        return 0
    print("=== BUOY PAIRING AUDIT — is each zone's buoy a valid directional reference? (READ-ONLY) ===")
    retired = _retired_reference_zones()
    latent = []   # task-4 guard: STRUCTURALLY INVALID buoys still trust-gated (not retired)
    for wfo, buoy, nspots in zones:
        rec = retired.get((wfo, str(buoy)))
        if rec:   # disposition recorded in the assignment file → report it as deliberate, not a failure
            axes = "+".join(rec.get("axes") or ["direction"])
            print(f"\n  {wfo}/{buoy} ({nspots} spots): RETIRED BY DESIGN (both axes: {axes}) — no valid buoy reference")
            print(f"      · {rec.get('reason')}")
            print("      · rides NWPS height + raycast direction, UNVERIFIED by a buoy; no comparison is run against 44098")
            continue
        meta = _ndbc_station_meta(buoy)
        verdict, reasons = _score_pairing(meta)
        print(f"\n  {wfo}/{buoy} ({nspots} spots): {verdict}")
        for r in reasons:
            print(f"      · {r}")
        if not reasons:
            print("      · no structural red flags in the known metadata (confirm payload/depth on the Mac)")
        if verdict == "STRUCTURALLY INVALID":
            latent.append(f"{wfo}/{buoy} ({nspots} sp)")
    # GUARD (task 4): a STRUCTURALLY INVALID buoy that is NOT retired is still being trust-gated against
    # an invalid reference — the exact inconsistency the 44098 retirement removed. Surface any second copy.
    if latent:
        print("\n  ⚠ INCONSISTENCY — these zones' buoy scores STRUCTURALLY INVALID but is NOT retired (still")
        print("    trust-gated against an invalid reference). Retire them both-axes like 44098:")
        for z in latent:
            print(f"      • {z}")
    else:
        print("\n  ✓ consistency: every tagged zone either has a usable (VALID/MARGINAL) buoy or is RETIRED —")
        print("    no zone is trust-gated against a STRUCTURALLY INVALID buoy (44098 was the only one; both")
        print("    its zones are retired on both axes).")
    print("\n  RETIRED BY DESIGN = the buoy is structurally invalid as a reference on BOTH axes AND no valid")
    print("  nearshore alternative exists (--find-buoy); the zone rides NWPS height + raycast, unverified.")
    print("  (Read-only: nothing tagged/untagged; spots_enriched.json untouched.)")
    return 0


# --------------------------------------------------------------------------- #
# --find-buoy — find the best VALID directional reference near a target        #
# (generalizes --pairing-audit from "score the pairing" to "find a pairing")   #
# --------------------------------------------------------------------------- #
# --------------------------------------------------------------------------- #
# --find-buoy validity: LIVENESS + directional-axis availability.              #
# Depth/payload metadata renders perfectly for a buoy dead for months, so it is #
# not enough on its own — a candidate must ALSO be currently reporting and       #
# publish the directional-spectrum files our reader needs.                       #
# --------------------------------------------------------------------------- #
# 12 h justification: NDBC directional buoys post .data_spec ~hourly, so a healthy buoy's newest
# observation is <~2 h old. Real transient gaps (a missed GOES DCS slot, a brief comms dropout) are
# a few hours and self-recover, so 12 h (~2x the normal worst-case gap) does NOT reject a good buoy;
# yet >12 h is a dozen consecutive missed hourly reports — well outside a normal gap — and an unsafe
# pick for a multi-month directional commitment. It catches every decay we observed: 46453 at 26 h,
# 46283 at 13 d, and any 404 / removed buoy (age-unknown → not 'available'). It is deliberately
# STRICTER than the BU-5 monitor's >30 d RETIRE alert because the decisions differ: SELECTING a NEW
# reference must demand active reporting (a false-alive is 26 spots placed on a dead buoy, undetected
# for weeks — the exact bug), while RETIRING an existing one is lenient (buoys come back after
# servicing). The shared piece is the age COMPUTATION (ndbc_spectral.hours_since_last_obs), not the
# threshold — different bars for different decisions is correct, not drift.
FIND_BUOY_LIVENESS_MAX_AGE_H = 12   # silent longer than this → NOT selectable as a new reference
FIND_BUOY_ROSTER_MAX_AGE_H = 12     # activestations.xml older than this → refetch before selecting


def _probe_realtime_file(buoy, suffix, get_fn):
    """Status-aware probe of ONE NDBC realtime2 file → ('available'|'unavailable'|'unknown', detail).
    Factored out so every probed endpoint gets IDENTICAL retry and empty-body handling; the
    three-state contract lives here and _probe_direction_axis just composes it across files.
      available    200 with a real (non-HTML, non-empty) body
      unavailable  404/410, or 200 with an empty/HTML body — PERMANENT for this file
      unknown      timeout / 5xx after ONE retry, or any other status — transient
    Uses http.get_once (single attempt; returns the Response for 4xx/5xx so we can read the status)
    and retries ONCE on a transient failure, so a network blip cannot blacklist a good buoy."""
    from ..config import NDBC_REALTIME2_BASE
    url = f"{NDBC_REALTIME2_BASE}/{str(buoy).upper()}{suffix}"
    detail = f"{suffix} unreachable"
    for _attempt in (1, 2):
        try:
            resp = get_fn(url, timeout=15)
        except Exception as e:  # noqa: BLE001  transport error (Timeout / ConnectionError) — transient
            detail = f"{suffix} {type(e).__name__}"
            continue            # retry once, then fall through to UNKNOWN
        code = getattr(resp, "status_code", None)
        if code in (404, 410):
            return ("unavailable", f"{suffix} {code}")
        if code == 200:
            text = (getattr(resp, "text", "") or "").strip()
            if text and not text.startswith("<"):
                return ("available", None)
            return ("unavailable", f"{suffix} empty")    # 200 but no / HTML body → treat as absent
        if code is not None and 500 <= code < 600:
            detail = f"{suffix} {code}"
            continue            # retry once on 5xx
        detail = f"{suffix} HTTP {code}"
        break                   # any other status → UNKNOWN (don't select, don't blacklist)
    return ("unknown", detail)


def _probe_direction_axis(buoy, *, _get=None):
    """Status-aware probe of whether a buoy publishes a usable DIRECTION axis. Same three states as
    before, unchanged in meaning:
      ("available",   None)            both files present — the directional axis is usable
      ("unavailable", "<file> 404")    404/410 (or an empty body) on EITHER file — PERMANENT: no
                                       directional axis (not selectable as a DIRECTION reference;
                                       may still be a HEIGHT reference)
      ("unknown",     "<file> <why>")  timeout / 5xx after ONE retry on EITHER file — transient:
                                       never silently selectable, never permanently blacklisted

    .data_spec ALONE IS NOT SUFFICIENT, which is what this fixes. .data_spec carries energy density
    by frequency only — it says how much energy sits at each frequency, not which way any of it is
    travelling. The directional MOMENTS live in .swdir (and .swr1). CDIP-mirrored coastal stations
    publish the energy spectrum without the moments, so a 200 on .data_spec is NOT proof of a
    direction axis: LJPC1 (La Jolla, CA 073) returns 200 on .data_spec and 404 on both .swdir and
    .swr1, and its .txt agrees — WVHT, DPD and APD present, MWD reading MM. Probing .data_spec alone
    therefore rated it as having a direction axis and it was recommended as a VALID directional
    reference for seven San Diego spots; the downstream reader then found nothing usable, which is
    why hours_since_last_obs returned None for it while returning 0 for real buoys. Real directional
    buoys (46254, 44095, 41120) return 200 on .data_spec, .swdir AND .swr1.

    So .swdir is REQUIRED in addition to .data_spec. .data_spec is probed first and a non-available
    result short-circuits, so a station with no spectra at all costs one probe, not two. Only
    .swdir is required, not .swr1: .swdir alone establishes that directional moments are published,
    and each extra required file is another chance for a transient failure to mask a good buoy.
    *_get* injects the fetcher for tests."""
    if _get is not None:
        get_fn = _get
    else:
        from ..http import get_once as get_fn
    state, detail = _probe_realtime_file(buoy, ".data_spec", get_fn)
    if state != "available":
        return (state, detail)              # no energy spectrum → the moments cannot help
    return _probe_realtime_file(buoy, ".swdir", get_fn)


def _liveness_verdict(age_h, *, roster_stale=False):
    """(status, text) for a candidate's liveness — status in {'alive','dead','unknown'}, text with
    the number. A stale, unrefreshable roster forces UNKNOWN (we must not report data we know is old
    as 'alive'). An unknown age (offline / not probed) is UNKNOWN. age > FIND_BUOY_LIVENESS_MAX_AGE_H
    is DEAD, stated with the elapsed span."""
    if roster_stale:
        return ("unknown", "LIVENESS UNKNOWN — roster stale, could not refresh (data not current)")
    if age_h is None:
        return ("unknown", "liveness not probed (offline)")
    if age_h > FIND_BUOY_LIVENESS_MAX_AGE_H:
        span = f"{age_h} h" if age_h < 48 else f"{age_h // 24} days"
        return ("dead", f"DEAD — no observation in {span}")
    return ("alive", f"alive — last obs {age_h} h ago")


def _activestations_age_h():
    """Age (hours) of the local activestations.xml find-buoy's candidate list comes from, or None
    if it is absent / unstat-able."""
    import time
    from ..config import NDBC_STATIONS_XML
    try:
        return (time.time() - NDBC_STATIONS_XML.stat().st_mtime) / 3600.0
    except OSError:
        return None


def _refresh_activestations():
    """Best-effort single refetch of activestations.xml (the candidate roster + station metadata
    source). Returns True on success. Never raises."""
    from ..config import NDBC_STATIONS_XML
    try:
        from ..http import get_once
        resp = get_once("https://www.ndbc.noaa.gov/activestations.xml", timeout=30)
        text = (getattr(resp, "text", "") or "").strip()
        if getattr(resp, "status_code", None) == 200 and text.startswith("<"):
            NDBC_STATIONS_XML.parent.mkdir(parents=True, exist_ok=True)
            NDBC_STATIONS_XML.write_text(text)
            return True
    except Exception:  # noqa: BLE001
        pass
    return False


def _find_buoy_roster_freshness(*, _age_fn=None, _refresh_fn=None):
    """(age_h, refreshed, stale_unrefreshable) for the activestations.xml candidate roster. Do NOT
    trust a cached roster: if it is older than FIND_BUOY_ROSTER_MAX_AGE_H, attempt ONE refetch (a
    days-stale roster kept a removed buoy listed). If the refetch fails, flag it so liveness is
    reported UNKNOWN rather than silently as current. Injectable for tests."""
    age = (_age_fn or _activestations_age_h)()
    if age is None or age <= FIND_BUOY_ROSTER_MAX_AGE_H:
        return (age, False, False)
    if (_refresh_fn or _refresh_activestations)():
        return (0.0, True, False)
    return (age, False, True)


_PAIRING_RANK = {"VALID REFERENCE": 0, "MARGINAL": 1, "STRUCTURALLY INVALID": 2}


def _rank_candidates(tlat, tlng, stations, radius_km, *, meta_fn=_ndbc_station_meta, spectral_fn=None):
    """PURE ranking core of --find-buoy (offline-testable). Score every station within *radius_km*
    of the target with the SHARED _score_pairing (distance folded into the meta), then order
    best-first: usable VALID, then MARGINAL, then STRUCTURALLY INVALID, then metadata-incomplete,
    then no-spectral (unusable by our reader); nearest first within a class. *meta_fn* / *spectral_fn*
    are injectable for tests. Returns a list of row dicts; scores nothing it has no data for.

    CANDIDATES ARE ORDERED BY DISTANCE **BEFORE** spectral_fn is called. spectral_fn is the live
    probe and it is capped by the caller, so the order it is invoked in decides WHICH stations get
    probed. Iterating the roster directly spent the cap in activestations.xml order: ljpc1 (La
    Jolla, 1 km, both endpoints 404) went unprobed and kept spectral None while stations 130 km away
    were probed, so it outranked 46254 SCRIPPS Nearshore — a live Waverider at the same distance
    with a confirmed .data_spec — and was recommended for seven San Diego spots. It also made the
    "probed the N nearest" message untrue. Sorting first makes the cap mean what it says."""
    near = []
    for s in stations:
        d = _haversine_km(tlat, tlng, s["lat"], s["lng"])
        if d <= radius_km:
            near.append((d, s))
    near.sort(key=lambda ds: ds[0])          # NEAREST FIRST — before a single probe is spent
    rows = []
    for d, s in near:
        meta = dict(meta_fn(s["id"]))
        meta["distance_km"] = d
        verdict, reasons = _score_pairing(meta)
        rows.append({"d": d, "id": s["id"], "name": s.get("name", ""),
                     "depth": meta.get("depth_m"), "payload": meta.get("payload"),
                     "spectral": (spectral_fn(s["id"]) if spectral_fn else None),
                     "verdict": verdict, "complete": meta.get("depth_m") is not None,
                     "reasons": reasons})

    def _key(r):
        base = _PAIRING_RANK.get(r["verdict"], 3)
        if not r["complete"]:      # unknown metadata must not outrank a known verdict / read as valid
            base = max(base, 3)
        if r["spectral"] is False:  # no .data_spec/.swdir → unusable by our reader, rank last
            base = 4
        # THREE-WAY, not two: confirmed-available (0) beats UNKNOWN (1) beats confirmed-absent (2).
        # Testing only `is False` let None — "not probed, therefore unknown" — escape demotion and
        # tie with a confirmed live directional buoy. Unknown must never rank as well as confirmed;
        # equally it must not be treated as absent, or one transient probe failure would discard a
        # good buoy. So it sorts between them, ahead of distance.
        spec_rank = {True: 0, None: 1, False: 2}[r["spectral"]]
        return (base, spec_rank, r["d"])
    rows.sort(key=_key)
    return rows


FIND_BUOY_FALLBACK_KM = 40.0   # depth-unconfirmed acceptance only within this range (Option-2 net)


def _depth_unconfirmed_valid(r):
    """Option-2 fallback — fires ONLY when depth didn't resolve. A candidate with NO depth may still
    be accepted as VALID on payload + distance + exposure iff it is a Waverider (or a CDIP
    'Nearshore' station), within FIND_BUOY_FALLBACK_KM, publishes spectra (CONFIRMED, see below), and
    _score_pairing already rated it VALID REFERENCE (which already means no sheltered/complex/
    structural flag). It can NEVER fire for a known-deep buoy: that scores STRUCTURALLY INVALID (not
    VALID) and carries a resolved depth, so the 44098-class depth veto is preserved.

    Spectra must be CONFIRMED (True), not merely not-known-absent. This path already accepts a buoy
    whose DEPTH never resolved; letting it also accept an UNPROBED directional axis would stack two
    unknowns into a "VALID REFERENCE" verdict — which is how ljpc1, publishing nothing at all, was
    recommended. Hence `is not True`, not `is False`: unknown declines HERE, even though it still
    ranks ahead of confirmed-absent in _rank_candidates (a transient probe failure must not discard
    a buoy from the list — it must only stop it being upgraded on faith). NOTE this also means the
    OFFLINE seed path (nothing probed → every spectral None) no longer fallback-accepts a
    depth-unconfirmed candidate; that output is already labelled as superseded by the Mac run, and
    declining is the conservative direction."""
    if (r.get("depth") is not None or r.get("verdict") != "VALID REFERENCE"
            or r.get("spectral") is not True):
        return False
    if r.get("d", 1e9) > FIND_BUOY_FALLBACK_KM:
        return False
    payload = (r.get("payload") or "").lower()
    name = (r.get("name") or "").lower()
    return ("waverider" in payload) or ("nearshore" in name) or ("cdip" in name)


def _not_dead(r):
    """A row is not KNOWN-dead if its age is unknown (None — offline / not probed, metadata
    fallback still allowed) or within FIND_BUOY_LIVENESS_MAX_AGE_H. A KNOWN-old observation
    (age > threshold) blocks selection — a live-metadata / dead-buoy candidate is not usable."""
    age = r.get("age_h")
    return age is None or age <= FIND_BUOY_LIVENESS_MAX_AGE_H


# --------------------------------------------------------------------------- #
# --find-buoy WRONG-SIDE-OF-HEADLAND gate.                                      #
# A headland between the spot and a candidate buoy means the buoy measures a    #
# different sea than the spot receives (a cape removes whole swell windows), so #
# a direction PASS against it would be false confidence. Validated 2026-07-24   #
# against 27 real pairings: on the Cape Canaveral set this cleanly separated 6  #
# wrong pairings (mid-path land crossings 4.8–13.7 km through Merritt Island /  #
# Canaveral) from 21 correct ones (nearest approach ~2.6 km), and the split held #
# at EVERY area threshold 100–5000 km² — the boundary is physical, not tuned.   #
#                                                                               #
# SCOPE LIMIT (do not mistake its reach): this detects WRONG-SIDE pairing across #
# RESOLVED LAND only (GSHHG full-res). It does NOT detect window-shadowing by    #
# SHOALS (e.g. Cape Fear / Frying Pan Shoals shadowing the Grand Strand from the #
# north): shoals are bathymetry, absent from GSHHG at any resolution, and remain #
# a MANUAL call.                                                                 #
#                                                                               #
# OWN-LANDMASS EXCLUSION (fixed 2026-07-27, mfl batch). A nearshore buoy sits    #
# AGAINST land by definition, so a spot-to-buoy geodesic that runs ROUGHLY       #
# PARALLEL to a straight coast clips the very landmass BOTH ends hang off. That  #
# clipped shoreline grew with along-coast distance and nothing else, so 41122    #
# (Hollywood Beach, ~200 m off the beach) was rejecting spots on the SAME        #
# unbroken Atlantic beach — hillsboro 1.6 km of "crossing" at 34 km, juno 63.1   #
# km at 99 km — while spots a few km away (dania, sunny-isles) cleared purely    #
# because their shorter path accumulated less clipped shore. Scaling with        #
# distance rather than with any cape is the signature of the bug.                #
#                                                                               #
# Only land that genuinely SEPARATES spot from buoy may count, so the landmass   #
# the buoy itself sits against is excluded — but ONLY when the path merely       #
# GRAZES it. Two cases keep counting it, which is what preserves the real        #
# rejections: (1) the buoy is INSIDE the polygon (a station in the marsh is by   #
# definition behind that land), and (2) the path TRAVERSES the polygon rather    #
# than grazing its shoreline — the buoy sits on the far side. Traverse vs graze  #
# is measured as how far INLAND the crossing gets from the shoreline, which      #
# unlike chord length does not grow with along-coast distance: a chord across a  #
# gently convex coast stays within a few km of the beach however long it is,     #
# while crossing the Florida peninsula to the Everglades f1 stations penetrates  #
# tens of km. A crossing of any OTHER polygon is untouched by all of this.       #
#                                                                               #
# READ THIS BEFORE TOUCHING THE GRAZE CEILING: on full-res GSHHG the entire      #
# North American mainland is a SINGLE polygon, so for essentially every East     #
# Coast buoy the "own landmass" IS the mainland and the graze test is the        #
# NORMAL path, not a rare edge case — a cape and the beach 100 km away belong to #
# one and the same polygon. The inland-penetration measure is therefore the only #
# thing separating a real headland from a coast-parallel clip along the whole    #
# seaboard, and its margin is ~2 km wide (see the constant below).               #
# --------------------------------------------------------------------------- #
FIND_BUOY_HEADLAND_AREA_KM2 = 500.0      # a crossed land polygon must exceed this to be a "headland"
                                          #   (500 sits mid-band of the 100–5000 km² clean range → drift-tolerant)
FIND_BUOY_HEADLAND_MID_START_KM = 3.0    # trim the spot's own shore / barrier off the near end
FIND_BUOY_HEADLAND_END_TRIM_KM = 2.0     # trim the near-buoy end
FIND_BUOY_HEADLAND_DENSIFY_M = 200.0     # geodesic vertex spacing (fine enough not to skip a barrier)
# Graze ceiling for the buoy's OWN landmass only (see OWN-LANDMASS EXCLUSION above): a crossing that
# never gets further than this from that polygon's shoreline is a coast-parallel clip, not a headland
# the path must round. Sits an order of magnitude below a peninsula traverse (the 11–94 km Everglades
# f1 crossings run tens of km inland) and above the few-km inland reach of a long coastal chord.
#
# LOWERED 8.0 → 3.0 (2026-07-28) — at 8.0 this test SWALLOWED REAL HEADLANDS. On full-res GSHHG the
# whole North American mainland is ONE polygon, so every offshore East Coast buoy resolves that
# polygon as its "own landmass" and EVERY mainland crossing was routed into the graze test rather
# than counted. Cape Canaveral crossings run 33–78 km of land but reach only 4.1–5.4 km INLAND, all
# under the old 8.0 bar, so all six wrong-side Canaveral pairings were silently cleared and the
# detector was a NO-OP for the entire mainland coast. The measured margins that set this value:
#     must-CLEAR along-coast pairings reach at most   2.15 km inland
#     must-FLAG Canaveral headland crossings reach     4.14 km inland (min of the six)
# 3.0 sits in that 2.15–4.14 km gap. It is a MEASURED boundary, not a tuned one — if it is ever
# changed, BOTH real-GSHHG acceptance bars must be re-run (test_headland_27_pair_regression: exactly
# 6 FLAG / 21 clear, and test_headland_mfl_regression: 4 clear / 2 FLAG). The gap is ~2 km wide, so
# unlike the 100–5000 km² area floor this constant is NOT drift-tolerant.
FIND_BUOY_HEADLAND_OWN_GRAZE_KM = 3.0
FIND_BUOY_HEADLAND_GRAZE_SAMPLES = 25    # penetration is smooth along the path; ~25 samples finds its max
# Distance ceiling: apply the wrong-side REJECTION only within this range. The 6 validated true
# positives were all nearshore (spot→buoy 41–96 km); 110 km catches them with margin yet stays
# inside the 150 km search radius. Beyond it, diffraction/refraction wrap swell into a headland's lee
# and a distant wrong-side buoy can still track the spot's sea — so above the ceiling we DOWNGRADE to
# a logged note rather than hard-reject (a safeguard against over-rejecting a buoy wrapping saves).
FIND_BUOY_HEADLAND_MAX_KM = 110.0


def _tree_idx(item, geom_list):
    """STRtree results are indices on shapely 2 and geometries on shapely 1 — normalise to an index
    into *geom_list* (identity match, never geometry equality). Same shim as orientation._as_geom,
    resolved the other way round because the crossing loop compares indices."""
    if hasattr(item, "__index__"):
        return int(item)
    return next((i for i, g in enumerate(geom_list) if g is item), None)


def _headland_own_landmass(buoy, land):
    """(polygon_index, contains) for the landmass the BUOY sits against — the polygon containing it
    if it is inside one (a marsh/bay station: that land is behind it by definition), else the polygon
    NEAREST to it (a nearshore buoy hangs ~200 m off its own coast). Reuses land.polygon_tree, the
    same STRtree the raycast queries; no second index and no coastline_tree dependency, so the
    synthetic LandIndex stand-ins in the tests exercise this path too. (None, False) when it cannot
    be resolved — the caller then counts every crossing, i.e. falls back to the old behaviour."""
    from shapely.geometry import Point
    p = Point(buoy[1], buoy[0])
    try:
        for c in land.polygon_tree.query(p):
            i = _tree_idx(c, land.polygons)
            if i is not None and land.polygons[i].intersects(p):
                return i, True                     # buoy is ON/INSIDE this land — never excluded
    except Exception:
        pass
    try:
        return _tree_idx(land.polygon_tree.nearest(p), land.polygons), False
    except Exception:
        return None, False


def _headland_inland_penetration_km(cross, poly, geod):
    """Deepest the crossing *cross* (a LineString inside *poly*) gets from *poly*'s shoreline, in km.
    This is the traverse-vs-graze measure: a coast-parallel chord stays within a few km of the beach
    no matter how long it is, while a path across a peninsula runs tens of km inland. Sampled at
    FIND_BUOY_HEADLAND_GRAZE_SAMPLES points (penetration varies smoothly, so the max is not missed)."""
    from shapely.geometry import Point
    from shapely.ops import nearest_points
    shore = poly.exterior
    cs = list(cross.coords)
    stride = max(1, len(cs) // FIND_BUOY_HEADLAND_GRAZE_SAMPLES)
    worst = 0.0
    for lo, la in cs[::stride]:
        a, b = nearest_points(Point(lo, la), shore)
        _, _, d = geod.inv(a.x, a.y, b.x, b.y)
        worst = max(worst, d / 1000.0)
    return worst


def _headland_land_chord_km(spot, buoy, land, detail=None, *,
                            near_trim_km=FIND_BUOY_HEADLAND_MID_START_KM,
                            end_trim_km=FIND_BUOY_HEADLAND_END_TRIM_KM,
                            area_km2=FIND_BUOY_HEADLAND_AREA_KM2,
                            own_graze_km=FIND_BUOY_HEADLAND_OWN_GRAZE_KM):
    """Geodesic length (km) of the LONGEST mid-path land crossing between *spot* and *buoy*, or 0.0
    if the mid-path geodesic crosses no land polygon larger than *area_km2*. *spot*
    and *buoy* are (lat, lng); *land* is a geodata.LandIndex (its .polygons list + .polygon_tree
    STRtree — the SAME GSHHG full-res index the enrich raycast loads; no second loader). Ports the
    validated harness verbatim: densify to ~FIND_BUOY_HEADLAND_DENSIFY_M, then drop the first
    *near_trim_km* (the spot's own shore/barrier) and the last *end_trim_km* (near the buoy).
    land None → 0.0 (offline / no GSHHG: not checked).

    THE FOUR KEYWORD CONSTANTS ARE SCALE, NOT POLICY, and every caller in this module uses the
    defaults. They default to the FIND_BUOY_HEADLAND_* values calibrated for 10–110 km spot→buoy
    pairings, so --find-buoy and both real-GSHHG acceptance bars exercise EXACTLY the numbers they
    were validated against. They are parameters ONLY so scripts/node_headland_calibrate.py can
    re-run the same geometry at a spot→node scale of ~1 km and reproduce why placement does no
    land-crossing check there (at buoy scale the 3.0 + 2.0 km combined trim returns 0.0 outright
    on 490 of the 491 placed spots; re-scaled it produced 23 false positives across seven WFOs).
    PLACEMENT DOES NOT CALL THIS — see the note above select_node.
    NOT _ray_linestring — that casts a fixed-length ray along a BEARING; here we need the actual
    spot→buoy SEGMENT — but it reuses the same pyproj.Geod densify + shapely-intersection primitives.

    The landmass the buoy sits against is skipped when the path only GRAZES its shoreline (see
    OWN-LANDMASS EXCLUSION above) — that is the along-coast false positive. *detail*, if a dict is
    passed, is filled with {own_idx, own_contains, own_chord_km, own_penetration_km, own_excluded}
    for the caller to surface; it is an out-param so the return type stays a bare float."""
    if land is None:
        return 0.0
    from pyproj import Geod
    from shapely.geometry import LineString
    geod = Geod(ellps="WGS84")
    (sla, sln), (bla, bln) = spot, buoy
    _, _, dist_m = geod.inv(sln, sla, bln, bla)
    total_km = dist_m / 1000.0
    if total_km <= near_trim_km + end_trim_km:
        return 0.0                                             # nothing left after trimming both ends
    n = max(4, int(dist_m / FIND_BUOY_HEADLAND_DENSIFY_M))
    pts = [(sln, sla)] + [(lo, la) for lo, la in geod.npts(sln, sla, bln, bla, n - 1)] + [(bln, bla)]
    cum, acc = [0.0], 0.0
    for (l1, a1), (l2, a2) in zip(pts[:-1], pts[1:]):
        _, _, d = geod.inv(l1, a1, l2, a2); acc += d / 1000.0; cum.append(acc)
    lo_km, hi_km = near_trim_km, total_km - end_trim_km
    mid = [p for p, c in zip(pts, cum) if lo_km <= c <= hi_km]
    if len(mid) < 2:
        return 0.0
    line = LineString(mid)
    own_idx, own_contains = _headland_own_landmass(buoy, land)
    if detail is not None:
        detail.update(own_idx=own_idx, own_contains=own_contains,
                      own_chord_km=0.0, own_penetration_km=None, own_excluded=False)
    best = 0.0
    for idx in land.polygon_tree.query(line):                 # same STRtree query as _ray_hits
        i = _tree_idx(idx, land.polygons)
        if i is None:
            continue
        poly = land.polygons[i]
        area_m2, _ = geod.geometry_area_perimeter(poly)
        if abs(area_m2) / 1e6 <= area_km2:      # at buoy scale: a barrier island / inlet bank, not a headland
            continue
        inter = line.intersection(poly)
        crossings = []
        for g in getattr(inter, "geoms", [inter]):
            if getattr(g, "geom_type", "") != "LineString" or g.is_empty:
                continue                                       # a tangential point touch is not a crossing
            cs = list(g.coords)
            chord = 0.0
            for (l1, a1), (l2, a2) in zip(cs[:-1], cs[1:]):
                _, _, d = geod.inv(l1, a1, l2, a2); chord += d / 1000.0
            crossings.append((chord, g))
        if not crossings:
            continue
        longest = max(c for c, _ in crossings)
        # The buoy's OWN landmass: a coast-parallel GRAZE of the shore both ends hang off does not
        # separate them, so it is not a headland. It still counts when the buoy is INSIDE this land,
        # or when the path TRAVERSES it (the buoy is on the far side) — the two real-rejection cases.
        if i == own_idx and not own_contains:
            pen = max(_headland_inland_penetration_km(g, poly, geod) for _, g in crossings)
            grazes = pen <= own_graze_km
            if detail is not None:
                detail.update(own_chord_km=longest, own_penetration_km=pen, own_excluded=grazes)
            if grazes:
                continue
        best = max(best, longest)
    return best


def _headland_verdict(spot, buoy, land, *, max_km=FIND_BUOY_HEADLAND_MAX_KM, **scale):
    """{"chord_km", "dist_km", "reject", "note"} for the wrong-side-of-headland check between *spot*
    and *buoy* (both (lat, lng)). reject = a headland crosses the mid-path AND the buoy is within
    *max_km*. note = it crosses but the buoy is beyond the ceiling (wrapping may save it — surfaced,
    not rejected). land None → never rejects (offline / no GSHHG: not checked).
    Also carries own_* diagnostics: when the buoy's own landmass was crossed but EXCLUDED as a
    coast-parallel graze, own_excluded is True and own_chord_km / own_penetration_km say what was
    skipped and why — so a cleared along-coast pairing is auditable, not silent.

    *max_km* and **scale (near_trim_km / end_trim_km / area_km2 / own_graze_km, forwarded to
    _headland_land_chord_km) default to the buoy-scale FIND_BUOY_HEADLAND_* constants, so an
    unqualified call — every --find-buoy caller, both real-GSHHG acceptance bars — behaves exactly
    as before. Only scripts/node_headland_calibrate.py passes anything else."""
    dist_km = _haversine_km(spot[0], spot[1], buoy[0], buoy[1])
    detail = {}
    chord = _headland_land_chord_km(spot, buoy, land, detail=detail, **scale)
    crosses = chord > 0.0
    within = dist_km < max_km
    return {"chord_km": chord, "dist_km": dist_km,
            "reject": crosses and within, "note": crosses and not within,
            "own_excluded": bool(detail.get("own_excluded")),
            "own_chord_km": detail.get("own_chord_km") or 0.0,
            "own_penetration_km": detail.get("own_penetration_km")}


def _best_valid(rows):
    """The best USABLE reference: a depth-resolved VALID REFERENCE that is publishing spectra
    (spectral not known-absent → a 404 is now 'False' and rejected), is not KNOWN-dead (recent
    observation), and is not on the WRONG SIDE of a headland (headland_reject) — or, only when depth
    is unavailable, a close Waverider / CDIP-nearshore accepted on payload+distance
    (_depth_unconfirmed_valid). Never a MARGINAL/INVALID, never a known-deep buoy, never a buoy with
    no directional axis, never a buoy silent past the liveness bar, never a buoy a headland sits
    between. Rows are ranked depth-resolved-first, so a confirmed VALID is always preferred over a
    fallback. None = the honest 'none qualifies' signal."""
    return next((r for r in rows
                 if r["verdict"] == "VALID REFERENCE" and r["spectral"] is not False
                 and _not_dead(r) and not r.get("headland_reject")
                 and (r["complete"] or _depth_unconfirmed_valid(r))), None)


def _resolve_find_target(spot, near, near_buoy):
    """((lat, lng), label) for --find-buoy from --near 'lat,lng' | --spot name | --near-buoy id.
    Read-only. Raises ValueError with a clear message when it cannot resolve."""
    if near:
        p = [x for x in near.replace(" ", "").split(",") if x]
        if len(p) != 2:
            raise ValueError("--near expects 'lat,lng'")
        return (float(p[0]), float(p[1])), f"{float(p[0]):.3f},{float(p[1]):.3f}"
    if spot:
        for s in (json.loads(ENRICHED.read_text()) if ENRICHED.exists() else []):
            if _slug(s.get("name", "")) == _slug(spot):
                la, ln = s.get("lat"), s.get("lng")
                if la is not None and ln is not None:
                    return (float(la), float(ln)), f"{s.get('name', spot)}"
        raise ValueError(f"spot '{spot}' not found in spots_enriched.json")
    if near_buoy:
        bid = str(near_buoy).lower()
        try:
            from ..enrichment.geodata import load_ndbc_active_stations
            for s in load_ndbc_active_stations():
                if s["id"] == bid:
                    return (s["lat"], s["lng"]), f"buoy {near_buoy} ({s.get('name', '')})"
        except Exception:  # noqa: BLE001
            pass
        seed = _FIND_BUOY_COORD_SEED.get(bid)
        if seed:
            return (seed[0], seed[1]), f"buoy {near_buoy} ({seed[2]}) [cited seed — Mac confirms]"
        raise ValueError(f"buoy '{near_buoy}' not in the active list or the cited seed — run on the Mac")
    raise ValueError("give one of --spot NAME, --near lat,lng, or --near-buoy ID")


def find_buoy(wfo, target, radius_km=150.0, *, label=""):
    """READ-ONLY — rank NDBC buoys near *target* (lat,lng) as candidate nearshore directional
    references, reusing the --pairing-audit scorer (single source of truth). Enumerates the live
    NDBC active-station list (Mac) and probes each candidate's spectral-file availability; offline
    it falls back to a small CITED Gulf-of-Maine seed and says so. Changes NO assignment, tags
    nothing — it only reports which buoy we SHOULD use (or that none qualifies). Returns 0."""
    tlat, tlng = target
    # Do NOT trust a cached roster: refetch activestations.xml if it is stale BEFORE enumerating
    # candidates (a days-stale roster is exactly how a removed buoy stayed a listed candidate).
    roster_age, roster_refreshed, roster_stale = None, False, False
    try:
        from ..enrichment.geodata import load_ndbc_active_stations
        roster_age, roster_refreshed, roster_stale = _find_buoy_roster_freshness()
        stations = load_ndbc_active_stations()
    except Exception:  # noqa: BLE001
        stations = []
    offline = not stations
    if offline:
        stations = [{"id": i, "lat": la, "lng": ln, "name": nm}
                    for i, (la, ln, nm) in _FIND_BUOY_COORD_SEED.items()]

    print(f"=== FIND BUOY — best VALID + LIVE directional reference near "
          f"{label or f'{tlat:.3f},{tlng:.3f}'} (wfo {wfo}, ≤{radius_km:.0f} km) — READ-ONLY ===")
    if offline:
        print("  ⚠ live NDBC activestations.xml unavailable (sandbox): showing the CITED Gulf-of-Maine")
        print("    candidate seed only. The FULL enumeration + station-page depth/payload + .data_spec")
        print("    availability + liveness probe runs on the Mac and supersedes this offline floor.\n")
    elif roster_refreshed:
        print(f"  roster: refetched activestations.xml (was {roster_age:.0f} h stale).")
    elif roster_stale:
        print(f"  ⚠ activestations.xml is {roster_age:.0f} h stale and could NOT be refreshed — the "
              "candidate list may be outdated and LIVENESS below is UNKNOWN, not current.\n")

    # Live probes are single-attempt per buoy and only run live; cap them and SAY if capped.
    from .ndbc_spectral import hours_since_last_obs
    PROBE_CAP = 30
    within = [s for s in stations if _haversine_km(tlat, tlng, s["lat"], s["lng"]) <= radius_km]
    probed = {"n": 0}
    assess = {}   # bid -> {"spectral": True/False/None, "detail": str|None, "age_h": int|None}

    def _spec(bid):
        if offline or probed["n"] >= PROBE_CAP:
            assess[bid] = {"spectral": None, "detail": "not probed (offline/cap)", "age_h": None}
            return None
        probed["n"] += 1
        state, detail = _probe_direction_axis(bid)          # .data_spec: available / 404 / timeout
        age = hours_since_last_obs(bid) if state == "available" else None   # liveness only if publishing
        spectral = {"available": True, "unavailable": False, "unknown": None}[state]
        assess[bid] = {"spectral": spectral, "detail": detail, "age_h": age}
        return spectral

    rows = _rank_candidates(tlat, tlng, stations, radius_km, spectral_fn=_spec)
    if not rows:
        print(f"  no active NDBC stations within {radius_km:.0f} km.")
        return 0
    for r in rows:                                          # fold liveness + probe detail onto each row
        r.update(assess.get(r["id"], {"spectral": r.get("spectral"), "detail": None, "age_h": None}))
    if not offline and len(within) > PROBE_CAP:
        print(f"  (.data_spec availability + liveness probed for the {PROBE_CAP} nearest of "
              f"{len(within)} candidates to stay polite; narrow --radius-km to probe the rest.)\n")

    # GATE ORDER (unchanged): depth veto (_score_pairing verdict) → liveness (_not_dead) → spectral
    # (_probe_direction_axis) → THEN wrong-side-of-headland, evaluated here ONLY for candidates that
    # already passed the first three (already shallow, live, publishing a directional axis). This
    # reuses the SAME GSHHG full-res index the enrich raycast loads (load_land_index → .polygons /
    # .polygon_tree); None offline / without GSHHG → headland is a no-op and NEVER rejects.
    land = None
    if not offline:
        try:
            from ..enrichment.geodata import load_land_index
            land = load_land_index()
        except Exception:  # noqa: BLE001
            land = None
    station_ll = {s["id"]: (s["lat"], s["lng"]) for s in stations}
    for r in rows:
        if not (r["verdict"] == "VALID REFERENCE" and r["spectral"] is not False and _not_dead(r)):
            continue                                  # depth / liveness / spectral already excluded it
        bll = station_ll.get(r["id"])
        if bll is None:
            continue
        r["headland"] = _headland_verdict(target, bll, land)
        if r["headland"]["reject"]:
            r["headland_reject"] = True

    print(f"  {'#':>2} {'buoy':>6} {'dist':>6} {'depth':>7} {'payload':<20} {'spec':>4} {'age':>6} verdict")
    for i, r in enumerate(rows, 1):
        dep = f"{r['depth']:.0f} m" if r["depth"] is not None else "  —"
        pay = (r["payload"] or "unknown")[:20]
        # '?' not '—': unknown must be visibly DIFFERENT from confirmed, and '—' is the same filler
        # the depth/age columns use for "no data", which reads as absence rather than as unchecked.
        sp = {True: "yes", False: "NO", None: "?"}[r["spectral"]]
        age_h = r.get("age_h")
        age_s = "—" if age_h is None else (f"{age_h}h" if age_h < 48 else f"{age_h // 24}d")
        live_status, live_text = _liveness_verdict(age_h, roster_stale=bool(roster_stale))
        if r["spectral"] is False:            # 404/410 (or empty) — PERMANENT: no directional axis
            vd = f"DIRECTION UNAVAILABLE — {r.get('detail') or '.data_spec absent'} (height ref only)"
        elif live_status == "dead":           # known-old newest observation
            vd = live_text
        elif r["spectral"] is None and not offline and not roster_stale:
            vd = f"DIRECTION UNKNOWN — {r.get('detail') or 'probe failed'} (transient; retry / Mac)"
        elif not r["complete"]:               # depth didn't resolve: fallback-accepted, or truly unknown
            vd = (f"{r['verdict']} (depth unconfirmed — accepted on payload+distance)"
                  if _depth_unconfirmed_valid(r) else "UNKNOWN (metadata → Mac)")
        else:
            vd = r["verdict"]
        # Never let a bare verdict imply the directional axis was checked when it was not. The
        # branches above catch the live probe-failure case; this catches the rest (offline, stale
        # roster, or simply beyond the probe cap), where the row would otherwise print a clean
        # "VALID REFERENCE" for a station nobody asked about.
        if r["spectral"] is None:
            vd = f"{vd} · direction axis UNCONFIRMED ({r.get('detail') or 'not probed'})"
        if r["spectral"] is not False and live_status != "dead":   # append the liveness evidence
            vd = f"{vd} · {live_text}"
        # Wrong-side-of-headland is its OWN verdict (never collapsed into a generic INVALID), and it
        # carries its evidence — the crossed-land chord + the buoy distance — so a rejection is never
        # invisible. Above the ceiling it downgrades to a kept note instead.
        hv = r.get("headland")
        if r.get("headland_reject"):
            vd = (f"WRONG SIDE OF HEADLAND — crosses {hv['chord_km']:.1f} km land, "
                  f"buoy {hv['dist_km']:.0f} km")
        elif hv and hv.get("note"):
            vd = (f"{vd} · headland note: crosses {hv['chord_km']:.1f} km land but buoy "
                  f"{hv['dist_km']:.0f} km > {FIND_BUOY_HEADLAND_MAX_KM:.0f} km ceiling — kept (wrap may save)")
        if hv and hv.get("own_excluded"):
            # the along-coast case: say so out loud, so a cleared pairing is auditable rather than silent
            vd = (f"{vd} · along-coast: {hv['own_chord_km']:.1f} km clipped off the buoy's OWN landmass "
                  f"ignored (reaches only {hv['own_penetration_km']:.1f} km inland ≤ "
                  f"{FIND_BUOY_HEADLAND_OWN_GRAZE_KM:.0f} km — a graze, not a headland)")
        print(f"  {i:>2} {r['id']:>6} {r['d']:>5.0f}k {dep:>7} {pay:<20} {sp:>4} {age_s:>6} {vd}  "
              f"{r['name'][:20]}")
        for rs in r["reasons"]:
            print(f"        · {rs}")

    best = _best_valid(rows)
    print("\n  ── recommendation ──")
    if best and roster_stale:
        print(f"  ⚠ best on metadata is {best['id']} ({best['name']}, {best['d']:.0f} km) — but the "
              "roster is stale/unrefreshable so LIVENESS is UNKNOWN. Do NOT commit until a fresh run "
              "confirms it is currently reporting.")
    elif best and best["complete"]:
        _, lt = _liveness_verdict(best.get("age_h"), roster_stale=bool(roster_stale))
        print(f"  ✓ USE {best['id']} ({best['name']}) — a VALID nearshore directional reference: "
              f"{best['d']:.0f} km, depth {best['depth']:.0f} m, {best['payload']}; {lt}.")
    elif best:   # Option-2 fallback: depth didn't resolve, accepted on payload+distance
        _, lt = _liveness_verdict(best.get("age_h"), roster_stale=bool(roster_stale))
        print(f"  ✓ USE {best['id']} ({best['name']}) — VALID (depth unconfirmed — accepted on "
              f"payload+distance): {best['d']:.0f} km, {best['payload'] or 'Waverider/CDIP nearshore'}; "
              f"{lt}. Confirm depth on the Mac.")
    else:
        print("  ✗ NO VALID + LIVE nearshore directional reference within radius. Every candidate is a")
        print("    deep/offshore platform (structural regime mismatch), differently exposed, a marginal")
        print("    payload, DEAD (not currently reporting), without a directional axis (.data_spec 404),")
        print("    or on the WRONG SIDE of a headland — none is a valid, live shallow-coast directional")
        print("    proxy. These spots CANNOT be direction-trust-gated from NDBC: they stay on the HEIGHT")
        print("    gate + the raycast/WW3 swell-window tier. That is the architecture-correct answer,")
        print("    NOT a failure.")
        marg = next((r for r in rows if r["verdict"] == "MARGINAL" and r["spectral"] is not False
                     and _not_dead(r) and not r.get("headland_reject")), None)
        if marg:
            mdep = f"depth {marg['depth']:.0f} m" if marg["depth"] is not None else "depth unconfirmed"
            print(f"  (least-bad — still only MARGINAL, do NOT treat as valid: {marg['id']} "
                  f"{marg['name']}, {marg['d']:.0f} km, {mdep}, {marg['payload'] or 'unknown'}.)")
    print("\n  (READ-ONLY: no assignment changed, nothing tagged/untagged, spots_enriched.json untouched.)")
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--validate", action="store_true", help="fetch one WFO cycle, place + sample (Mac)")
    ap.add_argument("--trustcheck", action="store_true", help="NWPS-vs-buoy trust gate (Mac)")
    ap.add_argument("--start-offset", type=int, default=0,
                    help="--reverify-tagged only: rotate the (sorted) zone list to start at this "
                         "index and wrap. Default 0 = unchanged order, so a Mac run is unaffected. "
                         "The scheduled workflow passes the run number so a time-capped run covers a "
                         "different window each time instead of always dying around zone 15.")
    ap.add_argument("--reverify-tagged", dest="reverify_tagged", action="store_true",
                    help="re-run the rebuilt gate against all tagged nwps zones (read-only report; Mac)")
    ap.add_argument("--depth-experiment", dest="depth_experiment", action="store_true",
                    help="re-run the gate at NEAREST vs SEAWARD node per tagged zone (read-only; Mac)")
    ap.add_argument("--pairing-audit", dest="pairing_audit", action="store_true",
                    help="score each tagged zone's buoy as a directional reference (read-only; known+Mac)")
    ap.add_argument("--find-buoy", dest="find_buoy", action="store_true",
                    help="rank NDBC buoys near a target as candidate directional references (read-only)")
    ap.add_argument("--spot", default=None, help="--find-buoy target: a spot name from spots_enriched.json")
    ap.add_argument("--near", default=None, help="--find-buoy target: 'lat,lng'")
    ap.add_argument("--near-buoy", dest="near_buoy", default=None,
                    help="--find-buoy target: search near an existing buoy id")
    ap.add_argument("--radius-km", type=float, default=None,
                    help="radius (km): --depth-experiment seaward re-pick (default 6), "
                         "--find-buoy candidate search (default 150)")
    ap.add_argument("--wfo", default=None,
                    help="NWPS WFO grid to fetch. For --validate it is ALSO the roster default "
                         "when --batch is absent (every nwps_wfo==WFO spot); omit --wfo entirely "
                         "to fall back to the okx_pilot.json set. --find-buoy / --trustcheck "
                         "default to okx when --wfo is omitted.")
    ap.add_argument("--batch", default=None,
                    help="comma-separated slugs to validate, loaded from spots_enriched.json "
                         "(each spot keeps its own nwps_wfo tag); overrides the --wfo roster default")
    ap.add_argument("--buoy", default="44025", help="trust-check buoy id (default 44025)")
    ap.add_argument("--cycles", type=int, default=4, help="recent NWPS cycles to assemble (default 4)")
    a = ap.parse_args(argv)
    if a.selftest:
        return _selftest()
    if a.validate:
        return validate_batch(a.batch, a.wfo)
    if a.reverify_tagged:
        try:
            return reverify_tagged(n_cycles=a.cycles, start_offset=a.start_offset)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ reverify needs live NOMADS+NDBC+eccodes ({type(e).__name__}: {e}) — run on the Mac.")
            return 0
    if a.depth_experiment:
        try:
            return depth_experiment(n_cycles=a.cycles, radius_km=(a.radius_km or 6.0))
        except Exception as e:  # noqa: BLE001
            print(f"⚠ depth experiment needs live NOMADS+NDBC+eccodes ({type(e).__name__}: {e}) — run on the Mac.")
            return 0
    if a.pairing_audit:
        return pairing_audit()
    if a.find_buoy:
        try:
            target, label = _resolve_find_target(a.spot, a.near, a.near_buoy)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ --find-buoy: {type(e).__name__}: {e}")
            return 0
        return find_buoy(a.wfo or "okx", target, radius_km=(a.radius_km or 150.0), label=label)
    if a.trustcheck:
        wfo = a.wfo or "okx"   # --wfo default is now None (for --validate); okx here as before
        print(f"=== NWPS {wfo.upper()} trust check vs NDBC {a.buoy} (Stage 1: height-primary, "
              "energy-weighted, tiered; Mac) ===")
        try:
            blat, blng = _buoy_latlng(a.buoy)   # NDBC active-station metadata; raises if unknown
            res = trust_check(wfo, a.buoy, blat, blng, n_cycles=a.cycles)
            v = res["verdict"]

            def _f(x, s):
                return s.format(x) if isinstance(x, (int, float)) and x == x else "—"
            print(f"buoy {a.buoy}: HEIGHT {v} (r={_f(res.get('height_r'), '{:.3f}')}) — the primary gate.")
            print(f"  direction (energy-weighted): circ_std={_f(res.get('dir_circ_std_w'), '{:.0f}°')} "
                  f"bias={_f(res.get('dir_bias_w'), '{:+.0f}°')}  [unweighted "
                  f"{_f(res.get('dir_circ_std_u'), '{:.0f}°')}/{_f(res.get('dir_bias_u'), '{:+.0f}°')}]  "
                  f"tier={res.get('tier')} → {'clears tier' if res.get('dir_flag') else 'FLAGGED (over tier)'}; "
                  f"{res.get('n_qualifying', 0)} comparable hr / {res.get('n_events', 0)} events "
                  f"(Rayleigh p={_f(res.get('dir_rayleigh_p'), '{:.2f}')})")
            print("  direction is a rolling, energy-weighted flag — it does not block a region on one window; "
                  "accumulate via --reverify-tagged and check --pairing-audit.")
            _print_trust_diag(a.buoy, blat, blng, res)
        except Exception as e:  # noqa: BLE001
            print(f"⚠ trust check needs live NOMADS+NDBC+cfgrib/eccodes ({type(e).__name__}: {e}) — run on the Mac.")
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
