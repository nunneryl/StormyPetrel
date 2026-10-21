"""Per-spot multiplicative correction of the DISPLAYED face_ft against CDIP MOP. Display only.

THE MEASUREMENT. For each California spot on the NWPS tier, the MOP validation harness
measured face_ft / (MOP Hs x 3.281) over ~334 joined hours. The median of that ratio is the
spot's FACTOR, and it is a DIVISOR: corrected_face = published_face / factor. A factor of
2.87 means we publish 2.87x what MOP implies. Within-spot p90/p10 spread has median 1.52,
i.e. a residual of about -18% / +23% after correction — near the 15-25% observer-noise floor.

WHERE THIS RUNS, AND WHY NOT WHERE IT WAS ASKED FOR. The brief said "a single seam in
interpret.rate_spot, after all four producers have converged on rated['face_ft']". That
point does not exist inside rate_spot: two of the four producers run AFTER it returns.
interpret.main is ordered

    1866   ratings = compute_ratings(...)      -> rate_spot writes face_ft (producers 2 + 3)
    1875   apply_mop_overrides(ratings, spots) -> overwrites face_ft   (producer 4)
    1892   apply_nwps_overrides(ratings, spots)-> overwrites face_ft   (producer 1)

so a seam inside rate_spot would be overwritten by both overrides on every hour they cover,
producing exactly the corrected/uncorrected adjacent-hour jump the single-seam design exists
to prevent. The convergence point is in main, after line 1892 — which is where this runs.
The intent is honoured; the address was wrong.

THE SEAM IS DISPLAY-ONLY: IT DIVIDES THE HEIGHT, NOT THE RATING. The seam writes face_ft (the
corrected height) and nothing else of the rating's. effective_size_ft and stars are left
EXACTLY as the producer computed them. Nothing here calls
composite_stars, so there is no star chain to keep in step with interpret.

  THE RATERS READ THE SAME FACTORS, since 2026-10-21 (rating-preview run 32). Each producer
  sizes a calibrated spot on face x M / F (interpret.rule_keywords; M = median_face_factor,
  F = spot_factor), and its beach-break ceiling reads face / F. An uncalibrated spot is rated
  exactly as before. So a corrected spot and an uncorrected one with identical inputs publish
  identical effective sizes but different stars, unless F = M. interpret.main reads the file
  once and hands the same dict to the raters and to this seam.

  WHY, AND WHAT IT REPLACED. FACE_CORRECTION_VERSION 1 also divided effective_size_ft by the
  factor and re-rated stars from the divided size. That rated the calibrated spots on a
  different yardstick from every other spot. The factor corrects the HEIGHT WE SHOW against
  MOP's significant wave height; the size curve and the quality weights were set before the
  correction existed, on the model's own size input, and every other spot is still rated on
  that input. Dividing it at the calibrated spots alone made them rate lower in the same
  swell. Measured on the published window on 2026-10-04 (kg_rating_diag, variant E):
  calibrated spots had 0.4% of daylight hours at 3+ stars, against 9.1% at uncalibrated
  spots, and 10.3% when rated from the undivided size. No other spot changes.

  WHAT THE COLUMNS MEAN AT A CORRECTED SPOT. face_ft is the calibrated height;
  effective_size_ft is the producer's effective face on the model's scale, as everywhere else
  (the rating sizes it at x M / F, which is not stored), so it no longer follows face_ft by
  the producer's rule (eff = face * dir_gain on the NWPS and
  MOP paths, eff = face on the WW3 path). face_ft_raw is the face that rule applies to, and
  it means exactly what it meant under version 1: the face before anything divided it.

WHAT IS EXCLUDED, AND HOW STRUCTURALLY.

  MOP TIER — by swell_window_source, never by list. apply_mop_overrides computes those
  spots' face FROM MOP Hs, so their measured ratio is period_factor by construction and
  correcting them would divide MOP by a number derived from MOP. The test is
  `spot.get("swell_window_source") in MOP_TIER_SOURCES`, which apply_mop_assignments sets
  when it promotes a spot — so a spot promoted to the MOP tier tomorrow is excluded
  tomorrow, with no edit here and no entry to remove from a list.

  HELD OUT — recorded per spot in the data file with a reason, and simply absent from the
  `factors` map, so this module cannot correct them however it is called.

  NO FACTOR — 511 spots have no MOP coverage. Absent is not 1.0: the lookup returns None
  and the arithmetic does not execute. `.get(slug, 1.0)` would make a typo'd slug
  indistinguishable from a deliberate omission, and would assert "this spot needs no
  correction" where the truth is "this spot was never measured".

SEASONALITY. See the data file's own header. Each file's factors come from a single
measurement window (its measurement.window) and are not validated across a season change;
the run summary warns past FACE_FACTOR_MAX_AGE_DAYS.

THE RANGE THE SITE SHOWS IS NOT MADE HERE. Until 2026-10-21 this seam also wrote face_lo_ft /
face_hi_ft, the corrected face divided by the factor record's p75 and p25: a band at the 141
calibrated spots and nothing at the rest, so a range meant two different things. The site now
prints the step of one fixed ladder that face_ft falls in (frontend/lib/formatting.ts,
fmtHeightRange), at every spot alike, and db_import writes NULL to both columns (migration
023; 024 drops them). The record's p25/p75 stay in the factor file: build_face_factors gates
on p75/p25 (FACE_FACTOR_MAX_IQR_RATIO) and diff_face_factors watches them drift. Nothing here
reads them any more.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import logging

from ..config import (
    FACE_FACTOR_MAX_AGE_DAYS,
    SPOT_FACE_FACTORS_FILE,
)

log = logging.getLogger("pipeline.forecast.face_correction")

# THE SEAM'S ARITHMETIC VERSION. Bump this when what the seam DOES to a row changes — the
# divisor form, what it does to face_ft. Do NOT bump it when the factor file
# is regenerated: that moves the fingerprint instead, and the two are deliberately separate
# because they mean different things to a reader of the stamp. See face_correction_stamp.
#
#   1  divided face_ft AND effective_size_ft, and re-rated stars from the divided size
#   2  divides face_ft only; effective_size_ft and stars are the producer's
#
# face_ft_raw means the same under both: the face before anything divided it. Version 2 also
# wrote the p25/p75 band until 2026-10-21. Dropping it did NOT bump the version: face_ft and
# face_ft_raw are unchanged, and migration 017 defines <code> as the face ARITHMETIC, so a MOP
# measurement spanning that date is still one measurement.
FACE_CORRECTION_VERSION = 2

# The two provenance keys the seam writes on every rated hour. Named once so the tests, the
# db_import record and the "which fields did the seam leave alone" checks all agree on the
# set rather than each carrying its own copy.
PROVENANCE_KEYS = ("face_ft_raw", "face_correction_version")

# Tiers whose face is computed FROM MOP. Structural exclusion — see the module docstring.
# apply_mop_assignments writes swell_window_source="cdip_mop" when it promotes a spot, so
# membership here is what a future promotion changes, not a list in this file.
MOP_TIER_SOURCES = frozenset({"cdip_mop"})

# The command that regenerates the data file. Named in the staleness warning so the log line
# carries its own remedy rather than pointing at a doc.
REGEN_COMMAND = "python3 scripts/build_face_factors.py --apply"


class FaceFactorSlugError(ValueError):
    """A slug in spot_face_factors.json matches no spot in the roster.

    Raised at startup rather than skipped. A slug that resolves to nothing is silently a
    no-op — indistinguishable from a spot deliberately left out — and the whole point of
    requiring an explicit entry is that an omission and a typo must not look alike. This is
    the one failure this module treats as fatal: a mis-keyed factor means the spot we
    believe we are correcting is not being corrected, and the run would otherwise report
    success.
    """


def load_face_factors(path=None):
    """{slug: record} from the committed factors file; {} when it is absent.

    The path is resolved from the module global INSIDE the body, not bound as a default
    argument. A default is evaluated once at import, which would make the location
    permanently unredirectable — no test could point it at a fixture, and the end-to-end
    seam test found exactly that. Mirrors enrich._load_spot_swell_windows, which resolves
    its path the same way for the same reason.

    An absent file is the no-op default and is NOT an error: the correction is opt-in, the
    file is generated from a Mac-local analysis artifact, and a checkout without it must
    still rate normally. A CORRUPT file is different — it means someone intended factors and
    they are unreadable — so that logs a warning and also degrades to {}, matching
    _load_spot_swell_windows' posture rather than killing the run.

    Records with a missing, non-numeric or non-positive factor are dropped and counted; a
    zero or negative divisor is not a correction, it is a bug that would produce infinities
    or negative faces.
    """
    path = path or SPOT_FACE_FACTORS_FILE
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text())
    except json.JSONDecodeError as e:
        log.warning("face factors file %s corrupt (%s); ignoring", path, e)
        return {}
    entries = doc.get("factors") or {}
    out: dict[str, dict] = {}
    for slug, rec in entries.items():
        if not isinstance(rec, dict):
            continue
        try:
            f = float(rec["factor"])
        except (KeyError, TypeError, ValueError):
            log.warning("face factor for slug %r missing or invalid; skipping", slug)
            continue
        if not (f > 0.0):
            log.warning("face factor for slug %r is %r, not a positive divisor; skipping",
                        slug, rec.get("factor"))
            continue
        out[slug] = {**rec, "factor": f}
    return out


def face_correction_stamp(factors):
    """The per-row provenance string: "<code>:<measured_on>:<fingerprint>".

    IDENTIFIES THE RUN, NOT THE OUTCOME. Every row the seam sees gets the same stamp —
    corrected, uncorrected, MOP-tier alike — because the question it has to answer is "were
    these two rows produced by the same correction regime", and a stamp that varied with the
    outcome would answer a different one. Whether an individual row was corrected is already
    recoverable: face_ft_raw != face_ft.

    THE THREE PARTS, and why not one hash of everything:

      <code>         FACE_CORRECTION_VERSION. Changes when the ARITHMETIC changes. This is
                     the part that invalidates a measurement even when face_ft_raw is
                     present, because raw is only pre-correction with respect to a seam that
                     behaves as this one does.
      <measured_on>  the newest measured_on across the factor records — human-readable, so a
                     stamp can be read without a lookup table. "none" when there are none.
      <fingerprint>  8 hex of sha256 over the sorted "slug=factor" pairs. A regeneration that
                     moves any divisor changes it; one that moves none does not, which is the
                     behaviour a fingerprint over the file bytes would NOT give (the file
                     carries generated_at and would differ on every run). "none" when empty.

    Formatted %.10g rather than repr so the fingerprint is stable across Python versions and
    across a float that round-trips differently, and so it does not depend on how json
    happened to parse the literal.

    A worked value: {"a": {"factor": 2.0}} with measured_on 2026-09-01 gives
    "1:2026-09-01:" + sha256("a=2")[:8].
    """
    if not factors:
        return f"{FACE_CORRECTION_VERSION}:none:none"
    lines = "\n".join(f"{slug}={float(rec['factor']):.10g}"
                      for slug, rec in sorted(factors.items()))
    fingerprint = hashlib.sha256(lines.encode("utf-8")).hexdigest()[:8]
    dates = sorted(str(rec.get("measured_on")) for rec in factors.values()
                   if rec.get("measured_on"))
    return f"{FACE_CORRECTION_VERSION}:{dates[-1] if dates else 'none'}:{fingerprint}"


def stamp_provenance(ratings, stamp):
    """Write face_ft_raw + face_correction_version on every hour, BEFORE anything divides.

    THIS IS THE WHOLE POINT OF THE CHANGE, so it is one pass over everything rather than a
    line in each of the seam's four branches. A per-branch stamp is a thing a fifth branch
    forgets, and the forgetting is silent — the row would carry a raw face equal to a
    corrected one and read as clean. Here the invariant "raw is the pre-correction face" is
    established once, for all rows, before the divisor is even looked up, so no later branch
    can violate it except by writing face_ft_raw again, which nothing does.

    face_ft_raw is a VERBATIM copy of face_ft, not a rounded or re-derived one. Whatever
    rounding the upstream producer applied is preserved, which makes the column directly
    comparable to the face_ft of a row written before the correction existed — the harness
    can fall back across the migration boundary without a units or precision seam.

    An hour with no face gets None, exactly as face_ft holds None. That is the honest value:
    the hour is unrateable, not "raw zero".
    """
    for entries in ratings.values():
        for e in entries:
            e["face_ft_raw"] = e.get("face_ft")
            e["face_correction_version"] = stamp


def validate_factor_slugs(factors, spots, slug_for):
    """Raise FaceFactorSlugError if any slug in *factors* matches no spot.

    Called before a single row is touched, so a mis-keyed file fails the run rather than
    silently correcting 131 spots and quietly not correcting the 132nd.
    """
    known = {slug_for(s.get("name")) for s in spots}
    missing = sorted(set(factors) - known)
    if missing:
        raise FaceFactorSlugError(
            f"{len(missing)} slug(s) in {SPOT_FACE_FACTORS_FILE} match no spot in the "
            f"roster: {missing}. A slug that resolves to nothing is a silent no-op — the "
            f"spot you believe is corrected is not. Fix the slug or remove the entry, then "
            f"regenerate with `{REGEN_COMMAND}`."
        )


def spot_factor(spot, factors, slug_for):
    """The divisor apply_face_corrections uses at *spot*, or None where it corrects nothing.

    The same two exclusions: the MOP tier by swell_window_source, and a spot with no record
    (held out, or never measured). ABSENT IS NOT 1.0 here either. The proposed beach-break
    ceiling (interpret.closeout_factor) reads the calibrated face, face / this, where it is
    not None, and the raw face where it is.
    """
    if spot.get("swell_window_source") in MOP_TIER_SOURCES:
        return None
    rec = factors.get(slug_for(spot.get("name")))
    return None if rec is None else rec["factor"]


def median_face_factor(factors):
    """M: the median factor over *factors* ({slug: record}), or None when there are none.

    The yardstick the rating sizes calibrated spots on (interpret.rule_keywords): a spot with
    factor F is sized at face x M / F. 1.5907 in the file measured 2026-09-08 to 2026-10-06;
    it moves when the file is regenerated, which is the point: one number, from one file,
    read once per run."""
    vals = sorted(rec["factor"] for rec in factors.values())
    if not vals:
        return None
    mid = len(vals) // 2
    return vals[mid] if len(vals) % 2 else (vals[mid - 1] + vals[mid]) / 2.0


def _stalest(factors, now):
    """(days, slug) for the oldest measured_on among *factors*, or None.

    Reported as the WORST case rather than a per-spot list: one line naming the oldest is
    actionable, 132 lines are scrolled past.
    """
    worst = None
    for slug, rec in factors.items():
        raw = rec.get("measured_on")
        if not raw:
            continue
        try:
            d = datetime.date.fromisoformat(str(raw))
        except (TypeError, ValueError):
            log.warning("face factor for %r has an unparseable measured_on %r", slug, raw)
            continue
        age = (now - d).days
        if worst is None or age > worst[0]:
            worst = (age, slug)
    return worst


def apply_face_corrections(ratings, spots, factors=None, slug_for=None, now=None):
    """Divide face_ft by the spot's factor, in place. The display only.

    Mutates *ratings*. Returns a stats dict for the run summary.

    effective_size_ft AND stars ARE NEVER WRITTEN, at any spot. They stay exactly as the
    producer computed them, so a corrected spot is rated on the same input as every other
    spot — see the module docstring. At a corrected spot the seam writes face_ft and nothing
    else of the rating's. Every spot without a factor, on the MOP tier, or held out has every
    RATING field left byte-identical — face_ft is not written, not even unchanged.

    THE GUARANTEE USED TO BE "the entry is byte-identical", full stop, and it is now
    "every rating field is byte-identical". The two PROVENANCE_KEYS are written on every
    hour, including those spots'. That is a deliberate narrowing, not an erosion: the
    property worth having is that no ARITHMETIC runs on a spot we never measured, and
    stamping a verbatim copy of face_ft is not arithmetic. Making the stamp conditional
    would defeat its purpose — a NULL face_correction_version has to mean "produced before
    this existed", and it cannot also mean "produced by a run that chose not to correct
    this spot", or the contamination query cannot distinguish the two.
    """
    if slug_for is None:
        from ..enrich import _slug_for as slug_for      # noqa: PLC0415 - avoids a cycle
    if factors is None:
        factors = load_face_factors()

    stamp = face_correction_stamp(factors)
    # VALIDATION STILL RUNS BEFORE ANY MUTATION, stamp included. A mis-keyed file fails the
    # run, and a failed run must leave the ratings exactly as it found them — otherwise the
    # only evidence of how far it got is the exception text.
    if factors:
        validate_factor_slugs(factors, spots, slug_for)

    # BEFORE THE EARLY RETURN, and before the divisor is looked up. A run with no factors
    # file still stamps, because "the seam ran and corrected nothing" is a fact worth
    # recording and is NOT the same fact as "this row predates the seam" — which is what a
    # NULL would say. It is also the state every fresh checkout is in, so leaving it
    # unstamped would make local rows indistinguishable from pre-migration ones.
    stamp_provenance(ratings, stamp)

    if not factors:
        return {"corrected_spots": 0, "corrected_hours": 0, "no_factor": len(spots),
                "mop_tier_skipped": 0, "unrateable_hours": 0, "stale": None,
                "stamp": stamp}

    now = now or datetime.date.today()
    stale = _stalest(factors, now)
    if stale and stale[0] > FACE_FACTOR_MAX_AGE_DAYS:
        # The window comes from the stale record itself: this used to say "14 SUMMER days",
        # which stopped being true when the 28-day 2026-09-08..10-06 file replaced the 14-day one.
        window = factors[stale[1]].get("window") or {}
        measured = (f"over {window['t0']} to {window['t1']}"
                    if window.get("t0") and window.get("t1") else "over a single window")
        log.warning(
            "face factors are %d days old (oldest: %s), past the %d-day limit. They were "
            "measured %s and are NOT validated across a season change — a factor absorbing "
            "period-dependent refraction will misfit when the period regime moves. "
            "Re-measure: %s (file: %s). Treat the first winter re-measurement as a TEST of "
            "whether one constant per spot holds at all, not as maintenance.",
            stale[0], stale[1], FACE_FACTOR_MAX_AGE_DAYS, measured, REGEN_COMMAND,
            SPOT_FACE_FACTORS_FILE,
        )

    by_name = {s.get("name"): s for s in spots}
    corrected_spots = corrected_hours = mop_skipped = unrateable = 0
    no_factor = 0
    for name, entries in ratings.items():
        spot = by_name.get(name)
        if spot is None:
            no_factor += 1
            continue
        # STRUCTURAL MOP EXCLUSION — by tier, not by name. See the module docstring.
        if spot.get("swell_window_source") in MOP_TIER_SOURCES:
            mop_skipped += 1
            continue
        rec = factors.get(slug_for(name))
        if rec is None:
            # ABSENT IS NOT 1.0. No arithmetic runs; the entries are untouched.
            no_factor += 1
            continue
        factor = rec["factor"]
        touched = 0
        for e in entries:
            face = e.get("face_ft")
            if face is None:
                # No face, so nothing to display: the hour is unrateable and left as it is.
                unrateable += 1
                continue
            # THE DISPLAY ONLY. effective_size_ft and stars are not read and not written:
            # they are the producer's, undivided, the input every uncorrected spot is rated
            # on. Version 1 divided effective_size_ft here as well and re-rated stars from
            # it, which rated these spots on a different yardstick — see the module docstring.
            e["face_ft"] = round(float(face) / factor, 2)
            touched += 1
        if touched:
            corrected_spots += 1
            corrected_hours += touched
    return {"corrected_spots": corrected_spots, "corrected_hours": corrected_hours,
            "no_factor": no_factor, "mop_tier_skipped": mop_skipped,
            "unrateable_hours": unrateable,
            "stale": {"days": stale[0], "slug": stale[1]} if stale else None,
            "stamp": stamp}
