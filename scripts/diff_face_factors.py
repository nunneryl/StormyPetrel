#!/usr/bin/env python3
"""Compare two pipeline/data/spot_face_factors.json files.

    python3 scripts/diff_face_factors.py OLD.json NEW.json
    python3 scripts/diff_face_factors.py OLD.json NEW.json --json
    python3 scripts/diff_face_factors.py --selftest

THE QUESTION THIS EXISTS TO ANSWER (P0-1): is a 14-day per-spot face factor stable?
The reading rules were fixed before the measurement, which is what makes the answer
falsifiable:

    stable                      -> the question closes
    churn in BOTH directions    -> hysteresis is worth building
    systematic ONE-WAY drift    -> a 14-day factor does not describe the next 14 days,
                                   which is more fundamental than hysteresis

THE ENTIRE POINT OF THE SEPARATION BELOW IS THAT THOSE RULES ONLY WORK IF THREE KINDS OF
CHANGE ARE NEVER SUMMED. The 2026-09-01 baseline and a 2026-09-19 regeneration differ for
three unrelated reasons, and a single "N spots changed" number mixes them into something
that cannot be read at all:

  1. FACTOR DRIFT — a spot kept in BOTH files. The ocean moved, or the measurement is
     noisy. THIS IS THE ONLY POPULATION THAT ANSWERS P0-1.

  2. MEMBERSHIP CHANGE — a spot entering or leaving the file because the GATES moved, not
     because anything about the spot did. Between 09-01 and 09-19 the shore-normal gate
     went 35 -> 90 degrees, an is_valid_surf_spot filter landed, and a 2200 m separation
     gate landed. Those spots have no before-and-after to compare and are excluded from
     every drift statistic. Counting them as churn would manufacture instability out of a
     decision we made ourselves.

  3. HOLD-OUT CHURN — a spot crossing between `factors` and `held_out` because its
     measured p75/p25 crossed FACE_FACTOR_MAX_IQR_RATIO. This is a QUALITY FILTER
     RECOMPUTED EVERY RUN, not a fixed group, and the margins are thin: in the 09-01 file
     ten-mile-beach sits 2% over the 1.7 threshold and mackerricher 5% over, while
     jug-handle (1.638) and westport (1.606) sit within 0.10 of it from the other side.
     A boundary wobble must be legible AS a boundary wobble, so crossings are reported
     with both spread values and the margin rather than as a bare in/out flip.

WHAT THIS TOOL DELIBERATELY DOES NOT DO: pick a threshold. "Stable" has no numeric
definition in the brief, and inventing one here would quietly answer P0-1 on the tool
author's authority rather than the measurement's. The statistics the three rules key on —
the median ratio, the up/down balance, and the spread — are reported prominently, and the
verdict is the reader's.

READ-ONLY. Neither input is ever opened for writing. The 09-01 file is the comparison
baseline and losing it makes the comparison impossible to repeat.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)
sys.path.insert(0, HERE)

# The NAMED hold-outs, taken from the generator's own dict rather than from a copy of the
# list. They are hardcoded by slug and cannot churn; the spread-excluded ones are
# recomputed per run and can. Reporting them together would inflate the apparent stability
# of the filter by diluting the churn with three spots that are structurally incapable of
# it.
from build_face_factors import HELD_OUT as NAMED_HELD_OUT      # noqa: E402
from build_face_factors import GATE_CONSTANT_KEYS              # noqa: E402

# Kept factors are written as round(median, 4); held-out factors are the raw float
# (build_face_factors.py, the keep branch vs the else branch). A spot crossing between the
# two maps therefore changes PRECISION as well as value, and 0.4967949362146973 -> 0.4968
# is a formatting artifact reading as a 0.001% move. Every factor is reduced to this many
# decimals before any comparison, so a crossing with an unchanged median compares equal.
FACTOR_DECIMALS = 4

# build_face_factors writes the spread into the exclusion reason as `{iqr:.2f}`, and that
# string is the ONLY record of it: a held_out record carries no p25/p75 (see the keep
# branch, which alone copies them). So a crossing's before-and-after spread is exact on
# the kept side and 2-decimal on the held side, and the report says which is which rather
# than presenting them as equally precise.
_SPREAD_IN_REASON = re.compile(r"spread\s+([0-9]*\.?[0-9]+)\s+exceeds")

# Symmetric in RATIO space, which is the only way a drift histogram can be read for
# one-way bias. Bands of "0.9-1.0" and "1.0-1.1" look symmetric and are not — 1/0.9 is
# 1.111, so the down-bucket is wider and a symmetric distribution would show more downs.
# These are |ln(ratio)| cuts, so r and 1/r always land in the same band.
_BANDS = (0.05, 0.10, 0.25, 0.50)
_BAND_EPS = 1e-12          # see band_of; resolves a representation tie, nothing else


class FactorFileError(Exception):
    """An input cannot be read as a factor file."""


# --------------------------------------------------------------------------- #
# Loading. Pure apart from the read.                                           #
# --------------------------------------------------------------------------- #

def load(path):
    """{path, factors, held_out, measurement, kept, held, all} — read-only."""
    try:
        with open(path) as fh:
            doc = json.load(fh)
    except FileNotFoundError:
        raise FactorFileError(f"{path}: no such file") from None
    except json.JSONDecodeError as e:
        raise FactorFileError(f"{path}: not valid JSON ({e})") from None
    if not isinstance(doc, dict):
        raise FactorFileError(f"{path}: expected a JSON object, got {type(doc).__name__}")
    factors = doc.get("factors")
    held = doc.get("held_out")
    if not isinstance(factors, dict) or not isinstance(held, dict):
        raise FactorFileError(
            f"{path}: expected object-valued 'factors' and 'held_out' keys. This does not "
            f"look like a spot_face_factors.json.")
    overlap = set(factors) & set(held)
    if overlap:
        # The generator cannot produce this, so it means a hand-edit. Refuse rather than
        # silently picking one, because which map a spot is in is the whole of section 3.
        raise FactorFileError(
            f"{path}: {len(overlap)} slug(s) appear in BOTH factors and held_out "
            f"({', '.join(sorted(overlap)[:5])}). The generator cannot produce that; the "
            f"file has been hand-edited and its map membership is not trustworthy.")
    measurement = doc.get("measurement") if isinstance(doc.get("measurement"), dict) else {}
    return {
        "path": path,
        "factors": factors,
        "held_out": held,
        "measurement": measurement,
        "schema_version": doc.get("_schema_version"),
        "kept": set(factors),
        "held": set(held),
        "all": set(factors) | set(held),
    }


# --------------------------------------------------------------------------- #
# Small numeric helpers.                                                       #
# --------------------------------------------------------------------------- #

def comparable(value):
    """A factor reduced to the precision the KEPT branch writes, or None.

    Applied to BOTH sides of every comparison, including kept-to-kept where it is a
    no-op. Uniform rather than conditional so there is no call site that forgets.
    """
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return round(v, FACTOR_DECIMALS)


def kept_spread(rec):
    """p75/p25 from a KEPT record — exact, computed from the stored quartiles."""
    try:
        p25, p75 = float(rec.get("p25")), float(rec.get("p75"))
    except (TypeError, ValueError):
        return None
    if not (p25 > 0.0) or not math.isfinite(p75):
        return None
    return p75 / p25


def held_spread(rec):
    """p75/p25 for a HELD record, parsed out of its reason string, or None.

    Only a `spread` verdict carries one. A named hold-out was never excluded on the
    statistic, so it has no spread to report and None here is the truth rather than a gap.
    """
    m = _SPREAD_IN_REASON.search(str(rec.get("reason") or ""))
    if not m:
        return None
    try:
        return float(m.group(1))
    except ValueError:
        return None


def spread_of(side, slug):
    """(value, precision) for a slug in one file. precision is 'exact' | '2dp' | None."""
    if slug in side["kept"]:
        v = kept_spread(side["factors"][slug])
        return (v, "exact" if v is not None else None)
    if slug in side["held"]:
        v = held_spread(side["held_out"][slug])
        return (v, "2dp (parsed from the reason string)" if v is not None else None)
    return (None, None)


def factor_of(side, slug):
    """The comparable factor for a slug in one file, whichever map it is in."""
    if slug in side["kept"]:
        return comparable(side["factors"][slug].get("factor"))
    if slug in side["held"]:
        return comparable(side["held_out"][slug].get("factor"))
    return None


def percentile(sorted_vals, p):
    """Nearest-rank on an already-sorted list. Stated rather than delegated so the
    selftest can pin an exact value against a hand-computed one."""
    if not sorted_vals:
        return None
    k = min(len(sorted_vals) - 1, max(0, int(round(p * (len(sorted_vals) - 1)))))
    return sorted_vals[k]


def band_of(ratio):
    """Which symmetric |ln ratio| band a ratio falls in. Returns the band's upper edge,
    or None for the outermost.

    THE EPSILON IS LOAD-BEARING AND WAS FOUND BY A TEST, not added defensively. 1/1.05 is
    not exactly representable, so abs(log(1/1.05)) exceeds log(1.05) by 7e-18 and a -5%
    move landed one band wider than a +5% move — precisely the asymmetry this scheme
    exists to remove, reintroduced at the boundary. 1e-12 is about 2e-11 of the tightest
    edge (log(1.05) = 0.0488), which is many orders below the 4-decimal precision of the
    factors being compared, so it can only ever resolve a representation tie.
    """
    if ratio is None or ratio <= 0.0:
        return None
    mag = abs(math.log(ratio))
    for edge in _BANDS:
        if mag <= math.log(1.0 + edge) + _BAND_EPS:
            return edge
    return None


# --------------------------------------------------------------------------- #
# The comparison.                                                              #
# --------------------------------------------------------------------------- #

def compare_gates(old, new):
    """Gate and window provenance, side by side, with disagreements called out.

    A DISAGREEMENT AND AN ABSENCE ARE DIFFERENT FINDINGS and are never merged. Two files
    recording 35 and 90 are a disagreement: the drift numbers below describe two different
    populations and do not mean what they appear to. A file recording nothing predates the
    provenance commit, and null there means UNRECORDED, not off — that makes the
    comparison unverifiable, which is weaker than knowing it is wrong.
    """
    om, nm = old["measurement"], new["measurement"]
    og = om.get("population_gates") if isinstance(om.get("population_gates"), dict) else {}
    ng = nm.get("population_gates") if isinstance(nm.get("population_gates"), dict) else {}

    gates, disagree, unverifiable = [], [], []
    for key in GATE_CONSTANT_KEYS:
        o, n = og.get(key), ng.get(key)
        row = {"gate": key, "old": o, "new": n}
        if o is None or n is None:
            row["status"] = "unverifiable"
            unverifiable.append(key)
        elif o != n:
            row["status"] = "DISAGREE"
            disagree.append(key)
        else:
            row["status"] = "same"
        gates.append(row)

    # The exclusion threshold is a gate too — it decides section 3's membership — and it
    # lives outside population_gates because it predates it.
    o_iqr, n_iqr = om.get("max_iqr_ratio_p75_p25"), nm.get("max_iqr_ratio_p75_p25")
    iqr_row = {"gate": "max_iqr_ratio_p75_p25", "old": o_iqr, "new": n_iqr}
    if o_iqr is None or n_iqr is None:
        iqr_row["status"] = "unverifiable"
        unverifiable.append("max_iqr_ratio_p75_p25")
    elif o_iqr != n_iqr:
        iqr_row["status"] = "DISAGREE"
        disagree.append("max_iqr_ratio_p75_p25")
    else:
        iqr_row["status"] = "same"
    gates.append(iqr_row)

    return {
        "windows": {
            "old": {"run_on": om.get("run_on"), "window": om.get("window"),
                    "generated_at": om.get("generated_at"),
                    "window_from": om.get("window_from"),
                    "schema_version": old["schema_version"]},
            "new": {"run_on": nm.get("run_on"), "window": nm.get("window"),
                    "generated_at": nm.get("generated_at"),
                    "window_from": nm.get("window_from"),
                    "schema_version": new["schema_version"]},
        },
        "gates": gates,
        "disagreement": sorted(disagree),
        "unverifiable": sorted(unverifiable),
    }


def compare_drift(old, new, both_kept):
    """Section 1 — the only population that answers P0-1.

    Every spot here was KEPT in both files, so both factors exist, both are at the same
    precision, and the change is a measurement of the same thing twice.
    """
    rows, unusable = [], []
    for slug in sorted(both_kept):
        o, n = factor_of(old, slug), factor_of(new, slug)
        if o is None or n is None or o <= 0.0:
            # A zero or negative divisor cannot produce a ratio, and the generator refuses
            # to write one; recorded rather than dropped so the count still reconciles.
            unusable.append({"slug": slug, "old": o, "new": n})
            continue
        ratio = n / o
        rows.append({
            "slug": slug, "old": o, "new": n,
            "ratio": ratio,
            "pct": (ratio - 1.0) * 100.0,
            "old_spread": kept_spread(old["factors"][slug]),
            "new_spread": kept_spread(new["factors"][slug]),
        })

    ratios = sorted(r["ratio"] for r in rows)
    n_up = sum(1 for r in rows if r["ratio"] > 1.0)
    n_down = sum(1 for r in rows if r["ratio"] < 1.0)
    n_flat = sum(1 for r in rows if r["ratio"] == 1.0)

    # AN ORDERED LIST OF EXCLUSIVE RANGES, NOT A DICT KEYED BY "within +/-N%".
    #
    # The old labels said "within +/-10%" for a bucket that actually held 5%-10% only, and
    # a dict ordered them lexicographically, so a real run printed
    #     within +/-10%  35 / within +/-25%  30 / within +/-5%  62 / within +/-50%  1
    # Two defects in four lines: "within" reads as cumulative, so 35 looks like the number
    # of spots that moved under 10% when the true figure is 97; and 5% sorts after 25%
    # because "5" > "2" as text. A reader who trusts either reading gets the stability
    # answer backwards, which is the one question this tool exists to inform.
    #
    # The ranges are exclusive and the cumulative count is carried alongside, so both
    # readings are available and neither has to be inferred. A list rather than a dict
    # because the ORDER IS PART OF THE MEANING and a dict does not promise it to a JSON
    # consumer.
    edges = list(_BANDS)
    counters = {edge: {"total": 0, "up": 0, "down": 0, "flat": 0}
                for edge in edges + [None]}
    for r in rows:
        bucket = counters[band_of(r["ratio"])]
        bucket["total"] += 1
        bucket["up" if r["ratio"] > 1 else "down" if r["ratio"] < 1 else "flat"] += 1

    bands, running = [], 0
    for i, edge in enumerate(edges + [None]):
        c = counters[edge]
        running += c["total"]
        lower = 0.0 if i == 0 else edges[i - 1]
        if edge is None:
            label = f"beyond {edges[-1] * 100:g}%"
        else:
            label = f"{lower * 100:g}-{edge * 100:g}%"
        bands.append({
            "label": label,
            "lower_pct": lower * 100,
            "upper_pct": None if edge is None else edge * 100,
            **c,
            "cumulative": running,
        })

    movers = sorted(rows, key=lambda r: abs(math.log(r["ratio"])), reverse=True)
    return {
        "n": len(rows),
        "unusable": unusable,
        "median_ratio": percentile(ratios, 0.50),
        "p10_ratio": percentile(ratios, 0.10),
        "p25_ratio": percentile(ratios, 0.25),
        "p75_ratio": percentile(ratios, 0.75),
        "p90_ratio": percentile(ratios, 0.90),
        "min_ratio": ratios[0] if ratios else None,
        "max_ratio": ratios[-1] if ratios else None,
        "n_up": n_up, "n_down": n_down, "n_unchanged": n_flat,
        # The single number the one-way-versus-symmetric reading turns on. Positive means
        # more spots rose than fell; zero means the directions balance whatever the
        # magnitudes did.
        "direction_balance": n_up - n_down,
        "bands": bands,
        "largest_movers": movers[:10],
        "rows": rows,
    }


def compare_membership(old, new):
    """Section 2 — spots the GATES moved, which have no drift to report."""
    def describe(side, slug):
        where = "factors" if slug in side["kept"] else "held_out"
        rec = (side["factors"] if where == "factors" else side["held_out"])[slug]
        return {"slug": slug, "map": where, "factor": factor_of(side, slug),
                "verdict": rec.get("verdict"), "hours": rec.get("hours")}

    entered = sorted(new["all"] - old["all"])
    left = sorted(old["all"] - new["all"])
    return {
        "entered": [describe(new, s) for s in entered],
        "left": [describe(old, s) for s in left],
        "n_entered": len(entered),
        "n_left": len(left),
    }


def compare_holdout(old, new, present_in_both):
    """Section 3 — crossings between `factors` and `held_out`, with the margins.

    Named hold-outs are split out because they are hardcoded by slug and cannot churn;
    averaging them into the crossing rate would understate how volatile the spread filter
    actually is.
    """
    threshold_new = new["measurement"].get("max_iqr_ratio_p75_p25")
    threshold_old = old["measurement"].get("max_iqr_ratio_p75_p25")

    def crossing(slug, direction):
        o_spread, o_prec = spread_of(old, slug)
        n_spread, n_prec = spread_of(new, slug)
        row = {
            "slug": slug, "direction": direction,
            "old_factor": factor_of(old, slug), "new_factor": factor_of(new, slug),
            "old_spread": o_spread, "old_spread_precision": o_prec,
            "new_spread": n_spread, "new_spread_precision": n_prec,
            "old_threshold": threshold_old, "new_threshold": threshold_new,
            "old_margin": (o_spread - threshold_old)
                          if (o_spread is not None and threshold_old is not None) else None,
            "new_margin": (n_spread - threshold_new)
                          if (n_spread is not None and threshold_new is not None) else None,
            "new_verdict": (new["held_out"].get(slug) or {}).get("verdict"),
            "old_verdict": (old["held_out"].get(slug) or {}).get("verdict"),
        }
        # Same factor either side means the spot did not move; the FILTER moved across it.
        row["factor_unchanged"] = (row["old_factor"] is not None
                                   and row["old_factor"] == row["new_factor"])
        return row

    named = sorted(NAMED_HELD_OUT)
    kept_to_held = sorted((old["kept"] & new["held"]) - set(named))
    held_to_kept = sorted((old["held"] & new["kept"]) - set(named))
    held_both = sorted((old["held"] & new["held"]) - set(named))

    named_rows = []
    for slug in named:
        where_old = ("held_out" if slug in old["held"] else
                     "factors" if slug in old["kept"] else "absent")
        where_new = ("held_out" if slug in new["held"] else
                     "factors" if slug in new["kept"] else "absent")
        # WHAT THE NAMED LIST ACTUALLY PINS, AND WHAT IT DOES NOT.
        #
        # HELD_OUT is consulted by classify() and only ever moves a spot that REACHED the
        # builder from `factors` into `held_out`. It says nothing about whether the spot
        # reaches the builder at all — the harness decides that, upstream, and can drop a
        # spot for reasons the named list has never heard of.
        #
        # fort-point is the case that exposed this. It is named, yet it vanished from the
        # new file because the harness rejected it on "no orientation_deg or no
        # metaShoreNormal": its MOP point's shore normal now reads as ABSENT rather than
        # as a fabricated 0.0, which is the masked-scalar fix working as intended. That is
        # a MEMBERSHIP change — it is already listed under section 2 — and calling it
        # impossible sent a reader looking for a bug in the hold-out machinery instead.
        #
        # So an absent side is a population change and points at section 2. Only a move
        # BETWEEN the two maps, with the spot present on both sides, is impossible: a
        # named spot can never be written to `factors`.
        #
        # ABSENT FROM BOTH IS ITS OWN ANSWER, not a departure. Calling it "left the
        # population" would report a loss that never happened — the spot was in neither
        # file, so nothing about it changed, and a reader chasing the loss finds nothing.
        if where_old == where_new == "absent":
            status = "absent from both"
            impossible = False
        elif "absent" in (where_old, where_new):
            status = "left the population" if where_new == "absent" else "entered"
            impossible = False
        elif where_old != where_new:
            status = "moved between maps"
            impossible = True
        else:
            status = "unchanged"
            impossible = False
        named_rows.append({
            "slug": slug, "old": where_old, "new": where_new,
            "status": status, "impossible": impossible,
        })
    return {
        "kept_to_held": [crossing(s, "kept -> held_out") for s in kept_to_held],
        "held_to_kept": [crossing(s, "held_out -> factors") for s in held_to_kept],
        "held_in_both": held_both,
        "n_crossings": len(kept_to_held) + len(held_to_kept),
        "named": named_rows,
        "named_slugs": named,
        "present_in_both": len(present_in_both),
    }


def reconcile(union, buckets):
    """{union, bucketed, ok} — or raise if the buckets do not partition the union.

    EVERY SPOT MUST LAND IN EXACTLY ONE BUCKET, and this is checked rather than assumed
    because the three sections are only readable if they are disjoint: a spot counted in
    both drift and churn is double-reported, and one counted in neither silently vanishes.
    Both are the exact failure the separation exists to prevent, so they abort rather than
    producing a report that looks fine.

    A SEPARATE FUNCTION so the invariant can be tested directly. Inline, no input could
    reach either branch while the bucket logic was correct, which made them untestable
    defensive code — mutation testing found both survived every fixture.
    """
    seen = set()
    for b in buckets:
        dupes = seen & b
        if dupes:
            raise AssertionError(f"slug(s) in two buckets: {sorted(dupes)}")
        seen |= b
    missing = set(union) - seen
    if missing:
        raise AssertionError(f"slug(s) in no bucket: {sorted(missing)}")
    return {"union": len(set(union)), "bucketed": len(seen), "ok": True}


def compare(old, new):
    """The whole report. Buckets partition the union of both populations."""
    both = old["all"] & new["all"]
    both_kept = old["kept"] & new["kept"]
    report = {
        "old_path": old["path"], "new_path": new["path"],
        "counts": {
            "old": {"kept": len(old["kept"]), "held_out": len(old["held"]),
                    "total": len(old["all"])},
            "new": {"kept": len(new["kept"]), "held_out": len(new["held"]),
                    "total": len(new["all"])},
        },
        "provenance": compare_gates(old, new),
        "drift": compare_drift(old, new, both_kept),
        "membership": compare_membership(old, new),
        "holdout": compare_holdout(old, new, both),
    }
    h = report["holdout"]
    report["reconciliation"] = reconcile(old["all"] | new["all"], [
        both_kept,
        {r["slug"] for r in h["kept_to_held"]},
        {r["slug"] for r in h["held_to_kept"]},
        set(h["held_in_both"]),
        set(NAMED_HELD_OUT) & both,
        {r["slug"] for r in report["membership"]["entered"]},
        {r["slug"] for r in report["membership"]["left"]},
    ])
    return report


# --------------------------------------------------------------------------- #
# Rendering.                                                                   #
# --------------------------------------------------------------------------- #

def _fmt(v, spec=".4f"):
    return "—" if v is None else format(v, spec)


def render(report):
    """The human-readable report. Sections in the order the reading rules need them."""
    out = []
    w = out.append
    prov = report["provenance"]
    c = report["counts"]

    w("=" * 78)
    w("FACE FACTOR COMPARISON")
    w(f"  old  {report['old_path']}")
    w(f"  new  {report['new_path']}")
    w("=" * 78)

    if prov["disagreement"]:
        w("")
        w("!" * 78)
        w("!! THE TWO FILES DISAGREE ON A GATE. The drift numbers below describe TWO")
        w("!! DIFFERENT POPULATIONS and do not measure what they appear to measure.")
        for key in prov["disagreement"]:
            row = next(g for g in prov["gates"] if g["gate"] == key)
            w(f"!!   {key}: old {row['old']!r} -> new {row['new']!r}")
        w("!! A spot can change factor because the ocean moved or because the gate that")
        w("!! selected it moved. This comparison cannot tell those apart.")
        w("!" * 78)

    w("")
    w("PROVENANCE")
    for label in ("old", "new"):
        p = prov["windows"][label]
        win = p["window"] or {}
        w(f"  {label:3}  run_on {p['run_on'] or '—'}   window "
          f"{win.get('t0') or '—'} .. {win.get('t1') or '—'}   "
          f"generated_at {p['generated_at'] or '—'}")
    w("")
    w(f"  {'gate':<38} {'old':>12} {'new':>12}  status")
    for g in prov["gates"]:
        w(f"  {g['gate']:<38} {str(g['old']):>12} {str(g['new']):>12}  {g['status']}")
    if prov["unverifiable"]:
        w("")
        w(f"  {len(prov['unverifiable'])} gate(s) unrecorded on one or both sides: "
          f"{', '.join(prov['unverifiable'])}")
        w("  NULL MEANS UNRECORDED, NOT OFF. A file predating the provenance commit says")
        w("  nothing about its gates; that makes the populations unverifiable rather than")
        w("  known to differ, which is a weaker finding than a disagreement, not a safer one.")

    w("")
    w(f"POPULATIONS   old {c['old']['kept']} kept + {c['old']['held_out']} held = "
      f"{c['old']['total']}      new {c['new']['kept']} kept + {c['new']['held_out']} held = "
      f"{c['new']['total']}")

    # --- 1 ---------------------------------------------------------------- #
    d = report["drift"]
    w("")
    w("-" * 78)
    w(f"1. FACTOR DRIFT — {d['n']} spots kept in BOTH files")
    w("   The only population that answers P0-1. Membership and hold-out changes are")
    w("   excluded from every number in this section.")
    w("-" * 78)
    if not d["n"]:
        w("   no spots kept in both files — nothing here can answer P0-1")
    else:
        w(f"   median ratio (new/old)   {_fmt(d['median_ratio'])}"
          f"      [1.0000 = no central drift]")
        w(f"   ratio spread   p10 {_fmt(d['p10_ratio'])}   p25 {_fmt(d['p25_ratio'])}"
          f"   p75 {_fmt(d['p75_ratio'])}   p90 {_fmt(d['p90_ratio'])}")
        w(f"   range          min {_fmt(d['min_ratio'])}   max {_fmt(d['max_ratio'])}")
        w("")
        w(f"   direction      {d['n_up']} up   {d['n_down']} down   "
          f"{d['n_unchanged']} unchanged      balance {d['direction_balance']:+d}")
        w("   A balance near zero with a wide spread is CHURN IN BOTH DIRECTIONS.")
        w("   A balance far from zero, or a median ratio away from 1.0, is ONE-WAY DRIFT.")
        w("")
        w("   distribution — EXCLUSIVE ranges, widest last. `cum` is the running total,")
        w("   i.e. the number of spots that moved by LESS than that band's upper edge.")
        w("   Bands are symmetric in ratio space: r and 1/r share a band.")
        w(f"     {'range':<14} {'n':>4} {'cum':>5}   direction")
        for b in d["bands"]:
            w(f"     {b['label']:<14} {b['total']:4d} {b['cumulative']:5d}   "
              f"({b['up']} up, {b['down']} down, {b['flat']} unchanged)")
        if d["largest_movers"]:
            w("")
            w("   largest movers")
            w(f"     {'slug':<30} {'old':>9} {'new':>9} {'ratio':>8} {'change':>9}")
            for r in d["largest_movers"]:
                w(f"     {r['slug']:<30} {r['old']:>9.4f} {r['new']:>9.4f} "
                  f"{r['ratio']:>8.3f} {r['pct']:>8.1f}%")
        if d["unusable"]:
            w("")
            w(f"   {len(d['unusable'])} spot(s) kept in both but with no usable ratio: "
              f"{', '.join(u['slug'] for u in d['unusable'])}")

    # --- 2 ---------------------------------------------------------------- #
    m = report["membership"]
    w("")
    w("-" * 78)
    w(f"2. MEMBERSHIP CHANGE — {m['n_entered']} entering, {m['n_left']} leaving")
    w("   The gates moved, not the ocean. These spots have NO before-and-after factor and")
    w("   appear in NONE of the drift statistics above.")
    w("-" * 78)
    if not (m["entered"] or m["left"]):
        w("   the two files cover the same spots")
    for label, rows in (("entering (no 09-01 counterpart)", m["entered"]),
                        ("leaving (no longer generated)", m["left"])):
        if rows:
            w(f"   {label}")
            for r in rows:
                v = f"  [{r['verdict']}]" if r.get("verdict") else ""
                w(f"     {r['slug']:<30} -> {r['map']:<9} factor {_fmt(r['factor'])}{v}")

    # --- 3 ---------------------------------------------------------------- #
    h = report["holdout"]
    w("")
    w("-" * 78)
    w(f"3. HOLD-OUT CHURN — {h['n_crossings']} crossing(s) between factors and held_out")
    w("   A quality filter recomputed every run, not a fixed group. Crossings are shown")
    w("   with both spread values and the margin, so a boundary wobble reads as one.")
    w("-" * 78)
    if not h["n_crossings"]:
        w("   no spot crossed the filter")
    for rows in (h["kept_to_held"], h["held_to_kept"]):
        for r in rows:
            w(f"   {r['slug']}  ({r['direction']})")
            w(f"     spread   old {_fmt(r['old_spread'], '.4f')} "
              f"[{r['old_spread_precision'] or 'not recorded'}]"
              f"   -> new {_fmt(r['new_spread'], '.4f')} "
              f"[{r['new_spread_precision'] or 'not recorded'}]")
            w(f"     margin   old {_fmt(r['old_margin'], '+.4f')} vs "
              f"{_fmt(r['old_threshold'], '.2f')}"
              f"   -> new {_fmt(r['new_margin'], '+.4f')} vs "
              f"{_fmt(r['new_threshold'], '.2f')}")
            w(f"     factor   old {_fmt(r['old_factor'])} -> new {_fmt(r['new_factor'])}"
              + ("   (UNCHANGED — the filter moved, the spot did not)"
                 if r["factor_unchanged"] else ""))
    if any(r["old_spread_precision"] == "2dp (parsed from the reason string)"
           or r["new_spread_precision"] == "2dp (parsed from the reason string)"
           for r in h["kept_to_held"] + h["held_to_kept"]):
        w("")
        w("   A held_out record carries no p25/p75 — only the 2-decimal value inside its")
        w("   reason string — so a margin of 0.03 is at the limit of what is recorded.")
    w("")
    w(f"   held out in BOTH files (not churn): {len(h['held_in_both'])}"
      + (f"  {', '.join(h['held_in_both'])}" if h["held_in_both"] else ""))
    w("")
    w("   NAMED hold-outs, reported separately — hardcoded by slug, cannot churn.")
    w("   Mixing them in would inflate the apparent stability of the spread filter.")
    w("   They pin what happens to a spot that REACHES the builder, not whether it does —")
    w("   the harness can drop one upstream for reasons this list never sees.")
    for r in h["named"]:
        if r["impossible"]:
            flag = "   <-- MOVED BETWEEN MAPS, which should be impossible"
        elif r["status"] == "left the population":
            flag = "   <-- LEFT THE POPULATION (not a hold-out failure) — see section 2"
        elif r["status"] == "entered":
            flag = "   <-- entered the population — see section 2"
        elif r["status"] == "absent from both":
            flag = "   <-- in neither file; nothing changed for it"
        else:
            flag = ""
        w(f"     {r['slug']:<30} old {r['old']:<9} new {r['new']:<9}{flag}")

    w("")
    w("-" * 78)
    w("READING")
    w("  This tool reports the three changes separately and deliberately does NOT classify")
    w("  the result. 'Stable' has no numeric definition in the brief, and choosing one here")
    w("  would answer P0-1 on the tool's authority rather than the measurement's.")
    w("  The numbers the fixed rules turn on are the median ratio, the direction balance")
    w("  and the spread, all in section 1.")
    if prov["disagreement"]:
        w("")
        w("  AND THE GATES DISAGREE (see the top). Section 1 is comparing two different")
        w("  populations; settle that before reading any of it as drift.")
    w("-" * 78)
    return "\n".join(out)


# --------------------------------------------------------------------------- #
# CLI.                                                                         #
# --------------------------------------------------------------------------- #

def main(argv=None):
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old", nargs="?", help="the baseline spot_face_factors.json")
    ap.add_argument("new", nargs="?", help="the regenerated spot_face_factors.json")
    ap.add_argument("--json", action="store_true", dest="as_json",
                    help="emit the structured form instead of the report")
    ap.add_argument("--selftest", action="store_true", help="offline logic proof")
    a = ap.parse_args(argv)
    if a.selftest:
        return run_selftest()
    if not a.old or not a.new:
        ap.error("both OLD and NEW are required (or use --selftest)")
    try:
        report = compare(load(a.old), load(a.new))
    except FactorFileError as e:
        print(str(e), file=sys.stderr)
        return 2
    if a.as_json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(render(report))
    return 0


# --------------------------------------------------------------------------- #
# Selftest — pure logic, synthetic files, no network and no repo data.          #
# --------------------------------------------------------------------------- #

def _doc(factors=None, held=None, gates=None, window=("2026-08-18", "2026-09-01"),
         max_iqr=1.7):
    m = {"run_on": window[1], "window": {"t0": window[0], "t1": window[1]},
         "generated_at": window[1] + "T00:00:00+00:00",
         "max_iqr_ratio_p75_p25": max_iqr}
    if gates is not None:
        m["population_gates"] = gates
    return {"_schema_version": 1, "measurement": m,
            "factors": factors or {}, "held_out": held or {}}


def _kept(factor, p25=1.0, p75=1.5, n=330):
    return {"factor": factor, "hours": n, "p10": 0.8, "p25": p25, "p75": p75,
            "p90": 2.0, "spread_p90_p10": 2.5}


def _held_spread(factor, iqr):
    return {"verdict": "spread", "factor": factor, "hours": 330,
            "reason": f"within-spot p75/p25 spread {iqr:.2f} exceeds 1.7 — the median is "
                      f"the centre of a cloud, not a stable offset"}


def _named(factor):
    return {"verdict": "held_out", "factor": factor, "hours": 330,
            "reason": "held out by name"}


def _write_pair(old_doc, new_doc):
    import tempfile
    d = tempfile.mkdtemp()
    po, pn = os.path.join(d, "old.json"), os.path.join(d, "new.json")
    with open(po, "w") as fh:
        json.dump(old_doc, fh)
    with open(pn, "w") as fh:
        json.dump(new_doc, fh)
    return po, pn


def run_selftest():
    ok = True

    def check(n, c):
        nonlocal ok
        ok = ok and c
        print(f"  {'PASS' if c else 'FAIL'}  {n}")

    GATES = {"FACE_SHORE_NORMAL_MAX_DELTA": 90.0, "MATCH_SEPARATION_M": 2200.0,
             "is_valid_surf_spot_filter_applied": True}

    # --- precision: a crossing must not read as drift ------------------------ #
    # 0.4967949362146973 is moonstone-beach-humboldt's real held_out factor; the kept
    # branch would write 0.4968. Without the reduction that is a 0.001% "move".
    check("a raw float and its 4dp form compare equal",
          comparable(0.4967949362146973) == comparable(0.4968))
    check("...and the reduction is to 4 places", comparable(1.23456789) == 1.2346)
    check("a non-numeric factor is None, not a crash", comparable("x") is None)
    check("a NaN factor is None", comparable(float("nan")) is None)

    # --- symmetric bands ----------------------------------------------------- #
    check("a ratio and its reciprocal land in the same band",
          band_of(1.10) == band_of(1 / 1.10) and band_of(1.30) == band_of(1 / 1.30))
    check("1.0 is in the tightest band", band_of(1.0) == _BANDS[0])
    check("exactly +5% is inside the 5% band", band_of(1.05) == 0.05)
    check("-5% (1/1.05) is inside it too", band_of(1 / 1.05) == 0.05)
    # Every edge, both directions. The 5% case above failed before _BAND_EPS existed.
    for _e in _BANDS:
        check(f"+{int(_e * 100)}% and its reciprocal share the {int(_e * 100)}% band",
              band_of(1.0 + _e) == _e and band_of(1.0 / (1.0 + _e)) == _e)
    check("the epsilon does not widen a band to swallow the next value",
          band_of(1.0501) == 0.10 and band_of(1.0 / 1.0501) == 0.10)
    check("beyond the outermost edge is None", band_of(3.0) is None)

    # --- percentile ---------------------------------------------------------- #
    check("percentile p50 of 1..5 is 3", percentile([1, 2, 3, 4, 5], 0.50) == 3)
    check("percentile p10 of 1..5 is 1", percentile([1, 2, 3, 4, 5], 0.10) == 1)
    check("percentile on an empty list is None", percentile([], 0.5) is None)

    # --- spread extraction --------------------------------------------------- #
    check("a kept record's spread is computed exactly",
          abs(kept_spread({"p25": 1.6, "p75": 2.5}) - 1.5625) < 1e-12)
    check("a held record's spread is parsed from its reason",
          held_spread(_held_spread(1.5, 1.73)) == 1.73)
    check("a named hold-out has no spread to parse",
          held_spread(_named(3.0)) is None)
    check("a kept record with a zero p25 yields None, not a division by zero",
          kept_spread({"p25": 0.0, "p75": 2.0}) is None)

    # --- SCENARIO: identical inputs ------------------------------------------ #
    same = _doc({"a": _kept(2.0), "b": _kept(1.5)},
                {"tarpits": _held_spread(0.7, 2.3), "rincon": _named(0.62)}, GATES)
    r = compare(load(*_write_pair(same, same)[:1]), load(_write_pair(same, same)[1]))
    check("identical files: 2 spots in drift", r["drift"]["n"] == 2)
    check("identical files: median ratio exactly 1.0", r["drift"]["median_ratio"] == 1.0)
    check("identical files: nothing moves",
          r["drift"]["n_up"] == 0 and r["drift"]["n_down"] == 0)
    check("identical files: balance zero", r["drift"]["direction_balance"] == 0)
    check("identical files: no membership change",
          r["membership"]["n_entered"] == 0 and r["membership"]["n_left"] == 0)
    check("identical files: no crossings", r["holdout"]["n_crossings"] == 0)
    check("identical files: no gate disagreement", r["provenance"]["disagreement"] == [])

    # --- SCENARIO: pure drift ------------------------------------------------ #
    old = _doc({"a": _kept(2.0), "b": _kept(1.0), "c": _kept(4.0)}, {}, GATES)
    new = _doc({"a": _kept(2.2), "b": _kept(0.9), "c": _kept(4.0)}, {}, GATES)
    r = compare(load(_write_pair(old, new)[0]), load(_write_pair(old, new)[1]))
    check("pure drift: all three in drift", r["drift"]["n"] == 3)
    check("pure drift: one up, one down, one flat",
          (r["drift"]["n_up"], r["drift"]["n_down"], r["drift"]["n_unchanged"]) == (1, 1, 1))
    check("pure drift: balance zero -> churn in both directions",
          r["drift"]["direction_balance"] == 0)
    check("pure drift: median ratio is the middle one (1.0)",
          r["drift"]["median_ratio"] == 1.0)
    check("pure drift: a's ratio is 1.1 exactly",
          abs(next(x["ratio"] for x in r["drift"]["rows"] if x["slug"] == "a") - 1.1) < 1e-12)
    check("pure drift: no membership or churn noise",
          r["membership"]["n_entered"] == 0 and r["holdout"]["n_crossings"] == 0)

    # --- SCENARIO: one-way drift is distinguishable -------------------------- #
    new_up = _doc({"a": _kept(2.2), "b": _kept(1.1), "c": _kept(4.4)}, {}, GATES)
    r = compare(load(_write_pair(old, new_up)[0]), load(_write_pair(old, new_up)[1]))
    check("one-way drift: balance is +3, not 0", r["drift"]["direction_balance"] == 3)
    check("one-way drift: median ratio is 1.1, not 1.0",
          abs(r["drift"]["median_ratio"] - 1.1) < 1e-9)

    # --- SCENARIO: pure membership change ------------------------------------ #
    old = _doc({"a": _kept(2.0), "gone": _kept(3.0)}, {}, GATES)
    new = _doc({"a": _kept(2.0), "new1": _kept(1.1), "new2": _kept(1.2)}, {}, GATES)
    r = compare(load(_write_pair(old, new)[0]), load(_write_pair(old, new)[1]))
    check("membership: two entering, one leaving",
          r["membership"]["n_entered"] == 2 and r["membership"]["n_left"] == 1)
    check("membership: the leaver is named", r["membership"]["left"][0]["slug"] == "gone")
    check("membership: only the shared spot is in drift", r["drift"]["n"] == 1)
    check("MEMBERSHIP NEVER ENTERS THE DRIFT STATISTICS",
          {x["slug"] for x in r["drift"]["rows"]} == {"a"})
    check("membership: drift is flat, so the file reads as stable despite 3 changes",
          r["drift"]["median_ratio"] == 1.0 and r["drift"]["direction_balance"] == 0)

    # --- SCENARIO: hold-out crossing, kept -> held --------------------------- #
    old = _doc({"wobbler": _kept(2.0, p25=1.0, p75=1.65)}, {}, GATES)
    new = _doc({}, {"wobbler": _held_spread(2.0, 1.73)}, GATES)
    r = compare(load(_write_pair(old, new)[0]), load(_write_pair(old, new)[1]))
    check("crossing out: one crossing, zero drift, zero membership",
          r["holdout"]["n_crossings"] == 1 and r["drift"]["n"] == 0
          and r["membership"]["n_entered"] == 0 and r["membership"]["n_left"] == 0)
    x = r["holdout"]["kept_to_held"][0]
    check("crossing out: old spread computed exactly (1.65)",
          abs(x["old_spread"] - 1.65) < 1e-12 and x["old_spread_precision"] == "exact")
    check("crossing out: new spread parsed (1.73) and labelled 2dp",
          x["new_spread"] == 1.73 and x["new_spread_precision"].startswith("2dp"))
    check("crossing out: old margin is under the threshold",
          abs(x["old_margin"] - (1.65 - 1.7)) < 1e-12 and x["old_margin"] < 0)
    check("crossing out: new margin is just over (+0.03)",
          abs(x["new_margin"] - 0.03) < 1e-9)
    check("crossing out: the FACTOR did not move — the filter did",
          x["factor_unchanged"] is True)

    # --- SCENARIO: hold-out crossing, held -> kept --------------------------- #
    # The raw-float / rounded asymmetry is the point: 0.4967949362146973 -> 0.4968.
    old = _doc({}, {"returner": _held_spread(0.4967949362146973, 1.94)}, GATES)
    new = _doc({"returner": _kept(0.4968, p25=1.0, p75=1.62)}, {}, GATES)
    r = compare(load(_write_pair(old, new)[0]), load(_write_pair(old, new)[1]))
    check("crossing in: one crossing the other way",
          len(r["holdout"]["held_to_kept"]) == 1 and r["holdout"]["n_crossings"] == 1)
    y = r["holdout"]["held_to_kept"][0]
    check("A PRECISION ARTIFACT IS NOT REPORTED AS A MOVE", y["factor_unchanged"] is True)
    check("crossing in: spread fell from 1.94 to 1.62",
          y["old_spread"] == 1.94 and abs(y["new_spread"] - 1.62) < 1e-12)
    check("crossing in: margin crossed from + to -",
          y["old_margin"] > 0 and y["new_margin"] < 0)
    check("crossing in: it is NOT in the drift statistics", r["drift"]["n"] == 0)

    # --- named hold-outs are separated --------------------------------------- #
    old = _doc({"a": _kept(2.0)},
               {"rincon": _named(0.62), "fort-point": _named(3.08),
                "sandspit": _named(3.46), "tarpits": _held_spread(0.68, 2.3)}, GATES)
    r = compare(load(_write_pair(old, old)[0]), load(_write_pair(old, old)[1]))
    check("the three NAMED hold-outs come from HELD_OUT, not from a copied list",
          r["holdout"]["named_slugs"] == sorted(NAMED_HELD_OUT)
          == ["fort-point", "rincon", "sandspit"])
    check("named hold-outs are NOT in held_in_both, which counts spread exclusions",
          set(r["holdout"]["held_in_both"]) == {"tarpits"})
    check("named hold-outs are reported with their side of each file",
          all(x["old"] == "held_out" and x["new"] == "held_out"
              for x in r["holdout"]["named"]))

    # --- SCENARIO: gate disagreement ----------------------------------------- #
    old = _doc({"a": _kept(2.0)}, {}, {**GATES, "FACE_SHORE_NORMAL_MAX_DELTA": 35.0})
    new = _doc({"a": _kept(2.0)}, {}, GATES)
    r = compare(load(_write_pair(old, new)[0]), load(_write_pair(old, new)[1]))
    check("gate disagreement is detected",
          r["provenance"]["disagreement"] == ["FACE_SHORE_NORMAL_MAX_DELTA"])
    check("...and nothing is flagged unverifiable when both sides recorded it",
          r["provenance"]["unverifiable"] == [])
    text = render(r)
    check("...and the report says so at the TOP", "DISAGREE" in text.split("PROVENANCE")[0])
    check("...and says the drift describes two different populations",
          "TWO" in text and "DIFFERENT POPULATIONS" in text)
    check("...and repeats it in the READING section",
          "AND THE GATES DISAGREE" in text)

    # --- a file with no gates at all ----------------------------------------- #
    old = _doc({"a": _kept(2.0)}, {}, None)          # predates the provenance commit
    new = _doc({"a": _kept(2.0)}, {}, GATES)
    r = compare(load(_write_pair(old, new)[0]), load(_write_pair(old, new)[1]))
    check("an unrecorded gate is 'unverifiable', NOT a disagreement",
          r["provenance"]["disagreement"] == []
          and set(r["provenance"]["unverifiable"]) == set(GATE_CONSTANT_KEYS))
    check("...and the report spells out that null means unrecorded, not off",
          "NULL MEANS UNRECORDED, NOT OFF" in render(r))

    # --- differing exclusion thresholds -------------------------------------- #
    old = _doc({"a": _kept(2.0)}, {}, GATES, max_iqr=1.7)
    new = _doc({"a": _kept(2.0)}, {}, GATES, max_iqr=1.6)
    r = compare(load(_write_pair(old, new)[0]), load(_write_pair(old, new)[1]))
    check("a moved exclusion threshold is a gate disagreement too",
          "max_iqr_ratio_p75_p25" in r["provenance"]["disagreement"])

    # --- reconciliation ------------------------------------------------------ #
    old = _doc({"a": _kept(2.0), "cross": _kept(1.0), "gone": _kept(3.0)},
               {"tarpits": _held_spread(0.7, 2.3), "rincon": _named(0.62)}, GATES)
    new = _doc({"a": _kept(2.1), "fresh": _kept(1.4)},
               {"cross": _held_spread(1.0, 1.8), "tarpits": _held_spread(0.7, 2.4),
                "rincon": _named(0.62)}, GATES)
    r = compare(load(_write_pair(old, new)[0]), load(_write_pair(old, new)[1]))
    check("every slug lands in exactly one bucket", r["reconciliation"]["ok"] is True)
    check("...and the buckets cover the union",
          r["reconciliation"]["union"] == r["reconciliation"]["bucketed"] == 6)
    check("mixed case: 1 drift, 1 crossing, 1 in + 1 out",
          r["drift"]["n"] == 1 and r["holdout"]["n_crossings"] == 1
          and r["membership"]["n_entered"] == 1 and r["membership"]["n_left"] == 1)

    # --- malformed input ----------------------------------------------------- #
    def raises(fn, needle):
        try:
            fn()
        except FactorFileError as e:
            return needle in str(e)
        return False

    po, pn = _write_pair(_doc({"a": _kept(1.0)}), {"nope": 1})
    check("a file with no factors/held_out maps is refused",
          raises(lambda: load(pn), "does not look like"))
    check("a missing file is refused by name",
          raises(lambda: load(pn + ".absent"), "no such file"))
    po2, _ = _write_pair({"factors": {"a": _kept(1.0)}, "held_out": {"a": _named(1.0)}},
                         _doc())
    check("a slug in BOTH maps is refused rather than silently resolved",
          raises(lambda: load(po2), "BOTH factors and held_out"))

    # --- read-only ----------------------------------------------------------- #
    po, pn = _write_pair(_doc({"a": _kept(2.0)}, {}, GATES),
                         _doc({"a": _kept(2.2)}, {}, GATES))
    before = (open(po).read(), open(pn).read())
    compare(load(po), load(pn))
    render(compare(load(po), load(pn)))
    check("NEITHER INPUT IS MODIFIED", (open(po).read(), open(pn).read()) == before)
    # Belt and braces on the check above: the PRODUCTION half of this module must contain
    # no write at all. Split on the section banner rather than on run_selftest, because
    # the fixture writers (_write_pair and friends) are defined above it and legitimately
    # write to a temp directory.
    src = open(os.path.abspath(__file__)).read()
    production = src.split("# Selftest — pure logic")[0]
    check("the production half of this module contains no write mode",
          production.count('"w"') == 0 and production.count("'w'") == 0)
    check("...and no other writing call",
          not any(bad in production for bad in ("json.dump(", ".write(", "os.remove",
                                                "shutil.", "os.rename")))

    print()
    print("selftest: " + ("ALL PASS" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
