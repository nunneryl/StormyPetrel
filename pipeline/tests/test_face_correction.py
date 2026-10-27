"""The per-spot MOP face correction, which divides the displayed height and nothing else.

WHAT IS BEING PINNED. A measured divisor scales the DISPLAYED face_ft (and, since migration
023, nothing else: the p25/p75 band is no longer written), and
leaves effective_size_ft and stars exactly as the producer computed them, so a corrected spot
rates exactly like an uncorrected one with identical inputs (FACE_CORRECTION_VERSION 2;
version 1 also divided effective_size_ft and re-rated stars from it). A spot with no factor,
a spot on the MOP tier, and a held-out spot must all come through BYTE-IDENTICAL — not
"unchanged in value", but with no key written at all, so a future reader cannot mistake an
untouched spot for one that was corrected by 1.0.

WHY BYTE-IDENTICAL MATTERS ENOUGH TO TEST. 511 of 648 spots have no factor. If the no-op
were implemented as `.get(slug, 1.0)` then a mis-keyed slug would be indistinguishable from
a deliberate omission, and the spot you believe is corrected would silently not be. The
explicit None lookup plus validate_factor_slugs is what makes those two states different,
and test_an_unresolvable_slug_fails_loudly is the test that keeps them different.

EVERY EXPECTED VALUE IS A LITERAL, hand-computed with the arithmetic in a comment. None is
produced by calling the function under test.

Run: python -m pipeline.tests.test_face_correction
"""
from __future__ import annotations

import datetime
import json
import logging

from pipeline.config import FACE_FACTOR_MAX_AGE_DAYS
from pipeline.forecast import face_correction as FC

TODAY = datetime.date(2026, 9, 1)


def _rec(factor, measured_on="2026-09-01", **kw):
    return {"factor": factor, "hours": 334, "p10": 2.0, "p90": 3.0,
            "spread_p90_p10": 1.5, "measured_on": measured_on,
            "window": {"t0": "2026-08-18", "t1": "2026-09-01"},
            "source": "scripts/mop_face_validation.py", **kw}


def _spot(name="Steamer Lane", source="nwps"):
    return {"name": name, "swell_window_source": source}


def _entry(face=6.0, eff=6.0, **kw):
    """One rating hour. Neutral quality factors so the geometric mean is exactly 1.0 and
    stars == clamp(round(size_score(eff) * 2) / 2, 1, 5)."""
    return {"valid_time": "2026-09-01T00:00:00Z", "face_ft": face, "effective_size_ft": eff,
            "dir_gain": 1.0, "wind_mult": 1.0, "tide_mult": 1.0, "chop_mult": 1.0,
            "period_quality": 1.0, "stars": 4.0, **kw}


def _slug(name):
    """The production slug rule, imported rather than reimplemented."""
    from pipeline.enrich import _slug_for
    return _slug_for(name)


class _CaptureLog(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


# --------------------------------------------------------------------------- #
# 1 — a spot WITH a factor has its displayed face divided, and its rating kept  #
# --------------------------------------------------------------------------- #

def test_a_factored_spot_has_its_face_divided_and_its_rating_left_alone():
    """The only arithmetic is the display: face 6.0 / 3.0 = 2.0. effective_size_ft and stars
    are the producer's — 6.0 and 4.0 going in, 6.0 and 4.0 coming out — and the raw face is
    the 6.0 the producer wrote.

    Version 1 also divided the size, eff 6.0 / 3.0 = 2.0 -> size_score 2.0 (a knot) -> 2.0
    stars, and this test asserted that; the rating no longer moves."""
    ratings = {"Steamer Lane": [_entry(face=6.0, eff=6.0)]}
    st = FC.apply_face_corrections(ratings, [_spot()],
                                   factors={"steamer-lane": _rec(3.0)},
                                   slug_for=_slug, now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["face_ft"] == 2.0, e["face_ft"]
    assert e["face_ft_raw"] == 6.0, e["face_ft_raw"]
    assert e["effective_size_ft"] == 6.0, e["effective_size_ft"]
    assert e["stars"] == 4.0, e["stars"]
    assert st["corrected_spots"] == 1 and st["corrected_hours"] == 1, st


def test_a_corrected_spot_rates_exactly_like_an_uncorrected_one_with_identical_inputs():
    """THE DECISION, at the seam. Three spots, one identical hour each, and a factor for two
    of them: Steamer Lane (corrected), Ocean Beach (no factor) and a MOP-tier spot (given a
    factor on purpose — its height is MOP's own, so even a factor must not touch it).

    Realistic, non-neutral inputs: an NWPS-path hour with face 7.71, dir_gain 0.7, so
    eff = round(7.71 * 0.7, 2) = 5.4, and a 3.0-star rating under wind 0.8 / chop 0.85.

        Steamer Lane   face 7.71 / 2.8084 = 2.74533... -> 2.75   (its factor in the 2026-09-01 file)
        everything else in the hour, the rating included, identical on all three spots
    """
    hour = _entry(face=7.71, eff=5.4, dir_gain=0.7, wind_mult=0.8, chop_mult=0.85,
                  period_quality=0.9, stars=3.0)
    ratings = {"Steamer Lane": [dict(hour)], "Ocean Beach": [dict(hour)],
               "Moss Landing": [dict(hour)]}
    st = FC.apply_face_corrections(
        ratings, [_spot(), _spot("Ocean Beach"), _spot("Moss Landing", source="cdip_mop")],
        factors={"steamer-lane": _rec(2.8084), "moss-landing": _rec(3.0)},
        slug_for=_slug, now=TODAY)
    cal, unc, mop = (ratings[n][0] for n in ("Steamer Lane", "Ocean Beach", "Moss Landing"))
    assert cal["face_ft"] == 2.75, cal["face_ft"]                     # the height shown: divided
    assert (cal["effective_size_ft"], cal["stars"]) == (5.4, 3.0), cal  # the rating: undivided
    # ...and identical to the uncorrected spot's, field for field, but for the height shown.
    assert {k: v for k, v in cal.items() if k != "face_ft"} == \
        {k: v for k, v in unc.items() if k != "face_ft"}, (cal, unc)
    assert_untouched(unc, hour)                                        # uncorrected: unchanged
    assert_untouched(mop, hour)                                        # MOP tier: unchanged
    assert cal["face_ft_raw"] == unc["face_ft_raw"] == mop["face_ft_raw"] == 7.71
    assert st["corrected_spots"] == 1 and st["mop_tier_skipped"] == 1 and st["no_factor"] == 1, st


def test_the_seam_never_re_rates_a_corrected_hour():
    """A sentinel, because a recompute from the UNDIVIDED size would pass every consistent
    fixture: composite_stars can never return 4.2 (it snaps to whole and half stars), and
    7.77 is not a size any fixture's face implies. Both must come out exactly as they went in.

    And the star chain is not imported at all: re-rating here would have to be added back
    deliberately, past the module docstring that says why it was taken out."""
    ratings = {"Steamer Lane": [_entry(face=6.0, eff=7.77, stars=4.2)]}
    FC.apply_face_corrections(ratings, [_spot()], factors={"steamer-lane": _rec(2.0)},
                              slug_for=_slug, now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["face_ft"] == 3.0, e
    assert e["effective_size_ft"] == 7.77 and e["stars"] == 4.2, e
    assert not hasattr(FC, "composite_stars"), "the seam must not import the star chain"


def test_the_four_quality_factors_are_passed_through_unchanged():
    """Nothing in the rating moves: wind/tide/chop/period, the size input and the stars are
    the row's own. The entry's 4.0 stars stay 4.0 — version 1 re-rated this hour to 2.5 from
    eff 8.0 / 2.0 = 4.0 (size_score 3.0 x 0.8 = 2.4 -> 2.5)."""
    ratings = {"Steamer Lane": [_entry(face=8.0, eff=8.0, wind_mult=0.8, tide_mult=0.8,
                                       chop_mult=0.8, period_quality=0.8)]}
    FC.apply_face_corrections(ratings, [_spot()], factors={"steamer-lane": _rec(2.0)},
                              slug_for=_slug, now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["face_ft"] == 4.0, e["face_ft"]
    assert e["effective_size_ft"] == 8.0 and e["stars"] == 4.0, e
    assert (e["wind_mult"], e["tide_mult"], e["chop_mult"], e["period_quality"]) \
        == (0.8, 0.8, 0.8, 0.8)


def test_a_factor_below_one_makes_the_face_bigger():
    """Rincon's 0.62 shape — held out in the shipped file, but the arithmetic must be right
    for whatever a future file contains.

        face 2.0 / 0.5 = 4.0; eff stays 2.0 and stars stays the entry's 4.0
    """
    ratings = {"Steamer Lane": [_entry(face=2.0, eff=2.0)]}
    FC.apply_face_corrections(ratings, [_spot()], factors={"steamer-lane": _rec(0.5)},
                              slug_for=_slug, now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["face_ft"] == 4.0 and e["effective_size_ft"] == 2.0 and e["stars"] == 4.0, e


def test_a_correction_can_no_longer_make_an_hour_flat():
    """Version 1 could push an hour under composite_stars' 0.5 ft flat cutoff by dividing its
    size: eff 1.0 / 2.87 = 0.348 -> 0.0 stars. The divisor now reaches only the height shown.

        face 1.0 / 2.87 = 0.34843... -> 0.35 shown; eff 1.0 and 1.0 star kept
    """
    ratings = {"Steamer Lane": [_entry(face=1.0, eff=1.0, stars=1.0)]}
    FC.apply_face_corrections(ratings, [_spot()], factors={"steamer-lane": _rec(2.87)},
                              slug_for=_slug, now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["face_ft"] == 0.35, e["face_ft"]
    assert e["effective_size_ft"] == 1.0 and e["stars"] == 1.0, e


# --------------------------------------------------------------------------- #
# 2 — everything without a factor keeps every RATING FIELD byte-identical       #
# --------------------------------------------------------------------------- #
# THE GUARANTEE NARROWED WITH MIGRATION 017 AND THESE TESTS GOT SHARPER, NOT LOOSER.
# It used to be "the entry is byte-identical". The seam now stamps two PROVENANCE keys on
# every hour — face_ft_raw and face_correction_version — including on spots it does not
# correct, because a NULL face_correction_version has to mean "produced before this existed"
# and cannot also mean "produced by a run that chose not to correct this spot".
#
# So each of these now asserts THREE things where it used to assert one:
#   * every rating field unchanged  (unchanged_except_provenance)
#   * face_ft_raw == face_ft        — which is the positive statement that NO DIVISION RAN,
#                                     something whole-entry equality could never say
#   * the stamp is present          — the row is attributable to this run


def unchanged_except_provenance(entry, before):
    """True when *entry* differs from *before* in nothing but the provenance keys.

    Written out here rather than imported from the module under test: a helper that asked
    face_correction which keys to ignore would pass no matter which keys it started
    writing, which is the one thing these tests exist to catch."""
    return {k: v for k, v in entry.items()
            if k not in ("face_ft_raw", "face_correction_version")} == before


def assert_untouched(entry, before):
    assert unchanged_except_provenance(entry, before), entry
    assert entry["face_ft_raw"] == entry["face_ft"], \
        "raw must equal published where nothing divided"
    assert entry["face_ft_raw"] == before["face_ft"], "and must equal the pre-seam value"
    assert isinstance(entry["face_correction_version"], str) and entry["face_correction_version"]

def test_a_spot_with_no_factor_is_byte_identical():
    before = _entry()
    ratings = {"Ocean Beach": [dict(before)]}
    st = FC.apply_face_corrections(ratings, [_spot("Ocean Beach"), _spot("Steamer Lane")],
                                   factors={"steamer-lane": _rec(2.87)},
                                   slug_for=_slug, now=TODAY)
    assert_untouched(ratings["Ocean Beach"][0], before)
    assert st["corrected_spots"] == 0 and st["no_factor"] == 1, st


def test_an_empty_factor_map_touches_nothing_and_short_circuits():
    before = _entry()
    ratings = {"Steamer Lane": [dict(before)]}
    st = FC.apply_face_corrections(ratings, [_spot()], factors={}, slug_for=_slug, now=TODAY)
    assert_untouched(ratings["Steamer Lane"][0], before)
    assert st["corrected_spots"] == 0 and st["corrected_hours"] == 0
    # An inert run still stamps, and its stamp says so rather than going quiet.
    assert st["stamp"] == "2:none:none", st["stamp"]


def test_absent_is_not_one_point_zero():
    """The distinction the design rests on: a spot with no entry must take NO arithmetic
    path, so a mis-keyed slug and a deliberate omission cannot look alike. Pinned by a face
    that raises the moment anything does arithmetic on it: `.get(slug, 1.0)` would divide it
    by 1.0 and trip. (This tripwired face_range until the band was retired; the face itself
    is the one thing every arithmetic path has to touch.)"""
    class _Tripwire:
        def __float__(self):
            raise AssertionError("an unfactored spot's face reached arithmetic")
        __truediv__ = __rtruediv__ = __mul__ = __rmul__ = __round__ = __float__
    face = _Tripwire()
    ratings = {"Ocean Beach": [_entry(face=face)]}
    # Steamer Lane is in the ROSTER (so validation passes) but has no RATINGS, so the only
    # spot iterated is the unfactored one.
    FC.apply_face_corrections(ratings, [_spot("Ocean Beach"), _spot("Steamer Lane")],
                              factors={"steamer-lane": _rec(2.0)},
                              slug_for=_slug, now=TODAY)
    assert ratings["Ocean Beach"][0]["face_ft"] is face, "the face object itself, untouched"


def test_a_rating_for_a_spot_missing_from_the_roster_is_untouched():
    before = _entry()
    ratings = {"Ghost Spot": [dict(before)]}
    st = FC.apply_face_corrections(ratings, [_spot("Steamer Lane")],
                                   factors={"steamer-lane": _rec(2.0)},
                                   slug_for=_slug, now=TODAY)
    assert_untouched(ratings["Ghost Spot"][0], before)
    assert st["corrected_spots"] == 0


def test_an_hour_with_no_face_is_counted_and_one_with_no_effective_size_is_still_shown():
    """Only a missing FACE makes an hour unrateable here: the seam reads no effective size
    since version 2, so a missing one no longer blocks the display correction — and it is
    never filled in. (No producer writes a face without an effective size; this pins what
    the seam does if one ever did.)

        x: face None            -> counted, untouched
        y: face 6.0, eff None   -> shown as 6.0 / 2.0 = 3.0, eff stays None, 4.0 stars kept
        z: face 6.0, eff 6.0    -> shown as 3.0, eff 6.0 and 4.0 stars kept
    """
    ratings = {"Steamer Lane": [
        {"valid_time": "x", "face_ft": None, "effective_size_ft": 6.0, "stars": 4.0},
        {"valid_time": "y", "face_ft": 6.0, "effective_size_ft": None, "stars": 4.0},
        _entry(),
    ]}
    st = FC.apply_face_corrections(ratings, [_spot()], factors={"steamer-lane": _rec(2.0)},
                                   slug_for=_slug, now=TODAY)
    x, y, z = ratings["Steamer Lane"]
    assert (x["face_ft"], x["effective_size_ft"], x["stars"]) == (None, 6.0, 4.0), x
    assert (y["face_ft"], y["effective_size_ft"], y["stars"]) == (3.0, None, 4.0), y
    assert (z["face_ft"], z["effective_size_ft"], z["stars"]) == (3.0, 6.0, 4.0), z
    assert st["unrateable_hours"] == 1 and st["corrected_hours"] == 2, st


# --------------------------------------------------------------------------- #
# 3 — the MOP tier is excluded STRUCTURALLY                                    #
# --------------------------------------------------------------------------- #

def test_a_mop_tier_spot_is_untouched_even_with_a_factor():
    """The exclusion is by swell_window_source, not by a list of names, so a spot promoted
    to the MOP tier is excluded the moment apply_mop_assignments writes the tag."""
    before = _entry()
    ratings = {"Steamer Lane": [dict(before)]}
    st = FC.apply_face_corrections(ratings, [_spot(source="cdip_mop")],
                                   factors={"steamer-lane": _rec(2.87)},
                                   slug_for=_slug, now=TODAY)
    assert_untouched(ratings["Steamer Lane"][0], before)
    assert st["mop_tier_skipped"] == 1 and st["corrected_spots"] == 0, st


def test_the_mop_tier_set_is_the_one_the_promoter_writes():
    """apply_mop_assignments writes swell_window_source="cdip_mop"; MOP_TIER_SOURCES must
    contain exactly that, or the structural exclusion silently stops matching."""
    assert "cdip_mop" in FC.MOP_TIER_SOURCES
    assert "nwps" not in FC.MOP_TIER_SOURCES
    assert "raycast" not in FC.MOP_TIER_SOURCES
    assert "orientation_derived" not in FC.MOP_TIER_SOURCES


def test_a_promoted_spot_needs_no_edit_here():
    """The structural claim, exercised: flip only the tier tag and the same spot with the
    same factor goes from corrected to excluded."""
    f = {"steamer-lane": _rec(2.0)}
    r1 = {"Steamer Lane": [_entry()]}
    FC.apply_face_corrections(r1, [_spot(source="nwps")], factors=f, slug_for=_slug, now=TODAY)
    assert r1["Steamer Lane"][0]["face_ft"] == 3.0
    r2 = {"Steamer Lane": [_entry()]}
    FC.apply_face_corrections(r2, [_spot(source="cdip_mop")], factors=f, slug_for=_slug,
                              now=TODAY)
    assert r2["Steamer Lane"][0]["face_ft"] == 6.0


# --------------------------------------------------------------------------- #
# 4 — held-out spots                                                          #
# --------------------------------------------------------------------------- #

def test_each_held_out_spot_is_absent_from_the_generated_factors():
    """fort-point, sandspit and rincon are excluded by being ABSENT from `factors`, so this
    module cannot correct them however it is called — not by a runtime check that could be
    bypassed."""
    import sys
    sys.path.insert(0, "scripts")
    from build_face_factors import HELD_OUT, classify
    assert sorted(HELD_OUT) == ["fort-point", "rincon", "sandspit"]
    for slug in ("fort-point", "sandspit", "rincon"):
        verdict, reason = classify(
            {"slug": slug, "face_ratio": {"median": 2.5, "n": 300, "p10": 1.0, "p90": 1.2}},
            set())
        assert verdict == "held_out", (slug, verdict)
        assert reason and len(reason) > 40, (slug, reason)


def test_a_held_out_spot_is_byte_identical_when_absent_from_the_map():
    before = _entry()
    ratings = {"Fort Point": [dict(before)]}
    FC.apply_face_corrections(ratings, [_spot("Fort Point"), _spot("Steamer Lane")],
                              factors={"steamer-lane": _rec(2.87)}, slug_for=_slug, now=TODAY)
    assert_untouched(ratings["Fort Point"][0], before)


# --------------------------------------------------------------------------- #
# 5 — the spread rule                                                         #
# --------------------------------------------------------------------------- #

def _fr(p25, p75, median=2.0, n=300, p10=0.5, p90=9.9):
    """A by_spot record whose p25/p75 are the numbers under test. p10/p90 are deliberately
    ABSURD — 0.5 and 9.9, a tail ratio of 19.8 — so any test that still passes is proving
    the tails no longer decide anything. Under the old rule every one of these excluded."""
    return {"slug": "s", "face_ratio": {"median": median, "n": n,
                                        "p10": p10, "p25": p25, "p75": p75, "p90": p90}}


def test_the_spread_rule_excludes_on_p75_over_p25_and_ignores_the_tails():
    """THE STATISTIC CHANGE. Every record here has a p90/p10 of 19.8 — far past the old 2.5
    — and the ones with a tight core are KEPT anyway. That is the whole point: a spot is no
    longer thrown out for a tail no reader sees.

    Hand-computed p75/p25, threshold 1.7, STRICTLY greater:
        1.25 / 1.00 = 1.25  -> keep
        1.70 / 1.00 = 1.70  -> keep    (exactly at the threshold)
        1.71 / 1.00 = 1.71  -> spread
        2.30 / 1.00 = 2.30  -> spread
    """
    import sys
    sys.path.insert(0, "scripts")
    from build_face_factors import classify
    for p75, expect in ((1.25, "keep"), (1.70, "keep"), (1.71, "spread"), (2.30, "spread")):
        v, _ = classify(_fr(1.00, p75), set())
        assert v == expect, (p75, v, expect)


def test_exactly_at_the_threshold_is_KEPT_not_excluded():
    """INCLUSIVE AT THE BOUNDARY, stated rather than left to be discovered. The comparison
    is `> max_iqr`, matching ARC_PRUNE_MAX_OFFSET_DEG and the old spread rule, so a spot at
    exactly 1.7 survives. Pinned from BOTH sides so an accidental >= fails here.
        1.700 / 1.000 = 1.700 exactly -> keep
        3.400 / 2.000 = 1.700 exactly -> keep   (same ratio, different magnitudes)
    """
    import sys
    sys.path.insert(0, "scripts")
    from build_face_factors import classify
    assert classify(_fr(1.0, 1.7), set())[0] == "keep"
    assert classify(_fr(2.0, 3.4), set())[0] == "keep"
    # And one hair above is excluded, so the boundary is real and not a rounding artefact.
    assert classify(_fr(1.0, 1.7001), set())[0] == "spread"


def test_the_twelve_measured_spots_land_where_the_threshold_says():
    """THE POPULATION, pinned per spot with its measured p75/p25 as a literal.

    Measured over the 2026-08-18..09-01 window. The seven above the line and the five below
    it are the cases the threshold decision turned on; every value is written out, none is
    read back from the factor file or recomputed by the code under test.

    rincon is EXCLUDED BY NAME regardless of its statistic — asserted separately below so
    that changing the threshold can never silently re-admit it.
    """
    import sys
    sys.path.insert(0, "scripts")
    from build_face_factors import classify
    # (slug, measured p75/p25, expected verdict at 1.7)
    cases = [
        ("tarpits",                 2.30, "spread"),
        ("moonstone-beach-humboldt", 1.94, "spread"),
        ("mackerricher",            1.78, "spread"),
        ("ten-mile-beach",          1.73, "spread"),
        # --- the 1.7 line ---
        ("jug-handle",              1.64, "keep"),    # excluded at 1.6, kept at 1.7
        ("westport",                1.61, "keep"),    # excluded at 1.6, kept at 1.7
        ("point-arena",             1.570, "keep"),   # the widest spot that passes
        ("caspar",                  1.557, "keep"),
        ("steamer-lane",            1.46, "keep"),    # the reported regression
        ("cowell-s-beach-children-s-surfing-area", 1.46, "keep"),
        ("doran-beach",             1.39, "keep"),
        ("second-peak",             1.36, "keep"),
        ("natural-bridges",         1.34, "keep"),
    ]
    for slug, iqr, expect in cases:
        rec = _fr(1.0, iqr)
        rec["slug"] = slug
        v, detail = classify(rec, set())
        assert v == expect, (slug, iqr, v, expect, detail)
    # The two the 1.6 proposal would have excluded and 1.7 keeps, stated as the difference.
    assert classify({**_fr(1.0, 1.64), "slug": "jug-handle"}, set())[0] == "keep"
    assert classify({**_fr(1.0, 1.61), "slug": "westport"}, set())[0] == "keep"


def test_the_named_hold_outs_are_unaffected_by_the_statistic_change():
    """fort-point, sandspit and rincon are excluded BEFORE any statistic is consulted, so
    the switch from p90/p10 to p75/p25 cannot re-admit them. Each is given a perfectly tight
    core (p75/p25 = 1.0) — the most passable spread possible — and must still be held out.

    rincon matters most: it is the clearest example the spread rule exists to catch, and its
    p75/p25 of 2.27 would exclude it anyway. The name is what guarantees it, not the number.
    """
    import sys
    sys.path.insert(0, "scripts")
    from build_face_factors import classify
    from build_face_factors import HELD_OUT
    for slug in ("fort-point", "sandspit", "rincon"):
        assert slug in HELD_OUT, slug
        rec = _fr(1.0, 1.0)          # a spread nothing could exclude
        rec["slug"] = slug
        v, detail = classify(rec, set())
        assert v == "held_out", (slug, v)
        assert detail == HELD_OUT[slug], slug
    # Order still holds: MOP tier beats a named hold-out beats the statistic.
    rec = _fr(1.0, 9.0)
    rec["slug"] = "rincon"
    assert classify(rec, {"rincon"})[0] == "mop_tier", "tier is checked first"


def test_a_record_with_no_quartiles_is_unusable_not_silently_kept():
    """A spread file predating the p25/p75 measurement cannot be judged. Keeping it would
    correct a spot whose spread was never measured — the same "absent is not a default" rule
    the factor lookup follows. Its p90/p10 is a perfect 1.0 and it is STILL not kept."""
    import sys
    sys.path.insert(0, "scripts")
    from build_face_factors import classify
    v, detail = classify({"slug": "s", "face_ratio": {"median": 2.0, "n": 300,
                                                      "p10": 1.0, "p90": 1.0}}, set())
    assert v == "unusable", (v, detail)
    assert "p25/p75" in detail, detail


def test_the_threshold_is_the_committed_constant():
    from pipeline.config import FACE_FACTOR_MAX_IQR_RATIO
    assert FACE_FACTOR_MAX_IQR_RATIO == 1.7
    # The old constant is GONE, not merely unused. A stale reader comparing p90/p10 against
    # 1.7 would exclude almost the whole roster, so the rename has to fail loudly.
    import pipeline.config as _cfg
    assert not hasattr(_cfg, "FACE_FACTOR_MAX_SPREAD"), (
        "FACE_FACTOR_MAX_SPREAD is back. The statistic changed from p90/p10 to p75/p25 and "
        "the threshold from 2.5 to 1.7; a reader holding the old name will compare the "
        "wrong ratio against the wrong number.")


def test_the_tail_ratio_is_still_recorded_but_no_longer_decides():
    """p90/p10 stays on every record as a diagnostic — a heavy tail should remain visible in
    the diff — but it must not exclude. Steamer Lane's own numbers make the case:
        p10 0.821  p25 2.234  p75 3.256  p90 3.630
        p90/p10 = 4.4214...  (would have excluded under the old rule)
        p75/p25 = 1.4575...  (comfortably inside 1.7)
    """
    import sys
    sys.path.insert(0, "scripts")
    from build_face_factors import _iqr_ratio, _spread
    rec = {"slug": "steamer-lane",
           "face_ratio": {"median": 2.808, "n": 334,
                          "p10": 0.821, "p25": 2.234, "p75": 3.256, "p90": 3.630}}
    # 3.630 / 0.821 = 4.42143...   3.256 / 2.234 = 1.45747...
    assert abs(_spread(rec) - 4.421437) < 1e-5, _spread(rec)
    assert abs(_iqr_ratio(rec) - 1.457475) < 1e-5, _iqr_ratio(rec)
    from build_face_factors import classify
    assert classify(rec, set())[0] == "keep", "the reported regression is fixed"


# --------------------------------------------------------------------------- #
# 6 — an unresolvable slug fails LOUDLY                                       #
# --------------------------------------------------------------------------- #

def test_an_unresolvable_slug_fails_loudly():
    raised = False
    try:
        FC.apply_face_corrections({"Steamer Lane": [_entry()]}, [_spot()],
                                  factors={"steamer-lane": _rec(2.0),
                                           "no-such-spot": _rec(2.0)},
                                  slug_for=_slug, now=TODAY)
    except FC.FaceFactorSlugError as e:
        raised = True
        assert "no-such-spot" in str(e), str(e)
        assert "build_face_factors" in str(e), "the error must name the regeneration command"
    assert raised, "an unresolvable slug must raise, not be skipped"


def test_validation_runs_before_any_row_is_touched():
    """A bad slug must not leave the run half-corrected."""
    before = _entry()
    ratings = {"Steamer Lane": [dict(before)]}
    try:
        FC.apply_face_corrections(ratings, [_spot()],
                                  factors={"steamer-lane": _rec(2.0), "bogus": _rec(2.0)},
                                  slug_for=_slug, now=TODAY)
    except FC.FaceFactorSlugError:
        pass
    assert ratings["Steamer Lane"][0] == before, "rows were mutated before validation failed"
    assert "face_ft_raw" not in ratings["Steamer Lane"][0], \
        "not even the provenance stamp may land before validation rejects the file"


# --------------------------------------------------------------------------- #
# 7 — the staleness warning                                                   #
# --------------------------------------------------------------------------- #

def _warn_lines(factors, now):
    """Run the correction and return only the staleness warnings.

    The roster carries every slug the caller may put in *factors* — validate_factor_slugs
    aborts otherwise, which is the behaviour tested elsewhere and would mask the warning
    here."""
    cap = _CaptureLog()
    FC.log.addHandler(cap)
    try:
        FC.apply_face_corrections(
            {"Steamer Lane": [_entry()]},
            [_spot(), _spot("Older Spot")],
            factors=factors, slug_for=_slug, now=now)
    finally:
        FC.log.removeHandler(cap)
    return [r.getMessage() for r in cap.records
            if r.levelno >= logging.WARNING and "days old" in r.getMessage()]


def test_the_age_warning_does_not_fire_at_exactly_the_limit():
    """120 days is NOT past the limit — the comparison is strictly greater.
    2026-09-01 + 120 days = 2026-12-30."""
    assert FACE_FACTOR_MAX_AGE_DAYS == 120
    assert _warn_lines({"steamer-lane": _rec(2.0, measured_on="2026-09-01")},
                       datetime.date(2026, 12, 30)) == []


def test_the_age_warning_fires_one_day_past_the_limit():
    """2026-12-31 is 121 days after 2026-09-01."""
    said = _warn_lines({"steamer-lane": _rec(2.0, measured_on="2026-09-01")},
                       datetime.date(2026, 12, 31))
    assert len(said) == 1, said
    assert "121 days old" in said[0], said[0]
    assert "steamer-lane" in said[0], said[0]
    assert "build_face_factors.py --apply" in said[0], "the warning must carry its remedy"
    # Why age matters here, and when the stale factors were measured — read from the record,
    # not written into the message: it used to say "14 SUMMER days" whatever the file held.
    assert "NOT validated across a season change" in said[0], said[0]
    assert "measured over 2026-08-18 to 2026-09-01" in said[0], said[0]


def test_the_age_warning_names_the_measurement_window_of_the_stale_record():
    """The 2026-10-06 file's window is 28 days, 2026-09-08 to 2026-10-06, and a record
    without a window (written before records carried one) is still warned about."""
    rec = _rec(2.0, measured_on="2026-10-06", window={"t0": "2026-09-08", "t1": "2026-10-06"})
    # 2027-02-04 is 121 days after 2026-10-06.
    said = _warn_lines({"steamer-lane": rec}, datetime.date(2027, 2, 4))
    assert len(said) == 1 and "measured over 2026-09-08 to 2026-10-06" in said[0], said
    bare = _rec(2.0, measured_on="2026-10-06")
    del bare["window"]
    said = _warn_lines({"steamer-lane": bare}, datetime.date(2027, 2, 4))
    assert len(said) == 1 and "measured over a single window" in said[0], said
    # With two records, the window named is the stalest record's, not the first one's: the
    # 10-06 record comes first here, and the warning is about the older 09-01 one.
    said = _warn_lines({"steamer-lane": rec, "older-spot": _rec(2.0, measured_on="2026-09-01")},
                       datetime.date(2027, 2, 4))
    assert len(said) == 1 and "older-spot" in said[0], said
    assert "measured over 2026-08-18 to 2026-09-01" in said[0], said
    assert "2026-09-08" not in said[0], said


def test_the_warning_names_the_oldest_factor_not_the_first():
    said = _warn_lines({"steamer-lane": _rec(2.0, measured_on="2026-09-01"),
                        "older-spot": _rec(2.0, measured_on="2026-01-01")},
                       datetime.date(2026, 12, 31))
    assert len(said) == 1, said
    assert "older-spot" in said[0], said[0]
    assert "364 days old" in said[0], said[0]      # 2026-01-01 -> 2026-12-31


def test_an_unparseable_measured_on_warns_but_does_not_crash():
    cap = _CaptureLog()
    FC.log.addHandler(cap)
    try:
        st = FC.apply_face_corrections({"Steamer Lane": [_entry()]}, [_spot()],
                                       factors={"steamer-lane": _rec(2.0, measured_on="nope")},
                                       slug_for=_slug, now=TODAY)
    finally:
        FC.log.removeHandler(cap)
    assert st["corrected_hours"] == 1
    assert any("unparseable measured_on" in r.getMessage() for r in cap.records)


# --------------------------------------------------------------------------- #
# 8 — the loader                                                              #
# --------------------------------------------------------------------------- #

def test_a_missing_factors_file_is_the_no_op_default_not_an_error():
    from pathlib import Path
    assert FC.load_face_factors(Path("/definitely/not/here.json")) == {}


def test_a_corrupt_factors_file_degrades_to_no_overrides():
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mkdtemp()) / "bad.json"
    p.write_text("{not json")
    assert FC.load_face_factors(p) == {}


def test_the_loader_drops_non_positive_and_non_numeric_factors():
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mkdtemp()) / "f.json"
    p.write_text(json.dumps({"factors": {
        "good": {"factor": 2.87}, "zero": {"factor": 0.0}, "neg": {"factor": -1.0},
        "nul": {"factor": None}, "nokey": {"hours": 3}, "notdict": 2.0}}))
    got = FC.load_face_factors(p)
    assert list(got) == ["good"], got
    assert got["good"]["factor"] == 2.87


def test_the_loader_preserves_the_provenance_fields():
    import tempfile
    from pathlib import Path
    p = Path(tempfile.mkdtemp()) / "f.json"
    p.write_text(json.dumps({"factors": {"s": {
        "factor": 2.87, "hours": 334, "spread_p90_p10": 1.52,
        "measured_on": "2026-09-01", "source": "scripts/mop_face_validation.py"}}}))
    got = FC.load_face_factors(p)["s"]
    assert got["hours"] == 334 and got["spread_p90_p10"] == 1.52
    assert got["measured_on"] == "2026-09-01"
    assert got["source"] == "scripts/mop_face_validation.py"



# --------------------------------------------------------------------------- #
# 9 — the seam is wired into interpret.main, AFTER both overrides              #
# --------------------------------------------------------------------------- #

def test_the_seam_runs_after_both_overrides_in_interpret_main():
    """THE PLACEMENT, pinned end to end. interpret.main is ordered

        compute_ratings        -> rate_spot writes face_ft   (producers 2 + 3)
        apply_mop_overrides    -> overwrites face_ft         (producer 4)
        apply_nwps_overrides   -> overwrites face_ft         (producer 1)
        apply_face_corrections -> the single seam

    A seam anywhere earlier would be overwritten by whichever override ran later, so this
    drives main on fixture files and asserts the correction survived to the output. Pure
    file-in / file-out: no database, no network.

    Fixture arithmetic, hand-computed. The NWPS override wins this hour, and nwps_stars
    uses RATING_SOURCE = "ww3", so period_factor(13.0, "ww3") = 1.15 + (13-12)/(14-12) *
    (1.20-1.15) = 1.175 and face = 2.0 * 1.175 * 3.281 = 7.71035 -> 7.71 published.
    With a factor of 3.0 the corrected face is 7.71 / 3.0 = 2.5700 -> 2.57.
    """
    import contextlib, io, tempfile
    from pathlib import Path as _P
    from pipeline import interpret

    tmp = _P(tempfile.mkdtemp())
    spot = {"name": "Steamer Lane", "slug": "steamer-lane", "lat": 36.9515,
            "lng": -122.0256, "swell_window_source": "nwps", "orientation_deg": 220.0,
            "optimal_swell_dir": 220, "nwps_wfo": "mtr",
            "swell_window_arcs": [{"min": 180, "max": 260, "span": 84}]}
    (tmp / "spots.json").write_text(json.dumps([spot]))
    (tmp / "nwps.json").write_text(json.dumps({"Steamer Lane": [
        {"valid_time": "2026-09-01T00:00:00Z", "hs": 2.0, "tp": 13.0, "dp": 220.0,
         "swell_hs": 2.0}]}))
    (tmp / "tides.json").write_text(json.dumps({}))
    (tmp / "factors.json").write_text(json.dumps({"factors": {"steamer-lane": {
        "factor": 3.0, "hours": 334, "measured_on": "2026-09-01",
        "source": "scripts/mop_face_validation.py"}}}))

    saved = FC.SPOT_FACE_FACTORS_FILE
    try:
        FC.SPOT_FACE_FACTORS_FILE = tmp / "factors.json"
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = interpret.main(["--spots", str(tmp / "spots.json"),
                                 "--nwps", str(tmp / "nwps.json"),
                                 "--tides", str(tmp / "tides.json"),
                                 "--output", str(tmp / "out.json")])
        out = json.loads((tmp / "out.json").read_text())
    finally:
        logging.disable(logging.NOTSET)
        FC.SPOT_FACE_FACTORS_FILE = saved
        for f in tmp.iterdir():
            f.unlink()
        tmp.rmdir()

    assert rc == 0, rc
    e = out["Steamer Lane"][0]
    assert e["face_ft"] == 2.57, ("the seam did not run, or ran before an override", e["face_ft"])


def test_through_interpret_main_a_calibrated_spot_is_sized_on_its_calibrated_face():
    """THE DECISION, end to end: rate_spot, both overrides and the seam, on fixture files.

    Two spots with IDENTICAL geometry and an identical NWPS hour; only Steamer Lane has a
    factor (3.0). A third roster spot carries the file's other factor (1.0), so the median
    factor M is (1.0 + 3.0) / 2 = 2.0.

    SINCE 2026-10-21 (rating-preview run 32, cal-median) a calibrated spot is SIZED on its
    calibrated face, on the yardstick of M: effective x M / F. The uncalibrated twin is rated
    exactly as before. Until then the two rated identically (version 2, below), which put
    calibrated California spots shown at 1-4 ft at 4-4.5 stars on the homepage.

    Hand-computed. The NWPS override rates the hour: no WW3 series, so direction 220 and
    period 13.0 come from NWPS; 220 is the optimal bearing inside the 180..260 window, so
    dir_gain = cos^2(0) = 1.0.
        face  = 2.0 * period_factor(13.0, "ww3") * 3.281 = 2.0 * 1.175 * 3.281 = 7.71035
        eff   = face * 1.0 = 7.71035 -> stored 7.71 on both (the producer's own size input)
        twin  : wind, tide, chop (2.0 of 2.0 is swell) and period (13 s) all score 1.0, so
                size_score(7.71035) = 4.0 + (1.71035 / 2) * 0.5 = 4.42759 -> 4.5 stars
        Steamer Lane: sized on 7.71035 * 2.0 / 3.0 = 5.14023 ft,
                size_score = 3.5 + (0.14023 / 2) * 0.5 = 3.53506 -> round(7.07) / 2 = 3.5 stars
        shown : Steamer Lane 7.71 / 3.0 = 2.57; the twin, with no factor, the surf height
                (forecast/surf_height.py, since 2026-10-27): Hs 7.71 / (1.175 x 3.281) = 1.99991
                -> 2.00 m at 13.0 s, shoaled from 10 m to breaking at 0.78 x depth by linear
                theory: breaks in 3.2583 m at Hb 2.54145 m, 8.33851 ft; 0.70 x 8.33851 =
                5.83696 -> 5.84. (Solved by a separate scalar bisection, not the code under test.)
    Version 1 re-rated Steamer Lane from eff 2.57 on the unshifted curve: 2.5 stars. Version 2
    rated both 4.5.
    """
    import contextlib, io, tempfile
    from pathlib import Path as _P
    from pipeline import interpret

    tmp = _P(tempfile.mkdtemp())
    geometry = {"lat": 36.9515, "lng": -122.0256, "swell_window_source": "nwps",
                "orientation_deg": 220.0, "optimal_swell_dir": 220, "nwps_wfo": "mtr",
                "swell_window_arcs": [{"min": 180, "max": 260, "span": 84}]}
    hour = {"valid_time": "2026-09-01T00:00:00Z", "hs": 2.0, "tp": 13.0, "dp": 220.0,
            "swell_hs": 2.0}
    (tmp / "spots.json").write_text(json.dumps([
        dict(geometry, name="Steamer Lane", slug="steamer-lane"),
        dict(geometry, name="Steamer Twin", slug="steamer-twin"),
        dict(geometry, name="Other Spot", slug="other-spot")]))
    (tmp / "nwps.json").write_text(json.dumps({"Steamer Lane": [dict(hour)],
                                               "Steamer Twin": [dict(hour)]}))
    (tmp / "tides.json").write_text(json.dumps({}))
    rec = {"hours": 334, "measured_on": "2026-09-01", "source": "scripts/mop_face_validation.py"}
    (tmp / "factors.json").write_text(json.dumps({"factors": {
        "steamer-lane": dict(rec, factor=3.0), "other-spot": dict(rec, factor=1.0)}}))

    saved = FC.SPOT_FACE_FACTORS_FILE
    try:
        FC.SPOT_FACE_FACTORS_FILE = tmp / "factors.json"
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = interpret.main(["--spots", str(tmp / "spots.json"),
                                 "--nwps", str(tmp / "nwps.json"),
                                 "--tides", str(tmp / "tides.json"),
                                 "--output", str(tmp / "out.json")])
        out = json.loads((tmp / "out.json").read_text())
    finally:
        logging.disable(logging.NOTSET)
        FC.SPOT_FACE_FACTORS_FILE = saved
        for f in tmp.iterdir():
            f.unlink()
        tmp.rmdir()

    assert rc == 0, rc
    cal, twin = out["Steamer Lane"][0], out["Steamer Twin"][0]
    assert (cal["face_ft"], twin["face_ft"]) == (2.57, 5.84), (cal["face_ft"], twin["face_ft"])
    assert twin["face_correction_version"] == cal["face_correction_version"] + "+surf:k0.70:d10:g0.78"
    assert cal["face_ft_raw"] == twin["face_ft_raw"] == 7.71
    assert cal["effective_size_ft"] == twin["effective_size_ft"] == 7.71
    assert (cal["stars"], twin["stars"]) == (3.5, 4.5), (cal["stars"], twin["stars"])
    # Field for field, nothing else differs: the producer's work is the same on both.
    skip = ("face_ft", "stars", "face_correction_version")
    assert {k: v for k, v in cal.items() if k not in skip} == \
        {k: v for k, v in twin.items() if k not in skip}, (cal, twin)



def test_the_generator_excludes_the_mop_tier_from_the_committed_file():
    """The SECOND line of defence, and it needs its own test because the runtime exclusion
    would mask its removal: if the generator stopped filtering, MOP-tier slugs would land in
    `factors` and apply_face_corrections would skip them anyway, so the pipeline's behaviour
    would not change — but the committed file would claim a factor for a spot that is never
    corrected, which is exactly the measured-vs-guessed confusion the file exists to prevent.

    Found by mutation: deleting the generator's tier check survived every other test here,
    all of which pass an EMPTY tier set."""
    import sys
    sys.path.insert(0, "scripts")
    from build_face_factors import classify
    clean = {"slug": "a-mop-spot",
             "face_ratio": {"median": 2.5, "n": 300,
                            "p10": 1.0, "p25": 1.0, "p75": 1.2, "p90": 1.2}}
    assert classify(clean, {"a-mop-spot"})[0] == "mop_tier"
    assert classify(clean, set())[0] == "keep", "same record, no tier set -> kept"
    # Tier beats every statistical gate: the objection is structural, not about the numbers.
    assert classify({"slug": "a-mop-spot",
                     "face_ratio": {"median": 2.0, "n": 9,
                                    "p10": 1.0, "p25": 1.0, "p75": 9.0, "p90": 9.0}},
                    {"a-mop-spot"})[0] == "mop_tier"


# --------------------------------------------------------------------------- #
# THE BAND IS RETIRED — the seam writes no face_lo_ft / face_hi_ft (migration 023) #
# --------------------------------------------------------------------------- #
# Until 2026-10-21 the seam divided the raw face by the record's p75 and p25 and wrote a band
# at the calibrated spots only. The site now prints the fixed ladder step face_ft falls in, at
# every spot (frontend/lib/formatting.ts, pinned in formatting.test.mts), and db_import writes
# NULL to both columns. The tests that held the band's arithmetic and its lo <= face <= hi
# backstop went with it; these hold its absence.

def _rec_banded(factor=2.0, p25=1.6, p75=2.5, **kw):
    """A factor record carrying p25/p75, as every committed one does. p25 < factor < p75."""
    return _rec(factor, p25=p25, p75=p75, **kw)


def test_a_record_with_a_spread_writes_no_band_keys():
    """The committed records all carry p25/p75; the seam must not turn them into a band.

        raw 8.0 / factor 2.0 = 4.0 shown; no face_lo_ft, no face_hi_ft, not even as None
    """
    ratings = {"Steamer Lane": [_entry(face=8.0, eff=8.0)]}
    st = FC.apply_face_corrections(
        ratings, [_spot()], factors={"steamer-lane": _rec_banded(2.0, 1.6, 2.5)},
        slug_for=lambda n: "steamer-lane", now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["face_ft"] == 4.0, e["face_ft"]
    assert "face_lo_ft" not in e and "face_hi_ft" not in e, sorted(e)
    assert st["corrected_spots"] == 1 and st["corrected_hours"] == 1, st


def test_the_run_summary_no_longer_counts_bands():
    """ranged_hours, spots_without_spread and bad_band_spots described a band that is no longer
    written; a summary that kept reporting 0 of them would read as a band that failed."""
    gone = {"ranged_hours", "spots_without_spread", "bad_band_spots"}
    st = FC.apply_face_corrections({"Steamer Lane": [_entry()]}, [_spot()],
                                   factors={"steamer-lane": _rec_banded()},
                                   slug_for=lambda n: "steamer-lane", now=TODAY)
    assert not gone & set(st), sorted(st)
    inert = FC.apply_face_corrections({"Steamer Lane": [_entry()]}, [_spot()], factors={},
                                      slug_for=lambda n: "steamer-lane", now=TODAY)
    assert not gone & set(inert), sorted(inert)


def test_the_quantiles_change_nothing_the_seam_writes():
    """Two runs on identical input, one record with p25/p75 and one without, produce identical
    entries: the same 4.0 shown (8.0 / 2.0), the producer's 8.0 size input and 4.0 stars. The
    spread is read only by build_face_factors' p75/p25 gate and diff_face_factors."""
    banded = {"Steamer Lane": [_entry(face=8.0, eff=8.0)]}
    plain = {"Steamer Lane": [_entry(face=8.0, eff=8.0)]}
    FC.apply_face_corrections(banded, [_spot()], factors={"steamer-lane": _rec_banded(2.0, 1.6, 2.5)},
                              slug_for=lambda n: "steamer-lane", now=TODAY)
    FC.apply_face_corrections(plain, [_spot()], factors={"steamer-lane": _rec(2.0)},
                              slug_for=lambda n: "steamer-lane", now=TODAY)
    b, p = banded["Steamer Lane"][0], plain["Steamer Lane"][0]
    # The stamps differ only if the fingerprint does, and it reads factors alone: same here.
    assert b == p, (b, p)
    assert b["face_ft"] == 4.0 and b["effective_size_ft"] == 8.0 and b["stars"] == 4.0, b


def test_face_range_is_gone_from_the_seam():
    """Nothing is left to call it; a band reintroduced would have to bring it back past the
    module docstring that says why it went."""
    assert not hasattr(FC, "face_range")


def test_every_committed_record_has_quantiles_that_bracket_its_median():
    """THE FULL FILE, as a data check. p25 <= median <= p75 holds for any genuine set of
    quantiles, so a record that breaks it was hand-edited or written by something other than
    build_face_factors — and the p75/p25 gate and the drift check both read those numbers."""
    import json as _json
    from pipeline.config import SPOT_FACE_FACTORS_FILE
    doc = _json.loads(SPOT_FACE_FACTORS_FILE.read_text())
    bad = [(slug, rec.get("p25"), rec.get("factor"), rec.get("p75"))
           for slug, rec in (doc.get("factors") or {}).items()
           if not (rec["p25"] <= rec["factor"] <= rec["p75"])]
    assert not bad, f"{len(bad)} committed record(s) whose quantiles miss their median: {bad[:5]}"


def test_the_relabel_did_not_rename_a_single_parsed_field():
    """DECISION 1 IS DISPLAY-ONLY, pinned at the field names.

    The tile now says "Swell height" because what is published is MOP significant wave
    height at the 10-15 m contour, not a breaking face. Nothing PARSES that string — but a
    later hand could reasonably decide to "finish the job" and rename the column too, and
    face_ft is referenced by db_import, both frontend select lists, daily_report,
    surf_reports.forecast_face_ft and migration 015. This is the test that makes that a
    deliberate migration rather than a silent break.

    Written as literals, not derived from the code under test."""
    ratings = {"Steamer Lane": [_entry(face=8.0, eff=8.0)]}
    FC.apply_face_corrections(
        ratings, [_spot()], factors={"steamer-lane": _rec_banded(2.0, 1.6, 2.5)},
        slug_for=lambda n: "steamer-lane", now=TODAY)
    keys = set(ratings["Steamer Lane"][0])
    for name in ("face_ft", "effective_size_ft", "stars"):
        assert name in keys, f"{name} was renamed; db_import and the frontend read it"
    # And the tempting new names are NOT present, so a half-done rename fails here.
    for wrong in ("swell_height_ft", "swell_ft", "swell_lo_ft", "swell_hi_ft"):
        assert wrong not in keys, f"{wrong} appeared — the column rename is a migration"


def test_every_committed_factor_carries_the_spread_it_was_kept_on():
    """THE SHIPPED STATE. This replaces the inert-state guard, which fired on schedule when
    the measurement was regenerated (724f442) and had done its job.

    Two invariants on the committed file, both of which the exclusion rule now depends on:

      1. EVERY kept factor carries p25/p75. classify() returns "unusable" without them, so a
         factor in the map that lacks them could only have arrived by hand-editing.
      2. NO kept factor exceeds the threshold. The file and the constant must agree, or the
         committed artifact is not the one the rule would produce.
    """
    import json as _json
    from pipeline.config import FACE_FACTOR_MAX_IQR_RATIO, SPOT_FACE_FACTORS_FILE
    doc = _json.loads(SPOT_FACE_FACTORS_FILE.read_text())
    factors = doc.get("factors") or {}
    assert factors, "the committed file should carry factors"
    missing = [s for s, r in factors.items() if r.get("p25") is None or r.get("p75") is None]
    assert not missing, f"{len(missing)} committed factor(s) carry no p25/p75: {missing[:5]}"
    over = [(s, r["p75"] / r["p25"]) for s, r in factors.items()
            if r["p25"] > 0 and r["p75"] / r["p25"] > FACE_FACTOR_MAX_IQR_RATIO]
    assert not over, (
        f"{len(over)} committed factor(s) exceed the {FACE_FACTOR_MAX_IQR_RATIO} threshold: "
        f"{sorted(over, key=lambda x: -x[1])[:5]}. Regenerate: "
        f"python3 scripts/build_face_factors.py --apply")


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\ntest_face_correction: {len(fns)} PASS")


if __name__ == "__main__":
    _run_all()


def test_interpret_main_hands_the_median_factor_to_every_rater():
    """main reads the factor file once and passes M to compute_ratings (rate_spot) and to both
    overrides. A spot off the NWPS tier is rated by rate_spot itself, so its stars show whether
    compute_ratings got M; the MOP override is replaced by a recorder, since its real fetch
    reads CDIP over the network.

    Hand-computed. Factors 3.0 (Steamer Lane) and 1.0 (Other Spot): M = 2.0. rate_spot's NWPS
    path sizes on swell_hs 2.0 m at 13 s on the NWPS table, period_factor(13) = 1.5 + 0.5 x 0.05
    = 1.525: face = 2.0 x 1.525 x 3.281 = 10.00705 ft, gain 1.0 (220 is the optimal), every
    quality score 1.0 (13 s scores 1.0).
        twin          size_score(10.00705) = 5.0 -> 5.0 stars
        Steamer Lane  10.00705 x 2.0 / 3.0 = 6.67137 ft, size 4.0 + 0.67137 / 2 x 0.5 = 4.16784
                      -> round(8.34) / 2 = 4.0 stars
    """
    import contextlib, io, tempfile
    from pathlib import Path as _P
    from pipeline import interpret
    from pipeline.forecast import mop

    tmp = _P(tempfile.mkdtemp())
    geometry = {"lat": 36.9515, "lng": -122.0256, "swell_window_source": "orientation",
                "orientation_deg": 220.0, "optimal_swell_dir": 220,
                "swell_window_arcs": [{"min": 180, "max": 260, "span": 84}]}
    hour = {"valid_time": "2026-09-01T00:00:00Z", "hs": 2.0, "tp": 13.0, "dp": 220.0,
            "swell_hs": 2.0, "swell_tp": 13.0, "swell_dp": 220.0}
    (tmp / "spots.json").write_text(json.dumps([
        dict(geometry, name="Steamer Lane", slug="steamer-lane"),
        dict(geometry, name="Steamer Twin", slug="steamer-twin"),
        dict(geometry, name="Other Spot", slug="other-spot")]))
    (tmp / "nwps.json").write_text(json.dumps({"Steamer Lane": [dict(hour)],
                                               "Steamer Twin": [dict(hour)]}))
    (tmp / "tides.json").write_text(json.dumps({}))
    rec = {"hours": 334, "measured_on": "2026-09-01", "source": "scripts/mop_face_validation.py"}
    (tmp / "factors.json").write_text(json.dumps({"factors": {
        "steamer-lane": dict(rec, factor=3.0), "other-spot": dict(rec, factor=1.0)}}))

    seen = {}

    def recorder(ratings, spots, **kw):
        seen.update(kw)
        return {"fed": 0, "fell_back": 0, "errored": 0, "details": []}

    saved, saved_mop = FC.SPOT_FACE_FACTORS_FILE, mop.apply_mop_overrides
    try:
        FC.SPOT_FACE_FACTORS_FILE = tmp / "factors.json"
        mop.apply_mop_overrides = recorder
        logging.disable(logging.CRITICAL)
        with contextlib.redirect_stdout(io.StringIO()):
            rc = interpret.main(["--spots", str(tmp / "spots.json"),
                                 "--nwps", str(tmp / "nwps.json"),
                                 "--tides", str(tmp / "tides.json"),
                                 "--output", str(tmp / "out.json")])
        out = json.loads((tmp / "out.json").read_text())
    finally:
        logging.disable(logging.NOTSET)
        FC.SPOT_FACE_FACTORS_FILE, mop.apply_mop_overrides = saved, saved_mop
        for f in tmp.iterdir():
            f.unlink()
        tmp.rmdir()

    assert rc == 0, rc
    assert seen.get("median_factor") == 2.0, seen
    assert (out["Steamer Lane"][0]["stars"], out["Steamer Twin"][0]["stars"]) == (4.0, 5.0)
