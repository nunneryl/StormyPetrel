"""Star preview: what the PUBLISHED forecast window would rate with a different swell window.

READ-ONLY. It SELECTs from the spots and forecasts tables and writes nothing to Supabase.
The only file it writes is its own report (--out).

    python -m pipeline.star_preview --arcs ARCS.json [--baseline BASELINE.json] [--out OUT.json]

ARCS and BASELINE are spots files in the spots_enriched.json shape; each spot's
swell_window_arcs, optimal_swell_dir, orientation_deg and mop_shore_normal are what it reads.
BASELINE is the geometry the published window was rated with (the roster on main); ARCS is
the geometry to preview.

HOW A ROW IS RE-SCORED. Every rater ends in composite_stars(effective_face, wind_mult,
tide_mult, chop_mult, period_quality), and the forecasts row stores the four multipliers, the
face, the WW3 partitions and the direction it was rated on. Only the swell window moves
here, so each row is replayed through the path that wrote it — read from its swell_source —
with the geometry swapped:

  nwps_height_ww3_dir, nwps   apply_nwps_overrides, through the rater's own nwps_stars.
                              Direction and period come from combine_ww3_partitions over
                              the row's partitions (the window chooses), and fall back to
                              NWPS dirpw/perpw (the row's dp/tp) when no partition has any
                              gain. The height takes the alignment term only, no window
                              rung (since run 19). The face scales with period_factor
                              when the dominant partition, and so the period, changes.
                              legacy_rescore keeps the rule rows were published under
                              before that: the window's gain, rung and all.
  cdip_mop                    apply_mop_overrides / mop_stars: the MOP direction against the
                              MOP shore normal; the face does not depend on the window.
  ww3                         rate_spot's WW3 path: the combined height and the dominant
                              partition are recomputed. If no partition keeps any gain the
                              hour would fall to rate_spot's NWPS path, whose inputs are not
                              stored: counted as NOT REPLAYABLE, never guessed.
  nwps_swell, buoy,           rate_spot's NWPS path: face x gain on the stored direction,
  nwps_total, none            unless the new window gives a partition gain, which moves the
                              hour onto the WW3 path (as rate_spot would).

THE REPLAY CHECK. Each row is first re-scored with BASELINE and compared with the stars
actually published. Only rows that reproduce are counted in the before/after comparison;
the rest are reported by path, so a path the replay gets wrong is visible rather than
silently mixed into the result. Stored multipliers are rounded to 3 decimals, which can
move a rating that sits on a half-star boundary.

WHAT IT CANNOT SEE. A swell window also feeds the NWPS trust tier (nwps_nearshore._spot_tier)
and, through it, which spots the trust gate reports on. That is not a per-row quantity and
is not previewed here.

THE TIDE PREVIEW (--tide). The same replay, with the swell window held and the stored
tide_mult replaced on every hour of a spot whose tide preference is UNVERIFIED — its
tide_preference_source in the live spots table is not one of config.TIDE_RATED_SOURCES
(reviewed, researched, scraped), the sources the rating trusts:

    keep        tide_mult unchanged (the replay check alone)
    neutral     tide_mult = 1.0, as if the preference were 'all'
    damp:K      tide_mult = 1 - K x (1 - tide_mult): K of the penalty kept, 0 < K < 1
    rated       the rater's own rule, on every spot: tide_mult = interpret.tide_multiplier(
                the row's tide_norm, interpret.rated_tide_preference(the spot in
                --tide-roster)). With every source unverified it must equal neutral hour for
                hour — the check that the rating now publishes what neutral previewed.

    python -m pipeline.star_preview --tide neutral,rated [--baseline B.json]
        [--tide-roster R.json] [--out OUT.json]

It also prints a census: the live spots' (tide_preference, tide_preference_source) counts,
and over the window the stored tide_mult values, by preference, and how many of them the rater's
rule reproduces from the row's tide_norm and the spot's live preference and source
(interpret.tide_multiplier of interpret.rated_tide_preference).

THE NWPS SHELTER MEASUREMENT (--nwps-shelter). Does the swell window cut, a second time, energy
NWPS already delivers? An NWPS row's height is the node's swh, after SWAN has refracted and
sheltered it; nwps_stars then multiplies it by directional_gain on the dominant WW3 partition's
deep-water direction. For every nwps_height_ww3_dir row, with the BASELINE geometry:

    rung    where the dominant partition sits: inside the window, or on the 0.40 / 0.15 rung
    share   that partition's hs² over all the swell partitions' hs²; only hours at
            SHELTER_SHARE_MIN or more are ATTRIBUTABLE, so the node's swell height is its
    R       node swell height (the row's swell_hs, NWPS shts) / the partition's deep-water hs
    R_rel   R on an outside hour / the median R of the same spot's attributable INSIDE hours in
            the same period band — which cancels shoaling and the node's depth
    real    R_rel >= SHELTER_REAL_ENERGY: the window cut an hour NWPS delivers with real energy

reported overall and by period band, state, rung and the node's distance from the spot, with
how often the row's dp (NWPS dirpw at the node) is inside the window on the outside hours. Then
every NWPS row is replayed with NO window gate (the spot's arcs removed, so directional_gain
falls to max(0.25, cos²(Δ/2)) about the optimal), against the published stars: A. Two more
replays keep the window's choice of partition and drop only its multiplier on the height —
B on that partition's direction, C on NWPS dirpw where the row has one. Each reports ups and
downs, the change by cause (same direction, another partition, or another source of
direction), and every spot with an hour moving by more than BIG_MOVE stars. Last, R: the
rater on this checkout replayed (nwps_stars), and checked against B hour for hour. All four
take legacy_rescore as their baseline, the rule the published rows carried until the
alignment-only rater ships; once its rows are published, the hours it changed read as not
reproduced against that baseline. SELECT only.

    python -m pipeline.star_preview --nwps-shelter [--baseline B.json] [--out OUT.json]

THE RATING PREVIEW (--rating). The strong-wind cap and the beach-break ceiling, replayed on every
row that reproduces its published stars with BASELINE (rows rated before the rules were wired;
once rows rated with them are published, the hours they changed read as not reproduced):

    cap         interpret.wind_star_cap: cross-shore or onshore wind above 13 kt, less a knot
                per second of period above 8 s, caps the stars (the /learn/wind ladder);
                above 25 kt measured the cap is 1.0, and 2.0 offshore
    ceiling     beach and jetty breaks over sand (is_sand_beach_break) keep
                interpret.closeout_factor of their size credit: all of it to 8 ft, then less,
                read at the CALIBRATED face where the spot has a MOP factor
                (face_correction.spot_factor) and the raw face elsewhere; the size credit
                itself stays on the raw face. Every spot it moves whose break type or bottom
                is unverified is listed
    both        the two together

Each reports the star change by source, state, cause and wind band, the 20 most-changed spots,
and the homepage's "Best conditions right now" before and after; the ceiling rules list every
hour where the ceiling at the calibrated face differs from the raw one. THE MERGE GATE for wiring
them into the rater: rater_check replays every reproduced row through the rater's own code
(wired_rater) and counts the hours where it differs from 'both', which must be none. --explain
prints every rating factor of each named spot's soonest hour, how the composite combines them,
and where its NWPS node sits. SELECT only.

    python -m pipeline.star_preview --rating cap,both [--explain slug,slug] [--baseline B.json]
        [--out OUT.json]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone
from statistics import median

from .config import TIDE_RATED_SOURCES
from .forecast.face_correction import load_face_factors, spot_factor
from .forecast.nwps_nearshore import _bearing, _haversine_km, nwps_stars
from .interpret import (
    _min_offset_from_arcs,
    combine_ww3_partitions,
    composite_stars,
    directional_gain,
    face_ft,
    in_any_arc,
    period_factor,
    COMPOSITE_FACTOR_EXPONENTS,
    _angle_off,
    beach_size_score,
    closeout_factor,
    period_quality,
    rated_tide_preference,
    rule_keywords,
    size_score,
    wind_class,
    wind_star_cap,
    KT_TO_MS,
    tide_multiplier,
    M_TO_FT,
)

NWPS_KINDS = ("nwps_height_ww3_dir", "nwps")
MOP_KINDS = ("cdip_mop",)
WW3_KINDS = ("ww3",)
GROUP = {**{k: "NWPS" for k in NWPS_KINDS}, **{k: "MOP" for k in MOP_KINDS},
         **{k: "WW3" for k in WW3_KINDS}}

PARTITION_COLS = tuple(f"{p}_{f}" for p in ("swell_1", "swell_2", "swell_3", "wind_wave")
                       for f in ("hs", "tp", "dp"))
FORECAST_COLS = ("spot_id, valid_time, hs, tp, dp, swell_hs, swell_tp, swell_dp, "
                 + ", ".join(PARTITION_COLS)
                 + ", swell_source, face_ft, face_ft_raw, dir_gain, wind_mult, tide_mult, "
                   "chop_mult, period_quality, effective_size_ft, stars, tide_norm, "
                   "wind_speed, wind_dir, chop_ratio, face_lo_ft, face_hi_ft")


def group_of(kind):
    """'NWPS', 'MOP', 'WW3' or 'other' (rate_spot's NWPS-path fallbacks)."""
    return GROUP.get(kind, "other")


def _num(v):
    return None if v is None else float(v)


def _face(row):
    """The face the rater produced, before any face correction divided it."""
    raw = row.get("face_ft_raw")
    return _num(raw if raw is not None else row.get("face_ft"))


def _partitions(row):
    """The row's WW3 partitions in the shape combine_ww3_partitions reads, or None."""
    entry = {c: row.get(c) for c in PARTITION_COLS if row.get(c) is not None}
    return entry or None


def _slug(name):
    """A spot name as the site's slug: lower case, runs of anything else to one hyphen."""
    return re.sub(r"[^a-z0-9]+", "-", (name or "").lower()).strip("-")


def _period(row):
    """The swell period a MOP or NWPS-path row was rated on: swell_tp, else tp."""
    tp = row.get("swell_tp")
    return _num(tp if tp is not None else row.get("tp"))


def _mults(row):
    return (_num(row.get("wind_mult")) if row.get("wind_mult") is not None else 1.0,
            _num(row.get("tide_mult")) if row.get("tide_mult") is not None else 1.0,
            _num(row.get("chop_mult")) if row.get("chop_mult") is not None else 1.0)


def _nwps_inputs(row, spot, use_dirpw=False):
    """(direction, period, path, swh) for an NWPS row as apply_nwps_overrides reads it: the
    spot's own arcs pick the partition (combine_ww3_partitions) and NWPS dirpw/perpw stand in
    when none keeps any gain. swh is recovered from the stored face. With *use_dirpw* the
    row's dp (NWPS dirpw) is the direction whenever it is present; the period stays the
    partition's. None when the row cannot be replayed."""
    arcs = spot.get("swell_window_arcs") or []
    optimal = spot.get("optimal_swell_dir")
    orient = spot.get("orientation_deg")
    parts = _partitions(row)
    face = _face(row)
    comb = combine_ww3_partitions(parts, arcs, optimal, orient) if parts else None
    if comb is not None:
        d, per, path = comb["dp"], comb["tp"], "nwps_height_ww3_dir"
    else:
        d, per, path = _num(row.get("dp")), _num(row.get("tp")), "nwps"
    dirpw = _num(row.get("dp"))
    if use_dirpw and dirpw is not None and comb is not None:
        d, path = dirpw, path + "+dirpw"
    old_per = _num(row.get("swell_tp"))
    if d is None or per is None or face is None or not old_per or orient is None:
        return None
    return d, per, path, face / (period_factor(old_per, "ww3") * M_TO_FT)


def _nwps_rescore(row, spot, gain_arcs, use_dirpw=False):
    """An NWPS row re-scored with its height's gain read against *gain_arcs*: the spot's own
    arcs for the rule the window published under until run 19 (the rung), [] for the
    alignment term alone (run 19's B; C with *use_dirpw*)."""
    got = _nwps_inputs(row, spot, use_dirpw)
    if got is None:
        return {"stars": None, "why": "nwps: direction, period or face missing"}
    d, per, path, swh = got
    optimal, orient = spot.get("optimal_swell_dir"), spot.get("orientation_deg")
    wm, tm, cm = _mults(row)
    dg = directional_gain(d, gain_arcs, optimal if optimal is not None else orient, orient)
    return {"stars": composite_stars(face_ft(swh, per, "ww3") * dg, wm, tm, cm,
                                     period_quality(per)),
            "gain": dg, "dir": d, "path": path}


def _rater_nwps(row, spot):
    """An NWPS row replayed through the RATER ITSELF: nwps_nearshore.nwps_stars, given the
    direction and period the window chose and the stored wind, tide and chop multipliers."""
    got = _nwps_inputs(row, spot)
    if got is None:
        return {"stars": None, "why": "nwps: direction, period or face missing"}
    d, per, path, swh = got
    wm, tm, cm = _mults(row)
    stars, face, dg, _, pq = nwps_stars(swh, per, d, None, spot.get("orientation_deg"), wm, tm,
                                        optimal=spot.get("optimal_swell_dir"),
                                        chop=(_num(row.get("chop_ratio")), cm))
    return {"stars": stars, "gain": dg, "dir": d, "path": path,
            "eff": None if face is None else face * dg, "pq": pq, "tp": per}


def legacy_rescore(row, spot):
    """rescore under the rule NWPS rows were published with until run 19: the NWPS height took
    the window's gain, rung and all. Every other row as rescore."""
    if row.get("swell_source") in NWPS_KINDS:
        return _nwps_rescore(row, spot, spot.get("swell_window_arcs") or [])
    return rescore(row, spot)


def rescore(row, spot):
    """Re-score one published row with *spot*'s geometry.

    Returns {"stars", "gain", "dir", "path"} or {"stars": None, "why": ...} when the row
    cannot be replayed from what is stored."""
    kind = row.get("swell_source")
    arcs = spot.get("swell_window_arcs") or []
    optimal = spot.get("optimal_swell_dir")
    orient = spot.get("orientation_deg")
    wm, tm, cm = _mults(row)
    parts = _partitions(row)
    face = _face(row)

    if kind in NWPS_KINDS:
        return _rater_nwps(row, spot)

    if kind in MOP_KINDS:
        sn = spot.get("mop_shore_normal")
        if sn is None:
            sn = orient
        d, pq = _num(row.get("swell_dp")), _num(row.get("period_quality"))
        if d is None or face is None or sn is None or pq is None:
            return {"stars": None, "why": "mop: direction, face, shore normal or period missing"}
        dg = directional_gain(d, arcs, optimal if optimal is not None else sn, sn)
        return {"stars": composite_stars(face * dg, wm, tm, cm, pq),
                "gain": dg, "dir": d, "path": "cdip_mop",
                "eff": face * dg, "pq": pq, "tp": _period(row)}

    comb = combine_ww3_partitions(parts, arcs, optimal, orient) if parts else None
    if comb is not None:
        fft = face_ft(comb["hs"], comb["tp"], "ww3")
        return {"stars": composite_stars(fft, wm, tm, cm, period_quality(comb["tp"])),
                "gain": comb["dir_gain"], "dir": comb["dp"], "path": "ww3",
                "eff": fft, "pq": period_quality(comb["tp"]), "tp": comb["tp"]}
    if kind in WW3_KINDS:
        return {"stars": None, "why": "ww3: no partition keeps any gain, so the hour moves to "
                                      "rate_spot's NWPS path, whose inputs are not stored"}
    d = row.get("swell_dp") if row.get("swell_dp") is not None else row.get("dp")
    pq = _num(row.get("period_quality"))
    if face is None or pq is None:
        return {"stars": None, "why": f"{kind}: face or period missing"}
    dg = directional_gain(float(d), arcs, optimal, orient) if d is not None else 0.0
    return {"stars": composite_stars(face * dg, wm, tm, cm, pq),
            "gain": dg, "dir": _num(d), "path": kind or "none",
            "eff": face * dg, "pq": pq, "tp": _period(row)}


BIG_MOVE = 1.5                 # a spot is flagged when any hour moves by more than this


def change_cause(before, after):
    """Why an hour's stars moved: 'same direction', 'another partition' (the direction moved
    and the path did not), or 'PATH -> PATH' when the direction came from somewhere else."""
    if abs(((after["dir"] - before["dir"] + 540.0) % 360.0) - 180.0) < 0.01:
        return "same direction"
    if after["path"] == before["path"]:
        return "another partition"
    return f"{before['path']} -> {after['path']}"


def compare(rows, before_spots, after_spots, names, row_after=None, rescore_after=None,
            rescore_before=None):
    """Replay every row with BASELINE and with ARCS. *names* maps spot_id -> (name, state).

    *row_after*, when given, maps a row to the row the AFTER replay reads (the tide preview
    swaps its tide_mult); each change then also records the tide it was rated on.
    *rescore_after*, when given, re-scores the AFTER replay in place of rescore, and
    *rescore_before* the BASELINE replay (the one checked against the published stars).

    Returns the report dict (see main for its keys)."""
    replay = Counter()
    unreplayable = Counter()
    changed = []
    by_group = defaultdict(Counter)
    by_state = defaultdict(Counter)
    for row in rows:
        name, state = names.get(row["spot_id"], (None, None))
        b, a = before_spots.get(name), after_spots.get(name)
        kind = row.get("swell_source") or "none"
        grp = group_of(kind)
        if b is None or a is None or row.get("stars") is None:
            unreplayable[f"{grp}: spot not in both spots files or no published stars"] += 1
            continue
        rb = (rescore_before or rescore)(row, b)
        if rb["stars"] is None:
            unreplayable[f"{grp}: {rb['why']}"] += 1
            continue
        ok = rb["stars"] == float(row["stars"])
        replay[(grp, ok)] += 1
        if not ok:
            continue
        row_a = row_after(row) if row_after else row
        ra = (rescore_after or rescore)(row_a, a)
        if ra["stars"] is None:
            unreplayable[f"{grp} (after): {ra['why']}"] += 1
            continue
        delta = round(ra["stars"] - rb["stars"], 1)
        by_group[grp][delta] += 1
        by_state[state][delta] += 1
        if delta:
            change = {"spot": name, "state": state, "valid_time": row["valid_time"],
                      "group": grp, "dir_before": rb["dir"], "dir_after": ra["dir"],
                      "gain_before": round(rb["gain"], 3), "gain_after": round(ra["gain"], 3),
                      "stars_before": rb["stars"], "stars_after": ra["stars"],
                      "delta": delta, "cause": change_cause(rb, ra)}
            if row_after:
                change.update(tide_norm=row.get("tide_norm"),
                              tide_mult_before=_mults(row)[1], tide_mult_after=_mults(row_a)[1])
            changed.append(change)
    per_spot = defaultdict(list)
    for c in changed:
        per_spot[c["spot"]].append(c)
    top = sorted(per_spot.items(),
                 key=lambda kv: (-len(kv[1]), -max(abs(c["delta"]) for c in kv[1]), kv[0]))[:20]
    big = []
    for spot, cs in per_spot.items():
        over = [c for c in cs if abs(c["delta"]) > BIG_MOVE]
        if over:
            big.append({"spot": spot, "state": cs[0]["state"], "hours_over": len(over),
                        "largest_up": max([c["delta"] for c in over if c["delta"] > 0], default=None),
                        "largest_down": min([c["delta"] for c in over if c["delta"] < 0], default=None)})
    big.sort(key=lambda r: (-r["hours_over"], r["spot"]))
    cause = Counter(f"{'up' if c['delta'] > 0 else 'down'}: {c['cause']}" for c in changed)
    return {
        "rows": len(rows),
        "replay": {f"{g} {'reproduced' if ok else 'NOT reproduced'}": n
                   for (g, ok), n in sorted(replay.items())},
        "unreplayable": dict(sorted(unreplayable.items())),
        "compared": sum(sum(c.values()) for c in by_group.values()),
        "changed_spot_hours": len(changed),
        "changed_spots": len(per_spot),
        "ups": sum(c["delta"] > 0 for c in changed),
        "downs": sum(c["delta"] < 0 for c in changed),
        "change_by_cause": dict(sorted(cause.items())),
        "spots_moving_over_1_5": big,
        "delta_by_group": {g: dict(sorted(c.items())) for g, c in sorted(by_group.items())},
        "delta_by_state": {s: dict(sorted(c.items())) for s, c in sorted(by_state.items(), key=lambda kv: str(kv[0]))},
        "top20": [{"spot": s, "changed_hours": len(cs),
                   # the largest change; the earliest such hour, since rows come in time order
                   "example": max(cs, key=lambda c: abs(c["delta"]))}
                  for s, cs in top],
    }


# --------------------------------------------------------------------------- #
# --rating: the strong-wind cap and the beach-break ceiling                    #
# --------------------------------------------------------------------------- #
# Each rule is (wind cap, beach ceiling, the ceiling also on jetty breaks over sand). Jetty
# breaks over sand are on the ceiling (decided after rating-preview run 29).
RATING_RULES = {
    "cap": (True, False, False),
    "ceiling": (False, True, True),
    "both": (True, True, True),
}


def is_sand_beach_break(spot, also_jetty=False):
    """break_type 'beach' on a 'sand' bottom (with *also_jetty*, 'jetty' on sand too)."""
    kinds = ("beach", "jetty") if also_jetty else ("beach",)
    return spot.get("break_type") in kinds and spot.get("bottom") == "sand"


def cited(spot):
    """Do both the break type and the bottom come from a source the rating trusts (the tide
    rule's config.TIDE_RATED_SOURCES: reviewed, researched, scraped)?"""
    return (spot.get("break_type_source") in TIDE_RATED_SOURCES
            and spot.get("bottom_source") in TIDE_RATED_SOURCES)


def _kt_band(kt):
    if kt is None:
        return "no wind"
    for top in (13, 15, 20, 25):
        if kt <= top:
            return f"<={top} kt"
    return ">25 kt"


def closeout_face(spot, rb, factors, calibrated=True):
    """The face the ceiling reads for a replayed hour: the effective face divided by the
    spot's MOP factor where it has one (and *calibrated*), else the effective face itself."""
    f = spot_factor(spot, factors or {}, _slug) if calibrated else None
    return rb["eff"] if f is None else rb["eff"] / f


def rating_after(row, spot, rb, rule, factors=None, calibrated=True):
    """The stars *rb* (a reproduced replay of *row*) gets under *rule*, and why it moved.

    *factors* are the MOP face factors (face_correction.load_face_factors); the ceiling reads
    the calibrated face where the spot has one, unless *calibrated* is False (the raw face
    everywhere, as rating-preview run 30 did).

    Returns (stars, cause): cause lists "wind cap" when the cap is below the stars the hour
    would otherwise get, and "beach ceiling" when the ceiling changes them."""
    cap_on, ceiling_on, jetty = RATING_RULES[rule]
    wm, tm, cm = _mults(row)
    cap = (wind_star_cap(_num(row.get("wind_dir")), _num(row.get("wind_speed")),
                         spot.get("offshore_wind_deg"), rb["tp"]) if cap_on else 5.0)
    beach = ceiling_on and is_sand_beach_break(spot, jetty)
    face = closeout_face(spot, rb, factors, calibrated) if beach else None
    uncapped = composite_stars(rb["eff"], wm, tm, cm, rb["pq"], closeout_face_ft=face)
    stars = composite_stars(rb["eff"], wm, tm, cm, rb["pq"], star_cap=cap, closeout_face_ft=face)
    cause = []
    if beach and uncapped != rb["stars"]:
        cause.append("beach ceiling")
    if cap < uncapped:
        cause.append("wind cap")
    return stars, " + ".join(cause)


def _wind(row, spot):
    """(class, knots) of a row's wind against the spot's offshore bearing, or (None, None)."""
    wd, ws, off = _num(row.get("wind_dir")), _num(row.get("wind_speed")), spot.get("offshore_wind_deg")
    if wd is None or ws is None or off is None:
        return None, None
    return wind_class(wd, off), ws / KT_TO_MS


def rating_compare(rows, spots, names, rule, factors=None):
    """Every reproduced row before and after *rule*, by source, state, cause and wind."""
    replay, unreplayable = Counter(), Counter()
    by_group, by_state = defaultdict(Counter), defaultdict(Counter)
    by_cause, by_wind, by_prov, changed = Counter(), Counter(), Counter(), []
    unverified = {}
    for row in rows:
        name, state = names.get(row["spot_id"], (None, None))
        spot = spots.get(name)
        grp = group_of(row.get("swell_source") or "none")
        if spot is None or row.get("stars") is None:
            unreplayable[f"{grp}: spot not in the spots file or no published stars"] += 1
            continue
        rb = rescore(row, spot)
        if rb["stars"] is None:
            unreplayable[f"{grp}: {rb['why']}"] += 1
            continue
        ok = rb["stars"] == float(row["stars"])
        replay[(grp, ok)] += 1
        if not ok:
            continue
        after, cause = rating_after(row, spot, rb, rule, factors)
        delta = round(after - rb["stars"], 1)
        by_group[grp][delta] += 1
        by_state[state][delta] += 1
        if delta:
            cls, kt = _wind(row, spot)
            by_cause[cause] += 1
            by_wind[f"{cls or 'no wind'} {_kt_band(kt)}"] += 1
            if "beach ceiling" in cause:
                by_prov["cited" if cited(spot) else "model recall or unattributed"] += 1
                if not cited(spot):
                    u = unverified.setdefault(name, {
                        "spot": name, "state": state, "break_type": spot.get("break_type"),
                        "break_type_source": spot.get("break_type_source"),
                        "bottom": spot.get("bottom"), "bottom_source": spot.get("bottom_source"),
                        "hours": 0, "largest_drop": 0.0, "largest_eff_ft": 0.0})
                    u["hours"] += 1
                    u["largest_drop"] = min(u["largest_drop"], delta)
                    u["largest_eff_ft"] = max(u["largest_eff_ft"], round(rb["eff"], 2))
            changed.append({"spot": name, "state": state, "valid_time": row["valid_time"],
                            "group": grp, "stars_before": rb["stars"], "stars_after": after,
                            "delta": delta, "cause": cause, "wind": cls,
                            "wind_kt": None if kt is None else round(kt, 1),
                            "eff_ft": round(rb["eff"], 2), "tp": rb["tp"]})
    per_spot = defaultdict(list)
    for c in changed:
        per_spot[c["spot"]].append(c)
    top = sorted(per_spot.items(),
                 key=lambda kv: (-len(kv[1]), min(c["delta"] for c in kv[1]), kv[0]))[:20]
    return {
        "rule": rule,
        "rows": len(rows),
        "replay": {f"{g} {'reproduced' if ok else 'NOT reproduced'}": n
                   for (g, ok), n in sorted(replay.items())},
        "unreplayable": dict(sorted(unreplayable.items())),
        "compared": sum(sum(c.values()) for c in by_group.values()),
        "changed_spot_hours": len(changed),
        "changed_spots": len(per_spot),
        "ups": sum(c["delta"] > 0 for c in changed),
        "downs": sum(c["delta"] < 0 for c in changed),
        "change_by_cause": dict(sorted(by_cause.items())),
        "change_by_wind": dict(sorted(by_wind.items())),
        "ceiling_hours_by_provenance": dict(sorted(by_prov.items())),
        # Every spot the ceiling moved whose break type or bottom is not from a trusted source,
        # most hours first: the list to check for a reef or point mislabelled as a beach.
        "ceiling_unverified_spots": sorted(unverified.values(),
                                           key=lambda u: (-u["hours"], u["spot"])),
        "delta_by_group": {g: dict(sorted(c.items())) for g, c in sorted(by_group.items())},
        "delta_by_state": {s: dict(sorted(c.items()))
                           for s, c in sorted(by_state.items(), key=lambda kv: str(kv[0]))},
        "top20": [{"spot": s, "changed_hours": len(cs),
                   "example": min(cs, key=lambda c: c["delta"])} for s, cs in top],
    }


def leaderboard(rows, spots, names, rule, n=10, factors=None):
    """The homepage's "Best conditions right now" before and after *rule*.

    The page (frontend/app/page.tsx) takes each spot's soonest row at or after now, sorts by
    stars, highest first, and shows ten; equal stars keep spot id order. Rows here come one
    spot at a time in time order from --since (default now), so a spot's first row is that
    row. A row that does not reproduce keeps its published stars in the AFTER table and is
    marked."""
    first = {}
    for row in rows:
        if row.get("stars") is not None and row["spot_id"] not in first:
            first[row["spot_id"]] = row
    before, after = [], []
    for sid, row in first.items():
        name, state = names.get(sid, (None, None))
        pub = float(row["stars"])
        spot = spots.get(name)
        rb = rescore(row, spot) if spot is not None else {"stars": None}
        replayed = rb["stars"] == pub
        new = rating_after(row, spot, rb, rule, factors)[0] if replayed else pub
        before.append((pub, sid, name, state, row["valid_time"]))
        after.append((new, sid, name, state, row["valid_time"], replayed))
    before.sort(key=lambda r: (-r[0], r[1]))
    after.sort(key=lambda r: (-r[0], r[1]))
    top_b, top_a = before[:n], after[:n]
    a_ids = {r[1] for r in top_a}
    b_ids = {r[1] for r in top_b}
    new_stars = {r[1]: r[0] for r in after}
    return {
        "before": [{"spot": r[2], "state": r[3], "stars": r[0], "valid_time": r[4]} for r in top_b],
        "after": [{"spot": r[2], "state": r[3], "stars": r[0], "replayed": r[5]} for r in top_a],
        "drop_out": [{"spot": r[2], "state": r[3], "stars_before": r[0],
                      "stars_after": new_stars[r[1]]} for r in top_b if r[1] not in a_ids],
        "come_in": [{"spot": r[2], "state": r[3], "stars": r[0]}
                    for r in top_a if r[1] not in b_ids],
    }


def calibration_diff(rows, spots, names, rule, factors):
    """Every reproduced hour whose stars under *rule* differ between the ceiling read at the
    raw face (rating-preview run 30) and at the calibrated face, time order within spot."""
    hours = []
    for row in rows:
        name, state = names.get(row["spot_id"], (None, None))
        spot = spots.get(name)
        if spot is None or row.get("stars") is None:
            continue
        if spot_factor(spot, factors, _slug) is None or not is_sand_beach_break(spot, True):
            continue
        rb = rescore(row, spot)
        if rb["stars"] is None or rb["stars"] != float(row["stars"]):
            continue
        raw = rating_after(row, spot, rb, rule, factors, calibrated=False)[0]
        cal = rating_after(row, spot, rb, rule, factors)[0]
        if raw != cal:
            hours.append({"spot": name, "state": state, "valid_time": row["valid_time"],
                          "eff_ft": round(rb["eff"], 2),
                          "calibrated_ft": round(closeout_face(spot, rb, factors), 2),
                          "factor": spot_factor(spot, factors, _slug),
                          "stars_published": rb["stars"], "stars_raw_ceiling": raw,
                          "stars_calibrated_ceiling": cal})
    return {"rule": rule, "hours": len(hours), "ups": sum(h["stars_calibrated_ceiling"] >
                                                          h["stars_raw_ceiling"] for h in hours),
            "spots": sorted({h["spot"] for h in hours}), "changes": hours}


def wired_rater(row, spot, rb, factor):
    """The stars the WIRED rater gives a replayed row, through the rater's own code: an NWPS
    row through nwps_stars with the spot, the hour's wind and *factor* (as apply_nwps_overrides
    calls it); any other row through composite_stars with interpret.rule_keywords (as rate_spot
    and mop_stars call it). The preview's own rules (rating_after) build the same two rules
    independently, from wind_star_cap, is_sand_beach_break and closeout_face here."""
    wm, tm, cm = _mults(row)
    wd, ws = _num(row.get("wind_dir")), _num(row.get("wind_speed"))
    if row.get("swell_source") in NWPS_KINDS:
        d, per, _, swh = _nwps_inputs(row, spot)
        return nwps_stars(swh, per, d, None, spot.get("orientation_deg"), wm, tm,
                          optimal=spot.get("optimal_swell_dir"),
                          chop=(_num(row.get("chop_ratio")), cm), spot=spot,
                          wind_dir=wd, wind_speed=ws, face_factor=factor)[0]
    return composite_stars(rb["eff"], wm, tm, cm, rb["pq"],
                           **rule_keywords(spot, wd, ws, rb["tp"], rb["eff"], factor))


def rater_check(rows, spots, names, factors):
    """The merge gate: the wired rater against the preview's 'both', hour for hour, on every row
    that reproduces its published stars. Also the wired rater's own change against the
    published stars, by source, which must equal 'both'."""
    same = differ = 0
    examples, by_group = [], defaultdict(Counter)
    for row in rows:
        name, state = names.get(row["spot_id"], (None, None))
        spot = spots.get(name)
        if spot is None or row.get("stars") is None:
            continue
        rb = rescore(row, spot)
        if rb["stars"] is None or rb["stars"] != float(row["stars"]):
            continue
        preview = rating_after(row, spot, rb, "both", factors)[0]
        wired = wired_rater(row, spot, rb, spot_factor(spot, factors or {}, _slug))
        by_group[group_of(row.get("swell_source") or "none")][round(wired - rb["stars"], 1)] += 1
        if wired == preview:
            same += 1
        else:
            differ += 1
            if len(examples) < 20:
                examples.append({"spot": name, "state": state, "valid_time": row["valid_time"],
                                 "published": rb["stars"], "preview": preview, "wired": wired})
    return {"hours": same + differ, "same": same, "different": differ, "examples": examples,
            "wired_delta_by_group": {g: dict(sorted(c.items())) for g, c in sorted(by_group.items())}}


def _print_rater_check(c):
    print(f"\n=== Wired rater vs preview 'both': {c['different']} differences in {c['hours']} "
          f"reproduced hours ({c['same']} equal) ===")
    for e in c["examples"]:
        print(f"  {e['spot']} ({e['state']}) {e['valid_time']}: published {e['published']}, "
              f"preview {e['preview']}, wired {e['wired']}")
    print("wired rater against the published stars, by source (delta: hours):")
    for g, d in c["wired_delta_by_group"].items():
        print(f"  {g}: {d}")


def node_position(spot):
    """Where the spot's NWPS node sits: its coordinates, and its distance (km) and bearing
    (degrees) from the spot, or None when the roster has no node."""
    nlat, nlng = spot.get("nwps_node_lat"), spot.get("nwps_node_lng")
    if nlat is None or nlng is None:
        return None
    return {"lat": nlat, "lng": nlng,
            "km": _haversine_km(spot["lat"], spot["lng"], nlat, nlng),
            "bearing": _bearing(spot["lat"], spot["lng"], nlat, nlng)}


def explain(row, spot, rules=tuple(RATING_RULES), factors=None):
    """Every factor of one published row, how the composite combines them, and the stars
    under each proposed rule. Pure: reads the row and the spot only."""
    rb = rescore(row, spot)
    wm, tm, cm = _mults(row)
    wd, ws, off = _num(row.get("wind_dir")), _num(row.get("wind_speed")), spot.get("offshore_wind_deg")
    out = {
        "valid_time": row["valid_time"], "source": row.get("swell_source"),
        "published_stars": _num(row.get("stars")),
        "face_ft": _num(row.get("face_ft")), "face_ft_raw": _num(row.get("face_ft_raw")),
        "face_lo_ft": _num(row.get("face_lo_ft")), "face_hi_ft": _num(row.get("face_hi_ft")),
        "dir_gain": _num(row.get("dir_gain")), "effective_size_ft": _num(row.get("effective_size_ft")),
        "swell_dp": _num(row.get("swell_dp")), "swell_tp": _num(row.get("swell_tp")),
        "wind_dir": wd, "wind_ms": ws,
        "wind_kt": None if ws is None else ws / KT_TO_MS,
        "wind_mph": None if ws is None else ws * 2.236936,
        "offshore_wind_deg": off,
        "wind_angle_off_offshore": None if wd is None or off is None else _angle_off(wd, off),
        "wind_class": None if wd is None or off is None else wind_class(wd, off),
        "wind_mult": wm, "tide_norm": _num(row.get("tide_norm")), "tide_mult": tm,
        "chop_ratio": _num(row.get("chop_ratio")), "chop_mult": cm,
        "period_quality": _num(row.get("period_quality")),
        "break_type": spot.get("break_type"), "bottom": spot.get("bottom"),
        "orientation_deg": spot.get("orientation_deg"),
        "optimal_swell_dir": spot.get("optimal_swell_dir"),
        "swell_window_arcs": spot.get("swell_window_arcs"),
        "nwps_node": node_position(spot) if spot.get("lat") is not None else None,
    }
    if rb["stars"] is None:
        out["replay"] = rb["why"]
        return out
    e = COMPOSITE_FACTOR_EXPONENTS
    terms = {"size_score": size_score(rb["eff"]), "wind": wm ** e["wind_mult"],
             "tide": tm ** e["tide_mult"], "chop": cm ** e["chop_mult"],
             "period": rb["pq"] ** e["period_quality"]}
    raw = 1.0
    for v in terms.values():
        raw *= v
    out.update(replay_eff_ft=rb["eff"], replay_tp=rb["tp"], replay_pq=rb["pq"],
               terms=terms, raw=raw, replay_stars=rb["stars"],
               reproduced=rb["stars"] == out["published_stars"],
               beach_size_score=beach_size_score(rb["eff"]),
               face_factor=spot_factor(spot, factors or {}, _slug),
               closeout_face_ft=closeout_face(spot, rb, factors),
               closeout_factor=closeout_factor(closeout_face(spot, rb, factors)),
               wind_cap=wind_star_cap(wd, ws, off, rb["tp"]),
               proposed={r: rating_after(row, spot, rb, r, factors) for r in rules})
    return out


def _f(v, fmt="{:.2f}"):
    return "—" if v is None else fmt.format(v)


def _print_explain(name, x):
    print(f"\n--- {name} @ {x['valid_time']} [{x['source']}]  published {x['published_stars']} stars")
    print(f"  face {_f(x['face_ft'])} ft (raw {_f(x['face_ft_raw'])}, band {_f(x['face_lo_ft'])}-"
          f"{_f(x['face_hi_ft'])}), dir_gain {_f(x['dir_gain'], '{:.3f}')}, effective "
          f"{_f(x['effective_size_ft'])} ft; swell {_f(x['swell_dp'], '{:.0f}')}° "
          f"{_f(x['swell_tp'], '{:.1f}')} s")
    print(f"  wind {_f(x['wind_dir'], '{:.0f}')}° {_f(x['wind_ms'], '{:.1f}')} m/s = "
          f"{_f(x['wind_kt'], '{:.1f}')} kt = {_f(x['wind_mph'], '{:.1f}')} mph; offshore "
          f"{_f(x['offshore_wind_deg'], '{:.0f}')}°, {_f(x['wind_angle_off_offshore'], '{:.0f}')}° "
          f"off it: {x['wind_class']}; wind_mult {_f(x['wind_mult'], '{:.3f}')}")
    print(f"  tide_norm {_f(x['tide_norm'], '{:.3f}')} tide_mult {_f(x['tide_mult'], '{:.3f}')}; "
          f"chop_ratio {_f(x['chop_ratio'], '{:.3f}')} chop_mult {_f(x['chop_mult'], '{:.3f}')}; "
          f"period_quality {_f(x['period_quality'], '{:.3f}')}; {x['break_type']} / {x['bottom']}")
    n = x.get("nwps_node")
    print(f"  faces {_f(x['orientation_deg'], '{:.0f}')}°, optimal swell "
          f"{_f(x['optimal_swell_dir'], '{:.0f}')}°, window "
          + ", ".join(f"{a['min']}-{a['max']}" for a in x.get("swell_window_arcs") or [])
          + ("; NWPS node " + (f"{n['lat']:.5f}, {n['lng']:.5f}: {n['km']:.2f} km at "
                               f"{n['bearing']:.0f}° from the spot" if n else "none")))
    if "terms" not in x:
        print(f"  not replayable: {x['replay']}")
        return
    t = x["terms"]
    print(f"  composite = size_score({x['replay_eff_ft']:.2f} ft) {t['size_score']:.3f}"
          f" x wind^0.35 {t['wind']:.3f} x tide^0.15 {t['tide']:.3f} x chop^0.25 {t['chop']:.3f}"
          f" x period^0.25 {t['period']:.3f} (pq {x['replay_pq']:.3f} at {x['replay_tp']:.1f} s)"
          f" = {x['raw']:.3f} -> {x['replay_stars']} stars "
          f"({'reproduces' if x['reproduced'] else 'DOES NOT reproduce'} the published value)")
    print(f"  ceiling reads {x['closeout_face_ft']:.2f} ft (MOP factor {x['face_factor']}), "
          f"keeps {x['closeout_factor']:.3f} of the size credit")
    print(f"  proposed: wind cap {x['wind_cap']}, beach size score {x['beach_size_score']:.3f}; "
          + "; ".join(f"{r} {s} ({c or 'no change'})" for r, (s, c) in x["proposed"].items()))


def _print_calibration_diff(d):
    print(f"ceiling at the calibrated face against the raw face ({d['rule']}): {d['hours']} hours "
          f"differ, {d['ups']} up, at {len(d['spots'])} spots")
    for h in d["changes"]:
        print(f"  {h['spot']} ({h['state']}) {h['valid_time']}: effective {h['eff_ft']} ft / "
              f"factor {h['factor']} = {h['calibrated_ft']} ft; published {h['stars_published']}, "
              f"raw ceiling {h['stars_raw_ceiling']} -> calibrated {h['stars_calibrated_ceiling']}")


def _print_rating(rep, lb, since):
    print(f"\n=== rule: {rep['rule']} — published window from {since}: {rep['rows']} rows ===")
    for k, v in rep["replay"].items():
        print(f"  {k}: {v}")
    for k, v in rep["unreplayable"].items():
        print(f"  not replayable — {k}: {v}")
    print(f"compared (reproduced rows): {rep['compared']}")
    print(f"changed spot-hours: {rep['changed_spot_hours']}   changed spots: {rep['changed_spots']}"
          f"   ups: {rep['ups']}   downs: {rep['downs']}")
    print(f"change by cause (hours): {rep['change_by_cause']}")
    print(f"change by wind (hours): {rep['change_by_wind']}")
    print("hours the beach ceiling moved, by the source of break type and bottom: "
          f"{rep['ceiling_hours_by_provenance']}")
    unv = rep["ceiling_unverified_spots"]
    print(f"spots the ceiling moved whose break type or bottom is unverified: {len(unv)}")
    for u in unv:
        print(f"  {u['spot']} ({u['state']}): {u['break_type']} [{u['break_type_source']}] / "
              f"{u['bottom']} [{u['bottom_source']}]; {u['hours']} h, largest drop "
              f"{u['largest_drop']}, largest effective face {u['largest_eff_ft']} ft")
    print("star change by source (delta: hours):")
    for g, c in rep["delta_by_group"].items():
        print(f"  {g}: {c}")
    print("star change by state (delta: hours):")
    for s, c in rep["delta_by_state"].items():
        print(f"  {s}: {c}")
    print("20 most-changed spots (changed hours; largest drop):")
    for t in rep["top20"]:
        e = t["example"]
        print(f"  {t['spot']} ({e['state']}): {t['changed_hours']} h; {e['valid_time']} [{e['group']}] "
              f"{e['stars_before']}->{e['stars_after']} ({e['cause']}; wind {e['wind']} "
              f"{e['wind_kt']} kt; eff {e['eff_ft']} ft; {e['tp']} s)")
    print("homepage 'Best conditions right now', before:")
    for i, r in enumerate(lb["before"], 1):
        print(f"  {i}. {r['spot']} ({r['state']}) {r['stars']}  [{r['valid_time']}]")
    print("after:")
    for i, r in enumerate(lb["after"], 1):
        print(f"  {i}. {r['spot']} ({r['state']}) {r['stars']}"
              f"{'' if r['replayed'] else '  (not replayed: published stars)'}")
    print(f"drop out: {[(d['spot'], d['stars_before'], d['stars_after']) for d in lb['drop_out']]}")
    print(f"come in: {[(d['spot'], d['stars']) for d in lb['come_in']]}")


# --------------------------------------------------------------------------- #
# --nwps-shelter                                                               #
# --------------------------------------------------------------------------- #
SHELTER_SHARE_MIN = 0.8        # the dominant partition's share of the swell energy
SHELTER_REAL_ENERGY = 0.5      # R_rel at or above this: NWPS delivers the energy the window cut
SWELL_PARTITIONS = ("swell_1", "swell_2", "swell_3")
OUTSIDE_RUNGS = ("0.40", "0.15", "blocked")


def period_band(tp):
    return "<=9 s" if tp <= 9.0 else ("9-13 s" if tp <= 13.0 else ">13 s")


def node_band(distance_m):
    """The NWPS node's distance from the spot, in the bands the report splits by."""
    if distance_m is None:
        return "unknown"
    km = float(distance_m) / 1000.0
    return "<1 km" if km < 1.0 else ("1-2.5 km" if km <= 2.5 else ">2.5 km")


def window_rung(dp, arcs):
    """Where *dp* sits against the window, in directional_gain's own terms."""
    if not arcs:
        return "no window"
    if in_any_arc(dp, arcs):
        return "inside"
    off = _min_offset_from_arcs(dp, arcs)
    return "0.40" if off < 45.0 else ("0.15" if off < 90.0 else "blocked")


def _swell_energy(row):
    """Σ hs² over the swell partitions combine_ww3_partitions would read."""
    total = 0.0
    for p in SWELL_PARTITIONS:
        hs, tp, dp = row.get(f"{p}_hs"), row.get(f"{p}_tp"), row.get(f"{p}_dp")
        if hs is None or tp is None or dp is None or float(hs) <= 0 or float(tp) <= 0:
            continue
        total += float(hs) ** 2
    return total


def shelter_hour(row, spot):
    """One NWPS row, measured: {"kind"} for a dirpw row; for an nwps_height_ww3_dir row also
    rung, band, node, share, R, dirpw_in_window and whether the dominant partition is the
    direction the row was published on. None for a row that is not NWPS."""
    kind = row.get("swell_source")
    if kind not in NWPS_KINDS:
        return None
    rec = {"kind": kind}
    if kind != "nwps_height_ww3_dir":
        return rec
    arcs = spot.get("swell_window_arcs") or []
    parts = _partitions(row)
    comb = combine_ww3_partitions(parts, arcs, spot.get("optimal_swell_dir"),
                                  spot.get("orientation_deg")) if parts else None
    if comb is None:
        rec["status"] = "no partition keeps any gain"
        return rec
    hs = float(row[f"{comb['dominant_partition']}_hs"])
    stored = _num(row.get("swell_dp"))
    node_hs = _num(row.get("swell_hs"))
    dirpw = _num(row.get("dp"))
    rec.update(
        rung=window_rung(comb["dp"], arcs),
        band=period_band(comb["tp"]),
        node=node_band(spot.get("nwps_node_distance_m")),
        share=hs * hs / _swell_energy(row),
        R=node_hs / hs if node_hs is not None else None,
        dirpw_in_window=None if dirpw is None or not arcs else in_any_arc(dirpw, arcs),
        matches_published=stored is not None and abs(stored - comb["dp"]) < 0.01,
    )
    return rec


def _shelter_table(hours, key):
    """{value: {hours, with_reference, median_R_rel, real_energy, real_share}} over *hours*."""
    groups = defaultdict(list)
    for h in hours:
        groups[h[key] if key else "all"].append(h)
    out = {}
    for value, hs in sorted(groups.items(), key=lambda kv: str(kv[0])):
        rel = [h["R_rel"] for h in hs if h["R_rel"] is not None]
        real = sum(r >= SHELTER_REAL_ENERGY for r in rel)
        out[value] = {"hours": len(hs), "with_reference": len(rel),
                      "median_R_rel": round(median(rel), 3) if rel else None,
                      "real_energy": real,
                      "real_share": round(real / len(rel), 3) if rel else None}
    return out


def shelter_report(rows, spots, names):
    """The --nwps-shelter measurement over the published rows (see the module docstring)."""
    by_source = Counter(row.get("swell_source") or "none" for row in rows)
    recs, no_spot = [], 0
    for row in rows:
        if row.get("swell_source") not in NWPS_KINDS:
            continue
        name, state = names.get(row["spot_id"], (None, None))
        spot = spots.get(name)
        if spot is None:
            no_spot += 1
            continue
        rec = shelter_hour(row, spot)
        rec.update(spot=name, state=state)
        recs.append(rec)
    ww3_dir = [r for r in recs if r["kind"] == "nwps_height_ww3_dir"]
    placed = [r for r in ww3_dir if "rung" in r]
    attributable = [r for r in placed if r["share"] >= SHELTER_SHARE_MIN and r["R"] is not None]
    inside = defaultdict(list)
    for r in attributable:
        if r["rung"] == "inside":
            inside[(r["spot"], r["band"])].append(r["R"])
    reference = {k: median(v) for k, v in inside.items()}
    outside = []
    for r in attributable:
        if r["rung"] not in OUTSIDE_RUNGS:
            continue
        ref = reference.get((r["spot"], r["band"]))
        outside.append(dict(r, R_rel=r["R"] / ref if ref else None))
    placed_outside = [r for r in placed if r["rung"] in OUTSIDE_RUNGS]
    return {
        "rows": len(rows),
        "by_swell_source": dict(sorted(by_source.items())),
        "nwps_rows_without_a_spot": no_spot,
        "ww3_dir_rows": len(ww3_dir),
        "no_partition_keeps_gain": len(ww3_dir) - len(placed),
        "dominant_matches_published_direction": dict(sorted(
            Counter(r["matches_published"] for r in placed).items())),
        "by_rung": dict(sorted(Counter(r["rung"] for r in placed).items())),
        "outside_not_attributable": {
            "share below %.2f" % SHELTER_SHARE_MIN:
                sum(r["share"] < SHELTER_SHARE_MIN for r in placed_outside),
            "no node swell height":
                sum(r["share"] >= SHELTER_SHARE_MIN and r["R"] is None for r in placed_outside),
        },
        "inside_reference": {"hours": sum(len(v) for v in inside.values()),
                             "spot_bands": len(reference),
                             "median_R": round(median([r for v in inside.values() for r in v]), 3)
                             if inside else None},
        "outside": {"all": _shelter_table(outside, None)["all"] if outside else
                    {"hours": 0, "with_reference": 0, "median_R_rel": None, "real_energy": 0,
                     "real_share": None},
                    "by_period_band": _shelter_table(outside, "band"),
                    "by_state": _shelter_table(outside, "state"),
                    "by_rung": _shelter_table(outside, "rung"),
                    "by_node_distance": _shelter_table(outside, "node")},
        "dirpw_inside_window_on_outside_hours": {
            str(k): n for k, n in sorted(Counter(r["dirpw_in_window"] for r in placed_outside).items(),
                                         key=lambda kv: str(kv[0]))},
    }


def ungated(spots):
    """The spots with no window: directional_gain falls to cos² about the optimal, floor 0.25."""
    return {n: dict(s, swell_window_arcs=[]) for n, s in spots.items()}


def _nwps_rows(rows):
    return [r for r in rows if r.get("swell_source") in NWPS_KINDS]


def no_gate_compare(rows, spots, names):
    """A: every NWPS row replayed with the spot's window removed, against the published stars.
    The window goes from the partition choice too, so a blocked partition can win it."""
    return compare(_nwps_rows(rows), spots, ungated(spots), names,
                   rescore_before=legacy_rescore)


def selected_alignment_compare(rows, spots, names, use_dirpw=False):
    """B (use_dirpw False): the window still picks WHICH partition's direction is used, but
    the NWPS height takes no window multiplier, only max(0.25, cos²(Δ/2)) on that direction.
    C (use_dirpw True): as B, with the row's NWPS dirpw as the direction when it has one."""
    return compare(_nwps_rows(rows), spots, spots, names,
                   rescore_after=lambda row, spot: _nwps_rescore(row, spot, [], use_dirpw),
                   rescore_before=legacy_rescore)


def rater_compare(rows, spots, names):
    """R: every NWPS row replayed through the rater itself (rescore -> nwps_stars) against
    the published stars, from the window-rung baseline the window was published under."""
    return compare(_nwps_rows(rows), spots, spots, names, rescore_before=legacy_rescore)


def rater_against_b(rows, spots, names):
    """The rater (nwps_stars) and B, hour for hour, on every NWPS row both can replay:
    {hours, equal, different, not_replayable, examples (up to 20 differences)}."""
    equal, examples, skipped, hours = 0, [], 0, 0
    for row in _nwps_rows(rows):
        name, state = names.get(row["spot_id"], (None, None))
        spot = spots.get(name)
        r = _rater_nwps(row, spot) if spot else {"stars": None}
        b = _nwps_rescore(row, spot, []) if spot else {"stars": None}
        if r["stars"] is None or b["stars"] is None:
            skipped += 1
            continue
        hours += 1
        if (r["stars"], r["gain"], r["dir"]) == (b["stars"], b["gain"], b["dir"]):
            equal += 1
        elif len(examples) < 20:
            examples.append({"spot": name, "state": state, "valid_time": row.get("valid_time"),
                             "rater": r, "b": b})
    return {"hours": hours, "equal": equal, "different": hours - equal,
            "not_replayable": skipped, "examples": examples}


def _print_shelter(rep):
    print(f"published window: {rep['rows']} rows, by swell_source: {rep['by_swell_source']}")
    if rep["nwps_rows_without_a_spot"]:
        print(f"NWPS rows whose spot is not in the baseline: {rep['nwps_rows_without_a_spot']}")
    print(f"nwps_height_ww3_dir rows: {rep['ww3_dir_rows']}  (no partition keeps any gain: "
          f"{rep['no_partition_keeps_gain']})")
    print(f"dominant partition is the published direction: "
          f"{rep['dominant_matches_published_direction']}")
    print(f"dominant partition, by rung: {rep['by_rung']}")
    print(f"outside hours not attributable: {rep['outside_not_attributable']}")
    ref = rep["inside_reference"]
    print(f"inside reference: {ref['hours']} attributable hours over {ref['spot_bands']} "
          f"spot x period-band pairs, median R {ref['median_R']}")
    print(f"outside hours (attributable; R_rel >= {SHELTER_REAL_ENERGY} counts as real energy):")
    print(f"  all: {rep['outside']['all']}")
    for title, key in (("period band", "by_period_band"), ("state", "by_state"),
                       ("rung", "by_rung"), ("node distance", "by_node_distance")):
        print(f"  by {title}:")
        for value, row in rep["outside"][key].items():
            print(f"    {value}: {row}")
    print(f"NWPS dirpw inside the window on outside hours: "
          f"{rep['dirpw_inside_window_on_outside_hours']}")


def tide_mode(spec):
    """'keep', 'neutral' or 'damp:K' -> (label, f) where f maps a stored tide_mult to the new one.
    ('rated' is not a map of the stored value; main builds it with rated_row_after.)"""
    if spec == "keep":
        return spec, lambda tm: tm
    if spec == "neutral":
        return spec, lambda tm: 1.0
    if spec.startswith("damp:"):
        k = float(spec.split(":", 1)[1])
        if not 0.0 < k < 1.0:
            raise ValueError(f"{spec}: K must be strictly between 0 and 1")
        return spec, lambda tm: 1.0 - k * (1.0 - tm)
    raise ValueError(f"unknown tide mode {spec!r}: keep, neutral, damp:K or rated")


def unverified(tide):
    """Is a spot's tide preference unverified — its source not one the rating trusts
    (config.TIDE_RATED_SOURCES)? *tide* is the spot's {"tide_preference",
    "tide_preference_source"} from the live table."""
    return (tide or {}).get("tide_preference_source") not in TIDE_RATED_SOURCES


def tide_row_after(tides, f):
    """The AFTER row for the tide preview: *f* applied to the stored tide_mult on a spot whose
    preference is unverified; a verified spot's row, and a row with no tide_mult, unchanged."""
    def after(row):
        tm = row.get("tide_mult")
        if tm is None or not unverified(tides.get(row["spot_id"])):
            return row
        return dict(row, tide_mult=f(float(tm)))
    return after


def rated_row_after(roster, names):
    """The AFTER row for the 'rated' mode: tide_mult recomputed by the rater's own rule from the
    row's tide_norm and the spot as *roster* (name -> spot) holds it. A row whose spot is not in
    the roster is unchanged."""
    def after(row):
        name = names.get(row["spot_id"], (None, None))[0]
        spot = roster.get(name)
        if spot is None:
            return row
        return dict(row, tide_mult=tide_multiplier(_num(row.get("tide_norm")),
                                                   rated_tide_preference(spot)))
    return after


def tide_census(rows, tides):
    """What the live table holds for the tide, and what the window's stored tide_mult did."""
    spots = Counter((t.get("tide_preference"), t.get("tide_preference_source"))
                    for t in tides.values())
    by_pref, repro, below = defaultdict(Counter), Counter(), Counter()
    for row in rows:
        pref = (tides.get(row["spot_id"]) or {}).get("tide_preference")
        tm = row.get("tide_mult")
        by_pref[str(pref)]["None" if tm is None else f"{float(tm):g}"] += 1
        if tm is None:
            continue
        if float(tm) < 1.0:
            below[str(pref)] += 1
        # The rater's own rule (interpret.rated_tide_preference): only a trusted source's
        # preference is applied, so since #251 an unattributed one rates 1.0.
        expected = round(tide_multiplier(_num(row.get("tide_norm")),
                                         rated_tide_preference(tides.get(row["spot_id"]) or {})), 3)
        repro["reproduced" if expected == round(float(tm), 3) else "NOT reproduced"] += 1
    return {
        "spots": {f"{p} / {s}": n for (p, s), n in sorted(spots.items(), key=str)},
        "rows": len(rows),
        "tide_mult_by_preference": {p: dict(sorted(c.items())) for p, c in sorted(by_pref.items())},
        "hours_below_1_by_preference": dict(sorted(below.items())),
        "tide_mult_from_tide_norm_and_preference": dict(sorted(repro.items())),
    }


def _by_name(path):
    return {s["name"]: s for s in json.load(open(path)) if s.get("name")}


def _fetch(client, since):
    """Every spot, then the published window ONE SPOT AT A TIME.

    One query per spot, filtered on spot_id, source and valid_time — the forecasts table's
    UNIQUE(spot_id, valid_time, source) key — so each read is a short index range. The first
    version paged the whole window with ORDER BY spot_id, valid_time and a growing OFFSET;
    Supabase cancelled it with a statement timeout (57014) partway through, in sw1-raycast
    run 11."""
    names, frm = {}, 0
    while True:
        rows = (client.table("spots").select("id, name, state").order("id")
                .range(frm, frm + 999).execute().data or [])
        names.update({r["id"]: (r["name"], r.get("state")) for r in rows})
        if len(rows) < 1000:
            break
        frm += 1000
    out = []
    for sid in sorted(names):
        frm = 0
        while True:
            rows = (client.table("forecasts").select(FORECAST_COLS)
                    .eq("spot_id", sid).eq("source", "nwps").gte("valid_time", since)
                    .order("valid_time")
                    .range(frm, frm + 999).execute().data or [])
            out.extend(rows)
            if len(rows) < 1000:
                break
            frm += 1000
    return names, out


def _fetch_tides(client):
    """{spot_id: {tide_preference, tide_preference_source}} from the live spots table, SELECT
    only. Before migration 018 there is no source column: then every source reads None (so
    every preference is unverified) and the census says so."""
    out, frm, cols = {}, 0, "id, tide_preference, tide_preference_source"
    while True:
        try:
            rows = (client.table("spots").select(cols).order("id")
                    .range(frm, frm + 999).execute().data or [])
        except Exception as e:  # noqa: BLE001 — a missing column is reported, not fatal
            if "tide_preference_source" not in cols:
                raise
            print(f"spots.tide_preference_source could not be read ({e}); reading the "
                  "preference alone, every source as None")
            out, frm, cols = {}, 0, "id, tide_preference"
            continue
        out.update({r["id"]: {"tide_preference": r.get("tide_preference"),
                              "tide_preference_source": r.get("tide_preference_source")}
                    for r in rows})
        if len(rows) < 1000:
            return out
        frm += 1000


def _print_census(c):
    print("live spots, tide_preference / tide_preference_source:")
    for k, v in c["spots"].items():
        print(f"  {k}: {v}")
    print(f"published window: {c['rows']} rows")
    print("stored tide_mult by the spot's preference (value: hours):")
    for p, d in c["tide_mult_by_preference"].items():
        print(f"  {p}: {d}")
    print(f"hours with tide_mult < 1 by preference: {c['hours_below_1_by_preference']}")
    print("stored tide_mult recomputed from tide_norm and the live preference: "
          f"{c['tide_mult_from_tide_norm_and_preference']}")


def _print(rep, since):
    print(f"published window from {since}: {rep['rows']} rows (source='nwps')")
    print("replay with BASELINE against the published stars:")
    for k, v in rep["replay"].items():
        print(f"  {k}: {v}")
    for k, v in rep["unreplayable"].items():
        print(f"  not replayable — {k}: {v}")
    print(f"compared (reproduced rows): {rep['compared']}")
    print(f"changed spot-hours: {rep['changed_spot_hours']}   changed spots: {rep['changed_spots']}")
    print(f"ups: {rep['ups']}   downs: {rep['downs']}")
    print("change by cause (hours):")
    for k, v in rep["change_by_cause"].items():
        print(f"  {k}: {v}")
    print("star change by source (delta: hours):")
    for g, c in rep["delta_by_group"].items():
        print(f"  {g}: {c}")
    print("star change by state (delta: hours):")
    for s, c in rep["delta_by_state"].items():
        print(f"  {s}: {c}")
    print("20 most-changed spots (changed hours; example hour: time, dir before->after, "
          "gain before->after, stars before->after):")
    for t in rep["top20"]:
        e = t["example"]
        tide = (f" tide_norm {e['tide_norm']} tide_mult {e['tide_mult_before']}->"
                f"{e['tide_mult_after']:.3f}" if "tide_mult_before" in e else "")
        print(f"  {t['spot']} ({e['state']}): {t['changed_hours']} h; {e['valid_time']} "
              f"[{e['group']}; {e['cause']}] dir {e['dir_before']}->{e['dir_after']} "
              f"gain {e['gain_before']}->{e['gain_after']}{tide} "
              f"stars {e['stars_before']}->{e['stars_after']}")
    big = rep["spots_moving_over_1_5"]
    print(f"spots with an hour moving by more than {BIG_MOVE} stars: {len(big)}")
    for b in big:
        print(f"  {b['spot']} ({b['state']}): {b['hours_over']} h, largest up {b['largest_up']}, "
              f"largest down {b['largest_down']}")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--arcs", default=None,
                   help="spots file whose swell windows to preview (default: --baseline)")
    p.add_argument("--baseline", default="pipeline/spots_enriched.json",
                   help="spots file the published window was rated with (default: the roster)")
    p.add_argument("--tide", default=None,
                   help="comma-separated tide modes to preview: keep, neutral, damp:K, rated")
    p.add_argument("--tide-roster", default=None,
                   help="spots file whose tide preference and source the 'rated' mode reads "
                        "(default: --baseline)")
    p.add_argument("--nwps-shelter", action="store_true",
                   help="measure the window's cut on NWPS rows, and replay them with no window")
    p.add_argument("--rating", default=None,
                   help="comma-separated proposed rating rules to preview: "
                        + ", ".join(RATING_RULES))
    p.add_argument("--explain", default="",
                   help="(with --rating) comma-separated spot slugs: print every rating factor "
                        "of each one's soonest published hour")
    p.add_argument("--since", default=None, help="ISO start of the window (default: now)")
    p.add_argument("--out", default=None, help="write the report as JSON here")
    args = p.parse_args(argv)
    if args.nwps_shelter and (args.arcs is not None or args.tide is not None):
        p.error("--nwps-shelter runs on its own, with --baseline")
    rules = [r.strip() for r in args.rating.split(",") if r.strip()] if args.rating else []
    if rules and (args.arcs is not None or args.tide is not None or args.nwps_shelter):
        p.error("--rating runs on its own, with --baseline")
    for r in rules:
        if r not in RATING_RULES:
            p.error(f"unknown rating rule {r!r}: {', '.join(RATING_RULES)}")
    if args.arcs is None and args.tide is None and not args.nwps_shelter and not rules:
        p.error("give --arcs (a swell-window preview), --tide (a tide preview), or both; "
                "or --nwps-shelter, or --rating")
    specs = [m.strip() for m in args.tide.split(",")] if args.tide else []
    modes = [(m, None) if m == "rated" else tide_mode(m) for m in specs]
    from supabase import create_client
    client = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    since = args.since or datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    names, rows = _fetch(client, since)
    before = _by_name(args.baseline)
    after = _by_name(args.arcs) if args.arcs else before
    out = {"since": since}
    if rules:
        factors = load_face_factors()
        out["explain"] = {}
        slugs = [x.strip() for x in args.explain.split(",") if x.strip()]
        by_slug = {_slug(n): sid for sid, (n, _) in names.items()}
        for sl in slugs:
            sid = by_slug.get(sl)
            row = next((r for r in rows if r["spot_id"] == sid), None) if sid else None
            name = names[sid][0] if sid else sl
            if row is None or before.get(name) is None:
                print(f"\n--- {sl}: no published row from {since} or not in the spots file")
                continue
            out["explain"][sl] = explain(row, before[name], rules, factors)
            _print_explain(name, out["explain"][sl])
        out["rater_check"] = rater_check(rows, before, names, factors)
        _print_rater_check(out["rater_check"])
        out["rating"] = {}
        for r in rules:
            rep = rating_compare(rows, before, names, r, factors)
            lb = leaderboard(rows, before, names, r, factors=factors)
            _print_rating(rep, lb, since)
            out["rating"][r] = dict(rep, leaderboard=lb)
            if RATING_RULES[r][1]:
                d = calibration_diff(rows, before, names, r, factors)
                out["rating"][r]["calibration_diff"] = d
                _print_calibration_diff(d)
    elif args.nwps_shelter:
        out["nwps_shelter"] = shelter_report(rows, before, names)
        _print_shelter(out["nwps_shelter"])
        print("\n=== A: NWPS rows with no window gate, in the partition choice too (cos² about the "
              "optimal, floor 0.25) ===")
        out["no_gate"] = no_gate_compare(rows, before, names)
        _print(out["no_gate"], since)
        print("\n=== B: the window picks the partition; the NWPS height takes the alignment term "
              "only, on that partition's direction ===")
        out["variant_b"] = selected_alignment_compare(rows, before, names)
        _print(out["variant_b"], since)
        print("\n=== C: as B, with NWPS dirpw as the direction where the row has one ===")
        out["variant_c"] = selected_alignment_compare(rows, before, names, use_dirpw=True)
        _print(out["variant_c"], since)
        print("\n=== R: the rater on this checkout (nwps_stars), replayed ===")
        out["rater"] = rater_compare(rows, before, names)
        _print(out["rater"], since)
        out["rater_against_b"] = rater_against_b(rows, before, names)
        m = out["rater_against_b"]
        print(f"the rater against B, hour for hour: equal on {m['equal']} of {m['hours']} hours, "
              f"different on {m['different']} (not replayable: {m['not_replayable']})")
        for e in m["examples"]:
            print(f"  {e['spot']} ({e['state']}) {e['valid_time']}: rater {e['rater']} B {e['b']}")
    elif not modes:
        rep = compare(rows, before, after, names)
        _print(rep, since)
        out.update(rep)
    else:
        tides = _fetch_tides(client)
        out["census"] = tide_census(rows, tides)
        _print_census(out["census"])
        out["tide"] = {}
        roster = _by_name(args.tide_roster) if args.tide_roster else before
        for label, f in modes:
            if label == "rated":
                print(f"\n=== tide: rated — the rater's rule on the sources in "
                      f"{args.tide_roster or args.baseline} "
                      f"({sum(rated_tide_preference(s) is not None for s in roster.values())} "
                      f"spots whose preference the rating applies) ===")
                row_after = rated_row_after(roster, names)
            else:
                print(f"\n=== tide: {label} on every unverified preference "
                      f"({sum(unverified(t) and t.get('tide_preference') is not None for t in tides.values())} "
                      f"spots with a preference and no source the rating trusts) ===")
                row_after = tide_row_after(tides, f)
            rep = compare(rows, before, after, names, row_after=row_after)
            _print(rep, since)
            out["tide"][label] = rep
    if args.out:
        with open(args.out, "w") as f:
            json.dump(out, f, indent=2, default=str)
    return 0


if __name__ == "__main__":
    sys.exit(main())
