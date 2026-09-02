"""Per-spot multiplicative correction of face_ft against CDIP MOP, and the star recompute.

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

HOW THE STARS ARE RECOMPUTED, AND WHY THIS FORM. Both face_ft and effective_size_ft are
scaled by the same 1/factor, then stars is recomputed by calling the PRODUCTION
interpret.composite_stars on the corrected effective size with the row's own four quality
factors unchanged. Nothing is reimplemented.

  Scaling the STORED effective_size_ft, rather than recomputing it as corrected_face x
  dir_gain, is deliberate and is the only branch-independent form. The three producers do
  not agree on how effective relates to face:

      nwps_stars      nwps_nearshore.py:804   eff = face * dg
      mop_stars       mop.py:165              eff = face * dg
      rate_spot       interpret.py:1425-1428  eff = fft * dg  on the NWPS path
                                              eff = fft       on the WW3 path, because
                                              combine_ww3_partitions already weighted by
                                              gain inside the RMS sum and multiplying again
                                              would double-count

  Effective is LINEAR in face under all three, so dividing the stored effective by the
  factor is exact for every one of them and needs no guess about which producer wrote the
  row. Recomputing as face x dir_gain would be WRONG on the WW3 path.

  (The brief states that effective_size_ft is not face x dir_gain and that dir_gain
  participates through the geometric mean. That is not what the code does — composite_stars
  takes no dir_gain argument, and all four call sites pass exactly
  (eff, wind_mult, tide_mult, chop_mult, period_quality). The conclusion the brief draws
  from it is right anyway, for a different reason: effective_size_ft is a separately
  persisted column computed upstream, so correcting the display leaves it stale. Scaling
  the stored effective is correct under either account, which is why it is used here.)

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

SEASONALITY. See the data file's own header. These factors were measured over 14 summer
days and are not validated across a season change; the run summary warns past
FACE_FACTOR_MAX_AGE_DAYS.
"""
from __future__ import annotations

import datetime
import json
import logging

from ..config import (
    FACE_FACTOR_MAX_AGE_DAYS,
    SPOT_FACE_FACTORS_FILE,
)
from ..interpret import composite_stars

log = logging.getLogger("pipeline.forecast.face_correction")

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
    """Scale face_ft (and effective_size_ft) by 1/factor and recompute stars, in place.

    Mutates *ratings*. Returns a stats dict for the run summary. Every spot without a
    factor, on the MOP tier, or held out is left BYTE-IDENTICAL — no key is written, not
    even an unchanged one.
    """
    if slug_for is None:
        from ..enrich import _slug_for as slug_for      # noqa: PLC0415 - avoids a cycle
    if factors is None:
        factors = load_face_factors()
    if not factors:
        return {"corrected_spots": 0, "corrected_hours": 0, "no_factor": len(spots),
                "mop_tier_skipped": 0, "unrateable_hours": 0, "stale": None}

    validate_factor_slugs(factors, spots, slug_for)

    now = now or datetime.date.today()
    stale = _stalest(factors, now)
    if stale and stale[0] > FACE_FACTOR_MAX_AGE_DAYS:
        log.warning(
            "face factors are %d days old (oldest: %s), past the %d-day limit. They were "
            "measured over 14 SUMMER days and are NOT validated across a season change — a "
            "factor absorbing period-dependent refraction will misfit when the period "
            "regime moves. Re-measure: %s (file: %s). Treat the first winter re-measurement "
            "as a TEST of whether one constant per spot holds at all, not as maintenance.",
            stale[0], stale[1], FACE_FACTOR_MAX_AGE_DAYS, REGEN_COMMAND,
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
            eff = e.get("effective_size_ft")
            if face is None or eff is None:
                unrateable += 1
                continue
            new_eff = float(eff) / factor
            # THE PRODUCTION STAR CHAIN, called not copied. composite_stars owns the
            # size_score curve, the weighted geometric mean, the half-star snap, the [1,5]
            # clamp and the sub-0.5 ft cutoff; a local copy would drift from any of them
            # silently. The four quality factors are the row's own, unchanged — the only
            # input that moves is the size.
            e["stars"] = composite_stars(
                new_eff,
                e.get("wind_mult", 1.0), e.get("tide_mult", 1.0),
                e.get("chop_mult", 1.0), e.get("period_quality", 1.0),
            )
            e["face_ft"] = round(float(face) / factor, 2)
            e["effective_size_ft"] = round(new_eff, 2)
            touched += 1
        if touched:
            corrected_spots += 1
            corrected_hours += touched
    return {"corrected_spots": corrected_spots, "corrected_hours": corrected_hours,
            "no_factor": no_factor, "mop_tier_skipped": mop_skipped,
            "unrateable_hours": unrateable,
            "stale": {"days": stale[0], "slug": stale[1]} if stale else None}
