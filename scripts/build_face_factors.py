#!/usr/bin/env python3
"""Generate pipeline/data/spot_face_factors.json FROM the MOP validation artifact.

WHY A GENERATOR AND NOT A TRANSCRIPTION. The factors are 130-odd hand-measured constants
with no physical derivation. Typing them into a committed file by hand would make every
one of them unverifiable — you could not tell a transcription slip from a measurement, and
the exclusion rules would be applied by eye. This reads scripts/mop_spread.json, applies
the documented rules mechanically, and writes a file that records what it did per spot, so
the committed artifact can be regenerated and diffed rather than trusted.

    python3 scripts/build_face_factors.py            # dry run — prints the plan, writes nothing
    python3 scripts/build_face_factors.py --apply    # writes pipeline/data/spot_face_factors.json

INPUT (Mac-local, GITIGNORED — never committed)
    scripts/mop_face_validation_out.json   <- WHAT THE HARNESS ACTUALLY WRITES
        by_spot[].slug, .face_ratio.median, .face_ratio.n,
        .face_ratio.p25, .face_ratio.p75   <- the exclusion statistic AND the published band
        .face_ratio.p10, .face_ratio.p90   <- diagnostic only; excludes nothing
        window.{t0,t1}                     <- THE DATES THIS FILE STAMPS (see below)
        generated_at                       <- the freshness check compares this to window.t1
        constants.*                        <- the population gates, copied through verbatim

    THE INPUT NAME USED TO BE scripts/mop_spread.json AND NOTHING EVER WROTE IT. The
    harness writes mop_face_validation_out.json (mop_face_validation.py:117); this script
    read mop_spread.json; the two have the same by_spot shape, so the gap was invisible
    until someone looked. A stale mop_spread.json left over from an older run would have
    regenerated the OLD file silently. The default is now the harness's real output, and a
    leftover mop_spread.json is reported as ignored rather than quietly preferred.

DATES ARE READ FROM THE MEASUREMENT, NEVER FROM THE CLOCK. run_on, the per-record
measured_on and both window blocks describe WHEN THE MEASUREMENT HAPPENED, which is a
property of the input artifact and not of when this script was invoked. They used to be
module-level literals (RUN_ON = "2026-09-01"), so every regeneration after that date wrote
a file whose every self-describing field was false while generated_at quietly told the
truth. Resolution order, and it FAILS rather than defaulting:

    1. the spread file's own `window: {t0, t1}`  — the normal route
    2. --window-t0 / --window-t1                 — required when (1) is absent
    3. neither                                   — abort

There is deliberately no fall-back to today's date. A run three days after the window
closed is normal (the committed 09-01 file was generated on 09-03), so "today" would be
wrong in the ordinary case, and a wrong date in a committed artifact cannot be spotted by
reading it — only by re-deriving it from an input that may no longer exist.

EXCLUSION RULES, applied in this order and recorded per spot in the output's `held_out`:

  1. MOP TIER — swell_window_source == "cdip_mop". apply_mop_overrides computes those
     spots' face FROM MOP Hs, so the measured ratio is period_factor by construction and
     correcting them would divide MOP by a number derived from MOP. Read from the roster,
     never from a list, so a spot promoted to the MOP tier is excluded automatically.
  2. NAMED HOLD-OUTS — fort-point, sandspit, rincon. Reasons recorded per spot.
  3. SPREAD — face_ratio p75/p25 > FACE_FACTOR_MAX_IQR_RATIO (1.7). Above that the median
     is not describing a stable offset, it is the centre of a cloud. This was p90/p10 > 2.5
     until the p25/p75 band shipped; excluding on a tail nobody sees threw out 22 stable
     spots in one regeneration. See the config constant for the Steamer Lane example.
  4. UNUSABLE — a missing, non-numeric or non-positive median, OR no p25/p75 at all (a
     spread file predating the quartile measurement cannot be judged and is not waved
     through).

A spot that survives all four gets a factor. Everything else is recorded with its reason
and is ABSENT from the `factors` map, so the pipeline cannot correct it however it is
called.
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from pipeline.config import FACE_FACTOR_MAX_IQR_RATIO                # noqa: E402
from pipeline.enrich import _slug_for                                # noqa: E402
from pipeline.forecast.face_correction import MOP_TIER_SOURCES       # noqa: E402

SPREAD_PATH = os.path.join(HERE, "mop_face_validation_out.json")
# Read by nothing — reported if present, so a leftover cannot be mistaken for the input.
LEGACY_SPREAD_PATH = os.path.join(HERE, "mop_spread.json")
ROSTER = os.path.join(ROOT, "pipeline", "spots_enriched.json")
OUT = os.path.join(ROOT, "pipeline", "data", "spot_face_factors.json")

# The measurement this file describes. Stated here, written into every record, so a later
# reader can tell a measured factor from a guessed one without archaeology.
#
# THE DATES ARE NOT HERE ANY MORE, AND THAT IS THE POINT — see the module docstring. They
# come from the artifact being read, because they describe that measurement.
SOURCE = "scripts/mop_face_validation.py"

# Every face_ratio field the KEEP branch copies verbatim into a factor record. A record
# that reaches `factors` missing one of these would write a null, which is what shipped
# once: an older spread file predating the quartile measurement produced "p25": null, the
# published band silently did not render, and it took four steps of tracing to find. The
# validator below turns that into an abort naming the spot and the field.
KEPT_FACE_RATIO_FIELDS = ("median", "n", "p10", "p25", "p75", "p90")

# Population gates, copied through from the spread file's `constants` VERBATIM so the
# committed factor file records which population produced it. Absent -> recorded as null
# with a note, NEVER invented: a guessed gate value is worse than an admitted gap, because
# it reads as evidence.
GATE_CONSTANT_KEYS = (
    "FACE_SHORE_NORMAL_MAX_DELTA",
    "MATCH_SEPARATION_M",
    "is_valid_surf_spot_filter_applied",
)

# Named hold-outs with the reason recorded per spot. Not a spread failure — these are
# judgements about whether a corrected rating would carry information at all.
HELD_OUT = {
    "fort-point": (
        "91.7% of hours land at the 1-star floor after correction. At that size the rating "
        "carries no information: composite_stars clamps to 1.0 for any effective size "
        "between 0.5 and ~1.25 ft, so wind, tide and chop stop mattering because size "
        "dominates and the spot becomes a binary flat-or-one-star readout."
    ),
    "sandspit": (
        "60.7% of hours at the 1-star floor after correction — same mechanism as "
        "fort-point, and enough of the range collapses that the corrected rating stops "
        "discriminating between conditions."
    ),
    "rincon": (
        "Factor 0.62 with a 6.47x within-spot spread. The correction would make Rincon "
        "BIGGER, and at that spread the ratio is nearly uncorrelated — the median is the "
        "centre of a cloud rather than a stable offset, so dividing by it moves the typical "
        "hour about as often as it fixes one. This is the clearest example in the "
        "population of what the spread rule exists to catch."
    ),
}

def _comment(t0, t1):
    """The file's own prose, with the measurement window interpolated.

    A FIFTH PLACE THAT NAMED THE MEASUREMENT. run_on, measurement.window and the two
    per-record fields were the obvious four; this sentence hardcoded "14 days of SUMMER
    (2026-08-18 to 2026-09-01)" inside a constant and would have gone on asserting it
    after a 09-19 regeneration — in the one paragraph a reader is most likely to trust,
    since it is the file's stated main limitation. Prose dates rot exactly like literal
    ones and are harder to spot, so this takes the window like everything else.

    The word SUMMER is gone rather than recomputed: classifying a season from two dates is
    a judgement this script has no business making, and the dates now say it plainly.
    """
    span = (datetime.date.fromisoformat(t1) - datetime.date.fromisoformat(t0)).days
    return _COMMENT_TEMPLATE.format(t0=t0, t1=t1, span=span)


_COMMENT_TEMPLATE = (
    "Per-spot multiplicative corrections for face_ft, measured against CDIP MOP. A factor is "
    "a DIVISOR: corrected_face = published_face / factor, so 2.87 means we publish 2.87x "
    "what MOP implies. Applied by pipeline/forecast/face_correction.py at the single seam in "
    "interpret.main AFTER apply_mop_overrides and apply_nwps_overrides, which is where the "
    "four face_ft producers actually converge. Both face_ft and effective_size_ft are scaled "
    "and stars is recomputed by the production interpret.composite_stars.\n\n"

    "GENERATED, NOT TRANSCRIBED. Regenerate with `python3 scripts/build_face_factors.py "
    "--apply` from scripts/mop_face_validation_out.json (Mac-local, gitignored). Do not "
    "hand-edit: an "
    "edit here cannot be traced back to a measurement, and every exclusion below was applied "
    "mechanically.\n\n"

    "THESE ARE SEASONAL AND THAT IS NOT A CAVEAT, IT IS THE MAIN LIMITATION. Every factor "
    "was measured over a single {span}-day window, {t0} to {t1}. California's swell "
    "climate changes in winter — the measurement itself found mtr's median period at 13.79 s "
    "against sgx's 7.98 s in the same fortnight — and a single constant that absorbs "
    "period-dependent refraction will misfit when the period regime moves. These factors are "
    "UNVALIDATED ACROSS A SEASON CHANGE. Nothing here has been tested against a winter "
    "swell.\n\n"

    "THE FIRST WINTER RE-MEASUREMENT IS A TEST, NOT MAINTENANCE. If the winter factors come "
    "back close to these, a single constant per spot is a defensible model and this file is "
    "worth keeping. If they move materially, then a per-spot constant is the wrong shape — "
    "it was fitting a season, not a spot — and this whole approach should be reconsidered "
    "rather than re-tuned. Re-tuning a constant that failed its first out-of-sample test is "
    "how period_factor became unfalsifiable. The run summary warns past 120 days; the "
    "trigger for acting is that warning plus the user feedback labels, and there is "
    "deliberately no scheduler.\n\n"

    "WHAT A FACTOR IS NOT. It is not derived from anything — not slope, not exposure, not "
    "break type. You cannot predict an unmeasured spot's factor from its geometry, which "
    "means a factor cannot transfer and cannot be sanity-checked against physics. It is "
    "anchored to MOP, a SWAN model over real bathymetry rather than a measurement of a "
    "breaking wave, so every bias in MOP is inherited here. And after this ships, the "
    "harness that produced these values measures ~1.0 by construction — the check that "
    "would catch a bad factor is the check the factor came from. Against non-circular "
    "ground truth there are two user labels."
)


class SpreadError(Exception):
    """The spread artifact cannot be trusted to describe a measurement.

    Every raise site here is a case where continuing would write a committed file that
    states something false about its own provenance. That is the one failure mode this
    script exists to prevent, so it aborts rather than degrading.
    """


def _date_part(value):
    """The YYYY-MM-DD prefix of an ISO date or datetime string, or None.

    The harness writes full timestamps ("2026-09-01T12:34:56.789012+00:00"); the factor
    file has always carried plain dates. Truncating rather than parsing-and-reformatting
    keeps this indifferent to whether the source had a time, a timezone or neither, and
    the fromisoformat call is what rejects a string that merely LOOKS like a date.
    """
    if not isinstance(value, str) or len(value) < 10:
        return None
    head = value[:10]
    try:
        datetime.date.fromisoformat(head)
    except ValueError:
        return None
    return head


def resolve_window(doc, cli_t0=None, cli_t1=None):
    """(t0, t1, provenance) as YYYY-MM-DD. Raises SpreadError rather than defaulting.

    THE ORDER IS FILE FIRST, FLAGS SECOND, ABORT THIRD. The file's own window is the
    measurement's own account of itself and needs no operator to be correct; the flags
    exist for an artifact that predates the window being recorded.

    A DISAGREEMENT IS AN ABORT, NOT A PRECEDENCE QUESTION. If the file says one window and
    the operator says another, one of them is wrong about what was measured, and there is
    no way to tell which from here. Picking either would commit a date on a coin-flip.
    Both values go in the message so the operator can settle it at the source.

    Passing one flag without the other is also an abort: half a window is not a window,
    and silently pairing a given t0 with a derived t1 would fabricate the very thing this
    function exists to stop.
    """
    win = doc.get("window") if isinstance(doc.get("window"), dict) else {}
    doc_t0, doc_t1 = _date_part(win.get("t0")), _date_part(win.get("t1"))
    from_doc = doc_t0 and doc_t1

    if (cli_t0 is None) != (cli_t1 is None):
        raise SpreadError(
            "--window-t0 and --window-t1 must be given together; a half-window cannot "
            "describe a measurement")
    from_cli = cli_t0 is not None
    if from_cli:
        for label, v in (("--window-t0", cli_t0), ("--window-t1", cli_t1)):
            if _date_part(v) != v:
                raise SpreadError(f"{label}={v!r} is not a YYYY-MM-DD date")

    if from_doc and from_cli and (doc_t0, doc_t1) != (cli_t0, cli_t1):
        raise SpreadError(
            f"window disagreement: the spread file records {doc_t0}..{doc_t1} but "
            f"--window-t0/--window-t1 say {cli_t0}..{cli_t1}. One of them is wrong about "
            f"what was measured and this script cannot tell which. Fix the source rather "
            f"than overriding it.")
    if from_doc:
        return doc_t0, doc_t1, "the spread file's own window"
    if from_cli:
        return cli_t0, cli_t1, "--window-t0/--window-t1"
    raise SpreadError(
        "the spread file records no window and none was supplied. Pass --window-t0 and "
        "--window-t1 with the dates the measurement actually covers. This script will NOT "
        "substitute today's date: the window is a property of the measurement, not of when "
        "this ran, and a wrong date in a committed file cannot be spotted by reading it.")


def assert_fresh(doc, t1, window_provenance):
    """Raise unless the artifact was written no earlier than the window it describes.

    A file whose generated_at predates its own window.t1 is describing hours it could not
    have seen — the signature of a stale artifact carrying a hand-edited or inherited
    window. Compared as dates because that is the resolution the factor file stamps.

    A MISSING generated_at IS ALSO AN ABORT when the window came from the file. A document
    that asserts a window but carries no timestamp cannot be freshness-checked at all, and
    waving it through is exactly the silent-acceptance this guard exists to end. When the
    window came from the flags the operator has already vouched for it, so a missing
    timestamp is only reported.
    """
    generated = _date_part(doc.get("generated_at"))
    if generated is None:
        if window_provenance == "the spread file's own window":
            raise SpreadError(
                "the spread file records a window but no usable generated_at, so it cannot "
                "be checked against the window it claims to describe. Regenerate it with "
                "scripts/mop_face_validation.py.")
        return None
    if generated < t1:
        raise SpreadError(
            f"stale spread file: generated_at {generated} is BEFORE the end of the window "
            f"it claims to describe ({t1}). It cannot contain the hours it reports. "
            f"Re-run scripts/mop_face_validation.py and build from its output.")
    return generated


def validate_spread_records(by_spot, mop_tier_slugs, max_iqr):
    """Raise SpreadError when the artifact cannot produce complete factor records.

    TWO DISTINCT FAILURES WITH DIFFERENT CAUSES AND DIFFERENT FIXES, so they are separate
    messages rather than one "bad file":

      WHOLE-FILE — not one record carries p25/p75. The file predates the quartile
      measurement entirely. classify() would mark every spot `unusable` and build() would
      return an empty factor map, which IS loud, but only if someone reads the plan; a
      `--apply` run would cheerfully overwrite the committed file with zero factors.

      PER-RECORD — a spot that WOULD be kept is missing a field the keep branch copies.
      classify() already refuses a missing median or p25/p75, so in practice this catches
      n, p10 and p90 — the fields nothing else checks and the ones that reach the output
      untested. This is the "p25": null incident's shape, and the abort names the spot and
      the field so it takes one step instead of four.
    """
    records = [r for r in by_spot if isinstance(r, dict)]
    if records and not any(_iqr_ratio(r) is not None for r in records):
        raise SpreadError(
            f"no record in the spread file carries p25/p75 ({len(records)} records "
            f"checked). The file predates the quartile measurement, every spot would be "
            f"excluded as unusable, and --apply would overwrite the committed factors with "
            f"an empty map. Re-run scripts/mop_face_validation.py.")

    holes = {}
    for rec in records:
        if classify(rec, mop_tier_slugs, max_iqr)[0] != "keep":
            continue
        fr = rec.get("face_ratio") or {}
        missing = [f for f in KEPT_FACE_RATIO_FIELDS if fr.get(f) is None]
        if missing:
            holes[rec.get("slug")] = missing
    if holes:
        detail = "; ".join(f"{slug}: {', '.join(fields)}"
                           for slug, fields in sorted(holes.items()))
        raise SpreadError(
            f"{len(holes)} spot(s) would be kept but are missing a face_ratio field the "
            f"factor record copies, which would write a null into the committed file — "
            f"{detail}. A missing source field stops the run; it does not become a null. "
            f"Re-run scripts/mop_face_validation.py.")


def population_gates(doc):
    """The gate settings the spread file records, copied VERBATIM.

    Never computed, never defaulted, never imported from mop_face_validation. This script
    must describe the population that produced the artifact in hand, which may have been
    measured by an older harness with different gates; reading today's constants would
    stamp today's gates onto yesterday's measurement and make the record worse than empty.
    A key the artifact does not carry is recorded as null.
    """
    consts = doc.get("constants") if isinstance(doc.get("constants"), dict) else {}
    return {k: consts.get(k) for k in GATE_CONSTANT_KEYS}


def _iqr_ratio(rec):
    """p75 / p25, or None when either quartile is missing or p25 is non-positive.

    THE STATISTIC WE EXCLUDE ON IS THE ONE WE PUBLISH — see FACE_FACTOR_MAX_IQR_RATIO for
    the full argument and the Steamer Lane worked example. The p90/p10 form this replaces
    is still computed as `spread_p90_p10` and carried on every record, because it is a
    genuinely useful diagnostic of the tails; it is simply no longer what decides anything.

    A record predating the p25/p75 measurement returns None, and classify treats that as
    "cannot judge the spread" rather than as a pass — see there."""
    fr = rec.get("face_ratio") or {}
    p25, p75 = fr.get("p25"), fr.get("p75")
    try:
        p25, p75 = float(p25), float(p75)
    except (TypeError, ValueError):
        return None
    if p25 <= 0.0:
        return None
    return p75 / p25


def _spread(rec):
    """p90 / p10 — the TAIL ratio. Diagnostic only since the switch to _iqr_ratio; it is
    still recorded on every factor so a heavy tail stays visible in the diff, but it no
    longer excludes anything. Kept as a separate function so the two cannot be confused at
    a call site."""
    fr = rec.get("face_ratio") or {}
    p10, p90 = fr.get("p10"), fr.get("p90")
    try:
        p10, p90 = float(p10), float(p90)
    except (TypeError, ValueError):
        return None
    if p10 <= 0.0:
        return None
    return p90 / p10


def classify(rec, mop_tier_slugs, max_iqr=FACE_FACTOR_MAX_IQR_RATIO):
    """('keep'|'mop_tier'|'held_out'|'spread'|'unusable', detail) for one by_spot record.

    Order matters and is the documented one: tier, then named hold-out, then spread, then
    usability. A MOP-tier spot is excluded even if its numbers look perfect, because the
    objection to correcting it is structural rather than statistical.
    """
    slug = rec.get("slug")
    if not slug:
        return "unusable", "no slug"
    if slug in mop_tier_slugs:
        return "mop_tier", "face is computed FROM MOP; correcting it would divide MOP by a ratio of itself"
    if slug in HELD_OUT:
        return "held_out", HELD_OUT[slug]
    fr = rec.get("face_ratio") or {}
    try:
        med = float(fr.get("median"))
    except (TypeError, ValueError):
        return "unusable", f"median is {fr.get('median')!r}"
    if not (med > 0.0):
        return "unusable", f"median {med} is not a positive divisor"
    iqr = _iqr_ratio(rec)
    if iqr is None:
        # NOT a pass. A record with no p25/p75 cannot be judged, and silently keeping it
        # would correct a spot whose spread was never measured — the same "absent is not a
        # default" rule the factor lookup itself follows.
        return "unusable", ("no p25/p75 — regenerate mop_spread.json with "
                            "scripts/mop_face_validation.py before building factors")
    if iqr > max_iqr:
        return "spread", (f"within-spot p75/p25 spread {iqr:.2f} exceeds {max_iqr} — the "
                          f"median is the centre of a cloud, not a stable offset")
    return "keep", None


def build(spread_path=SPREAD_PATH, roster_path=ROSTER, max_iqr=FACE_FACTOR_MAX_IQR_RATIO,
          window=None, validate=True):
    """(document, plan) — the file to write and a per-category slug listing.

    `window` is (t0, t1) as YYYY-MM-DD and is NOT OPTIONAL in practice: omitting it falls
    to resolve_window, which reads the artifact and raises if it cannot. It is a parameter
    rather than a module constant because it describes the measurement being read — the
    old RUN_ON/WINDOW_T0/WINDOW_T1 literals meant every regeneration after 2026-09-01
    stamped that date onto data it had never seen.

    `validate=False` skips the artifact guards ONLY. It exists so the selftest can drive
    build() with deliberately malformed fixtures to prove the downstream classification
    still behaves; main() never passes it.
    """
    doc_in = json.load(open(spread_path))
    by_spot = doc_in.get("by_spot")
    if not isinstance(by_spot, list):
        raise ValueError(f"{spread_path}: expected a top-level 'by_spot' list")
    roster = json.load(open(roster_path))
    mop_tier_slugs = {_slug_for(s.get("name")) for s in roster
                      if s.get("swell_window_source") in MOP_TIER_SOURCES}
    known = {_slug_for(s.get("name")) for s in roster}

    if window is None:
        t0, t1, provenance = resolve_window(doc_in)
    else:
        t0, t1 = window
        provenance = "supplied by the caller"
    if validate:
        assert_fresh(doc_in, t1, provenance)
        validate_spread_records(by_spot, mop_tier_slugs, max_iqr)
    # run_on IS the date the window closed, not the date this ran. The committed 09-01 file
    # has run_on 2026-09-01 against generated_at 2026-09-03, and that distinction is the
    # whole reason both fields exist.
    measured_on = t1

    factors, held, plan = {}, {}, {"keep": [], "mop_tier": [], "held_out": [],
                                   "spread": [], "unusable": [], "unknown_slug": []}
    for rec in by_spot:
        if not isinstance(rec, dict):
            continue
        slug = rec.get("slug")
        verdict, detail = classify(rec, mop_tier_slugs, max_iqr)
        # A slug that matches no spot would fail validate_factor_slugs at run time. Catch it
        # here instead, where it can be fixed, rather than shipping a file that aborts the
        # pipeline.
        if slug and slug not in known:
            plan["unknown_slug"].append(slug)
            held[slug] = {"reason": "slug matches no spot in spots_enriched.json — not "
                                    "written to `factors`, which would abort the run",
                          "verdict": "unknown_slug"}
            continue
        plan[verdict].append(slug)
        fr = rec.get("face_ratio") or {}
        if verdict == "keep":
            sp = _spread(rec)
            factors[slug] = {
                "factor": round(float(fr["median"]), 4),
                "hours": fr.get("n"),
                "p10": fr.get("p10"),
                # THE PUBLISHED BAND. face_correction.face_range divides the corrected face
                # by these to get hi and lo. .get() rather than [] because a spread file
                # generated before mop_face_validation carried them has neither, and the
                # right behaviour then is a factor with no range — not a crash, and not a
                # substituted p10/p90, which would silently double the published width.
                "p25": fr.get("p25"),
                "p75": fr.get("p75"),
                "p90": fr.get("p90"),
                "spread_p90_p10": round(sp, 3) if sp is not None else None,
                "measured_on": measured_on,
                "window": {"t0": t0, "t1": t1},
                "source": SOURCE,
            }
        else:
            held[slug] = {
                "reason": detail,
                "verdict": verdict,
                "factor": fr.get("median"),
                "hours": fr.get("n"),
                "spread_p90_p10": round(_spread(rec), 3) if _spread(rec) is not None else None,
                "measured_on": measured_on,
                "window": {"t0": t0, "t1": t1},
                "source": SOURCE,
            }
    doc = {
        "_comment": _comment(t0, t1),
        "_schema_version": 1,
        "measurement": {
            "source": SOURCE,
            "run_on": t1,
            "window": {"t0": t0, "t1": t1},
            "window_from": provenance,
            "reference": "CDIP MOP alongshore nowcast at the 10 m contour",
            "statistic": "median of face_ft / (MOP Hs * 3.281) over the joined hours",
            "published_band": "p25/p75 of the same ratio; lo = face/p75, hi = face/p25",
            "regenerate": "python3 scripts/mop_face_validation.py"
                          " && python3 scripts/build_face_factors.py --apply",
            "max_iqr_ratio_p75_p25": max_iqr,
            # WHICH POPULATION PRODUCED THESE FACTORS. Copied verbatim from the spread
            # file's own `constants`; a key that artifact does not carry is null here and
            # is NOT filled in from this process's imports, because the artifact may have
            # been measured by an older harness under different gates. Without this a
            # factor file is silent about whether its spots passed a 35-degree or a
            # 90-degree shore-normal gate, or any separation gate at all — which is
            # exactly the ambiguity that makes two factor files hard to diff.
            "population_gates": population_gates(doc_in),
            "population_gates_note": (
                "Copied verbatim from the spread artifact's `constants`. null means that "
                "artifact did not record the gate, NOT that the gate was off."
            ),
            "spread_artifact_generated_at": doc_in.get("generated_at"),
            "generated_at": datetime.datetime.now(datetime.timezone.utc)
                            .replace(microsecond=0).isoformat(),
        },
        "factors": dict(sorted(factors.items())),
        "held_out": dict(sorted(held.items())),
    }
    return doc, plan


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--spread", default=SPREAD_PATH, help=f"input (default {SPREAD_PATH})")
    ap.add_argument("--out", default=OUT, help=f"output (default {OUT})")
    ap.add_argument("--max-iqr", type=float, default=FACE_FACTOR_MAX_IQR_RATIO,
                    help=f"p75/p25 exclusion threshold (default {FACE_FACTOR_MAX_IQR_RATIO})")
    ap.add_argument("--window-t0", help="measurement window start, YYYY-MM-DD. Required "
                                        "only when the spread file records no window.")
    ap.add_argument("--window-t1", help="measurement window end, YYYY-MM-DD. Required "
                                        "only when the spread file records no window.")
    ap.add_argument("--apply", action="store_true", help="write the file (default: dry run)")
    ap.add_argument("--selftest", action="store_true", help="offline logic proof")
    a = ap.parse_args(argv)
    if a.selftest:
        return run_selftest()
    if not os.path.exists(a.spread):
        print(f"no MOP validation artifact at {a.spread}\n"
              "  It is Mac-local and gitignored. Produce it with the MOP validation harness "
              "first:\n    python3 scripts/mop_face_validation.py", file=sys.stderr)
        return 2
    # A leftover from when this script read a name nothing wrote. Reported, never read —
    # the whole point of moving the default is that a stale file cannot be picked up by
    # accident, and staying silent about it would leave the operator believing it was.
    if (os.path.exists(LEGACY_SPREAD_PATH)
            and os.path.abspath(a.spread) != os.path.abspath(LEGACY_SPREAD_PATH)):
        print(f"note: {LEGACY_SPREAD_PATH} exists and is NOT being read. Nothing writes "
              f"that name; the input is {a.spread}. Delete it or pass --spread explicitly "
              f"if you meant it.", file=sys.stderr)

    doc_in = json.load(open(a.spread))
    try:
        t0, t1, provenance = resolve_window(doc_in, a.window_t0, a.window_t1)
        doc, plan = build(a.spread, max_iqr=a.max_iqr, window=(t0, t1))
    except SpreadError as e:
        print(f"{a.spread}: {e}", file=sys.stderr)
        return 2
    # Echoed BEFORE the plan so an operator sees what the file will claim about itself
    # while there is still a chance to stop — a wrong window is not visible in the output.
    print(f"window      {t0} .. {t1}   (from {provenance})")
    print(f"run_on      {t1}   — the date the window closed, not today")
    gates = doc["measurement"]["population_gates"]
    missing_gates = [k for k, v in gates.items() if v is None]
    print("gates       " + ", ".join(f"{k}={v}" for k, v in gates.items()))
    if missing_gates:
        print(f"            {len(missing_gates)} gate(s) not recorded by the artifact and "
              f"left null, not guessed: {', '.join(missing_gates)}")
    print(f"kept        {len(plan['keep']):4d} spots -> factors")
    for cat, label in (("mop_tier", "MOP tier (structural)"),
                       ("held_out", "named hold-outs"),
                       ("spread", f"p75/p25 > {a.max_iqr}"),
                       ("unusable", "unusable median"),
                       ("unknown_slug", "slug matches no spot")):
        if plan[cat]:
            print(f"{label:<28} {len(plan[cat]):4d}  {sorted(plan[cat])}")
    if not a.apply:
        print(f"\nDRY RUN — nothing written. Re-run with --apply to write {a.out}")
        return 0
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w") as fh:
        json.dump(doc, fh, indent=2, ensure_ascii=False)
        fh.write("\n")
    print(f"\nwrote {a.out}: {len(doc['factors'])} factors, {len(doc['held_out'])} held out")
    return 0


def run_selftest():
    ok = True

    def check(n, c):
        nonlocal ok
        ok = ok and c
        print(f"  {'PASS' if c else 'FAIL'}  {n}")

    mop = {"a-mop-spot"}
    # THE STATISTIC IS p75/p25 (FACE_FACTOR_MAX_IQR_RATIO = 1.7), STRICTLY greater. Every
    # fixture below carries a DELIBERATELY AWFUL p10/p90 of 1.0/9.9 — a tail ratio of 9.9,
    # four times the old 2.5 threshold — so a fixture that is kept proves the tails no
    # longer decide. 1.3/1.0 = 1.3 -> kept; 1.7 exactly -> kept; 1.71 -> excluded.
    keep = {"slug": "ok", "face_ratio": {"median": 2.87, "n": 334,
                                         "p10": 1.0, "p25": 1.0, "p75": 1.3, "p90": 9.9}}
    check("a clean record is kept", classify(keep, mop)[0] == "keep")
    check("a MOP-tier slug is excluded structurally, before any statistic",
          classify({**keep, "slug": "a-mop-spot"}, mop)[0] == "mop_tier")
    check("a MOP-tier slug is excluded even with a perfect spread",
          classify({"slug": "a-mop-spot",
                    "face_ratio": {"median": 2.0, "n": 999, "p10": 1.0, "p25": 1.0,
                                   "p75": 1.0, "p90": 1.0}},
                   mop)[0] == "mop_tier")
    for slug in ("fort-point", "sandspit", "rincon"):
        check(f"{slug} is held out by name",
              classify({**keep, "slug": slug}, mop)[0] == "held_out")
    check("p75/p25 exactly 1.7 is KEPT (strictly greater excludes)",
          classify({**keep, "face_ratio": {"median": 2.0, "n": 9, "p10": 1.0, "p25": 1.0,
                                           "p75": 1.7, "p90": 9.9}}, mop)[0] == "keep")
    check("a tail ratio of 9.9 does NOT exclude when the core is tight",
          classify(keep, mop)[0] == "keep")
    check("Steamer Lane's real numbers are KEPT (p90/p10 4.42, p75/p25 1.46)",
          classify({"slug": "steamer-lane",
                    "face_ratio": {"median": 2.808, "n": 334, "p10": 0.821, "p25": 2.234,
                                   "p75": 3.256, "p90": 3.630}}, mop)[0] == "keep")
    check("a record with no p25/p75 is unusable, not waved through",
          classify({"slug": "old", "face_ratio": {"median": 2.0, "n": 300,
                                                  "p10": 1.0, "p90": 1.0}},
                   mop)[0] == "unusable")
    check("p75/p25 1.71 is excluded",
          classify({**keep, "face_ratio": {"median": 2.0, "n": 9, "p10": 1.0, "p25": 1.0,
                                           "p75": 1.71, "p90": 9.9}}, mop)[0] == "spread")
    check("rincon's p75/p25 of 2.27 would exclude it on the statistic even if unnamed",
          classify({"slug": "elsewhere",
                    "face_ratio": {"median": 0.62, "n": 300, "p10": 1.0, "p25": 1.0,
                                   "p75": 2.27, "p90": 6.47}}, mop)[0] == "spread")
    # The two spots the rejected 1.6 proposal would have excluded and 1.7 keeps.
    for _slug, _p75 in (("westport", 1.61), ("jug-handle", 1.64)):
        check(f"{_slug} p75/p25 {_p75} is KEPT at 1.7 (1.6 would have excluded it)",
              classify({"slug": _slug,
                        "face_ratio": {"median": 2.0, "n": 300, "p10": 1.0, "p25": 1.0,
                                       "p75": _p75, "p90": 9.9}}, mop)[0] == "keep")
    check("a null median is unusable",
          classify({"slug": "x", "face_ratio": {"median": None}}, mop)[0] == "unusable")
    check("a zero median is unusable",
          classify({"slug": "x", "face_ratio": {"median": 0.0}}, mop)[0] == "unusable")
    check("a negative median is unusable",
          classify({"slug": "x", "face_ratio": {"median": -2.0}}, mop)[0] == "unusable")
    check("a record with no slug is unusable",
          classify({"face_ratio": {"median": 2.0}}, mop)[0] == "unusable")
    check("_spread returns None when p10 is zero",
          _spread({"face_ratio": {"p10": 0.0, "p90": 2.0}}) is None)
    check("_spread computes p90/p10", _spread({"face_ratio": {"p10": 2.0, "p90": 5.0}}) == 2.5)

    # --- the published band survives into the factor record ------------------- #
    # p25/p75 are what face_correction.face_range divides by. Dropping them here would
    # regenerate a file with no band and ship the feature inert with nothing else failing,
    # so the carry-through is pinned end to end on a fixture rather than assumed.
    import json as _json
    import os as _os
    import tempfile as _tf
    _d = _tf.mkdtemp()
    _sp = _os.path.join(_d, "spread.json")
    _ro = _os.path.join(_d, "roster.json")
    with open(_sp, "w") as fh:
        _json.dump({"by_spot": [{"slug": "banded-spot", "face_ratio": {
            "median": 2.0, "n": 300, "p10": 1.2, "p25": 1.6,
            "p75": 2.5, "p90": 3.0}}]}, fh)
    with open(_ro, "w") as fh:
        _json.dump([{"name": "Banded Spot"}], fh)
    _W = ("2026-08-18", "2026-09-01")
    _doc, _plan = build(spread_path=_sp, roster_path=_ro, window=_W)
    _rec = (_doc.get("factors") or {}).get("banded-spot") or {}
    check(f"the band survives build: p25 1.6 ({_rec.get('p25')})", _rec.get("p25") == 1.6)
    check(f"the band survives build: p75 2.5 ({_rec.get('p75')})", _rec.get("p75") == 2.5)
    check("p10/p90 are still carried for the exclusion rule",
          _rec.get("p10") == 1.2 and _rec.get("p90") == 3.0)
    # A spread file predating the p25/p75 measurement now yields NO FACTOR AT ALL. This is
    # a deliberate tightening that came with making p75/p25 the exclusion statistic: the
    # rule cannot judge a record it has no quartiles for, and keeping it would correct a
    # spot whose spread was never measured. Never a p10/p90 substitute either — that would
    # silently compare the wrong ratio against a threshold tuned for a different one.
    # CONSEQUENCE, stated so it is not a surprise: mop_face_validation and this script must
    # be re-run TOGETHER. A stale mop_spread.json produces an empty factor map, loudly.
    with open(_sp, "w") as fh:
        _json.dump({"by_spot": [{"slug": "banded-spot", "face_ratio": {
            "median": 2.0, "n": 300, "p10": 1.2, "p90": 3.0}}]}, fh)
    # validate=False so this proves the CLASSIFICATION still behaves. The artifact guard
    # now aborts on this same file before classification is reached — pinned separately
    # below — but the downstream behaviour must not quietly change underneath it.
    _doc2, _plan2 = build(spread_path=_sp, roster_path=_ro, window=_W, validate=False)
    check("an old spread file yields NO factor, not a p10/p90 stand-in",
          "banded-spot" not in (_doc2.get("factors") or {}))
    check("...and it is recorded as unusable, with the remedy in the reason",
          "banded-spot" in (_doc2.get("held_out") or {})
          and "p25/p75" in (_doc2["held_out"]["banded-spot"].get("reason") or ""))
    check("...and it lands in the unusable bucket of the plan, not the spread one",
          "banded-spot" in (_plan2.get("unusable") or []))
    # ...and with the guard ON it never gets that far: an empty factor map would have
    # overwritten the committed file under --apply.
    try:
        build(spread_path=_sp, roster_path=_ro, window=_W)
        _aborted = False
    except SpreadError as _e:
        _aborted = "p25/p75" in str(_e)
    check("the same old file ABORTS when validated, rather than writing zero factors",
          _aborted)

    # --- dates come from the measurement, never from the clock ---------------- #
    check("no RUN_ON literal survives in this module",
          not hasattr(sys.modules[__name__], "RUN_ON"))
    check("window from the file's own record",
          resolve_window({"window": {"t0": "2026-09-05", "t1": "2026-09-19"}})[:2]
          == ("2026-09-05", "2026-09-19"))
    check("a full ISO timestamp is truncated to its date",
          resolve_window({"window": {"t0": "2026-09-05T00:00:00+00:00",
                                     "t1": "2026-09-19T11:22:33.456+00:00"}})[:2]
          == ("2026-09-05", "2026-09-19"))
    check("the provenance says where the dates came from",
          resolve_window({"window": {"t0": "2026-09-05", "t1": "2026-09-19"}})[2]
          == "the spread file's own window")

    def _raises(fn, needle):
        try:
            fn()
        except SpreadError as e:
            return needle in str(e)
        return False

    check("no window anywhere -> ABORT, not today's date",
          _raises(lambda: resolve_window({}), "records no window"))
    check("...and the message names the flags that would fix it",
          _raises(lambda: resolve_window({}), "--window-t0"))
    check("...and it does NOT silently substitute the clock",
          _raises(lambda: resolve_window({}), "not of when"))
    check("flags supply the window when the file has none",
          resolve_window({}, "2026-09-05", "2026-09-19")[:2] == ("2026-09-05", "2026-09-19"))
    check("...and the provenance says so",
          resolve_window({}, "2026-09-05", "2026-09-19")[2] == "--window-t0/--window-t1")
    check("half a window is an abort",
          _raises(lambda: resolve_window({}, "2026-09-05", None), "given together"))
    check("a malformed flag date is an abort",
          _raises(lambda: resolve_window({}, "05/09/2026", "2026-09-19"), "YYYY-MM-DD"))
    check("file and flags agreeing is fine",
          resolve_window({"window": {"t0": "2026-09-05", "t1": "2026-09-19"}},
                         "2026-09-05", "2026-09-19")[:2] == ("2026-09-05", "2026-09-19"))
    check("file and flags DISAGREEING is an abort, not a precedence rule",
          _raises(lambda: resolve_window({"window": {"t0": "2026-09-05",
                                                     "t1": "2026-09-19"}},
                                         "2026-08-18", "2026-09-01"), "disagreement"))
    check("...and both candidate windows are in the message",
          _raises(lambda: resolve_window({"window": {"t0": "2026-09-05",
                                                     "t1": "2026-09-19"}},
                                         "2026-08-18", "2026-09-01"), "2026-08-18"))

    # --- staleness ------------------------------------------------------------ #
    _FILE_WIN = "the spread file's own window"
    check("generated_at after the window closes is fresh",
          assert_fresh({"generated_at": "2026-09-21T00:00:00+00:00"}, "2026-09-19",
                       _FILE_WIN) == "2026-09-21")
    check("generated_at ON the closing date is fresh (a same-day build is normal)",
          assert_fresh({"generated_at": "2026-09-19T23:00:00+00:00"}, "2026-09-19",
                       _FILE_WIN) == "2026-09-19")
    check("generated_at BEFORE the window closes is stale -> abort",
          _raises(lambda: assert_fresh({"generated_at": "2026-09-03T00:00:00+00:00"},
                                       "2026-09-19", _FILE_WIN), "stale spread file"))
    check("...and the message carries both dates",
          _raises(lambda: assert_fresh({"generated_at": "2026-09-03T00:00:00+00:00"},
                                       "2026-09-19", _FILE_WIN), "2026-09-03"))
    check("a file-derived window with no generated_at cannot be checked -> abort",
          _raises(lambda: assert_fresh({}, "2026-09-19", _FILE_WIN), "no usable generated_at"))
    check("an operator-supplied window with no generated_at is allowed through",
          assert_fresh({}, "2026-09-19", "--window-t0/--window-t1") is None)

    # --- a missing source field stops the run, it does not become a null ------ #
    _full = {"slug": "s", "face_ratio": {"median": 2.0, "n": 300, "p10": 1.0, "p25": 1.2,
                                         "p75": 1.5, "p90": 3.0}}
    check("a complete record passes validation",
          validate_spread_records([_full], set(), FACE_FACTOR_MAX_IQR_RATIO) is None)
    for _missing in ("n", "p10", "p90"):
        _rec2 = {"slug": "s", "face_ratio": {k: v for k, v in _full["face_ratio"].items()
                                             if k != _missing}}
        check(f"a kept record missing {_missing} ABORTS (it would write a null)",
              _raises(lambda r=_rec2: validate_spread_records(
                  [r], set(), FACE_FACTOR_MAX_IQR_RATIO), "missing a face_ratio field"))
        check(f"...and the abort names {_missing}",
              _raises(lambda r=_rec2: validate_spread_records(
                  [r], set(), FACE_FACTOR_MAX_IQR_RATIO), _missing))
    check("an explicit null is as bad as an absent key",
          _raises(lambda: validate_spread_records(
              [{"slug": "s", "face_ratio": {**_full["face_ratio"], "p10": None}}],
              set(), FACE_FACTOR_MAX_IQR_RATIO), "p10"))
    check("a record that would NOT be kept is not held to the keep fields",
          validate_spread_records(
              [{"slug": "s", "face_ratio": {"median": 2.0, "p25": 1.0, "p75": 9.0}}],
              set(), FACE_FACTOR_MAX_IQR_RATIO) is None)
    check("an empty file is not a staleness abort",
          validate_spread_records([], set(), FACE_FACTOR_MAX_IQR_RATIO) is None)

    # --- the population gates are copied, never invented ---------------------- #
    _gates = population_gates({"constants": {"FACE_SHORE_NORMAL_MAX_DELTA": 90.0,
                                             "MATCH_SEPARATION_M": 2200.0,
                                             "is_valid_surf_spot_filter_applied": True}})
    check("gates are copied verbatim from the artifact",
          _gates == {"FACE_SHORE_NORMAL_MAX_DELTA": 90.0, "MATCH_SEPARATION_M": 2200.0,
                     "is_valid_surf_spot_filter_applied": True})
    check("an OLDER artifact's gates are copied as they were, not as they are now",
          population_gates({"constants": {"FACE_SHORE_NORMAL_MAX_DELTA": 35.0}})
          ["FACE_SHORE_NORMAL_MAX_DELTA"] == 35.0)
    check("a gate the artifact does not record is null, not invented",
          population_gates({"constants": {}}) ==
          {k: None for k in GATE_CONSTANT_KEYS})
    check("no constants block at all -> all null, no crash",
          population_gates({}) == {k: None for k in GATE_CONSTANT_KEYS})

    # --- the dates reach every place the file states them --------------------- #
    with open(_sp, "w") as fh:
        _json.dump({"generated_at": "2026-09-21T00:00:00+00:00",
                    "window": {"t0": "2026-09-05", "t1": "2026-09-19"},
                    "constants": {"FACE_SHORE_NORMAL_MAX_DELTA": 90.0,
                                  "MATCH_SEPARATION_M": 2200.0,
                                  "is_valid_surf_spot_filter_applied": True},
                    "by_spot": [{"slug": "banded-spot", "face_ratio": {
                        "median": 2.0, "n": 300, "p10": 1.2, "p25": 1.6,
                        "p75": 2.5, "p90": 3.0}}]}, fh)
    _doc3, _ = build(spread_path=_sp, roster_path=_ro)
    _m3 = _doc3["measurement"]
    check(f"measurement.run_on is the window end ({_m3['run_on']})",
          _m3["run_on"] == "2026-09-19")
    check("measurement.window is the artifact's window",
          _m3["window"] == {"t0": "2026-09-05", "t1": "2026-09-19"})
    check("per-record measured_on is the window end, NOT a 2026-09-01 literal",
          _doc3["factors"]["banded-spot"]["measured_on"] == "2026-09-19")
    check("per-record window matches",
          _doc3["factors"]["banded-spot"]["window"] == {"t0": "2026-09-05",
                                                        "t1": "2026-09-19"})
    check("the gates land in the measurement block",
          _m3["population_gates"]["MATCH_SEPARATION_M"] == 2200.0)
    check("the artifact's own timestamp is carried for the audit trail",
          _m3["spread_artifact_generated_at"] == "2026-09-21T00:00:00+00:00")
    check("generated_at is still the real clock, and differs from run_on",
          _m3["generated_at"][:4] >= "2026" and _m3["generated_at"] != _m3["run_on"])
    print()
    print("selftest: " + ("ALL PASS" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
