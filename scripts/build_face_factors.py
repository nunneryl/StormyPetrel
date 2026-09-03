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
    scripts/mop_spread.json
        by_spot[].slug, .face_ratio.median, .face_ratio.n, .face_ratio.p10, .face_ratio.p90

EXCLUSION RULES, applied in this order and recorded per spot in the output's `held_out`:

  1. MOP TIER — swell_window_source == "cdip_mop". apply_mop_overrides computes those
     spots' face FROM MOP Hs, so the measured ratio is period_factor by construction and
     correcting them would divide MOP by a number derived from MOP. Read from the roster,
     never from a list, so a spot promoted to the MOP tier is excluded automatically.
  2. NAMED HOLD-OUTS — fort-point, sandspit, rincon. Reasons recorded per spot.
  3. SPREAD — face_ratio p90/p10 > FACE_FACTOR_MAX_SPREAD (2.5). Above that the median is
     not describing a stable offset, it is the centre of a cloud.
  4. UNUSABLE — a missing, non-numeric or non-positive median.

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

from pipeline.config import FACE_FACTOR_MAX_SPREAD                   # noqa: E402
from pipeline.enrich import _slug_for                                # noqa: E402
from pipeline.forecast.face_correction import MOP_TIER_SOURCES       # noqa: E402

SPREAD_PATH = os.path.join(HERE, "mop_spread.json")
ROSTER = os.path.join(ROOT, "pipeline", "spots_enriched.json")
OUT = os.path.join(ROOT, "pipeline", "data", "spot_face_factors.json")

# The measurement this file describes. Stated here, written into every record, so a later
# reader can tell a measured factor from a guessed one without archaeology.
SOURCE = "scripts/mop_face_validation.py"
RUN_ON = "2026-09-01"
WINDOW_T0 = "2026-08-18"
WINDOW_T1 = "2026-09-01"

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

_COMMENT = (
    "Per-spot multiplicative corrections for face_ft, measured against CDIP MOP. A factor is "
    "a DIVISOR: corrected_face = published_face / factor, so 2.87 means we publish 2.87x "
    "what MOP implies. Applied by pipeline/forecast/face_correction.py at the single seam in "
    "interpret.main AFTER apply_mop_overrides and apply_nwps_overrides, which is where the "
    "four face_ft producers actually converge. Both face_ft and effective_size_ft are scaled "
    "and stars is recomputed by the production interpret.composite_stars.\n\n"

    "GENERATED, NOT TRANSCRIBED. Regenerate with `python3 scripts/build_face_factors.py "
    "--apply` from scripts/mop_spread.json (Mac-local, gitignored). Do not hand-edit: an "
    "edit here cannot be traced back to a measurement, and every exclusion below was applied "
    "mechanically.\n\n"

    "THESE ARE SEASONAL AND THAT IS NOT A CAVEAT, IT IS THE MAIN LIMITATION. Every factor "
    "was measured over 14 days of SUMMER (2026-08-18 to 2026-09-01). California's swell "
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


def _spread(rec):
    """p90 / p10, or None when either bound is missing or p10 is non-positive."""
    fr = rec.get("face_ratio") or {}
    p10, p90 = fr.get("p10"), fr.get("p90")
    try:
        p10, p90 = float(p10), float(p90)
    except (TypeError, ValueError):
        return None
    if p10 <= 0.0:
        return None
    return p90 / p10


def classify(rec, mop_tier_slugs, max_spread=FACE_FACTOR_MAX_SPREAD):
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
    sp = _spread(rec)
    if sp is not None and sp > max_spread:
        return "spread", (f"within-spot p90/p10 spread {sp:.2f} exceeds {max_spread} — the "
                          f"median is the centre of a cloud, not a stable offset")
    return "keep", None


def build(spread_path=SPREAD_PATH, roster_path=ROSTER, max_spread=FACE_FACTOR_MAX_SPREAD,
          today=None):
    """(document, plan) — the file to write and a per-category slug listing."""
    doc_in = json.load(open(spread_path))
    by_spot = doc_in.get("by_spot")
    if not isinstance(by_spot, list):
        raise ValueError(f"{spread_path}: expected a top-level 'by_spot' list")
    roster = json.load(open(roster_path))
    mop_tier_slugs = {_slug_for(s.get("name")) for s in roster
                      if s.get("swell_window_source") in MOP_TIER_SOURCES}
    known = {_slug_for(s.get("name")) for s in roster}
    measured_on = today or RUN_ON

    factors, held, plan = {}, {}, {"keep": [], "mop_tier": [], "held_out": [],
                                   "spread": [], "unusable": [], "unknown_slug": []}
    for rec in by_spot:
        if not isinstance(rec, dict):
            continue
        slug = rec.get("slug")
        verdict, detail = classify(rec, mop_tier_slugs, max_spread)
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
                "window": {"t0": WINDOW_T0, "t1": WINDOW_T1},
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
                "window": {"t0": WINDOW_T0, "t1": WINDOW_T1},
                "source": SOURCE,
            }
    doc = {
        "_comment": _COMMENT,
        "_schema_version": 1,
        "measurement": {
            "source": SOURCE,
            "run_on": RUN_ON,
            "window": {"t0": WINDOW_T0, "t1": WINDOW_T1},
            "reference": "CDIP MOP alongshore nowcast at the 10 m contour",
            "statistic": "median of face_ft / (MOP Hs * 3.281) over the joined hours",
            "published_band": "p25/p75 of the same ratio; lo = face/p75, hi = face/p25",
            "regenerate": "python3 scripts/mop_face_validation.py"
                          " && python3 scripts/build_face_factors.py --apply",
            "max_spread_p90_p10": max_spread,
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
    ap.add_argument("--max-spread", type=float, default=FACE_FACTOR_MAX_SPREAD,
                    help=f"p90/p10 exclusion threshold (default {FACE_FACTOR_MAX_SPREAD})")
    ap.add_argument("--apply", action="store_true", help="write the file (default: dry run)")
    ap.add_argument("--selftest", action="store_true", help="offline logic proof")
    a = ap.parse_args(argv)
    if a.selftest:
        return run_selftest()
    if not os.path.exists(a.spread):
        print(f"no MOP spread artifact at {a.spread}\n"
              "  It is Mac-local and gitignored. Produce it with the MOP validation harness "
              "first.", file=sys.stderr)
        return 2
    doc, plan = build(a.spread, max_spread=a.max_spread)
    print(f"kept        {len(plan['keep']):4d} spots -> factors")
    for cat, label in (("mop_tier", "MOP tier (structural)"),
                       ("held_out", "named hold-outs"),
                       ("spread", f"p90/p10 > {a.max_spread}"),
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
    # spread 3.0/1.0 = 3.0 > 2.5 -> excluded; 2.0/1.0 = 2.0 -> kept; 2.5/1.0 = 2.5 -> kept
    # (the rule is STRICTLY greater, so a spot exactly at the threshold survives).
    keep = {"slug": "ok", "face_ratio": {"median": 2.87, "n": 334, "p10": 1.0, "p90": 2.0}}
    check("a clean record is kept", classify(keep, mop)[0] == "keep")
    check("a MOP-tier slug is excluded structurally, before any statistic",
          classify({**keep, "slug": "a-mop-spot"}, mop)[0] == "mop_tier")
    check("a MOP-tier slug is excluded even with a perfect spread",
          classify({"slug": "a-mop-spot",
                    "face_ratio": {"median": 2.0, "n": 999, "p10": 1.0, "p90": 1.0}},
                   mop)[0] == "mop_tier")
    for slug in ("fort-point", "sandspit", "rincon"):
        check(f"{slug} is held out by name",
              classify({**keep, "slug": slug}, mop)[0] == "held_out")
    check("spread exactly 2.5 is KEPT (strictly greater excludes)",
          classify({**keep, "face_ratio": {"median": 2.0, "n": 9, "p10": 1.0, "p90": 2.5}},
                   mop)[0] == "keep")
    check("spread 2.51 is excluded",
          classify({**keep, "face_ratio": {"median": 2.0, "n": 9, "p10": 1.0, "p90": 2.51}},
                   mop)[0] == "spread")
    check("spread 6.47 (rincon's) would be excluded on spread even if unnamed",
          classify({"slug": "elsewhere",
                    "face_ratio": {"median": 0.62, "n": 300, "p10": 1.0, "p90": 6.47}},
                   mop)[0] == "spread")
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
    _doc, _plan = build(spread_path=_sp, roster_path=_ro)
    _rec = (_doc.get("factors") or {}).get("banded-spot") or {}
    check(f"the band survives build: p25 1.6 ({_rec.get('p25')})", _rec.get("p25") == 1.6)
    check(f"the band survives build: p75 2.5 ({_rec.get('p75')})", _rec.get("p75") == 2.5)
    check("p10/p90 are still carried for the exclusion rule",
          _rec.get("p10") == 1.2 and _rec.get("p90") == 3.0)
    # A spread file predating the p25/p75 measurement must yield a factor with NO band —
    # never a p10/p90 substitute, which would silently double the published width.
    with open(_sp, "w") as fh:
        _json.dump({"by_spot": [{"slug": "banded-spot", "face_ratio": {
            "median": 2.0, "n": 300, "p10": 1.2, "p90": 3.0}}]}, fh)
    _doc2, _ = build(spread_path=_sp, roster_path=_ro)
    _rec2 = (_doc2.get("factors") or {}).get("banded-spot") or {}
    check("an old spread file yields a factor with no band, not a p10/p90 stand-in",
          _rec2.get("p25") is None and _rec2.get("p75") is None)
    check("...and that factor is still usable for the point estimate",
          _rec2.get("factor") == 2.0)
    print()
    print("selftest: " + ("ALL PASS" if ok else "FAILURES ABOVE"))
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
