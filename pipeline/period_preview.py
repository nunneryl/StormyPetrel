"""Period preview: THE MERGE GATE for the swell train the site shows (forecast/shown_period.py).

READ-ONLY. It SELECTs from the spots, forecasts and buoy_observations tables, reads NWS Surf Zone
Forecasts from the Iowa Environmental Mesonet and zone outlines from api.weather.gov, and writes
nothing to Supabase. The only files it writes are its own report (--out, --csv, --compare-out).

    python -m pipeline.period_preview [--roster ROSTER.json] [--since ISO] [--archive-since ISO]
        [--out OUT.json] [--csv HOURS.csv] [--compare-out COMPARE.json]

THE DEFECT. The rated train is WW3's dominant SWELL partition; the wind sea is a separate
channel. Jacksonville Beach Pier, 2026-10-27: a 0.8 ft, 3 s swell partition was rated beside a
7.8 ft, 9 s wind sea, so the period tile read 3 s and #283's surf height shoaled at 3 s.

THE FIX (display only). On an hour rated on a WW3 swell partition, show the wind sea when it is
bigger and at least MIN_LONGER_S (0.5 s) longer than the rated partition (LONGER), or at least
DOMINANCE_K (4) times its height whatever its period (DOMINANT, run 56), unless it comes from
BLOCKED_OUTSIDE_DEG (90) or more outside the spot's swell window (run 57): its period and
direction on the site and in the daily report, its period for the surf height's shoaling. Stars
stay on the rated partition.

1. THE ARCHIVE (since --archive-since, default 2026-09-07; every archived NWPS hour, all spots).
   By state, among hours rated on a WW3 swell partition:
     big_wind_sea   wind sea over 2x the rated partition and longer-period (run 55's criterion);
     switched       the fix's rule, split into its LONGER and DOMINANT cases, and at each K in
                    KS (3, 4, 6);
     bigger_shorter the wind sea is bigger but NOT longer (run 55's definition; at 4x and over
                    these now switch, DOMINANT, and the shown period goes down);
     marginal       bigger and longer, but by under 0.5 s: switched in run 55, not now;
     blocked        switched at DOMINANCE_K but for the window: the wind sea is 90 degrees or
                    more outside it (shown_period.wind_sea_blocked); these hours are also listed
                    with the buoy beside them, as they would have been shown;
     unmatched      no swell partition carries the rated period and direction (never switched).
   For each K: the switched hours by state with the buoy beside them, how many have the wind sea
   OUTSIDE the spot's swell window (interpret.in_any_arc), by how far outside (under 45, 45 to
   90, 90 or more: the rule excludes the last), with WINDOW_EXAMPLES examples each from
   California and Hawaii, and the headline paths, all switched hours and the bigger-but-shorter
   ones.
   Jacksonville Beach Pier, every hour of 2026-10-26 to 28 UTC, switched or not: every train,
   the rated partition, the shown train, the surf height at each period, the buoy, and why.
   Hatteras (Rodanthe, Salvo, S-Turns, Outer Banks Fishing Pier), 2026-09-23 17-19Z: the
   published height traced step by step (hatteras_hour), and across the archive how many
   NWPS-override hours have the wind sea over 4x the rated swell with the stored alignment term
   (dir_gain) under 0.5.
   On the switched hours: the period shown before and after, the surf height before and after
   (uncalibrated spots' NWPS-override hours), and the nearest buoy's (roster nearest_buoy_id)
   observation within 30 minutes of the hour: its Hs, dominant period (DPD) and swell period.
   On the bigger_shorter hours, which still show the rated (longer) period: by state, the wind
   sea / rated swell Hs ratio and the rated swell's Hs; on their surf hours, the height shoaled at
   the rated period over the height at the wind sea's (does the scrap's period overstate it?),
   all and at a ratio of 4 or more; and the TOP_LOPSIDED most lopsided hours, both trains and
   the buoy.
   The switched hours split by the path that produced the headline (headline_path: #283's surf
   shoal, calibrated, MOP, other), here and in the published window: whether each path still
   reads the rated period through the face's period factor (PATH_DEPENDS, from the code), and
   the headline if that factor used the shown period. Report only: those paths are unchanged.

2. THE PUBLISHED WINDOW (every published hour from --since, default now) — THE GATE. Each row is
   taken back to before the surf step (face_ft_raw, the stamp without its surf mark) and run
   through apply_surf_height, the code interpret.main runs:
     stars              0 differences;
     other fields       0 differences outside face_ft and face_correction_version;
     #283 reproduced    every surf row the fix does not switch comes back byte for byte as
                        published (face_ft and face_correction_version);
     switched rows      gain ":windsea" on the mark and nothing else but face_ft;
     unexpected         0: no row gains or loses its surf mark, no other row moves;
     badges             no hour changes basis (daily_report.height_basis, the site's rule).
   Beside it: the period tile before and after, the homepage, and THE STARS IF THE RATING USED
   THE WIND SEA (question 3): every NWPS-override hour is replayed through nwps_stars from its
   stored inputs at the rated train; where that reproduces the published stars, the switched
   hours are rated again on the wind sea's period and direction. Informational: the fix does not
   change the rating.

3. AGAINST THE NWS. face_compare over the archive, mainland headline hours: the display at the
   rated period (as #283 shipped it, 'display_rated') must reproduce run 54's display table up to
   run 54's end (RUN54_UNTIL; shares within 0.01, counts within 1%) — part of the gate; then the
   display with the fix, by region, over the same window (the methodology post's numbers), and
   on the switched hours alone, split by case, with the hours that moved between inside, below
   and above. Beside it, for comparison only, a VARIANT the site does not ship
   ('display_variant'): the LONGER case shoaled at the wind sea's period as the fix does, the
   DOMINANT case kept at the rated period.
"""
from __future__ import annotations

import argparse
import csv
import heapq
import json
import math
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median

from .forecast.shown_period import (BLOCKED_OUTSIDE_DEG, DOMINANCE_K, MIN_LONGER_S,
                                    WW3_IDENTITY_SOURCES, rated_partition, shown_train,
                                    switch_case, wind_sea_dominates)
from .forecast.surf_height import (M_TO_FT, NWPS_OVERRIDE_SOURCES, SURF_K, SURF_MARK_TAG,
                                   WIND_SEA_MARK, apply_surf_height, is_surf_row, sized_hs,
                                   typical_face_ft)

PUB_COLS = ("spot_id, valid_time, hs, tp, dp, swell_hs, swell_tp, swell_dp, swell_source, "
            "swell_1_hs, swell_1_tp, swell_1_dp, swell_2_hs, swell_2_tp, swell_2_dp, "
            "swell_3_hs, swell_3_tp, swell_3_dp, wind_wave_hs, wind_wave_tp, wind_wave_dp, "
            "face_ft, face_ft_raw, face_correction_version, stars, effective_size_ft, dir_gain, "
            "wind_speed, wind_dir, wind_mult, tide_mult, chop_ratio, chop_mult, period_quality")
ALLOWED = {"face_ft", "face_correction_version"}
BUOY_WINDOW_S = 1800
PERIOD_NEAR_S = 2.0
TOP_LOPSIDED = 10
LOPSIDED_RATIO = 4.0        # "lopsided": the wind sea at least 4x the rated swell's Hs
KS = (3.0, 4.0, 6.0)        # the dominance ratios run 56 compares; the site's is DOMINANCE_K
WINDOW_EXAMPLES = 5
# Jacksonville Beach Pier, every hour of 2026-10-26 to 2026-10-28 UTC, switched or not.
JAX_SPOT = "Jacksonville Beach Pier"
JAX_FROM, JAX_TO = "2026-10-26T00:00:00", "2026-10-29T00:00:00"
# Hatteras, 2026-09-23 17:00 to 19:00Z: the published height traced step by step.
HATTERAS_SPOTS = ("Rodanthe", "Salvo", "S-Turns", "Outer Banks Fishing Pier")
HATTERAS_FROM, HATTERAS_TO = "2026-09-23T17:00:00", "2026-09-23T20:00:00"

# Question 3: does each headline path still read the rated period through the face's period
# factor? From the code, not inferred from rows.
PATH_DEPENDS = {
    "surf": "no: since this change #283's surf height is shoaled at the shown period; the rated "
            "period only recovers Hs (surf_height.sized_hs inverts the override's own face)",
    "calibrated": "yes: face_ft_raw = Hs x period_factor(rated swell_tp, 'ww3') x 3.281 "
                  "(interpret.rate_spot's WW3 path, or nwps_nearshore.nwps_stars), then divided by "
                  "the spot's MOP factor",
    "mop": "no: MOP's face is its Hs x 3.281 with no period factor (mop.mop_stars); and MOP hours "
           "(swell_source cdip_mop) are never switched",
    "other": "yes: face_ft = Hs x period_factor(rated swell_tp, 'ww3') x 3.281 (interpret.rate_spot's "
             "WW3 path at an uncalibrated spot)",
}

# Run 54 (sw1-raycast, 4036ad5): the display at the rated period, mainland headline hours,
# 2026-09-07 to 2026-10-27 18:00Z, by region: (n, inside, below, above). From
# docs/face_validation/run-54/surf_preview.json, nws.all_spots.<region>.display.
RUN54_UNTIL = datetime(2026, 10, 27, 18, tzinfo=timezone.utc)
RUN54_DISPLAY = {
    "all": (234897, 0.599, 0.357, 0.044),
    "Carolinas and Georgia": (25482, 0.814, 0.07, 0.117),
    "Florida Atlantic": (29339, 0.774, 0.103, 0.123),
    "Gulf": (10610, 0.705, 0.253, 0.042),
    "Mid-Atlantic": (25090, 0.788, 0.122, 0.09),
    "New England": (34912, 0.339, 0.654, 0.007),
    "Northern California": (32477, 0.341, 0.644, 0.014),
    "Pacific Northwest": (11497, 0.171, 0.829, 0.0),
    "Puerto Rico": (12144, 0.096, 0.904, 0.0),
    "Southern California": (53346, 0.825, 0.168, 0.008),
}
REPRO_SHARE_TOL = 0.01
REPRO_N_TOL = 0.01


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _epoch(iso):
    return int(datetime.fromisoformat(str(iso).replace("Z", "+00:00")).timestamp())


def _med(xs):
    xs = [x for x in xs if x is not None]
    return round(median(xs), 2) if xs else None


def _share(flags):
    flags = [f for f in flags if f is not None]
    return round(sum(flags) / len(flags), 3) if flags else None


# --------------------------------------------------------------------------- #
# 1. The archive (pure)                                                        #
# --------------------------------------------------------------------------- #
def _ft(m):
    return None if m is None else round(m * M_TO_FT, 2)


def in_window(dp, spot):
    """Is bearing *dp* inside the spot's swell window (interpret.in_any_arc)? None when the
    spot has no arcs or there is no bearing."""
    from .interpret import in_any_arc
    arcs = spot.get("swell_window_arcs") or []
    if dp is None or not arcs:
        return None
    return bool(in_any_arc(float(dp), arcs))


def outside_by(dp, spot):
    """Degrees bearing *dp* lies outside the spot's swell window, from its padded edge
    (interpret._min_offset_from_arcs; 0 inside). None when the spot has no arcs or there is no
    bearing."""
    from .interpret import _min_offset_from_arcs
    arcs = spot.get("swell_window_arcs") or []
    if dp is None or not arcs:
        return None
    return round(float(_min_offset_from_arcs(float(dp), arcs)), 1)


def offset_band(deg):
    """'inside', 'under 45', '45-90' or '90 up' (the rule's excluded rung), or None."""
    if deg is None:
        return None
    if deg <= 0:
        return "inside"
    return "under 45" if deg < 45 else ("45-90" if deg < BLOCKED_OUTSIDE_DEG else "90 up")


def classify_hour(row, arcs, ks=KS):
    """The archive's labels for one row at a spot whose window is *arcs*: None unless it was
    rated on a WW3 swell partition, else {'unmatched': True} or the labels: the case the site
    uses (switch_case at DOMINANCE_K) and the case at each K in *ks*; blocked (it would switch at
    DOMINANCE_K but for the window), and that case (unblocked_case); big_wind_sea (over 2x and
    longer, run 55's criterion); bigger_shorter (bigger, NOT longer: run 55's definition, kept
    so the counts compare); marginal (bigger and longer, but by under MIN_LONGER_S: no longer
    switched)."""
    if row.get("swell_source") not in WW3_IDENTITY_SOURCES:
        return None
    rated = rated_partition(row)
    if rated is None:
        return {"unmatched": True}
    wh, wt = _num(row.get("wind_wave_hs")), _num(row.get("wind_wave_tp"))
    tp = _num(row.get("swell_tp"))
    bigger = wh is not None and wh > rated[0]
    longer = wt is not None and tp is not None and wt > tp
    case = switch_case(row, arcs)
    unblocked = switch_case(row, None)
    return {"unmatched": False,
            "big_wind_sea": wh is not None and wh > 2 * rated[0] and longer,
            "switched": case is not None, "case": case,
            "cases": {k: switch_case(row, arcs, k) for k in ks},
            "blocked": case is None and unblocked is not None, "unblocked_case": unblocked,
            "bigger_shorter": bigger and not longer,
            "marginal": bigger and longer and wt - tp < MIN_LONGER_S,
            "rated_hs": rated[0]}


def switch_reason(row, arcs, k=DOMINANCE_K):
    """Why *row* did or did not switch to the wind sea, in words, with its numbers."""
    if row.get("swell_source") not in WW3_IDENTITY_SOURCES:
        return f"not rated on a WW3 swell partition (swell_source {row.get('swell_source')})"
    rated = rated_partition(row)
    if rated is None:
        return "no swell partition matches the rated period and direction"
    wh, wt, wd = (_num(row.get(f"wind_wave_{x}")) for x in ("hs", "tp", "dp"))
    if wh is None or wt is None or wd is None:
        return "no wind sea height, period or direction"
    rh, rt = rated[0], rated[1]
    case = switch_case(row, arcs, k)
    if case is None and switch_case(row, None, k) is not None:
        from .interpret import _min_offset_from_arcs
        return (f"BLOCKED: the wind sea from {wd:.0f} deg is "
                f"{_min_offset_from_arcs(wd, arcs):.0f} deg outside the window "
                f"(>= {BLOCKED_OUTSIDE_DEG:g})")
    ratio = wh / rh if rh > 0 else float("inf")
    if case == "longer":
        return (f"LONGER: wind sea {wh:.2f} m over the rated {rh:.2f} m, and {wt - rt:.2f} s "
                f"longer (>= {MIN_LONGER_S})")
    if case == "dominant":
        return f"DOMINANT: wind sea {ratio:.1f}x the rated swell (>= {k:g}x), {wt - rt:+.2f} s"
    if wh <= rh:
        return f"wind sea {wh:.2f} m is not bigger than the rated {rh:.2f} m"
    return (f"wind sea bigger ({ratio:.1f}x, under {k:g}x) but {wt - rt:+.2f} s against the "
            f"rated period (needs +{MIN_LONGER_S})")


def trains(row):
    """Every train on the row, Hs in ft: swell_1..3 and the wind sea."""
    out = {}
    for p in ("swell_1", "swell_2", "swell_3", "wind_wave"):
        h = _num(row.get(f"{p}_hs"))
        if h is not None:
            out[p] = {"hs_ft": _ft(h), "tp": _num(row.get(f"{p}_tp")), "dp": _num(row.get(f"{p}_dp"))}
    return out


def rated_name(row):
    """Which swell_N the hour was rated on, or None."""
    from .forecast.shown_period import MATCH_TOL
    tp, dp = _num(row.get("swell_tp")), _num(row.get("swell_dp"))
    if tp is None or dp is None:
        return None
    for p in ("swell_1", "swell_2", "swell_3"):
        t, d = _num(row.get(f"{p}_tp")), _num(row.get(f"{p}_dp"))
        if t is not None and d is not None and abs(t - tp) <= MATCH_TOL and abs(d - dp) <= MATCH_TOL:
            return p
    return None


def _surf_at(hs, tp):
    if hs is None or tp is None or tp <= 0:
        return None
    return round(SURF_K * float(typical_face_ft([hs], [tp])[0]), 2)


def jacksonville_hour(row, spot, calibrated):
    """Every field of one Jacksonville hour the report lists, and why it did or didn't switch."""
    nwps = row.get("swell_source") in NWPS_OVERRIDE_SOURCES
    hs = sized_hs(row) if nwps else None
    arcs = spot.get("swell_window_arcs")
    tp, dp, which = shown_train(row, arcs)
    surf = nwps and not calibrated
    return {"valid_time": row["valid_time"], "hour": _epoch(row["valid_time"]),
            "source": row.get("swell_source"), "trains": trains(row),
            "rated": rated_name(row), "rated_tp": _num(row.get("swell_tp")),
            "rated_dp": _num(row.get("swell_dp")),
            "shown_tp": tp, "shown_dp": dp, "shown": which, "case": switch_case(row, arcs),
            "why": switch_reason(row, arcs), "published_ft": _num(row.get("face_ft")),
            "surf_at_rated": _surf_at(hs, _num(row.get("swell_tp"))) if surf else None,
            "surf_at_shown": _surf_at(hs, tp) if surf else None,
            "wind_in_window": in_window(_num(row.get("wind_wave_dp")), spot),
            "buoy": spot.get("nearest_buoy_id")}


def hatteras_hour(row, spot, factor, median_factor):
    """The published height of one Hatteras hour, step by step (question 3 of run 56).

    face_ft (pre-#283, and face_ft_raw since) = NWPS Hs x period_factor(swell_tp, 'ww3') x 3.281
    (nwps_nearshore.nwps_stars); the ALIGNMENT term max(0.25, cos^2(delta/2)) about the spot's
    optimal swell direction (directional_gain with no arcs, as the override calls it) multiplies
    the face into effective_size_ft, which only the STARS read; the strong-wind cap and the
    beach-break ceiling (interpret.rule_keywords) also act on the stars only. Since #283 the
    shown height is 0.70 x the breaking height of that Hs, shoaled at the shown period."""
    from .interpret import _angle_off, directional_gain, period_factor, rule_keywords
    hs = _num(row.get("hs"))
    tp, dp = _num(row.get("swell_tp")), _num(row.get("swell_dp"))
    opt = spot.get("optimal_swell_dir")
    axis = opt if opt is not None else spot.get("orientation_deg")
    shown_tp, shown_dp, which = shown_train(row, spot.get("swell_window_arcs"))
    face_raw = _num(row.get("face_ft_raw")) or _num(row.get("face_ft"))
    pf = period_factor(tp, "ww3") if tp else None
    out = {"spot": spot.get("name"), "valid_time": row["valid_time"],
           "source": row.get("swell_source"), "nwps_cycle": row.get("nwps_cycle"),
           "nwps_hs_m": hs, "nwps_hs_ft": _ft(hs), "nwps_swell_hs_m": _num(row.get("swell_hs")),
           "trains": trains(row), "rated": rated_name(row), "rated_tp": tp, "rated_dp": dp,
           "axis_deg": axis, "rated_offset_deg": None if dp is None or axis is None else round(_angle_off(dp, axis), 1),
           "period_factor": pf,
           "face_from_hs": None if hs is None or pf is None else round(hs * pf * M_TO_FT, 2),
           "face_ft_raw": face_raw, "published_ft": _num(row.get("face_ft")),
           "alignment_stored": _num(row.get("dir_gain")),
           "alignment_rated": None if dp is None else round(directional_gain(dp, [], axis, spot.get("orientation_deg")), 3),
           "alignment_wind_sea": None if row.get("wind_wave_dp") is None else round(
               directional_gain(float(row["wind_wave_dp"]), [], axis, spot.get("orientation_deg")), 3),
           "effective_size_ft": _num(row.get("effective_size_ft")),
           "stars": row.get("stars"), "wind_speed": _num(row.get("wind_speed")),
           "wind_dir": _num(row.get("wind_dir")), "wind_mult": _num(row.get("wind_mult")),
           "chop_mult": _num(row.get("chop_mult")),
           "rated_in_window": in_window(dp, spot), "wind_in_window": in_window(_num(row.get("wind_wave_dp")), spot),
           "surf_at_rated_period": _surf_at(sized_hs(row), tp),
           "surf_at_shown_period": _surf_at(sized_hs(row), shown_tp), "shown": which,
           "why": switch_reason(row, spot.get("swell_window_arcs"))}
    eff = out["effective_size_ft"]
    if eff is not None and tp and row.get("wind_dir") is not None and row.get("wind_speed") is not None:
        try:
            rules = rule_keywords(spot, float(row["wind_dir"]), float(row["wind_speed"]), tp, eff,
                                  factor, median_factor)
            out["star_cap"] = rules.get("star_cap")
            out["closeout_face_ft"] = rules.get("closeout_face_ft")
        except Exception as e:                                    # noqa: BLE001 - reported
            out["rules_error"] = f"{type(e).__name__}: {e}"[:120]
    return out


class Archive:
    """on_rows hook for face_compare.run: counts by state, and keeps the hours switched at any K
    in KS (KS[0] holds the others), the bigger-but-shorter hours, Jacksonville's listing and the
    Hatteras trace."""

    KEYS = ("hours", "ww3_identity", "unmatched", "big_wind_sea", "switched", "switched_longer",
            "switched_dominant", "blocked", "blocked_longer", "blocked_dominant",
            "bigger_shorter", "marginal", "nwps_ratio_over_4",
            "nwps_ratio_over_4_alignment_under_half") + tuple(f"switched_k{k:g}" for k in KS)

    def __init__(self, roster_by_name, top=TOP_LOPSIDED, factors=None, median_factor=None):
        self.by_state = defaultdict(Counter)
        self.records = []
        self.roster = roster_by_name
        self.factors = factors or {}
        self.median_factor = median_factor
        # The bigger-but-shorter hours, switched or not: per state, each hour's wind-sea /
        # rated-swell Hs ratio and the rated swell's Hs (ft); the uncalibrated NWPS-override ones'
        # (state, ratio, Hs, rated tp, wind tp) for the shoaled height at each period; and the
        # TOP most lopsided as full records.
        self.bs_ratio = defaultdict(list)
        self.bs_rated_ft = defaultdict(list)
        self.bs_surf = []
        self.top, self.top_n = [], top
        self.jacksonville = []
        self.hatteras = []

    def __call__(self, s, spot, rows, calibrated):
        st = s.get("state") or "?"
        c = self.by_state[st]
        name = s.get("name")
        for r in rows:
            c["hours"] += 1
            if name == JAX_SPOT and JAX_FROM <= r["valid_time"][:19] < JAX_TO:
                self.jacksonville.append(jacksonville_hour(r, spot, calibrated))
            if name in HATTERAS_SPOTS and HATTERAS_FROM <= r["valid_time"][:19] < HATTERAS_TO:
                from .interpret import face_factors_by_name
                f = face_factors_by_name([spot], self.factors).get(name)
                self.hatteras.append(hatteras_hour(r, spot, f, self.median_factor))
            lab = classify_hour(r, spot.get("swell_window_arcs"))
            if lab is None:
                continue
            c["ww3_identity"] += 1
            if lab["unmatched"]:
                c["unmatched"] += 1
                continue
            for key in ("big_wind_sea", "switched", "blocked", "bigger_shorter", "marginal"):
                c[key] += lab[key]
            if lab["case"]:
                c[f"switched_{lab['case']}"] += 1
            if lab["blocked"]:
                c[f"blocked_{lab['unblocked_case']}"] += 1
            for k, case in lab["cases"].items():
                c[f"switched_k{k:g}"] += case is not None
            wh = _num(r.get("wind_wave_hs"))
            if r.get("swell_source") in NWPS_OVERRIDE_SOURCES and lab["rated_hs"] > 0 \
                    and wh is not None and wh > LOPSIDED_RATIO * lab["rated_hs"]:
                c["nwps_ratio_over_4"] += 1
                g = _num(r.get("dir_gain"))
                c["nwps_ratio_over_4_alignment_under_half"] += g is not None and g < 0.5
            rec = None
            if any(lab["cases"].values()) or lab["blocked"]:
                rec = self.record(s, spot, r, calibrated, lab)
                self.records.append(rec)
            if lab["bigger_shorter"]:
                self.lopsided(s, spot, r, calibrated, lab, rec)

    def lopsided(self, s, spot, r, calibrated, lab, rec=None):
        st = s.get("state") or "?"
        wh = _num(r.get("wind_wave_hs"))
        if not lab["rated_hs"] or lab["rated_hs"] <= 0:
            return
        ratio = wh / lab["rated_hs"]
        self.bs_ratio[st].append(ratio)
        self.bs_rated_ft[st].append(lab["rated_hs"] * M_TO_FT)
        rec = rec or self.record(s, spot, r, calibrated, lab)
        if rec["surf"] and rec["wind_tp"] is not None and rec["wind_tp"] > 0:
            self.bs_surf.append((st, ratio, rec["hs_m"], rec["rated_tp"], rec["wind_tp"]))
        key = (ratio, -rec["hour"], rec["spot"])
        if len(self.top) < self.top_n:
            heapq.heappush(self.top, (key, rec))
        elif key > self.top[0][0]:
            heapq.heapreplace(self.top, (key, rec))

    def record(self, s, spot, r, calibrated, lab):
        nwps = r.get("swell_source") in NWPS_OVERRIDE_SOURCES
        hs = sized_hs(r) if nwps else _num(r.get("hs"))
        return {"spot": s["name"], "state": s.get("state") or "?", "valid_time": r["valid_time"],
                "hour": _epoch(r["valid_time"]), "source": r.get("swell_source"),
                "calibrated": calibrated, "buoy": spot.get("nearest_buoy_id"),
                "path": headline_path(r.get("swell_source"), calibrated),
                "cases": {str(k): v for k, v in lab["cases"].items()},
                "case": lab["case"], "blocked": lab["blocked"],
                "unblocked_case": lab["unblocked_case"], "bigger_shorter": lab["bigger_shorter"],
                "rated_hs_m": lab["rated_hs"], "rated_tp": _num(r.get("swell_tp")),
                "rated_dp": _num(r.get("swell_dp")),
                "wind_hs_m": _num(r.get("wind_wave_hs")), "wind_tp": _num(r.get("wind_wave_tp")),
                "wind_dp": _num(r.get("wind_wave_dp")),
                "rated_in_window": in_window(_num(r.get("swell_dp")), spot),
                "wind_in_window": in_window(_num(r.get("wind_wave_dp")), spot),
                "wind_outside_deg": outside_by(_num(r.get("wind_wave_dp")), spot),
                "hs_m": hs, "published": _num(r.get("face_ft")),
                "surf": nwps and not calibrated and hs is not None}

    def switched(self, k=DOMINANCE_K):
        """The records switched at *k* (the site's rule at DOMINANCE_K)."""
        return [r for r in self.records if r["cases"][str(k)]]

    def blocked(self):
        """The records the window exclusion removed at DOMINANCE_K: they would have switched."""
        return [r for r in self.records if r["blocked"]]

    def finish(self):
        """Surf height before (rated period) and after (wind sea's) on the surf rows."""
        rs = [r for r in self.records if r["surf"]]
        if rs:
            before = typical_face_ft([r["hs_m"] for r in rs], [r["rated_tp"] for r in rs])
            after = typical_face_ft([r["hs_m"] for r in rs], [r["wind_tp"] for r in rs])
            for r, b, a in zip(rs, before, after):
                r["surf_before"] = round(SURF_K * float(b), 2)
                r["surf_after"] = round(SURF_K * float(a), 2)
        for r in self.records:
            r.setdefault("surf_before", None)
            r.setdefault("surf_after", None)
        # The bigger-but-shorter surf hours: the height shoaled at the rated (longer) period
        # against at the wind sea's (shorter) period, where the energy is.
        self.bs_overstate = []
        if self.bs_surf:
            at_rated = typical_face_ft([x[2] for x in self.bs_surf], [x[3] for x in self.bs_surf])
            at_wind = typical_face_ft([x[2] for x in self.bs_surf], [x[4] for x in self.bs_surf])
            for (st, ratio, *_), a, b in zip(self.bs_surf, at_rated, at_wind):
                if b > 0:
                    self.bs_overstate.append((st, ratio, float(a) / float(b)))
        self.jacksonville.sort(key=lambda h: h["hour"])
        self.hatteras.sort(key=lambda h: (h["valid_time"], h["spot"]))

    def lopsided_examples(self):
        """The TOP most lopsided bigger-but-shorter hours, most lopsided first."""
        return [rec for _k, rec in sorted(self.top, key=lambda kr: kr[0], reverse=True)]

    def bigger_shorter_table(self):
        """By state: the wind-sea / rated-swell Hs ratio and the rated swell's Hs (ft) on the
        bigger-but-shorter hours, and on their surf hours the height shoaled at the rated period
        over the height at the wind sea's."""
        states = sorted(self.bs_ratio)
        over = defaultdict(list)
        over4 = defaultdict(list)
        for st, ratio, x in getattr(self, "bs_overstate", []):
            for k in (st, "all"):
                over[k].append(x)
                if ratio >= LOPSIDED_RATIO:
                    over4[k].append(x)
        out = {}
        for st in states + ["all"]:
            ratios = (self.bs_ratio[st] if st != "all"
                      else [x for s in states for x in self.bs_ratio[s]])
            rated = (self.bs_rated_ft[st] if st != "all"
                     else [x for s in states for x in self.bs_rated_ft[s]])
            out[st] = {
                "hours": len(ratios),
                "ratio": quantiles(ratios, (0.5, 0.9, 0.99, 1.0)),
                "ratio_bands": {"1-2": sum(1 < x <= 2 for x in ratios),
                                "2-4": sum(2 < x <= 4 for x in ratios),
                                "4-8": sum(4 < x <= 8 for x in ratios),
                                "over 8": sum(x > 8 for x in ratios)},
                "rated_hs_ft": quantiles(rated, (0.1, 0.5, 0.9)),
                "surf_hours": len(over[st]),
                "shown_over_wind_sea_height": quantiles(over[st], (0.5, 0.9, 1.0)),
                "surf_hours_ratio_4_up": len(over4[st]),
                "shown_over_wind_sea_height_ratio_4_up": quantiles(over4[st], (0.5, 0.9, 1.0)),
            }
        return out

    def table(self):
        out = {}
        tot = Counter()
        for st, c in sorted(self.by_state.items()):
            tot.update(c)
            out[st] = {k: c[k] for k in self.KEYS}
        out["all"] = {k: tot[k] for k in self.KEYS}
        return out


def by_k(records):
    """For each K in KS: the switched hours by state (summarize_switched, with the buoy), split by
    case; the hours whose wind sea is outside the spot's window, by state, with WINDOW_EXAMPLES
    examples each from California and Hawaii; and the headline paths."""
    out = {}
    for k in KS:
        rs = [r for r in records if r["cases"][str(k)]]
        cases = defaultdict(Counter)
        win = defaultdict(Counter)
        for r in rs:
            for st in (r["state"], "all"):
                cases[st][r["cases"][str(k)]] += 1
                win[st]["switched"] += 1
                win[st]["wind_outside"] += r["wind_in_window"] is False
                win[st]["no_window"] += r["wind_in_window"] is None
                win[st]["rated_outside"] += r["rated_in_window"] is False
                band = offset_band(r.get("wind_outside_deg"))
                if band and band != "inside":
                    win[st][f"wind_outside_{band}"] += 1
        examples = {}
        for st in ("California", "Hawaii"):
            out_w = sorted((r for r in rs if r["state"] == st and r["wind_in_window"] is False),
                           key=lambda r: (-(r["wind_hs_m"] or 0), r["hour"], r["spot"]))
            examples[st] = [dict(example(r), case=r["cases"][str(k)],
                                 rated_in_window=r["rated_in_window"]) for r in out_w[:WINDOW_EXAMPLES]]
        out[f"{k:g}"] = {"switched": summarize_switched(rs),
                         "cases": {st: dict(c) for st, c in sorted(cases.items())},
                         "window": {st: dict(c) for st, c in sorted(win.items())},
                         "window_examples": examples,
                         "paths": paths(rs),
                         "paths_bigger_shorter": paths([r for r in rs if r["bigger_shorter"]])}
    return out


def quantiles(xs, ps):
    """{p: nearest-rank quantile of *xs*, 3 dp} for each p in *ps* (1.0 is the maximum);
    None for each when *xs* is empty."""
    s = sorted(xs)
    if not s:
        return {str(p): None for p in ps}
    return {str(p): round(s[max(0, math.ceil(p * len(s)) - 1)], 3) for p in ps}


def headline_path(source, calibrated):
    """What produced an hour's headline height: 'calibrated' (the MOP factor divided it),
    'mop' (MOP's own Hs), 'surf' (#283's shoal on an NWPS-override hour) or 'other' (the
    producer's face: the WW3 path and the fallbacks)."""
    if calibrated:
        return "calibrated"
    if source == "cdip_mop":
        return "mop"
    if source in NWPS_OVERRIDE_SOURCES:
        return "surf"
    return "other"


def paths(records):
    """Question 3's split of the switched hours by headline path. For each path: whether the
    headline still reads the rated period (PATH_DEPENDS), and what the headline would be if its
    face period factor used the shown period: face x period_factor(shown, 'ww3') /
    period_factor(rated, 'ww3'), the table both the WW3 path and the NWPS override size with.
    The surf path's after is the fix's own height (surf_after)."""
    from .interpret import period_factor
    by = defaultdict(list)
    for r in records:
        by[r["path"]].append(r)
    out = {}
    for path in ("surf", "calibrated", "mop", "other"):
        rs = by.get(path, [])
        pairs = []
        for r in rs:
            b = r.get("published") if path != "surf" else r.get("surf_before")
            if b is None or r.get("rated_tp") is None or r.get("wind_tp") is None:
                continue
            if path == "surf":
                a = r.get("surf_after")
            else:
                a = b * period_factor(r["wind_tp"], "ww3") / period_factor(r["rated_tp"], "ww3")
            if a is not None:
                pairs.append((b, a))
        ratios = [a / b for b, a in pairs if b]
        out[path] = {"hours": len(rs), "depends_on_rated_period": PATH_DEPENDS[path],
                     "headline_before": _med(b for b, _ in pairs),
                     "headline_if_shown_period": _med(a for _, a in pairs),
                     "ratio": quantiles(ratios, (0.5, 0.9, 1.0)),
                     "changed": sum(abs(a - b) >= 0.005 for b, a in pairs)}
    return out


def join_buoys(records, obs_by_buoy, window=BUOY_WINDOW_S):
    """Each record gains the nearest observation of its buoy within *window* seconds of its hour
    (buoy_hs_m, buoy_tp, buoy_swell_tp), or None for each. *obs_by_buoy*: {id: [(epoch, obs)]}."""
    import bisect
    keys = {b: [t for t, _ in obs] for b, obs in obs_by_buoy.items()}
    for r in records:
        r.update(buoy_hs_m=None, buoy_tp=None, buoy_swell_tp=None)
        obs = obs_by_buoy.get(r["buoy"])
        if not obs:
            continue
        ts = keys[r["buoy"]]
        i = bisect.bisect_left(ts, r["hour"])
        best = min((j for j in (i - 1, i) if 0 <= j < len(ts)),
                   key=lambda j: abs(ts[j] - r["hour"]), default=None)
        if best is None or abs(ts[best] - r["hour"]) > window:
            continue
        o = obs[best][1]
        r.update(buoy_hs_m=_num(o.get("hs")), buoy_tp=_num(o.get("tp")),
                 buoy_swell_tp=_num(o.get("swell_tp")))
    return records


def example(r):
    """One lopsided hour for the report: both trains (Hs in ft) and the buoy."""
    ft = lambda m: None if m is None else round(m * M_TO_FT, 2)      # noqa: E731
    return {"spot": r["spot"], "state": r["state"], "valid_time": r["valid_time"],
            "source": r["source"], "path": r["path"],
            "ratio": round(r["wind_hs_m"] / r["rated_hs_m"], 2),
            "rated": {"hs_ft": ft(r["rated_hs_m"]), "tp": r["rated_tp"], "dp": r["rated_dp"]},
            "wind_sea": {"hs_ft": ft(r["wind_hs_m"]), "tp": r["wind_tp"], "dp": r["wind_dp"]},
            "buoy": r["buoy"], "buoy_hs_ft": ft(r.get("buoy_hs_m")), "buoy_dpd": r.get("buoy_tp"),
            "published_ft": r["published"]}


def summarize_switched(records):
    """By state: the switched hours' periods, heights and the buoy beside them."""
    by = defaultdict(list)
    for r in records:
        by[r["state"]].append(r)
    by["all"] = records
    out = {}
    for st, rs in sorted(by.items(), key=lambda kv: (kv[0] != "all", kv[0])):
        wb = [r for r in rs if r["buoy_tp"] is not None]
        sr = [r for r in rs if r["surf_before"] is not None]
        sb = [r for r in sr if r["buoy_hs_m"] is not None]
        out[st] = {
            "hours": len(rs), "with_buoy": len(wb),
            "period_before": _med(r["rated_tp"] for r in rs),
            "period_after": _med(r["wind_tp"] for r in rs),
            "buoy_dpd": _med(r["buoy_tp"] for r in wb),
            "buoy_swell_tp": _med(r["buoy_swell_tp"] for r in wb),
            "near_buoy_dpd_before": _share(abs(r["rated_tp"] - r["buoy_tp"]) <= PERIOD_NEAR_S for r in wb),
            "near_buoy_dpd_after": _share(abs(r["wind_tp"] - r["buoy_tp"]) <= PERIOD_NEAR_S for r in wb),
            "rated_hs_ft": _med(r["rated_hs_m"] * M_TO_FT for r in rs),
            "wind_sea_hs_ft": _med(r["wind_hs_m"] * M_TO_FT for r in rs),
            "model_hs_ft": _med(r["hs_m"] * M_TO_FT for r in rs if r["hs_m"] is not None),
            "buoy_hs_ft": _med(r["buoy_hs_m"] * M_TO_FT for r in wb if r["buoy_hs_m"] is not None),
            "surf_hours": len(sr),
            "surf_before": _med(r["surf_before"] for r in sr),
            "surf_after": _med(r["surf_after"] for r in sr),
            "surf_over_buoy_hs_before": _med(r["surf_before"] / (r["buoy_hs_m"] * M_TO_FT)
                                             for r in sb if r["buoy_hs_m"]),
            "surf_over_buoy_hs_after": _med(r["surf_after"] / (r["buoy_hs_m"] * M_TO_FT)
                                            for r in sb if r["buoy_hs_m"]),
        }
    return out


# --------------------------------------------------------------------------- #
# 2. The published window (pure)                                               #
# --------------------------------------------------------------------------- #
def before_surf(row):
    """The row as it left apply_face_corrections: a surf row's face back to face_ft_raw and its
    stamp without the surf mark. Any other row as it is."""
    r = dict(row)
    v = r.get("face_correction_version")
    if is_surf_row(v):
        r["face_ft"] = r.get("face_ft_raw")
        r["face_correction_version"] = v.split(SURF_MARK_TAG + ":")[0] or None
    return r


def replay(rows_by_name, roster, factors, slug_for):
    """(published, replayed, stats): every row through before_surf then apply_surf_height."""
    pub = {n: [dict(r) for r in rows] for n, rows in rows_by_name.items()}
    after = {n: [before_surf(r) for r in rows] for n, rows in rows_by_name.items()}
    stats = apply_surf_height(after, roster, factors, slug_for)
    return pub, after, stats


def gate_rows(pub, after):
    from .daily_report import height_basis
    g = Counter()
    trans = Counter()
    examples = defaultdict(list)
    for name, rows in pub.items():
        for b, a in zip(rows, after[name]):
            g["hours"] += 1
            moved = {k for k in set(a) | set(b) if a.get(k) != b.get(k)}
            if a.get("stars") != b.get("stars"):
                g["star_differences"] += 1
                examples["star"].append((name, b.get("valid_time")))
            if moved - ALLOWED:
                g["other_field_differences"] += 1
                examples["other"].append((name, b.get("valid_time"), sorted(moved - ALLOWED)))
            bv, av = b.get("face_correction_version"), a.get("face_correction_version")
            if is_surf_row(bv) != is_surf_row(av):
                g["unexpected"] += 1
                examples["unexpected"].append((name, b.get("valid_time"), bv, av))
            elif is_surf_row(av) and av.endswith(WIND_SEA_MARK) and not bv.endswith(WIND_SEA_MARK):
                g["switched"] += 1
                if av != bv + WIND_SEA_MARK:
                    g["unexpected"] += 1
                    examples["unexpected"].append((name, b.get("valid_time"), bv, av))
                g["switched_face_moved"] += a.get("face_ft") != b.get("face_ft")
            elif is_surf_row(av):
                g["surf_unswitched"] += 1
                if moved:
                    g["unswitched_mismatch"] += 1
                    examples["unswitched_mismatch"].append(
                        (name, b.get("valid_time"), b.get("face_ft"), a.get("face_ft"), bv, av))
            elif moved:
                g["unexpected"] += 1
                examples["unexpected"].append((name, b.get("valid_time"), sorted(moved)))
            bb, ab = height_basis(b), height_basis(a)
            trans[(bb, ab)] += 1
            g["badge_flips"] += bb != ab
    for k in ("star_differences", "other_field_differences", "unexpected", "switched",
              "switched_face_moved", "surf_unswitched", "unswitched_mismatch", "badge_flips"):
        g.setdefault(k, 0)
    return {"counts": dict(g),
            "transitions": {f"{b} -> {a}": n for (b, a), n in sorted(trans.items(), key=str)},
            "examples": {k: v[:10] for k, v in examples.items()}}


def tiles(pub, after, state_by_name, arcs_by_name, calibrated=frozenset()):
    """The period tile before (the rated period, as pickSwell showed it) and after, by state, on
    the hours whose shown train switches; and every switched hour for the CSV."""
    by = defaultdict(list)
    hours = []
    for name, rows in pub.items():
        arcs = arcs_by_name.get(name)
        for b, a in zip(rows, after[name]):
            tp, dp, which = shown_train(b, arcs)
            if which != "wind_sea":
                continue
            old_tp = _num(b.get("swell_tp"))
            path = headline_path(b.get("swell_source"), name in calibrated)
            fb, fa = _num(b.get("face_ft")), _num(a.get("face_ft"))
            rec = {"spot": name, "state": state_by_name.get(name, "?"),
                   "valid_time": b.get("valid_time"), "source": b.get("swell_source"),
                   "path": path, "period_before": old_tp, "period_after": tp,
                   "dir_before": _num(b.get("swell_dp")), "dir_after": dp,
                   "face_before": fb, "face_after": fa, "stars": b.get("stars"),
                   # the fields paths() reads
                   "published": fb, "rated_tp": old_tp, "wind_tp": tp,
                   "surf_before": fb if path == "surf" else None,
                   "surf_after": fa if path == "surf" else None}
            by[rec["state"]].append(rec)
            by["all"].append(rec)
            hours.append(rec)
    out = {}
    for st, rs in sorted(by.items(), key=lambda kv: (kv[0] != "all", kv[0])):
        fm = [r for r in rs if r["face_before"] is not None and r["face_after"] is not None
              and r["face_after"] != r["face_before"]]
        out[st] = {"hours": len(rs), "period_before": _med(r["period_before"] for r in rs),
                   "period_after": _med(r["period_after"] for r in rs),
                   "surf_hours": len(fm), "face_before": _med(r["face_before"] for r in fm),
                   "face_after": _med(r["face_after"] for r in fm)}
    return out, hours


def homepage(pub, after, ids, arcs_by_name, n=10):
    """"Best conditions right now": each spot's soonest hour with stars, stars highest first,
    equal stars in spot id order (frontend/app/page.tsx), with its period and height."""
    rows = []
    for name, bs in pub.items():
        i = next((j for j, r in enumerate(bs) if r.get("stars") is not None), None)
        if i is None:
            continue
        rows.append((-float(bs[i]["stars"]), ids.get(name, 0), name, bs[i], after[name][i]))
    rows.sort(key=lambda r: (r[0], r[1]))
    out = []
    for _s, _i, name, b, a in rows[:n]:
        tp_after, _dp, which = shown_train(b, arcs_by_name.get(name))
        tp_before = _num(b.get("swell_tp"))
        out.append({"spot": name, "stars": b.get("stars"),
                    "period_before": tp_before if tp_before is not None else _num(b.get("tp")),
                    "period_after": tp_after, "train_after": which,
                    "face_before": b.get("face_ft"), "face_after": a.get("face_ft")})
    return out


def stars_on_the_wind_sea(pub, roster_by_name, factors):
    """Question 3. Every NWPS-override hour replayed through nwps_stars from its stored inputs at
    the rated train; where that reproduces the published stars, the switched hours rated again
    on the wind sea's period and direction."""
    from .forecast.face_correction import median_face_factor
    from .forecast.nwps_nearshore import nwps_stars
    from .interpret import face_factors_by_name
    ceiling = face_factors_by_name(list(roster_by_name.values()), factors)
    m = median_face_factor(factors)
    c = Counter()
    diffs = Counter()
    by_state = defaultdict(Counter)

    def rate(r, spot, name, per, dirn):
        return nwps_stars(sized_hs(r), per, dirn, _num(r.get("swell_hs")),
                          spot.get("orientation_deg"), _num(r.get("wind_mult")),
                          _num(r.get("tide_mult")), optimal=spot.get("optimal_swell_dir"),
                          chop=(_num(r.get("chop_ratio")), _num(r.get("chop_mult"))), spot=spot,
                          wind_dir=_num(r.get("wind_dir")), wind_speed=_num(r.get("wind_speed")),
                          face_factor=ceiling.get(name), median_factor=m)[0]

    for name, rows in pub.items():
        spot = roster_by_name.get(name)
        if spot is None:
            continue
        for r in rows:
            if r.get("swell_source") not in NWPS_OVERRIDE_SOURCES or r.get("stars") is None:
                continue
            if sized_hs(r) is None or r.get("wind_mult") is None or r.get("tide_mult") is None \
                    or r.get("chop_mult") is None:
                c["no_inputs"] += 1
                continue
            c["replayed"] += 1
            try:
                again = rate(r, spot, name, _num(r.get("swell_tp")), _num(r.get("swell_dp")))
            except Exception:                                   # noqa: BLE001 - counted
                c["errors"] += 1
                continue
            same = again is not None and abs(again - float(r["stars"])) < 1e-9
            c["reproduced"] += same
            if not wind_sea_dominates(r, spot.get("swell_window_arcs")):
                continue
            c["switched"] += 1
            if not same:
                c["switched_not_reproduced"] += 1
                continue
            alt = rate(r, spot, name, _num(r.get("wind_wave_tp")), _num(r.get("wind_wave_dp")))
            d = round(float(alt) - float(r["stars"]), 1)
            diffs[d] += 1
            st = spot.get("state") or "?"
            by_state[st]["hours"] += 1
            by_state[st]["up"] += d > 0
            by_state[st]["down"] += d < 0
    return {"counts": dict(c), "star_change_on_switched": {str(k): v for k, v in sorted(diffs.items())},
            "by_state": {k: dict(v) for k, v in sorted(by_state.items())}}


# --------------------------------------------------------------------------- #
# 3. Against the NWS (pure)                                                    #
# --------------------------------------------------------------------------- #
def _sides(rows, face_of):
    from .face_compare import classify
    s = Counter()
    for r in rows:
        f = face_of(r)
        side = classify(f, r["lo"], r["hi"]) if f is not None else None
        if side:
            s[side] += 1
    n = sum(s.values())
    return (n, *(round(s[k] / n, 3) if n else None for k in ("inside", "below", "above")))


def _by_region(rows):
    by = defaultdict(list)
    for r in rows:
        by[r["region"]].append(r)
    by["all"] = rows
    return by


def transitions(rows, before="display_rated", after="display"):
    """{'<side> -> <side>': hours} for the rows whose side of the NWS range (inside, below,
    above) differs between the *before* and *after* heights."""
    from .face_compare import classify
    t = Counter()
    for r in rows:
        if r.get(before) is None or r.get(after) is None:
            continue
        a, b = classify(r[before], r["lo"], r["hi"]), classify(r[after], r["lo"], r["hi"])
        if a and b and a != b:
            t[f"{a} -> {b}"] += 1
    return dict(sorted(t.items()))


def _three(rs):
    """The three heights' sides and the moves from display_rated to each of the other two."""
    return {"display_rated": _sides(rs, lambda r: r.get("display_rated")),
            "display": _sides(rs, lambda r: r.get("display")),
            "display_variant": _sides(rs, lambda r: r.get("display_variant")),
            "transitions": transitions(rs),
            "transitions_variant": transitions(rs, after="display_variant")}


def nws_tables(srf_rows, until=RUN54_UNTIL, want=None):
    """{reproduction: {region: got, want, ok}, ok, fixed, switched, switched_by_case} over
    mainland headline hours before *until*. fixed (every hour), switched (the switched hours)
    and switched_by_case ({region: {case: ...}}) each give display_rated (#283), display (the
    fix) and display_variant (the comparison: DOMINANT kept at the rated period) as (n, inside,
    below, above), and the hours that moved side from display_rated to each."""
    cut = int(until.timestamp())
    head = [r for r in srf_rows if r["group"] == "headline" and r["office"] != "HFO"
            and r["hour"] < cut]
    by = _by_region(head)
    repro, ok = {}, True
    for region, w in (want or RUN54_DISPLAY).items():
        got = _sides(by.get(region, []), lambda r: r.get("display_rated"))
        good = (got[0] > 0 and abs(got[0] - w[0]) <= REPRO_N_TOL * w[0]
                and all(abs(g - x) <= REPRO_SHARE_TOL for g, x in zip(got[1:], w[1:])))
        repro[region] = {"got": got, "want": w, "ok": good}
        ok = ok and good
    order = lambda kv: (kv[0] != "all", kv[0])                     # noqa: E731
    fixed = {g: _three(rs) for g, rs in sorted(by.items(), key=order)}
    sw = _by_region([r for r in head if r.get("switched")])
    switched = {g: _three(rs) for g, rs in sorted(sw.items(), key=order)}
    by_case = {g: {c: _three([r for r in rs if r.get("case") == c]) for c in ("longer", "dominant")}
               for g, rs in sorted(sw.items(), key=order)}
    return {"reproduction": repro, "ok": ok, "fixed": fixed, "switched": switched,
            "switched_by_case": by_case}


# --------------------------------------------------------------------------- #
# I/O                                                                          #
# --------------------------------------------------------------------------- #
def _page(q):
    out, frm = [], 0
    while True:
        rows = q().range(frm, frm + 999).execute().data or []
        out.extend(rows)
        if len(rows) < 1000:
            return out
        frm += 1000


def fetch_spots(client):
    return _page(lambda: client.table("spots").select("id, name, state").order("id"))


def fetch_published(client, sid, since):
    return _page(lambda: client.table("forecasts").select(PUB_COLS).eq("spot_id", sid)
                 .eq("source", "nwps").gte("valid_time", since).order("valid_time"))


def fetch_buoys(client, ids, since):
    """{buoy id: [(epoch, obs)]} sorted by time."""
    out = {}
    for b in sorted(i for i in ids if i):
        rows = _page(lambda b=b: client.table("buoy_observations")
                     .select("buoy_id, observed_at, hs, tp, swell_hs, swell_tp")
                     .eq("buoy_id", b).gte("observed_at", since).order("observed_at"))
        out[b] = sorted(((_epoch(r["observed_at"]), r) for r in rows), key=lambda x: x[0])
    return out


def run(client, roster, since, archive_since, until, *, factors=None, compare=None):
    from .enrich import _slug_for
    from .face_compare import run as compare_run
    from .forecast.face_correction import load_face_factors
    factors = load_face_factors() if factors is None else factors
    compare = compare or compare_run
    by_name = {s["name"]: s for s in roster}

    # 2. the published window
    spots = fetch_spots(client)
    pub_rows, ids, states = {}, {}, {}
    for s in spots:
        if s["name"] not in by_name:
            continue
        rows = fetch_published(client, s["id"], since)
        if rows:
            pub_rows[s["name"]] = rows
            ids[s["name"]] = s["id"]
            states[s["name"]] = s.get("state") or "?"
    pub, after, stats = replay(pub_rows, roster, factors, _slug_for)
    g = gate_rows(pub, after)
    from .forecast.face_correction import spot_factor
    cal = {n for n in pub if spot_factor(by_name[n], factors, _slug_for) is not None}
    arcs_by_name = {n: by_name[n].get("swell_window_arcs") for n in pub}
    tile, tile_hours = tiles(pub, after, states, arcs_by_name, cal)
    q3 = stars_on_the_wind_sea(pub, {n: dict(by_name[n], state=states.get(n)) for n in pub},
                               factors)

    # 1 and 3. the archive, in face_compare's own pass
    from .forecast.face_correction import median_face_factor
    arch = Archive(by_name, factors=factors, median_factor=median_face_factor(factors))
    comp = compare(client, archive_since, until, roster, factors=factors, on_rows=arch)
    arch.finish()
    # Jacksonville's hours after the archive's end come from the published window
    seen = {h["valid_time"] for h in arch.jacksonville}
    jax_cal = JAX_SPOT in cal
    for r in pub.get(JAX_SPOT, []):
        if JAX_FROM <= r["valid_time"][:19] < JAX_TO and r["valid_time"] not in seen:
            arch.jacksonville.append(dict(jacksonville_hour(r, by_name[JAX_SPOT], jax_cal),
                                          from_published_window=True))
    arch.jacksonville.sort(key=lambda h: h["hour"])
    examples = arch.lopsided_examples()
    buoys = fetch_buoys(client, {r["buoy"] for r in arch.records + examples + arch.jacksonville},
                        archive_since.isoformat())
    for rs in (arch.records, examples, arch.jacksonville):
        join_buoys(rs, buoys)
    prod = arch.switched()
    nws = nws_tables(comp["_pairs"]["srf"])

    c = g["counts"]
    passed = (c["hours"] > 0 and c["surf_unswitched"] > 0 and c["star_differences"] == 0
              and c["other_field_differences"] == 0 and c["unexpected"] == 0
              and c["unswitched_mismatch"] == 0 and c["badge_flips"] == 0 and nws["ok"])
    return {
        "window": {"published_since": since,
                   "archive": [archive_since.isoformat(), until.isoformat()]},
        "gate": {"pass": passed, **c, "run54_reproduced": nws["ok"]},
        "apply_stats": stats, "badges": g["transitions"], "examples": g["examples"],
        "archive_by_state": arch.table(),
        "switched_vs_buoy": summarize_switched(prod),
        "blocked_vs_buoy": summarize_switched(arch.blocked()),
        "by_k": by_k(arch.records),
        "bigger_shorter_by_state": arch.bigger_shorter_table(),
        "lopsided_examples": [dict(example(r), switched=r["cases"][str(DOMINANCE_K)])
                              for r in examples],
        "paths_archive": paths(prod), "paths_published": paths(tile_hours),
        "jacksonville": arch.jacksonville, "hatteras": arch.hatteras,
        "buoys_read": {b: len(v) for b, v in buoys.items()},
        "tiles": tile, "homepage": homepage(pub, after, ids, arcs_by_name),
        "stars_on_the_wind_sea": q3,
        "nws": nws, "compare_errors": comp.get("errors"),
        "_compare": comp, "_tile_hours": tile_hours, "_records": prod,
    }


def _p(v):
    return "-" if v is None else f"{100 * v:.1f}%"


def _sides_str(t):
    n, i, b, a = t
    return f"n {n:6}  in {_p(i):>6}  below {_p(b):>6}  above {_p(a):>6}"


def print_report(rep):
    g = rep["gate"]
    print(f"\nPeriod preview: published from {rep['window']['published_since']}, archive "
          f"{rep['window']['archive'][0]} to {rep['window']['archive'][1]}")
    print(f"\nMERGE GATE {'PASS' if g['pass'] else 'FAIL'}")
    for k in ("hours", "surf_unswitched", "switched", "switched_face_moved", "star_differences",
              "other_field_differences", "unexpected", "unswitched_mismatch", "badge_flips",
              "run54_reproduced"):
        print(f"  {k:28} {g[k]}")
    print(f"  apply_surf_height stats: {rep['apply_stats']}")
    print(f"  badges: {rep['badges']}")
    for k, v in rep["examples"].items():
        print(f"  examples {k}: {v}")

    print("\n1. Archive: hours rated on a WW3 swell partition, by state")
    print(f"  {'state':22} {'hours':>8} {'WW3 id':>8} {'unmatch':>8} {'wind>2x':>8} "
          f"{'switch':>8} {'blocked':>8} {'big+short':>9}")
    for st, c in rep["archive_by_state"].items():
        print(f"  {st:22} {c['hours']:8} {c['ww3_identity']:8} {c['unmatched']:8} "
              f"{c['big_wind_sea']:8} {c['switched']:8} {c['blocked']:8} {c['bigger_shorter']:9}")

    print("\n2. Archive: switched hours — period, height and the nearest buoy (medians)")
    for st, e in rep["switched_vs_buoy"].items():
        print(f"  {st:22} n {e['hours']:6} (buoy {e['with_buoy']:6})  period {e['period_before']} -> "
              f"{e['period_after']} s, buoy DPD {e['buoy_dpd']} s (swell {e['buoy_swell_tp']} s), "
              f"within 2 s of DPD {_p(e['near_buoy_dpd_before'])} -> {_p(e['near_buoy_dpd_after'])}")
        print(f"  {'':22} Hs ft: rated swell {e['rated_hs_ft']}, wind sea {e['wind_sea_hs_ft']}, "
              f"model {e['model_hs_ft']}, buoy {e['buoy_hs_ft']} | surf (n {e['surf_hours']}) "
              f"{e['surf_before']} -> {e['surf_after']} ft, x buoy Hs {e['surf_over_buoy_hs_before']}"
              f" -> {e['surf_over_buoy_hs_after']}")

    print("\n2a. Archive: the hours the window exclusion removed (the wind sea 90 deg or more "
          "outside the window), as they would have been shown")
    for st, e in rep["blocked_vs_buoy"].items():
        print(f"  {st:22} n {e['hours']:6} (buoy {e['with_buoy']:6})  period {e['period_before']} -> "
              f"{e['period_after']} s, within 2 s of DPD {_p(e['near_buoy_dpd_before'])} -> "
              f"{_p(e['near_buoy_dpd_after'])}; surf {e['surf_before']} -> {e['surf_after']} ft")

    print("\n2b. Archive: bigger-but-SHORTER wind seas (the plain rule would lower the period)")
    for st, e in rep["bigger_shorter_by_state"].items():
        print(f"  {st:22} n {e['hours']:7}  wind/rated Hs {e['ratio']}  bands {e['ratio_bands']}  "
              f"rated Hs ft {e['rated_hs_ft']}")
        print(f"  {'':22} surf n {e['surf_hours']:6}: shown / at wind-sea period "
              f"{e['shown_over_wind_sea_height']}; ratio >= 4: n {e['surf_hours_ratio_4_up']} "
              f"{e['shown_over_wind_sea_height_ratio_4_up']}")
    print("  most lopsided:")
    for x in rep["lopsided_examples"]:
        print(f"    {x['spot']:28} {x['valid_time']}  x{x['ratio']}  rated {x['rated']}  "
              f"wind sea {x['wind_sea']}  buoy {x['buoy']} {x['buoy_hs_ft']} ft {x['buoy_dpd']} s  "
              f"shown {x['published_ft']} ft ({x['path']})")

    print("\n2d. The dominance ratio K: switched hours, the buoy, and the wind sea's window")
    for k, e in rep["by_k"].items():
        a = e["switched"]["all"]
        w = e["window"].get("all", {})
        print(f"  K {k}: n {a['hours']} {e['cases'].get('all', {})}; period {a['period_before']} -> "
              f"{a['period_after']} s, buoy DPD {a['buoy_dpd']} s, within 2 s "
              f"{_p(a['near_buoy_dpd_before'])} -> {_p(a['near_buoy_dpd_after'])}; surf "
              f"{a['surf_before']} -> {a['surf_after']} ft, x buoy Hs {a['surf_over_buoy_hs_before']} -> "
              f"{a['surf_over_buoy_hs_after']}; wind sea outside the window {w.get('wind_outside', 0)}"
              f" (under 45 {w.get('wind_outside_under 45', 0)}, 45-90 {w.get('wind_outside_45-90', 0)},"
              f" 90 up {w.get('wind_outside_90 up', 0)}; no window {w.get('no_window', 0)})")
        for st, s in e["switched"].items():
            if st == "all":
                continue
            ws = e["window"].get(st, {})
            print(f"    {st:22} n {s['hours']:6} {e['cases'].get(st, {})}  period {s['period_before']} -> "
                  f"{s['period_after']}  near DPD {_p(s['near_buoy_dpd_before'])} -> "
                  f"{_p(s['near_buoy_dpd_after'])}  surf {s['surf_before']} -> {s['surf_after']}  "
                  f"x buoy {s['surf_over_buoy_hs_before']} -> {s['surf_over_buoy_hs_after']}  "
                  f"outside window {ws.get('wind_outside', 0)}")
        for st, xs in e["window_examples"].items():
            for x in xs:
                print(f"    outside, {st}: {x['spot']} {x['valid_time']} {x['case']} x{x['ratio']} "
                      f"rated {x['rated']} (in window {x['rated_in_window']}) wind sea {x['wind_sea']}")
        for part in ("paths", "paths_bigger_shorter"):
            for path in ("calibrated", "other"):
                pe = e[part][path]
                print(f"    {part} {path}: n {pe['hours']}, {pe['headline_before']} -> "
                      f"{pe['headline_if_shown_period']} ft, ratio {pe['ratio']}, changed {pe['changed']}")

    al = rep["archive_by_state"]["all"]
    print(f"\n2e. NWPS-override hours with the wind sea over 4x the rated swell: "
          f"{al['nwps_ratio_over_4']}; of them, alignment term under 0.5: "
          f"{al['nwps_ratio_over_4_alignment_under_half']}")
    for st, c in rep["archive_by_state"].items():
        if st != "all" and c["nwps_ratio_over_4"]:
            print(f"    {st:22} {c['nwps_ratio_over_4']:7}  under 0.5: "
                  f"{c['nwps_ratio_over_4_alignment_under_half']}")

    print("\n2f. Jacksonville Beach Pier, 2026-10-26 to 2026-10-28 UTC, every hour")
    for h in rep["jacksonville"]:
        print(f"  {h['valid_time'][:16]} {h['source']} rated {h['rated']} {h['rated_tp']} s "
              f"{h['rated_dp']} | trains {h['trains']} | shown {h['shown']} {h['shown_tp']} s "
              f"{h['shown_dp']} | surf {h['surf_at_rated']} -> {h['surf_at_shown']} ft | pub "
              f"{h['published_ft']} | buoy {_ft(h.get('buoy_hs_m'))} ft {h.get('buoy_tp')} s | {h['why']}")

    print("\n2g. Hatteras, 2026-09-23 17-19Z, the published height step by step")
    for h in rep["hatteras"]:
        print(f"  {h['spot']:26} {h['valid_time'][:16]} NWPS Hs {h['nwps_hs_m']} m ({h['nwps_hs_ft']} ft) "
              f"x pf {h['period_factor']} at {h['rated_tp']} s -> {h['face_from_hs']} ft (raw "
              f"{h['face_ft_raw']}, published {h['published_ft']}); alignment stored "
              f"{h['alignment_stored']} rated {h['alignment_rated']} (offset {h['rated_offset_deg']} deg) "
              f"wind sea {h['alignment_wind_sea']}; eff {h['effective_size_ft']} stars {h['stars']} "
              f"cap {h.get('star_cap')}; surf {h['surf_at_rated_period']} -> {h['surf_at_shown_period']} "
              f"({h['shown']}); trains {h['trains']}")

    for part in ("paths_archive", "paths_published"):
        print(f"\n2c. Switched hours by headline path ({part}): the headline if its period factor "
              f"used the shown period")
        for path, e in rep[part].items():
            print(f"  {path:11} n {e['hours']:7}  {e['headline_before']} -> "
                  f"{e['headline_if_shown_period']} ft, ratio {e['ratio']}, changed {e['changed']}")
            print(f"  {'':11} depends on the rated period: {e['depends_on_rated_period']}")

    print("\n3. Published window: the period tile and the surf height on switched hours (medians)")
    for st, e in rep["tiles"].items():
        print(f"  {st:22} n {e['hours']:5}  period {e['period_before']} -> {e['period_after']} s | "
              f"surf rows {e['surf_hours']:5}  {e['face_before']} -> {e['face_after']} ft")
    print("\n  Homepage, best conditions right now")
    for i, h in enumerate(rep["homepage"], 1):
        print(f"  {i:2}. {h['spot']:30} {h['stars']} stars  period {h['period_before']} -> "
              f"{h['period_after']} s ({h['train_after']})  {h['face_before']} -> {h['face_after']} ft")

    q = rep["stars_on_the_wind_sea"]
    print("\n4. If the RATING used the wind sea (informational; the fix does not)")
    print(f"  {q['counts']}")
    print(f"  star change on switched, reproduced hours: {q['star_change_on_switched']}")
    for st, c in q["by_state"].items():
        print(f"    {st:22} {c}")

    n = rep["nws"]
    print("\n5a. Run 54's display reproduced (display at the rated period, to 2026-10-27 18:00Z)")
    for r, e in n["reproduction"].items():
        print(f"  {r:24} {_sides_str(e['got'])} | {_sides_str(e['want'])}  "
              f"{'ok' if e['ok'] else 'DIFFERS'}")
    for part in ("fixed", "switched"):
        print(f"\n5b. Against the NWS range, {part} hours: run 54's display | with the fix | "
              f"variant (DOMINANT at the rated period)")
        for r, e in n[part].items():
            print(f"  {r:24} {_sides_str(e['display_rated'])} | {_sides_str(e['display'])} | "
                  f"{_sides_str(e['display_variant'])}")
            print(f"  {'':24} moved, fix: {e['transitions']}; variant: {e['transitions_variant']}")
    print("\n5c. Switched hours by case: run 54's display | with the fix | variant")
    for r, cs in n["switched_by_case"].items():
        for c, e in cs.items():
            print(f"  {r:24} {c:8} {_sides_str(e['display_rated'])} | {_sides_str(e['display'])} | "
                  f"{_sides_str(e['display_variant'])}")
            print(f"  {'':33} moved, fix: {e['transitions']}")
    if rep.get("compare_errors"):
        print(f"\n  comparison errors: {rep['compare_errors']}")


def write_csv(path, rows):
    if not rows:
        return
    cols = list(rows[0].keys())
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)


def main(argv=None):
    from .face_compare import SINCE, print_report as compare_print
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--roster", default="pipeline/spots_enriched.json")
    p.add_argument("--since", default=None, help="ISO start of the published window (default: now)")
    p.add_argument("--archive-since", default=SINCE.isoformat())
    p.add_argument("--out", default=None)
    p.add_argument("--csv", default=None, help="the published window's switched hours")
    p.add_argument("--archive-csv", default=None, help="the archive's switched hours, with the buoy")
    p.add_argument("--compare-out", default=None, help="face_compare's JSON for the archive")
    a = p.parse_args(argv)
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    now = datetime.now(timezone.utc)
    since = a.since or now.replace(microsecond=0).isoformat()
    until = now.replace(minute=0, second=0, microsecond=0)
    with open(a.roster) as fh:
        roster = json.load(fh)
    rep = run(client, roster, since, datetime.fromisoformat(a.archive_since), until)
    comp = rep.pop("_compare")
    tile_hours = rep.pop("_tile_hours")
    records = rep.pop("_records")
    compare_print(comp)
    print_report(rep)
    if a.out:
        with open(a.out, "w") as fh:
            json.dump(rep, fh, indent=1, default=str)
    if a.csv:
        write_csv(a.csv, tile_hours)
    if a.archive_csv:
        write_csv(a.archive_csv, records)
    if a.compare_out:
        with open(a.compare_out, "w") as fh:
            json.dump({k: v for k, v in comp.items() if k != "_pairs"}, fh, indent=1)
    return 0 if rep["gate"]["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
