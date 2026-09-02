"""The per-spot MOP face correction, and the star recompute that must follow it.

WHAT IS BEING PINNED. A measured divisor scales face_ft AND effective_size_ft, and stars is
recomputed by the production composite_stars from the corrected size. A spot with no factor,
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
# 1 — a spot WITH a factor is corrected, and its stars recomputed              #
# --------------------------------------------------------------------------- #

def test_a_factored_spot_has_face_effective_and_stars_all_corrected():
    """size_score knots: (0,0) (1,1) (2,2) (3,2.5) (4,3) (5,3.5) (6,4) (8,4.5) (10,5).

        before: eff 6.0 -> size_score 4.0 (a knot) -> stars 4.0
        factor 3.0
        after:  face 6.0 / 3.0 = 2.0
                eff  6.0 / 3.0 = 2.0 -> size_score 2.0 (a knot) -> stars 2.0
    """
    ratings = {"Steamer Lane": [_entry(face=6.0, eff=6.0)]}
    st = FC.apply_face_corrections(ratings, [_spot()],
                                   factors={"steamer-lane": _rec(3.0)},
                                   slug_for=_slug, now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["face_ft"] == 2.0, e["face_ft"]
    assert e["effective_size_ft"] == 2.0, e["effective_size_ft"]
    assert e["stars"] == 2.0, e["stars"]
    assert st["corrected_spots"] == 1 and st["corrected_hours"] == 1, st


def test_the_star_recompute_is_non_linear_in_the_face():
    """The whole reason this is not a display-layer fix. Dividing the face by 2.87 does NOT
    divide the stars by 2.87.

        before: eff 6.0 -> size_score 4.0 -> stars 4.0
        after:  eff 6.0 / 2.87 = 2.0905923...
                size_score interpolates (2,2)->(3,2.5): 2 + 0.0905923 * 0.5 = 2.0452961
                round(4.0905923) / 2 = 4 / 2 = 2.0
        4.0 -> 2.0 is a factor of 2.0 in stars for a factor of 2.87 in face.
    """
    ratings = {"Steamer Lane": [_entry(face=6.0, eff=6.0)]}
    FC.apply_face_corrections(ratings, [_spot()], factors={"steamer-lane": _rec(2.87)},
                              slug_for=_slug, now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["face_ft"] == 2.09, e["face_ft"]          # round(2.0905923, 2)
    assert e["effective_size_ft"] == 2.09, e["effective_size_ft"]
    assert e["stars"] == 2.0, e["stars"]


def test_the_recompute_uses_the_production_star_chain():
    """Identity, plus a tripwire proving composite_stars is CALLED rather than shadowed by
    a local copy of the curve."""
    import pipeline.interpret as I
    assert FC.composite_stars is I.composite_stars
    saved = FC.composite_stars
    try:
        FC.composite_stars = lambda *a, **k: 4.25
        ratings = {"Steamer Lane": [_entry()]}
        FC.apply_face_corrections(ratings, [_spot()], factors={"steamer-lane": _rec(2.0)},
                                  slug_for=_slug, now=TODAY)
        assert ratings["Steamer Lane"][0]["stars"] == 4.25
    finally:
        FC.composite_stars = saved


def test_the_four_quality_factors_are_passed_through_unchanged():
    """Only the size moves. wind/tide/chop/period are the row's own values.

        eff 8.0 / 2.0 = 4.0 -> size_score 3.0 (a knot)
        raw = 3.0 * 0.8**0.35 * 0.8**0.15 * 0.8**0.25 * 0.8**0.25
            = 3.0 * 0.8**1.0 = 2.4          (the exponents sum to 1.0)
        stars = round(4.8) / 2 = 5 / 2 = 2.5
    """
    ratings = {"Steamer Lane": [_entry(face=8.0, eff=8.0, wind_mult=0.8, tide_mult=0.8,
                                       chop_mult=0.8, period_quality=0.8)]}
    FC.apply_face_corrections(ratings, [_spot()], factors={"steamer-lane": _rec(2.0)},
                              slug_for=_slug, now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["stars"] == 2.5, e["stars"]
    assert (e["wind_mult"], e["tide_mult"], e["chop_mult"], e["period_quality"]) \
        == (0.8, 0.8, 0.8, 0.8)


def test_a_factor_below_one_makes_the_face_bigger():
    """Rincon's 0.62 shape — held out in the shipped file, but the arithmetic must be right
    for whatever a future file contains.

        face 2.0 / 0.5 = 4.0; eff 2.0 / 0.5 = 4.0 -> size_score 3.0 -> stars 3.0
    """
    ratings = {"Steamer Lane": [_entry(face=2.0, eff=2.0)]}
    FC.apply_face_corrections(ratings, [_spot()], factors={"steamer-lane": _rec(0.5)},
                              slug_for=_slug, now=TODAY)
    e = ratings["Steamer Lane"][0]
    assert e["face_ft"] == 4.0 and e["effective_size_ft"] == 4.0 and e["stars"] == 3.0


def test_the_sub_half_foot_cutoff_is_reachable_by_correction():
    """A correction can push an hour under composite_stars' 0.5 ft flat cutoff, which is a
    DIFFERENT state from the 1.0 floor.

        eff 1.0 / 2.87 = 0.348...  < 0.5  ->  0.0 stars, not 1.0
    """
    ratings = {"Steamer Lane": [_entry(face=1.0, eff=1.0, stars=1.0)]}
    FC.apply_face_corrections(ratings, [_spot()], factors={"steamer-lane": _rec(2.87)},
                              slug_for=_slug, now=TODAY)
    assert ratings["Steamer Lane"][0]["stars"] == 0.0


# --------------------------------------------------------------------------- #
# 2 — everything without a factor is BYTE-IDENTICAL                           #
# --------------------------------------------------------------------------- #

def test_a_spot_with_no_factor_is_byte_identical():
    before = _entry()
    ratings = {"Ocean Beach": [dict(before)]}
    st = FC.apply_face_corrections(ratings, [_spot("Ocean Beach"), _spot("Steamer Lane")],
                                   factors={"steamer-lane": _rec(2.87)},
                                   slug_for=_slug, now=TODAY)
    assert ratings["Ocean Beach"][0] == before, ratings["Ocean Beach"][0]
    assert st["corrected_spots"] == 0 and st["no_factor"] == 1, st


def test_an_empty_factor_map_touches_nothing_and_short_circuits():
    before = _entry()
    ratings = {"Steamer Lane": [dict(before)]}
    st = FC.apply_face_corrections(ratings, [_spot()], factors={}, slug_for=_slug, now=TODAY)
    assert ratings["Steamer Lane"][0] == before
    assert st["corrected_spots"] == 0 and st["corrected_hours"] == 0


def test_absent_is_not_one_point_zero():
    """The distinction the design rests on: a spot with no entry must take NO arithmetic
    path, so a mis-keyed slug and a deliberate omission cannot look alike. Pinned by
    tripwiring composite_stars — an unfactored spot must never reach it."""
    saved = FC.composite_stars
    try:
        def _boom(*_a, **_k):
            raise AssertionError("composite_stars was called for an unfactored spot")
        FC.composite_stars = _boom
        ratings = {"Ocean Beach": [_entry()]}
        # Steamer Lane is in the ROSTER (so validation passes) but has no RATINGS, so the
        # only spot iterated is the unfactored one.
        FC.apply_face_corrections(ratings, [_spot("Ocean Beach"), _spot("Steamer Lane")],
                                  factors={"steamer-lane": _rec(2.0)},
                                  slug_for=_slug, now=TODAY)
    finally:
        FC.composite_stars = saved


def test_a_rating_for_a_spot_missing_from_the_roster_is_untouched():
    before = _entry()
    ratings = {"Ghost Spot": [dict(before)]}
    st = FC.apply_face_corrections(ratings, [_spot("Steamer Lane")],
                                   factors={"steamer-lane": _rec(2.0)},
                                   slug_for=_slug, now=TODAY)
    assert ratings["Ghost Spot"][0] == before
    assert st["corrected_spots"] == 0


def test_an_hour_with_no_face_or_no_effective_is_counted_not_corrupted():
    ratings = {"Steamer Lane": [
        {"valid_time": "x", "face_ft": None, "effective_size_ft": 6.0, "stars": 4.0},
        {"valid_time": "y", "face_ft": 6.0, "effective_size_ft": None, "stars": 4.0},
        _entry(),
    ]}
    st = FC.apply_face_corrections(ratings, [_spot()], factors={"steamer-lane": _rec(2.0)},
                                   slug_for=_slug, now=TODAY)
    assert ratings["Steamer Lane"][0]["stars"] == 4.0   # untouched
    assert ratings["Steamer Lane"][1]["stars"] == 4.0   # untouched
    assert st["unrateable_hours"] == 2 and st["corrected_hours"] == 1, st


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
    assert ratings["Steamer Lane"][0] == before, ratings["Steamer Lane"][0]
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
    assert ratings["Fort Point"][0] == before


# --------------------------------------------------------------------------- #
# 5 — the spread rule                                                         #
# --------------------------------------------------------------------------- #

def test_the_spread_rule_excludes_above_two_point_five_and_keeps_at_it():
    import sys
    sys.path.insert(0, "scripts")
    from build_face_factors import classify
    # p90/p10: 2.50 -> keep (strictly greater excludes); 2.51 -> spread; 6.47 -> spread
    for p90, expect in ((2.0, "keep"), (2.5, "keep"), (2.51, "spread"), (6.47, "spread")):
        v, _ = classify({"slug": "s", "face_ratio": {"median": 2.0, "n": 9,
                                                     "p10": 1.0, "p90": p90}}, set())
        assert v == expect, (p90, v, expect)


def test_the_threshold_is_the_committed_constant():
    from pipeline.config import FACE_FACTOR_MAX_SPREAD
    assert FACE_FACTOR_MAX_SPREAD == 2.5


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
    assert "SUMMER" in said[0], "the warning must say why age matters here"


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
             "face_ratio": {"median": 2.5, "n": 300, "p10": 1.0, "p90": 1.2}}
    assert classify(clean, {"a-mop-spot"})[0] == "mop_tier"
    assert classify(clean, set())[0] == "keep", "same record, no tier set -> kept"
    # Tier beats every statistical gate: the objection is structural, not about the numbers.
    assert classify({"slug": "a-mop-spot",
                     "face_ratio": {"median": 2.0, "n": 9, "p10": 1.0, "p90": 9.0}},
                    {"a-mop-spot"})[0] == "mop_tier"


def _run_all():
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_") and callable(v)]
    for fn in fns:
        fn()
        print(f"  PASS  {fn.__name__}")
    print(f"\ntest_face_correction: {len(fns)} PASS")


if __name__ == "__main__":
    _run_all()
