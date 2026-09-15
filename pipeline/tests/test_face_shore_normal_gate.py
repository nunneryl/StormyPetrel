"""The face harness's shore-normal gate is 90 deg; the MOP adoption path's is still 35.

WHY THE TWO DIFFER — the distinction these tests exist to defend is SCALAR versus
DIRECTIONAL:

  * scripts/mop_ca_rollout.py ADOPTS MOP. It reads waveHs, waveTp, waveDp AND
    waveEnergyDensity and resolves waveDp against metaShoreNormal through interpret's
    directional_gain. A shore normal wrong by d makes every directional term wrong by d in
    a PUBLISHED rating, so 35 deg is a real tolerance there.
  * scripts/mop_face_validation.py REFERENCES MOP. fetch_mop_by_hour keeps waveHs and
    discards Tp and Dp on purpose; face_ratio is one scalar divided by another. Nothing on
    that path resolves a direction, so the 35-deg gate was guarding a risk it does not
    carry, and excluded New Brighton Reef by ONE degree.

If someone "tidies" these two back into one constant, these tests fail. That is the point.

THE DELTAS BELOW ARE FIXTURE DATA FROM A REAL HARNESS RUN, transcribed, not computed here.
They cannot be recomputed in CI: the deltas come from metaShoreNormal in
scripts/mop_points.json, which is gitignored and absent from any clone, and the roster
carries mop_shore_normal only for the 48 spots where MOP was ADOPTED — never for a rejected
one. So the expected sets are written out by name and compared against the gate, and no
expected value anywhere in this file is produced by calling the code under test.
"""
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, ROOT)

from mop_face_validation import (  # noqa: E402
    FACE_SHORE_NORMAL_MAX_DELTA,
    SHORE_NORMAL_MAX_DELTA,
    match_verdict,
    shore_normal_delta,
)

# The sixteen rejected on SHORE_NORMAL_MAX_DELTA, with the delta each was rejected at.
# Transcribed from the run's population.rejected / console output.
SIXTEEN = {
    "seal-beach-pier": 148,
    "surfside-jetty": 147,
    "seal-beach-california": 135,
    "oceanside-harbor": 129,
    "trinidad-state-beach": 99,
    "bolinas": 73,
    "crescent-city-beach": 66,
    "half-moon-bay-jetty": 52,
    "seal-beach-jetty": 51,
    "rat-beach": 50,
    "agate-beach": 47,
    "carmel-beach": 44,
    "pacifica-linda-mar": 44,
    "cronkhite": 40,
    "jenner-beach": 39,
    "new-brighton-reef": 36,
}

# WRITTEN OUT BY NAME, not filtered from SIXTEEN with the threshold under test. Deriving
# these two sets with `<= FACE_SHORE_NORMAL_MAX_DELTA` would make them agree with the gate
# by construction and pin nothing.
RECOVERED_AT_90 = {
    "new-brighton-reef",
    "jenner-beach",
    "cronkhite",
    "carmel-beach",
    "pacifica-linda-mar",
    "agate-beach",
    "rat-beach",
    "seal-beach-jetty",
    "half-moon-bay-jetty",
    "crescent-city-beach",
    "bolinas",
}
# RECOVERED BY THE ANGLE GATE — not necessarily present in a run. bolinas clears 90 deg
# and is then rejected on MATCH_SEPARATION_M at a reported 2439 m, so it is recovered
# here and absent there. These sets are about the angle and nothing else; the separation
# gate is pinned in pipeline/tests/test_face_separation_gate.py.
STILL_EXCLUDED_AT_90 = {
    "trinidad-state-beach",     # 99 deg, a real nonzero normal of 169.9
    "oceanside-harbor",         # 129 = 360 - 231, a 0.0 normal
    "seal-beach-california",    # 135 = 360 - 225, a 0.0 normal — see note below
    "surfside-jetty",           # 147 = 360 - 213, a 0.0 normal
    "seal-beach-pier",          # 148 = 360 - 212, a 0.0 normal
}

# seal-beach-california no longer REACHES this gate. is_population now drops
# is_valid_surf_spot false, and that entry is false (invalid_reason "duplicate"), so it is
# filtered out before any match or angle is computed. It stays in these sets because they
# are a transcript of the run that motivated the 90-deg relaxation, and rewriting history
# to match today's population would destroy the evidence that argued for the number. The
# tests below feed match_verdict directly and so are unaffected either way.
# Pinned in pipeline/tests/test_face_population_valid_spot.py.

# A match distance well inside MATCH_SEPARATION_M (2200 m), so every verdict below turns
# on the angle alone and never on distance. This was chosen against MATCH_SANITY_M when
# distance was unenforced; it is still correct, but the binding constant is now the
# separation gate, and a larger NEAR would silently start testing that instead.
NEAR = 600.0


def _accepted(delta):
    return match_verdict(NEAR, float(delta))[0]


def test_the_two_gates_are_different_numbers():
    assert FACE_SHORE_NORMAL_MAX_DELTA == 90.0
    assert SHORE_NORMAL_MAX_DELTA == 35.0
    assert FACE_SHORE_NORMAL_MAX_DELTA > SHORE_NORMAL_MAX_DELTA


def test_the_adoption_path_constant_is_untouched():
    """Imported from mop_handful_slice, which mop_ca_rollout also imports it from.

    Pinned by value. Relaxing the face path must not move the path that reads waveDp.
    """
    from mop_handful_slice import SHORE_NORMAL_MAX_DELTA as adoption_gate
    assert adoption_gate == 35.0


def test_the_adoption_path_still_reads_direction_and_the_face_path_does_not():
    """The justification for the split, asserted against the source rather than trusted.

    If the face harness ever starts reading waveDp, the scalar argument collapses and the
    gates should be reunified — so this test fails loudly in that case instead of leaving a
    stale 90 in place.
    """
    face = open(os.path.join(ROOT, "scripts", "mop_face_validation.py")).read()
    rollout = open(os.path.join(ROOT, "scripts", "mop_ca_rollout.py")).read()
    assert 'v("waveDp")' in rollout, "adoption path should still read the directional spectrum"
    assert 'v("waveEnergyDensity")' in rollout
    # The face harness names waveDp only in prose explaining that it is discarded.
    assert 'v("waveDp")' not in face, "face harness must not start reading waveDp"
    assert "Only waveHs is kept" in face


def test_the_comment_records_why_the_two_paths_differ():
    """The reason is the whole asset here; a silent constant invites reunification.

    Pinned by content, because a previous round of mutation testing found that rewritten
    explanatory prose was the one thing no test noticed.
    """
    src = open(os.path.join(ROOT, "scripts", "mop_face_validation.py")).read()
    assert "DO NOT REUNIFY THEM" in src
    assert "SCALAR VERSUS DIRECTIONAL" in src
    assert "waveEnergyDensity" in src
    assert "directional_gain" in src

    # MOP_README.md is where someone looks before touching these constants, so the table
    # row that marks 35 as adoption-only is load-bearing documentation, not decoration.
    readme = open(os.path.join(ROOT, "scripts", "MOP_README.md")).read()
    assert "ADOPTION ONLY" in readme
    assert "FACE_SHORE_NORMAL_MAX_DELTA = 90.0" in readme
    assert "must not be reunified" in readme


def test_new_brighton_reef_was_excluded_by_exactly_one_degree():
    """The spot that motivated the change: 36 deg against a 35-deg gate.

    Both literals are written out. 36 > 35 and 36 <= 90 are the two facts, and neither is
    obtained by asking the code under test what its threshold is.
    """
    assert SIXTEEN["new-brighton-reef"] == 36
    assert SIXTEEN["new-brighton-reef"] > 35.0        # why it was rejected
    assert _accepted(36) is True                       # why it is recovered
    assert _accepted(35) is True                       # and it was never a boundary case at 90


def test_new_brighton_reef_is_in_the_recovered_set_by_name():
    assert "new-brighton-reef" in RECOVERED_AT_90
    assert "new-brighton-reef" not in STILL_EXCLUDED_AT_90


def test_the_recovered_set_is_exactly_these_eleven():
    got = {slug for slug, d in SIXTEEN.items() if _accepted(d)}
    assert got == RECOVERED_AT_90
    assert len(RECOVERED_AT_90) == 11


def test_the_still_excluded_set_is_exactly_these_five():
    got = {slug for slug, d in SIXTEEN.items() if not _accepted(d)}
    assert got == STILL_EXCLUDED_AT_90
    assert len(STILL_EXCLUDED_AT_90) == 5


def test_the_two_sets_partition_the_sixteen():
    assert RECOVERED_AT_90 | STILL_EXCLUDED_AT_90 == set(SIXTEEN)
    assert RECOVERED_AT_90 & STILL_EXCLUDED_AT_90 == set()
    assert len(SIXTEEN) == 16


def test_every_recovered_spot_would_have_failed_the_adoption_gate():
    """None of the eleven is recovered by accident of already passing 35.

    This is what makes the change load-bearing rather than cosmetic: all eleven sit strictly
    above the adoption threshold and strictly at or below the face one.
    """
    for slug in RECOVERED_AT_90:
        assert SIXTEEN[slug] > 35.0, slug
        assert SIXTEEN[slug] <= 90.0, slug


def test_nothing_above_ninety_is_recovered():
    for slug in STILL_EXCLUDED_AT_90:
        assert SIXTEEN[slug] > 90.0, slug


def test_the_gate_is_inclusive_at_its_own_boundary():
    assert _accepted(90) is True
    assert _accepted(91) is False
    # 90.0 exactly, not 89.999: the boundary is the dot-product sign change.
    assert _accepted(90.0) is True
    assert _accepted(90.0001) is False


def test_a_zero_shore_normal_is_absent_not_due_north():
    """The four 0.0 rejections read delta == 360 - orientation, i.e. distance to the number.

    Without this guard a 90-deg gate would admit a fabricated bearing for any spot oriented
    within 90 deg of north — 26 of the 153 population spots, against 5 at the old 35.
    """
    assert shore_normal_delta(212.0, 0.0) is None
    assert shore_normal_delta(231.0, 0.0) is None
    assert shore_normal_delta(296.0, 0.0) is None   # would have read 64 and PASSED at 90
    assert shore_normal_delta(212.0, 0) is None     # int zero too


def test_a_zero_normal_is_rejected_by_the_verdict_not_quietly_accepted():
    delta = shore_normal_delta(296.0, 0.0)
    ok, why = match_verdict(NEAR, delta)
    assert ok is False
    assert "metaShoreNormal" in why


def test_a_genuine_bearing_just_off_north_still_counts():
    """The guard must catch the sentinel without discarding a real near-north normal.

    212 to 0.5 is 148.5, not 147.5: 0.5 lies half a degree PAST north going 212 -> 360 -> 0.5,
    so nudging the normal off zero moves it further from 212, not nearer. Hand-computed wrong
    the first time and caught here, which is the argument for literals over derived values.
    """
    assert abs(shore_normal_delta(212.0, 0.5) - 148.5) < 1e-9
    assert abs(shore_normal_delta(10.0, 359.0) - 11.0) < 1e-9
    assert shore_normal_delta(212.0, 360.0) is not None


def test_declining_a_zero_normal_is_REPORTED_and_not_silent():
    """Added after mutation testing: silencing the warning survived every other test.

    The report is part of the contract, not decoration. A zero normal drops a spot from the
    eligible set, and without a warning that happens invisibly — the cache entry is never
    repaired and the spot is simply missing from the next run with no trace of why. Asserted
    against ONE captured emission rather than a joined blob, so a mutant cannot pass by
    virtue of some other message happening to contain the same words.

    stderr specifically: the docstring promises it cannot interleave with the report this
    script prints to stdout, and a run is routinely piped.
    """
    import contextlib
    import io

    err, out = io.StringIO(), io.StringIO()
    with contextlib.redirect_stderr(err), contextlib.redirect_stdout(out):
        assert shore_normal_delta(296.0, 0.0) is None

    emitted = err.getvalue()
    assert emitted.count("WARNING") == 1, f"expected exactly one warning, got {emitted!r}"
    assert "metaShoreNormal" in emitted
    assert "0.0" in emitted, "the offending value must be in the message"
    assert "repair" in emitted, "the message must say the cache entry is repairable"
    assert out.getvalue() == "", "the warning must not go to stdout alongside the report"

    # and a good normal stays quiet
    err2 = io.StringIO()
    with contextlib.redirect_stderr(err2):
        assert shore_normal_delta(296.0, 310.0) is not None
    assert err2.getvalue() == ""


def test_a_non_finite_shore_normal_is_absent():
    assert shore_normal_delta(212.0, float("nan")) is None
    assert shore_normal_delta(212.0, float("inf")) is None
    assert shore_normal_delta(212.0, float("-inf")) is None


def test_the_four_zero_rejections_each_equal_360_minus_their_orientation():
    """The arithmetic that identified the zero class, pinned with hand-written literals.

    orientation values are from pipeline/spots_enriched.json; the deltas are from the run.
    Neither side is computed by the code under test.
    """
    for slug, orientation, delta in (
        ("seal-beach-pier", 212.0, 148),
        ("surfside-jetty", 213.0, 147),
        ("seal-beach-california", 225.0, 135),
        ("oceanside-harbor", 231.0, 129),
    ):
        assert SIXTEEN[slug] == delta
        assert abs((360.0 - orientation) - delta) < 0.6, slug


def test_distance_is_no_longer_the_unenforced_guard_and_bolinas_is_the_case():
    """THIS TEST RECORDS A DELIBERATE BEHAVIOUR CHANGE — it used to assert the opposite.

    When the angle was relaxed to 90, Bolinas was the standing argument for capping
    distance: it passes at 73 deg while its MOP point is reportedly 2439 m away across a
    curving coast. The earlier version of this test asserted that pairing was ACCEPTED,
    and said "if a distance cap is added later, this test is the one that records the
    behaviour it changed." MATCH_SEPARATION_M is that cap, so this is that record.

    The 2439 m figure is not verifiable from a clone — scripts/mop_points.json is
    gitignored — so what is asserted here is the gate's verdict AT that distance, never
    that Bolinas is really that far from its point.
    """
    assert SIXTEEN["bolinas"] == 73
    assert _accepted(73) is True                      # the ANGLE still passes it
    ok, why = match_verdict(2439.3, 73.0)             # the SEPARATION gate does not
    assert ok is False
    assert "separation gate" in why
    # and the sanity cap is still a real cap, far out
    from mop_ca_rollout import MATCH_SANITY_M
    assert match_verdict(MATCH_SANITY_M + 1.0, 10.0)[0] is False


def test_distance_and_angle_are_independent_gates():
    """A near match with an incoherent angle fails; a far match with a fine angle fails."""
    assert match_verdict(NEAR, 120.0)[0] is False
    from mop_ca_rollout import MATCH_SANITY_M
    assert match_verdict(MATCH_SANITY_M + 1.0, 5.0)[0] is False
    assert match_verdict(NEAR, 5.0)[0] is True


def test_the_delta_is_unsigned_and_wraps():
    assert abs(shore_normal_delta(350.0, 10.0) - 20.0) < 1e-9
    assert abs(shore_normal_delta(10.0, 350.0) - 20.0) < 1e-9
    assert shore_normal_delta(None, 270.0) is None
    assert shore_normal_delta(270.0, None) is None


def test_delta_never_exceeds_180():
    """A circular offset folded onto [0,180] can never produce a 181-deg disagreement."""
    for orientation in range(0, 360, 7):
        for normal in range(1, 360, 11):
            d = shore_normal_delta(float(orientation), float(normal))
            assert d is not None
            assert 0.0 <= d <= 180.0, (orientation, normal, d)
            assert math.isfinite(d)


if __name__ == "__main__":
    fails = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn()
                print(f"  PASS  {name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL  {name}: {e}")
    print("face shore-normal gate: ALL PASS" if not fails else f"{fails} FAILED")
    sys.exit(1 if fails else 0)
